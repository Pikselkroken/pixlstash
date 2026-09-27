"""What the owner writes about a WORKFLOW (#1623).

The workflow-level twin of :mod:`pixlstash.hub.workflow_card_writes`: the
owner's name, notes and hidden flag, the edits to a workflow's default recipe,
its pins and picture inputs, and the two gestures that move topologies between
workflows, merge and split. Every table here is keyed by ``workflow_id``
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
from typing import Optional

from pixlstash.hub.db import HubDatabase
from pixlstash.hub.workflow_card_reads import AUTO_STACK_PREFIX
from pixlstash.pixl_logging import get_logger
from pixlstash.services.workflow_identity import model_fix_kind

logger = get_logger(__name__)

# How a LoRA of the default recipe is addressed (``workflow_card_service``
# spells the same prefix; the service imports the hub, not the other way).
LORA_ADDRESS_PREFIX = "lora:"

# The owner's own tables, all keyed by ``workflow_id``. What a merge moves from
# its cover, and what an emptied workflow loses.
_GROUP_TABLES = (
    "workflow_group_attr",
    "workflow_group_default",
    "workflow_group_pins",
    "workflow_group_picture_input",
)

# Sentinel: this attribute was not in the request, so it stands.
UNSET = object()

# How the notes of a merge name the workflows it folded in (#1620 D4).
ALSO_NAMED = "Also named: "


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


def merge_workflows(
    hub: HubDatabase, ids: list[str], topologies: dict[str, list[str]]
) -> str:
    """Put every topology of the named workflows in one; return its id.

    ``ids[0]`` is the cover: its name, notes, hidden flag, defaults, pins and
    picture inputs are what the merged workflow has. Every other distinct name
    is appended to the notes as ``Also named: …`` (#1620 D4) and nothing else
    of the others is kept. A manual cover keeps its id; an automatic one is
    replaced by a new manual group, since ``auto:<core hash>`` names only the
    topologies sharing that hash.

    Args:
        ids: The workflows, cover first, validated and distinct.
        topologies: ``{workflow_id: [topology_hash]}`` for each of *ids*.
    """
    cover = ids[0]
    target = cover if not cover.startswith(AUTO_STACK_PREFIX) else uuid.uuid4().hex
    with hub.transaction() as conn:
        attrs = {
            row[0]: row
            for row in conn.execute(
                "SELECT workflow_id, name, notes, hidden FROM workflow_group_attr "
                f"WHERE workflow_id IN ({','.join('?' * len(ids))})",
                tuple(ids),
            ).fetchall()
        }
        cover_attr = attrs.get(cover)
        cover_name = cover_attr[1] if cover_attr else None
        others = list(
            dict.fromkeys(
                attrs[other][1]
                for other in ids[1:]
                if other in attrs and attrs[other][1] and attrs[other][1] != cover_name
            )
        )
        conn.execute(
            "INSERT INTO workflow_group (workflow_id, kind, core_hash) "
            "VALUES (?, 'manual', NULL) ON CONFLICT(workflow_id) DO NOTHING",
            (target,),
        )
        if target != cover:
            for table in _GROUP_TABLES:
                _copy_rows(conn, table, cover, target)
        conn.executemany(
            "INSERT INTO workflow_group_member (topology_hash, workflow_id) "
            "VALUES (?, ?) ON CONFLICT(topology_hash) "
            "DO UPDATE SET workflow_id = excluded.workflow_id",
            [
                (topology_hash, target)
                for workflow_id in ids
                for topology_hash in topologies.get(workflow_id, ())
            ],
        )
        for emptied in ids:
            if emptied == target:
                continue
            for table in _GROUP_TABLES:
                conn.execute(f"DELETE FROM {table} WHERE workflow_id = ?", (emptied,))
            conn.execute(
                "DELETE FROM workflow_group WHERE workflow_id = ? AND NOT EXISTS "
                "(SELECT 1 FROM workflow_group_member WHERE workflow_id = ?)",
                (emptied, emptied),
            )
        if others:
            notes = cover_attr[2] if cover_attr else None
            addition = ALSO_NAMED + ", ".join(others)
            conn.execute(
                "INSERT INTO workflow_group_attr (workflow_id, notes) VALUES (?, ?) "
                "ON CONFLICT(workflow_id) DO UPDATE SET notes = excluded.notes",
                (target, f"{notes}\n\n{addition}" if notes else addition),
            )
    logger.info(
        "Merged workflows %s into %s (%d topologies).",
        ", ".join(ids),
        target,
        sum(len(topologies.get(workflow_id, ())) for workflow_id in ids),
    )
    return target


def split_topology(hub: HubDatabase, workflow_id: str, topology_hash: str) -> str:
    """Take one topology out of *workflow_id* into a new manual group; its id.

    The workflow it leaves keeps its name, notes and settings; the new one
    starts with none of its own.
    """
    new_id = uuid.uuid4().hex
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO workflow_group (workflow_id, kind, core_hash) "
            "VALUES (?, 'manual', NULL)",
            (new_id,),
        )
        conn.execute(
            "INSERT INTO workflow_group_member (topology_hash, workflow_id) "
            "VALUES (?, ?) ON CONFLICT(topology_hash) "
            "DO UPDATE SET workflow_id = excluded.workflow_id",
            (topology_hash, new_id),
        )
    logger.info(
        "Split topology %s out of workflow %s into %s.",
        topology_hash,
        workflow_id,
        new_id,
    )
    return new_id


def _copy_rows(conn, table: str, source: str, target: str) -> None:
    """Copy every row of *table* keyed on *source* onto *target*."""
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    selected = ", ".join(
        "?" if column == "workflow_id" else column for column in columns
    )
    conn.execute(
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"SELECT {selected} FROM {table} WHERE workflow_id = ?",
        (target, source),
    )
