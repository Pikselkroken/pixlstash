"""ffmpeg, run as a separate process: playable copies and audio waveforms (#2807).

**The rule.** ffmpeg only ever runs as a separate process. Never import or link
it: no PyAV, no ``ctypes`` into ``libav*``, in the core or in any plugin. A
program run over pipes and the command line stays a separate work; linking it
in-process would make PixlStash a derivative of it. Keep it that way at review.

**Which binary.** :func:`ffmpeg_executable` asks
``imageio_ffmpeg.get_ffmpeg_exe()`` and PixlStash adds no lookup of its own.
That call checks, in order: ``IMAGEIO_FFMPEG_EXE``, the binary the wheel
bundles, conda's ffmpeg, and ``ffmpeg`` on PATH. The bundled one wins wherever
the wheel ships one; set ``IMAGEIO_FFMPEG_EXE`` to use your own.
"""

import functools
import os
import re
import secrets
import subprocess
from typing import Callable, Optional

import imageio_ffmpeg
import numpy as np

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

_TRANSCODE_TIMEOUT_S = 30 * 60
_PROBE_TIMEOUT_S = 60
_DECODE_TIMEOUT_S = 10 * 60
_STDERR_TAIL_BYTES = 2048
#: Audio is decoded at this rate, mono, so one sample is 1/8000 s.
PEAK_SAMPLE_RATE = 8000
_AUDIO_STREAM = re.compile(r"Stream #\d+:\d+.*: Audio:")
# Every ffmpeg this module has started and not yet reaped, so Server.close() can
# kill them. A transcode left running would hold interpreter exit for up to the
# full timeout, since the worker thread is not a daemon.
_active_processes: set[subprocess.Popen] = set()


@functools.cache
def ffmpeg_executable() -> Optional[str]:
    """Return the ffmpeg binary to run, or ``None`` when there is none.

    Cached, so the one warning for a missing binary is logged once per process.
    """
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        logger.warning(
            "No ffmpeg binary is available (%s): playable copies and audio "
            "waveforms are off until one is installed or IMAGEIO_FFMPEG_EXE "
            "names one.",
            exc,
        )
        return None


def terminate_running_processes() -> None:
    """Kill every ffmpeg still running. Called from ``Server.close()``.

    The job that owns each one then fails, logs, and removes its temp file.
    """
    for proc in list(_active_processes):
        if proc.poll() is None:
            logger.warning(
                "Stopping ffmpeg (pid %s) at shutdown; its output is discarded.",
                proc.pid,
            )
            proc.kill()


def _stderr_tail(stderr: bytes) -> str:
    return (stderr or b"")[-_STDERR_TAIL_BYTES:].decode("utf-8", errors="replace")


def make_playable_copy(
    source: str,
    dest: str,
    on_start: Optional[Callable[[subprocess.Popen], None]] = None,
) -> None:
    """Transcode *source* to an H.264/AAC MP4 at *dest*, replacing it atomically.

    The output goes to a temp file beside *dest* and is renamed onto it, so a
    reader sees the old copy or the whole new one, never a partial file.

    Args:
        source: The original clip.
        dest: Where the copy belongs.
        on_start: Called with the running process once it has started, so the
            caller can read when the transcode began.

    Raises:
        RuntimeError: No ffmpeg binary, a non-zero exit, or the 30-minute
            timeout. The temp file is removed and the failure is logged with the
            tail of ffmpeg's stderr.
    """
    ffmpeg = ffmpeg_executable()
    if ffmpeg is None:
        raise RuntimeError("no ffmpeg binary is available")
    temp = os.path.join(os.path.dirname(dest), f".proxy-tmp-{secrets.token_hex(8)}.mp4")
    args = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-i",
        source,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        "scale=-2:'min(1080,ih)'",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-movflags",
        "+faststart",
        temp,
    ]
    stderr = b""
    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        _active_processes.add(proc)
        try:
            if on_start is not None:
                on_start(proc)
            # communicate() drains stderr while it waits, so a chatty ffmpeg
            # cannot fill the pipe and deadlock the wait.
            _, stderr = proc.communicate(timeout=_TRANSCODE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, stderr = proc.communicate()
            raise RuntimeError(
                f"ffmpeg ran past {_TRANSCODE_TIMEOUT_S} s and was killed"
            )
        finally:
            _active_processes.discard(proc)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg exited with status {proc.returncode}")
        os.replace(temp, dest)
    except Exception as exc:
        logger.error(
            "Playable copy failed: source=%s dest=%s error=%s stderr_tail=%s",
            source,
            dest,
            exc,
            _stderr_tail(stderr),
        )
        if os.path.exists(temp):
            try:
                os.remove(temp)
            except OSError as remove_exc:
                logger.warning(
                    "Could not remove the partial copy %s: %s", temp, remove_exc
                )
        raise


def audio_peaks(source: str, buckets: int = 512) -> Optional[tuple[float, list[float]]]:
    """Return ``(duration, peaks)`` for the first audio track of *source*.

    *duration* is the audio's length in seconds. *peaks* is *buckets* evenly
    spaced slices of it, each the largest absolute sample in its slice divided
    by the loudest slice, so every value is in 0..1 and the tallest reaches 1.
    An all-silent track gives all zeros.

    Returns ``None`` when the clip has no audio track.

    Raises:
        RuntimeError: No ffmpeg binary, or ffmpeg could not open or decode the
            file. A failure is never mistaken for silence.
    """
    ffmpeg = ffmpeg_executable()
    if ffmpeg is None:
        raise RuntimeError("no ffmpeg binary is available")
    # ffmpeg with no output exits non-zero by design; the stream list it prints
    # first is what is wanted. imageio-ffmpeg ships no ffprobe.
    probe = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostdin", "-i", source],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=_PROBE_TIMEOUT_S,
    )
    listing = probe.stderr.decode("utf-8", errors="replace")
    if "Input #" not in listing:
        raise RuntimeError(
            f"ffmpeg could not open {source}: {_stderr_tail(probe.stderr)}"
        )
    if not _AUDIO_STREAM.search(listing):
        return None
    decoded = subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-loglevel",
            "error",
            "-i",
            source,
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            str(PEAK_SAMPLE_RATE),
            "-f",
            "s16le",
            "-",
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=_DECODE_TIMEOUT_S,
    )
    if decoded.returncode != 0:
        logger.warning(
            "Could not decode the audio of %s (status %s): %s",
            source,
            decoded.returncode,
            _stderr_tail(decoded.stderr),
        )
        raise RuntimeError(f"ffmpeg could not decode the audio of {source}")
    # ponytail: the whole track is decoded into memory (about 58 MB per hour of
    # sound at 8 kHz mono); stream it in chunks if hour-long clips become common.
    samples = np.frombuffer(decoded.stdout, dtype="<i2")
    magnitude = np.abs(samples.astype(np.int32))
    loudest = [
        float(part.max()) if part.size else 0.0
        for part in np.array_split(magnitude, buckets)
    ]
    top = max(loudest)
    peaks = [value / top for value in loudest] if top > 0 else [0.0] * buckets
    return samples.size / PEAK_SAMPLE_RATE, peaks


def remove_stale_temp_files(folder: str) -> int:
    """Delete ``.proxy-tmp-*`` files a killed transcode left in *folder*.

    Returns the count removed. Only safe at start-up, before any job runs.
    """
    if not os.path.isdir(folder):
        return 0
    removed = 0
    for name in os.listdir(folder):
        if not (name.startswith(".proxy-tmp-") and name.endswith(".mp4")):
            continue
        try:
            os.remove(os.path.join(folder, name))
            removed += 1
        except OSError as exc:
            logger.warning("Could not remove stale copy temp %s: %s", name, exc)
    return removed
