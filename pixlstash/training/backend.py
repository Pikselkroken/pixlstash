"""Trainer-agnostic interface and data structures for PixlStash training jobs.

This module defines the contract every trainer adapter implements. It holds no
trainer-specific knowledge and pulls in no heavy dependencies (no torch, no ML
stack), so it is cheap to import and trivial to unit-test.

The data classes are deliberately plain:

- :class:`TrainingSpec` is the *input*: everything PixlStash wants for a run,
  expressed in PixlStash's own terms (a dataset directory, a trigger word, a
  work dir, GPU selection). It carries no adapter state.
- :class:`TrainingHandle` is the *bookkeeping* a backend produces in
  :meth:`TrainerBackend.prepare`: the concrete on-disk paths and the launched
  PID. It is the only thing the caller needs to keep in order to poll, cancel,
  or reconcile a run later (e.g. after a server restart).
- :class:`TrainingProgress` is the *output* of a poll: a normalised snapshot of
  status, step, loss curve, and produced artifacts that the API/UI can render
  without knowing anything about the trainer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class TrainingSpec:
    """Everything PixlStash needs to launch one training run.

    Attributes:
        job_id: Stable unique id for this job (a UUID/slug). Also used as the
            ai-toolkit ``AITK_JOB_ID`` and as the primary key of the per-job
            ``Job`` table row.
        job_name: Human/slug name for the run. Defines the trainer's
            ``save_root`` (``<training_folder>/<job_name>``) and the checkpoint
            file stem.
        template_path: Path to the golden JSON config template with
            ``{{PLACEHOLDER}}`` fields (e.g. the built-in
            ``zimage-turbo-lora.json``).
        dataset_dir: Directory holding the exported images plus their sidecar
            ``.txt`` captions, fed to the trainer as the dataset folder.
        trigger_word: The character/set trigger token, injected into the config
            and into the sample prompts.
        work_dir: Per-job working directory PixlStash owns. The backend creates
            its output tree, config, and per-job DB underneath it.
        gpu_ids: Comma-separated CUDA device id(s) for ``CUDA_VISIBLE_DEVICES``.
        hf_token: Optional Hugging Face token, passed to the trainer process as
            ``HF_TOKEN`` if set. NEVER logged.
        config_overrides: Optional deep-merge patch applied on top of the
            rendered template, for full passthrough of any config field
            (e.g. a different base model). ``None`` means no overrides.
        training_folder: The shelf's ai-toolkit folder the run is written
            into (the registered ``source`` folder). ``None`` keeps the run
            under the work dir's ``output/``.
    """

    job_id: str
    job_name: str
    template_path: str
    dataset_dir: str
    trigger_word: str
    work_dir: str
    gpu_ids: str = "0"
    hf_token: str | None = None
    config_overrides: dict | None = None
    training_folder: str | None = None


@dataclass
class TrainingProgress:
    """Normalised snapshot of a run's progress and produced artifacts.

    A poll returns this regardless of which backend produced it. Every field is
    populated best-effort: a backend that cannot read a particular source (e.g.
    a locked or missing loss DB) returns empty/last-known values rather than
    raising, so a polling loop never crashes.

    Attributes:
        status: Lifecycle state, normalised to the trainer's vocabulary:
            ``queued`` | ``running`` | ``stopped`` | ``error`` | ``completed``.
        info: Human-readable status string from the trainer
            (e.g. ``"Loading model"``, ``"Saving model"``).
        step: Latest completed training step.
        total_steps: Total planned steps from the rendered config, or ``None``
            if it could not be determined.
        speed: Speed string from the trainer (e.g. ``"2.31 iter/sec"``), used
            to derive an ETA. Empty string when unknown.
        loss_curve: Ordered list of ``{"step": int, "loss": float}`` points.
        samples: Absolute paths to generated preview sample files.
        checkpoints: Absolute paths to intermediate ``.safetensors`` checkpoints
            (excluding the final one), oldest first.
        final_checkpoint: Absolute path to the final ``<job_name>.safetensors``
            once the run completes, else ``None``.
    """

    status: str
    info: str
    step: int
    total_steps: int | None
    speed: str
    loss_curve: list[dict] = field(default_factory=list)
    samples: list[str] = field(default_factory=list)
    checkpoints: list[str] = field(default_factory=list)
    final_checkpoint: str | None = None


@dataclass
class TrainingHandle:
    """Concrete bookkeeping for a prepared/launched run.

    Produced by :meth:`TrainerBackend.prepare` and consumed by every other
    backend method. Holds the resolved on-disk layout plus the launched PID so a
    run can be polled, cancelled, or reconciled after a restart from this handle
    alone (it is JSON-serialisable; all fields are primitives).

    Attributes:
        job_id: The job id (matches :attr:`TrainingSpec.job_id`).
        work_dir: The per-job working directory PixlStash owns.
        training_folder: The trainer's output root under ``work_dir``
            (``<work_dir>/output``); maps to the config's ``training_folder``.
        save_root: Where the trainer writes checkpoints/samples for this run
            (``<training_folder>/<job_name>``).
        sqlite_db_path: Path to the per-job ``Job``-table DB
            (``<work_dir>/job.db``) used for status/step/speed + cooperative
            cancel.
        loss_db_path: Path to the trainer's loss/step DB
            (``<save_root>/loss_log.db``), written by the trainer once it runs.
        config_path: Path to the rendered job config JSON the trainer is
            launched with.
        log_path: Path to the trainer's stdout/stderr log file.
        pid: PID of the launched trainer process group leader, or ``None``
            before :meth:`TrainerBackend.submit` runs.
    """

    job_id: str
    work_dir: str
    training_folder: str
    save_root: str
    sqlite_db_path: str
    loss_db_path: str
    config_path: str
    log_path: str
    pid: int | None = None


class TrainerBackend(ABC):
    """Abstract interface every PixlStash trainer adapter implements.

    Implementations are the single place that knows a specific trainer's
    invocation, config shape, and progress storage. PixlStash code above this
    interface deals only in the data classes in this module.

    The lifecycle is: :meth:`prepare` (lay out the work dir, render config,
    create the per-job DB) → :meth:`submit` (spawn the process) → repeated
    :meth:`progress` polling → optional :meth:`cancel`. After a restart,
    :meth:`reconcile` re-derives status from disk + PID liveness, and
    :meth:`is_alive` is the cheap liveness probe it builds on.
    """

    @abstractmethod
    def prepare(self, spec: TrainingSpec) -> TrainingHandle:
        """Lay out the work dir and produce a launchable handle.

        Creates the output tree, rotates any stale log, creates the per-job
        ``Job``-table DB with a single ``queued`` row, and renders + writes the
        job config. Does NOT spawn the trainer.

        Args:
            spec: The run specification.

        Returns:
            A :class:`TrainingHandle` with all paths resolved and ``pid=None``.
        """
        raise NotImplementedError

    @abstractmethod
    def submit(self, handle: TrainingHandle, spec: TrainingSpec) -> None:
        """Spawn the trainer process for a prepared handle.

        Launches the trainer in its own process group, records the PID onto
        *handle*, and marks the job ``running``.

        Args:
            handle: The handle returned by :meth:`prepare`.
            spec: The same spec passed to :meth:`prepare` (for env/GPU/token).
        """
        raise NotImplementedError

    @abstractmethod
    def progress(self, handle: TrainingHandle) -> TrainingProgress:
        """Read a fresh progress snapshot for a running or finished job.

        Must be robust: never raise on a locked/missing/partial DB; return
        empty or last-known values and log a warning instead.

        Args:
            handle: The job handle.

        Returns:
            A :class:`TrainingProgress` snapshot.
        """
        raise NotImplementedError

    @abstractmethod
    def cancel(self, handle: TrainingHandle) -> None:
        """Request cancellation of a running job.

        Prefers cooperative cancel (signal the trainer to stop cleanly); falls
        back to terminating the process group if the trainer does not exit
        within a grace period.

        Args:
            handle: The job handle.
        """
        raise NotImplementedError

    @abstractmethod
    def reconcile(self, handle: TrainingHandle) -> TrainingProgress:
        """Re-derive a job's state after a restart.

        Combines PID liveness with the persisted status so a job left
        ``running`` across a restart is correctly resolved to its real terminal
        state (or flagged as interrupted).

        Args:
            handle: The job handle, as persisted before the restart.

        Returns:
            A :class:`TrainingProgress` snapshot reflecting reconciled state.
        """
        raise NotImplementedError

    @abstractmethod
    def is_alive(self, handle: TrainingHandle) -> bool:
        """Return whether the trainer process for *handle* is still running.

        Args:
            handle: The job handle.

        Returns:
            ``True`` if the recorded PID is alive, ``False`` otherwise (including
            when no PID has been recorded).
        """
        raise NotImplementedError
