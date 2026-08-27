"""The batched metadata-hash path must be byte-compatible and O(1) per flush.

``_after_flush_hash_updater`` runs on the single writer thread, inside the open
write transaction, and used to issue five SELECTs plus one UPDATE **per dirty
picture**. A 64-picture tag batch therefore added ~384 statements while holding
the SQLite write lock (issue #651). It now issues a constant number of
statements per flush.

Two things have to hold, and this file asserts both:

* **Byte-identical digests.** ``metadata_hash`` is persisted into snapshot files
  and compared against live rows (``services/snapshot_service.py``,
  ``services/restore/preview.py``). If the canonical string that is hashed
  changes by one byte, every picture in every existing snapshot starts
  comparing as "changed". ``test_batch_hash_equals_legacy_algorithm`` therefore
  compares against a frozen, verbatim copy of the pre-batching implementation
  rather than against the new code's own output.
* **Constant statement count.** ``test_flush_hash_update_is_constant_queries``
  measures the hook's own statements for N=2 and N=20 dirty pictures.

The frozen copy below deliberately still imports ``_HASH_SKIP_COLS`` from
``database``: which *columns* are hashed is a deliberate, migration-backed knob
(see ``0050_reset_metadata_hash_membership``), whereas how the values are
*serialised* is the thing that must never move. Freezing the skip set as well
would make this test fail for an intended column change while telling us
nothing about serialisation.
"""

import hashlib
import json
import re
import tempfile
import threading
from datetime import datetime

import numpy as np
import pytest
from sqlalchemy import event, inspect as sa_inspect, select as sa_select

from pixlstash.database import (
    _HASH_SKIP_COLS,
    _compute_picture_metadata_hash,
    _compute_picture_metadata_hashes,
)
from pixlstash.db_models import (
    Character,
    Face,
    Picture,
    PictureProjectMember,
    PictureSet,
    PictureSetMember,
    Project,
)
from pixlstash.db_models.snapshot import Snapshot
from pixlstash.db_models.tag import Tag
from pixlstash.server import Server
from tests.utils import wipe_tables


# ---------------------------------------------------------------------------
# Frozen pre-batching implementation
# ---------------------------------------------------------------------------


def _legacy_compute_picture_metadata_hash(session, picture_id):
    """VERBATIM copy of ``_compute_picture_metadata_hash`` before batching.

    Do NOT "fix" or tidy this function. Its value is that it is the historical
    algorithm, including the fact that ``faces`` holds SQLAlchemy ``Row``
    objects which ``json.dumps(..., default=str)`` serialises as strings such as
    ``"(0, 0, '[1, 2, 3, 4]', 5)"`` rather than as JSON arrays. That accident is
    baked into every stored hash.
    """
    pic = session.get(Picture, picture_id)
    if pic is None:
        return None
    col_vals: dict = {}
    for col_attr in sa_inspect(type(pic)).column_attrs:
        col = col_attr.key
        if col in _HASH_SKIP_COLS:
            continue
        val = getattr(pic, col, None)
        if isinstance(val, np.ndarray):
            continue
        if hasattr(val, "isoformat"):
            val = val.isoformat()
        col_vals[col] = val
    tags = sorted(
        session.execute(sa_select(Tag.tag).where(Tag.picture_id == picture_id))
        .scalars()
        .all()
    )
    faces = sorted(
        session.execute(
            sa_select(
                Face.frame_index, Face.face_index, Face.bbox_, Face.character_id
            ).where(Face.picture_id == picture_id)
        ).all()
    )
    set_ids = sorted(
        session.execute(
            sa_select(PictureSetMember.set_id).where(
                PictureSetMember.picture_id == picture_id
            )
        )
        .scalars()
        .all()
    )
    project_ids = sorted(
        session.execute(
            sa_select(PictureProjectMember.project_id).where(
                PictureProjectMember.picture_id == picture_id
            )
        )
        .scalars()
        .all()
    )
    state = {
        "cols": col_vals,
        "tags": tags,
        "faces": faces,
        "sets": set_ids,
        "projects": project_ids,
    }
    return hashlib.sha256(
        json.dumps(state, sort_keys=True, default=str).encode()
    ).hexdigest()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def server():
    """A vault with the WorkPlanner off.

    Background finders would otherwise run queries against the very tables the
    statement counter watches, on their own threads. The counter also filters by
    thread, so this is belt-and-braces rather than the only defence.
    """
    with tempfile.TemporaryDirectory() as tmp:
        config_path = f"{tmp}/server-config.json"
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump({"disable_background_workers": True}, handle)
        with Server(config_path) as srv:
            yield srv


@pytest.fixture(autouse=True)
def clean_db(server):
    server.vault.db.run_task(
        wipe_tables,
        [
            Snapshot,
            Tag,
            Face,
            PictureSetMember,
            PictureProjectMember,
            PictureSet,
            Project,
            Character,
            Picture,
        ],
    )
    yield


# ---------------------------------------------------------------------------
# Byte-compatibility against the historical algorithm
# ---------------------------------------------------------------------------


def _seed_every_branch(server) -> dict:
    """Create pictures covering every branch of the hash input.

    Returns a mapping of a readable label to the picture id, plus the id of a
    picture that is deleted again so the "missing row" branch is covered.
    """

    def _do(session):
        ids = {}

        # 1. A picture with nothing attached: no tags, faces, sets or projects.
        bare = Picture(file_path="bare.jpg", filename="bare.jpg")
        session.add(bare)

        # 2. Tags only, deliberately inserted out of sort order.
        tagged = Picture(file_path="tagged.jpg", filename="tagged.jpg")
        session.add(tagged)

        # 3. Faces: one fully populated, one with a NULL character_id and a
        #    NULL bbox, so the sorted-tuple stringification is exercised with
        #    and without None members.
        faced = Picture(file_path="faced.jpg", filename="faced.jpg")
        session.add(faced)

        # 4. Set + project membership, multiple of each so the sort matters.
        member = Picture(file_path="member.jpg", filename="member.jpg")
        session.add(member)

        # 5. Datetime columns (``imported_at`` is hashed; ``created_at`` is in
        #    the skip set) and an ndarray column (``likeness_parameters``),
        #    which the digest must skip rather than serialise.
        dated = Picture(
            file_path="dated.jpg",
            filename="dated.jpg",
            imported_at=datetime(2026, 8, 4, 12, 34, 56),
            created_at=datetime(2025, 1, 2, 3, 4, 5),
        )
        session.add(dated)

        # 6. Everything at once.
        combo = Picture(file_path="combo.jpg", filename="combo.jpg")
        session.add(combo)

        # 7. Created only so it can be deleted, giving us an id with no row.
        doomed = Picture(file_path="doomed.jpg", filename="doomed.jpg")
        session.add(doomed)

        session.commit()

        character = Character(name="Ada")
        session.add(character)
        picture_set_b = PictureSet(name="B set")
        picture_set_a = PictureSet(name="A set")
        session.add(picture_set_a)
        session.add(picture_set_b)
        project_b = Project(name="B project")
        project_a = Project(name="A project")
        session.add(project_a)
        session.add(project_b)
        session.commit()

        session.add(Tag(picture_id=tagged.id, tag="zebra"))
        session.add(Tag(picture_id=tagged.id, tag="apple"))

        session.add(
            Face(
                picture_id=faced.id,
                frame_index=0,
                face_index=0,
                bbox=[10, 20, 30, 40],
                character_id=character.id,
            )
        )
        session.add(
            Face(
                picture_id=faced.id,
                frame_index=0,
                face_index=1,
                character_id=None,
            )
        )

        for set_id in (picture_set_b.id, picture_set_a.id):
            session.add(PictureSetMember(set_id=set_id, picture_id=member.id))
        for project_id in (project_b.id, project_a.id):
            session.add(
                PictureProjectMember(project_id=project_id, picture_id=member.id)
            )

        session.add(Tag(picture_id=combo.id, tag="combo-tag"))
        session.add(
            Face(
                picture_id=combo.id,
                frame_index=1,
                face_index=0,
                bbox=[1, 2, 3, 4],
                character_id=character.id,
            )
        )
        session.add(PictureSetMember(set_id=picture_set_a.id, picture_id=combo.id))
        session.add(PictureProjectMember(project_id=project_a.id, picture_id=combo.id))
        session.commit()

        ids = {
            "bare": bare.id,
            "tagged": tagged.id,
            "faced": faced.id,
            "member": member.id,
            "dated": dated.id,
            "combo": combo.id,
            "missing": doomed.id,
        }

        session.delete(session.get(Picture, doomed.id))
        session.commit()
        return ids

    return server.vault.db.run_task(_do)


def test_batch_hash_equals_legacy_algorithm(server):
    """The batched digest reproduces the pre-batching one, byte for byte.

    This is the whole point of the change: the hashes are persisted in snapshot
    files, so a serialisation difference would silently mark every picture in
    every existing snapshot as changed. Compared against a frozen copy of the
    old function, not against the new implementation's own output.
    """
    ids = _seed_every_branch(server)
    picture_ids = list(ids.values())

    def _compare(session):
        # ``no_autoflush`` so the in-memory ndarray assigned below stays
        # in-memory: ``likeness_parameters`` is a LargeBinary column whose live
        # value on a freshly built Picture is an ``np.ndarray``, and the skip
        # branch only fires while it still is one.
        with session.no_autoflush:
            pic = session.get(Picture, ids["dated"])
            pic.likeness_parameters = np.arange(11, dtype=np.float32)
            legacy = {
                pid: _legacy_compute_picture_metadata_hash(session, pid)
                for pid in picture_ids
            }
            batched = _compute_picture_metadata_hashes(session, picture_ids)
            single = {
                pid: _compute_picture_metadata_hash(session, pid) for pid in picture_ids
            }
        return legacy, batched, single

    legacy, batched, single = server.vault.db.run_immediate_read_task(_compare)

    for label, pid in ids.items():
        if label == "missing":
            assert legacy[pid] is None, "seed failed: doomed picture still exists"
            assert pid not in batched, (
                "a picture id with no row must have no entry, mirroring the "
                "None the per-picture helper returns"
            )
            assert single[pid] is None
            continue
        assert legacy[pid] is not None, f"{label}: legacy produced no hash"
        assert batched.get(pid) == legacy[pid], (
            f"{label}: batched digest differs from the historical algorithm "
            f"(legacy={legacy[pid]!r} batched={batched.get(pid)!r}). Every "
            "stored snapshot hash for this picture would now compare as changed."
        )
        assert single[pid] == legacy[pid], (
            f"{label}: the per-picture wrapper drifted from the historical algorithm"
        )

    # Guard against the digest being trivially constant across the branches.
    assert len(set(batched.values())) == len(batched), (
        "distinct pictures produced identical digests; the branches are not "
        "actually contributing to the hash"
    )


def test_batch_matches_legacy_for_the_stored_hashes(server):
    """The hook's persisted value still equals the historical algorithm's.

    ``test_batch_hash_equals_legacy_algorithm`` proves the two functions agree.
    This proves the value the after-flush hook actually wrote to the column is
    that same value, i.e. the executemany UPDATE persists the right hash to the
    right row rather than smearing one picture's digest across the batch.
    """
    ids = _seed_every_branch(server)
    picture_ids = [pid for label, pid in ids.items() if label != "missing"]

    def _read(session):
        stored = {
            pid: h
            for pid, h in session.execute(
                sa_select(Picture.id, Picture.metadata_hash).where(
                    Picture.id.in_(picture_ids)
                )
            )
        }
        legacy = {
            pid: _legacy_compute_picture_metadata_hash(session, pid)
            for pid in picture_ids
        }
        return stored, legacy

    stored, legacy = server.vault.db.run_immediate_read_task(_read)
    for pid in picture_ids:
        assert stored[pid] is not None, f"picture {pid} has no persisted hash"
        assert stored[pid] == legacy[pid], (
            f"picture {pid}: persisted hash {stored[pid]!r} != historical "
            f"algorithm {legacy[pid]!r}"
        )


# ---------------------------------------------------------------------------
# Statement cost of the after-flush hook
# ---------------------------------------------------------------------------


# The hook's reads all come from these five tables; its single write is the only
# statement in the codebase shaped ``UPDATE picture SET metadata_hash``.
_HASH_INPUT_TABLES = re.compile(
    r"\bfrom (picture|tag|face|picturesetmember|pictureprojectmember)\b"
)


class _HashHookStatementCounter:
    """Count only the statements attributable to the metadata-hash hook.

    Deliberately NOT a count of every statement on the engine. The listener
    fires for every thread, and any background activity would otherwise be
    measured as if it were the hook (see the same trap documented in
    ``tests/test_picture_sets_query_cost.py``). Two filters keep the count
    attributable:

    * **Thread.** ``bind_current_thread`` is called from inside the write task,
      so only statements issued on the single writer thread are counted, and
      only from that point on.
    * **Shape.** SELECTs against the five tables the digest reads from, plus the
      ``UPDATE picture SET metadata_hash`` the hook is alone in issuing. The
      flush's own ``INSERT INTO tag`` and any ``UPDATE picture SET <other>`` are
      excluded, so what remains grows only if the hook goes back to per-picture
      work.
    """

    def __init__(self, server):
        self._engine = server.vault.db._engine
        self._thread_ident = None
        self.count = 0
        self.statements: list[str] = []

    def bind_current_thread(self) -> None:
        self._thread_ident = threading.get_ident()

    def _on_execute(self, conn, cursor, statement, parameters, context, executemany):
        if self._thread_ident is None or threading.get_ident() != self._thread_ident:
            return
        collapsed = " ".join(statement.split()).lower()
        is_hash_write = collapsed.startswith("update picture set metadata_hash")
        is_hash_read = collapsed.startswith("select") and _HASH_INPUT_TABLES.search(
            collapsed
        )
        if is_hash_write or is_hash_read:
            self.count += 1
            self.statements.append(collapsed[:140])

    def __enter__(self):
        event.listen(self._engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *exc_info):
        event.remove(self._engine, "before_cursor_execute", self._on_execute)
        return False


def _create_pictures(server, count: int) -> list[int]:
    def _do(session):
        pictures = [
            Picture(file_path=f"cost_{i}.jpg", filename=f"cost_{i}.jpg")
            for i in range(count)
        ]
        session.add_all(pictures)
        session.commit()
        return [pic.id for pic in pictures]

    return server.vault.db.run_task(_do)


def _tag_pictures_in_one_flush(server, counter, picture_ids: list[int]) -> None:
    """Dirty every picture in a single flush by adding one tag to each."""

    def _do(session):
        counter.bind_current_thread()
        for pid in picture_ids:
            session.add(Tag(picture_id=pid, tag=f"batch-{pid}"))
        session.commit()

    server.vault.db.run_task(_do)


def test_flush_hash_update_is_constant_queries(server):
    """The hook costs the same number of statements for 2 pictures as for 20.

    The old shape was five SELECTs plus one UPDATE per dirty picture, so this
    count was exactly ``6 * N`` - 12 here versus 120 - all of it inside the open
    write transaction on the single writer thread.
    """
    small_ids = _create_pictures(server, 2)
    with _HashHookStatementCounter(server) as small:
        _tag_pictures_in_one_flush(server, small, small_ids)

    large_ids = _create_pictures(server, 20)
    with _HashHookStatementCounter(server) as large:
        _tag_pictures_in_one_flush(server, large, large_ids)

    assert small.count > 0, (
        "counted nothing at all; the filter no longer matches the hook's "
        f"statements. Seen: {small.statements}"
    )
    assert small.count == large.count, (
        f"the hook's statement count grew with the batch size: "
        f"{small.count} for 2 pictures, {large.count} for 20. The per-picture "
        f"loop is back.\n2-picture statements: {small.statements}\n"
        f"20-picture statements: {large.statements}"
    )
    # Five batched reads plus one executemany UPDATE. Asserted as an upper bound
    # rather than an equality so an extra eager-load statement is not a failure,
    # while a return to per-picture work (12 for N=2) still is.
    assert small.count <= 8, (
        f"expected a handful of batched statements, got {small.count}: "
        f"{small.statements}"
    )


def test_all_dirty_pictures_get_their_own_hash_in_one_flush(server):
    """Constant cost must not mean a shared or missing hash.

    The executemany UPDATE binds one parameter set per picture; a bug there
    would either leave hashes NULL or write one picture's digest to all rows.
    """
    picture_ids = _create_pictures(server, 20)

    def _tag(session):
        for pid in picture_ids:
            session.add(Tag(picture_id=pid, tag=f"unique-{pid}"))
        session.commit()

    server.vault.db.run_task(_tag)

    stored = server.vault.db.run_immediate_read_task(
        lambda session: dict(
            session.execute(
                sa_select(Picture.id, Picture.metadata_hash).where(
                    Picture.id.in_(picture_ids)
                )
            ).all()
        )
    )
    assert all(stored[pid] is not None for pid in picture_ids), (
        "some pictures dirtied in the batched flush have no hash"
    )
    assert len(set(stored.values())) == len(picture_ids), (
        "pictures dirtied in one flush share a hash; the executemany UPDATE is "
        "not binding per-row values"
    )
