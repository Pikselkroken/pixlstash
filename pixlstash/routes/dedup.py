"""HTTP routes for the vault-wide near-duplicate sweep (dry run only).

Two endpoints, both owner-only:

* ``GET  /dedup/sweep/policy``  — the default confidence policy plus the bounds
  every knob is validated against, so a client renders its controls from the
  server's values instead of re-hardcoding thresholds.
* ``POST /dedup/sweep/dry-run`` — resolve every near-duplicate group in the vault
  under a supplied policy and return the plan: how many groups auto-collapse, how
  many need review, why each review group needs review, what each group would do
  to existing stacks, and how many bytes the non-keeper members hold.

**Nothing here mutates anything.** The sweep's execution step is a later lane;
this surface is the policy-level consent screen's data source. Resolution means
stacking, never deleting — see :mod:`pixlstash.services.dedup_sweep_service`.

Authorization is declared in ``pixlstash/authz/registry.py`` (both routes
``OWNER_ONLY``) and enforced by the central gate before these handlers run; per
§16.1 there is deliberately no inline scope check here. A vault-wide aggregate
cannot be narrowed to a share token's scope without leaking counts about pictures
outside it, which is the same reasoning that makes the tag-health board owner-only.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from pixlstash.pixl_logging import get_logger
from pixlstash.services import dedup_sweep_service
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


def create_router(server) -> APIRouter:
    router = APIRouter()

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
