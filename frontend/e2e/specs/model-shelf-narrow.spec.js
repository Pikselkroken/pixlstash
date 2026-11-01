// The model list on a narrow panel: Sets goes below 820px, the thumbnail slot
// below 740px, and past that the fixed columns shrink to their floors while
// Name holds its own. Container queries and flex shrink are layout, which
// jsdom does not compute, so this is checked here.
//
// The list panel is sized directly rather than through the viewport: its width
// is what the container queries read, and the sidebar and rail in front of it
// would otherwise make every figure here depend on their widths too.

import { test, expect } from '../fixtures/test.js'
import { ModelShelfPage } from '../pages/ModelShelfPage.js'

const COLUMNS = ['kind', 'base', 'size', 'date', 'sets']

for (const { width, sets, thumb } of [
  { width: 900, sets: true, thumb: true },
  { width: 800, sets: false, thumb: true },
  { width: 700, sets: false, thumb: false },
  { width: 600, sets: false, thumb: false },
]) {
  test(`the model list at ${width}px keeps Name readable and the columns aligned`, async ({
    page,
  }) => {
    const shelf = new ModelShelfPage(page)
    await shelf.goto()
    const row = shelf.row('RealVisXL_V4.safetensors')
    await expect(row).toBeVisible()
    await page.locator('.shelf-body').evaluate((el, w) => {
      el.style.width = `${w}px`
    }, width)

    const m = await row.evaluate((rowEl, columns) => {
      const box = (el) => {
        if (!el || getComputedStyle(el).display === 'none') return null
        const r = el.getBoundingClientRect()
        return [Math.round(r.left), Math.round(r.right)]
      }
      const head = document.querySelector('.shelf-head')
      const scroll = document.querySelector('.shelf-scroll')
      return {
        thumbHead: box(head.querySelector('.shelf-head-ident')),
        thumbRow: box(rowEl.querySelector('.shelf-row-ident')),
        nameHead: box(head.querySelector('.shelf-head-col--label')),
        nameRow: box(rowEl.querySelector('.shelf-row-label')),
        cols: columns.map((key) => ({
          key,
          head: box(head.querySelector(`.shelf-head-col--${key}`)),
          row: box(rowEl.querySelector(`.shelf-col--${key}`)),
        })),
        overflow: scroll.scrollWidth - scroll.clientWidth,
      }
    }, COLUMNS)
    const why = JSON.stringify(m)

    expect(Boolean(m.thumbRow), why).toBe(thumb)
    expect(Boolean(m.thumbHead), why).toBe(thumb)
    for (const { key, head, row: cell } of m.cols) {
      const shown = key === 'sets' ? sets : true
      expect(Boolean(head), `${key} heading: ${why}`).toBe(shown)
      expect(Boolean(cell), `${key} cell: ${why}`).toBe(shown)
      // The heading sits exactly over its column: neither the basis nor the
      // floor comes from a cell's content, so both give up the same room.
      if (shown) expect(cell, `${key}: ${why}`).toEqual(head)
    }
    expect(m.nameRow, why).toEqual(m.nameHead)
    // MIN_NAME_WIDTH: the name keeps about 24 characters at every width here.
    expect(m.nameRow[1] - m.nameRow[0], why).toBeGreaterThanOrEqual(199)
    expect(m.overflow, why).toBeLessThanOrEqual(0)
  })
}
