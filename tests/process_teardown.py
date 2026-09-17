"""End the pytest process without running native teardown on Windows.

`backend-windows` intermittently exits 139 *seconds after* pytest has printed
a fully green summary. Run 35252455611 (`backend-windows shard 3`) is the
worked example: 180 passed at 17:37:59.757, ``Segmentation fault`` at
17:38:04.25, nothing in between.

What the runs establish:

* **Nothing of ours is still running.** ``_enforce_no_leaked_threads`` in
  ``tests/conftest.py`` writes a separator whenever *any* non-main thread is
  alive at session end, ours or third-party. That run's log has no such
  separator anywhere, so ``threading.enumerate()`` held the main thread only.
* **It is past CPython.** ``PYTHONFAULTHANDLER=1`` arms faulthandler at
  interpreter start-up, where pytest's plugin cannot unregister it, and it
  stays armed until ``_PyFaulthandler_Fini`` near the end of
  ``Py_FinalizeEx``. Six of these crashes have printed nothing, which places
  the fault after that point: in ``exit()`` - the CRT's ``atexit`` chain and
  the C++ static destructors of torch, onnxruntime, OpenCV and protobuf -
  rather than in any Python frame.
* **The Windows event log adds nothing.** The `Name the faulting module` step
  found no `Application Error` record at all. A hosted runner may simply have
  Windows Error Reporting switched off, so that is not evidence either way.

Which leaves a fault the suite cannot reach from Python. The native thread
pools that torch, onnxruntime and OpenMP keep are not Python threads, so
``threading.enumerate()`` cannot see them and no amount of Python-side
shutdown joins them; a static destructor running while one of them is still
spinning is the shape this crash has.

So do not run that teardown. Once ``pytest`` has computed its exit status the
process has no work left: every assertion has run, every report is written,
and the two gates that would catch a teardown defect of *ours* - the leaked
thread check and the explicit model release - have already run and passed.
``os._exit`` skips ``Py_FinalizeEx``, the CRT ``atexit`` chain and the static
destructors, and goes straight to ``ExitProcess`` with the status pytest
chose.

It is deliberately Windows-only. Linux has the same class of exit-time
destructor and has never crashed on it, so leaving Linux on the full
finalization path keeps a canary for a teardown bug that is genuinely ours.
``PIXLSTASH_PYTEST_FAST_EXIT`` overrides the platform decision in both
directions, so anyone diagnosing this further can put Windows back on the
normal path without editing code.

This does not silence the crash class it cannot reach: ``ExitProcess`` still
runs each DLL's ``DLL_PROCESS_DETACH``. If 139 survives this change, that is
where to look next, and the remaining step is
``TerminateProcess``, which runs no detach handler at all.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from typing import Any, NoReturn

# "1" forces the fast exit on any platform, "0" forces full interpreter
# finalization on every platform. Anything else (including unset) leaves the
# decision to the platform.
FAST_EXIT_ENV = "PIXLSTASH_PYTEST_FAST_EXIT"

# xdist sets this in every worker process. A worker reports back over a channel
# its controller is still reading, so killing one the moment it unconfigures
# turns a finished worker into a crashed one. The gate does not use xdist, but
# `-n auto` is the recommended local runner (pyproject.toml), so the fast exit
# has to stay out of the workers.
XDIST_WORKER_ENV = "PYTEST_XDIST_WORKER"

# The session recorded by ``pytest_sessionfinish``. ``pytest_unconfigure`` runs
# after the terminal summary but is handed only the config, and the exit status
# lives on the session.
_SESSION: Any | None = None


def fast_exit_reason(platform: str, environ: Mapping[str, str]) -> str | None:
    """Say why this process should skip native teardown, or ``None`` if not.

    ``platform`` is a ``sys.platform`` value and ``environ`` an environment
    mapping; both are arguments rather than reads of the real thing so the
    decision is testable on the platform that never takes it.
    """
    if XDIST_WORKER_ENV in environ:
        # Ahead of the override on purpose: forcing the fast exit is a way to
        # exercise it, never a reason to break the xdist protocol.
        return None
    override = environ.get(FAST_EXIT_ENV)
    if override == "0":
        return None
    if override == "1":
        return f"{FAST_EXIT_ENV}=1"
    if platform == "win32":
        return "win32: native exit-time destructors crash intermittently"
    return None


def remember_session(session: Any) -> None:
    """Record the session so ``exit_if_enabled`` can read its exit status."""
    global _SESSION
    _SESSION = session


def exit_if_enabled(
    config: Any,
    *,
    platform: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> None:
    """End the process now, if this platform should skip native teardown.

    Returns normally when it should not, which is the whole of the Linux path.
    """
    if _SESSION is None:
        # No session ran (``--collect-only``, a usage error). Nothing loaded
        # the native libraries this exists for, so take the normal path.
        return
    reason = fast_exit_reason(
        sys.platform if platform is None else platform,
        os.environ if environ is None else environ,
    )
    if reason is None:
        return
    _end_process(config, int(_SESSION.exitstatus), reason)


def _end_process(config: Any, status: int, reason: str) -> NoReturn:
    """Flush everything pytest wrote, then exit without finalizing."""
    # pytest's global capture is still in place: ``Config._cleanup`` stops it,
    # and that runs after the last ``pytest_unconfigure``. Stop it here so the
    # notice below lands in the job log rather than in a capture buffer that
    # nothing will ever read, and so anything captured and not yet replayed is
    # written back to the real streams exactly as a normal exit would.
    capman = config.pluginmanager.getplugin("capturemanager")
    if capman is not None:
        try:
            capman.stop_global_capturing()
        except Exception as exc:
            print(f"Could not stop global capturing: {exc!r}", file=sys.stderr)

    print(
        f"Exiting with status {status} without interpreter finalization "
        f"({reason}). See tests/process_teardown.py.",
        file=sys.stderr,
    )
    for stream in (sys.stdout, sys.stderr, sys.__stdout__, sys.__stderr__):
        if stream is None:
            continue
        try:
            stream.flush()
        except Exception as exc:
            print(f"Could not flush {stream!r} before exit: {exc!r}", file=sys.stderr)

    os._exit(status)
