"""Shared-behaviour tests for ``reconcile_entity_project_change``.

Pins the single project-membership reconciliation implementation
(``pixlstash/services/project_membership_service.py``) that both the character
PATCH (``routes/characters.py::patch_character``) and the picture-set PATCH
(``routes/picture_sets.py::update_picture_set``) delegate to. The same function
is exercised for BOTH entity kinds — character-anchored (via ``Face``, excluded
with ``exclude_character_id``) and set-anchored (via ``PictureSetMember``,
excluded with ``exclude_set_id``) — across every direction:

* **added** — entity gains a project (``old=None`` -> ``new=P``);
* **changed** — entity moves between projects (``old=A`` -> ``new=B``);
* **removed** — entity leaves all projects (``old=P`` -> ``new=None``);
* **unchanged** — idempotent repair (``old=P`` -> ``new=P``) heals a missing row;
* **reference-aware retention** — a picture stays in the old project when a
  second entity assigned to that project still anchors it.

Both kinds must produce identical membership/pointer outcomes for the direction
cases, and identical retention semantics differing only in the anchor type — that
equivalence is exactly what the dedup relies on.
"""

import gc
import json
import os
import tempfile

import pytest
from sqlmodel import select

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    Character,
    Face,
    Picture,
    PictureProjectMember,
    PictureSet,
    PictureSetMember,
    Project,
)
from pixlstash.server import Server
from pixlstash.services.project_membership_service import (
    reconcile_entity_project_change,
)


@pytest.fixture
def server():
    temp_dir = tempfile.TemporaryDirectory()
    config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(config_path, "w") as fh:
        fh.write(json.dumps({"port": 8000}))
    srv = Server(config_path)
    try:
        yield srv
    finally:
        srv.vault.close()
        temp_dir.cleanup()
        gc.collect()


def _run(server, fn, *args):
    """Run *fn(session, \\*args)* on the DB worker and return its result."""
    return server.vault.db.run_task(fn, *args, priority=DBPriority.IMMEDIATE)


def _memberships(session, pic_id):
    return set(
        session.exec(
            select(PictureProjectMember.project_id).where(
                PictureProjectMember.picture_id == pic_id
            )
        ).all()
    )


def _make_anchor(session, kind, project_id, pic_id, name, face_index=0):
    """Create an entity of *kind* assigned to *project_id* anchoring *pic_id*.

    Returns the entity id, so the caller can pass it as the excluded (moving)
    entity to the reference-aware check.
    """
    if kind == "character":
        entity = Character(name=name, project_id=project_id)
        session.add(entity)
        session.flush()
        session.add(
            Face(
                picture_id=pic_id,
                frame_index=0,
                face_index=face_index,
                character_id=entity.id,
                bbox_="0,0,10,10",
            )
        )
    else:
        entity = PictureSet(name=name, project_id=project_id)
        session.add(entity)
        session.flush()
        session.add(PictureSetMember(set_id=entity.id, picture_id=pic_id))
    session.flush()
    return entity.id


def _exclude_kwargs(kind, entity_id):
    if kind == "character":
        return {"exclude_character_id": entity_id}
    return {"exclude_set_id": entity_id}


@pytest.mark.parametrize("kind", ["character", "set"])
def test_reconcile_adds_membership_when_project_assigned(server, kind):
    """old=None -> new=P: picture is added to the new project and repointed."""

    def scenario(session):
        p1 = Project(name=f"add-p1-{kind}")
        session.add(p1)
        session.flush()
        pic = Picture(file_path="add.jpg")
        session.add(pic)
        session.flush()
        entity_id = _make_anchor(session, kind, p1.id, pic.id, f"add-{kind}")

        result = reconcile_entity_project_change(
            session,
            picture_ids=[pic.id],
            old_project_id=None,
            new_project_id=p1.id,
            **_exclude_kwargs(kind, entity_id),
        )
        return _memberships(session, pic.id), pic.project_id, p1.id, result.changed

    memberships, pointer, p1_id, changed = _run(server, scenario)
    assert memberships == {p1_id}
    assert pointer == p1_id
    assert changed is True


@pytest.mark.parametrize("kind", ["character", "set"])
def test_reconcile_changes_project(server, kind):
    """old=A -> new=B: picture leaves A (unanchored) and joins B."""

    def scenario(session):
        p1 = Project(name=f"chg-p1-{kind}")
        p2 = Project(name=f"chg-p2-{kind}")
        session.add(p1)
        session.add(p2)
        session.flush()
        pic = Picture(file_path="chg.jpg", project_id=p1.id)
        session.add(pic)
        session.flush()
        session.add(PictureProjectMember(picture_id=pic.id, project_id=p1.id))
        entity_id = _make_anchor(session, kind, p2.id, pic.id, f"chg-{kind}")
        session.flush()

        reconcile_entity_project_change(
            session,
            picture_ids=[pic.id],
            old_project_id=p1.id,
            new_project_id=p2.id,
            **_exclude_kwargs(kind, entity_id),
        )
        return _memberships(session, pic.id), pic.project_id, p1.id, p2.id

    memberships, pointer, p1_id, p2_id = _run(server, scenario)
    assert memberships == {p2_id}
    assert pointer == p2_id


@pytest.mark.parametrize("kind", ["character", "set"])
def test_reconcile_removes_membership_when_project_cleared(server, kind):
    """old=P -> new=None: membership is dropped and the pointer falls back."""

    def scenario(session):
        p1 = Project(name=f"rm-p1-{kind}")
        session.add(p1)
        session.flush()
        pic = Picture(file_path="rm.jpg", project_id=p1.id)
        session.add(pic)
        session.flush()
        session.add(PictureProjectMember(picture_id=pic.id, project_id=p1.id))
        entity_id = _make_anchor(session, kind, None, pic.id, f"rm-{kind}")
        session.flush()

        reconcile_entity_project_change(
            session,
            picture_ids=[pic.id],
            old_project_id=p1.id,
            new_project_id=None,
            **_exclude_kwargs(kind, entity_id),
        )
        return _memberships(session, pic.id), pic.project_id

    memberships, pointer = _run(server, scenario)
    assert memberships == set()
    assert pointer is None


@pytest.mark.parametrize("kind", ["character", "set"])
def test_reconcile_unchanged_is_idempotent_repair(server, kind):
    """old=P -> new=P with a missing membership row: it is healed, none removed."""

    def scenario(session):
        p1 = Project(name=f"rep-p1-{kind}")
        session.add(p1)
        session.flush()
        # Drifted state: entity is "assigned" to p1 but the membership row and the
        # scalar pointer were never written.
        pic = Picture(file_path="rep.jpg")
        session.add(pic)
        session.flush()
        entity_id = _make_anchor(session, kind, p1.id, pic.id, f"rep-{kind}")

        result = reconcile_entity_project_change(
            session,
            picture_ids=[pic.id],
            old_project_id=p1.id,
            new_project_id=p1.id,
            **_exclude_kwargs(kind, entity_id),
        )
        return _memberships(session, pic.id), pic.project_id, p1.id, result

    memberships, pointer, p1_id, result = _run(server, scenario)
    assert memberships == {p1_id}
    assert pointer == p1_id
    assert result.memberships_added == 1
    assert result.memberships_removed == 0
    assert result.changed is True


@pytest.mark.parametrize("kind", ["character", "set"])
def test_reconcile_reference_aware_retention(server, kind):
    """old=A -> new=B, but a second entity in A anchors the picture: A is kept."""

    def scenario(session):
        p1 = Project(name=f"ref-p1-{kind}")
        p2 = Project(name=f"ref-p2-{kind}")
        session.add(p1)
        session.add(p2)
        session.flush()
        pic = Picture(file_path="ref.jpg", project_id=p1.id)
        session.add(pic)
        session.flush()
        session.add(PictureProjectMember(picture_id=pic.id, project_id=p1.id))
        # The moving entity (assigned to p1) and a second entity that also anchors
        # the picture in p1 and must keep it there.
        moving_id = _make_anchor(session, kind, p1.id, pic.id, f"ref-move-{kind}", 0)
        _make_anchor(session, kind, p1.id, pic.id, f"ref-anchor-{kind}", 1)
        session.flush()

        reconcile_entity_project_change(
            session,
            picture_ids=[pic.id],
            old_project_id=p1.id,
            new_project_id=p2.id,
            **_exclude_kwargs(kind, moving_id),
        )
        return _memberships(session, pic.id), pic.project_id, p1.id, p2.id

    memberships, pointer, p1_id, p2_id = _run(server, scenario)
    assert memberships == {p1_id, p2_id}
    assert pointer == p2_id
