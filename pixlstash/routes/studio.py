"""The Studio's routes (#2819): scenes, takes, items and favourites.

Owner only, every one: the Studio is where the owner makes pictures, and like
the workflow library it is no part of a shared view. See
``db_models/studio.py`` for what is stored and ``services/studio.py`` for how
a shot is written.
"""

from datetime import datetime, timezone
from typing import Literal, Optional, Union

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    Character,
    Picture,
    PictureSet,
    PictureSetMember,
    Project,
    character_in_no_project,
    character_in_project,
    picture_set_in_no_project,
    picture_set_in_project,
)
from pixlstash.db_models.studio import (
    FAVOURITE_KINDS,
    ITEM_KINDS,
    Scene,
    SceneTake,
    StudioFavourite,
    StudioItem,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.services import studio
from pixlstash.services.comfyui_service import run_outcome

logger = get_logger(__name__)

# What a run that ended leaves the Studio to say; a running one stays.
_ENDED = ("completed", "failed")


class CastMember(BaseModel):
    model_config = ConfigDict(extra="ignore")

    character_id: int
    outfit_set_id: Optional[int] = None
    doing: str = ""
    pose: str = ""
    brings: Literal["text", "lora", "refs"] = "refs"
    box: Optional[list[float]] = Field(default=None, min_length=4, max_length=4)


class PropPlacement(BaseModel):
    model_config = ConfigDict(extra="ignore")

    set_id: int
    state: str = ""
    held_by: Optional[int] = None
    box: Optional[list[float]] = Field(default=None, min_length=4, max_length=4)


class Camera(BaseModel):
    model_config = ConfigDict(extra="ignore")

    shot: Literal["wide", "medium", "close"] = "medium"
    view: int = Field(default=0, ge=0, le=3)


class Setting(BaseModel):
    model_config = ConfigDict(extra="ignore")

    weather: str = ""
    time: str = ""
    happening: str = ""
    light: str = ""


def _named(value: Optional[str]) -> Optional[str]:
    """A name with its spaces taken off; one of spaces alone is no name."""
    if value is None:
        return None
    if not value.strip():
        raise ValueError("The scene needs a name.")
    return value.strip()


class SceneCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)

    _name = field_validator("name")(_named)
    project_id: Optional[int] = None
    location_set_id: Optional[int] = None


class SceneUpdate(BaseModel):
    """Only the fields sent change; a list is replaced whole."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    project_id: Optional[int] = None
    location_set_id: Optional[int] = None
    camera: Optional[Camera] = None

    _name = field_validator("name")(_named)
    setting: Optional[Setting] = None
    cast: Optional[list[CastMember]] = Field(default=None, max_length=32)
    props: Optional[list[PropPlacement]] = Field(default=None, max_length=32)


class ShotRequest(BaseModel):
    kind: Literal["still", "clip"] = "still"
    from_picture_id: Optional[int] = None
    max_references: int = Field(default=8, ge=0, le=16)


class Shot(BaseModel):
    prompt: str
    negative: str
    references: list[int]
    loras: list[str]


class RunStarted(BaseModel):
    key: str = Field(min_length=1, max_length=200)
    kind: Literal["still", "clip"] = "still"
    from_picture_id: Optional[int] = None


class RunsStarted(BaseModel):
    runs: list[RunStarted] = Field(min_length=1, max_length=64)


class ItemKind(BaseModel):
    kind: Literal["outfit", "location", "prop"]


class FavouriteToggle(BaseModel):
    kind: Literal["scene", "cast", "outfit", "location", "prop"]
    item_id: int
    favourite: bool


class SceneDeleted(BaseModel):
    status: str
    id: int


class ItemKindSet(BaseModel):
    set_id: int
    kind: str


class ItemRemoved(BaseModel):
    set_id: int
    removed: bool


class FavouriteSet(BaseModel):
    kind: str
    item_id: int
    favourite: bool


class SceneResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int
    name: str
    project_id: Optional[int] = None
    location_set_id: Optional[int] = None
    camera: dict
    setting: dict
    cast: list
    props: list
    runs: list
    takes: list
    ended: list = []
    favourite: bool = False


class Overview(BaseModel):
    model_config = ConfigDict(extra="allow")

    scenes: list
    cast: list
    outfits: list
    locations: list
    props: list


def _parse_scope(project: Optional[str]) -> Union[None, str, int]:
    """``any`` (or nothing) → None, ``none`` → "none", a number → that id."""
    if project in (None, "", "any"):
        return None
    if project == "none":
        return "none"
    try:
        return int(project)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="project_id is none or a project id"
        ) from exc


def create_router(server) -> APIRouter:
    router = APIRouter()
    db = server.vault.db

    def _favourites(session: Session) -> set[tuple[str, int]]:
        return {
            (row.kind, row.item_id) for row in session.exec(select(StudioFavourite))
        }

    def _scene(session: Session, scene_id: int) -> Scene:
        scene = session.get(Scene, scene_id)
        if scene is None:
            raise HTTPException(status_code=404, detail="No scene with that id.")
        return scene

    def _check_refs(session: Session, payload, holders=None) -> None:
        """Every id the request names is there: a 404 names the one that is not.

        Only what is sent is checked, so a scene whose person or set was
        deleted since can still be renamed. A prop's holder must be in
        *holders*, the cast the scene will have (the sent one by default).
        """
        if getattr(payload, "project_id", None) is not None:
            if session.get(Project, payload.project_id) is None:
                raise HTTPException(status_code=404, detail="No project with that id.")
        set_ids = {payload.location_set_id} if payload.location_set_id else set()
        character_ids: set[int] = set()
        for member in getattr(payload, "cast", None) or []:
            character_ids.add(member.character_id)
            if member.outfit_set_id is not None:
                set_ids.add(member.outfit_set_id)
        if holders is None:
            holders = character_ids
        for prop in getattr(payload, "props", None) or []:
            set_ids.add(prop.set_id)
            if prop.held_by is not None and prop.held_by not in holders:
                raise HTTPException(
                    status_code=422,
                    detail="A prop can only be held by someone in the scene's cast.",
                )
        for set_id in set_ids:
            if session.get(PictureSet, set_id) is None:
                raise HTTPException(
                    status_code=404, detail=f"No picture set with id {set_id}."
                )
        for character_id in character_ids:
            if session.get(Character, character_id) is None:
                raise HTTPException(
                    status_code=404, detail=f"No person with id {character_id}."
                )

    def _scene_body(session: Session, scene: Scene, ended=()) -> dict:
        takes = session.exec(
            select(SceneTake)
            .where(SceneTake.scene_id == scene.id)
            .order_by(SceneTake.created_at.desc(), SceneTake.id.desc())
        ).all()
        formats = (
            dict(
                session.exec(
                    select(Picture.id, Picture.format).where(
                        Picture.id.in_([take.picture_id for take in takes])
                    )
                ).all()
            )
            if takes
            else {}
        )
        return {
            "id": scene.id,
            "name": scene.name,
            "project_id": scene.project_id,
            "location_set_id": scene.location_set_id,
            "camera": scene.camera or {},
            "setting": scene.setting or {},
            "cast": scene.cast or [],
            "props": scene.props or [],
            "runs": scene.runs or [],
            "takes": [
                {
                    "picture_id": take.picture_id,
                    "kind": take.kind,
                    "from_picture_id": take.from_picture_id,
                    "format": formats.get(take.picture_id),
                    "created_at": take.created_at,
                }
                for take in takes
            ],
            "ended": list(ended),
            "favourite": ("scene", scene.id) in _favourites(session),
        }

    def _file_ended_runs(session: Session, scene: Scene, library_uuid) -> list[dict]:
        """File each of the scene's runs that has ended as its takes.

        A run PixlStash no longer knows (it restarted) ends as ``lost``: its
        pictures, if it made any, are in the library but not in the scene.
        """
        still: list[dict] = []
        ended: list[dict] = []
        # A picture is one take, however many runs or reads reach it.
        filed = set(
            session.exec(
                select(SceneTake.picture_id).where(SceneTake.scene_id == scene.id)
            ).all()
        )
        for run in scene.runs or []:
            outcome = run_outcome(run["key"])
            started_in = (outcome or {}).get("library_uuid")
            if outcome is not None and started_in and started_in != library_uuid:
                outcome = None
            if outcome is None:
                logger.warning(
                    "Scene %s's run %s is no longer followed; its takes, if any, "
                    "were not filed in the scene.",
                    scene.id,
                    run["key"],
                )
                ended.append({"key": run["key"], "status": "lost", "message": None})
                continue
            if outcome["status"] not in _ENDED:
                still.append(run)
                continue
            ids = [
                pid
                for pid in dict.fromkeys(outcome.get("picture_ids") or [])
                if pid not in filed and session.get(Picture, pid) is not None
            ]
            for pid in ids:
                filed.add(pid)
                session.add(
                    SceneTake(
                        scene_id=scene.id,
                        picture_id=pid,
                        kind=run.get("kind") or "still",
                        from_picture_id=run.get("from_picture_id"),
                    )
                )
            ended.append(
                {
                    "key": run["key"],
                    "status": outcome["status"],
                    "message": outcome.get("message"),
                    "picture_count": len(ids),
                }
            )
        if ended:
            scene.runs = still
            # New takes make it the scene shot last.
            scene.updated_at = datetime.now(timezone.utc)
            session.add(scene)
            session.commit()
            session.refresh(scene)
        return ended

    @router.get(
        "/studio/overview",
        summary="Everything the Studio lists",
        description=(
            "The scenes, cast, outfits, locations and props in one scope, for "
            "the Studio's sidebar and grids. `project_id` is a project's id, "
            "`none` (in no project) or absent (any project). Outfits follow the person "
            "who wears them; a person's `outfit_ids` are theirs. Each item's "
            "`picture_ids` are its angles in order."
        ),
        response_model=Overview,
    )
    def overview(request: Request, project_id: Optional[str] = None):
        scope = _parse_scope(project_id)

        def fetch(session: Session):
            favourites = _favourites(session)
            scene_query = select(Scene)
            if scope == "none":
                scene_query = scene_query.where(Scene.project_id.is_(None))
            elif scope is not None:
                scene_query = scene_query.where(Scene.project_id == scope)
            scenes = session.exec(scene_query.order_by(Scene.updated_at.desc())).all()
            take_counts = dict(
                session.exec(
                    select(SceneTake.scene_id, func.count(SceneTake.id)).group_by(
                        SceneTake.scene_id
                    )
                ).all()
            )
            covers = dict(
                session.exec(
                    select(SceneTake.scene_id, func.max(SceneTake.picture_id)).group_by(
                        SceneTake.scene_id
                    )
                ).all()
            )
            all_scenes = session.exec(select(Scene)).all()
            used: dict[tuple[str, int], int] = {}
            for scene in all_scenes:
                if scene.location_set_id:
                    key = ("location", scene.location_set_id)
                    used[key] = used.get(key, 0) + 1
                for member in scene.cast or []:
                    for key in (
                        ("cast", member.get("character_id")),
                        ("outfit", member.get("outfit_set_id")),
                    ):
                        used[key] = used.get(key, 0) + 1
                for entry in scene.props or []:
                    key = ("prop", entry.get("set_id"))
                    used[key] = used.get(key, 0) + 1

            char_query = select(Character)
            set_scope = None
            if scope == "none":
                char_query = char_query.where(character_in_no_project())
                set_scope = picture_set_in_no_project()
            elif scope is not None:
                char_query = char_query.where(character_in_project(scope))
                set_scope = picture_set_in_project(scope)
            people = [
                c
                for c in session.exec(char_query.order_by(Character.name)).all()
                if (c.name or "").strip()
            ]
            people_ids = {c.id for c in people}

            item_rows = session.exec(
                select(StudioItem, PictureSet)
                .join(PictureSet, PictureSet.id == StudioItem.set_id)
                .order_by(PictureSet.name)
            ).all()
            in_scope: Optional[set[int]] = None
            if set_scope is not None:
                in_scope = set(
                    session.exec(select(PictureSet.id).where(set_scope)).all()
                )
            set_ids = [row[1].id for row in item_rows]
            members: dict[int, list[int]] = {sid: [] for sid in set_ids}
            if set_ids:
                for set_id, picture_id in session.exec(
                    select(PictureSetMember.set_id, PictureSetMember.picture_id)
                    .where(PictureSetMember.set_id.in_(set_ids))
                    .order_by(PictureSetMember.picture_id)
                ).all():
                    members[set_id].append(picture_id)

            lists: dict[str, list] = {kind: [] for kind in ITEM_KINDS}
            for row, pset in item_rows:
                if row.kind == "outfit":
                    # An outfit is in scope with its wearer.
                    if pset.parent_character_id is None:
                        if in_scope is not None and pset.id not in in_scope:
                            continue
                    elif pset.parent_character_id not in people_ids:
                        continue
                elif in_scope is not None and pset.id not in in_scope:
                    continue
                lists[row.kind].append(
                    {
                        "id": pset.id,
                        "kind": row.kind,
                        "name": pset.name,
                        "description": pset.description or "",
                        "character_id": pset.parent_character_id,
                        "picture_ids": members.get(pset.id, []),
                        "scene_count": used.get((row.kind, pset.id), 0),
                        "favourite": (row.kind, pset.id) in favourites,
                    }
                )
            outfit_ids: dict[int, list[int]] = {}
            for outfit in lists["outfit"]:
                if outfit["character_id"] is not None:
                    outfit_ids.setdefault(outfit["character_id"], []).append(
                        outfit["id"]
                    )
            return {
                "scenes": [
                    {
                        "id": s.id,
                        "kind": "scene",
                        "name": s.name,
                        "project_id": s.project_id,
                        "location_set_id": s.location_set_id,
                        "cast_ids": [m.get("character_id") for m in (s.cast or [])],
                        "outfit_ids": [
                            m["outfit_set_id"]
                            for m in (s.cast or [])
                            if m.get("outfit_set_id") is not None
                        ],
                        "prop_ids": [p.get("set_id") for p in (s.props or [])],
                        "take_count": take_counts.get(s.id, 0),
                        "running": len(s.runs or []),
                        "cover_picture_id": covers.get(s.id),
                        "updated_at": s.updated_at,
                        "favourite": ("scene", s.id) in favourites,
                    }
                    for s in scenes
                ],
                "cast": [
                    {
                        "id": c.id,
                        "kind": "cast",
                        "name": c.name,
                        "description": c.description or "",
                        "thumbnail_picture_id": c.thumbnail_picture_id,
                        "outfit_ids": outfit_ids.get(c.id, []),
                        "scene_count": used.get(("cast", c.id), 0),
                        "favourite": ("cast", c.id) in favourites,
                    }
                    for c in people
                ],
                "outfits": lists["outfit"],
                "locations": lists["location"],
                "props": lists["prop"],
            }

        return db.run_immediate_read_task(fetch)

    @router.post(
        "/studio/scenes",
        summary="Make a scene",
        description="A new scene, empty but for its name, project and location.",
        response_model=SceneResponse,
        status_code=201,
    )
    def create_scene(payload: SceneCreate):
        def insert(session: Session):
            _check_refs(session, payload)
            scene = Scene(
                name=payload.name.strip(),
                project_id=payload.project_id,
                location_set_id=payload.location_set_id,
                camera={"shot": "medium", "view": 0},
            )
            session.add(scene)
            session.commit()
            session.refresh(scene)
            return _scene_body(session, scene)

        return db.run_task(insert, priority=DBPriority.IMMEDIATE)

    @router.get(
        "/studio/scenes/{scene_id}",
        summary="One scene and its takes",
        description=(
            "The scene, newest take first. Each of its shoots that has ended "
            "since the last read is filed first: a finished run's pictures "
            "become takes, and `ended` says how each run ended (`completed`, "
            "`failed`, or `lost` when PixlStash restarted and no longer knows "
            "it). `runs` are the shoots still drawing."
        ),
        response_model=SceneResponse,
    )
    def get_scene(request: Request, scene_id: int):
        library_uuid = request.state.library_lease.library_uuid

        def read(session: Session):
            scene = _scene(session, scene_id)
            ended = _file_ended_runs(session, scene, library_uuid)
            return _scene_body(session, scene, ended)

        return db.run_task(read, priority=DBPriority.IMMEDIATE)

    @router.patch(
        "/studio/scenes/{scene_id}",
        summary="Change a scene",
        description=(
            "Only the fields sent change; `cast` and `props` are replaced "
            "whole. A prop's `held_by` must be in the cast. 404 for an id "
            "that names nothing."
        ),
        response_model=SceneResponse,
    )
    def update_scene(scene_id: int, payload: SceneUpdate):
        sent = payload.model_fields_set

        def update(session: Session):
            scene = _scene(session, scene_id)
            # A prop's holder is checked against the cast the scene will have.
            holders = (
                {m.character_id for m in payload.cast}
                if "cast" in sent and payload.cast is not None
                else {m.get("character_id") for m in scene.cast or []}
            )
            _check_refs(session, payload, holders)
            if payload.name is not None:
                scene.name = payload.name.strip()
            if "project_id" in sent:
                scene.project_id = payload.project_id
            if "location_set_id" in sent:
                scene.location_set_id = payload.location_set_id
            if payload.camera is not None:
                scene.camera = payload.camera.model_dump()
            if payload.setting is not None:
                scene.setting = payload.setting.model_dump()
            if payload.cast is not None:
                scene.cast = [m.model_dump() for m in payload.cast]
            if payload.props is not None:
                scene.props = [p.model_dump() for p in payload.props]
            if payload.cast is not None and payload.props is None:
                # Someone left the cast: what they held is put down.
                cast_ids = {m.character_id for m in payload.cast}
                scene.props = [
                    {
                        **p,
                        "held_by": p.get("held_by")
                        if p.get("held_by") in cast_ids
                        else None,
                    }
                    for p in scene.props or []
                ]
            scene.updated_at = datetime.now(timezone.utc)
            session.add(scene)
            session.commit()
            session.refresh(scene)
            return _scene_body(session, scene)

        return db.run_task(update, priority=DBPriority.IMMEDIATE)

    @router.delete(
        "/studio/scenes/{scene_id}",
        summary="Delete a scene",
        description=(
            "The scene and its list of takes go; the take pictures stay in the library."
        ),
        response_model=SceneDeleted,
    )
    def delete_scene(scene_id: int):
        def remove(session: Session):
            scene = _scene(session, scene_id)
            for take in session.exec(
                select(SceneTake).where(SceneTake.scene_id == scene_id)
            ).all():
                session.delete(take)
            favourite = session.get(StudioFavourite, ("scene", scene_id))
            if favourite is not None:
                session.delete(favourite)
            session.delete(scene)
            session.commit()
            return {"status": "deleted", "id": scene_id}

        return db.run_task(remove, priority=DBPriority.IMMEDIATE)

    @router.post(
        "/studio/scenes/{scene_id}/shot",
        summary="Write one shot of a scene",
        description=(
            "The prompt, negative, reference pictures and LoRAs one shoot of "
            "the scene sends to `POST /workflows/run`: the location's view the "
            "camera takes, each cast member in their outfit doing what the "
            "scene says, the props, and what the location is doing. Each "
            "reference is named in the prompt by its number, so only "
            "`max_references` (the workflow's picture inputs) are named. A "
            "clip `from_picture_id` (a still take) has that still as its one "
            "reference. Stores nothing."
        ),
        response_model=Shot,
    )
    def shot(scene_id: int, payload: ShotRequest):
        def read(session: Session):
            scene = _scene(session, scene_id)
            if payload.from_picture_id is not None:
                take = session.exec(
                    select(SceneTake).where(
                        SceneTake.scene_id == scene_id,
                        SceneTake.picture_id == payload.from_picture_id,
                    )
                ).first()
                if take is None:
                    raise HTTPException(
                        status_code=404,
                        detail="That picture is not one of this scene's takes.",
                    )
            return studio.shot_for(
                session,
                scene,
                kind=payload.kind,
                from_picture_id=payload.from_picture_id,
                max_references=payload.max_references,
            )

        return db.run_immediate_read_task(read)

    @router.post(
        "/studio/scenes/{scene_id}/runs",
        summary="Follow a scene's shoots",
        description=(
            "The runs `POST /workflows/run` started for this scene, by the "
            "`run_id` (or `prompt_id`) it answered with. Reading the scene "
            "then files each run's pictures as takes once it ends."
        ),
        response_model=SceneResponse,
    )
    def follow_runs(scene_id: int, payload: RunsStarted):
        def update(session: Session):
            scene = _scene(session, scene_id)
            known = {run["key"] for run in scene.runs or []}
            scene.runs = [*(scene.runs or [])] + [
                run.model_dump() for run in payload.runs if run.key not in known
            ]
            scene.updated_at = datetime.now(timezone.utc)
            session.add(scene)
            session.commit()
            session.refresh(scene)
            return _scene_body(session, scene)

        return db.run_task(update, priority=DBPriority.IMMEDIATE)

    @router.delete(
        "/studio/scenes/{scene_id}/takes/{picture_id}",
        summary="Take a picture out of a scene's takes",
        description="The picture stays in the library.",
        response_model=SceneResponse,
    )
    def remove_take(scene_id: int, picture_id: int):
        def update(session: Session):
            scene = _scene(session, scene_id)
            for take in session.exec(
                select(SceneTake).where(
                    SceneTake.scene_id == scene_id,
                    SceneTake.picture_id == picture_id,
                )
            ).all():
                session.delete(take)
            scene.updated_at = datetime.now(timezone.utc)
            session.add(scene)
            session.commit()
            return _scene_body(session, scene)

        return db.run_task(update, priority=DBPriority.IMMEDIATE)

    @router.put(
        "/studio/items/{set_id}",
        summary="Use a picture set in the Studio",
        description=(
            "Makes the set an outfit, a location or a prop: the sheet "
            "dialog's sets, or any set of pictures of one. An outfit is worn "
            "by its set's parent person. 404 for a set that is not there."
        ),
        response_model=ItemKindSet,
    )
    def put_item(set_id: int, payload: ItemKind):
        def update(session: Session):
            if session.get(PictureSet, set_id) is None:
                raise HTTPException(
                    status_code=404, detail="No picture set with that id."
                )
            row = session.get(StudioItem, set_id) or StudioItem(
                set_id=set_id, kind=payload.kind
            )
            row.kind = payload.kind
            session.add(row)
            session.commit()
            return {"set_id": set_id, "kind": payload.kind}

        return db.run_task(update, priority=DBPriority.IMMEDIATE)

    @router.delete(
        "/studio/items/{set_id}",
        summary="Stop using a picture set in the Studio",
        description="The set and its pictures stay; scenes using it keep the id.",
        response_model=ItemRemoved,
    )
    def delete_item(set_id: int):
        def update(session: Session):
            row = session.get(StudioItem, set_id)
            if row is not None:
                session.delete(row)
                session.commit()
            return {"set_id": set_id, "removed": row is not None}

        return db.run_task(update, priority=DBPriority.IMMEDIATE)

    @router.put(
        "/studio/favourites",
        summary="Star or unstar a Studio item",
        description=f"`kind` is one of {', '.join(FAVOURITE_KINDS)}.",
        response_model=FavouriteSet,
    )
    def put_favourite(payload: FavouriteToggle):
        def update(session: Session):
            row = session.get(StudioFavourite, (payload.kind, payload.item_id))
            if payload.favourite and row is None:
                session.add(StudioFavourite(kind=payload.kind, item_id=payload.item_id))
            elif not payload.favourite and row is not None:
                session.delete(row)
            session.commit()
            return {
                "kind": payload.kind,
                "item_id": payload.item_id,
                "favourite": payload.favourite,
            }

        return db.run_task(update, priority=DBPriority.IMMEDIATE)

    return router
