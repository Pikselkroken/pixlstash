import logging as logging_
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
        handler = logging_.FileHandler(log_file)
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
