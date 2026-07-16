"""Finder that queues a split-assignment sweep whenever a picture lacks one.

Spec A of ``docs/reviews/tag-review-board-redesign-ux-spec.md`` §3: the
previous state of the world had zero callers of ``POST /picture_splits/assign``
anywhere in the app, so ``eval_candidate_n_pos`` (freeze eligibility) could
never move however much a tag was reviewed. This finder makes split
assignment invisible background maintenance, matching every other
derived-state task in this app (quality score, embeddings, likeness) — no
bespoke UI, just another lane in the existing Tasks-tab pipeline.

**Ordering (deliberately soft, not a ``depends_on()``).** The spec's
preference is to run this after ``IMAGE_EMBEDDING``/``LIKENESS`` have mostly
landed, since near-duplicate corroboration (the basis for which pictures get
unioned into one component) has more data available then. This finder does
NOT declare that via ``BaseTaskFinder.depends_on()`` on purpose:
``WorkPlanner`` blocks a finder for an entire planning cycle while *any*
listed dependency finder is not yet exhausted (see ``MissingTagPredictionFinder``
for the precedent), and ``IMAGE_EMBEDDING``/``LIKENESS`` can legitimately stay
unexhausted for a long time on an actively-importing or large vault — hard
-blocking on them here would starve split assignment exactly when it matters
most (mid-review, freeze eligibility stuck at 0), which is the very bug this
finder exists to fix. Missing embeddings degrade gracefully anyway
(``picture_split_service``'s union-find treats a picture with no corroborated
neighbour as a singleton component), and the write-path conflict guard
(``check_split_conflicts_for_new_edges``) catches any resulting
same-split-mismatch later. So: no hard ordering, documented here rather than
silently omitted.
"""

from typing import TYPE_CHECKING

from sqlmodel import select

from pixlstash.db_models.picture import Picture
from pixlstash.db_models.picture_split import PictureSplit
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task_finder import BaseTaskFinder

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)


class PictureSplitAssignmentFinder(BaseTaskFinder):
    """Discover whether any non-deleted picture lacks a ``PictureSplit`` row.

    Cheap existence check (``LIMIT 1`` anti-join) — when it finds one, queues
    a single :class:`~pixlstash.tasks.picture_split_assignment_task.
    PictureSplitAssignmentTask`, which handles every currently-unassigned
    picture in one pass (``assign_splits_in_session`` is naturally
    idempotent: pictures that already have a row are excluded from its
    target set, so re-running when nothing changed is a fast no-op).

    Gated on ``vault.picture_split_auto_assign_enabled`` (default True) —
    tests that hand-control ``PictureSplit`` rows to exercise
    ``assign_splits``/the conflict guard directly set this False so they
    aren't racing this finder's background sweep.

    Attributes:
        _vault: The owning Vault, used to reach the DB worker.
    """

    def __init__(self, vault: "Vault") -> None:
        """Initialise the finder.

        Args:
            vault: The owning Vault instance.
        """
        super().__init__()
        self._vault = vault

    def finder_name(self) -> str:
        return "PictureSplitAssignmentFinder"

    def max_inflight_tasks(self) -> int:
        return 1

    def find_task(self):
        if not self._vault.picture_split_auto_assign_enabled:
            return None

        def _has_unassigned_picture(session) -> bool:
            row = session.exec(
                select(Picture.id)
                .outerjoin(PictureSplit, PictureSplit.picture_id == Picture.id)
                .where(
                    Picture.deleted.is_(False),
                    PictureSplit.picture_id.is_(None),
                )
                .limit(1)
            ).first()
            return row is not None

        has_unassigned = self._vault.db.run_immediate_read_task(_has_unassigned_picture)
        if not has_unassigned:
            return None

        from pixlstash.tasks.picture_split_assignment_task import (
            PictureSplitAssignmentTask,
        )

        logger.debug(
            "PictureSplitAssignmentFinder: unassigned pictures found — queuing sweep"
        )
        return PictureSplitAssignmentTask(self._vault)
