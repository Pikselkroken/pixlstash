import { test, expect } from '../fixtures/test.js'

// Release plan §3 — Image Grid & Browsing: render, sort, column reflow, and
// search (with history + reset). Changing sort/columns is user-preference
// state, not data, so these stay effectively read-only for the fixture.

test.describe('grid browsing', () => {
  test.beforeEach(async ({ grid }) => {
    await grid.goto()
  })

  test('renders seeded thumbnails', async ({ grid }) => {
    expect(await grid.thumbnails.count()).toBeGreaterThan(0)
  })

  test('lists sort options and reorders when direction flips (§3.2)', async ({ page, grid }) => {
    await grid.waitForThumbnailLoaded()
    const before = await grid.firstThumbnailSrc()

    await grid.openSortMenu()
    const labels = await page.locator('.gb-sort-grid-label').allInnerTexts()
    expect(labels.length).toBeGreaterThanOrEqual(3)
    expect(labels.some((l) => /date/i.test(l))).toBe(true)

    // Flipping the direction reverses the order, so the top-left picture changes.
    await grid.sortDirectionButton.click()
    await page.keyboard.press('Escape')
    await expect.poll(() => grid.firstThumbnailSrc()).not.toBe(before)
  })

  test('reflows the grid when the column count changes (§3.1)', async ({ page, grid }) => {
    const trackCount = () =>
      grid.grid.evaluate(
        (el) => getComputedStyle(el).gridTemplateColumns.split(' ').length,
      )
    const before = await trackCount()

    await grid.openViewMenu()
    // Clicking near an end of the slider commits that extreme (fires @end),
    // guaranteeing a column count different from the default.
    await grid.columnsSlider.click({ position: { x: 4, y: 8 } })
    if ((await trackCount()) === before) {
      const box = await grid.columnsSlider.boundingBox()
      await grid.columnsSlider.click({ position: { x: (box?.width ?? 200) - 4, y: 8 } })
    }
    await page.keyboard.press('Escape')
    await expect.poll(trackCount).not.toBe(before)
  })

  test('search filters, records history, then resets (§3.3)', async ({ page, grid }) => {
    const nonsense = 'zzqxnomatch9173'

    await grid.searchButton.click()
    await expect(grid.searchOverlay).toBeVisible()
    await grid.searchInput.fill(nonsense)
    await page.keyboard.press('Enter')
    await expect(grid.searchOverlay).toBeHidden()
    await expect(grid.thumbnails).toHaveCount(0)

    // Reopen: the term is now in the history; clearing it restores all.
    await grid.searchButton.click()
    await expect(grid.searchOverlay).toBeVisible()
    await expect(
      grid.searchHistoryChips.filter({ hasText: nonsense }).first(),
    ).toBeVisible()

    await grid.searchInput.fill('')
    await page.keyboard.press('Enter')
    await expect(grid.thumbnails.first()).toBeVisible()
    expect(await grid.thumbnails.count()).toBeGreaterThan(0)
  })
})
