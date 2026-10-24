"""What a workflow's ComfyUI runs need on the GPU, learned from their runs.

Every run PixlStash submits is measured (``comfyui_tenant.PeakSampler``) and
its peak written here with how it ended and the size of the image it worked
on (``run_pixels``). A run's **need** is the largest of the newest five peaks
measured at about its size (within ``SIZE_BAND``), each scaled up to it, times
``COMFYUI_PEAK_MARGIN`` where the card had room for it (``_need``): one run on
a very large picture does not set what a small one waits for. With no such
peak the run is unmeasured, and runs with the other heavy tenants kept off
the card (D2). ``forget_peaks`` is the owner's reset.

A workflow can also run **alone** (``workflow_vram_policy``): set by the owner,
or once its newest ``OOM_STREAK`` runs all ran out of memory when the owner's
``auto_exclusive_after_oom`` allows it.

Tables in ``hub/schema.py``: ``workflow_vram_peak``, ``workflow_vram_policy``,
``user_vram_preference``.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from pixlstash.pixl_logging import get_logger
from pixlstash.resources.arbiter import safety_margin_mb

logger = get_logger(__name__)

#: Headroom over the largest measured peak.
COMFYUI_PEAK_MARGIN = 1.1
#: How many of the newest peaks the need is taken from.
NEED_WINDOW = 5
#: How many peaks are kept per workflow.
KEPT_PEAKS = 50
#: How many runs in a row must run out of memory before one runs alone.
OOM_STREAK = 3
#: A peak is used for a run within this factor of its image size, either way:
#: a 2x upscale is four times the pixels, and a different run.
SIZE_BAND = 1.5
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


def run_pixels(graph: dict, input_pixels=()) -> int | None:
    """The size of the image a run works on, in pixels, or None when unknown.

    The largest of the pictures put in (*input_pixels*) and of every size a
    latent or image node of *graph* sets, times its batch and frame count. A
    size wired from another node is read when that node holds it as a literal
    ``value`` (a primitive), and not otherwise.
    """
    sizes = [pixels for pixels in input_pixels if pixels]
    for node in (graph or {}).values():
        if not isinstance(node, dict):
            continue
        kind = str(node.get("class_type")).lower()
        if "latent" not in kind and "image" not in kind:
            continue
        inputs = node.get("inputs") or {}
        width = _literal(graph, inputs.get("width"))
        height = _literal(graph, inputs.get("height"))
        if not (_count(width) and _count(height)):
            continue
        size = width * height
        for key in ("batch_size", "length"):
            value = _literal(graph, inputs.get(key))
            if _count(value):
                size *= value
        sizes.append(size)
    return max(sizes) if sizes else None


def _literal(graph: dict, value):
    """*value*, or the literal ``value`` of the node a link points at."""
    if isinstance(value, list) and value:
        source = graph.get(str(value[0]))
        if isinstance(source, dict):
            return (source.get("inputs") or {}).get("value")
        return None
    return value


def _count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def record_peak(
    hub,
    workflow_id: str,
    model_set: str,
    device_name: str,
    device_total_mb: int,
    peak_mb: int,
    measured_by: str,
    outcome: str,
    pixels: int | None = None,
) -> None:
    """Write one run's peak and keep the newest ``KEPT_PEAKS`` of the workflow."""
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_vram_peak (workflow_id, model_set, device_name, "
            "device_total_mb, peak_mb, measured_by, outcome, at, pixels) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                workflow_id,
                model_set,
                device_name,
                int(device_total_mb),
                int(peak_mb),
                measured_by,
                outcome,
                _now(),
                pixels,
            ),
        )
        conn.execute(
            "DELETE FROM workflow_vram_peak WHERE workflow_id = ? AND id NOT IN "
            "(SELECT id FROM workflow_vram_peak WHERE workflow_id = ? "
            "ORDER BY at DESC, id DESC LIMIT ?)",
            (workflow_id, workflow_id, KEPT_PEAKS),
        )
    logger.info(
        "Workflow %s peaked at %s MB on %s (%s, %s, %s pixels)",
        workflow_id,
        peak_mb,
        device_name,
        outcome,
        measured_by,
        pixels,
    )


def forget_peaks(hub, workflow_id: str) -> int:
    """Forget every peak measured for a workflow, the owner's reset; how many.

    Its next run is unmeasured and measures it again. The out-of-memory
    streak goes with the rows it is counted from; "runs alone" is a switch of
    its own and stays as it is. A run still going writes its peak when it
    ends, after the reset.
    """
    with hub.transaction() as conn:
        gone = conn.execute(
            "DELETE FROM workflow_vram_peak WHERE workflow_id = ?", (workflow_id,)
        ).rowcount
    logger.info("Forgot the %s measured GPU peaks of workflow %s", gone, workflow_id)
    return gone


def _peaks(hub, workflow_ids: list[str]) -> dict[str, list]:
    if not workflow_ids:
        return {}
    marks = ",".join("?" for _ in workflow_ids)
    rows = hub.fetchall(
        "SELECT workflow_id, model_set, peak_mb, outcome, at, pixels, "
        "device_total_mb FROM "
        f"workflow_vram_peak WHERE workflow_id IN ({marks}) "
        "ORDER BY at DESC, id DESC",
        tuple(workflow_ids),
    )
    found: dict[str, list] = {}
    for row in rows:
        found.setdefault(row["workflow_id"], []).append(row)
    return found


def _need_rows(rows: list, model_set: str | None, pixels: int | None = None) -> list:
    """The rows the need is taken from: this model set's, else the workflow's.

    An aborted run never peaked, and an out-of-memory one is recorded at the
    card's total, which no budget can ever meet: the "runs alone" flag is what
    answers an OOM, not a need nothing fits. A run of a known size takes only
    the rows within ``SIZE_BAND`` of it, so a row of no known size never; one
    of unknown size takes them all.
    """
    usable = [row for row in rows if row["outcome"] not in ("aborted", "oom")]
    if pixels:
        usable = [
            row
            for row in usable
            if row["pixels"]
            and pixels / SIZE_BAND <= row["pixels"] <= pixels * SIZE_BAND
        ]
    if model_set is not None:
        same = [row for row in usable if row["model_set"] == model_set]
        if same:
            return same[:NEED_WINDOW]
    return usable[:NEED_WINDOW]


def _need(rows: list, pixels: int | None) -> int | None:
    """The need from *rows*: the largest peak, each scaled up to *pixels* and
    never down, with ``COMFYUI_PEAK_MARGIN`` while the card has room for it.

    The margin never lifts a need past the card's *ceiling*, its total less
    the arbiter's safety margin (what an otherwise empty card grants), divided
    by the margin: a peak that high is taken to have filled the card, since
    ComfyUI keeps models resident while there is room, so it is what the run
    completed in and asks for no more (#2081). Nor does a need pass the
    ceiling itself, which leaves it waiting for ever.
    """
    if not rows:
        return None
    needs = []
    for row in rows:
        peak = row["peak_mb"] * max(1.0, pixels / row["pixels"] if pixels else 1.0)
        ceiling = row["device_total_mb"] - safety_margin_mb(row["device_total_mb"])
        if ceiling <= 0:
            # A card whose size was not read: no ceiling to judge the margin
            # by, so the need is the peak the run completed in.
            needs.append(int(peak))
            continue
        # ponytail: "filled the card" is read off the card's size, not the
        # room the run actually had; record that per run if it misjudges.
        headroom = min(peak * COMFYUI_PEAK_MARGIN, ceiling / COMFYUI_PEAK_MARGIN)
        needs.append(int(min(max(peak, headroom), ceiling)))
    return max(needs)


def need_mb(
    hub,
    workflow_id: str | None,
    model_set: str | None = None,
    pixels: int | None = None,
) -> int | None:
    """What a run of *workflow_id* on an image of *pixels* needs, in MB, or
    None when never measured at about that size."""
    if not workflow_id:
        return None
    rows = _peaks(hub, [workflow_id]).get(workflow_id, [])
    return _need(_need_rows(rows, model_set, pixels), pixels)


def ran_out_in_a_row(hub, workflow_id: str | None) -> bool:
    """Whether the newest ``OOM_STREAK`` recorded runs all ran out of memory.

    A stopped run says nothing about memory, so it neither counts nor breaks
    the streak. A run with no GPU to measure writes no row and is not counted.
    """
    if not workflow_id:
        return False
    rows = [
        row
        for row in _peaks(hub, [workflow_id]).get(workflow_id, [])
        if row["outcome"] != "aborted"
    ][:OOM_STREAK]
    return len(rows) == OOM_STREAK and all(row["outcome"] == "oom" for row in rows)


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
        # The newest run's model set and size, as admission would ask for a
        # run like it: a workflow measured with two base models, or at two
        # sizes, shows the need that run will get.
        usable = [row for row in rows if row["outcome"] not in ("aborted", "oom")]
        newest = usable[0] if usable else None
        pixels = newest["pixels"] if newest is not None else None
        used = _need_rows(
            rows, newest["model_set"] if newest is not None else None, pixels
        )
        used_ids = {id(row) for row in used}
        held = policies.get(workflow_id)
        cards[workflow_id] = {
            "need_mb": _need(used, pixels),
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
                    "pixels": row["pixels"],
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
