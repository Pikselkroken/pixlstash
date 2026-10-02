"""What the owner writes about a WORKFLOW (#1623).

The workflow-level twin of :mod:`pixlstash.hub.workflow_card_writes`: the
owner's name, notes and hidden flag, the edits to a workflow's default recipe,
and its pins and picture inputs. Every table here is keyed by ``workflow_id``
(``auto:<core hash>`` or a manual group's uuid hex) and addresses a parameter
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
from pixlstash.hub import workflow_origin
from pixlstash.hub.workflow_origin import BUILTIN_ORIGIN, FILE_ORIGIN, INBOX_ORIGIN
from pixlstash.pixl_logging import get_logger
from pixlstash.services.workflow_identity import model_fix_kind
from pixlstash.utils.workflow_ids import MANUAL_PREFIX

logger = get_logger(__name__)

# How a LoRA of the default recipe is addressed (``workflow_card_service``
# spells the same prefix; the service imports the hub, not the other way).
LORA_ADDRESS_PREFIX = "lora:"

# Sentinel: this attribute was not in the request, so it stands.
UNSET = object()


def is_parameter_address(address: str) -> bool:
    """Whether *address* names a parameter rather than a model or a LoRA.

    ``PUT /workflows/{id}/defaults`` replaces the parameter rows only: a
    ``lora:`` row and a model loader's row belong to the default recipe's
    LoRAs and models, which the conversion writes and no parameter form shows.
    """
    if address.startswith(LORA_ADDRESS_PREFIX):
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
    record: Optional[tuple[str, str, Optional[str]]] = None,
) -> str:
    """Store *document* as a new manual workflow called *name*; return its id.

    Always a new row, even for a document already stored: identical copies are
    allowed, and deduplicating is the importer's decision, not this one's. The
    row and the name go in one transaction, so a workflow never shows nameless.

    Args:
        origin: How it arrived (``import``, ``inbox``, ``pull``, ``builtin``,
            ``duplicate``, ``fixed``, ``clone``, ``chain``, ``recipe``); the
            table's CHECK refuses anything else.
        from_workflow_id: The workflow (or, for ``recipe``, the recipe's
            workflow) it was made from, and *from_name* what that was called.
        api_document: The API graph an editor *document* converted into.
        record: ``(origin, remote_path, content_hash)`` of the
            ``workflow_origin`` row that says where it came from, written in
            the same transaction: a row stored without it would be stored
            again by the next hand-over.
    """
    workflow_id = f"{MANUAL_PREFIX}{uuid.uuid4().hex}"
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_document (workflow_id, document, api_document, "
            "origin, from_workflow_id, from_name, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                workflow_id,
                json.dumps(document),
                json.dumps(api_document) if api_document is not None else None,
                origin,
                from_workflow_id,
                from_name,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.execute(
            "INSERT INTO workflow_group_attr (workflow_id, name) VALUES (?, ?) "
            "ON CONFLICT(workflow_id) DO UPDATE SET name = excluded.name",
            (workflow_id, name),
        )
        if record is not None:
            record_origin, remote_path, content_hash = record
            workflow_origin.upsert(
                conn, record_origin, remote_path, workflow_id, None, content_hash
            )
    logger.info("Stored manual workflow %s (%s) from %s.", workflow_id, name, origin)
    return workflow_id


def set_manual_api_document(
    hub: HubDatabase, workflow_ids: list[str], api_document: dict
) -> None:
    """Store ComfyUI's API conversion on each named manual workflow."""
    with hub.transaction() as conn:
        conn.executemany(
            "UPDATE workflow_document SET api_document = ? WHERE workflow_id = ?",
            [(json.dumps(api_document), workflow_id) for workflow_id in workflow_ids],
        )


def delete_manual_workflow(hub: HubDatabase, workflow_id: str) -> None:
    """Forget one manual workflow's rows, in one transaction.

    Its inbox and built-in origin rows go, so handing the same content over
    again stores it again; every pull row naming it is **dismissed**, so the
    next pull does not bring it back. Then its document, name, defaults, pins
    and picture inputs. Nothing in any vault is written: its pictures fall
    back to the automatic workflow their graph is in.
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
            "workflow_document",
        ):
            conn.execute(f"DELETE FROM {table} WHERE workflow_id = ?", (workflow_id,))
    logger.info("Deleted manual workflow %s.", workflow_id)
