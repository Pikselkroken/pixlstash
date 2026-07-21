import os
import threading
import time

from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models.import_folder import ImportFolder
from pixlstash.utils.image_processing.video_utils import VideoUtils

from .base_task_finder import BaseTaskFinder
from .watch_folder_import_task import WatchFolderImportTask


class MissingWatchFolderImportFinder(BaseTaskFinder):
    """Find newly modified files in watch folders and create import tasks.

    Watch folder entries are read from the ``import_folder`` table.

    Each folder entry includes:

        folder (str): Absolute path to the directory to monitor recursively.
        delete_after_import (bool): When True, source files are deleted from
            the watch folder after a successful import. Defaults to False.
        last_checked (float): Unix timestamp of the last scan. Updated
            automatically after each scan; do not set this manually.

    """

    _supported_image_exts = {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".bmp",
        ".heic",
        ".heif",
        ".avif",
    }

    def __init__(self, database):
        super().__init__()
        self._db = database
        # Normalized paths seen in a previous scan cycle.  Any path not in this
        # set is treated as newly discovered regardless of its mtime/ctime, which
        # handles copy tools (e.g. shutil.copy2 on Windows) that preserve old
        # timestamps on the destination file.
        #
        # This set is read/written by the finder on the WorkPlanner thread and
        # also mutated by WatchFolderImportTask (via ``discard_seen_paths``) on
        # the TaskRunner thread when a candidate fails to import, so all access
        # is guarded by ``_seen_lock`` to avoid a concurrent-mutation race.
        self._seen_file_paths: set[str] = set()
        self._seen_lock = threading.Lock()

    def finder_name(self) -> str:
        return "MissingWatchFolderImportFinder"

    def find_task(self):
        db_folders = self._load_db_watch_folders()
        if not db_folders:
            return None
        return self._find_task_from_db(db_folders)

    def _load_db_watch_folders(self) -> list[ImportFolder]:
        def fetch(session: Session):
            return session.exec(select(ImportFolder).order_by(ImportFolder.id)).all()

        try:
            folders = self._db.run_task(fetch, priority=DBPriority.IMMEDIATE)
        except Exception:
            return []
        return list(folders or [])

    def _find_task_from_db(self, watch_folders: list[ImportFolder]):
        now_ts = time.time()
        candidate_files = []
        total_candidates = 0
        last_checked_updates = {}

        for entry in watch_folders:
            if entry.id is None:
                continue
            folder = entry.folder
            last_checked = float(entry.last_checked or 0)
            delete_after_import = bool(entry.delete_after_import)

            if not folder or not os.path.isdir(folder):
                continue

            latest_seen = last_checked
            for root, _, files in os.walk(folder):
                for file_name in files:
                    file_path = os.path.join(root, file_name)
                    normalized = os.path.normcase(os.path.abspath(file_path))
                    try:
                        mtime = os.path.getmtime(file_path)
                        ctime = os.path.getctime(file_path)
                    except OSError:
                        continue
                    if not self._is_supported_file(file_path):
                        continue
                    # Some copy workflows preserve mtime from the source file,
                    # so rely on the newer of mtime/ctime when deciding whether
                    # this file is new relative to last_checked.
                    seen_ts = max(mtime, ctime)
                    # A path not seen in any previous scan cycle is always a
                    # candidate, even when both timestamps predate last_checked.
                    # This handles shutil.copy2 on Windows, which copies ctime
                    # from the source, making it equally old as mtime.
                    with self._seen_lock:
                        is_new_path = normalized not in self._seen_file_paths
                        self._seen_file_paths.add(normalized)
                    if seen_ts > last_checked or is_new_path:
                        total_candidates += 1
                        candidate_files.append(
                            {
                                "file_path": file_path,
                                "delete_after_import": delete_after_import,
                                "import_source_folder": folder,
                            }
                        )
                    if seen_ts > latest_seen:
                        latest_seen = seen_ts

            last_checked_updates[int(entry.id)] = max(latest_seen, now_ts)

        if last_checked_updates and not candidate_files:
            self._persist_db_last_checked(last_checked_updates)
            return None

        if not candidate_files:
            return None

        return WatchFolderImportTask(
            database=self._db,
            candidate_files=candidate_files,
            total_candidates=total_candidates,
            last_checked_updates=last_checked_updates,
            finder=self,
        )

    def discard_seen_paths(self, file_paths: list[str]) -> None:
        """Forget paths so a later scan re-discovers them as new.

        Called by :class:`WatchFolderImportTask` for candidate files that failed
        to process (hash or import error). A file is normally added to
        ``_seen_file_paths`` at discovery time; if it then fails to import and
        also carries a copy-preserved old mtime (``seen_ts <= last_checked``),
        it would never become a candidate again. Removing it here makes the
        failure transient: the next scan sees the path as new and retries it.
        """
        if not file_paths:
            return
        normalized = {
            os.path.normcase(os.path.abspath(path)) for path in file_paths if path
        }
        if not normalized:
            return
        with self._seen_lock:
            self._seen_file_paths.difference_update(normalized)

    def _persist_db_last_checked(self, updates: dict[int, float]):
        def update(session: Session, values: dict[int, float]):
            folders = session.exec(
                select(ImportFolder).where(ImportFolder.id.in_(list(values.keys())))
            ).all()
            for folder in folders:
                next_ts = values.get(int(folder.id))
                if next_ts is None:
                    continue
                folder.last_checked = float(next_ts)
                session.add(folder)
            session.commit()

        self._db.run_task(update, updates, priority=DBPriority.IMMEDIATE)

    def _is_supported_file(self, file_path: str) -> bool:
        ext = os.path.splitext(file_path)[1].lower()
        if ext in self._supported_image_exts:
            return True
        return VideoUtils.is_video_file(file_path)
