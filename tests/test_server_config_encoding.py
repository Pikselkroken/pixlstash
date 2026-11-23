"""``server-config.json`` is read as UTF-8 whatever the system code page (#2802).

The desktop writes the file with Node's ``writeFileSync``: UTF-8, non-ASCII left
unescaped. A bare ``open()`` decodes with the locale code page instead, which
on a Cyrillic Windows is cp1251, so a library path such as ``Проба`` came back
as ``РџСЂРѕР±Р°`` and the server created and used an empty folder by that name.

Linux and macOS default to UTF-8, so each test makes ``open()`` default to
cp1251 the way Windows does, for the module under test only.
"""

import builtins
import json

import pytest

import pixlstash.app as app_module
import pixlstash.server as server_module
from pixlstash.server import Server

LIBRARY = "C:\\Users\\me\\Pictures\\Проба\\Паки"


def _cp1251_open(file, mode="r", *args, **kwargs):
    if "b" not in mode and len(args) < 3:
        kwargs.setdefault("encoding", "cp1251")
    return builtins.open(file, mode, *args, **kwargs)


@pytest.fixture
def desktop_config(tmp_path):
    path = tmp_path / "server-config.json"
    # What electron/src/main.ts `setup:commit` writes.
    path.write_bytes(
        json.dumps({"image_root": LIBRARY}, ensure_ascii=False).encode("utf-8")
    )
    return path


def test_server_reads_a_non_ascii_library_path_unchanged(desktop_config, monkeypatch):
    monkeypatch.setattr(server_module, "open", _cp1251_open, raising=False)

    config = Server.init_server_config(str(desktop_config))

    assert config["image_root"] == LIBRARY


def test_cli_does_not_rerun_first_run_setup_over_a_non_ascii_path(
    tmp_path, monkeypatch
):
    # "И" encodes as D0 98 and cp1251 has no 0x98, so a locale decode raises and
    # the CLI took the config for unreadable and offered first-run setup again.
    path = tmp_path / "server-config.json"
    path.write_bytes(
        json.dumps({"image_root": "C:\\Users\\me\\Игры"}, ensure_ascii=False).encode(
            "utf-8"
        )
    )
    monkeypatch.setattr(app_module, "open", _cp1251_open, raising=False)

    assert app_module._should_prompt_bootstrap(str(path), False) is False
