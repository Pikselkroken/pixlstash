from fastapi import APIRouter, Body, HTTPException
from sqlmodel import Session, delete, select

from pixlvault.db_models import (
    Face,
    FaceTag,
    Hand,
    HandTag,
    Picture,
    Tag,
    TAG_EMPTY_SENTINEL,
)
from pixlvault.event_types import EventType
from pixlvault.pixl_logging import get_logger
from pixlvault.utils import serialize_tag_objects

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
                session.exec(delete(FaceTag).where(FaceTag.tag_id.in_(tag_ids)))
                session.exec(delete(HandTag).where(HandTag.tag_id.in_(tag_ids)))
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

    @router.get(
        "/faces/{face_id}/tags",
        summary="List face tags",
        description="Returns tags currently linked to a face.",
    )
    async def list_face_tags(face_id: int):
        def fetch_tags(session: Session, face_id: int):
            face = session.get(Face, face_id)
            if face is None:
                raise HTTPException(status_code=404, detail="Face not found")
            rows = session.exec(
                select(Tag)
                .join(FaceTag, Tag.id == FaceTag.tag_id)
                .where(FaceTag.face_id == face_id)
            ).all()
            return serialize_tag_objects(rows)

        tags = server.vault.db.run_task(fetch_tags, face_id)
        return {"tags": tags}

    @router.post(
        "/faces/{face_id}/tags",
        summary="Add tag to face",
        description="Associates a tag with a face, creating the underlying picture tag when missing.",
    )
    async def add_tag_to_face(face_id: int, payload: dict = Body(...)):
        tag_value = (payload or {}).get("tag")
        if not tag_value:
            raise HTTPException(status_code=400, detail="Tag is required")

        def update_face(session: Session, face_id: int, tag_value: str):
            face = session.get(Face, face_id)
            if face is None:
                raise HTTPException(status_code=404, detail="Face not found")
            picture_id = face.picture_id
            sentinel = session.exec(
                select(Tag).where(
                    Tag.picture_id == picture_id,
                    Tag.tag == TAG_EMPTY_SENTINEL,
                )
            ).first()
            if sentinel is not None:
                session.delete(sentinel)
            tag = session.exec(
                select(Tag).where(
                    Tag.picture_id == picture_id,
                    Tag.tag == tag_value,
                )
            ).first()
            if tag is None:
                tag = Tag(tag=tag_value, picture_id=picture_id)
                session.add(tag)
                session.flush()
            if tag not in face.tags:
                face.tags.append(tag)
            session.add(face)
            session.commit()
            session.refresh(face)
            return serialize_tag_objects(face.tags)

        tags = server.vault.db.run_task(update_face, face_id, tag_value)
        server.vault.notify(EventType.CHANGED_TAGS)
        return {"status": "success", "tags": tags}

    @router.delete(
        "/faces/{face_id}/tags/{tag}",
        summary="Remove tag from face",
        description="Removes a tag association from a face using either tag id or tag value.",
    )
    async def remove_tag_from_face(face_id: int, tag: str):
        def update_face(session: Session, face_id: int, tag_value: str):
            face = session.get(Face, face_id)
            if face is None:
                raise HTTPException(status_code=404, detail="Face not found")
            target = None
            if tag_value.isdigit():
                target = next(
                    (
                        t
                        for t in (face.tags or [])
                        if t.id is not None and str(t.id) == tag_value
                    ),
                    None,
                )
            if target is None:
                target = next(
                    (t for t in (face.tags or []) if t.tag == tag_value),
                    None,
                )
            if target is not None:
                face.tags.remove(target)
            session.add(face)
            session.commit()
            session.refresh(face)
            return serialize_tag_objects(face.tags)

        tags = server.vault.db.run_task(update_face, face_id, tag)
        server.vault.notify(EventType.CHANGED_TAGS)
        return {"status": "success", "tags": tags}

    @router.get(
        "/hands/{hand_id}/tags",
        summary="List hand tags",
        description="Returns tags currently linked to a hand.",
    )
    async def list_hand_tags(hand_id: int):
        def fetch_tags(session: Session, hand_id: int):
            hand = session.get(Hand, hand_id)
            if hand is None:
                raise HTTPException(status_code=404, detail="Hand not found")
            rows = session.exec(
                select(Tag)
                .join(HandTag, Tag.id == HandTag.tag_id)
                .where(HandTag.hand_id == hand_id)
            ).all()
            return serialize_tag_objects(rows)

        tags = server.vault.db.run_task(fetch_tags, hand_id)
        return {"tags": tags}

    @router.post(
        "/hands/{hand_id}/tags",
        summary="Add tag to hand",
        description="Associates a tag with a hand, creating the underlying picture tag when missing.",
    )
    async def add_tag_to_hand(hand_id: int, payload: dict = Body(...)):
        tag_value = (payload or {}).get("tag")
        if not tag_value:
            raise HTTPException(status_code=400, detail="Tag is required")

        def update_hand(session: Session, hand_id: int, tag_value: str):
            hand = session.get(Hand, hand_id)
            if hand is None:
                raise HTTPException(status_code=404, detail="Hand not found")
            picture_id = hand.picture_id
            sentinel = session.exec(
                select(Tag).where(
                    Tag.picture_id == picture_id,
                    Tag.tag == TAG_EMPTY_SENTINEL,
                )
            ).first()
            if sentinel is not None:
                session.delete(sentinel)
            tag = session.exec(
                select(Tag).where(
                    Tag.picture_id == picture_id,
                    Tag.tag == tag_value,
                )
            ).first()
            if tag is None:
                tag = Tag(tag=tag_value, picture_id=picture_id)
                session.add(tag)
                session.flush()
            if tag not in hand.tags:
                hand.tags.append(tag)
            session.add(hand)
            session.commit()
            session.refresh(hand)
            return serialize_tag_objects(hand.tags)

        tags = server.vault.db.run_task(update_hand, hand_id, tag_value)
        server.vault.notify(EventType.CHANGED_TAGS)
        return {"status": "success", "tags": tags}

    @router.delete(
        "/hands/{hand_id}/tags/{tag}",
        summary="Remove tag from hand",
        description="Removes a tag association from a hand using either tag id or tag value.",
    )
    async def remove_tag_from_hand(hand_id: int, tag: str):
        def update_hand(session: Session, hand_id: int, tag_value: str):
            hand = session.get(Hand, hand_id)
            if hand is None:
                raise HTTPException(status_code=404, detail="Hand not found")
            target = None
            if tag_value.isdigit():
                target = next(
                    (
                        t
                        for t in (hand.tags or [])
                        if t.id is not None and str(t.id) == tag_value
                    ),
                    None,
                )
            if target is None:
                target = next(
                    (t for t in (hand.tags or []) if t.tag == tag_value),
                    None,
                )
            if target is not None:
                hand.tags.remove(target)
            session.add(hand)
            session.commit()
            session.refresh(hand)
            return serialize_tag_objects(hand.tags)

        tags = server.vault.db.run_task(update_hand, hand_id, tag)
        server.vault.notify(EventType.CHANGED_TAGS)
        return {"status": "success", "tags": tags}

    return router
