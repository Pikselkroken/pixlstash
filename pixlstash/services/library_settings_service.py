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

import hashlib
import hmac
import json
from typing import Optional

from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models.library_settings import LibrarySettings
from pixlstash.utils.service.smart_score_invalidation import invalidate_all_smart_scores
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


# ---------------------------------------------------------------------------
# Settings fingerprint (hub -> library)
# ---------------------------------------------------------------------------


def compute_settings_fingerprint(salt: str, penalised_tags: dict) -> str:
    """Return an opaque, keyed hash of the score-affecting settings.

    **Deliberately one-way and keyed, because the inputs are personal.** The
    penalised-tag table says what someone considers a defect, and hidden tags say
    what they keep off their own screen; both are information about the person,
    not about the pictures. A library folder is made to be copied, moved and
    handed to other people, so nothing derived from those settings may be
    recoverable from it. A tag vocabulary is small and guessable, so a plain hash
    would fall to a dictionary attack; the salt lives in the hub and never
    travels with the library, which is what makes the stored value meaningless on
    its own.

    Args:
        salt: The library's ``settings_salt`` from the hub.
        penalised_tags: The resolved ``{tag: weight}`` table.

    Returns:
        A hex digest. Equality is the only thing callers may infer from it.
    """
    canonical = json.dumps(
        {str(tag): float(weight) for tag, weight in sorted(penalised_tags.items())},
        separators=(",", ":"),
        sort_keys=True,
    )
    return hmac.new(
        salt.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def reconcile_settings_fingerprint(vault_db, salt: str, penalised_tags: dict) -> bool:
    """Invalidate this library's cached scores if the weights changed while it slept.

    A penalised-tag weight change invalidates cached smart scores in the library
    that is *open at the time*. A library that was closed then never learns, and
    nothing revisits its scores, because NULL is the only signal that a recompute
    is owed. This closes that gap at the one moment the answer is knowable: when
    the library is opened.

    On a mismatch it invalidates **every** cached score in the library rather
    than the pictures carrying the changed tags. That is the price of storing no
    settings detail: with only a keyed hash there is no way to compute a narrow
    diff, and privacy is worth more here than the extra recompute, which runs in
    the background and re-runs no AI models.

    Args:
        vault_db: The library's database.
        salt: The library's ``settings_salt`` from the hub.
        penalised_tags: The owner's current resolved ``{tag: weight}`` table.

    Returns:
        True when the fingerprint had changed and an invalidation was recorded.
    """
    if not salt:
        # No salt means an unkeyed hash, which would be recoverable from the
        # library. Skip rather than write something weaker than promised.
        logger.warning(
            "No settings salt for this library; skipping the fingerprint check. "
            "Scores stay as they are."
        )
        return False

    expected = compute_settings_fingerprint(salt, penalised_tags or {})

    def _reconcile(session: Session) -> bool:
        settings = _row(session)
        if settings.settings_fingerprint == expected:
            return False

        first_time = settings.settings_fingerprint is None
        settings.settings_fingerprint = expected
        session.add(settings)

        if first_time:
            # Nothing to repair: this library has simply never recorded one.
            session.commit()
            return False

        invalidate_all_smart_scores(session)
        session.commit()
        return True

    changed = vault_db.run_task(_reconcile, priority=DBPriority.IMMEDIATE)
    if changed:
        logger.info(
            "The penalised-tag weights changed while this library was closed; "
            "its cached smart scores have been invalidated and will be "
            "recomputed in the background."
        )
    return changed


# ---------------------------------------------------------------------------
# PixlStash Views (v1.11 Phase 7)
# ---------------------------------------------------------------------------


def get_views_config(vault_db) -> tuple[Optional[str], list[str]]:
    """Return ``(views_root, kinds)`` for this library.

    Stored per library rather than per user because the folder holds *this*
    library's people and sets: two libraries publishing into one folder would
    overwrite each other's tree.
    """

    def _read(session: Session) -> tuple[Optional[str], list[str]]:
        row = _row(session)
        raw = row.views_kinds or ""
        return row.views_root, [kind for kind in raw.split(",") if kind]

    return vault_db.run_immediate_read_task(_read)


def set_views_config(vault_db, root: Optional[str], kinds: list[str]) -> None:
    """Record where this library publishes views and which kinds it publishes."""
    serialised = ",".join(kinds)

    def _write(session: Session):
        row = _row(session)
        if row.views_root != root or (row.views_kinds or "") != serialised:
            row.views_root = root
            row.views_kinds = serialised
            session.add(row)
            session.commit()

    vault_db.run_task(_write, priority=DBPriority.IMMEDIATE)


# ---------------------------------------------------------------------------
# The folder layout (v1.11 Phase 4b)
# ---------------------------------------------------------------------------


def get_layout(vault_db) -> tuple[Optional[str], Optional[str]]:
    """Return ``(layout, unfiled)`` for this library's own picture root.

    ``(None, None)`` means the root has no layout, which is every library until
    its owner picks one - and while it has none, nothing is ever placed by the
    layout and nothing is ever moved by it.

    Per library rather than per user because it describes this library's own
    folder tree: the same segments applied to somebody's other library would
    name folders that are not there.
    """

    def _read(session: Session) -> tuple[Optional[str], Optional[str]]:
        row = _row(session)
        return row.layout, row.layout_unfiled

    return vault_db.run_immediate_read_task(_read)


def set_layout(vault_db, layout: Optional[str], unfiled: Optional[str]) -> None:
    """Record this library's layout. Validated by the caller, not here."""

    def _write(session: Session):
        row = _row(session)
        if row.layout != layout or row.layout_unfiled != unfiled:
            row.layout = layout
            row.layout_unfiled = unfiled
            session.add(row)
            session.commit()

    vault_db.run_task(_write, priority=DBPriority.IMMEDIATE)
