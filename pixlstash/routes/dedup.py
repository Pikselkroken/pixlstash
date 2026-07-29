"""HTTP routes for duplicate detection: the tiered queue plus the sweep dry run.

Two surfaces live here, and every route is owner-only.

**The v1.9 Duplicates queue** (:mod:`pixlstash.services.dedup_tier_service` and
:mod:`pixlstash.services.dedup_verdict_service`):

* ``GET  /dedup/policy``            — tier defaults + bounds; the client renders
  its tier switches and threshold slider from these values rather than
  re-hardcoding 0.90 and 0.65.
* ``GET  /dedup/groups``            — one page of the queue, confidence
  descending, plus this scope's scan progress for the banner.
* ``POST /dedup/counts``            — the sidebar badge, the per-tier counts, and
  as many scoped counts as the context menus need, in one request. Read-only
  despite the verb: the scope list does not fit in a URL.
* ``POST /dedup/scan``              — queue a scoped scan; returns immediately.
* ``POST /dedup/verdicts/stack``    — stack a group behind a chosen cover.
* ``POST /dedup/verdicts/keep-separate`` — remember that a group is not
  duplicates; permanent until reopened.
* ``POST /dedup/verdicts/reopen``   — return a decided group to the queue.
* ``POST /dedup/auto-stack``        — the exact tier's bulk action, one
  operation-log batch id so N stacks reverse with one undo.

**The vault-wide sweep dry run** (:mod:`pixlstash.services.dedup_sweep_service`),
unchanged and still non-destructive:

* ``GET  /dedup/sweep/policy``  — the default confidence policy plus its bounds.
* ``POST /dedup/sweep/dry-run`` — the vault-wide plan behind "N groups
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
from pydantic import BaseModel, ConfigDict, Field

from pixlstash.pixl_logging import get_logger
from pixlstash.services import dedup_sweep_service, dedup_tier_service
from pixlstash.services import dedup_verdict_service, operation_log_service
from pixlstash.services.dedup_tier_service import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_THRESHOLD,
    MAX_PAGE_SIZE,
    MAX_THRESHOLD,
    MIN_THRESHOLD,
    DedupScope,
    DedupTier,
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

logger = get_logger(__name__)

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
            "remaining gates. Either way the group is represented — unlike the "
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
        pair that contradicts itself still raises — resolving a default is not
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
            "The group's weakest observed likeness edge — the weak link of a "
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
            "in the count, so a lower value is a 400, never a silent clamp."
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
    cover_score: float = Field(
        description=(
            "`megapixels*4 + tags*3 + score*2 + 8 if RAW`. The highest wins the "
            "cover preselection; ties break to the oldest capture time."
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
    member_count: int = Field(description="How many pictures are in the group.")
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
    candidates: list[DedupCandidateModel] = Field(
        default_factory=list,
        description="Every member, cover first, with its own evidence.",
    )


class ScanProgressModel(BaseModel):
    """The "scanned N of M" banner's data."""

    model_config = ConfigDict(extra="allow")

    status: str = Field(
        description="`idle`, `pending`, `running`, `complete` or `failed`."
    )
    scanned_pictures: int = Field(description="Pictures covered so far.")
    total_pictures: int = Field(description="Pictures in scope.")
    scanned_buckets: int = Field(
        description="Tier-2 candidate buckets finished. Groups appear as these land."
    )
    total_buckets: int = Field(description="Tier-2 candidate buckets in total.")
    groups_found: int = Field(description="Unresolved groups this scan produced.")
    error: Optional[str] = Field(
        default=None, description="Why the scan failed, when it did."
    )


class DedupQueueResponse(BaseModel):
    """One page of the queue plus everything the header needs."""

    model_config = ConfigDict(extra="allow")

    groups: list[DedupGroupModel] = Field(
        default_factory=list, description="This page, confidence descending."
    )
    total: int = Field(
        description=(
            "Complete unresolved-group count in scope, so the client can size "
            "its scrollbar without a second request. The queue is paged from the "
            "database and is never loaded whole."
        )
    )
    offset: int = Field(description="Echo of the requested offset.")
    limit: int = Field(description="Effective page size after clamping.")
    policy: TierPolicyModel = Field(description="The policy this page was read under.")
    scope: dict[str, Any] = Field(description="The scope this page was read under.")
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
            "id across several calls to make them reverse as one undo."
        ),
    )


class SignatureRequestModel(BaseModel):
    """A verdict route that needs nothing but the group signature."""

    model_config = ConfigDict(extra="forbid")

    signature: str = Field(description="The group signature.")
    batch_id: Optional[str] = Field(
        default=None, description="Operation-log batch to record under."
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
            "Operation-log batch id. Omit to have the server mint one; every "
            "stack in the run shares it, so N stacks reverse with one undo."
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


def create_router(server) -> APIRouter:
    router = APIRouter()

    def _policy(model: Optional[TierPolicyModel]) -> TierPolicy:
        """Build the service policy from a request model, 400 on a bad one."""
        try:
            return (model or TierPolicyModel()).to_policy()
        except ValueError as exc:
            logger.info("[dedup] rejected tier policy %s: %s", model, exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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
                "verdicts": [
                    dedup_tier_service.VERDICT_STACKED,
                    dedup_tier_service.VERDICT_KEEP_SEPARATE,
                ],
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
            "response also carries this scope's scan progress for the banner."
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
        offset: int = Query(default=0, ge=0, description="Groups to skip."),
        limit: int = Query(
            default=DEFAULT_PAGE_SIZE,
            ge=1,
            le=MAX_PAGE_SIZE,
            description="Groups per page.",
        ),
    ):
        policy = _policy(
            TierPolicyModel(
                near_enabled=near_enabled,
                embedding_enabled=embedding_enabled,
                threshold=threshold,
            )
        )
        scope = _scope(ScopeRequestModel(scope_type=scope_type, scope_id=scope_id))
        return dedup_tier_service.queue_response(
            server.vault, policy, scope, offset, limit
        )

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
        return dedup_tier_service.request_scan(server.vault, policy, scope)

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
        try:
            result = dedup_verdict_service.apply_stack_verdict(
                server.vault,
                payload.signature,
                payload.cover_picture_id,
                list(payload.excluded_picture_ids),
                payload.batch_id,
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
            "changes. The decision is permanent until it is reopened from the "
            "Stacks view, which is what lets the sidebar count reach zero and "
            "stay there across rescans and re-imports."
        ),
        response_model=VerdictResponse,
    )
    def post_keep_separate_verdict(payload: SignatureRequestModel):
        # No request_context here on purpose: keep-separate writes no operation
        # row (it changes no reversible picture facet — see
        # dedup_verdict_service.OP_TYPE_STACK), so actor / source would be dead
        # arguments. If this verdict ever starts recording, wire it up like
        # post_stack_verdict does.
        try:
            result = dedup_verdict_service.apply_keep_separate(
                server.vault, payload.signature, payload.batch_id
            )
        except DedupVerdictError as exc:
            logger.info("[dedup] keep-separate verdict rejected: %s", exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result.as_dict()

    @router.post(
        "/dedup/verdicts/reopen",
        summary="Return a decided group to the queue",
        description=(
            "Clears the memory of a verdict so the group is offered again. The "
            "pictures are untouched: reopening a `stacked` verdict does not "
            "unstack anything, because unstacking is the Stacks view's own "
            "action. The verdict row is kept and marked reopened rather than "
            "deleted, so the decision history survives."
        ),
        response_model=ReopenResponse,
    )
    def post_reopen_verdict(payload: SignatureRequestModel):
        # No request_context here on purpose — see post_keep_separate_verdict.
        try:
            return dedup_verdict_service.reopen_verdict(server.vault, payload.signature)
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
        # §21 origin discipline, read in the handler — see post_stack_verdict.
        # This is the most far-reaching mutation on the surface, so it is the one
        # that most needs an attributed audit row.
        context = operation_log_service.request_context(request)
        return dedup_verdict_service.bulk_auto_stack(
            server.vault,
            scope,
            request_model.batch_id,
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
            "confidence policy and returns the plan — the data behind "
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

    return router
