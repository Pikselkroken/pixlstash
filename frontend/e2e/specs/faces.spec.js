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

    // The pointed-at crop's ring is whole: the Info column scrolls, so it
    // clips at its padding box, and the crop sits in its top right corner.
    const ring = 3 // px, `.rail-face--ringed` / hover in RailPicture.vue
    const [crop, clip] = await Promise.all([
      lightbox.faceCrops.first().evaluate((el) => el.getBoundingClientRect().toJSON()),
      page.locator('.lightbox-sidebar-group').evaluate((el) => {
        const r = el.getBoundingClientRect()
        return {
          left: r.left + el.clientLeft,
          top: r.top + el.clientTop,
          right: r.left + el.clientLeft + el.clientWidth,
        }
      }),
    ])
    expect(crop.top - ring, 'ring cut at the top').toBeGreaterThanOrEqual(clip.top)
    expect(crop.left - ring, 'ring cut at the left').toBeGreaterThanOrEqual(clip.left)
    expect(crop.right + ring, 'ring cut at the right').toBeLessThanOrEqual(clip.right)
  })
})
