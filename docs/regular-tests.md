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

## Selection ▾ / Context Menu Parity — `menu-parity.spec.js` (plan §3.5)
| Test | Covers | Status |
|------|--------|--------|
| both menus list the same items for a set of pictures | Selection ▾ dropdown (`.selection-menu-panel`) and right-click context menu (`.image-ctx-menu`) expose identical selection-scoped actions for the same multi-picture selection | ❌ |

> ❌ **Known failure — [#403](https://github.com/Pikselkroken/pixlstash/issues/403).**
> The context menu has actions the Selection ▾ menu lacks: **Restore from
> snapshot** and **Reverse image search** (plus, for a single selection,
> **Share image** / **Find similar faces**). The spec is intentionally red to
> keep this parity gap visible; it goes green once the menus are reconciled.
> (Toolbar.vue documents the Selection menu as "mirrors the right-click context
> menu exactly", so the source intent is parity.)

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
- **Import of malformed / huge / unsupported files** — graceful handling
  (no crash, nothing silently dropped) is manual-only.
- **Selection/context menu *actions* execute correctly** — `menu-parity.spec.js`
  only compares the *item lists*; it does not click through each action. Once
  #403 is fixed, consider asserting representative actions actually fire.
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
