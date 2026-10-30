import { test, expect } from '../fixtures/test.js'

// #2324: what a bulk action on a selection, and a drop on a sidebar row,
// actually does to the library. menu-parity.spec.js proves the two menus list
// the same labels and selection-rail.spec.js covers the rail; neither runs an
// action. Every test here acts on three known pictures and reads the result
// back through the API and from the sidebar's count badge.
//
// The backend is shared by the whole run, so each test works on throwaway
// entities (sets, a person, a project, all named `zz-e2e-bulk …`) and the
// afterEach puts the three pictures back: faces to their person, scores, the
// test tag, EXIF orientation. The pictures come from a person's faces so the
// person action has something to assign.

const PREFIX = 'zz-e2e-bulk'
const TAG = `${PREFIX}-tag`
const API = '/api/v1'

/** Picture id from a thumbnail `<img>` src (`…/thumbnails/<id>.webp?v=…`). */
function idFromSrc(src) {
  const m = String(src || '').match(/\/thumbnails\/(\d+)\./)
  return m ? Number(m[1]) : null
}

async function ok(res) {
  expect(res.ok(), `${res.url()} answered ${res.status()}: ${await res.text()}`).toBe(true)
  return res.json().catch(() => null)
}

/**
 * Three pictures that each carry exactly one face assigned to a person, with
 * the face ids, so the person tests can be undone face for face.
 */
async function pickPictures(api) {
  const people = await ok(await api.get(`${API}/characters`))
  for (const person of people) {
    const { faces } = await ok(await api.get(`${API}/characters/${person.id}/faces`))
    const byPicture = new Map()
    for (const f of faces) byPicture.set(f.picture_id, [...(byPicture.get(f.picture_id) || []), f])
    const single = [...byPicture.values()].filter((fs) => fs.length === 1).map((fs) => fs[0])
    if (single.length >= 3) {
      const picked = single.slice(0, 3)
      return {
        ids: picked.map((f) => f.picture_id),
        personId: person.id,
        personName: person.name,
        faceIds: picked.map((f) => f.id),
      }
    }
  }
  throw new Error('fixture has no person with three single-face pictures')
}

async function createSet(api, name, pictureIds = []) {
  const { picture_set: set } = await ok(await api.post(`${API}/picture_sets`, { data: { name } }))
  if (pictureIds.length) {
    await ok(await api.post(`${API}/picture_sets/${set.id}/members`, { data: { picture_ids: pictureIds } }))
  }
  return set
}

async function createPerson(api) {
  const { character } = await ok(await api.post(`${API}/characters`, { data: { name: `${PREFIX} person` } }))
  return character
}

async function setMembers(api, setId) {
  const { picture_ids } = await ok(await api.get(`${API}/picture_sets/${setId}/members`))
  return picture_ids.map(Number).sort((a, b) => a - b)
}

async function personPictures(api, personId) {
  const { faces } = await ok(await api.get(`${API}/characters/${personId}/faces`))
  return [...new Set(faces.map((f) => f.picture_id))].sort((a, b) => a - b)
}

async function pictureTags(api, id) {
  const { tags } = await ok(await api.get(`${API}/pictures/${id}/tags`))
  return tags
}

async function metadata(api, id) {
  return ok(await api.get(`${API}/pictures/${id}/metadata`))
}

/**
 * Delete every `zz-e2e-bulk …` set, person and project, unlocking a set
 * first. Runs before and after each test, so an attempt that died half way
 * leaves nothing for the retry or the next spec.
 */
async function removeThrowaways(api) {
  const ours = (name) => String(name).toLowerCase().startsWith(PREFIX)
  for (const s of await ok(await api.get(`${API}/picture_sets`))) {
    if (!ours(s.name)) continue
    if (s.locked) await ok(await api.patch(`${API}/picture_sets/${s.id}`, { data: { locked: false } }))
    await ok(await api.delete(`${API}/picture_sets/${s.id}`))
  }
  for (const c of await ok(await api.get(`${API}/characters`))) {
    if (ours(c.name)) await ok(await api.delete(`${API}/characters/${c.id}`))
  }
  for (const p of await ok(await api.get(`${API}/projects`))) {
    if (ours(p.name)) await ok(await api.delete(`${API}/projects/${p.id}`))
  }
}

/** Sorted ascending, for comparing id lists regardless of order. */
const sorted = (ids) => [...ids].map(Number).sort((a, b) => a - b)

let pics // { ids, personId, personName, faceIds }
let source // the set the grid shows: exactly the three pictures
let before // per-picture score and orientation, to put back

test.describe('bulk actions on a selection (#2324)', () => {
  test.beforeEach(async ({ apiContext }) => {
    await removeThrowaways(apiContext)
    pics = await pickPictures(apiContext)
    source = await createSet(apiContext, `${PREFIX} source`, pics.ids)
    before = {}
    for (const id of pics.ids) {
      const m = await metadata(apiContext, id)
      before[id] = { score: m.score, orientation: m.orientation }
    }
  })

  // The pictures first: they are fixture data other specs read, and nothing in
  // their restore depends on the throwaways being gone. (A lock on a throwaway
  // set would refuse the score PATCH, but every lock here is set on `zz-e2e-bulk
  // lock` and the score test locks nothing.)
  test.afterEach(async ({ apiContext }) => {
    // The faces go back to their person by id, whatever a test did to them.
    await ok(await apiContext.post(`${API}/characters/${pics.personId}/faces`, {
      data: { face_ids: pics.faceIds },
    }))
    for (const id of pics.ids) {
      // PATCH, not apply-scores: it can put an unrated picture back to null.
      await ok(await apiContext.patch(`${API}/pictures/${id}`, { data: { score: before[id].score } }))
    }
    for (const id of pics.ids) {
      for (const t of await pictureTags(apiContext, id)) {
        if (t.tag === TAG) await ok(await apiContext.delete(`${API}/pictures/${id}/tags/${t.id}`))
      }
    }
    await removeThrowaways(apiContext)
  })

  /** Open the source set and Ctrl-click its three pictures. */
  async function selectSource(page, grid) {
    await page.goto(`/set/${source.id}`)
    await grid.waitForLoaded()
    await expect(grid.thumbnailImages).toHaveCount(3)
    const shown = await grid.thumbnailImages.evaluateAll((els) => els.map((e) => e.getAttribute('src')))
    expect(sorted(shown.map(idFromSrc))).toEqual(sorted(pics.ids))
    await grid.selectCards(3)
  }

  /** The sidebar row of a set or person by name (a person's is capitalised). */
  function row(sidebar, kind, name) {
    const items = kind === 'set' ? sidebar.setItems : sidebar.characterItems
    return items.filter({ hasText: new RegExp(`^\\s*${name}\\s*\\d*\\s*$`, 'i') })
  }

  test('Add to set puts every picture in the set, and the badge counts them', async ({
    page, grid, sidebar, apiContext,
  }) => {
    const target = await createSet(apiContext, `${PREFIX} target`)
    await selectSource(page, grid)
    await expect(sidebar.count(row(sidebar, 'set', target.name))).toHaveText('0')

    await page.locator('.selection-ctx-bar .pill-ate .ate-btn').click()
    const menu = page.locator('.ate-menu.open')
    await menu.getByRole('option', { name: target.name }).click()
    await expect(menu.locator('.ate-status')).toHaveText(`Added to ${target.name}`)

    await expect.poll(() => setMembers(apiContext, target.id)).toEqual(sorted(pics.ids))
    await expect(sidebar.count(row(sidebar, 'set', target.name))).toHaveText('3')
  })

  test('New set… from the selection creates the set with the pictures in it', async ({
    page, grid, sidebar, apiContext,
  }) => {
    const name = `${PREFIX} new from selection`
    await selectSource(page, grid)

    // All three are one person's, so the menu offers the set inside them.
    await page.locator('.selection-ctx-bar .pill-ate .ate-btn').click()
    await page
      .locator('.ate-menu.open')
      .getByRole('button', { name: new RegExp(`New set in ${pics.personName}`) })
      .click()
    const editor = page.getByRole('dialog')
    await editor.getByPlaceholder('Picture set name').fill(name)
    await editor.getByRole('button', { name: 'Save' }).click()
    await expect(page.getByText(`Created ${name}, added 3 pictures.`)).toBeVisible()

    const created = (await ok(await apiContext.get(`${API}/picture_sets`))).find((s) => s.name === name)
    expect(created, `no set named ${name}`).toBeTruthy()
    expect(created.parent_character_id).toBe(pics.personId)
    await expect.poll(() => setMembers(apiContext, created.id)).toEqual(sorted(pics.ids))

    // The set's row sits under the person's.
    const toggle = page.getByRole('button', { name: `Show the sets in ${pics.personName}` })
    if (await toggle.isVisible()) await toggle.click()
    await expect(sidebar.count(row(sidebar, 'set', name))).toHaveText('3')
  })

  test('assigning a person and removing them moves all three faces', async ({
    page, grid, sidebar, apiContext,
  }) => {
    const person = await createPerson(apiContext)
    await selectSource(page, grid)
    const personRow = row(sidebar, 'character', person.name)
    await expect(personRow).toBeVisible()
    await expect(sidebar.count(personRow)).toHaveText('0')

    await grid.openSelectionMenu()
    await grid.selectionMenuPanel.getByRole('button', { name: 'Set character' }).click()
    const option = page.locator('.ate-menu.open').getByRole('option', { name: person.name })
    await option.click()
    await expect.poll(() => personPictures(apiContext, person.id)).toEqual(sorted(pics.ids))
    await expect.poll(() => personPictures(apiContext, pics.personId)).not.toContain(pics.ids[0])
    await expect(sidebar.count(personRow)).toHaveText('3')

    // The row is checked now; a second click takes the person off again.
    await expect(option).toHaveAttribute('aria-selected', 'true')
    await option.click()
    await expect.poll(() => personPictures(apiContext, person.id)).toEqual([])
    await expect(sidebar.count(personRow)).toHaveText('0')
  })

  test('Set project puts every picture in the project, and the scope menu counts them', async ({
    page, grid, sidebar, apiContext,
  }) => {
    const project = await ok(await apiContext.post(`${API}/projects`, { data: { name: `${PREFIX} project` } }))
    await selectSource(page, grid)

    await grid.openSelectionMenu()
    await grid.selectionMenuPanel.getByRole('button', { name: 'Set project' }).click()
    await page.locator('.ate-menu.open').getByRole('option', { name: project.name }).click()

    await expect.poll(async () => {
      const list = await ok(await apiContext.get(`${API}/pictures?project_id=${project.id}&limit=50`))
      return sorted(list.map((p) => p.id))
    }).toEqual(sorted(pics.ids))

    // Esc closes the flyout, then the menu.
    await page.keyboard.press('Escape')
    await page.keyboard.press('Escape')
    await expect(grid.selectionMenuPanel).toBeHidden()
    await sidebar.openScopeMenu()
    await expect(
      sidebar.scopeMenu.getByRole('menuitemradio', { name: new RegExp(project.name) }),
    ).toContainText('3')
  })

  // The menus' "Remove from <group>" row is the Scrapheap's Restore now; in a
  // set the selection leaves it through the set control's checked row.
  test('unticking the open set removes every picture from it', async ({
    page, grid, sidebar, apiContext,
  }) => {
    await selectSource(page, grid)
    await expect(sidebar.count(row(sidebar, 'set', source.name))).toHaveText('3')

    await page.locator('.selection-ctx-bar .pill-ate .ate-btn').click()
    const menu = page.locator('.ate-menu.open')
    const option = menu.getByRole('option', { name: source.name })
    await expect(option).toHaveAttribute('aria-selected', 'true')
    // The pictures leave the open set, so the pill and its menu go with them.
    await option.click()

    await expect.poll(() => setMembers(apiContext, source.id)).toEqual([])
    await expect(grid.thumbnailImages).toHaveCount(0)
    await expect(sidebar.count(row(sidebar, 'set', source.name))).toHaveText('0')
  })

  test('Rotate turns every picture and refreshes its thumbnail', async ({
    page, grid, apiContext,
  }) => {
    await selectSource(page, grid)
    const srcs = async () =>
      Object.fromEntries(
        await grid.thumbnailImages.evaluateAll((els) =>
          els.map((e) => [e.getAttribute('src').match(/\/thumbnails\/(\d+)\./)[1], e.getAttribute('src')]),
        ),
      )
    const srcBefore = await srcs()

    try {
      await grid.openSelectionMenu()
      await grid.selectionMenuPanel.locator('[data-group="adjust"] > .ctx-item').hover()
      await grid.selectionMenuPanel.getByRole('button', { name: 'Rotate 3 photos right' }).click()

      for (const id of pics.ids) {
        await expect.poll(async () => (await metadata(apiContext, id)).orientation).not.toBe(
          before[id].orientation,
        )
      }
      // Each card swaps to a new thumbnail URL, so the browser fetches the turn.
      await expect.poll(async () => {
        const now = await srcs()
        return pics.ids.filter((id) => now[id] && now[id] !== srcBefore[id]).length
      }).toBe(3)
    } finally {
      // Turn back whatever turned, so the files end as they started.
      const turned = []
      for (const id of pics.ids) {
        if (((await metadata(apiContext, id)).orientation ?? 1) !== (before[id].orientation ?? 1)) turned.push(id)
      }
      if (turned.length) {
        await ok(await apiContext.post(`${API}/pictures/rotate`, {
          data: { picture_ids: turned, direction: 'ccw' },
        }))
      }
    }
  })

  test('T tags every selected picture', async ({ page, grid, apiContext }) => {
    await selectSource(page, grid)
    await page.keyboard.press('t')
    const field = page.locator('.rail-tags__input')
    await expect(field).toBeFocused()
    await expect(field).toHaveAttribute('placeholder', 'Add a tag to all 3')
    await field.fill(TAG)
    await field.press('Enter')

    for (const id of pics.ids) {
      await expect.poll(async () => (await pictureTags(apiContext, id)).map((t) => t.tag)).toContain(TAG)
    }
  })

  test('a digit rates every selected picture', async ({ page, grid, apiContext }) => {
    // A digit toggles: on a picture already at 4 it would clear. Start at 0.
    await ok(await apiContext.post(`${API}/pictures/apply-scores`, {
      data: { scores: Object.fromEntries(pics.ids.map((id) => [String(id), 0])), only_unscored: false },
    }))
    await selectSource(page, grid)
    await page.keyboard.press('4')
    for (const id of pics.ids) {
      await expect.poll(async () => (await metadata(apiContext, id)).score).toBe(4)
    }
  })

  test('a locked member refuses the tag, says so, and the rest are tagged', async ({
    page, grid, apiContext,
  }) => {
    // Only the first picture is frozen: its own set is locked.
    const lock = await createSet(apiContext, `${PREFIX} lock`, [pics.ids[0]])
    await ok(await apiContext.patch(`${API}/picture_sets/${lock.id}`, { data: { locked: true } }))
    await selectSource(page, grid)
    await expect(page.locator('.thumbnail-lock-badge')).toHaveCount(1)

    await page.keyboard.press('t')
    const field = page.locator('.rail-tags__input')
    await expect(field).toBeFocused()
    await expect(field).toHaveAttribute('placeholder', 'Add a tag to all 3')
    await field.fill(TAG)
    await field.press('Enter')

    await expect(page.getByRole('alert').filter({ hasText: '1 of 3 tags were not added' })).toBeVisible()
    for (const id of pics.ids.slice(1)) {
      await expect.poll(async () => (await pictureTags(apiContext, id)).map((t) => t.tag)).toContain(TAG)
    }
    expect((await pictureTags(apiContext, pics.ids[0])).map((t) => t.tag)).not.toContain(TAG)
  })
})

test.describe('dragging pictures onto sidebar rows (#2324)', () => {
  test.beforeEach(async ({ apiContext }) => {
    await removeThrowaways(apiContext)
    pics = await pickPictures(apiContext)
    source = await createSet(apiContext, `${PREFIX} source`, pics.ids)
  })

  test.afterEach(async ({ apiContext }) => {
    await removeThrowaways(apiContext)
    await ok(await apiContext.post(`${API}/characters/${pics.personId}/faces`, {
      data: { face_ids: pics.faceIds },
    }))
  })

  /**
   * Record the native drag events the window sees, so a test can show the
   * browser's own `dragstart` and `drop` ran rather than a synthetic call.
   */
  async function recordDragEvents(page) {
    await page.evaluate(() => {
      window.__drag = []
      for (const type of ['dragstart', 'drop']) {
        window.addEventListener(type, (e) => window.__drag.push({
          type,
          trusted: e.isTrusted,
          payload: e.dataTransfer?.getData('application/json') || null,
        }), true)
      }
    })
  }

  async function openSource(page, grid) {
    await page.goto(`/set/${source.id}`)
    await grid.waitForLoaded()
    await expect(grid.thumbnailImages).toHaveCount(3)
    await recordDragEvents(page)
  }

  function setRow(sidebar, name) {
    return sidebar.setItems.filter({ has: sidebar.page.getByText(name, { exact: true }) })
  }

  /** Drag a grid card by its picture id onto a sidebar row. */
  async function dragOnto(grid, pictureId, target) {
    await grid.page
      .locator(`.thumbnail-img[src*="/thumbnails/${pictureId}."]`)
      .dragTo(target)
  }

  async function expectNativeDrop(page, ids) {
    const events = await page.evaluate(() => window.__drag)
    const start = events.find((e) => e.type === 'dragstart')
    const drop = events.find((e) => e.type === 'drop')
    expect(start?.trusted, 'no trusted dragstart reached the window').toBe(true)
    expect(drop?.trusted, 'no trusted drop reached the window').toBe(true)
    expect(sorted(JSON.parse(drop.payload).imageIds)).toEqual(sorted(ids))
  }

  test('one picture dropped on a set joins it', async ({ page, grid, sidebar, apiContext }) => {
    const target = await createSet(apiContext, `${PREFIX} target`)
    await openSource(page, grid)
    const targetRow = setRow(sidebar, target.name)
    await expect(sidebar.count(targetRow)).toHaveText('0')

    await dragOnto(grid, pics.ids[0], targetRow)

    await expectNativeDrop(page, [pics.ids[0]])
    await expect.poll(() => setMembers(apiContext, target.id)).toEqual([pics.ids[0]])
    await expect(sidebar.count(targetRow)).toHaveText('1')
  })

  test('a dragged selection joins the set together', async ({ page, grid, sidebar, apiContext }) => {
    const target = await createSet(apiContext, `${PREFIX} target`)
    await openSource(page, grid)
    await grid.selectCards(3)
    const targetRow = setRow(sidebar, target.name)

    await dragOnto(grid, pics.ids[1], targetRow)

    await expectNativeDrop(page, pics.ids)
    await expect.poll(() => setMembers(apiContext, target.id)).toEqual(sorted(pics.ids))
    await expect(sidebar.count(targetRow)).toHaveText('3')
  })

  test('a selection dropped on a person is assigned to them', async ({
    page, grid, sidebar, apiContext,
  }) => {
    const person = await createPerson(apiContext)
    await openSource(page, grid)
    await grid.selectCards(3)
    const personRow = sidebar.characterItems.filter({ hasText: new RegExp(person.name, 'i') })
    await expect(personRow).toBeVisible()

    await dragOnto(grid, pics.ids[0], personRow)

    await expectNativeDrop(page, pics.ids)
    await expect.poll(() => personPictures(apiContext, person.id)).toEqual(sorted(pics.ids))
    await expect(sidebar.count(personRow)).toHaveText('3')
  })

  test('a locked set refuses the drop and says why', async ({ page, grid, sidebar, apiContext }) => {
    const target = await createSet(apiContext, `${PREFIX} target`)
    await ok(await apiContext.patch(`${API}/picture_sets/${target.id}`, { data: { locked: true } }))
    await openSource(page, grid)
    const targetRow = setRow(sidebar, target.name)
    await expect(targetRow.locator('.sidebar-lock-icon')).toBeVisible()

    await dragOnto(grid, pics.ids[0], targetRow)

    await expectNativeDrop(page, [pics.ids[0]])
    await expect(page.locator('.sidebar-error-bubble')).toContainText('locked')
    expect(await setMembers(apiContext, target.id)).toEqual([])
    await expect(sidebar.count(targetRow)).toHaveText('0')
  })
})
