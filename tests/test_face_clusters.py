"""Face clusters, on synthetic embeddings (no server)."""

from types import SimpleNamespace

import numpy as np
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from pixlstash.db_models import Character, Face, Picture
from pixlstash.services import face_clusters
from pixlstash.services.face_clusters import (
    NotThePersonsFace,
    centrality,
    centroid,
    cluster_faces,
    probable_faces,
    ranked_by_likeness,
    reference_cluster_faces,
    reference_cluster_ids,
    seeded_cluster,
    set_reference_cluster,
    vector,
)

RNG = np.random.default_rng(3)
DIM = 64


def _person():
    return RNG.standard_normal(DIM)


def _faces_of(person, ids, spread=0.5):
    return [(pid, person + RNG.standard_normal(DIM) * spread) for pid in ids]


def test_two_people_and_a_stranger_give_two_clusters_and_one_unclustered():
    a, b, stranger = _person(), _person(), _person()
    faces = _faces_of(a, range(1, 9)) + _faces_of(b, range(11, 16))
    faces.append((99, stranger))
    clusters, unclustered = cluster_faces(faces, 0.45)
    assert [sorted(pid for pid, _ in c.members) for c in clusters] == [
        list(range(1, 9)),
        list(range(11, 16)),
    ]
    assert unclustered == [99]
    assert all(c.cohesion > 0.45 for c in clusters)


def test_the_planted_centre_is_the_most_central_member():
    person = _person()
    faces = [(1, person)] + _faces_of(person, range(2, 12), spread=0.8)
    (cluster,), _ = cluster_faces(faces, 0.3)
    assert cluster.members[0][0] == 1
    scores = [c for _, c in cluster.members]
    assert scores == sorted(scores, reverse=True)
    assert np.allclose(centrality([person, person * 2]), [1.0, 1.0])


def test_ranked_by_likeness_puts_the_centre_first_and_its_twin_above_others():
    a, b = _person(), _person()
    faces = _faces_of(a, range(1, 6)) + _faces_of(b, range(11, 16))
    centre = faces[7][1]
    faces.append((50, centre + RNG.standard_normal(DIM) * 0.05))
    ranked = ranked_by_likeness(faces, centre)
    assert ranked[0] == (13, pytest.approx(1.0))
    assert ranked[1][0] == 50
    values = [v for _, v in ranked]
    assert values == sorted(values, reverse=True)
    order = [pid for pid, _ in ranked]
    assert max(order.index(p) for p in range(11, 16)) < min(
        order.index(p) for p in range(1, 6)
    )
    assert ranked_by_likeness([], centre) == []


def test_a_seeded_cluster_ranks_every_face_by_the_picks_centre():
    a, b = _person(), _person()
    faces = _faces_of(a, range(1, 9)) + _faces_of(b, range(11, 16))
    cluster, ignored = seeded_cluster(faces, [1, 2, 3, 77])
    assert ignored == [77]
    assert sorted(pid for pid, _ in cluster.members) == sorted(p for p, _ in faces)
    values = [v for _, v in cluster.members]
    assert values == sorted(values, reverse=True)
    order = [pid for pid, _ in cluster.members]
    assert max(order.index(p) for p in range(1, 9)) < min(
        order.index(p) for p in range(11, 16)
    )
    by_id = dict(faces)
    expected = centroid([by_id[1], by_id[2], by_id[3]])
    assert cluster.centroid == pytest.approx(expected)
    assert 0 < cluster.cohesion < 1


def test_the_seeds_centroid_ranks_as_their_mean_likeness():
    """Why the seeded cluster ranks by one centre (#2738): mean likeness to each
    seed is the centroid likeness times a constant, so the order is the same."""
    a = _person()
    faces = _faces_of(a, range(1, 30), spread=1.2)
    seeds = [1, 5, 9, 14]
    cluster, _ = seeded_cluster(faces, seeds)
    units = {pid: vec / np.linalg.norm(vec) for pid, vec in faces}
    mean = {
        pid: float(np.mean([units[pid] @ units[s] for s in seeds])) for pid in units
    }
    assert [pid for pid, _ in cluster.members] == sorted(
        units, key=lambda pid: (-mean[pid], pid)
    )


def test_a_single_seed_holds_together_perfectly_and_no_seed_is_refused():
    faces = _faces_of(_person(), range(1, 5))
    cluster, ignored = seeded_cluster(faces, [2])
    assert cluster.cohesion == 1.0 and ignored == []
    assert cluster.members[0] == (2, pytest.approx(1.0))
    with pytest.raises(ValueError):
        seeded_cluster(faces, [98, 99])


def test_probable_faces_are_unassigned_look_alikes_on_other_pictures():
    """The best unassigned face a picture, never on a picture with the person's
    face, nor someone else's face, nor below the floor (an in-memory vault)."""
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    a, stranger = _person(), _person()
    with Session(engine) as session:
        person, other = Character(name="Wren"), Character(name="Ida")
        session.add_all([person, other])
        pictures = [Picture() for _ in range(6)]
        session.add_all(pictures)
        session.commit()
        hers, alike, twice, others, far, gone = (p.id for p in pictures)
        pictures[5].deleted = True

        def face(pid, index, vec, character=None):
            return Face(
                picture_id=pid,
                face_index=index,
                character_id=character,
                features=vec.astype(np.float32).tobytes(),
            )

        near = a + RNG.standard_normal(DIM) * 0.2
        session.add_all(
            [
                face(hers, 0, a, person.id),
                face(hers, 1, near),  # beside her own face: already ranked
                face(alike, 0, near),
                # Two look-alikes on one picture: the better one counts.
                face(twice, 0, a + RNG.standard_normal(DIM) * 0.1),
                face(twice, 1, near),
                face(others, 0, near, other.id),
                face(far, 0, stranger),
                face(gone, 0, near),
                face(alike, 1, a[: DIM // 2]),  # another width: not comparable
            ]
        )
        session.commit()
        vault = SimpleNamespace(
            db=SimpleNamespace(
                run_immediate_read_task=lambda fn, *args: fn(session, *args)
            )
        )
        found = probable_faces(vault, person.id, None, a)
        assert [f.picture_id for _, f, _ in found] == [twice, alike]
        twice_face = session.exec(
            select(Face).where(Face.picture_id == twice, Face.face_index == 0)
        ).one()
        assert found[0][0] == twice_face.id
        assert all(value >= 0.35 for *_, value in found)
        assert [
            f.picture_id
            for _, f, _ in probable_faces(vault, person.id, None, a, limit=1)
        ] == [twice]


def test_a_single_face_is_unclustered():
    assert cluster_faces([(7, _person())]) == ([], [7])
    assert cluster_faces([]) == ([], [])


def test_a_stricter_threshold_splits_a_loose_cluster():
    a = _person()
    # A second person halfway to the first: one person loosely, two strictly.
    b = a + _person() * 0.9
    faces = _faces_of(a, range(1, 7), 0.3) + _faces_of(b, range(11, 17), 0.3)
    loose, _ = cluster_faces(faces, 0.35)
    strict, _ = cluster_faces(faces, 0.75)
    assert [len(c.members) for c in loose] == [12]
    assert len(strict) == 2


def test_an_embedding_without_a_direction_is_no_vector():
    assert vector(np.zeros(8, dtype=np.float32).tobytes()) is None
    assert vector(np.full(8, np.nan, dtype=np.float32).tobytes()) is None
    assert vector(b"") is None
    assert vector(np.ones(8, dtype=np.float32).tobytes()) is not None


def test_a_face_without_an_embedding_does_not_carry_the_person():
    """The reference cluster holds only faces that can steer (an in-memory vault)."""
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        person = Character(name="Ida")
        session.add(person)
        pictures = [Picture() for _ in range(3)]
        session.add_all(pictures)
        session.commit()
        first, second = _person(), _person()
        both, bare, plain = (p.id for p in pictures)
        # Picture `both` carries the person twice: its first face counts.
        session.add_all(
            [
                Face(
                    picture_id=both,
                    face_index=0,
                    character_id=person.id,
                    features=first.astype(np.float32).tobytes(),
                ),
                Face(
                    picture_id=both,
                    face_index=1,
                    character_id=person.id,
                    features=second.astype(np.float32).tobytes(),
                ),
                Face(picture_id=bare, face_index=0, character_id=person.id),
                Face(
                    picture_id=plain,
                    face_index=0,
                    character_id=person.id,
                    features=first.astype(np.float32).tobytes(),
                ),
            ]
        )
        session.commit()
        flat = Picture()
        session.add(flat)
        session.commit()
        session.add(
            Face(
                picture_id=flat.id,
                face_index=0,
                character_id=person.id,
                features=np.zeros(64, dtype=np.float32).tobytes(),
            )
        )
        session.commit()
        # No embedding, or one with no direction: neither carries the person.
        with pytest.raises(NotThePersonsFace) as refused:
            set_reference_cluster(session, person.id, [both, bare, flat.id])
        assert refused.value.picture_ids == [bare, flat.id]
        assert reference_cluster_ids(session, person.id) == []
        set_reference_cluster(session, person.id, [both, plain])
        assert reference_cluster_ids(session, person.id) == [both, plain]
        chosen = {
            f.picture_id: f.face_index
            for f in reference_cluster_faces(session, person.id, 10)
        }
        assert chosen == {both: 0, plain: 0}
        # A first face whose embedding has no direction gives way to the next.
        first_face = session.exec(
            select(Face).where(Face.picture_id == both, Face.face_index == 0)
        ).one()
        first_face.features = np.zeros(64, dtype=np.float32).tobytes()
        session.commit()
        chosen = {
            f.picture_id: f.face_index
            for f in reference_cluster_faces(session, person.id, 10)
        }
        assert chosen == {both: 1, plain: 0}


def test_person_clusters_are_recomputed_when_a_face_changes(monkeypatch):
    """The grid filter's cache never answers for faces it was not built from."""
    a, b = _person(), _person()
    faces = [
        face_clusters.PersonFace(pid, vec, None, None)
        for pid, vec in _faces_of(a, [1, 2, 3], 0.1) + _faces_of(b, [4, 5], 0.1)
    ]
    monkeypatch.setattr(face_clusters, "read_person", lambda *_: (faces, []))
    clusters = face_clusters.person_cluster_ids(None, 99)
    assert [sorted(c) for c in clusters] == [[1, 2, 3], [4, 5]]
    # Picture 3's face is re-read as the other person's.
    faces[2] = face_clusters.PersonFace(
        3, b + RNG.standard_normal(DIM) * 0.1, None, None
    )
    clusters = face_clusters.person_cluster_ids(None, 99)
    assert [sorted(c) for c in clusters] == [[3, 4, 5], [1, 2]]
