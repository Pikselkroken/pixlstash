from pathlib import Path
import os
import shutil
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


# The ComfyUI-PixlStash nodes ride in the wheel, copied from the pinned
# submodule, so PixlStash can install the exact version it is tested with.
# Only what ComfyUI loads is copied; the pack's own tests and media stay out.
_ROOT = Path(__file__).resolve().parent
_PACK_SOURCE = _ROOT / "integrations" / "ComfyUI-PixlStash"
_PACK_STAGED = _ROOT / "pixlstash" / "data" / "comfyui-pack" / "ComfyUI-PixlStash"
# Kept equal to comfyui_pack_service.PACK_LEFT_OUT (a test holds them equal);
# not imported from it, since importing pixlstash at build time pulls its deps.
_PACK_LEFT_OUT = (
    ".git",
    ".git*",
    ".ruff_cache",
    ".pytest_cache",
    "tests",
    "examples",
    "screenshots",
    "__pycache__",
)


def _stage_comfyui_pack() -> None:
    """Copy the pinned nodes into package data.

    A build that ships (the desktop app, PyPI) sets
    ``PIXLSTASH_REQUIRE_COMFYUI_PACK=1``, and an empty submodule then stops it:
    a release without the nodes would build fine and offer an install it cannot
    do. Any other build (CI's ``pip install .``) warns and goes on; PixlStash
    then says it was built without the nodes.
    """
    if not (_PACK_SOURCE / "__init__.py").is_file():
        # Only an unpacked sdist (it has PKG-INFO at its root) may build from
        # the copy it carries. In a checkout a staged copy is left over from
        # an earlier build, of whatever version the submodule was then.
        if (_ROOT / "PKG-INFO").is_file():
            if (_PACK_STAGED / "__init__.py").is_file():
                return
        elif _PACK_STAGED.exists():
            shutil.rmtree(_PACK_STAGED)
        message = (
            f"{_PACK_SOURCE} is empty. Run `git submodule update --init`: "
            "the wheel carries the ComfyUI-PixlStash nodes."
        )
        if os.environ.get("PIXLSTASH_REQUIRE_COMFYUI_PACK") == "1":
            raise FileNotFoundError(message)
        print(f"setup.py: WARNING: {message} Building without them.", flush=True)
        return
    if _PACK_STAGED.exists():
        shutil.rmtree(_PACK_STAGED)
    shutil.copytree(
        _PACK_SOURCE, _PACK_STAGED, ignore=shutil.ignore_patterns(*_PACK_LEFT_OUT)
    )
    print(f"setup.py: staged ComfyUI-PixlStash into {_PACK_STAGED}", flush=True)


class build_py(_build_py):
    def run(self):
        _build_frontend()
        # An editable install (dev, CI, Docker) reads the submodule in place.
        if not getattr(self, "editable_mode", False):
            _stage_comfyui_pack()
        super().run()


class sdist(_sdist):
    def run(self):
        _build_frontend()
        _stage_comfyui_pack()
        super().run()


if __name__ == "__main__":
    setup(
        cmdclass={
            "build_py": build_py,
            "sdist": sdist,
        }
    )
