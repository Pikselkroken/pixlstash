import { test, expect } from '../fixtures/test.js'

// Boolean set operations — Ctrl/Cmd-clicking a second picture set enters
// multi-select and reveals the combine toolbar with Union / Overlap /
// Difference / Unique (XOR) modes. The combined grid must hold exactly what the
// set arithmetic says, computed from the API's own set memberships.
//
// The fixture's sets barely overlap, so this spec makes two sets of its own
// that do (A = p0..p2, B = p1..p3) and deletes them afterwards. Only unstacked
// pictures go in: adding a stacked picture to a set adds its whole stack, and
// a collapsed stack shows only its cover in the grid.

/** Picture ids the grid is showing, read off the thumbnail URLs. */
async function gridPictureIds(page) {
  const srcs = await page.locator('.thumbnail-img').evaluateAll((imgs) =>
    imgs.map((img) => img.getAttribute('src') || ''),
  )
  return srcs
    .map((src) => Number(src.match(/\/thumbnails\/(\d+)\./)?.[1]))
    .filter(Number.isFinite)
    .sort((a, b) => a - b)
}

const sorted = (ids) => [...ids].sort((a, b) => a - b)

test.describe('boolean set operations', () => {
  const sets = []

  test.beforeAll(async ({ apiContext }) => {
    const listed = await apiContext.get('/api/v1/pictures', {
      params: { fields: 'grid', limit: 200 },
    })
    expect(listed.ok(), await listed.text()).toBe(true)
    const loose = (await listed.json()).filter((p) => p.stack_id == null)
    expect(loose.length).toBeGreaterThanOrEqual(4)
    const [p0, p1, p2, p3] = loose.map((p) => p.id)
    const tag = Date.now().toString(36)
    for (const [name, ids] of [
      [`e2e-ops-A-${tag}`, [p0, p1, p2]],
      [`e2e-ops-B-${tag}`, [p1, p2, p3]],
    ]) {
      const created = await apiContext.post('/api/v1/picture_sets', { data: { name } })
      expect(created.ok(), await created.text()).toBe(true)
      const { picture_set: set } = await created.json()
      sets.push(set)
      const added = await apiContext.post(`/api/v1/picture_sets/${set.id}/members`, {
        data: { picture_ids: ids },
      })
      expect(added.ok(), await added.text()).toBe(true)
    }
  })

  test.afterAll(async ({ apiContext }) => {
    // Every set, even when one delete fails, then report the failures.
    const failed = []
    for (const set of sets) {
      const res = await apiContext.delete(`/api/v1/picture_sets/${set.id}`)
      if (!res.ok()) failed.push(`${set.id}: ${res.status()} ${await res.text()}`)
    }
    expect(failed).toEqual([])
  })

  test('the combined grid matches the set arithmetic for every mode', async ({
    page,
    grid,
    sidebar,
    apiContext,
  }) => {
    // Expected results come from the API's memberships, not from the ids the
    // sets were built with: the grid has to agree with what the server holds.
    const [a, b] = await Promise.all(
      sets.map(async (set) => {
        const res = await apiContext.get(`/api/v1/picture_sets/${set.id}/members`)
        expect(res.ok(), await res.text()).toBe(true)
        return new Set((await res.json()).picture_ids)
      }),
    )
    const expected = {
      union: sorted([...new Set([...a, ...b])]),
      intersection: sorted([...a].filter((id) => b.has(id))),
      difference: sorted([...a].filter((id) => !b.has(id))),
      xor: sorted([...a, ...b].filter((id) => !(a.has(id) && b.has(id)))),
    }
    // Each mode must give a different answer, or a mode that silently fell
    // back to another would still pass.
    expect(new Set(Object.values(expected).map(String)).size).toBe(4)

    await grid.goto()
    const rowA = sidebar.setItems.filter({ hasText: sets[0].name })
    const rowB = sidebar.setItems.filter({ hasText: sets[1].name })
    // First click selects a single set; Ctrl-click adds a second → multi-select.
    await rowA.click()
    await rowB.click({ modifiers: ['ControlOrMeta'] })

    await expect(grid.multiSelectToolbar).toBeVisible()
    await expect(grid.multiSelectLabel).toContainText('2 sets selected')

    const modes = await grid.multiSelectMode.locator('option').allInnerTexts()
    for (const label of ['Union', 'Overlap', 'Difference', 'Unique (XOR)']) {
      expect(modes).toContain(label)
    }

    for (const mode of ['union', 'intersection', 'difference', 'xor']) {
      await grid.multiSelectMode.selectOption(mode)
      // Difference is taken from a base set; `expected.difference` is A minus
      // B, so the toolbar's default base has to be A.
      if (mode === 'difference') {
        await expect(page.locator('.multi-select-toolbar__base')).toHaveValue(
          String(sets[0].id),
        )
      }
      await expect
        .poll(() => gridPictureIds(page), { message: `grid for ${mode}` })
        .toEqual(expected[mode])
    }

    await grid.multiSelectClear.click()
    await expect(grid.multiSelectToolbar).toBeHidden()
  })
})
