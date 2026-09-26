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
from pixlstash.services.workflow_hash import (
    SHA256_FIELD_RE,
    WorkflowGraphError,
    assets_from_reduction,
    digests_with_prefix,
    normalized_filename,
    reduce_api_graph,
)
from pixlstash.services.workflow_library_service import (
    cover_order,
    recipe_picture_counts,
    variant_cover_candidates,
)
from pixlstash.utils.adapter_header import (
    FILE_ADAPTER,
    FILE_CHECKPOINT,
    FILE_ENGINE,
    FILE_TEXT_ENCODER,
    FILE_UNKNOWN,
    FILE_VAE,
)
from pixlstash.utils.known_base_models import (
    COMPANION_LAYOUTS,
    SOURCE_FILENAME,
    SOURCE_USER,
    family_of,
    fold,
    modality_of,
    rank,
)
from pixlstash.utils.sql_chunking import chunked

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
    "family",
    "quant",
    "weights_id",
    "base_model_canonical",
    "base_model_source",
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

# What a row is grouped, sorted and filtered under: the identified base model,
# else the raw string for one nothing has identified (or a person typed
# something the table does not know).
_BASE_MODEL_KEY = "COALESCE(m.base_model_canonical, m.base_model)"

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
    "base_model": f"{_BASE_MODEL_KEY} COLLATE NOCASE",
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
        base_model: Exact match on the identified base model or on the raw
            string (a caller holding the trainer's own spelling still gets its
            rows), or :data:`UNSET` to select the rows that record none. ``None`` means no filter - a null base model is a bulk state
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
            where.append(f"{_BASE_MODEL_KEY} IS NULL")
        else:
            where.append(f"({_BASE_MODEL_KEY} = ? OR m.base_model = ?)")
            params.extend([base_model, base_model])
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


def fetch_picture_counts(hub, vault) -> dict[int, dict[str, int]]:
    """How many kept pictures in the active library used each model, by tier.

    ``verified`` counts pictures whose recipe names the model by its digest (a
    PixlStash loader's ``*_sha256``, or an A1111 short hash only this model's
    digest starts with): that exact file. ``by_filename`` counts
    the rest whose recipe names a file called what one of the model's copies is
    called: a file of that name, which is all the graph says. The two are never
    summed here, because a count that mixes them claims a certainty the data
    does not have, and a picture counted in the first is not counted again in
    the second.

    Read from the recipe keys every scanned picture carries and the hub's recipe
    asset names, so it covers pictures imported before the recipe tables existed
    and heals by itself when a model is added to the shelf later. A name that
    was forgotten matches nothing.

    Returns:
        ``{model_id: {"verified": n, "by_filename": n}}``, models no kept
        picture used absent.
    """
    pictures = vault.db.run_task(recipe_picture_counts, priority=DBPriority.IMMEDIATE)
    if not pictures:
        return {}
    by_name, by_digest, _filenames, _names = recipe_asset_index(hub)
    sorted_digests = sorted(by_digest)

    verified: dict[int, set[str]] = {}
    named: dict[int, set[str]] = {}
    for row in hub.fetchall(
        "SELECT structural_hash, widget_name, normalized_filename "
        "FROM workflow_recipe_asset"
    ):
        recipe = row["structural_hash"]
        if recipe not in pictures:
            continue
        if SHA256_FIELD_RE.search(row["widget_name"]):
            # An A1111 short hash is verified only when it names one model;
            # otherwise its picture is left to the filename tier.
            matched = models_for_digest(
                row["normalized_filename"], by_digest, sorted_digests
            )
            if len(matched) == 1:
                verified.setdefault(matched.pop(), set()).add(recipe)
        else:
            for model_id in by_name.get(row["normalized_filename"], ()):
                named.setdefault(model_id, set()).add(recipe)

    return {
        model_id: {
            "verified": sum(pictures[r] for r in verified.get(model_id, ())),
            "by_filename": sum(
                pictures[r]
                for r in named.get(model_id, set()) - verified.get(model_id, set())
            ),
        }
        for model_id in verified.keys() | named.keys()
    }


def attached_characters(vault, digests: list[str]) -> dict[str, list[tuple[int, str]]]:
    """``{sha256: [(character id, name)]}`` for the adapters named, oldest first.

    The workflow card's half of :func:`fetch_attachments`: only characters, and
    with their names, for the card's recipe LoRAs (``workflow_card_service``).
    """
    if not digests:
        return {}

    def fetch(session: Session):
        rows = []
        for batch in chunked(digests):
            rows.extend(
                session.exec(
                    select(
                        AdapterAttachment.adapter_sha256, Character.id, Character.name
                    )
                    .join(Character, Character.id == AdapterAttachment.entity_id)
                    .where(
                        AdapterAttachment.entity_type == ENTITY_CHARACTER,
                        AdapterAttachment.adapter_sha256.in_(batch),
                    )
                    .order_by(Character.id)
                ).all()
            )
        return rows

    attached: dict[str, list[tuple[int, str]]] = {}
    for sha256, character_id, name in vault.db.run_task(
        fetch, priority=DBPriority.IMMEDIATE
    ):
        attached.setdefault(sha256.lower(), []).append((character_id, name))
    return attached


def recipe_asset_index(
    hub,
) -> tuple[dict[str, set[int]], dict[str, int], dict[int, str], dict[int, str]]:
    """How a recipe's asset names reach shelf models.

    Returns ``(by_name, by_digest, filenames, names)``. ``by_name`` maps a normalized
    basename to every model a file of that name could be - the row's
    ``filename`` and each copy's basename - so a name two rows share maps to
    both. ``by_digest`` maps a lowercase sha256 to its one model. ``filenames``
    is the same ``model`` read the first map is built from, kept by id rather
    than thrown away: a caller that has resolved a digest needs the name to
    show for it, and re-issuing the identical SELECT to get it is a second scan
    of this table for nothing. ``names`` is the name a person gave each model
    that has one, off the same read, so a caller can show the shelf's name for
    a model rather than the file's.
    """
    by_name: dict[str, set[int]] = {}
    filenames: dict[int, str] = {}
    names: dict[int, str] = {}
    for row in hub.fetchall(
        "SELECT id, filename, display_name FROM model WHERE filename IS NOT NULL"
    ):
        by_name.setdefault(normalized_filename(row["filename"]), set()).add(row["id"])
        filenames[row["id"]] = row["filename"]
        if row["display_name"] and row["display_name"].strip():
            names[row["id"]] = row["display_name"].strip()
    for row in hub.fetchall("SELECT model_id, relpath FROM model_file"):
        by_name.setdefault(normalized_filename(row["relpath"]), set()).add(
            row["model_id"]
        )
    by_digest = {
        row["sha256"].lower(): row["id"]
        for row in hub.fetchall("SELECT id, sha256 FROM model WHERE sha256 IS NOT NULL")
    }
    return by_name, by_digest, filenames, names


def adapter_digest_index(hub) -> tuple[dict[str, set[str]], set[str]]:
    """Which shelf LoRA a graph's ``lora_name`` names, by case-folded basename.

    Returns ``(by_name, digests)``: ``by_name`` maps a
    :func:`normalized_filename` (lowercase basename) to the SHA-256 of every
    shelf model a file of that name could be - the row's ``filename`` and each
    copy's ``relpath`` - and ``digests`` is every such model's SHA-256. Only
    the kinds a LoRA loader can load count (an adapter, or a file the shelf has
    not classified), because a checkpoint sharing a LoRA's name is not the
    LoRA, and a digest nothing can load is no answer to "which LoRA is this".

    A name two models share maps to both, and the caller treats that as no
    match: it is the frontend's ``resolveRecipeLoras`` rule and
    ``_resolve_against_shelf``'s, so the LoRA editor, the save dialog and the
    run all agree on what the shelf can name (#1478).
    """
    kinds = (FILE_ADAPTER, FILE_UNKNOWN)
    by_name: dict[str, set[str]] = {}
    digests: set[str] = set()
    sha_by_id: dict[int, str] = {}
    for row in hub.fetchall(
        "SELECT id, filename, sha256 FROM model "
        "WHERE sha256 IS NOT NULL AND file_kind IN (?, ?)",
        kinds,
    ):
        digest = str(row["sha256"]).lower()
        sha_by_id[int(row["id"])] = digest
        digests.add(digest)
        if row["filename"]:
            by_name.setdefault(normalized_filename(row["filename"]), set()).add(digest)
    for row in hub.fetchall("SELECT model_id, relpath FROM model_file"):
        digest = sha_by_id.get(int(row["model_id"]))
        if digest is not None and row["relpath"]:
            by_name.setdefault(normalized_filename(row["relpath"]), set()).add(digest)
    return by_name, digests


def model_name_aliases(hub) -> dict[str, list[str]]:
    """The other names a recipe's model filename can be loaded under (#1439).

    Keyed by a normalized basename (:func:`normalized_filename`, so **lowercase**
    - the caller has to fold its lookup the same way), valued by the names of
    that model's copies that are actually **present**: the full relpath as the
    scanner recorded it and its basename, in the **scanner's own spelling** and
    never folded. A ComfyUI combo entry is a path relative to one of ComfyUI's
    own model folders and nothing here knows which prefix it puts in front, so
    both forms are offered and the caller's ``object_info`` decides; generosity
    here costs nothing because the verification is downstream. The *case* is not
    generosity but correctness: ComfyUI compares exactly, so a lowercased
    candidate is a filename it would refuse.

    **Same model, therefore same bytes.** The hub is content-addressed - one
    ``model`` row per SHA-256 - so every name under one key names one file's
    contents, which is what makes this a substitution rather than the suggestion
    a same-weights-different-precision match would have to stay.

    The keys include names no copy of which is present, which is the whole point:
    a copy removed to keep one of several (``POST /model-files/merge``) keeps its
    row in ``state = 'removed'``, and the name a recipe recorded is the one a
    graph still asks for.

    A candidate equal to the key's own spelling is dropped, but one differing
    only in case is not: a graph naming ``MyLora.safetensors`` on an install that
    lists ``mylora.safetensors`` is a real miss (``_match_option`` reports it as
    "present under a different case") and the correctly-spelled copy is the fix.

    A name two models share is dropped rather than resolved, the same rule
    ``picture_recipe_service._resolve_against_shelf`` applies to a name that
    matches two rows: it names neither of them, and swapping to a coin-flip
    would substitute a different model's weights into somebody's run.

    Returns:
        ``{lowercased basename: [name, ...]}``, each name as the scanner recorded
        it, the key's own source spelling excluded, and no entry at all for a
        model with nothing present to offer.
    """
    names_by_model: dict[int, set[str]] = {}
    present_by_model: dict[int, list[str]] = {}
    for row in hub.fetchall(
        "SELECT id, filename FROM model WHERE filename IS NOT NULL"
    ):
        names_by_model.setdefault(int(row["id"]), set()).add(row["filename"])
    for row in hub.fetchall("SELECT model_id, relpath, state FROM model_file"):
        model_id = int(row["model_id"])
        names_by_model.setdefault(model_id, set()).add(row["relpath"])
        if row["state"] != "present":
            continue
        offered = present_by_model.setdefault(model_id, [])
        # The relpath and its basename, both as the scanner spelled them. NOT
        # `normalized_filename`, which lowercases: that is the right key for a
        # lookup and the wrong thing to hand ComfyUI, which compares exactly.
        basename = row["relpath"].replace("\\", "/").rsplit("/", 1)[-1]
        for name in (row["relpath"], basename):
            if name not in offered:
                offered.append(name)

    claimants: dict[str, set[int]] = {}
    for model_id, names in names_by_model.items():
        for name in names:
            claimants.setdefault(normalized_filename(name), set()).add(model_id)

    aliases: dict[str, list[str]] = {}
    for model_id, names in names_by_model.items():
        offered = present_by_model.get(model_id)
        if not offered:
            continue
        for name in names:
            key = normalized_filename(name)
            if len(claimants[key]) != 1:
                continue
            for candidate in offered:
                if candidate == name:
                    continue
                aliases.setdefault(key, [])
                if candidate not in aliases[key]:
                    aliases[key].append(candidate)
    return aliases


def models_for_digest(
    value: str, by_digest: dict[str, int], sorted_digests: list[str]
) -> set[int]:
    """Every shelf model a ``*_sha256`` asset value could name.

    One for a whole digest. An A1111 short hash (``services/a1111_recipe.py``)
    names every model whose digest starts with it: one is that model, and
    several is a name its recipe cannot pin down, which both callers treat the
    way they treat an ambiguous filename. ``sorted_digests`` is sorted once per
    call rather than per asset row, which is what keeps the prefix search a
    bisect rather than a scan of the shelf.
    """
    return {by_digest[digest] for digest in digests_with_prefix(value, sorted_digests)}


# What a generation graph loads BESIDE a model rather than as one: the files a
# delete can leave with nothing to serve.
SUPPORT_FILE_KINDS = (FILE_VAE, FILE_TEXT_ENCODER)

# Kinds that never make a support file needed. An adapter runs on a base model
# and needs whatever that base needs, so counting it would keep a VAE "in use"
# by a LoRA whose checkpoint is the thing being deleted. `unknown` is NOT here:
# it may be a base model we failed to recognise, and keeping a file is the safe
# answer to not knowing.
_NOT_CONSUMERS = (*SUPPORT_FILE_KINDS, FILE_ADAPTER, FILE_ENGINE)


def resolve_recipe_models(
    hub, index: Optional[tuple] = None
) -> tuple[dict[str, set[int]], dict[str, set[int]], set[str]]:
    """Which shelf models each recipe on this hub is proven to have run with.

    One ``workflow_recipe`` is one graph that ran with exactly the files its
    ``workflow_recipe_asset`` rows name, so the models one recipe resolves to
    are models a picture proves ran together. Every recipe the hub holds
    counts, from every library and whether or not its pictures still exist.

    Returns ``(recipe_models, ambiguous, unresolved)``:

    * ``recipe_models`` - ``{structural_hash: {model_id, ...}}``, recipes that
      reached no shelf row absent;
    * ``ambiguous`` - per recipe, the models it reached only through a name or
      a digest prefix that several shelf rows answer to, so the membership is
      a guess about which of them;
    * ``unresolved`` - recipes naming a digest no shelf row matches while some
      row is still waiting for its hash. The ghost reader's rule
      (``hub/workflows._model_ghost_names``): that row may be the model the
      digest names, so the recipe's membership is incomplete rather than wrong.

    Shared by :func:`fetch_companions` and :func:`fetch_workflow_sets` because
    both read co-occurrence off the same table and must agree about what a
    recipe names - a second copy of this resolution is how the delete warning
    and the grid would come to disagree about the same pair of files.

    *index* is a :func:`recipe_asset_index` the caller already built in this
    request, so a route that needs both does not scan the tables twice.
    """
    by_name, by_digest, _filenames, _names = index or recipe_asset_index(hub)
    sorted_digests = sorted(by_digest)
    digests_are_complete = not hub.fetchall(
        "SELECT 1 FROM model WHERE sha256 IS NULL AND file_kind <> ? LIMIT 1",
        (FILE_ENGINE,),
    )

    recipe_models: dict[str, set[int]] = {}
    ambiguous: dict[str, set[int]] = {}
    unresolved: set[str] = set()
    for row in hub.fetchall(
        "SELECT structural_hash, widget_name, normalized_filename "
        "FROM workflow_recipe_asset"
    ):
        recipe = row["structural_hash"]
        if SHA256_FIELD_RE.search(row["widget_name"]):
            matched = models_for_digest(
                row["normalized_filename"], by_digest, sorted_digests
            )
            if not matched and not digests_are_complete:
                unresolved.add(recipe)
            if len(matched) > 1:
                ambiguous.setdefault(recipe, set()).update(matched)
        else:
            matched = by_name.get(row["normalized_filename"], set())
            if len(matched) > 1:
                ambiguous.setdefault(recipe, set()).update(matched)
        if matched:
            recipe_models.setdefault(recipe, set()).update(matched)
    return recipe_models, ambiguous, unresolved


def fetch_companions(hub, ids: list[int]) -> dict:
    """What deleting *ids* would leave behind, from the recipes on this machine.

    **The evidence is co-occurrence**: one ``workflow_recipe`` is one graph that
    ran with exactly the files its ``workflow_recipe_asset`` rows name, so a VAE
    and a checkpoint in one recipe are proven to work together. Every recipe the
    hub holds counts, from every library and whether or not its pictures still
    exist: evidence from more places can only keep more files, never offer one.

    For each support file (``vae``, ``text_encoder``) that shares a recipe with
    a model being deleted, its **consumers** are the base models across *all* of
    its recipes (adapters and support files excluded, ``unknown`` included):

    * **orphaned** - every consumer *a recipe records* is being deleted. That
      is all it means: a kept base model no recipe names may need the file
      too, which is why ``unrecorded`` counts those models beside it;
    * **shared** - some consumer stays, and it is named;
    * **unknown** - a recipe reached the file only by a basename another shelf
      row also has, or named a model by a digest while some shelf row is still
      waiting for its hash, so a consumer may be missing. Never reported as
      orphaned.

    **The absence of a recipe is not evidence.** A deleted model no recipe names
    is listed under ``no_evidence``, so the caller can say it cannot tell, and
    its support files are simply not examined.

    ``in_use`` answers the other direction: a support file being deleted that a
    model staying on the shelf has run with.

    Args:
        hub: The open hub database.
        ids: ``model.id`` values about to be deleted.

    Returns:
        ``{"orphaned", "shared", "unknown", "in_use", "no_evidence",
        "unrecorded"}``. The first four are lists of ``{"id", "name", ...}``,
        ``shared`` and ``in_use`` entries also carrying ``used_with``;
        ``no_evidence`` is a list of ids; ``unrecorded`` counts the base models
        staying on the shelf that no recipe names.
    """
    deleting = set(ids)
    models = {
        int(row["id"]): row
        for row in hub.fetchall(
            "SELECT id, file_kind, display_name, filename, file_size FROM model"
        )
    }
    recipe_models, ambiguous, unresolved = resolve_recipe_models(hub)

    recipes_of: dict[int, set[str]] = {}
    for recipe, members in recipe_models.items():
        for model_id in members:
            recipes_of.setdefault(model_id, set()).add(recipe)

    def kind(model_id: int) -> str:
        # `.get`, not an index: `models` was read before the recipe index, so a
        # model registered by a scan between the two queries is in a recipe and
        # not in this dict. `""` is in no kind list, so such a row is neither a
        # consumer nor a support file - which is the safe answer, and the same
        # guard `fetch_workflow_sets` makes with `if member in models`.
        row = models.get(model_id)
        return row["file_kind"] if row else ""

    def entry(model_id: int) -> dict:
        row = models[model_id]
        return {
            "id": model_id,
            "name": row["display_name"] or row["filename"] or f"model {model_id}",
            "file_size": row["file_size"],
        }

    def consumers(model_id: int) -> set[int]:
        found: set[int] = set()
        for recipe in recipes_of.get(model_id, ()):
            found.update(
                other
                for other in recipe_models[recipe]
                if other != model_id and kind(other) not in _NOT_CONSUMERS
            )
        return found

    def used_with(members: set[int]) -> list[dict]:
        return [
            {"id": m, "name": entry(m)["name"]}
            for m in sorted(members, key=lambda m: entry(m)["name"].lower())
        ]

    result: dict = {
        "orphaned": [],
        "shared": [],
        "unknown": [],
        "in_use": [],
        "no_evidence": sorted(
            model_id
            for model_id in deleting
            if model_id in models and model_id not in recipes_of
        ),
        "unrecorded": sum(
            1
            for model_id, row in models.items()
            if model_id not in deleting
            and model_id not in recipes_of
            and row["file_kind"] not in _NOT_CONSUMERS
        ),
    }

    candidates: set[int] = set()
    for model_id in deleting & recipes_of.keys():
        for recipe in recipes_of[model_id]:
            candidates.update(
                other
                for other in recipe_models[recipe]
                if other not in deleting and kind(other) in SUPPORT_FILE_KINDS
            )
        if kind(model_id) in SUPPORT_FILE_KINDS:
            kept = consumers(model_id) - deleting
            if kept:
                result["in_use"].append(
                    {**entry(model_id), "used_with": used_with(kept)}
                )

    for support_id in sorted(candidates, key=lambda m: entry(m)["name"].lower()):
        users = consumers(support_id)
        if not users & deleting:
            # It shares a recipe with a deleted adapter or support file only;
            # this delete does not change what it serves.
            continue
        if any(
            support_id in ambiguous.get(r, ()) or r in unresolved
            for r in recipes_of[support_id]
        ):
            result["unknown"].append(entry(support_id))
        elif users - deleting:
            result["shared"].append(
                {**entry(support_id), "used_with": used_with(users - deleting)}
            )
        else:
            result["orphaned"].append(entry(support_id))
    return result


def record_comfyui_history(hub, history: dict) -> int:
    """File which shelf models ran together in each finished ComfyUI run.

    *history* is ``GET /history`` as ComfyUI answers it. A run counts only when
    its status says it finished: a run that errored proves nothing about its
    files working together. Each asset name resolves the way a recipe's does
    (:func:`resolve_recipe_models`), but **only an unambiguous match is kept**,
    because what is stored is the model id and a guess stored as an id stops
    reading as a guess. A name the shelf does not hold is not stored at all, so
    nothing here keeps a model name the shelf has forgotten.

    Idempotent: a run read twice writes nothing new, and one re-read after the
    shelf gained a file gains that file.

    Returns:
        How many finished runs named at least one shelf model.
    """
    by_name, by_digest, _filenames, _names = recipe_asset_index(hub)
    sorted_digests = sorted(by_digest)
    rows: list[tuple[str, int]] = []
    runs = 0
    for prompt_id, entry in history.items():
        try:
            if entry["status"]["status_str"] != "success":
                continue
            nodes = reduce_api_graph(entry["prompt"][2])
        except (KeyError, IndexError, TypeError, WorkflowGraphError) as exc:
            logger.info(
                "Skipped ComfyUI run %s from the history: no finished graph (%s: %s)",
                prompt_id,
                type(exc).__name__,
                exc,
            )
            continue
        found: set[int] = set()
        for widget, value in assets_from_reduction(nodes):
            if SHA256_FIELD_RE.search(widget):
                matched = models_for_digest(value, by_digest, sorted_digests)
            else:
                matched = by_name.get(value, set())
            if len(matched) == 1:
                found |= matched
        if found:
            runs += 1
            rows.extend((str(prompt_id), model_id) for model_id in found)
    with hub.transaction() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO comfyui_history_model (prompt_id, model_id) "
            "VALUES (?, ?)",
            rows,
        )
    return runs


def known_base_model(row) -> Optional[str]:
    """The base model a clone may reason about for one ``model`` row, or ``None``.

    The identified known label (``base_model_canonical``) when it came from the
    owner, the file's declared metadata or its filename, and otherwise the
    stored ``base_model`` folded where it folds. A **fuzzy** match is left out:
    the shelf tags those as guesses, and a guess would widen the companion
    ladder or flag a LoRA on a claim nobody made.
    """
    canonical = row["base_model_canonical"]
    if canonical and rank(row["base_model_source"]) >= rank(SOURCE_FILENAME):
        return canonical
    return fold(row["base_model"]) or row["base_model"]


# The widening ladder `propose_companions` climbs, narrowest first. Each answer
# carries the step that produced it, so a weaker inference reads as weaker. The
# last step is not evidence at all, and says so.
VIA_CHECKPOINT = "checkpoint"
VIA_BASE_MODEL = "base_model"
VIA_FAMILY = "family"
VIA_DECLARED = "declared"
# Not a ladder step: a hand-made workflow set (#1520) that names this checkpoint.
# The owner's own word, so it is listed ahead of every step of the ladder,
# the declared-layout fallback included.
VIA_GROUPED = "grouped"


def propose_companions(
    hub, checkpoint_id: int, index: Optional[tuple] = None
) -> dict[str, list[dict]]:
    """The VAEs and text encoders recipes have run beside *checkpoint_id*.

    :func:`fetch_companions` read forwards: the same co-occurrence evidence, asked
    "what goes with this" rather than "what would deleting this orphan". A VAE
    carries no ``base_model`` to compare, so the first three steps propose a
    support file only because a recipe on this hub named it beside a
    checkpoint. The fourth is the cold case, and is not evidence.

    Per support kind, the first step of the ladder with any answer wins:

    1. ``checkpoint`` - recipes that name this checkpoint;
    2. ``base_model`` - recipes naming any base model with the same base model
       label (:func:`known_base_model`: the identified label unless it is a
       fuzzy guess), when this checkpoint has one;
    3. ``family`` - recipes naming any base model of the same architecture
       family (:func:`family_of`), when the label folds to one, and never
       across modalities (:func:`modality_of`): a video base does not answer
       for an image checkpoint, nor the reverse;
    4. ``declared`` - no recipe at all: the shelf's support files whose stored
       tensor layout (``model.family``) the family declares in
       :data:`~pixlstash.utils.known_base_models.COMPANION_LAYOUTS`. A layout
       that fits says the file loads, not that it suits, so these carry
       ``recipes`` 0 and the caller must present them as untested.

    **ComfyUI's own runs count too** (#1518): the ``comfyui_history_model``
    rows a workflow pull read off ``GET /history``, so a checkpoint used in
    ComfyUI but in nothing PixlStash filed still has evidence. Nothing here asks
    ComfyUI; the rows are whatever the last pull left. The two are counted
    apart, ``recipes`` and ``history_runs``, and recipe evidence ranks first.
    A run is evidence at every step, so ``declared`` answers only when neither
    kind does.

    A checkpoint nothing in its family has run with, whose family declares no
    layout for a kind or whose shelf holds no file of that layout, proposes
    nothing for that kind, and the caller is expected to say so. A support file a
    recipe reached only through an ambiguous name is not proposed from that
    recipe: it may be another row's file. Nor is one with no filename, which a
    clone has nothing to write for.

    Args:
        hub: The open hub database.
        checkpoint_id: The ``model.id`` the clone will load instead.
        index: A :func:`recipe_asset_index` already built in this request.

    **Grouped by the owner comes first.** Ahead of the ladder, every
    hand-made workflow set whose checkpoint member is this checkpoint proposes
    its on-shelf VAE and text-encoder members (``via: "grouped"``, ``recipes:
    0``, ``set_name`` the newest such set's name). ``prepick`` says whether the
    dialog may select one for the owner: true when the matching sets between
    them name exactly one on-shelf file of that kind, false when they name
    several. A set with no checkpoint never matches, and a member off the shelf
    is skipped. Ladder entries follow, minus any file already grouped, each
    with ``prepick`` true and ``set_name`` null.

    Returns:
        ``{"vae": [...], "text_encoder": [...]}``, each entry ``{"id",
        "filename", "display_name", "family", "via", "recipes",
        "history_runs", "set_name", "prepick"}``: grouped entries first, then
        most recipes first, then most runs. ``family`` is the file's own layout
        (``clip_l``, ``t5_xxl``), which is how a caller tells two text encoders
        apart. Both lists empty for an unknown id.
    """
    models = {
        int(row["id"]): row
        for row in hub.fetchall(
            "SELECT id, file_kind, base_model, base_model_canonical, "
            "base_model_source, filename, display_name, family FROM model"
        )
    }
    proposals: dict[str, list[dict]] = {kind: [] for kind in SUPPORT_FILE_KINDS}
    target = models.get(checkpoint_id)
    if target is None:
        return proposals
    recipe_models, ambiguous, _unresolved = resolve_recipe_models(hub, index)

    consumers = {
        model_id: row
        for model_id, row in models.items()
        if row["file_kind"] not in _NOT_CONSUMERS
    }
    ladder: list[tuple[str, set[int]]] = [(VIA_CHECKPOINT, {checkpoint_id})]
    label = known_base_model(target)
    if label:
        ladder.append(
            (
                VIA_BASE_MODEL,
                {
                    model_id
                    for model_id, row in consumers.items()
                    if known_base_model(row) == label
                },
            )
        )
    family = family_of(label)
    if family:
        modality = modality_of(label)
        ladder.append(
            (
                VIA_FAMILY,
                {
                    model_id
                    for model_id, row in consumers.items()
                    if family_of(known_base_model(row)) == family
                    and modality_of(known_base_model(row)) == modality
                },
            )
        )

    # ComfyUI's own runs (#1518), already resolved to unambiguous model ids by
    # `record_comfyui_history`, so nothing is subtracted from them.
    runs: dict[str, set[int]] = {}
    for row in hub.fetchall("SELECT prompt_id, model_id FROM comfyui_history_model"):
        runs.setdefault(row["prompt_id"], set()).add(int(row["model_id"]))

    def tally(witnesses, kind, anchors, skip) -> dict[int, int]:
        counts: dict[int, int] = {}
        for key, members in witnesses.items():
            if not members & anchors:
                continue
            for member in members - skip.get(key, set()):
                row = models.get(member)
                if row is not None and row["file_kind"] == kind and row["filename"]:
                    counts[member] = counts.get(member, 0) + 1
        return counts

    # Newest set first, so a file two sets group reads under the newest name.
    grouped: dict[str, dict[int, Optional[str]]] = {
        kind: {} for kind in SUPPORT_FILE_KINDS
    }
    for row in hub.fetchall(
        "SELECT s.name, member_model.id AS model_id "
        "FROM model_workflow_set AS s "
        "JOIN model_workflow_set_member AS ckpt "
        "  ON ckpt.set_id = s.id AND ckpt.slot = 'checkpoint' "
        "JOIN model AS ckpt_model ON ckpt_model.sha256 = ckpt.sha256 "
        "JOIN model_workflow_set_member AS member "
        "  ON member.set_id = s.id AND member.slot IN ('vae', 'text_encoder') "
        "JOIN model AS member_model ON member_model.sha256 = member.sha256 "
        "WHERE ckpt_model.id = ? "
        "ORDER BY s.created_at DESC, s.id DESC",
        (checkpoint_id,),
    ):
        row_model = models.get(int(row["model_id"]))
        # Filed by what the file IS, so a VAE never lands in the encoder list
        # whichever slot it was put in; no filename means nothing to write.
        if row_model is None or not row_model["filename"]:
            continue
        if row_model["file_kind"] in grouped:
            grouped[row_model["file_kind"]].setdefault(
                int(row["model_id"]), row["name"]
            )

    def layout_count(kind: str, model_id: int) -> int:
        # Per LAYOUT, not per kind: a Flux set's clip_l and t5_xxl are one file
        # each for two different rows, and counting them together would refuse
        # to pre-pick either and hand both rows to weaker, ungrouped evidence.
        family = models[model_id]["family"]
        return sum(1 for other in grouped[kind] if models[other]["family"] == family)

    for kind in SUPPORT_FILE_KINDS:
        proposals[kind] = [
            {
                "id": model_id,
                "filename": models[model_id]["filename"],
                "display_name": models[model_id]["display_name"],
                "family": models[model_id]["family"],
                "via": VIA_GROUPED,
                "recipes": 0,
                "history_runs": 0,
                "set_name": set_name,
                "prepick": layout_count(kind, model_id) == 1,
            }
            for model_id, set_name in sorted(
                grouped[kind].items(),
                key=lambda item: (models[item[0]]["filename"] or "").lower(),
            )
        ]
        # Whether an EVIDENCE step answered, on recipes or ComfyUI runs (#1518).
        # Not `proposals[kind]`: the grouped
        # entries are already in there, and reading them as evidence would
        # skip the declared fallback for a row the owner's sets do not cover
        # (a Flux set grouping a clip_l leaves its t5 row with nothing).
        ladder_found = False
        for via, anchors in ladder:
            by_recipe = tally(recipe_models, kind, anchors, ambiguous)
            by_run = tally(runs, kind, anchors, {})
            if not by_recipe and not by_run:
                continue
            step = [
                {
                    "id": model_id,
                    "filename": models[model_id]["filename"],
                    "display_name": models[model_id]["display_name"],
                    "family": models[model_id]["family"],
                    "via": via,
                    "recipes": by_recipe.get(model_id, 0),
                    "history_runs": by_run.get(model_id, 0),
                    "set_name": None,
                    "prepick": True,
                }
                for model_id in sorted(
                    by_recipe.keys() | by_run.keys(),
                    key=lambda m: (
                        -by_recipe.get(m, 0),
                        -by_run.get(m, 0),
                        (models[m]["filename"] or "").lower(),
                    ),
                )
                # Already listed above as grouped. Dropped here rather than
                # before the step is chosen, so grouping a file never widens
                # the ladder past the step that found it.
                if model_id not in grouped[kind]
            ]
            proposals[kind] += step
            # Found only if the step added something. Evidence that merely
            # repeats files the owner already grouped tells the other rows of
            # this kind nothing new, so the declared fallback still answers
            # for them.
            ladder_found = bool(step)
            break
        layouts = COMPANION_LAYOUTS.get(family, {}).get(kind)
        if ladder_found or not layouts:
            continue
        proposals[kind] += [
            {
                "id": model_id,
                "filename": row["filename"],
                "display_name": row["display_name"],
                "family": row["family"],
                "via": VIA_DECLARED,
                "recipes": 0,
                "history_runs": 0,
            }
            for model_id, row in sorted(
                models.items(), key=lambda item: (item[1]["filename"] or "").lower()
            )
            if row["file_kind"] == kind
            and row["filename"]
            and row["family"] in layouts
            # Listed above as grouped already, under the owner's own word.
            and model_id not in grouped[kind]
        ]
    return proposals


# How many of a combination's pictures the grid puts on a card's cover.
#
# Three, the shipped workflow card's cover depth (`workflow_card_service`'s
# ``COVER_DEPTH``): the set grid reuses that card, so asking for more would
# fetch bitmaps nothing draws.
SET_COVER_DEPTH = 3


def fetch_workflow_sets(hub, vault) -> dict:
    """Every set of shelf models a picture in this library proves ran together.

    One entry per distinct *combination* - the model ids one recipe resolves to.
    Several recipes that name the same files are one combination, with their
    recipe and picture counts summed, because the combination is the fact and
    the recipe is one witness of it.

    **Co-occurrence is evidence; its absence is not.** Two models in one recipe
    proves they ran together; two models never seen together proves nothing at
    all, so no combination is withheld and no pair is ruled out. The models no
    recipe in this library names come back under ``no_set`` rather than being
    dropped, so the caller can say it cannot tell rather than implying nobody
    has tried them.

    Scoped to the pictures of the ACTIVE library, unlike
    :func:`fetch_companions`, which counts every recipe the hub holds. The two
    differ because they answer different questions: a delete warning must keep
    a file some other library needs, and this grid is a picture of what the
    library in front of the reader has actually made. A recipe the hub holds
    with no kept picture here is therefore not a set.

    Returns:
        ``{"combinations": [...], "no_set": [model_id, ...]}``. Each
        combination carries ``key`` (its sorted member ids, joined), ``models``
        (``id``, ``name``, ``filename``, ``kind``, ``file_size``,
        ``ambiguous``), ``recipes``, ``picture_count`` and ``covers`` (up to
        :data:`SET_COVER_DEPTH` cover candidates, best first).
    """
    recipe_models, ambiguous, unresolved = resolve_recipe_models(hub)
    pictures = vault.db.run_immediate_read_task(
        lambda session: (
            recipe_picture_counts(session),
            variant_cover_candidates(session, SET_COVER_DEPTH),
        )
    )
    counts, candidates = pictures

    covers_by_recipe: dict[str, list] = {}
    for candidate in candidates:
        covers_by_recipe.setdefault(candidate.structural_hash, []).append(candidate)

    models = {
        int(row["id"]): row
        for row in hub.fetchall(
            "SELECT id, file_kind, display_name, filename, file_size FROM model"
        )
    }

    # Keyed on the frozen member set, so two recipes naming the same files are
    # one card. `unsure` is OR-ed across the witnesses rather than AND-ed: one
    # recipe that could only match a basename is enough to make the membership
    # a guess, and a second, cleaner witness does not unmake the first.
    grouped: dict[frozenset[int], dict] = {}
    for recipe, members in recipe_models.items():
        # Models the shelf no longer holds - a Forget between the recipe read
        # and now - are dropped rather than drawn as an id with no name.
        present = frozenset(member for member in members if member in models)
        if not present:
            continue
        entry = grouped.setdefault(
            present,
            {"recipes": 0, "picture_count": 0, "covers": [], "unsure": set()},
        )
        entry["recipes"] += 1
        entry["picture_count"] += counts.get(recipe, 0)
        entry["covers"].extend(covers_by_recipe.get(recipe, ()))
        if recipe in unresolved:
            entry["unsure"].update(present)
        entry["unsure"].update(ambiguous.get(recipe, set()) & present)

    def name(model_id: int) -> str:
        row = models[model_id]
        return row["display_name"] or row["filename"] or f"model {model_id}"

    combinations = []
    for present, entry in grouped.items():
        # A combination with no kept picture in this library is not a set here:
        # the grid draws what the library has made, and a recipe that made
        # nothing in it has no cover, no count and nothing to show.
        if not entry["picture_count"]:
            continue
        ordered = sorted(
            present, key=lambda m: (_set_kind_rank(models[m]), name(m).lower())
        )
        combinations.append(
            {
                "key": ",".join(str(m) for m in sorted(present)),
                "models": [
                    {
                        "id": model_id,
                        "name": name(model_id),
                        "filename": models[model_id]["filename"],
                        "kind": models[model_id]["file_kind"],
                        "file_size": models[model_id]["file_size"],
                        "ambiguous": model_id in entry["unsure"],
                    }
                    for model_id in ordered
                ],
                "recipes": entry["recipes"],
                "picture_count": entry["picture_count"],
                "covers": sorted(entry["covers"], key=cover_order, reverse=True)[
                    :SET_COVER_DEPTH
                ],
            }
        )
    # Biggest evidence first, so the grid opens on the combinations the library
    # actually leans on; the key breaks ties so a refetch draws the same order.
    combinations.sort(key=lambda c: (-c["picture_count"], -c["recipes"], c["key"]))

    # Read off the combinations that SURVIVED, not off `grouped`: a model whose
    # only recipes made no kept picture here would otherwise be in no
    # combination and in no `no_set` either, and so be missing from the screen
    # altogether - the one outcome the honesty rule forbids.
    in_a_set = {
        member["id"] for combination in combinations for member in combination["models"]
    }
    return {
        "combinations": combinations,
        # Engines are left out, and that is the honesty rule rather than an
        # exception to it. `no_set` means "nothing here has been made with
        # these", which is a statement a reader can act on for a checkpoint and
        # is simply FALSE for a tagger: PixlStash downloaded it for itself, no
        # generation graph can load it, and it will never appear in a recipe on
        # any machine. Listing them would pad the card with files whose absence
        # says nothing at all - and it is the same exclusion `_NOT_CONSUMERS`
        # and the digest-completeness probe above already make.
        "no_set": sorted(
            model_id
            for model_id in set(models) - in_a_set
            if models[model_id]["file_kind"] != FILE_ENGINE
        ),
    }


# Where each kind sits in a combination's member list: the base model it is
# named after first, then whatever we could not classify, then the support files
# a graph loads beside it, then the adapters, then the engines.
#
# The card's name is taken from the HEAD of this list, so the order is what
# decides which file a set is called after. `unknown` sits second for that
# reason and no other: a Flux or Wan graph loads a diffusion file rather than a
# checkpoint, `classify_model_file` files most of those as `checkpoint` and the
# rest as `unknown`, and there is no diffusion `file_kind` to key on - so the
# rule is "an unclassified file names a set before a VAE does", which is a
# ranking rather than a claim about what the file is.
_SET_KIND_RANK = {
    FILE_CHECKPOINT: 0,
    FILE_UNKNOWN: 1,
    FILE_VAE: 2,
    FILE_TEXT_ENCODER: 3,
    FILE_ADAPTER: 4,
    FILE_ENGINE: 5,
}


def _set_kind_rank(row) -> int:
    return _SET_KIND_RANK.get(row["file_kind"], len(_SET_KIND_RANK))


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
                assignments = dict(columns_only)
                if "base_model" in assignments:
                    # In the same UPDATE, or the shelf keeps grouping a corrected
                    # row under the scanner's old guess. `user` outranks every
                    # scan, so the answer sticks - including a cleared one,
                    # which is the owner saying "none of these", not a blank
                    # for the next scan to guess into.
                    assignments["base_model_canonical"] = fold(
                        assignments["base_model"]
                    )
                    assignments["base_model_source"] = SOURCE_USER
                columns = ", ".join(f"{field} = ?" for field in assignments)
                conn.execute(
                    f"UPDATE model SET {columns} WHERE id IN ({placeholders})",
                    tuple(assignments.values()) + tuple(ids),
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
        # Only while something still declares it, which is what `present`,
        # `unreachable` and `not_downloaded` mean here: a declared engine nothing
        # has fetched is `not_downloaded`, and `declare_folder`'s sweep writes
        # `missing` exactly when the declaration stopped naming the row.
        #
        # `removed` is excluded with it, and by construction rather than by
        # argument (#1439). It satisfies "not missing" while meaning the
        # opposite - the owner deleted that copy on purpose - so left in, an
        # engine every copy of which had been merged away would stay refused
        # forever as "something still declares it". It is unreachable today only
        # because `POST /model-files/merge` refuses an engine outright, which is a
        # gate in another file; this is the class closed here where the predicate
        # is, the way the other five were. That is the DISCOVERED
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
                "AND state NOT IN ('missing', 'removed'))",
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
