import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, expect } from '../fixtures/test.js'

const __dirname = dirname(fileURLToPath(import.meta.url))

// Release plan §2 — Picture Import, the drag affordance only: no file is
// imported here. A synthetic DataTransfer carrying a real JPEG File is
// dispatched onto the grid's scroll wrapper (the element that owns the
// dragenter/dragleave handlers in ImageGrid.vue), asserting the "Drop files
// here to import" overlay appears while dragging files and clears when the
// drag leaves.
//
// An import completing is covered by media-fixture.spec.js, through the
// Import dialog.

const SOURCE_JPG = resolve(
  __dirname,
  '../../../test-data/images/041514da-7779-4544-8e9b-e1dbe19540a4.jpg',
)

test.describe('picture import', () => {
  test('dragging files over the grid raises the import overlay; leaving clears it (§2)', async ({
    page,
    grid,
  }) => {
    await grid.goto()
    await grid.waitForThumbnailLoaded()

    const payload = readFileSync(SOURCE_JPG).toString('base64')
    const dataTransfer = await page.evaluateHandle(
      ([b64, name]) => {
        const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
        const dt = new DataTransfer()
        dt.items.add(new File([bytes], name, { type: 'image/jpeg' }))
        return dt
      },
      [payload, 'e2e-import.jpg'],
    )

    // Dragging a file over the grid raises the full-grid drop overlay.
    await grid.scrollWrapper.dispatchEvent('dragenter', { dataTransfer })
    await expect(page.locator('.drag-overlay')).toBeVisible()
    await expect(page.locator('.drag-overlay-message')).toHaveText(
      'Drop files here to import',
    )

    // Dragging back out dismisses it — no stuck overlay over the grid.
    await grid.scrollWrapper.dispatchEvent('dragleave', { dataTransfer })
    await expect(page.locator('.drag-overlay')).toBeHidden()
  })
})
