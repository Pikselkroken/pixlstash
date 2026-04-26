import json
import os
import threading

from sqlalchemy.orm import load_only
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import Picture
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask, TaskPriority
from pixlstash.utils.comfyui_utilities import extract_comfy_workflow_info
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.image_processing.video_utils import VideoUtils


logger = get_logger(__name__)


class ComfyUIExtractionTask(BaseTask):
    """Backfill task: read ComfyUI workflow metadata from existing picture files and store in DB.

    Runs once per picture on first startup after the 0005 migration.  Any picture
    where new ComfyUI data is found has its text_embedding cleared so that the
    TextEmbeddingTask will regenerate it with the full workflow context.
    """

    BATCH_SIZE = 32

    def __init__(self, database, image_root: str, pictures: list[Picture]):
        picture_ids = [pic.id for pic in (pictures or []) if getattr(pic, "id", None)]
        super().__init__(
            task_type="ComfyUIExtractionTask",
            params={
                "picture_ids": picture_ids,
                "batch_size": len(picture_ids),
            },
        )
        self._db = database
        self._image_root = image_root
        self._pictures = pictures or []
        self._stop_event = threading.Event()

    def on_cancel(self) -> None:
        self._stop_event.set()

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    def _run_task(self):
        if not self._pictures:
            return {"checked": 0, "found_comfyui": 0}

        picture_ids = [pic.id for pic in self._pictures]

        def fetch_fresh(session: Session, ids: list[int]) -> list[Picture]:
            return session.exec(
                select(Picture)
                .where(Picture.id.in_(ids))
                .options(
                    load_only(
                        Picture.id,
                        Picture.file_path,
                        Picture.comfyui_models,
                    )
                )
            ).all()

        fresh_pictures = self._db.run_immediate_read_task(fetch_fresh, picture_ids)

        # (picture_id, positive_prompt, models_json, loras_json, clear_embedding)
        updates: list[tuple] = []
        checked = 0
        found_comfyui = 0

        for pic in fresh_pictures:
            if self._stop_event.is_set():
                logger.debug(
                    "ComfyUIExtractionTask cancelled, stopping early at task %s",
                    self.id,
                )
                break
            resolved = ImageUtils.resolve_picture_path(self._image_root, pic.file_path)

            if not resolved or not os.path.exists(resolved):
                # Write the sentinel so the finder never re-queues this picture.
                updates.append((pic.id, None, "[]", "[]", False))
                checked += 1
                continue

            if VideoUtils.is_video_file(resolved):
                # Videos cannot contain ComfyUI metadata; mark as done.
                updates.append((pic.id, None, "[]", "[]", False))
                checked += 1
                continue

            positive_prompt = None
            models = []
            loras = []
            try:
                embedded_metadata = ImageUtils.extract_embedded_metadata(resolved)
                workflow_info = extract_comfy_workflow_info(embedded_metadata)
                if workflow_info:
                    positive_prompt = workflow_info.get("positive_prompt") or None
                    models = workflow_info.get("models") or []
                    loras = workflow_info.get("loras") or []
            except Exception as exc:
                logger.debug(
                    "ComfyUIExtractionTask: extraction failed for picture %s (%s): %s",
                    pic.id,
                    resolved,
                    exc,
                )

            # Always write at least "[]" so comfyui_models IS NULL remains the
            # "not yet checked" sentinel and this picture is never revisited.
            models_json = json.dumps(models)
            loras_json = json.dumps(loras)

            had_comfyui = bool(positive_prompt or models or loras)
            if had_comfyui:
                found_comfyui += 1

            updates.append(
                (pic.id, positive_prompt, models_json, loras_json, had_comfyui)
            )
            checked += 1

        if not updates:
            return {"checked": 0, "found_comfyui": 0}

        def persist(session: Session, rows: list[tuple]):
            for pid, pos_prompt, models_json, loras_json, clear_embedding in rows:
                db_pic = session.get(Picture, pid)
                if db_pic is None:
                    continue
                if pos_prompt is not None:
                    db_pic.comfyui_positive_prompt = pos_prompt
                # Always write the sentinel ("[]" at minimum) so this picture is
                # never re-queued by the finder.
                db_pic.comfyui_models = models_json
                db_pic.comfyui_loras = loras_json
                # Clear the existing embedding so TextEmbeddingTask regenerates it
                # with the newly stored ComfyUI context included.
                if clear_embedding:
                    db_pic.text_embedding = None
                session.add(db_pic)
            session.commit()

        self._db.run_task(persist, updates, priority=DBPriority.LOW)
        logger.debug(
            "ComfyUIExtractionTask: checked=%s, found_comfyui=%s",
            checked,
            found_comfyui,
        )
        return {"checked": checked, "found_comfyui": found_comfyui}
