import sys
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pixlstash.pixl_logging import get_logger
from pixlstash.task_runner import TaskCancelledError

if TYPE_CHECKING:
    from pixlstash.tasks.task_type import TaskType


logger = get_logger(__name__)


@dataclass
class _PassStats:
    """One finder's burst, from its first submit until the drain fires."""

    started_at: float
    pictures: int = 0
    tasks: int = 0
    gpu_util_sum: float = 0.0
    gpu_samples: int = 0


class _PlannerStopping(Exception):
    """Raised inside a finder's turn when shutdown began part-way through it.

    Distinguishes "abandon the whole cycle" from the finder failures the cycle
    is meant to survive, which are logged and skipped so the sweep continues.
    """


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
        notifier=None,
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
        from pixlstash.tasks.missing_orientation_finder import MissingOrientationFinder
        from pixlstash.tasks.dedup_scan_finder import DedupScanFinder
        from pixlstash.tasks.missing_stack_cohesion_finder import (
            MissingStackCohesionFinder,
        )
        from pixlstash.tasks.layout_move_finder import LayoutMoveFinder

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
            # Re-registered with a hub in `vault.py` when this vault was opened
            # through a hub registration, so it can also file workflows; without
            # one it keeps its pre-B3 predicate. Same reason GFS_SNAPSHOT and
            # CHECKPOINT_HASH are registered there rather than here.
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
                notifier=notifier,
            ),
            TaskType.PIXEL_SHA: MissingPixelShaFinder(
                database=database,
            ),
            TaskType.ORIENTATION: MissingOrientationFinder(
                database=database,
            ),
            TaskType.DEDUP_SCAN: DedupScanFinder(
                database=database,
            ),
            TaskType.STACK_COHESION: MissingStackCohesionFinder(
                database=database,
            ),
            TaskType.LAYOUT_MOVE: LayoutMoveFinder(
                database=database,
                notifier=notifier,
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
        # Armed when a finder's task is submitted, disarmed by whichever edge
        # first sees the finder both exhausted and idle. Without it,
        # on_all_tasks_complete() was announced only from the task-completion
        # edge and was missed entirely whenever the last task finished before
        # the finder reported that it had run out of work.
        self._all_done_pending: dict[str, bool] = {}
        # Per-finder burst accounting for the [PIPELINE_PASS] line, keyed by
        # finder name; created on the first submit, popped when the drain fires.
        self._pass_stats: dict[str, _PassStats] = {}
        # When a GPU finder's queue last ran dry mid-pass, so the refill can say
        # how long the stall actually was - the warning above only marks its
        # start, and "may be idle" is not a number anyone can act on.
        self._stalled_since: dict[str, float] = {}
        self._gpu_util_unavailable = False
        self._gpu_util_torch_missing_logged = False
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

        The `_finder_name_by_task_type` entry is deliberately kept: it is how
        `depends_on()` resolves a TaskType to the name `_finder_exhausted` is
        keyed by. Popping it made the exhausted flag written below unreachable,
        so the dependent finder blocked for ever anyway.
        """
        removed = set()
        with self._lock:
            for task_type in task_types:
                name = self._finder_name_by_task_type.get(task_type)
                if not name:
                    continue
                finder = self._task_finders_by_name.pop(name, None)
                if finder is None:
                    continue
                self._task_finders = [f for f in self._task_finders if f is not finder]
                self._finder_exhausted[name] = True
                self._pass_stats.pop(name, None)
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
        with self._lock:
            finder_name = self._finder_by_task_id.pop(getattr(task, "id", None), None)
            if finder_name:
                inflight_count = int(self._inflight_by_finder.get(finder_name, 0))
                new_inflight = max(0, inflight_count - 1)
                self._inflight_by_finder[finder_name] = new_inflight
                is_exhausted = self._finder_exhausted.get(finder_name, False)
                stats = self._pass_stats.get(finder_name)
                if stats is not None:
                    stats.tasks += 1
                    if error is None:
                        picture_ids = (getattr(task, "params", None) or {}).get(
                            "picture_ids"
                        ) or []
                        stats.pictures += len(picture_ids)
        if finder_name:
            _GPU_FINDERS = {"MissingTagFinder", "MissingFaceExtractionFinder"}
            if new_inflight == 0 and not is_exhausted and finder_name in _GPU_FINDERS:
                logger.warning(
                    "[PIPELINE_STALL] %s inflight count hit 0 while work remains; "
                    "GPU may be idle until next finder cycle.",
                    finder_name,
                )
                with self._lock:
                    self._stalled_since[finder_name] = time.perf_counter()
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
                # Re-read the state under the lock rather than trusting the
                # value computed at the top: releasing the claims above is what
                # makes this finder's pictures selectable again, so the loop
                # thread can have taken a new task in between and the drain
                # callback (a GPU session teardown for MissingTagFinder) would
                # land on it.
                if self._claim_drain(finder_name):
                    self._notify_all_tasks_complete(finder, finder_name)
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

            self._sample_gpu_busy()
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
            try:
                submitted_any = self._sweep_finder(
                    finder, idx, finder_count, submitted_any
                )
            except _PlannerStopping:
                return submitted_any
            except Exception:
                # A finder that raises must cost only its own turn. Catching
                # this around the whole cycle instead abandoned the rest of the
                # sweep, and _finder_order_idx advances only on a successful
                # submit or in the tail below, so it stayed parked on the raiser
                # and every finder behind it stopped being swept for the life of
                # the process while is_running() went on reporting healthy.
                logger.exception(
                    "WorkPlanner finder %s raised; skipping it for this cycle "
                    "and continuing with the rest.",
                    type(finder).__name__,
                )

        if not submitted_any:
            with self._lock:
                self._finder_order_idx = (self._finder_order_idx + 1) % finder_count
        return submitted_any

    def _sweep_finder(
        self, finder, idx: int, finder_count: int, submitted_any: bool
    ) -> bool:
        """Give one finder its turn and return the updated *submitted_any*.

        Raises:
            _PlannerStopping: Shutdown began mid-turn; the caller must abandon
                the whole cycle rather than move on to the next finder.
        """
        finder_name = finder.finder_name()
        max_inflight = max(1, int(finder.max_inflight_tasks()))

        blocking_finders = finder.depends_on()
        if blocking_finders:
            with self._lock:
                blocking_names = [
                    self._finder_name_by_task_type.get(tt, str(tt))
                    for tt in blocking_finders
                ]
                if any(
                    self._inflight_by_finder.get(name, 0) > 0
                    or not self._finder_exhausted.get(name, False)
                    for name in blocking_names
                ):
                    return submitted_any

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
                # The other edge into "exhausted and idle". When the last task
                # completed before the finder ran out of work, the completion
                # edge saw exhausted=False and nobody ever announced the drain,
                # so the GPU arena stayed held until a later full cycle.
                if self._claim_drain(finder_name):
                    self._notify_all_tasks_complete(finder, finder_name)
                break
            # A finder may block in database or filesystem work long enough
            # for stop()'s bounded join to return. Do not submit the task it
            # found after shutdown has begun and the runner may already be
            # stopped by Vault.stop().
            if self._stop.is_set():
                self._release_unsubmitted(
                    finder, task, "the planner stopped before it could submit"
                )
                raise _PlannerStopping

            task_id = getattr(task, "id", None)
            with self._lock:
                current_inflight = int(self._inflight_by_finder.get(finder_name, 0))
                self._inflight_by_finder[finder_name] = current_inflight + 1
                self._finder_exhausted[finder_name] = False
                # Armed *before* submit, because a runner that completes the
                # task synchronously calls back before submit() returns and the
                # flag has to be visible by then. Captured so the failure path
                # can put it back: see the rollback below.
                previous_all_done_pending = self._all_done_pending.get(
                    finder_name, False
                )
                self._all_done_pending[finder_name] = True
                if finder_name not in self._pass_stats:
                    self._pass_stats[finder_name] = _PassStats(
                        started_at=time.perf_counter()
                    )
                stalled_since = self._stalled_since.pop(finder_name, None)
            if stalled_since is not None:
                logger.warning(
                    "[PIPELINE_STALL] %s refilled after %.2fs",
                    finder_name,
                    time.perf_counter() - stalled_since,
                )
            with self._lock:
                if task_id:
                    self._finder_by_task_id[task_id] = finder_name

            try:
                submitted_task_id = self._task_runner.submit(task)
            except Exception as submit_exc:
                with self._lock:
                    current_inflight = int(self._inflight_by_finder.get(finder_name, 0))
                    self._inflight_by_finder[finder_name] = max(
                        0,
                        current_inflight - 1,
                    )
                    # Disarm, or leave it as it was. Nothing was submitted, so
                    # this burst never earned an `on_all_tasks_complete()`; a
                    # flag left armed here is claimed later by the first
                    # `find_task() -> None`, firing the callback for work that
                    # never ran. Restoring rather than clearing keeps a burst
                    # that was already legitimately armed by an earlier task.
                    self._all_done_pending[finder_name] = previous_all_done_pending
                    if not previous_all_done_pending:
                        self._pass_stats.pop(finder_name, None)
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
                    raise _PlannerStopping from submit_exc
                raise

            if submitted_task_id and submitted_task_id != task_id:
                with self._lock:
                    if task_id:
                        self._finder_by_task_id.pop(task_id, None)
                    self._finder_by_task_id[submitted_task_id] = finder_name

            with self._lock:
                self._finder_order_idx = (idx + 1) % finder_count
            logger.debug(
                "WorkPlanner submitted task id=%s via finder=%s",
                submitted_task_id,
                finder_name,
            )
            submitted_any = True

        return submitted_any

    def _claim_drain(self, finder_name: str) -> bool:
        """Whether this caller owns the finder's ``on_all_tasks_complete()`` call.

        True at most once per burst of work: armed when a task is submitted,
        and claimed by whichever of the two edges -- the last task completing,
        or the finder reporting no more work -- first observes the finder both
        exhausted and idle. The whole decision is made under ``_lock`` so the
        two edges cannot both fire it.
        """
        with self._lock:
            if not self._all_done_pending.get(finder_name, False):
                return False
            if int(self._inflight_by_finder.get(finder_name, 0)) != 0:
                return False
            if not self._finder_exhausted.get(finder_name, False):
                return False
            self._all_done_pending[finder_name] = False
            return True

    def _sample_gpu_busy(self) -> None:
        """Add one GPU-utilisation sample to every burst currently in flight.

        Runs once per planner cycle, so it costs one NVML query while any
        finder has work out and nothing at all when the planner is idle. torch
        is looked up in ``sys.modules`` rather than imported: it is kept off
        the boot path on purpose, and a GPU that no engine has touched yet has
        nothing worth sampling.
        """
        with self._lock:
            active = list(self._pass_stats.values())
        if not active or self._gpu_util_unavailable:
            return
        torch = sys.modules.get("torch")
        if torch is None:
            # Not sticky: torch arrives with the first model load, and a burst
            # that started before it simply has fewer samples.
            if not self._gpu_util_torch_missing_logged:
                self._gpu_util_torch_missing_logged = True
                logger.info(
                    "[PIPELINE_PASS] GPU-busy sampling waiting for torch to be "
                    "imported (no model loaded yet); bursts until then read n/a"
                )
            return
        try:
            if not torch.cuda.is_available():
                self._gpu_util_unavailable = True
                logger.info(
                    "[PIPELINE_PASS] GPU-busy sampling unavailable, reporting n/a: "
                    "torch.cuda.is_available() is False in the planner thread"
                )
                return
            utilization = float(torch.cuda.utilization())
        except Exception as exc:
            # ModuleNotFoundError when pynvml is not installed, or an NVML
            # error; either way the fraction is reported as n/a for the run.
            self._gpu_util_unavailable = True
            logger.info(
                "[PIPELINE_PASS] GPU-busy sampling unavailable, reporting n/a: %s: %s",
                type(exc).__name__,
                exc,
            )
            return
        with self._lock:
            for stats in active:
                stats.gpu_util_sum += utilization
                stats.gpu_samples += 1

    def _log_pipeline_pass(self, finder_name: str) -> None:
        with self._lock:
            stats = self._pass_stats.pop(finder_name, None)
        if stats is None:
            return
        wall_s = time.perf_counter() - stats.started_at
        gpu_busy = (
            f"{stats.gpu_util_sum / stats.gpu_samples / 100.0:.2f}"
            if stats.gpu_samples
            else "n/a"
        )
        logger.info(
            "[PIPELINE_PASS] finder=%s pictures=%d tasks=%d wall_s=%.1f "
            "img_per_s=%.1f gpu_busy=%s gpu_samples=%d",
            finder_name,
            stats.pictures,
            stats.tasks,
            wall_s,
            stats.pictures / wall_s if wall_s > 0 else 0.0,
            gpu_busy,
            stats.gpu_samples,
        )

    def _notify_all_tasks_complete(self, finder, finder_name: str):
        self._log_pipeline_pass(finder_name)
        try:
            finder.on_all_tasks_complete()
        except Exception as exc:
            logger.warning(
                "Finder all-complete callback failed for %s: %s",
                finder_name,
                exc,
            )

    def _release_unsubmitted(self, finder, task, reason: str):
        """Hand a task the planner will never submit back to its finder.

        ``find_task()`` has already claimed the batch's picture ids and
        ``on_task_complete()`` is the only thing that discards them, so a task
        dropped between finding and submitting leaks its claims for the life of
        the process and those pictures can never be selected again.

        The error is a ``TaskCancelledError`` rather than a bare
        ``RuntimeError`` because a finder that records permanent state on
        failure must be able to tell "this work was attempted and failed" from
        "this work never ran". The task did not run here, so deferring its rows
        for the rest of the session would strand them over a plain shutdown.

        Args:
            finder: The finder that produced *task* and holds its claims.
            task: The task that will not be submitted.
            reason: Why it was dropped, for the log and the finder's error.
        """
        task_id = getattr(task, "id", None)
        finder_name = finder.finder_name()
        error = TaskCancelledError(
            f"Task {task_id} was dropped before submission: {reason}",
        )
        released = True
        try:
            finder.on_task_complete(task, error)
        except Exception as exc:
            released = False
            logger.warning(
                "Finder %s failed to release the claims of dropped task %s: %s",
                finder_name,
                task_id,
                exc,
            )
        # Two messages, because one that always claimed the claims were released
        # would be at its most misleading in the one failure this helper exists
        # to surface: a finder whose `on_task_complete` raised still holds the
        # ids, and those pictures are refused until the process restarts.
        if released:
            logger.debug(
                "WorkPlanner dropped task id=%s from finder=%s and released its "
                "claims (%s).",
                task_id,
                finder_name,
                reason,
            )
        else:
            logger.debug(
                "WorkPlanner dropped task id=%s from finder=%s (%s); its claims "
                "were NOT released - see the warning above.",
                task_id,
                finder_name,
                reason,
            )
