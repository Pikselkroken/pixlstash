#!/usr/bin/env python3
"""split_library_by_project.py - Split a PixlStash library in two along a project boundary.

Part of a database split: one library becomes "the project" and the other becomes
"everything else".  Run the script twice, once against each half, with opposite
``--mode`` values:

  1. Copy the whole image root (it contains ``vault.db``) to a second location.
  2. Against the ORIGINAL library, run ``--mode remove-in-project`` - it strips
     out everything that belongs to the project, leaving the "everything else"
     half.
  3. Point the app at the COPY, restart it, and run ``--mode remove-out-of-project``
     - it strips out everything that does not belong to the project, leaving the
     project-only half.

Both halves must have their own copy of the image root before either run: the
purge removes picture rows **and unlinks the files from disk**.  Running this
against two databases that share one image root will destroy the other half's
images.

Membership rules
----------------
Project membership is many-to-many for pictures, picture sets and characters, so
an entity can sit on both sides of the split.  The rules are:

* A picture is "in the project" when it has a direct ``PictureProjectMember`` row
  for it, OR it is a member of a picture set in the project, OR it has a face
  assigned to a character in the project.
* Overlapping entities (in the project *and* in another project) are KEPT BY BOTH
  halves - neither run deletes them.
* Entities in no project at all belong to the "everything else" half, so
  ``--mode remove-out-of-project`` deletes them and ``--mode remove-in-project``
  keeps them.

Locked picture sets
-------------------
A locked set refuses deletion with a 423 and freezes its member pictures against
both the soft delete and the purge, so anything locked would silently survive the
split.  The script therefore unlocks every locked set that blocks the plan before
it deletes anything.  ``--no-unlock`` turns that off, in which case the run
refuses to start rather than leave rows behind.

Nothing is deleted without ``--apply``; the default is a dry run that prints the
full plan.  ``--report`` writes the plan (including every id) to a JSON file so
the run is auditable afterwards.

Usage:
    # Dry run - prints what would go, touches nothing.
    python scripts/split_library_by_project.py \\
        --url https://localhost:9537 --token API_TOKEN \\
        --project PixlTagger --mode remove-in-project --insecure

    # For real.
    python scripts/split_library_by_project.py \\
        --url https://localhost:9537 --token API_TOKEN \\
        --project PixlTagger --mode remove-in-project --insecure \\
        --report split-report.json --apply

The token must be a full-scope (unscoped owner) token: a resource-scoped token
sees only part of the library and would produce a silently incomplete split.
"""

import argparse
import http.cookiejar
import json
import logging
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

logger = logging.getLogger("split_library_by_project")

API = "/api/v1"

# Server-side cap on ids per bulk soft-delete request
# (BULK_DELETE_MAX_IDS in pixlstash/routes/pictures/_crud.py).
BULK_DELETE_MAX_IDS = 1000
# Ids per membership lookup. No server-side cap, but keep the request body sane.
MEMBERSHIP_BATCH = 2000
# Rows per /pictures/stream page (the server allows up to 5000).
STREAM_BATCH = 1000
# Ids per preview -> purge round trip. The confirm_token the preview mints lives
# for CONFIRM_TOKEN_TTL_SECONDS (300 s), so a chunk has to be small enough that
# its purge lands well inside that window.
PURGE_CHUNK = 500

MODE_REMOVE_IN = "remove-in-project"
MODE_REMOVE_OUT = "remove-out-of-project"

# Field projection for the picture streams. Deliberately NOT "grid": fields=grid
# implies stack_leaders_only on the server, which would hide every non-leader
# member of a stack and leave those rows behind in the split.
PICTURE_STREAM_FIELDS = "id"


class SplitError(RuntimeError):
    """A condition that must stop the run before anything is deleted."""


class PixlStashClient:
    """Minimal authenticated JSON client for the PixlStash REST API.

    Authentication mirrors scripts/fetch_pixlstash.py: POST the API token to
    /api/v1/login and keep the session cookie for every subsequent request.

    Attributes:
        base_url: Server root, e.g. ``https://localhost:9537``.
        timeout: Default per-request timeout in seconds.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        verify_ssl: bool = True,
        timeout: int = 120,
    ) -> None:
        """Build an opener with a cookie jar and (optionally) TLS verification off.

        Args:
            base_url: Server root URL.
            token: Full-scope API token.
            verify_ssl: False to accept the self-signed certificate a local
                PixlStash serves on https.
            timeout: Default per-request timeout in seconds.
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._token = token
        self._ssl_ctx = None if verify_ssl else ssl._create_unverified_context()
        cookie_jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self._ssl_ctx),
            urllib.request.HTTPCookieProcessor(cookie_jar),
        )

    def login(self) -> None:
        """Authenticate and store the session cookie.

        Raises:
            SplitError: The server rejected the token.
        """
        try:
            self.request("POST", f"{API}/login", payload={"token": self._token})
        except SplitError as exc:
            raise SplitError(
                f"Login failed against {self.base_url}. Check the token and that "
                f"the server is running. Underlying error: {exc}"
            ) from exc
        logger.info("Authenticated with %s", self.base_url)

    def request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        payload: Optional[dict] = None,
        timeout: Optional[int] = None,
    ) -> Any:
        """Issue one JSON request and decode the response.

        Args:
            method: HTTP verb.
            path: Path below the server root, including the API prefix.
            params: Query parameters.
            payload: JSON request body.
            timeout: Override for this request's timeout in seconds.

        Returns:
            The decoded JSON body, or None when the response body is empty.

        Raises:
            SplitError: The request failed, or the response was not JSON.
        """
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self._opener.open(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read().decode(errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            raise SplitError(
                f"HTTP {exc.code} {exc.reason} for {method} {url}: {body}"
            ) from exc
        except urllib.error.URLError as exc:
            raise SplitError(f"Could not reach {method} {url}: {exc.reason}") from exc
        if not raw.strip():
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SplitError(
                f"Non-JSON response from {method} {url}: {raw[:500]!r}"
            ) from exc

    def stream_picture_ids(self, only_deleted: bool) -> Set[int]:
        """Page through /pictures/stream and collect every picture id.

        Args:
            only_deleted: True to enumerate the scrapheap instead of the live
                library.

        Returns:
            The set of picture ids.

        Raises:
            SplitError: Pagination failed to advance (would loop forever).
        """
        ids: Set[int] = set()
        offset = 0
        while True:
            params: Dict[str, Any] = {
                "fields": PICTURE_STREAM_FIELDS,
                "batch_limit": STREAM_BATCH,
                "offset": offset,
            }
            if only_deleted:
                params["only_deleted"] = "true"
            resp = self.request("GET", f"{API}/pictures/stream", params=params) or {}
            for pic in resp.get("pictures") or []:
                pid = pic.get("id")
                if pid is not None:
                    ids.add(int(pid))
            if resp.get("done"):
                break
            next_offset = resp.get("next_offset")
            if next_offset is None or int(next_offset) <= offset:
                raise SplitError(
                    "/pictures/stream did not advance "
                    f"(offset={offset}, next_offset={next_offset}); refusing to "
                    "continue with a possibly incomplete picture list."
                )
            offset = int(next_offset)
        return ids

    def picture_project_membership(
        self, picture_ids: Sequence[int]
    ) -> Dict[int, Set[int]]:
        """Map project id -> picture ids, for direct project membership only.

        Args:
            picture_ids: Pictures to look up.

        Returns:
            ``{project_id: {picture_id, ...}}``.
        """
        result: Dict[int, Set[int]] = {}
        for batch in _chunks(sorted(picture_ids), MEMBERSHIP_BATCH):
            resp = (
                self.request(
                    "POST",
                    f"{API}/projects/membership",
                    payload={"picture_ids": batch},
                )
                or {}
            )
            for project_id, pids in (resp.get("project_assignments") or {}).items():
                result.setdefault(int(project_id), set()).update(int(p) for p in pids)
        return result

    def picture_character_membership(
        self, picture_ids: Sequence[int]
    ) -> Dict[int, Set[int]]:
        """Map character id -> picture ids that carry a face assigned to it.

        Args:
            picture_ids: Pictures to look up.

        Returns:
            ``{character_id: {picture_id, ...}}``.
        """
        result: Dict[int, Set[int]] = {}
        for batch in _chunks(sorted(picture_ids), MEMBERSHIP_BATCH):
            resp = (
                self.request(
                    "POST",
                    f"{API}/characters/membership",
                    payload={"picture_ids": batch},
                )
                or {}
            )
            for character_id, pids in (resp.get("character_assignments") or {}).items():
                result.setdefault(int(character_id), set()).update(int(p) for p in pids)
        return result

    def set_members(self, set_id: int) -> Set[int]:
        """Return every picture id in a set, scrapheaped members included.

        Args:
            set_id: The picture set to read.

        Returns:
            The set of member picture ids.
        """
        resp = (
            self.request(
                "GET",
                f"{API}/picture_sets/{set_id}/members",
                params={"include_deleted": "true"},
            )
            or {}
        )
        return {int(p) for p in (resp.get("picture_ids") or [])}


def _chunks(items: Sequence[Any], size: int) -> Iterable[List[Any]]:
    """Yield ``items`` in lists of at most ``size`` elements.

    Args:
        items: The sequence to slice.
        size: Maximum chunk length.

    Yields:
        Successive chunks.
    """
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


def _project_ids_of(entity: dict) -> Set[int]:
    """Read an entity's full project membership from a list-endpoint payload.

    ``project_ids`` carries every project since issue #125; ``project_id`` is the
    legacy scalar naming only the primary project and is used purely as a
    fallback for a server old enough not to send the list.

    Args:
        entity: A character or picture set dict from a list endpoint.

    Returns:
        The set of project ids the entity belongs to.
    """
    raw = entity.get("project_ids")
    if isinstance(raw, list):
        return {int(p) for p in raw if p is not None}
    scalar = entity.get("project_id")
    return {int(scalar)} if scalar is not None else set()


def build_plan(client: PixlStashClient, project_name: str, mode: str) -> dict:
    """Work out exactly what the run will delete, without deleting anything.

    Args:
        client: An authenticated API client.
        project_name: The project that defines the split boundary.
        mode: MODE_REMOVE_IN or MODE_REMOVE_OUT.

    Returns:
        The plan: resolved project, per-entity id lists to delete, and the
        counts the report prints.

    Raises:
        SplitError: The project does not exist, or the token is scoped.
    """
    projects = client.request("GET", f"{API}/projects") or []
    if not isinstance(projects, list):
        raise SplitError(f"Unexpected /projects response: {projects!r}")
    matches = [
        p for p in projects if str(p.get("name", "")).lower() == project_name.lower()
    ]
    if not matches:
        known = ", ".join(sorted(str(p.get("name")) for p in projects)) or "(none)"
        raise SplitError(f"No project named {project_name!r}. Known projects: {known}")
    project = matches[0]
    project_id = int(project["id"])
    other_project_ids = {int(p["id"]) for p in projects if int(p["id"]) != project_id}

    if mode == MODE_REMOVE_OUT and len(projects) == 1:
        logger.info(
            "%r is the only project on this server; the run will delete every "
            "picture, set and character that has no project.",
            project_name,
        )

    logger.info("Enumerating pictures (live and scrapheap)...")
    live_pids = client.stream_picture_ids(only_deleted=False)
    deleted_pids = client.stream_picture_ids(only_deleted=True)
    all_pids = live_pids | deleted_pids
    logger.info(
        "Found %d pictures (%d live, %d already in the scrapheap).",
        len(all_pids),
        len(live_pids),
        len(deleted_pids),
    )

    logger.info("Reading picture sets...")
    picture_sets = client.request("GET", f"{API}/picture_sets") or []
    set_members: Dict[int, Set[int]] = {}
    for pset in picture_sets:
        set_members[int(pset["id"])] = client.set_members(int(pset["id"]))

    logger.info("Reading characters...")
    characters = client.request("GET", f"{API}/characters") or []

    logger.info("Resolving project membership...")
    direct_by_project = client.picture_project_membership(sorted(all_pids))
    pics_by_character = client.picture_character_membership(sorted(all_pids))

    def pictures_in(pid_set: Set[int]) -> Set[int]:
        """Pictures reachable from any project in ``pid_set``."""
        reached: Set[int] = set()
        for project_key in pid_set:
            reached |= direct_by_project.get(project_key, set())
        for pset in picture_sets:
            if _project_ids_of(pset) & pid_set:
                reached |= set_members.get(int(pset["id"]), set())
        for character in characters:
            if _project_ids_of(character) & pid_set:
                reached |= pics_by_character.get(int(character["id"]), set())
        return reached & all_pids

    in_project = pictures_in({project_id})
    in_other = pictures_in(other_project_ids)
    overlap_pics = in_project & in_other
    unassigned_pics = all_pids - in_project - in_other

    # Reachability breakdown, for the report only.
    direct_hits = direct_by_project.get(project_id, set()) & all_pids
    set_hits: Set[int] = set()
    for pset in picture_sets:
        if project_id in _project_ids_of(pset):
            set_hits |= set_members.get(int(pset["id"]), set())
    character_hits: Set[int] = set()
    for character in characters:
        if project_id in _project_ids_of(character):
            character_hits |= pics_by_character.get(int(character["id"]), set())

    def classify(entities: List[dict]) -> Dict[str, List[dict]]:
        """Split sets/characters into exclusive, overlapping and out-of-project."""
        exclusive, overlapping, outside = [], [], []
        for entity in entities:
            memberships = _project_ids_of(entity)
            if project_id in memberships:
                (overlapping if memberships - {project_id} else exclusive).append(
                    entity
                )
            else:
                outside.append(entity)
        return {"exclusive": exclusive, "overlapping": overlapping, "outside": outside}

    set_groups = classify(picture_sets)
    character_groups = classify(characters)

    if mode == MODE_REMOVE_IN:
        # Delete what is exclusively the project's. Overlap stays on both sides,
        # and anything unassigned belongs to this half.
        pictures_to_delete = in_project - in_other
        sets_to_delete = set_groups["exclusive"]
        characters_to_delete = character_groups["exclusive"]
        projects_to_delete = [project]
    else:
        # Delete everything the project cannot reach. Unassigned entities belong
        # to the other half, so they go too.
        pictures_to_delete = all_pids - in_project
        sets_to_delete = set_groups["outside"]
        characters_to_delete = character_groups["outside"]
        projects_to_delete = [p for p in projects if int(p["id"]) != project_id]

    # Deleting a character also deletes its reference picture set (see
    # DELETE /characters/{id}). Flag any reference set that is not itself
    # scheduled for deletion, so the collateral is visible before it happens.
    doomed_set_ids = {int(s["id"]) for s in sets_to_delete}
    collateral_reference_sets = []
    for character in characters_to_delete:
        ref_set_id = character.get("reference_picture_set_id")
        if ref_set_id is not None and int(ref_set_id) not in doomed_set_ids:
            collateral_reference_sets.append(
                {"character_id": int(character["id"]), "set_id": int(ref_set_id)}
            )

    return {
        "mode": mode,
        "project": {"id": project_id, "name": project.get("name")},
        "counts": {
            "pictures_total": len(all_pids),
            "pictures_live": len(live_pids),
            "pictures_scrapheaped": len(deleted_pids),
            "pictures_in_project": len(in_project),
            "pictures_in_project_direct": len(direct_hits),
            "pictures_in_project_via_sets": len(set_hits & all_pids),
            "pictures_in_project_via_characters": len(character_hits & all_pids),
            "pictures_in_other_projects": len(in_other),
            "pictures_overlapping": len(overlap_pics),
            "pictures_unassigned": len(unassigned_pics),
            "sets_total": len(picture_sets),
            "sets_exclusive_to_project": len(set_groups["exclusive"]),
            "sets_overlapping": len(set_groups["overlapping"]),
            "sets_outside_project": len(set_groups["outside"]),
            "characters_total": len(characters),
            "characters_exclusive_to_project": len(character_groups["exclusive"]),
            "characters_overlapping": len(character_groups["overlapping"]),
            "characters_outside_project": len(character_groups["outside"]),
        },
        "delete": {
            "picture_ids": sorted(pictures_to_delete),
            "already_scrapheaped_ids": sorted(pictures_to_delete & deleted_pids),
            "sets": [
                {"id": int(s["id"]), "name": s.get("name")} for s in sets_to_delete
            ],
            "characters": [
                {"id": int(c["id"]), "name": c.get("name")}
                for c in characters_to_delete
            ],
            "projects": [
                {"id": int(p["id"]), "name": p.get("name")} for p in projects_to_delete
            ],
        },
        "keep": {
            "overlapping_picture_ids": sorted(overlap_pics),
            "overlapping_sets": [
                {"id": int(s["id"]), "name": s.get("name")}
                for s in set_groups["overlapping"]
            ],
            "overlapping_characters": [
                {"id": int(c["id"]), "name": c.get("name")}
                for c in character_groups["overlapping"]
            ],
        },
        "warnings": {"collateral_reference_sets": collateral_reference_sets},
    }


def check_locked(client: PixlStashClient, plan: dict) -> dict:
    """Find locked sets that would block the plan.

    A locked picture set refuses deletion (423) and freezes its member pictures
    against both the soft delete and the purge, so anything locked has to be
    unlocked first or it silently survives the split.

    Args:
        client: An authenticated API client.
        plan: The plan from build_plan.

    Returns:
        ``{"sets_to_unlock": [...], "frozen_picture_ids": [...]}``.
    """
    resp = client.request("GET", f"{API}/picture_sets/locked-members") or {}
    locked_sets = resp.get("sets") or []
    doomed_set_ids = {s["id"] for s in plan["delete"]["sets"]}
    doomed_pids = set(plan["delete"]["picture_ids"])

    sets_to_unlock = []
    frozen: Set[int] = set()
    for locked in locked_sets:
        set_id = int(locked["id"])
        members = {int(p) for p in (locked.get("picture_ids") or [])}
        blocks_pictures = members & doomed_pids
        if set_id in doomed_set_ids or blocks_pictures:
            sets_to_unlock.append({"id": set_id, "name": locked.get("name")})
            frozen |= blocks_pictures
    return {"sets_to_unlock": sets_to_unlock, "frozen_picture_ids": sorted(frozen)}


def print_report(plan: dict, locks: dict, applying: bool, unlocking: bool) -> None:
    """Print the human-readable plan.

    Args:
        plan: The plan from build_plan.
        locks: The result of check_locked.
        applying: True when the run will actually delete.
        unlocking: True when blocking locked sets will be unlocked first.
    """
    counts = plan["counts"]
    delete = plan["delete"]
    keep = plan["keep"]
    header = "APPLYING" if applying else "DRY RUN - nothing will be deleted"
    if plan["mode"] == MODE_REMOVE_IN:
        intent = f"remove everything belonging to {plan['project']['name']!r}"
    else:
        intent = f"remove everything NOT belonging to {plan['project']['name']!r}"

    print("=" * 78)
    print(f"PixlStash library split - {header}")
    print(f"  Project : {plan['project']['name']} (id={plan['project']['id']})")
    print(f"  Mode    : {plan['mode']} - {intent}")
    print("=" * 78)

    print(
        f"\nPictures - {counts['pictures_total']} total "
        f"({counts['pictures_live']} live, "
        f"{counts['pictures_scrapheaped']} already scrapheaped)"
    )
    print(f"  in the project                 {counts['pictures_in_project']:>8}")
    print(f"    via direct membership        {counts['pictures_in_project_direct']:>8}")
    print(
        f"    via a set in the project     {counts['pictures_in_project_via_sets']:>8}"
    )
    print(
        f"    via a character in it        {counts['pictures_in_project_via_characters']:>8}"
    )
    print(f"  in other projects              {counts['pictures_in_other_projects']:>8}")
    print(f"  in no project                  {counts['pictures_unassigned']:>8}")
    print(f"  on both sides (kept)           {counts['pictures_overlapping']:>8}")
    print(
        f"  TO DELETE                      {len(delete['picture_ids']):>8}"
        f"  ({len(delete['already_scrapheaped_ids'])} already in the scrapheap)"
    )

    print(f"\nPicture sets - {counts['sets_total']} total")
    print(f"  exclusive to the project       {counts['sets_exclusive_to_project']:>8}")
    print(f"  shared with other projects     {counts['sets_overlapping']:>8}  (kept)")
    print(f"  outside the project            {counts['sets_outside_project']:>8}")
    print(f"  TO DELETE                      {len(delete['sets']):>8}")

    print(f"\nCharacters - {counts['characters_total']} total")
    print(
        f"  exclusive to the project       {counts['characters_exclusive_to_project']:>8}"
    )
    print(
        f"  shared with other projects     {counts['characters_overlapping']:>8}  (kept)"
    )
    print(f"  outside the project            {counts['characters_outside_project']:>8}")
    print(f"  TO DELETE                      {len(delete['characters']):>8}")

    print(f"\nProjects TO DELETE - {len(delete['projects'])}")
    for entry in delete["projects"]:
        print(f"  {entry['id']:>6}  {entry['name']}")

    if keep["overlapping_sets"] or keep["overlapping_characters"]:
        print("\nKept because they sit on both sides of the split:")
        for entry in keep["overlapping_sets"]:
            print(f"  set        {entry['id']:>6}  {entry['name']}")
        for entry in keep["overlapping_characters"]:
            print(f"  character  {entry['id']:>6}  {entry['name']}")

    collateral = plan["warnings"]["collateral_reference_sets"]
    if collateral:
        print("\nWARNING - deleting these characters also deletes their reference")
        print("picture set, which is not otherwise scheduled for deletion:")
        for entry in collateral:
            print(f"  character {entry['character_id']} -> set {entry['set_id']}")

    if locks["sets_to_unlock"]:
        print(
            f"\n{len(locks['sets_to_unlock'])} locked picture set(s) block this plan. "
            "A locked set refuses"
        )
        print(
            "deletion (423) and freezes its members against the purge, so "
            f"{len(locks['frozen_picture_ids'])} picture(s)"
        )
        if unlocking:
            print("would silently survive. They will be UNLOCKED first:")
        else:
            print(
                "would silently survive. --no-unlock was given, so the run will "
                "refuse to start:"
            )
        for entry in locks["sets_to_unlock"]:
            print(f"  set {entry['id']:>6}  {entry['name']}")
    print()


def execute_plan(
    client: PixlStashClient,
    plan: dict,
    locks: dict,
    include_protected: bool,
    unlock: bool,
    purge_timeout: int,
) -> dict:
    """Carry out the plan.

    Order matters: characters first (that clears their faces and drops their
    reference sets), then sets, then the pictures, then the project rows. Every
    picture is soft-deleted into the scrapheap before it can be purged, because
    the purge only ever operates on scrapheap rows.

    Args:
        client: An authenticated API client.
        plan: The plan from build_plan.
        locks: The result of check_locked.
        include_protected: True to also destroy protected reference-folder
            originals (``allow_delete_file=false``).
        unlock: True to unlock the blocking sets first.
        purge_timeout: Timeout in seconds for each purge request.

    Returns:
        An outcome summary with per-stage counts and anything that was skipped.
    """
    outcome: Dict[str, Any] = {
        "unlocked_sets": [],
        "deleted_characters": 0,
        "deleted_sets": 0,
        "soft_deleted_pictures": 0,
        "purged_pictures": 0,
        "skipped_protected": 0,
        "skipped_locked": [],
        "deleted_projects": 0,
        "errors": [],
    }

    if unlock and locks["sets_to_unlock"]:
        for entry in locks["sets_to_unlock"]:
            try:
                client.request(
                    "PATCH",
                    f"{API}/picture_sets/{entry['id']}",
                    payload={"locked": False},
                )
                outcome["unlocked_sets"].append(entry["id"])
                logger.info("Unlocked set id=%s (%s).", entry["id"], entry["name"])
            except SplitError as exc:
                message = (
                    f"Failed to unlock set id={entry['id']} ({entry['name']}): {exc}"
                )
                logger.error(message)
                outcome["errors"].append(message)

    for entry in plan["delete"]["characters"]:
        try:
            client.request("DELETE", f"{API}/characters/{entry['id']}")
            outcome["deleted_characters"] += 1
        except SplitError as exc:
            message = (
                f"Failed to delete character id={entry['id']} ({entry['name']}): {exc}"
            )
            logger.error(message)
            outcome["errors"].append(message)
    logger.info("Deleted %d character(s).", outcome["deleted_characters"])

    for entry in plan["delete"]["sets"]:
        try:
            client.request("DELETE", f"{API}/picture_sets/{entry['id']}")
            outcome["deleted_sets"] += 1
        except SplitError as exc:
            # A 404 here is expected when the set was a character's reference set
            # and went with the character above; anything else is a real failure.
            if "HTTP 404" in str(exc):
                logger.info(
                    "Set id=%s (%s) was already gone - deleted with its character.",
                    entry["id"],
                    entry["name"],
                )
                continue
            message = f"Failed to delete set id={entry['id']} ({entry['name']}): {exc}"
            logger.error(message)
            outcome["errors"].append(message)
    logger.info("Deleted %d picture set(s).", outcome["deleted_sets"])

    picture_ids = plan["delete"]["picture_ids"]
    already_scrapheaped = set(plan["delete"]["already_scrapheaped_ids"])
    to_soft_delete = [pid for pid in picture_ids if pid not in already_scrapheaped]
    for batch in _chunks(to_soft_delete, BULK_DELETE_MAX_IDS):
        try:
            resp = (
                client.request(
                    "DELETE", f"{API}/pictures", payload={"picture_ids": batch}
                )
                or {}
            )
            outcome["soft_deleted_pictures"] += int(resp.get("deleted_count", 0))
            for pid in resp.get("skipped_locked") or []:
                outcome["skipped_locked"].append(int(pid))
        except SplitError as exc:
            message = f"Bulk soft-delete failed for {len(batch)} picture(s): {exc}"
            logger.error(message)
            outcome["errors"].append(message)
    logger.info(
        "Moved %d picture(s) to the scrapheap (%d were already there).",
        outcome["soft_deleted_pictures"],
        len(already_scrapheaped),
    )

    for batch in _chunks(picture_ids, PURGE_CHUNK):
        try:
            preview = (
                client.request(
                    "POST",
                    f"{API}/pictures/scrapheap/delete-preview",
                    payload={"ids": batch},
                )
                or {}
            )
            token = preview.get("confirm_token")
            if not token:
                message = f"Preview returned no confirm_token for {len(batch)} id(s)."
                logger.error(message)
                outcome["errors"].append(message)
                continue
            if not include_protected and preview.get("protected_count"):
                outcome["skipped_protected"] += int(preview["protected_count"])
            resp = (
                client.request(
                    "DELETE",
                    f"{API}/pictures/scrapheap",
                    payload={
                        "ids": batch,
                        "include_protected": include_protected,
                        "confirm_token": token,
                    },
                    timeout=purge_timeout,
                )
                or {}
            )
            outcome["purged_pictures"] += int(resp.get("deleted_count", 0))
            for pid in resp.get("skipped_locked") or []:
                outcome["skipped_locked"].append(int(pid))
        except SplitError as exc:
            message = f"Purge failed for a chunk of {len(batch)} picture(s): {exc}"
            logger.error(message)
            outcome["errors"].append(message)
    logger.info("Permanently removed %d picture(s).", outcome["purged_pictures"])

    for entry in plan["delete"]["projects"]:
        try:
            client.request("DELETE", f"{API}/projects/{entry['id']}")
            outcome["deleted_projects"] += 1
        except SplitError as exc:
            message = (
                f"Failed to delete project id={entry['id']} ({entry['name']}): {exc}"
            )
            logger.error(message)
            outcome["errors"].append(message)
    logger.info("Deleted %d project(s).", outcome["deleted_projects"])

    outcome["skipped_locked"] = sorted(set(outcome["skipped_locked"]))
    return outcome


def print_outcome(outcome: dict) -> None:
    """Print the post-run summary.

    Args:
        outcome: The result of execute_plan.
    """
    print("\n" + "=" * 78)
    print("Result")
    print("=" * 78)
    print(f"  unlocked sets            {len(outcome['unlocked_sets']):>8}")
    print(f"  characters deleted       {outcome['deleted_characters']:>8}")
    print(f"  picture sets deleted     {outcome['deleted_sets']:>8}")
    print(f"  pictures scrapheaped     {outcome['soft_deleted_pictures']:>8}")
    print(f"  pictures purged          {outcome['purged_pictures']:>8}")
    print(f"  projects deleted         {outcome['deleted_projects']:>8}")
    if outcome["skipped_protected"]:
        print(
            f"\n  {outcome['skipped_protected']} protected reference-folder original(s) "
            "were left in place."
        )
        print("  Re-run with --include-protected to destroy those too.")
    if outcome["skipped_locked"]:
        print(
            f"\n  {len(outcome['skipped_locked'])} picture(s) were skipped because a "
            "locked set still freezes them:"
        )
        print(f"  {outcome['skipped_locked']}")
        print("  Unlock the set and run the script again.")
    if outcome["errors"]:
        print(f"\n  {len(outcome['errors'])} error(s):")
        for message in outcome["errors"]:
            print(f"    {message}")
    print()


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse the command line.

    Args:
        argv: Argument list, defaulting to sys.argv[1:].

    Returns:
        The parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("PIXLSTASH_URL", "https://localhost:9537"),
        help="Server root URL (default: %(default)s, or env PIXLSTASH_URL).",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("PIXLSTASH_TOKEN"),
        help="Full-scope API token (or env PIXLSTASH_TOKEN). A resource-scoped "
        "token sees only part of the library and would split it incompletely.",
    )
    parser.add_argument(
        "--project",
        default="PixlTagger",
        help="Name of the project that defines the split (default: %(default)s).",
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=[MODE_REMOVE_IN, MODE_REMOVE_OUT],
        help=f"{MODE_REMOVE_IN}: delete everything belonging to the project. "
        f"{MODE_REMOVE_OUT}: delete everything that does not, including "
        "entities in no project at all.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete. Without this the script only prints the plan.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation prompt that --apply otherwise asks for.",
    )
    parser.add_argument(
        "--include-protected",
        action="store_true",
        help="Also destroy protected reference-folder originals "
        "(allow_delete_file=false). They are left in place by default.",
    )
    parser.add_argument(
        "--no-unlock",
        dest="unlock",
        action="store_false",
        help="Do not unlock the locked picture sets that block the plan. A locked "
        "set refuses deletion and freezes its members against the purge, so the "
        "run will refuse to start rather than leave them behind silently.",
    )
    parser.add_argument(
        "--keep-projects",
        action="store_true",
        help="Leave the project rows themselves in place, deleting only their "
        "pictures, sets and characters.",
    )
    parser.add_argument(
        "--report",
        help="Write the full plan, including every id, to this JSON file.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Skip TLS verification (needed for the self-signed certificate a "
        "local https server uses).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Per-request timeout in seconds (default: %(default)s).",
    )
    parser.add_argument(
        "--purge-timeout",
        type=int,
        default=600,
        help="Timeout in seconds for each purge request (default: %(default)s).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point.

    Args:
        argv: Argument list, defaulting to sys.argv[1:].

    Returns:
        Process exit code.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    if not args.token:
        logger.error("--token is required (or set PIXLSTASH_TOKEN).")
        return 2

    client = PixlStashClient(
        args.url,
        args.token,
        verify_ssl=not args.insecure,
        timeout=args.timeout,
    )
    try:
        client.login()
        plan = build_plan(client, args.project, args.mode)
        if args.keep_projects:
            plan["delete"]["projects"] = []
        locks = check_locked(client, plan)
    except SplitError as exc:
        logger.error("%s", exc)
        return 1

    print_report(plan, locks, applying=args.apply, unlocking=args.unlock)

    if args.report:
        report = {"plan": plan, "locks": locks, "applied": args.apply}
        try:
            with open(args.report, "w", encoding="utf-8") as handle:
                json.dump(report, handle, indent=2)
            logger.info("Wrote the plan to %s", args.report)
        except OSError as exc:
            logger.error("Could not write the report to %s: %s", args.report, exc)
            return 1

    if not args.apply:
        print("Dry run only. Re-run with --apply to carry this out.")
        return 0

    if locks["sets_to_unlock"] and not args.unlock:
        logger.error(
            "%d locked picture set(s) block this plan and --no-unlock was "
            "given. Refusing to run a split that would silently leave %d "
            "picture(s) behind.",
            len(locks["sets_to_unlock"]),
            len(locks["frozen_picture_ids"]),
        )
        return 1

    if not args.yes:
        total = (
            len(plan["delete"]["picture_ids"])
            + len(plan["delete"]["sets"])
            + len(plan["delete"]["characters"])
        )
        print(
            f"This permanently deletes {len(plan['delete']['picture_ids'])} picture(s) "
            "FROM THE DATABASE AND FROM DISK, plus "
            f"{len(plan['delete']['sets'])} set(s) and "
            f"{len(plan['delete']['characters'])} character(s) - {total} rows in all."
        )
        print("It cannot be undone. Make sure the other half of the split already")
        print("has its own copy of the image root.")
        answer = input(
            f"Type the project name ({plan['project']['name']}) to proceed: "
        )
        if answer.strip() != plan["project"]["name"]:
            logger.info("Confirmation did not match. Nothing was deleted.")
            return 1

    try:
        outcome = execute_plan(
            client,
            plan,
            locks,
            include_protected=args.include_protected,
            unlock=args.unlock,
            purge_timeout=args.purge_timeout,
        )
    except SplitError as exc:
        logger.error("Aborted mid-run: %s", exc)
        return 1

    print_outcome(outcome)

    if args.report:
        report = {"plan": plan, "locks": locks, "applied": True, "outcome": outcome}
        try:
            with open(args.report, "w", encoding="utf-8") as handle:
                json.dump(report, handle, indent=2)
            logger.info("Updated %s with the outcome.", args.report)
        except OSError as exc:
            logger.error("Could not update the report at %s: %s", args.report, exc)

    return 1 if outcome["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
