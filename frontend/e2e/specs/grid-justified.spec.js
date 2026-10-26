import { test, expect } from '../fixtures/test.js'

// The justified grid's geometry (#2162). jsdom has no layout, so the claims the
// issue's acceptance makes in pixels are made here: every full row ends flush
// with the column, with the rail open and closed; a selected tile's 2px ring
// sits in the gutter and never on a neighbour; and a stack says so at rest.

async function setThumbnailMode(apiContext, mode) {
  const res = await apiContext.patch('/api/v1/users/me/config', {
    data: { thumbnail_mode: mode },
  })
  expect(res.ok()).toBe(true)
}

/** The grid's content box and every rendered card's box, grouped by row. */
function measure(page) {
  return page.evaluate(() => {
    const grid = document.querySelector('.image-grid')
    const style = getComputedStyle(grid)
    const box = grid.getBoundingClientRect()
    const content = {
      left: box.left + parseFloat(style.paddingLeft),
      right: box.right - parseFloat(style.paddingRight),
      top: box.top + parseFloat(style.paddingTop),
    }
    const cards = [...grid.querySelectorAll('.image-card')].map((el) => {
      const r = el.getBoundingClientRect()
      return { left: r.left, right: r.right, top: r.top, bottom: r.bottom }
    })
    const rows = new Map()
    for (const card of cards) {
      const key = Math.round(card.top)
      if (!rows.has(key)) rows.set(key, [])
      rows.get(key).push(card)
    }
    return {
      padding: parseFloat(style.paddingLeft),
      box: { left: box.left, top: box.top },
      content,
      cards,
      rows: [...rows.values()],
    }
  })
}

async function expectFullRowsFlush(page) {
  const { padding, content, rows } = await measure(page)
  expect(padding).toBe(2)
  // The last rendered row may be the partial last row of the library, which
  // keeps its natural widths by design; every row above it is full.
  expect(rows.length).toBeGreaterThan(1)
  for (const row of rows.slice(0, -1)) {
    const right = Math.max(...row.map((c) => c.right))
    const left = Math.min(...row.map((c) => c.left))
    expect(Math.abs(right - content.right)).toBeLessThan(0.05)
    expect(Math.abs(left - content.left)).toBeLessThan(0.05)
  }
}

test.describe('justified grid', () => {
  test.beforeEach(async ({ apiContext }) => {
    await setThumbnailMode(apiContext, 'justified')
  })

  test.afterEach(async ({ apiContext }) => {
    await setThumbnailMode(apiContext, 'square')
  })

  test('full rows end flush with the column, rail open and closed', async ({
    page,
    grid,
  }) => {
    await grid.goto()
    await expect(page.locator('.image-grid--justified')).toBeVisible()
    await expectFullRowsFlush(page)

    const wasOpen = (await grid.statsToggle.getAttribute('aria-pressed')) === 'true'
    await grid.statsToggle.click()
    await expect(grid.statsToggle).toHaveAttribute(
      'aria-pressed',
      wasOpen ? 'false' : 'true',
    )
    // The repack follows a ResizeObserver; wait for the column to settle.
    await expect.poll(async () => {
      const { rows, content } = await measure(page)
      return rows.length > 1 &&
        Math.abs(Math.max(...rows[0].map((c) => c.right)) - content.right) < 0.05
    }).toBe(true)
    await expectFullRowsFlush(page)

    // A column whose width is not a whole pixel: rows are packed to the
    // floored width, and the cards have to grow into the remainder.
    const before = await measure(page)
    await page.evaluate(() => {
      document.querySelector('.image-grid').style.width = 'calc(100% - 0.5px)'
    })
    await expect.poll(async () => {
      const { content } = await measure(page)
      return content.right !== before.content.right
    }).toBe(true)
    const { content } = await measure(page)
    expect(content.right % 1).not.toBe(0)
    await expectFullRowsFlush(page)
  })

  test("a selected tile's ring sits in the gutter, clear of every neighbour", async ({
    page,
    grid,
  }) => {
    await grid.goto()
    // A tile from the middle of the first row has neighbours on both sides and
    // below, and the first tile of all has the grid's edges above and left.
    for (const index of [0, 1]) {
      const card = grid.cards.nth(index)
      await card.click()
      const overlay = card.locator('.selection-overlay')
      await expect(overlay).toBeAttached()
      const ring = await overlay.evaluate((el) => {
        const s = getComputedStyle(el)
        return {
          width: s.outlineWidth,
          style: s.outlineStyle,
          offset: s.outlineOffset,
          wash: s.backgroundColor,
          shadow: s.boxShadow,
        }
      })
      expect(ring).toEqual({
        width: '2px',
        style: 'solid',
        offset: '0px',
        wash: 'rgba(0, 0, 0, 0)',
        shadow: 'none',
      })

      const { cards, box } = await measure(page)
      const own = cards[index]
      const outer = {
        left: own.left - 2,
        right: own.right + 2,
        top: own.top - 2,
        bottom: own.bottom + 2,
      }
      // The ring is inside the grid's own box, in its edge gutter, so the
      // scroller does not clip it.
      expect(outer.left).toBeGreaterThanOrEqual(box.left - 0.05)
      expect(outer.top).toBeGreaterThanOrEqual(box.top - 0.05)
      for (const [i, other] of cards.entries()) {
        if (i === index) continue
        const overlapX = Math.min(outer.right, other.right) - Math.max(outer.left, other.left)
        const overlapY = Math.min(outer.bottom, other.bottom) - Math.max(outer.top, other.top)
        expect(
          overlapX > 0.05 && overlapY > 0.05,
          `ring of card ${index} overlaps card ${i}`,
        ).toBe(false)
      }
    }
  })

  test('a collapsed stack shows its count at rest, top-left', async ({ page, grid }) => {
    await grid.goto()
    // The fixture has stacks; nothing is hovered.
    await page.mouse.move(0, 0)
    const badge = page.locator('.image-card [data-testid="stack-badge"]').first()
    await expect(badge).toBeVisible()
    await expect(badge).toHaveText(/^\d+$/)
    const [badgeBox, tileBox] = await Promise.all([
      badge.boundingBox(),
      badge.locator('xpath=ancestor::div[contains(@class,"thumbnail-container")]').boundingBox(),
    ])
    expect(badgeBox.x - tileBox.x).toBeLessThan(8)
    expect(badgeBox.y - tileBox.y).toBeLessThan(8)
  })
})
