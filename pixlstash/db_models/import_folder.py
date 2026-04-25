from typing import Optional

from sqlmodel import Field, SQLModel


class ImportFolder(SQLModel, table=True):
    """A user-configured folder monitored for automatic imports.

    Attributes:
        id: Primary key.
        folder: Absolute path to the folder root that is scanned recursively.
        label: User-visible display name for the folder.
        delete_after_import: When True, successfully imported source files are
            deleted from the monitored folder.
        last_checked: Unix timestamp of the last completed scan.
    """

    __tablename__ = "import_folder"

    id: Optional[int] = Field(default=None, primary_key=True)
    folder: str = Field(index=True)
    label: str = Field(default="")
    delete_after_import: bool = Field(default=False)
    last_checked: Optional[float] = Field(default=None)
