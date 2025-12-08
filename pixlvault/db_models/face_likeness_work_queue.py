from sqlmodel import SQLModel, Field
from typing import Optional


class FaceLikenessWorkQueue(SQLModel, table=True):
    """
    Work queue for pending likeness calculations between picture pairs.
    Each row represents a pair of pictures to process.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    face_id_a: int = Field(index=True, nullable=False)
    face_id_b: int = Field(index=True, nullable=False)
    # Optionally, add timestamp or priority fields if needed
    # created_at: Optional[str] = Field(default=None, index=True)
    # priority: Optional[int] = Field(default=0, index=True)
