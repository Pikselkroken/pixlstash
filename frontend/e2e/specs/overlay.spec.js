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
})
