from sqlalchemy.orm import load_only
from sqlmodel import Session, select

from pixlstash.db_models import Picture
from pixlstash.pixl_logging import get_logger

from .base_task_finder import SimpleMissingFinder
from .comfyui_extraction_task import ComfyUIExtractionTask

logger = get_logger(__name__)


class MissingComfyUIExtractionFinder(SimpleMissingFinder):
    """Find pictures not yet checked for embedded ComfyUI workflow metadata.

    With a hub attached the predicate widens to the workflow scan, and it widens
    by *replacing* rather than by adding an ``OR``: ``workflow_hash_version`` is
    written by the same task and in the same batch as ``comfyui_models``, and it
    is a newer column, so every picture the old predicate matches is already
    matched by the new one. That keeps the idle probe a single indexed ``IS
    NULL`` term (``ix_picture_workflow_unscanned``) instead of a union of two.

    Without a hub the old predicate stands, so a vault opened by the CLI or by a
    test does not spin on a scan it cannot perform -- and a hub that turns out
    to be unwritable puts this finder back into exactly that state rather than
    re-offering the same pictures forever (``stand_down``).
    """

    def __init__(self, database, image_root: str, hub=None):
        super().__init__(database)
        self._image_root = image_root
        self._hub = hub
        self._scanning_workflows = hub is not None

    def finder_name(self) -> str:
        return "MissingComfyUIExtractionFinder"

    def _batch_size(self) -> int:
        return ComfyUIExtractionTask.BATCH_SIZE

    def _fetch_candidates(self, session: Session, limit: int) -> list[Picture]:
        # comfyui_models IS NULL means never checked; "[]" is the checked-but-empty
        # sentinel. workflow_hash_version says the same thing for the workflow
        # scan, and needs its own column because a hash column has no such string.
        unscanned = (
            Picture.workflow_hash_version.is_(None)
            if self._scanning_workflows
            else Picture.comfyui_models.is_(None)
        )
        return session.exec(
            select(Picture)
            .options(load_only(Picture.id, Picture.file_path, Picture.comfyui_models))
            .where(unscanned)
            .where(Picture.deleted.is_(False))
            .order_by(Picture.id)
            .limit(limit)
        ).all()

    def _create_task(self, pictures: list):
        return ComfyUIExtractionTask(
            database=self._db,
            image_root=self._image_root,
            pictures=pictures,
            hub=self._hub if self._scanning_workflows else None,
            on_hub_failure=self.stand_down,
        )

    def stand_down(self) -> None:
        """Narrow back to the pre-B3 predicate after an unwritable hub.

        Called by the task on the first hub write it cannot complete. The
        alternative -- leaving those pictures unmarked and matching them again
        next cycle -- makes this finder re-open and re-parse every image in the
        library on every planning cycle for as long as the hub stays broken.
        Falling back drains the old predicate and goes quiet, which is what the
        finder did before it learned to hash.
        """
        if not self._scanning_workflows:
            return
        self._scanning_workflows = False
        logger.error(
            "MissingComfyUIExtractionFinder: standing down the workflow scan "
            "for this process after a hub write failed. Pictures already "
            "scanned keep their keys; the rest stay unscanned until a restart."
        )
