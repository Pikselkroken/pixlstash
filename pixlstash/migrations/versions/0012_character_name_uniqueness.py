"""enforce unique character names within a project (case-insensitive)

Revision ID: 0012_character_name_uniqueness
Revises: 0011_pictureset_name_uniqueness
Create Date: 2026-03-29 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012_character_name_uniqueness"
down_revision: Union[str, None] = "0011_pictureset_name_uniqueness"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "character" not in set(inspector.get_table_names()):
        return

    # Deduplicate character names within the same project.
    rows = bind.execute(
        sa.text(
            "SELECT id, name, project_id FROM character "
            "WHERE project_id IS NOT NULL ORDER BY id"
        )
    ).fetchall()

    seen: dict[tuple, str] = {}  # (project_id, lower_name) -> canonical name
    for row in rows:
        char_id = int(row.id)
        raw_name = (row.name or "").strip() or f"Character {char_id}"
        project_id = int(row.project_id)
        key = (project_id, raw_name.lower())

        if key not in seen:
            seen[key] = raw_name
            if raw_name != row.name:
                bind.execute(
                    sa.text("UPDATE character SET name = :name WHERE id = :id"),
                    {"id": char_id, "name": raw_name},
                )
        else:
            base_name = raw_name
            counter = 2
            while True:
                candidate = f"{base_name} ({counter})"
                candidate_key = (project_id, candidate.lower())
                if candidate_key not in seen:
                    seen[candidate_key] = candidate
                    bind.execute(
                        sa.text("UPDATE character SET name = :name WHERE id = :id"),
                        {"id": char_id, "name": candidate},
                    )
                    break
                counter += 1

    # Composite unique index on (project_id, lower(name)).
    # NULL project_id rows are unaffected (SQLite NULL distinctness).
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("character")}
    if "ux_character_name_project_ci" not in existing_indexes:
        op.create_index(
            "ux_character_name_project_ci",
            "character",
            ["project_id", sa.text("lower(name)")],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "character" not in set(inspector.get_table_names()):
        return

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("character")}
    if "ux_character_name_project_ci" in existing_indexes:
        op.drop_index("ux_character_name_project_ci", table_name="character")
