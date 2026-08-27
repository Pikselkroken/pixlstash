"""Generated thumbnails must revalidate, not go stale under a stable URL.

The sidebar used to bust the character-thumbnail cache with a fresh
``?cb=<now>`` on every refresh, which guaranteed freshness by re-downloading
every thumbnail on every sidebar refresh - expensive against a route whose
picture lookup is already slow (issue #651). Dropping the buster made the URL
stable, which is only safe if the response says how it may be reused.

Starlette's ``FileResponse`` sets an ``ETag`` but answers no conditional request
(verified against starlette 1.3.1: its only conditional logic is ``If-Range``),
and these routes sent no ``Cache-Control`` at all - so browsers fell back to
*heuristic* caching and a regenerated thumbnail could stay stale for an
unbounded window with no revalidation.

These tests pin both halves of the fix: the header is present, and a conditional
request is actually answered with a 304 rather than the whole PNG again.
"""

import gc
import io
import json
import os
import tempfile

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from PIL import Image

from pixlstash.db_models import Face
from pixlstash.server import Server
from pixlstash.utils.http_cache import (
    REVALIDATE_CACHE_CONTROL,
    conditional_file_response,
    file_etag,
)
from tests.utils import upload_pictures_and_wait


# ── The helper itself ───────────────────────────────────────────────────────


# conftest patches TestClient to prepend the /api/v1 prefix to every request, so
# this bare app mounts its probe route there rather than fighting the patch.
_PROBE_PATH = "/api/v1/thing"


def _helper_client(tmp_path_file):
    app = FastAPI()

    @app.get(_PROBE_PATH)
    def thing(request: Request):
        return conditional_file_response(request, tmp_path_file)

    return TestClient(app)


def test_helper_sends_a_revalidate_always_cache_control(tmp_path):
    """Without this header the browser applies heuristic caching."""
    target = tmp_path / "thumb.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n-first")
    client = _helper_client(str(target))

    resp = client.get(_PROBE_PATH)

    assert resp.status_code == 200
    assert resp.headers["cache-control"] == REVALIDATE_CACHE_CONTROL
    assert "private" in resp.headers["cache-control"]
    assert "no-cache" in resp.headers["cache-control"]
    assert resp.headers.get("etag")
    assert resp.headers.get("last-modified")


def test_helper_answers_a_matching_if_none_match_with_304(tmp_path):
    """The point of `no-cache`: revalidation must be cheap, not a re-download."""
    target = tmp_path / "thumb.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n-first")
    client = _helper_client(str(target))

    first = client.get(_PROBE_PATH)
    etag = first.headers["etag"]

    second = client.get(_PROBE_PATH, headers={"If-None-Match": etag})

    assert second.status_code == 304
    assert second.content == b""
    # A 304 must still carry the validator and the policy, or the next
    # revalidation has nothing to send (RFC 9110 §15.4.5).
    assert second.headers["etag"] == etag
    assert second.headers["cache-control"] == REVALIDATE_CACHE_CONTROL


def test_helper_serves_the_new_bytes_when_the_thumbnail_is_regenerated(tmp_path):
    """The staleness bug this exists to prevent, end to end."""
    target = tmp_path / "thumb.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n-first")
    client = _helper_client(str(target))

    stale_etag = client.get(_PROBE_PATH).headers["etag"]

    # Regenerated: different size, so a different validator.
    target.write_bytes(b"\x89PNG\r\n\x1a\n-second-and-longer")

    resp = client.get(_PROBE_PATH, headers={"If-None-Match": stale_etag})

    assert resp.status_code == 200
    assert resp.content == b"\x89PNG\r\n\x1a\n-second-and-longer"
    assert resp.headers["etag"] != stale_etag


def test_no_etag_when_the_file_cannot_be_stated(tmp_path, caplog):
    """A missing file yields no validator - and says so, rather than failing."""
    missing = str(tmp_path / "missing.png")

    assert file_etag(missing) is None
    assert "Could not stat" in caplog.text


# ── The two routes that serve generated thumbnails ──────────────────────────


def _setup():
    temp_dir = tempfile.TemporaryDirectory()
    image_root = os.path.join(temp_dir.name, "images")
    os.makedirs(image_root, exist_ok=True)
    server_config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(server_config_path, "w") as f:
        f.write(json.dumps({"port": 8000}))
    server = Server(server_config_path)
    client = TestClient(server.api)
    resp = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert resp.status_code == 200
    return temp_dir, client, server


def _import_one_picture(client):
    import glob

    candidates = glob.glob(
        os.path.join(os.path.dirname(__file__), "..", "pictures", "*.png")
    ) + glob.glob(os.path.join(os.path.dirname(__file__), "..", "pictures", "*.jpg"))
    assert candidates, "No test images found in pictures/ directory"
    path = candidates[0]
    mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    with open(path, "rb") as image_file:
        import_resp = upload_pictures_and_wait(
            client, [("file", (os.path.basename(path), image_file, mime))]
        )
    return import_resp["results"][0]["picture_id"]


def _age_the_cached_thumbnail(server, subdir, stem, old_version):
    """Rewind a just-written cache entry to a pre-256 PixlStash.

    A fresh vault starts with an empty thumbnail cache, so the branch every
    *existing* install takes -- read the cache, decide it is stale, regenerate
    -- is unreachable without one on disk. This edits the metadata the route
    itself just wrote and changes **only** the version, so a regeneration can
    only be attributed to ``thumbnail_cache_version`` and not to a picture id
    or hidden-tag key that happened not to match.
    """
    cache_dir = os.path.join(server.vault.image_root, "tmp", subdir)
    meta_path = os.path.join(cache_dir, f"{stem}.json")
    with open(meta_path, "r", encoding="utf-8") as handle:
        meta = json.load(handle)
    assert meta["version"] > old_version, (
        f"{subdir} cache version was not bumped past {old_version}, so every "
        "already-cached 64x64 thumbnail would go on being served forever"
    )
    meta["version"] = old_version
    with open(meta_path, "w", encoding="utf-8") as handle:
        json.dump(meta, handle)
    Image.new("RGBA", (64, 64), (1, 2, 3, 255)).save(
        os.path.join(cache_dir, f"{stem}.png"), format="PNG"
    )


def _link_face(server, pic_id, char_id):
    def _add(session):
        session.add(
            Face(
                picture_id=pic_id,
                frame_index=0,
                face_index=0,
                character_id=char_id,
                bbox_="[0, 0, 64, 64]",  # Face.bbox json.loads() this
            )
        )
        session.commit()

    server.vault.db.run_task(_add)


def test_character_thumbnail_revalidates_instead_of_going_stale():
    """GET /characters/{id}/thumbnail - the route the sidebar polls."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _import_one_picture(client)
        char_id = client.post("/characters", json={"name": "Thumbed"}).json()[
            "character"
        ]["id"]
        _link_face(server, pic_id, char_id)

        first = client.get(f"/characters/{char_id}/thumbnail")
        assert first.status_code == 200, first.text
        assert first.headers["cache-control"] == REVALIDATE_CACHE_CONTROL
        # Served at 256, not the 64 this route used to hardcode: the in-app
        # consumer is a 24 px mark, but external pickers render it far larger.
        assert Image.open(io.BytesIO(first.content)).size == (256, 256)
        etag = first.headers.get("etag")
        assert etag

        # Second read comes off the on-disk cache - the branch the sidebar hits
        # on every refresh, and the one that must answer conditionally.
        second = client.get(f"/characters/{char_id}/thumbnail")
        assert second.status_code == 200
        assert second.headers["cache-control"] == REVALIDATE_CACHE_CONTROL

        conditional = client.get(
            f"/characters/{char_id}/thumbnail",
            headers={"If-None-Match": second.headers["etag"]},
        )
        assert conditional.status_code == 304
        assert conditional.content == b""

        # An install that already cached a 64x64 crop must not keep serving it.
        _age_the_cached_thumbnail(server, "face_thumbnails", f"character_{char_id}", 6)
        regenerated = client.get(f"/characters/{char_id}/thumbnail")
        assert regenerated.status_code == 200
        assert Image.open(io.BytesIO(regenerated.content)).size == (256, 256)
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()


def test_picture_set_thumbnail_revalidates_instead_of_going_stale():
    """GET /picture_sets/{id}/thumbnail had the identical header gap."""
    temp_dir, client, server = _setup()
    try:
        pic_id = _import_one_picture(client)
        set_resp = client.post("/picture_sets", json={"name": "Thumbed set"})
        assert set_resp.status_code == 200, set_resp.text
        set_id = set_resp.json()["picture_set"]["id"]
        add = client.post(f"/picture_sets/{set_id}/members/{pic_id}")
        assert add.status_code == 200, add.text

        first = client.get(f"/picture_sets/{set_id}/thumbnail")
        assert first.status_code == 200, first.text
        assert first.headers["cache-control"] == REVALIDATE_CACHE_CONTROL
        assert first.headers.get("etag")
        # Same size bump as the character route. This pins the served size
        # only -- whether the fan was *rendered* at that size or upscaled from
        # a smaller canvas is not something a PNG's dimensions can tell you.
        assert Image.open(io.BytesIO(first.content)).size == (256, 256)

        conditional = client.get(
            f"/picture_sets/{set_id}/thumbnail",
            headers={"If-None-Match": first.headers["etag"]},
        )
        assert conditional.status_code == 304
        assert conditional.content == b""

        # Same stale-cache branch as the character route.
        _age_the_cached_thumbnail(server, "set_thumbnails", f"picture_set_{set_id}", 16)
        regenerated = client.get(f"/picture_sets/{set_id}/thumbnail")
        assert regenerated.status_code == 200
        assert Image.open(io.BytesIO(regenerated.content)).size == (256, 256)
    finally:
        server.close()
        temp_dir.cleanup()
        gc.collect()
