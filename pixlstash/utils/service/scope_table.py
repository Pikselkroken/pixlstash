"""Materialise a large id scope into a per-connection SQLite ``TEMP TABLE``.

Filtering a column by a Python set with ``col.in_(ids)`` binds one SQL
parameter per id, so a scope that resolves to tens of thousands of pictures
blows past SQLite's ``SQLITE_MAX_VARIABLE_NUMBER`` ceiling (999 on builds
before 3.32, 32766 or higher after) and the query raises
``sqlite3.OperationalError: too many SQL variables``. This helper writes the
ids into a connection-scoped ``TEMP TABLE`` with a parameter-safe
``executemany`` insert and hands back a scalar subquery selecting that table's
id column, so a caller swaps

    col.in_(ids)                       # one bound param per id - has a ceiling

for

    col.in_(scope_id_subquery(session, ids))   # zero id params - no ceiling

The two forms are result-identical (a set-membership test), but the temp-table
form binds no per-id parameters, so an arbitrarily large scope is safe.

SQLite scopes ``TEMP`` tables to the DBAPI connection, and every query in a
single ``run_immediate_read_task`` / write-task callback runs on that one
connection, so a table materialised at the top of a callback is visible to
every query in it. Because the pool may hand the same connection to a later
callback, the table is dropped and recreated on each call - stale ids never
leak between calls. When two independent scopes must coexist in one callback
(e.g. the raw scope and its non-deleted subset), give them distinct ``name``s
so neither clobbers the other.
"""

from collections.abc import Iterable

from sqlalchemy import Integer, column, table
from sqlalchemy import select as sa_select
from sqlmodel import Session

# Default temp-table name. Prefixed to avoid colliding with any real table.
DEFAULT_SCOPE_TABLE = "_pixlstash_scope_ids"


def scope_id_subquery(
    session: Session, ids: Iterable[int], *, name: str = DEFAULT_SCOPE_TABLE
):
    """Materialise *ids* into a ``TEMP TABLE`` and return ``SELECT id FROM temp``.

    The returned selectable is meant to replace a Python-set ``.in_()`` -
    ``some_column.in_(scope_id_subquery(session, ids))`` - with an identical
    membership test that binds no per-id parameters, so a scope of any size is
    safe against SQLite's bound-parameter ceiling.

    Args:
        session: Active session; the table is created on its bound connection.
        ids: The scope ids. May be empty, yielding a zero-row table (an empty
            membership test - matches nothing, same as ``.in_(set())``).
        name: Temp-table name. Pass distinct names when two scopes must live on
            the same connection at once so they do not overwrite each other.

    Returns:
        A SQLAlchemy ``SELECT`` over the temp table's id column, usable directly
        as the argument to ``Column.in_(...)``. Reusable across multiple
        statements (and multiple ``.in_()`` clauses) as long as the table is not
        re-materialised under the same name in between.
    """
    conn = session.connection()
    # Drop + recreate so a connection reused from the pool never carries stale
    # ids from an earlier call. A rollback on pool return does not drop temp
    # tables, so this is the reset.
    conn.exec_driver_sql(f"DROP TABLE IF EXISTS temp.{name}")
    conn.exec_driver_sql(f"CREATE TEMP TABLE {name} (id INTEGER PRIMARY KEY)")
    # De-dupe (the PRIMARY KEY would reject repeats) and insert one row per
    # execution via executemany - each execution binds a single parameter, so
    # the insert itself never approaches the ceiling this helper exists to dodge.
    rows = [(int(i),) for i in set(ids)]
    if rows:
        conn.exec_driver_sql(f"INSERT INTO {name} (id) VALUES (?)", rows)
    scope = table(name, column("id", Integer))
    return sa_select(scope.c.id)
