import inspect
import os
import threading
import queue
from concurrent.futures import Future
from enum import IntEnum
from sqlalchemy import event
from sqlmodel import SQLModel, create_engine, Session
from rapidfuzz.distance import Levenshtein

from pixlvault.logging import get_logger
from pixlvault.picture_utils import PictureUtils
from pixlvault.db_models import Character, Conversation, FaceLikeness, Face, Message, PictureLikeness, PictureSet, Picture, Quality, Tag # noqa: F401


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
            return 100  # or some large default distance
        return Levenshtein.distance(str(a), str(b))
    except Exception as e:
        logger.error(f"Levenshtein error: {e} (a={a}, b={b})")
        return 100  # fallback value


def levenshtein(tag, query_words):
    # Split both tag and query_words into words
    if isinstance(query_words, str):
        query_words = query_words.split()
    tag_words = tag.split() if isinstance(tag, str) else [tag]
    # For each tag word, find the minimum distance to any query word
    min_dists = [
        min(levenshtein_function(tw, qw) for qw in query_words) for tw in tag_words
    ]
    # Return the average of these minimum distances
    return sum(min_dists) / len(min_dists) if min_dists else 100


def register_functions(dbapi_conn, conn_record):
    dbapi_conn.create_function("levenshtein", 2, levenshtein)
    dbapi_conn.create_function("cosine_similarity", 2, PictureUtils.cosine_similarity)


class VaultDatabase:
    def __init__(self, db_path: str):
        self._db_path = db_path
        db_exists = os.path.exists(self._db_path)
        logger.info(f"Vault init, db_path={self._db_path}, db_exists={db_exists}")

        self._engine = create_engine(f"sqlite:///{self._db_path}", echo=False)
        event.listen(self._engine, "connect", register_functions)

        SQLModel.metadata.create_all(self._engine)

        # Write queue and worker
        self._task_queue = queue.PriorityQueue()
        self._task_worker = threading.Thread(target=self._task_worker_loop, daemon=True)
        self._task_worker.start()

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
            task = self._task_queue.get()
            with Session(self._engine) as session:
                try:
                    result = task.func(session, *task.args, **task.kwargs)
                    task.future.set_result(result)
                except Exception as e:
                    session.rollback()
                    task.future.set_exception(e)
