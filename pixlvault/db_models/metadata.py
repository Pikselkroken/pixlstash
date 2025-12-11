from typing import ClassVar, Optional
from sqlmodel import SQLModel, Field


class MetaData(SQLModel, table=True):
    CURRENT_SCHEMA_VERSION: ClassVar[int] = 1
    """
    Manages the schema version for the vault database.
    """
    schema_version: int = Field(primary_key=True, default=CURRENT_SCHEMA_VERSION)
    description: Optional[str] = Field(default=None)
