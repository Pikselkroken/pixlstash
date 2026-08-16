"""Restore a backup archive into a new library, and make it the one that opens.

The counterpart to :mod:`pixlstash.services.library_backup_service`. Reading a
backup back used to be documented as "unpack the tar yourself", which is fine
advice about the tar and wrong about everything else — unpacking is not the hard
part:

**The library files are archived under ``images/``, but a library wants them
beside ``vault.db``.** A hand-unpacked archive therefore produces a folder shape
that ``attach`` refuses, and the fix is not guessable from the error.

**The hub, not ``server-config.json``, decides which library opens** (see
``Server._create_vault``'s note: "From then on the registry's active row wins").
So restoring the pictures and pointing ``image_root`` at them does nothing at
all. The archived ``hub.db`` has to become the installation's hub — which is
also what brings the owner's password and that library's API tokens back, the
thing a restore is actually for.

**Nothing is overwritten and nothing is deleted.** The restored library goes to
a folder that must not already hold anything, and the current
``server-config.json`` and ``hub.db`` are *moved* into a timestamped
``pre-restore-*`` directory beside themselves rather than replaced. Because the
hub is resolved as ``dirname(server-config.json)/hub.db``, that directory is
directly launchable: ``pixlstash-server --server-config <it>/server-config.json``
reopens the previous installation exactly as it was. The old library folder is
never touched at all.

That is the whole safety story, and it is structural rather than procedural:
there is no destructive step to get wrong, only two renames that a printed
command undoes.
"""

from __future__ import annotations

import json
import ntpath
import os
import shutil
import sqlite3
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import zstandard

from pixlstash.hub.registry import VAULT_FILENAME
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# `pixlstash.app.SERVER_CONFIG_PATH`'s basename. Spelled out rather than
# imported: importing `pixlstash.app` from the CLI would pull the whole server
# in to learn one filename.
SERVER_CONFIG_FILENAME = "server-config.json"

# Every extracted file, not just the databases. Writing them at the process
# umask would be looser than what came out of the library: `hub.db` is 0600 by
# contract (``hub/db.py``) and snapshot archives are chmodded 0600 on the way in
# (``snapshot_service``), so an extract at 0644 would silently *undo* that
# hardening for the exact files that carry credentials. Applied to pictures too
# rather than classifying members — the restored folder is 0700 either way, so
# uniform owner-only costs nothing and leaves no member to get wrong.
RESTORED_FILE_MODE = 0o600

# zstd's frame magic, so a renamed archive is still read correctly. The CLI's
# `--no-compress` writes a plain tar and users rename backups.
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

# Exactly what a backup archive may contain. Anything else is refused rather
# than ignored: an archive holding a member this does not recognise is not one
# of ours, and quietly skipping it would restore a partial library.
_MANIFEST_NAME = "manifest.json"
_HUB_NAME = "hub.db"
_IMAGES_PREFIX = "images/"

# Moved aside together, because they are one unit: the hub is located relative
# to the config file, so separating them would leave neither directory
# launchable. The sidecars carry committed transactions and must travel with
# the database.
_CONFIG_PAIR = (
    SERVER_CONFIG_FILENAME,
    _HUB_NAME,
    f"{_HUB_NAME}-wal",
    f"{_HUB_NAME}-shm",
)


class RestoreError(RuntimeError):
    """The restore could not be completed, with a message for the terminal."""


@dataclass
class RestorePlan:
    """What a restore is about to do, so the CLI can describe it before asking."""

    archive: str
    library_name: str
    library_uuid: str
    picture_count: int
    created_at: str
    source_path: str
    metadata_only: bool
    reference_folders: list[str]
    library_folder: str
    config_dir: str
    preserved_dir: str
    other_libraries: int

    @property
    def preserved_config(self) -> str:
        """The launchable ``--server-config`` path for the previous install."""
        return os.path.join(self.preserved_dir, SERVER_CONFIG_FILENAME)


@dataclass
class RestoreResult:
    """What a completed restore wrote, for the CLI to report."""

    plan: RestorePlan
    file_count: int
    had_previous_config: bool


def _open_archive_stream(handle):
    """Return a tar stream over *handle*, transparently zstd-decompressing."""
    prefix = handle.read(len(_ZSTD_MAGIC))
    handle.seek(0)
    if prefix == _ZSTD_MAGIC:
        reader = zstandard.ZstdDecompressor().stream_reader(handle)
        return tarfile.open(fileobj=reader, mode="r|")
    return tarfile.open(fileobj=handle, mode="r|")


def _safe_member_target(member: tarfile.TarInfo, root: str) -> Optional[str]:
    """Return where *member* may be written, or None if it is not part of a backup.

    Every rejection here is a refusal rather than a skip. Directory members are
    the one exception: the extraction creates parents itself, so they carry no
    information and are simply not written.

    Raises:
        RestoreError: The member is unsafe (absolute, traversing, or not a
            regular file) or is not something a backup archive contains.
    """
    # Windows-authored archives can carry backslashes. Normalise before any
    # check, so traversal is evaluated against the name that will be used.
    name = member.name.replace("\\", "/")
    while name.startswith("./"):
        name = name[2:]
    if not name or name != os.path.normpath(name).replace(os.sep, "/"):
        raise RestoreError(
            f"Refusing archive member with an unsafe name: {member.name}"
        )
    if name.startswith("/") or ".." in name.split("/"):
        raise RestoreError(
            f"Refusing archive member outside the archive: {member.name}"
        )
    # Checked on every platform, not just Windows, and deliberately so. A
    # component like `C:evil` makes `os.path.join` discard everything to its
    # left *on Windows only*, so a POSIX-only gate would never see the escape
    # it causes. Refusing it everywhere makes the Linux suite proof about the
    # Windows behaviour, and no backup this writes ever contains one.
    if any(ntpath.splitdrive(part)[0] for part in name.split("/")):
        raise RestoreError(
            f"Refusing archive member with a drive-qualified name: {member.name}"
        )

    if member.isdir():
        return None
    if not member.isreg():
        # Symlinks, hardlinks, devices and fifos. A backup writes none of them
        # (library_backup_service refuses them on the way in), so one here means
        # the archive was built or edited by something else.
        raise RestoreError(f"Refusing non-regular archive member: {member.name}")

    if name in (_MANIFEST_NAME, VAULT_FILENAME, _HUB_NAME):
        return _contained(os.path.join(root, name), root, member)
    if name.startswith(_IMAGES_PREFIX):
        # The whole point: `images/a/b.jpg` in the archive is `a/b.jpg` in the
        # library, beside vault.db rather than under a subfolder.
        relative = name[len(_IMAGES_PREFIX) :]
        if not relative:
            return None
        target = os.path.join(root, "library", *relative.split("/"))
        return _contained(target, root, member)
    raise RestoreError(
        f"{member.name} is not part of a PixlStash backup. Refusing to restore "
        "an archive this did not write."
    )


def _contained(target: str, root: str, member: tarfile.TarInfo) -> str:
    """Prove *target* is inside *root*, whatever the name checks concluded.

    The name checks above reason about the string; this reasons about the path
    that was actually built, and the two can disagree. On Windows a component
    carrying a drive letter makes ``os.path.join`` discard everything to its
    left — ``join(r"\\scratch\\library", "C:evil")`` is ``"C:evil"`` — so a
    member named ``images/C:evil`` passes every check above and lands outside
    the staging directory. One containment test closes that and any sibling of
    it, and costs nothing per member.
    """
    resolved = os.path.normcase(os.path.abspath(target))
    root_resolved = os.path.normcase(os.path.abspath(root))
    try:
        contained = os.path.commonpath((root_resolved, resolved)) == root_resolved
    except ValueError:
        # Different drives on Windows: not merely uncontained, but proof the
        # member steered the path somewhere else entirely.
        contained = False
    if not contained:
        raise RestoreError(
            f"Refusing archive member that resolves outside the restore "
            f"directory: {member.name}"
        )
    return target


def _extract(archive: str, scratch: str) -> int:
    """Stream *archive* into *scratch*, validating every member. Returns file count."""
    staged = os.path.join(scratch, "library")
    os.makedirs(staged, exist_ok=True)
    count = 0
    try:
        with open(archive, "rb") as handle:
            with _open_archive_stream(handle) as tar:
                for member in tar:
                    target = _safe_member_target(member, scratch)
                    if target is None:
                        continue
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    source = tar.extractfile(member)
                    if source is None:
                        raise RestoreError(
                            f"Could not read {member.name} from {archive}"
                        )
                    with source, open(target, "wb") as out:
                        shutil.copyfileobj(source, out)
                    os.chmod(target, RESTORED_FILE_MODE)
                    count += 1
    except RestoreError:
        raise
    except (OSError, tarfile.TarError, zstandard.ZstdError) as exc:
        raise RestoreError(f"Could not read the archive {archive}: {exc}") from exc
    return count


def _read_manifest(scratch: str, archive: str) -> dict:
    """Load and sanity-check the archive's manifest."""
    path = os.path.join(scratch, _MANIFEST_NAME)
    if not os.path.isfile(path):
        raise RestoreError(
            f"{archive} has no {_MANIFEST_NAME}, so it is not a PixlStash backup."
        )
    try:
        with open(path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as exc:
        raise RestoreError(
            f"{archive} has an unreadable {_MANIFEST_NAME}: {exc}"
        ) from exc
    if not isinstance(manifest, dict) or not manifest.get("library_uuid"):
        raise RestoreError(f"{archive} has a {_MANIFEST_NAME} without a library uuid.")
    for required in (VAULT_FILENAME, _HUB_NAME):
        if not os.path.isfile(os.path.join(scratch, required)):
            raise RestoreError(
                f"{archive} is missing {required}; it cannot be restored."
            )
    return manifest


def _require_empty_destination(folder: str) -> None:
    """Refuse anything that would put the restored library on top of something."""
    if os.path.islink(folder):
        raise RestoreError(
            f"Refusing to restore through symlink {folder}. Name a new folder."
        )
    if not os.path.exists(folder):
        return
    if not os.path.isdir(folder):
        raise RestoreError(f"{folder} already exists and is not a folder.")
    if os.listdir(folder):
        raise RestoreError(
            f"{folder} already has contents. Restore names a NEW folder so that "
            "nothing can be overwritten; pick a path that does not exist yet."
        )


def _assert_server_not_running(hub_path: str) -> None:
    """Refuse while the hub is held open, which means PixlStash is running.

    The restore moves ``hub.db`` and its WAL sidecar. Doing that under a live
    server hands it a database that is no longer there, and loses whatever the
    WAL had not yet checkpointed. ``BEGIN IMMEDIATE`` is the cheap, definite
    test: it takes the write lock the server holds.
    """
    if not os.path.isfile(hub_path):
        return
    try:
        conn = sqlite3.connect(hub_path, timeout=0.5)
    except sqlite3.Error as exc:
        raise RestoreError(
            f"Could not check whether {hub_path} is in use: {exc}"
        ) from exc
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()
    except sqlite3.OperationalError as exc:
        raise RestoreError(
            f"{hub_path} is locked, which means PixlStash is running. Stop the "
            "server (and close the desktop app) and run this again."
        ) from exc
    finally:
        conn.close()


def plan_restore(archive: str, folder: str, hub_path: str, scratch: str) -> RestorePlan:
    """Validate everything and stage the archive, without touching the install.

    Extracts to *scratch* and reads the manifest, so the caller can describe the
    restore accurately before asking for confirmation. Nothing outside *scratch*
    is written.

    Raises:
        RestoreError: The archive, the destination, or the installation state
            makes the restore impossible.
    """
    archive = os.path.abspath(os.path.expanduser(archive))
    if not os.path.isfile(archive):
        raise RestoreError(f"No archive at {archive}.")
    folder = os.path.abspath(os.path.expanduser(folder))
    _require_empty_destination(folder)

    config_dir = os.path.dirname(os.path.abspath(hub_path))
    _assert_server_not_running(hub_path)

    _extract(archive, scratch)
    manifest = _read_manifest(scratch, archive)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return RestorePlan(
        archive=archive,
        library_name=str(manifest.get("library_name") or "(unnamed)"),
        library_uuid=str(manifest["library_uuid"]),
        picture_count=int(manifest.get("picture_count") or 0),
        created_at=str(manifest.get("created_at") or "unknown"),
        source_path=str(manifest.get("source_path") or "unknown"),
        metadata_only=bool(manifest.get("metadata_only")),
        reference_folders=list(manifest.get("reference_folders") or []),
        library_folder=folder,
        config_dir=config_dir,
        preserved_dir=os.path.join(config_dir, f"pre-restore-{stamp}"),
        other_libraries=_count_other_libraries(
            os.path.join(scratch, _HUB_NAME), str(manifest["library_uuid"])
        ),
    )


def _count_other_libraries(hub_copy: str, library_uuid: str) -> int:
    """How many *other* registrations the archived hub carries."""
    try:
        conn = sqlite3.connect(f"file:{hub_copy}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error as exc:
        raise RestoreError(f"The archived hub could not be opened: {exc}") from exc
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM library WHERE uuid != ?", (library_uuid,)
        ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error as exc:
        raise RestoreError(f"The archived hub is not readable: {exc}") from exc
    finally:
        conn.close()


def _point_hub_at(hub_copy: str, plan: RestorePlan) -> None:
    """Retarget and activate the restored library inside the staged hub.

    Done while the hub is still in scratch, so a failure here leaves the live
    installation untouched. The archived row records the path the library had on
    the machine that made the backup, which is rarely where it is being restored.
    """
    # Imported here: the registry pulls the hub schema and its migrations in,
    # which the archive validation above has no use for.
    from pixlstash.hub.db import HubDatabase
    from pixlstash.hub.registry import LibraryError, LibraryRegistry

    try:
        hub = HubDatabase(hub_copy, repair_permissions=True)
    except Exception as exc:
        raise RestoreError(f"The archived hub could not be opened: {exc}") from exc
    try:
        registry = LibraryRegistry(hub)
        library = registry.by_uuid(plan.library_uuid)
        if library is None:
            raise RestoreError(
                f"The archived hub has no registration for {plan.library_uuid}, "
                "so the restored library could not be activated."
            )
        registry.relocate(library.id, plan.library_folder)
        registry.set_active(library.id)
    except LibraryError as exc:
        raise RestoreError(f"Could not activate the restored library: {exc}") from exc
    finally:
        hub.close()


def _preserve_current_config(plan: RestorePlan) -> bool:
    """Move the live config/hub pair into the plan's ``pre-restore-*`` directory.

    Returns:
        Whether anything was there to preserve. A first-ever run has no pair,
        and restoring onto one is legitimate.
    """
    present = [
        name
        for name in _CONFIG_PAIR
        if os.path.lexists(os.path.join(plan.config_dir, name))
    ]
    if not present:
        return False
    os.makedirs(plan.preserved_dir, mode=0o700, exist_ok=True)
    for name in present:
        source = os.path.join(plan.config_dir, name)
        try:
            os.replace(source, os.path.join(plan.preserved_dir, name))
        except OSError as exc:
            raise RestoreError(
                f"Could not move {source} aside to {plan.preserved_dir}: {exc}. "
                "Nothing further was changed."
            ) from exc
    return True


def _write_server_config(plan: RestorePlan, preserved: bool) -> None:
    """Write a config for the restored library, keeping the machine's settings.

    Port, TLS and the rest describe *this machine* and are carried over; only
    ``image_root`` is retargeted. It is the seed a first run uses, and keeping it
    honest matters even though the hub is what actually selects the library.
    """
    config: dict = {}
    if preserved:
        source = plan.preserved_config
        try:
            with open(source, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                config = loaded
        except FileNotFoundError:
            config = {}
        except (OSError, ValueError) as exc:
            logger.warning(
                "Could not reuse the previous server config %s (%s); writing a "
                "fresh one for the restored library.",
                source,
                exc,
            )
    config["image_root"] = plan.library_folder
    destination = os.path.join(plan.config_dir, SERVER_CONFIG_FILENAME)
    try:
        with open(destination, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=2)
    except OSError as exc:
        raise RestoreError(
            f"Could not write {destination}: {exc}. The previous configuration "
            f"is in {plan.preserved_dir} and can be moved back."
        ) from exc


def perform_restore(plan: RestorePlan, scratch: str, file_count: int) -> RestoreResult:
    """Publish the staged restore: library folder first, then the config pair.

    Ordering is deliberate, and the library folder has to come first even though
    it is the bigger step: ``relocate`` validates that the folder it is pointed
    at holds a vault, so the hub cannot be retargeted at a path that is not
    there yet. Publishing it first is safe because that path was proved empty —
    creating it destroys nothing, and it is removed again if the hub step fails.

    The live installation is therefore untouched until the last two renames,
    both inside one directory, and both undone by moving the preserved pair
    back.

    Raises:
        RestoreError: A step failed. The message names what is where.
    """
    staged_library = os.path.join(scratch, "library")
    os.replace(
        os.path.join(scratch, VAULT_FILENAME),
        os.path.join(staged_library, VAULT_FILENAME),
    )

    _require_empty_destination(plan.library_folder)
    created_folder = not os.path.isdir(plan.library_folder)
    parent = os.path.dirname(plan.library_folder) or "."
    os.makedirs(parent, exist_ok=True)
    try:
        if created_folder:
            os.replace(staged_library, plan.library_folder)
        else:
            # An empty folder the user made themselves: fill it rather than
            # refusing, since _require_empty_destination has cleared it.
            for entry in os.listdir(staged_library):
                os.replace(
                    os.path.join(staged_library, entry),
                    os.path.join(plan.library_folder, entry),
                )
        os.chmod(plan.library_folder, 0o700)
    except OSError as exc:
        raise RestoreError(
            f"Could not put the restored library at {plan.library_folder}: {exc}. "
            "Your installation is unchanged."
        ) from exc

    try:
        _point_hub_at(os.path.join(scratch, _HUB_NAME), plan)
    except RestoreError:
        # The folder is ours: it was proved empty a moment ago and everything in
        # it came out of the archive. Taking it back leaves no trace of a
        # restore that never reached the live installation.
        _withdraw_library_folder(plan.library_folder, remove_folder=created_folder)
        raise

    preserved = _preserve_current_config(plan)
    try:
        os.replace(
            os.path.join(scratch, _HUB_NAME),
            os.path.join(plan.config_dir, _HUB_NAME),
        )
    except OSError as exc:
        raise RestoreError(
            f"Could not install the restored hub: {exc}. The previous "
            f"configuration is intact in {plan.preserved_dir}; move its files "
            f"back into {plan.config_dir} to undo this."
        ) from exc
    _write_server_config(plan, preserved)

    logger.info(
        "Restored library %s (%s) from %s to %s; previous config preserved in %s",
        plan.library_name,
        plan.library_uuid,
        plan.archive,
        plan.library_folder,
        plan.preserved_dir if preserved else "(nothing to preserve)",
    )
    return RestoreResult(
        plan=plan, file_count=file_count, had_previous_config=preserved
    )


def _withdraw_library_folder(folder: str, *, remove_folder: bool) -> None:
    """Undo a published library folder after a later step failed.

    Only ever called on a folder this restore proved empty and then filled, so
    everything removed came out of the archive. Failure is logged rather than
    raised: the caller is already reporting the real error, and the leftover is
    an orphan folder rather than damage.
    """
    try:
        if remove_folder:
            shutil.rmtree(folder)
        else:
            for entry in os.listdir(folder):
                target = os.path.join(folder, entry)
                if os.path.isdir(target) and not os.path.islink(target):
                    shutil.rmtree(target)
                else:
                    os.unlink(target)
    except OSError as exc:
        logger.warning(
            "Could not withdraw the partially restored library at %s: %s. It "
            "holds only archive content and can be deleted by hand.",
            folder,
            exc,
        )


def restore_scratch(folder: str) -> str:
    """Create a scratch directory on the destination's own filesystem.

    Publication is ``os.replace``, which cannot cross a filesystem, and a
    library is exactly the payload nobody wants copied twice.
    """
    parent = os.path.dirname(os.path.abspath(os.path.expanduser(folder))) or "."
    try:
        os.makedirs(parent, exist_ok=True)
        return tempfile.mkdtemp(prefix=".pixlstash-restore-", dir=parent)
    except OSError as exc:
        raise RestoreError(f"Could not stage a restore beside {parent}: {exc}") from exc


def remove_scratch(path: Optional[str]) -> None:
    """Delete a scratch directory, logging rather than raising on failure."""
    if not path or not os.path.isdir(path):
        return
    try:
        shutil.rmtree(path)
    except OSError as exc:
        logger.warning("Could not clean up the restore scratch %s: %s", path, exc)
