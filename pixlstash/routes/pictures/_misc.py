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

from ._helpers import (
    _collect_set_filter_ids,
    _fetch_hidden_picture_ids,
    _fetch_set_candidate_ids,
    _normalize_set_mode,
    _project_membership_exists_clause,
    _project_unassigned_clause,
    _stats_cache,
    _STATS_TTL,
    clear_stats_cache,
)


logger = get_logger(__name__)


def register_routes(router, server):
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
        return plugin_service.list_plugins(server.vault)

    @router.post(
        "/pictures/plugins/{name}",
        summary="Run image plugin",
        description="Runs a named image plugin on selected pictures and imports outputs into stacks.",
    )
    async def run_picture_plugin(name: str, payload: dict = Body(...)):
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

        try:
            return await plugin_service.run_plugin_on_pictures(
                server, name, picture_ids, parameters, captions
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=f"Plugin failed: {exc}")

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

        params = PictureStatsParams(
            only_deleted=only_deleted,
            set_filter_ids=set_filter_ids,
            set_mode=set_mode,
            character_id_list=character_id_list,
            character_mode=character_mode,
            character_id_raw=character_id_raw,
            project_id_raw=project_id_raw,
            format_filter=format_filter,
            min_score=min_score,
            max_score=max_score,
            smart_score_bucket=smart_score_bucket,
            resolution_bucket=resolution_bucket,
            file_path_prefix=file_path_prefix,
            import_source_folder=import_source_folder,
            tags_filter=tags_filter,
            rejected_tags=rejected_tags,
            face_filter=face_filter,
            confidence_tag=confidence_tag,
            confidence_above=confidence_above,
            confidence_below=confidence_below,
            include=include,
            penalised_tag_set=penalised_tag_set,
            penalised_cooc_both=penalised_cooc_both,
        )
        result = compute_picture_stats(server.vault, params)
        _stats_cache[cache_key] = (now, result)
        return result

