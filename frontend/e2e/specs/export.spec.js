import { mkdtempSync, readdirSync, readFileSync, realpathSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, parse, sep } from 'node:path'
import { test, expect } from '../fixtures/test.js'

// Release plan §15 — Export. Select pictures (or a set view), open the toolbar
// Export panel, download the ZIP, and verify its actual contents — the entry
// count and image file names — by parsing the ZIP central directory from the
// downloaded bytes. Captions are set to "No Captions" so every entry is an
// image file and the count is exact. Export to Folder writes into a mkdtemp
// folder this spec makes and removes, and is checked on disk: the file count,
// a caption beside every picture, and the size the Resolution option asked
// for. Read-only for the fixture (exports copy data out; nothing is mutated).

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

/** Open the selection pill's Export panel and return it. */
async function openExportPanel(page) {
  // Select by class, not by name: the name is a live label that counts the
  // selection ("Export 3 pictures"). `.tb-export-btn` is the stable handle
  // and is what SelectionBar.test.js uses.
  await page.locator('button.tb-export-btn').click()
  const panel = page.locator('.tb-export-panel')
  await expect(panel).toBeVisible()
  return panel
}

/** One of the panel's labelled selects ("Captions", "Resolution", …). */
function exportOption(panel, label) {
  return panel.locator('.tbm-field', { hasText: label }).locator('select')
}

/** Open the selection pill's Export panel and export with captions disabled. */
async function downloadExportZip(page, testInfo) {
  const panel = await openExportPanel(page)
  await exportOption(panel, 'Captions').selectOption('none')

  const downloadPromise = page.waitForEvent('download', { timeout: 60_000 })
  await panel.locator('.app-btn--primary', { hasText: 'Export' }).click()
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

    // Export lives on the selection (#2160): a whole view is Ctrl+A, then
    // Export. The pill's count waits out the grid's stream.
    await page.keyboard.press('Control+a')
    await expect(page.locator('.selection-count')).toHaveText(
      `${expected} selected`,
    )

    // A selection that is the whole view exports the view's own query, not
    // the cells the grid happened to hold: a collapsed stack's members and a
    // tail still streaming are in the view and not in the selection.
    const exportUrls = []
    page.on('request', (req) => {
      if (/\/api\/v1\/pictures\/export\?/.test(req.url())) exportUrls.push(req.url())
    })
    const zip = zipEntryNames(await downloadExportZip(page, testInfo))
    const images = zip.filter((n) => IMAGE_EXT.test(n))
    expect(images).toHaveLength(expected)
    expect(exportUrls).toHaveLength(1)
    const sent = new URL(exportUrls[0]).searchParams
    expect(sent.getAll('id')).toEqual([])
    expect(sent.get('set_id')).toBeTruthy()
  })

  test.describe('to a folder', () => {
    let folder = null

    test.beforeEach(() => {
      folder = realpathSync(mkdtempSync(join(tmpdir(), 'pixlstash-e2e-export-')))
    })

    // The folder this test made, and nothing else: mkdtemp's own.
    test.afterEach(() => {
      if (folder) rmSync(folder, { recursive: true, force: true })
      folder = null
    })

    test('writes 3 half-size pictures and their tag captions into the folder picked (§15)', async ({
      page,
      grid,
      apiContext,
    }) => {
      await grid.goto()
      await grid.waitForThumbnailLoaded()

      // Three pictures that carry tags, so each tag caption has something in
      // it: the seeded reference-folder pictures at the top have none.
      const ids = []
      for (let i = 0; ids.length < 3 && i < 30; i++) {
        const card = grid.thumbnails.nth(i)
        const src = await card.locator('img.thumbnail-img').getAttribute('src')
        // A card still loading has no thumbnail URL yet; skip it.
        const id = Number(src?.match(/\/thumbnails\/(\d+)\./)?.[1])
        if (!Number.isFinite(id)) continue
        const tags = await apiContext.get(`/api/v1/pictures/${id}/tags`)
        expect(tags.ok(), await tags.text()).toBe(true)
        const payload = await tags.json()
        // `__tag…` rows are pending-retag sentinels, which captions leave out.
        const real = (payload?.tags ?? payload ?? []).filter(
          (t) => !String(t.tag ?? t).startsWith('__tag'),
        )
        if (!real.length) continue
        ids.push(id)
        await card.click({ modifiers: ['ControlOrMeta'] })
      }
      expect(ids, 'three tagged pictures near the top of the grid').toHaveLength(3)
      await expect(grid.selectionCountLabel).toHaveText('3 selected')

      const panel = await openExportPanel(page)
      await exportOption(panel, 'Captions').selectOption('tags')
      await exportOption(panel, 'Resolution').selectOption('half')
      await panel.locator('.tb-export-folder-btn').click()

      // The folder browser lists the server's disk and has no path field:
      // climb to the root, then walk down to the mkdtemp folder by name.
      const browser = page.locator('.app-dialog:has(.browse-path-bar)')
      const pathText = browser.locator('.browse-path-text')
      await expect(pathText).not.toBeEmpty()
      while ((await pathText.innerText()).trim() !== '/') {
        const before = (await pathText.innerText()).trim()
        await browser.locator('.browse-entry--up').click()
        await expect(pathText).not.toHaveText(before)
      }
      let walked = ''
      for (const part of folder.split(sep).filter(Boolean)) {
        walked += `${sep}${part}`
        const exact = new RegExp(`^${part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`)
        await browser.locator('.browse-entry-name').filter({ hasText: exact }).click()
        await expect(pathText).toHaveText(walked)
      }
      await browser.getByRole('button', { name: /^Select/ }).click()
      await expect(browser).toBeHidden()

      // The e2e backend's openers always refuse (serve_e2e_backend.py), so
      // the export reports the folder it wrote and that it could not open it.
      await expect(
        page.getByText(`Exported 3 pictures to ${folder}, but couldn't open it`),
      ).toBeVisible({ timeout: 30_000 })

      const files = readdirSync(folder)
      const images = files.filter((n) => IMAGE_EXT.test(n))
      const captions = files.filter((n) => n.endsWith('.txt'))
      expect(images).toHaveLength(3)
      expect(captions.map((n) => parse(n).name).sort()).toEqual(
        images.map((n) => parse(n).name).sort(),
      )
      expect(files).toHaveLength(6)
      // Each picture exported carries tags, so no caption may be blank.
      for (const name of captions) {
        expect(readFileSync(join(folder, name), 'utf8').trim(), name).not.toBe('')
      }

      // Half size, measured from the files' own pixels against the pictures'
      // recorded dimensions. Compared as sorted lists: the export names files
      // by position, not by picture id.
      const listed = await apiContext.get('/api/v1/pictures', {
        params: { fields: 'grid', limit: 1000 },
      })
      expect(listed.ok(), await listed.text()).toBe(true)
      const byId = new Map((await listed.json()).map((p) => [p.id, p]))
      const expected = ids
        .map((id) => byId.get(id))
        .map((p) => `${Math.floor(p.width / 2)}x${Math.floor(p.height / 2)}`)
        .sort()
      const written = []
      for (const name of images) {
        const b64 = readFileSync(join(folder, name)).toString('base64')
        written.push(
          await page.evaluate(async (data) => {
            const bytes = Uint8Array.from(atob(data), (c) => c.charCodeAt(0))
            const bitmap = await createImageBitmap(new Blob([bytes]))
            return `${bitmap.width}x${bitmap.height}`
          }, b64),
        )
      }
      expect(written.sort()).toEqual(expected)
    })
  })
})
