import hashlib
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime
from sqlmodel import Field, SQLModel


class DeletedFileLog(SQLModel, table=True):
    """Records permanently deleted pictures so restore knows what it must never
    resurrect — using only one-way hashes, never a readable path.

    A picture is identified by ``path_sha`` (SHA-256 of its vault file path)
    and, when available, ``pixel_sha`` (its content hash). The path is stored
    hashed so the ledger stays privacy-safe: reference-folder pictures have
    real on-disk paths that must not be retained in cleartext after deletion,
    and a vault picture's path is only an opaque UUID anyway. Matching needs
    exact equality, never the original string, so a hash is sufficient.

    Attributes:
        id: Primary key.
        path_sha: SHA-256 hex digest of the deleted picture's vault file path.
        pixel_sha: Content hash of the image at deletion time (may be NULL for
            rows whose pixel hash was never computed).
        deleted_at: UTC timestamp of permanent deletion.
    """

    __tablename__ = "deleted_file_log"

    id: Optional[int] = Field(default=None, primary_key=True)
    path_sha: str = Field(index=True)
    pixel_sha: Optional[str] = Field(default=None, index=True)
    deleted_at: datetime = Field(
        sa_column=Column("deleted_at", type_=DateTime, nullable=False)
    )

    @staticmethod
    def hash_path(file_path: str) -> str:
        """Return the ``path_sha`` digest for *file_path*.

        Centralised so every writer (scrapheap purge, missing-file purge) and
        the restore matcher derive the same digest from a picture's path.

        Args:
            file_path: Vault-relative (or absolute, for reference folders)
                picture path.

        Returns:
            SHA-256 hex digest of the UTF-8 encoded path.
        """
        return hashlib.sha256((file_path or "").encode("utf-8")).hexdigest()
