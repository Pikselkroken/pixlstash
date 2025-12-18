import gc
import json
import pytest
import random
import time
import tempfile
import os
from fastapi.testclient import TestClient
from pixlvault.server import Server

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
        config_path = os.path.join(temp_dir, "config.json")
        config = Server.create_config(
            default_device="cpu",
            image_roots=[image_root],
            selected_image_root=image_root,
        )
        with open(config_path, "w") as f:
            f.write(json.dumps(config, indent=2))
        server_config_path = os.path.join(temp_dir, "server-config.json")

        with Server(config_path, server_config_path) as server:
            server.vault.import_default_data(True)
            client = TestClient(server.api)
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
