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
    MAX_MEMBERS_PER_STACK,
    StackRefused,
    apply_stack,
    propose_stacks,
    remove_member,
    repair_stacks,
    set_cover,
    unstack,
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
        "JimmyVehicle_000000500.safetensors",
        "JimmyVehicle_000001000.safetensors",
        "JimmyVehicle.safetensors",
    ):
        _adapter(hub, folder, name)

    proposals = propose_stacks(hub)
    assert _names(proposals) == ["JimmyVehicle"]
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


def test_two_versions_of_one_subject_are_one_stack(hub, tmp_path):
    """The ask: a stack is a subject, not a training run.

    `Foxglove_v2` shares no training run with `Foxglove` - it is a retrain - but
    it is the same character on the shelf, so it belongs behind one row.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    _adapter(hub, folder, "Foxglove.safetensors")
    _adapter(hub, folder, "Foxglove_v2.safetensors")
    _adapter(hub, folder, "Foxglove_v3.safetensors")

    proposals = propose_stacks(hub)
    assert _names(proposals) == ["Foxglove"]
    assert proposals[0].tier == "version_group"
    # Newest version covers, and the strip reads backwards through them. The
    # unversioned file is v1: it existed before v2 did.
    assert [m.version for m in proposals[0].members] == ["v3", "v2", None]


def test_a_decimal_version_sorts_above_its_major(hub, tmp_path):
    """`v2.1` is later than `v2`, and `v10` is later than `v9`.

    Both fail under a string compare, which is why the token is parsed into
    `(major, minor)` rather than sorted as written.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    _adapter(hub, folder, "Clementine_v2.safetensors")
    _adapter(hub, folder, "Clementine_v10.safetensors")
    _adapter(hub, folder, "Clementine_v2.1.safetensors")

    members = propose_stacks(hub)[0].members
    assert [m.version for m in members] == ["v10", "v2.1", "v2"]


def test_versions_and_steps_order_versions_first(hub, tmp_path):
    """Inside a version the old rule is unchanged; between versions it loses.

    Step 4000 of v1 is a later moment in time than the bare final of v2, and the
    cover is still v2: what a person means by "the LoRA" is the newest version,
    not the newest file.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    _adapter(hub, folder, "Marigold_000004000.safetensors")
    _adapter(hub, folder, "Marigold_000000500.safetensors")
    _adapter(hub, folder, "Marigold_v2.safetensors")
    _adapter(hub, folder, "Marigold_v2_000000500.safetensors")

    members = propose_stacks(hub)[0].members
    assert [(m.version, m.step) for m in members] == [
        ("v2", None),
        ("v2", 500),
        (None, 4000),
        (None, 500),
    ]


def test_a_single_version_keeps_it_in_the_stack_name(hub, tmp_path):
    """Grouping strips the version; naming must not.

    A run of `portrait_mix_v2` checkpoints is called "portrait mix v2". Dropping
    the version would rename every versioned run already on the shelf.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    _adapter(hub, folder, "portrait_mix_v2_000000500.safetensors")
    _adapter(hub, folder, "portrait_mix_v2_000001000.safetensors")

    proposals = propose_stacks(hub)
    assert _names(proposals) == ["portrait mix v2"]
    assert proposals[0].tier == "step_group"


def test_the_stack_name_keeps_the_version_s_own_capitals(hub, tmp_path):
    """The name is what gets persisted, so folding its case renames the run.

    An all-lowercase fixture cannot see this: `.lower()` and `.upper()` both
    leave `portrait mix v2` alone, so the assertion above stays green either
    way. `_V3` is what makes the mutation visible.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    _adapter(hub, folder, "portrait_mix_V3_000000500.safetensors")
    _adapter(hub, folder, "portrait_mix_V3_000001000.safetensors")

    assert _names(propose_stacks(hub)) == ["portrait mix V3"]


def test_a_unicode_digit_is_not_a_version(hub, tmp_path):
    """Python's `\\d` spans every Unicode decimal; JavaScript's does not.

    Without `re.ASCII` the server reads `v٢` as version 2 while the shelf that
    draws the strip reads no version at all, and the two halves disagree about
    what a member is.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    _adapter(hub, folder, "Marigold_v٢.safetensors")
    _adapter(hub, folder, "Marigold_v2.safetensors")

    # Only one of the two carries a version, so there is no second version to
    # differ from and nothing is proposed.
    assert propose_stacks(hub) == []


def test_a_bare_trailing_digit_is_not_a_version(hub, tmp_path):
    """`JimmyVehicle` beside `JimmyVehicle2` is the prefix case, not offered.

    Only an explicit `v<digits>` counts. Reading a bare digit as a version would
    silently merge two subjects that may have nothing to do with each other.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    _adapter(hub, folder, "JimmyVehicle.safetensors")
    _adapter(hub, folder, "JimmyVehicle2.safetensors")

    assert propose_stacks(hub) == []


def test_a_bare_version_file_groups_nothing(hub, tmp_path):
    """Nothing survives the strip for `v2.safetensors`.

    Same refusal as a name that is only a step number: grouping on the empty
    string would collapse every such file in a folder into one invented subject.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    _adapter(hub, folder, "v2.safetensors")
    _adapter(hub, folder, "v3.safetensors")

    assert propose_stacks(hub) == []


def test_an_unversioned_file_beside_v1_is_not_a_subject_with_a_history(hub, tmp_path):
    """`Foxglove` and `Foxglove_v1` are the same version, so they are a copy.

    The distinctness test runs on the parsed version rather than the token; if
    it ran on the token these two would read as two versions and a duplicate
    would be presented as a history.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    _adapter(hub, folder, "Foxglove.safetensors")
    _adapter(hub, folder, "Foxglove_v1.safetensors")

    assert propose_stacks(hub) == []


def test_versions_in_two_folders_are_still_never_one_group(hub, tmp_path):
    """The per-folder rule is not weakened by the version rule."""
    first = _folder(hub, str(tmp_path / "disk-a"))
    second = _folder(hub, str(tmp_path / "disk-b"))
    _adapter(hub, first, "Foxglove.safetensors")
    _adapter(hub, second, "Foxglove_v2.safetensors")

    assert propose_stacks(hub) == []


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
    stack's members on two drives - which the move verb would then have to
    reason about.
    """
    first = _folder(hub, str(tmp_path / "disk-a"))
    second = _folder(hub, str(tmp_path / "disk-b"))
    _adapter(hub, first, "JimmyVehicle_000000500.safetensors")
    _adapter(hub, second, "JimmyVehicle_000001000.safetensors")

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
    _adapter(hub, folder, "JimmyVehicle_000000500.safetensors")
    _adapter(hub, folder, "JimmyVehicle_000001000.safetensors")

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


def test_applying_covers_a_version_stack_with_its_newest_version(hub, tmp_path):
    """The other half of the ask: pick the cover from name and version alone.

    No member here carries a step, so the old rule had nothing to sort on and
    the cover fell out of whatever order the ids happened to arrive in. The ids
    are handed over oldest-first precisely so that order cannot be what passes.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    first = _adapter(hub, folder, "Foxglove.safetensors")
    second = _adapter(hub, folder, "Foxglove_v2.safetensors")
    third = _adapter(hub, folder, "Foxglove_v10.safetensors")

    stack_id = apply_stack(hub, [first, second, third], "Foxglove")

    rows = hub.fetchall(
        "SELECT id, stack_position FROM model WHERE stack_id = ? "
        "ORDER BY stack_position",
        (stack_id,),
    )
    assert [r["id"] for r in rows] == [third, second, first]


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
    torn out of it - and if that leaves fewer than two, nothing is written at
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
    `unreachable` - files nobody has seen are not something to reorganise a
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
    drives - the run that never existed, which is exactly what the per-folder
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
    """The positive control for the rule above - over-blocking is its own
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

    That would make proposals nondeterministic - the same run grouping under a
    different folder call to call, and two members of one run potentially
    landing in different groups and never being offered together. `MIN()` is
    what makes the answer stable.
    """
    lower = _folder(hub, str(tmp_path / "disk-a"))
    higher = _folder(hub, str(tmp_path / "disk-b"))
    assert lower < higher

    # The HIGHER folder's location is written first, deliberately. With the
    # lower one first, SQLite returns it for a bare column too and the
    # assertion below cannot tell the two implementations apart - measured:
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


# ── fusing ──────────────────────────────────────────────────────────────────


def _members(hub, stack_id):
    return [
        int(row["id"])
        for row in hub.fetchall(
            "SELECT id FROM model WHERE stack_id = ? ORDER BY stack_position",
            (stack_id,),
        )
    ]


def test_stacking_two_stacks_fuses_them(hub, tmp_path):
    """The ask. Two stacks selected on the shelf become one.

    Without `fuse` every member is already stacked, so the gate empties the
    selection and the call is refused - which is right for the proposals flow
    and wrong for a person pointing at two rows and asking for one.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    v1_final = _adapter(hub, folder, "JimmyVehicle.safetensors")
    v1_step = _adapter(hub, folder, "JimmyVehicle_000000500.safetensors")
    v2_final = _adapter(hub, folder, "JimmyVehicle_v2.safetensors")
    v2_step = _adapter(hub, folder, "JimmyVehicle_v2_000000500.safetensors")

    first = apply_stack(hub, [v1_final, v1_step], "JimmyVehicle")
    second = apply_stack(hub, [v2_final, v2_step], "JimmyVehicle v2")

    fused = apply_stack(hub, [v1_final, v2_final], "JimmyVehicle", fuse=True)

    # Every member of both, cover-first: newest version leads, then its bare
    # final, then its steps.
    assert _members(hub, fused) == [v2_final, v2_step, v1_final, v1_step]
    # And the two absorbed stacks are gone rather than left as empty rows.
    for old in (first, second):
        assert hub.fetchone("SELECT id FROM adapter_stack WHERE id = ?", (old,)) is None


def test_fusing_absorbs_a_whole_stack_not_the_members_named(hub, tmp_path):
    """A stack is atomic, so naming one member takes all of them.

    The alternative leaves a remnant: a stack that gained a member between the
    click and the call would keep that one member and become a "stack" of one.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    a_one = _adapter(hub, folder, "Marigold_000000500.safetensors")
    a_two = _adapter(hub, folder, "Marigold_000001000.safetensors")
    a_three = _adapter(hub, folder, "Marigold_000002000.safetensors")
    loose = _adapter(hub, folder, "Marigold_v2.safetensors")

    original = apply_stack(hub, [a_one, a_two, a_three], "Marigold")

    # Only ONE of the three is named, beside a loose file.
    fused = apply_stack(hub, [a_one, loose], None, fuse=True)

    assert sorted(_members(hub, fused)) == sorted([a_one, a_two, a_three, loose])
    assert (
        hub.fetchone("SELECT id FROM adapter_stack WHERE id = ?", (original,)) is None
    )


def test_fusing_inherits_a_name_rather_than_dropping_it(hub, tmp_path):
    """The stack's name is the one field its files do not carry."""
    folder = _folder(hub, str(tmp_path / "loras"))
    first_a = _adapter(hub, folder, "Clementine_000000500.safetensors")
    first_b = _adapter(hub, folder, "Clementine_000001000.safetensors")
    loose = _adapter(hub, folder, "Clementine_v2.safetensors")
    apply_stack(hub, [first_a, first_b], "Clementine")

    fused = apply_stack(hub, [first_a, loose], None, fuse=True)

    row = hub.fetchone("SELECT name FROM adapter_stack WHERE id = ?", (fused,))
    assert row["name"] == "Clementine"


def test_stacking_a_stacked_model_is_still_refused_without_fuse(hub, tmp_path):
    """The default gate is unchanged, and that is the point of the flag.

    The proposals flow confirms a dry run over loose files. A row stacked in the
    meantime must be left in the stack it has, not silently rehomed - so fusing
    had to be something a caller asks for.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    one = _adapter(hub, folder, "Foxglove_000000500.safetensors")
    two = _adapter(hub, folder, "Foxglove_000001000.safetensors")
    loose = _adapter(hub, folder, "Foxglove_v2.safetensors")
    original = apply_stack(hub, [one, two], "Foxglove")

    with pytest.raises(StackRefused) as exc:
        apply_stack(hub, [one, loose], None)
    assert exc.value.reason == "already_stacked"

    # Nothing moved: the original stack still has both of its members.
    assert sorted(_members(hub, original)) == sorted([one, two])
    row = hub.fetchone("SELECT stack_id FROM model WHERE id = ?", (loose,))
    assert row["stack_id"] is None


def test_fusing_never_takes_a_row_from_a_stack_it_is_not_absorbing(hub, tmp_path):
    """The race guard survives the flag.

    `fuse` widens the gate to the stacks this call is absorbing and no further.
    A row that moved into some third stack between the widening and its own
    UPDATE still aborts the whole thing.
    """
    from pixlstash.services import stack_detector

    folder = _folder(hub, str(tmp_path / "loras"))
    one = _adapter(hub, folder, "Race_000000500.safetensors")
    two = _adapter(hub, folder, "Race_000001000.safetensors")

    real_step_of = stack_detector._step_of
    landed = []

    def write_inside_the_open_transaction(filename):
        # Same shape as the non-fusing race test: `_step_of` runs after the gate
        # SELECT and before the first UPDATE, so this lands exactly in the gap.
        if not landed:
            landed.append(True)
            conn = hub.connection
            conn.execute(
                "INSERT INTO adapter_stack (id, name, created_at, updated_at) "
                "VALUES (77, 'Third', '2026-08-11T00:00:00Z', "
                "'2026-08-11T00:00:00Z')"
            )
            conn.execute("UPDATE model SET stack_id = 77 WHERE id = ?", (two,))
        return real_step_of(filename)

    stack_detector._step_of = write_inside_the_open_transaction
    try:
        with pytest.raises(StackRefused):
            apply_stack(hub, [one, two], "Race", fuse=True)
    finally:
        stack_detector._step_of = real_step_of

    assert landed, "the interleaved write never ran; the window was not exercised"
    for model_id in (one, two):
        row = hub.fetchone("SELECT stack_id FROM model WHERE id = ?", (model_id,))
        assert row["stack_id"] is None
    assert hub.fetchone("SELECT COUNT(*) AS n FROM adapter_stack")["n"] == 0


# ── unstacking ──────────────────────────────────────────────────────────────


def test_unstacking_releases_every_member_and_removes_the_stack(hub, tmp_path):
    folder = _folder(hub, str(tmp_path / "loras"))
    one = _adapter(hub, folder, "Foxglove_000000500.safetensors")
    two = _adapter(hub, folder, "Foxglove_000001000.safetensors")
    stack_id = apply_stack(hub, [one, two], "Foxglove")

    assert unstack(hub, stack_id) == 2

    for model_id in (one, two):
        row = hub.fetchone(
            "SELECT stack_id, stack_position FROM model WHERE id = ?", (model_id,)
        )
        assert row["stack_id"] is None
        # The position goes too. A released row keeping `stack_position = 1`
        # would sort as though it were still behind a cover.
        assert row["stack_position"] is None
    assert (
        hub.fetchone("SELECT id FROM adapter_stack WHERE id = ?", (stack_id,)) is None
    )


def test_unstacking_touches_no_file_row(hub, tmp_path):
    """It writes two columns and deletes one row. Nothing on disk moves."""
    folder = _folder(hub, str(tmp_path / "loras"))
    one = _adapter(hub, folder, "Foxglove_000000500.safetensors")
    two = _adapter(hub, folder, "Foxglove_000001000.safetensors")
    stack_id = apply_stack(hub, [one, two], "Foxglove")
    before = hub.fetchall(
        "SELECT model_id, model_folder_id, relpath, state FROM model_file "
        "ORDER BY model_id"
    )

    unstack(hub, stack_id)

    after = hub.fetchall(
        "SELECT model_id, model_folder_id, relpath, state FROM model_file "
        "ORDER BY model_id"
    )
    assert [tuple(r) for r in after] == [tuple(r) for r in before]


def test_unstacking_an_id_that_names_no_stack_changes_nothing(hub, tmp_path):
    """A wrong address must not half-release rows on its way to saying so."""
    folder = _folder(hub, str(tmp_path / "loras"))
    one = _adapter(hub, folder, "Foxglove_000000500.safetensors")
    two = _adapter(hub, folder, "Foxglove_000001000.safetensors")
    stack_id = apply_stack(hub, [one, two], "Foxglove")

    with pytest.raises(StackRefused) as exc:
        unstack(hub, stack_id + 999)
    assert exc.value.reason == "no_such_stack"

    assert sorted(_members(hub, stack_id)) == sorted([one, two])


def test_unstacked_members_can_be_proposed_again(hub, tmp_path):
    """Stated because it is a consequence, not an accident.

    Released files are loose, so detection sees them again. Unstacking undoes a
    grouping; it does not record a refusal, and there is nowhere yet to keep one.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    one = _adapter(hub, folder, "Foxglove_000000500.safetensors")
    two = _adapter(hub, folder, "Foxglove_000001000.safetensors")
    stack_id = apply_stack(hub, [one, two], "Foxglove")
    assert propose_stacks(hub) == []

    unstack(hub, stack_id)

    assert _names(propose_stacks(hub)) == ["Foxglove"]


def test_a_fused_stack_can_be_unstacked_back_to_loose_files(hub, tmp_path):
    """The round trip, end to end: group, fuse, undo."""
    folder = _folder(hub, str(tmp_path / "loras"))
    ids = [
        _adapter(hub, folder, "Foxglove.safetensors"),
        _adapter(hub, folder, "Foxglove_000000500.safetensors"),
        _adapter(hub, folder, "Foxglove_v2.safetensors"),
        _adapter(hub, folder, "Foxglove_v2_000000500.safetensors"),
    ]
    first = apply_stack(hub, ids[:2], "Foxglove")
    apply_stack(hub, ids[2:], "Foxglove v2")
    fused = apply_stack(hub, [ids[0], ids[2]], "Foxglove", fuse=True)
    assert len(_members(hub, fused)) == 4

    assert unstack(hub, fused) == 4

    assert hub.fetchone("SELECT COUNT(*) AS n FROM adapter_stack")["n"] == 0
    loose = hub.fetchone("SELECT COUNT(*) AS n FROM model WHERE stack_id IS NULL")
    assert loose["n"] == 4
    assert first != fused


def test_fusing_cannot_walk_past_the_member_ceiling(hub, tmp_path):
    """Reported in review of #999, and reproduced before it was fixed.

    The route counts the ids it was SENT. Fusing then widens that set to every
    member of every stack absorbed, so two stacks of 150 arrive as two ids and
    would have left as a 300-member stack - measured at 300 against a ceiling of
    200. A limit the widening step can walk past is not a limit, so the count is
    repeated by the function that does the widening.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    half = MAX_MEMBERS_PER_STACK // 2 + 10
    first = [_adapter(hub, folder, f"Big_{i:09d}.safetensors") for i in range(half)]
    second = [_adapter(hub, folder, f"Other_{i:09d}.safetensors") for i in range(half)]
    left = apply_stack(hub, first, "Big")
    right = apply_stack(hub, second, "Other")

    # Two ids submitted, which is what makes the route's own check useless here.
    with pytest.raises(StackRefused) as exc:
        apply_stack(hub, [first[0], second[0]], "Fused", fuse=True)
    assert exc.value.reason == "too_many_models"

    # And nothing was written on the way to refusing: both stacks are intact.
    assert len(_members(hub, left)) == half
    assert len(_members(hub, right)) == half


def test_fusing_up_to_the_ceiling_still_works(hub, tmp_path):
    """The positive control. Over-blocking is its own regression, and a check
    written on the wrong side of the comparison would refuse everything."""
    folder = _folder(hub, str(tmp_path / "loras"))
    half = MAX_MEMBERS_PER_STACK // 2
    first = [_adapter(hub, folder, f"Fits_{i:09d}.safetensors") for i in range(half)]
    second = [_adapter(hub, folder, f"Also_{i:09d}.safetensors") for i in range(half)]
    apply_stack(hub, first, "Fits")
    apply_stack(hub, second, "Also")

    fused = apply_stack(hub, [first[0], second[0]], "Fused", fuse=True)

    assert len(_members(hub, fused)) == MAX_MEMBERS_PER_STACK


# ── choosing the cover, and taking a member out ─────────────────────────────


def test_choosing_a_cover_promotes_it_and_keeps_the_rest_in_order(hub, tmp_path):
    """The filename heuristic is a default, not a verdict.

    The owner knows step 1000 is the good checkpoint; the names cannot. What
    the promotion must NOT do is shuffle everything else, so the assertion is
    on the whole order rather than on position 0 alone.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    final = _adapter(hub, folder, "Foxglove.safetensors")
    high = _adapter(hub, folder, "Foxglove_000002000.safetensors")
    low = _adapter(hub, folder, "Foxglove_000001000.safetensors")
    stack_id = apply_stack(hub, [final, high, low], "Foxglove")
    assert _members(hub, stack_id) == [final, high, low]

    assert set_cover(hub, stack_id, low) == [low, final, high]
    assert _members(hub, stack_id) == [low, final, high]


def test_choosing_the_cover_that_is_already_the_cover_changes_nothing(hub, tmp_path):
    folder = _folder(hub, str(tmp_path / "loras"))
    final = _adapter(hub, folder, "Foxglove.safetensors")
    step = _adapter(hub, folder, "Foxglove_000002000.safetensors")
    stack_id = apply_stack(hub, [final, step], "Foxglove")

    assert set_cover(hub, stack_id, final) == [final, step]
    assert _members(hub, stack_id) == [final, step]


def test_choosing_a_cover_from_another_stack_is_refused(hub, tmp_path):
    """A member of somebody else's run must not be dragged into this one."""
    folder = _folder(hub, str(tmp_path / "loras"))
    mine = [
        _adapter(hub, folder, "Foxglove.safetensors"),
        _adapter(hub, folder, "Foxglove_000001000.safetensors"),
    ]
    theirs = [
        _adapter(hub, folder, "Clementine.safetensors"),
        _adapter(hub, folder, "Clementine_000001000.safetensors"),
    ]
    stack_id = apply_stack(hub, mine, "Foxglove")
    other = apply_stack(hub, theirs, "Clementine")

    with pytest.raises(StackRefused) as exc:
        set_cover(hub, stack_id, theirs[1])
    assert exc.value.reason == "not_a_member"

    assert _members(hub, stack_id) == mine
    assert _members(hub, other) == theirs


def test_choosing_a_cover_in_a_stack_that_is_not_there_is_refused(hub, tmp_path):
    folder = _folder(hub, str(tmp_path / "loras"))
    one = _adapter(hub, folder, "Foxglove.safetensors")
    two = _adapter(hub, folder, "Foxglove_000001000.safetensors")
    stack_id = apply_stack(hub, [one, two], "Foxglove")

    with pytest.raises(StackRefused) as exc:
        set_cover(hub, stack_id + 999, one)
    assert exc.value.reason == "no_such_stack"

    assert _members(hub, stack_id) == [one, two]


def test_a_chosen_cover_is_not_re_proposed_or_reordered(hub, tmp_path):
    """The choice sticks, which is the whole point of making it.

    Detection only ever looks at loose adapters, so nothing recomputes the
    order of a stack that already exists. Asserted rather than assumed: a
    future finder that renumbered stacked rows would silently undo every cover
    the owner ever set.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    final = _adapter(hub, folder, "Foxglove.safetensors")
    step = _adapter(hub, folder, "Foxglove_000001000.safetensors")
    stack_id = apply_stack(hub, [final, step], "Foxglove")
    set_cover(hub, stack_id, step)

    assert propose_stacks(hub) == []
    assert _members(hub, stack_id) == [step, final]


def test_removing_a_member_leaves_it_loose_and_renumbers_the_rest(hub, tmp_path):
    folder = _folder(hub, str(tmp_path / "loras"))
    final = _adapter(hub, folder, "Foxglove.safetensors")
    high = _adapter(hub, folder, "Foxglove_000002000.safetensors")
    low = _adapter(hub, folder, "Foxglove_000001000.safetensors")
    stack_id = apply_stack(hub, [final, high, low], "Foxglove")

    assert remove_member(hub, stack_id, high) == (1, False)

    released = hub.fetchone(
        "SELECT stack_id, stack_position FROM model WHERE id = ?", (high,)
    )
    assert released["stack_id"] is None
    assert released["stack_position"] is None
    # Contiguous from 0, so the survivors still have exactly one cover.
    rows = hub.fetchall(
        "SELECT id, stack_position FROM model WHERE stack_id = ? "
        "ORDER BY stack_position",
        (stack_id,),
    )
    assert [(int(r["id"]), r["stack_position"]) for r in rows] == [
        (final, 0),
        (low, 1),
    ]


def test_removing_the_cover_promotes_the_member_behind_it(hub, tmp_path):
    folder = _folder(hub, str(tmp_path / "loras"))
    final = _adapter(hub, folder, "Foxglove.safetensors")
    high = _adapter(hub, folder, "Foxglove_000002000.safetensors")
    low = _adapter(hub, folder, "Foxglove_000001000.safetensors")
    stack_id = apply_stack(hub, [final, high, low], "Foxglove")

    remove_member(hub, stack_id, final)

    assert _members(hub, stack_id) == [high, low]


def test_removing_the_second_to_last_member_dissolves_the_stack(hub, tmp_path):
    """One file is not a run, and a stack of one is a grouping nobody can see."""
    folder = _folder(hub, str(tmp_path / "loras"))
    one = _adapter(hub, folder, "Foxglove.safetensors")
    two = _adapter(hub, folder, "Foxglove_000001000.safetensors")
    stack_id = apply_stack(hub, [one, two], "Foxglove")

    assert remove_member(hub, stack_id, one) == (2, True)

    for model_id in (one, two):
        row = hub.fetchone(
            "SELECT stack_id, stack_position FROM model WHERE id = ?", (model_id,)
        )
        assert row["stack_id"] is None
        assert row["stack_position"] is None
    assert (
        hub.fetchone("SELECT id FROM adapter_stack WHERE id = ?", (stack_id,)) is None
    )


def test_removing_a_member_touches_no_file_row(hub, tmp_path):
    """The `model_file` rows are untouched, which is the recorded half.

    Stated as what it proves rather than as "nothing on disk": this asserts the
    rows, and the reason no file moves is that the function contains no
    filesystem call at all.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    one = _adapter(hub, folder, "Foxglove.safetensors")
    two = _adapter(hub, folder, "Foxglove_000001000.safetensors")
    three = _adapter(hub, folder, "Foxglove_000002000.safetensors")
    stack_id = apply_stack(hub, [one, two, three], "Foxglove")
    before = hub.fetchall(
        "SELECT model_id, model_folder_id, relpath, state FROM model_file "
        "ORDER BY model_id"
    )

    remove_member(hub, stack_id, two)

    after = hub.fetchall(
        "SELECT model_id, model_folder_id, relpath, state FROM model_file "
        "ORDER BY model_id"
    )
    assert [tuple(r) for r in after] == [tuple(r) for r in before]


def test_removing_a_model_that_is_not_in_the_stack_changes_nothing(hub, tmp_path):
    folder = _folder(hub, str(tmp_path / "loras"))
    one = _adapter(hub, folder, "Foxglove.safetensors")
    two = _adapter(hub, folder, "Foxglove_000001000.safetensors")
    loose = _adapter(hub, folder, "Clementine.safetensors")
    stack_id = apply_stack(hub, [one, two], "Foxglove")

    with pytest.raises(StackRefused) as exc:
        remove_member(hub, stack_id, loose)
    assert exc.value.reason == "not_a_member"

    assert _members(hub, stack_id) == [one, two]


def test_a_removed_member_can_be_stacked_again(hub, tmp_path):
    """It is loose, not marked. Removing undoes a grouping, not a decision."""
    folder = _folder(hub, str(tmp_path / "loras"))
    one = _adapter(hub, folder, "Foxglove.safetensors")
    two = _adapter(hub, folder, "Foxglove_000001000.safetensors")
    three = _adapter(hub, folder, "Foxglove_000002000.safetensors")
    stack_id = apply_stack(hub, [one, two, three], "Foxglove")

    remove_member(hub, stack_id, three)

    assert (
        apply_stack(
            hub, [three, _adapter(hub, folder, "Clementine.safetensors")], "Regrouped"
        )
        > 0
    )


def test_a_member_with_no_position_is_never_promoted_by_accident(hub, tmp_path):
    """NULL sorts FIRST in SQLite, and that is the trap.

    A member with no recorded position must not be read as the cover of the
    order a promotion renumbers from, or promoting one file would silently
    reshuffle the rest around an accident. Written with a NULL in the middle so
    both halves of `ORDER BY stack_position IS NULL, stack_position` are load
    bearing: drop the first term and this order comes back different.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    final = _adapter(hub, folder, "Foxglove.safetensors")
    high = _adapter(hub, folder, "Foxglove_000002000.safetensors")
    low = _adapter(hub, folder, "Foxglove_000001000.safetensors")
    stack_id = apply_stack(hub, [final, high, low], "Foxglove")
    with hub.transaction() as conn:
        conn.execute("UPDATE model SET stack_position = NULL WHERE id = ?", (high,))

    assert set_cover(hub, stack_id, low) == [low, final, high]


def test_repairing_a_stack_closes_the_gaps_a_deleted_member_left(hub, tmp_path):
    """The state `_purge` leaves behind: holes, and no position 0.

    Written against the columns directly rather than through a delete, because
    what is being asserted is the repair itself - every caller that drops a
    member reaches the same function.
    """
    folder = _folder(hub, str(tmp_path / "loras"))
    ids = [_adapter(hub, folder, f"Foxglove_00000{i}000.safetensors") for i in range(3)]
    stack_id = apply_stack(hub, ids, "Foxglove")
    with hub.transaction() as conn:
        # The cover gone, a gap behind it, and one member never positioned.
        conn.execute("UPDATE model SET stack_position = 4 WHERE id = ?", (ids[0],))
        conn.execute("UPDATE model SET stack_position = 9 WHERE id = ?", (ids[1],))
        conn.execute("UPDATE model SET stack_position = NULL WHERE id = ?", (ids[2],))

    with hub.transaction() as conn:
        repair_stacks(conn, [stack_id])

    rows = hub.fetchall(
        "SELECT id, stack_position FROM model WHERE stack_id = ? "
        "ORDER BY stack_position",
        (stack_id,),
    )
    assert [(int(r["id"]), r["stack_position"]) for r in rows] == [
        (ids[0], 0),
        (ids[1], 1),
        (ids[2], 2),
    ]


def test_repairing_dissolves_a_stack_left_with_one_member(hub, tmp_path):
    folder = _folder(hub, str(tmp_path / "loras"))
    one = _adapter(hub, folder, "Foxglove.safetensors")
    two = _adapter(hub, folder, "Foxglove_000001000.safetensors")
    stack_id = apply_stack(hub, [one, two], "Foxglove")
    with hub.transaction() as conn:
        # Children first, as `_purge` does: `model_file` references `model`.
        conn.execute("DELETE FROM model_file WHERE model_id = ?", (two,))
        conn.execute("DELETE FROM model WHERE id = ?", (two,))
        repair_stacks(conn, [stack_id])

    survivor = hub.fetchone(
        "SELECT stack_id, stack_position FROM model WHERE id = ?", (one,)
    )
    assert survivor["stack_id"] is None
    assert survivor["stack_position"] is None
    assert (
        hub.fetchone("SELECT id FROM adapter_stack WHERE id = ?", (stack_id,)) is None
    )


def test_repairing_leaves_an_empty_stack_row_alone(hub, tmp_path):
    """An import inserts its `adapter_stack` row BEFORE its members.

    Deleting empty rows here would therefore race a live import into removing
    the row it is about to point at. The interrupted-import leftover is inert
    and documented as such; this is the assertion that keeps it that way.
    """
    with hub.transaction() as conn:
        stack_id = int(
            conn.execute(
                "INSERT INTO adapter_stack (name, created_at, updated_at) "
                "VALUES ('Mid-import', '2026-08-16T00:00:00Z', "
                "'2026-08-16T00:00:00Z')"
            ).lastrowid
        )
        repair_stacks(conn, [stack_id])

    assert (
        hub.fetchone("SELECT id FROM adapter_stack WHERE id = ?", (stack_id,))
        is not None
    )
