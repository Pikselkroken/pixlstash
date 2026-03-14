import os
import tempfile
import numpy as np

from datetime import datetime
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session, select

from pixlstash.db_models.picture_set import PictureSet, PictureSetMember
from pixlstash.utils.image_processing.image_utils import ImageUtils
from pixlstash.server import Server


def _make_png_bytes(color: tuple[int, int, int]) -> bytes:
    img = Image.new("RGB", (24, 24), color=color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_picture_plugins_list_and_run_colour_filter():
    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)
            login_resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert login_resp.status_code == 200

            img_a = _make_png_bytes((255, 40, 40))
            img_b = _make_png_bytes((40, 255, 40))

            def add_pictures(session: Session):
                first = ImageUtils.create_picture_from_bytes(
                    image_root_path=server.vault.image_root,
                    image_bytes=img_a,
                )
                second = ImageUtils.create_picture_from_bytes(
                    image_root_path=server.vault.image_root,
                    image_bytes=img_b,
                )
                now = datetime.utcnow()
                first.imported_at = now
                second.imported_at = now
                session.add(first)
                session.add(second)
                session.commit()
                session.refresh(first)
                session.refresh(second)
                picture_set = PictureSet(name="Plugin Test Set", description="test")
                session.add(picture_set)
                session.commit()
                session.refresh(picture_set)
                session.add(
                    PictureSetMember(set_id=picture_set.id, picture_id=first.id)
                )
                session.add(
                    PictureSetMember(set_id=picture_set.id, picture_id=second.id)
                )
                session.commit()
                return [first.id, second.id, picture_set.id]

            inserted = server.vault.db.run_task(add_pictures)
            inserted_ids = inserted[:2]
            created_set_id = inserted[2]
            assert len(inserted_ids) == 2

            pictures_resp = client.get("/pictures?fields=grid")
            assert pictures_resp.status_code == 200
            pictures = pictures_resp.json()
            assert pictures and len(pictures) >= 2
            selected_ids = sorted(inserted_ids)

            plugins_resp = client.get("/pictures/plugins")
            assert plugins_resp.status_code == 200
            plugins_payload = plugins_resp.json()
            plugins = plugins_payload.get("plugins") or []
            names = {plugin.get("name") for plugin in plugins}
            assert "colour_filter" in names
            assert "scaling" in names
            assert "brightness_contrast" in names
            assert "blur_sharpen" in names
            colour_schema = next(
                (plugin for plugin in plugins if plugin.get("name") == "colour_filter"),
                None,
            )
            assert colour_schema is not None
            assert colour_schema.get("supports_images") is True
            assert colour_schema.get("supports_videos") is True
            brightness_contrast_schema = next(
                (
                    plugin
                    for plugin in plugins
                    if plugin.get("name") == "brightness_contrast"
                ),
                None,
            )
            assert brightness_contrast_schema is not None
            assert brightness_contrast_schema.get("supports_images") is True
            assert brightness_contrast_schema.get("supports_videos") is True
            blur_sharpen_schema = next(
                (plugin for plugin in plugins if plugin.get("name") == "blur_sharpen"),
                None,
            )
            assert blur_sharpen_schema is not None
            assert blur_sharpen_schema.get("supports_images") is True
            assert blur_sharpen_schema.get("supports_videos") is True

            run_resp = client.post(
                "/pictures/plugins/colour_filter",
                json={
                    "picture_ids": selected_ids,
                    "parameters": {"mode": "sepia"},
                },
            )
            assert run_resp.status_code == 200, run_resp.text
            run_payload = run_resp.json()
            assert run_payload.get("status") == "success"
            created_ids = run_payload.get("created_picture_ids") or []
            assert len(created_ids) == 2

            def fetch_set_members(session: Session, set_id: int):
                members = session.exec(
                    select(PictureSetMember).where(PictureSetMember.set_id == set_id)
                ).all()
                return {int(member.picture_id) for member in members}

            set_member_ids = server.vault.db.run_task(fetch_set_members, created_set_id)
            for created_id in created_ids:
                assert int(created_id) in set_member_ids

            # Fetch pictures individually to avoid stack_leaders_only filtering
            # (fields=grid hides non-leader stack members, which includes the
            # original source pictures after the plugin pushes them to position 1).
            for source_id, created_id in zip(selected_ids, created_ids):
                source_resp = client.get(f"/pictures/{source_id}/metadata")
                assert source_resp.status_code == 200, source_resp.text
                source = source_resp.json()
                created_resp = client.get(f"/pictures/{created_id}/metadata")
                assert created_resp.status_code == 200, created_resp.text
                created = created_resp.json()
                assert source.get("stack_id") is not None
                assert created.get("stack_id") == source.get("stack_id")
                assert int(created.get("stack_position")) == 0

            scale_resp = client.post(
                "/pictures/plugins/scaling",
                json={
                    "picture_ids": selected_ids,
                    "parameters": {
                        "algorithm": "lanczos",
                        "scale_factor": "2.0",
                    },
                },
            )
            assert scale_resp.status_code == 200, scale_resp.text
            scale_payload = scale_resp.json()
            assert scale_payload.get("status") == "success"
            scaled_ids = scale_payload.get("created_picture_ids") or []
            assert len(scaled_ids) == 2

            for source_id, scaled_id in zip(selected_ids, scaled_ids):
                source_resp = client.get(f"/pictures/{source_id}/metadata")
                assert source_resp.status_code == 200, source_resp.text
                source = source_resp.json()
                scaled_resp = client.get(f"/pictures/{scaled_id}/metadata")
                assert scaled_resp.status_code == 200, scaled_resp.text
                scaled = scaled_resp.json()
                assert int(scaled.get("width")) == int(source.get("width")) * 2
                assert int(scaled.get("height")) == int(source.get("height")) * 2

            brightness_resp = client.post(
                "/pictures/plugins/brightness_contrast",
                json={
                    "picture_ids": selected_ids,
                    "parameters": {
                        "brightness": 1.1,
                        "contrast": 1.2,
                    },
                },
            )
            assert brightness_resp.status_code == 200, brightness_resp.text
            brightness_payload = brightness_resp.json()
            assert brightness_payload.get("status") == "success"
            brightness_ids = brightness_payload.get("created_picture_ids") or []
            assert len(brightness_ids) == 2

            blur_resp = client.post(
                "/pictures/plugins/blur_sharpen",
                json={
                    "picture_ids": selected_ids,
                    "parameters": {
                        "mode": "blur",
                        "strength": 1.0,
                    },
                },
            )
            assert blur_resp.status_code == 200, blur_resp.text
            blur_payload = blur_resp.json()
            assert blur_payload.get("status") == "success"
            blur_output_ids = blur_payload.get("output_picture_ids") or []
            assert len(blur_output_ids) == 2


def test_create_picture_from_bytes_preserves_video_extension_format(monkeypatch):
    class _FakeVideoCapture:
        def __init__(self, _path):
            self._read = False

        def read(self):
            if self._read:
                return False, None
            self._read = True
            frame = np.zeros((32, 48, 3), dtype=np.uint8)
            return True, frame

        def release(self):
            return None

    monkeypatch.setattr(
        "pixlstash.utils.image_processing.video_utils.cv2.VideoCapture",
        _FakeVideoCapture,
    )

    with tempfile.TemporaryDirectory() as temp_dir:
        picture = ImageUtils.create_picture_from_bytes(
            image_root_path=temp_dir,
            image_bytes=b"not-an-image",
            picture_uuid="example.webm",
        )

        assert picture.format == "WEBM"
        assert picture.file_path is not None
        assert picture.file_path.endswith(".webm")
