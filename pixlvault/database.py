import inspect
import math
import os
import threading
import queue
from concurrent.futures import Future
from enum import IntEnum
from sqlalchemy import event
from sqlmodel import SQLModel, create_engine, Session
from rapidfuzz.distance import Levenshtein

from pixlvault.pixl_logging import get_logger
from pixlvault.picture_utils import PictureUtils

# These imports are necessary to register the models with SQLModel

# The following imports are required to register all models with SQLModel.
# They may appear unused, but are necessary for correct table creation and ORM operation.
from pixlvault.db_models import Character, FaceLikeness, Face  # noqa: F401
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


def levenshtein_function(a, b):
    try:
        if a is None or b is None:
            return 100.0  # or some large default distance
        return float(Levenshtein.distance(str(a), str(b)))
    except Exception as e:
        logger.error(f"Levenshtein error: {e} (a={a}, b={b})")
        return 100.0  # fallback value


def softmin(distances, beta=1.0):
    import math

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


def levenshtein(concatenated_tags, query):
    # Split the concatenated tags into tags
    tags = (
        concatenated_tags.split()
        if isinstance(concatenated_tags, str)
        else [concatenated_tags]
    )
    query_words = query.split() if isinstance(query, str) else [query]

    dists = []
    for tag in tags:
        min_dist = 1.0
        for query_word in query_words:
            min_dist = min(
                min_dist,
                levenshtein_function(tag, query_word)
                / max(len(tag), len(query_word), 1),
            )
        dists.append(min_dist)

    dists = sorted(dists)
    logger.info(
        f"Best Levenshtein distances for tags '{concatenated_tags}': {dists[:5]}"
    )

    # Return softmin of these distances
    return math.pow(softmin(dists, 2.5), 3.0) if dists else 1.0


def init_database(dbapi_conn, conn_record):
    dbapi_conn.create_function("levenshtein", 2, levenshtein)
    dbapi_conn.create_function("cosine_similarity", 2, PictureUtils.cosine_similarity)

    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class VaultDatabase:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self.image_root = os.path.dirname(self._db_path)
        db_exists = os.path.exists(self._db_path)
        logger.info(f"Vault init, db_path={self._db_path}, db_exists={db_exists}")

        self._engine = create_engine(f"sqlite:///{self._db_path}", echo=False)
        event.listen(self._engine, "connect", init_database)

        SQLModel.metadata.create_all(self._engine)

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
