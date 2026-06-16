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
        host_path: Host-side bind source for Docker helpers.
            For Docker installs this should be the real host folder that is
            mounted to ``folder`` inside the container.
        label: User-visible name; defaults to the last path component.
        allow_delete_file: When True, deleting a picture via the UI also
            removes the source file from disk.
        sync_descriptions: When True, description changes made in PixlStash are
            written back to a description sidecar file next to each image (and
            external edits are read back in).
        sync_tags: When True, tag changes made in PixlStash are written back to
            a tags sidecar file next to each image (and external edits are read
            back in).
        description_suffix: Filename suffix for description sidecars, applied to
            the image stem (e.g. ``_description.txt`` → ``image_description.txt``).
            NULL means "not explicitly configured"; callers fall back to the
            module default and legacy detection.
        tags_suffix: Filename suffix for tags sidecars (e.g. ``_tags.txt``).
            NULL means "not explicitly configured".
        status: Lifecycle state — pending_mount, active, or mount_error.
        last_scanned: Unix timestamp of the last completed scan pass.
    """

    __tablename__ = "reference_folder"

    id: Optional[int] = Field(default=None, primary_key=True)
    folder: str = Field(index=True)
    host_path: Optional[str] = Field(default=None)
    label: str = Field(default="")
    allow_delete_file: bool = Field(default=False)
    # When True, description/tag changes made in PixlStash are written back to
    # the picture's sidecar files so the folder stays in sync with the database.
    # The two types are independent: each has its own toggle and filename suffix.
    sync_descriptions: bool = Field(default=False)
    sync_tags: bool = Field(default=False)
    description_suffix: Optional[str] = Field(default=None)
    tags_suffix: Optional[str] = Field(default=None)
    status: str = Field(default=ReferenceFolderStatus.PENDING_MOUNT, index=True)
    last_scanned: Optional[float] = Field(default=None)

    pictures: List["Picture"] = Relationship(back_populates="reference_folder")
