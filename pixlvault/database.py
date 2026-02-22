import inspect
import math
import os
import threading
import queue
from pathlib import Path
from concurrent.futures import Future
from enum import IntEnum
from sqlalchemy import event, inspect as sa_inspect
from sqlmodel import create_engine, Session
from rapidfuzz.distance import Levenshtein

from pixlvault.pixl_logging import get_logger
from pixlvault.picture_utils import PictureUtils

# These imports are necessary to register the models with SQLModel

# The following imports are required to register all models with SQLModel.
# They may appear unused, but are necessary for correct table creation and ORM operation.
from pixlvault.db_models import Character, Face, FaceTag  # noqa: F401
from pixlvault.db_models import HandTag  # noqa: F401
from pixlvault.db_models import PictureLikeness, PictureSet, Picture, Quality, Tag, User  # noqa: F401


# Priority enum for DB operations
class DBPriority(IntEnum):
    LOW = 30
    MEDIUM = 20
    HIGH = 10
    IMMEDIATE = 0


# Database task for the queue
class DatabaseTask:
    def __init__(self, priority, func, args=(), kwargs=None):
        self.priority = priority
        self.func = func
        self.args = args
        self.kwargs = kwargs or {}
        self.future = Future()

    def __lt__(self, other):
        return self.priority < other.priority


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
    softmin_value = (
        sum(d * exp_neg for d, exp_neg in zip(distances, exp_neg_dists)) / sum_exp
    )
    return softmin_value


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


def init_database(dbapi_conn, conn_record):
    dbapi_conn.create_function("levenshtein", 2, levenshtein)
    dbapi_conn.create_function("levenshtein_with_id", 3, levenshtein_with_id)
    dbapi_conn.create_function("cosine_similarity", 2, PictureUtils.cosine_similarity)

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

    repo_root = Path(__file__).resolve().parents[1]
    alembic_ini = repo_root / "alembic.ini"
    migrations_dir = repo_root / "migrations"

    if not alembic_ini.exists() or not migrations_dir.exists():
        raise RuntimeError(
            f"Alembic config missing. Expected {alembic_ini} and {migrations_dir}."
        )

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
                "Existing database without Alembic version table detected; stamping head."
            )
            command.stamp(config, "head")
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
        logger.info(f"Vault init, db_path={self._db_path}, db_exists={db_exists}")

        self._engine = create_engine(f"sqlite:///{self._db_path}", echo=False)
        event.listen(self._engine, "connect", init_database)

        _run_migrations(self._engine, self._db_path, db_exists)
        _ensure_user_stack_strictness(self._engine)

        # Write queue and worker
        self._task_queue = queue.PriorityQueue()
        self._task_worker_stop_event = threading.Event()
        self._task_worker = threading.Thread(target=self._task_worker_loop, daemon=True)
        self._task_worker.start()

    def close(self):
        """
        Cleanly close the database engine and stop the worker thread.
        """
        import gc

        try:
            self._task_worker_stop_event.set()
            if self._task_worker:
                self._task_worker.join(timeout=5)
                self._task_worker = None
        except Exception as e:
            logger.warning(f"VaultDatabase: Exception during worker thread stop: {e}")
        # Attempt to close SQLAlchemy engine
        if hasattr(self, "_engine") and self._engine:
            try:
                self._engine.dispose()
                self._engine = None
                logger.info("VaultDatabase: SQLAlchemy engine disposed.")
            except Exception as e:
                logger.warning(f"VaultDatabase: Exception during engine dispose: {e}")

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
        with Session(self._engine) as session:
            result = func(session, *args, **kwargs)
        return result

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
        while not self._task_worker_stop_event.is_set():
            task = self._task_queue.get()
            with Session(self._engine) as session:
                try:
                    result = task.func(session, *task.args, **task.kwargs)
                    task.future.set_result(result)
                except Exception as e:
                    session.rollback()
                    task.future.set_exception(e)
