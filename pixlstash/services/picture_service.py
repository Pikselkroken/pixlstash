"""DB-layer helpers for single-picture reads from route handlers.

These functions accept a *Database* instance (``vault.db``) and delegate
session management to it, keeping direct ``vault.db`` calls out of route
handlers.
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from pixlstash.db_models import Picture


def fetch_picture_file_path(db, picture_id: int) -> Optional[str]:
    """Return the stored ``file_path`` of a non-deleted picture, or ``None``.

    Args:
        db: The ``vault.db`` Database instance.
        picture_id: The picture id to look up.

    Returns:
        The relative ``file_path`` string, or ``None`` when no matching
        non-deleted picture exists.
    """

    def _fetch(session: Session):
        return session.exec(
            select(Picture.file_path).where(
                Picture.id == picture_id,
                Picture.deleted.is_(False),
            )
        ).first()

    return db.run_immediate_read_task(_fetch)
