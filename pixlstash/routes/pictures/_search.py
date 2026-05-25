import re
import sys

from fastapi import (
    HTTPException,
    Query,
    Request,
)
from sqlmodel import select

from pixlstash.db_models import (
    Face,
    Picture,
    SortMechanism,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.service.filter_helpers import (
    collect_set_filter_ids,
    fetch_scope_allowed_picture_ids,
    fetch_set_candidate_ids,
    normalize_set_mode,
    project_membership_exists_clause,
    project_unassigned_clause,
)

from ._helpers import (
    _fetch_hidden_picture_ids,
)


logger = get_logger(__name__)


def register_routes(router, server):
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
        normalized_set_mode = normalize_set_mode(set_mode)
        set_filter_ids = collect_set_filter_ids(
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
                fetch_set_candidate_ids,
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
                    query_stmt = query_stmt.where(project_unassigned_clause(Picture))
                else:
                    try:
                        parsed_project_id = int(project_id_value)
                    except (TypeError, ValueError):
                        raise HTTPException(
                            status_code=400,
                            detail="Invalid project_id",
                        )
                    query_stmt = query_stmt.where(
                        project_membership_exists_clause(parsed_project_id, Picture)
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

        # Token scope enforcement: restrict candidate_ids to the pictures
        # allowed by the token's resource scope.  This prevents a scoped
        # token (e.g. picture_set, project, character) from seeing pictures
        # outside its authorised scope by passing arbitrary filter params.
        scope_allowed = fetch_scope_allowed_picture_ids(server, request)
        if scope_allowed is not None:
            candidate_ids = (
                scope_allowed
                if candidate_ids is None
                else candidate_ids & scope_allowed
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
