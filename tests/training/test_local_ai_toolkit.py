"""Integration tests for the LocalAiToolkit adapter, driven by fake_run.py.

These exercise the full prepare → submit → poll → (cancel) lifecycle against a
faithful CPU-only stub of ai-toolkit's ``run.py`` (``fake_run.py`` in this
directory). No GPU, no torch, and no fixed long sleeps: all waits are
poll-with-timeout loops so the suite runs in well under a second.
"""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace

import psutil
import pytest

from pixlstash.training.backend import TrainingSpec
from pixlstash.training.local_ai_toolkit import LocalAiToolkit, safe_job_stem

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
# A trimmed, fast template (12 steps, save_every 4) used to drive the stub.
# We adjust steps/save_every per test by writing a derived template, rather
# than via config_overrides: deep_merge replaces a list (process[]) wholesale,
# so patching process[0] would drop the placeholders it carries, and that
# wholesale-replacement contract is covered in test_config_render.py instead.
_FAST_TEMPLATE_PATH = os.path.join(_THIS_DIR, "fast_template.json")


def _make_backend() -> LocalAiToolkit:
    """Construct a LocalAiToolkit pointed at the in-tree fake run script."""
    return LocalAiToolkit(
        ai_toolkit_repo_path=_THIS_DIR,
        ai_toolkit_python=sys.executable,
        run_script="fake_run.py",
    )


def _write_template(tmp_path, *, steps: int, save_every: int) -> str:
    """Write a per-test copy of the fast template with the given step config.

    Args:
        tmp_path: Pytest tmp dir to write the derived template into.
        steps: Value for ``train.steps``.
        save_every: Value for ``save.save_every``.

    Returns:
        Path to the written template JSON.
    """
    with open(_FAST_TEMPLATE_PATH, encoding="utf-8") as fh:
        template = json.load(fh)
    process = template["config"]["process"][0]
    process["train"]["steps"] = steps
    process["save"]["save_every"] = save_every
    path = os.path.join(str(tmp_path), "template.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(template, fh)
    return path


def _make_spec(tmp_path, *, steps: int = 12, save_every: int = 4, **kwargs):
    """Build a TrainingSpec with sensible defaults for the fast stub run."""
    dataset_dir = os.path.join(str(tmp_path), "dataset")
    os.makedirs(dataset_dir, exist_ok=True)
    defaults = dict(
        job_id="job-uuid-1234",
        job_name="testjob",
        template_path=_write_template(tmp_path, steps=steps, save_every=save_every),
        dataset_dir=dataset_dir,
        trigger_word="ohwx_person",
        work_dir=os.path.join(str(tmp_path), "work"),
        gpu_ids="0",
        config_overrides=None,
    )
    defaults.update(kwargs)
    return TrainingSpec(**defaults)


def _poll_until(predicate, *, timeout=15.0, interval=0.02):
    """Poll *predicate* until it returns truthy or *timeout* elapses.

    Args:
        predicate: Zero-arg callable; its truthy return ends the wait.
        timeout: Max seconds to wait.
        interval: Sleep between polls.

    Returns:
        The truthy value, or the final (falsy) value on timeout.
    """
    deadline = time.monotonic() + timeout
    value = predicate()
    while not value and time.monotonic() < deadline:
        time.sleep(interval)
        value = predicate()
    return value


def test_prepare_creates_db_row_and_config(tmp_path):
    """prepare() lays out paths, creates the Job table + queued row, writes config."""
    backend = _make_backend()
    spec = _make_spec(tmp_path)

    handle = backend.prepare(spec)

    # Layout matches SEAM.md.
    assert handle.training_folder == os.path.join(handle.work_dir, "output")
    assert handle.save_root == os.path.join(handle.training_folder, "testjob")
    assert handle.sqlite_db_path == os.path.join(handle.work_dir, "job.db")
    assert handle.loss_db_path == os.path.join(handle.save_root, "loss_log.db")
    assert handle.pid is None
    assert os.path.isdir(handle.training_folder)
    assert os.path.isdir(handle.save_root)

    # Config rendered + written, placeholders gone, overrides applied.
    assert os.path.exists(handle.config_path)
    import json

    with open(handle.config_path, encoding="utf-8") as fh:
        config = json.load(fh)
    assert config["config"]["name"] == "testjob"
    assert config["config"]["process"][0]["sqlite_db_path"] == handle.sqlite_db_path
    assert config["config"]["process"][0]["training_folder"] == handle.training_folder
    assert config["config"]["process"][0]["train"]["steps"] == 12

    # Job table exists with exactly one queued row keyed by job_id.
    conn = sqlite3.connect(handle.sqlite_db_path)
    try:
        rows = conn.execute("SELECT id, status FROM Job").fetchall()
        # Each flag the trainer polls every step (DiffusionTrainer), all off: a
        # missing column fails the run after its first step.
        flags = conn.execute(
            "SELECT stop, return_to_queue, save_now, sample_now FROM Job"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("job-uuid-1234", "queued")]
    assert flags == [(0, 0, 0, 0)]


def test_prepare_adds_sample_now_to_a_job_db_made_before_it(tmp_path):
    """A pass re-prepares in the same work dir: an older Job table gains the
    column the trainer polls, and keeps its row."""
    backend = _make_backend()
    spec = _make_spec(tmp_path)
    db_path = backend.handle_for(
        spec.work_dir, spec.job_id, spec.job_name
    ).sqlite_db_path
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE Job (id TEXT NOT NULL PRIMARY KEY, status TEXT NOT NULL "
        "DEFAULT 'stopped', info TEXT NOT NULL DEFAULT '', step INTEGER NOT NULL "
        "DEFAULT 0, speed_string TEXT NOT NULL DEFAULT '', stop INTEGER NOT NULL "
        "DEFAULT 0, return_to_queue INTEGER NOT NULL DEFAULT 0, save_now INTEGER "
        "NOT NULL DEFAULT 0)"
    )
    conn.execute("INSERT INTO Job (id, step) VALUES (?, 250)", (spec.job_id,))
    conn.commit()
    conn.close()

    handle = backend.prepare(spec)

    conn = sqlite3.connect(handle.sqlite_db_path)
    try:
        rows = conn.execute("SELECT status, step, sample_now FROM Job").fetchall()
    finally:
        conn.close()
    assert rows == [("queued", 0, 0)]


def test_config_written_ascii_only(tmp_path):
    """A non-ASCII trigger word is escaped to \\uXXXX in the written config."""
    backend = _make_backend()
    spec = _make_spec(tmp_path, trigger_word="naïve_café")

    handle = backend.prepare(spec)

    with open(handle.config_path, "rb") as fh:
        raw = fh.read()
    # ensure_ascii=True means no raw non-ASCII bytes in the file.
    assert raw.decode("ascii")  # would raise if non-ASCII bytes present
    assert b"\\u00ef" in raw  # 'ï' escaped


def test_submit_spawns_and_records_pid(tmp_path):
    """submit() launches the process, records a pid, and sets status running."""
    backend = _make_backend()
    spec = _make_spec(tmp_path)
    handle = backend.prepare(spec)

    backend.submit(handle, spec)
    try:
        assert handle.pid is not None
        assert handle.pid > 0
        # pid.txt recorded.
        pid_file = os.path.join(handle.work_dir, "pid.txt")
        assert _poll_until(lambda: os.path.exists(pid_file))
        with open(pid_file, encoding="utf-8") as fh:
            assert int(fh.read().strip()) == handle.pid
    finally:
        # Make sure we don't leak the process if assertions fail.
        backend.cancel(handle)


def test_full_run_progresses_to_completed(tmp_path):
    """Polling shows running→completed, advancing step, loss curve, artifacts."""
    backend = _make_backend()
    spec = _make_spec(tmp_path)
    handle = backend.prepare(spec)

    backend.submit(handle, spec)

    # Observe at least one running snapshot with a real step before completion.
    saw_running = _poll_until(
        lambda: (
            backend.progress(handle).status == "running"
            and backend.progress(handle).step >= 1
        )
    )
    assert saw_running, "never observed a running snapshot with step >= 1"

    assert _poll_until(lambda: backend.progress(handle).status == "completed")
    progress = backend.progress(handle)
    assert progress.status == "completed", f"status was {progress.status!r}"
    assert progress.step == 12
    assert progress.total_steps == 12
    assert progress.speed  # non-empty speed string

    # Loss curve populated and ordered by step.
    assert len(progress.loss_curve) == 12
    steps = [point["step"] for point in progress.loss_curve]
    assert steps == sorted(steps)
    assert all("loss" in point for point in progress.loss_curve)

    # Checkpoints (every 4 of 12 steps → 3 intermediate) + final + a sample.
    assert len(progress.checkpoints) == 3
    assert progress.final_checkpoint is not None
    assert progress.final_checkpoint.endswith("testjob.safetensors")
    assert os.path.exists(progress.final_checkpoint)
    assert progress.final_checkpoint not in progress.checkpoints
    assert len(progress.samples) >= 1

    # Process should have exited on its own.
    assert _poll_until(lambda: not backend.is_alive(handle))


def test_cancel_midrun_stops_job_and_kills_process(tmp_path, monkeypatch):
    """cancel() mid-run leaves Job status 'stopped' and the process dead."""
    backend = _make_backend()
    # Slow each step down and use plenty of steps so cancel reliably lands
    # mid-run (the stub inherits this env from the parent at submit time).
    monkeypatch.setenv("FAKE_RUN_STEP_DELAY", "0.02")
    spec = _make_spec(tmp_path, steps=500, save_every=50)
    handle = backend.prepare(spec)

    backend.submit(handle, spec)

    # Wait until it is genuinely running and has advanced at least one step.
    assert _poll_until(
        lambda: (
            backend.progress(handle).status == "running"
            and backend.progress(handle).step >= 1
        )
    ), "job did not reach running state"

    backend.cancel(handle)

    # The process must be dead.
    assert _poll_until(lambda: not backend.is_alive(handle)), "process still alive"

    # Status must be 'stopped' (cooperative), not 'completed'.
    assert _poll_until(lambda: backend.progress(handle).status in {"stopped", "error"})
    progress = backend.progress(handle)
    assert progress.status == "stopped", (
        f"expected cooperative stop, got {progress.status!r}"
    )
    # It stopped before reaching the full step count.
    assert progress.step < 500


def _job_status(handle) -> str:
    """The Job row's status, read from the job's own database."""
    conn = sqlite3.connect(handle.sqlite_db_path)
    try:
        return conn.execute(
            "SELECT status FROM Job WHERE id=?", (handle.job_id,)
        ).fetchone()[0]
    finally:
        conn.close()


def test_cancel_never_signals_a_pid_that_is_not_the_trainer(tmp_path):
    """A pid persisted across a restart may now belong to another process.

    Review F3: the number is read from the database, and after pid reuse cancel
    must not kill that unrelated process group. The sleeper below is that
    process: its cmdline names neither the run script nor the job's config. It
    must survive, and the job is marked stopped.
    """
    backend = _make_backend()
    spec = _make_spec(tmp_path)
    handle = backend.prepare(spec)
    LocalAiToolkit._set_job_status(handle.sqlite_db_path, handle.job_id, "running")
    sleeper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    try:
        handle.pid = sleeper.pid
        backend.cancel(handle)
        assert sleeper.poll() is None, "cancel signalled a process that is not ours"
        assert _job_status(handle) == "stopped"
        assert not backend.is_alive(handle)
    finally:
        sleeper.kill()
        sleeper.wait()


def test_an_unverifiable_inherited_pid_stays_alive_but_is_never_signalled(
    tmp_path, monkeypatch
):
    """Where the cmdline cannot be read (macOS has no /proc), liveness falls back
    to the pid existing - calling a running trainer dead would finalise it
    mid-run - while cancel still refuses to signal what it cannot verify."""
    backend = _make_backend()
    spec = _make_spec(tmp_path)
    handle = backend.prepare(spec)
    LocalAiToolkit._set_job_status(handle.sqlite_db_path, handle.job_id, "running")
    monkeypatch.setattr(LocalAiToolkit, "_is_our_trainer", lambda self, h: None)
    sleeper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    try:
        handle.pid = sleeper.pid
        assert backend.is_alive(handle), "a live, unverifiable trainer read as dead"
        backend.cancel(handle)
        assert sleeper.poll() is None, "cancel signalled a pid it could not verify"
    finally:
        sleeper.kill()
        sleeper.wait()


def test_cancel_still_signals_an_inherited_trainer_it_can_verify(tmp_path, monkeypatch):
    """The positive control for the check above: after a restart, a process whose
    cmdline names this backend's run script and the job's config is our trainer,
    so cancel escalates to SIGTERM and stops it."""
    monkeypatch.setattr(
        "pixlstash.training.local_ai_toolkit._CANCEL_GRACE_SECONDS", 0.2
    )
    backend = _make_backend()
    spec = _make_spec(tmp_path)
    handle = backend.prepare(spec)
    LocalAiToolkit._set_job_status(handle.sqlite_db_path, handle.job_id, "running")
    trainer = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(60)",
            backend.run_script,
            handle.config_path,
        ],
        start_new_session=True,
    )
    try:
        handle.pid = trainer.pid
        assert backend.is_alive(handle)
        backend.cancel(handle)
        assert trainer.wait(timeout=10) == -15, "SIGTERM did not reach the trainer"
    finally:
        if trainer.poll() is None:
            trainer.kill()
            trainer.wait()


def _submit_loading(tmp_path, monkeypatch):
    """A trainer spending a while loading its model, deaf to ``stop`` meanwhile."""
    monkeypatch.setattr(
        "pixlstash.training.local_ai_toolkit._CANCEL_GRACE_SECONDS", 0.2
    )
    monkeypatch.setenv("FAKE_RUN_LOAD_SECONDS", "15")
    backend = _make_backend()
    spec = _make_spec(tmp_path)
    handle = backend.prepare(spec)
    backend.submit(handle, spec)
    assert _poll_until(lambda: backend.progress(handle).info == "Loading model")
    return backend, handle


def test_a_cancel_while_the_model_loads_ends_stopped_not_error(tmp_path, monkeypatch):
    """ai-toolkit reads ``stop`` only between steps, so a cancel during the model
    load ends in a signal with the row still ``running``. We asked for that, so
    it is a stop, not an interrupted run."""
    backend, handle = _submit_loading(tmp_path, monkeypatch)

    backend.cancel(handle)

    assert not backend.is_alive(handle)
    progress = backend.reconcile(handle)
    assert (progress.status, progress.info) == ("stopped", "Stopped")


def test_a_cancel_on_windows_ends_the_trainer_without_posix_signals(
    tmp_path, monkeypatch
):
    """Windows has no ``killpg``, ``getpgid`` or ``SIGKILL``: a cancel that
    outlasts the grace must end the trainer anyway, and raise nothing."""
    backend, handle = _submit_loading(tmp_path, monkeypatch)

    with monkeypatch.context() as windows:
        windows.setattr(os, "name", "nt")
        windows.delattr(os, "killpg")
        windows.delattr(os, "getpgid")
        windows.delattr(signal, "SIGKILL")
        backend.cancel(handle)

    assert not backend.is_alive(handle)
    assert backend.reconcile(handle).status == "stopped"


def test_ending_the_trainer_tree_ends_the_processes_it_started():
    """Without process groups the trainer's own children (data-loader workers)
    are ended one by one, or they would outlive it holding the card."""
    child = "import time; time.sleep(60)"
    parent = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import subprocess, sys, time; "
            f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(60)",
        ]
    )
    trainer = psutil.Process(parent.pid)
    workers = []
    try:
        workers = _poll_until(lambda: trainer.children(recursive=True))
        assert workers, "the trainer never started its worker"

        LocalAiToolkit._end_process_tree(parent.pid, force=False)

        parent.wait(timeout=10)
        _, alive = psutil.wait_procs(workers, timeout=10)
        assert not alive, "the trainer's worker outlived it"
    finally:
        for proc in [*workers, trainer]:
            if proc.is_running():
                proc.kill()
        parent.wait()


@pytest.mark.parametrize(
    "owner_env, expected",
    [
        ({}, {"PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True"}),
        (
            {"PYTORCH_ALLOC_CONF": "backend:cudaMallocAsync"},
            {"PYTORCH_ALLOC_CONF": "backend:cudaMallocAsync"},
        ),
        (
            {"PYTORCH_CUDA_ALLOC_CONF": "max_split_size_mb:512"},
            {"PYTORCH_CUDA_ALLOC_CONF": "max_split_size_mb:512"},
        ),
    ],
)
def test_the_trainer_gets_expandable_segments_unless_the_owner_chose(
    tmp_path, monkeypatch, owner_env, expected
):
    """A fragmented allocator fails a large VAE encode beside free memory."""
    for name in ("PYTORCH_ALLOC_CONF", "PYTORCH_CUDA_ALLOC_CONF"):
        monkeypatch.delenv(name, raising=False)
    for name, value in owner_env.items():
        monkeypatch.setenv(name, value)
    launched = {}

    class _Process:
        pid = 4242

    def fake_popen(cmd, **kwargs):
        launched.update(kwargs["env"])
        return _Process()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    backend = _make_backend()
    spec = _make_spec(tmp_path)
    backend.submit(backend.prepare(spec), spec)

    got = {
        name: launched[name]
        for name in ("PYTORCH_ALLOC_CONF", "PYTORCH_CUDA_ALLOC_CONF")
        if name in launched
    }
    assert got == expected


def test_the_trainer_does_not_inherit_the_backends_python_path_or_secrets(
    tmp_path, monkeypatch
):
    """ai-toolkit's Python must import its own torch, not the overlay's (#2288)."""
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "overlay"))
    monkeypatch.setenv("PYTHONHOME", str(tmp_path / "home"))
    monkeypatch.setenv("PIXLSTASH_DESKTOP_SESSION", "test-session")
    monkeypatch.setenv("CUDA_HOME", "/opt/cuda")
    launched = {}

    class _Process:
        pid = 4242

    def fake_popen(cmd, **kwargs):
        launched.update(kwargs["env"])
        return _Process()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    backend = _make_backend()
    spec = _make_spec(tmp_path)
    backend.submit(backend.prepare(spec), spec)

    assert "PYTHONPATH" not in launched
    assert "PYTHONHOME" not in launched
    assert not [key for key in launched if key.startswith("PIXLSTASH_")]
    assert launched["CUDA_HOME"] == "/opt/cuda"
    assert launched["AITK_JOB_ID"] == spec.job_id


def test_hf_token_never_written_to_log(tmp_path):
    """The HF token value must never appear in the trainer log file."""
    backend = _make_backend()
    secret = "fake-hf-token-value-123"
    spec = _make_spec(tmp_path, hf_token=secret)
    handle = backend.prepare(spec)

    backend.submit(handle, spec)
    try:
        # Let the run finish so the full log is written.
        assert _poll_until(lambda: not backend.is_alive(handle))
        assert os.path.exists(handle.log_path)
        with open(handle.log_path, encoding="utf-8") as fh:
            log_contents = fh.read()
        assert secret not in log_contents
        # But the env key *name* is fine to have appeared.
        assert "HF_TOKEN" in log_contents
    finally:
        backend.cancel(handle)


def test_reconcile_marks_interrupted_when_dead_and_nonterminal(tmp_path):
    """reconcile() flags a dead, non-terminal job as error (interrupted)."""
    backend = _make_backend()
    spec = _make_spec(tmp_path)
    handle = backend.prepare(spec)

    # Simulate a server that launched a job, recorded a (now-dead) pid, and left
    # the Job row 'running' before a crash/restart.
    handle.pid = 999_999_999  # almost certainly not a live pid
    conn = sqlite3.connect(handle.sqlite_db_path)
    try:
        conn.execute(
            "UPDATE Job SET status='running', step=5 WHERE id=?", (handle.job_id,)
        )
        conn.commit()
    finally:
        conn.close()

    assert not backend.is_alive(handle)
    progress = backend.reconcile(handle)
    assert progress.status == "error"
    assert progress.info  # an interrupted reason is filled in


@pytest.fixture(autouse=True)
def _fast_step_delay(monkeypatch):
    """Default the stub to a tiny per-step delay unless a test overrides it."""
    monkeypatch.setenv("FAKE_RUN_STEP_DELAY", "0.002")


# ----------------------------------------------------------------------
# Job-name sanitisation (CSO residual condition 1: a user-supplied name
# must not flow unsanitised into a filesystem path).
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, expected_stem",
    [
        ("../../etc/passwd", "etc_passwd"),
        ("a/b/c", "a_b_c"),
        ("  ", "job-uuid-fallback"),  # all-whitespace → uuid fallback
        ("", "job-uuid-fallback"),  # empty → uuid fallback
        ("...", "job-uuid-fallback"),  # only dots → stripped → uuid fallback
        ("My LoRA 🎨!", "My_LoRA"),  # emoji/space/punct collapse to one "_"
        ("-rf", "rf"),  # leading dash (flag-looking) stripped
        ("good_name-1.2", "good_name-1.2"),  # already safe, preserved verbatim
    ],
)
def test_safe_job_stem_maps_hostile_names(name, expected_stem):
    """safe_job_stem confines any name to a [A-Za-z0-9._-] stem (uuid fallback)."""
    stem = safe_job_stem(name, "job-uuid-fallback")
    assert stem == expected_stem
    assert stem  # never empty
    # No path separators or leading dot/dash survive.
    assert "/" not in stem and "\\" not in stem
    assert os.sep not in stem
    assert stem[0] not in ".-"
    # The stem is a single path component (basename == itself, no parent escape).
    assert os.path.basename(stem) == stem


def test_safe_job_stem_caps_length():
    """An over-long name is capped so save_root stays within OS limits."""
    stem = safe_job_stem("x" * 500, "uuid")
    assert len(stem) <= 64
    assert stem == "x" * 64


def test_prepare_confines_malicious_name_under_work_dir(tmp_path):
    """A traversal-style name lands under work_dir, not outside it."""
    backend = _make_backend()
    spec = _make_spec(tmp_path, job_name="../../etc/passwd")

    handle = backend.prepare(spec)

    work_dir = os.path.abspath(spec.work_dir)
    # save_root must be strictly inside the per-job work dir.
    assert os.path.commonpath([handle.save_root, work_dir]) == work_dir
    assert handle.save_root == os.path.join(work_dir, "output", "etc_passwd")
    assert os.path.isdir(handle.save_root)

    # The rendered config name is the SAME sanitised stem (drives ai-toolkit's
    # own save_root = training_folder/name), never the raw display name.
    with open(handle.config_path, encoding="utf-8") as fh:
        config = json.load(fh)
    assert config["config"]["name"] == "etc_passwd"
    assert "/" not in config["config"]["name"]


def test_malicious_name_run_completes_and_handle_round_trips(tmp_path):
    """A hostile name still completes the run and the handle round-trips.

    Proves the sanitised stem is consistent across (a) prepare's config.name
    (which the stub uses for its save_root), (b) the adapter's save_root /
    checkpoint scan, and (c) a handle reconstructed from the stored display name
    via handle_for (the cancel/reattach/reconcile path).
    """
    backend = _make_backend()
    display_name = "a/b/c"
    spec = _make_spec(tmp_path, job_name=display_name)
    handle = backend.prepare(spec)

    backend.submit(handle, spec)

    assert _poll_until(lambda: backend.progress(handle).status == "completed")
    progress = backend.progress(handle)
    assert progress.status == "completed", f"status was {progress.status!r}"
    # Final checkpoint stem is the sanitised stem, under save_root.
    assert progress.final_checkpoint is not None
    assert progress.final_checkpoint.endswith("a_b_c.safetensors")
    assert os.path.exists(progress.final_checkpoint)
    work_dir = os.path.abspath(spec.work_dir)
    assert os.path.commonpath([progress.final_checkpoint, work_dir]) == work_dir

    # Reconstruct the handle from the stored DISPLAY name (as cancel/reattach do
    # via TrainingService._handle_for_job); it must resolve the identical paths.
    rebuilt = LocalAiToolkit.handle_for(
        handle.work_dir, spec.job_id, display_name, handle.pid
    )
    assert rebuilt.save_root == handle.save_root
    assert rebuilt.loss_db_path == handle.loss_db_path
    assert rebuilt.log_path == handle.log_path
    # The reconstructed handle sees the same finished run.
    rebuilt_progress = backend.progress(rebuilt)
    assert rebuilt_progress.status == "completed"
    assert rebuilt_progress.final_checkpoint == progress.final_checkpoint


def test_pid_alive_never_signals_the_probed_process(monkeypatch):
    """The liveness probe reads process state; it must never send a signal.

    On Windows ``os.kill`` calls TerminateProcess, so the old probe killed the
    trainer it was checking. A live pid answers True with no signal sent.
    """

    def no_signals(pid, sig):
        raise AssertionError(f"liveness probe sent signal {sig} to {pid}")

    monkeypatch.setattr(os, "kill", no_signals)
    assert LocalAiToolkit._pid_alive(os.getpid()) is True


def test_pid_alive_reports_a_reaped_child_as_dead():
    """A child that has exited and been reaped is not alive."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert LocalAiToolkit._pid_alive(child.pid) is False


@pytest.mark.skipif(sys.platform == "win32", reason="waitid is POSIX-only")
def test_pid_alive_reports_an_unreaped_zombie_as_dead():
    """A finished child that nobody has reaped is a zombie, not a running job."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        # WNOWAIT leaves the child a zombie: it has exited but is not reaped.
        os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
        assert LocalAiToolkit._pid_alive(child.pid) is False
    finally:
        child.wait()


def test_scan_checkpoints_survives_a_checkpoint_pruned_mid_scan(tmp_path, monkeypatch):
    """A checkpoint the trainer deletes between the glob and the stat is skipped.

    Crashing here would kill the poll loop, which must never fail on a file the
    trainer is free to prune.
    """
    save_root = tmp_path / "run"
    save_root.mkdir()
    for name in ("run_000000100.safetensors", "run_000000200.safetensors"):
        (save_root / name).write_bytes(b"x")
    gone = str(save_root / "run_000000100.safetensors")
    real_getmtime = os.path.getmtime

    def pruned_getmtime(path):
        if path == gone:
            raise FileNotFoundError(path)
        return real_getmtime(path)

    monkeypatch.setattr(os.path, "getmtime", pruned_getmtime)
    handle = SimpleNamespace(save_root=str(save_root))

    intermediate, final = _make_backend()._scan_checkpoints(handle)

    assert intermediate == [str(save_root / "run_000000200.safetensors")]
    assert final is None
