"""ai-toolkit adapter: the only module that knows ai-toolkit specifics.

This is the "firewall" described in
``pixlstash/data/ai-toolkit-jobs/SEAM.md``. Every ai-toolkit fact PixlStash
relies on (the launch command, the config placeholders, the two sqlite files,
the cancel protocol, the checkpoint/sample naming) is implemented here and
nowhere else, so a trainer version bump is a single-file change.

It deliberately avoids heavy imports (no torch / ML stack): it only spawns the
external trainer process and reads the sqlite files and output directory the
trainer writes. That keeps the whole thing importable and unit-testable on a
machine without a GPU, driven by a faithful stub for ``run.py``.
"""

from __future__ import annotations

import glob
import json
import os
import re
import signal
import sqlite3
import subprocess
import time

import psutil

from pixlstash.pixl_logging import get_logger
from pixlstash.training.ai_toolkit_install import trainer_env
from pixlstash.training.backend import (
    TrainerBackend,
    TrainingHandle,
    TrainingProgress,
    TrainingSpec,
)
from pixlstash.training.config_render import render_job_config
from pixlstash.utils.atomic_write import write_json_atomic
from pixlstash.utils.sqlite_uri import sqlite_file_uri

logger = get_logger(__name__)

# Minimal Job table: only the columns the trainer reads/writes (SEAM.md §2).
_JOB_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS Job (
  id              TEXT    NOT NULL PRIMARY KEY,
  status          TEXT    NOT NULL DEFAULT 'stopped',
  info            TEXT    NOT NULL DEFAULT '',
  step            INTEGER NOT NULL DEFAULT 0,
  speed_string    TEXT    NOT NULL DEFAULT '',
  stop            INTEGER NOT NULL DEFAULT 0,
  return_to_queue INTEGER NOT NULL DEFAULT 0,
  save_now        INTEGER NOT NULL DEFAULT 0,
  sample_now      INTEGER NOT NULL DEFAULT 0
);
"""
# Flags added since a job.db could have been created, for a table that exists.
_LATER_JOB_FLAGS = ("sample_now",)

# The trainer's own stdout/stderr, in the work dir. Never the backend's streams:
# under the desktop app those are pipes that close when it quits, and the
# trainer's next print would die on them. Not log.txt either: ai-toolkit
# deletes and reopens that one itself.
_TRAINER_OUTPUT_NAME = "trainer_output.log"
# Windows has no sessions: these give the trainer its own hidden console, so a
# Ctrl+C in the backend's or closing its window does not reach it, and console
# tools it starts open no window (DETACHED_PROCESS would give each one its own).
# 0 elsewhere.
_DETACHED_FLAGS = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
    subprocess, "CREATE_NO_WINDOW", 0
)
# The desktop app stops the backend with `taskkill /T`, which follows parent-pid
# links and no creation flag cuts. So on Windows the trainer is started by a
# launcher that prints its pid and exits: its parent is then gone and the kill
# no longer reaches it. Its stdout and stderr are the launcher's stderr.
_ORPHAN_TRAINER = os.name == "nt"
_ORPHAN_LAUNCHER = (
    "import subprocess, sys\n"
    "trainer = subprocess.Popen(sys.argv[2:], stdin=subprocess.DEVNULL,"
    " stdout=sys.stderr, stderr=subprocess.STDOUT, start_new_session=True,"
    " creationflags=int(sys.argv[1]))\n"
    "print(trainer.pid, flush=True)\n"
)
# How long the launcher may take to start the trainer (seconds).
_ORPHAN_LAUNCH_TIMEOUT_SECONDS = 60.0

# The trainer's CUDA allocator setting, unless the owner set their own.
_ALLOC_CONF = "expandable_segments:True"

# The metric key the trainer uses for the training loss (SEAM.md §progress).
_LOSS_METRIC_KEY = "loss/loss"

# Cooperative-cancel grace before escalating to signals (seconds).
_CANCEL_GRACE_SECONDS = 10.0
# Wait between SIGTERM and SIGKILL when escalating (seconds).
_SIGTERM_GRACE_SECONDS = 5.0
# busy_timeout for sqlite reads of the (WAL) loss DB (milliseconds).
_SQLITE_BUSY_TIMEOUT_MS = 2000
# Retries for a transiently locked sqlite read.
_SQLITE_READ_RETRIES = 3

# Max length of a sanitised filesystem stem (keeps the save_root path well under
# any OS component limit even after the trainer appends "_NNNNNNNNN.safetensors").
_MAX_JOB_STEM_LEN = 64
# Characters allowed verbatim in a filesystem stem; everything else is collapsed.
_JOB_STEM_DISALLOWED = re.compile(r"[^A-Za-z0-9._-]+")
# A run of two or more separator chars (., -, _) collapsed to a single "_". This
# also dissolves any ".." that survived char-collapsing (e.g. from "../" → ".._"),
# so no traversal segment can reach the stem; an isolated single ".", "-", or "_"
# between alphanumerics (as in "good_name-1.2") is preserved.
_JOB_STEM_SEP_RUN = re.compile(r"[._-]{2,}")
# Leading/trailing separators to strip so the stem is never "..", a hidden file,
# or a name that argv-parses as a flag ("-rf").
_JOB_STEM_STRIP = "._-"


def safe_job_stem(name: str, job_id: str) -> str:
    """Map a display job name to a filesystem-safe single-component stem.

    The result is used as the on-disk ``save_root`` directory component
    (``<training_folder>/<stem>``) and the checkpoint file stem
    (``<stem>.safetensors``), and as ai-toolkit's own ``config.name`` (which the
    trainer joins to ``training_folder`` to compute the *same* ``save_root``).
    It is therefore the one value that turns a user-supplied name into a path, so
    it must never let the name steer the layout outside the per-job work dir
    (e.g. ``../escape`` or ``a/b``).

    The mapping is pure and deterministic in ``(name, job_id)`` so a handle
    reconstructed later (cancel / reattach / reconcile) from the stored display
    name resolves the identical path.

    Rules:

    - Keep ``[A-Za-z0-9._-]``; replace every run of other characters (slashes,
      whitespace, emoji, …) with a single ``_``.
    - Collapse any run of two or more separators (``.`` ``-`` ``_``) to one
      ``_`` (this dissolves any ``..`` traversal segment that survived above),
      then strip leading/trailing separators (defeats hidden files and
      flag-looking stems).
    - Cap at :data:`_MAX_JOB_STEM_LEN` characters.
    - **Fall back to the job uuid** if nothing usable remains (e.g. the name was
      empty, all-whitespace, or all-disallowed). The uuid is itself a safe stem.

    Args:
        name: The user-supplied display name (``training_run.name``).
        job_id: The job uuid, used as the safe fallback stem.

    Returns:
        A non-empty filesystem-safe stem containing only ``[A-Za-z0-9._-]`` with
        no leading/trailing separator and length ``<= _MAX_JOB_STEM_LEN``.
    """
    stem = _JOB_STEM_DISALLOWED.sub("_", name or "")
    stem = _JOB_STEM_SEP_RUN.sub("_", stem)
    stem = stem.strip(_JOB_STEM_STRIP)
    stem = stem[:_MAX_JOB_STEM_LEN].strip(_JOB_STEM_STRIP)
    if not stem:
        # Nothing usable survived sanitisation; the uuid is always a safe stem.
        return job_id
    return stem


class LocalAiToolkit(TrainerBackend):
    """Drive a local ai-toolkit checkout to train a LoRA.

    Spawns ``run.py`` from the ai-toolkit repo as a subprocess in its own
    process group, then reads progress back from the two sqlite files the
    trainer writes (the per-job ``Job`` table and the trainer's ``loss_log.db``)
    plus the output directory. All of that behaviour is the documented seam in
    ``SEAM.md``; this class is the sole owner of it.

    Attributes:
        ai_toolkit_repo_path: Filesystem path to the ai-toolkit checkout
            (the process ``cwd``).
        ai_toolkit_python: Python interpreter used to run the trainer.
        run_script: Entry script run by that interpreter, relative to the repo
            (defaults to ``run.py``; overridable so tests can point at a stub).
    """

    def __init__(
        self,
        ai_toolkit_repo_path: str,
        ai_toolkit_python: str,
        run_script: str = "run.py",
    ) -> None:
        """Initialise the adapter.

        Args:
            ai_toolkit_repo_path: Path to the ai-toolkit checkout (process cwd).
            ai_toolkit_python: Python interpreter to launch the trainer with.
            run_script: Entry script relative to the repo. Defaults to
                ``run.py``; tests pass a stub.
        """
        self.ai_toolkit_repo_path = ai_toolkit_repo_path
        self.ai_toolkit_python = ai_toolkit_python
        self.run_script = run_script
        # Popen handles for children we launched in this process, keyed by pid.
        # Held so we can reap them (poll()) and avoid mistaking a finished-but-
        # unreaped child (a zombie) for a live process. Empty after a restart,
        # and always on Windows (the trainer is not our child there), which is
        # why liveness also has a zombie-aware PID probe fallback.
        self._processes: dict[int, subprocess.Popen] = {}

    # ------------------------------------------------------------------
    # Path layout (single source of truth)
    # ------------------------------------------------------------------

    @staticmethod
    def handle_for(
        work_dir: str,
        job_id: str,
        job_name: str,
        pid: int | None = None,
        training_folder: str | None = None,
    ) -> TrainingHandle:
        """Derive the full on-disk handle layout for a job (paths only).

        This is the single source of truth for where every per-job file lives.
        :meth:`prepare` uses it to lay out a fresh run, and callers that need to
        reconstruct a handle after a restart (for cancel/reconcile) without
        having persisted the whole handle can rebuild it from just the work dir,
        job id, job name, and PID. Pure path computation: it touches no disk.

        The display *job_name* is run through :func:`safe_job_stem` before it
        becomes a path component, so an odd or hostile name (``../escape``,
        ``a/b``, whitespace, emoji) can never steer the layout outside the
        per-job ``work_dir``. The sanitisation is deterministic in
        ``(job_name, job_id)``, so a handle rebuilt later from the stored display
        name (cancel / reattach / reconcile) resolves the identical ``save_root``.

        Args:
            work_dir: The per-job working directory PixlStash owns.
            job_id: The job id (``Job.id`` / ``AITK_JOB_ID``).
            job_name: The display job name. Sanitised to the filesystem stem that
                defines ``save_root`` and the checkpoint stem.
            pid: The recorded trainer PID, or ``None`` if not (yet) launched.
            training_folder: The shelf's ai-toolkit folder the run is written
                into. ``None`` keeps the run under ``<work_dir>/output``. The
                folder is shared by every run, so the stem then carries the
                first eight characters of the job id: two runs with one name
                must not share a ``save_root``.

        Returns:
            A :class:`TrainingHandle` with all paths resolved.
        """
        work_dir = os.path.abspath(work_dir)
        if training_folder is None:
            training_folder = os.path.join(work_dir, "output")
            stem = safe_job_stem(job_name, job_id)
        else:
            training_folder = os.path.abspath(training_folder)
            stem = f"{safe_job_stem(job_name, job_id)}_{job_id[:8]}"
        save_root = os.path.join(training_folder, stem)
        return TrainingHandle(
            job_id=job_id,
            work_dir=work_dir,
            training_folder=training_folder,
            save_root=save_root,
            sqlite_db_path=os.path.join(work_dir, "job.db"),
            loss_db_path=os.path.join(save_root, "loss_log.db"),
            config_path=os.path.join(work_dir, ".job_config.json"),
            log_path=os.path.join(save_root, "log.txt"),
            pid=pid,
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def prepare(self, spec: TrainingSpec) -> TrainingHandle:
        """Lay out the work dir, create the per-job DB, and render the config.

        Creates ``<work_dir>/output`` (training folder) and
        ``<training_folder>/<stem>`` (save root, where ``stem`` is the sanitised
        job name), rotates any prior ``log.txt`` into ``logs/``, creates the
        per-job ``Job`` table with a single ``queued`` row, and renders +
        atomically writes the job config.

        The rendered config's ``name`` is set to the *same* sanitised stem that
        forms ``save_root``: ai-toolkit recomputes ``save_root`` as
        ``training_folder/config.name``, so the config name and our directory
        layout must agree on the sanitised stem, never the raw display name.

        Args:
            spec: The run specification.

        Returns:
            A fully-resolved :class:`TrainingHandle` with ``pid=None``.
        """
        handle = self.handle_for(
            spec.work_dir,
            spec.job_id,
            spec.job_name,
            training_folder=spec.training_folder,
        )
        training_folder = handle.training_folder
        save_root = handle.save_root
        sqlite_db_path = handle.sqlite_db_path
        config_path = handle.config_path
        log_path = handle.log_path
        # save_root's basename IS the sanitised stem (handle_for applied it);
        # reuse it so config.name and the on-disk layout share one value.
        stem = os.path.basename(save_root)

        os.makedirs(training_folder, exist_ok=True)
        os.makedirs(save_root, exist_ok=True)

        self._rotate_existing_log(log_path)
        self._create_job_db(sqlite_db_path, spec.job_id)

        config = render_job_config(
            spec.template_path,
            job_name=stem,
            training_folder=training_folder,
            sqlite_db_path=sqlite_db_path,
            trigger_word=spec.trigger_word,
            dataset_dir=spec.dataset_dir,
            overrides=spec.config_overrides,
            locked=spec.locked,
        )
        # write_json_atomic uses json.dump, whose default ensure_ascii=True
        # already escapes non-ASCII to \uXXXX, matching the SEAM requirement.
        write_json_atomic(config_path, config)
        logger.info(
            "Prepared training job %s: save_root=%s config=%s",
            spec.job_id,
            save_root,
            config_path,
        )

        return handle

    def submit(self, handle: TrainingHandle, spec: TrainingSpec) -> None:
        """Spawn the trainer process for a prepared handle.

        Launches ``<python> <run_script> <config> --log <log>`` from the repo
        in its own process group (so cancel can signal the group), records the
        PID onto *handle* and into ``pid.txt``, and marks the job ``running``.
        Its stdout/stderr go to ``<work_dir>/trainer_output.log`` and its stdin
        is closed, so it outlives the backend's streams. On Windows a launcher
        starts it and exits, so it also outlives ``taskkill /T`` on the backend.

        ``HF_TOKEN`` is forwarded to the child env when set but is NEVER logged;
        only the names of the env keys we set are logged.

        Args:
            handle: The handle returned by :meth:`prepare`.
            spec: The run spec (for GPU selection and the optional HF token).
        """
        env = trainer_env()
        env["AITK_JOB_ID"] = spec.job_id
        env["CUDA_VISIBLE_DEVICES"] = spec.gpu_ids
        env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
        set_env_keys = ["AITK_JOB_ID", "CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER"]
        if not env.get("PYTORCH_ALLOC_CONF") and not env.get("PYTORCH_CUDA_ALLOC_CONF"):
            # Without it a VAE encode needing one large block fails beside
            # gigabytes reserved but split (Krea 2 at 1024 on a 32 GB card).
            # The old name, because torch before 2.9 reads no other.
            env["PYTORCH_CUDA_ALLOC_CONF"] = _ALLOC_CONF
            set_env_keys.append("PYTORCH_CUDA_ALLOC_CONF")
        if spec.hf_token:
            env["HF_TOKEN"] = spec.hf_token
            set_env_keys.append("HF_TOKEN")

        cmd = [
            self.ai_toolkit_python,
            self.run_script,
            handle.config_path,
            "--log",
            handle.log_path,
        ]
        # Log the argv and the env *key names* only, never the HF token value.
        logger.info(
            "Launching trainer job %s: cmd=%s cwd=%s env_overrides=%s",
            handle.job_id,
            cmd,
            self.ai_toolkit_repo_path,
            set_env_keys,
        )

        output_path = os.path.join(handle.work_dir, _TRAINER_OUTPUT_NAME)
        try:
            # The child keeps its own copy of the descriptor; ours closes here.
            with open(output_path, "wb") as output:
                if _ORPHAN_TRAINER:
                    pid = self._launch_orphan(cmd, env, output)
                else:
                    process = subprocess.Popen(  # noqa: S603 - argv list, never shell
                        cmd,
                        cwd=self.ai_toolkit_repo_path,
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                        creationflags=_DETACHED_FLAGS,
                    )
                    pid = process.pid
                    self._processes[pid] = process
        except (OSError, subprocess.SubprocessError) as exc:
            logger.error(
                "Failed to launch trainer job %s (output %s): %s",
                handle.job_id,
                output_path,
                exc,
            )
            self._set_job_status(handle.sqlite_db_path, handle.job_id, "error")
            raise

        handle.pid = pid
        self._write_pid_file(handle.work_dir, pid)
        self._set_job_status(handle.sqlite_db_path, handle.job_id, "running")
        logger.info("Trainer job %s started with pid %d", handle.job_id, pid)

    def progress(self, handle: TrainingHandle) -> TrainingProgress:
        """Read a fresh progress snapshot from the Job table, loss DB, and disk.

        Robust by design: a locked or missing loss DB yields an empty loss curve
        (logged at warning level) rather than an exception, so a polling loop
        never crashes.

        Args:
            handle: The job handle.

        Returns:
            A :class:`TrainingProgress` snapshot.
        """
        status, info, step, speed = self._read_job_row(handle)
        loss_curve, loss_max_step = self._read_loss_db(handle)
        # Prefer the further-along of the two step sources.
        if loss_max_step is not None:
            step = max(step, loss_max_step)
        checkpoints, final_checkpoint = self._scan_checkpoints(handle)
        samples = self._scan_samples(handle)
        total_steps = self._read_total_steps(handle)

        return TrainingProgress(
            status=status,
            info=info,
            step=step,
            total_steps=total_steps,
            speed=speed,
            loss_curve=loss_curve,
            samples=samples,
            checkpoints=checkpoints,
            final_checkpoint=final_checkpoint,
        )

    def cancel(self, handle: TrainingHandle) -> None:
        """Cancel a running job, cooperatively first then by signal.

        Sets ``Job.stop=1`` (the trainer polls this every step and exits
        cleanly). If the process is still alive after a grace period, escalates
        to ending it, then to killing it (see :meth:`_signal_trainer`). Each
        step is logged.

        Args:
            handle: The job handle.
        """
        logger.info("Cancelling training job %s (cooperative stop)", handle.job_id)
        self._set_stop_flag(handle.sqlite_db_path, handle.job_id)

        if handle.pid is None:
            logger.info(
                "Job %s has no recorded pid; cooperative stop only", handle.job_id
            )
            return

        if not self._is_this_jobs_trainer(handle):
            # A pid persisted across a restart. Our trainer is gone and the number
            # now belongs to another process: never signal it. Stop the job here.
            if self._pid_alive(handle.pid):
                logger.warning(
                    "Job %s: pid %d is not this job's trainer (its cmdline does not "
                    "name %s and %s); not signalling it. Marking the job stopped.",
                    handle.job_id,
                    handle.pid,
                    self.run_script,
                    handle.config_path,
                )
                self._set_job_status(handle.sqlite_db_path, handle.job_id, "stopped")
            else:
                logger.info(
                    "Job %s: trainer pid %d already exited", handle.job_id, handle.pid
                )
            return

        if self._wait_for_exit(handle, _CANCEL_GRACE_SECONDS):
            logger.info("Job %s stopped cooperatively", handle.job_id)
            return

        logger.warning(
            "Job %s did not stop within %.0fs; terminating the trainer",
            handle.job_id,
            _CANCEL_GRACE_SECONDS,
        )
        self._signal_trainer(handle, force=False)

        if self._wait_for_exit(handle, _SIGTERM_GRACE_SECONDS):
            logger.info("Job %s terminated", handle.job_id)
            return

        logger.warning(
            "Job %s still alive after terminating it; killing the trainer",
            handle.job_id,
        )
        self._signal_trainer(handle, force=True)

    def reconcile(self, handle: TrainingHandle) -> TrainingProgress:
        """Re-derive a job's state after a restart.

        Reads a fresh progress snapshot, then resolves a process that is no
        longer alive but whose persisted status is still non-terminal: if the
        trainer recorded ``completed``/``error`` it is trusted, otherwise the
        run is reported as ``stopped`` when a stop was requested (ai-toolkit
        reads ``Job.stop`` only between steps, so a cancel while it loads the
        model or caches ends in a signal) and ``error`` (interrupted) when not.

        Args:
            handle: The job handle as persisted before the restart.

        Returns:
            A reconciled :class:`TrainingProgress` snapshot.
        """
        progress = self.progress(handle)
        terminal = {"completed", "error", "stopped"}
        if not self.is_alive(handle) and progress.status not in terminal:
            if self._stop_requested(handle.sqlite_db_path, handle.job_id):
                logger.info(
                    "Job %s ended while %r after a stop was requested; stopped",
                    handle.job_id,
                    progress.info,
                )
                progress.status = "stopped"
                progress.info = "Stopped"
                return progress
            logger.warning(
                "Job %s found dead with non-terminal status %r after restart; "
                "marking as error (interrupted)",
                handle.job_id,
                progress.status,
            )
            progress.status = "error"
            if not progress.info:
                progress.info = "Interrupted (process not running after restart)"
        return progress

    def is_alive(self, handle: TrainingHandle) -> bool:
        """Return whether the recorded trainer PID is still running.

        If we launched the process in this run, its ``Popen`` is polled (which
        also reaps it once it has exited, so it never lingers as a zombie). For
        a PID inherited across a restart, or one started through the Windows
        launcher (no ``Popen`` either way), a zombie-aware probe is used: a
        finished-but-unreaped process counts as dead.

        Args:
            handle: The job handle.

        Returns:
            ``True`` if the process is genuinely running, else ``False``
            (including when no PID is recorded or the process is a zombie).
        """
        if handle.pid is None:
            return False
        return self._pid_running(handle)

    # ------------------------------------------------------------------
    # Private helpers: process / filesystem
    # ------------------------------------------------------------------

    def _launch_orphan(self, cmd: list[str], env: dict[str, str], output) -> int:
        """Start the trainer through :data:`_ORPHAN_LAUNCHER` and return its pid.

        The trainer is not our child, so no ``Popen`` is kept: it is tracked
        like a pid inherited across a restart.

        Args:
            cmd: The trainer's argv.
            env: The trainer's environment.
            output: The open ``trainer_output.log``, the launcher's stderr.

        Returns:
            The trainer's pid.

        Raises:
            OSError: The launcher failed or printed no pid.
            subprocess.TimeoutExpired: The launcher did not finish in time
                and printed no pid.
        """
        try:
            done = subprocess.run(  # noqa: S603 - argv list, never shell
                [
                    self.ai_toolkit_python,
                    "-c",
                    _ORPHAN_LAUNCHER,
                    str(_DETACHED_FLAGS),
                    *cmd,
                ],
                cwd=self.ai_toolkit_repo_path,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=output,
                creationflags=_DETACHED_FLAGS,
                timeout=_ORPHAN_LAUNCH_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            # One that printed the pid and then hung has started the trainer:
            # keep tracking it rather than leave it running unseen.
            printed = (exc.stdout or b"").decode("ascii", "replace").strip()
            if not printed.isdigit():
                raise
            logger.warning(
                "Trainer launcher hung after starting pid %s and was killed", printed
            )
            return int(printed)
        printed = done.stdout.decode("ascii", "replace").strip()
        if done.returncode != 0 or not printed.isdigit():
            raise OSError(
                f"trainer launcher exited {done.returncode} printing {printed!r}; "
                f"its error is in {output.name}"
            )
        return int(printed)

    @staticmethod
    def _rotate_existing_log(log_path: str) -> None:
        """Move a pre-existing ``log.txt`` into a sibling ``logs/`` archive dir.

        Args:
            log_path: Path the trainer will write its log to.
        """
        if not os.path.exists(log_path):
            return
        logs_dir = os.path.join(os.path.dirname(log_path), "logs")
        os.makedirs(logs_dir, exist_ok=True)
        rotated = os.path.join(logs_dir, f"log.{int(time.time())}.txt")
        try:
            os.replace(log_path, rotated)
            logger.info("Rotated prior trainer log to %s", rotated)
        except OSError as exc:
            logger.warning("Failed to rotate prior trainer log %s: %s", log_path, exc)

    @staticmethod
    def _write_pid_file(work_dir: str, pid: int) -> None:
        """Record the launched PID into ``<work_dir>/pid.txt``.

        Args:
            work_dir: The per-job work directory.
            pid: The process group leader PID.
        """
        pid_path = os.path.join(work_dir, "pid.txt")
        try:
            with open(pid_path, "w", encoding="utf-8") as handle:
                handle.write(str(pid))
        except OSError as exc:
            logger.warning("Failed to write pid file %s: %s", pid_path, exc)

    def _pid_running(self, handle: TrainingHandle) -> bool:
        """Return whether the trainer recorded on *handle* is genuinely running.

        Zombies count as dead. Prefers the owned ``Popen`` (``poll()`` reaps a
        finished child). For an inherited PID the process must also be this
        job's trainer: a pid reused by an unrelated process is not the trainer.

        Args:
            handle: The job handle; its ``pid`` is probed.

        Returns:
            ``True`` if the trainer is running, else ``False``.
        """
        process = self._processes.get(handle.pid)
        if process is not None:
            if process.poll() is None:
                return True
            self._processes.pop(handle.pid, None)
            return False
        if not self._pid_alive(handle.pid):
            return False
        # Where the cmdline cannot be read, liveness is all there is: calling a
        # running trainer dead would finalise it mid-run. Only a signal needs
        # the identity, and cancel refuses one it cannot verify.
        return self._is_our_trainer(handle) is not False

    def _is_this_jobs_trainer(self, handle: TrainingHandle) -> bool:
        """Return whether the recorded pid is a trainer this backend started.

        A ``Popen`` we hold proves it. Otherwise the pid must still be the
        trainer: its cmdline names this backend's run script and the job's
        config. Refuses where the cmdline cannot be read, which is logged.

        Args:
            handle: The job handle.

        Returns:
            ``True`` if the pid is this job's trainer, else ``False``.
        """
        if handle.pid in self._processes:
            return True
        ours = self._is_our_trainer(handle)
        if ours is None:
            logger.warning(
                "Cannot verify pid %s is job %s's trainer on this platform; "
                "not signalling it.",
                handle.pid,
                handle.job_id,
            )
        return ours is True

    def _is_our_trainer(self, handle: TrainingHandle) -> bool | None:
        """Check the pid's cmdline (through psutil) for this job's trainer launch.

        Args:
            handle: The job handle; its ``pid`` and ``config_path`` are compared.

        Returns:
            ``True`` if the cmdline holds the run script and the job's config
            path as arguments, ``False`` if it does not, ``None`` if it cannot
            be read (access denied, or the process has just gone).
        """
        try:
            argv = psutil.Process(handle.pid).cmdline()
        except psutil.Error as exc:
            logger.debug(
                "Cannot read the cmdline of pid %s (job %s): %s",
                handle.pid,
                handle.job_id,
                exc,
            )
            return None
        return self.run_script in argv and handle.config_path in argv

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        """Return whether *pid* refers to a live, non-zombie process.

        Asks psutil, never ``os.kill``: on Windows ``os.kill`` calls
        TerminateProcess for any signal other than the console events, so a
        ``os.kill(pid, 0)`` probe would kill the trainer it inspects. psutil only
        reads process state, works without ``/proc`` (macOS, Windows), and
        reports a finished-but-unreaped child as a zombie rather than alive, so
        a completed job is not kept "running".

        Args:
            pid: Process id to probe.

        Returns:
            ``True`` if the process exists and is not a zombie, else ``False``.
        """
        try:
            status = psutil.Process(pid).status()
        except psutil.NoSuchProcess:
            return False
        except psutil.AccessDenied:
            # Exists but owned by another user, still "alive".
            return True
        except psutil.Error as exc:
            logger.warning("Liveness probe failed for pid %d: %s", pid, exc)
            return False
        return status not in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)

    def _wait_for_exit(self, handle: TrainingHandle, timeout: float) -> bool:
        """Poll until the job's trainer stops running or *timeout* elapses.

        Args:
            handle: The job handle whose trainer is waited on.
            timeout: Maximum seconds to wait.

        Returns:
            ``True`` if the trainer stopped within the timeout, else ``False``.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._pid_running(handle):
                return True
            time.sleep(0.05)
        return not self._pid_running(handle)

    def _signal_trainer(self, handle: TrainingHandle, *, force: bool) -> None:
        """End the job's trainer, after checking it still is the trainer.

        The check runs immediately before the signal, so a pid reused during the
        grace period is never signalled. POSIX signals the process group;
        Windows has no process groups, ``killpg`` or ``SIGKILL``, so there the
        trainer's process tree is ended through psutil.

        Args:
            handle: The job handle.
            force: Kill (``SIGKILL``) rather than terminate (``SIGTERM``).
        """
        if not self._is_this_jobs_trainer(handle):
            logger.warning(
                "Job %s: pid %d is no longer this job's trainer; not ending it",
                handle.job_id,
                handle.pid,
            )
            return
        if os.name == "nt":
            self._end_process_tree(handle.pid, force=force)
        else:
            self._signal_process_group(
                handle.pid, signal.SIGKILL if force else signal.SIGTERM
            )

    @staticmethod
    def _end_process_tree(pid: int, *, force: bool) -> None:
        """Terminate (or kill) *pid* and every process it started.

        The descendants are listed before anything is ended: once the trainer
        is gone its data-loader workers no longer show as its children. On
        Windows both terminate and kill are TerminateProcess.

        Args:
            pid: The trainer's pid.
            force: Kill rather than terminate.
        """
        try:
            trainer = psutil.Process(pid)
        except psutil.Error as exc:
            logger.info("Trainer pid %d is gone or unreadable: %s", pid, exc)
            return
        try:
            tree = [*trainer.children(recursive=True), trainer]
        except psutil.Error as exc:
            logger.warning(
                "Cannot list the processes trainer pid %d started (%s); ending the "
                "trainer alone, its workers may outlive it",
                pid,
                exc,
            )
            tree = [trainer]
        for proc in tree:
            try:
                (proc.kill if force else proc.terminate)()
            except psutil.NoSuchProcess:
                logger.info(
                    "Process %d of trainer pid %d already exited", proc.pid, pid
                )
            except psutil.Error as exc:
                logger.warning(
                    "Failed to end process %d of trainer pid %d: %s", proc.pid, pid, exc
                )

    @staticmethod
    def _signal_process_group(pid: int, sig: int) -> None:
        """Send *sig* to the process group led by *pid* (POSIX only).

        Args:
            pid: Process group leader PID (the child was started with
                ``start_new_session=True``, so pid == pgid).
            sig: Signal number to send (``SIGTERM`` / ``SIGKILL``).
        """
        try:
            os.killpg(os.getpgid(pid), sig)
        except ProcessLookupError:
            # Already gone: nothing to signal.
            logger.info("Process group for pid %d already exited", pid)
        except OSError as exc:
            logger.warning(
                "Failed to send signal %s to process group of pid %d: %s",
                sig,
                pid,
                exc,
            )

    def _scan_checkpoints(self, handle: TrainingHandle) -> tuple[list[str], str | None]:
        """Discover checkpoint files in ``save_root``.

        Per SEAM.md, intermediate checkpoints are
        ``<job_name>_<step:09d>.safetensors`` and the final one is
        ``<job_name>.safetensors``.

        Args:
            handle: The job handle.

        Returns:
            A ``(checkpoints, final_checkpoint)`` tuple where ``checkpoints`` is
            the intermediate list (oldest first) and ``final_checkpoint`` is the
            absolute path to the final file or ``None``.
        """
        if not os.path.isdir(handle.save_root):
            return [], None
        # The checkpoint file stem is the job_name, which is the final path
        # component of save_root (save_root == training_folder/job_name).
        job_name = os.path.basename(handle.save_root)
        final_path = os.path.join(handle.save_root, f"{job_name}.safetensors")
        stamped: list[tuple[float, str]] = []
        for path in glob.glob(os.path.join(handle.save_root, "*.safetensors")):
            try:
                stamped.append((os.path.getmtime(path), path))
            except FileNotFoundError:
                # The trainer pruned it between the glob and the stat. It is no
                # longer a checkpoint, so it is left out rather than crashing
                # the poll loop.
                logger.warning(
                    "Checkpoint %s vanished while scanning %s (pruned by the trainer?)",
                    path,
                    handle.save_root,
                )
        all_files = [path for _, path in sorted(stamped, key=lambda item: item[0])]
        final_checkpoint = final_path if os.path.exists(final_path) else None
        intermediate = [f for f in all_files if f != final_path]
        return intermediate, final_checkpoint

    @staticmethod
    def _scan_samples(handle: TrainingHandle) -> list[str]:
        """Discover generated sample files under ``save_root/samples``.

        Args:
            handle: The job handle.

        Returns:
            Sorted absolute paths to sample files (empty if none yet).
        """
        samples_dir = os.path.join(handle.save_root, "samples")
        if not os.path.isdir(samples_dir):
            return []
        return sorted(
            f for f in glob.glob(os.path.join(samples_dir, "*")) if os.path.isfile(f)
        )

    # ------------------------------------------------------------------
    # Private helpers: Job table (status / step / speed + cancel)
    # ------------------------------------------------------------------

    @staticmethod
    def _create_job_db(sqlite_db_path: str, job_id: str) -> None:
        """Create the per-job ``Job`` table and insert the single job row.

        The INSERT is mandatory: the trainer's UPDATEs are keyed ``WHERE id=?``
        with no upsert, so they silently no-op if the row is absent (SEAM.md).

        Args:
            sqlite_db_path: Path to the per-job DB file to create.
            job_id: The job id used as the ``Job.id`` primary key.
        """
        os.makedirs(os.path.dirname(os.path.abspath(sqlite_db_path)), exist_ok=True)
        conn = sqlite3.connect(sqlite_db_path, timeout=30.0)
        try:
            conn.execute(_JOB_TABLE_SQL)
            # A pass re-prepares in the same work dir, so a job.db made by an
            # older PixlStash lacks what the trainer now polls every step.
            columns = {row[1] for row in conn.execute("PRAGMA table_info(Job)")}
            for flag in _LATER_JOB_FLAGS:
                if flag not in columns:
                    conn.execute(
                        f"ALTER TABLE Job ADD COLUMN {flag} INTEGER NOT NULL DEFAULT 0"
                    )
            conn.execute(
                "INSERT OR REPLACE INTO Job (id, status) VALUES (?, 'queued')",
                (job_id,),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _set_job_status(sqlite_db_path: str, job_id: str, status: str) -> None:
        """Update the job's status column.

        Args:
            sqlite_db_path: Path to the per-job DB.
            job_id: The job id.
            status: New status value.
        """
        try:
            conn = sqlite3.connect(sqlite_db_path, timeout=30.0)
            try:
                conn.execute("UPDATE Job SET status = ? WHERE id = ?", (status, job_id))
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            logger.warning(
                "Failed to set status=%r for job %s: %s", status, job_id, exc
            )

    @staticmethod
    def _set_stop_flag(sqlite_db_path: str, job_id: str) -> None:
        """Set ``Job.stop=1`` to request a cooperative stop.

        Args:
            sqlite_db_path: Path to the per-job DB.
            job_id: The job id.
        """
        try:
            conn = sqlite3.connect(sqlite_db_path, timeout=30.0)
            try:
                conn.execute("UPDATE Job SET stop = 1 WHERE id = ?", (job_id,))
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            logger.warning("Failed to set stop flag for job %s: %s", job_id, exc)

    @staticmethod
    def _stop_requested(sqlite_db_path: str, job_id: str) -> bool:
        """Return whether ``Job.stop`` is set, i.e. :meth:`cancel` asked.

        Args:
            sqlite_db_path: Path to the per-job DB.
            job_id: The job id.

        Returns:
            ``True`` if a stop was requested; ``False`` if not or unreadable.
        """
        if not os.path.exists(sqlite_db_path):
            logger.warning("Job %s has no job DB at %s", job_id, sqlite_db_path)
            return False
        try:
            conn = sqlite3.connect(sqlite_db_path, timeout=5.0)
            try:
                row = conn.execute(
                    "SELECT stop FROM Job WHERE id = ?", (job_id,)
                ).fetchone()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            logger.warning("Failed to read the stop flag of job %s: %s", job_id, exc)
            return False
        return bool(row and row[0])

    def _read_job_row(self, handle: TrainingHandle) -> tuple[str, str, int, str]:
        """Read status/info/step/speed from the Job table.

        Args:
            handle: The job handle.

        Returns:
            A ``(status, info, step, speed)`` tuple. On any read failure returns
            conservative defaults (``("unknown", "", 0, "")``) and logs.
        """
        try:
            conn = sqlite3.connect(handle.sqlite_db_path, timeout=5.0)
            try:
                cursor = conn.execute(
                    "SELECT status, info, step, speed_string FROM Job WHERE id = ?",
                    (handle.job_id,),
                )
                row = cursor.fetchone()
            finally:
                conn.close()
        except sqlite3.Error as exc:
            logger.warning("Failed to read Job row for %s: %s", handle.job_id, exc)
            return "unknown", "", 0, ""

        if row is None:
            logger.warning("No Job row found for %s", handle.job_id)
            return "unknown", "", 0, ""
        status, info, step, speed = row
        return status or "unknown", info or "", int(step or 0), speed or ""

    # ------------------------------------------------------------------
    # Private helpers: loss DB (step + loss curve), WAL-aware/read-only
    # ------------------------------------------------------------------

    def _read_loss_db(self, handle: TrainingHandle) -> tuple[list[dict], int | None]:
        """Read the loss curve and max step from the trainer's loss DB.

        Opens ``loss_log.db`` read-only and WAL-aware (``mode=ro`` +
        ``PRAGMA query_only=1``) with a short busy timeout and a few retries.
        Tolerates a missing or locked DB by returning an empty curve.

        Args:
            handle: The job handle.

        Returns:
            A ``(loss_curve, max_step)`` tuple. ``loss_curve`` is a list of
            ``{"step": int, "loss": float}`` dicts ordered by step; ``max_step``
            is the highest logged step or ``None`` if unavailable.
        """
        if not os.path.exists(handle.loss_db_path):
            return [], None

        uri = sqlite_file_uri(handle.loss_db_path, read_only=True)
        last_exc: sqlite3.Error | None = None
        for attempt in range(_SQLITE_READ_RETRIES):
            try:
                conn = sqlite3.connect(uri, uri=True, timeout=2.0)
                try:
                    conn.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
                    conn.execute("PRAGMA query_only=1")
                    max_row = conn.execute("SELECT MAX(step) FROM steps").fetchone()
                    max_step = (
                        int(max_row[0])
                        if max_row is not None and max_row[0] is not None
                        else None
                    )
                    rows = conn.execute(
                        "SELECT step, value_real FROM metrics "
                        "WHERE key = ? ORDER BY step",
                        (_LOSS_METRIC_KEY,),
                    ).fetchall()
                finally:
                    conn.close()
            except sqlite3.Error as exc:
                last_exc = exc
                time.sleep(0.05 * (attempt + 1))
                continue

            loss_curve = [
                {"step": int(step), "loss": float(value)}
                for step, value in rows
                if value is not None
            ]
            return loss_curve, max_step

        logger.warning(
            "Could not read loss DB %s after %d attempts: %s",
            handle.loss_db_path,
            _SQLITE_READ_RETRIES,
            last_exc,
        )
        return [], None

    # ------------------------------------------------------------------
    # Private helpers: config
    # ------------------------------------------------------------------

    @staticmethod
    def _read_total_steps(handle: TrainingHandle) -> int | None:
        """Read ``train.steps`` from the rendered config for *handle*.

        Args:
            handle: The job handle.

        Returns:
            The configured total step count, or ``None`` if it cannot be read.
        """
        try:
            with open(handle.config_path, encoding="utf-8") as fh:
                config = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "Failed to read total steps from %s: %s", handle.config_path, exc
            )
            return None
        try:
            return int(config["config"]["process"][0]["train"]["steps"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            logger.warning("Config %s has no train.steps: %s", handle.config_path, exc)
            return None
