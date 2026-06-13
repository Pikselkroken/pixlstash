// Reproduces the marketing-site illustrations with the current UI. A first step
// forces the appearance the originals use (dark theme, sensible grid density,
// stars + face boxes on) via the user-config API, so every capture matches. For
// every reproducible scene (scenes.js) it drives the live SPA into the right
// state and writes a PNG to website/screenshots/output/, named after the site
// asset it replaces. A final coverage check reads the website "script"
// (script.json) and fails if any feature illustration is neither reproduced nor
// explicitly listed as manual/branding — so nothing is silently dropped.
import { mkdirSync, writeFileSync, existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, expect } from '../fixtures/test.js'
import { GridPage } from '../pages/GridPage.js'
import { ImageOverlay } from '../pages/ImageOverlay.js'
import { SettingsDialog } from '../pages/SettingsDialog.js'
import { SideBar } from '../pages/SideBar.js'
import { scenes, manual, sceneForAsset } from './scenes.js'
import { useDesktopBridge } from './desktopBridge.js'

const here = dirname(fileURLToPath(import.meta.url))
const OUT_DIR = join(here, '..', '..', '..', 'website', 'screenshots', 'output')
const SCRIPT = join(here, '..', '..', '..', 'website', 'screenshots', 'script.json')

// Pure branding / non-feature art that we never reproduce from the app.
const IGNORE = new Set([
  'logo.png',
  'docker-mark-black.svg',
  'Testimonial1.png',
  'Testimonial2.png',
  'Testimonial2.jpg',
  'VideoHat.webp',
])

mkdirSync(OUT_DIR, { recursive: true })

// Always emit JPEG (the site uses .jpg), named after the asset basename.
const outPath = (asset) =>
  join(OUT_DIR, asset.replace(/\.(jpe?g|png|gif|webp)$/i, '') + '.jpg')

function ctxFor(page) {
  return {
    page,
    grid: new GridPage(page),
    overlay: new ImageOverlay(page),
    settings: new SettingsDialog(page),
    sidebar: new SideBar(page),
  }
}

test.describe('reproduce website screenshots', () => {
  // Match the look of the original site shots: dark theme + a denser grid.
  // (Stars/face-box grid overlays are left off here — their click.stop handlers
  // intercept thumbnail clicks; the overlay scenes toggle face boxes
  // themselves.) Persisted server-side for the owner, so every later capture
  // inherits it. Runs first by file order (workers: 1).
  test('appearance: dark theme + density', async ({ apiContext }) => {
    const res = await apiContext.patch('/api/v1/users/me/config', {
      data: {
        theme_mode: 'dark',
        columns: 6,
        // Expanded sidebar (docked=false) so set / people / project rows are
        // rendered — the collapsed rail only shows icons, which also breaks the
        // projects + breadcrumb scenes that click those rows.
        sidebar_docked: false,
      },
    })
    expect(res.ok(), `config patch failed: ${res.status()}`).toBe(true)
  })

  for (const scene of scenes) {
    test(`${scene.id} → ${scene.assets.join(', ')}`, async ({ page, apiContext }) => {
      // Render as the desktop app (custom title bar + window controls) unless
      // the scene opts out (e.g. a recipient browser view).
      if (!scene.browser) await useDesktopBridge(page)
      const ctx = ctxFor(page)
      ctx.api = apiContext
      const target = await scene.setup(ctx)
      const shooter = target ?? page
      for (const asset of scene.assets) {
        await shooter.screenshot({ path: outPath(asset), type: 'jpeg', quality: 90 })
        expect(existsSync(outPath(asset))).toBe(true)
      }
    })
  }

  test('coverage report (no illustration silently dropped)', async () => {
    const manifest = JSON.parse(readFileSync(SCRIPT, 'utf8'))
    const images = manifest.filter((e) => e.kind === 'image')

    const reproduced = []
    const manualList = []
    const unaccounted = []
    for (const e of images) {
      if (sceneForAsset(e.asset)) reproduced.push(e)
      else if (manual[e.asset] || IGNORE.has(e.asset)) manualList.push(e)
      else unaccounted.push(e)
    }

    const lines = [
      '# Website screenshot reproduction — coverage',
      '',
      `Generated from \`website/screenshots/script.json\` (${images.length} feature illustrations).`,
      '',
      `- Reproduced from the SPA: **${reproduced.length}**`,
      `- Manual / external / branding: **${manualList.length}**`,
      `- Unaccounted: **${unaccounted.length}**`,
      '',
      '## Reproduced (written to output/)',
      '',
      ...reproduced.map(
        (e) => `- \`${e.asset}\` — ${sceneForAsset(e.asset).title} _(${e.label || e.alt || ''})_`,
      ),
      '',
      '## Manual / external (not reproduced from the app)',
      '',
      ...manualList.map(
        (e) => `- \`${e.asset}\` — ${manual[e.asset] || 'branding / non-feature art'}`,
      ),
    ]
    if (unaccounted.length) {
      lines.push('', '## ⚠ Unaccounted (classify these)', '')
      lines.push(...unaccounted.map((e) => `- \`${e.asset}\` — ${e.label || e.alt || ''}`))
    }
    writeFileSync(join(OUT_DIR, '..', 'coverage.md'), lines.join('\n') + '\n')

    expect(
      unaccounted.map((e) => e.asset),
      'every feature illustration must be reproduced or listed in scenes.manual / IGNORE',
    ).toEqual([])
  })
})
