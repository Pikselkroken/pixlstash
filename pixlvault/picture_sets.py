from typing import List, Optional

from pixlvault.database import DBPriority
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

        def insert_picture_set(conn, picture_set: PictureSetModel):
            sql = "INSERT INTO picture_sets (name, description) VALUES (:name, :description)"
            cursor = conn.cursor()
            cursor.execute(sql, picture_set.to_dict())
            conn.commit()
            return cursor.lastrowid

        picture_set.id = self._db.submit_task(
            insert_picture_set, picture_set, priority=DBPriority.IMMEDIATE
        ).result()
        logger.info(f"Created picture set: {picture_set.name} (id={picture_set.id})")
        return picture_set

    def get(self, set_id: int) -> Optional[PictureSetModel]:
        """Get a picture set by id."""
        rows = self._db.execute_read(
            lambda conn: conn.execute(
                "SELECT * FROM picture_sets WHERE id = ?", (set_id,)
            ).fetchall()
        )
        if not rows:
            return None
        return PictureSetModel.from_dict(rows[0])

    def list_all(self) -> List[PictureSetModel]:
        """List all picture sets."""
        rows = self._db.execute_read(
            lambda conn: conn.execute(
                "SELECT * FROM picture_sets ORDER BY name"
            ).fetchall()
        )
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

        def update_picture_set(conn, picture_set: PictureSetModel):
            cursor = conn.cursor()
            sql = "UPDATE picture_sets SET name = :name, description = :description WHERE id = :id"
            cursor.execute(sql, picture_set.to_dict())
            conn.commit()
            return cursor.rowcount

        updated_rows = self._db.submit_task(
            update_picture_set, picture_set, priority=DBPriority.IMMEDIATE
        ).result()
        if updated_rows == 0:
            return False

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
        def delete_set(conn, set_id: int):
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM picture_set_members WHERE set_id = ?", (set_id,)
            )
            cursor.execute("DELETE FROM picture_sets WHERE id = ?", (set_id,))
            conn.commit()

        self._db.submit_task(delete_set, set_id, priority=DBPriority.IMMEDIATE).result()
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
        existing = self._db.execute_read(
            lambda conn: conn.execute(
                "SELECT * FROM picture_set_members WHERE set_id = ? AND picture_id = ?",
                (set_id, picture_id),
            ).fetchone()
        )
        if existing:
            logger.debug(f"Picture {picture_id} already in set {set_id}")
            return False

        # Add to set
        def addmember(conn, set_id: int, picture_id: str):
            member = PictureSetMemberModel(set_id=set_id, picture_id=picture_id)
            sql = "INSERT INTO picture_set_members (set_id, picture_id) VALUES (:set_id, :picture_id)"
            conn.execute(sql, member.to_dict())
            logger.info(f"Added picture {picture_id} to set {set_id}")

        self._db.submit_task(
            addmember, set_id, picture_id, priority=DBPriority.IMMEDIATE
        ).result()
        return True

    def remove_picture(self, set_id: int, picture_id: str) -> bool:
        """
        Remove a picture from a set.

        Returns:
            True if removed, False if not in set
        """
        result = self._db.submit_task(
            lambda conn: conn.execute(
                "DELETE FROM picture_set_members WHERE set_id = ? AND picture_id = ?",
                (set_id, picture_id),
            ),
            priority=DBPriority.IMMEDIATE,
        ).result()
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
        rows = self._db.execute_read(
            lambda conn: conn.execute(
                "SELECT picture_id FROM picture_set_members WHERE set_id = ? ORDER BY picture_id",
                (set_id,),
            ).fetchall()
        )
        return [row["picture_id"] for row in rows]

    def get_set_count(self, set_id: int) -> int:
        """Get the number of pictures in a set."""
        rows = self._db.execute_read(
            lambda conn: conn.execute(
                "SELECT COUNT(*) as count FROM picture_set_members WHERE set_id = ?",
                (set_id,),
            ).fetchone()
        )
        return rows[0] if rows else 0
