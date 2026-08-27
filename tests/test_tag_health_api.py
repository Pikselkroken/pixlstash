"""Tests for the tag health board: signal aggregates on a small fixture vault,
the rebuild endpoint's background/progress reporting, no-model-signal rows,
and staleness detection / auto-rebuild (Spec B,
docs/reviews/tag-review-board-redesign-ux-spec.md §4).
"""

import gc
import io
import json
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import event as sa_event
from sqlalchemy import insert as sa_insert
from sqlmodel import select

from pixlstash.db_models import (
    Picture,
    PictureLikeness,
    PictureSet,
    PictureSetMember,
    PictureStack,
    Tag,
)
from pixlstash.db_models.tag_prediction import TagPrediction
from pixlstash.db_models.tag_suggestion import TagSuggestion
from pixlstash.db_models.tagger_run import TaggerRun
from pixlstash.server import Server
from pixlstash.utils.quality.anomaly_penalty import DEFAULT_TAG_PRECISION
from tests.utils import seed_likeness_stable, upload_pictures_and_wait

API = "/api/v1"


def _disable_background_tagger(server):
    """Turn the background tagger pipeline off for the whole test server.

    Every tag-health test seeds its own ``Tag``/``TagPrediction`` fixtures and
    asserts exact counts; it never relies on the model actually tagging. But
    ``upload_pictures_and_wait`` writes a ``__tag`` retag sentinel on each imported
    picture (see ``routes/pictures/_import.py``), which drives two background
    finders that would race the seed and the board rebuild:

    * ``MissingTagFinder`` claims any sentinel-carrying picture, runs the tagger,
      then ``TagTask._add_tags_bulk`` *deletes every ``Tag`` row for that picture*
      (sentinel and seeded tags alike) and rewrites them, plus writes
      ``TagPrediction`` rows on the tagger's own ``model_version`` - moving
      ``_current_model_version`` off the seeded ``"v1"`` and dropping the seeded
      predictions out of the version-pinned ``est_wrong``/``est_missing`` signals.
    * ``MissingTagPredictionFinder`` back-fills predictions for any picture that
      has a real tag but no prediction (e.g. the deliberately prediction-less
      ``no-model`` row), which would flip its ``has_model`` to true.

    Both only fire once the (uncached) tagger model finishes downloading and
    running, so the failure is timing-dependent - green on a cold cache, red on a
    warm one. Clearing ``active_tag_plugin`` bails ``MissingTagFinder`` (it treats
    a falsy active plugin as "off") and, together with the matching guard in
    ``MissingTagPredictionFinder``, bails the backfill too - removing the race at
    the source instead of trying to out-wait it.
    """
    server.vault.set_tagger_settings({"active_tag_plugin": None})


def _setup():
    temp_dir = tempfile.TemporaryDirectory()
    image_root = os.path.join(temp_dir.name, "images")
    os.makedirs(image_root, exist_ok=True)
    server_config_path = os.path.join(temp_dir.name, "server-config.json")
    with open(server_config_path, "w") as f:
        f.write(json.dumps({"port": 8000}))
    server = Server(server_config_path)
    client = TestClient(server.api)
    resp = client.post(
        "/login", json={"username": "testuser", "password": "testpassword"}
    )
    assert resp.status_code == 200
    # After login so no settings reload during authentication can clobber it.
    _disable_background_tagger(server)
    return temp_dir, client, server


def _teardown(temp_dir, server):
    server.close()
    temp_dir.cleanup()
    gc.collect()


_distinct_counter = [0]


def _upload_named(client):
    _distinct_counter[0] += 1
    n = _distinct_counter[0]
    img = Image.new("RGB", (16 + n, 16 + n), color=(n * 7 % 256, n * 13 % 256, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return upload_pictures_and_wait(
        client, [("file", (f"distinct{n}.png", buf.getvalue(), "image/png"))]
    )["results"][0]["picture_id"]


def _seed_tag(server, pid, tag):
    """Give *pid* one real (non-sentinel) Tag row.

    ``compute_tag_health_rows`` only emits a row for tags that appear in
    ``tag``/``tag_prediction`` at all -- an untagged picture with no
    predictions yields zero rows, so ``computed_at`` stays null and
    ``is_stale`` trivially returns False regardless of anything else. Tests
    that need a real, non-vacuous ``stale`` transition seed this first.
    """

    def seed(session):
        session.add(Tag(picture_id=pid, tag=tag))
        session.commit()

    server.vault.db.run_task(seed)


def _force_variable_limit(server, limit=999):
    """Pin every DB connection's ``SQLITE_LIMIT_VARIABLE_NUMBER`` to *limit*.

    Registers a ``connect`` listener on the vault engine and disposes the pool
    so subsequent connections are recreated with the lowered ceiling. Used to
    reproduce the historical 999-variable ceiling regardless of the running
    SQLite build's much higher default, so a large scope filtered by a plain
    ``.in_(ids)`` would raise ``OperationalError`` - proving the temp-table
    scope path is what keeps the query alive. Call AFTER seeding (an ORM bulk
    insert may itself batch many parameters).
    """
    engine = server.vault.db._engine

    def _set_limit(dbapi_conn, _record):
        dbapi_conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, limit)

    sa_event.listen(engine, "connect", _set_limit)
    engine.dispose()


def _rebuild_and_wait(client, timeout_s=30):
    resp = client.post(f"{API}/tag_health/rebuild")
    assert resp.status_code == 200, resp.text
    start = time.time()
    while time.time() - start < timeout_s:
        body = client.get(f"{API}/tag_health").json()
        if not body["building"]:
            return body
        time.sleep(0.1)
    raise AssertionError("tag_health rebuild did not finish in time")


def test_tag_health_aggregates_on_fixture_vault():
    temp_dir, client, server = _setup()
    try:
        p1 = _upload_named(client)  # tagged "t", conf 0.05 human POS → model-disputes
        p2 = _upload_named(client)  # untagged,   conf 0.95  → est_missing
        p3 = _upload_named(client)  # untagged,   conf 0.50  → boundary mass
        p4 = _upload_named(client)  # tagged "u", no predictions → no-model row
        p5 = _upload_named(client)  # tagged "t", conf 0.05 un-reviewed → est_wrong

        now = datetime.utcnow()

        def seed(session):
            session.add(Tag(picture_id=p1, tag="t"))
            session.add(Tag(picture_id=p4, tag="u"))
            session.add(Tag(picture_id=p5, tag="t"))
            # Four predictions for "t", all on the current model version. est_wrong
            # (tagged + un-reviewed low-confidence) and model_disputes (human POS the
            # model contradicts) are mutually exclusive since human-adjudicated rows
            # are excluded from est_wrong, so they need distinct pictures (p5 vs p1).
            session.add(
                TagPrediction(
                    picture_id=p1,
                    tag="t",
                    confidence=0.05,
                    model_version="v1",
                    predicted_at=now - timedelta(minutes=3),
                    # Human froze POS but the live model is confidently negative:
                    # a model-disputes-human row (and a verified one). Excluded from
                    # est_wrong precisely because a human already ruled on it.
                    label_state="POS",
                    label_source="human",
                )
            )
            session.add(
                TagPrediction(
                    picture_id=p2,
                    tag="t",
                    confidence=0.95,
                    model_version="v1",
                    predicted_at=now - timedelta(minutes=2),
                )
            )
            session.add(
                TagPrediction(
                    picture_id=p3,
                    tag="t",
                    confidence=0.50,
                    model_version="v1",
                    predicted_at=now - timedelta(minutes=1),
                )
            )
            # Tagged + un-reviewed (label_state defaults to UNKNOWN) low-confidence
            # prediction on the current version → the one genuine est_wrong row.
            session.add(
                TagPrediction(
                    picture_id=p5,
                    tag="t",
                    confidence=0.05,
                    model_version="v1",
                    predicted_at=now,
                )
            )
            # Reviewed history for "t": one accepted, one dismissed → overturn 0.5.
            session.add(
                TagSuggestion(
                    picture_id=p1,
                    tag="t",
                    direction="remove",
                    source="near_neighbor",
                    score=1.0,
                    status="ACCEPTED",
                    reviewed_at=now,
                )
            )
            session.add(
                TagSuggestion(
                    picture_id=p2,
                    tag="t",
                    direction="add",
                    source="model",
                    score=1.0,
                    status="DISMISSED",
                )
            )
            # A stored high-likeness pair disagreeing on "t" → mismatch 1.
            a, b = PictureLikeness.canon_pair(p1, p2)
            session.add(
                PictureLikeness(
                    picture_id_a=a, picture_id_b=b, likeness=0.99, metric="cosine"
                )
            )
            session.commit()

        # Seed with a stable likeness slate: the background likeness pipeline
        # would otherwise delete/recompute the seeded near-duplicate pair and
        # make `mismatch` non-deterministic (see seed_likeness_stable).
        seed_likeness_stable(server, seed)

        body = _rebuild_and_wait(client)
        assert body["computed_at"] is not None
        assert body["progress"] == 1.0
        rows = {r["tag"]: r for r in body["rows"]}

        t = rows["t"]
        assert t["est_wrong"] == 1
        assert t["est_missing"] == 1
        assert t["mismatch"] == 1
        # Four "t" predictions: 1 verified (p1 human POS), 1 boundary (p3 @ 0.50).
        assert abs(t["verified_pct"] - 1 / 4) < 1e-9
        assert abs(t["boundary_pct"] - 1 / 4) < 1e-9
        assert t["overturn_rate"] == 0.5
        assert t["model_disputes"] == 1
        assert t["has_model"] is True
        assert t["last_reviewed_at"] is not None  # newest reviewed_at ISO

        # A tag with ground truth but zero predictions still gets a row, with
        # the explicit no-model-signal state.
        u = rows["u"]
        assert u["has_model"] is False
        assert u["est_wrong"] == 0
        assert u["est_missing"] == 0
        assert u["overturn_rate"] is None
        assert u["verified_pct"] == 0.0
        assert u["last_reviewed_at"] is None  # never reviewed → "never"
    finally:
        _teardown(temp_dir, server)


def test_tag_health_same_stack_mismatch_and_no_double_count():
    temp_dir, client, server = _setup()
    try:
        p1 = _upload_named(client)
        p2 = _upload_named(client)
        p3 = _upload_named(client)

        def seed(session):
            stack = PictureStack(name="s")
            session.add(stack)
            session.commit()
            session.refresh(stack)
            for pid in (p1, p2, p3):
                pic = session.get(Picture, pid)
                pic.stack_id = stack.id
                session.add(pic)
            # One of the three stacked versions carries "t" → 1×2 = 2 pairs.
            session.add(Tag(picture_id=p1, tag="t"))
            # A stored likeness pair INSIDE the same stack must not be counted
            # twice on top of the stack pair.
            a, b = PictureLikeness.canon_pair(p1, p2)
            session.add(
                PictureLikeness(
                    picture_id_a=a, picture_id_b=b, likeness=0.99, metric="cosine"
                )
            )
            session.commit()

        seed_likeness_stable(server, seed)

        body = _rebuild_and_wait(client)
        rows = {r["tag"]: r for r in body["rows"]}
        assert rows["t"]["mismatch"] == 2
        assert rows["t"]["has_model"] is False  # no predictions at all
    finally:
        _teardown(temp_dir, server)


def test_tag_health_scoped_restricts_signals_and_tag_list():
    """`GET /tag_health?set_id=` computes live rows restricted to the scope:
    counts include only in-scope pictures, and tags that never appear on an
    in-scope picture get no row at all. The vault-wide cache is untouched."""
    temp_dir, client, server = _setup()
    try:
        p_in = _upload_named(client)  # in the set:  tagged "t", conf 0.05
        p_out = _upload_named(client)  # outside:     untagged "t", conf 0.95
        p_other = _upload_named(client)  # outside:     tagged "only_out"

        now = datetime.utcnow()

        def seed(session):
            ps = PictureSet(name="scope_set")
            session.add(ps)
            session.commit()
            session.refresh(ps)
            session.add(PictureSetMember(set_id=ps.id, picture_id=p_in))
            session.add(Tag(picture_id=p_in, tag="t"))
            session.add(Tag(picture_id=p_other, tag="only_out"))
            # In-scope est_wrong for "t"; out-of-scope est_missing for "t".
            session.add(
                TagPrediction(
                    picture_id=p_in,
                    tag="t",
                    confidence=0.05,
                    model_version="v1",
                    predicted_at=now,
                )
            )
            session.add(
                TagPrediction(
                    picture_id=p_out,
                    tag="t",
                    confidence=0.95,
                    model_version="v1",
                    predicted_at=now,
                )
            )
            session.commit()
            return ps.id

        set_id = server.vault.db.run_task(seed)

        # Vault-wide (cached) rows see both pictures and both tags.
        body = _rebuild_and_wait(client)
        rows = {r["tag"]: r for r in body["rows"]}
        assert rows["t"]["est_wrong"] == 1
        assert rows["t"]["est_missing"] == 1
        assert "only_out" in rows

        # Scoped to the set: only the in-scope picture's signals, and the
        # out-of-scope-only tag disappears from the board entirely.
        scoped = client.get(f"{API}/tag_health", params={"set_id": set_id}).json()
        assert scoped["scoped"] is True
        assert scoped["building"] is False
        srows = {r["tag"]: r for r in scoped["rows"]}
        assert srows["t"]["est_wrong"] == 1
        assert srows["t"]["est_missing"] == 0  # p_out is outside the scope
        assert "only_out" not in srows

        # An unknown scope id is a valid empty scope - no rows, not an error.
        empty = client.get(f"{API}/tag_health", params={"set_id": 99999}).json()
        assert empty["scoped"] is True
        assert empty["rows"] == []

        # The unscoped cache is untouched by scoped reads.
        body2 = client.get(f"{API}/tag_health").json()
        assert {r["tag"] for r in body2["rows"]} == {"t", "only_out"}
    finally:
        _teardown(temp_dir, server)


def test_tag_health_scoped_mismatch_excludes_out_of_scope_pairs():
    """Scoped mismatch counts only stored likeness pairs whose BOTH endpoints are
    in scope. Regression guard for pushing the scope into the pairs query: a pair
    with one endpoint outside the set must not be counted, and the result must
    match the pre-refactor Python-filtered behaviour (vault-wide sees both pairs).
    """
    temp_dir, client, server = _setup()
    try:
        p_a = _upload_named(client)  # in set, tagged "t"
        p_b = _upload_named(client)  # in set, untagged
        p_out = _upload_named(client)  # outside set, tagged "t"

        def seed(session):
            ps = PictureSet(name="scope_set")
            session.add(ps)
            session.commit()
            session.refresh(ps)
            session.add(PictureSetMember(set_id=ps.id, picture_id=p_a))
            session.add(PictureSetMember(set_id=ps.id, picture_id=p_b))
            session.add(Tag(picture_id=p_a, tag="t"))
            session.add(Tag(picture_id=p_out, tag="t"))
            # In-scope disagreeing pair (counts in both scoped and vault-wide).
            a1, b1 = PictureLikeness.canon_pair(p_a, p_b)
            session.add(
                PictureLikeness(
                    picture_id_a=a1, picture_id_b=b1, likeness=0.99, metric="cosine"
                )
            )
            # Cross-scope disagreeing pair (vault-wide only; p_out is out of scope).
            a2, b2 = PictureLikeness.canon_pair(p_b, p_out)
            session.add(
                PictureLikeness(
                    picture_id_a=a2, picture_id_b=b2, likeness=0.99, metric="cosine"
                )
            )
            session.commit()
            return ps.id

        set_id = seed_likeness_stable(server, seed)

        # Vault-wide: both disagreeing pairs count.
        body = _rebuild_and_wait(client)
        rows = {r["tag"]: r for r in body["rows"]}
        assert rows["t"]["mismatch"] == 2

        # Scoped: only the in-scope pair counts; the cross-scope pair is excluded.
        scoped = client.get(f"{API}/tag_health", params={"set_id": set_id}).json()
        srows = {r["tag"]: r for r in scoped["rows"]}
        assert srows["t"]["mismatch"] == 1
    finally:
        _teardown(temp_dir, server)


def test_tag_health_scoped_survives_large_scope():
    """A scope of ~1500 pictures must compute without tripping SQLite's
    bound-parameter ceiling. Regression guard for the scale refactor: with the
    variable limit pinned to the historical 999 floor, the pre-refactor
    ``.in_(picture_ids)`` in ``compute_tag_health_rows._scoped`` and
    ``_mismatch_counts`` (tag + likeness-pairs queries) would raise
    ``OperationalError: too many SQL variables``; the temp-table scope path
    keeps them alive and result-identical.
    """
    from pixlstash.services.tag_health_service import _mismatch_counts

    temp_dir, client, server = _setup()
    try:
        n = 1500

        def seed(session):
            # Core bulk insert bypasses the per-picture metadata-hash ORM hooks
            # (fast for 1500 rows). ``deleted`` is set explicitly - the model's
            # Python default is not applied by a Core insert.
            session.execute(
                sa_insert(Picture),
                [
                    {"id": i, "deleted": False, "file_path": f"/x/{i}.png"}
                    for i in range(1, n + 1)
                ],
            )
            ps = PictureSet(name="big_scope")
            session.add(ps)
            session.commit()
            session.refresh(ps)
            session.execute(
                sa_insert(PictureSetMember),
                [{"set_id": ps.id, "picture_id": i} for i in range(1, n + 1)],
            )
            # Plant exactly one mismatch: picture 1 tagged "bigtag", picture 2
            # untagged, a high-likeness pair between them (both in scope).
            session.add(Tag(picture_id=1, tag="bigtag"))
            a, b = PictureLikeness.canon_pair(1, 2)
            session.add(
                PictureLikeness(
                    picture_id_a=a, picture_id_b=b, likeness=0.99, metric="cosine"
                )
            )
            session.commit()
            return ps.id

        set_id = server.vault.db.run_task(seed)

        # Pin the ceiling to 999 now that seeding is done under the default.
        _force_variable_limit(server, 999)

        # Endpoint path exercises _scoped (many scoped aggregates) AND
        # _mismatch_counts, all over the 1500-id scope.
        resp = client.get(f"{API}/tag_health", params={"set_id": set_id})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["scoped"] is True
        rows = {r["tag"]: r for r in body["rows"]}
        assert rows["bigtag"]["mismatch"] == 1

        # Direct _mismatch_counts call with the same large scope.
        scope = set(range(1, n + 1))
        mm = server.vault.db.run_immediate_read_task(_mismatch_counts, scope)
        assert mm.get("bigtag") == 1
    finally:
        _teardown(temp_dir, server)


def test_tag_health_scoped_has_model_true_for_older_version_in_scope():
    """has_model is vault-wide *vocabulary* membership, not a scoped
    current-version count. A scoped board whose in-scope pictures were last
    tagged by an EARLIER run than the vault's newest prediction must still report
    the tag as in the current tagger's vocabulary (has_model=true).

    Regression guard for the scoped false-negative: pre-fix, has_model was
    derived from the count of current-version predictions *within the scope*, so
    zero in-scope current-version rows forced has_model=false on every row (the
    UI then showed "not in the tagger's vocabulary" for every tag).
    """
    temp_dir, client, server = _setup()
    try:
        p_in = _upload_named(client)  # in the set, tagged "t", OLD-gen prediction
        p_out = _upload_named(client)  # outside, "t" CURRENT-gen prediction (newer)

        now = datetime.utcnow()

        def seed(session):
            ps = PictureSet(name="scope_set")
            session.add(ps)
            session.commit()
            session.refresh(ps)
            session.add(PictureSetMember(set_id=ps.id, picture_id=p_in))
            session.add(Tag(picture_id=p_in, tag="t"))
            # In-scope picture was last tagged by the OLD generation.
            session.add(
                TagPrediction(
                    picture_id=p_in,
                    tag="t",
                    confidence=0.5,
                    model_version="v_old",
                    predicted_at=now - timedelta(days=2),
                )
            )
            # A NEWER generation exists vault-wide but OUT of scope, so
            # current_version is v_new and "t" is in the current vocabulary -
            # yet no in-scope prediction is on v_new.
            session.add(
                TagPrediction(
                    picture_id=p_out,
                    tag="t",
                    confidence=0.5,
                    model_version="v_new",
                    predicted_at=now,
                )
            )
            session.commit()
            return ps.id

        set_id = server.vault.db.run_task(seed)

        scoped = client.get(f"{API}/tag_health", params={"set_id": set_id}).json()
        assert scoped["scoped"] is True
        srows = {r["tag"]: r for r in scoped["rows"]}
        # "t" is in scope (via p_in's tag/prediction) and in the current
        # vocabulary (via p_out's v_new prediction) → has_model must be true even
        # though NO in-scope prediction is on the current version. Pre-fix: false.
        assert srows["t"]["has_model"] is True
    finally:
        _teardown(temp_dir, server)


def test_tag_health_set_scoped_board_reports_vocabulary_both_ways():
    """One set-filtered request must answer the vocabulary question correctly in
    BOTH directions: a tag whose only current-version prediction lives OUTSIDE
    the set is still in-vocabulary (has_model=true), while a tag that no current
    generation ever predicted anywhere is still out-of-vocabulary
    (has_model=false).

    This is the end-to-end guard for the reported bug - applying a picture-set
    filter in the review overlay reported "not in the tagger's vocabulary" for
    every row, because has_model was a *scope-restricted* current-version count.
    The negative direction is asserted in the same response so a fix that simply
    reports has_model=true everywhere cannot pass.
    """
    temp_dir, client, server = _setup()
    try:
        p_in = _upload_named(client)  # in the set: "in_vocab" + "out_vocab"
        p_out = _upload_named(client)  # outside the set: current-gen "in_vocab"

        now = datetime.utcnow()

        def seed(session):
            ps = PictureSet(name="scope_set")
            session.add(ps)
            session.commit()
            session.refresh(ps)
            session.add(PictureSetMember(set_id=ps.id, picture_id=p_in))
            session.add(Tag(picture_id=p_in, tag="in_vocab"))
            session.add(Tag(picture_id=p_in, tag="out_vocab"))
            # In-scope predictions are all from the OLD generation.
            session.add(
                TagPrediction(
                    picture_id=p_in,
                    tag="in_vocab",
                    confidence=0.5,
                    model_version="v_old",
                    predicted_at=now - timedelta(days=2),
                )
            )
            session.add(
                TagPrediction(
                    picture_id=p_in,
                    tag="out_vocab",
                    confidence=0.5,
                    model_version="v_old",
                    predicted_at=now - timedelta(days=2),
                )
            )
            # Newest prediction anywhere → current_version = v_new, and it only
            # covers "in_vocab" (and sits outside the scope).
            session.add(
                TagPrediction(
                    picture_id=p_out,
                    tag="in_vocab",
                    confidence=0.5,
                    model_version="v_new",
                    predicted_at=now,
                )
            )
            session.commit()
            return ps.id

        set_id = server.vault.db.run_task(seed)

        resp = client.get(f"{API}/tag_health", params={"set_id": set_id})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["scoped"] is True
        rows = {r["tag"]: r for r in body["rows"]}
        # Only in-scope tags get rows (p_out's tags are not in the set).
        assert set(rows) == {"in_vocab", "out_vocab"}
        # Current vocabulary via the out-of-scope v_new prediction. Pre-fix: false.
        assert rows["in_vocab"]["has_model"] is True
        # No v_new prediction anywhere → genuinely out of vocabulary.
        assert rows["out_vocab"]["has_model"] is False
    finally:
        _teardown(temp_dir, server)


def test_tag_health_has_model_false_for_out_of_vocabulary_tags():
    """has_model=false when a tag is absent from the current vocabulary: it has
    only older-version predictions anywhere in the vault, or no predictions at
    all. Confirms the vault-wide vocabulary query does not simply report
    has_model=true for every tag (the other direction of the scoped fix)."""
    temp_dir, client, server = _setup()
    try:
        p_current = _upload_named(client)  # "current_tag", CURRENT-gen prediction
        p_stale = _upload_named(client)  # "stale_tag", only OLD-gen prediction
        p_notag = _upload_named(client)  # "no_pred_tag", ground-truth only

        now = datetime.utcnow()

        def seed(session):
            session.add(Tag(picture_id=p_current, tag="current_tag"))
            session.add(Tag(picture_id=p_stale, tag="stale_tag"))
            session.add(Tag(picture_id=p_notag, tag="no_pred_tag"))
            # Newest prediction anywhere → current_version = v_new.
            session.add(
                TagPrediction(
                    picture_id=p_current,
                    tag="current_tag",
                    confidence=0.5,
                    model_version="v_new",
                    predicted_at=now,
                )
            )
            # Only an older generation ever predicted "stale_tag".
            session.add(
                TagPrediction(
                    picture_id=p_stale,
                    tag="stale_tag",
                    confidence=0.5,
                    model_version="v_old",
                    predicted_at=now - timedelta(days=2),
                )
            )
            session.commit()

        server.vault.db.run_task(seed)

        body = _rebuild_and_wait(client)
        rows = {r["tag"]: r for r in body["rows"]}
        # In the current vocabulary.
        assert rows["current_tag"]["has_model"] is True
        # Only an older-version prediction exists anywhere → out of vocabulary.
        assert rows["stale_tag"]["has_model"] is False
        # No prediction at all → out of vocabulary.
        assert rows["no_pred_tag"]["has_model"] is False
    finally:
        _teardown(temp_dir, server)


def test_tag_health_empty_vault_and_rebuild_idempotence():
    temp_dir, client, server = _setup()
    try:
        body = client.get(f"{API}/tag_health").json()
        assert body["rows"] == []
        assert body["building"] is False
        assert body["computed_at"] is None

        body = _rebuild_and_wait(client)
        assert body["rows"] == []

        # Rebuild twice in a row: second call while idle just re-runs; rows
        # are replaced, not duplicated.
        p1 = _upload_named(client)

        def seed(session):
            session.add(Tag(picture_id=p1, tag="t"))
            session.commit()

        server.vault.db.run_task(seed)
        body = _rebuild_and_wait(client)
        assert [r["tag"] for r in body["rows"]] == ["t"]
        body = _rebuild_and_wait(client)
        assert [r["tag"] for r in body["rows"]] == ["t"]
    finally:
        _teardown(temp_dir, server)


def test_tag_health_est_wrong_missing_pinned_to_current_model_version():
    """5a: est_wrong/est_missing must only count predictions from the current
    model version - a stale generation's rows must not leak in, even though
    the same tag also has current-version rows."""
    temp_dir, client, server = _setup()
    try:
        p_old = _upload_named(client)  # tagged "t", old-gen conf 0.05
        p_new_wrong = _upload_named(client)  # tagged "t", current-gen conf 0.05
        p_new_missing = _upload_named(client)  # untagged, current-gen conf 0.95

        now = datetime.utcnow()

        def seed(session):
            session.add(Tag(picture_id=p_old, tag="t"))
            session.add(Tag(picture_id=p_new_wrong, tag="t"))
            # Old generation: would count as est_wrong under the pre-fix (unpinned)
            # query, but must be excluded now that a newer generation exists.
            session.add(
                TagPrediction(
                    picture_id=p_old,
                    tag="t",
                    confidence=0.05,
                    model_version="v_old",
                    predicted_at=now - timedelta(days=2),
                )
            )
            # Current generation: the only rows that should be counted.
            session.add(
                TagPrediction(
                    picture_id=p_new_wrong,
                    tag="t",
                    confidence=0.05,
                    model_version="v_new",
                    predicted_at=now,
                )
            )
            session.add(
                TagPrediction(
                    picture_id=p_new_missing,
                    tag="t",
                    confidence=0.95,
                    model_version="v_new",
                    predicted_at=now,
                )
            )
            session.commit()

        server.vault.db.run_task(seed)

        body = _rebuild_and_wait(client)
        t = {r["tag"]: r for r in body["rows"]}["t"]
        assert t["est_wrong"] == 1  # only p_new_wrong, not p_old
        assert t["est_missing"] == 1  # only p_new_missing
        assert t["has_model"] is True  # current-version predictions exist
    finally:
        _teardown(temp_dir, server)


def test_tag_health_est_missing_excludes_human_rejected_pictures():
    """est_missing must count only *un-reviewed* untagged pictures. A picture a
    human already REJECTED (NEG) keeps the tagger's original high confidence and
    stays untagged, so before the fix it was double-counted: once (wrongly) as
    est_missing and again (correctly) as a model_dispute. It must now be excluded
    from est_missing and appear only in model_disputes."""
    from pixlstash.utils.quality.anomaly_penalty import DEFAULT_TAG_PRECISION
    from pixlstash.utils.service.label_ledger import NEG, record_human_label

    temp_dir, client, server = _setup()
    try:
        p_unreviewed = _upload_named(client)  # untagged, conf 0.95, no human ruling
        p_rejected = _upload_named(client)  # untagged, conf 0.95, human REJECTED

        now = datetime.utcnow()

        def seed(session):
            session.add(
                TagPrediction(
                    picture_id=p_unreviewed,
                    tag="t",
                    confidence=0.95,
                    model_version="v1",
                    predicted_at=now,
                )
            )
            session.add(
                TagPrediction(
                    picture_id=p_rejected,
                    tag="t",
                    confidence=0.95,
                    model_version="v1",
                    predicted_at=now,
                )
            )
            session.commit()
            # Human REJECT via the real ledger path: sets label_state=NEG /
            # source=human but DELIBERATELY keeps the live 0.95 confidence, which
            # is exactly what made this row match est_missing's raw conditions.
            record_human_label(session, p_rejected, "t", NEG)
            session.commit()

        server.vault.db.run_task(seed)

        body = _rebuild_and_wait(client)
        t = {r["tag"]: r for r in body["rows"]}["t"]

        # Only the un-reviewed picture is counted; the human-rejected one is
        # excluded (pre-fix this was 2 - the bug).
        assert t["est_missing"] == 1
        # est_missing_adj tracks the corrected raw count (no report → fallback).
        assert t["est_missing_adj"] == round(1 * DEFAULT_TAG_PRECISION)
        # The rejected row is still surfaced as a model dispute (unchanged), so
        # nothing is lost - it just isn't double-counted as an estimated fix.
        assert t["model_disputes"] == 1
        assert t["has_model"] is True
    finally:
        _teardown(temp_dir, server)


def test_tag_health_est_wrong_excludes_human_confirmed_pictures():
    """est_wrong must count only *un-reviewed* tagged pictures. A picture a human
    already CONFIRMED (POS) keeps its Tag row, so a low-confidence prediction on it
    was double-counted before the fix: once (wrongly) as est_wrong and again
    (correctly) as a model_dispute. It must now be excluded from est_wrong and
    appear only in model_disputes."""
    from pixlstash.utils.quality.anomaly_penalty import DEFAULT_TAG_PRECISION
    from pixlstash.utils.service.label_ledger import POS, record_human_label

    temp_dir, client, server = _setup()
    try:
        p_unreviewed = _upload_named(client)  # tagged "t", conf 0.05, no ruling
        p_confirmed = _upload_named(client)  # tagged "t", conf 0.05, human CONFIRMED

        now = datetime.utcnow()

        def seed(session):
            session.add(Tag(picture_id=p_unreviewed, tag="t"))
            session.add(Tag(picture_id=p_confirmed, tag="t"))
            session.add(
                TagPrediction(
                    picture_id=p_unreviewed,
                    tag="t",
                    confidence=0.05,
                    model_version="v1",
                    predicted_at=now,
                )
            )
            session.add(
                TagPrediction(
                    picture_id=p_confirmed,
                    tag="t",
                    confidence=0.05,
                    model_version="v1",
                    predicted_at=now,
                )
            )
            session.commit()
            # Human CONFIRM via the real ledger path: label_state=POS/source=human
            # but keeps the live 0.05 confidence and the Tag row, which is exactly
            # what made this row match est_wrong's raw conditions.
            record_human_label(session, p_confirmed, "t", POS)
            session.commit()

        server.vault.db.run_task(seed)

        body = _rebuild_and_wait(client)
        t = {r["tag"]: r for r in body["rows"]}["t"]

        # Only the un-reviewed tagged picture is counted; the human-confirmed one
        # is excluded (pre-fix this was 2 - the bug).
        assert t["est_wrong"] == 1
        assert t["est_wrong_adj"] == round(1 * DEFAULT_TAG_PRECISION)
        # The confirmed row is still surfaced as a model dispute (unchanged).
        assert t["model_disputes"] == 1
        assert t["has_model"] is True
    finally:
        _teardown(temp_dir, server)


def test_tag_health_default_tag_merges_folds_across_all_signals():
    """5b: a DEFAULT_TAG_MERGES child ("extra digit") must fold into its
    parent's ("malformed hand") board row across every signal - not just
    est_wrong/est_missing - and must not get a row of its own."""
    temp_dir, client, server = _setup()
    try:
        # est_wrong: one parent-literal hit, one child-literal hit.
        p_a = _upload_named(client)  # tagged "malformed hand", conf 0.05
        p_b = _upload_named(client)  # tagged "extra digit", conf 0.05
        # est_missing: one parent-literal hit, one child-literal hit.
        p_c = _upload_named(client)  # untagged, "malformed hand" conf 0.95
        p_d = _upload_named(client)  # untagged, "extra digit" conf 0.95
        # verified + boundary: one parent-literal, one child-literal.
        p_e = _upload_named(client)  # tagged "malformed hand", human POS @ 0.5
        p_f = _upload_named(client)  # tagged "extra digit", human POS @ 0.5
        # model_disputes: one parent-literal, one child-literal.
        p_g = _upload_named(client)  # "malformed hand" human POS @ 0.05
        p_h = _upload_named(client)  # "extra digit" human POS @ 0.05
        # overturn_rate / last_reviewed_at: one parent-literal, one child-literal.
        p_i = _upload_named(client)  # suggestion "malformed hand", ACCEPTED
        p_j = _upload_named(client)  # suggestion "extra digit", DISMISSED
        # mismatch: a parent/child pair must NOT mismatch (same folded identity);
        # a parent/untagged pair must still mismatch.
        p_k = _upload_named(client)  # tagged "malformed hand"
        p_l = _upload_named(client)  # tagged "extra digit"
        p_m = _upload_named(client)  # tagged "malformed hand"
        p_n = _upload_named(client)  # untagged

        t1 = datetime.utcnow()
        t2 = t1 + timedelta(minutes=5)

        def seed(session):
            session.add(Tag(picture_id=p_a, tag="malformed hand"))
            session.add(Tag(picture_id=p_b, tag="extra digit"))
            session.add(Tag(picture_id=p_e, tag="malformed hand"))
            session.add(Tag(picture_id=p_f, tag="extra digit"))
            session.add(Tag(picture_id=p_k, tag="malformed hand"))
            session.add(Tag(picture_id=p_l, tag="extra digit"))
            session.add(Tag(picture_id=p_m, tag="malformed hand"))

            session.add(
                TagPrediction(
                    picture_id=p_a,
                    tag="malformed hand",
                    confidence=0.05,
                    model_version="v1",
                )
            )
            session.add(
                TagPrediction(
                    picture_id=p_b,
                    tag="extra digit",
                    confidence=0.05,
                    model_version="v1",
                )
            )
            session.add(
                TagPrediction(
                    picture_id=p_c,
                    tag="malformed hand",
                    confidence=0.95,
                    model_version="v1",
                )
            )
            session.add(
                TagPrediction(
                    picture_id=p_d,
                    tag="extra digit",
                    confidence=0.95,
                    model_version="v1",
                )
            )
            session.add(
                TagPrediction(
                    picture_id=p_e,
                    tag="malformed hand",
                    confidence=0.5,
                    model_version="v1",
                    label_state="POS",
                    label_source="human",
                )
            )
            session.add(
                TagPrediction(
                    picture_id=p_f,
                    tag="extra digit",
                    confidence=0.5,
                    model_version="v1",
                    label_state="POS",
                    label_source="human",
                )
            )
            session.add(
                TagPrediction(
                    picture_id=p_g,
                    tag="malformed hand",
                    confidence=0.05,
                    model_version="v1",
                    label_state="POS",
                    label_source="human",
                )
            )
            session.add(
                TagPrediction(
                    picture_id=p_h,
                    tag="extra digit",
                    confidence=0.05,
                    model_version="v1",
                    label_state="POS",
                    label_source="human",
                )
            )

            session.add(
                TagSuggestion(
                    picture_id=p_i,
                    tag="malformed hand",
                    direction="add",
                    source="model",
                    score=1.0,
                    status="ACCEPTED",
                    reviewed_at=t1,
                )
            )
            session.add(
                TagSuggestion(
                    picture_id=p_j,
                    tag="extra digit",
                    direction="add",
                    source="model",
                    score=1.0,
                    status="DISMISSED",
                    reviewed_at=t2,
                )
            )

            a, b = PictureLikeness.canon_pair(p_k, p_l)
            session.add(
                PictureLikeness(
                    picture_id_a=a, picture_id_b=b, likeness=0.99, metric="cosine"
                )
            )
            a2, b2 = PictureLikeness.canon_pair(p_m, p_n)
            session.add(
                PictureLikeness(
                    picture_id_a=a2, picture_id_b=b2, likeness=0.99, metric="cosine"
                )
            )
            session.commit()

        seed_likeness_stable(server, seed)

        body = _rebuild_and_wait(client)
        rows = {r["tag"]: r for r in body["rows"]}

        # The child never gets a row of its own.
        assert "extra digit" not in rows
        mh = rows["malformed hand"]

        assert mh["est_wrong"] == 2  # p_a (parent) + p_b (child, folded)
        assert mh["est_missing"] == 2  # p_c (parent) + p_d (child, folded)

        # pred_agg: 8 total predictions (p_a, p_c, p_e, p_g on the parent literal;
        # p_b, p_d, p_f, p_h on the child literal), 4 verified (p_e/p_f/p_g/p_h),
        # 2 in the boundary band (p_e/p_f @ 0.5), all 8 on the current version.
        assert abs(mh["verified_pct"] - 4 / 8) < 1e-9
        assert abs(mh["boundary_pct"] - 2 / 8) < 1e-9
        assert mh["has_model"] is True

        # model_disputes: p_g (parent) + p_h (child, folded).
        assert mh["model_disputes"] == 2

        # overturn_rate/last_reviewed_at fold the child's suggestion in too, and
        # the later (child's) reviewed_at wins.
        assert mh["overturn_rate"] == 0.5
        assert mh["last_reviewed_at"] == t2.isoformat()

        # mismatch: parent/child pair folds to the same identity (no mismatch);
        # parent/untagged pair still mismatches.
        assert mh["mismatch"] == 1
    finally:
        _teardown(temp_dir, server)


def test_tag_health_soft_deleted_pictures_excluded_from_unscoped_board():
    """Soft-deleted pictures must not pollute the UNSCOPED cached board.

    The unscoped pred_agg / model_disputes / last_reviewed / overturn queries
    join Picture and filter `deleted.is_(False)` just like est_wrong/est_missing,
    so a deleted picture's predictions and suggestions never inflate
    verified_pct/boundary_pct/has_model/model_disputes/overturn_rate/
    last_reviewed_at. The unscoped board must therefore agree, tag-for-tag, with
    a board scoped to exactly the live pictures (the scope helper already
    excludes deleted).
    """
    temp_dir, client, server = _setup()
    try:
        # Live pictures for tag "t".
        l_wrong = _upload_named(client)  # tagged "t", conf 0.05, human POS (dispute)
        l_est_wrong = _upload_named(client)  # tagged "t", conf 0.05, un-reviewed
        l_missing = _upload_named(client)  # untagged, conf 0.95
        l_boundary = _upload_named(client)  # conf 0.50 (boundary mass)
        # Deleted pictures that would inflate every fixed signal if counted.
        d1 = _upload_named(client)
        d2 = _upload_named(client)
        d3 = _upload_named(client)
        d_est_wrong = _upload_named(client)  # deleted, tagged "t", 0.05, un-reviewed

        now = datetime.utcnow()
        t_live = now - timedelta(minutes=10)
        t_later = now  # strictly later than the live review timestamp

        def seed(session):
            session.add(Tag(picture_id=l_wrong, tag="t"))
            # model_disputes + verified (human POS the model doubts - NOT est_wrong,
            # since est_wrong now counts only un-reviewed pictures).
            session.add(
                TagPrediction(
                    picture_id=l_wrong,
                    tag="t",
                    confidence=0.05,
                    model_version="v1",
                    predicted_at=now,
                    label_state="POS",
                    label_source="human",
                )
            )
            # est_wrong: an un-reviewed tagged low-confidence prediction.
            session.add(Tag(picture_id=l_est_wrong, tag="t"))
            session.add(
                TagPrediction(
                    picture_id=l_est_wrong,
                    tag="t",
                    confidence=0.05,
                    model_version="v1",
                    predicted_at=now,
                )
            )
            # est_missing.
            session.add(
                TagPrediction(
                    picture_id=l_missing,
                    tag="t",
                    confidence=0.95,
                    model_version="v1",
                    predicted_at=now,
                )
            )
            # boundary mass.
            session.add(
                TagPrediction(
                    picture_id=l_boundary,
                    tag="t",
                    confidence=0.50,
                    model_version="v1",
                    predicted_at=now,
                )
            )
            # Live reviewed history: 1 ACCEPTED + 1 DISMISSED → overturn 0.5.
            session.add(
                TagSuggestion(
                    picture_id=l_wrong,
                    tag="t",
                    direction="remove",
                    source="near_neighbor",
                    score=1.0,
                    status="ACCEPTED",
                    reviewed_at=t_live,
                )
            )
            session.add(
                TagSuggestion(
                    picture_id=l_missing,
                    tag="t",
                    direction="add",
                    source="model",
                    score=1.0,
                    status="DISMISSED",
                    reviewed_at=t_live,
                )
            )

            # --- Deleted pictures: mark soft-deleted, then hang skewing signals
            # off them. If any of the four fixed queries fails to exclude
            # deleted, the asserted ratios/counts below shift. ---
            for pid in (d1, d2, d3):
                pic = session.get(Picture, pid)
                pic.deleted = True
                session.add(pic)
                session.add(Tag(picture_id=pid, tag="t"))
                # Each: verified, non-boundary, human-disputed prediction on "t".
                session.add(
                    TagPrediction(
                        picture_id=pid,
                        tag="t",
                        confidence=0.05,
                        model_version="v1",
                        predicted_at=now,
                        label_state="POS",
                        label_source="human",
                    )
                )
                # Two ACCEPTED reviewed later than the live ones - would push
                # overturn to 3/4 and last_reviewed to t_later if counted.
                session.add(
                    TagSuggestion(
                        picture_id=pid,
                        tag="t",
                        direction="remove",
                        source="near_neighbor",
                        score=1.0,
                        status="ACCEPTED",
                        reviewed_at=t_later,
                    )
                )

            # A deleted, un-reviewed est_wrong candidate: would add to est_wrong if
            # deleted pictures weren't excluded (proves deleted-exclusion still holds
            # for the un-reviewed est_wrong path after the human-decision fix).
            d_pic = session.get(Picture, d_est_wrong)
            d_pic.deleted = True
            session.add(d_pic)
            session.add(Tag(picture_id=d_est_wrong, tag="t"))
            session.add(
                TagPrediction(
                    picture_id=d_est_wrong,
                    tag="t",
                    confidence=0.05,
                    model_version="v1",
                    predicted_at=now,
                )
            )
            session.commit()

            # Scope = exactly the live pictures.
            ps = PictureSet(name="all_live")
            session.add(ps)
            session.commit()
            session.refresh(ps)
            for pid in (l_wrong, l_est_wrong, l_missing, l_boundary):
                session.add(PictureSetMember(set_id=ps.id, picture_id=pid))
            session.commit()
            return ps.id

        set_id = server.vault.db.run_task(seed)

        # Unscoped cached board (the path the fix corrects).
        body = _rebuild_and_wait(client)
        unscoped = {r["tag"]: r for r in body["rows"]}["t"]

        # Board scoped to exactly the live pictures.
        scoped_resp = client.get(f"{API}/tag_health", params={"set_id": set_id})
        assert scoped_resp.status_code == 200, scoped_resp.text
        scoped = {r["tag"]: r for r in scoped_resp.json()["rows"]}["t"]

        # Expected values - live pictures only (4 predictions: 1 verified/disputed,
        # 1 un-reviewed est_wrong, 1 est_missing, 1 boundary; overturn 1/1; last
        # review at t_live).
        signal_keys = [
            "est_wrong",
            "est_missing",
            "verified_pct",
            "boundary_pct",
            "has_model",
            "model_disputes",
            "overturn_rate",
            "last_reviewed_at",
        ]
        for key in signal_keys:
            assert unscoped[key] == scoped[key], (
                f"unscoped/scoped disagree on {key}: "
                f"{unscoped[key]!r} != {scoped[key]!r}"
            )

        # Only the live un-reviewed l_est_wrong counts; the live human-POS l_wrong is
        # a dispute (not est_wrong), and the deleted un-reviewed d_est_wrong is excluded.
        assert unscoped["est_wrong"] == 1
        assert unscoped["est_missing"] == 1
        # pred_agg over 4 live predictions: 1 verified, 1 boundary. Deleted rows
        # (all verified, non-boundary) would skew these if counted.
        assert abs(unscoped["verified_pct"] - 1 / 4) < 1e-9
        assert abs(unscoped["boundary_pct"] - 1 / 4) < 1e-9
        assert unscoped["has_model"] is True
        # Only l_wrong disputes; the 3 deleted human POS @0.05 rows must not add.
        assert unscoped["model_disputes"] == 1
        # 1 ACCEPTED / 1 DISMISSED live; deleted ACCEPTED rows must not shift it.
        assert unscoped["overturn_rate"] == 0.5
        # The later deleted reviews must not win "last reviewed".
        assert unscoped["last_reviewed_at"] == t_live.isoformat()
    finally:
        _teardown(temp_dir, server)


def test_tag_health_deleted_only_tag_excluded_from_unscoped_board():
    """A tag that exists ONLY on soft-deleted pictures must not appear as a row
    on the UNSCOPED board.

    The `all_tags` universe (ground_truth_tags / predicted_tags) joins Picture
    and filters `deleted.is_(False)`, matching every signal query, so a tag
    reachable only through deleted pictures - via a `Tag` row or a
    `TagPrediction` row - produces no board row at all (rather than a spurious
    all-zero one). A tag on a live picture still gets its row.
    """
    temp_dir, client, server = _setup()
    try:
        live = _upload_named(client)  # tag "live_tag" (ground truth)
        live_pred = _upload_named(client)  # prediction "live_pred_tag"
        d_gt = _upload_named(client)  # deleted, tag "deleted_gt_only"
        d_pred = _upload_named(client)  # deleted, prediction "deleted_pred_only"

        now = datetime.utcnow()

        def seed(session):
            session.add(Tag(picture_id=live, tag="live_tag"))
            session.add(
                TagPrediction(
                    picture_id=live_pred,
                    tag="live_pred_tag",
                    confidence=0.5,
                    model_version="v1",
                    predicted_at=now,
                )
            )
            # Soft-deleted pictures carrying a tag / prediction no live picture has.
            for pid in (d_gt, d_pred):
                pic = session.get(Picture, pid)
                pic.deleted = True
                session.add(pic)
            session.add(Tag(picture_id=d_gt, tag="deleted_gt_only"))
            session.add(
                TagPrediction(
                    picture_id=d_pred,
                    tag="deleted_pred_only",
                    confidence=0.5,
                    model_version="v1",
                    predicted_at=now,
                )
            )
            session.commit()

        server.vault.db.run_task(seed)

        body = _rebuild_and_wait(client)
        tags = {r["tag"] for r in body["rows"]}
        # Live-picture tags still appear.
        assert "live_tag" in tags
        assert "live_pred_tag" in tags
        # Deleted-only tags must not appear at all - not even as an all-zero row.
        assert "deleted_gt_only" not in tags
        assert "deleted_pred_only" not in tags
    finally:
        _teardown(temp_dir, server)


def test_tag_health_ground_truth_counts_pictures_and_folds_merge_aliases():
    """``ground_truth`` counts distinct in-scope, non-deleted PICTURES carrying
    the folded tag - not ``tag`` rows - in both the cached and the scoped payload.

    Three things are asserted deliberately:

    * a merge alias ("extra digit") contributes to its parent's
      ("malformed hand") count and gets no row of its own;
    * a picture carrying BOTH the child and the parent literal counts ONCE (the
      UNIQUE constraint is on (picture_id, tag), so both rows can coexist; a
      naive per-literal-tag sum would report it twice);
    * the resulting number equals ``|{pictures with any tag in scan_tag's equiv
      set}|`` - the correspondence the "this review would find nothing" gate
      rests on (see ``tag_scan_service.scan_tag``'s ``equiv``).
    """
    from pixlstash.db_models.tag import DEFAULT_TAG_MERGES

    temp_dir, client, server = _setup()
    try:
        p_parent = _upload_named(client)  # "malformed hand"
        p_child = _upload_named(client)  # "extra digit" (merge alias)
        p_both = _upload_named(client)  # both literals - one picture, counts once
        p_solo = _upload_named(client)  # unrelated tag with no aliases
        p_pred = _upload_named(client)  # prediction only, zero ground truth
        p_deleted = _upload_named(client)  # "malformed hand" but soft-deleted

        now = datetime.utcnow()

        def seed(session):
            session.add(Tag(picture_id=p_parent, tag="malformed hand"))
            session.add(Tag(picture_id=p_child, tag="extra digit"))
            session.add(Tag(picture_id=p_both, tag="malformed hand"))
            session.add(Tag(picture_id=p_both, tag="extra digit"))
            session.add(Tag(picture_id=p_solo, tag="solo"))
            session.add(Tag(picture_id=p_deleted, tag="malformed hand"))
            pic = session.get(Picture, p_deleted)
            pic.deleted = True
            session.add(pic)
            session.add(
                TagPrediction(
                    picture_id=p_pred,
                    tag="predicted_only",
                    confidence=0.5,
                    model_version="v1",
                    predicted_at=now,
                )
            )
            ps = PictureSet(name="gt_scope")
            session.add(ps)
            session.commit()
            session.refresh(ps)
            # Scope holds the parent-literal picture and the both-literals one.
            session.add(PictureSetMember(set_id=ps.id, picture_id=p_parent))
            session.add(PictureSetMember(set_id=ps.id, picture_id=p_both))
            session.commit()
            return ps.id

        set_id = server.vault.db.run_task(seed)

        body = _rebuild_and_wait(client)
        rows = {r["tag"]: r for r in body["rows"]}

        assert "extra digit" not in rows  # the child never gets its own row
        # p_parent + p_child + p_both == 3 distinct live pictures; p_both is one
        # picture despite holding two literal tag rows, and p_deleted is excluded.
        assert rows["malformed hand"]["ground_truth"] == 3
        assert rows["solo"]["ground_truth"] == 1
        # A tag known only from a prediction has no confirmed examples at all.
        assert rows["predicted_only"]["ground_truth"] == 0

        # The count must equal scan_tag's own equivalence-set picture count,
        # built here exactly the way scan_tag builds `equiv` / `concept`.
        equiv = {"malformed hand"} | {
            child
            for child, parent in DEFAULT_TAG_MERGES.items()
            if parent == "malformed hand"
        }

        def _scan_concept(session):
            return set(
                session.exec(
                    select(Tag.picture_id)
                    .join(Picture, Picture.id == Tag.picture_id)
                    .where(Picture.deleted.is_(False), Tag.tag.in_(sorted(equiv)))
                ).all()
            )

        concept = server.vault.db.run_immediate_read_task(_scan_concept)
        assert concept == {p_parent, p_child, p_both}
        assert rows["malformed hand"]["ground_truth"] == len(concept)

        # Scoped payload carries the field too, restricted to the scope: only
        # p_parent and p_both are in the set.
        scoped = client.get(f"{API}/tag_health", params={"set_id": set_id}).json()
        assert scoped["scoped"] is True
        srows = {r["tag"]: r for r in scoped["rows"]}
        assert srows["malformed hand"]["ground_truth"] == 2
        assert "extra digit" not in srows
        assert "solo" not in srows  # p_solo is outside the scope
    finally:
        _teardown(temp_dir, server)


def test_tag_health_zero_ground_truth_agrees_with_scan_confidence_fallback():
    """The "a review would find nothing" gate's correctness proof, as a test.

    When a tag has zero ground truth, ``tag_scan_service.scan_tag`` takes its
    near-zero-ground-truth branch (``n_ground_truth < MIN_GROUND_TRUTH_FOR_VOTE``)
    and its ONLY source of suspects is ``_load_confidence_fallback``, whose WHERE
    clause is documented as mirroring this module's ``est_missing`` aggregate
    one-for-one - same ``EST_MISSING_MIN_CONF``, same current-model-version pin,
    same ``Tag.picture_id IS NULL`` outer join, same ``label_state == "UNKNOWN"``
    (see the coupling note in ``tag_scan_service._load_confidence_fallback``'s
    docstring). The board can therefore promise "``ground_truth == 0`` and
    ``est_missing == 0`` ⇒ this review yields nothing".

    This asserts the two agree on the PICTURE SET, across every predicate that
    differentiates them at zero ground truth (confidence threshold, model-version
    pin, human ruling). Change one side's predicate without the other and this
    fails - which is the point. (``Tag.picture_id IS NULL`` is not exercised
    here because it is vacuously true for both at zero ground truth: no picture
    carries the tag.)
    """
    import numpy as np

    from pixlstash.services import tag_scan_service
    from pixlstash.utils.service.label_ledger import NEG, record_human_label

    temp_dir, client, server = _setup()
    try:
        p_hit_a = _upload_named(client)  # conf 0.95, current version, un-ruled
        p_hit_b = _upload_named(client)  # conf 0.95, current version, un-ruled
        p_low = _upload_named(client)  # conf 0.85 - under the threshold
        p_stale = _upload_named(client)  # conf 0.99 on a superseded version
        p_rejected = _upload_named(client)  # conf 0.95 but human-REJECTED (NEG)

        now = datetime.utcnow()
        # Deterministic, orthogonal embeddings: scan_tag needs >= 2 pictures with
        # embeddings to get past its guard, but the fallback branch never votes
        # on them, so their values are irrelevant to the outcome.
        for i, pid in enumerate((p_hit_a, p_hit_b, p_low, p_stale, p_rejected)):
            vec = np.zeros(512, dtype=np.float32)
            vec[i] = 1.0
            blob = vec.tobytes()

            def _set(session, pid=pid, blob=blob):
                pic = session.get(Picture, pid)
                pic.image_embedding = blob
                session.add(pic)
                session.commit()

            server.vault.db.run_task(_set)

        def seed(session):
            for pid, conf, version, at in (
                (p_hit_a, 0.95, "v_new", now),
                (p_hit_b, 0.95, "v_new", now),
                (p_low, 0.85, "v_new", now),
                (p_stale, 0.99, "v_old", now - timedelta(days=2)),
                (p_rejected, 0.95, "v_new", now),
            ):
                session.add(
                    TagPrediction(
                        picture_id=pid,
                        tag="coldstart",
                        confidence=conf,
                        model_version=version,
                        predicted_at=at,
                    )
                )
            session.commit()
            # Human REJECT keeps the 0.95 confidence and writes no Tag row, so
            # only the label_state filter can exclude it - on both sides.
            record_human_label(session, p_rejected, "coldstart", NEG)
            session.commit()

        server.vault.db.run_task(seed)

        body = _rebuild_and_wait(client)
        row = {r["tag"]: r for r in body["rows"]}["coldstart"]

        # Premise of the gate: no confirmed examples anywhere.
        assert row["ground_truth"] == 0
        assert row["est_missing"] == 2

        res = tag_scan_service.scan_tag(server.vault, "coldstart", project=None)
        assert res["count"] == 2
        assert res["added"] == 2
        assert res["removed"] == 0

        def _suspects(session):
            return {
                (int(s.picture_id), s.reason)
                for s in session.exec(
                    select(TagSuggestion).where(TagSuggestion.tag == "coldstart")
                ).all()
            }

        suspects = server.vault.db.run_immediate_read_task(_suspects)
        # The fallback branch actually ran (not the kNN vote).
        assert all("no confirmed examples yet" in reason for _, reason in suspects)
        # ...and it agrees with est_missing on the exact picture set.
        assert {pid for pid, _ in suspects} == {p_hit_a, p_hit_b}
        assert row["est_missing"] == len(suspects)
    finally:
        _teardown(temp_dir, server)


def test_tag_health_est_adj_reflects_precision_discount_and_fallback():
    """3: est_wrong_adj/est_missing_adj discount by the tag's measured precision
    (from the latest TaggerRun report), falling back to DEFAULT_TAG_PRECISION
    for a tag no report covers."""
    temp_dir, client, server = _setup()
    try:
        # "known_tag": precision 0.7 from the pushed TaggerRun report.
        known_wrong = [_upload_named(client) for _ in range(3)]  # est_wrong = 3
        known_missing = [_upload_named(client) for _ in range(2)]  # est_missing = 2
        # "unknown_tag": no report entry -> DEFAULT_TAG_PRECISION fallback.
        unknown_wrong = [_upload_named(client) for _ in range(4)]  # est_wrong = 4
        unknown_missing = [_upload_named(client)]  # est_missing = 1

        def seed(session):
            session.add(
                TaggerRun(
                    run="run-1",
                    report={
                        "payload": {"per_tag": [{"tag": "known_tag", "precision": 0.7}]}
                    },
                )
            )
            for pid in known_wrong:
                session.add(Tag(picture_id=pid, tag="known_tag"))
                session.add(
                    TagPrediction(
                        picture_id=pid,
                        tag="known_tag",
                        confidence=0.05,
                        model_version="v1",
                    )
                )
            for pid in known_missing:
                session.add(
                    TagPrediction(
                        picture_id=pid,
                        tag="known_tag",
                        confidence=0.95,
                        model_version="v1",
                    )
                )
            for pid in unknown_wrong:
                session.add(Tag(picture_id=pid, tag="unknown_tag"))
                session.add(
                    TagPrediction(
                        picture_id=pid,
                        tag="unknown_tag",
                        confidence=0.05,
                        model_version="v1",
                    )
                )
            for pid in unknown_missing:
                session.add(
                    TagPrediction(
                        picture_id=pid,
                        tag="unknown_tag",
                        confidence=0.95,
                        model_version="v1",
                    )
                )
            session.commit()

        server.vault.db.run_task(seed)

        body = _rebuild_and_wait(client)
        rows = {r["tag"]: r for r in body["rows"]}

        known = rows["known_tag"]
        assert known["est_wrong"] == 3
        assert known["est_missing"] == 2
        assert known["est_wrong_adj"] == round(3 * 0.7)
        assert known["est_missing_adj"] == round(2 * 0.7)

        unknown = rows["unknown_tag"]
        assert unknown["est_wrong"] == 4
        assert unknown["est_missing"] == 1
        assert unknown["est_wrong_adj"] == round(4 * DEFAULT_TAG_PRECISION)
        assert unknown["est_missing_adj"] == round(1 * DEFAULT_TAG_PRECISION)
    finally:
        _teardown(temp_dir, server)


# --------------------------------------------------------------------------- #
# Spec B: staleness detection (docs/reviews/tag-review-board-redesign-ux-spec.md
# §4). `_latest_health_relevant_change` mirrors review_service's
# `_latest_vault_change` (picture + tagger-run) plus the signal that idiom
# didn't need but the board does: reviewed TagSuggestions.
# --------------------------------------------------------------------------- #


def test_tag_health_stale_false_after_fresh_rebuild():
    temp_dir, client, server = _setup()
    try:
        pid = _upload_named(client)
        _seed_tag(server, pid, "fresh-tag")
        body = _rebuild_and_wait(client)
        assert body["computed_at"] is not None  # a real, non-vacuous build
        assert body["stale"] is False
    finally:
        _teardown(temp_dir, server)


def test_tag_health_stale_true_after_new_picture():
    """A new picture alone (review_service's own `_latest_vault_change`
    signal) must flip stale, with zero review activity."""
    temp_dir, client, server = _setup()
    try:
        pid = _upload_named(client)
        _seed_tag(server, pid, "picture-tag")
        body = _rebuild_and_wait(client)
        assert body["stale"] is False

        _upload_named(client)  # created_at newer than computed_at

        after = client.get(f"{API}/tag_health").json()
        assert after["stale"] is True
        # Rows/computed_at are untouched by a mere staleness check.
        assert after["computed_at"] == body["computed_at"]
    finally:
        _teardown(temp_dir, server)


def test_tag_health_stale_true_after_new_tagger_run():
    """A new TaggerRun ingest alone must flip stale."""
    temp_dir, client, server = _setup()
    try:
        pid = _upload_named(client)
        _seed_tag(server, pid, "tagger-run-tag")
        body = _rebuild_and_wait(client)
        assert body["stale"] is False

        def seed(session):
            session.add(TaggerRun(run="run-stale-check", model_version="v2"))
            session.commit()

        server.vault.db.run_task(seed)

        assert client.get(f"{API}/tag_health").json()["stale"] is True
    finally:
        _teardown(temp_dir, server)


def test_tag_health_stale_true_after_reviewed_suggestion():
    """A reviewed TagSuggestion alone (no new picture, no new TaggerRun) must
    flip stale - this is Spec B's added signal, beyond what
    review_service._latest_vault_change already covers, because every
    accept/dismiss/swap changes a tag's est_wrong/est_missing/mismatch/
    overturn_rate without necessarily touching Picture or TaggerRun."""
    temp_dir, client, server = _setup()
    try:
        pid = _upload_named(client)
        _seed_tag(server, pid, "reviewed-tag")
        body = _rebuild_and_wait(client)
        assert body["stale"] is False

        def seed(session):
            session.add(
                TagSuggestion(
                    picture_id=pid,
                    tag="reviewed-tag",
                    direction="add",
                    source="scan",
                    score=1.0,
                    status="ACCEPTED",
                    reviewed_at=datetime.utcnow(),
                )
            )
            session.commit()

        server.vault.db.run_task(seed)

        after = client.get(f"{API}/tag_health").json()
        assert after["stale"] is True
        assert after["computed_at"] == body["computed_at"]
    finally:
        _teardown(temp_dir, server)


def test_tag_health_rebuild_clears_staleness():
    temp_dir, client, server = _setup()
    try:
        pid = _upload_named(client)
        _seed_tag(server, pid, "clears-tag")
        body = _rebuild_and_wait(client)
        assert body["stale"] is False

        def seed(session):
            session.add(
                TagSuggestion(
                    picture_id=pid,
                    tag="clears-tag",
                    direction="add",
                    source="scan",
                    score=1.0,
                    status="ACCEPTED",
                    reviewed_at=datetime.utcnow(),
                )
            )
            session.commit()

        server.vault.db.run_task(seed)
        assert client.get(f"{API}/tag_health").json()["stale"] is True

        body2 = _rebuild_and_wait(client)
        assert body2["stale"] is False
        assert body2["computed_at"] != body["computed_at"]
    finally:
        _teardown(temp_dir, server)


def test_tag_health_scoped_response_is_never_stale():
    """A scoped board (project/set/character filter) is always computed live,
    never cached - stale=false regardless of vault activity."""
    temp_dir, client, server = _setup()
    try:
        pid = _upload_named(client)
        set_id = client.post(f"{API}/picture_sets", json={"name": "Scope"}).json()[
            "picture_set"
        ]["id"]

        def add_member(session):
            session.add(PictureSetMember(set_id=set_id, picture_id=pid))
            session.commit()

        server.vault.db.run_task(add_member)

        resp = client.get(f"{API}/tag_health", params={"set_id": set_id})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["scoped"] is True
        assert body["stale"] is False
    finally:
        _teardown(temp_dir, server)


def test_tag_health_auto_rebuild_finder_fires_when_stale_and_respects_debounce():
    """Spec B backend: a periodic finder (same shape as
    EnsureGfsSnapshotFinder's monotonic-clock check-interval gate)
    dispatches a rebuild through the same idempotent `start_rebuild` path
    `POST /tag_health/rebuild` uses when the cache is stale, and debounces -
    it must not requeue every tick even while the cache stays stale
    (AUTO_REBUILD_CHECK_INTERVAL_S)."""
    from pixlstash.tasks.tag_health_auto_rebuild_finder import (
        TagHealthAutoRebuildFinder,
    )

    temp_dir, client, server = _setup()
    try:
        pid = _upload_named(client)
        _seed_tag(server, pid, "auto-rebuild-base-tag")
        _rebuild_and_wait(client)
        assert client.get(f"{API}/tag_health").json()["stale"] is False

        def make_stale(session, suggestion_tag):
            session.add(
                TagSuggestion(
                    picture_id=pid,
                    tag=suggestion_tag,
                    direction="add",
                    source="scan",
                    score=1.0,
                    status="ACCEPTED",
                    reviewed_at=datetime.utcnow(),
                )
            )
            session.commit()

        server.vault.db.run_task(make_stale, "auto-rebuild-tag-1")
        assert client.get(f"{API}/tag_health").json()["stale"] is True

        # A fresh finder instance: _last_check_at starts at 0.0, so its very
        # first find_task() call always performs a real check (same shape as
        # EnsureGfsSnapshotFinder's precedent), independent of whether the
        # WorkPlanner-owned instance registered on this vault has already
        # used up its own check window.
        finder = TagHealthAutoRebuildFinder(server.vault)
        task = finder.find_task()
        assert task is not None, "finder did not dispatch a rebuild while stale"
        task.run()

        deadline = time.time() + 30
        body = client.get(f"{API}/tag_health").json()
        while time.time() < deadline and body["building"]:
            time.sleep(0.1)
            body = client.get(f"{API}/tag_health").json()
        assert not body["building"], "auto-dispatched rebuild never finished"
        assert body["stale"] is False, "auto-rebuild did not clear staleness"

        # Debounce: make it stale again immediately; the SAME finder instance
        # (still inside its check interval) must not dispatch a second
        # rebuild on every tick.
        server.vault.db.run_task(make_stale, "auto-rebuild-tag-2")
        assert client.get(f"{API}/tag_health").json()["stale"] is True
        assert finder.find_task() is None, "debounce window did not hold"
    finally:
        _teardown(temp_dir, server)
