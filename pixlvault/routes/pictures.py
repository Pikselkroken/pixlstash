import ast
import asyncio
import concurrent.futures
import base64
import os
import re
import shutil
import sys
import uuid
import zipfile
from io import BytesIO
from collections import defaultdict, deque, OrderedDict
from email.utils import formatdate
from datetime import datetime

from PIL import Image
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import delete, exists, func
from sqlmodel import Session, select

from pixlvault.database import DBPriority
from pixlvault.db_models import (
    Character,
    Face,
    Picture,
    PictureLikeness,
    PictureSetMember,
    SortMechanism,
    Tag,
)
from pixlvault.event_types import EventType
from pixlvault.image_plugins.registry import get_image_plugin_manager
from pixlvault.image_plugins.service import apply_plugin_to_pictures
from pixlvault.pixl_logging import get_logger
from pixlvault.picture_scoring import (
    compute_character_likeness_for_faces,
    fetch_smart_score_data,
    find_pictures_by_character_likeness,
    find_pictures_by_smart_score,
    get_smart_score_penalised_tags_from_request,
    prepare_smart_score_inputs,
    select_reference_faces_for_character,
)
from pixlvault.utils.image_processing.image_utils import ImageUtils
from pixlvault.utils.quality.smart_score_utils import SmartScoreUtils
from pixlvault.utils import (
    safe_model_dict,
    serialize_tag_objects,
    _normalize_hidden_tags,
)
from pixlvault.tasks import TaskType

logger = get_logger(__name__)

MEDIA_TYPE_BY_FORMAT = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "tiff": "image/tiff",
    "mp4": "video/mp4",
    "webm": "video/webm",
    "mov": "video/quicktime",
    "avi": "video/x-msvideo",
    "mkv": "video/x-matroska",
    "m4v": "video/mp4",
}


def _get_hidden_tags_from_request(server, request: Request) -> list[str]:
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


def _fetch_hidden_picture_ids(server, request: Request, picture_ids: list[int]):
    hidden_tags = _get_hidden_tags_from_request(server, request)
    if not hidden_tags or not picture_ids:
        return set()
    hidden_tag_set = {str(tag).strip().lower() for tag in hidden_tags if tag}

    def fetch_hidden(session: Session, ids: list[int], tags: set[str]):
        rows = session.exec(
            select(Tag.picture_id).where(
                Tag.picture_id.in_(ids),
                Tag.tag.is_not(None),
                func.lower(Tag.tag).in_(tags),
            )
        ).all()
        return {row for row in rows if row is not None}

    return server.vault.db.run_immediate_read_task(
        fetch_hidden, list(picture_ids), hidden_tag_set
    )


def _create_picture_imports(server, uploaded_files, dest_folder):
    """
    Given a list of (img_bytes, ext), create Picture objects for new images,
    skipping duplicates based on pixel_sha hash.
    Returns (shas, existing_map, new_pictures)
    """

    def create_sha(img_bytes):
        return ImageUtils.calculate_hash_from_bytes(img_bytes)

    with concurrent.futures.ThreadPoolExecutor() as executor:
        shas = list(
            executor.map(create_sha, (img_bytes for img_bytes, _ in uploaded_files))
        )

    existing_pictures = server.vault.db.run_immediate_read_task(
        lambda session: Picture.find(session, pixel_shas=shas, include_unimported=True)
    )

    existing_map = {pic.pixel_sha: pic for pic in existing_pictures}

    importable = [
        (entry, sha)
        for (entry, sha) in zip(uploaded_files, shas)
        if sha not in existing_map
    ]

    if importable:

        def create_one_picture(args):
            file_entry, sha = args
            img_bytes, ext = file_entry
            pic_uuid = str(uuid.uuid4()) + ext
            logger.debug(f"Importing picture from uploaded bytes as id={pic_uuid}")
            return ImageUtils.create_picture_from_bytes(
                image_root_path=dest_folder,
                image_bytes=img_bytes,
                picture_uuid=pic_uuid,
                pixel_sha=sha,
            )

        with concurrent.futures.ThreadPoolExecutor() as executor:
            new_pictures = list(executor.map(create_one_picture, importable))
    else:
        new_pictures = []

    return shas, existing_map, new_pictures


def _select_pictures_for_listing(
    *,
    server,
    request: Request,
    sort,
    descending,
    offset,
    limit,
    metadata_fields,
    return_ids_only: bool = False,
    exclude_query_params: set[str] | None = None,
):
    def serialize_metadata(pictures):
        return [
            {field: safe_model_dict(pic).get(field) for field in metadata_fields}
            for pic in pictures
        ]

    def parse_request_params():
        query_params = {}
        format = None
        if request.query_params:
            format = request.query_params.getlist("format")
            query_params = dict(request.query_params)
            query_params.pop("format", None)
            if exclude_query_params:
                for key in exclude_query_params:
                    query_params.pop(key, None)
            picture_ids = request.query_params.getlist("id")
            if picture_ids:
                query_params["id"] = picture_ids
            else:
                query_params.pop("id", None)
        return format, query_params

    def _character_id(value):
        if value == "ALL":
            return None
        if value is not None and value != "" and str(value).isdigit():
            return int(value)
        return value

    format, query_params = parse_request_params()
    sort = query_params.pop("sort", sort)
    desc_val = query_params.pop("descending", descending)
    descending = (
        desc_val.lower() == "true" if isinstance(desc_val, str) else bool(desc_val)
    )
    offset = int(query_params.pop("offset", offset))
    limit = int(query_params.pop("limit", limit))
    character_id = _character_id(query_params.pop("character_id", None))
    reference_character_id = query_params.pop("reference_character_id", None)
    only_deleted = False

    try:
        sort_mech = (
            SortMechanism.from_string(sort, descending=descending) if sort else None
        )
    except ValueError as ve:
        logger.error(f"Invalid sort mechanism: {sort} - {ve}")
        raise HTTPException(status_code=400, detail=str(ve))

    pics = []
    if character_id == "SCRAPHEAP":
        only_deleted = True
        character_id = None

    def fetch_smart_score_candidate_ids(
        session: Session,
        character_id_value,
        deleted_only: bool,
        formats: list[str] | None,
    ):
        if deleted_only:
            query = select(Picture.id).where(
                Picture.deleted.is_(True),
            )
        elif character_id_value == "UNASSIGNED":
            unassigned_condition = ~exists(
                select(Face.id).where(
                    Face.picture_id == Picture.id,
                    Face.character_id.is_not(None),
                )
            )
            not_in_set_condition = ~exists(
                select(PictureSetMember.picture_id).where(
                    PictureSetMember.picture_id == Picture.id
                )
            )
            query = select(Picture.id).where(
                unassigned_condition,
                not_in_set_condition,
                Picture.deleted.is_(False),
            )
        elif character_id_value is None or character_id_value == "":
            return None
        elif isinstance(character_id_value, int):
            query = (
                select(Picture.id)
                .join(Face, Face.picture_id == Picture.id)
                .where(
                    Face.character_id == character_id_value,
                    Picture.deleted.is_(False),
                )
            )
        else:
            return None

        if formats:
            query = query.where(Picture.format.in_(formats))
        return list(session.exec(query).all())

    if sort_mech and sort_mech.key == SortMechanism.Keys.CHARACTER_LIKENESS:
        if not reference_character_id:
            raise HTTPException(
                status_code=400,
                detail="reference_character_id is required for CHARACTER_LIKENESS sort",
            )
        candidate_ids = None
        if only_deleted:
            candidate_ids = server.vault.db.run_task(
                fetch_smart_score_candidate_ids,
                character_id,
                only_deleted,
                format,
            )
            if candidate_ids is not None and not candidate_ids:
                return []
        pics = find_pictures_by_character_likeness(
            server,
            character_id,
            reference_character_id,
            offset,
            limit,
            descending,
            candidate_ids=candidate_ids,
        )
        if pics:
            hidden_ids = _fetch_hidden_picture_ids(
                server,
                request,
                [pic.get("id") for pic in pics if pic.get("id") is not None],
            )
            if hidden_ids:
                pics = [
                    pic
                    for pic in pics
                    if pic.get("id") is None or pic.get("id") not in hidden_ids
                ]
        if return_ids_only:
            return [pic.get("id") for pic in pics if pic.get("id") is not None]
        return pics
    elif sort_mech and sort_mech.key == SortMechanism.Keys.SMART_SCORE:
        candidate_ids = server.vault.db.run_task(
            fetch_smart_score_candidate_ids,
            character_id,
            only_deleted,
            format,
        )
        if candidate_ids is not None and not candidate_ids:
            return []
        penalised_tags = get_smart_score_penalised_tags_from_request(server, request)
        pics = find_pictures_by_smart_score(
            server,
            format,
            offset,
            limit,
            descending,
            candidate_ids=candidate_ids,
            penalised_tags=penalised_tags,
            only_deleted=only_deleted,
        )
        if pics:
            hidden_ids = _fetch_hidden_picture_ids(
                server,
                request,
                [pic.get("id") for pic in pics if pic.get("id") is not None],
            )
            if hidden_ids:
                pics = [
                    pic
                    for pic in pics
                    if pic.get("id") is None or pic.get("id") not in hidden_ids
                ]
        if return_ids_only:
            return [pic.get("id") for pic in pics if pic.get("id") is not None]
        return pics
    elif character_id == "UNASSIGNED":
        pics = server.vault.db.run_task(
            Picture.find_unassigned,
            sort_mech=sort_mech,
            offset=offset,
            limit=limit,
            format=format,
            metadata_fields=metadata_fields,
        )
    elif only_deleted:
        pics = server.vault.db.run_task(
            Picture.find,
            sort_mech=sort_mech,
            offset=offset,
            limit=limit,
            select_fields=metadata_fields,
            format=format,
            only_deleted=True,
            include_unimported=True,
            **query_params,
        )
    else:
        if character_id is not None and character_id != "":

            def get_picture_ids_for_character(session, character_id):
                faces = session.exec(
                    select(Face).where(Face.character_id == character_id)
                ).all()
                return list({face.picture_id for face in faces})

            picture_ids = server.vault.db.run_task(
                get_picture_ids_for_character, character_id
            )
            if not picture_ids:
                return []
            query_params["id"] = picture_ids

        pics = server.vault.db.run_task(
            Picture.find,
            sort_mech=sort_mech,
            offset=offset,
            limit=limit,
            select_fields=metadata_fields,
            format=format,
            include_unimported=True,
            **query_params,
        )
    if pics:
        hidden_ids = _fetch_hidden_picture_ids(
            server,
            request,
            [pic.id for pic in pics if getattr(pic, "id", None) is not None],
        )
        if hidden_ids:
            pics = [pic for pic in pics if pic.id not in hidden_ids]
    if return_ids_only:
        return [pic.id for pic in pics]
    return serialize_metadata(pics)


def create_router(server) -> APIRouter:
    router = APIRouter()
    thumbnail_generation_locks: dict[int, asyncio.Lock] = {}
    thumbnail_memory_cache: OrderedDict[int, bytes] = OrderedDict()
    thumbnail_memory_cache_max = 128

    def get_thumbnail_lock(picture_id: int) -> asyncio.Lock:
        lock = thumbnail_generation_locks.get(picture_id)
        if lock is None:
            lock = asyncio.Lock()
            thumbnail_generation_locks[picture_id] = lock
        return lock

    def get_cached_thumbnail_bytes(picture_id: int) -> bytes | None:
        data = thumbnail_memory_cache.pop(picture_id, None)
        if data is None:
            return None
        thumbnail_memory_cache[picture_id] = data
        return data

    def cache_thumbnail_bytes(picture_id: int, thumbnail_bytes: bytes) -> None:
        if not thumbnail_bytes:
            return
        if picture_id in thumbnail_memory_cache:
            thumbnail_memory_cache.pop(picture_id, None)
        thumbnail_memory_cache[picture_id] = thumbnail_bytes
        while len(thumbnail_memory_cache) > thumbnail_memory_cache_max:
            thumbnail_memory_cache.popitem(last=False)

    @router.get(
        "/sort_mechanisms",
        summary="List picture sort mechanisms",
        description="Returns all available sorting keys and direction semantics supported by picture listing and search endpoints.",
    )
    async def get_pictures_sort_mechanisms():
        """Return available sorting mechanisms for pictures."""
        result = SortMechanism.all()
        logger.debug("Returning sort mechanisms: {}".format(result))
        return result

    @router.get(
        "/pictures/plugins",
        summary="List image plugins",
        description="Lists available image plugins and their parameter schemas.",
    )
    async def list_picture_plugins():
        manager = get_image_plugin_manager()
        manager.reload()
        return {
            "plugins": manager.list_plugins(),
            "plugin_errors": manager.list_errors(),
            "plugin_dirs": {
                "built_in": manager.built_in_dir,
                "user": manager.user_dir,
            },
        }

    @router.post(
        "/pictures/plugins/{name}",
        summary="Run image plugin",
        description="Runs a named image plugin on selected pictures and imports outputs into stacks.",
    )
    async def run_picture_plugin(name: str, payload: dict = Body(...)):
        manager = get_image_plugin_manager()
        manager.reload()
        plugin = manager.get_plugin(name)
        if plugin is None:
            raise HTTPException(status_code=404, detail="Plugin not found")

        raw_picture_ids = payload.get("picture_ids")
        if not isinstance(raw_picture_ids, list) or not raw_picture_ids:
            raise HTTPException(
                status_code=400, detail="picture_ids must be a non-empty list"
            )

        try:
            picture_ids = [
                int(pic_id) for pic_id in raw_picture_ids if pic_id is not None
            ]
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400, detail="picture_ids must contain integers"
            )

        if not picture_ids:
            raise HTTPException(
                status_code=400, detail="picture_ids must contain integers"
            )

        parameters = payload.get("parameters") or {}
        if not isinstance(parameters, dict):
            raise HTTPException(status_code=400, detail="parameters must be an object")

        plugin_run_id = str(uuid.uuid4())

        def emit_plugin_progress(progress_payload: dict):
            if not isinstance(progress_payload, dict):
                return
            server.vault.notify(
                EventType.PLUGIN_PROGRESS,
                {
                    "run_id": plugin_run_id,
                    "plugin": str(progress_payload.get("plugin") or name),
                    "status": "running",
                    **progress_payload,
                },
            )

        def emit_plugin_error(error_payload: dict):
            if not isinstance(error_payload, dict):
                return
            server.vault.notify(
                EventType.PLUGIN_PROGRESS,
                {
                    "run_id": plugin_run_id,
                    "plugin": str(error_payload.get("plugin") or name),
                    "status": "error",
                    "message": str(error_payload.get("message") or "Plugin error"),
                    "error": error_payload,
                },
            )

        server.vault.notify(
            EventType.PLUGIN_PROGRESS,
            {
                "run_id": plugin_run_id,
                "plugin": name,
                "status": "started",
                "current": 0,
                "total": len(picture_ids),
                "progress": 0.0,
                "message": f"Starting plugin: {name}",
            },
        )

        try:
            result = await asyncio.to_thread(
                apply_plugin_to_pictures,
                server,
                plugin,
                picture_ids,
                parameters,
                progress_reporter=emit_plugin_progress,
                error_reporter=emit_plugin_error,
            )
        except ValueError as exc:
            server.vault.notify(
                EventType.PLUGIN_PROGRESS,
                {
                    "run_id": plugin_run_id,
                    "plugin": name,
                    "status": "failed",
                    "message": str(exc),
                },
            )
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            logger.warning("Plugin run failed for '%s': %s", name, exc)
            server.vault.notify(
                EventType.PLUGIN_PROGRESS,
                {
                    "run_id": plugin_run_id,
                    "plugin": name,
                    "status": "failed",
                    "message": str(exc),
                },
            )
            raise HTTPException(status_code=500, detail=f"Plugin failed: {exc}")

        server.vault.notify(
            EventType.PLUGIN_PROGRESS,
            {
                "run_id": plugin_run_id,
                "plugin": name,
                "status": "completed",
                "current": len(picture_ids),
                "total": len(picture_ids),
                "progress": 100.0,
                "message": f"Completed plugin: {name}",
            },
        )

        created_ids = result.get("created_picture_ids") or []
        output_ids = result.get("output_picture_ids") or []
        if created_ids:
            server.vault.notify(EventType.PICTURE_IMPORTED, created_ids)
        if output_ids:
            server.vault.notify(EventType.CHANGED_PICTURES, output_ids)

        return {
            "status": "success",
            **result,
        }

    @router.get(
        "/pictures/stacks",
        summary="List computed picture stack groups",
        description="Builds stack-like groups from likeness edges using filtering options such as character, set, format, and threshold.",
    )
    async def get_picture_stacks(
        request: Request,
        threshold: float = 0.0,
        min_group_size: int = 2,
        set_id: int = Query(None),
        character_id: str = Query(None),
        format: list[str] = Query(None),
    ):
        candidate_ids = None
        only_deleted = character_id == "SCRAPHEAP"

        if set_id is not None:

            def fetch_set_ids(session, set_id):
                members = session.exec(
                    select(PictureSetMember.picture_id)
                    .join(Picture, Picture.id == PictureSetMember.picture_id)
                    .where(
                        PictureSetMember.set_id == set_id,
                        Picture.deleted.is_(False),
                    )
                ).all()
                normalized = []
                for row in members:
                    if isinstance(row, (list, tuple)):
                        if row:
                            normalized.append(row[0])
                        continue
                    normalized.append(row)
                return normalized

            candidate_ids = set(
                server.vault.db.run_immediate_read_task(fetch_set_ids, set_id)
            )
        elif character_id is not None:
            if character_id == "UNASSIGNED":

                def fetch_unassigned_ids(session):
                    query = select(Picture.id)
                    unassigned_condition = ~exists(
                        select(Face.id).where(
                            Face.picture_id == Picture.id,
                            Face.character_id.is_not(None),
                        )
                    )
                    not_in_set_condition = ~exists(
                        select(PictureSetMember.picture_id).where(
                            PictureSetMember.picture_id == Picture.id
                        )
                    )
                    query = query.where(
                        unassigned_condition,
                        not_in_set_condition,
                        Picture.deleted.is_(False),
                    )
                    return list(session.exec(query).all())

                candidate_ids = set(
                    server.vault.db.run_immediate_read_task(fetch_unassigned_ids)
                )
            elif character_id == "ALL" or character_id == "":
                candidate_ids = None
            elif character_id == "SCRAPHEAP":

                def fetch_deleted_ids(session):
                    rows = session.exec(
                        select(Picture.id).where(
                            Picture.deleted.is_(True),
                        )
                    ).all()
                    return list(rows)

                candidate_ids = set(
                    server.vault.db.run_immediate_read_task(fetch_deleted_ids)
                )
            elif character_id.isdigit():

                def fetch_character_ids(session, character_id):
                    faces = session.exec(
                        select(Face).where(Face.character_id == character_id)
                    ).all()
                    picture_ids = {face.picture_id for face in faces}
                    if not picture_ids:
                        return []
                    rows = session.exec(
                        select(Picture.id).where(
                            Picture.id.in_(picture_ids),
                            Picture.deleted.is_(False),
                        )
                    ).all()
                    return list(rows)

                candidate_ids = set(
                    server.vault.db.run_immediate_read_task(
                        fetch_character_ids, int(character_id)
                    )
                )

        if format:

            def fetch_format_ids(session, format, deleted_only: bool):
                query = select(Picture.id).where(
                    Picture.format.in_(format),
                )
                if deleted_only:
                    query = query.where(Picture.deleted.is_(True))
                else:
                    query = query.where(Picture.deleted.is_(False))
                rows = session.exec(query).all()
                return list(rows)

            format_ids = set(
                server.vault.db.run_immediate_read_task(
                    fetch_format_ids, format, only_deleted
                )
            )
            candidate_ids = (
                format_ids if candidate_ids is None else candidate_ids & format_ids
            )

        if candidate_ids is None:
            if only_deleted:

                def fetch_deleted_ids(session):
                    rows = session.exec(
                        select(Picture.id).where(
                            Picture.deleted.is_(True),
                        )
                    ).all()
                    return list(rows)

                candidate_ids = set(
                    server.vault.db.run_immediate_read_task(fetch_deleted_ids)
                )
            else:

                def fetch_active_ids(session):
                    rows = session.exec(
                        select(Picture.id).where(
                            Picture.deleted.is_(False),
                        )
                    ).all()
                    return list(rows)

                candidate_ids = set(
                    server.vault.db.run_immediate_read_task(fetch_active_ids)
                )

        def fetch_likeness(session):
            rows = session.exec(
                select(PictureLikeness).where(PictureLikeness.likeness >= threshold)
            ).all()
            logger.debug(
                "Fetched %d picture likeness rows above threshold=%s",
                len(rows),
                threshold,
            )
            return rows

        rows = server.vault.db.run_immediate_read_task(fetch_likeness)

        neighbors = defaultdict(set)
        for row in rows:
            if candidate_ids is not None:
                if (
                    row.picture_id_a not in candidate_ids
                    or row.picture_id_b not in candidate_ids
                ):
                    continue
            neighbors[row.picture_id_a].add(row.picture_id_b)
            neighbors[row.picture_id_b].add(row.picture_id_a)

        visited = set()
        groups = []
        for node in neighbors:
            if node in visited:
                continue
            stack = set()
            queue = deque([node])
            while queue:
                n = queue.popleft()
                if n in visited:
                    continue
                visited.add(n)
                stack.add(n)
                for nbr in neighbors[n]:
                    if nbr not in visited:
                        queue.append(nbr)
            if len(stack) >= min_group_size:
                groups.append(list(stack))

        groups = sorted(groups, key=min)
        stack_index_map = {}
        ordered_ids = []
        assigned_ids = set()

        if groups:

            def fetch_stack_map(session, ids, deleted_only: bool):
                query = select(Picture.id, Picture.stack_id).where(
                    Picture.id.in_(ids),
                )
                if deleted_only:
                    query = query.where(Picture.deleted.is_(True))
                else:
                    query = query.where(Picture.deleted.is_(False))
                return list(session.exec(query).all())

            group_ids = [pic_id for group in groups for pic_id in group]
            stack_rows = server.vault.db.run_immediate_read_task(
                fetch_stack_map,
                group_ids,
                only_deleted,
            )
            stack_map = {row[0]: row[1] for row in stack_rows}
            stack_ids = {stack_id for stack_id in stack_map.values() if stack_id}

            def fetch_stack_members(session, stack_ids, deleted_only: bool):
                if not stack_ids:
                    return []
                query = select(Picture.id, Picture.stack_id).where(
                    Picture.stack_id.in_(stack_ids),
                )
                if deleted_only:
                    query = query.where(Picture.deleted.is_(True))
                else:
                    query = query.where(Picture.deleted.is_(False))
                return list(session.exec(query).all())

            stack_member_rows = server.vault.db.run_immediate_read_task(
                fetch_stack_members,
                list(stack_ids),
                only_deleted,
            )
            stack_members_map = {}
            for pic_id, stack_id in stack_member_rows:
                if not stack_id:
                    continue
                stack_members_map.setdefault(stack_id, set()).add(pic_id)

            group_index = 0
            for group in groups:
                has_unstacked = any(stack_map.get(pic_id) is None for pic_id in group)
                if not has_unstacked:
                    continue
                expanded = set(group)
                for pic_id in group:
                    stack_id = stack_map.get(pic_id)
                    if stack_id:
                        expanded.update(stack_members_map.get(stack_id, set()))
                stack_ids_in_group = {
                    stack_map.get(pic_id)
                    for pic_id in expanded
                    if stack_map.get(pic_id)
                }
                if len(stack_ids_in_group) == 1:
                    stack_id = next(iter(stack_ids_in_group))
                    stack_members = stack_members_map.get(stack_id, set())
                    if expanded.issubset(stack_members):
                        continue
                next_ids = [
                    pic_id for pic_id in sorted(expanded) if pic_id not in assigned_ids
                ]
                if not next_ids:
                    continue
                for pic_id in next_ids:
                    stack_index_map[pic_id] = group_index
                    ordered_ids.append(pic_id)
                    assigned_ids.add(pic_id)
                group_index += 1

        if not ordered_ids:
            return []

        hidden_ids = _fetch_hidden_picture_ids(server, request, ordered_ids)
        if hidden_ids:
            ordered_ids = [pid for pid in ordered_ids if pid not in hidden_ids]
            if not ordered_ids:
                return []

        def fetch_pictures(session, ids, deleted_only: bool):
            return Picture.find(
                session,
                id=ids,
                select_fields=Picture.metadata_fields(),
                only_deleted=deleted_only,
            )

        ordered_pics = server.vault.db.run_immediate_read_task(
            fetch_pictures, ordered_ids, only_deleted
        )
        pics_by_id = {pic.id: pic for pic in ordered_pics}
        ordered_pics = [pics_by_id.get(pid) for pid in ordered_ids]
        ordered_pics = [pic for pic in ordered_pics if pic is not None]

        smart_score_by_id = {}
        if ordered_pics:
            try:
                penalised_tags = get_smart_score_penalised_tags_from_request(
                    server, request
                )
                good_anchors, bad_anchors, candidates = fetch_smart_score_data(
                    server,
                    None,
                    candidate_ids=ordered_ids,
                    penalised_tags=penalised_tags,
                )
                if candidates:
                    good_list, bad_list, cand_list, cand_ids = (
                        prepare_smart_score_inputs(
                            good_anchors, bad_anchors, candidates
                        )
                    )
                    if cand_list:
                        scores = SmartScoreUtils.calculate_smart_score_batch_numpy(
                            cand_list, good_list, bad_list
                        )
                        smart_score_by_id = {
                            int(pid): float(score)
                            for pid, score in zip(cand_ids, scores)
                            if score is not None
                        }
            except Exception as exc:
                logger.warning(
                    "[stacks] Failed to compute smart scores: %s",
                    exc,
                )

        stacks_by_index = defaultdict(list)
        for pic in ordered_pics:
            pic_dict = safe_model_dict(pic)
            pic_dict["stack_index"] = stack_index_map.get(pic.id)
            if pic.id in smart_score_by_id:
                pic_dict["smartScore"] = smart_score_by_id[pic.id]
            stacks_by_index[pic_dict["stack_index"]].append(pic_dict)

        response = []
        for stack_idx in sorted(stacks_by_index.keys()):
            stack_items = stacks_by_index[stack_idx]
            stack_items.sort(
                key=lambda item: (
                    -(item.get("score") or 0),
                    -(item.get("smartScore") or 0),
                    -(
                        item.get("created_at").timestamp()
                        if isinstance(item.get("created_at"), datetime)
                        else 0.0
                    ),
                    int(item.get("id") or 0),
                )
            )
            response.extend(stack_items)

        return response

    @router.get(
        "/pictures/thumbnails/{id}.webp",
        summary="Get picture thumbnail image",
        description="Returns a WebP thumbnail for a picture id, generating and caching it on demand when needed.",
    )
    async def get_thumbnail(id: int):
        started_at = datetime.now()

        def fetch_picture(session: Session, picture_id: int):
            pics = Picture.find(
                session,
                id=picture_id,
                select_fields=[
                    "id",
                    "file_path",
                ],
                include_deleted=True,
                include_unimported=True,
            )
            return pics[0] if pics else None

        pic = server.vault.db.run_immediate_read_task(fetch_picture, id)
        if not pic or not getattr(pic, "file_path", None):
            raise HTTPException(status_code=404, detail="Picture not found")

        thumb_path = ImageUtils.get_thumbnail_path(
            server.vault.image_root, pic.file_path
        )
        if thumb_path and os.path.exists(thumb_path):
            elapsed_ms = (datetime.now() - started_at).total_seconds() * 1000.0
            logger.debug(
                "Thumbnail GET cache-hit: id=%s path=%s elapsed_ms=%.1f",
                id,
                thumb_path,
                elapsed_ms,
            )
            return FileResponse(thumb_path, media_type="image/webp")

        cached_bytes = get_cached_thumbnail_bytes(id)
        if cached_bytes:
            elapsed_ms = (datetime.now() - started_at).total_seconds() * 1000.0
            logger.debug(
                "Thumbnail GET memory-hit: id=%s elapsed_ms=%.1f",
                id,
                elapsed_ms,
            )
            return Response(content=cached_bytes, media_type="image/webp")

        lock = get_thumbnail_lock(id)
        async with lock:
            if thumb_path and os.path.exists(thumb_path):
                elapsed_ms = (datetime.now() - started_at).total_seconds() * 1000.0
                logger.debug(
                    "Thumbnail GET cache-hit-after-wait: id=%s path=%s elapsed_ms=%.1f",
                    id,
                    thumb_path,
                    elapsed_ms,
                )
                return FileResponse(thumb_path, media_type="image/webp")

            cached_bytes = get_cached_thumbnail_bytes(id)
            if cached_bytes:
                elapsed_ms = (datetime.now() - started_at).total_seconds() * 1000.0
                logger.debug(
                    "Thumbnail GET memory-hit-after-wait: id=%s elapsed_ms=%.1f",
                    id,
                    elapsed_ms,
                )
                return Response(content=cached_bytes, media_type="image/webp")

            def build_thumbnail_blocking() -> tuple[
                str, str | None, bytes | None, str | None
            ]:
                resolved = ImageUtils.resolve_picture_path(
                    server.vault.image_root, pic.file_path
                )
                if not resolved or not os.path.exists(resolved):
                    return "missing-source", resolved, None, None

                img = ImageUtils.load_image_or_video(resolved)
                if img is None:
                    return "load-failed", resolved, None, None

                if not isinstance(img, Image.Image):
                    img = Image.fromarray(img)

                thumbnail_bytes = ImageUtils.generate_thumbnail_bytes(img)
                if not thumbnail_bytes:
                    return "encode-failed", resolved, None, None

                saved_thumb_path = ImageUtils.write_thumbnail_bytes(
                    server.vault.image_root, pic.file_path, thumbnail_bytes
                )
                if saved_thumb_path and os.path.exists(saved_thumb_path):
                    return "saved", resolved, None, saved_thumb_path

                return "memory-only", resolved, thumbnail_bytes, None

            (
                status,
                resolved_path,
                thumbnail_bytes,
                saved_thumb,
            ) = await asyncio.to_thread(build_thumbnail_blocking)

            if status == "saved" and saved_thumb:
                elapsed_ms = (datetime.now() - started_at).total_seconds() * 1000.0
                logger.debug(
                    "Thumbnail GET generated: id=%s source=%s elapsed_ms=%.1f",
                    id,
                    resolved_path,
                    elapsed_ms,
                )
                return FileResponse(saved_thumb, media_type="image/webp")

            if status == "memory-only" and thumbnail_bytes:
                cache_thumbnail_bytes(id, thumbnail_bytes)
                elapsed_ms = (datetime.now() - started_at).total_seconds() * 1000.0
                logger.warning(
                    "Thumbnail GET generated-memory-only: id=%s source=%s elapsed_ms=%.1f",
                    id,
                    resolved_path,
                    elapsed_ms,
                )
                return Response(content=thumbnail_bytes, media_type="image/webp")

            if status == "missing-source":
                logger.warning(
                    "Missing source file for on-demand thumbnail: %s",
                    resolved_path,
                )
            elif status == "load-failed":
                logger.warning(
                    "Failed to load image for on-demand thumbnail: %s",
                    resolved_path,
                )
            elif status == "encode-failed":
                logger.warning(
                    "Failed to encode on-demand thumbnail: %s",
                    resolved_path,
                )

        elapsed_ms = (datetime.now() - started_at).total_seconds() * 1000.0
        logger.warning(
            "Thumbnail GET failed: id=%s elapsed_ms=%.1f",
            id,
            elapsed_ms,
        )

        raise HTTPException(status_code=404, detail="Thumbnail not found")

    @router.post(
        "/pictures/thumbnails",
        summary="Get batch thumbnail metadata",
        description="Returns thumbnail URLs and mapped face/hand overlays for a list of picture ids, including penalised-tag hints.",
    )
    async def get_thumbnails(request: Request, payload: dict = Body(...)):
        ids = payload.get("ids", [])
        if not isinstance(ids, list):
            raise HTTPException(status_code=400, detail="'ids' must be a list")

        logger.debug(
            "Thumbnail batch request: client=%s count=%s ids_preview=%s",
            getattr(getattr(request, "client", None), "host", None),
            len(ids),
            ids[:8],
        )

        penalised_tags = get_smart_score_penalised_tags_from_request(server, request)
        penalised_tag_set = {
            str(tag).strip().lower() for tag in (penalised_tags or {}).keys() if tag
        }
        ids_int = []
        for raw_id in ids:
            try:
                ids_int.append(int(raw_id))
            except (TypeError, ValueError):
                continue

        penalised_tag_map = defaultdict(list)
        if ids_int and penalised_tag_set:

            def fetch_penalised_tags(session: Session):
                rows = session.exec(
                    select(Tag.picture_id, Tag.tag).where(
                        Tag.picture_id.in_(ids_int),
                        Tag.tag.is_not(None),
                        func.lower(Tag.tag).in_(penalised_tag_set),
                    )
                ).all()
                return rows

            rows = server.vault.db.run_task(
                fetch_penalised_tags, priority=DBPriority.IMMEDIATE
            )
            for pic_id, tag in rows or []:
                if tag:
                    penalised_tag_map[pic_id].append(tag)

        def map_bbox_to_thumbnail(bbox, picture):
            if not bbox or len(bbox) != 4:
                return bbox, False
            left = getattr(picture, "thumbnail_left", None)
            top = getattr(picture, "thumbnail_top", None)
            side = getattr(picture, "thumbnail_side", None)
            if left is None or top is None or side in (None, 0):
                return bbox, False
            try:
                scale = 256.0 / float(side)
                x1, y1, x2, y2 = bbox
                x1 = max(0.0, min(256.0, (x1 - left) * scale))
                y1 = max(0.0, min(256.0, (y1 - top) * scale))
                x2 = max(0.0, min(256.0, (x2 - left) * scale))
                y2 = max(0.0, min(256.0, (y2 - top) * scale))
                return (
                    [
                        int(round(x1)),
                        int(round(y1)),
                        int(round(x2)),
                        int(round(y2)),
                    ],
                    True,
                )
            except Exception:
                return bbox, False

        pics = server.vault.db.run_task(
            lambda session: Picture.find(
                session,
                id=ids,
                select_fields=[
                    "id",
                    "file_path",
                    "faces",
                    "thumbnail_left",
                    "thumbnail_top",
                    "thumbnail_side",
                ],
                include_deleted=True,
                include_unimported=True,
            ),
            priority=DBPriority.IMMEDIATE,
        )
        logger.debug(
            "Thumbnail batch resolved: requested=%s found=%s",
            len(ids),
            len(pics or []),
        )
        character_name_map = {}
        character_ids = set()
        for pic in pics:
            for face in getattr(pic, "faces", []):
                if getattr(face, "character_id", None) is not None:
                    character_ids.add(face.character_id)
        if character_ids:

            def fetch_character_names(session: Session):
                rows = session.exec(
                    select(Character.id, Character.name).where(
                        Character.id.in_(character_ids)
                    )
                ).all()
                return rows

            rows = server.vault.db.run_task(
                fetch_character_names, priority=DBPriority.IMMEDIATE
            )
            character_name_map = {char_id: name for char_id, name in rows or []}
        results = {}
        for pic in pics:
            try:
                face_entries = []
                mapped_any = False
                raw_face_bboxes = []
                for face in getattr(pic, "faces", []):
                    bbox = None
                    try:
                        bbox = face.bbox if hasattr(face, "bbox") else None
                        if bbox and isinstance(bbox, str):
                            bbox = ast.literal_eval(bbox)
                    except Exception:
                        bbox = None
                    if bbox and isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                        raw_face_bboxes.append(list(bbox))
                        face_entries.append(
                            {
                                "id": face.id,
                                "bbox": list(bbox),
                                "character_id": face.character_id,
                                "character_name": character_name_map.get(
                                    face.character_id
                                ),
                                "frame_index": getattr(face, "frame_index", None),
                            }
                        )
                face_data = []
                for entry in face_entries:
                    mapped_bbox, mapped = map_bbox_to_thumbnail(entry.get("bbox"), pic)
                    mapped_any = mapped_any or mapped
                    face_data.append({**entry, "bbox": mapped_bbox})

                thumbnail_url = f"/pictures/thumbnails/{pic.id}.webp"
                results[pic.id] = {
                    "thumbnail": thumbnail_url,
                    "faces": face_data,
                    "thumbnail_width": 256 if mapped_any else None,
                    "thumbnail_height": 256 if mapped_any else None,
                    "penalised_tags": list(
                        dict.fromkeys(penalised_tag_map.get(pic.id, []))
                    ),
                }
            except Exception as exc:
                logger.error(
                    f"Picture not found or error for id={pic.id} (thumbnail request): {exc}"
                )
                results[pic.id] = {
                    "thumbnail": None,
                    "faces": [],
                    "penalised_tags": [],
                }
        response = JSONResponse(results)
        origin = request.headers.get("origin")
        if origin and (
            origin in server.allow_origins
            or (
                server.allow_origin_regex
                and re.match(server.allow_origin_regex, origin)
            )
        ):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
        return response

    @router.get(
        "/pictures/export",
        summary="Start picture export job",
        description="Queues an asynchronous export task and returns a task id for polling status and downloading the generated archive.",
    )
    async def export_pictures_zip(
        request: Request,
        background_tasks: BackgroundTasks,
        query: str = Query(None),
        set_id: int = Query(None),
        threshold: float = Query(0.0),
        caption_mode: str = Query("description"),
        include_character_name: bool = Query(False),
        resolution: str = Query("original"),
        export_type: str = Query("full"),
    ):
        task_id = str(uuid.uuid4())
        server.export_tasks[task_id] = {
            "status": "in_progress",
            "file_path": None,
            "total": 0,
            "processed": 0,
            "filename": None,
        }

        from pixlvault.utils.service.export_utils import (
            ExportUtils as PictureServiceUtils,
        )

        # Gather extra params for the export service
        background_data = {
            "query": query,
            "set_id": set_id,
            "threshold": threshold,
            "caption_mode": caption_mode,
            "include_character_name": include_character_name,
            "resolution": resolution,
            "export_type": export_type,
        }
        background_tasks.add_task(
            PictureServiceUtils.generate_zip,
            server,
            request,
            task_id,
            server.export_tasks,
            background_data,
        )
        return JSONResponse({"task_id": task_id})

    @router.get(
        "/pictures/export/status",
        summary="Get export job status",
        description="Returns current progress for an export task id, including completion state and download URL when ready.",
    )
    async def export_status(task_id: str):
        task = server.export_tasks.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        total = task.get("total") or 0
        processed = task.get("processed") or 0
        progress = (processed / total * 100.0) if total else 0.0

        if task["status"] == "completed":
            return {
                "status": "completed",
                "download_url": f"/pictures/export/download/{task_id}",
                "total": total,
                "processed": processed,
                "progress": progress,
            }

        return {
            "status": task["status"],
            "total": total,
            "processed": processed,
            "progress": progress,
        }

    @router.get(
        "/pictures/export/download/{task_id}",
        summary="Download completed export",
        description="Downloads the generated export file for a completed task id.",
    )
    async def download_export(task_id: str):
        task = server.export_tasks.get(task_id)
        if not task or task["status"] != "completed":
            raise HTTPException(status_code=404, detail="File not ready")

        filename = task.get("filename") or os.path.basename(task["file_path"])
        return FileResponse(task["file_path"], filename=filename)

    @router.get(
        "/pictures/search",
        summary="Search pictures by text",
        description="Performs semantic text search across pictures with optional sort, filtering, and candidate scoping.",
    )
    async def search_pictures(
        request: Request,
        query: str,
        offset: int = Query(0),
        limit: int = Query(sys.maxsize),
        threshold: float = Query(0.5),
    ):
        query_params = {}
        format = None
        character_id = None
        set_id = None
        sort = None
        descending = True
        if request.query_params:
            query_params = dict(request.query_params)
            query = query_params.pop("query", query)
            offset = int(query_params.pop("offset", offset))
            limit = int(query_params.pop("limit", limit))
            character_id = query_params.pop("character_id", None)
            set_id = query_params.pop("set_id", None)
            sort = query_params.pop("sort", None)
            desc_val = query_params.pop("descending", descending)
            descending = (
                desc_val.lower() == "true"
                if isinstance(desc_val, str)
                else bool(desc_val)
            )
            format = request.query_params.getlist("format")
        if not query:
            raise HTTPException(
                status_code=400, detail="Query parameter is required for search"
            )

        only_deleted = character_id == "SCRAPHEAP"
        candidate_ids = None
        sort_mech = None

        if sort:
            try:
                sort_mech = SortMechanism.from_string(sort, descending=descending)
            except ValueError as ve:
                logger.error("Invalid sort mechanism for search: %s", ve)
                raise HTTPException(status_code=400, detail=str(ve))

        if set_id is not None:
            try:
                set_id = int(set_id)
            except (TypeError, ValueError):
                set_id = None

        if set_id is not None:

            def fetch_set_ids(session, set_id_value):
                members = session.exec(
                    select(PictureSetMember.picture_id)
                    .join(Picture, Picture.id == PictureSetMember.picture_id)
                    .where(
                        PictureSetMember.set_id == set_id_value,
                        Picture.deleted.is_(False),
                    )
                ).all()
                return [row for row in members]

            candidate_ids = set(
                server.vault.db.run_immediate_read_task(fetch_set_ids, set_id)
            )
        elif character_id is not None:
            if character_id == "UNASSIGNED":

                def fetch_unassigned_ids(session):
                    unassigned_condition = ~exists(
                        select(Face.id).where(
                            Face.picture_id == Picture.id,
                            Face.character_id.is_not(None),
                        )
                    )
                    not_in_set_condition = ~exists(
                        select(PictureSetMember.picture_id).where(
                            PictureSetMember.picture_id == Picture.id
                        )
                    )
                    query_stmt = select(Picture.id).where(
                        unassigned_condition,
                        not_in_set_condition,
                        Picture.deleted.is_(False),
                    )
                    return list(session.exec(query_stmt).all())

                candidate_ids = set(
                    server.vault.db.run_immediate_read_task(fetch_unassigned_ids)
                )
            elif character_id in ("ALL", ""):
                candidate_ids = None
            elif character_id == "SCRAPHEAP":
                candidate_ids = None
            elif str(character_id).isdigit():

                def fetch_character_ids(session, character_id_value):
                    faces = session.exec(
                        select(Face).where(Face.character_id == character_id_value)
                    ).all()
                    picture_ids = {face.picture_id for face in faces}
                    if not picture_ids:
                        return []
                    rows = session.exec(
                        select(Picture.id).where(
                            Picture.id.in_(picture_ids),
                            Picture.deleted.is_(False),
                        )
                    ).all()
                    return list(rows)

                candidate_ids = set(
                    server.vault.db.run_immediate_read_task(
                        fetch_character_ids, int(character_id)
                    )
                )

        if candidate_ids is not None and not candidate_ids:
            return []

        def find_by_text(session, query, offset, limit):
            words = re.findall(r"\b\w+\b", query.lower())
            semantic_offset = 0 if sort_mech else offset
            semantic_limit = sys.maxsize if sort_mech else limit
            candidate_size = len(candidate_ids) if candidate_ids else None

            def log_semantic_results(label: str, rows):
                if rows is None:
                    return
                if not rows:
                    logger.debug(
                        "Semantic search %s: no results (query=%r, words=%s, threshold=%s, format=%s, only_deleted=%s, candidate_ids=%s)",
                        label,
                        query,
                        words,
                        threshold,
                        format,
                        only_deleted,
                        candidate_size,
                    )
                    return
                preview = [
                    {
                        "id": getattr(pic, "id", None),
                        "score": round(float(score), 4),
                    }
                    for pic, score in rows[:10]
                ]
                logger.debug(
                    "Semantic search %s: results=%d (query=%r, words=%s, threshold=%s, format=%s, only_deleted=%s, candidate_ids=%s) top=%s",
                    label,
                    len(rows),
                    query,
                    words,
                    threshold,
                    format,
                    only_deleted,
                    candidate_size,
                    preview,
                )

            results = Picture.semantic_search(
                session,
                query,
                words,
                text_to_embedding=server.vault.generate_text_embedding,
                clip_text_to_embedding=server.vault.generate_clip_text_embedding,
                offset=semantic_offset,
                limit=semantic_limit,
                threshold=threshold,
                format=format,
                select_fields=Picture.metadata_fields(),
                only_deleted=only_deleted,
                candidate_ids=list(candidate_ids) if candidate_ids else None,
            )

            log_semantic_results("base", results)

            if not sort_mech:
                return results

            if not results:
                return []

            score_map = {
                pic.id: score
                for pic, score in results
                if pic is not None and getattr(pic, "id", None) is not None
            }
            if not score_map:
                return []

            sorted_pics = Picture.find(
                session,
                sort_mech=sort_mech,
                offset=offset,
                limit=limit,
                select_fields=Picture.metadata_fields(),
                format=format,
                only_deleted=only_deleted,
                id=list(score_map.keys()),
            )
            sorted_results = [(pic, score_map.get(pic.id, 0.0)) for pic in sorted_pics]
            log_semantic_results(f"sorted_{sort_mech.key.name}", sorted_results)
            return sorted_results

        results = server.vault.db.run_task(find_by_text, query, offset, limit)
        if results:
            hidden_ids = _fetch_hidden_picture_ids(
                server,
                request,
                [
                    getattr(pic, "id", None)
                    for pic, _score in results
                    if pic is not None and getattr(pic, "id", None) is not None
                ],
            )
            if hidden_ids:
                results = [
                    result
                    for result in results
                    if result[0] is not None
                    and getattr(result[0], "id", None) not in hidden_ids
                ]
        return [Picture.serialize_with_likeness(r) for r in results]

    @router.post(
        "/pictures/import",
        summary="Import media files",
        description="Starts an asynchronous import of uploaded image/video files (or zip contents) and returns a task id.",
    )
    async def import_pictures(
        background_tasks: BackgroundTasks,
        file: list[UploadFile] = File(None),
    ):
        _MAX_UPLOAD_BYTES = 5 * 1024**3  # 5 GB per uploaded file / zip
        _MAX_ZIP_ENTRIES = 5_000  # max files inside a zip
        _MAX_ZIP_DECOMPRESSED_BYTES = 10 * 1024**3  # 10 GB total decompressed
        if not server.vault.is_worker_running(TaskType.FACE_EXTRACTION):
            raise HTTPException(
                status_code=400,
                detail="Face worker is not running. Start it before import.",
            )

        dest_folder = server.vault.image_root
        logger.debug("Importing pictures to folder: " + str(dest_folder))
        os.makedirs(dest_folder, exist_ok=True)
        uploaded_files = []
        allowed_exts = {
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".gif",
            ".bmp",
            ".tiff",
            ".tif",
            ".mp4",
            ".webm",
            ".mov",
            ".avi",
            ".mkv",
        }
        if file is not None:
            for upload in file:
                if not upload.filename:
                    continue
                contents = await upload.read()
                if not contents:
                    continue
                if len(contents) > _MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Uploaded file '{upload.filename}' exceeds the 2 GB limit.",
                    )
                ext = os.path.splitext(upload.filename)[1].lower()
                if ext == ".zip":
                    try:
                        with zipfile.ZipFile(BytesIO(contents)) as zip_file:
                            entries = [i for i in zip_file.infolist() if not i.is_dir()]
                            if len(entries) > _MAX_ZIP_ENTRIES:
                                raise HTTPException(
                                    status_code=413,
                                    detail=f"Zip '{upload.filename}' contains too many files (max {_MAX_ZIP_ENTRIES:,}).",
                                )
                            total_decompressed = sum(i.file_size for i in entries)
                            if total_decompressed > _MAX_ZIP_DECOMPRESSED_BYTES:
                                raise HTTPException(
                                    status_code=413,
                                    detail=f"Zip '{upload.filename}' decompressed size exceeds the 10 GB limit.",
                                )
                            added = 0
                            for info in entries:
                                inner_ext = os.path.splitext(info.filename)[1].lower()
                                if inner_ext not in allowed_exts:
                                    continue
                                with zip_file.open(info) as handle:
                                    data = handle.read()
                                if not data:
                                    continue
                                uploaded_files.append((data, inner_ext))
                                added += 1
                            if added == 0:
                                logger.warning(
                                    "No valid media files found in zip: %s",
                                    upload.filename,
                                )
                    except zipfile.BadZipFile as exc:
                        logger.error("Invalid zip file: %s", upload.filename)
                        raise HTTPException(
                            status_code=400,
                            detail="Invalid zip file",
                        ) from exc
                else:
                    if ext not in allowed_exts:
                        logger.error("Invalid file extension: %s", ext)
                        raise HTTPException(
                            status_code=400, detail="Invalid file extension"
                        )
                    uploaded_files.append((contents, ext))
        else:
            logger.error("No files provided for import")
            raise HTTPException(status_code=400, detail="No image provided")

        if not uploaded_files:
            logger.error("No valid media files found for import")
            raise HTTPException(
                status_code=400,
                detail="No valid media files found for import",
            )

        total_import_bytes = sum(len(data) for data, _ in uploaded_files)
        free_bytes = shutil.disk_usage(dest_folder).free
        required_bytes = int(total_import_bytes * 1.1)  # 10% headroom
        if required_bytes > free_bytes:
            free_gb = free_bytes / 1024**3
            needed_gb = required_bytes / 1024**3
            raise HTTPException(
                status_code=507,
                detail=(
                    f"Not enough disk space. "
                    f"Import needs {needed_gb:.2f} GB (including 10% headroom) "
                    f"but only {free_gb:.2f} GB is available."
                ),
            )

        task_id = str(uuid.uuid4())
        server.import_tasks[task_id] = {
            "status": "in_progress",
            "total": len(uploaded_files),
            "processed": 0,
            "results": None,
            "error": None,
        }

        def run_import_task(server):
            try:
                shas, existing_map, new_pictures = _create_picture_imports(
                    server, uploaded_files, dest_folder
                )

                logger.debug(
                    f"Importing {len(new_pictures)} new pictures out of {len(uploaded_files)} uploaded."
                )

                if new_pictures:

                    def import_task(session):
                        session.add_all(new_pictures)
                        session.commit()
                        for pic in new_pictures:
                            session.refresh(pic)
                        return new_pictures

                    new_pictures = server.vault.db.run_task(import_task)
                    logger.debug(
                        f"Queuing likeness calculation for {len(new_pictures)} new pictures."
                    )
                else:
                    logger.warning("No new pictures to import; all are duplicates.")
                    new_pictures = []

                results = []
                duplicate_count = 0
                index = 0
                for _, sha in zip(uploaded_files, shas):
                    if sha in existing_map:
                        pic = existing_map[sha]
                        results.append(
                            {
                                "status": "duplicate",
                                "picture_id": pic.id,
                                "file": pic.file_path,
                            }
                        )
                        duplicate_count += 1
                    else:
                        pic = new_pictures[index]
                        results.append(
                            {
                                "status": "success",
                                "picture_id": pic.id,
                                "file": pic.file_path,
                            }
                        )
                        index += 1

                if duplicate_count:
                    logger.warning(
                        "Import completed with %d duplicate(s) out of %d file(s).",
                        duplicate_count,
                        len(uploaded_files),
                    )
                server.import_tasks[task_id]["results"] = results
                server.import_tasks[task_id]["processed"] = len(uploaded_files)
                if new_pictures:
                    server.import_tasks[task_id]["status"] = "processing_faces"
                    face_futures = [
                        server.vault.get_worker_future(
                            TaskType.FACE_EXTRACTION, Picture, pic.id, "faces"
                        )
                        for pic in new_pictures
                    ]
                    server.vault.notify(EventType.CHANGED_PICTURES)
                    face_timeout_s = 120
                    for pic, future in zip(new_pictures, face_futures):
                        try:
                            future.result(timeout=face_timeout_s)
                        except Exception as exc:
                            raise RuntimeError(
                                f"Face extraction timed out for picture id={pic.id}"
                            ) from exc
                    new_ids = [pic.id for pic in new_pictures if pic.id is not None]

                    def mark_imported(session, ids: list[int]):
                        if not ids:
                            return []
                        now = datetime.utcnow()
                        pics = session.exec(
                            select(Picture).where(Picture.id.in_(ids))
                        ).all()
                        updated = []
                        for pic in pics:
                            if pic.imported_at is None:
                                pic.imported_at = now
                                session.add(pic)
                                updated.append(pic.id)
                        session.commit()
                        return updated

                    imported_ids = server.vault.db.run_task(mark_imported, new_ids)
                    if imported_ids:
                        server.vault.notify(
                            EventType.PICTURE_IMPORTED,
                            imported_ids,
                        )
                    server.import_tasks[task_id]["status"] = "completed"
                else:
                    server.import_tasks[task_id]["status"] = "completed"
                    server.vault.notify(EventType.CHANGED_PICTURES)
            except Exception as exc:
                server.import_tasks[task_id]["status"] = "failed"
                server.import_tasks[task_id]["error"] = str(exc)
                logger.error(f"Import task {task_id} failed: {exc}")

        background_tasks.add_task(run_import_task, server)
        return {"task_id": task_id}

    @router.get(
        "/pictures/import/status",
        summary="Get import job status",
        description="Returns progress and result information for a previously started import task.",
    )
    async def import_status(task_id: str):
        task = server.import_tasks.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        total = task.get("total") or 0
        processed = task.get("processed") or 0
        progress = (processed / total * 100.0) if total else 0.0

        payload = {
            "status": task["status"],
            "total": total,
            "processed": processed,
            "progress": progress,
        }
        if task["status"] == "completed":
            payload["results"] = task.get("results") or []
        if task["status"] == "failed":
            payload["error"] = task.get("error")
        return payload

    @router.get(
        "/pictures/{id}.{ext}",
        summary="Get original picture file",
        description="Streams the original media file for a picture id when the requested extension matches the stored format.",
    )
    async def get_picture(request: Request, id: str, ext: str):
        if not isinstance(id, str):
            logger.error(f"Invalid id type: {type(id)} value: {id}")
            raise HTTPException(status_code=400, detail="Invalid picture id type")

        if not ext or not isinstance(ext, str):
            logger.error(f"Invalid extension type: {type(ext)} value: {ext}")
            raise HTTPException(status_code=400, detail="Invalid picture extension")
        id = int(id)

        pics = server.vault.db.run_task(
            lambda session: Picture.find(session, id=id, include_deleted=True)
        )
        if not pics:
            logger.error(f"Picture not found for id={id}")
            raise HTTPException(status_code=404, detail="Picture not found")
        pic = pics[0]

        file_path = ImageUtils.resolve_picture_path(
            server.vault.image_root, pic.file_path
        )
        if not file_path or not os.path.isfile(file_path):
            logger.error(
                f"File path missing or does not exist for picture id={pic.id}, file_path={pic.file_path}"
            )
            raise HTTPException(
                status_code=404, detail=f"File not found for picture id={pic.id}"
            )
        if pic.format.lower() != ext.lower():
            logger.error(
                f"Requested extension '{ext}' does not match picture format '{pic.format}' for id={pic.id}"
            )
            raise HTTPException(
                status_code=400,
                detail="Requested extension does not match picture format",
            )

        media_type = MEDIA_TYPE_BY_FORMAT.get(pic.format.lower())
        response = FileResponse(file_path, media_type=media_type)
        try:
            stat = os.stat(file_path)
            etag = f'W/"{stat.st_size}-{int(stat.st_mtime)}"'
            response.headers["ETag"] = etag
            response.headers["Last-Modified"] = formatdate(stat.st_mtime, usegmt=True)
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        except OSError:
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        origin = request.headers.get("origin")
        if origin and (
            origin in server.allow_origins
            or (
                server.allow_origin_regex
                and re.match(server.allow_origin_regex, origin)
            )
        ):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
        return response

    @router.get(
        "/pictures/{id}/metadata",
        summary="Get picture metadata",
        description="Returns metadata, tags, and optional smart score for a single picture, including embedded file metadata when available.",
    )
    async def get_picture_metadata(
        request: Request,
        id: str,
        smart_score: bool = Query(False),
    ):
        metadata_fields = Picture.metadata_fields()
        pics = server.vault.db.run_task(
            Picture.find, id=id, select_fields=metadata_fields, include_deleted=True
        )
        if not pics:
            logger.error(f"Picture not found for id={id}")
            raise HTTPException(status_code=404, detail="Picture not found")
        pic = pics[0]

        def fetch_image_only_tags(session: Session, pic_id: int):
            return session.exec(select(Tag).where(Tag.picture_id == pic_id)).all()

        pic_tags = server.vault.db.run_task(fetch_image_only_tags, pic.id)
        pic_dict = safe_model_dict(pic)
        pic_dict["tags"] = serialize_tag_objects(pic_tags)

        if smart_score:
            try:
                penalised_tags = get_smart_score_penalised_tags_from_request(
                    server, request
                )
                (
                    good_anchors,
                    bad_anchors,
                    candidates,
                ) = fetch_smart_score_data(
                    server,
                    None,
                    candidate_ids=[pic.id],
                    penalised_tags=penalised_tags,
                )
                smart_score_value = None
                if candidates:
                    (
                        good_list,
                        bad_list,
                        cand_list,
                        cand_ids,
                    ) = prepare_smart_score_inputs(
                        good_anchors, bad_anchors, candidates
                    )
                    if cand_list:
                        scores = SmartScoreUtils.calculate_smart_score_batch_numpy(
                            cand_list, good_list, bad_list
                        )
                        if cand_ids:
                            smart_score_value = float(scores[0])
                pic_dict["smartScore"] = smart_score_value
            except Exception as exc:
                logger.warning(
                    "[metadata] Failed to compute smart score for id=%s: %s",
                    pic.id,
                    exc,
                )
                pic_dict["smartScore"] = None

        embedded_metadata = {}
        try:
            file_path = ImageUtils.resolve_picture_path(
                server.vault.image_root, pic.file_path
            )
            logger.debug(
                "[metadata] Extracting embedded metadata for id=%s path=%s",
                pic.id,
                file_path,
            )
            embedded_metadata = ImageUtils.extract_embedded_metadata(file_path)
        except Exception as exc:
            logger.warning(
                "Failed to read embedded metadata for picture id=%s: %s",
                pic.id,
                exc,
            )

        if embedded_metadata:
            pic_dict["metadata"] = embedded_metadata

        if embedded_metadata:
            logger.debug(
                "[metadata] id=%s embedded_top_keys=%s",
                pic.id,
                list(embedded_metadata.keys()),
            )

        logger.debug("Returning dict: " + str(pic_dict))
        return pic_dict

    @router.post(
        "/pictures/{id}/face",
        summary="Create manual face entry",
        description="Adds a face bounding box to a picture and frame index, updating sentinel/ordering behavior for manual annotations.",
    )
    async def create_picture_face(id: str, payload: dict = Body(...)):
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

        bbox = payload.get("bbox") if isinstance(payload, dict) else None
        frame_index = payload.get("frame_index", 0) if isinstance(payload, dict) else 0
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            raise HTTPException(status_code=400, detail="bbox must be [x1, y1, x2, y2]")
        try:
            bbox_vals = [int(round(float(v))) for v in bbox]
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="bbox values must be numbers")
        try:
            frame_index = int(frame_index)
        except (TypeError, ValueError):
            frame_index = 0

        def create_face(session: Session):
            pic = session.get(Picture, pic_id)
            if not pic:
                return None
            sentinel = session.exec(
                select(Face).where(
                    Face.picture_id == pic_id,
                    Face.frame_index == frame_index,
                    Face.face_index == -1,
                )
            ).first()
            if sentinel is not None:
                session.delete(sentinel)
            max_index = session.exec(
                select(func.max(Face.face_index)).where(
                    Face.picture_id == pic_id,
                    Face.frame_index == frame_index,
                )
            ).one()
            next_index = (max_index or 0) + 1 if max_index is not None else 0
            face = Face(
                picture_id=pic_id,
                frame_index=frame_index,
                face_index=next_index,
                bbox=bbox_vals,
            )
            session.add(face)
            session.commit()
            session.refresh(face)
            return face

        face = server.vault.db.run_task(create_face, priority=DBPriority.IMMEDIATE)
        if not face:
            raise HTTPException(status_code=404, detail="Picture not found")
        server.vault.notify(EventType.CHANGED_PICTURES)
        return safe_model_dict(face)

    @router.delete(
        "/pictures/{id}/face/{index}",
        summary="Delete face by index",
        description="Deletes a face at frame 0 by index and reindexes remaining faces for stable ordering.",
    )
    async def delete_picture_face(id: str, index: int):
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

        def delete_face(session: Session):
            face = session.exec(
                select(Face).where(
                    Face.picture_id == pic_id,
                    Face.frame_index == 0,
                    Face.face_index == index,
                )
            ).first()
            if not face:
                return False
            session.delete(face)
            remaining = session.exec(
                select(Face)
                .where(
                    Face.picture_id == pic_id,
                    Face.frame_index == 0,
                    Face.face_index >= 0,
                )
                .order_by(Face.face_index, Face.id)
            ).all()
            for next_idx, entry in enumerate(remaining):
                if entry.face_index != next_idx:
                    entry.face_index = next_idx
                    session.add(entry)
            if not remaining:
                sentinel = session.exec(
                    select(Face).where(
                        Face.picture_id == pic_id,
                        Face.frame_index == 0,
                        Face.face_index == -1,
                    )
                ).first()
                if sentinel is None:
                    session.add(
                        Face(
                            picture_id=pic_id,
                            frame_index=0,
                            face_index=-1,
                            character_id=None,
                            bbox=None,
                        )
                    )
            session.commit()
            return True

        deleted = server.vault.db.run_task(delete_face, priority=DBPriority.IMMEDIATE)
        if not deleted:
            raise HTTPException(status_code=404, detail="Face not found")
        server.vault.notify(EventType.CHANGED_PICTURES)
        return {"status": "success", "message": "Face deleted."}

    @router.get(
        "/pictures/{id}/character_likeness",
        summary="Get picture character likeness",
        description="Computes max character-likeness score for faces in a picture against a reference character.",
    )
    async def get_picture_character_likeness(
        id: str,
        reference_character_id: int = Query(...),
        character_id: str = Query(None),
    ):
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

        def fetch_picture_characters(session):
            pic = session.exec(
                select(Picture).where(
                    Picture.id == pic_id,
                    Picture.deleted.is_(False),
                )
            ).first()
            if not pic:
                return None
            char_ids = [c.id for c in pic.characters] if pic.characters else []
            return {"character_ids": char_ids}

        context = server.vault.db.run_task(fetch_picture_characters)
        if not context:
            raise HTTPException(status_code=404, detail="Picture not found")

        def has_assigned_faces(session):
            face = session.exec(
                select(Face.id).where(
                    Face.picture_id == pic_id,
                    Face.character_id.is_not(None),
                )
            ).first()
            return face is not None

        def is_in_picture_set(session):
            member = session.exec(
                select(PictureSetMember.id).where(PictureSetMember.picture_id == pic_id)
            ).first()
            return member is not None

        if character_id == "UNASSIGNED" and (
            server.vault.db.run_task(has_assigned_faces)
            or server.vault.db.run_task(is_in_picture_set)
        ):
            return {
                "picture_id": pic_id,
                "character_likeness": None,
                "eligible": False,
            }

        def fetch_face_ids(session):
            query = select(Face.id).where(Face.picture_id == pic_id)
            if character_id == "UNASSIGNED":
                query = query.where(Face.character_id.is_(None))
            elif character_id and character_id != "ALL":
                query = query.where(Face.character_id == int(character_id))
            return session.exec(query).all()

        face_ids = server.vault.db.run_task(fetch_face_ids)
        if not face_ids:
            if character_id and character_id not in ("ALL", "UNASSIGNED"):
                return {
                    "picture_id": pic_id,
                    "character_likeness": None,
                    "eligible": False,
                }
            return {
                "picture_id": pic_id,
                "character_likeness": 0.0,
                "eligible": True,
            }

        def fetch_faces(session, ids):
            return session.exec(select(Face).where(Face.id.in_(ids))).all()

        candidate_faces = server.vault.db.run_task(fetch_faces, face_ids)
        reference_faces = server.vault.db.run_task(
            select_reference_faces_for_character,
            int(reference_character_id),
            10,
            priority=DBPriority.IMMEDIATE,
        )
        likeness_map = compute_character_likeness_for_faces(
            reference_faces,
            candidate_faces,
        )
        if not likeness_map:
            return {
                "picture_id": pic_id,
                "character_likeness": 0.0,
                "eligible": False,
            }
        score = 0.0
        for face_id in face_ids:
            score = max(score, float(likeness_map.get(face_id, 0.0)))

        return {
            "picture_id": pic_id,
            "character_likeness": score,
            "eligible": True,
        }

    @router.get(
        "/pictures/{id}/{field}",
        summary="Get raw picture field",
        description="Returns a single picture field value; large binary fields are base64 encoded and thumbnail returns image bytes.",
    )
    async def get_picture_field(id: str, field: str):
        pics = server.vault.db.run_task(
            lambda session: Picture.find(
                session,
                id=id,
                select_fields=[field],
                include_deleted=True,
            )
        )
        if not pics:
            logger.error(f"Picture not found for id={id}")
            raise HTTPException(status_code=404, detail="Picture not found")
        pic = pics[0]

        if field == "thumbnail":
            return Response(content=pic.thumbnail, media_type="image/png")
        if field in Picture.large_binary_fields():
            return {field: base64.b64encode(getattr(pic, field)).decode("utf-8")}
        return {field: safe_model_dict(getattr(pic, field))}

    @router.patch(
        "/pictures/{id}",
        summary="Patch picture fields",
        description="Updates mutable picture fields from query/body parameters, including tag replacement when provided.",
    )
    async def patch_picture(id: str, request: Request):
        params = dict(request.query_params)

        logger.debug("Got a PATCH request for picture id={}".format(id))

        content_type = request.headers.get("content-type", "")

        json_body = None
        if "application/json" in content_type:
            try:
                json_body = await request.json()
            except Exception:
                json_body = None

        try:
            pic_list = server.vault.db.run_task(
                lambda session: Picture.find(session, id=id, include_deleted=True)
            )
            if not pic_list:
                raise HTTPException(status_code=404, detail="Picture not found")
            pic = pic_list[0]
        except KeyError:
            raise HTTPException(status_code=404, detail="Picture not found")

        logger.debug(f"Updating picture id={id}")
        if json_body and isinstance(json_body, dict):
            params.update(json_body)

        logger.debug(
            f"Updating picture id={id} with params: {params} and json_body: {json_body}"
        )
        updated = False
        updated_fields = {}
        for key, value in params.items():
            if not hasattr(pic, key):
                logger.warning(
                    f"Picture does not have key '{key}' in PATCH request. Ignoring."
                )
                continue
            if key == "tags":
                if value is None:
                    continue
                if not isinstance(value, list):
                    raise HTTPException(
                        status_code=400,
                        detail="tags must be a list",
                    )
                if not value:
                    continue
                tags = [
                    tag if isinstance(tag, str) else str(tag)
                    for tag in value
                    if tag is not None
                ]
                if tags:
                    server.vault.db.run_task(Picture.clear_field, [pic.id], "tags")
                    for tag in tags:
                        server.vault.db.run_task(
                            Picture.set_tag, pic.id, tag, priority=DBPriority.IMMEDIATE
                        )
                    updated = True
                continue
            if key == "score":
                try:
                    value = int(value)
                except Exception:
                    value = None
            if getattr(pic, key) != value:
                updated_fields[key] = value
                updated = True

        if updated:
            picture_id = pic.id

            def apply_picture_updates(session: Session, picture_id: int, fields: dict):
                pic_db = session.get(Picture, picture_id)
                if pic_db is None:
                    raise KeyError("Picture not found")
                for field_name, field_value in fields.items():
                    setattr(pic_db, field_name, field_value)
                session.add(pic_db)
                session.commit()
                return pic_db

            try:
                pic = server.vault.db.run_task(
                    apply_picture_updates,
                    picture_id,
                    updated_fields,
                    priority=DBPriority.IMMEDIATE,
                )
            except KeyError:
                raise HTTPException(status_code=404, detail="Picture not found")
            server.vault.notify(EventType.CHANGED_PICTURES, [picture_id])

        return {"status": "success", "picture": safe_model_dict(pic)}

    @router.post(
        "/pictures/scrapheap/restore",
        summary="Restore deleted pictures",
        description="Restores deleted pictures from scrapheap, either all deleted pictures or a provided picture id subset.",
    )
    async def restore_scrapheap(payload: dict | None = Body(None)):
        picture_ids = None
        if payload:
            ids = payload.get("picture_ids")
            if ids is not None:
                if not isinstance(ids, list) or not ids:
                    raise HTTPException(
                        status_code=400,
                        detail="picture_ids must be a non-empty list",
                    )
                picture_ids = ids

        def restore_pictures(session: Session, ids: list[int] | None):
            query = select(Picture).where(Picture.deleted.is_(True))
            if ids is not None:
                query = query.where(Picture.id.in_(ids))
            pics = session.exec(query).all()
            restored_count = 0
            for pic in pics:
                pic.deleted = False
                session.add(pic)
                restored_count += 1
            session.commit()
            return restored_count

        restored_count = server.vault.db.run_task(
            restore_pictures,
            picture_ids,
            priority=DBPriority.IMMEDIATE,
        )
        server.vault.notify(EventType.CHANGED_PICTURES)
        return {"status": "success", "restored_count": restored_count}

    @router.delete(
        "/pictures/scrapheap",
        summary="Permanently delete scrapheap pictures",
        description="Permanently removes deleted pictures from database and disk for provided ids or for all scrapheap items when omitted.",
    )
    async def delete_scrapheap_selection(
        background_tasks: BackgroundTasks,
        payload: dict | None = Body(None),
    ):
        ids = None
        if payload is not None:
            maybe_ids = (
                payload.get("picture_ids") if isinstance(payload, dict) else None
            )
            if maybe_ids is not None:
                if not isinstance(maybe_ids, list) or not maybe_ids:
                    raise HTTPException(
                        status_code=400,
                        detail="picture_ids must be a non-empty list",
                    )
                try:
                    ids = [int(pid) for pid in maybe_ids]
                except (TypeError, ValueError):
                    raise HTTPException(
                        status_code=400,
                        detail="picture_ids must contain valid integers",
                    )

        def fetch_deleted(session: Session, ids: list[int] | None):
            query = select(Picture.id, Picture.file_path).where(
                Picture.deleted.is_(True)
            )
            if ids is not None:
                query = query.where(Picture.id.in_(ids))
            return session.exec(query).all()

        rows = server.vault.db.run_task(
            fetch_deleted, ids, priority=DBPriority.IMMEDIATE
        )
        if not rows:
            return {"status": "success", "deleted_count": 0}

        picture_ids = [row[0] for row in rows if row[0] is not None]
        file_paths = [row[1] for row in rows if row[1]]

        def delete_files(image_root: str, paths: list[str]):
            for rel_path in paths:
                file_path = ImageUtils.resolve_picture_path(image_root, rel_path)
                if file_path and os.path.isfile(file_path):
                    try:
                        os.remove(file_path)
                    except Exception as e:
                        logger.error(
                            "Failed to delete picture file %s: %s",
                            file_path,
                            e,
                        )
                thumb_path = ImageUtils.get_thumbnail_path(image_root, rel_path)
                if thumb_path and os.path.isfile(thumb_path):
                    try:
                        os.remove(thumb_path)
                    except Exception as e:
                        logger.warning(
                            "Failed to delete thumbnail %s: %s",
                            thumb_path,
                            e,
                        )

        background_tasks.add_task(
            delete_files,
            server.vault.image_root,
            file_paths,
        )

        def delete_rows(session: Session, ids: list[int]):
            if not ids:
                return 0
            session.exec(delete(Picture).where(Picture.id.in_(ids)))
            session.commit()
            return len(ids)

        deleted_count = server.vault.db.run_task(
            delete_rows,
            picture_ids,
            priority=DBPriority.IMMEDIATE,
        )
        server.vault.notify(EventType.CHANGED_PICTURES)
        return {"status": "success", "deleted_count": deleted_count}

    @router.delete(
        "/pictures/{id}",
        summary="Move picture to scrapheap",
        description="Soft-deletes a picture by marking it deleted, making it appear in scrapheap views.",
    )
    async def delete_picture(id: str):
        def delete_pic(session, id):
            pic = session.get(Picture, id)
            if not pic:
                return False
            if pic.deleted:
                return True
            pic.deleted = True
            session.add(pic)
            session.commit()
            return True

        success = server.vault.db.run_task(delete_pic, id)
        if not success:
            raise HTTPException(status_code=404, detail="Picture not found")
        return JSONResponse(
            content={"status": "success", "message": f"Picture id={id} deleted."}
        )

    @router.get(
        "/pictures",
        summary="List pictures",
        description="Lists pictures with filtering, sort, pagination, and optional grid field projection.",
    )
    async def list_pictures(
        request: Request,
        sort: str = Query(None),
        descending: bool = Query(True),
        offset: int = Query(0),
        limit: int = Query(sys.maxsize),
        fields: str = Query(None),
    ):
        if fields == "grid":
            metadata_fields = list(Picture.grid_fields())
        else:
            metadata_fields = Picture.metadata_fields()
        return _select_pictures_for_listing(
            server=server,
            request=request,
            sort=sort,
            descending=descending,
            offset=offset,
            limit=limit,
            metadata_fields=metadata_fields,
            return_ids_only=False,
        )

    return router
