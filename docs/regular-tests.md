# PixlStash Regular (Regression) Tests

The checks that must keep passing between releases. This catalogue covers the
**automated Playwright end-to-end suite** (browser journeys against a real
backend). Pure-logic unit tests live with the frontend (Vitest) and the API
suite is pytest — those are not duplicated here.

- **Location:** `frontend/e2e/specs/*.spec.js`
- **Run:** from `frontend/` → `npm run test:e2e` (UI mode: `npm run test:e2e:ui`).
  The harness builds the SPA and boots a throwaway backend against the committed
  `test-data/` fixture (see `frontend/e2e/README.md`).
- **Conventions:** owner session minted in `global-setup.js`; assert
  "at least one" / relative deltas rather than exact counts so fixture pruning
  doesn't break tests; pictures identified by thumbnail `src`
  (`.../thumbnails/<id>.webp`); CSS/ARIA-first selectors centralised in
  `e2e/pages/*`.

Each spec maps to a section of `release-test-plan.md` so the manual checklist
shrinks as automated coverage grows.

> **Numbering note (2026-07):** the release test plan was overhauled — fully
> automated sections were removed and the remaining manual sections renumbered
> (1 Installation, 2 Upgrade, 3 Desktop/Electron, 4 Grid manual remainder,
> 5 Import & background, 6 ComfyUI, 7 Plugins, 8 Performance, 9 Folders,
> 10 Live-update remainder, 11 Review sessions remainder). Spec titles and the
> "plan §" tags below still cite the **legacy** numbering; the plan's preamble
> carries the legacy→spec coverage map.

| Status | Meaning |
|--------|---------|
| ✅ | Automated and passing |
| ❌ | Automated and **currently failing** (tracks a known bug) |
| 📝 | Manual / exploratory only (not yet automated) |

---

## Authentication — `auth.spec.js` (plan §1)
| Test | Covers | Status |
|------|--------|--------|
| keeps the session across a reload | Session survives a page reload; grid re-renders without re-login (§1.3) | ✅ |
| redirects an unauthenticated visitor to login | No session → login screen, no thumbnails leak (§1.1) | ✅ |
| generates an API token from settings | Account tab → create token; row count increments; cleaned up after (§1.2) | ✅ |
| logs out via settings | Logout from an isolated fresh session returns to the login screen (§1.1) | ✅ |

## Image Grid & Browsing — `grid.spec.js`, `grid-browse.spec.js` (plan §3)
| Test | Covers | Status |
|------|--------|--------|
| renders seeded thumbnails (after login) | Grid populates from the fixture | ✅ |
| opens and closes the image overlay | Thumbnail click opens overlay; close button dismisses it | ✅ |
| opens the search overlay from the toolbar | Magnify button reveals search overlay, input focused | ✅ |
| lists sort options and reorders when direction flips | Sort dropdown lists ≥3 options inc. Date; flipping direction reorders (§3.2) | ✅ |
| reflows the grid when the column count changes | Column slider changes `grid-template-columns` track count (§3.1) | ✅ |
| search filters, records history, then resets | Search narrows results, term enters history, clearing restores all (§3.3) | ✅ |

## Selection ▾ / Context Menu Parity — manual (plan §3.5)
| Test | Covers | Status |
|------|--------|--------|
| both menus list the same items for a set of pictures | Selection ▾ dropdown (`.selection-menu-panel`) and right-click context menu (`.image-ctx-menu`) expose identical selection-scoped actions for the same multi-picture selection | 📝 |

> 📝 **Not automated — no parity spec exists.** The right-click context menu is
> automated on its own (see the `context-menu.spec.js` section below); the
> Selection ▾ ↔ context-menu *parity comparison* is manual only.
> **Known gap — [#403](https://github.com/Pikselkroken/pixlstash/issues/403):**
> the context menu has actions the Selection ▾ menu lacks — **Restore from
> snapshot** and **Reverse image search** (plus, for a single selection,
> **Share image** / **Find similar faces**). Keep this ❌ manually until the
> menus are reconciled. (Toolbar.vue documents the Selection menu as "mirrors the
> right-click context menu exactly", so the source intent is parity.)

## Picture Detail (ImageOverlay) — `overlay.spec.js` (plan §4)
| Test | Covers | Status |
|------|--------|--------|
| opens from a thumbnail and closes with the button | Overlay open/close lifecycle | ✅ |
| navigates next/previous with arrow keys | →/← move between grid images in the overlay | ✅ |
| closes with Escape | Escape dismisses the overlay | ✅ |

## Faces — `faces.spec.js` (plan §10)
| Test | Covers | Status |
|------|--------|--------|
| shows face crops and bounding-box overlays | Face detection results render as crops + bbox overlays | ✅ |

## Stacks — `stacks.spec.js` (plan §11)
| Test | Covers | Status |
|------|--------|--------|
| expands and collapses stacks from the View menu | Expand-all / collapse-all stack controls in the View menu | ✅ |

## Tags — `tags.spec.js` (plan §5)
| Test | Covers | Status |
|------|--------|--------|
| adds and removes a tag in the overlay | Add a tag via the inline input; remove it via its ✕ button (chip appears/disappears with no reload) | ✅ |

## Star rating — `rating.spec.js` (plan §6)
| Test | Covers | Status |
|------|--------|--------|
| sets a rating that persists across a reload | Click the Nth star in the overlay; the score round-trips to the backend and survives a reload | ✅ |
| a grid star click saves, survives a reload, and matches the overlay | The compact hover star badge on a thumbnail card: click the Nth star, reload — the grid badge still shows N and the overlay for the same picture agrees (grid↔overlay sync). Uses the 2nd card so the two tests never share a picture | ✅ |

## Picture import — `import.spec.js` (plan §2, partial)
| Test | Covers | Status |
|------|--------|--------|
| dragging files over the grid raises the import overlay; leaving clears it | A synthetic DataTransfer with a real JPEG File dispatched on the grid scroll wrapper raises the "Drop files here to import" overlay; dragleave clears it | ✅ |

> ⚠️ **Drop → import completes is NOT automatable in this harness:**
> `POST /pictures/import` returns 400 "Face worker is not running" and the e2e
> backend boots with `disable_background_workers: true`. The import-completion
> half of legacy §2 stays in the manual plan (Import & background processing)
> until the harness grows a worker-enabled mode or a test hook.

## Export — `export.spec.js` (plan §15)
| Test | Covers | Status |
|------|--------|--------|
| exports 3 selected pictures to a ZIP containing exactly 3 image files | Ctrl-click 3 cards → toolbar Export panel (captions "none") → Export → the downloaded ZIP's central directory holds exactly 3 image entries | ✅ |
| exports a picture set to a ZIP matching the sidebar count | Open a non-empty set, export the current view — ZIP image-entry count equals the set's sidebar count badge | ✅ |

## Tag predictions — `tag-predictions.spec.js` (plan §14)
| Test | Covers | Status |
|------|--------|--------|
| a deleted tag drops to a prediction chip and Confirm restores it | Discovers (via API) a picture whose applied tag has a live prediction ≥ 0.35, deep-links its overlay (`?overlay=<id>`), deletes the tag → the label drops to the "Rejected Tags" prediction chip with a confidence badge; the chip's ✓ confirms it back into the applied list (backend-verified). Round trip restores the fixture | ✅ |

> Prediction *generation* after a fresh import needs background workers —
> manual (plan section 5). Note the UI has no separate "Tag Predictions"
> accept list any more: only REJECTED predictions ≥ 0.3 render, as the
> "Rejected Tags" chips; a synthetic manual reject gets confidence 0.0 and
> stays hidden by design.

## Picture Sets / Projects / Characters — `entities.spec.js` (plan §7/§8/§9)
| Test | Covers | Status |
|------|--------|--------|
| filters the grid to a picture set (§7) | Sidebar set row → `/set/:id`, row goes active, grid renders | ✅ |
| filters the grid to a character (§9) | Sidebar character row → `/character/:id`, row goes active, grid renders | ✅ |
| opens a project from the Projects tab (§8) | Projects tab → project row → `/project/:id` | ✅ |

## Context menu — `context-menu.spec.js` (plan §3.5)
| Test | Covers | Status |
|------|--------|--------|
| opens on right-click and lists picture actions | Right-click a card opens `.image-ctx-menu` exposing Tag / Reverse image search / Share image; Escape dismisses it | ✅ |

## Statistics sidebar — `stats.spec.js`
| Test | Covers | Status |
|------|--------|--------|
| toggles the stats sidebar open and closed | Toolbar chart-bar toggle shows/hides `.stats-sidebar-content` with its Tags/Pictures/Tasks tabs | ✅ |

## Boolean set operations — `set-operations.spec.js`
| Test | Covers | Status |
|------|--------|--------|
| combines multiple sets via the multi-select toolbar | Ctrl-click a second set reveals the combine toolbar with Union / Overlap / Difference / Unique (XOR); clearing dismisses it | ✅ |

## Picture-set locking — `set-locking.spec.js` (plan §4)
| Test | Covers | Status |
|------|--------|--------|
| lock freezes tagging and unlock restores it (§4) | Lock a non-empty set from the sidebar → grid lock badge + set-row lock icon appear and the context-menu Tag action is disabled; unlock → badge/icon clear and Tag is enabled again (`afterEach` unlocks any leaked lock) | ✅ |

## Sharing — `sharing.spec.js`
| Test | Covers | Status |
|------|--------|--------|
| creates a read-only share link for a picture | Context menu → Share image → Create Link mints a read-only URL shown for copying | ✅ |

## Snapshots — `snapshots.spec.js`
| Test | Covers | Status |
|------|--------|--------|
| lists restore points with a restore action (list-only) | Settings → Snapshots lists ≥1 restore point, each offering a Restore action (rollback itself is **not** clicked — it would rewrite the shared DB) | ✅ |

## Grid live-update — `grid-own-change-no-pill.spec.js`, `grid-external-change-pill.spec.js`, `grid-injection.spec.js`, `grid-overlay-deferral.spec.js` (plan §19)
WebSocket-driven grid refresh and the two pills. The e2e harness simulates an
*external* change by sending requests with **no `X-Client-Id`**, and *own*
changes by driving the UI / sending the tab's own client id; floods are fired
via the guarded `POST /api/v1/test-hooks/ws-event` injection endpoint. Backs the
fixes for [#499](https://github.com/Pikselkroken/pixlstash/issues/499) and
[#500](https://github.com/Pikselkroken/pixlstash/issues/500).

| Test | Covers | Status |
|------|--------|--------|
| own change (own `X-Client-Id`) raises no pill | Mutating with the tab's own client id reconciles silently — no pill (§19.1, #499) | ✅ |
| external change raises the right pill | No-client-id mutation → external; add → "New pictures", update → "View changed externally"; click loads it (§19.2) | ✅ |
| injection: own-origin suppressed, external add raises pill | `ws-event` injection with matching origin is suppressed; external `added` raises "New pictures" (§19.1/§19.2) | ✅ |
| flood is coalesced | 100 external events → bounded number of grid refetches, not one per id (§19.3, #500) | ✅ |
| overlay defers external changes | External change while overlay open → no pill; deferred reconcile on close (§19.4) | ✅ |

## Review Sessions — `review-board.spec.js`, `review-session.spec.js` (plan §20)

The tag-review redesign: a tag-health board (landing), first-class review
sessions in a rail, and binary/pair decision cards. Drives the real backend.
The board is served from the `tag_health` cache, so the board spec's
`beforeEach` builds it through the API (`POST /api/v1/tag_health/rebuild`, poll
`GET /api/v1/tag_health` until `building=false`) before opening the overlay —
the new-review dialog's tag chips ARE the health rows, so the cache must exist.
Page object: `e2e/pages/ReviewSessions.js` (`reviews` fixture). The fixture
vault's real CLIP embeddings produce live suspects (probed: shirt/smile/hand 3,
face/formal/grin 2, man/beard/closed-eyes 1), and its `PictureLikeness` +
`PictureStack` pairs give ~24 tags a non-zero mismatch signal — so the board and
the near-neighbour scan both run end-to-end against committed data. The store
logic (accept/dismiss/swap mapping, tally deltas, queue sort, optimistic
mutations) is unit-covered in `src/stores/useReviewSessionsStore.test.js`.

| Test | Covers | Status |
|------|--------|--------|
| opens from the toolbar and renders the ranked board | Toolbar `Review and fix tags` → `.rs-overlay`; board title + the redesigned columns (Tag / Est. fixes / Est. wrong / Est. missing / Mismatch / Why it ranks here); ≥1 tag row | ✅ |
| the persistent rebuild control is visible in the header (Spec B) | The board header always exposes its rebuild control | ✅ |
| the Why column is never blank for a row with a ranking signal | A row with a ranking signal always shows a "Why it ranks here" explanation | ✅ |
| the filter input narrows the visible rows | `/`-focusable filter narrows rows to matches and restores on clear | ✅ |
| a no-match filter shows the empty state | `.rs-board-empty` "No tags match…" | ✅ |
| the anomalies-only toggle flips its pressed state | `aria-pressed` toggles false↔true | ✅ |
| a sortable header toggles the active sort | Clicking "Est. wrong" header goes active; rows still render | ✅ |
| the sort dropdown re-orders without emptying the board | Sort by tag / missing keeps rows | ✅ |
| a row with a mismatch signal shows Start review | A mismatch-flagged tag ("shirt") exposes the "Start review" action | ✅ |
| creating a review opens a session with a scan receipt and progress | Dialog → "Scan & create" → session view, receipt ("Scanned N · N suspects"), rail done/found, Undo disabled at start | ✅ |
| binary Yes/No map to keep/remove; tally + backend receipt track it | remove-dir No=remove / Yes=keep; tally + `GET /reviews/{id}` receipt agree; focus advances to the re-keyed card; Undo enabled after a decision | ✅ |
| Escape closes the review first (back to tag health); a second Escape closes the overlay | In a session, Escape returns to the tag-health board; a second Escape dismisses the overlay | ✅ |
| Undo reverses the last decision and decrements the tally | Undo (net counter) decrements the tally and re-disables when the stack empties; backend receipt returns to 0 | 📝 fixme |
| Skip removes the card with no decision and is reported separately | Skip → `.rs-tally-skipped` + rail "N skipped"; receipt removed/added/kept unchanged, skipped++ | 📝 fixme |
| keyboard Y / N / S / U drive decisions and focus stays on the card | Keyboard parity with the buttons; focus stays on `.rs-card` | 📝 fixme |
| working through the queue reaches completion and archives to a receipt | Decide all → completion state → Archive → `.rs-archived` receipt; backend status ARCHIVED | 📝 fixme |

**✅ BUG-RS-1 — RESOLVED.** The session card used to never render in a real
browser: the moment the suggestions queue loaded, `ReviewSessionView` crashed
with `TypeError: Cannot read properties of undefined (reading 'el')` in Vue's
`patchBlockChildren` and stayed stuck on "Loading…". Root cause was a `:key`
collision between the card's `v-else` branch and the compiler's numeric auto-key
for the sibling "Loading" `v-if` (only surfacing in production block patching);
fixed by namespacing the key (`card-${current.id}`) in `ReviewSessionView.vue`.
The three active specs above (create, binary decide, Escape-close) are the
authoritative regression guard and run green against the production build. The
four rows marked **📝 fixme** stay `test.fixme`'d for reasons **unrelated to
BUG-RS-1** — each encodes a behaviour that does not yet match the implementation
(single-suspect queue emptying, Skip tally, keyboard focus, queue completion/
archive) and needs QA+dev reconciliation; see the per-test `FIXME (not BUG-RS-1)`
notes in the spec. Un-`fixme` each as its behaviour is settled.

---

## Coverage gaps / testing debt (risk-based)

Tracked so they aren't forgotten — weighted by blast radius:

- **Snapshots rollback & selective restore (v1.5) — HIGH RISK, partially
  automated.** `snapshots.spec.js` now asserts the restore-point list renders
  with a Restore action, but the destructive round-trip (snapshot → mutate →
  rollback) and *selective metadata restore* are still uncovered — clicking
  Restore would rewrite the shared fixture DB, so it needs an isolated backend.
  Data-loss territory; should be the next spec.
- **Bulk operations at scale** — select-all/range and apply-to-many beyond a
  handful of pictures are only smoke-covered.
- **Import completion blocked by the worker requirement.** The import endpoint
  hard-requires the face worker, and the harness disables workers — so
  drop→import, malformed/huge/unsupported file handling, and everything
  downstream of an import (progress UI, caption/tag/face generation) is
  manual-only. A worker-enabled harness mode (or an import test hook) would
  unlock a large block of legacy §2/§16 automation at once.
- **Infinite scroll needs a bigger fixture.** The ~110-picture fixture renders
  in a single page (108 thumbnails mount eagerly), so "scroll loads more" has
  nothing to assert; it lives in the manual Performance section. A multi-page
  fixture would make it automatable.
- **Entity creation flows (projects/sets/characters) are navigation-only.**
  `entities.spec.js` navigates existing entities; creating a project with
  child sets/characters and assigning pictures through the UI (legacy §8/§9)
  is not automated.
- **Image plugins** — applying a plugin and asserting an output picture
  appears is automatable; the "visibly blurrier/brighter" judgement is not.
  Currently fully manual (plan section 7).
- **Selection ▾ ↔ context-menu parity is not automated.** `context-menu.spec.js`
  opens the right-click menu and lists its actions, but no spec compares the two
  menus' *item lists*, and none clicks through each action. Tracked as the #403
  parity gap (see the "Selection ▾ / Context Menu Parity" section above). Once
  #403 is fixed, consider a parity spec plus asserting representative actions fire.
- **Review Sessions — partial loop automation + manual-only signals (plan §20).**
  The board is automated (`review-board.spec.js`, 9 cases). The session loop is
  **partly automated**: create, binary decide, and Escape-close run green
  (BUG-RS-1 is resolved); four further cases (Undo, Skip, keyboard, queue
  completion/archive) stay `test.fixme`'d for reasons unrelated to BUG-RS-1 and
  are tracked separately. Separately, several board/creation
  paths are **not exercisable against the committed fixture** and stay manual
  (see release-test-plan §20): `est_wrong`/`est_missing` columns and
  auto-resolvable/bulk-accept (the fixture's predictions never disagree with
  ground truth ≥0.9/≤0.1, so these are always 0), the **"no model signal"** row
  (every fixture tag has predictions), **model-disputes** count, the
  build-progress bar (the small vault rebuilds synchronously — the bar never
  visibly fills), staleness / refresh NEW-badge (needs a real import or tagger
  run mid-session), and gamification celebration/sticker animations (assert XP
  counters + sticker shelf, not animation frames). A seeded fixture (predictions
  that disagree, a tag outside the tagger vocabulary, a bigger vault) would let
  most of these become e2e specs.
- **Grid live-update — manual-only paths (plan §19).** The four `grid-*` specs
  cover the deterministic core (own vs external, pill choice, flood coalescing,
  overlay deferral). These are **not yet automated** and stay manual:
  - *Real bulk work with background workers ON (§19.3, #500)* — the e2e backend
    runs `disable_background_workers: true`, so worker tag/quality/smart-score
    events never fire. The injection spec proves frontend coalescing under a
    synthetic flood, but the real worker-driven path needs a live server run.
  - *Real two-tab / two-device observation (§19.2)* — automated coverage uses
    the no-client-id lever to fake "external"; genuine concurrent same-owner
    clients are observed manually.
  - *Network reconnect (§19.5)* — DevTools offline/online and the **known gap**
    that events during downtime are lost (no replay) are not in the harness.
  - *Storage-denied `clientId` (§19.6, #501)* — private-window `sessionStorage`
    denial regenerates the client id per reload; this narrow misclassification
    edge is manual. See `docs/reviews/2026-06-grid-refresh-cleanup-plan.md` §7.
