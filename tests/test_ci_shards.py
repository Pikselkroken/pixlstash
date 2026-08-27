"""Guardrails for the sharded CI backend gate.

What these protect is a completeness property, not a performance one. CI named
individual test files in ``.github/workflows/ci.yml``, and the result was that
59 of the 99 files in ``tests/`` - including authz-gate, host-capability and
fail-closed suites - ran only in a non-blocking release-prep sweep. Nothing
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
  partition - every collected test in exactly one shard - by actually running
  pytest's collection, not by re-implementing the arithmetic.

``--ci-shard`` no longer deals by position: it places tests
longest-processing-time-first from the committed
``tests/ci_test_durations.json`` so the gate's shards finish together rather
than merely holding the same number of tests. That turns a data file into an
input to the partition, and eight independent processes have to derive the
identical partition from it, so the guardrails below cover the degraded inputs
as well as the happy one: an absent, truncated, wrongly-shaped or
negative-valued map, and tests the map has never heard of. All of them must
still yield a complete, disjoint partition - a slower gate is an acceptable
outcome, a dropped test is not.

The second property these protect is the release-prep sweep's *ordering*
control. The blocking gate deals tests round-robin (``--ci-shard``); the sweep
runs the same suite in contiguous blocks of collection order
(``--ci-block-shard``) precisely so it can still catch an order- or
shard-dependence that round-robin dealing would mask. Sharding the sweep with
the gate's own algorithm would audit that algorithm with itself, so
``test_each_job_uses_the_sharding_mode_it_needs`` pins the mode per job.
"""

import json
import os
import re
import subprocess
import sys
import warnings
from pathlib import Path

import pytest
import yaml
from tests import conftest as shard_conftest
from tests.conftest import (
    _block_shard_bounds,
    _load_recorded_durations,
    _time_balanced_shard_assignment,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
TESTS_DIR = REPO_ROOT / "tests"
DURATIONS_PATH = TESTS_DIR / "ci_test_durations.json"

# These workflows can publish packages/images/pages, create release issues or
# PRs, attach signed artifacts, mint OIDC tokens, or consume release-signing
# secrets. Their action and shell boundaries therefore need stronger invariants
# than an ordinary read-only CI job.
PRIVILEGED_WORKFLOW_PATHS = tuple(
    REPO_ROOT / ".github" / "workflows" / name
    for name in (
        "certum-signer-image.yml",
        "docker-publish.yml",
        "electron.yml",
        "pages.yml",
        "publish-pypi.yml",
        "record-test-durations.yml",
        "release-test-issues.yml",
        "release-version.yml",
        "windows-installer.yml",
        "windows-signing-test.yml",
    )
)

_ACTION_USE_RE = re.compile(
    r"^\s*(?:-\s*)?uses:\s*([^#\s]+)\s*(?:#\s*(.+?))?\s*$", re.MULTILINE
)
_FULL_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_VERSION_COMMENT_RE = re.compile(r"^v[0-9]+(?:\.[0-9]+){1,3}(?:[-+._][0-9A-Za-z.-]+)?$")
_RELEASE_SECRET_RE = re.compile(
    r"secrets\.(?:APPLE_|CERTUM_|DOCKERHUB_|RELEASE_BOT_TOKEN)"
)

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
# `backend_release_sweep` - `continue-on-error: true`, and triggered only during
# release prep. A test in test_authz_gate_step4.py that asserted on a route
# which does not exist was therefore red for five days with nothing to report
# it. The gap was not "the test was wrong"; it was "nothing blocking ever ran
# the enforcement tests".
#
# Re-deferring one of these is exactly the move that hid that bug, so it fails
# `test_security_suites_cannot_be_quietly_deferred` below. If a suite here goes
# red, fix it or delete it - parking it is not available. Adding a file here is
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
        # Streaming variant of the picture list - a separate code path from the
        # paged list, and historically its own BOLA vector.
        "test_pictures_stream.py",
        # The token-level half of share-link scoping: the only suite that mints a
        # real READ token and asserts on what comes back through the HTTP routes.
        # `/pictures`, `/pictures/stream` and `/pictures/count` are SCOPED_LIST
        # with `scope_aware=True`, so the AuthzGate does not object-check their
        # rows and the handler's narrowing is the sole enforcement, so nothing
        # else in the suite can catch a route that stops narrowing. It was
        # already on the ci.yml gate; pinning it here makes deferral unavailable.
        "test_read_token_security.py",
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

# Release-critical suites changed across the RC branch. These are intentionally
# stronger than ordinary classification: moving one back to DEFERRED would
# leave face-search, membership authz, undo/reviews, stacks, worker routes or
# WebSocket event contracts visible only in the informational sweep.
RELEASE_CRITICAL_MUST_BLOCK = frozenset(
    {
        "test_characters_api.py",
        "test_likeness_and_face_search.py",
        "test_project_membership_service.py",
        "test_projects_api.py",
        "test_reviews_api.py",
        "test_stacks_api.py",
        "test_stacks_membership.py",
        "test_workers_api.py",
        "test_ws_broadcaster.py",
    }
)

# Files that are deliberately NOT in the blocking gate yet. Every one of these
# still runs in the informational `backend_release_sweep`, so the coverage is
# visible; it just does not block a PR.
#
# This list is not documentation - it is half of a partition. `tests/` must be
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
        "test_near_neighbor.py",
        "test_person_tags.py",
        "test_predicate_filter.py",
        "test_quality_task_shutdown.py",
        "test_reference_folder_listing_count_parity.py",
        "test_reference_folder_sidecars.py",
        "test_rocm_device_check.py",
        "test_server_external_listener.py",
        "test_server_simple.py",
        "test_smart_score_invalidation.py",
        "test_snapshot_compression.py",
        "test_stack_position_invariant.py",
        "test_startup_banner_encoding.py",
        "test_stats_api.py",
        "test_tag_health_api.py",
        "test_tag_prediction_backfill.py",
        "test_tag_predictions_api.py",
        "test_tag_suggestions_api.py",
        "test_tagger_plugin_registry.py",
        "test_tagger_runs_api.py",
        "test_user_settings_tagger_settings.py",
    }
)

# How much of the gate the committed durations map must still time before its
# balance number stops meaning anything.
#
# Not zero, deliberately. The map is an optimisation input: a gated file it has
# never timed costs a little shard skew and no coverage at all, because
# `_time_balanced_shard_assignment` seeds every test on its round-robin position
# and charges the unknown ones the median. Failing the build over that would
# make refreshing the map a correctness obligation, which it is not - and would
# do it non-locally, on whichever PR happens to run next rather than on the one
# that added the file. That is not hypothetical: #832 added
# tests/test_security_supported_versions.py and merged on a check that predated
# this guardrail, so the guardrail could not block the PR that caused the hole
# and then failed every unrelated PR afterwards.
#
# Not absent either. The defect this guardrail was written for (#833) was a
# *silent* one: the balance was modelled over the map's own keys, so 28 of 119
# gated files were structurally invisible and the ratio read a perfect 1.000
# over data missing a quarter of the gate. A ratio computed over 76% of the gate
# is not a measurement, and no warning would have been read. So: name the gaps
# every run, and fail once the map has rotted far enough that the 1.05 assertion
# below is describing something other than the gate.
MINIMUM_GATE_COVERAGE = 0.9


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


def _privileged_workflows() -> list[tuple[Path, dict, str]]:
    """Load the release-capable workflows protected by the static guards."""
    loaded = []
    for path in PRIVILEGED_WORKFLOW_PATHS:
        assert path.is_file(), f"Missing privileged workflow: {path}"
        text = path.read_text(encoding="utf-8")
        loaded.append((path, yaml.safe_load(text), text))
    return loaded


def _grants_write_permission(value) -> bool:
    """Return whether a parsed workflow node grants any write permission."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "permissions":
                if child == "write-all" or (
                    isinstance(child, dict) and "write" in child.values()
                ):
                    return True
            if _grants_write_permission(child):
                return True
    elif isinstance(value, list):
        return any(_grants_write_permission(child) for child in value)
    return False


def test_privileged_workflow_inventory_is_complete():
    """New write/OIDC/release-secret workflows must enter the guarded set."""
    expected = set(PRIVILEGED_WORKFLOW_PATHS)
    discovered = set()
    for path in (REPO_ROOT / ".github" / "workflows").glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if _grants_write_permission(data) or _RELEASE_SECRET_RE.search(text):
            discovered.add(path)
    assert discovered == expected, (
        "Keep PRIVILEGED_WORKFLOW_PATHS aligned with workflows that grant write "
        "permissions or consume release credentials; "
        f"missing={sorted(discovered - expected)}, "
        f"stale={sorted(expected - discovered)}"
    )


def test_privileged_workflow_actions_are_immutable():
    """Third-party code in release-capable jobs must use reviewed commits."""
    for path, _workflow, text in _privileged_workflows():
        matches = list(_ACTION_USE_RE.finditer(text))
        assert matches, f"Expected at least one action in {path}"
        for match in matches:
            action, version_comment = match.groups()
            if action.startswith("./"):
                continue
            action_name, separator, ref = action.rpartition("@")
            assert separator and action_name, (
                f"Malformed action reference in {path}: {action}"
            )
            assert _FULL_COMMIT_SHA_RE.fullmatch(ref), (
                f"Privileged workflow action must use an exact 40-character "
                f"commit SHA, not a mutable tag: {path}: {action}"
            )
            assert version_comment and _VERSION_COMMENT_RE.fullmatch(version_comment), (
                f"Pinned action must retain a machine-readable version comment "
                f"for updates: {path}: {action}"
            )


def test_privileged_workflow_run_blocks_do_not_interpolate_expressions():
    """Event/ref/input expressions cross into shells only through quoted env."""
    for path, workflow_data, _text in _privileged_workflows():
        for job_name, job in workflow_data.get("jobs", {}).items():
            for step in job.get("steps", []):
                run = step.get("run")
                assert not run or "${{" not in run, (
                    f"Move GitHub expressions from `run` into a step `env` value "
                    f"and quote the environment variable in the shell: "
                    f"{path}:{job_name}:{step.get('name', '<unnamed>')}"
                )


def test_privileged_workflow_checkouts_do_not_persist_credentials():
    """Release jobs must not leave checkout credentials available to later code."""
    for path, workflow_data, _text in _privileged_workflows():
        for job_name, job in workflow_data.get("jobs", {}).items():
            for step in job.get("steps", []):
                uses = step.get("uses", "")
                if not uses.startswith("actions/checkout@"):
                    continue
                assert step.get("with", {}).get("persist-credentials") is False, (
                    f"Set persist-credentials: false on checkout in {path}:{job_name}"
                )


def test_electron_apple_signing_requires_validated_release_tag():
    """Branch dispatch cannot receive Apple credentials or sign a macOS build."""
    path = REPO_ROOT / ".github" / "workflows" / "electron.yml"
    workflow_data = yaml.safe_load(path.read_text(encoding="utf-8"))
    wheel_job = workflow_data["jobs"]["build-wheel"]
    electron_job = workflow_data["jobs"]["build-electron"]

    assert electron_job["needs"] == "build-wheel"
    assert "environment" not in electron_job, (
        "Branch/dispatch matrix builds must not enter a signing environment"
    )
    assert wheel_job["outputs"]["validated_release_tag"] == (
        "${{ steps.release-ref.outputs.validated_release_tag }}"
    )

    steps_by_name = {
        step.get("name"): step for step in wheel_job["steps"] + electron_job["steps"]
    }
    validation = steps_by_name["Classify and validate release ref"]
    assert "if" not in validation, "Ref classification must run for branch dispatch too"
    assert validation["env"] == {
        "RELEASE_REF": "${{ github.ref }}",
        "RELEASE_TAG": "${{ github.ref_name }}",
        "EXPECTED_VERSION": "${{ steps.version.outputs.version }}",
    }
    assert "validated_release_tag=false" in validation["run"]
    assert "validated_release_tag=true" in validation["run"]
    assert '"refs/tags/$RELEASE_TAG"' in validation["run"]
    assert "^v[0-9]+" in validation["run"]
    assert '"v$EXPECTED_VERSION"' in validation["run"]

    unsigned = steps_by_name["Build unsigned Electron installers"]
    assert unsigned["if"] == (
        "matrix.os != 'mac' || "
        "needs.build-wheel.outputs.validated_release_tag != 'true'"
    )
    assert set(unsigned.get("env", {})) == {"TARGET_OS"}
    assert "CSC_IDENTITY_AUTO_DISCOVERY=false" in unsigned["run"]
    assert "-unsigned.${ext}" in unsigned["run"]

    signed = steps_by_name["Build signed macOS Electron installer"]
    assert signed["if"] == (
        "matrix.os == 'mac' && "
        "needs.build-wheel.outputs.validated_release_tag == 'true'"
    )
    assert set(signed["env"]) == {
        "CSC_LINK",
        "CSC_KEY_PASSWORD",
        "APPLE_ID",
        "APPLE_APP_SPECIFIC_PASSWORD",
        "APPLE_TEAM_ID",
    }

    secret_steps = [
        step
        for step in electron_job["steps"]
        if "secrets.APPLE_" in yaml.safe_dump(step)
    ]
    assert secret_steps == [signed], (
        "Apple credentials must exist only in the validated tag-only signing step"
    )


def test_model_cache_saves_every_path_it_restores():
    """A path only the restore step lists is warmed on every run and never kept.

    The Windows ``downloaded_models`` directory was in the restore list and
    absent from the save list, so ``warm_models_windows`` downloaded the WD14
    and PixlStash taggers and saved an entry without them. That entry then went
    on hitting its primary key, so the save step (`hit != 'true'`) never ran
    again and could never repair it - every Windows shard re-fetched the
    taggers, which is the HuggingFace stampede the warm job exists to prevent.
    """
    action = yaml.safe_load(
        (REPO_ROOT / ".github" / "actions" / "model-cache" / "action.yml").read_text(
            encoding="utf-8"
        )
    )
    steps = {}
    for step in action["runs"]["steps"]:
        uses = step.get("uses", "")
        for mode in ("restore", "save"):
            if f"/cache/{mode}@" in uses:
                assert mode not in steps, f"Two {mode} steps in one path list"
                steps[mode] = step

    assert steps.keys() == {"restore", "save"}
    # Both are guarded on `mode`, or one of them runs in the wrong direction.
    assert steps["restore"]["if"] == "inputs.mode != 'save'"
    assert steps["save"]["if"] == "inputs.mode == 'save'"

    # One path per line, not `.split()`: a Windows path can contain a space.
    paths = {
        mode: {
            line.strip() for line in step["with"]["path"].splitlines() if line.strip()
        }
        for mode, step in steps.items()
    }
    assert paths["restore"] == paths["save"], (
        "Restore and save must cover the same model paths on every platform; "
        f"restore-only: {sorted(paths['restore'] - paths['save'])}, "
        f"save-only: {sorted(paths['save'] - paths['restore'])}"
    )
    assert "~/AppData/Local/pixlstash/pixlstash/downloaded_models" in paths["save"]


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


def _warn_or_fail_on_map_coverage(gated: set[str], recorded_files: set[str]) -> None:
    """Name every gated file the map has not timed; fail only once too few remain.

    Split out of the balance test so both branches can be exercised directly.
    The failing branch is otherwise unreachable from a green tree - the map is
    normally near-complete - and an unexercised fail branch is a guardrail on
    paper, which is the specific way this repo has been bitten before.

    Args:
        gated: Every ``tests/...py`` path the Linux gate runs.
        recorded_files: The files the durations map has at least one timing for.

    Raises:
        AssertionError: Coverage has fallen below :data:`MINIMUM_GATE_COVERAGE`.
    """
    unrecorded = sorted(gated - recorded_files)
    if unrecorded:
        # No ``stacklevel``: the interesting frame is this module, and pointing
        # one level up lands in ``_pytest/python.py``, which helps nobody.
        warnings.warn(
            f"{len(unrecorded)} of {len(gated)} gated test files have no "
            f"recorded durations: {unrecorded}. Every test in them is placed by "
            "round-robin fallback at the median cost, so this balance assertion "
            "says nothing about them. That costs shard skew, never coverage - "
            "refresh the map by dispatching "
            ".github/workflows/record-test-durations.yml when it is convenient."
        )

    coverage = (len(gated) - len(unrecorded)) / len(gated)
    assert coverage >= MINIMUM_GATE_COVERAGE, (
        f"The durations map now times only {coverage:.0%} of the {len(gated)} "
        f"gated files, below the {MINIMUM_GATE_COVERAGE:.0%} floor. Below that "
        "the balance asserted below is a measurement of a shrinking subset "
        "rather than of the gate - which is exactly how it reported a perfect "
        "1.000 over 76% of the gate. Refresh the map by dispatching "
        f".github/workflows/record-test-durations.yml. Missing: {unrecorded}"
    )


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
    would drift exactly as before - a new test file simply would not appear in
    CI and nothing would say so. Requiring ``tests/`` to equal GATED + DEFERRED
    turns "I forgot" into a red test, and makes deferring a file a decision
    someone has to write down.

    Discovery recurses. Suites that share one heavy environment live in a
    sub-package with their own ``conftest.py`` (``tests/multi_project_authz/``),
    and a non-recursive glob would let every file in one drop out of CI without
    anything going red - the exact drift this test exists to stop.
    """
    gated = _gated_files(workflow)
    discovered = {
        str(path.relative_to(REPO_ROOT)) for path in TESTS_DIR.rglob("test_*.py")
    }
    deferred = {f"tests/{name}" for name in DEFERRED_FROM_GATE}

    unclassified = sorted(discovered - gated - deferred)
    assert not unclassified, (
        "These test files are neither gated in .github/workflows/ci.yml nor "
        f"listed in DEFERRED_FROM_GATE: {unclassified}. Add each one to the "
        "`backend` job's file list (preferred - it then blocks PRs), or to "
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
    exact mechanism that hid a broken authz assertion for five days - the
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
        "declares - declaration completeness is already guarded every PR by "
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


def test_release_critical_suites_cannot_remain_informational(workflow):
    """Changed RC contracts must block rather than live only in the sweep."""
    gated = _gated_files(workflow)
    missing_files = sorted(
        name for name in RELEASE_CRITICAL_MUST_BLOCK if not (TESTS_DIR / name).is_file()
    )
    assert not missing_files, (
        f"RELEASE_CRITICAL_MUST_BLOCK names missing suites: {missing_files}"
    )

    ungated = sorted(
        f"tests/{name}"
        for name in RELEASE_CRITICAL_MUST_BLOCK
        if f"tests/{name}" not in gated
    )
    assert not ungated, (
        f"These release-critical suites do not block the stable backend gate: {ungated}"
    )

    parked = sorted(RELEASE_CRITICAL_MUST_BLOCK & DEFERRED_FROM_GATE)
    assert not parked, (
        "These release-critical suites were parked in the informational sweep: "
        f"{parked}"
    )


def test_e2e_fixture_check_fails_closed(workflow):
    """The e2e job cannot go green by skipping Playwright.

    The `build` aggregate that used to carry the other half of this test (that
    the stable check required e2e at all) was removed in #796: there is no
    branch protection consuming it, so there is no stable check to require.
    What still matters is that this job reports red rather than green when the
    committed fixture is missing.
    """
    e2e = workflow["jobs"]["e2e"]
    fixture_steps = [
        step for step in e2e.get("steps", []) if step.get("id") == "fixture"
    ]
    assert len(fixture_steps) == 1, "Expected one authoritative fixture check"
    fixture_script = fixture_steps[0].get("run", "")
    assert "git ls-files --error-unmatch test-data/images/vault.db" in fixture_script
    assert "test -f test-data/images/vault.db" in fixture_script
    assert "present=false" not in fixture_script
    assert "skipping Playwright" not in fixture_script


def test_cheap_electron_tests_are_in_the_stable_checks(workflow):
    """Desktop shell logic runs without invoking packaging."""
    steps = workflow["jobs"]["checks"].get("steps", [])
    electron = [step for step in steps if step.get("working-directory") == "electron"]
    assert electron, "The stable checks job must run Electron unit tests"
    commands = "\n".join(step.get("run", "") for step in electron)
    assert "npm ci" in commands
    assert "npm test" in commands
    assert "electron-builder" not in commands
    assert "npm run dist" not in commands


def test_telemetry_worker_config_and_d1_contract_are_validated(workflow):
    """CI checks both Worker behavior and Wrangler's deploy-time config."""
    steps = workflow["jobs"]["checks"].get("steps", [])
    telemetry = [
        step
        for step in steps
        if step.get("working-directory") == "website/telemetry-worker"
    ]
    commands = "\n".join(step.get("run", "") for step in telemetry)
    for required in ("npm ci", "npm test", "npm run check:config"):
        assert required in commands, f"Telemetry CI is missing {required!r}"

    worker_root = REPO_ROOT / "website/telemetry-worker"
    package = json.loads((worker_root / "package.json").read_text(encoding="utf-8"))
    config_text = (REPO_ROOT / "website/telemetry-worker/wrangler.jsonc").read_text(
        encoding="utf-8"
    )
    config = json.loads(
        "\n".join(line.split("//", 1)[0] for line in config_text.splitlines())
    )
    scripts = package.get("scripts", {})
    assert scripts.get("test") == "npm run test:unit && npm run test:d1"
    assert scripts.get("test:d1") == "node --test test/d1-integration.test.js"
    assert (worker_root / "test/d1-integration.test.js").is_file()
    assert scripts.get("check:config", "").startswith(
        "WRANGLER_LOG_PATH=.wrangler/logs wrangler deploy --dry-run"
    )
    assert scripts.get("deploy") == (
        "wrangler deploy --no-x-provision --no-x-auto-create"
    )
    d1_bindings = config.get("d1_databases", [])
    assert any(binding.get("binding") == "DB" for binding in d1_bindings)
    assert all("database_id" not in binding for binding in d1_bindings)
    assert config.get("triggers", {}).get("crons") == ["*/5 * * * *"]
    assert config.get("limits", {}).get("cpu_ms") == 30000
    assert config.get("observability", {}).get("enabled") is True


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
    dealing could introduce or mask. Re-using ``--ci-shard`` there - the
    obvious "simplification" - would shard the detector with the algorithm it
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
    of the suite at all - a silent coverage hole with a green tick on it.
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
    conftest hook regresses - which is the thing that would actually drop
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

    * complete - every position lands in some block;
    * disjoint - no position lands in two;
    * order-preserving - each block is a contiguous ascending slice, which is
      the property that makes the sweep an ordering control at all;
    * balanced - block sizes differ by at most one.
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
    what the workflow asked for - and for the sweep that means the ordering
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


class _StubItem:
    """The only part of a collected item the sharding hook reads."""

    def __init__(self, nodeid: str):
        self.nodeid = nodeid


class _StubHook:
    """Captures the ``pytest_deselected`` call the hook makes."""

    def __init__(self):
        self.deselected: list[_StubItem] = []

    def pytest_deselected(self, items):
        self.deselected.extend(items)


class _StubConfig:
    """A minimal ``config`` exposing just the two shard options."""

    def __init__(self, options: dict):
        self._options = options
        self.hook = _StubHook()

    def getoption(self, name):
        return self._options[name]


def _shard_via_hook(nodeids: list[str], option: str, spec: str) -> list[str]:
    """Run the real conftest hook over *nodeids* and return what it kept."""
    items = [_StubItem(nodeid) for nodeid in nodeids]
    config = _StubConfig(
        {
            _ROUND_ROBIN_OPTION: spec if option == _ROUND_ROBIN_OPTION else None,
            _BLOCK_OPTION: spec if option == _BLOCK_OPTION else None,
        }
    )
    shard_conftest.pytest_collection_modifyitems(config, items)
    return [item.nodeid for item in items]


def _synthetic_nodeids(count: int) -> list[str]:
    return [f"tests/test_synthetic.py::test_{index:04d}" for index in range(count)]


def _shard_loads(
    nodeids: list[str], assignment: list[int], total: int, durations: dict
) -> list[float]:
    loads = [0.0] * total
    for nodeid, shard in zip(nodeids, assignment):
        loads[shard] += durations.get(nodeid, 0.0)
    return loads


def _round_robin_loads(nodeids: list[str], total: int, durations: dict) -> list[float]:
    return _shard_loads(
        nodeids,
        [position % total for position in range(len(nodeids))],
        total,
        durations,
    )


@pytest.mark.parametrize("count", [0, 1, 7, 8, 9, 63, 200])
@pytest.mark.parametrize("total", [1, 2, 3, 8])
@pytest.mark.parametrize("coverage", ["none", "partial", "full"])
def test_time_balanced_assignment_is_a_total_partition(count, total, coverage):
    """Every collected test lands in exactly one shard, for every input.

    This is the property the whole mechanism is subordinate to. Balancing by
    recorded time introduces a *data file* into the partition decision, so the
    ways to break it multiply: a test the data has never seen, a data set that
    covers nothing, an awkward count/shard ratio. None of them may drop or
    duplicate a test, and the sweep here is arithmetic rather than a judgement
    call about which combinations are interesting.
    """
    nodeids = _synthetic_nodeids(count)
    if coverage == "none":
        durations = {}
    elif coverage == "partial":
        # Every third test known, with a wide spread so LPT actually moves them.
        durations = {
            nodeid: float((index % 17) ** 2)
            for index, nodeid in enumerate(nodeids)
            if index % 3 == 0
        }
    else:
        durations = {
            nodeid: float((index % 17) ** 2) for index, nodeid in enumerate(nodeids)
        }

    assignment = _time_balanced_shard_assignment(nodeids, total, durations)

    assert len(assignment) == count, "One shard decision per collected test"
    assert all(0 <= shard < total for shard in assignment), (
        f"Assignment left the 0..{total - 1} range: {sorted(set(assignment))}"
    )

    covered = sorted(
        position
        for shard in range(total)
        for position, chosen in enumerate(assignment)
        if chosen == shard
    )
    assert covered == list(range(count)), (
        "Shards are not a complete, disjoint partition of the collection for "
        f"count={count} total={total} coverage={coverage}"
    )


def test_no_recorded_durations_reproduces_the_round_robin_deal():
    """An empty map must be byte-for-byte the behaviour it replaced.

    "Degrades to today's behaviour" has to be exact, not approximate: it is the
    fallback every other failure path funnels into, so if it were subtly
    different the safety argument for all of them would be untested.
    """
    nodeids = _synthetic_nodeids(50)
    for total in (2, 3, 8):
        assert _time_balanced_shard_assignment(nodeids, total, {}) == [
            position % total for position in range(50)
        ]


def test_unknown_tests_keep_their_round_robin_position():
    """A test the durations map has never seen is still placed, positionally.

    New and renamed tests are the normal state of the map between refreshes.
    They must not need the map to exist, and they must not be quietly excluded
    from the deal, which is the failure that would look like a passing gate.
    """
    nodeids = _synthetic_nodeids(40)
    known = {nodeid: 10.0 for index, nodeid in enumerate(nodeids) if index % 2 == 0}
    total = 4

    assignment = _time_balanced_shard_assignment(nodeids, total, known)

    unknown_positions = [index for index in range(40) if index % 2 == 1]
    assert [assignment[index] for index in unknown_positions] == [
        index % total for index in unknown_positions
    ], "Unknown tests must keep the positional fallback"


def test_time_balanced_assignment_is_deterministic():
    """The same collection and data give the identical partition, every time.

    The shards decide independently, one process per runner, and never
    compare notes. If the decision depended on dict/set
    iteration order, a hash seed, or the order the data happened to be written
    in, two shards could disagree and a test would be run twice or not at all -
    with a green tick on it either way.
    """
    nodeids = _synthetic_nodeids(300)
    durations = {
        nodeid: float((index * 7919) % 401) / 3.0
        for index, nodeid in enumerate(nodeids)
        if index % 4 != 0
    }
    reversed_durations = dict(reversed(list(durations.items())))

    baseline = _time_balanced_shard_assignment(nodeids, 8, durations)
    for _ in range(5):
        assert _time_balanced_shard_assignment(nodeids, 8, durations) == baseline
    assert (
        _time_balanced_shard_assignment(nodeids, 8, reversed_durations) == baseline
    ), "Assignment changed when the durations map was written in another order"


def test_ties_do_not_depend_on_collection_order_of_equal_tests():
    """Equal durations break on nodeid, so the deal is stable and reproducible."""
    nodeids = _synthetic_nodeids(64)
    durations = dict.fromkeys(nodeids, 5.0)
    assignment = _time_balanced_shard_assignment(nodeids, 8, durations)

    counts = [assignment.count(shard) for shard in range(8)]
    assert counts == [8] * 8, f"Equal-cost tests should deal evenly, got {counts}"
    assert _time_balanced_shard_assignment(nodeids, 8, durations) == assignment


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("truncated.json", '{"durations": {"tests/a.py::t": 1.0'),
        ("not_json.json", "this is a CI log, not the durations map"),
        ("wrong_shape.json", '["tests/a.py::t", 1.0]'),
        ("no_durations_key.json", '{"version": 1}'),
        ("durations_not_object.json", '{"durations": []}'),
        ("empty.json", ""),
    ],
)
def test_unusable_duration_maps_degrade_to_round_robin(tmp_path, name, content):
    """A corrupt or missing map yields ``{}`` and a warning, never an error.

    A build must not fail because an optimisation input rotted, and it must not
    silently keep using half of a truncated file either. The loud-but-harmless
    middle is the only acceptable behaviour: warn, and fall back to the deal
    that needs no data at all.
    """
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")

    with pytest.warns(UserWarning):
        assert _load_recorded_durations(path) == {}

    with pytest.warns(UserWarning):
        assert _load_recorded_durations(tmp_path / "absent.json") == {}


def test_nonsense_duration_values_are_dropped_not_trusted(tmp_path):
    """Negative, non-finite and non-numeric durations are rejected per entry.

    A single ``-1e9`` would otherwise make one shard look infinitely cheap and
    collect the entire suite, which is a balanced-looking green run that tested
    everything on one runner.
    """
    path = tmp_path / "durations.json"
    path.write_text(
        json.dumps(
            {
                "durations": {
                    "tests/a.py::good": 1.5,
                    "tests/a.py::negative": -3.0,
                    "tests/a.py::infinite": float("inf"),
                    "tests/a.py::text": "12.0",
                    "tests/a.py::boolean": True,
                    "tests/a.py::integer": 4,
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.warns(UserWarning):
        durations = _load_recorded_durations(path)

    assert durations == {"tests/a.py::good": 1.5, "tests/a.py::integer": 4.0}


def test_committed_durations_map_is_well_formed():
    """The shipped map parses, is non-trivial, and names plausible tests."""
    assert DURATIONS_PATH.is_file(), (
        f"Missing {DURATIONS_PATH}. --ci-shard still partitions without it, but "
        "the gate falls back to count-balanced shards; regenerate it with "
        "scripts/record_test_durations.py."
    )
    durations = _load_recorded_durations(DURATIONS_PATH)
    assert len(durations) > 500, (
        f"Only {len(durations)} durations recorded; the gate collects far more "
        "than that, so this map was probably built from a truncated log."
    )
    malformed = [nodeid for nodeid in durations if "::" not in nodeid]
    assert not malformed, f"Durations keys must be pytest nodeids: {malformed[:5]}"
    outside = [nodeid for nodeid in durations if not nodeid.startswith("tests/")]
    assert not outside, f"Durations keys must be rooted at tests/: {outside[:5]}"


def test_recorded_durations_actually_balance_the_gate(workflow):
    """The whole point, asserted: LPT flattens the shards, round-robin does not.

    Modelled over the committed map at whatever shard count the gate currently
    declares, read from the workflow rather than hardcoded - a resize must
    re-prove the balance, not silently keep asserting it about the old N.
    Balance matters MORE as N falls: with fewer, larger buckets a single
    misplaced slow file moves the critical path further.

    The positional baseline is computed in sorted-nodeid order, which is a
    stand-in for collection order rather than the real thing - good enough to
    show the difference in kind, and it is the *balanced* side that carries the
    assertion. If a single test ever grows past a shard's share this fails, and
    it should: no assignment can balance that, and the fix is the test.

    The node list comes from the gate's own file list, never from the map's
    keys. Grading the map on the tests it happens to contain is a tautology: it
    reported 1.000 while 28 of 119 gated files were absent, and every test in
    those files was being placed by round-robin fallback rather than by the
    balance this claims to prove. So the coverage of the gate is reported every
    run and asserted against ``MINIMUM_GATE_COVERAGE`` - a handful of freshly
    added files is a refresh chore and warns, a map that has stopped describing
    the gate fails.
    """
    durations = _load_recorded_durations(DURATIONS_PATH)
    gated = _gated_files(workflow)
    recorded_files = {nodeid.split("::", 1)[0] for nodeid in durations}

    _warn_or_fail_on_map_coverage(gated, recorded_files)

    nodeids = sorted(
        nodeid for nodeid in durations if nodeid.split("::", 1)[0] in gated
    )
    total = len(_shard_matrix(workflow["jobs"][_GATE_JOB]))

    assignment = _time_balanced_shard_assignment(nodeids, total, durations)
    balanced = _shard_loads(nodeids, assignment, total, durations)
    positional = _round_robin_loads(nodeids, total, durations)

    assert max(balanced) < max(positional), (
        f"Time-balanced shards ({max(balanced):.1f}s) are no faster than the "
        f"positional deal ({max(positional):.1f}s) on the recorded data"
    )
    assert max(balanced) / min(balanced) < 1.05, (
        "Time-balanced shards are still uneven on the recorded data: "
        f"{[round(load, 1) for load in balanced]}"
    )


def test_negligible_tests_do_not_all_land_on_one_shard():
    """Sub-millisecond tests must still be dealt, not dumped.

    A greedy "cheapest shard wins" loop never changes which shard is cheapest
    when the item it just placed cost 0.0, so every test that rounds to zero
    goes to the same runner. Measured on the real map before the per-test floor
    was added: 648 tests on shard 6 against ~153 on each of the others. Total
    recorded load was perfectly level and the deal was still a valid partition,
    so nothing else in this file noticed - a *balanced* wrong answer is the
    hardest kind to see.
    """
    nodeids = _synthetic_nodeids(800)
    durations = {
        nodeid: (30.0 if index < 8 else 0.0) for index, nodeid in enumerate(nodeids)
    }

    assignment = _time_balanced_shard_assignment(nodeids, 8, durations)
    counts = [assignment.count(shard) for shard in range(8)]

    assert max(counts) <= 2 * min(counts), (
        f"Zero-cost tests piled onto one shard instead of being dealt: {counts}"
    )


class _BudgetStubReporter:
    """The three pieces of the terminal reporter the budget check touches."""

    def __init__(self, durations: list[float]):
        self.stats = {"passed": [_BudgetStubReport(value) for value in durations]}
        self.lines: list[str] = []

    def write_sep(self, _sep, title, **_kwargs):
        self.lines.append(title)

    def write_line(self, line, **_kwargs):
        self.lines.append(line)


class _BudgetStubReport:
    def __init__(self, duration: float):
        self.duration = duration


class _BudgetStubSession:
    """A session whose exit status the budget check is allowed to change."""

    def __init__(self, reporter, shard_spec, exitstatus=0):
        self.exitstatus = exitstatus
        self.config = _BudgetStubConfig(reporter, shard_spec)


class _BudgetStubConfig:
    def __init__(self, reporter, shard_spec):
        self._shard_spec = shard_spec
        self.pluginmanager = _BudgetStubPluginManager(reporter)

    def getoption(self, name):
        assert name == "--ci-shard", f"Unexpected option read: {name}"
        return self._shard_spec


class _BudgetStubPluginManager:
    def __init__(self, reporter):
        self._reporter = reporter

    def get_plugin(self, name):
        assert name == "terminalreporter", f"Unexpected plugin read: {name}"
        return self._reporter


def _budget_annotation(reporter) -> str | None:
    """The ``::warning::`` line the budget check emits, if it emitted one."""
    for line in reporter.lines:
        if line.startswith("::warning title=Test-time budget exceeded::"):
            return line
    return None


def test_test_time_budget_fires_on_over_budget_input():
    """The ceiling trips on synthetic time, and stays quiet under it.

    Both directions on purpose: a budget that can only be observed to fire is
    no better evidenced than one that never fires, and a guard permanently red
    gets muted within a week.

    The signal asserted on is the annotation, not the exit status: a shard
    failed for being slow cannot be harvested by
    ``record-test-durations.yml``, which is the workflow that fixes it.
    """
    ceiling = shard_conftest.TEST_TIME_BUDGET_SECONDS / 4

    over = _BudgetStubReporter([ceiling * 0.6, ceiling * 0.6])
    session = _BudgetStubSession(over, "1/4")
    shard_conftest._enforce_test_time_budget(session)
    assert session.exitstatus == 0, (
        "The budget failed the shard; a slow gate cannot then be re-measured"
    )
    assert _budget_annotation(over), f"No budget annotation was emitted: {over.lines}"
    assert any("TEST-TIME BUDGET EXCEEDED" in line for line in over.lines), (
        f"No budget banner was printed: {over.lines}"
    )
    assert any("NOT A TEST FAILURE" in line for line in over.lines), (
        "The banner must be unmistakable from a failing test"
    )

    under = _BudgetStubReporter([ceiling * 0.6, ceiling * 0.3])
    session = _BudgetStubSession(under, "1/4")
    shard_conftest._enforce_test_time_budget(session)
    assert session.exitstatus == 0, "Under-budget shard was failed anyway"
    assert under.lines == [], f"Under-budget shard printed a banner: {under.lines}"

    # The ceiling is one total-suite constant divided by the shard count, so
    # the same measured time is fine at N=4 and a breach at N=16.
    resharded = _BudgetStubReporter([ceiling * 0.6, ceiling * 0.3])
    session = _BudgetStubSession(resharded, "1/16")
    shard_conftest._enforce_test_time_budget(session)
    assert _budget_annotation(resharded), "A reshard silently kept the old ceiling"

    # Not sharding at all means not measuring: a local partial run collects a
    # fraction of the suite and must never be judged against a shard's share.
    local = _BudgetStubReporter([ceiling * 99])
    session = _BudgetStubSession(local, None)
    shard_conftest._enforce_test_time_budget(session)
    assert session.exitstatus == 0 and local.lines == [], (
        "The budget applied to an unsharded run"
    )

    # A run that is already red is red for a better reason. The check must not
    # downgrade that status, and must not print "this is not a test failure"
    # directly above a real FAILURES section, which is triage misdirection.
    interrupted = _BudgetStubReporter([ceiling * 99])
    session = _BudgetStubSession(interrupted, "1/4", exitstatus=2)
    shard_conftest._enforce_test_time_budget(session)
    assert session.exitstatus == 2, "The budget downgraded a worse exit status"
    assert interrupted.lines == [], (
        f"The budget banner claimed nothing failed on a red run: {interrupted.lines}"
    )


def test_test_time_budget_annotates_without_failing_the_run(tmp_path):
    """End to end: a passing shard over its ceiling still exits zero, loudly.

    The stub test above proves the arithmetic; this proves the wiring, which is
    where the previous guardrails in this repo failed. It runs the real hook,
    against the real terminal reporter's stats, in a real pytest process, and
    the only synthetic part is the shard count: ``1/100000000`` puts the
    ceiling at 0.12 ms, which any real test exceeds.

    The exit code stays 0 on purpose. ``record-test-durations.yml`` refuses any
    source run whose conclusion is not ``success``, and refreshing that map is
    the standard remedy for a slow gate, so failing the shard for slowness took
    away the fix. ``1 passed`` is asserted as well, because a renamed target
    would make pytest exit non-zero for "no tests ran" and the annotation would
    then be missing for the wrong reason.
    """
    target = (
        "tests/test_ci_shards.py::test_nonsense_duration_values_are_dropped_not_trusted"
    )
    summary = tmp_path / "step-summary.md"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", target, "--ci-shard=1/100000000"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "GITHUB_STEP_SUMMARY": str(summary)},
    )
    output = result.stdout + result.stderr

    assert "1 passed" in output, f"The target test did not run:\n{output}"
    assert result.returncode == 0, (
        "A shard over its test-time ceiling exited non-zero, which makes the "
        f"run ineligible for the durations refresh that fixes it:\n{output}"
    )
    assert "TEST-TIME BUDGET EXCEEDED" in output, (
        f"No budget banner in the over-budget run:\n{output}"
    )
    assert "::warning title=Test-time budget exceeded::" in output, (
        f"No GitHub annotation in the over-budget run:\n{output}"
    )
    assert summary.exists(), "The budget wrote no GITHUB_STEP_SUMMARY entry"
    assert "TEST-TIME BUDGET EXCEEDED" in summary.read_text(encoding="utf-8"), (
        f"The step summary does not name the breach: {summary.read_text()}"
    )


def test_duration_parser_reads_the_ids_pytest_actually_prints():
    """The recorder must not quietly skip tests whose ids are awkward.

    Requiring the nodeid to be a single non-space token dropped 19 real tests
    from an otherwise complete map - every one of them a parametrised id
    containing spaces. Nothing failed: the map simply had a hole, and the tests
    in it were balanced as if brand new, forever. The GitHub log prefix is
    covered here too, since ``gh run view --log`` is the intended refresh path.
    """
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    try:
        from record_test_durations import parse_durations
    finally:
        sys.path.pop(0)

    parsed = parse_durations(
        [
            "0.42s call     tests/test_a.py::test_plain",
            "0.01s setup    tests/test_a.py::test_plain",
            "12.34s call     tests/test_b.py::test_p[Clementine holding a rifle]",
            "backend\tRun tests\t2026-08-04T00:00:00.0Z 1.50s teardown "
            "tests/test_b.py::test_p[Clementine holding a rifle]",
            "5.00s call     not_tests/elsewhere.py::test_out_of_tree",
            "note: 3.00s call is mentioned in prose",
        ]
    )

    assert parsed == {
        "tests/test_a.py::test_plain": 0.43,
        "tests/test_b.py::test_p[Clementine holding a rifle]": 13.84,
    }


def test_block_shard_mode_never_consults_the_durations_map(monkeypatch):
    """The sweep's contiguous blocks stay a pure function of collection order.

    ``--ci-block-shard`` is an ordering control: re-dealing its blocks by
    recorded time would preserve the partition and destroy the only property
    the sweep exists to provide. Asserting "the blocks are still contiguous"
    would not catch a well-behaved reordering, so this asserts the stronger
    thing - block mode does not so much as *read* the data - by making the
    read explode.
    """

    def _explode(path=None):
        raise AssertionError("--ci-block-shard must not read the durations map")

    monkeypatch.setattr(shard_conftest, "_load_recorded_durations", _explode)

    nodeids = _synthetic_nodeids(20)
    union: list[str] = []
    for index in range(1, 5):
        block = _shard_via_hook(nodeids, _BLOCK_OPTION, f"{index}/4")
        start, stop = _block_shard_bounds(len(nodeids), index - 1, 4)
        assert block == nodeids[start:stop]
        union.extend(block)
    assert union == nodeids

    with pytest.raises(AssertionError, match="must not read the durations map"):
        _shard_via_hook(nodeids, _ROUND_ROBIN_OPTION, "1/4")


def test_hook_partitions_the_collection_with_the_committed_map():
    """End to end through the real hook: the shards tile the collection.

    Deliberately a fixed N rather than the gate's current one - this exercises
    the hook itself, so it should keep testing a multi-shard split even if the
    matrix is later resized down to two.
    """
    nodeids = _synthetic_nodeids(120) + sorted(_load_recorded_durations(DURATIONS_PATH))
    total = 8

    union: list[str] = []
    for index in range(1, total + 1):
        union.extend(_shard_via_hook(nodeids, _ROUND_ROBIN_OPTION, f"{index}/{total}"))

    assert sorted(union) == sorted(nodeids), (
        "The hook's shards are not a partition of the collection; "
        f"missing={sorted(set(nodeids) - set(union))[:5]}, "
        f"duplicated={len(union) - len(set(union))}"
    )


def test_shard_selection_is_reproducible_across_processes():
    """Two independent pytest processes select the identical shard.

    The unit tests above prove the algorithm is deterministic; this proves the
    *process* is, which is the form the gate actually relies on - eight
    interpreters, eight collections, one agreed partition.
    """
    targets = [
        str(TESTS_DIR / "test_scope_table.py"),
        str(TESTS_DIR / "test_ci_shards.py"),
    ]

    def collect() -> list[str]:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "--collect-only",
                "-q",
                *targets,
                "--ci-shard=2/4",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
        return [line for line in result.stdout.splitlines() if "::" in line]

    first = collect()
    assert first, "Expected shard 2/4 of the probe modules to be non-empty"
    assert collect() == first, "Two runs of the same shard selected different tests"


def test_backend_flags_record_the_durations_the_sharder_needs(workflow):
    """CI must keep emitting the data the committed map is rebuilt from.

    Dropping ``--durations`` would not break a single run - it would quietly
    make the map unrefreshable, so it would rot until the gate was back to a
    count-balanced deal with a stale file explaining why it should not be.
    """
    flags = workflow["env"]["PYTEST_FLAGS"]
    assert "--durations=0" in flags, (
        "PYTEST_FLAGS must keep --durations=0 so scripts/record_test_durations.py "
        f"can rebuild tests/ci_test_durations.json; got {flags!r}"
    )
    assert "--durations-min=0" in flags, (
        "PYTEST_FLAGS must keep --durations-min=0; pytest otherwise hides tests "
        "under 5 ms, and a hidden test is indistinguishable from a new one, "
        f"which the sharder charges the median cost. Got {flags!r}"
    )


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


# ── the coverage floor itself ────────────────────────────────────────────────
# `_warn_or_fail_on_map_coverage` is the only thing standing between a rotting
# map and a balance figure computed over a shrinking subset. Its failing branch
# never fires on a healthy tree, so without these it would be asserted by
# nobody - the exact shape the guardrail was written to replace.


def _coverage_case(recorded: int, total: int = 100) -> tuple[set[str], set[str]]:
    """Build a (gated, recorded) pair with a known coverage ratio."""
    gated = {f"tests/test_f{i}.py" for i in range(total)}
    return gated, {f"tests/test_f{i}.py" for i in range(recorded)}


@pytest.mark.parametrize(
    "recorded, passes",
    [
        (100, True),  # complete map
        (90, True),  # exactly at the floor
        (89, False),  # one file below it
        (76, False),  # the historical defect: 1.000 reported over 76%
        (0, False),  # nothing timed at all
    ],
)
def test_the_coverage_floor_fails_only_below_the_minimum(recorded, passes):
    gated, recorded_files = _coverage_case(recorded)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if passes:
            _warn_or_fail_on_map_coverage(gated, recorded_files)
            return
        with pytest.raises(AssertionError, match="below the .* floor"):
            _warn_or_fail_on_map_coverage(gated, recorded_files)


def test_an_untimed_file_warns_by_name_rather_than_failing():
    """The whole point of the ruling: a stale map warns, it does not go red.

    Named, because a warning that does not say which file is unactionable.
    """
    gated, recorded_files = _coverage_case(99)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _warn_or_fail_on_map_coverage(gated, recorded_files)
    assert len(caught) == 1, [str(w.message) for w in caught]
    assert "tests/test_f99.py" in str(caught[0].message)


def test_a_complete_map_warns_about_nothing():
    """Over-warning is its own regression: a noisy guardrail stops being read."""
    gated, recorded_files = _coverage_case(100)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _warn_or_fail_on_map_coverage(gated, recorded_files)
    assert caught == [], [str(w.message) for w in caught]
