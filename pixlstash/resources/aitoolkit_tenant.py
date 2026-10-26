"""The arbiter's tenant probe for a local ai-toolkit trainer (#2120).

The training worker tells it which processes are the trainer while a run is
watched, and which of them still held GPU memory after the trainer exited. The
arbiter reads it like ComfyUI's probe: what the trainer holds counts as AI
Toolkit's, not as other apps', so a lease shrunk to the run's measured peak is
not counted twice, and memory a dead trainer left behind is named for what it
is.
"""

from __future__ import annotations

import threading

from pixlstash.resources.ledger import ledger
from pixlstash.training import measurements


class AiToolkitProbe:
    """What the local ai-toolkit trainer holds on PixlStash's card."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: set[int] = set()
        self._leftover: set[int] = set()

    def track(self, pids: set[int]) -> None:
        """The trainer's processes now (the worker calls this on every poll)."""
        with self._lock:
            self._running = set(pids)

    def ended(self, leftover: set[int]) -> None:
        """The trainer exited; *leftover* still held memory when it was checked."""
        with self._lock:
            self._running = set()
            self._leftover = set(leftover)

    def device(self) -> str | None:
        return ledger().pixlstash_device()

    def usage_mb(self) -> int | None:
        """What the trainer, or what it left behind, holds; None without NVML."""
        if ledger().nvml() is None:
            return None
        with self._lock:
            pids = self._running | self._leftover
        if not pids:
            return 0
        held = measurements.gpu_mb(pids, self.device())
        if not held:
            with self._lock:
                # Gone from the card: nothing is left over any more.
                self._leftover = set()
        return held or 0

    def holder_label(self) -> str | None:
        with self._lock:
            stuck = bool(self._leftover) and not self._running
        return "AI Toolkit · memory not released" if stuck else None

    def busy(self) -> bool:
        return False

    def idle(self) -> bool:
        with self._lock:
            return not self._running

    def reclaim(self) -> bool:
        return False


__all__ = ["AiToolkitProbe"]
