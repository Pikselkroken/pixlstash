"""Content-hash matching for the import paths: Scrapheap included.

**Why this module exists.** Import de-duplication used to ask only "is there a
LIVE picture with this ``pixel_sha``?". ``Picture.find`` defaults
``include_deleted=False``, and the one-shot import called it that way
(``routes/pictures/_helpers.py``), so **a scrapheaped picture was invisible to
import dedup**: re-importing a file whose picture sits in the Scrapheap created a
brand-new second row while the original was still there, doubling the bytes on
disk and refilling the duplicate queue.

That was a rare annoyance while the Scrapheap held a handful of pictures. It
stops being rare the moment a bulk cleanup ("Keep cover only") puts hundreds of
pictures there in one gesture: those are *by definition* copies of files that
still exist wherever the user imports from, so the next import silently undoes
the cleanup.

**The rule this module encodes.** ``pixel_sha`` is a sampled candidate key, not
proof of identity. Candidates must also have the same byte size and pass a
full-file SHA-256 comparison. A confirmed match is one of exactly two things,
and the import must be able to tell them apart:

* a **live** match; the file is already in the library. Nothing to do; it is a
  duplicate, exactly as before.
* a **scrapheaped** match, the row exists but is soft-deleted. The file is NOT
  imported again (that is the doubling bug), and it is NOT reported as an
  ordinary duplicate either: the user deliberately scrapheaped it, so silently
  restoring would be its own surprise. It is reported as a third, distinct
  outcome and the caller is offered a restore.

**Live wins over scrapheaped for the same hash.** Both can exist today,
precisely because the bug above already created second rows and a live row
means the content IS in the library, so "duplicate" is the honest answer.

**A permanently purged file is not a match, by construction.** Delete-forever
removes the ``picture`` row outright (``scrapheap_service.purge_rows_in_session``)
and records the path/content in ``deleted_file_log``. With no row there is
nothing to match and nothing to restore, so a deliberate re-import of a purged
file is a genuinely NEW picture. The ledger deliberately is NOT consulted here:
it exists to stop a *snapshot restore* resurrecting rows the user destroyed
(``services/restore/full_restore.py``), not to refuse the user's own fresh
import of a file they still have.

**Blast radius.** Nothing here changes ``Picture.find``'s ``include_deleted``
default. The queries below are the import paths' own, so widening the match to
soft-deleted rows cannot leak deleted pictures into any listing, search, count,
export or dedup query that relies on the existing exclusion.
"""

from dataclasses import dataclass
from typing import Iterable, Optional

from sqlmodel import Session, select

from pixlstash.db_models import Picture
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.image_processing.image_utils import ImageUtils


logger = get_logger(__name__)

# SQLite caps the number of bound parameters per statement; the import batches
# can be thousands of files, so the ``IN`` list is chunked.
_SHA_QUERY_CHUNK = 500


@dataclass(frozen=True)
class ShaMatch:
    """An existing picture that already holds an incoming file's content.

    Deliberately a value object rather than a live ``Picture``: the callers only
    need identity and the on-disk path for their result rows, and the object
    outlives the read session that produced it.

    Attributes:
        id: The matching picture's id.
        pixel_sha: The content hash both sides share.
        size_bytes: The byte-size co-key both sides share.
        file_path: The matching picture's vault-relative path, when it has one.
        deleted: True when the match is soft-deleted (in the Scrapheap).
    """

    id: int
    pixel_sha: str
    size_bytes: int
    file_path: Optional[str]
    deleted: bool


ContentKey = tuple[str, int]
ContentFingerprint = tuple[str, int, str]


def load_match_candidates_in_session(
    session: Session,
    keys: Iterable[ContentKey],
    include_deleted: bool = True,
) -> dict[ContentKey, list[ShaMatch]]:
    """Load possible matches by the cheap ``(sampled hash, size)`` key.

    This function performs no filesystem IO. Callers confirm candidates with
    :func:`partition_confirmed_matches` after the database session has closed.
    A sampled digest is deliberately never returned as a match by itself.
    """
    wanted = {(str(sha), int(size)) for sha, size in keys if sha and size >= 0}
    candidates: dict[ContentKey, list[ShaMatch]] = {}
    if not wanted:
        return candidates

    wanted_shas = sorted({sha for sha, _size in wanted})
    for start in range(0, len(wanted_shas), _SHA_QUERY_CHUNK):
        chunk = wanted_shas[start : start + _SHA_QUERY_CHUNK]
        query = select(
            Picture.id,
            Picture.pixel_sha,
            Picture.size_bytes,
            Picture.file_path,
            Picture.deleted,
        ).where(Picture.pixel_sha.in_(chunk))
        if not include_deleted:
            query = query.where(Picture.deleted.is_(False))
        rows = session.exec(query.order_by(Picture.id)).all()
        for row_id, pixel_sha, size_bytes, file_path, deleted in rows:
            if row_id is None or not pixel_sha or size_bytes is None:
                continue
            key = (str(pixel_sha), int(size_bytes))
            if key not in wanted:
                continue
            match = ShaMatch(
                id=int(row_id),
                pixel_sha=str(pixel_sha),
                size_bytes=int(size_bytes),
                file_path=file_path,
                deleted=bool(deleted),
            )
            candidates.setdefault(key, []).append(match)
    return candidates


def partition_confirmed_matches(
    candidates: dict[ContentKey, list[ShaMatch]],
    fingerprints: Iterable[ContentFingerprint],
    image_root: str,
) -> tuple[dict[ContentFingerprint, ShaMatch], dict[ContentFingerprint, ShaMatch]]:
    """Confirm sampled candidates with a full-file SHA-256 comparison.

    The returned maps are keyed by ``(sampled hash, size, full hash)`` so two
    incoming files deliberately sharing a sampled key cannot overwrite one
    another in a batch map. A live confirmed row wins over a scrapheaped one.
    Missing or unreadable candidate files are conservatively treated as not a
    match: import may create a recoverable extra row, but never discards the
    incoming file on evidence it could not verify.
    """
    wanted = {
        (str(sampled), int(size), str(full_sha))
        for sampled, size, full_sha in fingerprints
        if sampled and full_sha and size >= 0
    }
    live: dict[ContentFingerprint, ShaMatch] = {}
    scrapheaped: dict[ContentFingerprint, ShaMatch] = {}
    full_hash_cache: dict[int, Optional[str]] = {}

    for fingerprint in wanted:
        sampled, size_bytes, full_sha = fingerprint
        confirmed_deleted: Optional[ShaMatch] = None
        for match in candidates.get((sampled, size_bytes), []):
            if match.id not in full_hash_cache:
                resolved = ImageUtils.resolve_picture_path(image_root, match.file_path)
                try:
                    full_hash_cache[match.id] = (
                        ImageUtils.calculate_full_hash_from_file_path(resolved)
                        if resolved
                        else None
                    )
                except OSError as exc:
                    logger.warning(
                        "Import dedup: could not confirm candidate picture %d at %r: %s; "
                        "treating the incoming file as new.",
                        match.id,
                        resolved,
                        exc,
                    )
                    full_hash_cache[match.id] = None
            if full_hash_cache[match.id] != full_sha:
                continue
            if not match.deleted:
                live[fingerprint] = match
                confirmed_deleted = None
                break
            if confirmed_deleted is None:
                confirmed_deleted = match
        if fingerprint not in live and confirmed_deleted is not None:
            scrapheaped[fingerprint] = confirmed_deleted

    if scrapheaped:
        logger.info(
            "Import dedup: %d fully-confirmed incoming file(s) match scrapheaped "
            "picture(s) %s: they will NOT be imported again and are reported "
            "as a restorable outcome, not as ordinary duplicates.",
            len(scrapheaped),
            sorted({m.id for m in scrapheaped.values()}),
        )
    return live, scrapheaped


def confirmed_match(
    candidates: dict[ContentKey, list[ShaMatch]],
    fingerprint: ContentFingerprint,
    image_root: str,
) -> Optional[ShaMatch]:
    """Return one fully-confirmed match, preferring a live row."""
    live, scrapheaped = partition_confirmed_matches(
        candidates, [fingerprint], image_root
    )
    return live.get(fingerprint) or scrapheaped.get(fingerprint)
