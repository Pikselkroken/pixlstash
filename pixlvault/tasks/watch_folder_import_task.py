import json
import os
import threading
from datetime import datetime

from sqlmodel import Session, select

from pixlvault.database import DBPriority
from pixlvault.db_models.picture import Picture
from pixlvault.picture_utils import PictureUtils
from pixlvault.pixl_logging import get_logger
from pixlvault.stacking import (
    assign_picture_to_stack,
    get_or_create_stack_for_picture,
    parse_stack_tags_from_filename,
)
from pixlvault.task_runner import BaseTask


logger = get_logger(__name__)

_CONFIG_LOCK = threading.Lock()


def load_watch_folders(config_path: str) -> list[dict]:
    if not config_path or not os.path.exists(config_path):
        return []
    with _CONFIG_LOCK:
        try:
            with open(config_path, "r") as handle:
                config = json.load(handle)
            return list(config.get("watch_folders", []) or [])
        except Exception as exc:
            logger.error("Failed to read watch_folders: %s", exc)
            return []


def persist_watch_folders(config_path: str, watch_folders: list[dict]):
    if not config_path:
        return
    with _CONFIG_LOCK:
        try:
            config = {}
            if os.path.exists(config_path):
                with open(config_path, "r") as handle:
                    config = json.load(handle)
            config["watch_folders"] = watch_folders
            with open(config_path, "w") as handle:
                json.dump(config, handle, indent=2)
        except Exception as exc:
            logger.error("Failed to persist watch_folders: %s", exc)


class WatchFolderImportTask(BaseTask):
    """Task that imports discovered files from watch folders."""

    def __init__(
        self,
        database,
        config_path: str,
        candidate_files: list[dict],
        updated_watch_folders: list[dict],
        total_candidates: int,
    ):
        super().__init__(
            task_type="WatchFolderImportTask",
            params={
                "candidate_count": len(candidate_files or []),
                "total_candidates": int(total_candidates or 0),
            },
        )
        self._db = database
        self._config_path = config_path
        self._candidate_files = candidate_files or []
        self._updated_watch_folders = updated_watch_folders or []
        self._total_candidates = int(total_candidates or 0)

    def _run_task(self):
        new_pictures = []
        stack_assignments = []
        delete_paths = []

        for candidate in self._candidate_files:
            file_path = candidate.get("file_path")
            if not file_path:
                continue

            try:
                pixel_sha = PictureUtils.calculate_hash_from_file_path(file_path)
            except Exception as exc:
                logger.warning("Failed to hash watched file %s: %s", file_path, exc)
                continue

            def find_existing(session: Session, hash_value: str):
                return session.exec(
                    select(Picture).where(Picture.pixel_sha == hash_value)
                ).first()

            existing = self._db.run_task(find_existing, pixel_sha)
            if existing:
                logger.debug("Already have picture with sha %s, skipping", pixel_sha)
                continue

            try:
                pic = PictureUtils.create_picture_from_file(
                    image_root_path=self._db.image_root,
                    source_file_path=file_path,
                    pixel_sha=pixel_sha,
                )
                pic.imported_at = datetime.now()
                new_pictures.append(pic)

                stack_id, source_id = parse_stack_tags_from_filename(file_path)
                if stack_id or source_id:
                    stack_assignments.append((pic, stack_id, source_id))
                if bool(candidate.get("delete_after_import", False)):
                    delete_paths.append(file_path)
            except Exception as exc:
                logger.warning("Failed to import watched file %s: %s", file_path, exc)

        changed = []
        imported_ids = []
        if new_pictures:

            def insert_pictures(session: Session, pictures: list[Picture]):
                session.add_all(pictures)
                session.commit()
                for pic in pictures:
                    session.refresh(pic)
                return pictures

            inserted = self._db.run_task(
                insert_pictures,
                new_pictures,
                priority=DBPriority.IMMEDIATE,
            )

            if stack_assignments:

                def apply_stack_assignments(session: Session, assignments: list[tuple]):
                    for pic, stack_id, source_id in assignments:
                        if pic.id is None:
                            continue
                        if stack_id:
                            assign_picture_to_stack(session, pic.id, stack_id)
                            continue
                        if source_id:
                            resolved_stack_id = get_or_create_stack_for_picture(
                                session, source_id
                            )
                            if resolved_stack_id:
                                assign_picture_to_stack(
                                    session,
                                    pic.id,
                                    resolved_stack_id,
                                )

                self._db.run_task(
                    apply_stack_assignments,
                    stack_assignments,
                    priority=DBPriority.IMMEDIATE,
                )

            for pic in inserted or []:
                if getattr(pic, "id", None) is None:
                    continue
                imported_ids.append(pic.id)
                changed.append((Picture, pic.id, "imported_at", pic.imported_at))

            logger.info("Added %d new pictures from watch folders.", len(imported_ids))

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

        if self._updated_watch_folders:
            persist_watch_folders(self._config_path, self._updated_watch_folders)

        return {
            "changed_count": len(changed),
            "changed": changed,
            "imported_picture_ids": imported_ids,
            "candidate_count": self._total_candidates,
        }
