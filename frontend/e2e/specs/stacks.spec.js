import { test, expect } from '../fixtures/test.js'

// Release plan §3.4 / §11 — Stacks. The fixture has 10 stacks. Driving the
// per-thumbnail badge is occluded by the stats sidebar at the right edge, so
// we exercise the same expand/collapse behaviour through the View menu's
// "Expand all" / "Collapse all" controls, asserting on the .image-card-stack-
// expanded class the grid applies to expanded leaders. Read-only.

test.describe('stacks', () => {
  test('expands and collapses stacks from the View menu', async ({ page, grid }) => {
    await grid.goto()
    const expandedCards = page.locator('.image-card-stack-expanded')

    await grid.openViewMenu()
    await grid.expandAllStacksButton.click()
    await expect(expandedCards.first()).toBeVisible()

    await grid.collapseAllStacksButton.click()
    await expect(expandedCards).toHaveCount(0)
  })
})
