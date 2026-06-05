import { test, expect } from '../fixtures/test.js'

// Release plan §3.5 — the right-click context menu. Right-clicking a grid card
// opens the menu and exposes the selection-scoped actions (Tag, Reverse image
// search, Share image …). Read-only: we open the menu and dismiss it without
// invoking any action.

test.describe('context menu', () => {
  test.beforeEach(async ({ grid }) => {
    await grid.goto()
  })

  test('opens on right-click and lists picture actions (§3.5)', async ({ page, grid }) => {
    await grid.openContextMenu()

    await expect(grid.contextMenuItem('Tag')).toBeVisible()
    await expect(grid.contextMenuItem('Reverse image search')).toBeVisible()
    await expect(grid.contextMenuItem('Share image')).toBeVisible()

    await page.keyboard.press('Escape')
    await expect(grid.contextMenu).toBeHidden()
  })
})
