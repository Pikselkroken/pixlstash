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
from pixlstash.utils.caption_file_utils import suffixes_collide
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
# The folder layout (v1.11 Phase 4b)
# ---------------------------------------------------------------------------


CAPTION_SYNC_FIELDS = (
    "sync_tags",
    "sync_descriptions",
    "tags_suffix",
    "description_suffix",
)


def _caption_fields(row: LibrarySettings) -> dict:
    return {name: getattr(row, name) for name in CAPTION_SYNC_FIELDS}


def _rescan_due(before: dict, after: dict) -> bool:
    """A kind that came on, or whose suffix changed while on, names files
    nothing has read yet; the root scan is the pass that reads them in and
    writes the missing ones out."""
    return any(
        after[toggle] and (not before[toggle] or after[suffix] != before[suffix])
        for toggle, suffix in (
            ("sync_tags", "tags_suffix"),
            ("sync_descriptions", "description_suffix"),
        )
    )


def get_caption_sync(vault_db) -> dict:
    """The root's caption-file sync settings, the four `CAPTION_SYNC_FIELDS`."""
    return vault_db.run_immediate_read_task(
        lambda session: _caption_fields(_row(session))
    )


def set_caption_sync(vault_db, **fields) -> tuple[dict, bool]:
    """Store the given `CAPTION_SYNC_FIELDS`; a field not passed keeps its value.

    Returns ``(stored settings, rescan due)``. Suffixes are trusted here: the
    route validates them at its boundary and `sidecar_path` refuses an unsafe
    one at the point of use.

    Raises:
        ValueError: The merged row would give both kinds one suffix.
    """
    unknown = set(fields) - set(CAPTION_SYNC_FIELDS)
    if unknown:
        raise ValueError(f"unknown caption sync fields: {sorted(unknown)}")

    def write(session: Session) -> tuple[dict, bool]:
        row = _row(session)
        before = _caption_fields(row)
        after = {**before, **fields}
        if suffixes_collide(after["tags_suffix"], after["description_suffix"]):
            raise ValueError(
                "Tags and descriptions cannot share a suffix; they would share "
                "one file and overwrite each other."
            )
        for name, value in fields.items():
            setattr(row, name, value)
        session.add(row)
        session.commit()
        return after, _rescan_due(before, after)

    return vault_db.run_task(write, priority=DBPriority.IMMEDIATE)


def seed_caption_suffixes(
    vault_db, tags_suffix: Optional[str], description_suffix: Optional[str]
) -> bool:
    """Turn a kind on with the suffix the owner confirmed on import.

    Confirming a pattern as tags or descriptions is the owner saying the folder
    stores its captions there, so sync of that kind goes on: edits reach the
    files and files edited on disk reach PixlStash. A suffix already stored is
    kept (an earlier import's convention wins); a kind not confirmed is left
    alone; a kind whose suffix would name the other kind's file is skipped and
    logged. Settings can turn either off again.

    Returns whether a root rescan is due, by `set_caption_sync`'s rule.
    """
    if not tags_suffix and not description_suffix:
        return False

    def write(session: Session) -> bool:
        row = _row(session)
        before = _caption_fields(row)
        for toggle, field, confirmed in (
            ("sync_tags", "tags_suffix", tags_suffix),
            ("sync_descriptions", "description_suffix", description_suffix),
        ):
            if not confirmed:
                continue
            effective = getattr(row, field) or confirmed
            other = (
                row.description_suffix if field == "tags_suffix" else row.tags_suffix
            )
            pair = (effective, other) if field == "tags_suffix" else (other, effective)
            if suffixes_collide(*pair):
                logger.warning(
                    "Not turning %s on for the library root: suffix %r would name "
                    "the same file as the other kind's.",
                    toggle,
                    effective,
                )
                continue
            setattr(row, toggle, True)
            setattr(row, field, effective)
        session.add(row)
        session.commit()
        return _rescan_due(before, _caption_fields(row))

    return vault_db.run_task(write, priority=DBPriority.IMMEDIATE)


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
