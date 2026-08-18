from __future__ import annotations

import json
import os
import sqlite3
import stat

import pytest

import pixlstash.startup_permissions as startup_permissions
from pixlstash.startup_permissions import (
    PERMISSION_REPAIR_PREFIX,
    find_startup_permission_issues,
    format_permission_problem,
    mkdir_private,
    permission_repair_signal,
    repair_permission_issues,
)


pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")


def mode(path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


@pytest.fixture
def app_owned_config(monkeypatch):
    owned: set[str] = set()
    monkeypatch.setattr(
        startup_permissions,
        "_app_owned_config_directories",
        lambda: owned,
    )
    return owned


def test_mkdir_private_beats_group_writable_umask_at_every_level(tmp_path):
    old_umask = os.umask(0o002)
    try:
        target = tmp_path / "first" / "second"
        mkdir_private(target)
    finally:
        os.umask(old_umask)

    assert mode(tmp_path / "first") == 0o700
    assert mode(target) == 0o700


def test_find_and_repair_hub_and_configured_library_permissions(
    tmp_path, app_owned_config
):
    config_dir = tmp_path / "config"
    library = config_dir / "images"
    config_dir.mkdir(mode=0o700)
    app_owned_config.add(os.path.realpath(config_dir))
    library.mkdir(mode=0o700)
    os.chmod(config_dir, 0o775)
    os.chmod(library, 0o775)

    hub = config_dir / "hub.db"
    hub.write_bytes(b"")
    os.chmod(hub, 0o644)
    vault = library / "vault.db"
    vault.write_bytes(b"")
    os.chmod(vault, 0o664)

    issues = find_startup_permission_issues(
        str(config_dir / "server-config.json"), str(library)
    )
    by_path = {issue.path: issue for issue in issues}

    assert by_path[str(config_dir)].repaired_mode == 0o700
    assert by_path[str(hub)].repaired_mode == 0o600
    # The default library sits inside PixlStash's private config root.
    assert by_path[str(library)].repaired_mode == 0o700
    assert by_path[str(vault)].repaired_mode == 0o600

    repair_permission_issues(issues)
    assert mode(config_dir) == 0o700
    assert mode(hub) == 0o600
    assert mode(library) == 0o700
    assert mode(vault) == 0o600
    assert not find_startup_permission_issues(
        str(config_dir / "server-config.json"), str(library)
    )


def test_discovers_the_registered_active_library_not_only_stale_config(tmp_path):
    config_dir = tmp_path / "config"
    configured = tmp_path / "configured"
    active = tmp_path / "active"
    for folder in (config_dir, configured, active):
        folder.mkdir(mode=0o700)
    os.chmod(active, 0o775)
    (active / "vault.db").write_bytes(b"")
    os.chmod(active / "vault.db", 0o644)

    hub = config_dir / "hub.db"
    connection = sqlite3.connect(hub)
    connection.execute(
        "CREATE TABLE library (path TEXT, is_active INTEGER, attached INTEGER)"
    )
    connection.execute(
        "INSERT INTO library(path, is_active, attached) VALUES (?, 1, 1)",
        (str(active),),
    )
    connection.commit()
    connection.close()
    os.chmod(hub, 0o600)

    issues = find_startup_permission_issues(
        str(config_dir / "server-config.json"), str(configured)
    )
    assert [issue.path for issue in issues] == [str(active)]


def test_never_offers_chmod_for_a_symlink(tmp_path):
    config_dir = tmp_path / "config"
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    config_dir.symlink_to(target, target_is_directory=True)

    assert not find_startup_permission_issues(
        str(config_dir / "server-config.json"), None
    )


def test_custom_config_directory_keeps_read_and_execute_bits(tmp_path):
    custom = tmp_path / "shared-deployment"
    custom.mkdir(mode=0o700)
    os.chmod(custom, 0o775)

    issues = find_startup_permission_issues(str(custom / "server.json"), None)
    assert len(issues) == 1
    assert issues[0].repaired_mode == 0o755

    repair_permission_issues(issues)
    assert mode(custom) == 0o755


def test_external_library_preserves_read_access(tmp_path):
    config = tmp_path / "config"
    library = tmp_path / "shared-library"
    config.mkdir(mode=0o700)
    library.mkdir(mode=0o700)
    os.chmod(library, 0o775)

    issues = find_startup_permission_issues(
        str(config / "server-config.json"), str(library)
    )
    assert len(issues) == 1
    assert issues[0].repaired_mode == 0o755


def test_human_and_electron_messages_name_paths_and_modes(
    tmp_path, app_owned_config
):
    config_dir = tmp_path / "config with spaces"
    config_dir.mkdir(mode=0o700)
    app_owned_config.add(os.path.realpath(config_dir))
    os.chmod(config_dir, 0o775)
    issues = find_startup_permission_issues(
        str(config_dir / "server-config.json"), None
    )

    message = format_permission_problem(issues)
    assert "PixlStash will not run" in message
    assert str(config_dir) in message
    assert "mode 775; needs 700" in message

    signal = permission_repair_signal(issues)
    assert signal.startswith(PERMISSION_REPAIR_PREFIX)
    payload = json.loads(signal.removeprefix(PERMISSION_REPAIR_PREFIX))
    assert payload["version"] == 1
    assert payload["issues"][0]["path"] == str(config_dir)
