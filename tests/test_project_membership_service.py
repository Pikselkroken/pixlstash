"""Shared-behaviour tests for ``reconcile_entity_project_change``.

Pins the single project-membership reconciliation implementation
(``pixlstash/services/project_membership_service.py``) that both the character
PATCH (``routes/characters.py::patch_character``) and the picture-set PATCH
(``routes/picture_sets.py::update_picture_set``) delegate to. The same function
is exercised for BOTH entity kinds - character-anchored (via ``Face``, excluded
with ``exclude_character_id``) and set-anchored (via ``PictureSetMember``,
excluded with ``exclude_set_id``) - across every direction:

* **added** - entity gains a project (``old=None`` -> ``new=P``);
* **changed** - entity moves between projects (``old=A`` -> ``new=B``);
* **removed** - entity leaves all projects (``old=P`` -> ``new=None``);
* **unchanged** - idempotent repair (``old=P`` -> ``new=P``) heals a missing row;
* **reference-aware retention** - a picture stays in the old project when a
  second entity assigned to that project still anchors it.

Both kinds must produce identical membership/pointer outcomes for the direction
cases, and identical retention semantics differing only in the anchor type - that
equivalence is exactly what the dedup relies on.
"""

import gc
import json
import os
import tempfile

import pytest
from fastapi import HTTPException
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
    character_project_ids,
    picture_set_project_ids,
    reconcile_entity_project_change,
    reconcile_entity_projects_change,
    set_character_projects,
    set_picture_set_projects,
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
        srv.close()
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

    Since issue #125 the assignment is written in both representations - the
    scalar primary-project FK *and* the membership join row - via
    ``set_character_projects`` / ``set_picture_set_projects``, because
    ``picture_referenced_by_project`` now reads the join. A fixture that only set
    the FK would leave the anchor invisible and silently weaken every
    reference-aware retention assertion below.

    Returns the entity id, so the caller can pass it as the excluded (moving)
    entity to the reference-aware check.
    """
    if kind == "character":
        entity = Character(name=name)
        session.add(entity)
        session.flush()
        set_character_projects(
            session, entity, [project_id] if project_id is not None else []
        )
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
        entity = PictureSet(name=name)
        session.add(entity)
        session.flush()
        set_picture_set_projects(
            session, entity, [project_id] if project_id is not None else []
        )
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


# ===========================================================================
# Issue #125 - multi-project entity membership
# ===========================================================================


def _entity_project_ids(session, kind, entity_id):
    if kind == "character":
        return character_project_ids(session, entity_id)
    return picture_set_project_ids(session, entity_id)


def _set_entity_projects(session, kind, entity, project_ids):
    if kind == "character":
        return set_character_projects(session, entity, project_ids)
    return set_picture_set_projects(session, entity, project_ids)


def _load_entity(session, kind, entity_id):
    return session.get(Character if kind == "character" else PictureSet, entity_id)


@pytest.mark.parametrize("kind", ["character", "set"])
def test_entity_joins_second_project_keeps_both(server, kind):
    """An entity in A that also joins B is a member of both, and its legacy
    primary-project FK settles on the lowest member id."""

    def scenario(session):
        p1 = Project(name=f"multi-a-{kind}")
        p2 = Project(name=f"multi-b-{kind}")
        session.add(p1)
        session.add(p2)
        session.flush()
        pic = Picture(file_path="multi.jpg")
        session.add(pic)
        session.flush()
        entity_id = _make_anchor(session, kind, p1.id, pic.id, f"multi-{kind}")
        entity = _load_entity(session, kind, entity_id)

        change = _set_entity_projects(session, kind, entity, [p1.id, p2.id])
        session.flush()
        reconcile_entity_projects_change(
            session,
            picture_ids=[pic.id],
            ensure_project_ids=change.target_project_ids,
            remove_project_ids=change.removed,
            **_exclude_kwargs(kind, entity_id),
        )
        return (
            _entity_project_ids(session, kind, entity_id),
            entity.project_id,
            _memberships(session, pic.id),
            pic.project_id,
            sorted([p1.id, p2.id]),
            change.added,
            change.removed,
        )

    (
        entity_projects,
        entity_primary,
        pic_memberships,
        pic_pointer,
        both,
        added,
        removed,
    ) = _run(server, scenario)
    assert entity_projects == both
    assert entity_primary == both[0]
    # The picture is anchored in BOTH projects, not just the primary one.
    assert pic_memberships == set(both)
    assert pic_pointer == both[0]
    assert added == [both[1]]
    assert removed == []


@pytest.mark.parametrize("kind", ["character", "set"])
def test_entity_leaves_one_of_two_projects(server, kind):
    """Dropping B from {A, B} removes only B's picture membership; A survives and
    the entity stays assigned to A."""

    def scenario(session):
        p1 = Project(name=f"drop-a-{kind}")
        p2 = Project(name=f"drop-b-{kind}")
        session.add(p1)
        session.add(p2)
        session.flush()
        pic = Picture(file_path="drop.jpg")
        session.add(pic)
        session.flush()
        entity_id = _make_anchor(session, kind, p1.id, pic.id, f"drop-{kind}")
        entity = _load_entity(session, kind, entity_id)
        change = _set_entity_projects(session, kind, entity, [p1.id, p2.id])
        session.flush()
        reconcile_entity_projects_change(
            session,
            picture_ids=[pic.id],
            ensure_project_ids=change.target_project_ids,
            remove_project_ids=change.removed,
            **_exclude_kwargs(kind, entity_id),
        )

        change = _set_entity_projects(session, kind, entity, [p1.id])
        session.flush()
        reconcile_entity_projects_change(
            session,
            picture_ids=[pic.id],
            ensure_project_ids=change.target_project_ids,
            remove_project_ids=change.removed,
            **_exclude_kwargs(kind, entity_id),
        )
        return (
            _entity_project_ids(session, kind, entity_id),
            entity.project_id,
            _memberships(session, pic.id),
            pic.project_id,
            p1.id,
            p2.id,
            change.removed,
        )

    entity_projects, entity_primary, memberships, pointer, p1_id, p2_id, removed = _run(
        server, scenario
    )
    assert entity_projects == [p1_id]
    assert entity_primary == p1_id
    assert removed == [p2_id]
    assert memberships == {p1_id}
    assert pointer == p1_id


@pytest.mark.parametrize("kind", ["character", "set"])
def test_entity_leaving_all_projects_unassigns_it(server, kind):
    """Clearing the membership set nulls the legacy FK and drops every picture
    membership the entity was the only anchor for."""

    def scenario(session):
        p1 = Project(name=f"clear-a-{kind}")
        p2 = Project(name=f"clear-b-{kind}")
        session.add(p1)
        session.add(p2)
        session.flush()
        pic = Picture(file_path="clear.jpg")
        session.add(pic)
        session.flush()
        entity_id = _make_anchor(session, kind, p1.id, pic.id, f"clear-{kind}")
        entity = _load_entity(session, kind, entity_id)
        change = _set_entity_projects(session, kind, entity, [p1.id, p2.id])
        session.flush()
        reconcile_entity_projects_change(
            session,
            picture_ids=[pic.id],
            ensure_project_ids=change.target_project_ids,
            remove_project_ids=change.removed,
            **_exclude_kwargs(kind, entity_id),
        )

        change = _set_entity_projects(session, kind, entity, [])
        session.flush()
        reconcile_entity_projects_change(
            session,
            picture_ids=[pic.id],
            ensure_project_ids=change.target_project_ids,
            remove_project_ids=change.removed,
            **_exclude_kwargs(kind, entity_id),
        )
        return (
            _entity_project_ids(session, kind, entity_id),
            entity.project_id,
            _memberships(session, pic.id),
            pic.project_id,
        )

    entity_projects, entity_primary, memberships, pointer = _run(server, scenario)
    assert entity_projects == []
    assert entity_primary is None
    assert memberships == set()
    assert pointer is None


@pytest.mark.parametrize("kind", ["character", "set"])
def test_secondary_membership_anchors_a_picture(server, kind):
    """Reference-aware retention must see an anchor's *secondary* project.

    The regression this pins: reading the primary-project FK instead of the join
    would miss an entity whose membership in the departed project is not its
    lowest one, and would evict the picture from a project it still belongs to.
    """

    def scenario(session):
        p1 = Project(name=f"anchor-a-{kind}")
        p2 = Project(name=f"anchor-b-{kind}")
        session.add(p1)
        session.add(p2)
        session.flush()
        pic = Picture(file_path="anchor.jpg")
        session.add(pic)
        session.flush()

        # Anchor entity: primary project p1, secondary p2.
        anchor_id = _make_anchor(session, kind, p1.id, pic.id, f"anchor-{kind}", 0)
        anchor = _load_entity(session, kind, anchor_id)
        _set_entity_projects(session, kind, anchor, [p1.id, p2.id])
        session.flush()

        # Moving entity: in p2 only, then leaves it. p2 must survive because the
        # anchor is still (secondarily) in p2.
        moving_id = _make_anchor(session, kind, p2.id, pic.id, f"moving-{kind}", 1)
        moving = _load_entity(session, kind, moving_id)
        session.add(PictureProjectMember(picture_id=pic.id, project_id=p2.id))
        session.flush()

        change = _set_entity_projects(session, kind, moving, [])
        session.flush()
        reconcile_entity_projects_change(
            session,
            picture_ids=[pic.id],
            ensure_project_ids=change.target_project_ids,
            remove_project_ids=change.removed,
            **_exclude_kwargs(kind, moving_id),
        )
        return _memberships(session, pic.id), p2.id

    memberships, p2_id = _run(server, scenario)
    assert p2_id in memberships


@pytest.mark.parametrize("kind", ["character", "set"])
def test_unknown_project_id_is_rejected(server, kind):
    """A membership write naming a non-existent project fails with 404 and leaves
    the entity untouched."""

    def scenario(session):
        p1 = Project(name=f"reject-{kind}")
        session.add(p1)
        session.flush()
        pic = Picture(file_path="reject.jpg")
        session.add(pic)
        session.flush()
        entity_id = _make_anchor(session, kind, p1.id, pic.id, f"reject-{kind}")
        entity = _load_entity(session, kind, entity_id)
        status = None
        try:
            _set_entity_projects(session, kind, entity, [p1.id, p1.id + 9999])
        except HTTPException as exc:
            status = exc.status_code
        return status, _entity_project_ids(session, kind, entity_id), p1.id

    status, entity_projects, p1_id = _run(server, scenario)
    assert status == 404
    assert entity_projects == [p1_id]
