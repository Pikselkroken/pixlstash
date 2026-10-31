"""Move or rename a directory inside a registered model folder (#2206).

The folder-shaped counterpart of :mod:`pixlstash.services.model_mover`, with the
same two phases: :func:`plan_directory_move` refuses before anything is touched,
and :func:`execute_directory_move` is what runs on the move thread. A **rename**
is a move into the same parent under ``new_name``; there is no separate verb.

**Same drive: one rename, then one transaction.** ``os.rename`` moves the whole
tree, the samples directories and the files the shelf does not list included,
and then one transaction repoints every ``model_file`` row under the old prefix.
If the repoint fails, the directory is renamed back, so the rows never name a
tree that moved away.

**Another drive: copy, verify, publish, repoint, then remove.** The tree is
copied with ``copytree(symlinks=True)`` into ``<name>.pixlstash-partial`` beside
the target, so a crash mid-copy never leaves a half-populated folder under the
real name. Every catalogued file in the copy is checked against its ``sha256``
(a file nobody has hashed yet is checked by size), the partial is renamed into
place, the rows are repointed, and only then is the source removed. A cancel or
a failure before the repoint removes the partial and repoints nothing; a failure
of the repoint removes the published copy. Either way the source is untouched.
A source that cannot be removed afterwards is a duplicate the rows do not name,
which is reported and never a dangling row.

**The ceiling.** A process killed between the rename (or the publication) and
the repoint commit leaves the tree at the new place and the rows naming the old
one, and a rescan of the destination landing in that window takes the new keys
first, so the repoint fails and the rename is undone with the rescan's rows
left behind. Both residues are rows a rescan of the two folders re-links by
content; the window is the few syscalls between the publication and one commit.
Journalling it is the upgrade if it is ever seen.

**Folders are never merged.** A target name that exists is refused, as is a
target inside the source, the registered root itself, a locked folder on either
side, and a source that holds another registered folder: moving it would move
that registration's files out from under it.

Every path is resolved with :func:`~pixlstash.services.model_mover.resolve_subfolder`,
so both ends are contained in their registered folders and refuse the links and
dot-names the tree never offers.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from typing import Callable, Optional

from pixlstash.hub.db import HubDatabase
from pixlstash.pixl_logging import get_logger
from pixlstash.services.model_folder_scanner import _LIKE_ESCAPE, STATE_PRESENT
from pixlstash.services.model_mover import (
    PARTIAL_SUFFIX,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_MOVED,
    MoveOutcome,
    MoveRefused,
    RepointLost,
    copy_and_digest,
    discard_partial_tree,
    file_digest,
    require_space,
    resolve_subfolder,
    same_device,
)
from pixlstash.utils.library_layout import refusal_for_folder_name
from pixlstash.utils.path_utils import path_is_within

logger = get_logger(__name__)


@dataclass(frozen=True)
class CataloguedFile:
    """One ``model_file`` row under the moving directory."""

    relpath: str
    model_id: int
    state: str
    sha256: Optional[str]
    size: int


@dataclass(frozen=True)
class DirectoryMovePlan:
    """A validated directory move. Nothing here has touched the disk."""

    source_folder_id: int
    source_subpath: str
    """POSIX, relative to the source folder."""
    source_path: str
    destination_folder_id: int
    destination_subpath: str
    """POSIX path of the directory after the move, relative to its folder."""
    destination_path: str
    name: str
    same_drive: bool
    files: int
    """Regular files on disk under the directory, catalogued or not."""
    bytes: int
    """Bytes on disk under the directory; what a cross-drive move copies."""
    catalogued: list[CataloguedFile] = field(default_factory=list)

    @property
    def models(self) -> int:
        """Distinct models with a present copy under the directory."""
        return len({f.model_id for f in self.catalogued if f.state == STATE_PRESENT})

    @property
    def other_files(self) -> int:
        """Files on disk the shelf does not catalogue: previews, notes, sidecars."""
        present = sum(1 for f in self.catalogued if f.state == STATE_PRESENT)
        return max(self.files - present, 0)

    @property
    def bytes_to_copy(self) -> int:
        return 0 if self.same_drive else self.bytes


class _CopyCancelled(Exception):
    """Raised from inside ``copytree`` to stop it between two files.

    Not an ``OSError``: ``copytree`` collects those per file and carries on, and
    a cancel has to stop the copy rather than be listed as one more error.
    """


def model_folder_row(hub: HubDatabase, folder_id: int, role: str) -> dict:
    """The registered folder, refused unless its contents are rearranged per item."""
    row = hub.fetchone(
        "SELECT id, path, kind, movable FROM model_folder WHERE id = ?", (folder_id,)
    )
    if row is None:
        raise MoveRefused(f"No such {role} folder.", status_code=404)
    if row["movable"] != "per_item":
        raise MoveRefused(
            f"The {role} folder is locked: PixlStash's own folders, the "
            "HuggingFace cache, the InsightFace packs and the folders models are "
            "taken from are not rearranged here.",
            status_code=409,
        )
    return dict(row)


def subpath_prefix(subpath: str) -> str:
    """A directory's stored relpath prefix: the scanner stores ``os.sep``."""
    return subpath.replace("/", os.sep) + os.sep


def rows_under(fetchall, folder_id: int, subpath: str) -> list:
    """Every ``model_file`` row under *subpath*, whatever its state.

    *fetchall* is ``hub.fetchall``, or the transaction's own reader, so the same
    query serves the plan and the repoint.

    LIKE folds ASCII case, so ``Flux/`` would match a listing of ``flux/``; the
    exact prefix is checked here, as the folder listing does.
    """
    prefix = subpath_prefix(subpath)
    rows = fetchall(
        "SELECT mf.relpath AS relpath, mf.model_id AS model_id, mf.state AS state, "
        "m.sha256 AS sha256, COALESCE(m.file_size, 0) AS file_size "
        "FROM model_file mf JOIN model m ON m.id = mf.model_id "
        "WHERE mf.model_folder_id = ? AND mf.relpath LIKE ? ESCAPE '\\'",
        (folder_id, prefix.translate(_LIKE_ESCAPE) + "%"),
    )
    return [row for row in rows if row["relpath"].startswith(prefix)]


def _disk_usage(path: str) -> tuple[int, int]:
    """``(files, bytes)`` under *path*, not following links (the copy does not)."""
    files = 0
    total = 0
    for root, _dirs, names in os.walk(path):
        for name in names:
            full = os.path.join(root, name)
            files += 1
            try:
                total += os.lstat(full).st_size
            except OSError as exc:
                logger.warning(
                    "Could not size %s while planning a folder move: %s. The "
                    "space check is short by that file.",
                    full,
                    exc,
                )
    return files, total


def _tree_listing(path: str) -> dict[str, int]:
    """``{relpath: size}`` of every file under *path*, not following links.

    A link to a directory is listed as an entry of its own: ``os.walk`` puts it
    among the directories and does not enter it, and the copy holds it as a link.
    """
    listing: dict[str, int] = {}
    for root, dirs, names in os.walk(path):
        for name in names + [d for d in dirs if os.path.islink(os.path.join(root, d))]:
            full = os.path.join(root, name)
            try:
                listing[os.path.relpath(full, path)] = os.lstat(full).st_size
            except OSError as exc:
                # Recorded as unreadable, so it can never match the copy and
                # the source is kept.
                logger.warning("Could not stat %s after a folder copy: %s", full, exc)
                listing[os.path.relpath(full, path)] = -1
    return listing


def _rewritten(path: str, copied_mtimes: dict[str, int]) -> bool:
    """Whether *path* was written to after its copy began. A link is not in
    *copied_mtimes* (``copytree`` makes it, not the copy function) and is
    compared by size alone."""
    if path not in copied_mtimes:
        return False
    try:
        return os.lstat(path).st_mtime_ns != copied_mtimes[path]
    except OSError as exc:
        logger.warning("Could not stat %s after a folder copy: %s", path, exc)
        return True


def _same_entry(a: str, b: str) -> bool:
    """Whether two names are one directory: a case-only rename on a
    case-insensitive drive, where the target name already "exists"."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def plan_directory_move(
    hub: HubDatabase,
    folder_id: int,
    subpath: str,
    destination_folder_id: int,
    destination_subpath: str,
    new_name: Optional[str] = None,
) -> DirectoryMovePlan:
    """Resolve and check a directory move without touching anything.

    Args:
        folder_id: The registered folder the directory is in.
        subpath: The directory, POSIX and relative to that folder.
        destination_folder_id: The registered folder it moves into.
        destination_subpath: The directory it moves into, ``""`` for the root.
            It must exist.
        new_name: The directory's name at the destination; its current name
            when None. A rename is this with the parent unchanged.

    Raises:
        MoveRefused: With the status the route answers: 400 for the registered
            root, a target inside the source, a bad name or path; 404 for a
            missing source; 409 for a locked folder, a taken name, a missing
            destination, a registered folder inside the source or a stale row
            at the target; 507 when a cross-drive copy would not fit.
    """
    source_folder = model_folder_row(hub, folder_id, "source")
    source_subpath, source_path = resolve_subfolder(source_folder["path"], subpath)
    if not source_subpath:
        raise MoveRefused(
            "That is the registered folder itself. Move or stop watching it from "
            "Manage model folders."
        )
    if not os.path.isdir(source_path):
        raise MoveRefused(
            f"There is no folder {source_subpath!r} in that model folder.",
            status_code=404,
        )
    destination_folder = model_folder_row(hub, destination_folder_id, "destination")
    parent_subpath, parent_path = resolve_subfolder(
        destination_folder["path"], destination_subpath
    )
    if not os.path.isdir(parent_path):
        raise MoveRefused(
            f"There is no folder {destination_subpath!r} to move into.",
            status_code=409,
        )

    name = source_subpath.rsplit("/", 1)[-1] if new_name is None else new_name
    reason = refusal_for_folder_name(name)
    if reason:
        raise MoveRefused(reason)
    new_subpath = f"{parent_subpath}/{name}" if parent_subpath else name
    if destination_folder_id == folder_id and new_subpath == source_subpath:
        raise MoveRefused("The folder is already there under that name.")

    real_source = os.path.realpath(source_path)
    real_parent = os.path.realpath(parent_path)
    if real_parent == real_source or real_parent.startswith(real_source + os.sep):
        raise MoveRefused("A folder cannot be moved into itself or a folder inside it.")

    destination_path = os.path.join(parent_path, name)
    if os.path.lexists(destination_path) and not _same_entry(
        source_path, destination_path
    ):
        raise MoveRefused(
            f"{new_subpath!r} already exists there. Folders are never merged; "
            "rename one of them first.",
            status_code=409,
        )

    for row in hub.fetchall("SELECT id, path FROM model_folder"):
        if path_is_within(row["path"], source_path):
            raise MoveRefused(
                f"{row['path']} is a registered model folder inside "
                f"{source_subpath!r}. Moving it would move that folder's files "
                "out from under it; stop watching it first.",
                status_code=409,
            )

    stale = rows_under(hub.fetchall, destination_folder_id, new_subpath)
    if stale:
        raise MoveRefused(
            f"{stale[0]['relpath']} is still registered in the destination with "
            "no folder behind it. Try again once that folder's next scan has caught up.",
            status_code=409,
        )

    catalogued = [
        CataloguedFile(
            relpath=row["relpath"],
            model_id=int(row["model_id"]),
            state=row["state"],
            sha256=row["sha256"],
            size=int(row["file_size"]),
        )
        for row in rows_under(hub.fetchall, folder_id, source_subpath)
    ]
    files, total = _disk_usage(source_path)
    same_drive = same_device(source_path, parent_path)
    if not same_drive:
        if os.path.lexists(destination_path + PARTIAL_SUFFIX):
            raise MoveRefused(
                f"{destination_path + PARTIAL_SUFFIX} is left over from an "
                "interrupted move. Remove it first.",
                status_code=409,
            )
        require_space(parent_path, total)

    return DirectoryMovePlan(
        source_folder_id=folder_id,
        source_subpath=source_subpath,
        source_path=source_path,
        destination_folder_id=destination_folder_id,
        destination_subpath=new_subpath,
        destination_path=destination_path,
        name=name,
        same_drive=same_drive,
        files=files,
        bytes=total,
        catalogued=catalogued,
    )


def _repoint(hub: HubDatabase, plan: DirectoryMovePlan) -> int:
    """Repoint every row under the old prefix, in one transaction.

    The rows are re-read inside the transaction rather than taken from the plan:
    a rescan is not under ``SHELF_IO_LOCK``, and a row it added since the plan
    would otherwise be left naming the old path.

    Returns:
        How many rows were repointed.

    Raises:
        RepointLost: An UPDATE moved no row. The transaction rolls back.
        sqlite3.Error: A racing writer took a destination key.
    """
    old_prefix = subpath_prefix(plan.source_subpath)
    new_prefix = subpath_prefix(plan.destination_subpath)
    with hub.transaction() as conn:
        rows = rows_under(
            lambda sql, params: conn.execute(sql, params).fetchall(),
            plan.source_folder_id,
            plan.source_subpath,
        )
        for row in rows:
            relpath = row["relpath"]
            cursor = conn.execute(
                "UPDATE model_file SET model_folder_id = ?, relpath = ? "
                "WHERE model_folder_id = ? AND relpath = ?",
                (
                    plan.destination_folder_id,
                    new_prefix + relpath[len(old_prefix) :],
                    plan.source_folder_id,
                    relpath,
                ),
            )
            if int(cursor.rowcount or 0) != 1:
                raise RepointLost(
                    f"Repointing (folder {plan.source_folder_id}, {relpath!r}) "
                    "affected no row."
                )
    return len(rows)


def _refuse_if_taken(plan: DirectoryMovePlan) -> None:
    """The plan's free check again, immediately before the publication."""
    if os.path.lexists(plan.destination_path) and not _same_entry(
        plan.source_path, plan.destination_path
    ):
        raise FileExistsError(
            f"{plan.destination_path} appeared while the move was running, so "
            "nothing was moved onto it."
        )


def _verify(
    plan: DirectoryMovePlan,
    copy_root: str,
    on_bytes: Optional[Callable[[int], None]] = None,
) -> None:
    """Check every catalogued file in the copy against what the hub says it is."""
    old_prefix = subpath_prefix(plan.source_subpath)
    for entry in plan.catalogued:
        copied = os.path.join(copy_root, entry.relpath[len(old_prefix) :])
        if not os.path.isfile(copied):
            # A row whose file was already gone before the move: nothing was
            # copied for it and there is nothing to check.
            continue
        if entry.sha256 is not None:
            arrived = file_digest(copied, on_bytes=on_bytes)
            if arrived != entry.sha256:
                raise OSError(
                    f"{entry.relpath} arrived as {arrived} but is registered as "
                    f"{entry.sha256}; nothing was moved. Try again once its "
                    "folder's next scan has re-read it."
                )
        elif os.path.getsize(copied) != os.path.getsize(
            os.path.join(plan.source_path, entry.relpath[len(old_prefix) :])
        ):
            raise OSError(f"{entry.relpath} did not copy whole; nothing was moved.")


def _rename(hub: HubDatabase, plan: DirectoryMovePlan) -> None:
    # A rename onto an *empty* directory that appeared since the check would
    # replace it on POSIX; a non-empty one fails. The window is the two syscalls
    # between the check and the rename.
    _refuse_if_taken(plan)
    os.rename(plan.source_path, plan.destination_path)
    try:
        _repoint(hub, plan)
    except (sqlite3.Error, RepointLost):
        logger.error(
            "Could not repoint the rows under %s; renaming %s back.",
            plan.source_path,
            plan.destination_path,
            exc_info=True,
        )
        try:
            os.rename(plan.destination_path, plan.source_path)
        except OSError:
            # The one state this module cannot repair by itself: the tree is at
            # the destination and the rows still name the source. A rescan of
            # both folders re-links the files by content.
            logger.critical(
                "Could not rename %s back to %s after the repoint failed. The "
                "folder is at the new place and its rows still name the old one; "
                "the next scan of both folders re-links it.",
                plan.destination_path,
                plan.source_path,
                exc_info=True,
            )
        raise


def _copy(
    hub: HubDatabase,
    plan: DirectoryMovePlan,
    should_cancel: Optional[Callable[[], bool]],
    on_bytes: Optional[Callable[[int], None]] = None,
) -> Optional[str]:
    """The cross-drive move. Returns None when cancelled, else the detail."""
    cancelled = should_cancel or (lambda: False)
    partial = plan.destination_path + PARTIAL_SUFFIX
    # Each source file's mtime as its copy began: a rewrite at the same size is
    # invisible to the size comparison below, and the source is then deleted.
    copied_mtimes: dict[str, int] = {}

    def copy_one(source: str, destination: str):
        if cancelled():
            raise _CopyCancelled()
        copied_mtimes[source] = os.stat(source).st_mtime_ns
        # Chunked rather than ``shutil.copy2`` so one large model in the folder
        # fills the progress bar as it copies. Mode and times are kept the same.
        copy_and_digest(source, destination, on_bytes=on_bytes)
        return destination

    # Made before the handlers below can discard it: a tree that appeared at
    # this name since the plan is somebody else's, and the mkdir refuses it.
    os.mkdir(partial)
    try:
        shutil.copytree(
            plan.source_path,
            partial,
            symlinks=True,
            copy_function=copy_one,
            dirs_exist_ok=True,
        )
        _verify(plan, partial, on_bytes=on_bytes)
        if cancelled():
            raise _CopyCancelled()
        _refuse_if_taken(plan)
        os.rename(partial, plan.destination_path)
    except _CopyCancelled:
        logger.info(
            "Folder move of %s cancelled; removing %s.", plan.source_path, partial
        )
        discard_partial_tree(partial)
        return None
    except BaseException:
        discard_partial_tree(partial)
        raise

    try:
        _repoint(hub, plan)
    except (sqlite3.Error, RepointLost):
        logger.error(
            "Could not repoint the rows under %s; removing the copy at %s. The "
            "source is untouched.",
            plan.source_path,
            plan.destination_path,
            exc_info=True,
        )
        discard_partial_tree(plan.destination_path)
        raise

    # The verify covers the catalogued files the plan knew. Anything written into
    # the source since the copy began (a preview a running job saved, a download)
    # is not in the copy, so the source is removed only when every file in it
    # arrived at the same size and none was written to since its copy began.
    source_files = _tree_listing(plan.source_path)
    copied_files = _tree_listing(plan.destination_path)
    changed = sorted(
        relpath
        for relpath, size in source_files.items()
        if copied_files.get(relpath) != size
        or _rewritten(os.path.join(plan.source_path, relpath), copied_mtimes)
    )
    if changed:
        logger.error(
            "Moved %s to %s, but kept the original: %d file(s) changed or appeared "
            "in it during the copy (%s). The rows name the new copy; nothing was "
            "deleted.",
            plan.source_path,
            plan.destination_path,
            len(changed),
            ", ".join(changed[:5]),
        )
        return (
            f"Moved, but the original was kept: {len(changed)} file(s) in it "
            "changed during the copy."
        )
    # Exactly the files just compared, never ``rmtree``: a file written after
    # the comparison is in no copy, so it and the directories holding it stay.
    try:
        for relpath in source_files:
            os.unlink(os.path.join(plan.source_path, relpath))
    except OSError as exc:
        logger.error(
            "Moved %s to %s, but could not remove the original: %s. The rows name "
            "the new copy; what is left at the source is a duplicate.",
            plan.source_path,
            plan.destination_path,
            exc,
        )
        return f"Moved, but the original could not be removed: {exc}"
    # Only directories the copy has: an empty one made in the source during the
    # move holds no file to compare, and is as much the owner's as a file is.
    copied_dirs = {
        os.path.relpath(root, plan.destination_path)
        for root, _dirs, _names in os.walk(plan.destination_path)
    }
    for root, _dirs, _names in os.walk(plan.source_path, topdown=False):
        if os.path.relpath(root, plan.source_path) not in copied_dirs:
            continue
        try:
            os.rmdir(root)
        except OSError as exc:
            logger.debug("Left %s in place: %s", root, exc)
    if os.path.lexists(plan.source_path):
        logger.error(
            "Moved %s to %s, but files or folders made in the original during the "
            "move were left there; they are not in the copy.",
            plan.source_path,
            plan.destination_path,
        )
        return (
            "Moved, but files or folders made in the original during the move "
            "were left there."
        )
    return ""


def execute_directory_move(
    hub: HubDatabase,
    plan: DirectoryMovePlan,
    *,
    should_cancel: Optional[Callable[[], bool]] = None,
    on_bytes: Optional[Callable[[int], None]] = None,
) -> MoveOutcome:
    """Carry out a planned directory move. Never raises for a failed move.

    *on_bytes* is called with a byte count as each chunk is copied, and again
    as each hashed model file is read back to verify. A rename reports nothing.

    Returns:
        One outcome for the directory: ``moved`` (``detail`` set when the
        original could not be removed), ``cancelled`` with nothing moved, or
        ``failed`` with the source untouched.
    """
    try:
        if plan.same_drive:
            _rename(hub, plan)
            detail = ""
        else:
            detail = _copy(hub, plan, should_cancel, on_bytes)
            if detail is None:
                return MoveOutcome(
                    plan.source_folder_id,
                    plan.source_subpath,
                    STATUS_CANCELLED,
                    "Cancelled before the folder was moved; nothing changed.",
                )
    except (OSError, sqlite3.Error, RepointLost) as exc:
        logger.error(
            "Moving folder %s to %s failed: %s. The source is untouched.",
            plan.source_path,
            plan.destination_path,
            exc,
            exc_info=True,
        )
        return MoveOutcome(
            plan.source_folder_id, plan.source_subpath, STATUS_FAILED, str(exc)
        )
    logger.info(
        "Moved folder %s to %s (%d catalogued file(s), %s).",
        plan.source_path,
        plan.destination_path,
        len(plan.catalogued),
        "renamed" if plan.same_drive else "copied",
    )
    return MoveOutcome(
        plan.source_folder_id, plan.source_subpath, STATUS_MOVED, detail or None
    )
