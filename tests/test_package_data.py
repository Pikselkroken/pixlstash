"""Every runtime data file under ``pixlstash/data`` must ship in the wheel.

A data folder missing from ``[tool.setuptools.package-data]`` works from a
source checkout and fails only once installed, as the training presets did on
the Windows desktop build (issue #2630: ``FileNotFoundError`` on
``data/training-presets`` when checking AI-toolkit). This test catches that
drift without building a wheel, by resolving the declared patterns the way
setuptools does (glob relative to the package directory) and comparing them
with the files git tracks there. ``MANIFEST.in`` is checked the same way for
the sdist (``include`` and ``recursive-include`` lines only).
"""

from __future__ import annotations

import fnmatch
import subprocess
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "pixlstash"
DATA_DIR = PACKAGE_DIR / "data"

# Staged into the wheel at build time from another repository (setup.py), so
# it is not on disk in a plain checkout.
STAGED_AT_BUILD = ("comfyui-pack",)
# Notes for developers, not read at runtime.
DOC_SUFFIXES = (".md",)


def _is_runtime_data(rel: str) -> bool:
    parts = rel.split("/")
    if len(parts) > 2 and parts[1] in STAGED_AT_BUILD:
        return False
    if "__pycache__" in parts or rel.endswith((".py", ".pyc")):
        return False
    return not rel.endswith(DOC_SUFFIXES)


def _data_files() -> set[str]:
    """Files under pixlstash/data, relative to the package directory.

    Taken from git when available, so a stray untracked file (an editor
    backup, ``.DS_Store``) does not fail the test on a developer machine.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "--", "pixlstash/data"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        listed = [line.split("/", 1)[1] for line in out.splitlines() if line]
    except (OSError, subprocess.CalledProcessError):
        listed = []
    if not listed:
        listed = [
            path.relative_to(PACKAGE_DIR).as_posix()
            for path in DATA_DIR.rglob("*")
            if path.is_file()
        ]
    return {rel for rel in listed if _is_runtime_data(rel)}


def test_package_data_covers_every_data_file():
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text("utf-8"))
    patterns = config["tool"]["setuptools"]["package-data"]["pixlstash"]
    shipped = set()
    for pattern in patterns:
        for path in PACKAGE_DIR.glob(pattern):
            if path.is_file():
                shipped.add(path.relative_to(PACKAGE_DIR).as_posix())

    missing = sorted(_data_files() - shipped)
    assert not missing, (
        "pixlstash/data files not matched by [tool.setuptools.package-data] "
        f"in pyproject.toml, so they would be missing from the wheel: {missing}"
    )


def test_manifest_covers_every_data_file():
    rules = []
    for line in (REPO_ROOT / "MANIFEST.in").read_text("utf-8").splitlines():
        words = line.split()
        if len(words) >= 2 and words[0] == "include":
            rules.extend(("file", word) for word in words[1:])
        elif len(words) >= 3 and words[0] == "recursive-include":
            rules.append(("tree", words[1], words[2:]))

    def covered(rel: str) -> bool:
        repo_rel = f"pixlstash/{rel}"
        for rule in rules:
            if rule[0] == "file" and fnmatch.fnmatch(repo_rel, rule[1]):
                return True
            if rule[0] == "tree" and repo_rel.startswith(rule[1].rstrip("/") + "/"):
                name = repo_rel.rsplit("/", 1)[-1]
                if any(fnmatch.fnmatch(name, pat) for pat in rule[2]):
                    return True
        return False

    missing = sorted(rel for rel in _data_files() if not covered(rel))
    assert not missing, (
        "pixlstash/data files not matched by MANIFEST.in, so they would be "
        f"missing from the sdist: {missing}"
    )
