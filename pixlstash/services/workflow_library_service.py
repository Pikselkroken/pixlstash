"""Vault-side reads over the workflow keys ``picture`` carries.

The rows these hashes name live in the hub and are content-addressed, so nothing
here joins across the database boundary: a hash the attached hub has never heard
of is a workflow this machine does not have, which the library view reports as
unknown rather than treating as an error.

**Soft-deleted pictures are excluded, and that is the point of the module
existing rather than the query being inlined at each call site.** A workflow
whose every picture sits in the Scrapheap must read as "none kept"; counting the
scrapheap in would make it read as live.
"""

from __future__ import annotations

from sqlalchemy import func
from sqlmodel import Session, select

from pixlstash.db_models import Picture


def topology_picture_counts(session: Session) -> dict[str, int]:
    """How many kept pictures each topology accounts for, **vault-wide**.

    Served by ``ix_picture_workflow_topology_hash``.

    **This count is unscoped and must not be returned to a scoped token as it
    stands.** It reads every non-deleted picture in the vault, so a route
    exposing it to a picture-, set- or project-scoped token would disclose the
    size of the whole library - the deny-by-default rule in
    ``docs/backend_architecture.md`` §16 exists because that class of omission
    has recurred here. A caller that needs a scoped answer adds the narrowing
    parameter then, against a real policy; inventing one now with no route to
    check it against would only look like the question had been settled.

    Returns:
        ``{topology_hash: count}``, with topologies whose pictures are all
        soft-deleted absent entirely rather than present with a zero.
    """
    rows = session.exec(
        select(Picture.workflow_topology_hash, func.count(Picture.id))
        .where(Picture.workflow_topology_hash.is_not(None))
        .where(Picture.deleted.is_(False))
        .group_by(Picture.workflow_topology_hash)
    ).all()
    return {topology: count for topology, count in rows}
