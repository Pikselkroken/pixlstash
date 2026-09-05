"""Unit tests for ``pixlstash.utils.service.scope_table.scope_id_subquery``.

This is where the SQLite bound-parameter ceiling lived: filtering a column by a
Python set with ``col.in_(ids)`` binds one parameter per id, so a scope of tens
of thousands of pictures raises ``OperationalError: too many SQL variables``.
The helper materialises the ids into a per-connection TEMP TABLE and filters via
``IN (SELECT ...)`` instead, binding zero id parameters.

The proof is build-independent: we lower this connection's
``SQLITE_LIMIT_VARIABLE_NUMBER`` to the historical 999 floor with
``setlimit``, show a plain ``.in_(large_set)`` raises there, then show the
helper path succeeds and returns the exact membership - at a scope size (1500)
that is more than the 999 ceiling but far below any modern default.
"""

import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlmodel import Field, Session, SQLModel, select

from pixlstash.utils.service.scope_table import scope_id_subquery

# Larger than the old 999-variable ceiling; small enough to stay well under any
# modern SQLite default, so the failure below is provoked purely by setlimit.
SCOPE_SIZE = 1500


class _Item(SQLModel, table=True):
    __tablename__ = "_scope_test_item"
    id: int = Field(primary_key=True)
    pid: int = Field(index=True)


def _seed(session: Session, n: int) -> None:
    for i in range(n):
        session.add(_Item(id=i, pid=i))
    session.commit()


def test_scope_id_subquery_beats_the_variable_ceiling():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            # Seed under the default (high) limit - an ORM bulk insert may batch
            # many parameters, which is fine before we lower the ceiling.
            _seed(session, SCOPE_SIZE)

            # Pin this connection's variable limit to the historical 999 floor.
            dbapi_conn = session.connection().connection
            dbapi_conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)

            big_scope = set(range(SCOPE_SIZE))

            # 1) The ceiling is real: a plain set ``.in_()`` overflows it.
            with pytest.raises(OperationalError, match="too many SQL variables"):
                session.exec(select(_Item.pid).where(_Item.pid.in_(big_scope))).all()

            # 2) The helper path runs and returns the full membership.
            sub = scope_id_subquery(session, big_scope)
            got = set(session.exec(select(_Item.pid).where(_Item.pid.in_(sub))).all())
            assert got == big_scope

            # 3) Membership is exact, not just a count: a proper subset scope
            #    returns exactly that subset.
            subset = {i for i in range(SCOPE_SIZE) if i % 2 == 0}
            sub2 = scope_id_subquery(session, subset)
            got2 = set(session.exec(select(_Item.pid).where(_Item.pid.in_(sub2))).all())
            assert got2 == subset

            # 4) Double-bind (the likeness-pairs site binds the scope at BOTH
            #    endpoints - it would hit the ceiling at half the scope size).
            #    One materialised subquery, referenced twice, still runs.
            sub3 = scope_id_subquery(session, big_scope)
            n = len(
                session.exec(
                    select(_Item.pid).where(_Item.pid.in_(sub3), _Item.id.in_(sub3))
                ).all()
            )
            assert n == SCOPE_SIZE

            # 5) An empty scope is a valid empty membership test (matches
            #    nothing), never an error - mirrors ``.in_(set())``.
            sub4 = scope_id_subquery(session, set())
            assert (
                session.exec(select(_Item.pid).where(_Item.pid.in_(sub4))).all() == []
            )
    finally:
        SQLModel.metadata.remove(_Item.__table__)
        engine.dispose()
