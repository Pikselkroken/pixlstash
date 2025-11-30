import pytest
from fastapi.testclient import TestClient
from pixlvault.server import Server
import tempfile
import os
import shutil
import time
from pixlvault.picture_tagger import PictureTagger


@pytest.fixture
def test_server():
    # Force CPU for all models during test
    PictureTagger.FORCE_CPU = True
    tmpdir = tempfile.mkdtemp()
    config_path = os.path.join(tmpdir, "config.json")
    server_config_path = os.path.join(tmpdir, "server_config.json")
    server = Server(config_path, server_config_path)
    client = TestClient(server.api)
    yield client
    shutil.rmtree(tmpdir)


def test_chat_history_save_and_load(test_server):
    client = test_server

    resp = client.post("/chat", params={"character_id": 0})
    assert resp.status_code == 200, f"Failed to create chat: {resp.text}"
    conversation_id = resp.json().get("conversation_id")
    assert conversation_id == 1

    payload = {
        "conversation_id": conversation_id,
        "timestamp": int(time.time()),
        "role": "user",
        "content": "Hello!",
        "picture_id": 42,
    }
    # Save a message
    resp = client.post("/chat/message", json=payload)
    assert resp.status_code == 200, f"Failed to save message: {resp.text}"
    assert resp.json()["status"] == "ok"
    # Load history
    resp = client.get(f"/chat/{conversation_id}")
    assert resp.status_code == 200, f"Failed to load history: {resp.text}"
    messages = resp.json()["messages"]
    assert len(messages) == 1
    assert messages[0]["content"] == "Hello!"
    assert int(messages[0]["picture_id"]) == 42


def test_chat_history_clear(test_server):
    client = test_server

    resp = client.post("/chat", params={"character_id": 0})
    assert resp.status_code == 200, f"Failed to create chat: {resp.text}"
    conversation_id = resp.json().get("conversation_id")
    assert conversation_id == 1

    payload = {
        "conversation_id": conversation_id,
        "timestamp": int(time.time()),
        "role": "user",
        "content": "To be deleted",
    }
    # Save a message
    resp = client.post("/chat/message", json=payload)
    assert resp.status_code == 200
    # Clear history
    resp = client.delete(f"/chat/{conversation_id}")
    assert resp.status_code == 200, f"Failed to clear history: {resp.text}"
    # Load history, should be empty
    resp = client.get(f"/chat/{conversation_id}")
    assert resp.status_code == 404


def test_chat_history_multiple_sessions(test_server):
    client = test_server

    resp = client.post("/chat", params={"character_id": 0})
    assert resp.status_code == 200, f"Failed to create chat: {resp.text}"
    conversation_id_1 = resp.json().get("conversation_id")
    assert conversation_id_1 == 1

    resp = client.post("/chat", params={"character_id": 0})
    assert resp.status_code == 200, f"Failed to create chat: {resp.text}"
    conversation_id_2 = resp.json().get("conversation_id")
    assert conversation_id_2 == 2

    # Save messages to two sessions
    payload1 = {
        "conversation_id": conversation_id_1,
        "timestamp": int(time.time()),
        "role": "user",
        "content": "Session A",
    }
    payload2 = {
        "conversation_id": conversation_id_2,
        "timestamp": int(time.time()),
        "role": "user",
        "content": "Session B",
    }
    client.post("/chat/message", json=payload1)
    client.post("/chat/message", json=payload2)
    # Clear only session A
    client.delete(f"/chat/{conversation_id_1}")
    # Session A should be empty
    resp = client.get(f"/chat/{conversation_id_1}")
    assert resp.status_code == 404
    # Session B should still exist
    resp = client.get(f"/chat/{conversation_id_2}")
    assert resp.status_code == 200
    messages = resp.json()["messages"]
    assert len(messages) == 1
    assert messages[0]["content"] == "Session B"
