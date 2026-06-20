import re
import sys
from datetime import datetime

from fastapi import (
    Depends,
    HTTPException,
    Query,
    Request,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import (
    or_,
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
    count_pictures_by_character_likeness,
    find_pictures_by_character_likeness_sql,
)
from pixlstash.utils.service.serialization_utils import safe_model_dict
from pixlstash.utils.service.filter_helpers import (
    collect_set_filter_ids,
    fetch_scope_allowed_picture_ids,
    fetch_set_candidate_ids,
    normalize_set_mode,
    project_membership_exists_clause,
    project_unassigned_clause,
)
from pixlstash.utils.query.predicate_filter import PredicateFilter

from ._helpers import (
    _enrich_stack_counts,
    _get_hidden_tags_from_request,
)


logger = get_logger(__name__)


class GridPicture(BaseModel):
    """A single picture row as returned by the grid listing endpoints.

    Fields are the projection used when ``fields=grid``; all are optional
    because the exact set returned depends on the requested ``fields`` and on
    server-side enrichment (stack counts, guest-score overlay, thumbnail/face
    data added by ``POST /pictures/thumbnails``). Documented for the OpenAPI
    schema only — responses are not filtered against this model, so additional
    fields may be present.
    """

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": 12345,
                "width": 1024,
                "height": 1536,
                "format": "PNG",
                "score": 4,
                "smart_score": 3.87,
                "created_at": "2026-01-14T09:30:00",
                "imported_at": "2026-01-15T18:02:11",
                "stack_id": 87,
                "stack_position": 0,
                "stack_count": 5,
                "file_path": "01a43b7e-dda4-45bb-a912-2ed4b786260d.png",
            }
        },
    )

    id: int | None = Field(None, description="Picture id (primary key).")
    width: int | None = Field(None, description="Image width in pixels.")
    height: int | None = Field(None, description="Image height in pixels.")
    format: str | None = Field(None, description="File format, e.g. PNG, JPEG, MP4.")
    score: int | None = Field(None, description="Manual star rating (0-5).")
    smart_score: float | None = Field(
        None, description="Model-predicted aesthetic score."
    )
    smartScore: float | None = Field(
        None, description="camelCase alias of smart_score (legacy clients)."
    )
    created_at: datetime | None = Field(
        None, description="When the source file was created."
    )
    imported_at: datetime | None = Field(
        None, description="When the picture was imported into the vault."
    )
    stack_id: int | None = Field(
        None, description="Id of the stack this picture belongs to, if any."
    )
    stack_position: int | None = Field(
        None, description="Position within its stack; 0 is the stack leader."
    )
    stack_count: int | None = Field(
        None,
        description="Number of pictures in the stack (only set on stack leaders "
        "when stack_leaders_only is used).",
    )
    tag_uncertainty: float | None = Field(
        None, description="Aggregate tag-prediction uncertainty."
    )
    anomaly_tag_uncertainty: float | None = Field(
        None, description="Uncertainty restricted to anomaly/problem tags."
    )
    text_score: float | None = Field(
        None, description="Relevance score for the active text search, if any."
    )
    reference_folder_id: int | None = Field(
        None, description="Source reference-folder id for externally-referenced files."
    )
    file_path: str | None = Field(
        None,
        description="Path relative to the image root (omitted when grid_lite=true).",
    )


class StreamPicturesResponse(BaseModel):
    """One batch of the streaming listing plus pagination state."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "pictures": [GridPicture.model_config["json_schema_extra"]["example"]],
                "done": False,
                "next_offset": 1000,
            }
        }
    )

    pictures: list[GridPicture] = Field(
        description="Pictures in this batch, in sort order."
    )
    done: bool = Field(
        description="True when the underlying SQL row count for this batch was "
        "<= batch_limit, i.e. there are no further rows to fetch.",
    )
    next_offset: int = Field(
        description="Offset to pass on the next request to continue paginating.",
    )


class PictureCountResponse(BaseModel):
    """Total number of pictures matching the listing filters.

    ``count`` is ``null`` for sorts where a total cannot be computed cheaply
    (e.g. CHARACTER_LIKENESS)."""

    model_config = ConfigDict(json_schema_extra={"example": {"count": 28259}})

    count: int | None = Field(
        None,
        description="Total matching pictures, or null when not cheaply computable.",
    )


class PictureListFilters:
    """Shared query-param filters for the picture listing endpoints.

    These were previously parsed directly out of ``request.query_params`` and so
    were invisible in the OpenAPI schema. Declaring them here as a ``Depends``
    dependency surfaces them in the generated docs (and validates the typed
    ones) without changing behaviour: the handlers still read the raw values
    from ``request.query_params``.
    """

    def __init__(
        self,
        character_id: str | None = Query(
            None,
            description="Character filter: a character id, or 'UNASSIGNED'.",
            examples=["42"],
        ),
        character_ids: list[str] = Query(
            default=[], description="Multi-character filter (repeatable)."
        ),
        character_mode: str | None = Query(
            None, description="union | intersection | difference | xor"
        ),
        set_id: str | None = Query(None, description="Single picture-set filter."),
        set_ids: list[str] = Query(
            default=[], description="Multi-set filter (repeatable)."
        ),
        set_mode: str | None = Query(
            None, description="union | intersection | difference"
        ),
        base_set_id: str | None = Query(
            None, description="Base set for set_mode=difference."
        ),
        reference_character_id: str | None = Query(
            None, description="Required reference for sort=CHARACTER_LIKENESS."
        ),
        min_score: int | None = Query(
            None, description="Minimum star score.", examples=[3]
        ),
        max_score: int | None = Query(
            None, description="Maximum star score.", examples=[5]
        ),
        smart_score_bucket: str | None = Query(
            None, description="unscored | 1-2 | 2-3 | 3-4 | 4-5"
        ),
        resolution_bucket: str | None = Query(
            None,
            description="unknown | lt1mp | 1-4mp | 4-8mp | 8-16mp | 16plus",
        ),
        file_path_prefix: str | None = Query(
            None, description="Restrict to file paths starting with this prefix."
        ),
        face_filter: str | None = Query(
            None, description="with_face | without_face", examples=["with_face"]
        ),
        shared_only: bool = Query(
            False, description="Only pictures shared with the current user."
        ),
        apply_tag_filter: bool = Query(
            False, description="Apply the user's hidden-tag filter."
        ),
        format: list[str] = Query(
            default=[], description="Filter by file format (repeatable)."
        ),
        tag: list[str] = Query(
            default=[],
            description="Require these tags (repeatable).",
            examples=["sunset"],
        ),
        rejected_tag: list[str] = Query(
            default=[], description="Exclude these tags (repeatable)."
        ),
        tag_confidence_above: list[str] = Query(
            default=[], description="'tag:threshold' prediction filter (repeatable)."
        ),
        tag_confidence_below: list[str] = Query(
            default=[], description="'tag:threshold' prediction filter (repeatable)."
        ),
        comfyui_model: list[str] = Query(
            default=[], description="Filter by ComfyUI model (repeatable)."
        ),
        comfyui_lora: list[str] = Query(
            default=[], description="Filter by ComfyUI LoRA (repeatable)."
        ),
        reference_folder_id: str | None = Query(
            None, description="Filter by reference-folder id."
        ),
        import_source_folder: str | None = Query(
            None, description="Filter by import source folder."
        ),
        id: list[str] = Query(
            default=[], description="Restrict to specific picture ids (repeatable)."
        ),
    ):
        # Values are intentionally not stored: the handlers read them from
        # request.query_params. This dependency exists purely to document and
        # validate the query surface in OpenAPI.
        pass


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
    scope_picture_id: int | None = None,
    stream_state: dict | None = None,
    count_only: bool = False,
):
    """List pictures for a request.

    When `stream_state` is a dict, the function operates in streaming mode:
    it over-fetches by one row at the SQL layer and records the pre-post-filter
    row count in `stream_state["sql_count"]`. This lets the caller decide
    completion (`done = sql_count <= limit`) without relying on the post-filter
    row count, which the historical implementation incorrectly conflated with
    end-of-stream. CHARACTER_LIKENESS now uses a registered SQLite scalar
    function (character_face_likeness) so sorting and pagination happen fully
    at the SQL layer, meaning the standard over-fetch probe applies.
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
        return 0 if count_only else []

    def serialize_metadata(pictures):
        result = []
        for pic in pictures:
            pic_d = safe_model_dict(pic)
            d = {
                field: pic_d.get(field) for field in metadata_fields if field != "tags"
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
            impossible_sources_param = request.query_params.getlist(
                "impossible_tag_source"
            )
            if impossible_sources_param:
                query_params["impossible_sources"] = impossible_sources_param
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
    impossible_sources = query_params.pop("impossible_sources", None)
    shared_only = bool(query_params.pop("shared_only", False))
    query_params.pop(
        "guest_session_id", None
    )  # handled separately; must not leak into **query_params
    query_params.pop(
        "stack_leaders_only", None
    )  # already consumed as an explicit kwarg; must not leak into **query_params
    _only_deleted_raw = query_params.pop("only_deleted", None)
    only_deleted = _only_deleted_raw == "true" or _only_deleted_raw is True
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

    # Token scope enforcement: a single-picture share token may only ever
    # resolve that one picture id.  Intersect with any caller-supplied ids so
    # a `picture`-scoped token cannot widen the result to the whole vault.
    if scope_picture_id is not None:
        existing_ids = query_params.get("id")
        if existing_ids:
            query_params["id"] = [
                i for i in existing_ids if str(i) == str(scope_picture_id)
            ]
        else:
            query_params["id"] = [str(scope_picture_id)]

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

    hidden_tags = _get_hidden_tags_from_request(server, request) or None

    pics = []
    if character_id == "SCRAPHEAP":
        logger.warning(
            "character_id=SCRAPHEAP is deprecated; use only_deleted=true instead"
        )
        only_deleted = True
        character_id = None

    # The scrapheap (deleted) view never collapses stacks: every deleted picture
    # is listed individually. A soft-deleted stack member (e.g. a former leader,
    # which normalize_stack_positions sorts behind its live siblings) is therefore
    # always visible and restorable, instead of being hidden because it is no
    # longer at stack_position == 0.
    if only_deleted:
        stack_leaders_only = False

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
        hidden_tags_filter_value: list[str] | None = None,
        comfyui_models_filter_value: list[str] | None = None,
        comfyui_loras_filter_value: list[str] | None = None,
        face_filter_value: str | None = None,
        impossible_sources_value: list[str] | None = None,
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
                and not hidden_tags_filter_value
                and not comfyui_models_filter_value
                and not comfyui_loras_filter_value
                and not face_filter_value
                and not impossible_sources_value
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

        # Intrinsic-attribute predicates via the shared compiler.  Deleted / project
        # / character scoping is handled above (woven into the candidate branches),
        # so the filter applies no lifecycle clauses here.
        query = PredicateFilter(
            format=formats,
            min_score=min_score_value,
            max_score=max_score_value,
            smart_score_bucket=smart_score_bucket_value,
            resolution_bucket=resolution_bucket_value,
            tags_filter=tags_filter_value,
            tags_rejected_filter=tags_rejected_filter_value,
            hidden_tags_filter=hidden_tags_filter_value,
            tags_confidence_above_filter=tags_confidence_above_filter_value,
            tags_confidence_below_filter=tags_confidence_below_filter_value,
            comfyui_models_filter=comfyui_models_filter_value,
            comfyui_loras_filter=comfyui_loras_filter_value,
            face_filter=face_filter_value,
            impossible_sources=impossible_sources_value,
            apply_deleted_filter=False,
        ).apply(query)

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
            hidden_tags_filter_value=hidden_tags,
            comfyui_models_filter_value=query_params.get("comfyui_models_filter"),
            comfyui_loras_filter_value=query_params.get("comfyui_loras_filter"),
            face_filter_value=face_filter,
            impossible_sources_value=impossible_sources,
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
            return _empty_result()
        if count_only:
            return count_pictures_by_character_likeness(
                server,
                character_id,
                candidate_ids=list(candidate_ids)
                if candidate_ids is not None
                else None,
                deleted_only=only_deleted,
                stack_leaders_only=stack_leaders_only,
            )
        pics = find_pictures_by_character_likeness_sql(
            server,
            character_id,
            reference_character_id,
            offset,
            effective_limit,
            descending,
            candidate_ids=list(candidate_ids) if candidate_ids is not None else None,
            deleted_only=only_deleted,
            stack_leaders_only=stack_leaders_only,
        )
        pics = _record_sql_count(pics)
        if return_ids_only:
            return [pic.get("id") for pic in pics if pic.get("id") is not None]
        if stack_leaders_only:
            pics = _enrich_stack_counts(server, pics)
        return pics
    if character_id == "UNASSIGNED":
        # Token scope enforcement (BOLA): this branch bypasses the set/character/
        # picture scope filters applied to the main query path, so constrain it
        # explicitly.  fetch_scope_allowed_picture_ids returns None for an
        # owner/unscoped token and the allowed id set otherwise; fail closed when
        # nothing is in scope rather than falling through to an unfiltered query.
        scope_allowed_ids = fetch_scope_allowed_picture_ids(server, request)
        if scope_allowed_ids is not None:
            requested = query_params.get("id")
            if requested:
                allowed = [
                    str(i)
                    for i in requested
                    if str(i).isdigit() and int(i) in scope_allowed_ids
                ]
            else:
                allowed = [str(i) for i in scope_allowed_ids]
            if not allowed:
                return _empty_result()
            query_params["id"] = allowed
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
        if count_only:
            return server.vault.db.run_task(
                Picture.find_unassigned,
                count_only=True,
                project_id=unassigned_project_id,
                only_unassigned_project=unassigned_project_only,
                format=format,
                min_score=min_score,
                max_score=max_score,
                smart_score_bucket=smart_score_bucket,
                resolution_bucket=resolution_bucket,
                face_filter=face_filter,
                impossible_sources=impossible_sources,
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
                hidden_tags_filter=hidden_tags,
                picture_ids=(
                    [int(i) for i in query_params["id"] if str(i).isdigit()]
                    if query_params.get("id")
                    else None
                ),
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
            hidden_tags_filter=hidden_tags,
            face_filter=face_filter,
            impossible_sources=impossible_sources,
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
        if count_only:
            return server.vault.db.run_task(
                Picture.find,
                count_only=True,
                only_deleted=True,
                include_unimported=True,
                stack_leaders_only=stack_leaders_only,
                format=format,
                min_score=min_score,
                max_score=max_score,
                smart_score_bucket=smart_score_bucket,
                resolution_bucket=resolution_bucket,
                face_filter=face_filter,
                impossible_sources=impossible_sources,
                hidden_tags_filter=hidden_tags,
                guest_session_id=guest_session_id,
                guest_token_id=guest_token_id,
                **query_params,
            )
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
            impossible_sources=impossible_sources,
            hidden_tags_filter=hidden_tags,
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
                stack_leaders_only=stack_leaders_only,
                format=format,
                include_unimported=True,
                min_score=min_score,
                max_score=max_score,
                smart_score_bucket=smart_score_bucket,
                resolution_bucket=resolution_bucket,
                file_path_prefix=file_path_prefix,
                face_filter=face_filter,
                impossible_sources=impossible_sources,
                hidden_tags_filter=hidden_tags,
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
            impossible_sources=impossible_sources,
            hidden_tags_filter=hidden_tags,
            guest_session_id=guest_session_id,
            guest_token_id=guest_token_id,
            **query_params,
        )
        pics = _record_sql_count(pics)
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
        response_model=list[GridPicture],
        responses={400: {"description": "Invalid sort mechanism."}},
    )
    def list_pictures(
        request: Request,
        sort: str = Query(
            None,
            description="Sort mechanism. One of: DATE, IMPORTED_AT, SCORE, "
            "SMART_SCORE, IMAGE_SIZE, TEXT_CONTENT, CHARACTER_LIKENESS. "
            "Omit for natural (id) order.",
            examples=["DATE"],
        ),
        descending: bool = Query(
            True, description="Sort direction; true = newest/highest first."
        ),
        offset: int = Query(0, description="Number of rows to skip (pagination)."),
        limit: int = Query(
            sys.maxsize, description="Maximum rows to return.", examples=[200]
        ),
        fields: str = Query(
            None,
            description="Field projection: 'grid' for the minimal grid set, a "
            "comma-separated field list, or omit for full metadata.",
            examples=["grid"],
        ),
        project_id: str | None = Query(
            None,
            description="Filter by project id or 'UNASSIGNED'",
            examples=["UNASSIGNED"],
        ),
        only_deleted: bool = Query(
            False,
            description="Return only deleted (scrapheap) pictures. "
            "Replaces the deprecated character_id=SCRAPHEAP.",
        ),
        filters: PictureListFilters = Depends(),
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
        scope_picture_id = (
            token_scope.resource_id
            if token_scope is not None and token_scope.resource_type == "picture"
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
            scope_picture_id=scope_picture_id,
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
        response_model=StreamPicturesResponse,
        responses={
            400: {"description": "Invalid sort mechanism."},
        },
    )
    def stream_pictures(
        request: Request,
        sort: str = Query(
            None,
            description="Sort mechanism (see /pictures). Omit for natural order.",
            examples=["DATE"],
        ),
        descending: bool = Query(
            True, description="Sort direction; true = newest/highest first."
        ),
        offset: int = Query(
            0, ge=0, description="Row offset for this batch (use next_offset)."
        ),
        batch_limit: int = Query(
            1000,
            ge=1,
            le=5000,
            description="Rows per batch (1-5000).",
            examples=[1000],
        ),
        fields: str = Query(
            None,
            description="Field projection: 'grid', a comma list, or omit.",
            examples=["grid"],
        ),
        grid_lite: bool = Query(
            False,
            description=(
                "When `fields=grid`, omit high-cardinality string fields "
                "such as `file_path` to reduce payload size for streaming."
            ),
        ),
        stack_leaders_only: bool = Query(
            False, description="Collapse stacks to their leader only."
        ),
        project_id: str | None = Query(
            None,
            description="Filter by project id or 'UNASSIGNED'",
            examples=["UNASSIGNED"],
        ),
        only_deleted: bool = Query(
            False,
            description="Return only deleted (scrapheap) pictures. "
            "Replaces the deprecated character_id=SCRAPHEAP.",
        ),
        filters: PictureListFilters = Depends(),
    ):
        if fields == "grid":
            metadata_fields = list(Picture.grid_fields())
            if grid_lite:
                metadata_fields = [f for f in metadata_fields if f != "file_path"]
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
        scope_picture_id = (
            token_scope.resource_id
            if token_scope is not None and token_scope.resource_type == "picture"
            else None
        )
        # Mirror the /pictures endpoint: fields=grid implies stack_leaders_only.
        effective_stack_leaders_only = stack_leaders_only or (fields == "grid")
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
            stack_leaders_only=effective_stack_leaders_only,
            project_id=project_id,
            scope_set_id=scope_set_id,
            scope_character_id=scope_character_id,
            scope_picture_id=scope_picture_id,
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
            "set used by `/pictures` and `/pictures/stream`."
        ),
        response_model=PictureCountResponse,
    )
    def count_pictures(
        request: Request,
        sort: str = Query(
            None,
            description="Sort mechanism; only affects whether a count is "
            "computable (CHARACTER_LIKENESS returns null).",
            examples=["DATE"],
        ),
        descending: bool = Query(
            True, description="Unused for counting; accepted for parity with /pictures."
        ),
        fields: str = Query(
            None, description="Unused for counting; accepted for parity."
        ),
        stack_leaders_only: bool = Query(
            False, description="Count stacks as one (their leader)."
        ),
        project_id: str | None = Query(
            None,
            description="Filter by project id or 'UNASSIGNED'",
            examples=["UNASSIGNED"],
        ),
        only_deleted: bool = Query(
            False,
            description="Return only deleted (scrapheap) pictures. "
            "Replaces the deprecated character_id=SCRAPHEAP.",
        ),
        filters: PictureListFilters = Depends(),
    ):
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
        scope_picture_id = (
            token_scope.resource_id
            if token_scope is not None and token_scope.resource_type == "picture"
            else None
        )
        # Use count_only=True to run a fast SELECT COUNT(*) rather than fetching all rows.
        # The count may be a small over-estimate for deployments with hidden-tag post-filtering,
        sort_mech = (
            SortMechanism.from_string(sort, descending=descending) if sort else None
        )
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
            stack_leaders_only=stack_leaders_only,
            project_id=project_id,
            scope_set_id=scope_set_id,
            scope_character_id=scope_character_id,
            scope_picture_id=scope_picture_id,
        )
        return {"count": count}
