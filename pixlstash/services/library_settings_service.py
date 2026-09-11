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

from sqlalchemy import update as sa_update
from sqlmodel import Session, select

from pixlstash.database import DBPriority
from pixlstash.db_models.library_settings import LibrarySettings
from pixlstash.db_models.picture import Picture
from pixlstash.utils.caption_file_utils import (
    DEFAULT_DESCRIPTION_SUFFIX,
    DEFAULT_TAGS_SUFFIX,
    suffixes_collide,
)
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


#: The picture columns each toggle governs: ``(recorded file, its mtime)``.
_CAPTION_MTIME_COLUMNS = {
    "sync_tags": ("tags_file", "tags_file_mtime"),
    "sync_descriptions": ("description_file", "description_file_mtime"),
}

#: The suffix field each toggle names its files with.
_CAPTION_SUFFIX_FIELDS = {
    "sync_tags": "tags_suffix",
    "sync_descriptions": "description_suffix",
}


def _forget_caption_mtimes(session: Session, toggle: str) -> None:
    """Make the next root scan re-read the caption files *toggle* governs.

    A picture indexed while sync was off can already carry a recorded file of
    that kind and the mtime it had when it was read, and its tags or
    description may have been edited in PixlStash since. Turning the kind on
    queues a root scan, but `_reconcile_sidecar` gates the read on
    ``(path, mtime)`` and would see neither changed: it imports nothing and
    exports nothing, and the file and the database stay divergent for good.

    Forgetting the mtime makes that scan see the file as changed on disk and
    read it in, which is the recorded-file rule - the file wins - and the same
    outcome as a file edited while PixlStash was closed. Only the root's own
    pictures: a reference folder's rows are that folder's toggle to manage.
    """
    file_column, mtime_column = _CAPTION_MTIME_COLUMNS[toggle]
    table = Picture.__table__
    session.execute(
        sa_update(table)
        .where(
            table.c.reference_folder_id.is_(None),
            table.c[file_column].is_not(None),
        )
        .values({mtime_column: None})
    )


def get_caption_sync(vault_db) -> dict:
    """The root's caption-file sync settings: the four `CAPTION_SYNC_FIELDS`."""

    def read(session: Session) -> dict:
        row = _row(session)
        return {name: getattr(row, name) for name in CAPTION_SYNC_FIELDS}

    return vault_db.run_immediate_read_task(read)


def set_caption_sync(vault_db, validate=None, **fields) -> tuple[dict, bool]:
    """Store the given `CAPTION_SYNC_FIELDS`; a field not passed keeps its value.

    Returns ``(stored settings, rescan due)``. The suffixes are trusted here:
    the route validates them at its boundary and `sidecar_path` refuses an
    unsafe one at the point of use, the same two doors a reference folder's go
    through.

    *rescan due* is True when this write left a kind on that was off, or
    changed the suffix of a kind that is on - a different set of files, none of
    them read yet. It is decided inside the writer, against the row this write
    actually replaced, because a caller comparing against a separately read
    snapshot can miss the transition: two PATCHes read suffix X, one writes Y
    and scans, the other writes X back and sees no change against its stale
    snapshot, so the state on disk is never read. An extra idempotent rescan is
    cheap; a dropped one leaves the files unread for good.

    *validate*, when given, is called with the MERGED row - the stored values
    with *fields* applied - inside the writer task, before anything is set,
    and raises ``ValueError`` to refuse. A cross-field rule checked against a
    separately read snapshot is a race: two partial PATCHes can each pass
    against the old row and serialise into the state the rule forbids.

    A toggle going off -> on also forgets that kind's recorded mtimes
    (`_forget_caption_mtimes`), in this same writer, so the scan the caller
    queues actually reads the existing files in.
    """
    unknown = set(fields) - set(CAPTION_SYNC_FIELDS)
    if unknown:
        raise ValueError(f"unknown caption sync fields: {sorted(unknown)}")

    def write(session: Session) -> tuple[dict, bool]:
        row = _row(session)
        merged = {name: getattr(row, name) for name in CAPTION_SYNC_FIELDS}
        merged.update(fields)
        if validate is not None:
            validate(merged)
        turned_on = [
            toggle
            for toggle in _CAPTION_MTIME_COLUMNS
            if fields.get(toggle) and not getattr(row, toggle)
        ]
        rescan_due = any(
            merged[toggle]
            and (
                not getattr(row, toggle)
                or merged[suffix_field] != getattr(row, suffix_field)
            )
            for toggle, suffix_field in _CAPTION_SUFFIX_FIELDS.items()
        )
        for name, value in fields.items():
            setattr(row, name, value)
        for toggle in turned_on:
            _forget_caption_mtimes(session, toggle)
        session.add(row)
        session.commit()
        session.refresh(row)
        stored = {name: getattr(row, name) for name in CAPTION_SYNC_FIELDS}
        return stored, rescan_due

    return vault_db.run_task(write, priority=DBPriority.IMMEDIATE)


def seed_caption_suffixes(
    vault_db, tags_suffix: Optional[str], description_suffix: Optional[str]
) -> bool:
    """Apply the owner's caption answers to the root's sync settings.

    Confirming a pattern as tags or as descriptions on the import screen is
    the owner saying the folder stores its captions in those files, so sync of
    that kind is turned ON with the confirmed suffix - an edit in PixlStash
    reaches the file, a file edited on disk reaches PixlStash, and a picture
    that has content but no file gets one on the next root scan. A suffix
    already set is kept (an earlier import's convention wins); the toggle is
    turned on either way. Nothing is changed for a kind the owner did not
    confirm, and Settings can turn either off again.

    A kind is skipped entirely - toggle unchanged, column left as it was, the
    reason logged - when its effective suffix would name the same file as the
    other kind's (``caption_file_utils.suffixes_collide``). Otherwise a second
    import could seed descriptions with the suffix tags already use and point
    both write-backs at one file.

    Turning a kind on forgets that kind's recorded mtimes on the root's
    pictures (`_forget_caption_mtimes`), so that rescan reads the files in
    rather than deciding nothing changed.

    Returns:
        True when this write left a kind on that was off, or changed the
        suffix of a kind that is on - the same rule `set_caption_sync` uses -
        so the caller can ask for the root rescan that reads the existing
        files in before anything is written back over them. A repeat import
        of a convention already confirmed changes nothing and returns False,
        rather than walking the whole library again for files already read.
    """
    if not tags_suffix and not description_suffix:
        return False

    def write(session: Session) -> bool:
        row = _row(session)
        rescan_due = False
        # Judged on the merged row, kind by kind: the effective suffix is the
        # stored one when there is one (an earlier convention wins) and the
        # confirmed one otherwise, and descriptions see whatever tags seeded.
        if tags_suffix:
            effective = row.tags_suffix or tags_suffix
            if suffixes_collide(effective, row.description_suffix):
                logger.warning(
                    "Not turning tag sync on for the library root: tags suffix "
                    "%r would name the same file as description suffix %r.",
                    effective,
                    row.description_suffix or DEFAULT_DESCRIPTION_SUFFIX,
                )
            else:
                if not row.sync_tags:
                    _forget_caption_mtimes(session, "sync_tags")
                # Same rule as `set_caption_sync`, and judged before the row
                # is written: a kind already on with this very suffix is a
                # set of files already read, not a reason to walk the library.
                rescan_due = (
                    rescan_due or not row.sync_tags or effective != row.tags_suffix
                )
                row.sync_tags = True
                row.tags_suffix = effective
        if description_suffix:
            effective = row.description_suffix or description_suffix
            if suffixes_collide(row.tags_suffix, effective):
                logger.warning(
                    "Not turning description sync on for the library root: "
                    "description suffix %r would name the same file as tags "
                    "suffix %r.",
                    effective,
                    row.tags_suffix or DEFAULT_TAGS_SUFFIX,
                )
            else:
                if not row.sync_descriptions:
                    _forget_caption_mtimes(session, "sync_descriptions")
                rescan_due = (
                    rescan_due
                    or not row.sync_descriptions
                    or effective != row.description_suffix
                )
                row.sync_descriptions = True
                row.description_suffix = effective
        session.add(row)
        session.commit()
        return rescan_due

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
