"""What the owner writes about a WORKFLOW (#1623).

The workflow-level twin of :mod:`pixlstash.hub.workflow_card_writes`: the
owner's name, notes and hidden flag, the edits to a workflow's default recipe,
and its pins and picture inputs. Every table here is keyed by ``workflow_id``
(``auto:<core and families digest>`` or a manual group's uuid hex) and addresses a parameter
by its **address** (``<slot label>/<input name>``, the slot label a
``core:<label>`` or a base-topology label), never by node id.

**Nothing here decides identity.** Which topologies a workflow holds is read in
:mod:`pixlstash.hub.workflow_card_reads`; the caller resolves the ids and this
module writes rows, one transaction per logical write.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from pixlstash.hub.db import HubDatabase
from pixlstash.hub import workflow_origin, workflow_versions
from pixlstash.hub.workflow_origin import BUILTIN_ORIGIN, FILE_ORIGIN, INBOX_ORIGIN
from pixlstash.pixl_logging import get_logger
from pixlstash.services import workflow_bindings
from pixlstash.services.workflow_identity import model_fix_kind
from pixlstash.utils.workflow_ids import MANUAL_PREFIX, stamp_workflow_id

logger = get_logger(__name__)

# How a LoRA of the default recipe is addressed (``workflow_card_service``
# spells the same prefix; the service imports the hub, not the other way).
LORA_ADDRESS_PREFIX = "lora:"

# How a stage's default is addressed: ``stage:upscale`` holding ``on`` or
# ``off``. The owner's own answer to "does this workflow run its upscale",
# over the vote of its pictures (``workflow_card_service.workflow_defaults``).
STAGE_ADDRESS_PREFIX = "stage:"
STAGE_ON, STAGE_OFF = "on", "off"

# The two sets of changes a workflow holds (``workflow_change.kind``): what
# waits in the Workflow tab, and what its last run was made with.
WAITING, RAN = "waiting", "run"

# Sentinel: this attribute was not in the request, so it stands.
UNSET = object()


def is_parameter_address(address: str) -> bool:
    """Whether *address* names a parameter rather than a model or a LoRA.

    ``PUT /workflows/{id}/defaults`` replaces the parameter rows only: a
    ``lora:`` row and a model loader's row belong to the default recipe's
    LoRAs and models, which the conversion writes and no parameter form shows.
    """
    if address.startswith((LORA_ADDRESS_PREFIX, STAGE_ADDRESS_PREFIX)):
        return False
    return not model_fix_kind("", address.rpartition("/")[2])


def set_group_attributes(
    hub: HubDatabase, workflow_id: str, name=UNSET, notes=UNSET, hidden=UNSET
) -> None:
    """Write the attributes the request carried; the rest stand.

    ``None`` for *name* or *notes* clears it, which is not the same as leaving
    the field out.
    """
    given = {
        column: (int(bool(value)) if column == "hidden" else value)
        for column, value in (("name", name), ("notes", notes), ("hidden", hidden))
        if value is not UNSET
    }
    if not given:
        return
    columns = ", ".join(given)
    placeholders = ", ".join("?" for _ in given)
    updates = ", ".join(f"{column} = excluded.{column}" for column in given)
    with hub.transaction() as conn:
        conn.execute(
            f"INSERT INTO workflow_group_attr (workflow_id, {columns}) "
            f"VALUES (?, {placeholders}) "
            f"ON CONFLICT(workflow_id) DO UPDATE SET {updates}",
            (workflow_id, *given.values()),
        )


def replace_parameter_defaults(
    hub: HubDatabase, workflow_id: str, defaults: list[tuple[str, str]]
) -> None:
    """Replace a workflow's PARAMETER edits whole: ``[(address, value)]``.

    Rows naming a model or a LoRA (:func:`is_parameter_address`) are left
    alone, so clearing the parameter form never takes out a default model.
    """
    with hub.transaction() as conn:
        stale = [
            (workflow_id, address)
            for (address,) in conn.execute(
                "SELECT address FROM workflow_group_default WHERE workflow_id = ?",
                (workflow_id,),
            ).fetchall()
            if is_parameter_address(address)
        ]
        conn.executemany(
            "DELETE FROM workflow_group_default WHERE workflow_id = ? AND address = ?",
            stale,
        )
        conn.executemany(
            "INSERT INTO workflow_group_default (workflow_id, address, value) "
            "VALUES (?, ?, ?)",
            [(workflow_id, address, value) for address, value in defaults],
        )


def set_default_lora(
    hub: HubDatabase, workflow_id: str, sha256: str, value: Optional[str]
) -> None:
    """Put one LoRA's edit in the default recipe, or take the edit out.

    ``value`` is its strength as text, ``"off"`` to keep it out of the default
    recipe, or ``None`` to drop the edit so the pictures decide again. The row
    is ``lora:<sha256>``; every other default row stands.
    """
    address = LORA_ADDRESS_PREFIX + sha256.lower()
    with hub.transaction() as conn:
        if value is None:
            conn.execute(
                "DELETE FROM workflow_group_default WHERE workflow_id = ? AND address = ?",
                (workflow_id, address),
            )
            return
        conn.execute(
            "INSERT INTO workflow_group_default (workflow_id, address, value) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(workflow_id, address) DO UPDATE SET value = excluded.value",
            (workflow_id, address, value),
        )


def write_saved_defaults(
    conn,
    workflow_id: str,
    superseded: list[str],
    defaults: list[tuple[str, str]],
) -> None:
    """What a Save does to the default rows, inside the caller's transaction.

    *superseded* are addresses whose row goes: a model or a LoRA the saved
    graph now carries itself, so an older edit of the default no longer
    speaks over it. *defaults* are ``[(address, value)]`` written over what
    is there: each stage's ``on`` or ``off``, and any parameter the save
    carried.
    """
    conn.executemany(
        "DELETE FROM workflow_group_default WHERE workflow_id = ? AND address = ?",
        [(workflow_id, address) for address in superseded],
    )
    conn.executemany(
        "INSERT INTO workflow_group_default (workflow_id, address, value) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT(workflow_id, address) DO UPDATE SET value = excluded.value",
        [(workflow_id, address, value) for address, value in defaults],
    )


def read_changes(
    hub: HubDatabase, workflow_id: str
) -> dict[str, tuple[dict, Optional[int]]]:
    """``{kind: (changes, base_version)}`` of one workflow: :data:`WAITING`
    and :data:`RAN`, each absent when there is none.

    A row that will not parse is logged and left out, so one bad row never
    stops the workflow from being read or saved.
    """
    found: dict[str, tuple[dict, Optional[int]]] = {}
    for row in hub.fetchall(
        "SELECT kind, changes, base_version FROM workflow_change WHERE workflow_id = ?",
        (workflow_id,),
    ):
        try:
            changes = json.loads(row["changes"])
        except ValueError as exc:
            logger.warning(
                "Workflow %s: its %s changes will not read, so they are left out: %s",
                workflow_id,
                row["kind"],
                exc,
            )
            continue
        if isinstance(changes, dict):
            found[row["kind"]] = (changes, row["base_version"])
    return found


def write_changes(
    hub: HubDatabase,
    workflow_id: str,
    kind: str,
    changes: Optional[dict],
    base_version: Optional[int] = None,
) -> None:
    """Replace one kind of a workflow's changes, or drop it with ``None``.

    *base_version* is the workflow's version the changes were made against.
    A waiting set that is replaced keeps the version it was started on:
    editing it further does not make a stale set current. A run's set is the
    run's, so each run writes its own.
    """
    rebase = ", base_version = excluded.base_version" if kind == RAN else ""
    with hub.transaction() as conn:
        if changes is None:
            conn.execute(
                "DELETE FROM workflow_change WHERE workflow_id = ? AND kind = ?",
                (workflow_id, kind),
            )
            return
        conn.execute(
            "INSERT INTO workflow_change (workflow_id, kind, changes, "
            "base_version, updated_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(workflow_id, kind) DO UPDATE SET "
            "changes = excluded.changes, updated_at = excluded.updated_at" + rebase,
            (
                workflow_id,
                kind,
                json.dumps(changes),
                base_version,
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def clear_changes(conn, workflow_id: str) -> None:
    """Drop everything waiting on a workflow, inside the caller's transaction:
    a save has made it part of the graph, or the owner discarded it."""
    conn.execute("DELETE FROM workflow_change WHERE workflow_id = ?", (workflow_id,))


def waiting_counts(hub: HubDatabase) -> dict[str, dict]:
    """``{workflow id: its waiting changes}`` for every workflow that has any:
    what the grid marks a card *Unsaved* from, read in one statement."""
    found = {}
    for row in hub.fetchall(
        "SELECT workflow_id, changes FROM workflow_change WHERE kind = ?", (WAITING,)
    ):
        try:
            changes = json.loads(row["changes"])
        except ValueError as exc:
            logger.warning(
                "Workflow %s: its waiting changes will not read: %s",
                row["workflow_id"],
                exc,
            )
            continue
        if isinstance(changes, dict):
            found[row["workflow_id"]] = changes
    return found


def replace_group_pins(
    hub: HubDatabase, workflow_id: str, pins: Optional[list[str]]
) -> None:
    """Store a workflow's pins whole, as addresses, or forget them with ``None``.

    ``[]`` is somebody who unpinned everything and is kept as a row.
    """
    with hub.transaction() as conn:
        if pins is None:
            conn.execute(
                "DELETE FROM workflow_group_pins WHERE workflow_id = ?", (workflow_id,)
            )
            return
        conn.execute(
            "INSERT INTO workflow_group_pins (workflow_id, pins) VALUES (?, ?) "
            "ON CONFLICT(workflow_id) DO UPDATE SET pins = excluded.pins",
            (workflow_id, json.dumps(list(pins))),
        )


def replace_group_picture_inputs(
    hub: HubDatabase,
    library_uuid: str,
    workflow_id: str,
    inputs: list[tuple[str, str, Optional[str]]],
) -> None:
    """Set how a workflow's picture inputs are filled in ONE library.

    ``inputs`` is ``[(address, mode, pixel_sha)]``; library-keyed because a
    ``fixed`` row names a picture by ``pixel_sha`` in one vault.
    """
    with hub.transaction() as conn:
        conn.execute(
            "DELETE FROM workflow_group_picture_input "
            "WHERE library_uuid = ? AND workflow_id = ?",
            (library_uuid, workflow_id),
        )
        conn.executemany(
            "INSERT INTO workflow_group_picture_input "
            "(library_uuid, workflow_id, address, mode, pixel_sha) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (library_uuid, workflow_id, address, mode, pixel_sha)
                for address, mode, pixel_sha in inputs
            ],
        )


def create_manual_workflow(
    hub: HubDatabase,
    name: str,
    document: dict,
    origin: str,
    from_workflow_id: Optional[str] = None,
    from_name: Optional[str] = None,
    api_document: Optional[dict] = None,
    record: Optional[tuple[str, str, Optional[int], Optional[str]]] = None,
) -> str:
    """Store *document* as a new manual workflow called *name*; return its id.

    Always a new row, even for a document already stored: identical copies are
    allowed, and deduplicating is the importer's decision, not this one's. The
    row and the name go in one transaction, so a workflow never shows nameless.

    Args:
        origin: How it arrived (``import``, ``inbox``, ``builtin``,
            ``duplicate``, ``fixed``, ``clone``, ``chain``, ``recipe``); the
            table's CHECK refuses anything else. ``pull`` is allowed and no
            longer written: rows the removed pull of ComfyUI's saved
            workflows made keep it.
        from_workflow_id: The workflow (or, for ``recipe``, the recipe's
            workflow) it was made from, and *from_name* what that was called.
        api_document: The API graph an editor *document* converted into.
        record: ``(origin, remote_path, remote_modified, content_hash)`` of the
            ``workflow_origin`` row that says where it came from, written in
            the same transaction: a row stored without it would be stored
            again by the next hand-over.

    An editor-format *document* is stored tagged with the new id
    (``extra.pixlstash_workflow_id``), replacing any tag it came with, so a
    run of it started in ComfyUI files its pictures here.
    """
    workflow_id = f"{MANUAL_PREFIX}{uuid.uuid4().hex}"
    stored = json.dumps(stamp_workflow_id(document, workflow_id))
    stored_api = json.dumps(api_document) if api_document is not None else None
    created_at = datetime.now(timezone.utc).isoformat()
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_document (workflow_id, document, api_document, "
            "origin, from_workflow_id, from_name, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                workflow_id,
                stored,
                stored_api,
                origin,
                from_workflow_id,
                from_name,
                created_at,
            ),
        )
        workflow_versions.insert_version(
            conn,
            workflow_id,
            1,
            stored,
            stored_api,
            origin,
            content_hash=record[3] if record is not None else None,
            remote_modified=record[2] if record is not None else None,
            created_at=created_at,
        )
        conn.execute(
            "INSERT INTO workflow_group_attr (workflow_id, name) VALUES (?, ?) "
            "ON CONFLICT(workflow_id) DO UPDATE SET name = excluded.name",
            (workflow_id, name),
        )
        if record is not None:
            record_origin, remote_path, remote_modified, content_hash = record
            workflow_origin.upsert(
                conn,
                record_origin,
                remote_path,
                workflow_id,
                remote_modified,
                content_hash,
            )
    logger.info("Stored manual workflow %s (%s) from %s.", workflow_id, name, origin)
    return workflow_id


def set_manual_api_document(
    hub: HubDatabase,
    workflow_ids: list[str],
    api_document: dict,
    canonical: Optional[str] = None,
    *,
    keep_stored: bool = False,
) -> list[str]:
    """Store an API conversion on each named manual workflow.

    On ``workflow_document`` and on the current version, which is the one it
    converts. With *canonical* (``workflow_bindings.canonical`` of the document
    that was converted), a workflow whose current document no longer is that
    one - a save made a new version since it was matched - is left alone, read
    and written in one write transaction, so the conversion of one version
    never lands on the next. Returns the ids it was stored on.

    *keep_stored* is PixlStash's own conversion
    (``workflow_card_service.converted_manual_document``): it fills only a
    version with no conversion yet, so ComfyUI's (the node's *Convert for
    PixlStash*, stored without it) always wins, whichever arrived first.
    """
    stored = json.dumps(api_document)
    unset = " AND api_document IS NULL" if keep_stored else ""
    with hub.transaction() as conn:
        written = []
        for workflow_id in workflow_ids:
            if canonical is not None:
                row = conn.execute(
                    "SELECT document FROM workflow_document WHERE workflow_id = ?",
                    (workflow_id,),
                ).fetchone()
                if row is None or not _holds(row[0], canonical):
                    logger.info(
                        "Manual workflow %s changed since it matched a "
                        "conversion; the conversion is not stored on it.",
                        workflow_id,
                    )
                    continue
            if not conn.execute(
                "UPDATE workflow_document SET api_document = ? WHERE workflow_id = ?"
                + unset,
                (stored, workflow_id),
            ).rowcount:
                continue
            # And on the version it converts: the current one.
            conn.execute(
                "UPDATE workflow_version SET api_document = ? WHERE workflow_id = ? "
                "AND version = (SELECT MAX(version) FROM workflow_version "
                "WHERE workflow_id = ?)" + unset,
                (stored, workflow_id, workflow_id),
            )
            written.append(workflow_id)
    return written


def _holds(document: str, canonical: str) -> bool:
    """Whether the stored *document* is *canonical* (logged when it will not read)."""
    try:
        return workflow_bindings.canonical(json.loads(document)) == canonical
    except (ValueError, RecursionError, AttributeError) as exc:
        logger.warning("A stored manual document will not read to compare: %s", exc)
        return False


def delete_manual_workflow(hub: HubDatabase, workflow_id: str) -> None:
    """Forget one manual workflow's rows, in one transaction.

    Its inbox, built-in and file origin rows go, so handing the same content
    over again stores it again. A row the removed pull of ComfyUI's saved
    workflows left (#1854) is marked **dismissed** instead: this build reads
    past it, and an older build sharing the hub, which still pulls, does not
    bring the workflow back. Then its document, name, defaults, pins and
    picture inputs. Nothing in any vault is written: its pictures fall back to
    the automatic workflow their graph is in.
    """
    with hub.transaction() as conn:
        conn.execute(
            "DELETE FROM workflow_origin WHERE workflow_name = ? "
            "AND origin IN (?, ?, ?)",
            (workflow_id, INBOX_ORIGIN, BUILTIN_ORIGIN, FILE_ORIGIN),
        )
        conn.execute(
            "UPDATE workflow_origin SET dismissed = 1 WHERE workflow_name = ?",
            (workflow_id,),
        )
        for table in (
            "workflow_group_default",
            "workflow_group_pins",
            "workflow_group_picture_input",
            "workflow_group_attr",
            "workflow_change",
            "workflow_version",
            "workflow_document",
        ):
            conn.execute(f"DELETE FROM {table} WHERE workflow_id = ?", (workflow_id,))
    logger.info("Deleted manual workflow %s.", workflow_id)
