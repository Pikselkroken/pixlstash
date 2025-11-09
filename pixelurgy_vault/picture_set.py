from dataclasses import dataclass, field
from typing import Self, Union
import sqlite3


@dataclass
class PictureSetModel:
    """
    Database model for the picture_sets table.
    A picture set is a named collection of pictures.
    """

    __tablename__ = "picture_sets"
    id: int = field(default=None, metadata={"primary_key": True, "autoincrement": True})
    name: str = field(default=None, metadata={"index": True})
    description: str = field(default=None)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, row: Union[dict, sqlite3.Row]) -> Self:
        assert isinstance(row, dict) or isinstance(row, sqlite3.Row)
        return cls(
            id=row["id"] if "id" in row.keys() else None,
            name=row["name"] if "name" in row.keys() else None,
            description=row["description"] if "description" in row.keys() else None,
        )


@dataclass
class PictureSetMemberModel:
    """
    Database model for the picture_set_members table.
    Many-to-many junction table between picture_sets and pictures.
    """

    __tablename__ = "picture_set_members"
    set_id: int = field(
        default=None,
        metadata={
            "foreign_key": "picture_sets(id)",
            "composite_key": True,
            "index": True,
        },
    )
    picture_id: str = field(
        default=None,
        metadata={
            "foreign_key": "pictures(id)",
            "composite_key": True,
            "index": True,
        },
    )

    def to_dict(self) -> dict:
        return {
            "set_id": self.set_id,
            "picture_id": self.picture_id,
        }

    @classmethod
    def from_dict(cls, row: Union[dict, sqlite3.Row]) -> Self:
        assert isinstance(row, dict) or isinstance(row, sqlite3.Row)
        return cls(
            set_id=row["set_id"] if "set_id" in row.keys() else None,
            picture_id=row["picture_id"] if "picture_id" in row.keys() else None,
        )
