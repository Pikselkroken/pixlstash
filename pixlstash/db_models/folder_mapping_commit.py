"""The folder-mapping commit that has not finished, so a restart can finish it.

A commit has two phases and only one of them is atomic. Indexing writes a
picture row per file over many transactions; assigning creates the projects,
people, sets and tags and links every picture to them in a single one. Kill the
app between them - the owner quits, the machine sleeps, the desktop shell
restarts the backend - and the library is left half made: some pictures
indexed, nothing organised, and no record anywhere that the owner ever asked
for the rest. The screen that would ask again is gone with the process, because
the read and the accepted assignments only ever lived in server memory.

One row here is that record. It is written before the commit thread starts and
settled inside the assigning transaction itself, so the two cannot disagree:
either the assignments landed and this row says ``done`` in the same commit, or
neither happened and it still says ``pending`` for the next start-up to pick up.

**At most one row is ``pending``**, which is the same single-slot rule the
commit endpoint already enforces in memory (``server.folder_structure_commit``).
Settled rows are kept: they are the answer to "what happened to that import",
and there are only ever a handful.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel

#: Written, not yet finished. The only state a start-up resumes.
STATE_PENDING = "pending"
#: The assigning transaction committed. Set inside that same transaction.
STATE_DONE = "done"
#: The owner aborted it. Never resumed; whatever was indexed stays indexed.
STATE_ABANDONED = "abandoned"
#: The owner chose "organise later": indexing ran to the end, the mapping was
#: deliberately not applied. Distinct from ``done`` because the library is
#: usable but unorganised, and from ``abandoned`` because nothing was refused.
STATE_DEFERRED = "deferred"

SETTLED_STATES = (STATE_DONE, STATE_ABANDONED, STATE_DEFERRED)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FolderMappingCommit(SQLModel, table=True):
    """One accepted folder mapping, durable from the moment it is accepted.

    Attributes:
        id: Primary key.
        task_id: The id the client polls. Kept across a resume so a client that
            remembers it can reattach to the resumed run.
        root_path: The folder that was read, absolute.
        mode: ``reference`` or ``local_import`` - which of the two commit paths
            this was, recorded because they index differently and a resume must
            take the same one.
        label: The reference folder's label, when the owner gave one.
        expected_pictures: The read's own count, the progress total.
        assignments: The accepted mapping as JSON, in the wire form
            ``folder_structure_commit_service.parse_assignments`` reads. Stored
            rather than re-derived: the read that produced it is gone after a
            restart, and re-running it would cost the same half hour again and
            could propose something else. ``[]`` is an "organise later" commit -
            index everything, decide what the folders mean another day.
        stage: Last stage reported (``registering`` / ``indexing`` /
            ``assigning``). Informational - a resume re-runs from the start of
            its phase, since indexing is idempotent by ``file_path`` and
            assigning is one transaction.
        state: :data:`STATE_PENDING`, :data:`STATE_DONE`,
            :data:`STATE_ABANDONED` or :data:`STATE_DEFERRED`.
        started_at: When the commit was first accepted.
        updated_at: When this row last changed.
    """

    __tablename__ = "folder_mapping_commit"

    id: Optional[int] = Field(default=None, primary_key=True)
    task_id: str = Field(index=True)
    root_path: str
    mode: str
    label: Optional[str] = None
    expected_pictures: int = 0
    assignments: str = "[]"
    stage: str = "registering"
    state: str = Field(default=STATE_PENDING, index=True)
    started_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
