"""enforce unique picture set names within a project (case-insensitive)

Revision ID: 0011_pictureset_name_uniqueness
Revises: 0010_project_name_uniqueness
Create Date: 2026-03-29 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011_pictureset_name_uniqueness"
down_revision: Union[str, None] = "0010_project_name_uniqueness"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "pictureset" not in set(inspector.get_table_names()):
        return

    # Deduplicate names within the same project (project_id IS NOT NULL only —
    # unscoped sets have no uniqueness constraint so no dedup needed).
    rows = bind.execute(
        sa.text(
            "SELECT id, name, project_id FROM pictureset "
            "WHERE project_id IS NOT NULL ORDER BY id"
        )
    ).fetchall()

    # Group by (project_id, lower(name)) and rename duplicates.
    seen: dict[tuple, str] = {}  # (project_id, lower_name) -> first canonical name
    for row in rows:
        set_id = int(row.id)
        raw_name = (row.name or "").strip() or f"Set {set_id}"
        project_id = int(row.project_id)
        key = (project_id, raw_name.lower())

        if key not in seen:
            seen[key] = raw_name
            if raw_name != row.name:
                bind.execute(
                    sa.text("UPDATE pictureset SET name = :name WHERE id = :id"),
                    {"id": set_id, "name": raw_name},
                )
        else:
            # Rename this duplicate to something unique within the project.
            base_name = raw_name
            counter = 2
            while True:
                candidate = f"{base_name} ({counter})"
                candidate_key = (project_id, candidate.lower())
                if candidate_key not in seen:
                    seen[candidate_key] = candidate
                    bind.execute(
                        sa.text("UPDATE pictureset SET name = :name WHERE id = :id"),
                        {"id": set_id, "name": candidate},
                    )
                    break
                counter += 1

    # Create composite unique index on (project_id, lower(name)).
    # SQLite treats NULL as distinct from every other NULL, so sets without a
    # project (project_id IS NULL) are unaffected by this constraint.
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("pictureset")}
    if "ux_pictureset_name_project_ci" not in existing_indexes:
        op.create_index(
            "ux_pictureset_name_project_ci",
            "pictureset",
            ["project_id", sa.text("lower(name)")],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "pictureset" not in set(inspector.get_table_names()):
        return

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("pictureset")}
    if "ux_pictureset_name_project_ci" in existing_indexes:
        op.drop_index("ux_pictureset_name_project_ci", table_name="pictureset")
