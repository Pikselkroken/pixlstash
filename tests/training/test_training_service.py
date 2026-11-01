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

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from types import SimpleNamespace

import pytest

from pixlstash.event_types import EventType
from pixlstash.hub import dataset_snapshots, training_runs
from pixlstash.hub.db import now_iso
from pixlstash.resources.aitoolkit_tenant import AiToolkitProbe
from pixlstash.services import training_service as training_service_module
from pixlstash.services.training_service import TrainingService
from pixlstash.training import measurements, presets
from pixlstash.training.backend import TrainingProgress
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

    def shrink(self, need_mb: int) -> bool:
        self.events.append(f"shrink {need_mb}")
        return True

    def warn(self, code: str, text: str, **payload) -> None:
        self.events.append(f"warn {code}")


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
        items = []
        for i in range(1, image_count + 1):
            stem = os.path.join(dest_dir, f"img_{i:05d}")
            caption = f"{trigger_word or 'trigger'}, a fake caption"
            with open(f"{stem}.png", "wb") as fh:
                fh.write(b"FAKE_PNG")
            with open(f"{stem}.txt", "w", encoding="utf-8") as fh:
                fh.write(f"{caption}\n")
            picture_ids.append(i)
            items.append(
                {
                    "picture_id": i,
                    "file_name": f"img_{i:05d}.png",
                    "sha256": hashlib.sha256(b"FAKE_PNG").hexdigest(),
                    "caption": caption,
                }
            )
        return {
            "dest_dir": dest_dir,
            "image_count": image_count,
            "picture_ids": picture_ids,
            "caption_mode": "tags",
            "trigger_word": trigger_word,
            "items": items,
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


def test_only_a_clip_recipe_builds_its_dataset_with_the_clips(hub, repo_dir):
    """The job's locked config decides whether the set's video clips are
    exported, so a recipe gone since it was queued changes nothing."""
    asked = []
    empty = _make_dataset_builder(image_count=0)

    def builder(**kwargs):
        asked.append(kwargs["clips"])
        return empty(**kwargs)

    lease, notify = _StubLease(), _CapturingNotify()
    service = _make_service(
        hub, repo_dir, lease=lease, notify=notify, dataset_builder=builder
    )
    template = _write_template(repo_dir, steps=4, save_every=2)
    service.start()
    try:
        # The third was queued under a recipe this PixlStash no longer ships.
        for preset_id, recorded in (
            ("minimax-h3-motion", "minimax-h3-motion@1"),
            ("minimax-h3-image", "minimax-h3-image@1"),
            ("minimax-h3-motion", "a-retired-recipe@1"),
        ):
            job = service.enqueue(
                set_id=_SET_ID,
                name=recorded,
                base_model="m",
                trigger_word="t",
                template_path=template,
                locked=presets.to_patch(presets.get_preset(preset_id)["locked"]),
                resolution={**_RESOLUTION, "preset": recorded},
            )
            assert _poll_until(lambda: _job_status(hub, job["uuid"]).status == "error")
    finally:
        service.stop()
    assert asked == [True, False, True]


def test_enqueue_needs_a_scope(hub, repo_dir):
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
    # Both is a character's reference set, filed under the character; the
    # route checks the pairing.
    both = service.enqueue(
        set_id=_SET_ID,
        character_id=2,
        name="reference",
        base_model="m",
        trigger_word="t",
        template_path=template,
        resolution=_RESOLUTION,
    )
    assert (both["set_id"], both["character_id"]) == (_SET_ID, 2)

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
    # The dataset was frozen before training: the run names its snapshot.
    snapshot = dataset_snapshots.get_snapshot(
        hub, final.library_uuid, final.dataset_snapshot_id
    )
    assert [item["picture_id"] for item in snapshot["items"]] == [1, 2, 3]
    assert snapshot["character_id"] == character_id
    assert snapshot["trigger_word"] == "ohwx_person"
    assert {item["caption"] for item in snapshot["items"]} == {
        "ohwx_person, a fake caption"
    }
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


def test_a_cancel_while_the_model_loads_ends_stopped(hub, repo_dir, monkeypatch):
    """ai-toolkit does not read ``stop`` while it loads the model, so the cancel
    ends in a signal; the run still ends ``stopped``, not ``error``."""
    monkeypatch.setenv("FAKE_RUN_LOAD_SECONDS", "15")
    monkeypatch.setattr(
        "pixlstash.training.local_ai_toolkit._CANCEL_GRACE_SECONDS", 0.2
    )
    lease = _StubLease()
    service = _make_service(
        hub,
        repo_dir,
        lease=lease,
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    job = service.enqueue(
        set_id=_SET_ID,
        name="loading",
        base_model="m",
        trigger_word="t",
        template_path=_write_template(repo_dir, steps=4, save_every=2),
        resolution=_RESOLUTION,
    )
    service.start()
    try:
        assert _poll_until(lambda: _job_status(hub, job["uuid"]).pid is not None)
        assert service.cancel(job["id"]) is True
        assert _poll_until(lambda: _job_status(hub, job["uuid"]).completed_at)
    finally:
        service.stop()

    final = _job_status(hub, job["uuid"])
    assert (final.status, final.error) == ("stopped", None)
    assert lease.acquire_count == lease.release_count == 1


def test_a_cancel_after_the_memory_check_is_not_overwritten(hub, repo_dir):
    """A cancel in the last seconds of checking memory and speed sees the run
    queued and stops it; the worker must not then launch it anyway."""
    importer = _RecordingImport()
    service = _make_service(
        hub,
        repo_dir,
        lease=_StubLease(),
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
        importer=importer,
    )

    def cancelled_while_checking(backend, info, *args):
        assert service.cancel(info["id"]) is True
        return True

    service._check_memory_and_speed = cancelled_while_checking
    job = service.enqueue(
        set_id=_SET_ID,
        name="checked",
        base_model="m",
        trigger_word="t",
        template_path=_write_template(repo_dir, steps=4, save_every=2),
        resolution=_RESOLUTION,
    )

    service._run_job(training_runs.get_run(hub, _LIBRARY_UUID, job["id"]))

    final = _job_status(hub, job["uuid"])
    assert (final.status, final.pid) == ("stopped", None)
    assert importer.calls == []


def test_a_cancel_that_read_queued_stops_a_run_launched_meanwhile(
    hub, repo_dir, monkeypatch
):
    """cancel() reads the run before taking the lock; if the worker launched it
    in between, the cancel must stop the trainer, not write ``stopped`` over a
    running row."""
    service = _make_service(
        hub,
        repo_dir,
        lease=_StubLease(),
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    job = service.enqueue(
        set_id=_SET_ID,
        name="raced",
        base_model="m",
        trigger_word="t",
        template_path=_write_template(repo_dir, steps=4, save_every=2),
    )
    stale = training_runs.get_run(hub, _LIBRARY_UUID, job["id"])
    training_runs.update_run(hub, job["uuid"], status="running", pid=4242)
    cancelled = []

    class _Backend:
        def cancel(self, handle):
            cancelled.append(handle.pid)

    service._active_backend = _Backend()
    service._active_job_uuid = job["uuid"]
    reads = iter([stale])
    real_get_run = training_runs.get_run
    monkeypatch.setattr(
        training_service_module.training_runs,
        "get_run",
        lambda *args: next(reads, None) or real_get_run(*args),
    )

    assert service.cancel(job["id"]) is True

    assert _job_status(hub, job["uuid"]).status == "stopping"
    assert cancelled == [4242]


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


def test_a_rebuilt_dataset_starts_from_an_empty_folder(hub, repo_dir, tmp_path):
    """A leftover from an earlier build would train without being recorded."""
    service = _make_service(
        hub,
        repo_dir,
        lease=_StubLease(grant=True),
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(image_count=1),
    )
    dataset_dir = tmp_path / "pixlstash-rebuilt"
    dataset_dir.mkdir()
    (dataset_dir / "img_00002.png").write_bytes(b"FROM_THE_FIRST_BUILD")

    manifest = service._build_dataset(
        {"uuid": "rebuilt", "set_id": _SET_ID, "trigger_word": "t"},
        str(dataset_dir),
    )

    assert len(manifest["items"]) == 1
    assert sorted(os.listdir(dataset_dir)) == ["img_00001.png", "img_00001.txt"]


def test_a_run_whose_snapshot_cannot_be_recorded_does_not_train(hub, repo_dir):
    """Lineage is a record: a run that cannot freeze its dataset never starts."""
    lease = _StubLease(grant=True)
    build = _make_dataset_builder()

    def builder(**kwargs):
        manifest = build(**kwargs)
        del manifest["items"][0]["sha256"]
        return manifest

    service = _make_service(
        hub, repo_dir, lease=lease, notify=_CapturingNotify(), dataset_builder=builder
    )
    template = _write_template(repo_dir, steps=4, save_every=2)

    service.start()
    try:
        job = service.enqueue(
            set_id=_SET_ID,
            name="unrecorded",
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
    assert final.error == "Dataset snapshot could not be recorded"
    assert final.dataset_snapshot_id is None
    assert hub.fetchone("SELECT COUNT(*) FROM dataset_snapshot")[0] == 0
    assert lease.acquire_count == 0


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


def test_a_failed_run_stores_a_summary_and_keeps_the_trainers_words(hub, repo_dir):
    """The card shows the summary; the whole OOM paragraph stays in the report."""
    service = _make_service(
        hub,
        repo_dir,
        lease=_StubLease(grant=True),
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    job = service.enqueue(
        set_id=_SET_ID,
        name="oom",
        base_model="m",
        trigger_word="t",
        template_path=_write_template(repo_dir, steps=4, save_every=2),
        resolution=_RESOLUTION,
    )
    info = (
        "CUDA out of memory. Tried to allocate 5.06 GiB. GPU 0 has a total "
        "capacity of 31.41 GiB of which 5.08 GiB is free. Process 101 has "
        "498.00 MiB memory in use."
    )
    ended = TrainingProgress(
        status="error", info=info, step=500, total_steps=1500, speed=""
    )

    service._finalize(None, None, {"uuid": job["uuid"]}, ended)

    final = _job_status(hub, job["uuid"])
    assert final.status == "error"
    assert final.error.startswith("Ran out of GPU memory at step 500:")
    assert final.report == info


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
    handle = SimpleNamespace(
        log_path=os.path.join(repo_dir, "missing.log"), pid=None, work_dir=repo_dir
    )

    service._poll_until_done(_Backend(), handle, "abc123", lease)

    # The phase rides along (the worker's phase telemetry); a trainer with no
    # process to read has no memory figures.
    unread = {"vram_mb": None, "ram_mb": None}
    assert lease.progress_calls == [
        {"step": 120, "total": 300, "loss": 0.42, "phase": "training", **unread},
        {"step": 300, "total": 300, "loss": None, "phase": "importing", **unread},
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
    while the surviving run still holds it. It is adopted before PixlStash's own
    GPU workers start, so none of them loads a model under it.
    """
    order: list[str] = []

    def noop(*_args, **_kwargs):
        return None

    stand_in = SimpleNamespace(
        _disable_background_workers=False,
        _started=False,
        _task_runner=SimpleNamespace(start=lambda: order.append("tasks")),
        _work_planner=SimpleNamespace(start=noop),
        _arbiter=SimpleNamespace(add_yielder=noop, add_listener=noop),
        _yield_gpu=noop,
        _on_resource_lease=noop,
        _ref_folder_watcher=SimpleNamespace(start=noop),
        _model_folder_watcher=SimpleNamespace(start=noop),
        _start_existing_folder_watches=noop,
        training_service=SimpleNamespace(
            adopt_live_runs=lambda: order.append("adopt"),
            reconcile_on_startup=lambda: order.append("reconcile"),
            start=lambda: order.append("start"),
        ),
    )

    Vault.start(stand_in)

    assert order == ["adopt", "tasks", "reconcile", "start"]


def test_a_poll_does_not_undo_a_cancel_the_trainer_has_not_seen_yet(hub, repo_dir):
    """The trainer reports running for a while after a cancel; "stopping"
    stands until it reaches an end state."""
    service = _make_service(
        hub,
        repo_dir,
        lease=_StubLease(grant=True),
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    training_runs.create_run(
        hub,
        library_uuid=_LIBRARY_UUID,
        uuid="cancelled-job",
        name="cancelled",
        set_id=None,
        character_id=_CHARACTER_ID,
        base_model="m",
        trigger_word="t",
    )
    handle = SimpleNamespace(log_path="")
    running = TrainingProgress(
        status="running", info="Training", step=40, total_steps=100, speed=""
    )

    training_runs.update_run(hub, "cancelled-job", status="stopping")
    service._persist_progress("cancelled-job", running, handle)
    assert _job_status(hub, "cancelled-job").status == "stopping"

    stopped = TrainingProgress(
        status="stopped", info="Job stopped", step=41, total_steps=100, speed=""
    )
    service._persist_progress("cancelled-job", stopped, handle)
    assert _job_status(hub, "cancelled-job").status == "stopped"

    # A run nobody cancelled still reads as running.
    training_runs.update_run(hub, "cancelled-job", status="running")
    service._persist_progress("cancelled-job", running, handle)
    assert _job_status(hub, "cancelled-job").status == "running"


# ---------------------------------------------------------------------------
# Checking memory and speed, and learning from runs (#2120)
# ---------------------------------------------------------------------------

_ZIT = presets.get_preset("zimage-turbo")
# A run small enough for the stub: four updates, a checkpoint every two.
_SMALL = {"train.steps": 4, "save.save_every": 2, "sample.sample_every": 2}
# What the stub trainer is read as holding on the card, while it runs.
_HOLDS_MB = 9000


def _resolve_small(hub, *, vram_mb=22_800, profile=None, prompts=None):
    """The recipe resolved for a two-picture set, with this hub's measurements."""
    return presets.resolve(
        _ZIT,
        subject="person",
        effort="standard",
        fit="auto",
        picture_count=2,
        vram_mb=vram_mb,
        ram_mb=60_000,
        pins=dict(_SMALL),
        lookup=measurements.make_lookup(hub, _ZIT, commit=None, device=None),
        profile=profile,
        prompts=prompts,
    )


def _record_big_previews(hub, profile, previews_mb=30000):
    """Record *profile* as measured: training fits, previews (by default) do
    not."""
    measurements.record(
        hub,
        _resolve_small(hub, profile=profile)["fingerprint"],
        preset=presets.preset_ref(_ZIT),
        profile=profile,
        device=None,
        phase_peaks={
            "training": {"vram_mb": 9000, "ram_mb": 12000},
            "previews": {"vram_mb": previews_mb, "ram_mb": 12000},
        },
        seconds_per_update=1.0,
        setup_seconds=10.0,
        seconds_per_preview=1.0,
        source="probe",
    )


def _enqueue_resolved(service, resolution, template, name="probed"):
    return service.enqueue(
        set_id=_SET_ID,
        name=name,
        base_model="m",
        trigger_word="t",
        template_path=template,
        config_overrides=presets.to_patch(presets.resolved_values(resolution)),
        locked=presets.to_patch(_ZIT["locked"]),
        resolution=resolution,
        pins=dict(_SMALL),
    )


@pytest.fixture
def watched(monkeypatch, tmp_path):
    """A stub trainer slow enough to be seen in each phase, read as holding
    :data:`_HOLDS_MB` on the card, with each launch counted."""
    monkeypatch.setenv("FAKE_RUN_STEP_DELAY", "0.05")
    launches = tmp_path / "launches.txt"
    monkeypatch.setenv("FAKE_RUN_LAUNCHES", str(launches))
    monkeypatch.setattr(training_service_module, "_SHORT_POLL_S", 0.02)
    monkeypatch.setattr(
        measurements, "gpu_mb", lambda pids, device: _HOLDS_MB if pids else None
    )

    def count():
        return launches.read_text().splitlines() if launches.exists() else []

    return count


def _run_to_end(hub, service, job, timeout=60.0):
    service.start()
    try:
        assert _poll_until(
            lambda: (
                _job_status(hub, job["uuid"]).status
                in ("completed", "error", "stopped")
            ),
            timeout=timeout,
        )
        # completed_at is written once the import is done.
        _poll_until(lambda: _job_status(hub, job["uuid"]).completed_at, timeout=10)
    finally:
        service.stop()
    return _job_status(hub, job["uuid"])


def test_an_unmeasured_setup_is_checked_then_learned_from(
    hub, repo_dir, watched, monkeypatch
):
    """The first run of a setup probes it, trains on what the probe measured,
    and leaves its own measurement for the next run."""
    seen = _spy_pass_pids(hub, monkeypatch)
    lease = _StubLease()
    service = _make_service(
        hub,
        repo_dir,
        lease=lease,
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    prompts = ["t reading", "a man reading", "t at a cafe"]
    resolution = _resolve_small(hub, prompts=prompts)
    assert resolution["probe"] is True and resolution["profile"] == "q8"
    job = _enqueue_resolved(
        service, resolution, _write_template(repo_dir, steps=4, save_every=2)
    )
    # A newer recipe arrives from pixlstash.dev while the job waits (#2122):
    # the re-resolved run uses it and its row says so.
    newer = {**_ZIT, "version": _ZIT["version"] + 1}
    monkeypatch.setattr(presets, "get_preset", lambda pid: newer)

    row = _run_to_end(hub, service, job)

    assert row.status == "completed", row.error
    assert row.preset == f"zimage-turbo@{newer['version']}"
    # One probe of six updates, then the run itself.
    assert watched() == [f"steps={training_service_module.PROBE_STEPS}", "steps=4"]
    phases = [entry["phase"] for entry in json.loads(row.phase_log)]
    assert phases.index("checking") < phases.index("training")
    assert any(call.get("shown_as") == "checking" for call in lease.progress_calls)
    settled = json.loads(row.resolved_profile)
    assert (settled["profile"], settled["probe"]) == ("q8", False)
    assert (settled["evidence"], settled["vram_mb"]) == ("measured", 9900)
    measured = measurements.read(hub, settled["fingerprint"])
    assert measured["source"] == "run"
    assert measured["phase_peaks"]["training"]["vram_mb"] == _HOLDS_MB
    assert measured["seconds_per_update"] is not None
    # The probe put its pid on the run while it trained, and was checked for
    # an orphan before its directory was cleared.
    assert seen["probe"] and all(
        pid is not None and pid == row_pid for pid, row_pid in seen["probe"]
    )
    assert seen["checked"], "the probe never checked for an orphaned pass"
    # The probe draws one preview; the run draws every prompt.
    probe_config = os.path.join(
        os.path.dirname(hub.path), "training", job["uuid"], "probe", ".job_config.json"
    )
    with open(probe_config, encoding="utf-8") as fh:
        probe_samples = json.load(fh)["config"]["process"][0]["sample"]["samples"]
    assert [s["prompt"] for s in probe_samples] == prompts[:1]
    run_samples = json.loads(row.rendered_config)["config"]["process"][0]["sample"]
    assert [s["prompt"] for s in run_samples["samples"]] == prompts
    # The next run of the same setup is admitted on it, with no probe.
    again = _resolve_small(hub, prompts=prompts)
    assert (again["probe"], again["evidence"]) == (False, "measured")
    assert again["estimate"]["basis"] == "measured"


def test_running_out_of_memory_steps_to_more_offloading(
    hub, repo_dir, watched, monkeypatch
):
    monkeypatch.setenv("FAKE_RUN_OOM_UNLESS_OFFLOAD", "1")
    service = _make_service(
        hub,
        repo_dir,
        lease=_StubLease(),
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    resolution = _resolve_small(hub)
    assert resolution["fallbacks"] == ["q8_cached", "q8_offload"]
    job = _enqueue_resolved(
        service, resolution, _write_template(repo_dir, steps=4, save_every=2)
    )

    row = _run_to_end(hub, service, job)

    assert row.status == "completed", row.error
    assert len(watched()) == 4  # q8 and q8_cached ran out, q8_offload held
    assert json.loads(row.resolved_profile)["profile"] == "q8_offload"
    model = json.loads(row.rendered_config)["config"]["process"][0]["model"]
    assert model["layer_offloading"] is True
    # A probe that ran out of memory measured nothing.
    assert measurements.read(hub, resolution["fingerprint"]) is None


def test_previews_that_do_not_fit_step_to_the_next_profile(hub, repo_dir, watched):
    """#2411: measured after the run was queued, q8's previews do not fit
    beside training. The next profile is probed before they are put off, and
    the run draws its previews while training on it."""
    service = _make_service(
        hub,
        repo_dir,
        lease=_StubLease(),
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    resolution = _resolve_small(hub)
    assert resolution["profile"] == "q8"
    job = _enqueue_resolved(
        service, resolution, _write_template(repo_dir, steps=4, save_every=2)
    )
    _record_big_previews(hub, "q8")

    row = _run_to_end(hub, service, job)

    assert row.status == "completed", row.error
    # One probe of q8_cached, then the run with previews (the stub's are small).
    assert watched() == [f"steps={training_service_module.PROBE_STEPS}", "steps=4"]
    settled = json.loads(row.resolved_profile)
    assert (settled["profile"], settled["previews_deferred"]) == ("q8_cached", False)
    assert "train.disable_sampling" not in settled["values"]


@pytest.mark.parametrize("measured_before_queueing", [False, True])
def test_a_failed_check_of_the_next_profile_keeps_the_one_that_trains(
    hub, repo_dir, watched, monkeypatch, measured_before_queueing
):
    """#2411: q8 trains but its previews do not fit; the RAM guard stops the
    check of q8_cached. The run trains on q8 with its previews put off
    instead of failing, also when q8 was measured before the run was queued
    and so was passed over for q8_cached there."""
    monkeypatch.setattr(
        training_service_module,
        "read_ram",
        lambda: {"total_mb": 64_000, "available_mb": 500},
    )
    service = _make_service(
        hub,
        repo_dir,
        lease=_StubLease(),
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    if measured_before_queueing:
        _record_big_previews(hub, "q8")
    resolution = _resolve_small(hub)
    assert resolution["profile"] == ("q8_cached" if measured_before_queueing else "q8")
    job = _enqueue_resolved(
        service, resolution, _write_template(repo_dir, steps=4, save_every=2)
    )
    _record_big_previews(hub, "q8")

    row = _run_to_end(hub, service, job)

    assert row.status == "completed", row.error
    settled = json.loads(row.resolved_profile)
    assert (settled["profile"], settled["previews_deferred"]) == ("q8", True)
    # The stopped check, the run, then its previews from each checkpoint.
    assert watched()[:2] == [f"steps={training_service_module.PROBE_STEPS}", "steps=4"]


def test_out_of_probes_a_measured_profile_further_on_still_counts(
    hub, repo_dir, watched, monkeypatch
):
    """The probe budget bounds probes only: with one left after q8 runs out of
    memory, q8_cached (unmeasured) is skipped and the measured q8_offload is
    still taken."""
    monkeypatch.setenv("FAKE_RUN_OOM_UNLESS_OFFLOAD", "1")
    monkeypatch.setattr(training_service_module, "MAX_PROBES", 1)
    _record_big_previews(hub, "q8_offload", previews_mb=9000)
    service = _make_service(
        hub,
        repo_dir,
        lease=_StubLease(),
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    resolution = _resolve_small(hub)
    assert resolution["profile"] == "q8"
    job = _enqueue_resolved(
        service, resolution, _write_template(repo_dir, steps=4, save_every=2)
    )

    row = _run_to_end(hub, service, job)

    assert row.status == "completed", row.error
    assert json.loads(row.resolved_profile)["profile"] == "q8_offload"
    # The one probe (q8, out of memory), then the run.
    assert watched() == [f"steps={training_service_module.PROBE_STEPS}", "steps=4"]


def test_a_loader_error_stops_the_search(hub, repo_dir, watched, monkeypatch):
    monkeypatch.setenv("FAKE_RUN_FAIL", "Error loading model: unexpected tensor")
    service = _make_service(
        hub,
        repo_dir,
        lease=_StubLease(),
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    job = _enqueue_resolved(
        service, _resolve_small(hub), _write_template(repo_dir, steps=4, save_every=2)
    )

    row = _run_to_end(hub, service, job)

    assert row.status == "error"
    assert row.error.startswith("Checking memory and speed failed: Error loading model")
    assert len(watched()) == 1


def test_the_ram_guard_stops_a_probe(hub, repo_dir, watched, monkeypatch):
    monkeypatch.setattr(
        training_service_module,
        "read_ram",
        lambda: {"total_mb": 64_000, "available_mb": 500},
    )
    lease = _StubLease()
    service = _make_service(
        hub,
        repo_dir,
        lease=lease,
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    job = _enqueue_resolved(
        service, _resolve_small(hub), _write_template(repo_dir, steps=4, save_every=2)
    )

    row = _run_to_end(hub, service, job)

    assert row.status == "error"
    assert row.error.startswith("Not enough system RAM to start")
    assert lease.release_count == 1


@pytest.mark.parametrize("id_prefix", [None, "09960682"])
def test_deferred_previews_are_drawn_from_each_checkpoint_after_training(
    hub, repo_dir, watched, monkeypatch, id_prefix
):
    """Measured: training fits, previews do not. The run trains without them and
    each kept checkpoint is sampled once training is done.

    An id that starts with eight digits gives the final checkpoint a name that
    reads like a step (``probed_09960682.safetensors``); it is still sampled at
    the run's own step."""
    if id_prefix:
        real_uuid4 = uuid.uuid4
        monkeypatch.setattr(
            uuid, "uuid4", lambda: uuid.UUID(id_prefix + real_uuid4().hex[8:])
        )
    seen = _spy_pass_pids(hub, monkeypatch)
    # No profile's previews fit, so none is worth trying for them (#2411).
    for profile in _ZIT["fit"]["auto"]:
        _record_big_previews(hub, profile)
    resolution = _resolve_small(hub)
    assert (resolution["probe"], resolution["previews_deferred"]) == (False, True)
    importer = _RecordingImport()
    service = _make_service(
        hub,
        repo_dir,
        lease=_StubLease(),
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
        importer=importer,
    )
    job = _enqueue_resolved(
        service, resolution, _write_template(repo_dir, steps=4, save_every=2)
    )

    row = _run_to_end(hub, service, job)

    assert row.status == "completed", row.error
    # Each profile was looked at for previews while training and none had
    # room, so the first that trains is kept.
    assert json.loads(row.resolved_profile)["profile"] == "q8"
    # The run, then one pass per checkpoint step: 2, and 4 once (the final
    # shares its step with the last save).
    assert watched() == ["steps=4", "steps=2", "steps=4"]
    run_dir = importer.calls[0][0]
    assert sorted(os.listdir(os.path.join(run_dir, "samples"))) == [
        "sample_000000002_0.jpg",
        "sample_000000004_0.jpg",
    ]
    work_dir = os.path.join(os.path.dirname(hub.path), "training", job["uuid"])
    assert not os.listdir(os.path.join(work_dir, "after"))
    # Each preview pass put its pid on the run while it drew, and was checked
    # for an orphan before its directory was cleared.
    assert len(seen["preview"]) >= 3 and all(
        pid is not None and pid == row_pid for pid, row_pid in seen["preview"]
    )
    assert seen["checked"] and set(seen["checked"]) == {work_dir}
    phases = [entry["phase"] for entry in json.loads(row.phase_log)]
    assert phases[-2:] == ["previews", "importing"]
    # The run's own record keeps the previews peak the deferral came from
    # (peaks merge by maximum), so the next run of the setup defers too.
    measured = measurements.read(hub, resolution["fingerprint"])
    assert measured["source"] == "run"
    assert measured["phase_peaks"]["previews"]["vram_mb"] == 30000
    assert _resolve_small(hub)["previews_deferred"] is True


def test_the_lease_shrinks_to_the_peak_once_the_first_checkpoint_is_saved(
    hub, repo_dir
):
    service = _make_service(
        hub,
        repo_dir,
        lease=_StubLease(),
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    lease = _StubLease()
    watch = training_service_module._Watch()
    measurements.note_peak(watch.peaks, "training", 10_000, None)
    saved = TrainingProgress(
        status="running",
        info="Training",
        step=250,
        total_steps=1000,
        speed="",
        checkpoints=["run_000000250.safetensors"],
    )
    before = TrainingProgress(
        status="running", info="Training", step=200, total_steps=1000, speed=""
    )
    service._maybe_shrink("job", lease, watch, before, deferred=False)
    service._maybe_shrink("job", lease, watch, saved, deferred=False)
    assert lease.events == []  # its previews' peak has not been seen yet
    # Inside the previews, before the VAE decode each one ends with: wait.
    usage = {"pids": set(), "peak_vram_mb": 12_000, "ram_mb": None}
    watch.see("previews", usage, 1.0)
    service._maybe_shrink("job", lease, watch, saved, deferred=False)
    assert lease.events == []
    watch.see("training", {**usage, "peak_vram_mb": 9_000}, 2.0)
    service._maybe_shrink("job", lease, watch, saved, deferred=False)
    service._maybe_shrink("job", lease, watch, saved, deferred=False)
    assert lease.events == ["shrink 13200"]
    deferred = training_service_module._Watch()
    measurements.note_peak(deferred.peaks, "training", 10_000, None)
    service._maybe_shrink("job", lease, deferred, saved, deferred=True)
    assert lease.events == ["shrink 13200", "shrink 11000"]


def test_memory_a_dead_trainer_kept_is_named_not_released(hub, repo_dir, monkeypatch):
    monkeypatch.setattr(training_service_module, "RELEASE_GRACE_S", 0.05)
    monkeypatch.setattr(measurements, "gpu_mb", lambda pids, device: 3072)
    monkeypatch.setattr(
        training_service_module,
        "ledger",
        lambda: SimpleNamespace(processes=lambda device: {4242: 3072, 77: 100}),
    )
    tenant = AiToolkitProbe()
    service = TrainingService(
        hub,
        _LIBRARY_UUID,
        os.path.dirname(hub.path),
        request_gpu=_StubLease().request,
        import_run=_RecordingImport(),
        notify=_CapturingNotify(),
        config_provider=lambda: {},
        dataset_builder=_make_dataset_builder(),
        tenant=tenant,
    )
    lease = _StubLease()
    lease.device = "GPU-test"
    watch = training_service_module._Watch(pids={4242, 4243})

    service._check_released("job", lease, watch)

    assert lease.events == ["warn aitoolkit_memory_not_released"]
    assert tenant.holder_label() == "AI Toolkit · memory not released"
    # Given back in time: nothing to say.
    monkeypatch.setattr(measurements, "gpu_mb", lambda pids, device: None)
    quiet = _StubLease()
    quiet.device = "GPU-test"
    service._check_released("job", quiet, watch)
    assert quiet.events == []
    assert tenant.holder_label() is None


def test_a_live_run_is_adopted_at_start_up_and_reconciled_on_that_lease(hub, repo_dir):
    """adopt_live_runs grants the surviving run its lease before anything else
    starts; reconcile_on_startup resumes watching it on that lease instead of
    asking again."""
    adopted = _StubLease()
    adopted.held = True
    asked = _StubLease()
    alive = {"value": True}

    class _Backend:
        def handle_for(self, work_dir, job_id, name, pid, training_folder=None):
            return LocalAiToolkit.handle_for(
                work_dir, job_id, name, pid, training_folder=training_folder
            )

        def is_alive(self, handle):
            return alive["value"]

        def progress(self, handle):
            alive["value"] = False
            return TrainingProgress(
                status="stopped", info="Job stopped", step=3, total_steps=4, speed=""
            )

        def reconcile(self, handle):
            return self.progress(handle)

    service = TrainingService(
        hub,
        _LIBRARY_UUID,
        os.path.dirname(hub.path),
        request_gpu=asked.request,
        adopt_gpu=adopted.request,
        import_run=_RecordingImport(),
        notify=_CapturingNotify(),
        config_provider=lambda: {"repo_path": repo_dir, "python": sys.executable},
        dataset_builder=_make_dataset_builder(),
        backend_factory=lambda repo_path, python: _Backend(),
        poll_interval_s=0.02,
    )
    job = service.enqueue(
        set_id=_SET_ID,
        name="survivor",
        base_model="m",
        trigger_word="t",
        template_path="unused.json",
        resolution=_RESOLUTION,
    )
    training_runs.update_run(
        hub,
        job["uuid"],
        status="running",
        pid=os.getpid(),
        training_folder=os.path.join(repo_dir, "output"),
    )

    service.adopt_live_runs()
    assert (adopted.acquire_count, adopted.need) == (1, (12000, 16000))
    service.reconcile_on_startup()
    assert _poll_until(lambda: _job_status(hub, job["uuid"]).status == "stopped")
    assert asked.acquire_count == 0
    assert _poll_until(lambda: adopted.release_count == 1)


def test_a_run_trained_but_not_imported_before_a_restart_is_imported(hub, repo_dir):
    """A restart while a finished run draws its previews (or is imported) leaves
    it completed with no completed_at; the next start's reconcile imports it."""
    final = os.path.join(repo_dir, "output", "run", "run.safetensors")
    importer = _RecordingImport()

    class _Backend:
        def handle_for(self, work_dir, job_id, name, pid, training_folder=None):
            return LocalAiToolkit.handle_for(
                work_dir, job_id, name, pid, training_folder=training_folder
            )

        def is_alive(self, handle):
            return False

        def reconcile(self, handle):
            return TrainingProgress(
                status="completed",
                info="Training completed",
                step=4,
                total_steps=4,
                speed="",
                final_checkpoint=final,
            )

    service = TrainingService(
        hub,
        _LIBRARY_UUID,
        os.path.dirname(hub.path),
        request_gpu=_StubLease().request,
        import_run=importer,
        notify=_CapturingNotify(),
        config_provider=lambda: {"repo_path": repo_dir, "python": sys.executable},
        dataset_builder=_make_dataset_builder(),
        backend_factory=lambda repo_path, python: _Backend(),
    )
    job = service.enqueue(
        set_id=_SET_ID,
        name="trained",
        base_model="m",
        trigger_word="t",
        template_path="unused.json",
        resolution=_RESOLUTION,
    )
    training_runs.update_run(
        hub,
        job["uuid"],
        status="completed",
        training_folder=os.path.join(repo_dir, "output"),
    )

    service.reconcile_on_startup()

    row = _job_status(hub, job["uuid"])
    assert row.status == "completed" and row.completed_at
    assert [call[0] for call in importer.calls] == [os.path.dirname(final)]
    assert row.finished_unwatched == 0


def test_a_lease_adopted_for_a_run_reconcile_cannot_take_is_released(
    hub, repo_dir, monkeypatch
):
    adopted = _StubLease()
    service = _make_service(
        hub,
        repo_dir,
        lease=_StubLease(),
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    service._adopted["gone"] = adopted

    def broken(info):
        raise RuntimeError("cannot read the run")

    monkeypatch.setattr(
        training_runs,
        "unfinished_runs",
        lambda hub, library_uuid: [{"uuid": "gone"}],
    )
    monkeypatch.setattr(service, "_reconcile_one", broken)

    service.reconcile_on_startup()

    assert adopted.release_count == 1 and service._adopted == {}


def test_a_run_finishing_at_shutdown_still_records_its_measurement(hub, repo_dir):
    service = _make_service(
        hub,
        repo_dir,
        lease=_StubLease(),
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    resolution = _resolve_small(hub)
    job = _enqueue_resolved(service, resolution, "unused.json")
    watch = training_service_module._Watch()
    measurements.note_peak(watch.peaks, "training", 9000, 12000)
    done = TrainingProgress(
        status="completed", info="", step=4, total_steps=4, speed="", samples=[]
    )
    handle = SimpleNamespace(loss_db_path=os.path.join(repo_dir, "missing.db"))
    service._stop.set()

    service._after_training(None, handle, job, done, _StubLease(), watch)

    measured = measurements.read(hub, resolution["fingerprint"])
    assert measured["phase_peaks"]["training"]["vram_mb"] == 9000


# ---------------------------------------------------------------------------
# Passes' pids, the active slot, and the stopping flag (#2295)
# ---------------------------------------------------------------------------


def _spy_pass_pids(hub, monkeypatch):
    """Record, on each poll of a probe or a preview pass, the pass's pid and
    the pid the run's own row holds at that moment."""
    seen = {"probe": [], "preview": []}
    real_progress = LocalAiToolkit.progress

    def progress(self, handle):
        if handle.work_dir.endswith(os.sep + "probe"):
            kind = "probe"
        elif f"{os.sep}after{os.sep}" in handle.work_dir:
            kind = "preview"
        else:
            kind = None
        if kind is not None:
            row = training_runs.get_run_by_uuid(hub, handle.job_id)
            seen[kind].append((handle.pid, row["pid"]))
        return real_progress(self, handle)

    monkeypatch.setattr(LocalAiToolkit, "progress", progress)
    checked = seen["checked"] = []
    real_stop_orphaned = TrainingService._stop_orphaned_pass

    def stop_orphaned(self, backend, job_uuid, name, work_dir):
        checked.append(work_dir)
        return real_stop_orphaned(self, backend, job_uuid, name, work_dir)

    monkeypatch.setattr(TrainingService, "_stop_orphaned_pass", stop_orphaned)
    return seen


def test_a_trainer_a_crashed_pass_left_running_is_stopped_before_a_new_pass(
    hub, repo_dir, tmp_path, monkeypatch
):
    """A pass records its pid on the run. The next pass of that run stops the
    trainer the pid names, if it is a pass of this run, before it clears the
    directory and launches. The orphan may be a preview pass at another step
    than the one about to run; a process that is no pass of the run survives."""
    monkeypatch.setattr(
        "pixlstash.training.local_ai_toolkit._CANCEL_GRACE_SECONDS", 0.2
    )
    service = _make_service(
        hub,
        repo_dir,
        lease=_StubLease(),
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    training_runs.create_run(
        hub,
        library_uuid=_LIBRARY_UUID,
        uuid="orphaned-job",
        name="orphaned",
        set_id=None,
        character_id=_CHARACTER_ID,
        base_model="m",
        trigger_word="t",
    )
    backend = service._make_backend()
    # A crash left the step-1000 preview pass running; the restart begins at 250.
    pass_dir = str(tmp_path / "after" / "1000")
    os.makedirs(pass_dir)
    os.makedirs(tmp_path / "after" / "250")
    pass_handle = LocalAiToolkit.handle_for(pass_dir, "orphaned-job", "orphaned")
    stranger = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", backend.run_script],
        start_new_session=True,
    )
    trainer = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(60)",
            backend.run_script,
            pass_handle.config_path,
        ],
        start_new_session=True,
    )
    try:
        service._update_job("orphaned-job", pid=stranger.pid)
        service._stop_orphaned_pass(backend, "orphaned-job", "orphaned", str(tmp_path))
        time.sleep(0.5)
        assert stranger.poll() is None, "a process that is no pass was stopped"

        service._update_job("orphaned-job", pid=trainer.pid)
        service._stop_orphaned_pass(backend, "orphaned-job", "orphaned", str(tmp_path))
        assert trainer.wait(timeout=10) == -15, "the orphaned trainer was not stopped"
    finally:
        for process in (trainer, stranger):
            if process.poll() is None:
                process.kill()
                process.wait()


def test_a_re_attached_run_leaves_a_later_runs_backend_in_the_slot(
    hub, repo_dir, monkeypatch
):
    """A re-attached run clears the active slot only while it still holds it:
    once a later run has taken the slot, its end must not clear that run."""
    service = _make_service(
        hub,
        repo_dir,
        lease=_StubLease(),
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    mine = service._make_backend()
    later = service._make_backend()
    monkeypatch.setattr(service, "_after_training", lambda *args, **kwargs: False)
    monkeypatch.setattr(service, "_check_released", lambda *args, **kwargs: None)
    ended = TrainingProgress(
        status="stopped", info="Job stopped", step=1, total_steps=2, speed=""
    )

    def take_over_while_polling(*args, **kwargs):
        # The lease shrank, so a later run got the card and took the slot.
        service._active_backend, service._active_job_uuid = later, "later-job"
        return ended

    monkeypatch.setattr(service, "_poll_until_done", take_over_while_polling)
    service._poll_and_finalize_reattached(
        mine, SimpleNamespace(work_dir=repo_dir), {"uuid": "old-job"}, _StubLease()
    )
    assert service._active_backend is later
    assert service._active_job_uuid == "later-job"

    monkeypatch.setattr(service, "_poll_until_done", lambda *args, **kwargs: ended)
    service._poll_and_finalize_reattached(
        mine, SimpleNamespace(work_dir=repo_dir), {"uuid": "old-job"}, _StubLease()
    )
    assert service._active_backend is None and service._active_job_uuid is None


def test_a_finished_run_leaves_the_slot_to_a_later_run(hub, repo_dir, monkeypatch):
    """The worker's end clears the slot only if it still holds its own backend."""
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
    later = service._make_backend()
    real_after = service._after_training

    def hand_over(*args, **kwargs):
        result = real_after(*args, **kwargs)
        with service._active_lock:
            service._active_backend = later
            service._active_job_uuid = "later-job"
        return result

    monkeypatch.setattr(service, "_after_training", hand_over)
    job = service.enqueue(
        set_id=_SET_ID,
        name="handover",
        base_model="m",
        trigger_word="t",
        template_path=_write_template(repo_dir, steps=4, save_every=2),
        resolution=_RESOLUTION,
    )

    _run_to_terminal(hub, service, job, expect="completed")

    assert service._active_backend is later
    assert service._active_job_uuid == "later-job"


def test_progress_and_cancel_write_the_stopping_flag_under_the_active_lock(
    hub, repo_dir, monkeypatch
):
    """The poll reads the stopping flag and writes the status in one held
    lock, and cancel writes stopping under that lock too, so a cancel cannot
    land between the poll's read and its write and be undone."""
    service = _make_service(
        hub,
        repo_dir,
        lease=_StubLease(grant=True),
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    training_runs.create_run(
        hub,
        library_uuid=_LIBRARY_UUID,
        uuid="racing-job",
        name="racing",
        set_id=None,
        character_id=_CHARACTER_ID,
        base_model="m",
        trigger_word="t",
    )
    training_runs.update_run(hub, "racing-job", status="running")
    written = []
    real_update = service._update_job

    def update_job(job_uuid, **fields):
        if "status" in fields:
            written.append((fields["status"], service._active_lock.locked()))
        real_update(job_uuid, **fields)

    monkeypatch.setattr(service, "_update_job", update_job)
    running = TrainingProgress(
        status="running", info="Training", step=1, total_steps=10, speed=""
    )
    service._persist_progress("racing-job", running, SimpleNamespace(log_path=""))
    assert written == [("running", True)]

    written.clear()
    service.cancel(training_runs.get_run_by_uuid(hub, "racing-job")["id"])
    assert written[0] == ("stopping", True)
