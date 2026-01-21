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

    def _run(self):
        logger.info("WatchFolderWorker started.")
        while not self._stop.is_set():
            try:
                watch_folders = self._load_watch_folders()
                if not watch_folders:
                    self._wait()
                    continue

                new_pictures = []
                updated = False
                now_ts = time.time()

                for entry in watch_folders:
                    folder = entry.get("folder")
                    last_checked = float(entry.get("last_checked") or 0)

                    if not folder:
                        continue
                    if not os.path.isdir(folder):
                        logger.warning("Watch folder does not exist: %s", folder)
                        continue

                    latest_seen = last_checked
                    candidate_files = []

                    for root, _, files in os.walk(folder):
                        for file_name in files:
                            file_path = os.path.join(root, file_name)
                            try:
                                mtime = os.path.getmtime(file_path)
                            except OSError:
                                continue
                            if mtime > last_checked:
                                candidate_files.append(file_path)
                            if mtime > latest_seen:
                                latest_seen = mtime

                    for file_path in candidate_files:
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
                            continue

                        try:
                            pic = PictureUtils.create_picture_from_file(
                                image_root_path=self._db.image_root,
                                source_file_path=file_path,
                                pixel_sha=pixel_sha,
                            )
                            new_pictures.append(pic)
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
                    self._notify_others(EventType.CHANGED_PICTURES)

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
