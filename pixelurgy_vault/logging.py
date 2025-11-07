import logging
from uvicorn.logging import ColourizedFormatter

LOG_FORMAT = "%(asctime)s %(levelprefix)s %(name)s: %(message)s"
LOG_LEVEL = logging.INFO


def setup_logging(log_file=None, log_level=LOG_LEVEL):
    """Configure root logging handlers and level.

    If *log_file* is provided, logs are written there with a standard formatter.
    Otherwise logs are emitted to stdout with Uvicorn's colourised formatter.
    *log_level* accepts either an int or name understood by logging._checkLevel.
    """
    root = logging.getLogger()
    root.handlers = []  # Remove any default handlers
    if log_file:
        handler = logging.FileHandler(log_file)
        # Use standard format for file logging
        formatter = logging.Formatter(
            fmt="%(asctime)s %(levelname)s %(name)s: %(message)s"
        )
    else:
        handler = logging.StreamHandler()
        formatter = ColourizedFormatter(fmt=LOG_FORMAT, use_colors=True)
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(log_level)


def get_logger(name=None):
    return logging.getLogger(name)
