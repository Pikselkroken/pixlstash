// The app scene behind each marketing-site illustration. Each scene drives the
// live SPA into the state the ORIGINAL screenshot shows (verified against
// website/assets/*), then returns the locator to capture (or null for the whole
// viewport). One scene can satisfy several site assets. Non-reproducible
// illustrations are listed in `manual` with a reason.
//
// Runs against the demo-data library (varied photos, populated people/projects/
// sets) in a forced dark theme, in the real desktop app (title bar + window
// controls) — see capture.spec.js. ctx = { page, grid, lightbox, settings,
// sidebar, api }. setup() returns a locator to shoot, `{ clip }` for a region
// of the window, or null for the whole window; an optional teardown(ctx) undoes
// anything it persisted, since every scene shares one app and one library.
import { cpSync, mkdirSync, readdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect } from '@playwright/test'

import { ReviewSessions } from '../pages/ReviewSessions.js'

const here = dirname(fileURLToPath(import.meta.url))

// v1.11's "Add a library" reads a real folder on disk, so the scenes for it
// build one. It lives in the OS temp dir rather than under the repo or the
// owner's home: the wizard refuses a folder inside the active library, and a
// capture must never put somebody's own folder names on the marketing site.
// The shape is the release's own default layout — Project, then Person — so the
// mapping screen has two levels to propose and a root to leave alone.
const FOLDER_FIXTURE_ROOT = join(tmpdir(), 'pixlstash-shots-library')
//: How long the folder read gets. It decodes and samples every picture in the
//: tree and runs face detection over them, on the CPU-only capture backend and
//: possibly from a cold model load, so it does not fit the file's default
//: timeout. Scenes that wait on it declare `timeout` too — the expect timeout
//: alone cannot outlive the test.
const READ_TIMEOUT_MS = 600_000
const FOLDER_FIXTURE_TREE = {
  'Client · Nordvik': { Mira: 5, Jonas: 4 },
  'Studio Tests': { Mira: 4, Ines: 4 },
}

/**
 * Fill FOLDER_FIXTURE_ROOT with copies of demo-data pictures and return it.
 * Rebuilt per call so a rerun never reads a half-written tree from the last
 * one, and so the read's picture counts are the same in every capture.
 */
function buildFolderFixture() {
  const demoImages = join(here, '..', '..', '..', 'demo-data', 'images')
  const sources = readdirSync(demoImages).filter(
    (name) => /\.(jpe?g|png)$/i.test(name) && !name.includes('_thumb'),
  )
  expect(sources.length, 'demo-data/images must hold pictures to copy').toBeGreaterThan(0)

  rmSync(FOLDER_FIXTURE_ROOT, { recursive: true, force: true })
  let next = 0
  for (const [project, people] of Object.entries(FOLDER_FIXTURE_TREE)) {
    for (const [person, count] of Object.entries(people)) {
      const dir = join(FOLDER_FIXTURE_ROOT, project, person)
      mkdirSync(dir, { recursive: true })
      for (let n = 1; n <= count; n++) {
        const source = sources[next++ % sources.length]
        cpSync(join(demoImages, source), join(dir, `${person}-${n}.jpg`))
      }
    }
  }
  return FOLDER_FIXTURE_ROOT
}

/**
 * The wizard's AppDialog, addressed by whichever step is currently mounted.
 * Not `:has(.choose-step)`: the steps replace each other, so a locator naming
 * one of them stops matching the moment the wizard advances — which is how the
 * mapping and preview scenes first failed, waiting inside a dialog that no
 * longer matched their own selector.
 */
function wizardDialog(page) {
  return page
    .locator('.app-dialog')
    .filter({ has: page.locator('.choose-step, .map-tree, .preview-step') })
}

/**
 * Open Settings → Libraries → "+ Add a library…" against the fixture folder and
 * wait for the server's verdict card. Returns the wizard dialog to shoot.
 */
async function openAddLibrary({ grid, settings, page }) {
  await readyGrid(grid)
  await settings.open()
  await settings.openTab('Libraries & imports')
  await page.getByRole('button', { name: '+ Add a library…' }).click()

  const wizard = wizardDialog(page)
  await expect(wizard).toBeVisible()
  const field = wizard.getByLabel('Folder')
  await field.fill(buildFolderFixture())
  await field.blur() // what triggers the inspect; Enter does the same
  await expect(wizard.locator('.choose-step__verdict')).toBeVisible()
  return wizard
}

/**
 * Carry the wizard from the verdict through the read to the mapping tree.
 *
 * Three gestures, not one: "Bring them in" starts the read, the scan card then
 * reports what it found and waits, and "Set up my library" is what opens the
 * mapping tree. The read decodes and samples every picture in the tree, so the
 * wait between the first two is the long one.
 */
async function readFoldersIntoMapping(ctx) {
  await openAddLibrary(ctx)
  const wizard = wizardDialog(ctx.page)
  await wizard.getByRole('button', { name: 'Bring them in' }).click()

  const proceed = wizard.getByRole('button', { name: 'Set up my library' })
  await expect(proceed).toBeEnabled({ timeout: READ_TIMEOUT_MS })
  await proceed.click()
  await expect(ctx.page.locator('.map-tree')).toBeVisible()
  await ctx.page.waitForTimeout(600) // let the level bands settle
  return wizard
}

const SECOND_LIBRARY = 'Client work'
// A demo-data tag the tagger has suspects for, so a review has cards to show.
const REVIEW_TAG = 'woman'

/**
 * Register a second, empty library beside demo-data, once per run.
 *
 * A row's full ⋯ menu (open / rename / stop using) only exists on a library
 * that is NOT the active one, and demo-data registers exactly one. An EMPTY
 * folder is the one verdict that adds a library outright, with no mapping step
 * in the way. The hub lives in the throwaway work dir, so this registers
 * nothing outside the capture.
 */
async function addSecondLibrary(api) {
  const listed = await api.get('/api/v1/libraries')
  expect(listed.ok(), `could not list libraries: ${listed.status()}`).toBe(true)
  const { libraries } = await listed.json()
  if (libraries.some((library) => library.name === SECOND_LIBRARY)) return
  const root = join(tmpdir(), 'pixlstash-shots-second-library')
  rmSync(root, { recursive: true, force: true })
  mkdirSync(root, { recursive: true })
  const res = await api.post('/api/v1/libraries', {
    data: { path: root, name: SECOND_LIBRARY },
  })
  expect(res.ok(), `could not add the second library: ${res.status()}`).toBe(true)
}

/**
 * Replace what a capture must not publish with a stand-in, just before the
 * shot. The values on screen were minted on the throwaway capture backend, but
 * a token, a share link or a work-dir path in a public image reads as real, and
 * the stand-ins say they are not (`example-`, `/home/me/`, 192.0.2.x).
 */
async function mask(page, selector, values) {
  await page.locator(selector).first().waitFor()
  await page.locator(selector).evaluateAll((nodes, texts) => {
    nodes.forEach((node, i) => {
      node.textContent = texts[Math.min(i, texts.length - 1)]
    })
  }, values)
}

const LIBRARY_PATHS = ['/home/me/Pictures/PixlStash', '/home/me/Pictures/Client work']

/** Scroll the grid until picture `id` is drawn, then open it in the lightbox. */
async function openPicture({ grid, lightbox, page }, id) {
  const thumb = page.locator(`.thumbnail-img[src*="/thumbnails/${id}."]`).first()
  for (let i = 0; i < 200 && !(await thumb.count()); i++) {
    await grid.scrollWrapper.evaluate((el) => el.scrollBy(0, el.clientHeight * 0.8))
    await page.waitForTimeout(120)
  }
  await thumb.scrollIntoViewIfNeeded()
  await thumb.dblclick()
  await expect(lightbox.mainImage).toBeVisible()
}

// A demo-data picture with Florence-2 object detections already stored: a
// woman with a burger in a diner, six labelled boxes.
const DETECTIONS_PICTURE = 1040

async function readyGrid(grid) {
  await grid.goto()
  await grid.waitForThumbnailLoaded()
  await grid.page.waitForTimeout(900) // let lazy thumbnails paint
}

/** Open the lightbox from the first grid thumbnail, robustly. */
async function openLightbox({ grid, lightbox, page }) {
  const card = grid.thumbnails.first()
  await card.scrollIntoViewIfNeeded()
  await card.dblclick()
  if (await lightbox.root.isVisible().catch(() => false)) return
  await page.waitForTimeout(400)
  await grid.thumbnailImages.first().dblclick({ force: true }).catch(() => {})
  await expect(lightbox.root).toBeVisible()
}

/** Filter the grid to the first person who actually has pictures. */
async function filterToPerson(sidebar) {
  if (!(await sidebar.characterItems.count())) return false
  const row = await sidebar.firstNonEmpty(sidebar.characterItems)
  await row.click()
  await sidebar.page.waitForTimeout(800)
  return true
}

/** Ctrl-click the first N thumbnails to build a multi-selection. */
async function selectN(grid, n = 3) {
  const cards = grid.thumbnails
  const count = Math.min(n, await cards.count())
  for (let i = 0; i < count; i++) {
    await cards.nth(i).click({ modifiers: ['Control'] })
  }
  await grid.page.waitForTimeout(300)
  return count
}

export const scenes = [
  {
    id: 'main-grid',
    assets: ['ScreenshotMain.jpg', 'ScreenshotGrid.jpg', 'ScreenshotApp.jpg'],
    title: 'Main interface / image grid (desktop app)',
    async setup({ grid }) {
      await readyGrid(grid)
      return null
    },
  },
  {
    id: 'toolbar',
    assets: ['ScreenshotToolbar.jpg'],
    title: 'Toolbar',
    async setup({ grid, page }) {
      await readyGrid(grid)
      return page.locator('.selection-bar-overlay').first()
    },
  },
  {
    id: 'sidebar',
    assets: ['ScreenshotSidebar2.jpg'],
    title: 'Sidebar',
    async setup({ grid, page }) {
      await readyGrid(grid)
      return page.locator('.sidebar').first()
    },
  },
  {
    id: 'lightbox',
    assets: ['ScreenshotImageOverlay.jpg'],
    title: 'Picture inspection lightbox',
    async setup({ grid, lightbox }) {
      await readyGrid(grid)
      await openLightbox({ grid, lightbox, page: grid.page })
      await expect(lightbox.mainImage).toBeVisible()
      await grid.page.waitForTimeout(600)
      return lightbox.root
    },
  },
  {
    id: 'face-detection',
    assets: ['ScreenshotImageOverlay2.jpg'],
    title: 'Face detection in the lightbox',
    async setup({ grid, lightbox, sidebar }) {
      await readyGrid(grid)
      await filterToPerson(sidebar)
      await openLightbox({ grid, lightbox, page: grid.page })
      await expect(lightbox.mainImage).toBeVisible()
      // Pointing at a face's chip draws its box for the shot.
      if (await lightbox.faceChips.count()) {
        await lightbox.faceChips.first().hover().catch(() => {})
      }
      await grid.page.waitForTimeout(600)
      return lightbox.root
    },
  },
  {
    id: 'context-menu',
    assets: ['ScreenshotContext.jpg'],
    title: 'Right-click context menu (multi-selection)',
    async setup({ grid, page }) {
      await readyGrid(grid)
      // Multi-select, then right-click one of the selected cards so the menu is
      // selection-scoped (shows the score range header), and highlight "Set".
      await selectN(grid, 3)
      await grid.openContextMenu(grid.thumbnails.first())
      // "Set" is an AddToEntityControl trigger (.ate-btn). Highlight it.
      const setItem = grid.contextMenu.locator('.ate-btn', { hasText: 'Set' }).first()
      await setItem.hover({ timeout: 3000 }).catch(() => {})
      await page.waitForTimeout(300)
      return null
    },
  },
  {
    id: 'reverse-search',
    assets: ['ReverseImageSearch.jpg'],
    title: 'Reverse image & face search (context menu)',
    async setup({ grid, page }) {
      await readyGrid(grid)
      await grid.openContextMenu(grid.thumbnails.first())
      // Highlight the "Reverse image search" action, as in the original. It
      // sits in the Find & compare group, which opens on hover.
      await grid.contextMenu
        .locator('.ctx-item', { hasText: 'Find & compare' })
        .first()
        .hover()
      const item = grid.contextMenu
        .locator('.ctx-item', { hasText: 'Reverse image search' })
        .first()
      await item.hover()
      await page.waitForTimeout(300)
      return null
    },
  },
  {
    id: 'selection',
    assets: ['ScreenshotGridSelection.jpg'],
    title: 'Batch selection',
    async setup({ grid }) {
      await readyGrid(grid)
      await selectN(grid, 5)
      return null
    },
  },
  {
    id: 'selective-restore',
    assets: ['ScreenshotSelectiveRestore.jpg'],
    title: 'Selective restore (restore selected pictures from a snapshot)',
    async setup({ grid, page }) {
      await readyGrid(grid)
      await selectN(grid, 3)
      await grid.openContextMenu(grid.thumbnails.first())
      // Open the "Restore from snapshot" submenu so its restore-point list shows.
      const restore = grid.contextMenu
        .locator('.ctx-item', { hasText: 'Restore from snapshot' })
        .first()
      if (!(await restore.count())) {
        throw new Error('No "Restore from snapshot" item — are there snapshots?')
      }
      await restore.hover()
      await page.waitForTimeout(500)
      await expect(grid.contextMenu.locator('.ctx-submenu').first()).toBeVisible()
      return null
    },
  },
  {
    id: 'backend-settings',
    assets: ['ScreenshotBackend.jpg'],
    title: 'Desktop Backend settings (remote access + desktop)',
    async setup({ grid, settings, page }) {
      await readyGrid(grid)
      await settings.open()
      // The desktop-only "Server" rail item: remote access + desktop.
      await settings.openTab('Server')
      await expect(page.getByText('Remote access')).toBeVisible()
      await page.waitForTimeout(400)
      return settings.card
    },
  },
  // ── v1.11: adding a library, and reading the folders it already has ──────
  // Ordered so the three wizard shots read as one walk-through, and placed
  // after the settings scenes because they all start from the same dialog.
  {
    id: 'add-library',
    assets: ['ScreenshotAddLibrary.jpg'],
    title: 'Add a library — the folder\'s own verdict',
    async setup(ctx) {
      return await openAddLibrary(ctx)
    },
  },
  {
    id: 'map-tree',
    assets: ['ScreenshotMapTree.jpg'],
    title: 'Naming what each folder level is (the mapping tree)',
    timeout: READ_TIMEOUT_MS + 120_000,
    async setup(ctx) {
      return await readFoldersIntoMapping(ctx)
    },
  },
  {
    id: 'map-preview',
    assets: ['ScreenshotMapPreview.jpg'],
    title: 'This is what your folders become (review before the import)',
    timeout: READ_TIMEOUT_MS + 120_000,
    async setup(ctx) {
      const wizard = await readFoldersIntoMapping(ctx)
      // Stops one screen short of "Yes, build this library" on purpose: the
      // point of the shot is that nothing has been written yet.
      await wizard.getByRole('button', { name: 'Review and import' }).click()
      await expect(wizard.locator('.preview-step')).toBeVisible()
      await ctx.page.waitForTimeout(600)
      return wizard
    },
  },
  {
    id: 'libraries',
    assets: ['ScreenshotLibraries.jpg'],
    title: 'Settings → Libraries with two registered libraries',
    async setup({ grid, settings, api, page }) {
      await addSecondLibrary(api)
      await readyGrid(grid)
      await settings.open()
      await settings.openTab('Libraries & imports')
      await expect(page.locator('.library-row')).toHaveCount(2)
      await mask(page, '.library-row__path', LIBRARY_PATHS)
      await page.waitForTimeout(300)
      return settings.card
    },
  },
  {
    id: 'libraries-manage',
    assets: ['ScreenshotLibrariesManage.jpg'],
    title: 'Settings → Libraries with a row\'s ⋯ menu open',
    async setup({ grid, settings, api, page }) {
      await addSecondLibrary(api)
      await readyGrid(grid)
      await settings.open()
      await settings.openTab('Libraries & imports')
      const row = page.locator('.library-row:not(.library-row--active)').first()
      await row.locator('.library-row__more').click()
      // Named, not `getByRole('menu')`: the shared surface puts `role=menu` on
      // every open menu, and a bare role query is strict-mode ambiguous.
      await expect(
        page.getByRole('menuitem', { name: 'Open this library' }),
      ).toBeVisible()
      await mask(page, '.library-row__path', LIBRARY_PATHS)
      await page.waitForTimeout(300)
      return settings.card
    },
  },
  {
    id: 'export-folder',
    assets: ['ScreenshotExportFolder.jpg'],
    title: 'Export panel with Export to Folder…',
    async setup({ grid, page }) {
      await readyGrid(grid)
      // Export is a selection verb (#2160): select the view, then open it.
      await page.keyboard.press('Control+a')
      await page.locator('.tb-export-btn').first().click()
      // By class, not accessible name: the activator's own name subsumes the
      // whole panel's text, so a name-based locator matches two buttons.
      await expect(page.locator('.tb-export-folder-btn')).toBeVisible()
      await page.waitForTimeout(300)
      return null
    },
  },
  {
    id: 'privacy',
    assets: ['ScreenshotPrivacy.jpg'],
    title: 'Privacy settings (update checks + anonymous install ID)',
    async setup({ grid, settings }) {
      await readyGrid(grid)
      await settings.open()
      await settings.openTab('Privacy & sharing')
      await grid.page.waitForTimeout(400)
      return settings.card
    },
  },
  {
    id: 'snapshots',
    assets: ['ScreenshotSnapshots.jpg'],
    title: 'Snapshots & restore',
    async setup({ grid, settings }) {
      await readyGrid(grid)
      await settings.open()
      await settings.openSnapshotsTab()
      await grid.page.waitForTimeout(400)
      return settings.card
    },
  },
  {
    id: 'stats-pictures',
    assets: ['ScreenshotPictureStatistics.jpg'],
    title: 'Statistics sidebar (pictures)',
    async setup({ grid, page }) {
      await readyGrid(grid)
      await grid.statsToggle.click()
      await expect(grid.statsSidebar).toBeVisible()
      await grid.statsTabs.filter({ hasText: /pictures/i }).first().click()
      await page.waitForTimeout(700)
      return grid.statsSidebar
    },
  },
  {
    id: 'stats-tags',
    assets: ['ScreenshotTagStatistics.jpg'],
    title: 'Statistics sidebar (tags)',
    async setup({ grid, page }) {
      await readyGrid(grid)
      await grid.statsToggle.click()
      await expect(grid.statsSidebar).toBeVisible()
      await grid.statsTabs.filter({ hasText: /tags/i }).first().click()
      await page.waitForTimeout(700)
      return grid.statsSidebar
    },
  },
  {
    id: 'characters',
    assets: ['ScreenshotCharacters.jpg'],
    title: 'People / characters in the sidebar',
    async setup({ grid, sidebar, page }) {
      await readyGrid(grid)
      if (await sidebar.characterItems.count()) {
        await sidebar.characterItems.first().scrollIntoViewIfNeeded()
      }
      await page.waitForTimeout(400)
      // The sidebar from the top down to just under the last person: the Sets
      // header and the Import button below them are not what the shot shows.
      const bar = await page.locator('.sidebar').first().boundingBox()
      const last = await sidebar.characterItems.last().boundingBox()
      return { clip: { x: bar.x, y: bar.y, width: bar.width, height: last.y + last.height + 8 - bar.y } }
    },
  },
  {
    id: 'projects',
    assets: ['ScreenshotProject.jpg'],
    title: 'Project organisation',
    async setup({ grid, sidebar, page }) {
      await readyGrid(grid)
      await sidebar.openScopeMenu()
      await page.waitForTimeout(500) // the menu fades in
      return null
    },
  },
  {
    id: 'breadcrumb',
    assets: ['ScreenshotBreadcrumb.jpg'],
    title: 'Breadcrumb navigation',
    async setup({ grid, sidebar, page }) {
      await readyGrid(grid)
      // Navigate into a project, then a person inside it, so the breadcrumb is a
      // deep path (Projects › Project › Person), shown in the full app view.
      await sidebar.chooseFirstProject()
      await page.waitForTimeout(500)
      const personInProject = sidebar.characterItems.first()
      if (await personInProject.count()) {
        await personInProject.click()
        await page.waitForTimeout(600)
      }
      // Capture the whole app so the breadcrumb is shown in context, like the
      // original (not just the breadcrumb strip on its own).
      return null
    },
  },
  {
    id: 'tagging',
    assets: ['ScreenshotTagging.jpg'],
    title: 'Tag autocompletion',
    async setup({ grid, lightbox, page }) {
      await readyGrid(grid)
      await openLightbox({ grid, lightbox, page })
      await expect(lightbox.mainImage).toBeVisible()
      // The tag field sits in the inspector rail; typing in it opens the list.
      await expect(lightbox.tagInput).toBeVisible()
      await lightbox.tagInput.fill('su')
      await page.waitForTimeout(600)
      return lightbox.root
    },
  },
  // ── 2.0: the remaining features.html illustrations ─────────────────────
  {
    id: 'keyboard',
    assets: ['ScreenshotKeyboard.jpg'],
    title: 'Keyboard shortcuts (F1)',
    async setup({ grid, page }) {
      await readyGrid(grid)
      await page.keyboard.press('F1')
      const dialog = page.locator('.app-dialog:has(.shortcuts-table)')
      await expect(dialog).toBeVisible()
      await page.waitForTimeout(400)
      return dialog
    },
  },
  {
    id: 'justified-settings',
    assets: ['ScreenshotsJustifiedSettings.jpg'],
    title: 'Appearance settings: square or justified thumbnails',
    async setup({ grid, settings, page }) {
      await readyGrid(grid)
      await settings.open()
      await settings.openTab('Appearance')
      await expect(page.getByText('Thumbnail layout')).toBeVisible()
      await page.waitForTimeout(300)
      return settings.card
    },
  },
  {
    id: 'justified',
    assets: ['ScreenshotsJustified.jpg'],
    title: 'Justified grid (thumbnails at their own aspect ratio)',
    // Persisted per owner, so teardown puts it back: every later grid scene is
    // a square grid.
    async setup({ grid, api, page }) {
      const res = await api.patch('/api/v1/users/me/config', {
        data: { thumbnail_mode: 'justified' },
      })
      expect(res.ok(), `could not switch to justified: ${res.status()}`).toBe(true)
      await readyGrid(grid)
      await page.waitForTimeout(600)
      return null
    },
    async teardown({ api }) {
      await api.patch('/api/v1/users/me/config', { data: { thumbnail_mode: 'square' } })
    },
  },
  {
    id: 'share',
    assets: ['ScreenshotShare1.jpg'],
    title: 'Share dialog with a generated link',
    async setup({ grid, page }) {
      await readyGrid(grid)
      await grid.openContextMenu(grid.thumbnails.first())
      await grid.contextMenuItem('Share image').click()
      const card = page.locator('.app-dialog:has(.share-dialog-hint)')
      await expect(card).toBeVisible()
      await card.getByRole('button', { name: 'Create link' }).click()
      await expect(page.locator('.share-dialog-url')).toBeVisible()
      await mask(page, '.share-dialog-url', ['http://192.0.2.10:9537/share/example-share-link.jpg'])
      await page.waitForTimeout(300)
      return card
    },
  },
  {
    id: 'plugins',
    assets: ['ScreenshotPlugins.jpg'],
    title: 'Image-filter plugins (Adjust → Filters…)',
    async setup({ grid, page }) {
      await readyGrid(grid)
      await selectN(grid, 3)
      await grid.openContextMenu(grid.thumbnails.first())
      // The image-filter plugins are behind Adjust → Filters…, which opens the
      // filter dialog. It stops short of running one.
      await grid.contextMenu.locator('.ctx-item', { hasText: 'Adjust' }).first().hover()
      await grid.contextMenu.locator('.ctx-item', { hasText: 'Filters' }).first().click()
      // SelectionBar's "Apply Filters" panel, anchored to the selection pill.
      await expect(page.locator('.plugin-menu-panel')).toBeVisible()
      await page.waitForTimeout(600)
      return null
    },
  },
  {
    id: 'tag-review',
    assets: ['ScreenshotReview1.jpg'],
    title: 'Tag review card',
    async setup({ grid, api, page }) {
      await readyGrid(grid)
      await ReviewSessions.ensureHealthBuilt(api)
      const reviews = new ReviewSessions(page)
      await reviews.open()
      await expect(reviews.boardRows.first()).toBeVisible()
      await reviews.createReview(REVIEW_TAG)
      await expect(reviews.card).toBeVisible()
      await page.waitForTimeout(800)
      return null
    },
  },
  {
    id: 'demo',
    assets: ['ScreenshotDemo.jpg'],
    title: 'Features page banner: the app at a glance (a 3:1 strip)',
    async setup({ grid }) {
      await readyGrid(grid)
      // The left of the window, sidebar and grid, as the demo shows them.
      return { clip: { x: 0, y: 0, width: 1200, height: 400 } }
    },
  },
  {
    id: 'object-detection',
    assets: ['ScreenshotSegmentation.jpg'],
    title: 'Object detection boxes in the lightbox',
    async setup(ctx) {
      await readyGrid(ctx.grid)
      await openPicture(ctx, DETECTIONS_PICTURE)
      const toggle = ctx.page.locator('.lightbox-segmentation-btn')
      await toggle.click()
      await expect(toggle).toHaveAttribute('aria-pressed', 'true')
      await ctx.page.waitForTimeout(800)
      return ctx.lightbox.root
    },
  },
  {
    id: 'set-operations',
    assets: ['ScreenshotOverlapNew.jpg'],
    title: 'Boolean set operations: two sets combined',
    async setup({ grid, sidebar, page }) {
      await readyGrid(grid)
      // Two demo-data sets that share pictures, combined as a union.
      await sidebar.setItems.filter({ hasText: 'AI Characters' }).first().click()
      await sidebar.setItems
        .filter({ hasText: 'Website' })
        .first()
        .click({ modifiers: ['ControlOrMeta'] })
      await expect(grid.multiSelectToolbar).toBeVisible()
      // The mode the store restores can land after a first pick; pick until
      // it holds.
      await expect(async () => {
        await grid.multiSelectMode.selectOption('union')
        await expect(grid.multiSelectMode).toHaveValue('union', { timeout: 500 })
      }).toPass()
      await grid.waitForThumbnailLoaded()
      await page.waitForTimeout(900)
      return null
    },
  },
  {
    id: 'workflows',
    assets: ['ScreenshotComfyUi.jpg'],
    title: 'ComfyUI integration: the Workflows page',
    async setup({ page }) {
      await page.goto('/workflows')
      const card = page.locator('.wf-card').first()
      await expect(card).toBeVisible()
      // Selected, so the inspector shows what the workflow is made of.
      await card.click()
      await page.waitForTimeout(1200)
      return null
    },
  },
  {
    id: 'model-shelf',
    assets: ['ScreenshotModelShelf.jpg'],
    title: 'The model shelf (the seeded stand-in models only)',
    async setup({ page }) {
      // Rows, not the sets grid. The shelf also lists the capture machine's
      // own folders (PixlStash's engines, its Hugging Face cache), so the shot
      // is of the seeded folder alone: serve_e2e_backend.py's stand-ins.
      await page.evaluate(() =>
        localStorage.setItem('pixlstash:modelShelfView', JSON.stringify({ v: 3, groupBy: 'none' })),
      )
      await page.goto('/models')
      const folder = page
        .getByTestId('model-folder-tree')
        .first()
        .locator('.mft-row', { hasText: 'model-shelf' })
        .first()
      await folder.click()
      await expect(page.locator('.shelf-row').first()).toBeVisible()
      // Each folder row's location line names where it is on the capture
      // machine (the work dir, its own caches); the labels say enough.
      await mask(page, '.mft-origin', [''])
      await page.waitForTimeout(800)
      return null
    },
  },
  // ── API docs (pixlstash/data/scalar-assets): creating an API token ────────
  {
    id: 'where-settings',
    assets: ['WhereIsUserSettings.jpg'],
    title: 'The Settings gear in the title bar',
    async setup({ grid, settings, page }) {
      await readyGrid(grid)
      await settings.openButton.hover()
      await page.waitForTimeout(300)
      // The right end of the title bar and the top of the grid under it.
      const box = await settings.openButton.boundingBox()
      return { clip: { x: box.x - 560, y: 0, width: 640, height: 440 } }
    },
  },
  {
    id: 'account-tokens',
    assets: ['ScreenshotsUserSettings.jpg'],
    title: 'Settings → Account & tokens',
    async setup({ grid, settings }) {
      await readyGrid(grid)
      await settings.open()
      await settings.openAccountTab()
      return settings.card
    },
  },
  {
    id: 'token-create',
    assets: ['ScreenshotTokens.jpg'],
    title: 'The New token dialog, filled in',
    async setup({ grid, settings, page }) {
      await readyGrid(grid)
      await settings.open()
      await settings.openAccountTab()
      await settings.openCreateTokenDialog()
      await settings.tokenDescription.fill('LM Studio')
      await page.waitForTimeout(300)
      // The settings dialog's box, with the New token dialog drawn over it.
      return settings.card
    },
  },
  {
    id: 'token-shown',
    assets: ['ScreenshotToken.jpg'],
    title: 'A new token, shown once',
    async setup({ grid, settings, page }) {
      await readyGrid(grid)
      await settings.open()
      await settings.openAccountTab()
      await settings.openCreateTokenDialog()
      await settings.tokenDescription.fill('LM Studio')
      await settings.createTokenButton.click()
      await expect(page.getByText(/only once|won.t be able to see it again/i).first()).toBeVisible()
      await mask(page, '.account-token-value', ['example-api-token-shown-only-once'])
      await page.waitForTimeout(300)
      return settings.card
    },
  },
  {
    // Runs LAST among grid scenes: it persists a similarity sort on the owner's
    // config, so keeping it at the end avoids re-sorting earlier grid captures.
    id: 'similarity',
    assets: ['ScreenshotGridSimilarity.jpg'],
    title: 'Similarity sorting (All Pictures by likeness to one person)',
    async setup({ grid, page }) {
      // Sort the WHOLE library by likeness to a person via the toolbar (the
      // user flow) so the grid actually re-fetches — config alone doesn't
      // re-trigger the query. Matches the original "Sort: Similarity <name>".
      await readyGrid(grid)
      await grid.openSortMenu()
      await grid.sortOption('Similarity to').click()
      await page.waitForTimeout(300)
      const person = page
        .locator('.gb-sort-panel .gb-sim-btn', { hasText: 'Angela Merkel' })
        .first()
      await expect(person).toBeVisible()
      await person.click()
      await page.waitForTimeout(800)
      await page.keyboard.press('Escape') // close the sort dropdown
      await grid.waitForThumbnailLoaded()
      await page.waitForTimeout(800)
      return null
    },
  },
  {
    // Same view, "Mixed stacks" mode: stacks whose members are not all the
    // same picture, flagged for review.
    id: 'mixed-stacks',
    assets: ['ScreenshotMixedStacks.jpg'],
    title: 'Duplicates queue — mixed stacks flagged for review',
    async setup({ page }) {
      await page.goto('/duplicates')
      await expect(page.locator('.dq')).toBeVisible()
      // The bar button and the ⋯ row are one pair: a container query at
      // ≤1180px flips which of the two is visible, and the queue's bar is
      // under that at this viewport, so take whichever is showing.
      const bar = page.getByTestId('mixed-toggle')
      if (await bar.isVisible()) {
        await bar.click()
      } else {
        await page.locator('.dq-overflow .tbo-trigger').click()
        await page.getByTestId('mixed-row').click()
      }
      await page.waitForTimeout(1200)
      return null
    },
  },
  {
    // LAST on purpose: it switches the active library, and every scene above
    // wants demo-data; teardown switches back for videos.spec.js. An empty
    // folder is the one verdict POST /libraries attaches outright, with no
    // mapping step in the way; the hub is per-run and thrown away with the
    // work dir, so nothing outside the capture is registered.
    id: 'empty-library',
    assets: ['ScreenshotEmptyLibrary.jpg'],
    title: 'The first screen of a new library (three ways in)',
    async setup({ grid, api, page }) {
      await readyGrid(grid)
      const listed = await (await api.get('/api/v1/libraries')).json()
      demoLibrary = listed.libraries.find((library) => library.is_active)?.uuid
      expect(demoLibrary, 'no active library to come back to').toBeTruthy()
      const root = join(tmpdir(), 'pixlstash-shots-empty-library')
      rmSync(root, { recursive: true, force: true })
      mkdirSync(root, { recursive: true })
      const added = await api.post('/api/v1/libraries', {
        data: { path: root, name: 'New library' },
      })
      expect(added.ok(), `could not add the empty library: ${added.status()}`).toBe(true)
      const { uuid } = await added.json()
      const switched = await api.post('/api/v1/libraries/active', {
        data: { uuid },
      })
      expect(switched.ok(), `could not switch library: ${switched.status()}`).toBe(true)
      // No reload here: the switch route tells every connected client to
      // reload itself, and racing it with page.reload() aborts the navigation.
      // The card is deliberately late (>=350ms after load) so it never flashes
      // over a library that does have pictures.
      await expect(page.locator('.library-empty')).toBeVisible({ timeout: 30_000 })
      await page.waitForTimeout(600)
      return null
    },
    async teardown({ api, grid }) {
      const back = await api.post('/api/v1/libraries/active', { data: { uuid: demoLibrary } })
      expect(back.ok(), `could not switch back to demo-data: ${back.status()}`).toBe(true)
      // The switch reloads the window; wait for demo-data's grid to be back.
      await expect(grid.thumbnailImages.first()).toBeVisible({ timeout: 30_000 })
    },
  },
]

// The library empty-library switches away from, for its teardown.
let demoLibrary = null

export const manual = {
  // ComfyUI illustrations. The 1.11 run overwrote these two with the SPA's
  // search box from a "search" scene; the originals are the ComfyUI nodes.
  'SemanticSearch.jpg': 'ComfyUI Semantic Search node — external app',
  'ScreenshotSearchEdit.jpg': 'Face likeness search into a ComfyUI image edit — external app',
  // 1.12–1.13. Each needs a live ComfyUI (a connection, a run, its templates
  // or a model download in flight), which the capture backend does not have.
  'ComfyAdapterLoader.jpg': 'ComfyUI graph — external app',
  'ComfyAdapterPicker.jpg': 'PixlStash picker inside ComfyUI — external app',
  'ComfyCheckpointPicker.jpg': 'PixlStash picker inside ComfyUI — external app',
  'ComfyPictureLikenessGate.jpg': 'ComfyUI graph — external app',
  'ConnectComfyUI.jpg': 'Connect ComfyUI dialog with its checks passed — needs a live ComfyUI',
  'EditInComfyUI.jpg': 'ComfyUI with a PixlStash workflow open — external app',
  'OpenInComfyUI.jpg': 'Open in ComfyUI menu — needs a live ComfyUI',
  'OpenOriginalInComfyUI.jpg': 'Open original in ComfyUI — needs a live ComfyUI',
  'RunWorkflow.jpg': 'Run window — needs a live ComfyUI and real workflows',
  'SaveToPixlStash.jpg': 'Saving back from ComfyUI — external app',
  'Workflows.jpg': 'Workflows grid — needs real workflows with real pictures',
  'WorkflowSets.jpg': 'Workflow sets — needs real workflows and checkpoints',
  'ModelDownload.jpg': 'Tasks tab with a model download in flight — needs a live download',
  'TemplateDownloads.jpg': 'ComfyUI templates tab — needs a live ComfyUI',
  'WaitingForGPU.png': 'A run waiting for the GPU — needs a live ComfyUI run',
  'ScreenshotWizard.jpg':
    'First-run setup (1.11) — the shell shows it only when it has no server-config, ' +
    'and the captures start from one',
  // Dropped as a scene: the panel is empty by the time the captures run.
  'ScreenshotTaskManager.jpg':
    'Task manager throughput — demo-data is fully processed before the captures start, ' +
    'so the panel reads "No active tasks"; needs work in flight to be worth a shot',
  // The 1.10 illustrations. They went onto the site without script.json being
  // regenerated, so the guardrail never saw them; the v1.11 regeneration is
  // what surfaced them. Classified here rather than left unaccounted.
  'ScreenshotCli.jpg': 'pixlstash-cli --help in a terminal — not the SPA',
  'ScreenshotCliLibraries.jpg': 'pixlstash-cli libraries in a terminal — not the SPA',
  'ScreenshotCliPlugins.jpg': 'pixlstash-cli plugins in a terminal — not the SPA',
  'ScreenshotAiToolkitImport.jpg':
    'Discovered ai-toolkit training runs — needs a real ai-toolkit output folder to read',
  'ScreenshotDeleteLora.jpg':
    'Deleting a model from the shelf — needs the model-shelf fixture above, and the dialog is destructive',
  'ScreenshotPermissionRepair.jpg':
    'The desktop start-up permission check — fires only on a library folder another account can write to, which the hardened capture work dir never is',
  'ScreenshotInstallId.jpg':
    'Upgrade dialog offering the anonymous install ID — needs an upgraded-from-older-version state, not a first run',
  'ScreenshotDuplicates.jpg':
    'Duplicates queue with groups — the duplicate scan is still queued when the capture runs, so the queue reads "Queue clear"; needs the scan to finish first',
  'ScreenshotKeepCoverOnly.jpg':
    '"Keep cover only" in the picture menu — reproducible next; demo-data has 13 stacks, the scene needs to select one and open the menu',
  'ScreenshotKeepCoverOnly2.jpg':
    '"Keep cover only" preview dialog — reproducible next, follows ScreenshotKeepCoverOnly.jpg',
  'ScreenshotStackFilter.jpg':
    'Grid filtered to stacked pictures — reproducible next; demo-data has 13 stacks',
  'ScreenshotMultiProject.jpg':
    'One character in more than one project — needs a multi-project fixture',
  'ScreenshotUndoHistory.jpg':
    'Undo/redo history dropdown — needs a library with recent operations; demo-data is freshly imported',
  'ScreenshotReview3.jpg':
    'Tag health table (1.7) — hand-shot; needs review history to populate the rankings',
  'ScreenshotLockSets.jpg':
    'Set context menu with Lock set (1.7) — hand-shot against a curated library',
  'ScreenshotSettings.jpg':
    'Refreshed settings dialog (1.7) — hand-shot; replaceable by a settings-tab scene later',
  'ComfyWorkflow.png': 'ComfyUI graph — external app, not the PixlStash SPA',
  'ComfyImageEdit.jpg': 'ComfyUI graph — external app',
  'ComfyOutpaint.jpg': 'ComfyUI graph — external app',
  'ComfyUpscale.jpg': 'ComfyUI graph — external app',
  'ComfyResult.jpg': 'ComfyUI graph — external app',
  'ComfyInstallation.jpg': 'ComfyUI Manager UI — external app',
  'ComfyFaceLikenessGate.jpg': 'ComfyUI graph — external app',
  'ComfyFaceLikenessGateUpscale.jpg': 'ComfyUI graph — external app',
  'ScreenshotPhotographySaver.jpg': 'ComfyUI custom node — external app',
  'ScreenshotLmStudio.jpg': 'LM Studio app — external',
  'ScreenshotChat1.jpg': 'LM Studio chat — external app',
  'ScreenshotChat2.jpg': 'LM Studio chat — external app',
  'ScreenshotChat3.jpg': 'LM Studio chat — external app',
  'ScreenshotJoyCaption.jpg': 'Historical (1.3) tagger settings — needs a settings-tab scene + matching plugins',
  'ScreenshotTaggers.jpg': 'Historical (1.3) tagger settings — needs a settings-tab scene',
  'ScreenshotUrl.jpg': 'Browser URL bar — outside the app viewport',
  'ScreenshotWhatsNew1_2.jpg': 'Historical composite from the 1.2 release notes',
  'ScreenshotIconColor.jpg': 'Custom set icon/colour picker — needs a set-edit fixture scene',
  'ScreenshotOverlap.jpg': 'Boolean Overlap mode — needs ≥2 selected people with shared pictures',
  'ScreenshotMutuallyExclusive.jpg': 'Mutually-exclusive tag setup — needs a specific tag fixture',
  'ScreenshotReferenceFolders.jpg': 'the sidebar no longer lists reference folders',
  'FaceLikenessSearch.jpg': 'Face-likeness search — needs a face-search fixture + indexed faces',
  'MultiLikenessSearch.jpg': 'Multi-face likeness search — needs a face-search fixture',
  'ScreenshotShare2.jpg': 'Recipient share view — needs a minted public share link',
  'ScreenshotDragCharacters.jpg': 'Live HTML5 drag (drag ghost + drop-zone highlight) is not reliably capturable headless — needs a manual capture',
  'SmartScreen.jpg': 'Windows SmartScreen OS dialog — not the app',
  'SmartScreen2.jpg': 'Windows SmartScreen OS dialog — not the app',
}

/** asset → scene that produces it (first match wins). */
export function sceneForAsset(asset) {
  return scenes.find((s) => s.assets.includes(asset)) || null
}
