from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, delete, select

from pixlstash.db_models import (
    Picture,
    Tag,
    TAG_EMPTY_SENTINEL,
)
from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.service.caption_utils import (
    serialize_tag_objects,
    sync_picture_sidecar,
)
from pixlstash.utils.service.tag_prediction_utils import (
    recompute_anomaly_tag_uncertainty,
)

logger = get_logger(__name__)


def _sync_sidecar(server, pic_id: int) -> list[dict]:
    return sync_picture_sidecar(server, pic_id)


def create_router(server) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/pictures/{id}/tags",
        summary="Add tag to picture",
        description="Adds a tag to a picture and removes empty-tag sentinel when appropriate.",
    )
    def add_tag_to_picture(id: str, payload: dict = Body(...)):
        try:
            try:
                pic_id = int(id)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Invalid picture id")
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
                        (t for t in pic.tags if t.tag == TAG_EMPTY_SENTINEL),
                        None,
                    )
                    if sentinel is not None:
                        session.delete(sentinel)
                    if not any(t.tag == tag for t in pic.tags):
                        pic.tags.append(Tag(tag=tag, picture_id=pic_id))
                    session.add(pic)
                    session.flush()
                    recompute_anomaly_tag_uncertainty(session, pic_id)
                    session.commit()
                    session.refresh(pic)
                    return pic

                server.vault.db.run_task(update_picture, pic.id, tag)
                server.vault.notify(EventType.CHANGED_TAGS, {"picture_ids": [pic_id]})

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
    )
    def list_picture_tags(id: str):
        try:
            try:
                pic_id = int(id)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Invalid picture id")
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
    )
    def remove_tag_from_picture(id: str, tag_id: str):
        try:
            try:
                pic_id = int(id)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Invalid picture id")
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
                session.delete(target)
                session.flush()
                remaining = session.exec(
                    select(Tag).where(
                        Tag.picture_id == pic_id,
                        Tag.tag.is_not(None),
                        Tag.tag != TAG_EMPTY_SENTINEL,
                    )
                ).all()
                if not remaining:
                    sentinel = session.exec(
                        select(Tag).where(
                            Tag.picture_id == pic_id,
                            Tag.tag == TAG_EMPTY_SENTINEL,
                        )
                    ).first()
                    if sentinel is None:
                        session.add(Tag(tag=TAG_EMPTY_SENTINEL, picture_id=pic_id))
                recompute_anomaly_tag_uncertainty(session, pic_id)
                session.commit()
                session.refresh(pic)
                return pic

            server.vault.db.run_task(update_picture, pic_id, tag_id_int)
            server.vault.notify(EventType.CHANGED_TAGS, {"picture_ids": [pic_id]})

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
    )
    def remove_tag_from_picture_everywhere(id: str, payload: dict = Body(...)):
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")
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
                session.exec(delete(Tag).where(Tag.id.in_(tag_ids)))
            session.flush()
            remaining = session.exec(
                select(Tag).where(
                    Tag.picture_id == pic_id,
                    Tag.tag.is_not(None),
                    Tag.tag != TAG_EMPTY_SENTINEL,
                )
            ).all()
            if not remaining:
                sentinel = session.exec(
                    select(Tag).where(
                        Tag.picture_id == pic_id,
                        Tag.tag == TAG_EMPTY_SENTINEL,
                    )
                ).first()
                if sentinel is None:
                    session.add(Tag(tag=TAG_EMPTY_SENTINEL, picture_id=pic_id))
            recompute_anomaly_tag_uncertainty(session, pic_id)
            session.commit()
            session.refresh(pic)
            return pic

        server.vault.db.run_task(update_picture, pic_id, tag_value)
        server.vault.notify(EventType.CHANGED_TAGS, {"picture_ids": [pic_id]})
        fresh_tags = _sync_sidecar(server, pic_id)
        return {"status": "success", "tags": fresh_tags}

    @router.delete(
        "/pictures/{id}/tags",
        summary="Clear all tags on picture",
        description="Removes all tags from a picture in a single operation and restores the empty-tag sentinel.",
    )
    def clear_all_tags_on_picture(id: str):
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

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
            session.add(Tag(tag=TAG_EMPTY_SENTINEL, picture_id=pic_id))
            session.flush()
            recompute_anomaly_tag_uncertainty(session, pic_id)
            session.commit()
            session.refresh(pic)
            return pic

        server.vault.db.run_task(do_clear, pic_id)
        server.vault.notify(EventType.CHANGED_TAGS, {"picture_ids": [pic_id]})
        fresh_tags = _sync_sidecar(server, pic_id)
        return {"status": "success", "tags": fresh_tags}

    @router.get(
        "/tags",
        summary="List all tags",
        description="Returns all unique tag values with their usage count, sorted by count descending then alphabetically.",
    )
    def list_all_tags():
        try:
            from sqlalchemy import func

            def fetch(session: Session):
                rows = session.exec(
                    select(Tag.tag, func.count(Tag.id).label("count"))
                    .where(Tag.tag.is_not(None), Tag.tag != TAG_EMPTY_SENTINEL)
                    .group_by(Tag.tag)
                    .order_by(func.count(Tag.id).desc(), Tag.tag)
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
    )
    def bulk_fetch_tags(payload: BulkFetchTagsRequest):
        try:
            ids = payload.picture_ids[:200]
            if not ids:
                return []

            def fetch(session: Session, ids: list):
                rows = session.exec(
                    select(Tag.picture_id, Tag.id, Tag.tag).where(
                        Tag.picture_id.in_(ids),
                        Tag.tag.is_not(None),
                        Tag.tag != TAG_EMPTY_SENTINEL,
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
