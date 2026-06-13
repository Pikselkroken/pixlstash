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

    def test_invalid_override_is_rejected_and_config_kept(
        self, tmp_path, monkeypatch, caplog
    ):
        """An unknown device value must not be written through (and is logged).

        Previously ``PIXLSTASH_DEFAULT_DEVICE=banana`` was written straight into
        the config and silently fell back to CPU with no explanation. It must be
        validated against the known set, warned about, and ignored — leaving the
        configured value in place.
        """
        path = tmp_path / "server-config.json"
        _write_config(path, default_device="auto")
        monkeypatch.setenv(DEVICE_ENV, "banana")

        with caplog.at_level("WARNING"):
            cfg = Server.init_server_config(str(path))

        assert cfg["default_device"] == "auto", (
            "Invalid override must not overwrite the configured device"
        )
        assert any(
            "banana" in r.message and "PIXLSTASH_DEFAULT_DEVICE" in r.message
            for r in caplog.records
        ), "Invalid override should be logged with a warning"

    def test_gpu_override_accepted(self, tmp_path, monkeypatch):
        """'gpu' is a known value (mapped to cuda by StartupChecks) and accepted."""
        path = tmp_path / "server-config.json"
        _write_config(path, default_device="auto")
        monkeypatch.setenv(DEVICE_ENV, "gpu")

        cfg = Server.init_server_config(str(path))

        assert cfg["default_device"] == "gpu"
