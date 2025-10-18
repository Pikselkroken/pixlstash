import json
from pixelurgy_vault.picture_iteration import PictureIteration
from pixelurgy_vault.picture_quality import PictureQuality
from typing import List
import time


class PictureIterations:
    def __init__(self, connection):
        self.connection = connection

    def __getitem__(self, iteration_id):
        cursor = self.connection.cursor()
        cursor.execute(
            """
            SELECT id, picture_id, file_path, format, width, height, size_bytes, created_at, is_master, derived_from, transform_metadata, thumbnail, quality, score, pixel_sha
            FROM picture_iterations WHERE id = ?
            """,
            (iteration_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise KeyError(f"PictureIteration with id {iteration_id} not found.")
        quality = None
        if row[12]:
            try:
                quality = PictureQuality(**json.loads(row[12]))
            except Exception:
                quality = None
        it = PictureIteration(
            id=row[0],
            picture_id=row[1],
            file_path=row[2],
            format=row[3],
            width=row[4],
            height=row[5],
            size_bytes=row[6],
            created_at=row[7],
            is_master=row[8],
            derived_from=row[9],
            transform_metadata=row[10],
            thumbnail=row[11],
            quality=quality,
            score=row[13],
            pixel_sha=row[14],
        )
        return it

    def __iter__(self):
        cursor = self.connection.cursor()
        cursor.execute("SELECT id FROM picture_iterations")
        for row in cursor.fetchall():
            yield row[0]

    def import_iterations(self, iterations: List[PictureIteration]):
        cursor = self.connection.cursor()
        vals = []
        for it in iterations:
            quality_json = None
            if it.quality:
                try:
                    quality_json = json.dumps(it.quality.__dict__)
                except Exception:
                    quality_json = None
            vals.append(
                (
                    it.id,
                    it.picture_id,
                    it.file_path,
                    it.format,
                    it.width,
                    it.height,
                    it.size_bytes,
                    it.created_at or time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    it.is_master,
                    it.derived_from,
                    it.transform_metadata,
                    it.thumbnail,
                    quality_json,
                    it.score,
                    it.pixel_sha if hasattr(it, "pixel_sha") else None,
                )
            )
        cursor.executemany(
            """
            INSERT INTO picture_iterations (
                id, picture_id, file_path, format, width, height, size_bytes, created_at, is_master,
                derived_from, transform_metadata, thumbnail, quality, score, pixel_sha
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            vals,
        )
        self.connection.commit()

    def find(self, **kwargs):
        cursor = self.connection.cursor()
        if not kwargs:
            cursor.execute("SELECT * FROM picture_iterations")
        else:
            query = "SELECT * FROM picture_iterations WHERE " + " AND ".join(
                [f"{k}=?" for k in kwargs.keys()]
            )
            cursor.execute(query, tuple(kwargs.values()))
        rows = cursor.fetchall()
        result = []
        for row in rows:
            quality = PictureQuality(**json.loads(row[12])) if row[12] else None
            it = PictureIteration(
                id=row[0],
                picture_id=row[1],
                file_path=row[2],
                format=row[3],
                width=row[4],
                height=row[5],
                size_bytes=row[6],
                created_at=row[7],
                is_master=row[8],
                derived_from=row[9],
                transform_metadata=row[10],
                thumbnail=row[11],
                quality=quality,
                score=row[13],
                pixel_sha=row[14],
            )
            result.append(it)
        return result
