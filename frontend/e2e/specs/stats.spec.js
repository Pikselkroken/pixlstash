import { test, expect } from '../fixtures/test.js'

// Statistics sidebar (StatsSidebar.vue) — the toolbar toggle opens a panel with
// Tags / Pictures tabs summarising the library (no Tasks: that is the title
// bar's pill). Read-only: toggling the
// panel changes only per-user UI state.

test.describe('statistics sidebar', () => {
  test('toggles the stats sidebar open and closed', async ({ grid }) => {
    await grid.goto()
    await expect(grid.statsToggle).toBeVisible()

    // The sidebar's default open/closed state is a persisted preference, so
    // normalise to open before asserting on its contents.
    if ((await grid.statsContent.count()) === 0) await grid.statsToggle.click()
    await expect(grid.statsContent).toBeVisible()
    // It renders its tabbed sections, Tags and Pictures, and no Tasks tab.
    await expect(grid.statsTabs).toHaveText([/Tags/, /Pictures/])

    // Toggling hides it, and toggling again brings it back.
    await grid.statsToggle.click()
    await expect(grid.statsContent).toBeHidden()
    await grid.statsToggle.click()
    await expect(grid.statsContent).toBeVisible()
  })
})
