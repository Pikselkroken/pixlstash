# Frontend review — `develop` → `main` merge (v1.6.0)

Reviewer: senior-frontend-developer persona. Architecture doc
(`docs/frontend_architecture.md`) read first. Diff range `main...develop`.

## MERGE VERDICT: READY WITH FIXES

No blockers. The build compiles, lint is clean, and the big Toolbar →
SelectionBar/SelectionMenu split is correct and behaviour-preserving. Remaining
items are Low-severity polish (two of them are leftovers the prior
`develop-electron-frontend.md` review already flagged and that were not picked up
in the follow-through). None of them break functionality; they can land with the
merge or as a fast follow.

- **BLOCKERS: 0**
- Build: **compiles** (`npm run build` → `✓ built in 2.28s`, only the pre-existing
  >1024 kB chunk-size warning, unchanged by this diff).
- Lint: **clean** (`eslint` on the new/changed files reports nothing).

## What this diff is

The frontend half of v1.6.0: the Electron desktop shell (TitleBar, window
controls, ComputeSection), the Toolbar restructure (selection UI extracted into
`SelectionBar.vue` + `SelectionMenu.vue`, Toolbar slimmed by ~1586 lines), the
`useBreadcrumb` / `useVersionCheck` composables, the sidebar pin/dock/auto-hide
model in `useSidebarStore`, the `PressStart2P → Tiny5` font swap, and the
Playwright screenshot e2e harness.

This range supersedes the earlier `develop..develop-electron` review at
`docs/reviews/develop-electron-frontend.md`. Status of that review's findings is
tracked below.

## Status of prior review (`develop-electron-frontend.md`)

| Prior finding | Status |
|---|---|
| **M1** — orphaned `@container selbar` query (container context lost in the split) | **Fixed.** `container: selbar / inline-size;` now lives on the grid-content wrapper in `ImageGrid.vue:6101`, with an explanatory comment, so the floating SelectionBar's `@container selbar` queries resolve. |
| **M2** — LoginScreen lost its only heading | **Fixed.** A visually-hidden `<h1 class="sr-only">` was added (`LoginScreen.vue:7-9`), `.sr-only` rule at line 155. |
| **M3** — desktop scrollbar thumb hardcoded to the dark palette | **Fixed.** `.is-desktop ::-webkit-scrollbar-thumb` now uses `rgba(var(--v-theme-on-surface), 0.18/0.32)` (`style.css:80-87`), tracking the active theme. |
| **L1** — dead selection props on Toolbar after the split | **Fixed.** Toolbar's `defineProps` is now trimmed to the 7 props it actually uses; the `<Toolbar>` binding in `ImageGrid.vue:47` passes only those. No dead props remain. |
| **L2** — `updateDismissed` inits `true` when nothing was dismissed | **Not fixed.** See L1 below. |
| **L3** — Sidebar Width copy describes the pin control, not the width toggle | **Not fixed.** See L2 below. |

## Findings (this range)

### L1 — `updateDismissed` still initialises to `true` on a fresh load
`frontend/src/composables/useVersionCheck.js:49-51`

```js
const updateDismissed = ref(
  localStorage.getItem(VERSION_CHECK_DISMISSED_KEY) === latestVersion.value,
);
```

At init `latestVersion.value` is `null`; with nothing stored,
`localStorage.getItem(...)` is also `null`, so this is `null === null` → **`true`**.
Not user-visible today: `updateAvailable` is false while `latestVersion` is null
so the alert is hidden, and the fetch callback (line 100) recomputes
`updateDismissed = dismissed === remote` once a version arrives. It's a latent
footgun for any future reader of `updateDismissed` before the first fetch.

Fix: `const updateDismissed = ref(false);` — the value is only meaningful after a
remote version exists, and the fetch path already sets it correctly.

### L2 — "Sidebar Width" section description describes the wrong control
`frontend/src/components/settings/AppearanceSection.vue:159`

The section renders the Full / Dock width toggle (with a correct `title` on the
heading: "Show the sidebar at full width or as a narrow icon dock."), but the
description line underneath reads:

```
Pin or unpin the sidebar from its header.
```

That describes the *pin* control (which lives in the sidebar header, a different
setting), not the width toggle. Reads as a copy leftover.

Fix: change the description to match the toggle, e.g. "Show the sidebar at full
width or as a narrow icon dock." (the wording already on the `title` one line up),
or drop the line.

### L3 — `sidebarRefreshPicturesDebounceTimeout` not cleared on unmount
`frontend/src/App.vue:129, 388-392, 1873-1893`

`App.vue` declares two debounce timers. The `onBeforeUnmount` block clears
`columnsMenuCloseTimeout` and `sidebarRefreshDebounceTimeout` but **not**
`sidebarRefreshPicturesDebounceTimeout` (declared line 129, set at line 391).

Impact is small: `App.vue` only unmounts on full shell teardown (logout / leaving
the authenticated view), and at most one already-scheduled debounced sidebar
picture-count refresh fires after unmount. But it's the same class as the timers
already cleaned two lines above, so it should match.

Fix: in `onBeforeUnmount`, mirror the existing pattern:
```js
if (sidebarRefreshPicturesDebounceTimeout) {
  clearTimeout(sidebarRefreshPicturesDebounceTimeout);
  sidebarRefreshPicturesDebounceTimeout = null;
}
```

### L4 — TitleBar update-dismiss button: add `type="button"` + `aria-label`
`frontend/src/components/TitleBar.vue:113-119`

The `×` dismiss button on the desktop update alert has a `:title` but no
`aria-label` (title is not a reliable accessible name) and no explicit
`type="button"`. The window-control buttons elsewhere in this file already do
both correctly; this one is the outlier.

Fix:
```html
<button
  type="button"
  class="titlebar-update-dismiss"
  aria-label="Dismiss update alert"
  :title="`Dismiss v${latestVersion} update alert`"
  @click.prevent="dismissUpdateAlert"
>&times;</button>
```

### L5 — TitleBar security/close colours are hardcoded hex
`frontend/src/components/TitleBar.vue` (security-update colours ~337-349; `.tb-close:hover` ~395)

The security-alert and Windows-style close-button colours are literal hex
(`#e57c00`, `#e53935`, `#e81123`, `#fff`) rather than theme tokens. This is
mostly fine — these are deliberate "danger" affordances and the close-on-hover red
is a Windows convention — but on the light desktop theme the warning text contrast
should be eyeballed. Low priority; flag only because the rest of this diff moved
scrollbars and washes to `var(--v-theme-*)`.

Fix (optional): use `rgb(var(--v-theme-error))` / `--v-theme-warning` /
`--v-theme-on-error` where a theme token expresses the same intent.

## Things checked and found correct (no action)

- **Toolbar → SelectionBar / SelectionMenu split.** Props/emits contracts line up:
  `<SelectionBar>` (`ImageGrid.vue:776`) receives the props it declares,
  `selectionBarRef` is wired and `defineExpose({ openTagInput, openPluginPanel,
  openComfyuiPanel })` is consumed via `selectionBarRef.value?.…`
  (`ImageGrid.vue:5466-5474`). Toolbar no longer holds any selection state; no
  duplicated store writes between Toolbar and SelectionBar. Build + lint confirm no
  broken imports or undefined handlers.
- **`useVersionCheck` single-owner.** TitleBar passes `enabled = Boolean(desktop)`,
  SideBar passes `enabled = !isDesktop`; exactly one owner runs the fetch/interval.
  `isEnabled()` gates `onMounted`, the watcher and `checkForUpdatesNow`;
  `onUnmounted` clears the interval. PEP 440 comparison logic is sound.
- **Desktop graceful degradation.** Desktop-only calls go through
  `window.pixlstashDesktop?.…` optional chaining (TitleBar window controls,
  ComputeSection bridge calls); they no-op in a plain browser. `App.vue`'s
  `onOpenSettings` registration is `?.`-guarded and torn down via `stopOpenSettings`.
- **`useSidebarStore` pin/dock/auto-hide model.** `effectivePinned` /
  `effectiveDocked` / `sidebarVisible` / `sidebarOverlay` are coherent computeds;
  mobile `sidebarForcedHidden` override respected; all localStorage access is
  try/caught. Sidebar prefs are localStorage-only (no server migration involved —
  there is no 0054/0055 sidebar migration in `migrations/versions/`, so the
  "match the migration" concern in the brief does not apply to these stores).
- **`useUserPrefsStore`.** Adds `sidebarWidth` (drag-resizable, documented clamp
  120–300) and `showKeyboardHint`; read-only/share sessions get sensible defaults
  (`themeMode='dark'`, smaller `sidebarThumbnailSize`).
- **`useBreadcrumb`.** Faithful extraction of the in-grid crumb logic; crumbs carry
  IDs (matches the doc's "names aren't unique" principle); consumed by both the
  in-grid nav (browser) and TitleBar (desktop).
- **TitleBar breadcrumb leading `›`.** The unconditional first separator
  (`TitleBar.vue:79`) is intentional — it separates the wordmark from the crumb
  trail and only renders inside `v-if="breadcrumb.length"`. Not a bug.
- **AppearanceSection width buttons.** Full / Dock buttons carry visible text
  labels (`<span class="sidebar-width-label">`), `type="button"`, and active-state
  binding — they are not icon-only and don't need an `aria-label`.
- **Font swap.** `PressStart2P-Regular.ttf` removed, `Tiny5-Regular.ttf` added and
  referenced in `style.css`; build bundles the new face.

## Non-findings (agent false positives, recorded so they aren't re-raised)

- "SelectionBar mixes `props.x` and bare `x` in the template" — both resolve
  identically under `<script setup>`; a style nit at most, not a correctness issue.
- "Width-toggle buttons missing `aria-label`" — they have visible text labels.

## Note for the architecture doc

`docs/frontend_architecture.md` still describes `Toolbar.vue` as the "combined
selection bar + top toolbar" and does not list `SelectionBar.vue`,
`SelectionMenu.vue`, `TitleBar.vue`, `WordmarkLogo.vue`, `ComputeSection.vue`, the
`useBreadcrumb` / `useVersionCheck` composables, or the pin/auto-hide additions to
`useSidebarStore` / `useUserPrefsStore`. Update it as part of this merge (the prior
review raised the same note; still outstanding).

## Fixes applied (2026-06-13)

All Low-severity findings above are fixed; the architecture-doc housekeeping note
is done. `npm run build` → `✓ built in 2.25s`, only the pre-existing >1024 kB
chunk-size warning (unchanged by this diff).

- **L1 — `updateDismissed` inits `true` on a fresh load.** Fixed.
  `frontend/src/composables/useVersionCheck.js:49` — changed the initialiser from
  `localStorage.getItem(VERSION_CHECK_DISMISSED_KEY) === latestVersion.value`
  (which was `null === null` → `true`) to `ref(false)`, with a comment explaining
  the value is only meaningful after a remote version arrives (the fetch path at
  line ~100 recomputes `dismissed === remote`).
- **L2 — "Sidebar Width" description describes the pin control.** Fixed.
  `frontend/src/components/settings/AppearanceSection.vue:159` — replaced
  "Pin or unpin the sidebar from its header." with
  "Show the sidebar at full width or as a narrow icon dock." (matching the
  heading `title`).
- **L3 — `sidebarRefreshPicturesDebounceTimeout` not cleared on unmount.** Fixed.
  `frontend/src/App.vue:1893` — added the missing `clearTimeout` /
  null-reset block in `onBeforeUnmount`, mirroring the two sibling timers.
- **L4 — TitleBar update-dismiss button missing `type` / `aria-label`.** Fixed.
  `frontend/src/components/TitleBar.vue:114` — added `type="button"` and
  `aria-label="Dismiss update alert"` to the `×` dismiss button, matching the
  window-control buttons.
- **Architecture-doc note.** Done. `docs/frontend_architecture.md` updated:
  Toolbar entry rewritten (no longer "combined selection bar + top toolbar");
  added catalogue entries for `SelectionBar.vue`, `SelectionMenu.vue`,
  `TitleBar.vue`, `WordmarkLogo.vue`, `ComputeSection.vue`, `BehaviourSection.vue`,
  `WorkflowsSection.vue`, `SnapshotsSection.vue`; added `useBreadcrumb.js` /
  `useVersionCheck.js` to the composables list; refreshed the `useSidebarStore`
  (pin/dock/auto-hide computeds) and `useUserPrefsStore` (`sidebarWidth`,
  `showKeyboardHint`) store rows; corrected the component-hierarchy diagram (12.1);
  and replaced the stale provide/inject diagram (12.5) with the store-direct model
  (`App.vue` no longer `provide()`s `gridBarState`/`toolbarState`; the toolbar
  components import the Pinia stores directly).
