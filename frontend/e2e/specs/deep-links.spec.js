import { test, expect } from '../fixtures/test.js'

// Cold loads straight into a URL, with no in-app state behind them: the route
// alone has to open the right lightbox, the right grid and the right review.
// `?overlay=` on `/` is covered by the rating and tag specs; this is every
// entity route the router documents (src/router/index.js), plus the combined
// multi-set / multi-character grids and the review overlay.
//
// Ids are read from the API rather than written down, so a pruned fixture
// moves the spec instead of breaking it.

async function json(apiContext, path) {
  const res = await apiContext.get(`/api/v1${path}`)
  expect(res.ok(), path).toBe(true)
  return res.json()
}

async function pictureIds(apiContext, query) {
  const body = await json(apiContext, `/pictures?${query}&stack_leaders_only=true&fields=grid&limit=500`)
  const rows = Array.isArray(body) ? body : body.pictures || body.items || []
  return rows.map((p) => String(p.id))
}

async function count(apiContext, query) {
  return Number((await json(apiContext, `/pictures/count?${query}&stack_leaders_only=true`)).count)
}

/** The first set and character that sit in a project and hold pictures. */
async function entities(apiContext) {
  const sets = await json(apiContext, '/picture_sets')
  const set = sets.find((s) => s.project_id != null && s.picture_count > 0)
  const characters = await json(apiContext, '/characters')
  let character = null
  for (const c of characters) {
    if (c.project_id != null && (await count(apiContext, `character_id=${c.id}`)) > 0) {
      character = c
      break
    }
  }
  expect(set, 'the fixture has a non-empty set in a project').toBeTruthy()
  expect(character, 'the fixture has a non-empty character in a project').toBeTruthy()
  return { set, character }
}

/** The picture ids of the cards on screen, from their thumbnail URLs. */
function cardIds(page) {
  return page.locator('.image-card img.thumbnail-img').evaluateAll((imgs) => [
    ...new Set(
      imgs
        .map((img) => /\/thumbnails\/(\d+)\./.exec(img.getAttribute('src') || '')?.[1])
        .filter(Boolean),
    ),
  ])
}

/**
 * The whole view's size, as the grid itself counted it: the Filters menu
 * header says "N of M" for the grid's match count over the view's total.
 */
async function gridSize(page) {
  await page.getByRole('button', { name: 'Filters', exact: true }).click()
  const of = page.locator('.fm-root .fm-of')
  await expect(of).toBeVisible()
  await expect(of).toHaveText(/^\d[\d,]* of \d/)
  const text = await of.innerText()
  await page.keyboard.press('Escape')
  await expect(of).toBeHidden()
  return text
}

test.describe('deep links: ?overlay= on entity routes', () => {
  /** @type {Array<[string, (e: any) => string, (e: any) => string]>} */
  const ROUTES = [
    ['/set/:id', ({ set }) => `/set/${set.id}`, ({ set }) => `set_id=${set.id}`],
    [
      '/character/:id',
      ({ character }) => `/character/${character.id}`,
      ({ character }) => `character_id=${character.id}`,
    ],
    [
      '/project/:pid/set/:id',
      ({ set }) => `/project/${set.project_id}/set/${set.id}`,
      ({ set }) => `set_id=${set.id}&project_id=${set.project_id}`,
    ],
  ]

  for (const [name, pathOf, queryOf] of ROUTES) {
    test(`${name}?overlay= opens that picture and closes onto the entity`, async ({
      page,
      grid,
      lightbox,
      apiContext,
    }) => {
      const found = await entities(apiContext)
      const path = pathOf(found)
      const members = await pictureIds(apiContext, queryOf(found))
      // Not the first: a lightbox that opened on whatever led the grid would
      // pass with the first. So the entity needs a second picture to open.
      expect(
        members.length,
        `${path} has at least two stack-leader pictures`,
      ).toBeGreaterThanOrEqual(2)
      const pictureId = members[1]

      await page.goto(`${path}?overlay=${pictureId}`)
      await expect(lightbox.root).toBeVisible({ timeout: 15_000 })
      await expect(lightbox.mainImage).toHaveAttribute(
        'src',
        new RegExp(`/pictures/${pictureId}(\\.|/|\\?)`),
      )
      expect(new URL(page.url()).searchParams.get('overlay')).toBe(pictureId)

      await lightbox.close()
      await expect
        .poll(() => new URL(page.url()).searchParams.get('overlay'))
        .toBeNull()
      expect(new URL(page.url()).pathname).toBe(path)
      await grid.waitForThumbnailLoaded()
      // The grid behind it is the entity's, not All Pictures.
      const shown = await cardIds(page)
      expect(shown.length).toBeGreaterThan(0)
      expect(members).toEqual(expect.arrayContaining(shown))
      expect(await gridSize(page)).toBe(`${members.length} of ${members.length}`)
    })
  }
})

test.describe('deep links: cold loads', () => {
  test('/project/:pid/character/:id resolves the project and the person', async ({
    page,
    grid,
    sidebar,
    apiContext,
  }) => {
    const { character } = await entities(apiContext)
    const projects = await json(apiContext, '/projects')
    const project = projects.find((p) => p.id === character.project_id)
    const members = await pictureIds(
      apiContext,
      `character_id=${character.id}&project_id=${character.project_id}`,
    )

    await page.goto(`/project/${character.project_id}/character/${character.id}`)
    await grid.waitForLoaded()
    // The project's branch opens in place, and the person lights inside it.
    await expect(sidebar.projectRow(project.name)).toHaveAttribute(
      'aria-expanded',
      'true',
    )
    await expect(
      sidebar
        .branch(project.name)
        .locator('.sidebar-character-group > .sidebar-list-item')
        .filter({ hasText: character.name })
        .first(),
    ).toHaveClass(/active/)
    expect(members).toEqual(expect.arrayContaining(await cardIds(page)))
    expect(await gridSize(page)).toBe(`${members.length} of ${members.length}`)
  })

  test('multi-set URLs resolve to the union and the intersection', async ({
    page,
    grid,
    apiContext,
  }) => {
    const sets = (await json(apiContext, '/picture_sets')).filter((s) => s.picture_count > 0)
    expect(sets.length, 'the fixture has two non-empty sets').toBeGreaterThanOrEqual(2)
    const [a, b] = sets
    const ids = `set_ids=${a.id}&set_ids=${b.id}`
    const union = await count(apiContext, `${ids}&set_mode=union`)
    const both = await count(apiContext, `${ids}&set_mode=intersection`)
    // Otherwise the union proves nothing over either set alone.
    expect(union).toBeGreaterThan(Math.max(a.picture_count, b.picture_count))
    // And the intersection from the union, or an ignored mode would pass.
    expect(both).toBeLessThan(union)

    await page.goto(`/set/${a.id}?ids=${a.id},${b.id}&mode=union`)
    await grid.waitForLoaded()
    await expect(grid.multiSelectToolbar).toBeVisible()
    expect(await gridSize(page)).toBe(`${union} of ${union}`)

    await page.goto(`/set/${a.id}?ids=${a.id},${b.id}&mode=intersection`)
    await expect(grid.multiSelectToolbar).toBeVisible()
    await expect.poll(() => gridSize(page)).toBe(`${both} of ${both}`)
  })

  test('a multi-character URL resolves to the union of both people', async ({
    page,
    grid,
    apiContext,
  }) => {
    const people = []
    for (const c of await json(apiContext, '/characters')) {
      const n = await count(apiContext, `character_id=${c.id}`)
      if (n > 0) people.push({ ...c, n })
    }
    expect(people.length, 'the fixture has two people with pictures').toBeGreaterThanOrEqual(2)
    const [a, b] = people
    const union = await count(
      apiContext,
      `character_ids=${a.id}&character_ids=${b.id}&character_mode=union`,
    )
    expect(union).toBeGreaterThan(Math.max(a.n, b.n))

    await page.goto(`/character/${a.id}?ids=${a.id},${b.id}&mode=union`)
    await grid.waitForLoaded()
    await expect(grid.multiSelectToolbar).toBeVisible()
    expect(await gridSize(page)).toBe(`${union} of ${union}`)
  })

  test('?review=board opens the tag health board', async ({ page, reviews }) => {
    await page.goto('/?review=board')
    await expect(reviews.overlay).toBeVisible({ timeout: 15_000 })
    await expect(reviews.board).toBeVisible()
    await expect(reviews.session).toHaveCount(0)
  })

  test('?review=<id> opens that review', async ({ page, reviews, apiContext }) => {
    const tag = 'smile'
    // One open review per tag: free the slot a previous attempt may hold.
    for (const r of await json(apiContext, '/reviews?status=OPEN')) {
      if (r.tag !== tag) continue
      const res = await apiContext.post(`/api/v1/reviews/${r.id}/abort`)
      expect(res.ok(), `abort review ${r.id}`).toBe(true)
    }
    const created = await apiContext.post('/api/v1/reviews', { data: { tag } })
    expect(created.ok(), await created.text()).toBe(true)
    const { id } = await created.json()
    try {
      await page.goto(`/?review=${id}`)
      await expect(reviews.overlay).toBeVisible({ timeout: 15_000 })
      await expect(reviews.session).toBeVisible()
      await expect(reviews.sessionTitle).toContainText(tag)
    } finally {
      const res = await apiContext.post(`/api/v1/reviews/${id}/abort`)
      expect(res.ok()).toBe(true)
    }
  })
})
