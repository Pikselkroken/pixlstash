"""The trainer outlives the backend being killed with its process tree (#2382).

The desktop app stops the backend with ``taskkill /T /F``, which ends every
process whose parent-pid chain leads back to it. On Windows the trainer is
therefore started by a launcher that exits, leaving it no parent to be found
through. Gated on Windows as well as Linux: the launcher runs on both, the kill
itself only where ``taskkill`` exists.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys

import psutil
import pytest

from pixlstash.training import local_ai_toolkit
from pixlstash.training.backend import TrainingHandle
from tests.training.test_local_ai_toolkit import (
    _THIS_DIR,
    _job_status,
    _make_backend,
    _make_spec,
    _poll_until,
)

# Submits a run like the backend does, prints the handle, then stays alive so
# the test can kill it the way the desktop app does on quit.
_SUBMIT_AND_WAIT = """
import dataclasses, json, sys, time
from pixlstash.training.backend import TrainingSpec
from pixlstash.training.local_ai_toolkit import LocalAiToolkit
backend = LocalAiToolkit(*sys.argv[1:4])
spec = TrainingSpec(**json.loads(sys.argv[4]))
handle = backend.prepare(spec)
backend.submit(handle, spec)
print(json.dumps(dataclasses.asdict(handle)), flush=True)
time.sleep(120)
"""


def test_the_launcher_leaves_a_trainer_that_is_not_our_child(tmp_path, monkeypatch):
    """The launched trainer runs to completion with no parent link to us."""
    monkeypatch.setattr(local_ai_toolkit, "_ORPHAN_TRAINER", True)
    monkeypatch.setenv("FAKE_RUN_STEP_DELAY", "0.05")
    backend = _make_backend()
    spec = _make_spec(tmp_path, steps=20, save_every=0)
    handle = backend.prepare(spec)
    backend.submit(handle, spec)
    try:
        assert handle.pid not in backend._processes
        assert psutil.Process(handle.pid).ppid() != os.getpid()
        assert backend.is_alive(handle)
        assert _poll_until(lambda: not backend.is_alive(handle), timeout=30)
        assert _job_status(handle) == "completed"
        with open(os.path.join(handle.work_dir, "trainer_output.log")) as fh:
            output = fh.read()
        assert "step 20/20" in output
        assert "progress 20" in output
    finally:
        backend.cancel(handle)


def test_a_failed_launch_marks_the_job_error(tmp_path, monkeypatch):
    """A launcher that prints no pid fails the submit and the job reads error."""
    monkeypatch.setattr(local_ai_toolkit, "_ORPHAN_TRAINER", True)
    monkeypatch.setattr(
        local_ai_toolkit, "_ORPHAN_LAUNCHER", "import sys; sys.exit('no trainer')"
    )
    backend = _make_backend()
    spec = _make_spec(tmp_path)
    handle = backend.prepare(spec)
    with pytest.raises(OSError, match="trainer launcher exited 1"):
        backend.submit(handle, spec)
    assert handle.pid is None
    assert _job_status(handle) == "error"
    with open(os.path.join(handle.work_dir, "trainer_output.log")) as fh:
        assert "no trainer" in fh.read()


@pytest.mark.skipif(os.name != "nt", reason="taskkill is the Windows desktop quit")
def test_a_run_outlives_taskkill_of_the_backend_tree(tmp_path, monkeypatch):
    """``taskkill /T /F`` on the backend, as the app's quit does, spares the run."""
    monkeypatch.setenv("FAKE_RUN_STEP_DELAY", "0.05")
    spec = _make_spec(tmp_path, steps=40, save_every=0)
    launcher = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _SUBMIT_AND_WAIT,
            _THIS_DIR,
            sys.executable,
            "fake_run.py",
            json.dumps(dataclasses.asdict(spec)),
        ],
        cwd=os.path.dirname(os.path.dirname(_THIS_DIR)),
        stdout=subprocess.PIPE,
    )
    try:
        line = launcher.stdout.readline()
        assert line, "the backend printed no handle"
        handle = TrainingHandle(**json.loads(line))
        subprocess.run(["taskkill", "/pid", str(launcher.pid), "/T", "/F"], check=True)
        launcher.wait(timeout=30)
    finally:
        launcher.kill()
        launcher.stdout.close()
    backend = _make_backend()
    try:
        assert backend.is_alive(handle), "taskkill /T ended the trainer"
        assert _poll_until(lambda: not backend.is_alive(handle), timeout=60)
        assert _job_status(handle) == "completed"
    finally:
        backend.cancel(handle)
