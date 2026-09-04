import logging as logging_
import threading
from contextlib import contextmanager

from uvicorn.logging import ColourizedFormatter as ColourisedFormatter

LOG_FORMAT = "%(asctime)s %(levelprefix)s %(name)s: %(message)s"
LOG_LEVEL = logging_.INFO

_UVICORN_NOISE_PREFIXES = (
    "Started server process",
    "Waiting for application startup",
    "Application startup complete",
    "connection open",
    "connection closed",
)


class _SuppressFilter(logging_.Filter):
    """Drop log records whose message starts with any suppressed prefix."""

    def filter(self, record: logging_.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(msg.startswith(p) for p in _UVICORN_NOISE_PREFIXES)


class PixlStashColourisedHandler(logging_.StreamHandler):
    def __init__(self, stream=None):
        super().__init__(stream)
        formatter = ColourisedFormatter(fmt=LOG_FORMAT, use_colors=True)
        self.setFormatter(formatter)


def setup_logging(log_file=None, log_level=LOG_LEVEL):
    """Configure root logging handlers and level.

    If *log_file* is provided, logs are written there with a standard formatter.
    Otherwise logs are emitted to stdout with Uvicorn's colourised formatter.
    *log_level* accepts either an int or name understood by logging_._checkLevel.
    """
    root = logging_.getLogger()
    root.handlers = []  # Remove any default handlers
    if log_file:
        # Force UTF-8: on Windows FileHandler would otherwise open with the
        # legacy ANSI codepage (cp1252) and raise UnicodeEncodeError on the
        # non-ASCII glyphs we log (arrows, box drawing, etc.).
        handler = logging_.FileHandler(log_file, encoding="utf-8")
        # Use standard format for file logging
        formatter = logging_.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s: %(message)s"
        )
        handler.setFormatter(formatter)
    else:
        handler = PixlStashColourisedHandler()
    root.addHandler(handler)
    root.setLevel(log_level)
    # Suppress noisy alembic plugin/migration INFO messages
    logging_.getLogger("alembic.runtime.plugins").setLevel(logging_.WARNING)
    logging_.getLogger("alembic.runtime.migration").setLevel(logging_.WARNING)
    # Suppress repetitive uvicorn lifecycle/connection messages
    logging_.getLogger("uvicorn.error").addFilter(_SuppressFilter())


class _HoldingHandler(logging_.Handler):
    """Collect records instead of emitting them, so a prompt owns the screen."""

    def __init__(self):
        super().__init__()
        self._records = []
        self._records_lock = threading.Lock()

    def emit(self, record: logging_.LogRecord) -> None:
        with self._records_lock:
            self._records.append(record)

    def take(self) -> list:
        with self._records_lock:
            held, self._records = self._records, []
        return held


@contextmanager
def hold_log_output():
    """Buffer log output while an interactive question is on screen.

    Start-up asks its questions after the server has been built and its
    background workers are already running, so the answer to "did anyone see
    the question?" used to be no: a first-run credentials prompt was written
    between two INFO lines, and a snapshot task logged its progress onto the
    same line as the prompt while it waited for an answer. Nothing is dropped -
    every held record is emitted, in order, once the question has been
    answered.

    Only the logging path is held. A bare ``print`` from another thread still
    reaches the terminal, so this narrows the window rather than sealing it.
    """
    root = logging_.getLogger()
    held = _HoldingHandler()
    previous = list(root.handlers)
    # Assigning a new list rather than mutating: a logging call on another
    # thread sees either the old handlers or the holder, never a half-empty
    # list it would silently drop a record into.
    root.handlers = [held]
    try:
        yield
    finally:
        root.handlers = previous
        for record in held.take():
            for handler in previous:
                if record.levelno >= handler.level:
                    handler.handle(record)


def get_logger(name=None):
    return logging_.getLogger(name)


# For Uvicorn log_config usage:
uvicorn_log_config = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "()": ColourisedFormatter,
            "fmt": LOG_FORMAT,
            "use_colors": True,
        },
    },
    "handlers": {
        "default": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "stream": "ext://sys.stdout",
        },
    },
    "loggers": {
        "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.error": {
            "handlers": ["default"],
            "level": "INFO",
            "propagate": False,
        },
        "uvicorn.access": {
            "handlers": ["default"],
            "level": "INFO",
            "propagate": False,
        },
        "alembic.runtime.plugins": {"level": "WARNING", "propagate": True},
        "alembic.runtime.migration": {"level": "WARNING", "propagate": True},
    },
    "root": {"handlers": ["default"], "level": "INFO"},
}
