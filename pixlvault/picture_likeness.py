from dataclasses import dataclass, field
from typing import Self


@dataclass
class ReferencePictureLikenessModel:
    """
    Database model for the reference_picture_likeness table.
    Stores likeness scores for each (picture, reference_picture, face) combination.
    """

    __tablename__ = "reference_picture_likeness"
    picture_id: str = field(
        default=None, metadata={"foreign_key": "pictures(id)", "composite_key": True}
    )
    reference_picture_id: str = field(
        default=None, metadata={"foreign_key": "pictures(id)", "composite_key": True}
    )
    character_id: int = field(
        default=None, metadata={"foreign_key": "characters(id)", "composite_key": True}
    )
    face_index: int = field(default=None, metadata={"composite_key": True})
    likeness_score: float = field(default=None)

    @classmethod
    def from_dict(cls, row: dict) -> Self:
        return cls(
            picture_id=row["picture_id"],
            reference_picture_id=row["reference_picture_id"],
            character_id=row["character_id"],
            face_index=row.get("face_index", 0),
            likeness_score=row.get("likeness_score"),
        )

    def to_dict(self) -> dict:
        return {
            "picture_id": self.picture_id,
            "reference_picture_id": self.reference_picture_id,
            "character_id": self.character_id,
            "face_index": self.face_index,
            "likeness_score": self.likeness_score,
        }
