from pathlib import Path
import subprocess
import sys
import time

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py
from setuptools.command.sdist import sdist as _sdist


def _run_npm(args, cwd, *, attempts=1) -> None:
    """Run ``npm <args>`` in ``cwd``, retrying transient failures.

    ``npm ci`` is network-bound and intermittently aborts on CI with ECONNRESET /
    "network aborted". frontend/.npmrc widens npm's own per-request retry window;
    this coarse loop is the backstop for a whole-process abort. Local-only commands
    such as ``npm run build`` use the default ``attempts=1`` (no retry).
    """
    for attempt in range(1, attempts + 1):
        try:
            subprocess.check_call(
                ["npm", *args],
                cwd=str(cwd),
                shell=sys.platform == "win32",
            )
            return
        except subprocess.CalledProcessError:
            if attempt == attempts:
                raise
            delay = 5 * attempt
            print(
                f"setup.py: 'npm {' '.join(args)}' failed "
                f"(attempt {attempt}/{attempts}); retrying in {delay}s...",
                flush=True,
            )
            time.sleep(delay)


def _build_frontend() -> None:
    repo_root = Path(__file__).resolve().parent
    frontend_dir = repo_root / "frontend"
    dist_dir = repo_root / "pixlstash" / "frontend" / "dist"
    required_frontend_sources = [
        frontend_dir / "package.json",
        frontend_dir / "index.html",
        frontend_dir / "vite.config.js",
        frontend_dir / "src",
    ]

    if not frontend_dir.is_dir():
        # Running from an installed/unpacked sdist that already has the built dist
        if dist_dir.is_dir():
            return
        raise FileNotFoundError(
            "frontend/ source directory not found and pixlstash/frontend/dist/ is missing. "
            "Cannot build the frontend."
        )

    # sdists can intentionally ship a prebuilt frontend dist without full source files.
    has_full_frontend_source = all(path.exists() for path in required_frontend_sources)
    if not has_full_frontend_source:
        if dist_dir.is_dir():
            print(
                "setup.py: frontend source incomplete; using existing pixlstash/frontend/dist/",
                flush=True,
            )
            return
        raise FileNotFoundError(
            "frontend/ source is incomplete and pixlstash/frontend/dist/ is missing. "
            "Cannot build the frontend."
        )

    node_modules = frontend_dir / "node_modules"
    if not node_modules.is_dir():
        print("setup.py: running npm ci in frontend/", flush=True)
        _run_npm(["ci"], frontend_dir, attempts=3)

    print("setup.py: running npm run build in frontend/", flush=True)
    _run_npm(["run", "build"], frontend_dir)


class build_py(_build_py):
    def run(self):
        _build_frontend()
        super().run()


class sdist(_sdist):
    def run(self):
        _build_frontend()
        super().run()


if __name__ == "__main__":
    setup(
        cmdclass={
            "build_py": build_py,
            "sdist": sdist,
        }
    )
