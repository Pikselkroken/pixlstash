import { test, expect } from '../fixtures/test.js'

// The lightbox filmstrip scrolls natively and on its own: the wheel never
// changes the picture, a click never moves the strip, and the strip only
// follows the picture on screen when that would leave view. Read-only.

/** Where the highlighted thumb sits in the strip's viewport, in px. */
function activePlacement(page) {
  return page.evaluate(() => {
    const view = document.querySelector('.filmstrip-viewport').getBoundingClientRect()
    const thumb = document
      .querySelector('.filmstrip-thumb-image-active')
      ?.getBoundingClientRect()
    if (!thumb) return null
    return {
      top: thumb.top - view.top,
      bottom: view.bottom - thumb.bottom,
      centreOffset: Math.abs(thumb.top + thumb.height / 2 - (view.top + view.height / 2)),
      size: thumb.height,
    }
  })
}

/** The thumb drawn at a point down the strip's viewport, or null. */
function thumbAt(page, fraction) {
  return page.evaluate((f) => {
    const view = document.querySelector('.filmstrip-viewport').getBoundingClientRect()
    const hit = document.elementFromPoint(view.left + view.width / 2, view.top + view.height * f)
    const thumb = hit?.closest('.filmstrip-thumb')
    if (!thumb) return null
    const r = thumb.getBoundingClientRect()
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 }
  }, fraction)
}

test.describe('lightbox filmstrip', () => {
  test.beforeEach(async ({ grid }) => {
    await grid.goto()
  })

  test('scrolls on its own and keeps the picture on screen in view', async ({ page, overlay }) => {
    await overlay.openFromGrid()
    const viewport = page.locator('.filmstrip-viewport')
    await expect(page.locator('.filmstrip-thumb-image-active')).toBeVisible()
    const scrollTop = () => viewport.evaluate((el) => el.scrollTop)
    const shown = await overlay.mainImage.getAttribute('src')

    // The wheel scrolls the strip, by the pixel, and leaves the picture alone.
    await viewport.hover()
    await page.mouse.wheel(0, 900)
    await expect.poll(scrollTop).toBeGreaterThan(0)
    expect(await overlay.mainImage.getAttribute('src')).toBe(shown)
    // Wherever it lands, the strip is drawn: no blank stretch.
    for (const f of [0.05, 0.5, 0.95]) expect(await thumbAt(page, f)).not.toBeNull()

    // A click on the second thumb in view goes to it and moves nothing.
    const before = await scrollTop()
    const slot = (await activePlacement(page))?.size ?? 0
    const second = await thumbAt(page, 0)
    expect(second).not.toBeNull()
    await page.mouse.click(second.x, second.y + (slot || 80))
    await expect.poll(() => overlay.mainImage.getAttribute('src')).not.toBe(shown)
    expect(await scrollTop()).toBe(before)

    // Stepping down keeps the picture on screen whole in view.
    for (let i = 0; i < 12; i += 1) {
      await page.keyboard.press('ArrowDown')
      await expect
        .poll(async () => {
          const at = await activePlacement(page)
          return at && at.top >= -1 && at.bottom >= -1
        })
        .toBe(true)
    }

    // Scrolled far from it, the next step centres it.
    await viewport.hover()
    await page.mouse.wheel(0, 8000)
    await expect
      .poll(async () => {
        const at = await activePlacement(page)
        return at === null || at.top < 0 || at.bottom < 0
      })
      .toBe(true)
    await page.keyboard.press('ArrowDown')
    await expect
      .poll(async () => (await activePlacement(page))?.centreOffset ?? Infinity)
      .toBeLessThan(2)
  })
})
