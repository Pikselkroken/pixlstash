"""Service layer for tagger evaluation runs pushed from PixlTagger.

PixlStash is the system of record for the tagger's history: PixlTagger POSTs its
report after every eval (including rejected runs). This module owns the DB side of
that — extracting the indexed fields from a pushed report and upserting on the run
name, plus listing stored runs for the stats panel.

Mirrors the vault-task conventions in :mod:`pixlstash.services.tag_prediction_service`.
"""

from typing import TYPE_CHECKING

from sqlmodel import Session, select

from pixlstash.db_models.tagger_run import TaggerRun
from pixlstash.pixl_logging import get_logger

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)


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


def ingest_run(vault: "Vault", report: dict) -> TaggerRun:
    """Upsert a pushed report (report.json payload) by run name and return the row.

    Stores every run pushed, including rejected ones, so the trend reflects the full
    history. Re-pushing a run with the same name updates it in place.

    Args:
        vault: Application vault, used for DB task dispatch.
        report: The raw report payload posted by PixlTagger.

    Returns:
        The persisted :class:`TaggerRun`.

    Raises:
        ValueError: If the report has no ``payload.run`` name to key on.
    """
    meta = _extract(report)
    run = meta["run"]
    if not run:
        raise ValueError("report payload.run is required")

    def _save(session: Session) -> TaggerRun:
        existing = session.exec(select(TaggerRun).where(TaggerRun.run == run)).first()
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

    return vault.db.run_task(_save)


def get_latest_tag_precisions(session: Session) -> dict[str, float]:
    """Return ``{lowercased tag: precision}`` from the most recent run that reports it.

    Reads per-tag precision out of the latest :class:`TaggerRun` whose report carries it
    (``report['payload']['per_tag'][i]['precision']``). Scans the few newest runs so a
    just-pushed run that happens to omit precision falls back to the previous one. Returns
    an empty map when nothing usable is found; callers then use
    :data:`pixlstash.utils.quality.anomaly_penalty.DEFAULT_TAG_PRECISION`.

    Takes a live ``session`` (not a vault) so the smart-score fetch paths can call it
    inside their existing read task without a second dispatch.
    """
    runs = session.exec(
        select(TaggerRun).order_by(TaggerRun.created_at.desc()).limit(10)
    ).all()
    for run in runs:
        report = run.report
        if not isinstance(report, dict):
            continue
        payload = report.get("payload", report)
        if not isinstance(payload, dict):
            continue
        per_tag = payload.get("per_tag")
        if not isinstance(per_tag, list):
            continue
        precisions: dict[str, float] = {}
        for entry in per_tag:
            if not isinstance(entry, dict):
                continue
            tag = entry.get("tag")
            precision = entry.get("precision")
            if tag is None or precision is None:
                continue
            try:
                precisions[str(tag).strip().lower()] = float(precision)
            except (TypeError, ValueError):
                continue
        if precisions:
            return precisions
    return {}


def list_runs(vault: "Vault", limit: int = 100) -> list[TaggerRun]:
    """Return stored runs for the stats panel, most recent first.

    Args:
        vault: Application vault, used for DB task dispatch.
        limit: Max rows to return (clamped by the caller to a sane range).

    Returns:
        List of :class:`TaggerRun` ordered by ``created_at`` descending.
    """

    def _fetch(session: Session) -> list[TaggerRun]:
        return list(
            session.exec(
                select(TaggerRun).order_by(TaggerRun.created_at.desc()).limit(limit)
            ).all()
        )

    return vault.db.run_immediate_read_task(_fetch)
