import os
import threading
import time

from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models.import_folder import ImportFolder
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.image_processing.video_utils import VideoUtils

from .base_task_finder import BaseTaskFinder
from .watch_folder_import_task import WatchFolderImportTask

logger = get_logger(__name__)


class MissingWatchFolderImportFinder(BaseTaskFinder):
    """Find newly modified files in watch folders and create import tasks.

    Watch folder entries are read from the ``import_folder`` table.

    Each folder entry includes:

        folder (str): Absolute path to the directory to monitor recursively.
        delete_after_import (bool): When True, source files are deleted from
            the watch folder after a successful import. Defaults to False.
        last_checked (float): Unix timestamp of the last scan. Reported for
            operators only; candidacy is decided from per-path stat signatures,
            not from this value. Updated automatically after each scan; do not
            set this manually.

    A file is only imported once its size and timestamps have stopped changing
    for ``SETTLE_SECONDS``, so a copy that is still in flight is never read
    half-written. See ``_claim_if_settled``.
    """

    # How long a file's stat signature must stay unchanged before the file is
    # handed to an import task.
    #
    # "Unchanged on two consecutive scans" is not a sufficient test on its own:
    # the planner polls as often as ``WorkPlanner.MIN_INTERVAL_S`` (50ms), so a
    # copy that merely pauses between writes - a network hiccup, an antivirus
    # scan, a slow source drive, a writer flushing in chunks - looks settled
    # after 50ms of quiet and is imported half-written. A wall-clock window
    # makes the check independent of how fast the planner happens to be
    # cycling. The cost is that a newly arrived file waits this long before it
    # is imported.
    SETTLE_SECONDS = 2.0

    _supported_image_exts = {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".bmp",
        ".heic",
        ".heif",
        ".avif",
    }

    def __init__(self, database):
        super().__init__()
        self._db = database
        # Candidacy is decided from a per-path *stat signature* rather than from
        # timestamps compared against ``last_checked``.  Timestamps are not a
        # reliable "is this file new" signal in a watch folder: copy tools
        # (e.g. shutil.copy2) preserve the source mtime on the destination, and
        # on Windows ctime is the creation time and never moves at all.
        #
        # ``_pending_stats`` maps a path that has not been handed to an import
        # task yet to ``(signature, first_seen)``, where ``first_seen`` is the
        # monotonic timestamp at which the current signature was first observed.
        # ``_dispatched_stats`` holds the signature a path had when it *was*
        # handed over.  A path only becomes a candidate once its signature has
        # been unchanged for ``SETTLE_SECONDS``, which is what keeps a file that
        # is still being written out of the import (see ``_claim_if_settled``).
        #
        # Both dicts are read/written by the finder on the WorkPlanner thread and
        # also mutated by WatchFolderImportTask (via ``discard_seen_paths``) on
        # the TaskRunner thread when a candidate fails to import, so all access
        # is guarded by ``_seen_lock`` to avoid a concurrent-mutation race.
        self._pending_stats: dict[str, tuple[tuple[int, float, float], float]] = {}
        self._dispatched_stats: dict[str, tuple[int, float, float]] = {}
        self._seen_lock = threading.Lock()

    def finder_name(self) -> str:
        return "MissingWatchFolderImportFinder"

    def find_task(self):
        db_folders = self._load_db_watch_folders()
        if not db_folders:
            return None
        return self._find_task_from_db(db_folders)

    def _load_db_watch_folders(self) -> list[ImportFolder]:
        def fetch(session: Session):
            return session.exec(select(ImportFolder).order_by(ImportFolder.id)).all()

        try:
            folders = self._db.run_task(fetch, priority=DBPriority.IMMEDIATE)
        except Exception as exc:
            logger.warning(
                "MissingWatchFolderImportFinder: failed to read watch folders "
                "from DB this cycle; skipping: %s",
                exc,
            )
            return []
        return list(folders or [])

    def _find_task_from_db(self, watch_folders: list[ImportFolder]):
        now_ts = time.time()
        # Settle windows are measured on the monotonic clock so a wall-clock
        # adjustment cannot make a file look settled early (or never).
        scan_started = time.monotonic()
        candidate_files = []
        total_candidates = 0
        last_checked_updates = {}
        observed_paths: set[str] = set()

        for entry in watch_folders:
            if entry.id is None:
                continue
            folder = entry.folder
            delete_after_import = bool(entry.delete_after_import)

            if not folder or not os.path.isdir(folder):
                continue

            for root, _, files in os.walk(folder):
                for file_name in files:
                    file_path = os.path.join(root, file_name)
                    if not self._is_supported_file(file_path):
                        continue
                    signature = self._stat_signature(file_path)
                    if signature is None:
                        continue
                    normalized = os.path.normcase(os.path.abspath(file_path))
                    observed_paths.add(normalized)

                    if self._claim_if_settled(normalized, signature, scan_started):
                        total_candidates += 1
                        candidate_files.append(
                            {
                                "file_path": file_path,
                                "delete_after_import": delete_after_import,
                                "import_source_folder": folder,
                            }
                        )

            last_checked_updates[int(entry.id)] = now_ts

        self._forget_vanished_paths(observed_paths)

        if last_checked_updates and not candidate_files:
            self._persist_db_last_checked(last_checked_updates)
            return None

        if not candidate_files:
            return None

        return WatchFolderImportTask(
            database=self._db,
            candidate_files=candidate_files,
            total_candidates=total_candidates,
            last_checked_updates=last_checked_updates,
            finder=self,
        )

    def discard_seen_paths(self, file_paths: list[str]) -> None:
        """Forget paths so a later scan re-discovers them as new.

        Called by :class:`WatchFolderImportTask` for candidate files that failed
        to process (hash or import error). A file is recorded as dispatched when
        it is handed to an import task; dropping that record here makes the
        failure transient, because the next scans re-observe the path, wait for
        it to settle again and retry it.
        """
        if not file_paths:
            return
        normalized = {
            os.path.normcase(os.path.abspath(path)) for path in file_paths if path
        }
        if not normalized:
            return
        with self._seen_lock:
            for path in normalized:
                self._pending_stats.pop(path, None)
                self._dispatched_stats.pop(path, None)

    @staticmethod
    def _stat_signature(file_path: str) -> tuple[int, float, float] | None:
        """Return ``(size, mtime, ctime)`` for *file_path*, or None if unreadable.

        A single ``os.stat`` call so all three values describe the same moment;
        comparing this tuple across scans is what detects a file that is still
        being written into the watch folder.
        """
        try:
            stat_result = os.stat(file_path)
        except OSError as exc:
            logger.debug(
                "MissingWatchFolderImportFinder: cannot stat %s, skipping this "
                "scan: %s",
                file_path,
                exc,
            )
            return None
        return (stat_result.st_size, stat_result.st_mtime, stat_result.st_ctime)

    def _claim_if_settled(
        self,
        normalized: str,
        signature: tuple[int, float, float],
        now: float,
    ) -> bool:
        """Claim *normalized* for import if it has stopped changing on disk.

        Returns True when the path is ready to be handed to an import task,
        recording it as dispatched so a later scan does not offer it again.

        A path qualifies only once the same stat signature has been observed for
        ``SETTLE_SECONDS`` of wall-clock time. A file that is still being copied
        into the watch folder changes size (and mtime), so it is held back until
        the copy has been quiet for that long. Without this, the importer hashes
        the bytes written so far, stores a ``pixel_sha`` that does not describe
        the finished file, and then imports the file a second time once it
        settles, because the settled content hashes differently and the
        duplicate check misses it.

        *now* is the monotonic timestamp of the current scan; passing one value
        for the whole scan keeps every file in it measured against the same
        instant.

        A file that changes after it was imported goes back through the same
        wait, so an in-place overwrite is never read half-written either.
        """
        with self._seen_lock:
            dispatched = self._dispatched_stats.get(normalized)
            if dispatched is not None:
                if dispatched == signature:
                    return False
                # Changed on disk since it was imported: re-settle before it is
                # offered again.
                del self._dispatched_stats[normalized]
                self._pending_stats[normalized] = (signature, now)
                return False

            previous = self._pending_stats.get(normalized)
            if previous is None or previous[0] != signature:
                # First sighting, or still being written: (re)start the clock.
                self._pending_stats[normalized] = (signature, now)
                return False

            if now - previous[1] < self.SETTLE_SECONDS:
                # Unchanged so far, but not for long enough to call the write
                # finished. Keep the original first-seen time so the window
                # measures how long the file has been quiet, not how long since
                # the previous scan.
                return False

            del self._pending_stats[normalized]
            self._dispatched_stats[normalized] = signature
            return True

    def _forget_vanished_paths(self, observed_paths: set[str]) -> None:
        """Drop bookkeeping for paths that are no longer in any watch folder.

        Keeps the tracking dicts bounded on a long-running server, which matters
        most with ``delete_after_import`` where every imported file is removed
        from the folder straight away.
        """
        with self._seen_lock:
            for tracked in (self._pending_stats, self._dispatched_stats):
                vanished = tracked.keys() - observed_paths
                for path in vanished:
                    del tracked[path]

    def _persist_db_last_checked(self, updates: dict[int, float]):
        def update(session: Session, values: dict[int, float]):
            folders = session.exec(
                select(ImportFolder).where(ImportFolder.id.in_(list(values.keys())))
            ).all()
            for folder in folders:
                next_ts = values.get(int(folder.id))
                if next_ts is None:
                    continue
                folder.last_checked = float(next_ts)
                session.add(folder)
            session.commit()

        self._db.run_task(update, updates, priority=DBPriority.IMMEDIATE)

    def _is_supported_file(self, file_path: str) -> bool:
        ext = os.path.splitext(file_path)[1].lower()
        if ext in self._supported_image_exts:
            return True
        return VideoUtils.is_video_file(file_path)
