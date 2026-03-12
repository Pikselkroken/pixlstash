from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request, Query
from sqlalchemy import case

from sqlmodel import Session, select

from pixlstash.db_models import Picture, PictureStack, SortMechanism
from pixlstash.picture_scoring import (
    fetch_smart_score_data,
    get_smart_score_penalised_tags_from_request,
    prepare_smart_score_inputs,
)
from pixlstash.utils.quality.smart_score_utils import SmartScoreUtils
from pixlstash.utils.service.serialization_utils import safe_model_dict
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


def create_router(server) -> APIRouter:
    router = APIRouter()

    def _ensure_secure_when_required(request: Request):
        server.auth.ensure_secure_when_required(request)

    def _normalize_picture_ids(raw_ids) -> list[int]:
        if not isinstance(raw_ids, list):
            raise HTTPException(status_code=400, detail="picture_ids must be a list")
        ids = []
        for raw_id in raw_ids:
            try:
                ids.append(int(raw_id))
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail="picture_ids must be integers",
                )
        if not ids:
            raise HTTPException(status_code=400, detail="picture_ids must not be empty")
        return ids

    def _fetch_stack_pictures(session: Session, stack_id: int):
        stack_position_order = case(
            (Picture.stack_position.is_(None), 1),
            else_=0,
        )
        return session.exec(
            select(Picture)
            .where(Picture.stack_id == stack_id)
            .order_by(stack_position_order, Picture.stack_position, Picture.id)
        ).all()

    def _stack_order_key(pic, smart_score_by_id: dict[int, float]):
        score = pic.score or 0
        smart_score = smart_score_by_id.get(pic.id, 0.0)
        created_at = pic.created_at or datetime.min
        created_ts = created_at.timestamp() if isinstance(created_at, datetime) else 0.0
        return (-score, -smart_score, -created_ts, int(pic.id or 0))

    def _compute_smart_score_map(
        request: Request,
        picture_ids: list[int],
    ) -> dict[int, float]:
        if not picture_ids:
            return {}
        try:
            penalised_tags = get_smart_score_penalised_tags_from_request(
                server, request
            )
            good_anchors, bad_anchors, candidates = fetch_smart_score_data(
                server,
                None,
                candidate_ids=picture_ids,
                penalised_tags=penalised_tags,
            )
            if candidates:
                good_list, bad_list, cand_list, cand_ids = prepare_smart_score_inputs(
                    good_anchors,
                    bad_anchors,
                    candidates,
                )
                if cand_list:
                    scores = SmartScoreUtils.calculate_smart_score_batch_numpy(
                        cand_list, good_list, bad_list
                    )
                    return {
                        int(pid): float(score)
                        for pid, score in zip(cand_ids, scores)
                        if score is not None
                    }
        except Exception as exc:
            logger.warning("[stacks] Failed to compute smart scores: %s", exc)
        return {}

    def _ensure_stack_positions(
        request: Request,
        stack_id: int,
        pictures: list[Picture],
    ) -> list[Picture]:
        if not pictures:
            return pictures
        if any(pic.stack_position is not None for pic in pictures):
            return sorted(
                pictures,
                key=lambda pic: (
                    pic.stack_position is None,
                    pic.stack_position or 0,
                    int(pic.id or 0),
                ),
            )

        smart_score_by_id = _compute_smart_score_map(
            request,
            [pic.id for pic in pictures if pic.id is not None],
        )
        ordered = sorted(
            pictures,
            key=lambda pic: _stack_order_key(pic, smart_score_by_id),
        )
        ordered_ids = [pic.id for pic in ordered if pic.id is not None]

        def update_positions(
            session: Session, stack_id_value: int, ordered_ids_value: list[int]
        ):
            stack = session.get(PictureStack, stack_id_value)
            if stack is None:
                return
            pics = session.exec(
                select(Picture).where(Picture.stack_id == stack_id_value)
            ).all()
            pic_by_id = {pic.id: pic for pic in pics}
            for idx, pic_id in enumerate(ordered_ids_value):
                pic = pic_by_id.get(pic_id)
                if pic is None:
                    continue
                pic.stack_position = idx
                session.add(pic)
            stack.updated_at = datetime.utcnow()
            session.add(stack)
            session.commit()

        if ordered_ids:
            server.vault.db.run_task(update_positions, stack_id, ordered_ids)

        return ordered

    @router.get(
        "/stacks/{stack_id}",
        summary="Get stack details",
        description="Returns stack metadata and ordered picture ids for a stack.",
    )
    async def get_stack(stack_id: int, request: Request):
        _ensure_secure_when_required(request)
        server.auth.require_user_id(request)

        def fetch_stack(session: Session, stack_id: int):
            stack = session.get(PictureStack, stack_id)
            if not stack:
                return None, []
            pictures = _fetch_stack_pictures(session, stack_id)
            return stack, pictures

        stack, pictures = server.vault.db.run_task(fetch_stack, stack_id)
        if not stack:
            raise HTTPException(status_code=404, detail="Stack not found")

        pictures = _ensure_stack_positions(request, stack_id, pictures)

        payload = safe_model_dict(stack)
        payload["picture_ids"] = [pic.id for pic in pictures]
        return payload

    @router.get(
        "/stacks/{stack_id}/pictures",
        summary="List pictures in stack",
        description="Returns ordered picture payloads for a stack using grid or metadata field sets.",
    )
    async def get_stack_pictures(
        stack_id: int,
        request: Request,
        fields: str = Query("grid"),
        include_deleted: bool = Query(False),
        sort: Optional[str] = Query(None),
        descending: bool = Query(True),
    ):
        _ensure_secure_when_required(request)
        server.auth.require_user_id(request)

        # Resolve sort mechanism; treat PICTURE_STACKS as "no sort" (stack order).
        sort_mech = None
        if sort:
            try:
                candidate = SortMechanism.from_string(sort, descending=descending)
                if candidate.key != SortMechanism.Keys.PICTURE_STACKS:
                    sort_mech = candidate
            except ValueError:
                pass

        def fetch_stack_pictures(
            session: Session,
            stack_id_value: int,
            fields_value: str,
            include_deleted_value: bool,
            sort_mech_value,
        ):
            stack = session.get(PictureStack, stack_id_value)
            if not stack:
                return None, None

            select_fields = (
                Picture.grid_fields()
                if fields_value == "grid"
                else Picture.metadata_fields()
            )

            pictures = Picture.find(
                session,
                stack_id=stack_id_value,
                sort_mech=sort_mech_value,
                select_fields=select_fields,
                include_deleted=include_deleted_value,
            )
            return select_fields, pictures

        select_fields, pictures = server.vault.db.run_task(
            fetch_stack_pictures,
            stack_id,
            fields,
            include_deleted,
            sort_mech,
        )
        if select_fields is None:
            raise HTTPException(status_code=404, detail="Stack not found")
        # Only apply stack-position ordering when no explicit sort is active;
        # this also persists positions for stacks that haven't been ordered yet.
        if sort_mech is None:
            pictures = _ensure_stack_positions(request, stack_id, pictures)
        return [
            {field: safe_model_dict(pic).get(field) for field in select_fields}
            for pic in pictures
        ]

    @router.get(
        "/pictures/{picture_id}/stack",
        summary="Get picture's stack",
        description="Returns the stack containing a picture, or null stack information when unstacked.",
    )
    async def get_stack_for_picture(picture_id: int, request: Request):
        _ensure_secure_when_required(request)
        server.auth.require_user_id(request)

        def fetch_stack_for_picture(session: Session, picture_id: int):
            pic = session.get(Picture, picture_id)
            if not pic or not pic.stack_id:
                return None, None, []
            stack = session.get(PictureStack, pic.stack_id)
            pictures = _fetch_stack_pictures(session, pic.stack_id)
            return pic.stack_id, stack, pictures

        stack_id, stack, pictures = server.vault.db.run_task(
            fetch_stack_for_picture, picture_id
        )
        if not stack_id or not stack:
            return {"stack_id": None, "picture_ids": []}

        pictures = _ensure_stack_positions(request, stack_id, pictures)

        payload = safe_model_dict(stack)
        payload["picture_ids"] = [pic.id for pic in pictures]
        return payload

    @router.post(
        "/stacks",
        summary="Create stack",
        description="Creates a new stack or reuses an existing compatible one and assigns provided pictures to it.",
    )
    async def create_stack(payload: dict = Body(...), request: Request = None):
        _ensure_secure_when_required(request)
        server.auth.require_user_id(request)

        picture_ids = _normalize_picture_ids(payload.get("picture_ids") or [])
        name = payload.get("name")
        if name is not None and not isinstance(name, str):
            name = str(name)

        def create_or_assign_stack(
            session: Session,
            picture_ids: list[int],
            name: Optional[str],
        ) -> int:
            pictures = session.exec(
                select(Picture).where(Picture.id.in_(picture_ids))
            ).all()
            if len(pictures) != len(picture_ids):
                missing = sorted(set(picture_ids) - {pic.id for pic in pictures})
                raise HTTPException(
                    status_code=404,
                    detail=f"Pictures not found: {missing}",
                )

            existing_stack_ids = {pic.stack_id for pic in pictures if pic.stack_id}
            if len(existing_stack_ids) > 1:
                raise HTTPException(
                    status_code=409,
                    detail="Pictures already belong to multiple stacks",
                )

            if existing_stack_ids:
                stack_id = existing_stack_ids.pop()
                stack = session.get(PictureStack, stack_id)
                if stack is None:
                    raise HTTPException(status_code=404, detail="Stack not found")
            else:
                stack = PictureStack(name=name)
                session.add(stack)
                session.commit()
                session.refresh(stack)

            existing_positions = []
            if stack.id is not None:
                rows = session.exec(
                    select(Picture.stack_position).where(
                        Picture.stack_id == stack.id,
                        Picture.stack_position.is_not(None),
                    )
                ).all()
                existing_positions = [row for row in rows if row is not None]
            next_position = max(existing_positions) + 1 if existing_positions else None

            for pic in pictures:
                pic.stack_id = stack.id
                if next_position is not None and pic.stack_position is None:
                    pic.stack_position = next_position
                    next_position += 1
                session.add(pic)

            stack.updated_at = datetime.utcnow()
            session.add(stack)
            session.commit()
            if stack.id is None:
                raise HTTPException(status_code=500, detail="Failed to create stack")
            return stack.id

        def fetch_stack_payload(session: Session, stack_id_value: int) -> dict:
            stack = session.get(PictureStack, stack_id_value)
            if stack is None:
                raise HTTPException(status_code=404, detail="Stack not found")
            return safe_model_dict(stack)

        stack_id = server.vault.db.run_task(create_or_assign_stack, picture_ids, name)
        pictures = server.vault.db.run_task(_fetch_stack_pictures, stack_id)
        pictures = _ensure_stack_positions(request, stack_id, pictures)
        payload = server.vault.db.run_task(fetch_stack_payload, stack_id)
        payload["picture_ids"] = [pic.id for pic in pictures]
        return payload

    @router.patch(
        "/stacks/{stack_id}/order",
        summary="Reorder stack",
        description="Sets explicit order for all members in a stack using a complete ordered id list.",
    )
    async def reorder_stack(
        stack_id: int, payload: dict = Body(...), request: Request = None
    ):
        _ensure_secure_when_required(request)
        server.auth.require_user_id(request)

        picture_ids = _normalize_picture_ids(payload.get("picture_ids") or [])
        unique_ids = list(dict.fromkeys(picture_ids))
        if len(unique_ids) != len(picture_ids):
            raise HTTPException(status_code=400, detail="picture_ids must be unique")

        def update_stack_order(
            session: Session, stack_id_value: int, ordered_ids: list[int]
        ):
            stack = session.get(PictureStack, stack_id_value)
            if stack is None:
                return None

            pics = session.exec(
                select(Picture).where(Picture.stack_id == stack_id_value)
            ).all()
            pic_by_id = {pic.id: pic for pic in pics}
            stack_ids = set(pic_by_id.keys())
            if stack_ids != set(ordered_ids):
                raise HTTPException(
                    status_code=400,
                    detail="picture_ids must include every picture in the stack",
                )

            for idx, pic_id in enumerate(ordered_ids):
                pic = pic_by_id.get(pic_id)
                if pic is None:
                    continue
                pic.stack_position = idx
                session.add(pic)

            stack.updated_at = datetime.utcnow()
            session.add(stack)
            session.commit()
            return ordered_ids

        result = server.vault.db.run_task(update_stack_order, stack_id, unique_ids)
        if result is None:
            raise HTTPException(status_code=404, detail="Stack not found")
        return {"stack_id": stack_id, "picture_ids": result}

    @router.post(
        "/stacks/{stack_id}/members",
        summary="Add stack members",
        description="Adds pictures to an existing stack while preventing cross-stack membership conflicts.",
    )
    async def add_stack_members(
        stack_id: int, payload: dict = Body(...), request: Request = None
    ):
        _ensure_secure_when_required(request)
        server.auth.require_user_id(request)

        picture_ids = _normalize_picture_ids(payload.get("picture_ids") or [])

        def add_members(session: Session, stack_id: int, picture_ids: list[int]):
            stack = session.get(PictureStack, stack_id)
            if stack is None:
                raise HTTPException(status_code=404, detail="Stack not found")

            pictures = session.exec(
                select(Picture).where(Picture.id.in_(picture_ids))
            ).all()
            if len(pictures) != len(picture_ids):
                missing = sorted(set(picture_ids) - {pic.id for pic in pictures})
                raise HTTPException(
                    status_code=404,
                    detail=f"Pictures not found: {missing}",
                )

            conflicts = [
                pic.id for pic in pictures if pic.stack_id not in (None, stack_id)
            ]
            if conflicts:
                raise HTTPException(
                    status_code=409,
                    detail=f"Pictures already in another stack: {sorted(conflicts)}",
                )

            existing_positions = []
            rows = session.exec(
                select(Picture.stack_position).where(
                    Picture.stack_id == stack_id,
                    Picture.stack_position.is_not(None),
                )
            ).all()
            existing_positions = [row for row in rows if row is not None]
            next_position = max(existing_positions) + 1 if existing_positions else None

            for pic in pictures:
                pic.stack_id = stack_id
                if next_position is not None and pic.stack_position is None:
                    pic.stack_position = next_position
                    next_position += 1
                session.add(pic)

            stack.updated_at = datetime.utcnow()
            session.add(stack)
            session.commit()
            return stack

        stack = server.vault.db.run_task(add_members, stack_id, picture_ids)
        pictures = server.vault.db.run_task(_fetch_stack_pictures, stack_id)
        pictures = _ensure_stack_positions(request, stack_id, pictures)
        payload = safe_model_dict(stack)
        payload["picture_ids"] = [pic.id for pic in pictures]
        return payload

    @router.delete(
        "/stacks/{stack_id}/members",
        summary="Remove stack members",
        description="Removes pictures from a stack and deletes the stack when one or fewer members remain.",
    )
    async def remove_stack_members(
        stack_id: int, payload: dict = Body(...), request: Request = None
    ):
        _ensure_secure_when_required(request)
        server.auth.require_user_id(request)

        picture_ids = _normalize_picture_ids(payload.get("picture_ids") or [])

        def remove_members(session: Session, stack_id: int, picture_ids: list[int]):
            stack = session.get(PictureStack, stack_id)
            if stack is None:
                raise HTTPException(status_code=404, detail="Stack not found")

            pictures = session.exec(
                select(Picture).where(Picture.id.in_(picture_ids))
            ).all()
            for pic in pictures:
                if pic.stack_id == stack_id:
                    pic.stack_id = None
                    session.add(pic)

            remaining = session.exec(
                select(Picture).where(Picture.stack_id == stack_id)
            ).all()

            if len(remaining) <= 1:
                for pic in remaining:
                    pic.stack_id = None
                    pic.stack_position = None
                    session.add(pic)
                session.delete(stack)
                session.commit()
                return None

            stack.updated_at = datetime.utcnow()
            session.add(stack)
            session.commit()
            return stack

        stack = server.vault.db.run_task(remove_members, stack_id, picture_ids)
        if stack is None:
            return {"status": "success", "stack_id": None, "picture_ids": picture_ids}

        payload = safe_model_dict(stack)
        payload["picture_ids"] = picture_ids
        return payload

    return router
