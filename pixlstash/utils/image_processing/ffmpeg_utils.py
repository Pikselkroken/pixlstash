"""ffmpeg, run as a separate program: playable copies and audio peaks (#2807).

**ffmpeg only ever runs as a subprocess.** Never ``import`` or link it: no
PyAV, no ``ctypes`` into ``libav*``, in the core or in any plugin. PixlStash's
licence is its owner's to choose, ffmpeg's is not, and a program driven over
pipes and the command line stays a separate work. PyAV is ruled out on exactly
this ground, not only for its size: its wheels link GPL ``libx264``/``libx265``
into the process.

The binary comes from ``imageio-ffmpeg``, a declared dependency, through
:func:`ffmpeg_executable`, and nothing here adds a lookup of its own.
``imageio_ffmpeg.get_ffmpeg_exe()`` tries, in order:

1. the ``IMAGEIO_FFMPEG_EXE`` environment variable (a user's own ffmpeg);
2. the binary bundled in the wheel;
3. conda's ffmpeg;
4. ``ffmpeg`` on ``PATH``.

The wheel ships a binary for every platform PixlStash ships on, so the bundled
one wins unless the user sets the variable. That build is GPL (it carries
``libx264``); ``docs/licensing.md`` holds its notice.
"""

import functools
import os
import re
import subprocess
import tempfile
from typing import Callable, Optional

import imageio_ffmpeg
import numpy as np

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

#: Prefix of an unfinished copy; anything left with it is swept at start-up.
COPY_TMP_PREFIX = ".copy-tmp-"
COPY_TIMEOUT_SECONDS = 30 * 60
#: The waveform: this many peaks, from mono audio decoded at this rate.
WAVEFORM_BUCKETS = 512
_PEAK_SAMPLE_RATE = 8000
_AUDIO_STREAM = re.compile(r"Stream #\d+:\d+.*: Audio:")
# No console window flashes up on Windows when the desktop app runs ffmpeg.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# The one copy being made (the copy executor has one worker), so shutdown and a
# library switch can stop it.
_running: Optional[subprocess.Popen] = None


@functools.cache
def ffmpeg_executable() -> Optional[str]:
    """The ffmpeg to run, or ``None`` (warned once) when there is none."""
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        logger.warning(
            "No ffmpeg found (%s); playable video copies and waveforms are "
            "unavailable on this install.",
            exc,
        )
        return None


def stop_running_copy() -> None:
    """Terminate the copy being made, if any. Its job then fails and cleans up."""
    proc = _running
    if proc is not None and proc.poll() is None:
        logger.info("Stopping the playable copy in progress (pid %s)", proc.pid)
        proc.terminate()


def make_playable_copy(
    source: str, dest: str, on_start: Optional[Callable[[], None]] = None
) -> None:
    """Write an H.264/AAC MP4 of *source* to *dest*, or raise.

    Written to a temp file beside *dest* and renamed onto it, so *dest* is
    either absent or whole. *on_start* is called once ffmpeg is running.

    Raises:
        RuntimeError: ffmpeg is missing, failed, or ran past the timeout. The
            temp file is gone and the reason is logged.
    """
    global _running
    exe = ffmpeg_executable()
    if not exe:
        raise RuntimeError("ffmpeg is not available")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=os.path.dirname(dest), prefix=COPY_TMP_PREFIX, suffix=".mp4"
    )
    os.close(fd)
    cmd = [
        exe,
        "-nostdin",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        source,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        # At most 1080 lines, both sides even (yuv420p needs that).
        "-vf",
        "scale=-2:'trunc(min(1080,ih)/2)*2'",
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
        tmp,
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=_NO_WINDOW,
        )
        _running = proc
        if on_start:
            on_start()
        try:
            _, stderr = proc.communicate(timeout=COPY_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, stderr = proc.communicate()
            stderr += b"\n(timed out)"
        if proc.returncode != 0:
            tail = stderr[-2048:].decode("utf-8", "replace")
            logger.warning(
                "ffmpeg could not make a playable copy of %s into %s (exit %s): %s",
                source,
                dest,
                proc.returncode,
                tail,
            )
            raise RuntimeError(f"ffmpeg exited with {proc.returncode}")
        os.replace(tmp, dest)
    finally:
        _running = None
        if os.path.exists(tmp):
            os.remove(tmp)


def _run(args: list, timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        [ffmpeg_executable(), "-nostdin", "-hide_banner", *args],
        capture_output=True,
        timeout=timeout,
        creationflags=_NO_WINDOW,
    )


def audio_peaks(
    source: str, buckets: int = WAVEFORM_BUCKETS
) -> Optional[tuple[float, list[float]]]:
    """``(audio seconds, peaks in [0, 1])`` of *source*'s first audio track.

    ``None`` when the file has no audio track. imageio-ffmpeg ships no
    ffprobe, so the probe reads the stream list ffmpeg prints for a bare
    ``-i`` (which exits non-zero by design).

    Raises:
        RuntimeError: the audio track exists but could not be decoded.
    """
    probe = _run(["-i", source], timeout=60)
    if not _AUDIO_STREAM.search(probe.stderr.decode("utf-8", "replace")):
        return None
    decoded = _run(
        [
            "-loglevel",
            "error",
            "-i",
            source,
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            str(_PEAK_SAMPLE_RATE),
            "-f",
            "s16le",
            "-",
        ],
        timeout=600,
    )
    if decoded.returncode != 0:
        tail = decoded.stderr[-2048:].decode("utf-8", "replace")
        logger.warning("ffmpeg could not decode the audio of %s: %s", source, tail)
        raise RuntimeError(f"ffmpeg exited with {decoded.returncode}")
    samples = np.abs(np.frombuffer(decoded.stdout, np.int16).astype(np.float32))
    if not samples.size:
        return None
    peaks = np.array(
        [part.max() if part.size else 0.0 for part in np.array_split(samples, buckets)]
    )
    top = peaks.max()
    if top > 0:
        peaks = peaks / top
    return samples.size / _PEAK_SAMPLE_RATE, [round(float(p), 4) for p in peaks]
