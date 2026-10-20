from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Column, ForeignKey, Integer
from sqlmodel import Field, Relationship, SQLModel, UTCDateTime

if TYPE_CHECKING:
    from .character import Character
    from .picture_set import PictureSet
    from .trained_lora import TrainedLora


class TrainingJob(SQLModel, table=True):
    """A single ai-toolkit LoRA training run and its live progress.

    A job trains a LoRA either for a picture set or for a character, never
    both: exactly one of ``set_id`` / ``character_id`` is populated. This
    invariant is enforced by the service layer that creates jobs, not by a
    database constraint, so a half-built row never trips a CHECK during
    multi-step construction.

    The progress fields (``status``, ``step``, ``loss_curve``,
    ``sample_image_paths``, ``log_tail``) are mutated in place while the
    background trainer runs; the terminal fields (``report``, ``error``,
    ``completed_at``) are written once when the run finishes or fails.

    Attributes:
        id: Primary key.
        uuid: Stable external identifier (indexed, unique).
        name: Human-readable job name.
        status: queued | running | stopping | stopped | completed | error.
        set_id: FK to the picture set being trained on (nullable).
        character_id: FK to the character being trained on (nullable).
        base_model: Base checkpoint the LoRA is trained against.
        trigger_word: Caption trigger word baked into the dataset.
        rendered_config: Full ai-toolkit config JSON used for the run.
        dataset_snapshot_path: Path to the frozen dataset snapshot.
        dataset_picture_ids: JSON list of picture ids in the dataset.
        ai_toolkit_commit: ai-toolkit git commit the run was launched with.
        training_folder: Working/output folder for this run.
        pid: OS process id of the running trainer (nullable).
        step: Latest reported training step.
        total_steps: Planned total number of training steps.
        loss_curve: JSON list of {"step", "loss"} points.
        sample_image_paths: JSON list of sample image paths.
        log_tail: Most recent trainer log output.
        report: Final human-readable run report.
        error: Error message when status is "error".
        created_at: UTC creation timestamp.
        started_at: UTC timestamp when the run started.
        completed_at: UTC timestamp when the run finished.
        picture_set: The picture set this job trains on (when set-scoped).
        character: The character this job trains on (when character-scoped).
        loras: Trained LoRA artifacts produced by this job.
    """

    __tablename__ = "training_job"

    id: Optional[int] = Field(default=None, primary_key=True)

    uuid: str = Field(index=True, unique=True, nullable=False)
    name: str = Field(nullable=False)
    status: str = Field(default="queued", index=True)

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

    base_model: Optional[str] = Field(default=None)
    trigger_word: Optional[str] = Field(default=None)
    rendered_config: Optional[str] = Field(default=None)
    dataset_snapshot_path: Optional[str] = Field(default=None)
    dataset_picture_ids: Optional[str] = Field(default=None)
    ai_toolkit_commit: Optional[str] = Field(default=None)
    training_folder: Optional[str] = Field(default=None)
    pid: Optional[int] = Field(default=None)
    step: int = Field(default=0)
    total_steps: Optional[int] = Field(default=None)
    loss_curve: Optional[str] = Field(default=None)
    sample_image_paths: Optional[str] = Field(default=None)
    log_tail: Optional[str] = Field(default=None)
    report: Optional[str] = Field(default=None)
    error: Optional[str] = Field(default=None)
    created_at: datetime = Field(
        sa_column=Column(UTCDateTime(), nullable=False),
        default_factory=lambda: datetime.now(timezone.utc),
    )
    started_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)

    picture_set: Optional["PictureSet"] = Relationship(
        back_populates="training_jobs",
        sa_relationship_kwargs={"foreign_keys": "[TrainingJob.set_id]"},
    )
    character: Optional["Character"] = Relationship(
        back_populates="training_jobs",
        sa_relationship_kwargs={"foreign_keys": "[TrainingJob.character_id]"},
    )
    loras: List["TrainedLora"] = Relationship(
        back_populates="job",
        sa_relationship_kwargs={"foreign_keys": "[TrainedLora.job_id]"},
    )
