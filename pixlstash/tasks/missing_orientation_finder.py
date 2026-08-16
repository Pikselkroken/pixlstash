"""Finder for pictures whose mirrored EXIF orientation has not been read yet."""

from pixlstash.tasks.base_task_finder import SimpleMissingFinder
from pixlstash.tasks.orientation_task import OrientationTask


class MissingOrientationFinder(SimpleMissingFinder):
    """Queue :class:`OrientationTask` for pictures whose ``orientation`` is NULL.

    A NULL orientation is a picture the operation log cannot record a rotate for
    honestly (§21), so this backfill is what makes in-place rotate undoable on a
    library that predates the column. Reading an EXIF header is cheap next to
    everything else in the pipeline, so it runs at one task in flight and gets
    out of the way.
    """

    def finder_name(self) -> str:
        return "MissingOrientationFinder"

    def max_inflight_tasks(self) -> int:
        return 1

    def _batch_size(self) -> int:
        return OrientationTask.BATCH_SIZE

    def _fetch_candidates(self, session, limit: int) -> list:
        return OrientationTask.find_pictures_missing_orientation(session, limit)

    def _create_task(self, pictures: list):
        return OrientationTask(database=self._db, pictures=pictures)
