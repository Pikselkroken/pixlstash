"""HTTP routes for tagger evaluation runs pushed from PixlTagger.

PixlStash is the system of record for the tagger's history: PixlTagger POSTs its report
after every eval (including rejected runs), and this stores them for the stats panel.
Ingest is an upsert on the run name, so re-pushing a run updates it.
"""

from typing import Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, select

from pixlstash.db_models.tagger_run import TaggerRun
from pixlstash.pixl_logging import get_logger

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


def _extract(report: dict) -> dict:
    """Pull the indexed fields out of a pushed report (report.json shape)."""
    payload = report.get("payload", report) if isinstance(report, dict) else {}
    if not isinstance(payload, dict):
        payload = {}
    macro = None
    deltas = payload.get("deltas")
    if isinstance(deltas, dict):
        amf = deltas.get("anomaly_macro_f1")
        if isinstance(amf, dict):
            macro = amf.get("candidate")
    return {
        "run": payload.get("run"),
        "verdict": payload.get("verdict"),
        "recommend": payload.get("recommend"),
        "accepted": payload.get("accepted"),
        "anomaly_macro_f1": macro,
        "model_version": payload.get("model_version") or report.get("model_version"),
    }


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
        meta = _extract(report)
        run = meta["run"]
        if not run:
            raise HTTPException(
                status_code=400, detail="report payload.run is required"
            )

        def _save(session: Session) -> TaggerRun:
            existing = session.exec(
                select(TaggerRun).where(TaggerRun.run == run)
            ).first()
            if existing is None:
                existing = TaggerRun(run=run)
                session.add(existing)
            existing.model_version = meta["model_version"]
            existing.verdict = meta["verdict"]
            existing.recommend = meta["recommend"]
            existing.accepted = meta["accepted"]
            existing.anomaly_macro_f1 = meta["anomaly_macro_f1"]
            existing.report = report
            session.commit()
            session.refresh(existing)
            return existing

        return _serialize(server.vault.db.run_task(_save))

    @router.get(
        "/tagger-runs",
        summary="List ingested tagger runs (newest first)",
        description="Returns stored runs for the stats panel, most recent first.",
        response_model=list[TaggerRunResponse],
    )
    def list_tagger_runs(limit: int = 100):
        limit = max(1, min(limit, 1000))

        def _fetch(session: Session) -> list[TaggerRun]:
            return list(
                session.exec(
                    select(TaggerRun)
                    .order_by(TaggerRun.created_at.desc())
                    .limit(limit)
                ).all()
            )

        rows = server.vault.db.run_immediate_read_task(_fetch)
        return [_serialize(r) for r in rows]

    return router
