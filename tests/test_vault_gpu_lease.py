"""Tests for the Vault GPU training-lease helper.

A full Vault is heavy to construct (opens a SQLite DB, builds an inference
engine), so these tests bind the real ``Vault.acquire_training_gpu_lease`` /
``release_training_gpu_lease`` methods onto a lightweight stub that exposes only
the attributes the lease code touches: a stubbed task runner and a stubbed
``set_keep_models_in_memory``.  This keeps the test focused on the lease contract
(pause + drain, record prior flag, request unload, restore + resume, reentrancy).
"""

import threading

from pixlstash.vault import Vault


class _StubTaskRunner:
    def __init__(self, drain_result=True):
        self.paused = False
        self.pause_calls = []
        self.resume_calls = 0
        self._drain_result = drain_result

    def pause_gpu_queue(self, wait=False, timeout=None):
        self.pause_calls.append({"wait": wait, "timeout": timeout})
        self.paused = True
        return self._drain_result

    def resume_gpu_queue(self):
        self.resume_calls += 1
        self.paused = False


class _StubVault:
    """Minimal stand-in carrying exactly the state the lease methods use."""

    GPU_LEASE_DRAIN_TIMEOUT_S = Vault.GPU_LEASE_DRAIN_TIMEOUT_S

    # Bind the real implementations under test.
    acquire_training_gpu_lease = Vault.acquire_training_gpu_lease
    release_training_gpu_lease = Vault.release_training_gpu_lease

    def __init__(self, task_runner, keep_models_in_memory=True):
        self._gpu_lease_lock = threading.Lock()
        self._gpu_lease_count = 0
        self._gpu_lease_prev_keep_models = None
        self._task_runner = task_runner
        self._keep_models_in_memory = keep_models_in_memory
        self.keep_models_calls = []

    def set_keep_models_in_memory(self, value):
        # Mirror the real method's externally-observable effect.
        self.keep_models_calls.append(bool(value))
        self._keep_models_in_memory = bool(value)


def test_acquire_pauses_drains_records_and_unloads():
    runner = _StubTaskRunner(drain_result=True)
    vault = _StubVault(runner, keep_models_in_memory=True)

    ok = vault.acquire_training_gpu_lease(drain_timeout_s=30.0)

    assert ok is True
    # Paused with a blocking drain.
    assert len(runner.pause_calls) == 1
    assert runner.pause_calls[0]["wait"] is True
    assert runner.pause_calls[0]["timeout"] == 30.0
    # Prior flag recorded, then unload requested (keep_models_in_memory -> False).
    assert vault._gpu_lease_prev_keep_models is True
    assert vault.keep_models_calls == [False]
    assert vault._keep_models_in_memory is False
    assert vault._gpu_lease_count == 1


def test_release_restores_flag_and_resumes():
    runner = _StubTaskRunner(drain_result=True)
    vault = _StubVault(runner, keep_models_in_memory=True)

    vault.acquire_training_gpu_lease()
    vault.release_training_gpu_lease()

    # keep_models_in_memory was driven False on acquire, restored True on release.
    assert vault.keep_models_calls == [False, True]
    assert vault._keep_models_in_memory is True
    assert runner.resume_calls == 1
    assert runner.paused is False
    assert vault._gpu_lease_count == 0
    assert vault._gpu_lease_prev_keep_models is None


def test_acquire_preserves_keep_false():
    # Starting with keep_models_in_memory already False, release must restore False.
    runner = _StubTaskRunner(drain_result=True)
    vault = _StubVault(runner, keep_models_in_memory=False)

    vault.acquire_training_gpu_lease()
    assert vault._gpu_lease_prev_keep_models is False
    vault.release_training_gpu_lease()
    # acquire forces False (unload), release restores the prior False.
    assert vault.keep_models_calls == [False, False]
    assert vault._keep_models_in_memory is False


def test_double_acquire_is_reentrant():
    runner = _StubTaskRunner(drain_result=True)
    vault = _StubVault(runner, keep_models_in_memory=True)

    assert vault.acquire_training_gpu_lease() is True
    assert vault.acquire_training_gpu_lease() is True
    # Only the first acquire pauses + unloads.
    assert len(runner.pause_calls) == 1
    assert vault.keep_models_calls == [False]
    assert vault._gpu_lease_count == 2

    # First release does not restore yet.
    vault.release_training_gpu_lease()
    assert vault._gpu_lease_count == 1
    assert runner.resume_calls == 0
    assert vault.keep_models_calls == [False]

    # Final release restores + resumes.
    vault.release_training_gpu_lease()
    assert vault._gpu_lease_count == 0
    assert runner.resume_calls == 1
    assert vault.keep_models_calls == [False, True]


def test_double_release_is_safe():
    runner = _StubTaskRunner(drain_result=True)
    vault = _StubVault(runner, keep_models_in_memory=True)

    vault.acquire_training_gpu_lease()
    vault.release_training_gpu_lease()
    # Second release with no active lease is a harmless no-op.
    vault.release_training_gpu_lease()
    assert vault._gpu_lease_count == 0
    assert runner.resume_calls == 1  # not resumed twice


def test_release_without_acquire_is_noop():
    runner = _StubTaskRunner(drain_result=True)
    vault = _StubVault(runner, keep_models_in_memory=True)

    vault.release_training_gpu_lease()
    assert runner.resume_calls == 0
    assert vault.keep_models_calls == []
    assert vault._gpu_lease_count == 0


def test_acquire_returns_false_when_drain_times_out():
    # Drain fails: lease is not granted, models are NOT unloaded.
    runner = _StubTaskRunner(drain_result=False)
    vault = _StubVault(runner, keep_models_in_memory=True)

    ok = vault.acquire_training_gpu_lease(drain_timeout_s=0.1)

    assert ok is False
    assert vault._gpu_lease_count == 0
    assert vault.keep_models_calls == []  # no unload
    assert vault._gpu_lease_prev_keep_models is None
    # Queue stays paused so the caller can retry/release.
    assert runner.paused is True


def test_disable_background_workers_is_noop():
    vault = _StubVault(task_runner=None, keep_models_in_memory=True)

    assert vault.acquire_training_gpu_lease() is True
    assert vault.keep_models_calls == []
    assert vault._gpu_lease_count == 0
    # Release is a safe no-op too.
    vault.release_training_gpu_lease()
    assert vault._gpu_lease_count == 0
