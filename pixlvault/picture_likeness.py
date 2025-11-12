from dataclasses import dataclass, field
from typing import Self


@dataclass
class PictureLikenessModel:
    """
    Database model for the picture_likeness table.
    Stores likeness scores for each (picture, picture, face) combination.
    """

    __tablename__ = "picture_likeness"
    picture_id_a: str = field(
        default=None,
        metadata={"foreign_key": "pictures(id)", "composite_key": True, "index": True},
    )
    picture_id_b: str = field(
        default=None,
        metadata={"foreign_key": "pictures(id)", "composite_key": True, "index": True},
    )
    likeness: float = field(default=None)
    metric: str = field(default=None)

    @classmethod
    def from_dict(cls, row: dict) -> Self:
        return cls(
            picture_id_a=row["picture_id_a"],
            picture_id_b=row["picture_id_b"],
            likeness=row.get("likeness"),
            metric=row.get("metric"),
        )

    def to_dict(self) -> dict:
        return {
            "picture_id_a": self.picture_id_a,
            "picture_id_b": self.picture_id_b,
            "likeness": self.likeness,
            "metric": self.metric,
        }
