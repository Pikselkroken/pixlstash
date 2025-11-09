from .database import VaultDatabase
from .logging import get_logger


class PictureCharacters:
    """
    CRUD operations for picture-character associations (many-to-many).
    """

    def __init__(self, db: VaultDatabase):
        """
        Initialize PictureCharacters with a database reference.

        Args:
            db: VaultDatabase instance.
        """
        self._db = db
        self.logger = get_logger(__name__)

    def add(self, picture_id: str, character_id: int):
        """
        Add a character to a picture.

        Args:
            picture_id: Picture ID
            character_id: Character ID
        """
        self._db.execute(
            "INSERT OR IGNORE INTO picture_characters (picture_id, character_id) VALUES (?, ?)",
            (picture_id, character_id),
        )
        self._db.commit()

    def remove(self, picture_id: str, character_id: int):
        """
        Remove a character from a picture.

        Args:
            picture_id: Picture ID
            character_id: Character ID
        """
        self._db.execute(
            "DELETE FROM picture_characters WHERE picture_id = ? AND character_id = ?",
            (picture_id, character_id),
        )
        self._db.commit()

    def get_characters_for_picture(self, picture_id: str) -> list[int]:
        """
        Get all character IDs associated with a picture.

        Args:
            picture_id: Picture ID

        Returns:
            List of character IDs
        """
        cursor = self._db.execute(
            "SELECT character_id FROM picture_characters WHERE picture_id = ?",
            (picture_id,),
        )
        return [row[0] for row in cursor.fetchall()]

    def get_pictures_for_character(self, character_id: int) -> list[str]:
        """
        Get all picture IDs associated with a character.

        Args:
            character_id: Character ID

        Returns:
            List of picture IDs
        """
        cursor = self._db.execute(
            "SELECT picture_id FROM picture_characters WHERE character_id = ?",
            (character_id,),
        )
        return [row[0] for row in cursor.fetchall()]

    def set_characters_for_picture(self, picture_id: str, character_ids: list[int]):
        """
        Set all characters for a picture (replaces existing associations).

        Args:
            picture_id: Picture ID
            character_ids: List of character IDs to associate
        """
        # Clear existing associations
        self._db.execute(
            "DELETE FROM picture_characters WHERE picture_id = ?", (picture_id,)
        )

        # Add new associations
        for character_id in character_ids:
            self._db.execute(
                "INSERT OR IGNORE INTO picture_characters (picture_id, character_id) VALUES (?, ?)",
                (picture_id, character_id),
            )

        self._db.commit()

    def clear_picture(self, picture_id: str):
        """
        Remove all character associations for a picture.

        Args:
            picture_id: Picture ID
        """
        self._db.execute(
            "DELETE FROM picture_characters WHERE picture_id = ?", (picture_id,)
        )
        self._db.commit()

    def clear_character(self, character_id: int):
        """
        Remove all picture associations for a character.

        Args:
            character_id: Character ID
        """
        self._db.execute(
            "DELETE FROM picture_characters WHERE character_id = ?", (character_id,)
        )
        self._db.commit()
