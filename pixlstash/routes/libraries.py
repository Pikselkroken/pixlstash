"""The two library routes: list what is registered, and switch the active one.

Deliberately small. In the MVP nothing here accepts a host path, because
creating, attaching and detaching a library are done from the CLI. That is what
keeps the whole HTTP surface of this feature to one read and one state change.

**The two routes sit at different access tiers on purpose** (multi-library plan
§11 q3/q4). Listing is ``OWNER_ONLY``, so the Settings tab always renders for an
owner. Switching is ``LOCAL_OWNER_ONLY`` because it resets every connected
client's session rather than only the caller's, and takes the outgoing library's
share links offline: it exercises authority over other principals' state.

*Switching is not a confidentiality boundary* (corrected 2026-08-07). Plan §11 q4
justified the tier as stopping a stolen token from reaching every library by
switching. That held for the unpinned-token design it was written against; the
library pin landed later and closes it on its own. A token stamped for a
non-active library is refused on every data route, and minting is pinned too, so
switching locks a thief out of what they had and gains them nothing. This is a
single-owner convenience feature with a disruptive side effect, which is all the
tier needs to be about.

**Host information is locality-conditioned.** A password/cookie owner on the
machine, the LAN or Tailscale sees the folder path and exact CLI command; any
other owner sees neither unless ``allow_remote_host_ops`` is explicitly enabled.
An ``ALL`` bearer token over Tailscale remains subject to the separate
``require_local_for_write`` middleware, whose narrower ``is_local_ip`` predicate
does not include Tailscale CGNAT. Both routes are library-independent: they
return no library content and keep answering while a switch is in flight.
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from pixlstash.auth import is_local_or_tailscale_ip
from pixlstash.event_types import EventType
from pixlstash.hub.cli_hint import cli_hint, running_in_docker
from pixlstash.hub.registry import LibraryError, LibraryNotFoundError
from pixlstash.pixl_logging import get_logger
from pixlstash.services.library_switch_service import LibrarySwitchError

logger = get_logger(__name__)


class LibraryResponse(BaseModel):
    """One registered library as the Settings tab sees it."""

    model_config = ConfigDict(extra="allow")

    uuid: str = Field(
        description=(
            "Stable identity of the library, and the value to send when "
            "switching. Deliberately not the row id: a client left open across "
            "a detach and attach would otherwise name a different library."
        )
    )
    name: str
    is_active: bool
    is_reachable: bool = Field(
        description=(
            "Whether the library's folder and vault file are present right now. "
            "False for an unplugged drive, which is shown as 'Not found' rather "
            "than hidden."
        )
    )
    active_share_links: int = Field(
        default=0,
        description=(
            "How many resource-scoped share links belong to this library. "
            "Owner metadata used to warn before switching away; it contains "
            "no host path and is returned to local and remote owners alike."
        ),
    )
    path: Optional[str] = Field(
        default=None,
        description=(
            "Absolute folder path. Present only for a local, LAN or Tailscale "
            "caller; omitted for any other owner session so a remote client "
            "learns no host filesystem layout."
        ),
    )


class LibraryListResponse(BaseModel):
    """Body of ``GET /libraries``."""

    model_config = ConfigDict(extra="allow")

    libraries: list[LibraryResponse]
    can_manage: bool = Field(
        description=(
            "Whether this caller may switch library. False for a remote session "
            "without allow_remote_host_ops, which is why the tab disables its "
            "controls rather than letting the call fail. ALL bearer tokens may "
            "still be denied earlier by require_local_for_write."
        )
    )
    in_docker: bool = Field(
        description="Whether the server runs in a container, so paths are container paths."
    )
    cli_hint: Optional[str] = Field(
        default=None,
        description=(
            "The exact command that runs the library CLI on this deployment. "
            "Present only for a local, LAN or Tailscale caller: it embeds an "
            "install path or a container name."
        ),
    )


class SwitchLibraryRequest(BaseModel):
    """Body of ``POST /libraries/active``."""

    model_config = ConfigDict(extra="forbid")

    uuid: str = Field(description="The uuid of the library to make active.")


class SwitchLibraryResponse(BaseModel):
    """Body of ``POST /libraries/active``."""

    model_config = ConfigDict(extra="allow")

    status: str
    library: LibraryResponse
    active_share_links: int = Field(
        default=0,
        description=(
            "How many resource-scoped share links pointed at the library that "
            "was active before this call. They stop working until it is active "
            "again, and the owner is the only person who can see that happen."
        ),
    )


def create_router(server) -> APIRouter:
    router = APIRouter()

    def _caller_is_local(request: Request) -> bool:
        """Whether this caller may see host paths and switch library.

        Uses the same predicate the authz gate applies to ``LOCAL_OWNER_ONLY``,
        so what the tab is told it can do and what the gate will actually allow
        cannot drift apart.
        """
        client_ip = server.auth.real_client_ip(request)
        if is_local_or_tailscale_ip(client_ip):
            return True
        return bool(server.auth.allow_remote_host_ops)

    def _count_share_links(library_uuid: str) -> int:
        """Unexpired resource-scoped tokens pointing at *library_uuid*."""
        row = server.hub.fetchone(
            "SELECT COUNT(*) FROM usertoken WHERE library_uuid = ? "
            "AND resource_type IS NOT NULL "
            "AND (expires_at IS NULL OR expires_at > ?)",
            (library_uuid, datetime.utcnow()),
        )
        return int(row[0]) if row else 0

    def _to_response(library, include_path: bool) -> LibraryResponse:
        return LibraryResponse(
            uuid=library.uuid,
            name=library.name,
            is_active=library.is_active,
            is_reachable=library.is_reachable,
            active_share_links=_count_share_links(library.uuid),
            path=library.path if include_path else None,
        )

    @router.get(
        "/libraries",
        summary="List registered libraries",
        description=(
            "Returns every attached library and which one is active. Folder "
            "paths and the CLI hint are included only for a local, LAN or "
            "Tailscale cookie owner, or when allow_remote_host_ops is enabled."
        ),
        tags=["libraries"],
        response_model=LibraryListResponse,
    )
    def list_libraries(request: Request):
        server.auth.ensure_secure_when_required(request)
        local = _caller_is_local(request)
        libraries = server.library_registry.list_libraries()
        return LibraryListResponse(
            libraries=[_to_response(library, local) for library in libraries],
            can_manage=local,
            in_docker=running_in_docker(),
            cli_hint=cli_hint() if local else None,
        )

    @router.post(
        "/libraries/active",
        summary="Switch the active library",
        description=(
            "Closes the current library and opens the named one. Every "
            "connected client is told to reload, because picture ids do not "
            "carry across libraries. If the target cannot be opened the session "
            "stays on the library it was already using."
        ),
        tags=["libraries"],
        response_model=SwitchLibraryResponse,
    )
    def switch_library(request: Request, payload: SwitchLibraryRequest):
        server.auth.ensure_secure_when_required(request)

        previous = server.library_registry.active_library()
        share_links = _count_share_links(previous.uuid) if previous else 0

        try:
            library = server.library_switch.switch_to(payload.uuid)
        except LibraryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except LibrarySwitchError as exc:
            # 409: the request was well-formed and the caller was allowed; the
            # library itself could not be opened. The session is unchanged.
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except LibraryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if previous and previous.uuid != library.uuid:
            # Every connected client is now looking at a library the server no
            # longer has open. Their picture ids mean something else here, so a
            # reload is the only honest instruction.
            server.handle_vault_event(
                EventType.LIBRARY_SWITCHED,
                {"uuid": library.uuid, "name": library.name},
            )
            if share_links:
                logger.info(
                    "Switched away from %s, which has %d active share link(s); "
                    "they stop working until it is active again",
                    previous.name,
                    share_links,
                )

        return SwitchLibraryResponse(
            status="ok",
            library=_to_response(library, _caller_is_local(request)),
            active_share_links=share_links,
        )

    return router
