import { test, expect } from '../fixtures/test.js'

// Release plan §4 — Picture Detail (ImageOverlay): open, keyboard next/prev,
// Escape close. Read-only: opening/closing the lightbox mutates no data.

test.describe('image overlay', () => {
  test.beforeEach(async ({ grid }) => {
    await grid.goto()
  })

  test('opens from a thumbnail and closes with the button', async ({ overlay }) => {
    await overlay.openFromGrid()
    await expect(overlay.root).toBeVisible()
    await overlay.close()
    await expect(overlay.root).toBeHidden()
  })

  test('navigates next/previous with arrow keys', async ({ overlay }) => {
    await overlay.openFromGrid()
    // Arrow nav swaps the displayed image but does not rewrite the URL, so we
    // track the full-image <img> src (unique per picture) instead.
    const first = await overlay.mainImage.getAttribute('src')
    expect(first).toBeTruthy()

    await overlay.showNext()
    await expect.poll(() => overlay.mainImage.getAttribute('src')).not.toBe(first)
    const second = await overlay.mainImage.getAttribute('src')

    await overlay.showPrev()
    await expect.poll(() => overlay.mainImage.getAttribute('src')).not.toBe(second)
  })

  test('closes with Escape', async ({ page, overlay }) => {
    await overlay.openFromGrid()
    await page.keyboard.press('Escape')
    await expect(overlay.root).toBeHidden()
  })

  // The lightbox opens inside the frame (#2163): over the centre column and in
  // the rail's place, never over the title bar or the sidebar.
  test('moves nothing outside the centre column', async ({ page, grid, overlay }) => {
    const box = (selector) =>
      page.locator(selector).first().evaluate((el) => {
        const r = el.getBoundingClientRect()
        return { x: r.x, y: r.y, w: r.width, h: r.height }
      })
    await overlay.openFromGrid()
    // The pane is open (the page object opens it); close the lightbox and open
    // the grid's rail, which has its own flag, to measure the frame as the
    // grid draws it.
    await overlay.close()
    if ((await grid.statsToggle.getAttribute('aria-pressed')) !== 'true') {
      await grid.statsToggle.click()
    }
    await expect(grid.statsToggle).toHaveAttribute('aria-pressed', 'true')
    await page.evaluate(() =>
      Promise.all(
        document
          .getAnimations()
          .filter((a) => a instanceof CSSTransition)
          .map((a) => a.finished.catch(() => null)),
      ),
    )
    const titleBar = await box('.titlebar')
    const sidebar = await box('.sidebar-shell')
    const rail = await box('.stats-sidebar')
    const toolbar = await box('.selection-bar-overlay')

    await overlay.openFromGrid()
    expect(await box('.titlebar')).toEqual(titleBar)
    expect(await box('.sidebar-shell')).toEqual(sidebar)
    // The lightbox's pane stands exactly where the rail stood, and its
    // toolbar on the grid toolbar's line.
    expect(await box('.image-overlay .overlay-sidebar')).toEqual(rail)
    const bar = await box('.image-overlay .overlay-topbar')
    expect(bar.y).toBe(toolbar.y)
    expect(bar.h).toBe(toolbar.h)

    await overlay.close()
    expect(await box('.stats-sidebar')).toEqual(rail)
    expect(await box('.sidebar-shell')).toEqual(sidebar)
  })
})
