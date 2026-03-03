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
    Picture,
    PictureSet,
    Tag,
    TAG_EMPTY_SENTINEL,
)
from .pixl_logging import get_logger
from .picture_tagger import PictureTagger
from .utils.image_processing.image_utils import ImageUtils
from .tasks.face_quality_task import FaceQualityTask
from .tasks.feature_extraction_task import FeatureExtractionTask
from .tasks.image_embedding_task import ImageEmbeddingTask
from .tasks.likeness_task import LikenessTask
from .tasks.quality_task import QualityTask
from .tasks.base_task import TaskStatus
from .task_runner import TaskRunner
from .work_planner import WorkPlanner
from .tasks import TaskType

from pixlvault.event_types import EventType


logger = get_logger(__name__)


class Vault:
    AGGRESSIVE_UNLOAD_INTERVAL = 180
    MODEL_STATUS_LOG_INTERVAL = 60

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
        self._last_model_status_log_at = 0.0
        self._keep_models_in_memory = True
        self._max_vram_gb = None
        self._server_config_path = server_config_path

        self._planner_watchers = {}
        self._planner_watchers_lock = threading.Lock()
        self._event_listeners = []
        self._event_listeners_lock = threading.Lock()
        self._task_runner = TaskRunner(name="vault-task-runner")
        self._planner_work_finders = WorkPlanner.work_finders(
            database=self.db,
            picture_tagger_getter=lambda: self._picture_tagger,
            config_path=self._server_config_path,
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

    def ensure_ready(self):
        """Initialise the picture tagger so the planner can process work immediately.

        Call this at server startup. Tests that do not need the tagger can skip it;
        tagger init is also triggered lazily by get_worker_future().
        """
        if not self._picture_tagger:
            self._picture_tagger = PictureTagger(image_root=self.image_root)
            self._picture_tagger.set_keep_models_in_memory(self._keep_models_in_memory)
            self._picture_tagger.set_max_vram_usage_gb(self._max_vram_gb)

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

    def add_event_listener(self, listener):
        """Register a callback to be invoked when vault events occur."""
        if not callable(listener):
            raise ValueError("listener must be callable")
        with self._event_listeners_lock:
            if listener not in self._event_listeners:
                self._event_listeners.append(listener)

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
        self._work_planner.stop()
        self._task_runner.stop()
        FeatureExtractionTask.release_detection_models()
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
        preprocessed_words = self._picture_tagger.preprocess_query_words(words=words)
        return preprocessed_words

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
            lambda session: session.exec(
                select(MetaData).where(
                    MetaData.schema_version == MetaData.CURRENT_SCHEMA_VERSION
                )
            )
            .first()
            .description
        ).result()

    def submit_task(self, task):
        """Submit an in-memory task to the shared task runner."""
        return self._task_runner.submit(task)

    def _on_task_completed(self, task, error):
        if error is not None or task.status != TaskStatus.COMPLETED:
            return

        result = task.result if isinstance(task.result, dict) else {}
        changed = result.get("changed") if isinstance(result, dict) else None
        if not changed:
            return

        if task.type == "TagTask":
            self._notify_worker_ids_processed(TaskType.TAGGER, changed)
            picture_ids = [pic_id for _, pic_id, _, _ in changed]
            if picture_ids:
                self.notify(EventType.CHANGED_TAGS, picture_ids)
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

        if task.type == "FeatureExtractionTask":
            self._notify_worker_ids_processed(TaskType.FACE, changed)
            picture_ids = result.get("picture_ids") or []
            if picture_ids:
                self.notify(EventType.CHANGED_FACES, picture_ids)
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

    def _notify_worker_ids_processed(self, worker_type: TaskType, changed):
        self._notify_planner_ids_processed(worker_type, changed)

    def _watch_planner_id(self, worker_type: TaskType, cls: type, object_id, attr: str):
        future = Future()
        with self._planner_watchers_lock:
            self._planner_watchers[(worker_type, cls, object_id, attr)] = future
        return future

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

    def _resolve_planner_future_if_already_processed(
        self,
        cls: type,
        object_id: int,
        attr: str,
    ) -> Future | None:
        is_ready, payload = self._planner_attr_current_value(cls, object_id, attr)
        if not is_ready:
            return None
        future = Future()
        future.set_result((object_id, payload))
        return future

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
        resolved_future = self._resolve_planner_future_if_already_processed(
            cls,
            object_id,
            attr,
        )
        if resolved_future is not None:
            return resolved_future
        return self._watch_planner_id(worker_type, cls, object_id, attr)

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
            elif worker_type == TaskType.FACE:
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
    def _count_missing_image_embeddings(session: Session) -> int:
        return ImageEmbeddingTask.count_remaining(session)

    @staticmethod
    def _count_pending_likeness_parameters(session: Session) -> int:
        result = session.exec(
            select(func.count())
            .select_from(Picture)
            .where(
                (Picture.likeness_parameters.is_(None))
                | (Picture.size_bin_index.is_(None))
            )
        ).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

    @staticmethod
    def _count_pending_likeness_queue(session: Session) -> int:
        return LikenessTask.count_queue(session)

    @staticmethod
    def _count_total_likeness_candidates(session: Session) -> int:
        return LikenessTask.count_total_candidates(session)

    def get_worker_progress(self) -> dict:
        progress = self._build_worker_progress_snapshot()
        self._maybe_aggressive_unload(progress)
        self._maybe_log_model_status(progress)
        return progress

    def _maybe_log_model_status(self, progress: dict):
        now = time.time()
        if now - self._last_model_status_log_at < self.MODEL_STATUS_LOG_INTERVAL:
            return

        worker_busy = 0
        for snapshot in (progress or {}).values():
            status = snapshot.get("status")
            current = int(snapshot.get("current") or 0)
            total = int(snapshot.get("total") or 0)
            remaining = snapshot.get("remaining")
            if remaining is None:
                remaining = max(0, total - current)
            else:
                remaining = max(0, int(remaining))
            if status not in ("idle", "stopped", "uninitialized") and (
                remaining > 0 or (total > 0 and current < total)
            ):
                worker_busy += 1

        model_state = {}
        if self._picture_tagger and hasattr(self._picture_tagger, "loaded_model_state"):
            try:
                model_state = self._picture_tagger.loaded_model_state()
            except Exception as exc:
                model_state = {"error": f"failed_to_collect_tagger_state:{exc}"}

        model_state["insightface_loaded"] = bool(
            getattr(FeatureExtractionTask, "_global_insightface_app", None) is not None
        )
        model_state["workers_busy"] = worker_busy
        logger.info("Model residency status: %s", model_state)
        self._last_model_status_log_at = now

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
            FeatureExtractionTask.release_detection_models()
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
            except Exception:
                pass
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
        picture.description = "PixlVault Logo"
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
