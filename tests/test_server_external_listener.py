"""Tests for the Electron desktop dual-listener / external-server support.

The desktop shell runs the backend as a private loopback service on an ephemeral
HTTP port, and can *optionally* expose a second, external listener (fixed port,
optional HTTPS) for remote devices. These tests cover the config defaulting and
the listener-config construction without binding any sockets.
"""

import json
import os
from types import SimpleNamespace

import pytest

from pixlstash.server import Server
from pixlstash.startup_checks import StartupChecks


def _write_config(tmp_path, **overrides):
    path = os.path.join(tmp_path, "server-config.json")
    base = {"image_root": os.path.join(tmp_path, "images")}
    base.update(overrides)
    with open(path, "w") as handle:
        json.dump(base, handle)
    return path


def test_init_server_config_adds_external_flag_to_fresh_config(tmp_path):
    """A brand-new config gets external_server_enabled defaulted to False."""
    path = os.path.join(tmp_path, "server-config.json")
    config = Server.init_server_config(path)
    assert config["external_server_enabled"] is False
    # And it is persisted, not just returned.
    with open(path) as handle:
        assert json.load(handle)["external_server_enabled"] is False


def test_init_server_config_backfills_external_flag(tmp_path):
    """An existing config without the key has it backfilled to False."""
    path = _write_config(tmp_path, host="localhost", port=9537)
    config = Server.init_server_config(path)
    assert config["external_server_enabled"] is False


def _fake_server(config, password_set=True):
    """A minimal stand-in exposing just what _build_electron_configs touches.

    By default the stand-in reports an owner password is set, so the external
    listener may bind. Pass ``password_set=False`` to simulate the auto-logged-in
    desktop owner who never set a password (the external listener must then
    refuse to bind — see test_external_listener_refused_without_owner_password).
    """
    password_hash = "bcrypt-hash" if password_set else None
    auth = SimpleNamespace(
        get_user=lambda: SimpleNamespace(password_hash=password_hash)
    )
    return SimpleNamespace(api=object(), _server_config=config, auth=auth)


def test_loopback_only_when_external_disabled():
    server = _fake_server({"external_server_enabled": False})
    configs, banner = Server._build_electron_configs(server, "127.0.0.1", 50101)

    assert len(configs) == 1
    loopback = configs[0]
    assert loopback.host == "127.0.0.1"
    assert loopback.port == 50101
    assert not loopback.is_ssl
    # The loopback server owns the app lifespan (uvicorn default, not "off").
    assert loopback.lifespan != "off"
    assert banner == [("Window", "http://127.0.0.1:50101")]


def test_external_http_listener_added_when_enabled():
    server = _fake_server({"external_server_enabled": True, "port": 9537})
    configs, banner = Server._build_electron_configs(server, "127.0.0.1", 50102)

    assert len(configs) == 2
    loopback, external = configs
    # Loopback stays private HTTP on the ephemeral port.
    assert loopback.host == "127.0.0.1"
    assert not loopback.is_ssl
    # External binds all interfaces on the configured port, no SSL here.
    assert external.host == "0.0.0.0"
    assert external.port == 9537
    assert not external.is_ssl
    # Only the loopback runs the lifespan; the external listener must not.
    assert external.lifespan == "off"
    assert ("Remote", "http://0.0.0.0:9537") in banner


def test_external_https_listener_uses_ssl_paths(tmp_path):
    keyfile = os.path.join(tmp_path, "key.pem")
    certfile = os.path.join(tmp_path, "cert.pem")
    server = _fake_server(
        {
            "external_server_enabled": True,
            "port": 8443,
            "require_ssl": True,
            "ssl_keyfile": keyfile,
            "ssl_certfile": certfile,
        }
    )
    configs, banner = Server._build_electron_configs(server, "127.0.0.1", 50103)

    external = configs[1]
    assert external.is_ssl
    assert external.ssl_keyfile == keyfile
    assert external.ssl_certfile == certfile
    # The loopback never gets SSL even when require_ssl is on.
    assert not configs[0].is_ssl
    assert ("Remote", "https://0.0.0.0:8443") in banner


def test_external_listener_refused_without_owner_password():
    """Remote access must NOT bind 0.0.0.0 while the owner has no password set.

    The auto-logged-in desktop owner can have password_hash=None; exposing the
    external listener in that state lets any LAN device claim the empty owner
    account (BLOCKER 1). _build_electron_configs must fail closed and serve the
    loopback window only.
    """
    server = _fake_server(
        {"external_server_enabled": True, "port": 9537}, password_set=False
    )
    configs, banner = Server._build_electron_configs(server, "127.0.0.1", 50104)

    # Only the loopback listener — the external one was refused.
    assert len(configs) == 1
    assert configs[0].host == "127.0.0.1"
    assert banner == [("Window", "http://127.0.0.1:50104")]
    # No "Remote" row leaked into the banner.
    assert all(label != "Remote" for label, _ in banner)


def test_external_listener_refused_when_no_auth_service():
    """Fail closed: with no auth service to verify the password, refuse to bind."""
    server = SimpleNamespace(
        api=object(),
        _server_config={"external_server_enabled": True, "port": 9537},
    )
    configs, banner = Server._build_electron_configs(server, "127.0.0.1", 50105)
    assert len(configs) == 1
    assert configs[0].host == "127.0.0.1"


def _fake_lifespan_server(config, password_set=True):
    """A minimal stand-in carrying the few attributes Server.lifespan reads.

    Reuses _fake_server's auth stub (password presence) and adds the no-op vault
    and lifespan bookkeeping fields the startup path touches, so the lifespan can
    run far enough to emit its ready/remote-access log lines without a real vault.
    """
    base = _fake_server(config, password_set=password_set)
    return SimpleNamespace(
        api=base.api,
        _server_config=config,
        auth=base.auth,
        _ws_loop=None,
        _shutdown_on_lifespan=False,
        vault=SimpleNamespace(start=lambda: None, close=lambda: None),
    )


def _drive_lifespan(server):
    import asyncio

    async def _run():
        async with Server.lifespan(server, app=object()):
            pass

    asyncio.run(_run())


def test_lifespan_reports_remote_access_active_with_password(monkeypatch, caplog):
    """With an owner password set, the ready log reports remote access active."""
    import logging

    monkeypatch.setenv("PIXLSTASH_INSTALL_TYPE", "electron")
    server = _fake_lifespan_server(
        {
            "external_server_enabled": True,
            "port": 9537,
            "generate_thumbnails_on_startup": False,
        },
        password_set=True,
    )
    with caplog.at_level(logging.INFO, logger="pixlstash.server"):
        _drive_lifespan(server)

    assert "Remote access enabled: http://0.0.0.0:9537/" in caplog.text
    assert "NOT active" not in caplog.text


def test_lifespan_warns_remote_access_inactive_without_password(monkeypatch, caplog):
    """Regression: without an owner password the external listener is refused, so
    the ready log must NOT claim remote access is enabled — it warns instead.

    This is the dishonest-log bug: the listener was refused for lack of a
    password (see test_external_listener_refused_without_owner_password) yet the
    startup log previously printed "Remote access enabled", which read as working
    while the host was unreachable from the LAN.
    """
    import logging

    monkeypatch.setenv("PIXLSTASH_INSTALL_TYPE", "electron")
    server = _fake_lifespan_server(
        {
            "external_server_enabled": True,
            "port": 9537,
            "generate_thumbnails_on_startup": False,
        },
        password_set=False,
    )
    with caplog.at_level(logging.INFO, logger="pixlstash.server"):
        _drive_lifespan(server)

    assert "Remote access enabled" not in caplog.text
    assert "Remote access is configured but NOT active" in caplog.text


def _run_startup_port_check(server_config, server_config_path, logger):
    checks = StartupChecks(
        server_config=server_config,
        server_config_path=server_config_path,
        logger=logger,
    )
    from pixlstash.startup_checks import StartupCheckOutcome

    outcome = StartupCheckOutcome()
    checks._check_port_bindable(outcome)
    return outcome


def test_port_check_skipped_for_disabled_electron_external(tmp_path, monkeypatch):
    """In electron mode with remote access off, the configured port is not checked.

    The loopback uses an ephemeral port, so a busy configured port must not fail
    startup. We bind the configured port first, then assert the check passes.
    """
    import logging
    import socket

    monkeypatch.setenv("PIXLSTASH_INSTALL_TYPE", "electron")
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", 0))
    busy_port = blocker.getsockname()[1]
    blocker.listen(1)
    try:
        config = {
            "host": "127.0.0.1",
            "port": busy_port,
            "external_server_enabled": False,
        }
        outcome = _run_startup_port_check(
            config, os.path.join(tmp_path, "server-config.json"), logging.getLogger()
        )
        assert not outcome.hard_failures
    finally:
        blocker.close()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
