"""Stack detection: what it groups, what it refuses, and that it never applies.

The assertions worth having are the refusals. Grouping six files of one run is
the easy half; the ways this must NOT group are what stop it inventing runs that
never existed and rearranging a shelf nobody asked it to touch.
"""

from __future__ import annotations

import hashlib

import pytest

from pixlstash.hub.db import HubDatabase
from pixlstash.services.stack_detector import (
    StackRefused,
    apply_stack,
    propose_stacks,
)


@pytest.fixture
def hub(tmp_path):
    database = HubDatabase(str(tmp_path / "hub.db"))
    yield database
    database.close()


def _folder(hub, path):
    with hub.transaction() as conn:
        return int(
            conn.execute(
                "INSERT INTO model_folder (path, kind, movable, created_at) "
                "VALUES (?, 'user', 'per_item', '2026-08-11T00:00:00Z')",
                (path,),
            ).lastrowid
        )


def _adapter(
    hub,
    folder_id,
    filename,
    *,
    file_kind="adapter",
    state="present",
    stack_id=None,
    size=1000,
):
    """Register one model with one location. Mirrors what the scan writes.

    The hash is derived from the filename because the schema's
    `CHECK (file_kind <> 'adapter' OR sha256 IS NOT NULL)` forbids an unhashed
    adapter, and a shared constant would collide on the unique index. Distinct
    per file, exactly as real digests are.
    """
    digest = hashlib.sha256(filename.encode()).hexdigest()
    with hub.transaction() as conn:
        model_id = int(
            conn.execute(
                "INSERT INTO model (file_kind, kind, filename, file_size, "
                "stack_id, sha256, provenance, created_at) "
                "VALUES (?, 'lora', ?, ?, ?, ?, 'scanned', "
                "'2026-08-11T00:00:00Z')",
                (file_kind, filename, size, stack_id, digest),
            ).lastrowid
        )
        conn.execute(
            "INSERT INTO model_file (model_id, model_folder_id, relpath, state) "
            "VALUES (?, ?, ?, ?)",
            (model_id, folder_id, filename, state),
        )
    return model_id


def _names(proposals):
    return sorted(p.name for p in proposals)


def test_files_differing_only_by_step_are_one_run(hub, tmp_path):
    folder = _folder(hub, str(tmp_path / "loras"))
    for name in (
        "JimmyCarr_000000500.safetensors",
        "JimmyCarr_000001000.safetensors",
        "JimmyCarr.safetensors",
    ):
        _adapter(hub, folder, name)

    proposals = propose_stacks(hub)
    assert _names(proposals) == ["JimmyCarr"]
    assert len(proposals[0].members) == 3


def test_the_bare_final_leads_and_the_rest_run_backwards(hub, tmp_path):
    """`stack_position` 0 is what a person means by "the LoRA".

    The bare no-step file is what the trainer wrote last, so it covers; without
    one the highest step is the best available answer. Same rule the run
    importer applies, deliberately not a second one.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    _adapter(hub, folder, "Foxglove_000000500.safetensors")
    _adapter(hub, folder, "Foxglove.safetensors")
    _adapter(hub, folder, "Foxglove_000002000.safetensors")

    members = propose_stacks(hub)[0].members
    assert [m.step for m in members] == [None, 2000, 500]


def test_a_run_with_no_bare_final_covers_with_its_highest_step(hub, tmp_path):
    folder = _folder(hub, str(tmp_path / "loras"))
    _adapter(hub, folder, "Clementine_000000500.safetensors")
    _adapter(hub, folder, "Clementine_000002750.safetensors")

    members = propose_stacks(hub)[0].members
    assert [m.step for m in members] == [2750, 500]


def test_two_files_sharing_a_name_with_no_step_are_not_a_run(hub, tmp_path):
    """The refusal that keeps a duplicate from being called a training run.

    Same name in one folder and no step anywhere is a copy or a coincidence.
    Collapsing it would hide one of the two behind the other.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    _adapter(hub, folder, "portrait_mix_v2.safetensors")
    _adapter(hub, folder, "portrait-mix-v2.safetensors")

    assert propose_stacks(hub) == []


def test_a_group_never_spans_two_folders(hub, tmp_path):
    """Two runs on different disks can easily share a name.

    Collapsing across folders would invent a run that never existed and put one
    stack's members on two drives — which the move verb would then have to
    reason about.
    """
    first = _folder(hub, str(tmp_path / "disk-a"))
    second = _folder(hub, str(tmp_path / "disk-b"))
    _adapter(hub, first, "JimmyCarr_000000500.safetensors")
    _adapter(hub, second, "JimmyCarr_000001000.safetensors")

    assert propose_stacks(hub) == []


def test_a_model_already_in_a_stack_is_never_re_proposed(hub, tmp_path):
    """An imported run is already a stack, and a ratified one is settled.

    The risk is in creating groupings nobody has seen, not in extending one
    they have.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    with hub.transaction() as conn:
        stack_id = int(
            conn.execute(
                "INSERT INTO adapter_stack (name, created_at, updated_at) "
                "VALUES ('Ratified', '2026-08-11T00:00:00Z', "
                "'2026-08-11T00:00:00Z')"
            ).lastrowid
        )
    _adapter(hub, folder, "Ratified_000000500.safetensors", stack_id=stack_id)
    _adapter(hub, folder, "Ratified_000001000.safetensors", stack_id=stack_id)

    assert propose_stacks(hub) == []


def test_a_checkpoint_is_never_stacked_with_adapters(hub, tmp_path):
    """A stack is a training run. A base model is not a step of one."""
    folder = _folder(hub, str(tmp_path / "loras"))
    _adapter(hub, folder, "Base_000000500.safetensors", file_kind="checkpoint")
    _adapter(hub, folder, "Base_000001000.safetensors", file_kind="checkpoint")

    assert propose_stacks(hub) == []


def test_a_file_that_is_not_on_disk_is_not_proposed(hub, tmp_path):
    """`missing` is a fact and `unreachable` is the absence of one; neither is
    something to reorganise a shelf around."""
    folder = _folder(hub, str(tmp_path / "loras"))
    _adapter(hub, folder, "Gone_000000500.safetensors", state="missing")
    _adapter(hub, folder, "Gone_000001000.safetensors", state="unreachable")

    assert propose_stacks(hub) == []


def test_a_name_that_is_only_a_step_number_groups_nothing(hub, tmp_path):
    """Nothing survives the strip for `000002750.safetensors`.

    Grouping on the empty string would collapse every such file in a folder into
    one invented run.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    _adapter(hub, folder, "000002750.safetensors")
    _adapter(hub, folder, "000005000.safetensors")

    assert propose_stacks(hub) == []


def test_detection_writes_nothing(hub, tmp_path):
    """The house rule, asserted rather than assumed."""
    folder = _folder(hub, str(tmp_path / "loras"))
    _adapter(hub, folder, "JimmyCarr_000000500.safetensors")
    _adapter(hub, folder, "JimmyCarr_000001000.safetensors")

    propose_stacks(hub)

    stacked = hub.fetchone("SELECT COUNT(*) AS n FROM model WHERE stack_id IS NOT NULL")
    stacks = hub.fetchone("SELECT COUNT(*) AS n FROM adapter_stack")
    assert stacked["n"] == 0
    assert stacks["n"] == 0


# ── applying ────────────────────────────────────────────────────────────────


def test_applying_orders_the_cover_first_whatever_order_it_was_given(hub, tmp_path):
    """The caller cannot choose the cover by reordering its list.

    Order is recomputed server-side from the filenames, which is what stops a
    client picking step 500 as the face of a run that finished at 2750.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    low = _adapter(hub, folder, "Foxglove_000000500.safetensors")
    final = _adapter(hub, folder, "Foxglove.safetensors")
    high = _adapter(hub, folder, "Foxglove_000002000.safetensors")

    stack_id = apply_stack(hub, [low, high, final], "Foxglove")

    rows = hub.fetchall(
        "SELECT id, stack_position FROM model WHERE stack_id = ? "
        "ORDER BY stack_position",
        (stack_id,),
    )
    assert [r["id"] for r in rows] == [final, high, low]


def test_applying_refuses_a_group_of_one(hub, tmp_path):
    folder = _folder(hub, str(tmp_path / "loras"))
    only = _adapter(hub, folder, "Lonely_000000500.safetensors")

    with pytest.raises(StackRefused) as exc:
        apply_stack(hub, [only], None)
    assert exc.value.reason == "too_few_models"


def test_applying_drops_a_row_something_else_stacked_first(hub, tmp_path):
    """The window between the dry run and the confirmation.

    A proposal is a snapshot the owner may have been looking at for a minute.
    A row stacked in the meantime must be left in the stack it already has, not
    torn out of it — and if that leaves fewer than two, nothing is written at
    all rather than a stack of one.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    first = _adapter(hub, folder, "Race_000000500.safetensors")
    second = _adapter(hub, folder, "Race_000001000.safetensors")

    with hub.transaction() as conn:
        other = int(
            conn.execute(
                "INSERT INTO adapter_stack (name, created_at, updated_at) "
                "VALUES ('Other', '2026-08-11T00:00:00Z', '2026-08-11T00:00:00Z')"
            ).lastrowid
        )
        conn.execute("UPDATE model SET stack_id = ? WHERE id = ?", (other, second))

    with pytest.raises(StackRefused) as exc:
        apply_stack(hub, [first, second], "Race")
    assert exc.value.reason == "already_stacked"

    # Nothing was written: the survivor is still loose and the other stack is
    # untouched.
    row = hub.fetchone("SELECT stack_id FROM model WHERE id = ?", (first,))
    assert row["stack_id"] is None
    row = hub.fetchone("SELECT stack_id FROM model WHERE id = ?", (second,))
    assert row["stack_id"] == other


def test_applying_refuses_a_model_with_no_copy_on_disk(hub, tmp_path):
    """The route must not offer what the dry run refuses.

    `propose_stacks` skips a model whose only copies are `missing` or
    `unreachable` — files nobody has seen are not something to reorganise a
    shelf around. Without the same gate here, `POST /model-stacks` would be a
    way to build a stack the detector would never have suggested.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    gone_a = _adapter(hub, folder, "Gone_000000500.safetensors", state="missing")
    gone_b = _adapter(hub, folder, "Gone_000001000.safetensors", state="unreachable")

    with pytest.raises(StackRefused) as exc:
        apply_stack(hub, [gone_a, gone_b], "Gone")
    assert exc.value.reason == "already_stacked"


def test_a_row_that_stops_being_loose_between_the_gate_and_the_update_aborts(
    hub, tmp_path
):
    """The gate is re-checked on the UPDATE itself, not merely read first.

    **This test used to drive a SECOND CONNECTION into the gap**, because the
    gap was reachable: the hub connects ``isolation_level=""``, so pysqlite
    opened a transaction on DML only and the gate SELECT ran in autocommit,
    taking no lock. `HubDatabase.transaction` now opens with ``BEGIN
    IMMEDIATE``, so that connection is refused the write lock instead of
    slipping in, and the old version of this test failed with "database is
    locked" rather than proving anything. The cross-process half is asserted
    directly now, in
    `test_hub_engine.py::test_another_process_cannot_write_between_a_gate_read_and_its_write`.

    So the interleaved write goes through `hub.connection` instead, which is the
    SAME connection `apply_stack` is holding, and therefore lands inside its open
    transaction. That still exercises exactly what the guard is for: a row that
    is loose when the gate reads it and is not loose by the time its own UPDATE
    runs, whatever moved it.

    **The write is forced INTO the gap rather than run before it.** A first
    attempt committed before calling `apply_stack`, which the gate SELECT simply
    excluded: it exercised the SELECT, left the UPDATE guard untested, and
    stayed green with that guard deleted. `_step_of` is called by the cover sort,
    which runs after the SELECT and before the first UPDATE, so patching it is
    what puts the write exactly where the guard has to catch it.
    """
    from pixlstash.services import stack_detector

    folder = _folder(hub, str(tmp_path / "loras"))
    first = _adapter(hub, folder, "Race_000000500.safetensors")
    second = _adapter(hub, folder, "Race_000001000.safetensors")

    real_step_of = stack_detector._step_of
    landed = []

    def write_inside_the_open_transaction(filename):
        if not landed:
            landed.append(True)
            conn = hub.connection
            conn.execute(
                "INSERT INTO adapter_stack (id, name, created_at, updated_at) "
                "VALUES (99, 'Other', '2026-08-11T00:00:00Z', "
                "'2026-08-11T00:00:00Z')"
            )
            conn.execute("UPDATE model SET stack_id = 99 WHERE id = ?", (second,))
        return real_step_of(filename)

    stack_detector._step_of = write_inside_the_open_transaction
    try:
        with pytest.raises(StackRefused):
            apply_stack(hub, [first, second], "Race")
    finally:
        stack_detector._step_of = real_step_of

    assert landed, "the interleaved write never ran; the window was not exercised"

    # StackRefused is not a sqlite3.Error, so it leaves `transaction()` through
    # `with self._conn`, which rolls back. Nothing half-landed: not the stack the
    # call was trying to build, and not the interleaved write either.
    row = hub.fetchone("SELECT stack_id FROM model WHERE id = ?", (second,))
    assert row["stack_id"] is None
    row = hub.fetchone("SELECT stack_id FROM model WHERE id = ?", (first,))
    assert row["stack_id"] is None
    assert hub.fetchone("SELECT id FROM adapter_stack WHERE id = 99") is None
    stacks = hub.fetchone("SELECT COUNT(*) AS n FROM adapter_stack")
    # Zero, where the second-connection version of this test expected one: the
    # interleaved write is now inside the SAME transaction, so the rollback
    # takes it too. There is no committed outside writer left to survive.
    assert stacks["n"] == 0, "a rolled-back INSERT left an orphan stack row"


def test_applying_refuses_models_that_are_not_all_in_one_folder(hub, tmp_path):
    """ "Grouped per folder, never shelf-wide" is the module's invariant, and it
    used to hold only in `propose_stacks`.

    `apply_stack` checked that each model had SOME present copy, not that they
    shared a folder, so the route could build a stack whose members sit on two
    drives — the run that never existed, which is exactly what the per-folder
    rule exists to prevent. Reported by the review of #882.
    """
    first = _folder(hub, str(tmp_path / "disk-a"))
    second = _folder(hub, str(tmp_path / "disk-b"))
    here = _adapter(hub, first, "Split_000000500.safetensors")
    there = _adapter(hub, second, "Split_000001000.safetensors")

    with pytest.raises(StackRefused) as exc:
        apply_stack(hub, [here, there], "Split")
    assert exc.value.reason == "not_one_folder"

    for model_id in (here, there):
        row = hub.fetchone("SELECT stack_id FROM model WHERE id = ?", (model_id,))
        assert row["stack_id"] is None


def test_a_model_in_two_folders_can_still_join_a_run_in_one_of_them(hub, tmp_path):
    """The positive control for the rule above — over-blocking is its own
    regression. A model copied into two folders shares a folder with its run,
    so the run still stacks."""
    first = _folder(hub, str(tmp_path / "disk-a"))
    second = _folder(hub, str(tmp_path / "disk-b"))
    everywhere = _adapter(hub, first, "Both_000000500.safetensors")
    with hub.transaction() as conn:
        conn.execute(
            "INSERT INTO model_file (model_id, model_folder_id, relpath, state) "
            "VALUES (?, ?, 'Both_000000500.safetensors', 'present')",
            (everywhere, second),
        )
    sibling = _adapter(hub, first, "Both_000001000.safetensors")

    stack_id = apply_stack(hub, [everywhere, sibling], "Both")
    rows = hub.fetchall("SELECT id FROM model WHERE stack_id = ?", (stack_id,))
    assert {row["id"] for row in rows} == {everywhere, sibling}


def test_a_model_in_two_folders_is_proposed_under_a_stable_one(hub, tmp_path):
    """One model legitimately has many `model_file` rows, and a bare
    `mf.model_folder_id` beside `GROUP BY m.id` lets SQLite return any of them.

    That would make proposals nondeterministic — the same run grouping under a
    different folder call to call, and two members of one run potentially
    landing in different groups and never being offered together. `MIN()` is
    what makes the answer stable.
    """
    lower = _folder(hub, str(tmp_path / "disk-a"))
    higher = _folder(hub, str(tmp_path / "disk-b"))
    assert lower < higher

    # The HIGHER folder's location is written first, deliberately. With the
    # lower one first, SQLite returns it for a bare column too and the
    # assertion below cannot tell the two implementations apart — measured:
    # the mutant survived until this order was flipped.
    for name in ("Stable_000000500.safetensors", "Stable_000001000.safetensors"):
        model_id = _adapter(hub, higher, name)
        with hub.transaction() as conn:
            conn.execute(
                "INSERT INTO model_file (model_id, model_folder_id, relpath, "
                "state) VALUES (?, ?, ?, 'present')",
                (model_id, lower, name),
            )

    seen = {propose_stacks(hub)[0].folder_id for _ in range(5)}
    assert seen == {lower}


def test_applying_ignores_a_duplicate_id(hub, tmp_path):
    """A list naming the same model twice is not two members."""
    folder = _folder(hub, str(tmp_path / "loras"))
    one = _adapter(hub, folder, "Dup_000000500.safetensors")

    with pytest.raises(StackRefused):
        apply_stack(hub, [one, one], None)
