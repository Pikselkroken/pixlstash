import json

from pixelurgy_vault.logging import get_logger

from pixelurgy_vault.picture import Picture
from pixelurgy_vault.picture_tagger import PictureTagger, MAX_CONCURRENT_IMAGES

logger = get_logger(__name__)

class Pictures:
    def __init__(self, connection, picture_iterations):
        self.connection = connection
        self.picture_iterations = picture_iterations
        self.picture_tagger = PictureTagger()

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

    def update_picture_tags(self, picture_id, tags):
        """
        Update the tags for a picture in the database.
        Uses a context manager for atomic update to avoid thread transaction issues.
        """
        tags_json = json.dumps(tags)
        with self.connection:
            cursor = self.connection.cursor()
            cursor.execute(
                "UPDATE pictures SET tags = ? WHERE id = ?",
                (tags_json, picture_id)
            )

    def start_tag_worker(self, interval=0.01):
        import threading
        if hasattr(self, '_tag_worker') and self._tag_worker.is_alive():
            return
        self._tag_worker_stop = threading.Event()
        self._tag_worker = threading.Thread(target=self._tag_worker_loop, args=(interval,), daemon=True)
        self._tag_worker.start()

    def stop_tag_worker(self):
        if hasattr(self, '_tag_worker_stop'):
            self._tag_worker_stop.set()
        if hasattr(self, '_tag_worker'):
            self._tag_worker.join(timeout=5)

    def _tag_worker_loop(self, interval):
        while not self._tag_worker_stop.is_set():
            # Find all Pictures with no tags
            untagged = [pic for pic in self.find() if not pic.tags]
            if not untagged:
                self._tag_worker_stop.wait(interval)
                continue
            # Select up to MAX_CONCURRENT_IMAGES
            batch = untagged[:MAX_CONCURRENT_IMAGES]
            # Get master iteration image paths using self.picture_iterations
            image_paths = []
            pic_by_path = {}
            for pic in batch:
                # Find master iteration for this picture
                master_iters = [it for it in self.picture_iterations.find(picture_id=pic.id, is_master=1)]
                if master_iters:
                    master_iter = master_iters[0]
                    image_paths.append(master_iter.file_path)
                    pic_by_path[master_iter.file_path] = pic
            if not image_paths:
                self._tag_worker_stop.wait(interval)
                continue
            # Tag images
            logger.info(f"Tagging {len(image_paths)} images")
            tag_results = self.picture_tagger.tag_images(image_paths)
            # Assign tags to each Picture
            for path, tags in tag_results.items():
                pic = pic_by_path.get(path)
                if pic is not None:
                    pic.tags = tags
                    self.update_picture_tags(pic.id, tags)
            self._tag_worker_stop.wait(interval)

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
