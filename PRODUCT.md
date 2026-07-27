# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

<!-- Cross-platform: same Vue 3 + Vuetify Web UI is served by the standalone
server and wrapped by the Electron desktop app. Design language is web today.
A dedicated native app is planned but not yet built — see Capabilities. -->

## Users

Primary user: **people who generate AI images at volume** — ComfyUI / LoRA /
character-based generation workflows that produce far more output than any
folder-and-filename scheme can keep up with. Their job in PixlStash is to triage,
score, tag, describe, organize into characters and sets, and cull large batches
of generated (and reference) images quickly, then feed selections back into
generation.

Secondary audiences (supported, not the design center): photographers and
collectors managing large personal libraries, and dataset curators preparing
image sets. Design for the AI-generation workflow first; keep the general
large-library case working.

## Product Purpose

PixlStash is a self-hosted picture-library server for organizing, filtering, and
reviewing very large image (and video) collections. It exists because generation
and camera output outgrows manual organization: PixlStash turns thousands of raw
images into a searchable, sortable, reviewable library automatically, on hardware
the user controls. Success is a user keeping their entire collection in PixlStash
because finding, judging, and acting on any subset is faster there than anywhere
else.

## Positioning

**AI organization at scale, locally.** Automatic tagging, AI descriptions
(selectable engines incl. JoyCaption), and smart quality scoring make a huge,
messy collection instantly searchable and sortable — without the library ever
leaving the user's machine. The hard-to-copy combination is *AI-grade
organization + local-first privacy + a review UX built for volume* (instant grid,
keyboard-driven scoring/selection/tagging, character & set structure, in-place
ComfyUI runs). A cloud photo tool can't credibly claim the privacy; a local file
browser can't credibly claim the AI organization.

## Operating Context

- Runs on the user's own machine; serves a browser UI at a local (or, if the user
  chooses, internet-facing) address. Default port `9537`.
- Delivered three ways from one codebase: **native desktop app** (Windows, macOS
  Apple Silicon, Linux — no Python/browser tab needed), **Docker image**, and
  **pip package**. Desktop and pip/Docker installs on the same machine share one
  library.
- First run downloads AI model weights (tagging, captioning, scoring) into the
  platform user-data directory; needs a network connection once, then works
  offline. Desktop app ships a CPU runtime and can add GPU acceleration (NVIDIA
  CUDA, experimental AMD ROCm) on demand.
- Core loop: import (incl. watched import/reference folders) → automatic
  processing (tags, descriptions, scores, faces) → review/filter/sort/score →
  organize into characters, sets, projects → act (retag/regenerate via context
  menu, run ComfyUI workflows on a selection, export, share).
- Sharing is via read-only, scoped share tokens (picture / set / character /
  project). Persistent view URLs make any view bookmarkable and refresh-safe.

## Capabilities and Constraints

Confirmed capabilities: automatic tagging + AI descriptions with selectable
engines; context-menu re-tag / regenerate on any selection; instant thumbnail
grid with async metadata fill; fast metadata/tag filtering; smart score sorting;
character & set organization; face detection/recognition (InsightFace, selectable
model pack with licensing tradeoff); local storage of all library data; ComfyUI
integration (run workflows on selected images in-place); plugin system for
user-defined filter operations (built-in API is MIT; backend core is GPL-3.0);
scoped read-only sharing; persistent/bookmarkable view URLs; keyboard shortcuts
for scoring, selection, tagging, deletion, navigation; REST API for integration;
real-time updates over WebSocket.

Constraints and terminology:
- Frontend: Vue 3 + Vuetify + Pinia; Python backend; one shared Web UI across
  server / Docker / Electron.
- Domain nouns to keep consistent: **picture**, **picture set**, **character**,
  **project**, **score**, **tag**, **description**, **share token**, **import
  folder** / **reference folder**, **plugin**.
- Backend core licensed GPL-3.0; plugin authoring API/template MIT-licensed.

Explicitly undecided / planned (do not treat as shipped):
- A dedicated **native mobile/desktop app** beyond the current Electron wrapper is
  planned but not built.
- **Mobile web** support is a secondary goal — it "should work acceptably" but is
  not yet a first-class layout.

## Brand Commitments

- Name and logo: **PixlStash** name, logo, and branding are property of the
  project's copyright owner (see `BRANDING.md`). May not be used to imply official
  endorsement or affiliation. The existing identity is fixed, not open for
  reinvention.
- Logo asset: `Logo.png` at repo root; screenshot/marketing assets under
  `website/assets/`.
- Project / vendor name: **Pikselkroken** (GitHub org, container registry).
- Website: `pixlstash.dev` (install / upgrade / demo pages).
- Existing design system is the incumbent visual authority: repo is source of
  truth via `docs/design/design-tokens.css` + `docs/design/visual-language.md`;
  app colors live in the Vuetify themes in `frontend/src/main.js`; frontend token
  mirror at `frontend/src/styles/design-tokens.css`. (Recorded as product fact;
  whether to preserve or evolve it is a design decision for later, not settled
  here.)

## Evidence on Hand

- Real, shipped product at version 1.7.1 with a public site (`pixlstash.dev`), a
  public demo (`demo.pixlstash.dev`), Docker images (`ghcr.io/pikselkroken`), and
  a pip package.
- Real screenshots/marketing assets under `website/assets/` (e.g.
  `ScreenshotGrid.jpg`, install/upgrade/demo banners).
- Product/legal docs in repo: `README.md`, `PRIVACY.md`, `BRANDING.md`,
  `LICENSE`, `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`.
- Architecture references: `docs/frontend_architecture.md`,
  `docs/backend_architecture.md`, `docs/integration_architecture.md`.
- No public testimonials, customer names, user counts, or benchmark figures are
  established here — future work must not fabricate them. The only usage signal is
  a deliberately coarse, aggregate lower-bound install estimate from the opt-in
  update check.

## Product Principles

1. **Volume is the point.** Every core interaction must stay fast and effortless
   at thousands-to-millions of images: instant grid, async metadata, keyboard
   flow, bulk actions over selections.
2. **The library never leaves the user's machine.** Local-first and private by
   default; no account, no cloud sync, no telemetry unless explicitly opted in.
   The UI must never nudge toward giving that up.
3. **AI does the sorting; the user does the judging.** Automatic tags,
   descriptions, and scores exist to surface and rank — the human stays in control
   of what to keep, tag, and act on.
4. **One product, everywhere.** A single Web UI must serve the standalone server,
   Docker, and the Electron desktop app coherently, and degrade acceptably toward
   mobile.
5. **Organization is actionable.** Structure (characters, sets, projects, scores,
   filters) is only worth it if it feeds back into doing things — regenerating,
   running ComfyUI workflows, exporting, sharing.

## Accessibility & Inclusion

Primary usage is dense, keyboard-driven review on desktop, so full
keyboard operability of core actions (scoring, selection, tagging, deletion,
navigation) is a product requirement, not a nicety. No further product-specific
standard has been established.
