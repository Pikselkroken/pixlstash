from fastapi import APIRouter, Body, HTTPException
from sqlmodel import Session, delete, select

from pixlstash.db_models import (
    Picture,
    Tag,
    TAG_EMPTY_SENTINEL,
)
from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.service.caption_utils import serialize_tag_objects

logger = get_logger(__name__)


def create_router(server) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/pictures/{id}/tags",
        summary="Add tag to picture",
        description="Adds a tag to a picture and removes empty-tag sentinel when appropriate.",
    )
    async def add_tag_to_picture(id: str, payload: dict = Body(...)):
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
                    session.commit()
                    session.refresh(pic)
                    return pic

                pic = server.vault.db.run_task(update_picture, pic.id, tag)
                server.vault.notify(EventType.CHANGED_TAGS)

            return {"status": "success", "tags": serialize_tag_objects(pic.tags)}
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
    async def list_picture_tags(id: str):
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
    async def remove_tag_from_picture(id: str, tag_id: str):
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
                session.commit()
                session.refresh(pic)
                return pic

            pic = server.vault.db.run_task(update_picture, pic_id, tag_id_int)
            server.vault.notify(EventType.CHANGED_TAGS)

            return {"status": "success", "tags": serialize_tag_objects(pic.tags)}
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
    async def remove_tag_from_picture_everywhere(id: str, payload: dict = Body(...)):
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
            session.commit()
            session.refresh(pic)
            return pic

        pic = server.vault.db.run_task(update_picture, pic_id, tag_value)
        server.vault.notify(EventType.CHANGED_TAGS)
        return {"status": "success", "tags": serialize_tag_objects(pic.tags)}

    @router.delete(
        "/pictures/{id}/tags",
        summary="Clear all tags on picture",
        description="Removes all tags from a picture in a single operation and restores the empty-tag sentinel.",
    )
    async def clear_all_tags_on_picture(id: str):
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
            session.commit()
            session.refresh(pic)
            return pic

        pic = server.vault.db.run_task(do_clear, pic_id)
        server.vault.notify(EventType.CHANGED_TAGS)
        return {"status": "success", "tags": serialize_tag_objects(pic.tags)}

    return router
