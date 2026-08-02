"""Read and write the active library's own settings.

Thin on purpose: exactly one setting lives here (``similarity_character``), and
the value of this module is that the *routing* decision is in one place. A
future setting that belongs to a library goes here rather than growing another
special case in the config handler.

The single row is created by migration 0092, so these helpers never create it in
the normal path; the fallback exists for a vault that somehow reaches them
without one rather than as an expected branch.
"""

from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models.library_settings import LibrarySettings
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)


def _row(session: Session) -> LibrarySettings:
    """Return the settings row, creating it if a vault somehow lacks one."""
    settings = session.exec(select(LibrarySettings)).first()
    if settings is None:
        logger.warning(
            "This vault has no library_settings row; creating one. Migration "
            "0092 should have done this, so a vault reaching here was likely "
            "restored from before the hub/vault split."
        )
        settings = LibrarySettings()
        session.add(settings)
        session.commit()
        session.refresh(settings)
    return settings


def get_similarity_character(vault_db) -> Optional[int]:
    """Return the character the grid sorts likeness against, for this library.

    Args:
        vault_db: The active library's database.

    Returns:
        A character id **in this vault**, or None when none is selected.
    """
    return vault_db.run_immediate_read_task(
        lambda session: _row(session).similarity_character
    )


def set_similarity_character(vault_db, character_id: Optional[int]) -> None:
    """Point this library's likeness sort at *character_id*.

    Stored per library rather than per user because the value is a row id in
    this vault: the same number in another library is a different person, so a
    per-user copy would silently sort against the wrong face after a switch.
    """

    def _write(session: Session):
        settings = _row(session)
        if settings.similarity_character != character_id:
            settings.similarity_character = character_id
            session.add(settings)
            session.commit()

    vault_db.run_task(_write, priority=DBPriority.IMMEDIATE)
