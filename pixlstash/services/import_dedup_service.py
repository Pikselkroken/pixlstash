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
  imported again (that is the doubling bug). Re-importing it means the user
  wants it back, so the import paths restore the picture and report it as a
  third, distinct outcome rather than an ordinary duplicate.

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

**The confirm reads a stored hash when it can.** Confirming a candidate means
the full-file SHA-256 of the library file, and reading that file was most of
what a re-imported duplicate cost. ``picture.full_sha`` caches it, guarded by
``picture.full_sha_stat`` (:func:`stat_signature`): one ``stat`` decides
whether the stored value still describes the file. The columns fill lazily,
from each new picture's incoming hash and from every confirm that had to read
(``learned``), so there is no upgrade-time read of the library. On Windows the
stored value is written but never trusted (:func:`_candidate_full_sha`).
"""

import os
from dataclasses import dataclass
from typing import Iterable, Optional

from sqlalchemy import update
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
        full_sha: The stored full-file SHA-256, or ``None`` when not yet known.
        full_sha_stat: The :func:`stat_signature` ``full_sha`` was taken under.
    """

    id: int
    pixel_sha: str
    size_bytes: int
    file_path: Optional[str]
    deleted: bool
    full_sha: Optional[str] = None
    full_sha_stat: Optional[str] = None


ContentKey = tuple[str, int]
ContentFingerprint = tuple[str, int, str]
# Full hashes a confirm learned by reading a library file:
# ``{picture id: (full sha, stat signature)}``, for :func:`save_full_shas`.
LearnedShas = dict[int, tuple[str, str]]


def stat_signature(st: os.stat_result) -> str:
    """What must not have changed for a stored full hash to be trusted.

    Size and mtime catch an ordinary edit. ctime catches an edit whose mtime was
    put back (``os.utime``): the kernel moves ctime on every content or metadata
    change and no user call can set it back. The inode catches a replacement by
    rename. A false "changed" only costs one re-read.
    """
    return f"{st.st_size}:{st.st_mtime_ns}:{st.st_ctime_ns}:{st.st_ino}"


def _trust_stored_full_sha() -> bool:
    """Whether a stored full hash may stand in for reading the file.

    Not on Windows, where ``st_ctime`` is the creation time: an in-place edit
    whose mtime was put back would pass the guard and a changed file would be
    called a duplicate. The columns are still written there, so lifting this
    once the guard is proven on NTFS is a one-line change.
    """
    return os.name != "nt"


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
            Picture.full_sha,
            Picture.full_sha_stat,
        ).where(Picture.pixel_sha.in_(chunk))
        if not include_deleted:
            query = query.where(Picture.deleted.is_(False))
        rows = session.exec(query.order_by(Picture.id)).all()
        for (
            row_id,
            pixel_sha,
            size_bytes,
            file_path,
            deleted,
            full_sha,
            full_sha_stat,
        ) in rows:
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
                full_sha=full_sha,
                full_sha_stat=full_sha_stat,
            )
            candidates.setdefault(key, []).append(match)
    return candidates


def partition_confirmed_matches(
    candidates: dict[ContentKey, list[ShaMatch]],
    fingerprints: Iterable[ContentFingerprint],
    image_root: str,
    learned: Optional[LearnedShas] = None,
) -> tuple[dict[ContentFingerprint, ShaMatch], dict[ContentFingerprint, ShaMatch]]:
    """Confirm sampled candidates with a full-file SHA-256 comparison.

    The returned maps are keyed by ``(sampled hash, size, full hash)`` so two
    incoming files deliberately sharing a sampled key cannot overwrite one
    another in a batch map. A live confirmed row wins over a scrapheaped one.
    Missing or unreadable candidate files are conservatively treated as not a
    match: import may create a recoverable extra row, but never discards the
    incoming file on evidence it could not verify.

    A candidate whose stored hash still matches its file's stat signature is
    not read (:func:`_candidate_full_sha`). Every hash that had to be read is
    put in *learned*, when given, for the caller to store with
    :func:`save_full_shas`.
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
                full_hash_cache[match.id] = _candidate_full_sha(
                    match, image_root, learned
                )
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
    learned: Optional[LearnedShas] = None,
) -> Optional[ShaMatch]:
    """Return one fully-confirmed match, preferring a live row."""
    live, scrapheaped = partition_confirmed_matches(
        candidates, [fingerprint], image_root, learned
    )
    return live.get(fingerprint) or scrapheaped.get(fingerprint)


def stamp_full_sha(
    picture: Picture, fingerprint: ContentFingerprint, image_root: str
) -> None:
    """Record an incoming file's full hash on the new picture made from it.

    The picture's file holds the incoming bytes verbatim
    (``ImageUtils.create_picture_from_bytes``), so the incoming hash is the
    library file's. Stamped on the unsaved row, so the caller's own insert
    stores it. Skipped when the written file's size is not the incoming size
    (the writer found a file already at that path and kept it): the next
    confirm then reads the file instead.

    Args:
        picture: The new, not yet inserted, picture.
        fingerprint: The incoming file's ``(sampled, size, full sha)``.
        image_root: The library root the picture's ``file_path`` is under.
    """
    _sampled, size_bytes, full_sha = fingerprint
    resolved = ImageUtils.resolve_picture_path(image_root, picture.file_path)
    if not resolved:
        return
    try:
        st = os.stat(resolved)
    except OSError as exc:
        logger.warning(
            "Import dedup: not storing the full hash of new picture %r, its "
            "stat failed: %s; its next confirm will read the file.",
            resolved,
            exc,
        )
        return
    if st.st_size != size_bytes:
        return
    picture.full_sha = full_sha
    picture.full_sha_stat = stat_signature(st)


def store_full_shas_in_session(session: Session, learned: LearnedShas) -> None:
    """Write learned ``{picture id: (full sha, signature)}`` in one statement."""
    if not learned:
        return
    session.exec(
        update(Picture),
        params=[
            {"id": picture_id, "full_sha": sha, "full_sha_stat": signature}
            for picture_id, (sha, signature) in learned.items()
        ],
    )
    session.commit()


def save_full_shas(db, learned: LearnedShas) -> None:
    """Store what a confirm learned, on the writer, so the next import skips it.

    A cache write: if it fails the import's outcomes stand and the next confirm
    reads the file again, so the failure is logged rather than raised.
    """
    if not learned:
        return
    try:
        db.run_task(store_full_shas_in_session, dict(learned))
    except Exception as exc:
        logger.warning(
            "Import dedup: could not store %d learned full hash(es) for "
            "picture(s) %s: %s; the next import will read those files again.",
            len(learned),
            sorted(learned)[:20],
            exc,
        )


def _candidate_full_sha(
    match: ShaMatch, image_root: str, learned: Optional[LearnedShas]
) -> Optional[str]:
    """The candidate file's full hash: stored when still valid, else read.

    One ``stat`` decides. A file that is gone or unreadable is not a match, as
    before. The signature is taken BEFORE the read, so a write racing the read
    leaves a stale signature, which only forces another read next time.
    """
    resolved = ImageUtils.resolve_picture_path(image_root, match.file_path)
    if not resolved:
        return None
    try:
        signature = stat_signature(os.stat(resolved))
        if _trust_stored_full_sha():
            known = learned.get(match.id) if learned is not None else None
            if known is not None and known[1] == signature:
                return known[0]
            if match.full_sha and match.full_sha_stat == signature:
                return match.full_sha
        digest = ImageUtils.calculate_full_hash_from_file_path(resolved)
    except OSError as exc:
        logger.warning(
            "Import dedup: could not confirm candidate picture %d at %r: %s; "
            "treating the incoming file as new.",
            match.id,
            resolved,
            exc,
        )
        return None
    if learned is not None:
        learned[match.id] = (digest, signature)
    return digest
