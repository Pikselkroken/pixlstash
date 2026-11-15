"""Face clusters, on synthetic embeddings (no server)."""

import numpy as np
import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from pixlstash.db_models import Character, Face, Picture
from pixlstash.services.face_clusters import (
    NotThePersonsFace,
    centrality,
    cluster_faces,
    reference_cluster_faces,
    reference_cluster_ids,
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
        with pytest.raises(NotThePersonsFace) as refused:
            set_reference_cluster(session, person.id, [both, bare])
        assert refused.value.picture_ids == [bare]
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
