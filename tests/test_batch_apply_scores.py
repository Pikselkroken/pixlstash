import os
import tempfile

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image
from io import BytesIO
from sqlmodel import select

from pixlstash.db_models.picture import Picture
from pixlstash.server import Server
from tests.utils import upload_pictures_and_wait


def _png_bytes(seed: int) -> bytes:
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, (128, 128, 3), dtype=np.uint8)
    img = Image.fromarray(arr)
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_batch_apply_scores_updates_only_unscored_pictures():
    """Batch endpoint should skip already-scored pictures when only_unscored is true."""

    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            login_resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert login_resp.status_code == 200

            first_import = upload_pictures_and_wait(
                client,
                [("file", ("batch-score-a.png", _png_bytes(1), "image/png"))],
            )
            second_import = upload_pictures_and_wait(
                client,
                [("file", ("batch-score-b.png", _png_bytes(2), "image/png"))],
            )

            pic_a = first_import["results"][0]["picture_id"]
            pic_b = second_import["results"][0]["picture_id"]

            set_existing_resp = client.patch(f"/pictures/{pic_a}", json={"score": 2})
            assert set_existing_resp.status_code == 200

            apply_resp = client.post(
                "/pictures/apply-scores",
                json={
                    "scores": {
                        str(pic_a): 5,
                        str(pic_b): 3,
                        "999999999": 4,
                    },
                    "only_unscored": True,
                },
            )
            assert apply_resp.status_code == 200
            payload = apply_resp.json()
            assert payload["status"] == "success"
            assert payload["updated_ids"] == [pic_b]
            assert payload["updated_count"] == 1
            assert payload["skipped_ids"] == [pic_a]
            assert payload["skipped_count"] == 1
            assert payload["missing_ids"] == [999999999]
            assert payload["missing_count"] == 1
            assert payload["reset_triggered"] is False

            meta_a = client.get(f"/pictures/{pic_a}/metadata")
            meta_b = client.get(f"/pictures/{pic_b}/metadata")
            assert meta_a.status_code == 200
            assert meta_b.status_code == 200
            assert meta_a.json().get("score") == 2
            assert meta_b.json().get("score") == 3


def test_batch_apply_scores_resets_smart_scores_for_anchor_transitions():
    """A batch crossing anchor boundaries should trigger one smart-score reset pass."""

    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            login_resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert login_resp.status_code == 200

            first_import = upload_pictures_and_wait(
                client,
                [("file", ("batch-anchor-a.png", _png_bytes(3), "image/png"))],
            )
            second_import = upload_pictures_and_wait(
                client,
                [("file", ("batch-anchor-b.png", _png_bytes(4), "image/png"))],
            )

            pic_a = first_import["results"][0]["picture_id"]
            pic_b = second_import["results"][0]["picture_id"]

            def seed_smart_scores(session, ids: list[int]) -> None:
                for pid in ids:
                    pic = session.get(Picture, pid)
                    assert pic is not None
                    pic.smart_score = 4.0
                    session.add(pic)
                session.commit()

            server.vault.db.run_task(seed_smart_scores, [pic_a, pic_b])

            apply_resp = client.post(
                "/pictures/apply-scores",
                json={
                    "scores": {
                        str(pic_a): 5,
                        str(pic_b): 4,
                    },
                    "only_unscored": True,
                },
            )
            assert apply_resp.status_code == 200
            payload = apply_resp.json()
            assert payload["updated_count"] == 2
            assert payload["reset_triggered"] is True

            def fetch_score_snapshot(
                session, ids: list[int]
            ) -> dict[int, tuple[int | None, float | None]]:
                rows = session.exec(select(Picture).where(Picture.id.in_(ids))).all()
                return {
                    int(row.id): (row.score, row.smart_score)
                    for row in rows
                    if row.id is not None
                }

            snapshot = server.vault.db.run_task(fetch_score_snapshot, [pic_a, pic_b])
            assert snapshot[pic_a][0] == 5
            assert snapshot[pic_b][0] == 4
            assert snapshot[pic_a][1] is None
            assert snapshot[pic_b][1] is None


def test_batch_apply_scores_accepts_zero_score_value():
    """Batch endpoint should accept score=0 to match single PATCH toggle behavior."""

    with tempfile.TemporaryDirectory() as temp_dir:
        server_config_path = os.path.join(temp_dir, "server_config.json")
        with Server(server_config_path=server_config_path) as server:
            client = TestClient(server.api)

            login_resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert login_resp.status_code == 200

            imported = upload_pictures_and_wait(
                client,
                [("file", ("batch-score-zero.png", _png_bytes(5), "image/png"))],
            )
            pic_id = imported["results"][0]["picture_id"]

            set_existing_resp = client.patch(f"/pictures/{pic_id}", json={"score": 3})
            assert set_existing_resp.status_code == 200

            apply_resp = client.post(
                "/pictures/apply-scores",
                json={
                    "scores": {str(pic_id): 0},
                    "only_unscored": False,
                },
            )
            assert apply_resp.status_code == 200
            payload = apply_resp.json()
            assert payload["updated_count"] == 1
            assert payload["updated_ids"] == [pic_id]

            meta = client.get(f"/pictures/{pic_id}/metadata")
            assert meta.status_code == 200
            assert meta.json().get("score") == 0
