from datetime import datetime
from typing import Optional

from sqlalchemy import Column, JSON
from sqlmodel import Field, SQLModel


class TaggerRun(SQLModel, table=True):
    """An evaluation report pushed from PixlTagger after a training run.

    PixlStash is the system of record for the tagger's history, so it stores *every*
    run PixlTagger pushes — including the ones the gate rejected — with the full report
    payload (per-tag precision/recall/F1, deltas, trend, narrative) kept as JSON. The
    indexed columns are just what the stats panel sorts/charts by; ``report`` holds the
    rest verbatim. Upserted on ``run`` so re-pushing a run updates it in place.
    """

    __tablename__ = "tagger_run"

    id: Optional[int] = Field(default=None, primary_key=True)

    run: str = Field(index=True, unique=True)  # e.g. "run-140"
    model_version: Optional[str] = Field(default=None, index=True)
    # Gate outcome — stored so the trend can show rejected attempts, not just promoted ones.
    verdict: Optional[str] = Field(default=None)  # e.g. "regressed" | "improved"
    recommend: Optional[str] = Field(default=None)  # e.g. "promote" | "hold"
    accepted: Optional[str] = Field(default=None)  # the baseline run it was compared against
    anomaly_macro_f1: Optional[float] = Field(default=None)

    # The full report.json payload (per_tag, deltas, drift, trend, narrative, …).
    report: Optional[dict] = Field(default=None, sa_column=Column(JSON))

    created_at: Optional[datetime] = Field(
        default_factory=datetime.utcnow, index=True
    )
