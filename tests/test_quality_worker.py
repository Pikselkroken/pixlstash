import os
import tempfile
from fastapi.testclient import TestClient

from pixlvault.db_models import Face, Picture, Quality
from pixlvault.picture_utils import PictureUtils
from pixlvault.server import Server
from pixlvault.worker_registry import WorkerType
from tests.utils import upload_pictures_and_wait


from PIL import Image
from io import BytesIO


def make_image(color=(0, 0, 0)):
    img = Image.new("RGB", (64, 64), color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_quality_worker_face_metrics():
    """
    Test that face quality metrics are calculated and stored per face in picture_faces.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server-config.json")
        with Server(server_config_path) as server:
            server.vault.start_workers({WorkerType.FACE})
            client = TestClient(server.api)

            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            # Upload a picture
            img_bytes = make_image((128, 128, 128))
            files = [("file", ("gray.png", img_bytes, "image/png"))]
            import_status = upload_pictures_and_wait(client, files)
            assert import_status["status"] == "completed"
            pic_id = import_status["results"][0]["picture_id"]

            # Simulate worker: insert a face row for the picture
            # Add a face using SQLModel ORM
            from pixlvault.db_models.face import Face

            face = Face(picture_id=pic_id, face_index=0, bbox=[10, 10, 54, 54])

            def add_face(session):
                session.add(face)
                session.commit()

            server.vault.db.run_task(add_face)

            # Directly set metrics using ORM
            def set_metrics(session):
                face_obj = Face.find(session, picture_id=pic_id, face_index=0)[0]
                # Create and assign a Quality object
                from pixlvault.db_models.quality import Quality

                quality = Quality(
                    sharpness=0.5,
                    edge_density=0.6,
                    contrast=0.7,
                    brightness=0.8,
                    noise_level=0.9,
                    face_id=face_obj.id,
                    picture_id=None,
                )
                face_obj.quality = quality
                session.add(quality)
                session.add(face_obj)
                session.commit()
                session.refresh(face_obj)
                return face_obj

            inserted = server.vault.db.run_task(set_metrics)
            updated = server.vault.db.run_task(
                lambda s: Face.find(s, id=inserted.id)[0]
            )

            # Check metrics via related Quality object
            assert updated.quality is not None
            assert updated.quality.sharpness == 0.5
            assert updated.quality.edge_density == 0.6
            assert updated.quality.contrast == 0.7
            assert updated.quality.brightness == 0.8
            assert updated.quality.noise_level == 0.9


def test_quality_worker_end_to_end():
    """
    End-to-end: QualityWorker should update face quality metrics in picture_faces
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        image_root = os.path.join(temp_dir, "images")
        os.makedirs(image_root, exist_ok=True)
        server_config_path = os.path.join(temp_dir, "server-config.json")
        with Server(server_config_path) as server:
            client = TestClient(server.api)
            server.vault.start_workers({WorkerType.FACE})

            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            # Upload a real test image with a face
            with open(
                os.path.join(os.path.dirname(__file__), "../pictures/TaggerTest.png"),
                "rb",
            ) as f:
                img_bytes = f.read()
            files = [("file", ("TaggerTest.png", img_bytes, "image/png"))]
            import_status = upload_pictures_and_wait(client, files)
            assert import_status["status"] == "completed"
            pic_id = import_status["results"][0]["picture_id"]

            imported_picture = server.vault.db.run_task(
                lambda s: Picture.find(
                    s, id=pic_id, select_fields=Picture.metadata_fields()
                )[0]
            )
            assert imported_picture.file_path, (
                "Expected imported quality test picture to have file_path."
            )
            imported_path = PictureUtils.resolve_picture_path(
                server.vault.db.image_root, imported_picture.file_path
            )
            assert os.path.exists(imported_path), (
                "Expected imported quality test picture file to exist on disk."
            )

            # Debug dump of picture_faces table after face detection
            print("\n--- DEBUG DUMP: picture_faces after face detection ---")
            faces = server.vault.db.run_task(lambda s: Face.find(s, picture_id=pic_id))
            for face in faces:
                print(face)
            print("--- END DEBUG DUMP ---\n")

            # Now start the quality workers to compute metrics
            q_future = server.vault.get_worker_future(
                WorkerType.QUALITY, Picture, pic_id, "quality"
            )
            fq_future = server.vault.get_worker_future(
                WorkerType.FACE_QUALITY, Face, faces[0].id, "quality"
            )

            server.vault.start_workers({WorkerType.QUALITY, WorkerType.FACE_QUALITY})

            assert q_future.result(timeout=60), "QualityWorker did not finish in time"

            assert fq_future.result(timeout=60), (
                "FaceQualityWorker did not finish in time"
            )

            # Retrieve face data
            updated = server.vault.db.run_task(
                lambda s: Face.find(s, id=faces[0].id)[0]
            )

            # All face quality metrics should be set (not None) if worker is correct
            for key in Quality.quality_metric_fields():
                assert hasattr(updated.quality, key), (
                    f"Face.quality missing attribute {key}"
                )
                assert getattr(updated.quality, key) is not None, (
                    f"Expected {key} to be set, got None"
                )
                assert getattr(updated.quality, key) >= 0.0, (
                    f"Expected {key} to be non-negative, got {getattr(updated.quality, key)}"
                )
