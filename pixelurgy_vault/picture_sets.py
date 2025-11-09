from typing import List, Optional
from .picture_set import PictureSetModel, PictureSetMemberModel
from .logging import get_logger

logger = get_logger(__name__)


class PictureSets:
    """
    Manages picture sets and their members using VaultDatabase pattern.
    """

    def __init__(self, db):
        """
        Args:
            db: VaultDatabase instance
        """
        self._db = db

    def create(self, name: str, description: str = None) -> PictureSetModel:
        """
        Create a new picture set.

        Args:
            name: Name of the set
            description: Optional description

        Returns:
            The created PictureSetModel with id populated
        """
        picture_set = PictureSetModel(name=name, description=description)
        sql = (
            "INSERT INTO picture_sets (name, description) VALUES (:name, :description)"
        )
        cursor = self._db.execute(sql, picture_set.to_dict())
        picture_set.id = cursor.lastrowid
        self._db.commit()
        logger.info(f"Created picture set: {picture_set.name} (id={picture_set.id})")
        return picture_set

    def get(self, set_id: int) -> Optional[PictureSetModel]:
        """Get a picture set by id."""
        rows = self._db.query("SELECT * FROM picture_sets WHERE id = ?", (set_id,))
        if not rows:
            return None
        return PictureSetModel.from_dict(rows[0])

    def list_all(self) -> List[PictureSetModel]:
        """List all picture sets."""
        rows = self._db.query("SELECT * FROM picture_sets ORDER BY name")
        return [PictureSetModel.from_dict(row) for row in rows]

    def update(self, set_id: int, name: str = None, description: str = None) -> bool:
        """
        Update a picture set's name and/or description.

        Returns:
            True if updated, False if set not found
        """
        picture_set = self.get(set_id)
        if not picture_set:
            return False

        if name is not None:
            picture_set.name = name
        if description is not None:
            picture_set.description = description

        sql = "UPDATE picture_sets SET name = :name, description = :description WHERE id = :id"
        self._db.execute(sql, picture_set.to_dict())
        self._db.commit()
        logger.info(f"Updated picture set id={set_id}")
        return True

    def delete(self, set_id: int) -> bool:
        """
        Delete a picture set and all its members.

        Returns:
            True if deleted, False if set not found
        """
        picture_set = self.get(set_id)
        if not picture_set:
            return False

        # Delete all members first
        self._db.execute("DELETE FROM picture_set_members WHERE set_id = ?", (set_id,))
        # Delete the set
        self._db.execute("DELETE FROM picture_sets WHERE id = ?", (set_id,))
        self._db.commit()
        logger.info(f"Deleted picture set id={set_id}")
        return True

    def add_picture(self, set_id: int, picture_id: str) -> bool:
        """
        Add a picture to a set.

        Args:
            set_id: The set id
            picture_id: The picture id

        Returns:
            True if added, False if already exists or set doesn't exist
        """
        # Check if set exists
        if not self.get(set_id):
            logger.warning(f"Cannot add picture to non-existent set id={set_id}")
            return False

        # Check if already in set
        existing = self._db.query(
            "SELECT * FROM picture_set_members WHERE set_id = ? AND picture_id = ?",
            (set_id, picture_id),
        )
        if existing:
            logger.debug(f"Picture {picture_id} already in set {set_id}")
            return False

        # Add to set
        member = PictureSetMemberModel(set_id=set_id, picture_id=picture_id)
        sql = "INSERT INTO picture_set_members (set_id, picture_id) VALUES (:set_id, :picture_id)"
        self._db.execute(sql, member.to_dict())
        self._db.commit()
        logger.info(f"Added picture {picture_id} to set {set_id}")
        return True

    def remove_picture(self, set_id: int, picture_id: str) -> bool:
        """
        Remove a picture from a set.

        Returns:
            True if removed, False if not in set
        """
        result = self._db.execute(
            "DELETE FROM picture_set_members WHERE set_id = ? AND picture_id = ?",
            (set_id, picture_id),
        )
        self._db.commit()
        removed = result.rowcount > 0
        if removed:
            logger.info(f"Removed picture {picture_id} from set {set_id}")
        return removed

    def get_pictures_in_set(self, set_id: int) -> List[str]:
        """
        Get all picture ids in a set.

        Returns:
            List of picture ids
        """
        rows = self._db.query(
            "SELECT picture_id FROM picture_set_members WHERE set_id = ? ORDER BY picture_id",
            (set_id,),
        )
        return [row["picture_id"] for row in rows]

    def get_sets_for_picture(self, picture_id: str) -> List[PictureSetModel]:
        """
        Get all sets that contain a given picture.

        Returns:
            List of PictureSetModel objects
        """
        rows = self._db.query(
            """
            SELECT ps.* FROM picture_sets ps
            JOIN picture_set_members psm ON ps.id = psm.set_id
            WHERE psm.picture_id = ?
            ORDER BY ps.name
            """,
            (picture_id,),
        )
        return [PictureSetModel.from_dict(row) for row in rows]

    def set_pictures(self, set_id: int, picture_ids: List[str]) -> bool:
        """
        Replace all pictures in a set with the given list.

        Args:
            set_id: The set id
            picture_ids: List of picture ids to set (duplicates will be ignored)

        Returns:
            True if successful, False if set doesn't exist
        """
        # Check if set exists
        if not self.get(set_id):
            logger.warning(f"Cannot set pictures for non-existent set id={set_id}")
            return False

        # Remove duplicates while preserving order
        unique_picture_ids = list(dict.fromkeys(picture_ids))

        # Delete all current members
        self._db.execute("DELETE FROM picture_set_members WHERE set_id = ?", (set_id,))

        # Add new members
        if unique_picture_ids:
            members = [(set_id, picture_id) for picture_id in unique_picture_ids]
            self._db.executemany(
                "INSERT INTO picture_set_members (set_id, picture_id) VALUES (?, ?)",
                members,
            )

        self._db.commit()
        logger.info(f"Set {len(unique_picture_ids)} pictures for set {set_id}")
        return True

    def clear_set(self, set_id: int) -> bool:
        """
        Remove all pictures from a set (but keep the set itself).

        Returns:
            True if cleared, False if set doesn't exist
        """
        if not self.get(set_id):
            return False

        self._db.execute("DELETE FROM picture_set_members WHERE set_id = ?", (set_id,))
        self._db.commit()
        logger.info(f"Cleared all pictures from set {set_id}")
        return True

    def get_set_count(self, set_id: int) -> int:
        """Get the number of pictures in a set."""
        rows = self._db.query(
            "SELECT COUNT(*) as count FROM picture_set_members WHERE set_id = ?",
            (set_id,),
        )
        return rows[0]["count"] if rows else 0
