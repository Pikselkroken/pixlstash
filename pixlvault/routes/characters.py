import json
import os
import time
from fastapi import APIRouter, Body, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from PIL import Image, ImageOps
from sqlalchemy import exists, func
from sqlmodel import Session, select

from pixlvault.database import DBPriority
from pixlvault.db_models import (
    Character,
    Face,
    Picture,
    PictureSet,
    PictureSetMember,
)
from pixlvault.event_types import EventType
from pixlvault.pixl_logging import get_logger
from pixlvault.picture_utils import PictureUtils
from pixlvault.picture_scoring import select_reference_faces_for_character
from pixlvault.utils import safe_model_dict

logger = get_logger(__name__)


def create_router(server) -> APIRouter:
    router = APIRouter()

    @router.get("/characters/{id}/summary")
    async def get_characters_summary(id: str = None):
        """
        Return summary statistics for a single category:
        - If character_id is ALL: all pictures
        - If character_id is UNASSIGNED: unassigned pictures
        - If character_id is set: that character's pictures
        """
        start = time.time()
        if id == "ALL":

            def count_all(session: Session) -> int:
                return session.exec(
                    select(func.count(Picture.id)).where(
                        Picture.deleted.is_(False),
                        Picture.imported_at.is_not(None),
                    )
                ).one()

            image_count = server.vault.db.run_immediate_read_task(count_all)
            logger.debug("ALL pics count: {}".format(image_count))
            char_id = None
        elif id == "SCRAPHEAP":

            def count_scrapheap(session: Session) -> int:
                return session.exec(
                    select(func.count(Picture.id)).where(
                        Picture.deleted.is_(True),
                        Picture.imported_at.is_not(None),
                    )
                ).one()

            image_count = server.vault.db.run_immediate_read_task(count_scrapheap)
            logger.debug("SCRAPHEAP pics count: {}".format(image_count))
            char_id = None
        elif id == "UNASSIGNED":

            def count_unassigned(session: Session) -> int:
                face_exists = exists().where(
                    Face.picture_id == Picture.id,
                    Face.character_id.is_not(None),
                )
                set_exists = exists().where(PictureSetMember.picture_id == Picture.id)
                return session.exec(
                    select(func.count(Picture.id)).where(
                        Picture.deleted.is_(False),
                        Picture.imported_at.is_not(None),
                        ~face_exists,
                        ~set_exists,
                    )
                ).one()

            image_count = server.vault.db.run_immediate_read_task(count_unassigned)
            logger.debug("UNASSIGNED pics count: {}".format(image_count))
            char_id = None
        else:

            def count_assigned(session: Session, character_id: int) -> int:
                return session.exec(
                    select(func.count(func.distinct(Face.picture_id)))
                    .join(Picture, Face.picture_id == Picture.id)
                    .where(
                        Face.character_id == character_id,
                        Picture.deleted.is_(False),
                        Picture.imported_at.is_not(None),
                    )
                ).one()

            image_count = server.vault.db.run_immediate_read_task(
                count_assigned, character_id=int(id)
            )
            char_id = int(id)

        if char_id:
            thumb_url = None
            if char_id not in (None, "", "null"):
                thumb_url = f"/characters/{char_id}/thumbnail"
        else:
            thumb_url = None

        summary = {
            "character_id": char_id,
            "image_count": image_count,
            "thumbnail_url": thumb_url,
        }
        elapsed = time.time() - start
        logger.debug(f"Category summary computed in {elapsed:.4f} seconds")
        logger.debug(f"Category summary: {summary}")
        return summary

    @router.get("/characters/{id}/reference_pictures")
    async def get_character_reference_pictures(id: int):
        """Return reference picture ids for a character.

        Args:
            id: Character id to fetch reference pictures for.

        Returns:
            A dict containing reference picture ids.
        """

        def fetch_reference_pictures(session: Session, character_id: int):
            faces = select_reference_faces_for_character(
                session,
                character_id=character_id,
                max_refs=10,
            )
            picture_ids = []
            seen = set()
            for face in faces:
                pic_id = getattr(face, "picture_id", None)
                if pic_id is None or pic_id in seen:
                    continue
                seen.add(pic_id)
                picture_ids.append(pic_id)
            return picture_ids

        picture_ids = server.vault.db.run_task(
            fetch_reference_pictures,
            id,
            priority=DBPriority.IMMEDIATE,
        )
        logger.info(
            "[reference_pictures] character_id=%s picture_ids=%s",
            id,
            picture_ids,
        )
        return {"reference_picture_ids": picture_ids}

    @router.patch("/characters/{id}")
    async def patch_character(id: int, request: Request):
        data = await request.json()
        name = data.get("name")
        description = data.get("description")
        char = None
        try:

            def alter_char(session: Session, id: int, name: str, description: str):
                character = session.get(Character, id)
                if character is None:
                    raise KeyError("Character not found")
                updated = False
                if name is not None and name != character.name:
                    character.name = name
                    updated = True
                if description is not None and description != character.description:
                    character.description = description
                    updated = True
                if updated:
                    session.add(character)

                    pictures = Picture.find(session, character_id=id)
                    for pic in pictures:
                        pic.description = None
                        pic.text_embedding = None
                        session.add(pic)

                    session.commit()
                return character

            char = server.vault.db.run_task(
                alter_char, id, name, description, priority=DBPriority.IMMEDIATE
            )
            server.vault.notify(EventType.CHANGED_CHARACTERS)

        except KeyError:
            raise HTTPException(status_code=404, detail="Character not found")

        return {"status": "success", "character": char}

    @router.delete("/characters/{id}")
    async def delete_character(id: int):
        try:

            def clear_character_and_nullify_faces(session: Session, character_id: int):
                character = session.get(Character, character_id)
                if character is None:
                    raise KeyError("Character not found")
                reference_set_id = character.reference_picture_set_id
                faces = session.exec(
                    select(Face).where(Face.character_id == character_id)
                ).all()
                for face in faces:
                    face.character_id = None
                    session.add(face)
                session.commit()
                session.delete(character)
                session.commit()

                if reference_set_id is None:
                    return

                members = session.exec(
                    select(PictureSetMember).where(
                        PictureSetMember.set_id == reference_set_id
                    )
                ).all()
                for member in members:
                    session.delete(member)

                reference_set = session.get(PictureSet, reference_set_id)
                if reference_set is not None:
                    session.delete(reference_set)
                session.commit()

            server.vault.db.run_task(
                clear_character_and_nullify_faces,
                id,
                priority=DBPriority.IMMEDIATE,
            )
            server.vault.notify(EventType.CHANGED_CHARACTERS)
            return {"status": "success", "deleted_id": id}
        except KeyError:
            raise HTTPException(status_code=404, detail="Character not found")

    @router.get("/characters/{id}")
    async def get_character_by_id(id: int):
        try:
            char = server.vault.db.run_immediate_read_task(
                lambda session: Character.find(session, id=id)
            )
            return char[0] if char else None
        except KeyError:
            raise HTTPException(status_code=404, detail="Character not found")

    @router.get("/characters/{id}/{field}")
    async def get_character_field_by_id(id: int, field: str):
        if field == "thumbnail":
            thumbnail_cache_version = 2
            cache_dir = os.path.join(server.vault.image_root, "tmp", "face_thumbnails")
            os.makedirs(cache_dir, exist_ok=True)
            cache_path = os.path.join(cache_dir, f"character_{id}.png")
            meta_path = os.path.join(cache_dir, f"character_{id}.json")

            def fetch_best_picture_id(session: Session, character_id: int):
                row = session.exec(
                    select(Picture.id, Picture.score)
                    .join(Face, Face.picture_id == Picture.id)
                    .where(
                        Face.character_id == character_id,
                        Picture.deleted.is_(False),
                        Picture.imported_at.is_not(None),
                    )
                    .order_by(
                        Picture.score.is_(None),
                        Picture.score.desc(),
                        Picture.id.desc(),
                    )
                    .limit(1)
                ).first()
                if not row:
                    return None
                pic_id, score = row
                return {
                    "picture_id": int(pic_id),
                    "score": float(score) if score is not None else None,
                }

            best_picture = server.vault.db.run_immediate_read_task(
                fetch_best_picture_id, character_id=id
            )
            if not best_picture:
                raise HTTPException(
                    status_code=404, detail="No face thumbnail found for character"
                )
            if os.path.exists(cache_path) and os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as handle:
                        meta = json.load(handle)
                    if (
                        meta.get("picture_id") == best_picture.get("picture_id")
                        and meta.get("version") == thumbnail_cache_version
                    ):
                        return FileResponse(cache_path, media_type="image/png")
                except Exception:
                    pass
            char = server.vault.db.run_immediate_read_task(
                Character.find,
                select_fields=["reference_picture_set_id", "faces"],
                id=id,
            )
            if not char:
                raise HTTPException(status_code=404, detail="Character not found")
            char = char[0]
            best_pic = None
            best_face = None

            def get_reference_set_and_members(session, reference_picture_set_id):
                ref_set = (
                    session.get(PictureSet, reference_picture_set_id)
                    if reference_picture_set_id
                    else None
                )
                if ref_set:
                    session.refresh(ref_set)
                    members = list(ref_set.members)
                    return ref_set, members
                return None, []

            ref_set, members = server.vault.db.run_immediate_read_task(
                get_reference_set_and_members, char.reference_picture_set_id
            )
            if ref_set and ref_set.members:
                pics = sorted(members, key=lambda p: (p.score or 0), reverse=True)
                for pic in pics:
                    faces = server.vault.db.run_immediate_read_task(
                        Face.find, picture_id=pic.id
                    )
                    for face in faces:
                        if face.character_id == char.id:
                            best_pic = pic
                            best_face = face
                            break
                    if best_pic and best_face:
                        logger.debug("Found thumbnail from reference set!")
                        break
            if not best_pic or not best_face:
                for face in char.faces:
                    pic = server.vault.db.run_immediate_read_task(
                        Picture.find,
                        id=face.picture_id,
                        sort_field="score",
                    )
                    if pic:
                        best_pic = pic
                        best_face = face
                        break
            if not best_pic or not best_face:
                raise HTTPException(
                    status_code=404, detail="No face thumbnail found for character"
                )

            bbox = best_face.bbox

            if isinstance(best_pic, list):
                best_pic = best_pic[0]

            picture_path = PictureUtils.resolve_picture_path(
                server.vault.image_root, best_pic.file_path
            )
            crop = PictureUtils.crop_face_bbox_exact(picture_path, bbox)
            if crop is None:
                raise HTTPException(
                    status_code=404, detail="Failed to crop face thumbnail"
                )
            try:
                crop = crop.convert("RGB")
            except Exception:
                pass
            crop = ImageOps.contain(crop, (36, 36), Image.LANCZOS)
            try:
                crop.save(cache_path, format="PNG")
                try:
                    with open(meta_path, "w", encoding="utf-8") as handle:
                        meta_payload = dict(best_picture)
                        meta_payload["version"] = thumbnail_cache_version
                        json.dump(meta_payload, handle)
                except Exception:
                    pass
                return FileResponse(cache_path, media_type="image/png")
            except Exception:
                from io import BytesIO

                buf = BytesIO()
                crop.save(buf, format="PNG")
                return Response(content=buf.getvalue(), media_type="image/png")
        try:
            char = server.vault.db.run_immediate_read_task(
                Character.find, select_fields=[field], id=id
            )
            if not char:
                raise KeyError("Character not found")
            char = char[0]
            logger.debug(
                "Data type for Character field {}: {}".format(field, type(char))
            )
            if not hasattr(char, field):
                raise HTTPException(
                    status_code=404, detail=f"Field {field} not found in Character"
                )
            returnValue = {field: safe_model_dict(getattr(char, field))}
            logger.debug(
                f"Returning character id={id} field={field} value={returnValue}"
            )
            return returnValue
        except KeyError:
            raise HTTPException(status_code=404, detail="Character not found")

    @router.get("/characters")
    async def get_characters(name: str = Query(None)):
        try:
            logger.debug(f"Fetching characters with name: {name}")
            characters = server.vault.db.run_immediate_read_task(
                lambda session: Character.find(session, name=name)
            )
            return characters
        except KeyError:
            logger.error("Character not found")
            raise HTTPException(status_code=404, detail="Character not found")
        except Exception as e:
            logger.error(f"Error fetching characters: {e}")
            raise HTTPException(status_code=500, detail="Internal Server Error")

    @router.post("/characters")
    async def create_character(payload: dict = Body(...)):
        try:

            def create_character_and_reference_set(session, payload):
                character = Character(**payload)
                session.add(character)
                session.commit()
                session.refresh(character)
                logger.debug("Created character with ID: {}".format(character.id))
                reference_set = PictureSet(
                    name="reference_pictures", description=str(character.name)
                )
                session.add(reference_set)
                session.commit()
                session.refresh(reference_set)
                character.reference_picture_set_id = reference_set.id
                session.add(character)
                session.commit()
                session.refresh(character)
                return character.model_dump(exclude_unset=False)

            char_dict = server.vault.db.run_task(
                create_character_and_reference_set,
                payload,
                priority=DBPriority.IMMEDIATE,
            )
            logger.debug("Created character: {}".format(char_dict))
            server.vault.notify(EventType.CHANGED_CHARACTERS)
            return {"status": "success", "character": char_dict}
        except Exception as e:
            logger.error(f"Error creating character: {e}")
            raise HTTPException(status_code=400, detail="Invalid character data")

    @router.post("/characters/{character_id}/faces")
    async def assign_face_to_character(character_id: int, payload: dict = Body(...)):
        face_ids = payload.get("face_ids")
        picture_ids = payload.get("picture_ids")
        if face_ids is not None and not isinstance(face_ids, list):
            raise HTTPException(status_code=400, detail="face_ids must be a list")
        if picture_ids is not None and not isinstance(picture_ids, list):
            raise HTTPException(status_code=400, detail="picture_ids must be a list")

        def assign_faces(
            session: Session,
            face_ids: list[int],
            picture_ids: list[str],
            character_id: int,
        ):
            faces_to_assign = []
            existing_faces = []
            if picture_ids:
                for pic_id in picture_ids:
                    faces = Face.find(session, picture_id=pic_id)
                    if not faces:
                        continue

                    def face_area(face):
                        try:
                            return (face.width or 0) * (face.height or 0)
                        except Exception:
                            return 0

                    largest_face = max(faces, key=face_area)
                    if largest_face.character_id == character_id:
                        existing_faces.append(largest_face)
                    else:
                        faces_to_assign.append(largest_face)
            if face_ids:
                for face_id in face_ids:
                    face = session.get(Face, face_id)
                    if not face:
                        raise HTTPException(
                            status_code=404, detail=f"Face {face_id} not found"
                        )
                    if face.character_id == character_id:
                        existing_faces.append(face)
                    else:
                        faces_to_assign.append(face)
            unique_faces = {face.id: face for face in faces_to_assign}.values()
            for face in unique_faces:
                face.character_id = character_id
                session.add(face)
            session.commit()
            for face in unique_faces:
                session.refresh(face)
            faces_payload = [
                {
                    "id": face.id,
                    "picture_id": face.picture_id,
                    "character_id": face.character_id,
                }
                for face in unique_faces
            ]
            existing_face_ids = [face.id for face in existing_faces]
            return faces_payload, existing_face_ids

        faces, existing_face_ids = server.vault.db.run_task(
            assign_faces,
            face_ids,
            picture_ids,
            character_id,
            priority=DBPriority.IMMEDIATE,
        )
        if not faces and len(existing_face_ids) > 0:
            if len(existing_face_ids) == 1:
                detail = (
                    f"Face {existing_face_ids[0]} is already assigned to this character"
                )
            else:
                detail = "All faces are already assigned to this character"
            raise HTTPException(
                status_code=403,
                detail=detail,
            )
        server.vault.db.run_task(
            Picture.clear_field,
            [face["picture_id"] for face in faces],
            "text_embedding",
        )
        for face in faces:
            if face["character_id"] != character_id:
                raise HTTPException(
                    status_code=500,
                    detail=(
                        f"Failed to set character {character_id} for face {face['id']}"
                    ),
                )
        server.vault.notify(EventType.CHANGED_CHARACTERS)
        server.vault.notify(EventType.CHANGED_FACES)
        return {
            "status": "success",
            "face_ids": [face["id"] for face in faces],
            "character_id": character_id,
        }

    @router.delete("/characters/{character_id}/faces")
    async def remove_character_from_faces(character_id: int, payload: dict = Body(...)):
        face_ids = payload.get("face_ids", None)
        picture_ids = payload.get("picture_ids", None)
        if not isinstance(face_ids, list) and not isinstance(picture_ids, list):
            raise HTTPException(
                status_code=400,
                detail="Must send a list of picture_ids or face_ids",
            )

        def remove_faces_from_character(
            session: Session,
            character_id: int,
            face_ids: list[int] = None,
            picture_ids: list[str] = None,
        ):
            faces = []
            if picture_ids:
                for pic_id in picture_ids:
                    pic_faces = Face.find(session, picture_id=pic_id)
                    for face in pic_faces:
                        if face.character_id == character_id:
                            face.character_id = None
                            session.add(face)
                            faces.append(face)
            elif face_ids:
                for face_id in face_ids:
                    face = session.get(Face, face_id)
                    if face and face.character_id == character_id:
                        face.character_id = None
                        session.add(face)
            session.commit()
            session.refresh(face)
            return faces

        server.vault.db.run_task(
            remove_faces_from_character,
            character_id,
            face_ids,
            picture_ids,
            priority=DBPriority.IMMEDIATE,
        )

        server.vault.db.run_task(Picture.clear_field, picture_ids, "text_embedding")
        server.vault.notify(EventType.CHANGED_CHARACTERS)
        server.vault.notify(EventType.CHANGED_FACES)
        return {
            "status": "success",
            "face_ids": face_ids,
            "character_id": character_id,
        }

    return router
