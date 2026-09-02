// The app scene behind each marketing-site illustration. Each scene drives the
// live SPA into the state the ORIGINAL screenshot shows (verified against
// website/assets/*), then returns the locator to capture (or null for the whole
// viewport). One scene can satisfy several site assets. Non-reproducible
// illustrations are listed in `manual` with a reason.
//
// Runs against the demo-data library (varied photos, populated people/projects/
// sets) in a forced dark theme, rendered as the desktop app (title bar + window
// controls) — see capture.spec.js. ctx = { page, grid, overlay, settings,
// sidebar, api }.
import { cpSync, mkdirSync, readdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect } from '@playwright/test'

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
  await settings.openTab('Libraries')
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

// listAccelerators() payloads for the desktop bridge, one per GPU vendor. Labels
// mirror electron/src/config.ts ACCEL_LABELS and the shape mirrors main.ts's
// describeAccelerators(): a bundled CPU runtime plus the single GPU overlay the
// hardware detector offers for that machine, not yet installed.
const cudaAccelerators = {
  bundled: { accel: 'cpu', label: 'CPU', active: true },
  items: [
    {
      accel: 'cu128',
      label: 'NVIDIA GPU (CUDA 12.8)',
      installed: false,
      active: false,
      recommended: true,
    },
  ],
}
const rocmAccelerators = {
  bundled: { accel: 'cpu', label: 'CPU', active: true },
  items: [
    {
      accel: 'rocm',
      label: 'AMD GPU (ROCm, experimental)',
      installed: false,
      active: false,
      recommended: true,
    },
  ],
}

async function readyGrid(grid) {
  await grid.goto()
  await grid.waitForThumbnailLoaded()
  await grid.page.waitForTimeout(900) // let lazy thumbnails paint
}

/** Open the detail overlay from the first grid thumbnail, robustly. */
async function openOverlay({ grid, overlay, page }) {
  const card = grid.thumbnails.first()
  await card.scrollIntoViewIfNeeded()
  await card.click()
  if (await overlay.root.isVisible().catch(() => false)) return
  await page.waitForTimeout(400)
  await grid.thumbnailImages.first().click({ force: true }).catch(() => {})
  await expect(overlay.root).toBeVisible()
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
    id: 'image-overlay',
    assets: ['ScreenshotImageOverlay.jpg'],
    title: 'Picture inspection overlay',
    async setup({ grid, overlay }) {
      await readyGrid(grid)
      await openOverlay({ grid, overlay, page: grid.page })
      await expect(overlay.mainImage).toBeVisible()
      await grid.page.waitForTimeout(600)
      return overlay.root
    },
  },
  {
    id: 'face-detection',
    assets: ['ScreenshotImageOverlay2.jpg'],
    title: 'Face detection in the overlay',
    async setup({ grid, overlay, sidebar }) {
      await readyGrid(grid)
      await filterToPerson(sidebar)
      await openOverlay({ grid, overlay, page: grid.page })
      await expect(overlay.mainImage).toBeVisible()
      if (await overlay.faceBboxToggle.count()) {
        await overlay.faceBboxToggle.click().catch(() => {})
        if (!(await overlay.faceBboxes.count())) {
          await overlay.faceBboxToggle.click().catch(() => {})
        }
      }
      await grid.page.waitForTimeout(600)
      return overlay.root
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
      // "Set" is an AddToEntityControl (.ate-btn), not a .ctx-item. Highlight it.
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
      // Highlight the "Reverse image search" action, as in the original.
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
    id: 'search',
    assets: ['SemanticSearch.jpg', 'ScreenshotSearchEdit.jpg'],
    title: 'Search overlay',
    async setup({ grid, page }) {
      await readyGrid(grid)
      await grid.searchButton.click()
      await expect(grid.searchOverlay).toBeVisible()
      await grid.searchInput.fill('a person smiling outdoors')
      await page.waitForTimeout(500)
      return grid.searchOverlay
    },
  },
  {
    id: 'settings',
    assets: ['ScreenshotUserSettings.jpg', 'ScreenshotsUserSettings.jpg', 'ScreenshotsUserSettings.png'],
    title: 'User settings dialog',
    async setup({ grid, settings }) {
      await readyGrid(grid)
      await settings.open()
      return settings.card
    },
  },
  {
    id: 'backend-settings',
    assets: ['ScreenshotBackend.jpg'],
    title: 'Desktop Backend settings (remote access + desktop)',
    async setup({ grid, settings, page }) {
      await readyGrid(grid)
      await settings.open()
      // The desktop-only "Backend" rail item. Compute acceleration used to
      // share this pane; it is its own "Compute" item now (backend-cuda /
      // backend-rocm below), so this one shows remote access + desktop.
      await settings.openTab('Backend')
      await expect(page.getByText('Remote access')).toBeVisible()
      await page.waitForTimeout(400)
      return settings.card
    },
  },
  {
    id: 'backend-cuda',
    assets: ['ScreenshotBackendCuda.jpg'],
    title: 'Desktop Backend settings — NVIDIA CUDA acceleration available',
    // Show the Backend tab on an NVIDIA machine: the CUDA overlay is offered
    // with an "Install (recommended)" action (the post-install "active" state
    // can't be shown without a real overlay download).
    bridge: { accelerators: cudaAccelerators },
    async setup({ grid, settings, page }) {
      await readyGrid(grid)
      await settings.open()
      await settings.openTab('Compute')
      await expect(page.getByText('NVIDIA GPU (CUDA 12.8)')).toBeVisible()
      await page.waitForTimeout(400)
      return settings.card
    },
  },
  {
    id: 'backend-rocm',
    assets: ['ScreenshotBackendRocm.jpg'],
    title: 'Desktop Backend settings — AMD ROCm acceleration available',
    // Same tab on an AMD machine: the experimental ROCm overlay is offered.
    bridge: { accelerators: rocmAccelerators },
    async setup({ grid, settings, page }) {
      await readyGrid(grid)
      await settings.open()
      await settings.openTab('Compute')
      await expect(page.getByText('AMD GPU (ROCm, experimental)')).toBeVisible()
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
    id: 'libraries-manage',
    assets: ['ScreenshotLibrariesManage.jpg'],
    title: 'Settings → Libraries with a row\'s ⋯ menu open',
    async setup({ grid, settings, page }) {
      await readyGrid(grid)
      await settings.open()
      await settings.openTab('Libraries')
      // The full menu (open / rename / stop using) only exists on a row that is
      // NOT the active library, and demo-data registers exactly one. Add a
      // second so the menu has every verb in it — an EMPTY folder, which is the
      // one verdict that adds a library outright with no mapping step in the
      // way. The hub is per-run and thrown away with the work dir, so this
      // registers nothing outside the capture.
      const secondRoot = join(tmpdir(), 'pixlstash-shots-second-library')
      rmSync(secondRoot, { recursive: true, force: true })
      mkdirSync(secondRoot, { recursive: true })
      const res = await page.request.post('/api/v1/libraries', {
        data: { path: secondRoot, name: 'Client work' },
      })
      expect(res.ok(), `could not add the second library: ${res.status()}`).toBe(true)
      await page.reload()
      await settings.open()
      await settings.openTab('Libraries')
      const row = page.locator('.library-row:not(.library-row--active)').first()
      await row.locator('.library-row__more').click()
      await expect(page.locator('.library-menu')).toBeVisible()
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
      await settings.openTab('Privacy')
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
      const tagsTab = grid.statsTabs.filter({ hasText: 'Tags' }).first()
      if (await tagsTab.count()) await tagsTab.click()
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
      return page.locator('.sidebar').first()
    },
  },
  {
    id: 'projects',
    assets: ['ScreenshotProject.jpg'],
    title: 'Project organisation',
    async setup({ grid, sidebar }) {
      await readyGrid(grid)
      await sidebar.openProjectsTab()
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
      await sidebar.openProjectsTab()
      const project = sidebar.projectRows.first()
      await project.click()
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
    async setup({ grid, overlay, page }) {
      await readyGrid(grid)
      await openOverlay({ grid, overlay, page })
      await expect(overlay.mainImage).toBeVisible()
      await overlay.addTagButton.click()
      await expect(overlay.tagInput).toBeVisible()
      await overlay.tagInput.fill('su')
      await page.waitForTimeout(600)
      return overlay.root
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
    // wants demo-data. An empty folder is the one verdict POST /libraries
    // attaches outright, with no mapping step in the way; the hub is per-run
    // and thrown away with the work dir, so nothing outside the capture is
    // registered.
    id: 'empty-library',
    assets: ['ScreenshotEmptyLibrary.jpg'],
    title: 'The first screen of a new library (three ways in)',
    async setup({ grid, page }) {
      await readyGrid(grid)
      const root = join(tmpdir(), 'pixlstash-shots-empty-library')
      rmSync(root, { recursive: true, force: true })
      mkdirSync(root, { recursive: true })
      const added = await page.request.post('/api/v1/libraries', {
        data: { path: root, name: 'New library' },
      })
      expect(added.ok(), `could not add the empty library: ${added.status()}`).toBe(true)
      const { uuid } = await added.json()
      const switched = await page.request.post('/api/v1/libraries/active', {
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
  },
]

export const manual = {
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
  'ScreenshotLibraries.jpg':
    'Settings → Libraries with two registered libraries — superseded on the site by ScreenshotLibrariesManage.jpg, which the libraries-manage scene reproduces',
  'ScreenshotModelShelf.jpg':
    'The model shelf — demo-data registers no model folders, so the shelf renders empty; needs a model-folder fixture',
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
  'ScreenshotsJustified.jpg':
    'Justified grid — reproducible next; the scene must persist thumbnail_mode and restore it so later captures stay square',
  'ScreenshotsJustifiedSettings.jpg':
    'Appearance settings layout picker — reproducible next, alongside ScreenshotsJustified.jpg',
  'ScreenshotReview1.jpg':
    'Tag review card (1.7) — hand-shot against a curated library; needs a review-session fixture scene',
  'ScreenshotReview3.jpg':
    'Tag health table (1.7) — hand-shot; needs review history to populate the rankings',
  'ScreenshotLockSets.jpg':
    'Set context menu with Lock set (1.7) — hand-shot against a curated library',
  'ScreenshotSegmentation.jpg':
    'Object-detection boxes (1.7) — hand-shot; needs a segmented picture fixture',
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
  'ScreenshotComfyUi.jpg': 'ComfyUI integration shown in ComfyUI — external app',
  'ScreenshotPhotographySaver.jpg': 'ComfyUI custom node — external app',
  'ScreenshotLmStudio.jpg': 'LM Studio app — external',
  'ScreenshotChat1.jpg': 'LM Studio chat — external app',
  'ScreenshotChat2.jpg': 'LM Studio chat — external app',
  'ScreenshotChat3.jpg': 'LM Studio chat — external app',
  'ScreenshotJoyCaption.jpg': 'Historical (1.3) tagger settings — needs a settings-tab scene + matching plugins',
  'ScreenshotTaggers.jpg': 'Historical (1.3) tagger settings — needs a settings-tab scene',
  'ScreenshotPlugins.jpg': 'Image-filter plugin settings — needs a settings-tab scene + installed plugins',
  'ScreenshotKeyboard.jpg': 'No standalone keyboard-shortcuts dialog located in the current UI',
  'ScreenshotDemo.jpg': 'The public demo site itself (pixlstash.dev demo), not a local app state',
  'ScreenshotUrl.jpg': 'Browser URL bar — outside the app viewport',
  'ScreenshotWhatsNew1_2.jpg': 'Historical composite from the 1.2 release notes',
  'ScreenshotIconColor.jpg': 'Custom set icon/colour picker — needs a set-edit fixture scene',
  'ScreenshotOverlap.jpg': 'Boolean Overlap mode — needs ≥2 selected people with shared pictures',
  'ScreenshotOverlapNew.jpg': 'Boolean set operations — needs ≥2 selected entities',
  'ScreenshotMutuallyExclusive.jpg': 'Mutually-exclusive tag setup — needs a specific tag fixture',
  'ScreenshotReferenceFolders.jpg': 'demo-data has no reference folders to show (Folders tab would be empty)',
  'ScreenshotReferenceFoldersNew.jpg': 'demo-data has no reference folders',
  'FaceLikenessSearch.jpg': 'Face-likeness search — needs a face-search fixture + indexed faces',
  'MultiLikenessSearch.jpg': 'Multi-face likeness search — needs a face-search fixture',
  'ScreenshotShare1.jpg': 'Share dialog — reproducible next; ShareDialog page object exists',
  'ScreenshotShare2.jpg': 'Recipient share view — needs a minted public share link',
  'ScreenshotDragCharacters.jpg': 'Live HTML5 drag (drag ghost + drop-zone highlight) is not reliably capturable headless — needs a manual capture',
  'SmartScreen.jpg': 'Windows SmartScreen OS dialog — not the app',
  'SmartScreen2.jpg': 'Windows SmartScreen OS dialog — not the app',
}

/** asset → scene that produces it (first match wins). */
export function sceneForAsset(asset) {
  return scenes.find((s) => s.assets.includes(asset)) || null
}
