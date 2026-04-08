"""
Pytest configuration and fixtures for test suite.
"""

import gc
import socket

from fastapi.testclient import TestClient
from pixlstash.picture_tagger import PictureTagger
from pixlstash.server import Server
from pixlstash.tasks.face_extraction_task import FaceExtractionTask
from pixlstash.tasks.image_embedding_task import ImageEmbeddingTask
from pixlstash.tasks.tag_task import TagTask

_API_V1_PREFIX = "/api/v1"
_NON_API_ROOT_PATHS = {
    "/",
    "/version",
    "/version/latest",
    "/favicon.ico",
}


def _normalize_test_path(path: str):
    if not isinstance(path, str):
        return path
    if not path.startswith("/"):
        return path
    if path.startswith(_API_V1_PREFIX):
        return path
    if path in _NON_API_ROOT_PATHS:
        return path
    return f"{_API_V1_PREFIX}{path}"


def _patch_test_client_api_prefix() -> None:
    for method_name in ("get", "post", "put", "patch", "delete", "websocket_connect"):
        original = getattr(TestClient, method_name)

        def _make_wrapper(original_method):
            def _wrapped(self, url, *args, **kwargs):
                return original_method(self, _normalize_test_path(url), *args, **kwargs)

            return _wrapped

        setattr(TestClient, method_name, _make_wrapper(original))


_patch_test_client_api_prefix()


def _find_free_port() -> int:
    """Return an ephemeral port number that is free on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("localhost", 0))
        return sock.getsockname()[1]


def pytest_addoption(parser):
    """Add custom command line options."""
    parser.addoption(
        "--force-cpu",
        action="store_true",
        default=False,
        help="Force CPU inference for all models (disable GPU usage)",
    )
    parser.addoption(
        "--fast-captions",
        action="store_true",
        default=False,
        help="Use minimal tokens for faster caption generation (for CI)",
    )
    parser.addoption(
        "--max-vram-gb",
        type=float,
        default=None,
        help="VRAM budget in GB applied to all Server instances (e.g. 4.0). "
        "Overrides the persisted user config value.",
    )


def pytest_configure(config):
    """Set static attributes on PictureTagger from command line options."""
    # Pick a free port for the test session so Server instances don't collide
    # with the production app when it is already running on the default port.
    Server.DEFAULT_PORT = _find_free_port()
    force_cpu = config.getoption("--force-cpu")
    PictureTagger.FORCE_CPU = force_cpu
    # Persist force-cpu as a Server-level override so startup checks cannot
    # clobber the flag after conftest sets it (startup checks set FORCE_CPU
    # based on the server config's default_device value).
    Server.DEFAULT_FORCE_CPU = True if force_cpu else None
    PictureTagger.FAST_CAPTIONS = config.getoption("--fast-captions")
    Server.DEFAULT_MAX_VRAM_GB = config.getoption("--max-vram-gb")


def pytest_sessionfinish(session, exitstatus):
    """Release native model/session resources before interpreter teardown."""
    try:
        # Drain optional CPU spillover tagger if one was created by tag tasks.
        TagTask._release_idle_cpu_spillover_tagger(force=True)
    except Exception:
        # Best-effort teardown: ignore spillover tagger cleanup failures.
        pass

    try:
        FaceExtractionTask.release_detection_models()
    except Exception:
        # Best-effort teardown: model release can fail during interpreter
        # shutdown, and this should not affect test session completion.
        pass

    try:
        ImageEmbeddingTask.release_models()
    except Exception:
        pass

    # Encourage deterministic finalization of native-backed objects.
    gc.collect()
