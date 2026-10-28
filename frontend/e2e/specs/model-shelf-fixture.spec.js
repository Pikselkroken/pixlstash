// The e2e backend seeds one model folder for the shelf specs (#2209).
//
// serve_e2e_backend.py lays out a small tree under its throwaway work dir and
// the server registers and scans it at start-up, because background workers
// (and so the scan a new folder normally gets) are off here. This spec is the
// fixture's own check: the folder is registered, it lives under the temp dir
// rather than in anybody's real model folders, and the shelf lists what the
// scan found - and only that.

import { realpathSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { sep } from 'node:path'
import { test, expect } from '../fixtures/test.js'

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
