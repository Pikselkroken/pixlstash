"""Tests for the Review Sessions API: create/scan receipt, one-open-per-tag,
diff-insert + no-resurrection, re-parenting with include_reviewed, neighbour
capture, kind derivation, refresh, archive/abort, and scope freezing."""

import gc
import io
import json
import os
import tempfile
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from PIL import Image

import numpy as np
from sqlmodel import SQLModel, delete, select

from pixlstash.db_models import (
    Picture,
    PictureSetMember,
    PictureStack,
    Review,
    Tag,
    UserToken,
)
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.db_models.tag_suggestion import TagSuggestion
from pixlstash.server import Server
from pixlstash.services.review_service import derive_kind
from pixlstash.tasks import TaskType
from tests.utils import upload_pictures_and_wait

API = "/api/v1"
TAG = "malformed hand"


# Backfill finders whose task owns a column these fixtures hand-write. They are
# taken out of the planner for the lifetime of the module server (see
# ``_disable_conflicting_backfill``).
_CONFLICTING_FINDERS = (
    # ImageEmbeddingTask writes both image_embedding and perceptual_hash.
    TaskType.IMAGE_EMBEDDING,
    # TagTask does `delete(Tag).where(Tag.picture_id.in_(...))` before writing
    # its own labels, so it removes the seeded suspect tag outright.
    TaskType.TAGGER,
    # TagPredictionBackfillTask rewrites tag_prediction, which several tests
    # seed by hand to drive auto-resolvable / human-label assertions.
    TaskType.TAG_PREDICTION_BACKFILL,
)


def _disable_conflicting_backfill(server):
    """Take the backfill finders that fight the fixtures out of the planner.

    The fixtures below hand-write ``image_embedding``, ``perceptual_hash``,
    ``Tag`` and ``TagPrediction`` rows straight into the DB so the scan sees a
    controlled neighbourhood with controlled labels. The backfill sweep owns
    those same columns and races those writes: it picks a picture up while the
    column is still NULL (or simply re-derives the labels), then writes the real
    value over the synthetic one - the pair stops being a pair, or the suspect
    loses the very tag the review is about, and the scan finds nothing.

    The per-test servers this module used to build hid the race. A vault that
    had just come up had nothing to backfill and no models loaded, so the sweep
    was still in a long backoff and never reached the picture before the test
    ended. A shared server always has the previous test's work to chew on, stays
    on a short interval with everything warm, and lands inside the test instead:
    two or three failures per run, never the same ones, and never the same
    reason twice.

    Only these finders go, and the planner keeps running: the import endpoint
    refuses outright while the face worker is down and every fixture here
    imports pictures. Waiting the pipeline out instead
    (``tests.utils.wait_likeness_settled``, or a narrower poll on the columns)
    was tried and measured slower than the per-test servers this replaces.

    Returns the names of the finders it removed, so ``reset_library`` can
    re-check before every test that they are still gone.
    """
    for task_type in _CONFLICTING_FINDERS:
        server.vault._planner_work_finders.pop(task_type)
    # detach_finders() edits the planner's finder structures under its own lock,
    # so this is safe against the loop thread that is running right now.
    return server.vault._work_planner.detach_finders(_CONFLICTING_FINDERS)


def _tables_to_wipe(server):
    """Vault tables that carry test state and must be emptied between tests.

    Every table the live vault schema has, minus the ones that already hold
    rows on a freshly started server (``library_settings``, ``metadata``,
    ``snapshot``, …) - those are start-up state, not test state. Deriving the
    list from the schema instead of hand-listing models keeps it complete: a
    table added later is wiped without anyone remembering to add it here, and a
    forgotten table is exactly how a shared environment starts making
    assertions pass for the wrong reason.

    Returned children-first (reverse dependency order) so the deletes do not
    trip the vault's foreign keys, which are enforced.
    """
    engine = server.vault.db._engine
    present = set(sa.inspect(engine).get_table_names())
    with engine.connect() as connection:
        seeded = {
            name
            for name in present
            if connection.execute(
                sa.select(sa.func.count()).select_from(sa.table(name))
            ).scalar()
        }
    return [
        table
        for table in reversed(SQLModel.metadata.sorted_tables)
        if table.name in present and table.name not in seeded
    ]


@pytest.fixture(scope="module")
def env():
    """Server + logged-in owner client shared by every test in this module.

    Building a Server (migrations, vault start-up, route registration) and
    minting the owner password cost several seconds each, and this module has
    43 tests. We pay that once and return the library to its just-started state
    between tests with the autouse ``reset_library`` fixture instead.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        os.makedirs(os.path.join(temp_dir, "images"), exist_ok=True)
        server_config_path = os.path.join(temp_dir, "server-config.json")
        with open(server_config_path, "w") as f:
            f.write(json.dumps({"port": 8000}))
        server = Server(server_config_path)
        disabled_finders = _disable_conflicting_backfill(server)
        try:
            client = TestClient(server.api)
            resp = client.post(
                "/login", json={"username": "testuser", "password": "testpassword"}
            )
            assert resp.status_code == 200, resp.text
            yield SimpleNamespace(
                client=client,
                server=server,
                wipe_tables=_tables_to_wipe(server),
                disabled_finders=disabled_finders,
            )
        finally:
            server.close()
            gc.collect()


@pytest.fixture(autouse=True)
def reset_library(env):
    """Return the shared library to its just-started state before each test.

    Reviews freeze a receipt on close and their rows carry decided/skipped
    state, and the tests seed pictures, tags, embeddings, sets, stacks and
    predictions on top - every one of which would silently change what a later
    test's assertion means. Wipe the vault, drop any API token a previous test
    minted (with the auth cache that mirrors it), then prove the result.
    """
    server = env.server

    def _wipe_vault(session):
        for table in env.wipe_tables:
            session.exec(delete(table))
        session.commit()

    server.vault.db.run_task(_wipe_vault)

    def _wipe_tokens(session):
        session.exec(delete(UserToken))
        session.commit()

    # Tokens live in the hub, not the vault. The owner's cookie session is not
    # backed by a UserToken row, so it survives this and stays logged in.
    server.hub_engine.run_task(_wipe_tokens)
    # Go through the flush helper so the revocation epoch is bumped too - a
    # bare _token_cache.clear() skips it (see AuthService._flush_token_cache).
    server.auth._flush_token_cache()

    # The integrity check lives here, in the per-test fixture, on purpose: the
    # CI gate shards individual tests, so a "runs last" canary test would only
    # ever guard the shard it happened to land in. It asserts on identity
    # (which reviews, which pictures) rather than on counts or status codes,
    # because a review that failed to clear and a review that was refused look
    # identical from a status code.
    running = server.vault._work_planner.registered_finder_names()
    assert running.isdisjoint(env.disabled_finders), (
        "a backfill finder that rewrites this module's fixture data is running "
        f"again: {sorted(running & env.disabled_finders)}"
    )
    listed = env.client.get(f"{API}/reviews")
    assert listed.status_code == 200, listed.text
    assert listed.json() == [], "a previous test left review sessions behind"
    leftovers = server.vault.db.run_immediate_read_task(
        lambda s: s.exec(select(Picture.id)).all()
    )
    assert leftovers == [], f"a previous test left pictures behind: {leftovers}"

    yield


@pytest.fixture
def server(env):
    return env.server


@pytest.fixture
def client(env):
    return env.client


_distinct_counter = [0]


def _upload_named(client):
    """Upload a fresh, content-distinct in-memory PNG and return its id."""
    _distinct_counter[0] += 1
    n = _distinct_counter[0]
    img = Image.new("RGB", (16 + n, 16 + n), color=(n * 7 % 256, n * 13 % 256, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return upload_pictures_and_wait(
        client, [("file", (f"distinct{n}.png", buf.getvalue(), "image/png"))]
    )["results"][0]["picture_id"]


def _seed_tag(server, pic_id, tag=TAG):
    def insert(session):
        session.add(Tag(picture_id=pic_id, tag=tag))
        session.commit()

    server.vault.db.run_task(insert)


def _set_embedding(server, pic_id, vec):
    blob = np.asarray(vec, dtype=np.float32).tobytes()

    def upd(session):
        pic = session.get(Picture, pic_id)
        pic.image_embedding = blob
        session.add(pic)
        session.commit()

    server.vault.db.run_task(upd)


def _set_phash(server, pic_id, phash_int):
    hex_str = f"{phash_int:016x}"

    def upd(session):
        pic = session.get(Picture, pic_id)
        pic.perceptual_hash = hex_str
        session.add(pic)
        session.commit()

    server.vault.db.run_task(upd)


def _axis_vec(axis, value=1.0):
    vec = [0.0] * 512
    vec[axis] = value
    return vec


def _make_pair(client, server, axis=0, tag=TAG):
    """Two pictures with identical embeddings on the given axis; first tagged.

    Far-apart phashes so the pair is 'binary', not a perceptual near-duplicate.
    Returns (tagged_id, untagged_id).
    """
    a = _upload_named(client)
    b = _upload_named(client)
    vec = _axis_vec(axis)
    _set_embedding(server, a, vec)
    _set_embedding(server, b, vec)
    # 64 bits apart on distinct patterns per pair so no accidental near-dups.
    _set_phash(server, a, 0xFFFF_FFFF_FFFF_FFFF >> axis)
    _set_phash(server, b, (0xFFFF_FFFF_FFFF_FFFF >> axis) ^ 0xFFFF_FFFF_FFFF_FFFF)
    _seed_tag(server, a, tag)
    return a, b


def _get_suggestion(server, sid):
    return server.vault.db.run_immediate_read_task(
        lambda s: s.exec(select(TagSuggestion).where(TagSuggestion.id == sid)).first()
    )


def test_create_review_receipt_neighbors_and_progress(client, server):
    a, b = _make_pair(client, server)
    resp = client.post(f"{API}/reviews", json={"tag": TAG})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tag"] == TAG
    assert body["status"] == "OPEN"
    assert body["scope"] == {
        "project_id": None,
        "set_id": None,
        "character_id": None,
    }
    assert body["stats"]["scanned"] == 2
    assert body["stats"]["found"] == 1
    assert body["stats"]["prev_reviewed"] == 0
    # No tagger predictions on file → nothing is auto-resolvable.
    assert body["stats"]["auto_resolvable"] == 0
    rid = body["id"]

    rows = client.get(f"{API}/reviews/{rid}/suggestions").json()
    assert len(rows) == 1
    row = rows[0]
    assert row["review_id"] == rid
    assert {row["picture_id"], row["twin_picture_id"]} == {a, b}
    assert row["direction"] in ("add", "remove")
    assert row["kind"] == "binary"  # no shared stack, far-apart dhash
    # Neighbour capture: with two pictures, k clamps to 1 - the one
    # neighbour is the other picture, with its has-the-tag flag.
    other = b if row["picture_id"] == a else a
    assert row["neighbors"] == [{"picture_id": other, "has": other == a}]

    # List view: progress and staleness.
    listed = client.get(f"{API}/reviews").json()
    assert [r["id"] for r in listed] == [rid]
    assert listed[0]["progress"] == {
        "done": 0,
        "pending": 1,
        "skipped": 0,
        "locked": 0,
    }
    assert listed[0]["stale"] is False

    detail = client.get(f"{API}/reviews/{rid}").json()
    assert detail["progress"] == {
        "done": 0,
        "pending": 1,
        "skipped": 0,
        "locked": 0,
    }
    assert detail["stats"]["scanned"] == 2
    assert "auto_resolvable" in detail["stats"]
    assert detail["receipt"] == {"removed": 0, "added": 0, "kept": 0, "skipped": 0}


def test_one_open_review_per_tag_conflict(client, server):
    _make_pair(client, server)
    first = client.post(f"{API}/reviews", json={"tag": TAG})
    assert first.status_code == 200
    rid = first.json()["id"]

    dup = client.post(f"{API}/reviews", json={"tag": TAG})
    assert dup.status_code == 409

    # A different tag is fine.
    other = client.post(f"{API}/reviews", json={"tag": "bad anatomy"})
    assert other.status_code == 200

    # Closing the first review frees the tag again.
    assert client.post(f"{API}/reviews/{rid}/archive").status_code == 200
    again = client.post(f"{API}/reviews", json={"tag": TAG})
    assert again.status_code == 200


def test_refresh_diff_inserts_and_never_resurrects_decided_rows(client, server):
    _make_pair(client, server, axis=0)
    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    rows = client.get(f"{API}/reviews/{rid}/suggestions").json()
    assert len(rows) == 1
    sid = rows[0]["id"]

    # Decide it (dismiss leaves the labels untouched, so a re-scan would
    # still flag the pair if resurrection were possible).
    assert client.post(f"/tag_suggestions/{sid}/dismiss").status_code == 200

    # Refresh: nothing new, and the decided row stays decided.
    refreshed = client.post(f"{API}/reviews/{rid}/refresh").json()
    assert refreshed["new_count"] == 0
    assert refreshed["found"] == 1
    assert refreshed["refreshed_at"] is not None
    row = _get_suggestion(server, sid)
    assert row.status == "DISMISSED"
    assert row.review_id == rid
    assert client.get(f"{API}/reviews/{rid}/suggestions").json() == []

    # A genuinely new pair appended by refresh, without duplicating the old.
    _make_pair(client, server, axis=1)
    refreshed = client.post(f"{API}/reviews/{rid}/refresh").json()
    assert refreshed["new_count"] == 1
    assert refreshed["found"] == 2
    pending = client.get(f"{API}/reviews/{rid}/suggestions").json()
    assert len(pending) == 1
    assert pending[0]["id"] != sid
    # Still exactly one row for the decided pair - no duplicates anywhere.
    all_rows = client.get(
        f"{API}/reviews/{rid}/suggestions", params={"status": ""}
    ).json()
    assert len(all_rows) == 2


def test_refresh_recomputes_prev_reviewed_receipt(client, server):
    # Regression: a refresh must recompute prev_reviewed, not freeze the
    # create-time count. Suspects decided in an earlier (foreign) review only
    # become visible after a re-scan, so the refreshed receipt must reflect them.
    from pixlstash.services.tag_scan_service import SOURCE

    # Open review over pair P0 (pending). Nothing decided yet → the create
    # receipt reports prev_reviewed == 0.
    _make_pair(client, server, axis=0)
    created = client.post(f"{API}/reviews", json={"tag": TAG}).json()
    rid = created["id"]
    assert created["stats"]["prev_reviewed"] == 0

    # A second suspect pair whose BOTH endpoints were already decided in a
    # different review (review_id=None models the legacy/foreign queue).
    # Injecting both sides means whichever survives pair-dedup is decided.
    p_a, p_b = _make_pair(client, server, axis=1)

    def _inject_decided(session):
        for pid in (p_a, p_b):
            session.add(
                TagSuggestion(
                    picture_id=pid,
                    tag=TAG,
                    direction="remove",
                    source=SOURCE,
                    score=0.9,
                    status="DISMISSED",
                    review_id=None,
                )
            )
        session.commit()

    server.vault.db.run_task(_inject_decided)

    # Refresh: P0 is this review's own pending row (never counted); the P1
    # suspect is decided-elsewhere, so the recomputed receipt is now 1.
    refreshed = client.post(f"{API}/reviews/{rid}/refresh").json()
    assert refreshed["new_count"] == 0
    detail = client.get(f"{API}/reviews/{rid}").json()
    assert detail["stats"]["prev_reviewed"] == 1


def test_include_reviewed_reparents_decided_rows(client, server):
    _make_pair(client, server)
    r1 = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    sid = client.get(f"{API}/reviews/{r1}/suggestions").json()[0]["id"]
    assert client.post(f"/tag_suggestions/{sid}/dismiss").status_code == 200
    assert client.post(f"{API}/reviews/{r1}/archive").status_code == 200

    # Default: previously-decided suspects stay suppressed, but are counted.
    r2_body = client.post(f"{API}/reviews", json={"tag": TAG}).json()
    assert r2_body["stats"]["found"] == 0
    assert r2_body["stats"]["prev_reviewed"] == 1
    assert client.post(f"{API}/reviews/{r2_body['id']}/abort").status_code == 200

    # include_reviewed: the SAME row is re-parented and reopened - not
    # duplicated (UNIQUE(picture_id, tag, source) intact) and not recreated.
    r3_body = client.post(
        f"{API}/reviews", json={"tag": TAG, "include_reviewed": True}
    ).json()
    r3 = r3_body["id"]
    assert r3_body["stats"]["found"] == 1
    assert r3_body["stats"]["prev_reviewed"] == 1
    rows = client.get(f"{API}/reviews/{r3}/suggestions").json()
    assert [r["id"] for r in rows] == [sid]
    assert rows[0]["status"] == "PENDING"
    row = _get_suggestion(server, sid)
    assert row.review_id == r3
    assert row.reviewed_at is None

    # The human-label ledger written by the dismissal is untouched: the
    # dismissal of a 'remove' suggestion asserted POS, and re-surfacing the
    # suspect must not erase that supervision.
    pred = server.vault.db.run_immediate_read_task(
        lambda s: s.exec(
            select(TagPrediction).where(
                TagPrediction.picture_id == row.picture_id,
                TagPrediction.tag == TAG,
            )
        ).first()
    )
    assert pred is not None
    assert pred.label_source == "human"
    assert pred.label_state in ("POS", "NEG")


def test_kind_pair_for_dhash_near_duplicates_and_stacks(client, server):
    a = _upload_named(client)
    b = _upload_named(client)
    vec = _axis_vec(0)
    _set_embedding(server, a, vec)
    _set_embedding(server, b, vec)
    # 2-bit dhash Hamming → versions of one shot → "pair".
    _set_phash(server, a, 0xFFFF_FFFF_FFFF_FFFF)
    _set_phash(server, b, 0xFFFF_FFFF_FFFF_FFFC)
    _seed_tag(server, a)

    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    rows = client.get(f"{API}/reviews/{rid}/suggestions").json()
    assert len(rows) == 1
    assert rows[0]["kind"] == "pair"

    # Same-stack derivation (pure function; stack ids are what matter).
    assert derive_kind((7, "00" * 8), (7, "ff" * 8)) == "pair"
    assert derive_kind((7, None), (8, None)) == "binary"
    assert derive_kind((None, None), (None, None)) == "binary"


def test_kind_pair_for_same_stack_via_api(client, server):
    a, b = _make_pair(client, server)  # far-apart dhash

    def make_stack(session):
        stack = PictureStack(name="s")
        session.add(stack)
        session.commit()
        session.refresh(stack)
        for pid in (a, b):
            pic = session.get(Picture, pid)
            pic.stack_id = stack.id
            session.add(pic)
        session.commit()

    server.vault.db.run_task(make_stack)

    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    rows = client.get(f"{API}/reviews/{rid}/suggestions").json()
    assert len(rows) == 1
    assert rows[0]["kind"] == "pair"


def test_archive_and_abort_leave_rows_and_guard_transitions(client, server):
    _make_pair(client, server)
    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    sid = client.get(f"{API}/reviews/{rid}/suggestions").json()[0]["id"]

    archived = client.post(f"{API}/reviews/{rid}/archive")
    assert archived.status_code == 200
    assert archived.json()["status"] == "ARCHIVED"
    # Suggestion rows untouched by closing the session.
    row = _get_suggestion(server, sid)
    assert row.status == "PENDING"
    assert row.review_id == rid

    # Idempotent re-archive; conflicting transitions rejected.
    assert client.post(f"{API}/reviews/{rid}/archive").status_code == 200
    assert client.post(f"{API}/reviews/{rid}/abort").status_code == 409
    assert client.post(f"{API}/reviews/{rid}/refresh").status_code == 409

    # Status filter on the list endpoint.
    assert (
        client.get(f"{API}/reviews", params={"status": "ARCHIVED"}).json()[0]["id"]
        == rid
    )
    assert client.get(f"{API}/reviews", params={"status": "OPEN"}).json() == []

    assert client.get(f"{API}/reviews/999999").status_code == 404
    assert client.post(f"{API}/reviews/999999/refresh").status_code == 404


def test_review_scope_is_frozen_and_restricts_the_scan(client, server):
    in_a, in_b = _make_pair(client, server, axis=0)
    out_a, out_b = _make_pair(client, server, axis=1)

    r = client.post(f"{API}/picture_sets", json={"name": "Scope"})
    set_id = r.json()["picture_set"]["id"]

    def add_members(session):
        session.add(PictureSetMember(set_id=set_id, picture_id=in_a))
        session.add(PictureSetMember(set_id=set_id, picture_id=in_b))
        session.commit()

    server.vault.db.run_task(add_members)

    body = client.post(f"{API}/reviews", json={"tag": TAG, "set_id": set_id}).json()
    assert body["scope"]["set_id"] == set_id
    assert body["stats"]["scanned"] == 2  # only the in-set pair
    rows = client.get(f"{API}/reviews/{body['id']}/suggestions").json()
    assert len(rows) == 1
    assert {rows[0]["picture_id"], rows[0]["twin_picture_id"]} == {in_a, in_b}
    assert out_a not in {rows[0]["picture_id"], rows[0]["twin_picture_id"]}


def test_stale_flag_after_tagger_run(client, server):
    from datetime import datetime

    from pixlstash.db_models import TaggerRun

    _make_pair(client, server)
    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    assert client.get(f"{API}/reviews/{rid}").json()["stale"] is False

    # A tagger run completed after the scan makes the review stale...
    def add_run(session):
        session.add(TaggerRun(run="run-1", created_at=datetime.utcnow()))
        session.commit()

    server.vault.db.run_task(add_run)
    assert client.get(f"{API}/reviews/{rid}").json()["stale"] is True

    # ...and refreshing clears it.
    client.post(f"{API}/reviews/{rid}/refresh")
    assert client.get(f"{API}/reviews/{rid}").json()["stale"] is False


def test_auto_resolvable_counts_review_scoped_bulk_dry_run(client, server):
    a, b = _make_pair(client, server)

    def seed_preds(session):
        for pid in (a, b):
            session.add(
                TagPrediction(
                    picture_id=pid,
                    tag=TAG,
                    confidence=0.03,
                    model_version="test-v1",
                    status="PENDING",
                )
            )
        session.commit()

    server.vault.db.run_task(seed_preds)

    body = client.post(f"{API}/reviews", json={"tag": TAG}).json()
    rid = body["id"]
    # The pair is a 'remove' with both taggers confidently negative - the
    # two independent signals agree, so the receipt offers it for bulk.
    assert body["stats"]["auto_resolvable"] == 1

    # Review-scoped bulk-accept applies exactly the review's rows.
    applied = client.post(
        "/tag_suggestions/bulk-accept",
        json={"tag": TAG, "min_combined": 0.9, "review_id": rid},
    ).json()
    assert applied["count"] == 1
    assert client.get(f"{API}/reviews/{rid}").json()["stats"]["auto_resolvable"] == 0
    assert client.get(f"{API}/reviews/{rid}").json()["progress"] == {
        "done": 1,
        "pending": 0,
        "skipped": 0,
        "locked": 0,
    }

    # Review-scoped bulk-reopen undoes it; a mismatched review_id is a no-op.
    noop = client.post(
        "/tag_suggestions/bulk-reopen",
        json={"ids": applied["accepted_ids"], "review_id": rid + 999},
    ).json()
    assert noop["count"] == 0
    undone = client.post(
        "/tag_suggestions/bulk-reopen",
        json={"ids": applied["accepted_ids"], "review_id": rid},
    ).json()
    assert undone["count"] == 1
    assert client.get(f"{API}/reviews/{rid}").json()["progress"] == {
        "done": 0,
        "pending": 1,
        "skipped": 0,
        "locked": 0,
    }


def test_skip_records_no_decision_and_reopens(client, server):
    a, _b = _make_pair(client, server)
    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    sid = client.get(f"{API}/reviews/{rid}/suggestions").json()[0]["id"]

    resp = client.post(f"/tag_suggestions/{sid}/skip")
    assert resp.status_code == 200
    assert resp.json()["status"] == "skipped"

    # No decision anywhere: labels untouched, no ledger entry written.
    row = _get_suggestion(server, sid)
    assert row.status == "SKIPPED"
    assert row.reviewed_at is not None
    tags = client.get(f"/pictures/{a}/tags").json()["tags"]
    assert any(t["tag"] == TAG for t in tags)  # suspect keeps its tag
    pred = server.vault.db.run_immediate_read_task(
        lambda s: s.exec(
            select(TagPrediction).where(
                TagPrediction.picture_id == row.picture_id,
                TagPrediction.tag == TAG,
            )
        ).first()
    )
    assert pred is None  # skip never writes the human-label ledger

    # Skipped is out of the PENDING queue, reported separately, and never
    # re-inserted by refresh.
    assert client.get(f"{API}/reviews/{rid}/suggestions").json() == []
    detail = client.get(f"{API}/reviews/{rid}").json()
    assert detail["progress"] == {
        "done": 0,
        "pending": 0,
        "skipped": 1,
        "locked": 0,
    }
    assert detail["receipt"] == {"removed": 0, "added": 0, "kept": 0, "skipped": 1}
    assert client.post(f"{API}/reviews/{rid}/refresh").json()["new_count"] == 0
    assert _get_suggestion(server, sid).status == "SKIPPED"

    # Reopen re-pends it (nothing to reverse).
    assert client.post(f"/tag_suggestions/{sid}/reopen").status_code == 200
    row = _get_suggestion(server, sid)
    assert row.status == "PENDING"
    assert row.reviewed_at is None

    assert client.post("/tag_suggestions/999999/skip").status_code == 404


def test_review_wide_bulk_reopen_undoes_decided_but_not_skipped(client, server):
    pair1_a, _ = _make_pair(client, server, axis=0)
    pair2_a, _ = _make_pair(client, server, axis=1)
    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    rows = client.get(f"{API}/reviews/{rid}/suggestions").json()
    assert len(rows) == 2
    by_suspect = {r["picture_id"]: r["id"] for r in rows}

    # Decide one (accept removes the suspect's tag), skip the other.
    accepted_sid = by_suspect[pair1_a]
    skipped_sid = by_suspect[pair2_a]
    assert client.post(f"/tag_suggestions/{accepted_sid}/accept").status_code == 200
    assert client.post(f"/tag_suggestions/{skipped_sid}/skip").status_code == 200
    detail = client.get(f"{API}/reviews/{rid}").json()
    assert detail["progress"] == {
        "done": 1,
        "pending": 0,
        "skipped": 1,
        "locked": 0,
    }
    assert detail["receipt"]["removed"] == 1

    # "Undo N changes": empty ids + review_id reopens ALL decided rows,
    # leaving SKIPPED alone (it made no changes to undo).
    undone = client.post(
        "/tag_suggestions/bulk-reopen", json={"ids": [], "review_id": rid}
    ).json()
    assert undone["count"] == 1
    assert _get_suggestion(server, accepted_sid).status == "PENDING"
    assert _get_suggestion(server, skipped_sid).status == "SKIPPED"
    # The accepted removal was reversed: the suspect has its tag back.
    tags = client.get(f"/pictures/{pair1_a}/tags").json()["tags"]
    assert any(t["tag"] == TAG for t in tags)

    # Abort leaves everything as-is (undo-then-abort is the caller's flow).
    assert client.post(f"{API}/reviews/{rid}/abort").status_code == 200
    assert _get_suggestion(server, skipped_sid).status == "SKIPPED"


def test_preview_reports_scope_and_prev_reviewed(client, server):
    in_a, in_b = _make_pair(client, server, axis=0)
    _make_pair(client, server, axis=1)  # out-of-scope pair

    r = client.post(f"{API}/picture_sets", json={"name": "Scope"})
    set_id = r.json()["picture_set"]["id"]

    def add_members(session):
        session.add(PictureSetMember(set_id=set_id, picture_id=in_a))
        session.add(PictureSetMember(set_id=set_id, picture_id=in_b))
        session.commit()

    server.vault.db.run_task(add_members)

    preview = client.get(f"{API}/reviews/preview", params={"tag": TAG}).json()
    assert preview == {"in_scope": 4, "prev_reviewed": 0}
    scoped = client.get(
        f"{API}/reviews/preview", params={"tag": TAG, "set_id": set_id}
    ).json()
    assert scoped == {"in_scope": 2, "prev_reviewed": 0}

    # Decide a suspect in an earlier review; preview now reports it.
    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    for row in client.get(f"{API}/reviews/{rid}/suggestions").json():
        assert client.post(f"/tag_suggestions/{row['id']}/dismiss").status_code == 200
    client.post(f"{API}/reviews/{rid}/archive")
    preview = client.get(f"{API}/reviews/preview", params={"tag": TAG}).json()
    assert preview["prev_reviewed"] == 2

    assert client.get(f"{API}/reviews/preview", params={"tag": " "}).status_code == 400


# ---------------------------------------------------------------------------
# F4 (SKIPPED adopts, not prev_reviewed) / F9 (freeze-on-close + undo survives
# re-parent) / F2 (soft-deleted card gone + unacceptable) / F5 (large-scope
# preview) regression coverage - see the tag-review-rewrite brief.
# ---------------------------------------------------------------------------


def test_skipped_row_readopts_without_dragging_decided_rows(client, server):
    # F4: a skipped-then-archived suspect must re-appear (adopted, PENDING) in a
    # new review and count as `found`, NOT prev_reviewed - while a genuinely
    # decided suspect stays suppressed and counts as prev_reviewed.
    _make_pair(client, server, axis=0)
    _make_pair(client, server, axis=1)
    r1 = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    rows = client.get(f"{API}/reviews/{r1}/suggestions").json()
    assert len(rows) == 2
    skipped_sid, dismissed_sid = rows[0]["id"], rows[1]["id"]
    assert client.post(f"/tag_suggestions/{skipped_sid}/skip").status_code == 200
    assert client.post(f"/tag_suggestions/{dismissed_sid}/dismiss").status_code == 200
    assert client.post(f"{API}/reviews/{r1}/archive").status_code == 200

    # Default (include_reviewed=False): the SKIPPED suspect is re-adopted
    # (PENDING again, counted as found); the DISMISSED suspect is not.
    r2_body = client.post(f"{API}/reviews", json={"tag": TAG}).json()
    r2 = r2_body["id"]
    assert r2_body["stats"]["found"] == 1
    assert r2_body["stats"]["prev_reviewed"] == 1
    rows2 = client.get(f"{API}/reviews/{r2}/suggestions").json()
    assert [r["id"] for r in rows2] == [skipped_sid]  # same row, re-parented
    assert rows2[0]["status"] == "PENDING"

    srow = _get_suggestion(server, skipped_sid)
    assert srow.review_id == r2 and srow.status == "PENDING"
    # The decided row was never dragged out of its archived review.
    drow = _get_suggestion(server, dismissed_sid)
    assert drow.review_id == r1 and drow.status == "DISMISSED"


def test_archived_receipt_is_frozen_against_reparenting_scan(client, server):
    # F9a: a closed review's receipt/progress aggregate LIVE over its rows; a
    # later include_reviewed scan re-parents those rows into a new review, which
    # would shrink the receipt if it were still live. Freezing on close keeps the
    # archived session's cover sheet immutable. (A dismiss leaves the pair still
    # disagreeing, so it is re-detectable and thus re-parentable - an accept
    # would RESOLVE the disagreement, so the scan could not re-detect it.)
    _make_pair(client, server)
    r1 = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    sid = client.get(f"{API}/reviews/{r1}/suggestions").json()[0]["id"]
    assert client.post(f"/tag_suggestions/{sid}/dismiss").status_code == 200

    receipt_before = client.get(f"{API}/reviews/{r1}").json()["receipt"]
    assert receipt_before == {"removed": 0, "added": 0, "kept": 1, "skipped": 0}
    assert client.post(f"{API}/reviews/{r1}/archive").status_code == 200
    # Freezing preserved the receipt/progress at close.
    detail = client.get(f"{API}/reviews/{r1}").json()
    assert detail["receipt"] == receipt_before
    assert detail["progress"]["done"] == 1

    # A new review re-parents A's dismissed row into itself.
    r2 = client.post(
        f"{API}/reviews", json={"tag": TAG, "include_reviewed": True}
    ).json()["id"]
    assert [r["id"] for r in client.get(f"{API}/reviews/{r2}/suggestions").json()] == [
        sid
    ]

    # A's frozen receipt/progress are UNCHANGED despite the row leaving.
    after = client.get(f"{API}/reviews/{r1}").json()
    assert after["receipt"] == receipt_before  # not shrunk to kept=0
    assert after["progress"]["done"] == 1
    # And the list surface serves the same frozen progress.
    listed = {r["id"]: r for r in client.get(f"{API}/reviews").json()}
    assert listed[r1]["progress"]["done"] == 1


def test_undo_survives_reparent_and_restores_prior_decision(client, server):
    # F9b: a DISMISS in A leaves the pair still disagreeing, so include_reviewed
    # re-parents the SAME row into B and re-pends it, capturing A's decision in
    # prior_*. Undo peels the re-parent back to A's decision (its ledger entry
    # still standing); a second undo reverses that decision through the normal
    # flow. (A resolving decision - accept/twin-fix - would remove the
    # disagreement, so the scan would not re-detect the pair and there would be
    # nothing to re-parent; only a still-disagreeing decision is re-surfaced.)
    _make_pair(client, server)
    r1 = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    row0 = client.get(f"{API}/reviews/{r1}/suggestions").json()[0]
    sid, suspect = row0["id"], row0["picture_id"]
    assert client.post(f"/tag_suggestions/{sid}/dismiss").status_code == 200

    def _human_label():
        pred = server.vault.db.run_immediate_read_task(
            lambda s: s.exec(
                select(TagPrediction).where(
                    TagPrediction.picture_id == suspect,
                    TagPrediction.tag == TAG,
                )
            ).first()
        )
        return None if pred is None else (pred.label_source, pred.label_state)

    # Dismiss recorded a human label (POS for a remove / NEG for an add).
    src, state = _human_label()
    assert src == "human" and state in ("POS", "NEG")
    assert client.post(f"{API}/reviews/{r1}/archive").status_code == 200

    # include_reviewed re-parents the SAME row and captures A's decision.
    r2 = client.post(
        f"{API}/reviews", json={"tag": TAG, "include_reviewed": True}
    ).json()["id"]
    assert [r["id"] for r in client.get(f"{API}/reviews/{r2}/suggestions").json()] == [
        sid
    ]
    row = _get_suggestion(server, sid)
    assert row.review_id == r2 and row.status == "PENDING"
    assert row.prior_review_id == r1 and row.prior_status == "DISMISSED"
    # A's decision (its ledger entry) is untouched by the re-parent.
    assert _human_label() == (src, state)

    # Undo #1: peel the re-parent - back to A's prior decided state, prior_*
    # cleared, A's decision still standing.
    assert client.post(f"/tag_suggestions/{sid}/reopen").status_code == 200
    row = _get_suggestion(server, sid)
    assert row.review_id == r1 and row.status == "DISMISSED"
    assert row.reviewed_at is not None
    assert row.prior_review_id is None and row.prior_status is None
    assert _human_label() == (src, state)

    # Undo #2 (normal reversal, now re-exposed): A's decision is reversed -
    # the ledger entry it wrote is cleared and the row re-pends under A.
    assert client.post(f"/tag_suggestions/{sid}/reopen").status_code == 200
    row = _get_suggestion(server, sid)
    assert row.review_id == r1 and row.status == "PENDING"
    src2, state2 = _human_label()
    assert src2 != "human" or state2 == "UNKNOWN"


def test_soft_deleted_suspect_absent_from_queue_and_unacceptable(client, server):
    # F2: a soft-deleted picture's card must not be listed, and accept must
    # refuse (not silently) to write a Tag onto it.
    from pixlstash.services.tag_suggestion_service import (
        SuggestionConflictError,
        accept_suggestion,
    )

    _make_pair(client, server)
    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    row = client.get(f"{API}/reviews/{rid}/suggestions").json()[0]
    sid, suspect = row["id"], row["picture_id"]

    def _soft_delete(session):
        session.get(Picture, suspect).deleted = True
        session.commit()

    server.vault.db.run_task(_soft_delete)

    assert client.get(f"{API}/reviews/{rid}/suggestions").json() == []
    with pytest.raises(SuggestionConflictError):
        accept_suggestion(server.vault, sid)
    assert _get_suggestion(server, sid).status == "PENDING"  # not accepted


def test_preview_review_survives_large_scope(client, server):
    # F5: a >1000-id scope must not trip SQLite's bound-parameter ceiling. Pin
    # the ceiling to the historical 999 floor; the temp-table scope path keeps
    # preview a 200 (a plain .in_(scope_ids) would raise OperationalError → 500).
    import sqlite3

    from sqlalchemy import event as sa_event
    from sqlalchemy import insert as sa_insert

    from pixlstash.db_models import PictureSetMember

    n = 1500
    set_id = client.post(f"{API}/picture_sets", json={"name": "Big"}).json()[
        "picture_set"
    ]["id"]

    def seed(session):
        session.execute(
            sa_insert(Picture),
            [
                {"id": i, "deleted": False, "file_path": f"/x/{i}.png"}
                for i in range(1, n + 1)
            ],
        )
        session.execute(
            sa_insert(PictureSetMember),
            [{"set_id": set_id, "picture_id": i} for i in range(1, n + 1)],
        )
        session.commit()

    server.vault.db.run_task(seed)

    # Pin every new connection's variable limit to the historical 999 floor.
    # The engine outlives this test now, so the listener has to come off again
    # (and the connections it configured have to be dropped) or every later
    # test in the module silently runs against a 999-variable SQLite.
    engine = server.vault.db._engine

    def _set_limit(dbapi_conn, _record):
        dbapi_conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)

    sa_event.listen(engine, "connect", _set_limit)
    engine.dispose()
    try:
        resp = client.get(
            f"{API}/reviews/preview", params={"tag": TAG, "set_id": set_id}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"in_scope": n, "prev_reviewed": 0}
    finally:
        sa_event.remove(engine, "connect", _set_limit)
        engine.dispose()


def test_full_pair_card_page_survives_sqlite_variable_ceiling(client, server):
    """A 500-card pair page may reference 1000 distinct pictures."""
    import sqlite3

    from sqlalchemy import event as sa_event
    from sqlalchemy import insert as sa_insert

    card_count = 500

    def seed(session):
        review = Review(tag=TAG, scanned=1000, found=card_count)
        session.add(review)
        session.flush()
        review_id = int(review.id)
        session.execute(
            sa_insert(Picture),
            [
                {
                    "id": picture_id,
                    "deleted": False,
                    "file_path": f"/x/{picture_id}.png",
                    "format": "png",
                }
                for picture_id in range(1, card_count * 2 + 1)
            ],
        )
        session.execute(
            sa_insert(TagSuggestion),
            [
                {
                    "picture_id": picture_id,
                    "twin_picture_id": picture_id + card_count,
                    "tag": TAG,
                    "direction": "remove",
                    "source": "near_neighbor",
                    "score": 0.9,
                    "twin_sim": 0.9,
                    "review_id": review_id,
                    "status": "PENDING",
                }
                for picture_id in range(1, card_count + 1)
            ],
        )
        session.commit()
        return review_id

    review_id = server.vault.db.run_task(seed)
    # As above: the shared engine keeps whatever we attach, so this comes off
    # again in the finally.
    engine = server.vault.db._engine

    def _set_limit(dbapi_conn, _record):
        dbapi_conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)

    sa_event.listen(engine, "connect", _set_limit)
    engine.dispose()
    try:
        response = client.get(
            f"{API}/reviews/{review_id}/suggestions", params={"limit": card_count}
        )
        assert response.status_code == 200, response.text
        cards = response.json()
        assert len(cards) == card_count
        assert (
            len(
                {card["picture_id"] for card in cards}
                | {card["twin_picture_id"] for card in cards}
            )
            == card_count * 2
        )
    finally:
        sa_event.remove(engine, "connect", _set_limit)
        engine.dispose()


# ---------------------------------------------------------------------------
# Security: /reviews is an owner-only, vault-wide curation surface. Every
# write/preview endpoint must reject a resource-scoped READ token (403) while
# still serving the owner (cookie) session - same policy the read endpoints
# already enforce. These use the versioned /api/v1 paths + a Bearer token so
# the auth middleware sets request.state.token_scope.
# ---------------------------------------------------------------------------


@pytest.fixture
def token(client, server, reset_library):
    """A READ token scoped to a picture set holding one suspect pair.

    ``client`` keeps the owner's cookie session; the returned value is the
    Bearer token for the scoped READ token. Depends on ``reset_library``
    explicitly so the pair and set it seeds are created after the wipe, not
    swept away by it.
    """
    in_a, in_b = _make_pair(client, server)
    set_id = client.post(f"{API}/picture_sets", json={"name": "Scope"}).json()[
        "picture_set"
    ]["id"]

    def add_members(session):
        session.add(PictureSetMember(set_id=set_id, picture_id=in_a))
        session.add(PictureSetMember(set_id=set_id, picture_id=in_b))
        session.commit()

    server.vault.db.run_task(add_members)

    r = client.post(
        f"{API}/users/me/token",
        json={
            "description": "set read",
            "scope": "READ",
            "resource_type": "picture_set",
            "resource_id": set_id,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["token"]


# ---------------------------------------------------------------------------
# Delete: single review + clear-all-archived. Deleting a review removes only the
# Review row; its suggestion rows detach (review_id -> NULL) so per-item
# decisions and the no-resurrection guarantee survive.
# ---------------------------------------------------------------------------


def test_delete_single_review_detaches_rows_and_preserves_decision(client, server):
    a, _b = _make_pair(client, server)
    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    sid = client.get(f"{API}/reviews/{rid}/suggestions").json()[0]["id"]

    # Decide it: accept removes the suspect's tag and writes the human ledger.
    assert client.post(f"/tag_suggestions/{sid}/accept").status_code == 200

    # Delete the review session.
    resp = client.delete(f"{API}/reviews/{rid}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": 1}

    # The review is gone; a re-GET is 404.
    assert client.get(f"{API}/reviews/{rid}").status_code == 404
    assert client.get(f"{API}/reviews").json() == []

    # The suggestion row survived, detached (review_id -> NULL) with its
    # decision intact - deleting the session resurrected nothing.
    row = _get_suggestion(server, sid)
    assert row is not None
    assert row.review_id is None
    assert row.status == "ACCEPTED"

    # The underlying label decision stands: accept removed the tag, and the
    # human-label ledger entry the accept wrote is untouched by the delete.
    tags = client.get(f"/pictures/{a}/tags").json()["tags"]
    assert not any(t["tag"] == TAG for t in tags)
    pred = server.vault.db.run_immediate_read_task(
        lambda s: s.exec(
            select(TagPrediction).where(
                TagPrediction.picture_id == a,
                TagPrediction.tag == TAG,
            )
        ).first()
    )
    assert pred is not None and pred.label_source == "human"


def test_delete_open_review_frees_the_tag_and_keeps_pending_row(client, server):
    _make_pair(client, server)
    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    sid = client.get(f"{API}/reviews/{rid}/suggestions").json()[0]["id"]

    # Delete the OPEN review mid-review: the pending row detaches, no decision
    # is lost, and the tag is free for a new OPEN review (unique index clear).
    delete_resp = client.delete(f"{API}/reviews/{rid}")
    assert delete_resp.status_code == 200, delete_resp.text
    assert delete_resp.json() == {"deleted": 1}
    row = _get_suggestion(server, sid)
    assert row is not None and row.review_id is None and row.status == "PENDING"

    again = client.post(f"{API}/reviews", json={"tag": TAG})
    assert again.status_code == 200


def test_delete_review_404_for_unknown_id(client, server):
    missing_resp = client.delete(f"{API}/reviews/999999")
    assert missing_resp.status_code == 404, missing_resp.text


def test_clear_archived_deletes_only_archived_and_returns_count(client, server):
    # Three reviews on distinct tags: two archived, one aborted, plus one OPEN.
    _make_pair(client, server, axis=0, tag="tag a")
    _make_pair(client, server, axis=1, tag="tag b")
    _make_pair(client, server, axis=2, tag="tag c")
    _make_pair(client, server, axis=3, tag="tag d")

    r_arch1 = client.post(f"{API}/reviews", json={"tag": "tag a"}).json()["id"]
    r_arch2 = client.post(f"{API}/reviews", json={"tag": "tag b"}).json()["id"]
    r_abort = client.post(f"{API}/reviews", json={"tag": "tag c"}).json()["id"]
    r_open = client.post(f"{API}/reviews", json={"tag": "tag d"}).json()["id"]

    assert client.post(f"{API}/reviews/{r_arch1}/archive").status_code == 200
    assert client.post(f"{API}/reviews/{r_arch2}/archive").status_code == 200
    assert client.post(f"{API}/reviews/{r_abort}/abort").status_code == 200

    # Clear all archived: exactly the two ARCHIVED reviews are deleted.
    resp = client.delete(f"{API}/reviews", params={"status": "ARCHIVED"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": 2}

    remaining = {r["id"]: r["status"] for r in client.get(f"{API}/reviews").json()}
    assert set(remaining) == {r_abort, r_open}
    assert remaining[r_abort] == "ABORTED"
    assert remaining[r_open] == "OPEN"

    # Idempotent: a second clear finds nothing to delete.
    clear_resp = client.delete(f"{API}/reviews", params={"status": "ARCHIVED"})
    assert clear_resp.status_code == 200, clear_resp.text
    assert clear_resp.json() == {"deleted": 0}


def test_clear_reviews_requires_archived_status(client, server):
    _make_pair(client, server)
    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]

    # Missing status -> 422 (required query param); never a delete-everything.
    no_status_resp = client.delete(f"{API}/reviews")
    assert no_status_resp.status_code == 422, no_status_resp.text
    # A non-ARCHIVED status is refused (guards OPEN/ABORTED sessions).
    open_resp = client.delete(f"{API}/reviews", params={"status": "OPEN"})
    assert open_resp.status_code == 400, open_resp.text
    aborted_resp = client.delete(f"{API}/reviews", params={"status": "ABORTED"})
    assert aborted_resp.status_code == 400, aborted_resp.text
    # The OPEN review is still there - nothing was deleted.
    assert client.get(f"{API}/reviews/{rid}").json()["status"] == "OPEN"


def test_scoped_token_cannot_delete_review(client, server, token):
    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    bearer = TestClient(server.api)
    headers = {"Authorization": f"Bearer {token}"}
    scoped_resp = bearer.delete(f"{API}/reviews/{rid}", headers=headers)
    assert scoped_resp.status_code == 403, scoped_resp.text
    # The rejected call must not have deleted the session.
    assert client.get(f"{API}/reviews/{rid}").json()["status"] == "OPEN"
    # Owner deletes fine.
    owner_resp = client.delete(f"{API}/reviews/{rid}")
    assert owner_resp.status_code == 200, owner_resp.text
    assert owner_resp.json() == {"deleted": 1}


def test_scoped_token_cannot_clear_archived_reviews(client, server, token):
    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    assert client.post(f"{API}/reviews/{rid}/archive").status_code == 200
    bearer = TestClient(server.api)
    headers = {"Authorization": f"Bearer {token}"}
    scoped_delete_resp = bearer.delete(
        f"{API}/reviews", params={"status": "ARCHIVED"}, headers=headers
    )
    assert scoped_delete_resp.status_code == 403
    # The rejected call must not have deleted the archived session.
    assert client.get(f"{API}/reviews/{rid}").json()["status"] == "ARCHIVED"
    # Owner clears fine.
    owner_resp = client.delete(f"{API}/reviews", params={"status": "ARCHIVED"})
    assert owner_resp.json() == {"deleted": 1}


def test_scoped_token_cannot_create_review(client, server, token):
    bearer = TestClient(server.api)
    headers = {"Authorization": f"Bearer {token}"}
    assert (
        bearer.post(f"{API}/reviews", json={"tag": TAG}, headers=headers).status_code
        == 403
    )
    # Owner (cookie) still creates fine - no over-blocking regression.
    assert client.post(f"{API}/reviews", json={"tag": TAG}).status_code == 200


def test_scoped_token_cannot_preview_review(client, server, token):
    bearer = TestClient(server.api)
    headers = {"Authorization": f"Bearer {token}"}
    assert (
        bearer.get(
            f"{API}/reviews/preview", params={"tag": TAG}, headers=headers
        ).status_code
        == 403
    )
    assert client.get(f"{API}/reviews/preview", params={"tag": TAG}).status_code == 200


def test_scoped_token_cannot_refresh_review(client, server, token):
    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    bearer = TestClient(server.api)
    headers = {"Authorization": f"Bearer {token}"}
    assert (
        bearer.post(f"{API}/reviews/{rid}/refresh", headers=headers).status_code == 403
    )
    # Owner refresh still works.
    assert client.post(f"{API}/reviews/{rid}/refresh").status_code == 200


def test_scoped_token_cannot_archive_review(client, server, token):
    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    bearer = TestClient(server.api)
    headers = {"Authorization": f"Bearer {token}"}
    assert (
        bearer.post(f"{API}/reviews/{rid}/archive", headers=headers).status_code == 403
    )
    # The rejected call must not have closed the session.
    assert client.get(f"{API}/reviews/{rid}").json()["status"] == "OPEN"
    # Owner archives fine.
    assert client.post(f"{API}/reviews/{rid}/archive").json()["status"] == "ARCHIVED"


def test_scoped_token_cannot_abort_review(client, server, token):
    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    bearer = TestClient(server.api)
    headers = {"Authorization": f"Bearer {token}"}
    assert bearer.post(f"{API}/reviews/{rid}/abort", headers=headers).status_code == 403
    assert client.get(f"{API}/reviews/{rid}").json()["status"] == "OPEN"
    # Owner aborts fine.
    assert client.post(f"{API}/reviews/{rid}/abort").json()["status"] == "ABORTED"


# --- The three review READ endpoints, both directions ------------------------
#
# The write-side scoped-token tests above are the easy half. These three are the
# data-egress half: /reviews/{id}/suggestions in particular serves twin +
# up-to-k neighbour picture ids, per-picture tag bits, and (since the locked-set
# work) picture-set NAMES - all of which routinely fall outside a share token's
# grant, which is why the whole surface is owner-only. The gate is correct today
# but was untested on the reads, i.e. one refactor from being a silent hole.
# Each test asserts BOTH directions: scoped token 403, owner still 200 with a
# populated payload (over-blocking is its own regression).


def test_scoped_token_cannot_read_reviews(client, server, token):
    client.post(f"{API}/reviews", json={"tag": TAG})
    bearer = TestClient(server.api)
    headers = {"Authorization": f"Bearer {token}"}
    scoped_resp = bearer.get(f"{API}/reviews", headers=headers)
    assert scoped_resp.status_code == 403, scoped_resp.text
    # Owner still lists the session - no over-blocking.
    owner_resp = client.get(f"{API}/reviews")
    assert owner_resp.status_code == 200, owner_resp.text
    assert len(owner_resp.json()) == 1


def test_scoped_token_cannot_read_review_detail(client, server, token):
    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    bearer = TestClient(server.api)
    headers = {"Authorization": f"Bearer {token}"}
    scoped_resp = bearer.get(f"{API}/reviews/{rid}", headers=headers)
    assert scoped_resp.status_code == 403, scoped_resp.text
    # A scoped token must not be able to distinguish "forbidden" from
    # "missing" either - a 404 here would confirm/deny review ids.
    assert bearer.get(f"{API}/reviews/999999", headers=headers).status_code == 403
    owner_resp = client.get(f"{API}/reviews/{rid}")
    assert owner_resp.status_code == 200, owner_resp.text
    assert owner_resp.json()["id"] == rid


def test_scoped_token_cannot_read_review_suggestions(client, server, token):
    """The one new data-egress path in the locked-sets work.

    The owner side additionally asserts the cards still carry ``locked_sets``,
    so the 403 above cannot be "fixed" by quietly emptying the payload.
    """
    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    bearer = TestClient(server.api)
    headers = {"Authorization": f"Bearer {token}"}
    scoped_resp = bearer.get(f"{API}/reviews/{rid}/suggestions", headers=headers)
    assert scoped_resp.status_code == 403, scoped_resp.text
    # The denial now comes from the AuthzGate, not from the handler: the
    # route is declared OWNER_ONLY in ROUTE_POLICIES, so the gate runs
    # require_unscoped_owner as a router dependency and the handler's own
    # inline check never executes for a scoped token. Asserting the gate's
    # message is what keeps this test honest about WHICH layer is holding
    # the line, which is the thing that would silently rot if the
    # declaration were ever dropped.
    assert (
        scoped_resp.json()["detail"] == "Owner-level (full, unscoped) access required"
    )

    # Owner side: the queue is served in full. These are exactly the fields
    # the gate exists to withhold - the twin's id and the neighbourhood ids.
    owner_resp = client.get(f"{API}/reviews/{rid}/suggestions")
    assert owner_resp.status_code == 200, owner_resp.text
    cards = owner_resp.json()
    assert cards, "owner must still get the queue"
    card = cards[0]
    assert card["twin_picture_id"] is not None

    # Lock a set holding the twin: the owner's card must still NAME it.
    # (The suspect is deliberately left unlocked - a locked suspect is
    # filtered out of the PENDING queue entirely, so it could not carry a
    # name to assert on.)
    twin_set = client.post(f"{API}/picture_sets", json={"name": "TwinFrozen"}).json()[
        "picture_set"
    ]["id"]
    assert (
        client.post(
            f"{API}/picture_sets/{twin_set}/members/{card['twin_picture_id']}"
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"{API}/picture_sets/{twin_set}", json={"locked": True}
        ).status_code
        == 200
    )
    locked_cards = client.get(f"{API}/reviews/{rid}/suggestions").json()
    assert any(
        c.get("twin_locked")
        and any(s["name"] == "TwinFrozen" for s in c.get("twin_locked_sets", []))
        for c in locked_cards
    ), "owner must still see the locking set names on the cards"


# --- Locked pictures must never be served as reviewable work -----------------
#
# A locked picture set freezes its members' label data, so every review action
# on such a picture 423s. Serving it as a card shows the user work they cannot
# action. Enforcement lives at two layers (scan-time selection and card-serve
# time); these tests pin both, plus the over-blocking regression.


def _new_set(client, name):
    resp = client.post("/picture_sets", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["picture_set"]["id"]


def _add_to_set(client, set_id, pic_id):
    resp = client.post(f"/picture_sets/{set_id}/members/{pic_id}")
    assert resp.status_code == 200, resp.text


def _set_locked(client, set_id, locked):
    resp = client.patch(f"/picture_sets/{set_id}", json={"locked": locked})
    assert resp.status_code == 200, resp.text


def test_locking_a_set_after_the_scan_withdraws_its_cards(client, server):
    # The reported bug: the scan runs once at create time, so a set locked
    # afterwards left already-scanned rows being served as un-actionable cards.
    # Card serving must therefore filter at READ time, not rely on the scan.
    a, b = _make_pair(client, server)
    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    rows = client.get(f"{API}/reviews/{rid}/suggestions").json()
    assert len(rows) == 1
    suspect = rows[0]["picture_id"]
    # Pending before the lock.
    assert client.get(f"{API}/reviews/{rid}").json()["progress"]["pending"] == 1

    set_id = _new_set(client, "Frozen")
    _add_to_set(client, set_id, suspect)
    _set_locked(client, set_id, True)

    # The card is withdrawn, and the session reports as exhausted rather
    # than stuck at "1 remaining" with an empty queue.
    assert client.get(f"{API}/reviews/{rid}/suggestions").json() == []
    progress = client.get(f"{API}/reviews/{rid}").json()["progress"]
    assert progress["pending"] == 0
    assert progress["locked"] == 1
    assert progress["done"] == 0

    # Unlocking restores it - the row was withheld, never destroyed.
    _set_locked(client, set_id, False)
    assert len(client.get(f"{API}/reviews/{rid}/suggestions").json()) == 1
    restored = client.get(f"{API}/reviews/{rid}").json()["progress"]
    assert restored["pending"] == 1 and restored["locked"] == 0


def test_locked_picture_is_never_scanned_as_a_suspect(client, server):
    # Scan-time layer: a picture already frozen when the review is created must
    # not even be selected as a suspect.
    a, b = _make_pair(client, server)
    set_id = _new_set(client, "Frozen")
    # Freeze both sides of the pair so neither can be the suspect.
    _add_to_set(client, set_id, a)
    _add_to_set(client, set_id, b)
    _set_locked(client, set_id, True)

    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    assert client.get(f"{API}/reviews/{rid}/suggestions").json() == []
    # Both pictures still counted as scanned (they remain in the embedding
    # pool as twins/neighbours); they simply produced no suspect row.
    detail = client.get(f"{API}/reviews/{rid}").json()
    assert detail["stats"]["scanned"] == 2
    assert detail["stats"]["found"] == 0
    assert detail["progress"] == {
        "done": 0,
        "pending": 0,
        "skipped": 0,
        "locked": 0,
    }


def test_stack_sibling_of_a_locked_member_is_also_withheld(client, server):
    # Set membership is stack-atomic, so the write guards freeze a picture that
    # merely SHARES A STACK with a locked-set member. Candidate selection and
    # card serving must use the same rule or they hand out un-actionable cards.
    a, b = _make_pair(client, server)
    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    suspect = client.get(f"{API}/reviews/{rid}/suggestions").json()[0]["picture_id"]

    # Put the suspect in a stack with a third picture, and lock a set
    # containing only that third picture.
    sibling = _upload_named(client)

    def _stack(session):
        stack = PictureStack()
        session.add(stack)
        session.commit()
        session.refresh(stack)
        for pid in (suspect, sibling):
            session.get(Picture, pid).stack_id = stack.id
        session.commit()

    server.vault.db.run_task(_stack)

    set_id = _new_set(client, "Frozen sibling")
    _add_to_set(client, set_id, sibling)
    # Membership reconciliation may pull the whole stack into the set; the
    # point of the test is the lock rule, so assert on the outcome below.
    _set_locked(client, set_id, True)

    assert client.get(f"{API}/reviews/{rid}/suggestions").json() == []
    assert client.get(f"{API}/reviews/{rid}").json()["progress"]["locked"] == 1


def test_unlocked_picture_in_the_same_scope_is_still_served(client, server):
    # Over-blocking regression: locking ONE set must not withdraw cards for
    # pictures outside it. Two independent pairs, only one frozen.
    a1, b1 = _make_pair(client, server, axis=0)
    a2, b2 = _make_pair(client, server, axis=7)
    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    rows = client.get(f"{API}/reviews/{rid}/suggestions").json()
    assert len(rows) == 2
    frozen_suspect = rows[0]["picture_id"]
    kept_suspect = rows[1]["picture_id"]

    set_id = _new_set(client, "Frozen")
    _add_to_set(client, set_id, frozen_suspect)
    _set_locked(client, set_id, True)

    served = client.get(f"{API}/reviews/{rid}/suggestions").json()
    assert [r["picture_id"] for r in served] == [kept_suspect]
    progress = client.get(f"{API}/reviews/{rid}").json()["progress"]
    assert progress["pending"] == 1 and progress["locked"] == 1


def test_locking_does_not_hide_already_decided_rows(client, server):
    # Only actionable work is withheld. A row DECIDED before the lock is this
    # review's audit record - it stays listable and stays counted in done, so
    # the served list and the receipt cannot disagree.
    _make_pair(client, server)
    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    row = client.get(f"{API}/reviews/{rid}/suggestions").json()[0]
    sid, suspect = row["id"], row["picture_id"]
    assert client.post(f"/tag_suggestions/{sid}/dismiss").status_code == 200

    set_id = _new_set(client, "Frozen after the decision")
    _add_to_set(client, set_id, suspect)
    _set_locked(client, set_id, True)

    # Still listed under its decided status, and still counted as done.
    decided = client.get(
        f"{API}/reviews/{rid}/suggestions", params={"status": "DISMISSED"}
    ).json()
    assert [r["id"] for r in decided] == [sid]
    progress = client.get(f"{API}/reviews/{rid}").json()["progress"]
    assert progress["done"] == 1
    assert progress["pending"] == 0 and progress["locked"] == 0


def _dhash_pair(client, server, axis=0, phash=0xFFFF_FFFF_FFFF_FFFF):
    """A genuine 'pair' card (2-bit dhash apart, NO stack) so either side can be
    locked independently. Returns (tagged_id, untagged_id)."""
    a = _upload_named(client)
    b = _upload_named(client)
    vec = _axis_vec(axis)
    _set_embedding(server, a, vec)
    _set_embedding(server, b, vec)
    _set_phash(server, a, phash)
    _set_phash(server, b, phash ^ 0x3)
    _seed_tag(server, a)
    return a, b


def test_locked_twin_is_flagged_per_side_and_degrades_card_to_binary(client, server):
    # The scan keeps frozen pictures in the pool as TWINS, so a card can have a
    # locked twin and a free suspect. The payload must say which SIDE is frozen
    # (the twin's lock blocks fix-twin/swap; accept+dismiss only write the
    # suspect), and the card degrades to binary because the pair-only corners
    # could only 423.
    _dhash_pair(client, server)
    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    row = client.get(f"{API}/reviews/{rid}/suggestions").json()[0]
    assert row["kind"] == "pair"
    # Unlocked direction: neither side flagged.
    assert row["locked"] is False and row["twin_locked"] is False
    assert row["locked_sets"] == [] and row["twin_locked_sets"] == []
    twin = row["twin_picture_id"]
    assert twin is not None

    set_id = _new_set(client, "Holiday 2019")
    _add_to_set(client, set_id, twin)
    _set_locked(client, set_id, True)

    row = client.get(f"{API}/reviews/{rid}/suggestions").json()[0]
    # The card is still served - the suspect is free and fully reviewable.
    assert row["twin_picture_id"] == twin
    # Per-side: only the twin is frozen, and the set name is carried for the
    # explanation copy without a per-card lookup.
    assert row["twin_locked"] is True
    assert row["twin_locked_sets"] == [{"id": set_id, "name": "Holiday 2019"}]
    assert row["locked"] is False
    assert row["locked_sets"] == []
    # Degraded: the pair-only corners (swap / fix-twin) are gone.
    assert row["kind"] == "binary"


def test_locked_twin_card_stays_actionable_and_out_of_the_locked_bucket(client, server):
    # A locked-twin card is actionable (accept + dismiss write only the suspect),
    # so it must stay in `pending`, NOT in the new `locked` bucket, and the
    # "complete when pending == 0" invariant must still hold once decided.
    _dhash_pair(client, server, axis=0, phash=0xFFFF_FFFF_FFFF_FFFF)
    _dhash_pair(client, server, axis=9, phash=0x0FFF_FFFF_FFFF_0000)
    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    rows = client.get(f"{API}/reviews/{rid}/suggestions").json()
    assert len(rows) == 2

    set_id = _new_set(client, "Frozen twins")
    for r in rows:
        _add_to_set(client, set_id, r["twin_picture_id"])
    _set_locked(client, set_id, True)

    # Both cards are still served, both flagged, both counted as real work.
    served = client.get(f"{API}/reviews/{rid}/suggestions").json()
    assert len(served) == 2
    assert all(r["twin_locked"] is True and r["locked"] is False for r in served)
    progress = client.get(f"{API}/reviews/{rid}").json()["progress"]
    assert progress["pending"] == 2  # actionable, so NOT the locked bucket
    assert progress["locked"] == 0

    # The two corners a binary card offers both work against a frozen twin:
    # dismiss and accept each write only the suspect.
    assert client.post(f"/tag_suggestions/{served[0]['id']}/dismiss").status_code == 200
    assert client.post(f"/tag_suggestions/{served[1]['id']}/accept").status_code == 200

    # Invariant holds: the session completes rather than hanging.
    progress = client.get(f"{API}/reviews/{rid}").json()["progress"]
    assert progress["pending"] == 0 and progress["locked"] == 0
    assert progress["done"] == 2
    assert client.get(f"{API}/reviews/{rid}/suggestions").json() == []


def test_locked_twin_blocks_only_the_pair_corners_and_makes_decisions_one_way(
    client, server
):
    # Pins exactly which actions a frozen twin refuses. fix-twin and swap write
    # the twin, so they 423 - this is why the card degrades to binary. reopen
    # also refuses (it guards BOTH sides unconditionally), so a decision on a
    # locked-twin card is currently ONE-WAY: actionable, but not undoable until
    # the set is unlocked.
    _dhash_pair(client, server)
    rid = client.post(f"{API}/reviews", json={"tag": TAG}).json()["id"]
    row = client.get(f"{API}/reviews/{rid}/suggestions").json()[0]
    sid = row["id"]

    set_id = _new_set(client, "Frozen twin")
    _add_to_set(client, set_id, row["twin_picture_id"])
    _set_locked(client, set_id, True)

    # The pair-only corners refuse.
    assert client.post(f"/tag_suggestions/{sid}/fix-twin").status_code == 423
    assert client.post(f"/tag_suggestions/{sid}/swap").status_code == 423

    # Dismiss succeeds, but undo does not while the twin is frozen.
    assert client.post(f"/tag_suggestions/{sid}/dismiss").status_code == 200
    assert client.post(f"/tag_suggestions/{sid}/reopen").status_code == 423

    # Unlocking restores undo, and the card comes back as a full pair.
    _set_locked(client, set_id, False)
    assert client.post(f"/tag_suggestions/{sid}/reopen").status_code == 200
    restored = client.get(f"{API}/reviews/{rid}/suggestions").json()[0]
    assert restored["twin_locked"] is False
    assert restored["twin_locked_sets"] == []
    assert restored["kind"] == "pair"
