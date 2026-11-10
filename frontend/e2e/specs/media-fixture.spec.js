// The e2e backend runs submitted tasks and the folder sweeps, and seeds a
// reference folder with one move pending review (#2320).
//
// serve_e2e_backend.py starts the task runner with a planner restricted to the
// folder finders (so the sweeps that rewrite the fixture stay off), lays out
// three images under its throwaway work dir, and the server registers that
// folder with a layout, scans it, moves Inbox/fern.png and scans again. This
// spec is the fixture's own check: every registered folder lives under the
// temp dir (Delete forever with include protected removes reference-folder
// originals from disk), the seeded folder holds its pictures, /moves lists the
// review, and an import through the Import dialog completes.

import { realpathSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { sep } from 'node:path'
import { crc32, deflateSync } from 'node:zlib'
import { test, expect } from '../fixtures/test.js'

const WORK_DIR_PREFIX = `${realpathSync(tmpdir())}${sep}pixlstash-e2e-`
const SEEDED = ['fern.png', 'moss.png', 'stone.png']

/** A small RGB PNG of one colour, so its pixels match nothing in the library. */
function flatPng(width, height, [r, g, b]) {
  const chunk = (type, data) => {
    const length = Buffer.alloc(4)
    length.writeUInt32BE(data.length)
    const body = Buffer.concat([Buffer.from(type, 'ascii'), data])
    const crc = Buffer.alloc(4)
    crc.writeUInt32BE(crc32(body))
    return Buffer.concat([length, body, crc])
  }
  const header = Buffer.alloc(13)
  header.writeUInt32BE(width, 0)
  header.writeUInt32BE(height, 4)
  header[8] = 8 // bit depth
  header[9] = 2 // RGB
  const row = Buffer.concat([Buffer.from([0]), Buffer.from(Array(width).fill([r, g, b]).flat())])
  const pixels = Buffer.concat(Array(height).fill(row))
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', header),
    chunk('IDAT', deflateSync(pixels)),
    chunk('IEND', Buffer.alloc(0)),
  ])
}

async function listPictureIds(apiContext) {
  const res = await apiContext.get('/api/v1/pictures', {
    params: { fields: 'id', limit: '10000' },
  })
  expect(res.ok(), await res.text()).toBe(true)
  return (await res.json()).map((picture) => picture.id)
}

test('every registered reference and import folder lives under the throwaway work dir', async ({
  apiContext,
}) => {
  const refs = await apiContext.get('/api/v1/reference-folders')
  expect(refs.ok(), await refs.text()).toBe(true)
  const imports = await apiContext.get('/api/v1/import-folders')
  expect(imports.ok(), await imports.text()).toBe(true)
  const paths = [
    ...(await refs.json()).folders.map((f) => f.folder),
    ...(await imports.json()).folders.map((f) => f.folder),
  ]
  expect(paths.length).toBeGreaterThan(0)
  for (const path of paths) {
    expect(path.startsWith(WORK_DIR_PREFIX), path).toBe(true)
  }
})

test('the seeded reference folder is registered and holds its pictures', async ({
  apiContext,
}) => {
  const res = await apiContext.get('/api/v1/reference-folders')
  const { folders } = await res.json()
  const seeded = folders.find((f) => f.folder.endsWith(`${sep}reference-folder`))
  expect(seeded, JSON.stringify(folders.map((f) => f.folder))).toBeTruthy()
  expect(seeded.status).toBe('active')
  expect(seeded.layout).toBeTruthy()

  const listed = await apiContext.get('/api/v1/pictures', {
    params: { reference_folder_id: String(seeded.id), fields: 'id,file_path' },
  })
  expect(listed.ok(), await listed.text()).toBe(true)
  const paths = (await listed.json()).map((p) => p.file_path)
  expect(paths.map((path) => path.split(sep).pop()).sort()).toEqual(SEEDED)
  // The seed's move was followed: fern's row points at its new folder.
  const fern = paths.find((path) => path.endsWith(`${sep}fern.png`))
  expect(fern.includes(`${sep}Inbox${sep}`), fern).toBe(false)
})

test('/moves lists the seeded move as an unambiguous review', async ({ apiContext }) => {
  const res = await apiContext.get('/api/v1/moves/pending')
  expect(res.ok(), await res.text()).toBe(true)
  const { unambiguous } = await res.json()
  const review = unambiguous.find((item) => item.old_path.endsWith(`${sep}Inbox${sep}fern.png`))
  expect(review, JSON.stringify(unambiguous)).toBeTruthy()
  expect(review.new_path.startsWith(WORK_DIR_PREFIX)).toBe(true)
  expect(review.removals).toEqual([])
  expect(review.additions).toHaveLength(1)
  expect(review.additions[0].facet).toBe('project')
})

test.describe('import through the Import dialog', () => {
  let importedId = null

  // The picture goes again, file and all: every later spec counts on the
  // fixture. It was copied into the work dir's library, so purging it removes
  // nothing outside the throwaway copy.
  test.afterAll(async ({ apiContext }) => {
    if (importedId === null) return
    const ids = [importedId]
    const scrapped = await apiContext.delete('/api/v1/pictures', { data: { picture_ids: ids } })
    expect(scrapped.ok(), await scrapped.text()).toBe(true)
    const preview = await apiContext.post('/api/v1/pictures/scrapheap/delete-preview', {
      data: { picture_ids: ids },
    })
    expect(preview.ok(), await preview.text()).toBe(true)
    const purged = await apiContext.delete('/api/v1/pictures/scrapheap', {
      data: {
        picture_ids: ids,
        include_protected: false,
        confirm_token: (await preview.json()).confirm_token,
      },
    })
    expect(purged.ok(), await purged.text()).toBe(true)
  })

  test('an import completes and the picture appears in the grid', async ({
    page,
    grid,
    apiContext,
  }) => {
    const before = new Set(await listPictureIds(apiContext))
    await grid.goto()

    await page.locator('.sidebar-import-btn').click()
    const committed = page.waitForResponse(
      (r) => r.request().method() === 'POST' && /\/import\/staging\/[^/]+\/commit$/.test(r.url()),
    )
    await page.locator('.local-import-input').setInputFiles({
      name: 'e2e-media-import.png',
      mimeType: 'image/png',
      buffer: flatPng(40, 30, [171, 64, 140]),
    })
    const commit = await committed
    expect(commit.ok(), await commit.text()).toBe(true)
    const { staging_id: stagingId } = await commit.json()

    await expect
      .poll(
        async () => {
          const res = await apiContext.get(`/api/v1/pictures/import/staging/${stagingId}/status`)
          const body = await res.json()
          return `${body.stage}:${body.imported_count}`
        },
        { timeout: 20_000 },
      )
      .toBe('completed:1')

    const added = (await listPictureIds(apiContext)).filter((id) => !before.has(id))
    expect(added).toHaveLength(1)
    importedId = added[0]
    await expect(grid.card(importedId)).toBeVisible({ timeout: 15_000 })
  })
})
