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

---

## Coverage gaps / testing debt (risk-based)

Tracked so they aren't forgotten — weighted by blast radius:

- **Snapshots & rollback (v1.5) — HIGHEST RISK, not yet automated.** The
  round-trip (snapshot → mutate → rollback) and *selective metadata restore*
  have no E2E coverage. Data-loss territory; should be the next spec.
- **Bulk operations at scale** — select-all/range and apply-to-many beyond a
  handful of pictures are only smoke-covered.
- **Import of malformed / huge / unsupported files** — graceful handling
  (no crash, nothing silently dropped) is manual-only.
- **Selection/context menu *actions* execute correctly** — `menu-parity.spec.js`
  only compares the *item lists*; it does not click through each action. Once
  #403 is fixed, consider asserting representative actions actually fire.
