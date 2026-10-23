"""ComfyUI as a tenant of the VRAM arbiter.

What the arbiter needs from ComfyUI, read over its HTTP API so it works on
every platform and inside Docker:

* **which card it is on**: a ComfyUI is on this machine when a card NVML sees
  has its name and size, *and* ComfyUI's free memory tracks that card's within
  ``SAME_HOST_FREE_TOLERANCE_MB`` on two reads a moment apart. The address
  alone cannot say: PixlStash in Docker reaches a host ComfyUI through
  ``host.docker.internal``, and a remote box can have the same card. A remote
  ComfyUI has no card here, and its runs are granted at once;
* **what it holds**: ``/system_stats`` ``devices[].torch_vram_total``,
  ComfyUI's reserved allocator bytes, cache included, or, for a ComfyUI on
  this machine, its process's memory as NVML reports it when that is more.
  ComfyUI's dynamic VRAM (``comfy-aimdo``) keeps model weights outside
  torch's allocator, so ``torch_vram_total`` alone reads a fraction of a GB
  while ComfyUI holds most of the card. The process is the one NVML lists on
  ComfyUI's card that listens on ComfyUI's port;
* **whether it is ours to fill**: ``/queue`` holding nothing PixlStash did not
  submit (``busy``), or nothing at all (``idle``);
* **freeing**: ``POST /free``, only on hand-off to a tenant that is not
  ComfyUI (the arbiter's reclaim ladder) or when the owner asks.
"""

from __future__ import annotations

import threading
import time
from urllib.parse import urlparse

import psutil
import requests

from pixlstash.pixl_logging import get_logger
from pixlstash.resources.arbiter import Tenant, arbiter
from pixlstash.resources.ledger import UNMEASURED_DEVICE, ledger

logger = get_logger(__name__)

#: How old a ``/system_stats`` or ``/queue`` read may be.
STATS_TTL_S = 1.0
#: How long a failed read stands: a hung ComfyUI costs one timeout per this,
#: not one per progress poll. An explicit fresh read (``max_age_s=0``) still asks.
FAILED_READ_TTL_S = 10.0
#: How long a same-host decision stands before it is checked again.
MAPPING_TTL_S = 60.0
#: How closely ComfyUI's free memory must track the NVML card's.
SAME_HOST_FREE_TOLERANCE_MB = 512
#: How closely ComfyUI's card size must match the NVML card's. ComfyUI reports
#: CUDA's total, which leaves out what the driver keeps back: 439 MB less than
#: NVML's on a 32 GB card. Cards of one name differ by whole GBs.
SAME_CARD_TOTAL_TOLERANCE_MB = 1024
#: How long ``free`` waits for ComfyUI's memory to settle: ``unload_all_models``
#: moves weights to RAM first, so the figure falls over seconds, not at once.
FREE_SETTLE_TIMEOUT_S = 30.0
#: Two reads this close together count as settled.
FREE_SETTLE_MB = 256
#: How often a run's peak is sampled.
PEAK_SAMPLE_S = 0.5

_MB = 1024 * 1024
# What ``_map_device`` answers when a read failed partway: unknown, not remote.
_UNREAD = object()
_NO_PROXY = {"http": None, "https": None, "all": None}


class ComfyUIProbe:
    """One ComfyUI address, as the arbiter's ``TenantProbe``.

    Args:
        client_id: The client id PixlStash submits under; a queue entry with
            any other is somebody else's.
    """

    def __init__(self, client_id: str) -> None:
        self._client_id = client_id
        self._lock = threading.Lock()
        self._base_url: str | None = None
        self._stats: tuple[float, dict | None] = (0.0, None)
        self._queue: tuple[float, dict | None] = (0.0, None)
        self._mapping: tuple[float, str | None] = (0.0, None)
        self._pid: int | None = None
        # The card's pids last searched for ComfyUI's, so a search that found
        # nothing is not repeated on every poll until they change.
        self._searched: frozenset[int] = frozenset()

    @property
    def base_url(self) -> str | None:
        return self._base_url

    def watch(self, base_url: str) -> None:
        """Point the probe at the ComfyUI PixlStash is about to submit to."""
        with self._lock:
            if base_url != self._base_url:
                self._base_url = base_url
                self._stats = (0.0, None)
                self._queue = (0.0, None)
                self._mapping = (0.0, None)
                self._pid = None
                self._searched = frozenset()

    # ── reads ────────────────────────────────────────────────────────────

    def _get(self, path: str):
        response = requests.get(f"{self._base_url}{path}", timeout=5, proxies=_NO_PROXY)
        response.raise_for_status()
        return response.json()

    def stats(self, max_age_s: float = STATS_TTL_S) -> dict | None:
        """ComfyUI's ``/system_stats``, or None when it cannot be read."""
        if self._base_url is None:
            return None
        read_at, value = self._stats
        # A failed read is cached too: a hung ComfyUI must cost one timeout
        # per TTL, not one per caller.
        if read_at and time.monotonic() - read_at < _ttl(value, max_age_s):
            return value
        try:
            value = self._get("/system_stats")
        except (requests.RequestException, ValueError) as exc:
            logger.debug(
                "ComfyUI's stats at %s cannot be read: %s", self._base_url, exc
            )
            value = None
        self._stats = (time.monotonic(), value)
        return value

    def _queue_now(self) -> dict | None:
        if self._base_url is None:
            return None
        read_at, value = self._queue
        if read_at and time.monotonic() - read_at < _ttl(value, STATS_TTL_S):
            return value
        try:
            value = self._get("/queue")
        except (requests.RequestException, ValueError) as exc:
            logger.debug(
                "ComfyUI's queue at %s cannot be read: %s", self._base_url, exc
            )
            value = None
        self._queue = (time.monotonic(), value)
        return value

    def _entries(self) -> list | None:
        queue = self._queue_now()
        if not isinstance(queue, dict):
            return None
        return [
            item
            for key in ("queue_running", "queue_pending")
            for item in queue.get(key) or []
            if isinstance(item, (list, tuple))
        ]

    def _device_row(self) -> dict | None:
        stats = self.stats()
        devices = (stats or {}).get("devices") or []
        return devices[0] if devices and isinstance(devices[0], dict) else None

    # ── TenantProbe ──────────────────────────────────────────────────────

    def device(self) -> str | None:
        """The UUID of the card this ComfyUI runs on, or None for a remote one."""
        if self._base_url is None:
            return None
        checked_at, mapped = self._mapping
        if checked_at and time.monotonic() - checked_at < MAPPING_TTL_S:
            return mapped
        if self.stats() is None:
            # Unreachable is not remote: keep the last answer and ask again,
            # rather than admit this ComfyUI's runs unchecked for a minute.
            return self._unread_answer(checked_at, mapped)
        found = self._map_device()
        if found is _UNREAD:
            return self._unread_answer(checked_at, mapped)
        self._mapping = (time.monotonic(), found)
        return found

    @staticmethod
    def _unread_answer(checked_at: float, mapped: str | None) -> str | None:
        """The card to assume when ComfyUI cannot be read: the last one found,
        or, never found yet, PixlStash's own. None would read as remote, whose
        runs are granted unchecked; sharing PixlStash's card keeps them
        fit-checked until a read says otherwise."""
        return mapped if checked_at else ledger().pixlstash_device()

    def _map_device(self) -> str | None | object:
        first = self._device_row()
        if first is None:
            return _UNREAD
        cards = ledger().devices(max_age_s=0.0)
        if cards and not cards[0].measurable:
            # No NVML to compare with (Metal, ROCm). A ComfyUI on a loopback
            # address shares this machine's one unmeasurable device.
            host = urlparse(self._base_url or "").hostname or ""
            if host in ("localhost", "127.0.0.1", "::1"):
                return UNMEASURED_DEVICE
            return None
        name = str(first.get("name") or "")
        total = int(first.get("vram_total") or 0) // _MB
        candidates = [
            card
            for card in cards
            if card.name
            and card.name in name
            and abs(card.total_mb - total) <= SAME_CARD_TOTAL_TOLERANCE_MB
        ]
        if not candidates:
            return None
        # Two reads a moment apart: an identical card on another machine has
        # the same name and size, but not the same free memory twice over.
        for attempt in range(2):
            row = first if attempt == 0 else self._device_row()
            if row is None:
                return _UNREAD
            free = int(row.get("vram_free") or 0) // _MB
            readings = {card.uuid: card for card in ledger().devices(max_age_s=0.0)}
            candidates = [
                card
                for card in candidates
                if card.uuid in readings
                and abs(readings[card.uuid].free_mb - free)
                <= SAME_HOST_FREE_TOLERANCE_MB
            ]
            if not candidates:
                return None
            if attempt == 0:
                time.sleep(0.3)
                self._stats = (0.0, None)
        logger.info(
            "ComfyUI at %s runs on this machine's %s (%s)",
            self._base_url,
            candidates[0].name,
            candidates[0].uuid,
        )
        return candidates[0].uuid

    def usage_mb(self) -> int | None:
        """What ComfyUI holds, MB; None when it cannot be read."""
        row = self._device_row()
        if row is None:
            return None
        held = self._process_mb()
        if row.get("torch_vram_total") is None:
            return held
        allocator = int(row.get("torch_vram_total") or 0) // _MB
        return max(allocator, held or 0)

    def _process_mb(self) -> int | None:
        """ComfyUI's process memory on its card per NVML, or None unknown.

        Only for a ComfyUI already mapped to a card here. None where NVML
        cannot attribute memory (Windows WDDM, Docker) or the process is not
        found, and the allocator's figure stands alone.
        """
        card = self._mapping[1]
        if not card or card == UNMEASURED_DEVICE:
            return None
        processes = ledger().processes(card)
        if self._pid not in processes and frozenset(processes) != self._searched:
            self._searched = frozenset(processes)
            self._pid = self._listening_pid(processes)
            if self._pid is None and processes:
                logger.warning(
                    "No process on ComfyUI's card listens on %s, so its memory "
                    "is read from its allocator alone and may show as other "
                    "apps'",
                    self._base_url,
                )
        return processes.get(self._pid)

    def _listening_pid(self, pids) -> int | None:
        """Which of *pids* listens on ComfyUI's port, or None."""
        url = urlparse(self._base_url or "")
        try:
            port = url.port or (443 if url.scheme == "https" else 80)
        except ValueError as exc:
            logger.debug("ComfyUI's address %s has no port: %s", self._base_url, exc)
            return None
        for pid in pids:
            try:
                connections = psutil.Process(pid).net_connections(kind="inet")
            except psutil.Error as exc:
                logger.debug("Could not read process %s's sockets: %s", pid, exc)
                continue
            if any(
                # An empty tuple for a socket with no local address yet.
                conn.status == psutil.CONN_LISTEN
                and conn.laddr
                and conn.laddr.port == port
                for conn in connections
            ):
                return pid
        return None

    def busy(self) -> bool:
        """Work PixlStash did not submit is queued or running on ComfyUI.

        An unreadable queue is not busy. This only picks a waiting lease's
        reason, "busy with work started outside PixlStash", which would be
        false for an unreachable ComfyUI. Memory is guarded elsewhere: every
        lease is admitted against the card's free memory, which counts what
        ComfyUI holds, and reclaim asks ``idle``, which is False here.
        """
        entries = self._entries()
        if entries is None:
            return False
        return any(
            not (
                len(item) > 3
                and isinstance(item[3], dict)
                and item[3].get("client_id") == self._client_id
            )
            for item in entries
        )

    def idle(self) -> bool:
        """Nothing at all is queued or running on ComfyUI."""
        entries = self._entries()
        return entries is not None and not entries

    def reclaim(self) -> bool:
        return self.free() is not None

    def free(self) -> int | None:
        """Ask an idle ComfyUI to unload its models; MB freed, or None if refused.

        Waits until two reads of its memory agree, at most
        ``FREE_SETTLE_TIMEOUT_S``: ``/free`` is applied by ComfyUI's idle prompt
        worker, not by the request, and moves weights to RAM first.
        """
        if self._base_url is None or not self.idle():
            return None
        before = self.usage_mb() or 0
        try:
            response = requests.post(
                f"{self._base_url}/free",
                json={"unload_models": True, "free_memory": True},
                timeout=10,
                proxies=_NO_PROXY,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning(
                "ComfyUI at %s could not be asked to free memory: %s",
                self._base_url,
                exc,
            )
            return None
        deadline = time.monotonic() + FREE_SETTLE_TIMEOUT_S
        last = before
        while time.monotonic() < deadline:
            time.sleep(1.0)
            now = self.stats(max_age_s=0.0)
            current = self.usage_mb()
            if now is None or current is None:
                break
            if abs(current - last) <= FREE_SETTLE_MB and current < before:
                last = current
                break
            last = current
        logger.info(
            "Asked ComfyUI at %s to free memory: %s MB -> %s MB",
            self._base_url,
            before,
            last,
        )
        return max(0, before - last)


def _ttl(value, max_age_s: float) -> float:
    """How long a read stands: longer for a failed one, unless asked fresh."""
    return (
        max(max_age_s, FAILED_READ_TTL_S) if value is None and max_age_s else max_age_s
    )


class PeakSampler:
    """Samples ComfyUI's allocation while one run executes; ``peak_mb`` is the max.

    Runs from the moment ComfyUI starts the run until ``stop``. A run's peak
    is ComfyUI's whole allocation, cache included, which is what its next run
    will grow to again.
    """

    def __init__(self, probe: ComfyUIProbe, started: callable) -> None:
        self._probe = probe
        self._started = started
        self._stop = threading.Event()
        self.peak_mb: int | None = None
        self._thread = threading.Thread(
            target=self._run, name="comfyui-peak", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(PEAK_SAMPLE_S):
            if not self._started():
                continue
            self._probe.stats(max_age_s=PEAK_SAMPLE_S / 2)
            usage = self._probe.usage_mb()
            if usage is not None:
                self.peak_mb = max(self.peak_mb or 0, usage)

    def stop(self) -> int | None:
        self._stop.set()
        self._thread.join(timeout=2)
        return self.peak_mb


_probe: ComfyUIProbe | None = None
_probe_lock = threading.Lock()


def probe(client_id: str, base_url: str | None = None) -> ComfyUIProbe:
    """The one ComfyUI probe, registered with the arbiter; pointed at *base_url*."""
    global _probe
    with _probe_lock:
        if _probe is None:
            _probe = ComfyUIProbe(client_id)
            arbiter().register_probe(Tenant.COMFYUI, _probe)
    if base_url:
        _probe.watch(base_url)
    return _probe
