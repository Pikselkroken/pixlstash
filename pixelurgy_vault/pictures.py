import json

from pixelurgy_vault.picture_quality import PictureQuality
from pixelurgy_vault.picture import Picture
from typing import Optional, List
import time
import json


class Pictures:
    def __init__(self, connection):
        self.connection = connection

    def __getitem__(self, picture_id):
        # Return master Picture by picture_uuid
        cursor = self.connection.cursor()
        cursor.execute(
            "SELECT id, character_id, description, tags, created_at FROM pictures WHERE id = ?",
            (picture_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise KeyError(f"Picture with id {picture_id} not found.")
        tags = []
        if row[3]:
            try:
                tags = json.loads(row[3])
            except Exception:
                tags = []
        pic = Picture(
            id=row[0],
            character_id=row[1],
            description=row[2],
            tags=tags,
            created_at=row[4],
        )
        return pic

    def __setitem__(self, picture_id, picture):
        picture.id = picture_id
        self.import_picture(picture)

    def __delitem__(self, picture_id):
        cursor = self.connection.cursor()
        cursor.execute("DELETE FROM pictures WHERE id = ?", (picture_id,))
        self.connection.commit()

    def __iter__(self):
        cursor = self.connection.cursor()
        cursor.execute("SELECT id FROM pictures")
        for row in cursor.fetchall():
            yield row[0]

    def import_pictures(self, pictures):
        """Import a list of Picture instances into the database using executemany for efficiency."""
        cursor = self.connection.cursor()
        values = []
        for picture in pictures:
            tags_json = json.dumps(picture.tags) if hasattr(picture, "tags") else None
            values.append(
                (
                    picture.id,
                    getattr(picture, "character_id", None),
                    getattr(picture, "description", None),
                    tags_json,
                    getattr(picture, "created_at", None),
                )
            )
        cursor.executemany(
            """
            INSERT INTO pictures (
                id, character_id, description, tags, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            values,
        )
        self.connection.commit()

    def contains(self, picture):
        """
        Check if a Picture with the same id exists in the database.
        """
        cursor = self.connection.cursor()
        cursor.execute("SELECT 1 FROM pictures WHERE id = ?", (picture.id,))
        return cursor.fetchone() is not None

    def find(self, **kwargs):
        """
        Find and return a list of Picture objects matching all provided attribute=value pairs.
        Example: pictures.find(character_id="hero")
        """
        cursor = self.connection.cursor()
        if not kwargs:
            cursor.execute("SELECT * FROM pictures")
        else:
            query = "SELECT * FROM pictures WHERE " + " AND ".join(
                [f"{k}=?" for k in kwargs.keys()]
            )
            cursor.execute(query, tuple(kwargs.values()))
        rows = cursor.fetchall()
        result = []
        for row in rows:
            # pictures table: id, character_id, description, tags, created_at
            pic = Picture(
                id=row[0],
                character_id=row[1],
                description=row[2],
                tags=json.loads(row[3]) if row[3] else [],
                created_at=row[4],
            )
            result.append(pic)
        return result
