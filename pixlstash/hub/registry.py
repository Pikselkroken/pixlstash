"""The library registry: create, attach, detach, list, and activate.

A *library* is what a vault already is: a folder holding ``vault.db`` and its
images. The registry records which of them this installation knows about and
which one is active. It never writes into a library it did not create, and it
never reads a library's identity tables: a folder copied in from elsewhere may
carry someone else's ``user``/``usertoken`` rows, and those must stay inert.

Every write here is a single short transaction (see
:class:`pixlstash.hub.db.HubDatabase`), and the two structural invariants -
one registration per path, at most one active library - are enforced by unique
indexes rather than by check-then-write logic, so a concurrent CLI and server
cannot interleave their way past them.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import uuid as uuid_module
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pixlstash.hub.db import HubDatabase
from pixlstash.startup_permissions import mkdir_private
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

VAULT_FILENAME = "vault.db"

# Every registry read selects the same columns, in the order
# :meth:`LibraryRegistry._row_to_library` expects.
_LIBRARY_COLUMNS = (
    "id, uuid, vault_uuid, settings_salt, identity_migration_state, name, path, created_at, attached_at, "
    "detached_at, attached, is_active, notes"
)

# Tables every PixlStash vault has. ``alembic_version`` proves it went through
# our migration lineage; ``picture`` proves it is a vault rather than some other
# PixlStash-managed SQLite file. Checked read-only, without importing the ORM.
_VAULT_MARKER_TABLES = ("alembic_version", "picture")

# A vault written before PixlStash adopted Alembic has no ``alembic_version``,
# so the lineage has to be recognised from the schema itself. This is the
# ``0001_baseline`` table set minus the tables a later migration added, and
# ``VaultDatabase`` already knows what to do with such a file: it stamps the
# baseline and upgrades it to head (see ``database.py``'s "Existing database
# without Alembic version table" branch). Refusing it here would make that
# branch unreachable for every library the hub owns, which is what turned an
# upgradable December-2025 vault into a backend that exited during first-run
# setup. The set is deliberately wide: one stray table named ``picture`` must
# still not be mistaken for a library.
_LEGACY_VAULT_MARKER_TABLES = (
    "picture",
    "character",
    "face",
    "tag",
    "quality",
    "metadata",
    "pictureset",
)


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
    settings_salt: Optional[str] = None
    identity_migration_state: str = "not_required"
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

    "Usable" means a vault ``VaultDatabase`` can open, which includes a
    pre-Alembic one it will stamp and upgrade - see
    :data:`_LEGACY_VAULT_MARKER_TABLES`.

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
        legacy_missing = [
            table for table in _LEGACY_VAULT_MARKER_TABLES if table not in tables
        ]
        if legacy_missing:
            # Both sets, not just the modern one. A database holding nothing
            # but a `picture` table is missing only `alembic_version` by the
            # modern reckoning, and saying so alone reads as "an old vault we
            # could upgrade" - the opposite of the truth. The reason is
            # surfaced verbatim by the recovery dialog, so it has to carry
            # which of the two it failed to be.
            raise NotAVaultError(
                f"{vault_path} does not look like a PixlStash vault (missing "
                f"{', '.join(missing)}), and not a pre-Alembic one either "
                f"(missing {', '.join(legacy_missing)})."
            )
        logger.info(
            "%s is a PixlStash vault from before Alembic (no alembic_version); "
            "it will be stamped at the baseline and upgraded when it is opened.",
            vault_path,
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

    @property
    def hub_path(self) -> str:
        """Filesystem path of the hub this registry reads."""
        return self._hub.path

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

    def _refuse_duplicate_name(
        self,
        cleaned: str,
        *,
        except_id: int | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        """Refuse a name another attached library already answers to.

        ``library.uuid`` and ``library.path`` carry unique indexes; ``name`` does
        not, so the ``sqlite3.IntegrityError`` that :meth:`rename` and
        :meth:`_register` catch can never fire for a name and both documented
        their ``LibraryExistsError`` for one anyway. Two libraries sharing a name
        is not cosmetic: :meth:`get` refuses a name that matches more than one
        row, so every CLI verb that takes a name stops working for both of them
        the moment the second is registered.

        Checked here rather than at each caller so the GUI and the CLI refuse
        alike, and left as a check rather than a new unique index because a hub
        written before this could already hold a duplicate, and a migration that
        cannot build its index fails a startup instead of a rename.

        Args:
            cleaned: The stripped name about to be written.
            except_id: A row allowed to hold the name already - the one being
                renamed, or revived into it.
            conn: An open transaction to read through.
                :meth:`~pixlstash.hub.db.HubDatabase.transaction` opens
                ``BEGIN IMMEDIATE``, so a check issued on the same connection as
                the write that follows is atomic against another process, where
                the same check on its own connection is check-then-write and two
                concurrent adds of one name both pass. Every caller that is
                about to write passes it. The exception is :meth:`create`'s early
                call, which exists to fail *before* a vault is built and is
                deliberately advisory - the authoritative check runs later,
                inside :meth:`_register`'s own transaction.

        Raises:
            LibraryExistsError: Another attached library has that name.
        """
        sql = "SELECT id FROM library WHERE name = ? COLLATE NOCASE AND attached = 1"
        rows = (
            conn.execute(sql, (cleaned,)).fetchall()
            if conn is not None
            else self._hub.fetchall(sql, (cleaned,))
        )
        if any(int(row[0]) != except_id for row in rows):
            raise LibraryExistsError(f'Another library is already named "{cleaned}".')

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

    def register_pending(
        self,
        folder: str,
        name: str | None = None,
    ) -> Library:
        """Register a folder whose vault does not exist yet.

        Startup-only. On a fresh install the server is about to create the vault
        moments later, so requiring one here would be a chicken-and-egg failure.
        Everything else goes through :meth:`attach` or :meth:`create`, both of
        which insist on a real vault.
        """
        resolved = resolve_path(folder)
        return self._register(
            resolved,
            name or os.path.basename(resolved),
            # Start-up must not die on a name. `bootstrap._register_first_library`
            # passes the hardcoded "Library 1" and does not catch
            # LibraryExistsError, so refusing here would turn a duplicate label -
            # a nuisance - into a server that will not boot.
            unique_name=False,
        )

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

        # Before anything is written. `_register` would refuse the name at the
        # end anyway, but by then the vault exists, and a refused `create` that
        # leaves a vault behind turns the folder into an `attach` case the owner
        # never asked for.
        cleaned = (name or "").strip() or os.path.basename(resolved)
        self._refuse_duplicate_name(cleaned)

        # Every MISSING component 0700, not only the leaf (W21: makedirs'
        # mode stops at the leaf, so a deep new path left 0775 intermediates
        # under umask 002 and the guarded open refused them). Existing
        # directories keep their modes.
        mkdir_private(Path(resolved))
        if os.name != "nt":
            os.chmod(resolved, 0o700)

        # Local import: pulls in the ORM and the image stack (numpy, PIL), which
        # `list`, `attach` and `detach` have no use for. Importing it at module
        # scope would make every CLI invocation pay for the one verb that needs
        # it. Sanctioned by CLAUDE.md's startup-time exception.
        from pixlstash.database import VaultDatabase

        logger.info("Initialising a new vault at %s", vault_path)
        vault = VaultDatabase(vault_path)
        try:
            registered = self._register(resolved, cleaned)
        finally:
            vault.close()
        return registered

    def record_legacy_preparation(
        self, resolved_path: str, payload_digest: str, name: str = "Library 1"
    ) -> Library:
        """Atomically register a legacy vault and its explicit migration intent.

        This is the sole registry path that may create ``pending`` identity
        migration state. The owner recheck, optional registry insertion, and
        operation insertion are one serialization point across CLI and server
        processes, because :meth:`~pixlstash.hub.db.HubDatabase.transaction`
        opens with ``BEGIN IMMEDIATE``. This method used to issue that itself,
        which was correct and is now redundant: the guarantee moved into
        ``transaction()`` so that every caller gets it rather than the three
        that remembered to ask.
        """
        cleaned = name.strip() or os.path.basename(resolved_path)
        fingerprint = read_vault_uuid(resolved_path)
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self._hub.transaction() as conn:
                row = conn.execute(
                    f"SELECT {_LIBRARY_COLUMNS} FROM library WHERE path = ?",
                    (resolved_path,),
                ).fetchone()
                library = self._row_to_library(row) if row is not None else None
                if library is not None:
                    operation = conn.execute(
                        "SELECT state FROM identity_migration_operation "
                        "WHERE library_uuid = ?",
                        (library.uuid,),
                    ).fetchone()
                    if operation is not None and operation[0] != "pending":
                        raise LibraryError(
                            f"Legacy migration is already {operation[0]}; "
                            "refusing to reauthorize it."
                        )

                if conn.execute("SELECT COUNT(*) FROM user").fetchone()[0]:
                    raise LibraryError(
                        "The hub already has an owner; refusing legacy import."
                    )

                if library is None:
                    library_uuid = new_library_uuid()
                    conn.execute(
                        "INSERT INTO library_uuid_issued "
                        "(uuid, issued_at, first_path) VALUES (?, ?, ?)",
                        (library_uuid, now, resolved_path),
                    )
                    first_library = (
                        conn.execute(
                            "SELECT COUNT(*) FROM library WHERE attached = 1"
                        ).fetchone()[0]
                        == 0
                    )
                    cursor = conn.execute(
                        "INSERT INTO library (uuid, vault_uuid, settings_salt, "
                        "identity_migration_state, name, path, created_at, "
                        "attached_at, is_active) VALUES (?, ?, ?, 'pending', ?, "
                        "?, ?, ?, ?)",
                        (
                            library_uuid,
                            fingerprint,
                            secrets.token_hex(16),
                            cleaned,
                            resolved_path,
                            now,
                            now,
                            1 if first_library else 0,
                        ),
                    )
                    library_id = int(cursor.lastrowid)
                else:
                    library_id = library.id
                    library_uuid = library.uuid
                    conn.execute(
                        "UPDATE library SET identity_migration_state='pending' "
                        "WHERE id=?",
                        (library_id,),
                    )

                conn.execute(
                    "INSERT INTO identity_migration_operation "
                    "(library_uuid, source_path, payload_digest, state) "
                    "VALUES (?, ?, ?, 'pending') "
                    "ON CONFLICT(library_uuid) DO UPDATE SET "
                    "source_path=excluded.source_path, "
                    "payload_digest=excluded.payload_digest",
                    (library_uuid, resolved_path, payload_digest),
                )
        except sqlite3.IntegrityError as exc:
            raise LibraryExistsError(
                f'Could not register {resolved_path} as "{cleaned}": the path '
                "or name is already in the registry."
            ) from exc
        return self.get(library_id)

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
                self._refuse_duplicate_name(cleaned, except_id=library.id, conn=conn)
                conn.execute(
                    "UPDATE library SET name = ? WHERE id = ?", (cleaned, library.id)
                )
        except sqlite3.IntegrityError as exc:
            raise LibraryExistsError(
                f'Another library is already named "{cleaned}".'
            ) from exc
        return self.get(library.id)

    def _register(
        self,
        resolved_path: str,
        name: str,
        *,
        identity_migration_state: str = "not_required",
        recovered_uuid: str | None = None,
        unique_name: bool = True,
    ) -> Library:
        """Register a library, reviving a previously detached row when it fits.

        A row kept by :meth:`detach` is revived only when the folder now at that
        path is provably the *same* library: its vault fingerprint matches the
        one recorded, or neither has one (a library that predates fingerprints).
        Otherwise the old row is left detached and a new identity is minted, so
        share links can never come back pointing at content they were not issued
        for.

        Args:
            unique_name: Refuse a name another attached library holds. True for
                every verb a person types a name at. False only for
                :meth:`register_pending`, whose caller is start-up: a duplicate
                name is a nuisance there and a failed boot is not, so the
                start-up path records what it was given.
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
                return self._revive(existing, cleaned, fingerprint, unique_name)
            # Before the UPDATE below, not after. That UPDATE commits, and it
            # renames the detached row's path to something `_find_by_path` can
            # never match again - so a refusal after it would strand that row's
            # uuid and every share token stamped with it, which is exactly what
            # `detach` promises cannot happen.
            #
            # Gated on the flag like every other call, or `unique_name=False`
            # would be a promise this branch quietly breaks: start-up would
            # still die on a name here (#1096 review). Nothing is stranded by
            # skipping it - with no name check anywhere in the call there is no
            # refusal left to land after the commit.
            if unique_name:
                self._refuse_duplicate_name(cleaned)
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

        if unique_name:
            self._refuse_duplicate_name(cleaned)

        now = datetime.now(timezone.utc).isoformat()
        first_library = not self.list_libraries()
        library_uuid = (
            self._record_recovered_uuid(recovered_uuid, resolved_path, now)
            if recovered_uuid
            else self._mint_uuid(resolved_path, now)
        )

        try:
            with self._hub.transaction() as conn:
                # The authoritative one: BEGIN IMMEDIATE is held from here to
                # the INSERT, so two processes adding the same name cannot both
                # pass. The call above is the cheap early refusal.
                if unique_name:
                    self._refuse_duplicate_name(cleaned, conn=conn)
                cursor = conn.execute(
                    "INSERT INTO library (uuid, vault_uuid, settings_salt, "
                    "identity_migration_state, name, path, created_at, "
                    "attached_at, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        library_uuid,
                        fingerprint,
                        # Keys the settings fingerprint this library stores. Minted
                        # here and never written into the library, which is what
                        # keeps that fingerprint meaningless to anyone holding only
                        # the folder (see hub/schema.py).
                        secrets.token_hex(16),
                        identity_migration_state,
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

    def _record_recovered_uuid(
        self, recovered_uuid: str, resolved_path: str, now: str
    ) -> str:
        """Restore a stamped UUID only under the caller's explicit recovery proof."""
        try:
            parsed = str(uuid_module.UUID(recovered_uuid))
        except (ValueError, AttributeError) as exc:
            raise LibraryError(
                "The recovered library fingerprint is not a UUID."
            ) from exc
        with self._hub.transaction() as conn:
            conn.execute(
                "INSERT INTO library_uuid_issued (uuid, issued_at, first_path) "
                "VALUES (?, ?, ?)",
                (parsed, now, resolved_path),
            )
        return parsed

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
        self,
        existing: Library,
        name: str,
        fingerprint: Optional[str],
        unique_name: bool = True,
    ) -> Library:
        """Re-attach a detached row, keeping its uuid and its tokens.

        Args:
            unique_name: As :meth:`_register`'s. Reviving is the branch a
                start-up registration takes when the folder is provably the same
                library, so it is on that path too and must honour the flag for
                the same reason.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._hub.transaction() as conn:
            # The row is about to take this name, so it has to clear the same
            # check a fresh registration does: a folder re-attached under a name
            # another library now answers to would break `get` for both.
            if unique_name:
                self._refuse_duplicate_name(name, except_id=existing.id, conn=conn)
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

    def forget_vault_fingerprint(self, library: Library) -> Library:
        """Treat a registered library whose vault file is gone as never opened.

        Startup creates a fresh vault at the same path and stamps it with the
        library's own uuid, exactly as for a folder registered without one.
        The library keeps its uuid, so share links stay stamped for it; they
        find nothing until pictures are imported again.
        """
        with self._hub.transaction() as conn:
            conn.execute(
                "UPDATE library SET vault_uuid = NULL WHERE id = ?", (library.id,)
            )
        return self.by_uuid(library.uuid) or library

    def adopt_vault_fingerprint(self, library: Library) -> Library:
        """Record the fingerprint of a registered vault this hub never opened.

        A registration made while its vault carried no fingerprint records
        ``vault_uuid = NULL``; the value is written on the first successful
        open. If something else stamps that vault first - another PixlStash
        installation on this machine pointed at the same folder - every later
        startup dies on the conflict check with nothing in the UI to undo it.

        A NULL fingerprint means this hub has never served the library, so
        adopting what the folder carries claims nothing: the library keeps its
        own uuid, which is what tokens are stamped with. A row that somehow
        already has tokens is refused, so a share link can never come back
        pointing at content it was not issued for.
        """
        if library.vault_uuid is not None:
            return library
        fingerprint = read_vault_uuid(library.path)
        if fingerprint is None or fingerprint == library.uuid:
            return library
        tokens = self._token_count(library.uuid)
        if tokens:
            logger.warning(
                "%s carries fingerprint %s but library %s was registered "
                "without one and already has %d token(s); not adopting it.",
                library.path,
                fingerprint,
                library.uuid,
                tokens,
            )
            return library
        with self._hub.transaction() as conn:
            conn.execute(
                "UPDATE library SET vault_uuid = ? WHERE id = ? AND vault_uuid IS NULL",
                (fingerprint, library.id),
            )
        logger.info(
            "Adopted the existing fingerprint %s for library %s at %s; it was "
            "registered before anything stamped the vault.",
            fingerprint,
            library.uuid,
            library.path,
        )
        return self.by_uuid(library.uuid) or library

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
            settings_salt=row["settings_salt"],
            identity_migration_state=row["identity_migration_state"],
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
