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

#: Stands in for the (mtime, size) of an entry that has no signature because
#: its file could not be stat'd at all. Its own object, not ``None``: ``None``
#: already means "stat failed just now" in :meth:`_stat_signature`, and the two
#: must not be confused.
_UNREACHABLE = object()


def _location_is_unreachable(file_path: str) -> bool:
    """``scrapheap_service.file_location_is_unreachable``, imported locally.

    Local because the import is a cycle: ``database`` imports this registry and
    ``scrapheap_service`` imports ``database``. One definition of "the parent
    directory is missing, so say nothing about the file" is worth the indirect
    import - the two must never drift, because between them they decide whether
    a picture is held or purged.
    """
    from pixlstash.services.scrapheap_service import file_location_is_unreachable

    return file_location_is_unreachable(file_path)


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

    def mark_unreachable(
        self, picture_id: "int | None", file_path: "str | None", *, reason: str = ""
    ) -> bool:
        """Record *picture_id* as unreachable: its file's whole LOCATION is gone.

        The other half of :meth:`mark_unprocessable`, which refuses a file it
        cannot ``stat`` and defers to the missing-file purge - and the purge
        deliberately refuses it too (``MissingFilePurgeTask`` skips a picture
        whose parent directory is missing, because purging would write
        ``file_removed=True`` and block a later restore). Between the two, a
        picture on an unplugged drive had no owner at all: every batch finder
        re-selected it on every sweep, produced nothing, and re-selected it
        again.

        Suppression is the right answer and deletion is not, because this
        state ends by itself: the entry lifts the moment the parent directory
        is back, so remounting the drive returns the picture to every stage
        with its pending work intact.

        Args:
            picture_id: The picture whose file could not be reached.
            file_path: Absolute path to the file, used to watch its parent.
            reason: Short description, for the one-time log line.

        Returns:
            ``True`` if a new mark was recorded.
        """
        if picture_id is None or not file_path:
            return False
        if not _location_is_unreachable(str(file_path)):
            # The directory is there, so the file is genuinely absent or
            # unopenable for some other reason - not this registry's case.
            return False
        pid = int(picture_id)
        with self._lock:
            if pid in self._entries:
                return False
            if len(self._entries) >= self._max_entries:
                logger.warning(
                    "UnprocessableImageRegistry: cap (%d) reached; NOT holding "
                    "picture id=%s path=%s as unreachable - it will keep being "
                    "retried until an existing entry is released.",
                    self._max_entries,
                    pid,
                    file_path,
                )
                return False
            # No signature: an unreachable file cannot be stat'd, and the
            # sentinel is what `_entry_is_live` reads to pick its predicate.
            self._entries[pid] = (str(file_path), _UNREACHABLE, _UNREACHABLE)
        logger.warning(
            "Unreachable image: picture id=%s path=%s - its location is not "
            "mounted (%s); holding it out of the work finders until the "
            "directory is back. Nothing is deleted and no work is lost.",
            pid,
            file_path,
            reason or "parent directory missing",
        )
        return True

    def _entry_is_live(self, path: str, mtime_ns: int, size: int) -> bool:
        """Whether a recorded entry still holds its picture out of the finders.

        Two kinds share one map. An *unreachable* entry lives while its
        location is still missing, and is dropped the moment the volume is
        back. An *undecodable* entry lives while the file is byte-for-byte the
        one that failed, and is dropped when it is rewritten or repaired.
        """
        if mtime_ns is _UNREACHABLE:
            return _location_is_unreachable(path)
        signature = self._stat_signature(path)
        return signature is not None and signature == (mtime_ns, size)

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
            if self._entry_is_live(path, mtime_ns, size):
                return True
            # Rewritten, repaired, or the volume is back - retry it.
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
                if self._entry_is_live(path, mtime_ns, size):
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
