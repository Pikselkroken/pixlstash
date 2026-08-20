"""Workflow provenance, across both databases (v1.11 AI-toolkit Phase 2).

The tables are empty until the canonicalizer and the ingest hook land on top of
them, so what there is to test at this stage is the shape, and specifically the
four decisions that are expensive to change once rows exist:

1. **The split is hub/vault, and the join is a hash.** ``recipe``,
   ``recipe_asset`` and ``recipe_instance`` are hub tables, because a workflow
   describes the machine and is shared by every library. ``generation`` and
   ``generation_input`` name a ``picture.id`` and are vault tables.
   ``generation.instance_hash`` carries the link, and must never become an
   integer: no foreign key spans the two files, and SQLite reissues a deleted
   row's id.

2. **One topology, one recipe row.** Every fixture in the field-classification
   spec is written as "-> 1 recipe", and grouping is the whole product, so the
   database refuses a second row for one structural hash. A ``v1`` hash and a
   ``v1-raw`` hash of the same graph are different values and both may exist.

3. **Nothing dies with a picture.** Both vault tables null the pointer and keep
   a sha256 instead. ``generation`` holds the seed, a third of what recreating
   a deleted image takes, so cascading would destroy the recreation while
   leaving the workflow standing and apparently intact. What survives is a
   **ghost**, and permanently forgetting one deletes the generation and keeps
   the recipe.

4. **Strict identity underneath, aggressive grouping on top.** ``family_hash``
   and ``topology_hash`` group hard for the Workflows view and are deliberately
   not unique, while ``structural_hash`` never merges. A group can be split
   later; a merged identity cannot be recovered.
"""

import os
import sqlite3
import subprocess
import sys
import tempfile

import pytest
import sqlalchemy as sa
from sqlmodel import Session, SQLModel, create_engine, select

from pixlstash.db_models import (
    ASSET_LORA,
    ENGINE_COMFYUI,
    HASH_VERSION_V1,
    HASH_VERSION_V1_RAW,
    Generation,
    GenerationInput,
    Picture,
)
from pixlstash.hub.schema import apply_migrations

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

VAULT_TABLES = ("generation", "generation_input")
HUB_TABLES = ("recipe", "recipe_asset", "recipe_instance")

STRUCTURAL_HASH = "s" * 64
TOPOLOGY_HASH = "t" * 64
FAMILY_HASH = "r" * 64
INSTANCE_HASH = "i" * 64
IMAGE_SHA = "f" * 64
GENERATED_SHA = "e" * 64


@pytest.fixture
def vault():
    """An in-memory vault with foreign keys actually enforced.

    SQLite defaults ``foreign_keys`` off, and a bare ``create_engine`` does not
    pick up the pragma the application's engine installs
    (``database.py::_apply_sqlite_settings``). Without this the set-null
    behaviour below would silently pass no matter what the DDL said.
    """
    engine = create_engine("sqlite://")

    @sa.event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def hub():
    """A hub built the way the application builds one: apply_migrations."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    apply_migrations(conn)
    yield conn
    conn.close()


def make_recipe(
    hub,
    structural_hash=STRUCTURAL_HASH,
    hash_version=HASH_VERSION_V1,
    topology_hash=TOPOLOGY_HASH,
    family_hash=FAMILY_HASH,
):
    cur = hub.execute(
        "INSERT INTO recipe (engine, document, structural_hash, topology_hash, "
        "family_hash, hash_version) VALUES (?, ?, ?, ?, ?, ?)",
        (
            ENGINE_COMFYUI,
            "{}",
            structural_hash,
            topology_hash,
            family_hash,
            hash_version,
        ),
    )
    hub.commit()
    return cur.lastrowid


def make_instance(hub, recipe_id, instance_hash=INSTANCE_HASH):
    cur = hub.execute(
        "INSERT INTO recipe_instance (recipe_id, instance_hash) VALUES (?, ?)",
        (recipe_id, instance_hash),
    )
    hub.commit()
    return cur.lastrowid


def make_generation(vault):
    """A picture, its generation, and the lock naming what the run consumed."""
    picture = Picture(deleted=False, is_video=False)
    consumed = Picture(deleted=False, is_video=False)
    vault.add(picture)
    vault.add(consumed)
    vault.commit()

    generation = Generation(
        image_id=picture.id,
        image_sha256=GENERATED_SHA,
        instance_hash=INSTANCE_HASH,
        seed=1234567890,
    )
    vault.add(generation)
    vault.commit()

    vault.add(
        GenerationInput(
            generation_id=generation.id,
            node_ref="7",
            position=0,
            image_sha256=IMAGE_SHA,
            image_id=consumed.id,
        )
    )
    vault.commit()
    return picture, consumed, generation


class TestTheSplitIsHubAndVault:
    def test_the_workflow_tables_are_in_the_hub(self, hub):
        names = {
            row[0]
            for row in hub.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert set(HUB_TABLES) <= names

    def test_the_workflow_tables_are_not_in_the_vault(self, vault):
        # If these were declared as SQLModel tables they would be created inside
        # every vault by the baseline's metadata.create_all(), which is the
        # exact hazard hub/schema.py's docstring exists to prevent.
        tables = set(SQLModel.metadata.tables)
        assert not (set(HUB_TABLES) & tables)
        assert set(VAULT_TABLES) <= tables

    def test_the_join_to_the_hub_is_a_hash_not_an_id(self, vault):
        columns = {c.name for c in Generation.__table__.columns}
        assert "instance_hash" in columns
        # An integer here would be an unenforceable reference across the
        # hub/vault boundary, and SQLite reissues deleted ids, so it would
        # silently come to name a different instance. Same rule as 0103.
        assert "instance_id" not in columns

    def test_no_foreign_key_points_out_of_the_vault(self, vault):
        for table in VAULT_TABLES:
            for fk in SQLModel.metadata.tables[table].foreign_keys:
                assert fk.column.table.name in {"picture", "generation"}

    def test_applying_the_hub_schema_twice_is_a_no_op(self, hub):
        recipe_id = make_recipe(hub)
        apply_migrations(hub)
        rows = hub.execute("SELECT id FROM recipe").fetchall()
        assert [r[0] for r in rows] == [recipe_id]


class TestOneTopologyOneRecipe:
    def test_a_second_row_for_the_same_structural_hash_is_refused(self, hub):
        make_recipe(hub)
        with pytest.raises(sqlite3.IntegrityError):
            make_recipe(hub)

    def test_a_raw_hash_of_the_same_graph_is_a_different_recipe(self, hub):
        make_recipe(hub, hash_version=HASH_VERSION_V1)
        make_recipe(hub, hash_version=HASH_VERSION_V1_RAW)

        versions = {
            row[0]
            for row in hub.execute(
                "SELECT hash_version FROM recipe WHERE structural_hash = ?",
                (STRUCTURAL_HASH,),
            )
        }
        # Neither supersedes the other: they are hashes of different
        # canonicalizations and nothing can compare them.
        assert versions == {HASH_VERSION_V1, HASH_VERSION_V1_RAW}

    def test_the_same_instance_hash_cannot_be_recorded_twice(self, hub):
        recipe_id = make_recipe(hub)
        make_instance(hub, recipe_id)
        with pytest.raises(sqlite3.IntegrityError):
            make_instance(hub, recipe_id)

    def test_two_images_of_one_instance_are_two_generations(self, vault):
        """The re-roll case: same everything, different seed."""
        for seed in (11, 22):
            picture = Picture(deleted=False, is_video=False)
            vault.add(picture)
            vault.commit()
            vault.add(
                Generation(image_id=picture.id, instance_hash=INSTANCE_HASH, seed=seed)
            )
        vault.commit()

        rows = vault.exec(select(Generation)).all()
        assert len(rows) == 2
        assert {r.instance_hash for r in rows} == {INSTANCE_HASH}


class TestThreeHashesGroupAtThreeStrengths:
    def test_recipes_sharing_a_family_hash_stay_separate_rows(self, hub):
        """Two variants of one workflow: one group, two replayable recipes."""
        make_recipe(hub, structural_hash="a" * 64, topology_hash="p" * 64)
        make_recipe(hub, structural_hash="b" * 64, topology_hash="q" * 64)

        rows = hub.execute(
            "SELECT structural_hash FROM recipe WHERE family_hash = ?",
            (FAMILY_HASH,),
        ).fetchall()
        # Grouping is a read, never a merge. Both keep their own identity, so
        # "which images used this LoRA" still resolves underneath the group.
        assert {r[0] for r in rows} == {"a" * 64, "b" * 64}

    def test_the_looser_hashes_are_not_unique_indexes(self, hub):
        """A unique index here would make the whole grouping design impossible."""
        unique = {
            row[1]: bool(row[2]) for row in hub.execute("PRAGMA index_list('recipe')")
        }
        assert unique["ix_recipe_family"] is False
        assert unique["ix_recipe_topology"] is False
        assert unique["ux_recipe_structural_identity"] is True

    def test_a_graphless_recipe_may_have_no_grouping_hashes(self, hub):
        """An ai-toolkit training config is a recipe with no node graph.

        NULL is the honest value there, not a hash of nothing.
        """
        make_recipe(hub, topology_hash=None, family_hash=None)
        row = hub.execute("SELECT topology_hash, family_hash FROM recipe").fetchone()
        assert row == (None, None)


class TestTheWorkflowOutlivesTheImage:
    def test_deleting_the_picture_leaves_a_ghost_that_can_still_recreate_it(
        self, vault, hub
    ):
        picture, _consumed, _generation = make_generation(vault)
        recipe_id = make_recipe(hub)
        make_instance(hub, recipe_id)

        vault.delete(picture)
        vault.commit()
        vault.expire_all()

        ghost = vault.exec(select(Generation)).one()
        # The pointer is gone, the recreation is not.
        assert ghost.image_id is None
        assert ghost.seed == 1234567890
        assert ghost.image_sha256 == GENERATED_SHA

        # And the hub half it resolves through is untouched by a vault delete.
        resolved = hub.execute(
            "SELECT recipe_id FROM recipe_instance WHERE instance_hash = ?",
            (ghost.instance_hash,),
        ).fetchone()
        assert resolved == (recipe_id,)

    def test_forgetting_a_ghost_keeps_the_workflow(self, vault, hub):
        """The privacy split: a prompt is personal, a graph of node types is not.

        Permanent-forget has to be able to remove everything about one image
        without costing the user the workflow they built.
        """
        picture, _consumed, _generation = make_generation(vault)
        recipe_id = make_recipe(hub)
        make_instance(hub, recipe_id)
        vault.delete(picture)
        vault.commit()

        vault.delete(vault.exec(select(Generation)).one())
        vault.commit()

        assert vault.exec(select(Generation)).all() == []
        # The lock rows go too, being children of the generation.
        assert vault.exec(select(GenerationInput)).all() == []
        # The workflow stays, in the other database entirely.
        assert hub.execute("SELECT COUNT(*) FROM recipe").fetchone() == (1,)
        assert hub.execute("SELECT COUNT(*) FROM recipe_instance").fetchone() == (1,)

    def test_deleting_a_consumed_picture_leaves_the_lock_standing(self, vault):
        _picture, consumed, generation = make_generation(vault)

        vault.delete(consumed)
        vault.commit()

        locks = vault.exec(select(GenerationInput)).all()
        assert len(locks) == 1
        # The fact survives the pointer: the run really did consume that file,
        # and "which generations used this image" has to stay answerable.
        assert locks[0].image_sha256 == IMAGE_SHA
        assert locks[0].image_id is None
        assert vault.exec(select(Generation)).all() == [generation]


class TestAssetResolutionIsHonest:
    def test_an_unresolved_asset_records_the_filename_and_no_identity(self, hub):
        """The primary state: a workflow names a file and nothing else.

        A filename is not an identity, so the resolved columns stay NULL until
        the model is actually registered on the shelf. They are what the
        retro-resolve pass fills in, and what "matched by filename only, treat
        as unverified" reads.
        """
        recipe_id = make_recipe(hub)
        hub.execute(
            "INSERT INTO recipe_asset (recipe_id, asset_type, asset_filename) "
            "VALUES (?, ?, ?)",
            (recipe_id, ASSET_LORA, "somelora.safetensors"),
        )
        hub.commit()

        row = hub.execute(
            "SELECT asset_sha256, resolved_adapter_sha256, "
            "resolved_checkpoint_sha256 FROM recipe_asset"
        ).fetchone()
        assert row == (None, None, None)

    def test_the_shelf_link_is_a_hash_not_a_model_id(self, hub):
        columns = {row[1] for row in hub.execute("PRAGMA table_info(recipe_asset)")}
        assert "resolved_adapter_sha256" in columns
        assert "resolved_checkpoint_sha256" in columns
        # An integer would break the moment a model is forgotten and its id
        # reissued, which is exactly what model_folder's AUTOINCREMENT avoids.
        assert "resolved_adapter_id" not in columns
        assert "resolved_model_id" not in columns

    def test_which_workflows_use_this_model_is_an_indexed_lookup(self, hub):
        """The shelf's reverse query, and the reason both columns are indexed."""
        indexed = {row[1] for row in hub.execute("PRAGMA index_list('recipe_asset')")}
        assert "ix_recipe_asset_sha" in indexed
        assert "ix_recipe_asset_filename" in indexed
        assert "ix_recipe_asset_adapter" in indexed
        assert "ix_recipe_asset_checkpoint" in indexed


def _describe(url):
    engine = sa.create_engine(url)
    inspector = sa.inspect(engine)
    described = {}
    for table in VAULT_TABLES:
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


def _upgrade_a_fresh_vault(tmp, name):
    url = f"sqlite:///{os.path.join(tmp, name)}"
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=os.path.join(PROJECT_ROOT, "pixlstash"),
        env={**os.environ, "PIXLSTASH_DB_URL": url, "PYTHONPATH": PROJECT_ROOT},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}"
    )
    return url


def test_the_migrated_schema_matches_what_a_fresh_vault_builds():
    """Revision 0105 and ``create_all`` must produce the same two tables.

    Revision 0103's ``CREATE INDEX`` calls sat inside its "if the table does not
    exist" branch, which a fresh database never enters, so a migrated vault got
    an index that a fresh one did not. Comparing the two schemas is the only
    check that catches that class of drift, and it catches a missing
    ``ondelete`` just as well.
    """
    with tempfile.TemporaryDirectory() as tmp:
        migrated_url = _upgrade_a_fresh_vault(tmp, "migrated.db")

        fresh_url = f"sqlite:///{os.path.join(tmp, 'fresh.db')}"
        fresh_engine = sa.create_engine(fresh_url)
        SQLModel.metadata.create_all(fresh_engine)
        fresh_engine.dispose()

        assert _describe(migrated_url) == _describe(fresh_url)


def test_the_hub_tables_never_appear_in_a_migrated_vault():
    """The hazard ``hub/schema.py``'s docstring names, asserted end to end."""
    with tempfile.TemporaryDirectory() as tmp:
        url = _upgrade_a_fresh_vault(tmp, "vault.db")
        engine = sa.create_engine(url)
        names = set(sa.inspect(engine).get_table_names())
        engine.dispose()
        assert not (set(HUB_TABLES) & names)
