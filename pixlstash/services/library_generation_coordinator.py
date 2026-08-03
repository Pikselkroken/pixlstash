"""Admission and writer fencing for the process-wide active vault generation."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from pixlstash.services.library_switch_service import SwitchState


@dataclass(frozen=True)
class LibraryReadLease:
    generation: int
    library_uuid: str
    vault: object
    db: object


class LibraryGenerationCoordinator:
    def __init__(self, server):
        self.server = server
        self._condition = threading.Condition()
        self.state = SwitchState.READY
        self.generation = 0
        self._readers: dict[int, int] = {}

    def _coherent(self) -> bool:
        library = self.server.library_registry.active_library()
        vault = getattr(self.server, "vault", None)
        auth = getattr(self.server, "auth", None)
        db = getattr(vault, "db", None)
        return bool(
            library is not None
            and vault is not None
            and getattr(vault, "is_open", False)
            and db is not None
            and getattr(db, "is_open", False)
            and auth is not None
            and auth.vault_db is db
            and vault.image_root == library.path
        )

    def acquire_read(self) -> LibraryReadLease | None:
        with self._condition:
            if self.state is not SwitchState.READY:
                return None
            if not self._coherent():
                return None
            library = self.server.library_registry.active_library()
            vault = self.server.vault
            auth = getattr(self.server, "auth", None)
            db = getattr(vault, "db", None)
            if library is None or vault is None or db is None or auth is None:
                return None
            if auth.vault_db is not db:
                return None
            lease = LibraryReadLease(self.generation, library.uuid, vault, db)
            self._readers[self.generation] = self._readers.get(self.generation, 0) + 1
            return lease

    def release_read(self, lease: LibraryReadLease) -> None:
        with self._condition:
            remaining = self._readers.get(lease.generation, 0) - 1
            if remaining > 0:
                self._readers[lease.generation] = remaining
            else:
                self._readers.pop(lease.generation, None)
            self._condition.notify_all()

    def begin_switch(self, timeout: float = 30.0) -> None:
        with self._condition:
            if self.state is not SwitchState.READY:
                raise RuntimeError("Library admission is not ready")
            self.state = SwitchState.SWITCHING
            deadline = time.monotonic() + timeout
            while self._readers.get(self.generation, 0):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if self._coherent():
                        self.state = SwitchState.READY
                    else:
                        self.state = SwitchState.UNAVAILABLE
                    self._condition.notify_all()
                    raise RuntimeError("Timed out waiting for active-library readers")
                self._condition.wait(timeout=remaining)

    def publish_ready(self) -> None:
        with self._condition:
            if not self._coherent():
                self.state = SwitchState.UNAVAILABLE
                raise RuntimeError("Refusing READY for an incoherent library runtime")
            self.generation += 1
            self.state = SwitchState.READY
            self._condition.notify_all()

    def restore_ready(self) -> None:
        with self._condition:
            if not self._coherent():
                self.state = SwitchState.UNAVAILABLE
                raise RuntimeError("Previous library runtime is not coherent")
            self.state = SwitchState.READY
            self._condition.notify_all()

    def mark_unavailable(self) -> None:
        with self._condition:
            self.state = SwitchState.UNAVAILABLE
            self._condition.notify_all()
