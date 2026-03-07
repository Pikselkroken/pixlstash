import threading
import time

from sqlmodel import Session

from pixlvault.database import DBPriority
from pixlvault.db_models import Picture
from pixlvault.picture_tagger import PictureTagger
from pixlvault.pixl_logging import get_logger
from pixlvault.tasks.base_task import BaseTask


logger = get_logger(__name__)


class DescriptionTask(BaseTask):
    """Task for generating and persisting description batches.

    Args:
        database: Vault database instance.
        picture_tagger: Tagger used to generate descriptions.
        pictures: Pictures to process in this batch.
    """

    CPU_SPILLOVER_REUSE_GRACE_S = 8.0
    _cpu_spillover_tagger: PictureTagger | None = None
    _cpu_spillover_last_used_at: float = 0.0
    _cpu_spillover_lock = threading.Lock()

    def __init__(
        self,
        database,
        picture_tagger: PictureTagger,
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
        self._picture_tagger = picture_tagger
        self._pictures = pictures or []
        self._cpu_spillover_enabled = False

    def allow_cpu_spillover(self) -> bool:
        return True

    def enable_cpu_spillover(self) -> None:
        self._cpu_spillover_enabled = True

    @classmethod
    def _acquire_cpu_spillover_tagger(cls, image_root: str) -> PictureTagger:
        with cls._cpu_spillover_lock:
            if cls._cpu_spillover_tagger is None:
                logger.debug("DescriptionTask: creating CPU spillover PictureTagger.")
                cls._cpu_spillover_tagger = PictureTagger(
                    silent=True,
                    device="cpu",
                    image_root=image_root,
                )
            cls._cpu_spillover_last_used_at = time.perf_counter()
            return cls._cpu_spillover_tagger

    @classmethod
    def _release_idle_cpu_spillover_tagger(cls, force: bool = False) -> None:
        with cls._cpu_spillover_lock:
            tagger = cls._cpu_spillover_tagger
            if tagger is None:
                return
            if not force:
                idle_s = time.perf_counter() - cls._cpu_spillover_last_used_at
                if idle_s < cls.CPU_SPILLOVER_REUSE_GRACE_S:
                    return
            cls._cpu_spillover_tagger = None
        try:
            tagger.close()
        except Exception as exc:
            logger.debug("DescriptionTask CPU spillover tagger close failed: %s", exc)

    def estimated_vram_mb(self) -> int:
        fn = getattr(self._picture_tagger, "estimate_description_vram_mb", None)
        if callable(fn):
            try:
                return max(0, int(fn(len(self._pictures))))
            except Exception:
                return 0
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

        self._release_idle_cpu_spillover_tagger(force=False)
        active_tagger = self._picture_tagger
        cpu_spillover_tagger = None
        if self._cpu_spillover_enabled:
            logger.debug(
                "DescriptionTask %s: using CPU spillover for ids=%s",
                self.id,
                picture_ids,
            )
            cpu_spillover_tagger = self._acquire_cpu_spillover_tagger(
                self._db.image_root
            )
            active_tagger = cpu_spillover_tagger

        descriptions_generated = []
        try:
            batch_results = active_tagger.generate_descriptions_batch(pictures)
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
            if cpu_spillover_tagger is not None:
                with self._cpu_spillover_lock:
                    self._cpu_spillover_last_used_at = time.perf_counter()
                self._release_idle_cpu_spillover_tagger(force=False)

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
