"""Rebuild ``tests/ci_test_durations.json`` from pytest ``--durations`` output.

The blocking backend gate splits the suite with ``--ci-shard i/8``. That deal
used to be a positional round-robin, which balances test *count* exactly and
test *time* not at all: eight shards of the same run measured 744 s to 1205 s,
so the gate waited ~460 s on its slowest shard every time. ``--ci-shard`` now
places tests longest-processing-time-first using the map this script writes.

**Why the map is committed rather than cached or downloaded as an artifact.**
The eight shards run in eight processes on eight runners and never talk to each
other, yet they must compute the *same* partition or tests get dropped and
duplicated. That makes the input a correctness input, not a cache:

* a committed file is identical for every shard by construction, is reviewable
  in the diff that changes it, and exists on the very first run;
* fork PRs cannot read this repository's caches or a previous run's artifacts,
  so an artifact-based map would silently give forks a different (or empty)
  input from the one the base branch used;
* a cache restore can partially fail or race a concurrent write, which is a new
  CI failure mode for a job whose whole point is to be trustworthy.

The price is staleness, and it is deliberately cheap: an unrecorded test is
placed by its round-robin position, and an unusable map degrades the entire
deal back to round-robin. Both are still exact partitions, just slower ones. So
refreshing this file is an optimisation chore, never a correctness obligation.

Usage
-----
Record from a local run::

    python -m pytest -q --durations=0 --durations-min=0 --force-cpu \\
        --fast-captions tests/ > durations.txt
    python scripts/record_test_durations.py durations.txt

Or from the eight shards of a real CI run, whose logs already carry the flags
(``PYTEST_FLAGS`` in ``.github/workflows/ci.yml``)::

    gh run view <run-id> --log > ci.log
    python scripts/record_test_durations.py ci.log

The parser tolerates GitHub's ``job\\tstep\\ttimestamp`` log prefix, so the raw
``gh run view --log`` output can be piped straight in.
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT = REPO_ROOT / "tests" / "ci_test_durations.json"

# A pytest durations line, e.g. "12.34s call     tests/test_server.py::test_x".
# Anchored on the trailing nodeid so a GitHub log prefix in front is harmless,
# and restricted to `tests/....py::...` so ordinary log chatter cannot match.
# The nodeid runs to end of line rather than to the next space, because a
# parametrised id can contain spaces - `...::test_x[Clementine holding a black
# assault rifle]`. Requiring `\S+` there silently dropped 19 real tests from an
# otherwise complete map, which is exactly the kind of quiet gap that leaves a
# slow test unbalanced forever.
_DURATION_LINE_RE = re.compile(
    r"(?:^|\s)(\d+\.\d+)s\s+(setup|call|teardown)\s+(tests/\S+\.py::.+?)\s*$"
)


def parse_durations(lines) -> dict[str, float]:
    """Sum the setup/call/teardown times of every test found in *lines*.

    Durations are summed rather than taking ``call`` alone because a shard pays
    for setup and teardown too, and some of the slowest tests here are slow
    precisely in their fixtures.

    When the same nodeid appears more than once (several shards' logs
    concatenated, or a rerun), the LARGEST total wins. Over-estimating a test
    costs a little balance; under-estimating it puts a long test on a shard
    that has no room for it, which is the failure this whole mechanism exists
    to avoid.
    """
    phases: dict[str, dict[str, float]] = defaultdict(dict)
    for line in lines:
        match = _DURATION_LINE_RE.search(line.rstrip("\n"))
        if match is None:
            continue
        seconds, phase, nodeid = match.groups()
        previous = phases[nodeid].get(phase)
        value = float(seconds)
        if previous is None or value > previous:
            phases[nodeid][phase] = value
    return {nodeid: round(sum(p.values()), 3) for nodeid, p in phases.items()}


def build_document(durations: dict[str, float], sources: list[str]) -> dict:
    """Wrap *durations* in the on-disk document the sharder reads."""
    return {
        "version": 1,
        "note": (
            "Per-test wall clock in seconds (setup + call + teardown), used by "
            "--ci-shard to balance the backend gate's shards by time. "
            "Regenerate with scripts/record_test_durations.py. Stale or "
            "missing entries are safe: those tests fall back to their "
            "round-robin position."
        ),
        "recorded_from": sources,
        "test_count": len(durations),
        "total_seconds": round(sum(durations.values()), 3),
        "durations": dict(sorted(durations.items())),
    }


def main(argv: list[str] | None = None) -> int:
    """Parse pytest output into the committed durations map."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "inputs",
        nargs="*",
        type=Path,
        help="Files containing pytest --durations output. Reads stdin if none.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Where to write the map (default: {DEFAULT_OUTPUT}).",
    )
    parser.add_argument(
        "--source",
        default="",
        help="Free-text provenance recorded in the file, e.g. a CI run URL.",
    )
    parser.add_argument(
        "--min-tests",
        type=int,
        default=100,
        help=(
            "Refuse to write fewer than this many tests. Guards against "
            "silently replacing a good map with the two lines that a truncated "
            "log happened to contain."
        ),
    )
    args = parser.parse_args(argv)

    if args.inputs:
        durations: dict[str, float] = {}
        for path in args.inputs:
            with path.open(encoding="utf-8", errors="replace") as handle:
                for nodeid, seconds in parse_durations(handle).items():
                    durations[nodeid] = max(durations.get(nodeid, 0.0), seconds)
        sources = [str(path) for path in args.inputs]
    else:
        durations = parse_durations(sys.stdin)
        sources = ["<stdin>"]
    if args.source:
        sources = [args.source]

    if len(durations) < args.min_tests:
        print(
            f"Refusing to write {len(durations)} tests (--min-tests="
            f"{args.min_tests}). Did the input contain a full "
            "`--durations=0 --durations-min=0` report?",
            file=sys.stderr,
        )
        return 1

    document = build_document(durations, sources)
    args.output.write_text(
        json.dumps(document, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    print(
        f"Wrote {len(durations)} test durations "
        f"({document['total_seconds']:.1f}s total) to {args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
