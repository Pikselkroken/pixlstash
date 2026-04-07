from datetime import datetime, timezone

from PIL import Image as PILImage
from sqlmodel import Session, delete, select

from pixlstash.database import DBPriority
from pixlstash.db_models import Picture
from pixlstash.db_models.face import Face
from pixlstash.db_models.tag import (
    Tag,
    TAG_EMPTY_SENTINEL,
)
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.picture_tagger import PictureTagger, QUALITY_CROP_TAG_WHITELIST
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask, TaskPriority
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.service.tag_prediction_utils import (
    recompute_anomaly_tag_uncertainty,
)

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

        # Accumulate full-image scores per picture id.
        label_scores_by_pic_id: dict[int, dict[str, float]] = {}
        for path, label_scores in scores_by_path.items():
            pic = pic_by_path.get(str(path))
            if pic is None or not label_scores:
                continue
            label_scores_by_pic_id[pic.id] = dict(label_scores)

        # Quality crop pass: score face crops and merge confidences.
        # This ensures tags detected only at crop resolution (e.g. "pixelated")
        # receive a real confidence score rather than the 0.0 fallback.
        try:
            pic_ids = [p.id for p in self._pictures if p.file_path]

            def _fetch_faces(session):
                faces = session.exec(
                    select(Face).where(Face.picture_id.in_(pic_ids))
                ).all()
                result = {}
                for face in faces:
                    result.setdefault(face.picture_id, []).append(face)
                return result

            faces_by_pic = self._db.run_task(_fetch_faces, priority=DBPriority.LOW)
            target = self._picture_tagger.custom_tagger_image_size_quality_crop()
            quality_items = []
            key_to_pic_id: dict[str, int] = {}
            for pic in self._pictures:
                if not pic.file_path:
                    continue
                faces = faces_by_pic.get(pic.id, [])
                valid_faces = [
                    f for f in faces if f.bbox and getattr(f, "face_index", 0) >= 0
                ]
                if not valid_faces:
                    continue
                file_path = str(
                    ImageUtils.resolve_picture_path(self._db.image_root, pic.file_path)
                )
                try:
                    img = PILImage.open(file_path).convert("RGB")
                    w, h = img.size
                    largest_face = max(
                        valid_faces,
                        key=lambda f: max(
                            0,
                            (float(f.bbox[2]) - float(f.bbox[0]))
                            * (float(f.bbox[3]) - float(f.bbox[1])),
                        ),
                    )
                    expanded = PictureTagger._expand_bbox_to_square(
                        largest_face.bbox, w, h, target
                    )
                    crop = img.crop(expanded)
                    key = f"{file_path}#face{largest_face.id}"
                    quality_items.append((key, crop))
                    key_to_pic_id[key] = pic.id
                except Exception as exc:
                    logger.warning(
                        "Could not load %s for prediction crop scoring: %s",
                        file_path,
                        exc,
                    )
            if quality_items:
                crop_scores = self._picture_tagger.score_quality_crops_raw(
                    quality_items
                )
                for key, tag_scores in crop_scores.items():
                    pic_id = key_to_pic_id.get(key)
                    if pic_id is None:
                        continue
                    merged = label_scores_by_pic_id.setdefault(pic_id, {})
                    for tag, conf in tag_scores.items():
                        # Only boost whitelist tags from crop scores — TagTask
                        # applies the same restriction, so non-whitelist tags
                        # must rely on their full-image score only.
                        if tag not in QUALITY_CROP_TAG_WHITELIST:
                            continue
                        if conf > merged.get(tag, 0.0):
                            merged[tag] = conf
        except Exception as exc:
            logger.warning("Quality crop prediction scoring failed: %s", exc)

        updates: list[dict] = []
        for pic_id, label_scores in label_scores_by_pic_id.items():
            if not label_scores:
                continue
            confs = list(label_scores.values())
            uncertainty = float(max(min(c, 1.0 - c) for c in confs))
            updates.append(
                {
                    "picture_id": pic_id,
                    "label_scores": label_scores,
                    "uncertainty": uncertainty,
                }
            )

        if not updates:
            return {"written": 0}

        picture_ids = [u["picture_id"] for u in updates]
        written = self._db.run_task(
            self._upsert_predictions,
            updates,
            self._model_version,
            priority=DBPriority.LOW,
        )
        return {
            "written": written,
            "picture_ids": picture_ids if written > 0 else [],
        }

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

            # Delete predictions from older model versions so only the latest
            # scores are kept.  Manual predictions are never purged.
            session.exec(
                delete(TagPrediction)
                .where(TagPrediction.picture_id == picture_id)
                .where(TagPrediction.model_version != model_version)
                .where(TagPrediction.model_version != "manual")
            )

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
                elif tag_task_has_run and existing.status != status:
                    existing.confidence = confidence
                    existing.model_version = model_version
                    existing.status = status
                    existing.predicted_at = now
                    written += 1

            # Ensure every confirmed tag has a prediction row even if the model
            # scored it below the minimum-confidence threshold (or doesn't know
            # the tag at all).  These rows get confidence=0.0 so the UI can
            # still display an informative tooltip for manually-added tags.
            if tag_task_has_run:
                label_score_tags = set(label_scores.keys())
                for tag in applied_tags:
                    if tag in label_score_tags:
                        continue  # already handled above
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
                                confidence=0.0,
                                model_version=model_version,
                                status="CONFIRMED",
                                predicted_at=now,
                            )
                        )
                        written += 1

            # Recompute anomaly_tag_uncertainty purely from the TagPrediction rows
            # that were just written (status + confidence are now up to date).
            recompute_anomaly_tag_uncertainty(session, picture_id)

            # Update tag_uncertainty on Picture
            pic = session.get(Picture, picture_id)
            if pic is not None:
                pic.tag_uncertainty = uncertainty

        session.commit()
        return written
