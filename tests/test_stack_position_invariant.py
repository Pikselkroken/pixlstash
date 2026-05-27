"""Regression tests for the stack position-0 leader invariant.

The grid selects each stack's leader purely in SQL with
``deleted = 0 AND (stack_id IS NULL OR stack_position = 0)``. For that to be
correct, every stack must always have a *non-deleted* member at
``stack_position == 0``. These tests pin that invariant for the shared
``normalize_stack_positions`` helper and the write paths that rely on it.
"""

from sqlalchemy import event
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, Session, create_engine, select

from pixlstash.db_models import Picture, PictureStack
from pixlstash.stacking import (
    assign_picture_to_stack,
    normalize_stack_positions,
)


def _engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'stack-invariant.db'}",
        echo=False,
        poolclass=NullPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(engine)
    return engine


def _new_stack(session):
    stack = PictureStack(name="s")
    session.add(stack)
    session.commit()
    session.refresh(stack)
    return stack.id


def _add_picture(session, stack_id, position, *, deleted=False):
    pic = Picture(
        file_path=f"p{position}.jpg",
        stack_id=stack_id,
        stack_position=position,
        deleted=deleted,
    )
    session.add(pic)
    session.commit()
    session.refresh(pic)
    return pic


def _positions(session, stack_id):
    """Return ``{picture_id: (stack_position, deleted)}`` for a stack."""
    pics = session.exec(select(Picture).where(Picture.stack_id == stack_id)).all()
    return {p.id: (p.stack_position, p.deleted) for p in pics}


def test_normalize_fixes_all_null_positions(tmp_path):
    with Session(_engine(tmp_path)) as session:
        stack_id = _new_stack(session)
        for _ in range(3):
            _add_picture(session, stack_id, None)

        normalize_stack_positions(session, stack_id)
        session.commit()

        positions = sorted(p for p, _ in _positions(session, stack_id).values())
        assert positions == [0, 1, 2]


def test_normalize_fixes_nonzero_minimum_preserving_order(tmp_path):
    with Session(_engine(tmp_path)) as session:
        stack_id = _new_stack(session)
        a = _add_picture(session, stack_id, 3)
        b = _add_picture(session, stack_id, 7)
        c = _add_picture(session, stack_id, 9)

        normalize_stack_positions(session, stack_id)
        session.commit()

        pos = {pid: p for pid, (p, _) in _positions(session, stack_id).items()}
        # Lowest position is now 0, relative order (a<b<c) preserved.
        assert pos[a.id] == 0
        assert pos[b.id] == 1
        assert pos[c.id] == 2


def test_normalize_promotes_live_member_over_deleted_leader(tmp_path):
    with Session(_engine(tmp_path)) as session:
        stack_id = _new_stack(session)
        leader = _add_picture(session, stack_id, 0, deleted=True)
        live_b = _add_picture(session, stack_id, 1)
        live_c = _add_picture(session, stack_id, 2)

        normalize_stack_positions(session, stack_id)
        session.commit()

        info = _positions(session, stack_id)
        # The position-0 member must be a live (non-deleted) picture.
        leaders_at_zero = [pid for pid, (p, deleted) in info.items() if p == 0]
        assert leaders_at_zero == [live_b.id]
        assert info[live_b.id] == (0, False)
        assert info[live_c.id] == (1, False)
        # The soft-deleted former leader is pushed to the back.
        assert info[leader.id] == (2, True)


def test_assign_picture_to_stack_guarantees_leader(tmp_path):
    with Session(_engine(tmp_path)) as session:
        stack_id = _new_stack(session)
        # Pathological pre-existing state: a member with a NULL position.
        _add_picture(session, stack_id, None)
        incoming = Picture(file_path="incoming.jpg")
        session.add(incoming)
        session.commit()
        session.refresh(incoming)

        assert assign_picture_to_stack(session, incoming.id, stack_id) is True

        positions = sorted(p for p, _ in _positions(session, stack_id).values())
        assert positions == [0, 1]
