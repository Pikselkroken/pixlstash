"""The route-policy registry: the single authorization declaration table.

``ROUTE_POLICIES`` is the one place every mounted HTTP route declares its access
requirement, keyed by ``(method, effective_path_template)`` — the *prefixed*
path as enumerated by :func:`pixlstash.route_inventory.iter_api_route_contexts`
(e.g. ``("GET", "/api/v1/pictures/{picture_id}/thumbnail")``). It IS the coverage
matrix: reviewable in one screen, diffable, greppable. See the backend refactor
plan §3.2 and ``docs/backend_architecture.md`` §16.2.

**Phase 1 Step 2 — back-fill of current behaviour.** Every mounted route below is
declared with the single :class:`AccessPolicy` that reproduces its behaviour
TODAY, so that when the gate flips to enforcing (Steps 3-4) nothing changes. The
derivation, per route, comes from the auth middleware gating in ``auth.py``
(``AUTH_EXCLUDED_*``, ``READ_BLOCKED_GET_PATHS``, ``READ_SAFE_POST_PATHS``, the
non-GET block for READ tokens, ``require_local_for_write``, the ``ALL``
+``resource_type`` fail-closed rejection) PLUS the inline object checks in the
handlers (``enforce_picture_scope`` / ``fetch_scope_allowed_picture_ids`` /
``require_unscoped_owner`` / the ``_require_scope_allows_*`` ladders). The full
per-route rationale and the reviewer flags live in
``docs/reviews/authz-coverage-matrix.md`` — that document is the artifact the
adversarial security review consumes.

**Semantics that shape the mapping (verified against the code):**

* The auth middleware blocks READ-scoped tokens from every non-GET method except
  the ``READ_SAFE_POST_PATHS`` allowlist, and from the ``READ_BLOCKED_GET_PATHS``
  GET set. Every resource-scoped share token is a READ token
  (``ALL``+``resource_type`` is refused at mint and fail-closed at the
  middleware), so a mutating route with no ``READ_SAFE`` exemption is reachable
  ONLY by an unscoped owner today — hence ``OWNER_ONLY`` is a no-op there.
* ``fetch_scope_allowed_picture_ids`` (and the ``_require_scope_allows_*``
  ladders) return "no restriction" for BOTH an owner token and an unscoped-READ
  token (``token_scope.resource_type is None``); they only narrow/deny a
  *resource-scoped* token. So a handler's inline scope filter only affects
  resource-scoped share tokens.
* The inline checks REMAIN until Step 5; these declarations record the intended
  end-state so the gate can take over without a behaviour change.

The ``AUTHZ_GATE_ENFORCING`` constant in ``pixlstash/authz/gate.py`` is still
``False`` — this table is declared but not yet enforced (Step 2 is declarations
only). The CI guardrail's audit allowlist burns to zero as this table fills.
"""

from __future__ import annotations

from pixlstash.authz.policy import AccessPolicy, RoutePolicy

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
# WITHOUT it — so a new, unaudited list route leaks nothing to a scoped token
# (backend refactor plan §3.6; principal ruling 2026-07-21 D4). Shared frozen
# singleton — every current list route is audited (matrix derivation), so they
# all point at this one instance.
_LIST_AWARE = RoutePolicy(_LIST, scope_aware=True)


ROUTE_POLICIES: dict[tuple[str, str], RoutePolicy] = {
    # ── App-level / public (auth-excluded in AUTH_EXCLUDED_*) ───────────────
    ("GET", "/"): RoutePolicy(
        _PUBLIC, justification="Frontend SPA index; auth-excluded; no owner data"
    ),
    ("GET", "/version"): RoutePolicy(
        _PUBLIC, justification="Health/version probe; auth-excluded"
    ),
    ("GET", "/scalar"): RoutePolicy(
        _PUBLIC, justification="API docs UI; auth-excluded"
    ),
    ("GET", "/favicon.ico"): RoutePolicy(
        _PUBLIC, justification="Static favicon; auth-excluded"
    ),
    ("GET", "/docs"): RoutePolicy(
        _PUBLIC, justification="Swagger UI; auth-excluded (/docs/ prefix)"
    ),
    ("GET", "/docs/oauth2-redirect"): RoutePolicy(
        _PUBLIC, justification="Swagger oauth2 redirect; auth-excluded"
    ),
    ("GET", "/openapi.json"): RoutePolicy(
        _PUBLIC, justification="OpenAPI schema; auth-excluded"
    ),
    ("GET", "/{full_path:path}"): RoutePolicy(
        _PUBLIC,
        justification=(
            "Frontend SPA fallback serving the static shell/assets; returns no "
            "owner resource data. NEEDS REVIEW: this template is not statically "
            "in AUTH_EXCLUDED_*, so the middleware requires auth for a concrete "
            "non-excluded deep path; the planned PUBLIC-consistency check must "
            "reconcile (add to exclusions or special-case)."
        ),
    ),
    ("GET", "/api/v1/check-session"): RoutePolicy(
        _PUBLIC, justification="Session status probe; auth-excluded (/check-session)"
    ),
    ("GET", "/api/v1/login"): RoutePolicy(
        _PUBLIC, justification="Registration-status probe; auth-excluded (/login)"
    ),
    ("POST", "/api/v1/login"): RoutePolicy(
        _PUBLIC,
        justification="Password login / first-owner claim; auth-excluded (/login)",
    ),
    ("POST", "/api/v1/logout"): RoutePolicy(
        _PUBLIC, justification="Logout; auth-excluded (/logout)"
    ),
    ("GET", "/share/{token_slug}"): RoutePolicy(
        _PUBLIC,
        justification="Share-link landing; resolves its own token; auth-excluded (/share/ prefix)",
    ),
    # ── App-level authenticated, no per-object data ─────────────────────────
    ("GET", "/api/v1/network/info"): RoutePolicy(_ANY),
    ("GET", "/api/v1/protected"): RoutePolicy(_ANY),
    # ── config.py (user account + server-config) ────────────────────────────
    ("GET", "/api/v1/users/me/config"): RoutePolicy(
        _OWNER,
        justification="Owner config; READ_BLOCKED_GET_PATHS blocks READ tokens; only owner reaches",
    ),
    ("GET", "/api/v1/users/me/penalised-tags"): RoutePolicy(_ANY),
    ("PATCH", "/api/v1/users/me/config"): RoutePolicy(
        _OWNER, justification="require_unscoped_owner; owner config write"
    ),
    ("POST", "/api/v1/users/me/auth"): RoutePolicy(
        _OWNER,
        justification="Change owner password; POST blocked for READ tokens; owner only",
    ),
    ("GET", "/api/v1/users/me/auth"): RoutePolicy(
        _OWNER,
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
    ("POST", "/api/v1/server-config/open"): RoutePolicy(
        _LOOPBACK,
        justification="§16.3.1 RED LINE: opens the server config path in the host file browser (_open_in_os → os.startfile/open/xdg-open — same host-GUI spawn as pictures/open-location and reference-folders/open); loopback-only, allow_remote_host_ops can NOT loosen it",
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
    ("GET", "/api/v1/pictures/plugins"): RoutePolicy(_ANY),
    ("GET", "/api/v1/sort_mechanisms"): RoutePolicy(_ANY),
    ("GET", "/api/v1/pictures/import/status"): RoutePolicy(_ANY),
    ("GET", "/api/v1/pictures/import/staging/{staging_id}/status"): RoutePolicy(_ANY),
    ("GET", "/api/v1/pictures/export/status"): RoutePolicy(_ANY),
    ("GET", "/api/v1/pictures/export/download/{task_id}"): RoutePolicy(_ANY),
    ("POST", "/api/v1/pictures/import"): RoutePolicy(
        _OWNER,
        justification="Import pictures; POST blocked for READ tokens; owner only",
    ),
    # Async streaming-staging import (#459). These stream client-provided upload
    # bytes into the vault and hand off to a background import task — they do NOT
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
    # ── characters.py ───────────────────────────────────────────────────────
    ("GET", "/api/v1/characters"): _LIST_AWARE,
    ("GET", "/api/v1/characters/{id}"): RoutePolicy(_CHAR, id_param="id"),
    ("GET", "/api/v1/characters/{id}/summary"): RoutePolicy(_CHAR, id_param="id"),
    ("GET", "/api/v1/characters/{id}/reference_pictures"): RoutePolicy(
        _CHAR, id_param="id"
    ),
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
            "exists — do not remove it in Step 5 before then."
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
            "exists — do not remove it in Step 5 before then."
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
            "int-or-name lookup (divergence risk, D2); the inline "
            "_require_scope_allows_project check remains the live enforcement until "
            "a shared resolver exists — do not remove it in Step 5 before then."
        ),
    ),
    ("GET", "/api/v1/projects/{id_or_name}/picture_sets"): RoutePolicy(
        _PROJ,
        id_param="id_or_name",
        resolved_inline=True,
        justification=(
            "§N3 id-or-name: {id_or_name} may be a numeric id OR a project name. "
            "The gate cannot resolve it without duplicating the handler's "
            "int-or-name lookup (divergence risk, D2); the inline "
            "_require_scope_allows_project check remains the live enforcement until "
            "a shared resolver exists — do not remove it in Step 5 before then."
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
    # before the membership check. Latent end-state — a scoped token cannot reach
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
    # resolver) and membership-checks every one — not just the first.
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
    ("GET", "/api/v1/taggers"): RoutePolicy(_ANY),
    ("POST", "/api/v1/taggers/{name}/download"): RoutePolicy(
        _OWNER,
        justification="Download tagger plugin; POST blocked for READ tokens; owner only",
    ),
    ("DELETE", "/api/v1/taggers/{name}/artifacts/{artifact_id}"): RoutePolicy(
        _OWNER,
        justification="Delete tagger artifact; DELETE blocked for READ tokens; owner only",
    ),
}

# WS routes: see authn/websocket.py — the HTTP authz gate does NOT cover
# WebSockets; their chokepoint is authenticate_websocket (plan §6). The two WS
# routes (/ws/comfyui, /api/v1/ws/updates) are acknowledged in the coverage
# matrix (tests/test_architecture_guardrails.py::test_websocket_routes_are_acknowledged)
# and are deliberately absent from ROUTE_POLICIES.

__all__ = ["ROUTE_POLICIES"]
