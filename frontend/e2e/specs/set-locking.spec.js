import { test, expect } from '../fixtures/test.js'

// Picture-set locking — plan §4 happy path (docs/reviews/2026-07-picture-set-
// locking-plan.md). Drives the real backend (Phase 1 lock enforcement is live):
//
//   navigate the grid to a non-empty set → lock it from the sidebar context menu
//   → the grid lock badge appears on its pictures and the set row shows a lock
//   → a tag edit is blocked (the context-menu Tag action is disabled) → unlock
//   → the badge clears and tagging is enabled again.
//
// One deterministic path. The set is left UNLOCKED (the final assertion), and an
// afterEach unlocks any still-locked set via the API so a mid-test failure can't
// leak lock state into the shared per-run backend DB (see e2e/README.md).

test.describe('picture-set locking', () => {
  // Fail-safe: leave no locked sets behind for the specs that share this DB.
  test.afterEach(async ({ apiContext }) => {
    const res = await apiContext.get('/api/v1/picture_sets')
    if (!res.ok()) return
    const sets = await res.json()
    for (const s of Array.isArray(sets) ? sets : []) {
      if (s?.locked) {
        await apiContext.patch(`/api/v1/picture_sets/${s.id}`, {
          data: { locked: false },
        })
      }
    }
  })

  test('lock freezes tagging and unlock restores it (§4)', async ({
    page,
    grid,
    sidebar,
  }) => {
    await grid.goto()

    // A non-empty set so the grid it filters to actually has pictures to badge.
    expect(await sidebar.setItems.count()).toBeGreaterThanOrEqual(1)
    const set = await sidebar.firstNonEmpty(sidebar.setItems)

    // Navigate the grid to the set and wait for its thumbnails to render.
    await set.click()
    await grid.waitForThumbnailLoaded()

    const lockBadge = page.locator('.thumbnail-lock-badge')

    // Unlocked to start: no badge on any shown picture.
    await expect(lockBadge).toHaveCount(0)

    // ── Lock the set from the sidebar context menu ──────────────────────────
    await sidebar.lockSet(set)

    // The grid lock badge appears (reactive to the locked-sets store refresh
    // toggleSetLock kicks) and the set row shows its trailing lock icon.
    await expect(lockBadge.first()).toBeVisible({ timeout: 10_000 })
    await expect(sidebar.setLockIcons.first()).toBeVisible()

    // A tag edit is blocked: the grid context menu's Tag action is disabled
    // (ImageGridContextMenu gates it on the selection's lock reason).
    await grid.openContextMenu()
    await expect(grid.contextMenuItem('Tag')).toBeDisabled()
    await page.keyboard.press('Escape')
    await expect(grid.contextMenu).toBeHidden()

    // ── Unlock and confirm editing is restored ──────────────────────────────
    await sidebar.unlockSet(set)

    await expect(lockBadge).toHaveCount(0, { timeout: 10_000 })
    await expect(sidebar.setLockIcons).toHaveCount(0)

    await grid.openContextMenu()
    await expect(grid.contextMenuItem('Tag')).toBeEnabled()
    await page.keyboard.press('Escape')
    await expect(grid.contextMenu).toBeHidden()
  })
})
