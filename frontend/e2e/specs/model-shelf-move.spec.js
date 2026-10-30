// Moving models into a subfolder by drag and by cut and paste (#2204).
//
// A drop never moves on release: it opens the Move dialog with the
// destination already chosen, and Enter confirms. So the drag test follows the
// gesture through to the disk, and the refusals assert that no dialog opens.
// Real HTML5 drag had not been exercised in this suite before, so the drag
// test also records that `dragstart` and `drop` fired: a spec whose drag never
// started would otherwise pass every refusal for the wrong reason.

import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { test, expect } from '../fixtures/test.js'
import {
  ModelShelfPage,
  deleteModels,
  makeDir,
  removeDir,
  seededFolder,
  stageStub,
  uniqueTag,
} from '../pages/ModelShelfPage.js'

let folder
let staged = []
let dirs = []

test.beforeAll(async ({ apiContext }) => {
  folder = await seededFolder(apiContext)
})

test.afterEach(async ({ apiContext }) => {
  await deleteModels(apiContext, staged.map((s) => s.modelId))
  for (const path of dirs) await removeDir(apiContext, folder, path)
  staged = []
  dirs = []
})

/** Record the drag events the window sees, in the capture phase. */
async function recordDrags(page) {
  await page.addInitScript(() => {
    window.__drags = []
    for (const type of ['dragstart', 'drop']) {
      window.addEventListener(type, () => window.__drags.push(type), true)
    }
  })
}

/**
 * Pick `source` up and hold it over `target`, button still down. Entering the
 * target fires only `dragenter`; the stepped move inside it is what fires the
 * `dragover` the refusal is drawn on.
 */
async function holdDragOver(page, source, target) {
  await source.hover()
  await page.mouse.down()
  await target.hover()
  const box = await target.boundingBox()
  await page.mouse.move(box.x + box.width / 2 + 4, box.y + box.height / 2, { steps: 4 })
}

test('a row dropped on a subfolder moves its file there on disk', async ({ page, apiContext }) => {
  const tag = uniqueTag()
  const stub = await stageStub(apiContext, folder, `E2E_Drag_${tag}`)
  staged.push(stub)
  const dest = await makeDir(apiContext, folder, `Drop_${tag}`)
  dirs.push(dest)

  const shelf = new ModelShelfPage(page)
  await recordDrags(page)
  await shelf.goto()
  await shelf.openFolder(folder.id)
  const row = shelf.row(stub.filename)
  await expect(row).toHaveCount(1)

  await row.dragTo(shelf.treeRow(folder.id, dest))
  expect(await page.evaluate(() => window.__drags)).toEqual(['dragstart', 'drop'])

  // Pre-chosen: the dialog's own tree has the subfolder picked.
  const dialog = shelf.moveDialog
  await expect(dialog).toBeVisible()
  await expect(
    dialog.locator(`[role="treeitem"][data-folder-id="${folder.id}"][data-path="${dest}"]`),
  ).toHaveAttribute('aria-selected', 'true')
  await page.keyboard.press('Enter')
  await expect(dialog).toBeHidden()

  const landed = join(folder.path, dest, stub.filename)
  await expect.poll(() => existsSync(landed)).toBe(true)
  expect(existsSync(stub.diskPath)).toBe(false)
  // The row goes from the root's view: the file is in the subfolder now.
  await expect(row).toHaveCount(0)
})

test('drops on a locked folder, the source folder or nothing are refused', async ({
  page,
  apiContext,
}) => {
  const stub = await stageStub(apiContext, folder, `E2E_Refuse_${uniqueTag()}`)
  staged.push(stub)
  const imports = []
  page.on('request', (req) => {
    if (req.method() === 'POST' && /\/(pictures|model-files)(\/|$|\?)/.test(req.url())) {
      imports.push(req.url())
    }
  })

  const shelf = new ModelShelfPage(page)
  await recordDrags(page)
  await shelf.goto()
  await shelf.openFolder(folder.id)
  const row = shelf.row(stub.filename)
  await expect(row).toHaveCount(1)

  // A refused folder never takes the drop, so the browser fires no `drop`
  // there. What proves the drag reached it is the refusal hatch drawn while
  // the drag is held over it; then the release must open nothing.
  //
  // The managed store the server keeps beside its hub is always registered,
  // and always locked. The second target is the folder the file is already in.
  const locked = shelf.tree.locator('.mft-row:has([data-testid="model-folder-lock"])').first()
  for (const target of [locked, shelf.treeRow(folder.id)]) {
    await holdDragOver(page, row, target)
    await expect(target).toHaveClass(/not-droppable/)
    await page.mouse.up()
    await expect(target).not.toHaveClass(/not-droppable/)
  }
  // A drop that misses every folder: the shelf's own toolbar.
  await holdDragOver(page, row, page.locator('.shelf-toolbar'))
  await page.mouse.up()

  expect(await page.evaluate(() => window.__drags)).toEqual([
    'dragstart',
    'dragstart',
    'dragstart',
  ])
  await expect(shelf.moveDialog).toHaveCount(0)
  expect(imports).toEqual([])
  expect(existsSync(stub.diskPath)).toBe(true)
})

test('Ctrl X then Ctrl V in a subfolder opens the move there; Esc clears the cut', async ({
  page,
  apiContext,
}) => {
  const tag = uniqueTag()
  const stub = await stageStub(apiContext, folder, `E2E_Cut_${tag}`)
  staged.push(stub)
  const dest = await makeDir(apiContext, folder, `Paste_${tag}`)
  dirs.push(dest)

  const shelf = new ModelShelfPage(page)
  await shelf.goto()
  await shelf.openFolder(folder.id)
  const row = shelf.row(stub.filename)
  await row.click()
  await expect(row).toHaveAttribute('aria-selected', 'true')

  // Esc puts a cut back.
  await page.keyboard.press('Control+x')
  await expect(row).toHaveClass(/shelf-row--cut/)
  await page.keyboard.press('Escape')
  await expect(row).not.toHaveClass(/shelf-row--cut/)

  // Cut again, open the subfolder, paste.
  await row.click()
  await page.keyboard.press('Control+x')
  await expect(row).toHaveClass(/shelf-row--cut/)
  await shelf.openFolder(folder.id, dest)
  await page.keyboard.press('Control+v')
  const dialog = shelf.moveDialog
  await expect(dialog).toBeVisible()
  await expect(
    dialog.locator(`[role="treeitem"][data-folder-id="${folder.id}"][data-path="${dest}"]`),
  ).toHaveAttribute('aria-selected', 'true')

  // Esc cancels the dialog and moves nothing.
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
  expect(existsSync(stub.diskPath)).toBe(true)
  expect(existsSync(join(folder.path, dest, stub.filename))).toBe(false)
})
