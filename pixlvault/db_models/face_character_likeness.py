from sqlalchemy import Column, Integer, ForeignKey, text
from sqlmodel import (
    SQLModel,
    Field,
    Session,
)


class FaceCharacterLikeness(SQLModel, table=True):
    """
    Database model for the face_character_likeness table.
    Stores likeness scores for each (Face, Character) combination.
    """

    face_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("face.id", ondelete="CASCADE"), primary_key=True
        )
    )
    character_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("character.id", ondelete="CASCADE"), primary_key=True
        )
    )
    likeness: float = Field(default=None, index=True)
    metric: str = Field(default=None)

    @classmethod
    def bulk_insert_ignore(cls, session: Session, likeness_results):
        """
        Bulk insert FaceCharacterLikeness rows, ignoring duplicates (SQLite only).
        likeness_results: list of FaceCharacterLikeness objects or dicts.
        """
        if not likeness_results:
            return 0

        # Prepare rows as dicts
        rows = [
            {
                "face_id": r.face_id if hasattr(r, "face_id") else r["face_id"],
                "character_id": r.character_id
                if hasattr(r, "character_id")
                else r["character_id"],
                "likeness": r.likeness if hasattr(r, "likeness") else r["likeness"],
                "metric": r.metric if hasattr(r, "metric") else r["metric"],
            }
            for r in likeness_results
        ]

        result = session.exec(
            text("""
                INSERT OR IGNORE INTO facecharacterlikeness (face_id, character_id, likeness, metric)
                VALUES (:face_id, :character_id, :likeness, :metric)
            """),
            params=rows,
        )
        session.commit()
        return result.rowcount
