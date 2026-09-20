import { test, expect } from '../fixtures/test.js'

// Release plan §3.4 / §11 — Stacks. The fixture has 10 stacks. Expanding one
// opens a tray under its card (design: "The Stack Opens a Tray"), and only one
// stack is open at a time, so the View menu carries a single "Collapse".
// Read-only.

test.describe('stacks', () => {
  test('a stack opens a tray under its own card, and only one at a time', async ({ grid }) => {
    await grid.goto()
    await expect(grid.stackBadges.first()).toBeVisible()
    await expect(grid.stackTray).toHaveCount(0)

    await grid.stackBadges.first().click()
    await expect(grid.stackTray).toBeVisible()
    await expect(grid.stackTrayHead).toContainText('Stack of')
    // The card the tray belongs to is drawn as its tab.
    await expect(grid.stackTabCards).toHaveCount(1)

    // Opening a second stack closes the first: a second tray is unrepresentable.
    const second = grid.stackBadges.nth(1)
    if (await second.isVisible()) {
      await second.click()
      await expect(grid.stackTray).toHaveCount(1)
      await expect(grid.stackTabCards).toHaveCount(1)
    }

    await grid.openViewMenu()
    await grid.collapseStackButton.click()
    await expect(grid.stackTray).toHaveCount(0)
    await expect(grid.stackTabCards).toHaveCount(0)
  })
})
