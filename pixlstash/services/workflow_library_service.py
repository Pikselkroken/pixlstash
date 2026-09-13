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

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import func
from sqlmodel import Session, select

from pixlstash.db_models import Picture


@dataclass(frozen=True)
class WorkflowActivity:
    """What a vault knows about one workflow key: how much, and how recently."""

    pictures: int
    last_used: Optional[datetime]


@dataclass(frozen=True)
class ScanProgress:
    """How far the ComfyUI extraction pass has read.

    The Workflows view has to say which of three states an empty list is in --
    not looked yet, looking, or looked and there is genuinely nothing -- and it
    cannot tell them apart from the list alone. ``scanned`` counts pictures
    carrying a ``workflow_hash_version``, which the extraction task writes for
    every picture it reads whether or not that picture held a workflow.
    """

    pictures: int
    scanned: int


# When a workflow was last used. ``created_at`` is the picture's own date and is
# nullable -- a PNG that carried no date has none -- so a bare ``max`` over it
# reads as "never" for a workflow whose every picture came in undated, which is
# a real state in the owner's libraries and not the one the column means. Falling
# back to ``imported_at`` answers with the best date the vault actually has.
_USED_AT = func.coalesce(Picture.created_at, Picture.imported_at)


def _activity(session: Session, column) -> dict[str, WorkflowActivity]:
    """Group kept pictures by one workflow hash column.

    Returns:
        ``{hash: WorkflowActivity}``, with keys whose pictures are all
        soft-deleted absent entirely rather than present with a zero.
    """
    rows = session.exec(
        select(column, func.count(Picture.id), func.max(_USED_AT))
        .where(column.is_not(None))
        .where(Picture.deleted.is_(False))
        .group_by(column)
    ).all()
    return {
        key: WorkflowActivity(pictures=count, last_used=last_used)
        for key, count, last_used in rows
    }


def topology_activity(session: Session) -> dict[str, WorkflowActivity]:
    """What each topology accounts for, **vault-wide**.

    Served by ``ix_picture_workflow_topology_hash``.

    **These counts are unscoped and must not be returned to a scoped token as
    they stand.** They read every non-deleted picture in the vault, so a route
    exposing them to a picture-, set- or project-scoped token would disclose the
    size of the whole library -- the deny-by-default rule in
    ``docs/backend_architecture.md`` §16 exists because that class of omission
    has recurred here. ``GET /workflows`` is declared ``OWNER_ONLY`` for exactly
    this reason. A caller that needs a scoped answer adds the narrowing
    parameter then, against a real policy.
    """
    return _activity(session, Picture.workflow_topology_hash)


def recipe_activity(
    session: Session, structural_hashes: list[str]
) -> dict[str, WorkflowActivity]:
    """The same figures per recipe, for the variants under one topology.

    Narrowed by hash rather than grouped vault-wide: the caller already knows
    which recipes it is expanding, and a topology holding 159 of them is still
    one ``IN`` over an indexed column.
    """
    if not structural_hashes:
        return {}
    column = Picture.workflow_structural_hash
    rows = session.exec(
        select(column, func.count(Picture.id), func.max(_USED_AT))
        .where(column.in_(structural_hashes))
        .where(Picture.deleted.is_(False))
        .group_by(column)
    ).all()
    return {
        key: WorkflowActivity(pictures=count, last_used=last_used)
        for key, count, last_used in rows
    }


def scan_progress(session: Session) -> ScanProgress:
    """How many kept pictures exist, and how many have been read for a workflow."""
    pictures, scanned = session.exec(
        select(
            func.count(Picture.id),
            func.count(Picture.workflow_hash_version),
        ).where(Picture.deleted.is_(False))
    ).one()
    return ScanProgress(pictures=pictures or 0, scanned=scanned or 0)


def topology_picture_ids(session: Session, topology_hash: str, limit: int) -> list[int]:
    """The newest kept pictures made by one topology, for the rail's tiles."""
    return list(
        session.exec(
            select(Picture.id)
            .where(Picture.workflow_topology_hash == topology_hash)
            .where(Picture.deleted.is_(False))
            .order_by(_USED_AT.desc(), Picture.id.desc())
            .limit(limit)
        ).all()
    )


# ---------------------------------------------------------------------------
# The vault-level entry points the routes call.
#
# The session-level functions above stay public because they are the ones whose
# contract is worth reading (and testing) on its own; these are the thin layer
# that owns the ``vault.db`` call, so no route file has to. That split is what
# ``tests/test_architecture_guardrails.py::test_no_new_direct_db_calls_from_routes``
# asks for, and it is why the list's two reads share one session rather than
# taking two.
# ---------------------------------------------------------------------------


def read_library(vault) -> tuple[dict[str, WorkflowActivity], ScanProgress]:
    """The list route's whole vault side: per-topology figures and scan state."""

    def _read(session: Session):
        return topology_activity(session), scan_progress(session)

    return vault.db.run_immediate_read_task(_read)


def read_recipe_activity(
    vault, structural_hashes: list[str]
) -> dict[str, WorkflowActivity]:
    """The expansion's vault side: figures for one topology's recipes."""
    return vault.db.run_immediate_read_task(recipe_activity, structural_hashes)


def read_topology_picture_ids(vault, topology_hash: str, limit: int) -> list[int]:
    """The inspector's tile ids for one topology."""
    return vault.db.run_immediate_read_task(topology_picture_ids, topology_hash, limit)
