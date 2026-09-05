from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class TagHealth(SQLModel, table=True):
    """Cached per-tag health signals for the tag health board (landing view).

    One row per tag, rebuilt on demand by
    :mod:`pixlstash.services.tag_health_service` (indexed SQL over
    ``tag_prediction`` / ``tag`` / ``tag_suggestion`` / ``picture``; no
    embeddings, no kNN). A cache, not user data: rows are wholesale replaced
    on every rebuild. ``tag`` is the DEFAULT_TAG_MERGES-folded identity (a
    child tag such as "extra digit" has no row of its own; its signal is
    folded into "malformed hand"'s row).

    Signals (see the redesign doc for definitions and thresholds):
        est_wrong: tagged pictures whose prediction confidence is very low, on
            the current model version only.
        est_missing: untagged pictures whose prediction confidence is very
            high, on the current model version only.
        est_wrong_adj: est_wrong discounted by the tag's measured precision
            (from the latest TaggerRun report), so an unreliable tag doesn't
            dominate the "estimated fixes" ranking; falls back to
            DEFAULT_TAG_PRECISION when no report covers the tag.
        est_missing_adj: est_missing, same precision discount as
            est_wrong_adj.
        mismatch: same-stack pairs + stored high-likeness pairs disagreeing on
            the tag (never a live O(N²) sweep).
        verified_pct: share of the tag's prediction rows with a non-UNKNOWN
            ledger ``label_state`` ("somebody looked").
        boundary_pct: share of predictions in the ambiguous middle band -
            flags fuzzy tag *definitions*.
        overturn_rate: ACCEPTED / (ACCEPTED + DISMISSED) over the tag's
            reviewed suggestions; NULL when the tag has no reviewed history.
        model_disputes: human-frozen labels the current prediction strongly
            contradicts (surfaced, never auto-requeued - human outranks model).
        has_model: the tag has prediction rows for the current model version;
            tags with no predictions at all still get a row with
            ``has_model=False`` (the board shows "no model signal").
        ground_truth: how many in-scope, non-deleted PICTURES carry this tag
            (confirmed examples). Counts pictures, not ``tag`` rows, and is
            DEFAULT_TAG_MERGES-folded like every other signal, so it matches
            the equivalence set ``tag_scan_service.scan_tag`` votes against.
            ``0`` means a review would fall back to the confidence-only
            bootstrap path. ``None`` means the row predates this field and has
            not been rebuilt yet - *not* zero.
    """

    __tablename__ = "tag_health"

    id: Optional[int] = Field(default=None, primary_key=True)

    tag: str = Field(index=True, unique=True)

    est_wrong: int = Field(default=0)
    est_missing: int = Field(default=0)
    # Precision-discounted counterparts of the two fields above (see class
    # docstring). Nullable: existing rows keep NULL until the next rebuild
    # recomputes them.
    est_wrong_adj: Optional[float] = Field(default=None)
    est_missing_adj: Optional[float] = Field(default=None)
    mismatch: int = Field(default=0)
    verified_pct: float = Field(default=0.0)
    boundary_pct: float = Field(default=0.0)
    overturn_rate: Optional[float] = Field(default=None)
    model_disputes: int = Field(default=0)
    has_model: bool = Field(default=False)
    # Count of distinct non-deleted pictures carrying the folded tag (see class
    # docstring). Zero is load-bearing: it is what lets the board tell the user a
    # review would find nothing before they start one. Nullable precisely so a
    # measured 0 stays distinguishable from "this row predates the field" - a
    # backfilled 0 would make the board claim zero yield for every stale row.
    # Every rebuild writes a real int; NULL only ever appears pre-rebuild.
    ground_truth: Optional[int] = Field(default=None)

    # Latest reviewed_at over the tag's suggestions (any source); NULL when the
    # tag has never had a suggestion reviewed ("Last review: never").
    last_reviewed_at: Optional[datetime] = Field(default=None)

    computed_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
