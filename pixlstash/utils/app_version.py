"""The version of PixlStash that is running, as one answer for every caller."""

import tomllib
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def app_version() -> str:
    """Return the running PixlStash version, or ``"unknown"``.

    A source checkout's ``pyproject.toml`` wins over installed metadata: an
    editable install's ``pixlstash.egg-info`` is written once and never updated,
    so a checkout installed at 1.9.0rc1 kept calling itself that through every
    release after it. A wheel install has no ``pyproject.toml`` beside the
    package, and reads its metadata, which is current there.
    """
    try:
        with open(_PYPROJECT, "rb") as handle:
            project = tomllib.load(handle).get("project", {})
        if project.get("name") == "pixlstash" and project.get("version"):
            return project["version"]
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.debug(
            "No version from %s (%s); using package metadata.", _PYPROJECT, exc
        )
    try:
        return package_version("pixlstash")
    except PackageNotFoundError:
        logger.warning("pixlstash is neither a checkout nor installed; version unknown")
        return "unknown"
