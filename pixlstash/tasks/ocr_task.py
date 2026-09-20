"""Task that reads the text in pictures (#1197)."""

from __future__ import annotations

import json
import os
import threading
from typing import TYPE_CHECKING

from sqlalchemy import func
from sqlalchemy.orm import load_only
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import Picture
from pixlstash.pixl_logging import get_logger
from pixlstash.tagger_plugins.florence2 import FLORENCE_OCR_NUM_BEAMS
from pixlstash.tasks.base_task import BaseTask, QueueType, TaskPriority
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.media_files import SUPPORTED_IMAGE_EXTS

if TYPE_CHECKING:
    from pixlstash.inference.engine import InferenceEngine


logger = get_logger(__name__)

# Only pictures that look like they carry text get read. ``text_score`` is
# exactly 0 for nearly every photograph, so the bar sits just above the hard
# gates rather than a long way up the ramp: measured over a real library, dense
# phone screenshots score 0.16-0.36 and a page of chat text scores 0.23, while
# the photographs that get past the gates at all (a blown-out sky over sand, a
# facade of windows) top out near 0.16. 0.25 read almost nothing; the two
# populations overlap, so this keeps the screenshots and pays for a few photos
# that read as nothing and are never tried again.
OCR_MIN_TEXT_SCORE = 0.10


class OcrTask(BaseTask):
    """Reads the text in a batch of pictures and stores it with word boxes.

    Every picture handed in gets ``ocr_text`` written, ``""`` when nothing was
    read or its file could not be opened, so it is not selected again. When the
    reader returns nothing for the whole batch the task fails and writes
    nothing: that is the model, not the pictures.

    Args:
        database: Vault database instance.
        engine: Inference engine holding the shared Florence-2 service.
        pictures: Pictures to read.
        interactive: True when a person asked for this read ("Read again").
    """

    # Kept small: one dense page makes the whole batch decode to its length.
    BATCH_SIZE = 4

    def __init__(
        self,
        database,
        engine: "InferenceEngine",
        pictures: list,
        interactive: bool = False,
    ):
        picture_ids = [pic.id for pic in (pictures or []) if getattr(pic, "id", None)]
        super().__init__(
            task_type="OcrTask",
            params={"picture_ids": picture_ids, "batch_size": len(picture_ids)},
        )
        self._db = database
        self._engine = engine
        self._pictures = pictures or []
        self._interactive = interactive
        self._stop_event = threading.Event()

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.URGENT if self._interactive else TaskPriority.LOW

    @property
    def queue_type(self) -> QueueType:
        return QueueType.GPU

    def on_cancel(self) -> None:
        self._stop_event.set()

    def estimated_vram_mb(self) -> int:
        # Captioning's estimate for the same model, times the beams reading
        # decodes with: each beam carries its own cache of generated tokens.
        try:
            return max(
                0,
                self._engine.description_workflow.estimate_vram_mb(
                    len(self._pictures) * FLORENCE_OCR_NUM_BEAMS,
                    plugin_name="florence2",
                ),
            )
        except Exception as exc:
            logger.warning(
                "OcrTask: VRAM estimate failed for %d picture(s); assuming 0: %s",
                len(self._pictures),
                exc,
            )
            return 0

    def _run_task(self):
        path_to_id: dict[str, int] = {}
        for pic in self._pictures:
            if pic.id is None or not getattr(pic, "file_path", None):
                continue
            path = str(
                ImageUtils.resolve_picture_path(self._db.image_root, pic.file_path)
            )
            if os.path.splitext(path)[1].lower() in SUPPORTED_IMAGE_EXTS:
                path_to_id[path] = pic.id

        lines_by_path: dict = {}
        if path_to_id and not self._stop_event.is_set():
            lines_by_path = self._engine.read_text(list(path_to_id)) or {}
            if not lines_by_path:
                # Nothing read at all is the model failing (not loaded, out of
                # memory), not every picture being unreadable. Store nothing, so
                # the pictures stay unread and a later sweep tries again.
                raise RuntimeError(
                    f"OcrTask: the reader returned nothing for picture ids "
                    f"{list(path_to_id.values())}; left unread"
                )
        if self._stop_event.is_set():
            return {"changed_count": 0, "changed": []}

        results: dict[int, list] = {pic.id: [] for pic in self._pictures if pic.id}
        for path, lines in lines_by_path.items():
            if path in path_to_id:
                results[path_to_id[path]] = lines
        skipped = [pid for pid in results if pid not in path_to_id.values()]
        failed = [pid for path, pid in path_to_id.items() if path not in lines_by_path]
        if skipped or failed:
            logger.warning(
                "OcrTask: stored as having no text: %s (not a still image or no "
                "file), %s (the image could not be opened)",
                skipped,
                failed,
            )

        changed = self._db.run_task(
            OcrTask._persist_text,
            results,
            priority=DBPriority.HIGH if self._interactive else DBPriority.LOW,
        )
        return {"changed_count": len(changed or []), "changed": changed or []}

    @staticmethod
    def _persist_text(session: Session, results: dict[int, list]) -> list:
        """Write ``ocr_text`` and ``ocr_words`` for each picture read."""
        changed = []
        for picture_id, lines in results.items():
            pic = session.get(Picture, picture_id)
            if pic is None:
                continue
            pic.ocr_text = "\n".join(
                " ".join(word["text"] for word in line) for line in lines
            )
            pic.ocr_words = json.dumps(lines) if lines else None
            session.add(pic)
            changed.append((Picture, picture_id, "ocr_text", bool(lines)))
        session.commit()
        return changed

    @staticmethod
    def _unread_query(query):
        return (
            query.where(Picture.ocr_text.is_(None))
            .where(Picture.text_score >= OCR_MIN_TEXT_SCORE)
            .where(Picture.deleted.is_(False))
            .where(Picture.file_path.is_not(None))
        )

    @staticmethod
    def find_unread_pictures(session: Session, limit: int) -> list:
        """Return pictures that look like they carry text and have not been read.

        Loads only what the task reads: the planner runs this probe continuously,
        and a whole row drags the embedding blobs along (§7, probe cost).
        ``ix_picture_ocr_unread`` serves both the filter and the order.
        """
        # Most text first, so a batch holds pages of similar length: batched
        # generation runs every picture until the longest one finishes.
        return session.exec(
            OcrTask._unread_query(
                select(Picture).options(load_only(Picture.id, Picture.file_path))
            )
            .order_by(Picture.text_score.desc())
            .limit(limit)
        ).all()

    @staticmethod
    def count_progress(session: Session) -> tuple[int, int]:
        """Return ``(qualifying, unread)``: pictures worth reading, and those not yet read."""
        qualifying, read = session.exec(
            select(func.count(), func.count(Picture.ocr_text))
            .select_from(Picture)
            .where(Picture.text_score >= OCR_MIN_TEXT_SCORE)
            .where(Picture.deleted.is_(False))
            .where(Picture.file_path.is_not(None))
        ).one()
        return int(qualifying or 0), int((qualifying or 0) - (read or 0))
