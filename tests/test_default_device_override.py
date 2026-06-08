"""``PIXLSTASH_DEFAULT_DEVICE`` overrides the config's ``default_device``.

The Electron desktop launcher passes this so the backend uses whatever the
*active runtime* actually provides — the bundled env ships CPU-only torch, GPU
wheels are added on demand as overlays — regardless of the config value (which
can't tell the CPU build from a GPU overlay). It's a general env override, so a
Docker deploy can use it too.
"""

import json

from pixlstash.server import Server

DEVICE_ENV = "PIXLSTASH_DEFAULT_DEVICE"


def _write_config(path, **overrides):
    config = {
        "image_root": str(path.parent / "images"),
        "default_device": "cuda",
    }
    config.update(overrides)
    with open(path, "w") as f:
        json.dump(config, f)


class TestDefaultDeviceOverride:
    def test_override_replaces_config_value(self, tmp_path, monkeypatch):
        path = tmp_path / "server-config.json"
        _write_config(path, default_device="cuda")
        monkeypatch.setenv(DEVICE_ENV, "cpu")

        cfg = Server.init_server_config(str(path))

        assert cfg["default_device"] == "cpu"

    def test_override_is_lowercased(self, tmp_path, monkeypatch):
        path = tmp_path / "server-config.json"
        _write_config(path)
        monkeypatch.setenv(DEVICE_ENV, "CUDA")

        cfg = Server.init_server_config(str(path))

        assert cfg["default_device"] == "cuda"

    def test_no_override_keeps_config_value(self, tmp_path, monkeypatch):
        path = tmp_path / "server-config.json"
        _write_config(path, default_device="cuda")
        monkeypatch.delenv(DEVICE_ENV, raising=False)

        cfg = Server.init_server_config(str(path))

        assert cfg["default_device"] == "cuda"

    def test_blank_override_ignored(self, tmp_path, monkeypatch):
        path = tmp_path / "server-config.json"
        _write_config(path, default_device="auto")
        monkeypatch.setenv(DEVICE_ENV, "   ")

        cfg = Server.init_server_config(str(path))

        assert cfg["default_device"] == "auto"
