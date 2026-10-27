"""The ComfyUI instances the owner can run workflows on (#2167).

``user.comfyui_connections`` is a JSON list of ``{"id", "name", "url",
"status", "gpu", "vram_mb"}``, the last three what the newest probe of that
address said (``status`` ``"connected"``, ``"offline"``, or null when never
asked). ``user.comfyui_url`` stays the address everything submits to, so the
current connection is the entry with that address and switching is a
``comfyui_url`` PATCH: every reader of ``comfyui_url`` follows the switch.

A NULL list reads as one entry for the saved address. That is the upgrade from
a single ``comfyui_url``, and it holds for a hub row as for an identity copied
from an older vault after the hub's migrations ran, which a seed written by
the migration would miss. The first change stores the list, and so does the
first probe answer noted on it.
"""

import json
import secrets
from urllib.parse import urlsplit

from pixlstash.database import DBPriority
from pixlstash.db_models import User
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# The id the saved address's entry reads as, and keeps once stored.
SAVED_ADDRESS_ID = "saved"

_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def _key(url: str | None) -> str:
    # ``comfyui_url`` PATCHed through the API is stored as typed, so the same
    # address may arrive in another case or without its trailing slash.
    return (url or "").strip().rstrip("/").lower()


def default_name(url: str) -> str:
    """ "This PC" for a loopback address, else its host."""
    host = urlsplit(url).hostname or ""
    return "This PC" if host in _LOOPBACK else host or "ComfyUI"


def _entry(entry_id: str, name: str, url: str) -> dict:
    return {
        "id": entry_id,
        "name": name,
        "url": url,
        "status": None,
        "gpu": None,
        "vram_mb": None,
    }


def read(user) -> list[dict]:
    """The owner's connections, in the order they were added."""
    raw = getattr(user, "comfyui_connections", None)
    if raw is not None:
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, list):
            return [e for e in parsed if isinstance(e, dict) and e.get("url")]
        logger.error(
            "user.comfyui_connections is not a JSON list (%r); reading it as "
            "the saved address alone. The next connection change rewrites it.",
            str(raw)[:200],
        )
    url = getattr(user, "comfyui_url", None)
    return [_entry(SAVED_ADDRESS_ID, default_name(url), url)] if url else []


def current_id(user) -> str | None:
    """The id of the connection runs go to, or None with no address saved."""
    saved = _key(getattr(user, "comfyui_url", None))
    return next(
        (e["id"] for e in read(user) if saved and _key(e["url"]) == saved), None
    )


def _store(user, entries: list[dict]) -> None:
    user.comfyui_connections = json.dumps(entries)


def add(user, name: str | None, url: str) -> dict:
    """Add *url* (already normalised); the entry already listed for it if any.

    Never switches: runs keep going where they went until the owner picks it.
    """
    entries = read(user)
    for entry in entries:
        if _key(entry["url"]) == _key(url):
            return entry
    entry = _entry(secrets.token_hex(4), (name or "").strip() or default_name(url), url)
    entries.append(entry)
    _store(user, entries)
    return entry


def set_url(user, new: str | None) -> None:
    """Set ``comfyui_url`` to *new*, keeping the list in step.

    An address already listed becomes current as it is. Otherwise the current
    connection takes the new address (a name made from the old one follows
    it), or with none current the address is added. Clearing the address
    removes the current connection and the first one left becomes current, so
    connections that remain are never hidden behind the Set up row.
    """
    entries = read(user)
    old = _key(user.comfyui_url)
    current = next((e for e in entries if old and _key(e["url"]) == old), None)
    if new is None:
        if current is not None:
            entries.remove(current)
        new = entries[0]["url"] if entries else None
    elif any(_key(e["url"]) == _key(new) for e in entries):
        pass
    elif current is not None:
        if current["name"] == default_name(current["url"]):
            current["name"] = default_name(new)
        current.update(url=new, status=None, gpu=None, vram_mb=None)
    else:
        entries.append(_entry(secrets.token_hex(4), default_name(new), new))
    user.comfyui_url = new
    _store(user, entries)


def record_probe(user, answer: dict) -> bool:
    """Note a probe's answer on the connection with its address.

    Returns whether anything changed, so an answer already noted writes nothing.
    """
    entries = read(user)
    seen = {
        "status": "connected" if answer.get("reachable") else "offline",
        "gpu": answer.get("gpu"),
        "vram_mb": answer.get("vram_mb"),
    }
    for entry in entries:
        if _key(entry["url"]) == _key(answer.get("url")):
            if all(entry.get(k) == v for k, v in seen.items()):
                return False
            entry.update(seen)
            _store(user, entries)
            return True
    return False


def save(server, user_id: int, change) -> User:
    """Apply *change* to the owner's hub row and keep the cached owner in step.

    *change* takes the user and returns whether it changed anything.
    """

    def write(session, user_id: int):
        user = session.get(User, user_id)
        if user is None:
            raise LookupError(f"No user {user_id} to keep ComfyUI connections on.")
        if change(user):
            session.add(user)
            session.commit()
            session.refresh(user)
        return user

    user = server.hub_engine.run_task(write, user_id, priority=DBPriority.IMMEDIATE)
    # As the config PATCH does: background work reads the cached owner.
    server.auth.user = user
    server._user = user
    return user
