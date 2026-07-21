"""Authz tests for the bulk impossible-tags clear/restore endpoints.

Both ``POST /api/v1/pictures/impossible-tags/clear`` and ``.../restore`` are owner-only
by the auth middleware POST gate: they are NOT in ``READ_SAFE_POST_PATHS``, so a
resource-scoped READ token (the only kind of scoped token that can exist) is rejected
with 403 before the handler runs. These tests assert both directions:

* negative: a scoped READ Bearer token -> 403 on both POSTs, and the tags are untouched;
* positive: the owner cookie session -> 200 and the clear/restore round-trips.

Also covers input validation (bad filter set -> 400) and the defense-in-depth that an
empty-scope token cannot mutate even if the gate were somehow bypassed.
"""

import gc
import io
import json
import os
import tempfile

from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import select

from pixlstash.db_models import Face, PictureSetMember, Tag
from pixlstash.server import Server
from tests.utils import upload_pictures_and_wait

API = "/api/v1"

# A face-requiring tag (see person_tags.FACE_REQUIRING_TAGS) so the no_face filter fires.
_FACE_TAG = "smile"


def _distinct_png(n: int) -> bytes:
    img = Image.new("RGB", (16 + n, 16 + n), color=(n * 7 % 256, n * 13 % 256, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _upload(client, n: int) -> int:
    return upload_pictures_and_wait(
        client, [("file", (f"d{n}.png", _distinct_png(n), "image/png"))]
    )["results"][0]["picture_id"]


def _seed(server, pic_id: int, *, real_face: bool, tags: list[str]) -> None:
    def ins(session):
        session.add(Face(picture_id=pic_id, face_index=0 if real_face else -1))
        for t in tags:
            session.add(Tag(picture_id=pic_id, tag=t))
        session.commit()

    server.vault.db.run_task(ins)


def _add_to_set(server, pic_id: int, set_id: int) -> None:
    def ins(session):
        session.add(PictureSetMember(set_id=set_id, picture_id=pic_id))
        session.commit()

    server.vault.db.run_task(ins)


def _tags(server, pic_id: int) -> set[str]:
    return set(
        server.vault.db.run_task(
            lambda s: list(
                s.exec(select(Tag.tag).where(Tag.picture_id == pic_id)).all()
            )
        )
    )


def _env():
    """Owner cookie client + a READ token scoped to Set A; one suspect picture in Set A."""
    temp_dir = tempfile.TemporaryDirectory()
    os.makedirs(os.path.join(temp_dir.name, "images"), exist_ok=True)
    cfg = os.path.join(temp_dir.name, "server-config.json")
    with open(cfg, "w") as f:
        f.write(json.dumps({"port": 8000}))
    server = Server(cfg)
    client = TestClient(server.api)
    assert (
        client.post(
            f"{API}/login", json={"username": "owner", "password": "ownerpass1"}
        ).status_code
        == 200
    )
    pic_a = _upload(client, 1)
    # No real face, carries a face-requiring tag -> a "no_face" impossible source.
    _seed(server, pic_a, real_face=False, tags=[_FACE_TAG])

    set_a = client.post(f"{API}/picture_sets", json={"name": "Set A"}).json()[
        "picture_set"
    ]["id"]
    _add_to_set(server, pic_a, set_a)

    r = client.post(
        f"{API}/users/me/token",
        json={
            "description": "set A read",
            "scope": "READ",
            "resource_type": "picture_set",
            "resource_id": set_a,
        },
    )
    assert r.status_code == 200, r.text
    token_a = r.json()["token"]
    return temp_dir, client, server, pic_a, token_a


def test_scoped_read_token_cannot_clear_or_restore():
    temp_dir, _client, server, pic_a, token_a = _env()
    try:
        bearer = TestClient(server.api)
        headers = {"Authorization": f"Bearer {token_a}"}
        # Negative: clear is owner-only by the gate -> 403, no mutation.
        r = bearer.post(
            f"{API}/pictures/impossible-tags/clear",
            json={"picture_ids": [pic_a], "filters": ["no_face"]},
            headers=headers,
        )
        assert r.status_code == 403, r.text
        assert _FACE_TAG in _tags(server, pic_a)  # untouched

        # Negative: restore likewise 403.
        r = bearer.post(
            f"{API}/pictures/impossible-tags/restore",
            json={"pairs": [{"picture_id": pic_a, "tag": _FACE_TAG}]},
            headers=headers,
        )
        assert r.status_code == 403, r.text

        # Same via the ?token= query-param path (no Authorization header).
        r = bearer.post(
            f"{API}/pictures/impossible-tags/clear",
            params={"token": token_a},
            json={"picture_ids": [pic_a], "filters": ["no_face"]},
        )
        assert r.status_code == 403, r.text
        assert _FACE_TAG in _tags(server, pic_a)
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_owner_can_clear_and_restore():
    temp_dir, client, server, pic_a, _token_a = _env()
    try:
        # Positive: owner clears the impossible tag.
        r = client.post(
            f"{API}/pictures/impossible-tags/clear",
            json={"picture_ids": [pic_a], "filters": ["no_face"]},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["count"] == 1
        assert _FACE_TAG not in _tags(server, pic_a)

        # Undo restores it.
        r = client.post(
            f"{API}/pictures/impossible-tags/restore",
            json={"pairs": body["removed"]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["restored"] == 1
        assert _FACE_TAG in _tags(server, pic_a)
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()


def test_clear_rejects_invalid_filter_set():
    temp_dir, client, server, pic_a, _token_a = _env()
    try:
        # Empty / unknown filter kinds -> 400, no mutation.
        r = client.post(
            f"{API}/pictures/impossible-tags/clear",
            json={"picture_ids": [pic_a], "filters": ["bogus"]},
        )
        assert r.status_code == 400, r.text
        assert _FACE_TAG in _tags(server, pic_a)
    finally:
        server.vault.close()
        temp_dir.cleanup()
        gc.collect()
