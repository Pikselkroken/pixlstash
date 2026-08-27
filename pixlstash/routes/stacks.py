from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Request, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case

from sqlmodel import Session, select

from pixlstash.db_models import Picture, PictureStack, SortMechanism
from pixlstash.services import keep_cover_only_service, operation_log_service
from pixlstash.services.keep_cover_only_service import KeepCoverOnlyError
from pixlstash.services.set_lock_service import (
    enforce_stack_detach_not_locked,
    enforce_stack_membership_not_locked,
)
from pixlstash.services.stack_membership import reconcile_stack_membership
from pixlstash.stacking import normalize_stack_positions
from pixlstash.scoring import (
    fetch_smart_score_data,
    get_smart_score_penalised_tags_from_request,
    prepare_smart_score_inputs,
)
from pixlstash.utils.quality.smart_score_utils import SmartScoreUtils
from pixlstash.utils.request_origin import require_client_batch_id
from pixlstash.utils.serialization_utils import safe_model_dict
from pixlstash.utils.service.filter_helpers import (
    fetch_scope_allowed_picture_ids,
    narrow_picture_project_ids,
)
from pixlstash.utils.service.scope_table import scope_id_subquery
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


class StackResponse(BaseModel):
    """Stack metadata plus its ordered picture ids.

    Also covers the unstacked response from ``get_stack_for_picture``
    (``{"stack_id": None, "picture_ids": []}``) and the stack-deleted response
    from ``remove_stack_members`` (``{"status", "stack_id": None,
    "picture_ids"}``), so all fields are optional and ``extra="allow"``.
    """

    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    name: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    picture_ids: Optional[list[int]] = None
    stack_id: Optional[int] = None
    status: Optional[str] = None


class StackOrderResponse(BaseModel):
    """Stack id with the resulting ordered picture ids."""

    model_config = ConfigDict(extra="allow")

    stack_id: int
    picture_ids: list[int]


class KeepCoverOnlyRequest(BaseModel):
    """The selection a Keep-cover-only call acts on.

    Send `stack_ids`, `picture_ids`, or both: they are unioned. At least one
    must be non-empty. The **unit is the stack**: any picture named pulls in its
    whole stack, so a partial selection inside a stack collapses the whole
    stack. Loose (unstacked) pictures name no stack and are ignored.
    """

    model_config = ConfigDict(extra="forbid")

    stack_ids: Optional[list[int]] = Field(
        default=None,
        description=(
            "Stacks to collapse, named directly. Prefer this when the client "
            "already knows the stack ids: it is the exact unit the action "
            "works on. Example: `[12, 19]`."
        ),
        examples=[[12, 19]],
    )
    picture_ids: Optional[list[int]] = Field(
        default=None,
        description=(
            "Pictures whose stacks should be collapsed: normally the grid "
            "selection, which is usually just the visible stack leaders. Each "
            "one resolves to its stack; soft-deleted pictures and loose "
            "pictures contribute nothing. Example: `[101, 102, 250]`."
        ),
        examples=[[101, 102, 250]],
    )
    batch_id: Optional[str] = Field(
        default=None,
        description=(
            "Operation-log batch id, so one user gesture that fans out into "
            "several requests stays a single undo. Must be client-namespaced: "
            "`cli-` followed by 4-76 characters of `A-Z a-z 0-9 _ -`, anything "
            "else is a `400`. Omit it and the server mints one (`srv-…`); the "
            "`X-Operation-Batch-Id` header is honoured too. Example: "
            "`cli-8f2c1a90`."
        ),
        examples=["cli-8f2c1a90"],
    )


class KeepCoverOnlyStackRow(BaseModel):
    """One stack in the dry run: what would happen to it, and why."""

    model_config = ConfigDict(extra="allow")

    stack_id: int = Field(
        description="The stack this row describes. Example: `12`.", examples=[12]
    )
    cover_picture_id: int = Field(
        description=(
            "The stack's **current** leader, which is what would be kept. This "
            "action never picks a new cover. Example: `101`."
        ),
        examples=[101],
    )
    member_count: int = Field(
        description=(
            "Live members of the stack, cover included. Soft-deleted members "
            "are not counted: they are already in the Scrapheap. Example: `4`."
        ),
        examples=[4],
    )
    copy_picture_ids: list[int] = Field(
        default_factory=list,
        description=(
            "The non-cover live members that would move to the Scrapheap. "
            "**Empty on a skipped stack**, because a skipped stack moves "
            "nothing at all. Example: `[102, 103, 104]`."
        ),
        examples=[[102, 103, 104]],
    )
    reference_folder_picture_ids: list[int] = Field(
        default_factory=list,
        description=(
            "The subset of `copy_picture_ids` that live in a reference folder. "
            "A **subset**, not a separate bucket: their rows move like any "
            "other, but their files are user-managed and are never touched. "
            "Example: `[104]`."
        ),
        examples=[[104]],
    )
    bytes_held_by_copies: int = Field(
        description=(
            "Bytes on disk held by `copy_picture_ids`. **Held, not freed**, a "
            "soft delete frees nothing; see the response-level field of the "
            "same name. Example: `7340032`."
        ),
        examples=[7340032],
    )
    cover_gains_tags: bool = Field(
        description=(
            "The metadata union would copy at least one tag from a copy onto "
            "the cover. Example: `true`."
        ),
        examples=[True],
    )
    cover_gains_score: bool = Field(
        description=(
            "The metadata union would lift the cover's rating to the stack's "
            "best. Example: `false`."
        ),
        examples=[False],
    )
    eligible: bool = Field(
        description=(
            "Whether this stack would actually collapse. Exactly the inverse "
            "of `skip_reason` being set. Example: `true`."
        ),
        examples=[True],
    )
    skip_reason: Optional[str] = Field(
        default=None,
        description=(
            "Why this stack is skipped, or `null` when it is eligible. One of "
            "`set_locked` (a live member is frozen by a locked picture set, "
            "the **whole** stack is refused, never one member), "
            "`character_only_on_copy` (a character link exists only on a "
            "member that would leave, and the union will not guess across "
            "several characters), or `single_member` (fewer than two live "
            "members, so there is nothing to collapse). Example: `set_locked`."
        ),
        examples=["set_locked"],
    )
    locked_sets: list[dict] = Field(
        default_factory=list,
        description=(
            "`[{id, name}, …]` locked picture sets freezing this stack. "
            "Non-empty only when `skip_reason` is `set_locked`, so the dialog "
            "can name the set the user has to unlock. "
            'Example: `[{"id": 3, "name": "Portfolio 2026"}]`.'
        ),
        examples=[[{"id": 3, "name": "Portfolio 2026"}]],
    )
    lost_characters: list[dict] = Field(
        default_factory=list,
        description=(
            "`[{id, name, picture_ids}, …]` naming each character whose only "
            "link sits on a copy, and the copies carrying it. Non-empty only "
            "when `skip_reason` is `character_only_on_copy`. "
            'Example: `[{"id": 7, "name": "Ada", "picture_ids": [103]}]`.'
        ),
        examples=[[{"id": 7, "name": "Ada", "picture_ids": [103]}]],
    )


class KeepCoverOnlyPreviewResponse(BaseModel):
    """The dry run: every figure the confirm dialog renders, from one read."""

    model_config = ConfigDict(extra="allow")

    stacks_selected: int = Field(
        description=(
            "Stacks the selection resolved to. The four bucket counts below "
            "are disjoint and sum to exactly this. Example: `20`."
        ),
        examples=[20],
    )
    stacks_eligible: int = Field(
        description=(
            "Stacks that would collapse: the figure the button acts on. Example: `17`."
        ),
        examples=[17],
    )
    stacks_skipped_locked: int = Field(
        description=(
            "Stacks refused whole because a live member is frozen by a locked "
            "picture set. Example: `2`."
        ),
        examples=[2],
    )
    stacks_skipped_character_on_copy: int = Field(
        description=(
            "Stacks skipped because a character link sits only on a copy, "
            "which collapsing would destroy. Example: `1`."
        ),
        examples=[1],
    )
    stacks_skipped_single_member: int = Field(
        description=(
            "Stacks with fewer than two live members: nothing to collapse. "
            "Example: `0`."
        ),
        examples=[0],
    )
    pictures_moving: int = Field(
        description=(
            "The headline figure: pictures that would move to the Scrapheap. "
            "Counted over the eligible stacks only, so it never includes a "
            "skipped stack's members. Example: `414`."
        ),
        examples=[414],
    )
    picture_ids_moving: list[int] = Field(
        default_factory=list,
        description=(
            "The ids behind `pictures_moving`, so the grid can mark exactly "
            "the cards that are about to go. Example: `[102, 103, 104]`."
        ),
        examples=[[102, 103, 104]],
    )
    covers_kept: int = Field(
        description=(
            "Covers that survive: one per eligible stack, by construction. "
            "Example: `17`."
        ),
        examples=[17],
    )
    cover_picture_ids: list[int] = Field(
        default_factory=list,
        description="The kept covers, one per eligible stack. Example: `[101]`.",
        examples=[[101]],
    )
    covers_gaining_tags: int = Field(
        description=(
            "Eligible stacks whose cover would gain at least one tag from a "
            "copy. Example: `11`."
        ),
        examples=[11],
    )
    covers_gaining_score: int = Field(
        description=(
            "Eligible stacks whose cover's rating would be lifted to the "
            "stack's best. Example: `3`."
        ),
        examples=[3],
    )
    covers_gaining_metadata: int = Field(
        description=(
            "Eligible stacks whose cover gains tags **or** score, the union "
            "of the two counts above, and the row the dialog shows. Not their "
            "sum: a cover can gain both. Example: `12`."
        ),
        examples=[12],
    )
    reference_folder_pictures_moving: int = Field(
        description=(
            "How many of `pictures_moving` live in a reference folder. A "
            "**subset** of that count, reported separately because their files "
            "are user-managed and are not touched. Example: `5`."
        ),
        examples=[5],
    )
    reference_folder_picture_ids_moving: list[int] = Field(
        default_factory=list,
        description="The ids behind the count above. Example: `[104]`.",
        examples=[[104]],
    )
    bytes_held_by_copies: int = Field(
        description=(
            "Bytes on disk held by `picture_ids_moving`. **Deliberately not "
            "named `bytes_freed`, and it must never be presented as freed or "
            "reclaimed space.** A soft delete frees nothing: the files stay "
            "until the Scrapheap is emptied, and with "
            "`scrapheap_retention_days: null` it never empties on its own. "
            "Render it as a sentence about what could later be reclaimed, "
            "never as a figure. Example: `1234567890`."
        ),
        examples=[1234567890],
    )
    originals_deleted_from_disk: int = Field(
        description=(
            "Always `0`, stated out loud rather than left implied; this "
            "action has no path to disk at all. Example: `0`."
        ),
        examples=[0],
    )
    scrapheap_retention_days: Optional[int] = Field(
        default=None,
        description=(
            "The **live** `scrapheap_retention_days` setting, so the recovery "
            "copy never hardcodes a window. `null` means **Never**: auto-purge "
            "is off and the Scrapheap does not empty on its own and `null` "
            "is the default on a fresh install. Example: `30`."
        ),
        examples=[30],
    )
    unknown_stack_ids: list[int] = Field(
        default_factory=list,
        description=(
            "Explicitly named `stack_ids` that resolve to no live stack. "
            "Reported outside the bucket arithmetic, because they are not "
            "stacks. Example: `[999]`."
        ),
        examples=[[999]],
    )
    stacks: list[KeepCoverOnlyStackRow] = Field(
        default_factory=list,
        description=(
            "One row per selected stack, eligible and skipped alike, so the "
            "dialog's rows and its headline come from the same read. Ordered "
            "by stack id."
        ),
    )


class KeepCoverOnlyResponse(BaseModel):
    """What a Keep-cover-only call actually did."""

    model_config = ConfigDict(extra="allow")

    status: str = Field(
        description="`success` when the call completed. Example: `success`.",
        examples=["success"],
    )
    stacks_collapsed: int = Field(
        description="Stacks that kept their cover and shed their copies. Example: `17`.",
        examples=[17],
    )
    stack_ids_collapsed: list[int] = Field(
        default_factory=list,
        description="The ids behind `stacks_collapsed`. Example: `[12, 19]`.",
        examples=[[12, 19]],
    )
    pictures_moved: int = Field(
        description=(
            "Pictures soft-deleted to the Scrapheap: the number the receipt "
            "names. Example: `414`."
        ),
        examples=[414],
    )
    picture_ids_moved: list[int] = Field(
        default_factory=list,
        description=(
            "The ids behind `pictures_moved`. Each keeps its `stack_id`, so "
            "restoring one returns it to its stack rather than leaving it "
            "loose. Example: `[102, 103, 104]`."
        ),
        examples=[[102, 103, 104]],
    )
    cover_picture_ids: list[int] = Field(
        default_factory=list,
        description="The kept covers, one per collapsed stack. Example: `[101]`.",
        examples=[[101]],
    )
    covers_gaining_metadata: int = Field(
        description=(
            "Covers that gained tags or score from a copy in this run. Example: `12`."
        ),
        examples=[12],
    )
    tags_added: int = Field(
        description=(
            "Tag rows written by the metadata union across every collapsed "
            "stack, before anything was deleted. Example: `48`."
        ),
        examples=[48],
    )
    scores_lifted: int = Field(
        description=(
            "Pictures whose rating the union raised to the stack's best. Example: `3`."
        ),
        examples=[3],
    )
    reference_folder_pictures_moved: int = Field(
        description=(
            "How many of `pictures_moved` live in a reference folder. Their "
            "rows moved; their files were not touched. Example: `5`."
        ),
        examples=[5],
    )
    originals_deleted_from_disk: int = Field(
        description="Always `0`. Nothing was removed from disk. Example: `0`.",
        examples=[0],
    )
    stacks_skipped_locked: list[KeepCoverOnlyStackRow] = Field(
        default_factory=list,
        description=(
            "Stacks refused whole for a locked picture set, each naming the "
            "sets, so the receipt's second sentence can say which."
        ),
    )
    stacks_skipped_character_on_copy: list[KeepCoverOnlyStackRow] = Field(
        default_factory=list,
        description=(
            "Stacks skipped because a character link sits only on a copy, each "
            "naming the characters that would have been lost."
        ),
    )
    stacks_skipped_single_member: list[int] = Field(
        default_factory=list,
        description=(
            "Stack ids with fewer than two live members: nothing to do. Example: `[7]`."
        ),
        examples=[[7]],
    )
    unknown_stack_ids: list[int] = Field(
        default_factory=list,
        description="Named `stack_ids` that resolve to no live stack. Example: `[999]`.",
        examples=[[999]],
    )
    batch_id: Optional[str] = Field(
        default=None,
        description=(
            "The operation-log batch covering the whole call: pass it to "
            "`POST /operations/batches/{batch_id}/undo` to put every stack "
            "back at once. `null` when nothing was collapsed. "
            "Example: `srv-4c1d77e2`."
        ),
        examples=["srv-4c1d77e2"],
    )


def create_router(server) -> APIRouter:
    router = APIRouter()

    def _ensure_secure_when_required(request: Request):
        server.auth.ensure_secure_when_required(request)

    def _normalize_picture_ids(raw_ids) -> list[int]:
        if not isinstance(raw_ids, list):
            raise HTTPException(status_code=400, detail="picture_ids must be a list")
        ids = []
        for raw_id in raw_ids:
            try:
                ids.append(int(raw_id))
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400,
                    detail="picture_ids must be integers",
                )
        if not ids:
            raise HTTPException(status_code=400, detail="picture_ids must not be empty")
        return ids

    def _fetch_stack_pictures(session: Session, stack_id: int):
        stack_position_order = case(
            (Picture.stack_position.is_(None), 1),
            else_=0,
        )
        return session.exec(
            select(Picture)
            .where(Picture.stack_id == stack_id)
            .order_by(stack_position_order, Picture.stack_position, Picture.id)
        ).all()

    def _stack_order_key(pic, smart_score_by_id: dict[int, float]):
        score = pic.score or 0
        smart_score = smart_score_by_id.get(pic.id, 0.0)
        created_at = pic.created_at or datetime.min
        created_ts = created_at.timestamp() if isinstance(created_at, datetime) else 0.0
        return (-score, -smart_score, -created_ts, int(pic.id or 0))

    def _compute_smart_score_map(
        request: Request,
        picture_ids: list[int],
    ) -> dict[int, float]:
        if not picture_ids:
            return {}
        try:
            penalised_tags = get_smart_score_penalised_tags_from_request(
                server, request
            )
            good_anchors, bad_anchors, candidates, tag_precisions = (
                fetch_smart_score_data(
                    server,
                    None,
                    candidate_ids=picture_ids,
                    penalised_tags=penalised_tags,
                )
            )
            if candidates:
                good_list, bad_list, cand_list, cand_ids = prepare_smart_score_inputs(
                    good_anchors,
                    bad_anchors,
                    candidates,
                )
                if cand_list:
                    scores = SmartScoreUtils.calculate_smart_score_batch_numpy(
                        cand_list,
                        good_list,
                        bad_list,
                        config={"tag_precisions": tag_precisions},
                    )
                    return {
                        int(pid): float(score)
                        for pid, score in zip(cand_ids, scores)
                        if score is not None
                    }
        except Exception as exc:
            logger.warning("[stacks] Failed to compute smart scores: %s", exc)
        return {}

    def _ensure_stack_positions(
        request: Request,
        stack_id: int,
        pictures: list[Picture],
    ) -> list[Picture]:
        if not pictures:
            return pictures
        if any(pic.stack_position is not None for pic in pictures):
            return sorted(
                pictures,
                key=lambda pic: (
                    pic.stack_position is None,
                    pic.stack_position or 0,
                    int(pic.id or 0),
                ),
            )

        smart_score_by_id = _compute_smart_score_map(
            request,
            [pic.id for pic in pictures if pic.id is not None],
        )
        ordered = sorted(
            pictures,
            key=lambda pic: _stack_order_key(pic, smart_score_by_id),
        )
        ordered_ids = [pic.id for pic in ordered if pic.id is not None]

        def update_positions(
            session: Session, stack_id_value: int, ordered_ids_value: list[int]
        ):
            stack = session.get(PictureStack, stack_id_value)
            if stack is None:
                return
            pics = session.exec(
                select(Picture).where(Picture.stack_id == stack_id_value)
            ).all()
            pic_by_id = {pic.id: pic for pic in pics}
            for idx, pic_id in enumerate(ordered_ids_value):
                pic = pic_by_id.get(pic_id)
                if pic is None:
                    continue
                pic.stack_position = idx
                session.add(pic)
            stack.updated_at = datetime.utcnow()
            session.add(stack)
            session.commit()

        if ordered_ids:
            server.vault.db.run_task(update_positions, stack_id, ordered_ids)

        return ordered

    def _compact_stack_positions_in_session(session: Session, stack_id: int) -> None:
        """Re-number all pictures in a stack to contiguous 0..N-1 positions.

        Thin wrapper around :func:`pixlstash.stacking.normalize_stack_positions`,
        the single canonical implementation of the position-0 invariant.  The
        caller is responsible for committing after this call.
        """
        normalize_stack_positions(session, stack_id)

    @router.get(
        "/stacks/{stack_id}",
        summary="Get stack details",
        description="Returns stack metadata and ordered picture ids for a stack.",
        response_model=StackResponse,
    )
    def get_stack(stack_id: int, request: Request):
        _ensure_secure_when_required(request)
        server.auth.require_user_id(request)

        def fetch_stack(session: Session, stack_id: int):
            stack = session.get(PictureStack, stack_id)
            if not stack:
                return None, []
            pictures = _fetch_stack_pictures(session, stack_id)
            return stack, pictures

        stack, pictures = server.vault.db.run_task(fetch_stack, stack_id)
        if not stack:
            raise HTTPException(status_code=404, detail="Stack not found")

        # Scope guard (BOLA): a resource-scoped token only sees stack members
        # within its grant; if none are in scope, the stack is not visible to it.
        scope_allowed = fetch_scope_allowed_picture_ids(server, request)
        if scope_allowed is not None:
            pictures = [pic for pic in pictures if pic.id in scope_allowed]
            if not pictures:
                raise HTTPException(status_code=404, detail="Stack not found")

        pictures = _ensure_stack_positions(request, stack_id, pictures)

        payload = safe_model_dict(stack)
        payload["picture_ids"] = [pic.id for pic in pictures]
        return payload

    @router.get(
        "/stacks/{stack_id}/pictures",
        summary="List pictures in stack",
        description="Returns ordered picture payloads for a stack using grid or metadata field sets.",
        response_model=list[dict],
    )
    def get_stack_pictures(
        stack_id: int,
        request: Request,
        fields: str = Query("grid"),
        include_deleted: bool = Query(False),
        sort: Optional[str] = Query(None),
        descending: bool = Query(True),
    ):
        _ensure_secure_when_required(request)
        server.auth.require_user_id(request)

        # Resolve sort mechanism; treat LIKENESS_GROUPS as "no sort" (stack order).
        sort_mech = None
        if sort:
            try:
                candidate = SortMechanism.from_string(sort, descending=descending)
                if candidate.key not in (
                    SortMechanism.Keys.LIKENESS_GROUPS,
                    SortMechanism.Keys.SMART_SCORE,
                ):
                    sort_mech = candidate
            except ValueError as exc:
                logger.debug("Unrecognised sort mechanism %r: %s", sort, exc)

        def fetch_stack_pictures(
            session: Session,
            stack_id_value: int,
            fields_value: str,
            include_deleted_value: bool,
            sort_mech_value,
        ):
            stack = session.get(PictureStack, stack_id_value)
            if not stack:
                return None, None

            select_fields = (
                Picture.grid_fields()
                if fields_value == "grid"
                else Picture.metadata_fields()
            )

            pictures = Picture.find(
                session,
                stack_id=stack_id_value,
                sort_mech=sort_mech_value,
                select_fields=select_fields,
                include_deleted=include_deleted_value,
            )
            return select_fields, pictures

        select_fields, pictures = server.vault.db.run_task(
            fetch_stack_pictures,
            stack_id,
            fields,
            include_deleted,
            sort_mech,
        )
        if select_fields is None:
            raise HTTPException(status_code=404, detail="Stack not found")
        # Scope guard (BOLA): restrict to stack members within the token's grant;
        # a scoped token with no in-scope members must not see the stack.
        scope_allowed = fetch_scope_allowed_picture_ids(server, request)
        if scope_allowed is not None:
            pictures = [pic for pic in pictures if pic.id in scope_allowed]
            if not pictures:
                raise HTTPException(status_code=404, detail="Stack not found")
        # Only apply stack-position ordering when no explicit sort is active;
        # this also persists positions for stacks that haven't been ordered yet.
        if sort_mech is None:
            pictures = _ensure_stack_positions(request, stack_id, pictures)
        rows = [
            {field: safe_model_dict(pic).get(field) for field in select_fields}
            for pic in pictures
        ]
        # `fields=grid` never selects `project_id`; anything else goes through
        # `metadata_fields()`, which does, and the route's `list[dict]` response
        # model filters nothing (issue #719, §16.6).
        narrow_picture_project_ids(server, request, rows)
        return rows

    @router.get(
        "/pictures/{picture_id}/stack",
        summary="Get picture's stack",
        description="Returns the stack containing a picture, or null stack information when unstacked.",
        response_model=StackResponse,
    )
    def get_stack_for_picture(picture_id: int, request: Request):
        _ensure_secure_when_required(request)
        server.auth.require_user_id(request)

        def fetch_stack_for_picture(session: Session, picture_id: int):
            pic = session.get(Picture, picture_id)
            if not pic or not pic.stack_id:
                return None, None, []
            stack = session.get(PictureStack, pic.stack_id)
            pictures = _fetch_stack_pictures(session, pic.stack_id)
            return pic.stack_id, stack, pictures

        stack_id, stack, pictures = server.vault.db.run_task(
            fetch_stack_for_picture, picture_id
        )
        if not stack_id or not stack:
            return {"stack_id": None, "picture_ids": []}

        # Scope guard (BOLA): a token not granted the queried picture must not
        # learn its stack; stack siblings outside the grant are filtered out.
        scope_allowed = fetch_scope_allowed_picture_ids(server, request)
        if scope_allowed is not None:
            if picture_id not in scope_allowed:
                raise HTTPException(status_code=404, detail="Stack not found")
            pictures = [pic for pic in pictures if pic.id in scope_allowed]

        pictures = _ensure_stack_positions(request, stack_id, pictures)

        payload = safe_model_dict(stack)
        payload["picture_ids"] = [pic.id for pic in pictures]
        return payload

    @router.post(
        "/stacks",
        summary="Create stack",
        description="Creates a new stack or reuses an existing compatible one and assigns provided pictures to it.",
        response_model=StackResponse,
    )
    def create_stack(payload: dict = Body(...), request: Request = None):
        _ensure_secure_when_required(request)
        server.auth.require_user_id(request)

        picture_ids = _normalize_picture_ids(payload.get("picture_ids") or [])
        name = payload.get("name")
        if name is not None and not isinstance(name, str):
            name = str(name)

        def create_or_assign_stack(
            session: Session,
            picture_ids: list[int],
            name: Optional[str],
        ) -> int:
            picture_scope = scope_id_subquery(
                session, picture_ids, name="_pixlstash_stack_route_picture_ids"
            )
            pictures = session.exec(
                select(Picture).where(Picture.id.in_(picture_scope))
            ).all()
            if len(pictures) != len(picture_ids):
                missing = sorted(set(picture_ids) - {pic.id for pic in pictures})
                raise HTTPException(
                    status_code=404,
                    detail=f"Pictures not found: {missing}",
                )

            # Stacks are set-membership-atomic, so stacking these pictures would
            # add each of them to every set any of them belongs to. Refuse up
            # front - before any row is written or committed below - if that
            # would grow a locked set.
            enforce_stack_membership_not_locked(
                session, picture_ids, None, "create a stack"
            )

            existing_stack_ids = {pic.stack_id for pic in pictures if pic.stack_id}
            if len(existing_stack_ids) > 1:
                # Merge: keep the stack whose leader appears first in the incoming
                # picture_ids order (frontend sends them sorted by grid position).
                ordered_stack_ids = []
                seen = set()
                for pid in picture_ids:
                    pic = next((p for p in pictures if p.id == pid), None)
                    if pic and pic.stack_id and pic.stack_id not in seen:
                        ordered_stack_ids.append(pic.stack_id)
                        seen.add(pic.stack_id)
                keeper_id = (
                    ordered_stack_ids[0]
                    if ordered_stack_ids
                    else min(existing_stack_ids)
                )
                stack = session.get(PictureStack, keeper_id)
                if stack is None:
                    raise HTTPException(status_code=404, detail="Stack not found")
                orphan_ids = existing_stack_ids - {keeper_id}
                # Shift orphan positions so they land after the keeper's last
                # explicitly-positioned member, preserving each stack's internal
                # order and avoiding duplicate position values.
                keeper_members = session.exec(
                    select(Picture).where(Picture.stack_id == keeper_id)
                ).all()
                shift_base = (
                    max(
                        (
                            m.stack_position
                            for m in keeper_members
                            if m.stack_position is not None
                        ),
                        default=-1,
                    )
                    + 1
                )
                for orphan_stack_id in orphan_ids:
                    orphan_members = session.exec(
                        select(Picture).where(Picture.stack_id == orphan_stack_id)
                    ).all()
                    orphan_max = max(
                        (
                            m.stack_position
                            for m in orphan_members
                            if m.stack_position is not None
                        ),
                        default=-1,
                    )
                    for i, member in enumerate(
                        sorted(
                            orphan_members,
                            key=lambda m: (
                                m.stack_position is None,
                                m.stack_position or 0,
                                int(m.id or 0),
                            ),
                        )
                    ):
                        member.stack_position = shift_base + i
                        member.stack_id = keeper_id
                        session.add(member)
                    shift_base += max(orphan_max + 1, len(orphan_members))
                    orphan_stack = session.get(PictureStack, orphan_stack_id)
                    if orphan_stack is not None:
                        session.delete(orphan_stack)
                session.flush()
            elif existing_stack_ids:
                stack_id = existing_stack_ids.pop()
                stack = session.get(PictureStack, stack_id)
                if stack is None:
                    raise HTTPException(status_code=404, detail="Stack not found")
            else:
                stack = PictureStack(name=name)
                session.add(stack)
                session.flush()
                session.refresh(stack)

            existing_positions = []
            if stack.id is not None:
                rows = session.exec(
                    select(Picture.stack_position).where(
                        Picture.stack_id == stack.id,
                        Picture.stack_position.is_not(None),
                    )
                ).all()
                existing_positions = [row for row in rows if row is not None]
            next_position = max(existing_positions) + 1 if existing_positions else None

            for pic in pictures:
                pic.stack_id = stack.id
                if next_position is not None and pic.stack_position is None:
                    pic.stack_position = next_position
                    next_position += 1
                session.add(pic)

            # Compact to guarantee unique, contiguous positions after any merge
            # or append that may have left gaps or duplicates.
            _compact_stack_positions_in_session(session, stack.id)

            # Stacks are atomic for project/set membership: reconcile the (newly
            # enlarged) stack to the union of its members' memberships.
            reconcile_stack_membership(session, stack.id)

            stack.updated_at = datetime.utcnow()
            session.add(stack)
            session.flush()
            if stack.id is None:
                raise HTTPException(status_code=500, detail="Failed to create stack")
            return stack.id

        def fetch_stack_payload(session: Session, stack_id_value: int) -> dict:
            stack = session.get(PictureStack, stack_id_value)
            if stack is None:
                raise HTTPException(status_code=404, detail="Stack not found")
            return safe_model_dict(stack)

        # Stacking is stack-atomic: a merge moves every member of the pictures'
        # pre-existing stacks and reconciles the whole stack's set/project
        # membership, so the snapshot has to cover those siblings too.
        stack_id, _operation = operation_log_service.run_recorded_metadata_task(
            server.vault,
            create_or_assign_stack,
            picture_ids,
            name,
            op_type="stacks.create",
            picture_ids=picture_ids,
            expand_stacks=True,
            summary=f"Stacked {len(picture_ids)} picture(s)",
            **operation_log_service.request_context(request),
        )
        pictures = server.vault.db.run_task(_fetch_stack_pictures, stack_id)
        pictures = _ensure_stack_positions(request, stack_id, pictures)
        payload = server.vault.db.run_task(fetch_stack_payload, stack_id)
        payload["picture_ids"] = [pic.id for pic in pictures]
        return payload

    @router.patch(
        "/stacks/{stack_id}/order",
        summary="Reorder stack",
        description="Sets explicit order for all members in a stack using a complete ordered id list.",
        response_model=StackOrderResponse,
    )
    def reorder_stack(
        stack_id: int, payload: dict = Body(...), request: Request = None
    ):
        _ensure_secure_when_required(request)
        server.auth.require_user_id(request)

        picture_ids = _normalize_picture_ids(payload.get("picture_ids") or [])
        unique_ids = list(dict.fromkeys(picture_ids))
        if len(unique_ids) != len(picture_ids):
            raise HTTPException(status_code=400, detail="picture_ids must be unique")

        def update_stack_order(
            session: Session, stack_id_value: int, ordered_ids: list[int]
        ):
            stack = session.get(PictureStack, stack_id_value)
            if stack is None:
                return None

            pics = session.exec(
                select(Picture).where(Picture.stack_id == stack_id_value)
            ).all()
            pic_by_id = {pic.id: pic for pic in pics}
            stack_ids = set(pic_by_id.keys())
            if stack_ids != set(ordered_ids):
                raise HTTPException(
                    status_code=400,
                    detail="picture_ids must include every picture in the stack",
                )

            for idx, pic_id in enumerate(ordered_ids):
                pic = pic_by_id.get(pic_id)
                if pic is None:
                    continue
                pic.stack_position = idx
                session.add(pic)

            stack.updated_at = datetime.utcnow()
            session.add(stack)
            session.commit()
            return ordered_ids

        result = server.vault.db.run_task(update_stack_order, stack_id, unique_ids)
        if result is None:
            raise HTTPException(status_code=404, detail="Stack not found")
        return {"stack_id": stack_id, "picture_ids": result}

    @router.post(
        "/stacks/{stack_id}/members",
        summary="Add stack members",
        description="Adds pictures to an existing stack while preventing cross-stack membership conflicts.",
        response_model=StackResponse,
    )
    def add_stack_members(
        stack_id: int, payload: dict = Body(...), request: Request = None
    ):
        _ensure_secure_when_required(request)
        server.auth.require_user_id(request)

        picture_ids = _normalize_picture_ids(payload.get("picture_ids") or [])

        def add_members(session: Session, stack_id: int, picture_ids: list[int]):
            stack = session.get(PictureStack, stack_id)
            if stack is None:
                raise HTTPException(status_code=404, detail="Stack not found")

            picture_scope = scope_id_subquery(
                session, picture_ids, name="_pixlstash_stack_route_picture_ids"
            )
            pictures = session.exec(
                select(Picture).where(Picture.id.in_(picture_scope))
            ).all()
            if len(pictures) != len(picture_ids):
                missing = sorted(set(picture_ids) - {pic.id for pic in pictures})
                raise HTTPException(
                    status_code=404,
                    detail=f"Pictures not found: {missing}",
                )

            conflicts = [
                pic.id for pic in pictures if pic.stack_id not in (None, stack_id)
            ]
            if conflicts:
                raise HTTPException(
                    status_code=409,
                    detail=f"Pictures already in another stack: {sorted(conflicts)}",
                )

            # Refuse before any mutation if joining this stack would add a member
            # to a locked set (stacks are set-membership-atomic).
            enforce_stack_membership_not_locked(
                session, picture_ids, stack_id, "add pictures to a stack"
            )

            existing_positions = []
            rows = session.exec(
                select(Picture.stack_position).where(
                    Picture.stack_id == stack_id,
                    Picture.stack_position.is_not(None),
                )
            ).all()
            existing_positions = [row for row in rows if row is not None]
            next_position = max(existing_positions) + 1 if existing_positions else None

            for pic in pictures:
                pic.stack_id = stack_id
                if next_position is not None and pic.stack_position is None:
                    pic.stack_position = next_position
                    next_position += 1
                session.add(pic)

            # Compact to guarantee unique, contiguous 0-based positions after the
            # append above (which may have left gaps, duplicates, or no position-0
            # member if the stack previously had NULL positions).
            _compact_stack_positions_in_session(session, stack_id)

            # Stacks are atomic for project/set membership: reconcile the (newly
            # enlarged) stack to the union of its members' memberships.
            reconcile_stack_membership(session, stack_id)

            stack.updated_at = datetime.utcnow()
            session.add(stack)
            session.commit()
            return stack

        stack = server.vault.db.run_task(add_members, stack_id, picture_ids)
        pictures = server.vault.db.run_task(_fetch_stack_pictures, stack_id)
        pictures = _ensure_stack_positions(request, stack_id, pictures)
        payload = safe_model_dict(stack)
        payload["picture_ids"] = [pic.id for pic in pictures]
        return payload

    @router.delete(
        "/stacks/{stack_id}/members",
        summary="Remove stack members",
        description=(
            "Removes pictures from a stack and deletes the stack when one or "
            "fewer members remain.\n\n"
            "**A locked picture set refuses the whole stack** (`423`), never "
            "just the frozen member. Stack membership reconciles to the union "
            "of its members' sets, and a locked set freezes a stack's siblings "
            "*through* the stack, so detaching any member severs a freeze the "
            "lock is there to hold. The same rule governs "
            "`POST /dedup/mixed-stacks/{stack_id}/split` and "
            "`POST /dedup/mixed-stacks/{stack_id}/unstack`."
        ),
        response_model=StackResponse,
        responses={
            423: {
                "description": (
                    "A live or scrapheaped member of this stack is frozen by a "
                    "locked picture set. Nothing was written. The `detail` "
                    "names the sets and the frozen picture ids."
                )
            }
        },
    )
    def remove_stack_members(
        stack_id: int, payload: dict = Body(...), request: Request = None
    ):
        _ensure_secure_when_required(request)
        server.auth.require_user_id(request)

        picture_ids = _normalize_picture_ids(payload.get("picture_ids") or [])

        def remove_members(session: Session, stack_id: int, picture_ids: list[int]):
            stack = session.get(PictureStack, stack_id)
            if stack is None:
                raise HTTPException(status_code=404, detail="Stack not found")

            # Refuse before any mutation if this stack is frozen. A locked set
            # freezes a stack's siblings THROUGH the stack, so detaching a member
            # severs that freeze and an operation the lock refused a moment ago
            # (a soft delete, a tag edit) starts succeeding. Same helper, same
            # whole-stack rule and same 423 as the two mixed-stack routes.
            enforce_stack_detach_not_locked(
                session, stack_id, "remove pictures from a locked stack"
            )

            picture_scope = scope_id_subquery(
                session, picture_ids, name="_pixlstash_stack_route_picture_ids"
            )
            pictures = session.exec(
                select(Picture).where(Picture.id.in_(picture_scope))
            ).all()
            for pic in pictures:
                if pic.stack_id == stack_id:
                    pic.stack_id = None
                    # Clear the position with the stack id. A detached picture
                    # that keeps `stack_position = 2` carries meaningless state
                    # that later sorting can still read, and the dissolve branch
                    # below and the mixed-stack split path both already leave
                    # `(None, None)`; this branch was the odd one out.
                    pic.stack_position = None
                    session.add(pic)

            remaining = session.exec(
                select(Picture).where(Picture.stack_id == stack_id)
            ).all()

            # A stack is a visible relationship between LIVE pictures. A hidden
            # scrapheaped row must not keep a one-live-member stack alive: the
            # grid would render the survivor as unstacked while its database row
            # still said otherwise. Dissolve all rows, deleted ones included.
            live_remaining = [pic for pic in remaining if not pic.deleted]
            if len(live_remaining) <= 1:
                for pic in remaining:
                    pic.stack_id = None
                    pic.stack_position = None
                    session.add(pic)
                session.delete(stack)
                session.flush()
                return None

            # Compact to close gaps left by the removed pictures.
            _compact_stack_positions_in_session(session, stack_id)

            stack.updated_at = datetime.utcnow()
            session.add(stack)
            session.flush()
            return stack

        # Expanding to the whole stack is what makes the dissolve branch
        # reversible: when one or fewer members remain the stack row is deleted
        # and the survivor is unstacked too, and that survivor is not in the
        # requested picture_ids.
        #
        # ...and the expansion has to include the SOFT-DELETED members, because
        # both branches above mutate them: `_compact_stack_positions_in_session`
        # renumbers every row pointing at the stack, deleted ones included, and
        # the dissolve branch clears their `stack_id` outright. Left out of the
        # snapshot, those changes are simply not undoable. `mixed_stack_service.
        # _apply_removal` passes `include_deleted=True` for exactly this hazard;
        # this route now agrees with its sibling.
        stack, _operation = operation_log_service.run_recorded_metadata_task(
            server.vault,
            remove_members,
            stack_id,
            picture_ids,
            op_type="stacks.dissolve",
            picture_ids=picture_ids,
            expand_stacks=True,
            expand_stacks_include_deleted=True,
            summary=f"Unstacked {len(picture_ids)} picture(s)",
            **operation_log_service.request_context(request),
        )
        if stack is None:
            return {"status": "success", "stack_id": None, "picture_ids": picture_ids}

        payload = safe_model_dict(stack)
        payload["picture_ids"] = picture_ids
        return payload

    @router.patch(
        "/stacks/{stack_id}/members/{picture_id}",
        summary="Set member position",
        description=(
            "Moves a single stack member to the given 0-based position, "
            "shifting all other members as needed."
        ),
        response_model=StackOrderResponse,
    )
    def set_member_position(
        stack_id: int,
        picture_id: int,
        payload: dict = Body(...),
        request: Request = None,
    ):
        _ensure_secure_when_required(request)
        server.auth.require_user_id(request)

        position = payload.get("position")
        if not isinstance(position, int) or position < 0:
            raise HTTPException(
                status_code=400,
                detail="position must be a non-negative integer",
            )

        def update_member_position(
            session: Session,
            stack_id_value: int,
            picture_id_value: int,
            target_position: int,
        ) -> list[int]:
            stack = session.get(PictureStack, stack_id_value)
            if stack is None:
                raise HTTPException(status_code=404, detail="Stack not found")

            pic = session.get(Picture, picture_id_value)
            if pic is None or pic.stack_id != stack_id_value:
                raise HTTPException(
                    status_code=404, detail="Picture not found in stack"
                )

            # Fetch all members in current stack order.
            all_pics = session.exec(
                select(Picture)
                .where(Picture.stack_id == stack_id_value)
                .order_by(
                    case(
                        (Picture.stack_position.is_(None), 1),
                        else_=0,
                    ),
                    Picture.stack_position,
                    Picture.id,
                )
            ).all()

            # Remove the target picture from its current slot, then insert at
            # the requested position (clamped to valid range).
            ordered = [p for p in all_pics if p.id != picture_id_value]
            insert_at = max(0, min(target_position, len(ordered)))
            ordered.insert(insert_at, pic)

            # Assign contiguous 0-based positions.
            for idx, p in enumerate(ordered):
                p.stack_position = idx
                session.add(p)

            stack.updated_at = datetime.utcnow()
            session.add(stack)
            session.commit()
            return [p.id for p in ordered]

        ordered_ids = server.vault.db.run_task(
            update_member_position, stack_id, picture_id, position
        )
        return {"stack_id": stack_id, "picture_ids": ordered_ids}

    # ── Keep cover only (docs/design/keep-cover-only.md) ─────────────────────

    def _selection(
        payload: Optional[KeepCoverOnlyRequest],
    ) -> tuple[list[int], list[int]]:
        """Validate the request body down to two id lists, or 400."""
        request_model = payload or KeepCoverOnlyRequest()
        try:
            stack_ids = keep_cover_only_service.coerce_selection_ids(
                request_model.stack_ids, "stack_ids"
            )
            picture_ids = keep_cover_only_service.coerce_selection_ids(
                request_model.picture_ids, "picture_ids"
            )
            # The cap is a per-REQUEST bound, so it is applied to the request:
            # capping the two lists separately let one call carry twice it.
            keep_cover_only_service.enforce_selection_budget(stack_ids, picture_ids)
        except KeepCoverOnlyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not stack_ids and not picture_ids:
            raise HTTPException(
                status_code=400,
                detail="Send at least one of stack_ids or picture_ids",
            )
        return stack_ids, picture_ids

    @router.post(
        "/stacks/keep-cover-only/preview",
        summary="Preview collapsing stacks to their covers",
        description=(
            "The **authoritative dry run** for `POST /stacks/keep-cover-only`, "
            "and the confirm dialog's only source of truth. Every figure comes "
            "from ONE read over the SAME selection through the same planner the "
            "mutation uses, so the dialog's headline and its rows cannot "
            "disagree: the failure the neighbouring auto-stack dialog shipped "
            'when it reported "62 stacks to create" for work that would '
            "create 3.\n\n"
            "**The stack buckets are disjoint and sum to `stacks_selected`:**\n\n"
            "```\n"
            "stacks_selected = stacks_eligible\n"
            "                + stacks_skipped_locked\n"
            "                + stacks_skipped_character_on_copy\n"
            "                + stacks_skipped_single_member\n"
            "```\n\n"
            "Each is counted directly; **none is derived by subtraction**, and "
            "the server refuses to answer at all if the sum does not hold. "
            "`unknown_stack_ids` sits outside the arithmetic because those are "
            "not stacks.\n\n"
            "**Why a stack is skipped.** `set_locked`, a live member is frozen "
            "by a locked picture set, which refuses the **whole** stack: stack "
            "membership reconciles to the union of its members' sets, so "
            "removing one member is exactly the mutation the lock forbids, and "
            "a partial collapse would be the worst outcome available. "
            "`character_only_on_copy`: a character link sits only on a member "
            "that would leave; the metadata union deliberately will not guess "
            "across several characters, so collapsing would destroy the link. "
            "`single_member`: fewer than two live members.\n\n"
            "**Nothing here is freed.** `originals_deleted_from_disk` is always "
            "`0`, and `bytes_held_by_copies` is bytes *held*: the files stay "
            "until the Scrapheap is emptied, which with "
            "`scrapheap_retention_days: null` (the default) never happens on "
            "its own. Read the retention window from this response rather than "
            "hardcoding one.\n\n"
            "Read-only: this plans, writes nothing and deletes nothing."
        ),
        response_model=KeepCoverOnlyPreviewResponse,
        responses={
            400: {
                "description": (
                    "Neither `stack_ids` nor `picture_ids` was usable, absent, "
                    "not a list of integers, over 2000 entries in one list, or over "
                    "2000 ids across the two lists together."
                )
            }
        },
    )
    def preview_keep_cover_only(
        request: Request,
        payload: Optional[KeepCoverOnlyRequest] = Body(default=None),
    ):
        _ensure_secure_when_required(request)
        stack_ids, picture_ids = _selection(payload)
        try:
            return keep_cover_only_service.preview(
                server.vault,
                stack_ids,
                picture_ids,
                keep_cover_only_service.read_retention_days(server),
            )
        except KeepCoverOnlyError as exc:
            logger.error("[keep-cover-only] preview refused: %s", exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post(
        "/stacks/keep-cover-only",
        summary="Collapse stacks to their covers",
        description=(
            "Each selected stack keeps its **current** cover; every other live "
            "member is **soft-deleted to the Scrapheap**, where it can be "
            "restored. Nothing is removed from disk: this is the same soft "
            "delete the grid's `Delete` performs, not a second permanent path. "
            "Call `POST /stacks/keep-cover-only/preview` first and show the "
            "user what it reports.\n\n"
            "**The metadata union runs first, unconditionally.** Before any "
            "copy leaves, the cover gains the union of the stack's tags and is "
            "lifted to the stack's best rating. This is not an optimisation to "
            "skip: only the dedup queue's stack verdict ever unioned before, so "
            "stacks made by hand in the grid never have been: measured on a "
            "real library, two thirds of them carry tags on a copy the cover "
            "lacks.\n\n"
            "**Stacks are skipped, never partly collapsed.** A stack with a "
            "member frozen by a locked picture set is refused whole; so is one "
            "whose only link to a character sits on a copy. Siblings in the "
            "same request still proceed, and each skip is returned with the "
            "sets or characters that caused it. The buckets match the preview's "
            "exactly.\n\n"
            "**The stack survives and undo is a flag flip.** A soft-deleted "
            "member keeps its `stack_id` and no stack row is dissolved, so "
            "`POST /pictures/scrapheap/restore` returns a copy to its stack "
            "rather than leaving it loose. The whole call is **one** operation "
            "under **one** `batch_id`, so a single `Ctrl+Z` "
            "(`POST /operations/undo`, or "
            "`POST /operations/batches/{batch_id}/undo`) restores every stack "
            "with its cover, positions and pre-union metadata intact.\n\n"
            "No `confirm_token` and no type-to-confirm: those are reserved for "
            "destroying an on-disk original, and spending them here would "
            'flatten the distinction between "recoverable" and "gone".'
        ),
        response_model=KeepCoverOnlyResponse,
        responses={
            400: {
                "description": (
                    "Neither `stack_ids` nor `picture_ids` was usable, absent, "
                    "not a list of integers, over 2000 entries in one list, or over "
                    "2000 ids across the two lists together."
                )
            },
            423: {
                "description": (
                    "A locked picture set was detected over a stack the planner "
                    "had already cleared. This is a defence-in-depth backstop, "
                    "not the ordinary path: a locked stack is normally reported "
                    "in `stacks_skipped_locked` while its siblings collapse. "
                    "Nothing was written: the whole call is rolled back rather "
                    "than risk a partial collapse."
                )
            },
        },
    )
    def post_keep_cover_only(
        request: Request,
        payload: Optional[KeepCoverOnlyRequest] = Body(default=None),
    ):
        _ensure_secure_when_required(request)
        stack_ids, picture_ids = _selection(payload)
        # §21 origin discipline: actor / source / origin_client_id are read from
        # the request HERE, on the request's own task, and passed down
        # explicitly: the contextvar is dead on the DB worker thread.
        context = operation_log_service.request_context(
            request, fallback_batch_id=operation_log_service.new_batch_id()
        )
        header_batch_id = context.pop("batch_id", None)
        # Validated, never taken verbatim: an arbitrary string lets a client mint
        # what reads as a server batch, or graft its rows into an existing batch
        # so one Ctrl+Z reverses more than the user did. Same helper the dedup
        # routes use, so the two cannot drift.
        body_batch_id = require_client_batch_id(
            (payload.batch_id if payload else None) or None
        )
        return keep_cover_only_service.keep_cover_only(
            server.vault,
            stack_ids,
            picture_ids,
            body_batch_id or header_batch_id,
            **context,
        )

    return router
