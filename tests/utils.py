import time

from sqlalchemy import func
from sqlmodel import delete, select

from pixlstash.db_models.picture import Picture
from pixlstash.db_models.picture_likeness import PictureLikeness, PictureLikenessQueue

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


def wait_for_import_task(client, task_id, timeout_s=30, poll_interval=0.1):
    """Poll ``GET /pictures/import/status`` until the task settles.

    **30s, not 10s, and the number follows from what is actually being waited
    on.** The import runs on a detached executor thread, but the write inside it
    goes through ``vault.db.run_task``, which queues onto the *single* DB worker.
    So this bounds "the DB queue drained far enough to run my write", not "the
    import worked — and in a test that imports twenty-one pictures in a loop,
    each iteration queues behind the tagging and embedding writes of the ones
    before it. At 10s that was a wall-clock race the loaded Windows shards lost
    (`test_server.py::test_semantic_search`, run 31481874866): a timeout there
    said nothing about the import and everything about how busy the queue was.
    30s matches ``wait_for_faces``, which waits on the same kind of queued work.

    A genuinely hung import now takes 30s to report instead of 10s, which is why
    the message below carries the last status and the elapsed time: a hang and a
    slow queue must not read the same.
    """
    start = time.time()
    status = None
    payload = None
    while time.time() - start < timeout_s:
        status_resp = client.get(
            f"{API_PREFIX}/pictures/import/status", params={"task_id": task_id}
        )
        assert status_resp.status_code == 200, f"Error: {status_resp.text}"
        payload = status_resp.json()
        status = payload.get("status")
        if status in {"completed", "failed"}:
            return payload
        time.sleep(poll_interval)
    raise AssertionError(
        f"Import task did not complete in {timeout_s}s "
        f"(waited {time.time() - start:.1f}s, last status {status!r}, "
        f"processed {(payload or {}).get('processed')!r} of "
        f"{(payload or {}).get('total')!r})"
    )


def upload_pictures_and_wait(
    client,
    files,
    timeout_s=30,
    poll_interval=0.1,
    form_data=None,
):
    """Upload and wait for the import to settle.

    The default tracks :func:`wait_for_import_task`'s deliberately — this is the
    wrapper almost every caller actually uses (including the loop in
    `test_semantic_search` that flaked), so leaving it at 10 would have kept the
    old bound in place everywhere while looking fixed.
    """
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


def wait_likeness_settled(server, timeout_s=_DEFAULT_TIMEOUT_S):
    """Block until the background likeness pipeline has fully processed every
    uploaded picture, so a manually-seeded ``PictureLikeness`` fixture will not
    be clobbered underneath the assertions that read it.

    ``timeout_s`` is generous (CPU embeddings + likeness recompute for the
    uploaded fixtures can take well over 30s on a loaded CI runner); the poll
    returns as soon as the pipeline is quiescent, so a large cap costs nothing
    on a fast machine and only avoids a spurious timeout under contention.

    Uploading real pictures runs two background stages that mutate the
    ``picturelikeness`` table: the likeness-parameter pass calls
    ``LikenessParameterUtils.reset_likeness_for_pictures`` (which DELETEs every
    pair touching a picture and re-queues it), then ``LikenessTask`` recomputes
    real pairs. Both race with a seeded fixture. Waiting on the parameter stage
    alone is not enough: ``count_pending_parameters`` reads 0 in the gaps
    between per-picture embedding batches, and ``MissingLikenessFinder`` then
    seeds the queue and writes real pairs for the pictures that *are* ready —
    which collides with the fixture's own INSERT on the
    ``(picture_id_a, picture_id_b)`` unique constraint.

    Once every picture is fully processed (embedding + likeness_parameters +
    perceptual_hash all set), the queue is drained, and the pair table is stable
    across two polls, the pipeline is quiescent: a subsequent atomic reseed (see
    ``seed_likeness_stable``) then stays put because the finder has no further
    work (queue empty + pairs present).
    """

    def _snapshot(session):
        total = int(session.exec(select(func.count()).select_from(Picture)).one())
        ready = int(
            session.exec(
                select(func.count())
                .select_from(Picture)
                .where(
                    Picture.image_embedding.is_not(None),
                    Picture.likeness_parameters.is_not(None),
                    Picture.perceptual_hash.is_not(None),
                )
            ).one()
        )
        queued = int(
            session.exec(select(func.count()).select_from(PictureLikenessQueue)).one()
        )
        pairs = int(
            session.exec(select(func.count()).select_from(PictureLikeness)).one()
        )
        return total, ready, queued, pairs

    start = time.time()
    prev_pairs = None
    while time.time() - start < timeout_s:
        total, ready, queued, pairs = server.vault.db.run_immediate_read_task(_snapshot)
        # Quiescent only when every picture is fully processed and the queue is
        # empty; stable across two polls confirms the last recompute wrote.
        if total > 0 and ready == total and queued == 0 and pairs == prev_pairs:
            return
        prev_pairs = pairs
        time.sleep(0.25)
    raise AssertionError("likeness pipeline did not settle in time")


def seed_likeness_stable(server, seed_fn):
    """Wait for the likeness pipeline to quiesce, then seed with a clean
    ``picturelikeness`` slate in one atomic transaction.

    Deletes any pipeline-computed pairs and drains the queue *inside* the seed
    transaction so no observer ever sees an empty-table/empty-queue state (which
    would re-trigger ``LikenessUtils.seed_queue``). Wiping first also means the
    fixture's own INSERTs cannot collide with a real pair the pipeline already
    computed for the same picture ids. After commit the queue is empty and the
    seeded pairs are present, so ``MissingLikenessFinder`` has no further work
    and the fixture stays authoritative. Returns whatever ``seed_fn`` returns.
    """
    wait_likeness_settled(server)

    def _wrapped(session):
        session.exec(delete(PictureLikeness))
        session.exec(delete(PictureLikenessQueue))
        return seed_fn(session)

    return server.vault.db.run_task(_wrapped)
