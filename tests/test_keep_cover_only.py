"""Keep cover only: collapsing a stack to its cover (docs/design/keep-cover-only.md).

The action keeps a stack's current cover and soft-deletes every other live
member to the Scrapheap. These cover the contract in **both** directions,
because over-blocking and over-collapsing are each their own regression:

* a hand-made stack has its tags unioned onto the cover **before** anything is
  deleted, and the tag is still there afterwards: the sharpest point in the
  design, because ``apply_metadata_union_in_session`` has only ever been called
  from the dedup verdict, so grid-made stacks have never been unioned;
* a stack with a locked-set member is refused **whole**, while its siblings in
  the **same request** still collapse;
* a stack whose only character link sits on a copy is skipped, counted **and
  named**, and one whose single character the union will propagate is not;
* the survivor keeps its ``stack_id``, no stack row is dissolved, and **one**
  undo restores the stack with its cover, positions and pre-union metadata;
* the preview's stack buckets are disjoint and sum to ``stacks_selected``, and
  the preview and the mutation agree over the same selection;
* both new routes are ``OWNER_ONLY`` at the central gate, a resource-scoped
  READ token is refused through the ``Authorization`` header *and* through
  ``?token=``, and the owner still reaches both.

Background workers are disabled and the pictures are inserted directly, so no
worker can rewrite metadata underneath the assertions.
"""

import gc
import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    Character,
    Face,
    Picture,
    PictureSet,
    PictureSetMember,
    PictureStack,
    Tag,
)
from pixlstash.server import Server
from pixlstash.services import keep_cover_only_service, operation_log_service
from tests.authz_guard import no_spa_fallback  # noqa: F401

API = "/api/v1"
PREVIEW_URL = f"{API}/stacks/keep-cover-only/preview"
COLLAPSE_URL = f"{API}/stacks/keep-cover-only"
UNDO_URL = f"{API}/operations/undo"
RESTORE_URL = f"{API}/pictures/scrapheap/restore"

# The SPA catch-all answers unmatched GETs with 200, so a wrong URL could make a
# positive assertion vacuous. See tests/authz_guard.py.
pytestmark = pytest.mark.usefixtures("no_spa_fallback")


def _run(server, fn, *args):
    return server.vault.db.run_task(fn, *args, priority=DBPriority.IMMEDIATE)


def _make_stack(server, members: list[dict]) -> tuple[int, list[int]]:
    """Insert one stack. ``members[0]`` becomes the cover (position 0).

    Each entry may carry ``tags`` (list[str]), ``score`` (int),
    ``size_bytes`` (int) and ``reference_folder_id``.
    """

    def insert(session):
        stack = PictureStack(name=None)
        session.add(stack)
        session.flush()
        picture_ids = []
        for position, spec in enumerate(members):
            picture = Picture(
                file_path=f"/vault/kco_{int(stack.id)}_{position}.png",
                format="png",
                width=1000,
                height=1000,
                size_bytes=spec.get("size_bytes", 1000),
                score=spec.get("score"),
                reference_folder_id=spec.get("reference_folder_id"),
                stack_id=int(stack.id),
                stack_position=position,
            )
            session.add(picture)
            session.flush()
            for tag in spec.get("tags", []):
                session.add(Tag(picture_id=int(picture.id), tag=tag))
            picture_ids.append(int(picture.id))
        session.commit()
        return int(stack.id), picture_ids

    return _run(server, insert)


def _assign_character(server, picture_id: int, character_name: str) -> int:
    """Give *picture_id* a face bound to a (created-on-demand) character."""

    def assign(session):
        character = session.exec(
            select(Character).where(Character.name == character_name)
        ).first()
        if character is None:
            character = Character(name=character_name)
            session.add(character)
            session.flush()
        session.add(
            Face(
                picture_id=picture_id,
                frame_index=0,
                face_index=0,
                character_id=int(character.id),
                x=0,
                y=0,
                width=10,
                height=10,
            )
        )
        session.commit()
        return int(character.id)

    return _run(server, assign)


def _lock_set_over(client, server, name: str, picture_id: int) -> int:
    """Create a locked picture set containing exactly *picture_id*."""
    set_id = client.post(f"{API}/picture_sets", json={"name": name}).json()[
        "picture_set"
    ]["id"]

    def freeze(session):
        session.add(PictureSetMember(set_id=set_id, picture_id=picture_id))
        picture_set = session.get(PictureSet, set_id)
        picture_set.locked = True
        session.add(picture_set)
        session.commit()

    _run(server, freeze)
    return set_id


def _picture_rows(server, picture_ids: list[int]) -> dict[int, tuple]:
    """``{picture_id: (deleted, stack_id, stack_position, score)}``."""

    def read(session):
        rows = session.exec(
            select(
                Picture.id,
                Picture.deleted,
                Picture.stack_id,
                Picture.stack_position,
                Picture.score,
            ).where(Picture.id.in_(picture_ids))
        ).all()
        return {
            int(pid): (bool(deleted), sid, pos, score)
            for pid, deleted, sid, pos, score in rows
        }

    return _run(server, read)


def _tags_of(server, picture_id: int) -> set[str]:
    def read(session):
        return {
            str(tag)
            for tag in session.exec(
                select(Tag.tag).where(Tag.picture_id == picture_id)
            ).all()
        }

    return _run(server, read)


def _env():
    """Owner cookie client plus a resource-scoped READ share token."""
    temp_dir = tempfile.TemporaryDirectory()
    os.makedirs(os.path.join(temp_dir.name, "images"), exist_ok=True)
    config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(config_path, "w") as handle:
        handle.write(json.dumps({"port": 8000, "disable_background_workers": True}))
    Server.DEFAULT_FORCE_CPU = True
    server = Server(config_path)
    client = TestClient(server.api)
    assert (
        client.post(
            f"{API}/login",
            json={"username": "owner", "password": "example-owner-password"},
        ).status_code
        == 200
    )
    return temp_dir, client, server


def _scoped_token(client, server, picture_id: int) -> str:
    """A resource-scoped READ token over a set holding *picture_id*."""
    set_id = client.post(f"{API}/picture_sets", json={"name": "Scope"}).json()[
        "picture_set"
    ]["id"]

    def add(session):
        session.add(PictureSetMember(set_id=set_id, picture_id=picture_id))
        session.commit()

    _run(server, add)
    return client.post(
        f"{API}/users/me/token",
        json={
            "description": "scope read",
            "scope": "READ",
            "resource_type": "picture_set",
            "resource_id": set_id,
        },
    ).json()["token"]


def _teardown(temp_dir, server):
    server.close()
    temp_dir.cleanup()
    gc.collect()


def _rows_by_stack(body) -> dict:
    return {row["stack_id"]: row for row in body["stacks"]}


# ── the metadata union, the sharpest point ───────────────────────────────────


def test_hand_made_stack_is_unioned_onto_the_cover_before_anything_is_deleted():
    """A grid-made stack's tags land on the cover, and are not lost with the copy.

    ``apply_metadata_union_in_session`` has only ever been called from the dedup
    stack verdict, so a stack made by hand in the grid has never been unioned.
    Both halves are asserted: the tag **arrives** on the cover, and the cover
    still has it once the copy carrying it is in the Scrapheap.
    """
    temp_dir, client, server = _env()
    try:
        stack_id, pics = _make_stack(
            server,
            [
                {"tags": ["cover_only"], "score": 2},
                {"tags": ["copy_only"], "score": 5},
            ],
        )
        cover, copy_id = pics

        # Precondition: this is the un-unioned state the design measured.
        assert _tags_of(server, cover) == {"cover_only"}

        response = client.post(COLLAPSE_URL, json={"stack_ids": [stack_id]})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["stacks_collapsed"] == 1
        assert body["pictures_moved"] == 1
        assert body["picture_ids_moved"] == [copy_id]
        assert body["covers_gaining_metadata"] == 1
        assert body["tags_added"] >= 1

        # The union ran BEFORE the delete: the copy's tag is on the cover, and
        # the copy is gone from every live view.
        assert _tags_of(server, cover) == {"cover_only", "copy_only"}
        rows = _picture_rows(server, pics)
        assert rows[cover][0] is False
        assert rows[copy_id][0] is True
        # Score lifts to the stack's best.
        assert rows[cover][3] == 5
    finally:
        _teardown(temp_dir, server)


def test_survivor_keeps_its_stack_and_one_undo_restores_the_stack():
    """Acceptance criterion 6: nothing is dissolved and undo is a flag flip.

    The cover holds position 0, every member still points at the stack, the
    ``PictureStack`` row survives, and one ``Ctrl+Z`` puts the stack back with
    its positions **and** its pre-union metadata.
    """
    temp_dir, client, server = _env()
    try:
        stack_id, pics = _make_stack(
            server,
            [{"tags": ["a"]}, {"tags": ["b"]}, {"tags": ["c"]}],
        )
        cover = pics[0]

        body = client.post(COLLAPSE_URL, json={"stack_ids": [stack_id]}).json()
        assert body["pictures_moved"] == 2
        batch_id = body["batch_id"]
        assert batch_id

        rows = _picture_rows(server, pics)
        # NOT dissolved and NOT detached: every member still points at the stack.
        assert {row[1] for row in rows.values()} == {stack_id}
        assert rows[cover][2] == 0, "the cover must hold position 0"
        assert all(rows[pid][0] for pid in pics[1:])
        assert _tags_of(server, cover) == {"a", "b", "c"}

        def stack_row(session):
            return session.get(PictureStack, stack_id) is not None

        assert _run(server, stack_row), "the stack row must survive the collapse"

        # One undo reverses the whole thing, the union included: the cover goes
        # back to carrying only its own tag.
        assert (
            client.post(f"{API}/operations/batches/{batch_id}/undo").status_code == 200
        )
        rows = _picture_rows(server, pics)
        assert not any(rows[pid][0] for pid in pics)
        assert {rows[pid][1] for pid in pics} == {stack_id}
        assert rows[cover][2] == 0
        assert _tags_of(server, cover) == {"a"}
    finally:
        _teardown(temp_dir, server)


def test_restoring_a_copy_from_the_scrapheap_returns_it_to_its_stack():
    """Acceptance criterion 7: a restored copy rejoins, it does not land loose.

    This is what leaving ``stack_id`` alone buys. ``restore_pictures`` already
    clears ``deleted_at`` and calls ``normalize_stack_positions``, so the copy
    comes back behind its cover rather than as a loose picture.
    """
    temp_dir, client, server = _env()
    try:
        stack_id, pics = _make_stack(server, [{}, {}, {}])

        assert (
            client.post(COLLAPSE_URL, json={"stack_ids": [stack_id]}).status_code == 200
        )
        assert (
            client.post(RESTORE_URL, json={"picture_ids": [pics[1]]}).status_code == 200
        )

        rows = _picture_rows(server, pics)
        assert rows[pics[1]][0] is False
        assert rows[pics[1]][1] == stack_id
        # The cover is still the leader; the restored copy sorts behind it.
        assert rows[pics[0]][2] == 0
        assert rows[pics[1]][2] == 1
        # The copy that was NOT restored stays in the Scrapheap, still stacked.
        assert rows[pics[2]][0] is True
        assert rows[pics[2]][1] == stack_id
    finally:
        _teardown(temp_dir, server)


# ── locked sets refuse the WHOLE stack, siblings proceed ─────────────────────


def test_locked_member_refuses_the_whole_stack_while_siblings_collapse():
    """One frozen member refuses its stack entirely, and only its stack.

    A partial collapse is the worst available outcome (some copies gone, the
    stack still there, no visible reason), so the refusal is whole-stack. It is
    also *local*: the unlocked sibling in the SAME request still collapses.
    """
    temp_dir, client, server = _env()
    try:
        locked_stack, locked_pics = _make_stack(server, [{}, {}])
        free_stack, free_pics = _make_stack(server, [{}, {}])
        # Freeze a NON-cover member: the lock has to reach the whole stack
        # through the sibling, not only through the row the caller names.
        set_id = _lock_set_over(client, server, "Frozen", locked_pics[1])

        body = client.post(
            COLLAPSE_URL, json={"stack_ids": [locked_stack, free_stack]}
        ).json()

        assert body["stacks_collapsed"] == 1
        assert body["stack_ids_collapsed"] == [free_stack]
        assert body["picture_ids_moved"] == [free_pics[1]]
        skipped = body["stacks_skipped_locked"]
        assert [row["stack_id"] for row in skipped] == [locked_stack]
        assert [entry["id"] for entry in skipped[0]["locked_sets"]] == [set_id]
        assert skipped[0]["copy_picture_ids"] == []

        rows = _picture_rows(server, locked_pics + free_pics)
        # Not one member of the locked stack moved.
        assert not any(rows[pid][0] for pid in locked_pics)
        # The sibling did.
        assert rows[free_pics[1]][0] is True
    finally:
        _teardown(temp_dir, server)


# ── characters: skipped, counted, named, and not over-blocked ───────────────


def test_character_only_on_a_copy_skips_the_stack_and_names_it():
    """A link that would be destroyed skips the stack, and the skip names it."""
    temp_dir, client, server = _env()
    try:
        stack_id, pics = _make_stack(server, [{}, {}])
        # Two characters, so the union refuses to guess; the second sits only on
        # the copy, which is exactly the link a collapse would destroy.
        _assign_character(server, pics[0], "Cover Character")
        stranded = _assign_character(server, pics[1], "Copy Character")

        preview = client.post(PREVIEW_URL, json={"stack_ids": [stack_id]}).json()
        assert preview["stacks_eligible"] == 0
        assert preview["stacks_skipped_character_on_copy"] == 1
        row = _rows_by_stack(preview)[stack_id]
        assert row["skip_reason"] == keep_cover_only_service.SKIP_CHARACTER_ON_COPY
        assert row["lost_characters"] == [
            {"id": stranded, "name": "Copy Character", "picture_ids": [pics[1]]}
        ]

        body = client.post(COLLAPSE_URL, json={"stack_ids": [stack_id]}).json()
        assert body["stacks_collapsed"] == 0
        assert body["pictures_moved"] == 0
        assert [r["stack_id"] for r in body["stacks_skipped_character_on_copy"]] == [
            stack_id
        ]
        assert not any(_picture_rows(server, pics)[pid][0] for pid in pics)
    finally:
        _teardown(temp_dir, server)


def test_a_single_character_on_a_copy_is_propagated_not_skipped():
    """Over-blocking is its own regression: the union handles the one-character case.

    With exactly one character across the stack the union assigns it to the
    cover (through ``pending_character_id``, never a fabricated face), so
    nothing is lost and the stack must still collapse.
    """
    temp_dir, client, server = _env()
    try:
        stack_id, pics = _make_stack(server, [{}, {}])
        character_id = _assign_character(server, pics[1], "Only Character")

        body = client.post(COLLAPSE_URL, json={"stack_ids": [stack_id]}).json()
        assert body["stacks_collapsed"] == 1
        assert body["stacks_skipped_character_on_copy"] == []

        def read_pending(session):
            return session.get(Picture, pics[0]).pending_character_id

        assert _run(server, read_pending) == character_id
    finally:
        _teardown(temp_dir, server)


# ── the dry run: disjoint buckets, and agreement with the mutation ───────────


def test_preview_buckets_are_disjoint_and_sum_to_the_total():
    """Acceptance criterion 1: no bucket is derived by subtraction."""
    temp_dir, client, server = _env()
    try:
        plain_stack, plain_pics = _make_stack(
            server, [{"size_bytes": 10}, {"size_bytes": 700}]
        )
        locked_stack, locked_pics = _make_stack(server, [{}, {}])
        _lock_set_over(client, server, "Frozen", locked_pics[0])
        character_stack, character_pics = _make_stack(server, [{}, {}])
        _assign_character(server, character_pics[0], "A")
        _assign_character(server, character_pics[1], "B")
        single_stack, _single_pics = _make_stack(server, [{}])

        body = client.post(
            PREVIEW_URL,
            json={
                "stack_ids": [
                    plain_stack,
                    locked_stack,
                    character_stack,
                    single_stack,
                ]
            },
        ).json()

        assert body["stacks_selected"] == 4
        assert body["stacks_eligible"] == 1
        assert body["stacks_skipped_locked"] == 1
        assert body["stacks_skipped_character_on_copy"] == 1
        assert body["stacks_skipped_single_member"] == 1
        assert (
            body["stacks_eligible"]
            + body["stacks_skipped_locked"]
            + body["stacks_skipped_character_on_copy"]
            + body["stacks_skipped_single_member"]
            == body["stacks_selected"]
        )
        # Every row appears exactly once, and only the eligible one moves.
        assert len(body["stacks"]) == 4
        assert len({row["stack_id"] for row in body["stacks"]}) == 4
        assert body["pictures_moving"] == 1
        assert body["picture_ids_moving"] == [plain_pics[1]]
        # Bytes HELD, not freed, and only over what would actually move.
        assert body["bytes_held_by_copies"] == 700
        assert body["originals_deleted_from_disk"] == 0
        # The default install never auto-empties the Scrapheap.
        assert body["scrapheap_retention_days"] is None
    finally:
        _teardown(temp_dir, server)


def test_preview_and_mutation_agree_on_the_same_selection():
    """The dialog's figures are the ones the button acts on."""
    temp_dir, client, server = _env()
    try:
        first, first_pics = _make_stack(server, [{"tags": ["x"]}, {"tags": ["y"]}, {}])
        second, second_pics = _make_stack(server, [{}, {}])
        locked, locked_pics = _make_stack(server, [{}, {}])
        _lock_set_over(client, server, "Frozen", locked_pics[1])
        selection = {"stack_ids": [first, second, locked]}

        preview = client.post(PREVIEW_URL, json=selection).json()
        applied = client.post(COLLAPSE_URL, json=selection).json()

        assert applied["stacks_collapsed"] == preview["stacks_eligible"]
        assert applied["pictures_moved"] == preview["pictures_moving"]
        assert applied["picture_ids_moved"] == preview["picture_ids_moving"]
        assert applied["cover_picture_ids"] == preview["cover_picture_ids"]
        assert applied["covers_gaining_metadata"] == preview["covers_gaining_metadata"]
        assert len(applied["stacks_skipped_locked"]) == preview["stacks_skipped_locked"]
        assert (
            applied["reference_folder_pictures_moved"]
            == (preview["reference_folder_pictures_moving"])
        )
        assert set(applied["picture_ids_moved"]) == {
            first_pics[1],
            first_pics[2],
            second_pics[1],
        }
    finally:
        _teardown(temp_dir, server)


def test_a_partial_selection_inside_a_stack_collapses_the_whole_stack():
    """The unit is the stack, and naming one copy names all of them."""
    temp_dir, client, server = _env()
    try:
        stack_id, pics = _make_stack(server, [{}, {}, {}])
        loose_id = _make_stack(server, [{}])[1][0]

        body = client.post(
            PREVIEW_URL, json={"picture_ids": [pics[1], loose_id]}
        ).json()
        # The loose picture's own stack is a stack of one, so it lands in the
        # single-member bucket rather than being silently dropped.
        rows = _rows_by_stack(body)
        assert rows[stack_id]["eligible"] is True
        assert rows[stack_id]["copy_picture_ids"] == pics[1:]
        assert body["pictures_moving"] == 2
    finally:
        _teardown(temp_dir, server)


def test_reference_folder_copies_are_counted_separately():
    """Their rows move; their files are user-managed and are never touched."""
    temp_dir, client, server = _env()
    try:
        folder = client.post(
            f"{API}/reference-folders",
            json={"folder": os.path.join(temp_dir.name, "refs"), "label": "Refs"},
        )
        assert folder.status_code in (200, 201), folder.text
        folder_id = folder.json().get("id") or folder.json()["reference_folder"]["id"]

        stack_id, pics = _make_stack(
            server, [{}, {"reference_folder_id": folder_id}, {}]
        )

        body = client.post(PREVIEW_URL, json={"stack_ids": [stack_id]}).json()
        assert body["pictures_moving"] == 2
        assert body["reference_folder_pictures_moving"] == 1
        assert body["reference_folder_picture_ids_moving"] == [pics[1]]
        assert body["originals_deleted_from_disk"] == 0

        applied = client.post(COLLAPSE_URL, json={"stack_ids": [stack_id]}).json()
        assert applied["reference_folder_pictures_moved"] == 1
        assert applied["originals_deleted_from_disk"] == 0
    finally:
        _teardown(temp_dir, server)


def test_retention_setting_is_served_live_not_hardcoded():
    """The recovery copy must branch on the configured window, including "never"."""
    temp_dir, client, server = _env()
    try:
        stack_id, _pics = _make_stack(server, [{}, {}])
        assert (
            client.post(PREVIEW_URL, json={"stack_ids": [stack_id]}).json()[
                "scrapheap_retention_days"
            ]
            is None
        )
        assert (
            client.patch(
                f"{API}/server-config/scrapheap-retention",
                json={"scrapheap_retention_days": 60},
            ).status_code
            == 200
        )
        assert (
            client.post(PREVIEW_URL, json={"stack_ids": [stack_id]}).json()[
                "scrapheap_retention_days"
            ]
            == 60
        )
    finally:
        _teardown(temp_dir, server)


# ── the operation log ────────────────────────────────────────────────────────


def test_the_whole_call_is_one_operation_under_one_batch():
    """Several stacks, one row, one Ctrl+Z."""
    temp_dir, client, server = _env()
    try:
        first, first_pics = _make_stack(server, [{}, {}])
        second, second_pics = _make_stack(server, [{}, {}])

        body = client.post(COLLAPSE_URL, json={"stack_ids": [first, second]}).json()
        batch_id = body["batch_id"]

        operations = client.get(f"{API}/operations", params={"limit": 10}).json()
        rows = operations if isinstance(operations, list) else operations["operations"]
        mine = [row for row in rows if row.get("batch_id") == batch_id]
        assert len(mine) == 1
        assert mine[0]["op_type"] == operation_log_service.OP_STACK_KEEP_COVER_ONLY
        assert "Scrapheap" in (mine[0]["summary"] or "")
        # The summary must not claim any space was freed.
        assert "free" not in (mine[0]["summary"] or "").lower()

        assert client.post(UNDO_URL, json={"batch_id": batch_id}).status_code == 200
        rows = _picture_rows(server, first_pics + second_pics)
        assert not any(row[0] for row in rows.values())
    finally:
        _teardown(temp_dir, server)


# ── request validation ───────────────────────────────────────────────────────


def test_an_empty_selection_is_a_400_on_both_routes():
    temp_dir, client, server = _env()
    try:
        for url in (PREVIEW_URL, COLLAPSE_URL):
            assert client.post(url, json={}).status_code == 400
            assert client.post(url, json={"stack_ids": []}).status_code == 400
            assert client.post(url, json={"stack_ids": ["nope"]}).status_code == 422, (
                "pydantic rejects a non-integer before the service sees it"
            )
    finally:
        _teardown(temp_dir, server)


def test_an_unknown_stack_id_is_reported_outside_the_bucket_arithmetic():
    temp_dir, client, server = _env()
    try:
        stack_id, _pics = _make_stack(server, [{}, {}])
        body = client.post(PREVIEW_URL, json={"stack_ids": [stack_id, 999999]}).json()
        assert body["stacks_selected"] == 1
        assert body["stacks_eligible"] == 1
        assert body["unknown_stack_ids"] == [999999]
    finally:
        _teardown(temp_dir, server)


# ── what the collapse announces ──────────────────────────────────────────────


def _capture_events(server) -> list[tuple]:
    """Record every ``vault.notify`` call, in order."""
    emitted: list[tuple] = []
    original = server.vault.notify

    def record(event_type, data=None):
        emitted.append((event_type, data))
        return original(event_type, data)

    server.vault.notify = record
    return emitted


def _stack_facet_events(emitted) -> list[dict]:
    return [
        data
        for _event, data in emitted
        if isinstance(data, dict) and data.get("fields") == ["stack_count"]
    ]


def test_the_covers_stack_change_is_announced_even_when_the_union_added_nothing():
    """The cover's STACK always changes; its metadata only sometimes does.

    The announcement used to be gated on ``tags_added or scores_lifted``, which
    tests the wrong property: a collapse whose union found nothing new said
    nothing at all about the cover, so every view kept drawing it as a stack of
    three around a picture that was now on its own (the owner's report). The two
    halves stay separate ``change_kind``s, the copies ``removed`` and the covers
    ``updated``, because merging them would tell the grid a scrapheaped picture
    was merely updated and leave a 404-clickable card behind.
    """
    temp_dir, client, server = _env()
    try:
        # Identical tags and scores on every member: the union has nothing to do.
        stack_id, pics = _make_stack(
            server,
            [
                {"tags": ["a"], "score": 4},
                {"tags": ["a"], "score": 4},
                {"tags": ["a"], "score": 4},
            ],
        )
        emitted = _capture_events(server)

        body = client.post(COLLAPSE_URL, json={"stack_ids": [stack_id]}).json()
        assert body["tags_added"] == 0 and body["scores_lifted"] == 0
        assert body["pictures_moved"] == 2

        removed = [
            data
            for _event, data in emitted
            if isinstance(data, dict) and data.get("change_kind") == "removed"
        ]
        assert removed, "the copies must still be announced as removed"
        assert removed[0]["picture_ids"] == sorted(pics[1:])
        # No `fields` on the removal: a vanished card is not a field change.
        assert not removed[0].get("fields")

        stack_events = _stack_facet_events(emitted)
        assert len(stack_events) == 1, emitted
        assert stack_events[0]["picture_ids"] == [pics[0]]
        assert stack_events[0]["change_kind"] == "updated"
        # And the two are separate events, never one merged announcement.
        assert stack_events[0] is not removed[0]
    finally:
        _teardown(temp_dir, server)


def test_a_union_that_did_something_keeps_its_own_unnarrowed_announcement():
    """The stack event is additive, not a replacement.

    Declaring ``fields=["stack_count"]`` on a cover that also gained a tag and a
    score would tell every client the change cannot affect its sort, which is
    false under a score sort. So the union keeps its own ``fields``-less event
    and the stack change rides beside it.
    """
    temp_dir, client, server = _env()
    try:
        stack_id, pics = _make_stack(
            server,
            [{"tags": ["a"], "score": 1}, {"tags": ["b"], "score": 5}],
        )
        emitted = _capture_events(server)

        body = client.post(COLLAPSE_URL, json={"stack_ids": [stack_id]}).json()
        assert body["tags_added"] > 0 and body["scores_lifted"] > 0

        cover_events = [
            data
            for _event, data in emitted
            if isinstance(data, dict)
            and data.get("picture_ids") == [pics[0]]
            and data.get("change_kind") == "updated"
        ]
        assert len(cover_events) == 2, cover_events
        assert [ev.get("fields") for ev in cover_events].count(None) == 1
        assert [ev.get("fields") for ev in cover_events].count(["stack_count"]) == 1
    finally:
        _teardown(temp_dir, server)


def test_a_collapse_that_moved_nothing_announces_nothing_about_the_covers():
    """No move, no stack change: a skipped stack's cover still leads its stack."""
    temp_dir, client, server = _env()
    try:
        # A single-member stack is skipped outright, so nothing moves.
        stack_id, pics = _make_stack(server, [{}])
        emitted = _capture_events(server)

        body = client.post(COLLAPSE_URL, json={"stack_ids": [stack_id]}).json()
        assert body["pictures_moved"] == 0
        assert body["stacks_collapsed"] == 0
        assert not _stack_facet_events(emitted)
        assert pics
    finally:
        _teardown(temp_dir, server)


def test_undo_announces_the_covers_stack_change_back():
    """The reverse direction, which is the whole reason the undo window exists.

    After the undo the cover leads a stack of three again, and the only thing
    that says so is the ``stack_count`` announcement over the pictures the undo
    did NOT move: the copies come back as ``restored``, the cover was never
    scrapheaped and so appears in neither lifecycle list.
    """
    temp_dir, client, server = _env()
    try:
        stack_id, pics = _make_stack(server, [{}, {}, {}])
        body = client.post(COLLAPSE_URL, json={"stack_ids": [stack_id]}).json()
        batch_id = body["batch_id"]
        assert batch_id

        emitted = _capture_events(server)
        assert (
            client.post(f"{API}/operations/batches/{batch_id}/undo").status_code == 200
        )

        restored = [
            data
            for _event, data in emitted
            if isinstance(data, dict) and data.get("change_kind") == "restored"
        ]
        assert restored, "the copies must come back as restored"
        assert sorted(restored[0]["picture_ids"]) == sorted(pics[1:])

        stack_events = _stack_facet_events(emitted)
        assert len(stack_events) == 1, emitted
        assert stack_events[0]["picture_ids"] == [pics[0]]
        assert stack_events[0]["change_kind"] == "updated"

        # And back again. A fix that only works one way is half a fix, and redo
        # re-collapses the stack the undo just put back.
        emitted.clear()
        assert client.post(f"{API}/operations/redo").status_code == 200
        assert _stack_facet_events(emitted) == [
            {
                "picture_ids": [pics[0]],
                "origin_client_id": None,
                "change_kind": "updated",
                "source": "ui",
                "fields": ["stack_count"],
            }
        ]
    finally:
        _teardown(temp_dir, server)


# ── authorization, both directions ───────────────────────────────────────────


def test_scoped_read_token_is_denied_on_both_keep_cover_only_routes():
    """Negative direction, through both entry points.

    The ``?token=`` query parameter is a separate reachability path from the
    ``Authorization`` header, and a gate covering only one would be a hole
    rather than a policy.
    """
    temp_dir, client, server = _env()
    try:
        stack_id, pics = _make_stack(server, [{}, {}])
        token = _scoped_token(client, server, pics[0])
        scoped = TestClient(server.api)
        headers = {"Authorization": f"Bearer {token}"}
        payload = {"stack_ids": [stack_id]}

        for url in (PREVIEW_URL, COLLAPSE_URL):
            assert scoped.post(url, json=payload, headers=headers).status_code == 403
            assert (
                scoped.post(url, json=payload, params={"token": token}).status_code
                == 403
            )

        # Fail-closed, not fail-late: the refused call changed nothing.
        assert not any(_picture_rows(server, pics)[pid][0] for pid in pics)
    finally:
        _teardown(temp_dir, server)


def test_the_owner_reaches_both_keep_cover_only_routes():
    """Positive direction: over-blocking would be its own regression."""
    temp_dir, client, server = _env()
    try:
        stack_id, pics = _make_stack(server, [{}, {}])
        assert (
            client.post(PREVIEW_URL, json={"stack_ids": [stack_id]}).status_code == 200
        )
        response = client.post(COLLAPSE_URL, json={"stack_ids": [stack_id]})
        assert response.status_code == 200
        assert response.json()["pictures_moved"] == 1
        assert _picture_rows(server, pics)[pics[1]][0] is True
    finally:
        _teardown(temp_dir, server)


# ── the batch id is validated, not stored verbatim ───────────────────────────


def test_a_forged_batch_id_cannot_graft_two_collapses_into_one_undo():
    """`batch_id` is the undo handle, so an unvalidated one is an undo forgery.

    Taken verbatim, a client could send `srv-…` so its rows read as a server
    batch, or reuse one id across separate gestures so a single `Ctrl+Z`
    reverses more than the user did. The dedup routes validated it; this one
    did not, which is exactly the kind of second copy the shared helper in
    `utils/request_origin.py` exists to prevent.
    """
    temp_dir, client, server = _env()
    try:
        first, _first_pics = _make_stack(server, [{}, {}])
        second, _second_pics = _make_stack(server, [{}, {}])

        for forged in (
            "srv-deadbeefdeadbeefdeadbeefdeadbeef",  # the server's own namespace
            "cli-" + "a" * 200,  # over the length cap
            "x" * 1_000_000,  # unbounded
            "cli-bad id",  # outside the charset
            "cli-ab",  # under the minimum length
        ):
            response = client.post(
                COLLAPSE_URL, json={"stack_ids": [first], "batch_id": forged}
            )
            assert response.status_code == 400, (forged[:32], response.text)
            assert "batch_id" in response.json()["detail"]

        # Nothing was collapsed by any of the refused calls.
        assert (
            client.post(PREVIEW_URL, json={"stack_ids": [first]}).json()[
                "stacks_eligible"
            ]
            == 1
        )

        # A well-formed client id is still honoured, and deliberately sharing it
        # across two calls is the legitimate use it exists for.
        shared = "cli-one-gesture-01"
        assert (
            client.post(
                COLLAPSE_URL, json={"stack_ids": [first], "batch_id": shared}
            ).json()["batch_id"]
            == shared
        )
        assert (
            client.post(
                COLLAPSE_URL, json={"stack_ids": [second], "batch_id": shared}
            ).json()["batch_id"]
            == shared
        )
    finally:
        _teardown(temp_dir, server)


# ── the selection cap bounds the REQUEST, not the de-duplicated set ──────────


def test_the_selection_cap_bounds_the_request_not_the_deduplicated_set():
    """Two ways the cap used to be no cap at all, and the shape that still works.

    It was applied to the de-duplicated set, so a body of a million repeats of
    one id passed; and it was applied per list, so `stack_ids` plus
    `picture_ids` legitimately carried twice it in one request.
    """
    temp_dir, client, server = _env()
    try:
        stack_id, pics = _make_stack(server, [{}, {}])
        cap = keep_cover_only_service.MAX_SELECTION_IDS

        # (1) Repeats count. This de-duplicates to one id and used to return 200.
        response = client.post(COLLAPSE_URL, json={"picture_ids": [pics[0]] * 200_000})
        assert response.status_code == 400, response.status_code
        assert "picture_ids" in response.json()["detail"]

        # (2) The two lists share one budget.
        response = client.post(
            COLLAPSE_URL,
            json={
                "stack_ids": list(range(1, cap + 1)),
                "picture_ids": list(range(cap + 1, 2 * cap + 1)),
            },
        )
        assert response.status_code == 400, response.status_code
        assert "together" in response.json()["detail"]

        # The preview shares the check, so the dialog cannot be used to sneak past.
        assert (
            client.post(PREVIEW_URL, json={"picture_ids": [pics[0]] * 200_000})
        ).status_code == 400

        # A selection right at the budget still works: over-blocking is its own
        # regression, and the natural gesture here is a whole-library sweep.
        response = client.post(
            COLLAPSE_URL,
            json={
                "stack_ids": [stack_id],
                "picture_ids": list(range(10_000, 10_000 + cap - 1)),
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["stacks_collapsed"] == 1
    finally:
        _teardown(temp_dir, server)
