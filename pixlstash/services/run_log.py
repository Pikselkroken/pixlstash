"""The runs that ended, kept for the Tasks tab's Finished group (#2050).

How a run ended is otherwise in memory only (``comfyui_service``'s outcomes),
so after a restart the Tasks tab could offer to save what a run changed and say
nothing of the run. Here every run PixlStash queues is a row from the moment it
is queued (:func:`queued`) until it is pruned, settled where every ending is
recorded (:func:`ended`, told by ``comfyui_service.record_run_outcome``).

A **batch** is what one ``POST /workflows/run`` made of one workflow: its runs
share a ``batch_id``, the first run's id, and are one Finished row once every
one of them has ended. The batch a workflow's run set names (``RAN_BY``) is the
one that offers to save it.

How long a batch stays: the newest :data:`KEPT_BATCHES` of the last
:data:`KEPT_FOR`, and a batch a run set names until that set goes (dismissed,
saved, or the workflow run again).

Table in ``hub/schema.py``: ``workflow_run_log``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Callable

from pixlstash.hub import workflow_versions
from pixlstash.hub.workflow_group_writes import RAN, RAN_BY, clear_changes
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

#: How many finished batches the group shows.
KEPT_BATCHES = 10
#: How long a finished batch is shown.
KEPT_FOR = timedelta(hours=24)

# A run queued before this process started that never ended was lost with the
# restart: nothing here will ever record its ending.
_STARTED = datetime.now(timezone.utc).isoformat()

LOST_MESSAGE = (
    "PixlStash restarted before this run ended, so what it made is not known."
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def queued(
    hub,
    run_id: str,
    batch_id: str,
    workflow_id: str | None,
    label: str,
    library_uuid: str | None,
) -> None:
    """Note one run as queued, in *batch_id*, and prune what has gone stale."""
    with hub.transaction() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO workflow_run_log (run_id, batch_id, "
            "workflow_id, label, library_uuid, status, queued_at) "
            "VALUES (?, ?, ?, ?, ?, 'queued', ?)",
            (run_id, batch_id, workflow_id, label, library_uuid, _now()),
        )
        _prune(conn)


def ended(hub, outcome: dict) -> None:
    """Settle a run's row from how it ended: ``record_run_outcome``'s record
    (``run_id``, ``status``, ``message``, ``picture_ids``). A run nothing
    queued here (``POST /comfyui/run``'s) has no row, and nothing changes.
    A completed run stays completed: a failure recorded after it (a step
    past the import that raised) would read as a run that made nothing."""
    run_id = outcome.get("run_id")
    if not run_id:
        return
    with hub.transaction() as conn:
        conn.execute(
            "UPDATE workflow_run_log SET status = ?, message = ?, "
            "picture_ids = ?, ended_at = ? WHERE run_id = ? "
            "AND status <> 'completed'",
            (
                outcome.get("status") or "failed",
                outcome.get("message"),
                json.dumps([int(i) for i in outcome.get("picture_ids") or []]),
                _now(),
                run_id,
            ),
        )


def offered(hub) -> dict[str, str]:
    """``{batch id: workflow id}`` for every batch a workflow's run set names
    and that set still applies to: made on the workflow's current version, as
    ``routes/workflows._stored_changes`` reads it. The workflow id is the
    set's, which follows a workflow whose id moved."""
    found: dict[str, str] = {}
    for workflow_id, batch_id, base in _run_sets(hub.fetchall(_RUN_SETS, (RAN,))):
        if base == workflow_versions.current_version(hub, workflow_id):
            found[batch_id] = workflow_id
    return found


def finished(
    hub,
    library_uuid: str | None,
    existing: Callable[[list[int]], set[int]],
) -> list[dict]:
    """The batches of *library_uuid* whose every run has ended, newest first.

    Each is ``{id, workflow_id, label, ended_at, status, message, made,
    picture_ids, offer}``: *made* is how many pictures it imported,
    *picture_ids* those of them *existing* still finds, *status*
    ``completed`` when any run completed, else ``failed`` with the first
    failure's *message*, and *offer* whether this is the batch its workflow's
    run set was made with.
    """
    offers = offered(hub)
    cutoff = (datetime.now(timezone.utc) - KEPT_FOR).isoformat()
    batches: dict[str, list] = {}
    for row in hub.fetchall(
        "SELECT * FROM workflow_run_log WHERE library_uuid IS NULL "
        "OR library_uuid = ? ORDER BY queued_at, run_id",
        (library_uuid,),
    ):
        batches.setdefault(row["batch_id"], []).append(row)
    found = []
    for batch_id, rows in batches.items():
        lost = [r for r in rows if r["ended_at"] is None]
        if any(r["queued_at"] >= _STARTED for r in lost):
            continue  # still going
        ended_at = max((r["ended_at"] or r["queued_at"]) for r in rows)
        if batch_id not in offers and ended_at < cutoff:
            continue
        made = [i for r in rows for i in _ids(r)]
        completed = any(r["status"] == "completed" for r in rows)
        failure = next(
            (
                r["message"] or (LOST_MESSAGE if r["ended_at"] is None else None)
                for r in rows
                if r["status"] != "completed"
            ),
            None,
        )
        alive = existing(made) if made else set()
        found.append(
            {
                "id": batch_id,
                "workflow_id": offers.get(batch_id, rows[0]["workflow_id"]),
                "label": rows[0]["label"],
                "ended_at": ended_at,
                "status": "completed" if completed else "failed",
                "message": None if completed else failure,
                "made": len(made),
                "picture_ids": [i for i in made if i in alive],
                "offer": batch_id in offers,
            }
        )
    found.sort(key=lambda batch: batch["ended_at"], reverse=True)
    kept, shown = [], 0
    for batch in found:
        if batch["offer"] or shown < KEPT_BATCHES:
            kept.append(batch)
            shown += not batch["offer"]
    return kept


def forget(hub, batch_id: str, library_uuid: str | None) -> tuple[int, str | None]:
    """Drop one batch of *library_uuid*, and the run set made with it in the
    same step.

    Answers how many runs went and the workflow whose run set went with
    them, else ``None``. A set made on an older version of its workflow,
    which nothing offers any more, goes too: it was this batch's. A batch
    of another library is left alone, and so is its set.
    """
    with hub.transaction() as conn:
        gone = conn.execute(
            "DELETE FROM workflow_run_log WHERE batch_id = ? AND "
            "(library_uuid IS NULL OR library_uuid = ?)",
            (batch_id, library_uuid),
        ).rowcount
        if not gone:
            return 0, None
        workflow_id = next(
            (
                wf
                for wf, named, _base in _run_sets(
                    conn.execute(_RUN_SETS, (RAN,)).fetchall()
                )
                if named == batch_id
            ),
            None,
        )
        if workflow_id is not None:
            clear_changes(conn, workflow_id, RAN)
    return gone, workflow_id


def forget_finished(hub, library_uuid: str | None) -> int:
    """Clear the Finished group of *library_uuid*: every batch it lists but
    those with a run set, which only their own dismiss throws away. One past
    what it lists is already on its way out (:func:`_prune`). Answers how
    many batches went."""
    gone = [
        batch["id"]
        for batch in finished(hub, library_uuid, lambda _ids: set())
        if not batch["offer"]
    ]
    with hub.transaction() as conn:
        conn.executemany(
            "DELETE FROM workflow_run_log WHERE batch_id = ?",
            [(batch_id,) for batch_id in gone],
        )
    return len(gone)


def _ids(row) -> list[int]:
    try:
        ids = json.loads(row["picture_ids"] or "[]")
    except ValueError as exc:
        logger.warning(
            "Run %s: its picture ids will not read, so it shows none: %s",
            row["run_id"],
            exc,
        )
        return []
    return [i for i in ids if isinstance(i, int)]


_RUN_SETS = (
    "SELECT workflow_id, changes, base_version FROM workflow_change WHERE kind = ?"
)


def _run_sets(rows) -> list[tuple[str, str, int | None]]:
    """``(workflow id, batch id, base version)`` of every run set naming a
    batch, out of the rows of :data:`_RUN_SETS`."""
    found = []
    for row in rows:
        try:
            batch_id = json.loads(row[1]).get(RAN_BY)
        except (ValueError, AttributeError) as exc:
            logger.warning(
                "Workflow %s: its run set will not read, so no run offers it: %s",
                row[0],
                exc,
            )
            continue
        if isinstance(batch_id, str) and batch_id:
            found.append((row[0], batch_id, row[2]))
    return found


def _prune(conn) -> None:
    """Drop the batches no longer shown: past the newest :data:`KEPT_BATCHES`,
    or older than :data:`KEPT_FOR`, unless a run set names it. A batch still
    going is the newest, and one queued in the last day is never dropped."""
    keep = {
        batch_id
        for _wf, batch_id, _base in _run_sets(
            conn.execute(_RUN_SETS, (RAN,)).fetchall()
        )
    }
    cutoff = (datetime.now(timezone.utc) - KEPT_FOR).isoformat()
    rows = conn.execute(
        "SELECT batch_id, MAX(COALESCE(ended_at, queued_at)) AS at, "
        "SUM(ended_at IS NULL) AS going FROM workflow_run_log "
        "GROUP BY batch_id ORDER BY at DESC"
    ).fetchall()
    shown = 0
    gone = []
    for batch_id, at, going in rows:
        if batch_id in keep or (going and at >= cutoff):
            continue
        if at >= cutoff and shown < KEPT_BATCHES:
            shown += 1
            continue
        gone.append((batch_id,))
    conn.executemany("DELETE FROM workflow_run_log WHERE batch_id = ?", gone)
