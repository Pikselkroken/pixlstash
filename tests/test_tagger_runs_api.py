"""Tests for the tagger-runs ingest/list API (PixlTagger → PixlStash stats push)."""

import gc
import json
import os
import tempfile

from fastapi.testclient import TestClient

from pixlstash.server import Server


def _setup():
    temp_dir = tempfile.TemporaryDirectory()
    os.makedirs(os.path.join(temp_dir.name, "images"), exist_ok=True)
    cfg = os.path.join(temp_dir.name, "server-config.json")
    with open(cfg, "w") as f:
        f.write(json.dumps({"port": 8000}))
    server = Server(cfg)
    client = TestClient(server.api)
    assert (
        client.post(
            "/login", json={"username": "testuser", "password": "testpassword"}
        ).status_code
        == 200
    )
    return temp_dir, client, server


def _report(run, verdict, macro):
    # Mirrors the shape of pixltagger's report.json.
    return {
        "payload": {
            "run": run,
            "accepted": "out135",
            "verdict": verdict,
            "recommend": "hold" if verdict == "regressed" else "promote",
            "deltas": {"anomaly_macro_f1": {"accepted": 0.71, "candidate": macro}},
            "per_tag": [{"tag": "malformed hand", "f1": 0.53}],
        },
        "narrative": {"headline": "test"},
    }


def test_ingest_lists_and_upserts_runs():
    temp_dir, client, server = _setup()
    try:
        # A rejected run is stored just like an accepted one (full history).
        r = client.post("/tagger-runs", json=_report("run-140", "regressed", 0.692))
        assert r.status_code == 200
        body = r.json()
        assert body["run"] == "run-140"
        assert body["verdict"] == "regressed"
        assert abs(body["anomaly_macro_f1"] - 0.692) < 1e-6
        assert body["report"]["payload"]["per_tag"][0]["tag"] == "malformed hand"

        client.post("/tagger-runs", json=_report("run-141", "improved", 0.715))
        rows = client.get("/tagger-runs").json()
        assert {row["run"] for row in rows} == {"run-140", "run-141"}

        # Re-pushing a run upserts (no duplicate, fields updated).
        client.post("/tagger-runs", json=_report("run-140", "improved", 0.705))
        rows = client.get("/tagger-runs").json()
        assert len(rows) == 2
        run140 = next(r for r in rows if r["run"] == "run-140")
        assert run140["verdict"] == "improved"
        assert abs(run140["anomaly_macro_f1"] - 0.705) < 1e-6
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_ingest_requires_run():
    temp_dir, client, server = _setup()
    try:
        resp = client.post("/tagger-runs", json={"payload": {"verdict": "improved"}})
        assert resp.status_code == 400
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()
