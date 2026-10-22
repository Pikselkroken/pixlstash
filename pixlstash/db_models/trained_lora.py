from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Column, ForeignKey, Integer
from sqlmodel import Field, Relationship, SQLModel, UTCDateTime

if TYPE_CHECKING:
    from .training_job import TrainingJob


class TrainedLora(SQLModel, table=True):
    """A LoRA artifact produced by a training job.

    The ``set_id`` / ``character_id`` columns are denormalised from the
    parent :class:`TrainingJob` so scoped listing (e.g. "all LoRAs for this
    character") can filter without joining back through the job table.

    Attributes:
        id: Primary key.
        uuid: Stable external identifier (indexed, unique).
        job_id: FK to the training job that produced this LoRA.
        set_id: Denormalised picture-set scope from the job (nullable).
        character_id: Denormalised character scope from the job (nullable).
        name: Human-readable LoRA name.
        file_path: Path relative to the vault image-root, e.g.
            ``loras/{uuid}.safetensors``.
        file_size_bytes: Size of the artifact on disk.
        file_sha: Content hash of the artifact.
        step: Training step the artifact was saved at.
        created_at: UTC creation timestamp.
        job: The training job that produced this LoRA.
    """

    __tablename__ = "trained_lora"

    id: Optional[int] = Field(default=None, primary_key=True)

    uuid: str = Field(index=True, unique=True, nullable=False)

    job_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("training_job.id"),
            index=True,
            nullable=True,
        ),
    )
    set_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("pictureset.id"),
            index=True,
            nullable=True,
        ),
    )
    character_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("character.id"),
            index=True,
            nullable=True,
        ),
    )

    name: str = Field(nullable=False)
    file_path: Optional[str] = Field(default=None)
    file_size_bytes: Optional[int] = Field(default=None)
    file_sha: Optional[str] = Field(default=None)
    step: Optional[int] = Field(default=None)
    created_at: datetime = Field(
        sa_column=Column(UTCDateTime(), nullable=False),
        default_factory=lambda: datetime.now(timezone.utc),
    )

    job: Optional["TrainingJob"] = Relationship(
        back_populates="loras",
        sa_relationship_kwargs={"foreign_keys": "[TrainedLora.job_id]"},
    )
