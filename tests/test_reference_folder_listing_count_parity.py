"""Invariant guard: /pictures and /pictures/count agree for reference-folder browsing.

Motivated by a smoke-test report that browsing a reference folder
(``reference_folder_id`` + ``file_path_prefix``) as the owner returned an empty
picture list while the count for the "same" folder was non-zero.

Both endpoints funnel through ``select_pictures_for_listing`` and read every filter
from ``request.query_params`` -- there is no separate filter-extraction path -- so
for identical query parameters they MUST return the same population.  These tests
pin that invariant (list length == count when count > 0) across both percent- and
plus-encoding of ``file_path_prefix``, so any future refactor that lets the two
diverge fails here.

Note: the reported empty-list/non-zero-count divergence is NOT reproducible with
identical parameters (see the investigation report); the real reproducible failure
mode is a stored-path vs browse-prefix mismatch, which is out of scope for this
guard.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from urllib.parse import quote

from fastapi.testclient import TestClient

from pixlstash.database import DBPriority
from pixlstash.db_models import Picture
from pixlstash.db_models.reference_folder import ReferenceFolder
from pixlstash.server import Server


PREFIX = "/home/user/Bilder fra 2020"


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
    assert resp.status_code == 200, resp.text
    return tmp, client, server


def _seed(server):
    def _insert(session):
        folder = ReferenceFolder(id=3, folder=PREFIX, label="Bilder fra 2020")
        session.add(folder)
        session.flush()
        ids = []
        for i in range(5):
            pic = Picture(
                file_path=f"{PREFIX}/img_{i:03d}.jpg",
                reference_folder_id=3,
                imported_at=datetime.now(),
            )
            session.add(pic)
            session.flush()
            ids.append(pic.id)
        # A file in a sub-directory: children-only browsing must exclude it,
        # so list and count both ignore it consistently.
        deep = Picture(
            file_path=f"{PREFIX}/sub/deep.jpg",
            reference_folder_id=3,
            imported_at=datetime.now(),
        )
        session.add(deep)
        session.commit()
        return ids

    return server.vault.db.run_task(_insert, priority=DBPriority.IMMEDIATE)


def _run(prefix_value: str):
    tmp, client, server = _setup_server()
    try:
        seeded = _seed(server)
        list_resp = client.get(
            f"/pictures?reference_folder_id=3&file_path_prefix={prefix_value}"
        )
        count_resp = client.get(
            "/pictures/count?stack_leaders_only=true"
            f"&reference_folder_id=3&file_path_prefix={prefix_value}"
        )
        assert list_resp.status_code == 200, list_resp.text
        assert count_resp.status_code == 200, count_resp.text
        pics = list_resp.json()
        count = count_resp.json()["count"]
        return seeded, pics, count
    finally:
        tmp.cleanup()


def test_reference_folder_list_matches_count_percent_encoded():
    seeded, pics, count = _run(quote(PREFIX))
    assert count == len(seeded), f"count={count} expected {len(seeded)}"
    assert len(pics) == count, (
        f"list returned {len(pics)} pictures but count reported {count}"
    )
    assert {p["id"] for p in pics} == set(seeded)


def test_reference_folder_list_matches_count_plus_encoded():
    # Browsers encode spaces in query strings as '+'; both endpoints must decode
    # it the same way.
    seeded, pics, count = _run(PREFIX.replace(" ", "+"))
    assert count == len(seeded)
    assert len(pics) == count
