"""The recipe / instance / generation tables (v1.11 AI-toolkit Phase 2).

These tables are empty until the canonicalizer and the ingest hook land on top
of them, so what there is to test at this stage is the shape, and specifically
the four decisions that are expensive to change once rows exist:

1. **One topology, one recipe row.** Every fixture in the field-classification
   spec is written as "-> 1 recipe", and grouping is the whole product. A second
   row for the same structural hash is a bug, not a variant, so the database
   refuses it. A ``v1`` hash and a ``v1-raw`` hash of the same graph are
   different values and both are allowed to exist.

2. **Nothing dies with a picture.** Both tables pointing at ``picture`` null the
   pointer and keep a sha256 instead. ``generation`` holds the seed, which is a
   third of what recreating a deleted image takes, so cascading it would destroy
   the recreation while leaving the workflow standing and apparently intact.
   ``generation_input`` records what a run consumed, and deleting one of those
   images does not unmake the run. What survives a deletion is a **ghost**, and
   permanently forgetting one deletes the generation and keeps the recipe.

3. **Strict identity underneath, aggressive grouping on top.** ``family_hash`` and
   ``topology_hash`` group hard for the Workflows view and are deliberately not
   unique, while ``structural_hash`` never merges. A group can be split later; a
   merged identity cannot be recovered.

4. **Both creation paths agree.** A fresh vault gets its tables from
   ``SQLModel.metadata.create_all``; an existing one gets them from revision
   0105. Revision 0103 shipped with an index that only ever existed on the
   migrated path, because the ``CREATE INDEX`` sat inside a branch a fresh
   database never enters. This test compares the two schemas directly so that
   cannot happen again silently.
"""

import os
import subprocess
import sys
import tempfile

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

from pixlstash.db_models import (
    ASSET_LORA,
    ENGINE_COMFYUI,
    HASH_VERSION_V1,
    HASH_VERSION_V1_RAW,
    Generation,
    GenerationInput,
    Picture,
    Recipe,
    RecipeAsset,
    RecipeInstance,
)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

RECIPE_TABLES = (
    "recipe",
    "recipe_asset",
    "recipe_instance",
    "generation",
    "generation_input",
)

STRUCTURAL_HASH = "s" * 64
TOPOLOGY_HASH = "t" * 64
FAMILY_HASH = "r" * 64
INSTANCE_HASH = "i" * 64
IMAGE_SHA = "f" * 64
GENERATED_SHA = "e" * 64


@pytest.fixture
def session():
    """An in-memory vault with foreign keys actually enforced.

    SQLite defaults ``foreign_keys`` off, and a bare ``create_engine`` does not
    pick up the pragma the application's engine installs
    (``database.py::_apply_sqlite_settings``). Without this the cascade and
    set-null behaviour below would silently pass no matter what the DDL said.
    """
    engine = create_engine("sqlite://")

    @sa.event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def make_recipe(
    session,
    structural_hash=STRUCTURAL_HASH,
    hash_version=HASH_VERSION_V1,
    topology_hash=TOPOLOGY_HASH,
    family_hash=FAMILY_HASH,
):
    recipe = Recipe(
        engine=ENGINE_COMFYUI,
        document="{}",
        structural_hash=structural_hash,
        topology_hash=topology_hash,
        family_hash=family_hash,
        hash_version=hash_version,
    )
    session.add(recipe)
    session.commit()
    return recipe


def make_generation(session):
    """A picture, the recipe that made it, and the lock naming what it consumed."""
    picture = Picture(deleted=False, is_video=False)
    consumed = Picture(deleted=False, is_video=False)
    session.add(picture)
    session.add(consumed)
    session.commit()

    recipe = make_recipe(session)
    instance = RecipeInstance(recipe_id=recipe.id, instance_hash=INSTANCE_HASH)
    session.add(instance)
    session.commit()

    generation = Generation(
        image_id=picture.id,
        image_sha256=GENERATED_SHA,
        instance_id=instance.id,
        seed=1234567890,
    )
    session.add(generation)
    session.commit()

    session.add(
        GenerationInput(
            generation_id=generation.id,
            node_ref="7",
            position=0,
            image_sha256=IMAGE_SHA,
            image_id=consumed.id,
        )
    )
    session.commit()
    return picture, consumed, generation


class TestOneTopologyOneRecipe:
    def test_a_second_row_for_the_same_structural_hash_is_refused(self, session):
        make_recipe(session)
        with pytest.raises(IntegrityError):
            make_recipe(session)

    def test_a_raw_hash_of_the_same_graph_is_a_different_recipe(self, session):
        make_recipe(session, hash_version=HASH_VERSION_V1)
        session.rollback()
        make_recipe(session, hash_version=HASH_VERSION_V1_RAW)

        rows = session.exec(
            select(Recipe).where(Recipe.structural_hash == STRUCTURAL_HASH)
        ).all()
        # Neither supersedes the other: they are hashes of different
        # canonicalizations and nothing can compare them.
        assert {row.hash_version for row in rows} == {
            HASH_VERSION_V1,
            HASH_VERSION_V1_RAW,
        }

    def test_two_images_of_one_instance_are_two_generations(self, session):
        """The re-roll case: same everything, different seed, no new recipe."""
        recipe = make_recipe(session)
        instance = RecipeInstance(recipe_id=recipe.id, instance_hash=INSTANCE_HASH)
        session.add(instance)
        session.commit()

        for seed in (11, 22):
            picture = Picture(deleted=False, is_video=False)
            session.add(picture)
            session.commit()
            session.add(
                Generation(image_id=picture.id, instance_id=instance.id, seed=seed)
            )
        session.commit()

        assert len(session.exec(select(Recipe)).all()) == 1
        assert len(session.exec(select(RecipeInstance)).all()) == 1
        assert len(session.exec(select(Generation)).all()) == 2

    def test_the_same_instance_hash_cannot_be_recorded_twice(self, session):
        recipe = make_recipe(session)
        session.add(RecipeInstance(recipe_id=recipe.id, instance_hash=INSTANCE_HASH))
        session.commit()
        session.add(RecipeInstance(recipe_id=recipe.id, instance_hash=INSTANCE_HASH))
        with pytest.raises(IntegrityError):
            session.commit()


class TestTheWorkflowOutlivesTheImage:
    """Delete the pixels, keep the ability to recreate them.

    Recreation needs three things: the recipe, the instance, and the seed. The
    first two have no link to a picture and survive on their own; the seed lives
    on the generation row, so this is where the property is actually won or
    lost.
    """

    def test_deleting_the_picture_leaves_a_ghost_that_can_still_recreate_it(
        self, session
    ):
        picture, _consumed, generation = make_generation(session)
        recipe_id = session.exec(select(Recipe)).one().id

        session.delete(picture)
        session.commit()
        session.expire_all()

        ghost = session.exec(select(Generation)).one()
        # The pointer is gone, the recreation is not.
        assert ghost.image_id is None
        assert ghost.seed == 1234567890
        assert ghost.image_sha256 == GENERATED_SHA

        instance = session.get(RecipeInstance, ghost.instance_id)
        assert instance is not None
        assert instance.recipe_id == recipe_id
        assert session.get(Recipe, recipe_id) is not None

    def test_forgetting_a_ghost_keeps_the_workflow(self, session):
        """The privacy split: a prompt is personal, a graph of node types is not.

        Permanent-forget has to be able to remove everything about one image
        without costing the user the workflow they built.
        """
        picture, _consumed, generation = make_generation(session)
        recipe_id = session.exec(select(Recipe)).one().id
        session.delete(picture)
        session.commit()

        session.delete(session.exec(select(Generation)).one())
        session.commit()

        assert session.exec(select(Generation)).all() == []
        # The lock rows go too, being children of the generation.
        assert session.exec(select(GenerationInput)).all() == []
        # The workflow stays.
        assert session.get(Recipe, recipe_id) is not None
        assert len(session.exec(select(RecipeInstance)).all()) == 1

    def test_deleting_a_consumed_picture_leaves_the_lock_standing(self, session):
        _picture, consumed, generation = make_generation(session)

        session.delete(consumed)
        session.commit()

        locks = session.exec(select(GenerationInput)).all()
        assert len(locks) == 1
        # The fact survives the pointer: the run really did consume that file,
        # and "which generations used this image" has to stay answerable.
        assert locks[0].image_sha256 == IMAGE_SHA
        assert locks[0].image_id is None
        assert session.exec(select(Generation)).all() == [generation]

    def test_dropping_a_recipe_takes_its_assets_and_instances(self, session):
        recipe = make_recipe(session)
        session.add(
            RecipeAsset(
                recipe_id=recipe.id,
                asset_type=ASSET_LORA,
                asset_filename="somelora.safetensors",
            )
        )
        session.add(RecipeInstance(recipe_id=recipe.id, instance_hash=INSTANCE_HASH))
        session.commit()

        session.delete(recipe)
        session.commit()

        assert session.exec(select(RecipeAsset)).all() == []
        assert session.exec(select(RecipeInstance)).all() == []


class TestThreeHashesGroupAtThreeStrengths:
    """Strict identity underneath, aggressive grouping on top.

    The Workflows view merges hard (93 groups from 281 recipes on a real
    library) and that is only safe because the strict hash below it never
    merges. A group can be split later; a merged identity cannot be recovered.
    """

    def test_recipes_sharing_a_family_hash_stay_separate_rows(self, session):
        """Two variants of one workflow: one group, two replayable recipes."""
        make_recipe(session, structural_hash="a" * 64, topology_hash="p" * 64)
        make_recipe(session, structural_hash="b" * 64, topology_hash="q" * 64)

        rows = session.exec(
            select(Recipe).where(Recipe.family_hash == FAMILY_HASH)
        ).all()
        assert len(rows) == 2
        # Grouping is a read, never a merge. Both keep their own identity, so
        # "which images used this LoRA" still resolves underneath the group.
        assert {r.structural_hash for r in rows} == {"a" * 64, "b" * 64}

    def test_the_looser_hashes_are_not_unique_constraints(self, session):
        """A unique index here would make the whole grouping design impossible."""
        indexes = {idx.name: idx.unique for idx in Recipe.__table__.indexes}
        assert indexes["ix_recipe_family_hash"] is False
        assert indexes["ix_recipe_topology_hash"] is False
        assert indexes["ix_recipe_structural_identity"] is True

    def test_a_graphless_recipe_may_have_no_grouping_hashes(self, session):
        """An ai-toolkit training config is a recipe with no node graph.

        NULL is the honest value there, not a hash of nothing.
        """
        recipe = make_recipe(session, topology_hash=None, family_hash=None)
        assert recipe.topology_hash is None
        assert recipe.family_hash is None


class TestAssetResolutionIsHonest:
    def test_an_unresolved_asset_records_the_filename_and_no_identity(self, session):
        """The primary state: a workflow names a file and nothing else.

        A filename is not an identity, so the resolved columns stay NULL until
        the model is actually registered on the shelf. They are what the
        retro-resolve pass fills in, and what "matched by filename only, treat
        as unverified" reads.
        """
        recipe = make_recipe(session)
        asset = RecipeAsset(
            recipe_id=recipe.id,
            asset_type=ASSET_LORA,
            asset_filename="somelora.safetensors",
        )
        session.add(asset)
        session.commit()

        assert asset.asset_sha256 is None
        assert asset.resolved_adapter_sha256 is None
        assert asset.resolved_checkpoint_sha256 is None

    def test_the_shelf_link_is_a_hash_not_a_model_id(self, session):
        columns = {c.name for c in RecipeAsset.__table__.columns}
        assert "resolved_adapter_sha256" in columns
        assert "resolved_checkpoint_sha256" in columns
        # An integer here would be an unenforceable reference across the
        # hub/vault boundary, and SQLite reissues deleted ids, so it would
        # silently come to name a different model. Same rule as revision 0103.
        assert "resolved_adapter_id" not in columns
        assert "resolved_checkpoint_id" not in columns


def _describe(url):
    engine = sa.create_engine(url)
    inspector = sa.inspect(engine)
    described = {}
    for table in RECIPE_TABLES:
        assert table in inspector.get_table_names(), f"{table} missing from {url}"
        described[table] = {
            "pk": tuple(inspector.get_pk_constraint(table)["constrained_columns"]),
            "columns": sorted(
                (c["name"], str(c["type"]), bool(c["nullable"]))
                for c in inspector.get_columns(table)
            ),
            "indexes": sorted(
                (i["name"], tuple(i["column_names"]), bool(i.get("unique")))
                for i in inspector.get_indexes(table)
            ),
            "foreign_keys": sorted(
                (
                    tuple(fk["constrained_columns"]),
                    fk["referred_table"],
                    tuple(fk["referred_columns"]),
                    (fk.get("options") or {}).get("ondelete"),
                )
                for fk in inspector.get_foreign_keys(table)
            ),
        }
    engine.dispose()
    return described


def test_the_migrated_schema_matches_what_a_fresh_vault_builds():
    """Revision 0105 and ``create_all`` must produce the same five tables.

    Revision 0103's ``CREATE INDEX`` calls sat inside its "if the table does not
    exist" branch, which a fresh database never enters, so a migrated vault got
    an index that a fresh one did not. Comparing the two schemas is the only
    check that catches that class of drift, and it catches a missing
    ``ondelete`` just as well.
    """
    with tempfile.TemporaryDirectory() as tmp:
        migrated_url = f"sqlite:///{os.path.join(tmp, 'migrated.db')}"
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
            cwd=os.path.join(PROJECT_ROOT, "pixlstash"),
            env={
                **os.environ,
                "PIXLSTASH_DB_URL": migrated_url,
                "PYTHONPATH": PROJECT_ROOT,
            },
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}"
        )

        fresh_url = f"sqlite:///{os.path.join(tmp, 'fresh.db')}"
        fresh_engine = sa.create_engine(fresh_url)
        SQLModel.metadata.create_all(fresh_engine)
        fresh_engine.dispose()

        assert _describe(migrated_url) == _describe(fresh_url)
