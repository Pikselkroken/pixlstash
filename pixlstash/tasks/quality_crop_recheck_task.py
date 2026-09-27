"""Re-run only the PixlStash tagger's quality crop over already-tagged pictures.

A change to ``tagger_settings.plugins.pixlstash_tagger.params.quality_crop``
applies to pictures tagged afterwards. ``POST /taggers/pixlstash_tagger/
quality-crop/recheck`` marks the pictures tagged before it
(``Picture.quality_crop_pending``) and this task answers the mark: it builds
each picture's quality crop exactly as :class:`TagTask` does (the shared
:class:`QualityCropSource`), runs the built-in tagger on it at the configured
size, and **adds** the crop's tags that are missing.

It is deliberately not a retag. A retag deletes every tag, hand-added ones
included (#1357); this writes nothing but new ``Tag`` rows for the tags the
crop owns, and never deletes one. The Tag table records no source for a row, so
"the model wrote this" cannot be proven for a tag the crop no longer finds, and
the owner's ruling is that a tag the owner may have added is always kept.
"""

import time

from sqlmodel import Session, select, update

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    Picture,
    Tag,
    TAG_SENTINEL_LIKE_PATTERN,
    TAG_SENTINEL_ESCAPE_CHAR,
)
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.inference.workflows.tagging import TaggingWorkflow
from pixlstash.pixl_logging import get_logger
from pixlstash.services.set_lock_service import locked_picture_ids
from pixlstash.tagger_plugins.pixlstash_tagger import quality_crop_whitelist
from pixlstash.tasks.base_task import BaseTask, QueueType, TaskPriority
from pixlstash.tasks.tag_task import QualityCropSource
from pixlstash.utils.service.smart_score_invalidation import (
    invalidate_on_anomaly_change,
)
from pixlstash.utils.service.tag_prediction_utils import (
    recompute_anomaly_tag_uncertainty,
)


logger = get_logger(__name__)


class QualityCropRecheckTask(QualityCropSource, BaseTask):
    """Re-check a batch of tagged pictures' quality crops, adding what they find.

    Args:
        database: The vault database.
        tagging_workflow: The engine's tagging workflow; its settings give the
            crop size, and its engine holds the PixlStash tagger.
        pictures: The pictures to re-check (``id`` and ``file_path`` loaded).
        reset_generation: ``database.tag_resets.current()`` read BEFORE the
            pictures were, as for :class:`TagTask`: a picture retagged after it
            gets no write from this task (#1361).
    """

    def __init__(
        self,
        database,
        tagging_workflow: TaggingWorkflow,
        pictures: list,
        reset_generation: int | None = None,
    ):
        picture_ids = [pic.id for pic in (pictures or []) if getattr(pic, "id", None)]
        super().__init__(
            task_type="QualityCropRecheckTask",
            params={
                "picture_ids": picture_ids,
                "batch_size": len(picture_ids),
            },
        )
        self._db = database
        self._tagging_workflow = tagging_workflow
        self._pictures = pictures or []
        # Optional on the database object, like `unprocessable_images`.
        self._tag_resets = getattr(database, "tag_resets", None)
        if reset_generation is None and self._tag_resets is not None:
            reset_generation = self._tag_resets.current()
        self._reset_generation = reset_generation

    @property
    def priority(self) -> TaskPriority:
        # Catch-up work the owner asked for, behind live tagging.
        return TaskPriority.LOW

    @property
    def queue_type(self) -> QueueType:
        # Runs the tagger, so it belongs on the serialised GPU queue.
        return QueueType.GPU

    def _run_task(self) -> dict:
        workflow = self._tagging_workflow
        n_pictures = len(self._pictures)
        target = workflow.pixlstash_tagger_image_size_quality_crop()
        if target is None:
            # Switched off after the finder handed this batch out. The flags stay:
            # the finder idles while the crop is off, and turning it back on
            # resumes exactly the re-check the owner asked for.
            logger.info(
                "Quality crop is off; leaving the crop re-check pending for "
                "%d picture(s).",
                n_pictures,
            )
            return {"pictures": 0, "tags_added": 0, "tagged_picture_ids": []}

        started_at = time.perf_counter()
        # The crop is always judged by the built-in tagger, whichever plugin
        # runs the full-image pass, so load that one explicitly.
        workflow.ensure_active_plugin_ready(engine_override="pixlstash_tagger")
        if not workflow.is_quality_crop_model_loaded():
            # Raised, not returned: `tag_quality_crops` would answer {} and every
            # picture would be recorded as re-checked with nothing found. The
            # flags stay and the finder backs off after a failed task.
            raise RuntimeError(
                "The PixlStash tagger is not loaded; cannot re-check quality crops "
                f"for {n_pictures} picture(s)."
            )

        pic_ids = [pic.id for pic in self._pictures]
        faces_by_pic = self._db.run_immediate_read_task(
            lambda session: self._fetch_faces_for_pictures(session, pic_ids)
        )

        items: list = []
        pic_id_by_key: dict[str, int] = {}
        centre_keys: set[str] = set()
        # Pictures that loaded but could not contribute a crop (a face row whose
        # bbox is not four numbers, say). Nothing about them will change by
        # retrying, so they are recorded as re-checked with nothing found.
        no_crop_ids: list[int] = []
        # Pictures whose file did not load are left pending: the undecodable one
        # is marked unprocessable and the unreachable one held, exactly as
        # TagTask leaves its sentinel, and the registry keeps both out of the
        # finder until the file changes or the location returns.
        unloaded_ids: list[int] = []
        for pic in self._pictures:
            file_path, img, undecodable = self._load_pic(pic)
            if img is None:
                if undecodable:
                    self._mark_unprocessable(pic, file_path)
                else:
                    self._hold_unreachable(pic, file_path)
                unloaded_ids.append(pic.id)
                continue
            built = self._build_quality_crop(
                pic, faces_by_pic.get(pic.id, []), target, {file_path: img}
            )
            if built is None:
                no_crop_ids.append(pic.id)
                continue
            key, crop, _path, is_centre_crop = built
            items.append((key, crop))
            pic_id_by_key[key] = pic.id
            if is_centre_crop:
                centre_keys.add(key)

        inference_start = time.perf_counter()
        quality_results = (
            workflow.tag_quality_crops(items, image_size=target) if items else {}
        )
        inference_s = time.perf_counter() - inference_start

        # found = the crop's tags (thresholded by the service, threshold_offset
        # included) that this crop type owns.
        found_by_pic: dict[int, set[str]] = {
            pid: set() for pid in pic_id_by_key.values()
        }
        for key, tags in quality_results.items():
            pic_id = pic_id_by_key.get(key)
            if pic_id is None:
                continue
            owned = quality_crop_whitelist(key in centre_keys)
            found_by_pic[pic_id].update(t for t in tags if t in owned)

        summary = self._db.run_task(
            self._apply_unless_reset,
            found_by_pic,
            no_crop_ids,
            priority=DBPriority.LOW,
        )
        logger.info(
            "[QUALITY_CROP_RECHECK] task_id=%s pictures=%d crops=%d size=%d "
            "tags_added=%d pictures_changed=%d skipped=%d no_crop=%d "
            "not_loaded=%d inference_s=%.3f total_s=%.3f",
            self.id,
            n_pictures,
            len(items),
            target,
            summary["tags_added"],
            len(summary["tagged_picture_ids"]),
            len(summary["skipped_ids"]),
            len(no_crop_ids),
            len(unloaded_ids),
            inference_s,
            time.perf_counter() - started_at,
        )
        return {
            "pictures": len(summary["cleared_ids"]),
            "tags_added": summary["tags_added"],
            "tagged_picture_ids": summary["tagged_picture_ids"],
        }

    def _apply_unless_reset(
        self,
        session: Session,
        found_by_pic: dict[int, set[str]],
        no_crop_ids: list[int],
    ) -> dict:
        """``apply_crop_tags`` minus the pictures retagged since the read (#1361).

        Their crop was judged against tags that have since been replaced, and a
        retag's own ``TagTask`` runs the crop pass again and clears the flag.
        Must run on the writer thread, where the resets also run.
        """
        candidate_ids = list(found_by_pic) + list(no_crop_ids)
        stale: set[int] = set()
        if self._tag_resets is not None:
            stale = self._tag_resets.reset_since(candidate_ids, self._reset_generation)
            if stale:
                logger.info(
                    "QualityCropRecheckTask %s: dropping output for %d picture(s) "
                    "retagged since it read them: %s",
                    self.id,
                    len(stale),
                    sorted(stale),
                )
        return self.apply_crop_tags(
            session,
            {pid: tags for pid, tags in found_by_pic.items() if pid not in stale},
            [pid for pid in no_crop_ids if pid not in stale],
        )

    @staticmethod
    def apply_crop_tags(
        session: Session,
        found_by_pic: dict[int, set[str]],
        no_crop_ids: list[int] | None = None,
    ) -> dict:
        """Add each picture's missing crop tags and clear its re-check flag.

        The whole rule, in one transaction:

        * **Add** a found tag the picture does not carry, unless the owner
          rejected it (a human NEG ``TagPrediction``).
        * **Never delete** a ``Tag`` row. A tag the crop owns but no longer finds
          is kept, whoever wrote it: the Tag table cannot say.
        * Skip a picture that is locked, scrapheaped, gone, or carries a retag
          sentinel (its pending ``TagTask`` runs the crop and clears the flag),
          leaving its flag untouched.
        * Clear ``quality_crop_pending`` for every other picture passed in,
          including the ones that found nothing and *no_crop_ids*.

        Args:
            session: The writer session.
            found_by_pic: ``{picture_id: tags the crop found that it owns}`` for
                every picture whose crop was judged.
            no_crop_ids: Pictures that loaded but produced no crop.

        Returns:
            ``{"tags_added": int, "tagged_picture_ids": [...], "cleared_ids":
            [...], "skipped_ids": [...]}``.
        """
        candidate_ids = sorted({*found_by_pic, *(no_crop_ids or [])})
        result = {
            "tags_added": 0,
            "tagged_picture_ids": [],
            "cleared_ids": [],
            "skipped_ids": [],
        }
        if not candidate_ids:
            return result

        live_ids = set(
            session.exec(
                select(Picture.id).where(
                    Picture.id.in_(candidate_ids), Picture.deleted.is_(False)
                )
            ).all()
        )
        # A pending retag is re-checked by its own TagTask; writing here would
        # add tags beside a sentinel the tagger is about to process.
        sentinel_ids = set(
            session.exec(
                select(Tag.picture_id).where(
                    Tag.picture_id.in_(candidate_ids),
                    Tag.tag.like(
                        TAG_SENTINEL_LIKE_PATTERN, escape=TAG_SENTINEL_ESCAPE_CHAR
                    ),
                )
            ).all()
        )
        # A locked set freezes its members' confirmed tags (and this task
        # writes nothing at all to a frozen picture, not even the flag).
        locked = locked_picture_ids(session, candidate_ids)
        eligible = [
            pid
            for pid in candidate_ids
            if pid in live_ids and pid not in sentinel_ids and pid not in locked
        ]
        result["skipped_ids"] = [pid for pid in candidate_ids if pid not in eligible]
        if not eligible:
            return result

        existing: dict[int, set[str]] = {}
        for pid, tag in session.exec(
            select(Tag.picture_id, Tag.tag).where(
                Tag.picture_id.in_(eligible), Tag.tag.is_not(None)
            )
        ).all():
            existing.setdefault(pid, set()).add(tag)
        human_neg: dict[int, set[str]] = {}
        for pid, tag in session.exec(
            select(TagPrediction.picture_id, TagPrediction.tag).where(
                TagPrediction.picture_id.in_(eligible),
                TagPrediction.label_source == "human",
                TagPrediction.label_state == "NEG",
            )
        ).all():
            human_neg.setdefault(pid, set()).add(tag)

        to_add: dict[int, set[str]] = {}
        for pid in eligible:
            missing = (
                set(found_by_pic.get(pid) or ())
                - existing.get(pid, set())
                - human_neg.get(pid, set())
            )
            if missing:
                to_add[pid] = missing

        # Applied tags feed the anomaly score (a model prediction is charged only
        # when its tag is applied), so the write is observed, as `_add_tags_bulk`
        # observes TagTask's.
        with invalidate_on_anomaly_change(
            session, list(to_add), context="quality crop re-check"
        ):
            for pid, tags in to_add.items():
                for tag in sorted(tags):
                    session.add(Tag(picture_id=pid, tag=tag))
            session.flush()
            if to_add:
                # Prediction status mirrors the applied set (CONFIRMED when the
                # tag is applied), which is what `_resolve_pending_predictions`
                # restores after TagTask's write. Adding tags can only move the
                # rows for the tags added, so only those are flipped: the
                # whole-picture resolver would also rewrite rows this pass made
                # no decision about, and commits on its own. Human-labelled rows
                # are never flipped; confidences are never touched.
                for row in session.exec(
                    select(TagPrediction).where(
                        TagPrediction.picture_id.in_(list(to_add)),
                        TagPrediction.status != "CONFIRMED",
                    )
                ).all():
                    if row.label_source == "human":
                        continue
                    if row.tag in to_add.get(row.picture_id, set()):
                        row.status = "CONFIRMED"
                for pid in to_add:
                    recompute_anomaly_tag_uncertainty(session, pid)
                session.flush()
        session.exec(
            update(Picture)
            .where(Picture.id.in_(eligible))
            .values(quality_crop_pending=False)
        )
        session.commit()

        result["tags_added"] = sum(len(tags) for tags in to_add.values())
        result["tagged_picture_ids"] = sorted(to_add)
        result["cleared_ids"] = eligible
        return result
