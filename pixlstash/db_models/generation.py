"""How a picture was made: the vault half of a recipe.

A picture made by a workflow is three facts at three widths. The **recipe** (the
graph bound to its models) and the **instance** (that recipe with one set of
parameters) are hub tables, because a workflow outlives its pictures and recurs
across libraries (``pixlstash/hub/schema.py``). What is left names a picture and
so can only live here: the **generation**, which is an instance plus a seed, and
its **inputs**, the pictures a run consumed.

**Both die with the picture.** An earlier draft kept a generation after its
picture was gone, so the seed would survive for a remake. What outlives a
destroyed picture is now decided in one place, the picture ghost and the
retention setting the owner chose (``services/workflow_ghost_service.py``), and
a ghost already carries the seed. A second, unconsented survivor here would be
exactly the retained trace that setting exists to control.

**The instance is reached through the picture, not stored again.**
``picture.workflow_instance_hash`` is the join to the hub, and it is the column
the covered-ghost triggers watch. A copy here could only drift from it.
"""

from typing import Optional

from sqlalchemy import Column, ForeignKey, Integer, String
from sqlmodel import Field, SQLModel


class Generation(SQLModel, table=True):
    """One picture's run: the seed its instance was sampled at.

    Written by the workflow scan for every picture whose embedded graph it
    filed. A picture scanned without a hub, or sitting in the Scrapheap when the
    backfill ran, has an instance hash and no row yet.

    Attributes:
        picture_id: The picture, and the key. One picture is one output.
        seed: The first sampler seed in the graph, or NULL when the graph has
            none (an upscale) or the file could not be read on the pass that
            wrote this row. **Text, not an integer:** ComfyUI draws seeds up to
            2**64 - 1 and SQLite's INTEGER stops at 2**63 - 1, so about half
            of real seeds would not fit.
    """

    __tablename__ = "generation"

    picture_id: int = Field(
        sa_column=Column(
            "picture_id",
            Integer,
            ForeignKey("picture.id", ondelete="CASCADE"),
            primary_key=True,
        )
    )
    seed: Optional[str] = Field(
        default=None, sa_column=Column("seed", String, nullable=True)
    )


class GenerationInput(SQLModel, table=True):
    """The resolution lock: which picture one input of a run actually loaded.

    A workflow holds the intent (a picker, a search, a fixed file); this holds
    the fact, so a replay can choose between re-running the query and loading
    exactly what was loaded. **Only a certain answer is written.** A graph's
    ``LoadImage`` names a file in ComfyUI's input folder and a Picture Loader
    names vault ids from whichever library it was built in, so neither
    identifies a picture here, and the backfill writes no rows rather than
    invented lineage. Rows arrive from runs PixlStash submits, where the input
    picture is known.

    Attributes:
        picture_id: The generated picture.
        node_ref: The input node's id in the submitted graph, so a workflow
            with two picture inputs keeps them apart.
        position: Order within that node's pictures.
        pixel_sha: What was loaded. The identity, and it outlives the input.
        input_picture_id: The input picture while it is in this library.
            Nulled, not cascaded, when it goes: deleting a source does not
            unmake what was made from it.
    """

    __tablename__ = "generation_input"

    picture_id: int = Field(
        sa_column=Column(
            "picture_id",
            Integer,
            ForeignKey("generation.picture_id", ondelete="CASCADE"),
            primary_key=True,
        )
    )
    node_ref: str = Field(sa_column=Column("node_ref", String, primary_key=True))
    position: int = Field(sa_column=Column("position", Integer, primary_key=True))
    # Indexed for the reverse question, "what was made from this picture".
    pixel_sha: str = Field(
        sa_column=Column("pixel_sha", String, nullable=False, index=True)
    )
    # Indexed because the SET NULL on a picture delete looks rows up by it.
    input_picture_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            "input_picture_id",
            Integer,
            ForeignKey("picture.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
