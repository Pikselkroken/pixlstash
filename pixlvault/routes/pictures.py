import ast
import concurrent.futures
import base64
import os
import re
import sys
import uuid
import zipfile
from collections import defaultdict, deque
from email.utils import formatdate

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
from sqlalchemy import exists, func
from sqlmodel import Session, select

from pixlvault.database import DBPriority
from pixlvault.db_models import (
    Character,
    Face,
    FaceTag,
    Hand,
    HandTag,
    Picture,
    PictureLikeness,
    PictureSetMember,
    PictureSet,
    SortMechanism,
    Tag,
    TAG_EMPTY_SENTINEL,
)
from pixlvault.event_types import EventType
from pixlvault.pixl_logging import get_logger
from pixlvault.picture_scoring import (
    compute_character_likeness_for_faces,
    fetch_smart_score_data,
    find_pictures_by_character_likeness,
    find_pictures_by_smart_score,
    get_smart_score_penalized_tags_from_request,
    prepare_smart_score_inputs,
    select_reference_faces_for_character,
)
from pixlvault.picture_utils import PictureUtils
from pixlvault.utils import safe_model_dict, serialize_tag_objects
from pixlvault.worker_registry import WorkerType

logger = get_logger(__name__)


def _create_picture_imports(server, uploaded_files, dest_folder):
    """
    Given a list of (img_bytes, ext), create Picture objects for new images,
    skipping duplicates based on pixel_sha hash.
    Returns (shas, existing_map, new_pictures)
    """

    def create_sha(img_bytes):
        return PictureUtils.calculate_hash_from_bytes(img_bytes)

    with concurrent.futures.ThreadPoolExecutor() as executor:
        shas = list(
            executor.map(create_sha, (img_bytes for img_bytes, _ in uploaded_files))
        )

    existing_pictures = server.vault.db.run_immediate_read_task(
        lambda session: Picture.find(session, pixel_shas=shas)
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
            return PictureUtils.create_picture_from_bytes(
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

    def normalize_character_id(value):
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
    character_id = normalize_character_id(query_params.pop("character_id", None))
    reference_character_id = query_params.pop("reference_character_id", None)

    try:
        sort_mech = (
            SortMechanism.from_string(sort, descending=descending) if sort else None
        )
    except ValueError as ve:
        logger.error(f"Invalid sort mechanism: {sort} - {ve}")
        raise HTTPException(status_code=400, detail=str(ve))

    pics = []
    if sort_mech and sort_mech.key == SortMechanism.Keys.CHARACTER_LIKENESS:
        if not reference_character_id:
            raise HTTPException(
                status_code=400,
                detail="reference_character_id is required for CHARACTER_LIKENESS sort",
            )
        pics = find_pictures_by_character_likeness(
            server,
            character_id,
            reference_character_id,
            offset,
            limit,
            descending,
        )
        if return_ids_only:
            return [pic.get("id") for pic in pics if pic.get("id") is not None]
        return pics
    elif sort_mech and sort_mech.key == SortMechanism.Keys.SMART_SCORE:
        penalized_tags = get_smart_score_penalized_tags_from_request(server, request)
        pics = find_pictures_by_smart_score(
            server,
            format,
            offset,
            limit,
            descending,
            penalized_tags=penalized_tags,
        )
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
            **query_params,
        )
    if return_ids_only:
        return [pic.id for pic in pics]
    return serialize_metadata(pics)


def create_router(server) -> APIRouter:
    router = APIRouter()

    @router.get("/sort_mechanisms")
    async def get_pictures_sort_mechanisms():
        """Return available sorting mechanisms for pictures."""
        result = SortMechanism.all()
        logger.debug("Returning sort mechanisms: {}".format(result))
        return result

    @router.get("/pictures/stacks")
    async def get_picture_stacks(
        threshold: float = 0.0,
        min_group_size: int = 2,
        set_id: int = Query(None),
        character_id: str = Query(None),
        format: list[str] = Query(None),
    ):
        candidate_ids = None

        if set_id is not None:

            def fetch_set_ids(session, set_id):
                members = session.exec(
                    select(PictureSetMember).where(PictureSetMember.set_id == set_id)
                ).all()
                return [m.picture_id for m in members]

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
                    query = query.where(unassigned_condition, not_in_set_condition)
                    return list(session.exec(query).all())

                candidate_ids = set(
                    server.vault.db.run_immediate_read_task(fetch_unassigned_ids)
                )
            elif character_id == "ALL" or character_id == "":
                candidate_ids = None
            elif character_id.isdigit():

                def fetch_character_ids(session, character_id):
                    faces = session.exec(
                        select(Face).where(Face.character_id == character_id)
                    ).all()
                    return list({face.picture_id for face in faces})

                candidate_ids = set(
                    server.vault.db.run_immediate_read_task(
                        fetch_character_ids, int(character_id)
                    )
                )

        if format:

            def fetch_format_ids(session, format):
                rows = session.exec(
                    select(Picture.id).where(Picture.format.in_(format))
                ).all()
                return list(rows)

            format_ids = set(
                server.vault.db.run_immediate_read_task(fetch_format_ids, format)
            )
            candidate_ids = (
                format_ids if candidate_ids is None else candidate_ids & format_ids
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
        for idx, group in enumerate(groups):
            for pic_id in sorted(group):
                stack_index_map[pic_id] = idx
                ordered_ids.append(pic_id)

        if not ordered_ids:
            return []

        def fetch_pictures(session, ids):
            return Picture.find(
                session,
                id=ids,
                select_fields=Picture.metadata_fields(),
            )

        ordered_pics = server.vault.db.run_immediate_read_task(
            fetch_pictures, ordered_ids
        )
        pics_by_id = {pic.id: pic for pic in ordered_pics}
        ordered_pics = [pics_by_id.get(pid) for pid in ordered_ids]
        ordered_pics = [pic for pic in ordered_pics if pic is not None]

        response = []
        for pic in ordered_pics:
            pic_dict = safe_model_dict(pic)
            pic_dict["stack_index"] = stack_index_map.get(pic.id)
            response.append(pic_dict)

        return response

    @router.get("/pictures/thumbnails/{id}.webp")
    async def get_thumbnail(id: int):
        def fetch_picture(session: Session, picture_id: int):
            pics = Picture.find(
                session,
                id=picture_id,
                select_fields=[
                    "id",
                    "file_path",
                ],
            )
            return pics[0] if pics else None

        pic = server.vault.db.run_immediate_read_task(fetch_picture, id)
        if not pic or not getattr(pic, "file_path", None):
            raise HTTPException(status_code=404, detail="Picture not found")

        thumb_path = PictureUtils.get_thumbnail_path(
            server.vault.image_root, pic.file_path
        )
        if thumb_path and os.path.exists(thumb_path):
            return FileResponse(thumb_path, media_type="image/webp")

        resolved_path = PictureUtils.resolve_picture_path(
            server.vault.image_root, pic.file_path
        )
        if resolved_path and os.path.exists(resolved_path):
            img = PictureUtils.load_image_or_video(resolved_path)
            if img is not None:
                if not isinstance(img, Image.Image):
                    img = Image.fromarray(img)
                thumbnail_bytes = PictureUtils.generate_thumbnail_bytes(img)
                if thumbnail_bytes:
                    saved_thumb = PictureUtils.write_thumbnail_bytes(
                        server.vault.image_root, pic.file_path, thumbnail_bytes
                    )
                    if saved_thumb and os.path.exists(saved_thumb):
                        return FileResponse(saved_thumb, media_type="image/webp")
                    logger.warning(
                        "Failed to persist on-demand thumbnail for picture %s",
                        pic.id,
                    )
            else:
                logger.warning(
                    "Failed to load image for on-demand thumbnail: %s",
                    resolved_path,
                )
        else:
            logger.warning(
                "Missing source file for on-demand thumbnail: %s",
                resolved_path,
            )

        raise HTTPException(status_code=404, detail="Thumbnail not found")

    @router.post("/pictures/thumbnails")
    async def get_thumbnails(request: Request, payload: dict = Body(...)):
        ids = payload.get("ids", [])
        if not isinstance(ids, list):
            raise HTTPException(status_code=400, detail="'ids' must be a list")

        penalized_tags = get_smart_score_penalized_tags_from_request(server, request)
        penalized_tag_set = {
            str(tag).strip().lower() for tag in (penalized_tags or {}).keys() if tag
        }
        ids_int = []
        for raw_id in ids:
            try:
                ids_int.append(int(raw_id))
            except (TypeError, ValueError):
                continue

        penalized_tag_map = defaultdict(list)
        if ids_int and penalized_tag_set:

            def fetch_penalized_tags(session: Session):
                rows = session.exec(
                    select(Tag.picture_id, Tag.tag).where(
                        Tag.picture_id.in_(ids_int),
                        Tag.tag.is_not(None),
                        func.lower(Tag.tag).in_(penalized_tag_set),
                    )
                ).all()
                return rows

            rows = server.vault.db.run_task(
                fetch_penalized_tags, priority=DBPriority.IMMEDIATE
            )
            for pic_id, tag in rows or []:
                if tag:
                    penalized_tag_map[pic_id].append(tag)

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
                    "hands",
                    "thumbnail_left",
                    "thumbnail_top",
                    "thumbnail_side",
                ],
            )
        )
        results = {}
        for pic in pics:
            try:
                face_entries = []
                hand_entries = []
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
                        character = (
                            server.vault.db.run_task(
                                lambda session: Character.find(
                                    session,
                                    id=face.character_id,
                                    select_fields=["name"],
                                )
                            )
                            if face.character_id
                            else None
                        )
                        face_entries.append(
                            {
                                "id": face.id,
                                "bbox": list(bbox),
                                "character_id": face.character_id,
                                "character_name": getattr(character[0], "name", None)
                                if character
                                else None,
                                "frame_index": getattr(face, "frame_index", None),
                            }
                        )
                for hand in getattr(pic, "hands", []):
                    bbox = None
                    try:
                        bbox = hand.bbox if hasattr(hand, "bbox") else None
                        if bbox and isinstance(bbox, str):
                            bbox = ast.literal_eval(bbox)
                    except Exception:
                        bbox = None
                    if bbox and isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                        hand_entries.append(
                            {
                                "id": hand.id,
                                "bbox": list(bbox),
                                "frame_index": getattr(hand, "frame_index", None),
                                "hand_index": getattr(hand, "hand_index", None),
                            }
                        )

                face_data = []
                hand_data = []
                for entry in face_entries:
                    mapped_bbox, mapped = map_bbox_to_thumbnail(entry.get("bbox"), pic)
                    mapped_any = mapped_any or mapped
                    face_data.append({**entry, "bbox": mapped_bbox})

                for entry in hand_entries:
                    mapped_bbox, mapped = map_bbox_to_thumbnail(entry.get("bbox"), pic)
                    mapped_any = mapped_any or mapped
                    hand_data.append({**entry, "bbox": mapped_bbox})

                thumbnail_url = f"/pictures/thumbnails/{pic.id}.webp"
                results[pic.id] = {
                    "thumbnail": thumbnail_url,
                    "faces": face_data,
                    "hands": hand_data,
                    "thumbnail_width": 256 if mapped_any else None,
                    "thumbnail_height": 256 if mapped_any else None,
                    "penalized_tags": list(
                        dict.fromkeys(penalized_tag_map.get(pic.id, []))
                    ),
                }
            except Exception as exc:
                logger.error(
                    f"Picture not found or error for id={pic.id} (thumbnail request): {exc}"
                )
                results[pic.id] = {
                    "thumbnail": None,
                    "faces": [],
                    "hands": [],
                    "penalized_tags": [],
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

    @router.get("/pictures/export")
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

        def generate_zip():
            try:
                export_type_value = (
                    request.query_params.get("export_type")
                    or request.query_params.get("exportType")
                    or export_type
                )
                export_type_normalized = Picture.ExportType.from_string(
                    export_type_value
                )
                caption_mode_normalized = (caption_mode or "description").lower()
                if caption_mode_normalized not in {"none", "description", "tags"}:
                    caption_mode_normalized = "description"
                include_character_name_enabled = (
                    bool(include_character_name) and caption_mode_normalized != "none"
                )
                if export_type_normalized != Picture.ExportType.FULL:
                    caption_mode_normalized = "tags"
                    include_character_name_enabled = False
                resolution_normalized = (resolution or "original").lower()
                if resolution_normalized not in {"original", "half", "quarter"}:
                    resolution_normalized = "original"
                scale_map = {
                    "original": 1.0,
                    "half": 0.5,
                    "quarter": 0.25,
                }
                scale_factor = scale_map.get(resolution_normalized, 1.0)

                picture_ids = request.query_params.getlist("id")

                select_fields = Picture.metadata_fields()
                if export_type_normalized == Picture.ExportType.FULL:
                    if caption_mode_normalized != "none":
                        select_fields = select_fields | {"tags"}
                    if include_character_name_enabled:
                        select_fields = select_fields | {"characters"}

                pics = []

                if picture_ids:
                    pics = server.vault.db.run_task(
                        Picture.find, id=picture_ids, select_fields=select_fields
                    )
                elif set_id is not None:
                    logger.debug("Exporting pictures set {} ".format(set_id))

                    def fetch_members(session, set_id):
                        members = session.exec(
                            select(PictureSetMember).where(
                                PictureSetMember.set_id == set_id
                            )
                        ).all()
                        picture_ids = [m.picture_id for m in members]
                        if not picture_ids:
                            return []
                        return Picture.find(
                            session,
                            id=picture_ids,
                            select_fields=select_fields,
                        )

                    pics = server.vault.db.run_task(fetch_members, set_id)
                elif query:
                    logger.debug(
                        "Exporting pictures using search query: {}".format(query)
                    )

                    def find_by_text(session, query):
                        words = re.findall(r"\b\w+\b", query.lower())
                        query_full = "A photo of " + query
                        return [
                            r[0]
                            for r in Picture.semantic_search(
                                session,
                                query_full,
                                words,
                                text_to_embedding=server.vault.generate_text_embedding,
                                offset=0,
                                limit=sys.maxsize,
                                threshold=threshold,
                                select_fields=select_fields,
                            )
                        ]

                    pics = server.vault.db.run_task(find_by_text, query)
                else:
                    logger.debug("Exporting pictures using list filters")
                    ordered_ids = _select_pictures_for_listing(
                        server=server,
                        request=request,
                        sort=None,
                        descending=True,
                        offset=0,
                        limit=sys.maxsize,
                        metadata_fields=select_fields,
                        return_ids_only=True,
                        exclude_query_params={
                            "query",
                            "set_id",
                            "threshold",
                            "caption_mode",
                            "include_character_name",
                            "export_type",
                            "resolution",
                        },
                    )
                    if ordered_ids:
                        pics = server.vault.db.run_task(
                            Picture.find,
                            id=ordered_ids,
                            select_fields=select_fields,
                        )
                        pic_map = {pic.id: pic for pic in pics}
                        pics = [
                            pic_map.get(pid) for pid in ordered_ids if pid in pic_map
                        ]

                logger.debug(
                    f"Export task {task_id}: {len(pics)} pictures to be added to the ZIP."
                )

                if not pics:
                    server.export_tasks[task_id]["status"] = "failed"
                    return

                filename_parts = []
                if set_id is not None:

                    def get_set(session, set_id):
                        return session.get(PictureSet, set_id)

                    picture_set = server.vault.db.run_task(get_set, set_id)
                    if picture_set:
                        filename_parts.append(picture_set.name.replace(" ", "_"))
                if query:
                    filename_parts.append(f"search_{query[:20]}")

                filename = "_".join(filename_parts) if filename_parts else "pictures"
                filename = f"{filename}_{len(pics)}_images.zip"
                server.export_tasks[task_id]["filename"] = filename

                zip_path = os.path.join(server.TEMP_EXPORT_DIR, f"export_{task_id}.zip")
                feature_faces_by_pic = {}
                feature_hands_by_pic = {}
                face_tags_by_face = {}
                hand_tags_by_hand = {}

                def _clamp_bbox(bbox, width, height):
                    if not bbox or len(bbox) != 4:
                        return None
                    x_min, y_min, x_max, y_max = [int(round(v)) for v in bbox]
                    x_min = max(0, min(x_min, width - 1))
                    y_min = max(0, min(y_min, height - 1))
                    x_max = max(x_min + 1, min(x_max, width))
                    y_max = max(y_min + 1, min(y_max, height))
                    return [x_min, y_min, x_max, y_max]

                if export_type_normalized != Picture.ExportType.FULL:

                    def fetch_features(session: Session, picture_ids):
                        faces = session.exec(
                            select(Face).where(Face.picture_id.in_(picture_ids))
                        ).all()
                        hands = session.exec(
                            select(Hand).where(Hand.picture_id.in_(picture_ids))
                        ).all()
                        face_ids = [face.id for face in faces]
                        hand_ids = [hand.id for hand in hands]

                        face_tags = []
                        hand_tags = []
                        if face_ids:
                            face_tags = session.exec(
                                select(FaceTag.face_id, Tag.tag)
                                .join(Tag, Tag.id == FaceTag.tag_id)
                                .where(FaceTag.face_id.in_(face_ids))
                            ).all()
                        if hand_ids:
                            hand_tags = session.exec(
                                select(HandTag.hand_id, Tag.tag)
                                .join(Tag, Tag.id == HandTag.tag_id)
                                .where(HandTag.hand_id.in_(hand_ids))
                            ).all()

                        faces_by_pic = {}
                        for face in faces:
                            faces_by_pic.setdefault(face.picture_id, []).append(face)

                        hands_by_pic = {}
                        for hand in hands:
                            hands_by_pic.setdefault(hand.picture_id, []).append(hand)

                        face_tags_by_face = {}
                        for face_id, tag in face_tags:
                            face_tags_by_face.setdefault(face_id, []).append(tag)

                        hand_tags_by_hand = {}
                        for hand_id, tag in hand_tags:
                            hand_tags_by_hand.setdefault(hand_id, []).append(tag)

                        return (
                            faces_by_pic,
                            hands_by_pic,
                            face_tags_by_face,
                            hand_tags_by_hand,
                        )

                    (
                        feature_faces_by_pic,
                        feature_hands_by_pic,
                        face_tags_by_face,
                        hand_tags_by_hand,
                    ) = server.vault.db.run_task(
                        fetch_features,
                        [pic.id for pic in pics],
                    )

                if export_type_normalized == Picture.ExportType.FULL:
                    total_items = len(pics)
                else:
                    total_items = 0
                    export_faces = export_type_normalized in {
                        Picture.ExportType.FACE,
                        Picture.ExportType.FACE_HAND,
                    }
                    export_hands = export_type_normalized in {
                        Picture.ExportType.HAND,
                        Picture.ExportType.FACE_HAND,
                    }
                    for pic in pics:
                        if not getattr(pic, "file_path", None) or not os.path.exists(
                            PictureUtils.resolve_picture_path(
                                server.vault.image_root, pic.file_path
                            )
                        ):
                            continue
                        full_path = PictureUtils.resolve_picture_path(
                            server.vault.image_root, pic.file_path
                        )
                        if PictureUtils.is_video_file(full_path):
                            continue
                        if export_faces:
                            faces = feature_faces_by_pic.get(pic.id, [])
                            for face in faces:
                                if getattr(face, "face_index", 0) < 0:
                                    continue
                                if not face.bbox:
                                    continue
                                total_items += 1
                        if export_hands:
                            hands = feature_hands_by_pic.get(pic.id, [])
                            for hand in hands:
                                if getattr(hand, "hand_index", 0) < 0:
                                    continue
                                if not hand.bbox:
                                    continue
                                total_items += 1

                server.export_tasks[task_id]["total"] = total_items
                server.export_tasks[task_id]["processed"] = 0

                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
                    for idx, pic in enumerate(pics, start=1):
                        if (
                            hasattr(pic, "file_path")
                            and pic.file_path
                            and os.path.exists(
                                PictureUtils.resolve_picture_path(
                                    server.vault.image_root, pic.file_path
                                )
                            )
                        ):
                            full_path = PictureUtils.resolve_picture_path(
                                server.vault.image_root, pic.file_path
                            )
                            ext = os.path.splitext(full_path)[1]
                            if export_type_normalized == Picture.ExportType.FULL:
                                arcname = f"image_{idx:05d}{ext}"
                                if (
                                    scale_factor < 1.0
                                    and not PictureUtils.is_video_file(full_path)
                                ):
                                    try:
                                        from PIL import Image
                                        from io import BytesIO

                                        with Image.open(full_path) as img:
                                            new_width = max(
                                                1, int(img.width * scale_factor)
                                            )
                                            new_height = max(
                                                1, int(img.height * scale_factor)
                                            )
                                            resized = img.resize(
                                                (new_width, new_height),
                                                resample=Image.LANCZOS,
                                            )
                                            buffer = BytesIO()
                                            save_format = (
                                                img.format or ext.lstrip(".").upper()
                                            )
                                            if save_format.upper() in {"JPG", "JPEG"}:
                                                resized.save(
                                                    buffer, format="JPEG", quality=95
                                                )
                                            else:
                                                resized.save(buffer, format=save_format)

                                        zip_file.writestr(arcname, buffer.getvalue())
                                    except Exception as exc:
                                        logger.warning(
                                            "Failed to resize %s (%s); falling back to original.",
                                            full_path,
                                            exc,
                                        )
                                        zip_file.write(full_path, arcname=arcname)
                                else:
                                    zip_file.write(full_path, arcname=arcname)

                                def build_tag_caption(picture):
                                    tags = []
                                    for tag in getattr(picture, "tags", []) or []:
                                        tag_value = getattr(tag, "tag", None)
                                        if tag_value in (None, TAG_EMPTY_SENTINEL):
                                            continue
                                        tags.append(tag_value)
                                    return ", ".join(tags)

                                caption_text = None
                                if caption_mode_normalized == "description":
                                    caption_text = pic.description or ""
                                    if not caption_text:
                                        caption_text = build_tag_caption(pic)
                                elif caption_mode_normalized == "tags":
                                    caption_text = build_tag_caption(pic)

                                if include_character_name_enabled:
                                    character_names = []
                                    for character in (
                                        getattr(pic, "characters", []) or []
                                    ):
                                        name_value = getattr(character, "name", None)
                                        if name_value:
                                            character_names.append(name_value)

                                    if character_names:
                                        if caption_mode_normalized == "tags":
                                            caption_text = ", ".join(
                                                character_names + [caption_text]
                                            )
                                        elif caption_mode_normalized == "description":
                                            caption_text = (
                                                ", ".join(character_names)
                                                + ": "
                                                + caption_text
                                            )

                                if (
                                    caption_mode_normalized != "none"
                                    and caption_text is not None
                                ):
                                    zip_file.writestr(
                                        f"image_{idx:05d}.txt",
                                        f"{caption_text}\n",
                                    )
                                server.export_tasks[task_id]["processed"] += 1
                            else:
                                if PictureUtils.is_video_file(full_path):
                                    continue
                                try:
                                    from PIL import Image
                                    from io import BytesIO

                                    with Image.open(full_path) as img:
                                        base_name = f"image_{idx:05d}"
                                        export_faces = export_type_normalized in {
                                            Picture.ExportType.FACE,
                                            Picture.ExportType.FACE_HAND,
                                        }
                                        export_hands = export_type_normalized in {
                                            Picture.ExportType.HAND,
                                            Picture.ExportType.FACE_HAND,
                                        }

                                        if export_faces:
                                            faces = feature_faces_by_pic.get(pic.id, [])
                                            for face in faces:
                                                if getattr(face, "face_index", 0) < 0:
                                                    continue
                                                if not face.bbox:
                                                    continue
                                                bbox = _clamp_bbox(
                                                    face.bbox, img.width, img.height
                                                )
                                                if not bbox:
                                                    continue
                                                crop = img.crop(bbox)
                                                buffer = BytesIO()
                                                crop.save(buffer, format="PNG")
                                                face_index = getattr(
                                                    face, "face_index", 0
                                                )
                                                arcname = f"{base_name}_face_{face_index:03d}.png"
                                                zip_file.writestr(
                                                    arcname, buffer.getvalue()
                                                )
                                                server.export_tasks[task_id][
                                                    "processed"
                                                ] += 1

                                                tags = face_tags_by_face.get(
                                                    face.id, []
                                                )
                                                if tags:
                                                    zip_file.writestr(
                                                        f"{base_name}_face_{face_index:03d}.txt",
                                                        ", ".join(tags) + "\n",
                                                    )

                                        if export_hands:
                                            hands = feature_hands_by_pic.get(pic.id, [])
                                            for hand in hands:
                                                if getattr(hand, "hand_index", 0) < 0:
                                                    continue
                                                if not hand.bbox:
                                                    continue
                                                bbox = _clamp_bbox(
                                                    hand.bbox, img.width, img.height
                                                )
                                                if not bbox:
                                                    continue
                                                crop = img.crop(bbox)
                                                buffer = BytesIO()
                                                crop.save(buffer, format="PNG")
                                                hand_index = getattr(
                                                    hand, "hand_index", 0
                                                )
                                                arcname = f"{base_name}_hand_{hand_index:03d}.png"
                                                zip_file.writestr(
                                                    arcname, buffer.getvalue()
                                                )
                                                server.export_tasks[task_id][
                                                    "processed"
                                                ] += 1

                                                tags = hand_tags_by_hand.get(
                                                    hand.id, []
                                                )
                                                if tags:
                                                    zip_file.writestr(
                                                        f"{base_name}_hand_{hand_index:03d}.txt",
                                                        ", ".join(tags) + "\n",
                                                    )

                                except Exception as exc:
                                    logger.warning(
                                        "Failed to export features for %s (%s).",
                                        full_path,
                                        exc,
                                    )

                zip_size = os.path.getsize(zip_path)
                logger.debug(
                    f"Export task {task_id}: ZIP file created with size {zip_size} bytes."
                )

                server.export_tasks[task_id]["status"] = "completed"
                server.export_tasks[task_id]["file_path"] = zip_path
            except Exception as exc:
                server.export_tasks[task_id]["status"] = "failed"
                logger.error(f"Export task {task_id} failed: {exc}")

        background_tasks.add_task(generate_zip)
        return JSONResponse({"task_id": task_id})

    @router.get("/pictures/export/status")
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

    @router.get("/pictures/export/download/{task_id}")
    async def download_export(task_id: str):
        task = server.export_tasks.get(task_id)
        if not task or task["status"] != "completed":
            raise HTTPException(status_code=404, detail="File not ready")

        filename = task.get("filename") or os.path.basename(task["file_path"])
        return FileResponse(task["file_path"], filename=filename)

    @router.get("/pictures/search")
    async def search_pictures(
        request: Request,
        query: str,
        offset: int = Query(0),
        limit: int = Query(sys.maxsize),
        threshold: float = Query(0.5),
    ):
        query_params = {}
        format = None
        if request.query_params:
            query_params = dict(request.query_params)
            query = query_params.pop("query", query)
            offset = int(query_params.pop("offset", offset))
            limit = int(query_params.pop("limit", limit))
            format = request.query_params.getlist("format")
        if not query:
            raise HTTPException(
                status_code=400, detail="Query parameter is required for search"
            )

        def find_by_text(session, query, offset, limit):
            words = re.findall(r"\b\w+\b", query.lower())
            query = "A photo of " + query
            return Picture.semantic_search(
                session,
                query,
                words,
                text_to_embedding=server.vault.generate_text_embedding,
                clip_text_to_embedding=server.vault.generate_clip_text_embedding,
                offset=offset,
                limit=limit,
                threshold=threshold,
                format=format,
                select_fields=Picture.metadata_fields(),
            )

        results = server.vault.db.run_task(find_by_text, query, offset, limit)
        return [Picture.serialize_with_likeness(r) for r in results]

    @router.post("/pictures/import")
    async def import_pictures(
        background_tasks: BackgroundTasks,
        file: list[UploadFile] = File(None),
    ):
        if not server.vault.is_worker_running(WorkerType.FACE):
            raise HTTPException(
                status_code=400,
                detail="Face worker is not running. Start it before import.",
            )

        dest_folder = server.vault.image_root
        logger.debug("Importing pictures to folder: " + str(dest_folder))
        os.makedirs(dest_folder, exist_ok=True)
        uploaded_files = []
        if file is not None:
            for upload in file:
                if not upload.filename:
                    continue
                contents = await upload.read()
                if not contents:
                    continue
                ext = os.path.splitext(upload.filename)[1].lower()
                if ext not in {
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
                }:
                    logger.error("Invalid file extension: %s", ext)
                    raise HTTPException(
                        status_code=400, detail="Invalid file extension"
                    )
                uploaded_files.append((contents, ext))
        else:
            logger.error("No files provided for import")
            raise HTTPException(status_code=400, detail="No image provided")

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
                            WorkerType.FACE, Picture, pic.id, "faces"
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

    @router.get("/pictures/import/status")
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

    @router.get("/pictures/{id}.{ext}")
    async def get_picture(request: Request, id: str, ext: str):
        if not isinstance(id, str):
            logger.error(f"Invalid id type: {type(id)} value: {id}")
            raise HTTPException(status_code=400, detail="Invalid picture id type")

        if not ext or not isinstance(ext, str):
            logger.error(f"Invalid extension type: {type(ext)} value: {ext}")
            raise HTTPException(status_code=400, detail="Invalid picture extension")
        id = int(id)

        pics = server.vault.db.run_task(lambda session: Picture.find(session, id=id))
        if not pics:
            logger.error(f"Picture not found for id={id}")
            raise HTTPException(status_code=404, detail="Picture not found")
        pic = pics[0]

        file_path = PictureUtils.resolve_picture_path(
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

        response = FileResponse(file_path)
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

    @router.get("/pictures/{id}/metadata")
    async def get_picture_metadata(
        request: Request,
        id: str,
        smart_score: bool = Query(False),
    ):
        metadata_fields = Picture.metadata_fields()
        pics = server.vault.db.run_task(
            Picture.find, id=id, select_fields=metadata_fields
        )
        if not pics:
            logger.error(f"Picture not found for id={id}")
            raise HTTPException(status_code=404, detail="Picture not found")
        pic = pics[0]

        def fetch_image_only_tags(session: Session, pic_id: int):
            face_tag_ids = (
                select(FaceTag.tag_id)
                .join(Tag, Tag.id == FaceTag.tag_id)
                .where(Tag.picture_id == pic_id)
            )
            hand_tag_ids = (
                select(HandTag.tag_id)
                .join(Tag, Tag.id == HandTag.tag_id)
                .where(Tag.picture_id == pic_id)
            )
            return session.exec(
                select(Tag).where(
                    Tag.picture_id == pic_id,
                    ~Tag.id.in_(face_tag_ids),
                    ~Tag.id.in_(hand_tag_ids),
                )
            ).all()

        pic_tags = server.vault.db.run_task(fetch_image_only_tags, pic.id)
        pic_dict = safe_model_dict(pic)
        pic_dict["tags"] = serialize_tag_objects(pic_tags)

        logger.info(f"Sending tags: {pic_dict['tags']} for picture id={pic.id}")

        if smart_score:
            try:
                penalized_tags = get_smart_score_penalized_tags_from_request(
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
                    penalized_tags=penalized_tags,
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
                        scores = PictureUtils.calculate_smart_score_batch_numpy(
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
            file_path = PictureUtils.resolve_picture_path(
                server.vault.image_root, pic.file_path
            )
            logger.debug(
                "[metadata] Extracting embedded metadata for id=%s path=%s",
                pic.id,
                file_path,
            )
            embedded_metadata = PictureUtils.extract_embedded_metadata(file_path)
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

    @router.delete("/pictures/{id}/face/{index}")
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
            session.commit()
            return True

        deleted = server.vault.db.run_task(delete_face, priority=DBPriority.IMMEDIATE)
        if not deleted:
            raise HTTPException(status_code=404, detail="Face not found")
        server.vault.notify(EventType.CHANGED_PICTURES)
        return {"status": "success", "message": "Face deleted."}

    @router.delete("/pictures/{id}/hand/{index}")
    async def delete_picture_hand(id: str, index: int):
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

        def delete_hand(session: Session):
            hand = session.exec(
                select(Hand).where(
                    Hand.picture_id == pic_id,
                    Hand.frame_index == 0,
                    Hand.hand_index == index,
                )
            ).first()
            if not hand:
                return False
            session.delete(hand)
            session.commit()
            return True

        deleted = server.vault.db.run_task(delete_hand, priority=DBPriority.IMMEDIATE)
        if not deleted:
            raise HTTPException(status_code=404, detail="Hand not found")
        server.vault.notify(EventType.CHANGED_PICTURES)
        return {"status": "success", "message": "Hand deleted."}

    @router.get("/pictures/{id}/character_likeness")
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
            pic = session.exec(select(Picture).where(Picture.id == pic_id)).first()
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

    @router.get("/pictures/{id}/{field}")
    async def get_picture_field(id: str, field: str):
        pics = server.vault.db.run_task(
            lambda session: Picture.find(session, id=id, select_fields=[field])
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

    @router.patch("/pictures/{id}")
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
                lambda session: Picture.find(session, id=id)
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
                    pic.id,
                    updated_fields,
                    priority=DBPriority.IMMEDIATE,
                )
            except KeyError:
                raise HTTPException(status_code=404, detail="Picture not found")
            server.vault.notify(EventType.CHANGED_PICTURES)

        return {"status": "success", "picture": safe_model_dict(pic)}

    @router.delete("/pictures/{id}")
    async def delete_picture(id: str):
        def delete_pic(session, id):
            pic = session.get(Picture, id)
            if not pic:
                return False
            file_path = PictureUtils.resolve_picture_path(
                server.vault.image_root, pic.file_path
            )
            if not file_path or not os.path.isfile(file_path):
                logger.error(
                    f"File path missing or does not exist for picture id={pic.id}, file_path={pic.file_path}"
                )
                session.delete(pic)
                session.commit()
                return True
            session.delete(pic)
            try:
                os.remove(file_path)
            except Exception as e:
                logger.error(f"Failed to delete picture file {file_path}: {e}")
                session.rollback()
                return False
            session.commit()
            return True

        success = server.vault.db.run_task(delete_pic, id)
        if not success:
            raise HTTPException(status_code=404, detail="Picture not found")
        return JSONResponse(
            content={"status": "success", "message": f"Picture id={id} deleted."}
        )

    @router.get("/pictures")
    async def list_pictures(
        request: Request,
        sort: str = Query(None),
        descending: bool = Query(True),
        offset: int = Query(0),
        limit: int = Query(sys.maxsize),
    ):
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
