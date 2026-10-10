"""Link a local owner's ComfyUI to PixlStash: one full-access token, written in.

The ComfyUI-PixlStash nodes call back into PixlStash with a URL, a token and a
certificate choice they read from ComfyUI's own persisted settings
(``user/default/comfy.settings.json``, keys ``PixlStash.*``). ComfyUI's
``POST /settings`` merges a JSON object into that file, so linking is three
settings written over ComfyUI's HTTP API; the pack needs no route of its own.

Owner decisions (2026-10-06), which this module implements and nothing more:

* The token is an ``ALL`` owner token. ComfyUI gets full access.
* The token works only from ComfyUI's own address (#1810). ComfyUI serves its
  settings, token included, to anyone who can reach it, so the token is bound
  to the source address PixlStash sees on the link's round-trip check, and
  refused from anywhere else (see ``auth.TOKEN_BIND_PENDING``).
* Automatic linking is for a ComfyUI on this computer (plain HTTP over the
  loopback listener) or on the local network (the external listener, which
  must be on **with HTTPS**; the pack is handed PixlStash's certificate and
  verifies against it). Anything else is a manual exercise.

The record of the live link (which token, which URL) is kept in the server
config under :data:`LINK_CONFIG_KEY`, so Disconnect can revoke exactly that
token and a restart can re-send a loopback URL whose port moved.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import threading
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit

import requests
from cryptography import x509
from fastapi import HTTPException, Request
from sqlmodel import select

from pixlstash.auth import TOKEN_BIND_PENDING, is_local_ip
from pixlstash.database import DBPriority
from pixlstash.db_models import UserToken
from pixlstash.pixl_logging import get_logger
from pixlstash.server_config_io import persist_server_config
from pixlstash.services.comfyui_service import (
    NO_PROXY,
    comfyui_can_open_workflows,
    normalize_comfyui_url,
    probe_comfyui,
)

logger = get_logger(__name__)

LINK_CONFIG_KEY = "comfyui_link"
TOKEN_DESCRIPTION = "ComfyUI (linked)"

# The pack's ComfyUI settings ids (ComfyUI-PixlStash connection.py).
SETTING_URL = "PixlStash.ServerURL"
SETTING_TOKEN = "PixlStash.APIToken"
SETTING_SSL = "PixlStash.VerifySSL"
SETTING_CA = "PixlStash.CACertificate"

STEP_IDS = ("reach", "nodes", "link", "check")

# One link or unlink at a time: two concurrent links would each mint a token and
# the second record would orphan the first.
_LINK_LOCK = threading.Lock()


class LinkRefused(Exception):
    """The link step cannot go ahead; ``reason`` is the API's code for why."""

    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason


def _step(state: str, detail: Optional[str] = None, reason: Optional[str] = None):
    return {"state": state, "detail": detail, "reason": reason}


def _addresses(host: str) -> list:
    """Every address *host* resolves to, IPv4-mapped IPv6 unwrapped."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        logger.info("Could not resolve ComfyUI host %r: %s", host, exc)
        return []
    found = []
    for info in infos:
        try:
            address = ipaddress.ip_address(str(info[4][0]).split("%")[0])
        except ValueError:
            continue
        if address.version == 6 and address.ipv4_mapped:
            address = address.ipv4_mapped
        found.append(address)
    return found


def locate(comfyui_url: str) -> tuple[Optional[str], Optional[str]]:
    """Where ComfyUI is, and the URL that reaches exactly that place.

    ``("this_computer" | "local_network", pinned_url)``, or ``(None, None)``
    for anywhere else. **Every** address the host resolves to must qualify,
    because the HTTP client tries each in turn; one public address among
    private ones is anywhere else. The pinned URL names the vetted address as
    an IP literal, so a request carrying the token cannot be steered by a
    second lookup (DNS rebinding) to a host that was not checked.

    Raises:
        ValueError: *comfyui_url* is not a plain http(s) address.
    """
    base = normalize_comfyui_url(comfyui_url)
    parts = urlsplit(base)
    addresses = _addresses(parts.hostname or "")
    if not addresses or any(a.is_unspecified or a.is_multicast for a in addresses):
        return None, None
    if all(a.is_loopback for a in addresses):
        where = "this_computer"
    elif all(a.is_loopback or is_local_ip(str(a)) for a in addresses):
        where = "local_network"
    else:
        return None, None
    chosen = sorted(addresses, key=lambda a: (a.version, int(a)))[0]
    host = f"[{chosen}]" if chosen.version == 6 else str(chosen)
    netloc = f"{host}:{parts.port}" if parts.port else host
    return where, f"{parts.scheme}://{netloc}{parts.path}"


def _address_toward(host: str, port: int) -> Optional[str]:
    """This machine's address on the interface that routes to *host*.

    A UDP ``connect`` sends nothing; it only asks the kernel for the route.
    *host* is an IP literal (``locate`` pins it), IPv4 or IPv6.
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            sock.connect((host, port))
            # An IPv6 link-local answer carries its zone ("fe80::1%eth0").
            return str(sock.getsockname()[0]).split("%")[0]
    except OSError as exc:
        logger.warning("No route from this machine to ComfyUI at %s: %s", host, exc)
        return None


def _cert_covers(cert_pem: bytes, ip: str) -> bool:
    cert = x509.load_pem_x509_certificate(cert_pem)
    try:
        names = cert.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
    except x509.ExtensionNotFound:
        return False
    return ipaddress.ip_address(ip) in names.get_values_for_type(x509.IPAddress)


def _read_cert(server) -> bytes:
    """PixlStash's HTTPS certificate, which the pack verifies against.

    Raises:
        LinkRefused: HTTPS is on but the certificate is not configured or
            cannot be read.
    """
    certfile = server._server_config.get("ssl_certfile")
    if not certfile:
        raise LinkRefused(
            "certificate_missing",
            "HTTPS is on, but PixlStash has no certificate configured to hand "
            "ComfyUI. Restart PixlStash to make one.",
        )
    try:
        with open(certfile, "rb") as handle:
            return handle.read()
    except OSError as exc:
        logger.warning("Could not read the HTTPS certificate %s: %s", certfile, exc)
        raise LinkRefused(
            "certificate_missing",
            f"PixlStash could not read its HTTPS certificate at {certfile}: {exc}",
        ) from exc


def _is_desktop() -> bool:
    return os.environ.get("PIXLSTASH_INSTALL_TYPE", "").strip().lower() == "electron"


def _loopback_port(server) -> int:
    port = os.environ.get("PIXLSTASH_PORT", "").strip()
    return int(port) if port else int(server._server_config.get("port", 9537))


def link_target(server, where: str, pinned_url: str) -> tuple[str, Optional[str]]:
    """The PixlStash URL ComfyUI should call, and the certificate to trust.

    Raises:
        LinkRefused: the listener ComfyUI needs is not serving what it must.
    """
    config = server._server_config
    https = bool(config.get("require_ssl", False))
    if where == "this_computer":
        if _is_desktop():
            # The desktop window's own listener: always on, always plain HTTP.
            return f"http://127.0.0.1:{_loopback_port(server)}", None
        scheme = "https" if https else "http"
        url = f"{scheme}://127.0.0.1:{_loopback_port(server)}"
        return url, (_read_cert(server).decode() if https else None)

    if _is_desktop():
        # Password first: remote access will not bind without one, so turning
        # it on before there is a password only moves the refusal.
        if not server._external_listener_password_ready():
            raise LinkRefused(
                "password_missing",
                "Remote access needs an owner password before it is switched on.",
            )
        if not config.get("external_server_enabled", False):
            raise LinkRefused(
                "remote_access_off",
                "ComfyUI is on another computer, so it reaches PixlStash over "
                "your network, and remote access is off.",
            )
    elif str(config.get("host", "127.0.0.1")) in ("127.0.0.1", "localhost", "::1"):
        raise LinkRefused(
            "remote_access_off",
            "PixlStash only listens on this computer, so ComfyUI on another "
            "computer cannot reach it.",
        )
    if not https:
        raise LinkRefused(
            "https_off",
            "Remote access is on without HTTPS. A ComfyUI on your network is "
            "only linked over HTTPS.",
        )
    port = int(config.get("port", 9537))
    comfy = urlsplit(pinned_url)
    comfy_host = (comfy.hostname or "").strip("[]")
    comfy_port = comfy.port or (443 if comfy.scheme == "https" else 80)
    address = _address_toward(comfy_host, comfy_port)
    if address is None:
        raise LinkRefused("not_local", f"This computer has no route to {comfy_host}.")
    cert = _read_cert(server)
    if not _cert_covers(cert, address):
        raise LinkRefused(
            "certificate_mismatch",
            f"PixlStash's HTTPS certificate does not cover {address}, the "
            "address ComfyUI would use. The address probably changed since the "
            "certificate was made; delete it and restart PixlStash to make a new one.",
        )
    host = f"[{address}]" if ":" in address else address
    return f"https://{host}:{port}", cert.decode()


def link_status(server) -> dict:
    """The live link. ``bound_address`` is where its token works from;
    ``refused_from`` the last other address it was used from since start-up
    (ComfyUI's address changed, or somebody else has the key); ``exposure``
    whether other computers can reach the linked ComfyUI (see :func:`exposure`).
    """
    record = server._server_config.get(LINK_CONFIG_KEY) or {}
    public_id = record.get("token_public_id")
    return {
        "linked": bool(public_id),
        "comfyui_url": record.get("comfyui_url"),
        "pixlstash_url": record.get("pixlstash_url"),
        "where": record.get("where"),
        "linked_at": record.get("linked_at"),
        "bound_address": record.get("bound_address"),
        "refused_from": (
            server.auth.bound_token_refusals.get(public_id) if public_id else None
        ),
        "exposure": record.get("exposure"),
    }


def exposure(where: Optional[str], probe: dict) -> Optional[str]:
    """Whether computers other than ComfyUI's own can reach it, and so can use
    PixlStash through the pack's routes: ``"other_computer"`` (ComfyUI is on
    another computer, so it listens on the network), ``"listening"`` (on this
    computer, started with ``--listen``), or None (this computer only, or not
    known)."""
    if where == "local_network":
        return "other_computer"
    if probe.get("listens_on_network"):
        return "listening"
    return None


def _bound_address(server, public_id: str) -> Optional[str]:
    def find(session):
        return session.exec(
            select(UserToken.bound_address).where(UserToken.public_id == public_id)
        ).first()

    return server.auth._db.run_task(find, priority=DBPriority.IMMEDIATE)


def _save_record(server, record: Optional[dict]) -> None:
    if record is None:
        server._server_config.pop(LINK_CONFIG_KEY, None)
    else:
        server._server_config[LINK_CONFIG_KEY] = record
    config_path = getattr(server, "_server_config_path", None)
    if config_path:
        persist_server_config(config_path, server._server_config)


def _write_settings(pinned_url: str, settings: dict) -> None:
    """Merge *settings* into ComfyUI's persisted settings. Raises on refusal.

    Never follows a redirect: ``requests`` re-sends the body, token included,
    to whatever ``Location`` names, and that host was never checked. Only a
    200 is success.
    """
    response = requests.post(
        f"{pinned_url}settings",
        json=settings,
        timeout=10,
        allow_redirects=False,
        proxies=NO_PROXY,
    )
    if response.status_code != 200:
        raise requests.HTTPError(
            f"ComfyUI answered HTTP {response.status_code} to the settings write"
        )


def _revoke(server, request: Request, public_id: str) -> None:
    """Revoke by ``public_id``, never by row id: SQLite reuses row ids, so a
    stored integer can come to name an unrelated token."""

    def find(session):
        return session.exec(
            select(UserToken.id).where(UserToken.public_id == public_id)
        ).first()

    token_id = server.auth._db.run_task(find, priority=DBPriority.IMMEDIATE)
    if token_id is None:
        logger.info("ComfyUI link token %s was already gone.", public_id)
        return
    try:
        server.auth.delete_token(request, token_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        logger.info("ComfyUI link token %s was already gone.", public_id)


def _check_round_trip(pinned_url: str, token: str) -> Optional[str]:
    """``None`` when ComfyUI reached PixlStash with *token*, else why not.

    The pack's proxy takes the token from the caller and the URL and
    certificate from ComfyUI's settings, so a 200 proves the settings just
    written reach PixlStash and PixlStash accepts the token.
    """
    try:
        response = requests.get(
            f"{pinned_url}pixlstash/sort_mechanisms",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
            allow_redirects=False,
            proxies=NO_PROXY,
        )
    except requests.RequestException as exc:
        logger.warning("ComfyUI link check at %s failed: %s", pinned_url, exc)
        return f"ComfyUI did not answer the check: {exc}"
    if response.status_code == 200:
        return None
    try:
        said = response.json().get("error")
    except (ValueError, AttributeError):
        said = None
    logger.warning(
        "ComfyUI link check at %s: HTTP %s (%s)",
        pinned_url,
        response.status_code,
        said,
    )
    return said or f"ComfyUI answered the check with HTTP {response.status_code}."


def link(server, request: Request, saved_url: Optional[str]) -> dict:
    """Run the four steps; the reply's ``steps`` say how far it got and why."""
    with _LINK_LOCK:
        return _link(server, request, saved_url)


def _link(server, request: Request, saved_url: Optional[str]) -> dict:
    steps = {step: _step("not_run") for step in STEP_IDS}

    exposed = None

    def reply(linked: bool = False) -> dict:
        return {
            "linked": linked,
            "link": link_status(server),
            "steps": [{"id": step, **steps[step]} for step in STEP_IDS],
            "exposure": exposed,
        }

    if not saved_url:
        steps["reach"] = _step("failed", "No ComfyUI address is saved.")
        return reply()
    try:
        probe = probe_comfyui(saved_url)
        where, pinned = locate(saved_url)
    except ValueError as exc:
        steps["reach"] = _step("failed", str(exc))
        return reply()
    if not probe["reachable"]:
        steps["reach"] = _step("failed", probe["detail"])
        return reply()
    comfyui_url = probe["url"]
    exposed = exposure(where, probe)
    place = {
        "this_computer": "on this computer",
        "local_network": "on your local network",
    }.get(where, "outside your local network")
    steps["reach"] = _step("done", f"{urlsplit(comfyui_url).netloc} · {place}")

    has_pack = comfyui_can_open_workflows(comfyui_url.rstrip("/"))
    if has_pack is None:
        steps["nodes"] = _step("failed", "ComfyUI did not list its extensions.")
        return reply()
    if not has_pack:
        steps["nodes"] = _step(
            "needs_you",
            "The PixlStash nodes are not installed in this ComfyUI, or are too old.",
            "pack_missing",
        )
        return reply()
    steps["nodes"] = _step("done", "Installed")

    if where is None:
        steps["link"] = _step(
            "needs_you",
            "This ComfyUI is outside your local network. Automatic setup only "
            "links a ComfyUI on this computer or your local network.",
            "not_local",
        )
        return reply()
    try:
        pixlstash_url, cert = link_target(server, where, pinned)
    except LinkRefused as refused:
        steps["link"] = _step("needs_you", str(refused), refused.reason)
        return reply()

    minted = server.auth.create_token(
        request, TOKEN_DESCRIPTION, scope="ALL", bound_address=TOKEN_BIND_PENDING
    )
    public_id = minted["public_id"]
    try:
        _write_settings(
            pinned,
            {
                SETTING_URL: pixlstash_url,
                SETTING_TOKEN: minted["token"],
                SETTING_SSL: True,
                SETTING_CA: cert or "",
            },
        )
    except requests.RequestException as exc:
        logger.warning("Writing PixlStash settings into ComfyUI failed: %s", exc)
        _revoke(server, request, public_id)
        steps["link"] = _step(
            "failed", f"ComfyUI refused the settings: {exc}", "write_failed"
        )
        return reply()
    old_record = server._server_config.get(LINK_CONFIG_KEY)
    old_token = (old_record or {}).get("token_public_id")
    record = {
        "token_public_id": public_id,
        "comfyui_url": comfyui_url,
        "pixlstash_url": pixlstash_url,
        "where": where,
        "linked_at": datetime.now(timezone.utc).isoformat(),
        "exposure": exposed,
    }
    try:
        _save_record(server, record)
    except Exception:
        # ComfyUI holds the token but no record does, so nothing could ever
        # revoke it: revoke it now, and take it back from ComfyUI.
        logger.exception("Recording the ComfyUI link failed; revoking its token.")
        if old_record is None:
            server._server_config.pop(LINK_CONFIG_KEY, None)
        else:
            server._server_config[LINK_CONFIG_KEY] = old_record
        _revoke(server, request, public_id)
        _clear_comfyui_key(pinned, comfyui_url)
        raise
    leftover = ""
    if old_token and old_token != public_id:
        try:
            _revoke(server, request, old_token)
        except Exception:
            # The new link is recorded and works; failing the request now would
            # report a link that exists as broken. The replaced token is still
            # a live full-access key, so say so here and in the reply.
            logger.exception(
                "ComfyUI is linked with token %s, but revoking the token it "
                "replaced (public id %s) failed: that full-access token is "
                "STILL LIVE. Delete it under Account > API Tokens.",
                public_id,
                old_token,
            )
            leftover = (
                'The key "ComfyUI (linked)" it replaced could not be revoked '
                "and still works: delete the older one under Account › API Tokens."
            )
    logger.info(
        "ComfyUI at %s linked to %s with full access (token %s; replaced %s).",
        comfyui_url,
        pixlstash_url,
        minted["token_id"],
        (old_record or {}).get("comfyui_url") or "no earlier link",
    )
    steps["link"] = _step(
        "done",
        " ".join(filter(None, [f"Full access, through {pixlstash_url}.", leftover])),
    )

    problem = _check_round_trip(pinned, minted["token"])
    bound = None if problem else _bound_address(server, public_id)
    if not problem and bound in (None, TOKEN_BIND_PENDING):
        # A 200 that did not come through PixlStash with this token: nothing
        # bound it, so it is not ComfyUI's.
        logger.warning(
            "ComfyUI link check at %s answered 200 but token %s was never "
            "used; undoing the link.",
            pinned,
            public_id,
        )
        problem = "ComfyUI answered the check without using its new key."
    if problem:
        # ComfyUI holds a full-access token that does not work from there.
        # Undo the link entirely, so no live token is left that the link
        # record says nothing useful about, and the status agrees with the
        # reply.
        _unlink(server, request)
        steps["link"] = _step(
            "failed",
            " ".join(
                filter(None, ["Undone: ComfyUI could not use the key.", leftover])
            ),
        )
        steps["check"] = _step("failed", problem, "check_failed")
        return reply()
    _save_record(server, {**record, "bound_address": bound})
    logger.info("ComfyUI link token %s is bound to %s.", public_id, bound)
    steps["check"] = _step(
        "done",
        f"ComfyUI reached PixlStash with its new key, which now works only "
        f"from {bound}.",
    )
    return reply(linked=True)


def unlink(server, request: Request) -> dict:
    """Revoke the link's token and clear ComfyUI's copy, best effort."""
    with _LINK_LOCK:
        return _unlink(server, request)


def _pinned(record: dict) -> Optional[str]:
    """The recorded ComfyUI, re-vetted: only ever a local address."""
    try:
        where, pinned = locate(record.get("comfyui_url") or "")
    except ValueError as exc:
        logger.warning("The recorded ComfyUI address is not usable: %s", exc)
        return None
    return pinned if where else None


def _unlink(server, request: Request) -> dict:
    record = server._server_config.get(LINK_CONFIG_KEY) or {}
    if record.get("token_public_id"):
        _revoke(server, request, record["token_public_id"])
    # No record is no link: Disconnect asks whenever it cannot tell.
    pinned = _pinned(record) if record.get("comfyui_url") else None
    if pinned:
        _clear_comfyui_key(pinned, record["comfyui_url"])
    _save_record(server, None)
    return {"linked": False}


def _clear_comfyui_key(pinned: str, comfyui_url: str) -> None:
    """Best effort: blank the key in ComfyUI's settings (it is revoked here)."""
    try:
        _write_settings(pinned, {SETTING_TOKEN: "", SETTING_CA: ""})
    except requests.RequestException as exc:
        logger.warning(
            "Could not clear the PixlStash key from ComfyUI at %s (it is "
            "revoked here, so it no longer works): %s",
            comfyui_url,
            exc,
        )


def reannounce(server) -> None:
    """After a restart, re-send a loopback URL whose port moved.

    The desktop listener's port is stable best-effort (``stableLoopbackPort``)
    and falls back to a random one when the usual port is taken. The token is
    unchanged, so only the URL is written.
    """
    record = server._server_config.get(LINK_CONFIG_KEY) or {}
    if record.get("where") != "this_computer" or not _is_desktop():
        return
    current = f"http://127.0.0.1:{_loopback_port(server)}"
    if record.get("pixlstash_url") == current:
        return
    pinned = _pinned(record)
    if pinned is None:
        logger.warning(
            "The linked ComfyUI at %s is no longer on this computer; not "
            "telling it PixlStash moved to %s.",
            record.get("comfyui_url"),
            current,
        )
        return
    try:
        _write_settings(pinned, {SETTING_URL: current})
    except requests.RequestException as exc:
        logger.warning(
            "PixlStash moved to %s but could not tell the linked ComfyUI at %s: "
            "%s. Link it again from Settings.",
            current,
            record.get("comfyui_url"),
            exc,
        )
        return
    _save_record(server, {**record, "pixlstash_url": current})
    logger.info("Told the linked ComfyUI that PixlStash is now at %s.", current)
