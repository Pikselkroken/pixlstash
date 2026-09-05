from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from sqlalchemy import func
from sqlmodel import Session, select, delete
import os
import threading
import time
from sqlalchemy.exc import IntegrityError

from PIL import Image as PILImage
from PIL import ImageOps

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    Face,
    Picture,
    Tag,
    TAG_SENTINEL_LIKE_PATTERN,
    TAG_SENTINEL_ESCAPE_CHAR,
)
from pixlstash.db_models.tag_prediction import (
    feeds_anomaly_score,
    is_plugin_model_version,
    TagPrediction,
)
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.image_processing.video_utils import VideoUtils
from pixlstash.utils.image_processing.face_utils import expand_bbox_to_square
from pixlstash.utils.service.smart_score_invalidation import (
    anomaly_state_signature,
    invalidate_changed_anomaly_scores,
    invalidate_on_anomaly_change,
)
from pixlstash.utils.service.tag_prediction_utils import PENALISED_TAG_SET
from pixlstash.inference.workflows.tagging import TaggingWorkflow
from pixlstash.inference.engine import InferenceEngine
from pixlstash.tagger_plugins.pixlstash_tagger import (
    CENTRE_CROP_TAG_WHITELIST,
    QUALITY_CROP_TAG_WHITELIST,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.services.set_lock_service import locked_picture_ids
from pixlstash.tasks.base_task import BaseTask, QueueType, TaskPriority


logger = get_logger(__name__)

# Extensions loaded through the video frame extractor rather than PIL. Only a
# hint for picking the *first* decoder to try: a file whose extension lies is
# still resolved by the shared loader in `_load_pic`.
_VIDEO_EXTS = frozenset({".mp4", ".avi", ".mov", ".mkv", ".webm", ".flv", ".wmv"})


def _is_transient_load_error(exc: BaseException) -> bool:
    """True when *exc* means the machine failed, not that the file is corrupt.

    This is the gate on `mark_unprocessable`. A mark suppresses the picture in
    `base_task_finder._filter_and_claim`, i.e. for **every** batch finder -
    thumbnails, embeddings and quality as well as tagging - until the file's
    mtime/size changes, and `vault._count_missing_*` subtracts suppressed ids
    from the "remaining" counters, so the loss does not even show in the UI.
    Marking a good picture because the machine ran out of file descriptors would
    therefore disable it silently for the rest of the server session.

    `OSError.errno` is the discriminator, and it is exact: a real filesystem or
    resource failure carries one (``EMFILE`` 24, ``EIO`` 5, ``ESTALE`` 116),
    while PIL's decode failures do not - both `UnidentifiedImageError` and the
    ``"image file is truncated"`` `OSError` leave it `None`.
    """
    if isinstance(exc, MemoryError):
        return True
    return isinstance(exc, OSError) and exc.errno is not None


def _file_is_readable(file_path: str) -> bool:
    """True when the file's bytes can be read at all.

    The last check before declaring a picture undecodable, for the decoders that
    swallow their own errors: `extract_representative_video_frames` returns `[]`
    whenever `cv2.VideoCapture` fails to open, which includes failing under file
    descriptor exhaustion. Without this, that path marks a good video.
    """
    try:
        with open(file_path, "rb") as handle:
            handle.read(1)
        return True
    except OSError as exc:
        logger.warning(
            "TagTask: %s could not be read (%s); not marking it unprocessable",
            file_path,
            exc,
        )
        return False


class TagTask(BaseTask):
    """Task that tags a batch of pictures and persists tag updates."""

    CPU_SPILLOVER_REUSE_GRACE_S = 8.0
    _cpu_spillover_engine: InferenceEngine | None = None
    _cpu_spillover_last_used_at: float = 0.0
    _cpu_spillover_lock = threading.Lock()

    # Tagging is low-priority relative to face extraction.  Uses the shared
    # GPU queue: serialised by the single GPU worker.
    # Face extraction (HIGH) always precedes tagging in the queue.

    def __init__(
        self,
        database,
        tagging_workflow: TaggingWorkflow,
        pictures: list,
        interactive: bool = False,
        engine_override: str | None = None,
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
        self._tagging_workflow = tagging_workflow
        self._pictures = pictures or []
        self._interactive = interactive
        self._engine_override = engine_override
        self._preloaded_images: dict[str, PILImage.Image] = {}
        self._preload_lock = threading.Lock()
        self._preload_thread: threading.Thread | None = None
        self._preload_cancel = threading.Event()
        self._preload_started_at: float | None = None
        self._preload_finished_at: float | None = None
        self._cpu_spillover_enabled = False
        self._model_preload_thread: threading.Thread | None = None
        self._model_preload_done: threading.Event = threading.Event()
        self._model_preload_error: Exception | None = None

    def on_queued(self) -> None:
        # Start image preloading immediately so it overlaps with model loading.
        if self._preload_thread is None or not self._preload_thread.is_alive():
            self._preload_cancel.clear()
            self._preload_started_at = time.perf_counter()
            self._preload_finished_at = None
            self._preload_thread = threading.Thread(
                target=self._preload_images,
                name=f"TagTaskPreload-{self.id[:8]}",
                daemon=True,
            )
            self._preload_thread.start()

        # Start model loading in a background thread and wait for it to finish
        # before returning.  submit() puts the task in the GPU queue only after
        # on_queued() returns, so the GPU worker will never block on model load.
        if self._model_preload_thread is None:
            self._model_preload_thread = threading.Thread(
                target=self._preload_model,
                name=f"TagModelPreload-{self.id[:8]}",
                daemon=True,
            )
            self._model_preload_thread.start()
        self._model_preload_done.wait()

    def _preload_model(self) -> None:
        t_start = time.perf_counter()
        try:
            self._tagging_workflow.ensure_active_plugin_ready(
                engine_override=self._engine_override,
            )
            logger.debug(
                "[TAG_MODEL_PRELOAD] task_id=%s done in %.1fs",
                self.id,
                time.perf_counter() - t_start,
            )
        except Exception as exc:
            logger.warning(
                "[TAG_MODEL_PRELOAD] task_id=%s failed after %.1fs: %s",
                self.id,
                time.perf_counter() - t_start,
                exc,
            )
            self._model_preload_error = exc
        finally:
            self._model_preload_done.set()

    def on_cancel(self) -> None:
        self._preload_cancel.set()
        if self._preload_thread is not None:
            self._preload_thread.join(timeout=10)
            if self._preload_thread.is_alive():
                logger.warning(
                    "TagTask preload thread did not stop in time for task %s",
                    self.id,
                )
        # Model loading cannot be interrupted; just wait briefly so the event
        # is set and any subsequent wait() calls return promptly.
        if self._model_preload_thread is not None:
            self._model_preload_thread.join(timeout=5)

    _PRELOAD_WORKERS = 4

    def _load_pic(self, pic) -> "tuple[str | None, PILImage.Image | None, bool]":
        """Load *pic*'s image for tagging.

        Args:
            pic: The picture to load.

        Returns:
            ``(file_path, image, undecodable)``. ``undecodable`` is True **only**
            when the file's bytes were readable and still could not be decoded by
            any loader - the sole condition under which the caller may mark it in
            the unprocessable registry (see `_is_transient_load_error` for why
            that distinction has to be exact).

        The extension only picks which decoder is tried first. Whatever it says,
        a failure falls back to `ImageUtils.load_image_or_video`, the loader the
        thumbnail, quality and embedding tasks use, so this path can never
        suppress a picture those pipelines are able to read - a real mp4 named
        `.png` is common enough with re-encoded downloads to matter.
        """
        file_path = ImageUtils.resolve_picture_path(self._db.image_root, pic.file_path)
        if not file_path:
            logger.warning(
                "TagTask: picture %s has no resolvable file path",
                getattr(pic, "id", None),
            )
            return None, None, False

        try:
            ext = os.path.splitext(str(file_path))[1].lower()
            if ext in _VIDEO_EXTS:
                frames = VideoUtils.extract_representative_video_frames(
                    str(file_path), count=1
                )
                if frames:
                    return file_path, frames[0].convert("RGB"), False
                logger.debug(
                    "TagTask: no frames extracted from %s; trying the shared loader",
                    file_path,
                )
            else:
                # exif_transpose, because everything this image is measured
                # against is already transposed. Face bboxes come from
                # `load_image_bgr_reduced`, which transposes: a phone photo with
                # orientation 6 is 4608x2592 on disk and 2592x4608 to anyone
                # looking at it, so a recorded bbox at y=3699 lands past the
                # bottom of the frame this loader used to return.
                # `expand_bbox_to_square` then clamped one edge and not the
                # other, and PIL refused the crop ("Coordinate 'lower' is less
                # than 'upper'").
                #
                # The refusal was the loud half. The quiet half is worse: with
                # the face nearer the top the box stayed valid and the crop came
                # out of the wrong part of a sideways picture - and the
                # whole-image tagging pass ran on that same sideways image, for
                # every rotated photo in the library.
                return (
                    file_path,
                    ImageOps.exif_transpose(PILImage.open(file_path)).convert("RGB"),
                    False,
                )
        except Exception as exc:
            if _is_transient_load_error(exc):
                logger.warning(
                    "TagTask: transient failure loading %s (%s); leaving it to retry",
                    file_path,
                    exc,
                )
                return file_path, None, False
            logger.debug(
                "TagTask: %s did not decode by extension (%s); trying the shared loader",
                file_path,
                exc,
            )

        # The extension lied, or the primary decoder failed on a file that is not
        # a resource problem. Ask the loader every suppressed pipeline uses before
        # concluding anything; it logs its own cause at ERROR.
        array = ImageUtils.load_image_or_video(str(file_path))
        if array is not None:
            return file_path, PILImage.fromarray(array), False
        return file_path, None, _file_is_readable(str(file_path))

    def _build_quality_crop(self, pic, faces, target, preloaded_images):
        """Build the high-resolution quality crop for one picture.

        Args:
            pic: The picture to crop.
            faces: That picture's `Face` rows, unfiltered.
            target: Square side length the crop is expanded to.
            preloaded_images: The task's path -> image cache; populated on a miss.

        Returns:
            ``(key, crop, file_path, is_centre_crop)``, or **None** when this
            picture cannot contribute a crop. `is_centre_crop` marks the faceless
            fallback, which is judged against the reduced whitelist.

        **Never raises**, and that is the point of it being a separate method.
        Everything here runs on data the caller does not control: `Face.bbox` is
        `json.loads` of a free-text column, so a row that is not ``[x1,y1,x2,y2]``
        raises `IndexError` in the `max()` key and `ValueError` in
        `expand_bbox_to_square`, and a bbox whose JSON is malformed raises on
        attribute access alone. Inline in the caller's loop, one such row escaped
        to the pass-level handler and cost **every remaining picture in the task**
        its quality crop - silently, because those pictures are still written as
        tagged, so no finder re-selects them.
        """
        file_path = ImageUtils.resolve_picture_path(self._db.image_root, pic.file_path)
        try:
            valid_faces = [
                face
                for face in faces
                if face.bbox and getattr(face, "face_index", 0) >= 0
            ]
            img = preloaded_images.get(file_path)
            if img is None:
                file_path, img, undecodable = self._load_pic(pic)
                if img is None:
                    if undecodable:
                        self._mark_unprocessable(pic, file_path)
                    return None
                preloaded_images[file_path] = img
            w, h = img.size
            if valid_faces:
                largest_face = max(
                    valid_faces,
                    key=lambda face: max(
                        0,
                        (float(face.bbox[2]) - float(face.bbox[0]))
                        * (float(face.bbox[3]) - float(face.bbox[1])),
                    ),
                )
                expanded = expand_bbox_to_square(largest_face.bbox, w, h, target)
                return (
                    f"{file_path}#face{largest_face.id}",
                    img.crop(expanded),
                    file_path,
                    False,
                )
            # No face detected: fall back to a centre crop so whole-image quality
            # defects (blockiness, blur, jpeg artifacts) still get a high-
            # resolution pass instead of relying only on the downscaled full-image
            # pass. A zero-size box at the image centre expands to the same
            # target-sized square the face path uses.
            centre_bbox = [w / 2.0, h / 2.0, w / 2.0, h / 2.0]
            expanded = expand_bbox_to_square(centre_bbox, w, h, target)
            return f"{file_path}#centre", img.crop(expanded), file_path, True
        except Exception as exc:
            logger.warning(
                "Could not build the quality crop for picture %s (%s): %s",
                getattr(pic, "id", None),
                file_path,
                exc,
            )
            return None

    def _mark_unprocessable(self, pic, file_path) -> None:
        """Record *pic* as undecodable so the finders stop re-selecting it (#585).

        Mirrors `thumbnail_generation_task` / `quality_task`: the registry is
        optional on the database object, and its absence degrades to a warning
        rather than a crash. Only ever called for a genuinely undecodable file -
        see `_load_pic`'s ``undecodable`` flag.
        """
        registry = getattr(self._db, "unprocessable_images", None)
        if registry is not None:
            registry.mark_unprocessable(
                getattr(pic, "id", None),
                str(file_path),
                reason="tag source could not be decoded",
            )
        else:
            logger.warning(
                "TagTask: failed to load source for picture %s (%s)",
                getattr(pic, "id", None),
                str(file_path),
            )

    def _preload_images(self) -> None:
        preloaded = {}

        def _load_one(pic) -> "tuple[str | None, PILImage.Image | None]":
            if self._preload_cancel.is_set():
                return None, None
            # The preload never marks: it is best-effort warming, and the batch
            # path re-loads and decides. Marking here would also race the cancel.
            file_path, img, _undecodable = self._load_pic(pic)
            if img is None:
                logger.debug(
                    "Preload failed for %s (%s)",
                    getattr(pic, "id", None),
                    str(file_path),
                )
            return file_path, img

        n_workers = min(self._PRELOAD_WORKERS, max(1, len(self._pictures)))
        with ThreadPoolExecutor(max_workers=n_workers) as pool:
            futures = {pool.submit(_load_one, pic): pic for pic in self._pictures}
            for future in as_completed(futures):
                if self._preload_cancel.is_set():
                    break
                file_path, img = future.result()
                if file_path is not None and img is not None:
                    preloaded[file_path] = img
        with self._preload_lock:
            self._preloaded_images = preloaded
        self._preload_finished_at = time.perf_counter()
        started_at = self._preload_started_at
        if started_at is not None:
            logger.debug(
                "[TAG_PRELOAD] task_id=%s status=ready preloaded=%s preload_s=%.3f",
                self.id,
                len(preloaded),
                self._preload_finished_at - started_at,
            )

    def _run_task(self):
        if not self._pictures:
            return {"changed_count": 0, "changed": []}

        changed = self._tag_pictures_batch()
        # Release preloaded PIL Images immediately after inference.  Without
        # this the images stay alive until the task object is garbage collected
        # (one full task cycle later), which can hold several hundred MB of RAM.
        self._preloaded_images = {}
        return {
            "changed_count": len(changed),
            "changed": changed,
        }

    def estimated_vram_mb(self) -> int:
        try:
            return max(
                0,
                self._tagging_workflow.estimated_incremental_vram_mb(
                    len(self._pictures)
                ),
            )
        except Exception as exc:
            logger.debug(
                "TagTask: VRAM estimate failed for %d picture(s); assuming 0: %s",
                len(self._pictures),
                exc,
            )
            return 0

    @property
    def priority(self) -> TaskPriority:
        # Interactive (user-triggered) tasks jump ahead of everything including
        # face extraction.  Background tagging takes priority over embeddings/descriptions.
        return TaskPriority.URGENT if self._interactive else TaskPriority.MEDIUM

    @property
    def queue_type(self) -> QueueType:
        return QueueType.GPU

    def allow_cpu_spillover(self) -> bool:
        return True

    def enable_cpu_spillover(self) -> None:
        self._cpu_spillover_enabled = True

    @classmethod
    def _acquire_cpu_spillover_engine(cls, image_root: str) -> InferenceEngine:
        with cls._cpu_spillover_lock:
            if cls._cpu_spillover_engine is None:
                cls._cpu_spillover_engine = InferenceEngine.create(
                    device="cpu",
                    image_root=image_root,
                )
            cls._cpu_spillover_last_used_at = time.perf_counter()
            return cls._cpu_spillover_engine

    @classmethod
    def release_idle_cpu_spillover_engine(cls, force: bool = False) -> None:
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
            logger.debug("CPU spillover engine close failed: %s", exc)

    @staticmethod
    def _add_tags_bulk(session: Session, updates: list[dict]):
        updated_ids = []
        candidate_ids = {
            int(update.get("pic_id"))
            for update in (updates or [])
            if update.get("pic_id") is not None
        }
        if not candidate_ids:
            return updated_ids

        existing_picture_ids = set(
            session.exec(
                select(Picture.id).where(Picture.id.in_(list(candidate_ids)))
            ).all()
        )

        # Bulk-fetch existing tags for all pictures in the batch at once.
        existing_tags_rows = session.exec(
            select(Tag.picture_id, Tag.tag).where(
                Tag.picture_id.in_(list(existing_picture_ids))
            )
        ).all()
        existing_tags_map: dict[int, set] = {}
        for row in existing_tags_rows:
            pid = row[0] if isinstance(row, tuple) else row.picture_id
            tag_val = row[1] if isinstance(row, tuple) else row.tag
            if tag_val is not None:
                existing_tags_map.setdefault(pid, set()).add(tag_val)

        # Human labels outrank the tagger in the ground-truth Tag table (mirrors the
        # prediction-status invariant / not_human_labeled): a tag the user confirmed
        # (POS) stays applied even when the fresh pass can't reproduce it - e.g. a
        # manually-added 'watermark' the model has no vocabulary for - and a tag the
        # user rejected (NEG) is never re-applied. Without this, re-tagging silently
        # drops a human-accepted tag that the model doesn't emit.
        human_pos_by_pic: dict[int, set[str]] = {}
        human_neg_by_pic: dict[int, set[str]] = {}
        human_rows = session.exec(
            select(
                TagPrediction.picture_id,
                TagPrediction.tag,
                TagPrediction.label_state,
            ).where(
                TagPrediction.picture_id.in_(list(existing_picture_ids)),
                TagPrediction.label_source == "human",
                TagPrediction.label_state.in_(["POS", "NEG"]),
            )
        ).all()
        for pid, tag_val, state in human_rows:
            if tag_val is None:
                continue
            target = human_pos_by_pic if state == "POS" else human_neg_by_pic
            target.setdefault(pid, set()).add(tag_val)

        # Determine which pictures need updating and their new effective tags.
        pics_to_update: list[tuple[int, set]] = []
        for update in updates:
            pic_id = update.get("pic_id")
            if pic_id is None:
                continue
            if pic_id not in existing_picture_ids:
                logger.debug("Skipping tag update for missing picture_id=%s", pic_id)
                continue
            tags = update.get("tags") or []

            # When the tagger found no applicable tags, leave the tag set empty.
            # An empty tag set means "processed but no tags found"; MissingTagFinder
            # only re-queues pictures that still carry a retag sentinel, so a
            # picture with no tags at all will not be reprocessed.
            effective_tags = set(tags) if tags else set()
            # Apply durable human supervision on top of the model's call.
            effective_tags |= human_pos_by_pic.get(pic_id, set())
            effective_tags -= human_neg_by_pic.get(pic_id, set())

            if effective_tags == existing_tags_map.get(pic_id, set()):
                continue

            pics_to_update.append((pic_id, effective_tags))

        # A locked set freezes a picture's CONFIRMED tags (the Tag table, not just
        # predictions), so the background tagger must never rewrite them - even if
        # a retag sentinel somehow landed on a locked picture (e.g. a reset that
        # slipped through). Skip locked pictures from the confirmed-Tag rewrite;
        # MissingTagFinder also excludes them so they are not re-queued.
        if pics_to_update:
            locked = locked_picture_ids(session, [pid for pid, _ in pics_to_update])
            if locked:
                logger.info(
                    "Tagger: preserving frozen confirmed tags - skipping "
                    "rewrite for %d locked picture(s) %s",
                    len(locked),
                    sorted(locked),
                )
                pics_to_update = [
                    (pid, t) for pid, t in pics_to_update if pid not in locked
                ]

        if not pics_to_update:
            return updated_ids

        # Bulk delete old tags and insert new ones in a single transaction.
        #
        # The rewrite is wrapped in invalidate_on_anomaly_change because an applied Tag
        # row is now an *input* to the scorer's anomaly penalty
        # (fetch_anomaly_confidences charges a model prediction only when the defect is
        # visible in the tag list). This method commits its tag write in its own DB task,
        # before _write_predictions_from_tags runs and takes its snapshot, so without a
        # guard here a re-tag that adds or drops an anomaly tag would move the score with
        # nothing observing it and the cached value would stay stale. update_pic_ids is
        # already lock-filtered above, so frozen pictures are never invalidated.
        update_pic_ids = [pid for pid, _ in pics_to_update]
        try:
            with invalidate_on_anomaly_change(
                session, update_pic_ids, context="tagger tag rewrite"
            ):
                session.exec(delete(Tag).where(Tag.picture_id.in_(update_pic_ids)))
                for pic_id, effective_tags in pics_to_update:
                    for tag_value in effective_tags:
                        session.add(Tag(picture_id=pic_id, tag=tag_value))
            session.commit()
            updated_ids.extend(update_pic_ids)
        except IntegrityError as exc:
            session.rollback()
            logger.warning(
                "Bulk tag write failed for %d pictures, falling back to per-picture: %s",
                len(update_pic_ids),
                exc,
            )
            for pic_id, effective_tags in pics_to_update:
                try:
                    with invalidate_on_anomaly_change(
                        session, [pic_id], context="tagger tag rewrite (per-picture)"
                    ):
                        session.exec(delete(Tag).where(Tag.picture_id == pic_id))
                        for tag_value in effective_tags:
                            session.add(Tag(picture_id=pic_id, tag=tag_value))
                    session.commit()
                    updated_ids.append(pic_id)
                except IntegrityError as inner_exc:
                    session.rollback()
                    logger.warning(
                        "Skipping tag update for picture_id=%s due to concurrent delete or FK constraint: %s",
                        pic_id,
                        inner_exc,
                    )

        return updated_ids

    @staticmethod
    def _fetch_faces_for_pictures(session: Session, picture_ids: list) -> dict:
        faces = session.exec(select(Face).where(Face.picture_id.in_(picture_ids))).all()
        result = {}
        for face in faces:
            result.setdefault(face.picture_id, []).append(face)
        return result

    @staticmethod
    def _resolve_pending_predictions(session: Session, picture_ids: list) -> None:
        """Flip any PENDING tag predictions to CONFIRMED or REJECTED based on
        the tags that TagTask wrote for these pictures."""
        if not picture_ids:
            return
        for picture_id in picture_ids:
            applied_tags = {
                row[0] if isinstance(row, tuple) else row
                for row in session.exec(
                    select(Tag.tag).where(
                        Tag.picture_id == picture_id,
                        Tag.tag.is_not(None),
                        ~Tag.tag.like(
                            TAG_SENTINEL_LIKE_PATTERN, escape=TAG_SENTINEL_ESCAPE_CHAR
                        ),
                    )
                ).all()
            }
            all_preds = session.exec(
                select(TagPrediction).where(
                    TagPrediction.picture_id == picture_id,
                    TagPrediction.status.in_(["PENDING", "CONFIRMED", "REJECTED"]),
                )
            ).all()
            for pred in all_preds:
                # Never auto-flip a human-labeled row: its POS/NEG decision is
                # durable supervision whose status was set by record_human_label
                # (mirrors not_human_labeled() / the delete-guard in
                # _write_predictions_from_tags). Without this, re-tagging a
                # picture pushes an accepted tag into the rejected pile when the
                # fresh tagger pass doesn't re-apply it.
                if pred.label_source == "human":
                    continue
                correct_status = "CONFIRMED" if pred.tag in applied_tags else "REJECTED"
                if pred.status != correct_status:
                    pred.status = correct_status
        session.commit()

    def _tag_pictures_batch(self) -> list:
        assert self._pictures is not None

        if self._preload_thread is None:
            self.on_queued()

        task_start_at = time.perf_counter()
        preload_started_at = self._preload_started_at
        preload_headstart_s = (
            max(0.0, task_start_at - preload_started_at)
            if preload_started_at is not None
            else 0.0
        )

        preload_wait_start = time.perf_counter()
        if self._preload_thread is not None:
            self._preload_thread.join()
        preload_wait_s = time.perf_counter() - preload_wait_start

        preload_finished_at = self._preload_finished_at
        preload_remaining_at_start_s = (
            max(0.0, preload_finished_at - task_start_at)
            if preload_finished_at is not None
            else preload_wait_s
        )

        with self._preload_lock:
            preloaded_images = dict(self._preloaded_images)

        logger.debug(
            "[TAG_PRELOAD] task_id=%s headstart_s=%.3f wait_block_s=%.3f "
            "remaining_at_start_s=%.3f preloaded=%s",
            self.id,
            preload_headstart_s,
            preload_wait_s,
            preload_remaining_at_start_s,
            len(preloaded_images),
        )

        batch = self._pictures
        image_paths = []
        pic_by_path = {}
        for pic in batch:
            file_path = ImageUtils.resolve_picture_path(
                self._db.image_root, pic.file_path
            )
            image_paths.append(file_path)
            pic_by_path[file_path] = pic

        tagged_pictures = []
        self.release_idle_cpu_spillover_engine(force=False)
        active_workflow: TaggingWorkflow = self._tagging_workflow
        cpu_spillover_engine = None
        if self._cpu_spillover_enabled:
            logger.debug("TagTask %s using CPU spillover mode", self.id)
            cpu_spillover_engine = self._acquire_cpu_spillover_engine(
                self._db.image_root
            )
            active_workflow = cpu_spillover_engine.tagging_workflow

        try:
            if image_paths:
                logger.debug("Tagging %s images", len(image_paths))
                logger.debug("Tagging image paths: %s", image_paths)
                # Collect raw confidence scores in the same GPU pass as tagging.
                # Whatever the active tagger is: a plugin that reports
                # confidences gets prediction rows too, fenced out of the
                # anomaly score by ``feeds_anomaly_score`` because raw
                # confidences are not comparable between models.
                full_scores_by_path: dict = {}
                use_pixlstash_tagger = active_workflow.is_pixlstash_tagger_enabled
                inference_start = time.perf_counter()
                tag_results = active_workflow.tag_images(
                    image_paths,
                    preloaded_images=preloaded_images,
                    out_raw_scores=full_scores_by_path,
                    engine_override=self._engine_override,
                )
                inference_s = time.perf_counter() - inference_start
                logger.debug("Got tag results for %s images.", len(tag_results))

                # --- Quality crop pass ---
                # Fetch face bboxes and run the custom tagger on expanded crops so
                # that quality tags (e.g. "blocky") that are invisible at full-
                # image resolution can still be detected.
                crop_inference_s = 0.0
                crop_fetch_s = 0.0
                crop_build_s = 0.0
                try:
                    crop_fetch_start = time.perf_counter()
                    pic_ids = [p.id for p in batch]
                    faces_by_pic = self._db.run_immediate_read_task(
                        lambda session: self._fetch_faces_for_pictures(
                            session, pic_ids
                        ),
                    )
                    crop_fetch_s = time.perf_counter() - crop_fetch_start
                    target = active_workflow.pixlstash_tagger_image_size_quality_crop()
                    quality_items = []
                    key_to_path = {}
                    # Paths whose crop is the faceless centre-crop fallback; these are
                    # judged against the reduced CENTRE_CROP_TAG_WHITELIST (no face tags).
                    centre_crop_paths: set = set()
                    # CPU work, and it sits between the two GPU timers: PIL
                    # crops and resizes, plus a decode for any picture the
                    # preload missed.
                    crop_build_start = time.perf_counter()
                    for pic in batch:
                        built = self._build_quality_crop(
                            pic,
                            faces_by_pic.get(pic.id, []),
                            target,
                            preloaded_images,
                        )
                        if built is None:
                            continue
                        key, crop, file_path, is_centre_crop = built
                        quality_items.append((key, crop))
                        key_to_path[key] = file_path
                        if is_centre_crop:
                            centre_crop_paths.add(file_path)
                    crop_build_s = time.perf_counter() - crop_build_start
                    if quality_items:
                        # Single GPU pass: get quality tags AND raw scores for predictions.
                        crop_raw_scores: dict = {}
                        crop_inf_start = time.perf_counter()
                        quality_results = active_workflow.tag_quality_crops(
                            quality_items,
                            out_raw_scores=crop_raw_scores
                            if use_pixlstash_tagger
                            else None,
                        )
                        crop_inference_s = time.perf_counter() - crop_inf_start
                        # The crop's authoritative tag set depends on its type: a face
                        # crop owns the full whitelist; the faceless centre-crop fallback
                        # owns only the non-face quality tags.
                        whitelist_by_path = {
                            path: (
                                CENTRE_CROP_TAG_WHITELIST
                                if path in centre_crop_paths
                                else QUALITY_CROP_TAG_WHITELIST
                            )
                            for path in key_to_path.values()
                        }
                        # Accumulate quality tags found across all crops per picture path,
                        # keeping only those the crop is allowed to own.
                        quality_tags_by_path = {}
                        for key, quality_tags in quality_results.items():
                            path = key_to_path.get(key)
                            if path:
                                allowed = whitelist_by_path[path]
                                quality_tags_by_path.setdefault(path, set()).update(
                                    t for t in quality_tags if t in allowed
                                )
                        # Crops are ground truth for the tags they own: strip those tags
                        # if the full-image pass produced them, then add only what the
                        # crop confirmed.  Applies to every picture that produced a crop -
                        # the largest face when one was found, otherwise the centre-crop
                        # fallback (which leaves face tags from the full-image pass alone).
                        for path, crop_quality in quality_tags_by_path.items():
                            if path not in tag_results:
                                continue
                            allowed = whitelist_by_path[path]
                            stripped = [
                                t for t in tag_results[path] if t not in allowed
                            ]
                            tag_results[path] = stripped + list(crop_quality)
                            if crop_quality:
                                logger.debug(
                                    "Quality crop tags for %s: %s", path, crop_quality
                                )
                        # Boost prediction scores using crop confidence, limited to the
                        # tags the crop is allowed to own (centre crops don't boost face
                        # tags).
                        if full_scores_by_path and crop_raw_scores:
                            for key, tag_scores in crop_raw_scores.items():
                                path = key_to_path.get(key)
                                if path is None:
                                    continue
                                allowed = whitelist_by_path[path]
                                merged = full_scores_by_path.setdefault(path, {})
                                for tag, conf in tag_scores.items():
                                    if tag not in allowed:
                                        continue
                                    if conf > merged.get(tag, 0.0):
                                        merged[tag] = conf
                except Exception as exc:
                    logger.warning("Quality crop pass failed: %s", exc)
                # --- end quality crop pass ---

                update_payloads = []
                for path, tags in tag_results.items():
                    pic = pic_by_path.get(path)
                    if not pic:
                        continue
                    logger.debug(
                        "Processing tags for image at path: %s: %s", path, tags
                    )
                    update_payloads.append(
                        {
                            "pic_id": pic.id,
                            "tags": tags or [],
                        }
                    )

                if update_payloads:
                    db_tags_start = time.perf_counter()
                    updated_ids = self._db.run_task(
                        self._add_tags_bulk,
                        update_payloads,
                        priority=DBPriority.LOW,
                    )
                    db_tags_s = time.perf_counter() - db_tags_start
                    updated_set = set(updated_ids or [])
                    for update in update_payloads:
                        pic_id = update.get("pic_id")
                        if pic_id in updated_set:
                            tagged_pictures.append(
                                (Picture, pic_id, "tags", update.get("tags") or [])
                            )

                    # Flip any PENDING predictions to CONFIRMED/REJECTED now that
                    # TagTask has made its decision for all processed pictures.
                    all_pic_ids = [u["pic_id"] for u in update_payloads]
                    db_resolve_start = time.perf_counter()
                    self._db.run_task(
                        self._resolve_pending_predictions,
                        all_pic_ids,
                        priority=DBPriority.LOW,
                    )
                    db_resolve_s = time.perf_counter() - db_resolve_start

                    # Write TagPrediction rows for this batch alongside the tags.
                    db_predictions_s = 0.0
                    if full_scores_by_path:
                        label_scores_by_pic_id: dict = {}
                        for path, scores in full_scores_by_path.items():
                            pic = pic_by_path.get(path)
                            if pic is not None and scores:
                                label_scores_by_pic_id[pic.id] = scores
                        if label_scores_by_pic_id:
                            tags_by_pic_id = {
                                u["pic_id"]: set(u.get("tags") or [])
                                for u in update_payloads
                            }
                            model_version = active_workflow.active_model_version(
                                self._engine_override
                            )
                            db_pred_start = time.perf_counter()
                            self._db.run_task(
                                self._write_predictions_from_tags,
                                label_scores_by_pic_id,
                                tags_by_pic_id,
                                model_version,
                                priority=DBPriority.LOW,
                            )
                            db_predictions_s = time.perf_counter() - db_pred_start

                    n = len(update_payloads)
                    total_s = time.perf_counter() - task_start_at
                    gpu_s = inference_s + crop_inference_s
                    gpu_throughput = n / gpu_s if gpu_s > 0 else 0.0
                    wall_throughput = n / total_s if total_s > 0 else 0.0
                    device = getattr(active_workflow, "_engine", None)
                    device = (
                        getattr(device, "device", "unknown")
                        if device is not None
                        else "unknown"
                    )
                    # The full-image pass runs exactly one plugin, so
                    # `inference_s` is all WD14 or all PixlStash tagger; the
                    # crop pass is always the PixlStash tagger. Split so the
                    # two models and the CPU crop build are separate numbers.
                    full_pass = active_workflow.active_plugin_name(
                        self._engine_override
                    )
                    wd14_s = inference_s if full_pass == "wd14" else 0.0
                    pixlstash_tagger_s = (
                        inference_s if full_pass == "pixlstash_tagger" else 0.0
                    ) + crop_inference_s
                    logger.info(
                        "[TAG_TIMING] task_id=%s n=%d device=%s "
                        "preload_wait_s=%.3f full_pass=%s inference_s=%.3f "
                        "wd14_s=%.3f pixlstash_tagger_s=%.3f "
                        "crop_fetch_s=%.3f crop_build_s=%.3f crop_inference_s=%.3f "
                        "db_tags_s=%.3f db_resolve_s=%.3f db_pred_s=%.3f "
                        "total_s=%.3f gpu_throughput=%.1f/s wall_throughput=%.1f/s",
                        self.id,
                        n,
                        device,
                        preload_wait_s,
                        full_pass,
                        inference_s,
                        wd14_s,
                        pixlstash_tagger_s,
                        crop_fetch_s,
                        crop_build_s,
                        crop_inference_s,
                        db_tags_s,
                        db_resolve_s,
                        db_predictions_s,
                        total_s,
                        gpu_throughput,
                        wall_throughput,
                    )
                    if device == "cpu" and inference_s > 10:
                        logger.warning(
                            "[TAG_TIMING] Inference ran on CPU and took %.1fs for %d image(s). "
                            "Set default_device=cuda in server-config.json to use the GPU.",
                            inference_s,
                            n,
                        )
        finally:
            if cpu_spillover_engine is not None:
                with self._cpu_spillover_lock:
                    self._cpu_spillover_last_used_at = time.perf_counter()
                self.release_idle_cpu_spillover_engine(force=False)

        return tagged_pictures

    @staticmethod
    def _write_predictions_from_tags(
        session: Session,
        label_scores_by_pic_id: dict,
        tags_by_pic_id: dict,
        model_version: str,
    ) -> int:
        """Persist raw confidence scores to TagPrediction alongside tag writes.

        Called from _tag_pictures_batch so CONFIRMED/REJECTED status is resolved
        immediately, without needing a separate TagPredictionTask pass.

        Uses bulk queries to avoid per-row SELECTs across the batch.

        Args:
            session: Database session.
            label_scores_by_pic_id: Mapping of picture_id to {tag: confidence}.
            tags_by_pic_id: Mapping of picture_id to the set of applied tag strings.
            model_version: Custom tagger model version string (e.g. "v42").

        Returns:
            Number of TagPrediction rows written or updated.
        """
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        picture_ids = [pid for pid, scores in label_scores_by_pic_id.items() if scores]
        if not picture_ids:
            return 0

        # Filter to pictures that still exist - a reference folder removal can
        # delete pictures while a tag task is already in flight, causing FK
        # violations when TagPrediction rows are flushed for a gone picture.
        existing_picture_ids: set[int] = set(
            session.exec(select(Picture.id).where(Picture.id.in_(picture_ids))).all()
        )
        picture_ids = [pid for pid in picture_ids if pid in existing_picture_ids]
        if not picture_ids:
            return 0
        label_scores_by_pic_id = {
            pid: scores
            for pid, scores in label_scores_by_pic_id.items()
            if pid in existing_picture_ids
        }
        tags_by_pic_id = {
            pid: tags
            for pid, tags in tags_by_pic_id.items()
            if pid in existing_picture_ids
        }

        # Snapshot the scorer's anomaly inputs before any prediction row is touched:
        # a re-tag that moves an anomaly confidence (or drops a stale-version row)
        # changes the smart score, whose cached value would otherwise never refresh.
        # Taken before the stale-row delete below so that delete is covered too.
        before_anomaly = anomaly_state_signature(session, picture_ids)

        # --- Single bulk fetch of all existing TagPrediction rows for the batch ---
        existing_rows = session.exec(
            select(TagPrediction).where(TagPrediction.picture_id.in_(picture_ids))
        ).all()
        existing_map: dict[tuple[int, str], TagPrediction] = {
            (row.picture_id, row.tag): row for row in existing_rows
        }

        # --- Bulk delete stale model-version rows ---
        # Never delete a human-labeled row: its label_state/label_source is durable
        # supervision the tagger must not clobber (mirrors not_human_labeled()).
        # Never delete across the built-in/plugin split either: a picture holds one
        # row per tag, so a plugin run would otherwise clear the built-in tagger's
        # confidences and silently zero the picture's anomaly_tag_uncertainty.
        writing_as_plugin = is_plugin_model_version(model_version)
        stale_rows = [
            row
            for row in existing_rows
            if row.model_version != model_version
            and row.model_version != "manual"
            and row.label_source != "human"
            and is_plugin_model_version(row.model_version) == writing_as_plugin
        ]
        if stale_rows:
            session.exec(
                delete(TagPrediction).where(
                    TagPrediction.id.in_([row.id for row in stale_rows])
                )
            )
            # Remove from map so they are not treated as existing below.
            for row in stale_rows:
                existing_map.pop((row.picture_id, row.tag), None)

        # --- Bulk fetch applied tags for anomaly uncertainty computation ---
        tag_rows = session.exec(
            select(Tag.picture_id, Tag.tag).where(
                Tag.picture_id.in_(picture_ids),
                Tag.tag.is_not(None),
                ~Tag.tag.like(
                    TAG_SENTINEL_LIKE_PATTERN, escape=TAG_SENTINEL_ESCAPE_CHAR
                ),
            )
        ).all()
        applied_tags_by_pic: dict[int, set[str]] = {}
        for pid, tag in tag_rows:
            applied_tags_by_pic.setdefault(pid, set()).add(tag)

        written = 0
        for picture_id, label_scores in label_scores_by_pic_id.items():
            if not label_scores:
                continue
            applied_tags = tags_by_pic_id.get(picture_id, set())

            for tag, confidence in label_scores.items():
                status = "CONFIRMED" if tag in applied_tags else "REJECTED"
                existing = existing_map.get((picture_id, tag))
                if (
                    existing is not None
                    and existing.model_version != "manual"
                    and is_plugin_model_version(existing.model_version)
                    != writing_as_plugin
                ):
                    # A row from the other population (built-in vs plugin) owns
                    # this tag, and the unique key allows only one.  Leave it:
                    # overwriting would swap a confidence the anomaly score is
                    # calibrated against for one that is not comparable to it.
                    # ponytail: whoever got there first wins; widen the unique
                    # key to (picture_id, tag, model_version) if predictions
                    # from several taggers ever need to coexist per tag.
                    continue
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
                else:
                    row_changed = False
                    # confidence/model_version are live on every row, including
                    # human-labeled ones (see TagPrediction's class docstring:
                    # only label_model_version/label_confidence in the human
                    # ledger are frozen, not these).
                    if (
                        existing.confidence != confidence
                        or existing.model_version != model_version
                    ):
                        existing.confidence = confidence
                        existing.model_version = model_version
                        row_changed = True
                    # Never clobber a human-labeled row's status: it's durable
                    # POS/NEG supervision (mirrors the delete-guard above and
                    # not_human_labeled()). Auto-flipping it here is what dropped
                    # an accepted tag into the rejected pile on re-tag.
                    if existing.label_source != "human" and existing.status != status:
                        existing.status = status
                        row_changed = True
                    if row_changed:
                        existing.predicted_at = now
                        written += 1

            # Ensure every applied tag has a prediction row even if the model
            # didn't score it (confidence=0.0 so the UI can still show a tooltip
            # for manually-added or low-scoring tags).
            label_score_tags = set(label_scores.keys())
            for tag in applied_tags:
                if tag in label_score_tags:
                    continue
                existing = existing_map.get((picture_id, tag))
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

            # Compute tag_uncertainty from model confidences.
            confs = list(label_scores.values())
            uncertainty = float(max(min(c, 1.0 - c) for c in confs)) if confs else 0.0
            pic = session.get(Picture, picture_id)
            if pic is not None:
                pic.tag_uncertainty = uncertainty

            # Compute anomaly_tag_uncertainty using the already-fetched applied tags
            # (avoids a redundant SELECT per picture inside recompute_anomaly_tag_uncertainty).
            # Only the built-in tagger's confidences may: they are what the anomaly
            # score is calibrated against, and a plugin's 0.4 does not mean the same
            # thing.  Leave the stored value alone rather than zeroing it, so a run
            # under a plugin does not discard the built-in tagger's assessment.
            if not feeds_anomaly_score(model_version):
                continue
            pic_applied = applied_tags_by_pic.get(picture_id, set())
            anomaly_scores: list[float] = []
            for tag, confidence in label_scores.items():
                if tag is None or tag.strip().lower() not in PENALISED_TAG_SET:
                    continue
                if tag in pic_applied:
                    anomaly_scores.append(1.0 - float(confidence))
                else:
                    anomaly_scores.append(float(confidence))
            if pic is not None:
                pic.anomaly_tag_uncertainty = (
                    max(anomaly_scores) if anomaly_scores else 0.0
                )

        # One bulk UPDATE for the whole batch - a write per picture here would
        # saturate the single DB writer queue on a large re-tag.
        invalidate_changed_anomaly_scores(
            session, picture_ids, before_anomaly, context="tagger prediction rewrite"
        )
        session.commit()
        return written

    @staticmethod
    def count_missing_tags(session: Session) -> int:
        """Count pictures with a pending-retag sentinel (awaiting tagging)."""
        has_sentinel = Tag.tag.like(
            TAG_SENTINEL_LIKE_PATTERN, escape=TAG_SENTINEL_ESCAPE_CHAR
        )
        result = session.exec(
            select(func.count())
            .select_from(Picture)
            .where(Picture.tags.any(has_sentinel))
            .where(Picture.deleted.is_(False))
            .where(Picture.file_path.is_not(None))
        ).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0
