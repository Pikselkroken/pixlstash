"""Caption and tag processing utilities for picture export."""

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
