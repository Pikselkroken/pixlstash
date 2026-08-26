import subprocess

import pytest

from pixlstash.utils import system_utils


@pytest.mark.parametrize(
    "total_mb, expected_gb",
    [("32768", 16.0), ("12288", 6.0), ("8192", 4.0)],
)
def test_default_max_vram_gb_is_card_aware(monkeypatch, total_mb, expected_gb):
    monkeypatch.setattr(
        subprocess, "check_output", lambda *args, **kwargs: f"{total_mb}\n"
    )
    assert system_utils.default_max_vram_gb() == expected_gb


def test_default_max_vram_gb_falls_back_to_6_without_nvidia_smi(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(subprocess, "check_output", missing)
    assert system_utils.default_max_vram_gb() == 6.0


def test_default_max_vram_gb_falls_back_to_6_when_total_is_zero(monkeypatch):
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: "0\n")
    assert system_utils.default_max_vram_gb() == 6.0
