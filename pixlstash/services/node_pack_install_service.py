"""Have ComfyUI-Manager install a node pack PixlStash knows, then restart ComfyUI.

A pre-flight that finds a workflow's nodes missing names the pack that provides
them (:func:`~pixlstash.services.node_packs.packs_for`). This asks the saved
ComfyUI's Manager to install that pack at its pinned version, waits for Manager
to finish, and restarts ComfyUI only when nothing is running or queued on it
(:func:`~pixlstash.services.comfyui_pack_service.restart_comfyui`, #2054).

**Manager does the work, never PixlStash.** A ComfyUI with no Manager, or one
that refuses, gets instructions instead; nothing is copied into
``custom_nodes`` from here. That is also why the ComfyUI may be on another
computer: the files are written by ComfyUI's own Manager, on its own disk.

Manager comes in two dialects. 3.x is a custom node and serves
``/manager/...`` and ``/customnode/...``; 4.x is built into ComfyUI
(``--enable-manager``) and serves the same reads under ``/v2/``, with one
queue route for every kind of task. A 4.x Manager in its legacy-UI mode speaks a
third shape, so it gets the instructions too.

A pack Manager already lists is never touched, whatever state it is in: the
owner installed it, and overwriting, re-enabling or changing its version is
theirs to do.
"""

from __future__ import annotations

import time
import uuid
from typing import Optional

import requests

from pixlstash.pixl_logging import get_logger
from pixlstash.services import comfyui_link_service, node_packs
from pixlstash.services.comfyui_pack_service import restart_comfyui

logger = get_logger(__name__)

V3 = "v3"
V4 = "v4"
# Every route both dialects share sits under this prefix in 4.x.
_PREFIX = {V3: "", V4: "v2/"}

POLL_EVERY_S = 2.0
# ponytail: a fixed ceiling on Manager's install; a pack with a long pip step
# can outlast it, and then the owner restarts by hand. Raise it, or report
# Manager's progress, if packs that slow join KNOWN_PACKS.
INSTALL_TIMEOUT_S = 180.0


class PackInstallRefused(Exception):
    """The pack cannot be installed through PixlStash; the message says why."""


class UnknownPack(PackInstallRefused):
    """The id names no pack in :data:`~pixlstash.services.node_packs.KNOWN_PACKS`."""


def _yourself(pack: node_packs.NodePack) -> str:
    return f"Install {pack.id} {pack.version} from ComfyUI Manager yourself."


def _call(
    method: str,
    pinned: str,
    path: str,
    *,
    body: Optional[dict] = None,
    timeout: float = 10,
) -> Optional[requests.Response]:
    """One request to the pinned ComfyUI, or ``None`` when it did not answer.

    Never through a proxy and never after a redirect: the pinned address is the
    vetted one, and a redirect could send the request somewhere that is not.
    """
    try:
        return requests.request(
            method,
            f"{pinned}{path}",
            json=body,
            timeout=timeout,
            allow_redirects=False,
            proxies=comfyui_link_service.NO_PROXY,
        )
    except requests.RequestException as exc:
        logger.info("ComfyUI did not answer %s %s%s: %s", method, pinned, path, exc)
        return None


def _manager(pinned: str, pack: node_packs.NodePack) -> str:
    """Which Manager dialect answers, refusing when none can install."""
    found = _call("GET", pinned, "v2/manager/version")
    if found is not None and found.status_code == 200:
        legacy = _call("GET", pinned, "v2/manager/is_legacy_manager_ui")
        try:
            is_legacy = bool(
                legacy is not None and legacy.json()["is_legacy_manager_ui"]
            )
        except (ValueError, KeyError, TypeError) as exc:
            # An answer that is not the documented shape: not knowing which
            # payload this Manager takes, PixlStash does not send one.
            logger.warning(
                "ComfyUI-Manager at %s did not say whether it runs its legacy "
                "UI (%s), so the install is refused.",
                pinned,
                exc,
            )
            is_legacy = True
        if is_legacy:
            logger.info(
                "ComfyUI-Manager 4 at %s runs its legacy UI; not installing.", pinned
            )
            raise PackInstallRefused(
                "ComfyUI-Manager runs in its legacy mode here, which PixlStash "
                f"cannot install through. {_yourself(pack)}"
            )
        return V4
    found = _call("GET", pinned, "manager/version")
    if found is not None and found.status_code == 200:
        return V3
    logger.info(
        "No ComfyUI-Manager answered at %s (last status %s); not installing %s.",
        pinned,
        getattr(found, "status_code", None),
        pack.id,
    )
    raise PackInstallRefused(
        "ComfyUI-Manager is not installed in this ComfyUI, so PixlStash cannot "
        f"install {pack.name}. Install ComfyUI-Manager first, or put {pack.name} "
        f"{pack.version} in ComfyUI's custom_nodes folder yourself."
    )


def _installed(pinned: str, manager: str, pack: node_packs.NodePack) -> bool:
    """Whether Manager lists the pack as installed, in any state or version.

    Manager keys the list by folder name and carries the registry id beside it;
    a registry install names the folder after the id, a git clone after the
    repository, so both are compared without case.
    """
    answer = _call("GET", pinned, f"{_PREFIX[manager]}customnode/installed")
    try:
        if answer is None or answer.status_code != 200:
            raise ValueError(f"HTTP {getattr(answer, 'status_code', None)}")
        listing = answer.json()
        if not isinstance(listing, dict):
            raise ValueError("not a JSON object")
    except ValueError as exc:
        logger.warning(
            "ComfyUI-Manager (%s) at %s did not list its installed packs: %s",
            manager,
            pinned,
            exc,
        )
        raise PackInstallRefused(
            f"ComfyUI-Manager did not say which node packs it has. {_yourself(pack)}"
        ) from exc
    for folder, info in listing.items():
        cnr_id = info.get("cnr_id") if isinstance(info, dict) else None
        if pack.id in (str(folder).lower(), str(cnr_id or "").lower()):
            return True
    return False


def _queue_install(pinned: str, manager: str, pack: node_packs.NodePack) -> None:
    """Put the install on Manager's queue and start the queue."""
    ui_id = f"pixlstash-{uuid.uuid4().hex[:12]}"
    params = {
        "id": pack.id,
        "version": pack.version,
        "selected_version": pack.version,
        "channel": "default",
        "mode": "remote",
        "ui_id": ui_id,
    }
    if manager == V4:
        path = "v2/manager/queue/task"
        body = {
            "ui_id": ui_id,
            "client_id": "pixlstash",
            "kind": "install",
            "params": params,
        }
    else:
        path = "manager/queue/install"
        body = params
    queued = _call("POST", pinned, path, body=body)
    status = getattr(queued, "status_code", None)
    # 3.x answers a security refusal with 403, or with 404 and "security
    # error" for the pack's risk level; 4.x checks the level in its worker.
    refused = status == 403 or (
        status == 404 and "security" in (queued.text or "").lower()
    )
    if refused:
        logger.warning(
            "ComfyUI-Manager (%s) at %s refused to install %s %s: HTTP %s",
            manager,
            pinned,
            pack.id,
            pack.version,
            status,
        )
        raise PackInstallRefused(
            "ComfyUI-Manager's security level does not allow installing node "
            f"packs. {_yourself(pack)}"
        )
    if status != 200:
        logger.warning(
            "ComfyUI-Manager (%s) at %s did not queue %s %s: HTTP %s",
            manager,
            pinned,
            pack.id,
            pack.version,
            status,
        )
        raise PackInstallRefused(
            f"ComfyUI-Manager did not accept the install (HTTP {status}). "
            f"{_yourself(pack)}"
        )
    # A JSON body: Manager refuses a form post to its body-less routes.
    started = _call("POST", pinned, f"{_PREFIX[manager]}manager/queue/start", body={})
    status = getattr(started, "status_code", None)
    if status not in (200, 201):
        logger.warning(
            "ComfyUI-Manager (%s) at %s did not start its queue: HTTP %s",
            manager,
            pinned,
            status,
        )
        raise PackInstallRefused(
            f"ComfyUI-Manager queued the install but did not start it (HTTP "
            f"{status}). Start it from ComfyUI Manager."
        )


def _wait_for_manager(pinned: str, manager: str) -> None:
    """Return once Manager's queue is idle; refuse after the ceiling."""
    deadline = time.monotonic() + INSTALL_TIMEOUT_S
    while True:
        answer = _call("GET", pinned, f"{_PREFIX[manager]}manager/queue/status")
        try:
            status = answer.json() if answer is not None else {}
        except ValueError as exc:
            logger.info("Manager's queue status at %s was not JSON: %s", pinned, exc)
            status = {}
        if (
            isinstance(status, dict)
            and status.get("is_processing") is False
            and not status.get("in_progress_count")
            and not status.get("pending_count")
        ):
            return
        if time.monotonic() >= deadline:
            logger.warning(
                "ComfyUI-Manager (%s) at %s was still busy after %ss: %s",
                manager,
                pinned,
                INSTALL_TIMEOUT_S,
                status,
            )
            raise PackInstallRefused(
                "ComfyUI-Manager is still installing; restart ComfyUI once it "
                "has finished."
            )
        time.sleep(POLL_EVERY_S)


def install(comfyui_url: Optional[str], pack_id: str) -> dict:
    """Install a known pack through the saved ComfyUI's Manager, then restart it.

    Raises:
        UnknownPack: *pack_id* is not in ``KNOWN_PACKS``.
        PackInstallRefused: anything else that stops the install; the message
            says what the owner can do instead.
    """
    pack = node_packs.pack(pack_id)
    if pack is None:
        raise UnknownPack(f"PixlStash does not know a node pack called {pack_id!r}.")
    if not comfyui_url:
        raise PackInstallRefused("No ComfyUI address is saved.")
    try:
        _where, pinned = comfyui_link_service.locate(comfyui_url)
    except ValueError as exc:
        raise PackInstallRefused(str(exc)) from exc
    if pinned is None:
        raise PackInstallRefused(
            "PixlStash installs node packs only into a ComfyUI on this computer "
            f"or the local network. {_yourself(pack)}"
        )

    manager = _manager(pinned, pack)
    if _installed(pinned, manager, pack):
        logger.info(
            "%s is already in ComfyUI-Manager's list at %s; leaving it alone.",
            pack.id,
            pinned,
        )
        if _offers(pinned, pack):
            raise PackInstallRefused(f"{pack.name} is already installed and loaded.")
        raise PackInstallRefused(
            f"{pack.name} is installed but not loading. Enable or fix it in "
            "ComfyUI Manager."
        )
    logger.info(
        "Asking ComfyUI-Manager (%s) at %s to install %s %s.",
        manager,
        pinned,
        pack.id,
        pack.version,
    )
    _queue_install(pinned, manager, pack)
    _wait_for_manager(pinned, manager)
    if not _installed(pinned, manager, pack):
        logger.warning(
            "ComfyUI-Manager (%s) at %s finished without installing %s %s.",
            manager,
            pinned,
            pack.id,
            pack.version,
        )
        raise PackInstallRefused(
            f"ComfyUI-Manager did not install {pack.name}; ComfyUI's log says "
            f"why. {_yourself(pack)}"
        )

    problem = restart_comfyui(pinned)
    if problem:
        logger.info("ComfyUI was not restarted for %s: %s", pack.id, problem)
    return {
        "pack": pack.id,
        "version": pack.version,
        "manager": manager,
        "restart": "manual" if problem else "requested",
        "detail": (
            f"{problem}, so restart it yourself once its queue is empty."
            if problem
            else None
        ),
    }


def loaded(comfyui_url: Optional[str], pack_id: str) -> bool:
    """Whether the saved ComfyUI now offers every node class of the pack.

    ComfyUI not answering counts as not loaded: while it restarts, that is the
    expected answer.

    Raises:
        UnknownPack: *pack_id* is not in ``KNOWN_PACKS``.
    """
    pack = node_packs.pack(pack_id)
    if pack is None:
        raise UnknownPack(f"PixlStash does not know a node pack called {pack_id!r}.")
    if not comfyui_url:
        return False
    try:
        _where, pinned = comfyui_link_service.locate(comfyui_url)
    except ValueError as exc:
        logger.info("Not checking %s at %r: %s", pack.id, comfyui_url, exc)
        return False
    return pinned is not None and _offers(pinned, pack)


def _offers(pinned: str, pack: node_packs.NodePack) -> bool:
    """Whether ComfyUI's ``object_info`` lists every class of *pack*.

    Asked the way every other call here is (pinned, no proxy, no redirect),
    with the longer timeout the whole map can take to build.
    """
    answer = _call("GET", pinned, "object_info", timeout=60)
    try:
        object_info = answer.json() if answer is not None else None
    except ValueError as exc:
        logger.info("ComfyUI's object_info at %s was not JSON: %s", pinned, exc)
        object_info = None
    if not isinstance(object_info, dict) or answer.status_code != 200:
        return False
    return all(name in object_info for name in pack.classes)
