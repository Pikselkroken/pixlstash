"""HTTP routes for tag groups: the library-wide tag-name -> group map (issue #2469).

Every route is OWNER_ONLY in ``pixlstash/authz/registry.py``: the map is library
vocabulary, not a per-picture fact, so narrowing it to a share token's scope is not
meaningful. Logic lives in :mod:`pixlstash.services.tag_group_service`; the handlers
translate requests and map :class:`TagGroupError` onto HTTP status codes.
"""

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from pixlstash.pixl_logging import get_logger
from pixlstash.services import operation_log_service, tag_group_service
from pixlstash.services.tag_group_service import TagGroupError

logger = get_logger(__name__)


class TagGroupItem(BaseModel):
    """One group."""

    id: int
    name: str
    position: int


class TagGroupMapResponse(BaseModel):
    """The whole map: groups in position order, and tag name -> group id."""

    model_config = ConfigDict(extra="allow")

    groups: list[TagGroupItem] = []
    tags: dict[str, int] = {}


class CreateTagGroupRequest(BaseModel):
    name: str


class CreateTagGroupResponse(TagGroupMapResponse):
    group: TagGroupItem


class ReorderTagGroupsRequest(BaseModel):
    ids: list[int]


class AssignTagGroupRequest(BaseModel):
    tags: list[str] = Field(default_factory=list)
    group_id: Optional[int] = None


def _refuse(exc: TagGroupError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def create_router(server) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/tag_groups",
        summary="The tag group map",
        response_model=TagGroupMapResponse,
    )
    def get_tag_groups(request: Request) -> dict[str, Any]:
        try:
            return tag_group_service.list_tag_groups(server.vault.db)
        except Exception:
            logger.exception("tag_groups: could not read the tag group map")
            raise

    @router.post(
        "/tag_groups",
        summary="Create a tag group (last in position)",
        response_model=CreateTagGroupResponse,
    )
    def create_tag_group(
        request: Request, payload: CreateTagGroupRequest
    ) -> dict[str, Any]:
        try:
            return tag_group_service.create_group(server.vault.db, payload.name)
        except TagGroupError as exc:
            raise _refuse(exc)
        except Exception:
            logger.exception("tag_groups: could not create group name=%r", payload.name)
            raise

    @router.put(
        "/tag_groups/order",
        summary="Reorder the tag groups",
        response_model=TagGroupMapResponse,
    )
    def reorder_tag_groups(
        request: Request, payload: ReorderTagGroupsRequest
    ) -> dict[str, Any]:
        try:
            return tag_group_service.reorder_groups(server.vault.db, payload.ids)
        except TagGroupError as exc:
            raise _refuse(exc)
        except Exception:
            logger.exception("tag_groups: could not reorder groups ids=%r", payload.ids)
            raise

    @router.post(
        "/tag_groups/assign",
        summary="File tags under a group (or make them Ungrouped)",
        description=(
            "Files each tag name under ``group_id``; ``null`` makes them Ungrouped. "
            "Tags are matched by exact name, and need not exist on any picture yet. "
            "A request that changes nothing records no undo entry. Otherwise the "
            "move is one undoable operation (``op_type`` ``tag_groups.move``)."
        ),
        response_model=TagGroupMapResponse,
    )
    def assign_tag_group(
        request: Request, payload: AssignTagGroupRequest
    ) -> dict[str, Any]:
        try:
            return tag_group_service.assign_tags(
                server.vault.db,
                payload.tags,
                payload.group_id,
                request_context=operation_log_service.request_context(request),
            )
        except TagGroupError as exc:
            raise _refuse(exc)
        except Exception:
            logger.exception(
                "tag_groups: could not assign %d tag(s) to group_id=%r",
                len(payload.tags),
                payload.group_id,
            )
            raise

    return router
