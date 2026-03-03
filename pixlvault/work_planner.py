import threading

from pixlvault.pixl_logging import get_logger


logger = get_logger(__name__)


class WorkPlanner:
    """Central planner that discovers tasks through registered task finders."""

    MIN_INTERVAL_S = 0.2
    MAX_INTERVAL_S = 10.0
    BACKOFF_FACTOR = 1.8

    @staticmethod
    def work_finders(database, picture_tagger_getter, config_path=None):
        from pixlvault.tasks import TaskType
        from pixlvault.tasks.missing_description_finder import MissingDescriptionFinder
        from pixlvault.tasks.missing_face_quality_finder import MissingFaceQualityFinder
        from pixlvault.tasks.missing_feature_extraction_finder import (
            MissingFeatureExtractionFinder,
        )
        from pixlvault.tasks.missing_image_embeddings_finder import (
            MissingImageEmbeddingsFinder,
        )
        from pixlvault.tasks.missing_likeness_parameters_finder import (
            MissingLikenessParametersFinder,
        )
        from pixlvault.tasks.missing_likeness_finder import MissingLikenessFinder
        from pixlvault.tasks.missing_quality_finder import MissingQualityFinder
        from pixlvault.tasks.missing_text_embeddings_finder import (
            MissingTextEmbeddingsFinder,
        )
        from pixlvault.tasks.missing_tags_finder import MissingTagsFinder
        from pixlvault.tasks.missing_watch_folder_imports_finder import (
            MissingWatchFolderImportsFinder,
        )

        return {
            TaskType.FACE: MissingFeatureExtractionFinder(
                database=database,
                picture_tagger_getter=picture_tagger_getter,
            ),
            TaskType.QUALITY: MissingQualityFinder(
                database=database,
            ),
            TaskType.FACE_QUALITY: MissingFaceQualityFinder(
                database=database,
            ),
            TaskType.TAGGER: MissingTagsFinder(
                database=database,
                picture_tagger_getter=picture_tagger_getter,
            ),
            TaskType.DESCRIPTION: MissingDescriptionFinder(
                database=database,
                picture_tagger_getter=picture_tagger_getter,
            ),
            TaskType.TEXT_EMBEDDING: MissingTextEmbeddingsFinder(
                database=database,
                picture_tagger_getter=picture_tagger_getter,
            ),
            TaskType.IMAGE_EMBEDDING: MissingImageEmbeddingsFinder(
                database=database,
                picture_tagger_getter=picture_tagger_getter,
            ),
            TaskType.LIKENESS_PARAMETERS: MissingLikenessParametersFinder(
                database=database,
            ),
            TaskType.LIKENESS: MissingLikenessFinder(
                database=database,
            ),
            TaskType.WATCH_FOLDERS: MissingWatchFolderImportsFinder(
                database=database,
                config_path=config_path,
            ),
        }

    def __init__(self, task_runner, task_finders: list):
        self._task_runner = task_runner
        self._task_finders = task_finders or []
        self._task_finders_by_name = {
            finder.finder_name(): finder for finder in self._task_finders
        }

        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None

        self._interval_s = self.MIN_INTERVAL_S
        self._finder_order_idx = 0
        self._inflight_by_finder = {}
        self._finder_by_task_id = {}
        self._lock = threading.Lock()

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._wake.clear()
        self._thread = threading.Thread(
            target=self._run, name="WorkPlanner", daemon=True
        )
        self._thread.start()
        logger.info("WorkPlanner started with %s finders.", len(self._task_finders))

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                logger.warning("WorkPlanner did not stop within timeout.")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def inflight_count(self, finder_name: str) -> int:
        if not finder_name:
            return 0
        with self._lock:
            return int(self._inflight_by_finder.get(finder_name, 0))

    def wake(self):
        self._wake.set()

    def on_task_complete(self, task, error):
        finder_name = None
        with self._lock:
            finder_name = self._finder_by_task_id.pop(getattr(task, "id", None), None)
            if finder_name:
                inflight_count = int(self._inflight_by_finder.get(finder_name, 0))
                self._inflight_by_finder[finder_name] = max(0, inflight_count - 1)
        if finder_name:
            finder = self._task_finders_by_name.get(finder_name)
            if finder is not None:
                try:
                    finder.on_task_complete(task, error)
                except Exception as exc:
                    logger.warning(
                        "Finder completion callback failed for %s: %s",
                        finder_name,
                        exc,
                    )
        self._wake.set()

    def _run(self):
        while not self._stop.is_set():
            submitted = self._run_finders_once()

            if submitted:
                self._interval_s = self.MIN_INTERVAL_S
            else:
                self._interval_s = min(
                    self.MAX_INTERVAL_S,
                    max(self.MIN_INTERVAL_S, self._interval_s * self.BACKOFF_FACTOR),
                )

            self._wake.wait(self._interval_s)
            self._wake.clear()

        logger.info("WorkPlanner stopped.")

    def _run_finders_once(self) -> bool:
        if not self._task_finders:
            return False

        finder_count = len(self._task_finders)
        for offset in range(finder_count):
            idx = (self._finder_order_idx + offset) % finder_count
            finder = self._task_finders[idx]
            finder_name = finder.finder_name()
            max_inflight = max(1, int(finder.max_inflight_tasks()))

            with self._lock:
                inflight_count = int(self._inflight_by_finder.get(finder_name, 0))
            if inflight_count >= max_inflight:
                continue

            task = finder.find_task()
            if task is None:
                continue

            task_id = self._task_runner.submit(task)
            with self._lock:
                self._inflight_by_finder[finder_name] = inflight_count + 1
                self._finder_by_task_id[task_id] = finder_name

            self._finder_order_idx = (idx + 1) % finder_count
            logger.debug(
                "WorkPlanner submitted task id=%s via finder=%s",
                task_id,
                finder_name,
            )
            return True

        self._finder_order_idx = (self._finder_order_idx + 1) % finder_count
        return False
