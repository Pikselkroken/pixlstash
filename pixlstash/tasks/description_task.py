from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from sqlmodel import Session

from pixlstash.database import DBPriority
from pixlstash.db_models import Picture
from pixlstash.inference.workflows.description import DescriptionWorkflow
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask, QueueType, TaskPriority

if TYPE_CHECKING:
    from pixlstash.inference.engine import InferenceEngine


logger = get_logger(__name__)


class DescriptionTask(BaseTask):
    """Task for generating and persisting description batches.

    Args:
        database: Vault database instance.
        workflow: :class:`~pixlstash.inference.workflows.description.DescriptionWorkflow`
            used to generate captions.
        pictures: Pictures to process in this batch.
    """

    CPU_SPILLOVER_REUSE_GRACE_S = 8.0
    _cpu_spillover_engine: "InferenceEngine | None" = None
    _cpu_spillover_last_used_at: float = 0.0
    _cpu_spillover_lock = threading.Lock()

    def __init__(
        self,
        database,
        workflow: DescriptionWorkflow,
        pictures: list[Picture],
    ):
        picture_ids = [pic.id for pic in (pictures or []) if getattr(pic, "id", None)]
        super().__init__(
            task_type="DescriptionTask",
            params={
                "picture_ids": picture_ids,
                "batch_size": len(picture_ids),
            },
        )
        self._db = database
        self._workflow = workflow
        self._pictures = pictures or []
        self._cpu_spillover_enabled = False

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    @property
    def queue_type(self) -> QueueType:
        return QueueType.GPU

    def allow_cpu_spillover(self) -> bool:
        return True

    def enable_cpu_spillover(self) -> None:
        self._cpu_spillover_enabled = True

    @classmethod
    def _acquire_cpu_spillover_engine(cls, image_root: str) -> "InferenceEngine":
        with cls._cpu_spillover_lock:
            if cls._cpu_spillover_engine is None:
                from pixlstash.inference.engine import InferenceEngine

                logger.debug("DescriptionTask: creating CPU spillover InferenceEngine.")
                cls._cpu_spillover_engine = InferenceEngine.create(
                    device="cpu",
                    image_root=image_root,
                )
            cls._cpu_spillover_last_used_at = time.perf_counter()
            return cls._cpu_spillover_engine

    @classmethod
    def _release_idle_cpu_spillover_engine(cls, force: bool = False) -> None:
        with cls._cpu_spillover_lock:
            engine = cls._cpu_spillover_engine
            if engine is None:
                return
            if not force:
                idle_s = time.perf_counter() - cls._cpu_spillover_last_used_at
                if idle_s < cls.CPU_SPILLOVER_REUSE_GRACE_S:
                    return
            cls._cpu_spillover_engine = None
        try:
            engine.close()
        except Exception as exc:
            logger.debug("DescriptionTask CPU spillover engine close failed: %s", exc)

    def estimated_vram_mb(self) -> int:
        try:
            return max(0, self._workflow.estimate_vram_mb(len(self._pictures)))
        except Exception:
            return 0

    def _run_task(self):
        if not self._pictures:
            return {"changed_count": 0, "changed": []}

        descriptions_generated = self._generate_descriptions_batch(self._pictures)
        if not descriptions_generated:
            return {"changed_count": 0, "changed": []}

        def update_descriptions(session: Session, pics):
            changed = []
            for pic in pics:
                db_pic = session.get(Picture, pic.id)
                if db_pic is not None:
                    db_pic.description = pic.description
                    session.add(db_pic)
                    changed.append((Picture, pic.id, "description", pic.description))
            session.commit()
            return changed

        changed = self._db.run_task(
            update_descriptions,
            descriptions_generated,
            priority=DBPriority.LOW,
        )

        return {
            "changed_count": len(changed),
            "changed": changed,
        }

    def _generate_descriptions_batch(self, pictures: list[Picture]) -> list[Picture]:
        picture_ids = [pic.id for pic in pictures]
        logger.debug(
            "DescriptionTask: Generating descriptions for batch_size=%s ids=%s",
            len(pictures),
            picture_ids,
        )

        self._release_idle_cpu_spillover_engine(force=False)
        active_workflow = self._workflow
        cpu_spillover_engine = None
        if self._cpu_spillover_enabled:
            logger.debug(
                "DescriptionTask %s: using CPU spillover for ids=%s",
                self.id,
                picture_ids,
            )
            cpu_spillover_engine = self._acquire_cpu_spillover_engine(
                self._db.image_root
            )
            active_workflow = cpu_spillover_engine.description_workflow

        descriptions_generated = []
        try:
            batch_results = active_workflow.generate_batch(pictures)
        except Exception as exc:
            import traceback

            logger.error(
                "DescriptionTask failed for ids=%s: %s\n%s",
                picture_ids,
                exc,
                traceback.format_exc(),
            )
            batch_results = None
        finally:
            if cpu_spillover_engine is not None:
                with self._cpu_spillover_lock:
                    self._cpu_spillover_last_used_at = time.perf_counter()
                self._release_idle_cpu_spillover_engine(force=False)

        if not batch_results:
            for pic in pictures:
                pic.description = ""
                descriptions_generated.append(pic)
            return descriptions_generated

        for pic in pictures:
            description = batch_results.get(pic.id)
            if description:
                pic.description = description
            else:
                logger.error("Failed to generate description for picture %s", pic.id)
                pic.description = ""
            descriptions_generated.append(pic)
        return descriptions_generated
