"""Face clusters and a person's reference cluster.

A person's pictures can hold more than one individual: generated originals
drift from one picture to the next, and a character sheet drawn from text is
a new face altogether. This module groups a person's faces by identity, says
how typical each face is of its group, and keeps the one group the owner
picked as who the person *is*.

* :func:`cluster_faces` is average-linkage clustering on cosine distance, cut
  at ``1 - threshold``: two groups merge while the mean likeness between their
  faces clears *threshold*. Pure; no database.
* :func:`centrality` is each face's cosine similarity to its group's centroid
  (the mean of the unit embeddings). The cluster view sizes faces by it and
  the reference selectors take the most central first, so both agree on what
  "typical" means.
* The **reference cluster** is stored as picture ids at the moment of the pick
  (``CharacterReferenceMember``) and frozen: a picture added later never
  joins it. An id whose picture is deleted or no longer carries the person's
  face is dropped on read; a person with none left has no reference cluster.

With a reference cluster, both reference selectors take their faces from it,
most central first: ``select_reference_faces_for_character`` (sheet ranking,
the Sheets step's reference strip, grid likeness sort, face auto-assignment)
and ``dataset_picker.pick_references`` (the training-set picker).
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage
from sqlmodel import Session, delete, select

from pixlstash.db_models import Character, CharacterReferenceMember, Face, Picture
from pixlstash.pixl_logging import get_logger

logger = get_logger(__name__)

# The default "same person" cut, the sheet ranking's SOME_AGREEMENT. The view's
# Loose / Normal / Strict steps sit either side of it. Placeholders until they
# are calibrated on real libraries (docs/backend_architecture.md §25.30).
SAME_PERSON = 0.45
STRICTNESS = {"loose": 0.35, "normal": SAME_PERSON, "strict": 0.55}

# person_cluster_ids' last answers, keyed on everything they are computed from.
_PERSON_CLUSTERS: dict[tuple, list[list[int]]] = {}

# A sheet cuts into six panels sharing one source picture; four of a person's
# pictures from one source is a sheet, not an edit or two.
_SHEET_PANELS = 4


@dataclass
class Cluster:
    """A group of a person's faces that look like one individual.

    Attributes:
        members: ``(picture_id, centrality)``, most central first.
        cohesion: The mean likeness between every two members.
        centroid: The unit mean of the members' unit embeddings.
    """

    members: list[tuple[int, float]]
    cohesion: float
    centroid: np.ndarray = field(repr=False)


def vector(features) -> np.ndarray | None:
    """A stored face embedding as a float vector.

    ``None`` when empty, zero or not finite: such a vector has no direction,
    and one of them would make the whole person's clustering fail.
    """
    if features is None:
        return None
    arr = np.frombuffer(bytes(features), dtype=np.float32)
    if not arr.size or not np.isfinite(arr).all() or not arr.any():
        return None
    return arr


def _unit(vectors) -> np.ndarray:
    arr = np.asarray(vectors, dtype=np.float64)
    return arr / np.maximum(np.linalg.norm(arr, axis=-1, keepdims=True), 1e-8)


def centroid(vectors) -> np.ndarray:
    """The unit mean of the unit *vectors*."""
    return _unit(_unit(vectors).mean(axis=0))


def centrality(vectors) -> list[float]:
    """Each vector's cosine similarity to the centroid of all of them."""
    if len(vectors) == 0:
        return []
    return [float(s) for s in _unit(vectors) @ centroid(vectors)]


def likeness(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity of two centroids."""
    return float(np.clip(_unit(a) @ _unit(b), -1.0, 1.0))


def cluster_faces(
    faces: list[tuple[int, np.ndarray]], threshold: float = SAME_PERSON
) -> tuple[list[Cluster], list[int]]:
    """Group *faces* by identity.

    Args:
        faces: ``(picture_id, embedding)``, one face per picture.
        threshold: The mean likeness two groups need to merge.

    Returns:
        ``(clusters, unclustered)``: clusters of two or more, largest first,
        and the picture ids of the faces that matched no one.
    """
    # ponytail: O(n²) distance matrix; sample or cap if a person passes ~5k faces.
    if len(faces) < 2:
        return [], [pid for pid, _ in faces]
    units = _unit([vec for _, vec in faces])
    labels = fcluster(
        linkage(units, method="average", metric="cosine"),
        t=1.0 - threshold,
        criterion="distance",
    )
    groups: dict[int, list[int]] = {}
    for index, label in enumerate(labels):
        groups.setdefault(int(label), []).append(index)
    clusters, unclustered = [], []
    for indices in groups.values():
        if len(indices) == 1:
            unclustered.append(faces[indices[0]][0])
            continue
        vecs = units[indices]
        sims = vecs @ vecs.T
        n = len(indices)
        cohesion = float((sims.sum() - n) / (n * (n - 1)))
        members = sorted(
            zip((faces[i][0] for i in indices), centrality(vecs), strict=True),
            key=lambda m: (-m[1], m[0]),
        )
        clusters.append(Cluster(members, cohesion, centroid(vecs)))
    clusters.sort(key=lambda c: (-len(c.members), c.members[0][0]))
    return clusters, sorted(unclustered)


@dataclass
class PersonFace:
    """One face of a person, as the cluster view reads it."""

    picture_id: int
    vector: np.ndarray
    score: int | None
    source_picture_id: int | None
    format: str | None = None


def person_faces(
    session: Session, character_id: int, model_pack: str | None
) -> list[PersonFace]:
    """The person's faces: one a picture.

    Every picture counts, stacked or not, as it does for the reference
    selectors, so the view and what steers agree on what the cluster holds.
    Only embeddings from *model_pack* are compared, as the training-set picker
    does; a wholly legacy set (no pack recorded) is used as is. Where a picture
    carries the person twice, its first face counts.
    """
    rows = session.exec(
        select(
            Face.picture_id,
            Face.features,
            Face.model_pack,
            Picture.score,
            Picture.source_picture_id,
            Picture.format,
        )
        .join(Picture, Face.picture_id == Picture.id)
        .where(
            Face.character_id == character_id,
            Face.features.is_not(None),
            Picture.deleted.is_(False),
        )
        .order_by(Face.picture_id, Face.id)
    ).all()
    packs = {row[2] for row in rows}
    if model_pack and packs != {None}:
        rows = [row for row in rows if row[2] == model_pack]
    out, seen = [], set()
    for pid, features, _pack, score, source, fmt in rows:
        vec = vector(features)
        if pid in seen or vec is None:
            continue
        seen.add(pid)
        out.append(PersonFace(pid, vec, score, source, fmt))
    # Embeddings of another width (a pack change mid-refresh) cannot be compared.
    width = Counter(face.vector.size for face in out).most_common(1)
    return [face for face in out if width and face.vector.size == width[0][0]]


def sheet_panels(faces: list[PersonFace]) -> set[int]:
    """The pictures cut from a character sheet: many share one source."""
    sources = Counter(f.source_picture_id for f in faces if f.source_picture_id)
    return {
        f.picture_id
        for f in faces
        if f.source_picture_id and sources[f.source_picture_id] >= _SHEET_PANELS
    }


def _carrying_face(session: Session, character_id: int, picture_ids) -> set[int]:
    """The live pictures among *picture_ids* with a face of the person that has
    an embedding: one without has nothing to steer with."""
    if not picture_ids:
        return set()
    rows = session.exec(
        select(Face.picture_id, Face.features)
        .join(Picture, Face.picture_id == Picture.id)
        .where(
            Face.character_id == character_id,
            Face.picture_id.in_(list(picture_ids)),
            Face.features.is_not(None),
            Picture.deleted.is_(False),
        )
    ).all()
    # A zero or non-finite embedding has no direction to steer with either.
    return {pid for pid, features in rows if vector(features) is not None}


def reference_cluster_ids(session: Session, character_id: int) -> list[int]:
    """The person's reference cluster as it stands: live pictures with their face."""
    stored = session.exec(
        select(CharacterReferenceMember.picture_id).where(
            CharacterReferenceMember.character_id == character_id
        )
    ).all()
    live = _carrying_face(session, character_id, stored)
    return sorted(pid for pid in stored if pid in live)


class NotThePersonsFace(ValueError):
    """A picture asked for the reference cluster does not carry the person's face."""

    def __init__(self, picture_ids: list[int]):
        super().__init__(f"Pictures without this person's face: {picture_ids}")
        self.picture_ids = picture_ids


def set_reference_cluster(
    session: Session, character_id: int, picture_ids: list[int]
) -> list[int]:
    """Replace the person's reference cluster with *picture_ids*; ``[]`` clears it.

    Raises :class:`NotThePersonsFace` naming every id that does not carry the
    person's face, and stores nothing then. Returns the stored ids.
    """
    ids = sorted(set(picture_ids))
    missing = sorted(set(ids) - _carrying_face(session, character_id, ids))
    if missing:
        raise NotThePersonsFace(missing)
    session.exec(
        delete(CharacterReferenceMember).where(
            CharacterReferenceMember.character_id == character_id
        )
    )
    for pid in ids:
        session.add(CharacterReferenceMember(character_id=character_id, picture_id=pid))
    session.commit()
    logger.info(
        "Reference cluster of character %s set to %d pictures", character_id, len(ids)
    )
    return ids


def read_person_in_session(
    session: Session, character_id: int, model_pack: str | None
) -> tuple[list[PersonFace], list[int]] | None:
    """The person's faces and their reference cluster; ``None`` for no person."""
    if session.get(Character, character_id) is None:
        return None
    return (
        person_faces(session, character_id, model_pack),
        reference_cluster_ids(session, character_id),
    )


def read_person(vault, character_id: int, model_pack: str | None):
    """:func:`read_person_in_session` on the vault's reader."""
    return vault.db.run_immediate_read_task(
        read_person_in_session, character_id, model_pack
    )


def model_pack_of(vault) -> str | None:
    """The InsightFace pack whose embeddings the person's faces are read in."""
    engine = getattr(vault, "_engine", None)
    return getattr(engine, "insightface_model_pack", None) or getattr(
        vault, "_insightface_model_pack", None
    )


def reference_first(
    clusters: list[Cluster], reference: list[int]
) -> tuple[list[int], int | None, list[float]]:
    """The order the cluster view lists *clusters* in.

    Returns:
        ``(order, best, shares)``: cluster indices with the reference cluster
        first and the rest as given (largest first); the index of the cluster
        holding most of *reference*, or ``None`` when none holds any; and each
        cluster's share of members in *reference*.
    """
    chosen = set(reference)
    shares = [
        sum(pid in chosen for pid, _ in c.members) / len(c.members) for c in clusters
    ]
    best = max(range(len(clusters)), key=lambda i: shares[i], default=None)
    if best is not None and shares[best] == 0:
        best = None
    order = sorted(range(len(clusters)), key=lambda i: i != best)
    return order, best, shares


def person_cluster_ids(
    vault, character_id: int, threshold: float = SAME_PERSON
) -> list[list[int]] | None:
    """The picture ids of each of the person's clusters, as the cluster view
    numbers them: "Cluster N" is entry ``N - 1``. ``None`` for no person.

    Recomputed on each call, like the view; nothing is stored.
    """
    found = read_person(vault, character_id, model_pack_of(vault))
    if found is None:
        return None
    faces, reference = found
    # The grid asks once for its count and again for every stream batch; the
    # linkage is what costs (about 1 s at 5k faces), the read is not. Keyed on
    # the faces read, so any change to them clusters afresh.
    digest = hashlib.blake2b(digest_size=16)
    for face in faces:
        digest.update(face.picture_id.to_bytes(8, "little"))
        digest.update(face.vector.tobytes())
    key = (character_id, threshold, tuple(reference), digest.digest())
    cached = _PERSON_CLUSTERS.get(key)
    if cached is not None:
        return cached
    clusters, _ = cluster_faces(
        [(face.picture_id, face.vector) for face in faces], threshold
    )
    order, _, _ = reference_first(clusters, reference)
    result = [[pid for pid, _ in clusters[i].members] for i in order]
    # ponytail: keeps the last few keys only; an LRU if several people's
    # grids are filtered at once.
    if len(_PERSON_CLUSTERS) >= 4:
        _PERSON_CLUSTERS.pop(next(iter(_PERSON_CLUSTERS)))
    _PERSON_CLUSTERS[key] = result
    return result


def _save_in_session(session: Session, character_id: int, picture_ids: list[int]):
    if session.get(Character, character_id) is None:
        return None
    try:
        return set_reference_cluster(session, character_id, picture_ids)
    except NotThePersonsFace as exc:
        return exc


def save_reference_cluster(vault, character_id: int, picture_ids: list[int]):
    """:func:`set_reference_cluster` on the vault's writer.

    Returns the stored ids, or ``None`` when there is no such person; raises
    :class:`NotThePersonsFace`.
    """
    stored = vault.db.run_task(_save_in_session, character_id, picture_ids)
    if isinstance(stored, NotThePersonsFace):
        raise stored
    return stored


def most_central(rows: list, vector_of) -> list:
    """*rows* ordered by how typical their face is of all of them, most first.

    Rows whose embedding is empty or of a minority width are left out.
    """
    vecs = [(row, vector_of(row)) for row in rows]
    vecs = [(row, vec) for row, vec in vecs if vec is not None]
    width = Counter(vec.size for _, vec in vecs).most_common(1)
    vecs = [(row, vec) for row, vec in vecs if width and vec.size == width[0][0]]
    scores = centrality([vec for _, vec in vecs])
    order = sorted(range(len(vecs)), key=lambda i: -scores[i])
    return [vecs[i][0] for i in order]


def reference_cluster_faces(
    session: Session, character_id: int, max_refs: int
) -> list[Face]:
    """The person's faces on their reference cluster, most central first.

    Empty when the person has no reference cluster (or none of it is left).
    """
    ids = reference_cluster_ids(session, character_id)
    if not ids:
        return []
    faces = session.exec(
        select(Face)
        .where(
            Face.character_id == character_id,
            Face.picture_id.in_(ids),
            Face.features.is_not(None),
        )
        .order_by(Face.picture_id, Face.id)
    ).all()
    # The first usable face of each picture, as person_faces takes it.
    first = {}
    for face in faces:
        if vector(face.features) is not None:
            first.setdefault(face.picture_id, face)
    return most_central(list(first.values()), lambda face: vector(face.features))[
        :max_refs
    ]
