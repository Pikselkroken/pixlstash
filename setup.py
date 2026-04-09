from pathlib import Path
import subprocess
import sys

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py
from setuptools.command.sdist import sdist as _sdist


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
        subprocess.check_call(
            ["npm", "ci"],
            cwd=str(frontend_dir),
            shell=sys.platform == "win32",
        )

    print("setup.py: running npm run build in frontend/", flush=True)
    subprocess.check_call(
        ["npm", "run", "build"],
        cwd=str(frontend_dir),
        shell=sys.platform == "win32",
    )


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
