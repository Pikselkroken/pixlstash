from typing import Optional

from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    id: int = Field(default=None, primary_key=True)
    username: Optional[str] = Field(default=None, index=True)
    password_hash: Optional[str] = Field(default=None)

    # User settings (formerly config.json)
    description: Optional[str] = Field(default=None)
    sort: Optional[str] = Field(default=None)
    descending: bool = Field(default=True)
    thumbnail_size: Optional[int] = Field(default=None)
    show_stars: bool = Field(default=True)
    similarity_character: Optional[int] = Field(default=None)
