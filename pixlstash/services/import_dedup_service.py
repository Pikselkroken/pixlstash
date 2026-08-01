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

**The rule this module encodes.** A content-hash match is one of exactly two
things, and the import must be able to tell them apart:

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
        file_path: The matching picture's vault-relative path, when it has one.
        deleted: True when the match is soft-deleted (in the Scrapheap).
    """

    id: int
    pixel_sha: str
    file_path: Optional[str]
    deleted: bool


def partition_by_pixel_sha_in_session(
    session: Session, shas: Iterable[str]
) -> tuple[dict[str, ShaMatch], dict[str, ShaMatch]]:
    """Split incoming content hashes into live and scrapheaped matches.

    Args:
        session: An open read session.
        shas: The incoming files' content hashes (duplicates tolerated).

    Returns:
        ``(live, scrapheaped)``: two dicts keyed by ``pixel_sha``. They are
        **disjoint**: a hash with both a live and a soft-deleted row appears only
        in ``live``. A hash in neither dict is genuinely new.
    """
    wanted = sorted({sha for sha in shas if sha})
    live: dict[str, ShaMatch] = {}
    scrapheaped: dict[str, ShaMatch] = {}
    if not wanted:
        return live, scrapheaped

    for start in range(0, len(wanted), _SHA_QUERY_CHUNK):
        chunk = wanted[start : start + _SHA_QUERY_CHUNK]
        rows = session.exec(
            # No ``deleted`` predicate on purpose: seeing the Scrapheap is the
            # whole point. The rows are classified below, not filtered out.
            select(Picture.id, Picture.pixel_sha, Picture.file_path, Picture.deleted)
            .where(Picture.pixel_sha.in_(chunk))
            .order_by(Picture.id)
        ).all()
        for row_id, pixel_sha, file_path, deleted in rows:
            if row_id is None or not pixel_sha:
                continue
            match = ShaMatch(
                id=int(row_id),
                pixel_sha=pixel_sha,
                file_path=file_path,
                deleted=bool(deleted),
            )
            if match.deleted:
                # A live row for the same hash outranks it; if the live one is
                # seen later it removes this entry (below).
                if pixel_sha not in live:
                    scrapheaped.setdefault(pixel_sha, match)
            else:
                live.setdefault(pixel_sha, match)
                scrapheaped.pop(pixel_sha, None)

    if scrapheaped:
        logger.info(
            "Import dedup: %d incoming content hash(es) match scrapheaped "
            "picture(s) %s: they will NOT be imported again and are reported "
            "as a restorable outcome, not as ordinary duplicates.",
            len(scrapheaped),
            sorted({m.id for m in scrapheaped.values()}),
        )
    return live, scrapheaped


def match_one_by_pixel_sha_in_session(
    session: Session, pixel_sha: str
) -> Optional[ShaMatch]:
    """Single-hash form of :func:`partition_by_pixel_sha_in_session`.

    Used by the streaming-staging import, which hashes and matches one staged
    file at a time so its progress counter can advance per file.

    Args:
        session: An open session.
        pixel_sha: The incoming file's content hash.

    Returns:
        The matching picture, preferring a live row over a scrapheaped one, or
        ``None`` when the content is new to the vault.
    """
    if not pixel_sha:
        return None
    live, scrapheaped = partition_by_pixel_sha_in_session(session, [pixel_sha])
    return live.get(pixel_sha) or scrapheaped.get(pixel_sha)
