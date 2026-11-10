import { test, expect } from '../fixtures/test.js'

// Release plan §5 — Tags. Add a tag through the Lightbox's inline input and
// confirm the chip appears immediately, then remove it via its ✕ button and
// confirm it disappears from the applied-tag list. The tag is unique to this
// test; removing it rejects the (synthetic) prediction, so the label lingers
// only in the separate "Rejected Tags" section, which the lightbox.tag() locator
// deliberately excludes — the applied-tag list is restored to how it was found.

const TEMP_TAG = 'zz-e2e-temp-tag'

test.describe('tags', () => {
  test('adds and removes a tag in the lightbox (§5)', async ({ grid, lightbox }) => {
    await grid.goto()
    await lightbox.openFromGrid()

    await lightbox.addTag(TEMP_TAG)
    await expect(lightbox.tag(TEMP_TAG)).toBeVisible()

    await lightbox.removeTag(TEMP_TAG)
    await expect(lightbox.tag(TEMP_TAG)).toBeHidden()
  })
})
