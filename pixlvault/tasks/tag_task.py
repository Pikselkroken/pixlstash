import cv2
from PIL import Image
import os

from sqlmodel import Session, select, delete

from pixlvault.database import DBPriority
from pixlvault.db_models import FaceTag, HandTag, Picture, Tag
from pixlvault.feature_tag_blacklist import is_face_tag, is_hand_tag
from pixlvault.picture_utils import PictureUtils
from pixlvault.pixl_logging import get_logger
from pixlvault.task_runner import BaseTask


logger = get_logger(__name__)


class TagTask(BaseTask):
    """Task that tags a batch of pictures and persists tag updates."""

    HAND_CROP_MIN_AREA_RATIO = 0.01
    HAND_CROP_MAX_PER_PICTURE = 2
    CROP_DEBUG_ENABLED = False

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
    def _custom_tagger_tags(tags: list[str]) -> list[str]:
        replacements = {
            "extra fingers": "extra digit",
            "malformed hands": "malformed hand",
        }
        normalized = []
        for tag in tags:
            normalized.append(replacements.get(tag, tag))
        return normalized

    @staticmethod
    def _add_tags_bulk(session: Session, updates: list[dict]):
        updated_ids = []
        for update in updates:
            pic_id = update.get("pic_id")
            if pic_id is None:
                continue
            tags = update.get("tags") or []
            face_map = update.get("face_map") or {}
            hand_map = update.get("hand_map") or {}

            existing_tags = session.exec(
                select(Tag.tag).where(Tag.picture_id == pic_id)
            ).all()
            existing_tag_set = {
                row[0] if isinstance(row, tuple) else row
                for row in existing_tags
                if row
            }
            if not face_map and not hand_map and set(tags) == existing_tag_set:
                continue

            tag_ids = session.exec(select(Tag.id).where(Tag.picture_id == pic_id)).all()
            tag_ids = [
                row[0] if isinstance(row, tuple) else row
                for row in tag_ids
                if row is not None
            ]
            if tag_ids:
                session.exec(delete(FaceTag).where(FaceTag.tag_id.in_(tag_ids)))
                session.exec(delete(HandTag).where(HandTag.tag_id.in_(tag_ids)))
            session.exec(delete(Tag).where(Tag.picture_id == pic_id))

            all_tag_values = set(tags)
            for face_tags in face_map.values():
                all_tag_values.update(face_tags)
            for hand_tags in hand_map.values():
                all_tag_values.update(hand_tags)

            tag_objs = {}
            for tag_value in all_tag_values:
                tag_obj = Tag(picture_id=pic_id, tag=tag_value)
                session.add(tag_obj)
                tag_objs[tag_value] = tag_obj

            session.flush()

            for face_id, face_tags in face_map.items():
                for tag_value in face_tags:
                    tag_obj = tag_objs.get(tag_value)
                    if tag_obj is None:
                        continue
                    session.add(FaceTag(face_id=face_id, tag_id=tag_obj.id))

            for hand_id, hand_tags in hand_map.items():
                for tag_value in hand_tags:
                    tag_obj = tag_objs.get(tag_value)
                    if tag_obj is None:
                        continue
                    session.add(HandTag(hand_id=hand_id, tag_id=tag_obj.id))

            updated_ids.append(pic_id)

        session.commit()
        return updated_ids

    def _tag_pictures_batch(self) -> list:
        assert self._pictures is not None

        batch = self._pictures[
            : max(1, int(self._picture_tagger.max_concurrent_images()))
        ]
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
            logger.debug("Tagging %s images", len(image_paths))
            logger.debug("Tagging image paths: %s", image_paths)
            tag_results = self._picture_tagger.tag_images(image_paths)
            crop_tags_by_pic_id = {}
            face_tags_by_face_id = {}
            hand_tags_by_hand_id = {}
            if self._picture_tagger.custom_tagger_ready():
                try:
                    enable_face_crops = True
                    enable_hand_crops = True
                    hand_min_area_ratio = self.HAND_CROP_MIN_AREA_RATIO
                    hand_max_per_picture = self.HAND_CROP_MAX_PER_PICTURE
                    crop_debug_enabled = self.CROP_DEBUG_ENABLED

                    all_items = []
                    item_to_pic_id = {}
                    item_to_face_id = {}
                    item_to_hand_id = {}
                    item_to_kind = {}
                    crop_debug_dir = "/tmp/pixlvault_crops"
                    if crop_debug_enabled:
                        os.makedirs(crop_debug_dir, exist_ok=True)

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
                        faces = getattr(pic, "faces", None) or []
                        hands = getattr(pic, "hands", None) or []
                        has_face_crops = enable_face_crops and any(
                            getattr(face, "face_index", 0) >= 0 for face in faces
                        )
                        has_hand_crops = enable_hand_crops and any(
                            getattr(hand, "hand_index", 0) >= 0 for hand in hands
                        )
                        if not has_face_crops and not has_hand_crops:
                            continue
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
                        if has_face_crops:
                            for face in faces:
                                if getattr(face, "face_index", 0) < 0:
                                    continue
                                bbox = getattr(face, "bbox", None)
                                clamped = PictureUtils.clamp_bbox(
                                    bbox, frame_w, frame_h
                                )
                                if clamped is None:
                                    continue
                                crop = PictureUtils.crop_face_from_frame(frame, clamped)
                                if crop is None:
                                    logger.debug(
                                        "Face crop failed for %s bbox=%s",
                                        file_path,
                                        clamped,
                                    )
                                    continue

                                try:
                                    crop_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
                                    crop_img = Image.fromarray(crop_rgb)
                                except Exception as exc:
                                    logger.debug(
                                        "Face crop conversion failed for %s: %s",
                                        file_path,
                                        exc,
                                    )
                                    continue
                                padded = PictureUtils.pad_image_to_square(crop_img)
                                if padded is None:
                                    continue
                                key = f"{file_path}#face{face.id or face.face_index}"
                                all_items.append((key, padded))
                                item_to_pic_id[key] = pic.id
                                item_to_kind[key] = "face"
                                if face.id is not None:
                                    item_to_face_id[key] = face.id

                        if has_hand_crops:
                            hand_candidates = []
                            for hand in hands:
                                if getattr(hand, "hand_index", 0) < 0:
                                    continue
                                bbox = getattr(hand, "bbox", None)
                                clamped = PictureUtils.clamp_bbox(
                                    bbox, frame_w, frame_h
                                )
                                if clamped is None:
                                    continue
                                ex1, ey1, ex2, ey2 = clamped
                                area = (ex2 - ex1) * (ey2 - ey1)
                                if (
                                    hand_min_area_ratio > 0
                                    and area / frame_area < hand_min_area_ratio
                                ):
                                    continue
                                hand_candidates.append((area, clamped, hand))

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

                                try:
                                    crop_rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
                                    crop_img = Image.fromarray(crop_rgb)
                                except Exception as exc:
                                    logger.warning(
                                        "Hand crop conversion failed for %s bbox=%s: %s",
                                        file_path,
                                        bbox,
                                        exc,
                                    )
                                    continue
                                padded = PictureUtils.pad_image_to_square(crop_img)
                                if padded is None:
                                    continue
                                key = f"{file_path}#hand{hand.id or hand.hand_index}"
                                all_items.append((key, padded))
                                item_to_pic_id[key] = pic.id
                                item_to_kind[key] = "hand"
                                if hand.id is not None:
                                    item_to_hand_id[key] = hand.id

                    if all_items:
                        unified_results = self._picture_tagger._tag_custom_items(
                            all_items,
                            threshold=self._picture_tagger.custom_tagger_threshold_crops(),
                            image_size=self._picture_tagger.custom_tagger_image_size_crops(),
                        )
                        logger.debug(
                            "Crop tagging results: %s items",
                            len(unified_results or {}),
                        )
                        for key, tags in unified_results.items():
                            pic_id = item_to_pic_id.get(key)
                            if pic_id is None or not tags:
                                continue
                            kind = item_to_kind.get(key)
                            if "blurry" in tags:
                                logger.debug(
                                    "[TAG SOURCE] pic_id=%s source=%s tag=blurry",
                                    pic_id,
                                    kind or "crop",
                                )
                            if kind == "hand":
                                custom_tags = self._custom_tagger_tags(tags)
                                filtered = [
                                    tag for tag in custom_tags if is_hand_tag(tag)
                                ]
                                existing = crop_tags_by_pic_id.get(pic_id, [])
                                existing.extend(filtered)
                                crop_tags_by_pic_id[pic_id] = existing
                                hand_id = item_to_hand_id.get(key)
                                if hand_id is not None:
                                    existing_hand = hand_tags_by_hand_id.get(
                                        hand_id, []
                                    )
                                    existing_hand.extend(filtered)
                                    hand_tags_by_hand_id[hand_id] = existing_hand
                            else:
                                existing = crop_tags_by_pic_id.get(pic_id, [])
                                existing.extend(tags)
                                crop_tags_by_pic_id[pic_id] = existing
                                face_id = item_to_face_id.get(key)
                                if face_id is not None:
                                    existing_face = face_tags_by_face_id.get(
                                        face_id, []
                                    )
                                    filtered = [tag for tag in tags if is_face_tag(tag)]
                                    existing_face.extend(filtered)
                                    face_tags_by_face_id[face_id] = existing_face
                except Exception as exc:
                    logger.warning("Crop tagging failed: %s", exc)

            merged_results = {}
            for path in image_paths:
                pic = pic_by_path.get(path)
                if not pic:
                    continue
                base_tags = tag_results.get(path, [])
                if "blurry" in base_tags:
                    logger.debug(
                        "[TAG SOURCE] pic_id=%s source=full tag=blurry", pic.id
                    )
                extra_tags = crop_tags_by_pic_id.get(pic.id, [])
                combined = sorted(set(base_tags) | set(extra_tags))
                if combined:
                    merged_results[path] = combined
            tag_results = merged_results
            logger.debug("Got tag results for %s images.", len(tag_results))

            update_payloads = []
            for path, tags in tag_results.items():
                pic = pic_by_path.get(path)
                logger.debug("Processing tags for image at path: %s: %s", path, tags)
                if not tags:
                    continue

                face_map = {}
                requires_face_tags = {"face", "close-up"}
                inherits_photo_tag = "photo" in tags
                if inherits_photo_tag:
                    requires_face_tags.add("photo")

                for face in getattr(pic, "faces", []) or []:
                    if getattr(face, "face_index", 0) < 0:
                        continue
                    if face.id is None:
                        continue
                    raw = face_tags_by_face_id.get(face.id, [])
                    combined = set(raw) | requires_face_tags
                    if combined:
                        face_map[face.id] = sorted(combined)

                hand_map = {}
                requires_hand_tags = {"hand", "close-up"}
                if inherits_photo_tag:
                    requires_hand_tags.add("photo")

                for hand in getattr(pic, "hands", []) or []:
                    if getattr(hand, "hand_index", 0) < 0:
                        continue
                    if hand.id is None:
                        continue
                    raw = hand_tags_by_hand_id.get(hand.id, [])
                    combined = set(raw) | requires_hand_tags
                    if combined:
                        hand_map[hand.id] = sorted(combined)

                update_payloads.append(
                    {
                        "pic_id": pic.id,
                        "tags": tags,
                        "face_map": face_map,
                        "hand_map": hand_map,
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
