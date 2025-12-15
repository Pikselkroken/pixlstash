import pytest
from fastapi.testclient import TestClient
from pixlvault.server import Server
import tempfile
import os
import shutil


@pytest.fixture
def test_server():
    # Force CPU for all models during test
    tmpdir = tempfile.mkdtemp()
    config_path = os.path.join(tmpdir, "config.json")
    server_config_path = os.path.join(tmpdir, "server_config.json")
    server = Server(config_path, server_config_path)
    server.vault.import_default_data()
    client = TestClient(server.api)

    # Create Esmeralda
    char_name = "Esmeralda"
    char_desc = "Default vault character"
    resp = client.post(
        "/characters",
        json={"name": char_name, "description": char_desc},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    char_id = data["character"]["id"]

    yield client, char_id
    shutil.rmtree(tmpdir)


def test_chat_history_save_and_load(test_server):
    client, char_id = test_server

    resp = client.post(
        "/conversations", params={"character_id": char_id, "description": "Test chat"}
    )
    assert resp.status_code == 200, f"Failed to create chat: {resp.text}"
    conversation_id = resp.json().get("conversation_id")
    assert conversation_id == 1

    payload = {
        "conversation_id": conversation_id,
        "role": "user",
        "content": "Hello!",
    }
    # Save a message
    resp = client.post("/conversations/message", json=payload)
    assert resp.status_code == 200, f"Failed to save message: {resp.text}"
    assert resp.json()["status"] == "ok"
    # Load history
    resp = client.get(f"/conversations/{conversation_id}")
    assert resp.status_code == 200, f"Failed to load history: {resp.text}"
    messages = resp.json()["messages"]
    assert len(messages) == 1
    assert messages[0]["content"] == "Hello!"


def test_chat_history_clear(test_server):
    client, char_id = test_server

    resp = client.post("/conversations", params={"character_id": char_id})
    assert resp.status_code == 200, f"Failed to create chat: {resp.text}"
    conversation_id = resp.json().get("conversation_id")
    assert conversation_id == 1

    payload = {
        "conversation_id": conversation_id,
        "role": "user",
        "content": "To be deleted",
    }
    # Save a message
    resp = client.post("/conversations/message", json=payload)
    assert resp.status_code == 200
    # Clear history
    resp = client.delete(f"/conversations/{conversation_id}")
    assert resp.status_code == 200, f"Failed to clear history: {resp.text}"
    # Load history, should be empty
    resp = client.get(f"/conversations/{conversation_id}")
    assert resp.status_code == 404


def test_chat_history_multiple_sessions(test_server):
    client, char_id = test_server

    resp = client.post("/conversations", params={"character_id": char_id})
    assert resp.status_code == 200, f"Failed to create chat: {resp.text}"
    conversation_id_1 = resp.json().get("conversation_id")
    assert conversation_id_1 == 1

    resp = client.post("/conversations", params={"character_id": char_id})
    assert resp.status_code == 200, f"Failed to create chat: {resp.text}"
    conversation_id_2 = resp.json().get("conversation_id")
    assert conversation_id_2 == 2

    # Save messages to two sessions
    payload1 = {
        "conversation_id": conversation_id_1,
        "role": "user",
        "content": "Session A",
    }
    payload2 = {
        "conversation_id": conversation_id_2,
        "role": "user",
        "content": "Session B",
    }
    client.post("/conversations/message", json=payload1)
    client.post("/conversations/message", json=payload2)
    # Clear only session A
    client.delete(f"/conversations/{conversation_id_1}")
    # Session A should be empty
    resp = client.get(f"/conversations/{conversation_id_1}")
    assert resp.status_code == 404
    # Session B should still exist
    resp = client.get(f"/conversations/{conversation_id_2}")
    assert resp.status_code == 200
    messages = resp.json()["messages"]
    assert len(messages) == 1
    assert messages[0]["content"] == "Session B"
