"""Tests for ``Picture.metadata_hash`` regeneration via the after-flush hook.

The hash powers the snapshot "identical-state detection" feature and the
per-picture hash-compare preview, so the hook MUST refire and the digest
MUST change for every kind of mutation the UI considers a change.

In particular: ``_before_flush_hash_tracker`` dirty-marks a picture on
Face changes (so a face edit costs a recompute), but the hash digest
itself was computed only from picture columns + tags. This file proves
both halves of the contract are in sync: a face mutation must change the
hash, not just trigger a hash recompute to the same value.
"""

import json
import tempfile

import pytest
from sqlalchemy import text
from sqlmodel import delete

from pixlstash.db_models import Character, Face, Picture
from pixlstash.db_models.tag import Tag
from pixlstash.db_models.snapshot import Snapshot
from pixlstash.server import Server


@pytest.fixture(scope="module")
def server():
    with tempfile.TemporaryDirectory() as tmp:
        config_path = f"{tmp}/server-config.json"
        with open(config_path, "w") as fh:
            json.dump({"disable_background_workers": True}, fh)
        with Server(config_path) as srv:
            yield srv


@pytest.fixture(autouse=True)
def clean_db(server):
    def _wipe(session):
        session.exec(text("PRAGMA foreign_keys = OFF"))
        session.exec(delete(Snapshot))
        session.exec(delete(Tag))
        session.exec(delete(Face))
        session.exec(delete(Character))
        session.exec(delete(Picture))
        session.exec(text("PRAGMA foreign_keys = ON"))
        session.commit()

    server.vault.db.run_task(_wipe)
    yield


def _add_picture(server, filename: str = "test.jpg") -> Picture:
    def _do(session):
        pic = Picture(file_path=filename, filename=filename)
        session.add(pic)
        session.commit()
        session.refresh(pic)
        return pic

    return server.vault.db.run_task(_do)


def _hash_of(server, pic_id: int) -> str | None:
    return server.vault.db.run_immediate_read_task(
        lambda s: s.get(Picture, pic_id).metadata_hash
    )


# ---------------------------------------------------------------------------
# Picture-column mutation: SHOULD change the hash (sanity)
# ---------------------------------------------------------------------------


def test_picture_description_change_changes_hash(server):
    pic = _add_picture(server)
    initial = _hash_of(server, pic.id)
    assert initial is not None, "Hash must be populated by the after-flush hook"

    def _mutate(session):
        p = session.get(Picture, pic.id)
        p.description = "new description"
        session.commit()

    server.vault.db.run_task(_mutate)

    after = _hash_of(server, pic.id)
    assert after is not None
    assert after != initial, (
        "Picture-column mutation must produce a new metadata_hash; "
        f"got identical hashes before={initial!r} after={after!r}"
    )


# ---------------------------------------------------------------------------
# Tag mutation: SHOULD change the hash (sanity)
# ---------------------------------------------------------------------------


def test_tag_addition_changes_hash(server):
    pic = _add_picture(server)
    initial = _hash_of(server, pic.id)
    assert initial is not None

    def _add_tag(session):
        session.add(Tag(picture_id=pic.id, tag="hello"))
        session.commit()

    server.vault.db.run_task(_add_tag)

    after = _hash_of(server, pic.id)
    assert after != initial, (
        f"Tag add must change metadata_hash; got identical "
        f"before={initial!r} after={after!r}"
    )


# ---------------------------------------------------------------------------
# Face mutation: SHOULD change the hash — this is the docs-2.md claim
# ---------------------------------------------------------------------------


def test_face_addition_changes_hash(server):
    """Adding a Face dirty-marks the picture (via
    ``_before_flush_hash_tracker``) — therefore the hash MUST change, not
    just recompute to the same value. If the digest doesn't include any
    face-derived state, the UI's identical-state detection silently lies
    for face-only edits."""
    pic = _add_picture(server)
    initial = _hash_of(server, pic.id)
    assert initial is not None

    def _add_face(session):
        session.add(
            Face(
                picture_id=pic.id,
                frame_index=0,
                face_index=0,
                bbox_="0,0,10,10",
            )
        )
        session.commit()

    server.vault.db.run_task(_add_face)

    after = _hash_of(server, pic.id)
    assert after != initial, (
        "Adding a Face to a picture must change its metadata_hash; "
        f"got identical hashes before={initial!r} after={after!r}. "
        "The before-flush tracker dirties on Face changes but the digest "
        "ignores faces — UI 'identical to snapshot' detection lies for "
        "face-only edits."
    )


def test_face_character_assignment_changes_hash(server):
    """Re-assigning a face to a different character is a user-visible
    metadata change; the hash must reflect it."""
    pic = _add_picture(server)

    def _setup(session):
        c1 = Character(name="alice")
        c2 = Character(name="bob")
        session.add(c1)
        session.add(c2)
        session.commit()
        session.refresh(c1)
        session.refresh(c2)
        face = Face(
            picture_id=pic.id,
            frame_index=0,
            face_index=0,
            character_id=c1.id,
            bbox_="0,0,10,10",
        )
        session.add(face)
        session.commit()
        session.refresh(face)
        return face.id, c2.id

    face_id, bob_id = server.vault.db.run_task(_setup)
    before = _hash_of(server, pic.id)
    assert before is not None

    def _reassign(session):
        face = session.get(Face, face_id)
        face.character_id = bob_id
        session.commit()

    server.vault.db.run_task(_reassign)

    after = _hash_of(server, pic.id)
    assert after != before, (
        "Re-assigning a face's character_id must change the picture's "
        f"metadata_hash; got identical before={before!r} after={after!r}."
    )
