import { test, expect } from '../fixtures/test.js'

// Boolean set operations — Ctrl/Cmd-clicking a second picture set enters
// multi-select and reveals the combine toolbar with Union / Overlap /
// Difference / Unique (XOR) modes. Navigation/preference state only; no data is
// mutated.

test.describe('boolean set operations', () => {
  test('combines multiple sets via the multi-select toolbar', async ({ grid, sidebar }) => {
    await grid.goto()
    expect(await sidebar.setItems.count()).toBeGreaterThanOrEqual(2)

    // First click selects a single set; Ctrl-click adds a second → multi-select.
    await sidebar.setItems.nth(0).click()
    await sidebar.setItems.nth(1).click({ modifiers: ['Control'] })

    await expect(grid.multiSelectToolbar).toBeVisible()
    await expect(grid.multiSelectLabel).toContainText(/sets selected/i)

    const modes = await grid.multiSelectMode.locator('option').allInnerTexts()
    for (const label of ['Union', 'Overlap', 'Difference', 'Unique (XOR)']) {
      expect(modes).toContain(label)
    }

    // Switching the mode keeps the grid mounted (the combined view re-queries).
    await grid.multiSelectMode.selectOption('intersection')
    await expect(grid.grid).toBeVisible()

    await grid.multiSelectClear.click()
    await expect(grid.multiSelectToolbar).toBeHidden()
  })
})
