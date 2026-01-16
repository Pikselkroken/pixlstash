import time


def wait_for_import_task(client, task_id, timeout_s=10, poll_interval=0.1):
    start = time.time()
    while time.time() - start < timeout_s:
        status_resp = client.get("/pictures/import/status", params={"task_id": task_id})
        assert status_resp.status_code == 200, f"Error: {status_resp.text}"
        status_payload = status_resp.json()
        status = status_payload.get("status")
        if status in {"completed", "failed"}:
            return status_payload
        time.sleep(poll_interval)
    raise AssertionError(f"Import task did not complete in {timeout_s}s")


def upload_pictures_and_wait(client, files, timeout_s=10, poll_interval=0.1):
    resp = client.post("/pictures/import", files=files)
    assert resp.status_code == 200, f"Error: {resp.text}"
    task_id = resp.json().get("task_id")
    assert task_id, "Missing task_id in import response"
    return wait_for_import_task(client, task_id, timeout_s, poll_interval)
