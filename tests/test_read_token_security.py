"""Security tests for READ-scoped API tokens.

Coverage
--------
1. Read token cannot perform any write operation (POST/PUT/PATCH/DELETE).
2. Read token cannot access owner-only data (config, token list, server config).
3. Read token scoped to one resource cannot read a different resource.
4. Expired read token is rejected.
5. ALL-scope bearer token scoped-token checks (READ token cannot create tokens).
6. Unauthenticated login endpoint brute-force lockout (≥5 failures → 429).
7. Global rate limiter blocks the unauthenticated login path after _LIMIT hits.
8. All picture data uploaded before token issuance survives every attack attempt
   intact (scores, filenames).

The server is created fresh per test function with a real temporary database and
the pictures from ``pictures/good/`` to give each scenario a realistic dataset.
"""

import io
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

import pixlstash.routes.pictures._crud as crud_module
import pixlstash.utils.rate_limiter as rl_module
from pixlstash.db_models import Picture
from pixlstash.server import Server
from tests.utils import upload_pictures_and_wait

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

API = "/api/v1"
PICTURES_DIR = Path(__file__).resolve().parent.parent / "pictures" / "good"


# A tiny valid PNG produced in-memory so tests that need a fresh image don't
# depend on disk files.
def _make_png_bytes(width: int = 32, height: int = 32) -> bytes:
    img = Image.new("RGB", (width, height), color=(100, 149, 237))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _good_picture_files() -> list[tuple[str, bytes, str]]:
    """Return (filename, bytes, content_type) tuples for every file in pictures/good/."""
    results = []
    for path in sorted(PICTURES_DIR.iterdir()):
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
            content_type = (
                "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
            )
            results.append((path.name, path.read_bytes(), content_type))
    return results


def _setup_server_with_pictures(temp_dir: str):
    """Create a Server, log in, upload all good pictures, set random scores.

    Returns (server, authed_client, picture_ids, read_token_value).
    """
    config_path = f"{temp_dir}/server-config.json"
    server = Server(config_path)
    server.__enter__()

    # Use a fresh TestClient that keeps the session cookie.
    client = TestClient(server.api, raise_server_exceptions=True)

    # Set up credentials.
    r = client.post(
        f"{API}/login", json={"username": "owner", "password": "ownerpass1"}
    )
    assert r.status_code == 200, r.text

    # Upload the good pictures.
    picture_files = _good_picture_files()
    assert picture_files, "No test pictures found in pictures/good/"

    files = [("file", (name, data, ct)) for name, data, ct in picture_files]
    import_status = upload_pictures_and_wait(client, files, timeout_s=30)
    assert import_status["status"] == "completed", (
        f"Picture import failed: {import_status}"
    )

    # Fetch the imported picture IDs.
    r = client.get(f"{API}/pictures")
    assert r.status_code == 200, r.text
    picture_ids = [p["id"] for p in r.json()]
    assert picture_ids, "No pictures after import"

    # Assign manual scores (1–5) so we can verify they survive attacks.
    scores = {str(pid): (i % 5) + 1 for i, pid in enumerate(picture_ids)}
    r = client.post(
        f"{API}/pictures/apply-scores",
        json={"scores": scores, "only_unscored": False},
    )
    assert r.status_code == 200, r.text

    # Create a global READ token (no resource restriction).
    r = client.post(
        f"{API}/users/me/token",
        json={"description": "global read", "scope": "READ"},
    )
    assert r.status_code == 200, r.text
    read_token = r.json()["token"]

    return server, client, picture_ids, read_token


def _assert_pictures_intact(authed_client, original_ids: list, original_scores: dict):
    """Verify picture IDs and scores in the DB match what was set at setup."""
    r = authed_client.get(f"{API}/pictures")
    assert r.status_code == 200, r.text
    current = {str(p["id"]): p for p in r.json()}

    # All original pictures still present.
    for pid in original_ids:
        assert str(pid) in current, f"Picture {pid} missing after attack"

    # Scores must not have changed.
    for pid_str, expected_score in original_scores.items():
        actual_score = current[pid_str].get("score")
        assert actual_score == expected_score, (
            f"Score for picture {pid_str} was tampered: "
            f"expected {expected_score}, got {actual_score}"
        )


# ---------------------------------------------------------------------------
# 0. An ALL-scope token cannot be restricted to a resource (F1/F3 footgun)
# ---------------------------------------------------------------------------


class TestAllScopeResourceTokenRejected:
    """Minting an ``ALL``+``resource_type`` token must be rejected.

    The auth middleware only builds ``request.state.token_scope`` for non-ALL
    scopes, so such a token would bypass every object-scope guard
    (``enforce_picture_scope`` / ``fetch_scope_allowed_picture_ids`` read
    ``token_scope``) *and* pass the owner-only token-creation check — it is a
    full owner token wearing a "restricted" label. See the F3 finding in
    docs/reviews/feature-slick-grid-updates.md.
    """

    def test_all_scope_with_resource_type_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, _ = _setup_server_with_pictures(tmp)
            try:
                r = owner_client.post(
                    f"{API}/users/me/token",
                    json={
                        "description": "sneaky scoped write token",
                        "scope": "ALL",
                        "resource_type": "picture",
                        "resource_id": picture_ids[0],
                    },
                )
                assert r.status_code == 400, (
                    "ALL-scope token must not be restrictable to a resource, "
                    f"got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_read_scope_with_resource_type_still_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, _ = _setup_server_with_pictures(tmp)
            try:
                r = owner_client.post(
                    f"{API}/users/me/token",
                    json={
                        "description": "legit resource share",
                        "scope": "READ",
                        "resource_type": "picture",
                        "resource_id": picture_ids[0],
                    },
                )
                assert r.status_code == 200, (
                    f"READ resource share must still mint, got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_all_scope_without_resource_still_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, _picture_ids, _ = _setup_server_with_pictures(tmp)
            try:
                r = owner_client.post(
                    f"{API}/users/me/token",
                    json={"description": "owner token", "scope": "ALL"},
                )
                assert r.status_code == 200, (
                    f"ALL owner token must still mint, got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 1. READ token must not perform write operations
# ---------------------------------------------------------------------------


class TestReadTokenBlocksWrites:
    """A READ token must be rejected for every mutating HTTP method."""

    def test_cannot_upload_picture(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, read_token = _setup_server_with_pictures(
                tmp
            )
            try:
                png = _make_png_bytes()
                r = TestClient(server.api).post(
                    f"{API}/pictures/import",
                    files=[("file", ("new.png", png, "image/png"))],
                    headers={"Authorization": f"Bearer {read_token}"},
                )
                assert r.status_code == 403, (
                    f"READ token should not be able to upload pictures, got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_cannot_delete_picture(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, read_token = _setup_server_with_pictures(
                tmp
            )
            try:
                target_id = picture_ids[0]
                r = TestClient(server.api).delete(
                    f"{API}/pictures/{target_id}",
                    headers={"Authorization": f"Bearer {read_token}"},
                )
                assert r.status_code == 403, (
                    f"READ token should not delete pictures, got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_cannot_patch_picture_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, read_token = _setup_server_with_pictures(
                tmp
            )
            try:
                target_id = picture_ids[0]
                r = TestClient(server.api).patch(
                    f"{API}/pictures/{target_id}",
                    json={"score": 1},
                    headers={"Authorization": f"Bearer {read_token}"},
                )
                assert r.status_code == 403, (
                    f"READ token should not PATCH pictures, got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_cannot_batch_apply_scores(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, read_token = _setup_server_with_pictures(
                tmp
            )
            try:
                payload = {"scores": {str(pid): 1 for pid in picture_ids}}
                r = TestClient(server.api).post(
                    f"{API}/pictures/apply-scores",
                    json=payload,
                    headers={"Authorization": f"Bearer {read_token}"},
                )
                assert r.status_code == 403, (
                    f"READ token should not batch-set scores, got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_cannot_change_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, read_token = _setup_server_with_pictures(
                tmp
            )
            try:
                r = TestClient(server.api).post(
                    f"{API}/users/me/auth",
                    json={
                        "current_password": "ownerpass1",
                        "new_password": "hacked12345",
                    },
                    headers={"Authorization": f"Bearer {read_token}"},
                )
                assert r.status_code == 403, (
                    f"READ token should not change password, got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_cannot_create_new_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, read_token = _setup_server_with_pictures(
                tmp
            )
            try:
                r = TestClient(server.api).post(
                    f"{API}/users/me/token",
                    json={"description": "escalated", "scope": "ALL"},
                    headers={"Authorization": f"Bearer {read_token}"},
                )
                assert r.status_code == 403, (
                    f"READ token should not create tokens, got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_cannot_delete_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, read_token = _setup_server_with_pictures(
                tmp
            )
            try:
                # The owner creates a second token to give us an ID to target.
                r2 = owner_client.post(
                    f"{API}/users/me/token",
                    json={"description": "victim token", "scope": "READ"},
                )
                assert r2.status_code == 200, r2.text
                victim_id = r2.json()["token_id"]

                r = TestClient(server.api).delete(
                    f"{API}/users/me/token/{victim_id}",
                    headers={"Authorization": f"Bearer {read_token}"},
                )
                assert r.status_code == 403, (
                    f"READ token should not delete tokens, got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_cannot_upload_watermark(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, read_token = _setup_server_with_pictures(
                tmp
            )
            try:
                png = _make_png_bytes()
                r = TestClient(server.api).post(
                    f"{API}/users/me/watermark",
                    files=[("file", ("wm.png", png, "image/png"))],
                    headers={"Authorization": f"Bearer {read_token}"},
                )
                assert r.status_code == 403, (
                    f"READ token should not upload watermark, got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_cannot_patch_user_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, read_token = _setup_server_with_pictures(
                tmp
            )
            try:
                r = TestClient(server.api).patch(
                    f"{API}/users/me/config",
                    json={"max_vram_gb": 0},
                    headers={"Authorization": f"Bearer {read_token}"},
                )
                assert r.status_code == 403, (
                    f"READ token should not PATCH user config, got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_cannot_restore_deleted_pictures(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, read_token = _setup_server_with_pictures(
                tmp
            )
            try:
                r = TestClient(server.api).post(
                    f"{API}/pictures/scrapheap/restore",
                    json={"picture_ids": picture_ids[:2]},
                    headers={"Authorization": f"Bearer {read_token}"},
                )
                assert r.status_code == 403, (
                    f"READ token should not restore pictures, got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 2. READ token must not access owner-restricted endpoints
# ---------------------------------------------------------------------------


class TestReadTokenBlocksOwnerData:
    """A READ token must not be able to read privileged owner data."""

    def test_cannot_read_user_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, read_token = _setup_server_with_pictures(
                tmp
            )
            try:
                r = TestClient(server.api).get(
                    f"{API}/users/me/config",
                    headers={"Authorization": f"Bearer {read_token}"},
                )
                assert r.status_code == 403, (
                    f"READ token should not read user config (contains secrets), "
                    f"got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_cannot_list_tokens(self):
        """READ token must not enumerate the owner's other token metadata."""
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, read_token = _setup_server_with_pictures(
                tmp
            )
            try:
                r = TestClient(server.api).get(
                    f"{API}/users/me/token",
                    headers={"Authorization": f"Bearer {read_token}"},
                )
                assert r.status_code == 403, (
                    f"READ token should not list all tokens (information disclosure), "
                    f"got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_cannot_read_filesystem_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, read_token = _setup_server_with_pictures(
                tmp
            )
            try:
                r = TestClient(server.api).get(
                    f"{API}/server-config/filesystem-roots",
                    headers={"Authorization": f"Bearer {read_token}"},
                )
                assert r.status_code == 403, (
                    f"READ token must not expose filesystem root paths, "
                    f"got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_cannot_read_watch_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, read_token = _setup_server_with_pictures(
                tmp
            )
            try:
                r = TestClient(server.api).get(
                    f"{API}/server-config/watch-folders",
                    headers={"Authorization": f"Bearer {read_token}"},
                )
                assert r.status_code == 403, (
                    f"READ token must not expose watch folder paths, "
                    f"got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_cannot_browse_filesystem(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, read_token = _setup_server_with_pictures(
                tmp
            )
            try:
                r = TestClient(server.api).get(
                    f"{API}/filesystem/browse",
                    headers={"Authorization": f"Bearer {read_token}"},
                )
                assert r.status_code == 403, (
                    f"READ token must not browse server filesystem, "
                    f"got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_cannot_detect_sidecars(self):
        """READ token must not walk the server filesystem via sidecar detection."""
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, read_token = _setup_server_with_pictures(
                tmp
            )
            try:
                r = TestClient(server.api).get(
                    f"{API}/reference-folders/detect-sidecars",
                    params={"path": tmp},
                    headers={"Authorization": f"Bearer {read_token}"},
                )
                assert r.status_code == 403, (
                    f"READ token must not probe the filesystem via detect-sidecars, "
                    f"got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_cannot_access_shared_resource_ids(self):
        """READ token must not enumerate which resources have been shared."""
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, read_token = _setup_server_with_pictures(
                tmp
            )
            try:
                r = TestClient(server.api).get(
                    f"{API}/users/me/shared-resource-ids",
                    params={"resource_type": "picture"},
                    headers={"Authorization": f"Bearer {read_token}"},
                )
                assert r.status_code == 403, (
                    f"READ token must not enumerate shared resource IDs, "
                    f"got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_cannot_revoke_tokens_for_resource(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, read_token = _setup_server_with_pictures(
                tmp
            )
            try:
                r = TestClient(server.api).delete(
                    f"{API}/users/me/tokens/by-resource",
                    params={"resource_type": "picture", "resource_id": picture_ids[0]},
                    headers={"Authorization": f"Bearer {read_token}"},
                )
                assert r.status_code == 403, (
                    f"READ token must not revoke tokens, got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 3. Resource-scoped READ token must not access other resources
# ---------------------------------------------------------------------------


class TestResourceScopedReadTokenIsolation:
    """A token scoped to resource A must not expose resource B."""

    def _setup_two_picture_sets(self, tmp: str):
        """Create two picture sets with one picture each; issue a token for set A only."""
        config_path = f"{tmp}/server-config.json"
        server = Server(config_path)
        server.__enter__()
        client = TestClient(server.api, raise_server_exceptions=True)

        r = client.post(
            f"{API}/login", json={"username": "owner", "password": "ownerpass1"}
        )
        assert r.status_code == 200, r.text

        # Upload two pictures.
        for png_bytes, name in [
            (_make_png_bytes(64, 64), "picA.png"),
            (_make_png_bytes(48, 48), "picB.png"),
        ]:
            import_status = upload_pictures_and_wait(
                client, [("file", (name, png_bytes, "image/png"))], timeout_s=15
            )
            assert import_status["status"] == "completed", (
                f"Import failed: {import_status}"
            )

        r = client.get(f"{API}/pictures")
        assert r.status_code == 200, r.text
        all_ids = [p["id"] for p in r.json()]
        assert len(all_ids) >= 2

        pic_a_id, pic_b_id = all_ids[0], all_ids[1]

        # Create two picture sets.
        r = client.post(f"{API}/picture_sets", json={"name": "Set A"})
        assert r.status_code == 200, r.text
        set_a_id = r.json()["picture_set"]["id"]

        r = client.post(f"{API}/picture_sets", json={"name": "Set B"})
        assert r.status_code == 200, r.text
        set_b_id = r.json()["picture_set"]["id"]

        # Add pictures to their respective sets.
        r = client.post(
            f"{API}/picture_sets/{set_a_id}/members/{pic_a_id}",
        )
        assert r.status_code in {200, 201, 204}, r.text

        r = client.post(
            f"{API}/picture_sets/{set_b_id}/members/{pic_b_id}",
        )
        assert r.status_code in {200, 201, 204}, r.text

        # Create a READ token scoped only to Set A.
        r = client.post(
            f"{API}/users/me/token",
            json={
                "description": "set A token",
                "scope": "READ",
                "resource_type": "picture_set",
                "resource_id": set_a_id,
            },
        )
        assert r.status_code == 200, r.text
        token_a = r.json()["token"]

        return server, client, set_a_id, set_b_id, pic_a_id, pic_b_id, token_a

    def test_scoped_token_cannot_list_all_picture_sets(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, set_a, set_b, pic_a, pic_b, token_a = (
                self._setup_two_picture_sets(tmp)
            )
            try:
                r = TestClient(server.api).get(
                    f"{API}/picture_sets",
                    headers={"Authorization": f"Bearer {token_a}"},
                )
                assert r.status_code == 200, r.text
                returned_ids = {ps["id"] for ps in r.json()}
                assert set_b not in returned_ids, (
                    "Token for set A exposed set B in the listing"
                )
            finally:
                server.__exit__(None, None, None)

    def test_scoped_token_cannot_fetch_other_picture_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, set_a, set_b, pic_a, pic_b, token_a = (
                self._setup_two_picture_sets(tmp)
            )
            try:
                r = TestClient(server.api).get(
                    f"{API}/picture_sets/{set_b}",
                    headers={"Authorization": f"Bearer {token_a}"},
                )
                assert r.status_code in {403, 404}, (
                    f"Token for set A must not read set B, got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_scoped_token_cannot_access_pictures_outside_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, set_a, set_b, pic_a, pic_b, token_a = (
                self._setup_two_picture_sets(tmp)
            )
            try:
                r = TestClient(server.api).get(
                    f"{API}/pictures/{pic_b}/metadata",
                    headers={"Authorization": f"Bearer {token_a}"},
                )
                assert r.status_code in {403, 404}, (
                    f"Token for set A must not read picture from set B, "
                    f"got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_stats_cannot_leak_out_of_scope_pictures(self):
        """GET /pictures/stats must be limited to the token's authorised set."""
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, set_a, set_b, pic_a, pic_b, token_a = (
                self._setup_two_picture_sets(tmp)
            )
            try:
                r = TestClient(server.api).get(
                    f"{API}/pictures/stats",
                    headers={"Authorization": f"Bearer {token_a}"},
                )
                assert r.status_code == 200, r.text
                data = r.json()
                assert data["total"] <= 1, (
                    f"Token for set A (1 picture) reported total={data['total']}; "
                    "out-of-scope pictures from set B leaked into stats"
                )
            finally:
                server.__exit__(None, None, None)

    def test_search_cannot_leak_out_of_scope_pictures(self):
        """GET /pictures/search must not return pictures outside the token's set."""
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, set_a, set_b, pic_a, pic_b, token_a = (
                self._setup_two_picture_sets(tmp)
            )
            try:
                r = TestClient(server.api).get(
                    f"{API}/pictures/search",
                    params={"query": "picture"},
                    headers={"Authorization": f"Bearer {token_a}"},
                )
                assert r.status_code == 200, r.text
                result_ids = {p["id"] for p in r.json()}
                assert pic_b not in result_ids, (
                    "Token for set A returned picture from set B in /pictures/search"
                )
            finally:
                server.__exit__(None, None, None)

    def test_likeness_groups_cannot_leak_out_of_scope_pictures(self):
        """GET /pictures/likeness-groups must not include pictures outside the token's set."""
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, set_a, set_b, pic_a, pic_b, token_a = (
                self._setup_two_picture_sets(tmp)
            )
            try:
                r = TestClient(server.api).get(
                    f"{API}/pictures/likeness-groups",
                    headers={"Authorization": f"Bearer {token_a}"},
                )
                assert r.status_code == 200, r.text
                all_ids = {pid for group in r.json() for pid in group}
                assert pic_b not in all_ids, (
                    "Token for set A returned picture from set B in /pictures/likeness-groups"
                )
            finally:
                server.__exit__(None, None, None)

    def test_picture_scoped_token_cannot_list_whole_library(self):
        """A single-picture share token must only ever resolve its own picture.

        Regression for the BOLA hole where /pictures, /pictures/stream and
        /pictures/count handled picture_set/project/character scopes but let a
        ``resource_type='picture'`` token fall through to an unrestricted query,
        leaking the entire library's grid metadata.
        """
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, set_a, set_b, pic_a, pic_b, token_a = (
                self._setup_two_picture_sets(tmp)
            )
            try:
                # Mint a token scoped to a single picture (pic_a).
                r = owner_client.post(
                    f"{API}/users/me/token",
                    json={
                        "description": "single picture token",
                        "scope": "READ",
                        "resource_type": "picture",
                        "resource_id": pic_a,
                    },
                )
                assert r.status_code == 200, r.text
                pic_token = r.json()["token"]

                client = TestClient(server.api)
                hdr = {"Authorization": f"Bearer {pic_token}"}
                for path in ("/pictures", "/pictures?fields=grid"):
                    r = client.get(f"{API}{path}", headers=hdr)
                    assert r.status_code == 200, r.text
                    ids = {p["id"] for p in r.json()}
                    assert ids <= {pic_a}, (
                        f"Single-picture token leaked other pictures via {path}: {ids}"
                    )
                # The count endpoint must not count the whole library either.
                r = client.get(f"{API}/pictures/count", headers=hdr)
                assert r.status_code == 200, r.text
                assert r.json().get("count") in (0, 1), (
                    f"Single-picture token saw count={r.json().get('count')}; "
                    "out-of-scope pictures leaked into /pictures/count"
                )
            finally:
                server.__exit__(None, None, None)

    def test_scoped_token_cannot_read_other_picture_tags(self):
        """Set-A token must not read tags/predictions of a set-B picture, but
        must still read its own (no over-blocking)."""
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, set_a, set_b, pic_a, pic_b, token_a = (
                self._setup_two_picture_sets(tmp)
            )
            try:
                client = TestClient(server.api)
                hdr = {"Authorization": f"Bearer {token_a}"}
                for path in (
                    f"/pictures/{pic_b}/tags",
                    f"/pictures/{pic_b}/tag_predictions",
                ):
                    r = client.get(f"{API}{path}", headers=hdr)
                    assert r.status_code in {403, 404}, (
                        f"Set-A token read {path} belonging to set B, "
                        f"got {r.status_code}: {r.text}"
                    )
                # In-scope picture must still be readable.
                r = client.get(f"{API}/pictures/{pic_a}/tags", headers=hdr)
                assert r.status_code == 200, (
                    f"Set-A token wrongly blocked from its own picture's tags: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_scoped_token_cannot_read_cross_resource_summaries(self):
        """A picture_set-scoped token must not read project or character
        summaries (different resource type) or aggregate category counts."""
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, set_a, set_b, pic_a, pic_b, token_a = (
                self._setup_two_picture_sets(tmp)
            )
            try:
                client = TestClient(server.api)
                hdr = {"Authorization": f"Bearer {token_a}"}
                for path in (
                    "/projects/1/summary",
                    "/characters/1/summary",
                    "/characters/ALL/summary",
                ):
                    r = client.get(f"{API}{path}", headers=hdr)
                    assert r.status_code == 403, (
                        f"picture_set token reached {path}, "
                        f"got {r.status_code}: {r.text}"
                    )
            finally:
                server.__exit__(None, None, None)

    def test_scoped_token_cannot_list_project_attachments(self):
        """A picture_set-scoped token must be rejected by the project-attachment
        endpoints (wrong resource type), regardless of include_attachments."""
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, set_a, set_b, pic_a, pic_b, token_a = (
                self._setup_two_picture_sets(tmp)
            )
            try:
                client = TestClient(server.api)
                hdr = {"Authorization": f"Bearer {token_a}"}
                r = client.get(f"{API}/projects/1/attachments", headers=hdr)
                assert r.status_code == 403, (
                    f"picture_set token reached project attachments, "
                    f"got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_export_cannot_include_out_of_scope_pictures(self):
        """GET /pictures/export must not package pictures outside the token's set."""
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, set_a, set_b, pic_a, pic_b, token_a = (
                self._setup_two_picture_sets(tmp)
            )
            try:
                scoped = TestClient(server.api)
                r = scoped.get(
                    f"{API}/pictures/export",
                    headers={"Authorization": f"Bearer {token_a}"},
                )
                assert r.status_code == 200, r.text
                task_id = r.json()["task_id"]

                deadline = time.monotonic() + 30
                status = None
                while time.monotonic() < deadline:
                    sr = scoped.get(
                        f"{API}/pictures/export/status",
                        params={"task_id": task_id},
                        headers={"Authorization": f"Bearer {token_a}"},
                    )
                    assert sr.status_code == 200, sr.text
                    status = sr.json()
                    if status["status"] in ("completed", "failed"):
                        break
                    time.sleep(0.1)

                assert status and status["status"] == "completed", (
                    f"Export task did not complete in time: {status}"
                )
                # `total` is set after scope filtering — must be 1, not 2
                assert status["total"] == 1, (
                    f"Export for set A (1 picture) reported total={status['total']}; "
                    "out-of-scope pictures from set B may have been included"
                )
            finally:
                server.__exit__(None, None, None)

    # -- Batch POST endpoints (READ_SAFE_POST_PATHS) must scope-filter -------
    #
    # These endpoints take a client-supplied picture-id list and are exempt
    # from the "block non-GET for READ tokens" rule.  A scoped token must only
    # ever receive data for ids inside its grant; posting an out-of-scope id
    # (pic_b) must never leak it back.  Regression guard for the BOLA fix.

    def test_bulk_fetch_tags_cannot_leak_out_of_scope_pictures(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, set_a, set_b, pic_a, pic_b, token_a = (
                self._setup_two_picture_sets(tmp)
            )
            try:
                r = TestClient(server.api).post(
                    f"{API}/pictures/tags/bulk_fetch",
                    json={"picture_ids": [pic_a, pic_b]},
                    headers={"Authorization": f"Bearer {token_a}"},
                )
                assert r.status_code == 200, r.text
                returned_ids = {entry["id"] for entry in r.json()}
                assert pic_b not in returned_ids, (
                    "bulk_fetch tags leaked out-of-scope picture B to a set-A token"
                )
                assert returned_ids <= {pic_a}, (
                    f"bulk_fetch tags returned unexpected ids {returned_ids}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_thumbnails_batch_cannot_leak_out_of_scope_pictures(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, set_a, set_b, pic_a, pic_b, token_a = (
                self._setup_two_picture_sets(tmp)
            )
            try:
                r = TestClient(server.api).post(
                    f"{API}/pictures/thumbnails",
                    json={"ids": [pic_a, pic_b]},
                    headers={"Authorization": f"Bearer {token_a}"},
                )
                assert r.status_code == 200, r.text
                assert str(pic_b) not in r.json(), (
                    "thumbnail batch leaked out-of-scope picture B to a set-A token"
                )
            finally:
                server.__exit__(None, None, None)

    def test_set_membership_cannot_leak_out_of_scope_pictures(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, set_a, set_b, pic_a, pic_b, token_a = (
                self._setup_two_picture_sets(tmp)
            )
            try:
                r = TestClient(server.api).post(
                    f"{API}/picture_sets/membership",
                    json={"picture_ids": [pic_a, pic_b]},
                    headers={"Authorization": f"Bearer {token_a}"},
                )
                assert r.status_code == 200, r.text
                result = r.json()
                assert str(set_b) not in result, (
                    "set membership leaked out-of-scope set B to a set-A token"
                )
                leaked = {pid for pids in result.values() for pid in pids}
                assert pic_b not in leaked, (
                    "set membership leaked out-of-scope picture B to a set-A token"
                )
            finally:
                server.__exit__(None, None, None)

    def test_project_membership_cannot_leak_out_of_scope_pictures(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, set_a, set_b, pic_a, pic_b, token_a = (
                self._setup_two_picture_sets(tmp)
            )
            try:
                r = TestClient(server.api).post(
                    f"{API}/projects/membership",
                    json={"picture_ids": [pic_a, pic_b]},
                    headers={"Authorization": f"Bearer {token_a}"},
                )
                assert r.status_code == 200, r.text
                result = r.json()
                assert pic_b not in result["unassigned_picture_ids"], (
                    "project membership leaked out-of-scope picture B (unassigned)"
                )
                leaked = {
                    pid
                    for pids in result["project_assignments"].values()
                    for pid in pids
                }
                assert pic_b not in leaked, (
                    "project membership leaked out-of-scope picture B (assigned)"
                )
            finally:
                server.__exit__(None, None, None)

    def test_character_membership_cannot_leak_out_of_scope_pictures(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, set_a, set_b, pic_a, pic_b, token_a = (
                self._setup_two_picture_sets(tmp)
            )
            try:
                r = TestClient(server.api).post(
                    f"{API}/characters/membership",
                    json={"picture_ids": [pic_a, pic_b]},
                    headers={"Authorization": f"Bearer {token_a}"},
                )
                assert r.status_code == 200, r.text
                result = r.json()
                assert pic_b not in result["pictures_with_faces"], (
                    "character membership leaked out-of-scope picture B (faces)"
                )
                leaked = {
                    pid
                    for pids in result["character_assignments"].values()
                    for pid in pids
                }
                assert pic_b not in leaked, (
                    "character membership leaked out-of-scope picture B (assigned)"
                )
            finally:
                server.__exit__(None, None, None)

    def test_unassigned_listing_cannot_leak_out_of_scope_pictures(self):
        """character_id=UNASSIGNED must honour token scope. Regression for the
        bypass where an empty intersected id list fell through to no filter and
        the set/character scope was never applied to the UNASSIGNED branch."""
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, set_a, set_b, pic_a, pic_b, token_a = (
                self._setup_two_picture_sets(tmp)
            )
            try:
                client = TestClient(server.api)
                hdr = {"Authorization": f"Bearer {token_a}"}
                for path in (
                    "/pictures?character_id=UNASSIGNED",
                    f"/pictures?character_id=UNASSIGNED&id={pic_b}",
                ):
                    r = client.get(f"{API}{path}", headers=hdr)
                    assert r.status_code == 200, r.text
                    ids = {p["id"] for p in r.json()}
                    assert pic_b not in ids, (
                        f"UNASSIGNED listing leaked out-of-scope picture via {path}: {ids}"
                    )
                r = client.get(
                    f"{API}/pictures/count?character_id=UNASSIGNED", headers=hdr
                )
                assert r.status_code == 200, r.text
                assert r.json().get("count") <= 1, (
                    f"UNASSIGNED count leaked out-of-scope pictures: {r.json()}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_scoped_token_cannot_read_other_picture_fields(self):
        """GET /pictures/{id}/{field} must enforce scope. Regression: it sat
        unguarded between get_picture and get_picture_metadata."""
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, set_a, set_b, pic_a, pic_b, token_a = (
                self._setup_two_picture_sets(tmp)
            )
            try:
                client = TestClient(server.api)
                hdr = {"Authorization": f"Bearer {token_a}"}
                for field in ("file_path", "width", "thumbnail"):
                    r = client.get(f"{API}/pictures/{pic_b}/{field}", headers=hdr)
                    assert r.status_code in {403, 404}, (
                        f"Set-A token read pic_b field '{field}', "
                        f"got {r.status_code}: {r.text}"
                    )
                # In-scope field still readable (no over-block).
                r = client.get(f"{API}/pictures/{pic_a}/width", headers=hdr)
                assert r.status_code == 200, (
                    f"Set-A token wrongly blocked from its own picture field: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_scoped_token_cannot_read_other_picture_character_likeness(self):
        """GET /pictures/{id}/character_likeness must enforce scope. Regression
        (R2): it sat unguarded alongside get_picture / get_picture_metadata /
        get_picture_field and leaked picture existence, likeness scores, and the
        face-extraction ``ready`` flag to any scoped token.

        ML helpers are stubbed so the in-scope (positive) path is deterministic
        and GPU-free; the out-of-scope (negative) path is rejected by
        ``enforce_picture_scope`` before any ML/DB work runs.
        """
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, set_a, set_b, pic_a, pic_b, token_a = (
                self._setup_two_picture_sets(tmp)
            )
            try:
                # A reference character is required by the endpoint's query.
                r = owner_client.post(f"{API}/characters", json={"name": "Ref"})
                assert r.status_code == 200, r.text
                ref_char_id = r.json()["character"]["id"]

                client = TestClient(server.api)
                hdr = {"Authorization": f"Bearer {token_a}"}
                params = {"reference_character_id": str(ref_char_id)}

                # Negative: set-A token must not read set-B picture likeness.
                r = client.get(
                    f"{API}/pictures/{pic_b}/character_likeness",
                    params=params,
                    headers=hdr,
                )
                assert r.status_code in {403, 404}, (
                    f"Set-A token read pic_b character_likeness, "
                    f"got {r.status_code}: {r.text}"
                )

                # Positive: in-scope picture still readable (no over-block).
                # Stub the ML helpers so any face-bearing path is deterministic
                # and never touches the GPU.
                def _fake_select_reference_faces(session, character_id, max_refs=10):
                    return []

                def _fake_compute_likeness(reference_faces, candidate_faces):
                    return {}

                with (
                    patch.object(
                        crud_module,
                        "select_reference_faces_for_character",
                        _fake_select_reference_faces,
                    ),
                    patch.object(
                        crud_module,
                        "compute_character_likeness_for_faces",
                        _fake_compute_likeness,
                    ),
                ):
                    r = client.get(
                        f"{API}/pictures/{pic_a}/character_likeness",
                        params=params,
                        headers=hdr,
                    )
                assert r.status_code == 200, (
                    f"Set-A token wrongly blocked from its own picture's "
                    f"character_likeness: {r.text}"
                )
                body = r.json()
                assert body["picture_id"] == pic_a
                assert "ready" in body, (
                    f"character_likeness response missing 'ready' flag: {body}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_scoped_token_cannot_read_stack_outside_scope(self):
        """Stack read endpoints must not leak out-of-scope pictures. Regression
        for unscoped /stacks/{id}/pictures and /pictures/{id}/stack.

        Uses a single-picture token (allow-set is exactly {pic_a}) so that
        stacking pic_a with pic_b cannot widen scope via set-membership
        propagation the way a set-scoped token would.
        """
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, set_a, set_b, pic_a, pic_b, token_a = (
                self._setup_two_picture_sets(tmp)
            )
            try:
                # Single-picture share token for pic_a only.
                r = owner_client.post(
                    f"{API}/users/me/token",
                    json={
                        "description": "single picture token",
                        "scope": "READ",
                        "resource_type": "picture",
                        "resource_id": pic_a,
                    },
                )
                assert r.status_code == 200, r.text
                pic_token = r.json()["token"]

                # Owner stacks pic_a and pic_b together.
                r = owner_client.post(
                    f"{API}/stacks", json={"picture_ids": [pic_a, pic_b]}
                )
                assert r.status_code in {200, 201}, r.text
                r = owner_client.get(f"{API}/pictures/{pic_a}/stack")
                assert r.status_code == 200, r.text
                stack_id = r.json().get("id")
                assert stack_id is not None, f"no stack id in {r.json()}"

                client = TestClient(server.api)
                hdr = {"Authorization": f"Bearer {pic_token}"}
                r = client.get(
                    f"{API}/stacks/{stack_id}/pictures?fields=metadata", headers=hdr
                )
                assert r.status_code in {200, 404}, r.text
                if r.status_code == 200:
                    ids = {row.get("id") for row in r.json()}
                    assert ids <= {pic_a}, (
                        f"Stack pictures leaked out-of-scope picture(s): {ids}"
                    )
                r = client.get(f"{API}/pictures/{pic_b}/stack", headers=hdr)
                assert r.status_code in {403, 404}, (
                    f"pic_a token learned pic_b's stack, got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def _seed_comfyui_vocab(self, server, pic_a, pic_b):
        """Write distinct ComfyUI model/LoRA JSON onto each picture.

        These columns are pipeline-populated JSON arrays with no owner-facing
        API, so the test seeds them directly via the DB task runner (mirrors the
        direct-write pattern in test_characters_api.py / test_many_to_many).
        """

        def _set(session):
            pic_a_row = session.get(Picture, pic_a)
            pic_b_row = session.get(Picture, pic_b)
            pic_a_row.comfyui_models = '["model-a-only"]'
            pic_a_row.comfyui_loras = '["lora-a-only"]'
            pic_b_row.comfyui_models = '["model-b-only"]'
            pic_b_row.comfyui_loras = '["lora-b-only"]'
            session.add(pic_a_row)
            session.add(pic_b_row)
            session.commit()

        server.vault.db.run_task(_set)

    def test_comfyui_models_cannot_leak_out_of_scope_vocab(self):
        """GET /pictures/comfyui_models must only return model names drawn from
        pictures inside the token's grant; owner/unscoped sees the union."""
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, set_a, set_b, pic_a, pic_b, token_a = (
                self._setup_two_picture_sets(tmp)
            )
            try:
                self._seed_comfyui_vocab(server, pic_a, pic_b)

                # Negative: Set-A token must not see Set-B-only models.
                r = TestClient(server.api).get(
                    f"{API}/pictures/comfyui_models",
                    headers={"Authorization": f"Bearer {token_a}"},
                )
                assert r.status_code == 200, r.text
                scoped = set(r.json())
                assert "model-b-only" not in scoped, (
                    f"Set-A token leaked out-of-scope model vocab: {scoped}"
                )
                assert scoped <= {"model-a-only"}, (
                    f"Set-A token returned unexpected models: {scoped}"
                )

                # Positive: owner/unscoped sees the union (no over-block).
                r = owner_client.get(f"{API}/pictures/comfyui_models")
                assert r.status_code == 200, r.text
                owner_models = set(r.json())
                assert {"model-a-only", "model-b-only"} <= owner_models, (
                    f"Owner did not see full model vocab: {owner_models}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_comfyui_loras_cannot_leak_out_of_scope_vocab(self):
        """GET /pictures/comfyui_loras must only return LoRA names drawn from
        pictures inside the token's grant; owner/unscoped sees the union."""
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, set_a, set_b, pic_a, pic_b, token_a = (
                self._setup_two_picture_sets(tmp)
            )
            try:
                self._seed_comfyui_vocab(server, pic_a, pic_b)

                # Negative: Set-A token must not see Set-B-only LoRAs.
                r = TestClient(server.api).get(
                    f"{API}/pictures/comfyui_loras",
                    headers={"Authorization": f"Bearer {token_a}"},
                )
                assert r.status_code == 200, r.text
                scoped = set(r.json())
                assert "lora-b-only" not in scoped, (
                    f"Set-A token leaked out-of-scope LoRA vocab: {scoped}"
                )
                assert scoped <= {"lora-a-only"}, (
                    f"Set-A token returned unexpected LoRAs: {scoped}"
                )

                # Positive: owner/unscoped sees the union (no over-block).
                r = owner_client.get(f"{API}/pictures/comfyui_loras")
                assert r.status_code == 200, r.text
                owner_loras = set(r.json())
                assert {"lora-a-only", "lora-b-only"} <= owner_loras, (
                    f"Owner did not see full LoRA vocab: {owner_loras}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_list_all_tags_cannot_leak_out_of_scope_vocab(self):
        """GET /tags must only return tag values (and counts) drawn from
        pictures inside the token's grant; owner/unscoped sees the union."""
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, set_a, set_b, pic_a, pic_b, token_a = (
                self._setup_two_picture_sets(tmp)
            )
            try:
                # Seed distinct tags per picture via the owner API.
                r = owner_client.post(
                    f"{API}/pictures/{pic_a}/tags", json={"tag": "tag-a-only"}
                )
                assert r.status_code == 200, r.text
                r = owner_client.post(
                    f"{API}/pictures/{pic_b}/tags", json={"tag": "tag-b-only"}
                )
                assert r.status_code == 200, r.text

                # Negative: Set-A token must not see the Set-B-only tag, and the
                # counts it does see must reflect only the in-scope picture.
                r = TestClient(server.api).get(
                    f"{API}/tags",
                    headers={"Authorization": f"Bearer {token_a}"},
                )
                assert r.status_code == 200, r.text
                scoped = {row["tag"]: row["count"] for row in r.json()}
                assert "tag-b-only" not in scoped, (
                    f"Set-A token leaked out-of-scope tag vocab: {scoped}"
                )
                assert scoped.get("tag-a-only") == 1, (
                    f"Set-A token tag count wrong (must reflect only Set A): {scoped}"
                )

                # Positive: owner/unscoped sees the union (no over-block).
                r = owner_client.get(f"{API}/tags")
                assert r.status_code == 200, r.text
                owner_tags = {row["tag"] for row in r.json()}
                assert {"tag-a-only", "tag-b-only"} <= owner_tags, (
                    f"Owner did not see full tag vocab: {owner_tags}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_scoped_token_cannot_read_other_picture_comfyui_workflow(self):
        """GET /comfyui/pictures/{id}/workflow must enforce scope (R1).

        Negative: a Set-A token is rejected on a Set-B picture by
        enforce_picture_scope before any DB read or metadata extraction.
        Positive: the in-scope picture is still served. The workflow extractor
        is stubbed so the in-scope call returns a deterministic 200 (the plain
        test PNG carries no embedded workflow), proving the scope gate let the
        request through rather than over-blocking.
        """
        import pixlstash.routes.comfyui as comfyui_module

        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, set_a, set_b, pic_a, pic_b, token_a = (
                self._setup_two_picture_sets(tmp)
            )
            try:
                client = TestClient(server.api)
                hdr = {"Authorization": f"Bearer {token_a}"}

                def _fake_extract(_metadata):
                    return {"workflow": {"nodes": []}, "models": [], "loras": []}

                # Negative: Set-A token must not reach Set-B picture's workflow.
                # Stubbed extractor would happily return data, so a pass here
                # proves the scope gate (not a missing-workflow 404) blocked it.
                with patch.object(
                    comfyui_module, "extract_comfy_workflow_info", _fake_extract
                ):
                    r = client.get(
                        f"{API}/comfyui/pictures/{pic_b}/workflow", headers=hdr
                    )
                    assert r.status_code in {403, 404}, (
                        f"Set-A token read pic_b ComfyUI workflow, "
                        f"got {r.status_code}: {r.text}"
                    )

                    # Positive: the in-scope picture is still served (no
                    # over-block) and returns the extracted workflow.
                    r = client.get(
                        f"{API}/comfyui/pictures/{pic_a}/workflow", headers=hdr
                    )
                    assert r.status_code == 200, (
                        f"Set-A token wrongly scope-blocked from its own "
                        f"picture's workflow: {r.status_code} {r.text}"
                    )
            finally:
                server.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 4. Expired token is rejected
# ---------------------------------------------------------------------------


class TestExpiredToken:
    def test_expired_token_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, _ = _setup_server_with_pictures(tmp)
            try:
                from datetime import datetime, timedelta

                past = (datetime.utcnow() - timedelta(days=1)).isoformat()
                r = owner_client.post(
                    f"{API}/users/me/token",
                    json={
                        "description": "expired",
                        "scope": "READ",
                        "expires_at": past,
                    },
                )
                assert r.status_code == 200, r.text
                expired_token = r.json()["token"]

                r = TestClient(server.api).get(
                    f"{API}/pictures",
                    headers={"Authorization": f"Bearer {expired_token}"},
                )
                assert r.status_code == 401, (
                    f"Expired token should be rejected, got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_today_token_still_valid(self):
        """A token with expires_at=today (normalized to end-of-day) should still work."""
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, _ = _setup_server_with_pictures(tmp)
            try:
                from datetime import datetime

                today_str = datetime.utcnow().strftime("%Y-%m-%d")
                r = owner_client.post(
                    f"{API}/users/me/token",
                    json={
                        "description": "today token",
                        "scope": "READ",
                        "expires_at": today_str,
                    },
                )
                assert r.status_code == 200, r.text
                today_token = r.json()["token"]

                r = TestClient(server.api).get(
                    f"{API}/pictures",
                    headers={"Authorization": f"Bearer {today_token}"},
                )
                assert r.status_code == 200, (
                    f"Token expiring today should still be valid, "
                    f"got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 5. No privilege escalation via token creation
# ---------------------------------------------------------------------------


class TestNoPrivilegeEscalation:
    def test_read_token_cannot_create_all_scope_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, read_token = _setup_server_with_pictures(
                tmp
            )
            try:
                r = TestClient(server.api).post(
                    f"{API}/users/me/token",
                    json={"description": "escalated ALL token", "scope": "ALL"},
                    headers={"Authorization": f"Bearer {read_token}"},
                )
                assert r.status_code == 403, (
                    f"READ token must not create ALL-scope token, "
                    f"got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_read_token_cannot_create_read_token(self):
        """Even creating another READ token via a READ token must be blocked."""
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, read_token = _setup_server_with_pictures(
                tmp
            )
            try:
                r = TestClient(server.api).post(
                    f"{API}/users/me/token",
                    json={"description": "cloned read token", "scope": "READ"},
                    headers={"Authorization": f"Bearer {read_token}"},
                )
                assert r.status_code == 403, (
                    f"READ token must not clone itself, got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_invalid_token_value_rejected(self):
        """A completely fabricated token must not authenticate."""
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, _ = _setup_server_with_pictures(tmp)
            try:
                fake = "AAAAAAAABBBBBBBBCCCCCCCCDDDDDDDDEEEEEEEEFFFFFFFF"
                r = TestClient(server.api).get(
                    f"{API}/pictures",
                    headers={"Authorization": f"Bearer {fake}"},
                )
                assert r.status_code == 401, (
                    f"Fabricated token should be rejected, got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_tampered_token_prefix_rejected(self):
        """Modifying the first byte of a valid token must invalidate it."""
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, read_token = _setup_server_with_pictures(
                tmp
            )
            try:
                # Flip the first character.
                flipped = ("X" if read_token[0] != "X" else "Y") + read_token[1:]
                r = TestClient(server.api).get(
                    f"{API}/pictures",
                    headers={"Authorization": f"Bearer {flipped}"},
                )
                assert r.status_code == 401, (
                    f"Tampered token prefix should be rejected, got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)

    def test_token_in_query_param_only_works_for_read(self):
        """?token= query param must only accept READ-scoped tokens, not ALL."""
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, _ = _setup_server_with_pictures(tmp)
            try:
                r = owner_client.post(
                    f"{API}/users/me/token",
                    json={"description": "all scope", "scope": "ALL"},
                )
                assert r.status_code == 200, r.text
                all_token = r.json()["token"]

                r = TestClient(server.api).get(
                    f"{API}/pictures",
                    params={"token": all_token},
                )
                assert r.status_code == 401, (
                    f"ALL-scope token in ?token= param should be rejected (only READ allowed via URL), "
                    f"got {r.status_code}: {r.text}"
                )
            finally:
                server.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 6. Login brute-force lockout
# ---------------------------------------------------------------------------


class TestLoginBruteForce:
    def test_lockout_after_five_failures(self):
        """After 5 wrong passwords the server returns 429 with Retry-After."""
        with tempfile.TemporaryDirectory() as tmp:
            config_path = f"{tmp}/server-config.json"
            with Server(config_path) as server:
                client = TestClient(server.api, raise_server_exceptions=True)

                # Establish a password.
                r = client.post(
                    f"{API}/login",
                    json={"username": "victim", "password": "correctpass123"},
                )
                assert r.status_code == 200, r.text

                # Hammer with wrong passwords (all ≥8 chars to pass validation).
                for i in range(5):
                    r = client.post(
                        f"{API}/login",
                        json={"username": "victim", "password": f"wrongpass{i}"},
                    )
                    assert r.status_code == 401, (
                        f"Expected 401 on attempt {i + 1}, got {r.status_code}"
                    )

                # The 6th attempt should be blocked.
                r = client.post(
                    f"{API}/login",
                    json={"username": "victim", "password": "wrongpass6"},
                )
                assert r.status_code == 429, (
                    f"Expected 429 lockout after 5 failures, got {r.status_code}: {r.text}"
                )
                assert "Too many" in r.json().get("detail", ""), (
                    f"Unexpected 429 body: {r.text}"
                )

    def test_correct_password_after_lockout_still_blocked(self):
        """Even the correct password is refused while the lockout window is active."""
        with tempfile.TemporaryDirectory() as tmp:
            config_path = f"{tmp}/server-config.json"
            with Server(config_path) as server:
                client = TestClient(server.api, raise_server_exceptions=True)

                r = client.post(
                    f"{API}/login",
                    json={"username": "victim", "password": "correctpass123"},
                )
                assert r.status_code == 200, r.text

                for i in range(5):
                    client.post(
                        f"{API}/login",
                        json={"username": "victim", "password": f"wrongpass{i}"},
                    )

                # Correct password — should still be blocked during lockout.
                r = client.post(
                    f"{API}/login",
                    json={"username": "victim", "password": "correctpass123"},
                )
                assert r.status_code == 429, (
                    f"Correct password during lockout should still return 429, "
                    f"got {r.status_code}: {r.text}"
                )

    def test_no_lockout_for_token_endpoints(self):
        """Failed login via a bad token value should not count toward the lockout
        that gates password logins — but each bad login does; verify the counter."""
        with tempfile.TemporaryDirectory() as tmp:
            config_path = f"{tmp}/server-config.json"
            with Server(config_path) as server:
                client = TestClient(server.api, raise_server_exceptions=True)

                r = client.post(
                    f"{API}/login",
                    json={"username": "victim", "password": "correctpass123"},
                )
                assert r.status_code == 200, r.text

                # 4 failures — not yet locked.
                for i in range(4):
                    client.post(
                        f"{API}/login",
                        json={"username": "victim", "password": f"wrongpass{i}"},
                    )

                # 5th attempt with correct password should succeed (lockout hits at 5+).
                r = client.post(
                    f"{API}/login",
                    json={"username": "victim", "password": "correctpass123"},
                )
                assert r.status_code == 200, (
                    f"4 failures should not yet trigger lockout; "
                    f"correct password on 5th attempt must succeed. Got {r.status_code}"
                )


# ---------------------------------------------------------------------------
# 7. Rate limiter blocks public-path DDoS (login endpoint)
# ---------------------------------------------------------------------------


class TestRateLimiter:
    def test_login_path_rate_limited_after_limit(self):
        """Exceeding _LIMIT requests to the login path returns 429 with Retry-After."""
        with patch.object(rl_module, "_LIMIT", 5):
            with tempfile.TemporaryDirectory() as tmp:
                config_path = f"{tmp}/server-config.json"
                with Server(config_path) as server:
                    client = TestClient(server.api, raise_server_exceptions=True)

                    # Set up credentials so the login path actually processes requests.
                    for _ in range(5):
                        client.post(
                            f"{API}/login",
                            json={"username": "u", "password": "initialpass"},
                        )

                    # This should now be rate limited.
                    r = client.post(
                        f"{API}/login",
                        json={"username": "u", "password": "initialpass"},
                    )
                    assert r.status_code == 429, (
                        f"Expected 429 from rate limiter after {5} hits, "
                        f"got {r.status_code}: {r.text}"
                    )
                    assert "Retry-After" in r.headers

    def test_authenticated_path_not_rate_limited(self):
        """Authenticated API paths bypass the rate limiter entirely."""
        with patch.object(rl_module, "_LIMIT", 3):
            with tempfile.TemporaryDirectory() as tmp:
                config_path = f"{tmp}/server-config.json"
                with Server(config_path) as server:
                    client = TestClient(server.api, raise_server_exceptions=True)

                    r = client.post(
                        f"{API}/login",
                        json={"username": "owner", "password": "ownerpass99"},
                    )
                    assert r.status_code == 200, r.text

                    # More than _LIMIT calls to an authenticated path — must all succeed.
                    for _ in range(20):
                        r = client.get(f"{API}/pictures")
                        assert r.status_code == 200, (
                            f"Authenticated path should not be rate-limited, "
                            f"got {r.status_code}: {r.text}"
                        )

    def test_rate_limit_window_resets(self):
        """After the window expires the counter resets and requests go through again."""
        with (
            patch.object(rl_module, "_LIMIT", 2),
            patch.object(rl_module, "_WINDOW", 1),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                config_path = f"{tmp}/server-config.json"
                with Server(config_path) as server:
                    client = TestClient(server.api, raise_server_exceptions=True)

                    # Exhaust the limit.
                    assert client.post(
                        f"{API}/login", json={"username": "u", "password": "password1"}
                    ).status_code in {200, 401}
                    assert client.post(
                        f"{API}/login", json={"username": "u", "password": "password1"}
                    ).status_code in {200, 401}
                    r = client.post(
                        f"{API}/login", json={"username": "u", "password": "password1"}
                    )
                    assert r.status_code == 429

                    # Wait out the 1-second window.
                    time.sleep(1.2)

                    r = client.post(
                        f"{API}/login", json={"username": "u", "password": "password1"}
                    )
                    assert r.status_code in {200, 401}, (
                        f"After window reset, login should no longer be rate-limited; "
                        f"got {r.status_code}: {r.text}"
                    )


# ---------------------------------------------------------------------------
# 8. Data integrity — pictures and scores survive all attacks
# ---------------------------------------------------------------------------


class TestDataIntegrityUnderAttack:
    """Upload real pictures, run all attack patterns, verify nothing changed."""

    def test_data_intact_after_write_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, read_token = _setup_server_with_pictures(
                tmp
            )
            try:
                # Record baseline scores.
                r = owner_client.get(f"{API}/pictures")
                assert r.status_code == 200, r.text
                original_scores = {str(p["id"]): p.get("score") for p in r.json()}

                attack_client = TestClient(server.api)
                headers = {"Authorization": f"Bearer {read_token}"}

                # Attempt to clobber every score to 1.
                attack_client.post(
                    f"{API}/pictures/apply-scores",
                    json={"scores": {str(pid): 1 for pid in picture_ids}},
                    headers=headers,
                )

                # Attempt to delete every picture.
                for pid in picture_ids:
                    attack_client.delete(f"{API}/pictures/{pid}", headers=headers)

                # Attempt PATCH on first picture.
                attack_client.patch(
                    f"{API}/pictures/{picture_ids[0]}",
                    json={"score": 1, "deleted": True},
                    headers=headers,
                )

                # Attempt to upload a replacement picture.
                attack_client.post(
                    f"{API}/pictures/import",
                    files=[("file", ("evil.png", _make_png_bytes(), "image/png"))],
                    headers=headers,
                )

                _assert_pictures_intact(owner_client, picture_ids, original_scores)
            finally:
                server.__exit__(None, None, None)

    def test_data_intact_after_privilege_escalation_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, read_token = _setup_server_with_pictures(
                tmp
            )
            try:
                r = owner_client.get(f"{API}/pictures")
                original_scores = {str(p["id"]): p.get("score") for p in r.json()}

                attack_client = TestClient(server.api)
                headers = {"Authorization": f"Bearer {read_token}"}

                # Try to create a super-token.
                attack_client.post(
                    f"{API}/users/me/token",
                    json={"description": "escalated", "scope": "ALL"},
                    headers=headers,
                )

                # Try to change password.
                attack_client.post(
                    f"{API}/users/me/auth",
                    json={
                        "current_password": "ownerpass1",
                        "new_password": "hacked12345",
                    },
                    headers=headers,
                )

                # Still able to log in with the original password.
                fresh_client = TestClient(server.api)
                r = fresh_client.post(
                    f"{API}/login",
                    json={"username": "owner", "password": "ownerpass1"},
                )
                assert r.status_code == 200, (
                    f"Owner password must be unchanged after attack attempts, "
                    f"got {r.status_code}: {r.text}"
                )

                _assert_pictures_intact(owner_client, picture_ids, original_scores)
            finally:
                server.__exit__(None, None, None)

    def test_data_intact_after_brute_force_lockout(self):
        """Trigger the login lockout, then verify pictures and scores are untouched."""
        with tempfile.TemporaryDirectory() as tmp:
            server, owner_client, picture_ids, _ = _setup_server_with_pictures(tmp)
            try:
                r = owner_client.get(f"{API}/pictures")
                original_scores = {str(p["id"]): p.get("score") for p in r.json()}

                # Trigger lockout.
                attack_client = TestClient(server.api)
                for i in range(6):
                    attack_client.post(
                        f"{API}/login",
                        json={"username": "owner", "password": f"wrongpass{i}"},
                    )

                _assert_pictures_intact(owner_client, picture_ids, original_scores)
            finally:
                server.__exit__(None, None, None)

    def test_data_intact_after_rate_limit_barrage(self):
        """Fire requests well past the rate limit; data must survive the barrage."""
        with patch.object(rl_module, "_LIMIT", 5):
            with tempfile.TemporaryDirectory() as tmp:
                server, owner_client, picture_ids, _ = _setup_server_with_pictures(tmp)
                try:
                    r = owner_client.get(f"{API}/pictures")
                    original_scores = {str(p["id"]): p.get("score") for p in r.json()}

                    # Hammer the login endpoint way past the limit.
                    attack_client = TestClient(server.api)
                    for i in range(20):
                        attack_client.post(
                            f"{API}/login",
                            json={"username": "anyuser", "password": f"anypassword{i}"},
                        )

                    _assert_pictures_intact(owner_client, picture_ids, original_scores)
                finally:
                    server.__exit__(None, None, None)
