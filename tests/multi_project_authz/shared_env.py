"""Helpers behind the shared multi-project authz environment.

Plain functions and constants only: everything pytest has to *resolve* lives in
``conftest.py`` next door, and everything a test module has to *call* lives
here, where a normal ``from ... import`` reaches it.

That split is the point of this package. The two suites used to share one
environment by having ``test_generic_field_reader_allowlist.py`` copy names out
of ``test_multi_project_membership_authz.py``'s module namespace
(``env = _multi_project.env``). Fixtures do not survive that: pytest resolves a
fixture's parameters against the module it is *collected* in, so the moment the
copied ``env`` grew a ``_module_env`` dependency that had not also been copied,
every test in the borrowing module errored at setup with ``fixture
'_module_env' not found`` - silently, and only in the borrowing file. Fixtures
now come from the package ``conftest.py``, where dependencies resolve
themselves and nothing has to be re-exported by hand.
"""

import contextlib
import os
import time
from pathlib import Path

from sqlalchemy import func, text
from sqlmodel import delete, select, update

from pixlstash.db_models import (
    Character,
    Face,
    Picture,
    PictureLikeness,
    PictureSet,
    Project,
    UserToken,
)
from pixlstash.db_models.entity_project import (
    CharacterProjectMember,
    PictureSetProjectMember,
)
from pixlstash.db_models.picture_likeness import PictureLikenessQueue
from pixlstash.db_models.picture_project import PictureProjectMember
from pixlstash.db_models.picture_set import PictureSetMember
from pixlstash.db_models.picture_stack import PictureStack
from pixlstash.tasks import TaskType

API = "/api/v1"


@contextlib.contextmanager
def _enforcing(server):
    prev = server.authz._enforcing
    server.authz._enforcing = True
    try:
        yield
    finally:
        server.authz._enforcing = prev


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


#: The repository's sample library, two directories up from this package.
_GOOD_PICTURES_DIR = Path(__file__).resolve().parents[2] / "pictures" / "good"


def _good_picture_files():
    assert _GOOD_PICTURES_DIR.is_dir(), (
        f"{_GOOD_PICTURES_DIR} does not exist; the fixture library is resolved "
        f"relative to this file, so moving it needs the path updated too"
    )
    results = []
    for name in sorted(os.listdir(_GOOD_PICTURES_DIR)):
        path = os.path.join(_GOOD_PICTURES_DIR, name)
        ext = os.path.splitext(name)[1].lower()
        if ext in {".png", ".jpg", ".jpeg", ".webp"}:
            ct = "image/png" if ext == ".png" else "image/jpeg"
            with open(path, "rb") as fh:
                results.append((name, fh.read(), ct))
    return results


# Faces this file seeds by hand all use an index far outside the detector's
# range (900 / 901, see `_make_face` and `_seed_set_sort_inputs`), which is also
# what lets the per-test reset delete exactly the seeded rows and leave the real
# extraction output - the thing `_wait_faces_extracted` waits for - in place.
_SEEDED_FACE_INDEX_FLOOR = 900

# Finders that rewrite, or delete, the very rows this file seeds by hand. With a
# per-test Server they were mostly harmless: a cold vault has no models loaded,
# so the sweeps sat in backoff and never reached the two pictures before the
# server was torn down again. A module-scoped Server is always warm, so they
# land *inside* the tests instead - `FaceModelRefreshTask` deletes a seeded face
# whose model_pack it cannot reproduce, `LikenessParametersTask` DELETEs every
# pair touching a picture, and `ImageEmbeddingTask` owns `image_embedding` and
# `perceptual_hash`. They are detached once the module fixture has let them
# finish with the two uploads, which is both correct and much cheaper than
# polling for quiescence before every seed.
_VOLATILE_TASK_TYPES = (
    TaskType.FACE_MODEL_REFRESH,
    TaskType.IMAGE_EMBEDDING,
    TaskType.LIKENESS,
    TaskType.LIKENESS_PARAMETERS,
    TaskType.SOURCE_FACE_LIKENESS,
)

# Picture columns the reset restores verbatim from the post-import snapshot, so
# every test sees byte-identical picture rows however the previous one mangled
# them. `image_embedding` matters most: `_seed_set_sort_inputs` overwrites the
# real CLIP vector with a ones-vector, and the semantic-search test downstream
# reads it.
_PICTURE_BASELINE_COLUMNS = (
    "image_embedding",
    "likeness_parameters",
    "perceptual_hash",
    "score",
    "smart_score",
    "project_id",
    "stack_id",
    "pending_character_id",
    "deleted",
    "deleted_at",
)

# Probes the oracle tests rely on genuinely *not* existing. Asserted per test as
# the owner (who is never scope-restricted), because "no such project" and "you
# may not see that project" are the two answers those tests exist to prove
# indistinguishable - for a scoped token only.
_MISSING_PROJECT_PROBES = ("99999999", "NoSuchProjectHere")


def _detach_volatile_finders(server):
    """Take `_VOLATILE_TASK_TYPES` out of the running WorkPlanner.

    The planner itself keeps running - the staging-import endpoint refuses while
    the face worker is down, and three tests here import - so this removes
    finders rather than stopping the scheduler. `WorkPlanner.detach_finders`
    edits the planner's three finder structures under the planner's own lock and
    marks each removed finder exhausted, so a finder that `depends_on()` one of
    them is not left waiting for a report that will never come.

    This used to stop the planner, wait out its thread by hand and start it
    again, because editing `_task_finders` under a live loop threw IndexError
    there and killed the planner outright. That is fixed in the planner itself,
    so the stop/wait/restart dance is gone.
    """
    planner = server.vault._work_planner
    finders = server.vault._planner_work_finders
    for task_type in _VOLATILE_TASK_TYPES:
        assert finders.pop(task_type, None) is not None, (
            f"{task_type} is not registered any more; this module's seeded rows "
            f"are only stable because it is detached - re-check the new finder set"
        )
    removed = planner.detach_finders(_VOLATILE_TASK_TYPES)
    assert planner.is_running(), (
        "the WorkPlanner is not running after detaching finders; the import "
        "tests in this module need a live worker"
    )
    return removed


def _picture_baseline(server, picture_ids):
    """Snapshot `_PICTURE_BASELINE_COLUMNS` for *picture_ids*."""

    def _read(session):
        rows = {}
        for picture_id in picture_ids:
            picture = session.get(Picture, int(picture_id))
            assert picture is not None, f"picture {picture_id} vanished before snapshot"
            rows[int(picture_id)] = {
                column: getattr(picture, column) for column in _PICTURE_BASELINE_COLUMNS
            }
        return rows

    return server.vault.db.run_immediate_read_task(_read)


def _reset_domain_state(server, baseline):
    """Put the vault back to "two imported pictures and nothing else".

    Everything this file's tests create - projects, sets, characters, stacks,
    hand-seeded faces, likeness pairs, tokens - is removed, and the two fixture
    pictures are restored column-for-column from *baseline*. The pictures
    themselves are deliberately NOT deleted: they keep their ids, their real
    extracted faces and their real embeddings, so nothing has to be re-imported
    and no finder can be left holding a claim on an id SQLite then hands to a
    different row.

    The deletes are ordered children-before-parents so that foreign keys stay
    satisfied statement by statement; that, not the pragma, is what makes this
    correct. `PRAGMA defer_foreign_keys` is kept on top of it for the FK edges
    nobody enumerated, and preferred over switching `foreign_keys` off and back
    on because it dies with the transaction rather than leaving enforcement
    disabled on a pooled connection for every later test. It is *asserted* live
    rather than assumed: issued before any DML it would silently run in
    autocommit and be gone by the time it was needed.

    Tokens are deleted from the **hub** database, not the vault. `usertoken` is a
    hub table (``pixlstash/hub/schema.py``) and ``AuthService`` reads it through
    its own handle; the vault carries an empty, never-read copy only because the
    baseline migration creates every model's table. Deleting the vault's copy
    revokes nothing, which is worth stating explicitly: it looks right, it runs
    without error, and it leaves every previous test's token live.
    """

    def _do(session):
        # The DELETE goes first on purpose: pysqlite emits BEGIN lazily on the
        # first DML, and `defer_foreign_keys` only holds for the transaction it
        # is set in - issued before any statement it lands in autocommit and is
        # gone again by the time it is needed. The assertion below proves it is
        # live rather than trusting that.
        session.exec(delete(Face).where(Face.face_index >= _SEEDED_FACE_INDEX_FLOOR))
        session.exec(text("PRAGMA defer_foreign_keys = ON"))
        assert session.exec(text("PRAGMA defer_foreign_keys")).one()[0] == 1, (
            "deferred FK enforcement did not engage; the deletes below would be "
            "order-sensitive without it"
        )
        session.exec(delete(PictureLikeness))
        session.exec(delete(PictureLikenessQueue))
        # Children before parents, so the statement order alone leaves every
        # foreign key satisfied and the pragma above is a safety net rather than
        # the thing correctness rests on. Removing the pragma from this exact
        # order was tried: it stays green, whereas deleting `project` while a
        # picture still pointed at it failed outright.
        # `pending_character_id` is nulled here for the pictures the
        # staging-import tests leave behind: the vault turns it into a
        # `Face.character_id` on a later pass, and character ids recycle, so a
        # survivor would re-target the *next* test's SharedChar.
        session.exec(
            update(Picture).values(
                stack_id=None, project_id=None, pending_character_id=None
            )
        )
        session.exec(delete(PictureStack))
        session.exec(delete(PictureProjectMember))
        session.exec(delete(CharacterProjectMember))
        session.exec(delete(PictureSetProjectMember))
        session.exec(delete(PictureSetMember))
        session.exec(delete(Character))
        session.exec(delete(PictureSet))
        session.exec(delete(Project))
        for picture_id, columns in baseline.items():
            picture = session.get(Picture, picture_id)
            assert picture is not None, (
                f"fixture picture {picture_id} was deleted by a previous test; the "
                f"shared library is unusable"
            )
            for column, value in columns.items():
                setattr(picture, column, value)
            session.add(picture)
        session.commit()

    server.vault.db.run_task(_do)

    def _revoke_tokens(session):
        session.exec(delete(UserToken))
        session.commit()

    server.auth._db.run_task(_revoke_tokens)
    # The token cache mirrors the rows just deleted, and a bare `.clear()` skips
    # the revocation epoch bump (see AuthService._flush_token_cache).
    server.auth._flush_token_cache()
    # `Session.scalar` unwraps the single column itself, so this compares an int
    # to an int. Accepting `(0,)` as well would mean the assertion also passes
    # for a `Row` holding *any* count, which is the one thing it must not do:
    # what it guards is that no credential survived the reset.
    remaining = server.auth._db.run_immediate_read_task(
        lambda session: session.scalar(select(func.count()).select_from(UserToken))
    )
    assert remaining == 0, (
        f"{remaining} token row(s) survived the reset; a stale credential stays live"
    )


def _build_fixture_entities(client, pic_a):
    """Create the three projects and the four entities the whole file asserts on.

    Returns the ids as a plain dict. Kept as a function so the module fixture and
    the per-test reset build exactly the same shape from exactly one place.
    """
    projects = {}
    for label in ("P1", "P2", "P3"):
        r = client.post(f"{API}/projects", json={"name": label})
        assert r.status_code in (200, 201), r.text
        projects[label] = r.json()["id"]

    # Set S holds picture A and belongs to BOTH P1 and P2. The member is added
    # before the project assignment so the PATCH reconciles picture-project
    # membership for both projects in one pass.
    r = client.post(f"{API}/picture_sets", json={"name": "SharedSet"})
    assert r.status_code in (200, 201), r.text
    set_id = r.json()["picture_set"]["id"]
    r = client.post(f"{API}/picture_sets/{set_id}/members/{pic_a}")
    assert r.status_code in (200, 201), r.text
    r = client.patch(
        f"{API}/picture_sets/{set_id}",
        json={"project_ids": [projects["P1"], projects["P2"]]},
    )
    assert r.status_code == 200, r.text

    # Character C belongs to BOTH P1 and P2 (created multi-project directly).
    r = client.post(
        f"{API}/characters",
        json={"name": "SharedChar", "project_ids": [projects["P1"], projects["P2"]]},
    )
    assert r.status_code == 200, r.text
    char_id = r.json()["character"]["id"]

    # Single-project control: belongs to P1 only, so a P2 token must be
    # refused it - proving the widening did not become "any project wins".
    r = client.post(
        f"{API}/picture_sets",
        json={"name": "P1OnlySet", "project_ids": [projects["P1"]]},
    )
    assert r.status_code in (200, 201), r.text
    p1_only_set_id = r.json()["picture_set"]["id"]
    r = client.post(
        f"{API}/characters",
        json={"name": "P1OnlyChar", "project_ids": [projects["P1"]]},
    )
    assert r.status_code == 200, r.text
    p1_only_char_id = r.json()["character"]["id"]

    return {
        "projects": projects,
        "set_id": set_id,
        "char_id": char_id,
        "p1_only_set_id": p1_only_set_id,
        "p1_only_char_id": p1_only_char_id,
    }


def _assert_fixture_shape(owner, ids, pic_a, pic_b):
    """Re-prove, by identity, the world every assertion in this file describes.

    This is the shared environment's integrity check, and it deliberately runs
    from the autouse fixture ahead of *every* test rather than from a trailing
    "canary" test: the CI gate deals tests individually across shards
    (``--ci-shard``, tests/conftest.py), so a canary would only ever guard the
    shard it happened to land in.

    Everything below is asserted on **identity** - which projects exist, under
    which names and ids, which entity is a member of which, which picture is in
    the set - never on a count, because a leaked or missing row is exactly what
    corrupts a count.

    The last two blocks are here for the oracle tests specifically
    (``test_project_path_routes_are_not_an_existence_oracle`` and its project-
    token twin). Those prove a scoped token cannot tell "this project holds the
    entity" from "this project does not" from "this project does not exist" -
    which is only a proof if the three cases are genuinely different to begin
    with. A shared environment that left P3 deleted, or SharedSet detached from
    P1, would collapse all three probes into "missing" and the oracle test would
    pass while demonstrating the opposite of its docstring. So the difference is
    established here as the **owner**, who is never scope-restricted, before any
    token is asked to be blind to it.
    """
    projects = ids["projects"]
    listed = {p["name"]: p["id"] for p in owner.get(f"{API}/projects").json()}
    assert listed == projects, (
        f"the three fixture projects must be exactly the projects that exist: "
        f"{listed} != {projects}"
    )

    both = sorted([projects["P1"], projects["P2"]])
    body = owner.get(f"{API}/characters/{ids['char_id']}").json()
    assert body["project_ids"] == both, f"SharedChar membership drifted: {body}"
    body = owner.get(f"{API}/picture_sets/{ids['set_id']}?info=true").json()
    assert body["project_ids"] == both, f"SharedSet membership drifted: {body}"
    body = owner.get(f"{API}/characters/{ids['p1_only_char_id']}").json()
    assert body["project_ids"] == [projects["P1"]], f"P1OnlyChar drifted: {body}"
    body = owner.get(f"{API}/picture_sets/{ids['p1_only_set_id']}?info=true").json()
    assert body["project_ids"] == [projects["P1"]], f"P1OnlySet drifted: {body}"

    r = owner.get(f"{API}/picture_sets/{ids['set_id']}")
    assert r.status_code == 200, r.text
    members = {p["id"] for p in r.json()["pictures"]}
    assert members == {pic_a}, (
        f"SharedSet must hold picture {pic_a} and nothing else; got {sorted(members)}"
    )
    # Picture membership, which the R2 tests read as "did this write reach every
    # project": A is anchored in P1+P2 through the set, B is filed nowhere.
    expected_membership = {pic_a: projects["P1"], pic_b: None}
    for picture_id, expected in expected_membership.items():
        r = owner.get(f"{API}/pictures/{picture_id}/metadata")
        assert r.status_code == 200, (
            f"fixture picture {picture_id} is gone - a negative assertion below "
            f"would be refused for the wrong reason: {r.text}"
        )
        assert r.json()["project_id"] == expected, (
            f"picture {picture_id} starts in the wrong project: "
            f"{r.json()['project_id']} != {expected}"
        )
    for label, project_id in projects.items():
        listed = {
            p["id"] for p in owner.get(f"{API}/pictures?project_id={project_id}").json()
        }
        expected = {pic_a} if label in ("P1", "P2") else set()
        assert listed == expected, (
            f"{label} must hold exactly {sorted(expected)} at the start of a "
            f"test; got {sorted(listed)}"
        )

    # The oracle tests' three probe shapes must actually be three different
    # things for the owner.
    r = owner.get(f"{API}/projects/P1/picture_sets/SharedSet")
    assert r.status_code == 200 and r.json()["id"] == ids["set_id"], (
        f"P1 must hold SharedSet, or the oracle probes collapse to two cases: "
        f"{r.status_code} {r.text[:200]}"
    )
    r = owner.get(f"{API}/projects/P3/picture_sets/SharedSet")
    assert r.status_code == 404 and "Picture set not found" in r.text, (
        f"P3 must exist and NOT hold SharedSet, or the oracle probes collapse: "
        f"{r.status_code} {r.text[:200]}"
    )
    for probe in _MISSING_PROJECT_PROBES:
        r = owner.get(f"{API}/projects/{probe}")
        assert r.status_code == 404 and "Project not found" in r.text, (
            f"the oracle tests use {probe!r} as a project that does not exist, "
            f"but it answers {r.status_code}: {r.text[:200]}"
        )


# Generous on purpose, and the number is evidence-led. CI runs the whole suite
# `--force-cpu` on a shared runner, so this waits on a face pass that has no GPU
# and may still be fetching its model pack. At 60 s it timed out on two separate
# PRs whose diffs could not touch face extraction - one of them frontend-only -
# reporting `rows per picture: {}`, i.e. the pass had produced nothing at all
# rather than being partway through. The same suite takes ~98 s end to end
# locally WITH a GPU. `test_likeness_and_face_search` already allows 120 s for an
# ML wait, so this is the suite's own scale rather than a new one.
#
# It is a ceiling on a poll loop, not a sleep: a pass that finishes in two
# seconds still returns in two seconds, and only a genuine hang pays the full
# budget before failing.
def _wait_faces_extracted(server, picture_ids, timeout_s=180.0):
    """Block until background face extraction has finished with *picture_ids*.

    Uploading a picture queues an extraction pass that ends by writing either
    the detected faces or a ``face_index=-1`` sentinel, so "the picture has at
    least one face row" is the signal that the pass is done. Waiting for it is
    not optional: a face seeded *before* the pass lands is deleted underneath
    the test (``Picture.faces`` cascades ``delete-orphan``), which was observed
    here as the reference face vanishing between two requests in the same test.
    """

    def _counts(session):
        return {
            int(pid): int(count)
            for pid, count in session.exec(
                select(Face.picture_id, func.count())
                .where(Face.picture_id.in_([int(p) for p in picture_ids]))
                .group_by(Face.picture_id)
            ).all()
        }

    start = time.time()
    counts = {}
    while time.time() - start < timeout_s:
        counts = server.vault.db.run_immediate_read_task(_counts)
        if all(counts.get(int(pid), 0) > 0 for pid in picture_ids):
            return
        time.sleep(0.25)
    raise AssertionError(
        f"face extraction did not finish for {list(picture_ids)} in {timeout_s}s; "
        f"rows per picture: {counts}"
    )


def _make_face(server, picture_id: int) -> int:
    """Insert a synthetic face row on *picture_id*.

    The face-assign path is exercised through its ``face_ids`` branch so the test
    does not depend on the detector finding a face in the CPU test profile (the
    reviewer's own probe failed twice for exactly that reason). ``face_index`` is
    deliberately far outside the detector's range so a real extraction running in
    the background cannot collide with the (picture, frame, face) unique
    constraint.
    """

    def _do(session):
        face = Face(
            picture_id=int(picture_id),
            frame_index=0,
            face_index=900,
            bbox=[0, 0, 16, 16],
        )
        session.add(face)
        session.commit()
        session.refresh(face)
        return int(face.id)

    return server.vault.db.run_task(_do)
