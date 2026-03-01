from sqlmodel import Session, select, delete

from pixlvault.database import DBPriority
from pixlvault.db_models import Face, Picture, Tag
from pixlvault.utils.image_processing.image_utils import ImageUtils
from pixlvault.picture_tagger import PictureTagger, QUALITY_CROP_TAG_WHITELIST
from pixlvault.pixl_logging import get_logger
from pixlvault.tasks.base_task import BaseTask


logger = get_logger(__name__)


class TagTask(BaseTask):
    """Task that tags a batch of pictures and persists tag updates."""

    def __init__(
        self,
        database,
        picture_tagger,
        pictures: list,
    ):
        picture_ids = [pic.id for pic in (pictures or []) if getattr(pic, "id", None)]
        super().__init__(
            task_type="TagTask",
            params={
                "picture_ids": picture_ids,
                "batch_size": len(picture_ids),
            },
        )
        self._db = database
        self._picture_tagger = picture_tagger
        self._pictures = pictures or []

    def _run_task(self):
        if not self._pictures:
            return {"changed_count": 0, "changed": []}

        changed = self._tag_pictures_batch()

        return {
            "changed_count": len(changed),
            "changed": changed,
        }

    @staticmethod
    def _add_tags_bulk(session: Session, updates: list[dict]):
        updated_ids = []
        for update in updates:
            pic_id = update.get("pic_id")
            if pic_id is None:
                continue
            tags = update.get("tags") or []

            existing_tags = session.exec(
                select(Tag.tag).where(Tag.picture_id == pic_id)
            ).all()
            existing_tag_set = {
                row[0] if isinstance(row, tuple) else row
                for row in existing_tags
                if row
            }
            if set(tags) == existing_tag_set:
                continue

            session.exec(delete(Tag).where(Tag.picture_id == pic_id))

            for tag_value in set(tags):
                session.add(Tag(picture_id=pic_id, tag=tag_value))

            updated_ids.append(pic_id)

        session.commit()
        return updated_ids

    @staticmethod
    def _fetch_faces_for_pictures(session: Session, picture_ids: list) -> dict:
        faces = session.exec(select(Face).where(Face.picture_id.in_(picture_ids))).all()
        result = {}
        for face in faces:
            result.setdefault(face.picture_id, []).append(face)
        return result

    def _tag_pictures_batch(self) -> list:
        assert self._pictures is not None

        batch = self._pictures[
            : max(1, int(self._picture_tagger.max_concurrent_images()))
        ]
        image_paths = []
        pic_by_path = {}
        for pic in batch:
            file_path = ImageUtils.resolve_picture_path(
                self._db.image_root, pic.file_path
            )
            image_paths.append(file_path)
            pic_by_path[file_path] = pic

        tagged_pictures = []
        if image_paths:
            logger.debug("Tagging %s images", len(image_paths))
            logger.debug("Tagging image paths: %s", image_paths)
            tag_results = self._picture_tagger.tag_images(image_paths)
            logger.debug("Got tag results for %s images.", len(tag_results))

            # --- Quality crop pass ---
            # Fetch face bboxes and run the custom tagger on expanded crops so
            # that quality tags (e.g. "pixelated") that are invisible at full-
            # image resolution can still be detected.
            try:
                from PIL import Image as PILImage

                pic_ids = [p.id for p in batch]
                faces_by_pic = self._db.run_task(
                    lambda session: self._fetch_faces_for_pictures(session, pic_ids),
                    priority=DBPriority.LOW,
                )
                target = self._picture_tagger.custom_tagger_image_size_full()
                quality_items = []
                key_to_path = {}
                for pic in batch:
                    file_path = ImageUtils.resolve_picture_path(
                        self._db.image_root, pic.file_path
                    )
                    faces = faces_by_pic.get(pic.id, [])
                    if not faces:
                        continue
                    try:
                        img = PILImage.open(file_path).convert("RGB")
                        w, h = img.size
                        for face in faces:
                            if not face.bbox or getattr(face, "face_index", 0) < 0:
                                continue
                            expanded = PictureTagger._expand_bbox_to_square(
                                face.bbox, w, h, target
                            )
                            crop = img.crop(expanded)
                            key = f"{file_path}#face{face.id}"
                            quality_items.append((key, crop))
                            key_to_path[key] = file_path
                    except Exception as exc:
                        logger.warning(
                            "Could not load %s for quality crop pass: %s",
                            file_path,
                            exc,
                        )
                if quality_items:
                    quality_results = self._picture_tagger.tag_quality_crops(
                        quality_items
                    )
                    # Accumulate quality tags found across all crops per picture path.
                    quality_tags_by_path = {}
                    for key, quality_tags in quality_results.items():
                        path = key_to_path.get(key)
                        if path:
                            quality_tags_by_path.setdefault(path, set()).update(
                                quality_tags
                            )
                    # Crops are ground truth for whitelist tags: strip any whitelist
                    # tags the full-image pass may have produced, then add only what
                    # the crops confirmed.  Only applies to pictures that had at least
                    # one crop — pictures without faces are left untouched.
                    for path, crop_quality in quality_tags_by_path.items():
                        if path not in tag_results:
                            continue
                        stripped = [
                            t
                            for t in tag_results[path]
                            if t not in QUALITY_CROP_TAG_WHITELIST
                        ]
                        tag_results[path] = stripped + list(crop_quality)
                        if crop_quality:
                            logger.debug(
                                "Quality crop tags for %s: %s", path, crop_quality
                            )
            except Exception as exc:
                logger.warning("Quality crop pass failed: %s", exc)
            # --- end quality crop pass ---

            update_payloads = []
            for path, tags in tag_results.items():
                pic = pic_by_path.get(path)
                logger.debug("Processing tags for image at path: %s: %s", path, tags)
                if not tags or not pic:
                    continue

                update_payloads.append(
                    {
                        "pic_id": pic.id,
                        "tags": tags,
                    }
                )

            if update_payloads:
                updated_ids = self._db.run_task(
                    self._add_tags_bulk,
                    update_payloads,
                    priority=DBPriority.LOW,
                )
                updated_set = set(updated_ids or [])
                for update in update_payloads:
                    pic_id = update.get("pic_id")
                    if pic_id in updated_set:
                        tagged_pictures.append(
                            (Picture, pic_id, "tags", update.get("tags") or [])
                        )

        return tagged_pictures
