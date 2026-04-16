"""Caption, tag, and hidden-tag processing utilities."""

import json

from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models.tag import TAG_EMPTY_SENTINEL
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.caption_file_utils import write_caption_file

_logger = get_logger(__name__)


class CaptionUtils:
    """Utility methods for building caption and tag strings from pictures."""

    @staticmethod
    def _build_tag_caption(picture, tag_format: str = "spaces") -> str:
        """Build a comma-separated tag string from a picture's tags.

        Args:
            picture: Picture ORM object with a ``tags`` relationship.
            tag_format: ``"spaces"`` (default) keeps tags as-is;
                ``"underscores"`` replaces spaces with underscores.
        """
        tags = []
        for tag in getattr(picture, "tags", []) or []:
            tag_value = getattr(tag, "tag", None)
            if tag_value in (None, TAG_EMPTY_SENTINEL):
                continue
            if tag_format == "underscores":
                tag_value = tag_value.replace(" ", "_")
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


def sync_picture_sidecar(server, pic_id: int) -> list[dict]:
    """Write current tags + description back to the caption sidecar file.

    Fetches all required data inside a single DB task so no detached-instance
    attribute access is needed by the caller.  Early-exits when the picture has
    no caption file or when the owning reference folder does not have
    ``sync_captions`` enabled.  Only updates *existing* caption files — never
    creates new ones.  Persists the new mtime so the next folder scan does not
    re-import the write-back as an external change.

    Args:
        server: The Server instance providing vault/db access.
        pic_id: Primary key of the Picture.

    Returns:
        List of ``{"id": ..., "tag": ...}`` dicts for all non-sentinel tags.
    """
    # Import here to avoid circular imports between db_models and utils.
    from pixlstash.db_models import Picture, Tag
    from pixlstash.db_models.reference_folder import ReferenceFolder

    def _do_sync(session: Session, _pic_id: int) -> list[dict]:
        pic_db = session.get(Picture, _pic_id)
        if pic_db is None:
            return []
        # Fetch tags via explicit query — avoids triggering the lazy relationship
        # on pic_db (which would interact with cascade="all, delete-orphan").
        tag_rows = session.exec(select(Tag).where(Tag.picture_id == _pic_id)).all()
        current_tags = [
            t.tag for t in tag_rows if t.tag and t.tag != TAG_EMPTY_SENTINEL
        ]
        fresh_tags = [
            {"id": t.id, "tag": t.tag}
            for t in tag_rows
            if t.tag and t.tag != TAG_EMPTY_SENTINEL
        ]

        if pic_db.reference_folder_id and pic_db.caption_file:
            rf = session.get(ReferenceFolder, pic_db.reference_folder_id)
            if rf is not None and rf.sync_captions:
                new_mtime = write_caption_file(
                    pic_db.caption_file, current_tags, pic_db.description
                )
                if new_mtime is not None:
                    pic_db.caption_file_mtime = new_mtime
                    session.add(pic_db)
                    session.commit()

        return fresh_tags

    try:
        return server.vault.db.run_task(_do_sync, pic_id, priority=DBPriority.IMMEDIATE)
    except Exception as exc:
        _logger.warning("Sidecar write-back failed for picture %d: %s", pic_id, exc)
        return []
