import gc
import os
import tempfile
from sqlmodel import select
from PIL import Image
from io import BytesIO

from fastapi.testclient import TestClient
from pixlvault.db_models import Picture
from pixlvault.pixl_logging import get_logger
from pixlvault.server import Server
from pixlvault.worker_registry import WorkerType
from tests.utils import upload_pictures_and_wait

logger = get_logger(__name__)


def make_image(color=(0, 0, 0)):
    img = Image.new("RGB", (64, 64), color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_tag_worker_picture_tags():
    """
    Test that tags are calculated and stored per Picture in picture_tags.
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

            # Simulate description (required for tag worker)
            def set_description(session):
                pic = session.exec(select(Picture).where(Picture.id == pic_id)).one()
                pic.description = "A test image for tagging."
                session.add(pic)
                session.commit()
                session.refresh(pic)
                return pic

            server.vault.db.run_task(set_description)

            future = server.vault.get_worker_future(
                WorkerType.TAGGER, Picture, pic_id, "tags"
            )

            server.vault.start_workers({WorkerType.TAGGER})

            assert future.result(timeout=60), "TagWorker did not finish in time"
            server.vault.stop_workers({WorkerType.TAGGER})

            # Check tags via related Tag object
            def get_tags(session):
                pic = session.exec(select(Picture).where(Picture.id == pic_id)).one()
                return [tag.tag for tag in pic.tags]

            tags = server.vault.db.run_task(get_tags)
            assert isinstance(tags, list)
            assert len(tags) > 0, "No tags were generated for the picture."
            for tag in tags:
                assert isinstance(tag, str)
                assert len(tag) > 0


def test_tag_worker_end_to_end():
    """
    End-to-end: TagWorker should update picture tags in picture_tags.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        image_root = os.path.join(temp_dir, "images")
        os.makedirs(image_root, exist_ok=True)
        server_config_path = os.path.join(temp_dir, "server-config.json")
        with Server(server_config_path) as server:
            server.vault.start_workers({WorkerType.FACE})

            client = TestClient(server.api)

            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            # Upload a real test image
            with open(
                os.path.join(os.path.dirname(__file__), "../pictures/TaggerTest.png"),
                "rb",
            ) as f:
                img_bytes = f.read()
            files = [("file", ("TaggerTest.png", img_bytes, "image/png"))]
            import_status = upload_pictures_and_wait(client, files)
            assert import_status["status"] == "completed"
            pic_id = import_status["results"][0]["picture_id"]

            # Simulate description (required for tag worker)
            def set_description(session):
                pic = session.exec(select(Picture).where(Picture.id == pic_id)).one()
                pic.description = "A test image for tagging."
                session.add(pic)
                session.commit()
                session.refresh(pic)
                return pic

            server.vault.db.run_task(set_description)

            # Start the tag worker to compute tags
            t_future = server.vault.get_worker_future(
                WorkerType.TAGGER, Picture, pic_id, "tags"
            )
            server.vault.start_workers({WorkerType.TAGGER})
            assert t_future.result(timeout=60), "TagWorker did not finish in time"
            server.vault.stop_workers({WorkerType.TAGGER})

            # Retrieve picture tags
            def get_tags(session):
                pic = session.exec(select(Picture).where(Picture.id == pic_id)).one()
                return [tag.tag for tag in pic.tags]

            tags = server.vault.db.run_task(get_tags)
            assert isinstance(tags, list)
            assert len(tags) > 0, "No tags were generated for the picture."
            for tag in tags:
                assert isinstance(tag, str)
                assert len(tag) > 0


def test_tagger_worker_adds_tags():
    """Test that uploading TaggerTest.png results in tags being added by the tag worker."""
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server-config.json")

        # Copy TaggerTest.png into temp dir
        src_img = os.path.join(os.path.dirname(__file__), "../pictures/TaggerTest.png")
        with Server(server_config_path=server_config_path) as server:
            server.vault.start_workers({WorkerType.FACE})

            client = TestClient(server.api)

            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            # Upload TaggerTest.png as a new picture
            with open(src_img, "rb") as f:
                files = [("file", ("TaggerTest.png", f.read(), "image/png"))]
                import_status = upload_pictures_and_wait(client, files)
            assert import_status["status"] == "completed"
            assert import_status["results"][0]["status"] == "success"
            picture_id = import_status["results"][0]["picture_id"]

            logger.info(f"Uploaded TaggerTest.png with picture ID {picture_id}")

            future = server.vault.get_worker_future(
                WorkerType.TAGGER, Picture, picture_id, "tags"
            )
            server.vault.start_workers(
                {
                    WorkerType.TAGGER,
                    WorkerType.DESCRIPTION,
                    WorkerType.SMART_SCORE_SCRAPHEAP,
                }
            )
            assert future.result(timeout=60), "Tagger worker did not finish in time"
            server.vault.stop_workers(
                {
                    WorkerType.TAGGER,
                    WorkerType.DESCRIPTION,
                    WorkerType.SMART_SCORE_SCRAPHEAP,
                }
            )

            get_pic_resp = client.get(f"/pictures/{picture_id}/metadata")
            assert get_pic_resp.status_code == 200, (
                f"Failed to get picture metadata {picture_id} with error {get_pic_resp.text}"
            )
            pic_info = get_pic_resp.json()
            assert pic_info.get("id") == picture_id, (
                f"Retrieved picture ID {pic_info.get('id')} does not match expected {picture_id}"
            )

            # Wait for tag worker to process
            get_resp = client.get(f"/pictures/{picture_id}/tags")
            assert get_resp.status_code == 200, (
                f"Failed to get tags for picture {picture_id} with error {get_resp.text}"
            )
            pic_info = get_resp.json()
            found_tags = pic_info.get("tags", [])
            pic_id = pic_info.get("id")
            assert found_tags, (
                f"Tagger worker did not add tags to TaggerTest.png {pic_id} after waiting."
            )
    gc.collect()
