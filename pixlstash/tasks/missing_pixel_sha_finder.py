"""Finder for pictures missing the tier-1 exact-duplicate hash."""

from pixlstash.tasks.base_task_finder import SimpleMissingFinder
from pixlstash.tasks.pixel_sha_task import PixelShaTask


class MissingPixelShaFinder(SimpleMissingFinder):
    """Queue :class:`PixelShaTask` for pictures whose ``pixel_sha`` is NULL.

    Tier 1 of the duplicate queue groups on ``picture.pixel_sha``; a NULL there
    is a duplicate nobody can find. Reading a file to digest it is cheap next to
    the embedding pipeline, so this runs at one task in flight and gets out of
    the way.
    """

    def finder_name(self) -> str:
        return "MissingPixelShaFinder"

    def max_inflight_tasks(self) -> int:
        return 1

    def _batch_size(self) -> int:
        return PixelShaTask.BATCH_SIZE

    def _fetch_candidates(self, session, limit: int) -> list:
        return PixelShaTask.find_pictures_missing_pixel_sha(session, limit)

    def _create_task(self, pictures: list):
        return PixelShaTask(database=self._db, pictures=pictures)
