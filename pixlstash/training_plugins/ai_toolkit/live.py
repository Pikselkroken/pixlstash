"""What the ai-toolkit plugin reads of a run for the live view and the service.

ai-toolkit's half of the live view: its info line in the per-job ``Job``
table, its step times in ``loss_log.db``, its "Generating images - n/m"
progress, its out-of-memory text and its rendered config's preview and save
settings. :class:`~pixlstash.training_plugins.ai_toolkit.adapter.AiToolkitPlugin`
answers the :class:`~pixlstash.training.backend.TrainerBackend` live-view hooks
with these; the generic timeline is :mod:`pixlstash.training.live_view`.
"""

from __future__ import annotations

import os
import re
import sqlite3

from pixlstash.pixl_logging import get_logger
from pixlstash.training import live_view
from pixlstash.utils.sqlite_uri import sqlite_file_uri

logger = get_logger(__name__)

# ai-toolkit's "Generating images - 3/6": the sample being drawn, of how many.
_PREVIEWS_RE = re.compile(r"generating images\s*-\s*(\d+)\s*/\s*(\d+)", re.I)

# huggingface_hub's progress lines while it fetches a model: the xet
# "Downloading bytes", "<file>: reconstructing file" and "Fetching n files",
# or a plain "<file>.safetensors:  12%|" bar.
_DOWNLOAD_RE = re.compile(
    r"\bdownloading\b|reconstructing|fetching \d+ files"
    r"|\.(?:safetensors|bin|gguf|pth?)\S*:\s+\d+%",
    re.I,
)
_DOWNLOAD_DONE_RE = re.compile(r"\bcomplete\b|100%", re.I)
# Enough of the log's end to hold its last progress line.
_DOWNLOAD_TAIL_BYTES = 2048

# ai-toolkit's out-of-memory errors, from torch and from its own messages.
OOM_RE = re.compile(r"out of memory|outofmemoryerror|\bOOM\b", re.I)
# torch's CUDA OOM message: what it asked for, and the card's free and total.
_OOM_FIGURES_RE = re.compile(
    r"Tried to allocate ([\d.]+ [KMG]iB).*?total capacity of ([\d.]+ [KMG]iB)"
    r" of which ([\d.]+ [KMG]iB) is free",
    re.S,
)
# The card's memory specifically, so a host-RAM error or an "oom" in a path
# keeps its own words.
_GPU_OOM_RE = re.compile(
    r"(?:CUDA|MPS|GPU|HIP)\b[^.\n]*out of memory|OutOfMemoryError", re.I
)
# ...and one clause per other process holding memory on the card.
_OOM_OTHER_RE = re.compile(
    r"Process \d+ has ([\d.]+) (bytes|KiB|MiB|GiB) memory in use"
)
_UNIT_MIB = {"bytes": 1 / 1024**2, "KiB": 1 / 1024, "MiB": 1.0, "GiB": 1024.0}
# A failure longer than this is cut; the whole text stays in the run's report.
_FAILURE_CHARS = 200


def running_phase(info: str | None, step: int) -> str:
    """The phase a running run is in, from ai-toolkit's info line.

    ai-toolkit writes "Loading model", "Loading dataset" (where it caches the
    latents and caption embeddings), "Training", "Generating images - n/m" and
    "Saving model" into its job row; the model's own loaders write other lines
    while it loads.
    """
    text = (info or "").strip().lower()
    if text.startswith("generating images"):
        return "previews"
    if text.startswith("saving"):
        return "saving"
    if text.startswith("loading dataset") or "cach" in text:
        return "caching"
    if text == "training" or step > 0:
        return "training"
    return "loading"


def phase_of(
    status: str, info: str | None, step: int, completed_at: str | None
) -> str | None:
    """:func:`pixlstash.training.live_view.phase_of` with ai-toolkit's phases."""
    return live_view.phase_of(status, info, step, completed_at, running_phase)


def preview_progress(info: str | None) -> dict | None:
    """``{"drawing": n, "total": m}`` while previews are drawn, else None.

    ``drawing`` is the 1-based number of the sample being drawn now.
    """
    match = _PREVIEWS_RE.search(info or "")
    if match is None:
        return None
    return {"drawing": int(match.group(1)), "total": int(match.group(2))}


def downloading(log_path: str) -> bool:
    """True while the trainer's log ends on a model download's progress line.

    A model a trainer has not loaded before is fetched while it loads, which
    on a first run is most of the wait; its info line says only that it loads.
    """
    try:
        with open(log_path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(fh.tell() - _DOWNLOAD_TAIL_BYTES, 0))
            tail = fh.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("Could not read the trainer log %s: %s", log_path, exc)
        return False
    lines = [line for line in re.split(r"[\r\n]", tail) if line.strip()]
    if not lines:
        return False
    last = lines[-1]
    return bool(_DOWNLOAD_RE.search(last)) and not _DOWNLOAD_DONE_RE.search(last)


def failure_summary(info: str | None, step: int) -> str:
    """One readable sentence for why a run failed, from the trainer's info line.

    ai-toolkit stores the exception text, and torch's CUDA OOM is a paragraph
    listing every process on the card. That paragraph becomes what it asked
    for, what was free, and how much other programs held, which is the part
    someone can act on. Anything else is cut to its first line.
    """
    text = (info or "").strip()
    if not text:
        return "Training failed."
    if _GPU_OOM_RE.search(text):
        summary = f"Ran out of GPU memory at step {step}"
        figures = _OOM_FIGURES_RE.search(text)
        if figures is None:
            return summary + "."
        asked, total, free = figures.groups()
        summary += f": it needed {asked} more, with {free} of {total} free."
        others = [
            float(size) * _UNIT_MIB[unit] for size, unit in _OOM_OTHER_RE.findall(text)
        ]
        if others:
            held = sum(others) / 1024
            programs = "program" if len(others) == 1 else "programs"
            summary += (
                f" {len(others)} other {programs} held {held:.2f} GiB of the card;"
                " close them and train again."
            )
        return summary
    first = text.splitlines()[0].strip()
    if len(first) > _FAILURE_CHARS:
        first = first[: _FAILURE_CHARS - 1].rstrip() + "…"
    return first


def sample_settings(rendered_config: dict | None) -> dict:
    """The run's preview and save settings from its rendered config.

    Returns:
        ``{sample_every, save_every, prompts, layer_offloading,
        offload_percent}``; a value the config does not carry is None.
    """
    try:
        process = rendered_config["config"]["process"][0]
    except (KeyError, IndexError, TypeError):
        return {
            "sample_every": None,
            "save_every": None,
            "prompts": [],
            "layer_offloading": False,
            "offload_percent": None,
        }
    sample = process.get("sample") or {}
    model = process.get("model") or {}
    prompts = [
        str(entry.get("prompt") or "")
        for entry in sample.get("samples") or []
        if isinstance(entry, dict)
    ]
    percent = model.get("layer_offloading_transformer_percent")
    return {
        "sample_every": _positive_int(sample.get("sample_every")),
        "save_every": _positive_int((process.get("save") or {}).get("save_every")),
        "prompts": prompts,
        "layer_offloading": bool(model.get("layer_offloading")),
        "offload_percent": float(percent)
        if isinstance(percent, (int, float))
        else None,
    }


def _positive_int(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def read_trainer_info(sqlite_db_path: str, job_uuid: str) -> str:
    """The trainer's own info line ("Generating images - 3/6"), or "".

    It lives in the per-job ``Job`` table ai-toolkit writes, not on the hub row.
    """
    try:
        conn = sqlite3.connect(
            sqlite_file_uri(sqlite_db_path, read_only=True), uri=True, timeout=2.0
        )
    except sqlite3.Error as exc:
        logger.debug("No trainer job row in %s: %s", sqlite_db_path, exc)
        return ""
    try:
        conn.execute("PRAGMA query_only=1")
        row = conn.execute("SELECT info FROM Job WHERE id = ?", (job_uuid,)).fetchone()
    except sqlite3.Error as exc:
        logger.debug(
            "Could not read the trainer job row in %s: %s", sqlite_db_path, exc
        )
        return ""
    finally:
        conn.close()
    return str(row[0] or "") if row else ""


def read_step_times(loss_db_path: str) -> list[tuple[int, float]]:
    """``(step, wall_time)`` of every logged step, oldest first; [] when unreadable.

    The same read-only, WAL-aware open the adapter uses for the loss curve.
    """
    try:
        conn = sqlite3.connect(
            sqlite_file_uri(loss_db_path, read_only=True), uri=True, timeout=2.0
        )
    except sqlite3.Error as exc:
        logger.debug("No step times in %s: %s", loss_db_path, exc)
        return []
    try:
        conn.execute("PRAGMA query_only=1")
        rows = conn.execute(
            "SELECT step, wall_time FROM steps ORDER BY step"
        ).fetchall()
    except sqlite3.Error as exc:
        logger.debug("Could not read step times from %s: %s", loss_db_path, exc)
        return []
    finally:
        conn.close()
    times = []
    for step, wall in rows:
        if wall is None:
            continue
        try:
            times.append((int(step), float(wall)))
        except (TypeError, ValueError) as exc:
            # One bad row is skipped, so the rest of the speed still reads.
            logger.warning(
                "Skipping an unreadable step time in %s: %s", loss_db_path, exc
            )
    return times
