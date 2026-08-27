"""Issue #125 - multi-project characters / picture sets, and what it does to scope.

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
"403" can never be an artefact of the resource simply not existing. It is built
by the autouse ``env`` fixture in this package's ``conftest.py``, which
``test_generic_field_reader_allowlist.py`` next door asserts against too.
"""

import io
import time
import zlib

import numpy as np
import pytest
from PIL import Image
from sqlmodel import delete, select

from pixlstash.db_models import (
    Face,
    Picture,
    PictureLikeness,
)
from pixlstash.db_models.picture_likeness import PictureLikenessQueue
from tests.authz_guard import assert_real_route
from tests.multi_project_authz.shared_env import (
    API,
    _bearer,
    _enforcing,
    _make_face,
    _wait_faces_extracted,
)

# Every positive assertion here must reach a real route: the SPA catch-all answers
# unmatched GETs with 200, which once made a whole-library BOLA vector's test
# vacuous. See tests/authz_guard.py. The fixture itself is registered by this
# package's conftest.
pytestmark = pytest.mark.usefixtures("no_spa_fallback")


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
        "the legacy FK must keep naming the primary project - it is not dropped "
        "until a later cleanup release"
    )

    body = owner.get(f"{API}/picture_sets/{env['set_id']}?info=true").json()
    assert body["project_ids"] == both
    assert body["project_id"] == both[0]


def test_leaving_one_project_keeps_the_other(env):
    """Dropping P2 leaves the entity in P1 - the FK follows, and the entity does
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
# Scope: list routes (SCOPED_LIST - the token narrows the listing in-handler)
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
    """``GET /picture_sets/locked-members`` narrows by project too - a locked
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
# R1 - `project_ids` is membership metadata about *other* projects
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
    """A project-scoped token reads the shared entity (200 - over-blocking would
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
    still reads its own entity - the narrowing must not turn into a refusal."""
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
    serialisation site - the primary project for the owner, the token's own
    project for a project token, ``None`` for an entity-scoped token - never
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
# R1c (issue #708) - the two channels the R1 narrowing did not cover
# ---------------------------------------------------------------------------
#
# R1 narrowed ``project_ids`` / ``project_id`` wherever an entity is serialised.
# Two ways of asking the same question stayed open:
#
# * a payload *keyed* by project id (``POST /projects/membership``) and the two
#   sites that still read the scalar straight off the model
#   (``GET /projects/{id}/picture_sets``, ``GET /characters/{id}/project_id``);
# * the ``project_id`` **filter**, which needs no payload at all - the presence
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

    ``unassigned_picture_ids`` is derived from the *narrowed* mapping - a picture
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
                # In-scope pictures must still come back - narrowing the project
                # keys must not turn into refusing the caller's own data.
                assert env["pic_a"] in payload["unassigned_picture_ids"], (
                    f"a {scope} token lost its own picture: {payload}"
                )


def test_project_filter_param_is_refused_without_project_visibility(env):
    """A character- / set- / picture-scoped token may not filter by *any*
    project id - a real one, an unrelated one, a non-existent one, or the
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
                # answers (200 - possibly with an empty, scope-filtered body).
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
    narrowed - including by ``UNASSIGNED``, which only a scoped token is
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
    project id, which for a set shared by P1+P2 is P1 - handed to a P2 token
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
    scalar ``project_id`` - the one character serialisation R1 did not reach."""
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
        # left to race with - and polling for quiescence is far slower than the
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
# R1d (issue #708, sign-off condition 2) - the project named in a PATH segment
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
# 403s - the same disclosure R1/R1c close, arriving through a path segment.
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
                    f"{scope} token can tell the probes apart on {template} - "
                    f"that is the oracle: "
                    + "; ".join(
                        f"{k} -> {v[0]} {v[1][:80]}" for k, v in answers.items()
                    )
                )


def test_project_token_is_not_told_which_other_projects_exist(env):
    """A *project* token has visibility of exactly one project, so the same
    indistinguishability must hold for every project that is not its own -
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
    """In-scope direction - over-blocking is its own regression. A project token
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
    the routes keep their ordinary, informative 404s - turning those into 403s
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
# R2 - a picture added to an already-multi-project entity joins *every* project
# ---------------------------------------------------------------------------
#
# Six write paths used to read the scalar primary FK to decide which
# ``PictureProjectMember`` row to create, so a picture added *after* the entity
# went multi-project silently joined the primary project only: the secondary
# project's token was 403'd and the owner's own ``?project_id=`` listing omitted
# it. That is an under-grant, never a leak - but it is the feature's headline case
# and invisible to the operator. Each path is pinned in both directions.


def _assert_picture_reaches_both_projects(env, picture_id, where):
    """The picture is anchored in P1 *and* P2 - and still not in P3."""
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
    """``POST /picture_sets/{id}/members/{picture_id}`` - the reviewer's original
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


def test_face_assignment_to_shared_character_joins_every_project(env):
    """``POST /characters/{id}/faces`` - the character twin of the set paths."""
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

    The seed is therefore a checksum of the whole name, not a sum of its bytes:
    a byte sum collides on any reordering, so two differently-named uploads
    could produce identical PNGs, dedupe into one picture, and fail the caller
    that expected its own. ``zlib.crc32`` is order-sensitive and stable across
    runs and interpreters, which ``hash()`` is not.
    """
    seed = zlib.crc32(filename.encode())
    color = (seed & 0xFF, (seed >> 8) & 0xFF, (seed >> 16) & 0xFF)
    buf = io.BytesIO()
    Image.new("RGB", (48, 48), color=color).save(buf, format="PNG")
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
    """``PictureImportTask._apply_set`` - the drop-target import path must read the
    same membership as the route it mirrors."""
    picture_id = _staged_import(env, {"set_id": env["set_id"]}, "import-into-set.png")
    _assert_picture_reaches_both_projects(
        env, picture_id, "import with set_id drop target"
    )


def test_import_into_shared_character_joins_every_project(env):
    """``PictureImportTask._apply_character`` - the character drop target."""
    picture_id = _staged_import(
        env, {"character_id": env["char_id"]}, "import-into-char.png"
    )
    _assert_picture_reaches_both_projects(
        env, picture_id, "import with character_id drop target"
    )
