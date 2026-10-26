"""Full-lifecycle tests for the TrainingService, GPU-free.

These drive the real :class:`~pixlstash.services.training_service.TrainingService`
end to end against the CPU stub trainer (``fake_run.py``, symlinked as ``run.py``
into a temp "repo", exactly as ``test_local_ai_toolkit.py`` sets it up) and a
real temp hub (:class:`~pixlstash.hub.db.HubDatabase`). Every external
dependency is injected: a stub VRAM lease (recording request/release order), a
capturing ``notify``, a ``config_provider`` returning the temp repo + venv
python, and a ``dataset_builder`` stub that writes fake image/caption pairs.

No GPU, no torch, no long fixed sleeps: all waits are poll-with-timeout loops and
``FAKE_RUN_STEP_DELAY=0`` so the suite runs fast. A completed run is handed to
an injected ``import_run`` callable (the vault's run importer in production), so
the suite records that call instead of touching a shelf.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from pixlstash.event_types import EventType
from pixlstash.hub import training_runs
from pixlstash.hub.db import now_iso
from pixlstash.services.training_service import TrainingService
from pixlstash.training.local_ai_toolkit import LocalAiToolkit
from pixlstash.vault import Vault

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_FAST_TEMPLATE_PATH = os.path.join(_THIS_DIR, "fast_template.json")
# The library every run in this module belongs to, and the vault ids the
# runs name. Sets and characters are vault rows, but a run only stores their
# ids, so none of them need to exist for the service to train.
_LIBRARY_UUID = "library-under-test"
_SET_ID = 11
# What the route records from the resolver; the service reads its needs.
_RESOLUTION = {"preset": "zimage-turbo@1", "vram_mb": 12000, "ram_mb": 16000}
_CHARACTER_ID = 22


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _poll_until(predicate, *, timeout=20.0, interval=0.02):
    """Poll *predicate* until truthy or *timeout* elapses; return last value."""
    deadline = time.monotonic() + timeout
    value = predicate()
    while not value and time.monotonic() < deadline:
        time.sleep(interval)
        value = predicate()
    return value


def _write_template(dest_dir, *, steps: int, save_every: int) -> str:
    """Write a derived copy of the fast template with the given step config."""
    with open(_FAST_TEMPLATE_PATH, encoding="utf-8") as fh:
        template = json.load(fh)
    process = template["config"]["process"][0]
    process["train"]["steps"] = steps
    process["save"]["save_every"] = save_every
    path = os.path.join(dest_dir, "template.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(template, fh)
    return path


class _StubLease:
    """Stub arbiter and its lease, recording request/release order.

    ``request`` stands in for ``request_gpu`` and answers this object as the
    lease; ``wait`` answers whether it was granted.
    """

    device = None
    #: Set on a lease the arbiter has already granted to the worker.
    held = False

    def __init__(self, grant: bool = True):
        self.events: list[str] = []
        self.grant = grant
        self.acquire_count = 0
        self.release_count = 0
        self.progress_calls: list[dict] = []

    def request(
        self, label: str, job_uuid: str, vram_mb: int, ram_mb: int
    ) -> "_StubLease":
        self.acquire_count += 1
        self.events.append("acquire")
        self.need = (vram_mb, ram_mb)
        return self

    def wait(self, cancel_event=None, timeout=None) -> bool:
        return self.grant

    def release(self, *, only_waiting: bool = False) -> None:
        if only_waiting and self.held:
            return  # the arbiter's only_waiting: a granted lease stays put
        self.release_count += 1
        self.events.append("release")

    def set_progress(self, **fields) -> None:
        self.progress_calls.append(fields)


class _RecordingImport:
    """Stands in for the vault's run importer: records each call, lands nothing.

    ``error`` makes the call raise, which is how a failed shelf import reaches
    the service.
    """

    def __init__(self, error: Exception | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.error = error

    def __call__(self, run_dir: str, info: dict) -> dict:
        self.calls.append((run_dir, dict(info)))
        if self.error is not None:
            raise self.error
        return {"stack_id": 7, "imported": 1, "cover_sha256": "a" * 64}


class _CapturingNotify:
    """Capture emitted (event_type, data) tuples."""

    def __init__(self):
        self.events: list[tuple] = []

    def __call__(self, event_type, data=None):
        self.events.append((event_type, data))

    def plugin_progress(self) -> list[dict]:
        return [
            data
            for et, data in self.events
            if et == EventType.PLUGIN_PROGRESS and isinstance(data, dict)
        ]


@pytest.fixture
def repo_dir(tmp_path):
    """A temp 'ai-toolkit repo' with fake_run.py available as run.py."""
    repo = tmp_path / "repo"
    repo.mkdir()
    src = os.path.join(_THIS_DIR, "fake_run.py")
    dst = os.path.join(str(repo), "run.py")
    try:
        os.symlink(src, dst)
    except (OSError, NotImplementedError):
        shutil.copyfile(src, dst)
    return str(repo)


def _make_dataset_builder(image_count: int = 2):
    """A dataset_builder stub: write fake image+txt pairs, return a manifest."""

    def builder(*, dest_dir, set_id=None, character_id=None, trigger_word=None, **_):
        os.makedirs(dest_dir, exist_ok=True)
        picture_ids = []
        for i in range(1, image_count + 1):
            stem = os.path.join(dest_dir, f"img_{i:05d}")
            with open(f"{stem}.png", "wb") as fh:
                fh.write(b"FAKE_PNG")
            with open(f"{stem}.txt", "w", encoding="utf-8") as fh:
                fh.write(f"{trigger_word or 'trigger'}, a fake caption\n")
            picture_ids.append(i)
        return {
            "dest_dir": dest_dir,
            "image_count": image_count,
            "picture_ids": picture_ids,
            "caption_mode": "tags",
            "trigger_word": trigger_word,
        }

    return builder


# Sentinel: "the default shelf registrar", distinct from an explicit None.
_DEFAULT_REGISTRAR = object()


def _make_service(
    hub,
    repo_dir,
    *,
    lease,
    notify,
    dataset_builder,
    importer=None,
    poll_interval_s=0.05,
    register_source_folder=_DEFAULT_REGISTRAR,
):
    """Build a TrainingService wired to the temp repo + injected deps.

    By default the service can add the shelf's source folder, as the server
    does; pass ``None`` for a service that cannot.
    """
    if register_source_folder is _DEFAULT_REGISTRAR:
        register_source_folder = _shelf_registrar(hub)
    return TrainingService(
        hub,
        _LIBRARY_UUID,
        os.path.dirname(hub.path),
        request_gpu=lease.request,
        import_run=importer or _RecordingImport(),
        notify=notify,
        config_provider=lambda: {
            "repo_path": repo_dir,
            "python": sys.executable,
            "models_dir": "",
        },
        dataset_builder=dataset_builder,
        poll_interval_s=poll_interval_s,
        register_source_folder=register_source_folder,
    )


def _shelf_registrar(hub):
    """A registrar that does what the folder route does for kind=source.

    It records each path it was asked to add, and writes the ``model_folder``
    row the route's INSERT writes. The row is what the service reads back.
    """

    class _Registrar:
        def __init__(self):
            self.paths: list[str] = []

        def __call__(self, path: str) -> None:
            self.paths.append(path)
            with hub.transaction() as conn:
                conn.execute(
                    "INSERT INTO model_folder (path, kind, owner, movable, "
                    "delete_after_import, created_at) VALUES (?, 'source', ?, 0, 0, ?)",
                    (path, "builtin", now_iso()),
                )

    return _Registrar()


def _add_source_folder(hub, path: str) -> None:
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO model_folder (path, kind, owner, movable, "
            "delete_after_import, created_at) VALUES (?, 'source', ?, 0, 0, ?)",
            (path, "builtin", now_iso()),
        )


def _job_status(hub, job_uuid):
    """The run row by uuid, as attributes, or None."""
    row = training_runs.get_run_by_uuid(hub, job_uuid)
    return SimpleNamespace(**row) if row is not None else None


@pytest.fixture(autouse=True)
def _fast_step_delay(monkeypatch):
    """Drive the stub with no per-step delay."""
    monkeypatch.setenv("FAKE_RUN_STEP_DELAY", "0")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_a_run_without_a_memory_profile_errors_instead_of_training(hub, repo_dir):
    """A row queued before recipes existed has no profile to lease against."""
    lease, notify = _StubLease(), _CapturingNotify()
    service = _make_service(
        hub,
        repo_dir,
        lease=lease,
        notify=notify,
        dataset_builder=_make_dataset_builder(),
    )
    template = _write_template(repo_dir, steps=4, save_every=2)
    service.start()
    try:
        job = service.enqueue(
            set_id=_SET_ID,
            name="legacy",
            base_model="m",
            trigger_word="t",
            template_path=template,
        )
        assert _poll_until(lambda: _job_status(hub, job["uuid"]).status == "error")
    finally:
        service.stop()
    assert "queue it again" in _job_status(hub, job["uuid"]).error
    assert lease.acquire_count == 0


def test_enqueue_validates_exactly_one_scope(hub, repo_dir):
    lease, notify = _StubLease(), _CapturingNotify()
    service = _make_service(
        hub,
        repo_dir,
        lease=lease,
        notify=notify,
        dataset_builder=_make_dataset_builder(),
    )
    template = _write_template(repo_dir, steps=4, save_every=2)

    with pytest.raises(ValueError):
        service.enqueue(
            name="bad", base_model="m", trigger_word="t", template_path=template
        )
    with pytest.raises(ValueError):
        service.enqueue(
            set_id=1,
            character_id=2,
            name="bad",
            base_model="m",
            trigger_word="t",
            template_path=template,
            resolution=_RESOLUTION,
        )

    set_id = _SET_ID
    job = service.enqueue(
        set_id=set_id,
        name="ok",
        base_model="m",
        trigger_word="t",
        template_path=template,
        resolution=_RESOLUTION,
    )
    assert job["status"] == "queued"
    assert job["uuid"]


def test_full_lifecycle_queued_to_completed_lora(hub, repo_dir):
    """enqueue → worker runs → running → completed → run handed to the shelf."""
    lease = _StubLease(grant=True)
    notify = _CapturingNotify()
    importer = _RecordingImport()
    service = _make_service(
        hub,
        repo_dir,
        lease=lease,
        notify=notify,
        dataset_builder=_make_dataset_builder(image_count=3),
        importer=importer,
    )
    template = _write_template(repo_dir, steps=8, save_every=4)
    character_id = _CHARACTER_ID
    # The job records the checkout's HEAD, read from git when it starts.
    subprocess.run(["git", "init", "-q", repo_dir], check=True)
    subprocess.run(
        ["git", "-C", repo_dir, "add", "run.py"], check=True, capture_output=True
    )
    subprocess.run(
        [
            "git",
            "-C",
            repo_dir,
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "commit",
            "-q",
            "-m",
            "init",
        ],
        check=True,
        capture_output=True,
    )
    head = subprocess.run(
        ["git", "-C", repo_dir, "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    service.start()
    try:
        job = service.enqueue(
            character_id=character_id,
            name="charjob",
            base_model="Z-Image",
            trigger_word="ohwx_person",
            template_path=template,
            resolution=_RESOLUTION,
        )

        # It progresses to running with a real step.
        assert _poll_until(lambda: _job_status(hub, job["uuid"]).status == "running"), (
            "never reached running"
        )

        # ... then to completed.
        assert _poll_until(
            lambda: _job_status(hub, job["uuid"]).status == "completed"
        ), f"final status was {_job_status(hub, job['uuid']).status!r}"
    finally:
        service.stop()

    final = _job_status(hub, job["uuid"])
    assert final.status == "completed"
    # The lease asked for the resolved profile's memory, and the row keeps it.
    assert lease.need == (12000, 16000)
    assert final.preset == "zimage-turbo@1"
    assert json.loads(final.resolved_profile) == _RESOLUTION
    assert final.step == 8
    assert final.total_steps == 8
    assert final.started_at is not None
    assert final.completed_at is not None
    assert final.ai_toolkit_commit == head
    # The live view's phase log: queued, launched, and imported last.
    phases = [entry["phase"] for entry in json.loads(final.phase_log)]
    assert phases[:2] == ["waiting_gpu", "loading"]
    assert phases[-1] == "importing"
    assert len(phases) == len(set(phases)), phases
    assert final.finished_unwatched == 0
    assert final.dataset_picture_ids
    assert json.loads(final.dataset_picture_ids) == [1, 2, 3]
    # Loss curve populated.
    assert final.loss_curve
    loss = json.loads(final.loss_curve)
    assert len(loss) == 8
    assert all("step" in p and "loss" in p for p in loss)

    # The finished run went to the shelf through the importer, once, with the
    # ai-toolkit run folder and the job's scope.
    assert len(importer.calls) == 1
    run_dir, info = importer.calls[0]
    assert os.path.isfile(
        os.path.join(run_dir, f"charjob_{job['uuid'][:8]}.safetensors")
    )
    assert info["uuid"] == job["uuid"]
    assert info["character_id"] == character_id
    assert info["set_id"] is None
    assert "1 file(s) on the shelf" in final.report

    # Samples recorded on the job, still where the trainer wrote them.
    assert final.sample_image_paths
    samples = json.loads(final.sample_image_paths)
    assert samples
    for path in samples:
        assert os.path.exists(path)

    # GPU lease acquired before submit and released after, in order.
    assert lease.events == ["acquire", "release"]

    # PLUGIN_PROGRESS events emitted with run_id == job["uuid"] and a completed one.
    progress = notify.plugin_progress()
    assert progress, "no PLUGIN_PROGRESS events emitted"
    assert all(p["run_id"] == job["uuid"] for p in progress)
    assert all(p["plugin"] == "ai-toolkit" for p in progress)
    assert any(p["status"] == "completed" for p in progress)


def test_cancel_midrun_ends_stopped_and_releases_lease(hub, repo_dir, monkeypatch):
    """cancel() mid-run leaves the job 'stopped' and releases the GPU lease."""
    monkeypatch.setenv("FAKE_RUN_STEP_DELAY", "0.02")
    lease = _StubLease(grant=True)
    notify = _CapturingNotify()
    service = _make_service(
        hub,
        repo_dir,
        lease=lease,
        notify=notify,
        dataset_builder=_make_dataset_builder(),
    )
    template = _write_template(repo_dir, steps=500, save_every=50)
    set_id = _SET_ID

    service.start()
    try:
        job = service.enqueue(
            set_id=set_id,
            name="cancelme",
            base_model="m",
            trigger_word="t",
            template_path=template,
            resolution=_RESOLUTION,
        )
        # Wait until it is genuinely running with a recorded pid.
        assert _poll_until(
            lambda: (
                _job_status(hub, job["uuid"]).status == "running"
                and _job_status(hub, job["uuid"]).pid is not None
            )
        ), "job did not reach running with a pid"

        assert service.cancel(job["id"]) is True

        assert _poll_until(
            lambda: _job_status(hub, job["uuid"]).status in {"stopped", "error"}
        ), f"job did not finalize, status={_job_status(hub, job['uuid']).status!r}"
    finally:
        service.stop()

    final = _job_status(hub, job["uuid"])
    assert final.status == "stopped", f"expected stopped, got {final.status!r}"
    assert final.step < 500
    # Lease released exactly as many times as acquired (no leak).
    assert lease.acquire_count == lease.release_count == 1


def test_shutdown_mid_run_leaves_the_live_job_running(hub, repo_dir, monkeypatch):
    """Stopping the service while a trainer runs must not mark it errored.

    The trainer outlives the service; reconcile_on_startup re-attaches to it on
    the next start. Finalizing it at shutdown would record a healthy run as an
    error with a completed_at.
    """
    monkeypatch.setenv("FAKE_RUN_STEP_DELAY", "0.02")
    lease = _StubLease(grant=True)
    service = _make_service(
        hub,
        repo_dir,
        lease=lease,
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    template = _write_template(repo_dir, steps=500, save_every=50)

    service.start()
    try:
        job = service.enqueue(
            set_id=_SET_ID,
            name="shutdown",
            base_model="m",
            trigger_word="t",
            template_path=template,
            resolution=_RESOLUTION,
        )
        assert _poll_until(
            lambda: (
                _job_status(hub, job["uuid"]).status == "running"
                and _job_status(hub, job["uuid"]).pid is not None
            )
        ), "job did not reach running with a pid"
    finally:
        service.stop()

    final = _job_status(hub, job["uuid"])
    assert final.status == "running", f"status={final.status!r}"
    assert final.completed_at is None

    # Stop the trainer the test left running, so nothing outlives the test.
    cleanup = _make_service(
        hub,
        repo_dir,
        lease=_StubLease(),
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    assert cleanup.cancel(job["id"]) is True
    assert _poll_until(lambda: not LocalAiToolkit._pid_alive(final.pid)), (
        f"trainer pid {final.pid} still alive after cancel"
    )


def test_zero_image_dataset_marks_error_no_lease_leak(hub, repo_dir):
    """A zero-image dataset marks the job error and never touches the GPU lease."""
    lease = _StubLease(grant=True)
    notify = _CapturingNotify()
    service = _make_service(
        hub,
        repo_dir,
        lease=lease,
        notify=notify,
        dataset_builder=_make_dataset_builder(image_count=0),
    )
    template = _write_template(repo_dir, steps=4, save_every=2)
    set_id = _SET_ID

    service.start()
    try:
        job = service.enqueue(
            set_id=set_id,
            name="empty",
            base_model="m",
            trigger_word="t",
            template_path=template,
            resolution=_RESOLUTION,
        )
        assert _poll_until(lambda: _job_status(hub, job["uuid"]).status == "error"), (
            f"status={_job_status(hub, job['uuid']).status!r}"
        )
    finally:
        service.stop()

    final = _job_status(hub, job["uuid"])
    assert final.status == "error"
    assert final.error and "empty" in final.error.lower()
    # The lease was never taken (we bailed before acquire) → no leak.
    assert lease.acquire_count == 0
    assert lease.release_count == 0


class _WaitingLease(_StubLease):
    """A lease the arbiter never grants: ``wait`` blocks until it is released."""

    def wait(self, cancel_event=None, timeout=None) -> bool:
        while not self.release_count and not (cancel_event and cancel_event.is_set()):
            time.sleep(0.01)
        return False


def test_lease_cancelled_while_waiting_stops_the_job_unlaunched(hub, repo_dir):
    """A waiting lease cancelled from the Tasks tab ends the job, never trained."""
    lease = _StubLease(grant=False)
    service = _make_service(
        hub,
        repo_dir,
        lease=lease,
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    template = _write_template(repo_dir, steps=4, save_every=2)
    set_id = _SET_ID

    service.start()
    try:
        job = service.enqueue(
            set_id=set_id,
            name="refused",
            base_model="m",
            trigger_word="t",
            template_path=template,
            resolution=_RESOLUTION,
        )
        assert _poll_until(lambda: _job_status(hub, job["uuid"]).status == "stopped"), (
            f"status={_job_status(hub, job['uuid']).status!r}"
        )
    finally:
        service.stop()

    final = _job_status(hub, job["uuid"])
    assert final.pid is None and final.started_at is None
    assert lease.events == ["acquire", "release"]


def test_cancel_queued_job_never_frees_a_lease_already_granted(hub, repo_dir):
    """cancel() on a queued job leaves a lease the worker was just granted alone.

    The worker is about to train on that card; freeing it would let the arbiter
    hand the GPU to another tenant while the run is on it.
    """
    lease = _StubLease()
    service = _make_service(
        hub,
        repo_dir,
        lease=lease,
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    template = _write_template(repo_dir, steps=4, save_every=2)
    # No worker is started, so the job stays queued and the test owns the state.
    job = service.enqueue(
        set_id=_SET_ID,
        name="granted",
        base_model="m",
        trigger_word="t",
        template_path=template,
    )
    granted = _StubLease()
    granted.held = True
    service._waiting = (job["uuid"], granted)

    assert service.cancel(job["id"]) is True

    assert granted.release_count == 0
    assert _job_status(hub, job["uuid"]).status == "stopped"


class _ShutdownLease(_StubLease):
    """A lease that is never granted and gives up only when the service stops."""

    def wait(self, cancel_event=None, timeout=None) -> bool:
        while not (cancel_event and cancel_event.is_set()):
            time.sleep(0.01)
        return False


def test_shutdown_while_queued_keeps_the_spec_for_the_next_start(hub, repo_dir):
    """A job still queued at shutdown keeps its spec, so the next start can train it."""
    lease = _ShutdownLease()
    service = _make_service(
        hub,
        repo_dir,
        lease=lease,
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    template = _write_template(repo_dir, steps=4, save_every=2)

    service.start()
    try:
        job = service.enqueue(
            set_id=_SET_ID,
            name="kept",
            base_model="m",
            trigger_word="t",
            template_path=template,
            resolution=_RESOLUTION,
        )
        assert _poll_until(lambda: lease.acquire_count == 1), "lease never requested"
    finally:
        service.stop()

    assert _job_status(hub, job["uuid"]).status == "queued"
    # A fresh service has no in-memory spec, so it must read the sidecar.
    restarted = _make_service(
        hub,
        repo_dir,
        lease=_StubLease(),
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    assert restarted._load_pending_spec(job["uuid"])["template_path"] == template


def test_cancel_queued_job_drops_the_lease_it_waits_on(hub, repo_dir):
    """Cancelling a job that waits for the GPU releases its lease, never trains."""
    lease = _WaitingLease()
    service = _make_service(
        hub,
        repo_dir,
        lease=lease,
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    template = _write_template(repo_dir, steps=4, save_every=2)
    set_id = _SET_ID

    service.start()
    try:
        job = service.enqueue(
            set_id=set_id,
            name="waiting",
            base_model="m",
            trigger_word="t",
            template_path=template,
            resolution=_RESOLUTION,
        )
        assert _poll_until(lambda: service._waiting is not None)
        assert service.cancel(_job_status(hub, job["uuid"]).id)
        assert _poll_until(lambda: service._waiting is None)
    finally:
        service.stop()

    final = _job_status(hub, job["uuid"])
    assert final.status == "stopped"
    assert final.pid is None and final.started_at is None
    assert lease.acquire_count == 1 and lease.release_count >= 1


def _run_to_terminal(hub, service, job, *, expect: str) -> None:
    """Start the worker, wait for *job* to reach *expect*, then stop it."""
    service.start()
    try:
        assert _poll_until(lambda: _job_status(hub, job["uuid"]).status == expect), (
            f"status={_job_status(hub, job['uuid']).status!r}, wanted {expect!r}"
        )
    finally:
        service.stop()


def test_completed_run_is_handed_to_the_importer_and_the_job_completes(hub, repo_dir):
    """A finished run calls the importer with its run folder; the job completes."""
    importer = _RecordingImport()
    lease = _StubLease(grant=True)
    service = _make_service(
        hub,
        repo_dir,
        lease=lease,
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
        importer=importer,
    )
    template = _write_template(repo_dir, steps=4, save_every=2)
    set_id = _SET_ID
    job = service.enqueue(
        set_id=set_id,
        name="shelfbound",
        base_model="m",
        trigger_word="t",
        template_path=template,
        resolution=_RESOLUTION,
    )

    _run_to_terminal(hub, service, job, expect="completed")

    assert len(importer.calls) == 1
    run_dir, info = importer.calls[0]
    # The run folder is the one holding the final checkpoint, not its parent.
    assert os.path.isfile(
        os.path.join(run_dir, f"shelfbound_{job['uuid'][:8]}.safetensors")
    )
    assert info["set_id"] == set_id and info["character_id"] is None
    # The importer is told which hub run made the model, so it can stamp it.
    assert info["id"] == job["id"]
    final = _job_status(hub, job["uuid"])
    assert final.status == "completed"
    assert final.report and "(stack 7)" in final.report
    assert lease.acquire_count == lease.release_count == 1


def test_an_importer_failure_marks_the_completed_job_error(hub, repo_dir):
    """The run finished but the shelf refused it: the job is an error, not done."""
    importer = _RecordingImport(error=RuntimeError("no room on the shelf"))
    lease = _StubLease(grant=True)
    service = _make_service(
        hub,
        repo_dir,
        lease=lease,
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
        importer=importer,
    )
    template = _write_template(repo_dir, steps=4, save_every=2)
    set_id = _SET_ID
    job = service.enqueue(
        set_id=set_id,
        name="refused",
        base_model="m",
        trigger_word="t",
        template_path=template,
        resolution=_RESOLUTION,
    )

    _run_to_terminal(hub, service, job, expect="error")

    assert len(importer.calls) == 1
    final = _job_status(hub, job["uuid"])
    assert final.status == "error"
    assert final.error == ("Run completed but the LoRA artifact could not be imported")
    assert final.report is None
    assert lease.acquire_count == lease.release_count == 1


def test_reconcile_on_startup_dead_running_job_finalizes(hub, repo_dir):
    """A job left 'running' with a dead pid is finalized; lease not left held.

    We pre-stage a job row as 'running' with a dead pid and a save_root that
    contains a final checkpoint, so reconcile must finalize it to 'completed'
    and import the LoRA without re-acquiring/holding the GPU lease.
    """
    lease = _StubLease(grant=True)
    notify = _CapturingNotify()
    importer = _RecordingImport()
    service = _make_service(
        hub,
        repo_dir,
        lease=lease,
        notify=notify,
        dataset_builder=_make_dataset_builder(),
        importer=importer,
    )

    job_uuid = "dead-job-uuid-0001"
    work_dir = os.path.join(os.path.dirname(hub.path), "training", job_uuid)
    training_folder = os.path.join(work_dir, "output")
    save_root = os.path.join(training_folder, "deadjob")
    os.makedirs(save_root, exist_ok=True)

    # Final checkpoint present on disk so reconcile finalizes as completed.
    with open(os.path.join(save_root, "deadjob.safetensors"), "wb") as fh:
        fh.write(b"FAKE_FINAL_SAFETENSORS")
    # A rendered config the service reads total_steps from (not strictly needed
    # for reconcile, but realistic).
    with open(os.path.join(work_dir, ".job_config.json"), "w", encoding="utf-8") as fh:
        json.dump({"config": {"process": [{"train": {"steps": 4}}]}}, fh)
    # The per-job Job DB with a 'completed' status so the adapter reconcile trusts it.
    job_db = os.path.join(work_dir, "job.db")
    conn = sqlite3.connect(job_db)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS Job ("
            "id TEXT PRIMARY KEY, status TEXT, info TEXT, step INTEGER, "
            "speed_string TEXT, stop INTEGER, return_to_queue INTEGER, save_now INTEGER)"
        )
        conn.execute(
            "INSERT INTO Job (id, status, info, step) VALUES (?, 'completed', 'done', 4)",
            (job_uuid,),
        )
        conn.commit()
    finally:
        conn.close()

    # Stage the run as 'running' with a dead pid, as a crashed worker left it.
    training_runs.create_run(
        hub,
        library_uuid=_LIBRARY_UUID,
        uuid=job_uuid,
        name="deadjob",
        set_id=None,
        character_id=_CHARACTER_ID,
        base_model="m",
        trigger_word="t",
    )
    training_runs.update_run(
        hub,
        job_uuid,
        status="running",
        pid=999_999_999,
        training_folder=training_folder,
        total_steps=4,
        step=4,
    )

    service.reconcile_on_startup()

    final = _job_status(hub, job_uuid)
    assert final.status == "completed", f"got {final.status!r}"
    assert [run_dir for run_dir, _ in importer.calls] == [save_root]
    # Nobody watched it end, and the live view says so.
    assert final.finished_unwatched == 1
    # Dead-process reconcile must not have taken the GPU lease at all.
    assert lease.acquire_count == 0
    assert lease.release_count == 0


def test_a_polling_run_keeps_its_lease_step_total_and_loss_live(hub, repo_dir):
    """Each poll hands the granted lease the run's step, total and latest loss.

    The Tasks tab's AI Toolkit row reads these from the arbiter snapshot, so a
    run whose lease never gets them shows no progress at all.
    """
    from types import SimpleNamespace

    from pixlstash.training.backend import TrainingProgress

    running = TrainingProgress(
        status="running",
        info="Training",
        step=120,
        total_steps=300,
        speed="2.31 iter/sec",
        loss_curve=[{"step": 119, "loss": 0.5}, {"step": 120, "loss": 0.42}],
        samples=[],
        checkpoints=[],
        final_checkpoint=None,
    )
    done = TrainingProgress(
        status="completed",
        info="done",
        step=300,
        total_steps=300,
        speed="",
        loss_curve=[],
        samples=[],
        checkpoints=[],
        final_checkpoint="x.safetensors",
    )

    class _Backend:
        def __init__(self):
            self.calls = 0

        def progress(self, handle):
            self.calls += 1
            return running if self.calls == 1 else done

        def is_alive(self, handle):
            return self.calls == 1

    lease = _StubLease()
    service = _make_service(
        hub,
        repo_dir,
        lease=lease,
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    handle = SimpleNamespace(log_path=os.path.join(repo_dir, "missing.log"))

    service._poll_until_done(_Backend(), handle, "abc123", lease)

    assert lease.progress_calls == [
        {"step": 120, "total": 300, "loss": 0.42},
        {"step": 300, "total": 300, "loss": None},
    ]


def test_the_worker_leaves_another_librarys_runs_alone(hub, repo_dir):
    """Claim and reconcile are scoped to the service's own library.

    A queued or unfinished run belonging to another library is not this
    service's to train or finalize: its set and character ids are vault ids of
    that library, and a second worker would run them against the wrong vault.
    """
    lease, notify = _StubLease(), _CapturingNotify()
    service = _make_service(
        hub,
        repo_dir,
        lease=lease,
        notify=notify,
        dataset_builder=_make_dataset_builder(),
    )
    training_runs.create_run(
        hub,
        library_uuid="other-library",
        uuid="other-queued",
        name="elsewhere",
        set_id=_SET_ID,
        character_id=None,
        base_model="m",
        trigger_word="t",
    )
    training_runs.create_run(
        hub,
        library_uuid="other-library",
        uuid="other-running",
        name="elsewhere-too",
        set_id=_SET_ID,
        character_id=None,
        base_model="m",
        trigger_word="t",
    )
    training_runs.update_run(hub, "other-running", status="running", pid=999_999_999)

    # The worker's claim, run once: nothing of ours is queued, so nothing runs.
    assert service._process_next_queued_job() is False
    service.reconcile_on_startup()

    assert _job_status(hub, "other-queued").status == "queued"
    assert _job_status(hub, "other-running").status == "running"
    assert lease.acquire_count == 0


# ---------------------------------------------------------------------------
# The shelf's ai-toolkit folder and the dataset folder
# ---------------------------------------------------------------------------


def test_first_job_registers_the_checkouts_output_as_the_shelf_source_folder(
    hub, repo_dir
):
    """With no source folder on the shelf, the first job registers <repo>/output
    through the registrar and writes its run there; the dataset goes under
    <repo>/datasets/pixlstash-<uuid>, not the vault's training root."""
    lease = _StubLease(grant=True)
    importer = _RecordingImport()
    registrar = _shelf_registrar(hub)
    service = _make_service(
        hub,
        repo_dir,
        lease=lease,
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(image_count=2),
        importer=importer,
        register_source_folder=registrar,
    )
    template = _write_template(repo_dir, steps=4, save_every=4)

    service.start()
    try:
        job = service.enqueue(
            character_id=_CHARACTER_ID,
            name="charjob",
            base_model="Z-Image",
            trigger_word="ohwx_person",
            template_path=template,
            resolution=_RESOLUTION,
        )
        _run_to_terminal(hub, service, job, expect="completed")
    finally:
        service.stop()

    output = os.path.join(repo_dir, "output")
    assert registrar.paths == [output], "the output folder is registered once"
    final = _job_status(hub, job["uuid"])
    assert final.training_folder == output
    assert importer.calls, "the completed run reached the importer"
    run_dir, _info = importer.calls[0]
    # The run folder is inside the shelf folder, and named with the job id so
    # two runs of one name cannot share a save_root in that shared folder.
    assert os.path.dirname(run_dir) == output
    assert os.path.basename(run_dir) == f"charjob_{job['uuid'][:8]}"
    dataset = os.path.join(repo_dir, "datasets", f"pixlstash-{job['uuid']}")
    assert json.loads(final.dataset_picture_ids) == [1, 2]
    assert os.path.isfile(os.path.join(dataset, "img_00001.png"))
    assert final.dataset_snapshot_path == dataset


def test_a_later_job_uses_the_registered_source_folder_and_does_not_register_again(
    hub, repo_dir, tmp_path
):
    """A source folder already on the shelf is the run folder; the registrar is
    never asked for another one."""
    shelf = str(tmp_path / "my-shelf-ai-toolkit")
    _add_source_folder(hub, shelf)
    registrar = _shelf_registrar(hub)
    importer = _RecordingImport()
    service = _make_service(
        hub,
        repo_dir,
        lease=_StubLease(grant=True),
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(image_count=2),
        importer=importer,
        register_source_folder=registrar,
    )
    template = _write_template(repo_dir, steps=4, save_every=4)

    service.start()
    try:
        job = service.enqueue(
            set_id=_SET_ID,
            name="setjob",
            base_model="Z-Image",
            trigger_word="ohwx_style",
            template_path=template,
            resolution=_RESOLUTION,
        )
        _run_to_terminal(hub, service, job, expect="completed")
    finally:
        service.stop()

    assert registrar.paths == [], "an existing source folder is never re-registered"
    assert _job_status(hub, job["uuid"]).training_folder == shelf
    run_dir, _info = importer.calls[0]
    assert os.path.dirname(run_dir) == shelf


def test_a_job_with_no_shelf_folder_and_no_registrar_fails_without_training(
    hub, repo_dir
):
    """No source folder and no way to add one: the job is an error with a
    message that names no host path, and nothing is built or launched."""
    builds: list[str] = []

    def builder(**kwargs):
        builds.append(kwargs["dest_dir"])
        return _make_dataset_builder()(**kwargs)

    service = _make_service(
        hub,
        repo_dir,
        lease=_StubLease(grant=True),
        notify=_CapturingNotify(),
        dataset_builder=builder,
        register_source_folder=None,
    )
    template = _write_template(repo_dir, steps=4, save_every=4)

    service.start()
    try:
        job = service.enqueue(
            set_id=_SET_ID,
            name="orphan",
            base_model="Z-Image",
            trigger_word="ohwx_style",
            template_path=template,
            resolution=_RESOLUTION,
        )
        _run_to_terminal(hub, service, job, expect="error")
    finally:
        service.stop()

    final = _job_status(hub, job["uuid"])
    assert final.error == "No model shelf folder is set up for ai-toolkit runs."
    assert builds == [], "no dataset is built when the run has nowhere to go"
    assert not os.path.exists(os.path.join(repo_dir, "output"))


def test_vault_reconciles_a_surviving_trainer_before_the_worker_starts():
    """A trainer that survived a restart is handled before the worker takes a job.

    Otherwise the worker can claim a queued job and ask the arbiter for the card
    while the surviving run still holds it.
    """
    order: list[str] = []

    def noop(*_args, **_kwargs):
        return None

    stand_in = SimpleNamespace(
        _disable_background_workers=False,
        _started=False,
        _task_runner=SimpleNamespace(start=noop),
        _work_planner=SimpleNamespace(start=noop),
        _arbiter=SimpleNamespace(add_yielder=noop, add_listener=noop),
        _yield_gpu=noop,
        _on_resource_lease=noop,
        _ref_folder_watcher=SimpleNamespace(start=noop),
        _start_existing_folder_watches=noop,
        training_service=SimpleNamespace(
            reconcile_on_startup=lambda: order.append("reconcile"),
            start=lambda: order.append("start"),
        ),
    )

    Vault.start(stand_in)

    assert order == ["reconcile", "start"]
