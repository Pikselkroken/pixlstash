import time
from datetime import datetime, timezone

from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    Tag,
    TAG_SENTINEL_LIKE_PATTERN,
    TAG_SENTINEL_ESCAPE_CHAR,
)
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.inference.workflows.tagging import TaggingWorkflow
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask, QueueType, TaskPriority
from pixlstash.tasks.tag_task import TagTask
from pixlstash.utils.image_processing.image_utils import ImageUtils


logger = get_logger(__name__)


class TagPredictionBackfillTask(BaseTask):
    """Generate TagPrediction rows for pictures that have tags but no predictions.

    Pictures tagged before inline prediction-writing existed (or by a different
    engine at the time) keep their confirmed ``Tag`` rows but have zero
    ``tag_prediction`` rows, and the normal pipeline never revisits them
    (``MissingTagFinder`` only re-tags pictures carrying a retag sentinel). This
    task runs the PixlStash tagger purely to recover the raw confidence scores,
    then writes predictions from those scores against the picture's *existing*
    applied tags. It never adds, removes, or rewrites a ``Tag`` row, so a
    curated tag set is left untouched.

    Scoped to the PixlStash tagger: only it produces the raw per-label sigmoid
    scores predictions are built from. The finder gates on that, but the task
    re-checks defensively.

    Note: this uses the full-image scoring pass only. The live ``TagTask`` also
    boosts a small whitelist of quality tags via face-crop inference; that
    refinement is intentionally omitted here to keep the backfill a single
    inference pass. Every applied tag still gets a prediction row (at worst with
    ``confidence=0.0``), so the review UI has a row for every tag.
    """

    def __init__(
        self,
        database,
        tagging_workflow: TaggingWorkflow,
        pictures: list,
    ):
        picture_ids = [pic.id for pic in (pictures or []) if getattr(pic, "id", None)]
        super().__init__(
            task_type="TagPredictionBackfillTask",
            params={
                "picture_ids": picture_ids,
                "batch_size": len(picture_ids),
            },
        )
        self._db = database
        self._tagging_workflow = tagging_workflow
        self._pictures = pictures or []

    @property
    def priority(self) -> TaskPriority:
        # Lowest priority: this is catch-up work for already-processed pictures.
        return TaskPriority.LOW

    @property
    def queue_type(self) -> QueueType:
        # Runs the tagger, so it belongs on the serialised GPU queue.
        return QueueType.GPU

    def _run_task(self) -> dict:
        workflow = self._tagging_workflow
        n_pictures = len(self._pictures)
        if not workflow.is_pixlstash_tagger_enabled:
            logger.info(
                "PixlStash tagger not active; skipping prediction backfill for "
                "%d picture(s).",
                n_pictures,
            )
            return {"backfilled": 0, "pictures": 0}

        pic_by_path: dict[str, object] = {}
        image_paths: list[str] = []
        attempted_pic_ids: list[int] = []
        for pic in self._pictures:
            if not getattr(pic, "file_path", None) or pic.id is None:
                continue
            path = ImageUtils.resolve_picture_path(self._db.image_root, pic.file_path)
            image_paths.append(path)
            pic_by_path[path] = pic
            attempted_pic_ids.append(pic.id)
        if not image_paths:
            return {"backfilled": 0, "pictures": 0}

        # Make sure the tagger model is loaded before inference.
        workflow.ensure_active_plugin_ready()

        # Single inference pass: we only want the raw scores, not the tags the
        # model would apply (the picture already has its tags).
        full_scores_by_path: dict = {}
        inference_start = time.perf_counter()
        workflow.tag_images(image_paths, out_raw_pixlstash_scores=full_scores_by_path)
        inference_s = time.perf_counter() - inference_start

        label_scores_by_pic_id: dict[int, dict] = {}
        for path, scores in full_scores_by_path.items():
            pic = pic_by_path.get(path)
            if pic is not None and scores:
                label_scores_by_pic_id[pic.id] = scores

        unscored = [
            pid for pid in attempted_pic_ids if pid not in label_scores_by_pic_id
        ]
        if unscored:
            # A picture can come back without scores if its file failed to load /
            # decode. Log it (don't swallow), and let the DB write put a
            # confidence=0.0 row on its existing tags so it drops out of the
            # "missing predictions" query instead of being reprocessed forever.
            logger.warning(
                "PixlStash tagger produced no scores for %d/%d picture(s) "
                "(ids=%s); writing zero-confidence rows so they are not retried.",
                len(unscored),
                len(attempted_pic_ids),
                unscored,
            )

        model_version = self._resolve_model_version(workflow)
        written = self._db.run_task(
            self._backfill_predictions,
            label_scores_by_pic_id,
            attempted_pic_ids,
            model_version,
            priority=DBPriority.LOW,
        )
        logger.info(
            "[TAG_PRED_BACKFILL] task_id=%s pictures=%d scored=%d rows_written=%d "
            "model_version=%s inference_s=%.3f",
            self.id,
            len(attempted_pic_ids),
            len(label_scores_by_pic_id),
            int(written or 0),
            model_version,
            inference_s,
        )
        return {
            "backfilled": int(written or 0),
            "pictures": len(attempted_pic_ids),
        }

    @staticmethod
    def _resolve_model_version(workflow: TaggingWorkflow) -> str:
        """Resolve the PixlStash tagger version string (mirrors TagTask)."""
        try:
            version_fn = getattr(workflow._engine, "pixlstash_tagger_version", None)
            if callable(version_fn):
                return f"v{version_fn()}"
        except Exception:
            logger.warning(
                "pixlstash_tagger_version() failed, using 'unknown' model version",
                exc_info=True,
            )
        return "unknown"

    @staticmethod
    def _backfill_predictions(
        session: Session,
        label_scores_by_pic_id: dict,
        attempted_picture_ids: list,
        model_version: str,
    ) -> int:
        """Fetch each picture's applied tags and write predictions from scores.

        Reuses ``TagTask._write_predictions_from_tags`` for the scored pictures
        so prediction status (CONFIRMED / REJECTED), the FK-safety filtering,
        and the uncertainty side-effects match the live tagging path exactly.
        For pictures the model returned no scores for (decode failure), it writes
        a ``confidence=0.0`` row per existing tag so they drop out of the
        "missing predictions" query rather than being reprocessed forever. The
        Tag table is read only, never written.
        """
        has_sentinel = Tag.tag.like(
            TAG_SENTINEL_LIKE_PATTERN, escape=TAG_SENTINEL_ESCAPE_CHAR
        )
        tag_rows = session.exec(
            select(Tag.picture_id, Tag.tag).where(
                Tag.picture_id.in_(attempted_picture_ids),
                Tag.tag.is_not(None),
                ~has_sentinel,
            )
        ).all()
        tags_by_pic_id: dict[int, set] = {}
        for pid, tag in tag_rows:
            tags_by_pic_id.setdefault(pid, set()).add(tag)

        written = TagTask._write_predictions_from_tags(
            session,
            label_scores_by_pic_id,
            tags_by_pic_id,
            model_version,
        )

        # Mark attempted-but-unscored pictures so they leave the missing query.
        # _write_predictions_from_tags committed already; do these in a second
        # commit. Existing rows (the picture already has a prediction) are left
        # alone, so this is a no-op on anything already handled above.
        unscored_ids = [
            pid for pid in attempted_picture_ids if not label_scores_by_pic_id.get(pid)
        ]
        if not unscored_ids:
            return written

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        existing_pairs = {
            (row.picture_id, row.tag)
            for row in session.exec(
                select(TagPrediction).where(TagPrediction.picture_id.in_(unscored_ids))
            ).all()
        }
        for pid in unscored_ids:
            for tag in tags_by_pic_id.get(pid, set()):
                if (pid, tag) in existing_pairs:
                    continue
                session.add(
                    TagPrediction(
                        picture_id=pid,
                        tag=tag,
                        confidence=0.0,
                        model_version=model_version,
                        status="CONFIRMED",
                        predicted_at=now,
                    )
                )
                written += 1
        session.commit()
        return written
