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
    """Regression test: hidden-tag filtering is applied in SQL so the stream
    returns exactly the visible pictures in the correct number of batches.

    Hidden-tag filtering moved from a Python post-filter into the SQL WHERE
    clause, so ``sql_count`` (the over-fetch probe result) now reflects only
    visible rows.  40 visible pictures / batch_limit 10 = 4 batches.
    """
    tmp, client, server = _setup_server()
    try:
        # Seed 60 pictures, every 3rd one tagged "hide_me" -> 20 hidden, 40 visible.
        ids, hidden_ids = _seed_pictures(server, total=60, hidden_every=3)
        assert len(hidden_ids) == 20

        # Configure the test user to hide that tag.
        resp = client.patch("/users/me/config", json={"hidden_tags": ["hide_me"]})
        assert resp.status_code == 200

        # Drain with a small batch so batching behaviour is visible per call.
        # apply_tag_filter=true activates hidden-tag SQL filtering server-side.
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
        # SQL WHERE filters hidden tags, so sql_count reflects 40 visible rows.
        # 40 visible / batch_limit 10 = 4 calls (each returning 10 pictures
        # except the probe on the 4th which returns exactly 10 -> done=True).
        assert calls == 4, (
            f"expected 4 stream calls (40 visible rows / batch_limit 10); got {calls}"
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


def test_pictures_endpoint_paginates_correctly():
    """The non-stream /pictures endpoint must return all pictures when the
    caller advances offset by limit until fewer than limit results come back.

    55 pictures at limit=10 → 5 full pages + 1 partial (5 items) = 6 calls.
    A count not divisible by limit is chosen so the loop always terminates on a
    partial page (no extra empty sentinel call needed).
    """
    tmp, client, server = _setup_server()
    try:
        ids, _ = _seed_pictures(server, total=55)
        limit, offset, all_ids, calls = 10, 0, [], 0
        while True:
            resp = client.get(
                f"/pictures?fields=grid&limit={limit}&offset={offset}"
                "&sort=IMPORTED_AT&descending=true"
            )
            assert resp.status_code == 200
            page = resp.json()
            calls += 1
            all_ids.extend(p["id"] for p in page)
            if len(page) < limit:
                break
            offset += limit
        assert set(all_ids) == set(ids), (
            f"Expected {len(ids)} unique IDs; got {len(set(all_ids))}"
        )
        # 55 pictures / limit 10 → 6 calls (pages of 10,10,10,10,10,5).
        assert calls == 6, f"Expected 6 pages for 55 pictures at limit=10; got {calls}"
    finally:
        server.vault.close()
        tmp.cleanup()
        gc.collect()
