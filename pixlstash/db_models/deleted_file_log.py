from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel


class DeletedFileLog(SQLModel, table=True):
    """Records permanently deleted picture files for incremental backup tracking.

    Attributes:
        id: Primary key.
        file_path: Vault-relative file path of the deleted image.
        pixel_sha: Content hash of the image at deletion time.
        deleted_at: UTC timestamp of permanent deletion.
    """

    __tablename__ = "deleted_file_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    file_path: str = Field(index=True)
    pixel_sha: Optional[str] = Field(default=None, index=True)
    deleted_at: datetime = Field(
        sa_column=Column("deleted_at", type_=DateTime, nullable=False)
    )
