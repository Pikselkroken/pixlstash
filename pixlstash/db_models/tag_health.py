from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class TagHealth(SQLModel, table=True):
    """Cached per-tag health signals for the tag health board (landing view).

    One row per tag, rebuilt on demand by
    :mod:`pixlstash.services.tag_health_service` (indexed SQL over
    ``tag_prediction`` / ``tag`` / ``tag_suggestion`` / ``picture``; no
    embeddings, no kNN). A cache, not user data: rows are wholesale replaced
    on every rebuild.

    Signals (see the redesign doc for definitions and thresholds):
        est_wrong: tagged pictures whose prediction confidence is very low.
        est_missing: untagged pictures whose prediction confidence is very high.
        mismatch: same-stack pairs + stored high-likeness pairs disagreeing on
            the tag (never a live O(N²) sweep).
        verified_pct: share of the tag's prediction rows with a non-UNKNOWN
            ledger ``label_state`` ("somebody looked").
        boundary_pct: share of predictions in the ambiguous middle band —
            flags fuzzy tag *definitions*.
        overturn_rate: ACCEPTED / (ACCEPTED + DISMISSED) over the tag's
            reviewed suggestions; NULL when the tag has no reviewed history.
        model_disputes: human-frozen labels the current prediction strongly
            contradicts (surfaced, never auto-requeued — human outranks model).
        has_model: the tag has prediction rows for the current model version;
            tags with no predictions at all still get a row with
            ``has_model=False`` (the board shows "no model signal").
    """

    __tablename__ = "tag_health"

    id: Optional[int] = Field(default=None, primary_key=True)

    tag: str = Field(index=True, unique=True)

    est_wrong: int = Field(default=0)
    est_missing: int = Field(default=0)
    mismatch: int = Field(default=0)
    verified_pct: float = Field(default=0.0)
    boundary_pct: float = Field(default=0.0)
    overturn_rate: Optional[float] = Field(default=None)
    model_disputes: int = Field(default=0)
    has_model: bool = Field(default=False)

    # Latest reviewed_at over the tag's suggestions (any source); NULL when the
    # tag has never had a suggestion reviewed ("Last review: never").
    last_reviewed_at: Optional[datetime] = Field(default=None)

    computed_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
