// Throwaway folders of pictures for the specs that register a reference or
// import folder (#2325).
//
// Every folder a spec hands the server comes from `scratchFolder`, and every
// spec calls `expectScratch` on a folder before it removes or relocates it. A
// remove deletes the folder's pictures from the library and a relocate moves
// its files on disk, so a folder that is not one this run made is a folder
// that must not be touched. `expectScratch` therefore accepts only folders
// `scratchFolder` made in this process, not anything that merely looks like
// one (another checkout's e2e work dir has the same prefix).

import { mkdirSync, mkdtempSync, realpathSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, sep } from 'node:path'
import { crc32, deflateSync } from 'node:zlib'
import { expect } from '@playwright/test'

const SCRATCH_PREFIX = 'pixlstash-e2e-'
// The folders scratchFolder made, in this worker process.
const made = new Set()

/** The temp root, resolved the way the server stores a folder (realpath). */
function scratchRoot() {
  return realpathSync(tmpdir())
}

/**
 * Make an empty folder under the temp dir and return its resolved path.
 * @param {string} name - a word for what the folder is for.
 */
export function scratchFolder(name) {
  const folder = realpathSync(mkdtempSync(join(scratchRoot(), `${SCRATCH_PREFIX}${name}-`)))
  made.add(folder)
  return folder
}

/** Fail unless *path* is a folder scratchFolder made in this run. */
export function expectScratch(path) {
  expect(made.has(String(path)), `${path} is not a folder this run made`).toBe(true)
}

/**
 * The reference folder the e2e launcher seeds under its work dir (#2320):
 * registered with a layout, watched, and holding one pending move.
 * @returns {Promise<{id: number, folder: string}>}
 */
export async function seededReferenceFolder(apiContext) {
  const res = await apiContext.get('/api/v1/reference-folders')
  expect(res.ok(), await res.text()).toBe(true)
  const { folders } = await res.json()
  const seeded = folders.find((f) => f.folder.endsWith(`${sep}reference-folder`))
  expect(seeded, JSON.stringify(folders.map((f) => f.folder))).toBeTruthy()
  // The launcher's, under its work dir in the temp root, never the owner's.
  expect(
    seeded.folder.startsWith(`${scratchRoot()}${sep}${SCRATCH_PREFIX}`),
    `${seeded.folder} is not under the e2e work dir`,
  ).toBe(true)
  return seeded
}

/** The ids of the pictures a reference folder holds, by file path. */
export async function picturesInFolder(apiContext, folderId) {
  const res = await apiContext.get('/api/v1/pictures', {
    params: { reference_folder_id: String(folderId), fields: 'grid' },
  })
  expect(res.ok(), await res.text()).toBe(true)
  return new Map((await res.json()).map((p) => [p.file_path, p.id]))
}

/**
 * Tell the server a reference folder changed on disk, the way its watcher
 * would. The watcher is not relied on: it needs an inotify instance, and a
 * machine running many processes may have none left to give it.
 */
export async function markFolderChanged(apiContext, folderId) {
  const res = await apiContext.post(`/api/v1/test-hooks/reference-folders/${folderId}/rescan`)
  expect(res.ok(), await res.text()).toBe(true)
}

/**
 * Wait for the folder's scan to bring in the file at *path*, and return the
 * picture's id.
 */
export async function waitForPicture(apiContext, folderId, path) {
  let id
  await expect
    .poll(
      async () => {
        id = (await picturesInFolder(apiContext, folderId)).get(path)
        return id
      },
      { message: `${path} was never scanned in`, timeout: 30_000 },
    )
    .toBeTruthy()
  return id
}

/**
 * Take pictures a spec imported back out of the library, and out of the
 * Scrapheap, so later specs read the fixture as they would have: a picture
 * left in the Scrapheap breaks every spec that needs it empty
 * (scrapheap.spec.js). A vault copy goes, file and all.
 *
 * A reference-folder original is protected, and a purge without *include
 * protected* leaves it in the Scrapheap. Pass *originalsUnder*, a folder the
 * spec's own files are in, to delete those originals too: every protected file
 * must be under it, or nothing is purged.
 * @param {{originalsUnder?: string}} [options]
 */
export async function discardPictures(apiContext, ids, { originalsUnder } = {}) {
  if (!ids.length) return
  const scrapped = await apiContext.delete('/api/v1/pictures', { data: { picture_ids: ids } })
  expect(scrapped.ok(), await scrapped.text()).toBe(true)
  const preview = await apiContext.post('/api/v1/pictures/scrapheap/delete-preview', {
    data: { picture_ids: ids },
  })
  expect(preview.ok(), await preview.text()).toBe(true)
  const plan = await preview.json()
  if (originalsUnder) {
    // The whole list, or no proof that nothing outside the folder goes.
    expect(plan.protected.length, JSON.stringify(plan)).toBe(plan.protected_count)
    for (const item of plan.protected) {
      expect(
        item.file_path.startsWith(`${originalsUnder}${sep}`),
        `${item.file_path} is not under ${originalsUnder}`,
      ).toBe(true)
    }
  }
  const purged = await apiContext.delete('/api/v1/pictures/scrapheap', {
    data: {
      picture_ids: ids,
      include_protected: Boolean(originalsUnder),
      confirm_token: plan.confirm_token,
    },
  })
  expect(purged.ok(), await purged.text()).toBe(true)
  if (!originalsUnder) return
  // Checked, not assumed: the Scrapheap holds none of them now.
  const heap = await apiContext.get('/api/v1/pictures', {
    params: { only_deleted: 'true', fields: 'grid' },
  })
  expect(heap.ok(), await heap.text()).toBe(true)
  const left = (await heap.json()).map((p) => p.id).filter((id) => ids.includes(id))
  expect(left, 'pictures still in the Scrapheap after the purge').toEqual([])
}

function chunk(type, data) {
  const length = Buffer.alloc(4)
  length.writeUInt32BE(data.length)
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data])
  const crc = Buffer.alloc(4)
  crc.writeUInt32BE(crc32(body))
  return Buffer.concat([length, body, crc])
}

/**
 * Write a small PNG whose pixels depend on *seed*, so no two pictures a spec
 * writes are the same file.
 * @param {string} path
 * @param {number} seed
 */
export function writePicture(path, seed) {
  const size = 64
  const header = Buffer.alloc(13)
  header.writeUInt32BE(size, 0)
  header.writeUInt32BE(size, 4)
  header[8] = 8 // bit depth
  header[9] = 2 // RGB
  const rows = []
  for (let y = 0; y < size; y += 1) {
    const row = Buffer.alloc(1 + size * 3) // filter byte 0, then RGB
    for (let x = 0; x < size; x += 1) {
      row[1 + x * 3] = (x * 4 + seed * 37) & 0xff
      row[2 + x * 3] = (y * 4 + seed * 91) & 0xff
      row[3 + x * 3] = (x * y + seed * 13) & 0xff
    }
    rows.push(row)
  }
  // The pattern repeats every 256 seeds; the first pixel carries the seed
  // itself, so no two seeds below 2^24 make the same file.
  rows[0][1] = (seed >> 16) & 0xff
  rows[0][2] = (seed >> 8) & 0xff
  rows[0][3] = seed & 0xff
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(
    path,
    Buffer.concat([
      Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
      chunk('IHDR', header),
      chunk('IDAT', deflateSync(Buffer.concat(rows))),
      chunk('IEND', Buffer.alloc(0)),
    ]),
  )
}
