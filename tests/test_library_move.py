"""Moving pictures from the active library into another one.

The rule the route rests on: a picture is committed in the other library before
anything happens to it here, and what happens here is the ordinary, undoable
move to the Scrapheap. So every test reads BOTH libraries, and the cases where
the copy does not land assert that the source was left alone.

The other library is opened through the same door the route uses
(``open_other_library``), which also proves the route closed it again: the door
holds a lock and would refuse a second caller.
"""

import io
import json
import os
import tempfile
from datetime import datetime, timezone

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session, select

from pixlstash.db_models import (
    Picture,
    PictureSet,
    PictureSetMember,
    PictureStack,
    Tag,
    UserToken,
)
from pixlstash.server import Server
from pixlstash.services.library_switch_service import LibrarySwitchError
from pixlstash.utils.image_processing.image_utils import ImageUtils
from tests.utils import reset_owner_auth

API = "/api/v1"
MOVE = f"{API}/pictures/move-to-library"


@pytest.fixture(scope="module")
def server():
    """One Server for the module, with no background work.

    A tagger sweep landing mid-test would replace the hand-written tags these
    tests move, and nothing here needs a model.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        config_path = f"{temp_dir}/server-config.json"
        with open(config_path, "w") as handle:
            json.dump(
                {
                    "disable_background_workers": True,
                    # So a test can present a remote address at all.
                    "trusted_proxies": ["testclient"],
                },
                handle,
            )
        with Server(config_path) as srv:
            yield srv


@pytest.fixture(scope="module")
def other(server, tmp_path_factory):
    """A second, real library that is not the active one."""
    folder = tmp_path_factory.mktemp("other-library")
    return server.library_registry.create(str(folder), "Other")


@pytest.fixture(autouse=True)
def clean_auth(server):
    """Start each test from a claimed owner with no tokens."""

    reset_owner_auth(server)
    yield


@pytest.fixture
def owner(server) -> TestClient:
    client = TestClient(server.api)
    response = client.post(
        "/login",
        json={"username": "moveowner", "password": "example-moveowner-password"},
    )
    assert response.status_code == 200, response.text
    return client


_SHADE = iter(range(1, 255))


def _png() -> bytes:
    """A picture no other test has made, so a duplicate is one on purpose."""
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (next(_SHADE), 40, 90)).save(buffer, format="PNG")
    return buffer.getvalue()


def _insert(session: Session, picture: Picture, tags) -> int:
    session.add(picture)
    session.flush()
    for tag in tags:
        session.add(Tag(tag=tag, picture_id=picture.id))
    session.commit()
    return picture.id


def _add(server, data: bytes, tags=(), **fields) -> int:
    """Put a picture in the active library and return its id."""
    picture = ImageUtils.create_picture_from_bytes(server.vault.image_root, data)
    for name, value in fields.items():
        setattr(picture, name, value)
    return server.vault.db.run_task(_insert, picture, tags)


def _here(server, picture_id: int) -> Picture:
    return server.vault.db.run_immediate_read_task(
        lambda session: session.get(Picture, picture_id)
    )


def _there(server, other, pixel_sha: str) -> list[dict]:
    """Every row the other library holds for *pixel_sha*, live or scrapheaped."""

    def read(session: Session):
        return [
            {
                "id": pic.id,
                "deleted": pic.deleted,
                "score": pic.score,
                "description": pic.description,
                "created_at": pic.created_at,
                "imported_at": pic.imported_at,
                "file_path": pic.file_path,
                "tags": sorted(
                    session.exec(select(Tag.tag).where(Tag.picture_id == pic.id)).all()
                ),
            }
            for pic in session.exec(
                select(Picture).where(Picture.pixel_sha == pixel_sha)
            ).all()
        ]

    with server.library_switch.open_other_library(other.uuid) as (_library, vault):
        rows = vault.db.run_immediate_read_task(read)
        for row in rows:
            row["on_disk"] = os.path.isfile(
                os.path.join(vault.image_root, row["file_path"])
            )
    return rows


def _move(client, other, ids, **kwargs):
    return client.post(
        MOVE, json={"picture_ids": ids, "library_uuid": other.uuid}, **kwargs
    )


class TestAMove:
    def test_two_requests_in_one_gesture_are_restored_by_one_undo(
        self, server, owner, other
    ):
        first = _add(server, _png())
        second = _add(server, _png())
        headers = {"X-Operation-Batch-Id": "cli-move-chunks-test"}

        assert _move(owner, other, [first], headers=headers).json()["moved"] == [first]
        assert _move(owner, other, [second], headers=headers).json()["moved"] == [
            second
        ]
        assert _here(server, first).deleted is True
        assert _here(server, second).deleted is True

        assert owner.post(f"{API}/operations/undo").status_code == 200
        assert _here(server, first).deleted is False
        assert _here(server, second).deleted is False

    def test_the_picture_arrives_with_what_the_owner_gave_it(
        self, server, owner, other
    ):
        taken = datetime(2021, 3, 4, 5, 6, 7, tzinfo=timezone.utc)
        picture_id = _add(
            server,
            _png(),
            tags=["harbour", "dusk"],
            score=4,
            description="boats at dusk",
            created_at=taken,
        )
        sha = _here(server, picture_id).pixel_sha

        response = _move(owner, other, [picture_id])

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["library_name"] == "Other"
        assert body["moved_count"] == 1
        assert body["moved"] == [picture_id]
        assert body["skipped_already_there"] == []
        assert (
            body["skipped_locked"],
            body["skipped_scrapheaped"],
            body["failed"],
        ) == (
            [],
            [],
            [],
        )

        (arrived,) = _there(server, other, sha)
        assert arrived["on_disk"] is True
        assert arrived["deleted"] is False
        assert arrived["tags"] == ["dusk", "harbour"]
        assert arrived["score"] == 4
        assert arrived["description"] == "boats at dusk"
        assert arrived["created_at"].replace(tzinfo=timezone.utc) == taken
        assert arrived["imported_at"] is not None

    def test_here_it_goes_to_the_scrapheap_and_one_undo_brings_it_back(
        self, server, owner, other
    ):
        picture_id = _add(server, _png(), tags=["kept"])
        source_file = os.path.join(
            server.vault.image_root, _here(server, picture_id).file_path
        )

        assert _move(owner, other, [picture_id]).status_code == 200

        # Soft-deleted, not removed: the file is still on disk until the
        # Scrapheap's own retention says otherwise.
        assert _here(server, picture_id).deleted is True
        assert os.path.isfile(source_file)

        assert owner.post(f"{API}/operations/undo").status_code == 200
        assert _here(server, picture_id).deleted is False

    def test_a_picture_with_no_tags_is_left_for_the_other_librarys_tagger(
        self, server, owner, other
    ):
        picture_id = _add(server, _png())
        sha = _here(server, picture_id).pixel_sha

        assert _move(owner, other, [picture_id]).status_code == 200

        (arrived,) = _there(server, other, sha)
        assert arrived["tags"] == ["__tag"]

    def test_a_video_moves_like_a_picture(self, server, owner, other, tmp_path):
        path = str(tmp_path / "clip.mp4")
        writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (32, 32))
        if not writer.isOpened():
            pytest.skip("no OpenCV video encoder available in this environment")
        for shade in range(6):
            writer.write(np.full((32, 32, 3), shade * 40, dtype=np.uint8))
        writer.release()
        with open(path, "rb") as handle:
            data = handle.read()
        picture = ImageUtils.create_picture_from_bytes(
            server.vault.image_root, data, picture_uuid="clip-to-move.mp4"
        )
        assert picture.is_video is True
        video_id = server.vault.db.run_task(_insert, picture, ["clip"])
        sha = _here(server, video_id).pixel_sha

        body = _move(owner, other, [video_id]).json()

        assert (body["moved_count"], body["failed"]) == (1, [])
        (arrived,) = _there(server, other, sha)
        assert arrived["on_disk"] is True
        assert arrived["file_path"].endswith(".mp4")
        assert arrived["tags"] == ["clip"]


class TestWhatTheOtherLibraryAlreadyHolds:
    def test_a_file_it_already_has_stays_here_with_its_own_tags(
        self, server, owner, other
    ):
        """The copy there may be tagged differently; a move must not pick one."""
        data = _png()
        first = _add(server, data, tags=["first"])
        sha = _here(server, first).pixel_sha
        assert _move(owner, other, [first]).status_code == 200
        again = _add(server, data, tags=["second"])

        body = _move(owner, other, [again]).json()

        assert body["moved_count"] == 0
        assert body["skipped_already_there"] == [again]
        assert _here(server, again).deleted is False
        (only,) = _there(server, other, sha)
        assert only["tags"] == ["first"]

    def test_a_file_in_its_scrapheap_stays_put_in_both(self, server, owner, other):
        data = _png()
        first = _add(server, data)
        sha = _here(server, first).pixel_sha
        assert _move(owner, other, [first]).status_code == 200

        def scrapheap(session: Session):
            for pic in session.exec(
                select(Picture).where(Picture.pixel_sha == sha)
            ).all():
                pic.deleted = True
                session.add(pic)
            session.commit()

        with server.library_switch.open_other_library(other.uuid) as (_library, vault):
            vault.db.run_task(scrapheap)
        again = _add(server, data)

        body = _move(owner, other, [again]).json()

        assert body["moved_count"] == 0
        assert body["skipped_scrapheaped"] == [again]
        assert _here(server, again).deleted is False
        (only,) = _there(server, other, sha)
        assert only["deleted"] is True


class TestWhatIsLeftBehind:
    @pytest.mark.parametrize("fail_before_commit", [True, False])
    def test_unreadable_source_recovery_reports_copy_without_guessing_delete_outcome(
        self, server, owner, other, monkeypatch, fail_before_commit
    ):
        from pixlstash.routes.pictures import _crud, _library_move

        picture_id = _add(server, _png())
        sha = _here(server, picture_id).pixel_sha
        read = server.vault.db.run_immediate_read_task
        scrapheap = _library_move.scrapheap_pictures

        def fail_recovery_read(task, *args, **kwargs):
            if task.__name__ == "source_outcomes":
                raise RuntimeError("Source state unavailable")
            return read(task, *args, **kwargs)

        def fail_record(*args, **kwargs):
            raise RuntimeError("Source transaction failed")

        def fail_after_commit(*args):
            scrapheap(*args)
            raise RuntimeError("Post-commit notification failed")

        monkeypatch.setattr(
            server.vault.db, "run_immediate_read_task", fail_recovery_read
        )
        if fail_before_commit:
            monkeypatch.setattr(
                _crud.operation_log_service, "record_operation_in_session", fail_record
            )
        else:
            monkeypatch.setattr(_library_move, "scrapheap_pictures", fail_after_commit)

        response = _move(owner, other, [picture_id])

        assert response.status_code == 200
        body = response.json()
        assert body["copied_source_unknown"] == [picture_id]
        assert body["moved"] == []
        assert body["moved_count"] == 0
        assert body["copied_but_not_moved"] == []
        assert body["copied_but_locked"] == []
        assert body["failed"] == []
        assert _here(server, picture_id).deleted is (not fail_before_commit)
        (arrived,) = _there(server, other, sha)
        assert arrived["deleted"] is False
        assert arrived["on_disk"] is True

    @pytest.mark.parametrize("fail_before_commit", [True, False])
    def test_recovery_distinguishes_new_locks_from_other_delete_failures(
        self, server, owner, other, monkeypatch, fail_before_commit
    ):
        from pixlstash.routes.pictures import _crud, _library_move

        frozen = _add(server, _png())
        free = _add(server, _png())
        fingerprints = [_here(server, pid).pixel_sha for pid in [frozen, free]]
        copy_into = _library_move._copy_into
        scrapheap = _library_move.scrapheap_pictures

        def copy_then_lock(target, source_root, row):
            outcome = copy_into(target, source_root, row)
            if row.id == frozen:

                def lock(session: Session):
                    group = PictureSet(name="locked-during-recovery-test", locked=True)
                    session.add(group)
                    session.flush()
                    session.add(PictureSetMember(set_id=group.id, picture_id=frozen))
                    session.commit()

                server.vault.db.run_task(lock)
            return outcome

        def fail_record(*args, **kwargs):
            raise RuntimeError("Source transaction failed")

        def fail_after_commit(*args):
            scrapheap(*args)
            raise RuntimeError("Post-commit notification failed")

        monkeypatch.setattr(_library_move, "_copy_into", copy_then_lock)
        if fail_before_commit:
            monkeypatch.setattr(
                _crud.operation_log_service, "record_operation_in_session", fail_record
            )
        else:
            monkeypatch.setattr(_library_move, "scrapheap_pictures", fail_after_commit)

        response = _move(owner, other, [frozen, free])

        assert response.status_code == 200
        body = response.json()
        assert body["copied_but_locked"] == [frozen]
        assert body["copied_but_not_moved"] == ([free] if fail_before_commit else [])
        assert body["moved"] == ([] if fail_before_commit else [free])
        assert body["moved_count"] == (0 if fail_before_commit else 1)
        assert body["skipped_locked"] == []
        assert _here(server, frozen).deleted is False
        assert _here(server, free).deleted is (not fail_before_commit)
        for sha in fingerprints:
            (arrived,) = _there(server, other, sha)
            assert arrived["deleted"] is False
            assert arrived["on_disk"] is True

    def test_source_transaction_failure_reports_committed_copies(
        self, server, owner, other, monkeypatch
    ):
        from pixlstash.routes.pictures import _crud

        picture_id = _add(server, _png())
        sha = _here(server, picture_id).pixel_sha

        def fail_record(*args, **kwargs):
            # Runs after the source mutation, before its transaction commits.
            raise RuntimeError("Operation recording failed")

        monkeypatch.setattr(
            _crud.operation_log_service, "record_operation_in_session", fail_record
        )

        response = _move(owner, other, [picture_id])

        assert response.status_code == 200
        body = response.json()
        assert body["moved"] == []
        assert body["moved_count"] == 0
        assert body["copied_but_not_moved"] == [picture_id]
        assert body["copied_but_locked"] == []
        assert body["failed"] == []
        assert _here(server, picture_id).deleted is False
        (arrived,) = _there(server, other, sha)
        assert arrived["deleted"] is False
        assert arrived["on_disk"] is True

    def test_failure_after_source_commit_does_not_claim_the_source_stayed_live(
        self, server, owner, other, monkeypatch
    ):
        from pixlstash.routes.pictures import _library_move

        picture_id = _add(server, _png())
        scrapheap = _library_move.scrapheap_pictures

        def fail_after_commit(*args):
            scrapheap(*args)
            raise RuntimeError("Post-commit notification failed")

        monkeypatch.setattr(_library_move, "scrapheap_pictures", fail_after_commit)

        response = _move(owner, other, [picture_id])

        assert response.status_code == 200
        assert response.json()["moved"] == [picture_id]
        assert response.json()["moved_count"] == 1
        assert response.json()["copied_but_not_moved"] == []
        assert _here(server, picture_id).deleted is True
        assert owner.post(f"{API}/operations/undo").status_code == 200
        assert _here(server, picture_id).deleted is False

    def test_moving_a_stack_leader_keeps_uncopied_siblings_and_undo_restores_positions(
        self, server, owner, other
    ):
        leader = _add(server, _png())
        sibling = _add(server, _png())
        sibling_sha = _here(server, sibling).pixel_sha

        def stack(session: Session):
            group = PictureStack()
            session.add(group)
            session.flush()
            for position, picture_id in enumerate([leader, sibling]):
                picture = session.get(Picture, picture_id)
                picture.stack_id = group.id
                picture.stack_position = position
                session.add(picture)
            session.commit()

        server.vault.db.run_task(stack)

        body = _move(owner, other, [leader]).json()

        assert body["moved"] == [leader]
        assert _here(server, leader).deleted is True
        assert _here(server, sibling).deleted is False
        assert _here(server, sibling).stack_position == 0
        assert _there(server, other, sibling_sha) == []

        assert owner.post(f"{API}/operations/undo").status_code == 200
        assert _here(server, leader).deleted is False
        assert _here(server, leader).stack_position == 0
        assert _here(server, sibling).deleted is False
        assert _here(server, sibling).stack_position == 1

    def test_a_lock_added_after_copy_is_reported_as_present_in_both_libraries(
        self, server, owner, other, monkeypatch
    ):
        from pixlstash.routes.pictures import _library_move

        picture_id = _add(server, _png())
        sha = _here(server, picture_id).pixel_sha
        copy_into = _library_move._copy_into

        def copy_then_lock(target, source_root, row):
            outcome = copy_into(target, source_root, row)

            def lock(session: Session):
                locked_set = PictureSet(name="locked-during-copy", locked=True)
                session.add(locked_set)
                session.flush()
                session.add(PictureSetMember(set_id=locked_set.id, picture_id=row.id))
                session.commit()

            server.vault.db.run_task(lock)
            return outcome

        monkeypatch.setattr(_library_move, "_copy_into", copy_then_lock)

        body = _move(owner, other, [picture_id]).json()

        assert body["moved"] == []
        assert body["moved_count"] == 0
        assert body["skipped_locked"] == []
        assert body["copied_but_locked"] == [picture_id]
        assert _here(server, picture_id).deleted is False
        (arrived,) = _there(server, other, sha)
        assert arrived["deleted"] is False
        assert arrived["on_disk"] is True

    def test_a_picture_in_a_locked_set_is_not_copied_at_all(self, server, owner, other):
        frozen = _add(server, _png())
        free = _add(server, _png())
        frozen_sha = _here(server, frozen).pixel_sha

        def lock(session: Session):
            locked_set = PictureSet(name="frozen-by-a-lock", locked=True)
            session.add(locked_set)
            session.flush()
            session.add(PictureSetMember(set_id=locked_set.id, picture_id=frozen))
            session.commit()

        server.vault.db.run_task(lock)

        body = _move(owner, other, [frozen, free]).json()

        assert body["moved_count"] == 1
        assert body["skipped_locked"] == [frozen]
        assert _here(server, frozen).deleted is False
        assert _here(server, free).deleted is True
        assert _there(server, other, frozen_sha) == []

    def test_a_picture_whose_file_is_gone_stays_and_the_rest_still_move(
        self, server, owner, other
    ):
        lost = _add(server, _png())
        fine = _add(server, _png())
        os.remove(os.path.join(server.vault.image_root, _here(server, lost).file_path))

        body = _move(owner, other, [lost, fine]).json()

        assert body["failed"] == [lost]
        assert body["moved_count"] == 1
        assert _here(server, lost).deleted is False
        assert _here(server, fine).deleted is True

    def test_an_id_that_is_not_a_live_picture_is_not_reported_as_moved(
        self, server, owner, other
    ):
        """Already in the Scrapheap, or never existed: in no list at all."""
        gone = _add(server, _png())
        assert (
            owner.request(
                "DELETE", f"{API}/pictures", json={"picture_ids": [gone]}
            ).json()["deleted_count"]
            == 1
        )
        sha = _here(server, gone).pixel_sha
        fine = _add(server, _png())

        body = _move(owner, other, [gone, 999_999, fine]).json()

        assert body["moved"] == [fine]
        assert body["moved_count"] == 1
        assert _there(server, other, sha) == []


class TestRefusals:
    def test_the_active_library_is_not_somewhere_to_move_to(self, server, owner):
        picture_id = _add(server, _png())
        active = server.library_registry.active_library()

        response = owner.post(
            MOVE, json={"picture_ids": [picture_id], "library_uuid": active.uuid}
        )

        assert response.status_code == 409, response.text
        assert _here(server, picture_id).deleted is False

    def test_an_unknown_library_is_a_404(self, server, owner):
        picture_id = _add(server, _png())

        response = owner.post(
            MOVE,
            json={"picture_ids": [picture_id], "library_uuid": "no-such-library"},
        )

        assert response.status_code == 404, response.text
        assert _here(server, picture_id).deleted is False

    def test_a_move_is_refused_while_a_switch_holds_the_library(
        self, server, owner, other
    ):
        picture_id = _add(server, _png())
        sha = _here(server, picture_id).pixel_sha

        with server.library_switch._lock:
            response = _move(owner, other, [picture_id])

        assert response.status_code == 409, response.text
        assert _here(server, picture_id).deleted is False
        assert _there(server, other, sha) == []


class TestTheLibraryPin:
    """A token good for one library must not be a way to write into another."""

    def _token(self, server, owner, scope="ALL") -> tuple[int, dict]:
        minted = owner.post(
            "/users/me/token", json={"description": "move test", "scope": scope}
        )
        assert minted.status_code == 200, minted.text
        token_id = server.hub_engine.run_immediate_read_task(
            lambda session: session.exec(select(UserToken)).one().id
        )
        return token_id, {"Authorization": f"Bearer {minted.json()['token']}"}

    def test_a_token_for_this_library_alone_is_refused(self, server, owner, other):
        picture_id = _add(server, _png())
        sha = _here(server, picture_id).pixel_sha
        _token_id, headers = self._token(server, owner)
        client = TestClient(server.api)
        # In-scope control: the same token does reach this library's pictures.
        assert (
            client.get(f"{API}/pictures/{picture_id}/tags", headers=headers).status_code
            == 200
        )

        response = _move(client, other, [picture_id], headers=headers)

        assert response.status_code == 403, response.text
        assert "cannot move pictures" in response.json()["detail"]
        assert _here(server, picture_id).deleted is False
        assert _there(server, other, sha) == []

    def test_a_token_the_owner_let_cover_every_library_may_move(
        self, server, owner, other
    ):
        picture_id = _add(server, _png())
        token_id, headers = self._token(server, owner)
        widened = owner.put(
            f"{API}/users/me/token/{token_id}/libraries", json={"all_libraries": True}
        )
        assert widened.status_code == 200, widened.text

        response = _move(TestClient(server.api), other, [picture_id], headers=headers)

        assert response.status_code == 200, response.text
        assert response.json()["moved_count"] == 1

    def test_a_read_token_cannot_move_anything(self, server, owner, other):
        picture_id = _add(server, _png())
        _token_id, headers = self._token(server, owner, scope="READ")

        response = _move(TestClient(server.api), other, [picture_id], headers=headers)

        assert response.status_code == 403, response.text
        assert "read-only" in response.json()["detail"].lower()
        assert _here(server, picture_id).deleted is False

    def test_a_session_made_from_that_token_keeps_the_pin(self, server, owner, other):
        """Exchanging a pinned token for a cookie must not launder the pin."""
        picture_id = _add(server, _png())
        sha = _here(server, picture_id).pixel_sha
        minted = owner.post(
            "/users/me/token", json={"description": "move test", "scope": "ALL"}
        )
        session = TestClient(server.api)
        assert (
            session.post("/login", json={"token": minted.json()["token"]}).status_code
            == 200
        )
        # In-scope control: the session does reach this library's pictures.
        assert session.get(f"{API}/pictures/{picture_id}/tags").status_code == 200

        response = _move(session, other, [picture_id])

        assert response.status_code == 403, response.text
        assert "cannot move pictures" in response.json()["detail"]
        assert _here(server, picture_id).deleted is False
        assert _there(server, other, sha) == []


class TestLocality:
    """The switch's tier: a remote owner may not open another library."""

    def test_a_remote_owner_is_refused_and_told_no_host_path(
        self, server, owner, other
    ):
        picture_id = _add(server, _png())
        sha = _here(server, picture_id).pixel_sha
        # 8.8.8.8 and not a documentation range: `ipaddress` calls those
        # private, so the locality predicate would count them as local.
        remote = {"X-Forwarded-For": "8.8.8.8"}
        # Control: the same remote session is still the owner of this library.
        assert (
            owner.get(f"{API}/pictures/{picture_id}/tags", headers=remote).status_code
            == 200
        )

        response = _move(owner, other, [picture_id], headers=remote)

        assert response.status_code == 403, response.text
        assert other.path not in response.text
        assert _here(server, picture_id).deleted is False
        assert _there(server, other, sha) == []


class TestOpeningTheOtherLibrary:
    def test_it_is_opened_without_workers_whatever_the_config_says(self, server, other):
        """Closing a worker-capable vault frees models the active library uses."""
        configured = server._server_config.get("disable_background_workers")
        server._server_config["disable_background_workers"] = False
        try:
            with server.library_switch.open_other_library(other.uuid) as (_lib, vault):
                assert vault._disable_background_workers is True
        finally:
            server._server_config["disable_background_workers"] = configured

    def test_a_library_still_on_its_first_import_is_refused(
        self, server, tmp_path_factory
    ):
        folder = tmp_path_factory.mktemp("still-importing")
        pending = server.library_registry.create(
            str(folder), "Still importing", pending_import=True
        )
        try:
            with pytest.raises(LibrarySwitchError, match="first import"):
                with server.library_switch.open_other_library(pending.uuid):
                    pytest.fail("a library on its first import was opened")
        finally:
            with server.hub.transaction() as conn:
                conn.execute("DELETE FROM library WHERE id = ?", (pending.id,))
