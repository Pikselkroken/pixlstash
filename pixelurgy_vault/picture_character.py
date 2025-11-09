from dataclasses import dataclass, field
from typing import Self
import sqlite3
from typing import Union


@dataclass
class PictureCharacterModel:
    """
    Junction table model for many-to-many relationship between pictures and characters.
    """

    __tablename__ = "picture_characters"

    picture_id: str = field(
        default=None,
        metadata={"foreign_key": "pictures(id)", "index": True, "composite_key": True},
    )
    character_id: int = field(
        default=None,
        metadata={
            "foreign_key": "characters(id)",
            "index": True,
            "composite_key": True,
        },
    )

    __indexes__ = [
        {
            "fields": ["picture_id"],
            "name": "idx_picture_characters_picture_id",
        },
        {
            "fields": ["character_id"],
            "name": "idx_picture_characters_character_id",
        },
    ]

    def to_dict(self) -> dict:
        return {
            "picture_id": self.picture_id,
            "character_id": self.character_id,
        }

    @classmethod
    def from_dict(cls, row: Union[dict, sqlite3.Row]) -> Self:
        assert isinstance(row, dict) or isinstance(row, sqlite3.Row)
        return cls(
            picture_id=row["picture_id"],
            character_id=row["character_id"],
        )
