"""What this machine measured a training setup to need (#2120)."""

from __future__ import annotations

import pytest

from pixlstash.training import measurements, presets

_ZIT = presets.get_preset("zimage-turbo")


def _values(**changes):
    resolution = presets.resolve(
        _ZIT,
        subject="person",
        effort="standard",
        fit="auto",
        picture_count=24,
        vram_mb=22_800,
        ram_mb=60_000,
    )
    values = presets.resolved_values(resolution)
    values.update(changes)
    return values


def _key(values=None, commit="abc123", device="Test GPU"):
    return measurements.fingerprint(
        _ZIT, values or _values(), commit=commit, device=device
    )


def test_fingerprint_follows_what_moves_memory_and_speed_only():
    base = _key()
    assert _key() == base
    # What a run trains with, not what it peaks at or how fast it goes.
    assert _key(_values(**{"train.lr": 0.0002, "train.steps": 99})) == base
    assert _key(_values(**{"datasets[*].caption_dropout_rate": 0.2})) == base
    # The deferral is decided from the measurement, so it is not part of it.
    assert _key(_values(**{"train.disable_sampling": True})) == base
    for changed in (
        _key(_values(**{"network.linear": 16})),
        _key(_values(**{"model.quantize": False})),
        _key(_values(**{"model.layer_offloading_transformer_percent": 0.5})),
        _key(_values(**{"datasets[*].resolution": [512, 768]})),
        _key(_values(**{"train.gradient_accumulation": 2})),
        _key(_values(**{"train.cache_text_embeddings": True})),
        _key(_values(**{"sample.width": 768})),
        _key(commit="def456"),
        _key(device="Other GPU"),
    ):
        assert changed != base


def test_peaks_keep_the_highest_figure_per_phase():
    peaks: dict = {}
    measurements.note_peak(peaks, "training", 9000, 12000)
    measurements.note_peak(peaks, "training", 8000, None)
    measurements.note_peak(peaks, "training", None, 13000)
    measurements.note_peak(peaks, None, 99999, 99999)
    assert peaks == {"training": {"vram_mb": 9000, "ram_mb": 13000}}


def test_needs_put_previews_apart_with_a_margin():
    needs = measurements.needs(
        {
            "phase_peaks": {
                "loading": {"vram_mb": 7000, "ram_mb": 20000},
                "training": {"vram_mb": 10000, "ram_mb": 15000},
                "previews": {"vram_mb": 14000, "ram_mb": None},
            }
        }
    )
    assert needs == {
        "vram_mb": 11000,
        "ram_mb": 22000,
        "preview_vram_mb": 15400,
        "preview_ram_mb": None,
    }


def test_estimate_is_setup_updates_and_preview_images():
    measured = {
        "seconds_per_update": 2.0,
        "setup_seconds": 120.0,
        "seconds_per_preview": 5.0,
    }
    assert measurements.estimate_seconds(measured, steps=100, previews=8) == 360.0
    assert (
        measurements.estimate_seconds(
            {"seconds_per_update": None}, steps=100, previews=0
        )
        is None
    )


def test_a_setup_measured_again_keeps_its_highest_peaks(hub):
    key = _key()
    assert measurements.read(hub, key) is None
    measurements.record(
        hub,
        key,
        preset="zimage-turbo@1",
        profile="q8",
        device="Test GPU",
        phase_peaks={"training": {"vram_mb": 10000, "ram_mb": 15000}},
        seconds_per_update=2.5,
        setup_seconds=100.0,
        seconds_per_preview=6.0,
        source="probe",
    )
    measurements.record(
        hub,
        key,
        preset="zimage-turbo@1",
        profile="q8",
        device="Test GPU",
        phase_peaks={
            "training": {"vram_mb": 9500, "ram_mb": 16000},
            "previews": {"vram_mb": 12000, "ram_mb": None},
        },
        seconds_per_update=2.0,
        setup_seconds=None,
        seconds_per_preview=None,
        source="run",
    )
    row = measurements.read(hub, key)
    assert row["phase_peaks"] == {
        "training": {"vram_mb": 10000, "ram_mb": 16000},
        "previews": {"vram_mb": 12000, "ram_mb": None},
    }
    # The newer speed, and the older timings it had no figure for.
    assert row["seconds_per_update"] == 2.0
    assert (row["setup_seconds"], row["seconds_per_preview"]) == (100.0, 6.0)
    assert row["source"] == "run"
    with pytest.raises(ValueError):
        measurements.record(
            hub,
            key,
            preset="zimage-turbo@1",
            profile="q8",
            device=None,
            phase_peaks={},
            seconds_per_update=None,
            setup_seconds=None,
            seconds_per_preview=None,
            source="guess",
        )


def test_make_lookup_answers_the_fingerprint_and_its_row(hub):
    lookup = measurements.make_lookup(hub, _ZIT, commit="abc123", device="Test GPU")
    key, row = lookup(_values())
    assert key == _key() and row is None
    measurements.record(
        hub,
        key,
        preset="zimage-turbo@1",
        profile="q8",
        device="Test GPU",
        phase_peaks={"training": {"vram_mb": 1, "ram_mb": 1}},
        seconds_per_update=None,
        setup_seconds=None,
        seconds_per_preview=None,
        source="probe",
    )
    assert lookup(_values())[1]["profile"] == "q8"
