import { test, expect } from '../fixtures/test.js'

// Statistics sidebar (StatsSidebar.vue) — the toolbar toggle opens a panel with
// Tags / Pictures / Tasks tabs summarising the library. Read-only: toggling the
// panel changes only per-user UI state.

test.describe('statistics sidebar', () => {
  test('toggles the stats sidebar open and closed', async ({ grid }) => {
    await grid.goto()
    await expect(grid.statsToggle).toBeVisible()

    // The sidebar's default open/closed state is a persisted preference, so
    // normalise to open before asserting on its contents.
    if ((await grid.statsContent.count()) === 0) await grid.statsToggle.click()
    await expect(grid.statsContent).toBeVisible()
    // It renders its tabbed sections (Tags / Pictures / Tasks).
    expect(await grid.statsTabs.count()).toBeGreaterThanOrEqual(2)

    // Toggling hides it, and toggling again brings it back.
    await grid.statsToggle.click()
    await expect(grid.statsContent).toBeHidden()
    await grid.statsToggle.click()
    await expect(grid.statsContent).toBeVisible()
  })
})
