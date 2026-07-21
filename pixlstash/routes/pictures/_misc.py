import os
import subprocess
import sys
from collections import defaultdict, deque
from datetime import datetime

from fastapi import (
    Body,
    HTTPException,
    Query,
    Request,
)
from sqlalchemy import (
    bindparam,
    text,
)
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, select
from typing import Optional

from pixlstash.db_models import (
    Face,
    Picture,
    PictureLikeness,
    SortMechanism,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.picture_scoring import (
    fetch_smart_score_data,
    get_smart_score_penalised_tags_from_request,
    prepare_smart_score_inputs,
)
from pixlstash.utils.quality.smart_score_utils import SmartScoreUtils
from pixlstash.utils.service.serialization_utils import safe_model_dict
from pixlstash.services import plugin_service
from pixlstash.utils.service.picture_stats import (
    PictureStatsParams,
    get_cached_picture_stats,
)
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
    _fetch_hidden_picture_ids,
)


logger = get_logger(__name__)


class SortMechanismResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    key: str
    field: Optional[str] = None
    description: Optional[str] = None


class ImagePluginListResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    plugins: list = []
    plugin_errors: list = []
    plugin_dirs: dict = {}


class PicturePluginRunResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str


class LikenessGroupResponse(BaseModel):
    """A picture row belonging to a computed likeness group.

    Picture metadata is large and dynamic; common fields are enumerated and
    ``extra="allow"`` preserves the rest (including the ``stack_index`` and
    ``smartScore`` overlays added by the handler)."""

    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    score: Optional[int] = None
    stack_index: Optional[int] = None
    smartScore: Optional[float] = None


class PictureStatsResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    total: Optional[int] = None
    total_tags: Optional[int] = None
    tagged: Optional[int] = None
    untagged: Optional[int] = None
    avg_tags_per_image: Optional[float] = None
    top_tags: Optional[list] = None
    top_cooccurrences: Optional[list] = None
    confidence_histogram: Optional[list] = None
    regular_tags: Optional[list] = None
    score_distribution: Optional[list] = None
    smart_score_distribution: Optional[list] = None
    resolution_distribution: Optional[list] = None


class OpenLocationResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str


def register_routes(router, server):
    @router.get(
        "/sort_mechanisms",
        summary="List picture sort mechanisms",
        description="Returns all available sorting keys and direction semantics supported by picture listing and search endpoints.",
        response_model=list[SortMechanismResponse],
    )
    def get_pictures_sort_mechanisms():
        """Return available sorting mechanisms for pictures."""
        result = SortMechanism.all()
        logger.debug("Returning sort mechanisms: {}".format(result))
        return result

    @router.get(
        "/pictures/plugins",
        include_in_schema=False,
        summary="List image plugins",
        description="Lists available image plugins and their parameter schemas.",
        response_model=ImagePluginListResponse,
    )
    def list_picture_plugins():
        return plugin_service.list_plugins(server.vault)

    @router.post(
        "/pictures/plugins/{name}",
        include_in_schema=False,
        summary="Run image plugin",
        description="Runs a named image plugin on selected pictures and imports the outputs. By default each output is placed in its source picture's stack; pass stack=false to skip stacking while still inheriting the source's set/project/face associations.",
        response_model=PicturePluginRunResponse,
    )
    async def run_picture_plugin(
        request: Request, name: str, payload: dict = Body(...)
    ):
        # Capture the originating tab's client id at request entry; the plugin
        # output import echoes it so that tab can do a targeted grid insert.
        origin_client_id = getattr(request.state, "origin_client_id", None)
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

        # Scope guard (BOLA): a write-capable resource-scoped token may only run
        # plugins on pictures within its granted resource. None == owner /
        # unscoped == no filter. This is all-or-nothing (403 if *any* requested
        # id is out of scope) rather than a silent partial drop, because the
        # optional positional `captions` array below is aligned 1:1 to
        # picture_ids — dropping ids here would misalign captions to the wrong
        # pictures.
        scope_allowed = fetch_scope_allowed_picture_ids(server, request)
        if scope_allowed is not None and any(
            pid not in scope_allowed for pid in picture_ids
        ):
            raise HTTPException(
                status_code=403,
                detail="Token is not authorised to access these pictures",
            )

        parameters = payload.get("parameters") or {}
        if not isinstance(parameters, dict):
            raise HTTPException(status_code=400, detail="parameters must be an object")

        # Optional physical stacking of plugin outputs. Default true preserves
        # the historical behaviour; when false, outputs skip the stack but still
        # inherit the source's set/project/face associations.
        stack = bool(payload.get("stack", True))

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
                server,
                name,
                picture_ids,
                parameters,
                captions,
                origin_client_id=origin_client_id,
                stack=stack,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except RuntimeError as exc:
            raise HTTPException(status_code=500, detail=f"Plugin failed: {exc}")

    @router.get(
        "/pictures/comfyui_models",
        include_in_schema=False,
        summary="List distinct ComfyUI model names",
        response_model=list[str],
    )
    def get_comfyui_models(request: Request):
        # Scope guard (BOLA): a READ-scoped share token may only see the model
        # vocabulary drawn from pictures within its granted resource. None ==
        # owner / unscoped == no filter (full list). An empty set means the
        # scope matched nothing, so return nothing rather than the full list.
        allowed = fetch_scope_allowed_picture_ids(server, request)
        if allowed is not None and not allowed:
            return []
        allowed_ids = list(allowed) if allowed is not None else None

        def fetch(session):
            sql = (
                "SELECT DISTINCT j.value FROM picture p, json_each(p.comfyui_models) j "
                "WHERE p.comfyui_models IS NOT NULL AND p.comfyui_models != '[]' "
                "AND p.deleted = 0"
            )
            if allowed_ids is not None:
                sql += " AND p.id IN :allowed_ids"
            sql += " ORDER BY j.value"
            stmt = text(sql)
            if allowed_ids is not None:
                stmt = stmt.bindparams(
                    bindparam("allowed_ids", value=allowed_ids, expanding=True)
                )
            rows = session.execute(stmt).all()
            return [r[0] for r in rows if r and r[0]]

        return server.vault.db.run_immediate_read_task(fetch)

    @router.get(
        "/pictures/comfyui_loras",
        include_in_schema=False,
        summary="List distinct ComfyUI LoRA names",
        response_model=list[str],
    )
    def get_comfyui_loras(request: Request):
        # Scope guard (BOLA): a READ-scoped share token may only see the LoRA
        # vocabulary drawn from pictures within its granted resource. None ==
        # owner / unscoped == no filter (full list). An empty set means the
        # scope matched nothing, so return nothing rather than the full list.
        allowed = fetch_scope_allowed_picture_ids(server, request)
        if allowed is not None and not allowed:
            return []
        allowed_ids = list(allowed) if allowed is not None else None

        def fetch(session):
            sql = (
                "SELECT DISTINCT j.value FROM picture p, json_each(p.comfyui_loras) j "
                "WHERE p.comfyui_loras IS NOT NULL AND p.comfyui_loras != '[]' "
                "AND p.deleted = 0"
            )
            if allowed_ids is not None:
                sql += " AND p.id IN :allowed_ids"
            sql += " ORDER BY j.value"
            stmt = text(sql)
            if allowed_ids is not None:
                stmt = stmt.bindparams(
                    bindparam("allowed_ids", value=allowed_ids, expanding=True)
                )
            rows = session.execute(stmt).all()
            return [r[0] for r in rows if r and r[0]]

        return server.vault.db.run_immediate_read_task(fetch)

    @router.get(
        "/pictures/likeness-groups",
        include_in_schema=False,
        summary="List computed likeness groups",
        description="Builds groups from likeness edges using filtering options such as character, set, format, and threshold.",
        response_model=list[LikenessGroupResponse],
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
        set_filter_ids = collect_set_filter_ids(
            set_id_value=set_id,
            set_ids_values=set_ids,
        )
        normalized_set_mode = normalize_set_mode(set_mode)

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
                    query = query.where(project_unassigned_clause(Picture))
                else:
                    try:
                        parsed_project_id = int(project_id_value)
                    except (TypeError, ValueError):
                        raise HTTPException(
                            status_code=400,
                            detail="Invalid project_id",
                        )
                    query = query.where(
                        project_membership_exists_clause(parsed_project_id, Picture)
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

        # Enforce token scope: scoped tokens must not see pictures outside
        # their authorised resource (picture_set, character, project).
        scope_allowed = fetch_scope_allowed_picture_ids(server, request)
        if scope_allowed is not None:
            candidate_ids = candidate_ids & scope_allowed

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
                # This post-filter narrows an already-id-bounded set; only the
                # deleted lifecycle clause plus score/comfyui/tag predicates apply
                # (no format/buckets/face — those were handled when the candidate
                # set was built).  All compiled by the shared PredicateFilter.
                query = PredicateFilter(
                    min_score=min_score_value,
                    max_score=max_score_value,
                    comfyui_models_filter=comfyui_models_filter_value,
                    comfyui_loras_filter=comfyui_loras_filter_value,
                    tags_filter=tags_filter_value,
                    tags_rejected_filter=tags_rejected_filter_value,
                    tags_confidence_above_filter=tags_confidence_above_filter_value,
                    tags_confidence_below_filter=tags_confidence_below_filter_value,
                    only_deleted=deleted_only,
                ).apply(query)
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
                good_anchors, bad_anchors, candidates, tag_precisions = (
                    fetch_smart_score_data(
                        server,
                        None,
                        candidate_ids=ordered_ids,
                        penalised_tags=penalised_tags,
                    )
                )
                if candidates:
                    good_list, bad_list, cand_list, cand_ids = (
                        prepare_smart_score_inputs(
                            good_anchors, bad_anchors, candidates
                        )
                    )
                    if cand_list:
                        scores = SmartScoreUtils.calculate_smart_score_batch_numpy(
                            cand_list,
                            good_list,
                            bad_list,
                            config={"tag_precisions": tag_precisions},
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
        include_in_schema=False,
        summary="Open picture location",
        description="Opens the containing folder of a reference picture in the OS file manager.",
        response_model=OpenLocationResponse,
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
        include_in_schema=False,
        summary="Get picture statistics",
        description="Returns tag statistics for the current view filter, cached for 60 seconds.",
        response_model=PictureStatsResponse,
    )
    def get_picture_stats(request: Request):
        cache_key = str(sorted(request.query_params.multi_items()))

        # Include scope identity in the cache key to prevent cross-token cache
        # poisoning when different scoped tokens share the same query params.
        token_scope = getattr(request.state, "token_scope", None)
        if token_scope is not None and token_scope.resource_type is not None:
            cache_key += f"|scope:{token_scope.resource_type}:{token_scope.resource_id}"

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
        # Intrinsic-attribute query params via the shared parser.  Membership / scope
        # params keep their bespoke handling, and min/max score keep their dedicated
        # HTTPException-on-invalid coercion below.
        _predicate_filter = PredicateFilter.from_query_params(request)
        tags_filter = _predicate_filter.tags_filter or []
        rejected_tags = _predicate_filter.tags_rejected_filter or []
        format_filter = _predicate_filter.format or []
        min_score_raw = request.query_params.get("min_score")
        max_score_raw = request.query_params.get("max_score")
        smart_score_bucket = _predicate_filter.smart_score_bucket
        resolution_bucket = _predicate_filter.resolution_bucket
        file_path_prefix = _predicate_filter.file_path_prefix
        import_source_folder = _predicate_filter.import_source_folder
        face_filter = _predicate_filter.face_filter
        confidence_tag = request.query_params.get("confidence_tag") or None
        confidence_above = _predicate_filter.tags_confidence_above_filter or []
        confidence_below = _predicate_filter.tags_confidence_below_filter or []
        include = set(request.query_params.getlist("include"))

        only_deleted = character_id_raw == "SCRAPHEAP"
        if only_deleted:
            character_id_raw = None
        set_mode = normalize_set_mode(set_mode_raw)
        set_filter_ids = collect_set_filter_ids(
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

        # Enforce token scope: compute allowed picture IDs for scoped tokens so
        # that stats are restricted to the authorised resource only.
        scope_allowed = fetch_scope_allowed_picture_ids(server, request)
        scoped_picture_ids = list(scope_allowed) if scope_allowed is not None else None

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
            scoped_picture_ids=scoped_picture_ids,
        )
        return get_cached_picture_stats(server.vault, params, cache_key)
