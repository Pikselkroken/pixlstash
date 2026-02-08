import sys

from fastapi import APIRouter, Body, HTTPException, Query, Request
from sqlalchemy.orm import selectinload
from sqlmodel import select

from pixlvault.database import DBPriority
from pixlvault.db_models import (
    Character,
    Picture,
    PictureSet,
    PictureSetMember,
    SortMechanism,
)
from pixlvault.event_types import EventType
from pixlvault.pixl_logging import get_logger
from pixlvault.picture_scoring import (
    find_pictures_by_character_likeness,
    find_pictures_by_smart_score,
    get_smart_score_penalized_tags_from_request,
)
from pixlvault.utils import safe_model_dict

logger = get_logger(__name__)


def create_router(server) -> APIRouter:
    router = APIRouter()

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

    @router.get("/picture_sets")
    async def get_picture_sets():
        def fetch_sets(session):
            sets = session.exec(
                select(PictureSet).options(selectinload(PictureSet.reference_character))
            ).all()
            result = []
            for s in sets:
                members = session.exec(
                    select(PictureSetMember).where(PictureSetMember.set_id == s.id)
                ).all()
                count = len({m.picture_id for m in members if m is not None})
                set_dict = safe_model_dict(s)
                set_dict["picture_count"] = count
                result.append(set_dict)
            return result

        result = safe_model_dict(server.vault.db.run_immediate_read_task(fetch_sets))
        logger.debug(f"Fetched picture set {result}")
        return result

    @router.post("/picture_sets")
    async def create_picture_set(payload: dict = Body(...)):
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

    @router.get("/picture_sets/{id}")
    async def get_picture_set(
        request: Request,
        id: int,
        info: bool = Query(False),
        sort: str = Query(None),
        descending: bool = Query(True),
        format: list[str] = Query(None),
        character_id: str | None = Query(None),
        reference_character_id: str | None = Query(None),
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
                select(PictureSetMember).where(PictureSetMember.set_id == id)
            ).all()
            seen = set()
            picture_ids = []
            for member in members:
                if not member:
                    continue
                pic_id = member.picture_id
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
        if info:
            set_dict = picture_set.dict()
            set_dict["picture_count"] = len(picture_ids)
            return set_dict

        if sort_mech and sort_mech.key == SortMechanism.Keys.SMART_SCORE:
            penalized_tags = get_smart_score_penalized_tags_from_request(
                server, request
            )
            pictures = find_pictures_by_smart_score(
                server,
                format,
                0,
                sys.maxsize,
                descending,
                candidate_ids=picture_ids,
                penalized_tags=penalized_tags,
            )
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
            return {"pictures": pictures, "set": safe_model_dict(picture_set)}

        def fetch_pics(session, picture_ids):
            pics = Picture.find(
                session,
                id=picture_ids,
                sort_mech=sort_mech,
                select_fields=Picture.metadata_fields(),
                format=format,
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
        return {"pictures": pictures, "set": safe_model_dict(picture_set)}

    @router.patch("/picture_sets/{id}")
    async def update_picture_set(id: int, payload: dict = Body(...)):
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

    @router.delete("/picture_sets/{id}")
    async def delete_picture_set(id: int):
        def delete_set(session, id):
            picture_set = session.get(PictureSet, id)
            if not picture_set:
                return False
            members = session.exec(
                select(PictureSetMember).where(PictureSetMember.set_id == id)
            ).all()
            for member in members:
                session.delete(member)
            session.delete(picture_set)
            session.commit()
            return True

        success = server.vault.db.run_task(
            delete_set, id, priority=DBPriority.IMMEDIATE
        )
        if not success:
            raise HTTPException(status_code=404, detail="Picture set not found")
        return {"status": "success", "deleted_id": id}

    @router.get("/picture_sets/{id}/members")
    async def get_picture_set_pictures(id: int):
        def fetch_members(session, id):
            picture_set = session.get(PictureSet, id)
            if not picture_set:
                return None
            members = session.exec(
                select(PictureSetMember).where(PictureSetMember.set_id == id)
            ).all()
            return list({m.picture_id for m in members if m is not None})

        picture_ids = server.vault.db.run_immediate_read_task(fetch_members, id)
        if picture_ids is None:
            raise HTTPException(status_code=404, detail="Picture set not found")
        return {"picture_ids": picture_ids}

    @router.post("/picture_sets/{id}/members/{picture_id}")
    async def add_picture_to_set(id: int, picture_id: str):
        reference_character_id = _find_reference_character_id_for_set(id)

        def add_member(session, id, picture_id, reference_character_id=None):
            picture_set = session.get(PictureSet, id)
            picture = session.get(Picture, picture_id)
            if not picture_set or not picture:
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

    @router.delete("/picture_sets/{id}/members/{picture_id}")
    async def remove_picture_from_set(id: int, picture_id: str):
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
