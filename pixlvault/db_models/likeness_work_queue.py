from sqlmodel import SQLModel, Field
from typing import Optional


class LikenessWorkQueue(SQLModel, table=True):
    """
    Work queue for pending likeness calculations between picture pairs.
    Each row represents a pair of pictures to process.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    picture_id_a: str = Field(
        index=True, nullable=False, foreign_key="picture.id", ondelete="CASCADE"
    )
    picture_id_b: str = Field(
        index=True, nullable=False, foreign_key="picture.id", ondelete="CASCADE"
    )
    # Optionally, add timestamp or priority fields if needed
    # created_at: Optional[str] = Field(default=None, index=True)
    # priority: Optional[int] = Field(default=0, index=True)
