"""HTTP routes for tag groups: the library-wide tag-name -> group map (issue #2469).

Every route is OWNER_ONLY in ``pixlstash/authz/registry.py``: the map is library
vocabulary, not a per-picture fact, so narrowing it to a share token's scope is not
meaningful. Logic lives in :mod:`pixlstash.services.tag_group_service`; the handlers
translate requests and map :class:`TagGroupError` onto HTTP status codes.
"""

from typing import Any, Literal, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from pixlstash.pixl_logging import get_logger
from pixlstash.services import operation_log_service, tag_group_service
from pixlstash.services.tag_group_service import (
    IMPORT_MAX_BYTES,
    TagGroupError,
)
from pixlstash.utils.service.picture_stats import clear_stats_cache

logger = get_logger(__name__)


class TagGroupItem(BaseModel):
    """One group. ``tag_count`` is every tag filed in it, library or not."""

    id: int
    name: str
    position: int
    tag_count: int = 0


class TagGroupMapResponse(BaseModel):
    """The map: groups in position order, the filings of library tags, two totals.

    ``tags`` holds only tags some picture carries; ``ungrouped_count`` counts the
    library's tags with no filing and ``all_count`` every known tag. The rest of the
    vocabulary is paged by ``GET /tag_groups/tags``.
    """

    model_config = ConfigDict(extra="allow")

    groups: list[TagGroupItem] = []
    tags: dict[str, int] = {}
    ungrouped_count: int = 0
    all_count: int = 0


class TagListItem(BaseModel):
    """One tag in the listing: its library picture count and its group (None = none)."""

    tag: str
    count: int
    group_id: Optional[int] = None


class TagListResponse(BaseModel):
    """One page of the listing; ``total`` counts every match, not the page."""

    total: int
    tags: list[TagListItem]


class CreateTagGroupRequest(BaseModel):
    name: str


class CreateTagGroupResponse(TagGroupMapResponse):
    group: TagGroupItem


class ReorderTagGroupsRequest(BaseModel):
    ids: list[int]


class AssignTagGroupRequest(BaseModel):
    tags: list[str] = Field(default_factory=list)
    group_id: Optional[int] = None


class TagImportResponse(BaseModel):
    """The import's plan, plus the map on a real (not dry) run."""

    plan: dict[str, Any]
    groups: Optional[list[TagGroupItem]] = None
    tags: Optional[dict[str, int]] = None
    ungrouped_count: Optional[int] = None
    all_count: Optional[int] = None


def _refuse(exc: TagGroupError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _read_capped(upload: UploadFile) -> bytes:
    """The upload's bytes, refused (413) when they pass the import cap."""
    # One byte past the cap is enough to tell that the cap was passed.
    data = upload.file.read(IMPORT_MAX_BYTES + 1)
    if len(data) > IMPORT_MAX_BYTES:
        raise TagGroupError(
            413,
            f"The tag file is larger than {IMPORT_MAX_BYTES // (1024 * 1024)} MB",
        )
    return data


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

    @router.get(
        "/tag_groups/tags",
        summary="Page through tags by group, with library picture counts",
        description=(
            "``group`` is a group id (every tag filed there), ``none`` (library tags "
            "with no filing) or ``all`` (filed tags and library tags). ``q`` matches "
            "a case-insensitive substring. ``sort`` is ``pictures`` (most pictures "
            "first, then name) or ``name``. ``count`` is the pictures in the library "
            "carrying the tag, 0 when none."
        ),
        response_model=TagListResponse,
    )
    def list_tag_group_tags(
        request: Request,
        group: str = Query(..., max_length=32),
        q: Optional[str] = Query(None, max_length=200),
        sort: Literal["pictures", "name"] = "pictures",
        offset: int = Query(0, ge=0, le=10_000_000),
        limit: int = Query(200, ge=1, le=500),
    ) -> dict[str, Any]:
        try:
            return tag_group_service.list_tags(
                server.vault.db,
                group=group,
                q=q,
                sort=sort,
                offset=offset,
                limit=limit,
            )
        except TagGroupError as exc:
            raise _refuse(exc)
        except Exception:
            logger.exception(
                "tag_groups: could not list tags group=%r q=%r sort=%r offset=%d",
                group,
                q,
                sort,
                offset,
            )
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
            result = tag_group_service.assign_tags(
                server.vault.db,
                payload.tags,
                payload.group_id,
                request_context=operation_log_service.request_context(request),
            )
            # Committed: a stats query narrowed by a group term now counts
            # different pictures, and no picture event will clear it.
            clear_stats_cache()
            return result
        except TagGroupError as exc:
            raise _refuse(exc)
        except Exception:
            logger.exception(
                "tag_groups: could not assign %d tag(s) to group_id=%r",
                len(payload.tags),
                payload.group_id,
            )
            raise

    @router.post(
        "/tag_groups/import",
        summary="Import a Danbooru tag file (.xlsx or .csv) as tag groups",
        description=(
            "Files every row by its ``category`` / ``subcategory`` (the ``*_en`` "
            "columns when present), whether or not a picture carries the tag yet. "
            "An adult row files under Adult; a row with no category is skipped. "
            "A Character row also files its franchise under Copyright. The file wins "
            "over defaults and Ungrouped tags. A tag the user filed under another "
            "group is a conflict: kept unless ``overwrite_user`` is true. ``dry_run`` "
            "reports the plan and changes nothing. A real run is one undoable "
            "operation (``op_type`` ``tag_groups.move``)."
        ),
        response_model=TagImportResponse,
        response_model_exclude_none=True,
    )
    def import_tag_groups(
        request: Request,
        file: UploadFile = File(...),
        dry_run: bool = Form(False),
        overwrite_user: bool = Form(False),
    ) -> dict[str, Any]:
        try:
            filename = file.filename or ""
            result = tag_group_service.import_tag_file(
                server.vault.db,
                filename=filename,
                data=_read_capped(file),
                dry_run=dry_run,
                overwrite_user=overwrite_user,
                request_context=operation_log_service.request_context(request),
            )
            if not dry_run:
                # Committed: a stats query narrowed by a group term now counts
                # different pictures, and no picture event will clear it.
                clear_stats_cache()
            return result
        except TagGroupError as exc:
            raise _refuse(exc)
        except Exception:
            logger.exception(
                "tag_groups: could not import %r (dry_run=%s, overwrite_user=%s)",
                file.filename,
                dry_run,
                overwrite_user,
            )
            raise

    return router
