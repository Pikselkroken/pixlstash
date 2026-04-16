from typing import Optional, List, TYPE_CHECKING

from sqlmodel import Field, SQLModel, Relationship

if TYPE_CHECKING:
    from .picture import Picture


class ReferenceFolderStatus:
    PENDING_MOUNT = "pending_mount"
    ACTIVE = "active"
    MOUNT_ERROR = "mount_error"


class ReferenceFolder(SQLModel, table=True):
    """A user-configured external folder indexed in place by PixlStash.

    Attributes:
        id: Primary key.
        folder: Absolute host-side path to the folder root.
        label: User-visible name; defaults to the last path component.
        allow_delete_file: When True, deleting a picture via the UI also
            removes the source file from disk.
        status: Lifecycle state — pending_mount, active, or mount_error.
        last_scanned: Unix timestamp of the last completed scan pass.
    """

    __tablename__ = "reference_folder"

    id: Optional[int] = Field(default=None, primary_key=True)
    folder: str = Field(index=True)
    label: str = Field(default="")
    allow_delete_file: bool = Field(default=False)
    # When True, tag changes made in PixlStash are written back to the picture's
    # sidecar caption file so the folder stays in sync with the database.
    sync_captions: bool = Field(default=False)
    status: str = Field(default=ReferenceFolderStatus.PENDING_MOUNT, index=True)
    last_scanned: Optional[float] = Field(default=None)

    pictures: List["Picture"] = Relationship(back_populates="reference_folder")
