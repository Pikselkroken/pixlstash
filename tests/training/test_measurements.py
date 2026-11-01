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
        _key(_values(**{"train.optimizer": "adamw"})),
        _key(_values(**{"train.train_text_encoder": True})),
        _key(_values(**{"network.conv": 8})),
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


def test_keeping_the_description_generic_is_a_setup_only_while_on():
    """It slows every step, so it is measured apart; off, a run keeps the key
    it was measured under before the switch existed."""
    base = _key()
    assert _key(_values(**{"train.diff_output_preservation": False})) == base
    assert _key(_values(**{"train.diff_output_preservation": True})) != base


def test_a_setup_measured_by_polling_alone_is_measured_again(monkeypatch):
    """Polls missed a preview's VAE decode, so a measurement taken that way
    must not admit a run: reading the allocator's peaks is a new setup."""
    base = _key()
    monkeypatch.setattr(measurements, "MEASURED_BY", "polls")
    assert _key() != base


def test_a_clip_recipe_fingerprints_its_sound_and_preview_length():
    """Sound and preview length move a motion run's memory; a recipe that
    resolves neither keeps the key it was measured under."""
    motion = presets.get_preset("minimax-h3-motion")
    values = presets.resolved_values(
        presets.resolve(
            motion,
            subject="person",
            effort="standard",
            fit="auto",
            picture_count=24,
            vram_mb=100_000,
            ram_mb=None,
        )
    )

    def key(**changes):
        return measurements.fingerprint(
            motion, {**values, **changes}, commit="abc123", device="Test GPU"
        )

    assert key(**{"datasets[*].do_audio": False}) != key()
    assert key(**{"sample.num_frames": 107}) != key()
    assert "datasets[*].do_audio" not in _values()


def test_peaks_keep_the_highest_figure_per_phase():
    peaks: dict = {}
    measurements.note_peak(peaks, "training", 9000, 12000)
    measurements.note_peak(peaks, "training", 8000, None)
    measurements.note_peak(peaks, "training", None, 13000)
    measurements.note_peak(peaks, None, 99999, 99999)
    assert peaks == {"training": {"vram_mb": 9000, "ram_mb": 13000}}


def test_a_spike_freed_between_two_polls_still_reaches_the_peak(tmp_path, monkeypatch):
    """A preview's VAE decode is freed before the next poll; the trainer's
    allocator lines carry it, read once each, a half-written line left for
    the next read."""
    monkeypatch.setattr(measurements, "process_tree", lambda pid: [])
    monkeypatch.setattr(measurements, "rss_mb", lambda tree: None)
    monkeypatch.setattr(measurements, "gpu_mb", lambda pids, device: 20_000)
    tree = {4242}
    log = tmp_path / "4242.log"
    log.write_text("19000 19000\n24200 19000\n19100 19100\n19500 1")
    offsets: dict = {}

    assert measurements.allocator_overshoot_mb(str(tmp_path), tree, offsets) == 5100
    # Read once: the same lines are not counted again.
    assert measurements.allocator_overshoot_mb(str(tmp_path), tree, offsets) == 0
    with log.open("a") as out:
        out.write("9500\n")
    # The finished line is whole now; nothing rose above what is held.
    assert measurements.allocator_overshoot_mb(str(tmp_path), tree, offsets) == 0
    # A process that never had a CUDA context wrote nothing.
    assert measurements.allocator_overshoot_mb(str(tmp_path), {77}, {}) == 0

    monkeypatch.setattr(
        measurements, "process_tree", lambda pid: [type("P", (), {"pid": 4242})()]
    )
    with log.open("a") as out:
        out.write("25000 19500\n")
    usage = measurements.trainer_usage(4242, "GPU-test", str(tmp_path), offsets)
    assert (usage["vram_mb"], usage["peak_vram_mb"]) == (20_000, 25_500)
    # Without a folder the poll is all there is.
    usage = measurements.trainer_usage(4242, "GPU-test")
    assert usage["peak_vram_mb"] == 20_000
    # Lines written while NVML had no figure are read then, not credited later.
    with log.open("a") as out:
        out.write("26000 19500\n")
    monkeypatch.setattr(measurements, "gpu_mb", lambda pids, device: None)
    usage = measurements.trainer_usage(4242, "GPU-test", str(tmp_path), offsets)
    assert usage["peak_vram_mb"] is None
    monkeypatch.setattr(measurements, "gpu_mb", lambda pids, device: 20_000)
    usage = measurements.trainer_usage(4242, "GPU-test", str(tmp_path), offsets)
    assert usage["peak_vram_mb"] == 20_000


def test_a_trainer_on_the_gpu_without_peaks_is_named_once(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(
        measurements, "process_tree", lambda pid: [type("P", (), {"pid": 4242})()]
    )
    monkeypatch.setattr(measurements, "rss_mb", lambda tree: None)
    monkeypatch.setattr(measurements, "gpu_mb", lambda pids, device: 20_000)
    for _ in range(5):
        measurements.trainer_usage(4242, "GPU-test", str(tmp_path), {})
    assert caplog.text.count("writes no allocator peaks") == 1
    # Once the trainer is gone its count is dropped, not kept for the next run.
    monkeypatch.setattr(measurements, "process_tree", lambda pid: [])
    monkeypatch.setattr(measurements, "gpu_mb", lambda pids, device: None)
    measurements.trainer_usage(4242, "GPU-test", str(tmp_path), {})
    assert str(tmp_path) not in measurements._unhooked
    # A folder without offsets would re-read every line on every poll.
    with pytest.raises(ValueError):
        measurements.trainer_usage(4242, "GPU-test", str(tmp_path))


def test_a_run_watched_from_its_middle_skips_the_peaks_before(tmp_path):
    (tmp_path / "4242.log").write_text("30000 19000\n19000 19000\n190")
    (tmp_path / "notes.txt").write_text("not a trainer's\n")
    offsets = measurements.peaks_read_to_end(str(tmp_path))
    assert offsets == {4242: len("30000 19000\n19000 19000\n")}
    assert measurements.allocator_overshoot_mb(str(tmp_path), {4242}, offsets) == 0
    assert measurements.peaks_read_to_end(str(tmp_path / "missing")) == {}


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
