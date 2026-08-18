from __future__ import annotations

import io
import os
import stat

import pytest

from pixlstash import app
import pixlstash.startup_permissions as startup_permissions
from pixlstash.startup_permissions import PERMISSION_REPAIR_PREFIX


pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits")


class TerminalInput(io.StringIO):
    def isatty(self) -> bool:
        return True


def mode(path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def loose_config(tmp_path):
    config_dir = tmp_path / "config"
    library = config_dir / "images"
    config_dir.mkdir(mode=0o700)
    library.mkdir(mode=0o700)
    os.chmod(config_dir, 0o775)
    os.chmod(library, 0o775)
    return config_dir / "server-config.json", library


@pytest.fixture(autouse=True)
def treat_test_config_as_app_owned(monkeypatch, tmp_path):
    monkeypatch.setattr(
        startup_permissions,
        "_app_owned_config_directories",
        lambda: {os.path.realpath(tmp_path / "config")},
    )


def test_terminal_default_yes_repairs_and_continues(tmp_path, monkeypatch, capsys):
    config_path, library = loose_config(tmp_path)
    monkeypatch.delenv("PIXLSTASH_INSTALL_TYPE", raising=False)
    monkeypatch.delenv("PIXLSTASH_REPAIR_PERMISSIONS", raising=False)
    monkeypatch.setattr(app.sys, "stdin", TerminalInput())
    monkeypatch.setattr("builtins.input", lambda _prompt: "")

    assert app._prepare_startup_permissions(
        str(config_path), {"image_root": str(library)}
    )
    assert mode(config_path.parent) == 0o700
    assert mode(library) == 0o700
    assert "Permissions fixed" in capsys.readouterr().err


def test_terminal_no_leaves_permissions_unchanged(tmp_path, monkeypatch, capsys):
    config_path, library = loose_config(tmp_path)
    monkeypatch.delenv("PIXLSTASH_INSTALL_TYPE", raising=False)
    monkeypatch.delenv("PIXLSTASH_REPAIR_PERMISSIONS", raising=False)
    monkeypatch.setattr(app.sys, "stdin", TerminalInput())
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")

    assert not app._prepare_startup_permissions(
        str(config_path), {"image_root": str(library)}
    )
    assert mode(config_path.parent) == 0o775
    assert "Permissions were not changed" in capsys.readouterr().err


def test_electron_emits_signal_without_changing_anything(tmp_path, monkeypatch, capsys):
    config_path, library = loose_config(tmp_path)
    monkeypatch.setenv("PIXLSTASH_INSTALL_TYPE", "electron")
    monkeypatch.delenv("PIXLSTASH_REPAIR_PERMISSIONS", raising=False)

    assert not app._prepare_startup_permissions(
        str(config_path), {"image_root": str(library)}
    )
    stderr = capsys.readouterr().err
    assert PERMISSION_REPAIR_PREFIX in stderr
    assert mode(config_path.parent) == 0o775


def test_noninteractive_launch_prints_copyable_commands(tmp_path, monkeypatch, capsys):
    config_path, library = loose_config(tmp_path)
    monkeypatch.delenv("PIXLSTASH_INSTALL_TYPE", raising=False)
    monkeypatch.delenv("PIXLSTASH_REPAIR_PERMISSIONS", raising=False)
    monkeypatch.setattr(app.sys, "stdin", io.StringIO())

    assert not app._prepare_startup_permissions(
        str(config_path), {"image_root": str(library)}
    )
    stderr = capsys.readouterr().err
    assert f"chmod 700 {config_path.parent}" in stderr
    assert f"chmod 700 {library}" in stderr
