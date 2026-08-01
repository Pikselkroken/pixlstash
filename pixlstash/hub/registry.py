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
import uuid as uuid_module
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from pixlstash.hub.db import HubDatabase
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

VAULT_FILENAME = "vault.db"

# Every registry read selects the same columns, in the order
# :meth:`LibraryRegistry._row_to_library` expects.
_LIBRARY_COLUMNS = (
    "id, uuid, vault_uuid, name, path, created_at, attached_at, detached_at, "
    "attached, is_active, notes"
)

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


def new_library_uuid() -> str:
    """Return a fresh identity for a library.

    A random (version 4) UUID, minted here and never taken from a vault. See
    the note on the ``library`` table in :mod:`pixlstash.hub.schema` for why an
    integer id cannot serve this purpose.
    """
    return str(uuid_module.uuid4())


@dataclass(frozen=True)
class Library:
    """One row of the registry, plus reachability resolved at read time."""

    id: int
    uuid: str
    name: str
    path: str
    created_at: str
    attached_at: str
    is_active: bool
    vault_uuid: Optional[str] = None
    attached: bool = True
    detached_at: Optional[str] = None
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


def read_vault_uuid(folder: str) -> Optional[str]:
    """Return the fingerprint a library carries, or None if it has none.

    Read from the vault's ``library_settings`` row, read-only, and treated as a
    fingerprint rather than an identity: it decides whether re-attaching a path
    revives the previous registration, and it is never referenced by a token and
    never trusted for authorization. A library folder can arrive from anyone, so
    a value found here must not be able to claim an identity that tokens on this
    machine are already stamped with.

    Returns None for a vault written before fingerprints existed, or one whose
    ``library_settings`` table is absent, which the caller treats as "cannot
    tell" rather than as a mismatch.
    """
    vault_path = os.path.join(folder, VAULT_FILENAME)
    if not os.path.isfile(vault_path):
        return None
    try:
        conn = sqlite3.connect(f"file:{vault_path}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        logger.warning("Could not read the fingerprint of %s: %s", vault_path, exc)
        return None

    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "library_settings" not in tables:
            return None
        row = conn.execute("SELECT library_uuid FROM library_settings").fetchone()
        return row[0] if row and row[0] else None
    except sqlite3.Error as exc:
        logger.warning("Could not read library_settings from %s: %s", vault_path, exc)
        return None
    finally:
        conn.close()


def _fingerprints_match(recorded: Optional[str], observed: Optional[str]) -> bool:
    """True when a re-attached folder is provably the library we saw before.

    Both absent means neither the row nor the folder carries a fingerprint (a
    library from before this existed), and path is the only evidence available,
    so the previous behaviour stands. One absent and one present means the
    folder changed in a way we cannot vouch for, and a mismatch is decisive.
    """
    if recorded is None and observed is None:
        return True
    return recorded is not None and recorded == observed


class LibraryRegistry:
    """Registry operations over an open :class:`HubDatabase`."""

    def __init__(self, hub: HubDatabase):
        """Bind the registry to *hub*; the caller owns the hub's lifetime."""
        self._hub = hub

    def list_libraries(self, *, include_detached: bool = False) -> list[Library]:
        """Return the attached libraries, active first, then by name.

        Args:
            include_detached: Also return rows kept only so their uuid and
                tokens survive a detach. Off by default: a detached library is
                not part of this installation as far as the UI and the CLI are
                concerned.
        """
        where = "" if include_detached else "WHERE attached = 1 "
        rows = self._hub.fetchall(
            f"SELECT {_LIBRARY_COLUMNS} FROM library {where}"
            "ORDER BY is_active DESC, name COLLATE NOCASE"
        )
        return [self._row_to_library(row) for row in rows]

    def active_library(self) -> Optional[Library]:
        """Return the active library, or None when the registry is empty."""
        row = self._hub.fetchone(
            f"SELECT {_LIBRARY_COLUMNS} FROM library WHERE is_active = 1"
        )
        return self._row_to_library(row) if row else None

    def by_uuid(self, library_uuid: str) -> Optional[Library]:
        """Return the library with this uuid, attached or not, else None.

        The lookup every caller outside the hub should use: a uuid names one
        library for the life of the installation, where an integer id does not.
        """
        row = self._hub.fetchone(
            f"SELECT {_LIBRARY_COLUMNS} FROM library WHERE uuid = ?",
            (library_uuid,),
        )
        return self._row_to_library(row) if row else None

    def get(self, name_or_id: str | int) -> Library:
        """Return the library matching an id or an exact (case-insensitive) name.

        Raises:
            LibraryNotFoundError: Nothing matches, or a name matches more than
                one library (which the unique-name rule should prevent, but a
                hub edited by hand can still present it).
        """
        row = None
        if isinstance(name_or_id, int) or str(name_or_id).isdigit():
            row = self._hub.fetchone(
                f"SELECT {_LIBRARY_COLUMNS} FROM library WHERE id = ?",
                (int(name_or_id),),
            )

        if row is None:
            row = self._hub.fetchone(
                f"SELECT {_LIBRARY_COLUMNS} FROM library WHERE uuid = ?",
                (str(name_or_id),),
            )

        if row is None:
            rows = self._hub.fetchall(
                f"SELECT {_LIBRARY_COLUMNS} FROM library "
                "WHERE name = ? COLLATE NOCASE AND attached = 1",
                (str(name_or_id),),
            )
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

    def register_pending(self, folder: str, name: str | None = None) -> Library:
        """Register a folder whose vault does not exist yet.

        Startup-only. On a fresh install the server is about to create the vault
        moments later, so requiring one here would be a chicken-and-egg failure.
        Everything else goes through :meth:`attach` or :meth:`create`, both of
        which insist on a real vault.
        """
        resolved = resolve_path(folder)
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
        """Deregister a library. Files and share links are never destroyed.

        Clears the ``attached`` flag rather than deleting the row, so the
        library's uuid and every token stamped with it survive. A detached
        library cannot be active, so those tokens are inert until the same
        folder is attached again, at which point they work once more. Deleting
        the row instead would silently revoke share links the owner had handed
        out, from a verb documented as "no files are removed".

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
            conn.execute(
                "UPDATE library SET attached = 0, detached_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), library.id),
            )
        logger.info(
            "Detached library %s (uuid=%s); files at %s untouched, %d token(s) "
            "kept and inert",
            library.name,
            library.uuid,
            library.path,
            self._token_count(library.uuid),
        )
        return self.by_uuid(library.uuid)

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

    def relocate(self, name_or_id: str | int, new_folder: str) -> Library:
        """Point an existing library at a folder that has moved.

        The registration keeps its uuid, so every token stamped with it keeps
        working. This is the supported way to move a library: detaching and
        attaching at the new path would mint a new identity and leave the old
        share links inert.

        Raises:
            NotAVaultError: The new folder does not hold a vault.
            LibraryExistsError: Another library is registered at that path.
        """
        library = self.get(name_or_id)
        resolved = resolve_path(new_folder)
        validate_vault_folder(resolved)

        clash = self._find_by_path(resolved)
        if clash is not None and clash.id != library.id:
            raise LibraryExistsError(
                f'{resolved} is already registered as "{clash.name}".'
            )

        fingerprint = read_vault_uuid(resolved)
        if not _fingerprints_match(library.vault_uuid, fingerprint):
            logger.warning(
                "Relocating %s to %s, where the library carries a different "
                "fingerprint (%s, expected %s). Proceeding because the move was "
                "explicit, but the tokens stamped for this library will now "
                "serve the content at the new path.",
                library.name,
                resolved,
                fingerprint,
                library.vault_uuid,
            )

        with self._hub.transaction() as conn:
            conn.execute(
                "UPDATE library SET path = ?, vault_uuid = COALESCE(?, vault_uuid) "
                "WHERE id = ?",
                (resolved, fingerprint, library.id),
            )
        logger.info(
            "Library %s (uuid=%s) moved from %s to %s",
            library.name,
            library.uuid,
            library.path,
            resolved,
        )
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
        """Register a library, reviving a previously detached row when it fits.

        A row kept by :meth:`detach` is revived only when the folder now at that
        path is provably the *same* library: its vault fingerprint matches the
        one recorded, or neither has one (a library that predates fingerprints).
        Otherwise the old row is left detached and a new identity is minted, so
        share links can never come back pointing at content they were not issued
        for.
        """
        cleaned = name.strip() or os.path.basename(resolved_path)
        fingerprint = read_vault_uuid(resolved_path)
        existing = self._find_by_path(resolved_path)

        if existing is not None and existing.attached:
            raise LibraryExistsError(
                f'{resolved_path} is already registered as "{existing.name}".'
            )

        if existing is not None:
            if _fingerprints_match(existing.vault_uuid, fingerprint):
                return self._revive(existing, cleaned, fingerprint)
            logger.warning(
                "A different library now sits at %s (fingerprint %s, expected "
                "%s). Registering it as new; the detached library keeps its "
                "identity and its tokens stay inert.",
                resolved_path,
                fingerprint,
                existing.vault_uuid,
            )
            # Free the path for the new row: the old one keeps its uuid (and so
            # its tokens), but a path is unique in the registry.
            with self._hub.transaction() as conn:
                conn.execute(
                    "UPDATE library SET path = ? WHERE id = ?",
                    (f"{existing.path}#detached-{existing.uuid}", existing.id),
                )

        now = datetime.now(timezone.utc).isoformat()
        first_library = not self.list_libraries()
        library_uuid = self._mint_uuid(resolved_path, now)

        try:
            with self._hub.transaction() as conn:
                cursor = conn.execute(
                    "INSERT INTO library (uuid, vault_uuid, name, path, "
                    "created_at, attached_at, is_active) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        library_uuid,
                        fingerprint,
                        cleaned,
                        resolved_path,
                        now,
                        now,
                        1 if first_library else 0,
                    ),
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
            "Registered library %s (uuid=%s) at %s%s",
            cleaned,
            library_uuid,
            resolved_path,
            " and made it active (first library)" if first_library else "",
        )
        return self.get(library_id)

    def _mint_uuid(self, resolved_path: str, now: str) -> str:
        """Return a never-before-issued library uuid, recording it in the ledger.

        The ledger is the structural half of "a library uuid is never reused":
        uniqueness on ``library.uuid`` stops two *live* rows sharing one, and
        this stops a uuid being re-issued after its row is gone. uuid4 makes a
        natural collision vanishingly unlikely; this makes a deliberate or
        accidental re-issue impossible rather than improbable.
        """
        while True:
            candidate = new_library_uuid()
            try:
                with self._hub.transaction() as conn:
                    conn.execute(
                        "INSERT INTO library_uuid_issued (uuid, issued_at, "
                        "first_path) VALUES (?, ?, ?)",
                        (candidate, now, resolved_path),
                    )
                return candidate
            except sqlite3.IntegrityError:
                # Already issued at some point in this hub's history. Astronomically
                # unlikely from uuid4, so log it: in practice this means a restored
                # or hand-edited ledger, which is worth knowing about.
                logger.warning(
                    "Library uuid %s has been issued before; minting another.",
                    candidate,
                )

    def _revive(
        self, existing: Library, name: str, fingerprint: Optional[str]
    ) -> Library:
        """Re-attach a detached row, keeping its uuid and its tokens."""
        now = datetime.now(timezone.utc).isoformat()
        with self._hub.transaction() as conn:
            conn.execute(
                "UPDATE library SET attached = 1, detached_at = NULL, "
                "attached_at = ?, name = ?, vault_uuid = COALESCE(?, vault_uuid) "
                "WHERE id = ?",
                (now, name, fingerprint, existing.id),
            )
        logger.info(
            "Re-attached library %s (uuid=%s) at %s; %d token(s) are live again",
            name,
            existing.uuid,
            existing.path,
            self._token_count(existing.uuid),
        )
        return self.get(existing.id)

    def _token_count(self, library_uuid: str) -> int:
        """Return how many tokens are stamped with this library."""
        row = self._hub.fetchone(
            "SELECT COUNT(*) FROM usertoken WHERE library_uuid = ?",
            (library_uuid,),
        )
        return int(row[0]) if row else 0

    def _find_by_path(self, resolved_path: str) -> Optional[Library]:
        """Return the library registered at *resolved_path*, if any."""
        row = self._hub.fetchone(
            f"SELECT {_LIBRARY_COLUMNS} FROM library WHERE path = ?",
            (resolved_path,),
        )
        return self._row_to_library(row) if row else None

    @staticmethod
    def _row_to_library(row: sqlite3.Row) -> Library:
        """Map a registry row onto :class:`Library`."""
        return Library(
            id=int(row["id"]),
            uuid=row["uuid"],
            vault_uuid=row["vault_uuid"],
            name=row["name"],
            path=row["path"],
            created_at=row["created_at"],
            attached_at=row["attached_at"],
            detached_at=row["detached_at"],
            attached=bool(row["attached"]),
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
