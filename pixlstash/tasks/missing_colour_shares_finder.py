"""Finder for pictures with a thumbnail whose colour summary is not computed yet."""

from sqlalchemy.orm import load_only
from sqlmodel import select

from pixlstash.db_models.picture import Picture
from pixlstash.tasks.base_task_finder import SimpleMissingFinder
from pixlstash.tasks.colour_shares_task import ColourSharesTask


class MissingColourSharesFinder(SimpleMissingFinder):
    """Queue :class:`ColourSharesTask` for pictures whose ``colour_shares`` is NULL.

    The probe is served by ``ix_picture_colour_shares_missing``; the thumbnail
    term keeps pictures still waiting for their bitmap out of the candidates.
    One task at a time: decoding a thumbnail is cheap, and the whole backfill
    should take the CPU lightly.
    """

    def finder_name(self) -> str:
        return "MissingColourSharesFinder"

    def max_inflight_tasks(self) -> int:
        return 1

    def _batch_size(self) -> int:
        return ColourSharesTask.BATCH_SIZE

    def _fetch_candidates(self, session, limit: int) -> list:
        stmt = (
            select(Picture)
            .options(load_only(Picture.id, Picture.file_path))
            .where(Picture.colour_shares.is_(None))
            .where(Picture.deleted.is_(False))
            .where(Picture.thumbnail_width.is_not(None))
            .where(Picture.file_path.is_not(None))
        )
        # Pictures whose thumbnail could not be read stay NULL; excluded here so
        # they cannot crowd the candidate window (as MissingThumbnailFinder).
        suppressed = self._db.unprocessable_images.active_suppressed_ids()
        if suppressed:
            stmt = stmt.where(Picture.id.notin_(tuple(suppressed)))
        return session.exec(stmt.order_by(Picture.id).limit(limit)).all()

    def _create_task(self, pictures: list):
        return ColourSharesTask(database=self._db, pictures=pictures)
