import concurrent
import ctypes
import platform

import datetime
import os
import time
import threading
import numpy as np

from typing import Optional
from concurrent.futures import Future

from sqlmodel import Session, select
from sqlalchemy import func


from .database import DBPriority, VaultDatabase
from .db_models import (
    MetaData,
    Character,
    Face,
    Picture,
    PictureSet,
    Tag,
    TAG_EMPTY_SENTINEL,
)
from .db_models.tag_prediction import TagPrediction
from .pixl_logging import get_logger
from .picture_tagger import PictureTagger
from .utils.image_processing.image_utils import ImageUtils
from .tasks.face_quality_task import FaceQualityTask
from .tasks.face_extraction_task import FaceExtractionTask
from .tasks.image_embedding_task import ImageEmbeddingTask
from .tasks.likeness_task import LikenessTask
from .tasks.quality_task import QualityTask
from .tasks.smart_score_task import SmartScoreTask
from .utils.likeness.likeness_parameter_utils import LikenessParameterUtils
from .tasks.base_task import TaskStatus
from .task_runner import TaskRunner
from .work_planner import WorkPlanner
from .tasks import TaskType
from .utils.reference_folder_watcher import ReferenceFolderWatcher

from pixlstash.event_types import EventType


logger = get_logger(__name__)


class Vault:
    AGGRESSIVE_UNLOAD_INTERVAL = 180

    def __enter__(self):
        # Allow use as a context manager for robust cleanup
        return self

    def __exit__(self, _, __, ___):
        self.close()

    """
    Represents a vault for storing images and metadata.

    The vault contains a database that manages a SQLite database and stores the vault description in the metadata table.
    """

    def __init__(
        self,
        image_root: str,
        description: Optional[str] = None,
        server_config_path: Optional[str] = None,
        path_mapper=None,
    ):
        """
        Initialize a Vault instance.

        Args:
            db_path (str): Path to the SQLite database file.
            image_root (Optional[str]): Path to the image root directory.
            description (Optional[str]): Description of the vault.
        """
        self.image_root = image_root
        logger.debug(f"Image root: {self.image_root}")
        assert self.image_root is not None, "image_root cannot be None"
        logger.debug(f"Using image_root: {self.image_root}")
        os.makedirs(self.image_root, exist_ok=True)
        assert os.path.exists(self.image_root), (
            f"Image root path does not exist: {self.image_root}"
        )

        self._db_path = os.path.join(self.image_root, "vault.db")
        self.db = VaultDatabase(self._db_path)
        self.set_description(description or "")

        self._picture_tagger = None
        self._last_aggressive_unload_at = 0.0
        self._keep_models_in_memory = True
        self._max_vram_gb = None
        self._wd14_tagger_enabled = False
        self._custom_tagger_enabled = True
        self._wd14_threshold = None
        self._custom_tagger_threshold_offset = None
        self._server_config_path = server_config_path

        self._planner_watchers = {}
        self._planner_watchers_lock = threading.Lock()
        self._changed_tags_notify_lock = threading.Lock()
        self._changed_tags_pending_ids: set[int] = set()
        self._changed_tags_flush_timer: threading.Timer | None = None
        self._changed_tags_flush_delay_s = 2.0
        self._event_listeners = []
        self._event_listeners_lock = threading.Lock()
        self._path_mapper = path_mapper
        self._task_runner = TaskRunner(name="vault-task-runner", num_workers=4)
        self._planner_work_finders = WorkPlanner.work_finders(
            database=self.db,
            picture_tagger_getter=lambda: self._picture_tagger,
            image_root=self.image_root,
            path_mapper=path_mapper,
        )
        self._work_planner = WorkPlanner(
            task_runner=self._task_runner,
            task_finders=list(self._planner_work_finders.values()),
        )
        self._closed = False

        self._task_runner.add_task_complete_callback(self._on_task_completed)
        self._task_runner.add_task_complete_callback(
            self._work_planner.on_task_complete
        )
        self._task_runner.start()
        self._work_planner.start()

        self._ref_folder_watcher = ReferenceFolderWatcher(
            on_folder_changed=self._on_reference_folder_fs_changed,
        )
        self._ref_folder_watcher.start()
        self._start_existing_folder_watches()

    def ensure_ready(self):
        """Initialise the picture tagger so the planner can process work immediately.

        Call this at server startup. Tests that do not need the tagger can skip it;
        tagger init is also triggered lazily by get_worker_future().
        """
        if not self._picture_tagger:
            self._picture_tagger = PictureTagger(image_root=self.image_root)
            self._picture_tagger.set_keep_models_in_memory(self._keep_models_in_memory)
            self._picture_tagger.set_max_vram_usage_gb(self._max_vram_gb)
            self._picture_tagger.set_wd14_tagger_enabled(self._wd14_tagger_enabled)
            self._picture_tagger.set_custom_tagger_enabled(self._custom_tagger_enabled)
            if self._wd14_threshold is not None:
                self._picture_tagger.set_wd14_threshold(self._wd14_threshold)
            if self._custom_tagger_threshold_offset is not None:
                self._picture_tagger.set_custom_tagger_threshold_offset(
                    self._custom_tagger_threshold_offset
                )

    def notify(self, event_type: EventType, data=None):
        """
        Notify all relevant workers for a given event type.

        Example:
            vault.notify(Vault.VaultEventType.NEW_PICTURE)
        """
        if self._work_planner and self._work_planner.is_running():
            self._work_planner.wake()
        with self._event_listeners_lock:
            listeners = list(self._event_listeners)
        for listener in listeners:
            try:
                listener(event_type, data)
            except Exception as exc:
                logger.warning("Event listener failed for %s: %s", event_type, exc)

    def wake(self) -> None:
        """Wake the work planner without emitting an event.

        This preserves compatibility with older call sites that used
        ``vault.wake()`` to resume background work after DB mutations.
        """
        if self._work_planner and self._work_planner.is_running():
            self._work_planner.wake()

    def add_event_listener(self, listener):
        """Register a callback to be invoked when vault events occur."""
        if not callable(listener):
            raise ValueError("listener must be callable")
        with self._event_listeners_lock:
            if listener not in self._event_listeners:
                self._event_listeners.append(listener)

    # -------------------------------------------------------------------------
    # Reference folder filesystem watching
    # -------------------------------------------------------------------------

    def _start_existing_folder_watches(self) -> None:
        """Start filesystem watches for all reference folders already in the DB."""
        from pixlstash.db_models.reference_folder import ReferenceFolder
        from pixlstash.utils.path_mapper import PathMapper

        effective_mapper = (
            self._path_mapper if self._path_mapper is not None else PathMapper()
        )

        def fetch(session: Session) -> list[ReferenceFolder]:
            return list(session.exec(select(ReferenceFolder)).all())

        try:
            folders = self.db.run_task(fetch, priority=DBPriority.IMMEDIATE)
        except Exception as exc:
            logger.warning("Could not fetch reference folders for FS watching: %s", exc)
            return

        for rf in folders:
            try:
                resolved = effective_mapper.resolve(rf.folder)
                self._ref_folder_watcher.watch_folder(rf.id, resolved)
            except Exception as exc:
                logger.warning(
                    "Could not start FS watch for reference folder %d (%s): %s",
                    rf.id,
                    rf.folder,
                    exc,
                )

    def watch_reference_folder(self, folder_id: int, folder_path: str) -> None:
        """Start watching *folder_path* for reference folder *folder_id*.

        Should be called after a new reference folder is created so that
        filesystem changes are picked up immediately.

        Args:
            folder_id: Primary key of the reference folder.
            folder_path: Stored (host) path of the folder.
        """
        from pixlstash.utils.path_mapper import PathMapper

        effective_mapper = (
            self._path_mapper if self._path_mapper is not None else PathMapper()
        )
        try:
            resolved = effective_mapper.resolve(folder_path)
            self._ref_folder_watcher.watch_folder(folder_id, resolved)
        except Exception as exc:
            logger.warning(
                "Could not start FS watch for new reference folder %d (%s): %s",
                folder_id,
                folder_path,
                exc,
            )

    def unwatch_reference_folder(self, folder_id: int) -> None:
        """Stop watching the directory for *folder_id*.

        Should be called after a reference folder is deleted.

        Args:
            folder_id: Primary key of the reference folder to stop watching.
        """
        self._ref_folder_watcher.unwatch_folder(folder_id)

    def _on_reference_folder_fs_changed(self, folder_id: int) -> None:
        """Callback invoked by the filesystem watcher when a relevant file changes.

        Resets ``last_scanned`` to zero so the
        :class:`~pixlstash.tasks.reference_folder_scan_finder.ReferenceFolderScanFinder`
        schedules an immediate rescan, then wakes the work-planner thread.
        """
        from pixlstash.db_models.reference_folder import ReferenceFolder

        def reset_last_scanned(session: Session, fid: int) -> None:
            rf = session.get(ReferenceFolder, fid)
            if rf is not None:
                rf.last_scanned = 0.0
                session.commit()

        try:
            self.db.run_task(
                reset_last_scanned, folder_id, priority=DBPriority.IMMEDIATE
            )
        except Exception as exc:
            logger.warning(
                "Failed to reset last_scanned for reference folder %d: %s",
                folder_id,
                exc,
            )
        if self._work_planner and self._work_planner.is_running():
            self._work_planner.wake()

    def __repr__(self):
        """
        Return a string representation of the Vault instance.

        Returns:
            str: String representation.
        """
        return f"Vault(db_path='{self._db_path}')"

    def close(self):
        """
        Cleanly close the vault, including stopping background workers and closing DB connection.
        """
        if self._closed:
            return
        self._closed = True
        with self._changed_tags_notify_lock:
            timer = self._changed_tags_flush_timer
            self._changed_tags_flush_timer = None
            self._changed_tags_pending_ids.clear()
        if timer is not None:
            timer.cancel()
        self._ref_folder_watcher.stop()
        self._work_planner.stop()
        self._task_runner.stop()
        FaceExtractionTask.release_detection_models()
        ImageEmbeddingTask.release_models()
        if self._picture_tagger:
            self._picture_tagger.close()
            del self._picture_tagger
            self._picture_tagger = None
        if self.db:
            self.db.close()
            del self.db
            self.db = None

    def set_keep_models_in_memory(self, keep_models_in_memory: bool):
        previous = self._keep_models_in_memory
        self._keep_models_in_memory = bool(keep_models_in_memory)

        if self._picture_tagger and hasattr(
            self._picture_tagger, "set_keep_models_in_memory"
        ):
            self._picture_tagger.set_keep_models_in_memory(self._keep_models_in_memory)

        if previous and not self._keep_models_in_memory:
            logger.info(
                "keep_models_in_memory disabled; attempting immediate model unload."
            )
            self._last_aggressive_unload_at = 0.0
            progress = self._build_worker_progress_snapshot()
            self._maybe_aggressive_unload(progress)

    def set_max_vram_usage_gb(self, max_vram_gb: Optional[float]):
        self._max_vram_gb = max_vram_gb
        self._task_runner.set_max_vram_usage_gb(max_vram_gb)
        if self._picture_tagger and hasattr(
            self._picture_tagger, "set_max_vram_usage_gb"
        ):
            self._picture_tagger.set_max_vram_usage_gb(max_vram_gb)

    def set_wd14_tagger_enabled(self, enabled: bool):
        self._wd14_tagger_enabled = bool(enabled)
        if self._picture_tagger and hasattr(
            self._picture_tagger, "set_wd14_tagger_enabled"
        ):
            self._picture_tagger.set_wd14_tagger_enabled(self._wd14_tagger_enabled)

    def set_custom_tagger_enabled(self, enabled: bool):
        self._custom_tagger_enabled = bool(enabled)
        if self._picture_tagger and hasattr(
            self._picture_tagger, "set_custom_tagger_enabled"
        ):
            self._picture_tagger.set_custom_tagger_enabled(self._custom_tagger_enabled)

    def set_wd14_threshold(self, threshold: Optional[float]):
        self._wd14_threshold = threshold
        if self._picture_tagger and hasattr(self._picture_tagger, "set_wd14_threshold"):
            value = threshold if threshold is not None else 0.85
            self._picture_tagger.set_wd14_threshold(value)

    def set_custom_tagger_threshold_offset(self, offset: Optional[float]):
        self._custom_tagger_threshold_offset = offset
        if self._picture_tagger and hasattr(
            self._picture_tagger, "set_custom_tagger_threshold_offset"
        ):
            value = offset if offset is not None else 0.0
            self._picture_tagger.set_custom_tagger_threshold_offset(value)

    def generate_text_embedding(self, query: str) -> Optional[np.ndarray]:
        """
        Generate a text embedding using PictureTagger.

        Args:
            text (str): Input text to generate embedding for.

        Returns:
            Optional[np.ndarray]: Generated text embedding or None if failed.
        """
        embedding = self._picture_tagger.generate_text_embedding(query=query)
        return embedding[0] if embedding is not None and len(embedding) > 0 else None

    def generate_clip_text_embedding(self, query: str) -> Optional[np.ndarray]:
        """
        Generate a CLIP text embedding for the provided query text.
        """
        return self._picture_tagger.generate_clip_text_embedding(query=query)

    def preprocess_query_words(self, words: list[str]) -> list[str]:
        """
        Preprocess a list of words using the PictureTagger.

        Args:
            words (list[str]): List of input words to preprocess.

        Returns:
            list[str]: Preprocessed list of words.
        """
        return self._picture_tagger.preprocess_query_words(words=words)

    def set_description(self, description: str):
        def op(session: Session):
            metadata = session.exec(
                select(MetaData).where(
                    MetaData.schema_version == MetaData.CURRENT_SCHEMA_VERSION
                )
            ).first()
            if metadata is None:
                metadata = MetaData(
                    schema_version=MetaData.CURRENT_SCHEMA_VERSION,
                    description=description,
                )
            else:
                metadata.description = description
            session.add(metadata)
            session.commit()

        self.db.submit_task(op, priority=DBPriority.IMMEDIATE)

    def get_description(self) -> Optional[str]:
        return self.db.submit_task(
            lambda session: (
                session.exec(
                    select(MetaData).where(
                        MetaData.schema_version == MetaData.CURRENT_SCHEMA_VERSION
                    )
                )
                .first()
                .description
            )
        ).result()

    def submit_task(self, task):
        """Submit an in-memory task to the shared task runner."""
        return self._task_runner.submit(task)

    def _on_task_completed(self, task, error):
        if error is not None or task.status != TaskStatus.COMPLETED:
            return

        result = task.result if isinstance(task.result, dict) else {}

        if task.type == "TagPredictionTask":
            picture_ids = result.get("picture_ids") or []
            if picture_ids:
                self._queue_changed_tags_notification(picture_ids)
            return

        if task.type == "ReferenceFolderScanTask":
            imported_ids = result.get("imported_picture_ids") or []
            if imported_ids:
                self.notify(EventType.CHANGED_PICTURES, imported_ids)
                self.notify(EventType.PICTURE_IMPORTED, imported_ids)
            picture_ids = result.get("caption_updated_picture_ids") or []
            if picture_ids:
                self._queue_changed_tags_notification(picture_ids)
            return

        if task.type == "SmartScoreTask":
            changed_count = int(result.get("changed_count") or 0)
            picture_ids = list(task.params.get("picture_ids") or [])
            if changed_count > 0 and picture_ids:
                smart_score_changes = [
                    (Picture, int(pic_id), "smart_score", None)
                    for pic_id in picture_ids
                    if pic_id is not None
                ]
                if smart_score_changes:
                    self._notify_worker_ids_processed(
                        TaskType.SMART_SCORE,
                        smart_score_changes,
                    )
                remaining = int(
                    self.db.run_immediate_read_task(SmartScoreTask.count_remaining)
                    or 0
                )
                if remaining == 0:
                    self.notify(EventType.CHANGED_PICTURES, picture_ids)
                else:
                    logger.debug(
                        "SmartScoreTask updated %s pictures; deferring CHANGED_PICTURES event with %s smart scores remaining.",
                        changed_count,
                        remaining,
                    )
            return

        changed = result.get("changed") if isinstance(result, dict) else None
        if not changed:
            return

        if task.type == "TagTask":
            self._notify_worker_ids_processed(TaskType.TAGGER, changed)
            picture_ids = [pic_id for _, pic_id, _, _ in changed]
            if picture_ids:
                self._queue_changed_tags_notification(picture_ids)
            return

        if task.type == "QualityTask":
            self._notify_worker_ids_processed(TaskType.QUALITY, changed)
            self.notify(EventType.QUALITY_UPDATED)
            return

        if task.type == "FaceQualityTask":
            self._notify_worker_ids_processed(TaskType.FACE_QUALITY, changed)
            return

        if task.type == "LikenessParametersTask":
            self._notify_worker_ids_processed(TaskType.LIKENESS_PARAMETERS, changed)
            return

        if task.type == "LikenessTask":
            self._notify_worker_ids_processed(TaskType.LIKENESS, changed)
            return

        if task.type == "FaceExtractionTask":
            self._notify_worker_ids_processed(TaskType.FACE_EXTRACTION, changed)
            picture_ids = result.get("picture_ids") or []
            if picture_ids:
                self.notify(EventType.CHANGED_FACES, picture_ids)
                self._process_pending_character_assignments(picture_ids)
            return

        if task.type == "DescriptionTask":
            self._notify_worker_ids_processed(TaskType.DESCRIPTION, changed)
            self.notify(EventType.CHANGED_DESCRIPTIONS)
            return

        if task.type == "TextEmbeddingTask":
            self._notify_worker_ids_processed(TaskType.TEXT_EMBEDDING, changed)
            return

        if task.type == "ImageEmbeddingTask":
            self._notify_worker_ids_processed(TaskType.IMAGE_EMBEDDING, changed)
            return

        if task.type == "WatchFolderImportTask":
            picture_ids = result.get("imported_picture_ids") or []
            if picture_ids:
                self.notify(EventType.CHANGED_PICTURES, picture_ids)
                self.notify(EventType.PICTURE_IMPORTED, picture_ids)

    def _process_pending_character_assignments(self, picture_ids: list[int]) -> None:
        """Honour deferred face-to-character assignments after face extraction runs.

        When POST /characters/{id}/faces is called before face extraction has run
        for a picture, the endpoint stores the target character id in
        Picture.pending_character_id.  This method is called after
        FaceExtractionTask completes and assigns the largest detected face for
        each such picture to the stored character, then clears the field.
        """

        def _assign_if_pending(session: Session) -> bool:
            pending = session.exec(
                select(Picture).where(
                    Picture.id.in_(picture_ids),
                    Picture.pending_character_id.is_not(None),
                )
            ).all()
            assigned_any = False
            for pic in pending:
                character_id = pic.pending_character_id
                pic.pending_character_id = None
                session.add(pic)
                faces = session.exec(
                    select(Face).where(
                        Face.picture_id == pic.id,
                        Face.face_index != -1,
                    )
                ).all()
                if not faces:
                    # Extraction ran but found no real faces; discard pending.
                    continue
                best_face = max(faces, key=lambda f: (f.width or 0) * (f.height or 0))
                if best_face.character_id != character_id:
                    best_face.character_id = character_id
                    session.add(best_face)
                    assigned_any = True
            session.commit()
            return assigned_any

        try:
            assigned = self.db.run_task(_assign_if_pending)
        except Exception:
            logger.exception("Failed to process pending character assignments")
            return
        if assigned:
            self.notify(EventType.CHANGED_CHARACTERS)
            self.notify(EventType.CHANGED_FACES)

    def _notify_worker_ids_processed(self, worker_type: TaskType, changed):
        self._notify_planner_ids_processed(worker_type, changed)

    def _queue_changed_tags_notification(self, picture_ids: list[int]) -> None:
        with self._changed_tags_notify_lock:
            self._changed_tags_pending_ids.update(
                int(pid) for pid in picture_ids if pid is not None
            )
            if self._changed_tags_flush_timer is not None:
                return
            self._changed_tags_flush_timer = threading.Timer(
                self._changed_tags_flush_delay_s,
                self._flush_changed_tags_notification,
            )
            self._changed_tags_flush_timer.daemon = True
            self._changed_tags_flush_timer.start()

    def _flush_changed_tags_notification(self) -> None:
        with self._changed_tags_notify_lock:
            pending_ids = list(self._changed_tags_pending_ids)
            self._changed_tags_pending_ids.clear()
            self._changed_tags_flush_timer = None
        if pending_ids:
            self.notify(EventType.CHANGED_TAGS, pending_ids)

    def _planner_attr_current_value(self, cls: type, object_id: int, attr: str):
        def fetch(session: Session):
            obj = session.get(cls, object_id)
            if obj is None:
                return False, None
            value = getattr(obj, attr, None)
            if attr in {"faces", "hands", "tags"}:
                try:
                    return len(value or []) > 0, value
                except Exception:
                    return False, value
            return value is not None, value

        return self.db.run_immediate_read_task(fetch)

    def _notify_planner_ids_processed(self, worker_type: TaskType, changed):
        with self._planner_watchers_lock:
            for cls, object_id, attr, payload in changed:
                future = self._planner_watchers.pop(
                    (worker_type, cls, object_id, attr),
                    None,
                )
                if future:
                    future.set_result((object_id, payload))

    def get_worker_future(
        self, worker_type: TaskType, cls: type, object_id: int, attr: str
    ) -> "concurrent.futures.Future":
        """
        Returns a Future that will be set when the specified worker has processed the given object ID.
        Args:
            worker_type (TaskType): The type of worker to wait for.
        Returns:
            concurrent.futures.Future: Future set to True when completed.
        """
        if not self._picture_tagger:
            self._picture_tagger = PictureTagger(image_root=self.image_root)
            self._picture_tagger.set_keep_models_in_memory(self._keep_models_in_memory)
            self._picture_tagger.set_max_vram_usage_gb(self._max_vram_gb)
            self._picture_tagger.set_wd14_tagger_enabled(self._wd14_tagger_enabled)
            self._picture_tagger.set_custom_tagger_enabled(self._custom_tagger_enabled)
            if self._wd14_threshold is not None:
                self._picture_tagger.set_wd14_threshold(self._wd14_threshold)
            if self._custom_tagger_threshold_offset is not None:
                self._picture_tagger.set_custom_tagger_threshold_offset(
                    self._custom_tagger_threshold_offset
                )

        # Register the watcher BEFORE checking the DB to avoid a TOCTOU race where
        # the task completes (and fires _notify_planner_ids_processed) in the gap
        # between the "already done?" check and registering the watcher, causing the
        # notification to be silently dropped and the future to never resolve.
        future = Future()
        with self._planner_watchers_lock:
            self._planner_watchers[(worker_type, cls, object_id, attr)] = future

        # Double-check: if the value is already in the DB (either it was ready all
        # along, or the task completed before we registered the watcher), resolve
        # the future ourselves.  If _notify_planner_ids_processed already fired and
        # resolved it, the pop will return None and we skip the duplicate set_result.
        is_ready, payload = self._planner_attr_current_value(cls, object_id, attr)
        if is_ready:
            with self._planner_watchers_lock:
                popped = self._planner_watchers.pop(
                    (worker_type, cls, object_id, attr), None
                )
            if popped is not None:
                future.set_result((object_id, payload))

        return future

    def is_worker_running(self, worker_type: TaskType) -> bool:
        """Check if a specific worker is running."""
        return bool(self._work_planner and self._work_planner.is_running())

    def _is_worker_active(self, worker_type: TaskType) -> bool:
        if not self._work_planner or not self._work_planner.is_running():
            return False
        finder = self._planner_work_finders.get(worker_type)
        if finder is None:
            return False
        finder_name = finder.finder_name()
        return self._work_planner.inflight_count(finder_name) > 0

    def _build_worker_progress_snapshot(self) -> dict:
        progress = {}
        for worker_type in TaskType.all():
            total = int(
                self.db.run_immediate_read_task(self._count_total_pictures) or 0
            )
            if worker_type == TaskType.DESCRIPTION:
                missing = int(
                    self.db.run_immediate_read_task(self._count_missing_descriptions)
                    or 0
                )
                label = "descriptions_generated"
            elif worker_type == TaskType.TAGGER:
                missing = int(
                    self.db.run_immediate_read_task(self._count_missing_tags) or 0
                )
                label = "pictures_tagged"
            elif worker_type == TaskType.QUALITY:
                missing = int(
                    self.db.run_immediate_read_task(self._count_missing_quality) or 0
                )
                label = "quality_scored"
            elif worker_type == TaskType.FACE_QUALITY:
                total = int(
                    self.db.run_immediate_read_task(self._count_total_faces) or 0
                )
                missing = int(
                    self.db.run_immediate_read_task(self._count_missing_face_quality)
                    or 0
                )
                label = "face_quality_scored"
            elif worker_type == TaskType.FACE_EXTRACTION:
                missing = int(
                    self.db.run_immediate_read_task(
                        self._count_missing_feature_extractions
                    )
                    or 0
                )
                label = "features_extracted"
            elif worker_type == TaskType.TEXT_EMBEDDING:
                described = int(
                    self.db.run_immediate_read_task(self._count_total_described) or 0
                )
                missing = int(
                    self.db.run_immediate_read_task(self._count_missing_text_embeddings)
                    or 0
                )
                total = max(described, 0)
                label = "text_embeddings"
            elif worker_type == TaskType.IMAGE_EMBEDDING:
                missing = int(
                    self.db.run_immediate_read_task(
                        self._count_missing_image_embeddings
                    )
                    or 0
                )
                label = "image_embeddings"
            elif worker_type == TaskType.LIKENESS_PARAMETERS:
                missing = int(
                    self.db.run_immediate_read_task(
                        self._count_pending_likeness_parameters
                    )
                    or 0
                )
                label = "likeness_parameters"
            elif worker_type == TaskType.LIKENESS:
                total = int(
                    self.db.run_immediate_read_task(
                        self._count_total_likeness_candidates
                    )
                    or 0
                )
                missing = int(
                    self.db.run_immediate_read_task(self._count_pending_likeness_queue)
                    or 0
                )
                label = "likeness_pairs"
            elif worker_type == TaskType.WATCH_FOLDERS:
                total = 0
                missing = 0
                label = "watch_folder_import"
            elif worker_type == TaskType.COMFYUI_EXTRACTION:
                missing = int(
                    self.db.run_immediate_read_task(
                        self._count_missing_comfyui_extraction
                    )
                    or 0
                )
                label = "comfyui_extraction"
            elif worker_type == TaskType.TAG_PREDICTION:
                tagger = self._picture_tagger
                use_custom = tagger is not None and getattr(
                    tagger, "_use_custom_tagger", False
                )
                if use_custom:
                    model_version = f"v{tagger.custom_tagger_version()}"
                    missing = int(
                        self.db.run_immediate_read_task(
                            lambda s, mv=model_version: (
                                self._count_missing_tag_predictions(s, mv)
                            )
                        )
                        or 0
                    )
                else:
                    missing = 0
                label = "tag_predictions_scored"
            elif worker_type == TaskType.MISSING_FILE_PURGE:
                total = 0
                missing = 0
                label = "missing_file_purge"
            else:
                missing = 0
                label = "planner_managed"
            worker_active = self._is_worker_active(worker_type)
            progress[worker_type.value] = {
                "label": label,
                "current": max(total - missing, 0),
                "total": total,
                "remaining": max(missing, 0),
                "updated_at": time.time(),
                "status": "running" if worker_active else "idle",
                "running": worker_active,
                "active": worker_active,
            }
        return progress

    @staticmethod
    def _count_missing_tag_predictions(session: Session, model_version: str) -> int:
        # Only pictures that have at least one real (non-sentinel) tag are
        # eligible for prediction scoring — sentinel-only pictures are waiting
        # for the tagger worker, not the prediction worker.
        from sqlalchemy import exists as sa_exists

        eligible_subq = (
            select(Tag.picture_id)
            .where(
                Tag.picture_id == Picture.id,
                Tag.tag.is_not(None),
                Tag.tag != TAG_EMPTY_SENTINEL,
            )
            .correlate(Picture)
        )
        total_result = session.exec(
            select(func.count())
            .select_from(Picture)
            .where(
                Picture.deleted.is_(False),
                Picture.file_path.is_not(None),
                sa_exists(eligible_subq),
            )
        ).one()
        total = (
            total_result[0]
            if isinstance(total_result, (tuple, list))
            else (total_result or 0)
        )

        scored_result = session.exec(
            select(func.count(func.distinct(TagPrediction.picture_id))).where(
                TagPrediction.model_version == model_version
            )
        ).one()
        scored = (
            scored_result[0]
            if isinstance(scored_result, (tuple, list))
            else (scored_result or 0)
        )

        return max(0, int(total) - int(scored))

    @staticmethod
    def _count_total_pictures(session: Session) -> int:
        result = session.exec(select(func.count()).select_from(Picture)).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

    @staticmethod
    def _count_missing_descriptions(session: Session) -> int:
        result = session.exec(
            select(func.count())
            .select_from(Picture)
            .where(Picture.description.is_(None))
        ).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

    @staticmethod
    def _count_missing_tags(session: Session) -> int:
        has_real_tag = (Tag.tag.is_not(None)) & (Tag.tag != TAG_EMPTY_SENTINEL)
        result = session.exec(
            select(func.count())
            .select_from(Picture)
            .where(~Picture.tags.any(has_real_tag))
        ).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

    @staticmethod
    def _count_missing_feature_extractions(session: Session) -> int:
        result = session.exec(
            select(func.count()).select_from(Picture).where(~Picture.faces.any())
        ).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

    @staticmethod
    def _count_missing_quality(session: Session) -> int:
        return QualityTask.count_missing_quality(session)

    @staticmethod
    def _count_total_faces(session: Session) -> int:
        return FaceQualityTask.count_total_faces(session)

    @staticmethod
    def _count_missing_face_quality(session: Session) -> int:
        return FaceQualityTask.count_missing_face_quality(session)

    @staticmethod
    def _count_total_described(session: Session) -> int:
        result = session.exec(
            select(func.count())
            .select_from(Picture)
            .where(Picture.description.is_not(None))
        ).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

    @staticmethod
    def _count_missing_text_embeddings(session: Session) -> int:
        result = session.exec(
            select(func.count())
            .select_from(Picture)
            .where(Picture.description.is_not(None))
            .where(Picture.text_embedding.is_(None))
        ).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

    @staticmethod
    def _count_missing_comfyui_extraction(session: Session) -> int:
        result = session.exec(
            select(func.count())
            .select_from(Picture)
            .where(Picture.comfyui_models.is_(None))
            .where(Picture.deleted.is_(False))
        ).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

    @staticmethod
    def _count_missing_image_embeddings(session: Session) -> int:
        return ImageEmbeddingTask.count_remaining(session)

    @staticmethod
    def _count_pending_likeness_parameters(session: Session) -> int:
        return LikenessParameterUtils.count_pending_parameters(session)

    @staticmethod
    def _count_pending_likeness_queue(session: Session) -> int:
        return LikenessTask.count_queue(session)

    @staticmethod
    def _count_total_likeness_candidates(session: Session) -> int:
        return LikenessTask.count_total_candidates(session)

    def get_worker_progress(self) -> dict:
        progress = self._build_worker_progress_snapshot()
        self._maybe_aggressive_unload(progress)
        return progress

    def _maybe_aggressive_unload(self, progress: dict):
        if self._keep_models_in_memory:
            return
        if not self._picture_tagger:
            return
        now = time.time()
        if now - self._last_aggressive_unload_at < self.AGGRESSIVE_UNLOAD_INTERVAL:
            return

        any_busy = False
        for snapshot in progress.values():
            status = snapshot.get("status")
            running = bool(snapshot.get("running"))
            if not running:
                continue
            if status in ("idle", "stopped", "uninitialized"):
                continue

            current = int(snapshot.get("current") or 0)
            total = int(snapshot.get("total") or 0)
            remaining = snapshot.get("remaining")
            if remaining is None:
                remaining = max(0, total - current)
            else:
                remaining = max(0, int(remaining))

            has_pending_work = remaining > 0 or (total > 0 and current < total)
            if has_pending_work:
                any_busy = True
                break
        if any_busy:
            return

        logger.warning("All workers idle; aggressively unloading models.")
        try:
            self._picture_tagger.aggressive_unload()
        except Exception as exc:
            logger.warning("Aggressive unload failed for PictureTagger: %s", exc)
        try:
            FaceExtractionTask.release_detection_models()
        except Exception as exc:
            logger.warning(
                "Aggressive unload failed for feature extraction models: %s", exc
            )
        try:
            ImageEmbeddingTask.release_models()
        except Exception as exc:
            logger.warning(
                "Aggressive unload failed for image embedding models: %s", exc
            )
        if platform.system().lower().startswith("linux"):
            try:
                ctypes.CDLL("libc.so.6").malloc_trim(0)
            except Exception as exc:
                logger.debug("malloc_trim call failed: %s", exc)
        self._last_aggressive_unload_at = now

    def import_default_data(self, add_tagger_test_images: bool = False):
        """
        Import default data into the vault.
        Extend this method to add default pictures or metadata as needed.
        """
        # Add Logo.png to every vault

        logo_src = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Logo.png")
        logo_dest_folder = self.image_root
        logger.debug(f"logo_dest_folder in _import_default_data: {logo_dest_folder}")

        characters = [
            "Esmeralda Vault",
            "Barbara Vault",
            "Barry Vault",
            "Cassandra Vault",
        ]

        def add_character(session: Session, character: Character):
            session.add(character)
            session.commit()
            session.refresh(character)
            char_id = character.id
            char_name = character.name
            # Create reference picture set for this character, using character name
            reference_set = PictureSet(
                name="reference_pictures", description=str(char_name)
            )
            session.add(reference_set)
            session.commit()
            session.refresh(reference_set)
            return char_id, char_name

        for character_name in characters:
            self.db.run_task(
                lambda session: add_character(
                    session,
                    Character(
                        name=character_name, description="Built-in vault character"
                    ),
                ),
                priority=DBPriority.IMMEDIATE,
            )

        picture = ImageUtils.create_picture_from_file(
            image_root_path=logo_dest_folder,
            source_file_path=logo_src,
        )
        picture.description = "PixlStash Logo"
        picture.imported_at = datetime.datetime.now()

        assert picture.file_path

        def add_picture(session: Session, picture: Picture):
            session.add(picture)
            session.commit()
            session.refresh(picture)
            return picture

        picture = self.db.run_task(
            lambda session: add_picture(session, picture),
            priority=DBPriority.IMMEDIATE,
        )

        if add_tagger_test_images:
            # Add all pictures/TaggerTest*.png
            for file in os.listdir(
                os.path.join(os.path.dirname(os.path.dirname(__file__)), "pictures")
            ):
                if file.startswith("TaggerTest") and file.endswith(".png"):
                    src_path = os.path.join(
                        os.path.dirname(os.path.dirname(__file__)),
                        "pictures",
                        file,
                    )
                    pic = ImageUtils.create_picture_from_file(
                        image_root_path=logo_dest_folder,
                        source_file_path=src_path,
                    )
                    pic.description = os.path.basename(src_path)
                    pic.imported_at = datetime.datetime.now()
                    assert pic.file_path
                    self.db.run_task(
                        add_picture,
                        pic,
                        priority=DBPriority.IMMEDIATE,
                    )
                    logger.debug(f"Imported default picture: {pic.file_path}")
        logger.info("Imported default data into the vault.")
