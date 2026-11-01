"""The one place GPU memory is handed out on this machine.

PixlStash, a ComfyUI on the same host and (later) AI-toolkit do not fit in one
card together. The arbiter admits **work PixlStash starts** - its own tasks,
the ComfyUI runs it submits - only when it fits, and makes the rest wait with a
reason. Memory held by anything else simply shrinks the budget.

Leases come in three kinds:

* ``LIGHT``: one PixlStash task. Decided on the spot and never queued: refused,
  the task spills to the CPU or is retried later, so the single GPU worker
  never blocks on a lease.
* ``HEAVY``: a ComfyUI run (later a training job once its peak is known).
  Queued FIFO per device, except that a ComfyUI run that fits passes ComfyUI
  runs waiting ahead of it.
* ``EXCLUSIVE``: heavy work that must have the card to itself. While one is
  granted or waiting, LIGHT grants on its device are refused.

See ``docs/backend_architecture.md`` §7, "VRAM arbitration", for the budget
arithmetic, the reclaim ladder and the reasons a lease waits.
"""

from __future__ import annotations

import itertools
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional, Protocol

from pixlstash.pixl_logging import get_logger
from pixlstash.resources.ledger import (
    DeviceReading,
    Ledger,
    ledger,
    pixlstash_usage_mb,
    ram,
)

logger = get_logger(__name__)

#: The rollback line. False grants every request at once; the decisions are
#: still computed and logged, so the log says what enforcing would have done.
VRAM_ORCHESTRATION_ENFORCING = True

#: What is always kept free on a card: the larger of a flat 1 GB and 5 % of it.
SAFETY_MARGIN_MB = 1024
SAFETY_MARGIN_SHARE = 0.05

#: How often the arbiter re-reads the cards while anything waits or heavy work
#: holds one.
POLL_S = 2.0

#: A device-used change this large wakes the arbiter between polls.
LEDGER_WAKE_MB = 256
#: The pause between passes while the card's use keeps moving (a model
#: loading): soon, but never a spin.
MOVED_POLL_S = 0.25


class Tenant(str, Enum):
    PIXLSTASH = "pixlstash"
    COMFYUI = "comfyui"
    AITOOLKIT = "aitoolkit"


class LeaseKind(str, Enum):
    LIGHT = "light"
    HEAVY = "heavy"
    EXCLUSIVE = "exclusive"


class LeaseState(str, Enum):
    WAITING = "waiting"
    GRANTED = "granted"
    RELEASED = "released"


class TenantProbe(Protocol):
    """What the arbiter asks of a tenant other than PixlStash's own tasks.

    Every method is called **outside** the arbiter's lock, from its refresh,
    because each may be a network round trip. A probe may also define
    ``holder_label()``, the name its memory goes by in the Tasks tab when the
    tenant's own name would not say enough (None keeps that name).
    """

    def device(self) -> str | None:
        """The UUID of the card this tenant runs on; None for a remote one."""

    def usage_mb(self) -> int | None:
        """What the tenant holds on its card now, or None if unmeasurable."""

    def busy(self) -> bool:
        """Whether work PixlStash did not submit is queued on the tenant."""

    def idle(self) -> bool:
        """Whether nothing at all is queued or running on the tenant."""

    def reclaim(self) -> bool:
        """Ask the tenant to give its memory back; True if it was asked."""


@dataclass(eq=False)
class Lease:
    """A claim on one card's memory.

    ``need_mb`` is absolute for ComfyUI (what ComfyUI as a whole grows to,
    because a run's peak is measured as ComfyUI's whole allocation) and
    incremental for PixlStash (what the task adds over what is resident).
    """

    tenant: Tenant
    label: str
    kind: LeaseKind
    device: str | None
    need_mb: int = 0
    ram_mb: int = 0
    worker_type: str | None = None
    run_id: str | None = None
    job_id: str | None = None
    workflow_id: str | None = None
    owner_pid: int | None = None
    #: Unmeasured work (D2): keeps other tenants' heavy work off the card
    #: without asking anybody to free memory.
    alone: bool = False
    #: An interactive task: fit-checked only, never blocked by waiting work.
    urgent: bool = False
    #: A PixlStash task that spilled to the CPU; holds no GPU memory.
    on_cpu: bool = False
    on_state: Optional[Callable[["Lease"], None]] = None
    id: str = field(default_factory=lambda: f"lease-{uuid.uuid4().hex[:12]}")
    state: LeaseState = LeaseState.WAITING
    since: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    seq: int = 0
    run_anyway: bool = False
    peak_mb: int | None = None
    measured_by: str = "estimate"
    warnings: list[dict] = field(default_factory=list)
    waiting_reason: dict | None = None
    #: Live figures a granted run reports for the Tasks tab (AI-toolkit's step,
    #: total and loss); None until the run has reported one.
    progress: dict | None = None
    reclaimed: set = field(default_factory=set)
    announced: bool = False
    #: ``time.monotonic()`` until which an adopted lease stays as it was
    #: adopted: exclusive, never shrunk (see :meth:`ResourceArbiter.adopt`).
    quiet_until: float | None = None
    _changed: threading.Event = field(default_factory=threading.Event)
    _arbiter: Optional["ResourceArbiter"] = None

    @property
    def heavy(self) -> bool:
        return self.kind is not LeaseKind.LIGHT

    def wait(
        self,
        cancel_event: threading.Event | None = None,
        timeout: float | None = None,
    ) -> bool:
        """Block until granted, released or *cancel_event* is set.

        Returns:
            True when the lease is granted.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while self.state is LeaseState.WAITING:
            if cancel_event is not None and cancel_event.is_set():
                break
            remaining = 0.5 if deadline is None else deadline - time.monotonic()
            if remaining <= 0:
                break
            self._changed.wait(min(0.5, remaining))
            self._changed.clear()
        return self.state is LeaseState.GRANTED

    def release(self, *, only_waiting: bool = False) -> None:
        """Give the memory back. Idempotent.

        With *only_waiting*, a lease granted meanwhile is left alone: the holder
        is already training on it and releases it itself.
        """
        if self._arbiter is not None:
            self._arbiter.release(self, only_waiting=only_waiting)

    def shrink(self, need_mb: int) -> bool:
        """Lower the reservation to what the work is now known to need.

        Bookkeeping only: nothing is freed inside the work itself. AI-toolkit's
        lease shrinks this way once its real peak is known.

        Returns:
            False while an adopted lease is still quiet: nothing changed.
        """
        if self._arbiter is not None:
            return self._arbiter.shrink(self, need_mb)
        return False

    def warn(self, code: str, text: str, **payload) -> None:
        """Attach a warning to this lease and announce it."""
        if self._arbiter is not None:
            self._arbiter.warn(self, code, text, **payload)

    def set_progress(self, **fields) -> None:
        """Replace the live figures the Tasks tab shows for this lease."""
        if self._arbiter is not None:
            self._arbiter.set_progress(self, fields)


def safety_margin_mb(total_mb: int) -> int:
    """What is kept free on a card of *total_mb*."""
    return max(SAFETY_MARGIN_MB, int(total_mb * SAFETY_MARGIN_SHARE))


def _gb(mb: int | float) -> str:
    return f"{mb / 1024:.1f} GB"


class ResourceArbiter:
    """Admits leases against the ledger. One per process (``arbiter()``).

    Args:
        ledger_: Where device readings come from; a fake in tests.
        usage: PixlStash's own resident memory, MB, or None. Injectable.
        ram_reader: ``() -> {total_mb, available_mb}``. Injectable.
        enforcing: Overrides ``VRAM_ORCHESTRATION_ENFORCING``; tests only.
    """

    def __init__(
        self,
        ledger_: Ledger,
        usage: Callable[[], int | None] = pixlstash_usage_mb,
        ram_reader: Callable[[], dict] = ram,
        enforcing: bool | None = None,
    ) -> None:
        self._ledger = ledger_
        self._usage = usage
        self._ram = ram_reader
        self._enforcing_override = enforcing
        self._lock = threading.RLock()
        self._leases: dict[str, Lease] = {}
        self._seq = itertools.count(1)
        self._probes: dict[Tenant, TenantProbe] = {}
        self._probe_state: dict[Tenant, dict] = {}
        # PixlStash's yield: ``(lease) -> [worker types it stopped]``.
        self._yielders: list[Callable[[Lease], list[str]]] = []
        self._listeners: list[Callable[[dict], None]] = []
        self._pixlstash_on_gpu = True
        self._pixlstash_cap_mb: int | None = None
        self._baseline: dict[tuple[Tenant, str], int] = {}
        self._preempted: list[dict] = []
        self._devices: list[DeviceReading] = []
        self._ram_now = {"total_mb": 0, "available_mb": 0}
        self._pixlstash_usage: int | None = None
        # Each _refresh numbers its read before taking it, so a slow pass on
        # the arbiter thread cannot store its reading over a newer fresh one.
        self._reads = itertools.count(1)
        self._stored_read = 0
        self._stored_probe_read = 0
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._reclaiming = False

    # ── configuration ────────────────────────────────────────────────────

    @property
    def enforcing(self) -> bool:
        if self._enforcing_override is not None:
            return self._enforcing_override
        return VRAM_ORCHESTRATION_ENFORCING

    def register_probe(self, tenant: Tenant, probe: TenantProbe | None) -> None:
        """Attach (or with None, detach) a tenant's probe."""
        with self._lock:
            if probe is None:
                self._probes.pop(tenant, None)
                self._probe_state.pop(tenant, None)
            else:
                self._probes[tenant] = probe

    def probe(self, tenant: Tenant) -> TenantProbe | None:
        with self._lock:
            return self._probes.get(tenant)

    def add_yielder(self, yielder: Callable[[Lease], list[str]]) -> None:
        """Register how PixlStash makes room: stop its GPU work and unload."""
        with self._lock:
            self._yielders.append(yielder)

    def remove_yielder(self, yielder) -> None:
        with self._lock:
            if yielder in self._yielders:
                self._yielders.remove(yielder)

    def add_listener(self, listener: Callable[[dict], None]) -> None:
        """Register a ``resource_lease`` event sink."""
        with self._lock:
            self._listeners.append(listener)

    def remove_listener(self, listener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def set_pixlstash_on_gpu(self, on_gpu: bool) -> None:
        """False when PixlStash runs on the CPU: its tasks then take no lease."""
        self._pixlstash_on_gpu = bool(on_gpu)

    def set_pixlstash_cap_gb(self, max_vram_gb: float | None) -> None:
        """``max_vram_gb``: the most PixlStash's own leases may hold together."""
        try:
            cap = int(float(max_vram_gb) * 1024) if max_vram_gb else None
        except (TypeError, ValueError):
            logger.warning("Ignoring an unreadable VRAM limit %r", max_vram_gb)
            cap = None
        self._pixlstash_cap_mb = cap if cap and cap > 0 else None

    # ── requests ─────────────────────────────────────────────────────────

    def request(
        self,
        tenant: Tenant,
        label: str,
        need_mb: int = 0,
        kind: LeaseKind = LeaseKind.HEAVY,
        device: str | None = None,
        **fields,
    ) -> Lease:
        """Ask for heavy or exclusive memory. Granted now or queued FIFO.

        *device* None means the tenant's own card; a tenant with no card here
        (a remote ComfyUI) is granted at once and takes nothing.
        """
        if kind is LeaseKind.LIGHT:
            raise ValueError("LIGHT leases are asked for with try_light")
        self._refresh()
        if device is None:
            device = self._tenant_device(tenant)
        lease = Lease(
            tenant=tenant,
            label=label,
            kind=kind,
            device=device,
            need_mb=max(0, int(need_mb)),
            **fields,
        )
        lease._arbiter = self
        with self._lock:
            lease.seq = next(self._seq)
            self._leases[lease.id] = lease
            if device is None or not self.enforcing:
                if device is not None:
                    self._evaluate_locked(log_only=True)
                changes = self._grant_locked(
                    lease, "no local card" if device is None else "not enforcing"
                )
            else:
                changes = self._evaluate_locked()
        self._announce(changes)
        if lease.state is LeaseState.WAITING:
            self._ensure_thread()
        return lease

    def try_light(
        self,
        label: str,
        need_mb: int = 0,
        worker_type: str | None = None,
        urgent: bool = False,
        ram_mb: int = 0,
    ) -> Lease | None:
        """A PixlStash task's lease, decided now: granted, or None if refused.

        Never queued. A refused task spills to the CPU or is tried again later.
        """
        need = max(0, int(need_mb))
        lease = Lease(
            tenant=Tenant.PIXLSTASH,
            label=label,
            kind=LeaseKind.LIGHT,
            device=None,
            need_mb=need,
            ram_mb=ram_mb,
            worker_type=worker_type,
            urgent=urgent,
        )
        lease._arbiter = self
        if not self._pixlstash_on_gpu:
            lease.on_cpu = True
            return self._admit_light(lease, "PixlStash runs on the CPU")
        self._refresh(cached=True, probes=False)
        with self._lock:
            lease.device = self._tenant_device(Tenant.PIXLSTASH)
            if lease.device is None:
                lease.on_cpu = True
                return self._admit_light(lease, "no GPU")
            refusal = self._light_refusal_locked(lease)
            if refusal is not None and self.enforcing:
                logger.debug("Refused GPU memory to %s: %s", label, refusal)
                return None
            if refusal is not None:
                logger.info(
                    "Would have refused GPU memory to %s (%s); not enforcing",
                    label,
                    refusal,
                )
            return self._admit_light(lease, "fits")

    def note_on_cpu(self, label: str, worker_type: str | None = None) -> Lease:
        """Record a PixlStash task that spilled to the CPU, for the snapshot."""
        lease = Lease(
            tenant=Tenant.PIXLSTASH,
            label=label,
            kind=LeaseKind.LIGHT,
            device=None,
            worker_type=worker_type,
            on_cpu=True,
        )
        lease._arbiter = self
        return self._admit_light(lease, "on the CPU")

    def adopt(
        self,
        tenant: Tenant,
        label: str,
        need_mb: int,
        device: str | None,
        *,
        quiet_s: float,
        **fields,
    ) -> Lease:
        """Grant an exclusive lease at once to work already running on *device*.

        A trainer that outlived a restart already holds its memory, so it is
        not queued, nothing is asked to make room for it, and what its tenant
        holds now counts as its own rather than as other apps'. For *quiet_s*
        it stays exactly as adopted: exclusive, so PixlStash's own GPU work
        keeps off the card, and refusing :meth:`shrink`, because PixlStash has
        not yet watched the run reach its peak since the restart.
        """
        self._refresh()
        lease = Lease(
            tenant=tenant,
            label=label,
            kind=LeaseKind.EXCLUSIVE,
            device=device,
            need_mb=max(0, int(need_mb)),
            **fields,
        )
        lease._arbiter = self
        lease.quiet_until = time.monotonic() + max(0.0, quiet_s)
        with self._lock:
            lease.seq = next(self._seq)
            self._leases[lease.id] = lease
            changes = self._grant_locked(lease, "adopted at start-up")
            if device is not None:
                # Everything it holds was there before the grant.
                self._baseline[(tenant, device)] = 0
        self._announce(changes)
        return lease

    def _admit_light(self, lease: Lease, why: str) -> Lease:
        with self._lock:
            lease.seq = next(self._seq)
            self._leases[lease.id] = lease
            self._grant_locked(lease, why)
        return lease

    def release(self, lease: Lease, *, only_waiting: bool = False) -> bool:
        """End a lease, granted or waiting. Idempotent.

        With *only_waiting*, a lease granted meanwhile is left alone. Returns
        whether this call ended the lease.
        """
        with self._lock:
            if only_waiting and lease.state is not LeaseState.WAITING:
                return False
            if self._leases.pop(lease.id, None) is None:
                return False
            was = lease.state
            lease.state = LeaseState.RELEASED
            lease.waiting_reason = None
            if not self._granted_locked(lease.tenant, lease.device):
                self._baseline.pop((lease.tenant, lease.device), None)
            self._preempted = [
                entry for entry in self._preempted if entry["caused_by"] != lease.id
            ]
            changes = [(lease, "released")] if lease.heavy else []
            if lease.heavy:
                logger.info(
                    "Released %s lease %s (%s) after %s",
                    lease.kind.value,
                    lease.id,
                    lease.label,
                    was.value,
                )
            changes += self._evaluate_locked()
        lease._changed.set()
        self._announce(changes)
        return True

    def shrink(self, lease: Lease, need_mb: int) -> bool:
        if lease.quiet_until is not None and time.monotonic() < lease.quiet_until:
            logger.info(
                "Not shrinking %s (%s) yet: adopted at start-up and still quiet",
                lease.id,
                lease.label,
            )
            return False
        with self._lock:
            lease.need_mb = max(0, min(lease.need_mb, int(need_mb)))
            if lease.kind is LeaseKind.EXCLUSIVE:
                lease.kind = LeaseKind.HEAVY
            changes = self._evaluate_locked()
        self._announce(changes)
        return True

    def set_progress(self, lease: Lease, fields: dict) -> None:
        """Store a lease's live figures. The next snapshot carries them."""
        with self._lock:
            lease.progress = dict(fields)

    def lease(self, lease_id: str) -> Lease | None:
        with self._lock:
            return self._leases.get(lease_id)

    def run_anyway(self, lease_id: str) -> Lease | None:
        """Start one waiting lease now, whatever it needs. The rest keep order."""
        with self._lock:
            lease = self._leases.get(lease_id)
            if lease is None or lease.state is not LeaseState.WAITING:
                return lease
            lease.run_anyway = True
            reason = lease.waiting_reason or {}
            logger.warning(
                "Run anyway: granting %s (%s) needing %s MB with %s MB budget",
                lease.id,
                lease.label,
                lease.need_mb,
                reason.get("free_mb"),
            )
            changes = self._evaluate_locked()
        self._announce(changes)
        return lease

    def cancel(self, lease_id: str) -> Lease | None:
        """Withdraw a waiting lease; the caller cancels the work behind it."""
        lease = self.lease(lease_id)
        if lease is None or not self.release(lease, only_waiting=True):
            return None
        return lease

    def leases(self, tenant: Tenant | None = None) -> list[Lease]:
        with self._lock:
            return [
                lease
                for lease in self._leases.values()
                if tenant is None or lease.tenant is tenant
            ]

    def warn(self, lease: Lease | None, code: str, text: str, **payload) -> None:
        """Attach a warning to a lease (if any) and announce it."""
        if lease is not None:
            with self._lock:
                lease.warnings.append({"code": code, "text": text})
        self._emit(
            {
                **(self._payload(lease) if lease is not None else {}),
                "change": "warning",
                "code": code,
                "text": text,
                **payload,
            }
        )

    # ── snapshot ─────────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        """The ``resources`` block of ``GET /workers/progress``."""
        self._refresh(cached=True)
        with self._lock:
            devices = []
            holders = []
            pixlstash_device = self._tenant_device(Tenant.PIXLSTASH)
            for reading in self._devices:
                outstanding = self._outstanding_locked(reading.uuid)
                devices.append(
                    {
                        "uuid": reading.uuid,
                        "name": reading.name,
                        "total_mb": reading.total_mb,
                        "used_mb": reading.used_mb,
                        "free_mb": reading.free_mb,
                        "reserved_mb": sum(outstanding.values()),
                        # Whose that reservation is, so the bar can draw it
                        # in the tenant's colour rather than as spare.
                        "reserved_by": {
                            tenant.value: mb for tenant, mb in outstanding.items() if mb
                        },
                        "budget_mb": max(0, self._budget_locked(reading)),
                        "measurable": reading.measurable,
                        "pixlstash": reading.uuid == pixlstash_device,
                    }
                )
                holders.extend(self._holders_locked(reading))
            ordered = sorted(
                self._leases.values(),
                key=lambda lease: (lease.state is not LeaseState.GRANTED, lease.seq),
            )
            waiting = [lease for lease in ordered if lease.state is LeaseState.WAITING]
            heavy_running = any(
                lease.heavy and lease.state is LeaseState.GRANTED for lease in ordered
            )
            return {
                "enforcing": self.enforcing,
                "devices": devices,
                "ram": dict(self._ram_now),
                "holders": holders,
                "leases": [self._lease_row(lease) for lease in ordered],
                "preempted": [dict(entry) for entry in self._preempted],
                "summary": {
                    "state": "waiting"
                    if waiting
                    else ("running" if heavy_running else "idle"),
                    "headline": (
                        f"{len(waiting)} waiting for memory"
                        if waiting
                        else ("Running" if heavy_running else "Idle")
                    ),
                },
            }

    def training_room(self, device: str | None) -> dict:
        """What an exclusive run on *device* can be given: ``{vram_mb, ram_mb}``.

        The card less the safety margin and what other apps hold: PixlStash
        yields its own work for an exclusive lease, and ComfyUI's and a
        finished AI-toolkit run's memory are given back before it is granted,
        so neither counts against it. ``vram_mb`` is None for a card that
        cannot be measured; ``ram_mb`` is available physical RAM (swap never
        counts), None when unreadable.
        """
        self._refresh(cached=True)
        with self._lock:
            reading = next((d for d in self._devices if d.uuid == device), None)
            vram = None
            if reading is not None and reading.measurable:
                other = sum(
                    row["used_mb"]
                    for row in self._holders_locked(reading)
                    if row["tenant"] == "unattributed"
                )
                vram = max(
                    0, reading.total_mb - other - safety_margin_mb(reading.total_mb)
                )
            ram_now = dict(self._ram_now)
        return {
            "vram_mb": vram,
            "ram_mb": ram_now["available_mb"] if ram_now.get("total_mb") else None,
        }

    def _lease_row(self, lease: Lease) -> dict:
        return {
            "id": lease.id,
            "tenant": lease.tenant.value,
            "label": lease.label,
            "kind": lease.kind.value,
            "state": lease.state.value,
            "device": lease.device,
            "need_mb": lease.need_mb,
            "ram_mb": lease.ram_mb,
            "reserved_mb": lease.need_mb if lease.state is LeaseState.GRANTED else 0,
            "held_mb": self._held_mb_locked(lease),
            "peak_mb": lease.peak_mb,
            "since": lease.since.isoformat(),
            "worker_type": lease.worker_type,
            "run_id": lease.run_id,
            "job_id": lease.job_id,
            "workflow_id": lease.workflow_id,
            "progress": dict(lease.progress) if lease.progress else None,
            "on_cpu": lease.on_cpu,
            "warnings": [dict(w) for w in lease.warnings],
            "waiting_reason": dict(lease.waiting_reason)
            if lease.waiting_reason
            else None,
        }

    def _held_mb_locked(self, lease: Lease) -> int:
        if lease.state is not LeaseState.GRANTED or lease.on_cpu:
            return 0
        if lease.tenant is Tenant.COMFYUI:
            state = self._probe_state.get(Tenant.COMFYUI) or {}
            # Its reading counts only for the card this lease was granted on.
            if state.get("device") == lease.device and state.get("usage") is not None:
                return int(state["usage"])
        return lease.need_mb

    def _holders_locked(self, reading: DeviceReading) -> list[dict]:
        rows = []
        known = 0
        if reading.uuid == self._tenant_device(Tenant.PIXLSTASH):
            usage = self._pixlstash_usage
            measured = usage is not None
            if usage is None:
                usage = sum(
                    lease.need_mb
                    for lease in self._granted_locked(Tenant.PIXLSTASH, reading.uuid)
                )
            if usage:
                rows.append(
                    self._holder(
                        reading.uuid,
                        Tenant.PIXLSTASH.value,
                        "PixlStash",
                        usage,
                        "torch_reserved" if measured else "estimate",
                    )
                )
                known += usage
        for tenant, state in self._probe_state.items():
            if state.get("device") != reading.uuid or not state.get("usage"):
                continue
            rows.append(
                self._holder(
                    reading.uuid,
                    tenant.value,
                    state.get("label")
                    or ("ComfyUI" if tenant is Tenant.COMFYUI else "AI Toolkit"),
                    int(state["usage"]),
                    "torch_reserved",
                )
            )
            known += int(state["usage"])
        if reading.measurable:
            other = reading.used_mb - known
            if other > 0:
                rows.append(
                    self._holder(
                        reading.uuid, "unattributed", "Other apps", other, "nvml"
                    )
                )
        rows.sort(key=lambda row: -row["used_mb"])
        return rows

    @staticmethod
    def _holder(device, tenant, label, used_mb, measured_by) -> dict:
        return {
            "device": device,
            "tenant": tenant,
            "label": label,
            "used_mb": int(used_mb),
            "measured_by": measured_by,
            "lease_id": None,
            "outside_pixlstash": False,
        }

    # ── the admission pass ───────────────────────────────────────────────

    def _tenant_device(self, tenant: Tenant) -> str | None:
        if tenant is Tenant.PIXLSTASH:
            return self._ledger.pixlstash_device() if self._pixlstash_on_gpu else None
        state = self._probe_state.get(tenant)
        if state is not None:
            return state.get("device")
        return None

    def _refresh(self, cached: bool = False, probes: bool = True) -> None:
        """Read the cards and every tenant, outside the lock.

        *probes* False keeps the tenants' last reading: ``try_light`` runs on
        the single GPU worker, which must not wait on ComfyUI's HTTP API.
        """
        with self._lock:
            read = next(self._reads)
        devices = self._ledger.devices(max_age_s=1.0 if cached else 0.0)
        usage = self._usage()
        try:
            ram_now = self._ram()
        except Exception as exc:
            logger.debug("Could not read RAM: %s", exc)
            ram_now = {"total_mb": 0, "available_mb": 0}
        with self._lock:
            # Numbered here, not with the ledger read: a stalled ledger read
            # samples its tenants later than a read that started after it.
            probe_read = next(self._reads)
            tenant_probes = dict(self._probes) if probes else {}
            last_devices = {
                tenant: state.get("device")
                for tenant, state in self._probe_state.items()
            }
        states = {}
        for tenant, probe in tenant_probes.items():
            try:
                label = getattr(probe, "holder_label", None)
                states[tenant] = {
                    "device": probe.device(),
                    "usage": probe.usage_mb(),
                    "busy": probe.busy(),
                    "idle": probe.idle(),
                    "label": label() if callable(label) else None,
                }
            except Exception as exc:
                logger.warning(
                    "Could not read the %s tenant; it is taken as busy: %s",
                    tenant.value,
                    exc,
                )
                # Its last card, not None: None means a remote tenant, which
                # is granted unchecked. Never read yet, PixlStash's own card,
                # as ComfyUIProbe assumes. Busy keeps its leases waiting.
                states[tenant] = {
                    "device": last_devices[tenant]
                    if tenant in last_devices
                    else self._ledger.pixlstash_device(),
                    "usage": None,
                    "busy": True,
                    "idle": False,
                }
        with self._lock:
            if read > self._stored_read:
                # A cached read can hand back a sample older than a fresh read
                # that started before it, so it never outranks one.
                if not cached:
                    self._stored_read = read
                self._devices = devices
                self._pixlstash_usage = usage
                self._ram_now = ram_now
            if probes and probe_read > self._stored_probe_read:
                self._stored_probe_read = probe_read
                for tenant, state in states.items():
                    if tenant in self._probes:
                        self._probe_state[tenant] = state

    def _tenant_usage_locked(self, tenant: Tenant, device: str) -> int | None:
        if tenant is Tenant.PIXLSTASH:
            return self._pixlstash_usage
        state = self._probe_state.get(tenant) or {}
        return state.get("usage") if state.get("device") == device else None

    def _granted_locked(self, tenant: Tenant, device: str | None) -> list[Lease]:
        return [
            lease
            for lease in self._leases.values()
            if lease.state is LeaseState.GRANTED
            and lease.tenant is tenant
            and lease.device == device
            and not lease.on_cpu
        ]

    def _outstanding_locked(self, device: str) -> dict[Tenant, int]:
        """Reserved memory per tenant not yet visible as used on the card.

        ComfyUI's runs share one reservation, the largest: ComfyUI executes
        one prompt at a time and a run's need is ComfyUI's whole allocation,
        so it is what ComfyUI grows *to*. Everyone else's add up, less what
        their usage has grown since the first of them was granted. A tenant
        that cannot be measured keeps its whole reservation outstanding.
        """
        outstanding = {}
        for tenant in Tenant:
            granted = self._granted_locked(tenant, device)
            if not granted:
                continue
            usage = self._tenant_usage_locked(tenant, device)
            if tenant is Tenant.COMFYUI:
                reserved = max(lease.need_mb for lease in granted)
                grown = usage or 0
            else:
                reserved = sum(lease.need_mb for lease in granted)
                baseline = self._baseline.get((tenant, device))
                if baseline is None and usage is not None:
                    # Unreadable when the first lease was granted: growth
                    # counts from the first reading instead.
                    baseline = self._baseline[(tenant, device)] = usage
                grown = (
                    max(0, usage - baseline)
                    if usage is not None and baseline is not None
                    else 0
                )
            outstanding[tenant] = max(0, reserved - grown)
        return outstanding

    def _budget_locked(
        self, reading: DeviceReading, for_tenant: Tenant | None = None
    ) -> int:
        """What a new request of *for_tenant* could be given on *reading*."""
        outstanding = self._outstanding_locked(reading.uuid)
        budget = (
            reading.free_mb
            - sum(outstanding.values())
            - safety_margin_mb(reading.total_mb)
        )
        if for_tenant is Tenant.COMFYUI:
            # ComfyUI evicts its own models to make room for its next run, so
            # what it already holds is room for its own lease. Without this,
            # back-to-back runs of one workflow wait for ever behind their own
            # cache. Its reservation is absolute (what it grows to), so its own
            # outstanding comes back out too.
            budget += (
                self._tenant_usage_locked(Tenant.COMFYUI, reading.uuid) or 0
            ) + outstanding.get(Tenant.COMFYUI, 0)
        return budget

    def _light_refusal_locked(self, lease: Lease) -> str | None:
        reading = next((d for d in self._devices if d.uuid == lease.device), None)
        if reading is None or not reading.measurable:
            # Today's behaviour where the card cannot be measured.
            return None
        if not lease.urgent:
            for other in self._leases.values():
                if other.device != lease.device or not other.heavy:
                    continue
                if other.kind is LeaseKind.EXCLUSIVE and other.state in (
                    LeaseState.GRANTED,
                    LeaseState.WAITING,
                ):
                    return f"{other.label} needs the GPU to itself"
                if other.state is LeaseState.WAITING and (
                    other.waiting_reason or {}
                ).get("code") in ("vram", "ram", "exclusive_held"):
                    return f"{other.label} is waiting for memory"
        if self._pixlstash_cap_mb is not None:
            held = sum(
                other.need_mb
                for other in self._granted_locked(Tenant.PIXLSTASH, lease.device)
            )
            if held + lease.need_mb > self._pixlstash_cap_mb:
                return (
                    f"PixlStash's VRAM limit is {_gb(self._pixlstash_cap_mb)} "
                    f"and its tasks hold {_gb(held)}"
                )
        if lease.need_mb and lease.need_mb > self._budget_locked(reading):
            return f"needs {lease.need_mb} MB, {self._budget_locked(reading)} MB free"
        if (
            lease.ram_mb
            and self._ram_now.get("total_mb")
            and lease.ram_mb > self._ram_now.get("available_mb", 0)
        ):
            return f"needs {lease.ram_mb} MB of RAM"
        return None

    def _grant_locked(self, lease: Lease, why: str) -> list[tuple[Lease, str]]:
        if lease.device is not None and not self._granted_locked(
            lease.tenant, lease.device
        ):
            usage = self._tenant_usage_locked(lease.tenant, lease.device)
            if usage is not None:
                self._baseline[(lease.tenant, lease.device)] = usage
        lease.state = LeaseState.GRANTED
        lease.waiting_reason = None
        lease._changed.set()
        if lease.heavy:
            logger.info(
                "Granted %s lease %s (%s) on %s, need %s MB: %s",
                lease.kind.value,
                lease.id,
                lease.label,
                lease.device,
                lease.need_mb,
                why,
            )
            # The PixlStash work stopped to make room may start again: the
            # budget now carries this lease's reservation.
            self._preempted = [
                entry for entry in self._preempted if entry["caused_by"] != lease.id
            ]
            return [(lease, "granted")]
        logger.debug(
            "Granted GPU memory to %s (%s MB): %s", lease.label, lease.need_mb, why
        )
        return []

    def _evaluate_locked(self, log_only: bool = False) -> list[tuple[Lease, str]]:
        """One admission pass, one grant at a time, FIFO per device (a ComfyUI
        run may pass ComfyUI runs waiting ahead of it).

        Returns the state changes to announce once the lock is let go.
        """
        changes: list[tuple[Lease, str]] = []
        while True:
            granted = self._grant_one_locked(log_only)
            if granted is None:
                break
            changes.append((granted, "granted"))
            if log_only:
                break
        return changes

    def _grant_one_locked(self, log_only: bool) -> Lease | None:
        waiting = sorted(
            (
                lease
                for lease in self._leases.values()
                if lease.state is LeaseState.WAITING
            ),
            key=lambda lease: lease.seq,
        )
        blocked: dict[str, list[Lease]] = {}
        positions: dict[str, int] = {}
        for lease in waiting:
            positions[lease.device] = positions.get(lease.device, 0) + 1
            # A ComfyUI run passes ComfyUI runs that wait ahead of it: what
            # ComfyUI holds counts as room for its own runs, so granting it
            # takes nothing from them. Anyone else's work keeps its place.
            ahead = next(
                (
                    other
                    for other in blocked.get(lease.device, ())
                    if not (lease.tenant is other.tenant is Tenant.COMFYUI)
                ),
                None,
            )
            reason = self._fit_locked(lease, ahead)
            if reason is None or lease.run_anyway:
                if log_only:
                    return None
                self._grant_locked(
                    lease, "run anyway" if reason is not None else "fits"
                )
                return lease
            reason["position"] = positions[lease.device]
            if lease.waiting_reason is None or (
                lease.waiting_reason.get("code") != reason["code"]
            ):
                logger.info(
                    "%s lease %s (%s) waits: %s",
                    lease.kind.value.capitalize(),
                    lease.id,
                    lease.label,
                    reason["text"],
                )
            lease.waiting_reason = reason
            blocked.setdefault(lease.device, []).append(lease)
        return None

    def _fit_locked(self, lease: Lease, ahead: Lease | None) -> dict | None:
        """None when *lease* can be granted now, else why it waits."""
        reading = next((d for d in self._devices if d.uuid == lease.device), None)
        state = self._probe_state.get(lease.tenant) or {}
        others = [
            other
            for other in self._leases.values()
            if other is not lease
            and other.device == lease.device
            and other.heavy
            and other.state is LeaseState.GRANTED
        ]
        foreign = [other for other in others if other.tenant is not lease.tenant]
        if lease.tenant is not Tenant.PIXLSTASH and state.get("busy"):
            return self._reason(
                lease,
                f"{lease.tenant.value}_busy",
                f"{'ComfyUI' if lease.tenant is Tenant.COMFYUI else 'AI Toolkit'} "
                "is busy with work started outside PixlStash.",
                reading,
            )
        exclusive = lease.kind is LeaseKind.EXCLUSIVE or (
            reading is not None and not reading.measurable
        )
        if (exclusive or lease.alone) and foreign:
            return self._reason(
                lease,
                "exclusive_held"
                if reading is None or reading.measurable
                else "unmeasurable",
                f"Needs the GPU to itself; {foreign[0].label} is using it.",
                reading,
                blocked_by=foreign,
            )
        if any(other.kind is LeaseKind.EXCLUSIVE or other.alone for other in foreign):
            holder = next(
                other
                for other in foreign
                if other.kind is LeaseKind.EXCLUSIVE or other.alone
            )
            return self._reason(
                lease,
                "exclusive_held",
                f"{holder.label} has the GPU to itself.",
                reading,
                blocked_by=[holder],
            )
        if ahead is not None:
            return self._reason(
                lease,
                "fifo",
                f"Waiting behind {ahead.label}.",
                reading,
                blocked_by=[ahead],
            )
        if (
            lease.kind is LeaseKind.EXCLUSIVE
            and "pixlstash" not in lease.reclaimed
            and self._yielders
            and lease.device == self._tenant_device(Tenant.PIXLSTASH)
        ):
            # Q27: an exclusive run has PixlStash move its own work off the
            # card first. "vram" is what sends the reclaim ladder to it.
            return self._reason(
                lease,
                "vram",
                "Making room: PixlStash is moving its own work off the GPU.",
                reading,
            )
        if reading is None or not reading.measurable:
            return None
        budget = self._budget_locked(reading, lease.tenant)
        if lease.need_mb > budget:
            return self._reason(lease, "vram", None, reading, budget=budget)
        if (
            lease.ram_mb
            and self._ram_now.get("total_mb")
            and lease.ram_mb > self._ram_now.get("available_mb", 0)
        ):
            return self._reason(
                lease,
                "ram",
                f"Needs {_gb(lease.ram_mb)} of RAM; "
                f"{_gb(self._ram_now['available_mb'])} is free.",
                reading,
            )
        return None

    def _reason(
        self,
        lease: Lease,
        code: str,
        text: str | None,
        reading: DeviceReading | None,
        budget: int | None = None,
        blocked_by: list[Lease] | None = None,
    ) -> dict:
        free = (
            budget
            if budget is not None
            else (self._budget_locked(reading, lease.tenant) if reading else 0)
        )
        free = max(0, free)
        holders = (
            [
                h
                for h in self._holders_locked(reading)
                if h["tenant"] != lease.tenant.value
            ]
            if reading is not None
            else []
        )
        if text is None:
            held = "; ".join(f"{h['label']} holds {_gb(h['used_mb'])}" for h in holders)
            text = f"Needs {_gb(lease.need_mb)}; {_gb(free)} is free." + (
                f" {held}." if held else ""
            )
        actions = ["run_anyway", "cancel"]
        comfy = self._probe_state.get(Tenant.COMFYUI) or {}
        if (
            lease.tenant is not Tenant.COMFYUI
            and code == "vram"
            and comfy.get("idle")
            and comfy.get("usage")
            and comfy.get("device") == lease.device
        ):
            actions.append("free_comfyui")
        return {
            "code": code,
            "text": text,
            "need_mb": lease.need_mb,
            "free_mb": free,
            "short_by_mb": max(0, lease.need_mb - free),
            "position": 1,
            "blocked_by": holders
            if blocked_by is None
            else [
                {
                    "device": other.device,
                    "tenant": other.tenant.value,
                    "label": other.label,
                    "used_mb": self._held_mb_locked(other),
                    "measured_by": other.measured_by,
                    "lease_id": other.id,
                    "outside_pixlstash": False,
                }
                for other in blocked_by
            ],
            "actions": actions,
        }

    # ── reclaim and the poll thread ──────────────────────────────────────

    def _reclaim_step(self) -> bool:
        """Run the next rung of the reclaim ladder for the first short lease.

        1. PixlStash yields: stops its GPU work and unloads its models.
        2. ComfyUI frees, only if nothing at all is queued on it and the lease
           asking is not ComfyUI's own (its cache already counts for it).
        3. Otherwise the lease waits.

        Returns True when a rung ran, so the caller re-reads the cards.
        """
        with self._lock:
            short = next(
                (
                    lease
                    for lease in sorted(self._leases.values(), key=lambda le: le.seq)
                    if lease.state is LeaseState.WAITING
                    and (lease.waiting_reason or {}).get("code")
                    in ("vram", "exclusive_held")
                ),
                None,
            )
            if short is None:
                return False
            yielders = list(self._yielders)
            pixlstash_here = short.device == self._tenant_device(Tenant.PIXLSTASH)
            comfy = self._probe_state.get(Tenant.COMFYUI) or {}
            comfy_probe = self._probes.get(Tenant.COMFYUI)
            short_of_memory = (short.waiting_reason or {}).get("code") == "vram"
        if (
            "pixlstash" not in short.reclaimed
            and pixlstash_here
            and yielders
            and short_of_memory
            and short.tenant is not Tenant.PIXLSTASH
        ):
            short.reclaimed.add("pixlstash")
            stopped: list[str] = []
            for yielder in yielders:
                try:
                    stopped += yielder(short) or []
                except Exception as exc:
                    logger.error(
                        "PixlStash could not make room for %s (%s): %s",
                        short.id,
                        short.label,
                        exc,
                        exc_info=True,
                    )
            now = datetime.now(timezone.utc).isoformat()
            with self._lock:
                if short.state is LeaseState.WAITING:
                    for worker_type in sorted(set(stopped)):
                        self._preempted.append(
                            {
                                "worker_type": worker_type,
                                "caused_by": short.id,
                                "since": now,
                                "text": f"Paused: making room for {short.label}",
                            }
                        )
            for worker_type in sorted(set(stopped)):
                logger.warning(
                    "Paused %s to make room for %s (%s)",
                    worker_type,
                    short.id,
                    short.label,
                )
                self._emit(
                    {
                        **self._payload(short),
                        "change": "preempted",
                        "worker_type": worker_type,
                        "text": f"Paused: making room for {short.label}",
                    }
                )
            return True
        if (
            "comfyui" not in short.reclaimed
            and short.tenant is not Tenant.COMFYUI
            and comfy_probe is not None
            and comfy.get("idle")
            and comfy.get("usage")
            and comfy.get("device") == short.device
        ):
            short.reclaimed.add("comfyui")
            try:
                comfy_probe.reclaim()
            except Exception as exc:
                logger.error(
                    "ComfyUI could not be asked to free memory for %s: %s",
                    short.id,
                    exc,
                    exc_info=True,
                )
            return True
        return False

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._wake.set()
                return
            self._thread = threading.Thread(
                target=self._run, name="vram-arbiter", daemon=True
            )
            self._thread.start()

    def kick(self) -> None:
        """Re-evaluate soon: something a waiting lease waits on changed."""
        self._wake.set()
        if self.leases():
            self._ensure_thread()

    def _run(self) -> None:
        last_used: dict[str, int] = {}
        while True:
            # Cleared before the pass, not after the wait: a kick that lands
            # during the pass is then seen by the next wait.
            self._wake.clear()
            try:
                self._refresh()
                with self._lock:
                    changes = self._evaluate_locked()
                self._announce(changes)
                if self._reclaim_step():
                    self._refresh()
                    with self._lock:
                        changes = self._evaluate_locked()
                    self._announce(changes)
            except Exception as exc:
                # The one thread every waiting lease is granted by: a failed
                # pass is logged and tried again, never the end of it.
                logger.error(
                    "The VRAM arbiter's pass failed; trying again in %ss: %s",
                    POLL_S,
                    exc,
                    exc_info=True,
                )
                self._wake.wait(POLL_S)
                continue
            with self._lock:
                live = [
                    lease
                    for lease in self._leases.values()
                    if lease.state is LeaseState.WAITING
                    or (lease.heavy and lease.state is LeaseState.GRANTED)
                ]
                if not live:
                    self._thread = None
                    return
                devices = {d.uuid: d.used_mb for d in self._devices}
            moved = any(
                abs(used - last_used.get(uuid_, used)) > LEDGER_WAKE_MB
                for uuid_, used in devices.items()
            )
            last_used = devices
            self._wake.wait(MOVED_POLL_S if moved else POLL_S)

    # ── events ───────────────────────────────────────────────────────────

    @staticmethod
    def _payload(lease: Lease) -> dict:
        return {
            "lease_id": lease.id,
            "tenant": lease.tenant.value,
            "label": lease.label,
            "kind": lease.kind.value,
            "state": lease.state.value,
            "worker_type": lease.worker_type,
            "run_id": lease.run_id,
            "workflow_id": lease.workflow_id,
        }

    def _announce(self, changes: list[tuple[Lease, str]]) -> None:
        for lease, change in changes:
            lease._changed.set()
            if lease.on_state is not None:
                try:
                    lease.on_state(lease)
                except Exception as exc:
                    logger.warning(
                        "Lease %s state callback failed: %s",
                        lease.id,
                        exc,
                        exc_info=True,
                    )
            if lease.heavy:
                self._emit({**self._payload(lease), "change": change})
        # A lease that started waiting is a change too.
        with self._lock:
            new_waiters = [
                lease
                for lease in self._leases.values()
                if lease.state is LeaseState.WAITING and not lease.announced
            ]
            for lease in new_waiters:
                lease.announced = True
        for lease in new_waiters:
            self._emit({**self._payload(lease), "change": "waiting"})

    def _emit(self, payload: dict) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(payload)
            except Exception as exc:
                logger.warning(
                    "A resource_lease listener failed: %s", exc, exc_info=True
                )


_arbiter: ResourceArbiter | None = None
_arbiter_lock = threading.Lock()


def arbiter() -> ResourceArbiter:
    """The process's one arbiter. It outlives library switches."""
    global _arbiter
    with _arbiter_lock:
        if _arbiter is None:
            _arbiter = ResourceArbiter(ledger())
        return _arbiter
