"""Security tests for READ-scoped API tokens.

Coverage
--------
1. Read token cannot perform any write operation (POST/PUT/PATCH/DELETE).
2. Read token cannot access owner-only data (config, token list, server config).
3. Read token scoped to one resource cannot read a different resource, including
   via the ComfyUI stack-member filter on /pictures, /pictures/stream and
   /pictures/count (see TestComfyuiFilterCannotEscapeTokenScope).
4. Expired read token is rejected.
5. ALL-scope bearer token scoped-token checks (READ token cannot create tokens).
6. Unauthenticated login endpoint brute-force lockout (≥5 failures → 429).
7. Global rate limiter blocks the unauthenticated login path after _LIMIT hits.
8. All picture data uploaded before token issuance survives every attack attempt
   intact (scores, filenames).

Environment sharing
-------------------
Booting a Server and pushing a library through the import pipeline costs
seconds; the assertions those libraries serve cost milliseconds. This module
therefore builds each of the three library shapes it needs **once per module**
(``_picture_library_env``, ``_two_set_env``, ``_comfyui_stack_env``) and resets
per test. Module scope rather than class scope because ``--ci-shard``
(tests/conftest.py) partitions *individual tests*, so a class-scoped fixture is
rebuilt once per shard per class and barely amortises.

What is emphatically **not** shared is the credential. Every per-test fixture
below deletes every token row, drops every session, flushes the token cache
(bumping its revocation epoch), clears the login lockout and the rate-limit
window, and then re-establishes the owner session from a fresh login before
minting the token the test will use.

Two separate things make that safe, and it is worth not confusing them. The
load-bearing one is structural: authentication runs as middleware *before*
routing, so a revoked, expired or absent credential is answered **401** on
every endpoint shape this module touches, while a live-but-wrong-scope
credential is answered 403/404 by the authz gate. A dead credential therefore
cannot satisfy any of the assertions below, all of which require 403 or 404.
The per-test wipe is defence in depth on top of that - it stops one test's
token from being the one a later test unknowingly exercises - and the in-scope
positive control below is what proves the credential is live rather than
assuming it.

Each per-test fixture also re-proves the environment before the test body runs:
the freshly minted token is exercised on an **in-scope read** (the positive
control adjacent to every negative one), and the library's **identity** - which
picture ids exist, which set holds which, which scores they carry - is
re-checked. Identity, never counts: a missing object answers a request the same
way a scope refusal does. These checks live in the fixture rather than in a
trailing "canary" test because the shard split would leave such a test watching
only its own shard.

Two classes deliberately keep a per-test Server: ``TestLoginBruteForce`` and
``TestRateLimiter`` exist to assert on server-lifetime lockout and rate-limit
state, which is the one thing a shared server cannot honestly provide. They
never import a picture, so they only pay the (cheap) boot.
"""

import io
import json
import tempfile
import time
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlmodel import Session, delete, select, update

import pixlstash.routes.pictures._character_likeness as likeness_module
import pixlstash.routes.pictures._listing as listing_module
import pixlstash.utils.rate_limiter as rl_module
from pixlstash.db_models import (
    Character,
    CharacterProjectMember,
    Picture,
    PictureSetMember,
    PictureStack,
    Tag,
    UserToken,
)
from pixlstash.server import Server
from pixlstash.utils.rate_limiter import RateLimitMiddleware
from tests.utils import upload_pictures_and_wait

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

API = "/api/v1"


# A tiny valid PNG produced in-memory so tests that need a fresh image don't
# depend on disk files.
def _make_png_bytes(width: int = 32, height: int = 32) -> bytes:
    img = Image.new("RGB", (width, height), color=(100, 149, 237))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# How many pictures the shared fixture library holds. Every assertion in this
# module is count-agnostic (`picture_ids[0]`, `picture_ids[:2]`, or "iterate
# them all"), and the manual scores below cycle 1-5, so five is the smallest
# library that still exercises the full score range AND leaves a
# single-picture share token narrowing a library strictly larger than its
# scope - which is the property the isolation tests actually assert.
_FIXTURE_PICTURE_COUNT = 5


def _good_picture_files() -> list[tuple[str, bytes, str]]:
    """Return (filename, bytes, content_type) tuples for the fixture library.

    These are generated in-memory rather than read from ``pictures/good/``.
    That directory is 19 MB of real photographs across 12 files, and
    ``_setup_server_with_pictures`` runs per test function - so the old version
    pushed 19 MB through the full import pipeline (embedding, face detection,
    tagging) 33 times per run of this module, to assert things like "a READ
    token gets 403 on POST /pictures/apply-scores".

    Nothing here reads image *content*: the two tests that touch a face- or
    likeness-bearing path stub the ML helpers outright (see
    ``test_picture_scoped_token_cannot_widen_via_character_likeness_sort`` and
    ``test_character_membership_does_not_leak_faces``), and every other
    assertion is about status codes, scoping and scores. The sibling helper
    ``_setup_two_picture_sets`` in this same module already uses generated
    PNGs for exactly this reason.

    Each image differs in size and colour so the importer's duplicate/hash
    detection keeps them as distinct pictures rather than collapsing them.
    """
    results: list[tuple[str, bytes, str]] = []
    for index in range(_FIXTURE_PICTURE_COUNT):
        width = 32 + index * 8
        height = 32 + index * 4
        img = Image.new(
            "RGB", (width, height), color=(20 + index * 40, 60, 200 - index * 30)
        )
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        results.append((f"fixture_{index:02d}.png", buf.getvalue(), "image/png"))
    return results


def _fixture_scores(picture_ids: list) -> dict[str, int]:
    """Manual scores (1-5) assigned to the fixture library, keyed by id string.

    Shared so a test can assert the library survived an attack without
    re-deriving the mapping ``_setup_server_with_pictures`` applied.
    """
    return {str(pid): (i % 5) + 1 for i, pid in enumerate(picture_ids)}


def _setup_server_with_pictures(temp_dir: str):
    """Create a Server, log in, upload all good pictures, set random scores.

    Returns (server, authed_client, picture_ids, read_token_value).
    """
    config_path = f"{temp_dir}/server-config.json"
    server = Server(config_path)
    server.__enter__()

    # Use a fresh TestClient that keeps the session cookie.
    client = TestClient(server.api, raise_server_exceptions=True)

    # Set up credentials.
    r = client.post(
        f"{API}/login", json={"username": "owner", "password": "example-owner-password"}
    )
    assert r.status_code == 200, r.text

    # Upload the good pictures.
    picture_files = _good_picture_files()
    # The generator, not a directory listing: the message this replaced named
    # pictures/good/, which this helper has not read since it started building
    # its PNGs in memory. Every scoping assertion below is indexed off the
    # fixture count, so a short library would fail them for the wrong reason.
    assert len(picture_files) == _FIXTURE_PICTURE_COUNT, (
        f"the fixture library is {len(picture_files)} pictures, not "
        f"{_FIXTURE_PICTURE_COUNT}"
    )

    files = [("file", (name, data, ct)) for name, data, ct in picture_files]
    import_status = upload_pictures_and_wait(client, files, timeout_s=30)
    assert import_status["status"] == "completed", (
        f"Picture import failed: {import_status}"
    )

    # Fetch the imported picture IDs.
    r = client.get(f"{API}/pictures")
    assert r.status_code == 200, r.text
    picture_ids = [p["id"] for p in r.json()]
    assert picture_ids, "No pictures after import"

    # Assign manual scores (1–5) so we can verify they survive attacks.
    scores = _fixture_scores(picture_ids)
    r = client.post(
        f"{API}/pictures/apply-scores",
        json={"scores": scores, "only_unscored": False},
    )
    assert r.status_code == 200, r.text

    # Create a global READ token (no resource restriction).
    r = client.post(
        f"{API}/users/me/token",
        json={"description": "global read", "scope": "READ"},
    )
    assert r.status_code == 200, r.text
    read_token = r.json()["token"]

    return server, client, picture_ids, read_token


def _assert_pictures_intact(authed_client, original_ids: list, original_scores: dict):
    """Verify picture IDs and scores in the DB match what was set at setup."""
    r = authed_client.get(f"{API}/pictures")
    assert r.status_code == 200, r.text
    current = {str(p["id"]): p for p in r.json()}

    # All original pictures still present.
    for pid in original_ids:
        assert str(pid) in current, f"Picture {pid} missing after attack"

    # Scores must not have changed.
    for pid_str, expected_score in original_scores.items():
        actual_score = current[pid_str].get("score")
        assert actual_score == expected_score, (
            f"Score for picture {pid_str} was tampered: "
            f"expected {expected_score}, got {actual_score}"
        )


def _setup_two_picture_sets(tmp: str):
    """Create two picture sets with one picture each.

    Returns a namespace carrying the server, the owner client and the ids. The
    set-A share token is *not* minted here - ``TestResourceScopedReadTokenIsolation``
    re-mints it per test so no test can inherit another test's credential.
    """
    config_path = f"{tmp}/server-config.json"
    server = Server(config_path)
    server.__enter__()
    client = TestClient(server.api, raise_server_exceptions=True)

    r = client.post(
        f"{API}/login", json={"username": "owner", "password": "example-owner-password"}
    )
    assert r.status_code == 200, r.text

    # Upload two pictures.
    for png_bytes, name in [
        (_make_png_bytes(64, 64), "picA.png"),
        (_make_png_bytes(48, 48), "picB.png"),
    ]:
        import_status = upload_pictures_and_wait(
            client, [("file", (name, png_bytes, "image/png"))], timeout_s=15
        )
        assert import_status["status"] == "completed", f"Import failed: {import_status}"

    r = client.get(f"{API}/pictures")
    assert r.status_code == 200, r.text
    all_ids = [p["id"] for p in r.json()]
    assert len(all_ids) >= 2

    pic_a_id, pic_b_id = all_ids[0], all_ids[1]

    # Create two picture sets.
    r = client.post(f"{API}/picture_sets", json={"name": "Set A"})
    assert r.status_code == 200, r.text
    set_a_id = r.json()["picture_set"]["id"]

    r = client.post(f"{API}/picture_sets", json={"name": "Set B"})
    assert r.status_code == 200, r.text
    set_b_id = r.json()["picture_set"]["id"]

    # Add pictures to their respective sets.
    r = client.post(f"{API}/picture_sets/{set_a_id}/members/{pic_a_id}")
    assert r.status_code in {200, 201, 204}, r.text

    r = client.post(f"{API}/picture_sets/{set_b_id}/members/{pic_b_id}")
    assert r.status_code in {200, 201, 204}, r.text

    # A project that actually exists, so the cross-resource-type refusals below
    # are measured against a real object. Aiming them at a made-up id would let
    # a plain "no such project" answer stand in for a scope refusal.
    r = client.post(f"{API}/projects", json={"name": "Real Project"})
    assert r.status_code in {200, 201}, r.text
    project_id = r.json()["id"]

    return SimpleNamespace(
        server=server,
        owner_client=client,
        set_a=set_a_id,
        set_b=set_b_id,
        pic_a=pic_a_id,
        pic_b=pic_b_id,
        project=project_id,
    )


# ---------------------------------------------------------------------------
# Shared environments and the per-test credential reset
# ---------------------------------------------------------------------------


def _stop_planner_and_settle(server, timeout_s: float = 120.0) -> None:
    """Stop *server*'s planner and wait out the work it already handed off.

    ``WorkPlanner.stop()`` stops the loop that FINDS work. It does not touch
    the ``TaskRunner``, which owns its own queues and worker threads, so a task
    the planner submitted seconds earlier is still queued or already executing
    when ``stop()`` returns - measured on this very fixture: one task on the
    GPU queue and a ``FaceExtractionTask`` running.

    That is what a shared module environment cannot survive. ``TagTask``
    deletes a picture's tags before writing its own, so a tagger still in
    flight lands inside a test body, wipes the ``tag-a-only`` row the test just
    posted, and leaves the tagger's own vocabulary in its place - which is
    exactly how ``test_list_all_tags_cannot_leak_out_of_scope_vocab`` failed on
    a CI runner slow enough to keep the pipeline alive that long, while passing
    on every machine fast enough to finish it during setup.

    Same shape as ``tests/test_picture_mutation_scope.py``'s pipeline settle:
    drain what is queued, then wait for what is running. Looped, because a
    finishing task's completion callback can submit its own follow-up.

    **The planner thread has to be dead before the queues mean anything.**
    ``stop()`` joins it for ``STOP_JOIN_TIMEOUT_S`` and then returns whether or
    not it exited; a pass slow enough on a loaded runner outlives that, and the
    loop then submits the ``TagTask`` its pass had found *after* this helper
    saw empty queues and returned. Exactly the failure described above, seen
    again with a pass that took eleven seconds. So the queues are only read
    once ``is_running()`` is false.
    """
    planner = server.vault._work_planner
    planner.stop()
    runner = server.vault._task_runner
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if planner.is_running():
            time.sleep(0.05)
            continue
        runner.cancel_pending_tasks()
        with runner._active_task_lock:
            active = list(runner._active_tasks.values())
        if not active and runner._queue.empty() and runner._gpu_queue.empty():
            return
        time.sleep(0.05)
    raise AssertionError(
        f"the import pipeline did not settle within {timeout_s}s; still "
        f"running: {active}"
    )


def _clear_rate_limit_window(server) -> None:
    """Empty the global rate limiter's sliding window on *server*.

    The limiter counts every request to an auth-excluded path, and
    ``POST /login`` is one - so the per-test re-login below is itself a counted
    event. Over a shared server those events accumulate inside the 60 s window
    and would eventually 429 the re-login (loudly, but for the wrong reason),
    and they would silently defang ``test_data_intact_after_rate_limit_barrage``
    by exhausting its patched limit before the barrage starts.

    The middleware instance only exists inside the built stack, so it is fetched
    by walking it. A miss raises rather than passing quietly: the reset would
    otherwise stop doing anything the day the middleware moves.
    """
    if server.api.middleware_stack is None:
        server.api.middleware_stack = server.api.build_middleware_stack()
    node = server.api.middleware_stack
    while node is not None:
        if isinstance(node, RateLimitMiddleware):
            with node._lock:
                node._events.clear()
            return
        node = getattr(node, "app", None)
    raise AssertionError(
        "RateLimitMiddleware not found in the app's middleware stack - the "
        "per-test rate-limit reset is no longer resetting anything"
    )


def _reset_owner_credentials(server, owner_client) -> None:
    """Destroy every credential this module minted, then re-establish the owner.

    Deleting the token rows, dropping the sessions and flushing the token cache
    (which bumps the revocation epoch) means no token minted by an earlier test
    can still authenticate anything: a test that reaches for one gets a 401, not
    a plausible-looking 403. The lockout counters and the rate-limit window are
    cleared because a test that hammers bad passwords or public paths would
    otherwise 429 the re-login below.

    The re-login is itself an assertion. It proves the owner password is still
    ``example-owner-password``, which is precisely what the "READ token cannot
    change the password" tests are protecting, and it fails loudly instead of
    letting a dirty environment masquerade as a scope refusal.
    """

    def _wipe(session: Session):
        session.exec(delete(UserToken))
        session.commit()

    server.hub_engine.run_task(_wipe)
    server.auth._clear_all_sessions()
    server.auth._flush_token_cache()
    server.auth._failed_login_attempts = 0
    server.auth._login_lockout_until = 0.0
    _clear_rate_limit_window(server)

    r = owner_client.post(
        f"{API}/login", json={"username": "owner", "password": "example-owner-password"}
    )
    assert r.status_code == 200, (
        f"owner re-login failed - the shared environment is dirty: {r.text}"
    )


def _mint_read_token(owner_client, description: str, **restriction) -> str:
    """Mint a fresh READ token as the owner and return its secret value."""
    r = owner_client.post(
        f"{API}/users/me/token",
        json={"description": description, "scope": "READ", **restriction},
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _prove_token_reads(server, token: str, path: str = "/pictures", **params) -> set:
    """Exercise *token* on an in-scope read and return the picture ids it saw.

    This is the positive control that sits in front of every negative assertion
    in this module: if the freshly minted token cannot read what it is entitled
    to, the refusals the test is about would prove nothing, and the fixture says
    so instead of letting the test pass.
    """
    r = TestClient(server.api).get(
        f"{API}{path}", headers={"Authorization": f"Bearer {token}"}, params=params
    )
    assert r.status_code == 200, (
        f"a fresh READ token cannot perform an in-scope read on {path} "
        f"({r.status_code}: {r.text}) - the refusals below would prove nothing"
    )
    body = r.json()
    if isinstance(body, dict):
        body = body["pictures"]
    return {row["id"] for row in body}


@pytest.fixture(scope="module")
def _picture_library_env():
    """One Server holding the five-picture scored fixture library.

    Every test served by this environment expects its writes to be *refused*,
    so the library is read-only in practice; anything that did land is caught by
    the ids-and-scores check in ``_SharedPictureLibrary.library_env`` before the
    next test asserts anything.

    Private on purpose: reach it through ``_SharedPictureLibrary``, never
    directly, or the test gets no credential reset and no integrity check.
    """
    with tempfile.TemporaryDirectory() as tmp:
        server, owner_client, picture_ids, _ = _setup_server_with_pictures(tmp)
        try:
            yield SimpleNamespace(
                server=server,
                owner_client=owner_client,
                picture_ids=picture_ids,
                scores=_fixture_scores(picture_ids),
            )
        finally:
            server.__exit__(None, None, None)


class _SharedPictureLibrary:
    """Mixin for the classes that run against ``_picture_library_env``.

    The reset is ``autouse`` so a test added later cannot opt out of it by
    forgetting to request the fixture; it still returns the environment so tests
    can take ``library_env`` as an argument.
    """

    @pytest.fixture(autouse=True)
    def library_env(self, _picture_library_env):
        env = _picture_library_env
        _reset_owner_credentials(env.server, env.owner_client)
        token = _mint_read_token(env.owner_client, "per-test read")

        seen = _prove_token_reads(env.server, token)
        assert seen == set(env.picture_ids), (
            "the shared library no longer holds exactly the fixture pictures: "
            f"{sorted(seen)} != {sorted(env.picture_ids)}"
        )
        _assert_pictures_intact(env.owner_client, env.picture_ids, env.scores)

        return SimpleNamespace(
            server=env.server,
            owner_client=env.owner_client,
            picture_ids=env.picture_ids,
            scores=env.scores,
            read_token=token,
        )


@pytest.fixture(scope="module")
def _two_set_env():
    """One Server holding two single-picture sets, shared by the isolation tests.

    The planner is stopped once the two imports are done, and the work it has
    ALREADY handed to the runner is waited out - see
    {@link _stop_planner_and_settle}, which is the half this fixture used to
    skip. Nothing after setup imports a picture, so there is no legitimate work
    left here - but several of these tests hand-write ``Tag``, ``Character``
    and ``PictureStack`` rows, and a ``TagTask`` still in flight (it deletes a
    picture's tags before rewriting them) or a stack-cohesion sweep would
    clobber them. Same move as tests/test_smart_score_invalidation.py.

    Private on purpose: reach it through ``TestResourceScopedReadTokenIsolation.env``,
    never directly, or the test gets no reset and no integrity check.
    """
    with tempfile.TemporaryDirectory() as tmp:
        env = _setup_two_picture_sets(tmp)
        try:
            _stop_planner_and_settle(env.server)
            yield env
        finally:
            env.server.__exit__(None, None, None)


def _picture_set_members(server, set_id: int) -> set:
    """Read a picture set's membership straight from the DB."""

    def _read(session: Session):
        return set(
            session.exec(
                select(PictureSetMember.picture_id).where(
                    PictureSetMember.set_id == set_id
                )
            ).all()
        )

    return server.vault.db.run_task(_read)


def _reset_two_set_library(env) -> None:
    """Put the shared two-set library back on the shape its tests assume.

    Five kinds of row are written by tests in that class and would otherwise be
    inherited by the ones that follow:

    * tags - one test asserts a per-tag count *exactly*, so a leftover tag row
      turns that count into someone else's;
    * a character named ``Ref``, created by two different tests;
    * the ComfyUI model/LoRA vocab columns, seeded straight onto the pictures;
    * a stack spanning both sets;
    * **set membership**. This one is the trap: forming a stack is
      stack-atomic for picture-set membership, so stacking ``pic_b`` onto
      ``pic_a`` pulls ``pic_b`` into Set A - and Set A is the scope every
      negative assertion in that class is measured against. Membership is
      therefore rebuilt from scratch, not just left alone.

    No foreign-key pragma is used. The statement order below leaves every
    reference satisfied at all times - children before parents, and the
    ``Picture`` rows detached from their stack *before* the stacks go - which
    makes deferral unnecessary. That is deliberate: with pysqlite a
    ``PRAGMA defer_foreign_keys = ON`` issued before any DML runs in its own
    autocommit transaction and is gone again by the time the DELETEs open
    theirs, so it can look like protection while providing none (measured in
    #821). An ordering that never needs the pragma cannot silently lose it.
    """

    def _wipe(session: Session):
        session.exec(delete(Tag))
        session.exec(delete(CharacterProjectMember))
        session.exec(delete(Character))
        # Detach the pictures first so the stack rows are unreferenced.
        session.exec(
            update(Picture).values(
                stack_id=None,
                stack_position=None,
                comfyui_models=None,
                comfyui_loras=None,
            )
        )
        session.exec(delete(PictureStack))
        session.exec(delete(PictureSetMember))
        session.add(PictureSetMember(set_id=env.set_a, picture_id=env.pic_a))
        session.add(PictureSetMember(set_id=env.set_b, picture_id=env.pic_b))
        session.commit()

    env.server.vault.db.run_task(_wipe)


# The ComfyUI stack fixture. Which seeded pictures carry the filtered
# model/LoRA, and how the two stacks are laid out once ``_form_stacks`` runs
# (``*`` = matches the filter)::
#
#     shared stack : cover*  sibling_a*  sibling_b*
#     other stack  : quiet_cover   quiet_member*
#     loose        : lone*
#
# Only ``cover`` is ever inside the token's grant. ``quiet_cover`` is the one
# that must come back for the *owner* purely through the member branch.
STACK_MODEL = "shared-model.safetensors"
STACK_LORA = "shared-lora.safetensors"
STACK_FILTER_PARAMS = ("comfyui_model", "comfyui_lora")
_STACK_MATCHING = {"cover", "sibling_a", "sibling_b", "quiet_member", "lone"}
_SHARED_STACK = ("cover", "sibling_a", "sibling_b")
_OTHER_STACK = ("quiet_cover", "quiet_member")
_STACK_PATHS = {
    name: f"/home/owner/private/{name}.png"
    for name in (*_SHARED_STACK, *_OTHER_STACK, "lone")
}


def _seed_stack_pictures(server) -> dict:
    """Create the six unstacked pictures with their ComfyUI metadata.

    ``comfyui_models`` / ``comfyui_loras`` are pipeline-populated JSON columns
    with no owner-facing write API, so they are seeded directly via the DB task
    runner (same pattern as ``_seed_comfyui_vocab``).
    """

    def _seed(session):
        ids = {}
        for name, file_path in _STACK_PATHS.items():
            matching = name in _STACK_MATCHING
            pic = Picture(
                file_path=file_path,
                comfyui_models=json.dumps(
                    [STACK_MODEL if matching else "unrelated-model"]
                ),
                comfyui_loras=json.dumps(
                    [STACK_LORA if matching else "unrelated-lora"]
                ),
            )
            session.add(pic)
            session.commit()
            session.refresh(pic)
            ids[name] = pic.id
        return ids

    return server.vault.db.run_task(_seed)


def _form_stacks(server, ids: dict) -> None:
    """Stack the seeded pictures, keeping ``lone`` unstacked.

    Done in the DB rather than through the stack API so the stack can be formed
    at an arbitrary point relative to picture-set membership -- the set-scoped
    test below depends on forming it *after*.
    """

    def _stack(session):
        for names in (_SHARED_STACK, _OTHER_STACK):
            stack = PictureStack()
            session.add(stack)
            session.commit()
            session.refresh(stack)
            for position, name in enumerate(names):
                pic = session.get(Picture, ids[name])
                pic.stack_id = stack.id
                pic.stack_position = position
                session.add(pic)
        session.commit()

    server.vault.db.run_task(_stack)


def _stack_layout(server) -> dict:
    """Return ``{file_path: (stack members by file_path, position)}`` for the vault.

    Used as the shared ComfyUI environment's integrity check: it pins *which*
    pictures exist, where they live on disk and how they are stacked, rather
    than how many there are.
    """

    def _read(session: Session):
        rows = session.exec(
            select(Picture.file_path, Picture.stack_id, Picture.stack_position)
        ).all()
        return {path: (stack_id, position) for path, stack_id, position in rows}

    return server.vault.db.run_task(_read)


@pytest.fixture(scope="module")
def _comfyui_stack_env():
    """One Server holding the straddling ComfyUI stack fixture.

    Read-only for every test it serves: the pictures and stacks are written once
    here, directly in the DB, and only queried afterwards. The planner is stopped
    for the same reason as ``_two_set_env`` - these rows point at file paths that
    do not exist, and a warm sweep has no business rewriting them.

    Private on purpose: reach it through
    ``TestComfyuiFilterCannotEscapeTokenScope.env``, never directly, or the test
    gets no credential reset and no stack-layout check.
    """
    with tempfile.TemporaryDirectory() as tmp:
        server = Server(f"{tmp}/server-config.json")
        server.__enter__()
        try:
            owner_client = TestClient(server.api, raise_server_exceptions=True)
            r = owner_client.post(
                f"{API}/login",
                json={"username": "owner", "password": "example-owner-password"},
            )
            assert r.status_code == 200, r.text

            ids = _seed_stack_pictures(server)
            _form_stacks(server, ids)
            _stop_planner_and_settle(server)

            yield SimpleNamespace(server=server, owner_client=owner_client, ids=ids)
        finally:
            server.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 0. An ALL-scope token cannot be restricted to a resource (F1/F3 footgun)
# ---------------------------------------------------------------------------


class TestAllScopeResourceTokenRejected(_SharedPictureLibrary):
    """Minting an ``ALL``+``resource_type`` token must be rejected.

    The auth middleware only builds ``request.state.token_scope`` for non-ALL
    scopes, so such a token would bypass every object-scope guard
    (``enforce_picture_scope`` / ``fetch_scope_allowed_picture_ids`` read
    ``token_scope``) *and* pass the owner-only token-creation check - it is a
    full owner token wearing a "restricted" label. See the F3 finding in
    docs/reviews/feature-slick-grid-updates.md.
    """

    def test_all_scope_with_resource_type_is_rejected(self, library_env):
        r = library_env.owner_client.post(
            f"{API}/users/me/token",
            json={
                "description": "sneaky scoped write token",
                "scope": "ALL",
                "resource_type": "picture",
                "resource_id": library_env.picture_ids[0],
            },
        )
        assert r.status_code == 400, (
            "ALL-scope token must not be restrictable to a resource, "
            f"got {r.status_code}: {r.text}"
        )

    def test_read_scope_with_resource_type_still_allowed(self, library_env):
        r = library_env.owner_client.post(
            f"{API}/users/me/token",
            json={
                "description": "legit resource share",
                "scope": "READ",
                "resource_type": "picture",
                "resource_id": library_env.picture_ids[0],
            },
        )
        assert r.status_code == 200, (
            f"READ resource share must still mint, got {r.status_code}: {r.text}"
        )

    def test_all_scope_without_resource_still_allowed(self, library_env):
        r = library_env.owner_client.post(
            f"{API}/users/me/token",
            json={"description": "owner token", "scope": "ALL"},
        )
        assert r.status_code == 200, (
            f"ALL owner token must still mint, got {r.status_code}: {r.text}"
        )


def _rescope(server, token: str, scope: str) -> str:
    """Force *token*'s hub row to *scope* and return the token unchanged.

    ``create_token`` allowlists ``ALL``/``READ``, so a token carrying any other
    scope has no mint path and the row has to be written directly - the row is
    the thing under test. The cache flush matters: the middleware answers from
    the token cache, so without it the request would still see ``READ``.

    **The write is verified, not assumed.** A ``READ`` token answers every
    refusal these tests assert on - the same 403 and the same
    ``"Token is read-only"`` body - so an UPDATE that matched no row (a renamed
    column, a changed prefix length, a token minted against a different hub)
    would leave all of them passing while testing nothing at all. The row is
    therefore read back and its scope asserted, which is the one thing that
    tells "the middleware refused the new scope" from "the new scope was never
    written".
    """
    prefix = token[:8]

    def _set(session: Session):
        result = session.exec(
            update(UserToken)
            .where(UserToken.token_prefix == prefix)
            .values(scope=scope)
        )
        session.commit()
        rows = session.exec(
            select(UserToken).where(UserToken.token_prefix == prefix)
        ).all()
        return result.rowcount, [row.scope for row in rows]

    rowcount, scopes = server.hub_engine.run_task(_set)
    assert rowcount == 1, (
        f"rescoping to {scope!r} matched {rowcount} token rows, not 1 - the "
        "refusals this token is about to be measured against would be the "
        "ordinary READ refusals, and would prove nothing"
    )
    assert scopes == [scope], (
        f"the token row reads back as {scopes} rather than [{scope!r}] - the "
        "scope under test was never written"
    )
    server.auth._flush_token_cache()
    return token


class TestUnknownScopeFailsClosed(_SharedPictureLibrary):
    """A scope the product does not recognise must be treated as read-only.

    The middleware used to refuse a write only for ``scope == "READ"``, so any
    other string - a misconfigured row, a forged one, a scope added in a later
    commit - skipped the refusal and reached every ``*_SCOPED`` mutation route,
    each of which is write-unreachable *solely* because of that comparison. It
    now keys on an explicit set of write-enabled scopes instead (issue #962).
    """

    def test_an_unknown_scope_cannot_write(self, library_env):
        token = _rescope(library_env.server, library_env.read_token, "BOGUS")

        # Positive control: the credential is live and reads what it is entitled
        # to, so the refusal below is a scope decision and not a dead token.
        assert _prove_token_reads(library_env.server, token) == set(
            library_env.picture_ids
        )

        r = TestClient(library_env.server.api).delete(
            f"{API}/pictures/{library_env.picture_ids[0]}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403, (
            f"an unrecognised scope must not reach a mutation route, got {r.status_code}: {r.text}"
        )
        assert r.json()["detail"] == "Token is read-only"
        _assert_pictures_intact(
            library_env.owner_client, library_env.picture_ids, library_env.scores
        )

    def test_an_unknown_scope_cannot_read_the_blocked_get_paths(self, library_env):
        token = _rescope(library_env.server, library_env.read_token, "BOGUS")
        r = TestClient(library_env.server.api).get(
            f"{API}/filesystem/browse",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403, (
            f"the filesystem belt must hold for every scoped token, got {r.status_code}: {r.text}"
        )
        # The gate declares this path LOCAL_OWNER_ONLY and would answer 403 as
        # well, so the body is what proves the middleware belt is the refuser -
        # which is the half this change hoisted out of the ``READ`` branch.
        assert r.json()["detail"] == "Token is read-only"

    def test_a_read_safe_post_path_still_works_for_a_read_token(self, library_env):
        """Over-blocking is its own regression: the allowlist must still open."""
        r = TestClient(library_env.server.api).post(
            f"{API}/pictures/thumbnails",
            headers={"Authorization": f"Bearer {library_env.read_token}"},
            json={"picture_ids": library_env.picture_ids},
        )
        assert r.status_code == 200, (
            f"READ_SAFE_POST_PATHS must stay reachable for a READ token: {r.text}"
        )


# ---------------------------------------------------------------------------
# 1. READ token must not perform write operations
# ---------------------------------------------------------------------------


class TestReadTokenBlocksWrites(_SharedPictureLibrary):
    """A READ token must be rejected for every mutating HTTP method.

    The Server is shared (``_picture_library_env``) but the credential is not:
    ``library_env`` re-mints one per test, proves it works on an in-scope read,
    and re-checks the library's ids and scores first, so a 403 below can only
    mean "wrong scope".
    """

    def test_cannot_upload_picture(self, library_env):
        png = _make_png_bytes()
        r = TestClient(library_env.server.api).post(
            f"{API}/pictures/import",
            files=[("file", ("new.png", png, "image/png"))],
            headers={"Authorization": f"Bearer {library_env.read_token}"},
        )
        assert r.status_code == 403, (
            f"READ token should not be able to upload pictures, got {r.status_code}: {r.text}"
        )

    def test_cannot_delete_picture(self, library_env):
        target_id = library_env.picture_ids[0]
        r = TestClient(library_env.server.api).delete(
            f"{API}/pictures/{target_id}",
            headers={"Authorization": f"Bearer {library_env.read_token}"},
        )
        assert r.status_code == 403, (
            f"READ token should not delete pictures, got {r.status_code}: {r.text}"
        )

    def test_cannot_patch_picture_score(self, library_env):
        target_id = library_env.picture_ids[0]
        r = TestClient(library_env.server.api).patch(
            f"{API}/pictures/{target_id}",
            json={"score": 1},
            headers={"Authorization": f"Bearer {library_env.read_token}"},
        )
        assert r.status_code == 403, (
            f"READ token should not PATCH pictures, got {r.status_code}: {r.text}"
        )

    def test_cannot_batch_apply_scores(self, library_env):
        payload = {"scores": {str(pid): 1 for pid in library_env.picture_ids}}
        r = TestClient(library_env.server.api).post(
            f"{API}/pictures/apply-scores",
            json=payload,
            headers={"Authorization": f"Bearer {library_env.read_token}"},
        )
        assert r.status_code == 403, (
            f"READ token should not batch-set scores, got {r.status_code}: {r.text}"
        )

    def test_cannot_change_password(self, library_env):
        r = TestClient(library_env.server.api).post(
            f"{API}/users/me/auth",
            json={
                "current_password": "example-owner-password",
                "new_password": "example-hacked-password",
            },
            headers={"Authorization": f"Bearer {library_env.read_token}"},
        )
        assert r.status_code == 403, (
            f"READ token should not change password, got {r.status_code}: {r.text}"
        )

    def test_cannot_create_new_token(self, library_env):
        r = TestClient(library_env.server.api).post(
            f"{API}/users/me/token",
            json={"description": "escalated", "scope": "ALL"},
            headers={"Authorization": f"Bearer {library_env.read_token}"},
        )
        assert r.status_code == 403, (
            f"READ token should not create tokens, got {r.status_code}: {r.text}"
        )

    def test_cannot_delete_token(self, library_env):
        # The owner creates a second token to give us an ID to target.
        r2 = library_env.owner_client.post(
            f"{API}/users/me/token",
            json={"description": "victim token", "scope": "READ"},
        )
        assert r2.status_code == 200, r2.text
        victim_id = r2.json()["token_id"]

        r = TestClient(library_env.server.api).delete(
            f"{API}/users/me/token/{victim_id}",
            headers={"Authorization": f"Bearer {library_env.read_token}"},
        )
        assert r.status_code == 403, (
            f"READ token should not delete tokens, got {r.status_code}: {r.text}"
        )

    def test_cannot_upload_watermark(self, library_env):
        png = _make_png_bytes()
        r = TestClient(library_env.server.api).post(
            f"{API}/users/me/watermark",
            files=[("file", ("wm.png", png, "image/png"))],
            headers={"Authorization": f"Bearer {library_env.read_token}"},
        )
        assert r.status_code == 403, (
            f"READ token should not upload watermark, got {r.status_code}: {r.text}"
        )

    def test_cannot_patch_user_config(self, library_env):
        r = TestClient(library_env.server.api).patch(
            f"{API}/users/me/config",
            json={"max_vram_gb": 0},
            headers={"Authorization": f"Bearer {library_env.read_token}"},
        )
        assert r.status_code == 403, (
            f"READ token should not PATCH user config, got {r.status_code}: {r.text}"
        )

    def test_cannot_restore_deleted_pictures(self, library_env):
        r = TestClient(library_env.server.api).post(
            f"{API}/pictures/scrapheap/restore",
            json={"picture_ids": library_env.picture_ids[:2]},
            headers={"Authorization": f"Bearer {library_env.read_token}"},
        )
        assert r.status_code == 403, (
            f"READ token should not restore pictures, got {r.status_code}: {r.text}"
        )


# ---------------------------------------------------------------------------
# 2. READ token must not access owner-restricted endpoints
# ---------------------------------------------------------------------------


class TestReadTokenBlocksOwnerData(_SharedPictureLibrary):
    """A READ token must not be able to read privileged owner data.

    Every 403 below is paired with the in-scope 200 that ``library_env`` already
    proved on ``GET /pictures`` with the very same token: the credential is
    known-good, so the refusal can only be about what it is refusing to reach.
    """

    def test_cannot_read_user_config(self, library_env):
        r = TestClient(library_env.server.api).get(
            f"{API}/users/me/config",
            headers={"Authorization": f"Bearer {library_env.read_token}"},
        )
        assert r.status_code == 403, (
            f"READ token should not read user config (contains secrets), "
            f"got {r.status_code}: {r.text}"
        )

    def test_cannot_list_tokens(self, library_env):
        """READ token must not enumerate the owner's other token metadata."""
        r = TestClient(library_env.server.api).get(
            f"{API}/users/me/token",
            headers={"Authorization": f"Bearer {library_env.read_token}"},
        )
        assert r.status_code == 403, (
            f"READ token should not list all tokens (information disclosure), "
            f"got {r.status_code}: {r.text}"
        )

    def test_cannot_read_filesystem_roots(self, library_env):
        r = TestClient(library_env.server.api).get(
            f"{API}/server-config/filesystem-roots",
            headers={"Authorization": f"Bearer {library_env.read_token}"},
        )
        assert r.status_code == 403, (
            f"READ token must not expose filesystem root paths, "
            f"got {r.status_code}: {r.text}"
        )

    def test_cannot_read_watch_folders(self, library_env):
        r = TestClient(library_env.server.api).get(
            f"{API}/server-config/watch-folders",
            headers={"Authorization": f"Bearer {library_env.read_token}"},
        )
        assert r.status_code == 403, (
            f"READ token must not expose watch folder paths, "
            f"got {r.status_code}: {r.text}"
        )

    def test_cannot_browse_filesystem(self, library_env):
        r = TestClient(library_env.server.api).get(
            f"{API}/filesystem/browse",
            headers={"Authorization": f"Bearer {library_env.read_token}"},
        )
        assert r.status_code == 403, (
            f"READ token must not browse server filesystem, "
            f"got {r.status_code}: {r.text}"
        )

    def test_cannot_detect_sidecars(self, library_env, tmp_path):
        """READ token must not walk the server filesystem via sidecar detection."""
        r = TestClient(library_env.server.api).get(
            f"{API}/reference-folders/detect-sidecars",
            params={"path": str(tmp_path)},
            headers={"Authorization": f"Bearer {library_env.read_token}"},
        )
        assert r.status_code == 403, (
            f"READ token must not probe the filesystem via detect-sidecars, "
            f"got {r.status_code}: {r.text}"
        )

    def test_cannot_access_shared_resource_ids(self, library_env):
        """READ token must not enumerate which resources have been shared."""
        r = TestClient(library_env.server.api).get(
            f"{API}/users/me/shared-resource-ids",
            params={"resource_type": "picture"},
            headers={"Authorization": f"Bearer {library_env.read_token}"},
        )
        assert r.status_code == 403, (
            f"READ token must not enumerate shared resource IDs, "
            f"got {r.status_code}: {r.text}"
        )

    def test_cannot_revoke_tokens_for_resource(self, library_env):
        r = TestClient(library_env.server.api).delete(
            f"{API}/users/me/tokens/by-resource",
            params={
                "resource_type": "picture",
                "resource_id": library_env.picture_ids[0],
            },
            headers={"Authorization": f"Bearer {library_env.read_token}"},
        )
        assert r.status_code == 403, (
            f"READ token must not revoke tokens, got {r.status_code}: {r.text}"
        )


# ---------------------------------------------------------------------------
# 3. Resource-scoped READ token must not access other resources
# ---------------------------------------------------------------------------


class TestResourceScopedReadTokenIsolation:
    """A token scoped to resource A must not expose resource B."""

    @pytest.fixture(autouse=True)
    def env(self, _two_set_env):
        """Reset the shared two-set library and re-mint the set-A share token.

        ``autouse`` so a test added later cannot skip the reset by forgetting to
        request the fixture. Three things happen before every test body, each
        closing a way one of the negative assertions below could pass for the
        wrong reason:

        * the vault-side rows the tests write (tags, characters, stacks) are
          dropped, so no test inherits another's library shape;
        * every token is deleted and the owner session re-established from a
          fresh login, so a revoked credential cannot masquerade as a scope
          refusal;
        * the library's **identity** is re-asserted - exactly ``{pic_a, pic_b}``
          exist, set A holds exactly ``pic_a``, set B exactly ``pic_b`` - and the
          new token is proven on an in-scope read that must return exactly
          ``{pic_a}``. A deleted picture answers a request just like a scope
          refusal, so "it is still there" has to be checked separately.
        """
        e = _two_set_env
        _reset_two_set_library(e)
        _reset_owner_credentials(e.server, e.owner_client)
        token_a = _mint_read_token(
            e.owner_client,
            "set A token",
            resource_type="picture_set",
            resource_id=e.set_a,
        )

        r = e.owner_client.get(f"{API}/pictures")
        assert r.status_code == 200, r.text
        assert {p["id"] for p in r.json()} == {e.pic_a, e.pic_b}, (
            "the shared two-set library no longer holds exactly its two "
            f"fixture pictures: {sorted(p['id'] for p in r.json())}"
        )
        assert _picture_set_members(e.server, e.set_a) == {e.pic_a}, (
            "set A no longer holds exactly pic_a - the scope every negative "
            "assertion below is measured against has moved"
        )
        assert _picture_set_members(e.server, e.set_b) == {e.pic_b}, (
            "set B no longer holds exactly pic_b - the out-of-scope picture "
            "every negative assertion below reaches for has moved"
        )
        assert _prove_token_reads(e.server, token_a) == {e.pic_a}, (
            "the fresh set-A token does not see exactly its own picture"
        )

        return SimpleNamespace(
            server=e.server,
            owner_client=e.owner_client,
            set_a=e.set_a,
            set_b=e.set_b,
            pic_a=e.pic_a,
            pic_b=e.pic_b,
            project=e.project,
            token_a=token_a,
        )

    def test_scoped_token_cannot_list_all_picture_sets(self, env):
        r = TestClient(env.server.api).get(
            f"{API}/picture_sets",
            headers={"Authorization": f"Bearer {env.token_a}"},
        )
        assert r.status_code == 200, r.text
        returned_ids = {ps["id"] for ps in r.json()}
        assert env.set_b not in returned_ids, (
            "Token for set A exposed set B in the listing"
        )
        assert env.set_a in returned_ids, (
            "Token for set A was wrongly blocked from its own set in the listing"
        )

    def test_scoped_token_cannot_fetch_other_picture_set(self, env):
        r = TestClient(env.server.api).get(
            f"{API}/picture_sets/{env.set_b}",
            headers={"Authorization": f"Bearer {env.token_a}"},
        )
        assert r.status_code in {403, 404}, (
            f"Token for set A must not read set B, got {r.status_code}: {r.text}"
        )
        # Positive: its own set is still readable (over-blocking is a regression).
        r = TestClient(env.server.api).get(
            f"{API}/picture_sets/{env.set_a}",
            headers={"Authorization": f"Bearer {env.token_a}"},
        )
        assert r.status_code == 200, (
            f"Token for set A was wrongly blocked from set A: {r.text}"
        )

    def test_scoped_token_cannot_access_pictures_outside_set(self, env):
        r = TestClient(env.server.api).get(
            f"{API}/pictures/{env.pic_b}/metadata",
            headers={"Authorization": f"Bearer {env.token_a}"},
        )
        assert r.status_code in {403, 404}, (
            f"Token for set A must not read picture from set B, "
            f"got {r.status_code}: {r.text}"
        )
        r = TestClient(env.server.api).get(
            f"{API}/pictures/{env.pic_a}/metadata",
            headers={"Authorization": f"Bearer {env.token_a}"},
        )
        assert r.status_code == 200, (
            f"Token for set A was wrongly blocked from its own picture: {r.text}"
        )

    def test_stats_cannot_leak_out_of_scope_pictures(self, env):
        """GET /pictures/stats must be limited to the token's authorised set."""
        r = TestClient(env.server.api).get(
            f"{API}/pictures/stats",
            headers={"Authorization": f"Bearer {env.token_a}"},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        # Exactly one pins both directions: 2 is the leak, 0 is over-blocking.
        assert data["total"] == 1, (
            f"Token for set A (1 picture) reported total={data['total']}; "
            "expected exactly 1 (2 leaks set B, 0 over-blocks set A)"
        )

    def test_search_cannot_leak_out_of_scope_pictures(self, env):
        """GET /pictures/search must not return pictures outside the token's set."""
        r = TestClient(env.server.api).get(
            f"{API}/pictures/search",
            params={"query": "picture"},
            headers={"Authorization": f"Bearer {env.token_a}"},
        )
        assert r.status_code == 200, r.text
        result_ids = {p["id"] for p in r.json()}
        assert env.pic_b not in result_ids, (
            "Token for set A returned picture from set B in /pictures/search"
        )

    def test_likeness_groups_cannot_leak_out_of_scope_pictures(self, env):
        """GET /pictures/likeness-groups must not include pictures outside the token's set."""
        r = TestClient(env.server.api).get(
            f"{API}/pictures/likeness-groups",
            headers={"Authorization": f"Bearer {env.token_a}"},
        )
        assert r.status_code == 200, r.text
        all_ids = {pid for group in r.json() for pid in group}
        assert env.pic_b not in all_ids, (
            "Token for set A returned picture from set B in /pictures/likeness-groups"
        )

    def test_picture_scoped_token_cannot_list_whole_library(self, env):
        """A single-picture share token must only ever resolve its own picture.

        Regression for the BOLA hole where /pictures, /pictures/stream and
        /pictures/count handled picture_set/project/character scopes but let a
        ``resource_type='picture'`` token fall through to an unrestricted query,
        leaking the entire library's grid metadata.
        """
        pic_token = _mint_read_token(
            env.owner_client,
            "single picture token",
            resource_type="picture",
            resource_id=env.pic_a,
        )

        client = TestClient(env.server.api)
        hdr = {"Authorization": f"Bearer {pic_token}"}
        for path in ("/pictures", "/pictures?fields=grid"):
            r = client.get(f"{API}{path}", headers=hdr)
            assert r.status_code == 200, r.text
            ids = {p["id"] for p in r.json()}
            # Exactly the granted picture: a wider set is the leak, an empty
            # one is over-blocking.
            assert ids == {env.pic_a}, (
                f"Single-picture token returned {ids} via {path}; "
                f"expected exactly {{{env.pic_a}}}"
            )
        # The count endpoint must not count the whole library either.
        r = client.get(f"{API}/pictures/count", headers=hdr)
        assert r.status_code == 200, r.text
        assert r.json().get("count") == 1, (
            f"Single-picture token saw count={r.json().get('count')}; "
            "expected exactly 1"
        )

    def test_picture_scoped_token_cannot_widen_via_character_likeness_sort(self, env):
        """CVE-class regression: sort=CHARACTER_LIKENESS resolves candidates
        without ever consulting query_params["id"] (see the "CHARACTER_LIKENESS
        sort branch is a known pre-existing exception" comment in
        select_pictures_for_listing, pixlstash/routes/pictures/_listing.py). A
        single-picture share token narrows scope only by mutating
        query_params["id"] (scope_picture_id), so this sort mode is a total
        bypass of that scope -- the same BOLA class as
        test_picture_scoped_token_cannot_list_whole_library above, on a sort
        mode that test didn't cover.

        find_pictures_by_character_likeness_sql is stubbed (no GPU/face
        pipeline needed, same technique as
        test_character_likeness_query_respects_project_filter in
        test_server.py) to simply echo back whatever candidate_ids it was
        given by _listing.py, so this test isolates exactly the wiring
        question: does the picture-scope narrowing ever reach that call.
        """
        r = env.owner_client.post(f"{API}/characters", json={"name": "Ref"})
        assert r.status_code == 200, r.text
        ref_char_id = r.json()["character"]["id"]

        pic_token = _mint_read_token(
            env.owner_client,
            "single picture token",
            resource_type="picture",
            resource_id=env.pic_a,
        )

        def fake_find_pictures_by_character_likeness_sql(
            _server,
            _character_id,
            _reference_character_id,
            _offset,
            _limit,
            _descending,
            candidate_ids=None,
            deleted_only=False,
            stack_leaders_only=False,
        ):
            # Mirrors the real function's contract: candidate_ids is
            # None (no restriction) unless a set/character/project
            # scope narrowed it upstream. Echo both known pictures
            # when unrestricted, exactly like an unfiltered
            # Face-join query would.
            ids = (
                sorted(set(candidate_ids)) if candidate_ids else [env.pic_a, env.pic_b]
            )
            return [{"id": pid, "character_likeness": 0.0} for pid in ids]

        client = TestClient(env.server.api)
        hdr = {"Authorization": f"Bearer {pic_token}"}
        with patch.object(
            listing_module,
            "find_pictures_by_character_likeness_sql",
            fake_find_pictures_by_character_likeness_sql,
        ):
            r = client.get(
                f"{API}/pictures",
                params={
                    "sort": "CHARACTER_LIKENESS",
                    "reference_character_id": str(ref_char_id),
                },
                headers=hdr,
            )
        assert r.status_code == 200, r.text
        ids = {p["id"] for p in r.json()}
        # Exactly the granted picture: the stub echoes back whatever
        # candidate_ids it was handed, so a wider set means the narrowing never
        # reached the call and an empty one means it over-narrowed.
        assert ids == {env.pic_a}, (
            "Single-picture token returned "
            f"{ids} via sort=CHARACTER_LIKENESS; expected {{{env.pic_a}}}"
        )

    def test_scoped_token_cannot_read_other_picture_tags(self, env):
        """Set-A token must not read tags/predictions of a set-B picture, but
        must still read its own (no over-blocking)."""
        client = TestClient(env.server.api)
        hdr = {"Authorization": f"Bearer {env.token_a}"}
        for path in (
            f"/pictures/{env.pic_b}/tags",
            f"/pictures/{env.pic_b}/tag_predictions",
        ):
            r = client.get(f"{API}{path}", headers=hdr)
            assert r.status_code in {403, 404}, (
                f"Set-A token read {path} belonging to set B, "
                f"got {r.status_code}: {r.text}"
            )
        # In-scope picture must still be readable.
        r = client.get(f"{API}/pictures/{env.pic_a}/tags", headers=hdr)
        assert r.status_code == 200, (
            f"Set-A token wrongly blocked from its own picture's tags: {r.text}"
        )

    def test_scoped_token_cannot_read_cross_resource_summaries(self, env):
        """A picture_set-scoped token must not read project or character
        summaries (different resource type) or aggregate category counts.

        The project id is a **real** one from the fixture, so the 403 cannot be
        a "no such project" answer wearing a scope refusal's status code. The
        owner reads the same path successfully at the end, which is what makes
        the refusal above about the token rather than about the object.
        """
        client = TestClient(env.server.api)
        hdr = {"Authorization": f"Bearer {env.token_a}"}
        for path in (
            f"/projects/{env.project}/summary",
            "/characters/1/summary",
            "/characters/ALL/summary",
        ):
            r = client.get(f"{API}{path}", headers=hdr)
            assert r.status_code == 403, (
                f"picture_set token reached {path}, got {r.status_code}: {r.text}"
            )

        r = env.owner_client.get(f"{API}/projects/{env.project}/summary")
        assert r.status_code == 200, (
            "the project the refusal above was measured against does not "
            f"exist for the owner either: {r.status_code} {r.text}"
        )

    def test_scoped_token_cannot_list_project_attachments(self, env):
        """A picture_set-scoped token must be rejected by the project-attachment
        endpoints (wrong resource type), regardless of include_attachments."""
        client = TestClient(env.server.api)
        hdr = {"Authorization": f"Bearer {env.token_a}"}
        r = client.get(f"{API}/projects/{env.project}/attachments", headers=hdr)
        assert r.status_code == 403, (
            f"picture_set token reached project attachments, "
            f"got {r.status_code}: {r.text}"
        )
        r = env.owner_client.get(f"{API}/projects/{env.project}/attachments")
        assert r.status_code == 200, (
            "the project the refusal above was measured against is not "
            f"readable by the owner either: {r.status_code} {r.text}"
        )

    def test_project_attachments_honour_the_grant_for_any_scope(self, env):
        """The attachment opt-in is a property of the grant, not of ``READ``.

        Both directions, and both scopes. ``list_attachments`` used to skip its
        refusal unless the scope was literally ``READ``, so a scope that was not
        READ read the attachments regardless of its own ``include_attachments``
        flag - item 3 of issue #962's blocking preconditions. The 200 leg is what
        stops the fix from being over-blocking: a grant that *does* carry the
        flag must still open.
        """
        client = TestClient(env.server.api)
        without = _mint_read_token(
            env.owner_client,
            "project token, attachments withheld",
            resource_type="project",
            resource_id=env.project,
            include_attachments=False,
        )
        path = f"{API}/projects/{env.project}/attachments"

        # Positive control: the owner can read the endpoint the refusals below
        # are measured against, so a 403 is a scope decision and not a dead path.
        assert env.owner_client.get(path).status_code == 200

        for scope in ("READ", "WRITE"):
            token = _rescope(env.server, without, scope)
            r = client.get(path, headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 403, (
                f"a {scope}-scoped grant without include_attachments reached "
                f"project attachments: {r.status_code} {r.text}"
            )

        _reset_owner_credentials(env.server, env.owner_client)
        with_attachments = _mint_read_token(
            env.owner_client,
            "project token, attachments granted",
            resource_type="project",
            resource_id=env.project,
            include_attachments=True,
        )
        r = client.get(path, headers={"Authorization": f"Bearer {with_attachments}"})
        assert r.status_code == 200, (
            f"a grant that carries include_attachments must still open: {r.text}"
        )

    def test_export_cannot_include_out_of_scope_pictures(self, env):
        """GET /pictures/export must not package pictures outside the token's set."""
        scoped = TestClient(env.server.api)
        r = scoped.get(
            f"{API}/pictures/export",
            headers={"Authorization": f"Bearer {env.token_a}"},
        )
        assert r.status_code == 200, r.text
        task_id = r.json()["task_id"]

        deadline = time.monotonic() + 30
        status = None
        while time.monotonic() < deadline:
            sr = scoped.get(
                f"{API}/pictures/export/status",
                params={"task_id": task_id},
                headers={"Authorization": f"Bearer {env.token_a}"},
            )
            assert sr.status_code == 200, sr.text
            status = sr.json()
            if status["status"] in ("completed", "failed"):
                break
            time.sleep(0.1)

        assert status and status["status"] == "completed", (
            f"Export task did not complete in time: {status}"
        )
        # `total` is set after scope filtering - must be 1, not 2
        assert status["total"] == 1, (
            f"Export for set A (1 picture) reported total={status['total']}; "
            "out-of-scope pictures from set B may have been included"
        )

    # -- Batch POST endpoints (READ_SAFE_POST_PATHS) must scope-filter -------
    #
    # These endpoints take a client-supplied picture-id list and are exempt
    # from the "block non-GET for READ tokens" rule.  A scoped token must only
    # ever receive data for ids inside its grant; posting an out-of-scope id
    # (pic_b) must never leak it back.  Regression guard for the BOLA fix.

    def test_bulk_fetch_tags_cannot_leak_out_of_scope_pictures(self, env):
        r = TestClient(env.server.api).post(
            f"{API}/pictures/tags/bulk_fetch",
            json={"picture_ids": [env.pic_a, env.pic_b]},
            headers={"Authorization": f"Bearer {env.token_a}"},
        )
        assert r.status_code == 200, r.text
        returned_ids = {entry["id"] for entry in r.json()}
        assert env.pic_b not in returned_ids, (
            "bulk_fetch tags leaked out-of-scope picture B to a set-A token"
        )
        assert returned_ids <= {env.pic_a}, (
            f"bulk_fetch tags returned unexpected ids {returned_ids}"
        )

    def test_thumbnails_batch_cannot_leak_out_of_scope_pictures(self, env):
        r = TestClient(env.server.api).post(
            f"{API}/pictures/thumbnails",
            json={"ids": [env.pic_a, env.pic_b]},
            headers={"Authorization": f"Bearer {env.token_a}"},
        )
        assert r.status_code == 200, r.text
        assert str(env.pic_b) not in r.json(), (
            "thumbnail batch leaked out-of-scope picture B to a set-A token"
        )

    def test_set_membership_cannot_leak_out_of_scope_pictures(self, env):
        r = TestClient(env.server.api).post(
            f"{API}/picture_sets/membership",
            json={"picture_ids": [env.pic_a, env.pic_b]},
            headers={"Authorization": f"Bearer {env.token_a}"},
        )
        assert r.status_code == 200, r.text
        result = r.json()
        assert str(env.set_b) not in result, (
            "set membership leaked out-of-scope set B to a set-A token"
        )
        leaked = {pid for pids in result.values() for pid in pids}
        assert env.pic_b not in leaked, (
            "set membership leaked out-of-scope picture B to a set-A token"
        )

    def test_project_membership_cannot_leak_out_of_scope_pictures(self, env):
        r = TestClient(env.server.api).post(
            f"{API}/projects/membership",
            json={"picture_ids": [env.pic_a, env.pic_b]},
            headers={"Authorization": f"Bearer {env.token_a}"},
        )
        assert r.status_code == 200, r.text
        result = r.json()
        assert env.pic_b not in result["unassigned_picture_ids"], (
            "project membership leaked out-of-scope picture B (unassigned)"
        )
        leaked = {
            pid for pids in result["project_assignments"].values() for pid in pids
        }
        assert env.pic_b not in leaked, (
            "project membership leaked out-of-scope picture B (assigned)"
        )

    def test_character_membership_cannot_leak_out_of_scope_pictures(self, env):
        r = TestClient(env.server.api).post(
            f"{API}/characters/membership",
            json={"picture_ids": [env.pic_a, env.pic_b]},
            headers={"Authorization": f"Bearer {env.token_a}"},
        )
        assert r.status_code == 200, r.text
        result = r.json()
        assert env.pic_b not in result["pictures_with_faces"], (
            "character membership leaked out-of-scope picture B (faces)"
        )
        leaked = {
            pid for pids in result["character_assignments"].values() for pid in pids
        }
        assert env.pic_b not in leaked, (
            "character membership leaked out-of-scope picture B (assigned)"
        )

    def test_unassigned_listing_cannot_leak_out_of_scope_pictures(self, env):
        """character_id=UNASSIGNED must honour token scope. Regression for the
        bypass where an empty intersected id list fell through to no filter and
        the set/character scope was never applied to the UNASSIGNED branch."""
        client = TestClient(env.server.api)
        hdr = {"Authorization": f"Bearer {env.token_a}"}
        listed = None
        for path in (
            "/pictures?character_id=UNASSIGNED",
            f"/pictures?character_id=UNASSIGNED&id={env.pic_b}",
        ):
            r = client.get(f"{API}{path}", headers=hdr)
            assert r.status_code == 200, r.text
            ids = {p["id"] for p in r.json()}
            assert env.pic_b not in ids, (
                f"UNASSIGNED listing leaked out-of-scope picture via {path}: {ids}"
            )
            assert ids <= {env.pic_a}, (
                f"UNASSIGNED listing returned ids outside the grant via {path}: {ids}"
            )
            if listed is None:
                listed = ids
        r = client.get(f"{API}/pictures/count?character_id=UNASSIGNED", headers=hdr)
        assert r.status_code == 200, r.text
        # Pinned against the row set rather than a literal: the fixture pictures
        # carry a "no face found" sentinel, so whether pic_a lands in the
        # UNASSIGNED bucket at all is the endpoint's business - but the count
        # and the listing must agree, and neither may exceed the grant.
        assert r.json().get("count") == len(listed), (
            f"UNASSIGNED count {r.json()} disagrees with the rows it returned: "
            f"{sorted(listed)}"
        )

    def test_scoped_token_cannot_read_other_picture_fields(self, env):
        """GET /pictures/{id}/{field} must enforce scope. Regression: it sat
        unguarded between get_picture and get_picture_metadata."""
        client = TestClient(env.server.api)
        hdr = {"Authorization": f"Bearer {env.token_a}"}
        for field in ("file_path", "width", "thumbnail"):
            r = client.get(f"{API}/pictures/{env.pic_b}/{field}", headers=hdr)
            assert r.status_code in {403, 404}, (
                f"Set-A token read pic_b field '{field}', got {r.status_code}: {r.text}"
            )
        # In-scope field still readable (no over-block).
        r = client.get(f"{API}/pictures/{env.pic_a}/width", headers=hdr)
        assert r.status_code == 200, (
            f"Set-A token wrongly blocked from its own picture field: {r.text}"
        )

    def test_scoped_token_cannot_read_other_picture_character_likeness(self, env):
        """GET /pictures/{id}/character_likeness must enforce scope. Regression
        (R2): it sat unguarded alongside get_picture / get_picture_metadata /
        get_picture_field and leaked picture existence, likeness scores, and the
        face-extraction ``ready`` flag to any scoped token.

        ML helpers are stubbed so the in-scope (positive) path is deterministic
        and GPU-free; the out-of-scope (negative) path is rejected by
        ``enforce_picture_scope`` before any ML/DB work runs.
        """
        # A reference character is required by the endpoint's query.
        r = env.owner_client.post(f"{API}/characters", json={"name": "Ref"})
        assert r.status_code == 200, r.text
        ref_char_id = r.json()["character"]["id"]

        client = TestClient(env.server.api)
        hdr = {"Authorization": f"Bearer {env.token_a}"}
        params = {"reference_character_id": str(ref_char_id)}

        # Negative: set-A token must not read set-B picture likeness.
        r = client.get(
            f"{API}/pictures/{env.pic_b}/character_likeness",
            params=params,
            headers=hdr,
        )
        assert r.status_code in {403, 404}, (
            f"Set-A token read pic_b character_likeness, got {r.status_code}: {r.text}"
        )

        # Positive: in-scope picture still readable (no over-block).
        # Stub the ML helpers so any face-bearing path is deterministic
        # and never touches the GPU.
        def _fake_select_reference_faces(session, character_id, max_refs=10):
            return []

        def _fake_compute_likeness(reference_faces, candidate_faces):
            return {}

        with (
            patch.object(
                likeness_module,
                "select_reference_faces_for_character",
                _fake_select_reference_faces,
            ),
            patch.object(
                likeness_module,
                "compute_character_likeness_for_faces",
                _fake_compute_likeness,
            ),
        ):
            r = client.get(
                f"{API}/pictures/{env.pic_a}/character_likeness",
                params=params,
                headers=hdr,
            )
        assert r.status_code == 200, (
            f"Set-A token wrongly blocked from its own picture's "
            f"character_likeness: {r.text}"
        )
        body = r.json()
        assert body["picture_id"] == env.pic_a
        assert "ready" in body, (
            f"character_likeness response missing 'ready' flag: {body}"
        )

    def test_scoped_token_cannot_read_stack_outside_scope(self, env):
        """Stack read endpoints must not leak out-of-scope pictures. Regression
        for unscoped /stacks/{id}/pictures and /pictures/{id}/stack.

        Uses a single-picture token (allow-set is exactly {pic_a}) so that
        stacking pic_a with pic_b cannot widen scope via set-membership
        propagation the way a set-scoped token would.

        The stack this creates is dropped again by ``env``'s reset, so the next
        test still sees the unstacked two-picture library it was written for.
        """
        # Single-picture share token for pic_a only.
        pic_token = _mint_read_token(
            env.owner_client,
            "single picture token",
            resource_type="picture",
            resource_id=env.pic_a,
        )

        # Owner stacks pic_a and pic_b together.
        r = env.owner_client.post(
            f"{API}/stacks", json={"picture_ids": [env.pic_a, env.pic_b]}
        )
        assert r.status_code in {200, 201}, r.text
        r = env.owner_client.get(f"{API}/pictures/{env.pic_a}/stack")
        assert r.status_code == 200, r.text
        stack_id = r.json().get("id")
        assert stack_id is not None, f"no stack id in {r.json()}"

        client = TestClient(env.server.api)
        hdr = {"Authorization": f"Bearer {pic_token}"}
        r = client.get(f"{API}/stacks/{stack_id}/pictures?fields=metadata", headers=hdr)
        assert r.status_code in {200, 404}, r.text
        if r.status_code == 200:
            ids = {row.get("id") for row in r.json()}
            assert ids <= {env.pic_a}, (
                f"Stack pictures leaked out-of-scope picture(s): {ids}"
            )
        r = client.get(f"{API}/pictures/{env.pic_b}/stack", headers=hdr)
        assert r.status_code in {403, 404}, (
            f"pic_a token learned pic_b's stack, got {r.status_code}: {r.text}"
        )

    def _seed_comfyui_vocab(self, server, pic_a, pic_b):
        """Write distinct ComfyUI model/LoRA JSON onto each picture.

        These columns are pipeline-populated JSON arrays with no owner-facing
        API, so the test seeds them directly via the DB task runner (mirrors the
        direct-write pattern in test_characters_api.py / test_many_to_many).
        """

        def _set(session):
            pic_a_row = session.get(Picture, pic_a)
            pic_b_row = session.get(Picture, pic_b)
            pic_a_row.comfyui_models = '["model-a-only"]'
            pic_a_row.comfyui_loras = '["lora-a-only"]'
            pic_b_row.comfyui_models = '["model-b-only"]'
            pic_b_row.comfyui_loras = '["lora-b-only"]'
            session.add(pic_a_row)
            session.add(pic_b_row)
            session.commit()

        server.vault.db.run_task(_set)

    def test_comfyui_models_cannot_leak_out_of_scope_vocab(self, env):
        """GET /pictures/comfyui_models must only return model names drawn from
        pictures inside the token's grant; owner/unscoped sees the union."""
        self._seed_comfyui_vocab(env.server, env.pic_a, env.pic_b)

        # Negative: Set-A token must not see Set-B-only models.
        r = TestClient(env.server.api).get(
            f"{API}/pictures/comfyui_models",
            headers={"Authorization": f"Bearer {env.token_a}"},
        )
        assert r.status_code == 200, r.text
        scoped = set(r.json())
        assert "model-b-only" not in scoped, (
            f"Set-A token leaked out-of-scope model vocab: {scoped}"
        )
        assert scoped <= {"model-a-only"}, (
            f"Set-A token returned unexpected models: {scoped}"
        )

        # Positive: owner/unscoped sees the union (no over-block).
        r = env.owner_client.get(f"{API}/pictures/comfyui_models")
        assert r.status_code == 200, r.text
        owner_models = set(r.json())
        assert {"model-a-only", "model-b-only"} <= owner_models, (
            f"Owner did not see full model vocab: {owner_models}"
        )

    def test_comfyui_loras_cannot_leak_out_of_scope_vocab(self, env):
        """GET /pictures/comfyui_loras must only return LoRA names drawn from
        pictures inside the token's grant; owner/unscoped sees the union."""
        self._seed_comfyui_vocab(env.server, env.pic_a, env.pic_b)

        # Negative: Set-A token must not see Set-B-only LoRAs.
        r = TestClient(env.server.api).get(
            f"{API}/pictures/comfyui_loras",
            headers={"Authorization": f"Bearer {env.token_a}"},
        )
        assert r.status_code == 200, r.text
        scoped = set(r.json())
        assert "lora-b-only" not in scoped, (
            f"Set-A token leaked out-of-scope LoRA vocab: {scoped}"
        )
        assert scoped <= {"lora-a-only"}, (
            f"Set-A token returned unexpected LoRAs: {scoped}"
        )

        # Positive: owner/unscoped sees the union (no over-block).
        r = env.owner_client.get(f"{API}/pictures/comfyui_loras")
        assert r.status_code == 200, r.text
        owner_loras = set(r.json())
        assert {"lora-a-only", "lora-b-only"} <= owner_loras, (
            f"Owner did not see full LoRA vocab: {owner_loras}"
        )

    def test_list_all_tags_cannot_leak_out_of_scope_vocab(self, env):
        """GET /tags must only return tag values (and counts) drawn from
        pictures inside the token's grant; owner/unscoped sees the union.

        ``env`` truncates the tag table before every test, so the exact count
        asserted below is this test's own tag and nothing another test left
        behind.
        """
        # Seed distinct tags per picture via the owner API.
        r = env.owner_client.post(
            f"{API}/pictures/{env.pic_a}/tags", json={"tag": "tag-a-only"}
        )
        assert r.status_code == 200, r.text
        r = env.owner_client.post(
            f"{API}/pictures/{env.pic_b}/tags", json={"tag": "tag-b-only"}
        )
        assert r.status_code == 200, r.text

        # Negative: Set-A token must not see the Set-B-only tag, and the
        # counts it does see must reflect only the in-scope picture.
        r = TestClient(env.server.api).get(
            f"{API}/tags",
            headers={"Authorization": f"Bearer {env.token_a}"},
        )
        assert r.status_code == 200, r.text
        scoped = {row["tag"]: row["count"] for row in r.json()}
        assert "tag-b-only" not in scoped, (
            f"Set-A token leaked out-of-scope tag vocab: {scoped}"
        )
        assert scoped.get("tag-a-only") == 1, (
            f"Set-A token tag count wrong (must reflect only Set A): {scoped}"
        )

        # Positive: owner/unscoped sees the union (no over-block).
        r = env.owner_client.get(f"{API}/tags")
        assert r.status_code == 200, r.text
        owner_tags = {row["tag"] for row in r.json()}
        assert {"tag-a-only", "tag-b-only"} <= owner_tags, (
            f"Owner did not see full tag vocab: {owner_tags}"
        )

    def test_scoped_token_cannot_read_other_picture_comfyui_workflow(self, env):
        """GET /comfyui/pictures/{id}/workflow must enforce scope (R1).

        Negative: a Set-A token is rejected on a Set-B picture by
        enforce_picture_scope before any DB read or metadata extraction.
        Positive: the in-scope picture is still served. The workflow extractor
        is stubbed so the in-scope call returns a deterministic 200 (the plain
        test PNG carries no embedded workflow), proving the scope gate let the
        request through rather than over-blocking.
        """
        import pixlstash.routes.comfyui as comfyui_module

        client = TestClient(env.server.api)
        hdr = {"Authorization": f"Bearer {env.token_a}"}

        def _fake_extract(_metadata):
            return {"workflow": {"nodes": []}, "models": [], "loras": []}

        # Negative: Set-A token must not reach Set-B picture's workflow.
        # Stubbed extractor would happily return data, so a pass here
        # proves the scope gate (not a missing-workflow 404) blocked it.
        with patch.object(comfyui_module, "extract_comfy_workflow_info", _fake_extract):
            r = client.get(f"{API}/comfyui/pictures/{env.pic_b}/workflow", headers=hdr)
            assert r.status_code in {403, 404}, (
                f"Set-A token read pic_b ComfyUI workflow, "
                f"got {r.status_code}: {r.text}"
            )

            # Positive: the in-scope picture is still served (no
            # over-block) and returns the extracted workflow.
            r = client.get(f"{API}/comfyui/pictures/{env.pic_a}/workflow", headers=hdr)
            assert r.status_code == 200, (
                f"Set-A token wrongly scope-blocked from its own "
                f"picture's workflow: {r.status_code} {r.text}"
            )


# ---------------------------------------------------------------------------
# 3b. The ComfyUI stack filter must not widen a scoped token's grant
# ---------------------------------------------------------------------------


class TestComfyuiFilterCannotEscapeTokenScope:
    """Token-level regression for the ComfyUI stack-member scope escape.

    ``Picture.find`` expands a stack-collapsed grid so a stack leader shows when
    *any* member of its stack was made with the filtered model or LoRA.  That
    expansion is a raw ``text()`` fragment, and ``text()`` is opaque to
    SQLAlchemy, so nothing parenthesises its ``OR`` for us.  ``AND`` binds
    tighter than ``OR``, so an unwrapped fragment renders as ``<all other
    predicates> AND self_match OR member_match`` and the member branch escapes
    every ANDed predicate -- including the ``id IN (...)`` narrowing that a
    share token's scope is expressed as.

    ``/api/v1/pictures`` and its siblings are declared ``SCOPED_LIST`` with
    ``scope_aware=True`` (``pixlstash/authz/registry.py``), which means the
    AuthzGate deliberately does *not* object-check the rows that come back: the
    handler's narrowing is the only enforcement there is.  So this is a property
    of the route, not of the query builder, and it needs a test that actually
    mints a token.  ``tests/test_comfyui_stack_filter.py`` calls
    ``Picture.find`` directly and would still pass if the route stopped
    narrowing altogether.

    The fixture is a stack that **straddles** the token's scope boundary, which
    is the only shape that reproduces the leak.  Picture-set membership is
    stack-atomic on add, so a *picture*-scoped share token is the reliable way
    to straddle; the set-scoped case only straddles when the pictures are
    stacked after set membership is granted, which the last test does.
    """

    FILTER_PARAMS = STACK_FILTER_PARAMS

    @pytest.fixture(autouse=True)
    def env(self, _comfyui_stack_env):
        """Re-mint the single-picture share token and re-pin the stack layout.

        Nothing in this class writes, so the reset is about the credential and
        about proving the fixture still *is* the straddling shape these
        assertions depend on: the six pictures at their exact file paths, and
        the two stacks holding exactly the members they were seeded with. The
        layout is asserted by identity - a picture quietly moved between stacks
        would turn "the filter did not widen scope" into a tautology.
        """
        e = _comfyui_stack_env
        _reset_owner_credentials(e.server, e.owner_client)
        token = _mint_read_token(
            e.owner_client,
            "single picture share",
            resource_type="picture",
            resource_id=e.ids["cover"],
        )

        layout = _stack_layout(e.server)
        assert set(layout) == set(_STACK_PATHS.values()), (
            "the shared ComfyUI stack library no longer holds exactly its "
            f"fixture pictures: {sorted(layout)}"
        )
        shared_stack = {layout[_STACK_PATHS[n]][0] for n in _SHARED_STACK}
        other_stack = {layout[_STACK_PATHS[n]][0] for n in _OTHER_STACK}
        assert len(shared_stack) == 1 and None not in shared_stack, (
            f"the straddled stack has come apart: {shared_stack}"
        )
        assert len(other_stack) == 1 and None not in other_stack, (
            f"the second stack has come apart: {other_stack}"
        )
        assert shared_stack != other_stack, "the two stacks have merged"
        assert layout[_STACK_PATHS["lone"]][0] is None, (
            "the loose picture has been stacked"
        )
        assert _prove_token_reads(
            e.server, token, "/pictures", fields="grid", limit=500
        ) == {e.ids["cover"]}, (
            "the fresh single-picture token does not see exactly its own picture"
        )

        return SimpleNamespace(
            server=e.server, owner_client=e.owner_client, ids=e.ids, token=token
        )

    def _filter_value(self, filter_param: str) -> str:
        return STACK_MODEL if filter_param == "comfyui_model" else STACK_LORA

    @staticmethod
    def _ids_from(response) -> set[int]:
        """Pull the id set out of a list response or a stream envelope."""
        assert response.status_code == 200, response.text
        body = response.json()
        if isinstance(body, dict):
            body = body["pictures"]
        return {row["id"] for row in body}

    def _scoped_get(self, server, token: str, path: str, params: dict):
        return TestClient(server.api).get(
            f"{API}{path}",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )

    def test_picture_token_cannot_widen_the_grid_via_comfyui_filter(self, env):
        """``GET /pictures?fields=grid&comfyui_model=...`` stays inside scope.

        ``fields=grid`` implies ``stack_leaders_only``, so this is the default
        share-gallery view: the exact request a share-link visitor's browser
        makes when they touch the ComfyUI filter chips.  Under the bug the
        response carried grid rows -- ``file_path`` included -- for every member
        of every stack that contained a match, library-wide.
        """
        for filter_param in self.FILTER_PARAMS:
            r = self._scoped_get(
                env.server,
                env.token,
                "/pictures",
                {
                    "fields": "grid",
                    "limit": 500,
                    filter_param: self._filter_value(filter_param),
                },
            )
            returned = self._ids_from(r)
            out_of_scope = returned - {env.ids["cover"]}
            assert not out_of_scope, (
                f"Single-picture token leaked ids {sorted(out_of_scope)} "
                f"via /pictures?fields=grid&{filter_param}=..."
            )
            # Positive: the granted picture is still served. The whole
            # point of the narrowing is to return this row and no other.
            assert returned == {env.ids["cover"]}, (
                f"In-scope picture {env.ids['cover']} was dropped from "
                f"/pictures?fields=grid&{filter_param}=...: {returned}"
            )
            # The grid projection carries file_path, so the leak handed
            # out on-disk locations of pictures that were never shared.
            paths = {row.get("file_path") for row in r.json()}
            assert paths == {_STACK_PATHS["cover"]}, (
                f"Grid rows exposed out-of-scope file paths: {paths}"
            )

    def test_picture_token_cannot_widen_the_stream_via_comfyui_filter(self, env):
        """``/pictures/stream`` is a separate handler and leaked identically.

        Both the ``fields=grid`` form and the explicit ``stack_leaders_only``
        form are exercised, because the stream endpoint reaches the collapsed
        query by two different routes.
        """
        for filter_param in self.FILTER_PARAMS:
            value = self._filter_value(filter_param)
            for extra in (
                {"fields": "grid"},
                {"stack_leaders_only": "true"},
            ):
                r = self._scoped_get(
                    env.server,
                    env.token,
                    "/pictures/stream",
                    {"batch_limit": 500, filter_param: value, **extra},
                )
                returned = self._ids_from(r)
                out_of_scope = returned - {env.ids["cover"]}
                assert not out_of_scope, (
                    f"Single-picture token leaked ids "
                    f"{sorted(out_of_scope)} via /pictures/stream "
                    f"({extra}, {filter_param})"
                )
                assert returned == {env.ids["cover"]}, (
                    f"In-scope picture {env.ids['cover']} was dropped from "
                    f"/pictures/stream ({extra}, {filter_param}): "
                    f"{returned}"
                )

    def test_picture_token_cannot_widen_the_count_via_comfyui_filter(self, env):
        """``/pictures/count`` leaked the size of the library, not just rows.

        The count runs ``SELECT COUNT(*)`` over the same WHERE clause, so the
        escaped ``OR`` inflated it to every member of every matching stack --
        5 instead of 1 in this fixture.  Nothing in the suite asserted on that
        number before.
        """
        for filter_param in self.FILTER_PARAMS:
            r = self._scoped_get(
                env.server,
                env.token,
                "/pictures/count",
                {
                    "stack_leaders_only": "true",
                    filter_param: self._filter_value(filter_param),
                },
            )
            assert r.status_code == 200, r.text
            count = r.json()["count"]
            # Exactly one: the granted picture matches the filter, so
            # this pins both directions at once -- an inflated count is
            # the leak, a zero count is over-blocking.
            assert count == 1, (
                f"Single-picture token saw count={count} for "
                f"{filter_param}; expected 1 (only picture "
                f"{env.ids['cover']} is in scope)"
            )

    def test_owner_still_gets_the_legitimate_stack_expansion(self, env):
        """The over-blocking direction: the feature the fragment exists for.

        For an unscoped owner the collapsed grid must return one tile per
        matching stack: ``cover`` (the leader itself matches), ``quiet_cover``
        (only a non-leader member matches -- the member branch) and ``lone``.
        Tightening the parentheses must not cost the member branch its reach.
        """
        for filter_param in self.FILTER_PARAMS:
            value = self._filter_value(filter_param)
            r = env.owner_client.get(
                f"{API}/pictures",
                params={"fields": "grid", "limit": 500, filter_param: value},
            )
            returned = self._ids_from(r)
            expected = {env.ids["cover"], env.ids["quiet_cover"], env.ids["lone"]}
            assert returned == expected, (
                f"Owner's collapsed grid for {filter_param} returned "
                f"{sorted(returned)}, expected {sorted(expected)} "
                "(one leader per matching stack, plus the loose match)"
            )
            r = env.owner_client.get(
                f"{API}/pictures/count",
                params={"stack_leaders_only": "true", filter_param: value},
            )
            assert r.status_code == 200, r.text
            assert r.json()["count"] == len(expected), (
                f"Owner count for {filter_param} disagrees with the "
                f"row set: {r.json()['count']} vs {len(expected)}"
            )

    def test_set_token_cannot_widen_via_comfyui_filter_on_a_late_stack(self):
        """The set-scoped straddle: pictures stacked *after* set membership.

        Adding a picture to a set pulls its whole stack in (membership is
        stack-atomic), so a set-scoped token normally cannot straddle a stack at
        all.  It can when the stack is formed afterwards, which is exactly what
        an auto-stack pass does to an already-shared set.  Same three routes,
        same expectation: only the set's own picture comes back.
        """
        with tempfile.TemporaryDirectory() as tmp:
            server = Server(f"{tmp}/server-config.json")
            server.__enter__()
            try:
                owner_client = TestClient(server.api, raise_server_exceptions=True)
                r = owner_client.post(
                    f"{API}/login",
                    json={"username": "owner", "password": "example-owner-password"},
                )
                assert r.status_code == 200, r.text

                # Order matters: seed loose pictures, put ONE of them in the set
                # while nothing is stacked (so the stack-atomic add pulls in
                # nothing), and only then form the stacks around it.
                ids = _seed_stack_pictures(server)

                r = owner_client.post(f"{API}/picture_sets", json={"name": "Shared"})
                assert r.status_code == 200, r.text
                set_id = r.json()["picture_set"]["id"]

                r = owner_client.post(
                    f"{API}/picture_sets/{set_id}/members/{ids['cover']}"
                )
                assert r.status_code in {200, 201, 204}, r.text

                _form_stacks(server, ids)

                r = owner_client.post(
                    f"{API}/users/me/token",
                    json={
                        "description": "set share",
                        "scope": "READ",
                        "resource_type": "picture_set",
                        "resource_id": set_id,
                    },
                )
                assert r.status_code == 200, r.text
                token = r.json()["token"]

                # Sanity: without the ComfyUI filter the token already sees only
                # the cover, so any widening below is the filter's doing.
                baseline = self._ids_from(
                    self._scoped_get(
                        server, token, "/pictures", {"fields": "grid", "limit": 500}
                    )
                )
                assert baseline == {ids["cover"]}, (
                    f"Set token's unfiltered grid is not the straddle shape this "
                    f"test needs: {baseline}"
                )

                for filter_param in self.FILTER_PARAMS:
                    value = self._filter_value(filter_param)
                    returned = self._ids_from(
                        self._scoped_get(
                            server,
                            token,
                            "/pictures",
                            {"fields": "grid", "limit": 500, filter_param: value},
                        )
                    )
                    assert returned == {ids["cover"]}, (
                        f"Set token widened via /pictures {filter_param}: "
                        f"{sorted(returned)}"
                    )

                    returned = self._ids_from(
                        self._scoped_get(
                            server,
                            token,
                            "/pictures/stream",
                            {"fields": "grid", "batch_limit": 500, filter_param: value},
                        )
                    )
                    assert returned == {ids["cover"]}, (
                        f"Set token widened via /pictures/stream {filter_param}: "
                        f"{sorted(returned)}"
                    )

                    r = self._scoped_get(
                        server,
                        token,
                        "/pictures/count",
                        {"stack_leaders_only": "true", filter_param: value},
                    )
                    assert r.status_code == 200, r.text
                    assert r.json()["count"] == 1, (
                        f"Set token widened /pictures/count for {filter_param}: "
                        f"{r.json()['count']}"
                    )
            finally:
                server.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# 4. Expired token is rejected
# ---------------------------------------------------------------------------


class TestExpiredToken(_SharedPictureLibrary):
    """Expiry is honoured in both directions on the same shared library.

    The negative (expired → 401) and the positive (expiring today → 200) run
    against the same server and the same freshly re-minted credentials, so the
    401 can only be about the expiry stamp.
    """

    def test_expired_token_is_rejected(self, library_env):
        past = (datetime.utcnow() - timedelta(days=1)).isoformat()
        r = library_env.owner_client.post(
            f"{API}/users/me/token",
            json={
                "description": "expired",
                "scope": "READ",
                "expires_at": past,
            },
        )
        assert r.status_code == 200, r.text
        expired_token = r.json()["token"]

        r = TestClient(library_env.server.api).get(
            f"{API}/pictures",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert r.status_code == 401, (
            f"Expired token should be rejected, got {r.status_code}: {r.text}"
        )

    def test_today_token_still_valid(self, library_env):
        """A token with expires_at=today (normalized to end-of-day) should still work."""
        today_str = datetime.utcnow().strftime("%Y-%m-%d")
        r = library_env.owner_client.post(
            f"{API}/users/me/token",
            json={
                "description": "today token",
                "scope": "READ",
                "expires_at": today_str,
            },
        )
        assert r.status_code == 200, r.text
        today_token = r.json()["token"]

        r = TestClient(library_env.server.api).get(
            f"{API}/pictures",
            headers={"Authorization": f"Bearer {today_token}"},
        )
        assert r.status_code == 200, (
            f"Token expiring today should still be valid, got {r.status_code}: {r.text}"
        )


# ---------------------------------------------------------------------------
# 5. No privilege escalation via token creation
# ---------------------------------------------------------------------------


class TestNoPrivilegeEscalation(_SharedPictureLibrary):
    def test_read_token_cannot_create_all_scope_token(self, library_env):
        r = TestClient(library_env.server.api).post(
            f"{API}/users/me/token",
            json={"description": "escalated ALL token", "scope": "ALL"},
            headers={"Authorization": f"Bearer {library_env.read_token}"},
        )
        assert r.status_code == 403, (
            f"READ token must not create ALL-scope token, got {r.status_code}: {r.text}"
        )

    def test_read_token_cannot_create_read_token(self, library_env):
        """Even creating another READ token via a READ token must be blocked."""
        r = TestClient(library_env.server.api).post(
            f"{API}/users/me/token",
            json={"description": "cloned read token", "scope": "READ"},
            headers={"Authorization": f"Bearer {library_env.read_token}"},
        )
        assert r.status_code == 403, (
            f"READ token must not clone itself, got {r.status_code}: {r.text}"
        )

    def test_invalid_token_value_rejected(self, library_env):
        """A completely fabricated token must not authenticate."""
        fake = "AAAAAAAABBBBBBBBCCCCCCCCDDDDDDDDEEEEEEEEFFFFFFFF"
        r = TestClient(library_env.server.api).get(
            f"{API}/pictures",
            headers={"Authorization": f"Bearer {fake}"},
        )
        assert r.status_code == 401, (
            f"Fabricated token should be rejected, got {r.status_code}: {r.text}"
        )

    def test_tampered_token_prefix_rejected(self, library_env):
        """Modifying the first byte of a valid token must invalidate it.

        The unmodified original was already proven to read this library by
        ``library_env``, so the 401 is about the flipped byte and nothing else.
        """
        read_token = library_env.read_token
        flipped = ("X" if read_token[0] != "X" else "Y") + read_token[1:]
        r = TestClient(library_env.server.api).get(
            f"{API}/pictures",
            headers={"Authorization": f"Bearer {flipped}"},
        )
        assert r.status_code == 401, (
            f"Tampered token prefix should be rejected, got {r.status_code}: {r.text}"
        )

    def test_token_in_query_param_only_works_for_read(self, library_env):
        """?token= query param must only accept READ-scoped tokens, not ALL."""
        r = library_env.owner_client.post(
            f"{API}/users/me/token",
            json={"description": "all scope", "scope": "ALL"},
        )
        assert r.status_code == 200, r.text
        all_token = r.json()["token"]

        r = TestClient(library_env.server.api).get(
            f"{API}/pictures",
            params={"token": all_token},
        )
        assert r.status_code == 401, (
            f"ALL-scope token in ?token= param should be rejected (only READ allowed via URL), "
            f"got {r.status_code}: {r.text}"
        )
        # Positive: the READ token this fixture minted *is* accepted the same
        # way, so the 401 above is the scope check and not a broken ?token= path.
        r = TestClient(library_env.server.api).get(
            f"{API}/pictures",
            params={"token": library_env.read_token},
        )
        assert r.status_code == 200, (
            f"READ token in ?token= param must still be accepted, "
            f"got {r.status_code}: {r.text}"
        )


# ---------------------------------------------------------------------------
# 6. Login brute-force lockout
# ---------------------------------------------------------------------------


class TestLoginBruteForce:
    """Deliberately NOT on a shared server.

    The subject of these tests *is* the server-lifetime lockout counter, and
    every other class in this module clears that counter before each test so it
    cannot poison them. A shared server would mean the thing under test is also
    the thing the reset destroys, so a "locked out" assertion could pass or fail
    for reasons that have nothing to do with the lockout. These tests import no
    pictures, so a fresh Server here costs only the boot.
    """

    def test_lockout_after_five_failures(self):
        """After 5 wrong passwords the server returns 429 with Retry-After."""
        with tempfile.TemporaryDirectory() as tmp:
            config_path = f"{tmp}/server-config.json"
            with Server(config_path) as server:
                client = TestClient(server.api, raise_server_exceptions=True)

                # Establish a password.
                r = client.post(
                    f"{API}/login",
                    json={"username": "victim", "password": "example-correct-password"},
                )
                assert r.status_code == 200, r.text

                # Hammer with wrong passwords (all ≥8 chars to pass validation).
                for i in range(5):
                    r = client.post(
                        f"{API}/login",
                        json={
                            "username": "victim",
                            "password": f"example-wrong-password-{i}",
                        },
                    )
                    assert r.status_code == 401, (
                        f"Expected 401 on attempt {i + 1}, got {r.status_code}"
                    )

                # The 6th attempt should be blocked.
                r = client.post(
                    f"{API}/login",
                    json={"username": "victim", "password": "example-wrong-password-6"},
                )
                assert r.status_code == 429, (
                    f"Expected 429 lockout after 5 failures, got {r.status_code}: {r.text}"
                )
                assert "Too many" in r.json().get("detail", ""), (
                    f"Unexpected 429 body: {r.text}"
                )

    def test_correct_password_after_lockout_still_blocked(self):
        """Even the correct password is refused while the lockout window is active."""
        with tempfile.TemporaryDirectory() as tmp:
            config_path = f"{tmp}/server-config.json"
            with Server(config_path) as server:
                client = TestClient(server.api, raise_server_exceptions=True)

                r = client.post(
                    f"{API}/login",
                    json={"username": "victim", "password": "example-correct-password"},
                )
                assert r.status_code == 200, r.text

                for i in range(5):
                    client.post(
                        f"{API}/login",
                        json={
                            "username": "victim",
                            "password": f"example-wrong-password-{i}",
                        },
                    )

                # Correct password - should still be blocked during lockout.
                r = client.post(
                    f"{API}/login",
                    json={"username": "victim", "password": "example-correct-password"},
                )
                assert r.status_code == 429, (
                    f"Correct password during lockout should still return 429, "
                    f"got {r.status_code}: {r.text}"
                )

    def test_no_lockout_for_token_endpoints(self):
        """Failed login via a bad token value should not count toward the lockout
        that gates password logins - but each bad login does; verify the counter."""
        with tempfile.TemporaryDirectory() as tmp:
            config_path = f"{tmp}/server-config.json"
            with Server(config_path) as server:
                client = TestClient(server.api, raise_server_exceptions=True)

                r = client.post(
                    f"{API}/login",
                    json={"username": "victim", "password": "example-correct-password"},
                )
                assert r.status_code == 200, r.text

                # 4 failures - not yet locked.
                for i in range(4):
                    client.post(
                        f"{API}/login",
                        json={
                            "username": "victim",
                            "password": f"example-wrong-password-{i}",
                        },
                    )

                # 5th attempt with correct password should succeed (lockout hits at 5+).
                r = client.post(
                    f"{API}/login",
                    json={"username": "victim", "password": "example-correct-password"},
                )
                assert r.status_code == 200, (
                    f"4 failures should not yet trigger lockout; "
                    f"correct password on 5th attempt must succeed. Got {r.status_code}"
                )


# ---------------------------------------------------------------------------
# 7. Rate limiter blocks public-path DDoS (login endpoint)
# ---------------------------------------------------------------------------


class TestRateLimiter:
    """Deliberately NOT on a shared server, for the same reason as
    ``TestLoginBruteForce``: the sliding window these tests assert on is exactly
    what every other class's reset empties. They import no pictures either.
    """

    def test_login_path_rate_limited_after_limit(self):
        """Exceeding _LIMIT requests to the login path returns 429 with Retry-After."""
        with patch.object(rl_module, "_LIMIT", 5):
            with tempfile.TemporaryDirectory() as tmp:
                config_path = f"{tmp}/server-config.json"
                with Server(config_path) as server:
                    client = TestClient(server.api, raise_server_exceptions=True)

                    # Set up credentials so the login path actually processes requests.
                    for _ in range(5):
                        client.post(
                            f"{API}/login",
                            json={
                                "username": "u",
                                "password": "example-initial-password",
                            },
                        )

                    # This should now be rate limited.
                    r = client.post(
                        f"{API}/login",
                        json={"username": "u", "password": "example-initial-password"},
                    )
                    assert r.status_code == 429, (
                        f"Expected 429 from rate limiter after {5} hits, "
                        f"got {r.status_code}: {r.text}"
                    )
                    assert "Retry-After" in r.headers

    def test_authenticated_path_not_rate_limited(self):
        """Authenticated API paths bypass the rate limiter entirely."""
        with patch.object(rl_module, "_LIMIT", 3):
            with tempfile.TemporaryDirectory() as tmp:
                config_path = f"{tmp}/server-config.json"
                with Server(config_path) as server:
                    client = TestClient(server.api, raise_server_exceptions=True)

                    r = client.post(
                        f"{API}/login",
                        json={
                            "username": "owner",
                            "password": "example-owner-password-99",
                        },
                    )
                    assert r.status_code == 200, r.text

                    # More than _LIMIT calls to an authenticated path - must all succeed.
                    for _ in range(20):
                        r = client.get(f"{API}/pictures")
                        assert r.status_code == 200, (
                            f"Authenticated path should not be rate-limited, "
                            f"got {r.status_code}: {r.text}"
                        )

    def test_rate_limit_window_resets(self):
        """After the window expires the counter resets and requests go through again.

        The window has to outlast the three logins below, because the limiter
        slides: with a 1s window the first hit was already evicted by the time
        the third arrived whenever the two bcrypt password verifications took
        more than a second between them, and the assertion below saw 200
        instead of 429. That is a wall-clock budget, not a limiter bug, and it
        went from rare to reproducible once CI started running test files
        concurrently. ``_WINDOW`` is therefore sized with enough headroom to
        survive a contended runner; the reset itself is still proven by
        sleeping past it.
        """
        window_seconds = 5
        with (
            patch.object(rl_module, "_LIMIT", 2),
            patch.object(rl_module, "_WINDOW", window_seconds),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                config_path = f"{tmp}/server-config.json"
                with Server(config_path) as server:
                    client = TestClient(server.api, raise_server_exceptions=True)

                    # Exhaust the limit.
                    assert client.post(
                        f"{API}/login",
                        json={"username": "u", "password": "example-password"},
                    ).status_code in {200, 401}
                    assert client.post(
                        f"{API}/login",
                        json={"username": "u", "password": "example-password"},
                    ).status_code in {200, 401}
                    r = client.post(
                        f"{API}/login",
                        json={"username": "u", "password": "example-password"},
                    )
                    assert r.status_code == 429

                    # Wait out the window.
                    time.sleep(window_seconds + 0.2)

                    r = client.post(
                        f"{API}/login",
                        json={"username": "u", "password": "example-password"},
                    )
                    assert r.status_code in {200, 401}, (
                        f"After window reset, login should no longer be rate-limited; "
                        f"got {r.status_code}: {r.text}"
                    )


# ---------------------------------------------------------------------------
# 8. Data integrity - pictures and scores survive all attacks
# ---------------------------------------------------------------------------


class TestDataIntegrityUnderAttack(_SharedPictureLibrary):
    """Run every attack pattern against the shared library; nothing may change.

    ``library_env`` already re-asserted the ids and scores before each test
    starts, so the baseline these attacks are measured against is a checked one
    rather than whatever the previous test left behind.

    Both state-poisoning tests below (the lockout and the rate-limit barrage)
    leave server-lifetime counters set. ``_reset_owner_credentials`` clears the
    lockout *and* the rate-limit window at the start of every test, so neither
    one silently defangs the next.
    """

    def test_data_intact_after_write_attempts(self, library_env):
        picture_ids = library_env.picture_ids
        attack_client = TestClient(library_env.server.api)
        headers = {"Authorization": f"Bearer {library_env.read_token}"}

        # Attempt to clobber every score to 1.
        attack_client.post(
            f"{API}/pictures/apply-scores",
            json={"scores": {str(pid): 1 for pid in picture_ids}},
            headers=headers,
        )

        # Attempt to delete every picture.
        for pid in picture_ids:
            attack_client.delete(f"{API}/pictures/{pid}", headers=headers)

        # Attempt PATCH on first picture.
        attack_client.patch(
            f"{API}/pictures/{picture_ids[0]}",
            json={"score": 1, "deleted": True},
            headers=headers,
        )

        # Attempt to upload a replacement picture.
        attack_client.post(
            f"{API}/pictures/import",
            files=[("file", ("evil.png", _make_png_bytes(), "image/png"))],
            headers=headers,
        )

        _assert_pictures_intact(
            library_env.owner_client, picture_ids, library_env.scores
        )

    def test_data_intact_after_privilege_escalation_attempts(self, library_env):
        attack_client = TestClient(library_env.server.api)
        headers = {"Authorization": f"Bearer {library_env.read_token}"}

        # Try to create a super-token.
        attack_client.post(
            f"{API}/users/me/token",
            json={"description": "escalated", "scope": "ALL"},
            headers=headers,
        )

        # Try to change password.
        attack_client.post(
            f"{API}/users/me/auth",
            json={
                "current_password": "example-owner-password",
                "new_password": "example-hacked-password",
            },
            headers=headers,
        )

        # Still able to log in with the original password.
        fresh_client = TestClient(library_env.server.api)
        r = fresh_client.post(
            f"{API}/login",
            json={"username": "owner", "password": "example-owner-password"},
        )
        assert r.status_code == 200, (
            f"Owner password must be unchanged after attack attempts, "
            f"got {r.status_code}: {r.text}"
        )

        _assert_pictures_intact(
            library_env.owner_client, library_env.picture_ids, library_env.scores
        )

    def test_data_intact_after_brute_force_lockout(self, library_env):
        """Trigger the login lockout, then verify pictures and scores are untouched."""
        attack_client = TestClient(library_env.server.api)
        for i in range(5):
            r = attack_client.post(
                f"{API}/login",
                json={"username": "owner", "password": f"example-wrong-password-{i}"},
            )
            assert r.status_code == 401, (
                f"attempt {i + 1} should have been a plain auth failure, "
                f"got {r.status_code}: {r.text} - the lockout this test is "
                "about was never reached"
            )
        r = attack_client.post(
            f"{API}/login",
            json={"username": "owner", "password": "example-wrong-password-5"},
        )
        assert r.status_code == 429, (
            f"the 6th failure should have hit the lockout, got {r.status_code}"
        )

        _assert_pictures_intact(
            library_env.owner_client, library_env.picture_ids, library_env.scores
        )

    def test_data_intact_after_rate_limit_barrage(self, library_env):
        """Fire requests well past the rate limit; data must survive the barrage."""
        with patch.object(rl_module, "_LIMIT", 5):
            # Hammer the login endpoint way past the limit.
            attack_client = TestClient(library_env.server.api)
            details = []
            for i in range(20):
                r = attack_client.post(
                    f"{API}/login",
                    json={"username": "anyuser", "password": f"anypassword{i}"},
                )
                if r.status_code == 429:
                    details.append(r.json().get("detail", ""))
            # The status code alone proves nothing: the login *lockout* also
            # answers 429 after five bad passwords, and it would fire on this
            # barrage whether the rate limiter existed or not. Only the
            # limiter's own detail string distinguishes them.
            assert any("Too many requests" in d for d in details), (
                "the barrage never tripped the rate limiter (only the login "
                f"lockout, if anything) - its window was dirty: {details}"
            )

        _assert_pictures_intact(
            library_env.owner_client, library_env.picture_ids, library_env.scores
        )
