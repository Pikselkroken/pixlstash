"""Token identity across deletion and across a database restore (issue #666).

Two related properties, both about a token id being used as a reference from
somewhere that can outlive the row it names.

1. ``usertoken.id`` is a plain SQLite ``INTEGER PRIMARY KEY`` - a rowid alias -
   so SQLite hands out the lowest free value and reissues a deleted token's id
   to the next token created.  ``UserToken.public_id`` is random and never
   reissued, and it is what the in-memory session-to-token maps are keyed on,
   so revoking a token can only ever end the sessions that token created.

2. A full restore replaces the whole database file.  Everything ``AuthService``
   holds in memory was derived from the previous file, so it is cleared once the
   swap is done.  The first property does **not** subsume the second: a restored
   snapshot brings its own ``public_id`` values back with it, so in-memory state
   that outlived the swap is stale regardless of how ids are minted.
"""

import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from pixlstash.db_models import Picture, UserToken
from pixlstash.server import Server

API_PREFIX = "/api/v1"


@pytest.fixture(scope="module")
def server():
    """One Server for the module; background workers off so restore is stable."""
    with tempfile.TemporaryDirectory() as tmp:
        config_path = os.path.join(tmp, "server-config.json")
        with open(config_path, "w") as fh:
            json.dump({"disable_background_workers": True}, fh)
        with Server(config_path) as srv:
            yield srv


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _claim_owner(client) -> None:
    """Claim the empty owner account so the token endpoints become reachable."""
    response = client.post(
        f"{API_PREFIX}/login",
        json={"username": "testuser", "password": "testpassword"},
    )
    assert response.status_code == 200, response.text


def _create_token(client, **payload) -> dict:
    response = client.post(f"{API_PREFIX}/users/me/token", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _public_id_of(server, token_id: int):
    return server.hub_engine.run_immediate_read_task(
        lambda s: s.get(UserToken, token_id).public_id
    )


def _delete_token_row_out_of_band(server, token_id: int) -> None:
    """Delete a token row without going through ``AuthService.delete_token``.

    This is what the restore path's ``_clear_api_tokens`` does: it removes rows
    straight from the database, so no session sweep runs and the integer id
    becomes free for the next token created.
    """

    def _do(session):
        session.delete(session.get(UserToken, token_id))
        session.commit()

    server.hub_engine.run_task(_do)


def _add_picture(server, filename: str, description: str) -> Picture:
    open(os.path.join(server.vault.image_root, filename), "wb").close()

    def _do(session):
        pic = Picture(file_path=filename, filename=filename, description=description)
        session.add(pic)
        session.commit()
        session.refresh(pic)
        return pic

    return server.vault.db.run_task(_do)


def _wipe_tokens(server) -> None:
    def _do(session):
        for token in session.exec(select(UserToken)).all():
            session.delete(token)
        session.commit()

    server.hub_engine.run_task(_do)
    server.auth._clear_all_sessions()
    server.auth._flush_token_cache()


# ---------------------------------------------------------------------------
# Part 1 - a token's public id is never reissued
# ---------------------------------------------------------------------------


def test_a_deleted_tokens_public_id_is_never_reissued(server):
    """Integer ids come back after a deletion; public ids do not.

    The first assertion is the mechanism this whole change defends against, so
    it is pinned deliberately rather than left implicit: if SQLite ever stopped
    reusing the ids, the reason for ``public_id`` would have changed and this
    test should be read again.
    """
    _wipe_tokens(server)
    with TestClient(server.api) as owner:
        _claim_owner(owner)

        created = [
            _create_token(owner, description=f"token {n}", scope="ALL")
            for n in range(5)
        ]
        original_int_ids = [token["token_id"] for token in created]
        original_public_ids = {
            _public_id_of(server, token["token_id"]) for token in created
        }
        assert None not in original_public_ids
        assert len(original_public_ids) == 5, "every token gets its own public id"

        for token in created:
            assert (
                owner.delete(
                    f"{API_PREFIX}/users/me/token/{token['token_id']}"
                ).status_code
                == 200
            )

        replacement = _create_token(owner, description="replacement", scope="ALL")

    assert replacement["token_id"] in original_int_ids, (
        "the integer primary key is expected to be reused after deletion - "
        "that is the behaviour public_id exists to work around"
    )
    assert _public_id_of(server, replacement["token_id"]) not in original_public_ids, (
        "a public id must never be handed to a second token"
    )


def test_revoking_a_token_ends_its_own_sessions_and_no_others(server):
    """A session is linked to its token by public id, not by integer id.

    The setup reproduces the id-reuse window exactly: a token's row is removed
    out of band (as a restore's token clear does), so no sweep runs, and the
    replacement token is handed the freed integer id.  Keyed on that integer,
    revoking the replacement would end the *first* token's session - the wrong
    session - while leaving the replacement's own untouched.
    """
    _wipe_tokens(server)
    with TestClient(server.api) as owner:
        _claim_owner(owner)
        first = _create_token(owner, description="first", scope="ALL")

        # A session minted from the first token.
        with TestClient(server.api) as first_client:
            signed_in = first_client.post(
                f"{API_PREFIX}/login", json={"token": first["token"]}
            )
            assert signed_in.status_code == 200, signed_in.text
            first_session = first_client.cookies["session_id"]
            assert first_client.get(f"{API_PREFIX}/protected").status_code == 200

            _delete_token_row_out_of_band(server, first["token_id"])

            replacement = _create_token(owner, description="replacement", scope="ALL")
            assert replacement["token_id"] == first["token_id"], (
                "this test needs the freed integer id to be reused; it was not, "
                "so it is not exercising what it claims to"
            )

            with TestClient(server.api) as replacement_client:
                signed_in = replacement_client.post(
                    f"{API_PREFIX}/login", json={"token": replacement["token"]}
                )
                assert signed_in.status_code == 200, signed_in.text
                assert (
                    replacement_client.get(f"{API_PREFIX}/protected").status_code == 200
                )

                deleted = owner.delete(
                    f"{API_PREFIX}/users/me/token/{replacement['token_id']}"
                )
                assert deleted.status_code == 200, deleted.text

                # Positive: the revoked token's own session is ended.
                assert (
                    replacement_client.get(f"{API_PREFIX}/protected").status_code == 401
                )

            # Negative: the unrelated session is not, because it was never
            # recorded under the integer id the replacement inherited.
            assert first_session in server.auth.active_session_ids, (
                "revoking the replacement token ended a session it never "
                "created - the session map is keyed on a reusable id"
            )


# ---------------------------------------------------------------------------
# Part 2 - a restore clears the in-memory authentication state
# ---------------------------------------------------------------------------


def test_a_full_restore_clears_the_token_cache_and_every_session(server):
    """Nothing that authenticated before the swap authenticates after it.

    A verified token is cached for five minutes and a session lives in a plain
    dictionary, neither of which the file swap touches.  Without the reset both
    keep working against a database that no longer holds the rows they were
    issued from, which is fail-open - and the restore's own token clear does not
    help, because the cache is consulted before the database is.
    """
    _wipe_tokens(server)
    with TestClient(server.api) as owner:
        _claim_owner(owner)
        token = _create_token(owner, description="pre-restore", scope="ALL")
        headers = {"Authorization": f"Bearer {token['token']}"}

        snapshot = server.vault.snapshot_service.create_snapshot("MANUAL")

        # Put the token in the cache and open a session from it.
        with TestClient(server.api) as bearer_client:
            assert (
                bearer_client.get(
                    f"{API_PREFIX}/protected", headers=headers
                ).status_code
                == 200
            )
        assert server.auth._token_cache, "the token should now be cached"

        with TestClient(server.api) as session_client:
            signed_in = session_client.post(
                f"{API_PREFIX}/login", json={"token": token["token"]}
            )
            assert signed_in.status_code == 200, signed_in.text
            assert session_client.get(f"{API_PREFIX}/protected").status_code == 200
            assert server.auth.active_session_ids
            assert server.auth._sessions_by_token_public_id

            report = server.vault.restore_service.restore_full(snapshot.id)
            assert not report.errors, report.errors

            # In-memory state is empty...
            assert server.auth._token_cache == {}
            assert server.auth.active_session_ids == {}
            assert server.auth._sessions_by_token_public_id == {}
            assert server.auth._token_public_id_by_session == {}
            assert server.auth._guest_sessions == {}

            # ...and neither credential works any more.
            assert session_client.get(f"{API_PREFIX}/protected").status_code == 401

        with TestClient(server.api) as bearer_client:
            assert (
                bearer_client.get(
                    f"{API_PREFIX}/protected", headers=headers
                ).status_code
                == 401
            ), "a token cached before the restore must not keep authenticating after it"


def test_a_full_restore_still_restores_and_leaves_the_owner_able_to_sign_in(server):
    """The reset must not break the restore, or lock the owner out of the vault.

    Over-blocking is its own regression: clearing the auth state is only correct
    if the owner can sign in again immediately afterwards and the restore did
    the thing it was asked to do.
    """
    _wipe_tokens(server)
    with TestClient(server.api) as owner:
        _claim_owner(owner)
        picture = _add_picture(server, "token-identity.jpg", "before")
        snapshot = server.vault.snapshot_service.create_snapshot("MANUAL")

        def _mutate(session):
            session.get(Picture, picture.id).description = "after"
            session.commit()

        server.vault.db.run_task(_mutate)
        assert (
            server.vault.db.run_immediate_read_task(
                lambda s: s.get(Picture, picture.id).description
            )
            == "after"
        )

        report = server.vault.restore_service.restore_full(snapshot.id)
        assert not report.errors, report.errors
        assert report.missing_files_count == 0

    # The snapshot's state is back.
    assert (
        server.vault.db.run_immediate_read_task(
            lambda s: s.get(Picture, picture.id).description
        )
        == "before"
    )

    # And the owner's password still works - the credential cache was re-read
    # from the restored database rather than left stale or emptied.
    with TestClient(server.api) as fresh:
        signed_in = fresh.post(
            f"{API_PREFIX}/login",
            json={"username": "testuser", "password": "testpassword"},
        )
        assert signed_in.status_code == 200, signed_in.text
        assert fresh.get(f"{API_PREFIX}/protected").status_code == 200


def test_per_resource_restore_leaves_the_authentication_state_alone(server):
    """A per-resource restore needs no reset, and must not perform one.

    It never replaces the database file and never reads or writes ``usertoken``
    / ``guest_session`` / ``guest_score`` - it upserts picture, set and
    character rows - so nothing held in memory becomes stale.  Signing everyone
    out for it would be gratuitous.
    """
    _wipe_tokens(server)
    with TestClient(server.api) as owner:
        _claim_owner(owner)
        picture = _add_picture(server, "per-resource.jpg", "before")
        snapshot = server.vault.snapshot_service.create_snapshot("MANUAL")

        def _mutate(session):
            session.get(Picture, picture.id).description = "after"
            session.commit()

        server.vault.db.run_task(_mutate)

        token = _create_token(owner, description="survivor", scope="ALL")
        headers = {"Authorization": f"Bearer {token['token']}"}
        with TestClient(server.api) as bearer_client:
            assert (
                bearer_client.get(
                    f"{API_PREFIX}/protected", headers=headers
                ).status_code
                == 200
            )
            cached_before = dict(server.auth._token_cache)
            assert cached_before

            server.vault.restore_service.restore_resource(
                snapshot.id, "picture", picture.id
            )

            # The resource came back...
            assert (
                server.vault.db.run_immediate_read_task(
                    lambda s: s.get(Picture, picture.id).description
                )
                == "before"
            )
            # ...and the session and the cached token are untouched.
            assert server.auth._token_cache.keys() == cached_before.keys()
            assert owner.get(f"{API_PREFIX}/protected").status_code == 200
            assert (
                bearer_client.get(
                    f"{API_PREFIX}/protected", headers=headers
                ).status_code
                == 200
            )
