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
from pixlstash.db_models.picture import Picture
from pixlstash.db_models.reference_folder import ReferenceFolder, ReferenceFolderStatus
from pixlstash.db_models.tag import Tag, TAG_PENDING_SENTINEL, is_tag_sentinel
from pixlstash.services.set_lock_service import locked_picture_ids
from pixlstash.tasks.base_task import BaseTask
from pixlstash.utils.caption_file_utils import (
    DEFAULT_DESCRIPTION_SUFFIX,
    DEFAULT_TAGS_SUFFIX,
    SIDECAR_TYPE_DESCRIPTION,
    SIDECAR_TYPE_TAGS,
    detect_folder_suffixes,
    get_sidecar_mtime,
    is_safe_sidecar_suffix,
    read_description_sidecar,
    read_tags_sidecar,
    resolve_typed_sidecar,
    write_sidecar,
    writeback_path,
)
from pixlstash.utils.image_processing.image_utils import ImageUtils, THUMBNAIL_EXTENSION
from pixlstash.utils.image_processing.video_utils import VideoUtils
from pixlstash.utils.media_files import is_supported_media_file
from pixlstash.pixl_logging import get_logger
from pixlstash.services.layout_move_service import (
    claim_own_moves,
    prune_move_journal,
)
from pixlstash.services.move_reconciliation_service import record_pending_reviews
from pixlstash.services.views_service import MARKER_NAME as VIEWS_MARKER_NAME
from pixlstash.utils.library_layout import DEFAULT_LAYOUT, parse_layout
from pixlstash.utils.path_utils import path_is_within

logger = get_logger(__name__)

_BUILD_CHUNK_SIZE = 128
_MAX_BUILD_WORKERS = 8


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
    """

    def __init__(
        self,
        database,
        folder_id: int,
        folder_path: str,
        resolved_path: str,
        other_resolved_paths: frozenset[str] = frozenset(),
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
        # Sidecar filename suffixes for this folder, loaded at the start of
        # _run_task(); None means "use known conventions / module defaults".
        self._tags_suffix: str | None = None
        self._description_suffix: str | None = None
        # The folder's layout, loaded at the start of _run_task(); None means
        # "no layout", same as an unset column (v1.11 Phase 5).
        self._layout = None

    def _run_task(self):
        resolved = self._resolved_path
        folder_id = self._folder_id

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
            detected = detect_folder_suffixes(resolved)
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
                self._persist_suffixes(seed)

        # Collect all supported files currently on disk.
        # Skip PixlStash-generated thumbnail files (e.g. foo_thumb.webp) that
        # may have been written next to source files by an older version - they
        # are not real pictures and would cause infinite re-indexing churn.
        _thumb_suffix = f"_thumb{THUMBNAIL_EXTENSION}"
        other_roots = self._other_resolved_paths
        disk_paths: set[str] = set()
        views_roots: list[str] = []
        for root, dirs, files in os.walk(resolved, topdown=True):
            # Prune subdirectories that are roots of other reference folders so
            # their files are only indexed by their own scan task.
            dirs[:] = [d for d in dirs if os.path.join(root, d) not in other_roots]
            # Prune a PixlStash Views tree. Every file under it is a link to a
            # picture indexed somewhere else already, and os.walk lists a
            # symlinked *file* in ``files`` -- only symlinked directories are
            # skipped by default -- so without this each picture would be
            # indexed a second time under its view path. views_service refuses
            # to publish inside a reference folder, but a folder can be
            # registered as one after a tree was published there.
            #
            # The root is REMEMBERED, not merely skipped, because "absent from
            # disk_paths" is what this task hard-deletes a Picture row for --
            # tags, scores, memberships and all. Pruning alone would turn a
            # marker file appearing over an indexed folder into a silent
            # library deletion, which is a far worse failure than the double
            # indexing this prune exists to prevent.
            if VIEWS_MARKER_NAME in files:
                dirs[:] = []
                views_roots.append(root)
                continue
            for file_name in files:
                if file_name.endswith(_thumb_suffix):
                    continue
                full_path = os.path.join(root, file_name)
                if _is_supported_file(full_path):
                    disk_paths.add(full_path)

        # Fetch all picture paths already indexed for this reference folder,
        # including scrapheap (deleted=True) pictures.  Scrapheap pictures must
        # be present in existing_by_path so their file paths are subtracted from
        # `new_paths`; without them, the scan would re-import the same file every
        # time it ran while the picture sat in the scrapheap.
        def fetch_existing(session: Session) -> list[Picture]:
            return list(
                session.exec(
                    select(Picture).where(
                        Picture.reference_folder_id == folder_id,
                    )
                ).all()
            )

        existing_pictures: list[Picture] = self._db.run_task(
            fetch_existing, priority=DBPriority.LOW
        )
        existing_by_path: dict[str, Picture] = {
            p.file_path: p for p in existing_pictures if p.file_path
        }

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
        candidate_new = disk_paths - set(existing_by_path.keys())

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
                DeletedFileLog.hash_path(p) for p in candidate_new
            } & deleted_path_shas
        else:
            new_paths = {
                p
                for p in candidate_new
                if DeletedFileLog.hash_path(p) not in deleted_path_shas
            }
            override_path_shas = set()
        removed_paths = set(existing_by_path.keys()) - disk_paths
        if views_roots:
            # A path under a pruned views tree was not looked for, so its
            # absence from disk_paths says nothing about whether the file is
            # there. Deleting its row would be acting on a question never asked.
            skipped = {
                path
                for path in removed_paths
                if any(path_is_within(path, views_root) for views_root in views_roots)
            }
            if skipped:
                logger.info(
                    "Reference folder %s: %d indexed pictures lie under a "
                    "PixlStash Views tree and were not scanned, so their "
                    "records are kept rather than removed.",
                    self._folder_path,
                    len(skipped),
                )
                removed_paths -= skipped

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
                old: self._carry_thumbnail(old, new) for old, new in moved_paths.items()
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
                    [(old_path, new_path) for _, new_path, _, old_path in pairs],
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
                    pic.file_path = new_path
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
                    if (old_path, new_path) not in ours:
                        external.append(pic_id)
                        external_moves.append((pic_id, old_path, new_path))
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
        if removed_paths:
            removed_ids = [
                existing_by_path[p].id
                for p in removed_paths
                if existing_by_path[p].id is not None
            ]

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
        for file_path, pic in existing_by_path.items():
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
                export_content=(pic.description or "").strip(),
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
                    if u.get("new_description") is not None:
                        pic_db.description = u["new_description"]
                    session.add(pic_db)
                    if "new_tags" in u:
                        # Replace tags - an empty list means all tags were removed.
                        session.exec(delete(Tag).where(Tag.picture_id == u["pic_id"]))
                        tags = u["new_tags"]
                        if tags:
                            session.add_all(
                                [Tag(picture_id=u["pic_id"], tag=t) for t in tags]
                            )
                        else:
                            session.add(
                                Tag(picture_id=u["pic_id"], tag=TAG_PENDING_SENTINEL)
                            )
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
            "removed_count": len(removed_paths),
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

    def _fetch_folder_tags(self, folder_id: int) -> dict[int, list[str]]:
        """Return ``{picture_id: [tag, ...]}`` for this folder's pictures.

        Used only when the folder exports tags, so the export step knows what to
        write into newly-created sidecars.  Sentinel/placeholder tags are
        excluded.  A single join avoids the SQLite bound-variable limit.
        """

        def fetch(session: Session) -> dict[int, list[str]]:
            rows = session.exec(
                select(Tag.picture_id, Tag.tag)
                .join(Picture, Tag.picture_id == Picture.id)
                .where(Picture.reference_folder_id == folder_id)
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
        """
        is_tags = sidecar_type == SIDECAR_TYPE_TAGS
        path_key = "tags_file" if is_tags else "description_file"
        mtime_key = "tags_file_mtime" if is_tags else "description_file_mtime"

        current_path = resolve_typed_sidecar(file_path, sidecar_type, suffix)
        if current_path is not None:
            current_mtime = get_sidecar_mtime(current_path)
            if current_path != stored_path or current_mtime != stored_mtime:
                update[path_key] = current_path
                update[mtime_key] = current_mtime
                if is_tags:
                    update["new_tags"] = read_tags_sidecar(current_path)
                else:
                    update["new_description"] = read_description_sidecar(current_path)
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
        makes the whole folder unmatchable rather than merely uncounted.
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
        unhashed_stable = 0
        for path, picture in existing_by_path.items():
            if path in removed_paths:
                continue
            if not picture.pixel_sha:
                unhashed_stable += 1
                continue
            key = (picture.pixel_sha, picture.size_bytes)
            stable_counts[key] = stable_counts.get(key, 0) + 1

        # ``pixel_sha`` is nullable and MissingPixelShaFinder backfills it in the
        # background, so a present, unchanged file can be invisible to the count
        # above.  That is exactly the file whose existence would have refused the
        # match, so a NULL there is not "no collision", it is "unknown".  Refuse
        # to follow anything until the backfill has caught up; this scan writes
        # the hash for every file it imports, so the gap is transient, and the
        # fallback is the delete-and-re-add that ran before this existed.
        if unhashed_stable:
            logger.info(
                "Reference folder %s: %d unchanged file(s) have no pixel hash "
                "yet, so a move cannot be told from a copy; re-importing until "
                "the hash backfill catches up.",
                self._folder_path,
                unhashed_stable,
            )
            return {}

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
                self._db.image_root, file_path, thumbnail_bytes
            )

        size_bytes = os.path.getsize(file_path)

        pic = Picture(
            file_path=file_path,
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

        # Detect and read the tags and description sidecars independently, using
        # the folder's configured suffixes (falling back to known conventions).
        tags_path = resolve_typed_sidecar(
            file_path, SIDECAR_TYPE_TAGS, self._tags_suffix
        )
        if tags_path:
            pic.tags_file = tags_path
            pic.tags_file_mtime = get_sidecar_mtime(tags_path)
            sidecar_tags = read_tags_sidecar(tags_path)
            # Tags are stored via the Tag relationship and cannot be set on the
            # unsaved Picture directly; stash them as a transient attribute so
            # the caller can persist them after the Picture is inserted.
            if sidecar_tags:
                pic._sidecar_tags = sidecar_tags  # type: ignore[attr-defined]

        description_path = resolve_typed_sidecar(
            file_path, SIDECAR_TYPE_DESCRIPTION, self._description_suffix
        )
        if description_path:
            pic.description_file = description_path
            pic.description_file_mtime = get_sidecar_mtime(description_path)
            sidecar_description = read_description_sidecar(description_path)
            if sidecar_description and not pic.description:
                pic.description = sidecar_description

        return pic

    def _persist_suffixes(self, suffixes: dict[str, str]) -> None:
        """Store auto-detected sidecar suffixes on the folder (only fills NULLs).

        A detected suffix is written straight into the folder's configuration
        and is thereafter appended to image stems to build sidecar paths, so it
        must clear the same bar as a suffix supplied through the API. Validate
        here too: this is the second door into that column, and skipping the
        check would let the scan persist a value the API would have rejected.
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
        if tags_suffix is None and description_suffix is None:
            return

        def update(session: Session) -> None:
            rf = session.get(ReferenceFolder, self._folder_id)
            if rf is None:
                return
            if tags_suffix and rf.tags_suffix is None:
                rf.tags_suffix = tags_suffix
            if description_suffix and rf.description_suffix is None:
                rf.description_suffix = description_suffix
            session.add(rf)
            session.commit()

        self._db.run_task(update, priority=DBPriority.LOW)

    def _set_status(
        self,
        status: str,
        *,
        update_last_scanned: bool = False,
        clear_pending_reimport: bool = False,
    ) -> None:
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
