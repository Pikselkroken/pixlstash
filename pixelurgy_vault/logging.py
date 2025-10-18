import logging
from uvicorn.logging import ColourizedFormatter

# Your own logs: colored level, module name
LOG_FORMAT = "%(levelprefix)s %(name)s: %(message)s"
LOG_LEVEL = logging.INFO

formatter = ColourizedFormatter(fmt=LOG_FORMAT, use_colors=True)
handler = logging.StreamHandler()
handler.setFormatter(formatter)

root = logging.getLogger()
root.handlers = []  # Remove any default handlers
root.addHandler(handler)
root.setLevel(LOG_LEVEL)


def get_logger(name=None):
    return logging.getLogger(name)
