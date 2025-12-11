import logging as logging_
import time
from uvicorn.logging import ColourizedFormatter

LOG_FORMAT = "%(asctime)s %(levelprefix)s %(name)s: %(message)s"
LOG_LEVEL = logging_.INFO


class PixlVaultColourizedHandler(logging_.StreamHandler):
    def __init__(self, stream=None):
        super().__init__(stream)
        formatter = ColourizedFormatter(fmt=LOG_FORMAT, use_colors=True)
        formatter.converter = time.gmtime  # Use UTC for asctime if desired
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
        handler = PixlVaultColourizedHandler()
    root.addHandler(handler)
    root.setLevel(log_level)


def get_logger(name=None):
    return logging_.getLogger(name)


# For Uvicorn log_config usage:
uvicorn_log_config = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "()": ColourizedFormatter,
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
    },
    "root": {"handlers": ["default"], "level": "INFO"},
}
