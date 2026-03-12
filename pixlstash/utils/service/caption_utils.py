"""Caption, tag, and hidden-tag processing utilities."""

import json

from pixlstash.db_models.tag import TAG_EMPTY_SENTINEL


class CaptionUtils:
    """Utility methods for building caption and tag strings from pictures."""

    @staticmethod
    def _build_tag_caption(picture) -> str:
        """Build a comma-separated tag string from a picture's tags."""
        tags = []
        for tag in getattr(picture, "tags", []) or []:
            tag_value = getattr(tag, "tag", None)
            if tag_value in (None, TAG_EMPTY_SENTINEL):
                continue
            tags.append(tag_value)
        return ", ".join(tags)

    @staticmethod
    def _build_character_caption(picture) -> str:
        """Build a comma-separated character name string from a picture's characters."""
        character_names = []
        for character in getattr(picture, "characters", []) or []:
            name_value = getattr(character, "name", None)
            if name_value:
                character_names.append(name_value)
        return ", ".join(character_names)


def serialize_tag_objects(tags: list | None, empty_sentinel: str = "") -> list[dict]:
    """Serialise a list of Tag ORM objects to plain dicts with id and tag fields."""
    items = []
    for tag in tags or []:
        if not tag or getattr(tag, "tag", None) in (None, empty_sentinel):
            continue
        items.append({"id": getattr(tag, "id", None), "tag": tag.tag})
    return items


def _normalize_hidden_tags(value):
    """Parse and normalise a hidden-tags value to a lowercase de-duped list.

    Accepts a JSON string, list, or dict (keys used as tags).
    Returns an empty list for None/empty, None for unparseable input.
    """
    if value is None:
        return []

    if isinstance(value, str):
        try:
            tags = json.loads(value)
        except Exception:
            return None
    else:
        tags = value

    if isinstance(tags, dict):
        tags = list(tags.keys())
    if not isinstance(tags, list):
        return None

    cleaned = []
    seen = set()
    for tag in tags:
        if tag is None:
            continue
        clean = str(tag).strip().lower()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        cleaned.append(clean)
    return cleaned
