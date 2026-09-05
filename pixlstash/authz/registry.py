"""The route-policy registry: the single authorization declaration table.

``ROUTE_POLICIES`` is the one place every mounted HTTP route declares its access
requirement, keyed by ``(method, effective_path_template)`` - the *prefixed*
path as enumerated by :func:`pixlstash.route_inventory.iter_api_route_contexts`
(e.g. ``("GET", "/api/v1/pictures/{picture_id}/thumbnail")``). It IS the coverage
matrix: reviewable in one screen, diffable, greppable. See the backend refactor
plan §3.2 and ``docs/backend_architecture.md`` §16.2.

**Phase 1 Step 2 - back-fill of current behaviour.** Every mounted route below is
declared with the single :class:`AccessPolicy` that reproduces its behaviour
TODAY, so that when the gate flips to enforcing (Steps 3-4) nothing changes. The
derivation, per route, comes from the auth middleware gating in ``auth.py``
(``AUTH_EXCLUDED_*``, ``READ_BLOCKED_GET_PATHS``, ``READ_SAFE_POST_PATHS``, the
non-GET block for READ tokens, ``require_local_for_write``, the ``ALL``
+``resource_type`` fail-closed rejection) PLUS the inline object checks in the
handlers (``enforce_picture_scope`` / ``fetch_scope_allowed_picture_ids`` /
``require_unscoped_owner`` / the ``_require_scope_allows_*`` ladders). The full
per-route rationale and the reviewer flags live in
``docs/authz-coverage-matrix.md`` - that document is the artifact the
adversarial security review consumes.

**Semantics that shape the mapping (verified against the code):**

* The auth middleware blocks READ-scoped tokens from every non-GET method except
  the ``READ_SAFE_POST_PATHS`` allowlist, and from the ``READ_BLOCKED_GET_PATHS``
  GET set. Every resource-scoped share token is a READ token
  (``ALL``+``resource_type`` is refused at mint and fail-closed at the
  middleware), so a mutating route with no ``READ_SAFE`` exemption is reachable
  ONLY by an unscoped owner today - hence ``OWNER_ONLY`` is a no-op there.
* ``fetch_scope_allowed_picture_ids`` (and the ``_require_scope_allows_*``
  ladders) return "no restriction" for BOTH an owner token and an unscoped-READ
  token (``token_scope.resource_type is None``); they only narrow/deny a
  *resource-scoped* token. So a handler's inline scope filter only affects
  resource-scoped share tokens.
* The inline checks REMAIN until Step 5; these declarations record the intended
  end-state so the gate can take over without a behaviour change.

The ``AUTHZ_GATE_ENFORCING`` constant in ``pixlstash/authz/gate.py`` is still
``False`` - this table is declared but not yet enforced (Step 2 is declarations
only). The CI guardrail's audit allowlist burns to zero as this table fills.
"""

from __future__ import annotations

from pixlstash.authz.policy import AccessPolicy, LibraryAccessMode, RoutePolicy

# Short aliases keep the table scannable in one screen.
_PUBLIC = AccessPolicy.PUBLIC
_ANY = AccessPolicy.ANY_TOKEN
_OWNER = AccessPolicy.OWNER_ONLY
_LOCAL = AccessPolicy.LOCAL_OWNER_ONLY
_LOOPBACK = AccessPolicy.LOOPBACK_OWNER_ONLY
_PIC = AccessPolicy.PICTURE_SCOPED
_SET = AccessPolicy.SET_SCOPED
_CHAR = AccessPolicy.CHARACTER_SCOPED
_PROJ = AccessPolicy.PROJECT_SCOPED
_LIST = AccessPolicy.SCOPED_LIST

# A SCOPED_LIST route that has been AUDITED to filter its own result set for a
# resource-scoped token (via the handler's inline ``fetch_scope_allowed_*`` /
# ``token_scope`` filter, or by self-emptying). ``scope_aware=True`` is the
# machine-checked record of that audit: the gate passes such a route through to
# its handler filter, and fails **closed** (403) for any SCOPED_LIST route left
# WITHOUT it - so a new, unaudited list route leaks nothing to a scoped token
# (backend refactor plan §3.6; principal ruling 2026-07-21 D4). Shared frozen
# singleton - every current list route is audited (matrix derivation), so they
# all point at this one instance.
_LIST_AWARE = RoutePolicy(_LIST, scope_aware=True)


ROUTE_POLICIES: dict[tuple[str, str], RoutePolicy] = {
    # ── App-level / public (auth-excluded in AUTH_EXCLUDED_*) ───────────────
    ("GET", "/"): RoutePolicy(
        _PUBLIC,
        library_access=LibraryAccessMode.HUB_ONLY,
        justification="Frontend SPA index; auth-excluded; no owner data",
    ),
    ("GET", "/version"): RoutePolicy(
        _PUBLIC,
        library_access=LibraryAccessMode.HUB_ONLY,
        justification="Health/version probe; auth-excluded",
    ),
    ("GET", "/scalar"): RoutePolicy(
        _PUBLIC,
        library_access=LibraryAccessMode.HUB_ONLY,
        justification="API docs UI; auth-excluded",
    ),
    ("GET", "/favicon.ico"): RoutePolicy(
        _PUBLIC,
        library_access=LibraryAccessMode.HUB_ONLY,
        justification="Static favicon; auth-excluded",
    ),
    ("GET", "/docs"): RoutePolicy(
        _PUBLIC,
        library_access=LibraryAccessMode.HUB_ONLY,
        justification="Swagger UI; auth-excluded (/docs/ prefix)",
    ),
    ("GET", "/docs/oauth2-redirect"): RoutePolicy(
        _PUBLIC,
        library_access=LibraryAccessMode.HUB_ONLY,
        justification="Swagger oauth2 redirect; auth-excluded",
    ),
    ("GET", "/openapi.json"): RoutePolicy(
        _PUBLIC,
        library_access=LibraryAccessMode.HUB_ONLY,
        justification="OpenAPI schema; auth-excluded",
    ),
    ("GET", "/{full_path:path}"): RoutePolicy(
        _PUBLIC,
        library_access=LibraryAccessMode.HUB_ONLY,
        justification=(
            "Frontend SPA fallback serving the static shell/assets; returns no "
            "owner resource data. NEEDS REVIEW: this template is not statically "
            "in AUTH_EXCLUDED_*, so the middleware requires auth for a concrete "
            "non-excluded deep path; the planned PUBLIC-consistency check must "
            "reconcile (add to exclusions or special-case)."
        ),
    ),
    ("GET", "/api/v1/check-session"): RoutePolicy(
        _PUBLIC,
        library_access=LibraryAccessMode.HUB_ONLY,
        justification="Session status probe; auth-excluded (/check-session)",
    ),
    ("GET", "/api/v1/login"): RoutePolicy(
        _PUBLIC,
        library_access=LibraryAccessMode.HUB_ONLY,
        justification="Registration-status probe; auth-excluded (/login)",
    ),
    ("POST", "/api/v1/login"): RoutePolicy(
        _PUBLIC,
        library_access=LibraryAccessMode.HUB_ONLY,
        justification="Password login / first-owner claim; auth-excluded (/login)",
    ),
    ("POST", "/api/v1/logout"): RoutePolicy(
        _PUBLIC,
        library_access=LibraryAccessMode.HUB_ONLY,
        justification="Logout; auth-excluded (/logout)",
    ),
    ("GET", "/share/{token_slug}"): RoutePolicy(
        _PUBLIC,
        justification="Share-link landing; resolves its own token; auth-excluded (/share/ prefix)",
    ),
    # ── App-level authenticated, no per-object data ─────────────────────────
    ("GET", "/api/v1/network/info"): RoutePolicy(
        _ANY, library_access=LibraryAccessMode.HUB_ONLY
    ),
    ("GET", "/api/v1/protected"): RoutePolicy(
        _ANY, library_access=LibraryAccessMode.HUB_ONLY
    ),
    # ── config.py (user account + server-config) ────────────────────────────
    ("GET", "/api/v1/users/me/config"): RoutePolicy(
        _OWNER,
        justification="Owner config; READ_BLOCKED_GET_PATHS blocks READ tokens; only owner reaches",
    ),
    ("GET", "/api/v1/users/me/penalised-tags"): RoutePolicy(_ANY),
    # ── telemetry.py (anonymous install ID) ─────────────────────────────────
    # Owner-only, not any_token: the install ID is a stable identifier for the
    # installation, so handing it to a share-link holder would let them
    # correlate visits across links. It returns no per-object picture data,
    # which is why it is owner_only rather than a *_SCOPED policy.
    ("GET", "/api/v1/telemetry/install-id"): RoutePolicy(
        _OWNER,
        justification=(
            "Owner-only read of the installation's anonymous install ID. Not "
            "any_token: the ID is a stable installation identifier and a "
            "resource-scoped share token must not be able to read it."
        ),
    ),
    ("POST", "/api/v1/telemetry/install-id/recreate"): RoutePolicy(
        _OWNER,
        justification=(
            "Owner-only rotation of the install ID; POST is blocked for READ "
            "tokens, so only an unscoped owner reaches it. Sibling of GET "
            "/telemetry/install-id."
        ),
    ),
    ("PATCH", "/api/v1/users/me/config"): RoutePolicy(
        _OWNER, justification="require_unscoped_owner; owner config write"
    ),
    ("POST", "/api/v1/users/me/auth"): RoutePolicy(
        _OWNER,
        justification="Change owner password; POST blocked for READ tokens; owner only",
    ),
    ("GET", "/api/v1/users/me/auth"): RoutePolicy(
        _OWNER,
        # Library-independent (2026-08-01): identity lives in the hub, so "who
        # am I" is the same answer whichever library is active, and it returns
        # no library content. Without this exemption a token stamped for a
        # non-active library could not even discover why it was being refused.
        library_independent=True,
        justification=(
            "Owner account state (owner username + has_password). F-c hardening "
            "rider (decided 2026-07-21): tightened any_token -> owner_only so a "
            "resource-scoped share token cannot read the owner's account identity. "
            "Gate now rejects scoped tokens; unscoped-READ newly 403'd here too."
        ),
    ),
    ("POST", "/api/v1/users/me/token"): RoutePolicy(
        _OWNER, justification="Mint API token; POST blocked for READ tokens; owner only"
    ),
    ("GET", "/api/v1/users/me/token"): RoutePolicy(
        _OWNER,
        justification="List API tokens; list_tokens rejects token_scope is not None (auth.py:1178), so every scoped/READ token is 403'd; owner only",
    ),
    ("DELETE", "/api/v1/users/me/token/{token_id}"): RoutePolicy(
        _OWNER,
        justification="Revoke API token; DELETE blocked for READ tokens; owner only",
    ),
    ("PATCH", "/api/v1/users/me/token/{token_id}"): RoutePolicy(
        _OWNER,
        justification="Update API token; PATCH blocked for READ tokens; owner only",
    ),
    ("GET", "/api/v1/users/me/watermark"): RoutePolicy(_ANY),
    ("POST", "/api/v1/users/me/watermark"): RoutePolicy(
        _OWNER,
        justification="Upload watermark; POST blocked for READ tokens; owner only",
    ),
    ("DELETE", "/api/v1/users/me/watermark"): RoutePolicy(
        _OWNER,
        justification="Delete watermark; DELETE blocked for READ tokens; owner only",
    ),
    ("GET", "/api/v1/users/me/shared-resource-ids"): RoutePolicy(
        _OWNER,
        justification="get_shared_resource_ids rejects token_scope is not None (auth.py:1314), so every scoped/READ token is 403'd; owner only",
    ),
    ("POST", "/api/v1/users/me/shared-picture-ids/batch"): RoutePolicy(
        _OWNER, justification="POST not in READ_SAFE; READ tokens blocked; owner only"
    ),
    ("DELETE", "/api/v1/users/me/tokens/by-resource"): RoutePolicy(
        _OWNER,
        justification="Revoke tokens for a resource; DELETE blocked for READ tokens; owner only",
    ),
    ("GET", "/api/v1/session/context"): RoutePolicy(_ANY),
    ("GET", "/api/v1/workers/progress"): RoutePolicy(_ANY),
    ("GET", "/api/v1/server-config/watch-folders"): RoutePolicy(
        _OWNER, justification="require_unscoped_owner; also READ_BLOCKED; owner only"
    ),
    ("GET", "/api/v1/server-config/filesystem-roots"): RoutePolicy(
        _OWNER, justification="require_unscoped_owner; also READ_BLOCKED; owner only"
    ),
    ("GET", "/api/v1/server-config/snapshots"): RoutePolicy(
        _OWNER, justification="require_unscoped_owner"
    ),
    ("PATCH", "/api/v1/server-config/snapshots"): RoutePolicy(
        _OWNER, justification="require_unscoped_owner"
    ),
    ("GET", "/api/v1/server-config/scrapheap-retention"): RoutePolicy(
        _OWNER,
        justification=(
            "Owner server-config read; returns no per-object data. Sibling of "
            "GET /server-config/snapshots (same owner-settings tier). Not a "
            "host-capability route (§16.3): it neither touches the host "
            "filesystem browser nor spawns a host GUI/shell, so no locality "
            "tier applies."
        ),
    ),
    ("GET", "/api/v1/server-config/scrapheap-retention/impact"): RoutePolicy(
        _OWNER,
        justification=(
            "Owner server-config read; sibling of GET "
            "/server-config/scrapheap-retention. Reports a per-LIBRARY "
            "destruction count (how many scrapheap pictures a retention "
            "reduction would purge), which is exactly the kind of aggregate a "
            "resource-scoped share token must not see, so owner_only rather "
            "than any_token. Pure read: no config write, no purge, no "
            "reduced_at stamp. Not a §16.3 host-capability route."
        ),
    ),
    ("PATCH", "/api/v1/server-config/scrapheap-retention"): RoutePolicy(
        _OWNER,
        justification=(
            "Owner server-config write; PATCH is blocked for READ tokens, so "
            "only an unscoped owner reaches it. Sibling of PATCH "
            "/server-config/snapshots. It sets the auto-purge window but "
            "performs NO destruction itself (the scheduled task is the only "
            "deleter), so the §16.3 host-capability tiers do not apply."
        ),
    ),
    ("GET", "/api/v1/server-config/layout"): RoutePolicy(
        _LOCAL,
        justification=(
            "§16.3 reads back how this library's own picture root is laid out, "
            "and is the control surface of the PATCH beside it - the tier that "
            "alone may decide where the owner's files get written is the tier "
            "that may see the decision. Same reasoning as GET "
            "/server-config/views; owner + loopback/LAN/Tailscale, or remote "
            "owner iff allow_remote_host_ops=true (§16.3.1)."
        ),
    ),
    ("PATCH", "/api/v1/server-config/layout"): RoutePolicy(
        _LOCAL,
        justification=(
            "§16.3 decides the folder names PixlStash writes into the library "
            "root from here on, and therefore where a background task later "
            "renames the owner's files to. It writes no file itself and moves "
            "nothing - a layout is true of every path already there - but the "
            "authority it hands out is host-filesystem authority, so it sits "
            "on the tier that grants it. Sibling of PATCH "
            "/server-config/views; owner + loopback/LAN/Tailscale, or remote "
            "owner iff allow_remote_host_ops=true (§16.3.1)."
        ),
    ),
    ("GET", "/api/v1/server-config/layout/migration"): RoutePolicy(
        _LOCAL,
        justification=(
            "§16.3 v1.11 Phase 4c. Counts what moving every file in the "
            "library root onto its layout would do, and is the consent screen "
            "of the POST beside it - the tier that alone may move the owner's "
            "whole tree is the tier that may see the count. It also reads back "
            "host-filesystem facts nothing else exposes: how many files cross "
            "a mount point inside the library, and sample paths relative to "
            "the root. Sibling of GET /server-config/layout; owner + "
            "loopback/LAN/Tailscale, or remote owner iff "
            "allow_remote_host_ops=true (§16.3.1)."
        ),
    ),
    ("POST", "/api/v1/server-config/layout/migration"): RoutePolicy(
        _LOCAL,
        justification=(
            "§16.3 v1.11 Phase 4c, and the most host-filesystem authority any "
            "route in this library exercises: it renames every picture in the "
            "library's own root into the folders the layout renders. It takes "
            "no caller-supplied path - the root is the library's own and every "
            "destination is computed from a layout only this same tier could "
            "have set - and the planner still refuses a source outside the "
            "root, a symlink, or a destination that would escape it. Strictly "
            "above POST /pictures/layout/move-to-match, which is picture-scoped "
            "because the caller names the pictures; here the caller names none "
            "and the scope is the whole library. Sibling of PATCH "
            "/server-config/layout, which grants this authority in the first "
            "place; owner + loopback/LAN/Tailscale, or remote owner iff "
            "allow_remote_host_ops=true (§16.3.1)."
        ),
    ),
    ("GET", "/api/v1/server-config/views"): RoutePolicy(
        _LOCAL,
        justification=(
            "§16.3 reads back a host path this library publishes its Views tree "
            "to, and is the control surface of the PATCH beside it - the tier "
            "that alone may publish the tree is the tier that may see where it "
            "went. Sibling of GET /model-moves for that reason; owner + "
            "loopback/LAN/Tailscale, or remote owner iff "
            "allow_remote_host_ops=true (§16.3.1)."
        ),
    ),
    ("PATCH", "/api/v1/server-config/views"): RoutePolicy(
        _LOCAL,
        justification=(
            "§16.3 takes a caller-supplied host path and writes a folder tree of "
            "links into it, removing and rebuilding the subtrees it owns - the "
            "POST /model-folders class for the path it accepts and the POST "
            "/model-moves class for the filesystem it writes. It creates only "
            "links; the ONLY thing it unlinks is a name that is not the last "
            "one (a symlink, or a regular file with st_nlink > 1), so no file "
            "whose sole copy is in the tree can be removed by it - anything "
            "else is reported as kept_by_owner and left alone. Each destination "
            "is resolved with resolve_path_within against its kind folder and "
            "each kind folder against the root, and a symlink standing where a "
            "kind folder goes is unlinked as a link rather than descended, so "
            "the rebuild cannot be steered outside the views root. owner + "
            "loopback/LAN/Tailscale, or remote owner iff "
            "allow_remote_host_ops=true (§16.3.1)."
        ),
    ),
    ("POST", "/api/v1/server-config/open"): RoutePolicy(
        _LOOPBACK,
        justification="§16.3.1 RED LINE: opens the server config path in the host file browser (_open_in_os → os.startfile/open/xdg-open - same host-GUI spawn as pictures/open-location and reference-folders/open); loopback-only, allow_remote_host_ops can NOT loosen it",
    ),
    # ── libraries.py (the hub/vault split; multi-library plan §11 q3/q4) ─────
    ("GET", "/api/v1/libraries"): RoutePolicy(
        _OWNER,
        # Library-independent: returns the registry, not library content, and
        # cannot reach another library's data. It must keep answering while a
        # token is being refused or a switch is in flight, or the tab could not
        # explain either.
        library_independent=True,
        justification=(
            "Registry read. OWNER_ONLY rather than LOCAL_OWNER_ONLY (CSO ruling "
            "2026-08-01, plan §11 q3) so the Settings tab renders for any owner; "
            "the host information it would otherwise leak (folder paths, the CLI "
            "hint) is omitted for a non-local caller by the handler instead of "
            "the whole route being denied. Returns no per-object data."
        ),
    ),
    # The lifecycle verbs (v1.11 "Your existing library", plan §Phase 1). All
    # four are HUB_ONLY: they read and write the registry, never the active
    # vault, so they need no library lease.
    #
    # HUB_ONLY also exempts them from the switch's 503, and that is deliberate
    # rather than incidental: the registry has to stay answerable when there is
    # no open vault, which is the state an owner recovers from by attaching or
    # switching. `DELETE` is the one that cannot take the exemption - it reads
    # `is_active`, and mid-swap that flag is moving - so its handler refuses
    # while a switch is in flight, in its own words.
    #
    # `library_independent` is a different knob and is left at its safe default
    # False: it governs the token PIN, not the 503, so an ALL token stamped for
    # another library is refused here exactly as it is on a data route. A route
    # is pinned by omission as an undeclared one is denied by omission, and none
    # of these four needs the exemption `GET /libraries` needs.
    ("GET", "/api/v1/libraries/inspect"): RoutePolicy(
        _LOCAL,
        library_access=LibraryAccessMode.HUB_ONLY,
        justification="§16.3 takes a caller-supplied host path and walks it to say what the folder is - the same host-filesystem read authority as GET /filesystem/browse, through the same validate_reference_folder_path chokepoint; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3.1)",
    ),
    ("POST", "/api/v1/libraries"): RoutePolicy(
        _LOCAL,
        library_access=LibraryAccessMode.HUB_ONLY,
        justification="§16.3 takes a caller-supplied host path and, for a folder with no vault, writes a SQLite database into it and restricts the folder to the owner (0700) - write authority inside a host folder, alongside POST /filesystem/folders which is already on this tier. It creates no directory: the folder must already exist, which is what keeps its authority to one named folder. Attaching moves, renames and copies nothing, and never reads the incoming vault's user/user_token rows; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3.1)",
    ),
    ("PATCH", "/api/v1/libraries/{library_uuid}"): RoutePolicy(
        _LOCAL,
        library_access=LibraryAccessMode.HUB_ONLY,
        justification="§16.3 tier by consistency, not by capability: renaming writes one hub column, takes no host path and renames nothing on disk. It sits with its siblings because the Settings pane gates the whole management menu on the same can_manage locality answer, and a looser tier here would give that pane two rules to explain while buying no reachability the owner does not already have",
    ),
    ("DELETE", "/api/v1/libraries/{library_uuid}"): RoutePolicy(
        _LOCAL,
        library_access=LibraryAccessMode.HUB_ONLY,
        justification="§16.3 tier for the POST /libraries/active reason rather than the path-authority one: it takes a registry uuid, never a host path, and removes no file - it clears the attached flag and keeps the row. What it exercises is authority over other principals' state, because every share link pointing at that library stops working until the folder is added again. The active library is refused by the registry; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3.1)",
    ),
    ("POST", "/api/v1/libraries/active"): RoutePolicy(
        _LOCAL,
        library_independent=True,
        library_access=LibraryAccessMode.SWITCH_WRITER,
        justification=(
            "§16.3 locality tier, but NOT for the usual reason: this route takes "
            "a registry uuid, never a caller-supplied host path, so it is not the "
            "path-authority class. It is local-only because switching resets "
            "every connected client's session and takes the outgoing library's "
            "share links offline: authority over OTHER principals' state, which "
            "is what the locality tier is buying. Loopback/LAN/Tailscale all "
            "pass, so a phone on Tailscale is unaffected; a genuinely remote "
            "owner needs allow_remote_host_ops. "
            "NOT a confidentiality pivot (corrected 2026-08-07). The original "
            "CSO ruling of 2026-08-01 and plan §11 q4 justified this tier as "
            "stopping one stolen token from reaching every library by switching. "
            "That was true when written, against a design where owner tokens "
            "were unpinned. The library pin landed afterwards and closes it "
            "independently: a token stamped for a non-active library is refused "
            "on every data route (auth.py), and minting is pinned too, so a "
            "thief who switches locks themselves out of the library they had "
            "and gains nothing in the new one. Keep the tier for the disruption "
            "reason above; do not re-derive it from the pivot."
        ),
    ),
    # ── model_shelf.py (the model shelf's reads; shelf plan B5) ──────────────
    # OWNER_ONLY on the DEFAULT library pin. ``library_independent`` is left at
    # False on purpose even though ``model`` lives in the hub: these routes join
    # hub content to the active vault's ``adapter_attachment`` rows, so they DO
    # return library content and a token stamped for another library must not
    # reach them. Scoped by omission, exactly as §16.1 intends.
    #
    # Accepted residual (shelf plan B5, stated in the PR): an owner token pinned
    # to library A sees machine-level model filenames, including ones only ever
    # used in library B. That is inherent to the hub holding models while tokens
    # are pinned per library; there is no cross-principal leak in a single-owner
    # product, and the attachment names stay per-vault.
    ("GET", "/api/v1/adapters"): RoutePolicy(_OWNER),
    ("GET", "/api/v1/adapters/{sha256}"): RoutePolicy(_OWNER),
    # The one shelf read that is NOT on the owner_only tier. Every other one
    # surfaces host paths but takes none, which is why they stayed there; this
    # one returns the **raw bytes** of a file inside a registered model folder,
    # which is the GET .../runs/{run_name}/samples/{filename} class exactly -
    # reads inside a registered host root, writes nothing, and is a new
    # capability rather than a narrower view of the metadata beside it. It takes
    # no host path at all (a sha256 the scanner already registered is the whole
    # input), so it is on this tier for the authority it exercises, not for what
    # it accepts. Loopback/LAN/Tailscale covers the case the route exists for -
    # a generator on the owner's own network - and a genuinely remote one needs
    # allow_remote_host_ops, which is the safe default direction.
    ("GET", "/api/v1/adapters/{sha256}/file"): RoutePolicy(
        _LOCAL,
        justification="§16.3 serves the raw bytes of a model file out of a registered model folder - the same read-inside-a-registered-root authority as model-folders/{folder_id}/runs/{run_name}/samples/{filename}, and a new capability rather than a subset of GET /adapters, which serves metadata only. Takes no host path: the sha256 addresses a row the scanner wrote, the join is contained, and only a `present` copy is served; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3.1)",
    ),
    ("GET", "/api/v1/checkpoints"): RoutePolicy(_OWNER),
    # The assignment path. OWNER_ONLY, same pin: it WRITES the active vault's
    # adapter_attachment rows, so a token stamped for another library must not
    # reach it. Not LOCAL_OWNER_ONLY - it names a hash and two row ids, never a
    # host path, so it is not the §16.3 filesystem-authority class.
    ("PUT", "/api/v1/adapters/{sha256}/attachments"): RoutePolicy(_OWNER),
    # The verb layer (F3). OWNER_ONLY on the same default library pin as the
    # reads above, and NOT the §16.3 locality tier the folder mutators carry:
    # both take a list of hub `model.id`s and write or delete hub rows, they
    # take no host path, and neither touches the filesystem - Forget drops
    # database rows and never unlinks a file. `PATCH /models` is the shelf's
    # only inline non-authz guard (it refuses a correction the hub's CHECK
    # constraints would reject) and that is a data check, not a scope one.
    ("PATCH", "/api/v1/models"): RoutePolicy(_OWNER),
    ("POST", "/api/v1/models/forget"): RoutePolicy(_OWNER),
    # The shelf's sixth verb, and the one route on this block that spawns a
    # process on the host's desktop. Same authority - and same red-line tier -
    # as POST /pictures/{id}/open-location: what it can do is bounded by what
    # the file manager can do, which is everything the owner's session can, so
    # a LAN or Tailscale caller must never reach it and no flag may say
    # otherwise. It takes no host path (a hub `model.id`, joined to the folder
    # the scanner recorded and contained), which is why the tier is about the
    # spawn rather than the input.
    ("POST", "/api/v1/models/{model_id}/open-location"): RoutePolicy(
        _LOOPBACK,
        justification="§16.3.1 RED LINE: opens a model's folder in the host file manager (open_in_file_manager → os.startfile/open/xdg-open - the same host-GUI spawn as pictures/open-location and reference-folders/open); loopback-only, allow_remote_host_ops can NOT loosen it",
    ),
    # The base-model field's completion list. OWNER_ONLY on the same default
    # pin as the rest of the shelf, and NOT ANY_TOKEN: it returns per-object
    # data - the distinct `base_model` strings recorded on this machine's model
    # rows - even though the shipped labels beside them are a constant.
    ("GET", "/api/v1/models/base-models"): RoutePolicy(_OWNER),
    # ── model_folders.py (shelf plan B5; §16.3 host-capability for the writes)
    # The read is OWNER_ONLY like the rest of the shelf. Every mutator and the
    # rescan take - or walk - a caller-supplied host path, which is the
    # reference-folders class exactly, so they carry the §16.3 locality tier.
    ("GET", "/api/v1/model-folders"): RoutePolicy(_OWNER),
    # OWNER_ONLY, not the §16.3 tier the mutators carry, and the difference is
    # deliberate. It takes no caller-supplied path, reads no file content and
    # walks nothing: it stats the folders that are ALREADY registered, whose
    # paths GET /model-folders returns to this same token. What it adds over
    # that is the mount point (a prefix of a path the caller can already read)
    # and the drive's size. Putting it on LOCAL_OWNER_ONLY would strip the
    # capacity meter from a remote owner for no disclosure that route does not
    # already make, and over-blocking is its own regression.
    ("GET", "/api/v1/model-folders/devices"): RoutePolicy(_OWNER),
    ("POST", "/api/v1/model-folders"): RoutePolicy(
        _LOCAL,
        justification="§16.3 model-folder create; takes a caller-supplied host path, same class as reference-folder create; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3.1)",
    ),
    ("PATCH", "/api/v1/model-folders/{folder_id}"): RoutePolicy(
        _LOCAL,
        justification="§16.3 model-folder update; sets the Docker bind host path; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3.1)",
    ),
    ("DELETE", "/api/v1/model-folders/{folder_id}"): RoutePolicy(
        _LOCAL,
        justification="§16.3 model-folder delete; drops a registered host path and tombstones its location rows; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3.1)",
    ),
    ("POST", "/api/v1/model-folders/{folder_id}/rescan"): RoutePolicy(
        _LOCAL,
        justification="§16.3 walks a registered host path and reads every model file under it - the same authority as reference-folders/detect-sidecars; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3.1)",
    ),
    # ── model_moves.py (shelf plan B7; §16.3 host-capability) ───────────────
    # The strongest filesystem authority the shelf has: this block writes new
    # files into a registered host folder and unlinks files out of another one.
    # The read (GET) is on the same tier rather than OWNER_ONLY because it is
    # the control surface of that operation - how a move is watched, next to the
    # DELETE that stops one - so a caller who may not start a move may not
    # observe or steer one either. It is NOT because the filenames are otherwise
    # unreachable: a remote owner is 200 on GET /adapters, which already serves
    # locations[].folder_path and locations[].relpath for every copy. (The
    # earlier "barred from every route that could produce them" reasoning was
    # false and was corrected in the B7 sign-off.)
    ("POST", "/api/v1/model-moves"): RoutePolicy(
        _LOCAL,
        justification="§16.3 writes model files into a registered host folder and unlinks them from another - strictly more filesystem authority than reference-folders/move-pictures, which is already on this tier; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3.1)",
    ),
    ("GET", "/api/v1/model-moves"): RoutePolicy(
        _LOCAL,
        justification="§16.3 the control surface of an in-flight host-filesystem move - how one is watched, beside the DELETE that stops one - so the tier that alone can start a move is the tier that may observe and steer it; not a secrecy claim about the relpaths, which GET /adapters already serves",
    ),
    ("POST", "/api/v1/model-folders/{folder_id}/relocate"): RoutePolicy(
        _LOCAL,
        justification="§16.3 takes a caller-supplied host path and moves every file a folder PixlStash owns into it, then unlinks the originals - the reference-folders/{folder_id}/relocate class with the file movement of POST /model-moves; the managed store and (since #905) PixlStash's own download folder, whose new location is recorded for every downloader; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3.1)",
    ),
    ("DELETE", "/api/v1/model-moves"): RoutePolicy(
        _LOCAL,
        justification="§16.3 cancels an in-flight host-filesystem move; halting the owner's own file operation is the same authority as starting it; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3.1)",
    ),
    # ── model_icons.py (shelf plan, the sixth verb) ────────────────────────
    # OWNER_ONLY, not the §16.3 locality tier. The icon store lives beside the
    # hub and is written and read by PixlStash alone: no route here takes,
    # walks or serves a caller-supplied host path. The GET serves bytes, but
    # they are bytes PixlStash put there itself under a name it computed, which
    # is categorically not the ai-toolkit sample route's "read inside a folder
    # the owner registered".
    ("POST", "/api/v1/models/{model_id}/icon"): RoutePolicy(
        _OWNER,
        justification="Stores an uploaded image in the hub's own icon store and points the caller's own model at it; no host path taken; POST blocked for READ tokens; owner only",
    ),
    ("GET", "/api/v1/model-icons/{sha256}"): RoutePolicy(
        _OWNER,
        justification="Serves one icon PixlStash itself stored, addressed by the content hash it computed; the path segment is validated as a digest and contained against the icon directory; owner only",
    ),
    ("POST", "/api/v1/models/icons/clear"): RoutePolicy(
        _OWNER,
        justification="Clears the icon column on the caller's own models; writes one hub column and no filesystem; POST blocked for READ tokens; owner only",
    ),
    # ── model_stacks.py (shelf plan F5) ────────────────────────────────────
    # OWNER_ONLY and deliberately NOT the §16.3 locality tier its shelf
    # neighbours sit on: not one of these routes touches the host filesystem.
    # Detection reads `model` rows the scan already wrote, and applying, fusing,
    # unstacking, covering and releasing a member write hub columns, so there is
    # no host path taken, walked, written or unlinked. They surface
    # folder ids, never paths - the same reason the shelf's other read routes
    # stayed owner_only while the folder mutators moved to the locality tier.
    ("GET", "/api/v1/model-stacks/proposals"): RoutePolicy(
        _OWNER,
        justification="Dry run over the caller's own shelf; reads hub rows only, writes nothing, takes no host path; owner only",
    ),
    ("POST", "/api/v1/model-stacks"): RoutePolicy(
        _OWNER,
        justification="Collapses the owner's own models into a stack, optionally fusing stacks it absorbs; writes hub columns only, no filesystem access; POST blocked for READ tokens; owner only",
    ),
    ("DELETE", "/api/v1/model-stacks/{stack_id}"): RoutePolicy(
        _OWNER,
        justification="Breaks up one of the owner's own stacks; clears two hub columns and deletes the adapter_stack row, touching no file on disk; DELETE blocked for READ tokens; owner only",
    ),
    ("PATCH", "/api/v1/model-stacks/{stack_id}/cover"): RoutePolicy(
        _OWNER,
        justification="Chooses which member covers one of the owner's own stacks; renumbers stack_position in the hub, touching no file on disk; PATCH blocked for READ tokens; owner only",
    ),
    ("DELETE", "/api/v1/model-stacks/{stack_id}/members/{model_id}"): RoutePolicy(
        _OWNER,
        justification="Releases one member of one of the owner's own stacks; clears two hub columns and may delete the emptied adapter_stack row, touching no file on disk; DELETE blocked for READ tokens; owner only",
    ),
    # ── model_imports.py (shelf plan B7; §16.3 host-capability) ────────────
    # The listing walks a registered output root; the import writes into one
    # registered folder and may unlink from another. Neither takes a host path:
    # the import names a run *inside* a registered folder and the server joins
    # and contains it.
    ("GET", "/api/v1/model-folders/{folder_id}/runs"): RoutePolicy(
        _LOCAL,
        justification="§16.3 walks a registered ai-toolkit output root and reads every run folder and config under it - the same authority as model-folders/{folder_id}/rescan and reference-folders/detect-sidecars; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3.1)",
    ),
    (
        "GET",
        "/api/v1/model-folders/{folder_id}/runs/{run_name}/samples/{filename}",
    ): RoutePolicy(
        _LOCAL,
        justification="§16.3 reads inside a registered ai-toolkit output root and writes nothing - the same authority class as model-folders/{folder_id}/rescan. NOT a subset of the listing beside it: that returns metadata for names matching the sample regex, this returns raw bytes for any allowlisted extension, which is a new capability rather than a narrower one. Both path segments are names joined and contained against the registered path, the samples directory is contained too (a symlinked one would otherwise become its own safe base), and the extension is allowlisted so nothing but an image can be served from our origin; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3.1)",
    ),
    ("POST", "/api/v1/model-imports"): RoutePolicy(
        _LOCAL,
        justification="§16.3 copies a run's files into a registered host folder and, when the source folder carries delete_after_import, unlinks them from the output root - the same filesystem authority as POST /model-moves; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3.1)",
    ),
    # The imported previews, read back off the shelf. NOT owner_only, and the
    # plan that asked for them said owner_only on the grounds that they are
    # addressed by a row id with no host path crossing the wire. That is exactly
    # the argument the matrix records as **not** the argument: GET
    # /adapters/{sha256}/file takes no host path either and is on this tier,
    # because what decides the tier is the authority exercised - reading bytes
    # out of a folder the owner registered - not what the route accepts. The
    # listing walks one directory inside that folder and reports names of files
    # PixlStash never registered, which is rescan's authority in miniature; the
    # byte route is the sample route beside it with the run replaced by a shelf
    # row. Both are kept on one tier so a caller who cannot fetch a preview is
    # not handed a list of them.
    ("GET", "/api/v1/models/{model_id}/samples"): RoutePolicy(
        _LOCAL,
        justification="§16.3 lists one directory inside a registered model folder, reporting filenames of files PixlStash never registered - rescan's walk-a-registered-root authority, narrowed to one directory; kept on the byte route's tier so a caller who cannot fetch a preview is not handed a list of them; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3.1)",
    ),
    ("GET", "/api/v1/models/{model_id}/samples/{filename}"): RoutePolicy(
        _LOCAL,
        justification="§16.3 serves raw image bytes out of a registered model folder - GET /adapters/{sha256}/file's authority class exactly, and the shelf-side twin of model-folders/{folder_id}/runs/{run_name}/samples/{filename}. Takes no host path (a model.id addresses a row the importer wrote), which per the 2026-08-11 correction is not the argument for owner_only. Two containment joins, not one: the samples directory against the registered folder path, because a symlinked <stem>_samples would otherwise become its own safe base, then the filename against that resolved directory, because a folder-level join alone would let ../alice.safetensors through; the extension is allowlisted so nothing but an image is served from our origin; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3.1)",
    ),
    # ── model_files.py (shelf plan F6, `Add file`; §16.3 host-capability) ──
    # The one shelf route that READS a caller-supplied host path - the loose
    # file it copies is by definition in a folder nobody registered - and it
    # writes into a registered folder. Both halves are already on this tier
    # (POST /model-folders takes a path, POST /model-moves writes files), so it
    # is on it for both. It never unlinks: the source is the owner's own file.
    ("POST", "/api/v1/model-files"): RoutePolicy(
        _LOCAL,
        justification="§16.3 takes a caller-supplied host path, copies that file into a registered host folder and registers it - the POST /model-folders path-taking class carrying the file writing of POST /model-moves, minus the unlink; the read is bounded to one regular .safetensors file and the write is contained against the destination folder; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3.1)",
    ),
    # The unlink half (#933), and the shelf's only destructive verb. Takes no
    # host path - the ids address rows the scanner wrote - but it REMOVES the
    # owner's files, which is the unlink of POST /model-moves without the copy
    # that justifies it, so it sits on the same tier as every other shelf route
    # that writes the host filesystem.
    ("POST", "/api/v1/model-files/delete"): RoutePolicy(
        _LOCAL,
        justification="§16.3 unlinks the owner's model files (OS trash by default, permanent on request) out of registered host folders - the unlink half of POST /model-moves standing alone, and the shelf's only destructive verb. Takes no host path: the ids address rows the scanner wrote, every path is contained against its registered folder - lexically for the file so a symlinked model loses its link and not the bytes it points at, and by realpath for the directory holding it so no symlinked component can redirect the unlink (`_contained_path`) - and only `user` and `managed` folders are eligible, so PixlStash's own engine roots, the InsightFace packs and the shared HuggingFace cache are refused whole; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3.1)",
    ),
    # ── filesystem.py (§16.3 host-capability; Step-3 → LOCAL_OWNER_ONLY) ─────
    ("GET", "/api/v1/filesystem/browse"): RoutePolicy(
        _LOCAL,
        justification="§16.3 host FS browse; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true; discharges CSO §16.3 accepted-risk",
    ),
    ("POST", "/api/v1/filesystem/folders"): RoutePolicy(
        _LOCAL,
        justification="§16.3 host FS mkdir; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3)",
    ),
    # ── folder_structure.py (§16.3 host-capability; v1.11 Phase 2) ──────────
    ("POST", "/api/v1/folder-structure/read"): RoutePolicy(
        _LOCAL,
        justification="§16.3 host FS read: takes a caller-supplied host path and walks it RECURSIVELY, decoding pictures off the disk, so it is GET /filesystem/browse's path-authority class and then some (browse lists one directory) and must not be a second, weaker way to ask what is on the disk. The blocklist runs on the realpath, not the string the caller sent (the same correction GET /libraries/inspect landed), AND again on every directory the walk descends into - a root-only check is a check on one string, and POST {path:'/'} names no restricted directory while walking every one of them. It writes nothing - no row is created, no file is moved or renamed - but what it RETURNS is a map of the owner's folder names and picture counts; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3.1)",
    ),
    ("GET", "/api/v1/folder-structure/read/status"): RoutePolicy(
        _LOCAL,
        justification="§16.3: carries the read's RESULT, which is the folder map itself - the same host information the POST is tiered for, so polling must not be a lower bar than starting; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3.1)",
    ),
    ("DELETE", "/api/v1/folder-structure/read"): RoutePolicy(
        _LOCAL,
        justification="§16.3: cancels the owner's in-flight read - authority over another principal's operation, on the same tier as the route that starts it; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3.1)",
    ),
    # ── folder_structure.py commit (§16.3 host-capability; v1.11 Phase 3) ───
    ("POST", "/api/v1/folder-structure/commit"): RoutePolicy(
        _LOCAL,
        justification="§16.3: commits an accepted mapping over the same host path the read already walked - registers it for in-place indexing (the reference-folders/POST write) and creates the projects/people/sets/tags it names; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3.1)",
    ),
    ("GET", "/api/v1/folder-structure/commit/status"): RoutePolicy(
        _LOCAL,
        justification="§16.3: carries the commit's result, the same host-path class as GET .../read/status; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3.1)",
    ),
    ("DELETE", "/api/v1/folder-structure/commit"): RoutePolicy(
        _LOCAL,
        justification="§16.3: stops the owner's in-flight commit (abort, or 'organise later') - authority over another principal's operation, on the same tier as the route that starts it, exactly as DELETE .../read is to POST .../read; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3.1)",
    ),
    # ── import_folders.py (§16.3 host-capability) ───────────────────────────
    (
        "GET",
        "/api/v1/import-folders",
    ): _LIST_AWARE,  # self-filters to empty for scoped tokens
    ("POST", "/api/v1/import-folders"): RoutePolicy(
        _LOCAL,
        justification="§16.3 import-folder create; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3)",
    ),
    ("PATCH", "/api/v1/import-folders/{folder_id}"): RoutePolicy(
        _LOCAL,
        justification="§16.3 import-folder update; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3)",
    ),
    ("DELETE", "/api/v1/import-folders/{folder_id}"): RoutePolicy(
        _LOCAL,
        justification="§16.3 import-folder delete; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3)",
    ),
    # ── reference_folders.py (§16.3 host-capability) ────────────────────────
    (
        "GET",
        "/api/v1/reference-folders",
    ): _LIST_AWARE,  # self-filters to empty for scoped tokens
    ("GET", "/api/v1/reference-folders/detect-sidecars"): RoutePolicy(
        _LOCAL,
        justification="§16.3 walks host path; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3)",
    ),
    ("POST", "/api/v1/reference-folders"): RoutePolicy(
        _LOCAL,
        justification="§16.3 reference-folder create; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3)",
    ),
    ("PATCH", "/api/v1/reference-folders/{folder_id}"): RoutePolicy(
        _LOCAL,
        justification="§16.3 reference-folder update; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3)",
    ),
    ("POST", "/api/v1/reference-folders/{folder_id}/relocate"): RoutePolicy(
        _LOCAL,
        justification="§16.3 reference-folder relocate; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3)",
    ),
    ("POST", "/api/v1/reference-folders/{folder_id}/move-pictures"): RoutePolicy(
        _LOCAL,
        justification="§16.3 move pictures on host FS; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3)",
    ),
    ("POST", "/api/v1/reference-folders/{folder_id}/metadata/export"): RoutePolicy(
        _LOCAL,
        justification="§16.3 write sidecars to host FS; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3)",
    ),
    ("POST", "/api/v1/reference-folders/{folder_id}/metadata/import"): RoutePolicy(
        _LOCAL,
        justification="§16.3 read sidecars from host FS; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3)",
    ),
    ("DELETE", "/api/v1/reference-folders/{folder_id}"): RoutePolicy(
        _LOCAL,
        justification="§16.3 reference-folder delete; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3)",
    ),
    ("POST", "/api/v1/reference-folders/{folder_id}/open"): RoutePolicy(
        _LOOPBACK,
        justification="§16.3 RED LINE: opens a folder in the host file manager (drives the server's host shell); loopback-only, allow_remote_host_ops can NOT loosen it",
    ),
    ("POST", "/api/v1/server/restart"): RoutePolicy(
        _LOOPBACK,
        justification="§16.3 RED LINE: restarts the server process; loopback-only, allow_remote_host_ops can NOT loosen it",
    ),
    # ── pictures: single-object reads (enforce_picture_scope) → PICTURE_SCOPED
    ("GET", "/api/v1/pictures/{id}.{ext}"): RoutePolicy(_PIC, id_param="id"),
    ("GET", "/api/v1/pictures/{id}/metadata"): RoutePolicy(_PIC, id_param="id"),
    ("GET", "/api/v1/pictures/{id}/character_likeness"): RoutePolicy(
        _PIC, id_param="id"
    ),
    ("GET", "/api/v1/pictures/{id}/detections"): RoutePolicy(_PIC, id_param="id"),
    ("GET", "/api/v1/pictures/{id}/faces"): RoutePolicy(_PIC, id_param="id"),
    ("GET", "/api/v1/pictures/{id}/{field}"): RoutePolicy(_PIC, id_param="id"),
    ("GET", "/api/v1/pictures/{id}/anomaly_region"): RoutePolicy(_PIC, id_param="id"),
    ("GET", "/api/v1/pictures/thumbnails/{id}.webp"): RoutePolicy(_PIC, id_param="id"),
    ("PATCH", "/api/v1/pictures/{id}"): RoutePolicy(_PIC, id_param="id"),
    ("POST", "/api/v1/pictures/{id}/face"): RoutePolicy(_PIC, id_param="id"),
    ("DELETE", "/api/v1/pictures/{id}/face/{index}"): RoutePolicy(_PIC, id_param="id"),
    ("DELETE", "/api/v1/pictures/{id}"): RoutePolicy(_PIC, id_param="id"),
    ("DELETE", "/api/v1/pictures"): RoutePolicy(
        _PIC, body_ids="picture_ids"
    ),  # loops enforce_picture_scope over every id
    # ── pictures: list / search / batch-filter (fetch_scope_allowed) → SCOPED_LIST
    ("GET", "/api/v1/pictures"): _LIST_AWARE,
    ("GET", "/api/v1/pictures/stream"): _LIST_AWARE,
    ("GET", "/api/v1/pictures/count"): _LIST_AWARE,
    ("GET", "/api/v1/pictures/search"): _LIST_AWARE,
    ("GET", "/api/v1/pictures/stats"): _LIST_AWARE,
    ("GET", "/api/v1/pictures/likeness-groups"): _LIST_AWARE,
    ("GET", "/api/v1/pictures/comfyui_models"): _LIST_AWARE,
    ("GET", "/api/v1/pictures/comfyui_loras"): _LIST_AWARE,
    (
        "GET",
        "/api/v1/pictures/export",
    ): _LIST_AWARE,  # generate_zip scope-filters via fetch_scope_allowed
    (
        "POST",
        "/api/v1/pictures/thumbnails",
    ): _LIST_AWARE,  # READ_SAFE; scope-filters ids
    (
        "POST",
        "/api/v1/pictures/tags/bulk_fetch",
    ): _LIST_AWARE,  # READ_SAFE; scope-filters ids
    (
        "POST",
        "/api/v1/pictures/character_likeness/batch",
    ): _LIST_AWARE,  # drops out-of-scope ids via fetch_scope_allowed
    ("POST", "/api/v1/pictures/plugins/{name}"): _LIST_AWARE,
    ("PATCH", "/api/v1/pictures/project"): _LIST_AWARE,
    ("POST", "/api/v1/pictures/apply-scores"): _LIST_AWARE,
    ("POST", "/api/v1/pictures/detect"): _LIST_AWARE,
    (
        "POST",
        "/api/v1/pictures/face-search",
    ): _LIST_AWARE,  # READ_SAFE; scope-filters ids
    (
        "POST",
        "/api/v1/pictures/likeness-search",
    ): _LIST_AWARE,  # READ_SAFE; scope-filters ids
    ("POST", "/api/v1/pictures/impossible-tags/clear"): _LIST_AWARE,
    ("POST", "/api/v1/pictures/impossible-tags/restore"): _LIST_AWARE,
    ("GET", "/api/v1/tags"): _LIST_AWARE,
    # ── pictures: owner-only surfaces ───────────────────────────────────────
    # Retargeted ANY_TOKEN -> OWNER_ONLY on 2026-08-15 (#326), for the reason
    # its tagger sibling was: every field is verbatim text out of a plugin's
    # own class body, half of them from a .py file the owner dropped in, and
    # the only thing the list drives (POST /pictures/plugins/{name}) a READ
    # token cannot call. Leaving it any_token while retargeting GET /taggers
    # would have been the same argument reaching two answers.
    ("GET", "/api/v1/pictures/plugins"): RoutePolicy(
        _OWNER,
        justification="Image plugin list; third-party plugin text, and the run endpoint beside it is owner-only",
    ),
    ("GET", "/api/v1/sort_mechanisms"): RoutePolicy(_ANY),
    # Import status is NOT ANY_TOKEN: the completed payload carries per-object
    # data (``results[].picture_id``, ``results[].file`` the vault-relative
    # filename, and ``scrapheaped_picture_ids``) for pictures anywhere in the
    # vault. ``ANY_TOKEN``'s contract is that a route returns no per-object
    # resource data, so declaring it there handed a resource-scoped share token
    # the ids and filenames of pictures its own thumbnail route refuses. Both
    # routes serve the owner's own import UI (their POST siblings are already
    # OWNER_ONLY, so nobody who cannot start an import has a task to poll), which
    # makes OWNER_ONLY the correct tier and not a narrowing of any live caller.
    ("GET", "/api/v1/pictures/import/status"): RoutePolicy(
        _OWNER,
        justification=(
            "Import job status; the completed payload names imported/duplicate/"
            "scrapheaped picture ids and vault filenames, so it is per-object "
            "owner data, not a progress counter"
        ),
    ),
    ("GET", "/api/v1/pictures/import/staging/{staging_id}/status"): RoutePolicy(
        _OWNER,
        justification=(
            "Async staging import status; same per-object payload as "
            "GET /pictures/import/status (scrapheaped_picture_ids), and its "
            "open/files/commit siblings are already owner only"
        ),
    ),
    ("GET", "/api/v1/pictures/export/status"): RoutePolicy(_ANY),
    ("GET", "/api/v1/pictures/export/download/{task_id}"): RoutePolicy(_ANY),
    ("POST", "/api/v1/pictures/export/folder"): RoutePolicy(
        _LOOPBACK,
        justification="§16.3 RED LINE (#291): writes exported pictures straight onto the host disk and, once done, opens the destination in the host file manager (same host-GUI spawn as pictures/open-location, via pixlstash/utils/host_open.py); loopback-only, allow_remote_host_ops can NOT loosen it",
    ),
    ("POST", "/api/v1/pictures/import"): RoutePolicy(
        _OWNER,
        justification="Import pictures; POST blocked for READ tokens; owner only",
    ),
    # Async streaming-staging import (#459). These stream client-provided upload
    # bytes into the vault and hand off to a background import task - they do NOT
    # read the host filesystem, so OWNER_ONLY is correct (mirrors POST
    # /pictures/import), NOT the §16.3 LOCAL_OWNER_ONLY host-capability tier.
    ("POST", "/api/v1/pictures/import/staging"): RoutePolicy(
        _OWNER,
        justification="Open async import staging session; upload path; owner only",
    ),
    ("POST", "/api/v1/pictures/import/staging/{staging_id}/files"): RoutePolicy(
        _OWNER,
        justification="Stream upload bytes into a staging session; owner only",
    ),
    ("POST", "/api/v1/pictures/import/staging/{staging_id}/commit"): RoutePolicy(
        _OWNER,
        justification="Hand staging off to the background import task; owner only",
    ),
    ("DELETE", "/api/v1/pictures/import/staging/{staging_id}"): RoutePolicy(
        _OWNER,
        justification="Cancel an uncommitted staging session; owner only",
    ),
    ("POST", "/api/v1/pictures/score_character_likeness"): RoutePolicy(
        _OWNER, justification="Owner scoring op; POST not in READ_SAFE; owner only"
    ),
    ("POST", "/api/v1/pictures/{id}/open-location"): RoutePolicy(
        _LOOPBACK,
        justification="§16.3 RED LINE: opens the file location in the host file manager (drives the server's host shell); loopback-only, allow_remote_host_ops can NOT loosen it",
    ),
    ("POST", "/api/v1/pictures/scrapheap/restore"): RoutePolicy(
        _OWNER, justification="require_unscoped_owner"
    ),
    ("DELETE", "/api/v1/pictures/scrapheap"): RoutePolicy(
        _OWNER, justification="require_unscoped_owner"
    ),
    ("POST", "/api/v1/pictures/scrapheap/delete-preview"): RoutePolicy(
        _OWNER,
        justification="Returns protected reference-original file paths; owner only",
    ),
    # PICTURE_SCOPED, like every other per-picture mutation on this surface, and
    # shaped exactly like ("DELETE", "/api/v1/pictures") above. The in-place
    # write is a metadata-only EXIF-orientation splice: the entropy-coded pixel
    # stream is copied through byte for byte, the whole prior state is one
    # enumerated value 1–8, so the operation is exactly reversible by the
    # ordinary undo machinery (§21.5) - and a file on a reference folder is
    # refused at the sink and reported ``unsupported`` rather than rewritten.
    # None of that is a destructive edit of someone else's original, so a
    # write-enabled grant that already reaches the picture is the right level.
    # READ tokens never arrive here at all: the auth middleware refuses a
    # non-GET from a READ token unless the path is in READ_SAFE_POST_PATHS, and
    # this one deliberately is not - that is what makes "write-enabled" the
    # operative condition and leaves the gate to answer "reaches this picture".
    # The gate resolves body_ids element by element and raises on the first id
    # out of scope, before the handler runs, so a mixed batch is refused whole
    # and rotates nothing. (#950)
    # v1.11 Phase 4b. Picture-scoped rather than §16.3 local, and the line is
    # the same one rotate sits on: the caller supplies no host path. It names
    # pictures, and the server computes both the root and the destination from
    # a layout only the LOCAL tier could have set. What moves is a file the
    # library already manages, inside the root it already lives in, and the
    # whole batch is one undo on the operation log.
    ("GET", "/api/v1/pictures/{id}/layout"): RoutePolicy(_PIC, id_param="id"),
    ("POST", "/api/v1/pictures/layout/move-to-match"): RoutePolicy(
        _PIC,
        body_ids="picture_ids",
        justification=(
            "Moves pictures the caller names to the folder this library's own "
            "layout renders for them - no caller-supplied path, no root the "
            "caller chose, and nothing outside the library root (a source that "
            "resolves outside it, or is a symlink, is refused at the planner). "
            "Write-enabled picture-scoped grant, the POST /pictures/rotate "
            "class. READ tokens are refused earlier by the middleware: POST is "
            "not in READ_SAFE. Gate loops enforce_picture_scope over every id."
        ),
    ),
    ("POST", "/api/v1/pictures/rotate"): RoutePolicy(
        _PIC,
        body_ids="picture_ids",
        justification="In-place rotate splices only the EXIF orientation (pixels byte-identical, exactly reversible, reference-folder files refused at the sink), so a write-enabled picture-scoped grant is the right level. READ tokens are refused earlier by the middleware: POST not in READ_SAFE. Gate loops enforce_picture_scope over every id",
    ),
    # ── tags.py: single-picture tag mutations (enforce_picture_scope) ────────
    ("POST", "/api/v1/pictures/{id}/tags"): RoutePolicy(_PIC, id_param="id"),
    ("GET", "/api/v1/pictures/{id}/tags"): RoutePolicy(_PIC, id_param="id"),
    ("DELETE", "/api/v1/pictures/{id}/tags/{tag_id}"): RoutePolicy(_PIC, id_param="id"),
    ("POST", "/api/v1/pictures/{id}/tags/remove_all"): RoutePolicy(_PIC, id_param="id"),
    ("DELETE", "/api/v1/pictures/{id}/tags"): RoutePolicy(_PIC, id_param="id"),
    # ── tag_predictions.py: #504 mutators (enforce_picture_scope) ────────────
    ("GET", "/api/v1/pictures/{id}/tag_predictions"): RoutePolicy(_PIC, id_param="id"),
    ("POST", "/api/v1/pictures/{id}/tag_predictions/{tag}/confirm"): RoutePolicy(
        _PIC, id_param="id"
    ),
    ("POST", "/api/v1/pictures/{id}/tag_predictions/{tag}/reject"): RoutePolicy(
        _PIC, id_param="id"
    ),
    ("POST", "/api/v1/pictures/{id}/tag_predictions/delete"): RoutePolicy(
        _PIC, id_param="id"
    ),
    ("POST", "/api/v1/pictures/{id}/reset_tags"): RoutePolicy(_PIC, id_param="id"),
    ("POST", "/api/v1/pictures/{id}/reset_description"): RoutePolicy(
        _PIC, id_param="id"
    ),
    ("POST", "/api/v1/pictures/reset_tags"): RoutePolicy(_PIC, body_ids="picture_ids"),
    ("POST", "/api/v1/pictures/reset_description"): RoutePolicy(
        _PIC, body_ids="picture_ids"
    ),
    ("GET", "/api/v1/tagger/label-thresholds"): RoutePolicy(_ANY),
    # ── stacks.py ───────────────────────────────────────────────────────────
    (
        "GET",
        "/api/v1/stacks/{stack_id}",
    ): _LIST_AWARE,  # returns pictures filtered by fetch_scope_allowed
    ("GET", "/api/v1/stacks/{stack_id}/pictures"): _LIST_AWARE,
    ("GET", "/api/v1/pictures/{picture_id}/stack"): _LIST_AWARE,
    ("POST", "/api/v1/stacks"): RoutePolicy(
        _OWNER, justification="Create stack; POST blocked for READ tokens; owner only"
    ),
    ("PATCH", "/api/v1/stacks/{stack_id}/order"): RoutePolicy(
        _OWNER, justification="Reorder stack; PATCH blocked for READ tokens; owner only"
    ),
    ("POST", "/api/v1/stacks/{stack_id}/members"): RoutePolicy(
        _OWNER,
        justification="Add stack members; POST blocked for READ tokens; owner only",
    ),
    ("DELETE", "/api/v1/stacks/{stack_id}/members"): RoutePolicy(
        _OWNER,
        justification="Remove stack members; DELETE blocked for READ tokens; owner only",
    ),
    ("PATCH", "/api/v1/stacks/{stack_id}/members/{picture_id}"): RoutePolicy(
        _OWNER,
        justification="Set member position; PATCH blocked for READ tokens; owner only",
    ),
    # ── stacks.py: Keep cover only (docs/design/keep-cover-only.md) ─────────
    ("POST", "/api/v1/stacks/keep-cover-only/preview"): RoutePolicy(
        _OWNER,
        justification=(
            "Dry run over stacks named only by stack or picture id, which can "
            "reach any stack in the vault. It returns per-stack membership, the "
            "names of the locked picture sets freezing a stack and of the "
            "characters a collapse would strand, plus a byte total, none of "
            "which can be narrowed to a share token's scope without either "
            "leaking that out-of-scope members exist or reporting counts "
            "measured over a subset, which would be wrong numbers rather than "
            "narrower ones (the same reasoning as GET /dedup/mixed-stacks). "
            "POST is also blocked for READ tokens"
        ),
    ),
    ("POST", "/api/v1/stacks/keep-cover-only"): RoutePolicy(
        _OWNER,
        justification=(
            "Soft-deletes stack members to the Scrapheap and writes the "
            "metadata union (tags, score, pending character) onto their covers, "
            "across pictures named only by a stack or picture id. Stacks are "
            "set-membership-atomic, so this also changes what collections "
            "effectively contain. Same reasoning as POST /dedup/verdicts/stack "
            "and the mixed-stack mutations; POST is also blocked for READ tokens"
        ),
    ),
    # ── dedup.py (near-duplicate sweep, dry run) ────────────────────────────
    ("GET", "/api/v1/dedup/sweep/policy"): RoutePolicy(
        _OWNER,
        justification=(
            "Sweep policy defaults/bounds for the vault-wide sweep; an "
            "owner-only operator surface returning no per-object data"
        ),
    ),
    ("POST", "/api/v1/dedup/sweep/dry-run"): RoutePolicy(
        _OWNER,
        justification=(
            "Vault-wide near-duplicate plan: counts and picture ids across the "
            "whole library, which cannot be narrowed to a share token's scope "
            "without leaking counts about out-of-scope pictures (same reasoning "
            "as tag_health). POST is also blocked for READ tokens"
        ),
    ),
    # ── dedup.py (v1.9 tiered duplicate queue) ──────────────────────────────
    # Every route on this surface is OWNER_ONLY for one of two reasons, both of
    # which are the tag-health reasoning: a vault-wide aggregate cannot be
    # narrowed to a share token's scope without leaking the existence and count
    # of out-of-scope pictures, and a verdict mutates stacks across arbitrary
    # pictures that a scoped token has no business touching.
    ("GET", "/api/v1/dedup/policy"): RoutePolicy(
        _OWNER,
        justification=(
            "Tier defaults/bounds for the duplicate queue; an owner-only "
            "operator surface returning no per-object data"
        ),
    ),
    ("GET", "/api/v1/dedup/groups"): RoutePolicy(
        _OWNER,
        justification=(
            "Returns duplicate groups with picture ids, dimensions and (for "
            "reference-folder pictures) file paths from anywhere in the vault. "
            "A group is defined by content identity, not by collection "
            "membership, so it routinely spans a share token's scope boundary "
            "and cannot be narrowed without leaking that out-of-scope copies "
            "exist"
        ),
    ),
    ("GET", "/api/v1/dedup/stacks/{stack_id}/members"): RoutePolicy(
        _OWNER,
        justification=(
            "The Duplicates queue's deck expansion: returns every live member "
            "of one existing stack with the same per-picture fields the queue "
            "row carries. It is the lazy half of GET /dedup/groups and is "
            "owner-only for the same reason: a stack folded into a duplicate "
            "group is defined by content identity, not by collection "
            "membership, so its members routinely straddle a share token's "
            "scope boundary and narrowing the list would leak that out-of-scope "
            "siblings exist. Deliberately NOT scoped like GET /stacks/"
            "{stack_id}/pictures, which self-filters for a scoped token: this "
            "surface must report the stack's TRUE depth (that is the whole "
            "point of the deck), and a filtered depth would be a wrong number "
            "rather than a narrower one"
        ),
    ),
    ("POST", "/api/v1/dedup/counts"): RoutePolicy(
        _OWNER,
        justification=(
            "Vault-wide and per-scope duplicate counts. Read-only but POST "
            "because the scope list does not fit a URL; POST is also blocked "
            "for READ tokens. Counts describe pictures outside any token's "
            "scope (same reasoning as tag_health)"
        ),
    ),
    ("POST", "/api/v1/dedup/scan"): RoutePolicy(
        _OWNER,
        justification=(
            "Queues a background scan over the whole vault or a chosen scope; "
            "an owner-only maintenance trigger. POST is also blocked for READ "
            "tokens"
        ),
    ),
    ("POST", "/api/v1/dedup/verdicts/stack"): RoutePolicy(
        _OWNER,
        justification=(
            "Mutates stack membership, tags, project/set membership and scores "
            "across pictures identified only by a content signature, which can "
            "name any picture in the vault. POST is also blocked for READ tokens"
        ),
    ),
    ("POST", "/api/v1/dedup/verdicts/keep-separate"): RoutePolicy(
        _OWNER,
        justification=(
            "Writes a permanent verdict about an arbitrary set of vault "
            "pictures. POST is also blocked for READ tokens"
        ),
    ),
    ("POST", "/api/v1/dedup/verdicts/batch"): RoutePolicy(
        _OWNER,
        justification=(
            "Atomically applies several stack or keep-separate verdicts over "
            "arbitrary vault pictures; same owner-only boundary as the single "
            "verdict routes. POST is also blocked for READ tokens"
        ),
    ),
    ("POST", "/api/v1/dedup/verdicts/reopen"): RoutePolicy(
        _OWNER,
        justification=(
            "Reverses a stored verdict about an arbitrary set of vault "
            "pictures. POST is also blocked for READ tokens"
        ),
    ),
    ("POST", "/api/v1/dedup/auto-stack"): RoutePolicy(
        _OWNER,
        justification=(
            "Bulk stacking across the whole vault under one undo batch; the "
            "most far-reaching mutation on this surface. POST is also blocked "
            "for READ tokens"
        ),
    ),
    # ── dedup.py (v1.9 Mixed stacks, design D5/B5) ──────────────────────────
    # The same two reasons as the rest of this surface. The list is a
    # vault-wide aggregate over every live stack, and the three actions mutate
    # stack membership on pictures named only by a stack id.
    ("GET", "/api/v1/dedup/mixed-stacks"): RoutePolicy(
        _OWNER,
        justification=(
            "Enumerates every live stack in the vault that is not one cluster, "
            "with its member picture ids. Cohesion is a fact about the whole "
            "stack, so the list cannot be narrowed to a share token's scope "
            "without either leaking that out-of-scope members exist or "
            "reporting a component count measured over a subset, a wrong "
            "number rather than a narrower one, the same reasoning that makes "
            "GET /dedup/stacks/{stack_id}/members owner-only"
        ),
    ),
    ("POST", "/api/v1/dedup/mixed-stacks/{stack_id}/split"): RoutePolicy(
        _OWNER,
        justification=(
            "Removes pictures from a stack anywhere in the vault, identified "
            "only by a stack id; stacks are set-membership-atomic, so this "
            "changes what collections effectively contain. POST is also "
            "blocked for READ tokens"
        ),
    ),
    ("POST", "/api/v1/dedup/mixed-stacks/{stack_id}/unstack"): RoutePolicy(
        _OWNER,
        justification=(
            "Dissolves a stack anywhere in the vault, freeing every member. "
            "Same reasoning as the split, at the whole-stack scale. POST is "
            "also blocked for READ tokens"
        ),
    ),
    ("POST", "/api/v1/dedup/mixed-stacks/{stack_id}/keep"): RoutePolicy(
        _OWNER,
        justification=(
            "Writes a durable dismissal against a stack anywhere in the vault. "
            "Changes no picture, but it is owner state on an owner-only "
            "surface and a scoped token has no listing to suppress. POST is "
            "also blocked for READ tokens"
        ),
    ),
    ("DELETE", "/api/v1/dedup/mixed-stacks/{stack_id}/keep"): RoutePolicy(
        _OWNER,
        justification=(
            "Clears the dismissal above; owner-only for the same reason. "
            "DELETE is also blocked for READ tokens"
        ),
    ),
    # ── characters.py ───────────────────────────────────────────────────────
    ("GET", "/api/v1/characters"): _LIST_AWARE,
    ("GET", "/api/v1/characters/{id}"): RoutePolicy(_CHAR, id_param="id"),
    ("GET", "/api/v1/characters/{id}/summary"): RoutePolicy(_CHAR, id_param="id"),
    ("GET", "/api/v1/characters/{id}/reference_pictures"): RoutePolicy(
        _CHAR, id_param="id"
    ),
    ("GET", "/api/v1/characters/{id}/faces"): RoutePolicy(_CHAR, id_param="id"),
    ("GET", "/api/v1/characters/{id}/{field}"): RoutePolicy(_CHAR, id_param="id"),
    ("GET", "/api/v1/projects/{project_name}/characters/{character_name}"): RoutePolicy(
        _CHAR,
        id_param="character_name",
        resolved_inline=True,
        justification=(
            "§N3 name-derived id: (project_name, character_name) -> character id. "
            "The gate cannot resolve name->id without duplicating the handler's "
            "lookup (divergence risk, D2); the inline _require_scope_allows_character "
            "check remains the live enforcement until a shared name->id resolver "
            "exists - do not remove it in Step 5 before then. The {project_name} "
            "half is enforced inline too, by enforce_project_path_scope, which the "
            "query-param chokepoint cannot see (#708 condition 2, §16.6)."
        ),
    ),
    (
        "POST",
        "/api/v1/characters/membership",
    ): _LIST_AWARE,  # READ_SAFE; scope-filters ids
    (
        "POST",
        "/api/v1/characters/likeness-search",
    ): _LIST_AWARE,  # READ_SAFE; fetch_scope_allowed_character_ids
    ("POST", "/api/v1/characters"): RoutePolicy(
        _OWNER,
        justification="Create character; POST blocked for READ tokens; owner only",
    ),
    ("PATCH", "/api/v1/characters/{id}"): RoutePolicy(
        _OWNER,
        justification="Update character; PATCH blocked for READ tokens; owner only",
    ),
    ("DELETE", "/api/v1/characters/{id}"): RoutePolicy(
        _OWNER,
        justification="Delete character; DELETE blocked for READ tokens; owner only",
    ),
    ("POST", "/api/v1/characters/{character_id}/faces"): RoutePolicy(
        _OWNER, justification="Assign face; POST blocked for READ tokens; owner only"
    ),
    ("DELETE", "/api/v1/characters/{character_id}/faces"): RoutePolicy(
        _OWNER, justification="Remove faces; DELETE blocked for READ tokens; owner only"
    ),
    # ── picture_sets.py ─────────────────────────────────────────────────────
    ("GET", "/api/v1/picture_sets"): _LIST_AWARE,
    ("GET", "/api/v1/picture_sets/locked-members"): _LIST_AWARE,
    ("GET", "/api/v1/picture_sets/{id}"): RoutePolicy(_SET, id_param="id"),
    ("GET", "/api/v1/picture_sets/{id}/thumbnail"): RoutePolicy(_SET, id_param="id"),
    ("GET", "/api/v1/picture_sets/{id}/members"): RoutePolicy(_SET, id_param="id"),
    (
        "GET",
        "/api/v1/projects/{project_name}/picture_sets/{picture_set_name}",
    ): RoutePolicy(
        _SET,
        id_param="picture_set_name",
        resolved_inline=True,
        justification=(
            "§N3 name-derived id: (project_name, picture_set_name) -> set id. The "
            "gate cannot resolve name->id without duplicating the handler's lookup "
            "(divergence risk, D2); the inline _require_scope_allows_picture_set "
            "check remains the live enforcement until a shared name->id resolver "
            "exists - do not remove it in Step 5 before then. The {project_name} "
            "half is enforced inline too, by enforce_project_path_scope, which the "
            "query-param chokepoint cannot see (#708 condition 2, §16.6)."
        ),
    ),
    (
        "POST",
        "/api/v1/picture_sets/membership",
    ): _LIST_AWARE,  # READ_SAFE; scope-filters ids
    ("POST", "/api/v1/picture_sets"): RoutePolicy(
        _OWNER, justification="Create set; POST blocked for READ tokens; owner only"
    ),
    ("PATCH", "/api/v1/picture_sets/{id}"): RoutePolicy(
        _OWNER, justification="Update set; PATCH blocked for READ tokens; owner only"
    ),
    ("DELETE", "/api/v1/picture_sets/{id}"): RoutePolicy(
        _OWNER, justification="Delete set; DELETE blocked for READ tokens; owner only"
    ),
    ("POST", "/api/v1/picture_sets/{id}/members/{picture_id}"): RoutePolicy(
        _OWNER, justification="Add set member; POST blocked for READ tokens; owner only"
    ),
    ("DELETE", "/api/v1/picture_sets/{id}/members/{picture_id}"): RoutePolicy(
        _OWNER,
        justification="Remove set member; DELETE blocked for READ tokens; owner only",
    ),
    ("POST", "/api/v1/picture_sets/{id}/members"): RoutePolicy(
        _OWNER,
        justification="Bulk add set members; POST blocked for READ tokens; owner only",
    ),
    ("PUT", "/api/v1/picture_sets/{id}/members"): RoutePolicy(
        _OWNER,
        justification="Bulk replace set members; PUT blocked for READ tokens; owner only",
    ),
    # ── projects.py ─────────────────────────────────────────────────────────
    ("GET", "/api/v1/projects"): _LIST_AWARE,
    ("GET", "/api/v1/projects/{id_or_name}"): RoutePolicy(
        _PROJ,
        id_param="id_or_name",
        resolved_inline=True,
        justification=(
            "§N3 id-or-name: {id_or_name} may be a numeric id OR a project name. "
            "The gate cannot resolve it without duplicating the handler's "
            "int-or-name lookup (divergence risk, D2); the inline check remains the "
            "live enforcement until a shared resolver exists - do not remove it in "
            "Step 5 before then. It is enforce_project_path_scope, NOT "
            "_require_scope_allows_project: the refusal must be identical whether "
            "the project exists or not, or the route is an existence oracle "
            "(#708 condition 2, §16.6)."
        ),
    ),
    ("GET", "/api/v1/projects/{id_or_name}/picture_sets"): RoutePolicy(
        _PROJ,
        id_param="id_or_name",
        resolved_inline=True,
        justification=(
            "§N3 id-or-name: {id_or_name} may be a numeric id OR a project name. "
            "The gate cannot resolve it without duplicating the handler's "
            "int-or-name lookup (divergence risk, D2); the inline check remains the "
            "live enforcement until a shared resolver exists - do not remove it in "
            "Step 5 before then. It is enforce_project_path_scope, NOT "
            "_require_scope_allows_project: the refusal must be identical whether "
            "the project exists or not, or the route is an existence oracle "
            "(#708 condition 2, §16.6)."
        ),
    ),
    ("GET", "/api/v1/projects/{project_id}/summary"): RoutePolicy(
        _PROJ, id_param="project_id"
    ),
    ("GET", "/api/v1/projects/{project_id}/export"): RoutePolicy(
        _PROJ, id_param="project_id"
    ),
    ("GET", "/api/v1/projects/{project_id}/attachments"): RoutePolicy(
        _PROJ, id_param="project_id"
    ),
    ("GET", "/api/v1/projects/{project_id}/attachments/{attachment_id}"): RoutePolicy(
        _PROJ, id_param="project_id"
    ),
    (
        "POST",
        "/api/v1/projects/membership",
    ): _LIST_AWARE,  # READ_SAFE; scope-filters ids
    ("POST", "/api/v1/projects"): RoutePolicy(
        _OWNER, justification="Create project; POST blocked for READ tokens; owner only"
    ),
    ("PUT", "/api/v1/projects/{project_id}"): RoutePolicy(
        _OWNER, justification="Update project; PUT blocked for READ tokens; owner only"
    ),
    ("DELETE", "/api/v1/projects/{project_id}"): RoutePolicy(
        _OWNER,
        justification="Delete project; DELETE blocked for READ tokens; owner only",
    ),
    ("POST", "/api/v1/projects/{project_id}/attachments"): RoutePolicy(
        _OWNER,
        justification="Upload attachment; POST blocked for READ tokens; owner only",
    ),
    ("POST", "/api/v1/projects/{project_id}/attachments/url"): RoutePolicy(
        _OWNER,
        justification="Add URL attachment; POST blocked for READ tokens; owner only",
    ),
    (
        "DELETE",
        "/api/v1/projects/{project_id}/attachments/{attachment_id}",
    ): RoutePolicy(
        _OWNER,
        justification="Delete attachment; DELETE blocked for READ tokens; owner only",
    ),
    # ── guest_scores.py (share-token guest scoring; READ_SAFE) ──────────────
    ("GET", "/api/v1/pictures/guest-scores"): _LIST_AWARE,
    (
        "DELETE",
        "/api/v1/pictures/guest-scores/session",
    ): _LIST_AWARE,  # READ_SAFE; scope + guest session
    (
        "POST",
        "/api/v1/pictures/guest-scores",
    ): _LIST_AWARE,  # READ_SAFE; scope-filters ids
    # ── comfyui.py ──────────────────────────────────────────────────────────
    ("GET", "/api/v1/comfyui/workflows"): RoutePolicy(_ANY),
    ("DELETE", "/api/v1/comfyui/workflows/{workflow_name}"): RoutePolicy(
        _OWNER,
        justification="Delete workflow; DELETE blocked for READ tokens; owner only",
    ),
    ("POST", "/api/v1/comfyui/abort"): RoutePolicy(
        _OWNER,
        justification="Abort generation; POST blocked for READ tokens; owner only",
    ),
    ("POST", "/api/v1/comfyui/workflows/import"): RoutePolicy(
        _OWNER,
        justification="Import workflow; POST blocked for READ tokens; owner only",
    ),
    ("POST", "/api/v1/comfyui/run_i2i"): RoutePolicy(
        _PIC, body_ids="picture_ids"
    ),  # loops enforce_picture_scope over body picture_ids
    ("POST", "/api/v1/comfyui/run_t2i"): RoutePolicy(
        _PIC, body_ids="source_picture_id"
    ),  # NEEDS REVIEW: single optional body id, enforce_picture_scope only when present
    ("GET", "/api/v1/comfyui/pictures/{picture_id}/workflow"): RoutePolicy(
        _PIC, id_param="picture_id"
    ),
    ("GET", "/api/v1/comfyui/pictures/{picture_id}/recipe"): RoutePolicy(
        _PIC, id_param="picture_id"
    ),
    ("POST", "/api/v1/comfyui/run_recipe"): RoutePolicy(
        _PIC, body_ids="picture_id"
    ),  # required single body id; re-extracts the graph from the scoped picture
    # ── snapshots.py (all require_unscoped_owner) ───────────────────────────
    ("GET", "/api/v1/snapshots"): RoutePolicy(
        _OWNER, justification="require_unscoped_owner"
    ),
    ("GET", "/api/v1/snapshots/status"): RoutePolicy(
        _OWNER, justification="require_unscoped_owner"
    ),
    ("POST", "/api/v1/snapshots"): RoutePolicy(
        _OWNER, justification="require_unscoped_owner"
    ),
    ("PATCH", "/api/v1/snapshots/{snapshot_id}"): RoutePolicy(
        _OWNER, justification="require_unscoped_owner"
    ),
    ("DELETE", "/api/v1/snapshots/{snapshot_id}"): RoutePolicy(
        _OWNER, justification="require_unscoped_owner"
    ),
    ("GET", "/api/v1/snapshots/{snapshot_id}/restore/preview"): RoutePolicy(
        _OWNER, justification="require_unscoped_owner"
    ),
    (
        "GET",
        "/api/v1/snapshots/{snapshot_id}/restore/{resource_type}/{resource_id}/preview",
    ): RoutePolicy(_OWNER, justification="require_unscoped_owner"),
    ("POST", "/api/v1/snapshots/{snapshot_id}/restore/preview/batch"): RoutePolicy(
        _OWNER, justification="require_unscoped_owner"
    ),
    ("POST", "/api/v1/snapshots/{snapshot_id}/restore"): RoutePolicy(
        _OWNER, justification="require_unscoped_owner"
    ),
    ("POST", "/api/v1/snapshots/{snapshot_id}/restore/batch"): RoutePolicy(
        _OWNER, justification="require_unscoped_owner"
    ),
    ("POST", "/api/v1/snapshots/{snapshot_id}/hash-compare"): RoutePolicy(
        _OWNER, justification="require_unscoped_owner"
    ),
    (
        "POST",
        "/api/v1/snapshots/{snapshot_id}/restore/{resource_type}/{resource_id}",
    ): RoutePolicy(_OWNER, justification="require_unscoped_owner"),
    # ── reviews.py (bespoke "reject resource-scoped" gate; owner surface) ────
    # NEEDS REVIEW: the inline _token_scope_ids gate also admits an unscoped-READ
    # token (owner-equivalent read-all); OWNER_ONLY would newly deny that at the
    # Step-3 flip. Confirm no unscoped-READ token is minted/relied on before Step 3.
    ("POST", "/api/v1/reviews"): RoutePolicy(
        _OWNER,
        justification="Owner-only review surface (inline rejects scoped tokens); write",
    ),
    ("GET", "/api/v1/reviews"): RoutePolicy(
        _OWNER, justification="Owner-only review queue (inline rejects scoped tokens)"
    ),
    ("DELETE", "/api/v1/reviews"): RoutePolicy(
        _OWNER, justification="Owner-only review surface; write"
    ),
    ("GET", "/api/v1/reviews/preview"): RoutePolicy(
        _OWNER, justification="Owner-only review preview (inline rejects scoped tokens)"
    ),
    ("GET", "/api/v1/reviews/{review_id}"): RoutePolicy(
        _OWNER, justification="Owner-only review read (inline rejects scoped tokens)"
    ),
    ("DELETE", "/api/v1/reviews/{review_id}"): RoutePolicy(
        _OWNER, justification="Owner-only review surface; write"
    ),
    ("POST", "/api/v1/reviews/{review_id}/refresh"): RoutePolicy(
        _OWNER, justification="Owner-only review surface; write"
    ),
    ("POST", "/api/v1/reviews/{review_id}/archive"): RoutePolicy(
        _OWNER, justification="Owner-only review surface; write"
    ),
    ("POST", "/api/v1/reviews/{review_id}/abort"): RoutePolicy(
        _OWNER, justification="Owner-only review surface; write"
    ),
    ("GET", "/api/v1/reviews/{review_id}/suggestions"): RoutePolicy(
        _OWNER, justification="Owner-only review read (inline rejects scoped tokens)"
    ),
    # ── operations.py (the append-only operation log: history + undo/redo) ──
    # Vault-wide change history and the undo/redo stack. OWNER_ONLY across the
    # board: the log enumerates every change to the whole library (a scoped
    # share token must not read it), and undo/redo write metadata back onto
    # arbitrary pictures across the vault, which no scoped grant can bound. The
    # handlers carry NO authz code - the gate is the only enforcement (§16.1).
    ("GET", "/api/v1/operations"): RoutePolicy(
        _OWNER, justification="Vault-wide change history; owner-only read"
    ),
    ("GET", "/api/v1/operations/undo-state"): RoutePolicy(
        _OWNER, justification="Vault-wide undo/redo availability; owner-only read"
    ),
    ("GET", "/api/v1/operations/{operation_id}"): RoutePolicy(
        _OWNER,
        justification=(
            "One operation incl. the recorded before/after metadata of its "
            "targets (arbitrary vault pictures); owner-only read"
        ),
    ),
    ("POST", "/api/v1/operations/undo"): RoutePolicy(
        _OWNER, justification="Reverts metadata across the vault; owner-only write"
    ),
    ("POST", "/api/v1/operations/redo"): RoutePolicy(
        _OWNER, justification="Re-applies metadata across the vault; owner-only write"
    ),
    ("POST", "/api/v1/operations/{operation_id}/undo"): RoutePolicy(
        _OWNER, justification="Reverts metadata across the vault; owner-only write"
    ),
    ("POST", "/api/v1/operations/batches/{batch_id}/undo"): RoutePolicy(
        _OWNER,
        justification="Reverts a whole bulk action across the vault; owner-only write",
    ),
    # ── insights.py (v1.11 "About your library"; read-only findings) ────────
    ("GET", "/api/v1/insights"): RoutePolicy(
        _OWNER,
        justification=(
            "Vault-wide findings: folder names and picture counts from anywhere "
            "in the library, plus the absolute path of the folder each finding "
            "points at. Same reasoning as tag_health and the dedup queue - the "
            "numbers ARE the aggregate, so narrowing them to a share token's "
            "scope would either leak the existence of out-of-scope pictures or "
            "report a wrong total. Reads only; queues no work and writes no row"
        ),
    ),
    # ── moves.py (v1.11 Phase 5, reconciling moves made outside PixlStash) ───
    # Vault-wide, like operations.py: the queue enumerates moves across the
    # whole library and apply/dismiss write project/set/person membership onto
    # arbitrary pictures, none of it boundable to a single resource-scoped
    # grant.
    ("GET", "/api/v1/moves/pending"): RoutePolicy(
        _OWNER,
        justification="Vault-wide reconciliation queue; owner-only read",
    ),
    ("POST", "/api/v1/moves/apply"): RoutePolicy(
        _OWNER,
        justification="Writes project/set/person membership across the vault; owner-only write",
    ),
    ("POST", "/api/v1/moves/dismiss"): RoutePolicy(
        _OWNER,
        justification="Clears rows from the vault-wide reconciliation queue; owner-only write",
    ),
    # ── tag_health.py (bespoke "reject resource-scoped" gate; owner-only) ────
    # Same unscoped-READ nuance as reviews (see NEEDS REVIEW above).
    ("GET", "/api/v1/tag_health"): RoutePolicy(
        _OWNER,
        justification="Vault-wide aggregates; inline _reject_scoped_tokens; owner/full only",
    ),
    ("POST", "/api/v1/tag_health/rebuild"): RoutePolicy(
        _OWNER,
        justification="Vault-wide rebuild; inline _reject_scoped_tokens; owner/full only",
    ),
    # ── tag_suggestions.py ──────────────────────────────────────────────────
    ("GET", "/api/v1/tag_suggestions"): _LIST_AWARE,
    (
        "POST",
        "/api/v1/tag_suggestions/bulk-accept",
    ): _LIST_AWARE,  # _resolve_review_picture_ids scope-filters
    ("POST", "/api/v1/tag_suggestions/scan"): RoutePolicy(
        _OWNER,
        justification="Rebuild suggestions for a tag; POST blocked for READ tokens; owner only",
    ),
    # Carry-forward (F2): single-item mutators shipped without enforce_picture_scope;
    # plan mandates PICTURE_SCOPED. Today reachable only by owner (POST blocked for
    # READ tokens). §N4: the path id is a suggestion_id, so the gate uses the
    # ``tag_suggestion`` id_resolver (TagSuggestion.picture_id) to reach the picture
    # before the membership check. Latent end-state - a scoped token cannot reach
    # these POSTs today (not in READ_SAFE_POST_PATHS).
    ("POST", "/api/v1/tag_suggestions/{suggestion_id}/accept"): RoutePolicy(
        _PIC, id_param="suggestion_id", id_resolver="tag_suggestion"
    ),
    ("POST", "/api/v1/tag_suggestions/{suggestion_id}/reopen"): RoutePolicy(
        _PIC, id_param="suggestion_id", id_resolver="tag_suggestion"
    ),
    ("POST", "/api/v1/tag_suggestions/{suggestion_id}/fix-twin"): RoutePolicy(
        _PIC, id_param="suggestion_id", id_resolver="tag_suggestion"
    ),
    ("POST", "/api/v1/tag_suggestions/{suggestion_id}/swap"): RoutePolicy(
        _PIC, id_param="suggestion_id", id_resolver="tag_suggestion"
    ),
    ("POST", "/api/v1/tag_suggestions/{suggestion_id}/skip"): RoutePolicy(
        _PIC, id_param="suggestion_id", id_resolver="tag_suggestion"
    ),
    ("POST", "/api/v1/tag_suggestions/{suggestion_id}/dismiss"): RoutePolicy(
        _PIC, id_param="suggestion_id", id_resolver="tag_suggestion"
    ),
    # Highest-risk carry-forward: bulk-reopen takes a body id list and has no
    # handler-level scope filter at all. §N4: body_ids names the list of
    # SUGGESTION ids; the gate resolves each to its picture (tag_suggestion
    # resolver) and membership-checks every one - not just the first.
    ("POST", "/api/v1/tag_suggestions/bulk-reopen"): RoutePolicy(
        _PIC, body_ids="ids", id_resolver="tag_suggestion"
    ),
    # ── tagger_runs.py ──────────────────────────────────────────────────────
    # NEEDS REVIEW: the plan carry-forward lists "tagger_runs -> PICTURE_SCOPED",
    # but these endpoints carry NO picture id (global model-eval stats). Declared
    # by actual behaviour: ingest is an owner write; list is reachable by READ
    # tokens today (GET, not READ_BLOCKED) and exposes model-eval stats.
    ("POST", "/api/v1/tagger-runs"): RoutePolicy(
        _OWNER,
        justification="Ingest tagger eval run; POST blocked for READ tokens; owner only",
    ),
    ("GET", "/api/v1/tagger-runs"): RoutePolicy(_ANY),
    # ── taggers.py ──────────────────────────────────────────────────────────
    # Retargeted ANY_TOKEN -> OWNER_ONLY on 2026-08-15 (#326). Two reasons, and
    # the first is the owner's own data: `settings` is this user's saved
    # tagger_settings run through fill_defaults, so a plugin declaring a
    # "string" parameter - which the plugin guide blesses - puts whatever the
    # owner typed into it (a model path, a prompt) in front of every share-link
    # holder. The second is that every other field is verbatim third-party text
    # from a plugin's own class body. Nothing is lost: tagging and captioning
    # are POSTs, which a READ token cannot make, so the list only ever rendered
    # controls a non-owner could not use.
    ("GET", "/api/v1/taggers"): RoutePolicy(
        _OWNER,
        justification="Plugin list + the caller's own tagger_settings; owner only - a scoped or READ token cannot run a tagger anyway",
    ),
    # The plugin folders and the load failures both came off GET /taggers so
    # this tier could hold them: the folders are host paths under the owner's
    # home directory, a load-failure message is exception text from third-party
    # code that can carry any path it was reaching for, and that route is
    # ANY_TOKEN, so every share-link holder was reading both. Local, not merely
    # owner, because a host path is the §16.3 disclosure class - and nothing is
    # lost remotely, since acting on either means editing a file in that folder.
    ("GET", "/api/v1/taggers/plugin-diagnostics"): RoutePolicy(
        _LOCAL,
        justification="§16.3 host-path disclosure: names the scanned tagger-plugin folders on the server's disk and returns plugin import errors carrying host paths; owner + loopback/LAN/Tailscale, or remote owner iff allow_remote_host_ops=true (§16.3.1)",
    ),
    ("POST", "/api/v1/taggers/{name}/download"): RoutePolicy(
        _OWNER,
        justification="Download tagger plugin; POST blocked for READ tokens; owner only",
    ),
    ("DELETE", "/api/v1/taggers/{name}/artifacts/{artifact_id}"): RoutePolicy(
        _OWNER,
        justification="Delete tagger artifact; DELETE blocked for READ tokens; owner only",
    ),
    # ── test_hooks.py (mounted ONLY when enable_test_hooks=True) ─────────────
    # Conditionally mounted, but ALWAYS declared: the gate resolves declarations
    # against the routes actually mounted at startup, so an undeclared
    # conditional route aborts boot the moment its flag is on - which is exactly
    # what killed the Playwright e2e backend. The mirror image (a declaration
    # with no mounted route) is a "dead declaration" and also aborts, so this
    # route is additionally listed in CONDITIONALLY_MOUNTED_ROUTES below, which
    # waives ONLY that absence complaint.
    ("POST", "/api/v1/test-hooks/ws-event"): RoutePolicy(
        _LOOPBACK,
        justification=(
            "E2E-only hook that calls vault.notify with a caller-supplied "
            "payload, i.e. it SYNTHESISES arbitrary grid WebSocket events "
            "(CHANGED_PICTURES / PICTURE_IMPORTED / CHANGED_TAGS / ...) that are "
            "broadcast to every connected client. That is a capability to drive "
            "OTHER clients' state, not merely to read or write the caller's own "
            "data, so it is classed with the host-shell red line rather than as "
            "an ordinary owner write. Loopback is free here and permanent: the "
            "router is mounted only when enable_test_hooks=True, which only "
            "frontend/e2e/serve_e2e_backend.py sets, and that backend binds "
            "127.0.0.1 with Playwright's webServer running the test on the same "
            "host - there is no legitimate remote caller, ever, by construction. "
            "LOOPBACK rather than LOCAL_OWNER_ONLY specifically because "
            "allow_remote_host_ops (a filesystem-operations flag) must never be "
            "able to expose a test hook. Net effect: if enable_test_hooks were "
            "ever switched on in a real, network-reachable deployment, the hook "
            "is still unreachable remotely for any correctly-configured "
            "deployment. Do NOT over-read that: it inherits the pre-existing "
            "proxy caveat shared by all loopback routes (docs §16.3.1, CSO "
            "Condition 2) - a reverse proxy that sets no X-Forwarded-For, or "
            "passes an inbound one through, makes a remote caller resolve to "
            "loopback. So safety depends on the flag being off OR the proxy "
            "being configured correctly, not on the tier alone. (Container "
            "port-mapping is not a bypass: Docker bridge / slirp present "
            "172.17.x / 10.0.2.x, which are not loopback.) Strictly stronger "
            "than the handler's existing inline require_unscoped_owner, which "
            "remains as defence in depth."
        ),
    ),
}

# Routes that exist only when a server-config flag is set. They are declared in
# ROUTE_POLICIES above so the gate admits them WHEN mounted (an undeclared route
# is denied at runtime and aborts boot), but they must not be reported as "dead
# declarations" in the normal configuration where they are absent.
#
# This set only ever SUPPRESSES a dead-declaration complaint. It cannot admit an
# undeclared route - ``undeclared`` is computed from the mounted set against the
# registry and never consults this - and it cannot weaken the policy applied to
# the route when it IS mounted. The cost is narrow and explicit: a declaration
# listed here will not be flagged as rot if its route is deleted outright, so
# keep the set tiny and justified.
CONDITIONALLY_MOUNTED_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        # routes/test_hooks.py - mounted only when ``enable_test_hooks`` is true,
        # which only frontend/e2e/serve_e2e_backend.py sets.
        ("POST", "/api/v1/test-hooks/ws-event"),
    }
)

# A conditional route must still be DECLARED: this set is an absence waiver, not
# a coverage waiver. Asserted at import so it can never be used to smuggle an
# undeclared route past the matrix.
_undeclared_conditionals = CONDITIONALLY_MOUNTED_ROUTES - set(ROUTE_POLICIES)
if _undeclared_conditionals:  # pragma: no cover - import-time invariant
    raise RuntimeError(
        "CONDITIONALLY_MOUNTED_ROUTES entries must also appear in ROUTE_POLICIES "
        f"(these do not): {sorted(_undeclared_conditionals)}"
    )
del _undeclared_conditionals

# WS routes: see authn/websocket.py - the HTTP authz gate does NOT cover
# WebSockets; their chokepoint is authenticate_websocket (plan §6). The two WS
# routes (/ws/comfyui, /api/v1/ws/updates) are acknowledged in the coverage
# matrix (tests/test_architecture_guardrails.py::test_websocket_routes_are_acknowledged)
# and are deliberately absent from ROUTE_POLICIES.

__all__ = ["ROUTE_POLICIES", "CONDITIONALLY_MOUNTED_ROUTES"]
