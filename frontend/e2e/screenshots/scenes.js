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
import { expect } from '@playwright/test'

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
    assets: ['ScreenshotPictureStatistics.png'],
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
    assets: ['ScreenshotTagStatistics.png'],
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
    id: 'task-manager',
    assets: ['ScreenshotTaskManager.jpg'],
    title: 'Task manager in the statistics sidebar',
    async setup({ grid, page }) {
      await readyGrid(grid)
      await grid.statsToggle.click()
      await expect(grid.statsSidebar).toBeVisible()
      const tasksTab = grid.statsTabs.filter({ hasText: /Task/i }).first()
      if (!(await tasksTab.count())) {
        throw new Error('No "Tasks" tab in the statistics sidebar on this build')
      }
      await tasksTab.click()
      await page.waitForTimeout(500)
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
        await page.locator('.dq-overflow').click()
        await page.getByTestId('mixed-row').click()
      }
      await page.waitForTimeout(1200)
      return null
    },
  },
]

export const manual = {
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
