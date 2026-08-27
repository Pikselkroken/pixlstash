"""In-memory registry of pictures whose image file cannot be decoded.

GitHub issue #585 ("Invalid Images break tasks"): the background pipeline is
data-driven - every ``Missing*Finder`` re-selects any picture whose target
column is still unset. When a picture points at a corrupt/undecodable file the
task can never produce a value, nothing durably marks the row as done (the
image-embedding task leaves the embedding NULL on failure, which ``fetch_work``
treats as missing), so the same picture is picked up on every sweep forever.

The reporter explicitly accepts that such a file may be "ignored for the
remaining duration of the server process lifetime, or until it is modified". This
registry implements exactly that: a process-lifetime, thread-safe set of picture
ids that failed to decode, pinned to the file's ``(mtime, size)`` at the moment of
failure. A finder consults :meth:`is_suppressed` to skip the picture; when the
file is rewritten (the "still being written" / repaired case) its signature moves
and suppression lifts automatically, so the picture is retried.

Nothing here is persisted - a server restart clears the registry, at which point
each such picture is retried exactly once more (re-decoded, fails, re-marked),
which is the accepted behaviour above. Keeping it in memory is deliberate: it
needs no schema change and no migration.
"""

import os
import threading

from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


class UnprocessableImageRegistry:
    """Thread-safe ``picture_id -> (file_path, mtime_ns, size)`` map of undecodable images.

    A picture is *suppressed* only while it is recorded here **and** its file's
    current ``(mtime_ns, size)`` still match what was captured when it was marked.
    Any change to the file (a rewrite, a repair, or the file being replaced) moves
    the signature and lifts suppression, so the picture flows back into the normal
    finder queue and is retried.

    All state is in memory and bounded to *max_entries*. Both the marking side
    (background task threads, on a decode failure) and the suppression side
    (finder threads, on every planning sweep) touch this map, so every method is
    guarded by a single lock. ``os.stat`` is performed under the lock: it is a
    fast syscall and contention is negligible (marks are rare, sweeps are cheap),
    and holding it removes any check-then-act race between marking and pruning.
    """

    # Corrupt images are pathological; this cap only bounds a runaway (e.g. a
    # whole reference tree of unreadable files) while staying far above any
    # realistic count.
    MAX_ENTRIES = 100_000

    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._max_entries = int(max_entries)
        self._lock = threading.Lock()
        # picture_id -> (file_path, mtime_ns, size)
        self._entries: dict[int, tuple[str, int, int]] = {}

    @staticmethod
    def _stat_signature(file_path: str) -> "tuple[int, int] | None":
        """Return ``(mtime_ns, size)`` for *file_path*, or ``None`` if it cannot be stat'd."""
        try:
            stat = os.stat(file_path)
        except OSError as exc:
            logger.debug(
                "UnprocessableImageRegistry: cannot stat %s: %s", file_path, exc
            )
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def mark_unprocessable(
        self, picture_id: "int | None", file_path: "str | None", *, reason: str = ""
    ) -> bool:
        """Record *picture_id* as undecodable, pinned to *file_path*'s current signature.

        A picture already marked for the *same* file version is a no-op (and is not
        re-logged), so repeated sweeps of the same corrupt file do not spam the log.
        A picture whose file can no longer be stat'd is left unmarked - there is
        nothing to pin the suppression to, and the missing-file purge finder owns
        that case.

        Args:
            picture_id: The picture that failed to decode.
            file_path: Absolute path to the file that could not be opened.
            reason: Short description of the failure, for the one-time log line.

        Returns:
            ``True`` if a new mark (or a re-mark for a changed file) was recorded.
        """
        if picture_id is None or not file_path:
            return False
        pid = int(picture_id)
        signature = self._stat_signature(file_path)
        if signature is None:
            logger.debug(
                "UnprocessableImageRegistry: not marking picture id=%s - file %s "
                "cannot be stat'd (leaving it to the missing-file purge).",
                pid,
                file_path,
            )
            return False
        mtime_ns, size = signature
        with self._lock:
            existing = self._entries.get(pid)
            if existing is not None and existing[1] == mtime_ns and existing[2] == size:
                return False  # already suppressed for this exact file version
            if existing is None and len(self._entries) >= self._max_entries:
                logger.warning(
                    "UnprocessableImageRegistry: cap (%d) reached; NOT suppressing "
                    "picture id=%s path=%s - it will keep being retried until an "
                    "existing entry is released or the server restarts.",
                    self._max_entries,
                    pid,
                    file_path,
                )
                return False
            self._entries[pid] = (str(file_path), mtime_ns, size)
        logger.warning(
            "Unprocessable image: picture id=%s path=%s could not be decoded (%s); "
            "skipping it for the rest of this server session (it will be retried "
            "only if the file changes).",
            pid,
            file_path,
            reason or "image could not be decoded",
        )
        return True

    def is_suppressed(self, picture_id: "int | None") -> bool:
        """Return whether *picture_id* is currently suppressed.

        ``True`` only if the picture is recorded and its stored file's current
        ``(mtime_ns, size)`` still match the marked signature. If the file changed
        or can no longer be stat'd the stale entry is pruned and ``False`` is
        returned, so the picture is retried.

        The signature is re-checked against the registry's OWN stored path, so
        callers never read ``Picture.file_path``: that ORM attribute is often
        deferred and the candidate pictures are detached by the time a finder
        claims, so touching it there raised ``DetachedInstanceError`` (issue #585).
        """
        if picture_id is None:
            return False
        pid = int(picture_id)
        with self._lock:
            existing = self._entries.get(pid)
            if existing is None:
                return False
            path, mtime_ns, size = existing
            signature = self._stat_signature(path)
            if signature is not None and signature == (mtime_ns, size):
                return True
            # File vanished or was rewritten since we marked it - retry it.
            self._entries.pop(pid, None)
            return False

    def active_suppressed_ids(self) -> set[int]:
        """Return the set of currently-suppressed picture ids, pruning stale entries.

        Re-validates every stored file signature; entries whose file changed or
        disappeared are dropped. Cheap in practice - the map only holds genuinely
        undecodable files, which are rare.
        """
        active: set[int] = set()
        with self._lock:
            for pid, (path, mtime_ns, size) in list(self._entries.items()):
                signature = self._stat_signature(path)
                if signature is not None and signature == (mtime_ns, size):
                    active.add(pid)
                else:
                    self._entries.pop(pid, None)
        return active

    def discard(self, picture_id: "int | None") -> None:
        """Forget *picture_id* (e.g. when the picture is deleted)."""
        if picture_id is None:
            return
        with self._lock:
            self._entries.pop(int(picture_id), None)

    def snapshot(self) -> dict[int, tuple[str, int, int]]:
        """Return a copy of the current ``{picture_id: (file_path, mtime_ns, size)}`` map."""
        with self._lock:
            return dict(self._entries)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
