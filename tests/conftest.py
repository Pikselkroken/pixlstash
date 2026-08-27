"""
Pytest configuration and fixtures for test suite.
"""

import gc
import hashlib
import json
import math
import os
import re
import socket
import statistics
import sys
import threading
import traceback
import warnings
from collections.abc import Mapping, Sequence
from pathlib import Path

import pixlstash
import pytest
from _pytest.config.exceptions import UsageError
from fastapi.testclient import TestClient
from pixlstash.server import Server
from pixlstash.tasks.face_extraction_task import FaceExtractionTask
from pixlstash.tasks.image_embedding_task import ImageEmbeddingTask
from pixlstash.tasks.tag_task import TagTask

# pytest appends the phase it is in to PYTEST_CURRENT_TEST.
_PYTEST_PHASE = re.compile(r" \((setup|call|teardown)\)$")

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
# on the very first run - including on fork PRs, which cannot read caches or
# artifacts. Staleness is the price, and it is a cheap one: an unknown test
# just falls back to its round-robin position.
_TEST_DURATIONS_PATH = Path(__file__).resolve().parent / "ci_test_durations.json"

# Floor charged to every test on top of its recorded time. No test is free -
# collection, fixture teardown and reporting all cost something - but the real
# reason this exists is arithmetic: a greedy "put it on the cheapest shard"
# loop never changes the cheapest shard when the item costs 0.0, so every
# sub-millisecond test lands on the SAME runner. Measured without this floor:
# 648 tests on one shard against ~153 on each of the others. The load was
# balanced and the count was absurd, and the count is not free either.
_PER_TEST_OVERHEAD_SECONDS = 0.005

# Ceiling on the gate's TOTAL test time, divided by the shard count at runtime
# so a reshard cannot silently move the effective bar.
#
# Measured from THIS run's own per-test report durations, never from
# ci_test_durations.json: that map is stale between refactors by design, so a
# guard reading it would be checking its own input and would report a healthy
# gate for tests it had never timed.
#
# Baseline: the last green develop run before this landed (Actions run
# 31307604252, N=8) summed to 6662 s of test time, worst shard 1005 s, mean
# 833 s. Only the WORST shard can trip, so the margin that matters is
# 1500 / 1005 = 1.49x, and at that run's 1.21 imbalance the gate goes red at a
# total near 9900 s rather than at 12000 s. That is still far above runner
# noise and above what a stale balance map can shift, and it fires at roughly
# half the drift back to the 2844 s/shard the gate had reached before the #796
# wave, which is the regression it exists to catch.
TEST_TIME_BUDGET_SECONDS = 12000.0


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
    nonsense values are therefore all *degradations*, never errors - but none
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
    That is the classic greedy makespan heuristic - worst case 4/3 of optimal,
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
    slice of the collection. Wall clock balances worse - blocks are equal in
    test *count*, not in test *time* - but relative order is preserved inside
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
    # Do not declare the model roots PixlStash owns. They are machine-global by
    # design - one download serves every library on the host - so a Server built
    # on a temp config dir would otherwise declare rows about the DEVELOPER'S
    # real home, and the shelf's contents would depend on which engines that
    # machine happens to have downloaded. `test_workers_api` caught it as
    # `assert 3 == 0` on a runner whose model cache was warm; no rows at all is
    # a state the suite can rely on, and `test_builtin_models.py` covers the
    # declaration directly against a `tmp_path`.
    #
    # This used to point `PIXLSTASH_BUILTIN_MODEL_DIR` at a fresh temp directory
    # instead. That stopped working when #905 made the downloaders read the same
    # accessor as the declaration - which is the whole point of that change, and
    # means an empty temp directory now costs every engine a fresh download on
    # every shard, rather than the warm model cache CI restores.
    Server.DEFAULT_DECLARE_MODEL_ROOTS = False
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


@pytest.fixture(autouse=True, scope="session")
def sandbox_the_recorded_model_locations(tmp_path_factory):
    """Put both recorded-location files somewhere the suite may safely write.

    A relocation records where PixlStash downloads its engines
    (``downloaded_models.location``) and where the InsightFace packs live
    (``insightface.location``). Both are **machine-global** - one download
    serves every library and every server instance on the host - so both live in
    the platform user data directory, next to nothing else the suite touches,
    and both outlive the process that wrote them.

    Which is how the developer's real machine ended up with a record naming a
    ``tmp_path`` from a finished test run. pytest deleted the directory; the
    accessor kept naming it; every start after that re-created the path and
    downloaded ~750 MB of engines into it, while the real ones sat untouched in
    the default folder. Nothing failed and nothing said so.

    ``pytest_configure`` above already stops the suite *declaring* these roots,
    for the same reason in the other direction: they describe the developer's
    home, not the test's. This is the write half of that decision, and it is
    made mechanically rather than remembered - three test modules redirect
    ``_pixlstash_data_dir`` per test to stay out of the way, which works right up
    until a relocation's worker thread finishes after the redirection is undone,
    or a fourth module forgets. Redirecting ``_pointer_path`` for the whole
    session leaves nothing to remember: no test can name the machine's file, so
    no test can write it.

    Only the machine's own directory is replaced. A test that redirects
    ``_pixlstash_data_dir`` itself still gets its pointer beside the directory it
    chose, which is what those modules assert on.

    **The sandbox is per test, not one shared file**, even though it is one
    directory. A record is read back on every call, so a single shared file
    would let one test's write change where every *later* test in the shard
    downloads - a flake the sharder reshuffles between runs. The redirected name
    carries the writing test's id instead, and the check below fails the run
    with whatever the sandbox holds.

    **That check is a tripwire, not a census.** It sees a write made while no
    test had redirected the seam. A write from a worker thread that lands
    *during* a later test which has redirected it goes to that test's own
    ``tmp_path`` instead - safe, which is the point, but invisible here and
    attributed to the wrong test if it does show up, since
    ``PYTEST_CURRENT_TEST`` is process-global. So an empty sandbox is not proof
    that nothing wrote; the protection is the redirection, and this only reports
    the cases it can see.

    Nothing is restored at the end. The rebinding has to outlive the session:
    a relocation records its new location from a daemon worker thread
    (``model_moves._start_job``), and the suite leaves such a thread unjoined -
    restoring the original here would reopen the machine's file for exactly the
    write this fixture exists to stop.
    """
    from pixlstash.services import builtin_models
    from pixlstash.utils import insightface_model_utils

    sandbox = str(tmp_path_factory.mktemp("recorded-model-locations"))
    machine_data_dir = os.path.realpath(builtin_models._pixlstash_data_dir())

    def _redirected(module, constant):
        def _pointer_path() -> str:
            # Both the seam and the filename are read per call: a test may have
            # redirected the first, and the second is a module constant.
            filename = getattr(module, constant)
            directory = builtin_models._pixlstash_data_dir()
            # `realpath`, not `==`: a trailing slash, a `..` or a symlink are
            # all the machine's own directory spelled differently, and a
            # redirection that reached it by one of those names would write the
            # real file. `is_builtin_model_dir` compares the same way.
            if os.path.realpath(directory) != machine_data_dir:
                return os.path.join(directory, filename)
            # One file per test. A shared one would let a record written by one
            # test change where a later test in the shard downloads, which is a
            # flake the sharder reshuffles. Hashed rather than spelled out: a
            # node id can carry spaces and run past NAME_MAX once the pointer's
            # own name is appended, and two ids must never collide here.
            # Only pytest's own phase suffix is stripped, so a record written
            # by a fixture is not invisible to the test body it was written for
            # and two parametrised ids whose params contain " (" stay apart.
            current = _PYTEST_PHASE.sub(
                "", os.environ.get("PYTEST_CURRENT_TEST", "session")
            )
            unique = hashlib.blake2b(current.encode(), digest_size=8).hexdigest()
            readable = "".join(c if c.isalnum() or c in "._-" else "_" for c in current)
            return os.path.join(sandbox, f"{unique}.{readable[-60:]}.{filename}")

        return _pointer_path

    builtin_models._pointer_path = _redirected(
        builtin_models, "BUILTIN_MODEL_DIR_POINTER"
    )
    insightface_model_utils._pointer_path = _redirected(
        insightface_model_utils, "INSIGHTFACE_ROOT_POINTER"
    )
    yield sandbox
    leaked = sorted(os.listdir(sandbox))
    assert not leaked, (
        "these tests recorded a machine-global model location, which outside "
        f"the suite lands in {machine_data_dir} and outlives the run: {leaked}. "
        "Every relocation a test starts has to finish inside it (await the 202) "
        "and the test has to redirect builtin_models._pixlstash_data_dir to a "
        "tmp_path first - tests/test_builtin_models.py::data_dir is the shape."
    )


@pytest.fixture(autouse=True)
def no_model_move_outlives_its_test():
    """Fail a test that leaves a model move running rather than the next one.

    A relocation or an import runs on a daemon thread (``_start_job``) whose
    *ending* is where the work lands: folder rows are repointed, and for the
    folder PixlStash downloads into, a machine-global location is recorded. A
    test that starts one and does not wait hands all of that to whichever test
    runs next - at a moment no fixture is holding the seams still, which is the
    shape that lets a recorded location escape the redirection above.

    Suite-wide rather than in the one module that noticed: ``_job`` is a global,
    and three test modules drive the same route. Await the ``202``.

    Not reset at set-up on purpose. Nulling a running job here would orphan the
    thread and hide the leak instead of reporting it; a slow one may accuse a
    later test as well, but the test that actually leaked it fails first and is
    the one named at the top of the report.
    """
    yield
    from pixlstash.routes import model_moves

    # Under the readers' lock, which is the contract the module states and
    # `test_the_move_worker_writes_the_job_under_the_readers_lock` pins: the
    # worker writes `status` while holding it.
    with model_moves._job_lock:
        job = model_moves._job
        status = None if job is None else job["status"]
    assert status != "running", (
        "this test left a model move running. Await it (`_await_move`) before "
        "the test ends, or its ending lands inside the next test. Tests after "
        "this one may fail for the same reason until that move finishes; this "
        "is the one that started it."
    )


def _measured_test_seconds(reporter) -> float:
    """Sum this run's own setup + call + teardown time over every phase report.

    ``stats`` holds one report per phase under the outcome key it was filed as
    (passed setup and teardown land under ``""``), which is the same
    setup+call+teardown total ``scripts/record_test_durations.py`` reconstructs
    from ``--durations`` output. Non-report entries (warnings, for example) do
    not carry a duration and are skipped.
    """
    return sum(
        report.duration
        for reports in reporter.stats.values()
        for report in reports
        if hasattr(report, "duration")
    )


def _enforce_test_time_budget(session) -> None:
    """Report the shard's measured test time when it blows the budget.

    Only active under ``--ci-shard``, so a local partial run cannot trip it.
    A signal rather than a correctness gate: every test can pass and the shard
    is still over budget, which is why the banner says exactly that. The drift
    it watches for (the gate creeping from 15 to 47 minutes per shard) has no
    other alarm.

    **It does not touch the exit status.** It used to, and that closed the loop
    the breach is normally fixed by: refreshing ``ci_test_durations.json`` is
    the standard remedy for a slow gate, and
    ``.github/workflows/record-test-durations.yml`` refuses to harvest any run
    whose conclusion is not ``success`` (a red shard may never have reported
    its durations). A shard failed purely for being slow therefore made the
    refresh impossible for exactly as long as it was needed. A red check also
    reads as "broken" to everything downstream that only sees an exit code, and
    this is not that. The signal is instead a GitHub annotation plus a step
    summary entry, both of which survive on the run page without lying about
    what happened.
    """
    spec = session.config.getoption("--ci-shard")
    if not spec:
        return
    if session.exitstatus != 0:
        # Already red for a better reason. Printing "this is not a test
        # failure" directly above a real FAILURES section is triage
        # misdirection, and the breach will still be there on the next green
        # run, so say nothing rather than something false.
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        # No terminal reporter (-p no:terminal): nothing accumulated the phase
        # reports this check measures, and there is nowhere to print the
        # banner, so the budget simply does not apply to that invocation.
        return

    _, shards = _parse_ci_shard(spec)
    measured = _measured_test_seconds(reporter)
    ceiling = TEST_TIME_BUDGET_SECONDS / shards
    if measured <= ceiling:
        return

    banner = "TEST-TIME BUDGET EXCEEDED - THIS IS NOT A TEST FAILURE"
    measurement = (
        f"This shard spent {measured:.6g}s of test time against a ceiling of "
        f"{ceiling:.6g}s ({TEST_TIME_BUDGET_SECONDS:.0f}s for the whole suite "
        f"/ {shards} shards)."
    )
    remedy = (
        "The suite got slower, it did not break. Reuse an existing "
        "module-scoped environment instead of standing up a Server per test "
        "(CLAUDE.md, 'Tests: reuse the environment, don't rebuild it'), "
        "refresh tests/ci_test_durations.json, or raise "
        "TEST_TIME_BUDGET_SECONDS in tests/conftest.py deliberately and put "
        "the measurement that justifies it in the commit message."
    )
    reporter.write_sep("=", banner, red=True, bold=True)
    reporter.write_line(f"Every test above may have passed. {measurement}")
    reporter.write_line(remedy)
    reporter.write_sep("=", banner, red=True, bold=True)
    # Written to stdout, which is where Actions reads workflow commands from.
    # Outside Actions it is one more harmless line under the banner.
    reporter.write_line(
        f"::warning title=Test-time budget exceeded::{measurement} {remedy}"
    )
    _append_step_summary(f"### {banner}\n\n{measurement}\n\n{remedy}\n")


def _append_step_summary(markdown: str) -> None:
    """Append to the GitHub Actions step summary, if we are running in one."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(markdown)
    except OSError as exc:
        # The annotation above already carries the message, so a summary that
        # cannot be written costs presentation and no signal. Say why anyway,
        # or a silently empty summary looks like the check never fired.
        print(f"Could not append to GITHUB_STEP_SUMMARY at {path}: {exc}")


# Both roots are needed. CI installs the package non-editable
# (`pip install .[test,dev]`), so `pixlstash` runs from site-packages while
# `tests/` runs from the checkout: a thread started inside product code has no
# frame under the repo root at all, and matching only there would call it
# third-party and let it through.
_OWNED_ROOTS = (
    Path(__file__).resolve().parent.parent,
    Path(pixlstash.__file__).resolve().parent,
)


def _thread_stack(thread: threading.Thread) -> list:
    """This thread's current stack, outermost frame first, or []."""
    frame = sys._current_frames().get(thread.ident)
    if frame is None:
        return []
    return traceback.extract_stack(frame)


def _owned(filename: str) -> bool:
    """Whether a frame's file is ours (the checkout or the installed package)."""
    path = Path(filename).resolve()
    return any(path.is_relative_to(root) for root in _OWNED_ROOTS)


def _is_ours(stack: list) -> bool:
    """Whether any frame in the stack belongs to code we own.

    Ownership has to be read from the WHOLE stack, not the innermost frame:
    a leaked database worker parked in ``queue.get()`` reports a stdlib
    ``queue.py`` frame, while its entry point is ``pixlstash/database.py``.
    """
    return any(_owned(entry.filename) for entry in stack)


def _enforce_no_leaked_threads(session) -> None:
    """Fail the session on any thread of ours still alive when it ends.

    A worker thread that outlives the session is a defect on its own: the
    object that owns it was never closed, so whatever that object held -
    a SQLAlchemy engine, pooled SQLite connections, an fd on vault.db - is
    still live too. It is also the leading suspect for the Windows-only
    SIGSEGV that fires *seconds after* a fully green pytest summary: CPython
    kills surviving daemon threads mid-instruction during ``Py_FinalizeEx``,
    and one that is inside ``sqlite3`` C code at that moment takes the process
    down with an access violation, long after pytest has stopped watching.

    Threads with no frame of ours anywhere in their stack are reported but not
    failed on - a third-party pool we do not own is not ours to close.
    """
    survivors = [
        (thread, _thread_stack(thread))
        for thread in threading.enumerate()
        if thread is not threading.main_thread() and thread.is_alive()
    ]
    if not survivors:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        return

    ours = [(thread, stack) for thread, stack in survivors if _is_ours(stack)]
    reporter.write_sep(
        "=",
        f"{len(survivors)} thread(s) still alive at session end",
        red=bool(ours),
        bold=bool(ours),
    )
    for thread, stack in survivors:
        top = stack[-1] if stack else None
        where = f"{top.filename}:{top.lineno} in {top.name}" if top else "<no frame>"
        owner = "OURS" if _is_ours(stack) else "third-party"
        reporter.write_line(f"  [{owner}] {thread.name} daemon={thread.daemon} {where}")
        if _is_ours(stack):
            for entry in stack:
                if _owned(entry.filename):
                    reporter.write_line(
                        f"      {entry.filename}:{entry.lineno} in {entry.name}"
                    )

    if not ours:
        return
    reporter.write_line(
        "Close the object that owns each thread above before the test that "
        "created it returns. A daemon thread left running is killed "
        "mid-instruction during interpreter finalization, which is how a green "
        "run still exits 139 on Windows."
    )
    session.exitstatus = 1


def _stop_tqdm_monitors() -> None:
    """Shut down tqdm's background monitor threads.

    tqdm starts a ``TMonitor`` daemon per tqdm class the moment any progress
    bar exists, and never stops it - a full Windows-shard run ends with two of
    them still ticking on a 10-second interval. They are third-party, so the
    leak gate above does not fail on them, but a daemon thread that wakes
    periodically is precisely what ``Py_FinalizeEx`` kills mid-instruction.
    Reached through ``threading.enumerate()`` rather than ``tqdm.tqdm.monitor``
    because each tqdm class (std, auto, the copies vendored by transformers and
    huggingface_hub) keeps its own.
    """
    for thread in threading.enumerate():
        if type(thread).__name__ != "TMonitor":
            continue
        try:
            thread.exit()
        except Exception as exc:
            print(
                f"Could not stop tqdm monitor {thread.name}: {exc!r}", file=sys.stderr
            )


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    """Release native model/session resources before interpreter teardown.

    ``trylast`` so any other session-finish work runs before this one. The
    phase reports the budget check measures are filed during the run rather
    than at teardown, so its input is complete wherever it sits.

    The budget check runs *last*, after the native handles are released: if it
    ever raises, the release below has already happened, rather than the
    session dropping into interpreter teardown still holding models.
    """
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

    _stop_tqdm_monitors()

    gc.collect()

    _enforce_no_leaked_threads(session)

    _enforce_test_time_budget(session)
