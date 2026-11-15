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


class FaceCluster(BaseModel):
    members: list[ClusterMember] = Field(description="Most central first.")
    size: int
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
            "no one is listed under `unclustered`. Computed on each call; "
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
    ):
        found = face_clusters.read_person(
            server.vault, character_id, face_clusters.model_pack_of(server.vault)
        )
        if found is None:
            raise HTTPException(status_code=404, detail=f"No character {character_id}.")
        faces, reference = found
        by_id = {face.picture_id: face for face in faces}
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

        panels = face_clusters.sheet_panels(faces)
        return FaceClusters(
            threshold=threshold,
            faces=len(faces),
            reference_cluster=reference or None,
            clusters=[
                FaceCluster(
                    members=[
                        ClusterMember(
                            picture_id=pid,
                            centrality=round(c, 4),
                            score=by_id[pid].score,
                            format=by_id[pid].format,
                        )
                        for pid, c in clusters[i].members
                    ],
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
                )
                for pid in unclustered
            ],
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
