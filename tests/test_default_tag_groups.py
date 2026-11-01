"""The shipped default tag groups file: shape, order, anomaly and NSFW guarantees.

Reads the committed JSON only; no Server, so it runs in well under a second.
``DEFAULTS_PATH`` is read at call time so a mutation check can point it at a copy.
"""

import importlib.util
import json
from pathlib import Path

from pixlstash.services.tag_group_defaults import (
    ANOMALY,
    DEFAULT_GROUP_NAMES,
    anomaly_tags,
    normalise_tag,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULTS_PATH = (
    REPO_ROOT / "pixlstash" / "data" / "tag_groups" / "default_tag_groups.json"
)
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_default_tag_groups.py"

# Explicit NSFW tags. Each must be absent from every group (Anomaly included).
NSFW_SAMPLE = (
    "nude",
    "naked",
    "topless",
    "bottomless",
    "cum",
    "nipples",
    "pussy",
    "sex",
    "fellatio",
    "spread legs",
    "penis",
    "vaginal",
    "masturbation",
    "bondage",
    "loli",
    "futanari",
    "pubic hair",
    "creampie",
    "breasts exposed",
    "naked apron",
)

# Names the toolkit uses for its fallback categories. None may be a stored tag.
FALLBACK_NAMES = frozenset(
    {
        "anime character",
        "artist",
        "artwork",
        "environment",
        "expressions & actions",
        "items",
        "person",
        "scene",
        "uncategorized",
    }
)


def _load() -> dict:
    with DEFAULTS_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _groups() -> dict[str, list[str]]:
    return {group["name"]: group["tags"] for group in _load()["groups"]}


def _nsfw_words():
    spec = importlib.util.spec_from_file_location(
        "build_default_tag_groups", BUILD_SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.NSFW_WORDS


def test_json_parses_with_source_and_groups():
    data = _load()
    assert isinstance(data["source"], str) and data["source"]
    assert isinstance(data["groups"], list)


def test_group_names_and_order_match_defaults():
    names = [group["name"] for group in _load()["groups"]]
    assert names == list(DEFAULT_GROUP_NAMES)


def test_version_is_a_positive_int():
    version = _load()["version"]
    assert isinstance(version, int) and not isinstance(version, bool)
    assert version > 0


def test_every_group_is_present_and_sorted():
    for group in _load()["groups"]:
        assert isinstance(group["tags"], list), group["name"]
        assert group["tags"] == sorted(group["tags"]), group["name"]


def test_no_tag_is_in_two_groups():
    seen: dict[str, str] = {}
    for name, tags in _groups().items():
        for tag in tags:
            assert tag not in seen, f"{tag!r} is in {seen[tag]!r} and {name!r}"
            seen[tag] = name


def test_every_tag_is_stored_in_normal_form():
    for name, tags in _groups().items():
        for tag in tags:
            assert normalise_tag(tag) == tag, f"{name!r} holds {tag!r}"


def test_anomaly_holds_every_known_anomaly_tag():
    anomaly = set(_groups()[ANOMALY])
    missing = sorted(t for t in anomaly_tags() if t not in anomaly)
    assert not missing, missing[:10]


def test_no_known_anomaly_tag_sits_in_another_group():
    groups = _groups()
    for name, tags in groups.items():
        if name == ANOMALY:
            continue
        stray = sorted(set(tags) & anomaly_tags())
        assert not stray, (name, stray[:10])


def test_no_fallback_category_name_is_a_tag():
    for name, tags in _groups().items():
        bad = [t for t in tags if t.casefold() in FALLBACK_NAMES]
        assert not bad, (name, bad)


def test_nsfw_sample_is_in_no_group():
    everything = {tag for tags in _groups().values() for tag in tags}
    present = sorted(set(NSFW_SAMPLE) & everything)
    assert not present, present


def test_nsfw_keywords_are_in_no_non_anomaly_group():
    words = _nsfw_words()
    for name, tags in _groups().items():
        if name == ANOMALY:
            continue
        hits = [t for t in tags if words.search(t)]
        assert not hits, (name, hits[:10])
