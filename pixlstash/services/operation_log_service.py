"""The operation log: record metadata changes, then undo/redo them (DAM 1.2).

Design in one paragraph. Instead of teaching every mutating endpoint how to
invert itself, the log records the **metadata state of the affected pictures
before and after** the mutation, keeping only the facets that actually changed.
Undo then means "write the recorded ``before`` back"; redo means "write the
recorded ``after`` back". That makes the applier uniform, makes a new mutating
endpoint undoable by wrapping its DB task (no inverse logic to write, none to
get wrong), and produces exactly the ``{before, after}`` payload the DAM roadmap
specifies for the audit log.

Scope discipline (DAM 1.2, binding): only the **metadata** facets in
:data:`FACETS` are captured and reversible - tags, the tag-prediction rows and
their human-label ledger, caption/description, rating, picture-set / project /
character membership, stacking, and the scrapheap soft-delete state. A facet is
either whole or absent: a tag decision that also writes the ledger records both
sides, because restoring the tag and leaving the ledger's rejection standing
would look undone while the tagger still treated the tag as refused (§21.2).
Values *derived* from those facets - ``anomaly_tag_uncertainty`` and the cached
``smart_score`` - are deliberately NOT snapshotted; they are recomputed and
invalidated on restore through the same guards the forward path uses, because a
snapshot of a derived value is a second source of truth waiting to drift.

**An operation is ``undoable=True`` exactly when the log stores the whole prior
state.** That is the principle behind the file-mutating rule, and it is why
``FACET_ORIENTATION`` is an exception to the rule rather than a hole in it. A
crop or a re-encode destroys information that exists nowhere but the prior file,
so those stay ``undoable=False`` until copy-on-write versions land (Stage 2 /
v2.1); an in-place rotate replaces one enumerated value 1-8 and copies the
entropy-coded stream through byte for byte, so ``{"orientation": n}`` IS the
whole prior state. A **permanent** delete (scrapheap purge, Empty Scrapheap,
retention auto-purge) destroys the file and is deliberately *not* recorded here:
there is nothing an undo could put back.

Atomicity: :func:`run_recorded_metadata_task` runs capture → mutation → capture →
record inside **one** DB-queue task, so the ``Operation`` row and the change it
describes commit against the same serialised writer. A separate before-read on
the caller's thread would leave a window in which another write lands between
the snapshot and the mutation and gets silently attributed to this operation.

Origin discipline (§15, binding): ``source`` / ``origin_client_id`` are passed in
**explicitly** by the caller, which read them from the request at request time.
This module never reads ``origin_client_id_var`` - the contextvar is dead on the
DB worker thread and on the broadcaster's loop, and a read here would be the same
silent-attribution bug ``test_source_origin_read_from_data_only`` exists to
prevent. Events emitted after an undo carry the origin in the event ``data`` dict
for the same reason.
"""

from __future__ import annotations

import importlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Iterable, Optional, Union

from fastapi import HTTPException
from sqlalchemy import event as sa_event
from sqlalchemy import or_
from sqlmodel import Session, select

from pixlstash.db_models import (
    Detection,
    Face,
    Operation,
    Picture,
    PictureProjectMember,
    PictureSetMember,
    PictureStack,
    Tag,
    TagPrediction,
)
from pixlstash.db_models.operation import (
    STATUS_APPLIED,
    STATUS_SUPERSEDED,
    STATUS_UNDONE,
    TARGET_PICTURE,
)
from pixlstash.event_types import EventType
from pixlstash.pixl_logging import get_logger
from pixlstash.services.layout_move_service import (
    restore_location,
    rollback_applied_moves,
)
from pixlstash.services.set_lock_service import enforce_pictures_not_locked
from pixlstash.services.stack_membership import expand_picture_ids_to_stacks
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.image_processing.orientation import (
    ROTATE_CW,
    read_orientation,
    rotate_orientation,
    write_orientation,
)
from pixlstash.utils.path_utils import resolve_path_within
from pixlstash.utils.service.label_ledger import MANUAL_MODEL_VERSION, UNKNOWN
from pixlstash.utils.service.smart_score_invalidation import (
    InteractiveRescoreRegistry,
    invalidate_on_anomaly_change,
)
from pixlstash.utils.service.scope_table import scope_id_subquery
from pixlstash.utils.service.tag_prediction_utils import (
    recompute_anomaly_tag_uncertainty,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pixlstash.vault import Vault

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Facets - the metadata scope DAM 1.2 makes reversible
# ---------------------------------------------------------------------------

FACET_TAGS = "tags"
FACET_TAG_PREDICTIONS = "tag_predictions"
FACET_DESCRIPTION = "description"
FACET_SCORE = "score"
FACET_SETS = "sets"
FACET_PROJECTS = "projects"
FACET_PROJECT_ID = "project_id"
FACET_CHARACTERS = "characters"
FACET_PENDING_CHARACTER_ID = "pending_character_id"
FACET_STACK = "stack"
FACET_DELETED = "deleted"
# The EXIF orientation tag, 1-8 (§21.5). The only facet whose applier writes a
# FILE, and it is reversible for the reason the rest are: the recorded value is
# the whole prior state, not a delta.
FACET_ORIENTATION = "orientation"
# Where the picture's FILE is (``Picture.file_path``), so the v1.11 layout
# engine's batch of moves is one Ctrl+Z (§4 of the release plan). The second
# facet whose applier writes to the filesystem, and reversible for the reason
# the orientation is: the recorded value is the whole prior state - a path - and
# putting a file back at a path it just came from loses nothing. Captured for
# every operation, so a move made by any recorded route is undoable, not only
# the engine's own.
FACET_LOCATION = "location"

FACETS = (
    FACET_TAGS,
    FACET_TAG_PREDICTIONS,
    FACET_DESCRIPTION,
    FACET_SCORE,
    FACET_SETS,
    FACET_PROJECTS,
    FACET_PROJECT_ID,
    FACET_CHARACTERS,
    FACET_PENDING_CHARACTER_ID,
    FACET_STACK,
    FACET_DELETED,
    FACET_ORIENTATION,
    FACET_LOCATION,
)

# Operation types the scrapheap lifecycle records. Named constants because the
# frontend keys its undo affordances off them and they are part of the API
# contract (docs/backend_architecture.md §21).
OP_SCRAPHEAP_MOVE = "pictures.scrapheap.move"
OP_SCRAPHEAP_RESTORE = "pictures.scrapheap.restore"

# Keep cover only (docs/design/keep-cover-only.md): a stack keeps its cover and
# every other live member is soft-deleted. Named `keep_cover_only`, never
# `squash`: in git that word means "merge without losing content", so a
# git-literate reader grepping it would assume this action loses nothing.
OP_STACK_KEEP_COVER_ONLY = "stack.keep_cover_only"

# The tag-review decisions (§21.2). Named for the same reason the scrapheap pair
# is: the frontend keys its icon/receipt affordances off the string.
OP_TAGS_CONFIRM = "pictures.tags.confirm"
OP_TAGS_REJECT = "pictures.tags.reject"

# In-place rotate (§21.5). ONE op_type for all three directions: the direction
# lives in the request and in the summary, never in the recorded state. The state
# stores the resulting orientation absolutely, which is what makes undo
# idempotent and lets it converge on a file something else has since turned.
OP_PICTURES_ROTATE = "pictures.rotate"

# HTTP status for "an undo target was permanently purged and cannot come back".
# 410 Gone, not 404: the picture demonstrably existed and was destroyed, and the
# operation row that named it is still there.
PURGED_STATUS_CODE = 410

# How many operations the API will ever return in one page.
MAX_LIST_LIMIT = 500

# Facet -> the event this facet's restoration should announce. CHANGED_PICTURES
# is the catch-all the grid listens to; the tag/character events additionally
# refresh the sidebar counts.
_FACET_EVENTS = {
    FACET_TAGS: (EventType.CHANGED_TAGS, EventType.CHANGED_PICTURES),
    FACET_TAG_PREDICTIONS: (EventType.CHANGED_TAGS, EventType.CHANGED_PICTURES),
    FACET_DESCRIPTION: (EventType.CHANGED_DESCRIPTIONS, EventType.CHANGED_PICTURES),
    FACET_SCORE: (EventType.CHANGED_PICTURES,),
    FACET_SETS: (EventType.CHANGED_PICTURES,),
    FACET_PROJECTS: (EventType.CHANGED_PICTURES,),
    FACET_PROJECT_ID: (EventType.CHANGED_PICTURES,),
    FACET_CHARACTERS: (EventType.CHANGED_CHARACTERS, EventType.CHANGED_PICTURES),
    FACET_PENDING_CHARACTER_ID: (EventType.CHANGED_CHARACTERS,),
    FACET_STACK: (EventType.CHANGED_PICTURES,),
    FACET_DELETED: (EventType.CHANGED_PICTURES,),
    FACET_LOCATION: (EventType.CHANGED_PICTURES,),
}


class OperationLogError(Exception):
    """Raised when an undo/redo request cannot be honoured as asked."""


# ---------------------------------------------------------------------------
# State capture
# ---------------------------------------------------------------------------


def _normalize_ids(picture_ids: Iterable[Any]) -> list[int]:
    """Sorted, de-duplicated, positive int ids; non-numeric entries dropped."""
    ids: set[int] = set()
    for raw in picture_ids or ():
        try:
            value = int(raw)
        except (TypeError, ValueError):
            logger.warning(
                "operation_log: ignoring non-numeric picture id %r in target list",
                raw,
            )
            continue
        if value > 0:
            ids.add(value)
    return sorted(ids)


def _naive_utc_iso(value: Optional[datetime]) -> Optional[str]:
    """Serialise a ``deleted_at`` stamp for the recorded state.

    Every ``Picture.deleted_at`` in the DB is **naive UTC** (SQLAlchemy's SQLite
    ``DateTime`` drops the offset on write - see ``scrapheap_service._naive_utc``),
    but a value just assigned in-session is still aware. Normalising both to
    naive-UTC ISO here keeps the before/after comparison honest: without it the
    same instant would compare unequal across a commit boundary and every capture
    would look like a change.
    """
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.isoformat()


def _parse_naive_utc(value) -> Optional[datetime]:
    """Inverse of :func:`_naive_utc_iso`, tolerant of a malformed stored value."""
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            logger.error(
                "operation_log: could not parse recorded deleted_at %r; restoring "
                "the picture without a retention stamp (it will never auto-purge "
                "until it is re-deleted)",
                value,
            )
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def capture_state_in_session(session: Session, picture_ids) -> dict[str, dict]:
    """Snapshot every reversible metadata facet of *picture_ids*.

    Args:
        session: Pre-opened DB session.
        picture_ids: Picture ids to snapshot. Missing ids are simply absent from
            the result (a picture created by the operation has no before-state).

    Returns:
        ``{"<picture_id>": {facet: value}}`` with string keys, so the structure
        round-trips through JSON unchanged.
    """
    ids = _normalize_ids(picture_ids)
    if not ids:
        return {}

    picture_scope = scope_id_subquery(
        session, ids, name="_pixlstash_operation_picture_ids"
    )
    state: dict[str, dict] = {}
    pictures = session.exec(select(Picture).where(Picture.id.in_(picture_scope))).all()
    for picture in pictures:
        if picture.id is None:
            continue
        state[str(int(picture.id))] = {
            FACET_DESCRIPTION: picture.description,
            FACET_SCORE: picture.score,
            FACET_PROJECT_ID: picture.project_id,
            FACET_PENDING_CHARACTER_ID: picture.pending_character_id,
            # Read from the COLUMN, never from the file. This capture runs twice
            # for every recorded operation over every affected picture, so
            # opening the file here would make a 2,700-row tag edit do 5,400 file
            # opens on the single DB writer thread. The column is a mirror kept
            # by ``apply_orientation`` and backfilled by
            # ``MissingOrientationFinder``.
            FACET_ORIENTATION: picture.orientation,
            FACET_LOCATION: picture.file_path,
            FACET_TAGS: [],
            FACET_TAG_PREDICTIONS: {},
            FACET_SETS: [],
            FACET_PROJECTS: [],
            FACET_CHARACTERS: {},
            FACET_STACK: {
                "id": picture.stack_id,
                "name": None,
                "position": picture.stack_position,
            },
            # The scrapheap lifecycle: the soft-delete flag and the retention
            # stamp travel together, because restoring one without the other
            # either loses the purge deadline or leaves a live picture carrying
            # a stale one.
            FACET_DELETED: {
                "deleted": bool(picture.deleted),
                "deleted_at": _naive_utc_iso(picture.deleted_at),
            },
        }
    if not state:
        return {}

    for picture_id, tag in session.exec(
        select(Tag.picture_id, Tag.tag).where(Tag.picture_id.in_(picture_scope))
    ).all():
        state[str(int(picture_id))][FACET_TAGS].append(tag)

    for (
        picture_id,
        tag,
        model_version,
        confidence,
        status,
        predicted_at,
        label_state,
        label_source,
        labeled_at,
        label_model_version,
        label_confidence,
    ) in session.exec(
        select(
            TagPrediction.picture_id,
            TagPrediction.tag,
            TagPrediction.model_version,
            TagPrediction.confidence,
            TagPrediction.status,
            TagPrediction.predicted_at,
            TagPrediction.label_state,
            TagPrediction.label_source,
            TagPrediction.labeled_at,
            TagPrediction.label_model_version,
            TagPrediction.label_confidence,
        ).where(TagPrediction.picture_id.in_(picture_scope))
    ).all():
        state[str(int(picture_id))][FACET_TAG_PREDICTIONS][tag] = {
            # The tagger's own live fields. Captured so a row the operation itself
            # invented can be rebuilt on redo - never written back onto a row that
            # still exists (see :func:`_apply_tag_predictions`).
            "model_version": model_version,
            "confidence": confidence,
            # The human-decision fields: the review status and the label ledger.
            "status": status,
            "predicted_at": _naive_utc_iso(predicted_at),
            "label_state": label_state,
            "label_source": label_source,
            "labeled_at": _naive_utc_iso(labeled_at),
            "label_model_version": label_model_version,
            "label_confidence": label_confidence,
        }

    for picture_id, set_id in session.exec(
        select(PictureSetMember.picture_id, PictureSetMember.set_id).where(
            PictureSetMember.picture_id.in_(picture_scope)
        )
    ).all():
        state[str(int(picture_id))][FACET_SETS].append(int(set_id))

    for picture_id, project_id in session.exec(
        select(PictureProjectMember.picture_id, PictureProjectMember.project_id).where(
            PictureProjectMember.picture_id.in_(picture_scope)
        )
    ).all():
        state[str(int(picture_id))][FACET_PROJECTS].append(int(project_id))

    for face_id, picture_id, character_id in session.exec(
        select(Face.id, Face.picture_id, Face.character_id).where(
            Face.picture_id.in_(picture_scope)
        )
    ).all():
        state[str(int(picture_id))][FACET_CHARACTERS][str(int(face_id))] = (
            int(character_id) if character_id is not None else None
        )

    # Stack names, so a dissolved stack can be recreated on undo rather than
    # leaving the picture pointing at a row that no longer exists.
    stack_ids = {
        entry[FACET_STACK]["id"]
        for entry in state.values()
        if entry[FACET_STACK]["id"] is not None
    }
    if stack_ids:
        stack_scope = scope_id_subquery(
            session, stack_ids, name="_pixlstash_operation_stack_ids"
        )
        names = dict(
            session.exec(
                select(PictureStack.id, PictureStack.name).where(
                    PictureStack.id.in_(stack_scope)
                )
            ).all()
        )
        for entry in state.values():
            stack_id = entry[FACET_STACK]["id"]
            if stack_id is not None:
                entry[FACET_STACK]["name"] = names.get(stack_id)

    for entry in state.values():
        entry[FACET_TAGS].sort()
        entry[FACET_SETS].sort()
        entry[FACET_PROJECTS].sort()

    return state


def diff_states(
    before: dict[str, dict], after: dict[str, dict]
) -> tuple[dict[str, dict], dict[str, dict]]:
    """Reduce two full snapshots to only the facets that differ.

    A picture whose facets are all unchanged disappears from both sides, so an
    endpoint that ran but changed nothing records no operation at all.

    Args:
        before: Snapshot taken before the mutation.
        after: Snapshot taken after it.

    Returns:
        ``(before_delta, after_delta)`` - same keys on both sides, each mapping a
        picture id to only its changed facets. A facet missing on one side (the
        picture did not exist yet, or no longer does) is recorded as ``None`` so
        the applier can tell "unchanged" from "absent".
    """
    before_delta: dict[str, dict] = {}
    after_delta: dict[str, dict] = {}
    for picture_id in sorted(set(before) | set(after), key=lambda k: int(k)):
        old = before.get(picture_id)
        new = after.get(picture_id)
        if old is None and new is None:
            continue
        changed_old: dict[str, Any] = {}
        changed_new: dict[str, Any] = {}
        for facet in FACETS:
            old_value = None if old is None else old.get(facet)
            new_value = None if new is None else new.get(facet)
            if old_value == new_value:
                continue
            changed_old[facet] = old_value
            changed_new[facet] = new_value
        if changed_old or changed_new:
            before_delta[picture_id] = changed_old
            after_delta[picture_id] = changed_new
    return before_delta, after_delta


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


# A summary is either a fixed sentence or a ``(before_delta, after_delta) -> str``
# builder evaluated once the diff is known. The callable form exists for handlers
# whose real target count is not knowable at call time - a bulk soft-delete that
# skips locked pictures, or a "restore everything" that never named an id.
SummarySpec = Union[str, Callable[[dict, dict], Optional[str]], None]


SERVER_BATCH_ID_PREFIX = "srv-"
"""Namespace for a batch id the *server* minted.

A batch id can also arrive from a client - the ``cli-`` shape, validated at
both request boundaries (the ``X-Operation-Batch-Id`` header in
``utils/request_origin.py``, the dedup verdict body field in
``routes/dedup.py``) - and the two must be distinguishable in the log: an
un-namespaced id makes a client-supplied grouping key indistinguishable from a
server-minted one, so a client could graft its rows into what reads as a
server batch. Every minting site in the backend goes through
:func:`new_batch_id`.
"""


def new_batch_id() -> str:
    """Return a fresh opaque batch id grouping one bulk action's operations."""
    return f"{SERVER_BATCH_ID_PREFIX}{uuid.uuid4().hex}"


def lifecycle_split(state: dict[str, dict]) -> tuple[list[int], list[int]]:
    """Split *state* into the pictures it scrapheaps and the ones it restores.

    Args:
        state: A recorded (already-diffed) state - the side about to be written.

    Returns:
        ``(scrapheaped_ids, restored_ids)``: pictures whose :data:`FACET_DELETED`
        target value is ``True`` and ``False`` respectively. Pictures the state
        does not move in or out of the scrapheap appear in neither list.
    """
    scrapheaped: list[int] = []
    restored: list[int] = []
    for picture_id, facets in (state or {}).items():
        lifecycle = (facets or {}).get(FACET_DELETED)
        if not isinstance(lifecycle, dict):
            continue
        (scrapheaped if lifecycle.get("deleted") else restored).append(int(picture_id))
    return sorted(scrapheaped), sorted(restored)


def _pictures(count: int) -> str:
    return f"{count} picture" if count == 1 else f"{count} pictures"


def scrapheap_move_summary(before: dict, after: dict) -> Optional[str]:
    """Build ``Moved 5 pictures to the Scrapheap`` from the recorded diff.

    Counting the diff rather than the request is what makes the sentence true:
    a bulk soft-delete silently skips pictures frozen by a locked set and
    already-scrapheaped ones, and neither shows up in the recorded change.
    """
    moved, _restored = lifecycle_split(after)
    if not moved:
        return None
    return f"Moved {_pictures(len(moved))} to the Scrapheap"


def scrapheap_restore_summary(before: dict, after: dict) -> Optional[str]:
    """Build ``Restored 5 pictures from the Scrapheap`` from the recorded diff."""
    _moved, restored = lifecycle_split(after)
    if not restored:
        return None
    return f"Restored {_pictures(len(restored))} from the Scrapheap"


def keep_cover_only_summary(stack_count: int, moved_count: int) -> str:
    """Build ``Kept the cover of 3 stacks · 7 pictures to the Scrapheap``.

    A plain string, built from the collapse **plan** rather than from the diff
    like :func:`scrapheap_move_summary` is. The plan is already the filtered
    truth here: skipped stacks (a locked-set member, a character link that
    lives only on a copy) are not in it, so both figures are counted directly
    and neither is derived by subtracting one query's answer from another's.

    The sentence names what you keep *and* what moves, mirroring the confirm
    dialog's title/button pairing, and it deliberately claims no space was
    freed, because a soft delete frees none.

    Args:
        stack_count: Stacks actually collapsed.
        moved_count: Pictures actually soft-deleted to the Scrapheap.
    """
    stacks = f"{stack_count} stack" if stack_count == 1 else f"{stack_count} stacks"
    return f"Kept the cover of {stacks} · {_pictures(moved_count)} to the Scrapheap"


def request_context(request, *, fallback_batch_id: Optional[str] = None) -> dict:
    """Actor + WS-envelope provenance + gesture batch, read from the request.

    Call this **in the handler**, on the request's own task. The values are then
    passed explicitly down to the recorder and, later, into the WS event ``data``
    dict - the §15 threading rule: the ``origin_client_id`` contextvar is dead on
    the DB worker thread and on the broadcaster's loop, so nothing downstream may
    read it.

    ``batch_id`` comes from the client's ``X-Operation-Batch-Id`` header, already
    validated by ``OriginClientMiddleware`` (``cli-`` namespace, bounded length,
    safe charset; a malformed header is ignored, never a 500). It is a *grouping
    hint*: one user gesture that fans out into several requests stamps them all
    with the same id, so the whole gesture becomes one undo unit (§21.2).
    Grouping never widens what an operation may touch and ``/operations*`` is
    OWNER_ONLY, so a caller can only regroup its own history.

    Args:
        request: The FastAPI request (duck-typed; only ``request.state`` is used).
        fallback_batch_id: Batch id to use when the caller sent no usable header -
            for a handler that is a bulk action in its own right and mints a
            server-side batch id (``srv-…``) regardless.

    Returns:
        ``{"actor", "source", "origin_client_id", "batch_id"}``, ready to splat
        into :func:`run_recorded_metadata_task`. ``source`` is ``"ui"`` when the
        caller identified itself with an ``X-Client-Id`` (an in-app action) and
        ``"external"`` otherwise, mirroring the envelope's own default.
    """
    state = getattr(request, "state", None)
    user_id = getattr(state, "auth_user_id", None)
    origin_client_id = getattr(state, "origin_client_id", None)
    return {
        "actor": str(user_id) if user_id is not None else None,
        "source": "ui" if origin_client_id else "external",
        "origin_client_id": origin_client_id,
        "batch_id": getattr(state, "operation_batch_id", None) or fallback_batch_id,
    }


def record_operation_in_session(
    session: Session,
    *,
    op_type: str,
    before: dict[str, dict],
    after: dict[str, dict],
    actor: Optional[str] = None,
    source: str = "external",
    origin_client_id: Optional[str] = None,
    batch_id: Optional[str] = None,
    summary: SummarySpec = None,
    undoable: bool = True,
    target_type: str = TARGET_PICTURE,
    empty_diff_target_ids: Optional[Iterable[Any]] = None,
    commit: bool = True,
) -> Optional[Operation]:
    """Append one operation row for the diff between two snapshots.

    Args:
        session: Pre-opened DB session.
        op_type: Dotted verb naming the change (``"pictures.tags"``).
        before: Full or already-diffed before snapshot.
        after: The matching after snapshot.
        actor: Who performed the change (user id as a string).
        source: WS-envelope source, passed in explicitly by the caller.
        origin_client_id: WS-envelope per-tab origin, likewise explicit.
        batch_id: Group id when this row is part of a bulk action.
        summary: Short human sentence for the undo toast / activity feed, or a
            ``(before_delta, after_delta) -> str | None`` builder evaluated here,
            once the real extent of the change is known (see :data:`SummarySpec`).
        undoable: ``False`` records the change for audit without offering undo
            (the DAM 1.2 rule for file-mutating operations).
        target_type: Kind of object the target ids refer to.
        empty_diff_target_ids: Normally an empty diff records nothing (a no-op
            endpoint must not consume a Ctrl+Z). Pass the affected ids here for
            an operation whose *entire* reversible state lives outside the
            picture facets - the dedup keep-separate verdict is the case in
            point - and the row is recorded anyway, with empty before/after
            payloads and these target ids, so undo/redo still find it and its
            registered post-restore hook performs the whole restore. Ignored
            when the diff is non-empty.
        commit: Commit the mutation and operation row before returning. Recorded
            wrappers pass ``False`` so serialization also remains inside their
            transaction; direct domain services retain the historical default.

    Returns:
        The persisted :class:`Operation`, or ``None`` when nothing changed and
        no ``empty_diff_target_ids`` were declared.
    """
    before_delta, after_delta = diff_states(before, after)
    forced_target_ids: Optional[list[int]] = None
    if not before_delta and not after_delta:
        if empty_diff_target_ids is None:
            return None
        forced_target_ids = _normalize_ids(empty_diff_target_ids)

    if callable(summary):
        try:
            summary = summary(before_delta, after_delta)
        except Exception:
            logger.exception(
                "operation_log: summary builder for %s failed over %d changed "
                "picture(s); recording the operation without a summary",
                op_type,
                len(after_delta),
            )
            summary = None

    target_ids = forced_target_ids
    if target_ids is None:
        target_ids = sorted(
            {int(pid) for pid in after_delta} | {int(pid) for pid in before_delta}
        )

    # A new operation invalidates the redo stack: anything previously undone can
    # no longer be replayed onto a history that has moved on. The rows stay -
    # this is an append-only audit log - only their status marker advances.
    superseded = session.exec(
        select(Operation).where(Operation.status == STATUS_UNDONE)
    ).all()
    for stale in superseded:
        stale.status = STATUS_SUPERSEDED
        session.add(stale)

    operation = Operation(
        batch_id=batch_id,
        created_at=datetime.utcnow(),
        actor=actor,
        op_type=op_type,
        target_type=target_type,
        target_ids=json.dumps(target_ids),
        target_count=len(target_ids),
        before_state=json.dumps(before_delta),
        after_state=json.dumps(after_delta),
        source=source,
        origin_client_id=origin_client_id,
        undoable=bool(undoable),
        status=STATUS_APPLIED,
        summary=summary,
    )
    session.add(operation)
    session.flush()
    if commit:
        session.commit()
    session.refresh(operation)
    logger.info(
        "operation_log: recorded %s id=%s batch=%s targets=%d undoable=%s",
        op_type,
        operation.id,
        batch_id,
        len(target_ids),
        undoable,
    )
    return operation


def run_recorded_metadata_task(
    vault: "Vault",
    work: Callable[..., Any],
    *args,
    op_type: str,
    picture_ids,
    actor: Optional[str] = None,
    source: str = "external",
    origin_client_id: Optional[str] = None,
    batch_id: Optional[str] = None,
    summary: SummarySpec = None,
    undoable: bool = True,
    expand_stacks: bool = False,
    expand_stacks_include_deleted: bool = False,
    resolve_picture_ids: Optional[Callable[[Session], Iterable[Any]]] = None,
    **kwargs,
) -> tuple[Any, Optional[dict]]:
    """Run a metadata mutation on the DB queue and record it in one task.

    Drop-in replacement for ``vault.db.run_task(work, *args)`` at a metadata
    mutation site: the surrounding snapshot/diff/record work happens on the same
    session, in the same queue slot, so no concurrent write can slip between the
    snapshot and the mutation.

    Args:
        vault: The vault owning the DB work queue.
        work: The existing ``(session, *args) -> result`` mutation callable.
        *args: Positional arguments forwarded to *work* after the session.
        op_type: Dotted verb naming the change.
        picture_ids: The pictures the mutation may touch.
        actor: Who is performing the change.
        source: WS-envelope source, read from the request by the caller.
        origin_client_id: WS-envelope per-tab origin, likewise.
        batch_id: Group id when this call is one step of a bulk action.
        summary: Short human sentence for the undo toast, or a
            ``(before_delta, after_delta) -> str | None`` builder (:data:`SummarySpec`).
        undoable: ``False`` records for audit only.
        expand_stacks: Snapshot the whole stack of every target picture, for
            mutations that are stack-atomic (project/set membership).
        expand_stacks_include_deleted: With *expand_stacks*, also pull in
            soft-deleted stack members. The scrapheap operations need it because
            ``normalize_stack_positions`` renumbers deleted members too, and an
            unsnapshotted renumber is a change undo could not reverse.
        resolve_picture_ids: Optional ``(session) -> ids`` run **before** the
            mutation, on the mutation's own session, for handlers whose targets
            are not knowable from the request alone - a request addressed by face
            id, or a replace-all that evicts members it was never told about. Its
            result is unioned with *picture_ids*; without it those pictures fall
            outside the snapshot and the operation records a half-change that
            undo could not fully reverse.
        **kwargs: Keyword arguments forwarded to *work*.

    Returns:
        ``(work_result, operation_dict_or_None)``.
    """

    def _task(session: Session):
        commit_guard_installed = False

        def _forbid_callback_commit(_session) -> None:
            raise RuntimeError(
                "A recorded metadata callback attempted to commit independently"
            )

        try:
            ids = _normalize_ids(picture_ids)
            if resolve_picture_ids is not None:
                ids = _normalize_ids([*ids, *(resolve_picture_ids(session) or ())])
            if expand_stacks and ids:
                ids = _normalize_ids(
                    expand_picture_ids_to_stacks(
                        session, ids, include_deleted=expand_stacks_include_deleted
                    )
                )
            before = capture_state_in_session(session, ids)
            # Prevent a newly-added callback (or a helper it calls) from quietly
            # reintroducing the split transaction this wrapper exists to avoid.
            # Raising in before_commit leaves the transaction open, so the
            # rollback below can still erase every pending domain write.
            sa_event.listen(session, "before_commit", _forbid_callback_commit)
            commit_guard_installed = True
            result = work(session, *args, **kwargs)
            after = capture_state_in_session(session, ids)
            operation = record_operation_in_session(
                session,
                op_type=op_type,
                before=before,
                after=after,
                actor=actor,
                source=source,
                origin_client_id=origin_client_id,
                batch_id=batch_id,
                summary=summary,
                undoable=undoable,
                commit=False,
            )
            serialized = serialize(operation) if operation is not None else None
            sa_event.remove(session, "before_commit", _forbid_callback_commit)
            commit_guard_installed = False
            session.commit()
            return result, serialized
        except BaseException:
            # The wrapper owns the only transaction boundary. Capture, domain
            # mutation, diff/JSON construction, Operation insertion and receipt
            # serialization either all succeed or none of them survive.
            if commit_guard_installed:
                sa_event.remove(session, "before_commit", _forbid_callback_commit)
            session.rollback()
            raise

    return vault.db.run_task(_task)


# ---------------------------------------------------------------------------
# Applying a recorded state (undo / redo)
# ---------------------------------------------------------------------------


def _apply_tags(session: Session, picture_id: int, tags) -> None:
    wanted = {str(tag) for tag in tags or []}
    existing = {
        row.tag: row
        for row in session.exec(select(Tag).where(Tag.picture_id == picture_id)).all()
    }
    for tag, row in existing.items():
        if tag not in wanted:
            session.delete(row)
    for tag in sorted(wanted - set(existing)):
        session.add(Tag(picture_id=picture_id, tag=tag))


def _apply_tag_predictions(session: Session, picture_id: int, predictions) -> None:
    """Restore the picture's prediction rows and their human-label ledger.

    Two rules keep this from rolling back work the operation never did:

    1. **The tagger's live fields are not written back onto a surviving row.**
       ``model_version`` / ``confidence`` belong to the model, and no human
       decision moves them, so restoring them could only revert a *tagger* run
       that happened after the operation. They are used solely to rebuild a row
       the recorded state has and the DB no longer does (a redo re-creating the
       synthetic row its undo deleted).
    2. **Only a synthetic ``manual`` row is deleted when the state omits it.**
       A user decision is the one thing that can *create* a prediction row
       (``record_human_label`` invents a ``model_version='manual'`` row for a tag
       the tagger never predicted), so that is the only kind an undo may remove.
       A real tagger row written since the recording is left alone and logged -
       deleting it would silently discard model output nobody asked to revert.

    Args:
        session: Pre-opened DB session; the caller commits.
        picture_id: Picture whose prediction rows are being restored.
        predictions: The recorded ``{tag: {field: value}}`` map for this picture.
    """
    if not isinstance(predictions, dict):
        return
    existing = {
        row.tag: row
        for row in session.exec(
            select(TagPrediction).where(TagPrediction.picture_id == picture_id)
        ).all()
    }
    for tag, fields in predictions.items():
        if not isinstance(fields, dict):
            logger.warning(
                "operation_log: recorded prediction state for tag %r on picture %d "
                "is %s, not a mapping; it cannot be restored",
                tag,
                picture_id,
                type(fields).__name__,
            )
            continue
        row = existing.get(tag)
        if row is None:
            row = TagPrediction(
                picture_id=picture_id,
                tag=tag,
                confidence=float(fields.get("confidence") or 0.0),
                model_version=str(fields.get("model_version") or MANUAL_MODEL_VERSION),
                predicted_at=_parse_naive_utc(fields.get("predicted_at")),
            )
        row.status = fields.get("status") or "PENDING"
        row.label_state = fields.get("label_state") or UNKNOWN
        row.label_source = fields.get("label_source")
        row.labeled_at = _parse_naive_utc(fields.get("labeled_at"))
        row.label_model_version = fields.get("label_model_version")
        label_confidence = fields.get("label_confidence")
        row.label_confidence = (
            float(label_confidence) if label_confidence is not None else None
        )
        session.add(row)

    kept: list[str] = []
    for tag, row in existing.items():
        if tag in predictions:
            continue
        if row.model_version == MANUAL_MODEL_VERSION:
            session.delete(row)
        else:
            kept.append(tag)
    if kept:
        logger.info(
            "operation_log: picture %d has %d tagger prediction row(s) written "
            "since this operation was recorded (%s); they are left in place "
            "because only the synthetic 'manual' rows a human decision creates "
            "may be removed by an undo",
            picture_id,
            len(kept),
            ", ".join(sorted(kept)[:10]),
        )


def _apply_sets(session: Session, picture_id: int, set_ids) -> None:
    wanted = {int(sid) for sid in set_ids or []}
    existing = {
        int(row.set_id): row
        for row in session.exec(
            select(PictureSetMember).where(PictureSetMember.picture_id == picture_id)
        ).all()
    }
    for set_id, row in existing.items():
        if set_id not in wanted:
            session.delete(row)
    for set_id in sorted(wanted - set(existing)):
        session.add(PictureSetMember(set_id=set_id, picture_id=picture_id))


def _apply_projects(session: Session, picture_id: int, project_ids) -> None:
    wanted = {int(pid) for pid in project_ids or []}
    existing = {
        int(row.project_id): row
        for row in session.exec(
            select(PictureProjectMember).where(
                PictureProjectMember.picture_id == picture_id
            )
        ).all()
    }
    for project_id, row in existing.items():
        if project_id not in wanted:
            session.delete(row)
    for project_id in sorted(wanted - set(existing)):
        session.add(PictureProjectMember(picture_id=picture_id, project_id=project_id))


def _apply_characters(session: Session, picture_id: int, assignments) -> None:
    if not isinstance(assignments, dict):
        return
    faces = {
        int(face.id): face
        for face in session.exec(
            select(Face).where(Face.picture_id == picture_id)
        ).all()
    }
    for face_id_raw, character_id in assignments.items():
        try:
            face_id = int(face_id_raw)
        except (TypeError, ValueError):
            logger.warning(
                "operation_log: skipping non-numeric face id %r while restoring "
                "character assignment on picture %s",
                face_id_raw,
                picture_id,
            )
            continue
        face = faces.get(face_id)
        if face is None:
            # The face row was re-extracted (or removed) after the operation was
            # recorded. Nothing to restore; log so the gap is visible rather than
            # silently dropped.
            logger.warning(
                "operation_log: face %d of picture %d no longer exists; its "
                "character assignment could not be restored",
                face_id,
                picture_id,
            )
            continue
        face.character_id = int(character_id) if character_id is not None else None
        session.add(face)


def _apply_stack(
    session: Session,
    picture: Picture,
    stack,
    vacated_stack_ids: Optional[set[int]] = None,
) -> None:
    """Restore one picture's stack pointer, recreating the stack row if needed.

    Args:
        session: Pre-opened DB session; the caller commits.
        picture: The picture whose pointer is being restored.
        stack: The recorded facet value, ``{"id", "name", "position"}``.
        vacated_stack_ids: Collector for the ids of stacks this restore moves
            pictures OFF of. Whether such a stack ends up empty cannot be
            decided here - this runs per picture, and a later picture of the
            same restore may still land on the stack - so the restore checks
            the collected ids once every state is applied
            (:func:`delete_emptied_stacks`).
    """
    if not isinstance(stack, dict):
        return
    stack_id = stack.get("id")
    if stack_id is not None:
        stack_id = int(stack_id)
        if session.get(PictureStack, stack_id) is None:
            # The stack was dissolved by the operation being undone; recreate the
            # row under its original id so the restored pointer stays valid.
            session.add(PictureStack(id=stack_id, name=stack.get("name")))
            session.flush()
    if (
        vacated_stack_ids is not None
        and picture.stack_id is not None
        and picture.stack_id != stack_id
    ):
        vacated_stack_ids.add(int(picture.stack_id))
    picture.stack_id = stack_id
    position = stack.get("position")
    picture.stack_position = int(position) if position is not None else None
    session.add(picture)


def delete_emptied_stacks(session: Session, stack_ids: set[int]) -> None:
    """Delete ``PictureStack`` rows a restore has just left with no members.

    The symmetric counterpart of the recreate branch in :func:`_apply_stack`:
    undoing a dissolve recreates the stack row, so undoing a stack *creation*
    (members restored to ``stack_id=None`` or to another stack) must delete the
    row it empties - otherwise every undone stacking leaves an orphaned empty
    ``PictureStack`` behind (issue #643, CSO finding C3 in the dedup sign-off).
    Public (no underscore) because the dedup clear-decision path applies a
    recorded stack state outside a restore and owes the same hygiene.

    Only stacks with **zero** remaining members are deleted. A picture outside
    the restored operations that still points at the stack keeps it alive:
    over-deletion would break a pointer the restore never touched, which is a
    worse bug than the row leak this cleanup exists to fix.

    Args:
        session: The restore's session; the caller commits.
        stack_ids: Ids of stacks some picture was moved off of by the restore.
    """
    if not stack_ids:
        return
    # Make the in-session stack_id writes visible to the membership query below.
    session.flush()
    for stack_id in sorted(stack_ids):
        stack = session.get(PictureStack, stack_id)
        if stack is None:
            # Already gone - e.g. the restore replayed a dissolve, whose forward
            # path deleted the row itself. Nothing to clean up; logged so the
            # skip is visible rather than silent.
            logger.debug(
                "operation_log: stack %d was vacated by this restore but its "
                "row is already gone; nothing to delete",
                stack_id,
            )
            continue
        survivor = session.exec(
            select(Picture.id).where(Picture.stack_id == stack_id).limit(1)
        ).first()
        if survivor is not None:
            logger.debug(
                "operation_log: stack %d still has members (e.g. picture %d) "
                "after the restore; keeping its row",
                stack_id,
                survivor,
            )
            continue
        logger.info(
            "operation_log: deleting stack %d (name=%r) - the restore moved its "
            "last member off it, and an empty stack row would otherwise be left "
            "orphaned",
            stack_id,
            stack.name,
        )
        session.delete(stack)


# The built-in rotate plugin owns the corner-rotation maths (all four corners
# rotated as points, then the axis-aligned box of the result). Its folder name
# carries a hyphen, so it is reachable only through ``importlib``; the call is
# cached in ``sys.modules`` and paid once per process. Reusing it is the point -
# a second copy of the formula is a second thing to get wrong, and this one is
# already exercised by the copy-producing rotate plugin.
_ROTATE_PLUGIN_MODULE = "pixlstash.image_plugins.built-in.rotate"

# Quarter turns clockwise → the rotate plugin's own direction vocabulary.
_ROTATE_STEP_DIRECTIONS = {1: "90_right", 2: "180", 3: "90_left"}

# The orientations that show the stored bitmap turned a quarter turn, so the
# DISPLAYED width and height are the stored ones swapped.
_TRANSPOSED_ORIENTATIONS = frozenset({5, 6, 7, 8})


def _clockwise_steps(current: int, target: int) -> Optional[int]:
    """Quarter turns clockwise taking *current* to *target*, or ``None``.

    ``None`` means the two orientations are not a rotation apart - they differ in
    mirroring, which nothing in this codebase writes. The caller turns the file
    anyway and leaves the boxes alone rather than moving them by a transform it
    cannot derive.
    """
    value = current
    for steps in (1, 2, 3):
        value = rotate_orientation(value, ROTATE_CW)
        if value == target:
            return steps
    return None


def _rotate_picture_boxes(
    session: Session, picture: Picture, steps: int, current: int
) -> None:
    """Turn every stored face/detection box with the picture's display.

    Both box tables are stored in **EXIF-corrected** space - the extraction tasks
    load through ``load_image_bgr_reduced``, which runs ``ImageOps.exif_transpose``
    - so a change of orientation moves them even though not one pixel moved.
    ``Picture.width`` / ``height`` are RAW and stay as they are; the display size
    the transform needs is those two swapped when the *current* orientation is a
    quarter turn.
    """
    if not picture.width or not picture.height:
        logger.warning(
            "operation_log: picture %s has no stored dimensions, so its face and "
            "detection boxes cannot be turned with the orientation; they are left "
            "as they are and will be wrong until the picture is re-processed",
            picture.id,
        )
        return
    display_w, display_h = int(picture.width), int(picture.height)
    if current in _TRANSPOSED_ORIENTATIONS:
        display_w, display_h = display_h, display_w

    plugin = importlib.import_module(_ROTATE_PLUGIN_MODULE).RotatePlugin()
    transform = plugin.get_bbox_transform(
        {"direction": _ROTATE_STEP_DIRECTIONS[steps]},
        (display_w, display_h),
        None,
    )
    for model in (Face, Detection):
        for row in session.exec(
            select(model).where(model.picture_id == picture.id)
        ).all():
            box = row.bbox
            if not box or len(box) != 4:
                continue
            row.bbox = transform([int(value) for value in box])
            session.add(row)


def apply_orientation(
    session: Session,
    picture_id: int,
    orientation,
    *,
    image_root: Optional[str] = None,
) -> bool:
    """Make *picture_id*'s file carry *orientation*, and re-derive what follows.

    The single applier behind BOTH the forward rotate and its undo/redo, which is
    what makes the two agree by construction. It is **absolute, not a delta**: it
    reads what the file carries now and turns it to *orientation*, so applying the
    same value twice is a no-op (the idempotence every restore promises) and a
    file something else has since rotated converges instead of drifting.

    Only the orientation is ever recorded. Everything below is DERIVED and
    re-derived here:

    * the ``Picture.orientation`` mirror the capture reads;
    * ``Face.bbox`` and ``Detection.bbox``, which live in EXIF-corrected space;
    * ``pixel_sha`` and ``size_bytes``, because the container's bytes changed
      even though the entropy-coded stream did not;
    * ``thumbnail_width`` / ``thumbnail_height``, NULLed so
      ``MissingThumbnailFinder`` regenerates the bitmap;
    * ``image_embedding`` / ``perceptual_hash``, NULLed so
      ``MissingImageEmbeddingFinder`` recomputes them - both describe the decoded
      image, which now decodes at a different rotation.

    ``Picture.width`` / ``height`` are deliberately untouched: they describe the
    stored bitmap, which is copied through byte for byte.

    Args:
        session: Pre-opened DB session; the caller commits.
        picture_id: The picture to turn.
        orientation: The EXIF orientation to store, 1-8. ``None`` (a recorded
            state from before the mirror was backfilled) is refused rather than
            guessed at.
        image_root: Vault image root, for resolving a relative ``file_path``.
            Passed explicitly for the same reason ``origin_client_id`` is (§15):
            this runs on the DB worker thread, which has no vault handle.

    Returns:
        Whether the file was actually turned.
    """
    if orientation is None:
        logger.warning(
            "operation_log: picture %s has no recorded orientation, so this "
            "restore cannot turn its file; the row predates the orientation "
            "mirror being backfilled",
            picture_id,
        )
        return False
    picture = session.get(Picture, picture_id)
    if picture is None:
        return False

    # Refused at the SINK, not only at the route, so undo/redo inherits it the
    # same way the locked-set guard does. A picture that was library-managed when
    # it was turned and is reference-managed by the time it is undone would
    # otherwise have its external file rewritten by the restore. These are the
    # user's own files, managed outside the library: we do not write to them.
    if picture.reference_folder_id is not None:
        logger.warning(
            "operation_log: refusing to set orientation %s on picture %s - it "
            "lives in a reference folder, whose files this library does not "
            "write to; rotate it as a copy instead",
            orientation,
            picture_id,
        )
        return False

    file_path = ImageUtils.resolve_picture_path(image_root, picture.file_path)
    if not file_path or not os.path.exists(file_path):
        logger.error(
            "operation_log: cannot set orientation %s on picture %s - %r does not "
            "resolve to a readable file, so the file and the stored mirror will "
            "disagree until the picture is rotated again",
            orientation,
            picture_id,
            file_path or picture.file_path,
        )
        return False

    # `Picture.file_path` is a database value, and `resolve_picture_path` hands an
    # absolute one straight back and joins a relative one without normalising, so
    # a `..` or a stray absolute path resolves wherever it says. Every other
    # destructive sink in the product checks containment before acting
    # (`scrapheap_service.delete_files_in_session`); this one writes the user's
    # ORIGINAL bytes, so it is the last place that should be taking the row's word
    # for it. Reference folders are already refused above, so the vault root is
    # the only legitimate location left.
    #
    # Containment here is STRICT - `resolve_path_within`, which realpaths both
    # sides - and not `path_is_within`, which answers True on a purely lexical
    # pass before any symlink is resolved (#1024). What that lenience costs is
    # not what the name suggests: a symlink planted inside the library cannot
    # carry the *write* out of it, because `write_orientation` renames a
    # `mkstemp` sibling over the path and `os.replace` replaces a symlink rather
    # than following it. `read_orientation` does follow it, though, so the
    # outside file's bytes - and, through `_carry_file_identity`, its mode and
    # owner - would be copied into the library under the link's name. That is a
    # read escape wearing a write sink's clothes, and this is the sink that can
    # close it.
    #
    # The price is deliberate and paid here rather than argued away: a symlinked
    # SUBFOLDER inside the library, photos kept on a second disk, is refused too,
    # because realpath cannot tell one from a planted link. Rotate declines those
    # pictures; nothing else about them changes. `path_is_within` itself is left
    # alone, so every read path and the model shelf keep the lenient form its
    # docstring promises.
    if not image_root:
        logger.error(
            "operation_log: refusing to set orientation %s on picture %s - no "
            "library root was supplied, so its stored path %r cannot be confirmed "
            "to be inside one; the file is not touched",
            orientation,
            picture_id,
            picture.file_path,
        )
        return False
    try:
        file_path = resolve_path_within(image_root, os.path.abspath(file_path))
    except ValueError as exc:
        logger.error(
            "operation_log: refusing to set orientation %s on picture %s - its "
            "stored path %r resolves through to %s, which is outside the library "
            "root %r (%s); the file is not touched",
            orientation,
            picture_id,
            picture.file_path,
            os.path.realpath(file_path),
            image_root,
            exc,
        )
        return False

    # The FILE is the source of truth, not the column: a mirror that drifted (an
    # external edit, a rolled-back transaction whose file write survived) would
    # otherwise make this compute the turn from a lie.
    current = read_orientation(file_path)
    if current == int(orientation):
        if picture.orientation != current:
            picture.orientation = current
            session.add(picture)
        return False

    steps = _clockwise_steps(current, int(orientation))
    write_orientation(file_path, int(orientation))
    picture.orientation = int(orientation)
    if steps is None:
        logger.warning(
            "operation_log: orientation %d -> %d on picture %s is not a rotation "
            "(the mirroring differs), so the face and detection boxes were left "
            "as they are",
            current,
            int(orientation),
            picture_id,
        )
    else:
        _rotate_picture_boxes(session, picture, steps, current)

    # The container changed, so the tier-1 duplicate key and the on-disk size did
    # too. Re-derived here rather than snapshotted, like every other derived value
    # a restore touches.
    try:
        picture.pixel_sha = ImageUtils.calculate_hash_from_file_path(file_path)
        picture.size_bytes = os.path.getsize(file_path)
    except (OSError, ValueError) as exc:
        logger.warning(
            "operation_log: could not re-derive pixel_sha/size_bytes for picture "
            "%s after turning %s (%s); the picture stays out of tier-1 duplicate "
            "detection until MissingPixelShaFinder picks it up",
            picture_id,
            file_path,
            exc,
        )
        picture.pixel_sha = None
    # The stored bitmap is now upside-down relative to what the file shows.
    # NULLing the dimensions is what MissingThumbnailFinder selects on.
    picture.thumbnail_width = None
    picture.thumbnail_height = None
    # Both are computed from the DECODED image, which exif_transpose has just
    # started producing at a different rotation, so both are now describing a
    # picture that no longer exists. Left stale they are worse than absent: the
    # near-duplicate tiers compare a turned picture against its own pre-turn
    # neighbours and mis-group it. NULLing re-queues them the way every other
    # regeneration in this codebase is triggered - `ImageEmbeddingTask.fetch_work`
    # selects on `image_embedding IS NULL`, and that one task owns the perceptual
    # hash as well, so this is one finder and one pass rather than two.
    picture.image_embedding = None
    picture.perceptual_hash = None
    session.add(picture)
    return True


def _apply_deleted(session: Session, picture: Picture, lifecycle) -> None:
    """Restore a picture's scrapheap state (the soft-delete flag + its stamp)."""
    if not isinstance(lifecycle, dict):
        return
    picture.deleted = bool(lifecycle.get("deleted"))
    # Written back verbatim rather than re-stamped to "now": the recorded value
    # IS the retention deadline this state had, and re-stamping would silently
    # extend (or invent) a purge window on every undo.
    picture.deleted_at = _parse_naive_utc(lifecycle.get("deleted_at"))
    session.add(picture)


def _enforce_scrapheap_targets_exist(
    session: Session, state: dict[str, dict], action: str
) -> None:
    """Raise ``410`` if a picture this state would move in/out of the scrapheap is gone.

    The one edge case a scrapheap undo has that a metadata undo does not: the
    picture may have been **permanently purged** since - by the 30-day retention
    sweep or by Empty Scrapheap - and a purge destroys the file, so no undo can
    bring it back. Fail closed and refuse the whole request, exactly as the
    locked-set guard above does: the operation stays ``applied``, nothing is
    committed, and the caller is told which pictures are gone rather than being
    handed a half-restored batch it would have to reconcile itself.

    Only pictures carrying the :data:`FACET_DELETED` facet are checked. A purged
    picture that merely appears in some *other* operation's recorded state (a tag
    edit, a stack renumber) keeps the long-standing skip-with-a-warning
    behaviour - there is no lifecycle promise to break there.

    Args:
        session: Pre-opened DB session.
        state: The recorded state about to be applied.
        action: Human verb phrase echoed in the error detail.

    Raises:
        HTTPException: :data:`PURGED_STATUS_CODE` naming the missing pictures.
    """
    wanted = sorted(
        int(picture_id)
        for picture_id, facets in (state or {}).items()
        if isinstance((facets or {}).get(FACET_DELETED), dict)
    )
    if not wanted:
        return
    alive = {
        int(picture_id)
        for picture_id in session.exec(
            select(Picture.id).where(Picture.id.in_(wanted))
        ).all()
        if picture_id is not None
    }
    missing = [picture_id for picture_id in wanted if picture_id not in alive]
    if not missing:
        return
    logger.warning(
        "operation_log: refusing to %s - %d of %d scrapheap target(s) %s were "
        "permanently purged and cannot be brought back; the operation stays "
        "applied and nothing was written",
        action,
        len(missing),
        len(wanted),
        missing,
    )
    raise HTTPException(
        status_code=PURGED_STATUS_CODE,
        detail={
            "code": "pictures_purged",
            "action": action,
            "picture_ids": missing,
            "message": (
                f"{len(missing)} of these pictures were permanently deleted from "
                "the Scrapheap and cannot be restored. Nothing was changed."
            ),
        },
    )


def _label_bearing_ids(state: dict[str, dict]) -> list[int]:
    """Picture ids in *state* whose restoration moves the anomaly-label inputs.

    Both the applied ``Tag`` rows and the prediction/ledger rows feed
    :func:`~pixlstash.scoring.smart_score.fetch_anomaly_confidences`, so a
    restore of either facet has to re-derive what the forward path derives.
    """
    return sorted(
        int(picture_id)
        for picture_id, facets in (state or {}).items()
        if FACET_TAGS in (facets or {}) or FACET_TAG_PREDICTIONS in (facets or {})
    )


def apply_state_in_session(
    session: Session,
    state: dict[str, dict],
    action: str = "restore metadata",
    *,
    registry: Optional[InteractiveRescoreRegistry] = None,
    origin_client_id: Optional[str] = None,
    vacated_stack_ids: Optional[set[int]] = None,
    image_root: Optional[str] = None,
    applied: Optional[list] = None,
) -> list[int]:
    """Write a recorded metadata state back onto its pictures.

    A locked picture set is a hard freeze on its members' label data; undo/redo
    must not become the one write path that walks around it. The guard runs here,
    at the single sink every restore goes through, and covers every facet. The
    purged-target guard (:func:`_enforce_scrapheap_targets_exist`) sits beside it
    on the same fail-closed contract.

    **Derived data is re-derived, not restored.** ``Picture.anomaly_tag_uncertainty``
    is recomputed from the restored rows and the cached ``smart_score`` is dropped
    through the very same :func:`invalidate_on_anomaly_change` guard the forward
    tag/label writes use. Snapshotting either would invite drift: they are
    functions of the label state, so the only way an undo can leave the scorer
    honest is to recompute them from what it just wrote.

    Args:
        session: Pre-opened DB session; the caller commits.
        state: ``{"<picture_id>": {facet: value}}`` as recorded. A facet value of
            ``None`` for a collection facet means "the picture did not exist on
            this side of the change" and is skipped rather than emptied.
        action: Human verb phrase echoed in the 423 when a target is frozen.
        registry: The vault's interactive-rescore registry, so a score this
            restore invalidates refreshes the initiating tab's card immediately
            instead of waiting for the whole backfill to drain.
        origin_client_id: The tab that asked for the undo/redo, stamped onto that
            refresh. Passed in explicitly (§15) - never read from a contextvar.
        vacated_stack_ids: Optional collector, forwarded to :func:`_apply_stack`,
            of the stacks this state moves pictures off of. The caller decides
            after ALL states are applied whether those stacks ended up empty and
            deletes the emptied rows (:func:`delete_emptied_stacks`).
        image_root: Vault image root, needed only by the orientation facet, whose
            applier writes the picture's file. Passed explicitly for the same
            reason ``origin_client_id`` is (§15): this runs on the DB worker
            thread, which holds no vault handle. A state carrying an orientation
            with no ``image_root`` and a relative path is logged and skipped, not
            guessed at.

    Returns:
        The picture ids actually written.

    Raises:
        HTTPException: ``423`` when a locked picture set freezes any target;
            ``410`` when a scrapheap target has since been permanently purged.
    """
    enforce_pictures_not_locked(session, [int(pid) for pid in (state or {})], action)
    _enforce_scrapheap_targets_exist(session, state, action)
    label_ids = _label_bearing_ids(state)
    touched: list[int] = []
    # The dispatch loop stays inside this function on purpose: the lock guard
    # above is what makes every sink below safe, and extracting the loop would
    # move those sinks into a helper the locked-set guardrail can no longer see
    # as guarded (tests/test_architecture_guardrails.py, guardrail 7).
    with invalidate_on_anomaly_change(
        session,
        label_ids,
        context=action,
        registry=registry,
        origin_client_id=origin_client_id,
    ):
        for picture_id_raw, facets in (state or {}).items():
            picture_id = int(picture_id_raw)
            picture = session.get(Picture, picture_id)
            if picture is None:
                logger.warning(
                    "operation_log: picture %d no longer exists; skipping its part "
                    "of the recorded state",
                    picture_id,
                )
                continue
            for facet, value in (facets or {}).items():
                if facet == FACET_DESCRIPTION:
                    picture.description = value
                    session.add(picture)
                elif facet == FACET_SCORE:
                    picture.score = int(value) if value is not None else None
                    session.add(picture)
                elif facet == FACET_PROJECT_ID:
                    picture.project_id = int(value) if value is not None else None
                    session.add(picture)
                elif facet == FACET_PENDING_CHARACTER_ID:
                    picture.pending_character_id = (
                        int(value) if value is not None else None
                    )
                    session.add(picture)
                elif facet == FACET_TAGS:
                    if value is not None:
                        _apply_tags(session, picture_id, value)
                elif facet == FACET_TAG_PREDICTIONS:
                    if value is not None:
                        _apply_tag_predictions(session, picture_id, value)
                elif facet == FACET_SETS:
                    if value is not None:
                        _apply_sets(session, picture_id, value)
                elif facet == FACET_PROJECTS:
                    if value is not None:
                        _apply_projects(session, picture_id, value)
                elif facet == FACET_CHARACTERS:
                    if value is not None:
                        _apply_characters(session, picture_id, value)
                elif facet == FACET_STACK:
                    if value is not None:
                        _apply_stack(
                            session,
                            picture,
                            value,
                            vacated_stack_ids=vacated_stack_ids,
                        )
                elif facet == FACET_ORIENTATION:
                    # Deliberately NOT guarded on `value is not None` like its
                    # siblings. A recorded `None` means the row predates the
                    # orientation mirror being backfilled, so its undo genuinely
                    # cannot turn the file - and `apply_orientation` says so, at
                    # warning level, naming the picture. Skipping here instead
                    # would make an incomplete undo indistinguishable from a
                    # complete one, and silence the one message that explains it.
                    #
                    # This is also the only facet whose write leaves the
                    # database, so the only one that can fail for reasons the
                    # rest of the batch has nothing to do with - a read-only
                    # file, a full disk, a photo replaced by something that is no
                    # longer an image. The forward path degrades those to
                    # `skipped`; letting them out of this loop instead would fail
                    # the whole undo, including every unrelated facet and picture
                    # sharing the transaction.
                    #
                    # ImportError belongs with the IO errors: the box rotation
                    # resolves the rotate plugin through importlib (its package
                    # directory is hyphenated, so it cannot be a plain import),
                    # and that runs AFTER the file is written. A damaged install
                    # would otherwise leave the file turned and abort an undo
                    # that has nothing to do with rotation.
                    try:
                        apply_orientation(
                            session,
                            picture_id,
                            value,
                            image_root=image_root,
                        )
                    except (OSError, ValueError, ImportError) as exc:
                        logger.error(
                            "operation_log: could not restore orientation %s "
                            "on picture %d (%s); the rest of this restore "
                            "still applies, and the file keeps the rotation "
                            "it has until the picture is turned again",
                            value,
                            picture_id,
                            exc,
                        )
                elif facet == FACET_LOCATION:
                    # Not guarded on ``value is not None``: a recorded None
                    # means the picture had no path at all, and
                    # ``restore_location`` says so rather than silently doing
                    # nothing. Like the orientation above it, this applier
                    # leaves the database, so it fails for reasons the rest of
                    # the batch has nothing to do with - a destination taken
                    # since, a read-only folder, a file the owner has since
                    # moved themselves. Those degrade to a logged skip instead
                    # of failing an undo that also has tags and memberships in
                    # it.
                    try:
                        restore_location(
                            session,
                            picture_id,
                            value,
                            image_root=image_root,
                            applied=applied,
                        )
                    except (OSError, ValueError) as exc:
                        logger.error(
                            "operation_log: could not move picture %d back to "
                            "%r (%s); the rest of this restore still applies "
                            "and the file keeps the path it has.",
                            picture_id,
                            value,
                            exc,
                        )
                elif facet == FACET_DELETED:
                    if value is not None:
                        _apply_deleted(session, picture, value)
                else:
                    logger.warning(
                        "operation_log: unknown facet %r on picture %d was recorded "
                        "but has no applier; it cannot be restored",
                        facet,
                        picture_id,
                    )
            touched.append(picture_id)
        if label_ids:
            # Derived data, re-derived from what was just written (never restored
            # from a snapshot). Must run before the context manager exits, so the
            # score invalidation observes the final label state.
            session.flush()
            for picture_id in label_ids:
                recompute_anomaly_tag_uncertainty(session, picture_id)
    return touched


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _loads(payload: Optional[str], default):
    if not payload:
        return default
    try:
        return json.loads(payload)
    except (TypeError, ValueError) as exc:
        logger.error(
            "operation_log: could not parse stored JSON payload (%s); treating it "
            "as empty. Payload head: %r",
            exc,
            (payload or "")[:200],
        )
        return default


def serialize(operation: Operation, include_state: bool = False) -> dict:
    """Return the API shape of one operation row.

    Args:
        operation: The row to serialize.
        include_state: Include the full ``before``/``after`` payloads. Off by
            default - the list endpoint would otherwise ship a whole library's
            metadata to the client.
    """
    data = {
        "id": operation.id,
        "batch_id": operation.batch_id,
        "created_at": (
            operation.created_at.isoformat() if operation.created_at else None
        ),
        "actor": operation.actor,
        "op_type": operation.op_type,
        "target_type": operation.target_type,
        "target_ids": _loads(operation.target_ids, []),
        "target_count": operation.target_count,
        "source": operation.source,
        "origin_client_id": operation.origin_client_id,
        "undoable": bool(operation.undoable),
        "status": operation.status,
        "undone_at": (operation.undone_at.isoformat() if operation.undone_at else None),
        "summary": operation.summary,
    }
    if include_state:
        data["before"] = _loads(operation.before_state, {})
        data["after"] = _loads(operation.after_state, {})
    return data


# ---------------------------------------------------------------------------
# Post-restore hooks - reopening the domain state an operation also decided
# ---------------------------------------------------------------------------

RESTORE_UNDO = "undo"
RESTORE_REDO = "redo"

# op_type -> callback(session, operations, direction). Registered by the feature
# that owns the op_type; this module never imports a feature module, so the core
# stays free of domain knowledge and an unregistered op_type simply has no hook.
#
# Why hooks exist at all: the recorded before/after state covers the reversible
# *picture* facets (:data:`FACETS`). An operation may additionally have decided
# something that is not a picture facet - the v1.9 duplicate verdict is the first
# - and restoring the pictures without reopening that decision leaves the two
# halves disagreeing. The hook runs inside the restore's own transaction, after
# every state has been applied, so the decision and the pictures commit together
# or not at all.
_POST_RESTORE_HOOKS: dict[str, Callable[[Session, list[Operation], str], None]] = {}


def register_post_restore_hook(
    op_type: str, hook: Callable[[Session, list[Operation], str], None]
) -> None:
    """Register *hook* to run after an ``op_type`` operation is undone or redone.

    Args:
        op_type: The ``Operation.op_type`` this hook owns.
        hook: ``(session, operations, direction) -> None``, called **once** per
            restore with every operation of this type in that restore (so a
            batch of 2 700 rows is one call, not 2 700). *direction* is
            :data:`RESTORE_UNDO` or :data:`RESTORE_REDO`. It runs on the
            restore's session, before the commit, and must not commit: raising
            aborts the whole undo, which is the intended fail-closed behaviour.
    """
    _POST_RESTORE_HOOKS[str(op_type)] = hook


def _run_post_restore_hooks(
    session: Session, operations: list[Operation], direction: str
) -> None:
    """Dispatch the registered hooks for *operations*, grouped by ``op_type``."""
    by_type: dict[str, list[Operation]] = {}
    for operation in operations:
        if operation.op_type in _POST_RESTORE_HOOKS:
            by_type.setdefault(operation.op_type, []).append(operation)
    for op_type, members in by_type.items():
        _POST_RESTORE_HOOKS[op_type](session, members, direction)


# ---------------------------------------------------------------------------
# Undo / redo
# ---------------------------------------------------------------------------


def _batch_members_in_session(
    session: Session, operation: Operation, status: str
) -> list[Operation]:
    """Every operation of *operation*'s batch in *status*, newest first.

    A bulk action is one undoable unit: undoing any member reverts the whole
    batch, in reverse order of application, so partially-reverted batches cannot
    exist.
    """
    if not operation.batch_id:
        return [operation]
    return list(
        session.exec(
            select(Operation)
            .where(Operation.batch_id == operation.batch_id)
            .where(Operation.status == status)
            .order_by(Operation.id.desc())
        ).all()
    )


def _stack_siblings_in_session(session: Session, moved: set[int]) -> list[int]:
    """Live members of the stacks *moved* touched, excluding *moved* itself.

    A card renders its stack's LIVE member count as its badge, so a scrapheap
    move changes what every **surviving** member of that stack should draw. The
    survivors are exactly the pictures a restore does *not* touch, so they carry
    no facet diff and never reach ``touched``: undoing a "Keep cover only" whose
    metadata union happened to be a no-op restored four copies and announced
    nothing whatsoever about the cover they belong to, which went on rendering
    "stack of 1".

    Read AFTER the restore's writes, so ``deleted`` is already the new truth.

    Args:
        session: The restore's own session.
        moved: Pictures this restore scrapheaped or brought back.

    Returns:
        Sorted picture ids, empty when nothing moved or nothing was stacked.
    """
    if not moved:
        return []
    stack_ids = {
        int(stack_id)
        for stack_id in session.exec(
            select(Picture.stack_id).where(
                Picture.id.in_(moved),
                Picture.stack_id.is_not(None),
            )
        ).all()
        if stack_id is not None
    }
    if not stack_ids:
        return []
    live = {
        int(picture_id)
        for picture_id in session.exec(
            select(Picture.id).where(
                Picture.stack_id.in_(stack_ids),
                Picture.deleted.is_(False),
            )
        ).all()
    }
    return sorted(live - moved)


def _restore(
    session: Session,
    operations: list[Operation],
    *,
    to_before: bool,
    registry: Optional[InteractiveRescoreRegistry] = None,
    origin_client_id: Optional[str] = None,
    image_root: Optional[str] = None,
    applied: Optional[list] = None,
) -> tuple[list[int], set[str], dict[str, list[int]]]:
    """Apply the before- (undo) or after- (redo) state of *operations* in order.

    Once every state is written, the registered post-restore hooks run on this
    same session (see :func:`register_post_restore_hook`) so an operation that
    also decided something outside the picture facets can reopen that decision in
    the same transaction. A hook that raises aborts the restore.

    Stacks the restore empties are then deleted (:func:`delete_emptied_stacks`),
    the mirror of :func:`_apply_stack` recreating a dissolved stack's row. The
    decision is deliberately made HERE, after every operation's state (and every
    hook) has been applied: emptiness is a property of the whole restore, not of
    any single picture's pointer write.

    Returns:
        ``(touched_ids, facets, lifecycle)`` where *lifecycle* is
        ``{"scrapheaped": [...], "restored": [...]}`` - the pictures this
        restoration moves into and out of the scrapheap, so the caller can
        announce them as ``removed`` / ``restored`` instead of ``updated``.
    """
    action = "undo an operation" if to_before else "redo an operation"
    touched: set[int] = set()
    facets: set[str] = set()
    scrapheaped: set[int] = set()
    restored: set[int] = set()
    vacated_stack_ids: set[int] = set()
    for operation in operations:
        state = _loads(
            operation.before_state if to_before else operation.after_state, {}
        )
        if not state:
            # An operation recorded through the empty-diff path (its whole
            # reversible state lives outside the picture facets - the dedup
            # keep-separate verdict). Its restore is entirely its post-restore
            # hook's, but the pictures it named still change domain state, so
            # they are returned and announced like any other restore target.
            touched.update(int(pid) for pid in _loads(operation.target_ids, []))
            continue
        touched.update(
            apply_state_in_session(
                session,
                state,
                action,
                registry=registry,
                origin_client_id=origin_client_id,
                vacated_stack_ids=vacated_stack_ids,
                image_root=image_root,
                applied=applied,
            )
        )
        for picture_facets in state.values():
            facets.update(picture_facets or {})
        moved_out, moved_in = lifecycle_split(state)
        scrapheaped.update(moved_out)
        restored.update(moved_in)
    _run_post_restore_hooks(
        session, operations, RESTORE_UNDO if to_before else RESTORE_REDO
    )
    delete_emptied_stacks(session, vacated_stack_ids)
    lifecycle = {
        "scrapheaped": sorted(scrapheaped),
        "restored": sorted(restored),
        # Computed here, not in `_emit`: it needs the session, and it needs to
        # run after `delete_emptied_stacks` so a stack the restore dissolved
        # contributes no phantom survivors.
        "stack_siblings": _stack_siblings_in_session(session, scrapheaped | restored),
    }
    return sorted(touched), facets, lifecycle


def _emit(
    vault: "Vault",
    picture_ids: list[int],
    facets: set[str],
    origin_client_id,
    lifecycle: Optional[dict[str, list[int]]] = None,
) -> None:
    """Announce a restored state on the WS envelope.

    ``origin_client_id`` is carried in the event ``data`` dict, never read from a
    contextvar - this runs on the DB worker thread where the contextvar is dead
    (§15, ``test_source_origin_read_from_data_only``).

    ``change_kind`` follows the scrapheap lifecycle rather than being a blanket
    ``"updated"``: undoing a move-to-Scrapheap puts a card back (``restored``)
    and redoing it takes the card away (``removed``), which is what the delete
    and restore endpoints themselves broadcast. Telling the grid a vanished
    picture was merely "updated" leaves a 404-clickable thumbnail behind.

    ``restored`` is deliberately NOT ``added``. Both put a card back, but only
    ``added`` means "this picture is new to the vault": the SPA's sidebar reads
    ``added`` as a fresh import and raises its NEW marker on the affected
    counts, which is a lie for a picture that has been in the library all along.

    A lifecycle move also changes the **live member count of every stack it
    touched**, and the members that did not move render that count as their
    stack badge. Those survivors carry no facet diff, so they are not even in
    ``picture_ids``: undoing a "Keep cover only" put four copies back and left
    the cover still drawn as a stack of one. ``lifecycle["stack_siblings"]``
    names them (resolved in :func:`_stack_siblings_in_session`, which has the
    session), and they get an announcement of their own with
    ``fields=["stack_count"]``: the derived, listing-only value the SPA
    re-reads, since it is absent from ``GET /pictures/{id}/metadata``.
    """
    if not picture_ids:
        return
    scrapheaped = list((lifecycle or {}).get("scrapheaped") or [])
    restored = list((lifecycle or {}).get("restored") or [])
    stack_siblings = list((lifecycle or {}).get("stack_siblings") or [])
    moved = set(scrapheaped) | set(restored)
    updated = [picture_id for picture_id in picture_ids if picture_id not in moved]

    events: list[EventType] = []
    for facet in facets:
        for event in _FACET_EVENTS.get(facet, (EventType.CHANGED_PICTURES,)):
            if event not in events:
                events.append(event)
    if not events:
        events = [EventType.CHANGED_PICTURES]

    def _notify(
        event: EventType,
        ids: list[int],
        change_kind: str,
        fields: Optional[list[str]] = None,
    ) -> None:
        if not ids:
            return
        data = {
            "picture_ids": ids,
            "origin_client_id": origin_client_id,
            "change_kind": change_kind,
            "source": "ui",
        }
        if fields:
            data["fields"] = list(fields)
        vault.notify(event, data)

    # Restoring an orientation rewrites the FILE, so the card's thumbnail URL
    # changes - and that URL comes from the batch-thumbnail endpoint, not from
    # `GET /pictures/{id}/metadata`. A bare `updated` therefore makes the client
    # re-read metadata it already has and go on painting the pre-rotate bitmap,
    # which is exactly what `stack_count` below exists to solve for a different
    # listing-only value. Naming the field is what lets the client know a
    # metadata refresh is not enough.
    #
    # `facets` is the union over the whole restore rather than per picture, so a
    # mixed batch re-reads a few thumbnails it did not need to. That costs one
    # request against a conditional-GET-friendly URL; guessing wrong the other
    # way leaves a visibly stale photo on screen.
    # A restored location moves the FILE, so the card's thumbnail URL changes
    # for exactly the reason a restored orientation does - the URL is derived
    # from the path, not from ``GET /pictures/{id}/metadata``.
    updated_fields = (
        ["pixels"] if facets & {FACET_ORIENTATION, FACET_LOCATION} else None
    )
    for event in events:
        _notify(event, updated, "updated", updated_fields)
    _notify(EventType.CHANGED_PICTURES, scrapheaped, "removed")
    _notify(EventType.CHANGED_PICTURES, restored, "restored")
    _notify(EventType.CHANGED_PICTURES, stack_siblings, "updated", ["stack_count"])


def _select_undo_target(session: Session, operation_id: Optional[int]) -> Operation:
    if operation_id is not None:
        operation = session.get(Operation, operation_id)
        if operation is None:
            raise OperationLogError(f"Operation {operation_id} not found")
        if operation.status != STATUS_APPLIED:
            raise OperationLogError(
                f"Operation {operation_id} is {operation.status}, not applied"
            )
        if not operation.undoable:
            raise OperationLogError(
                f"Operation {operation_id} ({operation.op_type}) is recorded for "
                "audit but is not reversible"
            )
        _enforce_latest_undo_unit(session, operation)
        return operation
    operation = session.exec(
        select(Operation)
        .where(Operation.status == STATUS_APPLIED)
        .where(Operation.undoable.is_(True))
        .order_by(Operation.id.desc())
    ).first()
    if operation is None:
        raise OperationLogError("Nothing to undo")
    _enforce_latest_undo_unit(session, operation)
    return operation


def _enforce_latest_undo_unit(session: Session, operation: Operation) -> None:
    """Reject a named undo that is no longer the top reversible history unit.

    A batch is one unit, but it must also be contiguous at the top of the
    applied history. Otherwise restoring an older member can overwrite a newer,
    unrelated mutation while leaving that newer operation marked applied.
    """
    latest = session.exec(
        select(Operation)
        .where(Operation.status == STATUS_APPLIED)
        .where(Operation.undoable.is_(True))
        .order_by(Operation.id.desc())
    ).first()
    if latest is None:
        raise OperationLogError("Nothing to undo")

    if operation.batch_id is None:
        if operation.id != latest.id:
            raise OperationLogError(
                f"Operation {operation.id} is stale; operation {latest.id} "
                "must be undone first"
            )
        return

    members = list(
        session.exec(
            select(Operation)
            .where(Operation.batch_id == operation.batch_id)
            .where(Operation.status == STATUS_APPLIED)
            .where(Operation.undoable.is_(True))
            .order_by(Operation.id.desc())
        ).all()
    )
    if not members:
        raise OperationLogError(f"Batch {operation.batch_id} has nothing to undo")
    oldest_id = min(int(member.id) for member in members if member.id is not None)
    conflict = session.exec(
        select(Operation)
        .where(Operation.status == STATUS_APPLIED)
        .where(Operation.undoable.is_(True))
        .where(Operation.id >= oldest_id)
        .where(
            or_(
                Operation.batch_id.is_(None),
                Operation.batch_id != operation.batch_id,
            )
        )
        .order_by(Operation.id.desc())
    ).first()
    if latest.batch_id != operation.batch_id or conflict is not None:
        blocker = conflict or latest
        raise OperationLogError(
            f"Batch {operation.batch_id} is stale; operation {blocker.id} "
            "must be undone first"
        )


def _mark_undone(session: Session, members: list[Operation]) -> None:
    now = datetime.utcnow()
    for member in members:
        member.status = STATUS_UNDONE
        member.undone_at = now
        session.add(member)


def undo_in_session(
    session: Session,
    operation_id: Optional[int] = None,
    registry: Optional[InteractiveRescoreRegistry] = None,
    origin_client_id: Optional[str] = None,
    image_root: Optional[str] = None,
) -> tuple[list[dict], list[int], list[str], dict[str, list[int]]]:
    """Undo one operation (and its whole batch), returning what it touched.

    Rows are serialized *inside* the session: the DB worker closes it when the
    task returns, and a detached SQLModel instance would raise on attribute
    access in the route.
    """
    operation = _select_undo_target(session, operation_id)
    members = _batch_members_in_session(session, operation, STATUS_APPLIED)
    members = [member for member in members if member.undoable]
    if not members:
        raise OperationLogError("Nothing to undo")
    applied: list = []
    try:
        touched, facets, lifecycle = _restore(
            session,
            members,
            to_before=True,
            registry=registry,
            origin_client_id=origin_client_id,
            image_root=image_root,
            applied=applied,
        )
        _mark_undone(session, members)
        session.commit()
    except BaseException:
        # An undo renames files before this transaction commits, and everything
        # between - the post-restore hooks, the emptied-stack sweep, the commit
        # itself - can raise. The writer then rolls the session back while the
        # files stay where the undo put them, and a row naming a path with no
        # file at it is what ``MissingFilePurgeTask`` deletes a picture over.
        rollback_applied_moves(applied, image_root)
        raise
    return [serialize(member) for member in members], touched, sorted(facets), lifecycle


def undo_batch_in_session(
    session: Session,
    batch_id: str,
    registry: Optional[InteractiveRescoreRegistry] = None,
    origin_client_id: Optional[str] = None,
    image_root: Optional[str] = None,
) -> tuple[list[dict], list[int], list[str], dict[str, list[int]]]:
    """Undo every still-applied operation of one batch (the sweep's Undo)."""
    members = list(
        session.exec(
            select(Operation)
            .where(Operation.batch_id == batch_id)
            .where(Operation.status == STATUS_APPLIED)
            .where(Operation.undoable.is_(True))
            .order_by(Operation.id.desc())
        ).all()
    )
    if not members:
        raise OperationLogError(f"Batch {batch_id} has nothing to undo")
    _enforce_latest_undo_unit(session, members[0])
    applied: list = []
    try:
        touched, facets, lifecycle = _restore(
            session,
            members,
            to_before=True,
            registry=registry,
            origin_client_id=origin_client_id,
            image_root=image_root,
            applied=applied,
        )
        _mark_undone(session, members)
        session.commit()
    except BaseException:
        rollback_applied_moves(applied, image_root)
        raise
    return [serialize(member) for member in members], touched, sorted(facets), lifecycle


def redo_in_session(
    session: Session,
    registry: Optional[InteractiveRescoreRegistry] = None,
    origin_client_id: Optional[str] = None,
    image_root: Optional[str] = None,
) -> tuple[list[dict], list[int], list[str], dict[str, list[int]]]:
    """Re-apply the most recently undone operation (and its whole batch)."""
    operation = session.exec(
        select(Operation)
        .where(Operation.status == STATUS_UNDONE)
        .order_by(Operation.id.desc())
    ).first()
    if operation is None:
        raise OperationLogError("Nothing to redo")
    members = _batch_members_in_session(session, operation, STATUS_UNDONE)
    # Redo replays in application order, the mirror of undo's reverse order.
    members = sorted(members, key=lambda op: op.id or 0)
    applied: list = []
    try:
        touched, facets, lifecycle = _restore(
            session,
            members,
            to_before=False,
            registry=registry,
            origin_client_id=origin_client_id,
            image_root=image_root,
            applied=applied,
        )
        for member in members:
            member.status = STATUS_APPLIED
            member.undone_at = None
            session.add(member)
        session.commit()
    except BaseException:
        rollback_applied_moves(applied, image_root)
        raise
    return [serialize(member) for member in members], touched, sorted(facets), lifecycle


def list_operations_in_session(
    session: Session,
    *,
    limit: int = 50,
    status: Optional[str] = None,
    batch_id: Optional[str] = None,
    op_type: Optional[str] = None,
) -> list[dict]:
    """Return recorded operations, newest first (the activity feed's read)."""
    statement = select(Operation)
    if status:
        statement = statement.where(Operation.status == status)
    if batch_id:
        statement = statement.where(Operation.batch_id == batch_id)
    if op_type:
        statement = statement.where(Operation.op_type == op_type)
    statement = statement.order_by(Operation.id.desc()).limit(
        max(1, min(int(limit), MAX_LIST_LIMIT))
    )
    return [serialize(row) for row in session.exec(statement).all()]


def undo_state_in_session(session: Session) -> dict:
    """What the Ctrl+Z / Ctrl+Shift+Z affordances should show right now."""
    next_undo = session.exec(
        select(Operation)
        .where(Operation.status == STATUS_APPLIED)
        .where(Operation.undoable.is_(True))
        .order_by(Operation.id.desc())
    ).first()
    next_redo = session.exec(
        select(Operation)
        .where(Operation.status == STATUS_UNDONE)
        .order_by(Operation.id.desc())
    ).first()
    return {
        "can_undo": next_undo is not None,
        "can_redo": next_redo is not None,
        "next_undo": serialize(next_undo) if next_undo is not None else None,
        "next_redo": serialize(next_redo) if next_redo is not None else None,
    }


# ---------------------------------------------------------------------------
# Vault wrappers (the thin bridge from a route to the DB work queue)
# ---------------------------------------------------------------------------


def list_operations(vault: "Vault", **kwargs) -> list[dict]:
    """Vault wrapper for :func:`list_operations_in_session`."""
    return vault.db.run_task(
        lambda session: list_operations_in_session(session, **kwargs)
    )


def undo_state(vault: "Vault") -> dict:
    """Vault wrapper for :func:`undo_state_in_session`."""
    return vault.db.run_task(undo_state_in_session)


def get_operation(vault: "Vault", operation_id: int) -> Optional[dict]:
    """Return one operation including its recorded before/after payloads."""

    def _fetch(session: Session):
        operation = session.get(Operation, operation_id)
        return serialize(operation, include_state=True) if operation else None

    return vault.db.run_task(_fetch)


def _finish(
    vault: "Vault", members, touched, facets, lifecycle, origin_client_id
) -> dict:
    _emit(vault, touched, set(facets), origin_client_id, lifecycle)
    return {
        "operations": members,
        "picture_ids": touched,
        "picture_count": len(touched),
        # Which of those pictures left / re-entered the scrapheap, so the client
        # can drop or re-add cards without diffing the whole grid.
        "scrapheaped_picture_ids": list((lifecycle or {}).get("scrapheaped") or []),
        "restored_picture_ids": list((lifecycle or {}).get("restored") or []),
    }


def undo(
    vault: "Vault",
    operation_id: Optional[int] = None,
    origin_client_id: Optional[str] = None,
) -> dict:
    """Undo the newest reversible operation, or a named one, plus its batch."""
    members, touched, facets, lifecycle = vault.db.run_task(
        undo_in_session,
        operation_id,
        vault.interactive_rescore_registry,
        origin_client_id,
        vault.image_root,
    )
    return _finish(vault, members, touched, facets, lifecycle, origin_client_id)


def undo_batch(
    vault: "Vault", batch_id: str, origin_client_id: Optional[str] = None
) -> dict:
    """Undo one whole bulk action by its batch id (the sweep's report Undo)."""
    members, touched, facets, lifecycle = vault.db.run_task(
        undo_batch_in_session,
        batch_id,
        vault.interactive_rescore_registry,
        origin_client_id,
        vault.image_root,
    )
    return _finish(vault, members, touched, facets, lifecycle, origin_client_id)


def redo(vault: "Vault", origin_client_id: Optional[str] = None) -> dict:
    """Re-apply the most recently undone operation (and its batch)."""
    members, touched, facets, lifecycle = vault.db.run_task(
        redo_in_session,
        vault.interactive_rescore_registry,
        origin_client_id,
        vault.image_root,
    )
    return _finish(vault, members, touched, facets, lifecycle, origin_client_id)
