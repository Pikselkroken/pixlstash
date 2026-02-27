import os
import time

from pixlvault.picture_utils import PictureUtils

from .base_task_finder import BaseTaskFinder
from .watch_folder_import_task import (
    WatchFolderImportTask,
    load_watch_folders,
    persist_watch_folders,
)


class MissingWatchFolderImportsFinder(BaseTaskFinder):
    """Find newly modified files in watch folders and create import tasks."""

    _supported_image_exts = {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".bmp",
        ".heic",
        ".heif",
    }

    def __init__(self, database, config_path: str):
        self._db = database
        self._config_path = config_path

    def finder_name(self) -> str:
        return "MissingWatchFolderImportsFinder"

    def find_task(self):
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

    def _is_supported_file(self, file_path: str) -> bool:
        ext = os.path.splitext(file_path)[1].lower()
        if ext in self._supported_image_exts:
            return True
        return PictureUtils.is_video_file(file_path)
