"""Propose which loose adapters are steps or versions of one subject.

**Detection proposes, it never applies.** The house rule, arrived at
independently three times (folder monitoring detects missing files and refuses
to clean up; the training-run scan lists and refuses to import; this). Reading
is free, silent and continuous; rearranging somebody's shelf takes a click. So
this module returns groups and writes nothing, and :func:`apply_stack` is a
separate call the UI makes only after the owner has seen the dry run.

**A stack is a subject, not a training run.** It started as one - six files
differing only by a step - but a person retrains a character and calls the
result ``Foxglove_v2``, and that is the same subject on the shelf even though it
shares no training run with ``Foxglove``. So grouping is on the name with both
the step *and* the version token removed, and a group is proposed when its
members differ by a step (one run), by a version (several runs of one subject),
or by both.

* **``step_group``.** Every member is the same version; the names differ only by
  a training step. One run, nothing for a person to weigh.
* **``version_group``.** The members carry two or more versions, so the group
  spans training runs. The newest version covers it, and expanding the strip
  reads backwards through the versions and then through each one's steps.

**Prefix grouping** (``JimmyVehicle`` beside ``JimmyVehicle2``) is still NOT
here, and that is what the version rule is careful about: only an explicit ``v<digits>``
token counts, because a bare trailing digit could as easily be part of the name
and merging on it would invent a subject. That case needs per-group
adjudication with counter-evidence, which is a design question rather than
missing code.

Only *unstacked* adapters are considered. A run imported from ai-toolkit is
already a stack (:mod:`pixlstash.services.run_importer` builds one), and a
stack the owner has ratified must never be re-proposed - the risk is in
creating groupings nobody has seen, not in extending one they have.

**Three functions curate a stack that already exists**, and none of them is
detection: :func:`set_cover` is the owner overruling the filenames about which
file the shelf draws for a run, :func:`remove_member` takes one file back out
of one, and :func:`repair_stacks` is what every *other* way a member can leave
- Forget, Delete, a duplicate merge - has to call so the run it left is still
a run. Like :func:`unstack`, not one of them touches a byte on disk.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pixlstash.hub.db import HubDatabase
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.adapter_header import FILE_ADAPTER
from pixlstash.utils.model_utils import (
    _TRAINING_SUFFIX_RE,
    clean_asset_name,
    split_model_version,
    version_sort_key,
)

logger = get_logger(__name__)

TIER_STEP_GROUP = "step_group"
TIER_VERSION_GROUP = "version_group"

# A group of one is not a stack. Two files that differ only by step are the
# smallest thing worth collapsing; one file is just a file.
MIN_GROUP_SIZE = 2

# Ceiling on one stack. A subject has a few versions of tens of steps each, not
# thousands, and a caller sending more is confused rather than lucky.
#
# **It lives here, not on the route, because fusing widens the set after the
# route has counted it.** The route checks what it was sent; `apply_stack` then
# pulls in every member of every stack absorbed, so two stacks of 150 arrive as
# two ids and leave as 300 members. Measured, not reasoned: with the ceiling
# enforced only at the route, that call produced a 300-member stack. A limit
# that the widening step can walk past is not a limit, so the check is repeated
# on the widened set by the function that does the widening.
MAX_MEMBERS_PER_STACK = 200


@dataclass
class ProposedMember:
    """One model a proposal would put into a stack."""

    model_id: int
    filename: str
    step: int | None
    file_size: int | None
    # Deliberately NOT defaulted. The cover sort reads this, and a missing
    # version silently sorts as v1 - so a future construction that forgets it
    # would pick the wrong cover with nothing to show for it. Required here
    # makes that a TypeError at the call site instead.
    version: str | None


@dataclass
class StackProposal:
    """One group of models detection believes belong together."""

    tier: str
    key: str
    name: str
    folder_id: int
    members: list[ProposedMember] = field(default_factory=list)

    @property
    def total_size(self) -> int:
        return sum(m.file_size or 0 for m in self.members)


def _step_of(filename: str) -> int | None:
    """The training step a filename records, or None for a bare final.

    Reads the same trailing token :func:`derive_model_name` strips, so a file is
    never grouped by a name whose suffix this cannot also explain. ``step00500``
    and ``000000500`` both yield 500; ``portrait mix v2`` yields None, because
    ``v2`` is not a training suffix and the name keeps it.
    """
    tokens = clean_asset_name(filename).split()
    if not tokens or not _TRAINING_SUFFIX_RE.match(tokens[-1]):
        return None
    digits = "".join(ch for ch in tokens[-1] if ch.isdigit())
    return int(digits) if digits else None


def propose_stacks(hub: HubDatabase) -> list[StackProposal]:
    """Group loose adapters that are steps or versions of one subject.

    Reads only. The caller shows the result and asks; nothing here writes.

    Grouped **per folder**, not shelf-wide. Two runs on different disks can
    easily share a name - ``JimmyVehicle`` is not a globally unique thing, which
    is the same reason ``run_key`` is documented as unique within a stack only -
    and collapsing across folders would invent a run that never existed and
    would put one stack's members on two drives.

    A group needs a difference the name can account for: at least one member
    carrying a step suffix, or two members carrying different versions. Without
    either, the shared key is just two files with the same name in one folder,
    which is a duplicate or a coincidence and is not a subject with a history.

    Args:
        hub: The hub database holding the model shelf.

    Returns:
        Proposals, largest group first, then by name for a stable order.
    """
    rows = hub.fetchall(
        # `stack_id IS NULL` is the whole work queue: an imported run is already
        # a stack and a ratified one must never be re-proposed.
        #
        # MIN() on the folder, and that is not cosmetic. One model legitimately
        # has many `model_file` rows, so a bare `mf.model_folder_id` beside
        # `GROUP BY m.id` is a bare column: SQLite may return the folder of ANY
        # of its rows, so a model catalogued on two disks would group under one
        # folder on this call and the other on the next - proposals would be
        # nondeterministic, and two members of one run could land in different
        # groups and never be offered together. MIN makes the choice stable, and
        # `apply_stack` re-derives the same common-folder rule, so a proposal
        # cannot be something the apply then refuses.
        "SELECT m.id AS id, m.filename AS filename, m.file_size AS file_size, "
        "MIN(mf.model_folder_id) AS folder_id "
        "FROM model m "
        "JOIN model_file mf ON mf.model_id = m.id "
        "WHERE m.stack_id IS NULL AND m.file_kind = ? AND mf.state = 'present' "
        "GROUP BY m.id ORDER BY m.id",
        (FILE_ADAPTER,),
    )

    groups: dict[tuple[int, str], StackProposal] = {}
    for row in rows:
        filename = row["filename"] or ""
        subject, version = split_model_version(filename)
        if not subject:
            # Nothing survived the strip (a file called `000002750.safetensors`,
            # or a bare `v2.safetensors`). Grouping every such file together
            # would collapse unrelated subjects under the empty string.
            continue
        folder_id = int(row["folder_id"])
        key = (folder_id, subject.casefold())
        proposal = groups.get(key)
        if proposal is None:
            proposal = StackProposal(
                tier=TIER_STEP_GROUP,
                key=f"{folder_id}:{subject.casefold()}",
                name=subject,
                folder_id=folder_id,
            )
            groups[key] = proposal
        proposal.members.append(
            ProposedMember(
                model_id=int(row["id"]),
                filename=os.path.basename(filename),
                step=_step_of(filename),
                file_size=row["file_size"],
                version=version,
            )
        )

    proposals = []
    for proposal in groups.values():
        if len(proposal.members) < MIN_GROUP_SIZE:
            continue
        # Compared on the sort key rather than the token, so `Foxglove` and
        # `Foxglove_v1` count as one version: they are a duplicate or a
        # coincidence, which is exactly what the no-step refusal is for.
        versions = {version_sort_key(m.version) for m in proposal.members}
        if len(versions) < 2 and not any(m.step is not None for m in proposal.members):
            continue
        proposal.tier = TIER_VERSION_GROUP if len(versions) > 1 else TIER_STEP_GROUP
        proposal.members.sort(key=_cover_first_key)
        if len(versions) == 1 and proposal.members[0].version:
            # One version throughout, so it belongs in the name: a run of
            # `portrait_mix_v2` checkpoints is called "portrait mix v2", not
            # "portrait mix". Only dropped when the group spans versions, where
            # naming it after one of them would be false.
            proposal.name = f"{proposal.name} {proposal.members[0].version}"
        proposals.append(proposal)
    proposals.sort(key=lambda p: (-len(p.members), p.name.casefold()))
    # DEBUG, not INFO: this writes nothing, so it has no audit value, and the
    # module's whole framing is that reading is "free, silent and continuous".
    # The APPLY is what deserves a durable line, and it logs one.
    logger.debug(
        "Stack detection proposes %d group(s) over %d loose adapter(s).",
        len(proposals),
        sum(len(p.members) for p in proposals),
    )
    return proposals


class StackRefused(ValueError):
    """A group could not be stacked, with the reason the receipt reports."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


def apply_stack(
    hub: HubDatabase,
    model_ids: list[int],
    name: str | None,
    *,
    fuse: bool = False,
) -> int:
    """Collapse the given models into one stack, in cover-first order.

    The names are only *read* here, never gated on: the caller may stack any
    loose adapters that share a folder, whether or not detection would have
    proposed them. What the names decide is the cover - newest version first,
    then the bare final of that version, then its highest step.

    **``fuse`` is what lets a stack be stacked.** Without it a model that is
    already in a stack is refused, which is right for the proposals flow: that
    caller is confirming a dry run over *loose* files, so a row stacked in the
    meantime must be left where it is rather than torn out. Fusing is the
    opposite intent - the owner picked two stacks on the shelf and asked for one
    - so it is a separate flag rather than a relaxed gate, and the default stays
    the strict one.

    **Fusing absorbs whole stacks, never part of one.** Every member of every
    stack touched comes along, even the ones the caller did not name. The shelf
    already treats stacks atomically (selecting a collapsed row selects the run,
    "or Move would take one step of six"), and the alternative is worse than
    untidy: a stack that gained a member between the click and the call would be
    left behind as a remnant of one, which is a stack that is not a stack.

    The applying half, and the only thing here that writes. Called after the
    owner has seen the dry run, never from detection.

    **Every gate is re-checked on the UPDATE itself, not just read first.**
    An earlier version read the gate with a SELECT inside ``hub.transaction()``
    and believed that was one critical section. It is not: the hub connects with
    ``isolation_level=""``, so pysqlite opens a transaction on *DML only* - a
    leading SELECT runs in autocommit and the INSERT below is what actually
    begins the write. Measured, not reasoned: with two connections on one WAL
    database, ``in_transaction`` reads ``False`` after the SELECT, and a second
    writer that stacks a row in the gap is then silently overwritten by the
    unguarded UPDATE. This is the same pysqlite behaviour CLAUDE.md already
    records for ``PRAGMA defer_foreign_keys``.

    So the ``stack_id IS NULL`` predicate is repeated **on the UPDATE**, and the
    row count is checked: a row that stopped being loose between the SELECT and
    its own UPDATE changes nothing and aborts the whole stack rather than being
    torn out of the stack it already has.

    :meth:`~pixlstash.hub.db.HubDatabase.transaction` now issues
    ``BEGIN IMMEDIATE``, so the leading SELECT is inside the write transaction
    after all and the race described above can no longer be scheduled. The
    re-check stays regardless. It keeps the invariant *local*: reading this
    function tells you the guard holds, without also knowing what a different
    module does at ``BEGIN``, and it is the half that survives if anyone ever
    relaxes that.

    The same reasoning applies to the ``present`` gate. ``propose_stacks``
    refuses a model with no copy on disk, and refusing it here too is what stops
    the route being a way to do what the dry run never offers.

    Args:
        hub: The hub database.
        model_ids: The models to stack, as the proposal named them. Order is
            recomputed here rather than trusted; the caller cannot smuggle in a
            cover.
        name: The stack's name, or None to leave it unnamed. When fusing, None
            inherits the first name among the stacks being absorbed rather than
            dropping it: the name is the one thing a stack carries that its
            files do not, so losing it silently is losing data.
        fuse: Allow models that are already stacked, absorbing their stacks
            whole. Off by default, because the proposals flow must keep refusing
            a row something else stacked first.

    Returns:
        The new ``adapter_stack.id``.

    Raises:
        StackRefused: If fewer than two of the ids are still stackable.
    """
    ids = list(dict.fromkeys(int(i) for i in model_ids))
    if len(ids) < MIN_GROUP_SIZE:
        raise StackRefused(
            "A stack needs at least two models.", reason="too_few_models"
        )

    now = _utcnow()
    with hub.transaction() as conn:
        absorbed: list[int] = []
        if fuse:
            # Widen the selection to every member of every stack named, BEFORE
            # the gate reads. Inside the transaction, so a stack cannot grow a
            # member between the widening and the write.
            marks = ",".join("?" for _ in ids)
            absorbed = [
                int(row["stack_id"])
                for row in conn.execute(
                    f"SELECT DISTINCT stack_id FROM model "
                    f"WHERE id IN ({marks}) AND stack_id IS NOT NULL "
                    f"ORDER BY stack_id",
                    ids,
                ).fetchall()
            ]
            if absorbed:
                stack_marks = ",".join("?" for _ in absorbed)
                ids = list(
                    dict.fromkeys(
                        ids
                        + [
                            int(row["id"])
                            for row in conn.execute(
                                f"SELECT id FROM model "
                                f"WHERE stack_id IN ({stack_marks}) ORDER BY id",
                                absorbed,
                            ).fetchall()
                        ]
                    )
                )

        # Counted AFTER the widening, which is the only place it means anything:
        # the route counted what the caller sent, and fusing is exactly the step
        # that turns two ids into two whole stacks. Raised inside the
        # transaction, so nothing is written on the way to refusing.
        if len(ids) > MAX_MEMBERS_PER_STACK:
            raise StackRefused(
                f"That would make a stack of {len(ids)} models; the ceiling is "
                f"{MAX_MEMBERS_PER_STACK}. A subject has a few versions of tens "
                "of steps, not thousands.",
                reason="too_many_models",
            )

        placeholders = ",".join("?" for _ in ids)
        # The gate. `stack_id IS NULL` is dropped only when fusing, and even then
        # a row is admitted solely from a stack this call is absorbing - never
        # from a third one that appeared in the meantime.
        stacked_clause = (
            f"(m.stack_id IS NULL OR m.stack_id IN ({','.join('?' for _ in absorbed)}))"
            if absorbed
            else "m.stack_id IS NULL"
        )
        rows = conn.execute(
            # `state = 'present'` matches `propose_stacks`: a model whose only
            # copies are `missing` or `unreachable` is not something to
            # reorganise a shelf around, and the route must not offer what the
            # dry run refuses.
            f"SELECT m.id AS id, m.filename AS filename FROM model m "
            f"WHERE m.id IN ({placeholders}) AND {stacked_clause} "
            f"AND m.file_kind = ? AND EXISTS ("
            f"  SELECT 1 FROM model_file mf "
            f"  WHERE mf.model_id = m.id AND mf.state = 'present')",
            (*ids, *absorbed, FILE_ADAPTER),
        ).fetchall()

        # "Grouped per folder, never shelf-wide" is the module's invariant, and
        # until now only `propose_stacks` enforced it - so the route could build
        # a stack whose members sit on two drives, which is exactly the run that
        # never existed. Checked as "is there ONE folder holding a present copy
        # of every named model", which is the honest reading of a run being
        # files that sit together.
        surviving = [int(row["id"]) for row in rows]
        if surviving:
            marks = ",".join("?" for _ in surviving)
            shared = conn.execute(
                f"SELECT model_folder_id FROM model_file "
                f"WHERE model_id IN ({marks}) AND state = 'present' "
                f"GROUP BY model_folder_id HAVING COUNT(DISTINCT model_id) = ?",
                (*surviving, len(surviving)),
            ).fetchone()
            if shared is None:
                raise StackRefused(
                    "Those models are not all in one folder. A run is files "
                    "that sit together; stacking across folders would invent "
                    "one and put its members on two drives.",
                    reason="not_one_folder",
                )
        if len(rows) < MIN_GROUP_SIZE:
            raise StackRefused(
                "Fewer than two of those models are still loose adapters; "
                "something stacked them first.",
                reason="already_stacked",
            )

        if fuse and name is None and absorbed:
            # Inherit rather than drop. The files carry their own names; the
            # stack's is the only thing a person typed, so a fuse that silently
            # blanks it is destroying the one field it was asked to preserve.
            inherited = conn.execute(
                f"SELECT name FROM adapter_stack "
                f"WHERE id IN ({','.join('?' for _ in absorbed)}) "
                f"AND name IS NOT NULL ORDER BY id LIMIT 1",
                absorbed,
            ).fetchone()
            if inherited is not None:
                name = inherited["name"]

        ordered = sorted(
            ((int(r["id"]), r["filename"] or "") for r in rows),
            key=lambda pair: _cover_first_key(
                ProposedMember(
                    model_id=pair[0],
                    filename=pair[1],
                    step=_step_of(pair[1]),
                    file_size=None,
                    version=split_model_version(pair[1])[1],
                )
            ),
        )
        stack_id = int(
            conn.execute(
                "INSERT INTO adapter_stack (name, created_at, updated_at) "
                "VALUES (?, ?, ?)",
                (name, now, now),
            ).lastrowid
        )
        # The same predicate as the gate, repeated on the UPDATE itself. A row
        # that moved into a stack this call is NOT absorbing between the SELECT
        # and its own UPDATE still changes nothing and still aborts the whole
        # thing, fusing or not.
        guard = (
            f"(stack_id IS NULL OR stack_id IN ({','.join('?' for _ in absorbed)}))"
            if absorbed
            else "stack_id IS NULL"
        )
        for position, (model_id, _) in enumerate(ordered):
            changed = conn.execute(
                f"UPDATE model SET stack_id = ?, stack_position = ? "
                f"WHERE id = ? AND {guard}",
                (stack_id, position, model_id, *absorbed),
            ).rowcount
            if not changed:
                # Raised inside the transaction, so the INSERT above and every
                # UPDATE before this one roll back together: a run is stacked
                # whole or not at all, never half.
                raise StackRefused(
                    f"Model {model_id} was stacked by something else while this "
                    "was being confirmed; nothing was changed.",
                    reason="already_stacked",
                )

        if absorbed:
            # The absorbed stacks are empty now - every member points at the new
            # one - so the rows go. Deleted by "has no members left" rather than
            # by id, so a stack that somehow kept one is left alone instead of
            # becoming an orphaned `stack_id` pointing at nothing.
            emptied = conn.execute(
                f"DELETE FROM adapter_stack WHERE id IN "
                f"({','.join('?' for _ in absorbed)}) "
                f"AND NOT EXISTS (SELECT 1 FROM model WHERE stack_id = "
                f"adapter_stack.id)",
                absorbed,
            ).rowcount
            logger.info(
                "Fused %d stack(s) into adapter_stack %d; %d emptied row(s) removed.",
                len(absorbed),
                stack_id,
                emptied,
            )
    logger.info(
        "Stacked %d model(s) as adapter_stack %d (%s).", len(ordered), stack_id, name
    )
    return stack_id


def unstack(hub: HubDatabase, stack_id: int) -> int:
    """Break a stack apart, leaving its members loose on the shelf.

    The undo the shelf never had. Everything else here is one-way - a stack was
    built by a confirmation and there was no gesture that took it back - which
    is why the grouping dialog had to warn that nothing unstacks a group, and
    why fusing had to be argued for so carefully. With this, both become
    ordinary edits rather than commitments.

    **The files are not touched.** This clears two hub columns and deletes one
    row; nothing is moved, renamed or unlinked on disk, and the members reappear
    as the individual adapters they always were.

    One consequence worth knowing: the members become *loose*, so
    :func:`propose_stacks` can offer to regroup them the next time the dialog is
    opened. That is honest - they really are files that look like a run - but it
    means unstacking is not a way to tell detection "never again". Recording
    that refusal needs somewhere to keep it, and there is nowhere yet.

    Args:
        hub: The hub database.
        stack_id: The ``adapter_stack`` to dissolve.

    Returns:
        How many models were released.

    Raises:
        StackRefused: If no such stack exists.
    """
    with hub.transaction() as conn:
        released = conn.execute(
            "UPDATE model SET stack_id = NULL, stack_position = NULL "
            "WHERE stack_id = ?",
            (stack_id,),
        ).rowcount
        deleted = conn.execute(
            "DELETE FROM adapter_stack WHERE id = ?", (stack_id,)
        ).rowcount
        if not deleted:
            # Inside the transaction, so the UPDATE above rolls back with it -
            # an id that names no stack must not half-release rows on its way to
            # reporting that.
            raise StackRefused(
                f"There is no stack {stack_id}; nothing was changed.",
                reason="no_such_stack",
            )
    logger.info(
        "Unstacked adapter_stack %d, releasing %d model(s).", stack_id, released
    )
    return released


def set_cover(hub: HubDatabase, stack_id: int, model_id: int) -> list[int]:
    """Promote one member of a stack to ``stack_position`` 0.

    The names decide the cover when a stack is built, and they are usually
    right - but only the owner knows that the run's best checkpoint is step
    1500 rather than the one the trainer wrote last. This is the override, and
    it is the only way a cover is ever chosen by hand.

    **The choice sticks, and needs no column of its own to say it was made.**
    Nothing renumbers a stack after it is built: :func:`apply_stack` writes
    positions once, :func:`propose_stacks` reads *loose* adapters only, and the
    run importer's upsert keeps an existing ``stack_position`` with
    ``COALESCE``. A member's row can still *disappear* - deleted, forgotten, or
    merged away as a duplicate by the checkpoint-hash task - and
    :func:`repair_stacks` closes the gap that leaves; a chosen cover survives
    all of it unless the chosen file is itself what went.

    The other members keep their relative order and close the gap behind the
    promoted one, exactly as ``PATCH /stacks/{id}/members/{picture_id}`` does
    for a picture stack. Nothing on disk is touched.

    Args:
        hub: The hub database.
        stack_id: The ``adapter_stack`` whose cover is being set.
        model_id: The member to promote. Must already be in that stack.

    Returns:
        The stack's member ids in their new order, cover first.

    Raises:
        StackRefused: If the stack has no members, or the model is not one.
    """
    with hub.transaction() as conn:
        # NULLs sort first in SQLite, which would silently make an unpositioned
        # member the head of the order this renumbers from. Ordered last
        # instead, then given a real position like every other member.
        ids = [
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM model WHERE stack_id = ? "
                "ORDER BY stack_position IS NULL, stack_position, id",
                (stack_id,),
            ).fetchall()
        ]
        if not ids:
            # Covers both "no such stack" and the inert empty row an
            # interrupted import can leave behind: neither has a member to
            # promote, and both are a wrong address rather than a refusal.
            raise StackRefused(
                f"Stack {stack_id} has no members; nothing was changed.",
                reason="no_such_stack",
            )
        if model_id not in ids:
            raise StackRefused(
                f"Model {model_id} is not in stack {stack_id}; nothing was changed.",
                reason="not_a_member",
            )
        ordered = [model_id] + [i for i in ids if i != model_id]
        for position, member_id in enumerate(ordered):
            conn.execute(
                "UPDATE model SET stack_position = ? WHERE id = ? AND stack_id = ?",
                (position, member_id, stack_id),
            )
        conn.execute(
            "UPDATE adapter_stack SET updated_at = ? WHERE id = ?",
            (_utcnow(), stack_id),
        )
    logger.info("Model %d is now the cover of adapter_stack %d.", model_id, stack_id)
    return ordered


def remove_member(hub: HubDatabase, stack_id: int, model_id: int) -> tuple[int, bool]:
    """Take one model out of a stack, leaving it loose on the shelf.

    :func:`unstack` breaks a whole stack up; this releases a single member,
    which is what the owner wants when one checkpoint of a run turns out to be
    a different subject. **Nothing on disk is touched** - two hub columns are
    cleared - and the released model becomes loose, so :func:`propose_stacks`
    may offer to regroup it, exactly as it may after an unstack.

    **A stack of one is not a stack**, so removing the second-to-last member
    dissolves the whole thing: both files go loose and the ``adapter_stack``
    row is deleted. The survivors are otherwise renumbered contiguously, so
    removing the cover promotes whichever member was behind it.

    Args:
        hub: The hub database.
        stack_id: The ``adapter_stack`` to take the model out of.
        model_id: The member to release.

    Returns:
        ``(released, dissolved)`` - how many models are loose again, and
        whether the stack itself is gone.

    Raises:
        StackRefused: If the stack has no members, or the model is not one.
    """
    with hub.transaction() as conn:
        ids = [
            int(row["id"])
            for row in conn.execute(
                "SELECT id FROM model WHERE stack_id = ? "
                "ORDER BY stack_position IS NULL, stack_position, id",
                (stack_id,),
            ).fetchall()
        ]
        if not ids:
            raise StackRefused(
                f"Stack {stack_id} has no members; nothing was changed.",
                reason="no_such_stack",
            )
        if model_id not in ids:
            raise StackRefused(
                f"Model {model_id} is not in stack {stack_id}; nothing was changed.",
                reason="not_a_member",
            )

        remaining = [i for i in ids if i != model_id]
        dissolved = len(remaining) < MIN_GROUP_SIZE
        conn.execute(
            "UPDATE model SET stack_id = NULL, stack_position = NULL "
            "WHERE id = ? AND stack_id = ?",
            (model_id, stack_id),
        )
        # The renumber and the dissolve are :func:`repair_stacks`' rule rather
        # than a second copy of it: a run that loses a member because its file
        # was deleted has to end up in exactly the state one that lost it to
        # this verb does, and two implementations of "what a stack looks like
        # after a member leaves" is how those two drift apart.
        repair_stacks(conn, [stack_id])
        released = 1 + (len(remaining) if dissolved else 0)
    logger.info(
        "Released %d model(s) from adapter_stack %d%s.",
        released,
        stack_id,
        "; the stack is gone" if dissolved else "",
    )
    return released, dissolved


def repair_stacks(conn, stack_ids: Iterable[int]) -> None:
    """Put the named stacks back in a state the shelf can draw, on an open conn.

    **A member can leave a run without asking this module.** Deleting its file
    and forgetting its row both end at
    :func:`~pixlstash.services.model_shelf_service._purge`, which drops the
    ``model`` row and knows nothing about stacks. Left alone that yields a run
    whose positions read ``0, 2, 3``, or one with **no** position 0 at all
    because the cover is what went, or a stack of one - which the shelf draws
    as a plain row while still holding a grouping nobody can see or undo.

    So this is the repair, and it is the one statement of the rule: renumber
    the survivors contiguously from 0 keeping their order, and dissolve any
    stack left with fewer than two members.

    Empty ``adapter_stack`` rows are deliberately **left alone**. An interrupted
    import leaves one and :mod:`pixlstash.services.run_importer` documents it as
    inert; more to the point, that module inserts the stack row before its
    members, so deleting empty ones here would race a live import into removing
    the row it is about to point at.

    Args:
        conn: An open hub connection, inside the caller's transaction - the
            repair has to land with whatever removed the member.
        stack_ids: The stacks to check. An id that names no stack, and a stack
            that is already tidy, cost one read and change nothing.
    """
    for stack_id in sorted({int(i) for i in stack_ids if i is not None}):
        members = [
            (int(row["id"]), row["stack_position"])
            for row in conn.execute(
                "SELECT id, stack_position FROM model WHERE stack_id = ? "
                "ORDER BY stack_position IS NULL, stack_position, id",
                (stack_id,),
            ).fetchall()
        ]
        if not members:
            continue
        if len(members) < MIN_GROUP_SIZE:
            conn.execute(
                "UPDATE model SET stack_id = NULL, stack_position = NULL "
                "WHERE stack_id = ?",
                (stack_id,),
            )
            conn.execute("DELETE FROM adapter_stack WHERE id = ?", (stack_id,))
            logger.info(
                "adapter_stack %d was left with one member and is gone.", stack_id
            )
            continue
        changed = False
        for position, (member_id, recorded) in enumerate(members):
            if recorded == position:
                continue
            conn.execute(
                "UPDATE model SET stack_position = ? WHERE id = ? AND stack_id = ?",
                (position, member_id, stack_id),
            )
            changed = True
        if changed:
            conn.execute(
                "UPDATE adapter_stack SET updated_at = ? WHERE id = ?",
                (_utcnow(), stack_id),
            )


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cover_first_key(member: ProposedMember):
    """Sort key putting the right cover at ``stack_position`` 0.

    **Version first, then the step rule.** The newest version is what a person
    means by "the LoRA" once they have retrained the subject - a stack of
    ``Foxglove`` and ``Foxglove_v2`` covers with v2, and an unversioned file
    reads as v1 because it existed before v2 did. *Within* one version the rule
    is unchanged: the bare no-step file is what the trainer wrote last so it
    leads, and without one the highest step is the best available answer.
    Expanding the strip reads backwards in time through versions and then
    through each one's steps.

    **This is now a strict superset of ``run_importer._cover_first``, not the
    same function.** That one is ``finals + stepped`` with no version term, and
    it is deliberately left alone: it orders a single ai-toolkit run, which is
    one version by construction, so the two agree on every input it can see. Say
    superset rather than "the same rule" - the older wording became false the
    moment a version term existed, and a reader checking the claim would find
    two functions. If the importer ever ingests more than one run at a time, it
    has to come here rather than grow its own second answer.
    """
    major, minor = version_sort_key(member.version)
    return (-major, -minor, member.step is not None, -(member.step or 0))
