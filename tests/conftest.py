"""
Pytest configuration and fixtures for test suite.
"""

import gc
import json
import math
import socket
import statistics
import warnings
from collections.abc import Mapping, Sequence
from pathlib import Path

from _pytest.config.exceptions import UsageError
from fastapi.testclient import TestClient
from pixlstash.server import Server
from pixlstash.utils import model_cache
from pixlstash.tasks.face_extraction_task import FaceExtractionTask
from pixlstash.tasks.image_embedding_task import ImageEmbeddingTask
from pixlstash.tasks.tag_task import TagTask

_API_V1_PREFIX = "/api/v1"
_NON_API_ROOT_PATHS = {
    "/",
    "/version",
    "/favicon.ico",
}

# Recorded per-test wall clock, used by --ci-shard to balance shards by TIME
# rather than by test count. Committed on purpose (see the module docstring of
# scripts/record_test_durations.py): every shard runs in its own process on its
# own runner and they must agree on the partition without talking to each
# other, so the input has to be identical, versioned with the code, and present
# on the very first run — including on fork PRs, which cannot read caches or
# artifacts. Staleness is the price, and it is a cheap one: an unknown test
# just falls back to its round-robin position.
_TEST_DURATIONS_PATH = Path(__file__).resolve().parent / "ci_test_durations.json"

# Floor charged to every test on top of its recorded time. No test is free —
# collection, fixture teardown and reporting all cost something — but the real
# reason this exists is arithmetic: a greedy "put it on the cheapest shard"
# loop never changes the cheapest shard when the item costs 0.0, so every
# sub-millisecond test lands on the SAME runner. Measured without this floor:
# 648 tests on one shard against ~153 on each of the others. The load was
# balanced and the count was absurd, and the count is not free either.
_PER_TEST_OVERHEAD_SECONDS = 0.005


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
        help="Run only the INDEX-th of TOTAL slices of the collected tests "
        "(1-based, e.g. '2/6'), balanced by RECORDED TEST TIME using "
        "tests/ci_test_durations.json (longest-processing-time-first). Tests "
        "with no recorded duration fall back to their round-robin position, "
        "and a missing or unusable durations file degrades the whole deal back "
        "to round-robin. Used by the blocking CI matrices to split the suite "
        "across runners. The union of all TOTAL shards is exactly the "
        "collected suite in every one of those cases, so coverage never "
        "depends on a hand-written list nor on the durations data being fresh.",
    )
    parser.addoption(
        "--ci-block-shard",
        type=str,
        default=None,
        metavar="INDEX/TOTAL",
        help="Like --ci-shard, but each shard is a CONTIGUOUS block of the "
        "collected suite instead of a round-robin deal, so collection order is "
        "preserved inside every shard. Used by the informational release-prep "
        "sweep, whose job is to detect order dependence: round-robin would "
        "reorder the very thing that sweep exists to check. Mutually exclusive "
        "with --ci-shard.",
    )


def _parse_ci_shard(spec: str, option: str = "--ci-shard") -> tuple[int, int]:
    """Parse an ``INDEX/TOTAL`` shard spec into zero-based (index, total)."""
    try:
        index_text, total_text = spec.split("/", 1)
        index = int(index_text)
        total = int(total_text)
    except ValueError as exc:
        raise UsageError(
            f"{option} expects INDEX/TOTAL (e.g. '2/6'), got {spec!r}"
        ) from exc
    if total < 1 or not (1 <= index <= total):
        raise UsageError(
            f"{option} index must be within 1..TOTAL and TOTAL >= 1, got {spec!r}"
        )
    return index - 1, total


def _load_recorded_durations(path: Path | None = None) -> dict[str, float]:
    """Return the recorded ``nodeid -> seconds`` map, or ``{}`` if unusable.

    Every failure path returns an empty map after warning, because the only
    thing the sharder must never do is drop or duplicate a test. An empty map
    makes ``--ci-shard`` behave exactly as it did before time-balancing existed
    (a pure round-robin deal), which is a slower gate but still a total
    partition. Missing file, unreadable file, truncated JSON, wrong shape and
    nonsense values are therefore all *degradations*, never errors — but none
    of them are silent.
    """
    path = _TEST_DURATIONS_PATH if path is None else path
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        warnings.warn(
            f"Could not read the CI test-duration map at {path}: {exc!r}. "
            "--ci-shard falls back to a round-robin deal, so the partition is "
            "still complete but the shards will be balanced by test count "
            "rather than by time. Regenerate it with "
            "scripts/record_test_durations.py.",
            stacklevel=2,
        )
        return {}

    entries = raw.get("durations") if isinstance(raw, dict) else None
    if not isinstance(entries, dict):
        warnings.warn(
            f"The CI test-duration map at {path} has no `durations` object "
            f"(top level is {type(raw).__name__}); ignoring it and falling "
            "back to a round-robin deal.",
            stacklevel=2,
        )
        return {}

    durations: dict[str, float] = {}
    rejected: list[str] = []
    for nodeid, value in entries.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            rejected.append(str(nodeid))
            continue
        try:
            # A hand-edited map can hold a JSON integer too large for a float
            # (Python parses it exactly, so it passes the isinstance guard and
            # only fails on conversion). Reject it like any other unusable
            # value: this runs during collection on every shard, so letting it
            # escape would take the whole gate down over one bad entry.
            seconds = float(value)
        except (OverflowError, ValueError):
            rejected.append(str(nodeid))
            continue
        if not math.isfinite(seconds) or seconds < 0.0:
            rejected.append(str(nodeid))
            continue
        durations[str(nodeid)] = seconds
    if rejected:
        warnings.warn(
            f"Ignoring {len(rejected)} entries with non-finite, negative or "
            f"non-numeric durations in {path} (first few: {rejected[:5]}). "
            "Those tests are placed by round-robin position instead.",
            stacklevel=2,
        )
    return durations


def _time_balanced_shard_assignment(
    nodeids: Sequence[str], total: int, durations: Mapping[str, float]
) -> list[int]:
    """Return the zero-based shard for every collected position.

    Longest-processing-time-first (LPT): take the tests whose duration is known,
    heaviest first, and drop each into whichever shard is currently cheapest.
    That is the classic greedy makespan heuristic — worst case 4/3 of optimal,
    and much closer than that whenever no single test is a large fraction of a
    shard's load, which is the case here.

    Two properties matter more than the balance:

    * **Total.** Every position starts on its round-robin shard and is only ever
      *moved*, so a test that is new, renamed, or simply absent from the
      durations map still lands in exactly one shard. With an empty map the
      result is byte-for-byte the old round-robin deal.
    * **Deterministic.** The eight shards compute this independently, in
      separate processes on separate runners, and must agree. Nothing here reads
      the clock, an RNG, or an unordered container: ties in duration break on
      nodeid then collection position, and ties in shard load break on the
      lowest shard index.

    Unknown tests are charged the median known cost while they sit on their
    round-robin shard, so the greedy placement starts from a realistic load
    instead of pretending those shards are empty. Every test also carries
    ``_PER_TEST_OVERHEAD_SECONDS`` on top of its recorded time, which is what
    stops the several hundred sub-millisecond tests from collapsing onto one
    shard.
    """
    assignment = [position % total for position in range(len(nodeids))]
    if total < 2:
        return assignment

    known = [
        (position, durations[nodeid] + _PER_TEST_OVERHEAD_SECONDS)
        for position, nodeid in enumerate(nodeids)
        if nodeid in durations
    ]
    if not known:
        return assignment

    estimate = statistics.median([seconds for _, seconds in known])
    known_positions = {position for position, _ in known}
    loads = [0.0] * total
    for position in range(len(nodeids)):
        if position not in known_positions:
            loads[position % total] += estimate

    for position, seconds in sorted(
        known, key=lambda entry: (-entry[1], nodeids[entry[0]], entry[0])
    ):
        target = min(range(total), key=lambda shard: (loads[shard], shard))
        assignment[position] = target
        loads[target] += seconds
    return assignment


def _block_shard_bounds(count: int, index: int, total: int) -> tuple[int, int]:
    """Return the ``[start, stop)`` bounds of block *index* of *total*.

    Splits ``range(count)`` into ``total`` contiguous blocks whose sizes differ
    by at most one: the first ``count % total`` blocks get one extra item. The
    blocks tile ``0..count`` exactly, so the partition is complete and disjoint,
    and because each block is a slice, relative order inside a block is the
    original collection order.
    """
    base, remainder = divmod(count, total)
    start = index * base + min(index, remainder)
    stop = start + base + (1 if index < remainder else 0)
    return start, stop


def pytest_collection_modifyitems(config, items):
    """Keep only the tests belonging to this shard, if one was requested.

    Sharding is applied to whatever pytest *collected*, so the CI matrix never
    names test files: adding ``tests/test_new_thing.py`` puts it in a shard
    automatically. That makes "every test is gated" a property of collection
    rather than of a hand-maintained allowlist, which is the failure mode that
    previously left most of ``tests/`` running only in the non-blocking
    release-prep sweep.

    Two modes, deliberately distinct, because they serve opposite goals:

    ``--ci-shard`` balances WALL CLOCK. It places tests by recorded duration
    (longest-processing-time-first over ``tests/ci_test_durations.json``),
    falling back to a ``position % total`` round-robin for any test the map does
    not know and for the whole deal if the map is missing or unusable. That is
    the right choice for the blocking gate, whose finish time is its slowest
    shard: dealing round-robin equalises test *count* perfectly and test *time*
    not at all, and the measured cost of that was a 1.62x spread across eight
    shards (744 s to 1205 s) with roughly 460 s of runner sitting idle every
    run. This mode does not preserve canonical execution order, in either
    variant.

    ``--ci-block-shard`` (contiguous) gives shard ``k`` the ``k``-th contiguous
    slice of the collection. Wall clock balances worse — blocks are equal in
    test *count*, not in test *time* — but relative order is preserved inside
    every shard, so an order dependence still fails wherever both tests land in
    the same block. Only the ``total - 1`` block boundaries lose adjacency. That
    is what lets the release-prep sweep stay an ordering control while running
    in parallel; sharding it round-robin would have audited the round-robin
    dealing algorithm with itself.

    Block mode is deliberately untouched by the duration data, and must stay
    that way. It is an *ordering* control, not a speed control: re-dealing its
    blocks by recorded time would destroy the only property it exists to give.
    """
    round_robin_spec = config.getoption("--ci-shard")
    block_spec = config.getoption("--ci-block-shard")
    if round_robin_spec and block_spec:
        raise UsageError(
            "--ci-shard and --ci-block-shard are mutually exclusive; pass one "
            f"(got {round_robin_spec!r} and {block_spec!r})"
        )

    if round_robin_spec:
        index, total = _parse_ci_shard(round_robin_spec, "--ci-shard")
        if total == 1:
            return
        assignment = _time_balanced_shard_assignment(
            [item.nodeid for item in items], total, _load_recorded_durations()
        )
        selected = {
            position for position, shard in enumerate(assignment) if shard == index
        }
    elif block_spec:
        index, total = _parse_ci_shard(block_spec, "--ci-block-shard")
        if total == 1:
            return
        start, stop = _block_shard_bounds(len(items), index, total)
        selected = range(start, stop)
    else:
        return

    kept = []
    deselected = []
    for position, item in enumerate(items):
        (kept if position in selected else deselected).append(item)
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

    # Reuse loaded model weights across Server/InferenceEngine instances for
    # the rest of this process. Off in production by default — see
    # pixlstash/utils/model_cache.py for the contract and the measurement that
    # motivated it. Every test that boots a Server builds a fresh engine, so
    # without this the suite reloads ViT-B-32 and a convnext_base checkpoint
    # per test function; the 508 tests that boot workers AND upload average
    # 7.11 s against 2.17 s for those that boot workers and do not.
    #
    # This does not change what any test exercises: the same weights are used,
    # the models are read-only during inference, and the objects are keyed by
    # device so a CPU and a CUDA model never collide. It is exactly the trick
    # FaceExtractionTask._global_insightface_app already plays for InsightFace.
    model_cache.enable()


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

    try:
        # Must run alongside the releases above, not instead of them: the cache
        # holds the only remaining strong reference to weights the per-task
        # helpers have already dropped, and ORT/CUDA free their arenas only on
        # collection. Skipping this would keep them resident until interpreter
        # exit — the teardown window these helpers exist to control.
        model_cache.clear()
    except Exception:
        # Best-effort teardown: ignore cleanup failures during session shutdown.
        pass

    gc.collect()
