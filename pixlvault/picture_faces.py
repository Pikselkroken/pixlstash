from .database import VaultDatabase
from .logging import get_logger

logger = get_logger(__name__)


class PictureFaces:
    """
    CRUD operations for detected faces in pictures (picture_faces table).
    """

    def get_faces_for_picture(self, picture_id: str):
        """
        Return a list of all face records for a given picture_id.
        """
        picture_id = str(picture_id)

        def op(conn):
            return conn.execute(
                "SELECT face_index, character_id, bbox, features, sharpness, edge_density, contrast, brightness, noise_level, frame_index FROM picture_faces WHERE picture_id = ?",
                (picture_id,),
            ).fetchall()

        rows = self._db.execute_read(op)
        faces = []
        for row in rows:
            from .face import Face

            keys = ["face_index", "character_id"] + Face.data_fields + ["frame_index"]
            faces.append(dict(zip(keys, row)))
        return faces

    def debug_dump(self):
        def op(conn):
            rows = conn.execute(
                "SELECT picture_id, character_id, bbox FROM picture_faces"
            ).fetchall()
            logger.debug(f"PICTURE_FACES TABLE DUMP: {[tuple(row) for row in rows]}")

        self._db.execute_read(op)

    def __init__(self, db: VaultDatabase):
        """
        Initialize PictureFaces with a database reference.
        Args:
            db: VaultDatabase instance.
        """
        self._db = db

    def add(
        self,
        picture_id: str,
        face_index: int = None,
        character_id: int = None,
        frame_index: int = 0,
        **face_data,
    ):
        """
        Add a detected face to a picture, with optional character assignment and face data.
        Args:
            picture_id: Picture ID
            face_index: Index of the face in the picture (int, can be None)
            character_id: Character ID (can be None for unassigned faces)
            face_data: Optional face data fields (bbox, features, etc.)
        """
        picture_id = str(picture_id)
        if face_index is not None:
            face_index = int(face_index)
        if character_id is not None:
            character_id = int(character_id)
        logger.debug(
            f"ADD: picture_id={picture_id} ({type(picture_id)}), face_index={face_index}, character_id={character_id}"
        )
        from .face import Face

        columns = Face.search_keys + Face.data_fields
        values = [picture_id, face_index, character_id, frame_index]
        for key in Face.data_fields:
            values.append(face_data.get(key, None))
        placeholders = ", ".join(["?" for _ in columns])
        sql = f"INSERT OR IGNORE INTO picture_faces ({', '.join(columns)}) VALUES ({placeholders})"

        def op(conn):
            conn.execute(sql, tuple(values))

        self._db.submit_task(op)

    def set_face_data(
        self, picture_id: str, face_index: int = None, frame_index: int = 0, **face_data
    ):
        """
        Set face data for a detected face in a picture.
        """
        picture_id = str(picture_id)
        if face_index is not None:
            face_index = int(face_index)

        assert frame_index is not None
        assert isinstance(frame_index, int)

        logger.debug(
            f"SET_FACE_DATA: picture_id={picture_id} ({type(picture_id)}), face_index={face_index}, frame_index={frame_index}, face_data={face_data}"
        )
        if not face_data:
            return
        assignments = []
        values = []
        from .face import Face

        for key in Face.data_fields + ["character_id"]:
            if key in face_data:
                assignments.append(f"{key} = ?")
                values.append(face_data[key])
        if assignments:
            # Build WHERE clause for picture_id, face_index, frame_index (never NULL)
            where_clauses = ["picture_id = ?"]
            where_values = [picture_id]
            where_clauses.append("face_index = ?")
            where_values.append(face_index)
            where_clauses.append("frame_index = ?")
            where_values.append(frame_index)
            sql = f"UPDATE picture_faces SET {', '.join(assignments)} WHERE {' AND '.join(where_clauses)}"
            values_all = values + where_values

            def op(conn):
                cur = conn.execute(sql, tuple(values_all))
                logger.debug(f"SET_FACE_DATA: rows updated = {cur.rowcount}")
                if cur.rowcount != 1:
                    logger.error(
                        f"SET_FACE_DATA: Expected to update 1 row, but updated {cur.rowcount}. SQL: {sql}, values: {values_all}"
                    )

            self._db.submit_task(op).result()

    def get_face_data(
        self,
        picture_id: str,
        face_index: int = None,
        character_id: int = None,
        frame_index: int = 0,
    ) -> dict:
        """
        Get face data for a detected face in a picture.
        """
        picture_id = str(picture_id)
        if face_index is not None:
            face_index = int(face_index)
        if character_id is not None:
            character_id = int(character_id)
        logger.info(
            f"GET_FACE_DATA BEFORE: picture_id={picture_id} ({type(picture_id)}), face_index={face_index}, character_id={character_id}, frame_index={frame_index}"
        )
        # Build WHERE clause dynamically based on provided keys
        where_clauses = ["picture_id = ?"]
        values = [picture_id]
        if face_index is not None:
            where_clauses.append("face_index = ?")
            values.append(face_index)
        else:
            where_clauses.append("face_index IS NULL")
        if character_id is not None:
            where_clauses.append("character_id = ?")
            values.append(character_id)
        # Do not use IS NULL for frame_index; always use equality
        where_clauses.append("frame_index = ?")
        values.append(frame_index)

        where_sql = " AND ".join(where_clauses)
        from .face import Face

        select_fields = Face.data_fields + ["character_id"]
        sql = f"SELECT {', '.join(select_fields)} FROM picture_faces WHERE {where_sql}"

        def op(conn):
            return conn.execute(sql, tuple(values)).fetchone()

        row = self._db.execute_read(op)
        if row:
            keys = Face.data_fields + ["character_id"]
            return dict(zip(keys, row))
        else:
            logger.warning("GET_FACE_DATA: No row found.")
            return None
