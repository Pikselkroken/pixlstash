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
    workflow_index,
    workflow_of_topology,
)
from pixlstash.hub.workflow_group_convert import (
    card_document,
    label_map,
    model_pins,
    translate,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.tasks.base_task import BaseTask, TaskPriority

if TYPE_CHECKING:
    from pixlstash.vault import Vault

logger = get_logger(__name__)


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
        placeholders = ",".join(str(int(i)) for i in self._recipe_ids)
        rows = self._vault.db.run_immediate_read_task(
            lambda session: session.execute(
                text(
                    "SELECT id, workflow_key, overrides, models FROM saved_recipe "
                    f"WHERE workflow_id IS NULL AND id IN ({placeholders})"
                )
            ).all()
        )
        cards = card_index(hub)
        by_key = {card.workflow_key: card for card in cards}
        bases = {
            w.workflow_id: w.base_topology for w in workflow_index(hub, cards=cards)
        }
        updates, deferred = [], []
        for recipe_id, workflow_key, raw_overrides, raw_models in rows:
            successor = hub.fetchone(
                "SELECT workflow_id FROM workflow_key_successor WHERE workflow_key = ?",
                (workflow_key,),
            )
            card = by_key.get(workflow_key)
            # A card saved on after the cut-over has no successor row: it is
            # in the workflow its own topology is in.
            workflow_id = (
                successor["workflow_id"]
                if successor is not None
                else workflow_of_topology(hub, card.topology_hash)
                if card is not None
                else None
            )
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
            labels = label_map(found[1]) if found else None
            overrides = _overrides(recipe_id, raw_overrides)
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
                        "overrides = :overrides, models = :models "
                        "WHERE id = :id AND workflow_id IS NULL"
                    ),
                    update,
                )
            session.commit()

        if updates:
            self._vault.db.run_task(write, priority=DBPriority.IMMEDIATE)
        return {"converted": len(updates), "deferred": deferred}


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
