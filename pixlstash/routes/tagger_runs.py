"""HTTP routes for tagger evaluation runs pushed from PixlTagger.

PixlStash is the system of record for the tagger's history: PixlTagger POSTs its report
after every eval (including rejected runs), and this stores them for the stats panel.
Ingest is an upsert on the run name, so re-pushing a run updates it.
"""

from typing import Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, ConfigDict

from pixlstash.db_models.tagger_run import TaggerRun
from pixlstash.pixl_logging import get_logger
from pixlstash.services import tagger_run_service

logger = get_logger(__name__)


class TaggerRunResponse(BaseModel):
    """A stored tagger evaluation run."""

    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    run: str
    model_version: Optional[str] = None
    verdict: Optional[str] = None
    recommend: Optional[str] = None
    accepted: Optional[str] = None
    anomaly_macro_f1: Optional[float] = None
    created_at: Optional[str] = None
    report: Optional[dict] = None


def _serialize(r: TaggerRun) -> dict:
    return {
        "id": r.id,
        "run": r.run,
        "model_version": r.model_version,
        "verdict": r.verdict,
        "recommend": r.recommend,
        "accepted": r.accepted,
        "anomaly_macro_f1": r.anomaly_macro_f1,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "report": r.report,
    }


def create_router(server) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/tagger-runs",
        summary="Ingest a tagger evaluation run from PixlTagger",
        description=(
            "Upserts a run's report (report.json payload) by run name. Stores every "
            "run pushed, including rejected ones, so the trend reflects the full history."
        ),
        response_model=TaggerRunResponse,
    )
    def ingest_tagger_run(report: dict = Body(...)):
        try:
            saved = tagger_run_service.ingest_run(server.vault, report)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return _serialize(saved)

    @router.get(
        "/tagger-runs",
        summary="List ingested tagger runs (newest first)",
        description="Returns stored runs for the stats panel, most recent first.",
        response_model=list[TaggerRunResponse],
    )
    def list_tagger_runs(limit: int = 100):
        limit = max(1, min(limit, 1000))
        rows = tagger_run_service.list_runs(server.vault, limit)
        return [_serialize(r) for r in rows]

    return router
