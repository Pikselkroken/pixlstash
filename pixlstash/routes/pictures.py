import ast
import asyncio
import concurrent.futures
import base64
import os
import re
import shutil
import subprocess
import sys
import time
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
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import (
    Integer,
    and_,
    case,
    cast,
    delete,
    desc,
    exists,
    func,
    or_,
    text,
    update,
)
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    Character,
    Face,
    Picture,
    PictureLikeness,
    PictureProjectMember,
    PictureSetMember,
    Project,
    Quality,
    ReferenceFolder,
    SortMechanism,
    Tag,
    TagPrediction,
)
from pixlstash.event_types import EventType
from pixlstash.image_plugins.registry import get_image_plugin_manager
from pixlstash.image_plugins.service import apply_plugin_to_pictures
from pixlstash.pixl_logging import get_logger
from pixlstash.picture_scoring import (
    compute_character_likeness_for_faces,
    fetch_smart_score_data,
    find_pictures_by_character_likeness,
    get_smart_score_penalised_tags_from_request,
    prepare_smart_score_inputs,
    select_reference_faces_for_character,
)
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.quality.smart_score_utils import SmartScoreUtils
from pixlstash.utils.service.caption_utils import (
    _normalize_hidden_tags,
    serialize_tag_objects,
    sync_picture_sidecar,
)
from pixlstash.utils.service.serialization_utils import safe_model_dict
from pixlstash.utils.stack.stack_utils import _deduplicate_by_stack
from pixlstash.tasks import TaskType
from pixlstash.db_models.tag import TAG_EMPTY_SENTINEL

logger = get_logger(__name__)

_stats_cache: dict = {}
_STATS_TTL = 60.0


def clear_stats_cache() -> None:
    """Discard all cached /pictures/stats results (e.g. after tag mutations)."""
    _stats_cache.clear()


def _score_is_good_anchor(score_value: int | None) -> bool:
    """Return True if score belongs to the good-anchor class used by smart-score seeding."""
    return score_value is not None and score_value >= 4


def _score_is_bad_anchor(score_value: int | None) -> bool:
    """Return True if score belongs to the bad-anchor class used by smart-score seeding."""
    return score_value is not None and 0 < score_value <= 1


def _score_anchor_membership_changed(
    old_score: int | None,
    new_score: int | None,
) -> bool:
    """Return True when a score change crosses either smart-score anchor boundary."""
    return _score_is_good_anchor(old_score) != _score_is_good_anchor(
        new_score
    ) or _score_is_bad_anchor(old_score) != _score_is_bad_anchor(new_score)


_SIDECAR_TAG_PATTERN = re.compile(
    r"^[a-z0-9]+(?:[ _-][a-z0-9]+){0,2}$",
    re.IGNORECASE,
)

MEDIA_TYPE_BY_FORMAT = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "tiff": "image/tiff",
    "avif": "image/avif",
    "mp4": "video/mp4",
    "webm": "video/webm",
    "mov": "video/quicktime",
    "avi": "video/x-msvideo",
    "mkv": "video/x-matroska",
    "m4v": "video/mp4",
}


def _get_hidden_tags_from_request(server, request: Request) -> list[str]:
    if request.query_params.get("apply_tag_filter", "").lower() != "true":
        return []
    try:
        user = server.auth.get_user_for_request(request)
    except HTTPException:
        user = server.auth.get_user()
    if not user:
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


def _project_membership_exists_clause(project_id: int, picture_model=Picture):
    return exists(
        select(PictureProjectMember.picture_id).where(
            PictureProjectMember.picture_id == picture_model.id,
            PictureProjectMember.project_id == project_id,
        )
    )


def _project_unassigned_clause(picture_model=Picture):
    return ~exists(
        select(PictureProjectMember.picture_id).where(
            PictureProjectMember.picture_id == picture_model.id
        )
    )


def _normalize_set_mode(value: str | None) -> str:
    mode = (value or "union").strip().lower()
    if mode not in {"union", "intersection", "difference", "xor"}:
        raise HTTPException(status_code=400, detail="Invalid set_mode")
    return mode


def _collect_set_filter_ids(
    *,
    set_id_value: int | str | None,
    set_ids_values: list[int | str] | None,
) -> list[int]:
    raw_values: list[int | str] = []
    if set_id_value is not None and str(set_id_value).strip() != "":
        raw_values.append(set_id_value)
    if set_ids_values:
        raw_values.extend(set_ids_values)

    normalized: list[int] = []
    seen: set[int] = set()
    for raw in raw_values:
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            continue
        if parsed <= 0 or parsed in seen:
            continue
        seen.add(parsed)
        normalized.append(parsed)
    return normalized


def _fetch_set_candidate_ids(
    session: Session,
    *,
    set_ids: list[int],
    set_mode: str,
    deleted_only: bool,
) -> set[int]:
    if not set_ids:
        return set()

    rows = session.exec(
        select(PictureSetMember.set_id, PictureSetMember.picture_id)
        .join(Picture, Picture.id == PictureSetMember.picture_id)
        .where(PictureSetMember.set_id.in_(set_ids))
        .where(
            Picture.deleted.is_(True) if deleted_only else Picture.deleted.is_(False)
        )
    ).all()

    members_by_set: dict[int, set[int]] = {sid: set() for sid in set_ids}
    for set_id_row, picture_id_row in rows:
        if picture_id_row is None:
            continue
        members_by_set.setdefault(int(set_id_row), set()).add(int(picture_id_row))

    if set_mode == "intersection":
        intersection: set[int] | None = None
        for sid in set_ids:
            current = members_by_set.get(sid, set())
            if intersection is None:
                intersection = set(current)
            else:
                intersection &= current
        return intersection or set()

    if set_mode == "difference":
        if not set_ids:
            return set()
        first_set = members_by_set.get(set_ids[0], set())
        rest: set[int] = set()
        for sid in set_ids[1:]:
            rest |= members_by_set.get(sid, set())
        return first_set - rest

    if set_mode == "xor":
        xor_union: set[int] = set()
        for sid in set_ids:
            xor_union |= members_by_set.get(sid, set())
        xor_intersection: set[int] | None = None
        for sid in set_ids:
            cur = members_by_set.get(sid, set())
            xor_intersection = (
                set(cur) if xor_intersection is None else xor_intersection & cur
            )
        return xor_union - (xor_intersection or set())

    union_ids: set[int] = set()
    for sid in set_ids:
        union_ids |= members_by_set.get(sid, set())
    return union_ids


def _create_picture_imports(
    server, uploaded_files, dest_folder, progress_callback=None
):
    """
    Given a list of (img_bytes, ext), create Picture objects for new images,
    skipping duplicates based on pixel_sha hash.
    Returns (shas, existing_map, new_pictures)

    Args:
        server: The server instance.
        uploaded_files: List of (img_bytes, ext) tuples.
        dest_folder: Destination folder for images.
        progress_callback: Optional callable invoked after each image is written
            to disk. Receives no arguments. Used for incremental progress tracking.
    """

    def create_sha(img_bytes):
        return ImageUtils.calculate_hash_from_bytes(img_bytes)

    with concurrent.futures.ThreadPoolExecutor() as executor:
        shas = list(
            executor.map(create_sha, (img_bytes for img_bytes, *_ in uploaded_files))
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
        new_pictures = []
        for file_entry, sha in importable:
            img_bytes, ext, original_name = file_entry
            pic_uuid = str(uuid.uuid4()) + ext
            logger.debug(f"Importing picture from uploaded bytes as id={pic_uuid}")
            pic = ImageUtils.create_picture_from_bytes(
                image_root_path=dest_folder,
                image_bytes=img_bytes,
                picture_uuid=pic_uuid,
                pixel_sha=sha,
                original_file_name=original_name,
            )
            new_pictures.append(pic)
            if progress_callback is not None:
                progress_callback()
    else:
        new_pictures = []

    return shas, existing_map, new_pictures


def _normalise_sidecar_stem(filename: str) -> str:
    return os.path.splitext(os.path.basename(filename or ""))[0].strip().lower()


def _normalise_vocab_token(value: str) -> str:
    if not value:
        return ""
    return " ".join(str(value).replace("_", " ").strip().lower().split())


def _parse_sidecar_tags(raw_text: str) -> list[str]:
    text = (raw_text or "").strip()
    if not text or "," not in text:
        return []

    tags_raw = [part.strip() for part in text.replace("\n", ",").split(",")]
    tags_raw = [tag for tag in tags_raw if tag]
    if len(tags_raw) < 2:
        return []

    seen = set()
    parsed = []
    for raw_tag in tags_raw:
        # Lenient sanity check: 1-3 words per tag using space/dash/underscore.
        compact_raw = " ".join(raw_tag.strip().split())
        if not _SIDECAR_TAG_PATTERN.fullmatch(compact_raw):
            continue
        # Preserve sidecar tag semantics (e.g. "1girl") while still
        # normalising separators/spacing for storage.
        candidate = _normalise_vocab_token(compact_raw)
        if not candidate:
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        parsed.append(candidate)
    return parsed


def _enrich_stack_counts(server, pics: list[dict]) -> list[dict]:
    """Add stack_count field to each dict in pics by querying the DB.

    Args:
        server: The application server with vault.db access.
        pics: List of picture dicts to enrich.

    Returns:
        New list with a stack_count key added to every dict.
    """
    if not pics:
        return pics
    picture_ids = [
        int(p.get("id"))
        for p in pics
        if isinstance(p, dict) and p.get("id") is not None
    ]
    if not picture_ids:
        return pics

    def fetch_stack_info(session: Session, ids: list[int]):
        id_stack_rows = session.exec(
            select(Picture.id, Picture.stack_id).where(
                Picture.id.in_(ids),
                Picture.deleted.is_(False),
            )
        ).all()
        stack_ids = sorted(
            {
                int(stack_id)
                for _pic_id, stack_id in id_stack_rows
                if stack_id is not None
            }
        )
        if not stack_ids:
            return id_stack_rows, []
        stack_count_rows = session.exec(
            select(Picture.stack_id, func.count(Picture.id))
            .where(
                Picture.stack_id.in_(stack_ids),
                Picture.deleted.is_(False),
            )
            .group_by(Picture.stack_id)
        ).all()
        return id_stack_rows, stack_count_rows

    id_stack_rows, stack_count_rows = server.vault.db.run_immediate_read_task(
        fetch_stack_info, picture_ids
    )
    stack_id_by_picture_id = {
        int(pic_id): stack_id for pic_id, stack_id in id_stack_rows
    }
    stack_count_by_stack_id = {
        int(stack_id): int(count)
        for stack_id, count in stack_count_rows
        if stack_id is not None
    }
    enriched: list[dict] = []
    for pic in pics:
        if not isinstance(pic, dict):
            enriched.append(pic)
            continue
        picture_id = pic.get("id")
        if picture_id is None:
            enriched.append(pic)
            continue
        numeric_id = int(picture_id)
        stack_id = pic.get("stack_id")
        if stack_id is None:
            stack_id = stack_id_by_picture_id.get(numeric_id)
        stack_count = 0
        if stack_id is not None:
            stack_count = stack_count_by_stack_id.get(int(stack_id), 1)
        enriched.append(
            {
                **pic,
                "stack_id": stack_id,
                "stack_count": stack_count,
            }
        )
    return enriched


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
    stack_leaders_only: bool = False,
    project_id: int | None = None,
):
    def serialize_metadata(pictures):
        result = []
        for pic in pictures:
            d = {
                field: safe_model_dict(pic).get(field)
                for field in metadata_fields
                if field != "tags"
            }
            if "tags" in metadata_fields:
                d["tags"] = [t.tag for t in getattr(pic, "tags", [])]
            if "smart_score" in d:
                d["smartScore"] = d["smart_score"]
            result.append(d)
        return result

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
            comfyui_models = request.query_params.getlist("comfyui_model")
            if comfyui_models:
                query_params["comfyui_models_filter"] = comfyui_models
            query_params.pop("comfyui_model", None)
            comfyui_loras = request.query_params.getlist("comfyui_lora")
            if comfyui_loras:
                query_params["comfyui_loras_filter"] = comfyui_loras
            query_params.pop("comfyui_lora", None)
            tags = request.query_params.getlist("tag")
            if tags:
                query_params["tags_filter"] = tags
            query_params.pop("tag", None)
            rejected_tags = request.query_params.getlist("rejected_tag")
            if rejected_tags:
                query_params["tags_rejected_filter"] = rejected_tags
            query_params.pop("rejected_tag", None)
            confidence_above = request.query_params.getlist("tag_confidence_above")
            if confidence_above:
                query_params["tags_confidence_above_filter"] = confidence_above
            query_params.pop("tag_confidence_above", None)
            confidence_below = request.query_params.getlist("tag_confidence_below")
            if confidence_below:
                query_params["tags_confidence_below_filter"] = confidence_below
            query_params.pop("tag_confidence_below", None)
            set_ids = request.query_params.getlist("set_ids")
            if set_ids:
                query_params["set_ids"] = set_ids
            character_ids_raw = request.query_params.getlist("character_ids")
            if character_ids_raw:
                query_params["character_ids"] = character_ids_raw
            face_filter_param = request.query_params.get("face_filter")
            if face_filter_param in ("with_face", "without_face"):
                query_params["face_filter"] = face_filter_param
        return format, query_params

    def _character_id(value):
        if value == "ALL":
            return None
        if value is not None and value != "" and str(value).isdigit():
            return int(value)
        return value

    format, query_params = parse_request_params()
    if project_id is not None:
        query_params["project_id"] = project_id
    sort = query_params.pop("sort", sort)
    desc_val = query_params.pop("descending", descending)
    descending = (
        desc_val.lower() == "true" if isinstance(desc_val, str) else bool(desc_val)
    )
    offset = int(query_params.pop("offset", offset))
    limit = int(query_params.pop("limit", limit))
    character_id = _character_id(query_params.pop("character_id", None))
    _character_ids_raw = query_params.pop("character_ids", None)
    character_id_list: list[int] = []
    if _character_ids_raw:
        for _v in (
            _character_ids_raw
            if isinstance(_character_ids_raw, list)
            else [_character_ids_raw]
        ):
            try:
                _cid = int(_v)
                if _cid > 0:
                    character_id_list.append(_cid)
            except (TypeError, ValueError):
                logger.warning(
                    "Ignoring invalid character_ids value %r in /pictures request",
                    _v,
                )
        character_id_list = sorted(set(character_id_list))
    character_mode_raw = query_params.pop("character_mode", "union")
    character_mode = (character_mode_raw or "union").strip().lower()
    if character_mode not in {"union", "intersection", "difference", "xor"}:
        character_mode = "union"
    set_id_raw = query_params.pop("set_id", None)
    set_ids_raw = query_params.pop("set_ids", None)
    set_mode_raw = query_params.pop("set_mode", "union")
    base_set_id_raw = query_params.pop("base_set_id", None)
    reference_character_id = query_params.pop("reference_character_id", None)
    min_score_raw = query_params.pop("min_score", None)
    min_score = int(min_score_raw) if min_score_raw is not None else None
    max_score_raw = query_params.pop("max_score", None)
    max_score = int(max_score_raw) if max_score_raw is not None else None
    smart_score_bucket = query_params.pop("smart_score_bucket", None) or None
    resolution_bucket = query_params.pop("resolution_bucket", None) or None
    project_id_raw = query_params.pop("project_id", None)
    file_path_prefix = query_params.pop("file_path_prefix", None) or None
    face_filter = query_params.pop("face_filter", None)
    only_deleted = False
    set_mode = _normalize_set_mode(set_mode_raw)
    set_filter_ids = _collect_set_filter_ids(
        set_id_value=set_id_raw,
        set_ids_values=set_ids_raw if isinstance(set_ids_raw, list) else None,
    )
    if set_mode == "difference" and base_set_id_raw is not None:
        try:
            _base_id = int(base_set_id_raw)
            if _base_id in set_filter_ids:
                set_filter_ids = [_base_id] + [
                    s for s in set_filter_ids if s != _base_id
                ]
        except (TypeError, ValueError):
            logger.warning(
                "Ignoring invalid base_set_id value %r in /pictures request",
                base_set_id_raw,
            )

    def fetch_set_candidate_ids(session: Session):
        return _fetch_set_candidate_ids(
            session,
            set_ids=set_filter_ids,
            set_mode=set_mode,
            deleted_only=only_deleted,
        )

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
        project_id_value: str | None,
        min_score_value: int | None = None,
        max_score_value: int | None = None,
        smart_score_bucket_value: str | None = None,
        resolution_bucket_value: str | None = None,
        tags_filter_value: list[str] | None = None,
        tags_rejected_filter_value: list[str] | None = None,
        tags_confidence_above_filter_value: list[str] | None = None,
        tags_confidence_below_filter_value: list[str] | None = None,
        comfyui_models_filter_value: list[str] | None = None,
        comfyui_loras_filter_value: list[str] | None = None,
        face_filter_value: str | None = None,
    ):
        if deleted_only:
            query = select(Picture.id).where(
                Picture.deleted.is_(True),
            )
        elif character_id_value == "UNASSIGNED":
            assignment_project_id = None
            assignment_unassigned_project = False
            if project_id_value == "UNASSIGNED":
                assignment_unassigned_project = True
            elif project_id_value is not None:
                try:
                    assignment_project_id = int(project_id_value)
                except (TypeError, ValueError):
                    logger.warning(
                        "Invalid project_id_raw value %r for UNASSIGNED assignment scope; treating as global scope",
                        project_id_value,
                    )
            unassigned_conditions = Picture.build_unassigned_conditions(
                enforce_stack_assignment=True,
                assignment_project_id=assignment_project_id,
                assignment_unassigned_project=assignment_unassigned_project,
            )
            query = select(Picture.id).where(
                *unassigned_conditions,
                Picture.deleted.is_(False),
            )
        elif character_id_value is None or character_id_value == "":
            if (
                project_id_value is None
                and not formats
                and min_score_value is None
                and max_score_value is None
                and smart_score_bucket_value is None
                and resolution_bucket_value is None
                and not tags_filter_value
                and not tags_rejected_filter_value
                and not tags_confidence_above_filter_value
                and not tags_confidence_below_filter_value
                and not comfyui_models_filter_value
                and not comfyui_loras_filter_value
                and not face_filter_value
            ):
                return None
            query = select(Picture.id).where(
                Picture.deleted.is_(False),
            )
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

        if project_id_value == "UNASSIGNED":
            query = query.where(_project_unassigned_clause(Picture))
        elif project_id_value is not None:
            try:
                query = query.where(
                    _project_membership_exists_clause(int(project_id_value), Picture)
                )
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid project_id_raw value %r for SMART_SCORE candidate filtering; skipping project filter",
                    project_id_value,
                )

        if formats:
            query = query.where(Picture.format.in_(formats))

        if min_score_value is not None:
            query = query.where(Picture.score >= min_score_value)
        if max_score_value is not None:
            query = query.where(Picture.score <= max_score_value)

        if smart_score_bucket_value == "unscored":
            query = query.where(Picture.smart_score.is_(None))
        elif smart_score_bucket_value == "1-2":
            query = query.where(
                Picture.smart_score.is_not(None), Picture.smart_score < 2.0
            )
        elif smart_score_bucket_value == "2-3":
            query = query.where(Picture.smart_score >= 2.0, Picture.smart_score < 3.0)
        elif smart_score_bucket_value == "3-4":
            query = query.where(Picture.smart_score >= 3.0, Picture.smart_score < 4.0)
        elif smart_score_bucket_value == "4-5":
            query = query.where(Picture.smart_score >= 4.0)

        if resolution_bucket_value == "unknown":
            query = query.where(or_(Picture.width.is_(None), Picture.height.is_(None)))
        elif resolution_bucket_value == "lt1mp":
            query = query.where(
                Picture.width.is_not(None),
                Picture.height.is_not(None),
                Picture.width * Picture.height < 1_000_000,
            )
        elif resolution_bucket_value == "1-4mp":
            query = query.where(
                Picture.width.is_not(None),
                Picture.height.is_not(None),
                Picture.width * Picture.height >= 1_000_000,
                Picture.width * Picture.height < 4_000_000,
            )
        elif resolution_bucket_value == "4-8mp":
            query = query.where(
                Picture.width.is_not(None),
                Picture.height.is_not(None),
                Picture.width * Picture.height >= 4_000_000,
                Picture.width * Picture.height < 8_000_000,
            )
        elif resolution_bucket_value == "8-16mp":
            query = query.where(
                Picture.width.is_not(None),
                Picture.height.is_not(None),
                Picture.width * Picture.height >= 8_000_000,
                Picture.width * Picture.height < 16_000_000,
            )
        elif resolution_bucket_value == "16plus":
            query = query.where(
                Picture.width.is_not(None),
                Picture.height.is_not(None),
                Picture.width * Picture.height >= 16_000_000,
            )

        if tags_filter_value:
            for i, tag in enumerate(tags_filter_value):
                query = query.where(
                    text(
                        f"EXISTS (SELECT 1 FROM tag WHERE tag.picture_id = picture.id AND tag.tag = :ss_tag_filter_{i})"
                    ).bindparams(**{f"ss_tag_filter_{i}": tag})
                )

        if tags_rejected_filter_value:
            for i, tag in enumerate(tags_rejected_filter_value):
                query = query.where(
                    text(
                        f"NOT EXISTS (SELECT 1 FROM tag WHERE tag.picture_id = picture.id AND tag.tag = :ss_rejected_tag_{i})"
                    ).bindparams(**{f"ss_rejected_tag_{i}": tag})
                )

        if tags_confidence_above_filter_value:
            for i, entry in enumerate(tags_confidence_above_filter_value):
                tag, threshold = entry.rsplit(":", 1)
                if float(threshold) <= 0.0:
                    query = query.where(
                        text(
                            f"("
                            f"EXISTS (SELECT 1 FROM tag_prediction WHERE tag_prediction.picture_id = picture.id"
                            f" AND tag_prediction.tag = :ss_ca_tag_{i} AND tag_prediction.confidence >= :ss_ca_thresh_{i})"
                            f" AND NOT EXISTS (SELECT 1 FROM tag WHERE tag.picture_id = picture.id AND tag.tag = :ss_ca_tag_{i})"
                            f") OR ("
                            f"EXISTS (SELECT 1 FROM tag WHERE tag.picture_id = picture.id AND tag.tag = :ss_ca_tag_{i})"
                            f" AND NOT EXISTS (SELECT 1 FROM tag_prediction WHERE tag_prediction.picture_id = picture.id AND tag_prediction.tag = :ss_ca_tag_{i})"
                            f")"
                        ).bindparams(
                            **{
                                f"ss_ca_tag_{i}": tag,
                                f"ss_ca_thresh_{i}": float(threshold),
                            }
                        )
                    )
                else:
                    query = query.where(
                        text(
                            f"EXISTS (SELECT 1 FROM tag_prediction WHERE tag_prediction.picture_id = picture.id"
                            f" AND tag_prediction.tag = :ss_ca_tag_{i} AND tag_prediction.confidence >= :ss_ca_thresh_{i})"
                            f" AND NOT EXISTS (SELECT 1 FROM tag WHERE tag.picture_id = picture.id AND tag.tag = :ss_ca_tag_{i})"
                        ).bindparams(
                            **{
                                f"ss_ca_tag_{i}": tag,
                                f"ss_ca_thresh_{i}": float(threshold),
                            }
                        )
                    )

        if tags_confidence_below_filter_value:
            for i, entry in enumerate(tags_confidence_below_filter_value):
                tag, threshold = entry.rsplit(":", 1)
                query = query.where(
                    text(
                        f"EXISTS (SELECT 1 FROM tag_prediction WHERE tag_prediction.picture_id = picture.id"
                        f" AND tag_prediction.tag = :ss_cb_tag_{i} AND tag_prediction.confidence < :ss_cb_thresh_{i})"
                        f" AND EXISTS (SELECT 1 FROM tag WHERE tag.picture_id = picture.id AND tag.tag = :ss_cb_tag_{i})"
                    ).bindparams(
                        **{f"ss_cb_tag_{i}": tag, f"ss_cb_thresh_{i}": float(threshold)}
                    )
                )

        if comfyui_models_filter_value:
            for i, m in enumerate(comfyui_models_filter_value):
                query = query.where(
                    text(
                        f"EXISTS (SELECT 1 FROM json_each(picture.comfyui_models) WHERE value = :ss_comfyui_model_{i})"
                    ).bindparams(**{f"ss_comfyui_model_{i}": m})
                )

        if comfyui_loras_filter_value:
            for i, m in enumerate(comfyui_loras_filter_value):
                query = query.where(
                    text(
                        f"EXISTS (SELECT 1 FROM json_each(picture.comfyui_loras) WHERE value = :ss_comfyui_lora_{i})"
                    ).bindparams(**{f"ss_comfyui_lora_{i}": m})
                )

        if face_filter_value == "with_face":
            query = query.where(
                text(
                    "EXISTS (SELECT 1 FROM face WHERE face.picture_id = picture.id AND face.face_index != -1)"
                )
            )
        elif face_filter_value == "without_face":
            query = query.where(
                text(
                    "NOT EXISTS (SELECT 1 FROM face WHERE face.picture_id = picture.id AND face.face_index != -1)"
                )
            )

        return list(session.exec(query).all())

    logger.info("Getting pictures with project id = %s", project_id_raw)

    if sort_mech and sort_mech.key == SortMechanism.Keys.CHARACTER_LIKENESS:
        if not reference_character_id:
            raise HTTPException(
                status_code=400,
                detail="reference_character_id is required for CHARACTER_LIKENESS sort",
            )
        candidate_ids = server.vault.db.run_task(
            fetch_smart_score_candidate_ids,
            character_id,
            only_deleted,
            format,
            project_id_raw,
            min_score_value=min_score,
            max_score_value=max_score,
            smart_score_bucket_value=smart_score_bucket,
            resolution_bucket_value=resolution_bucket,
            tags_filter_value=query_params.get("tags_filter"),
            tags_rejected_filter_value=query_params.get("tags_rejected_filter"),
            tags_confidence_above_filter_value=query_params.get(
                "tags_confidence_above_filter"
            ),
            tags_confidence_below_filter_value=query_params.get(
                "tags_confidence_below_filter"
            ),
            comfyui_models_filter_value=query_params.get("comfyui_models_filter"),
            comfyui_loras_filter_value=query_params.get("comfyui_loras_filter"),
            face_filter_value=face_filter,
        )
        if set_filter_ids:
            set_candidate_ids = server.vault.db.run_immediate_read_task(
                fetch_set_candidate_ids
            )
            candidate_ids = (
                set_candidate_ids
                if candidate_ids is None
                else set(candidate_ids) & set_candidate_ids
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
        if stack_leaders_only:
            pics = _deduplicate_by_stack(pics)
            pics = _enrich_stack_counts(server, pics)
        return pics
    if character_id == "UNASSIGNED":
        unassigned_project_id = None
        unassigned_project_only = False
        if project_id_raw == "UNASSIGNED":
            unassigned_project_only = True
        elif project_id_raw is not None:
            try:
                unassigned_project_id = int(project_id_raw)
            except (TypeError, ValueError):
                logger.warning(
                    "Invalid project_id_raw value %r for UNASSIGNED query; skipping project filter",
                    project_id_raw,
                )
        pics = server.vault.db.run_task(
            Picture.find_unassigned,
            sort_mech=sort_mech,
            offset=offset,
            limit=limit,
            format=format,
            metadata_fields=metadata_fields,
            stack_leaders_only=stack_leaders_only,
            min_score=min_score,
            max_score=max_score,
            smart_score_bucket=smart_score_bucket,
            resolution_bucket=resolution_bucket,
            project_id=unassigned_project_id,
            only_unassigned_project=unassigned_project_only,
            tags_filter=query_params.get("tags_filter") or None,
            tags_rejected_filter=query_params.get("tags_rejected_filter") or None,
            tags_confidence_above_filter=query_params.get(
                "tags_confidence_above_filter"
            )
            or None,
            tags_confidence_below_filter=query_params.get(
                "tags_confidence_below_filter"
            )
            or None,
            face_filter=face_filter,
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
            stack_leaders_only=stack_leaders_only,
            min_score=min_score,
            max_score=max_score,
            smart_score_bucket=smart_score_bucket,
            resolution_bucket=resolution_bucket,
            face_filter=face_filter,
            **query_params,
        )
    else:
        if set_filter_ids:
            set_candidate_ids = server.vault.db.run_immediate_read_task(
                fetch_set_candidate_ids
            )
            if not set_candidate_ids:
                return []
            existing_ids = query_params.get("id")
            if existing_ids:
                query_params["id"] = list(set(existing_ids) & set_candidate_ids)
            else:
                query_params["id"] = list(set_candidate_ids)

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

            # When a project filter is also present, restrict to pictures that
            # are members of that project so pictures removed from a project no
            # longer appear in its character grid view.
            if project_id_raw is not None:
                if project_id_raw == "UNASSIGNED":

                    def _get_project_unassigned_ids(session, ids):
                        rows = session.exec(
                            select(Picture.id).where(
                                Picture.id.in_(ids),
                                _project_unassigned_clause(Picture),
                            )
                        ).all()
                        return list(rows)

                    picture_ids = server.vault.db.run_task(
                        _get_project_unassigned_ids, picture_ids
                    )
                else:
                    try:
                        proj_id_int = int(project_id_raw)
                    except (TypeError, ValueError):
                        proj_id_int = None
                    if proj_id_int is not None:

                        def _get_project_member_ids(session, ids, pid):
                            rows = session.exec(
                                select(PictureProjectMember.picture_id).where(
                                    PictureProjectMember.picture_id.in_(ids),
                                    PictureProjectMember.project_id == pid,
                                )
                            ).all()
                            return list(rows)

                        picture_ids = server.vault.db.run_task(
                            _get_project_member_ids, picture_ids, proj_id_int
                        )

            if not picture_ids:
                return []
            query_params["id"] = picture_ids
        elif character_id_list:

            def get_picture_ids_for_characters(session, ids, mode):
                # Fetch picture_ids keyed by character for all chars
                rows = session.exec(
                    select(Face.character_id, Face.picture_id).where(
                        Face.character_id.in_(ids)
                    )
                ).all()
                members_by_char: dict[int, set[int]] = {cid: set() for cid in ids}
                for cid, pid in rows:
                    members_by_char.setdefault(int(cid), set()).add(int(pid))
                if mode == "intersection":
                    result: set[int] | None = None
                    for cid in ids:
                        current = members_by_char.get(cid, set())
                        result = set(current) if result is None else result & current
                    return list(result or set())
                if mode == "difference":
                    first = members_by_char.get(ids[0], set())
                    rest: set[int] = set()
                    for cid in ids[1:]:
                        rest |= members_by_char.get(cid, set())
                    return list(first - rest)
                if mode == "xor":
                    xor_u: set[int] = set()
                    for cid in ids:
                        xor_u |= members_by_char.get(cid, set())
                    xor_i: set[int] | None = None
                    for cid in ids:
                        cur = members_by_char.get(cid, set())
                        xor_i = set(cur) if xor_i is None else xor_i & cur
                    return list(xor_u - (xor_i or set()))
                # union
                union: set[int] = set()
                for cid in ids:
                    union |= members_by_char.get(cid, set())
                return list(union)

            picture_ids = server.vault.db.run_task(
                get_picture_ids_for_characters, character_id_list, character_mode
            )
            if not picture_ids:
                return []

            if project_id_raw is not None:
                if project_id_raw == "UNASSIGNED":

                    def _get_project_unassigned_ids_multi(session, ids):
                        rows = session.exec(
                            select(Picture.id).where(
                                Picture.id.in_(ids),
                                _project_unassigned_clause(Picture),
                            )
                        ).all()
                        return list(rows)

                    picture_ids = server.vault.db.run_task(
                        _get_project_unassigned_ids_multi, picture_ids
                    )
                else:
                    try:
                        proj_id_int = int(project_id_raw)
                    except (TypeError, ValueError):
                        proj_id_int = None
                    if proj_id_int is not None:

                        def _get_project_member_ids_multi(session, ids, pid):
                            rows = session.exec(
                                select(PictureProjectMember.picture_id).where(
                                    PictureProjectMember.picture_id.in_(ids),
                                    PictureProjectMember.project_id == pid,
                                )
                            ).all()
                            return list(rows)

                        picture_ids = server.vault.db.run_task(
                            _get_project_member_ids_multi, picture_ids, proj_id_int
                        )

            if not picture_ids:
                return []
            query_params["id"] = picture_ids
        elif project_id_raw is not None:
            # Project filter only applies when not already filtering by character/set.
            # "UNASSIGNED" means pictures with no project (project_id IS NULL).
            # A numeric value filters to that specific project.
            if project_id_raw == "UNASSIGNED":

                def get_unassigned_project_ids(session):
                    from pixlstash.db_models.picture import Picture as Pic

                    rows = session.exec(
                        select(Pic.id).where(
                            _project_unassigned_clause(Pic),
                            Pic.deleted.is_(False),
                        )
                    ).all()
                    return list(rows)

                project_pic_ids = server.vault.db.run_task(get_unassigned_project_ids)
                if not project_pic_ids:
                    return []
                existing_ids = query_params.get("id")
                if existing_ids:
                    query_params["id"] = list(set(existing_ids) & set(project_pic_ids))
                else:
                    query_params["id"] = project_pic_ids
            else:
                try:
                    query_params["project_id"] = int(project_id_raw)
                except (TypeError, ValueError):
                    # If project_id_raw is not a valid integer, skip applying a project filter.
                    logger.warning(
                        "Invalid project_id_raw value %r; skipping project filter",
                        project_id_raw,
                    )

        pics = server.vault.db.run_task(
            Picture.find,
            sort_mech=sort_mech,
            offset=offset,
            limit=limit,
            select_fields=metadata_fields,
            format=format,
            include_unimported=True,
            stack_leaders_only=stack_leaders_only,
            min_score=min_score,
            max_score=max_score,
            smart_score_bucket=smart_score_bucket,
            resolution_bucket=resolution_bucket,
            file_path_prefix=file_path_prefix,
            face_filter=face_filter,
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
    result = serialize_metadata(pics)
    if sort_mech and sort_mech.key == SortMechanism.Keys.TEXT_CONTENT and result:
        pic_ids = [d["id"] for d in result if d.get("id") is not None]

        def _fetch_text_scores(session, ids):
            rows = session.exec(
                select(Quality.picture_id, Quality.text_score).where(
                    Quality.picture_id.in_(ids),
                    Quality.face_id.is_(None),
                )
            ).all()
            return {pid: ts for pid, ts in rows}

        text_score_map = server.vault.db.run_immediate_read_task(
            _fetch_text_scores, pic_ids
        )
        for d in result:
            pid = d.get("id")
            if pid is not None:
                ts = text_score_map.get(pid)
                d["text_score"] = round(ts, 3) if ts is not None and ts >= 0 else None
    if stack_leaders_only:
        result = _enrich_stack_counts(server, result)
    return result


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
    def get_pictures_sort_mechanisms():
        """Return available sorting mechanisms for pictures."""
        result = SortMechanism.all()
        logger.debug("Returning sort mechanisms: {}".format(result))
        return result

    @router.get(
        "/pictures/plugins",
        summary="List image plugins",
        description="Lists available image plugins and their parameter schemas.",
    )
    def list_picture_plugins():
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

        raw_captions = payload.get("captions")
        captions: list[str] | None = None
        if isinstance(raw_captions, list):
            if len(raw_captions) != len(picture_ids):
                raise HTTPException(
                    status_code=400,
                    detail="captions length must match picture_ids length",
                )
            captions = [str(c or "") for c in raw_captions]

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
                captions,
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
        "/pictures/comfyui_models",
        summary="List distinct ComfyUI model names",
    )
    def get_comfyui_models():
        def fetch(session):
            rows = session.execute(
                text(
                    "SELECT DISTINCT j.value FROM picture p, json_each(p.comfyui_models) j "
                    "WHERE p.comfyui_models IS NOT NULL AND p.comfyui_models != '[]' "
                    "AND p.deleted = 0 ORDER BY j.value"
                )
            ).all()
            return [r[0] for r in rows if r and r[0]]

        return server.vault.db.run_immediate_read_task(fetch)

    @router.get(
        "/pictures/comfyui_loras",
        summary="List distinct ComfyUI LoRA names",
    )
    def get_comfyui_loras():
        def fetch(session):
            rows = session.execute(
                text(
                    "SELECT DISTINCT j.value FROM picture p, json_each(p.comfyui_loras) j "
                    "WHERE p.comfyui_loras IS NOT NULL AND p.comfyui_loras != '[]' "
                    "AND p.deleted = 0 ORDER BY j.value"
                )
            ).all()
            return [r[0] for r in rows if r and r[0]]

        return server.vault.db.run_immediate_read_task(fetch)

    @router.get(
        "/pictures/likeness-groups",
        summary="List computed likeness groups",
        description="Builds groups from likeness edges using filtering options such as character, set, format, and threshold.",
    )
    def get_likeness_groups(
        request: Request,
        threshold: float = 0.0,
        min_group_size: int = 2,
        set_id: int = Query(None),
        set_ids: list[int] = Query(None),
        set_mode: str = Query("union"),
        character_id: str = Query(None),
        project_id: str = Query(None),
        format: list[str] = Query(None),
        tag: list[str] = Query(None),
        rejected_tag: list[str] = Query(None),
        tag_confidence_above: list[str] = Query(None),
        tag_confidence_below: list[str] = Query(None),
        comfyui_model: list[str] = Query(None),
        comfyui_lora: list[str] = Query(None),
        min_score: int = Query(None),
        max_score: int = Query(None),
    ):
        candidate_ids = None
        only_deleted = character_id == "SCRAPHEAP"
        set_filter_ids = _collect_set_filter_ids(
            set_id_value=set_id,
            set_ids_values=set_ids,
        )
        normalized_set_mode = _normalize_set_mode(set_mode)

        if set_filter_ids:
            candidate_ids = server.vault.db.run_immediate_read_task(
                _fetch_set_candidate_ids,
                set_ids=set_filter_ids,
                set_mode=normalized_set_mode,
                deleted_only=only_deleted,
            )
        elif character_id is not None:
            if character_id == "UNASSIGNED":

                def fetch_unassigned_ids(session):
                    query = select(Picture.id)
                    assignment_project_id = None
                    assignment_unassigned_project = False
                    if project_id == "UNASSIGNED":
                        assignment_unassigned_project = True
                    elif project_id is not None:
                        try:
                            assignment_project_id = int(project_id)
                        except (TypeError, ValueError):
                            logger.warning(
                                "Invalid project_id %r for UNASSIGNED stack assignment scope; treating as global scope",
                                project_id,
                            )
                    unassigned_conditions = Picture.build_unassigned_conditions(
                        enforce_stack_assignment=True,
                        assignment_project_id=assignment_project_id,
                        assignment_unassigned_project=assignment_unassigned_project,
                    )
                    query = query.where(
                        *unassigned_conditions,
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

        if project_id is not None:

            def fetch_project_ids(
                session,
                project_id_value: str,
                deleted_only: bool,
            ):
                query = select(Picture.id)
                if project_id_value == "UNASSIGNED":
                    query = query.where(_project_unassigned_clause(Picture))
                else:
                    try:
                        parsed_project_id = int(project_id_value)
                    except (TypeError, ValueError):
                        raise HTTPException(
                            status_code=400,
                            detail="Invalid project_id",
                        )
                    query = query.where(
                        _project_membership_exists_clause(parsed_project_id, Picture)
                    )
                if deleted_only:
                    query = query.where(Picture.deleted.is_(True))
                else:
                    query = query.where(Picture.deleted.is_(False))
                rows = session.exec(query).all()
                return list(rows)

            project_ids = set(
                server.vault.db.run_immediate_read_task(
                    fetch_project_ids,
                    project_id,
                    only_deleted,
                )
            )
            candidate_ids = (
                project_ids if candidate_ids is None else candidate_ids & project_ids
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

        # Post-group filter: if tag/score/comfyui filters are active, keep only
        # groups where at least one member satisfies all the criteria. The full
        # group is still returned — the filter just decides whether the group is
        # shown at all.
        has_extra_filters = any(
            [
                min_score is not None,
                max_score is not None,
                tag,
                rejected_tag,
                tag_confidence_above,
                tag_confidence_below,
                comfyui_model,
                comfyui_lora,
            ]
        )
        if has_extra_filters:

            def fetch_extra_filtered_ids(
                session,
                candidate_ids_list,
                deleted_only: bool,
                min_score_value,
                max_score_value,
                tags_filter_value,
                tags_rejected_filter_value,
                tags_confidence_above_filter_value,
                tags_confidence_below_filter_value,
                comfyui_models_filter_value,
                comfyui_loras_filter_value,
            ):
                query = select(Picture.id).where(Picture.id.in_(candidate_ids_list))
                if deleted_only:
                    query = query.where(Picture.deleted.is_(True))
                else:
                    query = query.where(Picture.deleted.is_(False))
                if min_score_value is not None:
                    query = query.where(Picture.score >= min_score_value)
                if max_score_value is not None:
                    query = query.where(Picture.score <= max_score_value)
                if comfyui_models_filter_value:
                    for i, m in enumerate(comfyui_models_filter_value):
                        query = query.where(
                            text(
                                f"EXISTS (SELECT 1 FROM json_each(picture.comfyui_models) WHERE value = :comfyui_model_{i})"
                            ).bindparams(**{f"comfyui_model_{i}": m})
                        )
                if comfyui_loras_filter_value:
                    for i, m in enumerate(comfyui_loras_filter_value):
                        query = query.where(
                            text(
                                f"EXISTS (SELECT 1 FROM json_each(picture.comfyui_loras) WHERE value = :comfyui_lora_{i})"
                            ).bindparams(**{f"comfyui_lora_{i}": m})
                        )
                if tags_filter_value:
                    for i, t in enumerate(tags_filter_value):
                        query = query.where(
                            text(
                                f"EXISTS (SELECT 1 FROM tag WHERE tag.picture_id = picture.id AND tag.tag = :tag_filter_{i})"
                            ).bindparams(**{f"tag_filter_{i}": t})
                        )
                if tags_rejected_filter_value:
                    for i, t in enumerate(tags_rejected_filter_value):
                        query = query.where(
                            text(
                                f"NOT EXISTS (SELECT 1 FROM tag WHERE tag.picture_id = picture.id AND tag.tag = :rejected_tag_filter_{i})"
                            ).bindparams(**{f"rejected_tag_filter_{i}": t})
                        )
                if tags_confidence_above_filter_value:
                    for i, entry in enumerate(tags_confidence_above_filter_value):
                        t, thresh = entry.rsplit(":", 1)
                        if float(thresh) <= 0.0:
                            query = query.where(
                                text(
                                    f"("
                                    f"EXISTS (SELECT 1 FROM tag_prediction WHERE tag_prediction.picture_id = picture.id"
                                    f" AND tag_prediction.tag = :ca_tag_{i} AND tag_prediction.confidence >= :ca_thresh_{i})"
                                    f" AND NOT EXISTS (SELECT 1 FROM tag WHERE tag.picture_id = picture.id AND tag.tag = :ca_tag_{i})"
                                    f") OR ("
                                    f"EXISTS (SELECT 1 FROM tag WHERE tag.picture_id = picture.id AND tag.tag = :ca_tag_{i})"
                                    f" AND NOT EXISTS (SELECT 1 FROM tag_prediction WHERE tag_prediction.picture_id = picture.id AND tag_prediction.tag = :ca_tag_{i})"
                                    f")"
                                ).bindparams(
                                    **{
                                        f"ca_tag_{i}": t,
                                        f"ca_thresh_{i}": float(thresh),
                                    }
                                )
                            )
                        else:
                            query = query.where(
                                text(
                                    f"EXISTS (SELECT 1 FROM tag_prediction WHERE tag_prediction.picture_id = picture.id"
                                    f" AND tag_prediction.tag = :ca_tag_{i} AND tag_prediction.confidence >= :ca_thresh_{i})"
                                    f" AND NOT EXISTS (SELECT 1 FROM tag WHERE tag.picture_id = picture.id AND tag.tag = :ca_tag_{i})"
                                ).bindparams(
                                    **{
                                        f"ca_tag_{i}": t,
                                        f"ca_thresh_{i}": float(thresh),
                                    }
                                )
                            )
                if tags_confidence_below_filter_value:
                    for i, entry in enumerate(tags_confidence_below_filter_value):
                        t, thresh = entry.rsplit(":", 1)
                        query = query.where(
                            text(
                                f"EXISTS (SELECT 1 FROM tag_prediction WHERE tag_prediction.picture_id = picture.id"
                                f" AND tag_prediction.tag = :cb_tag_{i} AND tag_prediction.confidence < :cb_thresh_{i})"
                                f" AND EXISTS (SELECT 1 FROM tag WHERE tag.picture_id = picture.id AND tag.tag = :cb_tag_{i})"
                            ).bindparams(
                                **{f"cb_tag_{i}": t, f"cb_thresh_{i}": float(thresh)}
                            )
                        )
                return set(session.exec(query).all())

            matching_ids = server.vault.db.run_immediate_read_task(
                fetch_extra_filtered_ids,
                ordered_ids,
                only_deleted,
                min_score,
                max_score,
                tag or None,
                rejected_tag or None,
                tag_confidence_above or None,
                tag_confidence_below or None,
                comfyui_model or None,
                comfyui_lora or None,
            )
            # Determine which group indices have at least one matching picture.
            matching_group_indices = {
                stack_index_map[pid] for pid in matching_ids if pid in stack_index_map
            }
            # Keep all pictures that belong to a kept group.
            ordered_ids = [
                pid
                for pid in ordered_ids
                if stack_index_map.get(pid) in matching_group_indices
            ]
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
    def get_thumbnails(request: Request, payload: dict = Body(...)):
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
                return session.exec(
                    select(Tag.picture_id, Tag.tag).where(
                        Tag.picture_id.in_(ids_int),
                        Tag.tag.is_not(None),
                        func.lower(Tag.tag).in_(penalised_tag_set),
                    )
                ).all()

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
                return session.exec(
                    select(Character.id, Character.name).where(
                        Character.id.in_(character_ids)
                    )
                ).all()

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
    def export_pictures_zip(
        request: Request,
        background_tasks: BackgroundTasks,
        query: str = Query(None),
        set_id: int = Query(None),
        threshold: float = Query(0.0),
        caption_mode: str = Query("description"),
        include_character_name: bool = Query(False),
        use_original_file_names: bool = Query(False),
        resolution: str = Query("original"),
        export_type: str = Query("full"),
        tag_format: str = Query("spaces"),
    ):
        task_id = str(uuid.uuid4())
        server.export_tasks[task_id] = {
            "status": "in_progress",
            "file_path": None,
            "total": 0,
            "processed": 0,
            "filename": None,
        }

        from pixlstash.utils.service.export_utils import (
            ExportUtils as PictureServiceUtils,
        )

        # Gather extra params for the export service
        background_data = {
            "query": query,
            "set_id": set_id,
            "threshold": threshold,
            "caption_mode": caption_mode,
            "include_character_name": include_character_name,
            "use_original_file_names": use_original_file_names,
            "resolution": resolution,
            "export_type": export_type,
            "tag_format": tag_format,
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
    def export_status(task_id: str):
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
    def download_export(task_id: str):
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
    def search_pictures(
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
        set_ids = None
        set_mode = "union"
        project_id = None
        sort = None
        descending = True
        min_score_raw = None
        comfyui_models = []
        comfyui_loras = []
        tags_filter = []
        if request.query_params:
            query_params = dict(request.query_params)
            query = query_params.pop("query", query)
            offset = int(query_params.pop("offset", offset))
            limit = int(query_params.pop("limit", limit))
            character_id = query_params.pop("character_id", None)
            set_id = query_params.pop("set_id", None)
            set_ids = request.query_params.getlist("set_ids")
            set_mode = query_params.pop("set_mode", "union")
            base_set_id_raw = query_params.pop("base_set_id", None)
            project_id = query_params.pop("project_id", None)
            sort = query_params.pop("sort", None)
            desc_val = query_params.pop("descending", descending)
            descending = (
                desc_val.lower() == "true"
                if isinstance(desc_val, str)
                else bool(desc_val)
            )
            format = request.query_params.getlist("format")
            min_score_raw = query_params.pop("min_score", None)
            comfyui_models = request.query_params.getlist("comfyui_model")
            comfyui_loras = request.query_params.getlist("comfyui_lora")
            tags_filter = request.query_params.getlist("tag")
            tags_rejected_filter = request.query_params.getlist("rejected_tag")
        min_score = int(min_score_raw) if min_score_raw is not None else None
        if not query:
            raise HTTPException(
                status_code=400, detail="Query parameter is required for search"
            )

        only_deleted = character_id == "SCRAPHEAP"
        candidate_ids = None
        sort_mech = None
        normalized_set_mode = _normalize_set_mode(set_mode)
        set_filter_ids = _collect_set_filter_ids(
            set_id_value=set_id,
            set_ids_values=set_ids,
        )
        if normalized_set_mode == "difference" and base_set_id_raw is not None:
            try:
                _base_id = int(base_set_id_raw)
                if _base_id in set_filter_ids:
                    set_filter_ids = [_base_id] + [
                        s for s in set_filter_ids if s != _base_id
                    ]
            except (TypeError, ValueError):
                logger.warning(
                    "Ignoring invalid base_set_id value %r in /pictures/search request",
                    base_set_id_raw,
                )

        if sort:
            try:
                sort_mech = SortMechanism.from_string(sort, descending=descending)
            except ValueError as ve:
                logger.error("Invalid sort mechanism for search: %s", ve)
                raise HTTPException(status_code=400, detail=str(ve))

        if set_filter_ids:
            candidate_ids = server.vault.db.run_immediate_read_task(
                _fetch_set_candidate_ids,
                set_ids=set_filter_ids,
                set_mode=normalized_set_mode,
                deleted_only=only_deleted,
            )
        elif character_id is not None:
            if character_id == "UNASSIGNED":

                def fetch_unassigned_ids(session):
                    assignment_project_id = None
                    assignment_unassigned_project = False
                    if project_id == "UNASSIGNED":
                        assignment_unassigned_project = True
                    elif project_id is not None:
                        try:
                            assignment_project_id = int(project_id)
                        except (TypeError, ValueError):
                            logger.warning(
                                "Invalid project_id %r for UNASSIGNED search assignment scope; treating as global scope",
                                project_id,
                            )
                    unassigned_conditions = Picture.build_unassigned_conditions(
                        enforce_stack_assignment=True,
                        assignment_project_id=assignment_project_id,
                        assignment_unassigned_project=assignment_unassigned_project,
                    )
                    query_stmt = select(Picture.id).where(
                        *unassigned_conditions,
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
        else:
            search_character_ids_raw = request.query_params.getlist("character_ids")
            search_character_id_list = []
            for _v in search_character_ids_raw:
                try:
                    _cid = int(_v)
                    if _cid > 0:
                        search_character_id_list.append(_cid)
                except (TypeError, ValueError):
                    logger.warning(
                        "Ignoring invalid character_ids value %r in /pictures/search request",
                        _v,
                    )
            if search_character_id_list:
                search_character_mode_raw = query_params.get("character_mode", "union")
                search_character_mode = (
                    (search_character_mode_raw or "union").strip().lower()
                )
                if search_character_mode not in {
                    "union",
                    "intersection",
                    "difference",
                    "xor",
                }:
                    search_character_mode = "union"

                def fetch_character_ids_by_mode(session, ids, mode):
                    rows = session.exec(
                        select(Face.character_id, Face.picture_id).where(
                            Face.character_id.in_(ids)
                        )
                    ).all()
                    members_by_char: dict[int, set[int]] = {cid: set() for cid in ids}
                    for cid, pid in rows:
                        members_by_char.setdefault(int(cid), set()).add(int(pid))
                    if mode == "intersection":
                        result: set[int] | None = None
                        for cid in ids:
                            current = members_by_char.get(cid, set())
                            result = (
                                set(current) if result is None else result & current
                            )
                        candidate_pids = list(result or set())
                    elif mode == "difference":
                        first = members_by_char.get(ids[0], set())
                        rest_set: set[int] = set()
                        for cid in ids[1:]:
                            rest_set |= members_by_char.get(cid, set())
                        candidate_pids = list(first - rest_set)
                    elif mode == "xor":
                        xor_u: set[int] = set()
                        for cid in ids:
                            xor_u |= members_by_char.get(cid, set())
                        xor_i: set[int] | None = None
                        for cid in ids:
                            cur = members_by_char.get(cid, set())
                            xor_i = set(cur) if xor_i is None else xor_i & cur
                        candidate_pids = list(xor_u - (xor_i or set()))
                    else:
                        union_pids: set[int] = set()
                        for cid in ids:
                            union_pids |= members_by_char.get(cid, set())
                        candidate_pids = list(union_pids)
                    if not candidate_pids:
                        return []
                    rows2 = session.exec(
                        select(Picture.id).where(
                            Picture.id.in_(candidate_pids),
                            Picture.deleted.is_(False),
                        )
                    ).all()
                    return list(rows2)

                candidate_ids = set(
                    server.vault.db.run_immediate_read_task(
                        fetch_character_ids_by_mode,
                        search_character_id_list,
                        search_character_mode,
                    )
                )

        if project_id is not None:

            def fetch_project_ids(
                session,
                project_id_value: str,
                deleted_only: bool,
            ):
                query_stmt = select(Picture.id)
                if project_id_value == "UNASSIGNED":
                    query_stmt = query_stmt.where(_project_unassigned_clause(Picture))
                else:
                    try:
                        parsed_project_id = int(project_id_value)
                    except (TypeError, ValueError):
                        raise HTTPException(
                            status_code=400,
                            detail="Invalid project_id",
                        )
                    query_stmt = query_stmt.where(
                        _project_membership_exists_clause(parsed_project_id, Picture)
                    )
                if deleted_only:
                    query_stmt = query_stmt.where(Picture.deleted.is_(True))
                else:
                    query_stmt = query_stmt.where(Picture.deleted.is_(False))
                return list(session.exec(query_stmt).all())

            project_candidate_ids = set(
                server.vault.db.run_immediate_read_task(
                    fetch_project_ids,
                    project_id,
                    only_deleted,
                )
            )
            candidate_ids = (
                project_candidate_ids
                if candidate_ids is None
                else candidate_ids & project_candidate_ids
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
                min_score=min_score,
                comfyui_models_filter=comfyui_models or None,
                comfyui_loras_filter=comfyui_loras or None,
                tags_filter=tags_filter or None,
                tags_rejected_filter=tags_rejected_filter or None,
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
                min_score=min_score,
                comfyui_models_filter=comfyui_models or None,
                comfyui_loras_filter=comfyui_loras or None,
                tags_filter=tags_filter or None,
                tags_rejected_filter=tags_rejected_filter or None,
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
        file: list[UploadFile] = File(None),
        project_id: int | None = Form(None),
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
        uploaded_file_stems: list[str] = []
        sidecar_text_by_stem: dict[str, str] = {}
        allowed_media_exts = {
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".gif",
            ".bmp",
            ".tiff",
            ".tif",
            ".heic",
            ".heif",
            ".avif",
            ".mp4",
            ".webm",
            ".mov",
            ".avi",
            ".mkv",
        }
        allowed_caption_exts = {".txt"}
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
                                if (
                                    inner_ext not in allowed_media_exts
                                    and inner_ext not in allowed_caption_exts
                                ):
                                    continue
                                with zip_file.open(info) as handle:
                                    data = handle.read()
                                if not data:
                                    continue
                                base_name = os.path.basename(info.filename)
                                stem = _normalise_sidecar_stem(base_name)
                                if inner_ext in allowed_caption_exts:
                                    sidecar_text_by_stem.setdefault(
                                        stem,
                                        data.decode("utf-8", errors="ignore"),
                                    )
                                    continue
                                uploaded_files.append((data, inner_ext, base_name))
                                uploaded_file_stems.append(stem)
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
                    if ext in allowed_caption_exts:
                        stem = _normalise_sidecar_stem(upload.filename)
                        sidecar_text_by_stem.setdefault(
                            stem,
                            contents.decode("utf-8", errors="ignore"),
                        )
                        continue
                    if ext not in allowed_media_exts:
                        logger.warning(
                            "Skipping file with unsupported extension: %s",
                            upload.filename,
                        )
                        continue
                    uploaded_files.append((contents, ext, upload.filename))
                    uploaded_file_stems.append(_normalise_sidecar_stem(upload.filename))
        else:
            logger.error("No files provided for import")
            raise HTTPException(status_code=400, detail="No image provided")

        if not uploaded_files:
            logger.error("No valid media files found for import")
            raise HTTPException(
                status_code=400,
                detail="No valid media files found for import",
            )

        total_import_bytes_log = sum(len(data) for data, *_ in uploaded_files)
        logger.info(
            "Import request received: files=%d, sidecar_txt=%d, project_id=%s, total_bytes=%d",
            len(uploaded_files),
            len(sidecar_text_by_stem),
            project_id,
            total_import_bytes_log,
        )

        sidecar_tags_by_stem: dict[str, list[str]] = {}
        if sidecar_text_by_stem:
            media_stem_set = set(uploaded_file_stems)
            for stem, raw_text in sidecar_text_by_stem.items():
                # Only consume caption sidecars that have a corresponding media file.
                if stem not in media_stem_set:
                    continue
                parsed_tags = _parse_sidecar_tags(raw_text)
                if parsed_tags:
                    sidecar_tags_by_stem[stem] = parsed_tags

        total_import_bytes = sum(len(data) for data, *_ in uploaded_files)
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
        now_ms = int(time.time() * 1000)
        server.import_tasks[task_id] = {
            "status": "in_progress",
            "stage": "queued",
            "total": len(uploaded_files),
            "processed": 0,
            "results": None,
            "error": None,
            "created_epoch_ms": now_ms,
            "last_update_epoch_ms": now_ms,
            "last_poll_log_epoch_ms": 0,
        }

        logger.info(
            "Import task queued: task_id=%s total=%d project_id=%s",
            task_id,
            len(uploaded_files),
            project_id,
        )

        def run_import_task(server):
            try:
                task = server.import_tasks[task_id]

                def _mark_stage(stage: str, **extra):
                    task["stage"] = stage
                    task["last_update_epoch_ms"] = int(time.time() * 1000)
                    task.update(extra)
                    logger.info(
                        "Import task stage: task_id=%s stage=%s processed=%d/%d",
                        task_id,
                        stage,
                        int(task.get("processed") or 0),
                        int(task.get("total") or 0),
                    )

                _mark_stage("hash_and_write")

                def _on_picture_written():
                    task["processed"] = task.get("processed", 0) + 1
                    task["last_update_epoch_ms"] = int(time.time() * 1000)

                shas, existing_map, new_pictures = _create_picture_imports(
                    server,
                    uploaded_files,
                    dest_folder,
                    progress_callback=_on_picture_written,
                )

                # Duplicates are instantly "processed" — credit them now so that
                # the progress bar stays accurate even when most files are dupes.
                duplicate_count_initial = sum(1 for sha in shas if sha in existing_map)
                task["processed"] = len(new_pictures) + duplicate_count_initial
                task["last_update_epoch_ms"] = int(time.time() * 1000)

                _mark_stage(
                    "deduplicated",
                    duplicate_count_initial=duplicate_count_initial,
                    new_count=len(new_pictures),
                )

                logger.debug(
                    f"Importing {len(new_pictures)} new pictures out of {len(uploaded_files)} uploaded."
                )

                if new_pictures:
                    _mark_stage("persisting_new_pictures")

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

                _mark_stage("building_results")
                results = []
                duplicate_count = 0
                index = 0
                picture_id_sidecar_tags: dict[int, set[str]] = defaultdict(set)
                duplicate_picture_id_set: set[int] = set()
                for stem, _, sha in zip(uploaded_file_stems, uploaded_files, shas):
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
                        if pic.id is not None:
                            duplicate_picture_id_set.add(pic.id)
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

                    if (
                        pic.id is not None
                        and stem in sidecar_tags_by_stem
                        and sidecar_tags_by_stem[stem]
                    ):
                        picture_id_sidecar_tags[pic.id].update(
                            sidecar_tags_by_stem[stem]
                        )

                if duplicate_count:
                    logger.warning(
                        "Import completed with %d duplicate(s) out of %d file(s).",
                        duplicate_count,
                        len(uploaded_files),
                    )
                server.import_tasks[task_id]["results"] = results
                server.import_tasks[task_id]["processed"] = len(uploaded_files)
                server.import_tasks[task_id]["last_update_epoch_ms"] = int(
                    time.time() * 1000
                )
                # Only apply import context to pictures that were actually
                # touched by this request (one results row per uploaded file).
                all_imported_ids = list(
                    dict.fromkeys(
                        entry.get("picture_id")
                        for entry in results
                        if entry.get("picture_id") is not None
                    )
                )

                if picture_id_sidecar_tags:
                    _mark_stage("applying_sidecar_tags")

                    def apply_sidecar_tags(
                        session,
                        mapping: dict[int, set[str]],
                        replace_ids: set[int],
                    ):
                        if not mapping:
                            return []
                        pics = session.exec(
                            select(Picture)
                            .where(Picture.id.in_(list(mapping.keys())))
                            .where(Picture.deleted.is_(False))
                        ).all()
                        changed_ids = []
                        for pic in pics:
                            tag_values = mapping.get(pic.id) or set()
                            if not tag_values:
                                continue
                            existing_values = {
                                (row[0] if isinstance(row, tuple) else row or "")
                                .strip()
                                .lower()
                                for row in session.exec(
                                    select(Tag.tag).where(Tag.picture_id == pic.id)
                                ).all()
                            }
                            changed = False
                            if pic.id in replace_ids:
                                # For duplicate imports with sidecar captions,
                                # replace old tags with sidecar-provided tags.
                                session.exec(
                                    delete(Tag).where(Tag.picture_id == pic.id)
                                )
                                for tag_value in sorted(tag_values):
                                    session.add(Tag(tag=tag_value, picture_id=pic.id))
                                changed = True
                            else:
                                if TAG_EMPTY_SENTINEL in existing_values:
                                    session.exec(
                                        delete(Tag).where(
                                            Tag.picture_id == pic.id,
                                            Tag.tag == TAG_EMPTY_SENTINEL,
                                        )
                                    )
                                    changed = True
                                for tag_value in sorted(tag_values):
                                    if tag_value in existing_values:
                                        continue
                                    session.add(Tag(tag=tag_value, picture_id=pic.id))
                                    changed = True
                            if changed:
                                changed_ids.append(pic.id)
                        session.commit()
                        return changed_ids

                    tagged_ids = server.vault.db.run_task(
                        apply_sidecar_tags,
                        picture_id_sidecar_tags,
                        duplicate_picture_id_set,
                    )
                    if tagged_ids:
                        server.vault.notify(EventType.CHANGED_TAGS)

                if all_imported_ids:
                    _mark_stage("finalizing_import_context")
                    # Queue face extraction asynchronously — do not block on it.
                    for pic in new_pictures:
                        server.vault.get_worker_future(
                            TaskType.FACE_EXTRACTION, Picture, pic.id, "faces"
                        )

                    def apply_import_context(
                        session,
                        ids: list[int],
                        project_id_value: int | None,
                    ):
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
                            if project_id_value is not None:
                                member = session.exec(
                                    select(PictureProjectMember).where(
                                        PictureProjectMember.picture_id == pic.id,
                                        PictureProjectMember.project_id
                                        == project_id_value,
                                    )
                                ).first()
                                if member is None:
                                    session.add(
                                        PictureProjectMember(
                                            picture_id=pic.id,
                                            project_id=project_id_value,
                                        )
                                    )
                                pic.project_id = project_id_value
                            session.add(pic)
                            updated.append(pic.id)
                        session.commit()
                        return updated

                    imported_ids = server.vault.db.run_task(
                        apply_import_context,
                        all_imported_ids,
                        project_id,
                    )
                    server.import_tasks[task_id]["status"] = "completed"
                    server.import_tasks[task_id]["stage"] = "completed"
                    server.import_tasks[task_id]["last_update_epoch_ms"] = int(
                        time.time() * 1000
                    )
                    server.vault.notify(EventType.CHANGED_PICTURES)
                    if imported_ids:
                        server.vault.notify(
                            EventType.PICTURE_IMPORTED,
                            imported_ids,
                        )
                else:
                    server.import_tasks[task_id]["status"] = "completed"
                    server.import_tasks[task_id]["stage"] = "completed"
                    server.import_tasks[task_id]["last_update_epoch_ms"] = int(
                        time.time() * 1000
                    )
                    server.vault.notify(EventType.CHANGED_PICTURES)
                logger.info("Import task completed: task_id=%s", task_id)
            except Exception as exc:
                server.import_tasks[task_id]["status"] = "failed"
                server.import_tasks[task_id]["stage"] = "failed"
                server.import_tasks[task_id]["error"] = str(exc)
                server.import_tasks[task_id]["last_update_epoch_ms"] = int(
                    time.time() * 1000
                )
                logger.error(f"Import task {task_id} failed: {exc}")

        async def run_import_task_async(server):
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, run_import_task, server)

        # Schedule as an independent asyncio Task, detached from the ASGI
        # request lifecycle.  Using BackgroundTasks would tie the task to the
        # response, preventing uvicorn's h11 handler from accepting the next
        # request on the keep-alive connection until the full 0.7-1 s SHA-
        # hashing pass completes — causing TCP back-pressure that stalls the
        # browser's upload for large multi-batch imports.
        asyncio.create_task(run_import_task_async(server))
        return {"task_id": task_id}

    @router.get(
        "/pictures/import/status",
        summary="Get import job status",
        description="Returns progress and result information for a previously started import task.",
    )
    def import_status(task_id: str):
        task = server.import_tasks.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        now_ms = int(time.time() * 1000)
        last_poll_log_epoch_ms = int(task.get("last_poll_log_epoch_ms") or 0)
        if (
            task.get("status") == "in_progress"
            and now_ms - last_poll_log_epoch_ms >= 10_000
        ):
            task["last_poll_log_epoch_ms"] = now_ms
            created_ms = int(task.get("created_epoch_ms") or now_ms)
            elapsed_s = max(0.0, (now_ms - created_ms) / 1000.0)
            logger.info(
                "Import task heartbeat: task_id=%s stage=%s processed=%d/%d elapsed=%.1fs",
                task_id,
                task.get("stage", "unknown"),
                int(task.get("processed") or 0),
                int(task.get("total") or 0),
                elapsed_s,
            )

        total = task.get("total") or 0
        processed = task.get("processed") or 0
        progress = (processed / total * 100.0) if total else 0.0

        payload = {
            "status": task["status"],
            "stage": task.get("stage", "unknown"),
            "total": total,
            "processed": processed,
            "progress": progress,
        }
        if task["status"] == "completed":
            payload["results"] = task.get("results") or []
        if task["status"] == "failed":
            payload["error"] = task.get("error")
        return payload

    @router.patch(
        "/pictures/project",
        summary="Set project for pictures",
        description="Assigns, removes, or clears project association for a batch of pictures.",
    )
    def set_project_for_pictures(payload: dict = Body(...)):
        picture_ids_raw = payload.get("picture_ids")
        if not isinstance(picture_ids_raw, list):
            raise HTTPException(status_code=400, detail="picture_ids must be a list")

        try:
            picture_ids = sorted(
                {
                    int(pid)
                    for pid in picture_ids_raw
                    if pid is not None and int(pid) > 0
                }
            )
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail="picture_ids must contain valid positive integers",
            ) from exc

        if not picture_ids:
            raise HTTPException(
                status_code=400,
                detail="At least one valid picture id is required",
            )

        project_id_raw = payload.get("project_id", None)
        if project_id_raw is None:
            project_id_value = None
        else:
            try:
                project_id_value = int(project_id_raw)
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail="project_id must be an integer or null",
                ) from exc

        mode_raw = payload.get("mode", "set")
        mode = str(mode_raw).strip().lower()
        if mode not in {"set", "add", "remove"}:
            raise HTTPException(
                status_code=400,
                detail="mode must be one of: set, add, remove",
            )
        if mode in {"add", "remove"} and project_id_value is None:
            raise HTTPException(
                status_code=400,
                detail="project_id is required when mode is add or remove",
            )

        def update_picture_projects(
            session: Session,
            ids: list[int],
            project_id_target: int | None,
            update_mode: str,
        ):
            if project_id_target is not None:
                project = session.get(Project, project_id_target)
                if project is None:
                    raise HTTPException(status_code=404, detail="Project not found")

            pics = session.exec(
                select(Picture)
                .where(Picture.id.in_(ids))
                .where(Picture.deleted.is_(False))
            ).all()
            updated_ids: list[int] = []
            found_ids: set[int] = set()
            for pic in pics:
                if pic.id is None:
                    continue
                found_ids.add(int(pic.id))
                changed = False
                if update_mode == "set" and project_id_target is None:
                    existing_memberships = session.exec(
                        select(PictureProjectMember).where(
                            PictureProjectMember.picture_id == int(pic.id)
                        )
                    ).all()
                    if existing_memberships:
                        for membership in existing_memberships:
                            session.delete(membership)
                        changed = True
                    if pic.project_id is not None:
                        pic.project_id = None
                        session.add(pic)
                        changed = True
                elif update_mode == "remove" and project_id_target is not None:
                    existing_memberships = session.exec(
                        select(PictureProjectMember).where(
                            PictureProjectMember.picture_id == int(pic.id),
                            PictureProjectMember.project_id == project_id_target,
                        )
                    ).all()
                    if existing_memberships:
                        for membership in existing_memberships:
                            session.delete(membership)
                        changed = True
                    if pic.project_id == project_id_target:
                        fallback_project_id = session.exec(
                            select(PictureProjectMember.project_id)
                            .where(
                                PictureProjectMember.picture_id == int(pic.id),
                                PictureProjectMember.project_id != project_id_target,
                            )
                            .order_by(PictureProjectMember.project_id.asc())
                        ).first()
                        pic.project_id = (
                            int(fallback_project_id)
                            if fallback_project_id is not None
                            else None
                        )
                        session.add(pic)
                        changed = True
                else:
                    member = session.exec(
                        select(PictureProjectMember).where(
                            PictureProjectMember.picture_id == int(pic.id),
                            PictureProjectMember.project_id == project_id_target,
                        )
                    ).first()
                    if member is None:
                        session.add(
                            PictureProjectMember(
                                picture_id=int(pic.id),
                                project_id=project_id_target,
                            )
                        )
                        changed = True
                    if pic.project_id != project_id_target:
                        pic.project_id = project_id_target
                        session.add(pic)
                        changed = True

                if changed:
                    updated_ids.append(int(pic.id))
            if updated_ids:
                session.commit()
            missing_ids = [pid for pid in ids if pid not in found_ids]
            return updated_ids, missing_ids

        updated_ids, missing_ids = server.vault.db.run_task(
            update_picture_projects,
            picture_ids,
            project_id_value,
            mode,
            priority=DBPriority.IMMEDIATE,
        )

        if updated_ids:
            server.vault.notify(EventType.CHANGED_PICTURES)

        return {
            "status": "success",
            "project_id": project_id_value,
            "mode": mode,
            "updated_ids": updated_ids,
            "updated_count": len(updated_ids),
            "missing_ids": missing_ids,
        }

    @router.post(
        "/pictures/apply-scores",
        summary="Batch apply manual scores",
        description="Applies 0-5 manual scores to multiple pictures in one request while optionally enforcing only-unscored updates.",
    )
    def apply_scores_for_pictures(payload: dict = Body(...)):
        scores_payload = payload.get("scores")
        if not isinstance(scores_payload, dict) or not scores_payload:
            raise HTTPException(
                status_code=400,
                detail="scores must be a non-empty object mapping picture ids to integer scores",
            )

        only_unscored_raw = payload.get("only_unscored", True)
        if not isinstance(only_unscored_raw, bool):
            raise HTTPException(
                status_code=400,
                detail="only_unscored must be a boolean",
            )
        only_unscored = bool(only_unscored_raw)

        parsed_scores: dict[int, int] = {}
        for raw_picture_id, raw_score in scores_payload.items():
            try:
                picture_id = int(raw_picture_id)
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail="scores keys must be valid positive integer picture ids",
                ) from exc
            if picture_id <= 0:
                raise HTTPException(
                    status_code=400,
                    detail="scores keys must be valid positive integer picture ids",
                )

            try:
                score_value = int(raw_score)
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail="scores values must be integers in range 0..5",
                ) from exc
            if score_value < 0 or score_value > 5:
                raise HTTPException(
                    status_code=400,
                    detail="scores values must be integers in range 0..5",
                )

            parsed_scores[picture_id] = score_value

        ordered_picture_ids = sorted(parsed_scores.keys())

        def _apply_scores_batch(
            session: Session,
            picture_ids: list[int],
            picture_scores: dict[int, int],
            apply_only_unscored: bool,
        ):
            # Read only id+score to avoid loading heavy blob columns on Picture.
            # Loading full ORM rows here can be very expensive for large batches.
            score_rows = session.exec(
                select(Picture.id, Picture.score)
                .where(Picture.id.in_(picture_ids))
                .where(Picture.deleted.is_(False))
            ).all()

            found_ids: set[int] = set()
            updated_ids: list[int] = []
            skipped_ids: list[int] = []
            reset_triggered = False
            score_updates: dict[int, int] = {}

            for row in score_rows:
                pic_id_raw, old_score = row
                if pic_id_raw is None:
                    continue
                pic_id = int(pic_id_raw)
                found_ids.add(pic_id)

                if apply_only_unscored and old_score is not None:
                    skipped_ids.append(pic_id)
                    continue

                new_score = picture_scores[pic_id]
                if old_score == new_score:
                    continue

                if _score_anchor_membership_changed(old_score, new_score):
                    reset_triggered = True

                score_updates[pic_id] = new_score
                updated_ids.append(pic_id)

            missing_ids = [pid for pid in picture_ids if pid not in found_ids]

            if score_updates:
                session.exec(
                    update(Picture)
                    .where(Picture.id.in_(score_updates.keys()))
                    .values(
                        score=case(
                            score_updates,
                            value=Picture.id,
                            else_=Picture.score,
                        )
                    )
                )

            if reset_triggered:
                session.exec(
                    update(Picture)
                    .where(Picture.smart_score.is_not(None))
                    .values(smart_score=None)
                )

            if updated_ids or reset_triggered:
                session.commit()

            return (
                sorted(updated_ids),
                sorted(skipped_ids),
                sorted(missing_ids),
                reset_triggered,
            )

        updated_ids, skipped_ids, missing_ids, reset_triggered = (
            server.vault.db.run_task(
                _apply_scores_batch,
                ordered_picture_ids,
                parsed_scores,
                only_unscored,
                priority=DBPriority.IMMEDIATE,
            )
        )

        if updated_ids or reset_triggered:
            server.vault.notify(EventType.CHANGED_PICTURES)

        return {
            "status": "success",
            "only_unscored": only_unscored,
            "updated_ids": updated_ids,
            "updated_count": len(updated_ids),
            "skipped_ids": skipped_ids,
            "skipped_count": len(skipped_ids),
            "missing_ids": missing_ids,
            "missing_count": len(missing_ids),
            "reset_triggered": bool(reset_triggered),
        }

    @router.get(
        "/pictures/{id}.{ext}",
        summary="Get original picture file",
        description="Streams the original media file for a picture id when the requested extension matches the stored format.",
    )
    def get_picture(request: Request, id: str, ext: str):
        if not isinstance(id, str):
            logger.error(f"Invalid id type: {type(id)} value: {id}")
            raise HTTPException(status_code=400, detail="Invalid picture id type")

        if not ext or not isinstance(ext, str):
            logger.error(f"Invalid extension type: {type(ext)} value: {ext}")
            raise HTTPException(status_code=400, detail="Invalid picture extension")
        id = int(id)

        pics = server.vault.db.run_immediate_read_task(
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

        fmt_lower = pic.format.lower()

        # Browsers (Chrome, Firefox) cannot display HEIC/HEIF natively.
        # Transcode to JPEG on-the-fly so the overlay image loads correctly.
        if fmt_lower in ("heic", "heif"):
            try:
                pil_img = Image.open(file_path)
                buf = BytesIO()
                pil_img.convert("RGB").save(buf, format="JPEG", quality=92)
                buf.seek(0)
                jpeg_bytes = buf.read()
            except Exception as exc:
                logger.error(
                    "Failed to transcode HEIF to JPEG for picture id=%s: %s",
                    pic.id,
                    exc,
                )
                raise HTTPException(
                    status_code=500,
                    detail="Failed to transcode HEIF image",
                )
            response = Response(
                content=jpeg_bytes,
                media_type="image/jpeg",
            )
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
            return response

        media_type = MEDIA_TYPE_BY_FORMAT.get(fmt_lower)
        response = FileResponse(file_path, media_type=media_type)
        try:
            stat = os.stat(file_path)
            etag = f'W/"{stat.st_size}-{int(stat.st_mtime)}"'
            response.headers["ETag"] = etag
            response.headers["Last-Modified"] = formatdate(stat.st_mtime, usegmt=True)
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        except OSError:
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        if pic.original_file_name:
            # Suggest the original filename when using "Save image as" in the browser.
            # Using 'inline' keeps the image rendering in-page while still providing
            # the filename hint — no URL change needed.
            safe_name = pic.original_file_name.replace('"', "")
            response.headers["Content-Disposition"] = f'inline; filename="{safe_name}"'
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
    def get_picture_metadata(
        request: Request,
        id: str,
        smart_score: bool = Query(False),
    ):
        metadata_fields = Picture.metadata_fields()
        pics = server.vault.db.run_immediate_read_task(
            Picture.find, id=id, select_fields=metadata_fields, include_deleted=True
        )
        if not pics:
            logger.error(f"Picture not found for id={id}")
            raise HTTPException(status_code=404, detail="Picture not found")
        pic = pics[0]

        def fetch_image_only_tags(session: Session, pic_id: int):
            return session.exec(select(Tag).where(Tag.picture_id == pic_id)).all()

        pic_tags = server.vault.db.run_immediate_read_task(
            fetch_image_only_tags, pic.id
        )
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
    def create_picture_face(id: str, payload: dict = Body(...)):
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
    def delete_picture_face(id: str, index: int):
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
    def get_picture_character_likeness(
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
    def get_picture_field(id: str, field: str):
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

        picture_id = pic.id
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
                tag_values = [
                    tag if isinstance(tag, str) else str(tag)
                    for tag in value
                    if tag is not None
                ]
                if tag_values:
                    pic_id = pic.id

                    def _replace_tags(
                        session: Session,
                        pid: int,
                        new_tags: list[str],
                    ) -> None:
                        session.exec(delete(Tag).where(Tag.picture_id == pid))
                        session.add_all([Tag(picture_id=pid, tag=t) for t in new_tags])
                        session.commit()

                    server.vault.db.run_task(
                        _replace_tags,
                        pic_id,
                        tag_values,
                        priority=DBPriority.IMMEDIATE,
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
            old_score = pic.score if "score" in updated_fields else None

            def apply_picture_updates(session: Session, picture_id: int, fields: dict):
                pic_db = session.get(Picture, picture_id)
                if pic_db is None:
                    raise KeyError("Picture not found")
                for field_name, field_value in fields.items():
                    setattr(pic_db, field_name, field_value)
                session.add(pic_db)
                session.commit()
                session.refresh(pic_db)
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
            if "score" in updated_fields:
                new_score = updated_fields["score"]

                if _score_anchor_membership_changed(old_score, new_score):

                    def _reset_smart_scores(session: Session) -> None:
                        session.exec(update(Picture).values(smart_score=None))
                        session.commit()

                    server.vault.db.run_task(
                        _reset_smart_scores, priority=DBPriority.LOW
                    )
            server.vault.notify(EventType.CHANGED_PICTURES, [picture_id])

        # Write back description to caption sidecar when enabled.
        sync_picture_sidecar(server, picture_id)

        return {"status": "success", "picture": safe_model_dict(pic)}

    @router.post(
        "/pictures/scrapheap/restore",
        summary="Restore deleted pictures",
        description="Restores deleted pictures from scrapheap, either all deleted pictures or a provided picture id subset.",
    )
    def restore_scrapheap(payload: dict | None = Body(None)):
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
            query = select(Picture).where(
                Picture.deleted.is_(True),
                Picture.import_excluded.is_(False),
            )
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
    def delete_scrapheap_selection(
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
            query = select(
                Picture.id, Picture.file_path, Picture.reference_folder_id
            ).where(
                Picture.deleted.is_(True),
                Picture.import_excluded.is_(False),
            )
            if ids is not None:
                query = query.where(Picture.id.in_(ids))
            return session.exec(query).all()

        rows = server.vault.db.run_task(
            fetch_deleted, ids, priority=DBPriority.IMMEDIATE
        )
        if not rows:
            return {"status": "success", "deleted_count": 0}

        # Find reference folders where files must not be deleted; for those
        # pictures keep the DB row as a scan sentinel (import_excluded=True) so
        # the file is never re-imported on the next scan pass.
        def fetch_no_delete_folder_ids(session: Session) -> set[int]:
            result = session.exec(
                select(ReferenceFolder.id).where(
                    ReferenceFolder.allow_delete_file.is_(False),
                )
            ).all()
            return {r for r in result if r is not None}

        no_delete_folder_ids: set[int] = server.vault.db.run_task(
            fetch_no_delete_folder_ids, priority=DBPriority.IMMEDIATE
        )

        full_delete_ids: list[int] = []
        full_delete_file_paths: list[str] = []
        sentinel_ids: list[int] = []

        for row in rows:
            pic_id, file_path, ref_folder_id = row[0], row[1], row[2]
            if ref_folder_id is not None and ref_folder_id in no_delete_folder_ids:
                # Source file is protected — keep the DB row as a scan sentinel.
                if pic_id is not None:
                    sentinel_ids.append(pic_id)
            else:
                if pic_id is not None:
                    full_delete_ids.append(pic_id)
                if file_path:
                    full_delete_file_paths.append(file_path)

        if sentinel_ids:

            def mark_sentinels(session: Session, ids: list[int]) -> None:
                session.exec(
                    update(Picture)
                    .where(Picture.id.in_(ids))
                    .values(import_excluded=True)
                )
                session.commit()

            server.vault.db.run_task(
                mark_sentinels, sentinel_ids, priority=DBPriority.IMMEDIATE
            )
            logger.debug(
                "Scrapheap flush: kept %d picture(s) as import sentinels "
                "(allow_delete_file=False on reference folder).",
                len(sentinel_ids),
            )

        picture_ids = full_delete_ids
        file_paths = full_delete_file_paths

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
        return {
            "status": "success",
            "deleted_count": deleted_count + len(sentinel_ids),
        }

    @router.delete(
        "/pictures/{id}",
        summary="Move picture to scrapheap",
        description="Soft-deletes a picture by marking it deleted, making it appear in scrapheap views.",
    )
    def delete_picture(id: str):
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
    def list_pictures(
        request: Request,
        sort: str = Query(None),
        descending: bool = Query(True),
        offset: int = Query(0),
        limit: int = Query(sys.maxsize),
        fields: str = Query(None),
        project_id: str | None = Query(
            None, description="Filter by project id or 'UNASSIGNED'"
        ),
    ):
        if fields == "grid":
            metadata_fields = list(Picture.grid_fields())
        elif fields:
            metadata_fields = [f.strip() for f in fields.split(",") if f.strip()]
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
            stack_leaders_only=(fields == "grid"),
            project_id=project_id,
        )

    @router.post(
        "/pictures/{id}/open-location",
        summary="Open picture location",
        description="Opens the containing folder of a reference picture in the OS file manager.",
    )
    def open_picture_location(id: str):
        try:
            pic_id = int(id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid picture id")

        def get_path(session: Session):
            pic = session.get(Picture, pic_id)
            return pic.file_path if pic else None

        file_path = server.vault.db.run_immediate_read_task(get_path)
        if not file_path:
            raise HTTPException(status_code=404, detail="Picture not found")

        folder = os.path.dirname(file_path)
        if not os.path.isdir(folder):
            raise HTTPException(status_code=404, detail="Location not found on disk")

        try:
            if sys.platform.startswith("win"):
                os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.run(["open", folder], check=False)
            else:
                subprocess.run(["xdg-open", folder], check=False)
        except Exception as exc:
            logger.warning("Failed to open folder %s: %s", folder, exc)
            raise HTTPException(status_code=500, detail="Failed to open location")

        return {"status": "ok"}

    @router.get(
        "/pictures/stats",
        summary="Get picture statistics",
        description="Returns tag statistics for the current view filter, cached for 60 seconds.",
    )
    def get_picture_stats(request: Request):
        cache_key = str(sorted(request.query_params.multi_items()))
        now = time.monotonic()
        for expired_key in [
            k for k, (ts, _) in list(_stats_cache.items()) if now - ts >= _STATS_TTL
        ]:
            _stats_cache.pop(expired_key, None)
        cached = _stats_cache.get(cache_key)
        if cached is not None:
            ts, data = cached
            if now - ts < _STATS_TTL:
                return data

        only_penalised_raw = request.query_params.get("only_penalised") or ""
        only_penalised = only_penalised_raw in ("1", "true", "one", "both")
        penalised_cooc_both = only_penalised_raw == "both"
        penalised_tag_set: set[str] | None = None
        if only_penalised:
            raw_penalised = get_smart_score_penalised_tags_from_request(server, request)
            penalised_tag_set = {
                str(t).strip().lower() for t in (raw_penalised or {}).keys() if t
            } or None

        character_id_raw = request.query_params.get("character_id")
        character_ids_raw = request.query_params.getlist("character_ids")
        character_id_list: list[int] = []
        for _v in character_ids_raw:
            try:
                _cid = int(_v)
                if _cid > 0:
                    character_id_list.append(_cid)
            except (TypeError, ValueError):
                continue
        character_id_list = sorted(set(character_id_list))
        character_mode_raw = request.query_params.get("character_mode", "union")
        character_mode = (character_mode_raw or "union").strip().lower()
        if character_mode not in {"union", "intersection", "difference", "xor"}:
            character_mode = "union"
        set_id_raw = request.query_params.get("set_id")
        set_ids_raw = request.query_params.getlist("set_ids")
        set_mode_raw = request.query_params.get("set_mode", "union")
        project_id_raw = request.query_params.get("project_id")
        tags_filter = request.query_params.getlist("tag")
        rejected_tags = request.query_params.getlist("rejected_tag")
        format_filter = request.query_params.getlist("format")
        min_score_raw = request.query_params.get("min_score")
        max_score_raw = request.query_params.get("max_score")
        smart_score_bucket = request.query_params.get("smart_score_bucket") or None
        resolution_bucket = request.query_params.get("resolution_bucket") or None
        file_path_prefix = request.query_params.get("file_path_prefix") or None
        import_source_folder = request.query_params.get("import_source_folder") or None
        face_filter = request.query_params.get("face_filter") or None
        confidence_tag = request.query_params.get("confidence_tag") or None
        confidence_above = request.query_params.getlist("tag_confidence_above") or []
        confidence_below = request.query_params.getlist("tag_confidence_below") or []
        include = set(request.query_params.getlist("include"))

        only_deleted = character_id_raw == "SCRAPHEAP"
        if only_deleted:
            character_id_raw = None
        set_mode = _normalize_set_mode(set_mode_raw)
        set_filter_ids = _collect_set_filter_ids(
            set_id_value=set_id_raw,
            set_ids_values=set_ids_raw or None,
        )
        try:
            min_score = int(min_score_raw) if min_score_raw is not None else None
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Invalid min_score: must be an integer",
            ) from exc

        try:
            max_score = int(max_score_raw) if max_score_raw is not None else None
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail="Invalid max_score: must be an integer",
            ) from exc

        def compute(session):
            def _empty():
                return {
                    "total": 0,
                    "total_tags": 0,
                    "tagged": 0,
                    "untagged": 0,
                    "avg_tags_per_image": 0.0,
                    "top_tags": [],
                    "top_cooccurrences": [],
                    "confidence_histogram": [],
                    "regular_tags": [],
                    "score_distribution": [],
                    "smart_score_distribution": [],
                    "resolution_distribution": [],
                }

            deleted_clause = (
                Picture.deleted.is_(True)
                if only_deleted
                else Picture.deleted.is_(False)
            )
            pic_q = select(Picture.id).where(deleted_clause)

            if set_filter_ids:
                candidate_ids = _fetch_set_candidate_ids(
                    session,
                    set_ids=set_filter_ids,
                    set_mode=set_mode,
                    deleted_only=only_deleted,
                )
                if not candidate_ids:
                    return _empty()
                pic_q = pic_q.where(Picture.id.in_(candidate_ids))
            elif character_id_list:
                rows = session.exec(
                    select(Face.character_id, Face.picture_id).where(
                        Face.character_id.in_(character_id_list)
                    )
                ).all()
                members_by_char: dict[int, set[int]] = {
                    cid: set() for cid in character_id_list
                }
                for cid, pid in rows:
                    members_by_char.setdefault(int(cid), set()).add(int(pid))

                candidate_ids: set[int]
                if character_mode == "intersection":
                    intersection: set[int] | None = None
                    for cid in character_id_list:
                        current = members_by_char.get(cid, set())
                        intersection = (
                            set(current)
                            if intersection is None
                            else intersection & current
                        )
                    candidate_ids = intersection or set()
                elif character_mode == "difference":
                    first = members_by_char.get(character_id_list[0], set())
                    rest: set[int] = set()
                    for cid in character_id_list[1:]:
                        rest |= members_by_char.get(cid, set())
                    candidate_ids = first - rest
                elif character_mode == "xor":
                    xor_union: set[int] = set()
                    for cid in character_id_list:
                        xor_union |= members_by_char.get(cid, set())
                    xor_intersection: set[int] | None = None
                    for cid in character_id_list:
                        current = members_by_char.get(cid, set())
                        xor_intersection = (
                            set(current)
                            if xor_intersection is None
                            else xor_intersection & current
                        )
                    candidate_ids = xor_union - (xor_intersection or set())
                else:
                    candidate_ids = set()
                    for cid in character_id_list:
                        candidate_ids |= members_by_char.get(cid, set())

                if not candidate_ids:
                    return _empty()
                pic_q = pic_q.where(Picture.id.in_(candidate_ids))
            elif character_id_raw == "UNASSIGNED":
                unassigned_conditions = Picture.build_unassigned_conditions(
                    enforce_stack_assignment=True,
                )
                pic_q = pic_q.where(*unassigned_conditions)
            elif character_id_raw is not None and character_id_raw not in ("", "ALL"):
                try:
                    char_id_int = int(character_id_raw)
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail="Invalid character_id")
                pic_q = pic_q.where(
                    exists(
                        select(Face.id).where(
                            Face.picture_id == Picture.id,
                            Face.character_id == char_id_int,
                        )
                    )
                )

            if project_id_raw == "UNASSIGNED":
                pic_q = pic_q.where(_project_unassigned_clause(Picture))
            elif project_id_raw is not None:
                try:
                    pid_int = int(project_id_raw)
                except (TypeError, ValueError):
                    raise HTTPException(status_code=400, detail="Invalid project_id")
                pic_q = pic_q.where(_project_membership_exists_clause(pid_int, Picture))

            if format_filter:
                pic_q = pic_q.where(
                    Picture.format.in_([f.upper() for f in format_filter])
                )
            if min_score is not None:
                pic_q = pic_q.where(Picture.score >= min_score)
            if max_score is not None:
                pic_q = pic_q.where(Picture.score <= max_score)

            if smart_score_bucket == "unscored":
                pic_q = pic_q.where(Picture.smart_score.is_(None))
            elif smart_score_bucket == "1-2":
                pic_q = pic_q.where(
                    Picture.smart_score.is_not(None), Picture.smart_score < 2.0
                )
            elif smart_score_bucket == "2-3":
                pic_q = pic_q.where(
                    Picture.smart_score >= 2.0, Picture.smart_score < 3.0
                )
            elif smart_score_bucket == "3-4":
                pic_q = pic_q.where(
                    Picture.smart_score >= 3.0, Picture.smart_score < 4.0
                )
            elif smart_score_bucket == "4-5":
                pic_q = pic_q.where(Picture.smart_score >= 4.0)

            if resolution_bucket == "unknown":
                pic_q = pic_q.where(
                    or_(Picture.width.is_(None), Picture.height.is_(None))
                )
            elif resolution_bucket == "lt1mp":
                pic_q = pic_q.where(
                    Picture.width.is_not(None),
                    Picture.height.is_not(None),
                    Picture.width * Picture.height < 1_000_000,
                )
            elif resolution_bucket == "1-4mp":
                pic_q = pic_q.where(
                    Picture.width.is_not(None),
                    Picture.height.is_not(None),
                    Picture.width * Picture.height >= 1_000_000,
                    Picture.width * Picture.height < 4_000_000,
                )
            elif resolution_bucket == "4-8mp":
                pic_q = pic_q.where(
                    Picture.width.is_not(None),
                    Picture.height.is_not(None),
                    Picture.width * Picture.height >= 4_000_000,
                    Picture.width * Picture.height < 8_000_000,
                )
            elif resolution_bucket == "8-16mp":
                pic_q = pic_q.where(
                    Picture.width.is_not(None),
                    Picture.height.is_not(None),
                    Picture.width * Picture.height >= 8_000_000,
                    Picture.width * Picture.height < 16_000_000,
                )
            elif resolution_bucket == "16plus":
                pic_q = pic_q.where(
                    Picture.width.is_not(None),
                    Picture.height.is_not(None),
                    Picture.width * Picture.height >= 16_000_000,
                )

            if file_path_prefix:
                pic_q = pic_q.where(Picture.file_path.startswith(file_path_prefix))
            if import_source_folder:
                pic_q = pic_q.where(
                    Picture.import_source_folder == import_source_folder
                )
            for tag in tags_filter:
                pic_q = pic_q.where(
                    exists(
                        select(Tag.id).where(
                            Tag.picture_id == Picture.id,
                            Tag.tag == tag,
                        )
                    )
                )
            for tag in rejected_tags:
                pic_q = pic_q.where(
                    ~exists(
                        select(Tag.id).where(
                            Tag.picture_id == Picture.id,
                            Tag.tag == tag,
                        )
                    )
                )

            if face_filter == "with_face":
                pic_q = pic_q.where(
                    exists(
                        select(Face.id).where(
                            Face.picture_id == Picture.id,
                            Face.face_index != -1,
                        )
                    )
                )
            elif face_filter == "without_face":
                pic_q = pic_q.where(
                    ~exists(
                        select(Face.id).where(
                            Face.picture_id == Picture.id,
                            Face.face_index != -1,
                        )
                    )
                )
            for i, entry in enumerate(confidence_above):
                ca_tag, ca_thresh = entry.rsplit(":", 1)
                pic_q = pic_q.where(
                    text(
                        f"EXISTS (SELECT 1 FROM tag_prediction WHERE tag_prediction.picture_id = picture.id"
                        f" AND tag_prediction.tag = :ca_tag_{i} AND tag_prediction.confidence >= :ca_thresh_{i})"
                        f" AND NOT EXISTS (SELECT 1 FROM tag WHERE tag.picture_id = picture.id AND tag.tag = :ca_tag_{i})"
                    ).bindparams(
                        **{f"ca_tag_{i}": ca_tag, f"ca_thresh_{i}": float(ca_thresh)}
                    )
                )
            for i, entry in enumerate(confidence_below):
                cb_tag, cb_thresh = entry.rsplit(":", 1)
                pic_q = pic_q.where(
                    text(
                        f"EXISTS (SELECT 1 FROM tag_prediction WHERE tag_prediction.picture_id = picture.id"
                        f" AND tag_prediction.tag = :cb_tag_{i} AND tag_prediction.confidence < :cb_thresh_{i})"
                        f" AND EXISTS (SELECT 1 FROM tag WHERE tag.picture_id = picture.id AND tag.tag = :cb_tag_{i})"
                    ).bindparams(
                        **{f"cb_tag_{i}": cb_tag, f"cb_thresh_{i}": float(cb_thresh)}
                    )
                )

            pic_subq = pic_q.subquery()

            total = session.exec(select(func.count()).select_from(pic_subq)).one()

            tagged_subq = (
                select(Tag.picture_id)
                .where(
                    Tag.picture_id.in_(select(pic_subq.c.id)),
                    Tag.tag != TAG_EMPTY_SENTINEL,
                    Tag.tag.is_not(None),
                )
                .distinct()
                .subquery()
            )
            tagged = session.exec(select(func.count()).select_from(tagged_subq)).one()

            tag_count_subq = (
                select(
                    Tag.picture_id,
                    func.count(Tag.id).label("cnt"),
                )
                .where(
                    Tag.picture_id.in_(select(pic_subq.c.id)),
                    Tag.tag != TAG_EMPTY_SENTINEL,
                    Tag.tag.is_not(None),
                )
                .group_by(Tag.picture_id)
                .subquery()
            )
            avg_row = session.exec(select(func.avg(tag_count_subq.c.cnt))).one()
            avg_tags = float(avg_row) if avg_row is not None else 0.0
            total_tags_row = session.exec(select(func.sum(tag_count_subq.c.cnt))).one()
            total_tags = int(total_tags_row) if total_tags_row is not None else 0

            top_tags_q = select(Tag.tag, func.count(Tag.id).label("cnt")).where(
                Tag.picture_id.in_(select(pic_subq.c.id)),
                Tag.tag != TAG_EMPTY_SENTINEL,
                Tag.tag.is_not(None),
            )
            if penalised_tag_set:
                top_tags_q = top_tags_q.where(
                    func.lower(Tag.tag).in_(penalised_tag_set)
                )
            top_tags_rows = session.exec(
                top_tags_q.group_by(Tag.tag).order_by(desc("cnt")).limit(20)
            ).all()
            top_tags = [{"tag": row[0], "count": row[1]} for row in top_tags_rows]

            # ── Co-occurrences (expensive: only when requested) ───────────────
            if "cooc" in include:
                t1 = Tag.__table__.alias("t1")
                t2 = Tag.__table__.alias("t2")
                cooc_base = (
                    select(
                        t1.c.tag,
                        t2.c.tag,
                        func.count().label("cnt"),
                    )
                    .select_from(
                        t1.join(
                            t2,
                            and_(
                                t1.c.picture_id == t2.c.picture_id,
                                t1.c.tag < t2.c.tag,
                            ),
                        )
                    )
                    .where(
                        t1.c.picture_id.in_(select(pic_subq.c.id)),
                        t1.c.tag != TAG_EMPTY_SENTINEL,
                        t2.c.tag != TAG_EMPTY_SENTINEL,
                    )
                )
                if penalised_tag_set:
                    if penalised_cooc_both:
                        cooc_base = cooc_base.where(
                            and_(
                                func.lower(t1.c.tag).in_(penalised_tag_set),
                                func.lower(t2.c.tag).in_(penalised_tag_set),
                            )
                        )
                    else:
                        cooc_base = cooc_base.where(
                            or_(
                                func.lower(t1.c.tag).in_(penalised_tag_set),
                                func.lower(t2.c.tag).in_(penalised_tag_set),
                            )
                        )
                cooc_rows = session.execute(
                    cooc_base.group_by(t1.c.tag, t2.c.tag)
                    .order_by(desc("cnt"))
                    .limit(10)
                ).fetchall()
                top_cooccurrences = [
                    {"tags": [row[0], row[1]], "count": row[2]} for row in cooc_rows
                ]
            else:
                top_cooccurrences = []

            # ── Confidence histogram + tag lists (only when requested) ────────
            if "conf" in include:
                conf_raw_expr = cast(TagPrediction.confidence * 5, Integer)
                conf_bucket_expr = case(
                    (conf_raw_expr >= 5, 4),
                    else_=conf_raw_expr,
                )
                conf_q = select(
                    conf_bucket_expr.label("bkt"), func.count().label("n")
                ).where(TagPrediction.picture_id.in_(select(pic_subq.c.id)))
                if confidence_tag:
                    conf_q = conf_q.where(TagPrediction.tag == confidence_tag)
                ch_rows = session.execute(
                    conf_q.group_by(conf_bucket_expr).order_by(conf_bucket_expr)
                ).fetchall()
                ch_map = {int(row[0]): int(row[1]) for row in ch_rows}
                if confidence_tag:
                    # Pictures with the tag label applied but no prediction row are
                    # treated as having an implicit confidence of 0.0 → bucket 0
                    labelled_no_pred_count = session.execute(
                        select(func.count())
                        .select_from(Tag)
                        .where(
                            Tag.picture_id.in_(select(pic_subq.c.id)),
                            Tag.tag == confidence_tag,
                            ~Tag.picture_id.in_(
                                select(TagPrediction.picture_id).where(
                                    TagPrediction.tag == confidence_tag
                                )
                            ),
                        )
                    ).scalar_one()
                    ch_map[0] = ch_map.get(0, 0) + labelled_no_pred_count
                confidence_histogram = [
                    {"label": f"{b * 20}–{b * 20 + 20}%", "count": ch_map.get(b, 0)}
                    for b in range(5)
                ]
                reg_tag_rows = session.execute(
                    select(Tag.tag)
                    .where(
                        Tag.picture_id.in_(select(pic_subq.c.id)),
                        Tag.tag != TAG_EMPTY_SENTINEL,
                        Tag.tag.is_not(None),
                    )
                    .distinct()
                    .order_by(Tag.tag)
                ).fetchall()
                regular_tags = [row[0] for row in reg_tag_rows]
            else:
                confidence_histogram = []
                regular_tags = []

            # ── Picture stats (only when requested) ─────────────────────────
            if "picture" in include:
                # Score distribution (null=unscored, 1-5)
                score_rows = session.execute(
                    select(Picture.score, func.count().label("n"))
                    .where(Picture.id.in_(select(pic_subq.c.id)))
                    .group_by(Picture.score)
                    .order_by(Picture.score)
                ).fetchall()
                score_map = {
                    (row[0] if row[0] is not None else -1): int(row[1])
                    for row in score_rows
                }
                score_distribution = [
                    {"label": "Unscored", "count": score_map.get(-1, 0)},
                    {"label": "1", "count": score_map.get(1, 0)},
                    {"label": "2", "count": score_map.get(2, 0)},
                    {"label": "3", "count": score_map.get(3, 0)},
                    {"label": "4", "count": score_map.get(4, 0)},
                    {"label": "5", "count": score_map.get(5, 0)},
                ]

                # Smart score distribution (null=unscored, 1-2, 2-3, 3-4, 4-5)
                ss_bkt = case(
                    (Picture.smart_score.is_(None), -1),
                    (Picture.smart_score < 2, 0),
                    (Picture.smart_score < 3, 1),
                    (Picture.smart_score < 4, 2),
                    else_=3,
                )
                ss_rows = session.execute(
                    select(ss_bkt.label("bkt"), func.count().label("n"))
                    .where(Picture.id.in_(select(pic_subq.c.id)))
                    .group_by(ss_bkt)
                    .order_by(ss_bkt)
                ).fetchall()
                ss_map = {int(row[0]): int(row[1]) for row in ss_rows}
                smart_score_distribution = [
                    {"label": "Unscored", "count": ss_map.get(-1, 0)},
                    {"label": "1–2", "count": ss_map.get(0, 0)},
                    {"label": "2–3", "count": ss_map.get(1, 0)},
                    {"label": "3–4", "count": ss_map.get(2, 0)},
                    {"label": "4–5", "count": ss_map.get(3, 0)},
                ]

                # Resolution distribution (null + <1MP, 1–4MP, 4–8MP, 8–16MP, 16+MP)
                res_bkt = case(
                    (
                        or_(Picture.width.is_(None), Picture.height.is_(None)),
                        -1,
                    ),
                    (Picture.width * Picture.height < 1_000_000, 0),
                    (Picture.width * Picture.height < 4_000_000, 1),
                    (Picture.width * Picture.height < 8_000_000, 2),
                    (Picture.width * Picture.height < 16_000_000, 3),
                    else_=4,
                )
                res_rows = session.execute(
                    select(res_bkt.label("bkt"), func.count().label("n"))
                    .where(Picture.id.in_(select(pic_subq.c.id)))
                    .group_by(res_bkt)
                    .order_by(res_bkt)
                ).fetchall()
                res_map = {int(row[0]): int(row[1]) for row in res_rows}
                resolution_distribution = [
                    {"label": "Unknown", "count": res_map.get(-1, 0)},
                    {"label": "<1 MP", "count": res_map.get(0, 0)},
                    {"label": "1–4 MP", "count": res_map.get(1, 0)},
                    {"label": "4–8 MP", "count": res_map.get(2, 0)},
                    {"label": "8–16 MP", "count": res_map.get(3, 0)},
                    {"label": "16+ MP", "count": res_map.get(4, 0)},
                ]
            else:
                score_distribution = []
                smart_score_distribution = []
                resolution_distribution = []

            return {
                "total": int(total),
                "total_tags": total_tags,
                "tagged": int(tagged),
                "untagged": int(total) - int(tagged),
                "avg_tags_per_image": round(avg_tags, 2),
                "top_tags": top_tags,
                "top_cooccurrences": top_cooccurrences,
                "confidence_histogram": confidence_histogram,
                "regular_tags": regular_tags,
                "score_distribution": score_distribution,
                "smart_score_distribution": smart_score_distribution,
                "resolution_distribution": resolution_distribution,
            }

        result = server.vault.db.run_immediate_read_task(compute)
        _stats_cache[cache_key] = (now, result)
        return result

    return router
