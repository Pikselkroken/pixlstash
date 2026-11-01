"""The VRAM arbiter's admission arithmetic, against a fake ledger.

Pure: no server, no GPU. The ledger is a stand-in that answers whatever the
test says the card holds, so every figure here is chosen, not measured.
"""

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from pixlstash import gpu_select
from pixlstash.resources import arbiter as arbiter_module
from pixlstash.resources.arbiter import (
    LeaseKind,
    LeaseState,
    ResourceArbiter,
    Tenant,
    safety_margin_mb,
)
from pixlstash.resources.ledger import DeviceReading, Ledger

CARD = "GPU-00000000-0000-0000-0000-000000000001"
TOTAL = 24576  # 24 GB; margin is 5 % = 1228 MB


class FakeLedger:
    def __init__(self, used_mb=0, measurable=True):
        self.used_mb = used_mb
        self.measurable = measurable

    def devices(self, max_age_s=1.0):
        return [
            DeviceReading(
                uuid=CARD,
                name="Test GPU",
                total_mb=TOTAL if self.measurable else 0,
                used_mb=self.used_mb,
                free_mb=TOTAL - self.used_mb if self.measurable else 0,
                measurable=self.measurable,
            )
        ]

    def device(self, uuid):
        return next((d for d in self.devices() if d.uuid == uuid), None)

    def pixlstash_device(self):
        return CARD

    def other_processes(self, limit=3):
        return []


class FakeComfy:
    def __init__(self, usage=0, busy=False, idle=True, device=CARD):
        self.usage = usage
        self.is_busy = busy
        self.is_idle = idle
        self._device = device
        self.freed = 0

    def device(self):
        return self._device

    def usage_mb(self):
        return self.usage

    def busy(self):
        return self.is_busy

    def idle(self):
        return self.is_idle

    def reclaim(self):
        self.freed += 1
        self.usage = 0
        return True


def make(used_mb=0, pixl_usage=None, measurable=True, enforcing=True, thread=True):
    fake = FakeLedger(used_mb, measurable)
    usage = {"mb": pixl_usage}
    arb = ResourceArbiter(
        fake,
        usage=lambda: usage["mb"],
        ram_reader=lambda: {"total_mb": 65536, "available_mb": 32768},
        enforcing=enforcing,
    )
    if not thread:
        # The test drives the reclaim ladder itself, one rung at a time.
        arb._ensure_thread = lambda: None
    return arb, fake, usage


def budget(free_mb):
    return free_mb - safety_margin_mb(TOTAL)


def test_margin_is_the_larger_of_one_gb_and_five_percent():
    assert safety_margin_mb(8192) == 1024
    assert safety_margin_mb(TOTAL) == int(TOTAL * 0.05)


def test_training_room_counts_what_tenants_give_back_but_not_other_apps():
    """PixlStash's own memory and an idle ComfyUI's cache are freed for an
    exclusive run, so they are room; what other apps hold is not."""
    arb, _fake, _ = make(used_mb=12000, pixl_usage=3000, thread=False)
    arb.register_probe(Tenant.COMFYUI, FakeComfy(usage=7000))
    room = arb.training_room(CARD)
    # 12000 used = 3000 PixlStash + 7000 ComfyUI + 2000 other apps.
    assert room == {"vram_mb": budget(TOTAL - 2000), "ram_mb": 32768}
    assert arb.training_room("GPU-elsewhere")["vram_mb"] is None
    unmeasured, _f, _u = make(measurable=False, thread=False)
    assert unmeasured.training_room(CARD)["vram_mb"] is None


def test_heavy_that_fits_is_granted_at_once_and_snapshot_counts_it():
    arb, _fake, _ = make(used_mb=4000)
    lease = arb.request(
        Tenant.AITOOLKIT, "train", device=CARD, need_mb=budget(TOTAL - 4000)
    )
    assert lease.state is LeaseState.GRANTED
    device = arb.snapshot()["devices"][0]
    assert device["reserved_mb"] == lease.need_mb
    assert device["budget_mb"] == 0


def test_heavy_that_does_not_fit_waits_with_figures():
    arb, _fake, _ = make(used_mb=12000)
    lease = arb.request(Tenant.AITOOLKIT, "train", device=CARD, need_mb=20000)
    assert lease.state is LeaseState.WAITING
    reason = lease.waiting_reason
    assert reason["code"] == "vram"
    assert reason["free_mb"] == budget(TOTAL - 12000)
    assert reason["short_by_mb"] == 20000 - budget(TOTAL - 12000)
    assert "Other apps holds 11.7 GB" in reason["text"]
    lease.release()


def test_outstanding_shrinks_as_the_tenant_grows_into_it():
    """Reservation minus growth since grant, never the reservation twice."""
    arb, fake, _ = make()
    comfy = FakeComfy(usage=0)
    arb.register_probe(Tenant.AITOOLKIT, comfy)
    first = arb.request(Tenant.AITOOLKIT, "a", device=CARD, need_mb=8000)
    assert arb.snapshot()["devices"][0]["reserved_mb"] == 8000
    # The tenant now really holds 6000 of it, and the card says so.
    comfy.usage = 6000
    fake.used_mb = 6000
    device = arb.snapshot()["devices"][0]
    assert device["reserved_mb"] == 2000
    # The bar draws it as AI Toolkit's, not as spare.
    assert device["reserved_by"] == {"aitoolkit": 2000}
    first.release()
    assert arb.snapshot()["devices"][0]["reserved_by"] == {}


def test_a_tenant_unreadable_at_grant_counts_growth_from_its_first_reading():
    arb, fake, _ = make()
    tenant = FakeComfy(usage=None)
    arb.register_probe(Tenant.AITOOLKIT, tenant)
    first = arb.request(Tenant.AITOOLKIT, "a", device=CARD, need_mb=8000)
    assert arb.snapshot()["devices"][0]["reserved_mb"] == 8000
    # Readable now, with 2000 it already held; that is not growth into the lease.
    tenant.usage = fake.used_mb = 2000
    assert arb.snapshot()["devices"][0]["reserved_mb"] == 8000
    # 6000 more is, and is no longer counted twice.
    tenant.usage = fake.used_mb = 8000
    assert arb.snapshot()["devices"][0]["reserved_mb"] == 2000
    first.release()


def test_comfyui_cache_counts_as_room_for_its_own_next_run():
    """Back-to-back runs of one workflow must not wait behind their own cache."""
    arb, fake, _ = make(used_mb=20000)
    comfy = FakeComfy(usage=19000)
    arb.register_probe(Tenant.COMFYUI, comfy)
    lease = arb.request(Tenant.COMFYUI, "same workflow", need_mb=19500)
    assert lease.state is LeaseState.GRANTED
    # The same request from anyone else does not get ComfyUI's cache.
    other = arb.request(Tenant.AITOOLKIT, "train", device=CARD, need_mb=19500)
    assert other.state is LeaseState.WAITING
    other.release()
    lease.release()


def test_comfyui_runs_share_one_reservation():
    """ComfyUI runs prompts one at a time: four runs reserve one run's need."""
    arb, _fake, _ = make()
    arb.register_probe(Tenant.COMFYUI, FakeComfy(usage=0))
    leases = [arb.request(Tenant.COMFYUI, f"run {i}", need_mb=9000) for i in range(4)]
    assert all(lease.state is LeaseState.GRANTED for lease in leases)
    assert arb.snapshot()["devices"][0]["reserved_mb"] == 9000
    for lease in leases:
        lease.release()


def test_a_comfyui_that_cannot_be_read_waits_on_its_last_card():
    """A failed read is not a remote ComfyUI, which would be granted unchecked."""
    arb, _fake, _ = make()
    comfy = FakeComfy(usage=0)
    arb.register_probe(Tenant.COMFYUI, comfy)
    arb.snapshot()

    def unreadable():
        raise RuntimeError("ComfyUI did not answer")

    comfy.usage_mb = unreadable
    lease = arb.request(Tenant.COMFYUI, "run", need_mb=1000)
    assert lease.device == CARD
    assert lease.state is LeaseState.WAITING
    assert lease.waiting_reason["code"] == "comfyui_busy"
    lease.release()


def test_a_comfyui_unreadable_from_its_first_read_waits_on_pixlstashs_card():
    """Never read is not remote either: no last card, so PixlStash's own."""
    arb, _fake, _ = make(used_mb=TOTAL - 1000, thread=False)
    comfy = FakeComfy(usage=0)

    def unreadable():
        raise RuntimeError("ComfyUI did not answer")

    comfy.device = unreadable
    arb.register_probe(Tenant.COMFYUI, comfy)
    lease = arb.request(Tenant.COMFYUI, "run", need_mb=4000)
    assert lease.device == CARD
    assert lease.state is LeaseState.WAITING
    lease.release()


def test_heavy_is_strict_fifo_per_device():
    arb, fake, _ = make(used_mb=10000)
    big = arb.request(Tenant.AITOOLKIT, "big", device=CARD, need_mb=20000)
    small = arb.request(Tenant.AITOOLKIT, "small", device=CARD, need_mb=1000)
    assert big.state is LeaseState.WAITING
    assert small.state is LeaseState.WAITING
    assert small.waiting_reason["code"] == "fifo"
    assert small.waiting_reason["position"] == 2
    fake.used_mb = 0
    arb.kick()
    big.release()
    assert small.state is LeaseState.GRANTED
    small.release()


def test_a_comfyui_run_that_fits_passes_a_comfyui_run_that_does_not():
    """One run too big for the card now must not hold back the next one."""
    arb, _fake, _ = make(used_mb=20000)
    arb.register_probe(Tenant.COMFYUI, FakeComfy(usage=0))
    big = arb.request(Tenant.COMFYUI, "big", need_mb=10000)
    small = arb.request(Tenant.COMFYUI, "small", need_mb=2000)
    assert big.waiting_reason["code"] == "vram"
    assert small.state is LeaseState.GRANTED
    # Another tenant still keeps its place, in both directions.
    train = arb.request(Tenant.AITOOLKIT, "train", device=CARD, need_mb=100)
    assert train.waiting_reason["code"] == "fifo"
    assert train.waiting_reason["blocked_by"][0]["label"] == "big"
    after = arb.request(Tenant.COMFYUI, "after", need_mb=2000)
    assert after.waiting_reason["code"] == "fifo"
    assert after.waiting_reason["blocked_by"][0]["label"] == "train"
    for lease in (after, train, small, big):
        lease.release()


def test_a_comfyui_run_passes_a_comfyui_run_waiting_to_run_alone():
    """Running alone keeps other tenants off; ComfyUI's runs never blocked it."""
    arb, _fake, _ = make(used_mb=20000)
    arb.register_probe(Tenant.COMFYUI, FakeComfy(usage=0))
    alone = arb.request(
        Tenant.COMFYUI, "alone", need_mb=10000, kind=LeaseKind.EXCLUSIVE
    )
    small = arb.request(Tenant.COMFYUI, "small", need_mb=2000)
    assert alone.waiting_reason["code"] == "vram"
    assert small.state is LeaseState.GRANTED
    for lease in (small, alone):
        lease.release()


def test_exclusive_blocks_light_while_granted_or_waiting():
    arb, fake, _ = make(used_mb=23000)
    exclusive = arb.request(
        Tenant.AITOOLKIT, "train", device=CARD, need_mb=8000, kind=LeaseKind.EXCLUSIVE
    )
    assert exclusive.state is LeaseState.WAITING
    assert arb.try_light("Tagging", need_mb=0) is None
    fake.used_mb = 0
    arb.kick()
    exclusive.release()
    exclusive = arb.request(
        Tenant.AITOOLKIT, "train", device=CARD, need_mb=8000, kind=LeaseKind.EXCLUSIVE
    )
    assert exclusive.state is LeaseState.GRANTED
    assert arb.try_light("Tagging", need_mb=0) is None
    exclusive.release()
    light = arb.try_light("Tagging", need_mb=0)
    assert light is not None and light.state is LeaseState.GRANTED
    light.release()


def test_a_slow_read_never_overwrites_a_newer_one():
    """The arbiter thread's pass can read the card, stall, and store late.

    Stored over a newer reading, the stale one makes the next request wait on
    memory that was already freed (a CI flake of the test above).
    """
    arb, fake, _ = make(used_mb=23000, thread=False)
    read_taken, go = threading.Event(), threading.Event()
    devices = fake.devices

    def stalled_devices(max_age_s=1.0):
        reading = devices(max_age_s)
        if threading.current_thread().name == "slow-pass":
            read_taken.set()
            go.wait(5)
        return reading

    fake.devices = stalled_devices
    slow = threading.Thread(target=arb._refresh, name="slow-pass")
    slow.start()
    try:
        assert read_taken.wait(5)
        fake.used_mb = 0
        arb._refresh()
    finally:
        go.set()
        slow.join(5)
    assert arb._devices[0].used_mb == 0


def test_a_cached_read_never_outranks_a_fresh_one_started_before_it():
    """A cached read can return a sample older than a fresh read in flight."""
    arb, fake, _ = make(used_mb=23000, thread=False)
    started, go = threading.Event(), threading.Event()
    devices = fake.devices

    def fresh_read_in_flight(max_age_s=1.0):
        if threading.current_thread().name == "fresh-read":
            started.set()
            go.wait(5)  # the card is sampled after the cached read lands
        return devices(max_age_s)

    fake.devices = fresh_read_in_flight
    fresh = threading.Thread(target=arb._refresh, name="fresh-read")
    fresh.start()
    try:
        assert started.wait(5)
        arb._refresh(cached=True)  # the ledger's cache: still 23 GB
        assert arb._devices[0].used_mb == 23000
        fake.used_mb = 0
    finally:
        go.set()
        fresh.join(5)
    assert arb._devices[0].used_mb == 0


def test_a_stalled_ledger_read_still_stores_the_tenants_it_read_after():
    """Tenants are read after the card: a read stalled on the card reads them last."""
    arb, fake, _ = make(thread=False)
    comfy = FakeComfy(usage=5000)
    arb.register_probe(Tenant.COMFYUI, comfy)
    started, go = threading.Event(), threading.Event()
    devices = fake.devices

    def stalled_devices(max_age_s=1.0):
        if threading.current_thread().name == "stalled-read":
            started.set()
            go.wait(5)
        return devices(max_age_s)

    fake.devices = stalled_devices
    stalled = threading.Thread(target=arb._refresh, name="stalled-read")
    stalled.start()
    try:
        assert started.wait(5)
        arb._refresh(cached=True)
        assert arb._probe_state[Tenant.COMFYUI]["usage"] == 5000
        comfy.usage = 0
    finally:
        go.set()
        stalled.join(5)
    assert arb._probe_state[Tenant.COMFYUI]["usage"] == 0


def test_a_cached_read_that_stalls_never_stores_over_a_fresh_one():
    """The cached pass samples 23 GB, stalls, and the fresh 0 must survive it."""
    arb, fake, _ = make(used_mb=23000, thread=False)
    sampled, go = threading.Event(), threading.Event()
    ram = arb._ram

    def stalled_ram():
        if threading.current_thread().name == "cached-read":
            sampled.set()
            go.wait(5)
        return ram()

    arb._ram = stalled_ram
    cached = threading.Thread(
        target=arb._refresh, kwargs={"cached": True}, name="cached-read"
    )
    cached.start()
    try:
        assert sampled.wait(5)
        fake.used_mb = 0
        arb._refresh()
    finally:
        go.set()
        cached.join(5)
    assert arb._devices[0].used_mb == 0


def test_a_stalled_tenant_read_never_stores_over_a_newer_one():
    """The stalled pass reads ComfyUI at 5000 MB, then a newer pass reads 0."""
    arb, _fake, _ = make(thread=False)
    sampled, go = threading.Event(), threading.Event()

    class StallingComfy(FakeComfy):
        def idle(self):  # the last thing a pass asks a tenant
            if threading.current_thread().name == "stalled-read":
                sampled.set()
                go.wait(5)
            return super().idle()

    comfy = StallingComfy(usage=5000)
    arb.register_probe(Tenant.COMFYUI, comfy)
    stalled = threading.Thread(target=arb._refresh, name="stalled-read")
    stalled.start()
    try:
        assert sampled.wait(5)
        comfy.usage = 0
        arb._refresh()
    finally:
        go.set()
        stalled.join(5)
    assert arb._probe_state[Tenant.COMFYUI]["usage"] == 0


def test_urgent_light_is_fit_checked_only():
    arb, _fake, _ = make(used_mb=23000)
    exclusive = arb.request(
        Tenant.AITOOLKIT, "train", device=CARD, need_mb=8000, kind=LeaseKind.EXCLUSIVE
    )
    urgent = arb.try_light("Face detection", need_mb=0, urgent=True)
    assert urgent is not None
    urgent.release()
    exclusive.release()


def test_light_is_refused_while_heavy_waits_for_memory_but_not_for_a_busy_comfyui():
    arb, _fake, _ = make(used_mb=20000)
    comfy = FakeComfy(usage=0, busy=True)
    arb.register_probe(Tenant.COMFYUI, comfy)
    run = arb.request(Tenant.COMFYUI, "run", need_mb=1000)
    assert run.waiting_reason["code"] == "comfyui_busy"
    light = arb.try_light("Tagging", need_mb=100)
    assert light is not None, "a ComfyUI busy with outside work must not stall tagging"
    light.release()
    run.release()
    heavy = arb.request(Tenant.AITOOLKIT, "train", device=CARD, need_mb=10000)
    assert heavy.waiting_reason["code"] == "vram"
    assert arb.try_light("Tagging", need_mb=100) is None
    heavy.release()


def test_a_light_lease_with_no_card_is_on_the_cpu():
    arb, _fake, _ = make()
    arb.set_pixlstash_on_gpu(False)
    light = arb.try_light("Tagging", need_mb=500)
    assert light.on_cpu and light.device is None
    row = next(r for r in arb.snapshot()["leases"] if r["id"] == light.id)
    assert row["on_cpu"] and row["held_mb"] == 0
    light.release()


def test_a_failed_pass_does_not_end_the_thread_waiting_leases_need(monkeypatch):
    monkeypatch.setattr(arbiter_module, "POLL_S", 0.05)
    arb, fake, _ = make(used_mb=20000)
    lease = arb.request(Tenant.AITOOLKIT, "train", device=CARD, need_mb=8000)
    assert lease.state is LeaseState.WAITING
    real = fake.devices
    failed = []

    def once_then_free(max_age_s=1.0):
        if not failed:
            failed.append(True)
            raise RuntimeError("test-nvml-hiccup")
        fake.used_mb = 0
        return real(max_age_s)

    fake.devices = once_then_free
    arb.kick()
    deadline = time.monotonic() + 5
    while lease.state is LeaseState.WAITING and time.monotonic() < deadline:
        time.sleep(0.02)
    assert failed, "the pass never read the card"
    assert lease.state is LeaseState.GRANTED, "the thread died on one bad reading"
    lease.release()


def test_a_card_whose_use_keeps_moving_does_not_spin_the_poll(monkeypatch):
    """A model loading moves the reading every pass; the loop still pauses."""
    monkeypatch.setattr(arbiter_module, "POLL_S", 0.05)
    monkeypatch.setattr(arbiter_module, "MOVED_POLL_S", 0.05, raising=False)
    arb, fake, _ = make(used_mb=20000)
    reads = []
    real = fake.devices

    def moving(max_age_s=1.0):
        reads.append(1)
        fake.used_mb = 20000 + (len(reads) % 2) * 1000
        return real(max_age_s)

    fake.devices = moving
    lease = arb.request(Tenant.AITOOLKIT, "train", device=CARD, need_mb=8000)
    time.sleep(0.5)
    lease.release()
    # About ten at one pass per 0.05 s; a loop with no pause makes hundreds.
    assert len(reads) < 40, f"{len(reads)} passes in half a second"


def test_pixlstash_cap_limits_only_pixlstash():
    arb, _fake, _ = make()
    arb.set_pixlstash_cap_gb(2)
    first = arb.try_light("Tagging", need_mb=1500)
    assert first is not None
    assert arb.try_light("OCR", need_mb=1000) is None
    run = arb.request(Tenant.AITOOLKIT, "train", device=CARD, need_mb=8000)
    assert run.state is LeaseState.GRANTED
    run.release()
    first.release()


def test_ram_is_checked_for_leases_that_state_it():
    arb, _fake, _ = make()
    lease = arb.request(
        Tenant.AITOOLKIT, "train", device=CARD, need_mb=1000, ram_mb=40000
    )
    assert lease.waiting_reason["code"] == "ram"
    lease.release()


def test_unmeasurable_card_runs_heavy_one_at_a_time_and_light_freely():
    arb, _fake, _ = make(measurable=False)
    first = arb.request(Tenant.AITOOLKIT, "train", device=CARD, need_mb=99999)
    assert first.state is LeaseState.GRANTED
    arb.register_probe(Tenant.COMFYUI, FakeComfy(usage=None))
    second = arb.request(Tenant.COMFYUI, "run", need_mb=1)
    assert second.waiting_reason["code"] == "unmeasurable"
    assert arb.try_light("Tagging", need_mb=500) is not None
    first.release()
    assert second.state is LeaseState.GRANTED
    second.release()


def test_unmeasured_comfyui_run_keeps_other_tenants_off_but_not_comfyui():
    arb, _fake, _ = make()
    arb.register_probe(Tenant.COMFYUI, FakeComfy(usage=0))
    unmeasured = arb.request(Tenant.COMFYUI, "new workflow", need_mb=0, alone=True)
    assert unmeasured.state is LeaseState.GRANTED
    again = arb.request(Tenant.COMFYUI, "another run", need_mb=4000)
    assert again.state is LeaseState.GRANTED
    train = arb.request(Tenant.AITOOLKIT, "train", device=CARD, need_mb=1000)
    assert train.waiting_reason["code"] == "exclusive_held"
    for lease in (train, again, unmeasured):
        lease.release()


def test_cancel_withdraws_a_waiting_lease_and_leaves_a_granted_one_alone():
    arb, _fake, _ = make(used_mb=20000)
    granted = arb.request(Tenant.AITOOLKIT, "running", device=CARD, need_mb=100)
    waiting = arb.request(Tenant.AITOOLKIT, "waiting", device=CARD, need_mb=20000)
    # Granted a moment before the owner pressed Cancel: its work is running.
    assert arb.cancel(granted.id) is None
    assert granted.state is LeaseState.GRANTED
    assert arb.cancel(waiting.id) is waiting
    assert waiting.state is LeaseState.RELEASED
    granted.release()


def test_run_anyway_grants_one_and_keeps_the_others_in_order():
    arb, _fake, _ = make(used_mb=20000)
    first = arb.request(Tenant.AITOOLKIT, "first", device=CARD, need_mb=10000)
    second = arb.request(Tenant.AITOOLKIT, "second", device=CARD, need_mb=10000)
    third = arb.request(Tenant.AITOOLKIT, "third", device=CARD, need_mb=10000)
    arb.run_anyway(second.id)
    assert second.state is LeaseState.GRANTED
    assert first.state is LeaseState.WAITING
    assert third.waiting_reason["code"] == "fifo"
    assert third.waiting_reason["blocked_by"][0]["label"] == "first"
    for lease in (first, second, third):
        lease.release()


def test_reclaim_ladder_yields_pixlstash_then_frees_comfyui():
    arb, fake, usage = make(used_mb=20000, pixl_usage=6000, thread=False)
    comfy = FakeComfy(usage=8000)
    arb.register_probe(Tenant.COMFYUI, comfy)
    yielded = []

    def yielder(lease):
        yielded.append(lease.id)
        usage["mb"] = 0
        fake.used_mb = 14000
        return ["TagTask"]

    arb.add_yielder(yielder)
    lease = arb.request(Tenant.AITOOLKIT, "train", device=CARD, need_mb=15000)
    assert lease.state is LeaseState.WAITING
    assert arb._reclaim_step()
    assert yielded == [lease.id]
    assert arb.snapshot()["preempted"][0]["worker_type"] == "TagTask"
    arb._refresh()
    with arb._lock:
        arb._evaluate_locked()
    assert lease.state is LeaseState.WAITING, "still short after PixlStash yields"
    assert arb._reclaim_step()
    assert comfy.freed == 1
    fake.used_mb = 6000
    arb._refresh()
    with arb._lock:
        arb._evaluate_locked()
    assert lease.state is LeaseState.GRANTED
    assert arb.snapshot()["preempted"] == []
    lease.release()


def test_comfyui_is_never_asked_to_free_for_its_own_run():
    arb, _fake, usage = make(used_mb=23000, pixl_usage=None, thread=False)
    comfy = FakeComfy(usage=8000)
    arb.register_probe(Tenant.COMFYUI, comfy)
    lease = arb.request(Tenant.COMFYUI, "run", need_mb=20000)
    assert lease.state is LeaseState.WAITING
    while arb._reclaim_step():
        pass
    assert comfy.freed == 0
    lease.release()


def test_exclusive_makes_pixlstash_yield_before_it_is_granted():
    arb, _fake, _ = make(thread=False)
    yielded = []
    arb.add_yielder(lambda lease: yielded.append(lease.id) or [])
    arb.register_probe(Tenant.COMFYUI, FakeComfy(usage=0))
    lease = arb.request(Tenant.COMFYUI, "alone", kind=LeaseKind.EXCLUSIVE)
    assert lease.state is LeaseState.WAITING
    arb._reclaim_step()
    with arb._lock:
        arb._evaluate_locked()
    assert yielded == [lease.id]
    assert lease.state is LeaseState.GRANTED
    lease.release()


def test_wait_returns_on_grant_from_another_thread():
    arb, fake, _ = make(used_mb=20000)
    lease = arb.request(Tenant.AITOOLKIT, "train", device=CARD, need_mb=10000)
    fake.used_mb = 0
    done = threading.Event()
    threading.Thread(target=lambda: (lease.wait(timeout=10), done.set())).start()
    arb.kick()
    assert done.wait(10)
    assert lease.state is LeaseState.GRANTED
    lease.release()


def test_events_announce_heavy_changes_only():
    arb, _fake, _ = make(used_mb=20000)
    events = []
    arb.add_listener(events.append)
    arb.try_light("Tagging", need_mb=0)
    lease = arb.request(Tenant.AITOOLKIT, "train", device=CARD, need_mb=10000)
    lease.release()
    assert [e["change"] for e in events] == ["waiting", "released"]


def test_remote_tenant_is_granted_at_once():
    arb, _fake, _ = make(used_mb=24000)
    arb.register_probe(Tenant.COMFYUI, FakeComfy(device=None))
    lease = arb.request(Tenant.COMFYUI, "remote", need_mb=99999)
    assert lease.state is LeaseState.GRANTED
    assert lease.device is None
    lease.release()


@pytest.mark.parametrize("constant", [True, False])
def test_rollback_constant_grants_everything(monkeypatch, constant):
    monkeypatch.setattr(arbiter_module, "VRAM_ORCHESTRATION_ENFORCING", constant)
    fake = FakeLedger(used_mb=24000)
    arb = ResourceArbiter(fake, usage=lambda: None, ram_reader=lambda: {})
    lease = arb.request(Tenant.AITOOLKIT, "train", device=CARD, need_mb=10000)
    assert (lease.state is LeaseState.GRANTED) is (not constant)
    assert (arb.try_light("Tagging", need_mb=10000) is not None) is (not constant)
    lease.release()


# ── choosing PixlStash's GPU (gpu_select) ───────────────────────────────────


def _gpu(uuid, total_mb, name="NVIDIA GeForce RTX 3060"):
    return {
        "uuid": uuid,
        "name": name,
        "vendor": "nvidia",
        "total_mb": total_mb,
        "free_mb": total_mb,
        "integrated": False,
        "backend": "cu128",
    }


BIG = _gpu("GPU-big", 24576, "NVIDIA GeForce RTX 4090")
SPARE = _gpu("GPU-spare", 12288)
TINY = _gpu("GPU-tiny", 2048, "NVIDIA GeForce GT 1030")


def test_auto_takes_a_spare_comfyui_is_not_on():
    chosen, reason = gpu_select.choose([BIG, SPARE], "auto", "GPU-big")
    assert chosen == "GPU-spare"
    assert "RTX 3060" in reason


def test_auto_assumes_comfyui_on_the_first_card_when_never_seen():
    assert gpu_select.choose([BIG, SPARE], "auto", None)[0] == "GPU-spare"


def test_auto_shares_the_biggest_card_when_no_spare_is_big_enough():
    chosen, reason = gpu_select.choose([BIG, TINY], "auto", "GPU-big")
    assert chosen == "GPU-big"
    assert "Shares" in reason


def test_a_chosen_gpu_wins_and_a_vanished_one_falls_back_to_auto():
    assert gpu_select.choose([BIG, SPARE], "GPU-big", "GPU-big")[0] == "GPU-big"
    assert gpu_select.choose([BIG, SPARE], "GPU-gone", "GPU-big")[0] == "GPU-spare"


def test_apply_sets_the_visible_device_and_an_own_setting_wins(monkeypatch, tmp_path):
    config = tmp_path / "server-config.json"
    config.write_text('{"compute_gpu": "auto", "comfyui_gpu_uuid": "GPU-big"}')
    monkeypatch.setattr(gpu_select, "inventory", lambda nvml=None: [BIG, SPARE])
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    monkeypatch.delenv(gpu_select.RESOLVED_ENV, raising=False)
    # Under pytest torch is already loaded; apply refuses then, which is the
    # guard being exercised second. Hide it for the first half.
    torch = sys.modules.pop("torch", None)
    try:
        assert gpu_select.apply(str(config)) == "GPU-spare"
        assert os.environ["CUDA_VISIBLE_DEVICES"] == "GPU-spare"
        # Kept for app.main to log again once logging is set up.
        assert gpu_select.NOTE[0] == logging.INFO
        assert "GPU-spare" in gpu_select.NOTE[1]
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1")
        assert gpu_select.apply(str(config)) is None
        assert os.environ["CUDA_VISIBLE_DEVICES"] == "1"
    finally:
        if torch is not None:
            sys.modules["torch"] = torch
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES")
    monkeypatch.setitem(sys.modules, "torch", object())
    assert gpu_select.apply(str(config)) is None, "too late once torch is loaded"
    assert "CUDA_VISIBLE_DEVICES" not in os.environ


def test_one_gpu_is_nothing_to_choose(monkeypatch, tmp_path):
    monkeypatch.setattr(gpu_select, "inventory", lambda nvml=None: [BIG])
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    assert gpu_select.apply(str(tmp_path / "missing.json")) is None
    assert "CUDA_VISIBLE_DEVICES" not in os.environ


def test_the_gpu_is_chosen_before_anything_loads_torch():
    """A torch import ahead of gpu_select makes the choice a silent no-op."""
    probe = (
        "import sys; import pixlstash.gpu_select; "
        "assert 'torch' not in sys.modules, 'gpu_select loads torch'"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr
    # And app.py applies it before its first import that reaches the server.
    source = (Path(gpu_select.__file__).parent / "app.py").read_text()
    applied = source.index("gpu_select.apply()")
    assert applied < source.index("from pixlstash.server import")
    assert applied < source.index("from pixlstash.pixl_logging import")


def test_an_unreachable_comfyui_is_not_taken_for_a_remote_one(monkeypatch):
    """Remote is granted unchecked; a ComfyUI never reached must not be."""
    import requests

    from pixlstash.resources import comfyui_tenant
    from pixlstash.resources.comfyui_tenant import ComfyUIProbe

    def unreachable(path):
        raise requests.ConnectionError("test-unreachable")

    monkeypatch.setattr(comfyui_tenant, "ledger", FakeLedger)
    probe = ComfyUIProbe("test-client")
    probe.watch("http://comfy.invalid")
    probe._get = unreachable
    # Taken as sharing PixlStash's card, so its runs are fit-checked.
    assert probe.device() == CARD
    assert probe._mapping[0] == 0.0, "an unreachable answer was cached"


def test_comfyuis_reading_counts_only_on_the_card_its_lease_holds():
    arb, _fake, _ = make()
    arb.register_probe(Tenant.COMFYUI, FakeComfy(usage=9000, device="GPU-other"))
    arb.kick()
    lease = arb.request(Tenant.COMFYUI, "run", device=CARD, need_mb=3000)
    assert lease.state is LeaseState.GRANTED
    with arb._lock:
        arb._probe_state[Tenant.COMFYUI] = {"device": "GPU-other", "usage": 9000}
        assert arb._held_mb_locked(lease) == 3000
        arb._probe_state[Tenant.COMFYUI] = {"device": CARD, "usage": 9000}
        assert arb._held_mb_locked(lease) == 9000
    lease.release()


def test_a_failed_comfyui_read_stands_longer_than_a_good_one(monkeypatch):
    """A hung ComfyUI must not cost a timeout on every progress poll."""
    import requests

    from pixlstash.resources import comfyui_tenant
    from pixlstash.resources.comfyui_tenant import ComfyUIProbe

    calls = []

    def hung(path):
        calls.append(path)
        raise requests.Timeout("test-hung")

    now = [1000.0]
    monkeypatch.setattr(comfyui_tenant.time, "monotonic", lambda: now[0])
    probe = ComfyUIProbe("test-client")
    probe.watch("http://comfy.invalid")
    probe._get = hung
    assert probe.stats() is None
    now[0] += comfyui_tenant.STATS_TTL_S * 3
    assert probe.stats() is None
    assert calls == ["/system_stats"], "a failed read was asked again a second later"
    assert probe.stats(max_age_s=0.0) is None
    assert len(calls) == 2, "an explicit fresh read must still ask"


def test_a_read_failing_partway_through_the_card_match_is_not_cached_as_remote(
    monkeypatch,
):
    import requests

    from pixlstash.resources import comfyui_tenant
    from pixlstash.resources.comfyui_tenant import ComfyUIProbe

    mb = 1024 * 1024
    reads = iter(
        [
            {
                "devices": [
                    {
                        "name": "cuda:0 Test GPU",
                        "vram_total": TOTAL * mb,
                        "vram_free": TOTAL * mb,
                    }
                ]
            }
        ]
    )

    def first_read_only(path):
        try:
            return next(reads)
        except StopIteration:
            raise requests.ConnectionError("test-timeout") from None

    monkeypatch.setattr(comfyui_tenant, "ledger", FakeLedger)
    monkeypatch.setattr(comfyui_tenant.time, "sleep", lambda s: None)
    probe = ComfyUIProbe("test-client")
    probe.watch("http://comfy.invalid")
    probe._get = first_read_only
    # Never mapped, so taken as sharing PixlStash's card, not as remote.
    assert probe.device() == CARD
    assert probe._mapping[0] == 0.0, "a failed second read was cached as remote"


def _comfy_stats(total_mb, free_mb, torch_mb=0):
    mb = 1024 * 1024
    return {
        "devices": [
            {
                "name": "cuda:0 Test GPU : cudaMallocAsync",
                "vram_total": total_mb * mb,
                "vram_free": free_mb * mb,
                "torch_vram_total": torch_mb * mb,
            }
        ]
    }


def test_a_comfyui_reporting_cudas_smaller_card_total_is_still_on_this_card(
    monkeypatch,
):
    """CUDA's total leaves out what the driver keeps back (439 MB on a 32 GB
    card): a ComfyUI on this machine taken for a remote one has its memory
    drawn as other apps', and its runs granted unchecked."""
    from pixlstash.resources import comfyui_tenant
    from pixlstash.resources.comfyui_tenant import ComfyUIProbe

    monkeypatch.setattr(comfyui_tenant, "ledger", FakeLedger)
    monkeypatch.setattr(comfyui_tenant.time, "sleep", lambda s: None)
    probe = ComfyUIProbe("test-client")
    probe.watch("http://comfy.invalid")
    probe._get = lambda path: _comfy_stats(TOTAL - 439, TOTAL)
    assert probe.device() == CARD


class _ProcessLedger(FakeLedger):
    def __init__(self, processes):
        super().__init__()
        self.by_card = processes

    def processes(self, uuid=None):
        # As the ledger: no card means every card.
        if uuid is None:
            return {
                pid: mb for card in self.by_card.values() for pid, mb in card.items()
            }
        return dict(self.by_card.get(uuid, {}))


def test_comfyuis_figure_is_its_process_when_its_weights_live_outside_torch(
    monkeypatch,
):
    """Dynamic VRAM (comfy-aimdo) keeps the weights outside torch's allocator:
    ``torch_vram_total`` reads 160 MB while the process holds 13 GB."""
    from pixlstash.resources import comfyui_tenant
    from pixlstash.resources.comfyui_tenant import ComfyUIProbe

    ports = {4242: 8188, 5151: 7801}

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid

        def net_connections(self, kind="inet"):
            return [
                SimpleNamespace(status=psutil.CONN_LISTEN, laddr=()),
                SimpleNamespace(
                    status=psutil.CONN_LISTEN,
                    laddr=SimpleNamespace(port=ports[self.pid]),
                ),
            ]

    # The listener is not the largest process: the port picks it, not size.
    fake = _ProcessLedger({CARD: {5151: 20000, 4242: 13254}})
    monkeypatch.setattr(comfyui_tenant, "ledger", lambda: fake)
    monkeypatch.setattr(comfyui_tenant.time, "sleep", lambda s: None)
    monkeypatch.setattr(comfyui_tenant.psutil, "Process", FakeProcess)
    probe = ComfyUIProbe("test-client")
    probe.watch("http://comfy.invalid:8188")
    probe._get = lambda path: _comfy_stats(TOTAL, TOTAL, torch_mb=160)

    assert probe.device() == CARD
    assert probe.usage_mb() == 13254, "the process listening on ComfyUI's port"

    # NVML cannot attribute memory here (WDDM, Docker): the allocator stands.
    fake.by_card = {}
    assert probe.usage_mb() == 160

    # Nothing on the card listens on ComfyUI's port (a forwarding launcher).
    fake.by_card = {CARD: {5151: 20000}}
    assert probe.usage_mb() == 160

    # No allocator figure at all: the process's stands, and with neither
    # ComfyUI's memory is unread, not zero.
    stats = _comfy_stats(TOTAL, TOTAL)
    del stats["devices"][0]["torch_vram_total"]
    probe._get = lambda path: stats
    probe._stats = (0.0, None)
    fake.by_card = {CARD: {4242: 13254}}
    assert probe.usage_mb() == 13254
    fake.by_card = {}
    assert probe.usage_mb() is None
    probe._get = lambda path: _comfy_stats(TOTAL, TOTAL, torch_mb=160)
    probe._stats = (0.0, None)

    # Never the card of a remote ComfyUI, whatever runs here on that port.
    fake.by_card = {CARD: {4242: 13254}}
    probe._mapping = (probe._mapping[0], None)
    assert probe.usage_mb() == 160


def test_the_ledger_lists_one_cards_processes_without_its_own():
    mb = 1024 * 1024
    on_card = {
        0: [(os.getpid(), 900), (4242, 13254)],
        1: [(5151, 800), (6161, None)],
    }
    nvml = SimpleNamespace(
        nvmlInit=lambda: None,
        nvmlDeviceGetCount=lambda: 2,
        nvmlDeviceGetHandleByIndex=lambda index: index,
        nvmlDeviceGetUUID=lambda handle: f"GPU-{handle}",
        nvmlDeviceGetComputeRunningProcesses=lambda handle: [
            SimpleNamespace(pid=pid, usedGpuMemory=used and used * mb)
            for pid, used in on_card[handle]
        ],
        NVML_VALUE_NOT_AVAILABLE=-1,
    )
    ledger = Ledger(nvml)
    assert ledger.processes("GPU-0") == {4242: 13254}
    assert ledger.processes("GPU-1") == {5151: 800}, "not available is no figure"
    assert ledger.processes() == {4242: 13254, 5151: 800}


def test_a_local_comfyui_loading_a_model_is_still_on_this_card(monkeypatch):
    """Mid-load, ComfyUI's free memory and NVML's disagree by GBs; read as
    remote, its runs would be granted unchecked for a minute."""
    from pixlstash.resources import comfyui_tenant
    from pixlstash.resources.comfyui_tenant import ComfyUIProbe

    class Listening:
        def __init__(self, pid):
            self.pid = pid

        def net_connections(self, kind="inet"):
            return [
                SimpleNamespace(
                    status=psutil.CONN_LISTEN, laddr=SimpleNamespace(port=8188)
                )
            ]

    fake = _ProcessLedger({CARD: {4242: 9000}})
    monkeypatch.setattr(comfyui_tenant, "ledger", lambda: fake)
    monkeypatch.setattr(comfyui_tenant.time, "sleep", lambda s: None)
    monkeypatch.setattr(comfyui_tenant.psutil, "Process", Listening)
    loading = _comfy_stats(TOTAL, TOTAL - 8000)
    probe = ComfyUIProbe("test-client")
    probe.watch("http://127.0.0.1:8188")
    probe._get = lambda path: loading
    assert probe.device() == CARD

    # Not on a loopback address: a remote ComfyUI may share its port with a
    # local one, so likeness still decides, and it misses.
    remote = ComfyUIProbe("test-client")
    remote.watch("http://comfy.invalid:8188")
    remote._get = lambda path: loading
    assert remote.device() is None


def test_a_comfyui_on_a_card_a_gb_off_in_size_is_not_on_this_card(monkeypatch):
    from pixlstash.resources import comfyui_tenant
    from pixlstash.resources.comfyui_tenant import ComfyUIProbe

    monkeypatch.setattr(comfyui_tenant, "ledger", FakeLedger)
    monkeypatch.setattr(comfyui_tenant.time, "sleep", lambda s: None)
    probe = ComfyUIProbe("test-client")
    probe.watch("http://comfy.invalid")
    probe._get = lambda path: _comfy_stats(TOTAL - 1100, TOTAL)
    assert probe.device() is None


def test_an_identical_card_elsewhere_is_told_apart_by_a_fresh_second_read(
    monkeypatch,
):
    """Same name, size and free memory on the first read; only the second
    read, taken fresh, shows the other machine's card moving."""
    from pixlstash.resources import comfyui_tenant
    from pixlstash.resources.comfyui_tenant import ComfyUIProbe

    reads = iter([_comfy_stats(TOTAL, TOTAL), _comfy_stats(TOTAL, TOTAL)])
    monkeypatch.setattr(comfyui_tenant, "ledger", FakeLedger)
    monkeypatch.setattr(comfyui_tenant.time, "sleep", lambda s: None)
    probe = ComfyUIProbe("test-client")
    probe.watch("http://comfy.invalid")
    probe._get = lambda path: next(reads, _comfy_stats(TOTAL, TOTAL - 3000))
    assert probe.device() is None


def test_a_granted_lease_carries_its_live_progress_in_the_snapshot():
    arb, _fake, _ = make(used_mb=4000)
    lease = arb.request(
        Tenant.AITOOLKIT,
        "LoRA training 'x'",
        device=CARD,
        need_mb=budget(TOTAL - 4000),
        kind=LeaseKind.EXCLUSIVE,
        job_id="job-1",
    )
    row = next(r for r in arb.snapshot()["leases"] if r["id"] == lease.id)
    assert row["progress"] is None

    lease.set_progress(step=2750, total=4000, loss=0.123)
    row = next(r for r in arb.snapshot()["leases"] if r["id"] == lease.id)
    assert row["progress"] == {"step": 2750, "total": 4000, "loss": 0.123}
    assert row["job_id"] == "job-1"

    lease.set_progress(step=2800, total=4000, loss=None)
    row = next(r for r in arb.snapshot()["leases"] if r["id"] == lease.id)
    assert row["progress"] == {"step": 2800, "total": 4000, "loss": None}

    lease.release()
    assert all(r["id"] != lease.id for r in arb.snapshot()["leases"])


def test_adopt_grants_a_live_trainer_at_once_and_counts_its_memory_once():
    """A trainer that outlived a restart already holds its memory: it is
    granted without waiting, and what it holds is its reservation, not a
    second helping on top of it."""
    arb, fake, _ = make(used_mb=20000, thread=False)
    trainer = FakeComfy(usage=18000)
    arb.register_probe(Tenant.AITOOLKIT, trainer)
    # Asked for the ordinary way, the same need waits: the card is full.
    asked = arb.request(
        Tenant.AITOOLKIT, "asked", device=CARD, need_mb=18000, kind=LeaseKind.EXCLUSIVE
    )
    assert asked.state is LeaseState.WAITING
    asked.release()

    adopted = arb.adopt(Tenant.AITOOLKIT, "train", 18000, CARD, quiet_s=60)
    assert adopted.state is LeaseState.GRANTED
    assert adopted.kind is LeaseKind.EXCLUSIVE
    assert arb.snapshot()["devices"][0]["reserved_mb"] == 0
    assert arb.try_light("Tagging", need_mb=0) is None

    # Quiet: PixlStash has not watched it peak since the restart.
    assert adopted.shrink(12000) is False
    assert (adopted.kind, adopted.need_mb) == (LeaseKind.EXCLUSIVE, 18000)

    adopted.quiet_until = time.monotonic() - 1
    assert adopted.shrink(12000) is True
    assert (adopted.kind, adopted.need_mb) == (LeaseKind.HEAVY, 12000)
    light = arb.try_light("Tagging", need_mb=0)
    assert light is not None and light.state is LeaseState.GRANTED
    light.release()
    adopted.release()


def test_a_probe_can_name_the_memory_it_reports():
    arb, _fake, _ = make(used_mb=3000, thread=False)
    leftover = FakeComfy(usage=2000)
    leftover.holder_label = lambda: "AI Toolkit · memory not released"
    arb.register_probe(Tenant.AITOOLKIT, leftover)
    labels = {row["label"] for row in arb.snapshot()["holders"]}
    assert "AI Toolkit · memory not released" in labels
