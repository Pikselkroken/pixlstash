"""Finder that queues scan tasks for active and pending-mount reference folders."""

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


class ReferenceFolderScanFinder(BaseTaskFinder):
    """Discover reference folders that need scanning and queue one scan task at a time.

    Iterates all reference folders in the database and queues a
    :class:`ReferenceFolderScanTask` for the first folder that is either:

    - ``pending_mount`` — has never been scanned since being added; or
    - ``active`` — was last scanned more than ``_RESCAN_INTERVAL_S`` seconds ago.

    Folders with ``mount_error`` status are re-attempted at the same interval
    so that a previously missing mount can be picked up after a fix without
    requiring a restart.

    Path-map resolution is applied to translate stored host paths to container
    paths before Phase-2 validation (isdir / readable check).
    """

    def __init__(self, database, path_mapper) -> None:
        """Initialize the finder.

        Args:
            database: The application database instance.
            path_mapper: A :class:`~pixlstash.utils.path_mapper.PathMapper`
                instance used to translate host paths to container paths
                in Docker deployments.
        """
        super().__init__()
        self._db = database
        self._path_mapper = path_mapper

    def finder_name(self) -> str:
        return "ReferenceFolderScanFinder"

    def find_task(self):
        now = time.time()

        def fetch_folders(session):
            return list(session.exec(select(ReferenceFolder)).all())

        folders: list[ReferenceFolder] = self._db.run_immediate_read_task(fetch_folders)
        if not folders:
            return None

        for rf in folders:
            needs_scan = (
                rf.status == ReferenceFolderStatus.PENDING_MOUNT
                or rf.last_scanned is None
                or (now - float(rf.last_scanned)) >= _RESCAN_INTERVAL_S
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

        return None

    def _mark_mount_error(self, folder_id: int) -> None:
        def update(session: Session) -> None:
            rf = session.get(ReferenceFolder, folder_id)
            if rf is None:
                return
            rf.status = ReferenceFolderStatus.MOUNT_ERROR
            rf.last_scanned = time.time()
            session.add(rf)
            session.commit()

        self._db.run_task(update, priority=DBPriority.NORMAL)
