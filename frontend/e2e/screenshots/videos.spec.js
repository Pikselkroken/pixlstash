// Records the Getting started videos (website/introduction.html, 01-06) from
// the real desktop app, the same way capture.spec.js takes the stills: one
// Electron app off screen, the demo-data library (electronApp.js).
//
// A video is Chromium's screencast of the window (CDP Page.startScreencast),
// which sends a frame whenever the picture changes, timestamped. Those frames
// are laid end to end at their own durations and encoded to H.264 by ffmpeg,
// so ffmpeg must be on PATH. A screencast has no mouse pointer, so the page
// gets a drawn one that follows the real mouse events, and every move glides
// rather than jumps: the viewer has to see where the click lands.
//
// Each video writes website/screenshots/output/<name>.mp4. They change the
// library (an import, a new person, a tag), so they run after the stills.
import { execFileSync } from 'node:child_process'
import { mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, expect, freshUiState } from './electronApp.js'
import { WORK_INFO } from './electron-setup.js'
import { GridPage } from '../pages/GridPage.js'
import { SideBar } from '../pages/SideBar.js'

const here = dirname(fileURLToPath(import.meta.url))
const OUT_DIR = join(here, '..', '..', '..', 'website', 'screenshots', 'output')

// The site's videos are 1080 tall; the window is 1600x1000 CSS px at 2x.
const VIDEO_SIZE = '1728:1080'
// How long the last frame stays on screen before the loop restarts.
const FINAL_HOLD_S = 1.5

/** A pointer drawn in the page, following the real mouse events. */
function installPointer() {
  if (document.getElementById('shots-pointer')) return
  const pointer = document.createElement('div')
  pointer.id = 'shots-pointer'
  pointer.innerHTML =
    '<svg width="26" height="26" viewBox="0 0 24 24"><path d="M4 2l16 10.5-7 1.5 4 7.5-3 1.5-4-7.5L4 20z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>'
  Object.assign(pointer.style, {
    position: 'fixed',
    left: '0',
    top: '0',
    zIndex: '2147483647',
    pointerEvents: 'none',
    transformOrigin: '4px 2px',
    transform: 'translate(-4px, -2px)',
    filter: 'drop-shadow(0 1px 2px rgba(0,0,0,.5))',
    transition: 'scale 120ms ease-out',
  })
  const place = (e) => {
    pointer.style.left = `${e.clientX}px`
    pointer.style.top = `${e.clientY}px`
  }
  window.addEventListener('mousemove', place, true)
  window.addEventListener('mousedown', () => (pointer.style.scale = '0.82'), true)
  window.addEventListener('mouseup', () => (pointer.style.scale = '1'), true)
  document.documentElement.appendChild(pointer)
}

/** Where the mouse is, so the next glide starts from it. */
const mouseAt = new WeakMap()

/**
 * Move the mouse to (x, y) over `ms`, eased. Paced by the clock, not by a step
 * count: a move round-trip to Electron takes longer than a frame, so counted
 * steps made every glide two or three times as slow as asked.
 */
async function glide(page, x, y, ms = 500) {
  const from = mouseAt.get(page) ?? { x: 800, y: 500 }
  const started = Date.now()
  for (;;) {
    const t = Math.min(1, (Date.now() - started) / ms)
    const e = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2
    await page.mouse.move(from.x + (x - from.x) * e, from.y + (y - from.y) * e)
    if (t === 1) break
  }
  mouseAt.set(page, { x, y })
}

/**
 * Glide to the middle of `locator` and click it. `fromSide` comes in level
 * with it from just left of it: the way into a menu row without crossing the
 * rows below, whose submenus open on hover.
 */
async function glideClick(page, locator, { modifiers = [], dbl = false, ms, fromSide = false } = {}) {
  await locator.scrollIntoViewIfNeeded()
  const box = await locator.boundingBox()
  if (fromSide) await glide(page, box.x - 30, box.y + box.height / 2, ms)
  await glide(page, box.x + box.width / 2, box.y + box.height / 2, fromSide ? 250 : ms)
  await page.waitForTimeout(140)
  for (const key of modifiers) await page.keyboard.down(key)
  await page.mouse.click(mouseAt.get(page).x, mouseAt.get(page).y, { clickCount: dbl ? 2 : 1 })
  for (const key of modifiers) await page.keyboard.up(key)
  await page.waitForTimeout(200)
}

/**
 * Click `option` in the submenu `row` opened: sideways along the row first,
 * then down. A straight line crosses the menu's other rows, and hovering one
 * swaps the submenu away before the click lands.
 */
async function glideIntoSubmenu(page, row, option) {
  await expect(option).toBeVisible()
  const rowBox = await row.boundingBox()
  const box = await option.boundingBox()
  await glide(page, box.x + 24, rowBox.y + rowBox.height / 2, 450)
  await glideClick(page, option, { ms: 500 })
}

/**
 * Record what `body` does to the window into OUT_DIR/<name>.mp4.
 *
 * The screencast's frames carry their own timestamps; each is shown until the
 * next one, and the last for FINAL_HOLD_S.
 */
async function record(page, name, body) {
  const framesDir = join(OUT_DIR, `.${name}-frames`)
  rmSync(framesDir, { recursive: true, force: true })
  mkdirSync(framesDir, { recursive: true })
  const frames = []
  try {
    const cdp = await page.context().newCDPSession(page)
    cdp.on('Page.screencastFrame', ({ data, metadata, sessionId }) => {
      const file = join(framesDir, `${String(frames.length).padStart(5, '0')}.jpg`)
      writeFileSync(file, Buffer.from(data, 'base64'))
      frames.push({ file, t: metadata.timestamp })
      cdp.send('Page.screencastFrameAck', { sessionId }).catch(() => {})
    })
    await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 92, everyNthFrame: 1 })
    try {
      await body()
      await page.waitForTimeout(400)
    } finally {
      await cdp.send('Page.stopScreencast')
      await cdp.detach()
    }
    expect(frames.length, `the screencast of ${name} sent no frames`).toBeGreaterThan(1)

    const list = []
    frames.forEach((frame, i) => {
      const next = frames[i + 1]
      const seconds = next ? Math.max(1 / 60, next.t - frame.t) : FINAL_HOLD_S
      list.push(`file '${frame.file}'`, `duration ${seconds.toFixed(4)}`)
    })
    // The concat demuxer ignores the last entry's duration unless it is repeated.
    list.push(`file '${frames.at(-1).file}'`)
    const listFile = join(framesDir, 'frames.txt')
    writeFileSync(listFile, list.join('\n') + '\n')
    execFileSync('ffmpeg', [
      '-v', 'error', '-y',
      '-f', 'concat', '-safe', '0', '-i', listFile,
      '-vf', `fps=30,scale=${VIDEO_SIZE}:flags=lanczos,format=yuv420p`,
      '-c:v', 'libx264', '-preset', 'slow', '-crf', '20',
      '-movflags', '+faststart', '-an',
      join(OUT_DIR, `${name}.mp4`),
    ])
  } finally {
    // Also when the recording or the encode fails: hundreds of frames would
    // otherwise stay behind in output/.
    rmSync(framesDir, { recursive: true, force: true })
  }
}

/** The grid, settled, with the pointer installed and parked mid-window. */
async function start(page) {
  await freshUiState(page)
  const grid = new GridPage(page)
  await grid.goto()
  await grid.waitForThumbnailLoaded()
  await page.evaluate(installPointer)
  await page.mouse.move(800, 520)
  mouseAt.set(page, { x: 800, y: 520 })
  await page.waitForTimeout(900)
  return { grid, sidebar: new SideBar(page) }
}

/** The person row in the sidebar called `name`. */
function personRow(sidebar, name) {
  return sidebar.characterItems.filter({ hasText: name }).first()
}

mkdirSync(OUT_DIR, { recursive: true })

test.describe('Getting started videos', () => {
  test.setTimeout(180_000)

  test('01-PictureImport', async ({ page, apiContext }) => {
    void apiContext // the fixture applies the shared look
    const { grid } = await start(page)
    const dir = join(JSON.parse(readFileSync(WORK_INFO, 'utf8')).userData, 'import-video')
    const files = readdirSync(dir).map((name) => ({
      name,
      b64: readFileSync(join(dir, name)).toString('base64'),
    }))
    await record(page, '01-PictureImport', async () => {
      // A drag in from another window: the drop overlay first, then the drop.
      const target = grid.scrollWrapper
      const dataTransfer = await page.evaluateHandle((items) => {
        const dt = new DataTransfer()
        for (const item of items) {
          const bytes = Uint8Array.from(atob(item.b64), (c) => c.charCodeAt(0))
          dt.items.add(new File([bytes], item.name, { type: 'image/jpeg' }))
        }
        return dt
      }, files)
      await glide(page, 1500, 300, 300)
      await glide(page, 900, 520, 900)
      await target.dispatchEvent('dragenter', { dataTransfer })
      await target.dispatchEvent('dragover', { dataTransfer })
      await page.waitForTimeout(1200)
      const done = importCompleted(page)
      await target.dispatchEvent('drop', { dataTransfer })
      // The import runs, and the new pictures land at the top of the grid.
      expect((await done).imported_count).toBe(files.length)
      await grid.waitForThumbnailLoaded()
      await page.waitForTimeout(1200)
      // What the import set going: tagging, faces and scores, in the Tasks list.
      await glideClick(page, page.getByRole('button', { name: /task list/ }))
      await page.waitForTimeout(2500)
    })
  })

  test('02-CharacterAssignment', async ({ page, apiContext }) => {
    const ids = await unassignedPictures(apiContext, PERSON_PICTURES)
    const { grid, sidebar } = await start(page)
    await record(page, '02-CharacterAssignment', async () => {
      // A new person, named in place.
      await glideClick(page, page.locator('.sidebar-section-add').first())
      const name = page.locator('input:focus').first()
      await expect(name).toBeVisible()
      await name.pressSequentially(PERSON, { delay: 80 })
      await page.keyboard.press('Enter')
      await expect(personRow(sidebar, PERSON)).toBeVisible()
      await page.waitForTimeout(600)

      // Find her, pick three pictures, and say who she is.
      await glideClick(page, sidebar.page.locator('.sidebar-list-item', { hasText: 'All media' }).first())
      await glideClick(page, grid.searchButton)
      await grid.searchInput.pressSequentially(SEARCH)
      await grid.searchInput.press('Enter')
      await grid.waitForThumbnailLoaded()
      await page.waitForTimeout(500)
      const drawn = await page
        .locator('.thumbnail-img')
        .evaluateAll((imgs) => imgs.map((img) => Number(img.src.match(/thumbnails\/(\d+)\./)?.[1])))
      const picks = drawn.filter((id) => ids.includes(id)).slice(0, 3)
      expect(picks, `the search for "${SEARCH}" drew too few of her pictures`).toHaveLength(3)
      for (const [n, id] of picks.entries()) {
        const card = page.locator(`.thumbnail-card:has(.thumbnail-img[src*="/thumbnails/${id}."])`)
        await glideClick(page, card, { modifiers: n ? ['Control'] : [] })
      }
      await glideClick(page, grid.selectionMenuButton)
      const setPerson = grid.selectionMenuPanel.getByRole('button', { name: 'Set character' })
      await glideClick(page, setPerson, { fromSide: true })
      await glideIntoSubmenu(page, setPerson, page.locator('.ate-menu.open').getByRole('option', { name: PERSON }))
      await page.waitForTimeout(700)
      await page.keyboard.press('Escape')
      await page.keyboard.press('Escape')
      // Likeness sorting offers a person once one of her faces is embedded.
      await expect
        .poll(async () => {
          const body = await (await apiContext.get('/api/v1/characters')).json()
          return (body.characters ?? body).find((c) => c.name === PERSON)?.has_reference_faces
        }, { timeout: 60_000 })
        .toBe(true)

      // Then everything, by likeness to her.
      await glideClick(page, sidebar.page.locator('.sidebar-list-item', { hasText: 'All media' }).first())
      await glideClick(page, grid.sortMenuButton)
      await glideClick(page, grid.sortOption('Similarity to'))
      const simButton = page.locator('.gb-sort-panel .gb-sim-btn', { hasText: PERSON }).first()
      await expect(simButton).toBeVisible({ timeout: 20_000 })
      await glideClick(page, simButton)
      await page.keyboard.press('Escape')
      await grid.waitForThumbnailLoaded()
      await page.waitForTimeout(2200)
    })
  })

  test('03-Tagging', async ({ page, apiContext }) => {
    void apiContext
    const { grid } = await start(page)
    await record(page, '03-Tagging', async () => {
      for (const i of [0, 1, 2, 3]) {
        await glideClick(page, grid.thumbnails.nth(i), { modifiers: i ? ['Control'] : [] })
      }
      await page.waitForTimeout(400)
      await glideClick(page, page.locator('.selection-ctx-bar').getByRole('button', { name: /^Tag/ }).first())
      const field = page.locator('.rail-tags__input')
      await expect(field).toBeFocused()
      await page.waitForTimeout(500)
      await field.pressSequentially('portfolio', { delay: 60 })
      await page.waitForTimeout(500)
      await field.press('Enter')
      await page.waitForTimeout(2500)
    })
  })

  test('04-SmartScore', async ({ page, apiContext }) => {
    void apiContext // the fixture waits for the smart scores
    const { grid } = await start(page)
    await record(page, '04-SmartScore', async () => {
      await glideClick(page, grid.sortMenuButton)
      await glideClick(page, grid.sortOption('Smart Score'))
      await page.waitForTimeout(600)
      // Worst first: the menu closes on a pick, so open it again to flip it.
      await glideClick(page, grid.sortMenuButton)
      await glideClick(page, grid.sortDirectionButton)
      await expect(grid.sortDirectionButton).toContainText('Ascending')
      await page.waitForTimeout(400)
      await page.keyboard.press('Escape')
      await grid.waitForThumbnailLoaded()
      await page.waitForTimeout(1500)
      // The worst first: open it and look.
      await glideClick(page, grid.thumbnails.first(), { dbl: true })
      await page.waitForTimeout(2600)
    })
  })

  test('05-SemanticSearch', async ({ page, apiContext }) => {
    void apiContext
    const { grid } = await start(page)
    await record(page, '05-SemanticSearch', async () => {
      await glideClick(page, grid.searchButton)
      await expect(grid.searchOverlay).toBeVisible()
      await grid.searchInput.pressSequentially('man sorting photos', { delay: 30 })
      await page.waitForTimeout(400)
      await grid.searchInput.press('Enter')
      await grid.waitForThumbnailLoaded()
      await page.waitForTimeout(3000)
    })
  })

  test('06-Export', async ({ page, apiContext }) => {
    void apiContext
    const { sidebar } = await start(page)
    await record(page, '06-Export', async () => {
      await glideClick(page, personRow(sidebar, 'Jimmy Carr'))
      await page.waitForTimeout(900)
      await page.keyboard.press('Control+a')
      await page.waitForTimeout(500)
      await glideClick(page, page.locator('.tb-export-btn').first())
      await expect(page.locator('.tb-export-folder-btn')).toBeVisible()
      await page.waitForTimeout(1200)
      await glideClick(page, page.locator('.tbm-section').getByRole('button', { name: 'Export', exact: true }))
      // Done when the ZIP is in the work dir's downloads (electronApp.js).
      const downloads = join(JSON.parse(readFileSync(WORK_INFO, 'utf8')).userData, 'downloads')
      await expect
        .poll(() => readdirSync(downloads).filter((f) => f.endsWith('.zip')).length, { timeout: 60_000 })
        .toBeGreaterThan(0)
      await page.waitForTimeout(1500)
    })
  })
})

// 02's person: an AI-generated woman with dark curly hair, smiling at a mirror
// in five demo-data pictures and nobody's yet, by picture id (demo-data's
// vault is copied whole, so its ids hold). Each picture has her face alone, so
// assigning the picture assigns the face; a picture with two faces (her and a
// reflection) would wait for the owner to say which, and give likeness sorting
// nothing to go on. The video picks the first three of them that SEARCH draws.
const PERSON = 'Mira'
const PERSON_PICTURES = [183, 192, 193, 196, 197]
const SEARCH = 'woman with long dark hair looking in a mirror'

/** `ids`, each checked to show one face, which nobody owns yet. */
async function unassignedPictures(api, ids) {
  for (const id of ids) {
    const { faces } = await (await api.get(`/api/v1/pictures/${id}/faces`)).json()
    expect(faces.map((f) => f.character_id), `picture ${id}: one face, no person`).toEqual([null])
  }
  return ids
}

/** The terminal status of the next import the page runs. */
function importCompleted(page) {
  return page
    .waitForResponse(
      async (r) => {
        if (!/\/pictures\/import\/staging\/[^/]+\/status$/.test(r.url()) || !r.ok()) return false
        const body = await r.json().catch(() => null)
        return body?.stage === 'completed'
      },
      { timeout: 60_000 },
    )
    .then((r) => r.json())
}
