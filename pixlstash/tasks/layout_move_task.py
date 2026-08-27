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
    apply_moves,
    plan_moves,
    prune_move_journal,
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

        moved, skipped, operation = self._db.run_task(
            self._move_batch, picture_ids, priority=DBPriority.LOW
        )
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

    def _move_batch(self, session: Session, picture_ids: list):
        """Plan, count, move, record - all inside one transaction.

        One transaction on purpose, for the reason ``run_recorded_metadata_task``
        gives: the ``Operation`` row and the change it describes have to commit
        against the same serialised writer, or a write landing between the
        snapshot and the mutation is silently attributed to this move.

        The stamp is cleared for **every** candidate, not only the ones that
        moved. A picture whose folder is still true has had its question asked
        and answered; leaving it stamped would re-ask it on every cycle for
        ever.
        """
        image_root = self._db.image_root
        applied: list = []
        plan, skipped = plan_moves(session, picture_ids, image_root)
        for picture_id, reason in skipped:
            logger.warning(
                "Layout: picture %s should move but was left alone (%s).",
                picture_id,
                reason,
            )

        operation = None
        moved: list = []
        if plan:
            # Counted before it happens.
            logger.info(
                "Layout: %d picture(s) are in a folder that has stopped being "
                "true; moving them now.",
                len(plan),
            )
            targets = [move.picture_id for move in plan]
        try:
            if plan:
                before = capture_state_in_session(session, targets)
                moved = apply_moves(
                    session, plan, image_root=image_root, applied=applied
                )
                after = capture_state_in_session(session, targets)
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

            self._clear_due(session, picture_ids)
            pruned = prune_move_journal(session)
            if pruned:
                logger.debug("Layout: pruned %d expired move-journal row(s).", pruned)
            session.commit()
        except BaseException:
            # The rollback has to cover the WHOLE task, not just the move loop.
            # Everything after ``apply_moves`` - two captures, the operation row,
            # the flag clear, the commit - can raise, and the writer thread then
            # rolls the session back while the files stay where this put them.
            # A row naming a path with no file at it is not a cosmetic
            # inconsistency: ``MissingFilePurgeFinder`` deletes that row within
            # the hour and the picture's tags, sets and score go with it.
            rollback_applied_moves(applied, image_root)
            raise
        return moved, skipped, operation

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
