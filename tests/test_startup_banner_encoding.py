"""Regression tests for issue #448: startup crash under a non-UTF-8 console.

On Windows the standard streams default to the legacy ANSI codepage
(``cp1252``), which cannot encode the box-drawing glyphs in the startup banner.
That raised ``UnicodeEncodeError`` and took the whole backend down at startup.
``_force_utf8_streams`` must reconfigure the streams so the banner (and any
other non-ASCII output) prints cleanly.
"""

import io

import pytest

from pixlstash.app import _force_utf8_streams
from pixlstash.server import Server

_BANNER_ROWS = [
    ("Window", "http://127.0.0.1:9537"),
    ("Remote", "https://0.0.0.0:9537"),
]


def _cp1252_stream():
    """A text stream backed by cp1252, mimicking a Windows console/pipe."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252")


def test_banner_crashes_on_cp1252_stream(monkeypatch):
    """Without the fix, the banner cannot encode under cp1252 (the bug)."""
    monkeypatch.setattr("sys.stdout", _cp1252_stream())
    with pytest.raises(UnicodeEncodeError):
        Server._print_banner("1.6.0", _BANNER_ROWS)


def test_force_utf8_streams_lets_banner_print(monkeypatch):
    """After reconfiguring to UTF-8, the banner prints without crashing."""
    monkeypatch.setattr("sys.stdout", _cp1252_stream())
    monkeypatch.setattr("sys.stderr", _cp1252_stream())

    _force_utf8_streams()

    import sys

    assert sys.stdout.encoding.lower() == "utf-8"
    assert sys.stderr.encoding.lower() == "utf-8"
    # Must not raise UnicodeEncodeError anymore.
    Server._print_banner("1.6.0", _BANNER_ROWS)


def test_force_utf8_streams_tolerates_missing_reconfigure(monkeypatch):
    """A stream without ``reconfigure`` (frozen builds) is skipped, not fatal."""

    class _NoReconfigure:
        encoding = "cp1252"

    monkeypatch.setattr("sys.stdout", _NoReconfigure())
    monkeypatch.setattr("sys.stderr", None)

    # Best-effort: should return cleanly rather than raising.
    _force_utf8_streams()
