// Reproduces the marketing-site illustrations with the current UI, captured
// from the real desktop app (electronApp.js). A first step forces the
// appearance the originals use (dark theme, sensible grid density) via the
// user-config API, so every capture matches. For every reproducible scene
// (scenes.js) it drives the app into the right state and writes a JPEG to
// website/screenshots/output/, named after the site asset it replaces. A final
// coverage check reads the website "script" (script.json) and fails if any
// feature illustration is neither reproduced nor explicitly listed as
// manual/branding — so nothing is silently dropped.
import { mkdirSync, writeFileSync, existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, expect } from './electronApp.js'
import { GridPage } from '../pages/GridPage.js'
import { Lightbox } from '../pages/Lightbox.js'
import { SettingsDialog } from '../pages/SettingsDialog.js'
import { SideBar } from '../pages/SideBar.js'
import { scenes, manual, sceneForAsset } from './scenes.js'

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
    lightbox: new Lightbox(page),
    settings: new SettingsDialog(page),
    sidebar: new SideBar(page),
  }
}

test.describe('reproduce website screenshots', () => {
  // Match the look of the original site shots: dark theme + a denser grid.
  // (Stars/face-box grid overlays are left off here — their click.stop handlers
  // intercept thumbnail clicks; the lightbox scenes toggle face boxes
  // themselves.) Persisted server-side for the owner, so every later capture
  // inherits it. Runs first by file order (workers: 1).
  test('appearance: dark theme + density', async ({ apiContext }) => {
    const res = await apiContext.patch('/api/v1/users/me/config', {
      data: {
        // Answer the first-run prompts so no consent scrim lands in a shot,
        // and keep the update check and telemetry off a throwaway install.
        check_for_updates: false,
        telemetry_send_install_id: false,
        telemetry_send_feature_usage: false,
        telemetry_send_error_reports: false,
        telemetry_send_hardware_profile: false,
        telemetry_consent_prompted: true,
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
      // A scene that waits on real backend work (the v1.11 folder read runs
      // face detection over the fixture on CPU) declares how long it needs.
      // Its own expect timeout is not enough on its own — the test timeout
      // fires first and the wait never gets to finish.
      if (scene.timeout) test.setTimeout(scene.timeout)
      // Every scene shares one window, so start each from what a fresh
      // browser context used to give it: no per-tab UI state (an inspector
      // left open, a remembered tab) carried over from the scene before.
      await page.evaluate(() => {
        localStorage.clear()
        sessionStorage.clear()
      })
      const ctx = ctxFor(page)
      ctx.api = apiContext
      try {
        const target = await scene.setup(ctx)
        const shooter = target?.clip ? page : target ?? page
        for (const asset of scene.assets) {
          await shooter.screenshot({
            path: outPath(asset),
            type: 'jpeg',
            quality: 90,
            ...(target?.clip ? { clip: target.clip } : {}),
          })
          expect(existsSync(outPath(asset))).toBe(true)
        }
      } finally {
        if (scene.teardown) await scene.teardown(ctx)
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
