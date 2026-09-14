import subprocess

import pytest

from pixlstash.utils import system_utils, vram_utils


@pytest.fixture(autouse=True)
def a_card_host(monkeypatch):
    """Simulate a non-Apple host for every test in this module.

    These tests describe the **card** path, and they say so by patching
    ``nvidia-smi``. That was enough while "has a GPU" meant "has CUDA"; it is
    not enough now that ``query_total_vram_mb`` and ``default_max_vram_gb``
    both branch on the memory model first, because on an Apple machine they
    answer from Metal and never reach the mocked ``nvidia-smi`` at all. The
    tests then passed on CI's Linux runners and failed on a developer's Mac -
    a test whose result depends on the machine running it, which is the thing
    the simulated-host discipline in ``test_mps_memory_and_providers.py``
    exists to avoid. The unified-memory answers are asserted there.
    """
    monkeypatch.setattr(vram_utils, "is_apple_silicon", lambda: False)
    monkeypatch.setattr(system_utils, "is_apple_silicon", lambda: False)


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


@pytest.mark.parametrize(
    "total_mb, expected_gb",
    [("32768", 32.0), ("12288", 12.0), ("8192", 8.0)],
)
def test_the_settable_ceiling_is_the_card(monkeypatch, total_mb, expected_gb):
    """The default must always be settable: 16 GB on a 32 GB card was not."""
    monkeypatch.setattr(
        subprocess, "check_output", lambda *args, **kwargs: f"{total_mb}\n"
    )
    assert system_utils.max_vram_budget_gb() == expected_gb
    assert system_utils.default_max_vram_gb() <= system_utils.max_vram_budget_gb()


def test_the_settable_ceiling_falls_back_without_a_card(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("nvidia-smi")

    monkeypatch.setattr(subprocess, "check_output", missing)
    assert system_utils.max_vram_budget_gb() == system_utils.MAX_VRAM_BUDGET_GB
