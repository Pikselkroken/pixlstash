import { test, expect } from '../fixtures/test.js'

// Child sets (#2124): a picture set under a person or under another set, which
// is how a LoRA dataset is held. tests/test_child_sets.py owns the backend
// rules; this drives them from the sidebar. The rules asserted here are the
// backend's: the parent is a home, not a constraint, so membership is the
// child's own and a parent's count is its own; deleting a parent (set or
// person) lifts its children to the top level; a locked set refuses members
// and moves. The UI moves a set only out to the top level, so that is the
// move covered.
//
// Every entry is made here under a name unique to the attempt, and cleanup
// matches on id AND name: SQLite hands a deleted id to the next row created.

const API = '/api/v1'

let seq = 0
/** A name no other test, attempt or seeded entry carries. */
const uniq = (label) => `${label} ${Date.now().toString(36)}${++seq}`

async function body(res) {
  expect(res.ok(), `${res.url()} answered ${res.status()}`).toBe(true)
  return res.json()
}

const LIST = { set: 'picture_sets', person: 'characters' }

/** The entry of that kind and id as the backend lists it, or undefined. */
async function find(ctx, kind, id) {
  const rows = await body(await ctx.get(`${API}/${LIST[kind]}`))
  return rows.find((r) => r.id === id)
}

async function findNamed(ctx, kind, name) {
  const rows = await body(await ctx.get(`${API}/${LIST[kind]}`))
  return rows.find((r) => r.name === name)
}

let created

function track(kind, entry) {
  created.push({ kind, id: entry.id, name: entry.name })
  return entry
}

function retrack(kind, id, name) {
  const entry = created.find((c) => c.kind === kind && c.id === id)
  if (entry) entry.name = name
}

async function apiSet(ctx, name, extra = {}) {
  const res = await ctx.post(`${API}/picture_sets`, { data: { name, ...extra } })
  return track('set', (await body(res)).picture_set)
}

async function apiPerson(ctx, name) {
  const res = await ctx.post(`${API}/characters`, { data: { name } })
  return track('person', (await body(res)).character)
}

const pictureIds = (rows) => rows.map((p) => p.id).sort((a, b) => a - b)

const setMembers = async (ctx, setId) =>
  pictureIds(await body(await ctx.get(`${API}/pictures`, { params: { set_id: setId } })))

const totalPictures = async (ctx) =>
  (await body(await ctx.get(`${API}/pictures/count`))).count

async function livePictures(ctx, ids) {
  const params = new URLSearchParams()
  for (const id of ids) params.append('id', String(id))
  const rows = await body(await ctx.get(`${API}/pictures?${params}`))
  return pictureIds(rows.filter((p) => !p.deleted))
}

/** Three pictures of a seeded person: read only, nothing here changes them. */
async function seededPictures(ctx) {
  const people = await body(await ctx.get(`${API}/characters`))
  for (const person of people) {
    const rows = await body(
      await ctx.get(`${API}/pictures`, { params: { character_id: person.id } }),
    )
    if (rows.length >= 3) return pictureIds(rows.slice(0, 3))
  }
  throw new Error('the fixture has no person with three pictures')
}

async function addToSet(ctx, setId, ids) {
  await body(
    await ctx.post(`${API}/picture_sets/${setId}/members`, { data: { picture_ids: ids } }),
  )
}

function acceptConfirm(page) {
  return new Promise((resolve) => {
    page.once('dialog', async (d) => {
      const message = d.message()
      await d.accept()
      resolve(message)
    })
  })
}

const pathname = (page) => new URL(page.url()).pathname

const editor = (page) => page.locator('.app-dialog')
const nameField = (page) => editor(page).getByLabel('Name *')
const saveButton = (page) => editor(page).getByRole('button', { name: 'Save', exact: true })

// Exact on the label: a child row also carries "1 set" when it has children.
const setRow = (sidebar, name) =>
  sidebar.setItems.filter({ has: sidebar.page.getByText(name, { exact: true }) })
const personRow = (sidebar, name) =>
  sidebar.characterItems.filter({ has: sidebar.page.getByText(name, { exact: true }) })
/** The person's group: their row and the child set rows nested under it. */
const personGroup = (sidebar, name) =>
  sidebar.page
    .locator('.sidebar-character-group')
    .filter({ has: sidebar.page.getByText(name, { exact: true }) })

/** Open a collapsed parent so its children are on screen. */
async function showChildren(page, parentName) {
  await page.getByRole('button', { name: `Show the sets in ${parentName}`, exact: true }).click()
}

async function openCtx(sidebar, row) {
  await row.click({ button: 'right' })
  await expect(sidebar.ctxMenu).toBeVisible()
}

/** Open a set's pictures and select them all, ready to drag. */
async function selectAllIn(page, grid, setId, count) {
  await page.goto(`/set/${setId}`)
  await grid.waitForLoaded()
  await expect(grid.thumbnailImages).toHaveCount(count)
  await grid.selectCards(count)
}

async function dragPictureOnto(page, pictureId, target) {
  await page.locator(`.thumbnail-img[src*="/thumbnails/${pictureId}."]`).dragTo(target)
}

test.beforeEach(() => {
  created = []
})

test.afterEach(async ({ apiContext }) => {
  for (const { kind, id, name } of created) {
    const left = await find(apiContext, kind, id)
    if (left?.name !== name) continue
    if (left.locked) {
      await apiContext.patch(`${API}/picture_sets/${id}`, { data: { locked: false } })
    }
    await apiContext.delete(`${API}/${LIST[kind]}/${id}`)
  }
})

test.describe('creating a child set', () => {
  test('a set made inside a person is listed under them and opens', async ({
    page,
    grid,
    sidebar,
    apiContext,
  }) => {
    const person = await apiPerson(apiContext, uniq('E2E parent person'))
    const name = uniq('E2E person dataset')
    await grid.goto()

    await openCtx(sidebar, personRow(sidebar, person.name))
    await sidebar.ctxButton(`New set in "${person.name}"…`).click()
    await expect(editor(page)).toContainText(`New set in ${person.name}`)
    await nameField(page).fill(name)
    await saveButton(page).click()
    await expect(editor(page)).toBeHidden()

    const set = await findNamed(apiContext, 'set', name)
    expect(set, 'the set reached the backend').toBeTruthy()
    track('set', set)
    expect(set).toMatchObject({ parent_character_id: person.id, parent_set_id: null })

    await expect.poll(() => pathname(page)).toBe(`/set/${set.id}`)
    const row = personGroup(sidebar, person.name).locator('.sidebar-set-item', {
      has: page.getByText(name, { exact: true }),
    })
    await expect(row).toBeVisible()
    await expect(row).toHaveClass(/active/)
    await expect(personRow(sidebar, person.name)).toContainText('1 set')

    // The open set keeps its branch open across a reload.
    await page.reload()
    await expect(row).toBeVisible()
  })

  test('a set made inside a set is listed under it and opens', async ({
    page,
    grid,
    sidebar,
    apiContext,
  }) => {
    const parent = await apiSet(apiContext, uniq('E2E parent set'))
    const name = uniq('E2E concept dataset')
    await grid.goto()

    await openCtx(sidebar, setRow(sidebar, parent.name))
    await sidebar.ctxButton(`New set inside "${parent.name}"…`).click()
    await expect(editor(page)).toContainText(`New set in ${parent.name}`)
    await nameField(page).fill(name)
    await saveButton(page).click()
    await expect(editor(page)).toBeHidden()

    const set = await findNamed(apiContext, 'set', name)
    expect(set, 'the set reached the backend').toBeTruthy()
    track('set', set)
    expect(set).toMatchObject({ parent_set_id: parent.id, parent_character_id: null })

    await expect.poll(() => pathname(page)).toBe(`/set/${set.id}`)
    await expect(setRow(sidebar, name)).toHaveClass(/sidebar-tree-kid/)
    await expect(setRow(sidebar, name)).toHaveClass(/active/)
    await expect(setRow(sidebar, parent.name)).toContainText('1 set')
    await expect(setRow(sidebar, parent.name)).not.toHaveClass(/sidebar-tree-kid/)
  })
})

test('pictures dropped on a child set are its members, not its parent\'s', async ({
  page,
  grid,
  sidebar,
  apiContext,
}) => {
  const ids = await seededPictures(apiContext)
  const source = await apiSet(apiContext, uniq('E2E source'))
  await addToSet(apiContext, source.id, ids)
  const parent = await apiSet(apiContext, uniq('E2E home set'))
  const child = await apiSet(apiContext, uniq('E2E dataset'), { parent_set_id: parent.id })

  await selectAllIn(page, grid, source.id, ids.length)
  await showChildren(page, parent.name)
  const childRow = setRow(sidebar, child.name)
  await expect(sidebar.count(childRow)).toHaveText('0')

  await dragPictureOnto(page, ids[0], childRow)

  await expect.poll(() => setMembers(apiContext, child.id)).toEqual(ids)
  await expect(sidebar.count(childRow)).toHaveText(String(ids.length))
  // The parent is a home, not a superset: its members and count are its own.
  expect(await setMembers(apiContext, parent.id)).toEqual([])
  await expect(sidebar.count(setRow(sidebar, parent.name))).toHaveText('0')
})

test('a child set renames in place and moves out to the top level', async ({
  page,
  grid,
  sidebar,
  apiContext,
}) => {
  const ids = await seededPictures(apiContext)
  const parent = await apiSet(apiContext, uniq('E2E move parent'))
  const child = await apiSet(apiContext, uniq('E2E move child'), { parent_set_id: parent.id })
  await addToSet(apiContext, child.id, ids)
  const renamed = uniq('E2E moved child')
  await grid.goto()
  await showChildren(page, parent.name)

  await openCtx(sidebar, setRow(sidebar, child.name))
  await sidebar.ctxButton('Edit').click()
  await expect(editor(page)).toContainText('Edit picture set')
  await nameField(page).fill(renamed)
  await saveButton(page).click()
  await expect(editor(page)).toBeHidden()
  retrack('set', child.id, renamed)

  // Renamed where it is: still under the same parent.
  await expect(setRow(sidebar, renamed)).toHaveClass(/sidebar-tree-kid/)
  await expect(setRow(sidebar, child.name)).toHaveCount(0)
  expect(await find(apiContext, 'set', child.id)).toMatchObject({
    name: renamed,
    parent_set_id: parent.id,
  })

  await openCtx(sidebar, setRow(sidebar, renamed))
  await sidebar.ctxButton(`Move out of "${parent.name}"`).click()

  await expect
    .poll(async () => (await find(apiContext, 'set', child.id)).parent_set_id)
    .toBeNull()
  await expect(setRow(sidebar, renamed)).not.toHaveClass(/sidebar-tree-kid/)
  await expect(setRow(sidebar, parent.name)).not.toContainText('1 set')
  // Moving changes the home, never the members.
  expect(await setMembers(apiContext, child.id)).toEqual(ids)
})

test.describe('deleting', () => {
  test('deleting the child set on screen keeps its pictures, in the library and the parent', async ({
    page,
    grid,
    sidebar,
    apiContext,
  }) => {
    const ids = await seededPictures(apiContext)
    const parent = await apiSet(apiContext, uniq('E2E keeping parent'))
    const child = await apiSet(apiContext, uniq('E2E doomed child'), {
      parent_set_id: parent.id,
    })
    await addToSet(apiContext, parent.id, ids)
    await addToSet(apiContext, child.id, ids)
    const total = await totalPictures(apiContext)

    // Opening the child opens its branch, so its row is on screen.
    await page.goto(`/set/${child.id}`)
    await grid.waitForLoaded()
    const row = setRow(sidebar, child.name)
    await expect(row).toHaveClass(/active/)

    await openCtx(sidebar, row)
    const confirmed = acceptConfirm(page)
    await sidebar.ctxButton('Delete').click()
    expect(await confirmed).toBe(
      `Delete picture set "${child.name}"? This will unassign all their images.`,
    )

    await expect(row).toHaveCount(0)
    // Not left on the dead set's route (#2340).
    await expect.poll(() => pathname(page)).toBe('/')
    await grid.waitForThumbnailLoaded()
    await expect(setRow(sidebar, parent.name)).not.toContainText('1 set')

    expect(await find(apiContext, 'set', child.id)).toBeUndefined()
    expect(await totalPictures(apiContext)).toBe(total)
    expect(await livePictures(apiContext, ids)).toEqual(ids)
    expect(await setMembers(apiContext, parent.id)).toEqual(ids)
  })

  test('deleting a parent set lifts its child to the top level, members kept', async ({
    page,
    grid,
    sidebar,
    apiContext,
  }) => {
    const ids = await seededPictures(apiContext)
    const parent = await apiSet(apiContext, uniq('E2E doomed parent'))
    const child = await apiSet(apiContext, uniq('E2E orphan child'), {
      parent_set_id: parent.id,
    })
    await addToSet(apiContext, child.id, ids)
    await grid.goto()
    await showChildren(page, parent.name)
    await expect(setRow(sidebar, child.name)).toHaveClass(/sidebar-tree-kid/)

    await openCtx(sidebar, setRow(sidebar, parent.name))
    const confirmed = acceptConfirm(page)
    await sidebar.ctxButton('Delete').click()
    await confirmed

    await expect(setRow(sidebar, parent.name)).toHaveCount(0)
    await expect(setRow(sidebar, child.name)).toBeVisible()
    await expect(setRow(sidebar, child.name)).not.toHaveClass(/sidebar-tree-kid/)
    expect(await find(apiContext, 'set', parent.id)).toBeUndefined()
    expect(await find(apiContext, 'set', child.id)).toMatchObject({
      parent_set_id: null,
      parent_character_id: null,
    })
    expect(await setMembers(apiContext, child.id)).toEqual(ids)
  })

  test('deleting a person keeps their child set, now at the top level', async ({
    page,
    grid,
    sidebar,
    apiContext,
  }) => {
    const person = await apiPerson(apiContext, uniq('E2E doomed person'))
    const child = await apiSet(apiContext, uniq('E2E person orphan'), {
      parent_character_id: person.id,
    })
    await grid.goto()
    await showChildren(page, person.name)
    await expect(
      personGroup(sidebar, person.name).locator('.sidebar-set-item', {
        has: page.getByText(child.name, { exact: true }),
      }),
    ).toBeVisible()

    await openCtx(sidebar, personRow(sidebar, person.name))
    const confirmed = acceptConfirm(page)
    await sidebar.ctxButton('Delete').click()
    expect(await confirmed).toBe(`Delete character "${person.name}"?`)

    await expect(personRow(sidebar, person.name)).toHaveCount(0)
    await expect(setRow(sidebar, child.name)).toBeVisible()
    await expect(setRow(sidebar, child.name)).not.toHaveClass(/sidebar-tree-kid/)
    await expect(
      sidebar.page.locator('.sidebar-character-group .sidebar-set-item', {
        has: page.getByText(child.name, { exact: true }),
      }),
    ).toHaveCount(0)
    expect(await find(apiContext, 'set', child.id)).toMatchObject({
      parent_set_id: null,
      parent_character_id: null,
    })
  })
})

test('a locked child set refuses new members, a move and a delete', async ({
  page,
  grid,
  sidebar,
  apiContext,
}) => {
  const ids = await seededPictures(apiContext)
  const source = await apiSet(apiContext, uniq('E2E locked source'))
  await addToSet(apiContext, source.id, ids)
  const parent = await apiSet(apiContext, uniq('E2E locked parent'))
  const child = await apiSet(apiContext, uniq('E2E locked child'), { parent_set_id: parent.id })

  await selectAllIn(page, grid, source.id, ids.length)
  await showChildren(page, parent.name)
  const row = setRow(sidebar, child.name)
  await sidebar.lockSet(row)
  await expect(row.locator('.sidebar-lock-icon')).toBeVisible()
  await expect
    .poll(async () => (await find(apiContext, 'set', child.id)).locked)
    .toBe(true)

  await openCtx(sidebar, row)
  await expect(sidebar.ctxButton(`Move out of "${parent.name}"`)).toBeDisabled()
  await expect(sidebar.ctxButton('Delete')).toBeDisabled()
  await page.keyboard.press('Escape')
  await expect(sidebar.ctxMenu).toBeHidden()

  await dragPictureOnto(page, ids[0], row)

  await expect(page.locator('.sidebar-error-bubble')).toContainText('locked')
  expect(await setMembers(apiContext, child.id)).toEqual([])
  await expect(sidebar.count(row)).toHaveText('0')
  expect(await find(apiContext, 'set', child.id)).toMatchObject({ parent_set_id: parent.id })
})

test.describe('Train LoRA on a child set', () => {
  const trainDialog = (page) => page.getByRole('dialog', { name: 'Train a LoRA' })

  test('a set inside a set opens the dialog as a concept, "A thing"', async ({
    page,
    grid,
    sidebar,
    apiContext,
  }) => {
    const parent = await apiSet(apiContext, uniq('E2E train parent'))
    const child = await apiSet(apiContext, uniq('E2E train concept'), {
      parent_set_id: parent.id,
    })
    await grid.goto()
    await showChildren(page, parent.name)

    await openCtx(sidebar, setRow(sidebar, child.name))
    await sidebar.ctxButton('Train LoRA…').click()

    await expect(trainDialog(page)).toBeVisible()
    await expect.poll(() => pathname(page)).toBe(`/set/${child.id}`)
    await expect(trainDialog(page).getByTestId('tld-subject-thing')).toHaveAttribute(
      'aria-checked',
      'true',
    )
    await page.keyboard.press('Escape')
    await expect(trainDialog(page)).toBeHidden()
  })

  test('a set inside a person opens the dialog as that person\'s LoRA', async ({
    page,
    grid,
    sidebar,
    apiContext,
  }) => {
    const person = await apiPerson(apiContext, uniq('E2E train person'))
    const child = await apiSet(apiContext, uniq('E2E train likeness'), {
      parent_character_id: person.id,
    })
    await grid.goto()
    await showChildren(page, person.name)

    await openCtx(sidebar, setRow(sidebar, child.name))
    await sidebar.ctxButton('Train LoRA…').click()

    await expect(trainDialog(page)).toBeVisible()
    await expect.poll(() => pathname(page)).toBe(`/set/${child.id}`)
    await expect(trainDialog(page).getByTestId('tld-lead')).toContainText(
      `Character LoRA of ${person.name}`,
    )
    await page.keyboard.press('Escape')
    await expect(trainDialog(page)).toBeHidden()
  })
})
