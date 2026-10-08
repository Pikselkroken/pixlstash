"""Install the ComfyUI-PixlStash nodes PixlStash ships into a local ComfyUI.

PixlStash carries one pinned copy of the nodes (the ``integrations/`` submodule,
staged into the wheel as ``pixlstash/data/comfyui-pack/`` by ``setup.py``), so
the version it installs is the version it is tested with. Installing is a file
copy into ComfyUI's ``custom_nodes`` folder - the nodes need only ``requests``
and ``Pillow``, which every ComfyUI has - followed by a restart of ComfyUI.

Only for a ComfyUI on this computer: the copy writes this machine's disk, and
the folder ComfyUI reports is on ComfyUI's machine. The new copy is staged in
full first; only then does an older copy go to the system trash and the new one
take its place, so two copies never load side by side and a failed copy leaves
the old nodes where they were.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Optional

import requests
from send2trash import send2trash

from pixlstash.pixl_logging import get_logger
from pixlstash.services import comfyui_link_service
from pixlstash.services.comfyui_service import comfyui_folder_paths

logger = get_logger(__name__)

PACK_FOLDER = "ComfyUI-PixlStash"

# What a copy of the nodes leaves out: the pack's own tests, media, caches and
# git plumbing. ``setup.py`` stages the wheel copy with the same list (a test
# holds the two equal).
PACK_LEFT_OUT = (
    ".git",
    ".git*",
    ".ruff_cache",
    ".pytest_cache",
    "tests",
    "examples",
    "screenshots",
    "__pycache__",
)
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
# A checkout (dev, editable, Docker) reads the pinned submodule in place; a
# wheel install has no submodule and reads the copy staged into it. The
# submodule comes first because a checkout that once built a wheel keeps a
# staged copy, which must not outlive the next submodule bump.
_CANDIDATES = (
    _PACKAGE_ROOT.parent / "integrations" / PACK_FOLDER,
    _PACKAGE_ROOT / "data" / "comfyui-pack" / PACK_FOLDER,
)


class PackInstallRefused(Exception):
    """The nodes cannot be installed here; the message says why."""


def bundled_pack() -> Optional[Path]:
    """The copy of the nodes this PixlStash carries, or ``None``."""
    for candidate in _CANDIDATES:
        if (candidate / "__init__.py").is_file():
            return candidate
    return None


def pack_version(folder: Path) -> Optional[str]:
    try:
        text = (folder / "pyproject.toml").read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not read the nodes' version in %s: %s", folder, exc)
        return None
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else None


def _is_pack(folder: Path) -> bool:
    """Whether *folder* is a copy of ComfyUI-PixlStash, under any name.

    ComfyUI-Manager and the registry install it as ``comfyui-pixlstash``, a git
    clone as ``ComfyUI-PixlStash``, a zip as ``ComfyUI-PixlStash-main``. Its
    ``pyproject.toml`` names the package ``pixlstash`` and its saver node file
    is distinctive.
    """
    if folder.name.lower().startswith("comfyui-pixlstash"):
        return True
    try:
        text = (folder / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return False
    return bool(
        re.search(r'^name\s*=\s*"pixlstash"', text, re.MULTILINE)
        and (folder / "nodes" / "picture_saver.py").is_file()
    )


def _custom_nodes_folder(pinned_url: str) -> Path:
    try:
        folder_paths = comfyui_folder_paths(pinned_url)
    except (requests.RequestException, ValueError) as exc:
        raise PackInstallRefused(
            f"ComfyUI did not say where its custom nodes live: {exc}"
        ) from exc
    paths = (
        (folder_paths or {}).get("custom_nodes")
        if isinstance(folder_paths, dict)
        else None
    )
    for path in paths if isinstance(paths, list) else []:
        if not isinstance(path, str) or not os.path.isdir(path):
            continue
        folder = Path(path).resolve()
        if _plausible_custom_nodes(folder):
            return folder
        logger.warning(
            "Not installing the PixlStash nodes into %s: ComfyUI reported it as "
            "custom_nodes, but it does not look like one.",
            folder,
        )
    raise PackInstallRefused(
        "ComfyUI did not report a custom_nodes folder that exists on this computer."
    )


def _plausible_custom_nodes(folder: Path) -> bool:
    """Whether *folder* can be ComfyUI's custom_nodes folder.

    The path comes from whatever answers on ComfyUI's port, which picks where
    files are written and which folders go to the trash. So it must be named
    ``custom_nodes``, must not be the home folder, and must not hold the copy
    being installed (that would trash the source).
    """
    if folder.name != "custom_nodes" or folder == Path.home().resolve():
        return False
    source = bundled_pack()
    return source is None or not source.resolve().is_relative_to(folder)


def _restart_comfyui(pinned_url: str) -> Optional[str]:
    """Ask ComfyUI-Manager to restart ComfyUI; ``None`` on success, else why not.

    Manager 4 serves ``POST /v2/manager/reboot`` (refusing form posts, so the
    body is JSON); older Managers ``GET /manager/reboot``. ComfyUI itself has no
    restart route.
    """
    attempts = (("POST", "v2/manager/reboot"), ("GET", "manager/reboot"))
    last = "ComfyUI-Manager is not installed"
    for method, path in attempts:
        try:
            response = requests.request(
                method,
                f"{pinned_url}{path}",
                json={} if method == "POST" else None,
                timeout=10,
                allow_redirects=False,
                proxies=comfyui_link_service.NO_PROXY,
            )
        except requests.ConnectionError:
            # Manager restarts before it answers, so the connection drops. A
            # ComfyUI that was down all along also lands here; the dialog's
            # wait for the nodes to load tells the two apart.
            return None
        except requests.RequestException as exc:
            last = str(exc)
            continue
        if response.status_code == 200:
            return None
        if response.status_code == 403:
            return "ComfyUI-Manager's security level does not allow a restart"
        last = f"ComfyUI-Manager answered HTTP {response.status_code}"
    return last


def _remove_partial(partial: Path) -> None:
    """Delete a staging copy of PixlStash's own making. Raises ``OSError``."""
    if partial.is_symlink() or partial.is_file():
        partial.unlink()
    elif partial.exists():
        shutil.rmtree(partial)


def _discard_partial(partial: Path) -> None:
    """Best-effort :func:`_remove_partial` on a path that already failed."""
    try:
        _remove_partial(partial)
    except OSError as exc:
        logger.warning("Could not remove the staged nodes at %s: %s", partial, exc)


def install(comfyui_url: Optional[str]) -> dict:
    """Install the bundled nodes into the saved ComfyUI and restart it.

    Raises:
        PackInstallRefused: not a ComfyUI on this computer, no bundled copy, or
            no custom_nodes folder.
    """
    if not comfyui_url:
        raise PackInstallRefused("No ComfyUI address is saved.")
    try:
        where, pinned = comfyui_link_service.locate(comfyui_url)
    except ValueError as exc:
        raise PackInstallRefused(str(exc)) from exc
    if where != "this_computer":
        raise PackInstallRefused(
            "PixlStash only installs the nodes into a ComfyUI on this computer."
        )
    source = bundled_pack()
    if source is None:
        raise PackInstallRefused("This PixlStash was built without the ComfyUI nodes.")
    custom_nodes = _custom_nodes_folder(pinned)

    target = custom_nodes / PACK_FOLDER
    partial = custom_nodes / f".{PACK_FOLDER}.installing"
    if os.path.lexists(target) and (target.is_symlink() or not target.is_dir()):
        # Refused before anything is trashed: the move into place would fail
        # after the older copies had gone, leaving ComfyUI with none.
        raise PackInstallRefused(
            f"Cannot install the nodes: {target} is a file or a link, not a folder. "
            "Remove it yourself, then install again."
        )
    # Stage the whole copy before anything is trashed: a copy that fails half
    # way must leave ComfyUI with the nodes it had.
    try:
        _remove_partial(partial)
        shutil.copytree(source, partial, ignore=shutil.ignore_patterns(*PACK_LEFT_OUT))
    except OSError as exc:
        logger.warning("Staging the PixlStash nodes in %s failed: %s", partial, exc)
        _discard_partial(partial)
        raise PackInstallRefused(
            f"Could not write the nodes to {target}: {exc}"
        ) from exc

    replaced = []
    for folder in sorted(custom_nodes.iterdir()):
        if folder != partial and folder.is_dir() and _is_pack(folder):
            logger.info(
                "Moving the older ComfyUI-PixlStash at %s to the trash.", folder
            )
            try:
                send2trash(str(folder))
            except OSError as exc:
                # Never fall back to deleting it: the trash is the undo.
                logger.warning("Could not move %s to the trash: %s", folder, exc)
                _discard_partial(partial)
                raise PackInstallRefused(
                    f"Could not move the older copy at {folder} to the trash "
                    f"({exc}). Remove it yourself, then install again."
                ) from exc
            replaced.append(str(folder))
    try:
        os.replace(partial, target)
    except OSError as exc:
        logger.warning("Copying the PixlStash nodes into %s failed: %s", target, exc)
        _discard_partial(partial)
        raise PackInstallRefused(
            f"Could not write the nodes to {target}: {exc}"
        ) from exc
    version = pack_version(source)
    logger.info(
        "Installed ComfyUI-PixlStash %s into %s (replaced %s).",
        version,
        target,
        replaced or "nothing",
    )

    problem = _restart_comfyui(pinned)
    if problem:
        logger.info("ComfyUI was not restarted for the new nodes: %s", problem)
    return {
        "installed_to": str(target),
        "version": version,
        "replaced": replaced,
        "restart": "manual" if problem else "requested",
        "detail": f"{problem}, so restart ComfyUI yourself." if problem else None,
    }
