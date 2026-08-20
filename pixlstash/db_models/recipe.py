"""Provenance for generated images: recipe, instance, generation (v1.11 Phase 2).

Three levels, because "how did I make this?" has three different answers and
collapsing them loses the one people actually ask for:

* :class:`Recipe` is the **topology**: the graph and the assets it loads. Two
  images share a recipe when they came from the same workflow wired the same
  way, whatever the prompt or the seed. This is the level "show me everything I
  made with this LoRA" reads.
* :class:`RecipeInstance` is the **intent**: prompt and scalar parameters on top
  of a recipe. Editing a prompt makes a new instance, not a new recipe.
* :class:`Generation` is the **event**: one image, one seed. A re-roll is a new
  generation of the same instance.

:class:`RecipeAsset` names the files a recipe loads, by sha256 where it is
known and by filename where it is not, so an image can gain full lineage later
when the LoRA it names is finally registered (the retro-resolve pass).
:class:`GenerationInput` is the **resolution lock** for query-based nodes: the
PixlStash search and browse nodes pick images from vault state at run time, so
the workflow records the *intent* and this table records the *fact* of which
images were actually consumed.

Which bucket every ComfyUI node input falls into is specified verbatim in
``pixlstash-hash-field-classification.md`` (business repo). The hashes
themselves are computed in the canonicalizer, not here; this module is only
where the results land.

**These are vault tables, not hub tables.** They reference pictures, and a
picture belongs to one library. The model shelf they resolve *against* is
hub-side, which is why the resolved columns hold a sha256 and never an integer
model id: no foreign key can span the two databases, and SQLite hands a deleted
row's id to the next insert, so an integer would silently re-point at a
different model. Same rule, same reason, as
:mod:`pixlstash.db_models.adapter_attachment`.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Float, ForeignKey, Index, Integer, String
from sqlmodel import Field, SQLModel

# ``Recipe.engine`` values. What produced the document, which decides how it is
# parsed and replayed.
ENGINE_COMFYUI = "comfyui"
ENGINE_AI_TOOLKIT = "ai-toolkit"

# ``Recipe.hash_version`` / ``RecipeInstance.hash_version`` values.
#
# ``v1`` is the canonicalization in the field-classification spec, including the
# deterministic node-id relabelling that lets a workflow rebuilt from scratch
# hash identically to the original. ``v1-raw`` is the same rules with raw node
# ids, recorded when relabelling could not run (a graph with a cycle through a
# reroute edge case). It is a separate value rather than a flag so that the
# difference is visible in every query instead of silently grouping two
# incomparable hashes together.
HASH_VERSION_V1 = "v1"
HASH_VERSION_V1_RAW = "v1-raw"

# ``RecipeAsset.asset_type`` values. ``unknown_model`` is what the unknown-node
# default rule produces from a widget whose value merely ends in a model
# extension: it is a real, expected state, not a parse failure.
ASSET_LORA = "lora"
ASSET_CHECKPOINT = "checkpoint"
ASSET_VAE = "vae"
ASSET_CLIP = "clip"
ASSET_CONTROLNET = "controlnet"
ASSET_UPSCALE_MODEL = "upscale_model"
ASSET_EMBEDDING = "embedding"
ASSET_IMAGE = "image"
ASSET_SNAPSHOT = "snapshot"
ASSET_UNKNOWN_MODEL = "unknown_model"


class Recipe(SQLModel, table=True):
    """One workflow topology, identified by its structural hash.

    **Three hashes, one walk of the graph, three different questions.** They are
    nested, from strictest to loosest, and all three are exact: there is no
    similarity threshold anywhere in this design.

    ==================  =========================================  ============
    Column              Answers                                    Measured
    ==================  =========================================  ============
    ``structural_hash`` "can I replay this exactly?"               281 recipes
    ``topology_hash``   "same graph, different checkpoint?"        192 groups
    ``role_hash``       "same *kind* of workflow?"                 93 groups
    ==================  =========================================  ============

    (Counts are from a 13,463-image library; they are here to show the ratios
    between the levels, not as anything the code depends on.)

    ``structural_hash`` stays the identity and is never merged, which is what
    lets the looser two be as aggressive as they like: the Workflows view groups
    93 ways, and "which images used this LoRA" still resolves against the strict
    hash underneath. A group can always be split later; a merged identity cannot
    be recovered.

    ``role_hash`` is the one that makes the Workflows view browsable, and the
    trick in it is worth stating: **a node's identity comes from what it feeds
    into, not from what it is called.** The API-format JSON carries no type
    names, but the input name a node is wired to is ComfyUI's type discipline
    showing through, so anything wired to ``model`` is a model source and
    anything wired to ``clip`` is a text encoder. Node versions and pack
    variants therefore merge with no synonym table to maintain, which matters
    because a hand-kept table over a hundred-odd node classes goes stale every
    time a pack updates. Measured against the alternative: name-based families
    caught 2 of ~120 classes, while the role rule merged ``CLIPLoader`` with
    ``CLIPLoaderGGUF``, ``UNETLoader`` with ``UNETLoaderDisTorch2MultiGPU``, and
    ``EmptyLatentImage`` with ``EmptySD3LatentImage`` on its own.

    Attributes:
        engine: :data:`ENGINE_COMFYUI` or :data:`ENGINE_AI_TOOLKIT`.
        engine_version: Whatever the producer declared, free text. Environment
            drift is real and reproduction is best-effort equivalent rather
            than bit-exact, so this is evidence for a human, not a key.
        document: The API-format workflow JSON, as text. Scrubbed of any widget
            whose name looks like a credential before it is stored.
        document_ui: The UI-format JSON when the image carried one. Useful for
            showing the graph the way its author drew it; never hashed, because
            node positions and titles are cosmetic.
        structural_hash: Topology plus asset identity, every parameter nulled.
            The identity of the row.
        topology_hash: The same, with asset filenames dropped, so a graph run
            against two checkpoints is one entry with two variants.
        role_hash: The sorted *set* of node roles. Deliberately a set and not a
            multiset: dropping multiplicity is what puts a one-LoRA and a
            four-LoRA version of a graph in one group, which is the aggressive
            behaviour the view wants. The group detail view is expected to show
            the variants rather than hide them.
        hash_version: Which canonicalization produced the hashes. See
            :data:`HASH_VERSION_V1`.
    """

    __tablename__ = "recipe"

    id: Optional[int] = Field(default=None, primary_key=True)
    engine: str = Field(sa_column=Column("engine", String, nullable=False, index=True))
    engine_version: Optional[str] = Field(
        default=None, sa_column=Column("engine_version", String, nullable=True)
    )
    document: str = Field(
        sa_column=Column("document", String, nullable=False),
    )
    document_ui: Optional[str] = Field(
        default=None, sa_column=Column("document_ui", String, nullable=True)
    )
    structural_hash: str = Field(
        sa_column=Column("structural_hash", String, nullable=False, index=True)
    )
    # The two looser groupings. Nullable because they describe a *node graph*,
    # and an ai-toolkit training config is a recipe with no graph at all: for
    # those rows there is nothing to roll up and NULL is the honest value.
    topology_hash: Optional[str] = Field(
        default=None,
        sa_column=Column("topology_hash", String, nullable=True, index=True),
    )
    role_hash: Optional[str] = Field(
        default=None, sa_column=Column("role_hash", String, nullable=True, index=True)
    )
    hash_version: str = Field(
        default=HASH_VERSION_V1,
        sa_column=Column(
            "hash_version", String, nullable=False, server_default=HASH_VERSION_V1
        ),
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column("created_at", DateTime, nullable=False),
    )

    __table_args__ = (
        # The invariant the whole feature rests on: one topology, one row. Every
        # fixture in the classification spec is written as "-> 1 recipe", and
        # grouping is the product ("everything I made with this workflow"), so a
        # second row for the same hash is a bug rather than a variant. Paired
        # with hash_version because a v1 hash and a v1-raw hash of the same
        # graph are different values and neither supersedes the other.
        #
        # It is also what makes ingest idempotent: re-importing a file finds the
        # existing recipe instead of forking a near-duplicate.
        Index(
            "ix_recipe_structural_identity",
            "structural_hash",
            "hash_version",
            unique=True,
        ),
    )


class RecipeAsset(SQLModel, table=True):
    """One file a recipe loads: a LoRA, a checkpoint, a source image.

    The point of this table is **honest uncertainty**. A workflow names its
    models by filename, and a filename is not an identity: it can be renamed,
    and two different files can carry the same name. So the row records what
    the document said (:attr:`asset_filename`), what identity was available
    (:attr:`asset_sha256`), and separately what PixlStash has since managed to
    resolve it to (:attr:`resolved_adapter_sha256` /
    :attr:`resolved_checkpoint_sha256`). A generation whose checkpoint matched
    on filename alone is flagged as unverified rather than silently trusted.

    Unresolved rows are the work queue for the retro-resolve pass: registering a
    model on the shelf gives every image that ever named it full lineage,
    retroactively.

    Attributes:
        asset_type: One of the ``ASSET_*`` constants.
        asset_sha256: The hash if the document supplied one (the PixlStash
            loader nodes do), otherwise NULL.
        asset_filename: The reference as it appeared, lowercased basename with
            the directory stripped and the extension kept.
        role: Free text describing what the asset was loaded as, when the node
            makes that meaningful. Not part of identity.
        strength: A **representative** value, first seen, not identity. People
            sweep LoRA strengths constantly, so strength is instance behaviour
            (it lives per node in ``RecipeInstance.params``) and 0.8 against 0.6
            is two instances of one recipe, not two recipes.
    """

    __tablename__ = "recipe_asset"

    id: Optional[int] = Field(default=None, primary_key=True)
    recipe_id: int = Field(
        sa_column=Column(
            "recipe_id",
            Integer,
            ForeignKey("recipe.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    asset_type: str = Field(
        sa_column=Column("asset_type", String, nullable=False, index=True)
    )
    # Both reference columns are indexed because the retro-resolve pass arrives
    # from the other direction: a model has just been registered, and the
    # question is which recipes named it. It can match on either.
    asset_sha256: Optional[str] = Field(
        default=None,
        sa_column=Column("asset_sha256", String, nullable=True, index=True),
    )
    asset_filename: Optional[str] = Field(
        default=None,
        sa_column=Column("asset_filename", String, nullable=True, index=True),
    )
    resolved_adapter_sha256: Optional[str] = Field(
        default=None,
        sa_column=Column("resolved_adapter_sha256", String, nullable=True, index=True),
    )
    resolved_checkpoint_sha256: Optional[str] = Field(
        default=None,
        sa_column=Column(
            "resolved_checkpoint_sha256", String, nullable=True, index=True
        ),
    )
    role: Optional[str] = Field(
        default=None, sa_column=Column("role", String, nullable=True)
    )
    strength: Optional[float] = Field(
        default=None, sa_column=Column("strength", Float, nullable=True)
    )


class RecipeInstance(SQLModel, table=True):
    """One recipe plus one set of prompts and parameters. Everything but the seed.

    Attributes:
        instance_hash: Structural hash plus the canonical JSON of every
            parameter value. Unique, and the lookup ingest does on every image.
        prompt_positive: Lifted out of ``params`` because it is the field people
            search and read. Role is inferred from which sampler slot the text
            node feeds; text that cannot be attributed to a slot stays in
            ``params`` instead of being guessed into one of these columns.
        prompt_negative: As above.
        params: JSON object of every param-bucket value, keys namespaced
            ``{canonical_node_id}.{widget}`` so that two text encoders or
            stacked LoRA loaders never collide. Canonical ids are stable across
            ingests, which is what makes the namespacing usable as a key.
    """

    __tablename__ = "recipe_instance"

    id: Optional[int] = Field(default=None, primary_key=True)
    recipe_id: int = Field(
        sa_column=Column(
            "recipe_id",
            Integer,
            ForeignKey("recipe.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    instance_hash: str = Field(
        sa_column=Column(
            "instance_hash", String, nullable=False, unique=True, index=True
        )
    )
    prompt_positive: Optional[str] = Field(
        default=None, sa_column=Column("prompt_positive", String, nullable=True)
    )
    prompt_negative: Optional[str] = Field(
        default=None, sa_column=Column("prompt_negative", String, nullable=True)
    )
    params: Optional[str] = Field(
        default=None, sa_column=Column("params", String, nullable=True)
    )
    hash_version: str = Field(
        default=HASH_VERSION_V1,
        sa_column=Column(
            "hash_version", String, nullable=False, server_default=HASH_VERSION_V1
        ),
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column("created_at", DateTime, nullable=False),
    )


class Generation(SQLModel, table=True):
    """One image, produced by one instance, at one seed. **It outlives the image.**

    This is the row that makes "delete the pixels, keep the ability to recreate
    them" true, so its delete behaviour is the load-bearing decision in this
    module.

    ``image_id`` is nulled rather than cascaded when the picture goes. An
    earlier draft cascaded it, on the reasoning that the row describes one
    picture and says nothing without it. That was exactly backwards: the recipe
    and the instance have no link to a picture and survive anyway, but the
    **seed lives here**, so cascading meant deleting an image destroyed the last
    piece needed to reproduce it while leaving the workflow standing and
    apparently intact. Recipe plus instance plus seed is the whole recreation,
    and losing a third of it silently is worse than losing all of it.

    What is left after the picture is deleted is a **ghost**: the recipe (the
    core workflow), the instance (the diff off it), the seed, and
    ``image_sha256`` naming what used to be there. That is a few dozen bytes per
    deleted image against megabytes of pixels.

    ``image_sha256`` is why the ghost is not just a hole. It is the identity the
    row keeps once the pointer is gone, it is what a re-import matches against
    to reattach the picture to its own history, and it is what "you already made
    this" can be answered from.

    **Permanent forget deletes this row, and leaves the recipe alone.** A recipe
    is a graph of node types and settings and is not personal data; a prompt and
    a thumbnail can be. That split is the whole reason the retention story can
    be honest: forgetting an image never costs you the workflow, and keeping a
    workflow never keeps anything about the person in the picture.

    There is deliberately no unique constraint on ``image_id``. Ingest looks the
    image up before inserting, and a constraint here would turn a re-ingest race
    into an exception raised inside a background task, which is the failure mode
    the model-shelf work already learned to avoid. Nullability makes the point
    moot in the other direction too: many ghosts share a NULL ``image_id``.

    Attributes:
        image_id: The live picture, or NULL once it is gone. Never the identity.
        image_sha256: What the image was. Survives the picture, and is how a
            ghost is recognised if the same file comes back.
        seed: The one genuinely volatile value. A re-roll is a new row here and
            changes nothing above it.
        overrides: JSON for rare last-mile deviations that never made it into
            the document.
        remote_job_id: Set when the image came back from an offloaded run. No
            foreign key: ``remote_job`` is Phase 5 and does not exist yet.
    """

    __tablename__ = "generation"

    id: Optional[int] = Field(default=None, primary_key=True)
    image_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            "image_id",
            Integer,
            ForeignKey("picture.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    image_sha256: Optional[str] = Field(
        default=None,
        sa_column=Column("image_sha256", String, nullable=True, index=True),
    )
    instance_id: int = Field(
        sa_column=Column(
            "instance_id",
            Integer,
            ForeignKey("recipe_instance.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    seed: Optional[int] = Field(
        default=None, sa_column=Column("seed", Integer, nullable=True)
    )
    overrides: Optional[str] = Field(
        default=None, sa_column=Column("overrides", String, nullable=True)
    )
    remote_job_id: Optional[int] = Field(
        default=None, sa_column=Column("remote_job_id", Integer, nullable=True)
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column=Column("created_at", DateTime, nullable=False),
    )


class GenerationInput(SQLModel, table=True):
    """The resolution lock: which images a query-based node actually loaded.

    The PixlStash browse and search nodes select from vault state at run time,
    so the same workflow replayed next month loads different pictures. The
    workflow holds the query (the intent); this table holds what came back (the
    fact). Replay then has two honest modes: re-run the query for fresh results,
    or replay the lock for the exact images.

    A generation saved outside Picture Saver simply has no rows here, and that
    degrades to "query known, results unknown" rather than to a false claim of
    exact reproducibility.

    ``image_sha256`` is the identity and never dangles. ``image_id`` is a
    convenience pointer that is nulled if the picture is deleted, because the
    lock is a record of what happened and deleting a picture does not unmake the
    generation that consumed it. This is also what makes the reverse question,
    "which generations used this image", answerable before a deletion.

    Attributes:
        node_ref: Canonical node id of the node that did the resolving, so a
            workflow with two search nodes keeps their results apart.
        position: Order within that node's result set.
    """

    __tablename__ = "generation_input"

    generation_id: int = Field(
        sa_column=Column(
            "generation_id",
            Integer,
            ForeignKey("generation.id", ondelete="CASCADE"),
            primary_key=True,
        )
    )
    node_ref: str = Field(
        sa_column=Column("node_ref", String, primary_key=True),
    )
    position: int = Field(
        sa_column=Column("position", Integer, primary_key=True),
    )
    image_sha256: str = Field(
        sa_column=Column("image_sha256", String, nullable=False, index=True)
    )
    image_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            "image_id",
            Integer,
            ForeignKey("picture.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
