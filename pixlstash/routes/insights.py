"""HTTP route for the "About your library" screen (v1.11 Phase 6).

One GET, no writes anywhere on this surface. The findings and the reasoning
behind each of them live in
:mod:`pixlstash.services.library_insights_service`; this module is the schema
and the owner-only gate.
"""

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from pixlstash.services import library_insights_service


class InsightActionModel(BaseModel):
    """The tool that answers a finding, and how to open it on the right pictures.

    ``kind`` is a closed vocabulary the frontend switches on. Every value here
    is emitted by :mod:`pixlstash.services.library_insights_service`, and the
    frontend handles exactly these - a value listed and never sent is dead
    code on both sides:

    * ``unassigned_in_folder`` - the unassigned view, narrowed to ``path``.
    * ``unassigned_with_face`` - the unassigned view, narrowed to pictures that
      hold a face. Unassigned means no face here is named and ``with_face``
      means there is one, so the pair is the counted set exactly.
    * ``duplicates_in_folder`` - the duplicate queue, scoped to ``path``.
    * ``duplicates`` - the duplicate queue, unscoped. Sent when the two folders
      have no ancestor that would narrow anything.
    * ``settings`` - the settings dialog, on the pane named by ``tab``.
    """

    label: str = Field(description="The button's words, e.g. 'Sort them'.")
    note: str = Field(
        default="",
        description="What the button opens, under it - 'rapid triage', 'people review'.",
    )
    kind: str = Field(description="Which destination, from the closed set above.")
    path: Optional[str] = Field(
        default=None, description="Absolute folder path, for the folder-scoped kinds."
    )
    folder_label: Optional[str] = Field(
        default=None, description="That folder's last component, for the scope pill."
    )
    tab: Optional[str] = Field(
        default=None, description="Settings pane id, for ``kind='settings'``."
    )


class InsightFindingModel(BaseModel):
    """One row of the screen."""

    id: str = Field(description="Stable key for the check that produced this row.")
    state: str = Field(
        description=(
            "``todo`` when there is something to look at, ``clear`` when the check "
            "ran and found nothing wrong. A clear check still returns its row: the "
            "screen reports what it looked at, not only what it disliked."
        )
    )
    title: str = Field(description="The finding, with its number in it.")
    evidence: str = Field(description="The counts the finding was read off.")
    action: Optional[InsightActionModel] = Field(
        default=None, description="Null when there is nothing to open."
    )


class InsightsResponse(BaseModel):
    """Everything the screen renders."""

    total_pictures: int = Field(description="Live pictures in the library.")
    folder_pictures: int = Field(
        description=(
            "How many of those sit in a folder PixlStash reads in place. The rest "
            "are vault-managed and have no folder name of the owner's to report on."
        )
    )
    folders: int = Field(description="Distinct folders behind `folder_pictures`.")
    findings: list[InsightFindingModel] = Field(
        description="Ordered todo-first, then in check order."
    )


def create_router(server) -> APIRouter:
    """Return the insights router bound to *server*."""
    router = APIRouter()

    @router.get(
        "/insights",
        summary="Findings about the library, read-only",
        description=(
            "Every check the 'About your library' screen runs, in both "
            "directions: a check that found nothing returns ``state='clear'`` "
            "rather than disappearing. Computed live on each call - there is no "
            "cache to rebuild and no background job behind this - so 'Look "
            "again' is just another GET. Nothing on this surface writes, queues "
            "work, or moves a file."
        ),
        response_model=InsightsResponse,
    )
    def get_insights():
        return library_insights_service.build_insights(server.vault)

    return router
