"""Finder that queues scan tasks for reference folders and the library root.

The library's own picture root is scanned by the same task under the folder id
``None``: since v1.11 a layout turns the root into a human-readable folder tree
the owner reorganises by hand, and a rename nobody follows is a row the purge
sweep deletes an hour later, tags and all. The root has no ``ReferenceFolder``
row, so its schedule lives on this finder rather than in a column.
"""

import os
import time

from sqlmodel import Session, select

from pixlstash.database import DBPriority
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

    def mark_root_due(self) -> None:
        """Ask for the library root to be rescanned on the next planning cycle."""
        self._root_last_scanned = None

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

    def _root_task(self, folders: list[ReferenceFolder], now: float):
        """The library-root scan, when it is due. Folders go first: a root scan
        walks the whole library, so it must not push a pending mount back."""
        if self._image_root is None or not os.path.isdir(self._image_root):
            return None
        last = self._root_last_scanned
        if last is not None and (now - last) < _RESCAN_INTERVAL_S:
            return None
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
