// The e2e backend seeds one model folder for the shelf specs (#2209).
//
// serve_e2e_backend.py lays out a small tree under its throwaway work dir and
// the server registers and scans it at start-up, because background workers
// (and so the scan a new folder normally gets) are off here. This spec is the
// fixture's own check: the folder is registered, it lives under the temp dir
// rather than in anybody's real model folders, its file count is the three
// stubs (so the preview .png and the empty subfolder added nothing), and the
// shelf lists each of them. The shelf is not counted whole: on a developer's
// machine it also lists that machine's own model folders.

import { realpathSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { sep } from 'node:path'
import { test, expect } from '../fixtures/test.js'
import { ModelShelfPage, seededFolder } from '../pages/ModelShelfPage.js'

const SEEDED = [
  'Clementine_Portrait.safetensors',
  'DetailTweaker_v2.safetensors',
  'RealVisXL_V4.safetensors',
]

test('the seeded model folder is registered under the throwaway work dir', async ({
  apiContext,
}) => {
  const res = await apiContext.get('/api/v1/model-folders')
  expect(res.ok(), await res.text()).toBe(true)
  const { folders } = await res.json()
  const seeded = folders.find((f) => f.path.endsWith(`${sep}model-shelf`))
  expect(seeded, JSON.stringify(folders.map((f) => f.path))).toBeTruthy()
  expect(seeded.path.startsWith(`${realpathSync(tmpdir())}${sep}pixlstash-e2e-`)).toBe(true)
  expect(seeded.kind).toBe('user')
  expect(seeded.file_count).toBe(SEEDED.length)
})

test('the shelf lists the seeded models and not the preview image', async ({ page }) => {
  // The shelf opens on the set grid, which draws no rows; ask for the flat list.
  await page.addInitScript(() => {
    localStorage.setItem('pixlstash:modelShelfView', JSON.stringify({ v: 3, groupBy: 'none' }))
  })
  await page.goto('/models')
  const files = page.locator('.shelf-row-file')
  for (const name of SEEDED) {
    await expect(files.filter({ hasText: name })).toHaveCount(1)
  }
  await expect(files.filter({ hasText: '.png' })).toHaveCount(0)
})

test('a folder row keeps its count beside the hover menu', async ({ page, apiContext }) => {
  const folder = await seededFolder(apiContext)
  const shelf = new ModelShelfPage(page)
  await shelf.goto()
  const row = shelf.treeRow(folder.id)
  await row.hover()
  const count = row.locator('.mft-count')
  const more = row.getByTestId('model-folder-more')
  await expect(count).toBeVisible()
  await expect(count).toHaveText(/\d/)
  // The ⋯ sits after the count, never on it.
  const c = await count.boundingBox()
  const m = await more.boundingBox()
  expect(c.x + c.width).toBeLessThanOrEqual(m.x)
})
