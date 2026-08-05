"""Issue #721: the two generic by-name field readers serve columns only.

``GET /pictures/{id}/{field}`` and ``GET /characters/{id}/{field}`` used to hand
back *any* attribute of their object by name, relationships included, because
they end in ``safe_model_dict(getattr(obj, field))`` and ``safe_model_dict``
recurses into SQLModel instances and collections. The AuthzGate authorises the
*object*; nothing bounded which of that object's attributes came back. Reproduced
with the gate enforcing, before the fix:

* ``GET /pictures/1/projects`` -> ``{"projects":[{"id":1,"name":"P1",...}]}``
  while the same token got ``403`` on ``GET /projects/1``.
* ``GET /pictures/1/picture_sets`` -> set names **and** ``project_id`` again.
* ``GET /pictures/1/characters`` -> character names and ``project_id``.
* ``GET /characters/1/project`` -> the whole project row.
* ``GET /characters/1/pictures`` -> full ``Picture`` rows: ``project_id``,
  ``file_path``, ``pixel_sha``, ``original_file_name``, ``metadata_hash``,
  ``comfyui_positive_prompt``, ``comfyui_loras``, ``likeness_parameters``, ...

Both directions are asserted here, because over-blocking is its own regression
(CLAUDE.md §Security & authorization review process): every relationship is
refused, **and** the columns, the synthesised character ``thumbnail``, the
large-binary base64 branch and #719's narrowed ``project_id`` all still answer,
for the owner and for a scoped token alike.

**Reproduction note carried over from #719.** The fixture's set/character
PATCHes write ``PictureProjectMember`` join rows but leave the denormalised
``Picture.project_id`` NULL, so a naive probe reads ``null`` whether anything
narrows or not. That false negative is why this survived an earlier review. The
tests below backfill the scalar through ``PATCH /pictures/project`` and assert
the backfill landed before reading anything.
"""

import pytest
from sqlmodel import select

from pixlstash.db_models import Character, Picture
from pixlstash.route_inventory import api_endpoint_set
from pixlstash.utils.field_allowlist import (
    CHARACTER_EXTRA_SERVABLE_FIELDS,
    PICTURE_EXTRA_SERVABLE_FIELDS,
    servable_field_names,
)
from tests import test_multi_project_membership_authz as _multi_project
from tests.authz_guard import (  # noqa: F401
    assert_real_route,
    no_spa_fallback,
)

# Reused from the #125 / #719 suite rather than duplicated: that fixture already
# builds the three projects, the shared set and the shared character this needs,
# and its scoped tokens are the ones the leak was reproduced with. Re-exported by
# assignment (not `from … import env`) so the module-level name and the `env`
# test parameters do not read as a redefinition.
API = _multi_project.API
env = _multi_project.env
_bearer = _multi_project._bearer
_enforcing = _multi_project._enforcing
_make_face = _multi_project._make_face

pytestmark = pytest.mark.usefixtures("no_spa_fallback")

# The SPA catch-all answers unmatched GETs with 200, which once made a whole
# BOLA test vacuous; every positive assertion below reaches a real route.


# ---------------------------------------------------------------------------
# Guardrail: a new relationship cannot become servable by accident
# ---------------------------------------------------------------------------

#: The complete, reviewed set of non-column names each reader may serve. Pinned
#: rather than merely derived, so *growing* the exception set is a deliberate
#: edit that fails the build until someone updates this literal.
_PINNED_PICTURE_EXTRAS = {"faces"}
_PINNED_CHARACTER_EXTRAS = {"thumbnail", "faces"}


def test_declared_servable_exceptions_are_pinned():
    """The escape hatch must not grow quietly.

    ``PICTURE_EXTRA_SERVABLE_FIELDS`` / ``CHARACTER_EXTRA_SERVABLE_FIELDS`` are
    the only way a non-column name reaches the wire from these two routes. Each
    member is a live consumer with no dedicated endpoint to move to yet (#721);
    adding a member without recording why is the defect this whole file exists
    to stop.
    """
    assert set(PICTURE_EXTRA_SERVABLE_FIELDS) == _PINNED_PICTURE_EXTRAS, (
        "The picture reader's non-column exception set changed. Every member is "
        "a relationship or synthetic name served to a real consumer; justify the "
        "change in pixlstash/utils/field_allowlist.py and update this pin."
    )
    assert set(CHARACTER_EXTRA_SERVABLE_FIELDS) == _PINNED_CHARACTER_EXTRAS, (
        "The character reader's non-column exception set changed. Same rule."
    )


@pytest.mark.parametrize(
    "model, extras, expected_exposed",
    [
        (Picture, PICTURE_EXTRA_SERVABLE_FIELDS, _PINNED_PICTURE_EXTRAS),
        (Character, CHARACTER_EXTRA_SERVABLE_FIELDS, _PINNED_CHARACTER_EXTRAS),
    ],
    ids=["picture", "character"],
)
def test_no_new_relationship_becomes_servable(model, extras, expected_exposed):
    """A relationship added to the model must be denied by default.

    This is the completeness half of the fix: the allowlist is derived from the
    column namespace, so the *only* relationships that can ever be served are
    the declared exceptions. If someone adds ``Picture.audit_events`` tomorrow,
    it is refused with no code change here -- and if someone instead adds it to
    the exception set, this test goes red until they say why.
    """
    relationships = set(model.relationship_fields())
    columns = set(model.scalar_fields())

    # Anti-vacuity. An empty enumeration on either side would make the
    # subset assertion below pass while checking nothing at all.
    assert len(relationships) >= 4, (
        f"{model.__name__}.relationship_fields() returned {len(relationships)} "
        f"names; this guardrail is vacuous if the enumeration collapses"
    )
    assert len(columns) >= 5, (
        f"{model.__name__}.scalar_fields() returned {len(columns)} names; this "
        f"guardrail is vacuous if the enumeration collapses"
    )
    assert not (relationships & columns), (
        f"{model.__name__} has a name that is both a column and a relationship, "
        f"so 'servable == column namespace' no longer partitions cleanly: "
        f"{sorted(relationships & columns)}"
    )

    servable = servable_field_names(model, extras)
    exposed = relationships & servable
    assert exposed == set(expected_exposed) & relationships, (
        f"{model.__name__} relationship(s) {sorted(exposed)} are servable through "
        f"the generic by-name reader. safe_model_dict recurses into "
        f"relationships, so this serves whole related rows past every projection "
        f"and narrowing site in the codebase (#721). Deny it, or route the "
        f"consumer at a dedicated, projected endpoint."
    )
    # And the columns really are all servable -- the allowlist must not have
    # quietly become a hand-maintained subset.
    assert columns <= servable, (
        f"{model.__name__} column(s) {sorted(columns - servable)} are no longer "
        f"servable; the allowlist is meant to be the whole column namespace."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dedicated_get_literals(app, base: str) -> set[str]:
    """Literal sub-paths of ``GET /{base}/{id}/<literal>`` that have their own route.

    Several relationship names never reach the generic reader at all, because a
    dedicated GET route shadows them: ``GET /pictures/{id}/detections``,
    ``/tags``, ``/tag_predictions``, ``/{picture_id}/stack``. Those have their
    own handlers and their own projections, so demanding a 400 from them would
    be asserting against the wrong code. Derived from the live route inventory
    (the same enumeration the authz gate and the coverage matrix use) rather
    than a hand-kept list, so a newly added dedicated route is subtracted
    automatically -- while a newly added *relationship* is not, and stays in the
    denied set where it belongs.
    """
    literals = set()
    for method, path in api_endpoint_set(app):
        if method != "GET":
            continue
        parts = path.strip("/").split("/")
        # e.g. api / v1 / pictures / {id} / detections
        if len(parts) != 5 or parts[0] != "api" or parts[2] != base:
            continue
        if not (parts[3].startswith("{") and parts[3].endswith("}")):
            continue
        if parts[4].startswith("{"):
            continue
        literals.add(parts[4])
    return literals


def _names_routed_to_generic_reader(app, base: str, names) -> set[str]:
    """Of *names*, the ones a GET actually delivers to the generic by-name reader."""
    endpoints = api_endpoint_set(app)
    assert len(endpoints) > 100, (
        f"the route inventory returned {len(endpoints)} endpoints; every "
        f"subtraction below would be meaningless"
    )
    return set(names) - _dedicated_get_literals(app, base)


def _backfilled_env(env):
    """Give the fixture a picture whose scalar ``project_id`` is really set, a
    character with a real face (so ``Character.pictures`` is non-empty), and a
    non-NULL ``text_embedding`` (so the large-binary branch has bytes to encode).

    Without all three, the negative assertions below pass on empty data and
    prove nothing.
    """
    owner, projects = env["owner"], env["projects"]
    pic_a, char_id = env["pic_a"], env["char_id"]

    r = owner.patch(
        f"{API}/pictures/project",
        json={
            "picture_ids": [pic_a],
            "project_id": projects["P1"],
            "mode": "add",
        },
    )
    assert r.status_code == 200, r.text
    assert (
        owner.get(f"{API}/pictures/{pic_a}/project_id").json()["project_id"]
        == projects["P1"]
    ), "the scalar project_id backfill did not land; every probe below would read null"

    face_id = _make_face(env["server"], pic_a)
    r = owner.post(f"{API}/characters/{char_id}/faces", json={"face_ids": [face_id]})
    assert r.status_code == 200, r.text

    def _seed_embedding(session):
        pic = session.exec(select(Picture).where(Picture.id == pic_a)).one()
        pic.text_embedding = b"\x01\x02\x03\x04"
        session.add(pic)
        session.commit()

    env["server"].vault.db.run_task(_seed_embedding)

    # The relationships must actually hold rows, or "denied" is indistinguishable
    # from "empty" and the negative assertions are vacuous.
    def _counts(session):
        pic = session.exec(select(Picture).where(Picture.id == pic_a)).one()
        char = session.exec(select(Character).where(Character.id == char_id)).one()
        return {
            "picture.projects": len(pic.projects),
            "picture.picture_sets": len(pic.picture_sets),
            "picture.characters": len(pic.characters),
            "character.pictures": len(char.pictures),
            "character.project": 1 if char.project is not None else 0,
        }

    counts = env["server"].vault.db.run_immediate_read_task(_counts)
    for name, n in counts.items():
        assert n > 0, (
            f"{name} is empty in the fixture, so refusing it would prove nothing. "
            f"Counts: {counts}"
        )
    return counts


# ---------------------------------------------------------------------------
# Negative direction: every relationship is refused, on both readers
# ---------------------------------------------------------------------------


def test_picture_relationships_are_refused(env):
    """No ``Picture`` relationship is readable by name -- owner or scoped token.

    The owner is asserted too, deliberately: this is a response-shape bound, not
    an authorization decision, so it does not depend on who is asking. If it did,
    it would be the second scope ladder CLAUDE.md forbids.
    """
    counts = _backfilled_env(env)
    assert counts["picture.projects"] > 0

    owner, anon, mint = env["owner"], env["anon"], env["mint"]
    pic_a = env["pic_a"]

    candidates = set(Picture.relationship_fields()) - set(PICTURE_EXTRA_SERVABLE_FIELDS)
    denied = sorted(
        _names_routed_to_generic_reader(env["server"].api, "pictures", candidates)
    )
    assert {"projects", "picture_sets", "characters"} <= set(denied), (
        f"the three relationships #721 reproduced must reach the generic reader "
        f"and be denied there; got {denied}. If one of them gained a dedicated "
        f"route, assert that route's own projection instead of dropping it."
    )

    for field in denied:
        r = owner.get(f"{API}/pictures/{pic_a}/{field}")
        assert r.status_code == 400, (
            f"owner: GET /pictures/{{id}}/{field} returned {r.status_code}, "
            f"expected 400: {r.text[:400]}"
        )
        assert r.json() == {
            "detail": f"Field '{field}' is not readable on this endpoint"
        }, r.text

    with _enforcing(env["server"]):
        hdr = _bearer(mint("picture", pic_a))
        for field in denied:
            r = anon.get(f"{API}/pictures/{pic_a}/{field}", headers=hdr)
            assert r.status_code == 400, (
                f"picture-scoped token: GET /pictures/{{id}}/{field} returned "
                f"{r.status_code}, expected 400: {r.text[:400]}"
            )


def test_character_relationships_are_refused(env):
    """No ``Character`` relationship is readable by name -- owner or scoped token."""
    counts = _backfilled_env(env)
    assert counts["character.pictures"] > 0 and counts["character.project"] > 0

    owner, anon, mint = env["owner"], env["anon"], env["mint"]
    char_id = env["char_id"]

    candidates = set(Character.relationship_fields()) - set(
        CHARACTER_EXTRA_SERVABLE_FIELDS
    )
    denied = sorted(
        _names_routed_to_generic_reader(env["server"].api, "characters", candidates)
    )
    assert {"project", "pictures"} <= set(denied), (
        f"the two relationships #721 reproduced must reach the generic reader "
        f"and be denied there; got {denied}"
    )

    for field in denied:
        r = owner.get(f"{API}/characters/{char_id}/{field}")
        assert r.status_code == 400, (
            f"owner: GET /characters/{{id}}/{field} returned {r.status_code}, "
            f"expected 400: {r.text[:400]}"
        )
        assert r.json() == {
            "detail": f"Field '{field}' is not readable on this endpoint"
        }, r.text

    with _enforcing(env["server"]):
        hdr = _bearer(mint("character", char_id))
        for field in denied:
            r = anon.get(f"{API}/characters/{char_id}/{field}", headers=hdr)
            assert r.status_code == 400, (
                f"character-scoped token: GET /characters/{{id}}/{field} returned "
                f"{r.status_code}, expected 400: {r.text[:400]}"
            )


def test_refused_relationship_bodies_carry_no_related_data(env):
    """The refusal must not leak through the error body either.

    A 400 whose ``detail`` embedded the resolved rows would close nothing. The
    body echoes the caller's own input and nothing else.
    """
    _backfilled_env(env)
    owner, projects = env["owner"], env["projects"]

    r = owner.get(f"{API}/pictures/{env['pic_a']}/projects")
    assert r.status_code == 400, r.text
    body = r.text
    for needle in ("P1", "P2", str(projects["P1"]), "SharedSet"):
        assert needle not in body, f"the refusal body disclosed {needle!r}: {body}"


# ---------------------------------------------------------------------------
# Oracle properties of the refusal
# ---------------------------------------------------------------------------


def test_refusal_is_not_an_object_existence_oracle(env):
    """A denied field answers identically for a real and a non-existent object.

    ``require_servable_field`` runs before any database read, so the response
    cannot depend on whether the object exists. (The cross-token case is already
    the AuthzGate's: it 403s an out-of-scope object before the handler runs,
    whatever the field name -- asserted at the bottom of this test.)
    """
    _backfilled_env(env)
    owner, anon, mint = env["owner"], env["anon"], env["mint"]
    pic_a, char_id = env["pic_a"], env["char_id"]

    real = owner.get(f"{API}/pictures/{pic_a}/projects")
    missing = owner.get(f"{API}/pictures/999999/projects")
    assert real.status_code == missing.status_code == 400
    assert real.text == missing.text, (
        f"the refusal distinguishes an existing picture from a missing one: "
        f"{real.text} vs {missing.text}"
    )

    real = owner.get(f"{API}/characters/{char_id}/project")
    missing = owner.get(f"{API}/characters/999999/project")
    assert real.status_code == missing.status_code == 400
    assert real.text == missing.text, (
        f"the refusal distinguishes an existing character from a missing one: "
        f"{real.text} vs {missing.text}"
    )

    # ...while a *servable* field still 404s a missing object, which is the
    # distinction the client needs: 400 = not a readable field, 404 = gone.
    assert owner.get(f"{API}/pictures/999999/width").status_code == 404
    assert owner.get(f"{API}/characters/999999/name").status_code == 404

    # And an out-of-scope object is still the gate's 403, not this 400, so the
    # allowlist has not shadowed the authorization answer.
    with _enforcing(env["server"]):
        hdr = _bearer(mint("picture", pic_a))
        r = anon.get(f"{API}/pictures/{env['pic_b']}/width", headers=hdr)
        assert r.status_code in {403, 404}, (
            f"the gate must still refuse an out-of-scope picture: {r.status_code} {r.text}"
        )


def test_relationship_and_unknown_name_are_indistinguishable(env):
    """The refusal must not enumerate the ORM relationship namespace.

    If a relationship were refused differently from a typo, the response would
    tell a caller which names the model actually has.
    """
    _backfilled_env(env)
    owner = env["owner"]
    pic_a, char_id = env["pic_a"], env["char_id"]

    for base, obj_id, relationship in (
        ("pictures", pic_a, "projects"),
        ("characters", char_id, "project"),
    ):
        rel = owner.get(f"{API}/{base}/{obj_id}/{relationship}")
        junk = owner.get(f"{API}/{base}/{obj_id}/definitely_not_a_field")
        assert rel.status_code == junk.status_code == 400, (rel.text, junk.text)
        # Same template, differing only in the caller's own echoed input.
        assert rel.json()["detail"].replace(relationship, "X") == junk.json()[
            "detail"
        ].replace("definitely_not_a_field", "X"), (rel.text, junk.text)


# ---------------------------------------------------------------------------
# Positive direction: over-blocking is its own regression
# ---------------------------------------------------------------------------


def test_servable_picture_fields_still_work(env):
    """Columns, the large-binary base64 branch and #719's narrowed
    ``project_id`` all still answer -- owner and picture-scoped token."""
    _backfilled_env(env)
    owner, anon, mint, projects = (
        env["owner"],
        env["anon"],
        env["mint"],
        env["projects"],
    )
    pic_a = env["pic_a"]

    for field in ("width", "file_path", "project_id", "text_embedding", "faces"):
        assert_real_route(env["server"].api, "GET", f"{API}/pictures/{pic_a}/{field}")

    r = owner.get(f"{API}/pictures/{pic_a}/width")
    assert r.status_code == 200 and isinstance(r.json()["width"], int), r.text
    r = owner.get(f"{API}/pictures/{pic_a}/file_path")
    assert r.status_code == 200 and r.json()["file_path"], r.text

    # The large-binary base64 branch.
    r = owner.get(f"{API}/pictures/{pic_a}/text_embedding")
    assert r.status_code == 200, r.text
    assert r.json()["text_embedding"] == "AQIDBA==", r.text

    # #719's narrowing branch is untouched: the owner keeps the stored primary.
    r = owner.get(f"{API}/pictures/{pic_a}/project_id")
    assert r.status_code == 200 and r.json()["project_id"] == projects["P1"], r.text

    # The declared relationship exception still answers its live consumer
    # (frontend/src/api/pictures.js::listPictureFaces -- the face-box overlay).
    r = owner.get(f"{API}/pictures/{pic_a}/faces")
    assert r.status_code == 200 and r.json()["faces"], r.text

    with _enforcing(env["server"]):
        hdr = _bearer(mint("picture", pic_a))
        r = anon.get(f"{API}/pictures/{pic_a}/width", headers=hdr)
        assert r.status_code == 200, (
            f"a picture token was blocked from its own picture's column: {r.text}"
        )
        r = anon.get(f"{API}/pictures/{pic_a}/text_embedding", headers=hdr)
        assert r.status_code == 200, r.text
        r = anon.get(f"{API}/pictures/{pic_a}/faces", headers=hdr)
        assert r.status_code == 200, r.text
        # ...and #719's narrowing still applies to the scoped token.
        r = anon.get(f"{API}/pictures/{pic_a}/project_id", headers=hdr)
        assert r.status_code == 200 and r.json()["project_id"] is None, (
            f"a picture-scoped token has no project visibility, but the narrowed "
            f"branch handed it {r.text}"
        )

        hdr_p1 = _bearer(env["tokens"]["P1"])
        r = anon.get(f"{API}/pictures/{pic_a}/project_id", headers=hdr_p1)
        assert r.status_code == 200 and r.json()["project_id"] == projects["P1"], (
            f"a P1 token must still learn its own project id: {r.text}"
        )


def test_servable_character_fields_still_work(env):
    """``name``, ``thumbnail`` and the narrowed ``project_id`` all still answer.

    ``name`` and ``thumbnail`` are live frontend calls
    (``frontend/src/api/characters.js`` lines 169 and 193), and ``thumbnail`` is
    not a column at all -- it is the handler-generated face crop, admitted by
    the declared exception set. Blocking either would be a shipped regression.
    """
    _backfilled_env(env)
    owner, anon, mint, projects = (
        env["owner"],
        env["anon"],
        env["mint"],
        env["projects"],
    )
    char_id = env["char_id"]

    for field in ("name", "thumbnail", "project_id", "description"):
        assert_real_route(
            env["server"].api, "GET", f"{API}/characters/{char_id}/{field}"
        )

    r = owner.get(f"{API}/characters/{char_id}/name")
    assert r.status_code == 200 and r.json()["name"] == "SharedChar", r.text

    r = owner.get(f"{API}/characters/{char_id}/thumbnail")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/"), r.headers
    assert r.content, "the character thumbnail came back empty"

    r = owner.get(f"{API}/characters/{char_id}/description")
    assert r.status_code == 200 and "description" in r.json(), r.text

    r = owner.get(f"{API}/characters/{char_id}/project_id")
    assert r.status_code == 200 and r.json()["project_id"] == projects["P1"], r.text

    with _enforcing(env["server"]):
        hdr = _bearer(mint("character", char_id))
        r = anon.get(f"{API}/characters/{char_id}/name", headers=hdr)
        assert r.status_code == 200 and r.json()["name"] == "SharedChar", (
            f"a character token was blocked from its own character's name: {r.text}"
        )
        r = anon.get(f"{API}/characters/{char_id}/thumbnail", headers=hdr)
        assert r.status_code == 200 and r.headers["content-type"].startswith(
            "image/"
        ), (
            f"a character token was blocked from its own character's thumbnail: "
            f"{r.status_code} {r.text[:200]}"
        )
        r = anon.get(f"{API}/characters/{char_id}/project_id", headers=hdr)
        assert r.status_code == 200 and r.json()["project_id"] is None, (
            f"a character-scoped token has no project visibility, but the "
            f"narrowed branch handed it {r.text}"
        )

        hdr_p1 = _bearer(env["tokens"]["P1"])
        r = anon.get(f"{API}/characters/{char_id}/project_id", headers=hdr_p1)
        assert r.status_code == 200 and r.json()["project_id"] == projects["P1"], (
            f"a P1 token must still learn its own project id: {r.text}"
        )
