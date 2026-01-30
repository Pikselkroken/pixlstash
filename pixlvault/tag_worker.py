import os
import queue
import time
import uuid

from sqlmodel import select, Session, delete
from sqlalchemy.orm import load_only, selectinload

from pixlvault.event_types import EventType
from pixlvault.picture_tagger import PictureTagger
from pixlvault.picture_utils import PictureUtils
from pixlvault.database import DBPriority
from pixlvault.pixl_logging import get_logger
from pixlvault.database import VaultDatabase
from pixlvault.worker_registry import BaseWorker, WorkerType

from pixlvault.db_models import Character, FaceTag, HandTag, Picture, Tag
from pixlvault.feature_tag_blacklist import (
    FACE_TAG_BLACKLIST,
    HAND_TAG_BLACKLIST,
    is_face_tag,
    is_hand_tag,
)

logger = get_logger(__name__)

HAND_CROP_MIN_AREA_RATIO = 0.01
HAND_CROP_MAX_PER_PICTURE = 2
CROP_DEBUG_ENABLED = False


class DescriptionWorker(BaseWorker):
    """
    Worker for generating picture descriptions only.
    """

    def worker_type(self) -> WorkerType:
        return WorkerType.DESCRIPTION

    def _run(self):
        while not self._stop.is_set():
            try:
                start = time.time()
                logger.debug("DescriptionWorker: Starting iteration...")
                data_updated = False
                missing_descriptions = self._fetch_missing_descriptions()

                logger.debug(
                    f"DescriptionWorker: Got {len(missing_descriptions)} pictures needing descriptions."
                )
                if self._stop.is_set():
                    break
                descriptions_generated = self._generate_descriptions(
                    self._picture_tagger, missing_descriptions
                )
                logger.debug(
                    f"DescriptionWorker: Generated {len(descriptions_generated)} descriptions."
                )
                if self._stop.is_set():
                    break
                if descriptions_generated:

                    def update_descriptions(session: Session, pics):
                        changed = []
                        for pic in pics:
                            db_pic = session.get(Picture, pic.id)
                            if db_pic is not None:
                                db_pic.description = pic.description
                                session.add(db_pic)
                                changed.append(
                                    (Picture, pic.id, "description", pic.description)
                                )
                        session.commit()
                        return changed

                    changed = self._db.run_task(
                        update_descriptions,
                        descriptions_generated,
                        priority=DBPriority.LOW,
                    )
                    data_updated = len(changed) > 0
                    self._notify_ids_processed(changed)
                    self._notify_others(EventType.CHANGED_DESCRIPTIONS)
                timing = time.time() - start
                if data_updated:
                    logger.debug(f"DescriptionWorker: Done after {timing:.2f} seconds.")
                else:
                    logger.debug(
                        f"DescriptionWorker: Sleeping after {timing:.2f} seconds. No work needed."
                    )
                    self._wait()
            except Exception as e:
                import traceback

                logger.error(
                    "DescriptionWorker thread exiting due to error: %s\n%s",
                    e,
                    traceback.format_exc(),
                )
                break
        logger.info("Exiting DescriptionWorker loop.")

    def _fetch_missing_descriptions(self):
        logger.debug("Starting the database fetch for missing descriptions")

        return VaultDatabase.result_or_throw(
            self._db.submit_task(
                lambda session: session.exec(
                    select(Picture)
                    .where(Picture.description.is_(None))
                    .options(selectinload(Picture.characters))
                ).all()
            )
        )

    def _generate_descriptions(
        self, picture_tagger: PictureTagger, missing_descriptions: list[Picture]
    ) -> int:
        """Generate descriptions for pictures using PictureTagger."""
        assert missing_descriptions is not None
        batch = missing_descriptions[: picture_tagger.max_concurrent_images()]

        descriptions_generated = []
        for pic in batch:
            try:
                description = picture_tagger.generate_description(picture=pic)
                logger.debug("[DESCRIPTION WORKER] Got description: " + description)

                def set_description(session: Session, pic_id, description):
                    pic = session.exec(
                        select(Picture)
                        .where(Picture.id == pic_id)
                        .options(selectinload(Picture.characters))
                    ).one()
                    pic.description = description
                    session.add(pic)
                    session.commit()
                    session.refresh(pic)
                    return pic

                pic = self._db.run_task(
                    set_description, pic.id, description, priority=DBPriority.LOW
                )
                assert pic.description == description

                descriptions_generated.append(pic)

            except Exception as e:
                logger.error(
                    f"Failed to generate/store description for picture {pic.id}: {e}"
                )
        return descriptions_generated


class TagWorker(BaseWorker):
    """
    Worker for generating tags for pictures with descriptions.
    """

    def worker_type(self) -> WorkerType:
        return WorkerType.TAGGER  # Or define a new WorkerType if desired

    def _run(self):
        while not self._stop.is_set():
            try:
                start = time.time()
                logger.debug("TaggingWorker: Starting iteration...")
                missing_tags = self._fetch_missing_tags()
                logger.debug(
                    f"TaggingWorker: Got {len(missing_tags)} pictures needing tags."
                )
                if self._stop.is_set():
                    break
                tagged_pictures = self._tag_pictures(missing_tags)
                self._notify_ids_processed(tagged_pictures)
                logger.debug(f"TaggingWorker: Tagged {len(tagged_pictures)} pictures.")
                timing = time.time() - start
                if tagged_pictures:
                    picture_ids = [pic_id for _, pic_id, _, _ in tagged_pictures]
                    self._notify_others(EventType.CHANGED_TAGS, picture_ids)
                    logger.debug(
                        f"TaggingWorker: Done after {timing:.2f} seconds. Having updated {len(tagged_pictures)} pictures."
                    )
                else:
                    logger.debug(
                        f"TaggingWorker: Sleeping after {timing:.2f} seconds. No work needed."
                    )
                    self._wait()
            except Exception as e:
                import traceback

                logger.error(
                    "TaggingWorker thread exiting due to error: %s\n%s",
                    e,
                    traceback.format_exc(),
                )
                break
        logger.info("Exiting TaggingWorker loop.")

    def _load_feature_tag_blacklists(self):
        cached = getattr(self, "_feature_tag_blacklists", None)
        if cached is not None:
            return cached

        face_blacklist = set(FACE_TAG_BLACKLIST)
        hand_blacklist = set(HAND_TAG_BLACKLIST)
        self._feature_tag_blacklists = (face_blacklist, hand_blacklist)
        return self._feature_tag_blacklists

    def _fetch_missing_tags(self):
        logger.debug("Starting the database fetch for missing tags")

        picture_ids = []
        while True:
            try:
                payload = self._queue.get_nowait()
                if isinstance(payload, (list, tuple, set)):
                    picture_ids.extend(payload)
                elif isinstance(payload, int):
                    picture_ids.append(payload)
            except queue.Empty:
                break

        queued_ids = sorted({pid for pid in picture_ids if pid is not None})

        def fetch_tags(session: Session, queued_ids):
            if queued_ids:
                statement = (
                    select(Picture)
                    .where(Picture.id.in_(queued_ids))
                    .options(
                        selectinload(Picture.tags),
                        selectinload(Picture.faces),
                        selectinload(Picture.hands),
                    )
                )
            else:
                statement = (
                    select(Picture)
                    .where(~Picture.tags.any())
                    .options(
                        selectinload(Picture.tags),
                        selectinload(Picture.faces),
                        selectinload(Picture.hands),
                    )
                )
            result = session.exec(statement)
            return result.all()

        return VaultDatabase.result_or_throw(
            self._db.submit_task(fetch_tags, queued_ids)
        )

    def _tag_pictures(self, missing_tags) -> int:
        """Tag all pictures missing tags."""
        assert missing_tags is not None
        if self._stop.is_set():
            return []
        batch = missing_tags[: self._picture_tagger.max_concurrent_images()]
        image_paths = []
        pic_by_path = {}
        for pic in batch:
            file_path = PictureUtils.resolve_picture_path(
                self._db.image_root, pic.file_path
            )
            image_paths.append(file_path)
            pic_by_path[file_path] = pic

        tagged_pictures = []
        if image_paths:
            if self._stop.is_set():
                return []
            logger.debug(f"Tagging {len(image_paths)} images: {image_paths}")
            tag_results = self._picture_tagger.tag_images(
                image_paths, stop_event=self._stop
            )
            crop_tags_by_pic_id = {}
            face_tags_by_face_id = {}
            hand_tags_by_hand_id = {}
            face_blacklist, hand_blacklist = self._load_feature_tag_blacklists()
            if self._picture_tagger.custom_tagger_ready():
                try:
                    import cv2
                    from PIL import Image

                    enable_face_crops = True
                    enable_hand_crops = True
                    hand_min_area_ratio = HAND_CROP_MIN_AREA_RATIO
                    hand_max_per_picture = HAND_CROP_MAX_PER_PICTURE
                    crop_debug_enabled = CROP_DEBUG_ENABLED

                    face_items = []
                    hand_items = []
                    item_to_pic_id = {}
                    item_to_face_id = {}
                    item_to_hand_id = {}
                    crop_debug_dir = "/tmp/pixlvault_crops"
                    if crop_debug_enabled:
                        os.makedirs(crop_debug_dir, exist_ok=True)

                    def save_crop_debug(crop, prefix, pic_id):
                        if not crop_debug_enabled:
                            return
                        if crop is None or crop.size == 0:
                            return
                        name = f"{prefix}_{pic_id}_{uuid.uuid4().hex[:8]}.png"
                        path = os.path.join(crop_debug_dir, name)
                        try:
                            cv2.imwrite(path, crop)
                            logger.info("Saved crop: %s", path)
                        except Exception as exc:
                            logger.warning("Failed to write crop %s: %s", path, exc)

                    video_exts = {
                        ".mp4",
                        ".avi",
                        ".mov",
                        ".mkv",
                        ".webm",
                        ".flv",
                        ".wmv",
                    }
                    for pic in batch:
                        if self._stop.is_set():
                            break
                        file_path = PictureUtils.resolve_picture_path(
                            self._db.image_root, pic.file_path
                        )
                        ext = os.path.splitext(file_path)[1].lower()
                        if ext in video_exts:
                            continue
                        frame = cv2.imread(file_path)
                        if frame is None:
                            logger.warning(
                                "Failed to read image for crop tagging: %s",
                                file_path,
                            )
                            continue
                        frame_h, frame_w = frame.shape[:2]
                        frame_area = max(1, frame_w * frame_h)
                        if enable_face_crops:
                            faces = getattr(pic, "faces", None) or []
                            for face in faces:
                                if getattr(face, "face_index", 0) < 0:
                                    continue
                                bbox = getattr(face, "bbox", None)
                                crop = PictureUtils.crop_face_from_frame(frame, bbox)
                                if crop is None:
                                    logger.debug(
                                        "Face crop failed for %s bbox=%s",
                                        file_path,
                                        bbox,
                                    )
                                    continue
                                save_crop_debug(crop, "face", pic.id)
                                try:
                                    crop_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
                                    crop_img = Image.fromarray(crop_rgb)
                                except Exception as e:
                                    logger.debug(
                                        "Face crop conversion failed for %s bbox=%s: %s",
                                        file_path,
                                        bbox,
                                        e,
                                    )
                                    continue
                                key = f"{file_path}#face{face.id or face.face_index}"
                                face_items.append((key, crop_img))
                                item_to_pic_id[key] = pic.id
                                if face.id is not None:
                                    item_to_face_id[key] = face.id

                        if enable_hand_crops:
                            hands = getattr(pic, "hands", None) or []
                            hand_candidates = []
                            for hand in hands:
                                if getattr(hand, "hand_index", 0) < 0:
                                    continue
                                bbox = getattr(hand, "bbox", None)
                                if bbox is None or len(bbox) != 4:
                                    continue
                                x1, y1, x2, y2 = bbox
                                x1 = int(max(0, min(frame_w - 1, round(x1))))
                                y1 = int(max(0, min(frame_h - 1, round(y1))))
                                x2 = int(max(0, min(frame_w, round(x2))))
                                y2 = int(max(0, min(frame_h, round(y2))))
                                if x2 <= x1 or y2 <= y1:
                                    continue
                                area = (x2 - x1) * (y2 - y1)
                                if hand_min_area_ratio > 0:
                                    if area / frame_area < hand_min_area_ratio:
                                        continue
                                hand_candidates.append((area, (x1, y1, x2, y2), hand))

                            if hand_candidates:
                                hand_candidates.sort(
                                    key=lambda item: item[0], reverse=True
                                )
                                if hand_max_per_picture > 0:
                                    hand_candidates = hand_candidates[
                                        :hand_max_per_picture
                                    ]

                            for _, bbox, hand in hand_candidates:
                                crop = PictureUtils.crop_face_from_frame(frame, bbox)
                                if crop is None:
                                    logger.debug(
                                        "Hand crop failed for %s bbox=%s",
                                        file_path,
                                        bbox,
                                    )
                                    continue
                                save_crop_debug(crop, "hand", pic.id)
                                try:
                                    crop_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
                                    crop_img = Image.fromarray(crop_rgb)
                                except Exception as e:
                                    logger.warning(
                                        "Hand crop conversion failed for %s bbox=%s: %s",
                                        file_path,
                                        bbox,
                                        e,
                                    )
                                    continue
                                key = f"{file_path}#hand{hand.id or hand.hand_index}"
                                hand_items.append((key, crop_img))
                                item_to_pic_id[key] = pic.id
                                if hand.id is not None:
                                    item_to_hand_id[key] = hand.id

                    if face_items and not self._stop.is_set():
                        face_results = self._picture_tagger._tag_custom_items(
                            face_items,
                            stop_event=self._stop,
                            threshold=self._picture_tagger.custom_tagger_threshold_face(),
                            image_size=self._picture_tagger.custom_tagger_image_size_face(),
                        )
                        for key, tags in face_results.items():
                            pic_id = item_to_pic_id.get(key)
                            if pic_id is None or not tags:
                                continue
                            existing = crop_tags_by_pic_id.get(pic_id, [])
                            existing.extend(tags)
                            crop_tags_by_pic_id[pic_id] = existing
                            face_id = item_to_face_id.get(key)
                            if face_id is not None:
                                existing_face = face_tags_by_face_id.get(face_id, [])
                                filtered = [tag for tag in tags if is_face_tag(tag)]
                                existing_face.extend(filtered)
                                face_tags_by_face_id[face_id] = existing_face

                    if hand_items and not self._stop.is_set():
                        hand_results = self._picture_tagger._tag_custom_items(
                            hand_items,
                            stop_event=self._stop,
                            threshold=self._picture_tagger.custom_tagger_threshold_hand(),
                            image_size=self._picture_tagger.custom_tagger_image_size_hand(),
                        )
                        for key, tags in hand_results.items():
                            pic_id = item_to_pic_id.get(key)
                            if pic_id is None or not tags:
                                continue
                            existing = crop_tags_by_pic_id.get(pic_id, [])
                            existing.extend(tags)
                            crop_tags_by_pic_id[pic_id] = existing
                            hand_id = item_to_hand_id.get(key)
                            if hand_id is not None:
                                existing_hand = hand_tags_by_hand_id.get(hand_id, [])
                                filtered = [tag for tag in tags if is_hand_tag(tag)]
                                existing_hand.extend(filtered)
                                hand_tags_by_hand_id[hand_id] = existing_hand
                except Exception as e:
                    logger.warning("Crop tagging failed: %s", e)

            merged_results = {}
            for path in image_paths:
                pic = pic_by_path.get(path)
                if not pic:
                    continue
                base_tags = tag_results.get(path, [])
                extra_tags = crop_tags_by_pic_id.get(pic.id, [])
                combined = sorted(set(base_tags) | set(extra_tags))
                if combined:
                    merged_results[path] = combined
            tag_results = merged_results
            logger.debug(f"Got tag results for {len(tag_results)} images.")
            for path, tags in tag_results.items():
                pic = pic_by_path.get(path)
                logger.debug(f"Processing tags for image at path: {path}: {tags}")
                if tags:
                    face_map = {}
                    for face in getattr(pic, "faces", []) or []:
                        if getattr(face, "face_index", 0) < 0:
                            continue
                        if face.id is None:
                            continue
                        raw = face_tags_by_face_id.get(face.id, [])
                        if raw:
                            face_map[face.id] = sorted(set(raw))

                    hand_map = {}
                    for hand in getattr(pic, "hands", []) or []:
                        if getattr(hand, "hand_index", 0) < 0:
                            continue
                        if hand.id is None:
                            continue
                        raw = hand_tags_by_hand_id.get(hand.id, [])
                        if raw:
                            hand_map[hand.id] = sorted(set(raw))

                    def add_tags(session: Session, pic_id, tags, face_map, hand_map):
                        tag_ids = session.exec(
                            select(Tag.id).where(Tag.picture_id == pic_id)
                        ).all()
                        if tag_ids:
                            session.exec(
                                delete(FaceTag).where(FaceTag.tag_id.in_(tag_ids))
                            )
                            session.exec(
                                delete(HandTag).where(HandTag.tag_id.in_(tag_ids))
                            )
                        session.exec(delete(Tag).where(Tag.picture_id == pic_id))

                        pic = session.exec(
                            select(Picture)
                            .where(Picture.id == pic_id)
                            .options(selectinload(Picture.tags))
                        ).one()
                        existing = {t.tag: t for t in pic.tags}
                        for tag_value in tags:
                            if tag_value in existing:
                                continue
                            new_tag = Tag(picture_id=pic_id, tag=tag_value)
                            session.add(new_tag)
                            existing[tag_value] = new_tag

                        session.flush()

                        for face_id, face_tags in face_map.items():
                            for tag_value in face_tags:
                                tag_obj = existing.get(tag_value)
                                if tag_obj is None:
                                    tag_obj = Tag(picture_id=pic_id, tag=tag_value)
                                    session.add(tag_obj)
                                    session.flush()
                                    existing[tag_value] = tag_obj
                                exists = session.exec(
                                    select(FaceTag).where(
                                        FaceTag.face_id == face_id,
                                        FaceTag.tag_id == tag_obj.id,
                                    )
                                ).first()
                                if exists is None:
                                    session.add(
                                        FaceTag(face_id=face_id, tag_id=tag_obj.id)
                                    )

                        for hand_id, hand_tags in hand_map.items():
                            for tag_value in hand_tags:
                                tag_obj = existing.get(tag_value)
                                if tag_obj is None:
                                    tag_obj = Tag(picture_id=pic_id, tag=tag_value)
                                    session.add(tag_obj)
                                    session.flush()
                                    existing[tag_value] = tag_obj
                                exists = session.exec(
                                    select(HandTag).where(
                                        HandTag.hand_id == hand_id,
                                        HandTag.tag_id == tag_obj.id,
                                    )
                                ).first()
                                if exists is None:
                                    session.add(
                                        HandTag(hand_id=hand_id, tag_id=tag_obj.id)
                                    )

                        session.commit()
                        session.refresh(pic)
                        return pic

                    pic = self._db.run_task(
                        add_tags,
                        pic.id,
                        tags,
                        face_map,
                        hand_map,
                        priority=DBPriority.LOW,
                    )
                    tagged_pictures.append((Picture, pic.id, "tags", tags))

        return tagged_pictures


class EmbeddingWorker(BaseWorker):
    """
    Worker for generating text embeddings for pictures with descriptions.
    """

    EMBEDDING_BATCH_SIZE = 32

    def worker_type(self) -> WorkerType:
        return WorkerType.TEXT_EMBEDDING

    def _run(self):
        while not self._stop.is_set():
            try:
                start = time.time()
                logger.debug("[EMBEDDING WORKER]  Starting iteration...")
                embeddings_updated = 0
                pictures_to_embed = self._fetch_missing_text_embeddings()
                logger.debug(
                    f"[EMBEDDING WORKER]  Got {len(pictures_to_embed)} pictures needing embeddings."
                )
                if self._stop.is_set():
                    break
                embeddings_generated = self._generate_text_embeddings(pictures_to_embed)
                logger.debug(
                    f"[EMBEDDING WORKER]  Generated {len(embeddings_generated)} embeddings."
                )
                if self._stop.is_set():
                    break
                if embeddings_generated:
                    changed = self._update_text_embeddings(embeddings_generated)
                    embeddings_updated = len(changed)
                timing = time.time() - start
                if embeddings_updated > 0:
                    logger.debug(
                        f"[EMBEDDING WORKER]  Done after {timing:.2f} seconds. Having updated {embeddings_updated} pictures."
                    )
                else:
                    logger.debug(
                        f"[EMBEDDING WORKER]  Sleeping after {timing:.2f} seconds. No work needed."
                    )
                    self._wait()
            except Exception as e:
                logger.debug(
                    f"EmbeddingWorker thread exiting due to DB error (likely shutdown): {e}"
                )
                break
        logger.info("Exiting EmbeddingWorker loop.")

    def _fetch_missing_text_embeddings(self):
        """Return Pictures needing text embeddings."""

        def find_pictures_without_embeddings(session: Session):
            # Only load fields needed for text embedding
            query = select(Picture)
            query = query.options(
                load_only(Picture.id, Picture.description, Picture.text_embedding),
                selectinload(Picture.tags),
                selectinload(Picture.characters).load_only(
                    Character.id,
                    Character.name,
                    Character.description,
                ),
            )
            query = query.where(Picture.text_embedding.is_(None))
            query = query.where(Picture.description.is_not(None))
            query = query.order_by(Picture.id)
            query = query.limit(self.EMBEDDING_BATCH_SIZE)
            results = session.exec(query)
            return results.all()

        return VaultDatabase.result_or_throw(
            self._db.submit_task(find_pictures_without_embeddings)
        )

    def _generate_text_embeddings(self, pictures_to_embed):
        """
        Generate text embeddings for a batch of PictureModel objects using PictureTagger.
        Returns the number of pictures updated.
        """
        embeddings = self._picture_tagger.generate_text_embedding(
            pictures=pictures_to_embed
        )
        if not embeddings:
            return []

        if len(embeddings) != len(pictures_to_embed):
            logger.warning(
                "[EMBEDDING WORKER] Embedding count mismatch: embeddings=%s pictures=%s",
                len(embeddings),
                len(pictures_to_embed),
            )

        limit = min(len(embeddings), len(pictures_to_embed))
        for pic, embedding in zip(pictures_to_embed[:limit], embeddings[:limit]):
            pic.text_embedding = embedding

        return pictures_to_embed[:limit]

    def _update_text_embeddings(self, pictures: list[Picture]):
        """
        Update the text embeddings for a picture in the database, with detailed logging.
        """

        def update_pictures(session: Session, pics):
            changed = []
            for pic in pics:
                db_pic = session.get(Picture, pic.id)
                if db_pic:
                    db_pic.text_embedding = pic.text_embedding
                    session.add(db_pic)
                    changed.append(
                        (Picture, pic.id, "text_embedding", pic.text_embedding)
                    )
            session.commit()
            logger.debug(
                f"[EMBEDDING WORKER] Committed {len(changed)} embedding updates to DB."
            )
            return changed

        changed = self._db.run_task(update_pictures, pictures, priority=DBPriority.LOW)
        self._notify_ids_processed(changed)
        return changed
