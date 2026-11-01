"""Share tokens must not reach the server log through uvicorn's request lines."""

import logging
import logging.config

import pytest

from pixlstash.pixl_logging import RedactTokenFilter, uvicorn_log_config


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def uvicorn_loggers():
    """Apply the real uvicorn log config and capture both request loggers."""
    logging.config.dictConfig(uvicorn_log_config)
    capture = _Capture()
    names = ("uvicorn.access", "uvicorn.error")
    for name in names:
        logging.getLogger(name).addHandler(capture)
    try:
        yield capture
    finally:
        for name in names:
            logging.getLogger(name).removeHandler(capture)


def test_access_line_token_is_redacted(uvicorn_loggers):
    # uvicorn.access logs: client, method, path-with-query, http version, status.
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d',
        "127.0.0.1:5000",
        "GET",
        "/api/v1/pictures/7/thumbnail?token=test-secret-token&size=256",
        "1.1",
        200,
    )
    (line,) = uvicorn_loggers.lines
    assert "test-secret-token" not in line
    assert "?token=<redacted>&size=256" in line


def test_websocket_line_token_is_redacted(uvicorn_loggers):
    # The WebSocket accept line goes to uvicorn.error, not uvicorn.access.
    logging.getLogger("uvicorn.error").info(
        '%s - "WebSocket %s" [accepted]',
        "127.0.0.1:5000",
        "/ws/updates?client=a&token=test-secret-token",
    )
    (line,) = uvicorn_loggers.lines
    assert "test-secret-token" not in line
    assert "&token=<redacted>" in line


def test_lines_without_a_token_are_untouched():
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, "%s", ("/x?share_token=ok",), None
    )
    assert RedactTokenFilter().filter(record)
    assert record.getMessage() == "/x?share_token=ok"
