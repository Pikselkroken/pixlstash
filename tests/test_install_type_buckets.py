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

from pixlstash.server import Server

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION_CHECK_JS = REPO_ROOT / "frontend" / "src" / "composables" / "useVersionCheck.js"
WORKER_VALIDATE_JS = REPO_ROOT / "website" / "telemetry-worker" / "src" / "validate.js"
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
