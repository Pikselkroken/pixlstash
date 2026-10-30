// The Moves queue (/moves, MovesReview.vue) with real reviews in it (#2325).
//
// A review exists only when a scan finds a file the owner moved inside a
// laid-out folder. The e2e launcher seeds one such folder and leaves one move
// pending in it (#2320). Beyond that seeded review, each test makes its own:
// it drops a new picture into the seeded folder, waits for the scan to bring
// it in, then moves the file on disk into a folder named after a project and
// waits for the rescan to queue the review. Every test then asserts on its
// own pictures, never on a count of the whole queue.
//
// The seeded review is read first and acted on only by the bulk actions at
// the end of the file.

import { mkdirSync, renameSync } from 'node:fs'
import { join } from 'node:path'
import { test, expect } from '../fixtures/test.js'
import {
  expectScratch,
  markFolderChanged,
  seededReferenceFolder,
  waitForPicture,
  writePicture,
} from '../fixtures/folders.js'

let seed = Date.now() % 100_000

/** Every project, by name. */
async function projectsByName(apiContext) {
  const res = await apiContext.get('/api/v1/projects')
  expect(res.ok(), await res.text()).toBe(true)
  return new Map((await res.json()).map((p) => [p.name, p.id]))
}

/** The names of the projects a picture is a member of. */
async function projectsOf(apiContext, pictureId) {
  const names = []
  for (const [name, id] of await projectsByName(apiContext)) {
    const res = await apiContext.get('/api/v1/pictures', {
      params: { project_id: String(id), fields: 'grid' },
    })
    expect(res.ok(), await res.text()).toBe(true)
    if ((await res.json()).some((p) => p.id === pictureId)) names.push(name)
  }
  return names.sort()
}

async function pending(apiContext) {
  const res = await apiContext.get('/api/v1/moves/pending')
  expect(res.ok(), await res.text()).toBe(true)
  return res.json()
}

/** The pending review for a picture, from whichever bucket holds it. */
async function reviewFor(apiContext, pictureId) {
  const queue = await pending(apiContext)
  return [...queue.unambiguous, ...queue.ambiguous, ...queue.off_layout].find(
    (r) => r.picture_id === pictureId,
  )
}

/**
 * Put a new picture in the seeded folder at *from*, let the scan bring it in,
 * then move the file to *to* (both relative to the folder) and wait for the
 * review. *before* runs between the two, on the picture's id.
 */
async function pictureMovedOnDisk(apiContext, { from, to, before }) {
  const folder = await seededReferenceFolder(apiContext)
  expectScratch(folder.folder)
  seed += 1
  const name = `moved-${seed}.png`
  const source = join(folder.folder, from, name)
  writePicture(source, seed)
  await markFolderChanged(apiContext, folder.id)
  const pictureId = await waitForPicture(apiContext, folder.id, source)
  if (before) await before(pictureId)
  const target = join(folder.folder, to, name)
  mkdirSync(join(folder.folder, to), { recursive: true })
  renameSync(source, target)
  await markFolderChanged(apiContext, folder.id)
  await expect
    .poll(() => reviewFor(apiContext, pictureId), {
      message: `moving ${name} to ${to} queued no review`,
      timeout: 30_000,
    })
    .toBeTruthy()
  return pictureId
}

/** The queue's row for one picture: its thumbnail is the only per-row id. */
function rowFor(page, pictureId) {
  return page.locator('.mv-row', {
    has: page.locator(`img.mv-thumb[src*="/thumbnails/${pictureId}."]`),
  })
}

test.describe('the Moves queue', () => {
  test('lists the seeded review with the change it proposes', async ({
    page,
    apiContext,
  }) => {
    const queue = await pending(apiContext)
    const seeded = queue.unambiguous.find((r) => r.old_path.endsWith('fern.png'))
    expect(seeded, JSON.stringify(queue)).toBeTruthy()
    expect(seeded.removals).toEqual([])
    expect(seeded.additions).toHaveLength(1)
    const [{ facet, name }] = seeded.additions
    expect(facet).toBe('project')

    await page.goto('/moves')
    const view = page.getByTestId('moves-review')
    const row = rowFor(page, seeded.picture_id)
    await expect(row).toBeVisible()
    // Where the file went, and what that would add.
    await expect(row.locator('.mv-path')).toHaveText(`…/reference-folder/${name}`)
    await expect(row.locator('.mv-etag--in')).toHaveText(new RegExp(`\\+\\s*Project\\s*${name}`))
    await expect(row.getByRole('button', { name: 'Apply' })).toBeVisible()
    // Nothing is applied by looking.
    await expect(view.locator('.mv-sub')).toContainText('nothing has been changed yet')
    expect(await reviewFor(apiContext, seeded.picture_id)).toBeTruthy()
    expect(await projectsOf(apiContext, seeded.picture_id)).not.toContain(name)
  })

  test('Apply on one row adds the picture to the project it was moved into', async ({
    page,
    apiContext,
  }) => {
    const pictureId = await pictureMovedOnDisk(apiContext, { from: 'Inbox', to: 'Test' })
    expect(await projectsOf(apiContext, pictureId)).toEqual([])

    await page.goto('/moves')
    const row = rowFor(page, pictureId)
    await expect(row.locator('.mv-etag--in')).toContainText('Test')
    const others = await page.locator('.mv-row').count()
    await row.getByRole('button', { name: 'Apply' }).click()

    await expect(row).toHaveCount(0)
    await expect(page.locator('.mv-row')).toHaveCount(others - 1)
    expect(await projectsOf(apiContext, pictureId)).toEqual(['Test'])
    expect(await reviewFor(apiContext, pictureId)).toBeUndefined()
  })

  test('Keep both on a two-way move removes the review and changes nothing', async ({
    page,
    apiContext,
  }) => {
    const projects = await projectsByName(apiContext)
    // In two projects and filed under one; moved into the other's folder.
    // Leaving "Test" could mean "not Test any more" or "just refiled", so
    // the queue asks rather than guessing.
    const pictureId = await pictureMovedOnDisk(apiContext, {
      from: 'Test',
      to: 'Release Project',
      before: async (id) => {
        for (const name of ['Test', 'Release Project']) {
          const res = await apiContext.patch('/api/v1/pictures/project', {
            data: { picture_ids: [id], project_id: projects.get(name), mode: 'add' },
          })
          expect(res.ok(), await res.text()).toBe(true)
        }
      },
    })
    const review = await reviewFor(apiContext, pictureId)
    expect(review.removals).toEqual([{ facet: 'project', name: 'Test' }])
    expect([...review.current.project].sort()).toEqual(['Release Project', 'Test'])

    await page.goto('/moves')
    const row = page.locator('.mv-card--warn').locator(rowFor(page, pictureId))
    await expect(row.locator('.mv-current')).toHaveText(
      `in ${review.current.project.join(' and ')}`,
    )
    await expect(row.getByRole('button', { name: 'Only Release Project now' })).toBeVisible()
    await row.getByRole('button', { name: 'Keep both' }).click()

    await expect(rowFor(page, pictureId)).toHaveCount(0)
    expect(await reviewFor(apiContext, pictureId)).toBeUndefined()
    expect(await projectsOf(apiContext, pictureId)).toEqual(['Release Project', 'Test'])
  })

  test('Apply all applies every clear review at once', async ({ page, apiContext }) => {
    const first = await pictureMovedOnDisk(apiContext, { from: 'Inbox', to: 'Test' })
    const second = await pictureMovedOnDisk(apiContext, { from: 'Inbox', to: 'Test' })

    await page.goto('/moves')
    const card = page.locator('.mv-card--clear')
    await expect(card.locator(rowFor(page, first))).toBeVisible()
    await expect(card.locator(rowFor(page, second))).toBeVisible()
    const clear = (await pending(apiContext)).unambiguous.length
    await card.getByRole('button', { name: `Apply all ${clear}` }).click()

    await expect(card).toHaveCount(0)
    expect((await pending(apiContext)).unambiguous).toEqual([])
    expect(await projectsOf(apiContext, first)).toEqual(['Test'])
    expect(await projectsOf(apiContext, second)).toEqual(['Test'])
  })

  test('"Leave everything as it was" empties the queue and changes nothing', async ({
    page,
    apiContext,
  }) => {
    const pictureId = await pictureMovedOnDisk(apiContext, { from: 'Inbox', to: 'Test' })

    await page.goto('/moves')
    await expect(rowFor(page, pictureId)).toBeVisible()
    await page.getByRole('button', { name: 'Leave everything as it was' }).click()

    // The empty state, and the bulk actions gone with the rows.
    await expect(page.getByText('Nothing to reconcile right now.')).toBeVisible()
    await expect(page.locator('.mv-row')).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Leave everything as it was' })).toHaveCount(0)
    await expect(page.locator('.mv-sub')).toHaveText('nothing has been changed yet')
    const queue = await pending(apiContext)
    expect([...queue.unambiguous, ...queue.ambiguous]).toEqual([])
    expect(await projectsOf(apiContext, pictureId)).toEqual([])
  })
})
