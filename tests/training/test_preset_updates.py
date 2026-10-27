"""Tests for training recipes published to and fetched from pixlstash.dev."""

from __future__ import annotations

import copy
import hashlib
import json
import os

import pytest

from pixlstash.training import preset_updates, presets

SITE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "website", "training-presets", "v1"
)


def test_published_site_matches_the_shipped_recipes():
    """The guardrail: run scripts/build_training_presets_site.py after editing
    a recipe, or the site serves an older one than the app ships."""
    expected = preset_updates.site_files()
    on_disk = sorted(os.listdir(SITE_DIR))
    assert on_disk == sorted(expected), "website/training-presets/v1 has drifted"
    for name, raw in expected.items():
        with open(os.path.join(SITE_DIR, name), "rb") as handle:
            assert handle.read() == raw, f"website/training-presets/v1/{name} drifted"


def test_every_shipped_recipe_passes_as_its_own_update():
    """A recipe published unchanged must be accepted by every client."""
    for pid, shipped in presets.shipped_presets().items():
        assert presets.validate_update(copy.deepcopy(shipped), shipped)["id"] == pid


def _shipped():
    return presets.shipped_presets()["zimage-turbo"]


def _newer(**changes):
    preset = copy.deepcopy(_shipped())
    preset["version"] = _shipped()["version"] + 1
    preset.update(changes)
    return preset


@pytest.mark.parametrize(
    "mutate",
    [
        # A model source, the family, the code it needs.
        lambda p: p["locked"].__setitem__("model.name_or_path", "someone/other-model"),
        lambda p: p["locked"].__setitem__(
            "model.assistant_lora_path", "x/y.safetensors"
        ),
        lambda p: p["locked"].pop("model.extras_name_or_path"),
        lambda p: p.__setitem__("requires_arch", "flux2"),
        lambda p: p.__setitem__("template", "other"),
        lambda p: p.__setitem__("divisibility", 8),
        # A key outside the schema, anywhere.
        lambda p: p.__setitem__("hooks", {"run": "x"}),
        lambda p: p["hardware_profiles"]["q8"].__setitem__("env", {"A": "1"}),
        lambda p: p["defaults"].__setitem__("model.name_or_path", "someone/other"),
        lambda p: p["effort"]["standard"].__setitem__("process.type", "x"),
        lambda p: p["preview"].__setitem__("other", {"model.arch": "x"}),
        # Wrong types the shipped files never carry.
        lambda p: p.__setitem__("effort", []),
        lambda p: p["hardware_profiles"]["q8"].__setitem__("vram_mb", "16000"),
        lambda p: p["effort"]["gentle"].__setitem__("max_steps", float("inf")),
        lambda p: p.__setitem__("version", "2"),
    ],
)
def test_an_update_may_change_values_never_sources_or_schema(mutate):
    preset = _newer()
    mutate(preset)
    with pytest.raises(presets.PresetError):
        presets.validate_update(preset, _shipped())


def test_an_update_may_change_values_and_profiles():
    preset = _newer()
    preset["effort"]["standard"]["train.lr"] = 0.00015
    preset["hardware_profiles"]["q8"]["vram_mb"] = 15_000
    assert presets.validate_update(preset, _shipped()) is preset


# ---------------------------------------------------------------------------
# check(): a fake pixlstash.dev
# ---------------------------------------------------------------------------


@pytest.fixture
def site(tmp_path, monkeypatch):
    """A cache dir and a fake site holding a newer Z-Image Turbo."""
    presets.use_cache_dir(str(tmp_path / "cache"))
    files: dict[str, bytes] = {}
    fetched: list[str] = []

    def publish(preset, **entry):
        raw = json.dumps(preset).encode()
        files[f"{preset['id']}.json"] = raw
        row = {
            "id": preset["id"],
            "version": preset["version"],
            "file": f"{preset['id']}.json",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "min_pixlstash": "1.13.0",
        }
        row.update(entry)
        files["index.json"] = json.dumps({"schema": 1, "presets": [row]}).encode()

    def fetch(name):
        fetched.append(name)
        if name not in files:
            raise preset_updates.FetchError(f"{name} answered 404")
        return files[name]

    monkeypatch.setattr(preset_updates, "_fetch", fetch)
    monkeypatch.setattr(preset_updates, "installed_version", lambda: "1.13.0")
    newer = _newer()
    newer["effort"]["standard"]["train.lr"] = 0.00015
    publish(newer)
    try:
        yield {"publish": publish, "files": files, "fetched": fetched, "newer": newer}
    finally:
        presets.use_cache_dir(None)


def test_a_newer_valid_recipe_replaces_the_shipped_one(site):
    result = preset_updates.check()
    assert result == {
        "checked": True,
        "updated": [{"id": "zimage-turbo", "label": "Z-Image Turbo", "version": 2}],
        "error": None,
    }
    in_use = presets.get_preset("zimage-turbo")
    assert in_use["version"] == 2
    assert in_use["effort"]["standard"]["train.lr"] == 0.00015
    # Kept beside the hub: a fresh read (the next start) still uses it.
    presets.reload()
    assert presets.get_preset("zimage-turbo")["version"] == 2
    # Checking again fetches the index only: version 2 is not above 2.
    site["fetched"].clear()
    assert preset_updates.check()["updated"] == []
    assert site["fetched"] == ["index.json"]


@pytest.mark.parametrize(
    "entry",
    [
        {"sha256": "0" * 64},
        {"min_pixlstash": "1.14.0"},
        {"version": 3},  # the file says 2
        {"id": "unknown-family"},
    ],
)
def test_a_recipe_that_does_not_verify_is_ignored(site, entry):
    site["publish"](site["newer"], **entry)
    assert preset_updates.check()["updated"] == []
    assert presets.get_preset("zimage-turbo")["version"] == 1


def test_a_recipe_that_changes_a_model_source_is_ignored(site):
    bad = copy.deepcopy(site["newer"])
    bad["locked"]["model.name_or_path"] = "someone/other-model"
    site["publish"](bad)
    assert preset_updates.check()["updated"] == []
    assert presets.get_preset("zimage-turbo")["locked"]["model.name_or_path"] == (
        "Tongyi-MAI/Z-Image-Turbo"
    )


def test_an_unreachable_site_changes_nothing_and_says_so(site):
    site["files"].clear()
    result = preset_updates.check()
    assert result["checked"] is True
    assert result["updated"] == []
    assert "pixlstash.dev" in result["error"]
    assert presets.get_preset("zimage-turbo")["version"] == 1


def test_the_automatic_check_runs_at_most_once_a_day(site):
    assert preset_updates.check(automatic=True)["checked"] is True
    site["fetched"].clear()
    assert preset_updates.check(automatic=True) == {
        "checked": False,
        "updated": [],
        "error": None,
    }
    assert site["fetched"] == []
    # The button always checks.
    assert preset_updates.check()["checked"] is True


def test_a_cached_recipe_no_newer_than_the_shipped_one_is_ignored(site):
    """After an app upgrade ships version 2, a cached version 2 is not used."""
    preset_updates.check()
    shipped = copy.deepcopy(_shipped())
    shipped["version"] = 2
    cached = os.path.join(presets.cache_dir(), "zimage-turbo.json")
    with open(cached, encoding="utf-8") as handle:
        assert json.load(handle)["version"] == 2
    assert presets._cached(shipped) is None


def test_a_tampered_cache_file_falls_back_to_the_shipped_recipe(site):
    preset_updates.check()
    cached = os.path.join(presets.cache_dir(), "zimage-turbo.json")
    with open(cached, encoding="utf-8") as handle:
        preset = json.load(handle)
    preset["locked"]["model.name_or_path"] = "someone/other-model"
    with open(cached, "w", encoding="utf-8") as handle:
        json.dump(preset, handle)
    presets.reload()
    assert presets.get_preset("zimage-turbo")["version"] == 1
