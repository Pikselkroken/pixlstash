import { test, expect } from '../fixtures/test.js'

// Release plan §10 — Faces. The fixture has 115 faces, but not every picture
// has one, so we step through the lightbox until we land on a picture with
// detected faces, then assert the side-panel crops and the bounding-box
// lightbox render. Read-only.
//
// Showing faces is all this spec checks. Assigning a face and creating a
// person from one are lightbox edits, covered by #2326.

test.describe('faces', () => {
  test('shows face crops and bounding-box overlays', async ({ page, grid, lightbox }) => {
    await grid.goto()
    await lightbox.openFromGrid()

    // Walk forward until the current picture has at least one face crop.
    let found = false
    for (let i = 0; i < 25; i++) {
      if ((await lightbox.faceCrops.count()) > 0) {
        found = true
        break
      }
      await lightbox.showNext()
      await page.waitForTimeout(150)
    }
    expect(found, 'expected to find a picture with faces within 25 images').toBe(true)
    await expect(lightbox.faceCrops.first()).toBeVisible()

    // No box until asked: pointing at the first face's chip draws its box.
    await expect(lightbox.faceBboxes.first()).toBeHidden()
    await lightbox.faceChips.first().hover()
    await expect(lightbox.faceBboxes.first()).toBeVisible()
  })
})
