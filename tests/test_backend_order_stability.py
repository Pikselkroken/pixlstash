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
        {"sort": "ORDER BY score DESC"},
        {"sort": "ORDER BY score ASC"},
        {"primary_character_id": ""},
        {"query": ""},
        {"limit": 20, "offset": 0},
        {"limit": 20, "offset": 10},
        {"sort": "ORDER BY created_at DESC"},
        {"sort": "ORDER BY created_at ASC"},
    ],
)
def test_order_stability(params):
    """
    For each set of parameters, repeatedly query the backend and check that the returned image IDs are always in the same order.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = os.path.join(temp_dir, "config.json")
        config = Server.create_config(default_device="cpu")
        with open(config_path, "w") as f:
            f.write(json.dumps(config, indent=2))
        server_config_path = os.path.join(temp_dir, "server-config.json")

        with Server(config_path, server_config_path) as server:
            server.vault.import_default_data(True)
            client = TestClient(server.api)
            results = []
            for _ in range(5):
                time.sleep(random.uniform(0.01, 0.05))
                resp = client.get("/pictures", params={**params, "info": "true"})
                assert resp.status_code == 200, (
                    f"Backend returned {resp.status_code} for params {params}"
                )
                data = resp.json()
                ids = [img["id"] for img in data if "id" in img]
                results.append(ids)
            first = results[0]
            for r in results[1:]:
                assert r == first, (
                    f"Order not stable for params {params}: {first} != {r}"
                )
