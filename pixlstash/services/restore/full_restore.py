"""Full-database restore: safety snapshot, DB swap, planner pause.

Replaces the live database with a snapshot: opportunistic safety snapshot,
schema upgrade, missing-file / permanent-deletion scan, a serialised DB file
swap routed through the writer queue, and the post-swap cleanup that replays
live state (likeness pipeline, snapshot index, deletion ledger) into the
swapped-in database.  The planner-pause / DB-swap / cleanup ordering here is
load-bearing and preserved verbatim.
"""

import os
import shutil
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, select
from sqlalchemy import (
    delete as sa_delete,
    select as sa_select,
    update as sa_update,
)

from pixlstash.database import create_configured_engine
from pixlstash.db_models import (
    DeletedFileLog,
    Face,
    GuestScore,
    GuestSession,
    Picture,
    PictureProjectMember,
    PictureSetMember,
    Tag,
    UserToken,
)
from pixlstash.db_models.picture_likeness import (
    PictureLikenessFrontier,
    PictureLikenessQueue,
)
from pixlstash.db_models.snapshot import Snapshot
from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.path_utils import resolve_path_within

from ._models import (
    RestoreInProgressError,
    RestoreReport,
    SafetySnapshotFailedError,
    _MAX_MISSING_RATIO_FOR_CLEANUP,
    _MIN_PICTURES_FOR_MISSING_RATIO_CHECK,
)
from .schema_upgrade import snapshot_engine

logger = get_logger(__name__)


class FullRestoreMixin:
    """Full-database restore behaviour.

    Mixed into :class:`~pixlstash.services.restore.RestoreService`.
    """

    def restore_full(
        self,
        snapshot_id: int,
        dry_run: bool = False,
        allow_without_safety: bool = False,
        restore_request_lease: int | None = None,
    ) -> RestoreReport:
        """Replace the live database with a snapshot snapshot.

        Steps:
        1. Take an OPPORTUNISTIC safety snapshot of the current state.
        2. Upgrade the snapshot schema to the current Alembic head.
        3. Scan snapshot Picture rows; collect missing-file IDs.
        4. Dispose the live engine, copy the snapshot over the live DB, and
           re-open it.
        5. Clear every API token (see ``_clear_hub_api_tokens``) and the vault's
           guest state (see ``_clear_guest_state``) - a restore always leaves the
           vault with no tokens, whatever the snapshot held.
        6. Delete rows whose files are missing and perform post-swap cleanup.
        7. Reset the in-memory authentication state the swap invalidated (see
           ``_reset_auth_state``); clients sign in again afterwards.
        8. Reopen authentication only after every finalisation step succeeds.
        9. Resume the TaskRunner and emit ``RESTORE_COMPLETED``.

        Args:
            snapshot_id: ID of the snapshot to restore.
            dry_run: If True, perform all steps except the actual DB swap and
                return a report without modifying the live database.
            allow_without_safety: If True, proceed even when the safety
                snapshot in step 1 fails. The safety snapshot is the only
                rollback if the restore breaks something, so the default is
                to abort on failure. Set this only when the user has
                explicitly acknowledged that there will be no rollback (e.g.
                disk is full and they want to restore *because* the live DB
                is broken).
            restore_request_lease: Admission lease held by an authenticated
                HTTP restore request. The barrier excludes it from the drain
                after closing new admissions, avoiding a self-deadlock. Direct
                service callers leave this as ``None``.

        Returns:
            A ``RestoreReport`` summarising the operation.

        Raises:
            ValueError: If the snapshot is not found or the snapshot file is
                missing from disk.
            SafetySnapshotFailedError: If the safety snapshot fails and
                ``allow_without_safety`` is False.
        """
        if not self._restore_lock.acquire(blocking=False):
            raise RestoreInProgressError(
                "Another restore operation is already in progress; "
                "see GET /snapshots/status."
            )
        try:
            vault_root = self._vault.image_root
            report = RestoreReport(
                snapshot_id=snapshot_id,
                resource_type="full",
            )

            self._active_job = {
                "kind": "RESTORE",
                "snapshot_id": snapshot_id,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "progress": 0.0,
            }
            try:
                return self._restore_full_inner(
                    snapshot_id,
                    dry_run,
                    vault_root,
                    report,
                    allow_without_safety,
                    restore_request_lease,
                )
            finally:
                self._active_job = None
        finally:
            self._restore_lock.release()

    def _restore_full_inner(
        self,
        snapshot_id: int,
        dry_run: bool,
        vault_root: str,
        report: "RestoreReport",
        allow_without_safety: bool,
        restore_request_lease: int | None,
    ) -> "RestoreReport":
        """Inner implementation of full restore (called from restore_full).

        Args:
            snapshot_id: Snapshot ID.
            dry_run: If True skip DB swap.
            vault_root: Vault root path.
            report: Pre-constructed RestoreReport to populate.

        Returns:
            Populated RestoreReport.
        """
        cp = self._get_snapshot_or_raise(snapshot_id)
        abs_snapshot = resolve_path_within(vault_root, cp.relative_path)
        if not os.path.exists(abs_snapshot):
            raise ValueError(f"Snapshot file not found on disk: {abs_snapshot}")

        # Emit STARTED only AFTER the snapshot is known to exist (and the
        # lock was already acquired in the outer ``restore_full``) - a 404
        # or 409 must not leave the UI with ``activeJob`` set forever.
        # Anything that throws or early-returns from here on emits FAILED
        # so the frontend can clear ``activeJob``.
        started_payload = {"snapshot_id": snapshot_id, "resource_type": "full"}
        self._emit_lifecycle(EventType.RESTORE_STARTED, started_payload)
        try:
            return self._restore_full_steps(
                snapshot_id,
                dry_run,
                vault_root,
                report,
                allow_without_safety,
                abs_snapshot,
                restore_request_lease,
            )
        except Exception as exc:
            self._emit_lifecycle(
                EventType.RESTORE_FAILED,
                {**started_payload, "error": str(exc)},
            )
            raise

    def _restore_full_steps(
        self,
        snapshot_id: int,
        dry_run: bool,
        vault_root: str,
        report: "RestoreReport",
        allow_without_safety: bool,
        abs_snapshot: str,
        restore_request_lease: int | None,
    ) -> "RestoreReport":
        """The body of full restore steps 1-9 (separated so the lifecycle
        wrapper in ``_restore_full_inner`` stays narrow)."""
        db = self._vault.db

        # 1. Safety snapshot of current state.
        # The safety snapshot is the user's ONLY rollback if the restore
        # breaks something. Failing silently here trades a working DB for
        # an irrecoverable overwrite.  By default we abort; callers that
        # know what they are doing (e.g. live DB is already broken and the
        # whole point IS to overwrite it) can pass allow_without_safety.
        try:
            self._vault.snapshot_service.create_snapshot("OPPORTUNISTIC")
        except Exception as exc:
            if not allow_without_safety:
                msg = (
                    f"Safety snapshot failed and allow_without_safety=False; "
                    f"refusing to proceed without a rollback point: {exc}"
                )
                logger.error("RestoreService: %s", msg, exc_info=True)
                raise SafetySnapshotFailedError(msg) from exc
            logger.warning(
                "RestoreService: safety snapshot failed; proceeding because "
                "allow_without_safety=True (no rollback will be available): %s",
                exc,
            )

        # 2. Upgrade snapshot schema to head.
        upgraded_snapshot = self._upgrade_snapshot_schema(abs_snapshot)
        if upgraded_snapshot is None:
            report.errors.append("Schema upgrade failed; aborting restore.")
            raise RuntimeError("Schema upgrade failed; aborting restore.")

        # 3. Find missing-file Picture IDs from the snapshot.
        # Raises if vault_root is unreachable (transient mount failure).
        missing_ids, total_pictures = self._find_missing_file_ids(
            upgraded_snapshot, vault_root
        )
        report.missing_files_count = len(missing_ids)
        if missing_ids:
            logger.info(
                "RestoreService: %d picture file(s) missing on disk; "
                "those rows will be dropped after restore.",
                len(missing_ids),
            )

        # 3a. Cross-check the snapshot against the permanent-deletion ledger.
        #     Pictures whose file path or content hash is recorded in
        #     deleted_file_log were intentionally, permanently deleted: they
        #     must never be resurrected (even if the file somehow lingers on
        #     disk), and they must not count toward the missing-file ratio
        #     below - an intentional purge is not a mount failure.
        path_shas, pixel_shas = self._load_deleted_file_index()
        deleted_ids = self._find_permanently_deleted_ids(
            upgraded_snapshot, path_shas, pixel_shas
        )
        report.permanently_deleted_count = len(deleted_ids)
        if deleted_ids:
            logger.info(
                "RestoreService: %d snapshot picture(s) match the "
                "permanent-deletion ledger; they will not be restored.",
                len(deleted_ids),
            )

        # Safety check: if a suspiciously large fraction of files are
        # flagged as missing, this is almost certainly a partial-mount
        # failure rather than legitimate deletions. Refuse to wipe
        # metadata for that many pictures unless the caller has
        # explicitly opted in to "I know my data looks wrong, proceed".
        # Known permanent deletions are excluded - they are expected to be
        # gone, so they never read as a mount failure.
        # Skipped when the snapshot is too small for the ratio to mean
        # anything (a one-picture snapshot whose file the user deleted
        # is legitimately 100% missing).
        suspicious_missing = set(missing_ids) - deleted_ids
        if (
            total_pictures >= _MIN_PICTURES_FOR_MISSING_RATIO_CHECK
            and len(suspicious_missing) / total_pictures
            > _MAX_MISSING_RATIO_FOR_CLEANUP
            and not allow_without_safety
        ):
            ratio_pct = 100.0 * len(suspicious_missing) / total_pictures
            raise RuntimeError(
                f"{len(suspicious_missing)} of {total_pictures} pictures "
                f"({ratio_pct:.0f}%) are missing on disk - refusing to "
                "overwrite the live DB and drop metadata for that many "
                "pictures, this looks like a network mount issue. If you "
                "really did delete that many files, pass "
                "allow_without_safety=True."
            )

        if dry_run:
            logger.info("RestoreService: dry_run=True - skipping DB swap.")
            shutil.rmtree(os.path.dirname(upgraded_snapshot), ignore_errors=True)
            return report

        # 3b. Capture the live likeness pipeline state BEFORE the swap so
        #     the file swap doesn't reset progress-tracking to whatever
        #     was in the (now-stripped) snapshot file. The snapshot
        #     deliberately ships these tables empty; we replay the live
        #     state into the swapped DB during cleanup, reconciled with
        #     the post-restore picture set.
        live_likeness_queue = self._vault.db.run_immediate_read_task(
            lambda s: [
                (r.picture_id, r.queued_at)
                for r in s.exec(select(PictureLikenessQueue)).all()
            ]
        )
        live_likeness_frontier = self._vault.db.run_immediate_read_task(
            lambda s: [
                (r.picture_id_a, r.j_max)
                for r in s.exec(select(PictureLikenessFrontier)).all()
            ]
        )

        # 3c. Capture the snapshot index BEFORE the swap. The ``Snapshot`` table
        #     lives inside the live DB, so the file swap rolls it back to
        #     whatever snapshots existed when the target snapshot was taken -
        #     every newer snapshot (and the OPPORTUNISTIC safety snapshot taken
        #     in step 1) would vanish from the list even though their .sqlite +
        #     .manifest.json files are untouched on disk. We replay the missing
        #     rows into the swapped DB during cleanup so restoring an older
        #     snapshot never hides newer restore points - the user can always
        #     roll forward again. The ``id`` is deliberately not captured: rows
        #     are re-inserted with fresh autoincrement ids to avoid colliding
        #     with the restored DB's own snapshot ids.
        live_snapshots = self._vault.db.run_immediate_read_task(
            lambda s: [
                {
                    "kind": r.kind,
                    "created_at": r.created_at,
                    "relative_path": r.relative_path,
                    "manifest_relative_path": r.manifest_relative_path,
                    "byte_size": r.byte_size,
                    "picture_count": r.picture_count,
                    "schema_version": r.schema_version,
                    "label": r.label,
                }
                for r in s.exec(select(Snapshot)).all()
            ]
        )

        # 3d. Capture the permanent-deletion ledger BEFORE the swap. Like the
        #     Snapshot index, deleted_file_log lives inside the live DB, so the
        #     file swap rolls it back to its snapshot-era contents - losing the
        #     record of every file deleted since. We replay the live rows into
        #     the swapped DB during cleanup so "what has been permanently
        #     deleted (and is therefore unrestorable)" survives the restore.
        live_deleted_log = self._vault.db.run_immediate_read_task(
            lambda s: [
                {
                    "path_sha": r.path_sha,
                    "pixel_sha": r.pixel_sha,
                    "deleted_at": r.deleted_at,
                    "file_removed": r.file_removed,
                }
                for r in s.exec(select(DeletedFileLog)).all()
            ]
        )

        # Rows to drop after the swap: files missing on disk PLUS any picture
        # matched against the permanent-deletion ledger (the latter may still
        # be on disk, so it would otherwise survive the swap).
        drop_ids = sorted(set(missing_ids) | deleted_ids)

        # 4. Pause background work, then route the DB swap through the writer
        #    queue so it is serialised with all other DB operations and no
        #    competing connection can hold a lock during the file swap.
        live_db_path = db._db_path
        planner = self._vault._work_planner
        task_runner = self._vault._task_runner

        if planner is not None:
            planner.stop()
            logger.info("RestoreService: WorkPlanner stopped for full restore.")

        if task_runner is not None:
            cancelled = task_runner.cancel_pending_tasks()
            if cancelled:
                logger.info(
                    "RestoreService: cancelled %d pending background task(s).",
                    cancelled,
                )

        # 3e. Capture the RESOLVED paths of every LIVE, non-deleted picture. These
        #     are the files currently in active use - including content that was
        #     (re-)added AFTER the target snapshot. If the snapshot holds a
        #     scrapheap (``deleted=True``) row that resolves to one of these
        #     files, the swap would RESURRECT that stale ghost on top of a file
        #     the user is actively using: the row's real content was overwritten
        #     after the snapshot, so the entry is unrestorable, but emptying the
        #     scrapheap would then hard-delete the live file (data loss - the file
        #     was legitimately added after the snapshot and is NOT in
        #     deleted_file_log). ``_post_restore_cleanup`` drops such ghosts
        #     (keeping the file). deleted_file_log only guards intentionally-
        #     purged content; this closes the sibling gap for content re-added
        #     after deletion.
        #
        #     The match is resolution-aware and mirrors EXACTLY what the scrapheap
        #     deleter does (``routes/pictures/_crud.py`` →
        #     ``resolve_picture_path(image_root, file_path)`` then ``os.remove``):
        #     resolve against the SAME ``vault_root``, then ``realpath`` +
        #     ``normcase`` so a stored path that differs as a STRING but names the
        #     same on-disk file is still caught. This is a real scenario here -
        #     reference-folder pictures store ABSOLUTE ``file_path`` while
        #     imported/managed pictures store RELATIVE - and also covers ``./`` /
        #     ``//`` prefixes, symlinks, and case differences. Raw file_paths are
        #     read under the DB lock (cheap); the filesystem resolution runs here,
        #     off the lock. Captured AFTER the planner/task-runner are stopped so a
        #     background import in the capture→swap window can't add a live picture
        #     the guard would miss.
        from pixlstash.utils.image_processing.image_utils import ImageUtils

        def _resolved_key(fp: Optional[str]) -> Optional[str]:
            resolved = ImageUtils.resolve_picture_path(vault_root, fp)
            if not resolved:
                return None
            return os.path.normcase(os.path.realpath(resolved))

        def _confirmably_differs(snap_sha: Optional[str], live_shas: set) -> bool:
            # True only when BOTH sides are known and none match - i.e. the file
            # on disk is provably DIFFERENT content from the snapshot row (the
            # CSO's purge-evasion case: content C1 was purged, different content
            # C2 is alive at the same path). A NULL ``pixel_sha`` on either side
            # is unconfirmable, so we do NOT claim a difference: the caller then
            # rescues/keeps, which never re-drops a not-yet-hashed reference
            # picture (the deliberately non-strict variant).
            if snap_sha is None:
                return False
            if None in live_shas:
                return False
            return snap_sha not in live_shas

        # Resolved path (resolve → realpath → normcase) → set of ``pixel_sha`` of
        # every LIVE, non-deleted picture at that path. The set may include
        # ``None`` (a picture imported/indexed but not yet hashed). Both the
        # shadow-ghost guard (path membership) and the ledger rescue (content
        # comparison) read this map, so the two decisions share one capture.
        live_active_rows: list[tuple] = self._vault.db.run_immediate_read_task(
            lambda s: [
                (fp, sha)
                for fp, sha in s.exec(
                    select(Picture.file_path, Picture.pixel_sha).where(
                        Picture.deleted.is_(False)
                    )
                ).all()
                if fp
            ]
        )
        live_active_map: dict[str, set] = {}
        for fp, sha in live_active_rows:
            key = _resolved_key(fp)
            if key is None:
                continue
            live_active_map.setdefault(key, set()).add(sha)

        logger.info(
            "RestoreService: swapping live DB with snapshot (snapshot id=%d)",
            snapshot_id,
        )

        def _do_swap():
            # Runs as a control task - the writer thread does NOT open a
            # Session for this op, so there is no session bound to the
            # soon-to-be-disposed engine. _swap_database holds
            # exclusive_engine_access internally to fence out readers.
            self._swap_database(live_db_path, upgraded_snapshot)

        def _post_restore_cleanup(session):
            # Identify resurrected scrapheap ghosts: snapshot ``deleted=True``
            # rows whose file_path RESOLVES to a LIVE, non-deleted file (a file
            # re-added after the snapshot). Resurrecting these on top of an
            # active file lets a later "empty scrapheap" hard-delete that live
            # file - the exact data-loss vector for content added after the
            # snapshot. Drop the ghost rows (the swap NEVER touches image files,
            # so the live file is preserved and simply becomes an orphan, the
            # same safe outcome as any other added-after picture). Both sides use
            # the same ``_resolved_key`` (resolve → realpath → normcase) so the
            # guard and the deleter can never disagree on which on-disk file a
            # stored path refers to. We match in Python to sidestep SQLite's
            # bound-variable limit and because the scrapheap is normally a small
            # fraction of the library.
            shadow_ids: set[int] = set()
            if live_active_map:
                for pid, fp in session.execute(
                    sa_select(Picture.id, Picture.file_path).where(
                        Picture.deleted.is_(True)
                    )
                ).all():
                    key = _resolved_key(fp)
                    if key is not None and key in live_active_map:
                        shadow_ids.add(pid)

            # Rescue ledger-matched pictures that are CURRENTLY alive and active,
            # but ONLY when the on-disk content actually matches (content-aware).
            # ``deleted_ids`` drops any SNAPSHOT picture whose path/content sha is
            # in ``deleted_file_log`` so intentionally-purged content is never
            # resurrected. But the ledger is keyed by path/content, not by picture
            # identity: a path (or content hash) purged in the past - its file kept
            # on disk because the reference folder is protected
            # (``allow_delete_file=False``) - that the user has since RE-INDEXED is
            # alive again. Dropping it is silent data loss. Here the row is
            # ``deleted=False``: if its file resolves to one the LIVE DB is
            # actively using (``live_active_map``), the user is keeping it.
            #
            # Content-aware (CSO purge-evasion guard): rescue only when the
            # snapshot row is NOT ``_confirmably_differs`` from the live content at
            # that path - i.e. the shas MATCH, or either side is NULL
            # (not-yet-hashed → unconfirmable → keep, never re-drop a reference
            # picture). When BOTH shas are known and DIFFER, the on-disk file is
            # genuinely different content that merely shares a purged path, so the
            # stale snapshot row stays dropped and is not resurrected. The ledger
            # still drops any purged path with no live active picture
            # (``test_full_restore_skips_permanently_deleted_picture``: row removed
            # from live, file lingers - its path is NOT in ``live_active_map``).
            # Genuinely missing files (``missing_ids``) are never rescued: there is
            # no file to protect, and the check is independent of the ledger.
            rescued_ids: set[int] = set()
            ledger_only = set(deleted_ids) - set(missing_ids)
            if live_active_map and ledger_only:
                ledger_list = sorted(ledger_only)
                # Chunk the id filter to stay under SQLite's bound-variable limit
                # (the ledger can hold far more than 999 entries).
                for i in range(0, len(ledger_list), 900):
                    chunk = ledger_list[i : i + 900]
                    for pid, fp, sha in session.execute(
                        sa_select(
                            Picture.id, Picture.file_path, Picture.pixel_sha
                        ).where(Picture.id.in_(chunk))
                    ).all():
                        key = _resolved_key(fp)
                        if key is None or key not in live_active_map:
                            continue
                        if not _confirmably_differs(sha, live_active_map[key]):
                            rescued_ids.add(pid)
            if rescued_ids:
                # Keep the report honest: these were counted as permanently
                # deleted before the live-active cross-check ran.
                report.permanently_deleted_count -= len(rescued_ids)
                logger.info(
                    "RestoreService: kept %d ledger-matched picture(s) after "
                    "restore - their file resolves to a live, actively-used "
                    "picture (re-indexed after an earlier purge), so the "
                    "permanent-deletion ledger must not drop them.",
                    len(rescued_ids),
                )

            all_drop_ids = sorted((set(drop_ids) - rescued_ids) | shadow_ids)
            if all_drop_ids:
                # Drop dependents explicitly in FK-safe order, then the
                # pictures. SQLite FK CASCADE would normally take care of
                # this, but doing it explicitly keeps the cleanup robust to
                # relationship-config drift and prevents one failing ORM
                # cascade from rolling back the entire post-restore task and
                # silently leaving the dropped rows behind. ``all_drop_ids``
                # covers missing-on-disk files, permanent deletions (minus
                # live-active rescues), and scrapheap ghosts that shadow a live
                # added-after file.
                for child_model in (
                    Tag,
                    Face,
                    PictureSetMember,
                    PictureProjectMember,
                ):
                    session.execute(
                        sa_delete(child_model).where(
                            child_model.picture_id.in_(all_drop_ids)
                        )
                    )
                result = session.execute(
                    sa_delete(Picture).where(Picture.id.in_(all_drop_ids))
                )
                logger.info(
                    "RestoreService: dropped %d picture row(s) after restore "
                    "(%d missing-file, %d permanently deleted, %d scrapheap "
                    "ghost(s) shadowing a live added-after file - files kept; "
                    "%d ledger match(es) rescued as live).",
                    result.rowcount,
                    len(missing_ids),
                    len(deleted_ids) - len(rescued_ids),
                    len(shadow_ids),
                    len(rescued_ids),
                )
            # Re-arm the scrapheap retention clock. The swapped-in snapshot DB
            # carries each scrapheap row's ORIGINAL ``deleted_at``, which for any
            # snapshot older than the retention window is already expired - the
            # first 15-minute sweep after the restore would then permanently
            # destroy the very scrapheap the user just restored AND write
            # ``file_removed=True``, so a second restore could not bring it back.
            # That is the same failure family as the logged restore data-loss
            # incident. Re-stamping to now gives every restored scrapheap picture
            # a FULL fresh window, exactly as migration 0079 does for pre-existing
            # rows at upgrade. This only ever moves a deadline LATER, so it cannot
            # weaken any existing restore/ledger invariant: rows the ledger or the
            # ghost/missing-file guards decided to drop were already deleted
            # above, and ``deleted`` itself is untouched.
            rearmed = session.execute(
                sa_update(Picture)
                .where(Picture.deleted.is_(True))
                .values(deleted_at=datetime.now(timezone.utc))
            )
            if rearmed.rowcount:
                logger.info(
                    "RestoreService: re-armed the scrapheap retention clock on "
                    "%d restored scrapheap picture(s) - each gets a full "
                    "retention window from the restore, never a resumed one.",
                    rearmed.rowcount,
                )

            # NOTE: derived columns (embeddings + scores) are NOT NULL-reset
            # here. Snapshots now carry these blobs, so the swapped-in DB
            # already holds the real values and the WorkPlanner has nothing to
            # regenerate. Pictures whose blobs were genuinely NULL at snapshot
            # time stay NULL and get picked up by the finders as usual.

            # Replay the pre-swap likeness pipeline state into the swapped
            # DB, dropping entries for pictures that no longer exist in
            # the post-restore picture set. Then ensure every (possibly
            # new) picture has a frontier row so the pipeline picks them
            # up.
            live_pic_ids = set(session.exec(select(Picture.id)).all())
            for pic_id, queued_at in live_likeness_queue:
                if pic_id in live_pic_ids:
                    session.add(
                        PictureLikenessQueue(picture_id=pic_id, queued_at=queued_at)
                    )
            for pic_id_a, j_max in live_likeness_frontier:
                if pic_id_a in live_pic_ids:
                    session.add(
                        PictureLikenessFrontier(picture_id_a=pic_id_a, j_max=j_max)
                    )
            session.commit()
            # ensure_all does its own commit; runs AFTER replay so existing
            # frontier rows aren't double-inserted (it skips keys already
            # present in the table).
            PictureLikenessFrontier.ensure_all(session)

            # Replay the pre-swap snapshot index. The restored DB only knows
            # about snapshots that existed when the target snapshot was taken;
            # re-insert any captured row whose file still exists on disk and
            # isn't already present (deduped by relative_path). This brings the
            # newer snapshots - and the safety snapshot from step 1 - back into
            # the list so they remain valid restore points.
            existing_snapshot_paths = set(
                session.exec(select(Snapshot.relative_path)).all()
            )
            reinserted_snapshots = 0
            for snap in live_snapshots:
                if snap["relative_path"] in existing_snapshot_paths:
                    continue
                try:
                    abs_snap = resolve_path_within(vault_root, snap["relative_path"])
                except ValueError as exc:
                    logger.warning(
                        "RestoreService: snapshot index row %s points outside "
                        "the vault root; not re-inserting it after restore: %s",
                        snap["relative_path"],
                        exc,
                    )
                    continue
                if not os.path.exists(abs_snap):
                    # File was pruned/deleted since capture - skip rather than
                    # leave a dangling index row pointing at nothing.
                    logger.warning(
                        "RestoreService: snapshot file %s missing on disk; "
                        "not re-inserting its index row after restore.",
                        abs_snap,
                    )
                    continue
                session.add(Snapshot(**snap))
                reinserted_snapshots += 1
            if reinserted_snapshots:
                session.commit()
                logger.info(
                    "RestoreService: re-inserted %d snapshot index row(s) "
                    "hidden by the DB swap (newer snapshots + safety snapshot).",
                    reinserted_snapshots,
                )

            # Replay the permanent-deletion ledger. The swapped-in DB only
            # knows about files deleted up to the target snapshot's time;
            # re-insert any live entry it is missing (deduped by path_sha)
            # so the record of permanently-deleted, unrestorable content is
            # never lost by rolling back to an older snapshot.
            existing_path_shas = set(
                session.exec(select(DeletedFileLog.path_sha)).all()
            )
            reinserted_deleted = 0
            for entry in live_deleted_log:
                if entry["path_sha"] in existing_path_shas:
                    continue
                session.add(DeletedFileLog(**entry))
                existing_path_shas.add(entry["path_sha"])
                reinserted_deleted += 1
            if reinserted_deleted:
                session.commit()
                logger.info(
                    "RestoreService: replayed %d permanent-deletion ledger "
                    "row(s) hidden by the DB swap.",
                    reinserted_deleted,
                )

        # Close authentication before the database cutover.  The swap and
        # token deletion are separate writer-queue jobs; without this gate, an
        # old cookie or cached token can authenticate in the gap between them.
        # The gate deliberately stays closed after any swap/finalisation
        # failure.  At that point the process cannot prove its in-memory auth
        # state matches the live database, so only a successful recovery/reset
        # (or a process restart) may admit credentials again.
        auth_service = getattr(self._vault, "auth_service", None)
        if auth_service is not None:
            auth_service.close_auth_for_restore(restore_request_lease)

        # Steps 4-8 wrapped in try/finally so the planner always restarts -
        # if _do_swap or the cleanup raises, leaving the planner stopped
        # would silently halt every background worker (daily snapshots,
        # missing-file detection, embedding generation, ...) until restart.
        try:
            db.run_control_task(_do_swap)
            # Clear API tokens before anything else touches the swapped-in DB,
            # so no later failure in the cleanup below can leave a restored
            # token row in place.  ``run_control_task`` has returned, so the
            # swap has finished and released ``exclusive_engine_access``; this
            # writer task opens its session on the re-created engine.
            db.run_task(self._clear_guest_state, priority=0)
            self._clear_hub_api_tokens()
            db.run_task(_post_restore_cleanup, priority=0)
            # Rebuild process-local auth state only after the restored database
            # is in its final form. A reset failure is fatal and intentionally
            # leaves the gate closed; reporting success would be fail-open.
            self._reset_auth_state()
            if auth_service is not None:
                auth_service.reopen_auth_after_restore()
        finally:
            if planner is not None:
                planner.start()
                logger.info("RestoreService: WorkPlanner restarted after full restore.")

        self._emit_lifecycle(
            EventType.RESTORE_COMPLETED,
            {
                "snapshot_id": snapshot_id,
                "resource_type": "full",
                "missing_files_count": report.missing_files_count,
            },
        )

        logger.info(
            "RestoreService: full restore from snapshot %d completed "
            "(%d missing files).",
            snapshot_id,
            report.missing_files_count,
        )
        return report

    def _reset_auth_state(self) -> None:
        """Drop the in-memory authentication state the DB swap invalidated.

        Clearing ``usertoken`` in the swapped-in database is only half of it.
        ``AuthService`` keeps process-local state derived from the *previous*
        file - a token cache with its own TTL, ``active_session_ids``, the
        session-to-token maps, and a cached copy of the owner row - none of
        which the swap touches. Left alone, a session established before the
        restore keeps authenticating against a database that no longer contains
        the credential it was issued for, and a cached token keeps validating
        for the rest of its TTL. That is fail-open, and it is the reason issue
        #666 exists.

        **Where it runs, and why that is safe.** After
        ``run_control_task(_do_swap)`` has returned, so the swap has finished
        and released ``exclusive_engine_access()`` and the engine has been
        re-created; and after the token clear, so the swapped-in database
        already holds no token rows. The restore authentication gate has been
        closed since before the swap, so no request can repopulate the cache in
        the queue gap. ``reset_after_restore`` re-reads the
        owner row through the ordinary writer queue, which is exactly why it
        must not be called from inside the swap - doing so would take the
        writer queue while the engine lock is held and hang the request path.

        Failure is raised: although the database swap has happened, restore
        finalisation has not succeeded and authentication remains fail-closed.
        The caller must never receive a successful restore report in that state.
        """
        auth_service = getattr(self._vault, "auth_service", None)
        if auth_service is None:
            # A Vault built without a Server (tests, CLI tools) has no auth
            # service, and therefore no in-memory auth state to invalidate.
            logger.debug(
                "RestoreService: no auth service attached to the vault; "
                "no in-memory authentication state to reset."
            )
            return
        try:
            auth_service.reset_after_restore()
        except Exception as exc:
            logger.critical(
                "RestoreService: failed to reset in-memory authentication "
                "state after the restore; authentication remains disabled "
                "until recovery or process restart: %s",
                exc,
                exc_info=True,
            )
            raise RuntimeError(
                "Database restore completed its swap but authentication state "
                "could not be reset; authentication remains disabled."
            ) from exc

    @staticmethod
    def _clear_guest_state(session: Session) -> None:
        """Delete every API token row from the swapped-in database.

        A restore always leaves the vault with no API tokens, whatever the
        snapshot happened to hold.  The rule belongs to the restore path and is
        applied **unconditionally**: it never compares the snapshot's Alembic
        revision, because this project squashes migrations, so a revision
        identifier is not a durable statement about what a snapshot contains.
        A snapshot taken by the current release is cleared exactly like an old
        one.  Tokens are created again from Settings after a restore, and share
        links re-shared with their new values.

        ``guest_score`` and ``guest_session`` are cleared first, child before
        parent.  Both reference a token by id, and SQLite reuses the lowest
        free integer primary key, so a row left behind would come to describe
        whichever token is created next.

        Runs as an ordinary writer task, submitted only after
        ``run_control_task(_do_swap)`` has returned - the swap has therefore
        completed and released ``exclusive_engine_access``, and this session is
        opened on the re-created engine.  Nothing here runs while the engine
        lock is held.

        Args:
            session: Live writer session on the swapped-in database.
        """
        cleared: dict[str, int] = {}
        # Clear the abandoned legacy vault token table as well. Live tokens
        # are revoked from the hub below, but a restored portable credential
        # copy must remain inert and blank.
        for model in (GuestScore, GuestSession, UserToken):
            cleared[model.__name__] = session.execute(sa_delete(model)).rowcount
        session.commit()
        logger.info(
            "RestoreService: cleared %d guest session(s) and %d guest score(s) "
            "and %d legacy token row(s) from the restored vault.",
            cleared["GuestSession"],
            cleared["GuestScore"],
            cleared["UserToken"],
        )

    def _clear_hub_api_tokens(self) -> None:
        """Revoke tokens in the identity hub; tokens no longer live in a vault."""
        auth_service = getattr(self._vault, "auth_service", None)
        if auth_service is None:
            return

        def clear(session: Session) -> int:
            count = session.execute(sa_delete(UserToken)).rowcount
            session.commit()
            return count

        count = auth_service._db.run_task(clear, priority=0)
        logger.info(
            "RestoreService: revoked %d hub API token(s) after restore; tokens "
            "must be recreated and share links re-shared.",
            count,
        )

    def _find_missing_file_ids(
        self, abs_snapshot: str, vault_root: str
    ) -> tuple[list[int], int]:
        """Return Picture IDs from the snapshot whose files are absent on disk.

        Args:
            abs_snapshot: Absolute path to the (possibly upgraded) snapshot.
            vault_root: Root directory of the vault image files.

        Returns:
            Tuple of (missing_ids, total_picture_count) so the caller can
            apply a ratio-based safety check before deleting metadata.

        Raises:
            RuntimeError: If ``vault_root`` is not currently a readable
                directory. Treating a transient mount failure as "all files
                are missing" would wipe metadata for the entire vault, so
                we refuse rather than scan.
        """
        from pixlstash.utils.image_processing.image_utils import ImageUtils

        if not os.path.isdir(vault_root):
            raise RuntimeError(
                f"Vault root {vault_root!r} is not a readable directory; "
                "refusing to scan for missing files. If the vault is on a "
                "network mount, verify it is mounted before retrying."
            )

        missing: list[int] = []
        total: int = 0
        try:
            engine = snapshot_engine(abs_snapshot)
            try:
                with Session(engine) as session:
                    pictures = session.exec(select(Picture)).all()
                    total = len(pictures)
                    for pic in pictures:
                        if not pic.file_path:
                            continue
                        try:
                            resolved = ImageUtils.resolve_picture_path(
                                vault_root, pic.file_path
                            )
                            if not os.path.isfile(resolved):
                                missing.append(pic.id)
                        except Exception as exc:
                            logger.debug(
                                "RestoreService: could not resolve path for picture %s: %s",
                                pic.id,
                                exc,
                            )
            finally:
                engine.dispose()
        except Exception as exc:
            logger.error(
                "RestoreService: failed to scan snapshot for missing files: %s",
                exc,
                exc_info=True,
            )
        return missing, total

    def _load_deleted_file_index(self) -> tuple[set[str], set[str]]:
        """Read the live ``deleted_file_log`` into (path_shas, pixel_shas).

        These identify content the user has *permanently* deleted (see
        ``DeletedFileLog``). Both are one-way hashes - ``path_sha`` is the
        SHA-256 of a picture's vault path, ``pixel_sha`` its content hash.
        Restore consults them so a snapshot taken before the deletion can
        never resurrect that content, and so the missing-file ratio safety
        check does not mistake an intentional purge for a transient mount
        failure.

        Only rows with ``file_removed=True`` are returned. A ``file_removed=
        False`` row records a picture removed from the library whose on-disk
        file was deliberately KEPT (a protected reference-folder picture): its
        content is NOT gone, so restore must never treat it as a permanent
        deletion and drop the alive, file-present picture. The scanner reads the
        ledger separately (all rows) to avoid auto re-importing those kept paths.
        Existing pre-migration rows default to ``file_removed=True`` - they
        predate the distinction and are treated as genuinely deleted so the
        never-resurrect guarantee holds for them.

        Returns:
            ``(path_shas, pixel_shas)`` - path and content hashes recorded as
            permanently deleted (file actually removed from disk).
        """

        def _load(session: Session) -> tuple[set[str], set[str]]:
            rows = session.execute(
                sa_select(DeletedFileLog.path_sha, DeletedFileLog.pixel_sha).where(
                    DeletedFileLog.file_removed.is_(True)
                )
            ).all()
            path_shas = {ps for ps, _ in rows if ps}
            pixel_shas = {sha for _, sha in rows if sha}
            return path_shas, pixel_shas

        return self._vault.db.run_immediate_read_task(_load)

    @staticmethod
    def _match_deleted_picture_ids(
        snap_session: Session,
        path_shas: set[str],
        pixel_shas: set[str],
    ) -> set[int]:
        """Return snapshot Picture IDs whose content is permanently deleted.

        A snapshot row matches if the SHA-256 of its ``file_path`` is in
        *path_shas*, or its ``pixel_sha`` is in *pixel_shas*. The path is
        hashed here (the ledger never stores the raw path); matching on
        ``pixel_sha`` too catches content deleted and later re-added under a
        different filename.

        Args:
            snap_session: Read session on the (upgraded) snapshot DB.
            path_shas: Hashed paths recorded as permanently deleted.
            pixel_shas: Content hashes recorded as permanently deleted.

        Returns:
            Set of snapshot Picture IDs that must not be restored.
        """
        if not path_shas and not pixel_shas:
            return set()
        matched: set[int] = set()
        rows = snap_session.execute(
            sa_select(Picture.id, Picture.file_path, Picture.pixel_sha)
        ).all()
        for pid, fp, sha in rows:
            if (fp and DeletedFileLog.hash_path(fp) in path_shas) or (
                sha and sha in pixel_shas
            ):
                matched.add(pid)
        return matched

    def _find_permanently_deleted_ids(
        self, abs_snapshot: str, path_shas: set[str], pixel_shas: set[str]
    ) -> set[int]:
        """Scan the snapshot file for Picture rows in the deleted-file index.

        Path-based wrapper around ``_match_deleted_picture_ids`` used by the
        full restore (which works from the snapshot file rather than an open
        session). Fails open: any read error returns an empty set so a restore
        is never blocked by an unreadable ledger scan - the missing-file pass
        still drops files that are absent on disk.

        Args:
            abs_snapshot: Absolute path to the (upgraded) snapshot DB.
            path_shas: Hashed paths recorded as permanently deleted.
            pixel_shas: Content hashes recorded as permanently deleted.

        Returns:
            Set of snapshot Picture IDs that must not be restored.
        """
        if not path_shas and not pixel_shas:
            return set()
        try:
            engine = snapshot_engine(abs_snapshot)
            try:
                with Session(engine) as session:
                    return self._match_deleted_picture_ids(
                        session, path_shas, pixel_shas
                    )
            finally:
                engine.dispose()
        except Exception as exc:
            logger.error(
                "RestoreService: failed to scan snapshot for permanently-deleted "
                "pictures: %s",
                exc,
                exc_info=True,
            )
            return set()

    def _swap_database(self, live_db_path: str, new_db_path: str) -> None:
        """Replace the live SQLite file with *new_db_path*.

        Disposes the live engine, atomically swaps the new file into the live
        path, and re-creates the engine.

        The new DB is first copied to a sibling temp file on the same
        filesystem and then moved into place with ``os.replace`` (an atomic
        rename within a filesystem).  This guarantees the live file is always
        either the old or the new database - a crash mid-copy can never leave
        it truncated or partially written.

        Args:
            live_db_path: Absolute path to the live database file.
            new_db_path: Absolute path to the replacement database file.
        """
        db = self._vault.db
        staged_db_path = live_db_path + ".new"
        try:
            # Fence out immediate reads for the whole swap: this waits for any
            # in-flight run_immediate_read_task to finish and blocks new ones,
            # so none opens a session on the disposed engine or hits the file
            # while it is being replaced.
            with db.exclusive_engine_access():
                # Dispose engine and all pooled connections before touching the file.
                db._engine.dispose()
                # With every connection gone, the retained location guard may
                # (and on Windows MUST) release its fd: os.replace onto a file
                # with an open handle is WinError 5 there. Ordering matters -
                # guard only after dispose, or closing it strips the process's
                # POSIX locks out from under the live connections (the
                # corruption the guard retention exists to prevent). No new
                # guard is armed on the swapped file: connections take their
                # own locks, there is no further guard-close left to strip
                # them, and re-validating the namespace here would make restore
                # refuse installed-base directories that startup accepts. The
                # next process start guards the file as usual.
                guard = getattr(db, "_location_guard", None)
                if guard is not None:
                    guard.close()
                    db._location_guard = None
                # Remove stale WAL/SHM files so the new DB starts clean.
                for suffix in ("-wal", "-shm"):
                    stale = live_db_path + suffix
                    if os.path.exists(stale):
                        os.remove(stale)
                shutil.copy2(new_db_path, staged_db_path)
                # fsync the staged file and its parent directory so the
                # restored DB is durable on disk before we swap it into
                # place. Without this, a power loss between os.replace
                # and the next implicit fsync can leave the live DB
                # pointing at a file whose pages aren't yet on disk.
                # Open read-write ("rb+"): Windows refuses to fsync a
                # read-only handle ("rb") with EBADF, since CommitFileBuffers
                # requires write access.
                with open(staged_db_path, "rb+") as staged_fd:
                    # VACUUM INTO created the snapshot at 0644 & ~umask and
                    # copy2 preserved that mode, so under umask 002 a restore
                    # would leave the live vault.db group-writable - which the
                    # trusted-location check then refuses on the next startup.
                    # Windows has no fchmod and no real mode bits.
                    if hasattr(os, "fchmod"):
                        os.fchmod(staged_fd.fileno(), 0o600)
                    staged_fd.flush()
                    os.fsync(staged_fd.fileno())
                os.replace(staged_db_path, live_db_path)
                # Directory fsync is a POSIX durability guarantee. Windows
                # neither lets you open a directory as a file descriptor nor
                # needs it (NTFS journals its own metadata), so skip it there.
                if os.name != "nt":
                    live_dir_fd = os.open(
                        os.path.dirname(live_db_path) or ".", os.O_RDONLY
                    )
                    try:
                        os.fsync(live_dir_fd)
                    finally:
                        os.close(live_dir_fd)
                # Recreate the engine through the shared helper so the
                # swapped-in live DB gets exactly the startup engine's
                # configuration (§13). The two drifting apart was #651.
                db._engine = create_configured_engine(live_db_path)
            logger.info("RestoreService: DB swap complete, engine re-created.")
        except Exception as exc:
            logger.error("RestoreService: DB swap failed: %s", exc, exc_info=True)
            raise
        finally:
            try:
                if os.path.exists(staged_db_path):
                    os.remove(staged_db_path)
                os.remove(new_db_path)
                shutil.rmtree(os.path.dirname(new_db_path), ignore_errors=True)
            except Exception as exc:
                logger.warning(
                    "RestoreService: failed to clean up temp DB swap files: %s",
                    exc,
                )
