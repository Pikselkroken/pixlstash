import { test, expect } from '../fixtures/test.js'
import { PIL_IMAGE_EXTENSIONS, VIDEO_EXTENSIONS } from '../../src/utils/media.js'

// The toolbar's Filters menu (FilterMenu.vue), the strip of chips it fills
// (FilterStrip.vue) and the tag field (FilterTagField.vue), against the real
// listing. Every facet is checked the same way: the strip's "N of M" must be
// the API's own count for the same filter over the same view, a chip names
// it, and its × puts the grid back. Where the API's answer is small enough to
// be on screen, the cards themselves are compared too, so a grid that showed
// the right number of the wrong pictures still fails.
//
// The fixture records no LoRA and no workflow, and the e2e backend cannot
// make one (no import, no ComfyUI). Those two tests stub only the menu's LIST
// of choices; the grid's listing and the count it is compared with are the
// real server's, which for a value no picture has is 0. A filter the grid
// dropped on the floor would show the whole library instead.

// The All Pictures view with no filter: what `buildFilterCountBaseQuery` sends.
const BASE = 'stack_leaders_only=true'

const formatParams = (exts) =>
  new URLSearchParams(exts.map((e) => ['format', e.toUpperCase()])).toString()

async function apiCount(apiContext, extra = '', base = BASE) {
  const query = [base, extra].filter(Boolean).join('&')
  const res = await apiContext.get(`/api/v1/pictures/count?${query}`)
  expect(res.ok(), `count ${query}`).toBe(true)
  return Number((await res.json()).count)
}

async function apiIds(apiContext, extra = '', base = BASE) {
  const query = [base, extra, 'fields=grid', 'limit=500'].filter(Boolean).join('&')
  const res = await apiContext.get(`/api/v1/pictures?${query}`)
  expect(res.ok(), `list ${query}`).toBe(true)
  const body = await res.json()
  const rows = Array.isArray(body) ? body : body.pictures || body.items || []
  return rows.map((p) => String(p.id)).sort()
}

/** The picture ids of the cards on screen, from their thumbnail URLs. */
function cardIds(page) {
  return page.locator('.image-card img.thumbnail-img').evaluateAll((imgs) =>
    [
      ...new Set(
        imgs
          .map((img) => /\/thumbnails\/(\d+)\./.exec(img.getAttribute('src') || '')?.[1])
          .filter(Boolean),
      ),
    ].sort(),
  )
}

class Filters {
  constructor(page) {
    this.page = page
    this.button = page.getByRole('button', { name: 'Filters', exact: true })
    this.menu = page.locator('.fm-root')
    this.menuOf = page.locator('.fm-root .fm-of')
    this.strip = page.getByRole('region', { name: 'Active filters' })
    this.chips = this.strip.locator('.filter-chip')
    this.stripCount = this.strip.locator('.filter-strip-count')
    this.clearAll = this.strip.getByRole('button', { name: 'Clear all' })
  }

  async open() {
    if (await this.menu.isVisible()) return
    await this.button.click()
    await expect(this.menu).toBeVisible()
  }

  async close() {
    if (!(await this.menu.isVisible())) return
    await this.page.keyboard.press('Escape')
    await expect(this.menu).toBeHidden()
  }

  /** Open a kind's own menu from its root row ("Faces", "Score", …). */
  async kind(label) {
    await this.open()
    const row = this.menu.locator('.fm-row', { hasText: label }).first()
    if ((await row.getAttribute('aria-expanded')) !== 'true') await row.click()
    const sub = this.page.locator('.fm-sub-slot')
    await expect(sub).toBeVisible()
    return sub
  }

  /**
   * Pick one row of a pick-one kind (Media, Faces, Stacks, Sharing). A row's
   * name ends in its count ("Images 108"), and "Stacked" is inside
   * "Unstacked", so the match is anchored at the start.
   */
  async pick(kind, option) {
    const sub = await this.kind(kind)
    await sub.getByRole('radio', { name: new RegExp(`^${option}\\b`) }).click()
  }

  /**
   * Type a tag into a kind's "Add a tag" field and take it with Enter. Enter
   * takes the highlighted suggestion, and there is none until the menu's /tags
   * vocabulary has answered, so wait for that row, and for it to be this tag.
   */
  async addTag(sub, tag) {
    const field = sub.getByRole('combobox', { name: 'Add a tag' })
    await field.fill(tag)
    await expect(
      sub.locator('[role="option"][aria-selected="true"] .fm-check-label'),
    ).toHaveText(tag)
    await field.press('Enter')
  }

  chip(kind, value) {
    return this.chips.filter({ hasText: kind }).filter({ hasText: value })
  }

  async removeChip(kind, value) {
    await this.strip
      .getByRole('button', { name: `Remove filter: ${kind} ${value}` })
      .click()
  }
}

test.describe('grid filters', () => {
  /** @type {Filters} */
  let filters
  let total

  test.beforeEach(async ({ page, grid, apiContext }) => {
    filters = new Filters(page)
    total = await apiCount(apiContext)
    await grid.goto()
  })

  /**
   * The strip reads the API's count for `extra` over `total`, and when that is
   * small the cards on screen are exactly the API's pictures.
   */
  async function expectNarrowedTo(page, apiContext, extra) {
    const expected = await apiCount(apiContext, extra)
    expect(expected, `the fixture must narrow on ${extra}`).toBeLessThan(total)
    await expect(filters.stripCount).toHaveText(
      `${expected.toLocaleString()} of ${total.toLocaleString()}`,
    )
    if (expected > 0 && expected <= 12) {
      const ids = await apiIds(apiContext, extra)
      await expect.poll(() => cardIds(page)).toEqual(ids)
    }
    if (expected === 0) await expect(page.locator('.image-card')).toHaveCount(0)
    return expected
  }

  /** No strip, and the menu's own header says the whole view again. */
  async function expectRestored(grid) {
    await expect(filters.strip).toBeHidden()
    await filters.open()
    await expect(filters.menuOf).toHaveText(
      `${total.toLocaleString()} of ${total.toLocaleString()}`,
    )
    await filters.close()
    await grid.waitForThumbnailLoaded()
  }

  test('media type: Video narrows to the API answer, Images to all of it', async ({
    page,
    grid,
    apiContext,
  }) => {
    await filters.pick('Media', 'Video')
    await expect(filters.chip('Media', 'video')).toBeVisible()
    await expectNarrowedTo(page, apiContext, formatParams(VIDEO_EXTENSIONS))

    // Images is a choice too: it is the fixture's whole library (no videos),
    // and it still says so on the strip.
    await filters.pick('Media', 'Images')
    await expect(filters.chip('Media', 'images')).toBeVisible()
    const images = await apiCount(apiContext, formatParams(PIL_IMAGE_EXTENSIONS))
    await expect(filters.stripCount).toHaveText(
      `${images.toLocaleString()} of ${total.toLocaleString()}`,
    )

    await filters.close()
    await filters.removeChip('Media', 'images')
    await expectRestored(grid)
  })

  for (const [option, chip, param] of [
    ['No face', 'no face', 'face_filter=without_face'],
    ['Has face', 'has face', 'face_filter=with_face'],
  ]) {
    test(`faces: ${option}`, async ({ page, grid, apiContext }) => {
      await filters.pick('Faces', option)
      await filters.close()
      await expect(filters.chip('Faces', chip)).toBeVisible()
      await expectNarrowedTo(page, apiContext, param)
      await filters.removeChip('Faces', chip)
      await expectRestored(grid)
    })
  }

  for (const [option, chip, param] of [
    ['Stacked', 'stacked', 'stack_state=stacked'],
    ['Unstacked', 'unstacked', 'stack_state=unstacked'],
  ]) {
    test(`stacks: ${option}`, async ({ page, grid, apiContext }) => {
      await filters.pick('Stacks', option)
      await filters.close()
      await expect(filters.chip('Stacks', chip)).toBeVisible()
      await expectNarrowedTo(page, apiContext, param)
      await filters.removeChip('Stacks', chip)
      await expectRestored(grid)
    })
  }

  test('sharing: Shared narrows to the one picture this test shared', async ({
    page,
    grid,
    apiContext,
  }) => {
    // Nothing in the fixture is shared, so share one picture and revoke it.
    const [pictureId] = await apiIds(apiContext, 'face_filter=without_face')
    const created = await apiContext.post('/api/v1/users/me/token', {
      data: {
        description: 'e2e sharing filter',
        scope: 'READ',
        resource_type: 'picture',
        resource_id: Number(pictureId),
      },
    })
    expect(created.ok()).toBe(true)
    const token = await created.json()
    try {
      await page.reload()
      await grid.waitForLoaded()
      await filters.pick('Sharing', 'Shared')
      await filters.close()
      await expect(filters.chip('Sharing', 'shared')).toBeVisible()
      await expectNarrowedTo(page, apiContext, 'shared_only=true')
      // By identity, not count: the picture shared here is the one shown.
      expect(await cardIds(page)).toContain(pictureId)
      await filters.removeChip('Sharing', 'shared')
      await expectRestored(grid)
    } finally {
      const tokens = await (await apiContext.get('/api/v1/users/me/token')).json()
      const mine = (Array.isArray(tokens) ? tokens : tokens.tokens || []).filter(
        (t) => t.id === token.id || t.description === 'e2e sharing filter',
      )
      for (const t of mine) {
        const res = await apiContext.delete(`/api/v1/users/me/token/${t.id}`)
        expect(res.ok(), `revoke share token ${t.id}`).toBe(true)
      }
    }
  })

  test('score: At least 3 stars, and At most 2', async ({ page, grid, apiContext }) => {
    const sub = await filters.kind('Score')
    await sub.getByRole('radio', { name: 'At least 3 stars' }).click()
    await expect(filters.chip('Score', '3+')).toBeVisible()
    await expectNarrowedTo(page, apiContext, 'min_score=3')

    // At most 2 from 0 stars: a range from 0 takes the unrated with it.
    await sub.getByRole('radio', { name: 'At least 0 stars' }).click()
    await sub.getByRole('radio', { name: 'At most 2 stars' }).click()
    await expect(filters.chip('Score', 'up to 2')).toBeVisible()
    await expectNarrowedTo(page, apiContext, 'max_score=2&unscored=1')

    await filters.close()
    await filters.removeChip('Score', 'up to 2')
    await expectRestored(grid)
  })

  test('tags: Has tag, then flipped to Lacks tag', async ({ page, grid, apiContext }) => {
    const sub = await filters.kind('Tags')
    await filters.addTag(sub, 'beard')
    await expect(filters.chip('Has tag', 'beard')).toBeVisible()
    await expectNarrowedTo(page, apiContext, 'tag=beard')

    // Its chip in the menu flips it: never both rules for one tag.
    await sub.getByRole('button', { name: 'beard: has. Move to lacks' }).click()
    await expect(filters.chip('Lacks tag', 'beard')).toBeVisible()
    await expect(filters.chip('Has tag', 'beard')).toHaveCount(0)
    await expectNarrowedTo(page, apiContext, 'rejected_tag=beard')

    await filters.close()
    await filters.removeChip('Lacks tag', 'beard')
    await expectRestored(grid)
  })

  test('tag confidence: Missing at the default 80%', async ({ page, grid, apiContext }) => {
    const sub = await filters.kind('Tag confidence')
    await filters.addTag(sub, 'beard')
    await expect(filters.chip('Missing tag', 'beard')).toBeVisible()
    await expectNarrowedTo(page, apiContext, 'tag_confidence_above=beard%3A0.80')
    await filters.close()
    await filters.chips.filter({ hasText: 'Missing tag' }).locator('.filter-chip-x').click()
    await expectRestored(grid)
  })

  test('checkpoint: the fixture checkpoint', async ({ page, grid, apiContext }) => {
    const models = await (await apiContext.get('/api/v1/pictures/comfyui_models')).json()
    expect(models.length, 'the fixture records a checkpoint').toBeGreaterThan(0)
    const model = models[0].value
    const label = model.replace(/\.[^/.]+$/, '')

    const sub = await filters.kind('Checkpoint')
    await sub.locator('label', { hasText: label }).locator('input[type="checkbox"]').check()
    await filters.close()
    await expect(filters.chip('Checkpoint', label)).toBeVisible()
    await expectNarrowedTo(
      page,
      apiContext,
      new URLSearchParams([['comfyui_model', model]]).toString(),
    )
    await filters.removeChip('Checkpoint', label)
    await expectRestored(grid)
  })

  test('LoRA: a ticked LoRA reaches the listing', async ({ page, grid, apiContext }) => {
    const lora = 'e2e-no-picture-has-this.safetensors'
    await page.route(/\/api\/v1\/pictures\/comfyui_loras(\?|$)/, (route) =>
      route.fulfill({ json: [{ value: lora, name: null }] }),
    )
    const sub = await filters.kind('LoRA')
    await sub
      .locator('label', { hasText: 'e2e-no-picture-has-this' })
      .locator('input[type="checkbox"]')
      .check()
    await filters.close()
    await expect(filters.chip('LoRA', 'e2e-no-picture-has-this')).toBeVisible()
    await expectNarrowedTo(page, apiContext, new URLSearchParams([['comfyui_lora', lora]]).toString())
    await filters.removeChip('LoRA', 'e2e-no-picture-has-this')
    await expectRestored(grid)
  })

  test('workflow: a ticked workflow reaches the listing', async ({ page, grid, apiContext }) => {
    const card = { id: 'e2e-no-such-workflow', name: 'E2E workflow', picture_count: 0, covers: [] }
    await page.route(/\/api\/v1\/workflows\?/, (route) =>
      route.fulfill({ json: { cards: [card], one_offs: 0, hidden: 0 } }),
    )
    const sub = await filters.kind('Workflow')
    await sub.locator('label', { hasText: card.name }).locator('input[type="checkbox"]').check()
    await filters.close()
    await expect(filters.chip('Workflow', card.name)).toBeVisible()
    await expectNarrowedTo(page, apiContext, `workflow=${card.id}`)
    await filters.removeChip('Workflow', card.name)
    await expectRestored(grid)
  })

  /** Add a tag to Has tag through the menu's tag field. */
  async function hasTag(tag) {
    await filters.addTag(await filters.kind('Tags'), tag)
    await expect(filters.chip('Has tag', tag)).toBeVisible()
  }

  test('two facets combine, and Clear all clears both', async ({ page, grid, apiContext }) => {
    // Picked so the pair is narrower than either alone: a grid that dropped
    // one of them would show that one's count, not the pair's.
    const tag = await apiCount(apiContext, 'tag=beard')
    const stars = await apiCount(apiContext, 'min_score=1')
    const both = await apiCount(apiContext, 'tag=beard&min_score=1')
    expect(both, 'the fixture must narrow on the pair').toBeLessThan(Math.min(tag, stars))

    await hasTag('beard')
    const sub = await filters.kind('Score')
    await sub.getByRole('radio', { name: 'At least 1 stars' }).click()
    await filters.close()
    await expect(filters.chips).toHaveCount(2)
    await expectNarrowedTo(page, apiContext, 'tag=beard&min_score=1')

    await filters.clearAll.click()
    await expectRestored(grid)
  })

  test('filters survive a route change and reset on a reload', async ({
    page,
    grid,
    sidebar,
    apiContext,
  }) => {
    await hasTag('beard')
    await filters.close()

    // A sidebar entry is a navigation; the filter store is not the route's,
    // so the filter rides along and narrows the set too.
    const row = await sidebar.firstNonEmptyByName(sidebar.setItems)
    await row.click()
    await expect.poll(() => page.url()).toMatch(/\/set\/\d+/)
    const setId = /\/set\/(\d+)/.exec(page.url())[1]
    await expect(filters.chip('Has tag', 'beard')).toBeVisible()
    const setBase = `set_id=${setId}&${BASE}`
    const inSet = await apiCount(apiContext, 'tag=beard', setBase)
    const setTotal = await apiCount(apiContext, '', setBase)
    expect(inSet, 'the filter must narrow the set').toBeLessThan(setTotal)
    await expect(filters.stripCount).toHaveText(
      `${inSet.toLocaleString()} of ${setTotal.toLocaleString()}`,
    )

    // The filters are the tab's, not the URL's: a reload starts clean.
    await page.reload()
    await grid.waitForLoaded()
    await expect(filters.strip).toBeHidden()
    await filters.open()
    await expect(filters.menuOf).toHaveText(
      `${setTotal.toLocaleString()} of ${setTotal.toLocaleString()}`,
    )
    await filters.close()
  })
})
