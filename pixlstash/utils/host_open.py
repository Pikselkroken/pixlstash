"""Spawn the host's file manager on a path.

The mechanism three route modules already carry inline (``config.py``'s
``_open_in_os``, ``reference_folders.open_reference_folder``,
``pictures/_misc.open_picture_location``): ``os.startfile`` on Windows, ``open``
on macOS, ``xdg-open`` everywhere else. The shelf is the fourth caller, and it
is here rather than inline **because it is not byte-identical to the three**:
this one reads the opener's exit status, which is what makes a headless host an
honest refusal instead of a reported success (below). Migrating the other three
onto it is a change of its own - each wraps the spawn in different error
handling and each has tests patching ``subprocess.run`` in its own module - so
this is not yet the deduplication its existence invites.

Every caller of this is on the ``LOOPBACK_OWNER_ONLY`` red line
(``docs/backend_architecture.md`` §16.3.1) - it drives the server process's own
shell, which is authority over the host rather than over any row.
"""

import os
import subprocess
import sys

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


def open_in_file_manager(path: str) -> bool:
    """Open *path* in the host's file manager. ``False`` when it did not.

    **The POSIX openers' exit status is read**, which is the one thing the three
    inline copies do not do: they pass ``check=False`` and discard it. A
    headless or containerised host usually *has* ``xdg-open`` - it is a shell
    script - and it exits non-zero (1–4, documented) when there is no desktop
    to hand the path to, so ignoring the status reports success for the exact
    deployment where nothing opened. ``open`` on macOS behaves the same way.
    Windows' ``os.startfile`` has no status to read and raises instead.

    The opener returns as soon as it has handed the path over, not when the
    window is up, so waiting on it costs a few milliseconds rather than the
    file manager's start-up.

    Args:
        path: An existing directory or file on the server's own disk.

    Returns:
        True when the opener accepted the path. False when there is nothing at
        *path*, or the opener is absent, or it refused - all three are a
        refusal to report rather than a crash.
    """
    if not path or not os.path.exists(path):
        logger.warning("Refusing to open %r: nothing is there.", path)
        return False
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # noqa: S606 - Windows' own shell-open verb
            return True
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        completed = subprocess.run([opener, path], check=False)
        if completed.returncode != 0:
            logger.warning(
                "%s refused to open %s (exit %s); this host most likely has no "
                "desktop session to open it in.",
                opener,
                path,
                completed.returncode,
            )
            return False
        return True
    except Exception:
        # With the traceback: what lands here is platform-specific and varies by
        # desktop - a missing opener, a permission error, a Windows shell verb
        # that failed - and the frame is the only thing that says which.
        logger.exception(
            "Failed to open %s in the host file manager (platform %s).",
            path,
            sys.platform,
        )
        return False
