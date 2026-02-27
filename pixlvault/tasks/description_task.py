from sqlmodel import Session

from pixlvault.database import DBPriority
from pixlvault.db_models import Picture
from pixlvault.picture_tagger import PictureTagger
from pixlvault.pixl_logging import get_logger
from pixlvault.task_runner import BaseTask


logger = get_logger(__name__)


class DescriptionTask(BaseTask):
    """Task for generating and persisting description batches.

    Args:
        database: Vault database instance.
        picture_tagger: Tagger used to generate descriptions.
        pictures: Pictures to process in this batch.
    """

    def __init__(
        self,
        database,
        picture_tagger: PictureTagger,
        pictures: list[Picture],
    ):
        picture_ids = [pic.id for pic in (pictures or []) if getattr(pic, "id", None)]
        super().__init__(
            task_type="DescriptionTask",
            params={
                "picture_ids": picture_ids,
                "batch_size": len(picture_ids),
            },
        )
        self._db = database
        self._picture_tagger = picture_tagger
        self._pictures = pictures or []

    def _run_task(self):
        if not self._pictures:
            return {"changed_count": 0, "changed": []}

        descriptions_generated = self._generate_descriptions_batch(self._pictures)
        if not descriptions_generated:
            return {"changed_count": 0, "changed": []}

        def update_descriptions(session: Session, pics):
            changed = []
            for pic in pics:
                db_pic = session.get(Picture, pic.id)
                if db_pic is not None:
                    db_pic.description = pic.description
                    session.add(db_pic)
                    changed.append((Picture, pic.id, "description", pic.description))
            session.commit()
            return changed

        changed = self._db.run_task(
            update_descriptions,
            descriptions_generated,
            priority=DBPriority.LOW,
        )

        return {
            "changed_count": len(changed),
            "changed": changed,
        }

    def _generate_descriptions_batch(self, pictures: list[Picture]) -> list[Picture]:
        picture_ids = [pic.id for pic in pictures]
        logger.debug(
            "DescriptionTask: Generating descriptions for batch_size=%s ids=%s",
            len(pictures),
            picture_ids,
        )

        descriptions_generated = []
        try:
            batch_results = self._picture_tagger.generate_descriptions_batch(pictures)
        except Exception as exc:
            import traceback

            logger.error(
                "DescriptionTask failed for ids=%s: %s\n%s",
                picture_ids,
                exc,
                traceback.format_exc(),
            )
            batch_results = None

        if not batch_results:
            for pic in pictures:
                pic.description = ""
                descriptions_generated.append(pic)
            return descriptions_generated

        for pic in pictures:
            description = batch_results.get(pic.id)
            if description:
                pic.description = description
            else:
                logger.error("Failed to generate description for picture %s", pic.id)
                pic.description = ""
            descriptions_generated.append(pic)
        return descriptions_generated
