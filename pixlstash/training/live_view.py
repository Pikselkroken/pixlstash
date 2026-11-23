"""What the Training runs view draws for one run while it trains (#2118).

Pure functions over what a run already leaves behind: its ``training_run`` row,
its run folder, and what its training plugin reads of the trainer (its status
line, its step times and its rendered config's preview settings; for ai-toolkit
see :mod:`pixlstash.training_plugins.ai_toolkit.live`). The route in
``routes/training.py`` reads those and the machine's memory, and this module
turns them into the view's one timeline: the phase, the checkpoint columns, the
prompt rows, which sample fills which cell, the speed and the time left.

Every figure says where it came from (``measured``, ``estimated`` or ``none``),
and a figure that could not be read is ``None``, never 0: the view draws
"Unknown" for it.
"""

from __future__ import annotations

import json
import re
import statistics
from datetime import datetime
from typing import Callable

from pixlstash.pixl_logging import get_logger
from pixlstash.training import sample_prompts
from pixlstash.training.backend import TrainerBackend

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

# How many of the latest steps the speed is taken over.
_SPEED_WINDOW = 50


def phase_of(
    status: str,
    info: str | None,
    step: int,
    completed_at: str | None,
    running_phase: Callable[[str | None, int], str] | None = None,
) -> str | None:
    """The phase a run is in, from its status and the trainer's status line.

    A queued run waits for the GPU. A ``completed`` run with no
    ``completed_at`` is being imported: the worker writes ``completed_at``
    once the import is done. A running (or stopping) run's phase is its
    trainer's to read from *info* and *step*: *running_phase* is the run's
    training plugin's :meth:`~pixlstash.training.backend.TrainerBackend.running_phase`
    (None: the status-only default).

    Returns:
        One of :data:`PHASES`, or None for a run that has ended.
    """
    if status == "queued":
        return "waiting_gpu"
    if status == "completed":
        return None if completed_at else "importing"
    if status not in ("running", "stopping"):
        return None
    return (running_phase or TrainerBackend.running_phase)(info, step)


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


def preservation(resolution: dict | None) -> dict | None:
    """Whether a person's run kept its description generic, and the description.

    Read from the run's resolved values (``sample_prompts.preservation``). A run
    that never decided (queued before it existed, or not of a person) has no
    description there and answers None.
    """
    values = (resolution or {}).get("values") or {}
    described = values.get("train.diff_output_preservation_class") or {}
    description = (
        str(described.get("value") or "").strip() if isinstance(described, dict) else ""
    )
    if not description:
        return None
    kept = values.get("train.diff_output_preservation") or {}
    return {
        "on": bool(isinstance(kept, dict) and kept.get("value")),
        "description": description,
        "stranger": sample_prompts.stranger(description),
    }


def _words(phrase: str) -> re.Pattern:
    return re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)", re.I)


def prompt_rows(
    prompts: list[str],
    trigger_word: str | None,
    description: str | None = None,
    stranger: str | None = None,
) -> list[dict]:
    """The view's rows: each prompt by its sample index, and whether it has the word.

    ai-toolkit numbers a step's samples in prompt order, so a row's ``index`` is
    the ``_<index>`` of its samples' filenames. With the person's *description*
    and its *stranger*, a prompt without the word is a ``lookalike`` when it
    names the description and a ``stranger`` when it names the stranger; any
    other prompt (one the owner wrote) has no ``role``.
    """
    word = (trigger_word or "").strip()
    pattern = _words(word) if word else None
    described = _words(description) if description else None
    other = _words(stranger) if stranger else None
    rows = []
    for index, prompt in enumerate(prompts):
        with_trigger = bool(pattern and pattern.search(prompt))
        role = None
        if described is not None and not with_trigger:
            if described.search(prompt):
                role = "lookalike"
            elif other is not None and other.search(prompt):
                role = "stranger"
        rows.append(
            {
                "index": index,
                "prompt": prompt,
                "with_trigger": with_trigger,
                "role": role,
            }
        )
    return rows


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
    "parse_phase_log",
    "phase_durations",
    "phase_of",
    "prompt_rows",
    "speed",
]
