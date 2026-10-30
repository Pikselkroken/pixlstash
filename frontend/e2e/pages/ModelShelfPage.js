import { expect } from '@playwright/test'
import { randomBytes } from 'node:crypto'
import { mkdirSync, writeFileSync } from 'node:fs'
import { dirname, join, sep } from 'node:path'

/**
 * The Models shelf (ModelShelf.vue) and its Folders tree (ModelFolderTree.vue),
 * plus the API helpers its specs stage files with.
 *
 * Every file a spec touches is its own: `stageStub` writes a header-only
 * `.safetensors` under the e2e work dir and puts it on the shelf with
 * `POST /model-files`, into the folder serve_e2e_backend.py seeded (#2209).
 * The shelf also lists the machine's own model folders, so nothing here counts
 * rows or acts on a file it did not stage.
 */
export class ModelShelfPage {
  constructor(page) {
    this.page = page
    this.tree = page.getByTestId('model-folder-tree').first()
  }

  /** The shelf, drawn as rows rather than the set grid. */
  async goto() {
    await this.page.addInitScript(() => {
      localStorage.setItem('pixlstash:modelShelfView', JSON.stringify({ v: 3, groupBy: 'none' }))
    })
    await this.page.goto('/models')
  }

  /** A row of the sidebar's Folders tree, by registered folder and subpath. */
  treeRow(folderId, path = '') {
    return this.tree.locator(`.mft-row[data-folder-id="${folderId}"][data-path="${path}"]`)
  }

  /** Open a folder in the shelf and show its subfolders in the tree. */
  async openFolder(folderId, path = '') {
    const row = this.treeRow(folderId, path)
    await row.click()
    if ((await row.getAttribute('aria-expanded')) === 'false') {
      await row.locator('.mft-twist').click()
    }
    await expect(row).toHaveAttribute('aria-selected', 'true')
  }

  /** The shelf row whose file is `filename`. */
  row(filename) {
    return this.page.locator('.shelf-row').filter({
      has: this.page.locator('.shelf-row-file', { hasText: filename }),
    })
  }

  /** The Move files dialog. */
  get moveDialog() {
    return this.page.getByRole('dialog').filter({ hasText: 'Move files' })
  }
}

/**
 * The model folder serve_e2e_backend.py seeds, and the work dir it sits in.
 *
 * @returns {Promise<{id: number, path: string, workDir: string}>}
 */
export async function seededFolder(apiContext) {
  const res = await apiContext.get('/api/v1/model-folders')
  expect(res.ok(), await res.text()).toBe(true)
  const { folders } = await res.json()
  const folder = folders.find((f) => f.path.endsWith(`${sep}model-shelf`))
  expect(folder, 'the e2e backend seeds a model-shelf folder').toBeTruthy()
  return { id: folder.id, path: folder.path, workDir: dirname(folder.path) }
}

/**
 * Put one throwaway LoRA on the shelf, in the seeded folder's root.
 *
 * The metadata carries random bytes, so no two stubs share a hash and the
 * shelf never takes one for a copy of another.
 *
 * @param {string} name - file name, without the suffix.
 * @returns {Promise<{modelId: number, filename: string, diskPath: string}>}
 */
export async function stageStub(apiContext, folder, name) {
  const filename = `${name}.safetensors`
  const header = {
    'blocks.0.lora_A.weight': { dtype: 'F16', shape: [8, 16], data_offsets: [0, 0] },
    'blocks.0.lora_B.weight': { dtype: 'F16', shape: [16, 8], data_offsets: [0, 0] },
    __metadata__: { format: 'pt', ss_output_name: name, e2e_nonce: randomBytes(8).toString('hex') },
  }
  const blob = Buffer.from(JSON.stringify(header), 'utf8')
  const size = Buffer.alloc(8)
  size.writeBigUInt64LE(BigInt(blob.length))
  const staging = join(folder.workDir, 'e2e-staging')
  mkdirSync(staging, { recursive: true })
  const source = join(staging, filename)
  writeFileSync(source, Buffer.concat([size, blob]))
  const res = await apiContext.post('/api/v1/model-files', {
    data: { path: source, destination_folder_id: folder.id },
  })
  expect(res.ok(), await res.text()).toBe(true)
  const added = await res.json()
  return { modelId: added.model_id, filename, diskPath: join(folder.path, filename) }
}

/** Make a subfolder of the seeded folder's root; returns its subpath. */
export async function makeDir(apiContext, folder, name) {
  const res = await apiContext.post(`/api/v1/model-folders/${folder.id}/dirs`, {
    data: { path: '', name },
  })
  expect(res.ok(), await res.text()).toBe(true)
  return (await res.json()).path
}

/**
 * Move a staged stub from the seeded root into one of its subfolders, and
 * wait for the job: `POST /model-files` only ever lands in a folder's root.
 */
export async function moveInto(apiContext, folder, stub, subpath) {
  const res = await apiContext.post('/api/v1/model-moves', {
    data: {
      items: [{ folder_id: folder.id, relpath: stub.filename }],
      destination_folder_id: folder.id,
      destination_subpath: subpath,
    },
  })
  expect(res.ok(), await res.text()).toBe(true)
  let job
  await expect
    .poll(async () => {
      job = await (await apiContext.get('/api/v1/model-moves')).json()
      return job.status
    })
    .toBe('finished')
  expect(job.results?.map((r) => r.status)).toEqual(['moved'])
  return { ...stub, diskPath: join(folder.path, subpath, stub.filename) }
}

/** Permanently delete staged models. Already-gone ones are fine. */
export async function deleteModels(apiContext, ids) {
  if (!ids.length) return
  const res = await apiContext.post('/api/v1/model-files/delete', {
    data: { ids, permanent: true },
  })
  if (!res.ok()) console.warn(`[e2e] cleanup could not delete models ${ids}: ${await res.text()}`)
}

/** Permanently remove a staged subfolder, through the route's own dry run. */
export async function removeDir(apiContext, folder, path) {
  const url = `/api/v1/model-folders/${folder.id}/dirs/remove`
  const plan = await apiContext.post(url, { data: { path, dry_run: true } })
  if (!plan.ok()) {
    console.warn(`[e2e] cleanup could not read ${path}: ${await plan.text()}`)
    return
  }
  const { fingerprint } = await plan.json()
  const res = await apiContext.post(url, { data: { path, permanent: true, expect: fingerprint } })
  if (!res.ok()) console.warn(`[e2e] cleanup could not remove ${path}: ${await res.text()}`)
}

/** A short random tag, so a retried test never meets its own leftovers. */
export function uniqueTag() {
  return randomBytes(3).toString('hex')
}
