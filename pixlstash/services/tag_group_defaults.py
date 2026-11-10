"""The tag groups a library starts with, and how a Danbooru tag database maps onto them.

Two things share one mapping so they can never disagree:

* the **defaults** every library is seeded with (``pixlstash/data/tag_groups/
  default_tag_groups.json``, built from the taggers' vocabularies and the MIT
  ComfyUI-Danbooru-Tag-Toolkit database), and
* an **import** of a Danbooru tag file (``english`` / ``category`` /
  ``subcategory`` columns, English names in ``category_en`` / ``subcategory_en``
  when present), which files each tag the way the defaults would.

A tag goes in exactly one group. Anomaly comes first and wins over any semantic
group: a defect tag is filed as a defect even where the database calls it
clothing or a body part.

Adult subcategories have two uses, and the mapping serves both. The shipped
defaults are SFW only (the build script drops adult rows, and its keyword guard
drops explicit tags), so the build passes no adult group and an adult row gives no
group. An import files the user's own file as given: an adult row goes into the
:data:`ADULT_GROUP` ("Adult") group, which is created on demand and is never a
default group. The filter exists so PixlStash never distributes such tags, not so
a user's library drops them.
"""

import re
from functools import lru_cache
from typing import Optional

from pixlstash.db_models.tag import (
    DEFAULT_SMART_SCORE_PENALIZED_TAGS,
    DEFAULT_TAG_MERGES,
)

ANOMALY = "Anomaly"
CHARACTER = "Character"
COPYRIGHT = "Copyright"
ARTIST = "Artist"

# Rail order. The names are the groups' names in every new library.
DEFAULT_GROUP_NAMES: tuple[str, ...] = (
    ANOMALY,
    "People",
    CHARACTER,
    COPYRIGHT,
    ARTIST,
    "Hair",
    "Face & expression",
    "Body",
    "Clothing",
    "Accessories",
    "Pose & action",
    "Setting",
    "Objects",
    "Animals",
    "Composition",
    "Style & meta",
)

# Returned by :func:`group_for` for an adult subcategory when no adult group is
# asked for: the defaults build leaves such a row ungrouped.
ADULT = None
# The group an import files an adult subcategory under. Not a default group: it is
# created by an import that needs it, like any other, and never seeded.
ADULT_GROUP = "Adult"

# (category, subcategory), English names as the toolkit's ``*_en`` columns spell
# them, onto a default group, or ADULT.
SUBCATEGORY_GROUPS: dict[tuple[str, str], Optional[str]] = {
    ("Person", "Subject"): "People",
    ("Person", "Identity / Occupation"): "People",
    ("Person", "Age"): "People",
    ("Person", "Hairstyles"): "Hair",
    ("Person", "Face"): "Face & expression",
    ("Person", "Face Shape"): "Face & expression",
    ("Person", "Eyebrows"): "Face & expression",
    ("Person", "Eyes"): "Face & expression",
    ("Person", "Pupils"): "Face & expression",
    ("Person", "Nose"): "Face & expression",
    ("Person", "Mouth"): "Face & expression",
    ("Person", "Teeth"): "Face & expression",
    ("Person", "Tongue"): "Face & expression",
    ("Person", "Skin"): "Body",
    ("Person", "Body Type"): "Body",
    ("Person", "Nails"): "Body",
    ("Person", "Legs"): "Body",
    ("Person", "Abdomen"): "Body",
    ("Person", "Shoulders"): "Body",
    ("Person", "Waist"): "Body",
    ("Person", "Wings"): "Body",
    ("Person", "Chest"): ADULT,
    ("Person", "Genitals"): ADULT,
    ("Expressions & Actions", "Smiling"): "Face & expression",
    ("Expressions & Actions", "Unhappy"): "Face & expression",
    ("Expressions & Actions", "Crying"): "Face & expression",
    ("Expressions & Actions", "Angry"): "Face & expression",
    ("Expressions & Actions", "Disdain"): "Face & expression",
    ("Expressions & Actions", "Other Expressions"): "Face & expression",
    ("Expressions & Actions", "Emoticons"): "Face & expression",
    ("Camera", "Character Gaze Direction"): "Face & expression",
    ("Expressions & Actions", "Basic Actions"): "Pose & action",
    ("Expressions & Actions", "Hand Gestures"): "Pose & action",
    ("Expressions & Actions", "Holding Something"): "Pose & action",
    ("Expressions & Actions", "Grabbing Something"): "Pose & action",
    ("Expressions & Actions", "Hand Placement"): "Pose & action",
    ("Expressions & Actions", "Leg Actions"): "Pose & action",
    ("Expressions & Actions", "Other Actions"): "Pose & action",
    ("Expressions & Actions", "Sexual Actions"): ADULT,
    ("Clothing", "Upper-body Clothing"): "Clothing",
    ("Clothing", "Skirts"): "Clothing",
    ("Clothing", "Pants"): "Clothing",
    ("Clothing", "Socks"): "Clothing",
    ("Clothing", "Shoes"): "Clothing",
    ("Clothing", "Clothing Styles"): "Clothing",
    ("Clothing", "Clothing Patterns"): "Clothing",
    ("Clothing", "Clothing Sets"): "Clothing",
    ("Clothing", "Formal Wear"): "Clothing",
    ("Clothing", "Clothing Decorations"): "Clothing",
    ("Clothing", "Scarves"): "Clothing",
    ("Clothing", "Head Accessories"): "Accessories",
    ("Clothing", "Face Accessories"): "Accessories",
    ("Clothing", "Hand Accessories"): "Accessories",
    ("Clothing", "Leg Accessories"): "Accessories",
    ("Clothing", "Other Accessories"): "Accessories",
    ("Scene", "Indoors"): "Setting",
    ("Scene", "Outdoors"): "Setting",
    ("Scene", "City"): "Setting",
    ("Environment", "Nature"): "Setting",
    ("Environment", "Sky"): "Setting",
    ("Environment", "Water"): "Setting",
    ("Environment", "Weather"): "Setting",
    ("Environment", "Seasons"): "Setting",
    ("Environment", "Atmosphere"): "Setting",
    ("Environment", "Clouds"): "Setting",
    ("Artwork", "Background"): "Setting",
    ("Items", "Other Items"): "Objects",
    ("Items", "Food"): "Objects",
    ("Items", "Weapons"): "Objects",
    ("Items", "Digital Devices"): "Objects",
    ("Items", "Plants"): "Objects",
    ("Items", "Utensils"): "Objects",
    ("Items", "Musical Instruments"): "Objects",
    ("Items", "School Supplies"): "Objects",
    ("Items", "Animals"): "Animals",
    ("Items", "Adult Toys"): ADULT,
    ("Camera", "Character Framing"): "Composition",
    ("Camera", "Camera Angle"): "Composition",
    ("Camera", "Close-up Shot"): "Composition",
    ("Camera", "Other Framing"): "Composition",
    ("Camera", "Effects"): "Composition",
    ("Artwork", "Image Quality"): "Style & meta",
    ("Artwork", "Artwork Type"): "Style & meta",
    ("Artwork", "Colors"): "Style & meta",
    ("Artwork", "Art Style"): "Style & meta",
    ("Artwork", "Lighting"): "Style & meta",
    ("Artwork", "Brushwork"): "Style & meta",
    ("Artwork", "Art Genre"): "Style & meta",
    ("Artwork", "Artist Style"): "Style & meta",
}

# A whole category, any subcategory, onto a default group. Characters and artists
# carry no subcategory in the toolkit (or a stray one), so they are keyed by category.
CATEGORY_GROUPS: dict[str, str] = {
    "Anime Character": CHARACTER,
    "Artist": ARTIST,
}

# A character's trailing parenthetical names its franchise ("raiden shogun (genshin
# impact)"). These parentheticals name a form or an age, not a franchise, so they
# never become a Copyright tag. Keep this list short and explicit.
COPYRIGHT_QUALIFIERS: frozenset[str] = frozenset(
    {
        "1st costume",
        "2nd costume",
        "3rd costume",
        "adult",
        "alt",
        "alter",
        "alter ego",
        "alternate",
        "alternative",
        "append",
        "cat",
        "character",
        "chibi",
        "cosplay",
        "dangerous beast",
        "dog",
        "female",
        "full",
        "human",
        "male",
        "old",
        "young",
    }
)
_TRAILING_PARENTHETICAL = re.compile(r"\(([^()]*)\)\s*$")

# A database's own "no category" bucket is not a group. "Ungrouped" is the name
# the service gives a tag with no group, so a category of that name must not
# create a group of it.
UNGROUPED_CATEGORIES: frozenset[str] = frozenset(
    {"uncategorized", "uncategorised", "ungrouped"}
)

# Defect labels of the PixlStash tagger beyond the penalised and merged tags.
# Several also read as ordinary vocabulary ("noise", "cyclops"); in this tagger
# they are trained as generation defects, which is what a library filing them
# means.
TAGGER_DEFECT_TAGS: frozenset[str] = frozenset(
    {
        "absurd object",
        "awkward fingers",
        "bad clothing",
        "bad grip",
        "bad teeth",
        "context mismatch",
        "contorted limbs",
        "cutting fingers",
        "cyclops",
        "detached hand",
        "detached wire",
        "doppelganger",
        "duplicate objects",
        "elongated arm",
        "elongated hands",
        "elongated thumbs",
        "extra hand",
        "extra troe",
        "floating objects",
        "flux skin",
        "forked eye brows",
        "fused fingers",
        "fused jewelry",
        "inconsistent scene",
        "malformed arm",
        "malformed breast",
        "malformed face",
        "malformed legs",
        "malformed object",
        "malformed penis",
        "malformed text",
        "malformed tongue",
        "malformed vagina",
        "malplaced object",
        "mangled limbs",
        "mangled text",
        "meta:jpeg artifacts",
        "misplaced object",
        "misplaced objects",
        "oversharpened",
        "poor quality",
        "scene inconsistency",
        "time paradox",
        "twisted arm",
        "twisted hand",
        "unnatural arm",
        "unnatural breasts",
        "unnatural dog",
        "unnatural lighting",
        "unnatural object",
        "unnatural position",
        "unnatural smile",
        "unrealistic dog",
        "unrealistic muscles",
        "unrealistic object",
        "warped text",
        "watermarks",
        "weird breasts",
        "weird lighting",
        "weird object",
        "weird objects",
        "weird staring",
        "wrong grip",
    }
)


def normalise_tag(name: str) -> str:
    """A tag as PixlStash stores it: underscores as spaces, lower case, trimmed."""
    return " ".join(str(name or "").replace("_", " ").lower().split())


@lru_cache(maxsize=1)
def anomaly_tags() -> frozenset[str]:
    """Every tag the Anomaly group holds by default.

    The penalised defaults, both sides of the tag merges (a merged child is the
    same defect as its parent), and the tagger's other defect labels.
    """
    names = set(DEFAULT_SMART_SCORE_PENALIZED_TAGS)
    names.update(DEFAULT_TAG_MERGES)
    names.update(DEFAULT_TAG_MERGES.values())
    names.update(TAGGER_DEFECT_TAGS)
    return frozenset(normalise_tag(n) for n in names)


def group_for(
    tag: str,
    category: str,
    subcategory: str,
    *,
    adult_group: Optional[str] = None,
) -> Optional[str]:
    """The group a Danbooru database row files *tag* under.

    Args:
        tag: The row's tag, any spelling (normalised here).
        category: The row's category, English when the file has it.
        subcategory: The row's subcategory, likewise.
        adult_group: What an adult subcategory files under. ``None`` (the
            default, used by the defaults build) leaves it ungrouped; an import
            passes :data:`ADULT_GROUP`.

    Returns:
        ``"Anomaly"`` for a known defect; the mapped default group for a known
        (category, subcategory); *adult_group* for an adult subcategory; ``None``
        for a row with no category (empty, or "Uncategorized" / "Ungrouped" in any
        case); otherwise the file's own category name, so an import of a database
        with categories this mapping does not know still groups them.
    """
    if normalise_tag(tag) in anomaly_tags():
        return ANOMALY
    key = (str(category or "").strip(), str(subcategory or "").strip())
    if key in SUBCATEGORY_GROUPS:
        group = SUBCATEGORY_GROUPS[key]
        return adult_group if group is ADULT else group
    if key[0] in CATEGORY_GROUPS:
        return CATEGORY_GROUPS[key[0]]
    if key[0].casefold() in UNGROUPED_CATEGORIES:
        return None
    return key[0] or None


def franchise_of(tag: str) -> Optional[str]:
    """The Copyright tag a Character tag implies, or ``None``.

    Only the LAST parenthetical counts, and only when something precedes it:
    ``"raiden shogun (genshin impact)"`` gives ``"genshin impact"``. A qualifier in
    :data:`COPYRIGHT_QUALIFIERS` gives ``None``, as does a name that is itself an
    Anomaly tag. Used by the default build and by an import, so both agree.
    """
    name = normalise_tag(tag)
    match = _TRAILING_PARENTHETICAL.search(name)
    if match is None or not name[: match.start()].strip():
        return None
    franchise = normalise_tag(match.group(1))
    if not franchise or franchise in COPYRIGHT_QUALIFIERS:
        return None
    if franchise in anomaly_tags():
        return None
    return franchise
