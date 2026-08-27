"""The "About your library" findings (v1.11 Phase 6).

Read-only findings over an already-organised library. What is worth asserting
about a screen made of prose is not the prose - it is the four things the
screen's honesty rests on:

1. **Every check answers in both directions.** A tidy library still gets five
   rows, all ``clear``. A screen that only speaks when it disapproves is a nag,
   and a check that silently disappears when it passes cannot be trusted when
   it fires.
2. **The thresholds are the thresholds.** A pile is a pile at
   ``PILE_MIN_PICTURES``; a handful of loose files is not. Two folders are
   copies of each other above ``OVERLAP_MIN_SHARE``; a few shared files are not.
3. **The action opens onto the pictures the finding counted.** The folder the
   evidence names is the folder the button carries.
4. **The counts are over live pictures.** A scrapheaped picture is not a
   finding.

Plus the mandatory both-directions authz pair for the new route: an owner reads
it, a share token does not.
"""

import gc
import json
import os
import tempfile

import pytest
from sqlalchemy import func
from sqlmodel import delete, select, update
from starlette.testclient import TestClient

from pixlstash.database import DBPriority
from pixlstash.db_models import (
    Character,
    Face,
    Picture,
    PictureProjectMember,
    PictureSet,
    PictureSetMember,
    PictureStack,
    Project,
    Tag,
)
from pixlstash.server import Server
from pixlstash.services import dedup_tier_service as tiers
from pixlstash.services import library_insights_service as insights
from pixlstash.services.dedup_tier_service import DedupScope, ScopeType
from tests.authz_guard import no_spa_fallback  # noqa: F401

API = "/api/v1"

# A stand-in library root. Never a real path: the folder names are what the
# findings read, and the leading directories only have to be absolute.
LIB = "/home/me/library"


@pytest.fixture(scope="module")
def server():
    """One server for the module. Standing one up costs ~1.35 s; the assertions
    here cost microseconds, so a per-test server would be the whole runtime.

    Background workers are off: every finder in this repo writes exactly the
    columns these tests hand-write (tags, faces, descriptions), and a sweep
    landing mid-test would overwrite the library being asserted on.
    """
    temp_dir = tempfile.TemporaryDirectory()
    config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(config_path, "w") as fh:
        fh.write(json.dumps({"port": 8000, "disable_background_workers": True}))
    # Restored in the `finally`: a module-scoped fixture outlives its own file,
    # and `tests/test_florence.py` SKIPS on this value, so leaking it decides
    # whether an unrelated suite runs at all depending on shard order.
    previous_force_cpu = Server.DEFAULT_FORCE_CPU
    Server.DEFAULT_FORCE_CPU = True
    srv = Server(config_path)
    try:
        yield srv
    finally:
        Server.DEFAULT_FORCE_CPU = previous_force_cpu
        srv.close()
        temp_dir.cleanup()
        gc.collect()


@pytest.fixture(scope="module")
def client(server):
    """An owner session on the module server, for the tests that go over HTTP.

    Module-scoped with the server: the login is not what any test here is
    about, and `clean_library` wipes pictures rather than users, so one session
    survives every wipe.
    """
    api = TestClient(server.api, raise_server_exceptions=True)
    response = api.post(
        f"{API}/login", json={"username": "owner", "password": "example-owner-password"}
    )
    assert response.status_code == 200, response.text
    return api


def _run(srv, fn, *args):
    return srv.vault.db.run_task(fn, *args, priority=DBPriority.IMMEDIATE)


@pytest.fixture(autouse=True)
def clean_library(server):
    """Empty the library before every test.

    Children first, then the pictures they hang off, so no foreign key is left
    dangling and no PRAGMA is needed. Runs before rather than after, so a test
    that fails leaves its library on the table to look at.
    """

    def wipe(session):
        for model in (
            Tag,
            Face,
            PictureSetMember,
            PictureProjectMember,
        ):
            session.exec(delete(model))
        session.exec(delete(Picture))
        session.exec(delete(PictureStack))
        session.exec(delete(PictureSet))
        session.exec(delete(Character))
        session.commit()

    _run(server, wipe)


def _seed(srv, specs):
    """Insert one picture per spec and return the ids in order.

    Keys: ``folder`` (joined under :data:`LIB`; omit for a vault-managed flat
    name), ``name``, ``pixel_sha``, ``size_bytes``, ``description``, ``tags``,
    ``deleted``, ``face`` (``True`` for an unnamed face, a string for a named
    one), ``face_scanned_empty`` (the no-face sentinel row), ``in_set``,
    ``in_project``, ``stack`` (a key; all specs sharing it land in one stack)
    and ``stack_position``.
    """

    def insert(session):
        picture_ids = []
        stacks: dict = {}

        def named_row(model, name: str) -> int:
            """One row per name, the way ``stacks`` gives one stack per key.

            ``Project.name`` is unique, so two specs naming the same project
            used to raise an IntegrityError from inside the seed helper. Set and
            character names are not constrained, but two rows called
            ``selects`` is not what a spec saying ``in_set="selects"`` asks for
            either. The lookup hits the table rather than a local dict so a
            second ``_seed`` call in the same test reuses the first's rows.
            """
            row = session.exec(select(model).where(model.name == name)).first()
            if row is None:
                row = model(name=name)
                session.add(row)
                session.flush()
            return int(row.id)

        for index, spec in enumerate(specs):
            name = spec.get("name", f"pic_{index}.png")
            folder = spec.get("folder")
            pic = Picture(
                file_path=os.path.join(LIB, folder, name) if folder else name,
                format="png",
                width=4000,
                height=3000,
                size_bytes=spec.get("size_bytes", 1000),
                pixel_sha=spec.get("pixel_sha", f"sha_{index}"),
                description=spec.get("description"),
                deleted=bool(spec.get("deleted", False)),
            )
            session.add(pic)
            session.flush()
            pic_id = int(pic.id)
            for tag in spec.get("tags", []):
                session.add(Tag(picture_id=pic_id, tag=tag))
            face = spec.get("face")
            if face:
                character_id = None
                if isinstance(face, str):
                    character_id = named_row(Character, face)
                session.add(
                    Face(picture_id=pic_id, face_index=0, character_id=character_id)
                )
            if spec.get("face_scanned_empty"):
                # What the extractor ACTUALLY writes for a picture it found no
                # face in: a sentinel row at index -1 with a NULL character.
                # Most rows in a scanned library look like this.
                session.add(Face(picture_id=pic_id, face_index=-1, character_id=None))
            if spec.get("stack") is not None:
                stack_ids = spec["stack"]
                if stack_ids not in stacks:
                    stack = PictureStack()
                    session.add(stack)
                    session.flush()
                    stacks[stack_ids] = int(stack.id)
                pic.stack_id = stacks[stack_ids]
                pic.stack_position = spec.get("stack_position", index)
                session.add(pic)
            if spec.get("in_set"):
                session.add(
                    PictureSetMember(
                        set_id=named_row(PictureSet, spec["in_set"]),
                        picture_id=pic_id,
                    )
                )
            if spec.get("in_project"):
                session.add(
                    PictureProjectMember(
                        project_id=named_row(Project, spec["in_project"]),
                        picture_id=pic_id,
                    )
                )
            picture_ids.append(pic_id)
        session.commit()
        return picture_ids

    return _run(srv, insert)


def _findings(srv) -> dict:
    """``{finding_id: finding}`` for the current library."""
    return {f["id"]: f for f in insights.build_insights(srv.vault)["findings"]}


def _tidy(folder="Mira", count=30, **extra):
    """A picture that trips no check: in a folder, in a set, captioned, tagged,
    and its face is somebody."""
    spec = {
        "folder": folder,
        "description": "a picture of something",
        "tags": ["outdoor"],
        "face": "Mira",
        "in_set": "selects",
    }
    spec.update(extra)
    return [
        dict(spec, name=f"tidy_{i}.png", pixel_sha=f"tidy_{i}") for i in range(count)
    ]


# ── 1. both directions ───────────────────────────────────────────────────────


def test_a_tidy_library_still_gets_every_row_and_all_of_them_are_clear(server):
    """The finding that says there is nothing to do is a first-class finding.

    This is the assertion the screen's tone depends on: five checks ran, five
    rows came back, and not one of them is a complaint.
    """
    _seed(server, _tidy())

    found = _findings(server)
    assert set(found) == {
        "unsorted_pile",
        "overlapping_folders",
        "uncaptioned",
        "unnamed_faces",
        "untagged",
    }
    assert [f["state"] for f in found.values()] == ["clear"] * 5
    assert all(f["action"] is None for f in found.values())
    # A clear row still carries the number that made it clear - "nothing to fix"
    # with no evidence behind it is indistinguishable from a check that never ran.
    assert all(f["evidence"] for f in found.values())


def test_findings_are_ordered_with_what_to_look_at_first(server):
    _seed(server, _tidy() + [{"folder": "dump"} for _ in range(40)])

    states = [f["state"] for f in insights.build_insights(server.vault)["findings"]]
    assert states == sorted(states, key=lambda s: 0 if s == "todo" else 1)
    assert states[0] == "todo"


# ── 2. the unsorted pile ─────────────────────────────────────────────────────


def test_a_pile_names_its_folder_and_opens_the_unassigned_view_on_it(server):
    _seed(
        server,
        _tidy()
        + [
            {"folder": "_unsorted", "description": "x", "tags": ["t"]}
            for _ in range(insights.PILE_MIN_PICTURES)
        ],
    )

    pile = _findings(server)["unsorted_pile"]
    assert pile["state"] == "todo"
    assert "_unsorted" in pile["title"]
    assert str(insights.PILE_MIN_PICTURES) in pile["title"]
    # The button opens onto the folder the evidence just named.
    assert pile["action"]["kind"] == "unassigned_in_folder"
    assert pile["action"]["path"] == os.path.join(LIB, "_unsorted")
    assert pile["action"]["folder_label"] == "_unsorted"


def test_a_handful_of_loose_files_is_not_a_pile(server):
    """One below the bar. Every library has a few strays and calling that a
    finding is exactly the nagging this screen is designed not to do."""
    _seed(
        server,
        _tidy()
        + [
            {"folder": "_unsorted", "description": "x", "tags": ["t"]}
            for _ in range(insights.PILE_MIN_PICTURES - 1)
        ],
    )

    pile = _findings(server)["unsorted_pile"]
    assert pile["state"] == "clear"
    assert pile["action"] is None


# ── 3. two folders that are copies of each other ─────────────────────────────


def _solo(folder, count, marker):
    """`count` pictures that exist only in `folder`."""
    return [
        {
            "folder": folder,
            "name": f"{marker}_{i}.png",
            "pixel_sha": f"{marker}_{i}",
            "description": "x",
            "tags": ["t"],
            "in_set": "s",
        }
        for i in range(count)
    ]


def _copies(left, right, shared, extra_left=0, extra_right=0):
    """``shared`` identical files in both folders, plus solo files in each.

    Both sides can be padded, because the share is measured against the
    SMALLER folder: padding one side only makes the other side the smaller one
    and the share goes back to 100%.
    """
    specs = []
    for i in range(shared):
        for folder in (left, right):
            specs.append(
                {
                    "folder": folder,
                    "name": f"shot_{i}.png",
                    "pixel_sha": f"shared_{i}",
                    "size_bytes": 4242,
                    "description": "x",
                    "tags": ["t"],
                    "in_set": "s",
                }
            )
    specs += _solo(left, extra_left, "onlyleft")
    specs += _solo(right, extra_right, "onlyright")
    return specs


def test_two_folders_that_are_mostly_the_same_pictures_are_found(server):
    _seed(server, _copies("selects", "final", shared=insights.OVERLAP_MIN_PICTURES))

    overlap = _findings(server)["overlapping_folders"]
    assert overlap["state"] == "todo"
    assert "selects" in overlap["title"] and "final" in overlap["title"]
    # Total containment does not read as "the same pictures": with the share
    # measured against the smaller folder, 100% means the small one is INSIDE
    # the big one, and calling them the same is a claim about the big one that
    # is not true.
    assert overlap["title"].startswith("Every picture in ")
    # The exact path, not "one of these two": an `in (a, b)` assertion here
    # accepted either branch and could not fail. The scope is the pair's common
    # ancestor, for the reason in `_duplicate_scope`.
    assert overlap["action"]["kind"] == "duplicates_in_folder"
    assert overlap["action"]["path"] == LIB


def test_the_overlap_action_opens_a_queue_that_actually_holds_the_duplicates(server):
    """The finding's scope has to be one tier 1 can group inside.

    `find_exact_groups_in_session` applies the scope predicate INSIDE
    `HAVING count(*) > 1`, so a queue scoped to one of the two folders sees one
    copy of each shared file and finds nothing - an empty queue reached from a
    finding that just said the two folders are 100% the same pictures. The
    common ancestor holds both copies, and the queue's folder scope is a
    sub-tree prefix match, so it does.
    """
    _seed(server, _copies("selects", "final", shared=insights.OVERLAP_MIN_PICTURES))

    overlap = _findings(server)["overlapping_folders"]
    assert overlap["action"]["kind"] == "duplicates_in_folder"
    scope_path = overlap["action"]["path"]
    # Neither of the two folders - their parent.
    assert scope_path == LIB

    groups = _run(
        server,
        tiers.find_exact_groups_in_session,
        DedupScope(scope_type=ScopeType.FOLDER, scope_id=scope_path),
    )
    assert len(groups) == insights.OVERLAP_MIN_PICTURES, (
        "the queue this finding opens must hold the duplicates it described"
    )


def test_two_trees_with_no_usable_ancestor_open_the_whole_queue(server):
    """A common ancestor of `/` is a whole-vault scan wearing a folder's name.
    The finding says "duplicate queue" and opens it unscoped instead."""
    _seed(server, _copies("selects", "final", shared=insights.OVERLAP_MIN_PICTURES))
    # Push one folder onto a different root, so commonpath resolves to "/".
    _run(
        server,
        lambda session: (
            session.exec(
                update(Picture)
                .where(Picture.file_path.like(f"{LIB}/final/%"))
                .values(
                    file_path=func.replace(Picture.file_path, f"{LIB}/final", "/mnt/b")
                )
            ),
            session.commit(),
        ),
    )

    overlap = _findings(server)["overlapping_folders"]
    assert overlap["state"] == "todo"
    assert overlap["action"]["kind"] == "duplicates"
    assert "path" not in overlap["action"]


def test_a_few_shared_files_do_not_make_two_folders_copies(server):
    """Below the count bar. Two folders sharing a handful of files is an
    ordinary library, not something the owner did once."""
    _seed(
        server,
        _copies(
            "a",
            "b",
            shared=insights.OVERLAP_MIN_PICTURES - 1,
            extra_left=50,
            extra_right=50,
        ),
    )

    assert _findings(server)["overlapping_folders"]["state"] == "clear"


def test_folders_that_merely_share_a_lot_of_files_are_not_copies(server):
    """The SHARE bar, which the count bar does not exercise.

    Above `OVERLAP_MIN_PICTURES` shared files but well under
    `OVERLAP_MIN_SHARE` of the smaller folder: an ordinary library where two
    shoots happen to hold the same twelve exports, not a folder somebody
    duplicated. Deleting the share gate left every other test in this file
    green.
    """
    shared = insights.OVERLAP_MIN_PICTURES + 2
    # Both sides padded, so neither folder is mostly the shared files. Sized
    # from the constant, so moving the bar does not silently make this vacuous.
    extra = int(shared / insights.OVERLAP_MIN_SHARE) + 4
    _seed(
        server,
        _copies("a", "b", shared=shared, extra_left=extra, extra_right=extra),
    )

    assert shared / (shared + extra) < insights.OVERLAP_MIN_SHARE, (
        "fixture must sit below the bar"
    )
    assert _findings(server)["overlapping_folders"]["state"] == "clear"


def test_the_same_digest_at_a_different_size_is_not_a_copy(server):
    """Tier 1's own rule: ``pixel_sha`` is a sampled digest above 128 KiB, so
    the size is part of the identity. Grouping on the digest alone would report
    folders as copies of each other on a hash collision."""
    specs = _copies("a", "b", shared=insights.OVERLAP_MIN_PICTURES)
    for spec in specs:
        if spec["folder"] == "b":
            spec["size_bytes"] = 9999
    _seed(server, specs)

    assert _findings(server)["overlapping_folders"]["state"] == "clear"


# ── 4. captions, tags and faces ──────────────────────────────────────────────


def test_uncaptioned_and_untagged_each_offer_the_pane_that_switches_one_on(server):
    _seed(
        server, [{"folder": "Mira", "in_set": "s", "face": "Mira"} for _ in range(30)]
    )

    found = _findings(server)
    for key in ("uncaptioned", "untagged"):
        assert found[key]["state"] == "todo", key
        assert found[key]["action"]["kind"] == "settings", key
        assert found[key]["action"]["tab"] == "behaviour", key


def test_a_face_nobody_has_named_is_a_finding_and_a_named_one_is_not(server, client):
    """And the number is the number the destination shows.

    The finding is the INTERSECTION of "holds a face" and unassigned, because
    that is what `/character/UNASSIGNED?face=with_face` is. The first version
    counted every picture holding an unnamed face - including ones in a set,
    which that view excludes - so its own fixture (`_tidy` puts everything in a
    set) made the count unreachable from the button.
    """
    _seed(
        server,
        # Tidy pictures: named face, in a set. Neither counted nor shown.
        _tidy(count=10)
        # Unnamed face, in no set: counted AND shown.
        + [{"folder": "Nordvik", "face": True} for _ in range(5)]
        # Unnamed face but IN A SET: the destination cannot show it, so the
        # finding must not count it.
        + [{"folder": "Nordvik", "face": True, "in_set": "keepers"}]
        # No face row at all, in no set: in neither. `?face=with_face` excludes
        # them, so counting them would be the same defect one shape over.
        + [{"folder": "Nordvik"} for _ in range(3)],
    )

    faces = _findings(server)["unnamed_faces"]
    assert faces["state"] == "todo"
    assert "5 pictures" in faces["title"]
    assert faces["action"]["kind"] == "unassigned_with_face"

    shown = client.get(
        f"{API}/pictures",
        params={
            "character_id": "UNASSIGNED",
            "face_filter": "with_face",
            # What the app actually sends. Without it this exercises the one
            # branch the UI never uses - which is how the stack-collapse defect
            # below survived a green suite.
            "fields": "grid",
        },
    )
    assert shown.status_code == 200, shown.text
    assert len(shown.json()) == 5

    _run(server, lambda s: (s.exec(delete(Face)), s.commit()))
    assert _findings(server)["unnamed_faces"]["state"] == "clear"


def test_a_scanned_picture_with_no_face_in_it_is_not_an_unnamed_face(server, client):
    """The sentinel row, which is what most of a scanned library carries.

    `FaceExtractionTask` writes `face_index=-1, character_id=None` for every
    picture it found no face in. Read as "an unnamed face" that made the
    finding fire on nearly the whole library while its button opened an empty
    grid - and the fixture could not see it, because it only ever wrote
    `face_index=0`.
    """
    _seed(
        server,
        # Scanned, nothing in them. The bulk of a real library.
        [{"folder": "Landscapes", "face_scanned_empty": True} for _ in range(40)]
        # One picture that really does hold a face nobody has named.
        + [{"folder": "Portraits", "face": True}],
    )

    faces = _findings(server)["unnamed_faces"]
    assert faces["state"] == "todo"
    assert faces["title"].startswith("1 pictures") or "1 " in faces["title"]

    # And the destination agrees: `with_face` compiles the same `!= -1` test.
    shown = client.get(
        f"{API}/pictures",
        params={
            "character_id": "UNASSIGNED",
            "face_filter": "with_face",
            "fields": "grid",
        },
    )
    assert shown.status_code == 200, shown.text
    assert len(shown.json()) == 1


def test_a_library_scanned_and_holding_no_faces_is_clear(server):
    _seed(
        server,
        [{"folder": "Landscapes", "face_scanned_empty": True} for _ in range(40)],
    )
    assert _findings(server)["unnamed_faces"]["state"] == "clear"


def test_a_pile_counts_the_rows_its_button_will_draw_not_the_pictures(server, client):
    """A stack is ONE row.

    Every grid request the app makes carries `fields=grid`, which the listing
    route maps to `stack_leaders_only`. Counting pictures put "30 pictures" on
    a finding whose button opened fifteen rows.
    """
    _seed(
        server,
        # 30 pictures, in 15 stacks of two.
        [
            {"folder": "_unsorted", "stack": i // 2, "stack_position": i % 2}
            for i in range(30)
        ]
        # …plus 10 loose ones, so the pile clears PILE_MIN_PICTURES in ROWS.
        + [{"folder": "_unsorted"} for _ in range(10)],
    )

    pile = _findings(server)["unsorted_pile"]
    assert pile["state"] == "todo"
    assert pile["title"].startswith("25 pictures"), pile["title"]

    grid = client.get(
        f"{API}/pictures",
        params={
            "character_id": "UNASSIGNED",
            "file_path_prefix": pile["action"]["path"],
            # What the app actually sends. Querying without it exercises the one
            # branch the UI never uses.
            "fields": "grid",
        },
    )
    assert grid.status_code == 200, grid.text
    assert len(grid.json()) == 25


def test_a_stack_whose_leader_sits_in_another_folder_still_shows(server, client):
    """The folder-scoped grid must not drop a stack whose global leader is
    elsewhere.

    `find_unassigned`'s leaders-only fast path keys on the GLOBAL
    `stack_position == 0`. With a folder filter that member can be outside the
    scope, and the whole stack fell out of a grid whose own pictures were right
    there - the same defect the project-scope branch beside it was written for.
    """
    _seed(
        server,
        # Position 0 lives in `other`; position 1 is the one in `_unsorted`.
        [{"folder": "other", "stack": "s", "stack_position": 0}]
        + [{"folder": "_unsorted", "stack": "s", "stack_position": 1}],
    )

    grid = client.get(
        f"{API}/pictures",
        params={
            "character_id": "UNASSIGNED",
            "file_path_prefix": f"{LIB}/_unsorted",
            "fields": "grid",
        },
    )
    assert grid.status_code == 200, grid.text
    assert len(grid.json()) == 1, "the stack must be represented by its in-scope member"
    assert f"{LIB}/_unsorted" in grid.json()[0]["file_path"]


def test_a_face_on_a_stack_member_still_reaches_the_grid(server, client):
    """A face facet narrows too, so it can hide the stack's global leader.

    The cover of a stack is usually the tidy shot and the face is on a sibling.
    With `face_filter` outside the leader-scope set the whole stack fell out of
    `?face=with_face`, so the finding said 1 and its screen showed nothing -
    the same defect as the folder case, one facet over.
    """
    _seed(
        server,
        [
            # position 0: the cover, no face in it.
            {"folder": "Portraits", "stack": "s", "stack_position": 0},
            # position 1: the one holding an unnamed face.
            {"folder": "Portraits", "stack": "s", "stack_position": 1, "face": True},
        ],
    )

    faces = _findings(server)["unnamed_faces"]
    assert faces["state"] == "todo"
    assert faces["title"].startswith("1 pictures"), faces["title"]

    grid = client.get(
        f"{API}/pictures",
        params={
            "character_id": "UNASSIGNED",
            "face_filter": "with_face",
            "fields": "grid",
        },
    )
    assert grid.status_code == 200, grid.text
    assert len(grid.json()) == 1, "the finding's own number must reach the screen"


def test_the_ordinary_grid_keeps_a_folder_stack_whose_cover_is_elsewhere(
    server, client
):
    """`?path=` rides on every grid route, including `/` - which goes through
    `Picture.find`, not `find_unassigned`. Both take the same rule now."""
    _seed(
        server,
        [
            {"folder": "other", "stack": "s", "stack_position": 0, "in_set": "keep"},
            {
                "folder": "_unsorted",
                "stack": "s",
                "stack_position": 1,
                "in_set": "keep",
            },
        ],
    )

    grid = client.get(
        f"{API}/pictures",
        params={
            "character_id": "ALL",
            "file_path_prefix": f"{LIB}/_unsorted",
            "fields": "grid",
        },
    )
    assert grid.status_code == 200, grid.text
    assert len(grid.json()) == 1
    assert f"{LIB}/_unsorted" in grid.json()[0]["file_path"]


def test_a_prefix_sibling_folder_does_not_walk_through_the_ceiling(server):
    """The queue scopes on `LIKE '<prefix>%'` with no separator, so `/lib` also
    catches `/lib-archive`. Measuring a separator-bounded sub-tree instead let
    an unrelated folder's pictures into a scope the ceiling had cleared."""
    _seed(
        server,
        _copies("lib/selects", "lib/final", shared=insights.OVERLAP_MIN_PICTURES)
        + _solo("lib-archive", insights.OVERLAP_MIN_PICTURES * 20, "arch"),
    )

    overlap = _findings(server)["overlapping_folders"]
    assert overlap["state"] == "todo"
    assert overlap["action"]["kind"] == "duplicates"


def test_a_relative_path_never_becomes_a_queue_scope(server):
    """Stored paths are absolute, so a relative prefix matches nothing and the
    queue would be silently empty."""
    assert insights._duplicate_scope(None, "a/b", "a/c") is None
    assert insights._duplicate_scope(None, "/a/b", "a/c") is None


def test_an_ancestor_that_narrows_nothing_opens_the_whole_queue(server):
    """Two unrelated trees under one home directory are SIBLINGS, exactly like
    two folders inside a library, so no rule about path shape separates them.
    What separates them is how much the ancestor holds."""
    _seed(
        server,
        # The two folders that overlap: small.
        _copies("Pictures", "Downloads", shared=insights.OVERLAP_MIN_PICTURES)
        # …and a great deal else under the same parent, which is what makes the
        # ancestor the whole library rather than the folder these two share.
        + _solo("Archive", insights.OVERLAP_MIN_PICTURES * 20, "arch"),
    )

    overlap = _findings(server)["overlapping_folders"]
    assert overlap["state"] == "todo"
    assert overlap["action"]["kind"] == "duplicates"
    assert "path" not in overlap["action"]


# ── 5. what is not counted ───────────────────────────────────────────────────


def test_scrapheaped_pictures_are_not_a_finding(server):
    # (`_tidy` names every face, so the unnamed-faces check is clear here for
    # the right reason and the deleted rows are what is being tested.)
    """A soft-deleted picture is in the bin, not in the library. Counting one
    would put a number in front of the owner that no tool can act on."""
    _seed(
        server,
        _tidy()
        + [
            {"folder": "gone", "deleted": True, "face": True}
            for _ in range(insights.PILE_MIN_PICTURES * 2)
        ],
    )

    found = _findings(server)
    assert found["unsorted_pile"]["state"] == "clear"
    assert found["unnamed_faces"]["state"] == "clear"
    assert found["uncaptioned"]["state"] == "clear"
    assert insights.build_insights(server.vault)["total_pictures"] == 30


def test_a_vault_only_library_has_no_folder_names_to_read(server):
    """Pictures copied into the vault get a flat ``<uuid>.png``: storage, not
    organisation. Reporting "none of your 0 folders" would be arithmetic
    nobody can act on."""
    _seed(
        server,
        [
            {"description": "x", "tags": ["t"], "in_set": "s", "face": "Mira"}
            for _ in range(30)
        ],
    )

    payload = insights.build_insights(server.vault)
    assert payload["folders"] == 0
    assert payload["folder_pictures"] == 0
    found = {f["id"]: f for f in payload["findings"]}
    for key in ("unsorted_pile", "overlapping_folders"):
        assert found[key]["state"] == "clear", key
        assert "reference folder" in found[key]["evidence"], key


def test_an_empty_library_answers_without_dividing_by_zero(server):
    payload = insights.build_insights(server.vault)
    assert payload["total_pictures"] == 0
    assert len(payload["findings"]) == 5
    assert all(f["state"] == "clear" for f in payload["findings"])
    # And it says the library is empty rather than "all 0 pictures live in the
    # vault", which is true and reads as a bug.
    found = {f["id"]: f for f in payload["findings"]}
    assert "The library is empty" in found["unsorted_pile"]["evidence"]


# ── 6. the finding's button lands on the pictures the finding counted ────────


def test_the_pile_action_opens_a_grid_holding_exactly_the_pile(server, client):
    """The end-to-end check the finding's whole value rests on.

    "Sort them" navigates to `/character/UNASSIGNED?path=<folder>`, which the
    grid sends as `character_id=UNASSIGNED&file_path_prefix=<folder>`. The
    unassigned branch used to drop `file_path_prefix` outright
    (`Picture.find_unassigned` had no such parameter), so the button opened
    every unassigned picture in the library under a header naming one folder -
    green in every other test here, because they all stop at the action object.
    """
    _seed(
        server,
        # The pile the finding will name.
        [{"folder": "_unsorted"} for _ in range(insights.PILE_MIN_PICTURES)]
        # Two pictures in the same folder that belong to a PROJECT and nothing
        # else. The app does not treat that as assignment - the grid shows them
        # - so the finding must count them too. The first version of this
        # service restated "assigned" in Python, added project membership to it,
        # and undercounted the pile by exactly these rows. Two rather than one
        # because `Project.name` is unique: a seed helper that made a fresh
        # project per spec raised an IntegrityError on the second.
        + [{"folder": "_unsorted", "in_project": "Nordvik"} for _ in range(2)]
        # …and an unassigned group somewhere else, which the button must NOT show.
        + [{"folder": "elsewhere"} for _ in range(7)],
    )

    pile = _findings(server)["unsorted_pile"]
    assert pile["state"] == "todo"
    folder = pile["action"]["path"]

    scoped = client.get(
        f"{API}/pictures",
        params={"character_id": "UNASSIGNED", "file_path_prefix": folder},
    )
    assert scoped.status_code == 200, scoped.text
    whole = client.get(f"{API}/pictures", params={"character_id": "UNASSIGNED"})
    assert whole.status_code == 200, whole.text

    # The number in the finding's title is the number the grid shows. Read off
    # the finding rather than restated, so the two cannot drift.
    in_pile = insights.PILE_MIN_PICTURES + 2
    assert f"{in_pile:,}" in pile["title"]
    assert len(scoped.json()) == in_pile
    assert len(whole.json()) == in_pile + 7
    assert all(folder in pic["file_path"] for pic in scoped.json())


# ── 7. the route: owner reads it, a share token does not ─────────────────────

pytestmark = pytest.mark.usefixtures("no_spa_fallback")


def test_insights_is_owner_only_in_both_directions(server, client):
    """A new declared route is an authz change, so both directions are asserted:
    the owner reads it (over-blocking is its own regression) and a share token
    is refused.

    On the MODULE server. An earlier version stood up a second one "because this
    needs a login, which the module server does not have" - which the `client`
    fixture above disproves by logging into exactly that server. A second
    `Server` is ~1.4 s plus a full migration chain, per shard, for nothing.
    """
    minted = client.post(
        f"{API}/users/me/token",
        json={
            "description": "set share",
            "scope": "READ",
            "resource_type": "picture_set",
            "resource_id": 1,
        },
    )
    assert minted.status_code == 200, minted.text
    scoped = minted.json()["token"]

    # POSITIVE: the owner reads the findings.
    reading = client.get(f"{API}/insights")
    assert reading.status_code == 200, reading.text
    assert len(reading.json()["findings"]) == 5

    # NEGATIVE: a resource-scoped share token does not. The numbers ARE the
    # vault-wide aggregate, so a narrowed answer would either leak that
    # out-of-scope pictures exist or state a wrong total.
    #
    # A cookie-less client, because the auth middleware prefers a cookie
    # session over a Bearer token: sent on `client` this would authenticate as
    # the owner and never exercise the token scope at all.
    anon = TestClient(server.api, raise_server_exceptions=True)
    refused = anon.get(f"{API}/insights", headers={"Authorization": f"Bearer {scoped}"})
    assert refused.status_code == 403, refused.text
