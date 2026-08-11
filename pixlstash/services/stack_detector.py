"""Propose which loose adapters are steps of one training run.

**Detection proposes, it never applies.** The house rule, arrived at
independently three times (folder monitoring detects missing files and refuses
to clean up; the training-run scan lists and refuses to import; this). Reading
is free, silent and continuous; rearranging somebody's shelf takes a click. So
this module returns groups and writes nothing, and :func:`apply_stack` is a
separate call the UI makes only after the owner has seen the dry run.

**Two tiers, two treatments — not two numbers.** The dedup work already argued
this out for pictures and the shelf follows it exactly:

* **Tier 1, step grouping.** Six files whose names differ only by a training
  step really are one run; there is nothing for a person to weigh, so this tier
  gets one dry run and one confirmation for the whole batch.
* **Tier 2, prefix grouping** (``JimmyCarr`` beside ``JimmyCarr2``) needs a
  person and gets per-group adjudication with counter-evidence. It is NOT in
  this module yet, deliberately: shipping tier 1 alone is a usable increment,
  and tier 2's evidence model is the part that needs design rather than code.

Only *unstacked* adapters are considered. A run imported from ai-toolkit is
already a stack (:mod:`pixlstash.services.run_importer` builds one), and a
stack the owner has ratified must never be re-proposed — the risk is in
creating groupings nobody has seen, not in extending one they have.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pixlstash.hub.db import HubDatabase
from pixlstash.pixl_logging import get_logger
from pixlstash.utils.adapter_header import FILE_ADAPTER
from pixlstash.utils.model_utils import (
    _TRAINING_SUFFIX_RE,
    clean_asset_name,
    derive_model_name,
)

logger = get_logger(__name__)

TIER_STEP_GROUP = "step_group"

# A group of one is not a stack. Two files that differ only by step are the
# smallest thing worth collapsing; one file is just a file.
MIN_GROUP_SIZE = 2


@dataclass
class ProposedMember:
    """One model a proposal would put into a stack."""

    model_id: int
    filename: str
    step: int | None
    file_size: int | None


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
    """Group loose adapters that differ only by their training step.

    Reads only. The caller shows the result and asks; nothing here writes.

    Grouped **per folder**, not shelf-wide. Two runs on different disks can
    easily share a name — ``JimmyCarr`` is not a globally unique thing, which
    is the same reason ``run_key`` is documented as unique within a stack only —
    and collapsing across folders would invent a run that never existed and
    would put one stack's members on two drives.

    A group needs at least one member carrying a step suffix. Without that the
    shared key is just two files with the same name in one folder, which is a
    duplicate or a coincidence and is not a training run.

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
        # folder on this call and the other on the next — proposals would be
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
        derived = derive_model_name(filename)
        if not derived:
            # Nothing survived the strip (a file called `000002750.safetensors`).
            # Grouping every such file together would collapse unrelated runs
            # under the empty string.
            continue
        folder_id = int(row["folder_id"])
        key = (folder_id, derived.casefold())
        proposal = groups.get(key)
        if proposal is None:
            proposal = StackProposal(
                tier=TIER_STEP_GROUP,
                key=f"{folder_id}:{derived.casefold()}",
                name=derived,
                folder_id=folder_id,
            )
            groups[key] = proposal
        proposal.members.append(
            ProposedMember(
                model_id=int(row["id"]),
                filename=os.path.basename(filename),
                step=_step_of(filename),
                file_size=row["file_size"],
            )
        )

    proposals = [
        proposal
        for proposal in groups.values()
        if len(proposal.members) >= MIN_GROUP_SIZE
        and any(member.step is not None for member in proposal.members)
    ]
    for proposal in proposals:
        proposal.members.sort(key=_cover_first_key)
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


def apply_stack(hub: HubDatabase, model_ids: list[int], name: str | None) -> int:
    """Collapse the given models into one stack, in cover-first order.

    The applying half, and the only thing here that writes. Called after the
    owner has seen the dry run, never from detection.

    **Every gate is re-checked on the UPDATE itself, not just read first.**
    An earlier version read the gate with a SELECT inside ``hub.transaction()``
    and believed that was one critical section. It is not: the hub connects with
    ``isolation_level=""``, so pysqlite opens a transaction on *DML only* — a
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

    The same reasoning applies to the ``present`` gate. ``propose_stacks``
    refuses a model with no copy on disk, and refusing it here too is what stops
    the route being a way to do what the dry run never offers.

    Args:
        hub: The hub database.
        model_ids: The models to stack, as the proposal named them. Order is
            recomputed here rather than trusted; the caller cannot smuggle in a
            cover.
        name: The stack's name, or None to leave it unnamed.

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
    placeholders = ",".join("?" for _ in ids)
    with hub.transaction() as conn:
        rows = conn.execute(
            # `state = 'present'` matches `propose_stacks`: a model whose only
            # copies are `missing` or `unreachable` is not something to
            # reorganise a shelf around, and the route must not offer what the
            # dry run refuses.
            f"SELECT m.id AS id, m.filename AS filename FROM model m "
            f"WHERE m.id IN ({placeholders}) AND m.stack_id IS NULL "
            f"AND m.file_kind = ? AND EXISTS ("
            f"  SELECT 1 FROM model_file mf "
            f"  WHERE mf.model_id = m.id AND mf.state = 'present')",
            (*ids, FILE_ADAPTER),
        ).fetchall()

        # "Grouped per folder, never shelf-wide" is the module's invariant, and
        # until now only `propose_stacks` enforced it — so the route could build
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

        ordered = sorted(
            ((int(r["id"]), r["filename"] or "") for r in rows),
            key=lambda pair: _cover_first_key(
                ProposedMember(
                    model_id=pair[0],
                    filename=pair[1],
                    step=_step_of(pair[1]),
                    file_size=None,
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
        for position, (model_id, _) in enumerate(ordered):
            changed = conn.execute(
                "UPDATE model SET stack_id = ?, stack_position = ? "
                "WHERE id = ? AND stack_id IS NULL",
                (stack_id, position, model_id),
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
    logger.info(
        "Stacked %d model(s) as adapter_stack %d (%s).", len(ordered), stack_id, name
    )
    return stack_id


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cover_first_key(member: ProposedMember):
    """Sort key putting the right cover at ``stack_position`` 0.

    The same rule ``run_importer._cover_first`` applies, and deliberately not a
    second one: the bare no-step file is what the trainer wrote last and is what
    a person means by "the LoRA", so it leads; without one the highest step is
    the best available answer and the rest follow newest first, so expanding the
    strip reads backwards in time.
    """
    return (member.step is not None, -(member.step or 0))
