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
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import Future
from enum import IntEnum
from typing import Optional
from sqlalchemy import event, inspect as sa_inspect, update as sa_update, select as sa_select
from sqlalchemy.orm import attributes as orm_attributes
from sqlmodel import create_engine, Session
from rapidfuzz.distance import Levenshtein

import numpy as np

from pixlstash.pixl_logging import get_logger
from pixlstash.utils.image_processing.image_utils import ImageUtils

# These imports are necessary to register the models with SQLModel

# The following imports are required to register all models with SQLModel.
# They may appear unused, but are necessary for correct table creation and ORM operation.
from pixlstash.db_models import Character, Face  # noqa: F401
from pixlstash.db_models import PictureLikeness, PictureSet, Picture, Quality, Tag, User  # noqa: F401
from pixlstash.db_models import ChangeLog, Checkpoint  # noqa: F401


# ---------------------------------------------------------------------------
# Change-log configuration
# ---------------------------------------------------------------------------

# Tables whose data payloads are NOT stored in ChangeLog (regenerable or
# ephemeral). A lightweight metadata-only entry is still created for these so
# that undo_to_checkpoint can apply its timing heuristic.
CHANGE_LOG_EXCLUDED_TABLES: frozenset[str] = frozenset(
    {
        "picturelikeness",
        "picturelikenessqueue",
        "picturelikenessfrontier",
        "quality",
        "tag_prediction",
        "guest_session",
        "guest_score",
        "deleted_file_log",
        "changelog",
        "checkpoint",
        "alembic_version",
    }
)

# Tables whose full before/after data IS captured by the change-log mechanism.
# Every SQLModel table must appear in exactly one of the two sets; the
# architecture-guardrail test enforces this.
CHANGE_LOG_INCLUDED_TABLES: frozenset[str] = frozenset(
    {
        "picture",
        "face",
        "character",
        "pictureset",
        "picturesetmember",
        "picturestack",
        "project",
        "projectattachment",
        "pictureprojectmember",
        "tag",
        "metadata",
        "user",
        "usertoken",
        "import_folder",
        "reference_folder",
    }
)

# Context variables propagated from the call site to the writer-thread task.
_write_reason_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "_write_reason", default=None
)
_actor_user_id_var: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "_actor_user_id", default=None
)


# ---------------------------------------------------------------------------
# Change-log helpers
# ---------------------------------------------------------------------------


def _cl_table_name(obj) -> str:
    """Return the SQLite table name for a mapped ORM object."""
    return getattr(obj.__class__, "__tablename__", obj.__class__.__name__.lower())


def _cl_pk_json(obj) -> str:
    """Return a JSON string encoding the primary-key column(s) for *obj*."""
    try:
        state = sa_inspect(obj)
        mapper = state.mapper
        pk_col_names = {col.name for col in mapper.persist_selectable.primary_key}
        pk: dict = {}
        for attr in mapper.column_attrs:
            for col in attr.columns:
                if col.name in pk_col_names:
                    pk[attr.key] = getattr(obj, attr.key, None)
                    break
        return json.dumps(pk)
    except Exception:
        return "{}"


def _cl_serialize_state(state: dict) -> str:
    """Serialize a column-value dict to JSON, replacing BLOB values with SHA-256 markers."""
    result: dict = {}
    for k, v in state.items():
        if v is None:
            result[k] = None
        elif isinstance(v, bytes):
            result[k] = "sha256:" + hashlib.sha256(v).hexdigest()
        elif isinstance(v, datetime):
            result[k] = v.isoformat()
        else:
            try:
                json.dumps(v)
                result[k] = v
            except (TypeError, ValueError):
                result[k] = str(v)
    return json.dumps(result)


def _cl_current_state(obj) -> dict:
    """Capture all column attribute values of *obj* as a plain dict."""
    result: dict = {}
    try:
        mapper = sa_inspect(obj).mapper
        for attr in mapper.column_attrs:
            try:
                result[attr.key] = getattr(obj, attr.key, None)
            except Exception:
                pass
    except Exception:
        pass
    return result


def _cl_before_state_from_history(obj) -> dict:
    """Capture pre-flush column values for a dirty object using ORM attribute history."""
    result: dict = {}
    try:
        mapper = sa_inspect(obj).mapper
        for attr in mapper.column_attrs:
            key = attr.key
            try:
                hist = orm_attributes.get_history(obj, key)
                if hist.deleted:
                    result[key] = hist.deleted[0]
                elif hist.unchanged:
                    result[key] = hist.unchanged[0]
                elif hist.added:
                    result[key] = hist.added[0]
            except Exception:
                pass
    except Exception:
        pass
    return result


def _before_flush_handler(session, flush_context, instances) -> None:
    """Capture pre-flush state of dirty and deleted objects into session.info."""
    pending: list = session.info.setdefault("_cl_pending", [])

    for obj in list(session.dirty):
        table = _cl_table_name(obj)
        excluded = table in CHANGE_LOG_EXCLUDED_TABLES
        pk_json = _cl_pk_json(obj)
        before_json = (
            None
            if excluded
            else _cl_serialize_state(_cl_before_state_from_history(obj))
        )
        entry = {
            "op": "UPDATE",
            "table_name": table,
            "row_pk_json": pk_json,
            "before_json": before_json,
            "excluded": excluded,
        }
        if not excluded:
            entry["obj_ref"] = obj  # for after-state capture in after_flush
        pending.append(entry)

    for obj in list(session.deleted):
        table = _cl_table_name(obj)
        excluded = table in CHANGE_LOG_EXCLUDED_TABLES
        pk_json = _cl_pk_json(obj)
        before_json = None if excluded else _cl_serialize_state(_cl_current_state(obj))
        pending.append(
            {
                "op": "DELETE",
                "table_name": table,
                "row_pk_json": pk_json,
                "before_json": before_json,
                "excluded": excluded,
            }
        )

    for obj in list(session.new):
        table = _cl_table_name(obj)
        pending.append(
            {
                "op": "INSERT",
                "table_name": table,
                "row_pk_json": None,  # PK not yet assigned; filled in after_flush
                "excluded": table in CHANGE_LOG_EXCLUDED_TABLES,
                "obj_ref": obj,
            }
        )


def _after_flush_handler(session, flush_context) -> None:
    """Finalise ChangeLog entries and bulk-insert them in the same transaction."""
    from pixlstash.db_models.change_log import ChangeLog as _ChangeLog

    pending: list = session.info.get("_cl_pending", [])
    if not pending:
        return
    session.info["_cl_pending"] = []

    txn_id: str = session.info.get("_cl_txn_id", str(uuid.uuid4()))
    reason: Optional[str] = _write_reason_var.get(None)
    actor_user_id: Optional[int] = _actor_user_id_var.get(None)
    now = datetime.now(timezone.utc)
    seq: int = session.info.get("_cl_seq", 0)

    rows: list[dict] = []
    for entry in pending:
        op = entry["op"]
        excluded = entry.get("excluded", False)
        obj = entry.get("obj_ref")

        if op == "INSERT":
            pk_json = _cl_pk_json(obj) if obj is not None else "{}"
            after_json = (
                None
                if excluded
                else (_cl_serialize_state(_cl_current_state(obj)) if obj else None)
            )
        elif op == "UPDATE":
            pk_json = entry.get("row_pk_json") or "{}"
            after_json = (
                None
                if excluded
                else (_cl_serialize_state(_cl_current_state(obj)) if obj else None)
            )
        else:  # DELETE
            pk_json = entry.get("row_pk_json") or "{}"
            after_json = None

        rows.append(
            {
                "txn_id": txn_id,
                "seq_in_txn": seq,
                "table_name": entry["table_name"],
                "row_pk_json": pk_json,
                "op": op,
                "before_json": entry.get("before_json"),
                "after_json": after_json,
                "created_at": now,
                "actor_user_id": actor_user_id,
                "reason": reason,
            }
        )
        seq += 1

    session.info["_cl_seq"] = seq

    if rows:
        try:
            session.execute(_ChangeLog.__table__.insert(), rows)
        except Exception as exc:
            logger.warning("ChangeLog: failed to insert %d row(s): %s", len(rows), exc)


# ---------------------------------------------------------------------------
# Picture metadata-hash helpers
# ---------------------------------------------------------------------------

# Columns excluded from the metadata hash; matches _diff_picture's _SKIP set
# in restore_service.py so that the hash detects exactly what the preview does.
_HASH_SKIP_COLS: frozenset = frozenset({
    "id",
    "file_path",
    "created_at",
    "text_embedding",
    "image_embedding",
    "metadata_hash",
    # Derived/regenerable scores — excluded so that recalculating them
    # does not make a checkpoint appear as changed.
    "aesthetic_score",
    "smart_score",
    "text_score",
})


def _compute_picture_metadata_hash(
    session: Session, picture_id: int
) -> Optional[str]:
    """Return a SHA-256 hex digest of a picture's user-visible metadata.

    Covers all Picture columns not in ``_HASH_SKIP_COLS`` plus the sorted list
    of associated tag strings.  Called inside ``after_flush`` so all pending
    writes are already visible on the connection.

    Args:
        session: Active DB session (must be within an open transaction).
        picture_id: Primary key of the picture.

    Returns:
        Hex-encoded SHA-256 string, or None if the picture is not found.
    """
    pic = session.get(Picture, picture_id)
    if pic is None:
        return None
    col_vals: dict = {}
    for col in pic.__fields__:
        if col in _HASH_SKIP_COLS:
            continue
        val = getattr(pic, col, None)
        if isinstance(val, np.ndarray):
            continue
        if hasattr(val, "isoformat"):
            val = val.isoformat()
        col_vals[col] = val
    tags = sorted(
        session.execute(
            sa_select(Tag.tag).where(Tag.picture_id == picture_id)
        ).scalars().all()
    )
    state = {"cols": col_vals, "tags": tags}
    return hashlib.sha256(
        json.dumps(state, sort_keys=True, default=str).encode()
    ).hexdigest()


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


def _after_flush_hash_updater(session, flush_context) -> None:
    """Recompute and persist metadata_hash for dirty pictures in the same txn.

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
        for pid in dirty_pids:
            new_hash = _compute_picture_metadata_hash(session, pid)
            if new_hash is not None:
                session.execute(
                    sa_update(Picture)
                    .where(Picture.id == pid)
                    .values(metadata_hash=new_hash)
                )
                # Expire the in-memory attribute so it reflects the new value
                # on next access (the Core UPDATE bypasses ORM tracking).
                cached = session.identity_map.get((Picture, (pid,)))
                if cached is not None:
                    session.expire(cached, ["metadata_hash"])


def _attach_change_log_hooks(session: Session) -> None:
    """Attach per-session before_flush / after_flush event listeners."""
    event.listen(session, "before_flush", _before_flush_handler)
    event.listen(session, "after_flush", _after_flush_handler)
    event.listen(session, "before_flush", _before_flush_hash_tracker)
    event.listen(session, "after_flush", _after_flush_hash_updater)


# Priority enum for DB operations
class DBPriority(IntEnum):
    LOW = 30
    MEDIUM = 20
    HIGH = 10
    IMMEDIATE = 0


# Database task for the queue
class DatabaseTask:
    _sequence = itertools.count()

    def __init__(self, priority, func, args=(), kwargs=None):
        self.priority = priority
        self.sequence = next(self._sequence)
        self.func = func
        self.args = args
        self.kwargs = kwargs or {}
        self.future = Future()
        # Capture the current execution context so that write_reason() and
        # actor_user_id context vars set by the caller are visible inside the
        # worker thread when the task executes.
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
        return 0.0


def init_database(dbapi_conn, conn_record):
    dbapi_conn.create_function("levenshtein", 2, levenshtein)
    dbapi_conn.create_function("levenshtein_with_id", 3, levenshtein_with_id)
    dbapi_conn.create_function("cosine_similarity", 2, ImageUtils.cosine_similarity)
    dbapi_conn.create_function("character_face_likeness", 2, character_face_likeness)

    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def _run_migrations(engine, db_path: str, db_exists: bool) -> None:
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

    if db_exists:
        inspector = sa_inspect(engine)
        table_names = [
            name
            for name in inspector.get_table_names()
            if not name.startswith("sqlite_")
        ]
        has_version = "alembic_version" in table_names
        if has_version:
            try:
                command.upgrade(config, "head")
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
                            with engine.begin() as conn:
                                conn.exec_driver_sql("DELETE FROM alembic_version")
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
            return

    try:
        command.upgrade(config, "head")
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
                    with engine.begin() as conn:
                        conn.exec_driver_sql("DELETE FROM alembic_version")
                    command.stamp(config, "head")
                else:
                    raise
            return
        raise


def _ensure_user_stack_strictness(engine) -> None:
    inspector = sa_inspect(engine)
    if "user" not in inspector.get_table_names():
        return
    with engine.begin() as conn:
        existing_cols = {
            row[1] for row in conn.exec_driver_sql("PRAGMA table_info('user')")
        }
        if "stack_strictness" in existing_cols:
            return
        conn.exec_driver_sql(
            "ALTER TABLE user ADD COLUMN stack_strictness FLOAT DEFAULT 0.92"
        )


class VaultDatabase:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self.image_root = os.path.dirname(self._db_path)
        db_exists = os.path.exists(self._db_path)
        logger.debug(f"Vault init, db_path={self._db_path}, db_exists={db_exists}")

        self._engine = create_engine(f"sqlite:///{self._db_path}", echo=False)
        event.listen(self._engine, "connect", init_database)

        _run_migrations(self._engine, self._db_path, db_exists)
        _ensure_user_stack_strictness(self._engine)

        # Write queue and worker
        self._task_queue = queue.PriorityQueue()
        self._task_worker_stop_event = threading.Event()
        self._close_lock = threading.Lock()
        self._closed = False
        self._task_worker = threading.Thread(target=self._task_worker_loop, daemon=True)
        self._task_worker.start()

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
        if self._closed or self._engine is None:
            raise RuntimeError("VaultDatabase is closed.")
        with Session(self._engine) as session:
            return func(session, *args, **kwargs)

    @contextmanager
    def write_reason(self, reason: str, actor_user_id=None):
        """Context manager that labels all ChangeLog rows produced within its block.

        Args:
            reason: Human-readable description of the write operation.
            actor_user_id: ID of the user triggering the write, if known.
        """
        reason_token = _write_reason_var.set(reason)
        actor_token = _actor_user_id_var.set(actor_user_id)
        try:
            yield
        finally:
            _write_reason_var.reset(reason_token)
            _actor_user_id_var.reset(actor_token)

    @staticmethod
    def result_or_throw(future: Future):
        """
        Helper to get result from a Future or throw its exception. Logs full stack trace.
        """
        import traceback

        try:
            return future.result()
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

            if self._closed or self._engine is None:
                if not task.future.done():
                    task.future.set_exception(RuntimeError("VaultDatabase is closed."))
                continue

            with Session(self._engine) as session:
                _attach_change_log_hooks(session)
                session.info["_cl_txn_id"] = str(uuid.uuid4())
                session.info["_cl_seq"] = 0
                session.info["_cl_pending"] = []
                try:
                    result = task._context.run(
                        task.func, session, *task.args, **task.kwargs
                    )
                    task.future.set_result(result)
                except Exception as e:
                    session.rollback()
                    task.future.set_exception(e)
