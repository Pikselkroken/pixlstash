from typing import Callable

from sqlmodel import Session, select
from sqlalchemy.orm import selectinload

from pixlstash.db_models import (
    Picture,
    Tag,
    TAG_PENDING_SENTINEL,
    is_tag_sentinel,
    parse_tag_engine_from_sentinel,
)
from pixlstash.services.set_lock_service import locked_picture_id_subquery
from pixlstash.worker_config import TAGGER_MAX_INFLIGHT
from .base_task_finder import BaseTaskFinder
from .tag_task import TagTask

# Every sentinel value starts with TAG_PENDING_SENTINEL (``__tag`` or
# ``__tag:<engine>``), so the half-open range [``__tag``, ``__tah``) is the
# sentinel set. Written as a range rather than the LIKE pattern because SQLite
# cannot serve LIKE ... ESCAPE from an index: this form walks ``ix_tag_tag``.
_SENTINEL_RANGE_END = TAG_PENDING_SENTINEL[:-1] + chr(ord(TAG_PENDING_SENTINEL[-1]) + 1)


class MissingTagFinder(BaseTaskFinder):
    """Find a batch of pictures with a pending-retag sentinel and create a TagTask."""

    def __init__(
        self,
        database,
        engine_getter: Callable,
    ):
        super().__init__()
        self._db = database
        self._engine_getter = engine_getter

    def finder_name(self) -> str:
        return "MissingTagFinder"

    def max_inflight_tasks(self) -> int:
        return TAGGER_MAX_INFLIGHT

    # No `on_all_tasks_complete`: the drain used to tear the WD14 session down,
    # and with per-row dependencies a finder drains and refills many times per
    # pass, so that was a reload storm. The session is bounded by its own
    # `gpu_mem_limit` now, and `Vault._maybe_aggressive_unload` frees it once
    # every worker has been idle - only under `keep_models_in_memory=False`.

    def find_task(self):
        engine = self._engine_getter()
        if engine is None:
            return None
        # Only queue tagging when an active tag plugin is configured.
        tagger_settings = getattr(engine, "tagger_settings", None)
        active_tag_plugin = (tagger_settings or {}).get("active_tag_plugin")
        if not active_tag_plugin:
            return None

        batch_limit = max(
            1,
            int(engine.tagging_workflow.suggested_task_size()),
        )
        # Fetch enough candidates that _filter_and_claim can always fill one
        # additional task even when all max_inflight slots are already in-flight.
        max_inflight = max(1, self.max_inflight_tasks())
        pictures = self._db.run_immediate_read_task(
            lambda session: self._fetch_missing_tags(
                session, batch_limit * (max_inflight + 1)
            )
        )
        if not pictures:
            return None

        # Group pictures by their requested engine (None = use active_tag_plugin).
        groups: dict[str | None, list] = {}
        for pic in pictures:
            sentinel_tag = next(
                (t.tag for t in pic.tags if is_tag_sentinel(t.tag)),
                None,
            )
            engine_name = parse_tag_engine_from_sentinel(sentinel_tag)
            groups.setdefault(engine_name, []).append(pic)

        # Process the first group only (subsequent calls will handle the rest).
        first_engine, first_pics = next(iter(groups.items()))
        selected = self._filter_and_claim(first_pics, batch_limit)
        if not selected:
            return None

        return TagTask(
            database=self._db,
            tagging_workflow=engine.tagging_workflow,
            pictures=selected,
            engine_override=first_engine,
        )

    @staticmethod
    def _fetch_missing_tags(session: Session, limit: int):
        pending = select(Tag.picture_id).where(
            Tag.tag >= TAG_PENDING_SENTINEL, Tag.tag < _SENTINEL_RANGE_END
        )
        # Per-row stage dependency: tag a picture once ITS faces are known, not
        # once the whole face stage has drained. Face extraction always writes
        # at least one Face row (a ``face_index=-1`` sentinel when there is no
        # face), so ``faces.any()`` is exactly "face extraction has run". The
        # quality crop in TagTask only *prefers* a face (it centre-crops
        # without one), but cropping on the face is the shipped behaviour, so
        # the picture waits for it.
        faces_known = Picture.faces.any()
        # A picture frozen by a locked set keeps its confirmed tags: never re-queue
        # it for tagging (the write-side skip in TagTask is the belt; this is the
        # braces, and it avoids re-queuing a locked picture that still carries a
        # stray retag sentinel on every finder pass).
        #
        # Must be the shared set_lock_service predicate, not a local
        # PictureSetMember join. The local join had no stack arm, while TagTask's
        # write guard (`locked_picture_ids`) does: a picture merely *sharing a
        # stack* with a locked-set member was therefore selected here, ran full
        # GPU tagging, had its write skipped, kept its retag sentinel, and was
        # selected again on the very next sweep - an unbounded inference loop.
        # One definition on both sides is what closes it.
        locked_member = ~Picture.id.in_(locked_picture_id_subquery())
        return session.exec(
            select(Picture)
            .where(Picture.id.in_(pending), faces_known, locked_member)
            .options(
                selectinload(Picture.tags),
            )
            .order_by(Picture.id)
            .limit(limit)
        ).all()
