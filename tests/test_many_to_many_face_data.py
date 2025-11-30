import os
import tempfile
import gc
import sys

from PIL import Image
from io import BytesIO
from fastapi.testclient import TestClient

from pixlvault.server import Server
from pixlvault.db_models import Face


def make_image(color=(0, 0, 0)):
    img = Image.new("RGB", (64, 64), color)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_many_to_many_face_data():
    """
    Test that multiple characters can be associated with a picture, and that face data is stored per character.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "config.json")
        server_config_path = os.path.join(temp_dir, "server-config.json")
        with Server(config_path, server_config_path) as server:
            client = TestClient(server.api)

            # Create two characters
            resp1 = client.post(
                "/characters", json={"name": "Alice", "description": "Test char 1"}
            )
            resp2 = client.post(
                "/characters", json={"name": "Bob", "description": "Test char 2"}
            )
            assert resp1.status_code == 200
            assert resp2.status_code == 200
            char1 = resp1.json()["character"]["id"]
            char2 = resp2.json()["character"]["id"]

            print(
                f"Created characters: Alice id={char1}, Bob id={char2}",
                file=sys.stdout,
                flush=True,
            )
            assert char1 is not None
            assert char2 is not None

            # Upload a picture
            img_bytes = make_image((255, 0, 0))
            files = [("file", ("red.png", img_bytes, "image/png"))]
            r = client.post("/pictures", files=files)
            assert r.status_code == 200
            pic_id = r.json()["results"][0]["picture_id"]

            # Simulate worker: insert two face rows for the picture
            server.vault.db.run_task(
                lambda session: (
                    session.add(
                        Face(picture_id=pic_id, face_index=0, bbox=[1, 2, 3, 4])
                    ),
                    session.commit(),
                )
            )
            server.vault.db.run_task(
                lambda session: (
                    session.add(
                        Face(picture_id=pic_id, face_index=1, bbox=[10, 20, 30, 40])
                    ),
                    session.commit(),
                )
            )

            # Now associate Alice and Bob with the faces by setting character_id
            server.vault.db.run_task(
                lambda session: (
                    session.query(Face)
                    .filter(Face.picture_id == pic_id, Face.face_index == 0)
                    .update({"character_id": int(char1)}),
                    session.commit(),
                )
            )
            server.vault.db.run_task(
                lambda session: (
                    session.query(Face)
                    .filter(Face.picture_id == pic_id, Face.face_index == 1)
                    .update({"character_id": int(char2)}),
                    session.commit(),
                )
            )

            # Retrieve and check face data for each character/face
            def get_face_data(session, picture_id, face_index):
                return Face.find(session, picture_id=picture_id, face_index=face_index)

            faces1 = server.vault.db.run_task(get_face_data, pic_id, 0)
            faces2 = server.vault.db.run_task(get_face_data, pic_id, 1)

            face1 = faces1[0] if len(faces1) > 0 else None
            face2 = faces2[0] if len(faces2) > 0 else None

            assert face1 is not None, "Face 1 not found"
            assert face2 is not None, "Face 2 not found"

            assert face1.bbox == [1, 2, 3, 4]
            assert face1.character_id == int(char1)
            assert face2.bbox == [10, 20, 30, 40]
            assert face2.character_id == int(char2)
            # Remove Bob from the picture by setting character_id to None and check association is gone
            server.vault.db.run_task(
                lambda session: (
                    session.query(Face)
                    .filter(Face.picture_id == pic_id, Face.face_index == 1)
                    .update({"character_id": None}),
                    session.commit(),
                )
            )
            face2_after = server.vault.db.run_task(
                lambda session: session.query(Face)
                .filter(Face.picture_id == pic_id, Face.face_index == 1)
                .first()
            )
            assert face2_after.character_id is None, (
                f"Expected character_id None after removal, got: {face2_after}"
            )
    gc.collect()
