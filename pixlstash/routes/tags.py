from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, delete, select

from pixlstash.db_models import (
    Picture,
    Tag,
    TAG_SENTINEL_LIKE_PATTERN,
    TAG_SENTINEL_ESCAPE_CHAR,
    is_tag_sentinel,
)
from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.service.caption_utils import (
    serialize_tag_objects,
    sync_picture_sidecar,
)
from pixlstash.utils.service.label_ledger import (
    NEG,
    POS,
    record_human_label_if_relevant,
)
from pixlstash.utils.service.tag_prediction_utils import (
    recompute_anomaly_tag_uncertainty,
)
from pixlstash.utils.service.filter_helpers import fetch_scope_allowed_picture_ids
from pixlstash.routes.pictures._helpers import enforce_picture_scope
from pixlstash.services.impossible_tag_clear_service import (
    VALID_FILTERS,
    clear_impossible_tags,
    restore_cleared_tags,
)

logger = get_logger(__name__)


class TagItemResponse(BaseModel):
    """A single tag attached to a picture."""

    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    tag: str


class PictureTagsResponse(BaseModel):
    """Result of mutating a picture's tags (add/remove/clear)."""

    model_config = ConfigDict(extra="allow")

    status: str
    tags: list[TagItemResponse] = []


class ListPictureTagsResponse(BaseModel):
    """Result of listing a single picture's tags."""

    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    tags: list[TagItemResponse] = []


class TagCountResponse(BaseModel):
    """A unique tag value with its usage count."""

    model_config = ConfigDict(extra="allow")

    tag: str
    count: int


class BulkPictureTagsResponse(BaseModel):
    """Tags for a single picture in a bulk-fetch response."""

    model_config = ConfigDict(extra="allow")

    id: int
    tags: list[TagItemResponse] = []


class ClearedTagPair(BaseModel):
    """One removed ``(picture, tag)``, returned for the undo and echoed back to restore."""

    picture_id: int = Field(..., description="Picture the tag was removed from.")
    tag: str = Field(..., description="The removed tag value.")


class ClearImpossibleTagsRequest(BaseModel):
    """Bulk-clear request: the selected pictures and the active grid filters to apply."""

    picture_ids: list[int] = Field(
        ..., description="Selected picture ids to clear wrong tags from."
    )
    filters: list[str] = Field(
        ...,
        description='Active Impossible-tags filter kinds: "no_face", "no_humans", and/or "object".',
    )


class ClearImpossibleTagsResponse(BaseModel):
    """Result of a bulk clear: how many tags were removed, and which (for undo)."""

    status: str
    count: int = Field(..., description="Number of (picture, tag) removals.")
    removed: list[ClearedTagPair] = Field(
        default=[], description="The removed pairs, to pass back to /restore for undo."
    )


class RestoreClearedTagsRequest(BaseModel):
    """Undo request: re-add the pairs a previous clear removed."""

    pairs: list[ClearedTagPair] = Field(
        ..., description="The (picture, tag) pairs to re-add."
    )


class RestoreClearedTagsResponse(BaseModel):
    """Result of an undo."""

    status: str
    restored: int = Field(..., description="Number of pairs re-added.")


def _sync_sidecar(server, pic_id: int) -> list[dict]:
    return sync_picture_sidecar(server, pic_id)


def create_router(server) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/pictures/{id}/tags",
        summary="Add tag to picture",
        description="Adds a tag to a picture and removes empty-tag sentinel when appropriate.",
        response_model=PictureTagsResponse,
    )
    def add_tag_to_picture(request: Request, id: str, payload: dict = Body(...)):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        try:
            try:
                pic_id = int(id)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Invalid picture id")
            # Scope guard (BOLA): a write-capable resource-scoped token may only
            # mutate tags for pictures within its granted resource.
            enforce_picture_scope(server, request, pic_id)
            tag = payload.get("tag")
            if not tag:
                raise HTTPException(status_code=400, detail="Tag is required")

            pic_list = server.vault.db.run_task(
                lambda session: Picture.find(
                    session,
                    id=pic_id,
                    select_fields=["tags"],
                    include_deleted=True,
                    include_unimported=True,
                )
            )
            if not pic_list:
                raise HTTPException(status_code=404, detail="Picture not found")
            pic = pic_list[0]

            existing = next((t for t in pic.tags if t.tag == tag), None)
            if existing is None:

                def update_picture(session, pic_id, tag):
                    pic = Picture.find(
                        session,
                        id=pic_id,
                        select_fields=["tags"],
                        include_deleted=True,
                        include_unimported=True,
                    )[0]
                    sentinel = next(
                        (t for t in pic.tags if is_tag_sentinel(t.tag)),
                        None,
                    )
                    if sentinel is not None:
                        session.delete(sentinel)
                    if not any(t.tag == tag for t in pic.tags):
                        pic.tags.append(Tag(tag=tag, picture_id=pic_id))
                    session.add(pic)
                    # Manually applying an anomaly tag is a human POS decision.
                    record_human_label_if_relevant(session, pic_id, tag, POS)
                    session.flush()
                    recompute_anomaly_tag_uncertainty(session, pic_id)
                    session.commit()
                    session.refresh(pic)
                    return pic

                server.vault.db.run_task(update_picture, pic.id, tag)
                server.vault.notify(
                    EventType.CHANGED_TAGS,
                    {
                        "picture_ids": [pic_id],
                        "origin_client_id": origin_client_id,
                        "change_kind": "updated",
                    },
                )

            fresh_tags = _sync_sidecar(server, pic_id)

            return {"status": "success", "tags": fresh_tags}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to add tag: {e}")
            raise HTTPException(status_code=500, detail="Failed to add tag")

    @router.get(
        "/pictures/{id}/tags",
        summary="List picture tags",
        description="Returns all tags currently attached to a picture.",
        response_model=ListPictureTagsResponse,
    )
    def list_picture_tags(request: Request, id: str):
        try:
            try:
                pic_id = int(id)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Invalid picture id")
            # Scope guard (BOLA): a resource-scoped READ share token may only
            # read tags for pictures within its granted resource.
            enforce_picture_scope(server, request, pic_id)
            pic_list = server.vault.db.run_task(
                lambda session: Picture.find(
                    session,
                    id=pic_id,
                    select_fields=["tags"],
                    include_deleted=True,
                    include_unimported=True,
                )
            )
            if not pic_list:
                raise HTTPException(status_code=404, detail="Picture not found")
            pic = pic_list[0]
            return {
                "id": getattr(pic, "id", None),
                "tags": serialize_tag_objects(pic.tags),
            }
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("Failed to list tags for picture %s: %s", id, exc)
            raise HTTPException(
                status_code=500, detail="Failed to list tags for picture"
            )

    @router.delete(
        "/pictures/{id}/tags/{tag_id}",
        summary="Remove picture tag",
        description="Removes one tag from a picture by numeric tag id and restores empty-tag sentinel when needed.",
        response_model=PictureTagsResponse,
    )
    def remove_tag_from_picture(request: Request, id: str, tag_id: str):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        try:
            try:
                pic_id = int(id)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Invalid picture id")
            # Scope guard (BOLA): a write-capable resource-scoped token may only
            # mutate tags for pictures within its granted resource.
            enforce_picture_scope(server, request, pic_id)
            if not tag_id.isdigit():
                raise HTTPException(status_code=400, detail="tag_id must be numeric")
            tag_id_int = int(tag_id)

            def update_picture(session, pic_id, tag_id_value):
                pic = Picture.find(
                    session,
                    id=pic_id,
                    select_fields=["tags"],
                    include_deleted=True,
                    include_unimported=True,
                )[0]
                target = session.exec(
                    select(Tag).where(
                        Tag.picture_id == pic_id,
                        Tag.id == tag_id_value,
                    )
                ).first()
                if target is None:
                    raise HTTPException(
                        status_code=404, detail="Tag not found on picture"
                    )
                # Manually removing an anomaly tag is a human NEG decision — record it
                # before the delete so the reviewed negative survives the lost Tag row.
                record_human_label_if_relevant(session, pic_id, target.tag, NEG)
                session.delete(target)
                session.flush()
                recompute_anomaly_tag_uncertainty(session, pic_id)
                session.commit()
                session.refresh(pic)
                return pic

            server.vault.db.run_task(update_picture, pic_id, tag_id_int)
            server.vault.notify(
                EventType.CHANGED_TAGS,
                {
                    "picture_ids": [pic_id],
                    "origin_client_id": origin_client_id,
                    "change_kind": "updated",
                },
            )

            fresh_tags = _sync_sidecar(server, pic_id)

            return {"status": "success", "tags": fresh_tags}
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to remove tag: {e}")
            raise HTTPException(status_code=500, detail="Failed to remove tag")

    @router.post(
        "/pictures/{id}/tags/remove_all",
        summary="Remove tag everywhere on picture",
        description="Removes a tag value from the picture and its face/hand associations for that picture.",
        response_model=PictureTagsResponse,
    )
    def remove_tag_from_picture_everywhere(
        request: Request, id: str, payload: dict = Body(...)
    ):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")
        # Scope guard (BOLA): a write-capable resource-scoped token may only
        # mutate tags for pictures within its granted resource.
        enforce_picture_scope(server, request, pic_id)
        tag_value = (payload or {}).get("tag")
        if not tag_value:
            raise HTTPException(status_code=400, detail="Tag is required")

        def update_picture(session: Session, pic_id: str, tag_value: str):
            pic_list = Picture.find(
                session,
                id=pic_id,
                select_fields=["tags"],
                include_deleted=True,
                include_unimported=True,
            )
            if not pic_list:
                raise HTTPException(status_code=404, detail="Picture not found")
            pic = pic_list[0]
            tag_ids = [
                t.id for t in pic.tags if t.tag == tag_value and t.id is not None
            ]
            if tag_ids:
                # Explicit single-tag removal is a human NEG decision; record it.
                record_human_label_if_relevant(session, pic_id, tag_value, NEG)
                session.exec(delete(Tag).where(Tag.id.in_(tag_ids)))
            session.flush()
            recompute_anomaly_tag_uncertainty(session, pic_id)
            session.commit()
            session.refresh(pic)
            return pic

        server.vault.db.run_task(update_picture, pic_id, tag_value)
        server.vault.notify(
            EventType.CHANGED_TAGS,
            {
                "picture_ids": [pic_id],
                "origin_client_id": origin_client_id,
                "change_kind": "updated",
            },
        )
        fresh_tags = _sync_sidecar(server, pic_id)
        return {"status": "success", "tags": fresh_tags}

    @router.delete(
        "/pictures/{id}/tags",
        summary="Clear all tags on picture",
        description="Removes all tags from a picture in a single operation and restores the empty-tag sentinel.",
        response_model=PictureTagsResponse,
    )
    def clear_all_tags_on_picture(request: Request, id: str):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")
        # Scope guard (BOLA): a write-capable resource-scoped token may only
        # mutate tags for pictures within its granted resource.
        enforce_picture_scope(server, request, pic_id)

        def do_clear(session: Session, pic_id: int):
            pic_list = Picture.find(
                session,
                id=pic_id,
                select_fields=["tags"],
                include_deleted=True,
                include_unimported=True,
            )
            if not pic_list:
                raise HTTPException(status_code=404, detail="Picture not found")
            pic = pic_list[0]
            session.exec(delete(Tag).where(Tag.picture_id == pic_id))
            session.flush()
            recompute_anomaly_tag_uncertainty(session, pic_id)
            session.commit()
            session.refresh(pic)
            return pic

        server.vault.db.run_task(do_clear, pic_id)
        server.vault.notify(
            EventType.CHANGED_TAGS,
            {
                "picture_ids": [pic_id],
                "origin_client_id": origin_client_id,
                "change_kind": "updated",
            },
        )
        fresh_tags = _sync_sidecar(server, pic_id)
        return {"status": "success", "tags": fresh_tags}

    @router.post(
        "/pictures/impossible-tags/clear",
        summary="Bulk-clear impossible tags",
        description=(
            "Remove the wrong tags surfaced by the live Impossible-tags grid filters "
            "from the selected pictures, recording a human NEG per removed tag so the "
            "cleanup is durable training signal. Owner-only; returns the removed "
            "(picture, tag) pairs for an undo."
        ),
        response_model=ClearImpossibleTagsResponse,
    )
    def clear_impossible_tags_endpoint(
        request: Request, payload: ClearImpossibleTagsRequest
    ):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        filters = [f for f in payload.filters if f in VALID_FILTERS]
        if not filters:
            raise HTTPException(
                status_code=400,
                detail=f"filters must be a non-empty subset of {list(VALID_FILTERS)}",
            )
        picture_ids = [int(p) for p in payload.picture_ids]
        # Defense in depth: a scoped token cannot reach this POST (owner-only by the
        # middleware gate), but if a scoped picture set is ever active, never act on a
        # picture outside it.
        allowed = fetch_scope_allowed_picture_ids(server, request)
        if allowed is not None:
            picture_ids = [p for p in picture_ids if p in allowed]
        if not picture_ids:
            return {"status": "success", "count": 0, "removed": []}

        result = clear_impossible_tags(server.vault, picture_ids, filters)
        touched = sorted({r["picture_id"] for r in result["removed"]})
        if touched:
            server.vault.notify(
                EventType.CHANGED_TAGS,
                {
                    "picture_ids": touched,
                    "origin_client_id": origin_client_id,
                    "change_kind": "updated",
                },
            )
            for pid in touched:
                _sync_sidecar(server, pid)
        return {"status": "success", **result}

    @router.post(
        "/pictures/impossible-tags/restore",
        summary="Undo a bulk impossible-tags clear",
        description=(
            "Re-add tags removed by a previous /clear and reset their human-label "
            "ledger entries. Owner-only."
        ),
        response_model=RestoreClearedTagsResponse,
    )
    def restore_impossible_tags_endpoint(
        request: Request, payload: RestoreClearedTagsRequest
    ):
        origin_client_id = getattr(request.state, "origin_client_id", None)
        pairs = [(int(p.picture_id), p.tag) for p in payload.pairs]
        allowed = fetch_scope_allowed_picture_ids(server, request)
        if allowed is not None:
            pairs = [(p, t) for (p, t) in pairs if p in allowed]
        if not pairs:
            return {"status": "success", "restored": 0}

        result = restore_cleared_tags(server.vault, pairs)
        touched = result.get("picture_ids") or []
        if touched:
            server.vault.notify(
                EventType.CHANGED_TAGS,
                {
                    "picture_ids": touched,
                    "origin_client_id": origin_client_id,
                    "change_kind": "updated",
                },
            )
            for pid in touched:
                _sync_sidecar(server, pid)
        return {"status": "success", "restored": result["restored"]}

    @router.get(
        "/tags",
        summary="List all tags",
        description="Returns all unique tag values with their usage count, sorted by count descending then alphabetically.",
        response_model=list[TagCountResponse],
    )
    def list_all_tags(request: Request):
        try:
            from sqlalchemy import func

            # Scope guard (BOLA): a READ-scoped share token may only see the tag
            # vocabulary (and counts) drawn from pictures within its granted
            # resource. None == owner / unscoped == no filter (full list). An
            # empty set means the scope matched nothing, so return nothing
            # rather than the full list. Mirrors bulk_fetch_tags below.
            scope_allowed = fetch_scope_allowed_picture_ids(server, request)
            if scope_allowed is not None and not scope_allowed:
                return []
            allowed_ids = list(scope_allowed) if scope_allowed is not None else None

            def fetch(session: Session):
                query = select(Tag.tag, func.count(Tag.id).label("count")).where(
                    Tag.tag.is_not(None),
                    ~Tag.tag.like(
                        TAG_SENTINEL_LIKE_PATTERN, escape=TAG_SENTINEL_ESCAPE_CHAR
                    ),
                )
                if allowed_ids is not None:
                    query = query.where(Tag.picture_id.in_(allowed_ids))
                rows = session.exec(
                    query.group_by(Tag.tag).order_by(func.count(Tag.id).desc(), Tag.tag)
                ).all()
                return [{"tag": tag, "count": count} for tag, count in rows if tag]

            return server.vault.db.run_task(fetch)
        except Exception as exc:
            logger.error("Failed to list all tags: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to list tags")

    class BulkFetchTagsRequest(BaseModel):
        picture_ids: list[int] = []

    @router.post(
        "/pictures/tags/bulk_fetch",
        summary="Fetch tags for multiple pictures",
        description="Returns tags for each requested picture id. At most 200 ids accepted per call.",
        response_model=list[BulkPictureTagsResponse],
    )
    def bulk_fetch_tags(request: Request, payload: BulkFetchTagsRequest):
        try:
            ids = payload.picture_ids[:200]
            if not ids:
                return []

            # Scope guard (BOLA): a READ-scoped share token may only read tags
            # for pictures within its granted resource.  None == owner /
            # unscoped == no filter.
            scope_allowed = fetch_scope_allowed_picture_ids(server, request)
            if scope_allowed is not None:
                ids = [pic_id for pic_id in ids if pic_id in scope_allowed]
                if not ids:
                    return []

            def fetch(session: Session, ids: list):
                rows = session.exec(
                    select(Tag.picture_id, Tag.id, Tag.tag).where(
                        Tag.picture_id.in_(ids),
                        Tag.tag.is_not(None),
                        ~Tag.tag.like(
                            TAG_SENTINEL_LIKE_PATTERN, escape=TAG_SENTINEL_ESCAPE_CHAR
                        ),
                    )
                ).all()
                by_pic: dict = {i: [] for i in ids}
                for pic_id, tag_id, tag_val in rows:
                    if tag_val and pic_id in by_pic:
                        by_pic[pic_id].append({"id": tag_id, "tag": tag_val})
                return [{"id": pic_id, "tags": by_pic[pic_id]} for pic_id in ids]

            return server.vault.db.run_task(fetch, ids)
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("Failed to bulk fetch tags: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to bulk fetch tags")

    return router
