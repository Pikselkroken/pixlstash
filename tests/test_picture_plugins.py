import os
import tempfile

from datetime import datetime
from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session

from pixlvault.picture_utils import PictureUtils
from pixlvault.server import Server


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
                first = PictureUtils.create_picture_from_bytes(
                    image_root_path=server.vault.image_root,
                    image_bytes=img_a,
                )
                second = PictureUtils.create_picture_from_bytes(
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
                return [first.id, second.id]

            inserted_ids = server.vault.db.run_task(add_pictures)
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

            after_resp = client.get("/pictures?fields=grid")
            assert after_resp.status_code == 200
            after_pictures = {
                int(pic["id"]): pic
                for pic in after_resp.json()
                if pic.get("id") is not None
            }

            for source_id, created_id in zip(selected_ids, created_ids):
                source = after_pictures.get(int(source_id))
                created = after_pictures.get(int(created_id))
                assert source is not None
                assert created is not None
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

            scaled_after_resp = client.get("/pictures?fields=grid")
            assert scaled_after_resp.status_code == 200
            scaled_after_pictures = {
                int(pic["id"]): pic
                for pic in scaled_after_resp.json()
                if pic.get("id") is not None
            }
            for source_id, scaled_id in zip(selected_ids, scaled_ids):
                source = scaled_after_pictures.get(int(source_id))
                scaled = scaled_after_pictures.get(int(scaled_id))
                assert source is not None
                assert scaled is not None
                assert int(scaled.get("width")) == int(source.get("width")) * 2
                assert int(scaled.get("height")) == int(source.get("height")) * 2
