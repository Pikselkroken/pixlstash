"""Durable smart-score invalidation across the hub/vault boundary.

Changing a penalised tag's weight has to do two things: save the setting (hub)
and invalidate the cached scores it affects (vault). Those cannot share a
transaction, so the invalidation is *recorded* in the vault before the setting is
committed, and consumed together with the NULLing in one vault transaction.

The property under test is not "the happy path works". It is that **no crash
point leaves a saved setting with no record that a recompute is owed**, because
a stale smart score is a plausible number that nothing would ever notice.
"""

import json
import tempfile

import pytest
from sqlmodel import Session, delete, select

from pixlstash.db_models import Picture
from pixlstash.db_models.pending_score_invalidation import PendingScoreInvalidation
from pixlstash.db_models.tag import Tag
from pixlstash.server import Server
from pixlstash.utils.service.smart_score_invalidation import (
    apply_pending_invalidations,
    record_pending_invalidation,
)


@pytest.fixture(scope="module")
def server():
    """One Server for the module; building it runs migrations and vault startup."""
    with tempfile.TemporaryDirectory() as temp_dir:
        with Server(f"{temp_dir}/server-config.json") as srv:
            yield srv


@pytest.fixture(autouse=True)
def clean(server):
    """Start each test with no pictures, tags or pending records."""

    def _wipe(session: Session):
        session.exec(delete(PendingScoreInvalidation))
        session.exec(delete(Tag))
        session.exec(delete(Picture))
        session.commit()

    server.vault.db.run_task(_wipe)
    yield


def _picture_with_tag(server, tag: str, score: float = 4.2) -> int:
    """Create a scored picture carrying *tag*, and return its id."""

    def _create(session: Session):
        picture = Picture(file_path=f"/tmp/{tag}-{score}.png", smart_score=score)
        session.add(picture)
        session.commit()
        session.refresh(picture)
        session.add(Tag(picture_id=picture.id, tag=tag))
        session.commit()
        return picture.id

    return server.vault.db.run_task(_create)


def _score_of(server, picture_id: int):
    return server.vault.db.run_immediate_read_task(
        lambda session: session.get(Picture, picture_id).smart_score
    )


def _pending(server):
    return server.vault.db.run_immediate_read_task(
        lambda session: session.exec(select(PendingScoreInvalidation)).all()
    )


class TestRecording:
    def test_a_record_names_the_changed_tags(self, server):
        def _record(session: Session):
            record_pending_invalidation(session, {"Blurry", "extra_fingers"})
            session.commit()

        server.vault.db.run_task(_record)

        rows = _pending(server)
        assert len(rows) == 1
        assert json.loads(rows[0].tags) == ["blurry", "extra_fingers"]

    def test_recording_nothing_writes_nothing(self, server):
        def _record(session: Session):
            record_pending_invalidation(session, [])
            session.commit()

        server.vault.db.run_task(_record)
        assert _pending(server) == []

    def test_recording_does_not_invalidate_on_its_own(self, server):
        """Recording is a promise, not the act. The two are separable on purpose."""
        picture_id = _picture_with_tag(server, "blurry")

        def _record(session: Session):
            record_pending_invalidation(session, {"blurry"})
            session.commit()

        server.vault.db.run_task(_record)
        assert _score_of(server, picture_id) == pytest.approx(4.2)


class TestApplying:
    def test_applying_nulls_the_affected_score_and_clears_the_record(self, server):
        picture_id = _picture_with_tag(server, "blurry")

        def _record(session: Session):
            record_pending_invalidation(session, {"blurry"})
            session.commit()

        server.vault.db.run_task(_record)
        server.vault.db.run_task(apply_pending_invalidations)

        assert _score_of(server, picture_id) is None
        assert _pending(server) == []

    def test_an_unrelated_picture_keeps_its_score(self, server):
        """Scoped, not library-wide: only pictures carrying the tag can have moved."""
        affected = _picture_with_tag(server, "blurry")
        untouched = _picture_with_tag(server, "sharp", score=3.1)

        def _record(session: Session):
            record_pending_invalidation(session, {"blurry"})
            session.commit()

        server.vault.db.run_task(_record)
        server.vault.db.run_task(apply_pending_invalidations)

        assert _score_of(server, affected) is None
        assert _score_of(server, untouched) == pytest.approx(3.1)

    def test_applying_with_nothing_pending_is_a_no_op(self, server):
        assert server.vault.db.run_task(apply_pending_invalidations) == 0

    def test_applying_twice_is_harmless(self, server):
        """The finder may race the inline apply; the second run must not error."""
        _picture_with_tag(server, "blurry")

        def _record(session: Session):
            record_pending_invalidation(session, {"blurry"})
            session.commit()

        server.vault.db.run_task(_record)
        server.vault.db.run_task(apply_pending_invalidations)
        assert server.vault.db.run_task(apply_pending_invalidations) == 0

    def test_an_unreadable_record_is_dropped_and_logged(self, server):
        """A corrupt row must not wedge the queue behind it forever."""

        def _corrupt(session: Session):
            session.add(PendingScoreInvalidation(tags="not json at all"))
            session.commit()

        server.vault.db.run_task(_corrupt)
        server.vault.db.run_task(apply_pending_invalidations)
        assert _pending(server) == []


class TestTheCrashWindow:
    def test_a_record_that_survives_a_restart_is_still_applied(self, server):
        """The property the whole mechanism exists for.

        Simulates the process dying after the invalidation was recorded but
        before it was applied: the record is durable, so the next drain repairs
        the scores rather than leaving them silently wrong.
        """
        picture_id = _picture_with_tag(server, "blurry")

        def _record_only(session: Session):
            record_pending_invalidation(session, {"blurry"})
            session.commit()

        server.vault.db.run_task(_record_only)
        # ...process dies here. Nothing applied it.
        assert _score_of(server, picture_id) == pytest.approx(4.2)
        assert len(_pending(server)) == 1

        # On the next sweep the finder drains it.
        server.vault.db.run_task(apply_pending_invalidations)

        assert _score_of(server, picture_id) is None
        assert _pending(server) == []

    def test_the_finder_queues_work_only_when_something_is_pending(self, server):
        from pixlstash.tasks.pending_score_invalidation_finder import (
            PendingScoreInvalidationFinder,
        )

        finder = PendingScoreInvalidationFinder(vault=server.vault)
        assert finder.find_task() is None

        def _record(session: Session):
            record_pending_invalidation(session, {"blurry"})
            session.commit()

        server.vault.db.run_task(_record)
        finder._last_check_at = None  # skip the poll interval
        assert finder.find_task() is not None


class TestTheSettingsHandler:
    def test_changing_penalised_tags_leaves_no_record_behind(self, server):
        """End to end: the setting is saved and the record is consumed."""
        from fastapi.testclient import TestClient

        picture_id = _picture_with_tag(server, "blurry")
        client = TestClient(server.api)
        client.post("/login", json={"username": "pend", "password": "pendpass1"})

        response = client.patch(
            "/api/v1/users/me/config",
            json={"smart_score_penalised_tags": {"blurry": 0.9}},
        )

        assert response.status_code == 200, response.text
        assert _score_of(server, picture_id) is None
        assert _pending(server) == [], "the record should be consumed inline"
