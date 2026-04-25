import os
import time

from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models.import_folder import ImportFolder
from pixlstash.utils.image_processing.video_utils import VideoUtils

from .base_task_finder import BaseTaskFinder
from .watch_folder_import_task import (
    WatchFolderImportTask,
    load_watch_folders,
    persist_watch_folders,
)


class MissingWatchFolderImportFinder(BaseTaskFinder):
    """Find newly modified files in watch folders and create import tasks.

    Watch folder entries are read from the ``import_folder`` table. The finder
    falls back to legacy ``watch_folders`` entries in ``server-config.json``
    when no DB-backed folders exist yet.

    Each folder entry includes:

        folder (str): Absolute path to the directory to monitor recursively.
        delete_after_import (bool): When True, source files are deleted from
            the watch folder after a successful import. Defaults to False.
        last_checked (float): Unix timestamp of the last scan. Updated
            automatically after each scan; do not set this manually.

    Example legacy config entry::

        { "folder": "/home/user/downloads/photos", "delete_after_import": false }
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

    def __init__(self, database, config_path: str):
        super().__init__()
        self._db = database
        self._config_path = config_path

    def finder_name(self) -> str:
        return "MissingWatchFolderImportFinder"

    def find_task(self):
        db_folders = self._load_db_watch_folders()
        if db_folders:
            return self._find_task_from_db(db_folders)
        return self._find_task_from_config()

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
                    try:
                        mtime = os.path.getmtime(file_path)
                    except OSError:
                        continue
                    if not self._is_supported_file(file_path):
                        continue
                    if mtime > last_checked:
                        total_candidates += 1
                        candidate_files.append(
                            {
                                "file_path": file_path,
                                "delete_after_import": delete_after_import,
                                "import_source_folder": folder,
                            }
                        )
                    if mtime > latest_seen:
                        latest_seen = mtime

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
        )

    def _find_task_from_config(self):
        watch_folders = load_watch_folders(self._config_path)
        if not watch_folders:
            return None

        now_ts = time.time()
        candidate_files = []
        total_candidates = 0
        updated = False

        for entry in watch_folders:
            folder = entry.get("folder")
            last_checked = float(entry.get("last_checked") or 0)
            delete_after_import = bool(entry.get("delete_after_import", False))

            if not folder or not os.path.isdir(folder):
                continue

            latest_seen = last_checked
            for root, _, files in os.walk(folder):
                for file_name in files:
                    file_path = os.path.join(root, file_name)
                    try:
                        mtime = os.path.getmtime(file_path)
                    except OSError:
                        continue
                    if not self._is_supported_file(file_path):
                        continue
                    if mtime > last_checked:
                        total_candidates += 1
                        candidate_files.append(
                            {
                                "file_path": file_path,
                                "delete_after_import": delete_after_import,
                                "import_source_folder": folder,
                            }
                        )
                    if mtime > latest_seen:
                        latest_seen = mtime

            entry["last_checked"] = max(latest_seen, now_ts)
            updated = True

        if updated and not candidate_files:
            persist_watch_folders(self._config_path, watch_folders)
            return None

        if not candidate_files:
            return None

        return WatchFolderImportTask(
            database=self._db,
            config_path=self._config_path,
            candidate_files=candidate_files,
            updated_watch_folders=watch_folders,
            total_candidates=total_candidates,
        )

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
