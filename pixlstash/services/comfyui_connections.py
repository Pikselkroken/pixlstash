"""The ComfyUI instances the owner can run workflows on (#2167).

``user.comfyui_connections`` is a JSON list of ``{"id", "name", "url",
"status", "gpu", "vram_mb", "nodes"}``, the last four what the newest probe of
that address said (``status`` ``"connected"``, ``"offline"``, or null when never
asked; ``nodes`` whether the ComfyUI-PixlStash nodes can open a workflow there). ``user.comfyui_url`` stays the address everything submits to, so the
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


def is_listed(user, url: str | None) -> bool:
    """Whether *url* is a connection's address, however it is spelled."""
    return bool(_key(url)) and any(_key(e["url"]) == _key(url) for e in read(user))


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
        "nodes": None,
    }


def _whole(stored: dict) -> dict:
    """*stored* with every key present and of the type the routes answer with."""
    url = str(stored["url"])
    entry = _entry(str(stored["id"]), str(stored.get("name") or default_name(url)), url)
    if stored.get("status") in ("connected", "offline"):
        entry["status"] = stored["status"]
    if isinstance(stored.get("gpu"), str):
        entry["gpu"] = stored["gpu"]
    if isinstance(stored.get("vram_mb"), int) and not isinstance(
        stored["vram_mb"], bool
    ):
        entry["vram_mb"] = stored["vram_mb"]
    if isinstance(stored.get("nodes"), bool):
        entry["nodes"] = stored["nodes"]
    return entry


def read(user) -> list[dict]:
    """The owner's connections, in the order they were added."""
    raw = getattr(user, "comfyui_connections", None)
    if raw is not None:
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, list):
            # Only entries every reader can use (the routes key rows by id),
            # each made whole, so a hand-edited or older row cannot fail the
            # response.
            return [
                _whole(e)
                for e in parsed
                if isinstance(e, dict) and e.get("url") and e.get("id")
            ]
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
        # Only a removed current connection hands over; with none current,
        # clearing turns ComfyUI off.
        if current is not None:
            entries.remove(current)
            new = entries[0]["url"] if entries else None
    elif any(_key(e["url"]) == _key(new) for e in entries):
        pass
    elif current is not None:
        if current["name"] == default_name(current["url"]):
            current["name"] = default_name(new)
        current.update(url=new, status=None, gpu=None, vram_mb=None, nodes=None)
    else:
        entries.append(_entry(secrets.token_hex(4), default_name(new), new))
    user.comfyui_url = new
    _store(user, entries)


def _listed(entries: list[dict], connection_id: str) -> dict:
    entry = next((e for e in entries if e["id"] == connection_id), None)
    if entry is None:
        raise LookupError(f"No ComfyUI connection {connection_id!r}.")
    return entry


def rename(user, connection_id: str, name: str) -> bool:
    """Name the connection *name*, trimmed; names need not be unique.

    Raises:
        LookupError: no connection has that id.
        ValueError: the name is blank or longer than 120 characters.
    """
    name = (name or "").strip()
    if not 1 <= len(name) <= 120:
        raise ValueError("A connection name is 1 to 120 characters.")
    entries = read(user)
    entry = _listed(entries, connection_id)
    if entry["name"] == name:
        return False
    entry["name"] = name
    _store(user, entries)
    return True


def set_address(user, connection_id: str, url: str) -> bool:
    """Move the connection to *url* (already normalised).

    What the old address's probe said is forgotten, a name made from the old
    address follows the new one, and ``comfyui_url`` follows a current row.
    The same address in another spelling changes nothing.

    Raises:
        LookupError: no connection has that id.
        ValueError: another connection already has *url*.
    """
    entries = read(user)
    entry = _listed(entries, connection_id)
    if _key(entry["url"]) == _key(url):
        return False
    if any(_key(e["url"]) == _key(url) for e in entries):
        raise ValueError(f"Another connection already has the address {url}.")
    if _key(user.comfyui_url) == _key(entry["url"]):
        user.comfyui_url = url
    if entry["name"] == default_name(entry["url"]):
        entry["name"] = default_name(url)
    entry.update(url=url, status=None, gpu=None, vram_mb=None, nodes=None)
    _store(user, entries)
    return True


def remove(user, connection_id: str) -> bool:
    """Drop the connection; a current one hands over as clearing the address does.

    Raises:
        LookupError: no connection has that id.
    """
    entries = read(user)
    entry = _listed(entries, connection_id)
    if _key(user.comfyui_url) == _key(entry["url"]):
        set_url(user, None)
    else:
        entries.remove(entry)
        _store(user, entries)
    return True


def record_probe(user, answer: dict) -> bool:
    """Note a probe's answer on the connection with its address.

    Returns whether anything changed, so an answer already noted writes nothing.
    """
    entries = read(user)
    seen = {
        "status": "connected" if answer.get("reachable") else "offline",
        "gpu": answer.get("gpu"),
        "vram_mb": answer.get("vram_mb"),
        "nodes": answer.get("can_open_workflows"),
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
