import { test, expect } from '../fixtures/test.js'

// Release plan §10 — Faces. The fixture has 115 faces, but not every picture
// has one, so we step through the overlay until we land on a picture with
// detected faces, then assert the side-panel crops and the bounding-box
// overlay render. Read-only.

test.describe('faces', () => {
  test('shows face crops and bounding-box overlays', async ({ page, grid, overlay }) => {
    await grid.goto()
    await overlay.openFromGrid()

    // Walk forward until the current picture has at least one face crop.
    let found = false
    for (let i = 0; i < 25; i++) {
      if ((await overlay.faceCrops.count()) > 0) {
        found = true
        break
      }
      await overlay.showNext()
      await page.waitForTimeout(150)
    }
    expect(found, 'expected to find a picture with faces within 25 images').toBe(true)
    await expect(overlay.faceCrops.first()).toBeVisible()

    // Enabling the toggle draws bounding boxes over the image.
    await overlay.faceBboxToggle.click()
    await expect(overlay.faceBboxes.first()).toBeVisible()
  })
})
