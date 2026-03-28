"""enforce unique project names (case-insensitive)

Revision ID: 0010_project_name_uniqueness
Revises: 0009_add_picture_project_membership
Create Date: 2026-03-28 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010_project_name_uniqueness"
down_revision: Union[str, None] = "0009_add_picture_project_membership"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

__all__ = ["revision", "down_revision", "branch_labels", "depends_on"]


def _normalise_name(raw_name: str, fallback_id: int) -> str:
    value = (raw_name or "").strip()
    if not value:
        value = f"Project {fallback_id}"
    return value


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "project" not in set(inspector.get_table_names()):
        return

    rows = bind.execute(sa.text("SELECT id, name FROM project ORDER BY id")).fetchall()

    seen: set[str] = set()
    updates: list[tuple[int, str]] = []
    for row in rows:
        project_id = int(row.id)
        base_name = _normalise_name(row.name, project_id)
        candidate = base_name
        counter = 2
        while candidate.lower() in seen:
            candidate = f"{base_name} ({counter})"
            counter += 1
        seen.add(candidate.lower())
        if candidate != row.name:
            updates.append((project_id, candidate))

    if updates:
        for project_id, project_name in updates:
            bind.execute(
                sa.text("UPDATE project SET name = :name WHERE id = :id"),
                {"id": project_id, "name": project_name},
            )

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("project")}
    if "ux_project_name_ci" not in existing_indexes:
        op.create_index(
            "ux_project_name_ci",
            "project",
            [sa.text("lower(name)")],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "project" not in set(inspector.get_table_names()):
        return

    existing_indexes = {idx["name"] for idx in inspector.get_indexes("project")}
    if "ux_project_name_ci" in existing_indexes:
        op.drop_index("ux_project_name_ci", table_name="project")
