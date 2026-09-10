"""Finder that queues scan tasks for reference folders and the library root.

The library's own picture root is scanned by the same task under the folder id
``None``: since v1.11 a layout turns the root into a human-readable folder tree
the owner reorganises by hand, and a rename nobody follows is a row the purge
sweep deletes an hour later, tags and all. The root has no ``ReferenceFolder``
row, so its schedule lives on this finder rather than in a column.
"""

import os
import time
from typing import Optional

from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models.folder_mapping_commit import (
    STATE_ABANDONED,
    STATE_DEFERRED,
    STATE_DONE,
    STATE_PENDING,
    STATE_SUPERSEDED,
    FolderMappingCommit,
)
from pixlstash.db_models.picture import Picture
from pixlstash.db_models.reference_folder import ReferenceFolder, ReferenceFolderStatus
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task_finder import BaseTaskFinder
from pixlstash.tasks.reference_folder_scan_task import ReferenceFolderScanTask
from pixlstash.utils.reference_folder_validator import (
    validate_reference_folder_accessible,
    validate_reference_folder_path,
)

logger = get_logger(__name__)

# Re-scan active folders at most this often (seconds).
_RESCAN_INTERVAL_S: float = 300.0
#: How long a closed import gate is left alone before it is asked again. The
#: planner sweeps every finder up to twenty times a second and the local
#: importer wakes it after every chunk, so without this the gate's query
#: would run on every sweep for the whole of a pending import.
_GATE_RETRY_S: float = 5.0
# Retry mount_error folders quickly so transient bind/access glitches clear
# from UI without waiting for a full active re-scan interval.
_MOUNT_ERROR_RETRY_INTERVAL_S: float = 15.0


class ReferenceFolderScanFinder(BaseTaskFinder):
    """Discover reference folders that need scanning and queue one scan task at a time.

    Iterates all reference folders in the database and queues a
    :class:`ReferenceFolderScanTask` for the first folder that is either:

    - ``pending_mount`` - has never been scanned since being added; or
    - ``active`` - was last scanned more than ``_RESCAN_INTERVAL_S`` seconds ago.

    Folders with ``mount_error`` status are re-attempted on a shorter interval
    so that a previously missing mount can be picked up quickly without
    requiring a restart.

    Path-map resolution is applied to translate stored host paths to container
    paths before Phase-2 validation (isdir / readable check).
    """

    def __init__(self, database, path_mapper, image_root=None) -> None:
        """Initialize the finder.

        Args:
            database: The application database instance.
            path_mapper: A :class:`~pixlstash.utils.path_mapper.PathMapper`
                instance used to translate host paths to container paths
                in Docker deployments.
            image_root: The library's own picture root, scanned like a
                reference folder under the id ``None``. ``None`` disables the
                root scan (tests that build the finder without a root).
        """
        super().__init__()
        self._db = database
        self._path_mapper = path_mapper
        self._image_root = os.path.abspath(image_root) if image_root else None
        # None means "due now": the first scan after boot follows whatever the
        # owner renamed while the app was closed.
        self._root_last_scanned: float | None = None
        self._root_scanned_once = False
        # When the import gate last said no, and when to ask it again.
        self._gate_retry_at: float | None = None

    def mark_root_due(self) -> None:
        """Ask for the library root to be rescanned on the next planning cycle."""
        self._root_last_scanned = None
        self._gate_retry_at = None

    def root_scan_complete(self) -> bool:
        """Whether the library root has been scanned at least once since boot.

        ``MissingFilePurgeFinder`` waits on this: until the first root scan has
        matched renamed files with their rows, a vanished path is not yet known
        to be a deletion. Always ``True`` when there is no root to scan.
        """
        return self._image_root is None or self._root_scanned_once

    def _note_root_scanned(self) -> None:
        self._root_scanned_once = True

    def finder_name(self) -> str:
        return "ReferenceFolderScanFinder"

    def find_task(self):
        now = time.time()

        def _sort_key(rf: ReferenceFolder) -> tuple[int, float]:
            # Prioritize non-active folders (pending/mount_error) so new or
            # recovered mounts are scanned before routine active re-scans.
            priority = 0 if rf.status != ReferenceFolderStatus.ACTIVE else 1
            last_scanned = float(rf.last_scanned) if rf.last_scanned else 0.0
            return (priority, last_scanned)

        def fetch_folders(session):
            return list(session.exec(select(ReferenceFolder)).all())

        folders: list[ReferenceFolder] = self._db.run_immediate_read_task(fetch_folders)

        for rf in sorted(folders, key=_sort_key):
            last_scanned = float(rf.last_scanned) if rf.last_scanned else 0.0
            if rf.status == ReferenceFolderStatus.PENDING_MOUNT:
                needs_scan = True
            elif rf.status == ReferenceFolderStatus.MOUNT_ERROR:
                needs_scan = (
                    rf.last_scanned is None
                    or (now - last_scanned) >= _MOUNT_ERROR_RETRY_INTERVAL_S
                )
            else:
                needs_scan = (
                    rf.last_scanned is None
                    or (now - last_scanned) >= _RESCAN_INTERVAL_S
                )
            if not needs_scan:
                continue

            # Phase-2 path validation:
            # 1. Resolve host→container mapping.
            resolved = self._path_mapper.resolve(rf.folder)

            # 2. Apply blocklist to the resolved path (defence in depth).
            blocklist_error = validate_reference_folder_path(resolved)
            if blocklist_error:
                logger.warning(
                    "Reference folder %s (resolved: %s) blocked after path-map: %s",
                    rf.folder,
                    resolved,
                    blocklist_error,
                )
                self._mark_mount_error(rf.id)
                continue

            # 3. Accessibility check.
            access_error = validate_reference_folder_accessible(resolved)
            if access_error:
                logger.warning(
                    "Reference folder %s inaccessible: %s", rf.folder, access_error
                )
                self._mark_mount_error(rf.id)
                continue

            # If a folder previously failed mount/access checks, reflect that
            # recovery immediately in UI while it waits for the scan task.
            if rf.status == ReferenceFolderStatus.MOUNT_ERROR:
                self._mark_pending_mount(rf.id)

            return ReferenceFolderScanTask(
                database=self._db,
                folder_id=rf.id,
                folder_path=rf.folder,
                resolved_path=resolved,
                other_resolved_paths=frozenset(
                    self._path_mapper.resolve(other.folder)
                    for other in folders
                    if other.id != rf.id
                ),
            )

        return self._root_task(folders, now)

    def first_import_answered(self) -> bool:
        """Whether the root may be indexed yet.

        A library that holds no picture and has never had a folder-mapping
        commit is one whose owner has not been asked what the pictures in its
        folder are. The app asks when it loads an empty library over a folder
        that holds pictures - the first-run offer, and "Add a library" - and
        the boot-time root scan used to answer first: a small library was
        indexed, tags and all, before the screen came up, so the grid was no
        longer empty and the questions never appeared. The root scan waits
        for the answer.

        A ``local_import`` commit record counts only once it has **settled**:
        ``done``, or ``deferred`` ("organise later", which is an answer -
        index everything, map nothing). A ``pending`` one is the commit still
        running, so it is the question being answered rather than the answer,
        and treating it as one puts the 300-second root scan into a race with
        it: the scan builds its own rows with the sidecar probe, wins, and the
        commit's ``insert()`` reuses them without ever applying the owner's
        caption choices. ``abandoned`` is "bring nothing in" and ``superseded``
        was replaced by a newer record, so neither is an answer either. A
        ``reference`` commit is not one at all: it registers some other folder
        and says nothing about the root's own pictures.

        The **newest** ``local_import`` record decides, because records are
        kept and an older answer must not outrank a newer one: a ``done``
        import followed by an aborted one is an owner who declined the second
        batch, and reading the old ``done`` as the answer would scan in the
        very files just aborted. A ``pending`` or ``abandoned`` newest record
        wins over the picture row for the same reason: an unsettled
        ``local_import`` commits every chunk, so after the first one the
        library holds pictures the owner has not answered for. A running
        commit wakes the planner while its record is still ``pending``, and
        an aborted one leaves its chunks indexed behind ``abandoned``, and a
        ``superseded`` newest one was replaced by a reference commit with its
        chunks just as unanswered. Only with no ``local_import`` record at all
        does a picture row count: that library was filled some other way and
        is scanned as before.

        The two facts are read in **one statement** so they describe one
        snapshot: this read runs outside the writer queue, so across separate
        statements a chunk committing in between shows no ``pending`` record
        and a picture from that very commit, which would answer True with the
        import still running.

        Not cached: a finder lives as long as the process, and a later import
        must close the gate again the moment its record is pending. The read
        is one statement, and `_root_task` asks only when a root scan is
        otherwise due and, while the answer is no, no more than once per
        `_GATE_RETRY_S`.
        """

        def read(session: Session) -> tuple[Optional[str], bool]:
            newest = (
                select(FolderMappingCommit.state)
                .where(FolderMappingCommit.mode == "local_import")
                .order_by(FolderMappingCommit.id.desc())
                .limit(1)
                .scalar_subquery()
            )
            return session.exec(select(newest, select(Picture.id).exists())).one()

        newest_state, has_picture = self._db.run_immediate_read_task(read)
        if newest_state in (STATE_PENDING, STATE_ABANDONED, STATE_SUPERSEDED):
            # Superseded is a pending record a newer commit of either mode
            # replaced. A newer local_import would be the newest row itself,
            # so a superseded newest one was replaced by a reference commit
            # and its chunks are as unanswered as a pending one's.
            return False
        return newest_state in (STATE_DONE, STATE_DEFERRED) or has_picture

    def _root_task(self, folders: list[ReferenceFolder], now: float):
        """The library-root scan, when it is due. Folders go first: a root scan
        walks the whole library, so it must not push a pending mount back."""
        if self._image_root is None or not os.path.isdir(self._image_root):
            return None
        last = self._root_last_scanned
        if last is not None and (now - last) < _RESCAN_INTERVAL_S:
            return None
        # After the interval check, so the gate's one query runs only when a
        # scan would otherwise be handed out; and no more than once per
        # `_GATE_RETRY_S` while it says no, since a closed gate never stamps
        # `_root_last_scanned` and the interval check above would not throttle
        # it.
        if self._gate_retry_at is not None and now < self._gate_retry_at:
            return None
        if not self.first_import_answered():
            self._gate_retry_at = now + _GATE_RETRY_S
            return None
        self._gate_retry_at = None
        # Stamped when the task is handed out, not when it finishes, so a slow
        # scan is not queued a second time behind itself.
        self._root_last_scanned = now
        return ReferenceFolderScanTask(
            database=self._db,
            folder_id=None,
            folder_path=self._image_root,
            resolved_path=self._image_root,
            # A reference folder registered inside the root is that folder's
            # scan to index, not the root's.
            other_resolved_paths=frozenset(
                self._path_mapper.resolve(rf.folder) for rf in folders
            ),
            on_root_scanned=self._note_root_scanned,
        )

    def _mark_mount_error(self, folder_id: int) -> None:
        def update(session: Session) -> None:
            rf = session.get(ReferenceFolder, folder_id)
            if rf is None:
                return
            rf.status = ReferenceFolderStatus.MOUNT_ERROR
            rf.last_scanned = time.time()
            session.add(rf)
            session.commit()

        self._db.run_task(update, priority=DBPriority.MEDIUM)

    def _mark_pending_mount(self, folder_id: int) -> None:
        def update(session: Session) -> None:
            rf = session.get(ReferenceFolder, folder_id)
            if rf is None:
                return
            rf.status = ReferenceFolderStatus.PENDING_MOUNT
            rf.last_scanned = None
            session.add(rf)
            session.commit()

        self._db.run_task(update, priority=DBPriority.MEDIUM)
