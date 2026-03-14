import json
import os
import sys

from fastapi import APIRouter, Body, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select
from sqlalchemy import desc, func, nullslast
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    Character,
    Picture,
    PictureSet,
    PictureSetMember,
    SortMechanism,
    Tag,
)
from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.picture_scoring import (
    find_pictures_by_character_likeness,
    find_pictures_by_smart_score,
    get_smart_score_penalised_tags_from_request,
)
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.service.caption_utils import _normalize_hidden_tags
from pixlstash.utils.service.serialization_utils import safe_model_dict
from pixlstash.utils.stack.stack_utils import _deduplicate_by_stack

logger = get_logger(__name__)


def create_router(server) -> APIRouter:
    router = APIRouter()

    def _enrich_with_stack_counts(pictures: list[dict]) -> list[dict]:
        if not pictures:
            return pictures

        picture_ids = [
            int(pic.get("id"))
            for pic in pictures
            if isinstance(pic, dict) and pic.get("id") is not None
        ]
        if not picture_ids:
            return pictures

        def fetch_stack_info(session: Session, ids: list[int]):
            id_stack_rows = session.exec(
                select(Picture.id, Picture.stack_id).where(
                    Picture.id.in_(ids),
                    Picture.deleted.is_(False),
                )
            ).all()
            stack_ids = sorted(
                {
                    int(stack_id)
                    for _pic_id, stack_id in id_stack_rows
                    if stack_id is not None
                }
            )
            if not stack_ids:
                return id_stack_rows, []

            stack_count_rows = session.exec(
                select(Picture.stack_id, func.count(Picture.id))
                .where(
                    Picture.stack_id.in_(stack_ids),
                    Picture.deleted.is_(False),
                )
                .group_by(Picture.stack_id)
            ).all()
            return id_stack_rows, stack_count_rows

        id_stack_rows, stack_count_rows = server.vault.db.run_immediate_read_task(
            fetch_stack_info, picture_ids
        )
        stack_id_by_picture_id = {
            int(pic_id): stack_id for pic_id, stack_id in id_stack_rows
        }
        stack_count_by_stack_id = {
            int(stack_id): int(count)
            for stack_id, count in stack_count_rows
            if stack_id is not None
        }

        enriched: list[dict] = []
        for pic in pictures:
            if not isinstance(pic, dict):
                enriched.append(pic)
                continue
            picture_id = pic.get("id")
            if picture_id is None:
                enriched.append(pic)
                continue
            numeric_id = int(picture_id)
            stack_id = pic.get("stack_id")
            if stack_id is None:
                stack_id = stack_id_by_picture_id.get(numeric_id)
            stack_count = 0
            if stack_id is not None:
                stack_count = stack_count_by_stack_id.get(int(stack_id), 1)
            enriched.append(
                {
                    **pic,
                    "stack_id": stack_id,
                    "stack_count": stack_count,
                }
            )
        return enriched

    def _get_hidden_tags_from_request(request: Request) -> list[str]:
        try:
            user = server.auth.get_user_for_request(request)
        except HTTPException:
            user = server.auth.get_user()
        if not user:
            return []
        if not getattr(user, "apply_tag_filter", False):
            return []
        normalized = _normalize_hidden_tags(getattr(user, "hidden_tags", None))
        return normalized or []

    def _filter_hidden_picture_ids(
        session, picture_ids: list[int], hidden_tags: list[str]
    ) -> list[int]:
        if not picture_ids or not hidden_tags:
            return picture_ids
        hidden_tag_set = {str(tag).strip().lower() for tag in hidden_tags if tag}
        rows = session.exec(
            select(Tag.picture_id).where(
                Tag.picture_id.in_(picture_ids),
                Tag.tag.is_not(None),
                func.lower(Tag.tag).in_(hidden_tag_set),
            )
        ).all()
        hidden_ids = {row for row in rows if row is not None}
        return [pic_id for pic_id in picture_ids if pic_id not in hidden_ids]

    def _find_reference_character_id_for_set(picture_set_id):
        # Find reference_character_id if this is a reference set
        def find_reference_character(session, picture_set_id):
            character = Character.find(
                session,
                select_fields=["reference_picture_set_id"],
                reference_picture_set_id=picture_set_id,
            )
            logger.debug(
                f"Found reference character for set {picture_set_id}: {character}"
            )
            return character[0].id if character else None

        return server.vault.db.run_immediate_read_task(
            find_reference_character, picture_set_id
        )

    @router.get(
        "/picture_sets",
        summary="List picture sets",
        description="Returns picture sets with visible member counts, top pictures, and thumbnail URLs.",
    )
    def get_picture_sets(request: Request):
        hidden_tags = _get_hidden_tags_from_request(request)

        def fetch_sets(session):
            sets = session.exec(
                select(PictureSet).options(selectinload(PictureSet.reference_character))
            ).all()
            result = []
            for s in sets:
                members = session.exec(
                    select(PictureSetMember.picture_id)
                    .join(Picture, Picture.id == PictureSetMember.picture_id)
                    .where(
                        PictureSetMember.set_id == s.id,
                        Picture.deleted.is_(False),
                    )
                ).all()
                filtered_ids = _filter_hidden_picture_ids(
                    session,
                    [m for m in members if m is not None],
                    hidden_tags,
                )
                count = len(set(filtered_ids))
                set_dict = safe_model_dict(s)
                set_dict["picture_count"] = count
                top_picture_ids = []
                if filtered_ids:
                    top_rows = session.exec(
                        select(Picture.id)
                        .where(Picture.id.in_(filtered_ids))
                        .order_by(
                            nullslast(desc(Picture.score)),
                            nullslast(desc(Picture.aesthetic_score)),
                            nullslast(desc(Picture.imported_at)),
                            desc(Picture.id),
                        )
                        .limit(3)
                    ).all()
                    top_picture_ids = [row for row in top_rows if row is not None]
                set_dict["top_picture_ids"] = top_picture_ids
                set_dict["thumbnail_url"] = f"/picture_sets/{s.id}/thumbnail"
                result.append(set_dict)
            return result

        result = safe_model_dict(server.vault.db.run_immediate_read_task(fetch_sets))
        logger.debug(f"Fetched picture set {result}")
        return result

    @router.post(
        "/picture_sets",
        summary="Create picture set",
        description="Creates a new picture set with name and optional description.",
    )
    def create_picture_set(payload: dict = Body(...)):
        name = payload.get("name")
        description = payload.get("description", "")
        if not name:
            raise HTTPException(status_code=400, detail="name is required")

        def create_set(session, name, description):
            picture_set = PictureSet(name=name, description=description)
            session.add(picture_set)
            session.commit()
            session.refresh(picture_set)
            return picture_set.dict()

        set_dict = server.vault.db.run_task(
            create_set, name, description, priority=DBPriority.IMMEDIATE
        )
        return {"status": "success", "picture_set": set_dict}

    @router.get(
        "/picture_sets/{id}/thumbnail",
        summary="Get picture set thumbnail",
        description="Returns or generates a cached composite thumbnail representing top-scoring pictures in a set.",
    )
    def get_picture_set_thumbnail(id: int, request: Request):
        thumbnail_cache_version = 16
        cache_dir = os.path.join(server.vault.image_root, "tmp", "set_thumbnails")
        os.makedirs(cache_dir, exist_ok=True)
        cache_path = os.path.join(cache_dir, f"picture_set_{id}.png")
        meta_path = os.path.join(cache_dir, f"picture_set_{id}.json")
        hidden_tags = _get_hidden_tags_from_request(request)
        hidden_key = "|".join(sorted(tag for tag in hidden_tags if tag))

        def fetch_top_picture_ids(
            session: Session,
            set_id: int,
            active_hidden_tags: list[str],
        ):
            member_rows = session.exec(
                select(PictureSetMember.picture_id)
                .join(Picture, Picture.id == PictureSetMember.picture_id)
                .where(
                    PictureSetMember.set_id == set_id,
                    Picture.deleted.is_(False),
                )
            ).all()
            member_ids = [row for row in member_rows if row is not None]
            filtered_ids = _filter_hidden_picture_ids(
                session,
                member_ids,
                active_hidden_tags,
            )
            if not filtered_ids:
                return []
            rows = session.exec(
                select(Picture.id)
                .where(Picture.id.in_(filtered_ids))
                .order_by(
                    nullslast(desc(Picture.score)),
                    nullslast(desc(Picture.aesthetic_score)),
                    nullslast(desc(Picture.imported_at)),
                    desc(Picture.id),
                )
                .limit(3)
            ).all()
            return [row for row in rows if row is not None]

        top_ids = server.vault.db.run_immediate_read_task(
            fetch_top_picture_ids,
            set_id=id,
            active_hidden_tags=hidden_tags,
        )
        if not top_ids:
            raise HTTPException(status_code=404, detail="No pictures found for set")

        if os.path.exists(cache_path) and os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as handle:
                    meta = json.load(handle)
                if (
                    meta.get("version") == thumbnail_cache_version
                    and meta.get("picture_ids") == top_ids
                    and meta.get("hidden_key") == hidden_key
                ):
                    return FileResponse(cache_path, media_type="image/png")
            except Exception:
                pass

        def fetch_picture_paths(session: Session, picture_ids: list[int]):
            rows = session.exec(
                select(Picture.id, Picture.file_path).where(Picture.id.in_(picture_ids))
            ).all()
            return {int(row[0]): row[1] for row in rows if row and row[0] is not None}

        path_map = server.vault.db.run_immediate_read_task(
            fetch_picture_paths, picture_ids=top_ids
        )
        target_size = 64
        work_size = 256
        card_height = int(target_size * 0.75)
        card_width = max(1, int(card_height * 0.7))
        card_size = (card_width, card_height)
        angles = [20, 5, -20]
        offsets = [(0, 0), (0, 0), (0, -4)]
        base = Image.new("RGBA", (work_size, work_size), (0, 0, 0, 0))
        pivot_x = work_size // 2
        pivot_y = work_size // 2

        def build_card(image: Image.Image | None):
            if image is None:
                card = Image.new("RGBA", card_size, (255, 255, 255, 255))
            else:
                card = ImageOps.fit(image, card_size, Image.LANCZOS)
                if card.mode != "RGBA":
                    card = card.convert("RGBA")
            mask = Image.new("L", card_size, 0)
            draw = ImageDraw.Draw(mask)
            draw.rounded_rectangle(
                (0, 0, card_size[0], card_size[1]), radius=6, fill=255
            )
            card.putalpha(mask)
            draw = ImageDraw.Draw(card)
            draw.rounded_rectangle(
                (-2, -2, card_size[0] + 1, card_size[1] + 1),
                radius=7,
                outline=(170, 170, 170, 255),
                width=2,
            )
            return card

        cards = []
        for picture_id in top_ids:
            file_path = path_map.get(picture_id)
            resolved_path = (
                ImageUtils.resolve_picture_path(server.vault.image_root, file_path)
                if file_path
                else None
            )
            if not resolved_path:
                cards.append(build_card(None))
                continue
            try:
                img = Image.open(resolved_path).convert("RGB")
            except Exception:
                img = None
            cards.append(build_card(img))

        while len(cards) < 3:
            cards.append(build_card(None))

        # Map highest score to right/front, then middle, then left/back
        right_card = cards[0]
        middle_card = cards[1]
        left_card = cards[2]
        cards = [left_card, middle_card, right_card]

        # Layering: left (bottom), middle, right (top)
        layer_order = [0, 1, 2]

        for layer_index, card_index in enumerate(layer_order):
            card = cards[card_index]
            angle = angles[card_index]
            offset = offsets[card_index]
            layer = Image.new("RGBA", (work_size, work_size), (0, 0, 0, 0))
            paste_x = pivot_x + offset[0]
            paste_y = pivot_y - card_size[1] + offset[1]
            layer.paste(card, (paste_x, paste_y), card)
            rotated_layer = layer.rotate(
                angle,
                resample=Image.BICUBIC,
                expand=False,
                center=(pivot_x, pivot_y),
                fillcolor=(0, 0, 0, 0),
            )
            base.alpha_composite(rotated_layer)

        alpha = base.split()[-1]
        bbox = alpha.getbbox()
        if bbox:
            pad = 0
            left = max(0, bbox[0] - pad)
            top = max(0, bbox[1] - pad)
            right = min(work_size, bbox[2] + pad)
            bottom = min(work_size, bbox[3] + pad)
            base = base.crop((left, top, right, bottom))

        # Add a subtle drop shadow behind the whole fan
        shadow_layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        shadow_alpha = base.split()[-1]
        shadow_layer.putalpha(shadow_alpha)
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=4))
        shadow_tint = Image.new("RGBA", base.size, (0, 0, 0, 90))
        shadow = Image.composite(
            shadow_tint,
            Image.new("RGBA", base.size, (0, 0, 0, 0)),
            shadow_layer.split()[-1],
        )
        fan_with_shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
        fan_with_shadow.alpha_composite(shadow, (2, 3))
        fan_with_shadow.alpha_composite(base, (0, 0))
        base = fan_with_shadow

        shadow_alpha = base.split()[-1]
        shadow_bbox = shadow_alpha.getbbox()
        if shadow_bbox:
            shadow_pad = 0
            left = max(0, shadow_bbox[0] - shadow_pad)
            top = max(0, shadow_bbox[1] - shadow_pad)
            right = min(base.width, shadow_bbox[2] + shadow_pad)
            bottom = min(base.height, shadow_bbox[3] + shadow_pad)
            base = base.crop((left, top, right, bottom))

        final_img = ImageOps.fit(
            base,
            (target_size, target_size),
            Image.LANCZOS,
            centering=(0.5, 0.5),
        )

        try:
            final_img.save(cache_path, format="PNG")
            try:
                with open(meta_path, "w", encoding="utf-8") as handle:
                    json.dump(
                        {
                            "version": thumbnail_cache_version,
                            "picture_ids": top_ids,
                            "hidden_key": hidden_key,
                        },
                        handle,
                    )
            except Exception:
                pass
            return FileResponse(cache_path, media_type="image/png")
        except Exception:
            from io import BytesIO

            buf = BytesIO()
            final_img.save(buf, format="PNG")
            return Response(content=buf.getvalue(), media_type="image/png")

    @router.get(
        "/picture_sets/{id}",
        summary="Get picture set",
        description="Returns set metadata or member pictures with optional sort, format, and character-likeness/smart-score modes.",
    )
    def get_picture_set(
        request: Request,
        id: int,
        info: bool = Query(False),
        sort: str = Query(None),
        descending: bool = Query(True),
        format: list[str] = Query(None),
        character_id: str | None = Query(None),
        reference_character_id: str | None = Query(None),
        fields: str = Query(None),
    ):
        sort_mech = None
        if sort:
            try:
                sort_mech = SortMechanism.from_string(sort, descending=descending)
            except ValueError as ve:
                logger.error("Invalid sort mechanism: %s - %s", sort, ve)
                raise HTTPException(status_code=400, detail=str(ve))

        def fetch_set(session, id):
            picture_set = session.get(PictureSet, id)
            if not picture_set:
                return None, None
            members = session.exec(
                select(PictureSetMember.picture_id)
                .join(Picture, Picture.id == PictureSetMember.picture_id)
                .where(
                    PictureSetMember.set_id == id,
                    Picture.deleted.is_(False),
                )
            ).all()
            seen = set()
            picture_ids = []
            for pic_id in members:
                if pic_id is None:
                    continue
                if pic_id in seen:
                    continue
                seen.add(pic_id)
                picture_ids.append(pic_id)
            return picture_set, picture_ids

        picture_set, picture_ids = server.vault.db.run_immediate_read_task(
            fetch_set, id
        )
        if not picture_set:
            raise HTTPException(status_code=404, detail="Picture set not found")
        hidden_tags = _get_hidden_tags_from_request(request)

        def filter_hidden_ids(session, ids):
            return _filter_hidden_picture_ids(session, ids, hidden_tags)

        picture_ids = server.vault.db.run_immediate_read_task(
            filter_hidden_ids, picture_ids
        )

        # If any picture in the set belongs to a stack, treat the entire stack
        # as part of the set — mirroring how stacks work in the regular view.
        def expand_with_stack_members(session, ids):
            if not ids:
                return ids
            rows = session.exec(
                select(Picture.id, Picture.stack_id).where(
                    Picture.id.in_(ids),
                    Picture.deleted.is_(False),
                )
            ).all()
            stack_ids = [int(stack_id) for _, stack_id in rows if stack_id is not None]
            if not stack_ids:
                return ids
            extra = session.exec(
                select(Picture.id).where(
                    Picture.stack_id.in_(stack_ids),
                    Picture.deleted.is_(False),
                )
            ).all()
            return list(set(ids) | set(extra))

        picture_ids = server.vault.db.run_immediate_read_task(
            expand_with_stack_members, picture_ids
        )

        if info:
            set_dict = picture_set.dict()
            set_dict["picture_count"] = len(picture_ids)
            return set_dict

        if sort_mech and sort_mech.key == SortMechanism.Keys.SMART_SCORE:
            penalised_tags = get_smart_score_penalised_tags_from_request(
                server, request
            )
            pictures = find_pictures_by_smart_score(
                server,
                format,
                0,
                sys.maxsize,
                descending,
                candidate_ids=picture_ids,
                penalised_tags=penalised_tags,
            )
            if fields == "grid":
                pictures = _deduplicate_by_stack(pictures)
            pictures = _enrich_with_stack_counts(pictures)
            return {"pictures": pictures, "set": safe_model_dict(picture_set)}

        if sort_mech and sort_mech.key == SortMechanism.Keys.CHARACTER_LIKENESS:
            if not reference_character_id:
                raise HTTPException(
                    status_code=400,
                    detail="reference_character_id is required for CHARACTER_LIKENESS sort",
                )
            pictures = find_pictures_by_character_likeness(
                server,
                character_id,
                reference_character_id,
                0,
                sys.maxsize,
                descending,
                candidate_ids=picture_ids,
            )
            if fields == "grid":
                pictures = _deduplicate_by_stack(pictures)
            pictures = _enrich_with_stack_counts(pictures)
            return {"pictures": pictures, "set": safe_model_dict(picture_set)}

        def fetch_pics(session, picture_ids):
            pics = Picture.find(
                session,
                id=picture_ids,
                sort_mech=sort_mech,
                select_fields=Picture.metadata_fields(),
                format=format,
                include_unimported=True,
                stack_leaders_only=(fields == "grid"),
            )
            return [
                pic.dict(
                    exclude={
                        "file_path",
                        "thumbnail",
                        "text_embedding",
                        "image_embedding",
                    }
                )
                for pic in pics
            ]

        pictures = server.vault.db.run_immediate_read_task(fetch_pics, picture_ids)
        pictures = _enrich_with_stack_counts(pictures)
        return {"pictures": pictures, "set": safe_model_dict(picture_set)}

    @router.patch(
        "/picture_sets/{id}",
        summary="Update picture set",
        description="Updates picture set name and/or description.",
    )
    def update_picture_set(id: int, payload: dict = Body(...)):
        name = payload.get("name")
        description = payload.get("description")

        def update_set(session, id, name, description):
            picture_set = session.get(PictureSet, id)
            if not picture_set:
                return False
            if name is not None:
                picture_set.name = name
            if description is not None:
                picture_set.description = description

            session.commit()
            return True

        success = server.vault.db.run_task(
            update_set, id, name, description, priority=DBPriority.IMMEDIATE
        )
        if not success:
            raise HTTPException(status_code=404, detail="Picture set not found")
        return {"status": "success"}

    @router.delete(
        "/picture_sets/{id}",
        summary="Delete picture set",
        description="Deletes a picture set and all its membership links.",
    )
    def delete_picture_set(id: int):
        def delete_set(session, id):
            picture_set = session.get(PictureSet, id)
            if not picture_set:
                return False
            session.delete(picture_set)
            session.commit()
            return True

        success = server.vault.db.run_task(
            delete_set, id, priority=DBPriority.IMMEDIATE
        )
        if not success:
            raise HTTPException(status_code=404, detail="Picture set not found")
        return {"status": "success", "deleted_id": id}

    @router.get(
        "/picture_sets/{id}/members",
        summary="List picture set members",
        description="Returns unique picture ids that belong to a set, with optional deleted inclusion.",
    )
    def get_picture_set_pictures(
        id: int,
        include_deleted: bool = Query(False),
    ):
        def fetch_members(session, id, include_deleted):
            picture_set = session.get(PictureSet, id)
            if not picture_set:
                return None
            filters = [
                PictureSetMember.set_id == id,
            ]
            if not include_deleted:
                filters.append(Picture.deleted.is_(False))
            members = session.exec(
                select(PictureSetMember.picture_id)
                .join(Picture, Picture.id == PictureSetMember.picture_id)
                .where(*filters)
            ).all()
            return list({m for m in members if m is not None})

        picture_ids = server.vault.db.run_immediate_read_task(
            fetch_members, id, include_deleted
        )
        if picture_ids is None:
            raise HTTPException(status_code=404, detail="Picture set not found")
        return {"picture_ids": picture_ids}

    @router.post(
        "/picture_sets/{id}/members/{picture_id}",
        summary="Add picture to set",
        description="Adds one picture to a set when the set and picture are valid and membership does not already exist.",
    )
    def add_picture_to_set(id: int, picture_id: str):
        reference_character_id = _find_reference_character_id_for_set(id)

        def add_member(session, id, picture_id, reference_character_id=None):
            picture_set = session.get(PictureSet, id)
            picture = session.get(Picture, picture_id)
            if not picture_set or not picture or picture.deleted:
                return False
            exists = session.exec(
                select(PictureSetMember).where(
                    PictureSetMember.set_id == id,
                    PictureSetMember.picture_id == picture_id,
                )
            ).first()
            if exists:
                return False
            member = PictureSetMember(set_id=id, picture_id=picture_id)
            session.add(member)
            session.add(picture_set)
            session.commit()
            return True

        success = server.vault.db.run_task(
            add_member,
            id,
            picture_id,
            reference_character_id=reference_character_id,
            priority=DBPriority.IMMEDIATE,
        )
        if success:
            if reference_character_id is not None:
                server.vault.notify(EventType.CHANGED_CHARACTERS)
        else:
            raise HTTPException(
                status_code=400,
                detail="Failed to add picture to set (set may not exist or picture already in set)",
            )
        return {"status": "success"}

    @router.delete(
        "/picture_sets/{id}/members/{picture_id}",
        summary="Remove picture from set",
        description="Removes one picture membership from a picture set.",
    )
    def remove_picture_from_set(id: int, picture_id: str):
        reference_character_id = _find_reference_character_id_for_set(id)

        def remove_member(session, id, picture_id, reference_character_id=None):
            member = session.exec(
                select(PictureSetMember).where(
                    PictureSetMember.set_id == id,
                    PictureSetMember.picture_id == picture_id,
                )
            ).first()
            if not member:
                return False
            session.delete(member)
            session.commit()
            return True

        success = server.vault.db.run_task(
            remove_member,
            id,
            picture_id,
            reference_character_id=reference_character_id,
            priority=DBPriority.IMMEDIATE,
        )
        if success:
            if reference_character_id is not None:
                server.vault.notify(EventType.CHANGED_CHARACTERS)
        else:
            raise HTTPException(status_code=404, detail="Picture not in set")
        return {"status": "success"}

    return router
