// Removing a subfolder from the Folders tree (#2207).
//
// The dialog shows the dry run: every file going, the ones the shelf does not
// list marked "not listed". Its confirm sends the dry run's fingerprint, so a
// folder that changed after it was read is refused with a 409 and read again
// rather than removed. Every removal here is the permanent one (Shift and the
// typed name): the Trash would be the real one of whoever runs the suite.

import { existsSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { test, expect } from '../fixtures/test.js'
import {
  ModelShelfPage,
  deleteModels,
  makeDir,
  moveInto,
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

test('Del on a folder lists its files, refuses a changed folder, and never deletes the selected model', async ({
  page,
  apiContext,
}) => {
  const tag = uniqueTag()
  const name = `Remove_${tag}`
  const dir = await makeDir(apiContext, folder, name)
  dirs.push(dir)
  const inside = await moveInto(
    apiContext,
    folder,
    await stageStub(apiContext, folder, `E2E_Inside_${tag}`),
    dir,
  )
  staged.push(inside)
  writeFileSync(join(folder.path, dir, 'notes.txt'), 'not a model\n')
  // The model left selected on the shelf while the folder is removed.
  const kept = await stageStub(apiContext, folder, `E2E_Kept_${tag}`)
  staged.push(kept)

  const deletes = []
  page.on('request', (req) => {
    if (req.method() === 'POST' && req.url().includes('/model-files/delete')) {
      deletes.push(req.postData())
    }
  })

  const shelf = new ModelShelfPage(page)
  await shelf.goto()
  await shelf.openFolder(folder.id)
  const keptRow = shelf.row(kept.filename)
  await keptRow.click()
  await expect(keptRow).toHaveAttribute('aria-selected', 'true')

  // Del with the focus on the folder's tree row is Remove folder, not the
  // shelf's delete of the selected model.
  await shelf.treeRow(folder.id, dir).focus()
  await page.keyboard.press('Delete')
  const dialog = page.getByRole('alertdialog').filter({ hasText: 'Remove folder' })
  await expect(dialog).toBeVisible()
  const modelDelete = page.getByRole('dialog').filter({ hasText: 'Move this model' })
  await expect(keptRow).toHaveAttribute('aria-selected', 'true')

  // Waited for first, so the check on the model's line runs on a drawn list.
  const files = dialog.getByTestId('remove-folder-files').locator('.srf-file')
  await expect(files).toHaveCount(2)
  await expect(files.filter({ hasText: 'notes.txt' })).toContainText('not listed')
  await expect(files.filter({ hasText: inside.filename })).not.toContainText('not listed')

  // The folder changes after the dry run. Shift is held on the keyboard rather
  // than passed to the click: it relabels the button and opens the name field,
  // and the click must land after that has settled.
  writeFileSync(join(folder.path, dir, 'late.txt'), 'arrived after the dry run\n')
  const confirm = dialog.getByTestId('remove-folder-confirm')
  await page.keyboard.down('Shift')
  await expect(confirm).toHaveText('Permanently delete')
  await dialog.getByTestId('remove-folder-name').fill(name)
  const removal = () =>
    page.waitForResponse(
      (res) => res.url().includes('/dirs/remove') && !res.request().postData().includes('"dry_run":true'),
    )
  // The confirm comes back 409, re-reads, and shows the new file.
  const refused = removal()
  await confirm.click()
  expect((await refused).request().postDataJSON()).toMatchObject({ path: dir, permanent: true })
  expect((await refused).status()).toBe(409)
  await expect(dialog).toContainText('The folder changed since it was read')
  await expect(files.filter({ hasText: 'late.txt' })).toContainText('not listed')
  expect(existsSync(inside.diskPath)).toBe(true)

  // Confirmed against what it shows now, it goes.
  const accepted = removal()
  await confirm.click()
  expect((await accepted).status()).toBe(200)
  await page.keyboard.up('Shift')
  await expect(dialog).toBeHidden()
  expect(existsSync(join(folder.path, dir))).toBe(false)

  // The selected model was never the target. Asked last, when the shelf's own
  // delete prompt has long had time to open had Del reached it.
  await expect(modelDelete).toHaveCount(0)
  expect(deletes).toEqual([])
  expect(existsSync(kept.diskPath)).toBe(true)
  await expect(keptRow).toHaveCount(1)
})
