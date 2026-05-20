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
    case,
    delete,
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
    ReferenceFolder,
    SortMechanism,
    Tag,
)
from pixlstash.db_models.guest_score import GuestScore
from pixlstash.db_models.user import User
from pixlstash.db_models.user_token import UserToken
from pixlstash.event_types import EventType
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
from pixlstash.utils.watermark import apply_watermark, get_watermark_bytes
from pixlstash.tasks import TaskType
from pixlstash.db_models.tag import TAG_EMPTY_SENTINEL
from pixlstash.services._filter_helpers import (
    collect_set_filter_ids,
    fetch_set_candidate_ids,
    normalize_set_mode,
    project_membership_exists_clause,
    project_unassigned_clause,
)
from pixlstash.services import plugin_service
from pixlstash.services.picture_stats import PictureStatsParams, compute_picture_stats


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


# These helpers were extracted to pixlstash/services/_filter_helpers.py.
# The private-prefixed aliases below keep existing callers in this file working
# without requiring a bulk rename in the same changeset.
_project_membership_exists_clause = project_membership_exists_clause
_project_unassigned_clause = project_unassigned_clause
_normalize_set_mode = normalize_set_mode
_collect_set_filter_ids = collect_set_filter_ids
_fetch_set_candidate_ids = fetch_set_candidate_ids


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




def _picture_id_in_scoped_set(server, picture_id: int, set_id: int) -> bool:
    """Return True if picture_id is a member of set_id."""

    def check(session):
        return (
            session.exec(
                select(PictureSetMember).where(
                    PictureSetMember.set_id == set_id,
                    PictureSetMember.picture_id == picture_id,
                )
            ).first()
            is not None
        )

    return server.vault.db.run_immediate_read_task(check)

def _picture_id_in_scoped_character(server, picture_id: int, character_id: int) -> bool:
    """Return True if the picture has at least one face assigned to character_id."""

    def check(session):
        return (
            session.exec(
                select(Face).where(
                    Face.picture_id == picture_id,
                    Face.character_id == character_id,
                )
            ).first()
            is not None
        )

    return server.vault.db.run_immediate_read_task(check)

def _picture_id_in_scoped_project(server, picture_id: int, project_id: int) -> bool:
    """Return True if picture_id is a member of project_id."""

    def check(session):
        return (
            session.exec(
                select(PictureProjectMember).where(
                    PictureProjectMember.picture_id == picture_id,
                    PictureProjectMember.project_id == project_id,
                )
            ).first()
            is not None
        )

    return server.vault.db.run_immediate_read_task(check)

def enforce_picture_scope(server, request: Request, picture_id: int):
    """Raise 403 if a scoped token does not permit access to this picture."""
    scope = getattr(request.state, "token_scope", None)
    if scope is None:
        return
    if scope.resource_type == "picture_set":
        if not _picture_id_in_scoped_set(server, picture_id, scope.resource_id):
            raise HTTPException(
                status_code=403,
                detail="Token is not authorised to access this picture",
            )
    elif scope.resource_type == "character":
        if not _picture_id_in_scoped_character(server, picture_id, scope.resource_id):
            raise HTTPException(
                status_code=403,
                detail="Token is not authorised to access this picture",
            )
    elif scope.resource_type == "project":
        if not _picture_id_in_scoped_project(server, picture_id, scope.resource_id):
            raise HTTPException(
                status_code=403,
                detail="Token is not authorised to access this picture",
            )
    elif scope.resource_type is not None:
        raise HTTPException(
            status_code=403,
            detail="Token is not authorised for this resource type",
        )

