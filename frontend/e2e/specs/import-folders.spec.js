// An import folder, added the way the owner adds one and fed a picture on
// disk (#2325).
//
// Import folders are added in Settings › Libraries & imports, and what they bring
// in waits in the Inbox (/inbox; the old /import-folder/:id route redirects
// there). The pickup is the planner's import-folder sweep, which polls, so
// nothing here waits on a filesystem watcher. The folder is a mkdtemp folder.

import { existsSync, rmSync } from 'node:fs'
import { basename, join } from 'node:path'
import { test, expect } from '../fixtures/test.js'
import {
  discardPictures,
  expectScratch,
  scratchFolder,
  writePicture,
} from '../fixtures/folders.js'

test.describe('an import folder', () => {
  let folder = null
  let folderId = null
  let pictureId = null

  test.afterAll(async ({ apiContext }) => {
    if (folderId != null) {
      const res = await apiContext.delete(`/api/v1/import-folders/${folderId}`)
      expect(res.ok(), await res.text()).toBe(true)
    }
    // The picture goes again, so later specs (and the Inbox) read the
    // fixture as they would have.
    if (pictureId != null) await discardPictures(apiContext, [pictureId])
    if (folder) {
      expectScratch(folder)
      rmSync(folder, { recursive: true, force: true })
    }
  })

  test('added in Settings, it imports a dropped picture into the Inbox', async ({
    page,
    apiContext,
    settings,
  }) => {
    folder = scratchFolder('import')

    await page.goto('/')
    await settings.open()
    await settings.openTab('Libraries & imports')
    await settings.card.getByRole('button', { name: 'Add import folder…' }).click()
    const dialog = page.getByRole('dialog', { name: 'Add import folder', exact: true })
    await dialog.getByLabel('Folder', { exact: true }).fill(folder)
    await dialog.getByRole('button', { name: 'Add', exact: true }).click()
    await expect(dialog).toBeHidden()
    const row = settings.card.locator('.ifs-row', { hasText: basename(folder) })
    await expect(row).toContainText('Keep original')

    const listed = await (await apiContext.get('/api/v1/import-folders')).json()
    const registered = listed.folders.find((f) => f.folder === folder)
    expect(registered, JSON.stringify(listed.folders.map((f) => f.folder))).toBeTruthy()
    folderId = registered.id

    // A file arriving on disk is picked up once it has settled.
    const file = join(folder, 'arrival.png')
    writePicture(file, 401)
    await expect
      .poll(
        async () => {
          const res = await apiContext.get('/api/v1/pictures', {
            params: { import_source_folder: folder, fields: 'grid' },
          })
          return (await res.json()).map((p) => p.id)
        },
        { message: 'the dropped picture was never imported', timeout: 30_000 },
      )
      .toHaveLength(1)
    pictureId = (
      await (
        await apiContext.get('/api/v1/pictures', {
          params: { import_source_folder: folder, fields: 'grid' },
        })
      ).json()
    )[0].id
    // "Keep original": the file the owner dropped is still where they put it.
    expect(existsSync(file)).toBe(true)

    // The Inbox lists the batch from this folder, and narrowed to the folder
    // the grid holds exactly that picture.
    await page.goto('/inbox')
    const batch = page.locator('.inbox-batch', { hasText: registered.label || folder })
    await expect(batch).toContainText('1 new')
    await expect(page.locator(`.image-card img[src*="/thumbnails/${pictureId}."]`)).toBeVisible()
    await batch.locator('.inbox-batch__name').click()
    await expect(page).toHaveURL(/\/inbox\?folder=/)
    expect(new URL(page.url()).searchParams.get('folder')).toBe(folder)
    await expect(page.locator('.image-card img[src*="/thumbnails/"]')).toHaveCount(1)
    await expect(page.locator(`.image-card img[src*="/thumbnails/${pictureId}."]`)).toBeVisible()
  })

  test('the old /import-folder/:id address lands on the Inbox', async ({ page }) => {
    await page.goto('/import-folder/1')
    await expect(page).toHaveURL(/\/inbox$/)
    await expect(page.locator('.inbox-bar')).toBeVisible()
  })
})
