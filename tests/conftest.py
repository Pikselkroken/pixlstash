"""
Pytest configuration and fixtures for test suite.
"""

import gc
import socket

from _pytest.config.exceptions import UsageError
from fastapi.testclient import TestClient
from pixlstash.server import Server
from pixlstash.tasks.face_extraction_task import FaceExtractionTask
from pixlstash.tasks.image_embedding_task import ImageEmbeddingTask
from pixlstash.tasks.tag_task import TagTask

_API_V1_PREFIX = "/api/v1"
_NON_API_ROOT_PATHS = {
    "/",
    "/version",
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
    parser.addoption(
        "--insightface-model-pack",
        type=str,
        default=None,
        help="InsightFace model pack applied to all Server instances "
        "(e.g. 'buffalo_l' or 'auraface'). Overrides the persisted config value.",
    )
    parser.addoption(
        "--ci-shard",
        type=str,
        default=None,
        metavar="INDEX/TOTAL",
        help="Run only the INDEX-th of TOTAL equal slices of the collected "
        "tests (1-based, e.g. '2/6'). Used by the CI matrix to split the "
        "suite across runners. The union of all TOTAL shards is exactly the "
        "collected suite, so coverage never depends on a hand-written list.",
    )


def _parse_ci_shard(spec: str) -> tuple[int, int]:
    """Parse an ``INDEX/TOTAL`` shard spec into zero-based (index, total)."""
    try:
        index_text, total_text = spec.split("/", 1)
        index = int(index_text)
        total = int(total_text)
    except ValueError as exc:
        raise UsageError(
            f"--ci-shard expects INDEX/TOTAL (e.g. '2/6'), got {spec!r}"
        ) from exc
    if total < 1 or not (1 <= index <= total):
        raise UsageError(
            f"--ci-shard index must be within 1..TOTAL and TOTAL >= 1, got {spec!r}"
        )
    return index - 1, total


def pytest_collection_modifyitems(config, items):
    """Keep only the tests belonging to this ``--ci-shard`` slice.

    Sharding is applied to whatever pytest *collected*, so the CI matrix never
    names test files: adding ``tests/test_new_thing.py`` puts it in a shard
    automatically. That makes "every test is gated" a property of collection
    rather than of a hand-maintained allowlist, which is the failure mode that
    previously left most of ``tests/`` running only in the non-blocking
    release-prep sweep.

    Assignment is round-robin over the deterministic collection order, so each
    file's tests are dealt evenly across shards (a single very slow file cannot
    become one shard's critical path) and shard sizes differ by at most one.
    """
    spec = config.getoption("--ci-shard")
    if not spec:
        return
    index, total = _parse_ci_shard(spec)
    if total == 1:
        return
    kept = []
    deselected = []
    for position, item in enumerate(items):
        (kept if position % total == index else deselected).append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = kept


def pytest_configure(config):
    """Set static attributes on Server from command line options."""
    # Pick a free port for the test session so Server instances don't collide
    # with the production app when it is already running on the default port.
    Server.DEFAULT_PORT = _find_free_port()
    force_cpu = config.getoption("--force-cpu")
    # Persist force-cpu as a Server-level override so startup checks cannot
    # clobber the flag after conftest sets it (startup checks set forced_cpu
    # based on the server config's default_device value).
    Server.DEFAULT_FORCE_CPU = True if force_cpu else None
    Server.DEFAULT_FAST_CAPTIONS = config.getoption("--fast-captions")
    Server.DEFAULT_MAX_VRAM_GB = config.getoption("--max-vram-gb")
    Server.DEFAULT_INSIGHTFACE_MODEL_PACK = config.getoption("--insightface-model-pack")


def pytest_sessionfinish(session, exitstatus):
    """Release native model/session resources before interpreter teardown."""
    try:
        # Drain optional CPU spillover tagger if one was created by tag tasks.
        TagTask.release_idle_cpu_spillover_engine(force=True)
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
        # Best-effort teardown: ignore cleanup failures during session shutdown.
        pass

    # Encourage deterministic finalization of native-backed objects.
    gc.collect()
