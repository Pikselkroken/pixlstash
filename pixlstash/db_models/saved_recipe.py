"""A look the owner kept: prompt, LoRAs and overrides, saved from a workflow.

**"Recipe" means two things here, and this is the user's one.** In the hub,
``workflow_recipe`` / ``structural_hash`` is a graph bound to model filenames -
new code calls that a **variant**. A *saved recipe* is what somebody pressed
Save on: the prompt, the recipe LoRAs with their strengths, the parameters they
changed. See the glossary line in ``docs/backend_architecture.md``.

**In the vault, not the hub** (implementation plan §5.5 / §9.3). The hub's
workflow rows are derived - re-file the pictures and they come back - and are
shared by every library on the machine. A saved recipe is authored, holds the
owner's prompt, cannot be rebuilt from anything, and must travel with a
snapshot or a library move. That is a vault row.

**It belongs to the workflow it was saved from** (decision D10), by
``workflow_key``: a card key, which is content, so the recipe survives a
regrouping, an Unstack and a ``CORE_VERSION`` bump. The stack's Recipes tab
lists every member's recipes and each one stays with its own workflow when the
stack is broken up; nothing here names a stack.

**Credit is computed on read and there is no link table.** Which pictures a
recipe accounts for is a question about prompts and LoRA names, which the
picture rows already answer (``services/saved_recipe_service.py``). A stored
link would be a second copy of that answer, wrong the moment a picture is
re-scanned or binned.
"""

from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlmodel import Field, SQLModel


class SavedRecipe(SQLModel, table=True):
    """One saved recipe: a named look that runs on a workflow's stack.

    Attributes:
        id: The row, and what the routes address it by.
        name: What the owner called it. Not unique: two workflows may both
            have a "portrait, cold light".
        position: Order within the Recipes tab, ascending, ties broken by id.
            Written by ``PUT /recipes/order``; a new recipe is appended.
        workflow_key: The card it was saved from
            (``services/workflow_identity.py``). Text, not a foreign key: the
            row it names lives in the hub, which is a different database and
            content-addressed, so a key this machine's hub has never derived is
            a workflow it does not have rather than an error. Indexed because
            every read starts from it.
        prompt: The positive prompt, and half of what credit matches on.
        negative: The negative prompt, which nothing matches on: a picture's
            negative is not extracted today.
        loras: JSON ``[{"filename", "sha256", "strength"}]``. The digest is
            what makes a LoRA findable again after a rename; the strength is
            the look, and is deliberately ignored by credit because no picture
            row stores one.
        overrides: JSON ``{parameter address: value}``, addressed by slot label
            and input name like ``workflow_default_override`` in the hub -
            never by node id, which is whatever the file that was serialised
            last happened to call it.
        seed: The seed to reuse, as text for ``Generation.seed``'s reason:
            ComfyUI draws seeds up to 2**64 - 1 and SQLite's INTEGER stops at
            2**63 - 1. NULL means the run draws a new one.
        keep_seed: Whether a run reuses ``seed`` rather than drawing. A flag of
            its own because "no seed kept" and "a seed kept that happens to be
            0" are different states.
        source_picture_id: The picture the recipe was saved from, while it is
            in this library. Nulled rather than cascaded when that picture
            goes: deleting the picture does not unmake the look it taught.
        created_at: When it was saved.
    """

    __tablename__ = "saved_recipe"

    id: Optional[int] = Field(default=None, primary_key=True)

    name: str = Field(default="")
    position: int = Field(default=0)
    workflow_key: str = Field(index=True)

    prompt: str = Field(default="", sa_column=sa.Column(sa.Text(), nullable=False))
    negative: Optional[str] = Field(
        default=None, sa_column=sa.Column(sa.Text(), nullable=True)
    )
    loras: str = Field(default="[]", sa_column=sa.Column(sa.Text(), nullable=False))
    overrides: str = Field(default="{}", sa_column=sa.Column(sa.Text(), nullable=False))

    seed: Optional[str] = Field(default=None)
    keep_seed: bool = Field(default=False)

    # Indexed because the SET NULL on a picture delete looks rows up by it.
    source_picture_id: Optional[int] = Field(
        default=None,
        sa_column=sa.Column(
            "source_picture_id",
            sa.Integer,
            sa.ForeignKey("picture.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )

    created_at: Optional[datetime] = Field(default_factory=datetime.utcnow)
