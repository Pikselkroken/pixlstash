import gc
import pytest
import random
import time
import tempfile
import os
import datetime
from fastapi.testclient import TestClient
from pixlstash.server import Server
from pixlstash.utils.image_processing.image_utils import ImageUtils

BACKEND_URL = "http://localhost:9537"


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"sort": "SCORE", "descending": True},
        {"sort": "DATE", "descending": False},
    ],
)
def test_order_stability(params):
    """
    For each set of parameters, repeatedly query the backend and check that the returned image IDs are always in the same order.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        image_root = os.path.join(temp_dir, "images")
        os.makedirs(image_root, exist_ok=True)
        server_config_path = os.path.join(temp_dir, "server-config.json")

        with Server(server_config_path) as server:
            server.vault.import_default_data(True)
            client = TestClient(server.api)

            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200
            print("Login Response: ", resp.json())  # Should include "session_id"
            assert "session_id" in client.cookies, (
                "session_id cookie not set after login"
            )

            resp = client.get("/protected")
            print("Protected Response: ", resp.json())

            first_ids = []

            for i in range(0, 3):
                time.sleep(random.uniform(0.01, 0.05))
                resp = client.get("/pictures", params={**params})
                assert resp.status_code == 200, (
                    f"Backend returned {resp.status_code} for params {params}"
                )
                data = resp.json()
                ids = [img["id"] for img in data if "id" in img]
                if i == 0:
                    first_ids = ids
                else:
                    assert ids == first_ids, (
                        f"Order not stable for params {params}: {ids} != {first_ids}"
                    )
    gc.collect()


@pytest.mark.parametrize(
    "sort_key,field",
    [
        ("DATE", "created_at"),
        ("IMPORTED_AT", "imported_at"),
    ],
)
def test_date_sort_uses_seconds(sort_key, field):
    """
    Verify that DATE and IMPORTED_AT sorts respect full second-level precision.

    Even though the display layer drops seconds (e.g. shows "2024-01-15 10:30"),
    the backend must still sort by the complete timestamp including seconds so
    that images captured/imported within the same minute appear in the correct
    order.
    """
    # source image to copy — any small valid image file in the test-data set
    logo_src = os.path.join(os.path.dirname(os.path.dirname(__file__)), "Logo.png")
    if not os.path.exists(logo_src):
        pytest.skip("Logo.png not available for test image creation")

    # Three timestamps within the same minute but at different seconds:
    # earliest → middle → latest
    base = datetime.datetime(2022, 6, 15, 10, 30, 0)
    ts_early = base.replace(second=1)
    ts_mid = base.replace(second=30)
    ts_late = base.replace(second=59)

    with tempfile.TemporaryDirectory() as temp_dir:
        image_root = os.path.join(temp_dir, "images")
        os.makedirs(image_root, exist_ok=True)
        server_config_path = os.path.join(temp_dir, "server-config.json")

        with Server(server_config_path) as server:

            def _add_picture(session, created_dt, imported_dt):
                pic = ImageUtils.create_picture_from_file(
                    image_root_path=image_root,
                    source_file_path=logo_src,
                    picture_uuid=None,
                )
                pic.created_at = created_dt
                pic.imported_at = imported_dt
                session.add(pic)
                session.commit()
                session.refresh(pic)
                return pic.id

            id_early = server.vault.db.run_task(_add_picture, ts_early, ts_early)
            id_mid = server.vault.db.run_task(_add_picture, ts_mid, ts_mid)
            id_late = server.vault.db.run_task(_add_picture, ts_late, ts_late)

            client = TestClient(server.api)
            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200

            # Descending: latest first → ts_late, ts_mid, ts_early
            resp = client.get(
                "/pictures", params={"sort": sort_key, "descending": "true"}
            )
            assert resp.status_code == 200
            ids_desc = [
                img["id"]
                for img in resp.json()
                if img["id"] in (id_early, id_mid, id_late)
            ]
            assert ids_desc == [id_late, id_mid, id_early], (
                f"Expected descending second-sort [{id_late}, {id_mid}, {id_early}], got {ids_desc}"
            )

            # Ascending: earliest first → ts_early, ts_mid, ts_late
            resp = client.get(
                "/pictures", params={"sort": sort_key, "descending": "false"}
            )
            assert resp.status_code == 200
            ids_asc = [
                img["id"]
                for img in resp.json()
                if img["id"] in (id_early, id_mid, id_late)
            ]
            assert ids_asc == [id_early, id_mid, id_late], (
                f"Expected ascending second-sort [{id_early}, {id_mid}, {id_late}], got {ids_asc}"
            )

    gc.collect()
