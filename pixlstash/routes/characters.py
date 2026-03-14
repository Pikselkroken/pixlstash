import ast
import json
import os
import time
import cv2
from fastapi import APIRouter, Body, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from PIL import Image
from sqlalchemy import case as sa_case, exists, func
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    Character,
    Face,
    Picture,
    PictureSet,
    PictureSetMember,
    Tag,
)
from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.image_processing.video_utils import VideoUtils
from pixlstash.picture_scoring import select_reference_faces_for_character
from pixlstash.utils.service.caption_utils import _normalize_hidden_tags
from pixlstash.utils.service.serialization_utils import safe_model_dict

logger = get_logger(__name__)


def create_router(server) -> APIRouter:
    router = APIRouter()

    def _get_hidden_tags_from_request(request: Request) -> list[str]:
        try:
            user = server.auth.get_user_for_request(request)
        except HTTPException:
            user = server.auth.get_user()
        if not user or not getattr(user, "apply_tag_filter", False):
            return []
        normalized = _normalize_hidden_tags(getattr(user, "hidden_tags", None))
        return normalized or []

    @router.get(
        "/characters/{id}/summary",
        summary="Get character category summary",
        description="Returns summary counts and thumbnail reference for ALL, UNASSIGNED, SCRAPHEAP, or a specific character id.",
    )
    def get_characters_summary(request: Request, id: str = None):
        """
        Return summary statistics for a single category:
        - If character_id is ALL: all pictures
        - If character_id is UNASSIGNED: unassigned pictures
        - If character_id is set: that character's pictures
        """
        start = time.time()
        hidden_tags = _get_hidden_tags_from_request(request)
        hidden_tag_set = {str(tag).strip().lower() for tag in hidden_tags if tag}
        hidden_tag_filter = None
        if hidden_tag_set:
            hidden_tag_filter = ~exists(
                select(Tag.id).where(
                    Tag.picture_id == Picture.id,
                    Tag.tag.is_not(None),
                    func.lower(Tag.tag).in_(hidden_tag_set),
                )
            )

        if id == "ALL":

            def count_all(session: Session) -> int:
                conditions = [
                    Picture.deleted.is_(False),
                ]
                if hidden_tag_filter is not None:
                    conditions.append(hidden_tag_filter)
                return session.exec(
                    select(func.count(Picture.id)).where(*conditions)
                ).one()

            image_count = server.vault.db.run_immediate_read_task(count_all)
            logger.debug("ALL pics count: {}".format(image_count))
            char_id = None
        elif id == "SCRAPHEAP":

            def count_scrapheap(session: Session) -> int:
                conditions = [
                    Picture.deleted.is_(True),
                ]
                if hidden_tag_filter is not None:
                    conditions.append(hidden_tag_filter)
                return session.exec(
                    select(func.count(Picture.id)).where(*conditions)
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
                conditions = [
                    Picture.deleted.is_(False),
                    ~face_exists,
                    ~set_exists,
                ]
                if hidden_tag_filter is not None:
                    conditions.append(hidden_tag_filter)
                return session.exec(
                    select(func.count(Picture.id)).where(*conditions)
                ).one()

            image_count = server.vault.db.run_immediate_read_task(count_unassigned)
            logger.debug("UNASSIGNED pics count: {}".format(image_count))
            char_id = None
        else:

            def count_assigned(session: Session, character_id: int) -> int:
                conditions = [
                    Face.character_id == character_id,
                    Picture.deleted.is_(False),
                ]
                if hidden_tag_filter is not None:
                    conditions.append(hidden_tag_filter)
                return session.exec(
                    select(func.count(func.distinct(Face.picture_id)))
                    .join(Picture, Face.picture_id == Picture.id)
                    .where(*conditions)
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

    @router.get(
        "/characters/{id}/reference_pictures",
        summary="List reference pictures",
        description="Returns picture ids selected as reference faces for the given character.",
    )
    def get_character_reference_pictures(id: int):
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

    @router.patch(
        "/characters/{id}",
        summary="Update character",
        description="Updates character fields and clears dependent picture text embeddings when identity data changes.",
    )
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

    @router.delete(
        "/characters/{id}",
        summary="Delete character",
        description="Deletes a character, clears character assignment from faces, and removes its reference set when present.",
    )
    def delete_character(id: int):
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

    @router.get(
        "/characters/{id}",
        summary="Get character by id",
        description="Returns a single character record by id.",
    )
    def get_character_by_id(id: int):
        try:
            char = server.vault.db.run_immediate_read_task(
                lambda session: Character.find(session, id=id)
            )
            return char[0] if char else None
        except KeyError:
            raise HTTPException(status_code=404, detail="Character not found")

    @router.get(
        "/characters/{id}/{field}",
        summary="Get character field",
        description="Returns one character field value, including generated thumbnail handling for field=thumbnail.",
    )
    def get_character_field_by_id(id: int, field: str):
        if field == "thumbnail":
            thumbnail_cache_version = 6
            cache_dir = os.path.join(server.vault.image_root, "tmp", "face_thumbnails")
            os.makedirs(cache_dir, exist_ok=True)
            cache_path = os.path.join(cache_dir, f"character_{id}.png")
            meta_path = os.path.join(cache_dir, f"character_{id}.json")

            def fetch_best_picture_id(session: Session, character_id: int):
                _video_exts = (".mp4", ".mov", ".webm", ".avi", ".mkv")
                is_video_expr = sa_case(
                    *[(Picture.file_path.ilike(f"%{ext}"), 1) for ext in _video_exts],
                    else_=0,
                )
                row = session.exec(
                    select(Picture.id, Picture.score)
                    .join(Face, Face.picture_id == Picture.id)
                    .where(
                        Face.character_id == character_id,
                        Picture.deleted.is_(False),
                    )
                    .order_by(
                        is_video_expr,  # prefer still images over videos
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
                pics = sorted(members, key=lambda p: p.score or 0, reverse=True)
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

            picture_path = ImageUtils.resolve_picture_path(
                server.vault.image_root, best_pic.file_path
            )
            if isinstance(bbox, str):
                try:
                    bbox = ast.literal_eval(bbox)
                except Exception:
                    bbox = None
            if not bbox or not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                raise HTTPException(
                    status_code=404, detail="Failed to crop face thumbnail"
                )
            try:
                if VideoUtils.is_video_file(picture_path):
                    frame_bgr = VideoUtils._read_first_video_frame_bgr(picture_path)
                    if frame_bgr is None:
                        raise ValueError("Could not read first frame from video")
                    frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                    image = Image.fromarray(frame_rgb)
                else:
                    image = Image.open(picture_path).convert("RGB")
            except Exception:
                raise HTTPException(
                    status_code=404, detail="Failed to crop face thumbnail"
                )
            image_width, image_height = image.size
            x1, y1, x2, y2 = [float(v) for v in bbox]
            x1 = max(0.0, min(float(image_width - 1), x1))
            y1 = max(0.0, min(float(image_height - 1), y1))
            x2 = max(0.0, min(float(image_width), x2))
            y2 = max(0.0, min(float(image_height), y2))
            if x2 <= x1 or y2 <= y1:
                raise HTTPException(
                    status_code=404, detail="Failed to crop face thumbnail"
                )
            side = max(x2 - x1, y2 - y1)
            cx = (x1 + x2) / 2.0
            cy = (y1 + y2) / 2.0
            new_x1 = cx - side / 2.0
            new_x2 = cx + side / 2.0
            new_y1 = cy - side / 2.0
            new_y2 = cy + side / 2.0
            if new_x1 < 0:
                new_x2 -= new_x1
                new_x1 = 0.0
            if new_x2 > image_width:
                shift = new_x2 - image_width
                new_x1 -= shift
                new_x2 = float(image_width)
            if new_y1 < 0:
                new_y2 -= new_y1
                new_y1 = 0.0
            if new_y2 > image_height:
                shift = new_y2 - image_height
                new_y1 -= shift
                new_y2 = float(image_height)
            new_x1 = max(0.0, min(float(image_width - 1), new_x1))
            new_y1 = max(0.0, min(float(image_height - 1), new_y1))
            new_x2 = max(0.0, min(float(image_width), new_x2))
            new_y2 = max(0.0, min(float(image_height), new_y2))
            crop = image.crop(
                (
                    int(round(new_x1)),
                    int(round(new_y1)),
                    int(round(new_x2)),
                    int(round(new_y2)),
                )
            )
            crop = crop.resize((64, 64), Image.LANCZOS)
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

    @router.get(
        "/characters",
        summary="List characters",
        description="Lists characters, optionally filtered by exact name.",
    )
    def get_characters(name: str = Query(None)):
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

    @router.post(
        "/characters",
        summary="Create character",
        description="Creates a character and its linked reference picture set.",
    )
    def create_character(payload: dict = Body(...)):
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

    @router.post(
        "/characters/{character_id}/faces",
        summary="Assign faces to character",
        description="Assigns provided face ids or largest faces from picture ids to a character.",
    )
    def assign_face_to_character(character_id: int, payload: dict = Body(...)):
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

    @router.delete(
        "/characters/{character_id}/faces",
        summary="Unassign faces from character",
        description="Removes character assignment from provided face ids or from faces in provided picture ids.",
    )
    def remove_character_from_faces(character_id: int, payload: dict = Body(...)):
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
