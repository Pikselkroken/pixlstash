"""The install-type bucket list must agree in all four places that hold it.

``install_type`` is one string that four independent components have to recognise,
and three of them are not Python:

1. ``Server.INSTALL_TYPES`` decides what the backend will report at all.
2. ``TELEMETRY_INSTALL_BUCKETS`` (``useVersionCheck.js``) decides what the
   browser will put in the version-check path; anything else collapses to
   ``other``.
3. ``INSTALL_TYPES`` (``telemetry-worker/src/validate.js``) decides which install
   pings the ingestion Worker accepts rather than rejecting outright.
4. ``website/latest-version/<bucket>.json`` decides whether the version-check URL
   for that bucket answers JSON or a 404 page.

Adding a bucket to some-but-not-all of those is the exact shape of the bug this
file exists to stop, and it has already happened once: the metrics collector was
taught to classify a declared ``dev`` machine into its own excluded bucket while
(1) still rejected ``PIXLSTASH_INSTALL_TYPE=dev`` as invalid. A developer who set
it was reported as an ordinary ``pip`` install, so the cohort the change existed
to subtract was never marked.

(4) is the one with teeth, because a missing manifest is not merely a lost
signal. The bucket answers 404 *HTML*, and the client used to stamp its
24-hour throttle only after parsing JSON, so the first machine to ask for a
bucket with no file would have re-checked on every page load. That combination
never shipped -- every released bucket had a manifest, so every check got JSON --
and ``useVersionCheck.js`` now stamps before the request. This test keeps the
other half of the invariant.

A fifth holder, ``INSTALL_BUCKETS`` in the pixlstash-metrics collector, lives in
another repository and cannot be checked from here. It is the *consumer*: a
bucket it does not know is counted as real rather than dropped, which
over-reports installs instead of losing them. Adding a bucket means editing it
too.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
import yaml

from pixlstash.server import Server

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION_CHECK_JS = REPO_ROOT / "frontend" / "src" / "composables" / "useVersionCheck.js"
WORKER_VALIDATE_JS = REPO_ROOT / "website" / "telemetry-worker" / "src" / "validate.js"


@pytest.fixture(autouse=True)
def isolate_the_machine(tmp_path, monkeypatch):
    """Cut every test in this file off from the developer's own machine.

    ``detect_install_type()`` reads the dev-machine marker out of
    ``user_data_dir("pixlstash")`` at priority 0, ahead of the
    ``PIXLSTASH_INSTALL_TYPE`` override - so on a machine that actually has
    the marker, every channel assertion below returns ``"dev"`` and nine of
    these tests fail. That machine is not hypothetical: it is precisely the
    maintainer box the release checklist tells you to create the marker on.

    CI cannot catch this - a runner has neither the marker nor the env var -
    so the isolation has to be here. Points the probe at an empty per-test
    directory and clears both declaring variables; a test that wants either
    sets it itself afterwards.
    """
    monkeypatch.setattr(
        "pixlstash.server.user_data_dir", lambda *args, **kwargs: str(tmp_path)
    )
    monkeypatch.delenv(Server.DEV_MACHINE_ENV_VAR, raising=False)
    monkeypatch.delenv("PIXLSTASH_INSTALL_TYPE", raising=False)
    return tmp_path


MANIFEST_DIR = REPO_ROOT / "website" / "latest-version"


def _string_literals(block: str) -> set[str]:
    """Return every quoted string inside *block*."""
    return set(re.findall(r"""["']([^"']+)["']""", block))


def _js_collection(path: Path, declaration: str) -> set[str]:
    """Return the string members of a JS array/Set literal named *declaration*.

    Deliberately a source parse rather than a build step: the point is to fail on
    a hand-edit of the constant, and running the bundler (or node) to learn a
    frozen array's contents would make a documentation guardrail depend on the
    frontend toolchain being installed in the backend gate.
    """
    source = path.read_text(encoding="utf-8")
    start = source.find(declaration)
    assert start != -1, (
        f"{declaration} not found in {path}; the guardrail cannot see the list"
    )
    opening = source.find("[", start)
    closing = source.find("]", opening)
    assert opening != -1 and closing != -1, (
        f"Could not find the array literal for {declaration} in {path}"
    )
    return _string_literals(source[opening + 1 : closing])


def test_frontend_version_check_buckets_match_the_backend():
    """The browser must be willing to send every type the backend can report."""
    assert _js_collection(VERSION_CHECK_JS, "TELEMETRY_INSTALL_BUCKETS") == set(
        Server.INSTALL_TYPES
    )


def test_telemetry_worker_accepts_every_backend_install_type():
    """A ping the backend can send must not be rejected as an unknown bucket."""
    assert _js_collection(WORKER_VALIDATE_JS, "export const INSTALL_TYPES") == set(
        Server.INSTALL_TYPES
    )


def test_every_bucket_has_a_published_version_manifest():
    """Every bucket resolves to a real file, so no bucket can answer 404 HTML.

    The live URL carries a version segment
    (``/latest-version/{version}/{bucket}.json``) that a Cloudflare rewrite rule
    strips before serving these files, so the filename is the whole contract.
    """
    published = {p.stem for p in MANIFEST_DIR.glob("*.json")}
    missing = set(Server.INSTALL_TYPES) - published
    assert not missing, (
        f"No website/latest-version/ manifest for {sorted(missing)}. Those buckets "
        "would answer 404 HTML, which leaves the client's 24h throttle unstamped "
        "and re-checks on every page load. Add the file on main and deploy it "
        "BEFORE shipping a client that asks for the bucket."
    )


def test_version_manifests_agree_on_the_version():
    """All manifests carry the identical payload, as the release job writes them."""
    payloads = {
        p.name: json.loads(p.read_text(encoding="utf-8"))
        for p in MANIFEST_DIR.glob("*.json")
    }
    assert payloads, "no version manifests found at all"
    distinct = {json.dumps(v, sort_keys=True) for v in payloads.values()}
    assert len(distinct) == 1, f"version manifests disagree: {payloads}"


@pytest.mark.parametrize("bucket", Server.INSTALL_TYPES)
def test_declared_bucket_is_honoured_as_an_override(monkeypatch, bucket):
    """Every allowed bucket survives as a declaration, not just in the tuple.

    ``dev`` is the one that matters: it is a declaration with no detectable
    signal behind it, so if the override path ever stopped honouring it the value
    would fall back to ``pip`` and the machine would be counted as a real
    install. Docker signals are forced on to prove the override still wins.
    """
    monkeypatch.setenv("PIXLSTASH_IN_DOCKER", "1")
    monkeypatch.delenv(Server.DEV_MACHINE_ENV_VAR, raising=False)
    monkeypatch.setenv("PIXLSTASH_INSTALL_TYPE", bucket)
    assert Server.detect_install_type() == bucket


def test_dev_machine_marker_outranks_the_channel(monkeypatch):
    """A dev desktop launch must report ``dev``, not ``electron``.

    The shell has to keep declaring ``electron`` because the backend reads that
    exact value as a runtime switch, so the machine is declared separately and
    has to win. Without this the developer's own desktop app is indistinguishable
    from a real user's.
    """
    monkeypatch.setenv("PIXLSTASH_INSTALL_TYPE", "electron")
    monkeypatch.setenv(Server.DEV_MACHINE_ENV_VAR, "1")
    assert Server.detect_install_type() == "dev"


def test_the_dev_marker_does_not_disturb_the_electron_runtime_switch(monkeypatch):
    """Labelling the machine must not change how the process runs.

    ``PIXLSTASH_INSTALL_TYPE == "electron"`` gates ``cookie_secure``, the loopback
    listener and the external-listener startup check, and those read the
    environment directly rather than going through ``detect_install_type``. This
    pins that separation: the marker changes the reported bucket and leaves the
    switch the desktop transport depends on exactly as the shell set it.
    """
    monkeypatch.setenv("PIXLSTASH_INSTALL_TYPE", "electron")
    monkeypatch.setenv(Server.DEV_MACHINE_ENV_VAR, "1")
    assert Server.detect_install_type() == "dev"
    assert os.environ["PIXLSTASH_INSTALL_TYPE"].strip().lower() == "electron"


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off"])
def test_an_unset_or_negative_dev_marker_is_not_a_declaration(monkeypatch, value):
    """Only an affirmative value declares a dev machine.

    An empty or falsy marker leaking into a released build would relabel real
    users' installs as ours and quietly delete them from the active-install count.
    """
    monkeypatch.setenv("PIXLSTASH_INSTALL_TYPE", "electron")
    monkeypatch.setenv(Server.DEV_MACHINE_ENV_VAR, value)
    assert Server.detect_install_type() == "electron"


def test_the_shell_sets_the_marker_only_for_a_dev_backend():
    """The declaration must be wired up in the shell, not just readable here.

    A test that only exercised the Python side would pass with the desktop app
    never setting the marker at all, which is the bug being fixed.
    """
    source = (
        REPO_ROOT / "electron" / "src" / "backend" / "ServerProcess.ts"
    ).read_text(encoding="utf-8")
    assert "PIXLSTASH_TELEMETRY_DEV: '1'" in source, (
        "the desktop shell no longer declares its dev backend as a dev machine"
    )
    assert "isDevBackend() ? { PIXLSTASH_TELEMETRY_DEV" in source, (
        "the dev marker must be conditional on isDevBackend(), or every desktop "
        "install would report as ours"
    )
    assert "PIXLSTASH_INSTALL_TYPE: 'electron'" in source, (
        "the shell must keep declaring the electron channel - it is a runtime "
        "switch for cookie_secure and the loopback listener"
    )


def test_e2e_suite_blocks_the_production_host():
    """The Playwright suite must never depend on pixlstash.dev being reachable.

    ``useVersionCheck.js``'s 24h throttle lives in ``localStorage``, and every
    fresh Playwright context starts with empty storage - so a context that
    reaches the live version-check endpoint is a real check-in against
    production, not just a latent flake (issue #1213). The fix is a route
    block registered once, on the shared ``browser`` fixture that every spec's
    context (including the handful minted by hand with
    ``browser.newContext()``) is funnelled through, so a future spec author
    does not have to remember to add it themselves.
    """
    source = (REPO_ROOT / "frontend" / "e2e" / "fixtures" / "test.js").read_text(
        encoding="utf-8"
    )
    assert "https://pixlstash.dev/**" in source, (
        "frontend/e2e/fixtures/test.js no longer names the production host to "
        "block; the e2e suite can reach it again"
    )
    assert re.search(r"\.route\(\s*PRODUCTION_HOST_PATTERN", source), (
        "frontend/e2e/fixtures/test.js declares the production host pattern "
        "but no longer registers a route to abort it"
    )
    assert "browser.newContext = async" in source, (
        "the route must be wired onto the browser fixture's newContext, or "
        "specs that call browser.newContext() directly (auth.spec.js, "
        "sharing.spec.js, read-only-features.spec.js) bypass the block"
    )


def _declares_dev_machine(env) -> bool:
    """True when *env* sets the marker to a value ``detect_install_type`` accepts.

    Mirrors the server's own acceptance set rather than pinning the literal
    ``'1'``: a workflow rewritten to ``true`` (or unquoted ``1``, which YAML
    hands back as an int) still declares the machine, and this guardrail is
    about the declaration, not its spelling.
    """
    value = (env or {}).get(Server.DEV_MACHINE_ENV_VAR)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def test_app_booting_workflows_declare_themselves_dev():
    """Every workflow that boots the real server exports the dev marker.

    Belt and braces with the Playwright route block above: anything that
    reaches the network by a path Playwright's route interception does not
    cover (or, for the non-Playwright smoke jobs below, any future step that
    grows one) should still arrive labelled ``dev`` rather than a real
    install. See ``Server.DEV_MACHINE_ENV_VAR``.

    Parsed rather than pattern-matched. ``tests/test_ci_shards.py`` already
    reads these files with ``yaml.safe_load`` for the same kind of assertion,
    and a parser is immune to the things that have nothing to do with the
    guardrail: which job comes last in the file, how the value is quoted, and
    how deeply the block is indented.
    """
    workflows_dir = REPO_ROOT / ".github" / "workflows"

    def job(workflow: str, name: str) -> dict:
        data = yaml.safe_load((workflows_dir / workflow).read_text(encoding="utf-8"))
        jobs = data.get("jobs") or {}
        assert name in jobs, f"{workflow} no longer has a `{name}:` job"
        return jobs[name]

    e2e = job("ci.yml", "e2e")
    assert _declares_dev_machine(e2e.get("env")), (
        "ci.yml's e2e job no longer declares PIXLSTASH_TELEMETRY_DEV"
    )

    smoke = job("install-smoke.yml", "smoke")
    assert _declares_dev_machine(smoke.get("env")), (
        "install-smoke.yml's smoke job no longer declares PIXLSTASH_TELEMETRY_DEV"
    )

    docker = job("docker-build.yml", "build")
    assert _declares_dev_machine(docker.get("env")), (
        "docker-build.yml's build job no longer declares PIXLSTASH_TELEMETRY_DEV"
    )
    # A job-level env var means nothing to a container unless it is forwarded.
    run_steps = " ".join(
        step.get("run", "")
        for step in (docker.get("steps") or [])
        if isinstance(step, dict)
    )
    assert f"-e {Server.DEV_MACHINE_ENV_VAR}" in run_steps, (
        "docker-build.yml declares the marker but no longer forwards it into "
        "the smoked container"
    )


def test_marker_file_declares_dev_machine(isolate_the_machine):
    """A marker file in the app-data directory declares a dev machine.

    Release-candidate testing and packaged desktop builds launched from the OS
    shell do not inherit the ``PIXLSTASH_TELEMETRY_DEV`` env var, so the marker
    file provides a durable declaration that survives reinstalling and repackaging.
    """
    (isolate_the_machine / ".pixlstash-dev-machine").touch()
    assert Server.detect_install_type() == "dev"


def test_marker_file_absent_falls_back_to_detection(isolate_the_machine):
    """Without a marker file or env var, install type is detected normally.

    This ensures the marker file is truly optional and does not interfere with
    normal install-type detection when not present.
    """
    assert not (isolate_the_machine / ".pixlstash-dev-machine").exists()
    # Falls through to channel detection. Assert on what this test is about -
    # the marker did not fire - rather than on which channel the runner
    # happens to look like: the same suite runs under Docker in CI, where
    # "pip" would be the wrong answer.
    result = Server.detect_install_type()
    assert result in Server.INSTALL_TYPES
    assert result != "dev"
