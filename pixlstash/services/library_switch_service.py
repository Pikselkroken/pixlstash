"""Switching the active library on a running server.

The riskiest code in the multi-library lane, because it replaces a live vault
underneath a process that was built assuming one vault per lifetime.

**Construct, then swap.** The new vault is opened *before* the old one is
closed. Opening is where the failures live: a missing folder, a corrupt
database, an Alembic migration that will not apply. If any of them happen, the
old vault is still open and serving, so the session stays exactly where it was.
Closing first would mean a failed open leaves the server with no vault at all,
which is the "blank grid" outcome the plan explicitly rules out (§3.3 step 4).

Two vaults are briefly open at once. That is safe (separate files, separate
engines, separate writer threads) and cheap, because a vault loads no models
until it is started, and the new one is started only after the swap.

**Requests during the swap are refused, not served.** The window is short but
real, and a request served against a half-swapped server would read from one
library and write to another. ``SWITCHING`` is a typed state and the gate turns
it into 503, so a client sees "come back in a second" rather than a 500 or, far
worse, a plausible answer from the wrong library.
"""

from __future__ import annotations

import os
import pathlib
import threading
from enum import Enum
from typing import TYPE_CHECKING, Optional

from pixlstash.hub.registry import (
    Library,
    LibraryError,
    LibraryNotFoundError,
    resolve_path,
    validate_vault_folder,
)
from pixlstash.pixl_logging import get_logger

if TYPE_CHECKING:
    from pixlstash.server import Server

logger = get_logger(__name__)

# Where the vault's Alembic revisions live. Used to answer "is this vault newer
# than this build?" without opening it for migration.
_MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parent.parent / "migrations/versions"


class SwitchState(str, Enum):
    """What the server is currently able to serve."""

    READY = "ready"
    """A vault is open and requests are served normally."""

    SWITCHING = "switching"
    """Mid-swap. Data requests are refused with 503 until this clears."""


class LibrarySwitchError(LibraryError):
    """A switch was refused or failed, with the session left on its old library."""


def known_vault_revisions() -> set[str]:
    """Return every Alembic revision id this build understands."""
    return {
        path.stem
        for path in _MIGRATIONS_DIR.glob("*.py")
        if not path.stem.startswith("__")
    }


def assert_vault_not_newer(vault_path: str) -> None:
    """Refuse a vault whose schema this build has never heard of.

    Opening a newer vault would run *this* build's migrations against a database
    written by a later one, and Alembic's usual answer to an unknown head is not
    a clean refusal. Checking first turns a potential corruption into a message.

    A vault with no ``alembic_version`` row is accepted: that is a brand-new
    database about to be initialised, not a future one.

    Raises:
        LibrarySwitchError: The vault names a revision this build does not have.
    """
    import sqlite3

    try:
        conn = sqlite3.connect(f"file:{vault_path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        raise LibrarySwitchError(f"{vault_path} could not be opened: {exc}") from exc

    try:
        rows = conn.execute("SELECT version_num FROM alembic_version").fetchall()
    except sqlite3.Error:
        # No alembic_version table: nothing to compare against.
        return
    finally:
        conn.close()

    known = known_vault_revisions()
    unknown = [row[0] for row in rows if row[0] and row[0] not in known]
    if unknown:
        raise LibrarySwitchError(
            f"This library was last opened by a newer version of PixlStash "
            f"(database revision {', '.join(unknown)}). Update PixlStash before "
            "switching to it. Nothing has been changed."
        )


class LibrarySwitchService:
    """Owns the active-library swap and the state it passes through."""

    def __init__(self, server: "Server"):
        """Bind the service to *server*, whose vault it will replace."""
        self._server = server
        self._state = SwitchState.READY
        # Serialises switches against each other. Two concurrent switches would
        # race to close the same vault.
        self._lock = threading.Lock()

    @property
    def state(self) -> SwitchState:
        """What the server can currently serve."""
        return self._state

    @property
    def is_switching(self) -> bool:
        """True while a swap is in flight."""
        return self._state is SwitchState.SWITCHING

    def switch_to(self, library_uuid: str) -> Library:
        """Make the library with *library_uuid* the active one.

        Args:
            library_uuid: The library's stable identity. Deliberately not its
                row id: a client that has been open across a detach and attach
                would otherwise switch to whatever now holds that number.

        Returns:
            The now-active library.

        Raises:
            LibraryNotFoundError: No such library, or it is detached.
            LibrarySwitchError: The library is unreachable, is not a vault, is
                newer than this build, or failed to open. The session is left on
                the library it was already using.
        """
        registry = self._server.library_registry
        target = registry.by_uuid(library_uuid)
        if target is None or not target.attached:
            raise LibraryNotFoundError(f"No attached library with id {library_uuid}.")
        if target.is_active:
            logger.info("Library %s is already active; nothing to do", target.name)
            return target

        with self._lock:
            return self._swap(target)

    def _swap(self, target: Library) -> Library:
        """Open the target, then retire the current vault. Never the reverse."""
        resolved = self._revalidate(target)

        previous = self._server.vault
        self._state = SwitchState.SWITCHING
        logger.info(
            "Switching library: %s -> %s (%s)",
            previous.image_root,
            target.name,
            resolved,
        )

        try:
            incoming = self._server.build_vault(resolved)
        except Exception as exc:
            # Nothing has been closed yet, so the session simply carries on.
            self._state = SwitchState.READY
            logger.exception("Could not open library %s; staying put", target.name)
            raise LibrarySwitchError(
                f'Could not open "{target.name}": {exc}. PixlStash is still using '
                f"the library it was already on, and nothing has been changed."
            ) from exc

        try:
            # Past this point the old vault is going away, so failures are
            # logged and pressed through rather than raised: leaving the server
            # with a closed old vault and an unused new one would be the worst
            # of both.
            previous.close()

            self._server.vault = incoming
            incoming.add_event_listener(self._server.handle_vault_event)
            incoming.auth_service = self._server.auth
            # Guest sessions are per-library, so the auth service has to follow
            # the vault (see AuthService.vault_db).
            self._server.auth.vault_db = incoming.db
            self._server.apply_user_settings_to_vault(incoming)
            incoming.start()

            self._server.library_registry.set_active(target.id)
        finally:
            self._state = SwitchState.READY

        active = self._server.library_registry.by_uuid(target.uuid)
        logger.info("Active library is now %s (%s)", active.name, active.path)
        return active

    def _revalidate(self, target: Library) -> str:
        """Re-check the registered path immediately before opening it.

        ``attach`` validated this folder once, possibly weeks ago. Between then
        and now a drive can have been unplugged, a network share remounted, or a
        symlink repointed, and this is the moment a SQLite file gets opened with
        write authority and migrated. Cheap check, one real failure mode.
        """
        resolved = resolve_path(target.path)
        if not os.path.isdir(resolved):
            raise LibrarySwitchError(
                f'"{target.name}" is not where PixlStash left it ({resolved}). '
                "Reconnect the drive, or point the library at its new location, "
                "then try again."
            )
        try:
            vault_path = validate_vault_folder(resolved)
        except LibraryError as exc:
            raise LibrarySwitchError(
                f'"{target.name}" no longer looks like a library: {exc}'
            ) from exc

        assert_vault_not_newer(vault_path)
        return resolved


def switching_state_of(server: Optional["Server"]) -> SwitchState:
    """Return a server's switch state, defaulting to READY when it has none."""
    service = getattr(server, "library_switch", None)
    return service.state if service is not None else SwitchState.READY
