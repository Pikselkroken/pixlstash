from datetime import datetime
import gc
import json
import numpy as np
import os
import tempfile

from fastapi.testclient import TestClient
from sqlmodel import select

from pixlvault.server import Server
from pixlvault.db_models import Picture, Face, Character
from pixlvault.database import DBPriority


def setup_server():
    temp_dir = tempfile.TemporaryDirectory()
    image_root = os.path.join(temp_dir.name, "images")
    os.makedirs(image_root, exist_ok=True)
    server_config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(server_config_path, "w") as f:
        f.write(json.dumps({"port": 0}))
    server = Server(server_config_path)
    client = TestClient(server.api)

    # Login
    client.post("/login", json={"username": "testuser", "password": "testpassword"})
    return temp_dir, client, server


def test_smart_score_consistency():
    temp_dir, client, server = setup_server()
    try:
        # data setup
        def setup_data(session):
            emb_array = np.random.rand(128).astype(np.float32)
            emb_bytes = emb_array.tobytes()

            # Picture 1
            p1 = Picture(
                file_path="p1.jpg",
                image_embedding=emb_bytes,
                aesthetic_score=5.5,
                score=0,
                imported_at=datetime.now(),
            )
            session.add(p1)

            # Picture 2
            p2 = Picture(
                file_path="p2.jpg",
                image_embedding=emb_bytes,
                aesthetic_score=4.5,
                score=0,
                imported_at=datetime.now(),
            )
            session.add(p2)

            session.commit()
            session.refresh(p1)
            session.refresh(p2)

            # Character
            c = Character(name="CharC")
            session.add(c)
            session.commit()
            session.refresh(c)

            # Face for P1
            f = Face(picture_id=p1.id, bbox=[0, 0, 10, 10])
            session.add(f)
            session.commit()
            session.refresh(f)

            return p1.id, p2.id, c.id

        p1_id, p2_id, c_id = server.vault.db.run_task(
            setup_data, priority=DBPriority.IMMEDIATE
        )

        def fetch_embeddings(session, ids):
            rows = session.exec(
                select(Picture.id, Picture.image_embedding).where(Picture.id.in_(ids))
            ).all()
            return rows

        emb_rows = server.vault.db.run_task(
            fetch_embeddings, [p1_id, p2_id], priority=DBPriority.IMMEDIATE
        )
        for pid, emb in emb_rows:
            assert emb is not None, f"image_embedding missing for picture id={pid}"
            assert len(emb) > 0, f"image_embedding empty for picture id={pid}"

        # Helper to get score for a pic from response
        def get_score(resp_json, pid):
            for p in resp_json:
                if p["id"] == pid:
                    return p.get("smartScore")
            return None

        # 1. Query ALL
        resp_all = client.get("/pictures?sort=SMART_SCORE&descending=true")
        assert resp_all.status_code == 200
        score_all_p1 = get_score(resp_all.json(), p1_id)

        # 2. Query Character specific (filter by char)
        # Note: server.py needs to find p1 when filtering by char c_id
        # Normally logic joins Face and Face.character_id.
        # But we need to assign the face to the character for it to appear in the filter?
        # server.py logic: query.join(Face).where(Face.character_id == cid)
        # We need to manually update face.character_id for it to appear in the list?
        # NO, "candidates" filtering logic in _fetch_smart_score_data matches list_pictures.
        # Let's check logic in _fetch_smart_score_data:
        # elif character_id and ...: query = query.join(Face).where(Face.character_id == cid)

        # So for P1 to appear in query(c_id), we MUST set Face.character_id.
        def assign_face(session):
            faces = session.exec(select(Face).where(Face.picture_id == p1_id)).all()
            for f in faces:
                f.character_id = c_id
                session.add(f)
            session.commit()

        server.vault.db.run_task(assign_face, priority=DBPriority.IMMEDIATE)

        resp_char = client.get(
            f"/pictures?character_id={c_id}&sort=SMART_SCORE&descending=true"
        )
        assert resp_char.status_code == 200
        score_char_p1 = get_score(resp_char.json(), p1_id)

        # 3. Assertions
        assert score_all_p1 is not None
        assert score_char_p1 is not None

        # They should be equal (or very close float)
        assert abs(score_all_p1 - score_char_p1) < 0.0001

        # Also, check that score is actually using likeness.
        # Base aesthetic is same for P1 and P2 (5.0). embeddings are random same.
        # P1 has likeness 0.99. P2 has 0.0.
        # P1 score should be > P2 score.
        score_all_p2 = get_score(resp_all.json(), p2_id)
        assert score_all_p1 > score_all_p2

    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()
