"""Move the pictures whose folder has stopped being true.

v1.11 Phase 4b. The background half of the move engine: the flush hook in
``database.py`` stamps a picture when its project / set / person membership
changes, this task asks - once the debounce has passed - whether the folder it
is sitting in still describes it, and moves only the ones where the answer is
no.

**The count comes before the move.** The plan is built whole, logged with its
size, and only then executed, so "37 files are about to move" is a fact the log
holds even if the move then fails half way. The whole batch is one row on the
operation log, so one Ctrl+Z puts every file back.

**Almost every pass moves nothing**, and that is the design working. Adding a
second project or a second person stamps the picture and the answer is "still
true"; the stamp is spent and the file never moved.
"""

import time

from sqlmodel import Session, select, update as sa_update

from pixlstash.database import DBPriority
from pixlstash.db_models.picture import Picture
from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.services.layout_move_service import (
    BATCH_SIZE,
    OP_LAYOUT_MOVE,
    drop_unlanded_journal,
    journal_moves,
    move_planned_files,
    plan_moves,
    prune_move_journal,
    record_moves,
    rollback_applied_moves,
)
from pixlstash.services.operation_log_service import (
    capture_state_in_session,
    record_operation_in_session,
)
from pixlstash.tasks.base_task import BaseTask, TaskPriority

logger = get_logger(__name__)


class LayoutMoveTask(BaseTask):
    """Check a batch of stamped pictures and move the ones that must move."""

    BATCH_SIZE = BATCH_SIZE

    @property
    def priority(self) -> TaskPriority:
        return TaskPriority.LOW

    def __init__(self, database, pictures: list, notifier=None):
        picture_ids = [pic.id for pic in (pictures or []) if getattr(pic, "id", None)]
        super().__init__(
            task_type="LayoutMoveTask",
            params={"picture_ids": picture_ids, "batch_size": len(picture_ids)},
        )
        self._db = database
        self._notifier = notifier

    @staticmethod
    def find_due_pictures(session: Session, limit: int, now: float) -> list:
        """Return pictures whose layout check has come due.

        One indexed predicate over a column that is NULL for everything the
        owner has not just touched, which is what makes this cheap to ask on
        every planning cycle.
        """
        return list(
            session.exec(
                select(Picture)
                .where(Picture.layout_check_due_at.is_not(None))
                .where(Picture.layout_check_due_at <= now)
                .where(Picture.deleted.is_(False))
                .order_by(Picture.layout_check_due_at)
                .limit(limit)
            ).all()
        )

    def _run_task(self):
        start = time.time()
        picture_ids = list(self.params.get("picture_ids") or [])
        if not picture_ids:
            return {"moved_count": 0, "moved_picture_ids": [], "skipped": []}

        image_root = self._db.image_root
        applied: list = []
        # Phase 1: decide, and commit the intent before a single file moves.
        plan, skipped = self._db.run_task(
            self._plan_and_journal, picture_ids, priority=DBPriority.LOW
        )
        if not plan:
            return {
                "moved_count": 0,
                "moved_picture_ids": [],
                "skipped": skipped,
                "operation": None,
            }
        try:
            # Phase 2: the renames, on this thread. Not the writer's.
            landed = move_planned_files(plan, applied=applied)
            # Phase 3: repoint the rows and log the operation, in one short
            # transaction that holds no filesystem work at all.
            moved, operation = self._db.run_task(
                self._record_batch, plan, landed, priority=DBPriority.LOW
            )
        except BaseException:
            rollback_applied_moves(applied, image_root)
            self._abandon_journal(plan)
            raise
        if moved:
            logger.info(
                "Layout: moved %d file(s) in %.2fs; one undo puts them all back.",
                len(moved),
                time.time() - start,
            )
            self._notify(moved)
        return {
            "moved_count": len(moved),
            "moved_picture_ids": moved,
            "skipped": skipped,
            "operation": operation,
        }

    def _plan_and_journal(self, session: Session, picture_ids: list):
        """Plan the batch, journal the intent, spend the stamps. Then commit.

        **The journal is committed before any file moves**, which is the whole
        reason this is its own transaction. A crash during the renames then
        leaves a durable row naming both paths, and ``MissingFilePurgeTask``
        repoints the picture instead of deleting it and its metadata. Rows for
        files that do not end up moving are dropped in phase three.

        The stamp is cleared for **every** candidate, not only the ones that
        will move. A picture whose folder is still true has had its question
        asked and answered; leaving it stamped would re-ask it on every cycle
        for ever.
        """
        image_root = self._db.image_root
        plan, skipped = plan_moves(session, picture_ids, image_root)
        for picture_id, reason in skipped:
            logger.warning(
                "Layout: picture %s should move but was left alone (%s).",
                picture_id,
                reason,
            )
        if plan:
            # Counted before it happens.
            logger.info(
                "Layout: %d picture(s) are in a folder that has stopped being "
                "true; moving them now.",
                len(plan),
            )
            journal_moves(session, plan)
        self._clear_due(session, picture_ids)
        pruned = prune_move_journal(session)
        if pruned:
            logger.debug("Layout: pruned %d expired move-journal row(s).", pruned)
        session.commit()
        return plan, skipped

    def _record_batch(self, session: Session, plan: list, landed: list):
        """Repoint the moved rows and record the undo. One short transaction.

        The ``before`` capture, the row writes and the ``after`` capture stay in
        a single transaction for the reason ``run_recorded_metadata_task``
        gives: the ``Operation`` row and the change it describes must commit
        against the same serialised writer, or a write landing between the
        snapshot and the mutation is silently attributed to this move. Only the
        filesystem work moved out, and it never belonged here.
        """
        image_root = self._db.image_root
        targets = [move.picture_id for move in plan]
        before = capture_state_in_session(session, targets)
        moved = record_moves(session, landed, image_root=image_root)
        drop_unlanded_journal(session, plan, landed)
        after = capture_state_in_session(session, targets)
        operation = None
        if moved:
            record = record_operation_in_session(
                session,
                op_type=OP_LAYOUT_MOVE,
                before=before,
                after=after,
                source="system",
                summary=_summary,
                undoable=True,
                commit=False,
            )
            operation = record.id if record is not None else None
        session.commit()
        return moved, operation

    def _abandon_journal(self, plan: list) -> None:
        """Drop the intent rows after the files were put back. Best effort.

        Runs while an error is already on its way to the caller, so a failure
        here is logged rather than raised - it would replace the real one. A row
        left behind names a move that did not happen, which would let a later
        genuine owner move between the same two paths be dismissed as ours.
        """
        try:
            self._db.run_task(
                lambda session: (
                    drop_unlanded_journal(session, plan, []),
                    session.commit(),
                ),
                priority=DBPriority.LOW,
            )
        except Exception as exc:
            logger.warning(
                "Layout: could not drop the move journal for %d abandoned "
                "move(s): %s. A later owner move between the same paths may be "
                "mistaken for ours until the row expires.",
                len(plan),
                exc,
            )

    @staticmethod
    def _clear_due(session: Session, picture_ids: list) -> None:
        """Spend the debounce stamp on every picture this pass considered."""
        if not picture_ids:
            return
        session.execute(
            sa_update(Picture.__table__)
            .where(Picture.__table__.c.id.in_([int(pid) for pid in picture_ids]))
            .values(layout_check_due_at=None)
        )

    def _notify(self, picture_ids: list) -> None:
        """Tell open clients the files moved.

        ``pixels`` because a moved file changes the thumbnail URL, which is
        derived from the path and does not come back from
        ``GET /pictures/{id}/metadata`` - the same marker a rotate raises, for
        the same reason.
        """
        if not self._notifier or not picture_ids:
            return
        try:
            self._notifier(
                EventType.CHANGED_PICTURES,
                {
                    "picture_ids": list(picture_ids),
                    "change_kind": "updated",
                    "fields": ["file_path", "pixels"],
                },
            )
        except Exception as exc:
            logger.warning(
                "Layout: could not announce %d moved picture(s): %s",
                len(picture_ids),
                exc,
            )


def _summary(before_delta: dict, after_delta: dict) -> str:
    """The sentence the undo toast shows."""
    count = len(after_delta)
    return (
        "Moved 1 picture to match its folders"
        if count == 1
        else f"Moved {count} pictures to match their folders"
    )
