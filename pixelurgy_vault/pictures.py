import json

from pixelurgy_vault.picture_quality import PictureQuality
from pixelurgy_vault.picture import Picture


class Pictures:
    def __init__(self, connection):
        self.connection = connection

    def __getitem__(self, picture_id):
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM pictures WHERE id = ?", (picture_id,))
        row = cursor.fetchone()
        if not row:
            raise KeyError(f"Picture with id {picture_id} not found.")
        tags = json.loads(row[5]) if row[5] else []
        quality = None
        if row[10]:
            quality = PictureQuality(**json.loads(row[10]))
        pic = Picture(
            file_path=row[1],
            character_id=row[2],
            title=row[3],
            description=row[4],
            tags=tags,
            width=row[6],
            height=row[7],
            format=row[8],
            created_at=row[9],
            thumbnail=row[11],
        )
        pic.id = row[0]
        pic.quality = quality
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
            quality_json = (
                json.dumps(picture.quality.__dict__)
                if hasattr(picture, "quality") and picture.quality
                else None
            )
            values.append(
                (
                    picture.id,
                    picture.file_path,
                    getattr(picture, "character_id", None),
                    getattr(picture, "title", None),
                    getattr(picture, "description", None),
                    tags_json,
                    getattr(picture, "width", None),
                    getattr(picture, "height", None),
                    getattr(picture, "format", None),
                    getattr(picture, "created_at", None),
                    quality_json,
                    getattr(picture, "thumbnail_array", None),
                )
            )
        cursor.executemany(
            """
            INSERT INTO pictures (
                id, file_path, character_id, title, description, tags, width, height, format, created_at, quality, thumbnail
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        Example: pictures.find(character_id="hero", format="png")
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
            tags = json.loads(row[5]) if row[5] else []
            quality = None
            if row[10]:
                quality = PictureQuality(**json.loads(row[10]))
            pic = Picture(
                file_path=row[1],
                character_id=row[2],
                title=row[3],
                description=row[4],
                tags=tags,
                width=row[4],
                height=row[5],
                format=row[6],
                created_at=row[7],
                thumbnail=row[10],
            )
            pic.id = row[0]
            pic.quality = quality
            result.append(pic)
        return result
