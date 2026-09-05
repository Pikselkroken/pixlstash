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
import shutil
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
from pixlstash.hub.bootstrap import (
    known_vault_revisions,
    newer_library_message,
    registered_vault_path,
)
from pixlstash.routes.pictures import clear_stats_cache
from pixlstash.routes.pictures._anomaly import clear_anomaly_region_cache
from pixlstash.utils.image_processing.image_utils import ImageUtils

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

    UNAVAILABLE = "unavailable"
    """No verified coherent open vault exists; restart is required."""


class LibrarySwitchError(LibraryError):
    """A switch was refused or failed, with the session left on its old library."""


def _bring_up(vault, label: str) -> None:
    """Build the inference engine, then start the vault's background workers.

    ``Vault.start()`` alone gets the WorkPlanner running, but every
    engine-gated finder - tags, descriptions, face extraction, embeddings,
    likeness - returns ``None`` for as long as ``Vault._engine`` is ``None``,
    so a switched-to library would sit there with the planner sweeping and no
    AI work ever queued, until the next restart. ``app.main`` does this for the
    boot vault; a switch has to do it for its own.

    A failed engine build costs the AI workers, not the switch: the library is
    perfectly usable without them, and raising here would roll the user back to
    the library they just left.
    """
    try:
        vault.ensure_ready()
    except Exception:
        logger.exception(
            "Could not build the inference engine for the %s library; it will "
            "serve without tagging, descriptions, face extraction or embeddings "
            "until the next restart",
            label,
        )
    vault.start()


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
            newer_library_message(unknown, library=f"The library at {vault_path}")
        )


class LibrarySwitchService:
    """Owns the active-library swap and the state it passes through."""

    def __init__(self, server: "Server"):
        """Bind the service to *server*, whose vault it will replace."""
        self._server = server
        # Serialises switches against each other. Two concurrent switches would
        # race to close the same vault.
        self._lock = threading.Lock()

    @property
    def state(self) -> SwitchState:
        """What the server can currently serve."""
        return self._server.library_coordinator.state

    @property
    def is_switching(self) -> bool:
        """True while a swap is in flight."""
        return self.state is SwitchState.SWITCHING

    @property
    def generation(self) -> int:
        return self._server.library_coordinator.generation

    def _clear_generation_retained_state(self, previous_root: str) -> None:
        """Discard paths, IDs, artifacts and availability state from the old vault."""
        for task in list(self._server.export_tasks.values()):
            private_dir = task.get("private_dir")
            if private_dir:
                if not os.path.basename(private_dir).startswith("pixlstash_export_"):
                    logger.warning(
                        "Refusing to clean unexpected export path %s", private_dir
                    )
                    continue
                try:
                    if os.path.islink(private_dir):
                        os.unlink(private_dir)
                    elif os.path.isdir(private_dir):
                        shutil.rmtree(private_dir)
                except OSError as exc:
                    logger.warning(
                        "Could not remove stale export %s: %s", private_dir, exc
                    )
        self._server.export_tasks.clear()
        self._server.import_tasks.clear()

        previous_real = os.path.realpath(previous_root)
        staging_root = os.path.abspath(os.path.join(previous_root, ".staging"))
        staging_root_real = os.path.realpath(staging_root)
        try:
            safe_staging_root = os.path.commonpath(
                (previous_real, staging_root_real)
            ) == previous_real and not os.path.islink(staging_root)
        except ValueError:
            safe_staging_root = False
        for session in list(self._server.staging_sessions.values()):
            staging_dir = session.get("staging_dir")
            if not staging_dir:
                continue
            candidate = os.path.abspath(staging_dir)
            candidate_real = os.path.realpath(candidate)
            try:
                is_old_staging = (
                    safe_staging_root
                    and os.path.commonpath((staging_root_real, candidate_real))
                    == staging_root_real
                )
            except ValueError:
                is_old_staging = False
            if not is_old_staging:
                logger.warning(
                    "Refusing to clean unexpected staging path %s", candidate
                )
                continue
            try:
                if os.path.islink(candidate):
                    os.unlink(candidate)
                elif os.path.isdir(candidate):
                    shutil.rmtree(candidate)
            except OSError as exc:
                logger.warning(
                    "Could not remove stale staging session %s: %s", candidate, exc
                )
        self._server.staging_sessions.clear()
        self._server.auth.clear_guest_session_tracking()
        ImageUtils._extract_embedded_metadata_cached.cache_clear()

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
        if not self._lock.acquire(blocking=False):
            raise LibrarySwitchError(
                "Another library switch is already in progress. Try again in a moment."
            )
        try:
            registry = self._server.library_registry
            target = registry.by_uuid(library_uuid)
            if target is None or not target.attached:
                raise LibraryNotFoundError(
                    f"No attached library with uuid {library_uuid}."
                )
            if target.is_active:
                logger.info("Library %s is already active; nothing to do", target.name)
                return target
            try:
                self._server.library_coordinator.begin_switch()
            except RuntimeError as exc:
                # A refused switch is now a reachable, expected state rather
                # than a rare one: a folder-mapping commit holds a read lease
                # for as long as it runs, precisely so it cannot be retargeted
                # at another library half way through. "Timed out waiting for
                # active-library readers" is the right mechanism and the wrong
                # sentence to hand the owner, so name the work instead.
                busy = self._what_is_holding_the_library()
                if busy:
                    raise LibrarySwitchError(
                        f"This library is busy: {busy}. Switching now would "
                        "leave that work writing into the other library, so it "
                        "waits. Let it finish, or stop it, then switch."
                    ) from exc
                raise LibrarySwitchError(str(exc)) from exc
            try:
                return self._swap(target)
            except Exception:
                if self._server.library_coordinator.state is SwitchState.SWITCHING:
                    self._server.library_coordinator.restore_ready()
                raise
        finally:
            self._lock.release()

    def _what_is_holding_the_library(self) -> Optional[str]:
        """Name the long-running work a switch is waiting on, when it can be.

        The coordinator counts readers; it does not know what they are. These
        two are the only holders that keep a lease for minutes rather than
        milliseconds, so they are what an owner is actually waiting on when a
        switch refuses. Returns ``None`` when nothing recognisable is running,
        and the caller falls back to the coordinator's own words.
        """
        with self._server.folder_structure_commit_lock:
            commit = self._server.folder_structure_commit
            if commit and commit.get("status") in ("queued", "running"):
                return "a folder mapping is still being organised"
        for task in list(getattr(self._server, "import_tasks", {}).values()):
            if isinstance(task, dict) and task.get("status") in (
                "queued",
                "running",
            ):
                return "an import is still running"
        return None

    def _swap(self, target: Library) -> Library:
        """Open the target, then retire the current vault. Never the reverse."""
        # PREPARE: validate the registered target and run its migrations while
        # the current library remains fully available.
        resolved = self._revalidate(target)
        previous = self._server.vault
        previous_library = self._server.library_registry.active_library()
        pre_switch_clients = []
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
            logger.exception("Could not open library %s; staying put", target.name)
            raise LibrarySwitchError(
                f'Could not open "{target.name}": {exc}. PixlStash is still using '
                f"the library it was already on, and nothing has been changed."
            ) from exc

        old_retirement_started = False
        try:
            # VALIDATE/CONFIGURE: all fallible candidate work happens before
            # the old vault is retired.
            incoming.add_event_listener(self._server.handle_vault_event)
            incoming.auth_service = self._server.auth
            self._server.apply_user_settings_to_vault(incoming)
            self._server.reconcile_library_settings(incoming, target)

            # INVALIDATE/DRAIN OLD: no library-derived cache may survive
            # publication. Vault.close cancels queued work, signals active
            # tasks and joins workers before closing the database.
            clear_stats_cache()
            clear_anomaly_region_cache()
            clear_thumbnails = getattr(
                self._server, "_clear_thumbnail_runtime_cache", None
            )
            if clear_thumbnails is not None:
                clear_thumbnails()
            # Recovery responsibility starts before close: Vault.close() may
            # release workers/DB resources and only then surface an exception.
            # Treat any exception from this point as a retired old handle.
            old_retirement_started = True
            previous.close()
            _bring_up(incoming, "incoming")

            # The candidate is now fully started and admission is still closed.
            # Remove every old-generation capability/artifact before publishing
            # the new runtime, so its first request cannot observe stale IDs.
            self._clear_generation_retained_state(previous.image_root)

            # PUBLISH: requests remain refused while the registry and the two
            # runtime handles cross the same short publication boundary.
            self._server.library_registry.set_active(target.id)
            self._server.vault = incoming
            self._server.auth.vault_db = incoming.db

            # Atomically claim the complete old-generation socket set while
            # admission is still closed. publish_ready() then opens admission;
            # any socket registered after it belongs to the new generation and
            # must not be swept into the old clients' 1012 close.
            pre_switch_clients = self._server.claim_websockets_for_switch()
            self._server.library_coordinator.publish_ready()

            # RETIRE CLIENT VIEWS only after READY. A tab reloads synchronously
            # on this 1012 and must be admitted directly to the target tuple,
            # never observe a transient SWITCHING 503.
            try:
                self._server.close_websocket_snapshot_for_switch(pre_switch_clients)
            except Exception:
                # Publication is already committed and coherent. A socket that
                # fails to close is stale client state, not grounds to roll the
                # server back underneath newly admitted target requests.
                logger.exception("Could not close every pre-switch WebSocket")
        except Exception as exc:
            logger.exception("Library switch to %s failed", target.name)
            if pre_switch_clients:
                try:
                    self._server.close_websocket_snapshot_for_switch(pre_switch_clients)
                except Exception:
                    logger.exception(
                        "Could not close claimed WebSockets after publication failure"
                    )
            try:
                incoming.close()
            except Exception:
                logger.exception("Could not close failed incoming vault")
            if old_retirement_started and previous_library is not None:
                # RECOVER: rebuild the old vault rather than ever publishing a
                # closed handle or leaving auth.vault_db mixed with another DB.
                try:
                    # Idempotent for a normal Vault; ensures a partially failed
                    # close gets one final cleanup attempt before replacement.
                    try:
                        previous.close()
                    except Exception:
                        logger.exception(
                            "Previous vault cleanup raised again during recovery"
                        )
                    recovered = self._server.build_vault(
                        registered_vault_path(self._server.hub, previous_library)
                    )
                    recovered.add_event_listener(self._server.handle_vault_event)
                    recovered.auth_service = self._server.auth
                    self._server.apply_user_settings_to_vault(recovered)
                    self._server.reconcile_library_settings(recovered, previous_library)
                    _bring_up(recovered, "recovered previous")
                    self._server.library_registry.set_active(previous_library.id)
                    self._server.vault = recovered
                    self._server.auth.vault_db = recovered.db
                except Exception as recovery_exc:
                    logger.critical(
                        "Could not recover the previous library after a failed swap",
                        exc_info=True,
                    )
                    self._server.vault = None
                    self._server.auth.vault_db = None
                    self._server.library_coordinator.mark_unavailable()
                    try:
                        self._server.close_all_websockets_for_switch()
                    except Exception:
                        logger.exception("Could not close sockets in fatal state")
                    self._server.request_fatal_shutdown()
                    raise LibrarySwitchError(
                        "The switch failed after the old library closed, and "
                        "PixlStash could not reopen it. Restart the server."
                    ) from recovery_exc
            elif old_retirement_started:
                self._server.vault = None
                self._server.auth.vault_db = None
                self._server.library_coordinator.mark_unavailable()
                self._server.request_fatal_shutdown()
                raise LibrarySwitchError(
                    "The switch retired the old library without a recoverable "
                    "registry entry. Restart the server."
                ) from exc
            raise LibrarySwitchError(
                f'Could not switch to "{target.name}": {exc}. PixlStash is still '
                "using the previous library."
            ) from exc
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

        # Folder validation is diagnostic only. Fingerprint and revision are
        # decisive on VaultDatabase's securely guarded initial connection,
        # which is also the connection handed to Alembic.
        del vault_path
        return registered_vault_path(self._server.hub, target)


def switching_state_of(server: Optional["Server"]) -> SwitchState:
    """Return a server's switch state, defaulting to READY when it has none."""
    service = getattr(server, "library_switch", None)
    return service.state if service is not None else SwitchState.READY
