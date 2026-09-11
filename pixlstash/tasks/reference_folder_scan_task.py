"""Task that scans a reference folder and indexes new image files in place."""

import concurrent.futures
import io
import os
import time
from datetime import datetime, timezone

from PIL import Image
from sqlmodel import Session, delete, select

from pixlstash.database import DBPriority
from pixlstash.db_models.deleted_file_log import DeletedFileLog
from pixlstash.db_models.folder_mapping_commit import (
    STATE_ABANDONED,
    STATE_DEFERRED,
    STATE_DONE,
    STATE_PENDING,
    STATE_SUPERSEDED,
    FolderMappingCommit,
)
from pixlstash.db_models.library_settings import LibrarySettings
from pixlstash.db_models.picture import Picture
from pixlstash.db_models.reference_folder import ReferenceFolder, ReferenceFolderStatus
from pixlstash.db_models.tag import (
    Tag,
    TAG_PENDING_SENTINEL,
    description_caption_content,
    is_tag_sentinel,
)
from pixlstash.services.set_lock_service import locked_picture_ids
from pixlstash.tasks.base_task import BaseTask
from pixlstash.tasks.missing_file_purge_task import MissingFilePurgeTask
from pixlstash.utils.caption_file_utils import (
    DEFAULT_DESCRIPTION_SUFFIX,
    DEFAULT_TAGS_SUFFIX,
    SIDECAR_TYPE_DESCRIPTION,
    SIDECAR_TYPE_TAGS,
    attach_sidecars,
    detect_folder_suffixes,
    get_sidecar_mtime,
    is_safe_sidecar_suffix,
    parse_caption_tags,
    read_caption_text,
    recorded_sidecar,
    resolve_typed_sidecar,
    suffixes_collide,
    write_sidecar,
    writeback_path,
)
from pixlstash.utils.image_processing.image_utils import ImageUtils, THUMBNAIL_EXTENSION
from pixlstash.utils.image_processing.video_utils import VideoUtils
from pixlstash.utils.media_files import (
    has_hidden_component,
    is_hidden_entry,
    is_supported_media_file,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.services.layout_move_service import (
    claim_own_moves,
    prune_move_journal,
)
from pixlstash.services.move_reconciliation_service import record_pending_reviews
from pixlstash.utils.library_layout import DEFAULT_LAYOUT, parse_layout
from pixlstash.utils.reference_folder_watcher import ROOT_INTERNAL_DIRS
from pixlstash.utils.path_utils import path_is_within
from pixlstash.utils.service.smart_score_invalidation import (
    invalidate_on_anomaly_change,
)

logger = get_logger(__name__)

#: Marker file the removed PixlStash Views feature wrote at the root of a
#: published tree. The feature is gone, but its API was live in v1.11 and any
#: tree it published is still on disk, so the scan still refuses to descend one.
#: Removing this check would double-import every picture under such a folder.
VIEWS_MARKER_NAME = ".pixlstash-views"

_BUILD_CHUNK_SIZE = 128
_MAX_BUILD_WORKERS = 8

# Top-level directories PixlStash itself writes under the library root, and so
# must never be indexed by a root scan; the watcher ignores the same set.
# Dot-directories (.pixlstash-thumbnails, .staging) are pruned by name shape.
# vault.db and friends are not media and fall out on extension.
_ROOT_INTERNAL_DIRS = ROOT_INTERNAL_DIRS

# A file in the library root younger than this is left alone by a root scan.
# PixlStash's own imports write the file first and insert the row a moment
# later (longer under load), and a scan landing in between would index the
# file as the owner's and leave the import to insert a second row. A file the
# owner drops in is picked up once it has settled, on the next scan. A rename
# keeps the mtime, so a moved file is followed at once.
_ROOT_SETTLE_S = 60.0


def root_import_answered(session: Session) -> bool:
    """Whether the library root may be indexed yet.

    A library that holds no picture and has never had a folder-mapping commit
    is one whose owner has not been asked what the pictures in its folder are.
    The app asks when it loads an empty library over a folder that holds
    pictures - the first-run offer, and "Add a library" - and the boot-time
    root scan used to answer first: a small library was indexed, tags and all,
    before the screen came up, so the grid was no longer empty and the
    questions never appeared. The root scan waits for the answer.

    A ``local_import`` commit record counts only once it has **settled**:
    ``done``, or ``deferred`` ("organise later", which is an answer - index
    everything, map nothing). A ``pending`` one is the commit still running,
    so it is the question being answered rather than the answer, and treating
    it as one puts the 300-second root scan into a race with it: the scan
    builds its own rows with the sidecar probe, wins, and the commit's
    ``insert()`` reuses them without ever applying the owner's caption
    choices. ``abandoned`` is "bring nothing in" and ``superseded`` was
    replaced by a newer record, so neither is an answer either. A
    ``reference`` commit is not one at all: it registers some other folder and
    says nothing about the root's own pictures.

    The **newest** ``local_import`` record decides, because records are kept
    and an older answer must not outrank a newer one: a ``done`` import
    followed by an aborted one is an owner who declined the second batch, and
    reading the old ``done`` as the answer would scan in the very files just
    aborted. A ``pending`` or ``abandoned`` newest record wins over the picture
    row for the same reason: an unsettled ``local_import`` commits every chunk,
    so after the first one the library holds pictures the owner has not
    answered for. A running commit wakes the planner while its record is still
    ``pending``, and an aborted one leaves its chunks indexed behind
    ``abandoned``, and a ``superseded`` newest one was replaced by a reference
    commit with its chunks just as unanswered. Only with no ``local_import``
    record at all does a picture row count: that library was filled some other
    way and is scanned as before.

    The two facts are read in **one statement** so they describe one snapshot:
    this read runs outside the writer queue, so across separate statements a
    chunk committing in between shows no ``pending`` record and a picture from
    that very commit, which would answer True with the import still running.

    Asked twice, by both halves of the handoff.
    :meth:`~pixlstash.tasks.reference_folder_scan_finder.ReferenceFolderScanFinder.first_import_answered`
    decides whether a root scan is handed out at all; the re-check at the start
    of :meth:`ReferenceFolderScanTask._run_task` is what makes that handoff
    fail closed, since `record_pending_commit` can write a pending record
    between the finder's read and the runner starting the task. Never cached
    on either side: a later import must close the gate again the moment its
    record is pending.
    """
    newest = (
        select(FolderMappingCommit.state)
        .where(FolderMappingCommit.mode == "local_import")
        .order_by(FolderMappingCommit.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    newest_state, has_picture = session.exec(
        select(newest, select(Picture.id).exists())
    ).one()
    if newest_state in (STATE_PENDING, STATE_ABANDONED, STATE_SUPERSEDED):
        # Superseded is a pending record a newer commit of either mode
        # replaced. A newer local_import would be the newest row itself, so a
        # superseded newest one was replaced by a reference commit and its
        # chunks are as unanswered as a pending one's.
        return False
    return newest_state in (STATE_DONE, STATE_DEFERRED) or has_picture


def _is_supported_file(file_path: str) -> bool:
    return is_supported_media_file(file_path)


class ReferenceFolderScanTask(BaseTask):
    """Task that scans a single reference folder and indexes new image files in place.

    New files found on disk are inserted as Picture rows with their absolute
    paths so that PixlStash serves them directly from their original location.
    Files that have been removed from disk since the last scan have their DB
    records deleted, unless the same pixels turned up at a new path in the same
    pass: that is a move, and the existing record follows the file rather than
    being replaced by a fresh one.

    Phase-2 path validation (resolve mapping → blocklist → isdir/access) is
    performed before any filesystem work.  On failure the folder status is set
    to ``mount_error`` and the task exits without touching the picture table.

    A followed move is attributed before it is reported: a pair the move
    journal claims was made by PixlStash itself (v1.11 Phase 4b), and every
    other pair was made by the owner.  ``external_moved_picture_ids`` in the
    result is that second list, and it is what Phase 5 reconciles.  Without the
    split, PixlStash's own writes come back through this scan as owner intent
    and the two flip each other for ever.

    Of that list, only the moves inside a root that has a *layout* are queued
    for review (``external_moves_queued_for_review``,
    ``move_reconciliation_service.record_pending_reviews``): a root with no
    layout has no vocabulary a folder name could contradict, so there is
    nothing for Phase 5 to reconcile even though the file still moved.

    **The library's own picture root is scanned by this same task** with
    ``folder_id=None``. A laid-out root is a folder tree the owner reorganises
    in their file manager, and without this scan a rename there is a row the
    purge sweep deletes an hour later. The root differs from a reference folder
    in exactly the ways ``layout_move_service.LayoutRoot`` names: pictures are
    the ``reference_folder_id IS NULL`` rows, ``Picture.file_path`` is stored
    relative to the root (:meth:`_stored`), the layout and the caption-file
    sync settings come from ``LibrarySettings`` and there is no status.
    Everything else - move following by pixel hash, the move journal, the
    review queue, the thumbnail carry, the sidecar reconcile - is shared
    unchanged.
    """

    def __init__(
        self,
        database,
        folder_id: int | None,
        folder_path: str,
        resolved_path: str,
        other_resolved_paths: frozenset[str] = frozenset(),
        on_root_scanned=None,
    ):
        super().__init__(
            task_type="ReferenceFolderScanTask",
            params={
                "folder_id": folder_id,
                "folder_path": folder_path,
                "resolved_path": resolved_path,
            },
        )
        self._db = database
        self._folder_id = folder_id
        self._folder_path = folder_path
        self._resolved_path = resolved_path
        self._other_resolved_paths = other_resolved_paths
        # ``None`` is the library's own picture root (see the class docstring).
        self._is_root = folder_id is None
        self._on_root_scanned = on_root_scanned
        # Sidecar filename suffixes for this folder, loaded at the start of
        # _run_task(); None means "use known conventions / module defaults".
        self._tags_suffix: str | None = None
        self._description_suffix: str | None = None
        # Sidecar kinds this scan must not read or write at all, filled in by
        # _run_task() when a detected suffix is refused (see there).
        self._disabled_kinds: set[str] = set()
        # The folder's layout, loaded at the start of _run_task(); None means
        # "no layout", same as an unset column (v1.11 Phase 5).
        self._layout = None

    def _import_started_mid_scan(self) -> bool:
        """Whether a local import began after this root scan's gate read.

        `root_import_answered` reads the database and releases it, and this
        task does more work before `os.walk` even starts. In that window the
        commit endpoint can write its pending row and set
        `local_import_pictures` walking, and then both sides build a row for
        the same file - exactly the race the gate exists to prevent. Another
        preflight read cannot close it, because the window is *after* the
        read, not before it.

        `vault.db.local_import_running` closes it, and the ORDER is what makes
        it fail closed. `record_pending_commit` SETS the flag **before** it
        writes its row and therefore before its walk; the root scan checks it
        **after** its own gate read and again once per directory. So a commit
        that starts after the scan's read is seen at the latest one directory
        later, and the rows built in that one directory are reused by the
        commit's `insert()` rather than duplicated. The window is bounded to a
        single directory instead of the whole walk, and it never runs the other
        way: the flag cannot be stale in the direction that lets a scan walk on
        during a live import.

        Cheap on purpose - an in-process `Event.is_set()`, not a query - so
        asking it per directory costs nothing. The database re-check stays for
        the cases an in-memory flag cannot cover: a crash-resumed import (the
        flag is seeded at vault start) and any second process.
        """
        if not self._is_root or not self._db.local_import_running.is_set():
            return False
        logger.info(
            "Library root scan stopped: a local import is pending. Nothing "
            "further is indexed; retried on a later cycle."
        )
        return True

    def _run_task(self):
        resolved = self._resolved_path
        folder_id = self._folder_id

        # The finder's read decided a root scan could be handed out; this asks
        # again now the scan is actually starting, which is what makes the
        # handoff fail closed. `record_pending_commit` can write a pending
        # local_import in between - the owner answering the first-import offer
        # while this task sat in the queue - and walking on that stale answer
        # is the race the gate exists to prevent. `_root_last_scanned` stays
        # stamped: `mark_root_due` or the next interval asks again.
        if self._is_root and not self._db.run_immediate_read_task(root_import_answered):
            logger.info(
                "Library root scan skipped: an unanswered local import was "
                "recorded after the scan was queued. Retried on a later cycle."
            )
            return {"status": "skipped", "folder_id": folder_id}

        # And the same question the cheap way, for the commit that starts
        # AFTER the read above. See `_import_started_mid_scan`.
        if self._import_started_mid_scan():
            return {"status": "skipped", "folder_id": folder_id}

        if not os.path.isdir(resolved):
            logger.warning(
                "Reference folder %s (resolved: %s) is not a directory - marking mount_error",
                self._folder_path,
                resolved,
            )
            self._set_status(ReferenceFolderStatus.MOUNT_ERROR)
            return {"status": "mount_error", "folder_id": folder_id}

        if not os.access(resolved, os.R_OK | os.X_OK):
            logger.warning(
                "Reference folder %s (resolved: %s) is not readable - marking mount_error",
                self._folder_path,
                resolved,
            )
            self._set_status(ReferenceFolderStatus.MOUNT_ERROR)
            return {"status": "mount_error", "folder_id": folder_id}

        # Load the folder's sidecar configuration once. The suffixes drive how
        # tags/description sidecars are resolved for new and existing pictures;
        # the sync flags decide whether missing sidecars are exported to disk.
        def fetch_folder_config(session: Session):
            if self._is_root:
                settings = session.exec(select(LibrarySettings)).first()
                if settings is None:
                    return (None, None, False, False, False, None, None)
                return (
                    settings.tags_suffix,
                    settings.description_suffix,
                    bool(settings.sync_tags),
                    bool(settings.sync_descriptions),
                    False,
                    settings.layout,
                    settings.layout_unfiled,
                )
            rf = session.get(ReferenceFolder, folder_id)
            if rf is None:
                return None
            return (
                rf.tags_suffix,
                rf.description_suffix,
                bool(rf.sync_tags),
                bool(rf.sync_descriptions),
                bool(rf.pending_reimport),
                rf.layout,
                rf.layout_unfiled,
            )

        config = self._db.run_task(fetch_folder_config, priority=DBPriority.LOW)
        (
            self._tags_suffix,
            self._description_suffix,
            sync_tags,
            sync_descriptions,
            pending_reimport,
            layout_text,
            layout_unfiled,
        ) = config or (None, None, False, False, False, None, None)
        # v1.11 Phase 5: only a laid-out root has a vocabulary a folder name
        # can contradict, so only a laid-out root's moves are worth queuing for
        # reconciliation at all (see move_reconciliation_service.record_pending_reviews).
        try:
            self._layout = parse_layout(
                layout_text, layout_unfiled or DEFAULT_LAYOUT.unfiled
            )
        except ValueError as exc:
            logger.error(
                "Reference folder %s: layout %r is not usable: %s. Moves made "
                "outside PixlStash will not be reconciled until it is corrected.",
                self._folder_path,
                layout_text,
                exc,
            )
            self._layout = None

        # When a synced folder has no explicit suffix yet (a migrated folder or a
        # Docker folder added before its mount was reachable), detect the naming
        # convention already on disk and lock it in.  This keeps exports aligned
        # with any existing sidecars instead of creating duplicates under the
        # default names.  Only runs while a suffix is unset and sync is on.
        if (sync_tags or sync_descriptions) and (
            self._tags_suffix is None or self._description_suffix is None
        ):
            # Detect over exactly what this scan is about to walk. The walk
            # below prunes hidden trees, the reference folders registered
            # inside this one, and (under the root) PixlStash's own
            # directories; a suffix detected from captions in one of those is
            # persisted as this library's convention and then names every
            # sidecar written beside pictures the scan does index.
            skip_dirs = set(self._other_resolved_paths)
            if self._is_root:
                skip_dirs |= {
                    os.path.join(resolved, name) for name in _ROOT_INTERNAL_DIRS
                }
            detected = detect_folder_suffixes(resolved, skip_dirs=skip_dirs)
            seed: dict[str, str] = {}
            if self._tags_suffix is None:
                self._tags_suffix = detected["tags_suffix"] or DEFAULT_TAGS_SUFFIX
                seed["tags_suffix"] = self._tags_suffix
            if self._description_suffix is None:
                self._description_suffix = (
                    detected["description_suffix"] or DEFAULT_DESCRIPTION_SUFFIX
                )
                seed["description_suffix"] = self._description_suffix
            if seed:
                # What the folder actually holds after the write is what drives
                # this scan, not what was detected: the owner may have stored a
                # suffix through the API since the config was fetched, and that
                # value - not the detection the writer then skipped - is the
                # one the rest of the scan must reconcile with.
                #
                # No stored suffix means the detection was refused (unsafe, or
                # colliding with the other kind), and that kind stays OFF for
                # this scan rather than falling back to the known conventions:
                # the probe finds the very file the rejection was about. Tags
                # on `_caption.txt` refuse the detected description suffix
                # `_caption.txt`, and a description probe would then re-find it
                # and read one file as both kinds - which is what the collision
                # rule forbids.
                effective = self._persist_suffixes(seed)
                if "tags_suffix" in seed:
                    self._tags_suffix = effective.get("tags_suffix")
                    if not self._tags_suffix:
                        self._disabled_kinds.add(SIDECAR_TYPE_TAGS)
                if "description_suffix" in seed:
                    self._description_suffix = effective.get("description_suffix")
                    if not self._description_suffix:
                        self._disabled_kinds.add(SIDECAR_TYPE_DESCRIPTION)

        # Collect all supported files currently on disk.
        # Skip PixlStash-generated thumbnail files (e.g. foo_thumb.webp) that
        # may have been written next to source files by an older version - they
        # are not real pictures and would cause infinite re-indexing churn.
        _thumb_suffix = f"_thumb{THUMBNAIL_EXTENSION}"
        other_roots = self._other_resolved_paths
        disk_paths: set[str] = set()
        # Root mode: files too young to be anyone's but the writer's. On disk,
        # so never "removed"; not indexed, so never "new" - see _ROOT_SETTLE_S.
        settling: set[str] = set()
        settle_before = time.time() - _ROOT_SETTLE_S
        # Every subtree this walk did not look inside. "Absent from disk_paths"
        # is what this task hard-deletes a Picture row for -- tags, scores,
        # memberships and all -- so a subtree nobody looked in must never be
        # read as "the owner deleted everything under it". Each one is
        # REMEMBERED, not merely skipped: a kept row costs one stale record
        # until the next scan, a wrong delete costs the pictures.
        unscanned_roots: list[str] = []

        def _walk_error(exc: OSError) -> None:
            # os.walk swallows listdir/scandir failures silently by default,
            # which turns an unreadable directory into an empty one.
            failed = getattr(exc, "filename", None) or resolved
            unscanned_roots.append(failed)
            logger.warning(
                "Reference folder %s: could not list %s (%s); the records under "
                "it are kept rather than removed.",
                self._folder_path,
                failed,
                exc,
            )

        for root, dirs, files in os.walk(resolved, topdown=True, onerror=_walk_error):
            # One `is_set()` per directory: a commit accepted after the gate
            # read above is seen at the latest one directory later, and this
            # walk has built nothing yet, so stopping here leaves the whole
            # import to the commit. `_root_last_scanned` stays stamped and
            # `on_root_scanned` is not called, so the purge sweep keeps
            # waiting. See `_import_started_mid_scan`.
            if self._import_started_mid_scan():
                return {"status": "skipped", "folder_id": folder_id}
            # Prune subdirectories that are roots of other reference folders so
            # their files are only indexed by their own scan task; dot-folders,
            # which are nobody's pictures - a vault's own caches or something
            # the owner hid, and which the Phase 2 folder-structure read prunes
            # too, so the read that counts a root and the scan that indexes it
            # cannot disagree about what is in it; and, under the library root,
            # the non-dot folders PixlStash writes itself.
            kept: list[str] = []
            for name in dirs:
                full = os.path.join(root, name)
                if (
                    full in other_roots
                    or is_hidden_entry(name)
                    or (
                        self._is_root
                        and root == resolved
                        and name in _ROOT_INTERNAL_DIRS
                    )
                ):
                    unscanned_roots.append(full)
                    continue
                if os.path.islink(full):
                    # os.walk does not descend a directory symlink, so nothing
                    # under it was looked at either.
                    unscanned_roots.append(full)
                    continue
                kept.append(name)
            dirs[:] = kept
            # Prune a PixlStash Views tree. Every file under it is a link to a
            # picture indexed somewhere else already, and os.walk lists a
            # symlinked *file* in ``files`` -- only symlinked directories are
            # skipped by default -- so without this each picture would be
            # indexed a second time under its view path. The feature that wrote
            # these trees has been removed, but its API shipped in v1.11 and any
            # tree it published is still on disk.
            #
            # Remembered like every other unscanned subtree above: pruning
            # alone would turn a marker file appearing over an indexed folder
            # into a silent library deletion, which is a far worse failure than
            # the double indexing this prune exists to prevent.
            if VIEWS_MARKER_NAME in files:
                dirs[:] = []
                unscanned_roots.append(root)
                continue
            for file_name in files:
                if is_hidden_entry(file_name) or file_name.endswith(_thumb_suffix):
                    continue
                full_path = os.path.join(root, file_name)
                if _is_supported_file(full_path):
                    disk_paths.add(full_path)
                    if self._is_root:
                        try:
                            if os.stat(full_path).st_mtime > settle_before:
                                settling.add(full_path)
                        except OSError as exc:
                            # Gone between listing and stat - the next scan
                            # sees whatever is true then.
                            logger.debug(
                                "Root scan: could not stat %s: %s", full_path, exc
                            )

        # Fetch all picture paths already indexed for this reference folder,
        # including scrapheap (deleted=True) pictures.  Scrapheap pictures must
        # be present in existing_by_path so their file paths are subtracted from
        # `new_paths`; without them, the scan would re-import the same file every
        # time it ran while the picture sat in the scrapheap.
        def fetch_existing(session: Session) -> list[Picture]:
            owner = (
                Picture.reference_folder_id.is_(None)
                if self._is_root
                else Picture.reference_folder_id == folder_id
            )
            return list(session.exec(select(Picture).where(owner)).all())

        existing_pictures: list[Picture] = self._db.run_task(
            fetch_existing, priority=DBPriority.LOW
        )
        # Keyed by the path os.walk produces. For a reference folder that is
        # the stored absolute path as-is; for the root the stored path is
        # relative, so it is resolved here and a row whose path escapes the
        # root is left out - not in disk_paths either, so never "removed".
        existing_by_path: dict[str, Picture] = {}
        for p in existing_pictures:
            if not p.file_path:
                continue
            key = p.file_path
            if self._is_root:
                key = os.path.normpath(os.path.join(resolved, p.file_path))
                if not path_is_within(key, resolved):
                    continue
            existing_by_path[key] = p

        # Fetch the permanent-deletion ledger.  When a user empties the
        # scrapheap and the reference folder forbids file deletion
        # (allow_delete_file=False), the Picture row is removed but the file
        # stays on disk; a DeletedFileLog row records the path hash so the file
        # is never re-imported.  Match disk paths against the ledger by the same
        # path_sha used by the writer so a still-present file is skipped.
        def fetch_deleted_path_shas(session: Session) -> set[str]:
            rows = session.exec(select(DeletedFileLog.path_sha)).all()
            return {sha for sha in rows if sha}

        deleted_path_shas: set[str] = self._db.run_task(
            fetch_deleted_path_shas, priority=DBPriority.LOW
        )

        # Determine what is new and what has been removed.  A disk path is new
        # only if it is not already indexed.
        candidate_new = disk_paths - set(existing_by_path.keys()) - settling

        # An *explicit* (re-)import overrides the ledger; a routine background
        # sync does not.  The signal is the dedicated ``pending_reimport`` flag,
        # set only by the deliberate folder (re-)add endpoint and cleared by this
        # scan once it completes (see the end of _run_task).  No routine path
        # (sync-toggle, rename, relocate, mount-recovery, watcher, periodic
        # re-scan) ever sets it, so a routine scan can never override the ledger
        # - this closes the edge where an already-emptied folder whose
        # last_scanned was reset would have resurfaced removed-but-kept files.
        # On the explicit path we re-import ledger-listed files that are actually
        # present on disk and clear their ledger entries so restore resurfaces
        # them.  Because every cleared path is drawn from disk_paths,
        # genuinely-gone content (absent on disk, never in disk_paths) is never
        # resurfaced or restored.
        is_explicit_import = pending_reimport
        if is_explicit_import:
            new_paths = set(candidate_new)
            override_path_shas = {
                DeletedFileLog.hash_path(self._stored(p)) for p in candidate_new
            } & deleted_path_shas
        else:
            new_paths = {
                p
                for p in candidate_new
                if DeletedFileLog.hash_path(self._stored(p)) not in deleted_path_shas
            }
            override_path_shas = set()
        removed_paths = set(existing_by_path.keys()) - disk_paths
        # Hidden paths first, and separately from the unscanned subtrees below:
        # `unscanned_roots` records pruned DIRECTORIES, and the file loop skips
        # hidden FILES as well, which nothing above remembers. Same reasoning
        # either way - a path that was never looked for says nothing about
        # whether its file is still there, and "absent from disk_paths" is what
        # this task hard-deletes a row for. A folder indexed before the prune
        # existed keeps its pictures instead of losing them on the next scan.
        hidden_kept = {p for p in removed_paths if has_hidden_component(p, resolved)}
        if hidden_kept:
            logger.info(
                "Reference folder %s: %d indexed pictures lie under a hidden "
                "folder that is no longer scanned, so their records are kept "
                "rather than removed.",
                self._folder_path,
                len(hidden_kept),
            )
            removed_paths -= hidden_kept
        if removed_paths and unscanned_roots:
            # A path under a subtree this walk did not enter was not looked
            # for, so its absence from disk_paths says nothing about whether
            # the file is there. Deleting its row would be acting on a question
            # never asked.
            skipped = {
                path
                for path in removed_paths
                if any(path_is_within(path, skip_root) for skip_root in unscanned_roots)
            }
            if skipped:
                logger.info(
                    "Reference folder %s: %d indexed picture(s) lie under a "
                    "subtree this scan did not enter, so their records are kept "
                    "rather than removed.",
                    self._folder_path,
                    len(skipped),
                )
                removed_paths -= skipped
        if removed_paths and not disk_paths:
            # Nothing at all was found where a whole library is indexed. An
            # empty directory is exactly what an unmounted drive looks like -
            # Vault.__init__ creates the mount point, so the path exists and is
            # readable - and "the owner deleted every single file" is the far
            # less likely reading. Keep the rows; a mounted drive brings them
            # back for free, and there is no coming back from the alternative.
            logger.warning(
                "Reference folder %s: no files at all were found while %d "
                "picture(s) are indexed there. Treating this as an unmounted or "
                "unreadable location, not as a deletion, and keeping the records.",
                self._folder_path,
                len(removed_paths),
            )
            removed_paths = set()

        # --- Override the ledger on an explicit re-import ---
        # Clear the permanent-deletion ledger rows for the re-imported paths so a
        # subsequent restore no longer treats them as deleted.  Safe by
        # construction: every path_sha here belongs to a file found on disk in
        # this scan, so the content is present - clearing cannot resurrect gone
        # content.
        if override_path_shas:

            def clear_ledger(session: Session, shas: list[str]) -> int:
                result = session.exec(
                    delete(DeletedFileLog).where(DeletedFileLog.path_sha.in_(shas))
                )
                session.commit()
                return int(result.rowcount or 0)

            cleared = self._db.run_task(
                clear_ledger, sorted(override_path_shas), priority=DBPriority.LOW
            )
            logger.info(
                "Reference folder %s: explicit re-import cleared %d permanent-"
                "deletion ledger entries for files present on disk.",
                self._folder_path,
                cleared,
            )

        # --- Follow moved files before anything is deleted ---
        # A file moved inside the folder is one path in ``removed_paths`` and
        # another in ``new_paths``, same bytes.  Handled in that order it is a
        # delete plus a re-add: the row goes and with it everything keyed to the
        # picture id (tags, smart score, faces, likeness pairs, project/set
        # membership, stack membership, review state).  The pixels survive and
        # everything PixlStash added does not, so the only safe way to use a
        # reference folder was to never reorganize it.  Both halves are already
        # in this same pass, so match them and move the row instead.
        #
        # Runs before the removal block by necessity: the delete is what
        # destroys the row being rescued.
        moved_paths = self._match_moved_paths(
            existing_by_path, new_paths, removed_paths
        )
        moved_picture_ids: list[int] = []
        # The moves this scan attributes to the OWNER, i.e. everything the move
        # journal did not claim. v1.11 Phase 5 reconciles these into assignment
        # changes; Phase 4b's job is only to make sure PixlStash's own writes
        # are never in the list.
        external_moved_picture_ids: list[int] = []
        if moved_paths:
            # The thumbnail is stored under sha256(file_path), so the bitmap
            # follows the file rather than being abandoned at the old name.
            # Carrying it beats blanking the dimensions and letting
            # MissingThumbnailFinder regenerate: nothing is re-rendered, no
            # unreachable file is left behind in .ref_thumbs (the only cleanup
            # that exists derives its paths from each row's *current*
            # file_path, so an orphan there is permanent), and the row never
            # becomes NULL-width, which is what an in-flight
            # ThumbnailGenerationTask would otherwise be free to overwrite.
            carried_thumbnails = {
                old: self._carry_thumbnail(self._stored(old), self._stored(new))
                for old, new in moved_paths.items()
            }

            def apply_moves(
                session: Session, pairs: list[tuple[int, str, bool, str]]
            ) -> list[int]:
                # Which of these moves did PixlStash make itself? The layout
                # engine (v1.11 Phase 4b) journals every file it moves, and a
                # move that is ours is NOT the owner reorganising their library:
                # reading it as intent is what makes our write come back as a
                # change, unfile the picture, and start the two flipping each
                # other for ever over real files. Claimed here, at the one place
                # that has both paths and a session.
                ours = claim_own_moves(
                    session,
                    [
                        (self._stored(old_path), self._stored(new_path))
                        for _, new_path, _, old_path in pairs
                    ],
                )
                # The journal's only other reader is ``LayoutMoveTask``, which
                # runs only when a picture is due a check - so in a library
                # where the owner only ever renames things, nothing would ever
                # prune the rows a rename writes. This scan runs on its own
                # schedule and is the journal's other consumer, so it is where
                # the retention window is actually enforced.
                prune_move_journal(session)
                external: list[int] = []
                external_moves: list[tuple[int, str, str]] = []
                for pic_id, new_path, thumbnail_carried, old_path in pairs:
                    pic = session.get(Picture, pic_id)
                    if pic is None:
                        # The row went between the scan's read and this write -
                        # the purge sweep is the likely author.  The pair has
                        # already been taken out of removed_paths and new_paths,
                        # so the file is now neither moved nor imported until the
                        # next scan; say so rather than losing it silently.
                        logger.warning(
                            "Reference folder %s: picture %d vanished before its "
                            "move to %s could be applied; the file will be "
                            "re-imported on the next scan.",
                            self._folder_path,
                            pic_id,
                            new_path,
                        )
                        continue
                    pic.file_path = self._stored(new_path)
                    # The explicit move route (routes/reference_folders.py) sets
                    # this from the destination basename, and _build_picture
                    # initialises it from the path.  Leaving it alone would make
                    # a renamed file download under its old name.
                    pic.original_file_name = os.path.basename(new_path)
                    if not thumbnail_carried:
                        # No bitmap to carry, so point the sweep at it instead of
                        # at a thumbnail that is not there.
                        pic.thumbnail_width = None
                        pic.thumbnail_height = None
                    session.add(pic)
                    stored_pair = (self._stored(old_path), self._stored(new_path))
                    if stored_pair not in ours:
                        external.append(pic_id)
                        external_moves.append((pic_id, *stored_pair))
                if external_moves and self._layout is not None:
                    # Only a laid-out root has a vocabulary this move could
                    # contradict (v1.11 Phase 5); see
                    # move_reconciliation_service.record_pending_reviews.
                    record_pending_reviews(session, external_moves)
                session.commit()
                return external

            move_pairs = sorted(
                (
                    existing_by_path[old].id,
                    new,
                    carried_thumbnails.get(old, False),
                    old,
                )
                for old, new in moved_paths.items()
                if existing_by_path[old].id is not None
            )
            external_moved_picture_ids = (
                self._db.run_task(apply_moves, move_pairs, priority=DBPriority.LOW)
                or []
            )
            # A followed move changes file_path (and with it the thumbnail URL
            # and the download name) on a row an open grid may already be
            # showing, and nothing else in this task reports it: without this the
            # grid keeps the old state until the next full reload.
            moved_picture_ids = [pic_id for pic_id, _, _, _ in move_pairs]
            logger.info(
                "Reference folder %s: followed %d moved file(s), %d of them "
                "moved by PixlStash itself.",
                self._folder_path,
                len(moved_paths),
                len(moved_paths) - len(external_moved_picture_ids),
            )
            # A moved file is neither removed nor new.  ``existing_by_path`` is
            # re-keyed as well as narrowed, because the sidecar pass below walks
            # it by path and would otherwise reconcile at the old location.
            for old_path, new_path in moved_paths.items():
                picture = existing_by_path.pop(old_path)
                picture.file_path = new_path
                existing_by_path[new_path] = picture
            removed_paths -= moved_paths.keys()
            new_paths -= set(moved_paths.values())

        # --- Handle removed files ---
        removed_ids: list[int] = []
        if removed_paths:
            candidates = [
                existing_by_path[p]
                for p in removed_paths
                if existing_by_path[p].id is not None
            ]
            # Consult the move journal before deleting anything, with the same
            # reader the purge sweep uses so the two cannot disagree about
            # whose move a vanished path was. A row the layout engine moved but
            # had not finished repointing is repaired here; a move still in
            # flight defers to a later scan. Without this, a root scan landing
            # inside LayoutMoveTask's rename-then-repoint window hard-deletes
            # exactly the rows the engine is about to repoint, whenever hash
            # pairing refused to call it a move.
            repairs, deferred, still_missing = MissingFilePurgeTask(
                database=self._db, pictures=[]
            )._separate_our_own_moves(candidates)
            if repairs:
                self._db.run_task(
                    MissingFilePurgeTask._repair_moved_pictures,
                    repairs,
                    self._db.image_root,
                    priority=DBPriority.LOW,
                )
                # The file is at the repointed path, so it is that row's file
                # and not a new import.
                new_paths -= {self._on_disk(path) for _, path, _ in repairs}
                logger.info(
                    "Reference folder %s: repointed %d picture(s) PixlStash "
                    "itself had moved rather than deleting them.",
                    self._folder_path,
                    len(repairs),
                )
            if deferred:
                logger.info(
                    "Reference folder %s: %d vanished picture(s) have a move "
                    "PixlStash recorded and has not finished; keeping their "
                    "records until it settles.",
                    self._folder_path,
                    deferred,
                )
            if still_missing and settling:
                # After move matching and after the journal, so a rename is
                # still followed and our own move is still repaired: what is
                # deferred is only the delete. A file copied across
                # filesystems inside the root is a removal plus a young file,
                # and deleting the row now would lose the pairing the next scan
                # makes once the copy has settled.
                logger.info(
                    "Library root: %d indexed path(s) vanished while %d file(s) "
                    "are still settling; keeping their records until the next "
                    "scan.",
                    len(still_missing),
                    len(settling),
                )
                still_missing = []
            removed_ids = [pic.id for pic in still_missing]

            def delete_removed(session: Session, ids: list[int]) -> None:
                for pic_id in ids:
                    pic = session.get(Picture, pic_id)
                    if pic is not None:
                        session.delete(pic)
                session.commit()

            if removed_ids:
                self._db.run_task(delete_removed, removed_ids, priority=DBPriority.LOW)
                logger.info(
                    "Reference folder %s: removed %d stale picture records.",
                    self._folder_path,
                    len(removed_ids),
                )

        # --- Handle new files ---
        imported_picture_ids: list[int] = []
        if new_paths:
            pending_paths = sorted(new_paths)
            for i in range(0, len(pending_paths), _BUILD_CHUNK_SIZE):
                chunk_paths = pending_paths[i : i + _BUILD_CHUNK_SIZE]
                chunk_pictures = self._build_picture_chunk(chunk_paths, folder_id)
                if not chunk_pictures:
                    continue
                imported_picture_ids.extend(self._insert_pictures(chunk_pictures))

        if imported_picture_ids:
            logger.info(
                "Reference folder %s: indexed %d new pictures.",
                self._folder_path,
                len(imported_picture_ids),
            )

        # --- Handle sidecar changes (and exports) for existing pictures ---
        # For each picture we reconcile the tags sidecar and the description
        # sidecar independently in both directions:
        #   read  - an external file that appeared or changed is imported (cheap
        #           os.stat() gate so content is only read when mtime differs);
        #   write - when the folder syncs that type and a picture with content
        #           has no sidecar yet, the file is created on disk (export).
        # An empty sidecar is never created.
        tags_by_pic: dict[int, list[str]] = {}
        if sync_tags:
            tags_by_pic = self._fetch_folder_tags(folder_id)

        caption_updates: list[dict] = []
        # The root reconciles an ALREADY-INDEXED picture only once the owner
        # turned sync on: with it off, a stray .txt that appears beside a
        # managed picture is not a caption, and reading it as one would retag
        # the picture. On, it has a suffix to go by - the wizard's confirmed
        # convention, or one detected above - exactly as a reference folder
        # does. A picture's FIRST indexing is a different rule and reads the
        # sidecar beside it either way (`_build_picture` -> `attach_sidecars`),
        # the same as the local-import wizard: a file arriving with its caption
        # keeps it, and there are no tags yet to overwrite.
        sidecar_candidates = (
            ()
            if self._is_root and not (sync_tags or sync_descriptions)
            else existing_by_path.items()
        )
        for file_path, pic in sidecar_candidates:
            if file_path in removed_paths or pic.deleted:
                # Don't touch sidecar data for removed/scrapheap pictures.
                continue
            update: dict = {"pic_id": pic.id}
            self._reconcile_sidecar(
                update,
                file_path,
                SIDECAR_TYPE_TAGS,
                self._tags_suffix,
                stored_path=pic.tags_file,
                stored_mtime=pic.tags_file_mtime,
                sync=sync_tags,
                export_content=", ".join(tags_by_pic.get(pic.id, [])),
            )
            self._reconcile_sidecar(
                update,
                file_path,
                SIDECAR_TYPE_DESCRIPTION,
                self._description_suffix,
                stored_path=pic.description_file,
                stored_mtime=pic.description_file_mtime,
                sync=sync_descriptions,
                export_content=description_caption_content(pic.description),
            )
            if len(update) > 1:
                caption_updates.append(update)

        caption_updated_picture_ids: list[int] = []
        if caption_updates:

            def apply_caption_updates(
                session: Session,
                updates: list[dict],
            ) -> None:
                # A sidecar re-sync writes confirmed tags/description onto EXISTING
                # pictures; a picture frozen by a locked set is read-only, so skip
                # it (background task - skip-and-log rather than raising 423).
                locked = locked_picture_ids(session, [u["pic_id"] for u in updates])
                if locked:
                    logger.info(
                        "Reference-folder sync: skipping %d locked picture(s) %s",
                        len(locked),
                        sorted(locked),
                    )
                # An applied Tag row is an input to the scorer's anomaly penalty, and a
                # non-NULL cached smart score is never recomputed, so a sidecar tag edit
                # that adds or drops an anomaly tag would leave the score stale. Wrap the
                # rewrite the way the other tag mutations do, and commit inside the same
                # transaction as the invalidation.
                retagged_ids = [
                    u["pic_id"]
                    for u in updates
                    if "new_tags" in u and u["pic_id"] not in locked
                ]
                with invalidate_on_anomaly_change(
                    session,
                    retagged_ids,
                    context="reference-folder sidecar tag sync",
                ):
                    for u in updates:
                        if u["pic_id"] in locked:
                            continue
                        pic_db = session.get(Picture, u["pic_id"])
                        if pic_db is None:
                            continue
                        if "tags_file" in u:
                            pic_db.tags_file = u["tags_file"]
                            pic_db.tags_file_mtime = u["tags_file_mtime"]
                        if "description_file" in u:
                            pic_db.description_file = u["description_file"]
                            pic_db.description_file_mtime = u["description_file_mtime"]
                        if "new_description" in u:
                            # Presence, not truthiness: the key is only set after a
                            # successful read, and ``None`` there is the owner
                            # having emptied a sidecar this picture was tracking.
                            pic_db.description = u["new_description"]
                        session.add(pic_db)
                        if "new_tags" in u:
                            # Replace tags - an empty list means all tags were removed.
                            session.exec(
                                delete(Tag).where(Tag.picture_id == u["pic_id"])
                            )
                            tags = u["new_tags"]
                            if tags:
                                session.add_all(
                                    [Tag(picture_id=u["pic_id"], tag=t) for t in tags]
                                )
                            else:
                                session.add(
                                    Tag(
                                        picture_id=u["pic_id"], tag=TAG_PENDING_SENTINEL
                                    )
                                )
                    session.flush()
                session.commit()

            self._db.run_task(
                apply_caption_updates, caption_updates, priority=DBPriority.LOW
            )
            caption_updated_picture_ids = [u["pic_id"] for u in caption_updates]
            logger.info(
                "Reference folder %s: reconciled sidecar data for %d existing pictures.",
                self._folder_path,
                len(caption_updates),
            )

        # Clear the one-shot explicit-re-import flag now that a scan has
        # consumed it (same transaction as the status update). A mount_error
        # exit above does NOT clear it, so the explicit intent survives until a
        # real scan runs.
        self._set_status(
            ReferenceFolderStatus.ACTIVE,
            update_last_scanned=True,
            clear_pending_reimport=is_explicit_import,
        )
        return {
            "status": "active",
            "folder_id": folder_id,
            "new_count": len(imported_picture_ids),
            # What was actually deleted, not what merely vanished from the
            # listing: a repaired or deferred row is neither.
            "removed_count": len(removed_ids),
            "caption_updated_count": len(caption_updates),
            "caption_updated_picture_ids": caption_updated_picture_ids,
            "imported_picture_ids": imported_picture_ids,
            "moved_picture_ids": moved_picture_ids,
            "external_moved_picture_ids": external_moved_picture_ids,
            # v1.11 Phase 5: which of those were actually queued for
            # reconciliation review - empty whenever this root has no layout,
            # even though external_moved_picture_ids is not.
            "external_moves_queued_for_review": (
                external_moved_picture_ids if self._layout is not None else []
            ),
        }

    def _on_disk(self, stored: str) -> str:
        """The walked path for a stored ``Picture.file_path``, inverse of :meth:`_stored`."""
        if not self._is_root:
            return stored
        return os.path.normpath(os.path.join(self._resolved_path, stored))

    def _stored(self, path: str) -> str:
        """The value ``Picture.file_path`` holds for an on-disk *path*.

        Absolute for a reference folder, root-relative with ``/`` for the
        library root - the two conventions ``ImageUtils.get_thumbnail_path``
        and ``layout_move_service.stored_form`` already read.
        """
        if not self._is_root:
            return path
        return os.path.relpath(path, self._resolved_path).replace(os.sep, "/")

    def _fetch_folder_tags(self, folder_id: int) -> dict[int, list[str]]:
        """Return ``{picture_id: [tag, ...]}`` for this folder's pictures.

        Used only when the folder exports tags, so the export step knows what to
        write into newly-created sidecars.  Sentinel/placeholder tags are
        excluded.  A single join avoids the SQLite bound-variable limit.
        """

        def fetch(session: Session) -> dict[int, list[str]]:
            owner = (
                Picture.reference_folder_id.is_(None)
                if folder_id is None
                else Picture.reference_folder_id == folder_id
            )
            rows = session.exec(
                select(Tag.picture_id, Tag.tag)
                .join(Picture, Tag.picture_id == Picture.id)
                .where(owner)
            ).all()
            out: dict[int, list[str]] = {}
            for pic_id, tag in rows:
                if tag and not is_tag_sentinel(tag):
                    out.setdefault(pic_id, []).append(tag)
            return out

        return self._db.run_task(fetch, priority=DBPriority.LOW)

    def _reconcile_sidecar(
        self,
        update: dict,
        file_path: str,
        sidecar_type: str,
        suffix: str | None,
        *,
        stored_path: str | None,
        stored_mtime: float | None,
        sync: bool,
        export_content: str,
    ) -> None:
        """Reconcile one sidecar type for one picture, mutating *update* in place.

        Read direction: when the file exists and its (path, mtime) differs from
        what was last recorded, queue an import of its content.  Write direction:
        when *sync* is on, the file is missing, and *export_content* is non-empty,
        create the file on disk now and record its new path/mtime.  A vanished
        file only clears the stored reference (the database data is kept).

        The picture's *recorded* file wins while it exists: the configured
        suffix names the files this scan creates, not the ones the owner
        already had. Resolving by suffix first dropped a ``photo.txt``
        recorded at import under a ``_tags.txt`` setting and exported a
        second file beside it.

        The root reconciles per type, not per folder: with descriptions on and
        tags off, a Stable Diffusion prompt ``.txt`` beside a managed picture
        is not the tag set, and reading it as one replaces every tag the
        picture has. A reference folder keeps its own contract, where the
        folder's suffixes say what to read.

        A kind in `_disabled_kinds` is off for this scan whatever the folder
        is, the same skip: its detected suffix was refused, and probing the
        known conventions for it would find the file the refusal was about.
        A file that exists but will not open records the path with a ``None``
        mtime and imports nothing, so the next pass reads it again rather than
        treating the failed read as an up-to-date empty caption.
        """
        if sidecar_type in self._disabled_kinds:
            return
        if self._is_root and not sync:
            return
        is_tags = sidecar_type == SIDECAR_TYPE_TAGS
        path_key = "tags_file" if is_tags else "description_file"
        mtime_key = "tags_file_mtime" if is_tags else "description_file_mtime"

        current_path = recorded_sidecar(
            file_path, stored_path
        ) or resolve_typed_sidecar(file_path, sidecar_type, suffix)
        if current_path is not None:
            current_mtime = get_sidecar_mtime(current_path)
            if current_path != stored_path or current_mtime != stored_mtime:
                raw = read_caption_text(current_path)
                if raw is None:
                    # The file is there but would not open. Record it, leave the
                    # mtime unset and import nothing: stamping the mtime of a
                    # read that failed makes the next pass see "unchanged" and
                    # the owner's captions are then missed for good, while
                    # importing the empty result would clear what is stored.
                    # Only written when it differs, so a permanently unreadable
                    # file does not report a change on every pass.
                    if current_path != stored_path or stored_mtime is not None:
                        update[path_key] = current_path
                        update[mtime_key] = None
                    return
                update[path_key] = current_path
                update[mtime_key] = current_mtime
                content = raw.strip()
                # An empty file that this picture was not already tracking is
                # "nothing to import", not "delete the caption": a stray empty
                # `.txt` beside a picture is not the owner emptying their
                # sidecar, and `apply_caption_updates` would clear the whole
                # tag set and drop in the pending sentinel. A file the picture
                # already had may well be empty - that IS the owner clearing
                # it, for tags and descriptions alike.
                if not content and stored_path is None:
                    return
                if is_tags:
                    update["new_tags"] = parse_caption_tags(raw)
                else:
                    update["new_description"] = content or None
            return

        # No sidecar on disk. Drop a stale stored reference (keep the DB data).
        if stored_path is not None:
            update[path_key] = None
            update[mtime_key] = None

        # Export: create the file from the database when there is content to write.
        if sync and export_content:
            target = writeback_path(file_path, sidecar_type, suffix, None)
            if target is None:
                return
            new_mtime = write_sidecar(target, export_content)
            if new_mtime is not None:
                update[path_key] = target
                update[mtime_key] = new_mtime

    def _carry_thumbnail(self, old_path: str, new_path: str) -> bool:
        """Move a followed picture's thumbnail bitmap to its new path-derived name.

        Thumbnails live at ``sha256(file_path)`` under
        ``image_root/.pixlstash-thumbnails`` (``ImageUtils.get_thumbnail_path``),
        so a followed move renames the bitmap rather than re-rendering it.
        Nothing sweeps that directory by anything but a row's current
        ``file_path``, so a bitmap left at the old name would never be reachable
        and never be collected.

        Returns:
            ``True`` when the new path now has the bitmap, so the caller can keep
            the stored dimensions.  ``False`` when there was nothing to carry or
            the rename failed, in which case the caller blanks the dimensions and
            ``MissingThumbnailFinder`` renders a fresh one.
        """
        image_root = self._db.image_root
        old_thumb = ImageUtils.find_thumbnail(image_root, old_path)
        new_thumb = ImageUtils.get_thumbnail_path(image_root, new_path)
        if not old_thumb or not new_thumb or old_thumb == new_thumb:
            return bool(old_thumb and old_thumb == new_thumb)
        try:
            os.makedirs(os.path.dirname(new_thumb), exist_ok=True)
            os.replace(old_thumb, new_thumb)
            return True
        except OSError as exc:
            # Not fatal: the picture simply regenerates its thumbnail.  Say so,
            # because a bitmap stranded at the old name is never collected.
            logger.warning(
                "Reference folder %s: could not carry the thumbnail for the move "
                "%s -> %s (%s); it will be regenerated and %s may be left behind.",
                self._folder_path,
                old_path,
                new_path,
                exc,
                old_thumb,
            )
            return False

    def _match_moved_paths(
        self,
        existing_by_path: dict[str, Picture],
        new_paths: set[str],
        removed_paths: set[str],
    ) -> dict[str, str]:
        """Pair vanished indexed paths with new disk paths holding the same pixels.

        Args:
            existing_by_path: This folder's indexed pictures, keyed by file path.
            new_paths: Disk paths not yet indexed, after ledger filtering.
            removed_paths: Indexed paths no longer present on disk.

        Returns:
            ``{old_path: new_path}`` for each unambiguous move.  Empty when
            either side is empty, so the expensive case costs nothing: a first
            scan of a large folder has no removals and never hashes here, and a
            folder losing files without gaining any never hashes either.

        Only a 1:1 match on ``(pixel_sha, size_bytes)`` counts as a move, and
        only when no unchanged file in the folder shares that key either.
        Scrapheap rows are never the *source* of a move (a hidden soft-deleted
        row would otherwise swallow an unrelated new file of the same content)
        but do count as unchanged files blocking one, since their file is still
        on disk.  A present file whose ``pixel_sha`` has not been backfilled yet
        blocks every candidate of its own size, since it could be a copy of any
        of them.
        Identical pixels at several paths are genuine copies, and the rows
        behind them can differ in tags, sets and scores - pairing them by guess
        would move one picture's work onto another picture's file, which is the
        loss this exists to prevent.  Ambiguous groups fall through to the
        delete-and-re-add path, which is no worse than the behaviour before
        this existed.

        The confirmation stops at the size.  Import de-duplication follows a
        ``(sampled hash, size)`` candidate match with a full-byte hash of both
        sides; here one side is a file that no longer exists, so its bytes are
        unavailable and the stored columns are all there is to compare.

        Scoped to one reference folder, because the scan is: a file moved
        between two reference folders is a removal in one scan and an addition
        in another, with no shared pass to match them in.  Since the scan walks
        the whole tree under the root, moving between subfolders - the case
        this is for - is within scope.
        """
        if not removed_paths or not new_paths:
            return {}

        # ponytail: os.walk is not atomic, so a file moved mid-walk from an
        # unvisited directory into a visited one is in neither set and still
        # reads as a removal.  Deferring deletion by one scan (mark and sweep)
        # would close it; not worth the state for a race this narrow.
        # The key is ``(pixel_sha, size_bytes)``, never ``pixel_sha`` alone.
        # ``calculate_hash_from_file_path`` samples 8 x 8 KiB windows of
        # anything over 128 KiB and does not mix the size into the digest, so
        # on its own it is a candidate key and not an identity -- its own
        # docstring says so, and import de-duplication pairs it with the size
        # for exactly this reason.  Here a false pair is worse than the bug
        # being fixed: a lost row is visible, one picture's tags and
        # memberships silently rebound onto another picture's file are not.
        # Scrapheap rows are not move candidates.  ``fetch_existing`` loads
        # ``deleted=True`` pictures on purpose, so without this a hidden
        # scrapheap row whose file really was deleted would swallow an unrelated
        # new file of the same content: the arrival is taken out of
        # ``new_paths`` as "a move", and what the user gets is not a new picture
        # but a soft-deleted one they cannot see.  Leaving them in
        # ``removed_paths`` keeps the ordinary cleanup path unchanged.
        gone_by_key: dict[tuple[str, int | None], list[str]] = {}
        for path in removed_paths:
            picture = existing_by_path[path]
            if picture.pixel_sha and not picture.deleted:
                key = (picture.pixel_sha, picture.size_bytes)
                gone_by_key.setdefault(key, []).append(path)
        if not gone_by_key:
            return {}

        # An unchanged file sharing the key makes the group ambiguous too, and
        # the stable rows cost nothing to count: their hash is already loaded.
        # Without this, deleting A and separately adding an identical C while
        # an identical B sits untouched in the folder reads as a clean 1:1.
        #
        # Scrapheap rows *do* count here, unlike above.  Their file is still on
        # disk, so it is a real identical file the arrival could be a copy of;
        # counting it only ever refuses a match, which is the safe direction.
        stable_counts: dict[tuple[str, int | None], int] = {}
        # ``pixel_sha`` is nullable, so a present, unchanged file can be
        # invisible to the count above.  That is exactly the file whose
        # existence would have refused the match, so a NULL there is not "no
        # collision", it is "unknown", and every key of that file's SIZE is
        # refused.  Scoped to the size and not to the whole root on purpose:
        # ``MissingPixelShaFinder`` only backfills non-deleted rows, so one
        # scrapheap row with a NULL hash would otherwise turn every rename in
        # the library into a delete plus a re-import, for ever.
        unknown_sizes: set[int] = set()
        unknown_any = False
        for path, picture in existing_by_path.items():
            if path in removed_paths:
                continue
            if not picture.pixel_sha:
                size = picture.size_bytes
                if size is None:
                    try:
                        size = os.path.getsize(path)
                    except OSError as exc:
                        # Neither hash nor size: it could collide with
                        # anything, so nothing can be followed this pass.
                        logger.warning(
                            "Reference folder %s: %s has no pixel hash and could "
                            "not be sized (%s), so no move can be told from a "
                            "copy in this pass.",
                            self._folder_path,
                            path,
                            exc,
                        )
                        unknown_any = True
                        continue
                unknown_sizes.add(size)
                continue
            key = (picture.pixel_sha, picture.size_bytes)
            stable_counts[key] = stable_counts.get(key, 0) + 1

        arrived_by_key: dict[tuple[str, int | None], list[str]] = {}
        for path in sorted(new_paths):
            try:
                size_bytes = os.path.getsize(path)
                pixel_sha = ImageUtils.calculate_hash_from_file_path(path)
            except Exception as exc:
                # Not fatal: an unhashable file simply cannot be matched, and
                # _build_picture_chunk reports it again when it tries to import.
                logger.warning(
                    "Reference folder scan: failed to hash %s while looking for "
                    "moved files: %s",
                    path,
                    exc,
                )
                continue
            if pixel_sha:
                arrived_by_key.setdefault((pixel_sha, size_bytes), []).append(path)

        moved: dict[str, str] = {}
        for key, old_paths in gone_by_key.items():
            candidates = arrived_by_key.get(key, ())
            if not candidates:
                continue
            if unknown_any or key[1] in unknown_sizes:
                logger.info(
                    "Reference folder %s: an unchanged file of the same size has "
                    "no pixel hash yet, so this move cannot be told from a copy; "
                    "re-importing until the hash backfill catches up.",
                    self._folder_path,
                )
                continue
            stable = stable_counts.get(key, 0)
            if len(old_paths) == 1 and len(candidates) == 1 and stable == 0:
                moved[old_paths[0]] = candidates[0]
            else:
                logger.info(
                    "Reference folder %s: %d vanished, %d new and %d unchanged "
                    "file(s) share one pixel hash and size; too ambiguous to "
                    "call a move, re-importing.",
                    self._folder_path,
                    len(old_paths),
                    len(candidates),
                    stable,
                )
        return moved

    def _build_picture_chunk(
        self,
        file_paths: list[str],
        folder_id: int,
    ) -> list[Picture]:
        def _build(file_path: str) -> Picture | None:
            try:
                pixel_sha = ImageUtils.calculate_hash_from_file_path(file_path)
            except Exception as exc:
                logger.warning(
                    "Reference folder scan: failed to hash %s: %s", file_path, exc
                )
                return None

            try:
                return self._build_picture(file_path, pixel_sha, folder_id)
            except Exception as exc:
                logger.warning(
                    "Reference folder scan: failed to build picture for %s: %s",
                    file_path,
                    exc,
                )
                return None

        if not file_paths:
            return []

        max_workers = min(
            _MAX_BUILD_WORKERS,
            max(1, len(file_paths)),
            max(1, os.cpu_count() or 1),
        )
        if max_workers <= 1:
            return [
                pic for pic in (_build(path) for path in file_paths) if pic is not None
            ]

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            return [pic for pic in executor.map(_build, file_paths) if pic is not None]

    def _insert_pictures(self, pictures: list[Picture]) -> list[int]:
        def insert_pictures(
            session: Session, pictures_batch: list[Picture]
        ) -> list[int]:
            # Re-check inside the write transaction. The root scan and a
            # folder-structure commit into the same root both walk the disk,
            # compare against the table and insert, and the single writer is
            # the only place their check-then-insert cannot interleave. Whoever
            # got here first owns the row; the other's build is dropped.
            taken = set(
                session.exec(
                    select(Picture.file_path).where(
                        Picture.file_path.in_([p.file_path for p in pictures_batch])
                    )
                ).all()
            )
            if taken:
                logger.info(
                    "Reference folder %s: %d file(s) were indexed by another "
                    "writer while this scan built them; keeping theirs.",
                    self._folder_path,
                    len(taken),
                )
                pictures_batch = [p for p in pictures_batch if p.file_path not in taken]
                if not pictures_batch:
                    return []
            session.add_all(pictures_batch)
            session.commit()
            for pic in pictures_batch:
                session.refresh(pic)

            sidecar_tags_to_add = []
            sentinel_tags_to_add = []
            imported_ids: list[int] = []
            for pic in pictures_batch:
                if pic.id is not None:
                    imported_ids.append(int(pic.id))
                sidecar_tags = getattr(pic, "_sidecar_tags", None)
                if sidecar_tags and pic.id is not None:
                    for tag_str in sidecar_tags:
                        sidecar_tags_to_add.append(Tag(picture_id=pic.id, tag=tag_str))
                elif pic.id is not None:
                    sentinel_tags_to_add.append(
                        Tag(picture_id=pic.id, tag=TAG_PENDING_SENTINEL)
                    )

            if sidecar_tags_to_add or sentinel_tags_to_add:
                session.add_all(sidecar_tags_to_add + sentinel_tags_to_add)
                session.commit()
            return imported_ids

        return self._db.run_task(insert_pictures, pictures, priority=DBPriority.MEDIUM)

    def _build_picture(self, file_path: str, pixel_sha: str, folder_id: int) -> Picture:
        """Read image metadata and build a Picture for a reference folder file.

        Args:
            file_path: Absolute path to the source image file.
            pixel_sha: Pre-computed pixel hash of the file.
            folder_id: Primary key of the owning ReferenceFolder.

        Returns:
            An unsaved Picture instance ready for insertion.
        """
        with open(file_path, "rb") as fh:
            image_bytes = fh.read()

        created_at = ImageUtils.extract_created_at_from_metadata(
            image_bytes, fallback_file_path=file_path
        )

        width = height = None
        img_format = None
        thumbnail_bytes = None
        # AR-bitmap dims + faceless square crop; faces refine the crop later.
        thumb_cols: dict = {}

        # A video is not PIL's to open; the thumbnail finder renders its frame.
        if not VideoUtils.is_video_file(file_path):
            try:
                with Image.open(io.BytesIO(image_bytes)) as img:
                    img_format = img.format or "PNG"
                    width, height = img.size
                    rendered = ImageUtils.render_thumbnail(img)
                    if rendered is not None:
                        thumbnail_bytes, bmp_w, bmp_h, crop = rendered
                        thumb_cols = {
                            "thumbnail_width": bmp_w,
                            "thumbnail_height": bmp_h,
                            "square_crop_x": crop["x"],
                            "square_crop_y": crop["y"],
                            "square_crop_side": crop["side"],
                        }
            except Exception as exc:
                logger.warning(
                    "Reference scan: could not decode %s for a thumbnail (%s: %s); "
                    "the picture is indexed without one for now.",
                    file_path,
                    type(exc).__name__,
                    exc,
                )

        # The thumbnail goes to image_root/.pixlstash-thumbnails/, never
        # inside the reference folder where the next scan would index it.
        if thumbnail_bytes:
            ImageUtils.write_thumbnail_bytes(
                self._db.image_root, self._stored(file_path), thumbnail_bytes
            )

        size_bytes = os.path.getsize(file_path)

        pic = Picture(
            file_path=self._stored(file_path),
            reference_folder_id=folder_id,
            pixel_sha=pixel_sha,
            format=img_format,
            width=width,
            height=height,
            size_bytes=size_bytes,
            imported_at=datetime.now(timezone.utc),
            original_file_name=os.path.basename(file_path),
            is_video=VideoUtils.is_video_file(file_path),
            **thumb_cols,
        )
        if created_at:
            pic.created_at = created_at

        # Tags are stored via the Tag relationship and cannot be set on the
        # unsaved Picture directly; `attach_sidecars` stashes them as a
        # transient attribute so `_insert_pictures` can persist them once the
        # Picture has an id.
        attach_sidecars(
            pic,
            file_path,
            self._read_suffixes(SIDECAR_TYPE_TAGS, self._tags_suffix),
            self._read_suffixes(SIDECAR_TYPE_DESCRIPTION, self._description_suffix),
        )

        return pic

    def _read_suffixes(self, sidecar_type: str, suffix: str | None) -> list[str] | None:
        """What `attach_sidecars` may read *sidecar_type* at, for this scan.

        ``[]`` reads nothing - the kind is disabled (`_disabled_kinds`), so a
        picture indexed by this scan records no file of it. ``None`` probes the
        known conventions, which is what an unset suffix has always meant.
        """
        if sidecar_type in self._disabled_kinds:
            return []
        return [suffix] if suffix else None

    def _persist_suffixes(self, suffixes: dict[str, str]) -> dict[str, str | None]:
        """Store auto-detected sidecar suffixes on the folder (only fills NULLs).

        A detected suffix is written straight into the folder's configuration
        and is thereafter appended to image stems to build sidecar paths, so it
        must clear the same bar as a suffix supplied through the API. Validate
        here too: this is the second door into that column, and skipping the
        check would let the scan persist a value the API would have rejected.

        Returns:
            The folder's EFFECTIVE suffix per kind once the write has run -
            what is stored on the row, or ``None`` when nothing is. Read from
            the row inside the write task, so a suffix the owner stored
            between `fetch_folder_config()` and this write wins over the
            detected one instead of being silently ignored: the column is no
            longer NULL, nothing is persisted, and the caller must reconcile
            with the stored value rather than the detection it asked about. A
            ``None`` means the detection was refused and nothing else is
            stored, so the caller must not keep using it for this scan either.
        """

        def _accepted(key: str) -> str | None:
            value = suffixes.get(key)
            if not value:
                return None
            if not is_safe_sidecar_suffix(value):
                logger.warning(
                    "Refusing to persist unsafe detected %s %r for folder %s; "
                    "leaving it unset so the module default is used.",
                    key,
                    value,
                    self._folder_id,
                )
                return None
            return value

        tags_suffix = _accepted("tags_suffix")
        description_suffix = _accepted("description_suffix")

        def update(session: Session) -> dict[str, str | None]:
            rf = (
                session.exec(select(LibrarySettings)).first()
                if self._is_root
                else session.get(ReferenceFolder, self._folder_id)
            )
            if rf is None:
                # No row to merge with, so the validated detections are all the
                # scan has to go on.
                return {
                    "tags_suffix": tags_suffix,
                    "description_suffix": description_suffix,
                }
            # Judged on the merged row: a detected suffix that would name the
            # same file as the other kind's effective one is not persisted, or
            # the two write-backs would overwrite each other. Descriptions see
            # whatever tags just persisted.
            if tags_suffix and rf.tags_suffix is None:
                if suffixes_collide(tags_suffix, rf.description_suffix):
                    logger.warning(
                        "Not persisting detected tags suffix %r for folder %s: "
                        "it would name the same file as description suffix %r.",
                        tags_suffix,
                        self._folder_id,
                        rf.description_suffix or DEFAULT_DESCRIPTION_SUFFIX,
                    )
                else:
                    rf.tags_suffix = tags_suffix
            if description_suffix and rf.description_suffix is None:
                if suffixes_collide(rf.tags_suffix, description_suffix):
                    logger.warning(
                        "Not persisting detected description suffix %r for "
                        "folder %s: it would name the same file as tags "
                        "suffix %r.",
                        description_suffix,
                        self._folder_id,
                        rf.tags_suffix or DEFAULT_TAGS_SUFFIX,
                    )
                else:
                    rf.description_suffix = description_suffix
            session.add(rf)
            session.commit()
            return {
                "tags_suffix": rf.tags_suffix,
                "description_suffix": rf.description_suffix,
            }

        return self._db.run_task(update, priority=DBPriority.LOW)

    def _set_status(
        self,
        status: str,
        *,
        update_last_scanned: bool = False,
        clear_pending_reimport: bool = False,
    ) -> None:
        if self._is_root:
            # No row to stamp; the finder keeps the root's schedule. A finished
            # scan (not a mount_error exit) is what the purge sweep waits for.
            if status == ReferenceFolderStatus.ACTIVE and self._on_root_scanned:
                self._on_root_scanned()
            return

        def update(session: Session) -> None:
            rf = session.get(ReferenceFolder, self._folder_id)
            if rf is None:
                return
            rf.status = status
            if update_last_scanned:
                rf.last_scanned = time.time()
            if clear_pending_reimport:
                rf.pending_reimport = False
            session.add(rf)
            session.commit()

        self._db.run_task(update, priority=DBPriority.LOW)
