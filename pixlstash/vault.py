import concurrent
import ctypes
import platform

import datetime
import os
import time
import threading
import numpy as np

from pathlib import Path
from typing import Optional
from concurrent.futures import Future

from sqlmodel import Session, select
from sqlalchemy import func, and_, or_, update


from .database import DBPriority, VaultDatabase
from .db_models import (
    MetaData,
    Character,
    Face,
    Picture,
    PictureSet,
    Snapshot,
    Tag,
    TAG_SENTINEL_LIKE_PATTERN,
    TAG_SENTINEL_ESCAPE_CHAR,
    DESCRIPTION_SENTINEL_LIKE_PATTERN,
    DESCRIPTION_SENTINEL_ESCAPE_CHAR,
    make_description_sentinel,
)
from .pixl_logging import get_logger
from pixlstash.startup_permissions import mkdir_private
from pixlstash.inference.engine import InferenceEngine
from .utils.image_processing.image_utils import ImageUtils
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
from . import worker_config

from pixlstash.event_types import EventType
from pixlstash.utils.service.smart_score_invalidation import InteractiveRescoreRegistry
from pixlstash.tagger_plugins.registry import (
    get_tagger_plugin_manager,
    unload_loaded_tagger_plugins,
)
from pixlstash.services.set_lock_service import (
    enforce_pictures_not_locked,
    locked_picture_id_subquery,
)
from pixlstash.utils.sql_chunking import chunked
from pixlstash.services.scrapheap_service import DEFAULT_RETENTION_DAYS
from pixlstash.services.snapshot_service import SnapshotService
from pixlstash.services.restore import RestoreService
from pixlstash.trusted_sqlite import TrustedSQLiteLocation


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
        disable_background_workers: bool = False,
        force_cpu: bool = False,
        fast_captions: bool = False,
        daily_snapshots_enabled: bool = True,
        insightface_model_pack: str = "buffalo_l",
        scrapheap_retention_days: Optional[int] = DEFAULT_RETENTION_DAYS,
        scrapheap_retention_reduced_at: Optional[datetime.datetime] = None,
    ):
        """
        Initialize a Vault instance.

        Args:
            db_path (str): Path to the SQLite database file.
            image_root (Optional[str]): Path to the image root directory.
            description (Optional[str]): Description of the vault.
            disable_background_workers (bool): When True, the TaskRunner,
                WorkPlanner and ReferenceFolderWatcher are not started and
                no models are ever loaded.  Intended for read-only deployments
                where all processing is already complete.
        """
        registered_hub = getattr(image_root, "hub", None)
        registered_library = getattr(image_root, "library", None)
        self.image_root = str(image_root)
        logger.debug(f"Image root: {self.image_root}")
        assert self.image_root is not None, "image_root cannot be None"
        logger.debug(f"Using image_root: {self.image_root}")
        # Creation only: existing directories keep their modes, whatever they
        # are - the user may deliberately share their image folder with a media
        # server or sync agent. Not makedirs(mode=0o700): since Python 3.7 that
        # mode reaches only the LEAF, so intermediates of a deep new image_root
        # came out 0775 under Ubuntu's umask 002 and the guarded open then
        # refused the namespace the app itself had just created (W21).
        mkdir_private(Path(self.image_root))
        assert os.path.exists(self.image_root), (
            f"Image root path does not exist: {self.image_root}"
        )

        self._db_path = os.path.join(self.image_root, "vault.db")
        if registered_hub is not None and registered_library is not None:
            from pixlstash.hub.bootstrap import (
                finalize_library_connection,
                prevalidate_opened_library,
            )

            # The engine's connect hook legitimately creates WAL/SHM before
            # verification. That is handled inside the guard now, for every
            # caller, rather than by this one site opting out of a directory
            # check the other three still paid for.
            location_guard = TrustedSQLiteLocation.open(
                self._db_path,
                create=not os.path.exists(self._db_path),
            )
            self.db = VaultDatabase(
                self._db_path,
                location_guard=location_guard,
                pre_migration_hook=lambda connection: prevalidate_opened_library(
                    connection, registered_library
                ),
                post_migration_hook=lambda connection: finalize_library_connection(
                    registered_hub, registered_library, connection
                ),
            )
        else:
            self.db = VaultDatabase(self._db_path)
        self.set_description(description or "")

        self.snapshot_service = SnapshotService(self)
        self.restore_service = RestoreService(self)
        # The server's AuthService, attached by ``Server.__init__`` once it has
        # been constructed (it takes ``self.db``, so it cannot exist yet here).
        # The restore path needs it: a full restore replaces the database file,
        # which invalidates every piece of authentication state held in memory
        # (see AuthService.reset_after_restore and §18.5). Stays ``None`` for a
        # Vault built without a Server - tests, the CLI tools - and the restore
        # path treats that as "nothing to reset".
        self.auth_service = None

        self._engine: InferenceEngine | None = None
        self._force_cpu = force_cpu
        self._fast_captions = fast_captions
        self._insightface_model_pack = insightface_model_pack
        self._last_aggressive_unload_at = 0.0
        self._keep_models_in_memory = True
        self._max_vram_gb = None
        self._wd14_tagger_enabled = False
        self._pixlstash_tagger_enabled = True
        self._wd14_threshold = None
        self._pixlstash_tagger_threshold_offset = None
        self._tagger_settings: dict | None = None
        # Single-entry memo for resolve_anomaly_apply_thresholds: (key, resolved_map),
        # keyed on (meta_path, offset, default_threshold) so it self-invalidates when any
        # of those move. Declared before the disable_background_workers early-return so
        # both modes carry it.
        self._anomaly_apply_thresholds_memo: tuple | None = None
        self._server_config_path = server_config_path
        self._disable_background_workers = disable_background_workers
        self._daily_snapshots_enabled: bool = daily_snapshots_enabled
        # Scrapheap auto-purge retention window (days) or None = "Never", plus
        # the instant it was last LOWERED (puts a +1 day floor under EVERY
        # picture's purge deadline, however old). See scrapheap_service.
        # Defaults to None: auto-purge stays off until the user turns it on.
        self._scrapheap_retention_days: Optional[int] = scrapheap_retention_days
        self._scrapheap_retention_reduced_at: Optional[datetime.datetime] = (
            scrapheap_retention_reduced_at
        )

        self._planner_watchers = {}
        self._planner_watchers_lock = threading.Lock()
        self._changed_tags_notify_lock = threading.Lock()
        self._changed_tags_pending_ids: set[int] = set()
        self._changed_tags_flush_timer: threading.Timer | None = None
        self._changed_tags_flush_delay_s = 2.0
        self._event_listeners = []
        self._event_listeners_lock = threading.Lock()
        self._path_mapper = path_mapper
        # Bridges an interactive tag edit that NULLs a smart_score to the immediate,
        # origin-stamped grid refresh emitted when its background rescore lands - so the
        # edited card updates in place instead of waiting for the whole backfill to drain
        # (see _on_task_completed's SmartScoreTask branch). Created before the
        # disable_background_workers early-return because the mutation handlers that
        # record into it run in both modes.
        self.interactive_rescore_registry = InteractiveRescoreRegistry()

        if disable_background_workers:
            logger.info(
                "disable_background_workers=True: TaskRunner, WorkPlanner and "
                "ReferenceFolderWatcher will not be started. No models will be loaded."
            )
            self._task_runner = None
            self._planner_work_finders = {}
            self._work_planner = None
            self._ref_folder_watcher = None
            self._closed = False
            self._started = False
            return

        self._task_runner = TaskRunner(
            name="vault-task-runner",
            num_workers=worker_config.NUM_WORKERS,
            # So a GPU out-of-memory retry can raise a toast instead of only a
            # log line. Bound method, like the finders' notifier below.
            notifier=self.notify,
        )
        self._planner_work_finders = WorkPlanner.work_finders(
            database=self.db,
            engine_getter=lambda: self._engine,
            image_root=self.image_root,
            path_mapper=path_mapper,
            # So a regenerated thumbnail can announce itself. Passed as the bound
            # method rather than the vault, to keep the finders' surface to what
            # they actually use.
            notifier=self.notify,
        )
        from pixlstash.tasks import (
            TaskType,
            EnsureGfsSnapshotFinder,
            ScrapheapRetentionPurgeFinder,
            TagHealthAutoRebuildFinder,
        )

        self._planner_work_finders[TaskType.GFS_SNAPSHOT] = EnsureGfsSnapshotFinder(
            vault=self
        )
        # Needs a full Vault to read the retention settings (and to notify on
        # purge); same reason GFS_SNAPSHOT is registered here rather than in
        # WorkPlanner.work_finders().
        self._planner_work_finders[TaskType.SCRAPHEAP_RETENTION_PURGE] = (
            ScrapheapRetentionPurgeFinder(vault=self)
        )
        # Needs a full Vault (not just `database`) to reach its service
        # layer's vault-facing wrapper (start_rebuild), same reason
        # GFS_SNAPSHOT is registered here rather than in
        # WorkPlanner.work_finders().
        self._planner_work_finders[TaskType.TAG_HEALTH_AUTO_REBUILD] = (
            TagHealthAutoRebuildFinder(vault=self)
        )
        # Needs a full Vault to resolve the tagger's per-label acceptance thresholds
        # (meta.json path + user threshold offset) for the anomaly penalty; same reason
        # as the two finders above.
        from pixlstash.tasks.missing_smart_score_finder import MissingSmartScoreFinder

        self._planner_work_finders[TaskType.SMART_SCORE] = MissingSmartScoreFinder(
            vault=self
        )
        # Repairs the one window the hub/vault split opened: a penalised-tag
        # weight change commits its setting to the hub and its score
        # invalidation to the vault, and those cannot share a transaction. The
        # settings handler records the invalidation durably first and applies it
        # inline; this finder is what completes it if the process died in
        # between. Registered here, like the finders above, because it needs the
        # Vault rather than just the database.
        from pixlstash.tasks.pending_score_invalidation_finder import (
            PendingScoreInvalidationFinder,
        )

        self._planner_work_finders[TaskType.PENDING_SCORE_INVALIDATION] = (
            PendingScoreInvalidationFinder(vault=self)
        )
        # The one finder here that works on the HUB rather than on this vault:
        # a model folder is a fact about the machine, so the checkpoints it
        # registers are shared by every library. Registered only when the vault
        # was opened through a hub registration (a Vault built without one - the
        # CLI tools, most tests - has no hub to reach), and harmless in the
        # multi-library case because only one vault is live at a time.
        if registered_hub is not None:
            from pixlstash.tasks.missing_checkpoint_hash_finder import (
                MissingCheckpointHashFinder,
            )

            self._planner_work_finders[TaskType.CHECKPOINT_HASH] = (
                MissingCheckpointHashFinder(hub=registered_hub)
            )
            # And the workflow library's store is the hub too, so the ComfyUI
            # extraction can file a picture's graph where it outlives the
            # picture (§B3). Replaces the hubless finder work_finders() built:
            # that one still extracts ComfyUI metadata, on the pre-B3
            # `comfyui_models IS NULL` predicate -- it is only the WORKFLOW scan
            # it cannot do, because without a hub there is nowhere to file a
            # graph.
            from pixlstash.tasks.missing_comfyui_extraction_finder import (
                MissingComfyUIExtractionFinder,
            )

            self._planner_work_finders[TaskType.COMFYUI_EXTRACTION] = (
                MissingComfyUIExtractionFinder(
                    database=self.db,
                    image_root=self.image_root,
                    hub=registered_hub,
                )
            )
        self._work_planner = WorkPlanner(
            task_runner=self._task_runner,
            task_finders=self._planner_work_finders,
        )
        self._task_runner.add_task_complete_callback(self._on_task_completed)
        self._task_runner.add_task_complete_callback(
            self._work_planner.on_task_complete
        )

        self._ref_folder_watcher = ReferenceFolderWatcher(
            on_folder_changed=self._on_reference_folder_fs_changed,
        )
        self._closed = False
        self._started = False

    def ensure_ready(self):
        """Initialise the picture tagger so the planner can process work immediately.

        Call this at server startup. Tests that do not need the tagger can skip it;
        tagger init is also triggered lazily by get_worker_future().
        """
        if self._disable_background_workers:
            return
        if not self._engine:
            self._engine = InferenceEngine.create(
                image_root=self.image_root,
                force_cpu=self._force_cpu,
                fast_captions=self._fast_captions,
                max_vram_gb=self._max_vram_gb,
                wd14_enabled=self._wd14_tagger_enabled,
                pixlstash_tagger_enabled=self._pixlstash_tagger_enabled,
                wd14_threshold=self._wd14_threshold,
                pixlstash_tagger_threshold_offset=self._pixlstash_tagger_threshold_offset
                or 0.0,
                keep_models_in_memory=self._keep_models_in_memory,
                insightface_model_pack=self._insightface_model_pack,
                tagger_settings=self._tagger_settings,
            )
            self._bind_engine_services()

    def start(self) -> None:
        """Start background workers.

        Must be called once after configuration is complete (e.g. from
        ``Server.lifespan``).  Idempotent: subsequent calls are no-ops.
        When ``disable_background_workers=True`` this method is a no-op.
        """
        if self._disable_background_workers or self._started:
            return
        self._task_runner.start()
        self._work_planner.start()
        self._ref_folder_watcher.start()
        self._start_existing_folder_watches()
        self._started = True

    def _bind_engine_services(self) -> None:
        """Inject the engine's service instances into registry plugins.

        Built-in tagger plugins (WD14, PixlStash Tagger, Florence-2) have their
        own ``_service`` attribute that stays ``None`` on the registry instance
        because the engine creates its own dedicated service objects.  Binding
        those services here makes ``plugin.is_loaded()`` reflect reality so the
        Settings UI shows the correct loaded state.
        """
        if self._engine is None:
            return
        mgr = get_tagger_plugin_manager()
        bindings = [
            ("wd14", self._engine.wd14_service),
            ("pixlstash_tagger", self._engine.pixlstash_tagger_service),
            ("florence2", self._engine.florence_service),
        ]
        for plugin_name, service in bindings:
            plugin = mgr.get_plugin(plugin_name)
            if (
                plugin is not None
                and service is not None
                and hasattr(plugin, "bind_service")
            ):
                plugin.bind_service(service)

    def emit_event(self, event_type: EventType, data=None):
        """Emit an event to all registered listeners and wake the work planner.

        Alias for ``notify()`` used by the service layer.

        Args:
            event_type: The event type to emit.
            data: Optional data payload.
        """
        self.notify(event_type, data)

    def notify(self, event_type: EventType, data=None):
        """
        Notify all relevant workers for a given event type.

        Example:
            vault.notify(Vault.VaultEventType.NEW_PICTURE)
        """
        # Waking the planner means "there may be work to pick up". A VRAM_OOM is
        # the opposite statement - the GPU is full - and waking on it would send
        # the planner looking for more GPU work three times per failing task, at
        # the one moment there is no room for any.
        if (
            event_type is not EventType.VRAM_OOM
            and self._work_planner
            and self._work_planner.is_running()
        ):
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
        if self._ref_folder_watcher is None:
            return
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

        # The library's own root is watched under the id None, the same key
        # layout_move_service.layout_roots gives it. A rename the owner makes
        # in the laid-out tree is otherwise a row the purge sweep deletes.
        try:
            self._ref_folder_watcher.watch_folder(None, self.image_root)
        except Exception as exc:
            logger.warning(
                "Could not start FS watch for the library root %s: %s",
                self.image_root,
                exc,
            )

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

    def reference_folder_roots(self) -> tuple[str, ...]:
        """Return the roots pictures may legitimately live under besides image_root.

        Each configured reference folder contributes its stored (host-side)
        path and, when a path mapper is configured, its mapped
        (container-side) form: stored ``Picture.file_path`` values are written
        under the mapped form in Docker deployments, while the
        ``reference_folder`` row keeps the host form.

        Returns:
            The root directories, empty when the folder list cannot be read
            (logged) so a caller containing a destructive operation refuses
            rather than guesses.
        """
        from pixlstash.db_models.reference_folder import ReferenceFolder

        def fetch(session: Session) -> list[str]:
            return [
                row.folder
                for row in session.exec(select(ReferenceFolder)).all()
                if row.folder
            ]

        try:
            folders = self.db.run_immediate_read_task(fetch)
        except Exception as exc:
            logger.warning(
                "Could not read the reference folders of %s; treating the set "
                "as empty, so paths outside the image root will be refused: %s",
                self.image_root,
                exc,
            )
            return ()

        roots: list[str] = []
        for folder in folders:
            roots.append(folder)
            if self._path_mapper is not None:
                mapped = self._path_mapper.resolve(folder)
                if mapped != folder:
                    roots.append(mapped)
        return tuple(roots)

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
        if self._ref_folder_watcher is None:
            return
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
        if self._ref_folder_watcher is None:
            return
        self._ref_folder_watcher.unwatch_folder(folder_id)

    def _on_reference_folder_fs_changed(self, folder_id: int | None) -> None:
        """Callback invoked by the filesystem watcher when a relevant file changes.

        Resets ``last_scanned`` to zero so the
        :class:`~pixlstash.tasks.reference_folder_scan_finder.ReferenceFolderScanFinder`
        schedules an immediate rescan, then wakes the work-planner thread.
        ``None`` is the library root, whose schedule the finder keeps itself.
        """
        from pixlstash.db_models.reference_folder import ReferenceFolder

        def reset_last_scanned(session: Session, fid: int) -> None:
            rf = session.get(ReferenceFolder, fid)
            if rf is not None:
                rf.last_scanned = 0.0
                session.commit()

        if folder_id is None:
            finder = self._planner_work_finders.get(TaskType.REFERENCE_FOLDER_SCAN)
            if finder is not None:
                finder.mark_root_due()
        else:
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

    @property
    def is_open(self) -> bool:
        return not self._closed and self.db is not None and self.db.is_open

    def close(self):
        """
        Cleanly close the vault, including stopping background workers and closing DB connection.
        """
        if self._closed:
            return
        self._closed = True
        self.stop()

    def stop(self) -> None:
        """Stop background workers and release all resources.

        Safe to call multiple times; subsequent calls after the first are no-ops.
        Called automatically by ``close()``.
        """
        with self._changed_tags_notify_lock:
            timer = self._changed_tags_flush_timer
            self._changed_tags_flush_timer = None
            self._changed_tags_pending_ids.clear()
        if timer is not None:
            timer.cancel()
        if self._started:
            if self._ref_folder_watcher is not None:
                self._ref_folder_watcher.stop()
            if self._work_planner is not None:
                self._work_planner.stop()
            if self._task_runner is not None:
                self._task_runner.stop()
        if not self._disable_background_workers:
            FaceExtractionTask.release_detection_models()
            ImageEmbeddingTask.release_models()
        if self._engine:
            self._engine.close()
            del self._engine
            self._engine = None
        if self.db:
            self.db.close()
            del self.db
            self.db = None
        self._started = False

    def set_daily_snapshots_enabled(self, enabled: bool) -> None:
        """Enable or disable automatic (GFS) snapshots at runtime.

        This is the master switch for the whole DAILY/WEEKLY/MONTHLY schedule.
        Takes effect immediately; the next EnsureGfsSnapshotFinder cycle will
        skip snapshot creation when ``enabled`` is False.

        Args:
            enabled: True to allow automatic snapshots, False to suppress them.
        """
        self._daily_snapshots_enabled = bool(enabled)

    @property
    def daily_snapshots_enabled(self) -> bool:
        """Whether automatic (GFS) snapshots are enabled."""
        return self._daily_snapshots_enabled

    def set_scrapheap_retention(
        self,
        retention_days: Optional[int],
        reduced_at: Optional[datetime.datetime],
    ) -> None:
        """Set the scrapheap auto-purge window at runtime.

        Takes effect on the next ScrapheapRetentionPurgeFinder cycle. It never
        purges anything synchronously - a config save must not destroy files.

        Args:
            retention_days: Days an UNPROTECTED soft-deleted picture stays in
                the scrapheap, or None for "Never" (auto-purge disabled).
            reduced_at: When the window was last lowered, or None if it never
                was. Puts a floor of ``reduced_at + 1 day`` under every
                picture's deadline, so a lowering never purges anything within
                a day of the save, however old it is.
        """
        self._scrapheap_retention_days = (
            None if retention_days is None else int(retention_days)
        )
        self._scrapheap_retention_reduced_at = reduced_at

    @property
    def scrapheap_retention_days(self) -> Optional[int]:
        """Scrapheap auto-purge window in days; None means "Never"."""
        return self._scrapheap_retention_days

    @property
    def scrapheap_retention_reduced_at(self) -> Optional[datetime.datetime]:
        """When the retention window was last lowered, or None."""
        return self._scrapheap_retention_reduced_at

    @property
    def unprocessable_images(self):
        """Registry of pictures whose image file cannot be decoded (issue #585).

        Lives on the database so task threads (which mark decode failures) and
        finder threads (which suppress them) reach the same instance via their
        ``self._db``; exposed here for discoverability.
        """
        return self.db.unprocessable_images

    def set_keep_models_in_memory(self, keep_models_in_memory: bool):
        previous = self._keep_models_in_memory
        self._keep_models_in_memory = bool(keep_models_in_memory)

        if self._engine:
            self._engine.set_keep_models_in_memory(self._keep_models_in_memory)

        if previous and not self._keep_models_in_memory:
            logger.info(
                "keep_models_in_memory disabled; attempting immediate model unload."
            )
            self._last_aggressive_unload_at = 0.0
            progress = self._build_worker_progress_snapshot()
            self._maybe_aggressive_unload(progress)

    def set_max_vram_usage_gb(self, max_vram_gb: Optional[float]):
        self._max_vram_gb = max_vram_gb
        if self._task_runner is not None:
            self._task_runner.set_max_vram_usage_gb(max_vram_gb)
        if self._engine:
            self._engine.set_max_vram_usage_gb(max_vram_gb)

    def set_tagger_settings(self, settings: dict) -> None:
        """Replace the full tagger plugin settings and propagate to the engine."""

        settings = get_tagger_plugin_manager().fill_defaults(settings)
        self._tagger_settings = settings
        # Keep legacy fields in sync so existing code paths work unchanged.
        plugins = settings.get("plugins", {})
        wd14_cfg = plugins.get("wd14", {})
        pixl_cfg = plugins.get("pixlstash_tagger", {})
        self._wd14_tagger_enabled = bool(wd14_cfg.get("enabled", False))
        self._pixlstash_tagger_enabled = bool(pixl_cfg.get("enabled", False))
        wd14_threshold = wd14_cfg.get("params", {}).get("threshold")
        if wd14_threshold is not None:
            self._wd14_threshold = float(wd14_threshold)
        threshold_offset = pixl_cfg.get("params", {}).get("threshold_offset")
        if threshold_offset is not None:
            self._pixlstash_tagger_threshold_offset = float(threshold_offset)
        if self._engine:
            self._engine.set_tagger_settings(settings)

    def set_wd14_tagger_enabled(self, enabled: bool):
        self._wd14_tagger_enabled = bool(enabled)
        if self._engine:
            self._engine.set_wd14_tagger_enabled(self._wd14_tagger_enabled)

    def set_pixlstash_tagger_enabled(self, enabled: bool):
        self._pixlstash_tagger_enabled = bool(enabled)
        if self._engine:
            self._engine.set_pixlstash_tagger_enabled(self._pixlstash_tagger_enabled)

    def set_wd14_threshold(self, threshold: Optional[float]):
        self._wd14_threshold = threshold
        if self._engine:
            value = threshold if threshold is not None else 0.85
            self._engine.set_wd14_threshold(value)

    def set_pixlstash_tagger_threshold_offset(self, offset: Optional[float]):
        self._pixlstash_tagger_threshold_offset = offset
        if self._engine:
            value = offset if offset is not None else 0.0
            self._engine.set_pixlstash_tagger_threshold_offset(value)

    def get_pixlstash_tagger_threshold_offset(self) -> float:
        return self._pixlstash_tagger_threshold_offset or 0.0

    def get_pixlstash_tagger_meta_path(self):
        if self._engine is None:
            return None
        tagger = self._engine.pixlstash_tagger_service
        return tagger.meta_path if tagger is not None else None

    def get_pixlstash_acceptance_threshold(self) -> float:
        from pixlstash.tagger_plugins.pixlstash_tagger import (
            PIXLSTASH_TAGGER_DEFAULT_THRESHOLD,
        )

        bias = self._pixlstash_tagger_threshold_offset or 0.0
        return max(0.01, float(PIXLSTASH_TAGGER_DEFAULT_THRESHOLD) + bias)

    def retag_picture_interactive(
        self, picture_id: int, engine_name: str | None = None
    ) -> None:
        if self._engine is None:
            return
        from pixlstash.tasks.tag_task import TagTask

        def _fetch_pic(session: Session):
            return session.get(Picture, picture_id)

        pic = self.db.run_immediate_read_task(_fetch_pic)
        if pic is not None:
            task = TagTask(
                database=self.db,
                tagging_workflow=self._engine.tagging_workflow,
                pictures=[pic],
                interactive=True,
                engine_override=engine_name,
            )
            self.submit_task(task)

    def redescribe_picture_interactive(
        self, picture_id: int, engine_name: str | None = None
    ) -> None:
        """Queue an immediate description-generation pass for a single picture.

        Args:
            picture_id: Primary key of the picture to describe.
            engine_name: Optional plugin name to use for this picture.  When
                ``None``, the current ``active_description_plugin`` setting is used.
        """
        if self._engine is None:
            return
        from pixlstash.tasks.description_task import DescriptionTask

        def _fetch_pic(session: Session):
            return session.get(Picture, picture_id)

        pic = self.db.run_immediate_read_task(_fetch_pic)
        if pic is not None:
            task = DescriptionTask(
                database=self.db,
                workflow=self._engine.description_workflow,
                pictures=[pic],
                engine_override=engine_name,
                interactive=True,
            )
            self.submit_task(task)

    def reset_description_interactive(
        self,
        picture_id: int,
        engine_name: str | None = None,
        origin_client_id: str | None = None,
    ) -> bool:
        """Request a fresh description pass for a picture by writing a sentinel.

        Sets ``description`` to a ``__description::<engine>`` sentinel value
        (or ``__description::`` for the default plugin).  The
        :class:`~pixlstash.tasks.missing_description_finder.MissingDescriptionFinder`
        picks up pictures with this sentinel and creates a
        :class:`~pixlstash.tasks.description_task.DescriptionTask` with the
        appropriate engine override.  Using a sentinel instead of ``NULL``
        prevents the background finder from racing with an interactive request
        and substituting the wrong plugin's output.

        Args:
            picture_id: Primary key of the picture to reset.
            engine_name: Optional plugin name to embed in the sentinel.
            origin_client_id: Opaque ``X-Client-Id`` of the originating tab,
                threaded from the route handler so the CHANGED_PICTURES echo is
                recognised as the user's own change (targeted reconcile, no pill)
                instead of being treated as external.

        Returns:
            ``True`` if the picture was found and updated, ``False`` otherwise.
        """
        sentinel = make_description_sentinel(engine_name)

        def _set_sentinel(session: Session) -> bool:
            pic = session.get(Picture, picture_id)
            if pic is None:
                return False
            # Resetting the description overwrites frozen label/curation data with
            # a redescribe sentinel - refuse on a picture in a locked set.
            enforce_pictures_not_locked(
                session, [picture_id], "reset the description of a locked picture"
            )
            pic.description = sentinel
            session.commit()
            return True

        found = self.db.run_task(_set_sentinel)
        if not found:
            return False
        self.notify(
            EventType.CHANGED_PICTURES,
            {
                "picture_ids": [picture_id],
                "origin_client_id": origin_client_id,
                "change_kind": "updated",
            },
        )
        self.redescribe_picture_interactive(picture_id, engine_name=engine_name)
        return True

    def reset_descriptions(
        self,
        picture_ids: list[int],
        engine_name: str | None = None,
        origin_client_id: str | None = None,
    ) -> int:
        """Queue a fresh description pass for many pictures in one write.

        Writes the ``__description::<engine>`` sentinel on every picture in one
        transaction and wakes the planner; ``MissingDescriptionFinder`` then
        captions them in properly sized batches. Unlike
        :meth:`reset_description_interactive` no per-picture task is submitted:
        one urgent single-image task per picture ran the captioner N times at
        batch size 1 while the finder, which cannot tell those sentinels from
        its own, captioned the same pictures a second time (#1162).

        Args:
            picture_ids: Primary keys of the pictures to reset.
            engine_name: Optional plugin name to embed in the sentinel.
            origin_client_id: Opaque ``X-Client-Id`` of the originating tab.

        Returns:
            How many pictures were found and reset.
        """
        ids = sorted({int(pid) for pid in picture_ids})
        if not ids:
            return 0
        sentinel = make_description_sentinel(engine_name)

        def _set_sentinels(session: Session) -> list[int]:
            found: list[int] = []
            # Chunked: "all pictures" can exceed SQLite's bound-parameter cap.
            for chunk in chunked(ids):
                enforce_pictures_not_locked(
                    session, chunk, "reset the description of a locked picture"
                )
                present = list(
                    session.exec(select(Picture.id).where(Picture.id.in_(chunk))).all()
                )
                if not present:
                    continue
                session.exec(
                    update(Picture)
                    .where(Picture.id.in_(present))
                    .values(description=sentinel)
                )
                found.extend(present)
            session.commit()
            return found

        reset_ids = self.db.run_task(_set_sentinels)
        if not reset_ids:
            return 0
        self.notify(
            EventType.CHANGED_PICTURES,
            {
                "picture_ids": reset_ids,
                "origin_client_id": origin_client_id,
                "change_kind": "updated",
            },
        )
        return len(reset_ids)

    def generate_text_embedding(self, query: str) -> Optional[np.ndarray]:
        """
        Generate a text embedding using InferenceEngine.

        Args:
            text (str): Input text to generate embedding for.

        Returns:
            Optional[np.ndarray]: Generated text embedding or None if failed.
        """
        if self._engine is None:
            return None
        embedding = self._engine.text_embedding_workflow.encode_query(query)
        return embedding[0] if embedding is not None and len(embedding) > 0 else None

    def generate_clip_text_embedding(self, query: str) -> Optional[np.ndarray]:
        """
        Generate a CLIP text embedding for the provided query text.
        """
        if self._engine is None:
            return None
        return self._engine.text_embedding_workflow.encode_clip_query(query)

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

    def submit_task(self, task):
        """Submit an in-memory task to the shared task runner."""
        if self._task_runner is None:
            return None
        return self._task_runner.submit(task)

    def _on_task_completed(self, task, error):
        if error is not None or task.status != TaskStatus.COMPLETED:
            return

        result = task.result if isinstance(task.result, dict) else {}

        if task.type == "ReferenceFolderScanTask":
            imported_ids = result.get("imported_picture_ids") or []
            if imported_ids:
                self.notify(EventType.CHANGED_PICTURES, imported_ids)
                self.notify(EventType.PICTURE_IMPORTED, imported_ids)
            # A followed move changed file_path on an existing row: the grid has
            # to refetch it, but nothing was imported, so no PICTURE_IMPORTED.
            moved_ids = result.get("moved_picture_ids") or []
            if moved_ids:
                self.notify(EventType.CHANGED_PICTURES, moved_ids)
            # v1.11 Phase 5: moves this scan could not attribute to PixlStash
            # itself, in a root laid out enough to have something to say about
            # them. The sidebar strip listens for this rather than polling
            # GET /moves/pending.
            queued_for_review = result.get("external_moves_queued_for_review") or []
            if queued_for_review:
                self.notify(EventType.EXTERNAL_MOVES_PENDING, queued_for_review)
            picture_ids = result.get("caption_updated_picture_ids") or []
            if picture_ids:
                self._queue_changed_tags_notification(picture_ids)
            return

        if task.type == "SmartScoreTask":
            changed_count = int(result.get("changed_count") or 0)
            picture_ids = list(task.params.get("picture_ids") or [])
            persisted_ids = [
                int(pid)
                for pid in (result.get("persisted_ids") or [])
                if pid is not None
            ]
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
                # INTERACTIVE path: rescored ids that a user tag edit invalidated get an
                # immediate, origin-stamped CHANGED_PICTURES so the initiating tab
                # reconciles the card in place - independent of the global drain gate
                # below. Only *persisted* ids are consulted, so an id the CAS in
                # _persist_scores skipped (still NULL) is never announced as rescored.
                # Ids that were never registered (background-only rescores, or interactive
                # ids demoted when the registry was full) are left to the bulk drain emit.
                announced_ids: set[int] = set()
                for origin, ids in self.interactive_rescore_registry.consume(
                    persisted_ids
                ).items():
                    announced_ids.update(ids)
                    self.notify(
                        EventType.CHANGED_PICTURES,
                        {
                            "picture_ids": sorted(ids),
                            "fields": ["smart_score"],
                            "origin_client_id": origin,
                        },
                    )
                remaining = int(
                    self.db.run_immediate_read_task(SmartScoreTask.count_remaining) or 0
                )
                if remaining == 0:
                    # Exclude ids already announced origin-stamped above: re-announcing
                    # them origin-less here would raise the "view changed externally" pill
                    # on the very tab that made the edit (a self-pill). Unregistered
                    # ids - genuine background rescores, or interactive ids demoted when
                    # the registry was full - still ride this bulk emit so their cards
                    # refresh; over-suppressing them would strand real external changes.
                    drain_ids = [
                        pid for pid in picture_ids if int(pid) not in announced_ids
                    ]
                    if drain_ids:
                        # Tag the event with the changed field so the SPA only
                        # reloads the grid when it is actually sorting/filtering by
                        # smart_score; under any other sort this change is invisible.
                        self.notify(
                            EventType.CHANGED_PICTURES,
                            {"picture_ids": drain_ids, "fields": ["smart_score"]},
                        )
                    else:
                        logger.debug(
                            "SmartScoreTask drained with all %s rescored id(s) announced "
                            "origin-stamped; no origin-less drain event emitted.",
                            len(announced_ids),
                        )
                else:
                    logger.debug(
                        "SmartScoreTask updated %s pictures; deferring CHANGED_PICTURES event with %s smart scores remaining.",
                        changed_count,
                        remaining,
                    )
            return

        if task.type == "PictureImportTask":
            imported_ids = result.get("imported_picture_ids") or []
            origin_client_id = result.get("origin_client_id") or task.params.get(
                "origin_client_id"
            )
            # Mirror the streaming-upload import: a genuine PixlStash tab carries
            # an origin_client_id (slick in-place insert); an external caller does
            # not (raise the New-pictures pill instead).
            import_source = "ui" if origin_client_id else "external"
            self.notify(
                EventType.CHANGED_PICTURES,
                {
                    "picture_ids": imported_ids,
                    "source": import_source,
                    "origin_client_id": origin_client_id,
                    "change_kind": "added",
                },
            )
            if imported_ids:
                self.notify(
                    EventType.PICTURE_IMPORTED,
                    {
                        "ids": imported_ids,
                        "source": import_source,
                        "origin_client_id": origin_client_id,
                        "change_kind": "added",
                    },
                )
            return

        if task.type == "DetectionTask":
            picture_ids = result.get("picture_ids") or []
            if picture_ids:
                # Attribute to the originating tab and tag the change as the
                # (grid-invisible) "detections" field, so the SPA refreshes
                # detection overlays without raising the external-change pill or
                # reshuffling the grid.
                self.notify(
                    EventType.CHANGED_PICTURES,
                    {
                        "picture_ids": picture_ids,
                        "change_kind": "updated",
                        "fields": ["detections"],
                        "origin_client_id": task.params.get("origin_client_id"),
                    },
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
                # Do NOT fire CHANGED_FACES here: newly-extracted faces always
                # have character_id=None so no character sidebar data changes.
                # _process_pending_character_assignments fires CHANGED_FACES
                # itself only when it actually assigns a face to a character.
                self._process_pending_character_assignments(picture_ids)
            return

        if task.type == "DescriptionTask":
            self._notify_worker_ids_processed(TaskType.DESCRIPTION, changed)
            picture_ids = [pic_id for _, pic_id, _, _ in changed]
            self.notify(
                EventType.CHANGED_DESCRIPTIONS, picture_ids if picture_ids else None
            )
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
            if attr in {"faces", "tags"}:
                try:
                    return len(value or []) > 0, value
                except Exception:
                    # Relationship-length guard: a non-lenable value is simply
                    # "not populated"; (False, value) IS the answer.
                    return False, value
            return value is not None, value

        return self.db.run_task(fetch, priority=DBPriority.IMMEDIATE)

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
        if not self._engine:
            self._engine = InferenceEngine.create(
                image_root=self.image_root,
                force_cpu=self._force_cpu,
                fast_captions=self._fast_captions,
                max_vram_gb=self._max_vram_gb,
                wd14_enabled=self._wd14_tagger_enabled,
                pixlstash_tagger_enabled=self._pixlstash_tagger_enabled,
                wd14_threshold=self._wd14_threshold,
                pixlstash_tagger_threshold_offset=self._pixlstash_tagger_threshold_offset
                or 0.0,
                keep_models_in_memory=self._keep_models_in_memory,
                insightface_model_pack=self._insightface_model_pack,
                tagger_settings=self._tagger_settings,
            )
            self._bind_engine_services()

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
        """Whether *worker_type* will actually be scheduled right now.

        Both conditions matter and used to be conflated: the planner thread has
        to be alive, and a finder for this task type has to still be registered
        with it. Ignoring the argument meant a dead planner and a detached
        finder produced the same answer, and callers reported whichever symptom
        they happened to name.
        """
        if not self._work_planner or not self._work_planner.is_running():
            return False
        return self._work_planner.has_finder(worker_type)

    def worker_unavailable_reason(self, worker_type: TaskType) -> str | None:
        """Why *worker_type* is not schedulable, or None when it is.

        Callers that refuse a request need to say which of the two conditions
        failed; "the worker is not running" reads as a configuration choice and
        has sent every investigation of a dead planner thread down the wrong
        path.
        """
        if not self._work_planner:
            return "the background work planner was never started"
        if not self._work_planner.is_running():
            return (
                "the background work planner thread is not alive (it was "
                "stopped, or it died); restart the server"
            )
        if not self._work_planner.has_finder(worker_type):
            return f"no {worker_type} finder is registered with the work planner"
        return None

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
            # When set by a branch below, this overrides the finder-based
            # activity check (used by user-triggered tasks that have no finder).
            worker_active_override = None
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
            elif worker_type == TaskType.TAG_PREDICTION_BACKFILL:
                missing = int(
                    self.db.run_immediate_read_task(self._count_missing_tag_predictions)
                    or 0
                )
                label = "tag_prediction_backfill"
            elif worker_type == TaskType.QUALITY:
                missing = int(
                    self.db.run_immediate_read_task(self._count_missing_quality) or 0
                )
                label = "quality_scored"
            elif worker_type == TaskType.FACE_EXTRACTION:
                missing = int(
                    self.db.run_immediate_read_task(
                        self._count_missing_feature_extractions
                    )
                    or 0
                )
                label = "faces_extracted"
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
                label = "watch_folder_import"
                active_wf_tasks = (
                    self._task_runner.get_active_tasks_of_type("WatchFolderImportTask")
                    if self._task_runner is not None
                    else []
                )
                if active_wf_tasks:
                    total = sum(
                        int(getattr(t, "_total_candidates", 0)) for t in active_wf_tasks
                    )
                    processed = sum(
                        int(getattr(t, "_processed_count", 0)) for t in active_wf_tasks
                    )
                    missing = max(0, total - processed)
                else:
                    total = 0
                    missing = 0
            elif worker_type == TaskType.PICTURE_IMPORT:
                # User-triggered (no finder): surface live progress straight from
                # the running task(s), mirroring the WATCH_FOLDERS / DETECTION
                # handling so an async streaming import (#459) renders as a task
                # row in the frontend task manager.
                label = "picture_import"
                active_import_tasks = (
                    self._task_runner.get_active_tasks_of_type("PictureImportTask")
                    if self._task_runner is not None
                    else []
                )
                if active_import_tasks:
                    total = sum(
                        int(getattr(t, "_total_count", 0)) for t in active_import_tasks
                    )
                    processed = sum(
                        int(getattr(t, "_processed_count", 0))
                        for t in active_import_tasks
                    )
                    missing = max(0, total - processed)
                    worker_active_override = True
                else:
                    total = 0
                    missing = 0
                    worker_active_override = False
            elif worker_type == TaskType.MODEL_FOLDER_SCAN:
                # User-triggered (no finder): live counters straight off the
                # running task(s), the PICTURE_IMPORT / DETECTION shape. Counted
                # in model files, not pictures - the shelf lives in the hub and
                # the generic library total would be meaningless here.
                label = "model_folder_scan"
                active_scan_tasks = (
                    self._task_runner.get_active_tasks_of_type("ModelFolderScanTask")
                    if self._task_runner is not None
                    else []
                )
                if active_scan_tasks:
                    total = sum(
                        int(getattr(t, "_total_count", 0)) for t in active_scan_tasks
                    )
                    processed = sum(
                        int(getattr(t, "_processed_count", 0))
                        for t in active_scan_tasks
                    )
                    missing = max(0, total - processed)
                    worker_active_override = True
                else:
                    total = 0
                    missing = 0
                    worker_active_override = False
            elif worker_type == TaskType.COMFYUI_EXTRACTION:
                missing = int(
                    self.db.run_immediate_read_task(
                        self._count_missing_comfyui_extraction
                    )
                    or 0
                )
                label = "comfyui_extraction"
            elif worker_type == TaskType.MISSING_FILE_PURGE:
                total = 0
                missing = 0
                label = "missing_file_purge"
            elif worker_type == TaskType.SNAPSHOT_IDENTITY_SCRUB:
                # Counted in snapshot archives, not pictures. This is a one-time
                # migration with a real finish line, so the generic picture total
                # would claim "N / N, nothing remaining" while the scrub still
                # has archives to rewrite: the same misleading shape the dedup
                # scan avoids below, but worse here because the user is waiting
                # for it to end.
                total = int(
                    self.db.run_immediate_read_task(self._count_total_snapshots) or 0
                )
                missing = int(
                    self.db.run_immediate_read_task(self._count_unscrubbed_snapshots)
                    or 0
                )
                label = "snapshot_identity_scrub"
            elif worker_type == TaskType.TEXT_SCORE:
                missing = int(
                    self.db.run_immediate_read_task(self._count_missing_text_score) or 0
                )
                label = "text_score"
            elif worker_type == TaskType.DETECTION:
                # User-triggered (no finder): surface live progress straight from
                # the running task(s), mirroring the WATCH_FOLDERS handling.
                label = "object_detection"
                active_detection_tasks = (
                    self._task_runner.get_active_tasks_of_type("DetectionTask")
                    if self._task_runner is not None
                    else []
                )
                if active_detection_tasks:
                    total = sum(
                        int(getattr(t, "_total_count", 0))
                        for t in active_detection_tasks
                    )
                    processed = sum(
                        int(getattr(t, "_processed_count", 0))
                        for t in active_detection_tasks
                    )
                    missing = max(0, total - processed)
                    worker_active_override = True
                else:
                    total = 0
                    missing = 0
                    worker_active_override = False
            elif worker_type == TaskType.DEDUP_SCAN:
                # A duplicate scan keeps working after it has enumerated every
                # picture: near buckets and embedding components still remain.
                # Use the task's cooperative phase counters instead of the
                # generic library total, which otherwise renders a misleading
                # "N / N" while the active task is still running.
                label = "duplicate_scan"
                active_dedup_tasks = (
                    self._task_runner.get_active_tasks_of_type("DedupScanTask")
                    if self._task_runner is not None
                    else []
                )
                if active_dedup_tasks:
                    snapshots = [task.task_progress() for task in active_dedup_tasks]
                    total = max(1, sum(snapshot[1] for snapshot in snapshots))
                    processed = sum(snapshot[0] for snapshot in snapshots)
                    # The task remains active for a tiny interval after its final
                    # DB slice returns. Never advertise terminal progress until
                    # TaskRunner has actually removed it from the active set.
                    processed = min(processed, total - 1)
                    missing = total - processed
                    worker_active_override = True
                else:
                    total = 0
                    missing = 0
                    worker_active_override = False
            elif worker_type == TaskType.CHECKPOINT_HASH:
                # The one worker here whose denominator is the model shelf and
                # not the picture library. Left in the `planner_managed` default
                # below it inherited the library's picture count as its own
                # total and a hardcoded `missing = 0`, so reading tens of
                # gigabytes off disk for minutes rendered as "N / N, nothing
                # remaining, 0/s" on a row that still said running - which is
                # what made a healthy long task look like a stuck one.
                finder = self._planner_work_finders.get(TaskType.CHECKPOINT_HASH)
                total, missing = finder.progress() if finder is not None else (0, 0)
                label = "checkpoints_hashed"
            elif worker_type == TaskType.ORIENTATION:
                # Without its own count this row fell into `planner_managed`
                # below - `missing = 0`, so it read "12,094 / 12,094, 0.00/s"
                # while 6,462 pictures were still NULL and the finder was
                # working through them 128 at a time; every batch made the
                # row pop back in, apparently finished.
                missing = int(
                    self.db.run_immediate_read_task(self._count_missing_orientations)
                    or 0
                )
                label = "orientations_read"
            elif worker_type == TaskType.THUMBNAIL_GENERATION:
                # Whole-frame bitmap regeneration (MissingThumbnailFinder). After
                # the v1.8.0 upgrade this counts the entire library, which is what
                # the in-app "Upgrading thumbnails" progress bar reads.
                missing = int(
                    self.db.run_immediate_read_task(self._count_missing_thumbnails) or 0
                )
                label = "thumbnails_generated"
            else:
                missing = 0
                label = "planner_managed"
            worker_active = (
                worker_active_override
                if worker_active_override is not None
                else self._is_worker_active(worker_type)
            )
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
    def _count_total_snapshots(session: Session) -> int:
        """Every registered snapshot, scrubbed or not."""
        result = session.exec(select(func.count()).select_from(Snapshot)).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

    @staticmethod
    def _count_unscrubbed_snapshots(session: Session) -> int:
        """Legacy archives the identity scrub still owes work to.

        Mirrors ``MissingSnapshotIdentityScrubFinder._fetch_next``: keyed on
        ``identity_scrubbed_at IS NULL``, which means a pre-hub archive and
        nothing else, because snapshots created since are stamped at creation.
        """
        result = session.exec(
            select(func.count())
            .select_from(Snapshot)
            .where(Snapshot.identity_scrubbed_at.is_(None))
        ).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

    @staticmethod
    def _count_missing_orientations(session: Session) -> int:
        """Mirrors ``OrientationTask.find_pictures_missing_orientation``."""
        result = session.exec(
            select(func.count())
            .select_from(Picture)
            .where(Picture.orientation.is_(None))
            .where(Picture.deleted.is_(False))
            .where(Picture.file_path.is_not(None))
        ).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

    def _count_missing_thumbnails(self, session: Session) -> int:
        """Pictures awaiting whole-frame thumbnail (re)generation.

        Mirrors ``MissingThumbnailFinder._fetch_missing``: keyed on
        ``thumbnail_width IS NULL`` for live, file-backed pictures. Excludes
        undecodable pictures (issue #585) so the "Upgrading thumbnails"
        progress bar can reach 0 instead of stalling on files that can never
        produce a thumbnail.
        """
        suppressed_ids = self.db.unprocessable_images.active_suppressed_ids()
        stmt = (
            select(func.count())
            .select_from(Picture)
            .where(Picture.thumbnail_width.is_(None))
            .where(Picture.deleted.is_(False))
            .where(Picture.file_path.is_not(None))
        )
        if suppressed_ids:
            stmt = stmt.where(Picture.id.notin_(tuple(suppressed_ids)))
        result = session.exec(stmt).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

    @staticmethod
    def _count_missing_descriptions(session: Session) -> int:
        # The same predicate MissingDescriptionFinder selects on: NULL or a
        # ``__description::`` sentinel, and never a picture frozen by a locked
        # set. A reset writes the sentinel, not NULL, so counting NULL alone
        # read "N / N" through an entire regeneration (#1162).
        result = session.exec(
            select(func.count())
            .select_from(Picture)
            .where(
                or_(
                    Picture.description.is_(None),
                    Picture.description.like(
                        DESCRIPTION_SENTINEL_LIKE_PATTERN,
                        escape=DESCRIPTION_SENTINEL_ESCAPE_CHAR,
                    ),
                ),
                ~Picture.id.in_(locked_picture_id_subquery()),
            )
        ).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

    @staticmethod
    def _count_missing_tags(session: Session) -> int:
        has_sentinel = Tag.tag.like(
            TAG_SENTINEL_LIKE_PATTERN, escape=TAG_SENTINEL_ESCAPE_CHAR
        )
        result = session.exec(
            select(func.count())
            .select_from(Picture)
            .where(Picture.tags.any(has_sentinel))
        ).one()
        if isinstance(result, (tuple, list)):
            return result[0]
        return result or 0

    @staticmethod
    def _count_missing_tag_predictions(session: Session) -> int:
        """Count pictures with real tags but no tag-prediction rows.

        These were tagged before predictions were written inline (or by a
        different engine then); the MissingTagPredictionFinder back-fills them.
        """
        has_sentinel = Tag.tag.like(
            TAG_SENTINEL_LIKE_PATTERN, escape=TAG_SENTINEL_ESCAPE_CHAR
        )
        has_real_tag = Picture.tags.any(and_(Tag.tag.is_not(None), ~has_sentinel))
        no_prediction = ~Picture.tag_predictions.any()
        result = session.exec(
            select(func.count())
            .select_from(Picture)
            .where(has_real_tag, no_prediction, Picture.deleted.is_(False))
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

    def _count_missing_image_embeddings(self, session: Session) -> int:
        # Exclude undecodable pictures (issue #585) so "remaining" can reach 0
        # instead of stalling on files that can never produce an embedding.
        suppressed_ids = self.db.unprocessable_images.active_suppressed_ids()
        return ImageEmbeddingTask.count_remaining(
            session, suppressed_ids=suppressed_ids
        )

    @staticmethod
    def _count_pending_likeness_parameters(session: Session) -> int:
        return LikenessParameterUtils.count_pending_parameters(session)

    @staticmethod
    def _count_missing_text_score(session: Session) -> int:
        from pixlstash.tasks.text_score_task import TextScoreTask

        return TextScoreTask.count_missing_text_score(session)

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
        if not self._engine:
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
        # The snapshot only sees planner in-flight counts. A task submitted
        # straight to the runner (an interactive retag or redescribe) is
        # invisible to it, and unloading the model under a running caption
        # batch makes the captioner return nothing for every picture in it.
        # GPU-queue tasks only: a dedup scan or an import holds no model.
        if self._task_runner is not None and self._task_runner.has_active_gpu_tasks():
            return

        logger.warning("All workers idle; aggressively unloading models.")
        try:
            self._engine.aggressive_unload()
        except Exception as exc:
            logger.warning("Aggressive unload failed for InferenceEngine: %s", exc)
        # The engine unloads the services it owns by name; a tagger plugin's
        # model is reachable only through the registry, and until this it was
        # not reachable at all - a plugin captioner stayed resident for the
        # life of the process and this setting could not free it (#967).
        try:
            unload_loaded_tagger_plugins()
        except Exception as exc:
            logger.warning("Aggressive unload failed for tagger plugins: %s", exc)
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
