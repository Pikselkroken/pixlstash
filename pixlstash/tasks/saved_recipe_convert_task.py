"""Put saved recipes on the workflow their card became (#1623, the cut-over).

A saved recipe is a vault row naming a card (``workflow_key``) and holding
overrides addressed ``<slot label>/<input>`` on that card's topology. After the
hub's conversion (``hub/workflow_group_convert.py``) it runs as a WORKFLOW,
so this fills ``workflow_id`` from ``workflow_key_successor``, rewrites each
override to its workflow address, and pins ``models`` to the card's own
checkpoint, VAE and text encoders: a recipe saved on a checkpoint-B card keeps
running on B instead of on the workflow's default checkpoint.

**Nothing is deleted.** An override that names no node of the workflow is kept
as it was (a run logs it and applies nothing), and a recipe with no successor
row is left untouched and reported back to the finder, which defers it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sqlalchemy import text

from pixlstash.database import DBPriority
from pixlstash.hub.workflow_card_reads import (
    card_index,
    moved_card_workflows,
    workflow_id_successors,
    workflow_index,
    workflow_of_variant,
)
from pixlstash.utils.workflow_ids import MANUAL_PREFIX
from pixlstash.hub.workflow_group_convert import (
    _core_strip_v2,
    _core_strip_v3,
    _core_strip_v4,
    card_document,
    core_label_maps,
    label_map,
    model_pins,
    rewritten_address,
    translate,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.services.workflow_library_service import read_variant_picture_counts
from pixlstash.tasks.base_task import BaseTask, TaskPriority

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)

# A saved recipe still to convert: no workflow yet, a retired one, or a card
# moved out of a workflow that lives on. Binds: :func:`pending_recipe_binds`.
PENDING_RECIPE_WHERE = (
    "(workflow_id IS NULL "
    "OR workflow_id IN (SELECT value FROM json_each(:retired)) "
    "OR workflow_key || ' ' || workflow_id IN (SELECT value FROM json_each(:moved)))"
)


def pending_recipe_binds(successors: dict, moved: dict) -> dict:
    """:data:`PENDING_RECIPE_WHERE`'s binds, from the hub's two successor maps."""
    return {
        "retired": json.dumps(sorted(successors)),
        "moved": json.dumps(sorted(moved)),
    }


class SavedRecipeConvertTask(BaseTask):
    """Convert one batch of saved recipes that have no ``workflow_id`` yet."""

    BATCH_SIZE = 50

    def __init__(self, vault: "Vault", recipe_ids: list[int]):
        """Initialise the task.

        Args:
            vault: The owning Vault, for its database and its hub.
            recipe_ids: The ``saved_recipe`` rows to convert, from the finder.
        """
        super().__init__(
            task_type="SavedRecipeConvertTask",
            params={"recipe_ids": list(recipe_ids)},
        )
        self._vault = vault
        self._recipe_ids = list(recipe_ids)

    @property
    def priority(self) -> TaskPriority:
        """LOW: a recipe keeps running on its card until this reaches it."""
        return TaskPriority.LOW

    def _run_task(self):
        hub = self._vault.hub
        # A workflow retired for another (data step 7: a file-only card's
        # `auto:<topology>` for the manual workflow made of its file) takes a
        # recipe naming it along, so nothing saved changes where it lists.
        successors = workflow_id_successors(hub)
        # A card a family pass moved out of a workflow that lives on (#1689):
        # its recipe follows it, the id it names being live.
        moved = moved_card_workflows(hub)
        placeholders = ",".join(str(int(i)) for i in self._recipe_ids)
        rows = self._vault.db.run_immediate_read_task(
            lambda session: session.execute(
                text(
                    "SELECT id, workflow_key, overrides, models, workflow_id "
                    f"FROM saved_recipe WHERE {PENDING_RECIPE_WHERE} "
                    f"AND id IN ({placeholders})"
                ),
                pending_recipe_binds(successors, moved),
            ).all()
        )
        cards = card_index(hub)
        by_key = {card.workflow_key: card for card in cards}
        # This library's base, the one a run of the recipe starts from, so a
        # stage-node address is kept on the graph it will be applied to.
        bases = {
            w.workflow_id: w.base_topology
            for w in workflow_index(
                hub,
                read_variant_picture_counts(self._vault)
                if getattr(self._vault, "library_uuid", None)
                else None,
                cards,
            )
        }
        updates, deferred = [], []
        for recipe_id, workflow_key, raw_overrides, raw_models, was in rows:
            successor = hub.fetchone(
                "SELECT workflow_id FROM workflow_key_successor WHERE workflow_key = ?",
                (workflow_key,),
            )
            card = by_key.get(workflow_key)
            # A card saved on after the cut-over has no successor row: it is
            # in the workflow its own topology is in.
            workflow_id = (
                was
                if was is not None
                else successor["workflow_id"]
                if successor is not None
                else workflow_of_variant(hub, card.variants[0])
                if card is not None and card.variants
                else None
            )
            workflow_id = successors.get(workflow_id, workflow_id)
            if workflow_id is None:
                logger.warning(
                    "Saved recipe %s names card %s, which became no workflow; it "
                    "is left as it is and runs on that card.",
                    recipe_id,
                    workflow_key,
                )
                deferred.append(recipe_id)
                continue
            found = card_document(hub, card) if card is not None else None
            overrides = _overrides(recipe_id, raw_overrides)
            core_map = _core_successor_map(hub, was)
            # Where the family pass moved its card: recorded, never guessed
            # from a variant, since a card's variants may sit in several
            # workflows. Same core, so nothing to rewrite.
            target = moved.get(f"{workflow_key} {was}")
            if target is not None and core_map is None:
                core_map = {}
            if core_map is not None:
                # Retired by core rule v2 (data step 8): the overrides and
                # models are already workflow addresses, on the v1 core. A
                # split workflow's recipe follows its own card's variant.
                if target is not None:
                    workflow_id = successors.get(target, target)
                elif card is not None and card.variants:
                    workflow_id = (
                        workflow_of_variant(hub, card.variants[0]) or workflow_id
                    )
                if workflow_id == was:
                    # Its card's workflow is the very id it names (a family
                    # the shelf learned, then forgot): nothing to move, and
                    # moving it would repeat every sweep.
                    logger.info(
                        "Saved recipe %s: its card is in workflow %s again; "
                        "left as it is.",
                        recipe_id,
                        was,
                    )
                    deferred.append(recipe_id)
                    continue
                # Keyed by the retired rule's labels: v1 (step 8), v2 (step
                # 10), v3 (step 13) or v4 (step 15). Where two rules give one
                # label, it is the same node's.
                stage_slots = (
                    {
                        **core_label_maps(found[1])[1],
                        **core_label_maps(found[1], _core_strip_v2)[1],
                        **core_label_maps(found[1], _core_strip_v3)[1],
                        **core_label_maps(found[1], _core_strip_v4)[1],
                    }
                    if found and card.topology_hash == bases.get(workflow_id)
                    else {}
                )
                updates.append(
                    _onto_core_v2(
                        recipe_id,
                        was,
                        workflow_key,
                        workflow_id,
                        overrides,
                        raw_overrides,
                        raw_models,
                        core_map,
                        stage_slots,
                    )
                )
                continue
            labels = label_map(found[1]) if found else None
            converted = {}
            for address, value in (overrides or {}).items():
                slot_label, _, input_name = str(address).rpartition("/")
                new = translate(
                    labels,
                    card.topology_hash if card else "",
                    bases.get(workflow_id),
                    slot_label,
                    input_name,
                )
                if new is None:
                    logger.warning(
                        "Saved recipe %s: override %s = %r names no node of "
                        "workflow %s; it is kept as it was and a run applies "
                        "nothing for it.",
                        recipe_id,
                        address,
                        value,
                        workflow_id,
                    )
                converted[new or address] = value
            models = raw_models
            if models is None and card is not None:
                pins = model_pins(hub, card)
                models = json.dumps(pins) if pins else None
            updates.append(
                {
                    "id": recipe_id,
                    "was": was,
                    # A manual workflow is its own card: the recipe runs its
                    # document (`routes/workflows._plan`'s own-card branch).
                    "workflow_key": workflow_id
                    if workflow_id.startswith(MANUAL_PREFIX)
                    else workflow_key,
                    "workflow_id": workflow_id,
                    "overrides": json.dumps(converted)
                    if overrides is not None
                    else raw_overrides,
                    "models": models,
                }
            )
            logger.info(
                "Saved recipe %s moves from card %s to workflow %s (%d override(s), "
                "models %s).",
                recipe_id,
                workflow_key,
                workflow_id,
                len(converted),
                models,
            )

        def write(session):
            for update in updates:
                # `workflow_id IS NULL` again: a recipe the owner re-saved onto
                # a workflow since the read keeps what they chose.
                session.execute(
                    text(
                        "UPDATE saved_recipe SET workflow_id = :workflow_id, "
                        "workflow_key = :workflow_key, overrides = :overrides, "
                        "models = :models WHERE id = :id AND "
                        "(workflow_id IS NULL OR workflow_id = :was)"
                    ),
                    update,
                )
            session.commit()

        if updates:
            self._vault.db.run_task(write, priority=DBPriority.IMMEDIATE)
        return {"converted": len(updates), "deferred": deferred}


def _core_successor_map(hub, was):
    """``{v1 core label: v2 label or None}`` when *was* was retired by data step 8.

    Every topology of the retired workflow shares its v1 core, but v2 may prune
    a node in one and keep it in another, so a kept label wins over ``None``;
    read in topology order, so a recipe on a card the hub no longer holds
    still maps.
    """
    if was is None:
        return None
    rows = hub.fetchall(
        "SELECT label_map FROM workflow_core_successor "
        "WHERE old_workflow_id = ? ORDER BY topology_hash",
        (was,),
    )
    if not rows:
        return None
    labels: dict = {}
    for row in rows:
        for old, new in json.loads(row["label_map"]).items():
            # As data step 8 merges them: a label kept anywhere beats a prune.
            if labels.get(old) is None:
                labels[old] = new
    return labels


def _onto_core_v2(
    recipe_id,
    was,
    workflow_key,
    workflow_id,
    overrides,
    raw_overrides,
    raw_models,
    labels,
    stage_slots,
) -> dict:
    """One recipe's update, its ``core:`` addresses rewritten onto the v2 core.

    **Nothing is dropped**: an address naming a node v2 removed, with no stage
    to hold it, is kept as it was and logged, as a card override is.
    """

    def moved(address: str, what: str, value) -> str:
        new = rewritten_address(address, labels, stage_slots)
        if new is None:
            logger.warning(
                "Saved recipe %s: %s %s = %r names a node the new core rule removed; "
                "it is kept as it was and a run applies nothing for it.",
                recipe_id,
                what,
                address,
                value,
            )
        return new or address

    converted = {
        moved(str(address), "override", value): value
        for address, value in (overrides or {}).items()
    }
    models = raw_models
    try:
        pins = json.loads(raw_models) if raw_models else None
    except json.JSONDecodeError as exc:
        logger.warning(
            "Saved recipe %s has unreadable models, kept as they are: %s",
            recipe_id,
            exc,
        )
        pins = None
    if isinstance(pins, list):
        models = json.dumps(
            [
                dict(pin, address=moved(pin["address"], "model", pin.get("filename")))
                if isinstance(pin, dict) and isinstance(pin.get("address"), str)
                else pin
                for pin in pins
            ]
        )
    logger.info(
        "Saved recipe %s moves from workflow %s to %s under core rule v2.",
        recipe_id,
        was,
        workflow_id,
    )
    return {
        "id": recipe_id,
        "was": was,
        "workflow_key": workflow_key,
        "workflow_id": workflow_id,
        "overrides": json.dumps(converted) if overrides is not None else raw_overrides,
        "models": models,
    }


def _overrides(recipe_id: int, raw: str):
    """The stored overrides as a dict, or ``None`` (kept verbatim) when unreadable."""
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        logger.warning(
            "Saved recipe %s has unreadable overrides, so they are kept as they "
            "are: %s",
            recipe_id,
            exc,
        )
        return None
    return parsed if isinstance(parsed, dict) else None
