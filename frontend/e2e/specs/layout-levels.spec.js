import { test, expect } from '../fixtures/test.js'
import { SettingsDialog } from '../pages/SettingsDialog.js'

// The level row must not wrap up to FOUR levels. jsdom has no layout, so the
// component test can only pin the DOM contract (four selects, three separators);
// the pixel claim has to be made in a real browser, which is what this does.
//
// It also caught a defect a wrap check alone would have missed: a filled select
// is taller than an empty one, so `align-items: center` put the boxes on
// different tops while the row was still one line. Hence distinctTops, not just
// row height.

async function openLayoutDialog(page, grid) {
  const settings = new SettingsDialog(page)
  await grid.goto()
  await settings.open()
  await settings.openTab('Libraries')
  const activeRow = page.locator('.library-row', { has: page.locator('.library-row__active, [class*="active"]') }).first()
  const anyRow = (await activeRow.count()) ? activeRow : page.locator('.library-row').first()
  await anyRow.locator('button[aria-haspopup], .library-row__menu, button:has-text("···")').first().click()
  await page.getByRole('menuitem', { name: /Choose a layout/i }).click()
  await expect(page.locator('.layout-levels')).toBeVisible({ timeout: 15_000 })
}

async function setLevel(page, index, facet) {
  const select = page.locator('.layout-level__select').nth(index)
  await select.click()
  await page.locator('.v-overlay .v-list-item', { hasText: new RegExp(`^${facet}$`, 'i') }).first().click()
  // NOT Escape: Escape closes the dialog itself, not just the select menu.
  // Click the dialog's own heading to dismiss the overlay and keep the dialog.
  await page.locator('.v-overlay-container .v-list').first().waitFor({ state: 'visible' })
  await page.mouse.click(5, 5)
  await expect(page.locator('.layout-levels')).toBeVisible()
}

async function rowMetrics(page) {
  return page.evaluate(() => {
    const row = document.querySelector('.layout-levels')
    const cells = [...row.querySelectorAll('.layout-level')]
    const tops = cells.map((c) => Math.round(c.getBoundingClientRect().top))
    return {
      levels: cells.length,
      distinctTops: [...new Set(tops)].length,
      rowHeight: Math.round(row.getBoundingClientRect().height),
      cellHeight: Math.round(cells[0].getBoundingClientRect().height),
      rowWidth: Math.round(row.getBoundingClientRect().width),
      scrolls: row.scrollWidth > row.clientWidth + 1,
      labels: cells.map((c) => c.innerText.replace(/\s+/g, ' ').trim()),
      cellBoxes: cells.map((c) => {
        const r = c.getBoundingClientRect()
        return `top=${Math.round(r.top)} h=${Math.round(r.height)} w=${Math.round(r.width)}`
      }),
    }
  })
}

test('the level row stays on one line up to four levels', async ({ page, grid }) => {
  await openLayoutDialog(page, grid)
  const results = []
  const facets = ['Project', 'Person', 'Set', 'Tag']
  for (let i = 0; i < facets.length; i++) {
    await setLevel(page, i, facets[i])
    await page.waitForTimeout(700)
    const m = await rowMetrics(page)
    results.push(m)
    console.log(`SLOTS=${m.levels} filled=${i + 1} distinctTops=${m.distinctTops} rowH=${m.rowHeight} cellH=${m.cellHeight} rowW=${m.rowWidth} scrolls=${m.scrolls} boxes=${JSON.stringify(m.cellBoxes)} labels=${JSON.stringify(m.labels)}`)
    const tree = await page.evaluate(() =>
      [...document.querySelectorAll('.layout-tree__row')].slice(0, 10).map((r) => ({
        text: r.querySelector('.layout-tree__name')?.innerText.replace(/\s+/g, ' ').trim(),
        title: r.querySelector('.layout-tree__name')?.getAttribute('title'),
        clipped:
          (r.querySelector('.layout-tree__crumbs')?.scrollWidth ?? 0) >
          (r.querySelector('.layout-tree__crumbs')?.clientWidth ?? 0) + 1,
      })),
    )
    console.log(`TREE filled=${i + 1} ${JSON.stringify(tree, null, 1)}`)
  }
  // The claim under test: up to four levels, one line.
  for (const m of results) {
    expect(m.distinctTops, `levels=${m.levels} wrapped onto ${m.distinctTops} lines`).toBe(1)
    expect(m.rowHeight, `levels=${m.levels} row grew past a single cell`).toBeLessThan(m.cellHeight * 1.6)
    expect(m.scrolls, `levels=${m.levels} produced a horizontal scrollbar`).toBe(false)
  }
})
