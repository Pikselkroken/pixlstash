"""Durable single-worker orchestrator that turns queued jobs into LoRAs.

The :class:`TrainingService` is the layer between PixlStash's data model and the
trainer adapter (:class:`~pixlstash.training.local_ai_toolkit.LocalAiToolkit`).
It owns the job lifecycle: pick the oldest queued :class:`TrainingJob`, snapshot
its dataset, take the GPU lease, drive the adapter (``prepare`` → ``submit`` →
poll → finalize), and persist a :class:`TrainedLora` into the vault when the run
completes. Exactly one job runs at a time because a single GPU is the binding
constraint.

It is built with **dependency injection** so the whole thing is testable without
a GPU (or even a full Vault): every external collaborator is a callable handed
to the constructor: the GPU lease, the event emitter, the owner-config reader,
the dataset builder, and the trainer-backend factory. The default backend
factory builds a real :class:`LocalAiToolkit`; tests pass one pointed at a CPU
stub.

Shutdown policy (important): :meth:`stop` does **not** terminate a running
external trainer. The trainer runs in its own process group and is expected to
survive a PixlStash restart; :meth:`stop` only halts the poll thread and joins
it with a timeout. On the next startup, :meth:`reconcile_on_startup` re-attaches
to any still-alive run (re-acquiring the GPU lease and resuming polling) so a
multi-hour run is never killed by a server bounce. The only thing that stops a
run is an explicit :meth:`cancel` (a user action).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Callable

from sqlmodel import Session, select

from pixlstash.db_models.trained_lora import TrainedLora
from pixlstash.db_models.training_job import TrainingJob
from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.training.backend import TrainerBackend, TrainingProgress, TrainingSpec
from pixlstash.training.local_ai_toolkit import LocalAiToolkit

logger = get_logger(__name__)

# Statuses that mean the job is finished and must not be re-polled / re-run.
_TERMINAL_JOB_STATUSES = frozenset({"completed", "stopped", "error"})
# Adapter progress statuses that mean the external process has reached an end
# state; the poll loop exits once the process is dead AND status is terminal.
_TERMINAL_PROGRESS_STATUSES = frozenset({"completed", "stopped", "error"})
# How long to back off before re-trying a queued job whose GPU lease was denied
# (the drain timed out). Avoids a hot spin while another GPU task drains.
_GPU_BACKOFF_SECONDS = 10.0
# Cap on the trainer log tail we persist onto the job row.
_LOG_TAIL_BYTES = 8192


class TrainingService:
    """Drive queued :class:`TrainingJob` rows to finished :class:`TrainedLora`.

    One background thread runs at a time and processes queued jobs FIFO (oldest
    ``created_at`` first). See the module docstring for the dependency-injection
    rationale and the shutdown/reconcile policy.

    Attributes:
        poll_interval_s: Seconds between progress polls of a running job.
    """

    def __init__(
        self,
        db,
        image_root: str,
        *,
        gpu_acquire: Callable[[], bool],
        gpu_release: Callable[[], None],
        notify: Callable[[EventType, dict], None],
        config_provider: Callable[[], dict],
        dataset_builder: Callable[..., dict],
        backend_factory: Callable[[str, str], TrainerBackend] | None = None,
        poll_interval_s: float = 5.0,
        disable_background_workers: bool = False,
    ) -> None:
        """Initialise the service.

        Args:
            db: A ``VaultDatabase`` (provides ``run_task`` /
                ``run_immediate_read_task``).
            image_root: Vault root. LoRAs land in ``<image_root>/loras/`` and
                per-job work dirs in ``<image_root>/training/<job_uuid>/``.
            gpu_acquire: Acquires the GPU training lease; returns ``True`` on
                success, ``False`` if the GPU did not drain in time.
            gpu_release: Releases the GPU training lease (paired with
                ``gpu_acquire`` in a ``finally`` block).
            notify: Emits an event (``event_type, data``) to the UI/listeners.
            config_provider: Returns the owner's ai-toolkit config as
                ``{"repo_path", "python", "commit"}``.
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
        """
        self._db = db
        self._image_root = image_root
        self._gpu_acquire = gpu_acquire
        self._gpu_release = gpu_release
        self._notify = notify
        self._config_provider = config_provider
        self._dataset_builder = dataset_builder
        self._backend_factory = backend_factory or (
            lambda repo_path, python: LocalAiToolkit(repo_path, python)
        )
        self.poll_interval_s = poll_interval_s
        self._disable_background_workers = disable_background_workers

        self._loras_dir = os.path.join(image_root, "loras")
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
        # Live snapshot of the running job, for the StatsSidebar "Tasks" tab
        # (surfaced via /workers/progress). Set/cleared alongside the active job.
        self._active_job_name: str | None = None
        self._latest_progress: TrainingProgress | None = None
        self._active_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the single worker thread. No-op if workers are disabled."""
        if self._disable_background_workers:
            logger.info("TrainingService: background workers disabled; not starting.")
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
    ) -> TrainingJob:
        """Create a queued :class:`TrainingJob` and wake the worker.

        Exactly one of ``set_id`` / ``character_id`` must be given (the
        set/character invariant). The job is persisted with a fresh uuid and
        ``status="queued"``; the worker picks it up FIFO.

        Args:
            set_id: Picture-set to train on (mutually exclusive with
                ``character_id``).
            character_id: Character to train on (mutually exclusive with
                ``set_id``).
            name: Human-readable job name.
            base_model: Base checkpoint the LoRA trains against.
            trigger_word: Caption trigger word baked into the dataset.
            template_path: Path to the golden ai-toolkit config template.
            config_overrides: Optional deep-merge config patch (stored on the
                job for the run; not persisted as a column).

        Returns:
            The persisted :class:`TrainingJob`.

        Raises:
            ValueError: If not exactly one of ``set_id`` / ``character_id`` is
                provided.
        """
        if (set_id is None) == (character_id is None):
            raise ValueError(
                "Exactly one of set_id / character_id must be provided "
                f"(got set_id={set_id!r}, character_id={character_id!r})."
            )

        job_uuid = uuid.uuid4().hex

        def _create(session: Session) -> TrainingJob:
            job = TrainingJob(
                uuid=job_uuid,
                name=name,
                status="queued",
                set_id=set_id,
                character_id=character_id,
                base_model=base_model,
                trigger_word=trigger_word,
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            # Detach a plain copy of the fields the caller/worker need so no
            # lazy load happens on a closed session.
            session.expunge(job)
            return job

        job = self._db.run_task(_create)
        # The template path + config overrides have no dedicated column. Keep
        # them in memory for the common (no-restart) case and also persist a
        # sidecar so a restart can recover a still-queued job's spec.
        pending = {"template_path": template_path, "config_overrides": config_overrides}
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

    def cancel(self, job_id: int) -> bool:
        """Cooperatively cancel a job by primary-key id.

        Loads the job, and if it is the one currently running, asks the active
        backend to cancel it (cooperative stop, escalating to signals if
        needed). Marks the job ``stopping``; the poll loop observes the terminal
        process state and finalizes it. If the job is queued (not yet started),
        it is marked ``stopped`` directly.

        Args:
            job_id: Primary key of the :class:`TrainingJob` to cancel.

        Returns:
            ``True`` if a job was found and a cancel was actioned, else
            ``False``.
        """

        def _load(session: Session) -> dict | None:
            job = session.get(TrainingJob, job_id)
            if job is None:
                return None
            return {
                "uuid": job.uuid,
                "name": job.name,
                "status": job.status,
                "pid": job.pid,
                "training_folder": job.training_folder,
            }

        info = self._db.run_immediate_read_task(_load)
        if info is None:
            logger.warning("cancel: no training job with id=%s", job_id)
            return False

        if info["status"] in _TERMINAL_JOB_STATUSES:
            logger.info(
                "cancel: job %s already terminal (%s); nothing to do.",
                info["uuid"],
                info["status"],
            )
            return False

        if info["status"] == "queued":
            # Not started yet: just mark it stopped so the worker skips it.
            self._update_job(
                info["uuid"], status="stopped", error="Cancelled before start"
            )
            self._emit_progress(
                info["uuid"], status="stopped", message="Cancelled before start"
            )
            logger.info("cancel: queued job %s marked stopped.", info["uuid"])
            return True

        # Running/stopping: mark the job 'stopping' BEFORE signalling the
        # backend. backend.cancel() blocks until the process exits, by which
        # point the worker's poll loop may have already finalized the job to its
        # terminal status; writing 'stopping' after that would clobber the
        # terminal state. Marking it first lets the worker's _finalize win.
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

    def current_worker_snapshot(self) -> dict | None:
        """Return a ``/workers/progress``-style entry for the running job.

        Lets the StatsSidebar "Tasks" tab render live training progress with the
        same worker-row UI (label / current·total / rate sparkline) as the
        background workers, instead of a bespoke panel. Returns ``None`` when no
        job is actively running.

        Returns:
            A worker-snapshot dict (keyed under ``lora_training`` by the caller),
            or ``None`` when idle.
        """
        with self._active_lock:
            progress = self._latest_progress
            name = self._active_job_name
            active_uuid = self._active_job_uuid
        if progress is None or active_uuid is None or progress.status != "running":
            return None
        total = int(progress.total_steps or 0)
        step = int(progress.step or 0)
        latest_loss = (
            progress.loss_curve[-1].get("loss") if progress.loss_curve else None
        )
        return {
            "label": "lora_training",
            "current": step,
            "total": total,
            "remaining": max(total - step, 0),
            "updated_at": time.time(),
            "status": "running",
            "running": True,
            "active": True,
            "job_name": name,
            "loss": latest_loss,
            "info": progress.info or "",
        }

    def reconcile_on_startup(self) -> None:
        """Resolve jobs left ``running`` / ``stopping`` across a restart.

        For each such job: reconstruct its handle and check liveness. If the
        external trainer is still alive, re-attach: re-acquire the GPU lease and
        resume polling it on the worker thread (a restart must not kill an
        in-flight multi-hour run). If it is dead, finalize from its persisted
        status / on-disk checkpoints (completed → :class:`TrainedLora`, else
        ``error``).
        """
        if self._disable_background_workers:
            return

        def _load_unfinished(session: Session) -> list[dict]:
            jobs = session.exec(
                select(TrainingJob).where(
                    TrainingJob.status.in_(["running", "stopping"])
                )
            ).all()
            return [
                {
                    "uuid": j.uuid,
                    "name": j.name,
                    "status": j.status,
                    "pid": j.pid,
                    "training_folder": j.training_folder,
                    "set_id": j.set_id,
                    "character_id": j.character_id,
                }
                for j in jobs
            ]

        try:
            unfinished = self._db.run_immediate_read_task(_load_unfinished)
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
                "reconcile: job %s still alive (pid=%s); re-attaching under GPU lease.",
                info["uuid"],
                info["pid"],
            )
            if not self._gpu_acquire():
                logger.warning(
                    "reconcile: could not re-acquire GPU lease for live job %s; "
                    "leaving it for a later cycle.",
                    info["uuid"],
                )
                return
            # Resume polling on the worker thread, owning the lease until the
            # run ends. We deliberately hand off to a fresh thread so startup is
            # not blocked by a multi-hour run.
            self._wake.set()  # ensure the worker is awake to also pick queued work
            reattach = threading.Thread(
                target=self._poll_and_finalize_reattached,
                args=(backend, handle, info),
                name=f"TrainingReattach-{info['uuid'][:8]}",
                daemon=True,
            )
            reattach.start()
            return

        # Dead process: trust the adapter's reconcile to derive the terminal
        # state, then finalize from it.
        logger.info("reconcile: job %s is dead; finalizing from disk.", info["uuid"])
        progress = backend.reconcile(handle)
        self._finalize(backend, handle, info, progress)

    def _poll_and_finalize_reattached(
        self, backend: TrainerBackend, handle, info: dict
    ) -> None:
        """Resume polling a re-attached live job, then finalize and release GPU.

        Args:
            backend: The backend bound to the live run.
            handle: The reconstructed handle.
            info: Persisted job fields.
        """
        with self._active_lock:
            self._active_backend = backend
            self._active_job_uuid = info["uuid"]
            self._active_job_name = info.get("name")
        try:
            progress = self._poll_until_done(backend, handle, info["uuid"])
            self._finalize(backend, handle, info, progress)
        except Exception:
            logger.exception("Re-attached job %s failed during polling", info["uuid"])
            self._update_job(
                info["uuid"], status="error", error="Polling failed after re-attach"
            )
        finally:
            with self._active_lock:
                self._active_backend = None
                self._active_job_uuid = None
                self._active_job_name = None
                self._latest_progress = None
            self._gpu_release()

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

        def _claim(session: Session) -> dict | None:
            job = session.exec(
                select(TrainingJob)
                .where(TrainingJob.status == "queued")
                .order_by(TrainingJob.created_at, TrainingJob.id)
            ).first()
            if job is None:
                return None
            return {
                "uuid": job.uuid,
                "name": job.name,
                "set_id": job.set_id,
                "character_id": job.character_id,
                "trigger_word": job.trigger_word,
                "base_model": job.base_model,
                "rendered_config": job.rendered_config,
            }

        info = self._db.run_immediate_read_task(_claim)
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

        # 1. Build the dataset snapshot.
        manifest = self._build_dataset(info, work_dir)
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

        # 2. Acquire the GPU lease (back off + retry if the drain timed out).
        if not self._gpu_acquire():
            logger.info(
                "Job %s: GPU lease denied (drain timeout); leaving queued, "
                "backing off %.0fs.",
                job_uuid,
                _GPU_BACKOFF_SECONDS,
            )
            # Leave status queued and wait; the worker will retry on the next
            # cycle. Release defensively in case the lease left the queue paused.
            self._gpu_release()
            self._stop.wait(_GPU_BACKOFF_SECONDS)
            return

        try:
            backend = self._make_backend()
            handle, progress = self._train_under_lease(
                backend, info, work_dir, manifest
            )
            self._finalize(backend, handle, info, progress)
        except Exception:
            logger.exception("Job %s failed during training", job_uuid)
            self._update_job(job_uuid, status="error", error="Training run failed")
            self._emit_progress(job_uuid, status="error", message="Training run failed")
        finally:
            with self._active_lock:
                self._active_backend = None
                self._active_job_uuid = None
                self._active_job_name = None
                self._latest_progress = None
            self._gpu_release()
            with self._pending_specs_lock:
                self._pending_specs.pop(job_uuid, None)
            self._remove_pending_sidecar(job_uuid)

    def _train_under_lease(
        self, backend: TrainerBackend, info: dict, work_dir: str, manifest: dict
    ) -> tuple[object, TrainingProgress]:
        """Prepare + submit + poll a run while the GPU lease is held.

        Args:
            backend: The trainer backend.
            info: Queued-job fields.
            work_dir: The per-job work directory.
            manifest: The dataset manifest (provides ``dest_dir``).

        Returns:
            A ``(handle, progress)`` tuple: the launched handle and the terminal
            :class:`TrainingProgress` snapshot.
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
        )
        handle = backend.prepare(spec)
        backend.submit(handle, spec)

        with self._active_lock:
            self._active_backend = backend
            self._active_job_uuid = job_uuid
            self._active_job_name = info["name"]

        config = self._read_rendered_config(handle.config_path)
        total_steps = self._total_steps_from_config(config)
        cfg = self._config_provider() or {}
        self._update_job(
            job_uuid,
            status="running",
            pid=handle.pid,
            training_folder=handle.training_folder,
            rendered_config=json.dumps(config) if config is not None else None,
            ai_toolkit_commit=cfg.get("commit"),
            total_steps=total_steps,
            started_at=datetime.now(timezone.utc),
        )
        self._emit_progress(
            job_uuid, status="running", current=0, total=total_steps, message="Started"
        )

        progress = self._poll_until_done(backend, handle, job_uuid)
        return handle, progress

    def _poll_until_done(
        self, backend: TrainerBackend, handle, job_uuid: str
    ) -> TrainingProgress:
        """Poll progress until the process is dead AND status is terminal.

        Persists step / loss curve / samples / log tail and emits a
        PLUGIN_PROGRESS event each cycle.

        Args:
            backend: The trainer backend.
            handle: The job handle.
            job_uuid: The job uuid (used as the event ``run_id``).

        Returns:
            The final :class:`TrainingProgress` snapshot.
        """
        progress = backend.progress(handle)
        while not self._stop.is_set():
            self._persist_progress(job_uuid, progress, handle)
            with self._active_lock:
                self._latest_progress = progress
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
    # Finalization
    # ------------------------------------------------------------------

    def _finalize(
        self, backend: TrainerBackend, handle, info: dict, progress: TrainingProgress
    ) -> None:
        """Write the terminal job state and, on success, the TrainedLora.

        Args:
            backend: The trainer backend (unused beyond context, kept for
                symmetry / future per-backend finalization).
            handle: The job handle.
            info: Persisted job fields (needs uuid, name, set_id, character_id).
            progress: The terminal progress snapshot.
        """
        job_uuid = info["uuid"]
        status = progress.status

        if status == "completed" and progress.final_checkpoint:
            try:
                lora = self._import_lora(info, progress)
                report = (
                    f"Trained {info['name']} to step {progress.step}; "
                    f"LoRA {lora['uuid']} ({lora['file_size_bytes']} bytes)."
                )
                self._update_job(
                    job_uuid,
                    status="completed",
                    step=progress.step,
                    report=report,
                    completed_at=datetime.now(timezone.utc),
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
                    "Job %s reached 'completed' but importing the LoRA failed",
                    job_uuid,
                )
                self._update_job(
                    job_uuid,
                    status="error",
                    error="Run completed but the LoRA artifact could not be imported",
                    completed_at=datetime.now(timezone.utc),
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
                completed_at=datetime.now(timezone.utc),
            )
            self._emit_progress(job_uuid, status="error", message=msg)
            return

        # stopped / error / anything else terminal.
        final_status = "stopped" if status == "stopped" else "error"
        error_msg = (
            None if final_status == "stopped" else (progress.info or "Training failed")
        )
        self._update_job(
            job_uuid,
            status=final_status,
            step=progress.step,
            error=error_msg,
            report=progress.info or None,
            completed_at=datetime.now(timezone.utc),
        )
        self._emit_progress(
            job_uuid,
            status=final_status,
            current=progress.step,
            total=progress.total_steps,
            message=progress.info or final_status,
        )
        logger.info("Job %s finalized as %s.", job_uuid, final_status)

    def _import_lora(self, info: dict, progress: TrainingProgress) -> dict:
        """Move the final checkpoint into the vault and create a TrainedLora row.

        Copies samples under the vault too. Returns a small dict of the created
        LoRA's fields.

        Args:
            info: Persisted job fields (uuid, name, set_id, character_id).
            progress: The completed progress snapshot (has ``final_checkpoint``).

        Returns:
            ``{"uuid", "file_path", "file_sha", "file_size_bytes"}``.
        """
        os.makedirs(self._loras_dir, exist_ok=True)
        lora_uuid = uuid.uuid4().hex
        dest_abs = os.path.join(self._loras_dir, f"{lora_uuid}.safetensors")
        rel_path = os.path.join("loras", f"{lora_uuid}.safetensors")

        # Move the artifact into the vault (fall back to copy across devices).
        shutil.move(progress.final_checkpoint, dest_abs)
        file_size = os.path.getsize(dest_abs)
        file_sha = self._sha256_file(dest_abs)

        # Copy any sample previews under the vault next to the LoRA.
        sample_rel_paths: list[str] = []
        if progress.samples:
            samples_dir_abs = os.path.join(self._loras_dir, f"{lora_uuid}_samples")
            os.makedirs(samples_dir_abs, exist_ok=True)
            for sample in progress.samples:
                if not os.path.exists(sample):
                    continue
                base = os.path.basename(sample)
                shutil.copyfile(sample, os.path.join(samples_dir_abs, base))
                sample_rel_paths.append(
                    os.path.join("loras", f"{lora_uuid}_samples", base)
                )

        def _create(session: Session) -> None:
            job = session.exec(
                select(TrainingJob).where(TrainingJob.uuid == info["uuid"])
            ).first()
            lora = TrainedLora(
                uuid=lora_uuid,
                job_id=job.id if job is not None else None,
                set_id=info.get("set_id"),
                character_id=info.get("character_id"),
                name=info["name"],
                file_path=rel_path,
                file_size_bytes=file_size,
                file_sha=file_sha,
                step=progress.step,
            )
            session.add(lora)
            if sample_rel_paths:
                job_obj = job
                if job_obj is not None:
                    job_obj.sample_image_paths = json.dumps(sample_rel_paths)
                    session.add(job_obj)
            session.commit()

        self._db.run_task(_create)
        logger.info(
            "Imported LoRA %s for job %s: %s (%d bytes, %d samples).",
            lora_uuid,
            info["uuid"],
            rel_path,
            file_size,
            len(sample_rel_paths),
        )
        return {
            "uuid": lora_uuid,
            "file_path": rel_path,
            "file_sha": file_sha,
            "file_size_bytes": file_size,
        }

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _update_job(self, job_uuid: str, **fields) -> None:
        """Update columns on a :class:`TrainingJob` by uuid.

        Args:
            job_uuid: The job uuid.
            **fields: Column names → values to set.
        """

        def _do(session: Session) -> None:
            job = session.exec(
                select(TrainingJob).where(TrainingJob.uuid == job_uuid)
            ).first()
            if job is None:
                logger.warning("_update_job: no job with uuid=%s", job_uuid)
                return
            for key, value in fields.items():
                setattr(job, key, value)
            session.add(job)
            session.commit()

        try:
            self._db.run_task(_do)
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
        self._update_job(
            job_uuid,
            status=progress.status if progress.status != "unknown" else "running",
            step=progress.step,
            total_steps=progress.total_steps,
            loss_curve=json.dumps(progress.loss_curve) if progress.loss_curve else None,
            sample_image_paths=(
                json.dumps(progress.samples) if progress.samples else None
            ),
            log_tail=self._read_log_tail(handle.log_path),
        )

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

    def _build_dataset(self, info: dict, work_dir: str) -> dict | None:
        """Build the dataset snapshot for a job into ``<work_dir>/dataset``.

        Args:
            info: Queued-job fields (set_id / character_id / trigger_word).
            work_dir: The per-job work directory.

        Returns:
            The dataset manifest, or ``None`` if the build raised (the job is
            marked ``error`` in that case).
        """
        dataset_dir = os.path.join(work_dir, "dataset")
        os.makedirs(dataset_dir, exist_ok=True)
        try:
            return self._dataset_builder(
                dest_dir=dataset_dir,
                set_id=info.get("set_id"),
                character_id=info.get("character_id"),
                trigger_word=info.get("trigger_word"),
            )
        except Exception:
            logger.exception("Job %s: dataset build failed", info["uuid"])
            self._update_job(info["uuid"], status="error", error="Dataset build failed")
            self._emit_progress(
                info["uuid"], status="error", message="Dataset build failed"
            )
            return None

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

        Derives the work dir from ``training_folder`` (which is
        ``<work_dir>/output``) and rebuilds the layout via the adapter's
        ``handle_for`` when available (the adapter is the source of truth for
        paths). Falls back to the documented convention for a generic backend.

        Args:
            backend: The backend (for its ``handle_for`` if present).
            info: Persisted job fields (uuid, name, pid, training_folder).

        Returns:
            A reconstructed :class:`TrainingHandle`.
        """
        training_folder = info["training_folder"]
        work_dir = os.path.dirname(training_folder)
        handle_for = getattr(backend, "handle_for", None)
        if callable(handle_for):
            return handle_for(work_dir, info["uuid"], info["name"], info.get("pid"))
        return LocalAiToolkit.handle_for(
            work_dir, info["uuid"], info["name"], info.get("pid")
        )

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
    def _sha256_file(path: str) -> str:
        """Compute the SHA-256 hex digest of a file's contents."""
        hasher = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

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
