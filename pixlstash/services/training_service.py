"""Durable single-worker orchestrator that turns queued jobs into LoRAs.

The :class:`TrainingService` is the layer between PixlStash's data model and the
trainer adapter (:class:`~pixlstash.training.local_ai_toolkit.LocalAiToolkit`).
It owns the job lifecycle: pick the oldest queued training run, snapshot
its dataset, wait for the VRAM arbiter's lease, drive the adapter (``prepare`` → ``submit`` →
poll → finalize), and hand the finished run to the model shelf when it completes.
Exactly one job runs at a time because a single GPU is the binding constraint.

It is built with **dependency injection** so the whole thing is testable without
a GPU (or even a full Vault): every external collaborator is a callable handed
to the constructor: the VRAM lease request, the run importer, the event emitter,
the owner-config reader, the dataset builder, and the trainer-backend factory. The default backend
factory builds a real :class:`LocalAiToolkit`; tests pass one pointed at a CPU
stub.

Shutdown policy (important): :meth:`stop` does **not** terminate a running
external trainer. The trainer runs in its own process group and is expected to
survive a PixlStash restart; :meth:`stop` only halts the poll thread and joins
it with a timeout. On the next startup, :meth:`reconcile_on_startup` re-attaches
to any still-alive run (asking for its lease again and resuming polling) so a
multi-hour run is never killed by a server bounce. The only thing that stops a
run is an explicit :meth:`cancel` (a user action).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from pixlstash.event_types import EventType
from pixlstash.hub import dataset_snapshots, training_runs
from pixlstash.hub.db import HubDatabase, now_iso
from pixlstash.pixl_logging import get_logger
from pixlstash.resources.aitoolkit_tenant import AiToolkitProbe
from pixlstash.resources.arbiter import Lease
from pixlstash.resources.ledger import ledger
from pixlstash.resources.ledger import ram as read_ram
from pixlstash.services.model_folder_scanner import SOURCE_FOLDER_KIND
from pixlstash.training import ai_toolkit_install, live_view, measurements, presets
from pixlstash.training.backend import TrainerBackend, TrainingProgress, TrainingSpec
from pixlstash.training.config_render import deep_merge
from pixlstash.training.local_ai_toolkit import LocalAiToolkit

logger = get_logger(__name__)

# Statuses that mean the job is finished and must not be re-polled / re-run.
_TERMINAL_JOB_STATUSES = frozenset({"completed", "stopped", "error"})
# Adapter progress statuses that mean the external process has reached an end
# state; the poll loop exits once the process is dead AND status is terminal.
_TERMINAL_PROGRESS_STATUSES = frozenset({"completed", "stopped", "error"})
# Cap on the trainer log tail we persist onto the job row.
_LOG_TAIL_BYTES = 8192

#: How long a run adopted at start-up keeps its lease exclusive and unshrunk:
#: PixlStash has not watched it reach its peak since the restart.
ADOPT_QUIET_S = 120.0
#: Updates a probe trains: enough for a warm speed after the first.
PROBE_STEPS = 6
#: The most probes one search runs; a measured profile costs none.
MAX_PROBES = 3
#: A probe is stopped when the machine's available RAM falls below this.
RAM_GUARD_MB = 1024
#: How long a dead trainer's processes get to give their GPU memory back.
RELEASE_GRACE_S = 10.0
# How often a probe or an after-training preview pass is polled.
_SHORT_POLL_S = 1.0
# A checkpoint's step: ``<stem>_<step:09d>.safetensors``.
_CHECKPOINT_STEP_RE = re.compile(r"_(\d{4,})\.safetensors$")


@dataclass
class _Watch:
    """What one poll loop has seen of a trainer.

    Attributes:
        peaks: ``{phase: {vram_mb, ram_mb}}``, the highest figures per phase.
        seconds: Seconds spent per phase, between polls.
        pids: Every process of the trainer's tree seen, so the ones left
            holding memory after it exits can be found.
        from_start: Whether the loop saw the trainer start (a run re-attached
            after a restart did not, so its setup time is unknown).
        shrunk: Whether the run's lease has been lowered to its peak.
        offsets: Where each of the trainer's allocator peak files was read to.
    """

    peaks: dict = field(default_factory=dict)
    seconds: dict = field(default_factory=dict)
    pids: set = field(default_factory=set)
    from_start: bool = True
    shrunk: bool = False
    offsets: dict = field(default_factory=dict)
    _last: tuple | None = None

    def see(self, phase: str | None, usage: dict, now: float) -> None:
        if self._last is not None and self._last[0] is not None:
            self.seconds[self._last[0]] = self.seconds.get(self._last[0], 0.0) + (
                now - self._last[1]
            )
        self._last = (phase, now)
        self.pids |= usage["pids"]
        measurements.note_peak(
            self.peaks, phase, usage.get("peak_vram_mb"), usage["ram_mb"]
        )

    @property
    def phase(self) -> str | None:
        """The phase the last poll saw."""
        return self._last[0] if self._last is not None else None

    def peak_vram_mb(self) -> int | None:
        figures = [e["vram_mb"] for e in self.peaks.values() if e["vram_mb"]]
        return max(figures) if figures else None


def handle_for_run(training_root: str, info: dict, backend=None):
    """Reconstruct a run's :class:`TrainingHandle` from its persisted fields.

    The work dir is ``<training_root>/<uuid>``. The persisted
    ``training_folder`` is the shelf source folder for a run started since the
    shelf took runs, or the legacy ``<work_dir>/output`` for an older one; only
    the former is passed on, so an older run keeps the save root it was started
    with. The adapter's ``handle_for`` is the source of truth for the paths.

    Args:
        training_root: ``<image_root>/training``.
        info: Persisted job fields (uuid, name, pid, training_folder).
        backend: A backend whose ``handle_for`` to use; None uses
            :class:`LocalAiToolkit`'s.

    Returns:
        A reconstructed :class:`TrainingHandle`.
    """
    work_dir = os.path.join(training_root, info["uuid"])
    stored = info.get("training_folder")
    shared = None
    if stored and os.path.normcase(os.path.abspath(stored)) != os.path.normcase(
        os.path.join(os.path.abspath(work_dir), "output")
    ):
        shared = stored
    handle_for = getattr(backend, "handle_for", None)
    if not callable(handle_for):
        handle_for = LocalAiToolkit.handle_for
    return handle_for(
        work_dir,
        info["uuid"],
        info["name"],
        info.get("pid"),
        training_folder=shared,
    )


class TrainingService:
    """Drive queued training runs to finished LoRAs on the shelf.

    One background thread runs at a time and processes queued jobs FIFO (oldest
    ``created_at`` first). See the module docstring for the dependency-injection
    rationale and the shutdown/reconcile policy.

    Attributes:
        poll_interval_s: Seconds between progress polls of a running job.
    """

    def __init__(
        self,
        hub: HubDatabase | None,
        library_uuid: str | None,
        image_root: str,
        *,
        request_gpu: Callable[[str, str, int, int], Lease],
        import_run: Callable[[str, dict], dict],
        notify: Callable[[EventType, dict], None],
        config_provider: Callable[[], dict],
        dataset_builder: Callable[..., dict],
        backend_factory: Callable[[str, str], TrainerBackend] | None = None,
        poll_interval_s: float = 5.0,
        disable_background_workers: bool = False,
        register_source_folder: Callable[[str], None] | None = None,
        adopt_gpu: Callable[[str, str, int, int], Lease] | None = None,
        tenant: AiToolkitProbe | None = None,
    ) -> None:
        """Initialise the service.

        Args:
            hub: The open hub, which holds the ``training_run`` rows. None when
                no hub is open: the service then never starts and refuses new
                runs.
            library_uuid: The library this service trains for. Every run it
                reads or writes is scoped to it, so another library's queue is
                never claimed here.
            image_root: Vault root. Per-job work dirs live in
                ``<image_root>/training/<job_uuid>/``.
            request_gpu: Asks the VRAM arbiter for a run's memory, called as
                ``request_gpu(label, job_uuid, vram_mb, ram_mb)`` with the
                needs of the run's resolved memory profile; returns the
                arbiter's :class:`~pixlstash.resources.arbiter.Lease`, granted
                or waiting.
            import_run: Brings a completed run onto the model shelf, called as
                ``import_run(run_dir, job_info)`` where ``run_dir`` is the
                ai-toolkit run folder holding the checkpoints. Returns a dict
                describing what landed. Raising marks the job ``error``.
            notify: Emits an event (``event_type, data``) to the UI/listeners.
            config_provider: Returns the owner's ai-toolkit config as
                ``{"repo_path", "python"}``.
            dataset_builder: Builds the dataset snapshot; called as
                ``dataset_builder(dest_dir=..., set_id=..., character_id=...,
                trigger_word=...)`` and returns a manifest with ``image_count``
                and ``picture_ids``.
            backend_factory: Builds a :class:`TrainerBackend` from
                ``(repo_path, python)``. Defaults to constructing a real
                :class:`LocalAiToolkit`.
            poll_interval_s: Seconds between progress polls of a running job.
            disable_background_workers: When True, :meth:`start` is a no-op
                (mirrors the Vault flag for read-only deployments).
            register_source_folder: Adds a folder to the model shelf as its
                ``source`` (ai-toolkit output) folder, called as
                ``register_source_folder(path)``. Goes through the same
                registration the folder routes use. Called at most once, when a
                job starts and the shelf has no source folder yet. ``None``
                means a job that needs one fails with a clear error.
            adopt_gpu: Grants a live run found at start-up its memory at once,
                called like ``request_gpu``
                (:meth:`~pixlstash.resources.arbiter.ResourceArbiter.adopt`).
                None asks with ``request_gpu`` instead.
            tenant: The arbiter's AI-toolkit probe, told which processes are
                the trainer and which held memory after it exited. None tells
                nobody.
        """
        self._hub = hub
        self._library_uuid = library_uuid
        self._image_root = image_root
        self._request_gpu = request_gpu
        self._import_run = import_run
        self._notify = notify
        self._config_provider = config_provider
        self._dataset_builder = dataset_builder
        self._backend_factory = backend_factory or (
            lambda repo_path, python: LocalAiToolkit(repo_path, python)
        )
        self.poll_interval_s = poll_interval_s
        self._disable_background_workers = disable_background_workers
        self._register_source_folder = register_source_folder
        self._adopt_gpu = adopt_gpu
        self._tenant = tenant
        # Leases granted at start-up to runs that outlived the restart, by job
        # uuid, until reconcile_on_startup resumes watching them.
        self._adopted: dict[str, Lease] = {}
        # Runs whose remaining after-training previews were cancelled.
        self._skip_previews: set[str] = set()

        # Per-job work dirs (job.db, the rendered config, the pending sidecar).
        # The run itself lives in the shelf's source folder, the dataset under
        # the checkout's datasets/ folder.
        self._training_root = os.path.join(image_root, "training")

        # Per-job spec extras (template_path / config_overrides) that have no
        # dedicated columns. Keyed by job uuid; populated by enqueue, consumed
        # by the worker. Persisted to a per-job sidecar so a restart can recover
        # them when re-running a job that was still queued.
        self._pending_specs: dict[str, dict] = {}
        self._pending_specs_lock = threading.Lock()

        # Single worker thread + its lifecycle primitives.
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        # The adapter currently driving a job, so cancel() can reach it. Set
        # while a job is running, cleared in the worker's finally.
        self._active_backend: TrainerBackend | None = None
        self._active_job_uuid: str | None = None
        # The lease a claimed job is still waiting on, so cancel() can drop it.
        self._waiting: tuple[str, Lease] | None = None
        self._active_lock = threading.Lock()
        # Each run's phase log as last written, so a poll that stays in one
        # phase writes nothing; loaded from the row the first time it is needed.
        self._phase_logs: dict[str, list[dict]] = {}
        self._phase_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the single worker thread. No-op if workers are disabled."""
        if self._disable_background_workers:
            logger.info("TrainingService: background workers disabled; not starting.")
            return
        if self._hub is None or not self._library_uuid:
            logger.warning(
                "TrainingService: no hub or library open; training is unavailable."
            )
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._wake.clear()
        self._thread = threading.Thread(
            target=self._run, name="TrainingService", daemon=True
        )
        self._thread.start()
        logger.info("TrainingService worker started.")

    def stop(self) -> None:
        """Stop the poll thread and join it with a timeout.

        Does NOT terminate a running external trainer (see the module
        docstring): the process is left running and re-attached on the next
        :meth:`reconcile_on_startup`. Safe to call multiple times.
        """
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=10.0)
            if thread.is_alive():
                logger.warning(
                    "TrainingService worker did not stop within timeout; the poll "
                    "loop will exit at its next iteration. The external trainer "
                    "(if any) is intentionally left running for reconcile."
                )
        self._thread = None
        logger.info("TrainingService worker stopped (external trainer left running).")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(
        self,
        *,
        set_id: int | None = None,
        character_id: int | None = None,
        name: str,
        base_model: str,
        trigger_word: str,
        template_path: str,
        config_overrides: dict | None = None,
        locked: dict | None = None,
        resolution: dict | None = None,
        pins: dict | None = None,
    ) -> dict:
        """Create a queued training run and wake the worker.

        At least one of ``set_id`` / ``character_id`` must be given. Both
        mean a character's reference set: the pictures come from the set and
        the run is filed under the character (the route checks the pairing).
        The job is persisted with a fresh uuid and ``status="queued"``; the
        worker picks it up FIFO.

        Args:
            set_id: Picture-set to train on.
            character_id: Character to train on, or to file a reference-set
                run under.
            name: Human-readable job name.
            base_model: Base checkpoint the LoRA trains against.
            trigger_word: Caption trigger word baked into the dataset.
            template_path: Path to the golden ai-toolkit config template.
            config_overrides: The run's tunable values as a config patch
                (kept in the pending spec, not a column).
            locked: The recipe's locked keys as a config patch (pending spec).
            resolution: The resolver's result
                (:func:`pixlstash.training.presets.resolve`). Recorded on the
                row with its ``preset``; its ``vram_mb`` / ``ram_mb`` are what
                the run asks the arbiter for.
            pins: The owner's Advanced values, recorded on the row.

        Returns:
            The persisted run, as the hub row (a dict of its columns).

        Raises:
            ValueError: If neither ``set_id`` nor ``character_id`` is given.
            RuntimeError: No hub or library is open to hold the run.
        """
        if set_id is None and character_id is None:
            raise ValueError("A training run needs a set_id or a character_id.")
        hub, library_uuid = self._store()

        job_uuid = uuid.uuid4().hex
        run_id = training_runs.create_run(
            hub,
            library_uuid=library_uuid,
            uuid=job_uuid,
            name=name,
            set_id=set_id,
            character_id=character_id,
            base_model=base_model,
            trigger_word=trigger_word,
            preset=(resolution or {}).get("preset"),
            resolved_profile=json.dumps(resolution) if resolution else None,
            advanced_pins=json.dumps(pins or {}),
        )
        self._note_phase(job_uuid, "waiting_gpu")
        job = training_runs.get_run(hub, library_uuid, run_id)
        # The template path and the config patches have no column. Keep them
        # in memory for the common (no-restart) case and also persist a sidecar
        # so a restart can recover a still-queued job's spec.
        pending = {
            "template_path": template_path,
            "config_overrides": config_overrides,
            "locked": locked,
        }
        with self._pending_specs_lock:
            self._pending_specs[job_uuid] = pending
        self._write_pending_sidecar(job_uuid, pending)
        logger.info(
            "Enqueued training job %s (name=%s, set_id=%s, character_id=%s).",
            job_uuid,
            name,
            set_id,
            character_id,
        )
        self._wake.set()
        return job

    def set_source_folder_registrar(self, register: Callable[[str], None]) -> None:
        """Bind the function that registers the shelf's ``source`` folder.

        The server calls this once it has its folder registration; see the
        ``register_source_folder`` argument of the constructor.

        Args:
            register: Called as ``register(path)``.
        """
        self._register_source_folder = register

    def cancel(self, job_id: int) -> bool:
        """Cooperatively cancel a job by primary-key id.

        Loads the job, and if it is the one currently running, asks the active
        backend to cancel it (cooperative stop, escalating to signals if
        needed). Marks the job ``stopping``; the poll loop observes the terminal
        process state and finalizes it. If the job is queued (not yet started),
        it is marked ``stopped`` directly.

        Args:
            job_id: Primary key of the ``training_run`` row to cancel.

        Returns:
            ``True`` if a job was found and a cancel was actioned, else
            ``False``.
        """

        hub, library_uuid = self._store()
        info = training_runs.get_run(hub, library_uuid, job_id)
        if info is None:
            logger.warning("cancel: no training job with id=%s", job_id)
            return False

        with self._active_lock:
            drawing = self._active_job_uuid == info["uuid"]
        if info["status"] == "completed" and not info["completed_at"] and drawing:
            # Trained, and drawing its deferred previews: stop those and
            # import the run as it is.
            self._skip_previews.add(info["uuid"])
            logger.info("cancel: job %s skips its remaining previews.", info["uuid"])
            return True

        if info["status"] in _TERMINAL_JOB_STATUSES:
            logger.info(
                "cancel: job %s already terminal (%s); nothing to do.",
                info["uuid"],
                info["status"],
            )
            return False

        if info["status"] == "queued":
            # Not started yet: mark it stopped so the worker skips it, and drop
            # the lease it may be waiting on so the worker moves on. The stop is
            # written under the lock the worker clears its wait under, and the
            # lease is released only while still waiting: one granted meanwhile
            # belongs to the worker, which releases it when it sees the stop.
            # The worker launches the run under the same lock, so the status is
            # read again there: one launched since is stopped like a running one.
            with self._active_lock:
                waiting = self._waiting
                queued = self._job_status(info["uuid"]) == "queued"
                if queued:
                    self._update_job(
                        info["uuid"], status="stopped", error="Cancelled before start"
                    )
            if queued:
                if waiting is not None and waiting[0] == info["uuid"]:
                    waiting[1].release(only_waiting=True)
                self._emit_progress(
                    info["uuid"], status="stopped", message="Cancelled before start"
                )
                logger.info("cancel: queued job %s marked stopped.", info["uuid"])
                return True
            info = training_runs.get_run(hub, library_uuid, job_id)
            if info is None or info["status"] in _TERMINAL_JOB_STATUSES:
                return False

        # Running/stopping: mark the job 'stopping' BEFORE signalling the
        # backend. backend.cancel() blocks until the process exits, by which
        # point the worker's poll loop may have already finalized the job to its
        # terminal status; writing 'stopping' after that would clobber the
        # terminal state. Marking it first lets the worker's _finalize win. Under
        # the lock _persist_progress reads and writes under, so a poll cannot
        # read "running" and then write it over this flag.
        with self._active_lock:
            self._update_job(info["uuid"], status="stopping")
        logger.info("cancel: job %s asked to stop (status=stopping).", info["uuid"])

        with self._active_lock:
            backend = self._active_backend
            active_uuid = self._active_job_uuid
        if backend is not None and active_uuid == info["uuid"]:
            handle = self._handle_for_job(backend, info)
            backend.cancel(handle)
        else:
            # Not the active job (e.g. a re-attached run polled by another
            # cycle): build a transient backend to deliver the cooperative stop.
            transient = self._make_backend()
            handle = self._handle_for_job(transient, info)
            transient.cancel(handle)

        return True

    def adopt_live_runs(self) -> None:
        """Give every run whose trainer outlived the restart its lease at once.

        Called before PixlStash's own GPU workers start, so none of them loads
        a model onto a card a live trainer is using. The lease is granted
        without waiting and stays exclusive for :data:`ADOPT_QUIET_S`;
        :meth:`reconcile_on_startup` then resumes watching the run with it.
        """
        if self._disable_background_workers or self._hub is None:
            return
        try:
            unfinished = training_runs.unfinished_runs(self._hub, self._library_uuid)
        except Exception:
            logger.exception("adopt_live_runs: failed to load unfinished jobs")
            return
        for info in unfinished:
            if not info.get("training_folder"):
                continue
            try:
                backend = self._make_backend()
                if not backend.is_alive(self._handle_for_job(backend, info)):
                    continue
                vram_mb, ram_mb = self._lease_need(info) or (0, 0)
                adopt = self._adopt_gpu or self._request_gpu
                self._adopted[info["uuid"]] = adopt(
                    self._lease_label(info), info["uuid"], vram_mb, ram_mb
                )
                logger.info(
                    "Adopted the live training run %s (pid %s) at start-up.",
                    info["uuid"],
                    info.get("pid"),
                )
            except Exception:
                logger.exception(
                    "adopt_live_runs: could not adopt job %s; reconcile asks for "
                    "its lease instead",
                    info["uuid"],
                )

    def reconcile_on_startup(self) -> None:
        """Resolve jobs left ``running`` / ``stopping`` across a restart.

        For each such job: reconstruct its handle and check liveness. If the
        external trainer is still alive, re-attach: ask for its VRAM lease again and
        resume polling it on the worker thread (a restart must not kill an
        in-flight multi-hour run). If it is dead, finalize from its persisted
        status / on-disk checkpoints (completed → imported to the shelf, else
        ``error``).
        """
        if self._disable_background_workers or self._hub is None:
            return

        try:
            unfinished = training_runs.unfinished_runs(self._hub, self._library_uuid)
        except Exception:
            logger.exception("reconcile_on_startup: failed to load unfinished jobs")
            return

        for info in unfinished:
            try:
                self._reconcile_one(info)
            except Exception:
                logger.exception(
                    "reconcile_on_startup: failed to reconcile job %s", info["uuid"]
                )
        # A lease adopted for a run the reconcile could not take over would keep
        # the card exclusive until the next restart.
        for job_uuid, lease in list(self._adopted.items()):
            logger.warning(
                "reconcile_on_startup: releasing the lease adopted for job %s, "
                "which could not be re-attached.",
                job_uuid,
            )
            lease.release()
        self._adopted.clear()

    def _reconcile_one(self, info: dict) -> None:
        """Reconcile a single unfinished job (alive → re-attach, dead → finish).

        Args:
            info: A dict of persisted job fields (uuid, name, status, pid,
                training_folder, set_id, character_id).
        """
        if not info.get("training_folder"):
            # Never got far enough to launch; treat as interrupted.
            logger.warning(
                "reconcile: job %s has no training_folder; marking error.",
                info["uuid"],
            )
            self._update_job(
                info["uuid"],
                status="error",
                error="Interrupted before launch (no training folder recorded)",
            )
            return

        backend = self._make_backend()
        handle = self._handle_for_job(backend, info)
        if backend.is_alive(handle):
            logger.info(
                "reconcile: job %s still alive (pid=%s); re-attaching.",
                info["uuid"],
                info["pid"],
            )
            # Adopted at start-up (adopt_live_runs) when it could be; else asked
            # for, granted once PixlStash has yielded. A live run without a
            # profile (started before recipes) still takes the exclusive lease
            # at 0 MB: it keeps other GPU work off the card under the running
            # trainer, and is released when the run ends.
            lease = self._adopted.pop(info["uuid"], None)
            if lease is None:
                vram_mb, ram_mb = self._lease_need(info) or (0, 0)
                lease = self._request_gpu(
                    self._lease_label(info), info["uuid"], vram_mb, ram_mb
                )
            # Resume polling on the worker thread, owning the lease until the
            # run ends. We deliberately hand off to a fresh thread so startup is
            # not blocked by a multi-hour run.
            self._wake.set()  # ensure the worker is awake to also pick queued work
            reattach = threading.Thread(
                target=self._poll_and_finalize_reattached,
                args=(backend, handle, info, lease),
                name=f"TrainingReattach-{info['uuid'][:8]}",
                daemon=True,
            )
            reattach.start()
            return

        # Dead process (it may have exited since it was adopted): trust the
        # adapter's reconcile to derive the terminal state, then finalize from
        # it. Nobody watched it end, which the live view says.
        adopted = self._adopted.pop(info["uuid"], None)
        if adopted is not None:
            adopted.release()
        logger.info("reconcile: job %s is dead; finalizing from disk.", info["uuid"])
        if info["status"] != "completed":
            # A completed run left unimported was watched to its end.
            self._update_job(info["uuid"], finished_unwatched=1)
        progress = backend.reconcile(handle)
        self._finalize(backend, handle, info, progress)

    def _poll_and_finalize_reattached(
        self, backend: TrainerBackend, handle, info: dict, lease: Lease
    ) -> None:
        """Resume polling a re-attached live job, then finalize and release GPU.

        Args:
            backend: The backend bound to the live run.
            handle: The reconstructed handle.
            info: Persisted job fields.
            lease: The run's VRAM lease, released when the run ends.
        """
        with self._active_lock:
            self._active_backend = backend
            self._active_job_uuid = info["uuid"]
        watch = _Watch(
            from_start=False,
            offsets=measurements.peaks_read_to_end(
                LocalAiToolkit.peaks_folder(handle.work_dir)
            ),
        )
        try:
            progress = self._poll_until_done(
                backend, handle, info["uuid"], lease, watch
            )
            if self._after_training(backend, handle, info, progress, lease, watch):
                self._finalize(backend, handle, info, progress)
        except Exception:
            logger.exception("Re-attached job %s failed during polling", info["uuid"])
            self._update_job(
                info["uuid"], status="error", error="Polling failed after re-attach"
            )
        finally:
            with self._active_lock:
                # Only the run that holds the slot clears it: a later run may
                # have taken it since this one's lease shrank.
                if self._active_backend is backend:
                    self._active_backend = None
                    self._active_job_uuid = None
            self._check_released(info["uuid"], lease, watch)
            lease.release()

    # ------------------------------------------------------------------
    # Worker loop
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """The single worker thread: process queued jobs FIFO."""
        while not self._stop.is_set():
            ran = False
            try:
                ran = self._process_next_queued_job()
            except Exception:
                logger.exception("TrainingService worker iteration failed")
            if self._stop.is_set():
                break
            # If we ran a job, immediately look for the next one; otherwise wait
            # for a wake (enqueue/cancel) or a poll interval.
            if not ran:
                self._wake.wait(self.poll_interval_s)
            self._wake.clear()
        logger.info("TrainingService worker loop exited.")

    def _process_next_queued_job(self) -> bool:
        """Pick the oldest queued job and run it to completion.

        Returns:
            ``True`` if a job was processed (so the loop should re-check
            immediately), ``False`` if there was no queued work.
        """

        if self._hub is None:
            return False
        info = training_runs.next_queued_run(self._hub, self._library_uuid)
        if info is None:
            return False

        self._run_job(info)
        return True

    def _run_job(self, info: dict) -> None:
        """Run one job end to end: dataset → lease → train → finalize.

        Args:
            info: Persisted queued-job fields.
        """
        job_uuid = info["uuid"]
        work_dir = os.path.join(self._training_root, job_uuid)

        # 0. Where the run lands and where its dataset is written.
        try:
            training_folder, dataset_dir = self._run_folders(job_uuid)
        except RuntimeError as exc:
            logger.error("Job %s: %s", job_uuid, exc)
            self._update_job(job_uuid, status="error", error=str(exc))
            self._emit_progress(job_uuid, status="error", message=str(exc))
            return

        need = self._lease_need(info)
        if need is None:
            msg = "Queued before training recipes existed; queue it again."
            self._update_job(job_uuid, status="error", error=msg)
            self._emit_progress(job_uuid, status="error", message=msg)
            return

        # 1. Build the dataset snapshot.
        manifest = self._build_dataset(info, dataset_dir)
        if manifest is None:
            return  # dataset error already recorded
        if int(manifest.get("image_count") or 0) <= 0:
            msg = "Training dataset is empty (no eligible images)."
            logger.warning("Job %s: %s", job_uuid, msg)
            self._update_job(job_uuid, status="error", error=msg)
            self._emit_progress(job_uuid, status="error", message=msg)
            return

        self._update_job(
            job_uuid,
            dataset_snapshot_path=manifest.get("dest_dir"),
            dataset_picture_ids=json.dumps(manifest.get("picture_ids") or []),
        )
        if not self._record_snapshot(info, manifest):
            return  # snapshot error already recorded

        # 2. Wait for the arbiter: PixlStash yields the card, an idle ComfyUI
        # frees it, and the Tasks tab shows the job waiting until then.
        lease = self._request_gpu(self._lease_label(info), job_uuid, *need)
        watch = _Watch()
        backend: TrainerBackend | None = None
        try:
            with self._active_lock:
                self._waiting = (job_uuid, lease)
            granted = lease.wait(self._stop)
            with self._active_lock:
                self._waiting = None
            if self._job_status(job_uuid) != "queued":
                return  # cancelled while it waited
            if not granted:
                if not self._stop.is_set():
                    # Cancelled from the Tasks tab's waiting row.
                    msg = "Cancelled while waiting for the GPU"
                    self._update_job(job_uuid, status="stopped", error=msg)
                    self._emit_progress(job_uuid, status="stopped", message=msg)
                return  # on shutdown it stays queued for the next start
            backend = self._make_backend()
            if not self._check_memory_and_speed(
                backend, info, work_dir, manifest, lease
            ):
                return  # stopped, failed or shut down; the row says which
            launched = self._train_under_lease(
                backend,
                info,
                work_dir,
                manifest,
                lease,
                gpu_ids=lease.device or "0",
                training_folder=training_folder,
                watch=watch,
            )
            if launched is None:
                return  # cancelled after its memory check, before launch
            handle, progress = launched
            if self._after_training(backend, handle, info, progress, lease, watch):
                self._finalize(backend, handle, info, progress)
        except Exception:
            logger.exception("Job %s failed during training", job_uuid)
            self._update_job(job_uuid, status="error", error="Training run failed")
            self._emit_progress(job_uuid, status="error", message="Training run failed")
        finally:
            with self._active_lock:
                # Only the run that holds the slot clears it, as a re-attached
                # run's end does (_poll_and_finalize_reattached).
                if backend is not None and self._active_backend is backend:
                    self._active_backend = None
                    self._active_job_uuid = None
                self._waiting = None
            self._check_released(job_uuid, lease, watch)
            lease.release()
            # A job still queued at shutdown runs on the next start, and needs
            # its spec then, so only drop it once the job has left the queue.
            if self._job_status(job_uuid) != "queued":
                with self._pending_specs_lock:
                    self._pending_specs.pop(job_uuid, None)
                self._remove_pending_sidecar(job_uuid)

    def _train_under_lease(
        self,
        backend: TrainerBackend,
        info: dict,
        work_dir: str,
        manifest: dict,
        lease: Lease,
        gpu_ids: str = "0",
        training_folder: str | None = None,
        watch: _Watch | None = None,
    ) -> tuple[object, TrainingProgress] | None:
        """Prepare + submit + poll a run while the GPU lease is held.

        The run is launched and marked ``running`` under the lock
        :meth:`cancel` writes a queued run's stop under, and only while the run
        is still queued: a cancel during the memory check's last seconds, or
        between the claim and the launch, is never overwritten.

        Args:
            backend: The trainer backend.
            info: Queued-job fields.
            work_dir: The per-job work directory.
            manifest: The dataset manifest (provides ``dest_dir``).
            lease: The run's granted lease; its progress fields are kept live.
            gpu_ids: ``CUDA_VISIBLE_DEVICES`` for the trainer: the leased
                card's UUID, so it trains on the card the arbiter cleared.
            training_folder: The shelf source folder the run is written into.
            watch: Collects the run's phase peaks and times.

        Returns:
            A ``(handle, progress)`` tuple: the launched handle and the terminal
            :class:`TrainingProgress` snapshot. None when the run was cancelled
            before it could launch.
        """
        job_uuid = info["uuid"]
        pending = self._load_pending_spec(job_uuid)
        spec = TrainingSpec(
            job_id=job_uuid,
            job_name=info["name"],
            template_path=pending.get("template_path"),
            dataset_dir=manifest["dest_dir"],
            trigger_word=info.get("trigger_word") or "",
            work_dir=work_dir,
            hf_token=os.environ.get("HF_TOKEN") or None,
            config_overrides=pending.get("config_overrides"),
            locked=pending.get("locked"),
            gpu_ids=gpu_ids,
            training_folder=training_folder,
        )
        # The commit is what the checkout holds when the job starts, read from
        # git and never from the owner's config (review F6).
        cfg = self._config_provider() or {}
        commit = (
            ai_toolkit_install.read_commit(cfg["repo_path"])
            if cfg.get("repo_path")
            else None
        )
        with self._active_lock:
            if self._job_status(job_uuid) != "queued":
                logger.info("Job %s was cancelled before launch.", job_uuid)
                return None
            handle = backend.prepare(spec)
            backend.submit(handle, spec)
            self._active_backend = backend
            self._active_job_uuid = job_uuid
            config = self._read_rendered_config(handle.config_path)
            total_steps = self._total_steps_from_config(config)
            self._update_job(
                job_uuid,
                status="running",
                pid=handle.pid,
                training_folder=handle.training_folder,
                rendered_config=json.dumps(config) if config is not None else None,
                ai_toolkit_commit=commit,
                total_steps=total_steps,
                started_at=now_iso(),
            )
        self._note_phase(job_uuid, "loading")
        self._emit_progress(
            job_uuid, status="running", current=0, total=total_steps, message="Started"
        )

        progress = self._poll_until_done(backend, handle, job_uuid, lease, watch)
        return handle, progress

    def _poll_until_done(
        self,
        backend: TrainerBackend,
        handle,
        job_uuid: str,
        lease: Lease,
        watch: _Watch | None = None,
    ) -> TrainingProgress:
        """Poll progress until the process is dead AND status is terminal.

        Persists step / loss curve / samples / log tail and emits a
        PLUGIN_PROGRESS event each cycle. Each cycle also reads what the
        trainer holds into *watch* and onto the lease, and lowers the lease to
        the run's measured peak once its first checkpoint is saved.

        Args:
            backend: The trainer backend.
            handle: The job handle.
            job_uuid: The job uuid (used as the event ``run_id``).
            lease: The run's lease; the Tasks tab's AI Toolkit row reads its
                step, total, latest loss, phase and memory from it.
            watch: Collects the phase peaks and times; None keeps a private one.

        Returns:
            The final :class:`TrainingProgress` snapshot.
        """
        watch = watch if watch is not None else _Watch()
        deferred = bool(self._resolution(job_uuid).get("previews_deferred"))
        progress = backend.progress(handle)
        while not self._stop.is_set():
            self._persist_progress(job_uuid, progress, handle)
            phase = live_view.phase_of(
                progress.status, progress.info, progress.step, None
            )
            self._observe(handle, progress, lease, watch, phase)
            self._maybe_shrink(job_uuid, lease, watch, progress, deferred)
            self._emit_progress(
                job_uuid,
                status=progress.status,
                current=progress.step,
                total=progress.total_steps,
                message=progress.info,
            )
            alive = backend.is_alive(handle)
            terminal = progress.status in _TERMINAL_PROGRESS_STATUSES
            if not alive and terminal:
                break
            if not alive and not terminal:
                # Process gone but status not terminal; let the adapter reconcile
                # the real end state (interrupted → error), then stop.
                progress = backend.reconcile(handle)
                self._persist_progress(job_uuid, progress, handle)
                break
            self._stop.wait(self.poll_interval_s)
            progress = backend.progress(handle)
        return progress

    # ------------------------------------------------------------------
    # Memory and speed (#2120)
    # ------------------------------------------------------------------

    def _resolution(self, job_uuid: str) -> dict:
        """The run's recorded resolution; {} when it has none or it is unreadable."""
        run = training_runs.get_run_by_uuid(self._hub, job_uuid)
        try:
            resolution = json.loads((run or {}).get("resolved_profile") or "null")
        except ValueError as exc:
            logger.warning("Job %s has an unreadable resolution: %s", job_uuid, exc)
            return {}
        return resolution if isinstance(resolution, dict) else {}

    def _observe(
        self,
        handle,
        progress: TrainingProgress,
        lease: Lease,
        watch: _Watch,
        phase: str | None,
        **extra,
    ) -> None:
        """Read what the trainer holds into *watch* and onto the lease.

        The lease's progress is the worker's phase telemetry: the Tasks tab
        and the live view read the phase and the trainer's memory from it.
        """
        usage = measurements.trainer_usage(
            handle.pid,
            lease.device,
            LocalAiToolkit.peaks_folder(handle.work_dir),
            watch.offsets,
        )
        watch.see(phase, usage, time.monotonic())
        if self._tenant is not None:
            self._tenant.track(usage["pids"])
        lease.set_progress(
            step=progress.step,
            total=progress.total_steps,
            loss=progress.loss_curve[-1].get("loss") if progress.loss_curve else None,
            phase=phase,
            vram_mb=usage["vram_mb"],
            ram_mb=usage["ram_mb"],
            **extra,
        )

    def _maybe_shrink(
        self,
        job_uuid: str,
        lease: Lease,
        watch: _Watch,
        progress: TrainingProgress,
        deferred: bool,
    ) -> None:
        """Lower the lease to the run's peak once its first checkpoint is saved.

        By then it has loaded, cached, trained, saved and (unless its previews
        are deferred) drawn a round of previews, so its peak has been seen. A
        round is drawn once the run has left the previews phase: the first
        poll inside it comes before the VAE decode each preview ends with.
        The lease stops being exclusive, and other GPU work may use what the
        run does not. An adopted lease refuses while it is quiet; the next
        poll tries again.
        """
        if watch.shrunk or not (progress.checkpoints or progress.final_checkpoint):
            return
        if "training" not in watch.peaks:
            return
        if not deferred and (
            "previews" not in watch.peaks or watch.phase == "previews"
        ):
            return
        peak = watch.peak_vram_mb()
        if peak is None:
            return
        need = measurements.with_margin(peak)
        if lease.shrink(need):
            watch.shrunk = True
            logger.info(
                "Job %s: first checkpoint saved; its lease now asks for %s MB "
                "(peak %s MB).",
                job_uuid,
                need,
                peak,
            )

    def _check_released(self, job_uuid: str, lease: Lease, watch: _Watch) -> None:
        """Warn when a trainer that exited left its GPU memory held.

        Its processes get :data:`RELEASE_GRACE_S` to let go. Whatever they
        still hold is announced on the lease and named "memory not released"
        in the Tasks tab rather than counted as other apps'.
        """
        if not watch.pids or lease.device is None or self._stop.is_set():
            return
        deadline = time.monotonic() + RELEASE_GRACE_S
        held = measurements.gpu_mb(watch.pids, lease.device)
        while held and time.monotonic() < deadline and not self._stop.is_set():
            self._stop.wait(1.0)
            held = measurements.gpu_mb(watch.pids, lease.device)
        if not held:
            if self._tenant is not None:
                self._tenant.ended(set())
            return
        leftover = {
            pid for pid in ledger().processes(lease.device) if pid in watch.pids
        }
        text = (
            f"AI-toolkit exited but {held / 1024:.1f} GB of GPU memory was not "
            f"released (process {', '.join(str(p) for p in sorted(leftover))}). "
            "Other GPU work counts it as used until those processes end."
        )
        logger.warning("Job %s: %s", job_uuid, text)
        lease.warn("aitoolkit_memory_not_released", text)
        if self._tenant is not None:
            self._tenant.ended(leftover)

    def _check_memory_and_speed(
        self,
        backend: TrainerBackend,
        info: dict,
        work_dir: str,
        manifest: dict,
        lease: Lease,
    ) -> bool:
        """Probe a run whose setup this machine has not measured yet.

        The probe trains a few updates at the largest bucket, draws one preview
        and saves once, under a RAM guard, and records what each
        phase peaked at. Running out of GPU memory steps to the next profile
        of the fit choice (more offloading); a loader error, a Triton error or
        the RAM guard ends the search. A profile that held but whose previews
        do not fit beside training steps on too, so previews are drawn while
        training wherever a profile allows it. The run then trains with the
        first profile that held with its previews, or else the first that held,
        re-resolved on its measurement, its previews deferred.

        Returns:
            True to train. False when the run was stopped, failed (the row says
            why) or PixlStash is shutting down (it stays queued).
        """
        job_uuid = info["uuid"]
        resolution = self._resolution(job_uuid)
        if "probe" not in resolution:
            return True  # resolved without measurements
        preset_id = str(resolution.get("preset") or "").split("@")[0]
        preset = presets.get_preset(preset_id)
        if preset is None:
            logger.warning(
                "Job %s: recipe %s is gone; training without checking memory.",
                job_uuid,
                resolution.get("preset"),
            )
            return True
        cfg = self._config_provider() or {}
        commit = (
            ai_toolkit_install.read_commit(cfg["repo_path"])
            if cfg.get("repo_path")
            else None
        )
        lookup = measurements.make_lookup(
            self._hub, preset, commit=commit, device=self._device_name(lease.device)
        )
        # Resolved again at launch even when it was measured at enqueue: the
        # checkout or the card may have changed since, which is a new setup.
        # The profiles passed over for their previews come first: measured,
        # they cost no probe and are kept to fall back on (#2411).
        names = [
            *(resolution.get("passed_over") or []),
            resolution["profile"],
            *(resolution.get("fallbacks") or []),
        ]
        reason = None
        # The first profile that held but whose previews do not fit: kept while
        # the next ones are tried for previews drawn while training (#2411).
        deferred = None
        probes = 0
        for name in names:
            try:
                settled = self._reresolve(info, resolution, preset, lookup, name)
                if settled["probe"]:
                    if probes == MAX_PROBES:
                        # Out of probes; a measured profile further on costs none.
                        continue
                    probes += 1
                    self._note_phase(job_uuid, "checking")
                    outcome, reason = self._probe_once(
                        backend, info, work_dir, manifest, lease, settled
                    )
                    if outcome == "oom":
                        logger.info(
                            "Job %s: %s ran out of GPU memory; trying the next "
                            "profile.",
                            job_uuid,
                            name,
                        )
                        continue
                    if outcome == "failed" and deferred is not None:
                        # A profile that trains is in hand: its previews wait.
                        logger.warning(
                            "Job %s: checking %s failed (%s); training with %s, "
                            "previews after training.",
                            job_uuid,
                            name,
                            reason,
                            deferred["profile"],
                        )
                        break
                    if outcome == "failed":
                        self._fail(job_uuid, reason)
                    if outcome != "ok":
                        return False
                    settled = self._reresolve(info, resolution, preset, lookup, name)
            except presets.NoProfileFits as exc:
                reason = str(exc)
                logger.info("Job %s: %s does not fit: %s", job_uuid, name, exc)
                continue
            sampling = settled["values"].get("train.disable_sampling") or {}
            if settled["previews_deferred"] and sampling.get("source") == "gpu":
                logger.info(
                    "Job %s: %s's previews do not fit while training; trying "
                    "the next profile.",
                    job_uuid,
                    name,
                )
                deferred = deferred or settled
                continue
            self._settle(job_uuid, settled)
            return True
        if deferred is not None:
            self._settle(job_uuid, deferred)
            return True
        self._fail(
            job_uuid,
            "Not enough GPU memory: none of this recipe's ways of fitting the GPU "
            "held while checking memory and speed"
            + (f" ({reason})" if reason else "")
            + ".",
        )
        return False

    def _reresolve(
        self, info: dict, resolution: dict, preset: dict, lookup, profile: str
    ) -> dict:
        """The run resolved again on *profile* alone, measurements included.

        The same choices, pictures, room, pins and prompts as at enqueue.
        """
        try:
            pins = json.loads(info.get("advanced_pins") or "{}")
        except ValueError as exc:
            logger.warning("Job %s has unreadable pins: %s", info["uuid"], exc)
            pins = {}
        entry = (resolution.get("values") or {}).get(presets.PROMPTS_KEY) or {}
        prompts = entry.get("value") if entry.get("source") == "captions" else None
        room = resolution.get("room") or {}
        try:
            return presets.resolve(
                preset,
                subject=resolution["subject"],
                effort=resolution["effort"],
                fit=resolution["fit"],
                picture_count=int(resolution["picture_count"]),
                vram_mb=int(room["vram_mb"]),
                ram_mb=room.get("ram_mb"),
                pins=pins,
                prompts=prompts,
                lookup=lookup,
                profile=profile,
            )
        except presets.NoProfileFits:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise presets.NoProfileFits(
                f"the run could not be resolved again: {exc}", "profile", 0, 0
            ) from exc

    def _settle(self, job_uuid: str, settled: dict) -> None:
        """Make *settled* the run's resolution and what it renders."""
        pending = dict(self._load_pending_spec(job_uuid))
        pending["config_overrides"] = presets.to_patch(presets.resolved_values(settled))
        with self._pending_specs_lock:
            self._pending_specs[job_uuid] = pending
        self._write_pending_sidecar(job_uuid, pending)
        # The recipe may be a newer version than at enqueue (#2122): the row
        # names the one it now trains with.
        self._update_job(
            job_uuid, resolved_profile=json.dumps(settled), preset=settled["preset"]
        )
        logger.info(
            "Job %s trains with %s (%s; previews %s).",
            job_uuid,
            settled["profile"],
            settled["evidence"],
            "deferred" if settled["previews_deferred"] else "while training",
        )

    def _stop_orphaned_pass(
        self, backend: TrainerBackend, job_uuid: str, name: str, work_dir: str
    ) -> None:
        """Stop the trainer a crashed probe or preview pass left running.

        A pass records its pid on the run, as the run does. Before a new pass
        clears its directory and launches, the trainer that pid names is
        cancelled if it is a pass of this run, so a second one never trains
        beside it on the card. The pid may belong to any earlier pass (the
        probe, or a preview pass at another step), so each pass directory the
        run has on disk is tried. Any other process is left alone.

        Args:
            backend: The trainer backend.
            job_uuid: The run's uuid.
            name: The run's display name, as the pass's paths derive from it.
            work_dir: The run's work directory, holding ``probe`` and
                ``after/<step>``.
        """
        run = training_runs.get_run_by_uuid(self._hub, job_uuid) or {}
        pid = run.get("pid")
        if not pid:
            return
        after = os.path.join(work_dir, "after")
        steps = sorted(os.listdir(after)) if os.path.isdir(after) else []
        pass_dirs = [os.path.join(work_dir, "probe")]
        pass_dirs += [os.path.join(after, step) for step in steps]
        for pass_dir in pass_dirs:
            handle = LocalAiToolkit.handle_for(pass_dir, job_uuid, name, pid)
            if backend.is_alive(handle):
                logger.warning(
                    "Job %s: stopping pid %s, a pass of it left running in %s.",
                    job_uuid,
                    pid,
                    pass_dir,
                )
                backend.cancel(handle)
                return

    def _probe_once(
        self,
        backend: TrainerBackend,
        info: dict,
        work_dir: str,
        manifest: dict,
        lease: Lease,
        settled: dict,
    ) -> tuple[str, str | None]:
        """Run one probe of *settled*'s profile and record what it measured.

        Returns:
            ``(outcome, reason)``: ``ok`` (measured and recorded), ``oom`` (try
            more offloading), ``failed`` (the RAM guard or a trainer error
            stopped it; *reason* is the run's error) or ``stopped`` (the run
            was cancelled, failed or PixlStash is shutting down; the row
            already says which).
        """
        job_uuid = info["uuid"]
        pending = self._load_pending_spec(job_uuid)
        values = presets.resolved_values(settled)
        # One preview peaks and takes as long as any other, and each one more
        # costs a whole preview (a minute on a large model) before the run.
        if values.get(presets.PROMPTS_KEY):
            values[presets.PROMPTS_KEY] = values[presets.PROMPTS_KEY][:1]
        process: dict = {
            "train": {
                "steps": PROBE_STEPS,
                "skip_first_sample": True,
                "disable_sampling": False,
            },
            "save": {"save_every": PROBE_STEPS},
            "sample": {"sample_every": PROBE_STEPS},
        }
        sizes = values.get("datasets[*].resolution") or []
        if sizes:
            # The worst bucket only, so the peak is the largest a step makes.
            process["datasets"] = [{"resolution": [max(sizes)]}]
        probe_dir = os.path.join(work_dir, "probe")
        self._stop_orphaned_pass(backend, job_uuid, info["name"], work_dir)
        self._remove_tree(probe_dir)
        spec = TrainingSpec(
            job_id=job_uuid,
            job_name=info["name"],
            template_path=pending.get("template_path"),
            dataset_dir=manifest["dest_dir"],
            trigger_word=info.get("trigger_word") or "",
            work_dir=probe_dir,
            hf_token=os.environ.get("HF_TOKEN") or None,
            config_overrides=presets.to_patch(values),
            locked=deep_merge(
                pending.get("locked") or {}, {"config": {"process": [process]}}
            ),
            gpu_ids=lease.device or "0",
        )
        handle = backend.prepare(spec)
        backend.submit(handle, spec)
        self._update_job(job_uuid, pid=handle.pid)
        started = time.time()
        watch = _Watch()

        def stop_probe() -> str | None:
            if self._job_status(job_uuid) != "queued":
                return "stopped"
            available = read_ram().get("available_mb")
            if available and available < RAM_GUARD_MB:
                return "ram"
            return "shutdown" if self._stop.is_set() else None

        progress, guard = self._watch_pass(
            backend, handle, lease, watch, "checking", stop_probe
        )
        self._check_released(job_uuid, lease, watch)
        if guard == "stopped":
            return "stopped", None
        if guard == "shutdown":
            logger.info("Job %s: probe stopped by shutdown; stays queued.", job_uuid)
            return "stopped", None
        if guard == "ram":
            return "failed", (
                "Not enough system RAM to start: less than "
                f"{RAM_GUARD_MB // 1024} GB was free while checking memory and "
                "speed, so the check was stopped. Close other programs or choose "
                "a fit that offloads less."
            )
        text = f"{progress.info or ''}\n{self._read_log_tail(handle.log_path) or ''}"
        if live_view.OOM_RE.search(text):
            return "oom", settled["profile_label"]
        if progress.status != "completed" or not progress.final_checkpoint:
            return "failed", (
                "Checking memory and speed failed: "
                + (
                    live_view.failure_summary(progress.info, progress.step)
                    if progress.info
                    else "the trainer stopped with an error"
                ).rstrip(".")
                + ". See the trainer log."
            )
        step_times = live_view.read_step_times(handle.loss_db_path)
        images = len(progress.samples)
        try:
            measurements.record(
                self._hub,
                settled["fingerprint"],
                preset=settled["preset"],
                profile=settled["profile"],
                device=self._device_name(lease.device),
                phase_peaks=watch.peaks,
                seconds_per_update=live_view.speed(step_times, None)[
                    "seconds_per_step"
                ],
                setup_seconds=(step_times[0][1] - started) if step_times else None,
                seconds_per_preview=(
                    watch.seconds["previews"] / images
                    if images and watch.seconds.get("previews")
                    else None
                ),
                source="probe",
            )
        except Exception:
            logger.exception("Job %s: could not record the probe", job_uuid)
            self._fail(job_uuid, "Checking memory and speed could not be recorded.")
            return "stopped", None
        self._remove_tree(handle.save_root)
        return "ok", None

    def _watch_pass(
        self,
        backend: TrainerBackend,
        handle,
        lease: Lease,
        watch: _Watch,
        shown_as: str,
        should_stop: Callable[[], str | None],
    ) -> tuple[TrainingProgress, str | None]:
        """Poll a short trainer pass (a probe, an after-training preview) to its end.

        The lease shows *shown_as* as the phase, since the trainer's own info
        line cannot tell these passes from training. *should_stop* is asked
        each poll; what it answers stops the pass and is returned with it.
        """
        while True:
            progress = backend.progress(handle)
            phase = live_view.phase_of(
                progress.status, progress.info, progress.step, None
            )
            self._observe(handle, progress, lease, watch, phase, shown_as=shown_as)
            stop = should_stop()
            if stop is not None:
                backend.cancel(handle)
                return backend.progress(handle), stop
            if not backend.is_alive(handle):
                if progress.status not in _TERMINAL_PROGRESS_STATUSES:
                    progress = backend.reconcile(handle)
                return progress, None
            self._stop.wait(_SHORT_POLL_S)

    def _after_training(
        self,
        backend: TrainerBackend,
        handle,
        info: dict,
        progress: TrainingProgress,
        lease: Lease,
        watch: _Watch,
    ) -> bool:
        """What a finished run leaves before it is imported.

        Its measurement is recorded for the next run of the same setup, and
        a run whose previews were deferred has each kept checkpoint sampled.

        Returns:
            False when a shutdown stopped the previews: the run is left
            completed and unimported, and the next start's reconcile imports
            it. True to import now.
        """
        if progress.status != "completed":
            return True
        resolution = self._resolution(info["uuid"])
        # Recorded even on shutdown: only the preview passes wait for a start.
        self._record_run(info["uuid"], handle, progress, lease, watch, resolution)
        if self._stop.is_set():
            return True
        if resolution.get("previews_deferred") and progress.final_checkpoint:
            try:
                return self._sample_after_training(
                    backend, handle, info, progress, lease
                )
            finally:
                self._skip_previews.discard(info["uuid"])
        return True

    def _record_run(
        self,
        job_uuid: str,
        handle,
        progress: TrainingProgress,
        lease: Lease,
        watch: _Watch,
        resolution: dict,
    ) -> None:
        """Store what a finished run measured under its setup's fingerprint."""
        key = resolution.get("fingerprint")
        if not key or not watch.peaks:
            return
        step_times = live_view.read_step_times(handle.loss_db_path)
        setup = None
        if watch.from_start and "loading" in watch.seconds:
            setup = watch.seconds["loading"] + watch.seconds.get("caching", 0.0)
        previews = watch.seconds.get("previews")
        images = len(progress.samples)
        try:
            measurements.record(
                self._hub,
                key,
                preset=resolution["preset"],
                profile=resolution["profile"],
                device=self._device_name(lease.device),
                phase_peaks=watch.peaks,
                seconds_per_update=live_view.speed(step_times, None)[
                    "seconds_per_step"
                ],
                setup_seconds=setup,
                seconds_per_preview=previews / images
                if previews and images and watch.from_start
                else None,
                source="run",
            )
        except Exception:
            logger.exception("Job %s: could not record its measurement", job_uuid)

    def _sample_after_training(
        self,
        backend: TrainerBackend,
        handle,
        info: dict,
        progress: TrainingProgress,
        lease: Lease,
    ) -> bool:
        """Draw the previews of a run that trained without them.

        Each kept checkpoint is loaded on its own (ai-toolkit samples a
        pretrained LoRA at a given step without training), and the pictures
        are moved into the run's ``samples/`` under that step. A pass that
        fails is logged and skipped: the run itself is done. A cancel stops the
        passes and the run is imported as it is.

        Returns:
            False when a shutdown stopped them, else True.
        """
        job_uuid = info["uuid"]
        pending = self._load_pending_spec(job_uuid)
        run = training_runs.get_run_by_uuid(self._hub, job_uuid) or {}
        self._note_phase(job_uuid, "previews")

        def stop() -> str | None:
            if self._stop.is_set():
                return "shutdown"
            return "cancelled" if job_uuid in self._skip_previews else None

        checkpoints = [*progress.checkpoints, progress.final_checkpoint]
        drawn: set[int] = set()
        for path in checkpoints:
            # The final is ``<job_name>.safetensors``, with no step to read: a
            # job name ending in digits would otherwise read as one.
            match = (
                None
                if path == progress.final_checkpoint
                else _CHECKPOINT_STEP_RE.search(os.path.basename(path))
            )
            step = int(match.group(1)) if match else int(progress.step)
            if step in drawn:
                continue  # the final can share its step with the last save
            drawn.add(step)
            if stop() is not None:
                break
            pass_dir = os.path.join(handle.work_dir, "after", str(step))
            self._stop_orphaned_pass(backend, job_uuid, info["name"], handle.work_dir)
            self._remove_tree(pass_dir)
            process = {
                "train": {
                    "steps": step,
                    "start_step": step,
                    "skip_first_sample": True,
                    "disable_sampling": False,
                },
                "network": {"pretrained_lora_path": path},
            }
            spec = TrainingSpec(
                job_id=job_uuid,
                job_name=info["name"],
                template_path=pending.get("template_path"),
                dataset_dir=run.get("dataset_snapshot_path") or "",
                trigger_word=info.get("trigger_word") or "",
                work_dir=pass_dir,
                hf_token=os.environ.get("HF_TOKEN") or None,
                config_overrides=pending.get("config_overrides"),
                locked=deep_merge(
                    pending.get("locked") or {}, {"config": {"process": [process]}}
                ),
                gpu_ids=lease.device or "0",
            )
            try:
                pass_handle = backend.prepare(spec)
                backend.submit(pass_handle, spec)
                self._update_job(job_uuid, pid=pass_handle.pid)
                ended, stopped = self._watch_pass(
                    backend, pass_handle, lease, _Watch(), "previews", stop
                )
                if stopped is not None:
                    break
                if ended.status != "completed":
                    logger.warning(
                        "Job %s: previews of step %s failed: %s",
                        job_uuid,
                        step,
                        ended.info,
                    )
                    continue
                self._move_samples(pass_handle.save_root, handle.save_root)
            except Exception:
                logger.exception(
                    "Job %s: could not draw the previews of step %s", job_uuid, step
                )
            finally:
                self._remove_tree(pass_dir)
        if stop() == "shutdown":
            logger.info(
                "Job %s: previews stopped by shutdown; it is imported on the next "
                "start.",
                job_uuid,
            )
            return False
        self._note_phase(job_uuid, "importing")
        return True

    @staticmethod
    def _move_samples(from_root: str, to_root: str) -> None:
        """Move the pictures in ``<from_root>/samples`` into ``<to_root>/samples``."""
        source = os.path.join(from_root, "samples")
        if not os.path.isdir(source):
            return
        target = os.path.join(to_root, "samples")
        os.makedirs(target, exist_ok=True)
        for name in os.listdir(source):
            shutil.move(os.path.join(source, name), os.path.join(target, name))

    @staticmethod
    def _remove_tree(path: str) -> None:
        """Remove a folder the service made for a run, if it is there."""
        if not os.path.isdir(path):
            return
        try:
            shutil.rmtree(path)
        except OSError as exc:
            logger.warning("Could not remove %s: %s", path, exc)

    @staticmethod
    def _device_name(device: str | None) -> str | None:
        """The card's name, which a measurement is tied to; None when unknown."""
        reading = ledger().device(device) if device else None
        return reading.name if reading is not None else None

    def _fail(self, job_uuid: str, message: str) -> None:
        """End a run that never trained as ``error`` with *message*."""
        logger.warning("Job %s: %s", job_uuid, message)
        self._update_job(job_uuid, status="error", error=message)
        self._emit_progress(job_uuid, status="error", message=message)

    # ------------------------------------------------------------------
    # Finalization
    # ------------------------------------------------------------------

    def _finalize(
        self, backend: TrainerBackend, handle, info: dict, progress: TrainingProgress
    ) -> None:
        """Write the terminal job state and, on success, bring the run to the shelf.

        Args:
            backend: The trainer backend (unused beyond context, kept for
                symmetry / future per-backend finalization).
            handle: The job handle.
            info: Persisted job fields (needs uuid, name, set_id, character_id).
            progress: The terminal progress snapshot.
        """
        job_uuid = info["uuid"]
        status = progress.status

        if status in _TERMINAL_PROGRESS_STATUSES:
            with self._phase_lock:
                self._phase_logs.pop(job_uuid, None)

        if self._stop.is_set() and status not in _TERMINAL_PROGRESS_STATUSES:
            # Shutdown interrupted the poll while the trainer is still running.
            # Leave the row as it is: reconcile_on_startup re-attaches to the
            # live process, or finalizes it from disk once it has exited.
            logger.info(
                "Job %s is still %s at shutdown; leaving it for reconcile_on_startup.",
                job_uuid,
                status,
            )
            return

        if status == "completed" and progress.final_checkpoint:
            try:
                # The final checkpoint sits in the run folder, which is the
                # ai-toolkit run the importer reads (``save_root``).
                landed = self._import_run(
                    os.path.dirname(progress.final_checkpoint), info
                )
                report = (
                    f"Trained {info['name']} to step {progress.step}; "
                    f"{landed['imported']} file(s) on the shelf "
                    f"(stack {landed['stack_id']})."
                )
                self._update_job(
                    job_uuid,
                    status="completed",
                    step=progress.step,
                    report=report,
                    completed_at=now_iso(),
                )
                self._emit_progress(
                    job_uuid,
                    status="completed",
                    current=progress.step,
                    total=progress.total_steps,
                    message="Completed",
                )
                logger.info("Job %s completed: %s", job_uuid, report)
                return
            except Exception:
                logger.exception(
                    "Job %s reached 'completed' but importing the run to the shelf failed",
                    job_uuid,
                )
                self._update_job(
                    job_uuid,
                    status="error",
                    error="Run completed but the LoRA artifact could not be imported",
                    completed_at=now_iso(),
                )
                self._emit_progress(
                    job_uuid, status="error", message="LoRA import failed"
                )
                return

        if status == "completed" and not progress.final_checkpoint:
            msg = "Run reported completed but no final checkpoint was found."
            logger.warning("Job %s: %s", job_uuid, msg)
            self._update_job(
                job_uuid,
                status="error",
                error=msg,
                completed_at=now_iso(),
            )
            self._emit_progress(job_uuid, status="error", message=msg)
            return

        # stopped / error / anything else terminal.
        final_status = "stopped" if status == "stopped" else "error"
        # The card shows the error; the trainer's whole text stays in the report.
        error_msg = (
            None
            if final_status == "stopped"
            else live_view.failure_summary(progress.info, progress.step)
        )
        self._update_job(
            job_uuid,
            status=final_status,
            step=progress.step,
            error=error_msg,
            report=progress.info or None,
            completed_at=now_iso(),
        )
        self._emit_progress(
            job_uuid,
            status=final_status,
            current=progress.step,
            total=progress.total_steps,
            message=error_msg or progress.info or final_status,
        )
        logger.info("Job %s finalized as %s.", job_uuid, final_status)

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _lease_need(info: dict) -> tuple[int, int] | None:
        """``(vram_mb, ram_mb)`` of the run's resolved profile, or None.

        None for a row without one (queued before recipes existed).
        """
        try:
            resolution = json.loads(info.get("resolved_profile") or "null")
            return int(resolution["vram_mb"]), int(resolution["ram_mb"])
        except (TypeError, KeyError, ValueError) as exc:
            logger.warning(
                "Job %s has no readable memory profile: %s", info.get("uuid"), exc
            )
            return None

    @staticmethod
    def _lease_label(info: dict) -> str:
        """The name a job's lease carries in the Tasks tab."""
        return f"LoRA training '{info.get('name') or info['uuid'][:8]}'"

    def _store(self) -> tuple[HubDatabase, str]:
        """The hub and library the runs live in, or raise if none is open.

        Raises:
            RuntimeError: The service was built without a hub or library.
        """
        if self._hub is None or not self._library_uuid:
            raise RuntimeError(
                "No hub or library is open, so training runs cannot be stored."
            )
        return self._hub, self._library_uuid

    def _job_status(self, job_uuid: str) -> str | None:
        """A job's persisted status, or None when it is gone."""
        run = training_runs.get_run_by_uuid(self._hub, job_uuid)
        return run["status"] if run is not None else None

    def _update_job(self, job_uuid: str, **fields) -> None:
        """Update columns on a training run by uuid.

        Args:
            job_uuid: The job uuid.
            **fields: Column names → values to set.
        """
        try:
            if not training_runs.update_run(self._hub, job_uuid, **fields):
                logger.warning("_update_job: no job with uuid=%s", job_uuid)
        except Exception:
            logger.exception("Failed to update job %s with %s", job_uuid, list(fields))

    def _persist_progress(
        self, job_uuid: str, progress: TrainingProgress, handle
    ) -> None:
        """Persist the live progress fields onto the job row.

        Args:
            job_uuid: The job uuid.
            progress: The latest progress snapshot.
            handle: The job handle (for reading the log tail).
        """
        status = progress.status if progress.status != "unknown" else "running"
        log_tail = self._read_log_tail(handle.log_path)
        # The trainer reports running for a while after a cancel; the row's
        # "stopping" stands until the trainer reaches an end state. The read and
        # the write sit under the lock cancel writes "stopping" under.
        with self._active_lock:
            if status == "running" and self._job_status(job_uuid) == "stopping":
                status = "stopping"
            self._update_job(
                job_uuid,
                status=status,
                step=progress.step,
                total_steps=progress.total_steps,
                loss_curve=json.dumps(progress.loss_curve)
                if progress.loss_curve
                else None,
                sample_image_paths=(
                    json.dumps(progress.samples) if progress.samples else None
                ),
                log_tail=log_tail,
            )
        # A completed trainer is imported next: `_finalize` writes completed_at.
        self._note_phase(
            job_uuid, live_view.phase_of(status, progress.info, progress.step, None)
        )

    def _note_phase(self, job_uuid: str, phase: str | None) -> None:
        """Append *phase* to the run's phase log when it is a new one.

        Args:
            job_uuid: The job uuid.
            phase: One of :data:`pixlstash.training.live_view.PHASES`, or None
                (nothing is written).
        """
        if phase is None:
            return
        with self._phase_lock:
            log = self._phase_logs.get(job_uuid)
            if log is None:
                run = training_runs.get_run_by_uuid(self._hub, job_uuid)
                log = live_view.parse_phase_log(run.get("phase_log") if run else None)
                self._phase_logs[job_uuid] = log
            if not live_view.append_phase(log, phase, now_iso()):
                return
            # Written under the lock, so two threads cannot store their
            # snapshots out of order.
            self._update_job(job_uuid, phase_log=json.dumps(log))

    def _emit_progress(
        self,
        run_id: str,
        *,
        status: str,
        current: int = 0,
        total: int | None = None,
        message: str = "",
    ) -> None:
        """Emit a PLUGIN_PROGRESS event in the ComfyUI-compatible shape.

        Args:
            run_id: The job uuid (the event ``run_id``).
            status: Lifecycle status string.
            current: Current step.
            total: Total steps (``None`` → treated as 0 for progress).
            message: Human-readable info string.
        """
        total_val = int(total or 0)
        progress_frac = (current / total_val) if total_val > 0 else 0.0
        try:
            self._notify(
                EventType.PLUGIN_PROGRESS,
                {
                    "plugin": "ai-toolkit",
                    "status": status,
                    "run_id": run_id,
                    "message": str(message or ""),
                    "current": int(current or 0),
                    "total": total_val,
                    "progress": progress_frac,
                },
            )
        except Exception as exc:
            logger.debug("Failed to emit training progress event: %s", exc)

    # ------------------------------------------------------------------
    # Dataset / backend / handle helpers
    # ------------------------------------------------------------------

    def _run_folders(self, job_uuid: str) -> tuple[str, str]:
        """Resolve where a job's run and its dataset go.

        The run goes in the model shelf's ``source`` folder. When the shelf has
        none, ``<repo>/output`` is registered as one, the first time a job
        needs it. The dataset goes in ``<repo>/datasets/pixlstash-<uuid>``.

        Args:
            job_uuid: The job's uuid.

        Returns:
            ``(training_folder, dataset_dir)``.

        Raises:
            RuntimeError: ai-toolkit is not configured, or no shelf folder could
                be found or registered. The message names no host path; the
                detail is logged.
        """
        cfg = self._config_provider() or {}
        repo_path = cfg.get("repo_path")
        if not repo_path:
            raise RuntimeError("ai-toolkit is not configured; cannot run training.")

        training_folder = self._shelf_source_folder()
        if training_folder is None:
            if self._register_source_folder is None:
                raise RuntimeError(
                    "No model shelf folder is set up for ai-toolkit runs."
                )
            output = os.path.join(repo_path, "output")
            try:
                self._register_source_folder(output)
            except Exception as exc:
                logger.exception(
                    "Could not register %s as the shelf's ai-toolkit folder: %s",
                    output,
                    exc,
                )
                raise RuntimeError(
                    "Could not add the ai-toolkit output folder to the model shelf."
                ) from exc
            training_folder = self._shelf_source_folder()
            if training_folder is None:
                raise RuntimeError(
                    "No model shelf folder is set up for ai-toolkit runs."
                )

        dataset_dir = os.path.join(repo_path, "datasets", f"pixlstash-{job_uuid}")
        return training_folder, dataset_dir

    def _shelf_source_folder(self) -> str | None:
        """The path of the first registered shelf ``source`` folder, or None.

        Returns:
            The folder's stored path, or None when none is registered.
        """
        row = self._hub.fetchone(
            "SELECT path FROM model_folder WHERE kind = ? ORDER BY id LIMIT 1",
            (SOURCE_FOLDER_KIND,),
        )
        return row["path"] if row is not None else None

    def _build_dataset(self, info: dict, dataset_dir: str) -> dict | None:
        """Build the dataset snapshot for a job into *dataset_dir*.

        Args:
            info: Queued-job fields (set_id / character_id / trigger_word).
            dataset_dir: The directory the image/caption pairs are written to.

        Returns:
            The dataset manifest, or ``None`` if the build raised (the job is
            marked ``error`` in that case).
        """
        # A run rebuilt after a restart starts from an empty folder: the
        # trainer reads every file in it, so a leftover from the first build
        # would train without being in the snapshot. The folder is this run's
        # own (``pixlstash-<uuid>``, see _run_folders).
        self._remove_tree(dataset_dir)
        os.makedirs(dataset_dir, exist_ok=True)
        # The clips go in when the job's own locked config reads video: the
        # config it renders, not a recipe looked up again, which may be gone.
        locked = self._load_pending_spec(info["uuid"]).get("locked") or {}
        try:
            return self._dataset_builder(
                dest_dir=dataset_dir,
                set_id=info.get("set_id"),
                character_id=info.get("character_id"),
                trigger_word=info.get("trigger_word"),
                clips=presets.reads_video(locked),
            )
        except Exception:
            logger.exception("Job %s: dataset build failed", info["uuid"])
            self._update_job(info["uuid"], status="error", error="Dataset build failed")
            self._emit_progress(
                info["uuid"], status="error", message="Dataset build failed"
            )
            return None

    def _record_snapshot(self, info: dict, manifest: dict) -> bool:
        """Freeze what the run is about to train on (#2123).

        A run whose lineage cannot be recorded does not train: the LoRA it
        made would name a dataset nobody can show.

        Args:
            info: Queued-job fields.
            manifest: The dataset builder's manifest, with its ``items``.

        Returns:
            True when the snapshot is stored; False when it failed and the job
            was marked ``error``.
        """
        hub, library_uuid = self._store()
        try:
            dataset_snapshots.record_snapshot(
                hub,
                library_uuid=library_uuid,
                run_uuid=info["uuid"],
                set_id=info.get("set_id"),
                character_id=info.get("character_id"),
                trigger_word=manifest.get("trigger_word"),
                caption_mode=manifest.get("caption_mode"),
                items=manifest.get("items") or [],
            )
            return True
        except Exception:
            logger.exception(
                "Job %s: the dataset snapshot of %s could not be recorded",
                info["uuid"],
                manifest.get("dest_dir"),
            )
            msg = "Dataset snapshot could not be recorded"
            self._update_job(info["uuid"], status="error", error=msg)
            self._emit_progress(info["uuid"], status="error", message=msg)
            return False

    def _make_backend(self) -> TrainerBackend:
        """Construct a trainer backend from the owner's ai-toolkit config.

        Returns:
            A :class:`TrainerBackend` from the injected factory.

        Raises:
            ValueError: If the owner config is missing the repo path / python.
        """
        cfg = self._config_provider() or {}
        repo_path = cfg.get("repo_path")
        python = cfg.get("python")
        if not repo_path or not python:
            raise ValueError(
                "ai-toolkit is not configured (missing repo_path/python in owner "
                "config); cannot run training."
            )
        return self._backend_factory(repo_path, python)

    def _handle_for_job(self, backend: TrainerBackend, info: dict):
        """Reconstruct a :class:`TrainingHandle` from persisted job fields.

        See :func:`handle_for_run`; *backend*'s ``handle_for`` is used when it
        has one.
        """
        return handle_for_run(self._training_root, info, backend)

    # ------------------------------------------------------------------
    # Pending-spec sidecar (template_path / config_overrides without columns)
    # ------------------------------------------------------------------

    def _pending_sidecar_path(self, job_uuid: str) -> str:
        """Path to the per-job pending-spec sidecar JSON."""
        return os.path.join(self._training_root, job_uuid, "pending_spec.json")

    def _write_pending_sidecar(self, job_uuid: str, pending: dict) -> None:
        """Persist the pending spec so a restart can recover a queued job.

        Args:
            job_uuid: The job uuid.
            pending: ``{"template_path", "config_overrides"}``.
        """
        path = self._pending_sidecar_path(job_uuid)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(pending, fh)
        except OSError as exc:
            logger.warning(
                "Could not write pending-spec sidecar for job %s: %s", job_uuid, exc
            )

    def _load_pending_spec(self, job_uuid: str) -> dict:
        """Load the pending spec from memory, falling back to the sidecar.

        Args:
            job_uuid: The job uuid.

        Returns:
            ``{"template_path", "config_overrides"}`` (possibly empty values).
        """
        with self._pending_specs_lock:
            pending = self._pending_specs.get(job_uuid)
        if pending is not None:
            return pending
        path = self._pending_sidecar_path(job_uuid)
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "No pending spec for job %s (memory or sidecar %s): %s",
                job_uuid,
                path,
                exc,
            )
            return {}

    def _remove_pending_sidecar(self, job_uuid: str) -> None:
        """Delete the pending-spec sidecar once the job is done with it."""
        path = self._pending_sidecar_path(job_uuid)
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            logger.debug(
                "Could not remove pending-spec sidecar for job %s: %s", job_uuid, exc
            )

    # ------------------------------------------------------------------
    # Small static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _read_rendered_config(config_path: str) -> dict | None:
        """Read the rendered job config JSON, or ``None`` if unreadable."""
        try:
            with open(config_path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read rendered config %s: %s", config_path, exc)
            return None

    @staticmethod
    def _total_steps_from_config(config: dict | None) -> int | None:
        """Pull ``config.process[0].train.steps`` from a rendered config."""
        if not config:
            return None
        try:
            return int(config["config"]["process"][0]["train"]["steps"])
        except (KeyError, IndexError, TypeError, ValueError):
            return None

    @staticmethod
    def _read_log_tail(log_path: str) -> str | None:
        """Read the last ``_LOG_TAIL_BYTES`` of the trainer log, or ``None``."""
        if not log_path or not os.path.exists(log_path):
            return None
        try:
            size = os.path.getsize(log_path)
            with open(log_path, "rb") as fh:
                if size > _LOG_TAIL_BYTES:
                    fh.seek(size - _LOG_TAIL_BYTES)
                data = fh.read()
            return data.decode("utf-8", errors="replace")
        except OSError as exc:
            logger.debug("Could not read log tail %s: %s", log_path, exc)
            return None
