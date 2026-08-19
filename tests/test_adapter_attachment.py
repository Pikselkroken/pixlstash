"""Attaching a model-shelf adapter to a character or a set.

The load-bearing decision under test: **the link is the adapter's sha256, not an
integer id.** No foreign key can span the hub and a vault, and SQLite hands a
deleted row's id to the next insert, so an integer link would silently re-point
at a different adapter after a delete plus insert while still looking valid.
That is the same recycled-identifier hazard ``library.uuid`` exists to kill.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect
from sqlmodel import Session, SQLModel, create_engine, select

from pixlstash.db_models import (
    ENTITY_CHARACTER,
    ENTITY_SET,
    AdapterAttachment,
    Character,
)

SHA_A = "a" * 64
SHA_B = "b" * 64


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def attach(session, sha, entity_type, entity_id):
    row = AdapterAttachment(
        adapter_sha256=sha,
        entity_type=entity_type,
        entity_id=entity_id,
        created_at=datetime.now(timezone.utc),
    )
    session.add(row)
    session.commit()
    return row


class TestTheLinkIsAHash:
    def test_the_adapter_is_addressed_by_content_not_by_id(self, session):
        columns = {c.name for c in AdapterAttachment.__table__.columns}
        assert "adapter_sha256" in columns
        # An `adapter_id` here would be an unenforceable cross-database
        # reference AND would survive a delete plus insert pointing at the
        # wrong adapter. Neither is acceptable, so the column must not exist.
        assert "adapter_id" not in columns

    def test_the_hash_column_is_text(self, session):
        column = AdapterAttachment.__table__.columns["adapter_sha256"]
        assert isinstance(column.type, type(Character.__table__.columns["name"].type))


class TestPeers:
    def test_a_character_and_a_set_are_addressed_the_same_way(self, session):
        attach(session, SHA_A, ENTITY_CHARACTER, 1)
        attach(session, SHA_A, ENTITY_SET, 1)
        rows = session.exec(select(AdapterAttachment)).all()
        assert {r.entity_type for r in rows} == {ENTITY_CHARACTER, ENTITY_SET}

    def test_the_same_id_in_different_namespaces_does_not_collide(self, session):
        # Character 1 and set 1 are different things. A scalar character_id /
        # set_id pair would make this representable in two ways.
        attach(session, SHA_A, ENTITY_CHARACTER, 1)
        attach(session, SHA_A, ENTITY_SET, 1)
        assert len(session.exec(select(AdapterAttachment)).all()) == 2

    def test_one_adapter_serves_many_entities(self, session):
        for entity_id in (1, 2, 3):
            attach(session, SHA_A, ENTITY_CHARACTER, entity_id)
        assert len(session.exec(select(AdapterAttachment)).all()) == 3

    def test_one_entity_holds_many_adapters(self, session):
        for sha in (SHA_A, SHA_B):
            attach(session, sha, ENTITY_CHARACTER, 1)
        assert len(session.exec(select(AdapterAttachment)).all()) == 2


class TestIdempotence:
    def test_attaching_twice_is_refused_by_the_database(self, session):
        # The composite key makes a double attach a no-op at the storage layer
        # rather than a duplicate row the UI has to de-duplicate.
        attach(session, SHA_A, ENTITY_CHARACTER, 1)
        with pytest.raises(Exception):
            attach(session, SHA_A, ENTITY_CHARACTER, 1)

    def test_the_primary_key_is_all_three_columns(self, session):
        key = {c.name for c in AdapterAttachment.__table__.primary_key.columns}
        assert key == {"adapter_sha256", "entity_type", "entity_id"}


class TestCharacterColor:
    def test_a_character_carries_a_colour_like_a_set_does(self, session):
        character = Character(name="Clementine", character_color="#8E24AA")
        session.add(character)
        session.commit()
        assert session.exec(select(Character)).one().character_color == "#8E24AA"

    def test_the_colour_is_optional(self, session):
        session.add(Character(name="Unpainted"))
        session.commit()
        assert session.exec(select(Character)).one().character_color is None


class TestSchemaShape:
    def test_both_lookup_directions_are_indexed(self, session):
        # The shelf asks "who uses this adapter" and "what does this character
        # use". Both are hot paths in the same view.
        engine = create_engine("sqlite://")
        SQLModel.metadata.create_all(engine)
        indexed = {
            tuple(ix["column_names"])
            for ix in inspect(engine).get_indexes("adapter_attachment")
        }
        assert ("adapter_sha256",) in indexed
        # The composite, not merely a column: `attached_hashes()` filters on
        # `entity_type AND entity_id` together. Asserting only that `entity_id`
        # appears somewhere passed on a fresh database that had nothing but the
        # single-column index, which is the shape revision 0103 could not reach.
        assert ("entity_type", "entity_id") in indexed
