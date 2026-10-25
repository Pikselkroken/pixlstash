"""Find a local ai-toolkit checkout and check that it can train.

Behind ``/training/ai-toolkit/discover`` and ``/training/ai-toolkit/check``.
:func:`discover` looks for a checkout in the usual places without running
anything; :func:`check` runs the checkout's own Python twice (a torch/CUDA probe
and ``run.py --help``) as argv lists, never through a shell. Both name host
paths or start host processes, so the routes that call them are
``LOCAL_OWNER_ONLY``.

Nothing here imports torch or ai-toolkit: the probe runs in a child process, so
PixlStash stays importable on a machine with no GPU stack.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from typing import Callable, Iterable

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# Where a checkout is looked for after the registered output folders and the
# saved path. Relative to the owner's home directory.
HOME_CHECKOUT_CANDIDATES = (
    "ai-toolkit",
    os.path.join("Projects", "ai-toolkit"),
    os.path.join("src", "ai-toolkit"),
    os.path.join("code", "ai-toolkit"),
    os.path.join("git", "ai-toolkit"),
)

# Seconds: the probe imports torch (slow on a cold disk); run.py --help loads the
# trainer's modules. Both are bounded so a wedged install cannot hang the route.
PROBE_TIMEOUT_S = 90.0
TRIAL_TIMEOUT_S = 180.0

# A short script, run by the checkout's own interpreter. It prints one JSON line.
PROBE_SCRIPT = (
    "import json, torch\n"
    "cuda = bool(torch.cuda.is_available())\n"
    "gpus = []\n"
    "if cuda:\n"
    "    for i in range(torch.cuda.device_count()):\n"
    "        p = torch.cuda.get_device_properties(i)\n"
    "        gpus.append({'name': p.name, 'total_mb': int(p.total_memory // (1024 * 1024))})\n"
    "print(json.dumps({'torch': torch.__version__, 'cuda': cuda, 'gpus': gpus}))\n"
)

_VERSION_RE = re.compile(r"""^\s*VERSION\s*=\s*["']([^"'\r\n]+)["']""", re.MULTILINE)

# (returncode, stdout, stderr). Injected by tests so no real Python is spawned.
Runner = Callable[[list[str], str, float], tuple[int, str, str]]


def is_checkout(path: str) -> bool:
    """Return True when *path* is an ai-toolkit checkout: ``run.py`` and ``toolkit/``.

    Args:
        path: A candidate directory.

    Returns:
        Whether both markers are present.
    """
    return (
        os.path.isdir(path)
        and os.path.isfile(os.path.join(path, "run.py"))
        and os.path.isdir(os.path.join(path, "toolkit"))
    )


def read_version(repo_path: str) -> str | None:
    """Read ``VERSION = "x.y.z"`` from the checkout's ``version.py``.

    The file is parsed with a regex and never imported or executed.

    Args:
        repo_path: The checkout directory.

    Returns:
        The version string, or None if the file or the assignment is missing.
    """
    path = os.path.join(repo_path, "version.py")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read(64 * 1024)
    except OSError as exc:
        logger.warning("Could not read ai-toolkit version file %s: %s", path, exc)
        return None
    match = _VERSION_RE.search(text)
    return match.group(1).strip() if match else None


def find_python(
    repo_path: str, which: Callable[[str], str | None] = shutil.which
) -> str | None:
    """Find the interpreter AI-toolkit itself would use.

    ``.venv`` is tried first, then ``venv``, each as ``bin/python`` (or
    ``Scripts/python.exe`` on Windows); then ``python3`` on the PATH.

    Args:
        repo_path: The checkout directory.
        which: Lookup for ``python3`` on the PATH; injected by tests.

    Returns:
        An absolute interpreter path, or None.
    """
    exe = (
        os.path.join("Scripts", "python.exe")
        if os.name == "nt"
        else os.path.join("bin", "python")
    )
    for env_dir in (".venv", "venv"):
        candidate = os.path.join(repo_path, env_dir, exe)
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return which("python3")


def discover(
    *,
    shelf_source_folders: Iterable[str] = (),
    saved_repo_path: str | None = None,
    home: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> dict:
    """Look for an ai-toolkit checkout in the usual places.

    Order: the parent of each registered shelf ``source`` folder (an ai-toolkit
    ``output/`` folder), the saved ``ai_toolkit_repo_path``, then the common
    locations under the home directory. The first directory that is a checkout
    wins.

    Args:
        shelf_source_folders: Registered ``source`` folder paths.
        saved_repo_path: The owner's currently saved checkout path, if any.
        home: Home directory to search; defaults to the process user's.
        which: Lookup for ``python3`` on the PATH; injected by tests.

    Returns:
        ``{"found", "repo_path", "python", "version", "searched"}``. ``searched``
        lists every directory considered, in order.
    """
    home = home or os.path.expanduser("~")
    candidates: list[str] = [
        os.path.dirname(os.path.normpath(folder)) for folder in shelf_source_folders
    ]
    if saved_repo_path:
        candidates.append(saved_repo_path)
    candidates.extend(os.path.join(home, rel) for rel in HOME_CHECKOUT_CANDIDATES)

    searched: list[str] = []
    for candidate in candidates:
        if candidate not in searched:
            searched.append(candidate)

    for candidate in searched:
        if is_checkout(candidate):
            repo = os.path.abspath(candidate)
            return {
                "found": True,
                "repo_path": repo,
                "python": find_python(repo, which),
                "version": read_version(repo),
                "searched": searched,
            }
    return {
        "found": False,
        "repo_path": None,
        "python": None,
        "version": None,
        "searched": searched,
    }


def run_host_process(argv: list[str], cwd: str, timeout: float) -> tuple[int, str, str]:
    """Run one host process with an argv list, no shell, and a timeout.

    Args:
        argv: The command, ``argv[0]`` being the interpreter.
        cwd: The working directory (the checkout).
        timeout: Seconds before the process is abandoned.

    Returns:
        ``(returncode, stdout, stderr)``. A timeout or a failure to start is
        returned as a nonzero code with the reason in ``stderr``.
    """
    try:
        done = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return -1, "", f"timed out after {int(timeout)} s"
    except OSError as exc:
        return -1, "", str(exc)
    return done.returncode, done.stdout, done.stderr


def _last_line(text: str) -> str:
    """The last non-empty line of *text*, bounded, or a placeholder."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1][:300] if lines else "no output"


def check(repo_path: str, python: str, runner: Runner | None = None) -> dict:
    """Probe the checkout's Python for CUDA and run ``run.py --help``.

    "Can train" means torch imports with CUDA and ``run.py --help`` exits 0.
    The caller validates both paths first; this function does not.

    Args:
        repo_path: The checkout directory (the process cwd).
        python: The interpreter that runs the trainer.
        runner: Runs one process; defaults to :func:`run_host_process`.

    Returns:
        ``{"ok", "version", "torch", "cuda", "gpus", "trial_ok", "detail"}``.
        ``detail`` is one plain sentence naming the first failure, or None.
    """
    run = runner or run_host_process
    result: dict = {
        "ok": False,
        "version": read_version(repo_path),
        "torch": None,
        "cuda": False,
        "gpus": [],
        "trial_ok": False,
        "detail": None,
    }

    code, out, err = run([python, "-c", PROBE_SCRIPT], repo_path, PROBE_TIMEOUT_S)
    probe = None
    if code == 0:
        try:
            probe = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            logger.warning("ai-toolkit probe printed no JSON: %r", out[:200])
    if probe is None:
        probe_detail = f"torch did not import: {_last_line(err)}"
    else:
        result["torch"] = probe.get("torch")
        result["cuda"] = bool(probe.get("cuda"))
        result["gpus"] = [
            {
                "name": str(gpu.get("name", "")),
                "total_mb": int(gpu.get("total_mb") or 0),
            }
            for gpu in probe.get("gpus") or []
        ]
        probe_detail = None if result["cuda"] else "torch imports but sees no CUDA GPU"

    code, _out, err = run([python, "run.py", "--help"], repo_path, TRIAL_TIMEOUT_S)
    result["trial_ok"] = code == 0
    trial_detail = None if code == 0 else f"run.py --help failed: {_last_line(err)}"

    result["detail"] = probe_detail or trial_detail
    result["ok"] = result["detail"] is None
    return result


__all__ = [
    "HOME_CHECKOUT_CANDIDATES",
    "check",
    "discover",
    "find_python",
    "is_checkout",
    "read_version",
    "run_host_process",
]
