"""A person's face clusters and their reference cluster.

* ``GET /face-clusters?character_id=`` groups the person's faces by identity
  at a strictness, with each face's likeness to its group's centre.
  Untemplated so it can sit on the READ-token belt
  (``auth.READ_BLOCKED_GET_PATHS``), which matches literal paths only.
* ``PUT /characters/{id}/reference-cluster`` stores the pictures the owner
  picked as who the person is; the reference selectors then take their faces
  from them.

The work is in ``services/face_clusters.py``; this module translates.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from pixlstash.event_types import EventType
from pixlstash.services import face_clusters

MAX_REFERENCE_CLUSTER = 5000
MAX_SEEDS = 500


class ClusterMember(BaseModel):
    picture_id: int
    centrality: float = Field(
        description="Likeness (cosine) to the cluster's centre: how typical the face is."
    )
    score: int | None = Field(description="The owner's rating, or null when unrated.")
    format: str | None = Field(
        description="The file's format, for loading it full size at "
        "`/pictures/{id}.{format}`."
    )
    width: int | None = None
    height: int | None = None
    thumbnail_width: int | None = None
    thumbnail_height: int | None = None
    orientation: int | None = Field(
        default=None,
        description="EXIF orientation; with the sizes, the tile's aspect ratio.",
    )
    pinned: bool = Field(
        default=False, description="A picked picture of the seeded cluster."
    )
    probable: bool = Field(
        default=False,
        description="An unassigned face like the seeded cluster's centre: "
        "probably the person, not theirs yet.",
    )
    face_id: int | None = Field(
        default=None, description="The face, on a probable member: what assigns it."
    )


class FaceCluster(BaseModel):
    members: list[ClusterMember] = Field(description="Most central first.")
    size: int = Field(description="Members that are the person's; probable ones aside.")
    cohesion: float = Field(
        description="Mean likeness between every two members: how well it holds together."
    )
    reference_share: float = Field(
        description="The fraction of the members in the reference cluster."
    )
    is_reference: bool = Field(
        description="The cluster holding most of the reference cluster; at most one."
    )
    similarity_to_reference: float | None = Field(
        description="Likeness of its centre to the reference cluster's centre, or "
        "before one is picked to the largest cluster's; null with no clusters."
    )
    from_sheets: bool = Field(
        description="Most of its members were cut from character sheets."
    )


class UnclusteredFace(BaseModel):
    picture_id: int
    score: int | None
    similarity_to_reference: float | None
    format: str | None = Field(
        default=None, description="The file's format, as on a cluster member."
    )


class FaceClusters(BaseModel):
    threshold: float
    faces: int = Field(description="How many of the person's pictures were clustered.")
    reference_cluster: list[int] | None = Field(
        description="The reference cluster's live picture ids; null when none is picked."
    )
    clusters: list[FaceCluster] = Field(
        description="The reference cluster's first, then largest first."
    )
    unclustered: list[UnclusteredFace]
    centred: list[ClusterMember] | None = Field(
        None,
        description="With `centre`: every face of the person, its `centrality` "
        "the likeness to the centre face, most alike first; null without.",
    )
    seeded: FaceCluster | None = Field(
        None,
        description="With `seed`: every face of the person, and probable "
        "unassigned ones, ranked by likeness to the centre of the seeds' faces. "
        "Not in `clusters`, so their numbering is unchanged; null without.",
    )
    seeds_ignored: list[int] = Field(
        default_factory=list,
        description="The `seed` ids with no face of the person.",
    )


class ReferenceClusterRequest(BaseModel):
    picture_ids: list[int] = Field(max_length=MAX_REFERENCE_CLUSTER)


class ReferenceCluster(BaseModel):
    picture_ids: list[int]


def create_router(server) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/face-clusters",
        summary="Group a person's faces by identity",
        description=(
            "Clusters the person's faces (one a picture) by "
            "average-linkage on cosine distance: groups merge while the mean "
            "likeness between their faces clears `threshold`. Each member "
            "carries its likeness to its cluster's centre. A face that matches "
            "no one is listed under `unclustered`. With `centre`, `centred` "
            "also ranks every face by likeness to that picture's face (422 "
            "when it is not one of the person's faces). With `seed` (repeated), "
            "`seeded` ranks every face, and unassigned look-alikes marked "
            "`probable`, by likeness to the centre of those pictures' faces, "
            "the seeds `pinned` (422 when none has the person's face). "
            "Computed on each call; "
            "nothing is stored. Not to be confused with "
            "`/pictures/likeness-groups`, which joins near-duplicate pictures."
        ),
        response_model=FaceClusters,
    )
    def get_face_clusters(
        character_id: int,
        threshold: float = Query(
            face_clusters.SAME_PERSON,
            ge=0.05,
            le=0.95,
            description="The mean likeness two groups need to count as one person.",
        ),
        centre: int | None = Query(
            None,
            description="A picture of this person's: also rank every face by "
            "likeness to its face.",
        ),
        seed: list[int] = Query(
            [],
            description="Picked pictures (repeat the parameter): also rank every "
            "face by likeness to the centre of their faces.",
        ),
    ):
        if len(seed) > MAX_SEEDS:
            raise HTTPException(
                status_code=422, detail=f"At most {MAX_SEEDS} seeds, not {len(seed)}."
            )
        found = face_clusters.read_person(
            server.vault, character_id, face_clusters.model_pack_of(server.vault)
        )
        if found is None:
            raise HTTPException(status_code=404, detail=f"No character {character_id}.")
        faces, reference = found
        by_id = {face.picture_id: face for face in faces}
        # One answer whether the picture is missing, scrapheaped, someone
        # else's or of another pack: it reveals no more than the face list.
        if centre is not None and centre not in by_id:
            raise HTTPException(
                status_code=422,
                detail=f"Picture {centre} is not one of this person's faces.",
            )
        # ponytail: a re-centre re-runs the clustering below (about 1 s at 5k
        # faces); cache it keyed like person_cluster_ids if that is felt.
        clusters, unclustered = face_clusters.cluster_faces(
            [(face.picture_id, face.vector) for face in faces], threshold
        )
        order, best, shares = face_clusters.reference_first(clusters, reference)
        in_reference = [by_id[pid].vector for pid in reference if pid in by_id]
        if in_reference:
            target = face_clusters.centroid(in_reference)
        elif clusters:
            target = clusters[0].centroid
        else:
            target = None

        def to_target(vec):
            if target is None:
                return None
            return round(face_clusters.likeness(vec, target), 4)

        def member(pid, value, face=None, **flags):
            face = face or by_id[pid]
            return ClusterMember(
                picture_id=pid,
                centrality=round(value, 4),
                score=face.score,
                format=face.format,
                width=face.width,
                height=face.height,
                thumbnail_width=face.thumbnail_width,
                thumbnail_height=face.thumbnail_height,
                orientation=face.orientation,
                **flags,
            )

        centred = None
        if centre is not None:
            centred = [
                member(pid, value)
                for pid, value in face_clusters.ranked_by_likeness(
                    [(face.picture_id, face.vector) for face in faces],
                    by_id[centre].vector,
                )
            ]
        panels = face_clusters.sheet_panels(faces)
        seeded, seeds_ignored = None, []
        if seed:
            try:
                picks, seeds_ignored = face_clusters.seeded_cluster(
                    [(face.picture_id, face.vector) for face in faces], seed
                )
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail="None of these pictures has this person's face.",
                ) from exc
            pinned = set(seed)
            own = [pid for pid, _ in picks.members]
            ranked = [
                (value, pid, member(pid, value, pinned=pid in pinned))
                for pid, value in picks.members
            ] + [
                (
                    value,
                    face.picture_id,
                    member(
                        face.picture_id, value, face, probable=True, face_id=face_id
                    ),
                )
                for face_id, face, value in face_clusters.probable_faces(
                    server.vault,
                    character_id,
                    face_clusters.model_pack_of(server.vault),
                    picks.centroid,
                )
                # A pick without the person's face is "left out", not "probably".
                if face.picture_id not in pinned
            ]
            ranked.sort(key=lambda m: (-m[0], m[1]))
            reference_ids = set(reference)
            seeded = FaceCluster(
                members=[m for _, _, m in ranked],
                size=len(own),
                cohesion=round(picks.cohesion, 4),
                reference_share=round(
                    sum(pid in reference_ids for pid in own) / len(own), 4
                ),
                is_reference=False,
                similarity_to_reference=to_target(picks.centroid),
                from_sheets=2 * sum(pid in panels for pid in own) > len(own),
            )
        return FaceClusters(
            threshold=threshold,
            faces=len(faces),
            reference_cluster=reference or None,
            clusters=[
                FaceCluster(
                    members=[member(pid, c) for pid, c in clusters[i].members],
                    size=len(clusters[i].members),
                    cohesion=round(clusters[i].cohesion, 4),
                    reference_share=round(shares[i], 4),
                    is_reference=i == best,
                    similarity_to_reference=to_target(clusters[i].centroid),
                    from_sheets=2 * sum(pid in panels for pid, _ in clusters[i].members)
                    > len(clusters[i].members),
                )
                for i in order
            ],
            unclustered=[
                UnclusteredFace(
                    picture_id=pid,
                    score=by_id[pid].score,
                    similarity_to_reference=to_target(by_id[pid].vector),
                    format=by_id[pid].format,
                )
                for pid in unclustered
            ],
            centred=centred,
            seeded=seeded,
            seeds_ignored=seeds_ignored,
        )

    @router.put(
        "/characters/{id}/reference-cluster",
        summary="Set a person's reference cluster",
        description=(
            "Stores the pictures the owner picked as who the person is. Every "
            "id must carry the person's face (422 otherwise, naming the ids). "
            "The cluster is frozen: pictures added later never join it. An "
            "empty list clears it. With one, the person's reference faces are "
            "its faces, most central first."
        ),
        response_model=ReferenceCluster,
    )
    def put_reference_cluster(
        request: Request, id: int, payload: ReferenceClusterRequest
    ):
        try:
            stored = face_clusters.save_reference_cluster(
                server.vault, id, payload.picture_ids
            )
        except face_clusters.NotThePersonsFace as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "These pictures do not carry this person's face.",
                    "picture_ids": exc.picture_ids,
                },
            ) from exc
        if stored is None:
            raise HTTPException(status_code=404, detail=f"No character {id}.")
        server.vault.notify(
            EventType.CHANGED_CHARACTERS,
            {"origin_client_id": getattr(request.state, "origin_client_id", None)},
        )
        return ReferenceCluster(picture_ids=stored)

    return router
