import { test, expect } from '../fixtures/test.js'

// #2139: select tags on one picture, Copy, step to the next picture and press
// Ctrl+V with the sidebar closed, so nothing in the Tags panel has focus. The
// tags are unique to this test and are removed again from both pictures.

const TAGS = ['zz-e2e-copy-a', 'zz-e2e-copy-b']
const SHOTS = process.env.TAG_COPY_SHOTS

test.describe('tag copy and paste', () => {
  test('copies selected tags and pastes them on the next picture (#2139)', async ({
    grid,
    overlay,
    page,
  }) => {
    const shot = async (name) => {
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png` })
    }
    await grid.goto()
    await overlay.openFromGrid()
    for (const tag of TAGS) {
      await overlay.addTag(tag)
      await expect(overlay.tag(tag)).toBeVisible()
    }

    // A click selects a chip; the selection bar offers Copy.
    for (const tag of TAGS) await overlay.tag(tag).click()
    const bar = page.locator('.tag-selection-bar')
    await expect(bar).toContainText('2 selected')
    await shot('1-selected')
    // The x of one selected chip lights the x of every selected chip.
    await overlay.tag(TAGS[0]).locator('.tag-delete-btn').hover()
    await expect(page.locator('.overlay-tag--remove-hot')).toHaveCount(2)
    await shot('1b-remove-hot')
    await bar.hover()
    await bar.getByRole('button', { name: 'Copy' }).click()
    await expect(bar.getByRole('button', { name: 'Clear selection' })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(bar).toBeHidden()
    await expect(page.locator('.tag-paste-btn')).toContainText('Paste 2')
    await shot('2-copied')

    // Next picture, rail closed: Ctrl+V still pastes on it.
    await overlay.toggleRail()
    await overlay.next.click()
    await page.keyboard.press('Control+v')
    await overlay.toggleRail()
    for (const tag of TAGS) await expect(overlay.tag(tag)).toBeVisible()
    await shot('3-pasted')

    // Put both pictures back as they were.
    for (const tag of TAGS) await overlay.removeTag(tag)
    await overlay.prev.click()
    for (const tag of TAGS) await overlay.removeTag(tag)
    for (const tag of TAGS) await expect(overlay.tag(tag)).toBeHidden()
  })
})
