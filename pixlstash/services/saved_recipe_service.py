"""Saved recipes: the vault side, and the credit that is computed rather than stored.

The rows are :class:`~pixlstash.db_models.saved_recipe.SavedRecipe`; which
workflows a recipe runs on is a hub question, answered by
``hub/workflow_cards.py`` (``effective_stack_keys`` and
``variant_hashes_for_keys``), which the route asks first and hands here as
structural hashes. Nothing in this module crosses the database boundary.

**Credit is a match, not a link** (implementation plan §5.5). A recipe accounts
for the stack's kept pictures whose positive prompt is the recipe's and whose
LoRA names are the recipe's. The seed is ignored, and so are strengths: no
picture row stores one, so strength-exact credit would be a match on a value
that is not there. Names are compared through
:func:`~pixlstash.services.workflow_hash.normalized_filename`, the rule the
whole workflow library already compares filenames by, so a LoRA the graph
loaded as ``characters/Ada.safetensors`` credits a recipe holding
``ada.safetensors``.

**One grouped read per stack, not one query per recipe.** Pictures are grouped
by ``(prompt, loras)`` in SQL and the recipes are matched against the groups in
Python: a stack has a handful of distinct looks and a library has thousands of
pictures, so this is one scan of an indexed ``IN`` however many recipes the tab
holds.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Iterable, Optional

from sqlalchemy import func
from sqlmodel import Session, select

from pixlstash.db_models import Picture, SavedRecipe
from pixlstash.pixl_logging import get_logger
from pixlstash.services.workflow_hash import normalized_filename

logger = get_logger(__name__)


class UnknownSourcePicture(Exception):
    """The caller named a ``source_picture_id`` this library does not hold.

    Raised rather than left to the vault's foreign key, which would surface as
    an unhandled ``IntegrityError`` — a 500 and an ``Unhandled exception`` line
    in the owner's log for what is an ordinary bad request. The route maps it
    to a 422.
    """


# Every field a caller may set, and the only ones PATCH will write. Listed
# rather than derived from the model so that adding a column does not silently
# make it writable through the API.
WRITABLE_FIELDS = (
    "name",
    "prompt",
    "negative",
    "loras",
    "overrides",
    "seed",
    "keep_seed",
    "source_picture_id",
)


def _decode(value: Optional[str], default: Any, *, field: str, where: Any) -> Any:
    """Parse one stored JSON column, or log and fall back to *default*.

    A row is only ever written from validated payloads, so a decode failure
    means the column was edited outside the API or the file is damaged. The
    recipe still lists - losing the whole tab because one row's overrides will
    not parse is the worse answer - but the reason is logged with the column and
    ``where`` (a recipe id, or the picture group being read), because nothing
    else would say which row went bad.
    """
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "Unreadable %s on %s (%r); serving %r instead: %s",
            field,
            where,
            value,
            default,
            exc,
        )
        return default


def lora_key(entries: Iterable[Any]) -> tuple[str, ...]:
    """The comparable form of a LoRA list, from either side of the match.

    A picture's ``comfyui_loras`` holds filenames; a recipe's ``loras`` holds
    ``{"filename", "sha256", "strength"}`` objects. Both reduce to normalized
    basenames, and anything that is neither is dropped rather than compared as
    itself: a half-written entry must not make a recipe credit-match a picture
    it has nothing to do with.

    **Sorted, and duplicates kept**, which is why this is not a set. The picture
    side writes one entry per LoRA loader node
    (``utils/comfyui_utilities.py``), so a graph that loads one file twice - a
    stacked LoRA, and a materially different look - says so, and a recipe that
    stacks the same file twice has to key the same way. Sorted because neither
    side's order is meaningful: it follows node numbering on one side and the
    dialog's rows on the other.
    """
    names = []
    for entry in entries or ():
        if isinstance(entry, str):
            filename = entry
        elif isinstance(entry, dict):
            filename = entry.get("filename")
        else:
            continue
        if isinstance(filename, str) and filename:
            names.append(normalized_filename(filename))
    return tuple(sorted(names))


def prompt_key(value: Optional[str]) -> str:
    """The comparable form of a prompt: stripped, and NULL is the empty one.

    Stripped because the two sides arrive by different routes — a recipe's
    prompt is typed or copied into the Save dialog, a picture's is read out of
    the graph — and a trailing newline is not a different look. Anything
    stronger (case, whitespace inside, punctuation) would start crediting looks
    the owner deliberately told apart, so the rule stops here.
    """
    return (value or "").strip()


def serialize(recipe: SavedRecipe) -> dict:
    """One recipe as the API shape, with its two JSON columns parsed."""
    return {
        "id": recipe.id,
        "name": recipe.name,
        "position": recipe.position,
        "workflow_key": recipe.workflow_key,
        "prompt": recipe.prompt or "",
        "negative": recipe.negative,
        "loras": _decode(
            recipe.loras, [], field="loras", where=f"saved recipe {recipe.id}"
        ),
        "overrides": _decode(
            recipe.overrides,
            {},
            field="overrides",
            where=f"saved recipe {recipe.id}",
        ),
        "seed": recipe.seed,
        "keep_seed": bool(recipe.keep_seed),
        "source_picture_id": recipe.source_picture_id,
        "created_at": recipe.created_at.isoformat() if recipe.created_at else None,
        # Filled in by the route once the hub has said which variants the
        # recipe's stack covers; 0 when the caller asked for no credit.
        "pictures": 0,
    }


# ---------------------------------------------------------------------------
# Session-level reads and writes
# ---------------------------------------------------------------------------


def list_in_session(
    session: Session, workflow_keys: Optional[list[str]] = None
) -> list[dict]:
    """The recipes of these workflows, or every recipe when none are named.

    Ordered by ``position`` then ``id``: the tab lists several workflows'
    recipes together and their positions are only unique within the list the
    owner last ordered, so the id is what keeps the order total and stable.
    """
    statement = select(SavedRecipe)
    if workflow_keys is not None:
        if not workflow_keys:
            return []
        statement = statement.where(SavedRecipe.workflow_key.in_(workflow_keys))
    rows = session.exec(statement.order_by(SavedRecipe.position, SavedRecipe.id)).all()
    return [serialize(row) for row in rows]


def rekey_in_session(session: Session, moved: dict[str, list[str]]) -> int:
    """Move every recipe of a re-keyed card onto the key it went to.

    **This is the migration ``db_models/saved_recipe.py`` says the change that
    makes a re-keying reachable owes this table** (v1.12 B4). A card key is
    content, so it survives a regrouping, an Unstack and a ``CORE_VERSION``
    bump - but not a slot-mark flip, which recomputes it. A recipe left on the
    old key is addressed by a key no variant carries: its workflow's tab stops
    listing it and nothing says where it went, and unlike a hub row it is
    **authored** and cannot be rebuilt from anything.

    Args:
        moved: ``{old key: [new key, ...]}`` from
            :func:`pixlstash.hub.workflow_card_writes.flip_slot_marks`,
            biggest successor first.

    Returns:
        How many recipes moved.

    A split has several successors and a recipe goes to the **first**, which is
    the one holding most of the card's pictures. The row names a workflow and
    not a variant, so which half of a split it was saved from is not recorded
    anywhere and cannot be recovered - sending it to the biggest is a choice,
    and the alternatives (copying it to every successor, or dropping it) are
    both worse: one invents recipes the owner never saved, the other is the
    silent loss this function exists to close.

    **A card that is still there keeps its recipes, whatever its siblings
    did.** ``old_key in successors`` and not ``successors[0] == old_key``: a
    variant whose document will not parse keeps the key it is on, so if a
    sibling moved, that key is in *moved* with a successor that is not itself
    while the card is still open at its own URL. The hub half of the flip
    copies the owner's attributes to the new key and leaves the old key's rows
    where they are; moving the recipes instead emptied the tab of a live card,
    which is this function's own failure mode pointed the other way. It is
    reachable through one Unstack - stacked, both halves share a core hash and
    the list expands across them, so nothing shows.
    """
    moved_count = 0
    for old_key, successors in moved.items():
        if not successors or old_key in successors:
            continue
        rows = session.exec(
            select(SavedRecipe).where(SavedRecipe.workflow_key == old_key)
        ).all()
        for row in rows:
            row.workflow_key = successors[0]
            session.add(row)
        moved_count += len(rows)
    if moved_count:
        session.commit()
    return moved_count


def counts_by_workflow_key(session: Session) -> dict[str, int]:
    """How many saved recipes each card holds, for the whole library at once.

    One ``GROUP BY`` rather than a read per card: the Workflows grid needs the
    number on every card it draws, and asking per card would be an N+1 over the
    one table this module owns. Keys with no recipe are absent rather than
    present with a zero, so the caller reads a missing key as none.

    Served by ``ix_savedrecipe_workflow_key``.
    """
    rows = session.exec(
        select(SavedRecipe.workflow_key, func.count(SavedRecipe.id)).group_by(
            SavedRecipe.workflow_key
        )
    ).all()
    return {workflow_key: count for workflow_key, count in rows}


def credit_groups_in_session(
    session: Session, structural_hashes: list[str]
) -> list[tuple[Optional[str], Optional[str], int]]:
    """Kept pictures of these variants, grouped by what credit matches on.

    **Only pictures that have been read for ComfyUI metadata.** A NULL
    ``comfyui_loras`` is the "never checked" sentinel (``db_models/picture.py``)
    and says nothing about what the picture loaded; counting it would fold every
    un-extracted picture in the stack into the empty prompt with no LoRAs, which
    is exactly the key of a recipe saved with neither, and credit it a library's
    worth of pictures it never made. A picture that was read and loaded no LoRAs
    holds ``"[]"`` and is counted.
    """
    if not structural_hashes:
        return []
    rows = session.exec(
        select(
            Picture.comfyui_positive_prompt,
            Picture.comfyui_loras,
            func.count(Picture.id),
        )
        .where(Picture.workflow_structural_hash.in_(structural_hashes))
        .where(Picture.comfyui_loras.is_not(None))
        .where(Picture.deleted.is_(False))
        .group_by(Picture.comfyui_positive_prompt, Picture.comfyui_loras)
    ).all()
    return [(prompt, loras, count) for prompt, loras, count in rows]


def _require_source_picture(session: Session, picture_id: Optional[int]) -> None:
    """Refuse a ``source_picture_id`` no picture in this vault answers to."""
    if picture_id is None:
        return
    if session.get(Picture, picture_id) is None:
        raise UnknownSourcePicture(
            f"No picture {picture_id} in this library to save a recipe from."
        )


def create_in_session(session: Session, fields: dict) -> dict:
    """Append one recipe, after every recipe the vault already holds.

    The next position is taken over the whole table rather than over the
    workflow's own recipes, because a stack's tab lists several workflows'
    recipes in one order: a per-workflow counter would hand two members the
    same position and leave the id to break a tie the owner did not choose.
    """
    _require_source_picture(session, fields.get("source_picture_id"))
    highest = session.exec(select(func.max(SavedRecipe.position))).one()
    recipe = SavedRecipe(
        workflow_key=fields["workflow_key"],
        name=fields.get("name") or "",
        position=0 if highest is None else highest + 1,
        prompt=fields.get("prompt") or "",
        negative=fields.get("negative"),
        loras=json.dumps(fields.get("loras") or []),
        overrides=json.dumps(fields.get("overrides") or {}),
        seed=fields.get("seed"),
        keep_seed=bool(fields.get("keep_seed")),
        source_picture_id=fields.get("source_picture_id"),
        created_at=datetime.utcnow(),
    )
    session.add(recipe)
    session.commit()
    session.refresh(recipe)
    return serialize(recipe)


def update_in_session(
    session: Session, recipe_id: int, changes: dict
) -> Optional[dict]:
    """Write the named fields of one recipe. ``None`` when there is no such row.

    Only fields the caller actually sent are written, so a PATCH carrying a name
    does not blank a prompt; ``workflow_key`` and ``position`` are not writable
    here, the first because a recipe does not move between workflows and the
    second because ordering is ``PUT /recipes/order``'s whole job.

    Raises:
        UnknownSourcePicture: The change names a picture this library lacks.
    """
    recipe = session.get(SavedRecipe, recipe_id)
    if recipe is None:
        return None
    if "source_picture_id" in changes:
        _require_source_picture(session, changes["source_picture_id"])
    for field in WRITABLE_FIELDS:
        if field not in changes:
            continue
        value = changes[field]
        if field in ("name", "prompt"):
            # NOT NULL columns. A caller sending an explicit null means "clear
            # it", which is the empty string; writing the null itself would be
            # an unhandled IntegrityError out of an ordinary request.
            setattr(recipe, field, value or "")
        elif field == "loras":
            recipe.loras = json.dumps(value or [])
        elif field == "overrides":
            recipe.overrides = json.dumps(value or {})
        elif field == "keep_seed":
            recipe.keep_seed = bool(value)
        else:
            setattr(recipe, field, value)
    session.add(recipe)
    session.commit()
    session.refresh(recipe)
    return serialize(recipe)


def delete_in_session(session: Session, recipe_id: int) -> bool:
    """Delete one recipe. ``False`` when there was no such row."""
    recipe = session.get(SavedRecipe, recipe_id)
    if recipe is None:
        return False
    session.delete(recipe)
    session.commit()
    return True


def reorder_in_session(session: Session, recipe_ids: list[int]) -> Optional[list[int]]:
    """Re-position exactly these recipes into this order.

    **The positions the rows already hold are dealt out again in the new
    order**, rather than 0..n-1 being written over them. A tab shows one stack's
    recipes and reorders that subset, so writing 0..n-1 would drop them on top
    of positions other workflows' recipes already occupy: the unfiltered listing
    would interleave the two, and the next save — which appends after the
    highest position in the table — would land in the middle. Permuting leaves
    every recipe outside the request exactly where it was.

    ``None`` when one of the ids does not exist: a partial reorder would leave
    the tab in an order the owner never chose and no error to say so.

    Two consequences of permuting, both intended. A **subset** of one tab is
    legal and moves only its own rows, so reordering ``[A3, A1]`` out of
    ``A1 A2 A3`` leaves A2 sitting between them: the caller sent two rows and
    two rows moved. And the route is **not workflow-scoped** - a request may
    name recipes of different workflows, which is what a stack's tab does on
    every reorder, since the recipes it lists belong to its several members.
    """
    rows = {
        row.id: row
        for row in session.exec(
            select(SavedRecipe).where(SavedRecipe.id.in_(recipe_ids))
        ).all()
    }
    if len(rows) != len(recipe_ids):
        return None
    positions = sorted(row.position for row in rows.values())
    for position, recipe_id in zip(positions, recipe_ids):
        recipe = rows[recipe_id]
        recipe.position = position
        session.add(recipe)
    session.commit()
    return recipe_ids


# ---------------------------------------------------------------------------
# Credit, over what the two reads above returned
# ---------------------------------------------------------------------------


def credit_by_recipe(
    recipes: list[dict], groups: list[tuple[Optional[str], Optional[str], int]]
) -> dict[int, int]:
    """How many of the stack's kept pictures each recipe accounts for.

    A group whose prompt and LoRA names are a recipe's counts for that recipe,
    and for every other recipe that matches it too: two recipes differing only
    in a LoRA strength are the same look as far as a picture row can tell, and
    silently crediting one of them would be a guess.
    """
    matched: dict[tuple[str, tuple[str, ...]], int] = {}
    for prompt, loras, count in groups:
        # The group is already narrowed to pictures that were read, so "[]"
        # here means a picture that loaded no LoRAs and matches a recipe with
        # none.
        names = lora_key(
            _decode(
                loras,
                [],
                field="comfyui_loras",
                where=f"the picture group with prompt {prompt_key(prompt)[:60]!r}",
            )
        )
        key = (prompt_key(prompt), names)
        matched[key] = matched.get(key, 0) + int(count)

    credit = {}
    for recipe in recipes:
        key = (prompt_key(recipe.get("prompt")), lora_key(recipe.get("loras") or []))
        # ``get``, never ``pop``: a group counts for EVERY recipe it matches.
        # Two recipes differing only in a LoRA strength are the same look as far
        # as a picture row can tell, and crediting whichever was read first
        # would be a guess dressed as an answer.
        credit[recipe["id"]] = matched.get(key, 0)
    return credit


# ---------------------------------------------------------------------------
# The vault-level entry points the route calls, so the route owns no db call.
# ---------------------------------------------------------------------------


def read_recipes(vault, workflow_keys: Optional[list[str]] = None) -> list[dict]:
    """Every saved recipe of these workflows, ordered for the tab."""
    return vault.db.run_immediate_read_task(list_in_session, workflow_keys)


def read_credit_groups(
    vault, structural_hashes: list[str]
) -> list[tuple[Optional[str], Optional[str], int]]:
    """The grouped picture rows :func:`credit_by_recipe` matches against."""
    return vault.db.run_immediate_read_task(credit_groups_in_session, structural_hashes)


def create_recipe(vault, fields: dict) -> dict:
    """Save one recipe."""
    return vault.db.run_task(create_in_session, fields)


def update_recipe(vault, recipe_id: int, changes: dict) -> Optional[dict]:
    """Edit one recipe; ``None`` when it does not exist."""
    return vault.db.run_task(update_in_session, recipe_id, changes)


def delete_recipe(vault, recipe_id: int) -> bool:
    """Forget one recipe; ``False`` when it does not exist."""
    return vault.db.run_task(delete_in_session, recipe_id)


def reorder_recipes(vault, recipe_ids: list[int]) -> Optional[list[int]]:
    """Re-position these recipes; ``None`` when one of them does not exist."""
    return vault.db.run_task(reorder_in_session, recipe_ids)


def rekey_recipes(vault, moved: dict[str, list[str]]) -> int:
    """Follow a card re-keying; returns how many recipes moved.

    **Not in the hub's transaction, and it cannot be**: the recipes are vault
    rows and the cards are hub rows, so this is a second write to a second
    database. A crash between the two leaves recipes on a key no variant
    carries, which is the state this closes rather than one it introduces -
    and the log below is what says so, since nothing else would.
    """
    if not moved:
        return 0
    return vault.db.run_task(rekey_in_session, moved)


def read_in_session(session: Session, recipe_id: int) -> Optional[SavedRecipe]:
    """One saved recipe row by id, or ``None``."""
    return session.get(SavedRecipe, recipe_id)


def read_recipe(vault, recipe_id: int) -> Optional[SavedRecipe]:
    """One saved recipe by id, for a run that starts from it (B7)."""
    return vault.db.run_immediate_read_task(read_in_session, recipe_id)
