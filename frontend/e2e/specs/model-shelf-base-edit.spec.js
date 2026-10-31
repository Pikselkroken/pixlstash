// The Base column's inline field fits the cell it replaces.
//
// BaseModelInput has two template roots, so ModelShelf's scoped rule for
// `.shelf-row-base-edit` never matched it and the field drew as a bare browser
// input: no padding, wider than the column, its focus ring cut off by the
// cell's `overflow: hidden`. jsdom computes no layout, so this is checked here.

import { test, expect } from '../fixtures/test.js'
import { ModelShelfPage } from '../pages/ModelShelfPage.js'

test('the inline base-model field is styled and fits its cell', async ({ page }) => {
  const shelf = new ModelShelfPage(page)
  await shelf.goto()
  const cell = shelf.row('RealVisXL_V4.safetensors').locator('.shelf-col--base')
  await cell.locator('.shelf-base-text, .shelf-chip--none').first().dblclick()
  const field = cell.locator('.shelf-row-base-edit')
  await expect(field).toBeFocused()

  const m = await field.evaluate((el) => {
    const f = el.getBoundingClientRect()
    const c = el.parentElement.getBoundingClientRect()
    return {
      field: [f.left, f.right],
      cell: [c.left, c.right],
      padding: parseFloat(getComputedStyle(el).paddingLeft),
    }
  })
  // The shelf's own rule reached the input: a bare one has no side padding.
  expect(m.padding, JSON.stringify(m)).toBeGreaterThan(0)
  // Inside the cell on both sides, so the overflow clip leaves the ring's
  // stroke whole (it is drawn inward; only the soft outer glow meets the edge).
  expect(m.field[0], JSON.stringify(m)).toBeGreaterThanOrEqual(m.cell[0])
  expect(m.field[1], JSON.stringify(m)).toBeLessThanOrEqual(m.cell[1])
})
