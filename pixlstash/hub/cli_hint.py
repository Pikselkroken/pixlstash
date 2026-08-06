"""Compose the exact library-CLI invocation for the running deployment.

In the MVP the CLI is the only way to add or remove a library, so the Settings
› Libraries tab has to *teach* it (multi-library plan §3.4). Printing a generic
``pixlstash-libraries`` and leaving the user to work out whether it is on PATH
is what that requirement exists to prevent, so the server composes the command
from its own deployment and the UI renders it verbatim.

The result is host information (an install path, or a container name), so it is
sent only to a caller that passes the locality check — see plan §11 q3 and the
route's declaration in :mod:`pixlstash.authz.registry`.
"""

from __future__ import annotations

import os
import shlex
import shutil
import socket
import sys

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# The console script declared in pyproject, mirroring ``pixlstash-server``.
# Verbs are grouped, so the plan's ``pixlstash libraries <verb>`` is spelled
# ``pixlstash-cli libraries <verb>`` and the CLI has room for command groups
# beyond libraries.
CONSOLE_SCRIPT = "pixlstash-cli"

MODULE_INVOCATION = "-m pixlstash.cli"


def running_in_docker() -> bool:
    """Return True when this process is inside a Docker container.

    Mirrors :meth:`pixlstash.server.Server.running_in_docker` (explicit env flag
    from our own images, else the runtime's ``/.dockerenv`` marker). Duplicated
    rather than imported because :mod:`pixlstash.server` pulls in the whole
    application and this module is reached from the CLI.
    """
    if os.environ.get("PIXLSTASH_IN_DOCKER", "") == "1":
        return True
    return os.path.exists("/.dockerenv")


def _quote(path: str) -> str:
    """Shell-quote *path* while keeping a leading ``~`` expandable.

    ``shlex.quote`` would wrap ``~/x`` as ``'~/x'``, and a shell reads that as a
    literal directory named ``~``: the abbreviation would break the command it
    is meant to shorten. Quoting only the part after the tilde keeps expansion
    working while still surviving a path with spaces.

    Args:
        path: A path, possibly already abbreviated by :func:`_shorten`.

    Returns:
        The path, quoted where it needs to be.
    """
    if path.startswith("~/"):
        return "~/" + shlex.quote(path[2:])
    return shlex.quote(path)


def _shorten(path: str) -> str:
    """Abbreviate the user's home directory to ``~`` in *path*.

    A venv install prints a path long enough to wrap the settings panel, and
    most of it is the home directory the reader already knows. POSIX shells
    expand ``~``, so the result is still copy-pasteable.

    Skipped on Windows, where neither cmd nor PowerShell expands ``~`` in the
    middle of a command line: a shorter string that no longer runs is a worse
    hint than a long one.

    Args:
        path: An absolute filesystem path.

    Returns:
        The path with ``$HOME`` replaced by ``~`` when that is safe, else *path*.
    """
    if os.name == "nt":
        return path
    home = os.path.expanduser("~")
    if home and home != os.sep and path.startswith(home + os.sep):
        return "~" + path[len(home) :]
    return path


def cli_hint(verb: str = "libraries list") -> str:
    """Return a copy-pasteable command that runs the library CLI here.

    Args:
        verb: The command to show, group included. ``libraries list`` is the
            safe one to put in front of a user who has not read the docs yet.

    Returns:
        A single shell command line. Paths are quoted, so a Windows install
        directory with spaces survives the copy, and the home directory is
        abbreviated to ``~`` where the shell will expand it again.
    """
    if running_in_docker():
        container = os.environ.get("HOSTNAME") or socket.gethostname()
        return f"docker exec -it {shlex.quote(container)} {CONSOLE_SCRIPT} {verb}"

    # A frozen desktop build has no console scripts on PATH and its interpreter
    # is the bundled backend executable.
    if getattr(sys, "frozen", False):
        return f"{_quote(_shorten(sys.executable))} {MODULE_INVOCATION} {verb}"

    on_path = shutil.which(CONSOLE_SCRIPT)
    if on_path:
        # Installed and resolvable by name: the short form is what the user
        # would type, so show that rather than an absolute path.
        return f"{CONSOLE_SCRIPT} {verb}"

    # Installed in an environment whose scripts are not on PATH (a venv the
    # server was started from by absolute path, most often). Prefer the console
    # script sitting beside the running interpreter: it is the same environment,
    # and it is shorter than spelling out the module invocation.
    beside = os.path.join(os.path.dirname(sys.executable), CONSOLE_SCRIPT)
    if os.path.isfile(beside):
        return f"{_quote(_shorten(beside))} {verb}"

    # No console script to point at. The interpreter that is running is the one
    # that can import pixlstash, so name it.
    logger.debug(
        "%s is not on PATH; falling back to a module invocation for the CLI hint",
        CONSOLE_SCRIPT,
    )
    return f"{_quote(_shorten(sys.executable))} {MODULE_INVOCATION} {verb}"
