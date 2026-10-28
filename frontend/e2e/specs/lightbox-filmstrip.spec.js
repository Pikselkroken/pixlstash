import { test, expect } from '../fixtures/test.js'

// The lightbox filmstrip is a band under the picture that scrolls natively and
// on its own: the wheel never changes the picture, a click never moves the
// strip, and the strip only follows the picture on screen when that would
// leave view. Read-only.

/** Where the highlighted thumb sits in the strip's viewport, in px. */
function activePlacement(page) {
  return page.evaluate(() => {
    const view = document.querySelector('.filmstrip-viewport').getBoundingClientRect()
    const thumb = document.querySelector('.filmstrip-thumb--active')?.getBoundingClientRect()
    if (!thumb) return null
    return {
      left: thumb.left - view.left,
      right: view.right - thumb.right,
      centreOffset: Math.abs(thumb.left + thumb.width / 2 - (view.left + view.width / 2)),
    }
  })
}

/** The thumb drawn at a point across the strip's viewport, or null. */
function thumbAt(page, fraction) {
  return page.evaluate((f) => {
    const view = document.querySelector('.filmstrip-viewport').getBoundingClientRect()
    const hit = document.elementFromPoint(view.left + view.width * f, view.top + view.height / 2)
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

  test('scrolls on its own and keeps the picture on screen in view', async ({ page, lightbox }) => {
    await lightbox.openFromGrid()
    const viewport = page.locator('.filmstrip-viewport')
    await expect(page.locator('.filmstrip-thumb--active')).toBeVisible()
    const scrollLeft = () => viewport.evaluate((el) => el.scrollLeft)
    const shown = await lightbox.mainImage.getAttribute('src')

    // The wheel scrolls the strip sideways, by the pixel, and leaves the
    // picture alone.
    await viewport.hover()
    await page.mouse.wheel(0, 900)
    await expect.poll(scrollLeft).toBeGreaterThan(0)
    expect(await lightbox.mainImage.getAttribute('src')).toBe(shown)
    // Wherever it lands, the strip is drawn: no blank stretch.
    for (const f of [0.05, 0.5, 0.95]) expect(await thumbAt(page, f)).not.toBeNull()

    // A click on a thumb in view goes to it and moves nothing.
    const before = await scrollLeft()
    const target = await thumbAt(page, 0.5)
    expect(target).not.toBeNull()
    await page.mouse.click(target.x, target.y)
    await expect.poll(() => lightbox.mainImage.getAttribute('src')).not.toBe(shown)
    expect(await scrollLeft()).toBe(before)

    // Stepping on keeps the picture on screen whole in view.
    for (let i = 0; i < 12; i += 1) {
      await page.keyboard.press('ArrowRight')
      await expect
        .poll(async () => {
          const at = await activePlacement(page)
          return at && at.left >= -1 && at.right >= -1
        })
        .toBe(true)
    }

    // Scrolled far from it, the next step centres it.
    await viewport.hover()
    await page.mouse.wheel(0, 8000)
    await expect
      .poll(async () => {
        const at = await activePlacement(page)
        return at === null || at.left < 0 || at.right < 0
      })
      .toBe(true)
    await page.keyboard.press('ArrowRight')
    await expect
      .poll(async () => (await activePlacement(page))?.centreOffset ?? Infinity)
      .toBeLessThan(2)
    // And it stays there. The arrow's own native scroll is smooth and lands
    // after the jump, so a poll that reads early passes while the strip ends
    // up 40px off; that drift failed only on CI's slower runners.
    await page.waitForTimeout(750)
    expect((await activePlacement(page))?.centreOffset ?? Infinity).toBeLessThan(2)
  })
})
