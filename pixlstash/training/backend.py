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

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable


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
        config_overrides: The run's tunable values as a deep-merge patch over
            the template (:func:`pixlstash.training.presets.to_patch`), checked
            against the tunables at render. ``None`` means none.
        locked: The recipe's locked keys as a patch, merged last. ``None``
            means none.
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
    locked: dict | None = None
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
    """The interface every training plugin implements.

    Implementations are the single place that knows a specific trainer's
    invocation, config shape, and progress storage. PixlStash code above this
    interface (the training service, the training routes) deals only in this
    class and the data classes in this module. A plugin is listed in
    :mod:`pixlstash.training.registry` under :attr:`name`; a recipe names its
    trainer with ``"trainer"``.

    The lifecycle is: :meth:`prepare` (lay out the work dir, render config,
    create the per-job DB) → :meth:`submit` (spawn the process) → repeated
    :meth:`progress` polling → optional :meth:`cancel`. After a restart,
    :meth:`reconcile` re-derives status from disk + PID liveness, and
    :meth:`is_alive` is the cheap liveness probe it builds on. With
    :meth:`handle_for`, those seven are all a plugin must implement: every
    other member has a default that core handles (no memory probe, no
    after-training previews, a status-only live view, no Train longer).

    **The output-folder contract.** A run's ``save_root`` must hold what
    :mod:`pixlstash.utils.training_run_folder` reads, since that is how a run
    reaches the model shelf and how the live view finds its previews:
    ``<name>_<step:09d>.safetensors`` per kept step, the bare final
    ``<name>.safetensors``, ``config.yaml``, and
    ``samples/<timestamp>__<step:09d>_<prompt index>.<ext>``.

    Attributes:
        name: The registry key (``"ai-toolkit"``), stored on a run's
            ``trainer`` column.
        display_name: What the owner reads.
        presets_dir: The folder of this trainer's recipes, or None.
        template_dir: Where a recipe's ``template`` resolves, or None.
        supports_train_longer: Whether :meth:`kept_checkpoints`,
            :meth:`carry_on_from` and :meth:`template_from_rendered` are
            implemented, so a finished run can be trained on.
    """

    name: str = ""
    display_name: str = ""
    presets_dir: str | None = None
    template_dir: str | None = None
    supports_train_longer: bool = False

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_owner_config(cls, cfg: dict) -> TrainerBackend:
        """Build the trainer from the owner's settings for it.

        Raises:
            ValueError: The trainer is not configured; the message says so.
        """
        return cls()

    @classmethod
    def version_stamp(cls, cfg: dict) -> str | None:
        """What a run records as the trainer's exact version (a commit), or None.

        Measurements are tied to it: a new stamp is a new setup.
        """
        return None

    @classmethod
    def version(cls, cfg: dict) -> str | None:
        """The trainer's release version, or None when it has none."""
        return None

    # ------------------------------------------------------------------
    # Reconstruction without an instance
    # ------------------------------------------------------------------

    @staticmethod
    @abstractmethod
    def handle_for(
        work_dir: str,
        job_id: str,
        job_name: str,
        pid: int | None = None,
        training_folder: str | None = None,
    ) -> TrainingHandle:
        """Derive a run's :class:`TrainingHandle` from its persisted fields.

        Paths only. It must return the same paths for the same inputs across
        restarts and upgrades, or a live run is not found again.
        """
        raise NotImplementedError

    @staticmethod
    def peaks_folder(work_dir: str) -> str | None:
        """Where the trainer writes its allocator peaks; None: polls only."""
        return None

    @staticmethod
    def kept_checkpoints(
        handle: TrainingHandle, final_step: int | None = None
    ) -> dict[int, str]:
        """The checkpoints a run can be trained on from, ``{step: path}``."""
        return {}

    @staticmethod
    def carry_on_from(
        handle: TrainingHandle, step: int, final_step: int | None = None
    ) -> str | None:
        """Leave *step* the newest checkpoint, for Train longer.

        Only called when :attr:`supports_train_longer` is set.
        """
        raise NotImplementedError

    @staticmethod
    def template_from_rendered(config: dict) -> dict:
        """A run's rendered config turned back into its template.

        Only called when :attr:`supports_train_longer` is set.
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Where a run lands, and whether a recipe is offered
    # ------------------------------------------------------------------

    @classmethod
    def run_folders(
        cls,
        cfg: dict,
        work_dir: str,
        job_uuid: str,
        ensure_source_folder: Callable[[str], str],
    ) -> tuple[str | None, str]:
        """``(training_folder, dataset_dir)`` for a new run.

        *ensure_source_folder* registers a folder as the model shelf's
        ``source`` folder when it has none, and answers the one to use. The
        default keeps the run under ``<work_dir>/output`` and the dataset in
        ``<work_dir>/dataset``.

        Raises:
            RuntimeError: The run cannot be placed; the message says why.
        """
        return None, os.path.join(work_dir, "dataset")

    @classmethod
    def offer_refusal(cls, preset: dict, cfg: dict) -> str | None:
        """Why this trainer, set up as *cfg*, cannot train *preset*; None: it can."""
        return None

    # ------------------------------------------------------------------
    # Live-view reads
    # ------------------------------------------------------------------

    @staticmethod
    def status_line(handle: TrainingHandle) -> str:
        """The trainer's own status line for a running run, or ""."""
        return ""

    @staticmethod
    def step_times(handle: TrainingHandle) -> list[tuple[int, float]]:
        """``(step, wall_time)`` of every logged step, oldest first."""
        return []

    @staticmethod
    def running_phase(info: str | None, step: int) -> str:
        """The phase a running run is in, from its status line and step.

        One of ``loading``, ``caching``, ``training``, ``previews``,
        ``saving`` (:data:`pixlstash.training.live_view.PHASES`).
        """
        return "training" if step > 0 else "loading"

    @staticmethod
    def preview_progress(info: str | None) -> dict | None:
        """``{"drawing": n, "total": m}`` while previews are drawn, else None."""
        return None

    @staticmethod
    def downloading(log_path: str) -> bool:
        """Whether the trainer is fetching a model, from its log."""
        return False

    @staticmethod
    def sample_settings(rendered_config: dict | None) -> dict:
        """The run's preview and save settings, from its rendered config.

        Returns:
            ``{sample_every, save_every, prompts, layer_offloading,
            offload_percent}``.
        """
        return {
            "sample_every": None,
            "save_every": None,
            "prompts": [],
            "layer_offloading": False,
            "offload_percent": None,
        }

    @staticmethod
    def failure_summary(info: str | None, step: int) -> str:
        """One readable sentence for why a run failed."""
        lines = (info or "").strip().splitlines()
        return lines[0].strip() if lines else "Training failed."

    @staticmethod
    def is_oom(text: str) -> bool:
        """Whether the trainer's output says it ran out of memory."""
        return False

    # ------------------------------------------------------------------
    # Config patches the service applies
    # ------------------------------------------------------------------

    @staticmethod
    def probe_overrides(steps: int, values: dict) -> dict | None:
        """The locked patch that turns a run into a short memory probe.

        *values* are the run's resolved recipe values. None: this trainer
        cannot probe, and the run trains with its recipe's seed profile.
        """
        return None

    @staticmethod
    def preview_pass_overrides(step: int, checkpoint_path: str) -> dict | None:
        """The locked patch that draws the previews of one kept checkpoint.

        None: this trainer cannot, and a run whose previews were deferred is
        imported without them.
        """
        return None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

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
