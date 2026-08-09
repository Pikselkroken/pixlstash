"""Reads for the model shelf: one hub query, one locations query, one vault query.

The shelf's rows straddle two SQLite files and this module is where that seam is
handled once. ``model`` / ``model_file`` / ``model_folder`` are **hub** tables
(what is on this disk is a fact about this machine); ``adapter_attachment`` is a
**vault** table (which character uses a LoRA is a fact about this library). No
foreign key and no SQL join can cross the two, so a filter that mixes them is two
queries intersected in Python — and, importantly, *two* queries no matter how
many rows come back.

Everything here is shaped so the sorting work is a change to one SELECT rather
than the unpicking of an N+1: the list is one hub query, the locations for the
whole page are one more, and the attachments for the whole page are one vault
query. Nothing is fetched per row.

**Sorting (B7) kept that promise.** A stack's size is the sum of its members and
its date is the newest member's, and a row must never sort by a number it does
not display — so those aggregates are two ``LEFT JOIN``s onto grouped subqueries
inside the *same* ``SELECT`` as the rows, not a lookup per row. 1,806 rows sorted
by an aggregate is the N+1 this shape exists to prevent.

Both list blocks — adapters and checkpoints — go through :func:`fetch_models`.
There is one content table, so there is one query; ``file_kind`` is the only
thing that differs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, delete, select

from pixlstash.database import DBPriority
from pixlstash.db_models.adapter_attachment import (
    ENTITY_CHARACTER,
    ENTITY_SET,
    AdapterAttachment,
)
from pixlstash.db_models.character import Character
from pixlstash.db_models.picture_set import PictureSet
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# Which vault table each ``entity_type`` names. The attachment table addresses
# characters and sets through a discriminator rather than two nullable scalar
# columns, so this mapping is the one place the discriminator is resolved.
_ENTITY_MODELS = {ENTITY_CHARACTER: Character, ENTITY_SET: PictureSet}

ENTITY_TYPES = tuple(_ENTITY_MODELS)


class UnknownAttachmentEntityError(LookupError):
    """An attachment named a character or set that does not exist in this library.

    Its own type rather than an ``HTTPException`` so the service stays free of
    transport concerns; the route maps it to a 404.
    """

    def __init__(self, entity_type: str, entity_id: int) -> None:
        super().__init__(f"No {entity_type} with id {entity_id} in this library.")
        self.entity_type = entity_type
        self.entity_id = entity_id


# "Has no base model recorded", as a filter value. The same spelling the project
# filter already uses for "has none", so the frontend has one idiom rather than
# two. A real base model literally named UNASSIGNED would be unreachable through
# this filter; free text makes that theoretically possible and practically not.
UNSET = "UNASSIGNED"

# SQLite LIKE wildcards, escaped before a caller-supplied search term reaches
# one, or a search for ``sd_xl`` would also match ``sdaxl``.
_LIKE_ESCAPE = str.maketrans({"\\": "\\\\", "%": "\\%", "_": "\\_"})

MODEL_COLUMNS = (
    "id",
    "file_kind",
    "kind",
    "sha256",
    "display_name",
    "filename",
    "base_model",
    "trigger_words",
    "provenance",
    "training_run_id",
    "training_step",
    "param_count",
    "file_size",
    "hashed_at",
    "stack_id",
    "stack_position",
    "run_key",
    "created_at",
)

# Computed per row by the two aggregate joins below, never by a second query.
AGGREGATE_COLUMNS = (
    "member_count",
    "total_size",
    "newest_member_at",
    "newest_file_mtime",
)

# A stack's numbers, one grouped pass over ``model``. The shelf shows a stack as
# one row whose size is the sum of every member (a cover understates by ~6x in
# the column the shelf exists to answer) and whose date is the newest member's.
# Sorting never reorders members: ``stack_position`` is the cover and stays put.
_STACK_JOIN = """
LEFT JOIN (
    SELECT stack_id,
           COUNT(*)        AS member_count,
           SUM(file_size)  AS total_size,
           MAX(created_at) AS newest_member_at
    FROM model
    WHERE stack_id IS NOT NULL
    GROUP BY stack_id
) st ON st.stack_id = m.stack_id
"""

# "File modified" is a fact about a *copy*, and a model can have several. The
# newest ``present`` one is the honest answer: a ``missing`` row's mtime is the
# last thing we saw, not the last thing that happened.
_LOCATION_JOIN = """
LEFT JOIN (
    SELECT model_id, MAX(file_mtime) AS newest_file_mtime
    FROM model_file
    WHERE state = 'present'
    GROUP BY model_id
) loc ON loc.model_id = m.id
"""

_SELECT_LIST = ", ".join(
    [*(f"m.{name}" for name in MODEL_COLUMNS), *(f"{c}" for c in AGGREGATE_COLUMNS)]
)

_FROM = f"FROM model m{_STACK_JOIN}{_LOCATION_JOIN}"

# The five sort keys ruled 2026-08-08. ``COALESCE`` on the stack aggregate is the
# "a row never sorts by a number it does not display" rule in SQL: a stacked row
# displays the stack's total, a standalone row displays its own.
#
# ``COLLATE NOCASE`` on the two text keys because "Name A to Z" that puts every
# lowercase name after every uppercase one is not A to Z.
SORT_KEYS = {
    "added_at": "COALESCE(st.newest_member_at, m.created_at)",
    "file_mtime": "loc.newest_file_mtime",
    "name": "m.display_name COLLATE NOCASE",
    "size": "COALESCE(st.total_size, m.file_size)",
    "base_model": "m.base_model COLLATE NOCASE",
}

DEFAULT_SORT = "added_at"
DEFAULT_DIRECTION = "desc"

# Fixed and implicit, never a second control: a tie-break dropdown doubles the
# state space of a control nobody has learned. Name A to Z, falling back to
# filename when the primary key already *is* the name.
_TIE_BREAK = "m.filename COLLATE NOCASE"
_DEFAULT_TIE_BREAK = "m.display_name COLLATE NOCASE"


def _order_by(sort: str, direction: str) -> str:
    """Build the ``ORDER BY`` clause for one sort key and direction.

    Nulls last in **both** directions, spelled ``(expr) IS NULL`` rather than
    ``NULLS LAST`` so it does not depend on the host SQLite being 3.30+. It
    governs hundreds of rows, not an edge case: 37 % of a measured real folder
    records no base model and none of those files carries a name either, and a
    user who flips the direction does not want 900 unnamed rows at the top.

    ``m.id`` closes the clause so the order is total. Two rows that tie on the
    key *and* the tie-break would otherwise come back in whatever order SQLite
    chose that run, which is a paging bug waiting to be reported as a ghost.
    """
    expression = SORT_KEYS[sort]
    descending = "DESC" if direction == "desc" else "ASC"
    tie = _TIE_BREAK if sort == "name" else _DEFAULT_TIE_BREAK
    return (
        f"ORDER BY ({expression}) IS NULL, {expression} {descending}, "
        f"({tie}) IS NULL, {tie} ASC, m.id ASC"
    )


def fetch_models(
    hub,
    file_kinds: tuple[str, ...],
    *,
    base_model: Optional[str] = None,
    kind: Optional[str] = None,
    q: Optional[str] = None,
    sort: str = DEFAULT_SORT,
    direction: str = DEFAULT_DIRECTION,
) -> list[dict]:
    """Return the shelf rows of the given ``file_kind``s, sorted.

    One SELECT, whatever the filter and whatever the sort. The stack and
    location aggregates are joined in, so sorting 1,806 rows by "total size of
    the stack this row belongs to" costs one grouped scan rather than 1,806
    lookups.

    Args:
        hub: The open :class:`~pixlstash.hub.db.HubDatabase`.
        file_kinds: Which ``model.file_kind`` values to serve. One query, not one
            per kind: there is one content table.
        base_model: Exact match, or :data:`UNSET` to select the rows that record
            none. ``None`` means no filter — a null base model is a bulk state
            (37 % of a measured 91-file folder), so it is never dropped by
            default.
        kind: Adapter algorithm (``lora``, ``lokr``, …).
        q: Substring of the display name, filename or trigger words.
            Case-insensitive for ASCII, which is what SQLite's default LIKE
            gives, and wildcard-escaped.
        sort: One of :data:`SORT_KEYS`. Defaults to newest-added first.
        direction: ``asc`` or ``desc``. Nulls stay last in both.

    Returns:
        One dict per row, keyed by :data:`MODEL_COLUMNS` plus
        :data:`AGGREGATE_COLUMNS`.

    Raises:
        KeyError: *sort* is not a known key. The routes constrain it with a
            ``Literal`` before it reaches here, so this is the programmer-error
            path, not the request path.
    """
    where = [f"m.file_kind IN ({','.join('?' * len(file_kinds))})"]
    params: list = list(file_kinds)

    if base_model is not None:
        if base_model == UNSET:
            where.append("m.base_model IS NULL")
        else:
            where.append("m.base_model = ?")
            params.append(base_model)
    if kind:
        where.append("m.kind = ?")
        params.append(kind)
    if q and q.strip():
        term = f"%{q.strip().translate(_LIKE_ESCAPE)}%"
        where.append(
            "(m.display_name LIKE ? ESCAPE '\\' OR m.filename LIKE ? ESCAPE '\\' "
            "OR m.trigger_words LIKE ? ESCAPE '\\')"
        )
        params.extend([term, term, term])

    sql = (
        f"SELECT {_SELECT_LIST} {_FROM} "
        f"WHERE {' AND '.join(where)} {_order_by(sort, direction)}"
    )
    return [dict(row) for row in hub.fetchall(sql, tuple(params))]


def fetch_model_by_hash(hub, sha256: str) -> Optional[dict]:
    """Return the one model row carrying *sha256*, or None.

    The same SELECT as the list, so the detail response carries the stack and
    mtime aggregates too and the two shapes cannot drift apart.
    """
    rows = hub.fetchall(
        f"SELECT {_SELECT_LIST} {_FROM} WHERE m.sha256 = ?",
        (sha256,),
    )
    return dict(rows[0]) if rows else None


def fetch_locations(hub, model_id: Optional[int] = None) -> dict[int, list[dict]]:
    """Return ``model_id -> [location, …]`` in a single query.

    One query for the whole page, grouped in Python — not one per row, and not a
    join onto ``model`` that would duplicate every model row once per copy.

    Args:
        hub: The open hub database.
        model_id: Restrict to one model (the detail route). Omit for the page.
    """
    sql = (
        "SELECT mf.model_id, mf.model_folder_id, mf.relpath, mf.state, "
        "mf.file_mtime, f.path AS folder_path "
        "FROM model_file mf JOIN model_folder f ON f.id = mf.model_folder_id"
    )
    params: tuple = ()
    if model_id is not None:
        sql += " WHERE mf.model_id = ?"
        params = (model_id,)

    grouped: dict[int, list[dict]] = {}
    for row in hub.fetchall(sql, params):
        grouped.setdefault(int(row["model_id"]), []).append(
            {
                "folder_id": int(row["model_folder_id"]),
                "folder_path": row["folder_path"],
                "relpath": row["relpath"],
                "state": row["state"],
                "file_mtime": row["file_mtime"],
            }
        )
    return grouped


def fetch_attachments(vault, *, sha256: Optional[str] = None) -> dict[str, list[dict]]:
    """Return ``sha256 -> [{entity_type, entity_id}, …]`` from the **vault**.

    The cross-database half. These rows travel with the library while the models
    do not, which is why this is a second query and can never become a join.

    Args:
        vault: The active :class:`~pixlstash.vault.Vault`.
        sha256: Restrict to one model (the detail route). Omit for the page.
    """

    def fetch(session: Session):
        statement = select(AdapterAttachment)
        if sha256 is not None:
            statement = statement.where(AdapterAttachment.adapter_sha256 == sha256)
        return list(session.exec(statement).all())

    grouped: dict[str, list[dict]] = {}
    for row in vault.db.run_task(fetch, priority=DBPriority.IMMEDIATE):
        grouped.setdefault(row.adapter_sha256, []).append(
            {"entity_type": row.entity_type, "entity_id": row.entity_id}
        )
    return grouped


def attached_hashes(vault, entity_type: str, entity_id: int) -> set[str]:
    """Return the sha256 set one character or set uses, read from the vault."""

    def fetch(session: Session):
        return list(
            session.exec(
                select(AdapterAttachment.adapter_sha256).where(
                    AdapterAttachment.entity_type == entity_type,
                    AdapterAttachment.entity_id == entity_id,
                )
            ).all()
        )

    return set(vault.db.run_task(fetch, priority=DBPriority.IMMEDIATE))


def replace_attachments(
    vault, sha256: str, wanted: list[tuple[str, int]]
) -> list[dict]:
    """Make *sha256*'s attachment set exactly *wanted*, in one transaction.

    A full replacement rather than an add/remove pair: the shelf's assignment UI
    hands over the state it wants, and computing the delta client-side would let
    two open tabs interleave into a set neither of them chose.

    Every ``entity_id`` is checked against the live table before anything is
    written. ``adapter_attachment`` carries no foreign key — it cannot, its other
    end is in the hub — so nothing else would ever notice a typo'd id, and the
    row would sit there invisible and permanent.

    Args:
        vault: The active vault.
        sha256: The model's interop identity. Not validated here; the caller has
            already resolved it to a hub row.
        wanted: ``(entity_type, entity_id)`` pairs, deduplicated by the composite
            primary key.

    Returns:
        The attachment set as stored, oldest entity first.

    Raises:
        UnknownAttachmentEntityError: An entity id names no row in this library.
    """

    def write(session: Session):
        for entity_type, entity_id in wanted:
            model = _ENTITY_MODELS[entity_type]
            if session.get(model, entity_id) is None:
                raise UnknownAttachmentEntityError(entity_type, entity_id)
        session.exec(
            delete(AdapterAttachment).where(AdapterAttachment.adapter_sha256 == sha256)
        )
        now = datetime.now(timezone.utc)
        for entity_type, entity_id in dict.fromkeys(wanted):
            session.add(
                AdapterAttachment(
                    adapter_sha256=sha256,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    created_at=now,
                )
            )
        session.commit()
        return [
            {"entity_type": row.entity_type, "entity_id": row.entity_id}
            for row in session.exec(
                select(AdapterAttachment)
                .where(AdapterAttachment.adapter_sha256 == sha256)
                .order_by(AdapterAttachment.entity_type, AdapterAttachment.entity_id)
            ).all()
        ]

    return vault.db.run_task(write, priority=DBPriority.IMMEDIATE)
