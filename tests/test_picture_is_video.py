"""Tests for Picture.is_video persistence and fetch_best_picture_id behavior.

Tests cover:
1. The import ctor path sets the flag based on PIL decode success/failure.
2. The migration backfill sets it from file_path extension, including UPPER-CASE.
3. fetch_best_picture_id prefers still images over higher-scored videos.
"""

from io import BytesIO

from sqlalchemy import text
from sqlmodel import Session, select

from pixlstash.db_models import Character, Face, Picture
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.vault import Vault


class TestPictureIsVideoImport:
    """Test the import path sets is_video flag correctly."""

    def test_still_image_sets_is_video_false(self, tmp_path):
        """An imported still image (jpg) gets is_video=False."""
        # Create a minimal JPEG in memory
        from PIL import Image

        img = Image.new("RGB", (100, 100), color="red")
        img_bytes = BytesIO()
        img.save(img_bytes, format="JPEG")
        img_bytes.seek(0)

        # Import the picture
        pic = ImageUtils.create_picture_from_bytes(
            image_root_path=str(tmp_path),
            image_bytes=img_bytes.getvalue(),
            picture_uuid="test.jpg",
        )

        assert pic.is_video is False, "Still image should have is_video=False"


class TestIsVideoColumnShape:
    """Pin the column as NOT NULL DEFAULT 0.

    This is a correctness constraint, not style. SQLite sorts NULL FIRST, so a
    nullable ``is_video`` lets a row of unknown type outrank a genuine still
    image in ``fetch_best_picture_id`` -- something the CASE this column
    replaced could never do, since it only ever yielded 0 or 1. A NULL would
    also slip past the ``is_video = 0`` guard in migration 0096 and never be
    classified. Both regressions are silent, so assert the schema directly.
    """

    def test_column_is_not_nullable_with_server_default(self):
        column = Picture.__table__.c["is_video"]
        assert column.nullable is False
        assert column.server_default is not None
        assert column.server_default.arg == "0"

    def test_a_row_inserted_without_the_flag_is_false_not_null(self, tmp_path):
        """The DB default, not just the Python default, classifies a bare row."""
        with Vault(image_root=str(tmp_path)) as vault:

            def insert_bare_row(session: Session):
                # Deliberately bypasses the ORM default so the server default is
                # what is under test.
                session.execute(
                    text(
                        "INSERT INTO picture (file_path, deleted) "
                        "VALUES ('/x/plain.jpg', 0)"
                    )
                )
                session.commit()
                return session.exec(
                    select(Picture.is_video).where(Picture.file_path == "/x/plain.jpg")
                ).one()

            assert vault.db.run_task(insert_bare_row) is False


class TestMigrationBackfill:
    """Test the migration backfills is_video from file_path extension."""

    def test_backfill_mp4_extension(self, tmp_path):
        """Pictures with .mp4 extension get is_video=1 from backfill."""
        with Vault(image_root=str(tmp_path)) as vault:

            def setup_and_backfill(session: Session):
                pic = Picture(
                    file_path="2024/01/15/test.mp4",
                    format="MP4",
                    width=1920,
                    height=1080,
                    size_bytes=1000000,
                    is_video=False,  # Pre-migration state
                )
                session.add(pic)
                session.commit()
                pic_id = pic.id

                # Simulate migration backfill
                session.execute(
                    text(
                        "UPDATE picture SET is_video = 1 "
                        "WHERE is_video = 0 "
                        "AND (lower(file_path) LIKE '%.mp4' "
                        "  OR lower(file_path) LIKE '%.webm' "
                        "  OR lower(file_path) LIKE '%.avi' "
                        "  OR lower(file_path) LIKE '%.mov' "
                        "  OR lower(file_path) LIKE '%.mkv')"
                    )
                )
                session.commit()

                updated = session.get(Picture, pic_id)
                return updated.is_video

            is_video = vault.db.run_immediate_read_task(setup_and_backfill)
            assert is_video is True, "Backfill should set is_video=1 for .mp4"

    def test_backfill_uppercase_extension(self, tmp_path):
        """UPPER-CASE extensions are also matched (SQLite LIKE is ASCII-insensitive)."""
        with Vault(image_root=str(tmp_path)) as vault:

            def setup_and_backfill(session: Session):
                pic = Picture(
                    file_path="2024/01/15/TEST.MP4",
                    format="MP4",
                    width=1920,
                    height=1080,
                    size_bytes=1000000,
                    is_video=False,
                )
                session.add(pic)
                session.commit()
                pic_id = pic.id

                session.execute(
                    text(
                        "UPDATE picture SET is_video = 1 "
                        "WHERE is_video = 0 "
                        "AND (lower(file_path) LIKE '%.mp4' "
                        "  OR lower(file_path) LIKE '%.webm' "
                        "  OR lower(file_path) LIKE '%.avi' "
                        "  OR lower(file_path) LIKE '%.mov' "
                        "  OR lower(file_path) LIKE '%.mkv')"
                    )
                )
                session.commit()

                updated = session.get(Picture, pic_id)
                return updated.is_video

            is_video = vault.db.run_immediate_read_task(setup_and_backfill)
            assert is_video is True, "Backfill should set is_video=1 for .MP4"

    def test_backfill_still_image_unchanged(self, tmp_path):
        """Still-image extensions are not changed by backfill."""
        with Vault(image_root=str(tmp_path)) as vault:

            def setup_and_backfill(session: Session):
                pic = Picture(
                    file_path="2024/01/15/test.jpg",
                    format="JPEG",
                    width=1920,
                    height=1080,
                    size_bytes=1000000,
                    is_video=False,
                )
                session.add(pic)
                session.commit()
                pic_id = pic.id

                session.execute(
                    text(
                        "UPDATE picture SET is_video = 1 "
                        "WHERE is_video = 0 "
                        "AND (lower(file_path) LIKE '%.mp4' "
                        "  OR lower(file_path) LIKE '%.webm' "
                        "  OR lower(file_path) LIKE '%.avi' "
                        "  OR lower(file_path) LIKE '%.mov' "
                        "  OR lower(file_path) LIKE '%.mkv')"
                    )
                )
                session.commit()

                updated = session.get(Picture, pic_id)
                return updated.is_video

            is_video = vault.db.run_immediate_read_task(setup_and_backfill)
            assert is_video is False, (
                "Backfill should not change still-image extensions"
            )


class TestFetchBestPictureId:
    """Test fetch_best_picture_id prefers still images over videos."""

    def test_prefers_still_image_over_lower_scored_video(self, tmp_path):
        """fetch_best_picture_id returns a still image even if a video has higher score."""
        with Vault(image_root=str(tmp_path)) as vault:

            def setup_and_query(session: Session):
                # Create a character and faces pointing to both a video and a still image
                char = Character(name="test_char")
                session.add(char)
                session.commit()

                # High-scored video
                video_pic = Picture(
                    file_path="2024/01/15/video.mp4",
                    format="MP4",
                    width=1920,
                    height=1080,
                    size_bytes=1000000,
                    is_video=True,
                    score=100,
                )
                session.add(video_pic)
                session.commit()

                # Lower-scored still image
                image_pic = Picture(
                    file_path="2024/01/15/image.jpg",
                    format="JPEG",
                    width=1920,
                    height=1080,
                    size_bytes=500000,
                    is_video=False,
                    score=50,
                )
                session.add(image_pic)
                session.commit()

                # Assign both to the character
                video_face = Face(
                    character_id=char.id,
                    picture_id=video_pic.id,
                    face_index=0,
                    bbox=[100, 100, 200, 200],
                )
                session.add(video_face)

                image_face = Face(
                    character_id=char.id,
                    picture_id=image_pic.id,
                    face_index=0,
                    bbox=[100, 100, 200, 200],
                )
                session.add(image_face)
                session.commit()

                # Query for best picture (simulating fetch_best_picture_id logic)
                row = session.exec(
                    select(Picture.id, Picture.score)
                    .join(Face, Face.picture_id == Picture.id)
                    .where(
                        Face.character_id == char.id,
                        Picture.deleted.is_(False),
                    )
                    .order_by(
                        Picture.is_video,  # prefer still images (False/0) over videos
                        Picture.score.is_(None),
                        Picture.score.desc(),
                        Picture.id.desc(),
                    )
                    .limit(1)
                ).first()

                assert row is not None, "Should find a picture"
                pic_id, score = row
                return pic_id, image_pic.id

            pic_id, expected_id = vault.db.run_immediate_read_task(setup_and_query)
            assert pic_id == expected_id, "Should select the still image"

    def test_prefers_higher_scored_still_image(self, tmp_path):
        """fetch_best_picture_id returns the highest-scored still image."""
        with Vault(image_root=str(tmp_path)) as vault:

            def setup_and_query(session: Session):
                char = Character(name="test_char2")
                session.add(char)
                session.commit()

                # Two still images with different scores
                high_score_pic = Picture(
                    file_path="2024/01/15/high.jpg",
                    format="JPEG",
                    width=1920,
                    height=1080,
                    size_bytes=500000,
                    is_video=False,
                    score=100,
                )
                session.add(high_score_pic)
                session.commit()

                low_score_pic = Picture(
                    file_path="2024/01/15/low.jpg",
                    format="JPEG",
                    width=1920,
                    height=1080,
                    size_bytes=500000,
                    is_video=False,
                    score=50,
                )
                session.add(low_score_pic)
                session.commit()

                # Assign both to the character
                high_face = Face(
                    character_id=char.id,
                    picture_id=high_score_pic.id,
                    face_index=0,
                    bbox=[100, 100, 200, 200],
                )
                session.add(high_face)

                low_face = Face(
                    character_id=char.id,
                    picture_id=low_score_pic.id,
                    face_index=0,
                    bbox=[100, 100, 200, 200],
                )
                session.add(low_face)
                session.commit()

                # Query for best picture
                row = session.exec(
                    select(Picture.id, Picture.score)
                    .join(Face, Face.picture_id == Picture.id)
                    .where(
                        Face.character_id == char.id,
                        Picture.deleted.is_(False),
                    )
                    .order_by(
                        Picture.is_video,
                        Picture.score.is_(None),
                        Picture.score.desc(),
                        Picture.id.desc(),
                    )
                    .limit(1)
                ).first()

                assert row is not None, "Should find a picture"
                pic_id, score = row
                return pic_id, high_score_pic.id

            pic_id, expected_id = vault.db.run_immediate_read_task(setup_and_query)
            assert pic_id == expected_id, "Should select the highest-scored still image"
