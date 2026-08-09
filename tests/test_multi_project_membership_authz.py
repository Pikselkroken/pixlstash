"""Issue #125 — multi-project characters / picture sets, and what it does to scope.

Making a character or picture set reachable from several projects **widens** what
a project-scoped share token can see: a token for project B now reaches an entity
whose *primary* project is A, as long as B is among its memberships. That is the
intended semantics, and it is exactly the kind of change that must be pinned in
both directions, because the two failure modes are opposite and both are bugs:

* **Under-grant (over-blocking).** Reading the legacy scalar
  ``Character.project_id`` / ``PictureSet.project_id`` instead of the new join
  tables makes a secondary membership invisible: project B's token is refused an
  entity it legitimately shares, and B's listings silently omit it. Over-blocking
  is its own regression (CLAUDE.md §Security & authorization review process).
* **Over-grant (BOLA).** The widening must stop at declared membership: a token
  for an unrelated project C must still be 403'd on the same routes, and must not
  learn the entity exists through any sibling vector.

Every assertion below therefore pairs an in-scope 200 with an out-of-scope 403,
across the sibling vectors that share the semantics: by-id and by-name routes
(the name-derived routes keep an inline check per §16.1, so they are a genuinely
separate enforcement path), list and single-item routes, the locked-members
listing, the project's own set listing, and the picture-level consequence of an
entity's membership.

Fixture shape (deliberately three projects, not two): set ``S`` and character
``C`` belong to ``{P1, P2}``; ``P3`` exists solely as the out-of-scope probe, so
"403" can never be an artefact of the resource simply not existing.
"""

import contextlib
import gc
import io
import json
import os
import tempfile
import time
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image
from sqlalchemy import func, text
from sqlmodel import delete, select, update
from starlette.testclient import TestClient

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
from pixlstash.server import Server
from pixlstash.tasks import TaskType
from tests.authz_guard import (  # noqa: F401
    assert_real_route,
    no_spa_fallback,
    resolves_to_real_route,
)
from tests.utils import upload_pictures_and_wait, wait_likeness_settled

API = "/api/v1"

# Every positive assertion here must reach a real route: the SPA catch-all answers
# unmatched GETs with 200, which once made a whole-library BOLA vector's test
# vacuous. See tests/authz_guard.py.
pytestmark = pytest.mark.usefixtures("no_spa_fallback")


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


def _good_picture_files():
    pictures_dir = os.path.join(os.path.dirname(__file__), "..", "pictures", "good")
    results = []
    for name in sorted(os.listdir(pictures_dir)):
        path = os.path.join(pictures_dir, name)
        ext = os.path.splitext(name)[1].lower()
        if ext in {".png", ".jpg", ".jpeg", ".webp"}:
            ct = "image/png" if ext == ".png" else "image/jpeg"
            with open(path, "rb") as fh:
                results.append((name, fh.read(), ct))
    return results


# Faces this file seeds by hand all use an index far outside the detector's
# range (900 / 901, see `_make_face` and `_seed_set_sort_inputs`), which is also
# what lets the per-test reset delete exactly the seeded rows and leave the real
# extraction output — the thing `_wait_faces_extracted` waits for — in place.
_SEEDED_FACE_INDEX_FLOOR = 900

# Finders that rewrite, or delete, the very rows this file seeds by hand. With a
# per-test Server they were mostly harmless: a cold vault has no models loaded,
# so the sweeps sat in backoff and never reached the two pictures before the
# server was torn down again. A module-scoped Server is always warm, so they
# land *inside* the tests instead — `FaceModelRefreshTask` deletes a seeded face
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
# indistinguishable — for a scoped token only.
_MISSING_PROJECT_PROBES = ("99999999", "NoSuchProjectHere")


def _detach_volatile_finders(server):
    """Take `_VOLATILE_TASK_TYPES` out of the running WorkPlanner.

    The planner itself keeps running — the staging-import endpoint refuses while
    the workers are down, and three tests here import — so this removes finders
    rather than stopping the scheduler. `WorkPlanner.__init__` copies the finder
    mapping into three of its own structures, so all three have to be pruned;
    each removed finder is also marked exhausted so a dependent finder is not
    left waiting forever for one that will never report again.
    """
    planner = server.vault._work_planner
    finders = server.vault._planner_work_finders
    names = set()
    for task_type in _VOLATILE_TASK_TYPES:
        finder = finders.pop(task_type, None)
        assert finder is not None, (
            f"{task_type} is not registered any more; this module's seeded rows "
            f"are only stable because it is detached — re-check the new finder set"
        )
        names.add(finder.finder_name())

    # Stop the scheduler before shortening its list. `_run_finders_once` reads
    # `_task_finders[idx]` against a length it captured a moment earlier, so
    # swapping the list under a live thread throws IndexError there and kills
    # the planner outright — observed, not theoretical.
    planner.stop()
    for task_type in _VOLATILE_TASK_TYPES:
        planner._finder_name_by_task_type.pop(task_type, None)
    planner._task_finders = [
        finder for finder in planner._task_finders if finder.finder_name() not in names
    ]
    for name in names:
        planner._task_finders_by_name.pop(name, None)
        # A detached finder never reports "nothing to do" again, and a finder
        # that depends_on() it would otherwise block for ever.
        planner._finder_exhausted[name] = True
    planner._finder_order_idx = 0
    planner.start()
    # `stop()` joins with a 5 s timeout and only *logs* if the thread outlives
    # it, and `start()` then returns early on a still-live thread while `_stop`
    # stays set — which would leave the planner dead for the module's lifetime
    # and the staging-import tests answering 503.
    assert planner.is_running(), (
        "the WorkPlanner did not restart after detaching finders; the import "
        "tests in this module need a live worker"
    )


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

    Everything this file's tests create — projects, sets, characters, stacks,
    hand-seeded faces, likeness pairs, tokens — is removed, and the two fixture
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
        # is set in — issued before any statement it lands in autocommit and is
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
    assert server.auth._db.run_immediate_read_task(
        lambda session: session.exec(select(func.count()).select_from(UserToken)).one()
    ) in (0, (0,)), "token rows survived the reset; a stale credential stays live"


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
    # refused it — proving the widening did not become "any project wins".
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

    Everything below is asserted on **identity** — which projects exist, under
    which names and ids, which entity is a member of which, which picture is in
    the set — never on a count, because a leaked or missing row is exactly what
    corrupts a count.

    The last two blocks are here for the oracle tests specifically
    (``test_project_path_routes_are_not_an_existence_oracle`` and its project-
    token twin). Those prove a scoped token cannot tell "this project holds the
    entity" from "this project does not" from "this project does not exist" —
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
            f"fixture picture {picture_id} is gone — a negative assertion below "
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


@pytest.fixture(scope="module")
def _module_env():
    """One Server, one login and one imported library for the whole module.

    Booting a Server (migrations, vault start-up, route registration), importing
    two real pictures through the pipeline and tearing the lot down again is the
    entire cost of this file — the assertions themselves are HTTP calls costing
    milliseconds. It is paid once here; per-test isolation comes from the
    autouse ``env`` fixture below, which rebuilds the domain state and re-mints
    every credential.
    """
    temp_dir = tempfile.TemporaryDirectory()
    config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(config_path, "w") as fh:
        fh.write(json.dumps({"port": 8000}))
    server = Server(config_path)
    server.__enter__()
    try:
        client = TestClient(server.api, raise_server_exceptions=True)
        anon = TestClient(server.api, raise_server_exceptions=True)
        r = client.post(
            f"{API}/login", json={"username": "owner", "password": "ownerpass1"}
        )
        assert r.status_code == 200, r.text

        files = [("file", (n, d, c)) for n, d, c in _good_picture_files()[:2]]
        assert len(files) >= 2, "need >=2 test pictures"
        # 120 s, not 30: this is the first import after a cold Server boot in
        # this file, and on the shared Windows CI runner that start-up cost
        # (ONNX session init, thumbnailing) blew a 30 s wall-clock bound while
        # the import itself was healthy. Same reasoning as the recorded
        # test_florence skip: a tight wall-clock bound on contended CI hardware
        # is a flake generator, not a signal.
        st = upload_pictures_and_wait(client, files, timeout_s=120)
        assert st["status"] == "completed", st
        pic_ids = [p["id"] for p in client.get(f"{API}/pictures").json()]
        assert len(pic_ids) >= 2
        pic_a, pic_b = pic_ids[0], pic_ids[1]

        # Let the background pipeline finish with the two uploads ONCE, then
        # detach the finders that would keep rewriting what the tests seed. The
        # alternative — waiting for quiescence before each seed — is the slower
        # of the two by a wide margin, and it does not stop a sweep landing
        # halfway through the assertions that follow it.
        _wait_faces_extracted(server, [pic_a, pic_b])
        wait_likeness_settled(server)
        _detach_volatile_finders(server)

        yield SimpleNamespace(
            server=server,
            owner=client,
            anon=anon,
            pic_a=pic_a,
            pic_b=pic_b,
            baseline=_picture_baseline(server, [pic_a, pic_b]),
        )
    finally:
        server.__exit__(None, None, None)
        temp_dir.cleanup()
        gc.collect()


@pytest.fixture(autouse=True)
def env(_module_env, request):
    """Fresh projects, entities and credentials for every test.

    Autouse rather than opt-in: a test added later that forgets to request
    ``env`` would otherwise inherit whatever the previous one left behind.

    ``no_spa_fallback`` is pulled in explicitly below, and that is not
    decoration: pytest sets autouse fixtures up *before* the
    ``usefixtures``-requested ones of the same scope, so making this fixture
    autouse moved its own ~25 HTTP calls — including the bare
    ``status_code == 200`` positive control at the end — out from under the
    anti-vacuity guard, where the SPA catch-all could have satisfied them
    (tests/authz_guard.py). Requesting it here puts them back under it.

    What is reset, and why each one is not optional in a *security* suite where
    almost every assertion is a refusal:

    * **The domain state** (``_reset_domain_state``). A negative assertion here
      reads "403", and a 403 is what you also get when the object is simply
      gone. ``test_deleting_a_project_leaves_the_other_membership_intact``
      deletes P2 outright, ``test_locked_members_listing_both_directions`` locks
      the shared set, and half a dozen tests file extra pictures into projects.
      Every one of those would leave a later test proving nothing.
    * **The credentials.** Every token row is deleted and re-minted per test, so
      a revoked or stale token can never masquerade as a scope refusal. Note
      that the ids themselves recycle — SQLite hands project 1 straight back
      after a whole-table delete — so a surviving token from a previous test
      would still *authenticate*, and against whatever now occupies its id.
      Deleting the rows is therefore the only thing standing between this suite
      and an isolation guarantee resting on rowid reuse. It also keeps token
      verification cheap: it is a bcrypt call per candidate row.
    * **The shape** (``_assert_fixture_shape``), which re-proves by identity
      that the world the assertions describe is actually there.
    """
    request.getfixturevalue("no_spa_fallback")
    m = _module_env
    server, client, anon = m.server, m.owner, m.anon

    _reset_domain_state(server, m.baseline)

    r = client.post(
        f"{API}/login", json={"username": "owner", "password": "ownerpass1"}
    )
    assert r.status_code == 200, (
        f"owner re-login failed — the shared environment is dirty: {r.text}"
    )

    ids = _build_fixture_entities(client, m.pic_a)
    _assert_fixture_shape(client, ids, m.pic_a, m.pic_b)

    def mint(resource_type, resource_id):
        r = client.post(
            f"{API}/users/me/token",
            json={
                "description": f"{resource_type}:{resource_id}",
                "scope": "READ",
                "resource_type": resource_type,
                "resource_id": resource_id,
            },
        )
        assert r.status_code == 200, r.text
        return r.json()["token"]

    tokens = {label: mint("project", pid) for label, pid in ids["projects"].items()}

    # The positive control for every refusal below: each freshly minted token
    # must actually work on an in-scope read *now*. Without this a dead or
    # unminted credential produces exactly the 403 the negative assertions are
    # looking for, and the whole file passes for the wrong reason.
    with _enforcing(server):
        for label, token in tokens.items():
            probe = anon.get(
                f"{API}/projects/{ids['projects'][label]}", headers=_bearer(token)
            )
            assert probe.status_code == 200, (
                f"the fresh {label} token cannot read its own project "
                f"({probe.status_code}: {probe.text[:200]}) — every 403 asserted "
                f"in this test would prove nothing"
            )

    yield {
        "server": server,
        "owner": client,
        "anon": anon,
        "pic_a": m.pic_a,
        "pic_b": m.pic_b,
        "tokens": tokens,
        # Exposed so a test can mint a character- / set-scoped token too: the
        # `project_ids` narrowing (R1) has a different rung for those.
        "mint": mint,
        **ids,
    }


# ---------------------------------------------------------------------------
# The write path: both representations stay in sync
# ---------------------------------------------------------------------------


def test_membership_is_written_to_both_representations(env):
    """``project_ids`` is the read model; the legacy scalar ``project_id`` stays
    populated with the primary (lowest) project. Neither may drift."""
    owner, projects = env["owner"], env["projects"]
    both = sorted([projects["P1"], projects["P2"]])

    body = owner.get(f"{API}/characters/{env['char_id']}").json()
    assert body["project_ids"] == both
    assert body["project_id"] == both[0], (
        "the legacy FK must keep naming the primary project — it is not dropped "
        "until a later cleanup release"
    )

    body = owner.get(f"{API}/picture_sets/{env['set_id']}?info=true").json()
    assert body["project_ids"] == both
    assert body["project_id"] == both[0]


def test_leaving_one_project_keeps_the_other(env):
    """Dropping P2 leaves the entity in P1 — the FK follows, and the entity does
    not become unassigned."""
    owner, projects = env["owner"], env["projects"]
    r = owner.patch(
        f"{API}/characters/{env['char_id']}", json={"project_ids": [projects["P1"]]}
    )
    assert r.status_code == 200, r.text
    body = owner.get(f"{API}/characters/{env['char_id']}").json()
    assert body["project_ids"] == [projects["P1"]]
    assert body["project_id"] == projects["P1"]

    # Restore, so the fixture's shared state is not left half-torn for readers.
    r = owner.patch(
        f"{API}/characters/{env['char_id']}",
        json={"project_ids": [projects["P1"], projects["P2"]]},
    )
    assert r.status_code == 200, r.text


def test_unknown_project_id_is_404_not_a_silent_partial_write(env):
    """A membership write naming a missing project is rejected whole."""
    owner, projects = env["owner"], env["projects"]
    r = owner.patch(
        f"{API}/characters/{env['char_id']}",
        json={"project_ids": [projects["P1"], 9_999_999]},
    )
    assert r.status_code == 404, r.text
    body = owner.get(f"{API}/characters/{env['char_id']}").json()
    assert body["project_ids"] == sorted([projects["P1"], projects["P2"]])


# ---------------------------------------------------------------------------
# Scope: by-id routes, both directions
# ---------------------------------------------------------------------------


def test_character_by_id_secondary_project_token_reaches_it(env):
    """CHARACTER_SCOPED ``GET /characters/{id}``: the P2 token reaches a character
    whose primary project is P1 (in-scope 200, the widening), the P3 token does
    not (out-of-scope 403), and a P1-only character is refused to P2 (the
    widening did not degrade into "any project")."""
    anon, tokens = env["anon"], env["tokens"]
    path = f"{API}/characters/{env['char_id']}"
    assert_real_route(env["server"].api, "GET", path)
    with _enforcing(env["server"]):
        assert anon.get(path, headers=_bearer(tokens["P1"])).status_code == 200
        r = anon.get(path, headers=_bearer(tokens["P2"]))
        assert r.status_code == 200, (
            f"secondary-project token must not be over-blocked: {r.status_code} "
            f"{r.text}"
        )
        r = anon.get(path, headers=_bearer(tokens["P3"]))
        assert r.status_code == 403, f"unrelated project must 403: {r.text}"

        r = anon.get(
            f"{API}/characters/{env['p1_only_char_id']}", headers=_bearer(tokens["P2"])
        )
        assert r.status_code == 403, f"P1-only character must 403 for P2: {r.text}"


def test_picture_set_by_id_secondary_project_token_reaches_it(env):
    """SET_SCOPED sibling of the character route, same three directions."""
    anon, tokens = env["anon"], env["tokens"]
    path = f"{API}/picture_sets/{env['set_id']}"
    assert_real_route(env["server"].api, "GET", path)
    with _enforcing(env["server"]):
        assert anon.get(path, headers=_bearer(tokens["P1"])).status_code == 200
        r = anon.get(path, headers=_bearer(tokens["P2"]))
        assert r.status_code == 200, (
            f"secondary-project token must not be over-blocked: {r.status_code} "
            f"{r.text}"
        )
        assert anon.get(path, headers=_bearer(tokens["P3"])).status_code == 403

        r = anon.get(
            f"{API}/picture_sets/{env['p1_only_set_id']}",
            headers=_bearer(tokens["P2"]),
        )
        assert r.status_code == 403, f"P1-only set must 403 for P2: {r.text}"


def test_picture_scope_follows_the_shared_set(env):
    """PICTURE_SCOPED consequence: the set's member picture is anchored in BOTH
    projects, so the P2 token reaches it; a non-member picture is still 403."""
    anon, tokens = env["anon"], env["tokens"]
    # A refusal is only evidence of scope enforcement if the route exists: the
    # scope checks sit ahead of routing, so a renamed or misspelled path answers
    # a scoped token with the same 403 an in-scope refusal does.
    assert_real_route(
        env["server"].api, "GET", f"{API}/pictures/{env['pic_a']}/metadata"
    )
    with _enforcing(env["server"]):
        r = anon.get(
            f"{API}/pictures/{env['pic_a']}/metadata", headers=_bearer(tokens["P2"])
        )
        assert r.status_code == 200, f"in-scope picture must pass: {r.text}"
        r = anon.get(
            f"{API}/pictures/{env['pic_b']}/metadata", headers=_bearer(tokens["P2"])
        )
        assert r.status_code == 403, f"out-of-scope picture must 403: {r.text}"
        r = anon.get(
            f"{API}/pictures/{env['pic_a']}/metadata", headers=_bearer(tokens["P3"])
        )
        assert r.status_code == 403, f"unrelated project must 403: {r.text}"


# ---------------------------------------------------------------------------
# Scope: the name-derived sibling routes (§16.1 residual inline enforcement)
# ---------------------------------------------------------------------------


def test_character_by_project_and_name_both_directions(env):
    """``GET /projects/{project_name}/characters/{character_name}`` resolves the
    character *within* a named project and keeps an inline scope check (§16.1).
    It must find the shared character under its secondary project's name, admit
    that project's token, and refuse an unrelated one."""
    owner, anon, tokens = env["owner"], env["anon"], env["tokens"]
    path = f"{API}/projects/P2/characters/SharedChar"
    assert_real_route(env["server"].api, "GET", path)
    # Owner: the lookup must resolve at all under the secondary project.
    r = owner.get(path)
    assert r.status_code == 200, f"secondary-project name lookup must resolve: {r.text}"
    assert r.json()["id"] == env["char_id"]
    with _enforcing(env["server"]):
        assert anon.get(path, headers=_bearer(tokens["P2"])).status_code == 200
        assert anon.get(path, headers=_bearer(tokens["P3"])).status_code == 403


def test_picture_set_by_project_and_name_both_directions(env):
    """Set twin of the character-by-name route, same three directions."""
    owner, anon, tokens = env["owner"], env["anon"], env["tokens"]
    path = f"{API}/projects/P2/picture_sets/SharedSet"
    assert_real_route(env["server"].api, "GET", path)
    r = owner.get(path)
    assert r.status_code == 200, f"secondary-project name lookup must resolve: {r.text}"
    assert r.json()["id"] == env["set_id"]
    with _enforcing(env["server"]):
        assert anon.get(path, headers=_bearer(tokens["P2"])).status_code == 200
        assert anon.get(path, headers=_bearer(tokens["P3"])).status_code == 403


# ---------------------------------------------------------------------------
# Scope: list routes (SCOPED_LIST — the token narrows the listing in-handler)
# ---------------------------------------------------------------------------


def test_character_list_is_narrowed_to_the_tokens_project(env):
    """``GET /characters`` forces a project token's listing to its own project.
    The shared character appears for P1 and P2 and not for P3, and the P1-only
    character never appears for P2."""
    anon, tokens = env["anon"], env["tokens"]
    path = f"{API}/characters"
    assert_real_route(env["server"].api, "GET", path)
    with _enforcing(env["server"]):
        for label in ("P1", "P2"):
            r = anon.get(path, headers=_bearer(tokens[label]))
            assert r.status_code == 200, r.text
            ids = {c["id"] for c in r.json()}
            assert env["char_id"] in ids, (
                f"{label} token must see the shared character; got {sorted(ids)}"
            )
        r = anon.get(path, headers=_bearer(tokens["P2"]))
        assert env["p1_only_char_id"] not in {c["id"] for c in r.json()}

        r = anon.get(path, headers=_bearer(tokens["P3"]))
        assert r.status_code == 200, r.text
        assert env["char_id"] not in {c["id"] for c in r.json()}, (
            "an unrelated project's token must not learn the character exists"
        )


def test_picture_set_list_is_narrowed_to_the_tokens_project(env):
    """``GET /picture_sets`` twin of the character listing."""
    anon, tokens = env["anon"], env["tokens"]
    path = f"{API}/picture_sets"
    assert_real_route(env["server"].api, "GET", path)
    with _enforcing(env["server"]):
        for label in ("P1", "P2"):
            r = anon.get(path, headers=_bearer(tokens[label]))
            assert r.status_code == 200, r.text
            ids = {s["id"] for s in r.json()}
            assert env["set_id"] in ids, (
                f"{label} token must see the shared set; got {sorted(ids)}"
            )
        r = anon.get(path, headers=_bearer(tokens["P2"]))
        assert env["p1_only_set_id"] not in {s["id"] for s in r.json()}

        r = anon.get(path, headers=_bearer(tokens["P3"]))
        assert r.status_code == 200, r.text
        assert env["set_id"] not in {s["id"] for s in r.json()}


def test_owner_project_filters_read_the_join(env):
    """Owner-side listing filters: ``?project_id=`` matches a secondary membership,
    and ``UNASSIGNED`` must not swallow a multi-project entity."""
    owner, projects = env["owner"], env["projects"]
    for label in ("P1", "P2"):
        chars = owner.get(f"{API}/characters?project_id={projects[label]}").json()
        assert env["char_id"] in {c["id"] for c in chars}, label
        sets = owner.get(f"{API}/picture_sets?project_id={projects[label]}").json()
        assert env["set_id"] in {s["id"] for s in sets}, label

    chars = owner.get(f"{API}/characters?project_id={projects['P3']}").json()
    assert env["char_id"] not in {c["id"] for c in chars}
    sets = owner.get(f"{API}/picture_sets?project_id={projects['P3']}").json()
    assert env["set_id"] not in {s["id"] for s in sets}

    chars = owner.get(f"{API}/characters?project_id=UNASSIGNED").json()
    assert env["char_id"] not in {c["id"] for c in chars}
    sets = owner.get(f"{API}/picture_sets?project_id=UNASSIGNED").json()
    assert env["set_id"] not in {s["id"] for s in sets}


def test_project_picture_sets_listing_both_directions(env):
    """``GET /projects/{id_or_name}/picture_sets`` (PROJECT_SCOPED, name-derived
    inline check): P2 lists the shared set, P3's token is refused P2's listing."""
    owner, anon, tokens, projects = (
        env["owner"],
        env["anon"],
        env["tokens"],
        env["projects"],
    )
    path = f"{API}/projects/{projects['P2']}/picture_sets"
    assert_real_route(env["server"].api, "GET", path)
    r = owner.get(path)
    assert r.status_code == 200, r.text
    assert env["set_id"] in {s["id"] for s in r.json()}
    with _enforcing(env["server"]):
        r = anon.get(path, headers=_bearer(tokens["P2"]))
        assert r.status_code == 200, r.text
        assert env["set_id"] in {s["id"] for s in r.json()}
        assert anon.get(path, headers=_bearer(tokens["P3"])).status_code == 403


def test_locked_members_listing_both_directions(env):
    """``GET /picture_sets/locked-members`` narrows by project too — a locked
    shared set is visible to its secondary project's token and to nobody else."""
    owner, anon, tokens = env["owner"], env["anon"], env["tokens"]
    r = owner.patch(f"{API}/picture_sets/{env['set_id']}", json={"locked": True})
    assert r.status_code == 200, r.text
    path = f"{API}/picture_sets/locked-members"
    assert_real_route(env["server"].api, "GET", path)
    with _enforcing(env["server"]):
        for label in ("P1", "P2"):
            r = anon.get(path, headers=_bearer(tokens[label]))
            assert r.status_code == 200, r.text
            assert env["set_id"] in {s["id"] for s in r.json()["sets"]}, label
        r = anon.get(path, headers=_bearer(tokens["P3"]))
        assert r.status_code == 200, r.text
        assert env["set_id"] not in {s["id"] for s in r.json()["sets"]}


def test_scoped_list_pictures_not_over_blocked(env):
    """SCOPED_LIST pass-through must survive the change, including the
    ``character_id=UNASSIGNED`` branch that was a historical leak vector."""
    anon, tok = env["anon"], env["tokens"]["P2"]
    paths = (
        f"{API}/pictures",
        f"{API}/pictures/stream",
        f"{API}/pictures?character_id=UNASSIGNED",
    )
    for path in paths:
        assert_real_route(env["server"].api, "GET", path.split("?")[0])
    with _enforcing(env["server"]):
        for path in paths:
            r = anon.get(path, headers=_bearer(tok))
            assert r.status_code == 200, (
                f"SCOPED_LIST {path} must not be over-blocked; got "
                f"{r.status_code}: {r.text}"
            )


def test_deleting_a_project_leaves_the_other_membership_intact(env):
    """Deleting P2 removes only P2's rows: the entities stay in P1 and the picture
    keeps P1's membership. The P1 token still reaches them."""
    owner, anon, tokens, projects = (
        env["owner"],
        env["anon"],
        env["tokens"],
        env["projects"],
    )
    r = owner.delete(f"{API}/projects/{projects['P2']}")
    assert r.status_code == 200, r.text

    body = owner.get(f"{API}/characters/{env['char_id']}").json()
    assert body["project_ids"] == [projects["P1"]]
    assert body["project_id"] == projects["P1"]
    body = owner.get(f"{API}/picture_sets/{env['set_id']}?info=true").json()
    assert body["project_ids"] == [projects["P1"]]

    with _enforcing(env["server"]):
        assert (
            anon.get(
                f"{API}/characters/{env['char_id']}", headers=_bearer(tokens["P1"])
            ).status_code
            == 200
        )
        assert (
            anon.get(
                f"{API}/picture_sets/{env['set_id']}", headers=_bearer(tokens["P1"])
            ).status_code
            == 200
        )
        assert (
            anon.get(
                f"{API}/pictures/{env['pic_a']}/metadata",
                headers=_bearer(tokens["P1"]),
            ).status_code
            == 200
        )


# ---------------------------------------------------------------------------
# R1 — `project_ids` is membership metadata about *other* projects
# ---------------------------------------------------------------------------
#
# Every serialisation of a multi-project entity carries the full membership list.
# The entity itself is in scope for the token reading it; the ids of the *other*
# projects it is filed under are not, and are obtainable from no endpoint that
# token may call (``GET /projects/{other_id}`` is project-scoped and 403s). So the
# list is intersected with the token's visible projects, on the same ladder
# ``fetch_scope_allowed_set_ids`` implements. The owner is never narrowed.


def _char_project_ids(client, env, headers=None):
    """``project_ids`` for the shared character on every route that serialises it."""
    kw = {"headers": headers} if headers else {}
    out = {}
    r = client.get(f"{API}/characters/{env['char_id']}", **kw)
    assert r.status_code == 200, r.text
    out["by_id"] = r.json()["project_ids"]
    listed = {c["id"]: c for c in client.get(f"{API}/characters", **kw).json()}
    assert env["char_id"] in listed, "the shared character must still be listed"
    out["list"] = listed[env["char_id"]]["project_ids"]
    return out


def _set_project_ids(client, env, headers=None):
    """``project_ids`` for the shared set on every route that serialises it."""
    kw = {"headers": headers} if headers else {}
    out = {}
    r = client.get(f"{API}/picture_sets/{env['set_id']}?info=true", **kw)
    assert r.status_code == 200, r.text
    out["info"] = r.json()["project_ids"]
    r = client.get(f"{API}/picture_sets/{env['set_id']}", **kw)
    assert r.status_code == 200, r.text
    out["pictures"] = r.json()["set"]["project_ids"]
    listed = {s["id"]: s for s in client.get(f"{API}/picture_sets", **kw).json()}
    assert env["set_id"] in listed, "the shared set must still be listed"
    out["list"] = listed[env["set_id"]]["project_ids"]
    return out


def test_project_ids_narrowed_to_the_tokens_own_project(env):
    """A project-scoped token reads the shared entity (200 — over-blocking would
    be its own regression) but learns only its own project id from
    ``project_ids``; the owner keeps the full membership list."""
    owner, anon, tokens, projects = (
        env["owner"],
        env["anon"],
        env["tokens"],
        env["projects"],
    )
    both = sorted([projects["P1"], projects["P2"]])

    for site, ids in _char_project_ids(owner, env).items():
        assert ids == both, f"owner must not be narrowed on characters.{site}"
    for site, ids in _set_project_ids(owner, env).items():
        assert ids == both, f"owner must not be narrowed on picture_sets.{site}"
    assert (
        owner.get(f"{API}/projects/P1/characters/SharedChar").json()["project_ids"]
        == both
    )
    assert (
        owner.get(f"{API}/projects/P1/picture_sets/SharedSet").json()["project_ids"]
        == both
    )

    with _enforcing(env["server"]):
        for label in ("P1", "P2"):
            headers = _bearer(tokens[label])
            mine = [projects[label]]

            for site, ids in _char_project_ids(anon, env, headers).items():
                assert ids == mine, (
                    f"{label} token must not learn the other project's id from "
                    f"characters.{site}; got {ids}"
                )
            for site, ids in _set_project_ids(anon, env, headers).items():
                assert ids == mine, (
                    f"{label} token must not learn the other project's id from "
                    f"picture_sets.{site}; got {ids}"
                )

            # The name-derived siblings serialise it too.
            r = anon.get(
                f"{API}/projects/{label}/characters/SharedChar", headers=headers
            )
            assert r.status_code == 200, r.text
            assert r.json()["project_ids"] == mine
            r = anon.get(
                f"{API}/projects/{label}/picture_sets/SharedSet", headers=headers
            )
            assert r.status_code == 200, r.text
            assert r.json()["project_ids"] == mine


def test_project_ids_is_empty_for_entity_scoped_tokens(env):
    """The other rung of the ladder: a character- or picture-set-scoped token has
    no project visibility at all, so ``project_ids`` serialises as ``[]``. It
    still reads its own entity — the narrowing must not turn into a refusal."""
    anon, mint = env["anon"], env["mint"]
    char_headers = _bearer(mint("character", env["char_id"]))
    set_headers = _bearer(mint("picture_set", env["set_id"]))

    with _enforcing(env["server"]):
        for site, ids in _char_project_ids(anon, env, char_headers).items():
            assert ids == [], (
                f"a character token has no project visibility; characters.{site} "
                f"leaked {ids}"
            )
        for site, ids in _set_project_ids(anon, env, set_headers).items():
            assert ids == [], (
                f"a set token has no project visibility; picture_sets.{site} "
                f"leaked {ids}"
            )


def _char_payloads(client, env, headers=None, project_label=None):
    """Every character payload that serialises the scalar ``project_id``."""
    kw = {"headers": headers} if headers else {}
    out = {}
    r = client.get(f"{API}/characters/{env['char_id']}", **kw)
    assert r.status_code == 200, r.text
    out["by_id"] = r.json()
    listed = {c["id"]: c for c in client.get(f"{API}/characters", **kw).json()}
    out["list"] = listed[env["char_id"]]
    if project_label is not None:
        r = client.get(f"{API}/projects/{project_label}/characters/SharedChar", **kw)
        assert r.status_code == 200, r.text
        out["by_name"] = r.json()
    return out


def _set_payloads(client, env, headers=None, project_label=None):
    """Every picture-set payload that serialises the scalar ``project_id``,
    including the sort-variant siblings of ``GET /picture_sets/{id}`` which
    build their ``set`` payload on separate return paths."""
    kw = {"headers": headers} if headers else {}
    out = {}
    r = client.get(f"{API}/picture_sets/{env['set_id']}?info=true", **kw)
    assert r.status_code == 200, r.text
    out["info"] = r.json()
    r = client.get(f"{API}/picture_sets/{env['set_id']}", **kw)
    assert r.status_code == 200, r.text
    out["pictures"] = r.json()["set"]
    r = client.get(f"{API}/picture_sets/{env['set_id']}?sort=SMART_SCORE", **kw)
    assert r.status_code == 200, r.text
    out["pictures_smart_sort"] = r.json()["set"]
    r = client.get(
        f"{API}/picture_sets/{env['set_id']}?sort=CHARACTER_LIKENESS"
        f"&reference_character_id={env['char_id']}",
        **kw,
    )
    assert r.status_code == 200, r.text
    out["pictures_likeness_sort"] = r.json()["set"]
    listed = {s["id"]: s for s in client.get(f"{API}/picture_sets", **kw).json()}
    out["list"] = listed[env["set_id"]]
    if project_label is not None:
        r = client.get(f"{API}/projects/{project_label}/picture_sets/SharedSet", **kw)
        assert r.status_code == 200, r.text
        out["by_name"] = r.json()
    return out


def test_scalar_project_id_is_derived_from_the_narrowed_list(env):
    """R1b: the legacy scalar ``project_id`` must never name a project the token
    has no grant for. It is derived from the narrowed ``project_ids`` at every
    serialisation site — the primary project for the owner, the token's own
    project for a project token, ``None`` for an entity-scoped token — never
    read straight off the model."""
    owner, anon, tokens, projects, mint = (
        env["owner"],
        env["anon"],
        env["tokens"],
        env["projects"],
        env["mint"],
    )
    both = sorted([projects["P1"], projects["P2"]])

    for site, payload in {
        **_char_payloads(owner, env, project_label="P1"),
        **_set_payloads(owner, env, project_label="P1"),
    }.items():
        assert payload["project_ids"] == both, f"owner narrowed on {site}"
        assert payload["project_id"] == both[0], (
            f"{site}: the owner's scalar must stay the primary project"
        )

    with _enforcing(env["server"]):
        headers = _bearer(tokens["P2"])
        for site, payload in {
            **_char_payloads(anon, env, headers, project_label="P2"),
            **_set_payloads(anon, env, headers, project_label="P2"),
        }.items():
            assert payload["project_ids"] == [projects["P2"]], site
            assert payload["project_id"] == projects["P2"], (
                f"{site}: scalar project_id named a project the P2 token has "
                f"no grant for ({payload['project_id']})"
            )

        char_headers = _bearer(mint("character", env["char_id"]))
        for site, payload in _char_payloads(anon, env, char_headers).items():
            assert payload["project_id"] is None, (
                f"characters.{site}: scalar leaked to a character token"
            )
        set_headers = _bearer(mint("picture_set", env["set_id"]))
        for site, payload in _set_payloads(anon, env, set_headers).items():
            assert payload["project_id"] is None, (
                f"picture_sets.{site}: scalar leaked to a set token"
            )


# ---------------------------------------------------------------------------
# R1c (issue #708) — the two channels the R1 narrowing did not cover
# ---------------------------------------------------------------------------
#
# R1 narrowed ``project_ids`` / ``project_id`` wherever an entity is serialised.
# Two ways of asking the same question stayed open:
#
# * a payload *keyed* by project id (``POST /projects/membership``) and the two
#   sites that still read the scalar straight off the model
#   (``GET /projects/{id}/picture_sets``, ``GET /characters/{id}/project_id``);
# * the ``project_id`` **filter**, which needs no payload at all — the presence
#   or count of rows answers "does project N hold this?" for a token that is
#   403'd on ``GET /projects/N``. That one is enforced centrally by the authz
#   gate (``enforce_project_filter_scope``), so it covers every route that takes
#   the parameter, including ones added later.
#
# Both directions, as always: the invisible project stays invisible, and the
# token's *own* project keeps working (over-blocking is its own regression).


# Every route that accepts a ``project_id`` filter and is reachable by a
# resource-scoped token. The gate refuses the parameter on all of them; the same
# request without the parameter must keep working.
def _project_filter_routes(env):
    return [
        f"{API}/picture_sets",
        f"{API}/characters",
        f"{API}/pictures",
        f"{API}/pictures/count",
        f"{API}/pictures/stream",
        f"{API}/pictures/stats",
        f"{API}/picture_sets/{env['set_id']}",
        f"{API}/characters/{env['char_id']}/summary",
    ]


def test_membership_payload_project_keys_are_narrowed(env):
    """``POST /projects/membership`` is keyed by project id, so the keys are the
    disclosure. An entity-scoped token gets none of them (and still gets its own
    picture back); a project token gets only its own; the owner keeps everything.

    ``unassigned_picture_ids`` is derived from the *narrowed* mapping — a picture
    filed only under an invisible project must come back as unassigned, never as
    a hole in both lists, which would re-leak what the narrowing removed.
    """
    owner, anon, tokens, projects, mint = (
        env["owner"],
        env["anon"],
        env["tokens"],
        env["projects"],
        env["mint"],
    )
    body = {"picture_ids": [env["pic_a"], env["pic_b"]]}
    both = sorted([projects["P1"], projects["P2"]])

    r = owner.post(f"{API}/projects/membership", json=body)
    assert r.status_code == 200, r.text
    payload = r.json()
    assert sorted(int(k) for k in payload["project_assignments"]) == both, (
        f"the owner must not be narrowed: {payload}"
    )
    for label in ("P1", "P2"):
        assert payload["project_assignments"][str(projects[label])] == [env["pic_a"]]
    assert payload["unassigned_picture_ids"] == [env["pic_b"]]

    with _enforcing(env["server"]):
        r = anon.post(
            f"{API}/projects/membership", json=body, headers=_bearer(tokens["P1"])
        )
        assert r.status_code == 200, r.text
        payload = r.json()
        assert sorted(int(k) for k in payload["project_assignments"]) == [
            projects["P1"]
        ], f"the P1 token learned another project's id: {payload}"
        assert payload["project_assignments"][str(projects["P1"])] == [env["pic_a"]]

        for scope, resource_id in (
            ("picture_set", env["set_id"]),
            ("character", env["char_id"]),
            ("picture", env["pic_a"]),
        ):
            headers = _bearer(mint(scope, resource_id))
            r = anon.post(f"{API}/projects/membership", json=body, headers=headers)
            assert r.status_code == 200, r.text
            payload = r.json()
            assert payload["project_assignments"] == {}, (
                f"a {scope} token has no project visibility but was told "
                f"{payload['project_assignments']}"
            )
            if scope != "character":
                # In-scope pictures must still come back — narrowing the project
                # keys must not turn into refusing the caller's own data.
                assert env["pic_a"] in payload["unassigned_picture_ids"], (
                    f"a {scope} token lost its own picture: {payload}"
                )


def test_project_filter_param_is_refused_without_project_visibility(env):
    """A character- / set- / picture-scoped token may not filter by *any*
    project id — a real one, an unrelated one, a non-existent one, or the
    ``UNASSIGNED`` sentinel. The same 403 for all four, so the refusal itself
    is not an oracle. The unfiltered request must still succeed."""
    anon, projects, mint = env["anon"], env["projects"], env["mint"]
    probes = [
        str(projects["P1"]),
        str(projects["P2"]),
        str(projects["P3"]),
        "UNASSIGNED",
        "99999999",
    ]

    with _enforcing(env["server"]):
        for scope, resource_id in (
            ("picture_set", env["set_id"]),
            ("character", env["char_id"]),
            ("picture", env["pic_a"]),
        ):
            headers = _bearer(mint(scope, resource_id))
            for path in _project_filter_routes(env):
                assert_real_route(env["server"].api, "GET", path)
                for probe in probes:
                    r = anon.get(f"{path}?project_id={probe}", headers=headers)
                    assert r.status_code == 403, (
                        f"{scope} token filtered {path} by project_id={probe} and "
                        f"got {r.status_code}: {r.text[:200]}"
                    )
                # Over-blocking check: without the parameter the route still
                # answers (200 — possibly with an empty, scope-filtered body).
                r = anon.get(path, headers=headers)
                assert r.status_code in (200, 403), r.text
                if path in (
                    f"{API}/picture_sets",
                    f"{API}/pictures",
                    f"{API}/pictures/count",
                ):
                    assert r.status_code == 200, (
                        f"{scope} token was over-blocked on the unfiltered "
                        f"{path}: {r.status_code} {r.text[:200]}"
                    )


def test_project_token_keeps_filtering_by_its_own_project(env):
    """The in-scope direction: a project token filters by its own project on
    every one of those routes exactly as before, and the owner is never
    narrowed — including by ``UNASSIGNED``, which only a scoped token is
    refused."""
    owner, anon, tokens, projects = (
        env["owner"],
        env["anon"],
        env["tokens"],
        env["projects"],
    )

    for path in _project_filter_routes(env):
        assert_real_route(env["server"].api, "GET", path)

    for probe in (str(projects["P1"]), "UNASSIGNED"):
        for path in _project_filter_routes(env):
            r = owner.get(f"{path}?project_id={probe}")
            assert r.status_code == 200, (
                f"the owner must not be restricted: {path}?project_id={probe} "
                f"-> {r.status_code} {r.text[:200]}"
            )

    with _enforcing(env["server"]):
        headers = _bearer(tokens["P1"])
        for path in _project_filter_routes(env):
            r = anon.get(f"{path}?project_id={projects['P1']}", headers=headers)
            assert r.status_code == 200, (
                f"the P1 token was over-blocked on its own project: {path} -> "
                f"{r.status_code} {r.text[:200]}"
            )
        # Its own project's listings still contain the shared entities.
        listed = {
            s["id"]
            for s in anon.get(
                f"{API}/picture_sets?project_id={projects['P1']}", headers=headers
            ).json()
        }
        assert env["set_id"] in listed, f"the shared set vanished: {listed}"
        listed = {
            c["id"]
            for c in anon.get(
                f"{API}/characters?project_id={projects['P1']}", headers=headers
            ).json()
        }
        assert env["char_id"] in listed, f"the shared character vanished: {listed}"

        # A *secondary* project it does not hold a token for is still refused,
        # even though the entity itself is shared with it.
        for path in _project_filter_routes(env):
            r = anon.get(f"{path}?project_id={projects['P2']}", headers=headers)
            assert r.status_code == 403, (
                f"the P1 token read {path} filtered by P2 and got "
                f"{r.status_code}: {r.text[:200]}"
            )


def test_project_set_listing_scalar_is_narrowed(env):
    """``GET /projects/{id_or_name}/picture_sets`` serialised the set's *primary*
    project id, which for a set shared by P1+P2 is P1 — handed to a P2 token
    listing its own project. The scalar comes from the narrowed list here too."""
    owner, anon, tokens, projects = (
        env["owner"],
        env["anon"],
        env["tokens"],
        env["projects"],
    )
    both = sorted([projects["P1"], projects["P2"]])

    r = owner.get(f"{API}/projects/{projects['P2']}/picture_sets")
    assert r.status_code == 200, r.text
    listed = {s["id"]: s for s in r.json()}
    assert env["set_id"] in listed, "the shared set must be listed under P2"
    assert listed[env["set_id"]]["project_ids"] == both
    assert listed[env["set_id"]]["project_id"] == both[0], (
        "the owner's scalar must stay the primary project"
    )

    with _enforcing(env["server"]):
        r = anon.get(
            f"{API}/projects/{projects['P2']}/picture_sets",
            headers=_bearer(tokens["P2"]),
        )
        assert r.status_code == 200, r.text
        listed = {s["id"]: s for s in r.json()}
        assert env["set_id"] in listed, (
            "the P2 token must still see the set it shares (over-blocking is its "
            "own regression)"
        )
        assert listed[env["set_id"]]["project_id"] == projects["P2"], (
            f"the P2 token was told the set's primary project: {listed[env['set_id']]}"
        )
        assert listed[env["set_id"]]["project_ids"] == [projects["P2"]]


def test_character_project_id_field_route_is_narrowed(env):
    """``GET /characters/{id}/{field}`` returns any column by name, including the
    scalar ``project_id`` — the one character serialisation R1 did not reach."""
    owner, anon, tokens, projects, mint = (
        env["owner"],
        env["anon"],
        env["tokens"],
        env["projects"],
        env["mint"],
    )
    path = f"{API}/characters/{env['char_id']}/project_id"
    assert_real_route(env["server"].api, "GET", path)

    r = owner.get(path)
    assert r.status_code == 200, r.text
    assert r.json()["project_id"] == sorted([projects["P1"], projects["P2"]])[0]

    with _enforcing(env["server"]):
        r = anon.get(path, headers=_bearer(tokens["P2"]))
        assert r.status_code == 200, r.text
        assert r.json()["project_id"] == projects["P2"], (
            f"the P2 token was told the character's primary project: {r.json()}"
        )

        r = anon.get(path, headers=_bearer(mint("character", env["char_id"])))
        assert r.status_code == 200, (
            f"a character token must still read its own character: {r.text}"
        )
        assert r.json()["project_id"] is None, (
            f"a character token has no project visibility: {r.json()}"
        )


# ---------------------------------------------------------------------------
# R1e (issue #719): the *picture's* own scalar `project_id`
# ---------------------------------------------------------------------------
#
# R1b narrowed the scalar on characters and picture sets. A picture carries the
# same column, and `Picture.metadata_fields()` is "every scalar column minus the
# blobs", so it rides into every payload built from that projection. None of the
# picture response models filters it back out: they all set `extra="allow"`, so
# the handler's own narrowing is the only thing between the column and the wire.
#
# The reproduction has a trap that made the first probe of this come back clean:
# the set/character PATCHes in the fixture write `PictureProjectMember` rows but
# leave `Picture.project_id` NULL, so every site returns None whether it narrows
# or not. A real membership write (`PATCH /pictures/project`) maintains the
# scalar as a denormalised primary. Both tests below backfill it first and assert
# the backfill landed, so a regression cannot hide behind a NULL column.

# Picture-row sites that answer deterministically in this fixture. Asserted as a
# required subset, so a site that silently stops answering cannot quietly drop
# out of coverage.
_PICTURE_SCALAR_SITES = {
    "metadata",
    "field_route",
    "set_pictures",
    "stack_pictures_full",
}

# The two sort variants of ``GET /picture_sets/{id}`` build their picture rows on
# separate return paths, and each one narrows separately. They return nothing on
# the fixture's raw uploads, so they are probed opportunistically here and pinned
# properly, with the inputs each branch needs, by
# ``test_picture_set_sort_variants_narrow_their_rows`` below.
_PICTURE_SCALAR_OPPORTUNISTIC_SITES = {
    "set_pictures_smart_sort",
    "set_pictures_likeness_sort",
}


def _picture_scalar_sites(client, env, picture_id, headers=None, stack_id=None):
    """``project_id`` for *picture_id* on every picture-row route the caller can
    reach. A site whose route refuses the token, or which does not list the
    picture, is omitted, so every caller asserts that the deterministic sites are
    all present before reading their values."""
    kw = {"headers": headers} if headers else {}
    out = {}

    r = client.get(f"{API}/pictures/{picture_id}/metadata", **kw)
    if r.status_code == 200:
        body = r.json()
        assert "project_id" in body, "the metadata payload must keep the key"
        out["metadata"] = body["project_id"]

    r = client.get(f"{API}/pictures/{picture_id}/project_id", **kw)
    if r.status_code == 200:
        out["field_route"] = r.json()["project_id"]

    def pick(rows, label):
        row = next((item for item in rows if item.get("id") == picture_id), None)
        if row is None:
            return
        assert "project_id" in row, f"{label}: the row lost its project_id key"
        out[label] = row["project_id"]

    r = client.get(f"{API}/picture_sets/{env['set_id']}", **kw)
    if r.status_code == 200:
        pick(r.json()["pictures"], "set_pictures")
    r = client.get(f"{API}/picture_sets/{env['set_id']}?sort=SMART_SCORE", **kw)
    if r.status_code == 200:
        pick(r.json()["pictures"], "set_pictures_smart_sort")
    r = client.get(
        f"{API}/picture_sets/{env['set_id']}?sort=CHARACTER_LIKENESS"
        f"&reference_character_id={env['char_id']}",
        **kw,
    )
    if r.status_code == 200:
        pick(r.json()["pictures"], "set_pictures_likeness_sort")

    if stack_id is not None:
        r = client.get(f"{API}/stacks/{stack_id}/pictures?fields=full", **kw)
        if r.status_code == 200:
            pick(r.json(), "stack_pictures_full")
    return out


def test_picture_scalar_project_id_is_narrowed(env):
    """R1e: a picture's scalar ``project_id`` is derived from its *narrowed*
    membership at every serialisation site: the stored primary for the owner,
    the token's own project for a project token, ``None`` for an entity-scoped
    token that may see no project at all."""
    owner, anon, tokens, projects, mint = (
        env["owner"],
        env["anon"],
        env["tokens"],
        env["projects"],
        env["mint"],
    )
    pic_a, pic_b = env["pic_a"], env["pic_b"]

    # pic_b joins the shared set (and so both projects); stacking the two gives
    # the stack route a page it can serve to every token under test.
    r = owner.post(f"{API}/picture_sets/{env['set_id']}/members/{pic_b}")
    assert r.status_code in (200, 201), r.text
    r = owner.post(f"{API}/stacks", json={"picture_ids": [pic_a, pic_b]})
    assert r.status_code in (200, 201), r.text
    stack_id = r.json()["id"]

    # Backfill the denormalised scalar the way a real membership write does.
    r = owner.patch(
        f"{API}/pictures/project",
        json={
            "picture_ids": [pic_a, pic_b],
            "project_id": projects["P1"],
            "mode": "add",
        },
    )
    assert r.status_code == 200, r.text

    for path in (
        f"{API}/pictures/{pic_a}/metadata",
        f"{API}/pictures/{pic_a}/project_id",
        f"{API}/stacks/{stack_id}/pictures",
    ):
        assert_real_route(env["server"].api, "GET", path)

    owner_sites = _picture_scalar_sites(owner, env, pic_a, stack_id=stack_id)
    assert _PICTURE_SCALAR_SITES <= set(owner_sites), (
        f"a picture-row site stopped answering, so its narrowing is untested: "
        f"{sorted(owner_sites)}"
    )
    # State the universe explicitly: anything not answered here must be one of
    # the two known-empty sort variants, so a third silent gap fails the build.
    assert (_PICTURE_SCALAR_SITES | _PICTURE_SCALAR_OPPORTUNISTIC_SITES) - set(
        owner_sites
    ) <= _PICTURE_SCALAR_OPPORTUNISTIC_SITES, sorted(owner_sites)
    for site, value in owner_sites.items():
        assert value == projects["P1"], (
            f"{site}: the owner must keep the stored primary project, and the "
            f"scalar must actually be backfilled; got {value}"
        )

    with _enforcing(env["server"]):
        for label in ("P1", "P2"):
            sites = _picture_scalar_sites(
                anon, env, pic_a, _bearer(tokens[label]), stack_id
            )
            assert _PICTURE_SCALAR_SITES <= set(sites), (
                f"the {label} token must still read every picture-row site "
                f"(over-blocking is its own regression): {sorted(sites)}"
            )
            for site, value in sites.items():
                assert value == projects[label], (
                    f"{site}: the {label} token was handed project id {value}, "
                    f"which it is 403'd on by name"
                )

        set_sites = _picture_scalar_sites(
            anon, env, pic_a, _bearer(mint("picture_set", env["set_id"])), stack_id
        )
        assert _PICTURE_SCALAR_SITES <= set(set_sites), sorted(set_sites)
        for site, value in set_sites.items():
            assert value is None, (
                f"{site}: a set-scoped token has no project visibility, but was "
                f"handed project id {value}"
            )

        pic_sites = _picture_scalar_sites(
            anon, env, pic_a, _bearer(mint("picture", pic_a))
        )
        assert {"metadata", "field_route"} <= set(pic_sites), (
            f"a picture token must still read its own picture: {sorted(pic_sites)}"
        )
        for site, value in pic_sites.items():
            assert value is None, (
                f"{site}: a picture-scoped token has no project visibility, but "
                f"was handed project id {value}"
            )


def test_picture_search_and_likeness_group_rows_are_narrowed(env):
    """R1e siblings: the two picture-row payloads that need seeded data before
    they return anything: semantic search and the likeness groups."""
    owner, anon, tokens, projects, mint = (
        env["owner"],
        env["anon"],
        env["tokens"],
        env["projects"],
        env["mint"],
    )
    pic_a, pic_b = env["pic_a"], env["pic_b"]

    r = owner.post(f"{API}/picture_sets/{env['set_id']}/members/{pic_b}")
    assert r.status_code in (200, 201), r.text
    r = owner.patch(
        f"{API}/pictures/project",
        json={
            "picture_ids": [pic_a, pic_b],
            "project_id": projects["P1"],
            "mode": "add",
        },
    )
    assert r.status_code == 200, r.text
    assert (
        owner.get(f"{API}/pictures/{pic_a}/project_id").json()["project_id"]
        == projects["P1"]
    ), "the scalar must be backfilled, or every assertion below is vacuous"

    def seed(session):
        # The reset already emptied both tables; re-emptying them inside the
        # seed transaction keeps the invariant local to the code that depends on
        # it. There is deliberately no wait for the likeness pipeline to
        # quiesce first: the finders that write this table are detached for the
        # module's lifetime (`_detach_volatile_finders`), so there is nothing
        # left to race with — and polling for quiescence is far slower than the
        # per-test Server it replaced.
        session.exec(delete(PictureLikeness))
        session.exec(delete(PictureLikenessQueue))
        low, high = sorted([pic_a, pic_b])
        session.add(
            PictureLikeness(
                picture_id_a=low, picture_id_b=high, likeness=0.99, metric="test"
            )
        )
        session.commit()

    env["server"].vault.db.run_task(seed)

    for path in (f"{API}/pictures/likeness-groups", f"{API}/pictures/search"):
        assert_real_route(env["server"].api, "GET", path)

    def groups(client, headers=None):
        kw = {"headers": headers} if headers else {}
        r = client.get(f"{API}/pictures/likeness-groups?threshold=0.9", **kw)
        assert r.status_code == 200, r.text
        rows = [item for item in r.json() if item.get("id") == pic_a]
        assert rows, f"picture {pic_a} must be in a likeness group: {r.json()}"
        assert "project_id" in rows[0], "the group row lost its project_id key"
        return rows[0]["project_id"]

    def search(client, headers=None):
        kw = {"headers": headers} if headers else {}
        r = client.get(f"{API}/pictures/search?query=picture&threshold=0.0", **kw)
        assert r.status_code == 200, r.text
        rows = [item for item in r.json() if item.get("id") == pic_a]
        assert rows, f"semantic search must return picture {pic_a}: {r.json()}"
        assert "project_id" in rows[0], "the search row lost its project_id key"
        return rows[0]["project_id"]

    assert groups(owner) == projects["P1"]
    assert search(owner) == projects["P1"]

    with _enforcing(env["server"]):
        for label in ("P1", "P2"):
            headers = _bearer(tokens[label])
            assert groups(anon, headers) == projects[label], (
                f"likeness-groups handed the {label} token a project id it is "
                f"403'd on by name"
            )
            assert search(anon, headers) == projects[label], (
                f"search handed the {label} token a project id it is 403'd on by name"
            )

        set_headers = _bearer(mint("picture_set", env["set_id"]))
        assert groups(anon, set_headers) is None
        assert search(anon, set_headers) is None


def _wait_faces_extracted(server, picture_ids, timeout_s=60.0):
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


def _seed_set_sort_inputs(server, picture_ids, character_id):
    """Give the two sort branches of ``GET /picture_sets/{id}`` rows to return.

    Neither branch emits a picture on the fixture's raw uploads, and each is
    blocked by a different missing input:

    * ``sort=SMART_SCORE`` scores only pictures whose ``image_embedding`` is
      non-NULL (``scoring/smart_score.py``); everything else is reported as
      unscored.
    * ``sort=CHARACTER_LIKENESS`` needs a *reference* face for the reference
      character, which is a ``Face`` carrying ``features`` on a non-deleted
      picture scored 5 (``scoring/character_likeness.py``), plus a candidate
      face on the set's members.

    Both are written straight to the database rather than computed, so the test
    does not depend on an embedding model or the face detector producing
    anything under the CPU test profile. Two properties keep the seed alive
    against the background pipeline, and both were established by watching it
    delete the face mid-test:

    * it runs only once extraction has settled (:func:`_wait_faces_extracted`),
      and uses a ``face_index`` far outside the detector's range, so a late pass
      cannot collide with the ``(picture, frame, face)`` unique constraint;
    * it copies ``model_pack`` off a row the extractor just wrote. A face that
      carries ``features`` with a *different* (or NULL) pack is exactly what
      ``MissingFaceModelRefreshFinder`` selects, and ``FaceModelRefreshTask``
      then deletes any seeded row its re-detection does not match. Adopting the
      live pack keeps the row out of that sweep entirely.
    """
    _wait_faces_extracted(server, picture_ids)

    def _do(session):
        vector = np.ones(512, dtype=np.float32).tobytes()
        model_pack = session.exec(
            select(Face.model_pack).where(Face.model_pack.is_not(None)).limit(1)
        ).first()
        assert model_pack, (
            "no extracted face carries a model_pack, so the seeded face would be "
            "swept as stale-pack and deleted mid-test"
        )
        for picture_id in picture_ids:
            pic = session.get(Picture, int(picture_id))
            assert pic is not None, f"picture {picture_id} vanished from the fixture"
            pic.image_embedding = vector
            pic.score = 5
            session.add(pic)
            session.add(
                Face(
                    picture_id=int(picture_id),
                    frame_index=0,
                    face_index=901,
                    character_id=int(character_id),
                    features=vector,
                    model_pack=model_pack,
                )
            )
        session.commit()

    server.vault.db.run_task(_do)

    def _readback(session):
        return session.exec(
            select(Face.picture_id, Face.character_id)
            .where(Face.features.is_not(None))
            .where(Face.character_id == int(character_id))
        ).all(), session.exec(
            select(Picture.id, Picture.score).where(
                Picture.image_embedding.is_not(None)
            )
        ).all()

    # These two readbacks were the guard against a background stage rewriting
    # the seed mid-test. `_detach_volatile_finders` now removes those stages for
    # the module's lifetime, so they can no longer fail on that account; they are
    # kept as a plain "the write landed" check.
    faces, scored = server.vault.db.run_immediate_read_task(_readback)
    assert len(faces) >= len(picture_ids), (
        f"the seeded reference faces did not survive; a background stage most "
        f"likely rewrote them. faces={faces}"
    )
    assert len(scored) >= len(picture_ids), (
        f"the seeded embeddings did not survive. scored={scored}"
    )


def _set_sort_row_project_id(client, env, picture_id, query, marker, headers=None):
    """``project_id`` for *picture_id* on one sort variant of the set contents.

    Asserts the *marker* key that only that branch adds, because the default
    picture path returns the same row shape without it: a sort that silently fell
    through would otherwise satisfy every other assertion here while leaving the
    branch under test unexecuted.
    """
    kw = {"headers": headers} if headers else {}
    r = client.get(f"{API}/picture_sets/{env['set_id']}?{query}", **kw)
    assert r.status_code == 200, r.text
    rows = [row for row in r.json()["pictures"] if row.get("id") == picture_id]
    assert rows, f"{query}: picture {picture_id} must be returned, got {r.json()}"
    row = rows[0]
    assert marker in row, (
        f"{query}: the row carries no {marker!r} key, so this request did not "
        f"take the sort branch it is meant to pin: {sorted(row)}"
    )
    assert "project_id" in row, f"{query}: the row lost its project_id key"
    return row["project_id"]


def test_picture_set_sort_variants_narrow_their_rows(env):
    """R1e: ``sort=SMART_SCORE`` and ``sort=CHARACTER_LIKENESS`` build their
    picture rows on their own return paths in ``get_picture_set``, each with its
    own narrowing call. Deleting either one leaves the rest of this file green,
    so both branches are pinned here with the inputs they need to emit a row."""
    owner, anon, tokens, projects, mint = (
        env["owner"],
        env["anon"],
        env["tokens"],
        env["projects"],
        env["mint"],
    )
    pic_a, pic_b = env["pic_a"], env["pic_b"]

    r = owner.post(f"{API}/picture_sets/{env['set_id']}/members/{pic_b}")
    assert r.status_code in (200, 201), r.text
    r = owner.patch(
        f"{API}/pictures/project",
        json={
            "picture_ids": [pic_a, pic_b],
            "project_id": projects["P1"],
            "mode": "add",
        },
    )
    assert r.status_code == 200, r.text
    assert (
        owner.get(f"{API}/pictures/{pic_a}/project_id").json()["project_id"]
        == projects["P1"]
    ), "the scalar must be backfilled, or every assertion below is vacuous"

    _seed_set_sort_inputs(env["server"], [pic_a, pic_b], env["char_id"])

    variants = (
        ("sort=SMART_SCORE", "smartScore"),
        (
            f"sort=CHARACTER_LIKENESS&reference_character_id={env['char_id']}",
            "character_likeness",
        ),
    )

    for query, marker in variants:
        assert (
            _set_sort_row_project_id(owner, env, pic_a, query, marker) == projects["P1"]
        ), f"{query}: the owner must keep the stored primary project"

    with _enforcing(env["server"]):
        for query, marker in variants:
            for label in ("P1", "P2"):
                value = _set_sort_row_project_id(
                    anon, env, pic_a, query, marker, _bearer(tokens[label])
                )
                assert value == projects[label], (
                    f"{query}: the {label} token was handed project id {value}, "
                    f"which it is 403'd on by name"
                )

            value = _set_sort_row_project_id(
                anon,
                env,
                pic_a,
                query,
                marker,
                _bearer(mint("picture_set", env["set_id"])),
            )
            assert value is None, (
                f"{query}: a set-scoped token has no project visibility, but was "
                f"handed project id {value}"
            )


# ---------------------------------------------------------------------------
# R1d (issue #708, sign-off condition 2) — the project named in a PATH segment
# ---------------------------------------------------------------------------
#
# ``enforce_project_filter_scope`` reads ``request.query_params``, so it cannot
# see a project named in the URL path. The four name-derived routes (§16.1's
# residual ``resolved_inline`` exception) do exactly that, and each resolved the
# project *before* any scope check ran. Their ordinary error branches then
# answered from the project space:
#
#     GET /projects/P1/picture_sets/SharedSet        -> 200
#     GET /projects/P3/picture_sets/SharedSet        -> 404 "Picture set not found"
#     GET /projects/Nope/picture_sets/SharedSet      -> 404 "Project not found"
#     GET /projects/{existing}                       -> 403
#     GET /projects/{missing}                        -> 404
#
# Three (respectively two) distinguishable answers are a project-existence and
# project-membership oracle for a token that ``GET /projects/N`` deliberately
# 403s — the same disclosure R1/R1c close, arriving through a path segment.
# ``enforce_project_path_scope`` now runs on the resolved id first, so every
# refusal is byte-identical.
#
# The over-blocking direction matters just as much here: a token that CAN see
# the project must still reach these routes, and the owner's 404s must survive.

# (method, path template) of the four routes, with the concrete probes used
# below. Kept as one list so a fifth name-derived route is added in one place.
_PROJECT_PATH_ROUTES = (
    "/projects/{project}/picture_sets/SharedSet",
    "/projects/{project}/characters/SharedChar",
    "/projects/{project}",
    "/projects/{project}/picture_sets",
)


def _path_probes(env):
    """Return the (label, project path segment) probes for the path routes.

    Deliberately three shapes with the SAME expected answer for a token that
    cannot see the project: a project that exists and holds the entity, a
    project that exists and does not, and a project that does not exist at all
    (by name and by numeric id). If any two of them differ, the route is an
    oracle again.
    """
    return [
        ("exists, holds the entity", str(env["projects"]["P1"])),
        ("exists, holds the entity (by name)", "P1"),
        ("exists, does not hold it", str(env["projects"]["P3"])),
        ("exists, does not hold it (by name)", "P3"),
        ("does not exist (numeric)", "99999999"),
        ("does not exist (name)", "NoSuchProjectHere"),
    ]


def test_project_path_routes_are_not_an_existence_oracle(env):
    """Out-of-scope direction: for a token with no project visibility at all,
    every one of the four path routes answers identically for a project that
    holds its entity, a project that does not, and a project that does not
    exist. Status *and* body, because the body used to carry the distinction
    ("Picture set not found" vs "Project not found")."""
    anon, mint = env["anon"], env["mint"]

    with _enforcing(env["server"]):
        for scope, resource_id in (
            ("picture_set", env["set_id"]),
            ("character", env["char_id"]),
            ("picture", env["pic_a"]),
        ):
            headers = _bearer(mint(scope, resource_id))
            for template in _PROJECT_PATH_ROUTES:
                answers = {}
                for label, segment in _path_probes(env):
                    path = f"{API}{template.format(project=segment)}"
                    assert_real_route(env["server"].api, "GET", path)
                    r = anon.get(path, headers=headers)
                    assert r.status_code == 403, (
                        f"{scope} token on {path} got {r.status_code}; a token "
                        f"with no project visibility must be refused: {r.text[:200]}"
                    )
                    answers[label] = (r.status_code, r.text)
                distinct = set(answers.values())
                assert len(distinct) == 1, (
                    f"{scope} token can tell the probes apart on {template} — "
                    f"that is the oracle: "
                    + "; ".join(
                        f"{k} -> {v[0]} {v[1][:80]}" for k, v in answers.items()
                    )
                )


def test_project_token_is_not_told_which_other_projects_exist(env):
    """A *project* token has visibility of exactly one project, so the same
    indistinguishability must hold for every project that is not its own —
    including one that does not exist."""
    anon, tokens = env["anon"], env["tokens"]

    with _enforcing(env["server"]):
        headers = _bearer(tokens["P1"])
        for template in _PROJECT_PATH_ROUTES:
            answers = {}
            for label, segment in (
                ("another project (id)", str(env["projects"]["P2"])),
                ("another project (name)", "P2"),
                ("unrelated project (id)", str(env["projects"]["P3"])),
                ("missing project (id)", "99999999"),
                ("missing project (name)", "NoSuchProjectHere"),
            ):
                path = f"{API}{template.format(project=segment)}"
                # Same reason as the sibling test above: the uniform 403 these
                # routes answer with is also what a nonexistent path would
                # produce, so the route has to be proven real first.
                assert_real_route(env["server"].api, "GET", path)
                r = anon.get(path, headers=headers)
                assert r.status_code == 403, (
                    f"P1 token on {path} got {r.status_code}: {r.text[:200]}"
                )
                answers[label] = (r.status_code, r.text)
            assert len(set(answers.values())) == 1, (
                f"the P1 token can tell another project from a missing one on "
                f"{template}: "
                + "; ".join(f"{k} -> {v[0]} {v[1][:80]}" for k, v in answers.items())
            )


def test_project_path_routes_still_serve_a_token_that_sees_the_project(env):
    """In-scope direction — over-blocking is its own regression. A project token
    still reads its own project, its own project's set listing, and both
    name-derived routes under its own project's name."""
    anon, tokens, projects = env["anon"], env["tokens"], env["projects"]

    with _enforcing(env["server"]):
        for label in ("P1", "P2"):
            headers = _bearer(tokens[label])

            path = f"{API}/projects/{projects[label]}"
            r = anon.get(path, headers=headers)
            assert r.status_code == 200, f"{label} token lost its own project: {r.text}"
            assert r.json()["id"] == projects[label]

            path = f"{API}/projects/{label}"
            r = anon.get(path, headers=headers)
            assert r.status_code == 200, (
                f"{label} token lost its own project by name: {r.text}"
            )

            path = f"{API}/projects/{projects[label]}/picture_sets"
            r = anon.get(path, headers=headers)
            assert r.status_code == 200, r.text
            assert env["set_id"] in {s["id"] for s in r.json()}, (
                f"{label} token lost the shared set from its own project listing"
            )

            path = f"{API}/projects/{label}/picture_sets/SharedSet"
            r = anon.get(path, headers=headers)
            assert r.status_code == 200, (
                f"{label} token lost the by-name set route: {r.text}"
            )
            assert r.json()["id"] == env["set_id"]

            path = f"{API}/projects/{label}/characters/SharedChar"
            r = anon.get(path, headers=headers)
            assert r.status_code == 200, (
                f"{label} token lost the by-name character route: {r.text}"
            )
            assert r.json()["id"] == env["char_id"]


def test_owner_keeps_the_404s_on_the_project_path_routes(env):
    """The uniform 403 is for *scoped* tokens only. The owner is unrestricted, so
    the routes keep their ordinary, informative 404s — turning those into 403s
    for everyone would be a usability regression, not a fix."""
    owner = env["owner"]

    with _enforcing(env["server"]):
        r = owner.get(f"{API}/projects/P1/picture_sets/SharedSet")
        assert r.status_code == 200, r.text

        r = owner.get(f"{API}/projects/P3/picture_sets/SharedSet")
        assert r.status_code == 404 and "Picture set not found" in r.text, r.text

        r = owner.get(f"{API}/projects/NoSuchProjectHere/picture_sets/SharedSet")
        assert r.status_code == 404 and "Project not found" in r.text, r.text

        r = owner.get(f"{API}/projects/P3/characters/SharedChar")
        assert r.status_code == 404 and "Character not found" in r.text, r.text

        r = owner.get(f"{API}/projects/NoSuchProjectHere")
        assert r.status_code == 404 and "Project not found" in r.text, r.text

        r = owner.get(f"{API}/projects/99999999/picture_sets")
        assert r.status_code == 404 and "Project not found" in r.text, r.text


# ---------------------------------------------------------------------------
# R2 — a picture added to an already-multi-project entity joins *every* project
# ---------------------------------------------------------------------------
#
# Six write paths used to read the scalar primary FK to decide which
# ``PictureProjectMember`` row to create, so a picture added *after* the entity
# went multi-project silently joined the primary project only: the secondary
# project's token was 403'd and the owner's own ``?project_id=`` listing omitted
# it. That is an under-grant, never a leak — but it is the feature's headline case
# and invisible to the operator. Each path is pinned in both directions.


def _assert_picture_reaches_both_projects(env, picture_id, where):
    """The picture is anchored in P1 *and* P2 — and still not in P3."""
    owner, anon, tokens, projects = (
        env["owner"],
        env["anon"],
        env["tokens"],
        env["projects"],
    )
    # The P3 leg below asserts a 403, which a nonexistent path answers with
    # identically, so the route is proven real before it is trusted.
    assert_real_route(env["server"].api, "GET", f"{API}/pictures/{picture_id}/metadata")
    for label in ("P1", "P2"):
        r = owner.get(f"{API}/pictures?project_id={projects[label]}")
        assert r.status_code == 200, r.text
        ids = {p["id"] for p in r.json()}
        assert picture_id in ids, (
            f"{where}: the owner's {label} listing must contain picture "
            f"{picture_id}; got {sorted(ids)}"
        )
    ids = {
        p["id"] for p in owner.get(f"{API}/pictures?project_id={projects['P3']}").json()
    }
    assert picture_id not in ids, (
        f"{where}: an unrelated project must not gain the picture"
    )

    with _enforcing(env["server"]):
        for label in ("P1", "P2"):
            r = anon.get(
                f"{API}/pictures/{picture_id}/metadata", headers=_bearer(tokens[label])
            )
            assert r.status_code == 200, (
                f"{where}: the {label} token must reach the picture; got "
                f"{r.status_code}: {r.text}"
            )
        r = anon.get(
            f"{API}/pictures/{picture_id}/metadata", headers=_bearer(tokens["P3"])
        )
        assert r.status_code == 403, f"{where}: unrelated project must 403: {r.text}"


def test_add_member_to_shared_set_joins_every_project(env):
    """``POST /picture_sets/{id}/members/{picture_id}`` — the reviewer's original
    reproduction: a picture added *after* the set became P1+P2."""
    r = env["owner"].post(f"{API}/picture_sets/{env['set_id']}/members/{env['pic_b']}")
    assert r.status_code in (200, 201), r.text
    _assert_picture_reaches_both_projects(
        env, env["pic_b"], "POST /picture_sets/{id}/members/{picture_id}"
    )


def test_bulk_add_to_shared_set_joins_every_project(env):
    """``POST /picture_sets/{id}/members`` (bulk add), same semantics."""
    r = env["owner"].post(
        f"{API}/picture_sets/{env['set_id']}/members",
        json={"picture_ids": [env["pic_b"]]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["added"] >= 1, r.text
    _assert_picture_reaches_both_projects(
        env, env["pic_b"], "POST /picture_sets/{id}/members"
    )


def test_bulk_replace_members_joins_every_project(env):
    """``PUT /picture_sets/{id}/members`` (replace) rebuilds the whole member
    list, so every member must be re-anchored in every project."""
    r = env["owner"].put(
        f"{API}/picture_sets/{env['set_id']}/members",
        json={"picture_ids": [env["pic_a"], env["pic_b"]]},
    )
    assert r.status_code == 200, r.text
    for pic_id in (env["pic_a"], env["pic_b"]):
        _assert_picture_reaches_both_projects(
            env, pic_id, "PUT /picture_sets/{id}/members"
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


def test_face_assignment_to_shared_character_joins_every_project(env):
    """``POST /characters/{id}/faces`` — the character twin of the set paths."""
    face_id = _make_face(env["server"], env["pic_b"])
    r = env["owner"].post(
        f"{API}/characters/{env['char_id']}/faces", json={"face_ids": [face_id]}
    )
    assert r.status_code == 200, r.text
    _assert_picture_reaches_both_projects(
        env, env["pic_b"], "POST /characters/{id}/faces"
    )


def _png_bytes(filename: str) -> bytes:
    """A PNG whose pixels are derived from *filename*.

    Content-distinct per caller on purpose: the imported pictures outlive the
    test that made them (the shared library keeps its picture rows so no finder
    is left claiming an id SQLite would hand to a different row), and two
    byte-identical uploads would be deduplicated rather than imported.
    """
    seed = sum(filename.encode())
    buf = io.BytesIO()
    Image.new("RGB", (48, 48), color=(seed % 256, (seed * 7) % 256, 200)).save(
        buf, format="PNG"
    )
    return buf.getvalue()


def _staged_import(env, open_body, filename, timeout_s=60) -> int:
    """Run one staging import (open → stream → commit → wait) and return the id
    of the picture it created."""
    client = env["owner"]
    before = {p["id"] for p in client.get(f"{API}/pictures").json()}
    r = client.post(f"{API}/pictures/import/staging", json=open_body)
    assert r.status_code == 200, r.text
    staging_id = r.json()["staging_id"]
    r = client.post(
        f"{API}/pictures/import/staging/{staging_id}/files",
        files=[("file", (filename, _png_bytes(filename), "image/png"))],
    )
    assert r.status_code == 200, r.text
    r = client.post(f"{API}/pictures/import/staging/{staging_id}/commit")
    assert r.status_code == 200, r.text
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        last = client.get(f"{API}/pictures/import/staging/{staging_id}/status").json()
        if last["stage"] in ("completed", "failed"):
            assert last["stage"] == "completed", last
            fresh = {p["id"] for p in client.get(f"{API}/pictures").json()} - before
            assert len(fresh) == 1, (
                f"expected exactly one newly imported picture, got {sorted(fresh)}"
            )
            return fresh.pop()
        time.sleep(0.1)
    raise AssertionError(f"staging {staging_id} never finished: {last}")


def test_import_into_shared_set_joins_every_project(env):
    """``PictureImportTask._apply_set`` — the drop-target import path must read the
    same membership as the route it mirrors."""
    picture_id = _staged_import(env, {"set_id": env["set_id"]}, "import-into-set.png")
    _assert_picture_reaches_both_projects(
        env, picture_id, "import with set_id drop target"
    )


def test_import_into_shared_character_joins_every_project(env):
    """``PictureImportTask._apply_character`` — the character drop target."""
    picture_id = _staged_import(
        env, {"character_id": env["char_id"]}, "import-into-char.png"
    )
    _assert_picture_reaches_both_projects(
        env, picture_id, "import with character_id drop target"
    )
