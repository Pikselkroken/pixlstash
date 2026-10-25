"""Full-lifecycle tests for the TrainingService, GPU-free.

These drive the real :class:`~pixlstash.services.training_service.TrainingService`
end to end against the CPU stub trainer (``fake_run.py``, symlinked as ``run.py``
into a temp "repo", exactly as ``test_local_ai_toolkit.py`` sets it up) and a
real temp-SQLite :class:`~pixlstash.database.VaultDatabase`. Every external
dependency is injected: a stub VRAM lease (recording request/release order), a
capturing ``notify``, a ``config_provider`` returning the temp repo + venv
python, and a ``dataset_builder`` stub that writes fake image/caption pairs.

No GPU, no torch, no long fixed sleeps: all waits are poll-with-timeout loops and
``FAKE_RUN_STEP_DELAY=0`` so the suite runs fast.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import time

import pytest
from sqlmodel import select

from pixlstash.database import VaultDatabase
from pixlstash.db_models.character import Character
from pixlstash.db_models.picture_set import PictureSet
from pixlstash.db_models.trained_lora import TrainedLora
from pixlstash.db_models.training_job import TrainingJob
from pixlstash.event_types import EventType
from pixlstash.services.training_service import TrainingService

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_FAST_TEMPLATE_PATH = os.path.join(_THIS_DIR, "fast_template.json")


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

    def __init__(self, grant: bool = True):
        self.events: list[str] = []
        self.grant = grant
        self.acquire_count = 0
        self.release_count = 0

    def request(self, label: str, job_uuid: str) -> "_StubLease":
        self.acquire_count += 1
        self.events.append("acquire")
        return self

    def wait(self, cancel_event=None, timeout=None) -> bool:
        return self.grant

    def release(self) -> None:
        self.release_count += 1
        self.events.append("release")


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


@pytest.fixture
def db(tmp_path):
    """A real temp-SQLite VaultDatabase (migrations auto-run on construct)."""
    image_root = tmp_path / "vault"
    image_root.mkdir()
    database = VaultDatabase(str(image_root / "vault.db"))
    try:
        yield database
    finally:
        database.close()


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


def _make_service(
    db,
    repo_dir,
    *,
    lease,
    notify,
    dataset_builder,
    poll_interval_s=0.05,
):
    """Build a TrainingService wired to the temp repo + injected deps."""
    return TrainingService(
        db,
        db.image_root,
        request_gpu=lease.request,
        notify=notify,
        config_provider=lambda: {
            "repo_path": repo_dir,
            "python": sys.executable,
            "models_dir": "",
            "commit": "deadbeef",
        },
        dataset_builder=dataset_builder,
        poll_interval_s=poll_interval_s,
    )


def _job_status(db, job_uuid):
    return db.run_immediate_read_task(
        lambda s: s.exec(
            select(TrainingJob).where(TrainingJob.uuid == job_uuid)
        ).first()
    )


def _make_character(db, name="Test Character") -> int:
    """Create a Character row (FK target for character-scoped jobs)."""

    def _insert(session):
        char = Character(name=name)
        session.add(char)
        session.commit()
        session.refresh(char)
        return char.id

    return db.run_task(_insert)


def _make_set(db, name="Test Set") -> int:
    """Create a PictureSet row (FK target for set-scoped jobs)."""

    def _insert(session):
        pset = PictureSet(name=name)
        session.add(pset)
        session.commit()
        session.refresh(pset)
        return pset.id

    return db.run_task(_insert)


@pytest.fixture(autouse=True)
def _fast_step_delay(monkeypatch):
    """Drive the stub with no per-step delay."""
    monkeypatch.setenv("FAKE_RUN_STEP_DELAY", "0")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_enqueue_validates_exactly_one_scope(db, repo_dir):
    lease, notify = _StubLease(), _CapturingNotify()
    service = _make_service(
        db,
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
        )

    set_id = _make_set(db)
    job = service.enqueue(
        set_id=set_id,
        name="ok",
        base_model="m",
        trigger_word="t",
        template_path=template,
    )
    assert job.status == "queued"
    assert job.uuid


def test_full_lifecycle_queued_to_completed_lora(db, repo_dir):
    """enqueue → worker runs → running → completed → TrainedLora on disk."""
    lease = _StubLease(grant=True)
    notify = _CapturingNotify()
    service = _make_service(
        db,
        repo_dir,
        lease=lease,
        notify=notify,
        dataset_builder=_make_dataset_builder(image_count=3),
    )
    template = _write_template(repo_dir, steps=8, save_every=4)
    character_id = _make_character(db)

    service.start()
    try:
        job = service.enqueue(
            character_id=character_id,
            name="charjob",
            base_model="Z-Image",
            trigger_word="ohwx_person",
            template_path=template,
        )

        # It progresses to running with a real step.
        assert _poll_until(lambda: _job_status(db, job.uuid).status == "running"), (
            "never reached running"
        )

        # ... then to completed.
        assert _poll_until(lambda: _job_status(db, job.uuid).status == "completed"), (
            f"final status was {_job_status(db, job.uuid).status!r}"
        )
    finally:
        service.stop()

    final = _job_status(db, job.uuid)
    assert final.status == "completed"
    assert final.step == 8
    assert final.total_steps == 8
    assert final.started_at is not None
    assert final.completed_at is not None
    assert final.ai_toolkit_commit == "deadbeef"
    assert final.dataset_picture_ids
    assert json.loads(final.dataset_picture_ids) == [1, 2, 3]
    # Loss curve populated.
    assert final.loss_curve
    loss = json.loads(final.loss_curve)
    assert len(loss) == 8
    assert all("step" in p and "loss" in p for p in loss)

    # A TrainedLora row with a real file under <image_root>/loras/.
    lora = db.run_immediate_read_task(
        lambda s: s.exec(
            select(TrainedLora).where(TrainedLora.job_id == final.id)
        ).first()
    )
    assert lora is not None
    assert lora.character_id == character_id
    assert lora.set_id is None
    assert lora.step == 8
    assert lora.file_path == f"loras/{lora.uuid}.safetensors"
    lora_abs = os.path.join(db.image_root, lora.file_path)
    assert os.path.exists(lora_abs)
    assert lora.file_size_bytes == os.path.getsize(lora_abs)
    assert lora.file_sha and len(lora.file_sha) == 64

    # Samples recorded on the job and present under the vault.
    assert final.sample_image_paths
    samples = json.loads(final.sample_image_paths)
    assert samples
    for rel in samples:
        assert os.path.exists(os.path.join(db.image_root, rel))

    # GPU lease acquired before submit and released after, in order.
    assert lease.events == ["acquire", "release"]

    # PLUGIN_PROGRESS events emitted with run_id == job.uuid and a completed one.
    progress = notify.plugin_progress()
    assert progress, "no PLUGIN_PROGRESS events emitted"
    assert all(p["run_id"] == job.uuid for p in progress)
    assert all(p["plugin"] == "ai-toolkit" for p in progress)
    assert any(p["status"] == "completed" for p in progress)


def test_cancel_midrun_ends_stopped_and_releases_lease(db, repo_dir, monkeypatch):
    """cancel() mid-run leaves the job 'stopped' and releases the GPU lease."""
    monkeypatch.setenv("FAKE_RUN_STEP_DELAY", "0.02")
    lease = _StubLease(grant=True)
    notify = _CapturingNotify()
    service = _make_service(
        db,
        repo_dir,
        lease=lease,
        notify=notify,
        dataset_builder=_make_dataset_builder(),
    )
    template = _write_template(repo_dir, steps=500, save_every=50)
    set_id = _make_set(db)

    service.start()
    try:
        job = service.enqueue(
            set_id=set_id,
            name="cancelme",
            base_model="m",
            trigger_word="t",
            template_path=template,
        )
        # Wait until it is genuinely running with a recorded pid.
        assert _poll_until(
            lambda: (
                _job_status(db, job.uuid).status == "running"
                and _job_status(db, job.uuid).pid is not None
            )
        ), "job did not reach running with a pid"

        assert service.cancel(job.id) is True

        assert _poll_until(
            lambda: _job_status(db, job.uuid).status in {"stopped", "error"}
        ), f"job did not finalize, status={_job_status(db, job.uuid).status!r}"
    finally:
        service.stop()

    final = _job_status(db, job.uuid)
    assert final.status == "stopped", f"expected stopped, got {final.status!r}"
    assert final.step < 500
    # Lease released exactly as many times as acquired (no leak).
    assert lease.acquire_count == lease.release_count == 1


def test_zero_image_dataset_marks_error_no_lease_leak(db, repo_dir):
    """A zero-image dataset marks the job error and never touches the GPU lease."""
    lease = _StubLease(grant=True)
    notify = _CapturingNotify()
    service = _make_service(
        db,
        repo_dir,
        lease=lease,
        notify=notify,
        dataset_builder=_make_dataset_builder(image_count=0),
    )
    template = _write_template(repo_dir, steps=4, save_every=2)
    set_id = _make_set(db)

    service.start()
    try:
        job = service.enqueue(
            set_id=set_id,
            name="empty",
            base_model="m",
            trigger_word="t",
            template_path=template,
        )
        assert _poll_until(lambda: _job_status(db, job.uuid).status == "error"), (
            f"status={_job_status(db, job.uuid).status!r}"
        )
    finally:
        service.stop()

    final = _job_status(db, job.uuid)
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


def test_lease_cancelled_while_waiting_stops_the_job_unlaunched(db, repo_dir):
    """A waiting lease cancelled from the Tasks tab ends the job, never trained."""
    lease = _StubLease(grant=False)
    service = _make_service(
        db,
        repo_dir,
        lease=lease,
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    template = _write_template(repo_dir, steps=4, save_every=2)
    set_id = _make_set(db)

    service.start()
    try:
        job = service.enqueue(
            set_id=set_id,
            name="refused",
            base_model="m",
            trigger_word="t",
            template_path=template,
        )
        assert _poll_until(lambda: _job_status(db, job.uuid).status == "stopped"), (
            f"status={_job_status(db, job.uuid).status!r}"
        )
    finally:
        service.stop()

    final = _job_status(db, job.uuid)
    assert final.pid is None and final.started_at is None
    assert lease.events == ["acquire", "release"]


def test_cancel_queued_job_drops_the_lease_it_waits_on(db, repo_dir):
    """Cancelling a job that waits for the GPU releases its lease, never trains."""
    lease = _WaitingLease()
    service = _make_service(
        db,
        repo_dir,
        lease=lease,
        notify=_CapturingNotify(),
        dataset_builder=_make_dataset_builder(),
    )
    template = _write_template(repo_dir, steps=4, save_every=2)
    set_id = _make_set(db)

    service.start()
    try:
        job = service.enqueue(
            set_id=set_id,
            name="waiting",
            base_model="m",
            trigger_word="t",
            template_path=template,
        )
        assert _poll_until(lambda: service._waiting is not None)
        assert service.cancel(_job_status(db, job.uuid).id)
        assert _poll_until(lambda: service._waiting is None)
    finally:
        service.stop()

    final = _job_status(db, job.uuid)
    assert final.status == "stopped"
    assert final.pid is None and final.started_at is None
    assert lease.acquire_count == 1 and lease.release_count >= 1


def test_reconcile_on_startup_dead_running_job_finalizes(db, repo_dir):
    """A job left 'running' with a dead pid is finalized; lease not left held.

    We pre-stage a job row as 'running' with a dead pid and a save_root that
    contains a final checkpoint, so reconcile must finalize it to 'completed'
    and import the LoRA without re-acquiring/holding the GPU lease.
    """
    lease = _StubLease(grant=True)
    notify = _CapturingNotify()
    service = _make_service(
        db,
        repo_dir,
        lease=lease,
        notify=notify,
        dataset_builder=_make_dataset_builder(),
    )

    job_uuid = "dead-job-uuid-0001"
    work_dir = os.path.join(db.image_root, "training", job_uuid)
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

    character_id = _make_character(db)

    # Insert the TrainingJob row as 'running' with a dead pid.
    def _insert(session):
        job = TrainingJob(
            uuid=job_uuid,
            name="deadjob",
            status="running",
            character_id=character_id,
            base_model="m",
            trigger_word="t",
            pid=999_999_999,
            training_folder=training_folder,
            total_steps=4,
            step=4,
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job.id

    db.run_task(_insert)

    service.reconcile_on_startup()

    final = _job_status(db, job_uuid)
    assert final.status == "completed", f"got {final.status!r}"
    lora = db.run_immediate_read_task(
        lambda s: s.exec(
            select(TrainedLora).where(TrainedLora.uuid != None)  # noqa: E711
        ).first()
    )
    assert lora is not None
    assert os.path.exists(os.path.join(db.image_root, lora.file_path))
    # Dead-process reconcile must not have taken the GPU lease at all.
    assert lease.acquire_count == 0
    assert lease.release_count == 0


def test_current_worker_snapshot_idle_and_running(db, repo_dir):
    """The /workers/progress snapshot is None when idle, populated when running."""
    from pixlstash.training.backend import TrainingProgress

    lease, notify = _StubLease(), _CapturingNotify()
    service = _make_service(
        db,
        repo_dir,
        lease=lease,
        notify=notify,
        dataset_builder=_make_dataset_builder(),
    )

    # Idle: no active job → None.
    assert service.current_worker_snapshot() is None

    # Simulate a running job's stashed live state.
    service._active_job_uuid = "abc123"
    service._active_job_name = "my_lora"
    service._latest_progress = TrainingProgress(
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
    snap = service.current_worker_snapshot()
    assert snap is not None
    assert snap["label"] == "lora_training"
    assert snap["current"] == 120 and snap["total"] == 300
    assert snap["remaining"] == 180
    assert snap["active"] is True and snap["running"] is True
    assert snap["job_name"] == "my_lora"
    assert snap["loss"] == 0.42

    # A terminal status is not "running" → snapshot drops back to None.
    service._latest_progress = TrainingProgress(
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
    assert service.current_worker_snapshot() is None
