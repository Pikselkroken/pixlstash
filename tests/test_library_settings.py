"""``similarity_character`` belongs to the library, not to the person.

It is a row id in one vault's character table, so a per-user copy silently names
a different person after a library switch: character 7 in Family Photos and
character 7 in Client Work are not the same face. This is the one setting from
the §5 candidate list that moved to the vault (decided 2026-08-02); hidden tags,
the tag filter and the penalised-tag weights stay in the hub as user preferences.

The API surface is unchanged, so the tests assert the seam is invisible from
outside: one flat config object, two databases behind it.
"""

import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select

from pixlstash.db_models import User, UserToken
from pixlstash.db_models.library_settings import LibrarySettings
from pixlstash.server import Server
from pixlstash.services import library_settings_service

API = "/api/v1"


@pytest.fixture(scope="module")
def server():
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = f"{temp_dir}/server-config.json"
        with open(config_path, "w") as handle:
            json.dump({}, handle)
        with Server(config_path) as srv:
            yield srv


@pytest.fixture(autouse=True)
def clean(server):
    def _wipe(session: Session):
        session.exec(delete(UserToken))
        session.exec(delete(User))
        session.commit()

    server.hub_engine.run_task(_wipe)
    server.auth.password_hash = None
    server.auth.username = None
    server.auth.user = None
    server.auth._clear_all_sessions()
    server.auth._flush_token_cache()
    server.auth._failed_login_attempts = 0
    server.auth._login_lockout_until = 0.0
    server.auth.ensure_user()
    library_settings_service.set_similarity_character(server.vault.db, None)
    yield


def _owner(server) -> TestClient:
    client = TestClient(server.api)
    response = client.post(
        "/login", json={"username": "libset", "password": "libsetpass1"}
    )
    assert response.status_code == 200, response.text
    return client


class TestStorage:
    def test_it_is_stored_in_the_vault(self, server):
        library_settings_service.set_similarity_character(server.vault.db, 7)

        stored = server.vault.db.run_immediate_read_task(
            lambda session: session.exec(select(LibrarySettings)).first()
        )
        assert stored.similarity_character == 7

    def test_it_is_not_stored_in_the_hub(self, server):
        """The hub keeps the column for model compatibility but must not own it."""
        library_settings_service.set_similarity_character(server.vault.db, 7)

        in_hub = server.hub_engine.run_immediate_read_task(
            lambda session: session.exec(select(User)).first().similarity_character
        )
        assert in_hub is None

    def test_clearing_it_works(self, server):
        library_settings_service.set_similarity_character(server.vault.db, 7)
        library_settings_service.set_similarity_character(server.vault.db, None)

        assert (
            library_settings_service.get_similarity_character(server.vault.db) is None
        )

    def test_there_is_exactly_one_row(self, server):
        library_settings_service.set_similarity_character(server.vault.db, 3)
        library_settings_service.set_similarity_character(server.vault.db, 4)

        rows = server.vault.db.run_immediate_read_task(
            lambda session: session.exec(select(LibrarySettings)).all()
        )
        assert len(rows) == 1


class TestTheApiSeamIsInvisible:
    def test_the_config_get_returns_it(self, server):
        library_settings_service.set_similarity_character(server.vault.db, 11)

        body = _owner(server).get(f"{API}/users/me/config").json()

        assert body["similarity_character"] == 11

    def test_the_config_patch_writes_it_to_the_vault(self, server):
        client = _owner(server)

        response = client.patch(
            f"{API}/users/me/config", json={"similarity_character": 5}
        )

        assert response.status_code == 200, response.text
        assert library_settings_service.get_similarity_character(server.vault.db) == 5

    def test_patching_it_does_not_touch_the_hub_copy(self, server):
        client = _owner(server)

        client.patch(f"{API}/users/me/config", json={"similarity_character": 5})

        in_hub = server.hub_engine.run_immediate_read_task(
            lambda session: session.exec(select(User)).first().similarity_character
        )
        assert in_hub is None, "the hub must not become a second, stale home for it"

    def test_it_can_be_cleared_through_the_api(self, server):
        client = _owner(server)
        client.patch(f"{API}/users/me/config", json={"similarity_character": 5})

        client.patch(f"{API}/users/me/config", json={"similarity_character": None})

        assert (
            library_settings_service.get_similarity_character(server.vault.db) is None
        )

    def test_patching_other_settings_still_works(self, server):
        """The split must not break the settings that stayed in the hub."""
        client = _owner(server)

        response = client.patch(
            f"{API}/users/me/config",
            json={"theme_mode": "dark", "similarity_character": 9},
        )

        assert response.status_code == 200, response.text
        body = client.get(f"{API}/users/me/config").json()
        assert body["theme_mode"] == "dark"
        assert body["similarity_character"] == 9


class TestItFollowsTheLibrary:
    def test_each_library_keeps_its_own_selection(self, server, tmp_path):
        """The bug this move exists to prevent, end to end.

        Two libraries, two selections. Before the move both would have read the
        same per-user number, so switching would have sorted against whoever
        happened to hold that id in the new library.
        """
        client = _owner(server)
        original = server.library_registry.active_library()
        client.patch(f"{API}/users/me/config", json={"similarity_character": 4})

        other = server.library_registry.create(str(tmp_path / "other"), "Other")
        try:
            server.library_switch.switch_to(other.uuid)

            # A fresh library has made no selection, and must not inherit one.
            assert (
                client.get(f"{API}/users/me/config").json()["similarity_character"]
                is None
            )

            client.patch(f"{API}/users/me/config", json={"similarity_character": 12})
            assert (
                client.get(f"{API}/users/me/config").json()["similarity_character"]
                == 12
            )
        finally:
            server.library_switch.switch_to(original.uuid)

        # Back on the first library, its own selection is intact.
        assert client.get(f"{API}/users/me/config").json()["similarity_character"] == 4

    def test_a_user_preference_does_follow_the_switch(self, server, tmp_path):
        """The other direction: theme is the user's, so it must not be per-library."""
        client = _owner(server)
        original = server.library_registry.active_library()
        client.patch(f"{API}/users/me/config", json={"theme_mode": "dark"})

        other = server.library_registry.create(str(tmp_path / "theme-other"), "Theme")
        try:
            server.library_switch.switch_to(other.uuid)
            assert client.get(f"{API}/users/me/config").json()["theme_mode"] == "dark"
        finally:
            server.library_switch.switch_to(original.uuid)


class TestTheSettingsFingerprint:
    """The library records *that* the owner's weights changed, never what they are.

    Penalised and hidden tags say what someone collects and what they hide. A
    library folder is made to be copied, moved and handed to other people, so
    nothing derived from those settings may be recoverable from it.
    """

    def test_the_vault_stores_a_hash_and_not_the_tags(self, server):
        from pixlstash.services.library_settings_service import (
            reconcile_settings_fingerprint,
        )

        secret_tags = {"a-very-distinctive-tag": 0.5, "another-private-one": 0.9}
        library = server.library_registry.active_library()
        reconcile_settings_fingerprint(
            server.vault.db, library.settings_salt, secret_tags
        )

        stored = server.vault.db.run_immediate_read_task(
            lambda session: session.exec(select(LibrarySettings)).first()
        )
        assert stored.settings_fingerprint
        for tag in secret_tags:
            assert tag not in stored.settings_fingerprint

    def test_the_whole_vault_file_contains_no_tag_names(self, server, tmp_path):
        """The real test of the claim: grep the bytes a recipient would receive."""
        from pixlstash.services.library_settings_service import (
            reconcile_settings_fingerprint,
        )

        marker = "zzz-private-marker-tag"
        library = server.library_registry.active_library()
        reconcile_settings_fingerprint(
            server.vault.db, library.settings_salt, {marker: 0.7}
        )

        vault_bytes = open(
            os.path.join(server.vault.image_root, "vault.db"), "rb"
        ).read()
        assert marker.encode() not in vault_bytes

    def test_the_key_is_not_in_the_library(self, server):
        """Without the salt the hash is unguessable, so the salt must stay in the hub."""
        library = server.library_registry.active_library()
        vault_bytes = open(
            os.path.join(server.vault.image_root, "vault.db"), "rb"
        ).read()

        assert library.settings_salt
        assert library.settings_salt.encode() not in vault_bytes

    def test_identical_settings_hash_differently_per_library(self, server, tmp_path):
        """The salt is per library, so one fingerprint says nothing about another."""
        from pixlstash.services.library_settings_service import (
            compute_settings_fingerprint,
        )

        first = server.library_registry.active_library()
        second = server.library_registry.create(str(tmp_path / "salted"), "Salted")
        settings = {"blurry": 0.5}

        assert compute_settings_fingerprint(
            first.settings_salt, settings
        ) != compute_settings_fingerprint(second.settings_salt, settings)

    def test_the_same_settings_hash_the_same_way(self, server):
        from pixlstash.services.library_settings_service import (
            compute_settings_fingerprint,
        )

        salt = "a-salt"
        # Order must not matter: a reordered payload is not a change.
        assert compute_settings_fingerprint(salt, {"a": 1.0, "b": 2.0}) == (
            compute_settings_fingerprint(salt, {"b": 2.0, "a": 1.0})
        )

    def test_a_changed_weight_changes_the_hash(self, server):
        from pixlstash.services.library_settings_service import (
            compute_settings_fingerprint,
        )

        salt = "a-salt"
        assert compute_settings_fingerprint(salt, {"a": 1.0}) != (
            compute_settings_fingerprint(salt, {"a": 1.5})
        )


class TestCatchingUpADormantLibrary:
    def test_a_weights_change_while_closed_invalidates_on_open(self, server):
        """The gap this closes: a dormant library never hears about the change."""
        from pixlstash.db_models import Picture
        from pixlstash.services.library_settings_service import (
            reconcile_settings_fingerprint,
        )

        library = server.library_registry.active_library()
        reconcile_settings_fingerprint(
            server.vault.db, library.settings_salt, {"blurry": 0.5}
        )

        def _add_scored_picture(session: Session):
            picture = Picture(file_path="/tmp/dormant.png", smart_score=4.5)
            session.add(picture)
            session.commit()
            session.refresh(picture)
            return picture.id

        picture_id = server.vault.db.run_task(_add_scored_picture)

        # The owner re-weights while this library is closed; on reopening, the
        # fingerprint no longer matches.
        changed = reconcile_settings_fingerprint(
            server.vault.db, library.settings_salt, {"blurry": 0.9}
        )

        assert changed is True
        score = server.vault.db.run_immediate_read_task(
            lambda session: session.get(Picture, picture_id).smart_score
        )
        assert score is None, "the stale cached score should have been cleared"

    def test_no_change_leaves_scores_alone(self, server):
        """Over-invalidating would re-score the whole library on every open."""
        from pixlstash.db_models import Picture
        from pixlstash.services.library_settings_service import (
            reconcile_settings_fingerprint,
        )

        library = server.library_registry.active_library()
        reconcile_settings_fingerprint(
            server.vault.db, library.settings_salt, {"blurry": 0.5}
        )

        def _add(session: Session):
            picture = Picture(file_path="/tmp/unchanged.png", smart_score=3.3)
            session.add(picture)
            session.commit()
            session.refresh(picture)
            return picture.id

        picture_id = server.vault.db.run_task(_add)

        changed = reconcile_settings_fingerprint(
            server.vault.db, library.settings_salt, {"blurry": 0.5}
        )

        assert changed is False
        score = server.vault.db.run_immediate_read_task(
            lambda session: session.get(Picture, picture_id).smart_score
        )
        assert score == pytest.approx(3.3)

    def test_the_first_ever_reconcile_invalidates_nothing(self, server):
        """A library that has never recorded one has nothing to repair."""
        from pixlstash.services.library_settings_service import (
            reconcile_settings_fingerprint,
        )

        library = server.library_registry.active_library()

        def _clear(session: Session):
            row = session.exec(select(LibrarySettings)).first()
            row.settings_fingerprint = None
            session.add(row)
            session.commit()

        server.vault.db.run_task(_clear)

        assert (
            reconcile_settings_fingerprint(
                server.vault.db, library.settings_salt, {"blurry": 0.5}
            )
            is False
        )

    def test_a_missing_salt_is_skipped_rather_than_hashed_unkeyed(self, server):
        """An unkeyed hash in a portable folder is the thing being avoided."""
        from pixlstash.services.library_settings_service import (
            reconcile_settings_fingerprint,
        )

        assert (
            reconcile_settings_fingerprint(server.vault.db, None, {"blurry": 0.5})
            is False
        )
