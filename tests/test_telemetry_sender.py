"""Tests for the install-ping sender.

The properties that matter here are mostly refusals: it must not send without
consent, must not send from our own CI or the demo, must not send more than once
a day, and must never raise into the caller no matter what the network does.
"""

import json
import os

import pytest

from pixlstash.telemetry import install_id, sender


@pytest.fixture
def config_path(tmp_path):
    """A server-config path with an install ID already stored beside it."""
    path = str(tmp_path / "server-config.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"port": 9537}, handle)
    install_id.ensure_install_identity(path)
    return path


@pytest.fixture(autouse=True)
def _clear_suppression(monkeypatch):
    """Run each test outside the CI/test suppression unless it asks for it.

    PYTEST_CURRENT_TEST is always set while pytest runs, so without this every
    test would exercise only the suppressed path.
    """
    for name in sender._SUPPRESS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_does_not_send_without_consent(config_path, monkeypatch):
    def _fail(*args, **kwargs):
        raise AssertionError("sent without consent")

    monkeypatch.setattr(sender, "send_install_ping", _fail)

    assert (
        sender.maybe_send_in_background(config_path, "pip", consent_enabled=False)
        is None
    )


@pytest.mark.parametrize("marker", sender._SUPPRESS_ENV_VARS)
def test_suppressed_in_our_own_environments(config_path, monkeypatch, marker):
    monkeypatch.setenv(marker, "1")

    def _fail(*args, **kwargs):
        raise AssertionError(f"sent despite {marker}")

    monkeypatch.setattr(sender, "send_install_ping", _fail)

    # Our CI, test runs and the public demo would otherwise manufacture
    # installs that do not exist, in exactly the cohorts this measures.
    #
    # Asserting *that* it is suppressed, not which marker won: pytest re-sets
    # PYTEST_CURRENT_TEST for every test phase, so it is always present here
    # regardless of what this case sets.
    assert sender.suppressed_reason() is not None
    assert (
        sender.maybe_send_in_background(config_path, "pip", consent_enabled=True)
        is None
    )


def test_suppression_names_the_marker_that_caused_it(monkeypatch):
    monkeypatch.setattr(sender.os, "environ", {"PIXLSTASH_DEMO_MODE": "1"})

    assert sender.suppressed_reason() == "PIXLSTASH_DEMO_MODE"


def test_not_suppressed_on_an_ordinary_install(monkeypatch):
    monkeypatch.setattr(sender.os, "environ", {})

    assert sender.suppressed_reason() is None


def test_does_not_send_twice_within_a_day(config_path, monkeypatch):
    sender._write_last_sent(config_path, 1_000_000.0)
    monkeypatch.setattr(
        sender, "is_due", lambda path, now=None: sender.is_due(path, now=1_000_100.0)
    )

    assert (
        sender.maybe_send_in_background(config_path, "pip", consent_enabled=True)
        is None
    )


def test_is_due_after_the_interval(config_path):
    sender._write_last_sent(config_path, 1_000_000.0)

    assert sender.is_due(config_path, now=1_000_000.0 + 3600) is False
    assert (
        sender.is_due(config_path, now=1_000_000.0 + sender.SEND_INTERVAL_SECONDS)
        is True
    )


def test_is_due_when_nothing_was_ever_sent(config_path):
    assert sender.is_due(config_path, now=1_000_000.0) is True


def test_a_backwards_clock_does_not_suppress_forever(config_path):
    sender._write_last_sent(config_path, 2_000_000.0)

    # A machine whose clock jumped back would otherwise stay silent until real
    # time caught up, which on a badly-set clock could be years.
    assert sender.is_due(config_path, now=1_000_000.0) is True


def test_corrupt_send_state_is_treated_as_never_sent(config_path):
    with open(sender._state_path(config_path), "w", encoding="utf-8") as handle:
        handle.write("{not json")

    assert sender.is_due(config_path, now=1_000_000.0) is True


# ---------------------------------------------------------------------------
# The request itself
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_sends_exactly_the_three_accepted_keys(config_path, monkeypatch):
    captured = {}

    def _fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.method
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse(204)

    monkeypatch.setattr(sender.urllib.request, "urlopen", _fake_urlopen)

    assert sender.send_install_ping(config_path, "docker") is True
    # The Worker rejects any unrecognised key outright, so an extra field here
    # would turn every ping into a 400.
    assert sorted(captured["body"]) == [
        "install_id",
        "install_type",
        "is_new_install",
    ]
    assert captured["body"]["install_type"] == "docker"
    assert captured["method"] == "POST"


def test_sends_no_version_timestamp_or_machine_detail(config_path, monkeypatch):
    captured = {}

    def _fake_urlopen(request, timeout=None):
        captured["raw"] = request.data.decode("utf-8")
        return _FakeResponse(204)

    monkeypatch.setattr(sender.urllib.request, "urlopen", _fake_urlopen)
    sender.send_install_ping(config_path, "pip")

    assert "T" not in captured["raw"].replace("install_type", "")
    assert "version" not in captured["raw"]


def test_records_the_send_so_the_next_start_stays_quiet(config_path, monkeypatch):
    monkeypatch.setattr(
        sender.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(204)
    )

    assert sender.is_due(config_path) is True
    sender.send_install_ping(config_path, "pip")
    assert sender.is_due(config_path) is False


def test_a_refused_ping_is_not_recorded_as_sent(config_path, monkeypatch):
    monkeypatch.setattr(
        sender.urllib.request, "urlopen", lambda *a, **k: _FakeResponse(500)
    )

    assert sender.send_install_ping(config_path, "pip") is False
    # Not recorded, so a later start retries rather than assuming success.
    assert sender.is_due(config_path) is True


def test_network_failure_never_raises(config_path, monkeypatch):
    def _boom(*args, **kwargs):
        raise OSError("no route to host")

    monkeypatch.setattr(sender.urllib.request, "urlopen", _boom)

    # Offline is the normal state for many self-hosted installs. It must cost
    # nothing but a missing datapoint.
    assert sender.send_install_ping(config_path, "pip") is False
    assert sender.is_due(config_path) is True


def test_http_error_never_raises(config_path, monkeypatch):
    def _http_error(*args, **kwargs):
        raise sender.urllib.error.HTTPError(
            sender.TELEMETRY_ENDPOINT, 400, "Bad Request", {}, None
        )

    monkeypatch.setattr(sender.urllib.request, "urlopen", _http_error)

    assert sender.send_install_ping(config_path, "pip") is False


def test_missing_install_id_skips_rather_than_inventing_one(tmp_path, monkeypatch):
    path = str(tmp_path / "server-config.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({}, handle)

    def _fail(*args, **kwargs):
        raise AssertionError("sent with no stored identity")

    monkeypatch.setattr(sender.urllib.request, "urlopen", _fail)

    assert sender.send_install_ping(path, "pip") is False


def test_state_file_sits_beside_the_config_not_in_the_library(config_path):
    state = sender._state_path(config_path)

    assert os.path.dirname(state) == os.path.dirname(os.path.abspath(config_path))
    assert state.endswith(sender.SEND_STATE_FILENAME)
