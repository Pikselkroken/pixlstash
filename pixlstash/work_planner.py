import threading
from typing import TYPE_CHECKING

from pixlstash.pixl_logging import get_logger

if TYPE_CHECKING:
    from pixlstash.tasks.task_type import TaskType


logger = get_logger(__name__)


class WorkPlanner:
    """Central planner that discovers tasks through registered task finders."""

    MIN_INTERVAL_S = 0.05
    MAX_INTERVAL_S = 10.0
    BACKOFF_FACTOR = 1.8
    # How long stop() waits for the loop thread, and how long start() waits for
    # a previous thread that is still winding down.
    STOP_JOIN_TIMEOUT_S = 5.0

    @staticmethod
    def work_finders(
        database,
        engine_getter,
        image_root=None,
        path_mapper=None,
    ):
        from pixlstash.tasks import TaskType
        from pixlstash.tasks.missing_description_finder import MissingDescriptionFinder
        from pixlstash.tasks.missing_face_extraction_finder import (
            MissingFaceExtractionFinder,
        )
        from pixlstash.tasks.missing_face_model_refresh_finder import (
            MissingFaceModelRefreshFinder,
        )
        from pixlstash.tasks.missing_image_embedding_finder import (
            MissingImageEmbeddingFinder,
        )
        from pixlstash.tasks.missing_likeness_parameters_finder import (
            MissingLikenessParametersFinder,
        )
        from pixlstash.tasks.missing_likeness_finder import MissingLikenessFinder
        from pixlstash.tasks.missing_quality_finder import MissingQualityFinder
        from pixlstash.tasks.missing_text_embedding_finder import (
            MissingTextEmbeddingFinder,
        )
        from pixlstash.tasks.missing_tag_finder import MissingTagFinder
        from pixlstash.tasks.missing_tag_prediction_finder import (
            MissingTagPredictionFinder,
        )
        from pixlstash.tasks.missing_watch_folder_import_finder import (
            MissingWatchFolderImportFinder,
        )
        from pixlstash.tasks.missing_comfyui_extraction_finder import (
            MissingComfyUIExtractionFinder,
        )
        from pixlstash.tasks.missing_source_face_likeness_finder import (
            MissingSourceFaceLikenessCharacterFinder,
        )
        from pixlstash.tasks.missing_file_purge_finder import MissingFilePurgeFinder
        from pixlstash.tasks.missing_snapshot_identity_scrub_finder import (
            MissingSnapshotIdentityScrubFinder,
        )
        from pixlstash.tasks.reference_folder_scan_finder import (
            ReferenceFolderScanFinder,
        )
        from pixlstash.tasks.missing_text_score_finder import MissingTextScoreFinder
        from pixlstash.tasks.missing_thumbnail_finder import MissingThumbnailFinder
        from pixlstash.tasks.missing_pixel_sha_finder import MissingPixelShaFinder
        from pixlstash.tasks.dedup_scan_finder import DedupScanFinder
        from pixlstash.tasks.missing_stack_cohesion_finder import (
            MissingStackCohesionFinder,
        )

        from pixlstash.utils.path_mapper import PathMapper

        effective_path_mapper = path_mapper if path_mapper is not None else PathMapper()

        return {
            TaskType.FACE_EXTRACTION: MissingFaceExtractionFinder(
                database=database,
                engine_getter=engine_getter,
            ),
            TaskType.FACE_MODEL_REFRESH: MissingFaceModelRefreshFinder(
                database=database,
                engine_getter=engine_getter,
            ),
            TaskType.QUALITY: MissingQualityFinder(
                database=database,
            ),
            TaskType.TAGGER: MissingTagFinder(
                database=database,
                engine_getter=engine_getter,
            ),
            TaskType.TAG_PREDICTION_BACKFILL: MissingTagPredictionFinder(
                database=database,
                engine_getter=engine_getter,
            ),
            TaskType.DESCRIPTION: MissingDescriptionFinder(
                database=database,
                engine_getter=engine_getter,
            ),
            TaskType.TEXT_EMBEDDING: MissingTextEmbeddingFinder(
                database=database,
                engine_getter=engine_getter,
            ),
            TaskType.IMAGE_EMBEDDING: MissingImageEmbeddingFinder(
                database=database,
                engine_getter=engine_getter,
            ),
            TaskType.LIKENESS_PARAMETERS: MissingLikenessParametersFinder(
                database=database,
            ),
            TaskType.LIKENESS: MissingLikenessFinder(
                database=database,
            ),
            TaskType.WATCH_FOLDERS: MissingWatchFolderImportFinder(
                database=database,
            ),
            TaskType.COMFYUI_EXTRACTION: MissingComfyUIExtractionFinder(
                database=database,
                image_root=image_root or "",
            ),
            TaskType.SOURCE_FACE_LIKENESS: MissingSourceFaceLikenessCharacterFinder(
                database=database,
            ),
            TaskType.MISSING_FILE_PURGE: MissingFilePurgeFinder(
                database=database,
            ),
            TaskType.SNAPSHOT_IDENTITY_SCRUB: MissingSnapshotIdentityScrubFinder(
                database=database,
            ),
            TaskType.REFERENCE_FOLDER_SCAN: ReferenceFolderScanFinder(
                database=database,
                path_mapper=effective_path_mapper,
            ),
            TaskType.TEXT_SCORE: MissingTextScoreFinder(
                database=database,
            ),
            TaskType.THUMBNAIL_GENERATION: MissingThumbnailFinder(
                database=database,
            ),
            TaskType.PIXEL_SHA: MissingPixelShaFinder(
                database=database,
            ),
            TaskType.DEDUP_SCAN: DedupScanFinder(
                database=database,
            ),
            TaskType.STACK_COHESION: MissingStackCohesionFinder(
                database=database,
            ),
        }

    def __init__(
        self,
        task_runner,
        task_finders: "dict[TaskType, object] | list",
    ):
        self._task_runner = task_runner
        if isinstance(task_finders, dict):
            self._task_finders = list(task_finders.values())
            # Maps TaskType → finder_name string for resolving typed depends_on() values.
            self._finder_name_by_task_type: dict = {
                task_type: finder.finder_name()
                for task_type, finder in task_finders.items()
            }
        else:
            self._task_finders = list(task_finders)
            self._finder_name_by_task_type: dict = {}
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
        # Tracks whether a finder has explicitly returned None (no work found).
        # A finder starts as NOT exhausted (False = may have work) so that
        # dependent finders are blocked until the finder has been given a chance
        # to run and confirmed it has nothing to do.
        self._finder_exhausted: dict[str, bool] = {}
        self._lock = threading.Lock()
        # Serialises start()/stop() so a restart cannot interleave with a
        # shutdown that is still joining the outgoing thread.
        self._lifecycle_lock = threading.RLock()

    def start(self):
        with self._lifecycle_lock:
            previous = self._thread
            if previous is not None and previous.is_alive():
                if not self._stop.is_set():
                    return
                # A thread that is alive with _stop set is winding down from an
                # earlier stop() whose bounded join expired. Returning here
                # would leave _stop set and, once that thread notices it and
                # exits, the planner permanently dead with is_running() having
                # answered True at the moment it was asked. Wait it out instead.
                logger.info(
                    "WorkPlanner start() found the previous thread still "
                    "shutting down; waiting for it before restarting."
                )
                previous.join(timeout=self.STOP_JOIN_TIMEOUT_S)
                if previous.is_alive():
                    raise RuntimeError(
                        "WorkPlanner cannot restart: the previous loop thread "
                        f"is still alive {self.STOP_JOIN_TIMEOUT_S}s after "
                        "stop(). A finder is blocked; starting a second loop "
                        "would double-submit work."
                    )
            self._stop.clear()
            self._wake.clear()
            self._thread = threading.Thread(
                target=self._run, name="WorkPlanner", daemon=True
            )
            self._thread.start()
        with self._lock:
            finder_count = len(self._task_finders)
        logger.debug("WorkPlanner started with %s finders.", finder_count)

    def stop(self):
        with self._lifecycle_lock:
            self._stop.set()
            self._wake.set()
            if self._thread is not None:
                self._thread.join(timeout=self.STOP_JOIN_TIMEOUT_S)
                if self._thread.is_alive():
                    logger.warning("WorkPlanner did not stop within timeout.")

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def has_finder(self, task_type: "TaskType") -> bool:
        """Whether a finder for *task_type* is still registered with the loop."""
        with self._lock:
            name = self._finder_name_by_task_type.get(task_type)
            return bool(name) and name in self._task_finders_by_name

    def detach_finders(self, task_types) -> set:
        """Unregister the finders for *task_types* and return their names.

        Supported replacement for reaching into `_task_finders` from a test
        fixture: the planner keeps the finder set in three structures, all of
        which have to stay consistent, and mutating them from another thread
        used to race the loop's own read. Detached finders are marked exhausted
        so a finder that `depends_on()` one of them is not blocked for ever
        waiting for a report that will never come again.
        """
        removed = set()
        with self._lock:
            for task_type in task_types:
                name = self._finder_name_by_task_type.pop(task_type, None)
                if not name:
                    continue
                finder = self._task_finders_by_name.pop(name, None)
                if finder is None:
                    continue
                self._task_finders = [f for f in self._task_finders if f is not finder]
                self._finder_exhausted[name] = True
                removed.add(name)
            self._finder_order_idx = 0
        return removed

    def registered_finder_names(self) -> set:
        """Names of the finders the loop will currently visit."""
        with self._lock:
            return set(self._task_finders_by_name)

    def inflight_count(self, finder_name: str) -> int:
        if not finder_name:
            return 0
        with self._lock:
            return int(self._inflight_by_finder.get(finder_name, 0))

    def wake(self):
        self._wake.set()

    def on_task_complete(self, task, error):
        finder_name = None
        all_done = False
        with self._lock:
            finder_name = self._finder_by_task_id.pop(getattr(task, "id", None), None)
            if finder_name:
                inflight_count = int(self._inflight_by_finder.get(finder_name, 0))
                new_inflight = max(0, inflight_count - 1)
                self._inflight_by_finder[finder_name] = new_inflight
                is_exhausted = self._finder_exhausted.get(finder_name, False)
                all_done = new_inflight == 0 and is_exhausted
        if finder_name:
            _GPU_FINDERS = {"MissingTagFinder", "MissingFaceExtractionFinder"}
            if new_inflight == 0 and not is_exhausted and finder_name in _GPU_FINDERS:
                logger.warning(
                    "[PIPELINE_STALL] %s inflight count hit 0 while work remains; "
                    "GPU may be idle until next finder cycle.",
                    finder_name,
                )
            with self._lock:
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
                if all_done:
                    try:
                        finder.on_all_tasks_complete()
                    except Exception as exc:
                        logger.warning(
                            "Finder all-complete callback failed for %s: %s",
                            finder_name,
                            exc,
                        )
        self._wake.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                submitted = self._run_finders_once()
            except Exception:
                # Without this the exception unwinds the thread and the planner
                # is simply gone: nothing schedules work again, is_running()
                # answers False and every dependent caller reports its own
                # unrelated symptom. Log it and keep cycling; the backoff below
                # keeps a persistently failing cycle from spinning.
                logger.exception(
                    "WorkPlanner cycle raised; the planner keeps running. "
                    "Finders registered: %s",
                    sorted(self.registered_finder_names()),
                )
                submitted = False

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
        # One immutable snapshot per cycle. The finder set is mutated from other
        # threads (detach_finders), and indexing the live list against a length
        # captured a moment earlier throws IndexError, which used to unwind the
        # loop thread outright. A snapshot is taken rather than holding the lock
        # across the loop because find_task() does database and filesystem work
        # and must not block on_task_complete() for the length of it.
        with self._lock:
            finders = list(self._task_finders)
            order_idx = self._finder_order_idx
        if not finders:
            return False

        submitted_any = False
        finder_count = len(finders)
        for offset in range(finder_count):
            idx = (order_idx + offset) % finder_count
            finder = finders[idx]
            finder_name = finder.finder_name()
            max_inflight = max(1, int(finder.max_inflight_tasks()))

            blocking_finders = finder.depends_on()
            if blocking_finders:
                blocking_names = [
                    self._finder_name_by_task_type.get(tt, str(tt))
                    for tt in blocking_finders
                ]
                with self._lock:
                    if any(
                        self._inflight_by_finder.get(name, 0) > 0
                        or not self._finder_exhausted.get(name, False)
                        for name in blocking_names
                    ):
                        continue

            # Fill all available inflight slots for this finder in one pass so
            # that fast-completing tasks (e.g. custom tagger at ~100ms) don't
            # leave the GPU idle while the planner sleeps MIN_INTERVAL_S between
            # cycles.  Previously, a single submit + return True meant inflight
            # was always 1 regardless of max_inflight.
            while True:
                with self._lock:
                    inflight_count = int(self._inflight_by_finder.get(finder_name, 0))
                if inflight_count >= max_inflight:
                    break

                task = finder.find_task()
                if task is None:
                    with self._lock:
                        self._finder_exhausted[finder_name] = True
                    break
                # A finder may block in database or filesystem work long enough
                # for stop()'s bounded join to return. Do not submit the task it
                # found after shutdown has begun and the runner may already be
                # stopped by Vault.stop().
                if self._stop.is_set():
                    self._release_unsubmitted(
                        finder, task, "the planner stopped before it could submit"
                    )
                    return submitted_any

                task_id = getattr(task, "id", None)
                with self._lock:
                    current_inflight = int(self._inflight_by_finder.get(finder_name, 0))
                    self._inflight_by_finder[finder_name] = current_inflight + 1
                    self._finder_exhausted[finder_name] = False
                    if task_id:
                        self._finder_by_task_id[task_id] = finder_name

                try:
                    submitted_task_id = self._task_runner.submit(task)
                except Exception as submit_exc:
                    with self._lock:
                        current_inflight = int(
                            self._inflight_by_finder.get(finder_name, 0)
                        )
                        self._inflight_by_finder[finder_name] = max(
                            0,
                            current_inflight - 1,
                        )
                        if task_id:
                            self._finder_by_task_id.pop(task_id, None)
                    self._release_unsubmitted(
                        finder, task, f"submit() raised: {submit_exc}"
                    )
                    # Close the remaining race between the stop check above and
                    # TaskRunner.submit(). A stopped runner is expected during
                    # vault teardown; any submission failure while still live
                    # remains a real error and is re-raised.
                    if self._stop.is_set():
                        return submitted_any
                    raise

                if submitted_task_id and submitted_task_id != task_id:
                    with self._lock:
                        if task_id:
                            self._finder_by_task_id.pop(task_id, None)
                        self._finder_by_task_id[submitted_task_id] = finder_name

                self._finder_order_idx = (idx + 1) % finder_count
                logger.debug(
                    "WorkPlanner submitted task id=%s via finder=%s",
                    submitted_task_id,
                    finder_name,
                )
                submitted_any = True

        if not submitted_any:
            self._finder_order_idx = (self._finder_order_idx + 1) % finder_count
        return submitted_any

    def _release_unsubmitted(self, finder, task, reason: str):
        """Hand a task the planner will never submit back to its finder.

        ``find_task()`` has already claimed the batch's picture ids and
        ``on_task_complete()`` is the only thing that discards them, so a task
        dropped between finding and submitting leaks its claims for the life of
        the process and those pictures can never be selected again.

        Args:
            finder: The finder that produced *task* and holds its claims.
            task: The task that will not be submitted.
            reason: Why it was dropped, for the log and the finder's error.
        """
        task_id = getattr(task, "id", None)
        finder_name = finder.finder_name()
        error = RuntimeError(
            f"Task {task_id} was dropped before submission: {reason}",
        )
        try:
            finder.on_task_complete(task, error)
        except Exception as exc:
            logger.warning(
                "Finder %s failed to release the claims of dropped task %s: %s",
                finder_name,
                task_id,
                exc,
            )
        logger.debug(
            "WorkPlanner dropped task id=%s from finder=%s and released its "
            "claims (%s).",
            task_id,
            finder_name,
            reason,
        )
