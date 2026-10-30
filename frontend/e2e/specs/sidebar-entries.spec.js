import { test, expect } from '../fixtures/test.js'

// Creating, editing and deleting the sidebar's people, picture sets and
// projects (#2323): the add entry points, the three editors, and the sidebar's
// Delete. Every entry a test edits or deletes it creates first, through the UI
// or the API, under a name unique to the attempt, so the seeded fixture entries
// stay as they are and a CI retry never meets the first attempt's rows. Backend
// truth is read through the API after each UI gesture.

const API = '/api/v1'

let seq = 0
/** A name no other test, attempt or seeded entry carries. */
const uniq = (label) => `${label} ${Date.now().toString(36)}${++seq}`

async function body(res) {
  expect(res.ok(), `${res.url()} answered ${res.status()}`).toBe(true)
  return res.json()
}

// The list route of each kind. Existence is read from these: the single-id
// GETs answer with pictures rather than the entry.
const LIST = { set: 'picture_sets', person: 'characters', project: 'projects' }

/** The entry of that kind and id as the backend lists it, or undefined. */
async function find(ctx, kind, id) {
  const rows = await body(await ctx.get(`${API}/${LIST[kind]}`))
  return rows.find((r) => r.id === id)
}

/** The entry of that kind the backend lists under that name. */
async function findNamed(ctx, kind, name) {
  const rows = await body(await ctx.get(`${API}/${LIST[kind]}`))
  return rows.find((r) => r.name === name)
}

// What this test made, removed in afterEach if the test did not get to it.
// By id AND name: SQLite hands a deleted entry's id to the next one created.
let created

function track(kind, entry) {
  created.push({ kind, id: entry.id, name: entry.name })
  return entry
}

async function apiProject(ctx, name) {
  return track('project', await body(await ctx.post(`${API}/projects`, { data: { name } })))
}

async function apiSet(ctx, name, extra = {}) {
  const res = await ctx.post(`${API}/picture_sets`, { data: { name, ...extra } })
  return track('set', (await body(res)).picture_set)
}

async function apiCharacter(ctx, name, extra = {}) {
  const res = await ctx.post(`${API}/characters`, { data: { name, ...extra } })
  return track('person', (await body(res)).character)
}

const listPictures = async (ctx, params) =>
  body(await ctx.get(`${API}/pictures`, { params }))

const pictureIds = (rows) => rows.map((p) => p.id).sort((a, b) => a - b)

const totalPictures = async (ctx) =>
  (await body(await ctx.get(`${API}/pictures/count`))).count

/** The rows of the given pictures that are still live in the library. */
async function livePictures(ctx, ids) {
  const params = new URLSearchParams()
  for (const id of ids) params.append('id', String(id))
  const rows = await body(await ctx.get(`${API}/pictures?${params}`))
  return pictureIds(rows.filter((p) => !p.deleted))
}

/** A few pictures of a seeded person: read only, they keep their person. */
async function seededPersonPictures(ctx) {
  const people = await body(await ctx.get(`${API}/characters`))
  for (const person of people) {
    const rows = await listPictures(ctx, { character_id: person.id })
    if (rows.length >= 2) return { person, ids: pictureIds(rows.slice(0, 3)) }
  }
  throw new Error('the fixture has no person with two pictures')
}

async function addToSet(ctx, setId, ids) {
  await body(
    await ctx.post(`${API}/picture_sets/${setId}/members`, {
      data: { picture_ids: ids },
    }),
  )
}

/** Accept the next native confirm and hand back its text. */
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
// By role: the set editor's Locked checkbox carries "description" in its help.
const descriptionField = (page) =>
  editor(page).getByRole('textbox', { name: 'Description' })
const saveButton = (page) => editor(page).getByRole('button', { name: 'Save', exact: true })

/** The one visible control by accessible name (the docked rail carries copies). */
const visibleButton = (page, name) =>
  page.getByRole('button', { name, exact: true }).filter({ visible: true }).first()

const setRow = (sidebar, name) => sidebar.setItems.filter({ hasText: name })
const personRow = (sidebar, name) => sidebar.characterItems.filter({ hasText: name })

async function openEditor(sidebar, row) {
  await row.click({ button: 'right' })
  await expect(sidebar.ctxMenu).toBeVisible()
  await sidebar.ctxButton('Edit').click()
  await expect(editor(sidebar.page)).toBeVisible()
}

test.beforeEach(() => {
  created = []
})

test.afterEach(async ({ apiContext }) => {
  for (const { kind, id, name } of created) {
    const left = await find(apiContext, kind, id)
    if (left?.name !== name) continue
    // A locked set refuses its own delete.
    if (left.locked) {
      await apiContext.patch(`${API}/picture_sets/${id}`, { data: { locked: false } })
    }
    await apiContext.delete(`${API}/${LIST[kind]}/${id}`)
  }
})

test.describe('people', () => {
  test('a person made from the sidebar opens and survives a reload', async ({
    page,
    grid,
    sidebar,
    apiContext,
  }) => {
    const name = uniq('E2E person')
    await grid.goto()

    await visibleButton(page, 'Add person').click()
    await expect(editor(page)).toContainText('New person')
    await nameField(page).fill(name)
    await descriptionField(page).fill('made by e2e')
    await saveButton(page).click()
    await expect(editor(page)).toBeHidden()

    const person = await findNamed(apiContext, 'person', name)
    expect(person, 'the person reached the backend').toBeTruthy()
    track('person', person)
    expect(person.description).toBe('made by e2e')

    await expect(personRow(sidebar, name)).toBeVisible()
    await expect.poll(() => pathname(page)).toBe(`/character/${person.id}`)

    await page.reload()
    await expect(personRow(sidebar, name)).toBeVisible()
  })

  test('the editor renames a person and refuses an empty or taken name', async ({
    page,
    grid,
    sidebar,
    apiContext,
  }) => {
    // Names are unique within a project, so the clash needs one.
    const project = await apiProject(apiContext, uniq('E2E people project'))
    const taken = await apiCharacter(apiContext, uniq('E2E taken'), {
      project_ids: [project.id],
    })
    const person = await apiCharacter(apiContext, uniq('E2E rename me'), {
      project_ids: [project.id],
    })
    const renamed = uniq('E2E renamed')
    await grid.goto()

    await openEditor(sidebar, personRow(sidebar, person.name))
    await nameField(page).fill('   ')
    await expect(saveButton(page)).toBeDisabled()

    await nameField(page).fill(taken.name)
    await saveButton(page).click()
    await expect(page.locator('.notice-message')).toContainText('already exists')
    await expect(editor(page), 'a refused save keeps the editor open').toBeVisible()
    const unchanged = await find(apiContext, 'person', person.id)
    expect(unchanged.name).toBe(person.name)

    await nameField(page).fill(renamed)
    await descriptionField(page).fill('renamed by e2e')
    await saveButton(page).click()
    await expect(editor(page)).toBeHidden()
    await expect(personRow(sidebar, renamed)).toBeVisible()
    await expect(personRow(sidebar, person.name)).toHaveCount(0)

    await page.reload()
    await expect(personRow(sidebar, renamed)).toBeVisible()
    const saved = await find(apiContext, 'person', person.id)
    expect(saved).toMatchObject({ name: renamed, description: 'renamed by e2e' })
  })

  test('deleting the person on screen keeps their pictures and leaves for All Pictures', async ({
    page,
    grid,
    sidebar,
    apiContext,
  }) => {
    const person = await apiCharacter(apiContext, uniq('E2E doomed person'))
    // Faces nobody owns yet, so the seeded people lose none of theirs.
    const loose = await listPictures(apiContext, { character_id: 'UNASSIGNED' })
    await body(
      await apiContext.post(`${API}/characters/${person.id}/faces`, {
        data: { picture_ids: pictureIds(loose.slice(0, 6)) },
      }),
    )
    const ids = pictureIds(await listPictures(apiContext, { character_id: person.id }))
    expect(ids.length, 'the fixture has loose faces to give the person').toBeGreaterThan(0)
    const keeper = await apiSet(apiContext, uniq('E2E keeper set'))
    await addToSet(apiContext, keeper.id, ids)
    const total = await totalPictures(apiContext)

    await grid.goto()
    await personRow(sidebar, person.name).click()
    await expect.poll(() => pathname(page)).toBe(`/character/${person.id}`)

    await personRow(sidebar, person.name).click({ button: 'right' })
    const confirmed = acceptConfirm(page)
    await sidebar.ctxButton('Delete').click()
    expect(await confirmed).toBe(`Delete character "${person.name}"?`)

    await expect(personRow(sidebar, person.name)).toHaveCount(0)
    await expect.poll(() => pathname(page)).toBe('/')
    await grid.waitForThumbnailLoaded()

    expect(await find(apiContext, 'person', person.id)).toBeUndefined()
    expect(await totalPictures(apiContext)).toBe(total)
    expect(await livePictures(apiContext, ids)).toEqual(ids)
    expect(pictureIds(await listPictures(apiContext, { set_id: keeper.id }))).toEqual(ids)
  })
})

test.describe('picture sets', () => {
  test('a set made from the sidebar opens and survives a reload', async ({
    page,
    grid,
    sidebar,
    apiContext,
  }) => {
    const name = uniq('E2E set')
    await grid.goto()

    await visibleButton(page, 'Create new set').click()
    await expect(editor(page)).toContainText('New picture set')
    await nameField(page).fill(name)
    await descriptionField(page).fill('made by e2e')
    await saveButton(page).click()
    await expect(editor(page)).toBeHidden()

    const set = await findNamed(apiContext, 'set', name)
    expect(set, 'the set reached the backend').toBeTruthy()
    track('set', set)
    expect(set.description).toBe('made by e2e')

    await expect(setRow(sidebar, name)).toBeVisible()
    await expect.poll(() => pathname(page)).toBe(`/set/${set.id}`)
    await expect(setRow(sidebar, name)).toHaveClass(/active/)

    await page.reload()
    await expect(setRow(sidebar, name)).toBeVisible()
  })

  test('the editor renames a set and refuses an empty or taken name', async ({
    page,
    grid,
    sidebar,
    apiContext,
  }) => {
    const project = await apiProject(apiContext, uniq('E2E sets project'))
    const taken = await apiSet(apiContext, uniq('E2E taken set'), {
      project_ids: [project.id],
    })
    const set = await apiSet(apiContext, uniq('E2E rename set'), {
      project_ids: [project.id],
    })
    const renamed = uniq('E2E renamed set')
    await grid.goto()

    await openEditor(sidebar, setRow(sidebar, set.name))
    await nameField(page).fill('')
    await expect(saveButton(page)).toBeDisabled()

    await nameField(page).fill(taken.name)
    await saveButton(page).click()
    await expect(page.locator('.notice-message')).toContainText('already exists')
    await expect(editor(page), 'a refused save keeps the editor open').toBeVisible()
    const unchanged = await find(apiContext, 'set', set.id)
    expect(unchanged.name).toBe(set.name)

    await nameField(page).fill(renamed)
    await descriptionField(page).fill('renamed by e2e')
    await saveButton(page).click()
    await expect(editor(page)).toBeHidden()
    await expect(setRow(sidebar, renamed)).toBeVisible()

    await page.reload()
    await expect(setRow(sidebar, renamed)).toBeVisible()
    await expect(setRow(sidebar, set.name)).toHaveCount(0)
    const saved = await find(apiContext, 'set', set.id)
    expect(saved).toMatchObject({ name: renamed, description: 'renamed by e2e' })
  })

  test('deleting the set on screen keeps its pictures and their other memberships', async ({
    page,
    grid,
    sidebar,
    apiContext,
  }) => {
    const { person, ids } = await seededPersonPictures(apiContext)
    const doomed = await apiSet(apiContext, uniq('E2E doomed set'))
    const keeper = await apiSet(apiContext, uniq('E2E keeper set'))
    await addToSet(apiContext, doomed.id, ids)
    await addToSet(apiContext, keeper.id, ids)
    const total = await totalPictures(apiContext)

    await grid.goto()
    await setRow(sidebar, doomed.name).click()
    await expect.poll(() => pathname(page)).toBe(`/set/${doomed.id}`)

    await setRow(sidebar, doomed.name).click({ button: 'right' })
    const confirmed = acceptConfirm(page)
    await sidebar.ctxButton('Delete').click()
    expect(await confirmed).toBe(
      `Delete picture set "${doomed.name}"? This will unassign all their images.`,
    )

    await expect(setRow(sidebar, doomed.name)).toHaveCount(0)
    await expect.poll(() => pathname(page)).toBe('/')
    await grid.waitForThumbnailLoaded()

    expect(await find(apiContext, 'set', doomed.id)).toBeUndefined()
    expect(await totalPictures(apiContext)).toBe(total)
    expect(await livePictures(apiContext, ids)).toEqual(ids)
    expect(pictureIds(await listPictures(apiContext, { set_id: keeper.id }))).toEqual(ids)
    const stillTheirs = pictureIds(await listPictures(apiContext, { character_id: person.id }))
    expect(stillTheirs).toEqual(expect.arrayContaining(ids))
  })

  test('several selected sets delete together', async ({
    page,
    grid,
    sidebar,
    apiContext,
  }) => {
    const { ids } = await seededPersonPictures(apiContext)
    const first = await apiSet(apiContext, uniq('E2E multi one'))
    const second = await apiSet(apiContext, uniq('E2E multi two'))
    const bystander = await apiSet(apiContext, uniq('E2E multi bystander'))
    await addToSet(apiContext, first.id, ids)
    await addToSet(apiContext, second.id, ids)
    const total = await totalPictures(apiContext)

    await grid.goto()
    await setRow(sidebar, first.name).click()
    await setRow(sidebar, second.name).click({ modifiers: ['ControlOrMeta'] })
    await expect(grid.multiSelectLabel).toContainText(/2 sets selected/i)

    await setRow(sidebar, second.name).click({ button: 'right' })
    const confirmed = acceptConfirm(page)
    await sidebar.ctxButton('Delete 2 sets').click()
    expect(await confirmed).toBe(
      'Delete 2 picture sets? This will unassign all their images.',
    )

    await expect(setRow(sidebar, first.name)).toHaveCount(0)
    await expect(setRow(sidebar, second.name)).toHaveCount(0)
    await expect(setRow(sidebar, bystander.name)).toBeVisible()
    await expect.poll(() => pathname(page)).toBe('/')
    await expect(grid.multiSelectToolbar).toBeHidden()

    for (const s of [first, second]) {
      expect(await find(apiContext, 'set', s.id)).toBeUndefined()
    }
    expect(await totalPictures(apiContext)).toBe(total)
    expect(await livePictures(apiContext, ids)).toEqual(ids)
  })

  test('a locked set in the selection is refused while the rest still go', async ({
    page,
    grid,
    sidebar,
    apiContext,
    notices,
  }) => {
    const open = await apiSet(apiContext, uniq('E2E unlocked'))
    const locked = await apiSet(apiContext, uniq('E2E locked'))
    await body(
      await apiContext.patch(`${API}/picture_sets/${locked.id}`, { data: { locked: true } }),
    )
    await grid.goto()
    await setRow(sidebar, open.name).click()
    await setRow(sidebar, locked.name).click({ modifiers: ['ControlOrMeta'] })

    // Delete is gated on the row right-clicked, so the unlocked one offers it.
    await setRow(sidebar, open.name).click({ button: 'right' })
    const confirmed = acceptConfirm(page)
    await sidebar.ctxButton('Delete 2 sets').click()
    await confirmed

    await expect(notices.messages).toContainText("Couldn't delete those sets")
    await expect(setRow(sidebar, open.name)).toHaveCount(0)
    await expect(setRow(sidebar, locked.name)).toBeVisible()
    await expect.poll(() => pathname(page)).toBe('/')
    expect(await find(apiContext, 'set', open.id)).toBeUndefined()
    expect(await find(apiContext, 'set', locked.id)).toMatchObject({ locked: true })
  })

  test('a locked set can be neither deleted nor edited until it is unlocked', async ({
    page,
    grid,
    sidebar,
    apiContext,
  }) => {
    const set = await apiSet(apiContext, uniq('E2E locked set'))
    await body(
      await apiContext.patch(`${API}/picture_sets/${set.id}`, { data: { locked: true } }),
    )
    await grid.goto()
    const row = setRow(sidebar, set.name)

    await row.click({ button: 'right' })
    await expect(sidebar.ctxButton('Delete')).toBeDisabled()
    await sidebar.ctxButton('Edit').click()
    await expect(nameField(page)).toBeDisabled()
    await expect(descriptionField(page)).toBeDisabled()

    // Unticking Locked and saving is the unlock path; nothing else is sent.
    await editor(page).getByRole('checkbox', { name: /^Locked/ }).uncheck()
    await saveButton(page).click()
    await expect(editor(page)).toBeHidden()
    await expect
      .poll(async () => (await find(apiContext, 'set', set.id)).locked)
      .toBe(false)

    await openEditor(sidebar, row)
    await expect(nameField(page)).toBeEnabled()
    await page.keyboard.press('Escape')
    await expect(editor(page)).toBeHidden()
    await row.click({ button: 'right' })
    await expect(sidebar.ctxButton('Delete')).toBeEnabled()
    await page.keyboard.press('Escape')
  })
})

test.describe('projects', () => {
  test('a project made from the scope menu opens and survives a reload', async ({
    page,
    grid,
    sidebar,
    apiContext,
  }) => {
    const name = uniq('E2E project')
    await grid.goto()

    await sidebar.openScopeMenu()
    await sidebar.scopeMenu.getByRole('menuitem', { name: 'New project…' }).click()
    await expect(editor(page)).toContainText('New project')
    await nameField(page).fill(name)
    await descriptionField(page).fill('made by e2e')
    await saveButton(page).click()
    await expect(editor(page)).toBeHidden()

    const project = await findNamed(apiContext, 'project', name)
    expect(project, 'the project reached the backend').toBeTruthy()
    track('project', project)
    expect(project.description).toBe('made by e2e')

    await expect.poll(() => pathname(page)).toBe(`/project/${project.id}`)
    await expect(sidebar.scopeButton).toContainText(name)

    await page.reload()
    await expect(sidebar.scopeButton).toContainText(name)
  })

  test('the editor renames a project and refuses an empty or taken name', async ({
    page,
    sidebar,
    apiContext,
  }) => {
    const taken = await apiProject(apiContext, uniq('E2E taken project'))
    const project = await apiProject(apiContext, uniq('E2E rename project'))
    const renamed = uniq('E2E renamed project')
    await page.goto(`/project/${project.id}`)
    await expect(sidebar.scopeButton).toContainText(project.name)

    await sidebar.openScopeMenu()
    await sidebar.scopeMenu
      .getByRole('menuitem', { name: `Edit “${project.name}”…` })
      .click()
    await expect(editor(page)).toContainText('Edit project')

    // This editor reports in place rather than disabling Save.
    await nameField(page).fill('')
    await saveButton(page).click()
    await expect(editor(page)).toContainText('Name is required.')

    await nameField(page).fill(taken.name)
    await saveButton(page).click()
    await expect(editor(page)).toContainText('Project name already exists')
    const unchanged = await find(apiContext, 'project', project.id)
    expect(unchanged.name).toBe(project.name)

    await nameField(page).fill(renamed)
    await descriptionField(page).fill('renamed by e2e')
    await saveButton(page).click()
    await expect(editor(page)).toBeHidden()
    await expect(sidebar.scopeButton).toContainText(renamed)

    await page.reload()
    await expect(sidebar.scopeButton).toContainText(renamed)
    const saved = await find(apiContext, 'project', project.id)
    expect(saved).toMatchObject({ name: renamed, description: 'renamed by e2e' })
  })

  test('deleting a project keeps its sets, people and pictures, outside it', async ({
    page,
    sidebar,
    apiContext,
  }) => {
    const { ids } = await seededPersonPictures(apiContext)
    const project = await apiProject(apiContext, uniq('E2E doomed project'))
    const set = await apiSet(apiContext, uniq('E2E project set'), {
      project_ids: [project.id],
    })
    const person = await apiCharacter(apiContext, uniq('E2E project person'), {
      project_ids: [project.id],
    })
    await addToSet(apiContext, set.id, ids)
    const total = await totalPictures(apiContext)

    await page.goto(`/project/${project.id}`)
    await expect(sidebar.scopeButton).toContainText(project.name)
    await expect(setRow(sidebar, set.name)).toBeVisible()

    // Right-click on the slot opens the current project's own menu.
    await sidebar.scopeButton.click({ button: 'right' })
    await expect(sidebar.ctxMenu).toBeVisible()
    const confirmed = acceptConfirm(page)
    await sidebar.ctxButton('Delete').click()
    expect(await confirmed).toBe(
      `Delete project "${project.name}"? Its people, sets and pictures stay in the library; its attachments are deleted.`,
    )

    await expect(sidebar.scopeButton).toContainText('Any project')
    await expect.poll(() => pathname(page)).not.toContain('/project/')
    // Kept, and now outside any project, so they list under Any project.
    await expect(setRow(sidebar, set.name)).toBeVisible()
    await expect(personRow(sidebar, person.name)).toBeVisible()

    expect(await find(apiContext, 'project', project.id)).toBeUndefined()
    const keptSet = await find(apiContext, 'set', set.id)
    expect(keptSet.project_ids).toEqual([])
    const keptPerson = await find(apiContext, 'person', person.id)
    expect(keptPerson.project_ids).toEqual([])
    expect(await totalPictures(apiContext)).toBe(total)
    expect(pictureIds(await listPictures(apiContext, { set_id: set.id }))).toEqual(ids)
  })
})
