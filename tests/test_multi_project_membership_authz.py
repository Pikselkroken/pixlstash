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

import pytest
from PIL import Image
from starlette.testclient import TestClient

from pixlstash.db_models import Face
from pixlstash.server import Server
from tests.authz_guard import (  # noqa: F401
    assert_real_route,
    no_spa_fallback,
    resolves_to_real_route,
)
from tests.utils import upload_pictures_and_wait

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


@pytest.fixture
def env():
    """Live server with 3 projects, a set and a character shared by P1+P2, and a
    project-scoped READ token for each project."""
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
        st = upload_pictures_and_wait(client, files, timeout_s=30)
        assert st["status"] == "completed", st
        pic_ids = [p["id"] for p in client.get(f"{API}/pictures").json()]
        assert len(pic_ids) >= 2
        pic_a, pic_b = pic_ids[0], pic_ids[1]

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
            json={
                "name": "SharedChar",
                "project_ids": [projects["P1"], projects["P2"]],
            },
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

        yield {
            "server": server,
            "owner": client,
            "anon": anon,
            "pic_a": pic_a,
            "pic_b": pic_b,
            "projects": projects,
            "set_id": set_id,
            "char_id": char_id,
            "p1_only_set_id": p1_only_set_id,
            "p1_only_char_id": p1_only_char_id,
            "tokens": {label: mint("project", pid) for label, pid in projects.items()},
            # Exposed so a test can mint a character- / set-scoped token too: the
            # `project_ids` narrowing (R1) has a different rung for those.
            "mint": mint,
        }
    finally:
        server.__exit__(None, None, None)
        temp_dir.cleanup()
        gc.collect()


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


def _png_bytes(color=(11, 99, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (48, 48), color=color).save(buf, format="PNG")
    return buf.getvalue()


def _staged_import(client, open_body, filename, timeout_s=60) -> None:
    """Run one staging import (open → stream → commit → wait) to completion."""
    r = client.post(f"{API}/pictures/import/staging", json=open_body)
    assert r.status_code == 200, r.text
    staging_id = r.json()["staging_id"]
    r = client.post(
        f"{API}/pictures/import/staging/{staging_id}/files",
        files=[("file", (filename, _png_bytes(), "image/png"))],
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
            return
        time.sleep(0.1)
    raise AssertionError(f"staging {staging_id} never finished: {last}")


def _imported_picture_id(env):
    """The one picture id that is not part of the fixture's two uploads."""
    ids = {p["id"] for p in env["owner"].get(f"{API}/pictures").json()}
    fresh = ids - {env["pic_a"], env["pic_b"]}
    assert len(fresh) == 1, f"expected exactly one newly imported picture, got {fresh}"
    return fresh.pop()


def test_import_into_shared_set_joins_every_project(env):
    """``PictureImportTask._apply_set`` — the drop-target import path must read the
    same membership as the route it mirrors."""
    _staged_import(env["owner"], {"set_id": env["set_id"]}, "import-into-set.png")
    _assert_picture_reaches_both_projects(
        env, _imported_picture_id(env), "import with set_id drop target"
    )


def test_import_into_shared_character_joins_every_project(env):
    """``PictureImportTask._apply_character`` — the character drop target."""
    _staged_import(
        env["owner"], {"character_id": env["char_id"]}, "import-into-char.png"
    )
    _assert_picture_reaches_both_projects(
        env, _imported_picture_id(env), "import with character_id drop target"
    )
