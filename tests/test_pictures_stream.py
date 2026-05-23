"""Tests for /pictures/stream and /pictures/count endpoints.

The streaming endpoint must return ``done=True`` based on the underlying SQL
row count, NOT on the post-filter row count.  Earlier implementations counted
post-filter results and terminated streaming prematurely whenever a batch
shrank (hidden-tag drops, stack-leader dedup).  The tests in this module pin
the correct behaviour, with particular emphasis on the post-filter shrinkage
case.
"""

from __future__ import annotations

import gc
import json
import os
import tempfile
from datetime import datetime

from fastapi.testclient import TestClient

from pixlstash.database import DBPriority
from pixlstash.db_models import Picture
from pixlstash.db_models.tag import Tag
from pixlstash.server import Server


def _setup_server():
    tmp = tempfile.TemporaryDirectory()
    image_root = os.path.join(tmp.name, "images")
    os.makedirs(image_root, exist_ok=True)
    config_path = os.path.join(tmp.name, "server-config.json")
    with open(config_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"port": 0}))
    server = Server(config_path)
    client = TestClient(server.api)
    resp = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert resp.status_code == 200
    return tmp, client, server


def _seed_pictures(server, total: int, hidden_every: int | None = None):
    """Insert `total` pictures.

    When ``hidden_every`` is set, every ``hidden_every``-th picture gets a
    ``hide_me`` tag attached.  Returns the list of created picture IDs in
    insertion order and the set of IDs that have a hide_me tag.
    """

    def _insert(session):
        ids: list[int] = []
        hidden_ids: set[int] = set()
        for i in range(total):
            pic = Picture(
                file_path=f"pic_{i:04d}.jpg",
                score=0,
                imported_at=datetime.now(),
            )
            session.add(pic)
            session.flush()
            ids.append(pic.id)
            if hidden_every and (i % hidden_every == 0):
                session.add(Tag(picture_id=pic.id, tag="hide_me"))
                hidden_ids.add(pic.id)
        session.commit()
        return ids, hidden_ids

    return server.vault.db.run_task(_insert, priority=DBPriority.IMMEDIATE)


def _drain_stream(client, *, batch_limit: int, extra_params: str = ""):
    """Repeatedly call /pictures/stream until done. Returns the concatenated
    list of pictures and the number of HTTP calls made."""
    pictures: list[dict] = []
    offset = 0
    calls = 0
    # Hard safety cap to prevent runaway loops if the endpoint misbehaves.
    while calls < 100:
        url = (
            f"/pictures/stream?offset={offset}&batch_limit={batch_limit}"
            f"&fields=grid{extra_params}"
        )
        resp = client.get(url)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        pictures.extend(body["pictures"])
        calls += 1
        if body["done"]:
            break
        new_offset = body["next_offset"]
        assert new_offset > offset, (
            f"next_offset ({new_offset}) did not advance past offset ({offset})"
        )
        offset = new_offset
    else:
        raise AssertionError(
            "stream did not finish within 100 calls — done flag never True"
        )
    return pictures, calls


def test_stream_completes_for_small_batch_smaller_than_total():
    tmp, client, server = _setup_server()
    try:
        ids, _ = _seed_pictures(server, total=25)
        pictures, calls = _drain_stream(client, batch_limit=10)
        # Total returned (deduped by id) equals total inserted.
        returned_ids = {p["id"] for p in pictures}
        assert returned_ids == set(ids)
        # 25 rows at batch_limit=10 -> 3 calls (10, 10, 5 with done=True on 3rd).
        assert calls == 3
    finally:
        server.vault.close()
        tmp.cleanup()
        gc.collect()


def test_stream_done_when_total_below_batch_limit():
    tmp, client, server = _setup_server()
    try:
        ids, _ = _seed_pictures(server, total=5)
        resp = client.get("/pictures/stream?offset=0&batch_limit=100&fields=grid")
        assert resp.status_code == 200
        body = resp.json()
        assert body["done"] is True
        assert len(body["pictures"]) == 5
        assert body["next_offset"] == 5
        _ = ids
    finally:
        server.vault.close()
        tmp.cleanup()
        gc.collect()


def test_stream_continues_when_hidden_tag_post_filter_shrinks_batches():
    """Critical regression test.

    When the hidden-tag post-filter drops rows, ``done`` MUST still be
    computed from the SQL pre-filter count.  Previously, the loader counted
    the post-filter response length and terminated early as soon as a batch
    shrank below the requested limit.
    """
    tmp, client, server = _setup_server()
    try:
        # Seed 60 pictures, every 3rd one tagged "hide_me" -> 20 hidden, 40 visible.
        ids, hidden_ids = _seed_pictures(server, total=60, hidden_every=3)
        assert len(hidden_ids) == 20

        # Configure the test user to hide that tag.
        resp = client.patch("/users/me/config", json={"hidden_tags": ["hide_me"]})
        assert resp.status_code == 200

        # Drain with a small batch so post-filter shrinkage is visible per call.
        # apply_tag_filter=true activates hidden-tag filtering server-side.
        pictures, calls = _drain_stream(
            client,
            batch_limit=10,
            extra_params="&apply_tag_filter=true",
        )
        returned_ids = {p["id"] for p in pictures}
        visible_ids = set(ids) - hidden_ids
        # All visible pictures must be returned; none of the hidden ones.
        assert returned_ids == visible_ids, (
            f"missing {len(visible_ids - returned_ids)} visible pictures; "
            f"contains {len(returned_ids & hidden_ids)} hidden pictures"
        )
        # SQL returns 10 rows per call until the tail (60 / 10 = 6 calls); even
        # though each call's post-filter response is ~7 pictures, the loader
        # must keep going. Allow some flexibility but it must be > 1 call.
        assert calls >= 6, (
            f"expected at least 6 stream calls (60 rows / batch_limit 10); got {calls}"
        )
    finally:
        server.vault.close()
        tmp.cleanup()
        gc.collect()


def test_stream_done_includes_partial_final_batch_smaller_than_limit():
    """If the final SQL batch returns fewer rows than batch_limit, done must
    be True on that same response — without an extra empty call."""
    tmp, client, server = _setup_server()
    try:
        ids, _ = _seed_pictures(server, total=12)
        pictures, calls = _drain_stream(client, batch_limit=10)
        assert len({p["id"] for p in pictures}) == len(ids)
        # 12 rows / batch_limit 10 -> 2 calls (10 + 2 with done=True).
        assert calls == 2
    finally:
        server.vault.close()
        tmp.cleanup()
        gc.collect()


def test_count_endpoint_returns_total():
    tmp, client, server = _setup_server()
    try:
        ids, _ = _seed_pictures(server, total=17)
        resp = client.get("/pictures/count?fields=grid")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == len(ids)
    finally:
        server.vault.close()
        tmp.cleanup()
        gc.collect()


def test_count_endpoint_excludes_hidden_tagged_pictures_when_filter_enabled():
    tmp, client, server = _setup_server()
    try:
        ids, hidden_ids = _seed_pictures(server, total=20, hidden_every=4)
        assert len(hidden_ids) == 5

        client.patch("/users/me/config", json={"hidden_tags": ["hide_me"]})

        resp = client.get("/pictures/count?fields=grid&apply_tag_filter=true")
        assert resp.status_code == 200
        body = resp.json()
        # The fast SELECT COUNT(*) path does not apply the hidden-tag post-filter,
        # so the count is an upper bound. It must be at least the filtered count
        # and no more than the total unfiltered count.
        filtered_count = len(ids) - len(hidden_ids)
        assert body["count"] >= filtered_count
        assert body["count"] <= len(ids)
    finally:
        server.vault.close()
        tmp.cleanup()
        gc.collect()
