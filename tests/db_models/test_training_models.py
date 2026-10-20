"""Unit tests for the TrainingJob / TrainedLora models and the ai-toolkit
config plumbing.

These run against a throwaway SQLite database (no Server needed): creating the
two tables plus their picture-set/character parents from the registered
``SQLModel.metadata`` is enough to exercise the relationships and table names.
The config round-trip tests poke the serialise/patch helpers directly.
"""

import pytest
from sqlalchemy.pool import NullPool
from sqlmodel import Session, SQLModel, create_engine

# Importing the models registers them on SQLModel.metadata.
from pixlstash.db_models import (  # noqa: F401
    Character,
    PictureSet,
    TrainedLora,
    TrainingJob,
    User,
)
from pixlstash.utils.service.user_settings_utils import (
    apply_user_config_patch,
    serialize_user_config,
)


@pytest.fixture
def session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'training.db'}",
        echo=False,
        poolclass=NullPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_table_names():
    assert TrainingJob.__tablename__ == "training_job"
    assert TrainedLora.__tablename__ == "trained_lora"


def test_create_set_scoped_job_with_lora(session):
    picture_set = PictureSet(name="my-set")
    session.add(picture_set)
    session.commit()
    session.refresh(picture_set)

    job = TrainingJob(uuid="job-set-1", name="set job", set_id=picture_set.id)
    session.add(job)
    session.commit()
    session.refresh(job)

    assert job.id is not None
    assert job.status == "queued"
    assert job.step == 0
    assert job.created_at is not None
    assert job.character_id is None

    lora = TrainedLora(
        uuid="lora-1",
        job_id=job.id,
        set_id=picture_set.id,
        name="my-lora",
        file_path="loras/lora-1.safetensors",
        file_size_bytes=1234,
        file_sha="deadbeef",
        step=1000,
    )
    session.add(lora)
    session.commit()
    session.refresh(lora)
    session.refresh(job)

    # Relationship both directions.
    assert lora.job is not None
    assert lora.job.id == job.id
    assert [m.uuid for m in job.loras] == ["lora-1"]

    # Parent back-population on the picture set.
    session.refresh(picture_set)
    assert [j.uuid for j in picture_set.training_jobs] == ["job-set-1"]
    assert job.picture_set is not None
    assert job.picture_set.id == picture_set.id


def test_create_character_scoped_job(session):
    character = Character(name="hero")
    session.add(character)
    session.commit()
    session.refresh(character)

    job = TrainingJob(uuid="job-char-1", name="char job", character_id=character.id)
    session.add(job)
    session.commit()
    session.refresh(job)
    session.refresh(character)

    assert job.set_id is None
    assert job.character is not None
    assert job.character.id == character.id
    assert [j.uuid for j in character.training_jobs] == ["job-char-1"]


def test_uuid_is_unique(session):
    session.add(TrainingJob(uuid="dup", name="a"))
    session.commit()
    session.add(TrainingJob(uuid="dup", name="b"))
    with pytest.raises(Exception):
        session.commit()


_AI_TOOLKIT_KEYS = {
    "ai_toolkit_repo_path": "/opt/ai-toolkit",
    "ai_toolkit_python": "/opt/ai-toolkit/.venv/bin/python",
    "ai_toolkit_commit": "abc123",
}


def test_config_round_trip_ai_toolkit_keys():
    user = User()

    updated = apply_user_config_patch(user, dict(_AI_TOOLKIT_KEYS))
    assert updated is True
    for key, value in _AI_TOOLKIT_KEYS.items():
        assert getattr(user, key) == value

    config = serialize_user_config(user)
    for key, value in _AI_TOOLKIT_KEYS.items():
        assert config[key] == value


def test_config_ai_toolkit_keys_clearable():
    user = User()
    apply_user_config_patch(user, dict(_AI_TOOLKIT_KEYS))

    cleared = apply_user_config_patch(user, {key: "" for key in _AI_TOOLKIT_KEYS})
    assert cleared is True
    for key in _AI_TOOLKIT_KEYS:
        assert getattr(user, key) is None


def test_config_ai_toolkit_keys_trimmed():
    user = User()
    apply_user_config_patch(user, {"ai_toolkit_repo_path": "  /opt/ai-toolkit  "})
    assert user.ai_toolkit_repo_path == "/opt/ai-toolkit"
