"""The library registry: create, attach, detach, list, and activate.

A *library* is what a vault already is: a folder holding ``vault.db`` and its
images. The registry records which of them this installation knows about and
which one is active. It never writes into a library it did not create, and it
never reads a library's identity tables: a folder copied in from elsewhere may
carry someone else's ``user``/``usertoken`` rows, and those must stay inert.

Every write here is a single short transaction (see
:class:`pixlstash.hub.db.HubDatabase`), and the two structural invariants —
one registration per path, at most one active library — are enforced by unique
indexes rather than by check-then-write logic, so a concurrent CLI and server
cannot interleave their way past them.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from pixlstash.hub.db import HubDatabase
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

VAULT_FILENAME = "vault.db"

# Tables every PixlStash vault has. ``alembic_version`` proves it went through
# our migration lineage; ``picture`` proves it is a vault rather than some other
# PixlStash-managed SQLite file. Checked read-only, without importing the ORM.
_VAULT_MARKER_TABLES = ("alembic_version", "picture")


class LibraryError(RuntimeError):
    """Base class for registry errors that carry a user-facing message."""


class NotAVaultError(LibraryError):
    """The folder does not contain a usable ``vault.db``."""


class LibraryExistsError(LibraryError):
    """The path or name is already registered."""


class LibraryNotFoundError(LibraryError):
    """No registered library matches the given name or id."""


class ActiveLibraryError(LibraryError):
    """The operation is refused because the library is the active one."""


@dataclass(frozen=True)
class Library:
    """One row of the registry, plus reachability resolved at read time."""

    id: int
    name: str
    path: str
    created_at: str
    attached_at: str
    is_active: bool
    notes: Optional[str] = None

    @property
    def vault_path(self) -> str:
        """Path of this library's ``vault.db``."""
        return os.path.join(self.path, VAULT_FILENAME)

    @property
    def is_reachable(self) -> bool:
        """True when the folder and its vault file are present right now.

        Resolved on every read rather than stored: an external drive is
        unplugged and replugged without anything telling the registry.
        """
        return os.path.isfile(self.vault_path)


def resolve_path(folder: str) -> str:
    """Return the absolute, symlink-resolved path used as a library's identity.

    Symlinks are resolved so that two registrations pointing at the same folder
    through different links collide on the unique index instead of quietly
    becoming two libraries over one vault.
    """
    return os.path.realpath(os.path.abspath(os.path.expanduser(folder)))


def validate_vault_folder(folder: str) -> str:
    """Check that *folder* holds a usable vault and return its ``vault.db`` path.

    Opened read-only and inspected through ``sqlite_master`` only: this must not
    migrate, write to, or otherwise touch a foreign vault, and it must not read
    its identity tables.

    Raises:
        NotAVaultError: No folder, no ``vault.db``, unreadable, or missing the
            marker tables.
    """
    if not os.path.isdir(folder):
        raise NotAVaultError(f"{folder} is not a folder.")

    vault_path = os.path.join(folder, VAULT_FILENAME)
    if not os.path.isfile(vault_path):
        raise NotAVaultError(
            f"No {VAULT_FILENAME} in {folder}. Pick the folder that contains "
            f"it, or use `create` to start an empty library."
        )

    try:
        conn = sqlite3.connect(f"file:{vault_path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        logger.error("Could not open vault %s read-only: %s", vault_path, exc)
        raise NotAVaultError(f"{vault_path} could not be opened: {exc}") from exc

    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    except sqlite3.Error as exc:
        logger.error("Could not read the schema of vault %s: %s", vault_path, exc)
        raise NotAVaultError(
            f"{vault_path} is not a readable SQLite database: {exc}"
        ) from exc
    finally:
        conn.close()

    tables = {row[0] for row in rows}
    missing = [table for table in _VAULT_MARKER_TABLES if table not in tables]
    if missing:
        raise NotAVaultError(
            f"{vault_path} does not look like a PixlStash vault (missing "
            f"{', '.join(missing)})."
        )

    return vault_path


class LibraryRegistry:
    """Registry operations over an open :class:`HubDatabase`."""

    def __init__(self, hub: HubDatabase):
        """Bind the registry to *hub*; the caller owns the hub's lifetime."""
        self._hub = hub

    def list_libraries(self) -> list[Library]:
        """Return every registered library, active first, then by name."""
        rows = self._hub.connection.execute(
            "SELECT id, name, path, created_at, attached_at, is_active, notes "
            "FROM library ORDER BY is_active DESC, name COLLATE NOCASE"
        ).fetchall()
        return [self._row_to_library(row) for row in rows]

    def active_library(self) -> Optional[Library]:
        """Return the active library, or None when the registry is empty."""
        row = self._hub.connection.execute(
            "SELECT id, name, path, created_at, attached_at, is_active, notes "
            "FROM library WHERE is_active = 1"
        ).fetchone()
        return self._row_to_library(row) if row else None

    def get(self, name_or_id: str | int) -> Library:
        """Return the library matching an id or an exact (case-insensitive) name.

        Raises:
            LibraryNotFoundError: Nothing matches, or a name matches more than
                one library (which the unique-name rule should prevent, but a
                hub edited by hand can still present it).
        """
        conn = self._hub.connection
        row = None
        if isinstance(name_or_id, int) or str(name_or_id).isdigit():
            row = conn.execute(
                "SELECT id, name, path, created_at, attached_at, is_active, "
                "notes FROM library WHERE id = ?",
                (int(name_or_id),),
            ).fetchone()

        if row is None:
            rows = conn.execute(
                "SELECT id, name, path, created_at, attached_at, is_active, "
                "notes FROM library WHERE name = ? COLLATE NOCASE",
                (str(name_or_id),),
            ).fetchall()
            if len(rows) > 1:
                raise LibraryNotFoundError(
                    f'"{name_or_id}" matches {len(rows)} libraries; use the id '
                    "from `list` instead."
                )
            row = rows[0] if rows else None

        if row is None:
            raise LibraryNotFoundError(f'No library named or numbered "{name_or_id}".')
        return self._row_to_library(row)

    def overlapping(self, path: str) -> list[Library]:
        """Return registered libraries that contain, or sit inside, *path*.

        Not an error: nested libraries are legal and sometimes deliberate. The
        caller warns, because two libraries sharing files will eventually fight
        over sidecars and deletes.
        """
        resolved = resolve_path(path)
        overlaps = []
        for library in self.list_libraries():
            if resolved == library.path:
                continue
            if _is_within(resolved, library.path) or _is_within(library.path, resolved):
                overlaps.append(library)
        return overlaps

    def attach(self, folder: str, name: str | None = None) -> Library:
        """Register an existing library folder.

        Validates that the folder holds a vault, then records it. Any
        ``user``/``user_token`` rows inside that vault are ignored, by never
        being read: a library copied in from elsewhere must not import
        somebody's credentials.

        Raises:
            NotAVaultError: The folder is not a vault.
            LibraryExistsError: The path or name is already registered.
        """
        resolved = resolve_path(folder)
        validate_vault_folder(resolved)
        return self._register(resolved, name or os.path.basename(resolved))

    def create(self, folder: str, name: str | None = None) -> Library:
        """Create a folder, initialise a fresh vault in it, and register it.

        The vault is built by the same code the server runs at startup, so a
        created library is indistinguishable from one the server made.

        Raises:
            LibraryExistsError: The path or name is already registered, or the
                folder already holds a vault (use ``attach``).
        """
        resolved = resolve_path(folder)
        vault_path = os.path.join(resolved, VAULT_FILENAME)
        if os.path.exists(vault_path):
            raise LibraryExistsError(
                f"{resolved} already contains a {VAULT_FILENAME}. Use `attach` "
                "to register it."
            )

        os.makedirs(resolved, exist_ok=True)

        # Local import: pulls in the ORM and the image stack (numpy, PIL), which
        # `list`, `attach` and `detach` have no use for. Importing it at module
        # scope would make every CLI invocation pay for the one verb that needs
        # it. Sanctioned by CLAUDE.md's startup-time exception.
        from pixlstash.database import VaultDatabase

        logger.info("Initialising a new vault at %s", vault_path)
        vault = VaultDatabase(vault_path)
        try:
            registered = self._register(resolved, name or os.path.basename(resolved))
        finally:
            vault.close()
        return registered

    def detach(self, name_or_id: str | int) -> Library:
        """Deregister a library. Files are never touched.

        Raises:
            ActiveLibraryError: It is the active library.
            LibraryNotFoundError: Nothing matches.
        """
        library = self.get(name_or_id)
        if library.is_active:
            raise ActiveLibraryError(
                f'Cannot detach "{library.name}": it is the active library.\n'
                "Switch to another library in Settings (or, with the server "
                "stopped, change the active library), then detach. No files "
                "have been changed."
            )
        with self._hub.transaction() as conn:
            conn.execute("DELETE FROM library WHERE id = ?", (library.id,))
        logger.info(
            "Detached library %s (id=%d); files at %s untouched",
            library.name,
            library.id,
            library.path,
        )
        return library

    def set_active(self, name_or_id: str | int) -> Library:
        """Mark a library active and every other inactive, atomically.

        Both statements share one transaction because the partial unique index
        forbids a moment with two active rows; clearing first and setting second
        inside the same transaction is the only ordering that satisfies it.
        """
        library = self.get(name_or_id)
        with self._hub.transaction() as conn:
            conn.execute("UPDATE library SET is_active = 0 WHERE is_active = 1")
            conn.execute("UPDATE library SET is_active = 1 WHERE id = ?", (library.id,))
        logger.info("Active library is now %s (id=%d)", library.name, library.id)
        return self.get(library.id)

    def rename(self, name_or_id: str | int, new_name: str) -> Library:
        """Change a library's label.

        Raises:
            LibraryExistsError: Another library already has that name.
        """
        library = self.get(name_or_id)
        cleaned = new_name.strip()
        if not cleaned:
            raise LibraryError("A library name cannot be empty.")
        try:
            with self._hub.transaction() as conn:
                conn.execute(
                    "UPDATE library SET name = ? WHERE id = ?", (cleaned, library.id)
                )
        except sqlite3.IntegrityError as exc:
            raise LibraryExistsError(
                f'Another library is already named "{cleaned}".'
            ) from exc
        return self.get(library.id)

    def _register(self, resolved_path: str, name: str) -> Library:
        """Insert a registry row, activating it when it is the only library."""
        existing = self._find_by_path(resolved_path)
        if existing is not None:
            raise LibraryExistsError(
                f'{resolved_path} is already registered as "{existing.name}".'
            )

        cleaned = name.strip() or os.path.basename(resolved_path)
        now = datetime.now(timezone.utc).isoformat()
        first_library = not self.list_libraries()

        try:
            with self._hub.transaction() as conn:
                cursor = conn.execute(
                    "INSERT INTO library (name, path, created_at, attached_at, "
                    "is_active) VALUES (?, ?, ?, ?, ?)",
                    (cleaned, resolved_path, now, now, 1 if first_library else 0),
                )
                library_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            # The unique indexes, not a check-then-write race: another process
            # may have registered this path or name between the check above and
            # here.
            logger.warning(
                "Registering %s as %s violated a hub uniqueness constraint: %s",
                resolved_path,
                cleaned,
                exc,
            )
            raise LibraryExistsError(
                f'Could not register {resolved_path} as "{cleaned}": the path '
                "or name is already in the registry."
            ) from exc

        logger.info(
            "Registered library %s (id=%d) at %s%s",
            cleaned,
            library_id,
            resolved_path,
            " and made it active (first library)" if first_library else "",
        )
        return self.get(library_id)

    def _find_by_path(self, resolved_path: str) -> Optional[Library]:
        """Return the library registered at *resolved_path*, if any."""
        row = self._hub.connection.execute(
            "SELECT id, name, path, created_at, attached_at, is_active, notes "
            "FROM library WHERE path = ?",
            (resolved_path,),
        ).fetchone()
        return self._row_to_library(row) if row else None

    @staticmethod
    def _row_to_library(row: sqlite3.Row) -> Library:
        """Map a registry row onto :class:`Library`."""
        return Library(
            id=int(row["id"]),
            name=row["name"],
            path=row["path"],
            created_at=row["created_at"],
            attached_at=row["attached_at"],
            is_active=bool(row["is_active"]),
            notes=row["notes"],
        )


def _is_within(inner: str, outer: str) -> bool:
    """True when *inner* is *outer* itself or a path below it."""
    try:
        return os.path.commonpath([inner, outer]) == outer
    except ValueError:
        # Different drives on Windows: commonpath raises rather than returning
        # a sentinel, and unrelated drives cannot overlap.
        return False
