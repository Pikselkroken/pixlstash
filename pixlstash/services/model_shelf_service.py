"""Reads for the model shelf: one hub query, one locations query, one vault query.

The shelf's rows straddle two SQLite files and this module is where that seam is
handled once. ``model`` / ``model_file`` / ``model_folder`` are **hub** tables
(what is on this disk is a fact about this machine); ``adapter_attachment`` is a
**vault** table (which character uses a LoRA is a fact about this library). No
foreign key and no SQL join can cross the two, so a filter that mixes them is two
queries intersected in Python - and, importantly, *two* queries no matter how
many rows come back.

Everything here is shaped so the sorting work is a change to one SELECT rather
than the unpicking of an N+1: the list is one hub query, the locations for the
whole page are one more, and the attachments for the whole page are one vault
query. Nothing is fetched per row.

**Sorting (B7) kept that promise.** A stack's size is the sum of its members and
its date is the newest member's, and a row must never sort by a number it does
not display - so those aggregates are two ``LEFT JOIN``s onto grouped subqueries
inside the *same* ``SELECT`` as the rows, not a lookup per row. 1,806 rows sorted
by an aggregate is the N+1 this shape exists to prevent.

Both list blocks - adapters and checkpoints - go through :func:`fetch_models`.
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
from pixlstash.services.model_features import (
    FEATURE_CAPTIONER,
    FEATURE_DETECTOR,
    FEATURE_FACE,
    FEATURE_SCORER,
    FEATURE_SEARCH,
    FEATURE_TAGGER,
)
from pixlstash.services.stack_detector import repair_stacks
from pixlstash.utils.adapter_header import (
    FILE_ADAPTER,
    FILE_CHECKPOINT,
    FILE_ENGINE,
    FILE_TEXT_ENCODER,
    FILE_UNKNOWN,
    FILE_VAE,
)

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

# Ceiling on one verb's selection, shared by `PATCH /models`, `POST
# /models/forget` and `POST /model-files/delete`. A shelf of 1,806 rows can be
# selected whole, so this is set above the largest real selection rather than
# below it: the cost is one hub UPDATE or DELETE over an id list, not a lookup
# per id, and 5,000 ids is still a single short transaction. It exists so a
# malformed client cannot post an unbounded list into the SQL builder, not to
# ration the verb. It lives here rather than in a route module because a second
# route needed it and routes do not import each other.
MAX_MODELS_PER_EDIT = 5000

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
    "icon_sha256",
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
            none. ``None`` means no filter - a null base model is a bulk state
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


def fetch_distinct_base_models(hub) -> list[str]:
    """Return every base model string recorded on a model row, once each.

    The user's own vocabulary, which ``known_base_models.completions`` takes as
    its *extra*: anything the module does not recognise is theirs and is offered
    verbatim from the moment it was saved. Distinct on the raw column, because
    that is what the field stores and what completing it has to produce.
    """
    rows = hub.fetchall(
        "SELECT DISTINCT base_model FROM model "
        "WHERE base_model IS NOT NULL AND TRIM(base_model) <> ''"
    )
    return [row["base_model"] for row in rows]


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

    One query for the whole page, grouped in Python - not one per row, and not a
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


def fetch_capabilities(hub, model_id: Optional[int] = None) -> dict[int, list[str]]:
    """Return ``model_id -> [capability, …]`` in a single query.

    Same shape and same reason as :func:`fetch_locations`: one query for the
    whole page, grouped in Python. Joining ``model_capability`` onto the row
    SELECT would duplicate every model row once per capability, which is exactly
    the fan-out the aggregate joins in that SELECT exist to avoid.

    Ordered by ``rowid``, so the set comes back in the order the declaration
    wrote it - primary first, matching ``model.kind``. A row's capabilities
    reading "Detection, Captioning" for a captioner is a small lie the shelf
    does not have to tell.

    Args:
        hub: The open hub database.
        model_id: Restrict to one model (the detail route). Omit for the page.
    """
    sql = "SELECT model_id, capability FROM model_capability"
    params: tuple = ()
    if model_id is not None:
        sql += " WHERE model_id = ?"
        params = (model_id,)
    sql += " ORDER BY model_id, rowid"

    grouped: dict[int, list[str]] = {}
    for row in hub.fetchall(sql, params):
        grouped.setdefault(int(row["model_id"]), []).append(str(row["capability"]))
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
    written. ``adapter_attachment`` carries no foreign key - it cannot, its other
    end is in the hub - so nothing else would ever notice a typo'd id, and the
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


# The columns a person may edit, and the only ones the verb layer writes. Every
# one of them is upserted with COALESCE by the scanner, so a correction made
# here is never re-derived away on the next pass.
CURATABLE_FIELDS = ("display_name", "base_model", "kind", "file_kind")

# What a file may be corrected to. Closed, and checked before the UPDATE rather
# than left to the CHECK constraint: a violation would surface as a 500 naming
# a constraint, which tells the owner nothing about the file they picked.
#
# `vae` and `text_encoder` are correctable like the rest, and they are the two
# most likely to need it: their kind is read off the folder the file sits in, so
# a support file kept outside the layout gets the answer the layout gives.
FILE_KINDS = (
    FILE_ADAPTER,
    FILE_CHECKPOINT,
    FILE_VAE,
    FILE_TEXT_ENCODER,
    FILE_UNKNOWN,
)

# What a model may be said to be FOR, which is a different question from what it
# IS and lives in its own table because one repo can answer it twice: Florence-2
# captions and detects.
#
# Settable at all because the shelf shows it. `model_capability` was written
# only by PixlStash's own declarations, so the Kind column read `Captioning` on
# rows whose editor offered nothing but file kinds - a value on screen that the
# owner could not correct. It is a CLASSIFICATION, and for a model PixlStash
# does not load it is a guess off a name or a `config.json`; the owner's answer
# beats ours.
#
# Two of the eight stored values are deliberately not offered. `checkpoint` is
# what the file IS and belongs to `FILE_KINDS`, where the same dialog already
# asks; `other` is the classifier's shrug, and an empty set says that without
# printing a heading for it.
CURATABLE_CAPABILITIES = (
    FEATURE_CAPTIONER,
    FEATURE_TAGGER,
    FEATURE_DETECTOR,
    FEATURE_FACE,
    FEATURE_SEARCH,
    FEATURE_SCORER,
)

# A location state that means the bytes are still out there somewhere, so the
# row is NOT a candidate for Forget. `unreachable` is in here deliberately: it
# is "we could not look", and forgetting on it would let one click wipe the
# curation for a drive that is merely unplugged.
_KEEPS_A_MODEL_ALIVE = ("present", "unreachable")


def update_models(hub, ids: list[int], changes: dict) -> list[int]:
    """Write curated columns onto the given models, in one transaction.

    Only the fields the caller actually sent are written, so setting a base
    model cannot blank a name that was never mentioned. A field set to ``None``
    IS written: clearing a wrong base model back to "not set" is a correction
    the owner is entitled to make, and it puts the row back in the `Needs a
    name` / unset queues where it belongs.

    ``capabilities`` is the one entry that is not a column. It is the complete
    set for every id, written to ``model_capability``: replaced wholesale rather
    than merged, because these sets are two entries long and a merge would leave
    the owner no way to take one off.

    Args:
        hub: The open hub database.
        ids: ``model.id`` values to write. Ids that name no row are ignored.
        changes: A subset of :data:`CURATABLE_FIELDS` mapped to their new
            values, and/or ``capabilities`` as a list.

    Returns:
        The ids that existed and were written, ascending.
    """
    if not ids or not changes:
        return []
    capabilities = changes.get("capabilities")
    columns_only = {k: v for k, v in changes.items() if k != "capabilities"}
    unknown = set(columns_only) - set(CURATABLE_FIELDS)
    if unknown:
        raise ValueError(f"not a curatable field: {sorted(unknown)}")

    placeholders = ", ".join("?" for _ in ids)
    with hub.transaction() as conn:
        existing = [
            int(row[0])
            for row in conn.execute(
                f"SELECT id FROM model WHERE id IN ({placeholders})", tuple(ids)
            ).fetchall()
        ]
        if existing:
            if columns_only:
                columns = ", ".join(f"{field} = ?" for field in columns_only)
                conn.execute(
                    f"UPDATE model SET {columns} WHERE id IN ({placeholders})",
                    tuple(columns_only.values()) + tuple(ids),
                )
            # Correcting what a file IS drops the capabilities we guessed it
            # served - unless the same call states them, which is the owner
            # answering both questions at once and must not be undone by the
            # answer to the first. Capability rows are only ever written by a
            # declaration (the scanner writes none), so this reaches exactly the
            # found HuggingFace repos, where the capability came from a name or
            # a `config.json` and the owner has just said it was wrong. Left
            # standing, the Feature axis would keep filing the row under the
            # guess while the Kind column beside it read the correction.
            if capabilities is not None or "file_kind" in changes:
                conn.execute(
                    f"DELETE FROM model_capability WHERE model_id IN ({placeholders})",
                    tuple(ids),
                )
            if capabilities:
                conn.executemany(
                    "INSERT INTO model_capability (model_id, capability) VALUES (?, ?)",
                    [
                        (model_id, capability)
                        for model_id in existing
                        # Ordered as the caller sent them, so `model.kind` and
                        # the shelf's primary-first reading still hold: the
                        # first box ticked is the word the Kind column shows.
                        for capability in dict.fromkeys(capabilities)
                    ],
                )
    return sorted(existing)


def forget_models(hub, ids: list[int]) -> tuple[list[int], list[dict]]:
    """Drop models whose files are gone, with their location rows.

    This is the one shelf operation that destroys curation: the ``model`` row
    goes and takes the name, base model, kind and trigger words with it. Folder
    removal only tombstones, which is why that needs no prompt and this one
    does.

    **Vault attachments are deliberately left alone.** ``adapter_attachment``
    lives in each library's vault keyed by the content hash, so there is no way
    to reach the ones held by libraries that are not open, and deleting only the
    active library's half would be an arbitrary subset. Left in place they are
    invisible (every read joins hub to vault) and they re-link by content if the
    file ever comes back, which is the same property that makes folder removal
    safe.

    Args:
        hub: The open hub database.
        ids: ``model.id`` values the caller wants forgotten.

    Returns:
        ``(forgotten, refused)``. ``refused`` carries ``{"id", "reason"}`` for
        each id that names no row, or that still has a copy somewhere.
    """
    if not ids:
        return [], []

    placeholders = ", ".join("?" for _ in ids)
    forgettable: list[int] = []
    refused: list[dict] = []

    # ONE critical section, gate and delete together. `hub.fetchall` takes and
    # releases the hub lock per call, so reading the states outside this block
    # left a window in which a background `ModelFolderScanner` could flip a row
    # from `missing` back to `present` between the check and the DELETE - and
    # the model would be forgotten anyway. Small window, unrecoverable
    # consequence, on the one shelf operation with no undo behind it.
    #
    # Reading on `conn` closes that against threads in THIS process, because
    # `HubDatabase._lock` is held for the whole block. It closed nothing against
    # the `pixlstash.libraries` CLI until `transaction()` began issuing
    # `BEGIN IMMEDIATE`: pysqlite defers `BEGIN` to the first DML, so these
    # SELECTs ran in autocommit and took no lock at all in WAL. The claim above
    # is only true because of that, which is why it is asserted over in
    # `test_transaction_is_already_open_before_its_first_write` (the SELECTs are
    # inside a transaction) and
    # `test_another_process_cannot_write_between_a_gate_read_and_its_write` (that
    # transaction holds the write lock), rather than here.
    with hub.transaction() as conn:
        known = {
            int(row[0])
            for row in conn.execute(
                f"SELECT id FROM model WHERE id IN ({placeholders})", tuple(ids)
            ).fetchall()
        }
        alive = {
            int(row[0])
            for row in conn.execute(
                f"SELECT DISTINCT model_id FROM model_file WHERE model_id IN "
                f"({placeholders}) AND state IN "
                f"({', '.join('?' for _ in _KEEPS_A_MODEL_ALIVE)})",
                tuple(ids) + _KEEPS_A_MODEL_ALIVE,
            ).fetchall()
        }

        # Engines are declared by PixlStash on every start, so forgetting one
        # deletes a row that comes straight back. Refused here rather than at the
        # route because the read belongs inside this transaction - the same
        # critical section the state gate runs in.
        #
        # Only while something still declares it, which is what every non-
        # `missing` state means here: a declared engine nothing has fetched is
        # `not_downloaded`, and `declare_folder`'s sweep writes `missing` exactly
        # when the declaration stopped naming the row. That is the DISCOVERED
        # roots - a repo dropped by `huggingface-cli delete-cache`, a deleted
        # InsightFace pack - and nothing fetches those back, so refusing them
        # left the owner a row drawn as a fault that no verb on the shelf could
        # clear.
        #
        # An engine whose copy is `present` or `unreachable` stays refused by
        # this set rather than falling through to `alive`, which is checked after
        # it: the reason it reports is `is_a_builtin_engine`, and that is the
        # more useful of the two answers for a file that is ours. So this only
        # ever widens Forget to an engine whose every copy is gone.
        builtin = {
            int(row[0])
            for row in conn.execute(
                f"SELECT id FROM model WHERE id IN ({placeholders}) AND file_kind = ? "
                "AND EXISTS (SELECT 1 FROM model_file WHERE model_id = model.id "
                "AND state <> 'missing')",
                (*ids, FILE_ENGINE),
            ).fetchall()
        }

        for model_id in ids:
            if model_id not in known:
                refused.append({"id": model_id, "reason": "no_such_model"})
            elif model_id in builtin:
                refused.append({"id": model_id, "reason": "is_a_builtin_engine"})
            elif model_id in alive:
                refused.append({"id": model_id, "reason": "still_has_a_copy"})
            else:
                forgettable.append(model_id)

        _purge(conn, forgettable)
    return sorted(forgettable), refused


def _purge(conn, ids: list[int]) -> None:
    """Delete the given models and their child rows, on an open connection.

    Repairs the stacks they were in on the way out. A member's row can leave a
    run through here - Forget and Delete both end at this function - and until
    the shelf let a single member be selected that could not happen, so nothing
    tidied up after it. Left alone it yields a run numbered ``0, 2, 3``, or one
    with no cover at all because the cover is what went, or a stack of one,
    which the shelf draws as a plain row while still holding a grouping nobody
    can see. :func:`~pixlstash.services.stack_detector.repair_stacks` states
    that rule once; this only has to remember to call it.
    """
    if not ids:
        return
    marks = ", ".join("?" for _ in ids)
    # Read before the delete, because afterwards the rows that named the stacks
    # are gone. Only the stacks these models were in: repairing every stack on
    # the shelf would race a live import, which inserts its `adapter_stack` row
    # before the members that make it a stack.
    stack_ids = [
        int(row["stack_id"])
        for row in conn.execute(
            f"SELECT DISTINCT stack_id FROM model "
            f"WHERE id IN ({marks}) AND stack_id IS NOT NULL",
            tuple(ids),
        ).fetchall()
    ]
    # Children first: `model_file` and `model_capability` both reference
    # `model(id)`, and the delete order is what keeps this working without
    # turning foreign keys off. Missing either one does not leak a row, it
    # aborts the whole delete.
    conn.execute(f"DELETE FROM model_file WHERE model_id IN ({marks})", tuple(ids))
    conn.execute(
        f"DELETE FROM model_capability WHERE model_id IN ({marks})", tuple(ids)
    )
    conn.execute(f"DELETE FROM model WHERE id IN ({marks})", tuple(ids))
    repair_stacks(conn, stack_ids)


def purge_deleted_models(hub, emptied: dict[int, list[tuple[int, str]]]) -> list[int]:
    """Drop the location rows a delete removed, and the models left with none.

    The row half of ``POST /model-files/delete``, which unlinks the bytes first
    and calls this for the copies it actually removed. It shares
    :func:`forget_models`' delete order, and it keeps a gate of its own - a
    narrower one, because the state check there would refuse every row this is
    called for. A ``model`` row goes only when **no** ``model_file`` row for it
    survives the location delete, so a copy a background ``ModelFolderScanner``
    registered while the files were being removed keeps its model alive rather
    than being purged out from under a file that is really there. That is the
    other half of the window :func:`~pixlstash.routes.model_files._plan_deletions`
    cannot hold a transaction across.

    Args:
        hub: The open hub database.
        emptied: ``model.id`` to the ``(model_folder_id, relpath)`` keys of the
            copies this call dealt with.

    Returns:
        The ids whose ``model`` row is gone, ascending. Shorter than
        ``emptied`` only in the race above, which the caller logs.
    """
    if not emptied:
        return []
    ids = list(emptied)
    marks = ", ".join("?" for _ in ids)
    with hub.transaction() as conn:
        for model_id, keys in emptied.items():
            for folder_id, relpath in keys:
                conn.execute(
                    "DELETE FROM model_file WHERE model_folder_id = ? AND relpath = ?",
                    (folder_id, relpath),
                )
        survivors = {
            int(row[0])
            for row in conn.execute(
                f"SELECT DISTINCT model_id FROM model_file WHERE model_id IN ({marks})",
                tuple(ids),
            ).fetchall()
        }
        gone = [model_id for model_id in ids if model_id not in survivors]
        _purge(conn, gone)
    return sorted(gone)
