
import logging
from uvicorn.logging import ColourizedFormatter

LOG_FORMAT = "%(levelprefix)s %(name)s: %(message)s"
LOG_LEVEL = logging.INFO

def setup_logging(log_file=None):
    """
    Set up logging to a file if log_file is given, else to stdout with color.
    Uses standard format for file logging to avoid 'levelprefix' KeyError.
    """
    root = logging.getLogger()
    root.handlers = []  # Remove any default handlers
    if log_file:
        handler = logging.FileHandler(log_file)
        # Use standard format for file logging
        formatter = logging.Formatter(fmt="%(levelname)s %(name)s: %(message)s")
    else:
        handler = logging.StreamHandler()
        formatter = ColourizedFormatter(fmt=LOG_FORMAT, use_colors=True)
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(LOG_LEVEL)

def get_logger(name=None):
    return logging.getLogger(name)
