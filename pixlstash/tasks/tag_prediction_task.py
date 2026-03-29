from datetime import datetime, timezone

from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models import Picture
from pixlstash.db_models.tag import Tag, TAG_EMPTY_SENTINEL
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.picture_tagger import PictureTagger
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask, TaskPriority
from pixlstash.utils.image_processing.image_utils import ImageUtils

logger = get_logger(__name__)

_PREDICTION_MIN_CONFIDENCE = 0.05


class TagPredictionTask(BaseTask):
    """Run the custom tagger on a batch of pictures and persist raw confidence
    scores to the ``TagPrediction`` table.

    This task is separate from ``TagTask`` so that the full probability
    distribution from the custom model is captured without affecting the
    existing tag workflow.  Results are stored with a ``model_version`` of
    ``"epoch-{N}"`` derived from the custom tagger's meta.json.
    """

    def __init__(
        self,
        database,
        picture_tagger: PictureTagger,
        pictures: list,
        model_version: str,
    ):
        picture_ids = [p.id for p in (pictures or []) if getattr(p, "id", None)]
        super().__init__(
            task_type="TagPredictionTask",
            params={"picture_ids": picture_ids, "model_version": model_version},
        )
        self._db = database
        self._picture_tagger = picture_tagger
        self._pictures = pictures or []
        self._model_version = model_version

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    def _run_task(self):
        if not self._pictures:
            return {"written": 0}

        image_paths = [
            ImageUtils.resolve_picture_path(self._db.image_root, pic.file_path)
            for pic in self._pictures
            if pic.file_path
        ]
        pic_by_path = {
            str(
                ImageUtils.resolve_picture_path(self._db.image_root, pic.file_path)
            ): pic
            for pic in self._pictures
            if pic.file_path
        }
        if not image_paths:
            return {"written": 0}

        scores_by_path = self._picture_tagger.score_images_custom(
            image_paths,
            min_confidence=_PREDICTION_MIN_CONFIDENCE,
        )

        updates: list[dict] = []
        for path, label_scores in scores_by_path.items():
            pic = pic_by_path.get(str(path))
            if pic is None or not label_scores:
                continue
            confs = list(label_scores.values())
            uncertainty = float(max(min(c, 1.0 - c) for c in confs))
            updates.append(
                {
                    "picture_id": pic.id,
                    "label_scores": label_scores,
                    "uncertainty": uncertainty,
                }
            )

        if not updates:
            return {"written": 0}

        written = self._db.run_task(
            self._upsert_predictions,
            updates,
            self._model_version,
            priority=DBPriority.LOW,
        )
        return {"written": written}

    @staticmethod
    def _upsert_predictions(
        session: Session,
        updates: list[dict],
        model_version: str,
    ) -> int:
        written = 0
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for update in updates:
            picture_id = update["picture_id"]
            label_scores: dict[str, float] = update["label_scores"]
            uncertainty: float = update["uncertainty"]

            # Determine whether TagTask has already run for this picture.
            # TagTask always writes at least one row to the tag table (a real
            # tag or the empty sentinel for zero-tag pictures), so any tag row
            # indicates the tagger has made its decision and we can resolve
            # directly to CONFIRMED or REJECTED.  If no rows exist at all the
            # tagger hasn't run yet, so we write PENDING for later resolution.
            all_tag_rows = session.exec(
                select(Tag.tag).where(Tag.picture_id == picture_id)
            ).all()
            tag_task_has_run = len(all_tag_rows) > 0
            applied_tags = {
                (row[0] if isinstance(row, tuple) else row)
                for row in all_tag_rows
                if (row[0] if isinstance(row, tuple) else row)
                not in (None, TAG_EMPTY_SENTINEL)
            }

            for tag, confidence in label_scores.items():
                if tag_task_has_run:
                    status = "CONFIRMED" if tag in applied_tags else "REJECTED"
                else:
                    status = "PENDING"
                existing = session.exec(
                    select(TagPrediction).where(
                        TagPrediction.picture_id == picture_id,
                        TagPrediction.tag == tag,
                    )
                ).first()
                if existing is None:
                    session.add(
                        TagPrediction(
                            picture_id=picture_id,
                            tag=tag,
                            confidence=confidence,
                            model_version=model_version,
                            status=status,
                            predicted_at=now,
                        )
                    )
                    written += 1
                elif existing.model_version != model_version:
                    # New model version — update scores and re-evaluate status
                    existing.confidence = confidence
                    existing.model_version = model_version
                    existing.status = status
                    existing.predicted_at = now
                    written += 1

            # Update denormalised tag_uncertainty on Picture
            pic = session.get(Picture, picture_id)
            if pic is not None:
                pic.tag_uncertainty = uncertainty

        session.commit()
        return written
