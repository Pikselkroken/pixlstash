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
    # A launcher that knows better than we can. The desktop app sets this: its
    # console script is sealed inside the app image at a path that changes every
    # launch, and its hub is not the platform default one, so the command that
    # works there is the app's own launcher (or the shell shim pointing at it)
    # and nothing this module could infer from ``sys.executable``.
    declared = os.environ.get("PIXLSTASH_CLI_COMMAND", "").strip()
    if declared:
        return f"{declared} {verb}"

    if running_in_docker():
        container = os.environ.get("HOSTNAME") or socket.gethostname()
        return f"docker exec -it {shlex.quote(container)} {CONSOLE_SCRIPT} {verb}"

    # A frozen desktop build is the one case that genuinely needs a path: it
    # ships no console script and has no ``python`` to fall back on, because its
    # interpreter *is* the bundled backend executable.
    if getattr(sys, "frozen", False):
        return f"{_quote(_shorten(sys.executable))} {MODULE_INVOCATION} {verb}"

    # Everywhere else the hint stays a short command the user can read at a
    # glance. An absolute interpreter path is technically the most precise
    # answer and it was what this returned, but it wrapped the settings panel
    # and buried the part that matters (the verb) behind boilerplate the reader
    # already knows. The cost is one assumption, documented in the README next
    # to these commands: run them with the same environment active that the
    # server runs in.
    beside = os.path.join(os.path.dirname(sys.executable), CONSOLE_SCRIPT)
    if shutil.which(CONSOLE_SCRIPT) or os.path.isfile(beside):
        return f"{CONSOLE_SCRIPT} {verb}"

    # No console script anywhere: a source checkout, where the module
    # invocation is what actually works.
    logger.debug(
        "%s is not installed as a console script; showing the module invocation",
        CONSOLE_SCRIPT,
    )
    return f"python {MODULE_INVOCATION} {verb}"
