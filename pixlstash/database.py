import contextvars
import hashlib
import inspect
import itertools
import json
import math
import os
import struct
import threading
import queue
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from concurrent.futures import Future
from enum import IntEnum
from typing import Optional
from sqlalchemy import (
    bindparam as sa_bindparam,
    event,
    inspect as sa_inspect,
    update as sa_update,
    select as sa_select,
)
from sqlmodel import create_engine, Session
from fastapi import HTTPException
from rapidfuzz.distance import Levenshtein

import numpy as np

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.utils.unprocessable_image_registry import UnprocessableImageRegistry

# These imports are necessary to register the models with SQLModel

# The following imports are required to register all models with SQLModel.
# They may appear unused, but are necessary for correct table creation and ORM operation.
from pixlstash.db_models import Character, Face  # noqa: F401
from pixlstash.db_models import PictureLikeness, PictureSet, Picture, Quality, Tag, User  # noqa: F401
from pixlstash.db_models import Snapshot  # noqa: F401
from pixlstash.db_models import PictureProjectMember, PictureSetMember
from pixlstash.db_models import LibrarySettings, ReferenceFolder  # noqa: F401
from pixlstash.db_models.picture_move import CHECK_DEBOUNCE_S
from pixlstash.db_models.picture_move import PictureMove  # noqa: F401


# ---------------------------------------------------------------------------
# Picture metadata-hash helpers
# ---------------------------------------------------------------------------

# Columns excluded from the metadata hash; matches _diff_picture's _SKIP set
# in restore_service.py so that the hash detects exactly what the preview does.
_HASH_SKIP_COLS: frozenset = frozenset(
    {
        "id",
        "file_path",
        "created_at",
        "text_embedding",
        "image_embedding",
        "metadata_hash",
        # Derived/regenerable scores - excluded so that recalculating them
        # does not make a snapshot appear as changed.
        "aesthetic_score",
        "smart_score",
        "text_score",
    }
)


# Maximum number of picture ids per ``IN (...)`` list when batching the
# metadata-hash inputs. A bulk import or a library-wide task can dirty
# thousands of pictures in one flush, and SQLite caps bind variables per
# statement (``SQLITE_LIMIT_VARIABLE_NUMBER``: 32766 on some builds, 999 on
# older ones). 500 keeps every statement safely inside the smallest cap while
# still collapsing a realistic 64-picture task batch into a single round trip.
_HASH_ID_CHUNK = 500


def _compute_picture_metadata_hashes(session: Session, picture_ids) -> dict[int, str]:
    """Return ``{picture_id: sha256_hex}`` for every requested picture that exists.

    Batched equivalent of :func:`_compute_picture_metadata_hash`: five queries
    per chunk of ``_HASH_ID_CHUNK`` ids instead of five queries per picture.
    The digest input is byte-for-byte identical to the per-picture version -
    ``metadata_hash`` is persisted into snapshots and compared against live
    rows, so any change to the canonical string would make every picture in
    every existing snapshot compare as "changed".

    Ids with no picture row simply have no entry in the result, matching the
    ``None`` the per-picture helper returns for a missing picture.

    Args:
        session: Active DB session (must be within an open transaction).
        picture_ids: Iterable of picture primary keys; duplicates are ignored.

    Returns:
        Mapping of picture id to hex-encoded SHA-256 string.
    """
    ids = list(dict.fromkeys(pid for pid in picture_ids if pid is not None))
    if not ids:
        return {}

    pictures: dict[int, Picture] = {}
    tags_by_pid: dict[int, list] = {}
    faces_by_pid: dict[int, list] = {}
    sets_by_pid: dict[int, list] = {}
    projects_by_pid: dict[int, list] = {}

    for start in range(0, len(ids), _HASH_ID_CHUNK):
        chunk = ids[start : start + _HASH_ID_CHUNK]
        # ORM entity select (NOT a Core column select): the returned instances
        # come from the session's identity map, so ``getattr`` below sees the
        # same in-memory values ``session.get`` used to return - including the
        # ndarray columns that must be skipped and the datetimes that must be
        # ``isoformat``-ed. A Core select would hand back raw driver values and
        # silently change the digest.
        for pic in (
            session.execute(sa_select(Picture).where(Picture.id.in_(chunk)))
            .scalars()
            .all()
        ):
            pictures[pic.id] = pic
        for pid, tag in session.execute(
            sa_select(Tag.picture_id, Tag.tag).where(Tag.picture_id.in_(chunk))
        ):
            tags_by_pid.setdefault(pid, []).append(tag)
        for pid, frame_index, face_index, bbox_, character_id in session.execute(
            sa_select(
                Face.picture_id,
                Face.frame_index,
                Face.face_index,
                Face.bbox_,
                Face.character_id,
            ).where(Face.picture_id.in_(chunk))
        ):
            faces_by_pid.setdefault(pid, []).append(
                (frame_index, face_index, bbox_, character_id)
            )
        for pid, set_id in session.execute(
            sa_select(PictureSetMember.picture_id, PictureSetMember.set_id).where(
                PictureSetMember.picture_id.in_(chunk)
            )
        ):
            sets_by_pid.setdefault(pid, []).append(set_id)
        for pid, project_id in session.execute(
            sa_select(
                PictureProjectMember.picture_id, PictureProjectMember.project_id
            ).where(PictureProjectMember.picture_id.in_(chunk))
        ):
            projects_by_pid.setdefault(pid, []).append(project_id)

    hashes: dict[int, str] = {}
    for pid in ids:
        pic = pictures.get(pid)
        if pic is None:
            continue
        # Iterate only persisted columns - NOT ``model_fields``, which also
        # contains SQLModel relationship fields (``tags``, ``faces``,
        # ``project``, ...). ``getattr`` on a relationship triggers a lazy load
        # whose ORM objects aren't JSON-serialisable; ``json.dumps(...,
        # default=str)`` then digests Python ``repr()`` (memory addresses) and
        # the hash becomes non-deterministic across reloads.
        col_vals: dict = {}
        for col_attr in sa_inspect(type(pic)).column_attrs:
            col = col_attr.key
            if col in _HASH_SKIP_COLS:
                continue
            val = getattr(pic, col, None)
            if isinstance(val, np.ndarray):
                continue
            if hasattr(val, "isoformat"):
                val = val.isoformat()
            col_vals[col] = val
        # Face-derived state: the before-flush hash tracker dirties on Face
        # mutations (a Face add / remove / character reassignment is a
        # user-visible change), so the digest must include face state - else
        # the recompute would round-trip to the same value and the UI's
        # identical-state detection would lie for face-only edits.  We hash
        # the bbox + character_id of every face, sorted so the digest is
        # insensitive to row order.  ``features`` (the embedding BLOB) is
        # deliberately excluded - it's a derived column the WorkPlanner
        # regenerates and is not user-visible.
        #
        # LOAD-BEARING: ``str(tuple(...))`` is not cosmetic. The original
        # per-picture implementation put SQLAlchemy ``Row`` objects in this
        # list, and ``json.dumps(..., default=str)`` cannot serialise a Row, so
        # it fell back to ``str(row)`` and emitted each face as a JSON *string*
        # like ``"(0, 0, '[1, 2, 3, 4]', 5)"`` rather than a JSON array.
        # ``Row.__str__`` is identical to ``tuple.__str__`` (verified on this
        # repo's SQLAlchemy 2.0.51), so formatting the plain tuple reproduces
        # the stored digests exactly. Emitting real JSON arrays here would
        # invalidate the ``metadata_hash`` of every picture in every existing
        # snapshot.
        faces = [str(face) for face in sorted(faces_by_pid.get(pid, ()))]
        # Picture-set and project membership are user-visible and reverted by a
        # full restore, but live in their own tables (not Picture columns), so
        # they must be folded in explicitly - otherwise a picture whose only
        # change is being moved between sets/projects hashes identically and
        # the restore preview / identical-state detection would wrongly report
        # "unchanged".
        state = {
            "cols": col_vals,
            "tags": sorted(tags_by_pid.get(pid, ())),
            "faces": faces,
            "sets": sorted(sets_by_pid.get(pid, ())),
            "projects": sorted(projects_by_pid.get(pid, ())),
        }
        hashes[pid] = hashlib.sha256(
            json.dumps(state, sort_keys=True, default=str).encode()
        ).hexdigest()
    return hashes


def _compute_picture_metadata_hash(session: Session, picture_id: int) -> Optional[str]:
    """Return a SHA-256 hex digest of a picture's user-visible metadata.

    Covers all Picture columns not in ``_HASH_SKIP_COLS`` plus the sorted tag
    list, face state, and picture-set / project **membership** - everything a
    full restore would revert. Thin wrapper over
    :func:`_compute_picture_metadata_hashes` so the single-picture and batched
    paths can never drift apart in what they digest.

    Args:
        session: Active DB session (must be within an open transaction).
        picture_id: Primary key of the picture.

    Returns:
        Hex-encoded SHA-256 string, or None if the picture is not found.
    """
    return _compute_picture_metadata_hashes(session, (picture_id,)).get(picture_id)


def _before_flush_hash_tracker(session, flush_context, instances) -> None:
    """Record picture IDs whose metadata hash needs recomputing after flush."""
    dirty_pids: set = session.info.setdefault("_hash_dirty_pids", set())
    new_pics: list = session.info.setdefault("_hash_new_pics", [])
    for obj in itertools.chain(session.new, session.dirty, session.deleted):
        if isinstance(obj, Picture):
            if obj.id is not None:
                dirty_pids.add(obj.id)
            else:
                new_pics.append(obj)
        elif isinstance(obj, Tag) and obj.picture_id is not None:
            dirty_pids.add(obj.picture_id)
        elif isinstance(obj, Face) and obj.picture_id is not None:
            dirty_pids.add(obj.picture_id)
        elif (
            isinstance(obj, (PictureSetMember, PictureProjectMember))
            and obj.picture_id is not None
        ):
            # Adding/removing a picture from a set or project changes its
            # restore-visible state, so its hash must be recomputed. Works for
            # session.deleted too - the row keeps its picture_id while pending.
            dirty_pids.add(obj.picture_id)


def _after_flush_hash_updater(session, flush_context) -> None:
    """Recompute and persist metadata_hash for dirty pictures in the same txn.

    Runs on the single writer thread INSIDE the write transaction, so its cost
    is held against the SQLite write lock: the old shape issued five SELECTs
    plus one UPDATE per dirty picture, which turned a 64-picture tag batch into
    ~384 extra statements before the lock could be released (#651). It is now a
    constant number of statements per flush - the batched hash read plus one
    executemany UPDATE.

    Uses Core SQL UPDATE so the change is committed with the same transaction
    without triggering a second ORM flush cycle.
    """
    dirty_pids: set = session.info.pop("_hash_dirty_pids", set())
    new_pics: list = session.info.pop("_hash_new_pics", [])
    for pic in new_pics:
        if pic.id is not None:
            dirty_pids.add(pic.id)
    if not dirty_pids:
        return
    with session.no_autoflush:
        new_hashes = _compute_picture_metadata_hashes(session, dirty_pids)
        if not new_hashes:
            return
        # One prepared statement, N parameter sets. Target ``Picture.__table__``
        # (Core ``Table``) rather than the ORM ``Picture`` mapper so SQLAlchemy
        # does not route this through the ORM bulk-by-primary-key path (which
        # clashes with the explicit WHERE bindparam) and so this very hook does
        # not re-fire. Same precedent as ``tasks/smart_score_task.py``.
        stmt = (
            sa_update(Picture.__table__)
            .where(Picture.__table__.c.id == sa_bindparam("_pid"))
            .values(metadata_hash=sa_bindparam("_hash"))
        )
        session.execute(
            stmt,
            [{"_pid": pid, "_hash": new_hash} for pid, new_hash in new_hashes.items()],
        )
        for pid in new_hashes:
            # Expire the in-memory attribute so it reflects the new value
            # on next access (the Core UPDATE bypasses ORM tracking).
            cached = session.identity_map.get((Picture, (pid,)))
            if cached is not None:
                session.expire(cached, ["metadata_hash"])


def _before_flush_layout_tracker(session, flush_context, instances) -> None:
    """Record picture IDs whose project / set / person assignments changed.

    The v1.11 layout engine's trigger (``services/layout_move_service.py``), and
    it is here rather than at the mutation sites because there are far too many
    of those to keep in step: a picture gains a project through the import
    route, the CRUD route, the membership service, a plugin, the ComfyUI
    ingest, stack propagation and a restore. Anything that misses one is a
    picture whose folder has quietly stopped being true and that nothing ever
    revisits.

    Only the four facets a layout can be built from count. A rating, a caption
    or a tag edit is not an assignment and must not wake the engine - the whole
    rule is that a picture moves when its *folder* stops being true, not
    whenever something about it changes.
    """
    dirty_pids: set = session.info.setdefault("_layout_dirty_pids", set())
    for obj in itertools.chain(session.new, session.dirty, session.deleted):
        if isinstance(obj, (PictureSetMember, PictureProjectMember)):
            if obj.picture_id is not None:
                dirty_pids.add(obj.picture_id)
        elif isinstance(obj, Face) and obj.picture_id is not None:
            if _attribute_changed(obj, "character_id"):
                dirty_pids.add(obj.picture_id)
        elif isinstance(obj, Picture) and obj.id is not None:
            # Deliberately NOT session.new: a picture that has just been created
            # is either where the engine placed it or where the owner already
            # had it, and both are true by construction.
            if obj in session.dirty and _attribute_changed(obj, "project_id"):
                dirty_pids.add(obj.id)


def _attribute_changed(obj, name: str) -> bool:
    """Whether *name* actually changed on *obj* in this flush.

    ``session.dirty`` is "something on this object changed", not "this column
    did". Without the narrowing every score, caption and thumbnail write on a
    Picture would look like an assignment change and stamp the whole library
    due.
    """
    try:
        return sa_inspect(obj).attrs[name].history.has_changes()
    except Exception:
        # An object with no usable state (detached, or a mapper without the
        # attribute) - treat as changed rather than silently missing a real
        # assignment change, and say so.
        logger.warning(
            "Layout tracker: could not read %s history on %r; treating it as "
            "changed so the check is not silently skipped.",
            name,
            type(obj).__name__,
            exc_info=True,
        )
        return True


def _library_has_layout(session) -> bool:
    """Whether any root in this library has a layout, cached per session.

    The gate that keeps the stamp free for every library that has not chosen a
    layout - which, on the day this ships, is all of them. One small indexed
    read per task that touched an assignment, not per flush and not per row.
    """
    cached = session.info.get("_library_has_layout")
    if cached is None:
        try:
            cached = bool(
                session.execute(
                    sa_select(LibrarySettings.id)
                    .where(LibrarySettings.layout.is_not(None))
                    .limit(1)
                ).first()
                or session.execute(
                    sa_select(ReferenceFolder.id)
                    .where(ReferenceFolder.layout.is_not(None))
                    .limit(1)
                ).first()
            )
        except Exception:
            # A vault mid-migration has no such column yet. Fail closed on the
            # cheap side: no layout, no stamp, and the next task asks again.
            logger.debug("Layout tracker: layout gate unavailable", exc_info=True)
            cached = False
        session.info["_library_has_layout"] = cached
    return cached


def _after_flush_layout_marker(session, flush_context) -> None:
    """Stamp the tracked pictures as due a layout check, in the same txn.

    The stamp is written unconditionally rather than only when it moves later:
    re-stamping IS the debounce, so a second change to the same picture pushes
    its check out again and a remove-then-add settles into one move.
    """
    dirty_pids: set = session.info.pop("_layout_dirty_pids", set())
    if not dirty_pids or not _library_has_layout(session):
        return
    due_at = time.time() + CHECK_DEBOUNCE_S
    with session.no_autoflush:
        # Core UPDATE against the Table for the same reason the hash hook uses
        # one: it must not re-enter the ORM flush cycle that called it.
        session.execute(
            sa_update(Picture.__table__)
            .where(Picture.__table__.c.id.in_(sorted(dirty_pids)))
            .values(layout_check_due_at=due_at)
        )
        for pid in dirty_pids:
            cached = session.identity_map.get((Picture, (pid,)))
            if cached is not None:
                session.expire(cached, ["layout_check_due_at"])


def _attach_session_hooks(session: Session) -> None:
    """Attach per-session before_flush / after_flush event listeners.

    Two pairs: the metadata-hash hooks, and the v1.11 layout tracker. The writer
    thread runs every task inside a session with these listeners, so any picture
    mutation (or tag/face mutation that affects a picture) refreshes the
    ``metadata_hash`` column in the same transaction, and any change to a
    picture's project / set / person membership stamps it as owing a layout
    check.
    """
    event.listen(session, "before_flush", _before_flush_hash_tracker)
    event.listen(session, "after_flush", _after_flush_hash_updater)
    event.listen(session, "before_flush", _before_flush_layout_tracker)
    event.listen(session, "after_flush", _after_flush_layout_marker)


class _EngineRWLock:
    """A small writer-preferring readers/writer lock.

    Any number of readers may hold it concurrently, but a writer gets
    exclusive access.  Once a writer is waiting it blocks new readers, so a
    steady stream of reads cannot starve it.  Used to fence the live-DB file
    swap (the writer) against ``run_immediate_read_task`` reads so no session
    is opened on the engine while it is being disposed and re-created.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._readers = 0
        self._writers_waiting = 0
        self._writer_active = False

    @contextmanager
    def read(self):
        with self._cond:
            while self._writer_active or self._writers_waiting > 0:
                self._cond.wait()
            self._readers += 1
        try:
            yield
        finally:
            with self._cond:
                self._readers -= 1
                if self._readers == 0:
                    self._cond.notify_all()

    @contextmanager
    def write(self):
        with self._cond:
            self._writers_waiting += 1
            try:
                while self._writer_active or self._readers > 0:
                    self._cond.wait()
            finally:
                self._writers_waiting -= 1
            self._writer_active = True
        try:
            yield
        finally:
            with self._cond:
                self._writer_active = False
                self._cond.notify_all()


# Priority enum for DB operations
class DBPriority(IntEnum):
    LOW = 30
    MEDIUM = 20
    HIGH = 10
    IMMEDIATE = 0


# Database task for the queue
class DatabaseTask:
    _sequence = itertools.count()

    def __init__(self, priority, func, args=(), kwargs=None, is_control=False):
        self.priority = priority
        self.sequence = next(self._sequence)
        self.func = func
        self.args = args
        self.kwargs = kwargs or {}
        self.future = Future()
        # Control tasks run on the writer thread WITHOUT an open Session; the
        # callable receives only the args it was submitted with (no implicit
        # ``session``). Use for engine-level operations such as the restore
        # DB-file swap, which must serialise with normal writes but cannot
        # tolerate a session bound to the soon-to-be-disposed engine.
        self.is_control = is_control
        # Capture the current execution context so any context vars set by
        # the caller are visible inside the worker thread when the task runs.
        self._context = contextvars.copy_context()

    def __lt__(self, other):
        if not isinstance(other, DatabaseTask):
            return NotImplemented
        return (self.priority, self.sequence) < (other.priority, other.sequence)

    def __le__(self, other):
        if not isinstance(other, DatabaseTask):
            return NotImplemented
        return (self.priority, self.sequence) <= (other.priority, other.sequence)

    def __eq__(self, other):
        if not isinstance(other, DatabaseTask):
            return NotImplemented
        return (self.priority, self.sequence) == (other.priority, other.sequence)


logger = get_logger(__name__)

LEVENSHTEIN_STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}


def levenshtein_function(a, b):
    try:
        if a is None or b is None:
            return 100.0  # or some large default distance
        return float(Levenshtein.distance(str(a), str(b)))
    except Exception as e:
        logger.error(f"Levenshtein error: {e} (a={a}, b={b})")
        return 100.0  # fallback value


def softmin(distances, beta=1.0):
    if not distances:
        return float("inf")
    exp_neg_dists = [math.exp(-beta * d) for d in distances]
    sum_exp = sum(exp_neg_dists)
    if sum_exp == 0:
        return float("inf")  # Avoid division by zero
    return sum(d * exp_neg for d, exp_neg in zip(distances, exp_neg_dists)) / sum_exp


def _levenshtein_internal(concatenated_tags, query, picture_id=None):
    # Split the concatenated tags into tags
    tags = (
        concatenated_tags.split()
        if isinstance(concatenated_tags, str)
        else [concatenated_tags]
    )
    query_words = query.split() if isinstance(query, str) else [query]
    d_query_words = [str(word).lower() for word in query_words]
    filtered_query_words = [
        word
        for word in d_query_words
        if len(word) > 2 and word not in LEVENSHTEIN_STOPWORDS
    ]
    if filtered_query_words:
        d_query_words = filtered_query_words

    d_tags = [str(tag).lower() for tag in tags if tag is not None]

    tag_dists = []
    for tag_value in d_tags:
        min_dist = 1.0
        for query_word in d_query_words:
            min_dist = min(
                min_dist,
                levenshtein_function(tag_value, query_word)
                / max(len(tag_value), len(query_word), 1),
            )
        tag_dists.append(min_dist)

    query_dists = []
    query_dist_map = {}
    for query_word in d_query_words:
        min_dist = 1.0
        for tag_value in d_tags:
            min_dist = min(
                min_dist,
                levenshtein_function(tag_value, query_word)
                / max(len(tag_value), len(query_word), 1),
            )
        query_dists.append(min_dist)
        query_dist_map[query_word] = min_dist

    tag_dists = sorted(tag_dists)
    best_k = min(5, len(tag_dists))
    best_dists = tag_dists[:best_k]
    softmin_value = softmin(best_dists, 2.5) if best_dists else 1.0
    mean_best = (sum(best_dists) / best_k) if best_dists else 1.0
    mean_query = (sum(query_dists) / len(query_dists)) if query_dists else 1.0
    good_match_threshold = 0.25
    exact_match_threshold = 0.05
    matched_words = sum(1 for dist in query_dists if dist <= good_match_threshold)
    exact_matches = sum(1 for dist in query_dists if dist <= exact_match_threshold)
    coverage = matched_words / len(query_dists) if query_dists else 0.0
    logger.info(
        "Best Levenshtein distances for tags '%s': %s (picture_id=%s, best_k=%d, total_tags=%d, mean_best=%.4f, mean_query=%.4f, softmin=%.4f, coverage=%.2f, exact=%d, query_words=%s)",
        concatenated_tags,
        best_dists,
        picture_id,
        best_k,
        len(tags),
        mean_best,
        mean_query,
        softmin_value,
        coverage,
        exact_matches,
        d_query_words,
    )
    if query_dist_map:
        logger.info(
            "Query word min distances (picture_id=%s): %s",
            picture_id,
            {word: round(dist, 4) for word, dist in query_dist_map.items()},
        )

    # Prioritize query-word matches over non-matching tags.
    base_score = 0.75 * mean_query + 0.15 * softmin_value + 0.10 * mean_best
    if coverage < 1.0:
        base_score *= 1.0 + (1.0 - coverage) * 0.15
    else:
        base_score *= 0.85

    # Bonus for strong query-word matches (reduce distance when more words match well).
    if coverage > 0.0:
        bonus = min(0.12, 0.06 * coverage + 0.02 * exact_matches)
        base_score = max(0.0, base_score * (1.0 - bonus))

    # Apply a mild penalty for very few tags so single-tag matches don't dominate.
    min_tags = 5
    if len(tags) < min_tags and len(tags) > 0:
        scarcity_penalty = min_tags / float(len(tags))
        base_score = min(1.0, base_score * scarcity_penalty)

    return base_score


def levenshtein(concatenated_tags, query):
    return _levenshtein_internal(concatenated_tags, query)


def levenshtein_with_id(concatenated_tags, query, picture_id):
    return _levenshtein_internal(concatenated_tags, query, picture_id)


def character_face_likeness(candidate_blob: bytes, refs_blob: bytes) -> float:
    """Compute softmax-weighted cosine similarity between a candidate face and packed reference faces.

    This function is registered as a SQLite scalar function and called once per face row.
    It enables ORDER BY on likeness score at the SQL level so LIMIT/OFFSET pagination works.

    Args:
        candidate_blob: Feature vector bytes for the candidate face (float32 array).
        refs_blob: Packed reference face vectors with header:
            bytes 0-3: int32 n_refs (little-endian)
            bytes 4-7: int32 vec_size (little-endian)
            remaining: n_refs * vec_size float32 values (pre-normalised)

    Returns:
        Softmax-weighted cosine similarity in [-1, 1], or 0.0 on any error.
    """
    try:
        if candidate_blob is None or refs_blob is None or len(refs_blob) < 8:
            return 0.0
        n_refs, vec_size = struct.unpack_from("<ii", refs_blob, 0)
        if n_refs <= 0 or vec_size <= 0:
            return 0.0
        cand = np.frombuffer(candidate_blob, dtype=np.float32)
        if cand.size != vec_size:
            return 0.0
        norm = np.linalg.norm(cand)
        if norm < 1e-8:
            return 0.0
        cand_norm = cand / norm
        ref_norm = np.frombuffer(refs_blob, dtype=np.float32, offset=8).reshape(
            n_refs, vec_size
        )
        sims = ref_norm @ cand_norm  # (n_refs,)
        sims = np.clip(sims, -1.0, 1.0)
        alpha = 5.0
        weights = np.exp(alpha * sims)
        denom = weights.sum()
        if denom < 1e-8:
            return 0.0
        return float((weights * sims).sum() / denom)
    except Exception:
        # Best-effort per-row SQL scalar: any decode/maths failure means "no
        # likeness" for this face, and 0.0 IS the ranking answer. This fires once
        # per candidate row, so logging would flood (allowlisted in the guardrail).
        return 0.0


# ---------------------------------------------------------------------------
# SQLite connection settings (documented in docs/backend_architecture.md §13)
# ---------------------------------------------------------------------------

# How long a connection waits for the write lock before raising
# "database is locked". sqlite3's default is 5 s, which is short for a vault
# where a background task batch can hold the write transaction longer than
# that. Passed as ``connect_args={"timeout": ...}``, which sqlite3 turns into
# ``PRAGMA busy_timeout``. The engine the restore path rebuilds after a DB
# swap already used 30 s (services/restore/full_restore.py); this makes the
# engine built at startup match it.
SQLITE_BUSY_TIMEOUT_S = 30

# Per-connection page cache. Negative means KiB rather than pages, so this is
# 16 MiB against SQLite's 2 MiB default. It is a cap, not a reservation, but a
# single index scan is enough to reach it: measured peak RSS for 15 connections
# (QueuePool size=5 + max_overflow=10) is ~263 MB versus ~79 MB at the default.
# 16 MiB is where a scan of this repo's largest dev vault stopped growing its
# cache; larger values cost proportionally more resident memory for no
# measured gain (32 MiB -> ~513 MB, 64 MiB -> ~1010 MB across 15 connections).
SQLITE_CACHE_SIZE_KIB = -16384


def _apply_sqlite_settings(dbapi_conn, *, wal: bool, foreign_keys: bool) -> None:
    """Apply the documented SQLite connection settings to a raw DBAPI connection.

    Registers the custom SQL functions the ORM queries rely on and sets the
    per-connection PRAGMAs from §13 of docs/backend_architecture.md.

    Args:
        dbapi_conn: The sqlite3 connection handed over by SQLAlchemy's
            ``connect`` event.
        wal: When True, put the file in WAL mode and pair it with
            ``synchronous=NORMAL``. The two travel together: ``NORMAL`` is only
            crash-safe under WAL, so with ``wal=False`` ``synchronous`` is left
            at SQLite's ``FULL`` default rather than silently weakening
            durability in rollback-journal mode.
        foreign_keys: Whether to enable FK enforcement (SQLite defaults it off).
    """
    dbapi_conn.create_function("levenshtein", 2, levenshtein)
    dbapi_conn.create_function("levenshtein_with_id", 3, levenshtein_with_id)
    dbapi_conn.create_function("cosine_similarity", 2, ImageUtils.cosine_similarity)
    dbapi_conn.create_function("character_face_likeness", 2, character_face_likeness)

    cursor = dbapi_conn.cursor()
    if wal:
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.execute(f"PRAGMA foreign_keys={'ON' if foreign_keys else 'OFF'}")
    cursor.execute(f"PRAGMA cache_size={SQLITE_CACHE_SIZE_KIB}")
    cursor.close()


def init_database(dbapi_conn, conn_record):
    """SQLAlchemy ``connect`` listener for the live vault engine.

    Kept as a named listener because the vault engine is the fully configured
    case (WAL + FK on); ``create_configured_engine`` attaches it for the
    default settings.
    """
    _apply_sqlite_settings(dbapi_conn, wal=True, foreign_keys=True)


def create_configured_engine(
    db_path,
    *,
    wal: bool = True,
    foreign_keys: bool = True,
    echo: bool = False,
):
    """Build a SQLite engine with the settings documented in §13.

    **This is the only supported way to build a SQLite engine in PixlStash.**
    The busy timeout (``connect_args``) and the PRAGMAs (the ``connect``
    listener) are one configuration in two halves; a bare ``create_engine``
    call gets neither, and that drift has been a real bug twice (#651, #709).
    Anything other than the defaults is a deliberate deviation and must be
    justified where it is requested.

    Args:
        db_path: Filesystem path to the SQLite database file.
        wal: Whether to use WAL journalling (with ``synchronous=NORMAL``).
            Pass ``False`` for a database that must stay a **single file** on
            disk. WAL is a persistent property of the file header and spawns
            ``-wal``/``-shm`` companions, which a path that copies the main
            file by name would silently truncate away. See
            ``services/restore/schema_upgrade.snapshot_engine``.
        foreign_keys: Whether to enforce foreign keys. Reads are unaffected
            either way; this only matters for a connection that writes.
        echo: SQLAlchemy statement echo.

    Returns:
        A configured SQLAlchemy ``Engine``.
    """
    engine = create_engine(
        f"sqlite:///{db_path}",
        echo=echo,
        connect_args={"timeout": SQLITE_BUSY_TIMEOUT_S},
    )
    if wal and foreign_keys:
        event.listen(engine, "connect", init_database)
    else:

        def _listener(dbapi_conn, conn_record):
            _apply_sqlite_settings(dbapi_conn, wal=wal, foreign_keys=foreign_keys)

        event.listen(engine, "connect", _listener)
    return engine


def _run_migrations(connection, db_path: str, db_exists: bool) -> None:
    try:
        from alembic import command
        from alembic.config import Config
        from alembic.util.exc import CommandError
    except Exception as exc:
        logger.error("Alembic is required for database migrations: %s", exc)
        raise

    module_dir = Path(__file__).resolve().parent
    repo_root = module_dir.parent

    candidate_locations = [
        (repo_root / "alembic.ini", repo_root / "migrations"),
        (module_dir / "alembic.ini", module_dir / "migrations"),
    ]

    alembic_ini = None
    migrations_dir = None
    for candidate_ini, candidate_migrations in candidate_locations:
        if candidate_ini.exists() and candidate_migrations.exists():
            alembic_ini = candidate_ini
            migrations_dir = candidate_migrations
            break

    if alembic_ini is None or migrations_dir is None:
        expected = " or ".join(
            f"({candidate_ini}, {candidate_migrations})"
            for candidate_ini, candidate_migrations in candidate_locations
        )
        raise RuntimeError(f"Alembic config missing. Expected {expected}.")

    config = Config(str(alembic_ini))
    config.set_main_option("script_location", str(migrations_dir))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    # The registered-library opener has already validated this exact
    # connection. Alembic must use it rather than reopening the path and giving
    # a replacement database migration authority.
    config.attributes["connection"] = connection

    if db_exists:
        inspector = sa_inspect(connection)
        table_names = [
            name
            for name in inspector.get_table_names()
            if not name.startswith("sqlite_")
        ]
        has_version = "alembic_version" in table_names
        if has_version:
            try:
                command.upgrade(config, "head")
                # Alembic is running on our already-open, security-validated
                # SQLAlchemy connection. Introspection above may have started
                # an implicit SQLAlchemy 2.x transaction before Alembic enters
                # its own context; without this explicit commit, upgrades of an
                # existing vault can be rolled back when the connection closes.
                connection.commit()
                return
            except CommandError as exc:
                msg = str(exc)
                if "Can't locate revision identified by" in msg:
                    logger.warning(
                        "Missing Alembic revision detected (%s). Stamping head.",
                        msg,
                    )
                    try:
                        command.stamp(config, "head")
                    except CommandError as stamp_exc:
                        if "Can't locate revision identified by" in str(stamp_exc):
                            logger.warning(
                                "Stamp failed due to missing revision; clearing alembic_version and retrying."
                            )
                            connection.exec_driver_sql("DELETE FROM alembic_version")
                            connection.commit()
                            command.stamp(config, "head")
                        else:
                            raise
                    return
                raise
        if table_names:
            logger.info(
                "Existing database without Alembic version table detected; "
                "stamping baseline and upgrading to head to apply missing columns."
            )
            command.stamp(config, "0001_baseline")
            command.upgrade(config, "head")
            connection.commit()
            return

    try:
        command.upgrade(config, "head")
        connection.commit()
    except CommandError as exc:
        msg = str(exc)
        if "Can't locate revision identified by" in msg:
            logger.warning(
                "Missing Alembic revision detected (%s). Stamping head.",
                msg,
            )
            try:
                command.stamp(config, "head")
            except CommandError as stamp_exc:
                if "Can't locate revision identified by" in str(stamp_exc):
                    logger.warning(
                        "Stamp failed due to missing revision; clearing alembic_version and retrying."
                    )
                    connection.exec_driver_sql("DELETE FROM alembic_version")
                    connection.commit()
                    command.stamp(config, "head")
                else:
                    raise
            return
        raise


def _ensure_user_stack_strictness(connection) -> None:
    inspector = sa_inspect(connection)
    if "user" not in inspector.get_table_names():
        return
    existing_cols = {
        row[1] for row in connection.exec_driver_sql("PRAGMA table_info('user')")
    }
    if "stack_strictness" in existing_cols:
        return
    connection.exec_driver_sql(
        "ALTER TABLE user ADD COLUMN stack_strictness FLOAT DEFAULT 0.92"
    )
    connection.commit()


class VaultDatabase:
    def __init__(
        self,
        db_path: str,
        *,
        location_guard=None,
        pre_migration_hook=None,
        post_migration_hook=None,
    ):
        self._db_path = db_path
        self.image_root = os.path.dirname(self._db_path)
        # In-memory, process-lifetime set of pictures whose image file cannot be
        # decoded (issue #585). Shared by task threads (marking) and finder
        # threads (suppression); reachable from both via their ``self._db``.
        self.unprocessable_images = UnprocessableImageRegistry()
        db_exists = os.path.exists(self._db_path)
        logger.debug(f"Vault init, db_path={self._db_path}, db_exists={db_exists}")

        if not db_exists:
            # Pre-create the database file 0600. Left to SQLite, a missing
            # database is created at 0644 & ~umask - group/world-readable
            # under the Debian/Ubuntu default umask 002. Doing it here covers
            # every construction site, guarded or not. Without O_EXCL a lost
            # race simply opens the other creator's file; the mode argument is
            # ignored for an existing file.
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            os.close(os.open(self._db_path, flags, 0o600))

        self._engine = create_configured_engine(self._db_path)

        # The guard holds a raw fd on vault.db. POSIX advisory locks are
        # per-process, per-inode: closing ANY fd a process has on a file
        # releases EVERY fcntl lock that process holds on it - including the
        # ones SQLite took for the engine's live connections
        # (sqlite.org/howtocorrupt.html §2.2). Closing the guard here, while
        # pooled connections were open, therefore stripped the server of its
        # kernel locks on the vault; a second process (CLI, the e2e dedup
        # seeder, another instance) closing its own connection then saw the
        # database as unused, checkpointed, and DELETED the live -wal/-shm,
        # split-braining every open connection ("disk I/O error", "database
        # disk image is malformed"). The guard must outlive the engine: it is
        # retained and closed in close(), after dispose().
        self._location_guard = location_guard
        try:
            with self._engine.connect() as initial_connection:
                if location_guard is not None:
                    location_guard.verify_after_open()
                if pre_migration_hook is not None:
                    pre_migration_hook(initial_connection)
                _run_migrations(initial_connection, self._db_path, db_exists)
                if post_migration_hook is not None:
                    post_migration_hook(initial_connection)
                _ensure_user_stack_strictness(initial_connection)
        except Exception:
            self._engine.dispose()
            self._engine = None
            # All connections are gone, so closing the guard fd is safe now.
            if location_guard is not None:
                location_guard.close()
            raise

        # Write queue and worker
        self._task_queue = queue.PriorityQueue()
        self._task_worker_stop_event = threading.Event()
        self._close_lock = threading.Lock()
        # Fences immediate reads against the live-DB file swap (see
        # exclusive_engine_access / run_immediate_read_task).
        self._engine_rwlock = _EngineRWLock()
        self._closed = False
        self._task_worker = threading.Thread(target=self._task_worker_loop, daemon=True)
        self._task_worker.start()

    @property
    def is_open(self) -> bool:
        return not self._closed and self._engine is not None

    def close(self):
        """
        Cleanly close the database engine and stop the worker thread.
        """
        import gc

        with self._close_lock:
            if self._closed:
                return
            self._closed = True

            try:
                self._task_worker_stop_event.set()
                self._task_queue.put(DatabaseTask(DBPriority.IMMEDIATE, None))
                if self._task_worker:
                    self._task_worker.join(timeout=10)
                    if self._task_worker.is_alive():
                        logger.warning(
                            "VaultDatabase: worker thread did not stop cleanly before engine disposal."
                        )
                self._task_worker = None
            except Exception as e:
                logger.warning(
                    f"VaultDatabase: Exception during worker thread stop: {e}"
                )

            while True:
                try:
                    pending = self._task_queue.get_nowait()
                except queue.Empty:
                    break
                if getattr(pending, "func", None) is None:
                    continue
                if not pending.future.done():
                    pending.future.set_exception(
                        RuntimeError("VaultDatabase is closed; task cancelled.")
                    )

            if hasattr(self, "_engine") and self._engine:
                try:
                    self._engine.dispose()
                    self._engine = None
                    logger.info("VaultDatabase: SQLAlchemy engine disposed.")
                except Exception as e:
                    logger.warning(
                        f"VaultDatabase: Exception during engine dispose: {e}"
                    )

            # Only after every SQLite connection is disposed may the guard fd
            # be closed: closing it earlier releases the process's POSIX locks
            # on the vault file out from under the live connections (see the
            # comment in __init__).
            if getattr(self, "_location_guard", None) is not None:
                try:
                    self._location_guard.close()
                except OSError as e:
                    logger.warning(
                        f"VaultDatabase: Exception closing location guard: {e}"
                    )
                self._location_guard = None

        gc.collect()
        logger.info("VaultDatabase.close called, resources released.")

    # --- Queued API ---
    def submit_task(self, func, *args, priority=DBPriority.MEDIUM, **kwargs):
        """
        Submit a database operation (INSERT/UPDATE/DELETE) to be executed serially using SQLModel.
        Returns a Future you can .result(timeout) on.

        The function should accept a SQLModel Session as its first argument.

        Examples:

        # Using a lambda for a simple write
        future = db.submit_task(lambda session: session.exec(
            update(Picture).where(Picture.id == "pic123").values(quality=0.95)
        ))
        result = future.result()

        # Using a full function for more complex logic
        def update_picture_quality(session, pic_id, new_quality):
            picture = session.exec(select(Picture).where(Picture.id == pic_id)).first()
            if picture:
                picture.quality = new_quality
                session.add(picture)
                session.commit()
            return picture

        future = db.submit_task(update_picture_quality, "pic123", 0.95)
        result = future.result()
        """
        if self._closed:
            future = Future()
            future.set_exception(RuntimeError("VaultDatabase is closed."))
            return future
        task = DatabaseTask(priority, func, args, kwargs)
        self._task_queue.put(task)
        return task.future

    # --- Synchronous API ---
    def run_task(self, func, *args, priority=DBPriority.IMMEDIATE, **kwargs):
        """
        Run a database operation and wait for the result.
        The function should accept a SQLModel Session as its first argument.

        Examples:

        result = db.run_task(lambda session: session.exec(
            select(Picture).where(Picture.quality > 0.9)
        ).all())
        """
        return self.result_or_throw(
            self.submit_task(func, *args, priority=priority, **kwargs)
        )

    def submit_control_task(self, func, *args, **kwargs):
        """Submit a control task that runs on the writer thread WITHOUT a Session.

        Use for engine-level operations (e.g. the restore DB-file swap) that
        must serialise with normal writes but cannot tolerate a session bound
        to the current engine. The callable receives only the args/kwargs it
        was submitted with - no implicit ``session`` argument.

        Control tasks always run at IMMEDIATE priority and skip the
        ``self._engine is None`` precondition (a swap is permitted to recreate
        the engine), but still honor ``self._closed``.
        """
        if self._closed:
            future = Future()
            future.set_exception(RuntimeError("VaultDatabase is closed."))
            return future
        task = DatabaseTask(DBPriority.IMMEDIATE, func, args, kwargs, is_control=True)
        self._task_queue.put(task)
        return task.future

    def run_control_task(self, func, *args, **kwargs):
        """Submit a control task and wait for it to complete. See ``submit_control_task``."""
        return self.result_or_throw(self.submit_control_task(func, *args, **kwargs))

    def run_immediate_read_task(self, func, *args, **kwargs):
        """
        Run a database read operation without queuing.
        The function should accept a SQLModel Session as its first argument.
        This should only be used for read-only operations that need immediate results.

        Examples:

        result = db.run_immediate_read_task(lambda session: session.exec(
            select(Picture).where(Picture.quality > 0.9)
        ).all())
        """
        # Hold the read side of the engine lock for the whole read so the live
        # engine cannot be disposed/swapped out from under an open session
        # during a restore DB-file swap.
        with self._engine_rwlock.read():
            if self._closed or self._engine is None:
                raise RuntimeError("VaultDatabase is closed.")
            with Session(self._engine) as session:
                return func(session, *args, **kwargs)

    @contextmanager
    def exclusive_engine_access(self):
        """Block all ``run_immediate_read_task`` reads for the duration of the block.

        Acquires the writer side of the engine lock, waiting for in-flight
        reads to drain and preventing new ones from starting.  The restore
        DB-file swap holds this while it disposes, replaces, and re-creates the
        engine so no read can touch a disposed engine or the file mid-copy.
        """
        with self._engine_rwlock.write():
            yield

    @staticmethod
    def result_or_throw(future: Future):
        """
        Helper to get result from a Future or throw its exception.

        A task that raises a 4xx ``HTTPException`` has not failed: the services
        use it as the way to refuse a request the caller is allowed to make (a
        locked set, a name conflict, an out-of-scope token), and FastAPI turns it
        into that exact response. Logging those at ERROR with a stack trace makes
        every ordinary refusal look like a server fault and buries the real
        faults in them, so they are logged as one INFO line instead. Everything
        else - including a 5xx ``HTTPException`` - keeps the ERROR and the trace.
        """
        try:
            return future.result()
        except HTTPException as exc:
            if not 400 <= exc.status_code < 500:
                raise
            caller = inspect.currentframe().f_back
            logger.info(
                "Database task refused with %d: %s at %s:%d",
                exc.status_code,
                exc.detail,
                caller.f_code.co_filename,
                caller.f_lineno,
            )
            raise
        except Exception:
            frame = inspect.currentframe()
            caller = frame.f_back
            logger.error(
                f"Database task failed: {future.exception()} at {caller.f_code.co_filename}:{caller.f_lineno}\n"
                f"Full stack trace:\n{traceback.format_exc()}"
            )
            raise

    def _task_worker_loop(self):
        while True:
            try:
                task = self._task_queue.get(timeout=0.2)
            except queue.Empty:
                if self._task_worker_stop_event.is_set():
                    break
                continue

            if task.func is None:
                break

            if self._closed:
                if not task.future.done():
                    task.future.set_exception(RuntimeError("VaultDatabase is closed."))
                continue

            if task.is_control:
                # Control op: runs without a session so it can dispose and
                # re-create the engine without leaving an open Session bound
                # to a now-disposed engine (whose __exit__ would try to return
                # connections to the disposed pool, possibly via the
                # after_flush metadata-hash hook).
                try:
                    result = task._context.run(task.func, *task.args, **task.kwargs)
                    task.future.set_result(result)
                except Exception as e:
                    task.future.set_exception(e)
                continue

            if self._engine is None:
                if not task.future.done():
                    task.future.set_exception(RuntimeError("VaultDatabase is closed."))
                continue

            with Session(self._engine) as session:
                _attach_session_hooks(session)
                try:
                    result = task._context.run(
                        task.func, session, *task.args, **task.kwargs
                    )
                    task.future.set_result(result)
                except Exception as e:
                    session.rollback()
                    task.future.set_exception(e)
