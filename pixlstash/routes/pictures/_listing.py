import re
import sys
from datetime import datetime

from fastapi import (
    HTTPException,
    Query,
    Request,
)
from sqlalchemy import (
    or_,
    text,
)
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    Face,
    Picture,
    PictureProjectMember,
    SortMechanism,
)
from pixlstash.db_models.guest_score import GuestScore
from pixlstash.db_models.user_token import UserToken
from pixlstash.pixl_logging import get_logger
from pixlstash.picture_scoring import (
    find_pictures_by_character_likeness,
)
from pixlstash.utils.service.serialization_utils import safe_model_dict
from pixlstash.utils.service.filter_helpers import (
    collect_set_filter_ids,
    fetch_set_candidate_ids,
    normalize_set_mode,
    project_membership_exists_clause,
    project_unassigned_clause,
)
from pixlstash.utils.stack.stack_utils import deduplicate_by_stack

from ._helpers import (
    _enrich_stack_counts,
    _fetch_hidden_picture_ids,
)


logger = get_logger(__name__)


def select_pictures_for_listing(
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
    scope_set_id: int | None = None,
    scope_character_id: int | None = None,
    stream_state: dict | None = None,
    count_only: bool = False,
):
    """List pictures for a request.

    When `stream_state` is a dict, the function operates in streaming mode:
    it over-fetches by one row at the SQL layer and records the pre-post-filter
    row count in `stream_state["sql_count"]`. This lets the caller decide
    completion (`done = sql_count <= limit`) without relying on the post-filter
    row count, which the historical implementation incorrectly conflated with
    end-of-stream. For the CHARACTER_LIKENESS sort the function sets
    `stream_state["oneshot"] = True` to signal that the caller should treat
    the response as complete (no further pagination).
    """
    effective_limit = limit + 1 if stream_state is not None else limit

    def _record_sql_count(pics):
        if stream_state is not None:
            stream_state["sql_count"] = len(pics)
            if len(pics) > limit:
                return pics[:limit]
        return pics

    def _empty_result():
        # An empty-result early-return is always end-of-stream for a streaming
        # caller: any subsequent fetch with a larger offset would yield the
        # same empty set.
        if stream_state is not None:
            stream_state["sql_count"] = 0
            stream_state["oneshot"] = True
        return []

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
            shared_only_param = request.query_params.get("shared_only")
            if shared_only_param == "true":
                query_params["shared_only"] = True
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
    shared_only = bool(query_params.pop("shared_only", False))
    query_params.pop(
        "guest_session_id", None
    )  # handled separately; must not leak into **query_params
    only_deleted = False
    set_mode = normalize_set_mode(set_mode_raw)
    set_filter_ids = collect_set_filter_ids(
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

    # Token scope enforcement: if a picture_set token is active, restrict to that set.
    if scope_set_id is not None:
        set_filter_ids = [scope_set_id]
        set_mode = "union"

    # Token scope enforcement: if a character token is active, restrict to that character.
    if scope_character_id is not None:
        character_id = scope_character_id
        character_id_list = [scope_character_id]
        character_mode = "union"

    # Shared-only filter: restrict to pictures that have an active READ token for the current user.
    if shared_only:
        auth_user_id = getattr(request.state, "auth_user_id", None)
        if auth_user_id is not None:

            def _fetch_shared_ids(session: Session, uid: int) -> list[int]:
                now = datetime.utcnow()
                return list(
                    session.exec(
                        select(UserToken.resource_id).where(
                            UserToken.user_id == uid,
                            UserToken.resource_type == "picture",
                            UserToken.scope == "READ",
                            UserToken.resource_id.is_not(None),
                            or_(
                                UserToken.expires_at.is_(None),
                                UserToken.expires_at > now,
                            ),
                        )
                    ).all()
                )

            shared_ids = server.vault.db.run_task(
                _fetch_shared_ids, auth_user_id, priority=DBPriority.IMMEDIATE
            )
            shared_id_set = set(shared_ids)
            existing_ids = query_params.get("id")
            if existing_ids:
                query_params["id"] = [
                    i for i in existing_ids if int(i) in shared_id_set
                ]
            else:
                query_params["id"] = [str(i) for i in shared_id_set]

    def _set_candidate_ids_for_session(session: Session):
        return fetch_set_candidate_ids(
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

    guest_session_id = getattr(request.state, "guest_session_id", None)
    guest_token_id = (
        getattr(request.state, "token_id", None) if guest_session_id else None
    )
    # Fallback: rejected-consent guests have no HttpOnly cookie, but may pass
    # the in-memory session ID as a query param so scores are overlaid for the
    # current page session.  Only honoured for READ-scoped tokens.
    if not guest_session_id:
        token_scope = getattr(request.state, "token_scope", None)
        if token_scope is not None and token_scope.scope == "READ":
            qp_sid = request.query_params.get("guest_session_id", "")
            if qp_sid and re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", qp_sid):
                guest_session_id = qp_sid
                guest_token_id = getattr(request.state, "token_id", None)

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
            query = query.where(project_unassigned_clause(Picture))
        elif project_id_value is not None:
            try:
                query = query.where(
                    project_membership_exists_clause(int(project_id_value), Picture)
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
        if count_only:
            return None  # CHARACTER_LIKENESS count is not supported cheaply
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
                _set_candidate_ids_for_session
            )
            candidate_ids = (
                set_candidate_ids
                if candidate_ids is None
                else set(candidate_ids) & set_candidate_ids
            )
        if candidate_ids is not None and not candidate_ids:
            if stream_state is not None:
                stream_state["sql_count"] = 0
                stream_state["oneshot"] = True
            return []
        # CHARACTER_LIKENESS sort is inherently non-streamable: the underlying
        # routine loads and ranks all candidates in memory. Signal one-shot to
        # the streaming caller so it stops paginating after this batch.
        if stream_state is not None:
            stream_state["oneshot"] = True
        pics = find_pictures_by_character_likeness(
            server,
            character_id,
            reference_character_id,
            offset,
            limit,
            descending,
            candidate_ids=candidate_ids,
        )
        if stream_state is not None:
            stream_state["sql_count"] = len(pics)
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
            pics = deduplicate_by_stack(pics)
            pics = _enrich_stack_counts(server, pics)
        return pics
    if character_id == "UNASSIGNED":
        if count_only:
            return None  # UNASSIGNED count requires a separate query not implemented here
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
            limit=effective_limit,
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
            picture_ids=(
                [int(i) for i in query_params["id"] if str(i).isdigit()]
                if query_params.get("id")
                else None
            ),
            guest_session_id=guest_session_id,
            guest_token_id=guest_token_id,
        )
        pics = _record_sql_count(pics)
    elif only_deleted:
        pics = server.vault.db.run_task(
            Picture.find,
            sort_mech=sort_mech,
            offset=offset,
            limit=effective_limit,
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
            guest_session_id=guest_session_id,
            guest_token_id=guest_token_id,
            **query_params,
        )
        pics = _record_sql_count(pics)
    else:
        if set_filter_ids:
            set_candidate_ids = server.vault.db.run_immediate_read_task(
                _set_candidate_ids_for_session
            )
            if not set_candidate_ids:
                return _empty_result()
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
                return _empty_result()

            # When a project filter is also present, restrict to pictures that
            # are members of that project so pictures removed from a project no
            # longer appear in its character grid view.
            if project_id_raw is not None:
                if project_id_raw == "UNASSIGNED":

                    def _get_project_unassigned_ids(session, ids):
                        rows = session.exec(
                            select(Picture.id).where(
                                Picture.id.in_(ids),
                                project_unassigned_clause(Picture),
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
                return _empty_result()
            existing_ids = query_params.get("id")
            if existing_ids:
                existing_id_set = {int(i) for i in existing_ids if str(i).isdigit()}
                picture_ids = [i for i in picture_ids if int(i) in existing_id_set]
                if not picture_ids:
                    return _empty_result()
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
                return _empty_result()

            if project_id_raw is not None:
                if project_id_raw == "UNASSIGNED":

                    def _get_project_unassigned_ids_multi(session, ids):
                        rows = session.exec(
                            select(Picture.id).where(
                                Picture.id.in_(ids),
                                project_unassigned_clause(Picture),
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
                return _empty_result()
            existing_ids = query_params.get("id")
            if existing_ids:
                existing_id_set = {int(i) for i in existing_ids if str(i).isdigit()}
                picture_ids = [i for i in picture_ids if int(i) in existing_id_set]
                if not picture_ids:
                    return _empty_result()
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
                            project_unassigned_clause(Pic),
                            Pic.deleted.is_(False),
                        )
                    ).all()
                    return list(rows)

                project_pic_ids = server.vault.db.run_task(get_unassigned_project_ids)
                if not project_pic_ids:
                    return _empty_result()
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

        if count_only:
            return server.vault.db.run_task(
                Picture.find,
                count_only=True,
                format=format,
                include_unimported=True,
                min_score=min_score,
                max_score=max_score,
                smart_score_bucket=smart_score_bucket,
                resolution_bucket=resolution_bucket,
                file_path_prefix=file_path_prefix,
                face_filter=face_filter,
                **query_params,
            )
        pics = server.vault.db.run_task(
            Picture.find,
            sort_mech=sort_mech,
            offset=offset,
            limit=effective_limit,
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
            guest_session_id=guest_session_id,
            guest_token_id=guest_token_id,
            **query_params,
        )
        pics = _record_sql_count(pics)
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
        for d in result:
            ts = d.get("text_score")
            d["text_score"] = round(ts, 3) if ts is not None and ts >= 0 else None
    if stack_leaders_only:
        result = _enrich_stack_counts(server, result)

    if guest_session_id and result:
        picture_ids_set = {d["id"] for d in result if "id" in d}

        def fetch_guest_scores(session: Session) -> dict[int, int]:
            # Fetch all scores for this session; filter to the current page in
            # Python.  Avoids .in_() on sa_column-defined fields which can
            # silently produce no rows in SQLModel.
            stmt = select(GuestScore).where(GuestScore.session_id == guest_session_id)
            if guest_token_id is not None:
                stmt = stmt.where(GuestScore.token_id == guest_token_id)
            rows = session.exec(stmt).all()
            return {row.picture_id: row.score for row in rows}

        try:
            guest_scores: dict[int, int] = server.vault.db.run_immediate_read_task(
                fetch_guest_scores
            )
        except Exception:
            logger.exception("[guest-scores] Failed to fetch guest scores for overlay")
            guest_scores = {}

        if guest_scores:
            for d in result:
                pic_id = d.get("id")
                if pic_id in guest_scores and pic_id in picture_ids_set:
                    d["score"] = guest_scores[pic_id]
    return result


def register_routes(router, server):
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
        token_scope = getattr(request.state, "token_scope", None)
        scope_set_id = (
            token_scope.resource_id
            if token_scope is not None and token_scope.resource_type == "picture_set"
            else None
        )
        # For project-scoped tokens, force the project filter regardless of what
        # the caller passes.  For character-scoped tokens, force the character
        # filter by injecting it as a query-param override via project_id /
        # character_id below.
        if token_scope is not None and token_scope.resource_type == "project":
            project_id = str(token_scope.resource_id)
        scope_character_id = (
            token_scope.resource_id
            if token_scope is not None and token_scope.resource_type == "character"
            else None
        )
        return select_pictures_for_listing(
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
            scope_set_id=scope_set_id,
            scope_character_id=scope_character_id,
        )

    @router.get(
        "/pictures/stream",
        summary="Stream pictures in batches",
        description=(
            "Fetches a single batch of pictures and reports completion via an "
            "explicit `done` flag derived from the underlying SQL row count "
            "(not the post-filter row count). Callers paginate by passing the "
            "returned `next_offset` until `done` is true."
        ),
    )
    def stream_pictures(
        request: Request,
        sort: str = Query(None),
        descending: bool = Query(True),
        offset: int = Query(0, ge=0),
        batch_limit: int = Query(1000, ge=1, le=5000),
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
        token_scope = getattr(request.state, "token_scope", None)
        scope_set_id = (
            token_scope.resource_id
            if token_scope is not None and token_scope.resource_type == "picture_set"
            else None
        )
        if token_scope is not None and token_scope.resource_type == "project":
            project_id = str(token_scope.resource_id)
        scope_character_id = (
            token_scope.resource_id
            if token_scope is not None and token_scope.resource_type == "character"
            else None
        )
        stream_state: dict = {}
        pictures = select_pictures_for_listing(
            server=server,
            request=request,
            sort=sort,
            descending=descending,
            offset=offset,
            limit=batch_limit,
            metadata_fields=metadata_fields,
            return_ids_only=False,
            stack_leaders_only=False,  # TODO: re-enable once streaming baseline is stable
            project_id=project_id,
            scope_set_id=scope_set_id,
            scope_character_id=scope_character_id,
            stream_state=stream_state,
        )
        sql_count = int(stream_state.get("sql_count", 0))
        oneshot = bool(stream_state.get("oneshot", False))
        # `done` is decided purely on the pre-post-filter SQL row count. This
        # is the crucial property that prevents the historical bug where
        # post-filter shrinkage (hidden tags, stack dedup) was misread as
        # end-of-stream.
        done = oneshot or sql_count <= batch_limit
        next_offset = offset + min(batch_limit, sql_count)
        logger.debug(
            "[stream] offset=%d batch_limit=%d sort=%r sql_count=%d "
            "returned=%d done=%s next_offset=%d first_id=%s last_id=%s",
            offset, batch_limit, sort, sql_count,
            len(pictures), done, next_offset,
            pictures[0].get("id") if pictures else None,
            pictures[-1].get("id") if pictures else None,
        )
        return {
            "pictures": pictures,
            "done": done,
            "next_offset": next_offset,
        }

    @router.get(
        "/pictures/count",
        summary="Total picture count for a listing filter",
        description=(
            "Returns the total number of pictures matching the same filter "
            "set used by `/pictures` and `/pictures/stream`. For non-"
            "streamable views (character-likeness) the count is reported as "
            "`null`."
        ),
    )
    def count_pictures(
        request: Request,
        sort: str = Query(None),
        descending: bool = Query(True),
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
        token_scope = getattr(request.state, "token_scope", None)
        scope_set_id = (
            token_scope.resource_id
            if token_scope is not None and token_scope.resource_type == "picture_set"
            else None
        )
        if token_scope is not None and token_scope.resource_type == "project":
            project_id = str(token_scope.resource_id)
        scope_character_id = (
            token_scope.resource_id
            if token_scope is not None and token_scope.resource_type == "character"
            else None
        )
        # Use count_only=True to run a fast SELECT COUNT(*) rather than
        # fetching all rows. Character-likeness paths are inherently
        # non-streamable; we return null in that case. The count may be a
        # small over-estimate for deployments with hidden-tag post-filtering,
        # but is exact for the common case.
        sort_mech = SortMechanism.from_string(sort, descending=descending) if sort else None
        if sort_mech and sort_mech.key == SortMechanism.Keys.CHARACTER_LIKENESS:
            return {"count": None}
        count = select_pictures_for_listing(
            server=server,
            request=request,
            sort=sort,
            descending=descending,
            offset=0,
            limit=sys.maxsize,
            metadata_fields=[],
            count_only=True,
            project_id=project_id,
            scope_set_id=scope_set_id,
            scope_character_id=scope_character_id,
        )
        logger.debug("[count] sort=%r returning count=%s", sort, count)
        return {"count": count}

