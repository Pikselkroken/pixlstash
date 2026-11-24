"""ffmpeg, run as a separate program: playable copies and audio peaks (#2807).

**ffmpeg only ever runs as a subprocess.** Never ``import`` or link it: no
PyAV, no ``ctypes`` into ``libav*``, in the core or in any plugin. PixlStash's
licence is its owner's to choose, ffmpeg's is not, and a program driven over
pipes and the command line stays a separate work. PyAV is ruled out on exactly
this ground: its wheels link GPL ``libx264``/``libx265`` into the process.

**PixlStash ships only an LGPL ffmpeg build, with openh264 (BSD) for H.264,
never a GPL build.** The desktop app and the Docker image put it where
:func:`ffmpeg_executable` looks, in this order:

1. the ``PIXLSTASH_FFMPEG`` environment variable, an absolute path (the desktop
   app sets it to its bundled binary); a path that does not exist is warned
   about once and skipped;
2. ``ffmpeg`` on ``PATH`` (``/usr/local/bin/ffmpeg`` in Docker, or the user's
   own).

The H.264 encoder is the first the binary lists of ``libopenh264``,
``h264_videotoolbox`` (macOS) and ``libx264``. Running a user's own GPL ffmpeg
with x264 is fine; PixlStash never ships one. With no H.264 encoder there are
no copies, but the waveform still works: it encodes nothing.
"""

import functools
import os
import re
import shutil
import subprocess
import tempfile
from typing import Callable, Optional

import numpy as np

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.image_processing.video_utils import VIDEO_EXTENSIONS
from pixlstash.utils.system_utils import child_process_env

logger = get_logger(__name__)

#: An absolute path to the ffmpeg to run, ahead of ``PATH``.
FFMPEG_ENV = "PIXLSTASH_FFMPEG"
#: H.264 encoders, most preferred first, with their quality arguments.
# ponytail: openh264 and VideoToolbox take a bitrate, not a CRF; 6 Mb/s is
# plenty for at most 1080 lines and too much for small clips. Scale it with the
# frame size if copies of short low-res clips come out large.
H264_ENCODERS = {
    "libopenh264": ["-b:v", "6M"],
    "h264_videotoolbox": ["-b:v", "6M"],
    "libx264": ["-preset", "veryfast", "-crf", "23"],
}
#: Prefix of an unfinished copy; anything left with it is swept at start-up.
COPY_TMP_PREFIX = ".copy-tmp-"
COPY_TIMEOUT_SECONDS = 30 * 60
#: The waveform: this many peaks, from mono audio decoded at this rate.
WAVEFORM_BUCKETS = 512
_PEAK_SAMPLE_RATE = 8000
# ponytail: the waveform decodes at most this much audio (about 29 MB of
# samples); a longer clip's lane covers its start. Bucket in two streamed
# passes if hour-long recordings matter.
_PEAK_MAX_SECONDS = 30 * 60
_AUDIO_STREAM = re.compile(r"Stream #\d+:\d+.*: Audio:")
# The demuxers that read each video extension PixlStash accepts
# (``VIDEO_EXTENSIONS``). ffmpeg may open only these, and only local files, so
# a crafted file cannot make it fetch a URL or follow a playlist (HLS, concat)
# to other files, nor reach one of its many rarely used demuxers. A new
# accepted extension without an entry here fails at import, on purpose.
_DEMUXERS = {
    ".mp4": "mov,mp4,m4a,3gp,3g2,mj2",
    ".mov": "mov,mp4,m4a,3gp,3g2,mj2",
    ".mkv": "matroska,webm",
    ".webm": "matroska,webm",
    ".avi": "avi",
}
_FORMAT_WHITELIST = ",".join(
    sorted({name for ext in VIDEO_EXTENSIONS for name in _DEMUXERS[ext].split(",")})
)
# No console window flashes up on Windows when the desktop app runs ffmpeg.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# The one copy being made (the copy executor has one worker), so shutdown and a
# library switch can stop it.
_running: Optional[subprocess.Popen] = None


@functools.cache
def ffmpeg_executable() -> Optional[str]:
    """The ffmpeg to run, or ``None`` (warned once) when there is none."""
    configured = os.environ.get(FFMPEG_ENV)
    if configured:
        if os.path.isfile(configured):
            return configured
        logger.warning(
            "%s is %s, which does not exist; looking for ffmpeg on PATH instead.",
            FFMPEG_ENV,
            configured,
        )
    found = shutil.which("ffmpeg")
    if found and not _on_path(os.path.dirname(os.path.abspath(found))):
        # Windows searches the working directory before PATH, so a stray
        # ffmpeg.exe there (a Downloads folder) would run in place of the real one.
        logger.warning(
            "Ignoring %s: it was found in the working directory, not on PATH.",
            found,
        )
        found = None
    if not found:
        logger.warning(
            "No ffmpeg found (set %s or put ffmpeg on PATH); playable video "
            "copies and waveforms are unavailable on this install.",
            FFMPEG_ENV,
        )
    return found


def _on_path(folder: str) -> bool:
    """Whether ``folder`` is one of the PATH entries (not merely the cwd)."""
    entries = os.environ.get("PATH", "").split(os.pathsep)
    want = os.path.normcase(folder)
    return any(e and os.path.normcase(os.path.abspath(e)) == want for e in entries)


def pick_h264_encoder(listing: str) -> Optional[str]:
    """The preferred H.264 encoder in ``ffmpeg -encoders`` output, or ``None``."""
    names = {
        parts[1] for parts in map(str.split, listing.splitlines()) if len(parts) > 1
    }
    return next((name for name in H264_ENCODERS if name in names), None)


@functools.cache
def h264_encoder() -> Optional[str]:
    """The H.264 encoder this install's ffmpeg has, or ``None`` (warned once)."""
    exe = ffmpeg_executable()
    if not exe:
        return None
    try:
        listing = subprocess.run(
            [exe, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=_NO_WINDOW,
            env=child_process_env(),
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Could not list the encoders of %s: %s", exe, exc)
        return None
    encoder = pick_h264_encoder(listing)
    if encoder is None:
        logger.warning(
            "%s has none of the H.264 encoders %s; playable video copies are "
            "unavailable (waveforms still work).",
            exe,
            ", ".join(H264_ENCODERS),
        )
    return encoder


def _input(source: str) -> list[str]:
    """``-i source`` behind the protocol and demuxer whitelists."""
    return [
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        _FORMAT_WHITELIST,
        "-i",
        source,
    ]


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
    encoder = h264_encoder()
    if not exe or not encoder:
        raise RuntimeError("no ffmpeg with an H.264 encoder")
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
        *_input(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        # At most 1080 lines, both sides even (yuv420p needs that).
        "-vf",
        "scale=-2:'trunc(min(1080,ih)/2)*2'",
        "-c:v",
        encoder,
        *H264_ENCODERS[encoder],
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
            env=child_process_env(),
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
        env=child_process_env(),
    )


def audio_peaks(
    source: str, buckets: int = WAVEFORM_BUCKETS
) -> Optional[tuple[float, list[float]]]:
    """``(audio seconds, peaks in [0, 1])`` of *source*'s first audio track.

    ``None`` when the file has no audio track. Only ffmpeg is shipped, not
    ffprobe, so the probe reads the stream list ffmpeg prints for a bare
    ``-i`` (which exits non-zero by design).

    Raises:
        RuntimeError: the audio track exists but could not be decoded.
    """
    probe = _run(_input(source), timeout=60)
    if not _AUDIO_STREAM.search(probe.stderr.decode("utf-8", "replace")):
        return None
    decoded = _run(
        [
            "-loglevel",
            "error",
            *_input(source),
            "-t",
            str(_PEAK_MAX_SECONDS),
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
    # int32, so abs(-32768) does not wrap; half the memory of float.
    samples = np.abs(np.frombuffer(decoded.stdout, np.int16).astype(np.int32))
    if not samples.size:
        return None
    peaks = np.array(
        [part.max() if part.size else 0 for part in np.array_split(samples, buckets)],
        dtype=np.float64,
    )
    top = peaks.max()
    if top > 0:
        peaks = peaks / top
    return samples.size / _PEAK_SAMPLE_RATE, [round(float(p), 4) for p in peaks]
