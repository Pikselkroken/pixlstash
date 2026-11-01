"""What this machine measured a training setup to need (#2120).

A recipe's hardware profiles start as research seeds. Once a setup has run
here, its measured peaks and speed replace them for admission and the time
estimate. A setup is named by its **fingerprint**: everything that moves the
memory a run peaks at or the time an update takes, and nothing that does not
(the trigger word, the learning rate, the step count).

- :func:`fingerprint` hashes the model and adapter revisions (the recipe's
  locked keys), the ai-toolkit commit, the card, the quantizer, the offload
  fractions, the largest bucket, the rank, the optimizer, whether the text
  encoder trains, the effective batch, caption caching
  and the preview policy (its size and sampler steps; whether previews are
  deferred is a decision made *from* the measurement, so it is not part of it).
- :func:`note_peak` keeps the highest GPU and RAM figure seen per phase while a
  probe or a run is watched; :func:`needs` turns the phase peaks into what a
  run asks the arbiter for, previews apart.
- :func:`record` and :func:`read` keep one row per fingerprint in the hub's
  ``training_profile_measurement`` table, which is the machine's, not a
  library's.
- :func:`trainer_usage` reads what a trainer's process tree holds now, and
  the most it held since the last read.

Every figure is in MB. A figure that could not be read is None, never 0.
"""

from __future__ import annotations

import hashlib
import json
import math
import os

import psutil

from pixlstash.hub.db import HubDatabase, now_iso
from pixlstash.pixl_logging import get_logger
from pixlstash.resources.ledger import ledger

logger = get_logger(__name__)

_MB = 1024 * 1024

#: The resolved values that move a run's peak memory or its speed.
FINGERPRINT_KEYS = (
    "model.quantize",
    "model.qtype",
    "model.quantize_te",
    "model.qtype_te",
    "model.low_vram",
    "model.layer_offloading",
    "model.layer_offloading_transformer_percent",
    "model.layer_offloading_text_encoder_percent",
    "network.linear",
    "network.conv",
    "train.optimizer",
    "train.train_text_encoder",
    "train.batch_size",
    "train.gradient_accumulation",
    "train.cache_text_embeddings",
    "sample.width",
    "sample.height",
    "sample.sample_steps",
)

#: Keys only clip recipes resolve. Named only when present, so a recipe
#: without them keeps the fingerprints it was measured under.
CLIP_FINGERPRINT_KEYS = ("datasets[*].do_audio", "sample.num_frames")

#: Switches that slow every step, named only while on, so a run with them off
#: keeps the fingerprint it was measured under.
ON_FINGERPRINT_KEYS = ("train.diff_output_preservation",)

#: The phase whose peak is the preview need; every other phase is training's.
PREVIEW_PHASE = "previews"

#: A measured peak is asked for with this much room on top, like a learned
#: ComfyUI need (§7 "Learned need").
NEED_MARGIN = 1.1

#: How a measurement's GPU peaks were read. Polling alone missed a preview's VAE
#: decode, which spikes and is freed between two polls; the trainer's allocator
#: peaks (:func:`allocator_overshoot_mb`) do not. A setup measured the old way
#: is a different fingerprint, so it is measured again.
MEASURED_BY = "allocator-peaks"

SOURCES = ("probe", "run")


def fingerprint(preset: dict, values: dict, *, commit: str | None, device: str | None):
    """The setup a measurement holds for, as a short hex digest.

    Args:
        preset: The recipe; its ``locked`` keys are the model and adapter
            revisions.
        values: The run's resolved ``{key: value}``, memory profile included.
        commit: The ai-toolkit checkout's commit, or None when unknown.
        device: The card's name (a different card is a different speed).

    Returns:
        32 hex characters.
    """
    sizes = values.get("datasets[*].resolution") or []
    batch = int(values.get("train.batch_size") or 1) * int(
        values.get("train.gradient_accumulation") or 1
    )
    named = {
        "locked": preset["locked"],
        "commit": commit,
        "device": device,
        "max_bucket": max(sizes, default=0),
        "effective_batch": batch,
        "measured_by": MEASURED_BY,
        **{key: values.get(key) for key in FINGERPRINT_KEYS},
        **{key: values[key] for key in CLIP_FINGERPRINT_KEYS if key in values},
        **{key: True for key in ON_FINGERPRINT_KEYS if values.get(key)},
    }
    blob = json.dumps(named, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def note_peak(
    peaks: dict, phase: str | None, vram_mb: int | None, ram_mb: int | None
) -> None:
    """Raise *phase*'s ``{vram_mb, ram_mb}`` in *peaks* to the figures given.

    A None figure leaves that side as it was.
    """
    if phase is None or (vram_mb is None and ram_mb is None):
        return
    entry = peaks.setdefault(phase, {"vram_mb": None, "ram_mb": None})
    for key, value in (("vram_mb", vram_mb), ("ram_mb", ram_mb)):
        if value is not None and (entry[key] is None or value > entry[key]):
            entry[key] = int(value)


def _peak(peaks: dict, key: str, phases) -> int | None:
    figures = [
        peaks[p][key] for p in phases if p in peaks and peaks[p].get(key) is not None
    ]
    return max(figures) if figures else None


def with_margin(mb: int | None) -> int | None:
    """*mb* times :data:`NEED_MARGIN`, rounded up to a whole MB."""
    # Rounded first: 14000 * 1.1 is 15400.000000000002 in floating point.
    return None if mb is None else int(math.ceil(round(mb * NEED_MARGIN, 6)))


def needs(measurement: dict) -> dict:
    """What a run of a measured setup asks for, previews apart.

    Returns:
        ``{vram_mb, ram_mb, preview_vram_mb, preview_ram_mb}``, each the phase
        peak times :data:`NEED_MARGIN`, None where nothing was measured.
        Training's figures are the highest of every phase but previews.
    """
    peaks = measurement.get("phase_peaks") or {}
    training = [p for p in peaks if p != PREVIEW_PHASE]
    return {
        "vram_mb": with_margin(_peak(peaks, "vram_mb", training)),
        "ram_mb": with_margin(_peak(peaks, "ram_mb", training)),
        "preview_vram_mb": with_margin(_peak(peaks, "vram_mb", [PREVIEW_PHASE])),
        "preview_ram_mb": with_margin(_peak(peaks, "ram_mb", [PREVIEW_PHASE])),
    }


def estimate_seconds(measurement: dict, *, steps: int, previews: int) -> float | None:
    """How long a measured setup takes for *steps* updates and *previews* images.

    Setup (loading and caching) once, each update at the warm speed, each
    preview image at its measured time. None without a warm speed.
    """
    per_update = measurement.get("seconds_per_update")
    if per_update is None:
        return None
    return (
        float(measurement.get("setup_seconds") or 0)
        + steps * float(per_update)
        + previews * float(measurement.get("seconds_per_preview") or 0)
    )


def record(
    hub: HubDatabase,
    key: str,
    *,
    preset: str,
    profile: str,
    device: str | None,
    phase_peaks: dict,
    seconds_per_update: float | None,
    setup_seconds: float | None,
    seconds_per_preview: float | None,
    source: str,
) -> None:
    """Store what a probe or a run measured for fingerprint *key*.

    A setup measured before keeps the higher of each phase peak (a poll can
    miss a short peak, never invent one) and takes the newer timings where this
    measurement has them.
    """
    if source not in SOURCES:
        raise ValueError(f"source must be one of {', '.join(SOURCES)}.")
    with hub.transaction() as conn:
        row = conn.execute(
            "SELECT phase_peaks, seconds_per_update, setup_seconds, "
            "seconds_per_preview FROM training_profile_measurement "
            "WHERE fingerprint = ?",
            (key,),
        ).fetchone()
        peaks: dict = {}
        if row is not None:
            peaks = _parse_peaks(row[0], key)
            seconds_per_update = (
                seconds_per_update if seconds_per_update is not None else row[1]
            )
            setup_seconds = setup_seconds if setup_seconds is not None else row[2]
            seconds_per_preview = (
                seconds_per_preview if seconds_per_preview is not None else row[3]
            )
        for phase, entry in phase_peaks.items():
            note_peak(peaks, phase, entry.get("vram_mb"), entry.get("ram_mb"))
        conn.execute(
            "INSERT INTO training_profile_measurement (fingerprint, preset, "
            "profile, device_name, phase_peaks, seconds_per_update, setup_seconds, "
            "seconds_per_preview, source, measured_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(fingerprint) DO UPDATE SET preset = excluded.preset, "
            "profile = excluded.profile, device_name = excluded.device_name, "
            "phase_peaks = excluded.phase_peaks, "
            "seconds_per_update = excluded.seconds_per_update, "
            "setup_seconds = excluded.setup_seconds, "
            "seconds_per_preview = excluded.seconds_per_preview, "
            "source = excluded.source, measured_at = excluded.measured_at",
            (
                key,
                preset,
                profile,
                device,
                json.dumps(peaks, sort_keys=True),
                seconds_per_update,
                setup_seconds,
                seconds_per_preview,
                source,
                now_iso(),
            ),
        )
    logger.info(
        "Recorded the %s measurement of %s (%s): peaks %s, %s s per update.",
        source,
        profile,
        key,
        peaks,
        seconds_per_update,
    )


def read(hub: HubDatabase | None, key: str) -> dict | None:
    """The measurement of fingerprint *key*, peaks parsed, or None."""
    if hub is None:
        return None
    row = hub.fetchone(
        "SELECT * FROM training_profile_measurement WHERE fingerprint = ?", (key,)
    )
    if row is None:
        return None
    out = dict(row)
    out["phase_peaks"] = _parse_peaks(out.get("phase_peaks"), key)
    return out


def make_lookup(
    hub: HubDatabase | None, preset: dict, *, commit: str | None, device: str | None
):
    """The ``lookup`` :func:`pixlstash.training.presets.resolve` takes.

    It answers ``(fingerprint, measurement or None)`` for a profile's full
    values on this checkout and card.
    """

    def lookup(values: dict) -> tuple[str, dict | None]:
        key = fingerprint(preset, values, commit=commit, device=device)
        return key, read(hub, key)

    return lookup


def _parse_peaks(raw: str | None, key: str) -> dict:
    try:
        peaks = json.loads(raw or "{}")
    except (TypeError, ValueError) as exc:
        logger.warning("Unreadable phase peaks of measurement %s: %s", key, exc)
        return {}
    return {phase: entry for phase, entry in peaks.items() if isinstance(entry, dict)}


def process_tree(pid: int | None) -> list:
    """The process *pid* and every child of it, or [] when it is not running."""
    if not pid:
        return []
    try:
        root = psutil.Process(pid)
        return [root, *root.children(recursive=True)]
    except psutil.Error as exc:
        logger.debug("Trainer process %s cannot be read: %s", pid, exc)
        return []


def rss_mb(processes: list) -> int | None:
    """The resident memory of *processes* together; None if none could be read."""
    total = 0
    any_read = False
    for process in processes:
        try:
            total += process.memory_info().rss
            any_read = True
        except psutil.Error as exc:
            logger.debug("No memory reading for pid %s: %s", process.pid, exc)
    return total // _MB if any_read else None


def gpu_mb(pids: set[int], device: str | None) -> int | None:
    """What *pids* hold on *device* by NVML's per-process figure.

    None where NVML cannot attribute memory (Windows WDDM, Docker, no NVML) or
    no pid is on the card yet.
    """
    if not pids:
        return None
    on_card = ledger().processes(device) if device else {}
    held = [on_card[pid] for pid in pids if pid in on_card]
    return sum(held) if held else None


#: Polls a trainer may hold GPU memory without peaks before that is logged.
_UNHOOKED_POLLS = 3
_unhooked: dict[str, int] = {}


def allocator_overshoot_mb(folder: str, pids: set[int], offsets: dict) -> int:
    """How far *pids*' CUDA allocators rose above what they reserve now.

    Reads the lines ``trainer_hook/sitecustomize.py`` appended to
    ``<folder>/<pid>.log`` since the last call (*offsets* keeps where each file
    was read to): ``<peak since the line before> <reserved now>``, in MB. A
    poll every second or more misses a spike a preview's VAE decode holds for
    less; the allocator's own peak does not.

    Returns:
        The sum over *pids* of the highest new peak less the latest reserved
        figure, never below 0; 0 where a process wrote nothing new.
    """
    total = 0
    for pid in pids:
        path = os.path.join(folder, f"{pid}.log")
        try:
            with open(path, encoding="ascii") as handle:
                handle.seek(offsets.get(pid, 0))
                text = handle.read()
        except FileNotFoundError:
            continue  # this process never had a CUDA context
        except OSError as exc:
            logger.warning("Cannot read the trainer's memory peaks %s: %s", path, exc)
            continue
        # A line still being written waits for the next read.
        complete = text[: text.rfind("\n") + 1]
        offsets[pid] = offsets.get(pid, 0) + len(complete)
        rows = [line.split() for line in complete.splitlines()]
        rows = [row for row in rows if len(row) == 2 and all(f.isdigit() for f in row)]
        if rows:
            total += max(0, max(int(row[0]) for row in rows) - int(rows[-1][1]))
    return total


def peaks_read_to_end(folder: str) -> dict:
    """Offsets past every whole line already in *folder*'s peak files.

    A run watched from its middle (re-attached after a restart) starts here, so
    the peaks it held before are not credited to the phase it is in now.
    """
    offsets: dict = {}
    try:
        entries = list(os.scandir(folder))
    except FileNotFoundError:
        return offsets
    except OSError as exc:
        logger.warning("Cannot list the trainer's memory peaks %s: %s", folder, exc)
        return offsets
    for entry in entries:
        stem, ext = os.path.splitext(entry.name)
        if ext != ".log" or not stem.isdigit():
            continue
        try:
            with open(entry.path, encoding="ascii") as handle:
                text = handle.read()
        except OSError as exc:
            logger.warning(
                "Cannot read the trainer's memory peaks %s: %s", entry.path, exc
            )
            continue
        offsets[int(stem)] = text.rfind("\n") + 1
    return offsets


def trainer_usage(
    pid: int | None,
    device: str | None,
    peaks_folder: str | None = None,
    offsets: dict | None = None,
) -> dict:
    """What a trainer's process tree holds now, and the most it held since.

    Args:
        pid: The trainer's pid.
        device: The card it trains on.
        peaks_folder: Where its allocator peaks are written
            (:func:`allocator_overshoot_mb`); None reads none.
        offsets: Where each peaks file was read to, kept between calls;
            required with *peaks_folder*, or every poll re-reads the history.

    Returns:
        ``{pids, vram_mb, peak_vram_mb, ram_mb}``; ``pids`` is the set read, so
        a caller can look for the same processes after the trainer exits.
        ``peak_vram_mb`` is ``vram_mb`` plus how far the allocators rose above
        it since the last call.

    Raises:
        ValueError: *peaks_folder* without *offsets*.
    """
    tree = process_tree(pid)
    pids = {process.pid for process in tree}
    vram = gpu_mb(pids, device)
    peak = vram
    if peaks_folder:
        if offsets is None:
            raise ValueError("peaks_folder needs offsets to read each line once")
        if not pids:
            _unhooked.pop(peaks_folder, None)  # the trainer is gone
        # Read even when NVML has no figure, so the lines are not all credited
        # to the first poll that has one.
        overshoot = allocator_overshoot_mb(peaks_folder, pids, offsets)
        if vram is not None:
            peak = vram + overshoot
            _check_hooked(peaks_folder, pids)
    return {"pids": pids, "vram_mb": vram, "peak_vram_mb": peak, "ram_mb": rss_mb(tree)}


def _check_hooked(folder: str, pids: set[int]) -> None:
    """Warn once when a trainer holds GPU memory but writes no peaks.

    Its phase peaks are then polls alone, which miss a preview's VAE decode.
    A few polls are allowed first: the hook writes within a quarter second of
    the CUDA context, which a poll can come before.
    """
    if any(os.path.exists(os.path.join(folder, f"{pid}.log")) for pid in pids):
        _unhooked.pop(folder, None)
        return
    misses = _unhooked.get(folder, 0) + 1
    _unhooked[folder] = misses
    if misses == _UNHOOKED_POLLS:
        logger.warning(
            "The trainer holds GPU memory but writes no allocator peaks to %s; "
            "its measurement is polls alone and can miss a preview's VAE decode "
            "(see the trainer's output for why the hook stopped).",
            folder,
        )


__all__ = [
    "FINGERPRINT_KEYS",
    "MEASURED_BY",
    "NEED_MARGIN",
    "PREVIEW_PHASE",
    "allocator_overshoot_mb",
    "estimate_seconds",
    "fingerprint",
    "gpu_mb",
    "make_lookup",
    "needs",
    "note_peak",
    "peaks_read_to_end",
    "process_tree",
    "read",
    "record",
    "rss_mb",
    "trainer_usage",
    "with_margin",
]
