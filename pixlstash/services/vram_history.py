"""What a workflow's ComfyUI runs need on the GPU, learned from their runs.

Every run PixlStash submits is measured (``comfyui_tenant.PeakSampler``) and
its peak written here with how it ended. A workflow's **need** is the largest
of its newest five peaks, times ``COMFYUI_PEAK_MARGIN``; with no peak yet it is
unmeasured, and runs with the other heavy tenants kept off the card (D2).

A workflow can also run **alone** (``workflow_vram_policy``): set by the owner,
or after a run ran out of memory when the owner's ``auto_exclusive_after_oom``
allows it.

Tables in ``hub/schema.py``: ``workflow_vram_peak``, ``workflow_vram_policy``,
``user_vram_preference``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

#: Headroom over the largest measured peak.
COMFYUI_PEAK_MARGIN = 1.1
#: How many of the newest peaks the need is taken from.
NEED_WINDOW = 5
#: How many peaks are kept per workflow.
KEPT_PEAKS = 50
#: Below this many measured runs there is no trend.
TREND_MIN_RUNS = 10
#: A mean that moved this much between two windows of five is a trend.
TREND_THRESHOLD = 0.10

#: Graph inputs that name the base model, text encoder or VAE. A LoRA's do not
#: count: ``model_set`` is what makes a run's memory, and every LoRA
#: combination of one workflow would otherwise be a workflow never measured.
_MODEL_INPUTS = frozenset(
    {
        "ckpt_name",
        "unet_name",
        "model_name",
        "clip_name",
        "clip_name1",
        "clip_name2",
        "clip_name3",
        "vae_name",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def model_set_of(graph: dict) -> str:
    """A digest of the base model, text encoder and VAE a graph loads."""
    names = sorted(
        f"{key}={value}"
        for node in (graph or {}).values()
        if isinstance(node, dict) and "lora" not in str(node.get("class_type")).lower()
        for key, value in (node.get("inputs") or {}).items()
        if key in _MODEL_INPUTS and isinstance(value, str)
    )
    return hashlib.sha256(json.dumps(names).encode()).hexdigest()[:16]


def record_peak(
    hub,
    workflow_id: str,
    model_set: str,
    device_name: str,
    device_total_mb: int,
    peak_mb: int,
    measured_by: str,
    outcome: str,
) -> None:
    """Write one run's peak and keep the newest ``KEPT_PEAKS`` of the workflow."""
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_vram_peak (workflow_id, model_set, device_name, "
            "device_total_mb, peak_mb, measured_by, outcome, at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                workflow_id,
                model_set,
                device_name,
                int(device_total_mb),
                int(peak_mb),
                measured_by,
                outcome,
                _now(),
            ),
        )
        conn.execute(
            "DELETE FROM workflow_vram_peak WHERE workflow_id = ? AND id NOT IN "
            "(SELECT id FROM workflow_vram_peak WHERE workflow_id = ? "
            "ORDER BY at DESC, id DESC LIMIT ?)",
            (workflow_id, workflow_id, KEPT_PEAKS),
        )
    logger.info(
        "Workflow %s peaked at %s MB on %s (%s, %s)",
        workflow_id,
        peak_mb,
        device_name,
        outcome,
        measured_by,
    )


def _peaks(hub, workflow_ids: list[str]) -> dict[str, list]:
    if not workflow_ids:
        return {}
    marks = ",".join("?" for _ in workflow_ids)
    rows = hub.fetchall(
        "SELECT workflow_id, model_set, peak_mb, outcome, at FROM "
        f"workflow_vram_peak WHERE workflow_id IN ({marks}) "
        "ORDER BY at DESC, id DESC",
        tuple(workflow_ids),
    )
    found: dict[str, list] = {}
    for row in rows:
        found.setdefault(row["workflow_id"], []).append(row)
    return found


def _need_rows(rows: list, model_set: str | None) -> list:
    """The rows the need is taken from: this model set's, else the workflow's.

    An aborted run never peaked, and an out-of-memory one is recorded at the
    card's total, which no budget can ever meet: the "runs alone" flag is what
    answers an OOM, not a need nothing fits.
    """
    usable = [row for row in rows if row["outcome"] not in ("aborted", "oom")]
    if model_set is not None:
        same = [row for row in usable if row["model_set"] == model_set]
        if same:
            return same[:NEED_WINDOW]
    return usable[:NEED_WINDOW]


def need_mb(hub, workflow_id: str | None, model_set: str | None = None) -> int | None:
    """What a run of *workflow_id* needs, in MB, or None when never measured."""
    if not workflow_id:
        return None
    rows = _need_rows(_peaks(hub, [workflow_id]).get(workflow_id, []), model_set)
    if not rows:
        return None
    return int(max(row["peak_mb"] for row in rows) * COMFYUI_PEAK_MARGIN)


def policy(hub, workflow_id: str | None) -> dict | None:
    """``{exclusive, source, at}`` for a workflow that runs alone, else None."""
    if not workflow_id:
        return None
    row = hub.fetchone(
        "SELECT exclusive, source, at FROM workflow_vram_policy WHERE workflow_id = ?",
        (workflow_id,),
    )
    if row is None or not row["exclusive"]:
        return None
    return {"exclusive": True, "source": row["source"], "at": row["at"]}


def set_exclusive(hub, workflow_id: str, exclusive: bool, source: str) -> None:
    """Make a workflow run alone (``source`` ``owner`` or ``oom``), or not."""
    with hub.transaction() as conn:
        if exclusive:
            conn.execute(
                "INSERT INTO workflow_vram_policy (workflow_id, exclusive, source, at) "
                "VALUES (?, 1, ?, ?) ON CONFLICT(workflow_id) DO UPDATE SET "
                "exclusive = 1, source = excluded.source, at = excluded.at",
                (workflow_id, source, _now()),
            )
        else:
            conn.execute(
                "DELETE FROM workflow_vram_policy WHERE workflow_id = ?",
                (workflow_id,),
            )
    logger.info(
        "Workflow %s %s (%s)",
        workflow_id,
        "now runs alone on the GPU" if exclusive else "shares the GPU again",
        source,
    )


def auto_exclusive_after_oom(hub, user_id: int | None) -> bool:
    """The owner's setting; on when never set."""
    if user_id is None:
        return True
    row = hub.fetchone(
        "SELECT auto_exclusive_after_oom FROM user_vram_preference WHERE user_id = ?",
        (user_id,),
    )
    return True if row is None else bool(row["auto_exclusive_after_oom"])


def set_auto_exclusive_after_oom(hub, user_id: int, enabled: bool) -> None:
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO user_vram_preference (user_id, auto_exclusive_after_oom) "
            "VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET "
            "auto_exclusive_after_oom = excluded.auto_exclusive_after_oom",
            (user_id, 1 if enabled else 0),
        )


def _trend(rows: list) -> str | None:
    # An out-of-memory run is recorded at the card's total, not measured.
    measured = [
        row["peak_mb"] for row in rows if row["outcome"] not in ("aborted", "oom")
    ]
    if len(measured) < TREND_MIN_RUNS:
        return None
    newest = sum(measured[:NEED_WINDOW]) / NEED_WINDOW
    before = sum(measured[NEED_WINDOW : 2 * NEED_WINDOW]) / NEED_WINDOW
    if before <= 0:
        return None
    change = (newest - before) / before
    if change > TREND_THRESHOLD:
        return "rising"
    if change < -TREND_THRESHOLD:
        return "falling"
    return "flat"


def card_vram(hub, workflow_ids: list[str], with_peaks: bool = False) -> dict:
    """The ``vram`` block of each workflow card, by workflow id."""
    peaks = _peaks(hub, workflow_ids)
    policies = {}
    if workflow_ids:
        marks = ",".join("?" for _ in workflow_ids)
        for row in hub.fetchall(
            "SELECT workflow_id, source, at FROM workflow_vram_policy "
            f"WHERE exclusive = 1 AND workflow_id IN ({marks})",
            tuple(workflow_ids),
        ):
            policies[row["workflow_id"]] = row
    cards = {}
    for workflow_id in workflow_ids:
        rows = peaks.get(workflow_id, [])
        # The newest run's model set, as admission would ask for the next run:
        # a workflow measured with two base models shows the need it will get.
        usable = [row for row in rows if row["outcome"] not in ("aborted", "oom")]
        used = _need_rows(rows, usable[0]["model_set"] if usable else None)
        used_ids = {id(row) for row in used}
        held = policies.get(workflow_id)
        cards[workflow_id] = {
            "need_mb": int(max(row["peak_mb"] for row in used) * COMFYUI_PEAK_MARGIN)
            if used
            else None,
            "measured_runs": sum(
                1 for row in rows if row["outcome"] not in ("aborted", "oom")
            ),
            "exclusive": held is not None,
            "exclusive_source": held["source"] if held is not None else None,
            "exclusive_at": held["at"] if held is not None else None,
            "trend": _trend(rows),
            "peaks": [
                {
                    "peak_mb": int(row["peak_mb"]),
                    "outcome": row["outcome"],
                    "at": row["at"],
                    "used": id(row) in used_ids,
                }
                for row in rows
            ]
            if with_peaks
            else [],
        }
    return cards
