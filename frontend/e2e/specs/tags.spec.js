import { test, expect } from '../fixtures/test.js'

// Release plan §5 — Tags. Add a tag through the ImageOverlay's inline input and
// confirm the chip appears immediately, then remove it via its ✕ button and
// confirm it disappears. The test adds and then removes its own unique tag, so
// the shared fixture DB is left exactly as it was found.

const TEMP_TAG = 'zz-e2e-temp-tag'

test.describe('tags', () => {
  test('adds and removes a tag in the overlay (§5)', async ({ grid, overlay }) => {
    await grid.goto()
    await overlay.openFromGrid()

    await overlay.addTag(TEMP_TAG)
    await expect(overlay.tag(TEMP_TAG)).toBeVisible()

    await overlay.removeTag(TEMP_TAG)
    await expect(overlay.tag(TEMP_TAG)).toBeHidden()
  })
})
