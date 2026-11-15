"""Face clusters, on synthetic embeddings (no server)."""

import numpy as np

from pixlstash.services.face_clusters import centrality, cluster_faces, vector

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
