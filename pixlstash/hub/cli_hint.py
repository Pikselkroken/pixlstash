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

# The console script declared in pyproject. The plan writes the verbs as
# ``pixlstash libraries <verb>``; the package ships them as a dedicated script
# instead, matching the existing ``pixlstash-server`` entry point rather than
# introducing an umbrella command that would have to absorb it.
CONSOLE_SCRIPT = "pixlstash-libraries"

MODULE_INVOCATION = "-m pixlstash.libraries"


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


def cli_hint(verb: str = "list") -> str:
    """Return a copy-pasteable command that runs the library CLI here.

    Args:
        verb: The verb to show. ``list`` is the safe one to put in front of a
            user who has not read the docs yet.

    Returns:
        A single shell command line. Paths are quoted, so a Windows install
        directory with spaces survives the copy.
    """
    if running_in_docker():
        container = os.environ.get("HOSTNAME") or socket.gethostname()
        return f"docker exec -it {shlex.quote(container)} {CONSOLE_SCRIPT} {verb}"

    # A frozen desktop build has no console scripts on PATH and its interpreter
    # is the bundled backend executable.
    if getattr(sys, "frozen", False):
        return f"{shlex.quote(sys.executable)} {MODULE_INVOCATION} {verb}"

    on_path = shutil.which(CONSOLE_SCRIPT)
    if on_path:
        # Installed and resolvable by name: the short form is what the user
        # would type, so show that rather than an absolute path.
        return f"{CONSOLE_SCRIPT} {verb}"

    # Installed in an environment whose scripts are not on PATH (a venv the
    # server was started from by absolute path, most often). The interpreter
    # that is running is the one that can import pixlstash, so name it.
    logger.debug(
        "%s is not on PATH; falling back to a module invocation for the CLI hint",
        CONSOLE_SCRIPT,
    )
    return f"{shlex.quote(sys.executable)} {MODULE_INVOCATION} {verb}"
