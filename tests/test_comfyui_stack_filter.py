"""Unit tests for the ComfyUI membership filter in :meth:`Picture.find`.

ComfyUI membership is the one leaf ``find()`` does not delegate to
:class:`PredicateFilter`: on a stack-collapsed grid a stack leader must be shown
when *any* member of its stack was made with the filtered model or LoRA, not only
when the leader row itself was.  That expansion is expressed as a raw ``text()``
fragment of the shape ``(self match) OR (stack member match)``.

``text()`` is opaque to SQLAlchemy, so nothing parenthesises that disjunction for
us.  SQL ``AND`` binds tighter than ``OR``, so an unwrapped fragment ANDed into
the rest of the WHERE clause renders as::

    WHERE deleted = 0 AND <leader condition> AND self_match OR member_match

which parses as ``(deleted = 0 AND leader AND self) OR member``.  The
stack-member branch escapes the deleted filter, the stack-leader condition and
any scope narrowing, returning every member of a matching stack instead of one
tile for it.  These tests pin both directions: the leak is closed, and the
legitimate stack expansion that the fragment exists for still works.

The same precedence trap, and the same outer-parenthesis fix, is documented on
``tags_confidence_above_filter`` in ``pixlstash/utils/query/predicate_filter.py``.
"""

from __future__ import annotations

import itertools
import json

import pytest
from sqlalchemy import event
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, create_engine

# Importing the models registers them on SQLModel.metadata.
from pixlstash.db_models import Picture, PictureProjectMember, PictureStack, Project

_file_names = itertools.count()


@pytest.fixture
def session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'comfyui_stack.db'}",
        echo=False,
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _new_stack(session) -> int:
    stack = PictureStack()
    session.add(stack)
    session.commit()
    session.refresh(stack)
    return stack.id


def _add_picture(session, *, models=None, loras=None, **kwargs) -> Picture:
    kwargs.setdefault("file_path", f"pic_{next(_file_names)}.jpg")
    if models is not None:
        kwargs["comfyui_models"] = json.dumps(models)
    if loras is not None:
        kwargs["comfyui_loras"] = json.dumps(loras)
    pic = Picture(**kwargs)
    session.add(pic)
    session.commit()
    session.refresh(pic)
    return pic


def _found_ids(session, **kwargs) -> set[int]:
    return {pic.id for pic in Picture.find(session, **kwargs)}


def _found_count(session, **kwargs) -> int:
    return Picture.find(session, count_only=True, **kwargs)


def _assert_found(session, expected: set[int], **kwargs):
    """The id set and the COUNT(*) that the grid runs must agree."""
    assert _found_ids(session, **kwargs) == expected
    assert _found_count(session, **kwargs) == len(expected)


# --- the leak: the member branch must not escape the other predicates ---------


def test_stack_expansion_does_not_leak_non_leader_members(session):
    """One tile per matching stack, not one per member.

    The leader condition is ANDed alongside the ComfyUI fragment.  Without the
    outer parentheses the member branch is OR'd against the whole conjunction,
    so every sibling of a matching picture comes back as its own row.
    """
    stack = _new_stack(session)
    cover = _add_picture(session, stack_id=stack, stack_position=0, models=["m1"])
    second = _add_picture(session, stack_id=stack, stack_position=1, models=["other"])
    third = _add_picture(session, stack_id=stack, stack_position=2)

    _assert_found(
        session,
        {cover.id},
        stack_leaders_only=True,
        comfyui_models_filter=["m1"],
    )
    found = _found_ids(session, stack_leaders_only=True, comfyui_models_filter=["m1"])
    assert second.id not in found
    assert third.id not in found


def test_stack_expansion_does_not_leak_deleted_pictures(session):
    """A soft-deleted sibling of a match is still deleted.

    ``deleted = 0`` is emitted by the PredicateFilter and ANDed in front of the
    ComfyUI fragment, so it is one of the predicates an unwrapped ``OR`` escapes.
    """
    stack = _new_stack(session)
    cover = _add_picture(session, stack_id=stack, stack_position=0, models=["m1"])
    dead = _add_picture(
        session, stack_id=stack, stack_position=1, models=["m1"], deleted=True
    )

    _assert_found(
        session,
        {cover.id},
        stack_leaders_only=True,
        comfyui_models_filter=["m1"],
    )
    assert dead.id not in _found_ids(
        session, stack_leaders_only=True, comfyui_models_filter=["m1"]
    )


def test_stack_expansion_does_not_leak_outside_an_id_scope(session):
    """A picture set / share-token scope is an ``id IN (...)`` narrowing.

    It is applied as its own ANDed predicate, so an unwrapped ``OR`` hands the
    caller rows it never asked for: a share token would return pictures outside
    its scope purely because a stack sibling matched the model filter.
    """
    stack = _new_stack(session)
    cover = _add_picture(session, stack_id=stack, stack_position=0, models=["m1"])
    second = _add_picture(session, stack_id=stack, stack_position=1, models=["m1"])
    third = _add_picture(session, stack_id=stack, stack_position=2, models=["m1"])

    scope = [cover.id, second.id]
    _assert_found(
        session,
        {cover.id},
        stack_leaders_only=True,
        comfyui_models_filter=["m1"],
        id=scope,
    )
    assert third.id not in _found_ids(
        session,
        stack_leaders_only=True,
        comfyui_models_filter=["m1"],
        id=scope,
    )


def test_stack_expansion_does_not_leak_outside_a_project_scope(session):
    """Project membership is an ANDed ``EXISTS``, and must survive the ``OR``."""
    project = Project(name="scoped")
    other_project = Project(name="elsewhere")
    session.add(project)
    session.add(other_project)
    session.commit()
    session.refresh(project)
    session.refresh(other_project)

    stack = _new_stack(session)
    cover = _add_picture(session, stack_id=stack, stack_position=0, models=["m1"])
    outsider = _add_picture(session, stack_id=stack, stack_position=1, models=["m1"])
    session.add(PictureProjectMember(picture_id=cover.id, project_id=project.id))
    session.add(
        PictureProjectMember(picture_id=outsider.id, project_id=other_project.id)
    )
    session.commit()

    _assert_found(
        session,
        {cover.id},
        stack_leaders_only=True,
        comfyui_models_filter=["m1"],
        project_id=project.id,
    )
    assert outsider.id not in _found_ids(
        session,
        stack_leaders_only=True,
        comfyui_models_filter=["m1"],
        project_id=project.id,
    )


def test_lora_filter_does_not_leak_either(session):
    """The LoRA fragments share the code path, so they share the trap."""
    stack = _new_stack(session)
    cover = _add_picture(session, stack_id=stack, stack_position=0, loras=["lora_x"])
    second = _add_picture(session, stack_id=stack, stack_position=1)

    _assert_found(
        session,
        {cover.id},
        stack_leaders_only=True,
        comfyui_loras_filter=["lora_x"],
    )
    assert second.id not in _found_ids(
        session, stack_leaders_only=True, comfyui_loras_filter=["lora_x"]
    )


# --- the other direction: the filter must still return what it should --------


def test_stack_is_shown_when_only_a_non_cover_member_matches(session):
    """The behaviour the raw fragment exists for.

    The cover was not made with the filtered model but a member was, so the
    stack still earns exactly one tile: its leader.  Over-blocking here is its
    own regression, and was the bug the stack-member branch was added to fix.
    """
    stack = _new_stack(session)
    cover = _add_picture(session, stack_id=stack, stack_position=0, models=["other"])
    member = _add_picture(session, stack_id=stack, stack_position=1, models=["m1"])

    found = _found_ids(session, stack_leaders_only=True, comfyui_models_filter=["m1"])
    assert found == {cover.id}
    assert member.id not in found
    assert (
        _found_count(session, stack_leaders_only=True, comfyui_models_filter=["m1"])
        == 1
    )


def test_unstacked_matches_are_returned_and_non_matches_are_not(session):
    matching = _add_picture(session, models=["m1"])
    _add_picture(session, models=["other"])
    _add_picture(session)

    _assert_found(
        session,
        {matching.id},
        stack_leaders_only=True,
        comfyui_models_filter=["m1"],
    )


def test_stack_with_no_matching_member_contributes_nothing(session):
    stack = _new_stack(session)
    _add_picture(session, stack_id=stack, stack_position=0, models=["other"])
    _add_picture(session, stack_id=stack, stack_position=1, models=["another"])
    loose = _add_picture(session, models=["m1"])

    _assert_found(
        session,
        {loose.id},
        stack_leaders_only=True,
        comfyui_models_filter=["m1"],
    )


def test_expanded_stacks_and_loose_matches_combine(session):
    """A whole-library shape: expansion, non-matching stacks and loose rows."""
    matching_stack = _new_stack(session)
    matching_cover = _add_picture(
        session, stack_id=matching_stack, stack_position=0, models=["other"]
    )
    _add_picture(session, stack_id=matching_stack, stack_position=1, models=["m1"])
    _add_picture(session, stack_id=matching_stack, stack_position=2)

    quiet_stack = _new_stack(session)
    _add_picture(session, stack_id=quiet_stack, stack_position=0, models=["other"])
    _add_picture(session, stack_id=quiet_stack, stack_position=1)

    loose_match = _add_picture(session, models=["m1", "extra"])
    _add_picture(session, models=["extra"])

    _assert_found(
        session,
        {matching_cover.id, loose_match.id},
        stack_leaders_only=True,
        comfyui_models_filter=["m1"],
    )


def test_without_stack_collapse_only_the_matching_rows_come_back(session):
    """``stack_leaders_only=False`` takes the self-only branch: no expansion."""
    stack = _new_stack(session)
    _add_picture(session, stack_id=stack, stack_position=0, models=["other"])
    member = _add_picture(session, stack_id=stack, stack_position=1, models=["m1"])
    loose = _add_picture(session, models=["m1"])
    _add_picture(session, models=["other"])

    _assert_found(
        session,
        {member.id, loose.id},
        comfyui_models_filter=["m1"],
    )
