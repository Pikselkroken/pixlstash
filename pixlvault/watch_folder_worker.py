import json
import os
import threading
import time
from typing import List, Dict

from sqlmodel import Session, select

from pixlvault.database import DBPriority
from pixlvault.event_types import EventType
from pixlvault.pixl_logging import get_logger
from pixlvault.picture_utils import PictureUtils
from pixlvault.worker_registry import BaseWorker, WorkerType
from pixlvault.db_models.picture import Picture


logger = get_logger(__name__)


class WatchFolderWorker(BaseWorker):
    INTERVAL = 20

    _supported_image_exts = {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".bmp",
        ".heic",
        ".heif",
    }

    _config_path = None
    _config_lock = threading.Lock()

    @classmethod
    def configure(cls, config_path: str):
        cls._config_path = config_path

    def worker_type(self) -> WorkerType:
        return WorkerType.WATCH_FOLDERS

    def _load_watch_folders(self) -> List[Dict]:
        if not self._config_path or not os.path.exists(self._config_path):
            return []
        with self._config_lock:
            try:
                with open(self._config_path, "r") as f:
                    config = json.load(f)
                return list(config.get("watch_folders", []) or [])
            except Exception as exc:
                logger.error("Failed to read watch_folders: %s", exc)
                return []

    def _persist_watch_folders(self, watch_folders: List[Dict]):
        if not self._config_path:
            return
        with self._config_lock:
            try:
                config = {}
                if os.path.exists(self._config_path):
                    with open(self._config_path, "r") as f:
                        config = json.load(f)
                config["watch_folders"] = watch_folders
                with open(self._config_path, "w") as f:
                    json.dump(config, f, indent=2)
            except Exception as exc:
                logger.error("Failed to persist watch_folders: %s", exc)

    def _is_supported_file(self, file_path: str) -> bool:
        ext = os.path.splitext(file_path)[1].lower()
        if ext in self._supported_image_exts:
            return True
        return PictureUtils.is_video_file(file_path)

    def _run(self):
        logger.info("WatchFolderWorker started.")
        while not self._stop.is_set():
            try:
                watch_folders = self._load_watch_folders()
                if not watch_folders:
                    self._wait()
                    continue

                new_pictures = []
                delete_paths = []
                updated = False
                now_ts = time.time()
                total_candidates = 0

                for entry in watch_folders:
                    folder = entry.get("folder")
                    last_checked = float(entry.get("last_checked") or 0)
                    delete_after_import = bool(entry.get("delete_after_import", False))

                    if not folder:
                        continue
                    if not os.path.isdir(folder):
                        logger.warning("Watch folder does not exist: %s", folder)
                        continue

                    latest_seen = last_checked
                    candidate_files = []

                    for root, _, files in os.walk(folder):
                        for file_name in files:
                            logger.debug(f"Scanning file {file_name} in {folder}")
                            file_path = os.path.join(root, file_name)
                            try:
                                mtime = os.path.getmtime(file_path)
                            except OSError:
                                continue
                            if not self._is_supported_file(file_path):
                                continue
                            if mtime > last_checked:
                                candidate_files.append(file_path)
                            else:
                                logger.debug(
                                    f"File {file_name} not modified since last check."
                                )
                            if mtime > latest_seen:
                                latest_seen = mtime

                    total_candidates += len(candidate_files)

                    for file_path in candidate_files:
                        logger.debug(f"# Found new file {file_path}")
                        try:
                            pixel_sha = PictureUtils.calculate_hash_from_file_path(
                                file_path
                            )
                        except Exception as exc:
                            logger.warning(
                                "Failed to hash watched file %s: %s", file_path, exc
                            )
                            continue

                        def find_existing(session: Session, pixel_sha: str):
                            return session.exec(
                                select(Picture).where(Picture.pixel_sha == pixel_sha)
                            ).first()

                        existing = self._db.run_task(find_existing, pixel_sha)
                        if existing:
                            logger.debug(
                                "Already have picture with sha %s, skipping", pixel_sha
                            )
                            continue

                        try:
                            pic = PictureUtils.create_picture_from_file(
                                image_root_path=self._db.image_root,
                                source_file_path=file_path,
                                pixel_sha=pixel_sha,
                            )
                            new_pictures.append(pic)
                            if delete_after_import:
                                delete_paths.append(file_path)
                        except Exception as exc:
                            logger.warning(
                                "Failed to import watched file %s: %s", file_path, exc
                            )

                    entry["last_checked"] = max(latest_seen, now_ts)
                    updated = True

                if new_pictures:

                    def insert_pictures(session: Session, pictures: List[Picture]):
                        session.add_all(pictures)
                        session.commit()
                        for pic in pictures:
                            session.refresh(pic)
                        return pictures

                    self._db.run_task(
                        insert_pictures,
                        new_pictures,
                        priority=DBPriority.IMMEDIATE,
                    )
                    logger.info(
                        "Added %d new pictures from watch folders.", len(new_pictures)
                    )
                    self._notify_others(EventType.CHANGED_PICTURES)

                    if delete_paths:
                        for file_path in delete_paths:
                            try:
                                os.remove(file_path)
                            except Exception as exc:
                                logger.warning(
                                    "Failed to delete watched file %s: %s",
                                    file_path,
                                    exc,
                                )

                self._set_progress(
                    label="watch_folder_import",
                    current=len(new_pictures),
                    total=total_candidates,
                )

                if updated:
                    self._persist_watch_folders(watch_folders)

                if not new_pictures:
                    self._wait()
            except Exception as exc:
                import traceback

                logger.error(
                    "WatchFolderWorker exiting due to error: %s\n%s",
                    exc,
                    traceback.format_exc(),
                )
                break
        logger.info("WatchFolderWorker stopped.")
