import { readFileSync } from 'node:fs'
import { test, expect } from '../fixtures/test.js'

// Release plan §15 — Export. Select pictures (or a set view), open the toolbar
// Export panel, download the ZIP, and verify its actual contents — the entry
// count and image file names — by parsing the ZIP central directory from the
// downloaded bytes. Captions are set to "No Captions" so every entry is an
// image file and the count is exact. Read-only for the fixture (exports copy
// data out; nothing is mutated).

const IMAGE_EXT = /\.(jpe?g|png|webp|gif|bmp|tiff?)$/i

/** File names from a ZIP buffer's central directory (PK\x01\x02 records). */
function zipEntryNames(buf) {
  const names = []
  let i = 0
  while ((i = buf.indexOf('PK\x01\x02', i, 'binary')) !== -1) {
    const nameLen = buf.readUInt16LE(i + 28)
    names.push(buf.subarray(i + 46, i + 46 + nameLen).toString('utf8'))
    i += 46 + nameLen
  }
  return names
}

/** Open the toolbar Export panel and run an export with captions disabled. */
async function downloadExportZip(page, testInfo) {
  // Select by class, not by title: the title is a live label that names the
  // current selection ("Export 3 pictures to zip"), so a title selector breaks
  // as soon as anything is selected. `.tb-export-btn` is the stable handle and
  // is what Toolbar.test.js already uses.
  await page.locator('button.tb-export-btn').click()
  const panel = page.locator('.tb-export-panel')
  await expect(panel).toBeVisible()
  await panel
    .locator('.tbm-field', { hasText: 'Captions' })
    .locator('select')
    .selectOption('none')

  const downloadPromise = page.waitForEvent('download', { timeout: 60_000 })
  await panel.locator('.tbm-action--primary', { hasText: 'Export' }).click()
  const download = await downloadPromise
  const zipPath = testInfo.outputPath(`export-${Date.now()}.zip`)
  await download.saveAs(zipPath)
  return readFileSync(zipPath)
}

test.describe('export', () => {
  test('exports 3 selected pictures to a ZIP containing exactly 3 image files (§15)', async ({
    page,
    grid,
  }, testInfo) => {
    await grid.goto()
    await grid.waitForThumbnailLoaded()

    // Ctrl-click three cards to build a multi-selection.
    for (let i = 0; i < 3; i++) {
      await grid.thumbnails.nth(i).click({ modifiers: ['Control'] })
    }

    const zip = zipEntryNames(await downloadExportZip(page, testInfo))
    const images = zip.filter((n) => IMAGE_EXT.test(n))
    expect(images).toHaveLength(3)
    expect(zip).toHaveLength(3)
    // Selection is per-page in-memory state; the next test's goto() resets it.
  })

  test('exports a picture set to a ZIP matching the sidebar count (§15)', async ({
    page,
    grid,
    sidebar,
  }, testInfo) => {
    await grid.goto()
    const row = await sidebar.firstNonEmpty(sidebar.setItems)
    const countText = (await sidebar.count(row).innerText()).trim()
    const expected = Number(countText.match(/\d+/)?.[0])
    expect(expected).toBeGreaterThan(0)

    await row.click()
    await expect.poll(() => page.url()).toContain('/set/')
    await grid.waitForThumbnailLoaded()

    const zip = zipEntryNames(await downloadExportZip(page, testInfo))
    const images = zip.filter((n) => IMAGE_EXT.test(n))
    expect(images).toHaveLength(expected)
  })
})
