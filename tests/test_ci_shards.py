"""Guardrails for the sharded CI backend gate.

What these protect is a completeness property, not a performance one. CI named
individual test files in ``.github/workflows/ci.yml``, and the result was that
59 of the 99 files in ``tests/`` — including authz-gate, host-capability and
fail-closed suites — ran only in a non-blocking release-prep sweep. Nothing
failed when a new test file was added without touching the workflow, so nothing
stopped the drift, and PR #588 added a test that would have landed ungated.

The gate is still an allowlist for now (the deferred files are not yet green),
so the drift is stopped a different way: ``tests/`` must be exactly the gated
set plus the explicitly deferred set. A new test file belongs to neither until
someone says which, and that is a failure:

* ``test_every_test_file_is_classified`` fails if a file under ``tests/`` is
  neither gated in ci.yml nor listed in ``DEFERRED_FROM_GATE``. This is the
  forcing function that replaces "remember to edit the workflow".
* ``test_security_suites_cannot_be_quietly_deferred`` fails if any file in
  ``MUST_BLOCK_ON_EVERY_PR`` leaves the gate. Deferral is a legitimate tool for
  a suite that is not green yet, but it is also how a broken authz assertion
  survived five days unnoticed, so the security suites are not eligible for it.
* ``test_shard_counts_match_the_matrix`` fails if a matrix is resized without
  updating the ``i/N`` divisor, which would silently drop a slice.
* ``test_shards_partition_the_collected_suite`` proves the sharding itself is a
  partition — every collected test in exactly one shard — by actually running
  pytest's collection, not by re-implementing the arithmetic.

The second property these protect is the release-prep sweep's *ordering*
control. The blocking gate deals tests round-robin (``--ci-shard``); the sweep
runs the same suite in contiguous blocks of collection order
(``--ci-block-shard``) precisely so it can still catch an order- or
shard-dependence that round-robin dealing would mask. Sharding the sweep with
the gate's own algorithm would audit that algorithm with itself, so
``test_each_job_uses_the_sharding_mode_it_needs`` pins the mode per job.
"""

import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from tests.conftest import _block_shard_bounds

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
TESTS_DIR = REPO_ROOT / "tests"

# Which `--ci-*shard` option each sharded job is required to use. Round-robin
# balances wall clock and is right for the blocking gates; contiguous blocks
# preserve collection order and are the only mode that keeps the informational
# sweep meaningful as an ordering control.
_ROUND_ROBIN_OPTION = "--ci-shard"
_BLOCK_OPTION = "--ci-block-shard"
_SHARD_MODE_BY_JOB = {
    "backend": _ROUND_ROBIN_OPTION,
    "backend_windows": _ROUND_ROBIN_OPTION,
    "backend_release_sweep": _BLOCK_OPTION,
}

# Only the Linux `backend` job is the gate; `backend_windows` is a deliberate
# OS-sensitive subset (see the comment above it in ci.yml) and `checks` runs no
# tests at all.
_GATE_JOB = "backend"

# Suites whose only job is to prove the runtime defences actually ENFORCE what
# they declare. These MUST be on the blocking `backend` gate; they are not
# eligible for DEFERRED_FROM_GATE.
#
# Declaration completeness has always blocked every PR
# (tests/test_architecture_guardrails.py::test_all_routes_declare_access_policy
# asserts every data route carries an AccessPolicy). The behavioural half did
# not: these seven files sat in DEFERRED_FROM_GATE, so their only run was
# `backend_release_sweep` — `continue-on-error: true`, and triggered only during
# release prep. A test in test_authz_gate_step4.py that asserted on a route
# which does not exist was therefore red for five days with nothing to report
# it. The gap was not "the test was wrong"; it was "nothing blocking ever ran
# the enforcement tests".
#
# Re-deferring one of these is exactly the move that hid that bug, so it fails
# `test_security_suites_cannot_be_quietly_deferred` below. If a suite here goes
# red, fix it or delete it — parking it is not available. Adding a file here is
# fine and encouraged; removing one is a security decision that needs the
# authz sign-off in CLAUDE.md, not a CI tidy-up.
MUST_BLOCK_ON_EVERY_PR = frozenset(
    {
        # AuthzGate object-scope enforcement, steps 3 and 4 of the rollout.
        "test_authz_gate_step3.py",
        "test_authz_gate_step4.py",
        # §16.3 host-capability tiers (LOCAL_OWNER_ONLY / LOOPBACK_OWNER_ONLY).
        "test_authz_host_capability_16_3.py",
        # The loopback/IP-locality check must fail CLOSED when locality is
        # undecidable; a silent fail-open here re-opens the host-capability tier.
        "test_ip_locality_fail_closed.py",
        # Streaming variant of the picture list — a separate code path from the
        # paged list, and historically its own BOLA vector.
        "test_pictures_stream.py",
        # The ComfyUI membership filter is the one leaf `Picture.find()` does not
        # delegate to `PredicateFilter`: it hand-rolls a raw `text()` WHERE
        # fragment. An unparenthesised `OR` in it let the stack-member branch
        # escape the id/project scope narrowing for ~10 weeks (shipped in
        # 84ffdd22), so a scoped token could read outside its scope. Same risk
        # class as test_pictures_stream.py above.
        "test_comfyui_stack_filter.py",
        # Deleted-picture retention: proves scrapheap rows stay scoped and are
        # actually reaped rather than lingering readable.
        "test_scrapheap_retention.py",
        # Staged async import: uploads land in a per-session staging area before
        # they exist as scoped objects, so this is where scope is established.
        "test_async_import_staging.py",
    }
)

# Files that are deliberately NOT in the blocking gate yet. Every one of these
# still runs in the informational `backend_release_sweep`, so the coverage is
# visible; it just does not block a PR.
#
# This list is not documentation — it is half of a partition. `tests/` must be
# exactly GATED + DEFERRED, and the test below fails otherwise, so a newly added
# test file cannot quietly belong to neither. Moving a file OFF this list and
# into the ci.yml gate is the intended direction of travel; the end state is an
# empty list and a gate that just says `tests/`.
#
# Known-red as of this writing: test_smart_score_invalidation.py fails on the
# baseline (2 failures, unrelated to CI). That is what blocks the flip.
DEFERRED_FROM_GATE = frozenset(
    {
        "test_anomaly_penalty.py",
        "test_anomaly_thresholds_cache.py",
        "test_api_coverage.py",
        "test_batch_apply_scores.py",
        "test_build_desktop_runtime.py",
        "test_characters_api.py",
        "test_default_device_override.py",
        "test_detection_florence.py",
        "test_detection_model.py",
        "test_docker_windows_host_paths.py",
        "test_except_hygiene_guardrail.py",
        "test_export_api.py",
        "test_face_detection_extreme_aspect_ratio.py",
        "test_face_extraction_speed.py",
        "test_full_pipeline.py",
        "test_gfs_snapshot_schedule.py",
        "test_guest_scoring.py",
        "test_image_plugins_api.py",
        "test_impossible_clear.py",
        "test_impossible_filter.py",
        "test_insightface_model_pack.py",
        "test_justified_thumbnails.py",
        "test_likeness_and_face_search.py",
        "test_near_neighbor.py",
        "test_person_tags.py",
        "test_predicate_filter.py",
        "test_project_membership_service.py",
        "test_projects_api.py",
        "test_quality_task_shutdown.py",
        "test_reference_folder_listing_count_parity.py",
        "test_reference_folder_sidecars.py",
        "test_reviews_api.py",
        "test_rocm_device_check.py",
        "test_server_external_listener.py",
        "test_server_simple.py",
        "test_smart_score_invalidation.py",
        "test_snapshot_compression.py",
        "test_stack_position_invariant.py",
        "test_stacks_api.py",
        "test_stacks_membership.py",
        "test_startup_banner_encoding.py",
        "test_stats_api.py",
        "test_tag_health_api.py",
        "test_tag_prediction_backfill.py",
        "test_tag_predictions_api.py",
        "test_tag_suggestions_api.py",
        "test_tag_task.py",
        "test_tagger_plugin_registry.py",
        "test_tagger_runs_api.py",
        "test_user_settings_tagger_settings.py",
        "test_workers_api.py",
        "test_ws_broadcaster.py",
    }
)


@pytest.fixture(scope="module")
def workflow() -> dict:
    """Parse ``.github/workflows/ci.yml`` once for the whole module."""
    assert WORKFLOW_PATH.is_file(), f"Missing CI workflow at {WORKFLOW_PATH}"
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _pytest_steps(job: dict) -> list[dict]:
    """Return the steps of *job* whose ``run`` invokes pytest."""
    return [
        step for step in job.get("steps", []) if "pytest" in (step.get("run") or "")
    ]


def _shard_matrix(job: dict) -> list:
    """Return the ``shard`` matrix values declared by *job*."""
    matrix = job.get("strategy", {}).get("matrix", {})
    shards = matrix.get("shard")
    assert shards, "Expected a `shard` matrix on this job"
    return shards


def _shard_divisors(job: dict) -> set[int]:
    """Return every ``N`` used in a ``--ci-*shard <something>/N`` in *job*."""
    divisors = set()
    for step in _pytest_steps(job):
        for token in step["run"].split():
            # The workflow passes the shard as "$CI_SHARD/6".
            if token.strip('"').startswith("$CI_SHARD/"):
                divisors.add(int(token.strip('"').split("/", 1)[1]))
    return divisors


def _shard_options(job: dict) -> set[str]:
    """Return the ``--ci-shard`` / ``--ci-block-shard`` flags *job* passes."""
    return {
        token
        for step in _pytest_steps(job)
        for token in step["run"].split()
        if token.split("=", 1)[0] in {_ROUND_ROBIN_OPTION, _BLOCK_OPTION}
    }


def _gated_files(workflow: dict) -> set[str]:
    """Return the ``tests/...py`` paths the Linux gate runs."""
    job = workflow["jobs"][_GATE_JOB]
    steps = _pytest_steps(job)
    assert steps, f"The `{_GATE_JOB}` job runs no pytest step"
    gated = {
        token
        for step in steps
        for token in step["run"].split()
        if token.endswith(".py") and token.startswith("tests/")
    }
    for step in steps:
        assert "--ci-shard" in step["run"], (
            f"The `{_GATE_JOB}` gate must shard with --ci-shard: {step['run']!r}"
        )
    return gated


def test_every_test_file_is_classified(workflow):
    """Every file under ``tests/`` is either gated or explicitly deferred.

    This is the forcing function. The gate is an allowlist, so on its own it
    would drift exactly as before — a new test file simply would not appear in
    CI and nothing would say so. Requiring ``tests/`` to equal GATED + DEFERRED
    turns "I forgot" into a red test, and makes deferring a file a decision
    someone has to write down.
    """
    gated = _gated_files(workflow)
    discovered = {
        str(path.relative_to(REPO_ROOT)) for path in TESTS_DIR.glob("test_*.py")
    }
    deferred = {f"tests/{name}" for name in DEFERRED_FROM_GATE}

    unclassified = sorted(discovered - gated - deferred)
    assert not unclassified, (
        "These test files are neither gated in .github/workflows/ci.yml nor "
        f"listed in DEFERRED_FROM_GATE: {unclassified}. Add each one to the "
        "`backend` job's file list (preferred — it then blocks PRs), or to "
        "DEFERRED_FROM_GATE with a reason if it is not green yet. Do not leave "
        "it unclassified: that is how the suite silently fell out of CI before."
    )

    overlap = sorted(gated & deferred)
    assert not overlap, (
        f"These files are both gated and deferred: {overlap}. A file is one or "
        "the other, or the counts stop meaning anything."
    )

    stale = sorted(deferred - discovered)
    assert not stale, (
        f"DEFERRED_FROM_GATE names files that no longer exist: {stale}. Remove "
        "them so the list keeps reflecting real coverage."
    )

    missing = sorted(gated - discovered)
    assert not missing, f"The gate names test files that do not exist: {missing}"


def test_security_suites_cannot_be_quietly_deferred(workflow):
    """The enforcement suites block every PR, and cannot be parked.

    ``test_every_test_file_is_classified`` above accepts *either* answer for
    any file: gated, or deferred with a reason. For the behavioural authz and
    fail-closed suites only one answer is acceptable, because deferral is the
    exact mechanism that hid a broken authz assertion for five days — the
    suites ran only in ``backend_release_sweep``, which is
    ``continue-on-error: true`` and only triggers during release prep.

    Three ways to lose the property, all of them failures here: drop the file
    from the ``backend`` job's list in ci.yml, move it back into
    ``DEFERRED_FROM_GATE``, or delete/rename the file and leave
    ``MUST_BLOCK_ON_EVERY_PR`` pointing at nothing.
    """
    gated = _gated_files(workflow)

    absent = sorted(
        name for name in MUST_BLOCK_ON_EVERY_PR if not (TESTS_DIR / name).is_file()
    )
    assert not absent, (
        f"MUST_BLOCK_ON_EVERY_PR names files that do not exist: {absent}. A "
        "renamed suite must be renamed here too; a deleted one is a deliberate "
        "reduction in security coverage and needs the authz sign-off, not a "
        "silent list edit."
    )

    ungated = sorted(
        f"tests/{name}"
        for name in MUST_BLOCK_ON_EVERY_PR
        if f"tests/{name}" not in gated
    )
    assert not ungated, (
        f"These security suites are not on the blocking `backend` gate: "
        f"{ungated}. They prove the AuthzGate actually ENFORCES the policies it "
        "declares — declaration completeness is already guarded every PR by "
        "test_architecture_guardrails.py, this is the other half. Put them back "
        "in the `backend` job's file list in .github/workflows/ci.yml."
    )

    parked = sorted(MUST_BLOCK_ON_EVERY_PR & DEFERRED_FROM_GATE)
    assert not parked, (
        f"These security suites were moved back into DEFERRED_FROM_GATE: "
        f"{parked}. Deferring means the only run is the non-blocking, "
        "release-prep-only sweep, which is precisely how a red authz test "
        "survived five days undetected. Fix the suite or delete it; parking it "
        "is not an option."
    )


def test_deferred_files_still_run_in_the_informational_sweep(workflow):
    """Deferred is "does not block", not "does not run".

    The whole justification for deferring a file is that the release-prep sweep
    keeps it visible. If that sweep ever stopped covering the whole suite, the
    deferred list would become a list of tests nobody runs at all.
    """
    sweep = workflow["jobs"]["backend_release_sweep"]
    steps = _pytest_steps(sweep)
    assert steps, "The informational sweep runs no pytest step"
    assert any(step["run"].split()[-1].rstrip("/") == "tests" for step in steps), (
        "The informational sweep must run the whole `tests` directory, because "
        "that is what keeps the DEFERRED_FROM_GATE files covered at all."
    )


def test_sweep_stays_informational_and_release_prep_only(workflow):
    """The sweep must keep reporting, and must keep not gating.

    Two properties that a matrix conversion is easy to drop on the floor:
    ``continue-on-error`` (a failing test there is a triage signal, not a
    merge-blocker) and the release-prep-only ``if:``. Without the first, an
    informational job silently becomes a gate; without the second, ~6 extra
    runners burn on every PR.
    """
    sweep = workflow["jobs"]["backend_release_sweep"]

    condition = sweep.get("if", "")
    for expected in ("rc-prep", "release", "refs/tags/v", "workflow_dispatch"):
        assert expected in condition, (
            f"The sweep's release-prep trigger lost {expected!r}: {condition!r}"
        )

    steps = _pytest_steps(sweep)
    assert steps, "The informational sweep runs no pytest step"
    for step in steps:
        assert step.get("continue-on-error") is True, (
            "Every pytest step in backend_release_sweep must keep "
            "`continue-on-error: true`; it is what makes a red there a triage "
            f"signal instead of a gate failure. Offending step: {step!r}"
        )

    assert sweep.get("strategy", {}).get("matrix", {}).get("shard"), (
        "The sweep is expected to be sharded; a single-process sweep is a "
        "~40-50 min serial job on the release-prep critical path."
    )
    assert sweep["strategy"].get("fail-fast") is False, (
        "fail-fast would cancel the other blocks on the first red, which is "
        "the opposite of what an informational triage job is for."
    )


def test_each_job_uses_the_sharding_mode_it_needs(workflow):
    """The gate deals round-robin; the sweep runs contiguous blocks.

    This is the load-bearing assertion of the whole sweep design. The sweep
    exists to catch an order- or shard-dependence that the gate's round-robin
    dealing could introduce or mask. Re-using ``--ci-shard`` there — the
    obvious "simplification" — would shard the detector with the algorithm it
    is auditing and quietly reduce the sweep to a slower duplicate of the gate.
    """
    for job_name, expected_option in _SHARD_MODE_BY_JOB.items():
        options = _shard_options(workflow["jobs"][job_name])
        assert options == {expected_option}, (
            f"`{job_name}` must shard with {expected_option} only, got "
            f"{sorted(options) or '[]'}. Round-robin (--ci-shard) balances wall "
            "clock and belongs on the blocking gates; contiguous blocks "
            "(--ci-block-shard) preserve collection order and are the only "
            "mode that keeps backend_release_sweep an ordering control."
        )


@pytest.mark.parametrize("job_name", sorted(_SHARD_MODE_BY_JOB))
def test_shard_counts_match_the_matrix(workflow, job_name):
    """``i/N`` must agree with the matrix, for every sharded job.

    A matrix grown from 6 to 8 entries while the command still says ``/6``
    would run shards 7 and 8 as duplicates of nothing and never run two slices
    of the suite at all — a silent coverage hole with a green tick on it.
    """
    job = workflow["jobs"][job_name]
    shards = _shard_matrix(job)
    divisors = _shard_divisors(job)

    assert divisors == {len(shards)}, (
        f"`{job_name}` declares {len(shards)} matrix shards but its pytest "
        f"command(s) divide by {divisors or '{}'}."
    )
    assert sorted(shards) == list(range(1, len(shards) + 1)), (
        f"`{job_name}` shard matrix must be 1..N with no gaps, got {shards}."
    )


def test_windows_subset_files_all_exist(workflow):
    """Every file the Windows subset names must exist.

    Windows keeps an explicit list on purpose, so the failure mode there is a
    stale path silently narrowing the subset rather than an ungated file.
    """
    job = workflow["jobs"]["backend_windows"]
    named = [
        token
        for step in _pytest_steps(job)
        for token in step["run"].split()
        if token.endswith(".py") and token.startswith("tests/")
    ]
    assert named, "The Windows job should still name its OS-sensitive subset"
    missing = [path for path in named if not (REPO_ROOT / path).is_file()]
    assert not missing, f"Windows CI names test files that do not exist: {missing}"


def test_shards_partition_the_collected_suite():
    """The union of all shards is the whole collection, and they are disjoint.

    Collected for real (via ``--collect-only`` on a couple of cheap modules)
    rather than by re-deriving the round-robin here, so this fails if the
    conftest hook regresses — which is the thing that would actually drop
    tests.
    """
    targets = [
        str(TESTS_DIR / "test_scope_table.py"),
        str(TESTS_DIR / "test_ci_shards.py"),
    ]

    def collect(*extra: str) -> list[str]:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", *targets, *extra],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"collection failed for {extra}:\n{result.stdout}\n{result.stderr}"
        )
        return [line for line in result.stdout.splitlines() if "::" in line]

    baseline = collect()
    assert baseline, "Expected the probe modules to collect at least one test"

    total = 4
    union: list[str] = []
    for index in range(1, total + 1):
        union.extend(collect(f"--ci-shard={index}/{total}"))

    assert len(union) == len(set(union)), "A test was collected by two shards"
    assert set(union) == set(baseline), (
        "Shards do not cover the collection exactly; "
        f"missing={set(baseline) - set(union)}, extra={set(union) - set(baseline)}"
    )


def test_block_shards_are_a_complete_disjoint_order_preserving_partition():
    """Pure-arithmetic proof of the contiguous-block split.

    ``test_block_shards_partition_the_collected_suite`` below exercises the
    real hook but only at one size; this sweeps sizes so the four properties
    the sweep depends on are checked at the boundaries too (fewer items than
    shards, exact multiples, and everything in between):

    * complete — every position lands in some block;
    * disjoint — no position lands in two;
    * order-preserving — each block is a contiguous ascending slice, which is
      the property that makes the sweep an ordering control at all;
    * balanced — block sizes differ by at most one.
    """
    for count in range(0, 40):
        for total in range(1, 9):
            bounds = [
                _block_shard_bounds(count, index, total) for index in range(total)
            ]

            for start, stop in bounds:
                assert 0 <= start <= stop <= count, (
                    f"block out of range for count={count} total={total}: {bounds}"
                )

            # Contiguous and in order: block k starts exactly where k-1 ended.
            assert bounds[0][0] == 0 and bounds[-1][1] == count, (
                f"blocks do not span 0..{count} for total={total}: {bounds}"
            )
            for (_, previous_stop), (start, _) in zip(bounds, bounds[1:]):
                assert start == previous_stop, (
                    f"blocks are not contiguous for count={count} "
                    f"total={total}: {bounds}"
                )

            covered = [
                position for start, stop in bounds for position in range(start, stop)
            ]
            assert covered == list(range(count)), (
                f"blocks are not a complete, disjoint, ordered partition for "
                f"count={count} total={total}: {bounds}"
            )

            sizes = [stop - start for start, stop in bounds]
            assert max(sizes) - min(sizes) <= 1, (
                f"block sizes differ by more than one for count={count} "
                f"total={total}: {sizes}"
            )


def test_block_shards_partition_the_collected_suite():
    """Contiguous blocks cover the collection exactly, in the original order.

    Run through pytest's real collection rather than re-deriving the slicing,
    so this fails if the conftest hook regresses. The extra assertion over the
    round-robin case is the one the sweep is built on: each shard's tests must
    appear in the same relative order as in the unsharded collection, and must
    be an unbroken run of it.
    """
    targets = [
        str(TESTS_DIR / "test_scope_table.py"),
        str(TESTS_DIR / "test_ci_shards.py"),
    ]

    def collect(*extra: str) -> list[str]:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", *targets, *extra],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, (
            f"collection failed for {extra}:\n{result.stdout}\n{result.stderr}"
        )
        return [line for line in result.stdout.splitlines() if "::" in line]

    baseline = collect()
    assert baseline, "Expected the probe modules to collect at least one test"

    total = 4
    union: list[str] = []
    for index in range(1, total + 1):
        shard = collect(f"--ci-block-shard={index}/{total}")
        start, stop = _block_shard_bounds(len(baseline), index - 1, total)
        assert shard == baseline[start:stop], (
            f"block {index}/{total} is not the contiguous slice "
            f"[{start}:{stop}] of the canonical collection order"
        )
        union.extend(shard)

    assert union == baseline, (
        "Concatenating the blocks in order must reproduce the canonical "
        "collection exactly; that identity is what makes this a weaker but "
        "real substitute for the old single-process sweep."
    )


def test_shard_modes_are_mutually_exclusive():
    """Passing both modes must fail, not silently pick one.

    Whichever one won, the run would be quietly testing something other than
    what the workflow asked for — and for the sweep that means the ordering
    control is gone with a green tick on it.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            str(TESTS_DIR / "test_scope_table.py"),
            "--ci-shard=1/2",
            "--ci-block-shard=1/2",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, "Both shard modes at once was accepted"
    assert "mutually exclusive" in (result.stdout + result.stderr)


@pytest.mark.parametrize("option", ["--ci-shard", "--ci-block-shard"])
@pytest.mark.parametrize("spec", ["7/6", "0/6", "abc", "1/0"])
def test_invalid_shard_spec_is_rejected(option, spec):
    """A malformed or out-of-range shard spec must fail loudly, not silently.

    An unnoticed ``--ci-shard 7/6`` that quietly selected nothing would be a
    green run that tested nothing.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            str(TESTS_DIR / "test_scope_table.py"),
            f"{option}={spec}",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, f"{option}={spec} was accepted"
    assert option in (result.stdout + result.stderr), (
        f"{option}={spec} failed without naming the option"
    )
