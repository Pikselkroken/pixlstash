import time

API_PREFIX = "/api/v1"

_DEFAULT_TIMEOUT_S = 180


def poll_until_zero(
    server, count_fn, label, timeout_s=_DEFAULT_TIMEOUT_S, interval=0.5
):
    """Poll a DB count function until it returns 0, then return.

    Args:
        server: Server instance (provides server.vault.db).
        count_fn: Callable accepting a SQLModel Session that returns an int.
        label: Human-readable description used in the timeout error message.
        timeout_s: Maximum seconds to wait before raising AssertionError.
        interval: Seconds between polls.

    Raises:
        AssertionError: If the count does not reach 0 within timeout_s.
    """
    start = time.time()
    remaining = None
    while time.time() - start < timeout_s:
        remaining = server.vault.db.run_immediate_read_task(count_fn)
        if remaining == 0:
            return
        time.sleep(interval)
    raise AssertionError(
        f"Timed out after {timeout_s}s waiting for {label}: {remaining} still pending"
    )


def wait_for_import_task(client, task_id, timeout_s=10, poll_interval=0.1):
    start = time.time()
    while time.time() - start < timeout_s:
        status_resp = client.get(
            f"{API_PREFIX}/pictures/import/status", params={"task_id": task_id}
        )
        assert status_resp.status_code == 200, f"Error: {status_resp.text}"
        status_payload = status_resp.json()
        status = status_payload.get("status")
        if status in {"completed", "failed"}:
            return status_payload
        time.sleep(poll_interval)
    raise AssertionError(f"Import task did not complete in {timeout_s}s")


def upload_pictures_and_wait(
    client,
    files,
    timeout_s=10,
    poll_interval=0.1,
    form_data=None,
):
    kwargs = {"files": files}
    if form_data:
        kwargs["data"] = form_data
    resp = client.post(f"{API_PREFIX}/pictures/import", **kwargs)
    assert resp.status_code == 200, f"Error: {resp.text}"
    task_id = resp.json().get("task_id")
    assert task_id, "Missing task_id in import response"
    return wait_for_import_task(client, task_id, timeout_s, poll_interval)


def wait_for_faces(client, picture_id, timeout_s=30, poll_interval=0.5):
    """Poll GET /pictures/{picture_id}/faces until at least one face appears or timeout.

    Returns the list of faces (may be empty if no faces were detected in time).
    Face extraction is now asynchronous so callers must poll rather than relying
    on the import task completion.
    """
    start = time.time()
    while time.time() - start < timeout_s:
        resp = client.get(f"{API_PREFIX}/pictures/{picture_id}/faces")
        assert resp.status_code == 200, (
            f"Error fetching faces for {picture_id}: {resp.text}"
        )
        faces = resp.json().get("faces", [])
        if faces:
            return faces
        time.sleep(poll_interval)
    # Return whatever is there (possibly empty) after timeout — callers decide whether to skip
    resp = client.get(f"{API_PREFIX}/pictures/{picture_id}/faces")
    assert resp.status_code == 200
    return resp.json().get("faces", [])
