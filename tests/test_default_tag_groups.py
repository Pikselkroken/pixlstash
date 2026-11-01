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
    # Every tag the review of issue #2469 dropped from the defaults (sexual,
    # fetish, nudity, minors in a suggestive context, explicit gore or torture).
    "abuse",
    "adjusting panties",
    "age difference",
    "age regression",
    "aged down",
    "ahegao",
    "apron lift",
    "arm between legs",
    "arms between legs",
    "aroused",
    "asphyxiation",
    "ass",
    "babydoll",
    "bara",
    "belt collar",
    "between legs",
    "between thighs",
    "bikini bottom aside",
    "bikini bottom only",
    "bikini top only",
    "black babydoll",
    "black garter belt",
    "black garter straps",
    "bodystocking",
    "boobplate",
    "bouncing breasts",
    "boy sandwich",
    "bra peek",
    "breast expansion",
    "breasts apart",
    "bridal lingerie",
    "bursting breasts",
    "buruma aside",
    "buttocks",
    "cat lingerie",
    "chain leash",
    "chained",
    "chained wrists",
    "chest harness",
    "chikan",
    "child on child",
    "cleavage cutout",
    "clothes down",
    "clothes grab",
    "clothes lift",
    "clothes lifted",
    "clothes pull",
    "clothes theft",
    "clothing aside",
    "condom belt",
    "corpse",
    "covering ass",
    "covering breasts",
    "covering crotch",
    "covering one breast",
    "covering privates",
    "cream on face",
    "cross pasties",
    "crotch plate",
    "crotch seam",
    "crotch zipper",
    "decapitation",
    "diaper",
    "dimples of venus",
    "disembodied head",
    "dissolving clothes",
    "dominatrix",
    "downblouse",
    "dress lift",
    "dress pull",
    "exhibitionism",
    "exploding clothes",
    "eyepatch bikini",
    "female pervert",
    "finger in another's mouth",
    "finger in own mouth",
    "flat ass",
    "flat chest grab",
    "food on body",
    "food on breasts",
    "foot worship",
    "frontless outfit",
    "frottage",
    "g-string",
    "garter belt",
    "garter straps",
    "giantess",
    "girl sandwich",
    "gokkun",
    "guro",
    "gusset",
    "hand between legs",
    "hand on own ass",
    "hand under clothes",
    "hand under shirt",
    "handcuffs",
    "hands on own ass",
    "hands on own breasts",
    "headless",
    "heart pasties",
    "heavy breathing",
    "hickey",
    "highleg bikini",
    "highleg panties",
    "hogtie",
    "holding bra",
    "holding panties",
    "holding underwear",
    "hoodie lift",
    "huge ass",
    "human scabbard",
    "impaled",
    "impossible bodysuit",
    "impossible clothes",
    "impossible dress",
    "impossible leotard",
    "impossible shirt",
    "impossible swimsuit",
    "intestines",
    "jacket lift",
    "jacket pull",
    "jockstrap",
    "josou seme",
    "kimono lift",
    "kimono pull",
    "latex",
    "latex bodysuit",
    "latex legwear",
    "leash",
    "leash pull",
    "leg between thighs",
    "legwear garter",
    "leotard aside",
    "leotard peek",
    "leotard pull",
    "lingerie",
    "lowleg bikini",
    "lowleg panties",
    "m legs",
    "maid bikini",
    "male playboy bunny",
    "male underwear peek",
    "masochism",
    "mesugaki",
    "micro bikini",
    "micro bra",
    "micro panties",
    "micro shorts",
    "microskirt",
    "moaning",
    "mole on ass",
    "mouth drool",
    "naughty face",
    "negligee",
    "nippleless clothes",
    "no pants",
    "no shirt",
    "nontraditional playboy bunny",
    "nudist",
    "o-ring bikini",
    "o-ring bottom",
    "o-ring swimsuit",
    "o-ring top",
    "one-piece swimsuit pull",
    "one-piece thong",
    "open bra",
    "open clothes",
    "open fly",
    "panties around one leg",
    "panties on head",
    "pants pull",
    "panty straps",
    "pantylines",
    "partially unbuttoned",
    "partially unzipped",
    "pasties",
    "pervert",
    "pet play",
    "phallic symbol",
    "playboy bunny",
    "pole dancing",
    "presenting foot",
    "prostitution",
    "public indecency",
    "pulling another's clothes",
    "reverse bunnysuit",
    "riding crop",
    "ryona",
    "sadism",
    "sailor collar lift",
    "saliva drip",
    "saliva trail",
    "sandwiched",
    "seductive smile",
    "see-through",
    "see-through cleavage",
    "see-through dress",
    "see-through jacket",
    "see-through legwear",
    "see-through leotard",
    "see-through shirt",
    "see-through silhouette",
    "see-through skirt",
    "see-through sleeves",
    "see-through swimsuit",
    "self-harm",
    "severed head",
    "severed limb",
    "shackles",
    "shirt lift",
    "shirt lifted",
    "shirt pull",
    "shorts around one leg",
    "shorts pull",
    "single garter strap",
    "size difference",
    "skirt around one leg",
    "skirt flip",
    "skirt pull",
    "slingshot swimsuit",
    "spanked",
    "sperm cell",
    "stained sheets",
    "star pasties",
    "stomach bulge",
    "straitjacket",
    "strangling",
    "strap between breasts",
    "strap lift",
    "strap slip",
    "strap slipping",
    "string bikini",
    "string bra",
    "string panties",
    "suicide",
    "suspension",
    "swastika",
    "sweater lift",
    "sweater pull",
    "swimsuit aside",
    "teacher and student",
    "tearing clothes",
    "thong",
    "thong bikini",
    "thong leotard",
    "tied up (nonsexual)",
    "tight underwear",
    "toe cleavage",
    "toilet use",
    "torn bodystocking",
    "torture",
    "under covers",
    "underboob",
    "underboob cutout",
    "unworn bikini bottom",
    "unworn bikini top",
    "unworn bra",
    "upshirt",
    "upshorts",
    "virgin destroyer sweater",
    "virgin killer outfit",
    "virgin killer sweater",
    "voyeurism",
    "wardrobe malfunction",
    "wearing only a bikini top",
    "whip marks",
    "white garter belt",
    "white garter straps",
    "x-ray",
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


def test_the_guard_words_match_their_phrases_and_not_their_neighbours():
    words = _nsfw_words()
    for phrase in (
        "one-piece thong",
        "g-string",
        "underboob",
        "sperm cell",
        "hand on own ass",
        "torture",
        "sadism",
        "masochism",
        "child on child",
        "on child",
    ):
        assert words.search(phrase), phrase
    # Whole words only, and a minor on its own is not a sexual phrase.
    for neighbour in ("class", "glass", "passage", "butterfly", "holding child"):
        assert not words.search(neighbour), neighbour
