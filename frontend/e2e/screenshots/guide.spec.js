// Guide illustrations (issue #2825): one 800x500 JPEG per concept, written to
// frontend/src/assets/guide/<concept-id>.jpg. Real screenshots of the desktop
// app over the demo-data library, through the same fixture and scene helpers as
// capture.spec.js. Concepts `new` and `studio` have no shot by design, and the
// Extend task and Studio are never shot (unstable).
//
// Run (from frontend/; needs demo-data at the repo root, see the report):
//   npm run screenshots:guide
// It needs no website checkout (capture.spec.js does). Each concept is one
// test: it drives the app to the state its catalogue brief describes, frames
// the region the brief names, and encodes the PNG with Pillow in the same
// interpreter the rig uses (PIXLSTASH_PYTHON, else the repo's .venv).
import { execFileSync } from 'node:child_process'
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, freshUiState, test } from './electronApp.js'
import { WORK_INFO } from './electron-setup.js'
import { GridPage } from '../pages/GridPage.js'
import { Lightbox } from '../pages/Lightbox.js'
import { SettingsDialog } from '../pages/SettingsDialog.js'
import { SideBar } from '../pages/SideBar.js'
import { scenes } from './scenes.js'
import { WELCOME_EDITION } from '../../src/guide/catalogue.js'

const here = dirname(fileURLToPath(import.meta.url))
const OUT_DIR = join(here, '..', '..', 'src', 'assets', 'guide')
const TMP_DIR = join(here, '..', '.auth', 'guide')

// The window the rig fixes (electronApp.js). Frames are CSS px inside it.
const WIDTH = 1600
const HEIGHT = 1000
const RATIO = 1.6 // 800x500
// The fake ComfyUI the config starts (playwright.guide.config.js).
const COMFYUI_URL = 'http://127.0.0.1:9800'

// Pillow resize + JPEG encode.
const ENCODE = `
import sys
from PIL import Image
src, dst = sys.argv[1], sys.argv[2]
im = Image.open(src).convert('RGB').resize((800, 500), Image.LANCZOS)
im.save(dst, 'JPEG', quality=72, optimize=True, progressive=True)
`

/** A 16:10 frame that holds `boxes` (viewport CSS px), padded, kept in the window. */
function frameFromBoxes(boxes, pad = 24) {
  const left = Math.min(...boxes.map((b) => b.x)) - pad
  const top = Math.min(...boxes.map((b) => b.y)) - pad
  const right = Math.max(...boxes.map((b) => b.x + b.width)) + pad
  const bottom = Math.max(...boxes.map((b) => b.y + b.height)) + pad
  let w = right - left
  let h = bottom - top
  if (w / h < RATIO) w = h * RATIO
  else h = w / RATIO
  if (w > WIDTH) {
    w = WIDTH
    h = WIDTH / RATIO
  }
  if (h > HEIGHT) {
    h = HEIGHT
    w = HEIGHT * RATIO
  }
  const cx = (left + right) / 2
  const cy = (top + bottom) / 2
  const x = Math.min(Math.max(cx - w / 2, 0), WIDTH - w)
  const y = Math.min(Math.max(cy - h / 2, 0), HEIGHT - h)
  return { x, y, width: w, height: h }
}

/** A 16:10 frame around the boxes of `locators`. */
async function frameAround(locators, pad = 24) {
  const boxes = []
  for (const locator of locators) {
    const box = await locator.first().boundingBox()
    if (box) boxes.push(box)
  }
  expect(boxes.length, 'nothing to frame').toBeGreaterThan(0)
  return frameFromBoxes(boxes, pad)
}

/** Shoot `page` (the whole window, or `clip`) and encode it to <concept>.jpg. */
async function shoot(page, python, concept, clip = { x: 0, y: 0, width: WIDTH, height: HEIGHT }) {
  mkdirSync(OUT_DIR, { recursive: true })
  mkdirSync(TMP_DIR, { recursive: true })
  const png = join(TMP_DIR, `${concept}.png`)
  const jpg = join(OUT_DIR, `${concept}.jpg`)
  writeFileSync(png, await page.screenshot({ type: 'png', clip }))
  execFileSync(python, ['-I', '-c', ENCODE, png, jpg], { stdio: 'inherit' })
  rmSync(png, { force: true })
  expect(readFileSync(jpg).length).toBeGreaterThan(0)
}

/**
 * The config a fresh library lacks, so no first-run prompt (privacy, the Guide
 * welcome) covers a shot. The same values the fixture sets on the demo library.
 */
async function primeBody(api) {
  const { version } = await (await api.get('/version')).json()
  return {
      last_seen_version: version,
      check_for_updates: false,
      telemetry_send_install_id: false,
      telemetry_send_feature_usage: false,
      telemetry_send_error_reports: false,
      telemetry_send_hardware_profile: false,
      telemetry_consent_prompted: true,
      theme_mode: 'dark',
      columns: 6,
      sidebar_docked: false,
      // The fake ComfyUI of playwright.guide.config.js: it turns the Generate
      // actions on, which need a configured ComfyUI.
      comfyui_url: COMFYUI_URL,
      ...(WELCOME_EDITION ? { guide_welcome_seen: WELCOME_EDITION } : {}),
  }
}

async function primeLibrary(api) {
  const res = await api.patch('/api/v1/users/me/config', { data: await primeBody(api) })
  expect(res.ok(), `could not prime the library config: ${res.status()}`).toBe(true)
}

/** The same, sent by the window itself (its session), for a library the token is not on. */
async function primeLibraryInPage(page, api) {
  const body = await primeBody(api)
  const status = await page.evaluate(
    (data) =>
      fetch('/api/v1/users/me/config', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(data),
      }).then((res) => res.status),
    body,
  )
  expect(status, 'the window could not prime the library config').toBe(200)
}

async function activeLibrary(api) {
  const listed = await (await api.get('/api/v1/libraries')).json()
  return listed.libraries.find((library) => library.is_active)
}

async function switchLibrary(api, uuid) {
  const res = await api.post('/api/v1/libraries/active', { data: { uuid } })
  expect(res.ok(), `could not switch library: ${res.status()}`).toBe(true)
}

/** Scroll the grid until picture `id` is drawn; its thumbnail image. */
async function thumbFor({ grid, page }, id) {
  const thumb = page.locator(`.thumbnail-img[src*="/thumbnails/${id}."]`).first()
  for (let i = 0; i < 200 && !(await thumb.count()); i++) {
    await grid.scrollWrapper.evaluate((el) => el.scrollBy(0, el.clientHeight * 0.8))
    await page.waitForTimeout(120)
  }
  await thumb.scrollIntoViewIfNeeded()
  return thumb
}

/** Scroll the grid until picture `id` is drawn, then open it in the lightbox. */
async function openPicture(ctx, id) {
  const { lightbox, page } = ctx
  const thumb = await thumbFor(ctx, id)
  await thumb.dblclick()
  if (!(await lightbox.root.isVisible().catch(() => false))) {
    await thumb.dblclick({ force: true })
  }
  await expect(lightbox.root).toBeVisible()
  await page.waitForTimeout(300)
}

/** The first `count` still-picture ids (of the first 400) whose ComfyUI recipe the library holds. */
async function picturesWithRecipe(api, count) {
  const pictures = await (await api.get('/api/v1/pictures', { params: { fields: 'grid', limit: 400 } })).json()
  const found = []
  for (const picture of pictures) {
    if (found.length === count) break
    // The lightbox's Recipe tab depends on what the recipe read returns, so the
    // candidates are checked in the lightbox itself (see the recipes shot).
    const res = await api.get(`/api/v1/comfyui/pictures/${picture.id}/recipe`)
    if (res.ok() && (await res.json()).reason !== 'no_prompt_chunk') found.push(picture.id)
  }
  expect(found.length, `still pictures with a recipe among the first 400`).toBe(count)
  return found
}

async function readyGrid(grid) {
  await grid.goto()
  await grid.waitForThumbnailLoaded()
  await grid.page.waitForTimeout(900) // let lazy thumbnails paint
}

/** Ctrl-click the first `count` thumbnails. */
async function selectFirst(grid, count) {
  const n = Math.min(count, await grid.thumbnails.count())
  for (let i = 0; i < n; i++) {
    await grid.thumbnails.nth(i).click({ modifiers: ['ControlOrMeta'] })
  }
  await grid.page.waitForTimeout(300)
  return n
}

/**
 * Replace, in the text of `scope`, every match of `pattern` with `replacement`:
 * a share link's token or a path must not reach a published shot.
 */
async function maskText(page, scope, pattern, replacement) {
  await scope.evaluate(
    (root, [source, flags, text]) => {
      const re = new RegExp(source, flags)
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
      const nodes = []
      while (walker.nextNode()) nodes.push(walker.currentNode)
      for (const node of nodes) {
        if (re.test(node.textContent)) node.textContent = node.textContent.replace(new RegExp(source, flags), text)
      }
    },
    [pattern.source, pattern.flags, replacement],
  )
}

/** Open a scene from scenes.js and return what it framed. */
async function reuse(id, ctx) {
  const scene = scenes.find((s) => s.id === id)
  expect(scene, `scene ${id} exists in scenes.js`).toBeTruthy()
  return scene.setup(ctx)
}

// One entry per concept with a shot. Each returns null (the whole window), a
// frame {x, y, width, height}, or a locator / scene clip to frame.
const CONCEPTS = {
  // Get pictures in: the drop overlay over an empty library.
  'import-pictures': async (ctx) => {
    const { api, page } = ctx
    const root = join(TMP_DIR, 'empty-library')
    rmSync(root, { recursive: true, force: true })
    mkdirSync(root, { recursive: true })
    const added = await api.post('/api/v1/libraries', { data: { path: root, name: 'New library' } })
    expect(added.ok(), `could not add the empty library: ${added.status()}`).toBe(true)
    await switchLibrary(api, (await added.json()).uuid)
    await page.waitForTimeout(1500) // the switch reloads the window; let it settle
    await page.goto('/')
    await primeLibraryInPage(page, api)
    await page.reload()
    await expect(page.locator('.library-empty')).toBeVisible({ timeout: 30_000 })
    // A file mid-drag over the grid: the overlay lights on dragenter.
    await page.evaluate(() => {
      const dt = new DataTransfer()
      dt.items.add(new File([new Uint8Array([0xff, 0xd8, 0xff])], 'holiday.jpg', { type: 'image/jpeg' }))
      const target = document.querySelector('.grid-scroll-wrapper') ?? document.body
      target.dispatchEvent(new DragEvent('dragenter', { dataTransfer: dt, bubbles: true, cancelable: true }))
      target.dispatchEvent(new DragEvent('dragover', { dataTransfer: dt, bubbles: true, cancelable: true }))
    })
    await expect(page.locator('.drag-overlay')).toBeVisible()
    // The empty-library card sits over the overlay's message: hide the card so
    // the message reads. The overlay itself is the shot.
    await page.addStyleTag({ content: '.library-empty { visibility: hidden !important; }' })
    await page.waitForTimeout(300)
    return null
  },

  // Get pictures in: the title-bar picker with three libraries and their space.
  libraries: async (ctx) => {
    const { api, grid, page } = ctx
    for (const name of ['Client work', 'Archive 2019']) {
      const listed = await (await api.get('/api/v1/libraries')).json()
      if (listed.libraries.some((library) => library.name === name)) continue
      const root = join(TMP_DIR, name.replace(/\W+/g, '-').toLowerCase())
      rmSync(root, { recursive: true, force: true })
      mkdirSync(root, { recursive: true })
      const res = await api.post('/api/v1/libraries', { data: { path: root, name } })
      expect(res.ok(), `could not add ${name}: ${res.status()}`).toBe(true)
    }
    await readyGrid(grid)
    await page.getByTestId('titlebar-library').click()
    const menu = page.getByTestId('titlebar-library-menu')
    await expect(menu.locator('.ctx-item').nth(2)).toBeVisible()
    await page.waitForTimeout(400)
    return frameAround([page.getByTestId('titlebar-library'), menu], 40)
  },

  // Find: the lightbox over the grid, with the filmstrip and the stars.
  'browse-inspect': async (ctx) => {
    await reuse('lightbox', ctx)
    return null
  },

  // Find: the search field with a blue swatch picked.
  search: async (ctx) => {
    const { grid, page } = ctx
    await readyGrid(grid)
    await grid.searchButton.click()
    const blue = page.locator('.stf-colour button[aria-label*="lue" i], .stf-colour button[title*="lue" i]').first()
    expect(await blue.count(), 'the colour row names a blue swatch').toBeGreaterThan(0)
    await blue.click()
    await page.waitForTimeout(900)
    await grid.waitForThumbnailLoaded()
    await page.waitForTimeout(600)
    return null
  },

  // Organise: the People list with four people and the + lit.
  people: async (ctx) => {
    const { grid, page, sidebar } = ctx
    await readyGrid(grid)
    await sidebar.characterItems.first().scrollIntoViewIfNeeded()
    const add = page.locator('.sidebar [aria-label*="erson" i][aria-label^="Add" i], .sidebar [aria-label^="Add" i]').first()
    await add.hover()
    await page.waitForTimeout(300)
    const bar = await page.locator('.sidebar').first().boundingBox()
    const last = await sidebar.characterItems.nth(3).boundingBox()
    return frameFromBoxes([{ x: bar.x, y: bar.y, width: bar.width, height: last.y + last.height - bar.y }], 8)
  },

  // Organise: the side panel's Tags section and the Description under it.
  'tags-descriptions': async (ctx) => {
    const { grid, lightbox, page } = ctx
    await readyGrid(grid)
    await grid.thumbnails.first().dblclick()
    await expect(lightbox.mainImage).toBeVisible()
    await page.waitForTimeout(600)
    return frameAround([page.locator('.lightbox .rail-tags').first(), lightbox.descriptionField], 16)
  },

  // Review: the Inbox, with a picture open to its Keep / Skip / Scrap keys.
  'review-clean-up': async ({ page, grid }) => {
    await page.goto('/inbox')
    await page.getByTestId('inbox-period').click()
    await page.getByTestId('inbox-period-all').click()
    await grid.waitForThumbnailLoaded()
    await page.waitForTimeout(1200)
    return null
  },

  // Generate: the model shelf with checkpoints and LoRAs, sizes and base models.
  models: async (ctx) => {
    await reuse('model-shelf', ctx)
    return null
  },

  // Generate: the lightbox Recipe tab, with the sampler and size and Run.
  recipes: async (ctx) => {
    const { grid, lightbox, page, api } = ctx
    await readyGrid(grid)
    // The first of a few recipe pictures whose lightbox shows a Recipe tab.
    const tab = page.locator('.lightbox .inspector-tab', { hasText: 'Recipe' })
    for (const id of await picturesWithRecipe(api, 4)) {
      await openPicture({ grid, lightbox, page, api }, id)
      // The Recipe tab lands once the recipe read answers, a moment after the lightbox opens.
      if (await tab.waitFor({ state: 'visible', timeout: 10_000 }).then(() => true, () => false)) {
        await page.waitForFunction(
          () => {
            const img = document.querySelector('.lightbox-media-inner img')
            return !img || (img.complete && img.naturalWidth > 0)
          },
          null,
          { timeout: 8000 },
        ).catch(() => console.warn(`[guide] picture ${id}'s image did not finish loading`))
        break
      }
      await lightbox.close()
    }
    await tab.click()
    await page.waitForTimeout(800)
    return null
  },

  // Generate: the Workflows grid with a card selected and its inspector.
  workflows: async (ctx) => {
    await reuse('workflows', ctx)
    return null
  },

  // Edit: the lightbox Edit tab with the Mark where tools and a selection.
  'edit-picture': async (ctx) => {
    const { grid, lightbox, page } = ctx
    await readyGrid(grid)
    await grid.thumbnails.first().dblclick()
    await expect(lightbox.mainImage).toBeVisible()
    await lightbox.openEditTab()
    // A rectangle selection on the picture, so the Refine section shows.
    await page.locator('[role="toolbar"][aria-label="Mark tools"] button[aria-label*="ectang" i]').first().click()
    const box = await lightbox.mainImage.boundingBox()
    await page.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.3)
    await page.mouse.down()
    await page.mouse.move(box.x + box.width * 0.62, box.y + box.height * 0.6, { steps: 12 })
    await page.mouse.up()
    await page.waitForTimeout(600)
    return null
  },

  // Share & connect: the Export panel over 24 selected, Bounding boxes on COCO.
  export: async (ctx) => {
    const { grid, page } = ctx
    await readyGrid(grid)
    await selectFirst(grid, 24)
    await page.locator('.tb-export-btn').first().click()
    const select = page.locator('[data-guide="export.bounding-boxes"] + .tbm-select-wrap select')
    const coco = await select.locator('option').filter({ hasText: /COCO/ }).first().getAttribute('value')
    expect(coco, 'the Bounding boxes list offers COCO').not.toBeNull()
    await select.selectOption(coco)
    await page.waitForTimeout(400)
    return null
  },

  // Share & connect: the Share dialog for a set, watermark ticked.
  'share-links': async (ctx) => {
    const { grid, sidebar, page } = ctx
    await readyGrid(grid)
    await sidebar.openSetContextMenu(sidebar.setItems.first())
    await sidebar.ctxMenu.getByRole('button', { name: /^Share "/ }).first().click()
    const card = page.locator('.app-dialog').filter({ hasText: /^Share/ }).first()
    await expect(card).toBeVisible()
    await card.getByRole('checkbox', { name: 'Embed watermark' }).check()
    await card.getByRole('button', { name: 'Create link' }).click()
    await expect(card.getByText(/token=/)).toBeVisible()
    await maskText(page, card, /http:\/\/127\.0\.0\.1:\d+\/\?token=\S+/, 'https://example.com/share/example-link')
    await page.waitForTimeout(400)
    return frameAround([card], 12)
  },

  // Share & connect: the API reference page.
  api: async ({ page }) => {
    await page.goto('/scalar')
    await page.waitForTimeout(1500)
    return null
  },

  // Share & connect: the Connect an AI agent dialog (the agent chat itself is
  // outside the app and is not shot).
  'connect-agent': async (ctx) => {
    const { settings, page } = ctx
    await page.locator('.titlebar-settings').click()
    await expect(settings.card).toBeVisible()
    await settings.openTab('Account & tokens')
    await page.getByRole('button', { name: 'Connect AI agent' }).click()
    const card = page.locator('.app-dialog').filter({ hasText: 'Connect an AI agent' })
    await expect(card).toBeVisible()
    await page.waitForTimeout(400)
    return frameAround([card], 12)
  },

  // Keep it safe: the title-bar History panel listing the recent operations.
  'undo-recovery': async (ctx) => {
    const { grid, lightbox, page } = ctx
    await readyGrid(grid)
    // Operations for the History panel to list: four ratings.
    for (let i = 0; i < 4; i++) {
      await grid.thumbnails.nth(i).dblclick()
      await expect(lightbox.mainImage).toBeVisible()
      await lightbox.setRating(((i + 2) % 5) + 1)
      await lightbox.close()
      await grid.waitForThumbnailLoaded()
    }
    await page.locator('.uc-btn--chevron').first().click()
    await page.waitForTimeout(600)
    return null
  },
}

/**
 * Turn what a concept returns into a clip. null: the whole window. A frame
 * {x,y,width,height} is used as it is. A locator is framed; a scene's {clip}
 * is framed at 16:10 around its own box.
 */
async function clipFor(result) {
  if (!result) return undefined
  if (typeof result.boundingBox === 'function') return frameFromBoxes([await result.boundingBox()], 16)
  if (result.clip) return frameFromBoxes([result.clip], 0)
  return result
}

test.describe('guide illustrations', () => {
  const python = JSON.parse(readFileSync(WORK_INFO, 'utf8')).python

  for (const [concept, run] of Object.entries(CONCEPTS)) {
    test(`guide: ${concept}`, async ({ page, apiContext }) => {
      // The library this shot starts on. A shot that switches library must not
      // leave the next one (or the next worker) on an empty library.
      const home = await activeLibrary(apiContext)
      await primeLibrary(apiContext)
      await freshUiState(page)
      // A shot may leave the window on another route (the API page): start on the grid.
      await page.goto('/')
      const ctx = {
        page,
        api: apiContext,
        grid: new GridPage(page),
        lightbox: new Lightbox(page),
        settings: new SettingsDialog(page),
        sidebar: new SideBar(page),
      }
      try {
        const result = await run(ctx)
        await shoot(page, python, concept, await clipFor(result))
      } finally {
        const now = await activeLibrary(apiContext)
        if (now.uuid !== home.uuid) {
          await switchLibrary(apiContext, home.uuid)
          await page.goto('/')
          await expect(page.locator('.thumbnail-img').first()).toBeVisible({ timeout: 60_000 })
        }
        // `import-pictures` registers a throwaway library: detach it (never the
        // active one, which the API refuses) so later shots list only theirs.
        const listed = await (await apiContext.get('/api/v1/libraries')).json()
        for (const library of listed.libraries) {
          if (library.name !== 'New library' || library.uuid === home.uuid) continue
          const removed = await apiContext.delete(`/api/v1/libraries/${library.uuid}`)
          expect(removed.ok(), `could not detach the throwaway library: ${removed.status()}`).toBe(true)
        }
      }
    })
  }
})
