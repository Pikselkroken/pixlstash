"""HTTP routes for duplicate detection: the tiered queue plus the sweep dry run.

Two surfaces live here, and every route is owner-only.

**The v1.9 Duplicates queue** (:mod:`pixlstash.services.dedup_tier_service` and
:mod:`pixlstash.services.dedup_verdict_service`):

* ``GET  /dedup/policy``            - tier defaults + bounds; the client renders
  its tier switches and threshold slider from these values rather than
  re-hardcoding 0.90 and 0.65.
* ``GET  /dedup/groups``            - one page of the queue, confidence
  descending, plus this scope's scan progress for the banner.
* ``GET  /dedup/stacks/{stack_id}/members``: one page of an existing stack's
  members, for the deck expansion strip. The lazy half of the queue's stack
  contract: a queue row ships each stack's count and leader, never its members.
* ``POST /dedup/counts``            - the sidebar badge, the per-tier counts, and
  as many scoped counts as the context menus need, in one request. Read-only
  despite the verb: the scope list does not fit in a URL.
* ``POST /dedup/scan``              - queue a scoped scan; returns immediately.
* ``POST /dedup/verdicts/stack``    - stack a group behind a chosen cover.
* ``POST /dedup/verdicts/keep-separate`` - remember that a group is not
  duplicates; undoable through the operation log (owner override, 2026-07-30)
  and reopenable from the Stacks view.
* ``POST /dedup/verdicts/batch``       - apply one multi-group UI gesture in one
  transaction and one contiguous undo unit.
* ``POST /dedup/verdicts/reopen``   - return a decided group to the queue.
* ``POST /dedup/auto-stack``        - the exact tier's bulk action, one
  operation-log batch id so N stacks reverse with one undo.

**The vault-wide sweep dry run** (:mod:`pixlstash.services.dedup_sweep_service`),
unchanged and still non-destructive:

* ``GET  /dedup/sweep/policy``  - the default confidence policy plus its bounds.
* ``POST /dedup/sweep/dry-run`` - the vault-wide plan behind "N groups
  auto-collapse, M need review".

**Nothing in v1.9 deletes a picture.** A verdict is either a stack (additive, and
a stack is a grouping row plus a cover pointer) or a note that the group is not
duplicates. There is no destructive route on this surface.

Authorization is declared in ``pixlstash/authz/registry.py`` (every route
``OWNER_ONLY``) and enforced by the central gate before these handlers run; per
§16.1 there is deliberately no inline scope check here. A vault-wide aggregate
cannot be narrowed to a share token's scope without leaking counts about pictures
outside it, which is the same reasoning that makes the tag-health board
owner-only, and the verdict routes mutate stacks across arbitrary pictures.
"""

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, model_validator

from pixlstash.pixl_logging import get_logger
from pixlstash.services import dedup_sweep_service, dedup_tier_service
from pixlstash.services import dedup_verdict_service, mixed_stack_service
from pixlstash.services import operation_log_service
from pixlstash.services.mixed_stack_service import (
    DEFAULT_PAGE_SIZE as MIXED_STACK_PAGE_SIZE,
    MAX_PAGE_SIZE as MAX_MIXED_STACK_PAGE_SIZE,
    MixedStackError,
)
from pixlstash.services.dedup_tier_service import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_STACK_MEMBER_PAGE_SIZE,
    DEFAULT_THRESHOLD,
    MAX_PAGE_SIZE,
    MAX_STACK_MEMBER_PAGE_SIZE,
    MAX_THRESHOLD,
    MIN_THRESHOLD,
    VERDICT_ORDER,
    DedupCursorError,
    DedupScanBusyError,
    DedupScope,
    DedupTier,
    DedupVerdictKind,
    ScopeType,
    TierPolicy,
)
from pixlstash.services.dedup_verdict_service import DedupVerdictError
from pixlstash.services.dedup_sweep_service import (
    DEFAULT_AUTO_RESOLVE_LIKENESS,
    DEFAULT_LIKENESS_THRESHOLD,
    DEFAULT_MAX_AUTO_GROUP_SIZE,
    DEFAULT_MAX_GROUPS_LISTED,
    DEFAULT_MIN_GROUP_SIZE,
    DEFAULT_SMART_SCORE_MARGIN,
    MAX_LIKENESS,
    MIN_LIKENESS,
    CrossStackPolicy,
    ReviewReason,
    SweepOutcome,
    SweepPolicy,
    SweepVerdict,
)
from pixlstash.utils.request_origin import require_client_batch_id

logger = get_logger(__name__)

# The client-supplied batch-id contract lives in
# ``pixlstash/utils/request_origin.py``, alongside the ``X-Operation-Batch-Id``
# header it shares a pattern with. This module used to carry its own copy while
# that branch was unmerged; it has merged, so the copy is gone and there is
# exactly one definition. Every route taking a body ``batch_id`` must call
# ``require_client_batch_id``; a second copy is how ``POST
# /stacks/keep-cover-only`` shipped storing the field verbatim.

MAX_CURSOR_LENGTH = 128
"""Cap on the opaque queue cursor a client may send back.

The cursors this server mints are ~40 characters; anything longer is not one of
ours, and bounding it here keeps a malformed value from reaching the base64
decoder as an unbounded string."""

MAX_COUNT_SCOPES = 200
"""Cap on the scope list of one ``POST /dedup/counts``.

Each scope is a separate correlated ``COUNT`` subquery, so an uncapped list turns
one request into thousands of queries against the owner's own server. 200 is far
more than any real context menu needs (the sidebar asks for a handful) and keeps
the worst case bounded."""


class SweepPolicyModel(BaseModel):
    """The confidence policy: every knob that decides act-vs-propose.

    Sent as the ``POST /dedup/sweep/dry-run`` body and returned by
    ``GET /dedup/sweep/policy`` as the server's defaults. Omitted fields fall back
    to the default shown, so an empty ``{}`` body is a valid default sweep.
    """

    model_config = ConfigDict(extra="forbid")

    likeness_threshold: float = Field(
        default=DEFAULT_LIKENESS_THRESHOLD,
        ge=MIN_LIKENESS,
        le=MAX_LIKENESS,
        description=(
            "Minimum pairwise likeness for two pictures to land in the same "
            "candidate group. Matches the grid's Likeness Groups threshold."
        ),
    )
    auto_resolve_likeness: float = Field(
        default=DEFAULT_AUTO_RESOLVE_LIKENESS,
        ge=MIN_LIKENESS,
        le=MAX_LIKENESS,
        description=(
            "The higher bar a group must clear to be acted on without review. A "
            "group whose weakest observed likeness edge falls below this is "
            "reported with reason `weak_likeness` instead. Must be >= "
            "`likeness_threshold`; setting it equal disables this gate. When "
            "omitted it is raised to `likeness_threshold` if the default would "
            "sit below it, so tightening the candidate threshold alone is valid; "
            "supplying both inconsistently is a 400, never a silent retune."
        ),
    )
    smart_score_margin: float = Field(
        default=DEFAULT_SMART_SCORE_MARGIN,
        ge=0.0,
        le=1.0,
        description=(
            "How far the keeper must lead the runner-up on smart score, when the "
            "two tie on the human score, for the keeper to count as unambiguous. "
            "0 switches the smart-score axis off entirely."
        ),
    )
    min_group_size: int = Field(
        default=DEFAULT_MIN_GROUP_SIZE,
        ge=2,
        description="Smallest group the sweep considers at all.",
    )
    max_auto_group_size: int = Field(
        default=DEFAULT_MAX_AUTO_GROUP_SIZE,
        ge=2,
        description=(
            "Groups larger than this are reported with reason `oversized_group` "
            "rather than acted on: a large transitively-chained component is "
            "rarely a single duplicate cluster."
        ),
    )
    cross_stack: CrossStackPolicy = Field(
        default=CrossStackPolicy.REPORT,
        description=(
            "What to do with a group whose members already live in several "
            "stacks. `report` (default) proposes the merge but always routes it "
            "to review; `merge` treats it as an ordinary outcome subject to the "
            "remaining gates. Either way the group is represented - unlike the "
            "grid action, which skips it."
        ),
    )
    max_groups_listed: int = Field(
        default=DEFAULT_MAX_GROUPS_LISTED,
        ge=0,
        description=(
            "Cap on the `groups` array in the response. All counts and byte "
            "totals stay complete; `listing_truncated` says whether the array "
            "shows every group."
        ),
    )

    def to_policy(self) -> SweepPolicy:
        """Convert to the service's parameter object, raising ``ValueError``.

        The one accommodation: an *unset* ``auto_resolve_likeness`` is lifted to
        ``likeness_threshold`` when the default would fall below it, so raising
        the candidate threshold alone is a valid request. An explicitly supplied
        pair that contradicts itself still raises - resolving a default is not
        the same as overriding what the caller asked for.
        """
        auto_resolve_likeness = self.auto_resolve_likeness
        if "auto_resolve_likeness" not in self.model_fields_set:
            auto_resolve_likeness = max(auto_resolve_likeness, self.likeness_threshold)
        return SweepPolicy(
            likeness_threshold=self.likeness_threshold,
            auto_resolve_likeness=auto_resolve_likeness,
            smart_score_margin=self.smart_score_margin,
            min_group_size=self.min_group_size,
            max_auto_group_size=self.max_auto_group_size,
            cross_stack=self.cross_stack,
            max_groups_listed=self.max_groups_listed,
        )


class SweepDryRunRequest(BaseModel):
    """The dry-run body: a policy plus the operation-log correlation seam."""

    model_config = ConfigDict(extra="forbid")

    policy: Optional[SweepPolicyModel] = Field(
        default=None,
        description=(
            "The confidence policy. Omit it (or send an empty body) to use the "
            "server defaults from GET /dedup/sweep/policy."
        ),
    )
    operation_batch_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional operation-log batch id to tag this plan with. A dry run "
            "writes nothing, so it is inert here and simply echoed back in the "
            "report; it exists so a later apply step can correlate the plan it "
            "executed with the batch that can undo it."
        ),
    )


class SweepPolicyBoundsModel(BaseModel):
    """The range each policy knob is validated against."""

    model_config = ConfigDict(extra="allow")

    min_likeness: float = Field(
        description="Lower bound for both likeness knobs.",
    )
    max_likeness: float = Field(
        description="Upper bound for both likeness knobs.",
    )
    verdicts: list[str] = Field(
        description="Every value the per-group `verdict` field can take.",
    )
    outcomes: list[str] = Field(
        description=(
            "Every value the per-group `outcome` field can take. All are "
            "additive: the sweep creates, grows, or merges stacks and never "
            "deletes a picture."
        ),
    )
    review_reasons: list[str] = Field(
        description="Every reason code a review group can carry.",
    )
    cross_stack_options: list[str] = Field(
        description="Every accepted value of the `cross_stack` policy knob.",
    )


class SweepPolicyResponse(BaseModel):
    """Server-side defaults plus the bounds a client should render controls from."""

    model_config = ConfigDict(extra="allow")

    defaults: SweepPolicyModel = Field(
        description="The policy a dry run uses when the body omits a field.",
    )
    bounds: SweepPolicyBoundsModel = Field(
        description="Validation bounds and the closed vocabularies.",
    )


class SweepGroupResponse(BaseModel):
    """One resolved near-duplicate group and the stacking action it proposes."""

    model_config = ConfigDict(extra="allow")

    index: int = Field(
        description=(
            "Stable 0-based position in this report's group ordering (ascending "
            "lowest member id), so a client can address a group without a DB id."
        )
    )
    picture_ids: list[int] = Field(
        description="Every member, keeper first, then canonical stack order."
    )
    keeper_id: int = Field(
        description="The picture that would lead the resulting stack."
    )
    verdict: SweepVerdict = Field(
        description="`auto_collapse` (act) or `needs_review` (propose)."
    )
    reasons: list[ReviewReason] = Field(
        default_factory=list,
        description=(
            "Why this group needs review. Empty for an auto group; never empty "
            "for a review group."
        ),
    )
    outcome: SweepOutcome = Field(
        description=(
            "The stacking action: `create_stack`, `add_to_stack`, or "
            "`merge_stacks`. Always additive."
        )
    )
    target_stack_id: Optional[int] = Field(
        default=None,
        description=(
            "The existing stack that would receive the members, or null when a "
            "new stack would be created."
        ),
    )
    merged_stack_ids: list[int] = Field(
        default_factory=list,
        description=(
            "The other stacks that would be folded into `target_stack_id`. "
            "Non-empty only for `merge_stacks`."
        ),
    )
    likeness_min: float = Field(
        description=(
            "The group's weakest observed likeness edge - the weak link of a "
            "transitive chain, and what `auto_resolve_likeness` is compared to."
        )
    )
    likeness_max: float = Field(
        description="The group's strongest observed likeness edge."
    )
    keeper_margin: Optional[float] = Field(
        default=None,
        description=(
            "How far the keeper leads the runner-up on the deciding signal, or "
            "null when no signal could separate them."
        ),
    )
    keeper_margin_basis: str = Field(
        description=(
            "Which signal decided the keeper: `score` (the human rating), "
            "`smart_score`, or `none` when neither could separate the top two."
        )
    )
    held_bytes: int = Field(
        description=(
            "Bytes of stored pixels held by the non-keeper members of this "
            "group. Reported so a client can show how much weight the near-"
            "duplicates carry; nothing here reclaims it."
        )
    )
    linked_member_ids: list[int] = Field(
        default_factory=list,
        description=(
            "Members with no likeness edge of their own, pulled in because they "
            "share an existing stack with a member that has one. Stacks move as "
            "a unit, so they are part of the proposed action."
        ),
    )


class SweepReportResponse(BaseModel):
    """The dry-run plan: complete vault-wide counts plus a capped group listing."""

    model_config = ConfigDict(extra="allow")

    policy: SweepPolicyModel = Field(
        description="The policy this report was produced under, echoed back."
    )
    operation_batch_id: Optional[str] = Field(
        default=None,
        description=(
            "The operation-log batch id supplied by the caller, echoed back so a "
            "plan can be correlated with the batch that later applies it. A dry "
            "run writes nothing, so this is inert here."
        ),
    )
    generated_at: datetime = Field(
        description="UTC timestamp the report was computed at."
    )
    scanned_edges: int = Field(
        description="Likeness pairs at or above `likeness_threshold` that were folded into groups."
    )
    candidate_groups: int = Field(
        description="Connected components found before any stack reconciliation."
    )
    already_collapsed_groups: int = Field(
        description=(
            "Candidate groups needing no action because every member already "
            "sits in one and the same stack."
        )
    )
    absorbed_groups: int = Field(
        description=(
            "Candidate groups whose members were all claimed by an earlier group "
            "through a shared stack, leaving nothing of their own to resolve."
        )
    )
    groups_total: int = Field(description="Groups that propose an action.")
    auto_collapse_groups: int = Field(
        description="Groups that clear every confidence gate and would be acted on."
    )
    needs_review_groups: int = Field(
        description="Groups routed to review, each with at least one reason code."
    )
    auto_collapse_pictures: int = Field(
        description="Pictures across all auto-collapse groups."
    )
    needs_review_pictures: int = Field(description="Pictures across all review groups.")
    outcome_counts: dict[str, int] = Field(
        default_factory=dict,
        description="Group count per `outcome` value, across both lanes.",
    )
    reason_counts: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Group count per review reason. A group carrying several reasons is "
            "counted under each, so these do not sum to `needs_review_groups`."
        ),
    )
    held_bytes_auto: int = Field(
        description="Non-keeper bytes across the auto-collapse groups."
    )
    held_bytes_review: int = Field(
        description="Non-keeper bytes across the review groups."
    )
    groups: list[SweepGroupResponse] = Field(
        default_factory=list,
        description="The group listing, capped at `policy.max_groups_listed`.",
    )
    listing_truncated: bool = Field(
        description="True when `groups` shows fewer groups than `groups_total`."
    )


# --- Tiered queue models ----------------------------------------------------


class TierPolicyModel(BaseModel):
    """Which tiers feed the queue, and how similar counts as similar.

    Tier 1 (exact) has no switch: it is always included and cannot be turned
    off. Each looser tier is a separate opt-in, and enabling one requires the
    tier above it, so a user cannot land on "same scene" suggestions without
    having deliberately walked down to them.
    """

    model_config = ConfigDict(extra="forbid")

    near_enabled: bool = Field(
        default=False,
        description=(
            "Tier 2: perceptual hashes compared inside candidate buckets (same "
            "dimensions / capture minute / import batch / folder). Opt-in."
        ),
    )
    embedding_enabled: bool = Field(
        default=False,
        description=(
            "Tier 3: full embedding similarity, for cross-folder and "
            "differently-framed near-duplicates. Opt-in, and requires "
            "`near_enabled` - enabling a tier requires the tier above it."
        ),
    )
    threshold: float = Field(
        default=DEFAULT_THRESHOLD,
        ge=MIN_THRESHOLD,
        le=MAX_THRESHOLD,
        description=(
            f"Minimum similarity for a near or embedding group to be suggested. "
            f"Defaults to {DEFAULT_THRESHOLD}. Exact matches are always shown "
            f"regardless. Below {MIN_THRESHOLD} nothing is suggested at all: a "
            "low threshold produces confident-looking garbage and destroys trust "
            "in the count, so a lower value is refused (422 from this bound), "
            "never a silent clamp."
        ),
    )

    min_group_size: int = Field(
        default=dedup_tier_service.DEFAULT_MIN_GROUP_SIZE,
        ge=2,
        description="Smallest group that counts as a duplicate group at all.",
    )
    max_group_size: int = Field(
        default=dedup_tier_service.DEFAULT_MAX_GROUP_SIZE,
        ge=2,
        description=(
            "Groups larger than this keep every member but carry an "
            "`Unusually large group` evidence-against pill, because a large "
            "transitively-chained blob is rarely one duplicate cluster."
        ),
    )

    def to_policy(self) -> TierPolicy:
        """Convert to the service parameter object, raising ``ValueError``."""
        return TierPolicy(
            near_enabled=self.near_enabled,
            embedding_enabled=self.embedding_enabled,
            threshold=self.threshold,
            min_group_size=self.min_group_size,
            max_group_size=self.max_group_size,
        )


class TierPolicyBoundsModel(BaseModel):
    """The values a client should render its tier controls from."""

    model_config = ConfigDict(extra="allow")

    min_threshold: float = Field(
        description="Hard floor. Nothing below this is ever suggested."
    )
    max_threshold: float = Field(description="Upper bound for the threshold.")
    tiers: list[str] = Field(
        description=("Every tier id, strongest evidence first. `exact` is always on.")
    )
    always_on_tiers: list[str] = Field(description="Tiers the user cannot switch off.")
    tier_requires: dict[str, Optional[str]] = Field(
        description=(
            "Per tier, the tier that must be enabled before it can be. `null` "
            "for a tier with no prerequisite."
        )
    )
    scope_types: list[str] = Field(
        description="Every accepted `scope_type` for a scoped scan or count."
    )
    verdicts: list[str] = Field(
        description=("Every verdict a group can carry. There is no deletion verdict.")
    )
    max_page_size: int = Field(description="Largest accepted queue page size.")


class TierPolicyResponse(BaseModel):
    """Server defaults plus the bounds and closed vocabularies."""

    model_config = ConfigDict(extra="allow")

    defaults: TierPolicyModel = Field(
        description="The policy used when a request omits a field."
    )
    bounds: TierPolicyBoundsModel = Field(
        description="Validation bounds and closed vocabularies."
    )


class WhyPillModel(BaseModel):
    """One piece of evidence, in either direction.

    Signals cut both ways: matching evidence renders as an olive check, and
    anything arguing against a stack (different resolution, different aspect
    ratio, fewer tags) as a red x. A group carrying red pills is exactly the one
    that needs a closer look, so the pills do the warning rather than generic
    "review carefully" copy. The server reports reasons; the user concludes.
    """

    model_config = ConfigDict(extra="allow")

    text: str = Field(description="The pill's label, ready to render.")
    against: bool = Field(
        description="True when this argues against stacking the group."
    )


class LockedSetRefModel(BaseModel):
    """A locked picture set, named so the client can explain a refusal."""

    model_config = ConfigDict(extra="allow")

    id: int = Field(description="The picture set's id.")
    name: str = Field(description="Its name, for the tooltip and the notice.")


class LockedSkipModel(BaseModel):
    """One member a locked set kept out of a stack."""

    model_config = ConfigDict(extra="allow")

    picture_id: int = Field(description="The member that stayed out.")
    reason: str = Field(
        default="set_locked",
        description="Why it stayed out. `set_locked` is the only value today.",
    )
    sets: list[LockedSetRefModel] = Field(
        default_factory=list,
        description="The locked sets that would have gained a member.",
    )


class DedupCandidateModel(BaseModel):
    """One picture in a group, with the fields Compare shows column by column."""

    model_config = ConfigDict(extra="allow")

    picture_id: int = Field(description="The picture.")
    width: Optional[int] = Field(default=None, description="Stored pixel width.")
    height: Optional[int] = Field(default=None, description="Stored pixel height.")
    megapixels: float = Field(
        description="Total pixels in millions - the cover formula's first term."
    )
    size_bytes: Optional[int] = Field(
        default=None, description="Size of the stored file."
    )
    format: Optional[str] = Field(default=None, description="Stored image format.")
    is_raw: bool = Field(
        description="Whether this is a camera original, which earns a cover bonus."
    )
    score: Optional[int] = Field(default=None, description="The user's own rating.")
    tag_count: int = Field(description="How many tags this picture carries.")
    created_at: Optional[datetime] = Field(
        default=None, description="Capture time; the cover tie-break, oldest wins."
    )
    imported_at: Optional[datetime] = Field(
        default=None, description="When it entered the library."
    )
    stack_id: Optional[int] = Field(
        default=None, description="Stack it already belongs to, if any."
    )
    reference_folder_id: Optional[int] = Field(
        default=None, description="Reference folder it belongs to, if any."
    )
    file_path: Optional[str] = Field(
        default=None,
        description=(
            "Full path, populated **only for reference-folder pictures**, where "
            "the user manages the files and needs to know which copy is which. "
            "Null for managed-library pictures, where the path is an "
            "implementation detail."
        ),
    )
    thumbnail_version: str = Field(
        description=(
            "Cache-buster token for this picture's thumbnail URL: append it as "
            "`?v=`, exactly as the batch-thumbnail endpoint does (both call the "
            "same helper). It changes whenever the stored bitmap is regenerated, "
            "so a thumbnail rebuilt mid-triage refetches instead of the queue "
            'painting the stale cached image. `"0"` until the picture has been '
            "processed."
        )
    )
    smart_score: Optional[float] = Field(
        default=None,
        description=(
            "The library's composite quality opinion on the [1, 5] scale "
            "(CLIP anchors, aesthetics, sharpness, resolution, anomaly "
            "penalty) - the DOMINANT signal of the cover preselection, "
            "compared in 0.25 buckets so scoring noise cannot outrank a real "
            "size difference. Null when not yet computed or failed; an "
            "unknown score ranks neutral, never worst. Display-ready (show a "
            "dash for null)."
        ),
    )
    sharpness: Optional[float] = Field(
        default=None,
        description=(
            "Objective sharpness metric (typical range 0-0.5, higher is "
            "sharper) - the ranking's tie-breaker after smart score and image "
            "size. Null when not computed or failed; unknown ranks neutral."
        ),
    )
    cover_score: float = Field(
        description=(
            "DEPRECATED legacy composite (`megapixels*4 + tags*3 + score*2 + "
            "8 if RAW`). No longer the selection rule: the preselection ranks "
            "smart score (bucketed), then pixel count, then sharpness, then "
            "stars/tags/RAW/bytes, ties to the oldest capture. Read "
            "`smart_score` and the why-pills instead; this field will be "
            "removed."
        )
    )
    why: list[WhyPillModel] = Field(
        default_factory=list,
        description=(
            "Per-candidate evidence, both directions: what this picture is best "
            "at and where it loses. Rendered so the user can disagree with the "
            "preselection knowing why it was made."
        ),
    )
    stackable: bool = Field(
        default=True,
        description=(
            "Whether this candidate may be stacked with the rest of the group. "
            "False when a **locked set** would gain a member: a locked set's "
            "membership cannot change, and an enlarged stack reconciles to the "
            "union of its members' sets, so a group straddling a locked-set "
            "boundary can only stack the largest side of it. Render a false "
            "candidate as excluded-by-the-server (it is still a real member of "
            "the group and still worth showing); the Stack button acts on the "
            "true ones only. A group with fewer than two true candidates has no "
            "legal stack, and only Keep separate applies."
        ),
    )
    blocked_by_sets: list[LockedSetRefModel] = Field(
        default_factory=list,
        description=(
            "Empty when `stackable`. Otherwise the locked sets that would gain a "
            "member, for the tooltip that says why this thumbnail is out."
        ),
    )


class DedupStackModel(BaseModel):
    """An existing stack the group touches, as the queue renders it: one deck.

    A stack verdict moves whole **stacks**, so the queue draws each stack the
    group touches as a single unit rather than as its individual members. The
    unit stands for the **entire existing stack**, not just the members that
    happen to be in the group: on a real library one stack-touching group in
    three names only ONE member of a stack, so a client sizing the deck from
    `candidates` would draw a 4-deep stack as a single picture and then silently
    move all four.

    Count and leader are **eager**; the members are **not**. Inlining every
    member of every stack would put a 40-member stack's worth of tiles behind
    one queue row. Fetch them from
    `GET /dedup/stacks/{stack_id}/members` when the user opens the expansion.
    """

    model_config = ConfigDict(extra="allow")

    stack_id: int = Field(
        description="The existing stack this deck stands for.",
        examples=[12],
    )
    member_count: int = Field(
        description=(
            "The stack's **real live member count**, the deck's depth. This is "
            "NOT the number of this stack's members that are in the group (see "
            "`matched_picture_ids` for that), and it is routinely larger, so a "
            "group's true picture total can exceed `candidates.length`. Counts "
            "only non-scrapheaped members."
        ),
        examples=[4],
    )
    leader_picture_id: int = Field(
        description=(
            "The stack's leader (`stack_position` 0): the deck's face. It is "
            "frequently NOT one of `matched_picture_ids`, and it is deliberately "
            "the picture shown: a cover choice on a deck resolves to the leader, "
            "so a tile showing one picture while meaning another is the mismatch "
            "the deck exists to remove. Ranked exactly as the grid ranks a stack "
            "leader, so the two surfaces always show the same face."
        ),
        examples=[501],
    )
    leader_thumbnail_version: str = Field(
        description=(
            "Cache-buster for the LEADER's thumbnail URL, same contract as a "
            "candidate's `thumbnail_version`: append it as `?v=`. Present so the "
            "deck's face renders from the queue payload alone, without expanding "
            'the stack. `"0"` until the leader has been processed.'
        ),
        examples=["1024x768"],
    )
    matched_picture_ids: list[int] = Field(
        default_factory=list,
        description=(
            "Which of this stack's members are actually in the group, always a "
            "subset of the group's `candidates`, ascending. Its length over "
            '`member_count` is the accessible name\'s "1 of 4 matched"; it is '
            "deliberately not drawn on the tile, which has no corner budget for "
            "it."
        ),
        examples=[[503]],
    )
    stackable: bool = Field(
        default=True,
        description=(
            "The **unit-level** rollup of the members' own `stackable`: false "
            "when ANY member of this deck is frozen by a locked picture set, "
            "because a stack cannot be partially stacked: it moves as a unit or "
            "not at all. Already accounts for a locked sibling **outside** the "
            "group: a locked set freezes a whole stack, so a member the group "
            "never names still blocks the deck."
        ),
    )
    blocked_by_sets: list[LockedSetRefModel] = Field(
        default_factory=list,
        description=(
            "Empty when `stackable`. Otherwise the union of the locked sets "
            "freezing this deck's members, deduplicated and sorted by set id, "
            "for the tooltip that says why the whole deck is out."
        ),
    )


class DedupGroupModel(BaseModel):
    """One queue row: a group, its evidence, and its cover preselection."""

    model_config = ConfigDict(extra="allow")

    signature: str = Field(
        description=(
            "Stable identity of this *set of files* (a hash of the sorted member "
            "content hashes). Verdicts are keyed on it, so a rescan or a "
            "re-import never re-asks. This is the id every verdict route takes."
        )
    )
    tier: DedupTier = Field(
        description="Which tier found the group: `exact`, `near` or `embedding`."
    )
    confidence: float = Field(
        description=(
            "1.0 for an exact match; otherwise the group's **weakest** pairwise "
            "similarity, so a transitive chain is judged by its weak link. The "
            "queue is ordered by this, descending."
        )
    )
    member_count: int = Field(
        description=(
            "How many **live** pictures are in the group, matching `candidates` "
            "exactly. A member that has been moved to the Scrapheap is not "
            "counted and is not listed: a soft-deleted picture never appears in "
            "the queue, in a group, or in any count."
        )
    )
    cover_picture_id: Optional[int] = Field(
        default=None,
        description=(
            "The server's cover preselection. Always a preselection, never a "
            "silent decision: the client shows it and the user may override it."
        ),
    )
    why: list[WhyPillModel] = Field(
        default_factory=list, description="Group-level evidence, both directions."
    )
    created_at: Optional[datetime] = Field(
        default=None, description="When the group was first detected."
    )
    verdict: Optional[str] = Field(
        default=None,
        description=(
            "On a `decided=true` page only: the live verdict that resolved the "
            "group, `stacked` or `keep_separate`. Null on the open queue."
        ),
    )
    decided_at: Optional[datetime] = Field(
        default=None,
        description=(
            "On a `decided=true` page only: when the decision last became live "
            "(a redo re-stamps it), naive-UTC ISO 8601 with microseconds and "
            "no offset suffix, like every timestamp on this API. This is the "
            "value the decided ordering sorts by, so it is display-ready as "
            "the row's decision time. Null on the open queue, and null for "
            "the stale edge of a resolved group with no live verdict - never "
            "an invented stamp."
        ),
    )
    candidates: list[DedupCandidateModel] = Field(
        default_factory=list,
        description="Every member, cover first, with its own evidence.",
    )
    stacks: dict[str, DedupStackModel] = Field(
        default_factory=dict,
        description=(
            "Every **existing stack** this group touches, keyed by stack id as a "
            "string (the id is repeated as an integer inside each entry). Empty "
            "when no member of the group is stacked.\n\n"
            "This is the stack truth a `stack_id` on a candidate cannot carry. "
            "Render each entry as ONE deck whose depth is `member_count`, in "
            "place of that stack's individual candidates: the smallest thing a "
            "stack verdict can move is a whole stack, so a row offering to "
            "exclude one member of one, or to make one member the cover, is "
            "offering a gesture the backend cannot honour. Group the row's "
            "`candidates` by `stack_id`; a candidate with a null `stack_id` is "
            "its own unit."
        ),
        examples=[
            {
                "12": {
                    "stack_id": 12,
                    "member_count": 4,
                    "leader_picture_id": 501,
                    "leader_thumbnail_version": "1024x768",
                    "matched_picture_ids": [503],
                    "stackable": True,
                    "blocked_by_sets": [],
                }
            }
        ],
    )


class DedupStackMemberModel(DedupCandidateModel):
    """One member of an expanded deck, in canonical stack order.

    Every field a queue candidate carries, so the expansion strip reuses the
    row's tile unchanged, plus the member's place in its stack.
    """

    model_config = ConfigDict(extra="allow")

    position: int = Field(
        description=(
            "0-based place in the canonical stack order, so `0` is the leader. "
            "It is the member's rank across the WHOLE stack, not within this "
            "page, and it survives paging."
        ),
        examples=[0],
    )
    is_leader: bool = Field(
        description=(
            "True for the one member the stack leads with: the same picture "
            "the deck shows as its face and the grid shows as the stack's "
            "leader."
        )
    )


class DedupStackMembersResponse(BaseModel):
    """One page of an existing stack's members, for the deck expansion strip."""

    model_config = ConfigDict(extra="allow")

    stack_id: int = Field(description="The stack that was expanded.", examples=[12])
    member_count: int = Field(
        description=(
            "The stack's real live member count, identical to the queue row's "
            "`stacks[stack_id].member_count`, so the strip and the deck can "
            "never disagree about the depth."
        ),
        examples=[4],
    )
    leader_picture_id: int = Field(
        description="The stack's leader, echoed so the strip can mark it.",
        examples=[501],
    )
    leader_thumbnail_version: str = Field(
        description="The leader's thumbnail cache-buster; same contract as the queue's.",
        examples=["1024x768"],
    )
    stackable: bool = Field(
        description=(
            "The unit-level rollup, computed over the **whole** stack rather "
            "than this page, so page 2 can never report a different answer "
            "from page 1. `false` means split, unstack and "
            "`DELETE /stacks/{stack_id}/members` will all answer `423` and "
            "write nothing, so offer the action disabled with "
            "`blocked_by_sets` as the reason. Same pair, same meaning and the "
            "same member rows as `GET /dedup/mixed-stacks`.\n\n"
            "It is rolled up over **every** member row, scrapheaped ones "
            "included, because that is what the server refuses on: a "
            "scrapheaped picture that is itself in a locked set still freezes "
            "its stack against being broken up. So this can be `false` while "
            "every member on the page reports `stackable: true`; the frozen "
            "row is in the Scrapheap and is not listed. The per-member flag "
            "answers a narrower question (may *this picture* be put in a "
            "dedup stack), and a scrapheaped locked member projects no freeze "
            "onto its live siblings."
        ),
        examples=[True],
    )
    blocked_by_sets: list[LockedSetRefModel] = Field(
        default_factory=list,
        description=(
            "Empty exactly when `stackable` is `true`; otherwise the locked "
            "sets freezing the stack, `[{id, name}]` sorted by set id, so the "
            "refusal can be named before the action is offered."
        ),
        examples=[[]],
    )
    offset: int = Field(
        description="Echo of the requested offset.",
        examples=[0],
    )
    limit: int = Field(
        description="Effective page size after clamping.",
        examples=[DEFAULT_STACK_MEMBER_PAGE_SIZE],
    )
    next_offset: Optional[int] = Field(
        default=None,
        description=(
            "Pass as `offset` for the next page, or `null` at the end of the "
            "stack. Plain offset paging is correct here, unlike the queue's "
            "cursor: a stack's membership is not a live list being decided out "
            "from under the client."
        ),
        examples=[None],
    )
    members: list[DedupStackMemberModel] = Field(
        default_factory=list,
        description=(
            "This page of members, leader first, in canonical stack order. Each "
            "carries `why: []`: evidence is a property of the duplicate group, "
            "not of a stack the user already made, and the expanded members are "
            "read-only in the queue row. May be shorter than `limit` before the "
            "end of the stack if a member was scrapheaped mid-request; that is "
            "logged server-side."
        ),
    )


class ScanProgressModel(BaseModel):
    """The "scanned N of M" banner's data."""

    model_config = ConfigDict(extra="allow")

    status: str = Field(
        description=(
            "`idle`, `pending`, `running`, `complete`, `partial` or `failed`. "
            "`partial` means bounded near-scan work omitted comparisons and "
            "the prior complete near evidence was preserved."
        )
    )
    scanned_pictures: int = Field(description="Pictures covered so far.")
    total_pictures: int = Field(description="Pictures in scope.")
    scanned_buckets: int = Field(
        description="Tier-2 candidate buckets finished. Groups appear as these land."
    )
    total_buckets: int = Field(description="Tier-2 candidate buckets in total.")
    groups_found: int = Field(description="Unresolved groups this scan produced.")
    error: Optional[str] = Field(
        default=None,
        description="Why the scan failed or is partial, when applicable.",
    )


class DedupQueueResponse(BaseModel):
    """One page of the queue plus everything the header needs."""

    model_config = ConfigDict(extra="allow")

    groups: list[DedupGroupModel] = Field(
        default_factory=list, description="This page, confidence descending."
    )
    total: int = Field(
        description=(
            "Complete group count in scope under this page's own filter - the "
            "unresolved queue, or the decided page narrowed to `verdict` - so "
            "the client can size its scrollbar without a second request. The "
            "queue is paged from the database and is never loaded whole."
        )
    )
    offset: int = Field(
        description=(
            "Echo of the requested offset. **Deprecated** - offset paging skips "
            "groups whenever the queue changes underneath it (a verdict on a "
            "delivered row, or a tier-2 scan committing a bucket, shifts every "
            "later row up). Page with `cursor` instead; `0` is echoed when "
            "cursor paging."
        )
    )
    limit: int = Field(description="Effective page size after clamping.")
    cursor: Optional[str] = Field(
        default=None, description="Echo of the cursor this page was read from."
    )
    next_cursor: Optional[str] = Field(
        default=None,
        description=(
            "Pass as `cursor` to fetch the next page. `null` at end-of-found. "
            "Opaque: pass it back verbatim, never construct or parse one."
        ),
    )
    policy: TierPolicyModel = Field(description="The policy this page was read under.")
    scope: dict[str, Any] = Field(description="The scope this page was read under.")
    verdicts: list[str] = Field(
        default_factory=list,
        description=(
            "Echo of the `verdict` filter in force, sorted. Empty means every "
            "decision (and is always empty on the open queue)."
        ),
    )
    by_verdict: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "On a `decided=true` page: decided groups per verdict in scope, "
            "counted **without** the `verdict` filter so the client can show "
            "what turning a verdict back on would add. Empty on the open queue. "
            "May sum to less than `total`: a resolved group whose live verdict "
            "row is missing is a stale edge state that still lists (so its "
            '"clear decision" way back survives) but belongs to no verdict.'
        ),
    )
    scan: ScanProgressModel = Field(description="Scan progress for this scope.")


class ScopeRequestModel(BaseModel):
    """A scope to scan or count."""

    model_config = ConfigDict(extra="forbid")

    scope_type: ScopeType = Field(
        default=ScopeType.GLOBAL,
        description=(
            "`global` for the whole vault, or the collection kind behind a "
            '"Find duplicates in ..." context-menu entry.'
        ),
    )
    scope_id: Optional[str] = Field(
        default=None,
        description=(
            "The collection's id, or the absolute folder path for "
            "`scope_type=folder`. Required unless `scope_type` is `global`."
        ),
    )

    def to_scope(self) -> DedupScope:
        """Convert to the service scope, raising ``ValueError``."""
        return DedupScope(scope_type=self.scope_type, scope_id=self.scope_id)


class DedupCountsRequestModel(BaseModel):
    """Ask for the sidebar badge and any number of scoped counts at once."""

    model_config = ConfigDict(extra="forbid")

    policy: Optional[TierPolicyModel] = Field(
        default=None, description="Tier policy; server defaults when omitted."
    )
    scopes: list[ScopeRequestModel] = Field(
        default_factory=list,
        max_length=MAX_COUNT_SCOPES,
        description=(
            "Extra scopes to count. The global count is always returned, so a "
            "context menu can ask for its own scopes and get the badge for free. "
            f"At most {MAX_COUNT_SCOPES} per request: each scope is a separate "
            "correlated COUNT, so an uncapped list turns one request into "
            "thousands of queries."
        ),
    )


class DedupCountsResponse(BaseModel):
    """Live counts: the sidebar badge, the per-tier split, and scoped counts."""

    model_config = ConfigDict(extra="allow")

    unresolved_groups: int = Field(
        description=(
            "The sidebar badge: duplicate decisions still to make across the "
            "whole vault, under the supplied policy."
        )
    )
    by_tier: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Unresolved groups per tier, **including tiers that are switched "
            "off**, so the user can see what enabling a tier would add before "
            "enabling it."
        ),
    )
    scopes: list[dict[str, Any]] = Field(
        default_factory=list,
        description="One `{scope_type, scope_id, key, unresolved_groups}` per requested scope.",
    )
    policy: TierPolicyModel = Field(
        description="The policy these counts were read under."
    )
    scan: ScanProgressModel = Field(description="Global scan progress.")


class DedupScanRequestModel(BaseModel):
    """Queue a scan. Returns immediately; poll ``GET /dedup/groups`` for progress."""

    model_config = ConfigDict(extra="forbid")

    policy: Optional[TierPolicyModel] = Field(
        default=None, description="Tier policy; server defaults when omitted."
    )
    scope: Optional[ScopeRequestModel] = Field(
        default=None, description="Scope to scan; the whole vault when omitted."
    )


class StackVerdictRequestModel(BaseModel):
    """Stack a group behind a chosen cover."""

    model_config = ConfigDict(extra="forbid")

    signature: str = Field(description="The group signature from the queue.")
    cover_picture_id: Optional[int] = Field(
        default=None,
        description=(
            "The cover the user chose or confirmed. Defaults to the server's "
            "preselection stored on the group. Must be an included member."
        ),
    )
    excluded_picture_ids: list[int] = Field(
        default_factory=list,
        description=(
            "Members the user left out of the stack. They are untouched and "
            "recorded on the verdict, so a rescan does not treat the exclusion "
            "as an unfinished decision."
        ),
    )
    batch_id: Optional[str] = Field(
        default=None,
        description=(
            "Operation-log batch to record this verdict under. Supply the same "
            "`cli-<4-76 chars of A-Z a-z 0-9 _ ->` id across several calls to "
            "make them reverse as one undo. Omit it and the server mints its own "
            "`srv-` id; any other shape is a 400, so a client cannot mint what "
            "reads as a server batch or graft its rows into one."
        ),
    )


class SignatureRequestModel(BaseModel):
    """A verdict route that needs nothing but the group signature."""

    model_config = ConfigDict(extra="forbid")

    signature: str = Field(description="The group signature.")
    batch_id: Optional[str] = Field(
        default=None,
        description=(
            "Operation-log batch to record under. Must be a client-namespaced "
            "`cli-…` id; omit it to have the server mint one."
        ),
    )


class VerdictResponse(BaseModel):
    """What a verdict did, for the action receipt."""

    model_config = ConfigDict(extra="allow")

    signature: str = Field(description="The signature the verdict was recorded on.")
    verdict: str = Field(description="`stacked` or `keep_separate`.")
    stack_id: Optional[int] = Field(
        default=None, description="The resulting stack, for a stack verdict."
    )
    cover_picture_id: Optional[int] = Field(
        default=None, description="The picture the stack leads with."
    )
    picture_ids: list[int] = Field(
        default_factory=list, description="Members the verdict covers."
    )
    excluded_picture_ids: list[int] = Field(
        default_factory=list, description="Members deliberately left out."
    )
    batch_id: Optional[str] = Field(
        default=None, description="Operation-log batch this verdict belongs to."
    )
    metadata_union: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "What the metadata union changed: `tags_added`, `scores_lifted`, "
            "`characters_pending`, `membership_changed`. Stacking unions tags, "
            "project and set membership onto every member and lifts every member "
            "to the highest score. Nothing is overwritten or lost."
        ),
    )
    skipped: list[LockedSkipModel] = Field(
        default_factory=list,
        description=(
            "Members a **locked set** kept out of the stack, so the verdict is a "
            "partial success rather than a whole-group refusal. Empty on the "
            "everyday path: the queue already marks these members, so this only "
            "fills when the lock landed after the page was loaded. The ids also "
            "appear in `excluded_picture_ids` (the verdict records them as "
            "exclusions so a rescan does not re-ask); `skipped` is what says the "
            "exclusion was the server's, not the user's. When fewer than two "
            "members are left the route returns **423** instead, with "
            "`{code: 'set_locked', action, sets, picture_ids}`."
        ),
    )


class VerdictBatchItemModel(BaseModel):
    """One stack or keep-separate decision in an atomic UI gesture."""

    model_config = ConfigDict(extra="forbid")

    verdict: DedupVerdictKind
    signature: str = Field(min_length=1)
    cover_picture_id: Optional[int] = None
    excluded_picture_ids: list[int] = Field(default_factory=list)


class VerdictBatchRequestModel(BaseModel):
    """A bounded, all-or-nothing set of duplicate verdicts."""

    model_config = ConfigDict(extra="forbid")

    actions: list[VerdictBatchItemModel] = Field(min_length=1, max_length=500)
    batch_id: Optional[str] = None

    @model_validator(mode="after")
    def unique_signatures(self):
        signatures = [action.signature for action in self.actions]
        if len(signatures) != len(set(signatures)):
            raise ValueError("a verdict batch cannot contain a signature twice")
        return self


class VerdictBatchResponse(BaseModel):
    """The single undo handle and per-group outcomes of an atomic gesture."""

    batch_id: str
    results: list[VerdictResponse]


class ReopenResponse(BaseModel):
    """The result of returning a decided group to the queue."""

    model_config = ConfigDict(extra="allow")

    signature: str = Field(description="The reopened signature.")
    previous_verdict: str = Field(description="The verdict that was in force.")
    reopened_at: Optional[datetime] = Field(
        default=None, description="When it was reopened."
    )
    group_returned_to_queue: bool = Field(
        description=(
            "Whether a group row for this signature exists and is back in the "
            "queue. False when the group has not been re-detected yet; the next "
            "scan will bring it back."
        )
    )
    batch_id: Optional[str] = Field(
        default=None,
        description=(
            "Operation-log batch of the clear, when clearing a `stacked` "
            "verdict dissolved its stack - the `POST /operations/batches/"
            "{batch_id}/undo` handle. Null when no picture changed "
            "(keep-separate, or a stack the user had already dissolved)."
        ),
    )
    unstacked_picture_ids: list[int] = Field(
        default_factory=list,
        description=(
            "Pictures whose stack state the clear restored to its pre-verdict "
            "shape. Empty when the clear touched no pictures."
        ),
    )


class AutoStackRequestModel(BaseModel):
    """The exact tier's bulk action, behind one consent."""

    model_config = ConfigDict(extra="forbid")

    scope: Optional[ScopeRequestModel] = Field(
        default=None, description="Restrict to a scope; the whole vault when omitted."
    )
    dry_run: bool = Field(
        default=True,
        description=(
            "True (the default) counts what would happen and writes nothing - "
            "this is what the consent dialog reads. Send `false` to apply."
        ),
    )
    batch_id: Optional[str] = Field(
        default=None,
        description=(
            "Operation-log batch id. Omit to have the server mint one (`srv-…`); "
            "every stack in the run shares it, so N stacks reverse with one "
            "undo. A supplied id must be client-namespaced `cli-…`."
        ),
    )
    limit: Optional[int] = Field(
        default=None,
        ge=1,
        description="Cap the number of groups acted on, for a paged run.",
    )


class AutoStackDryRunSummaryModel(BaseModel):
    """Aggregates for the consent dialog, from the dry run's own snapshot."""

    model_config = ConfigDict(extra="allow")

    groups: int = Field(description="Groups the run would act on.")
    groups_by_tier: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Group count per tier, zero-filled for every tier. Only `exact` is "
            "ever non-zero today - auto-stack is exact-only - but the shape is "
            "stable so the dialog does not need a special case if that changes."
        ),
    )
    pictures: int = Field(description="Pictures across those groups.")
    covers_gaining_tags: int = Field(
        description="Covers that would gain at least one tag from the union."
    )
    covers_gaining_score: int = Field(
        description="Covers whose score the union would lift."
    )
    covers_gaining_metadata: int = Field(
        description=(
            "Covers gaining tags **or** score - the design's "
            '"covers gaining metadata" row. Derived from the planned verdicts in '
            "the same read as the counts above, so the dialog's numbers can never "
            "disagree with each other; the union itself is not run and nothing is "
            "written."
        )
    )


class AutoStackResponse(BaseModel):
    """What the bulk auto-stack did, or would do."""

    model_config = ConfigDict(extra="allow")

    batch_id: Optional[str] = Field(
        default=None,
        description=(
            "The shared batch id, and the `POST /operations/batches/{batch_id}/"
            "undo` handle for the whole run. Always present on an applied run - "
            "including a partially applied one - so work that did happen is "
            "never left without a way to reverse it. Null only for a dry run "
            "with none supplied."
        ),
    )
    dry_run: bool = Field(description="Whether anything was written.")
    groups: int = Field(
        description="Exact groups actually stacked (or, on a dry run, that would be)."
    )
    pictures: int = Field(description="Pictures across the stacked groups.")
    scope: dict[str, Any] = Field(description="The scope the run covered.")
    dry_run_summary: Optional[AutoStackDryRunSummaryModel] = Field(
        default=None,
        description="Consent-dialog aggregates. Present on a dry run only.",
    )
    results: list[VerdictResponse] = Field(
        default_factory=list,
        description=(
            'One entry per applied group, each with `outcome: "applied"`. Empty '
            "for a dry run."
        ),
    )
    failures: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "One entry per group that was not applied: `{signature, outcome, "
            "status_code, error}`, where `outcome` is `blocked` (a guard refused "
            "it - in practice a locked picture set, 423) or `failed` (it could "
            "not be resolved at all). A single unstackable group never aborts "
            "the run, so a partial result is reported honestly rather than "
            "hidden, and the run still returns its `batch_id`."
        ),
    )
    blocked: int = Field(
        default=0, description="Groups refused by a guard, typically a locked set."
    )
    failed: int = Field(default=0, description="Groups that could not be resolved.")


# ── Mixed stacks (design D5 / B5) ────────────────────────────────────────────


class MemberEdgeModel(BaseModel):
    """How close one member gets to its nearest sibling in the same stack.

    The evidence column this page needs and the duplicate queue does not.
    Compare's other metrics answer *which copy is the better file*; here the
    question is *which of these does not belong*.

    **Two numbers, and they answer different questions.** `strongest_edge` is
    thresholded, so it is `null` for a stranded member by construction and is
    what the stranded verdict is made on. `nearest_edge` is unconditional: how
    close this member really gets to its closest sibling, whatever the
    threshold. Show `nearest_edge` to a user; decide with `strongest_edge`.
    """

    model_config = ConfigDict(extra="allow")

    picture_id: int = Field(
        description="The member this row is about. Example: `11`.", examples=[11]
    )
    strongest_edge: Optional[float] = Field(
        default=None,
        description=(
            "Highest similarity between this member and any other member of the "
            "same stack, counting only edges that survive at the row's "
            "`threshold`. `null` means this member has no qualifying edge at "
            "all, and the row says which of three things that is: the id is in "
            "`stranded_picture_ids` (it was compared and no edge of its survives "
            "this `threshold`; `nearest_edge` says how close it got), it is "
            "in `unhashed_picture_ids` (it could not be compared yet, so read it "
            "as *not comparable*, never as *does not belong*), or the stack has "
            "a single member and there is no sibling to match. Example: "
            "`0.984375`."
        ),
        examples=[0.984375],
    )
    closest_picture_id: Optional[int] = Field(
        default=None,
        description=(
            "The member on the other end of that surviving edge, so a client "
            "can name the nearest sibling rather than only score it. Ties "
            "resolve to the lower picture id. `null` exactly when "
            "`strongest_edge` is. Example: `8`."
        ),
        examples=[8],
    )
    nearest_edge: Optional[float] = Field(
        default=None,
        description=(
            "Similarity between this member and its closest sibling in the same "
            "stack, **measured regardless of the `threshold`** and regardless of "
            "the distance at which the server stops caching pairs. This is the "
            "number to put in front of a user: a stranded member has no "
            "`strongest_edge` by definition, and printing a dash there told "
            "people a picture matched nothing when its closest sibling was 89% "
            "similar. `null` only when there is genuinely nothing to compare "
            "against - this member has no usable `perceptual_hash`, or no other "
            "member of the stack has one - so a dash now means *not comparable*, "
            "never *unlike everything*. Always `>=` `strongest_edge`, and equal "
            "to it whenever that is not `null`. Example: `0.890625`."
        ),
        examples=[0.890625],
    )
    nearest_picture_id: Optional[int] = Field(
        default=None,
        description=(
            "The sibling `nearest_edge` was measured to, same tie rule (lower "
            "picture id). `null` exactly when `nearest_edge` is. Example: `8`."
        ),
        examples=[8],
    )


class MixedStackModel(BaseModel):
    """One live stack whose members do not form a single connected cluster."""

    model_config = ConfigDict(extra="allow")

    stack_id: int = Field(
        description="The stack. Example: `42`.",
        examples=[42],
    )
    threshold: float = Field(
        description=(
            "The similarity the components were folded at. Echoed on every row "
            "because the whole verdict is threshold-relative: this same stack "
            "may be one clean cluster at `0.65`. Example: `0.9`."
        ),
        examples=[0.9],
    )
    member_count: int = Field(
        description="Live members of the stack. Example: `12`.", examples=[12]
    )
    member_ids: list[int] = Field(
        description=(
            "Every live member, canonical stack order (leader first) - the same "
            "order the deck and `GET /dedup/stacks/{stack_id}/members` use. "
            "Example: `[7, 8, 9]`."
        ),
        examples=[[7, 8, 9]],
    )
    membership_fingerprint: str = Field(
        description=(
            "Digest of the sorted live member ids. This is the key a `Keep` "
            "dismissal is stored against, so a client can tell whether a Keep "
            "it just made still applies. Example: "
            '`"6f1c9a2b4d8e70153c9a2b4d8e701534"`.'
        ),
        examples=["6f1c9a2b4d8e70153c9a2b4d8e701534"],
    )
    component_count: int = Field(
        description=(
            "Connected clusters at this threshold. Always at least `2` on this "
            "list - `1` is a cohesive stack and is not listed. Example: `3`."
        ),
        examples=[3],
    )
    component_sizes: list[int] = Field(
        description=(
            "Size of each cluster, largest first. `[10, 2]` is a stack with one "
            "big burst and a stray pair; `[1, 1]` is a two-picture stack whose "
            "members do not match each other. Example: `[10, 1, 1]`."
        ),
        examples=[[10, 1, 1]],
    )
    components: list[list[int]] = Field(
        description=(
            "The clusters themselves, largest first, each sorted by picture id, "
            "so a client can show which members hang together rather than only "
            "how many do. Example: `[[7, 8, 9], [11]]`."
        ),
        examples=[[[7, 8, 9], [11]]],
    )
    largest_component_size: int = Field(
        description=(
            "Size of the biggest cluster - the stack that would survive a "
            "split. Example: `10`."
        ),
        examples=[10],
    )
    stranded_picture_ids: list[int] = Field(
        description=(
            "Members joined to nothing else in the stack (no edge at all at "
            "this threshold). These are the pictures `POST /dedup/mixed-stacks/"
            "{stack_id}/split` removes when the request omits `picture_ids`, "
            "and the strong case the design marks on a tile. Example: `[11]`."
        ),
        examples=[[11]],
    )
    weakest_edge: Optional[float] = Field(
        default=None,
        description=(
            "Lowest similarity among the edges that survive at this threshold - "
            "the same weakest-link reading `confidence` carries on a duplicate "
            "group. `null` when no pair is close enough to be an edge at all, "
            "i.e. every member is stranded, which is the extreme of the same "
            "scale rather than missing information. Example: `0.90625`."
        ),
        examples=[0.90625],
    )
    unhashed_picture_ids: list[int] = Field(
        description=(
            "Members with no usable `perceptual_hash` yet (the embedding worker "
            "has not reached them, or the file could not be hashed). They can "
            "carry no edge, so they appear stranded without being unlike "
            "anything - report them as *not yet comparable*, never as a "
            "mistake. Normally empty. Example: `[]`."
        ),
        examples=[[]],
    )
    member_edges: list[MemberEdgeModel] = Field(
        default_factory=list,
        description=(
            "One entry per member, in the same canonical order as `member_ids`: "
            "each member's strongest edge at this `threshold` (the verdict) and "
            "its closest sibling regardless of threshold (the truth). This is "
            "Compare's per-member evidence column; bind the visible number to "
            "`nearest_edge`. Folded out of the same edge pass as `components`, "
            "so it adds no request cost."
        ),
    )
    why: list[WhyPillModel] = Field(
        default_factory=list,
        description=(
            "Row-level evidence in the same `[{text, against}]` shape a "
            "duplicate group carries, so the shipped pill component renders it "
            "unchanged. Only the content differs: the strangers named by how "
            "unlike the rest they measure (`1 picture is only 89% like the "
            "rest`), the qualitative component structure (`1 picture differs "
            "from the rest`, `Most pictures differ`, or `All pictures differ`), "
            "and the "
            "weakest surviving edge (`Weakest match 97%`), plus `1 picture not "
            "comparable yet` when a member has no hash. A stranger is never "
            "described as matching nothing: at a 6-bit cut a member 7 bits from "
            "its neighbour is out of the cluster, not unlike everything. "
            "`against` keeps its meaning - true argues against these pictures "
            "belonging in one stack - so an unhashed member is *not* `against`: "
            "it argues for waiting, not for breaking the stack up, and it is "
            "subtracted from the stranger count for the same reason. When fewer "
            "than two members can be compared at all the only pill is `Nothing "
            "here can be compared yet`. The component-structure pill also "
            "carries an optional `accessible_text` expansion; the structured "
            "`component_sizes` and `components` fields remain authoritative."
        ),
    )
    suggested_action: str = Field(
        description=(
            "The outcome the primary button should name: `split` when a strict "
            "majority cluster survives removing the stranded members, "
            "`unstack` when there is no majority worth keeping. Example: "
            '`"split"`.'
        ),
        examples=["split"],
    )
    kept: bool = Field(
        description=(
            "Whether a `Keep` dismissal covers this exact membership. Always "
            "`false` unless the request asked for `include_kept=true`. Example: "
            "`false`."
        ),
        examples=[False],
    )
    leader_picture_id: int = Field(
        description=(
            "The stack's leader (`stack_position` 0) - the picture the row's "
            "thumbnail should show, matching the grid and the deck. Example: `7`."
        ),
        examples=[7],
    )
    leader_thumbnail_version: Optional[str] = Field(
        default=None,
        description=(
            "Cache-buster for the leader's thumbnail, so the row renders from "
            'this payload alone. Example: `"512x384"`.'
        ),
        examples=["512x384"],
    )
    stackable: bool = Field(
        default=True,
        description=(
            "Whether this stack can be split or unstacked at all. `false` when "
            "**any** member is frozen by a locked picture set: a locked set "
            "freezes a stack's siblings *through* the stack, so detaching one "
            "would sever the freeze and both actions refuse the whole stack "
            "with `423`. Disable the row's primary button on `false` and show "
            "`blocked_by_sets` as the reason, rather than letting the user "
            "press it and read an error. Same pair, same meaning as on "
            "`GET /dedup/stacks/{stack_id}/members`. Example: `true`."
        ),
        examples=[True],
    )
    blocked_by_sets: list[dict] = Field(
        default_factory=list,
        description=(
            "The locked picture sets freezing this stack, `[{id, name}]` "
            "sorted by set id, so the row can name them. Empty exactly when "
            "`stackable` is `true`. Example: `[]`."
        ),
        examples=[[]],
    )


class MixedStacksResponse(BaseModel):
    """One page of the Mixed stacks list."""

    model_config = ConfigDict(extra="allow")

    threshold: float = Field(
        description=(
            "The threshold this page was computed at, echoed so a client can "
            "confirm the list matches its slider. Example: `0.9`."
        ),
        examples=[0.9],
    )
    total: int = Field(
        description=(
            "Mixed stacks matching this request, i.e. what `stacks` pages "
            "through. Excludes kept stacks unless `include_kept=true`. "
            "Example: `26`."
        ),
        examples=[26],
    )
    kept_total: int = Field(
        description=(
            "How many mixed stacks carry a `Keep` at their current membership. "
            "Counted whether or not they are included, so a client can offer "
            '"N kept" without a second request. Example: `4`.'
        ),
        examples=[4],
    )
    live_stack_count: int = Field(
        description=(
            "Live stacks with two or more members - the denominator behind "
            '"26 of 209". Example: `209`.'
        ),
        examples=[209],
    )
    offset: int = Field(description="Rows skipped. Example: `0`.", examples=[0])
    limit: int = Field(description="Rows per page. Example: `20`.", examples=[20])
    next_offset: Optional[int] = Field(
        default=None,
        description=("Offset of the next page, or `null` at the end. Example: `20`."),
        examples=[20],
    )
    stacks: list[MixedStackModel] = Field(
        description=(
            "The page, ranked least-held-together first: stranded members "
            "descending, then component count descending, then weakest edge "
            "ascending (a stack with no edge at all sorts first)."
        )
    )


class MixedStackSplitRequestModel(BaseModel):
    """Split the marked member(s) off one mixed stack."""

    model_config = ConfigDict(extra="forbid")

    picture_ids: Optional[list[int]] = Field(
        default=None,
        description=(
            "The members to split off: **the ids the user marked on the row**, "
            "so the split matches what they were looking at even if the stack "
            "changed since. The row's marks start from "
            "`stranded_picture_ids` and the user adjusts them, so this list is "
            "*not* required to match that set. Omit it and the server uses the "
            "whole stranded set it computes at `threshold`, which is the same "
            "opening marking.\n\n"
            "**Every id must be a live member of this stack.** An id belonging "
            "to another stack or to none is a `400`, and so is a soft-deleted "
            "(scrapheaped) member: those are not on the row, the client cannot "
            "see them, and moving one blind is not something a mark can mean. "
            "Restore it first if you meant it. Example: `[11]`."
        ),
        examples=[[11]],
    )
    threshold: float = Field(
        default=DEFAULT_THRESHOLD,
        ge=MIN_THRESHOLD,
        le=MAX_THRESHOLD,
        description=(
            "Only read when `picture_ids` is omitted: the similarity at which "
            "the stranded set is computed, i.e. the marking the row opens with. "
            "An explicit list is bounded by the stack's live membership, not by "
            "cohesion, so this value does not narrow it. Example: `0.9`."
        ),
        examples=[0.9],
    )
    batch_id: Optional[str] = Field(
        default=None,
        description=(
            "Operation-log batch to record under. Must be a client-namespaced "
            "`cli-<4-76 chars of A-Z a-z 0-9 _ ->` id; omit it to have the "
            "server mint an `srv-` one. Share one id across several calls to "
            'make them reverse as a single `Ctrl+Z`. Example: `"cli-split-3"`.'
        ),
        examples=["cli-split-3"],
    )


class MixedStackUnstackRequestModel(BaseModel):
    """Dissolve one mixed stack entirely."""

    model_config = ConfigDict(extra="forbid")

    batch_id: Optional[str] = Field(
        default=None,
        description=(
            "Operation-log batch to record under. Must be a client-namespaced "
            "`cli-…` id; omit it to have the server mint one. Example: "
            '`"cli-unstack-9"`.'
        ),
        examples=["cli-unstack-9"],
    )


class MixedStackActionResponse(BaseModel):
    """What a split or unstack did, for the receipt and the undo handle."""

    model_config = ConfigDict(extra="allow")

    stack_id: int = Field(
        description="The stack that was acted on. Example: `42`.", examples=[42]
    )
    split_picture_ids: list[int] = Field(
        description=(
            "Pictures that left the stack and are now loose. For an unstack "
            "this is every member. Example: `[11]`."
        ),
        examples=[[11]],
    )
    remaining_picture_ids: list[int] = Field(
        description=(
            "Pictures still in the stack afterwards. Empty when the stack was "
            "dissolved. Example: `[7, 8, 9]`."
        ),
        examples=[[7, 8, 9]],
    )
    stack_dissolved: bool = Field(
        description=(
            "Whether the stack itself is gone. `true` for every unstack, and "
            "for a split that would otherwise have left a stack of one - a "
            "state the grid cannot render honestly, so the last member is freed "
            "too rather than left in a one-picture stack (the same rule "
            "`DELETE /stacks/{stack_id}/members` applies). Reported rather than "
            "inferred. Example: `false`."
        ),
        examples=[False],
    )
    batch_id: Optional[str] = Field(
        default=None,
        description=(
            "The `POST /operations/batches/{batch_id}/undo` handle. One split "
            "or unstack is exactly one operation, so a single `Ctrl+Z` puts "
            "every picture back in its original stack at its original position "
            "and recreates a dissolved stack under its original id. Example: "
            '`"srv-1a2b3c"`.'
        ),
        examples=["srv-1a2b3c"],
    )


class MixedStackKeepResponse(BaseModel):
    """The state of the `Keep` dismissal on one stack."""

    model_config = ConfigDict(extra="allow")

    stack_id: int = Field(description="The stack. Example: `42`.", examples=[42])
    dismissed: bool = Field(
        description=(
            "`true` after a `Keep`, `false` after clearing it. Example: `true`."
        ),
        examples=[True],
    )
    created: Optional[bool] = Field(
        default=None,
        description=(
            "Whether this call created a new dismissal (`false` means it was "
            "already kept at this exact membership - pressing Keep twice is "
            "idempotent). Absent when clearing. Example: `true`."
        ),
        examples=[True],
    )
    removed: Optional[int] = Field(
        default=None,
        description=(
            "Dismissals deleted by a clear, across every membership the stack "
            "was ever kept at. Absent when keeping. Example: `1`."
        ),
        examples=[1],
    )
    membership_fingerprint: Optional[str] = Field(
        default=None,
        description=(
            "The membership the Keep was recorded against. Adding a member "
            "later changes this, no dismissal matches, and the stack returns to "
            "the list. Absent when clearing. Example: "
            '`"6f1c9a2b4d8e70153c9a2b4d8e701534"`.'
        ),
        examples=["6f1c9a2b4d8e70153c9a2b4d8e701534"],
    )
    member_count: Optional[int] = Field(
        default=None,
        description=(
            "Members at the time of the Keep, for the audit trail. Absent when "
            "clearing. Example: `12`."
        ),
        examples=[12],
    )


def create_router(server) -> APIRouter:
    router = APIRouter()

    def _policy(model: Optional[TierPolicyModel]) -> TierPolicy:
        """Build the service policy from a request model, 400 on a bad one."""
        try:
            return (model or TierPolicyModel()).to_policy()
        except ValueError as exc:
            logger.info("[dedup] rejected tier policy %s: %s", model, exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def _batch_id(value: Optional[str]) -> Optional[str]:
        """Accept only a client-namespaced batch id, 400 on anything else.

        Thin alias for the shared
        :func:`~pixlstash.utils.request_origin.require_client_batch_id` so the
        call sites in this module read the same as they did; the rule itself has
        exactly one implementation.
        """
        return require_client_batch_id(value)

    def _scope(model: Optional[ScopeRequestModel]) -> DedupScope:
        """Build the service scope from a request model, 400 on a bad one."""
        try:
            return (model or ScopeRequestModel()).to_scope()
        except ValueError as exc:
            logger.info("[dedup] rejected scope %s: %s", model, exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get(
        "/dedup/policy",
        summary="Duplicate detection tier defaults",
        description=(
            "Returns the tier gating defaults plus the bounds and closed "
            "vocabularies a client builds its tier switches and threshold "
            "slider from, so 0.90 and the 0.65 floor are never hardcoded twice. "
            "Read-only."
        ),
        response_model=TierPolicyResponse,
    )
    def get_tier_policy():
        return {
            "defaults": TierPolicyModel(),
            "bounds": {
                "min_threshold": MIN_THRESHOLD,
                "max_threshold": MAX_THRESHOLD,
                "tiers": [tier.value for tier in dedup_tier_service.TIER_ORDER],
                "always_on_tiers": [DedupTier.EXACT.value],
                "tier_requires": {
                    DedupTier.EXACT.value: None,
                    DedupTier.NEAR.value: DedupTier.EXACT.value,
                    DedupTier.EMBEDDING.value: DedupTier.NEAR.value,
                },
                "scope_types": [item.value for item in ScopeType],
                "verdicts": [item.value for item in VERDICT_ORDER],
                "max_page_size": MAX_PAGE_SIZE,
            },
        }

    @router.get(
        "/dedup/groups",
        summary="One page of the duplicate queue",
        description=(
            "Returns unresolved duplicate groups, confidence descending, with "
            "each group's evidence pills, its cover preselection and every "
            "candidate's own evidence - so the client renders reasons rather "
            "than conclusions. The queue is paged from the database and is never "
            "loaded whole: 10 groups and 10,000 cost the same per page. The "
            "response also carries this scope's scan progress for the banner.\n\n"
            "Page with `cursor`: pass the previous page's `next_cursor` back "
            "verbatim, and stop when `next_cursor` is `null`. The queue is a "
            "live list - deciding a verdict on a delivered group removes it, and "
            "a tier-2 scan commits new groups after every bucket - so `offset` "
            "paging skips exactly as many groups as the previous page changed. "
            "`offset` still works for compatibility but is deprecated, and "
            "sending both `cursor` and `offset` is a 400."
        ),
        response_model=DedupQueueResponse,
    )
    def get_dedup_groups(
        near_enabled: bool = Query(
            default=False, description="Include tier 2 (bucketed near-duplicates)."
        ),
        embedding_enabled: bool = Query(
            default=False,
            description="Include tier 3. Requires `near_enabled`.",
        ),
        threshold: float = Query(
            default=DEFAULT_THRESHOLD,
            ge=MIN_THRESHOLD,
            le=MAX_THRESHOLD,
            description="Minimum similarity; exact matches are always included.",
        ),
        scope_type: ScopeType = Query(
            default=ScopeType.GLOBAL, description="Scope kind."
        ),
        scope_id: Optional[str] = Query(
            default=None, description="Scope id, required unless scope is global."
        ),
        offset: Optional[int] = Query(
            default=None,
            ge=0,
            deprecated=True,
            description=(
                "Groups to skip. Deprecated - use `cursor`; offset paging skips "
                "groups when the queue changes between pages. Mutually exclusive "
                "with `cursor`."
            ),
        ),
        cursor: Optional[str] = Query(
            default=None,
            max_length=MAX_CURSOR_LENGTH,
            description=(
                "Opaque keyset position from the previous page's `next_cursor`. "
                "Omit for the first page. Mutually exclusive with `offset`."
            ),
        ),
        limit: int = Query(
            default=DEFAULT_PAGE_SIZE,
            ge=1,
            le=MAX_PAGE_SIZE,
            description="Groups per page.",
        ),
        decided: bool = Query(
            default=False,
            description=(
                "Page the DECIDED groups instead of the open queue: resolved "
                "groups with their live verdict (`stacked` / `keep_separate`) "
                "and `decided_at`, so a decision can be reviewed and cleared "
                "via `POST /dedup/verdicts/reopen`. Ordered by `decided_at` "
                "descending - most recent decision first, not by confidence. "
                "Ignores the tier gate and the threshold - a policy change "
                "must not hide a decision. `next_cursor` values from the two "
                "orderings are distinct families and reject each other."
            ),
        ),
        verdict: Optional[list[DedupVerdictKind]] = Query(
            default=None,
            description=(
                "Repeatable. On a `decided=true` page, list only groups whose "
                "live verdict is one of these (`stacked`, `keep_separate`); "
                "omit for every decision. `total` and `next_cursor` are "
                "computed under the same filter, so the page and the count "
                "cannot disagree. Sending it without `decided` is a 400: the "
                "open queue's groups carry no verdict, so a filter there would "
                "silently empty it."
            ),
        ),
    ):
        if cursor is not None and offset is not None:
            # Refused rather than silently preferring one: a client that sends
            # both has two different ideas of where it is, and answering either
            # one hands back a page it did not ask for.
            raise HTTPException(
                status_code=400,
                detail=(
                    "cursor and offset are mutually exclusive; page with cursor "
                    "(offset is deprecated)"
                ),
            )
        if verdict and not decided:
            # Refused rather than ignored: a client that filters the OPEN queue
            # by verdict is asking for rows that cannot exist, and answering the
            # unfiltered queue would hand it a page it did not ask for.
            raise HTTPException(
                status_code=400,
                detail=(
                    "verdict filters the decided page; send it with decided=true "
                    "(open-queue groups carry no verdict)"
                ),
            )
        policy = _policy(
            TierPolicyModel(
                near_enabled=near_enabled,
                embedding_enabled=embedding_enabled,
                threshold=threshold,
            )
        )
        scope = _scope(ScopeRequestModel(scope_type=scope_type, scope_id=scope_id))
        try:
            return dedup_tier_service.queue_response(
                server.vault,
                policy,
                scope,
                offset or 0,
                limit,
                cursor,
                decided,
                [item.value for item in verdict or []],
            )
        except DedupCursorError as exc:
            logger.info("[dedup] rejected queue cursor: %s", exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get(
        "/dedup/stacks/{stack_id}/members",
        summary="One page of an existing stack's members",
        description=(
            "Returns the members of one existing stack, leader first, for the "
            "Duplicates queue's deck expansion.\n\n"
            "This is the **lazy half** of the queue's stack contract. A queue "
            "row already carries each stack's real member count and its leader "
            "in `groups[].stacks`, which is everything needed to draw the deck; "
            "the members themselves are fetched only when the user opens an "
            "expansion, because inlining them would put a 40-member stack's "
            "worth of tiles behind a row that has room for none.\n\n"
            "Members come back with exactly the fields a queue candidate "
            "carries, so the strip reuses the row's tile unchanged, plus "
            "`position` and `is_leader`. Paged with a plain `offset` (a stack's "
            "membership is not a live list being decided out from under the "
            "client, so the queue's keyset cursor buys nothing here); follow "
            "`next_offset` and stop when it is `null`. Read-only: nothing here "
            "reorders, promotes or unstacks anything."
        ),
        response_model=DedupStackMembersResponse,
        responses={
            404: {
                "description": (
                    "No live member carries this stack id: the stack was "
                    "dissolved, scrapheaped, or never existed. Reported rather "
                    "than answered with an empty stack that appears to exist."
                )
            }
        },
    )
    def get_dedup_stack_members(
        stack_id: int,
        offset: int = Query(
            default=0,
            ge=0,
            description=(
                "Members to skip, in canonical stack order. Use the previous "
                "page's `next_offset`."
            ),
        ),
        limit: int = Query(
            default=DEFAULT_STACK_MEMBER_PAGE_SIZE,
            ge=1,
            le=MAX_STACK_MEMBER_PAGE_SIZE,
            description=(
                f"Members per page, at most {MAX_STACK_MEMBER_PAGE_SIZE}. The "
                "cap is what keeps a pathological stack from returning an "
                "unbounded list."
            ),
        ),
    ):
        payload = dedup_tier_service.stack_members(
            server.vault, stack_id, offset, limit
        )
        if payload is None:
            logger.info(
                "[dedup] stack %s has no live members; expansion refused", stack_id
            )
            raise HTTPException(
                status_code=404,
                detail=f"stack {stack_id} has no live members",
            )
        return payload

    @router.post(
        "/dedup/counts",
        summary="Live duplicate counts, global and scoped",
        description=(
            "Returns the sidebar badge (unresolved duplicate groups across the "
            "vault), the per-tier split including tiers that are switched off, "
            "and one count per requested scope so a context menu can label every "
            'its "Find duplicates in ..." entries in a single request. '
            "Read-only despite being a POST: the scope list does not fit a URL."
        ),
        response_model=DedupCountsResponse,
    )
    def post_dedup_counts(
        payload: Optional[DedupCountsRequestModel] = Body(default=None),
    ):
        request_model = payload or DedupCountsRequestModel()
        policy = _policy(request_model.policy)
        scopes = [_scope(item) for item in request_model.scopes]
        return dedup_tier_service.counts_response(server.vault, policy, scopes)

    @router.post(
        "/dedup/scan",
        summary="Queue a duplicate scan",
        description=(
            "Queues a scan for the given scope and returns its progress row "
            "immediately - the queue can be opened while it runs. Cached hashes "
            "are reused (`pixel_sha` and `perceptual_hash` are computed on "
            "import), so a scoped scan only reads and compares them. Tier 1 "
            "completes in milliseconds; tier 2 streams its groups in as each "
            "candidate bucket finishes."
        ),
        response_model=ScanProgressModel,
    )
    def post_dedup_scan(payload: Optional[DedupScanRequestModel] = Body(default=None)):
        request_model = payload or DedupScanRequestModel()
        policy = _policy(request_model.policy)
        scope = _scope(request_model.scope)
        try:
            return dedup_tier_service.request_scan(server.vault, policy, scope)
        except DedupScanBusyError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "dedup_scan_busy",
                    "message": str(exc),
                    "active_scan": jsonable_encoder(exc.active_scan),
                },
            ) from exc

    @router.post(
        "/dedup/verdicts/stack",
        summary="Stack a duplicate group",
        description=(
            "Stacks the group's included members behind the chosen cover and "
            "applies the metadata union: tags, project and set membership are "
            "unioned onto every member and every member is lifted to the highest "
            "score. Nothing is overwritten, nothing is deleted, and no file "
            "moves - a stack is a grouping row plus a cover pointer, so dropping "
            "it restores the flat grid exactly. The verdict is remembered "
            "against the group signature, so a rescan never re-asks."
        ),
        response_model=VerdictResponse,
    )
    def post_stack_verdict(request: Request, payload: StackVerdictRequestModel):
        # §21 origin discipline: actor / source / origin_client_id are read from
        # the request HERE, on the request's own task, and passed down
        # explicitly. The contextvar is dead on the DB worker thread, so the
        # service must never read it for itself.
        context = operation_log_service.request_context(request)
        # The gesture header (X-Operation-Batch-Id) also arrives via
        # request_context. Precedence: an explicit body batch_id wins over the
        # ambient header; the service mints srv- when both are absent.
        header_batch_id = context.pop("batch_id", None)
        try:
            result = dedup_verdict_service.apply_stack_verdict(
                server.vault,
                payload.signature,
                payload.cover_picture_id,
                list(payload.excluded_picture_ids),
                _batch_id(payload.batch_id) or header_batch_id,
                **context,
            )
        except DedupVerdictError as exc:
            logger.info("[dedup] stack verdict rejected: %s", exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result.as_dict()

    @router.post(
        "/dedup/verdicts/keep-separate",
        summary="Record that a group is not duplicates",
        description=(
            "Remembers that these pictures should stay separate. No picture row "
            "changes. The decision is recorded as one undoable operation "
            "(`dedup.keep_separate`), exactly like a stack verdict: the "
            "returned `batch_id` is the `POST /operations/batches/{batch_id}/"
            "undo` handle, an undo returns the group to the queue and a redo "
            "re-decides it. The explicit reopen from the Stacks view remains "
            "available. The memory is what lets the sidebar count reach zero "
            "and stay there across rescans and re-imports."
        ),
        response_model=VerdictResponse,
    )
    def post_keep_separate_verdict(request: Request, payload: SignatureRequestModel):
        # §21 origin discipline, read in the handler - see post_stack_verdict.
        # Recording here is the owner's 2026-07-30 reversal of the #644-era
        # ruling that kept this verdict out of the operation log.
        context = operation_log_service.request_context(request)
        # Body batch_id wins over the ambient gesture header (see
        # post_stack_verdict); the service mints srv- when both are absent.
        header_batch_id = context.pop("batch_id", None)
        try:
            result = dedup_verdict_service.apply_keep_separate(
                server.vault,
                payload.signature,
                _batch_id(payload.batch_id) or header_batch_id,
                **context,
            )
        except DedupVerdictError as exc:
            logger.info("[dedup] keep-separate verdict rejected: %s", exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result.as_dict()

    @router.post(
        "/dedup/verdicts/batch",
        summary="Apply one atomic multi-group duplicate gesture",
        description=(
            "Applies every Stack or Keep Separate action in one database task "
            "and one transaction. All recorded operations share one contiguous "
            "batch, so another client cannot interleave a write into the middle "
            "of this undo unit. Any refused action rolls back the whole gesture."
        ),
        response_model=VerdictBatchResponse,
    )
    def post_verdict_batch(request: Request, payload: VerdictBatchRequestModel):
        context = operation_log_service.request_context(request)
        header_batch_id = context.pop("batch_id", None)
        actions = [
            {
                "verdict": action.verdict.value,
                "signature": action.signature,
                "cover_picture_id": action.cover_picture_id,
                "excluded_picture_ids": list(action.excluded_picture_ids),
            }
            for action in payload.actions
        ]
        try:
            return dedup_verdict_service.apply_verdict_batch(
                server.vault,
                actions,
                _batch_id(payload.batch_id) or header_batch_id,
                **context,
            )
        except DedupVerdictError as exc:
            logger.info("[dedup] verdict batch rejected: %s", exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/dedup/verdicts/reopen",
        summary="Return a decided group to the queue",
        description=(
            "Clears the memory of a verdict so the group is offered again. "
            "Clearing a `stacked` verdict whose stack still stands also "
            "dissolves that stack - restoring the recorded pre-verdict stack "
            "state, so a stack the verdict folded in comes back - because the "
            "open queue only offers groups whose members span two or more "
            "stack units. That unstack is recorded as one undoable "
            "`dedup.reopen` operation and the returned `batch_id` is its undo "
            "handle; undoing it restacks the pictures and re-marks the "
            "decision. A clear that touches no picture (keep-separate, or a "
            "stack already dissolved by hand) records nothing and returns a "
            "null `batch_id`. The metadata union is never reverted here - "
            "that full inverse is the verdict's own undo. The verdict row is "
            "kept and marked reopened rather than deleted, so the decision "
            "history survives."
        ),
        response_model=ReopenResponse,
    )
    def post_reopen_verdict(request: Request, payload: SignatureRequestModel):
        # §21 origin discipline, read in the handler - see post_stack_verdict.
        # A clear can now mutate picture stack state and record an operation,
        # so the context values are no longer dead arguments. Body batch_id
        # wins over the ambient gesture header; the service mints srv- when
        # both are absent AND pictures actually change.
        context = operation_log_service.request_context(request)
        header_batch_id = context.pop("batch_id", None)
        try:
            return dedup_verdict_service.reopen_verdict(
                server.vault,
                payload.signature,
                _batch_id(payload.batch_id) or header_batch_id,
                **context,
            )
        except DedupVerdictError as exc:
            logger.info("[dedup] reopen rejected: %s", exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/dedup/auto-stack",
        summary="Bulk auto-stack the exact tier",
        description=(
            "Stacks every unresolved **exact** group under one operation-log "
            "batch id, so a thousand stacks reverse with a single undo. Exact "
            "matches are the tier with no human judgment left in them, which is "
            "why they get one consent dialog instead of per-group adjudication; "
            "near and embedding groups always go through the queue no matter how "
            "confident they look. Defaults to `dry_run=true`, which returns the "
            "counts the dialog shows and writes nothing."
        ),
        response_model=AutoStackResponse,
    )
    def post_auto_stack(
        request: Request,
        payload: Optional[AutoStackRequestModel] = Body(default=None),
    ):
        request_model = payload or AutoStackRequestModel()
        scope = _scope(request_model.scope)
        # §21 origin discipline, read in the handler - see post_stack_verdict.
        # This is the most far-reaching mutation on the surface, so it is the one
        # that most needs an attributed audit row.
        context = operation_log_service.request_context(request)
        # Body batch_id wins over the ambient gesture header (see
        # post_stack_verdict); the service mints srv- when both are absent.
        header_batch_id = context.pop("batch_id", None)
        return dedup_verdict_service.bulk_auto_stack(
            server.vault,
            scope,
            _batch_id(request_model.batch_id) or header_batch_id,
            request_model.dry_run,
            request_model.limit,
            **context,
        )

    @router.get(
        "/dedup/sweep/policy",
        summary="Near-duplicate sweep policy defaults",
        description=(
            "Returns the server's default confidence policy plus the bounds and "
            "closed vocabularies a client should build its controls from, so "
            "thresholds are never hardcoded twice. Read-only."
        ),
        response_model=SweepPolicyResponse,
    )
    def get_sweep_policy():
        return {
            "defaults": SweepPolicyModel(),
            "bounds": {
                "min_likeness": MIN_LIKENESS,
                "max_likeness": MAX_LIKENESS,
                "verdicts": [item.value for item in SweepVerdict],
                "outcomes": [item.value for item in SweepOutcome],
                "review_reasons": [item.value for item in ReviewReason],
                "cross_stack_options": [item.value for item in CrossStackPolicy],
            },
        }

    @router.post(
        "/dedup/sweep/dry-run",
        summary="Plan a vault-wide near-duplicate sweep",
        description=(
            "Resolves every near-duplicate group in the vault under the supplied "
            "confidence policy and returns the plan - the data behind "
            '"N groups auto-collapse, M need review". **Nothing is written**: '
            "this is a dry run, and the sweep's resolution is stacking, never "
            "deletion. Groups spanning several existing stacks are reported as "
            "an explicit `merge_stacks` outcome rather than skipped. Send an "
            "empty body to use the server defaults from GET /dedup/sweep/policy."
        ),
        response_model=SweepReportResponse,
    )
    def dedup_sweep_dry_run(payload: Optional[SweepDryRunRequest] = Body(default=None)):
        request_model = payload or SweepDryRunRequest()
        policy_model = request_model.policy or SweepPolicyModel()
        try:
            policy = policy_model.to_policy()
        except ValueError as exc:
            logger.info(
                "[dedup-sweep] rejected policy %s: %s",
                policy_model.model_dump(),
                exc,
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        report = dedup_sweep_service.plan_near_duplicate_sweep(
            server.vault,
            policy,
            operation_batch_id=request_model.operation_batch_id,
        )
        return report.as_dict()

    # ── Mixed stacks (design D5 / B5) ───────────────────────────────────────

    @router.get(
        "/dedup/mixed-stacks",
        summary="Live stacks whose members do not all match",
        description=(
            "Lists **mixed stacks**: live stacks whose members do not form one "
            "connected cluster at the given similarity threshold, measured with "
            "the same 64-bit dHash Hamming distance and the same "
            "connected-components test the near tier uses.\n\n"
            "**Components, not the worst pair.** A legitimate burst chains "
            "A~B~C, so its ends can be far apart while every step is tight; "
            "condemning it on the worst pair would flag exactly the stacks a "
            "user most deliberately made. What is reported instead is whether "
            "the members hang together at all: how many clusters there are, how "
            "big each one is, which members are joined to nothing "
            "(`stranded_picture_ids`), and the weakest edge that survives.\n\n"
            "**Bound to the threshold, never to a constant.** The same stack is "
            "mixed at `0.90` and one clean cluster at `0.65`; pass the queue's "
            "own slider value and the list follows it.\n\n"
            "Ranked by how little holds each stack together - stranded members "
            "descending, then component count descending, then weakest edge "
            "ascending - so the clearest mistakes are first.\n\n"
            "Each row carries `stackable` / `blocked_by_sets`, the same pair "
            "`GET /dedup/stacks/{stack_id}/members` reports: `false` means a "
            "locked picture set freezes a member, so split and unstack will "
            "both answer `423` and the row's button should say so instead of "
            "offering the action. Read-only: nothing here splits, unstacks or "
            "marks anything."
        ),
        response_model=MixedStacksResponse,
    )
    def get_mixed_stacks(
        threshold: float = Query(
            default=DEFAULT_THRESHOLD,
            ge=MIN_THRESHOLD,
            le=MAX_THRESHOLD,
            description=(
                "Similarity at which members must connect. The queue's own "
                "threshold - pass the slider value, do not hardcode it. "
                "Example: `0.9`."
            ),
            examples=[0.9],
        ),
        offset: int = Query(
            default=0,
            ge=0,
            description=(
                "Rows to skip; follow the previous page's `next_offset`. Plain "
                "offset paging is correct here (unlike the queue's keyset "
                "cursor): this list is tens of rows, not thousands, and is not "
                "being decided out from under the client. Example: `0`."
            ),
            examples=[0],
        ),
        limit: int = Query(
            default=MIXED_STACK_PAGE_SIZE,
            ge=1,
            le=MAX_MIXED_STACK_PAGE_SIZE,
            description=(
                f"Rows per page, at most {MAX_MIXED_STACK_PAGE_SIZE}. Example: `20`."
            ),
            examples=[20],
        ),
        include_kept: bool = Query(
            default=False,
            description=(
                "Include stacks the user pressed **Keep** on, each marked "
                "`kept: true`. Off by default, because the point of Keep is "
                "that the row leaves the list; `kept_total` is reported either "
                "way. Example: `false`."
            ),
            examples=[False],
        ),
    ):
        return mixed_stack_service.list_mixed_stacks(
            server.vault, threshold, offset, limit, include_kept
        )

    @router.post(
        "/dedup/mixed-stacks/{stack_id}/split",
        summary="Split the marked member(s) off a mixed stack",
        description=(
            "Removes the marked pictures from the stack, leaving the cluster "
            "that does hang together. By default those are the members with no "
            "edge to any sibling at the given `threshold`. The removed pictures "
            "become loose; nothing is "
            "deleted and no file moves.\n\n"
            "Recorded as **one** operation, so a single `Ctrl+Z` puts every "
            "picture back in its original stack at its original position - the "
            "same undo contract the dedup verdicts carry. The response's "
            "`batch_id` is the `POST /operations/batches/{batch_id}/undo` "
            "handle.\n\n"
            "If the split would leave a stack of one, the last member is freed "
            "too and the stack row goes; the response says so in "
            "`stack_dissolved` rather than leaving the client to infer it.\n\n"
            "**Live members of this stack only.** `picture_ids` carries the "
            "marks the user made on the row, which start from "
            "`stranded_picture_ids` and are theirs to adjust, so an id the "
            "engine did not strand is accepted. What is refused is an id that "
            "is not a live member of this stack: a picture in another stack or "
            "in none, and a soft-deleted member, are each a `400`. Omit "
            "`picture_ids` and the stranded set at `threshold` is used, the "
            "same marking the row opens with.\n\n"
            "*(This bound was narrower for one day: a security review of "
            "2026-08-01 (F7) required the list to be a subset of the stranded "
            "set. It was widened deliberately on 2026-08-02, because a page "
            "where the user marks the strangers cannot have the engine's marks "
            "as a veto. F7 was rated LOW on its own reasoning that this is not "
            "a privilege boundary - the route is `OWNER_ONLY` and `DELETE "
            "/stacks/{stack_id}/members` already gives the same principal an "
            "unrestricted remove - so the widening grants no new capability.)*"
            "\n\n"
            "**A locked picture set refuses the whole stack** (`423`), never "
            "just the frozen member: a locked set freezes a stack's siblings "
            "*through* the stack, so detaching one severs a freeze the lock is "
            "there to hold. Rows carry `stackable` and `blocked_by_sets` so "
            "the button can say so before it is pressed."
        ),
        response_model=MixedStackActionResponse,
        responses={
            400: {
                "description": (
                    "The stack has no live members, `picture_ids` was empty, "
                    "`picture_ids` named something that is not a live member of "
                    "this stack (a foreign picture, or a soft-deleted member - "
                    "`detail` says which), or `picture_ids` was omitted and no "
                    "member is stranded at `threshold`, so there is nothing to "
                    "split."
                )
            },
            423: {
                "description": (
                    "A member of this stack is frozen by a locked picture set. "
                    "Nothing was written; `detail` names the sets and the "
                    "frozen picture ids."
                )
            },
        },
    )
    def post_mixed_stack_split(
        request: Request,
        stack_id: int,
        payload: Optional[MixedStackSplitRequestModel] = Body(default=None),
    ):
        # §21 origin discipline: actor / source / origin_client_id are read from
        # the request HERE, on the request's own task, and passed down
        # explicitly - the contextvar is dead on the DB worker thread.
        request_model = payload or MixedStackSplitRequestModel()
        context = operation_log_service.request_context(request)
        header_batch_id = context.pop("batch_id", None)
        try:
            return mixed_stack_service.split_stranded(
                server.vault,
                stack_id,
                request_model.picture_ids,
                request_model.threshold,
                _batch_id(request_model.batch_id) or header_batch_id,
                **context,
            )
        except MixedStackError as exc:
            logger.info("[mixed-stacks] split of stack %s rejected: %s", stack_id, exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/dedup/mixed-stacks/{stack_id}/unstack",
        summary="Dissolve a mixed stack",
        description=(
            "Frees every member of the stack and drops the stack itself. The "
            "outcome for a stack with no majority cluster - where there is no "
            "coherent group left once the strangers go.\n\n"
            "Recorded as **one** operation, so a single `Ctrl+Z` restacks every "
            "picture at its original position and recreates the stack under its "
            "original id. Nothing is deleted and no file moves: a stack is a "
            "grouping row plus a cover pointer.\n\n"
            "**A locked picture set refuses the stack** (`423`). Dissolving is "
            "the most complete form of the detach a lock forbids: it severs "
            "the through-stack freeze for every member at once, so a picture "
            "the lock was protecting becomes deletable. Rows carry "
            "`stackable` and `blocked_by_sets` so the button can say so before "
            "it is pressed."
        ),
        response_model=MixedStackActionResponse,
        responses={
            400: {"description": "The stack has no live members."},
            423: {
                "description": (
                    "A member of this stack is frozen by a locked picture set. "
                    "Nothing was written; `detail` names the sets and the "
                    "frozen picture ids."
                )
            },
        },
    )
    def post_mixed_stack_unstack(
        request: Request,
        stack_id: int,
        payload: Optional[MixedStackUnstackRequestModel] = Body(default=None),
    ):
        # §21 origin discipline, read in the handler - see post_mixed_stack_split.
        request_model = payload or MixedStackUnstackRequestModel()
        context = operation_log_service.request_context(request)
        header_batch_id = context.pop("batch_id", None)
        try:
            return mixed_stack_service.unstack(
                server.vault,
                stack_id,
                _batch_id(request_model.batch_id) or header_batch_id,
                **context,
            )
        except MixedStackError as exc:
            logger.info(
                "[mixed-stacks] unstack of stack %s rejected: %s", stack_id, exc
            )
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post(
        "/dedup/mixed-stacks/{stack_id}/keep",
        summary="Keep a mixed stack as it is",
        description=(
            "Records that this stack is fine and should stop being listed. "
            "Durable and server-side.\n\n"
            "**Keyed on the stack's membership, not just its id.** The "
            "dismissal stores the `membership_fingerprint` of the members that "
            "were approved, so adding a member later produces a fingerprint no "
            "dismissal matches and the stack returns to the list - the user "
            "approved *these* pictures together, not the stack forever. "
            "Removing that member again restores the dismissal rather than "
            "asking twice.\n\n"
            "Idempotent: pressing Keep on an unchanged stack returns "
            "`created: false` and writes nothing. This changes no picture, so "
            "it is not an undoable operation - `DELETE` the same path to clear "
            "it."
        ),
        response_model=MixedStackKeepResponse,
        responses={
            400: {"description": "The stack has no live members."},
        },
    )
    def post_mixed_stack_keep(request: Request, stack_id: int):
        # Only `actor` is used - the dismissal changes no picture facet, so
        # there is no WS announcement and no operation-log row to originate.
        context = operation_log_service.request_context(request)
        try:
            return mixed_stack_service.dismiss_stack(
                server.vault, stack_id, context.get("actor")
            )
        except MixedStackError as exc:
            logger.info("[mixed-stacks] keep of stack %s rejected: %s", stack_id, exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete(
        "/dedup/mixed-stacks/{stack_id}/keep",
        summary="Undo a Keep",
        description=(
            "Clears every `Keep` on this stack, whatever membership each was "
            "made at, so the stack is listed again if it is still mixed. The "
            "way back from a mis-pressed Keep.\n\n"
            "Every fingerprint is cleared rather than only the current one: a "
            "dismissal made at an older membership would otherwise re-hide the "
            "stack the moment an unrelated membership change was undone, and a "
            "Keep the user has explicitly retracted must not come back on its "
            "own. Idempotent - clearing a stack that was never kept returns "
            "`removed: 0`."
        ),
        response_model=MixedStackKeepResponse,
    )
    def delete_mixed_stack_keep(stack_id: int):
        return mixed_stack_service.undismiss_stack(server.vault, stack_id)

    return router
