"""The library lifecycle over HTTP: list, inspect, add, rename, detach, switch.

Routes over :class:`~pixlstash.hub.registry.LibraryRegistry`, which already
implements every one of these verbs and raises a typed, user-facing error for
each refusal. Nothing here re-derives a rule; it surfaces the registry's.

**The routes sit at two access tiers on purpose** (multi-library plan §11 q3/q4).
Listing is ``OWNER_ONLY``, so the Settings tab always renders for an owner.
Everything else is ``LOCAL_OWNER_ONLY``: ``inspect`` and ``POST /libraries``
because they take a caller-supplied host path and read or write the host
filesystem (the §16.3 class), ``DELETE`` and ``POST /libraries/active`` because
they reset every connected client's session or take a library's share links
offline - authority over other principals' state rather than only the caller's.
``PATCH`` writes one hub column and touches no filesystem, but it is a
management verb on the same surface and the Settings pane gates the whole ``⋯``
menu on ``can_manage``; putting it on a looser tier would buy nothing and give
the pane two rules to explain.

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
does not include Tailscale CGNAT. ``GET /libraries`` and ``POST /libraries/active`` are
library-independent: they return no library content and keep answering while a
switch is in flight. The lifecycle routes added later are hub-only but stay
pinned, because none of them has to answer mid-swap.
"""

import os
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from pixlstash.auth import is_local_or_tailscale_ip
from pixlstash.event_types import EventType
from pixlstash.hub.cli_hint import cli_hint, running_in_docker
from pixlstash.hub.registry import (
    ActiveLibraryError,
    LibraryError,
    LibraryExistsError,
    LibraryNotFoundError,
    NotAVaultError,
    resolve_path,
    validate_vault_folder,
)
from pixlstash.pixl_logging import get_logger
from pixlstash.services.library_switch_service import LibrarySwitchError
from pixlstash.utils.media_files import count_media_files
from pixlstash.utils.reference_folder_validator import validate_reference_folder_path

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


LibraryVerdict = Literal["attached", "overlaps", "vault", "pictures", "empty"]


class LibraryInspection(BaseModel):
    """Body of ``GET /libraries/inspect``: what a folder turns out to be.

    One picker, five answers, and no mode for the owner to choose first. Three
    verdicts are the same ``POST /libraries`` with a different consequence
    (``vault`` attaches, ``pictures`` and ``empty`` create); the other two are
    the registry refusing for a reason it can already name.
    """

    model_config = ConfigDict(extra="allow")

    verdict: LibraryVerdict = Field(
        description=(
            "attached: this exact folder is already registered. overlaps: a "
            "registered library contains it, or it contains one. vault: it "
            "holds a vault nothing is using - attach it. pictures: a folder of "
            "pictures with no vault. empty: no pictures and no vault."
        )
    )
    path: str = Field(description="The folder as the server resolved it.")
    can_add: bool = Field(
        description="Whether POST /libraries would accept this path right now."
    )
    headline: str = Field(description="One line naming what the folder is.")
    detail: str = Field(description="The consequence, or the reason for a refusal.")
    suggested_name: str = Field(
        description="What the library would be called if no name is given."
    )
    picture_count: int = Field(
        default=0, description="Indexable files found under the folder."
    )
    picture_count_capped: bool = Field(
        default=False,
        description=(
            "True when the count stopped at its entry cap, so picture_count is "
            "a floor rather than a total. A folder picker has to answer while "
            "somebody is looking at it."
        ),
    )
    library: Optional[LibraryResponse] = Field(
        default=None,
        description=(
            "The library this verdict is about, for `attached` and `overlaps`. "
            "Its path obeys the same locality rule as the listing's."
        ),
    )


class CreateLibraryRequest(BaseModel):
    """Body of ``POST /libraries``."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="Absolute folder path on the server's machine.")
    name: Optional[str] = Field(
        default=None,
        description="Label for the library. Defaults to the folder's own name.",
    )


class RenameLibraryRequest(BaseModel):
    """Body of ``PATCH /libraries/{library_uuid}``."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="The library's new label.")


class DetachLibraryResponse(BaseModel):
    """Body of ``DELETE /libraries/{library_uuid}``."""

    model_config = ConfigDict(extra="allow")

    status: str
    library: LibraryResponse
    inert_share_links: int = Field(
        default=0,
        description=(
            "How many of the library's share links stop working. They are kept, "
            "not revoked: attaching the same folder again revives the row and "
            "they work once more."
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
            # The hub path only ever reaches a local caller, along with the
            # library paths beside it: it is host layout, same as those.
            cli_hint=cli_hint(hub_path=server.hub.path) if local else None,
        )

    def _safe_folder(path: str) -> str:
        """Resolve a caller-supplied folder, or refuse it the way the rest do.

        **Resolved first, then validated.** ``validate_reference_folder_path``
        compares against a literal blocklist, so checking the string the caller
        sent would let ``/home/me/link-to-etc`` through and hand ``/etc`` to a
        route that chmods the folder 0700 and writes a database into it. The
        sibling that gets this right is
        ``validate_reference_folder_accessible``, which realpaths before it
        checks; this follows it, not ``filesystem/browse``'s ordering.

        Resolution is the registry's own ``resolve_path`` - the same symlink
        resolution registrations are stored under - because the value is about
        to be compared against registry rows, and normalising it any other way
        would make "already attached" miss.

        ``filesystem_roots`` is honoured for the same reason
        ``POST /filesystem/folders`` honours it: an operator who confined the
        picker to a set of roots did not mean "except for the route that can
        write a vault anywhere".
        """
        candidate = path.strip() if isinstance(path, str) else ""
        if not candidate:
            raise HTTPException(status_code=400, detail="A folder path is required.")

        # Refused here rather than left to the blocklist check below, which now
        # runs on the resolved path and so can never see a relative one:
        # `resolve_path` calls `abspath`, which would quietly resolve "Pictures"
        # against the *server's* working directory - never what the caller meant.
        # `~` is expanded first, since it is absolute once expanded.
        expanded = os.path.expanduser(candidate)
        if not os.path.isabs(expanded):
            raise HTTPException(status_code=400, detail="Path must be absolute.")
        resolved = resolve_path(expanded)

        error = validate_reference_folder_path(resolved)
        if error:
            raise HTTPException(status_code=400, detail=error)

        roots = [
            os.path.realpath(root)
            for root in (server._server_config.get("filesystem_roots") or [])
            if isinstance(root, str) and root
        ]
        if roots and not any(
            resolved == root or resolved.startswith(root + os.sep) for root in roots
        ):
            raise HTTPException(
                status_code=403,
                detail="Path is not within any configured filesystem root.",
            )
        return resolved

    def _count(folder: str, wanted: bool) -> tuple[int, bool]:
        """The folder's picture total, or ``(0, False)`` when nobody asked.

        The empty branch is decided by ``found == 0``, so a skipped count always
        lands on ``empty``. That is safe only because the caller that skips it -
        ``POST /libraries`` re-checking the rule - treats ``empty``, ``pictures``
        and ``vault`` identically: all three are ``can_add``, and only the two
        refusals decided above the count change what it does.
        """
        return count_media_files(folder) if wanted else (0, False)

    def _holds_vault(folder: str) -> bool:
        """Whether *folder* holds a vault this build can attach."""
        try:
            validate_vault_folder(folder)
        except NotAVaultError:
            return False
        return True

    def _inspect(
        folder: str, include_path: bool, *, count: bool = True
    ) -> LibraryInspection:
        """Decide which of the five things *folder* is.

        Order is not arbitrary. The two refusals come first, because a folder
        that is already covered is covered whatever else it also happens to be:
        a vault nested inside an attached library must not offer an Add button
        that would leave two libraries indexing one set of pictures, each moving
        them by its own layout.

        Args:
            folder: An already-resolved, already-validated path.
            include_path: Whether the named library's path may be returned.
            count: Walk the folder for a picture total. ``POST /libraries``
                re-inspects to re-check the rule and needs only the verdict, and
                walking a 28k library twice per add is the whole cost of the
                call.
        """
        suggested = os.path.basename(folder) or folder
        registry = server.library_registry

        for library in registry.list_libraries():
            if library.path == folder:
                # Counted here too: the desktop's first-run setup makes a
                # vault in whatever folder was chosen, so an attached library
                # can sit on top of pictures nothing has indexed yet. The
                # count is what lets the empty library offer to bring them in.
                found, capped = _count(folder, count)
                return LibraryInspection(
                    verdict="attached",
                    path=folder,
                    can_add=False,
                    headline="Already on the list",
                    detail=f'This folder is the library "{library.name}".',
                    suggested_name=suggested,
                    picture_count=found,
                    picture_count_capped=capped,
                    library=_to_response(library, include_path),
                )

        overlaps = registry.overlapping(folder)
        if overlaps:
            # A library that CONTAINS this folder is named first when there is
            # one. `overlapping` returns registry order (active first, then
            # name), which says nothing about the direction, so with a parent
            # and a child both overlapping the message could otherwise name the
            # child while the copy reads "covers this folder".
            containing = [
                library
                for library in overlaps
                if folder.startswith(library.path + os.sep)
            ]
            other = containing[0] if containing else overlaps[0]
            covers = bool(containing)
            detail = (
                f'"{other.name}" covers this folder.'
                if covers
                else f'This folder contains the library "{other.name}".'
            )
            return LibraryInspection(
                verdict="overlaps",
                path=folder,
                can_add=False,
                headline="Inside a library you already have",
                detail=(
                    f"{detail} Two libraries indexing the same pictures would "
                    "each move them by their own layout, and neither would be "
                    "wrong."
                ),
                suggested_name=suggested,
                library=_to_response(other, include_path),
            )

        if _holds_vault(folder):
            found, capped = _count(folder, count)
            return LibraryInspection(
                verdict="vault",
                path=folder,
                can_add=True,
                headline="A library you already made",
                detail=(
                    f"{'At least ' if capped else ''}{found:,} "
                    f"{'picture' if found == 1 else 'pictures'}. Added as it is, "
                    "with its tags, scores and people."
                ),
                suggested_name=suggested,
                picture_count=found,
                picture_count_capped=capped,
            )

        found, capped = _count(folder, count)
        if found:
            return LibraryInspection(
                verdict="pictures",
                path=folder,
                can_add=True,
                headline=(
                    f"{'At least ' if capped else ''}{found:,} "
                    f"{'picture' if found == 1 else 'pictures'}, no library here yet"
                ),
                detail=(
                    "Bring them in and name what your folders mean. Nothing is moved."
                ),
                suggested_name=suggested,
                picture_count=found,
                picture_count_capped=capped,
            )

        return LibraryInspection(
            verdict="empty",
            path=folder,
            can_add=True,
            headline="Empty",
            detail="A fresh library. Nothing is here yet.",
            suggested_name=suggested,
        )

    def _by_uuid_or_404(library_uuid: str):
        """Resolve a uuid, refusing the registry's name and row-id fallbacks.

        ``LibraryRegistry.get`` also accepts a row id and a name, which is right
        for a CLI a person types at. Over HTTP it is not: a client left open
        across a detach and attach would name a different library by row id, and
        renaming one library to another's old name would silently retarget a
        request. The uuid is the only identifier that means one library for the
        life of the installation, so it is the only one accepted here.
        """
        # "uuid", not "id": these routes accept nothing else, so a caller who
        # sends a row id or a name gets a 404 whose wording should not suggest
        # the value was looked up and missing (#1096 review). The identical
        # message in library_switch_service.py says the same for the same
        # reason and is corrected with it.
        library = server.library_registry.by_uuid(library_uuid)
        # `by_uuid` deliberately returns detached rows - that is how a uuid stays
        # meaningful across a detach for the tokens stamped with it. These routes
        # want the attached set, the one `GET /libraries` shows: without this,
        # DELETE on an already-detached library answers 200 "ok" for a no-op, and
        # PATCH renames a row nobody can see onto the name of one they can (the
        # duplicate check only inspects attached rows).
        if library is None or not library.attached:
            raise HTTPException(
                status_code=404, detail=f"No attached library with uuid {library_uuid}."
            )
        return library

    @router.get(
        "/libraries/inspect",
        summary="Ask what a folder is",
        description=(
            "Answers which of five things a folder is - already attached, "
            "overlapping an attached library, an unregistered vault, a folder "
            "of pictures, or empty - so one picker can offer the right action "
            "instead of asking the owner to choose a mode first. Reads only."
        ),
        tags=["libraries"],
        response_model=LibraryInspection,
    )
    def inspect_folder(
        request: Request,
        path: str = Query(description="Absolute folder path to inspect."),
    ):
        server.auth.ensure_secure_when_required(request)
        folder = _safe_folder(path)
        if not os.path.isdir(folder):
            raise HTTPException(status_code=404, detail=f"{folder} is not a folder.")
        return _inspect(folder, _caller_is_local(request))

    @router.post(
        "/libraries",
        summary="Add a library",
        description=(
            "Attaches the folder when it already holds a vault, and starts a "
            "fresh library in it when it does not. No file is moved, renamed or "
            "copied either way. The folder must already exist - the picker's "
            "`New folder` button makes one, so this route never creates a "
            "directory the owner did not point at - and starting a fresh "
            "library there restricts it to the owner (0700), because it is "
            "about to hold the vault database. The folder is re-inspected here "
            "rather than trusted from the picker's answer, so one that became "
            "covered in between is still refused."
        ),
        tags=["libraries"],
        response_model=LibraryResponse,
        status_code=201,
    )
    def add_library(request: Request, payload: CreateLibraryRequest):
        server.auth.ensure_secure_when_required(request)
        folder = _safe_folder(payload.path)
        name = (payload.name or "").strip() or None

        if not os.path.isdir(folder):
            raise HTTPException(status_code=404, detail=f"{folder} is not a folder.")

        verdict = _inspect(folder, include_path=False, count=False)
        if not verdict.can_add:
            # 409: the request was well-formed and the caller was allowed; the
            # registry's own rule refuses it, in the registry's own words.
            raise HTTPException(status_code=409, detail=verdict.detail)

        registry = server.library_registry
        try:
            if verdict.verdict == "vault":
                library = registry.attach(folder, name)
            else:
                # `create`, not `register_pending`. The plan's route table named
                # the latter, but it registers a row whose vault does not exist:
                # the library the owner just added would render as "Not found"
                # and refuse to be switched to, because the switch revalidates
                # the folder and insists on a real vault. `create` builds the
                # vault with the same code the server runs at startup, so the
                # row is usable the moment it appears.
                library = registry.create(folder, name)
        except LibraryExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except NotAVaultError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except PermissionError as exc:
            logger.error("Could not add a library at %s: %s", folder, exc)
            raise HTTPException(
                status_code=403,
                detail=f"PixlStash is not allowed to write to {folder}.",
            ) from exc
        except OSError as exc:
            logger.error("Could not add a library at %s: %s", folder, exc)
            raise HTTPException(
                status_code=500, detail=f"Could not add a library at {folder}: {exc}"
            ) from exc
        except LibraryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        logger.info(
            "Added library %s (uuid=%s) at %s by %s",
            library.name,
            library.uuid,
            library.path,
            "attach" if verdict.verdict == "vault" else "create",
        )
        return _to_response(library, _caller_is_local(request))

    @router.patch(
        "/libraries/{library_uuid}",
        summary="Rename a library",
        description="Changes the label only. Nothing on disk is renamed.",
        tags=["libraries"],
        response_model=LibraryResponse,
    )
    def rename_library(
        request: Request, library_uuid: str, payload: RenameLibraryRequest
    ):
        server.auth.ensure_secure_when_required(request)
        target = _by_uuid_or_404(library_uuid)
        try:
            library = server.library_registry.rename(target.uuid, payload.name)
        except LibraryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except LibraryExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except LibraryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _to_response(library, _caller_is_local(request))

    @router.delete(
        "/libraries/{library_uuid}",
        summary="Stop using a library",
        description=(
            "Deregisters the library. Every picture and folder inside it stays "
            "exactly where it is, and its row is kept rather than deleted so "
            "the share links pointing at it survive, inert, until the same "
            "folder is added again. The active library is refused; switch away "
            "first."
        ),
        tags=["libraries"],
        response_model=DetachLibraryResponse,
    )
    def detach_library(request: Request, library_uuid: str):
        server.auth.ensure_secure_when_required(request)
        # The gate does not do this for us: these routes are HUB_ONLY, which is
        # what exempts them from the switch's 503 - deliberately, so the registry
        # stays answerable when there is no open vault to recover from. Detach is
        # the one that cannot take the exemption. It reads `is_active` to refuse
        # the active library, and mid-swap that flag is being moved: a detach
        # landing in the window could forget the library the switch is about to
        # publish as active, and the switch would then open a library the
        # registry says nobody has.
        if server.library_switch.is_switching:
            raise HTTPException(
                status_code=503,
                detail="PixlStash is switching library. Try again in a moment.",
                headers={"Retry-After": "2"},
            )
        target = _by_uuid_or_404(library_uuid)
        share_links = _count_share_links(target.uuid)
        try:
            library = server.library_registry.detach(target.uuid)
        except LibraryNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ActiveLibraryError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except LibraryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return DetachLibraryResponse(
            status="ok",
            library=_to_response(library, _caller_is_local(request)),
            inert_share_links=share_links,
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
