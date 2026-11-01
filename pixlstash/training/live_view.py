"""What the Training runs view draws for one run while it trains (#2118).

Pure functions over what a run already leaves behind: its ``training_run`` row,
its rendered config, its run folder and the trainer's ``loss_log.db``. The route
in ``routes/training.py`` reads those and the machine's memory, and this module
turns them into the view's one timeline: the phase, the checkpoint columns, the
prompt rows, which sample fills which cell, the speed and the time left.

Every figure says where it came from (``measured``, ``estimated`` or ``none``),
and a figure that could not be read is ``None``, never 0: the view draws
"Unknown" for it.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import statistics
from datetime import datetime

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.sqlite_uri import sqlite_file_uri

logger = get_logger(__name__)

# The eight phases, in the order a run passes through them. The first four are
# setup, which the view folds to one line once training has begun.
PHASES = (
    "waiting_gpu",
    "checking",
    "loading",
    "caching",
    "training",
    "previews",
    "saving",
    "importing",
)
SETUP_PHASES = PHASES[:4]

MEASURED = "measured"
ESTIMATED = "estimated"
NOT_MEASURED = "none"

# ai-toolkit's "Generating images - 3/6": the sample being drawn, of how many.
_PREVIEWS_RE = re.compile(r"generating images\s*-\s*(\d+)\s*/\s*(\d+)", re.I)

# huggingface_hub's progress lines while it fetches a model: the xet
# "Downloading bytes", "<file>: reconstructing file" and "Fetching n files",
# or a plain "<file>.safetensors:  12%|" bar.
_DOWNLOAD_RE = re.compile(
    r"download|reconstruct|fetching \d+ files|\.(?:safetensors|bin|gguf|pth?)\S*:\s+\d+%",
    re.I,
)
_DOWNLOAD_DONE_RE = re.compile(r"complete|100%")
# Enough of the log's end to hold its last progress line.
_DOWNLOAD_TAIL_BYTES = 2048

# How many of the latest steps the speed is taken over.
_SPEED_WINDOW = 50

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


def phase_of(
    status: str, info: str | None, step: int, completed_at: str | None
) -> str | None:
    """The phase a run is in, from its status and the trainer's info line.

    ai-toolkit writes "Loading model", "Loading dataset" (where it caches the
    latents and caption embeddings), "Training", "Generating images - n/m" and
    "Saving model" into its job row; the model's own loaders write other lines
    while it loads. A ``completed`` run with no ``completed_at`` is being
    imported: the worker writes ``completed_at`` once the import is done.

    Returns:
        One of :data:`PHASES`, or None for a run that has ended.
    """
    if status == "queued":
        return "waiting_gpu"
    if status == "completed":
        return None if completed_at else "importing"
    if status not in ("running", "stopping"):
        return None
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


def append_phase(log: list[dict], phase: str | None, at: str) -> bool:
    """Append ``{phase, at}`` to *log* when *phase* differs from the last entry.

    Returns:
        True when an entry was appended.
    """
    if phase is None or (log and log[-1].get("phase") == phase):
        return False
    log.append({"phase": phase, "at": at})
    return True


def parse_phase_log(raw: str | None) -> list[dict]:
    """The stored phase log, or an empty list when it is missing or unreadable."""
    if not raw:
        return []
    try:
        log = json.loads(raw)
    except (TypeError, ValueError) as exc:
        logger.warning("Unreadable training phase log %r: %s", raw[:80], exc)
        return []
    return [entry for entry in log if isinstance(entry, dict)]


def _seconds_between(start: str, end: str) -> float | None:
    try:
        return (
            datetime.fromisoformat(end) - datetime.fromisoformat(start)
        ).total_seconds()
    except (TypeError, ValueError):
        return None


def phase_durations(log: list[dict], until: str | None) -> list[dict]:
    """Each logged phase with how long it lasted, the last one up to *until*.

    *until* None leaves the last phase's duration unknown.

    Returns:
        ``[{phase, seconds}]`` in log order; ``seconds`` is None when a stamp
        does not parse.
    """
    out = []
    for index, entry in enumerate(log):
        end = log[index + 1]["at"] if index + 1 < len(log) else until
        out.append(
            {"phase": entry["phase"], "seconds": _seconds_between(entry["at"], end)}
        )
    return out


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


def prompt_rows(prompts: list[str], trigger_word: str | None) -> list[dict]:
    """The view's rows: each prompt by its sample index, and whether it has the word.

    ai-toolkit numbers a step's samples in prompt order, so a row's ``index`` is
    the ``_<index>`` of its samples' filenames.
    """
    word = (trigger_word or "").strip()
    pattern = re.compile(rf"(?<!\w){re.escape(word)}(?!\w)", re.I) if word else None
    return [
        {
            "index": index,
            "prompt": prompt,
            "with_trigger": bool(pattern and pattern.search(prompt)),
        }
        for index, prompt in enumerate(prompts)
    ]


def checkpoint_columns(
    total_steps: int | None, sample_every: int | None, seen_steps: list[int]
) -> list[int]:
    """The steps the view has a column for: step 0, every preview step, the last.

    The steps the samples on disk name are included too, so a run whose config
    could not be read still shows what it drew.
    """
    steps = {0, *seen_steps}
    if total_steps and sample_every:
        steps.update(range(sample_every, total_steps + 1, sample_every))
    if total_steps:
        steps.add(total_steps)
    return sorted(step for step in steps if step >= 0)


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


def speed(step_times: list[tuple[int, float]], total_steps: int | None) -> dict:
    """Seconds per update and the time left, from the logged step times.

    Seconds per update is the median gap between consecutive steps over the
    latest :data:`_SPEED_WINDOW`, so a preview or a save between two steps does
    not count as a slow step. The time left is a range: its low end is that
    median for every step to go, its high end the run's own average so far,
    which does include the previews and saves still to come.

    Returns:
        ``{seconds_per_step, remaining_low_s, remaining_high_s, last_step_at}``;
        a value that cannot be told yet is None.
    """
    gaps = [
        later[1] - earlier[1]
        for earlier, later in zip(step_times, step_times[1:])
        if later[0] == earlier[0] + 1 and later[1] >= earlier[1]
    ][-_SPEED_WINDOW:]
    per_step = statistics.median(gaps) if gaps else None
    last_at = step_times[-1][1] if step_times else None
    low = high = None
    if per_step is not None and total_steps:
        done = step_times[-1][0]
        left = max(0, total_steps - done)
        span = step_times[-1][0] - step_times[0][0]
        average = (step_times[-1][1] - step_times[0][1]) / span if span else per_step
        low = left * per_step
        high = left * max(average, per_step)
    return {
        "seconds_per_step": per_step,
        "remaining_low_s": low,
        "remaining_high_s": high,
        "last_step_at": last_at,
    }


def cell_state(
    *,
    column: int,
    index: int,
    has_sample: bool,
    phase: str | None,
    step: int,
    drawing_column: int,
    previews: dict | None,
    previews_deferred: bool,
) -> str:
    """What one cell of the grid shows.

    *drawing_column* is the column a preview round now drawing belongs to: the
    last one at or before *step*.

    Returns:
        ``sample`` (its picture is on disk), ``drawing`` (being drawn now),
        ``queued`` (next in this round of previews), ``deferred`` (drawn after
        training), ``future`` (the run has not reached it) or ``missing`` (the
        run passed it and drew nothing).
    """
    if has_sample:
        return "sample"
    if phase == "previews" and previews is not None and column == drawing_column:
        drawing = previews["drawing"] - 1
        if index == drawing:
            return "drawing"
        if index > drawing:
            return "queued"
        return "missing"
    if previews_deferred and column > 0:
        return "deferred"
    if phase is not None and column >= step:
        return "future"
    return "missing"


__all__ = [
    "ESTIMATED",
    "MEASURED",
    "NOT_MEASURED",
    "PHASES",
    "SETUP_PHASES",
    "append_phase",
    "cell_state",
    "checkpoint_columns",
    "downloading",
    "parse_phase_log",
    "phase_durations",
    "phase_of",
    "preview_progress",
    "prompt_rows",
    "read_step_times",
    "read_trainer_info",
    "sample_settings",
    "speed",
]
