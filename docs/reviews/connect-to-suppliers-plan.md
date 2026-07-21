# "Connect to …" capability for external suppliers — implementation plan

**Date:** 2026-06-23
**Scope:** Investigate whether PixlStash can add a "Connect to <supplier>" affordance,
surfaced from the toolbar's **Export**, **Import**, and **Text-to-Image (ComfyUI)** menus.
**Status:** Planning only. No code was changed by this document.

> **Headline finding:** PixlStash today has **no OAuth / OIDC / external-API
> connection infrastructure of any kind.** The only "supplier" the app actually
> *connects to over the network* is **ComfyUI**, and it does so via a plain
> per-user URL string (`comfyui_url`) with **no authentication**. Every other
> "supplier" surfaced in the UI (Google Photos, iCloud, Flickr) is a **manual
> zip-import instruction screen** — the app never talks to those services.
> Export is a **local zip download** only. So "Connect to …" is genuinely new
> capability for all suppliers except ComfyUI, where a lightweight "Connect /
> test connection" affordance is cheap and low-risk.

---

## 1. Current state

| Supplier / target | Surface | Does the app connect? | How / auth | Where it lives (file:line) |
|---|---|---|---|---|
| **Local files** | Import dialog → "Local import" tab; also drag-drop on grid | N/A (browser-side file pick → multipart upload) | None — `POST /pictures/import` multipart | `frontend/src/components/io/PhotosImportDialog.vue:237-268` (picker/dropzone), `:117-123` (`triggerLocalImport`); backend `pixlstash/routes/pictures/_import.py:76-82` (`import_pictures`) |
| **Automatic folder monitoring** | Import dialog → "Automatic Folder Monitoring" tab | Yes — server reads a local/host filesystem path | None (filesystem path; owner-only) | dialog tab `PhotosImportDialog.vue:270-290`, `fetchWatchFolders` `:157-173` → `GET /import-folders`; backend `pixlstash/routes/import_folders.py`, `pixlstash/db_models/import_folder.py`, `pixlstash/tasks/watch_folder_import_task.py` |
| **Google Photos** | Import dialog → "Google Photos" tab | **No.** Static instructions to use Google Takeout and drag the zip in | None — manual zip | `PhotosImportDialog.vue:291-315` (instructions only; external link to takeout.google.com) |
| **iCloud Photos** | Import dialog → "iCloud Photos" tab | **No.** Static export instructions, then drag zip in | None — manual zip | `PhotosImportDialog.vue:316-348` |
| **Flickr** | Import dialog → "Flickr" tab | **No.** Static "request your archive" instructions, then drag zip in | None — manual zip | `PhotosImportDialog.vue:349-371` |
| **Generic "external API" import source** | — | **Does not exist.** No external-API import source, route, or component found | — | (searched; none — see §5) |
| **ComfyUI (Text-to-Image / Image-to-Image)** | Toolbar T2I menu + per-picture runner; configured in Settings → Workflows | **Yes** — the *backend* calls a ComfyUI HTTP/WS server | **No auth.** Per-user `comfyui_url` string (host+port), default `http://127.0.0.1:8188/` | URL config UI `frontend/src/components/settings/WorkflowsSection.vue:55-166`; stored via `PATCH /users/me/config {comfyui_url}` `WorkflowsSection.vue:104,127`; backend uses it in `pixlstash/routes/comfyui.py:1186-1187,1338-1339,1398-1399,1560-1561`; default `comfyui.py:53`; "configured?" flag `frontend/src/App.vue:1440`; T2I panel `frontend/src/components/panels/TbComfyPanel.vue` |
| **Export target (zip download)** | Toolbar Export menu | N/A — produces a zip the browser downloads | None | panel `frontend/src/components/panels/TbExportPanel.vue`; store `frontend/src/stores/useExportStore.js`; backend `pixlstash/routes/pictures/_export.py:37-131` (start job → poll status → `GET …/download` `application/zip`) |
| **No external export destination** | — | **Does not exist.** Export is local zip only | — | `_export.py:124-131` returns `application/zip`; no cloud/remote target found |

### Connection / auth infrastructure that exists today (what a "Connect to" flow could reuse)

- **Session auth:** cookie-based login/logout/check-session + a per-tab client id and an
  optional share-`token` query param. `frontend/src/utils/apiClient.js:130-205` (`login`,
  `logout`, `checkSession`), interceptors `:86-128`. Backend auth in `pixlstash/auth.py`.
- **Personal access tokens (PAT):** create/list/delete/patch scoped tokens for *inbound*
  share access — `pixlstash/routes/config.py:354-400`. These are **PixlStash's own** tokens
  for sharing *out*; they are **not** credentials for third-party services.
- **Per-user config store:** `GET`/`PATCH /users/me/config` with an `extra="allow"` schema,
  persisted on the `User` row — `pixlstash/routes/config.py:238-332`, schema `:71-111`
  (note `comfyui_url`, `public_url` already live here). This is the natural home for any
  future per-user connection settings, **but it is plaintext on the user row** — unsuitable
  for storing third-party OAuth refresh tokens or API secrets as-is.
- **Settings sections pattern:** `frontend/src/components/settings/` — `AccountSection.vue`,
  `WorkflowsSection.vue` (ComfyUI host config + workflow import), `ComputeSection.vue`, etc.
  `WorkflowsSection.vue` is the closest existing precedent for a "configure an external
  service" panel (host/port dialog, save/clear, success/error states).
- **Outbound HTTP to an external service:** only ComfyUI, via `requests`/`websockets`
  in `pixlstash/routes/comfyui.py` (e.g. `_submit_comfyui_prompt` `:345-399`,
  `_upload_image_to_comfyui` `:289-342`, WS proxy `:1163-1254`). There is **no** generic
  outbound-connector abstraction, no OAuth client, no token vault, no secret encryption.
- **WebSocket proxy with origin + owner enforcement:** `comfyui.py:1163-1180` — a reusable
  pattern if other suppliers ever need a proxied live channel.

---

## 2. Feasibility per supplier

Legend — effort: **S** (hours), **M** (a few days), **L** (1–2+ weeks).
Risk: anything that introduces OAuth, stores third-party secrets, or makes the server
fetch from arbitrary external hosts is a **security-gated** item (must pass
`chief-security-officer` before merge, per CLAUDE.md).

### ComfyUI — **"Connect / Test connection"** — *makes sense, do this first*
- **What it would do:** Replace the implicit "is `comfyui_url` set?" check with an explicit
  "Connect to ComfyUI" affordance: configure host/port (already exists), then **ping** the
  server (e.g. ComfyUI `GET /system_stats` or `/object_info`) and show **Connected /
  Unreachable** status. Surface a "Connect to ComfyUI…" entry in the T2I toolbar menu when
  not yet configured (today the whole T2I button is simply hidden — `Toolbar.vue:418`
  `v-if="filterStore.comfyuiConfigured"`).
- **Effort:** **S–M.** UI already 90% built in `WorkflowsSection.vue`. New work: a backend
  reachability endpoint (the backend must do the fetch — browser → ComfyUI is cross-origin
  and `apiClient` deliberately *won't* attach creds to external hosts, `apiClient.js:95-106`),
  plus a status pill and a menu CTA.
- **Risk:** **Low-moderate.** No new secrets. **But** a "ping arbitrary host:port from the
  server" endpoint is an **SSRF surface** — must be owner-only and ideally validated. Route
  it past `chief-security-officer`. Reuse the owner-gating already in the WS proxy.

### Local files — **not a "connection"** — *N/A*
- It's a browser file picker; there is nothing to "connect to." Skip. (A "Connect to"
  entry here would be meaningless.)

### Automatic folder monitoring — **already a managed connection** — *optional polish*
- It is effectively "connect to a folder on disk." It already has management UI (sidebar
  Folders tab). A "Connect a folder…" shortcut from the Import menu is a **discoverability**
  improvement, not new capability. **Effort S; risk low** (owner-only, local FS path —
  existing surface).

### Google Photos — **real OAuth integration** — *high effort, security-gated*
- **What it would do:** OAuth 2.0 to the Google Photos Library API, list/select albums,
  pull media into PixlStash. This is the canonical "Connect to Google Photos" button.
- **Effort:** **L.** Requires: an OAuth client (server-side), a redirect/callback route,
  **encrypted at-rest storage of refresh tokens** (the current plaintext `User` config is
  not adequate), token refresh, the Photos API client, paging, dedupe, and a picker UI.
  None of this scaffolding exists today.
- **Risk:** **High / fully security-gated.** New secrets (client secret + per-user refresh
  tokens), new external data egress, new callback endpoint (CSRF/`state` handling). Google
  also has API-scope review/verification requirements and (as of recent platform changes)
  significant restrictions on third-party Photos Library read access — **verify current API
  availability before committing** (see §5). Must pass `chief-security-officer`; likely a CEO
  build-vs-buy decision too.

### iCloud Photos — **no supported API** — *not feasible as a "connection"*
- Apple provides **no public iCloud Photos read API** for third parties (CloudKit is for an
  app's *own* container, not a user's personal photo library). A "Connect to iCloud" button
  cannot be built on a supported API. Keep the manual zip-instruction tab. **Effort N/A;
  do not attempt.** (Flag to CEO if there's demand — the honest answer is "export + drag in.")

### Flickr — **OAuth API exists** — *medium effort, security-gated, low ROI*
- Flickr has an OAuth 1.0a API that can list/download a user's photos. Technically a real
  "Connect to Flickr" is buildable. **Effort M–L** (OAuth 1.0a is fiddlier than OAuth 2.0;
  same secret-storage problem as Google). **Risk:** security-gated (secrets + egress).
  **ROI is low** given Flickr's shrinking user base — defer unless product asks for it.

### Generic "external API" import source — **does not exist** — *would be net-new*
- No such source/route/component exists. If desired, the right shape is a **pluggable
  importer/connector framework** (see §3) rather than a one-off. **Effort L.**

### Export to an external destination (e.g. cloud drive / S3 / WebDAV) — **does not exist** — *net-new, security-gated*
- Export is local-zip-only (`_export.py`). "Connect to <storage>" for *push* export
  (Google Drive, Dropbox, S3/R2, WebDAV) is a coherent feature but entirely new: needs
  destination credentials (secrets), an upload pipeline, and per-destination auth.
  **Effort M–L per destination; security-gated.**

---

## 3. Recommended approach

**Phase the work; do not build a generic OAuth framework speculatively.**

1. **Ship the cheap, real one first: ComfyUI "Connect / Test connection."**
   This is the only supplier the app actually connects to, the UI mostly exists, and it
   adds genuine value (clear connected/unreachable status instead of a silently hidden
   button). It establishes the *visual* "Connect to <supplier>" pattern (status pill +
   menu CTA) without dragging in OAuth or secret storage.

2. **Standardise a "Connect to <supplier>" presentation pattern**, but **only render
   entries for suppliers we can actually connect to.** Concretely:
   - A small shared **`SupplierConnectionStatus`** presentation component (pill: *Connected
     / Not connected / Unreachable* + a "Connect…/Configure…" button) reused by the T2I
     menu, the Import dialog tabs, and (future) the Export menu.
   - A **`useConnectionsStore`** (Pinia) holding per-supplier connection state, seeded from
     `GET /users/me/config` (which already carries `comfyui_url`). Today it would track
     exactly one real connection (ComfyUI); it gives later suppliers a home.
   - In menus, drive entries from a **capability registry** so a supplier with no real API
     (iCloud) shows the existing *instructions* affordance, not a fake "Connect" button.

3. **Decision: per-supplier, not one unified OAuth pattern — for now.**
   The suppliers diverge too much (no-auth URL vs OAuth 2.0 vs OAuth 1.0a vs no-API) to
   justify a single abstraction today. Build the **presentation** uniformly (step 2) but
   keep the **auth/transport** per-supplier. *Revisit* a unified connector framework only
   once ≥2 OAuth suppliers are actually greenlit by the CEO — at which point the shared
   need (encrypted secret storage, callback route, token refresh) becomes worth a real
   abstraction. Adding that framework now would be speculative debt.

4. **Any OAuth supplier (Google/Flickr) requires a prerequisite: a secrets-at-rest story.**
   The current `User`-row plaintext config (`config.py:71-111`) must **not** hold third-party
   refresh tokens. Before any OAuth supplier, design encrypted credential storage +
   callback/`state` handling and clear it with `chief-security-officer`. This is the real
   gate — treat it as a separate, blocking work item, not part of the UI task.

**Where the menu entries attach:**
- **T2I menu:** `TbComfyPanel.vue` (panel body) and `Toolbar.vue:417-445` (the `v-if`
  that hides the button when unconfigured → change to *show* a "Connect to ComfyUI…" CTA).
- **Import menu/dialog:** `PhotosImportDialog.vue` tabs (`:229-372`). Each tab's header is
  the natural slot for a status pill / Connect button vs the current instructions block.
- **Export menu:** `TbExportPanel.vue` — only once a real external destination exists.

---

## 4. Concrete next steps (ordered, file-level)

**Milestone A — ComfyUI "Connect / Test connection" (S–M, low risk):**
1. Backend: add an **owner-only** reachability endpoint, e.g. `GET /comfyui/status`, in
   `pixlstash/routes/comfyui.py` that resolves the user's `comfyui_url`
   (reuse the `getattr(user, "comfyui_url", …) or DEFAULT_COMFYUI_URL` pattern at
   `comfyui.py:1186-1187`) and does a short-timeout `GET {base}/system_stats`, returning
   `{configured, reachable, detail}`. **Owner-gate it** (mirror the WS-proxy owner check
   `comfyui.py:1176-1179`). **SSRF note: route this past `chief-security-officer`.**
2. Frontend store: add `useConnectionsStore` (or extend `useFilterStore.comfyuiConfigured`,
   `App.vue:1440`) to hold `{comfyui: {configured, reachable}}`, populated from
   `GET /users/me/config` + the new status endpoint.
3. Frontend component: `frontend/src/components/SupplierConnectionStatus.vue` — presentational
   pill + Connect/Configure button (lead-designer to confirm tokens; design manual is
   mandatory per CLAUDE.md item 4).
4. Toolbar: in `Toolbar.vue:417-445`, when **not** configured, render a "Connect to
   ComfyUI…" entry (opens Settings → Workflows or a focused connect dialog) instead of
   hiding the T2I button entirely.
5. Settings: in `WorkflowsSection.vue` ComfyUI Host section (`:662-688`), add a "Test
   connection" button that calls the new status endpoint and shows Connected/Unreachable.

**Milestone B — uniform presentation in the Import dialog (S, no new backend):**
6. In `PhotosImportDialog.vue`, add the `SupplierConnectionStatus` pill to each supplier
   tab header; for Google/iCloud/Flickr keep the *instructions* affordance (no real
   connection yet) so the UI is honest. Drive per-tab behaviour from a small capability map.

**Milestone C — (gated, only if product greenlights) OAuth supplier groundwork (L):**
7. Design + review encrypted third-party credential storage and an OAuth callback/`state`
   route with `chief-security-officer` **before any provider code**. (Blocking gate.)
8. Implement the first chosen OAuth provider (likely Google Photos *iff* API access is
   confirmed available — see §5) end-to-end: connect button → consent → callback → token
   store → album picker → import pipeline reusing `POST /pictures/import`.

**Milestone D — (gated, future) external export destination (M–L):**
9. Add a destination connector + push-upload path off `_export.py`; "Connect to <storage>"
   in `TbExportPanel.vue`. Security-gated (destination secrets + egress).

---

## 5. Open questions / unknowns

- **Google Photos Library API availability for third-party read.** Google has tightened
  third-party access to users' Photos libraries in recent platform changes. **I don't know
  the current (2026) state of read access for an app like PixlStash** without checking
  Google's live API docs and verification requirements — this must be confirmed (and likely
  needs a `deep-research` / Google API console check) **before** committing to Milestone C.
  If read access is gated/unavailable, Google Photos stays a manual-zip tab like iCloud.
- **iCloud:** I'm confident there is **no supported public API** for reading a user's iCloud
  Photos library from a third-party server, but I did not exhaustively verify Apple's current
  developer offerings; treat "not feasible" as high-confidence, not certified.
- **Secrets-at-rest design.** There is **no** encrypted credential storage today. The exact
  mechanism (where keys live, how per-user tokens are encrypted, rotation) is undecided and
  is the real blocker for any OAuth supplier — needs a `chief-security-officer`-led design.
- **Does ComfyUI ever need auth?** Today the integration assumes an unauthenticated ComfyUI
  on a trusted (often localhost) host. If users run ComfyUI behind auth/a tunnel, "Connect"
  may need credentials — out of scope for Milestone A but worth noting.
- **Product priority / ROI.** Whether any OAuth supplier is worth the L-effort + security
  surface is a **CEO build-vs-cut call** (`chief-executive-officer`). Flickr in particular
  looks low-ROI. I did not assess demand.
- **Generic connector framework timing.** I recommend deferring it (§3.3); if the roadmap
  already commits to ≥2 OAuth suppliers, that calculus changes. I don't know the roadmap.
- **ComfyUI status probe endpoint choice.** `GET /system_stats` is the likely reachability
  probe, but I did not verify the exact ComfyUI endpoint contract in this repo's pinned
  ComfyUI version — confirm before implementing Milestone A step 1.
