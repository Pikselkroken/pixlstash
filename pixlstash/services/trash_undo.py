"""Undo for a delete that went to the system trash: models and workflows.

Models and workflows live in the hub, not in a library, so they are outside the
operation log (backend_architecture.md §21). Their delete still sends the bytes
to the trash, and the hub rows it drops are all that stands between the trash
and a full undo. So a delete that went to the trash snapshots those rows and
remembers them here under a token, the delete answers with the token, and the
undo route spends it: the files come back out of the trash
(:mod:`pixlstash.utils.trash_restore`) and the rows go back in with the ids they
had, so the client's Redo is simply the same delete again.

A token lives in this process only and is spent by its undo. That fits the
receipt it backs, which offers Undo for a few seconds; it does not survive a
restart, and a permanent delete never gets one.
"""

from __future__ import annotations

import secrets
import threading
from collections import OrderedDict
from typing import Any

# ponytail: in-memory and capped; a hub table if undo ever has to outlive a
# restart or a long history.
MAX_TOKENS = 64

KIND_MODELS = "models"
KIND_WORKFLOW = "workflow"

_entries: OrderedDict[str, tuple[str, Any]] = OrderedDict()
_lock = threading.Lock()


def remember(kind: str, payload: Any) -> str:
    """Keep *payload* for one undo of *kind* and return its token."""
    token = secrets.token_urlsafe(16)
    with _lock:
        _entries[token] = (kind, payload)
        while len(_entries) > MAX_TOKENS:
            _entries.popitem(last=False)
    return token


def take(kind: str, token: str) -> Any | None:
    """Spend *token*: its payload, or None when it is unknown, spent or another kind."""
    with _lock:
        entry = _entries.get(token)
        if entry is None or entry[0] != kind:
            return None
        del _entries[token]
    return entry[1]


def snapshot_rows(conn, table: str, column: str, value: Any) -> list[dict]:
    """Every row of *table* whose *column* is *value*, as plain dicts.

    *table* and *column* are this codebase's constants, never request input.
    """
    return [
        dict(row)
        for row in conn.execute(
            f"SELECT * FROM {table} WHERE {column} = ?", (value,)
        ).fetchall()
    ]


def insert_rows(conn, table: str, rows: list[dict]) -> None:
    """Put snapshotted *rows* back into *table*, column for column."""
    for row in rows:
        columns = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        conn.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({marks})", tuple(row.values())
        )
