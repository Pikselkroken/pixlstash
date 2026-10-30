// A reference folder from registration to removal, with real pictures in it
// (#2325).
//
// The folder is a mkdtemp folder this spec writes PNGs into. Reference folders
// have no sidebar row and no add, relocate or remove control any more: adding
// a folder from the UI is "Add a library", and a reference folder's view is
// reached by URL (docs/frontend_architecture.md, the sidebar list). So the
// folder is registered, relocated and removed through the API, and what the
// owner sees is checked on /ref-folder/:id.
//
// Serial: each step works on what the one before left. A retry reruns the
// group from beforeAll, against a new folder.

import { existsSync, rmSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { test, expect } from '../fixtures/test.js'
import {
  expectScratch,
  markFolderChanged,
  picturesInFolder,
  scratchFolder,
  waitForPicture,
  writePicture,
} from '../fixtures/folders.js'

test.describe.configure({ mode: 'serial' })

test.describe('a reference folder', () => {
  let root = null
  let relocatedTo = null
  let folderId = null
  // file path → picture id, as the scan found them.
  const pictures = new Map()
  const files = ['dune.png', 'reef.png', join('2024', 'tide.png')]

  test.beforeAll(async ({ apiContext }) => {
    root = scratchFolder('ref')
    files.forEach((name, i) => writePicture(join(root, name), 200 + i))
    const created = await apiContext.post('/api/v1/reference-folders', {
      data: { folder: root },
    })
    expect(created.ok(), await created.text()).toBe(true)
    const folder = await created.json()
    expect(folder.folder).toBe(root)
    folderId = folder.id
  })

  test.afterAll(async ({ apiContext }) => {
    // Whatever the tests got to: the registration goes, then the folders this
    // spec made, and nothing else.
    if (folderId != null) {
      const res = await apiContext.delete(`/api/v1/reference-folders/${folderId}`)
      expect([200, 404], await res.text()).toContain(res.status())
    }
    for (const dir of [root, relocatedTo]) {
      if (!dir) continue
      expectScratch(dir)
      rmSync(dir, { recursive: true, force: true })
    }
  })

  const sorted = (ids) => [...ids].sort()

  /** The pictures directly in *folder*: the folder view lists those only,
      and a subfolder is reached through ?path=. */
  const directlyIn = (folder) =>
    [...pictures].filter(([path]) => dirname(path) === folder).map(([, id]) => id)

  /** Wait for the grid to show exactly *ids*, read off its thumbnails. */
  async function expectGrid(page, ids) {
    await expect
      .poll(async () => {
        const srcs = await page
          .locator('.image-card img[src*="/thumbnails/"]')
          .evaluateAll((imgs) => imgs.map((img) => img.getAttribute('src')))
        return sorted(srcs.map((src) => Number(src.match(/\/thumbnails\/(\d+)\./)[1])))
      })
      .toEqual(sorted(ids))
  }

  test('its pictures are scanned in and listed on its own view', async ({
    page,
    apiContext,
  }) => {
    // A new folder has never been scanned, so the planner scans it at once.
    for (const name of files) {
      const path = join(root, name)
      pictures.set(path, await waitForPicture(apiContext, folderId, path))
    }
    await page.goto(`/ref-folder/${folderId}`)
    await expectGrid(page, directlyIn(root))
  })

  test('a file added on disk appears after the folder is rescanned', async ({
    page,
    apiContext,
  }) => {
    const path = join(root, 'kelp.png')
    writePicture(path, 299)
    await markFolderChanged(apiContext, folderId)
    pictures.set(path, await waitForPicture(apiContext, folderId, path))

    await page.goto(`/ref-folder/${folderId}`)
    await expectGrid(page, directlyIn(root))
  })

  test('?path= narrows the grid to one subfolder', async ({ page }) => {
    const inSub = pictures.get(join(root, '2024', 'tide.png'))
    await page.goto(`/ref-folder/${folderId}?path=2024`)
    await expectGrid(page, [inSub])
  })

  test('relocating keeps every picture, its tags and its memberships', async ({
    page,
    apiContext,
  }) => {
    const [first, second] = pictures.values()
    const tagged = await apiContext.post(`/api/v1/pictures/${first}/tags`, {
      data: { tag: 'e2e relocated' },
    })
    expect(tagged.ok(), await tagged.text()).toBe(true)
    const projects = await (await apiContext.get('/api/v1/projects')).json()
    const project = projects[0]
    const joined = await apiContext.patch('/api/v1/pictures/project', {
      data: { picture_ids: [second], project_id: project.id, mode: 'add' },
    })
    expect(joined.ok(), await joined.text()).toBe(true)

    // The relocate moves every file on disk: both ends must be this run's.
    relocatedTo = scratchFolder('ref-moved')
    expectScratch(root)
    expectScratch(relocatedTo)
    const res = await apiContext.post(`/api/v1/reference-folders/${folderId}/relocate`, {
      data: { destination_folder: relocatedTo },
    })
    expect(res.ok(), await res.text()).toBe(true)
    expect((await res.json()).new_folder).toBe(relocatedTo)

    // The same pictures, under the new root, with nothing left at the old.
    const after = await picturesInFolder(apiContext, folderId)
    const expected = new Map(
      [...pictures].map(([path, id]) => [path.replace(root, relocatedTo), id]),
    )
    expect([...after].sort()).toEqual([...expected].sort())
    for (const path of expected.keys()) expect(existsSync(path), path).toBe(true)
    expect(existsSync(root)).toBe(false)

    const tags = await (await apiContext.get(`/api/v1/pictures/${first}/tags`)).json()
    expect(JSON.stringify(tags)).toContain('e2e relocated')
    const members = await apiContext.get('/api/v1/pictures', {
      params: { project_id: String(project.id), fields: 'grid' },
    })
    expect((await members.json()).map((p) => p.id)).toContain(second)

    pictures.clear()
    for (const [path, id] of expected) pictures.set(path, id)
    await page.goto(`/ref-folder/${folderId}`)
    await expectGrid(page, directlyIn(relocatedTo))
  })

  test('removing it takes its pictures out of the library and leaves the files', async ({
    apiContext,
  }) => {
    expectScratch(relocatedTo)
    const res = await apiContext.delete(`/api/v1/reference-folders/${folderId}`)
    expect(res.ok(), await res.text()).toBe(true)
    folderId = null

    for (const id of pictures.values()) {
      const gone = await apiContext.get(`/api/v1/pictures/${id}/metadata`)
      expect(gone.status(), `picture ${id} is still in the library`).toBe(404)
    }
    // The files on disk are the owner's: not one of them is touched.
    for (const path of pictures.keys()) expect(existsSync(path), path).toBe(true)
    const folders = await (await apiContext.get('/api/v1/reference-folders')).json()
    expect(folders.folders.map((f) => f.folder)).not.toContain(relocatedTo)
  })
})
