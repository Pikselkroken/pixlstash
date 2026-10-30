// Deleting models from the shelf: Delete moves to the Trash, Shift+Delete
// deletes for good. The prompt counts MODELS, so a stack of two is two, and
// says what it knows about the support files the delete leaves behind.
//
// The Trash path is checked at the request and answered here: letting it
// through would put a stub in the real Trash of whoever runs the suite.

import { existsSync } from 'node:fs'
import { test, expect } from '../fixtures/test.js'
import {
  ModelShelfPage,
  deleteModels,
  seededFolder,
  stageStub,
  uniqueTag,
} from '../pages/ModelShelfPage.js'

let folder
let staged = []

test.beforeAll(async ({ apiContext }) => {
  folder = await seededFolder(apiContext)
})

test.afterEach(async ({ apiContext }) => {
  await deleteModels(apiContext, staged.map((s) => s.modelId))
  staged = []
})

function prompt(page, title) {
  return page.locator('[role="dialog"], [role="alertdialog"]').filter({ hasText: title })
}

test('Delete asks to move the model to the Trash and sends exactly that', async ({
  page,
  apiContext,
}) => {
  const stub = await stageStub(apiContext, folder, `E2E_Trash_${uniqueTag()}`)
  staged.push(stub)
  const sent = []
  // Any query string too: a request this missed would reach the real Trash.
  await page.route(/\/api\/v1\/model-files\/delete(\?.*)?$/, async (route) => {
    sent.push(route.request().postDataJSON())
    await route.fulfill({
      json: { deleted: [stub.modelId], files_removed: 1, permanent: false, trash_name: 'Trash', refused: [] },
    })
  })

  const shelf = new ModelShelfPage(page)
  await shelf.goto()
  await shelf.openFolder(folder.id)
  await shelf.row(stub.filename).click()
  await page.keyboard.press('Delete')

  const dialog = prompt(page, /Move this model to the (Trash|Recycle Bin)\?/)
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: /^Move to (Trash|Recycle Bin)$/ }).click()
  await expect.poll(() => sent).toEqual([{ ids: [stub.modelId], permanent: false }])
})

test('Shift+Delete on a stack counts its members and deletes every file for good', async ({
  page,
  apiContext,
}) => {
  const tag = uniqueTag()
  const a = await stageStub(apiContext, folder, `E2E_Stack_${tag}_step100`)
  const b = await stageStub(apiContext, folder, `E2E_Stack_${tag}_step200`)
  staged.push(a, b)
  const stacked = await apiContext.post('/api/v1/model-stacks', {
    data: { model_ids: [a.modelId, b.modelId] },
  })
  expect(stacked.ok(), await stacked.text()).toBe(true)

  const shelf = new ModelShelfPage(page)
  await shelf.goto()
  await shelf.openFolder(folder.id)
  // Collapsed, the stack is one row standing for both files.
  const row = page.locator('.shelf-row').filter({ hasText: `E2E_Stack_${tag}` })
  await expect(row).toHaveCount(1)
  await row.click()
  await page.keyboard.press('Shift+Delete')

  const dialog = prompt(page, 'Permanently delete 2 models?')
  await expect(dialog).toBeVisible()
  // The support-file read ran and was said: neither stub has a recipe.
  await expect(dialog).toContainText('No workflow PixlStash has read uses these models')
  await dialog.getByRole('button', { name: 'Delete permanently' }).click()

  await expect.poll(() => existsSync(a.diskPath) || existsSync(b.diskPath)).toBe(false)
  await expect(row).toHaveCount(0)
})
