import json

from pixelurgy_vault.picture import Picture


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
        if row['tags']:
            try:
                tags = json.loads(row['tags'])
            except Exception:
                tags = []
        pic = Picture(
            id=row['id'],
            character_id=row['character_id'],
            description=row['description'],
            tags=tags,
            created_at=row['created_at'],
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
            yield row['id']

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
                    getattr(picture, "is_reference", 0),
                )
            )
        cursor.executemany(
            """
            INSERT INTO pictures (
                id, character_id, description, tags, created_at, is_reference
            ) VALUES (?, ?, ?, ?, ?, ?)
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
            pic = Picture(
                id=row['id'],
                character_id=row['character_id'],
                description=row['description'],
                tags=json.loads(row['tags']) if row['tags'] else [],
                created_at=row['created_at'],
                is_reference=row['is_reference'] if 'is_reference' in row.keys() else 0,
            )
            result.append(pic)
        return result
