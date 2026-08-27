"""Restore a backup archive into a new library, and make it the one that opens.

The counterpart to :mod:`pixlstash.services.library_backup_service`. Reading a
backup back used to be documented as "unpack the tar yourself", which is fine
advice about the tar and wrong about everything else - unpacking is not the hard
part:

**The library files are archived under ``images/``, but a library wants them
beside ``vault.db``.** A hand-unpacked archive therefore produces a folder shape
that ``attach`` refuses, and the fix is not guessable from the error.

**The hub, not ``server-config.json``, decides which library opens** (see
``Server._create_vault``'s note: "From then on the registry's active row wins").
So restoring the pictures and pointing ``image_root`` at them does nothing at
all. The archived ``hub.db`` has to become the installation's hub - which is
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
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import zstandard
from tqdm import tqdm

from pixlstash.hub.registry import VAULT_FILENAME
from pixlstash.pixl_logging import get_logger
from pixlstash.services.library_backup_service import (
    human_bytes,
)
from pixlstash.utils.system_utils import space_shortfall

logger = get_logger(__name__)


def progress_disabled() -> bool:
    """Whether to suppress progress bars, decided here rather than by tqdm.

    ``disable=None`` looks like it means "auto-detect a terminal", but tqdm
    wraps ``file`` in ``DisableOnWriteError`` *before* testing ``isatty``, so
    what it actually does depends on that wrapper forwarding the call. Deciding
    it from ``sys.stderr`` directly is one line, says what it means, and is
    testable - which matters because the failure mode is silent either way: a
    bar that never draws, or a cron log full of redrawn ones.
    """
    return not (hasattr(sys.stderr, "isatty") and sys.stderr.isatty())


# `pixlstash.app.SERVER_CONFIG_PATH`'s basename. Spelled out rather than
# imported: importing `pixlstash.app` from the CLI would pull the whole server
# in to learn one filename.
SERVER_CONFIG_FILENAME = "server-config.json"

# Every extracted file, not just the databases. Writing them at the process
# umask would be looser than what came out of the library: `hub.db` is 0600 by
# contract (``hub/db.py``) and snapshot archives are chmodded 0600 on the way in
# (``snapshot_service``), so an extract at 0644 would silently *undo* that
# hardening for the exact files that carry credentials. Applied to pictures too
# rather than classifying members - the restored folder is 0700 either way, so
# uniform owner-only costs nothing and leaves no member to get wrong.
RESTORED_FILE_MODE = 0o600

# zstd's frame magic, so a renamed archive is still read correctly. The CLI's
# `--no-compress` writes a plain tar and users rename backups.
_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

# What to assume a zstd archive expands to when its header does not say.
# Measured against this repo's own backups, whose payload is dominated by
# already-compressed image data; the databases compress far better but are a
# small share of the bytes.
_UNKNOWN_RATIO = 1.6

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
    space_warning: Optional[str] = None

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
    left - ``join(r"\\scratch\\library", "C:evil")`` is ``"C:evil"`` - so a
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
        archive_size = os.path.getsize(archive)
        with open(archive, "rb") as handle:
            # Measured against the *archive* rather than the extracted files:
            # the tar is read as a stream, so the member count is not known
            # until the end, but how far into the file we have read is known
            # exactly and maps to the wait. Suppressed off a terminal (see
            # `progress_disabled`), keeping scripted restores quiet; it draws
            # on stderr, so stdout stays the report.
            with (
                _open_archive_stream(handle) as tar,
                tqdm(
                    total=archive_size,
                    unit="B",
                    unit_scale=True,
                    unit_divisor=1024,
                    desc="Restoring",
                    disable=progress_disabled(),
                    leave=False,
                ) as bar,
            ):
                for member in tar:
                    target = _safe_member_target(member, scratch)
                    # Advance by however much of the archive that member cost,
                    # including the ones skipped above. `update` with a delta
                    # keeps tqdm's own redraw throttling, which `n = ...` plus
                    # `refresh()` would defeat on an archive of small files.
                    bar.update(max(0, min(handle.tell(), archive_size) - bar.n))
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
    return manifest


def _require_extracted_members(scratch: str, archive: str) -> None:
    """Check the databases a full extraction must have produced.

    Separate from :func:`_read_manifest` because the plan reads the manifest out
    of a *partial* extraction - the peek deliberately stops before ``vault.db``
    - and folding the two together made planning demand a file it had chosen not
    to unpack.
    """
    for required in (VAULT_FILENAME, _HUB_NAME):
        if not os.path.isfile(os.path.join(scratch, required)):
            raise RestoreError(
                f"{archive} is missing {required}; it cannot be restored."
            )


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


def _estimated_extracted_bytes(archive: str) -> tuple[int, bool]:
    """Estimate what *archive* expands to, and whether the figure is exact.

    zstd records the uncompressed size in the frame header only when the writer
    knew it up front. Backups are written with ``stream_writer``, which does not,
    so this is usually a guess - and it says which it is rather than quietly
    presenting one as the other.

    The fallback multiplier is deliberately mild. A library is mostly JPEG and
    PNG, which barely compress; the databases compress well but are the small
    part. Guessing high would cry wolf on every restore, which trains people to
    answer "y" without reading.

    Returns:
        The estimated byte count and whether it came from the frame header.
    """
    compressed = os.path.getsize(archive)
    try:
        with open(archive, "rb") as handle:
            prefix = handle.read(len(_ZSTD_MAGIC))
            handle.seek(0)
            if prefix != _ZSTD_MAGIC:
                # A plain tar is its own uncompressed size.
                return compressed, True
            size = zstandard.frame_content_size(handle.read(24))
    except (OSError, zstandard.ZstdError):
        return int(compressed * _UNKNOWN_RATIO), False
    if size is not None and size > 0:
        return int(size), True
    return int(compressed * _UNKNOWN_RATIO), False


def _announce_start(plan: "RestorePlan") -> None:
    """Say what is about to happen, before the bar takes over the line."""
    print(
        f"Reading {plan.archive}\nRestoring into {plan.library_folder}",
        file=sys.stderr,
    )


def _peek(archive: str) -> tuple[dict, str, str]:
    """Read the manifest and hub out of *archive* without unpacking the library.

    This is what lets the confirmation come *before* the copy. A backup writes
    ``manifest.json``, ``vault.db`` and ``hub.db`` before any picture, so the
    few members needed to describe a restore - its name, its picture count, the
    other registrations its hub carries - are at the front of the stream and
    reading them costs nothing on a 60 GB archive. Extracting the whole thing
    first and asking afterwards, which is what this used to do, made the user
    wait through the entire restore to be asked whether they wanted it.

    Returns:
        The manifest, the path to the extracted hub copy, and the temporary
        directory holding it, which the caller must remove.

    Raises:
        RestoreError: The archive is unreadable or is not a PixlStash backup.
    """
    peek_dir = tempfile.mkdtemp(prefix="pixlstash-restore-peek-")
    wanted = {_MANIFEST_NAME, _HUB_NAME}
    found: set[str] = set()
    try:
        with open(archive, "rb") as handle:
            with _open_archive_stream(handle) as tar:
                for member in tar:
                    name = member.name.replace("\\", "/").lstrip("./")
                    if name not in wanted or not member.isreg():
                        continue
                    source = tar.extractfile(member)
                    if source is None:
                        continue
                    target = os.path.join(peek_dir, name)
                    with source, open(target, "wb") as out:
                        shutil.copyfileobj(source, out)
                    os.chmod(target, RESTORED_FILE_MODE)
                    found.add(name)
                    if found == wanted:
                        # Everything needed is at the front; stop rather than
                        # stream past every picture in the archive.
                        break
    except (OSError, tarfile.TarError, zstandard.ZstdError) as exc:
        remove_scratch(peek_dir)
        raise RestoreError(f"Could not read the archive {archive}: {exc}") from exc

    missing = wanted - found
    if missing:
        remove_scratch(peek_dir)
        raise RestoreError(
            f"{archive} is missing {', '.join(sorted(missing))}; it is not a "
            "PixlStash backup this can restore."
        )
    manifest = _read_manifest(peek_dir, archive)
    return manifest, os.path.join(peek_dir, _HUB_NAME), peek_dir


def plan_restore(
    archive: str,
    folder: str,
    hub_path: str,
) -> RestorePlan:
    """Describe the restore without unpacking it, so the caller can ask first.

    Reads only the manifest and hub out of the archive (see :func:`_peek`), so
    nothing large is written and nothing outside a small temporary directory is
    touched. The library itself is unpacked by :func:`perform_restore`, after
    the caller has confirmed.

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

    manifest, hub_copy, peek_dir = _peek(archive)
    try:
        other_libraries = _count_other_libraries(
            hub_copy, str(manifest["library_uuid"])
        )
    finally:
        remove_scratch(peek_dir)

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
        other_libraries=other_libraries,
        space_warning=_space_warning(archive, folder),
    )


def _space_warning(archive: str, folder: str) -> Optional[str]:
    """Return a sentence about a likely shortfall, or None when it should fit.

    Returned rather than prompted: the CLI already asks one question about the
    whole restore, and a second prompt for this would be two questions about
    the same decision. Measured against the filesystem the library will land
    on, which is where the bytes actually go.
    """
    needed, exact = _estimated_extracted_bytes(archive)
    shortfall = space_shortfall(os.path.dirname(folder) or ".", needed)
    if shortfall is None:
        return None
    required, free = shortfall
    qualifier = "needs" if exact else "looks like it needs roughly"
    return (
        f"{os.path.dirname(folder) or '.'} may not have room: this restore "
        f"{qualifier} {human_bytes(required)} including 10% headroom, and only "
        f"{human_bytes(free)} is free."
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


def perform_restore(plan: RestorePlan, scratch: str) -> RestoreResult:
    """Publish the staged restore: library folder first, then the config pair.

    Ordering is deliberate, and the library folder has to come first even though
    it is the bigger step: ``relocate`` validates that the folder it is pointed
    at holds a vault, so the hub cannot be retargeted at a path that is not
    there yet. Publishing it first is safe because that path was proved empty -
    creating it destroys nothing, and it is removed again if the hub step fails.

    The live installation is therefore untouched until the last two renames,
    both inside one directory, and both undone by moving the preserved pair
    back.

    Raises:
        RestoreError: A step failed. The message names what is where.
    """
    _announce_start(plan)
    file_count = _extract(plan.archive, scratch)
    _require_extracted_members(scratch, plan.archive)

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

    # Copied into the config directory BEFORE the old pair moves aside, and
    # copied rather than renamed. The scratch sits beside the restored library
    # (so publishing it is a rename), which is routinely a different filesystem
    # from the config directory -- `~/Pictures` and `~/.config` on separate
    # mounts is the ordinary case, and `os.replace` across them raises EXDEV.
    # Doing the copy first also means the only step left after the destructive
    # move is a rename within one directory.
    staged_hub = _stage_hub_beside_config(scratch, plan)
    preserved = _preserve_current_config(plan)
    try:
        os.replace(staged_hub, os.path.join(plan.config_dir, _HUB_NAME))
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


def _stage_hub_beside_config(scratch: str, plan: RestorePlan) -> str:
    """Copy the restored hub into the config directory, ready to be renamed in.

    Returns the temporary path, which is on the config directory's own
    filesystem so the final publication is an atomic same-directory rename.
    The hub is a few megabytes at most, so copying it is cheap - unlike the
    library, which is why that one is staged next to its destination instead.

    Raises:
        RestoreError: The copy failed, before anything live has moved.
    """
    source = os.path.join(scratch, _HUB_NAME)
    handle, temp_path = tempfile.mkstemp(
        prefix=".pixlstash-hub-", suffix=".tmp", dir=plan.config_dir
    )
    os.close(handle)
    try:
        shutil.copyfile(source, temp_path)
        os.chmod(temp_path, RESTORED_FILE_MODE)
    except OSError as exc:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise RestoreError(
            f"Could not stage the restored hub in {plan.config_dir}: {exc}. "
            "Your installation is unchanged."
        ) from exc
    return temp_path


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
