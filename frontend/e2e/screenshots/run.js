#!/usr/bin/env node
// Runner behind `npm run screenshots [version] [playwright args...]`.
//
// With no arguments this is exactly `playwright test --config=
// playwright.screenshots.config.js`, and the captures show whatever version the
// tree is on. Pass a version — `npm run screenshots v1.9.0` — and the whole run
// is stamped with it, so release media can be shot before the release is cut.
//
// Two places show a version, and both are pinned so a single capture can never
// disagree with itself:
//   - the title bar, from vite's build-time __APP_VERSION__ (vite.config.js)
//   - the telemetry-consent dialog, from a runtime GET /version (App.vue),
//     intercepted in capture.spec.js
//
// Any argument that does not look like a version is forwarded to Playwright, so
// `npm run screenshots 1.9.0 --headed -g main-grid` works.
import { spawnSync } from 'node:child_process'

// PEP 440-ish: 1.9.0, v1.9.0, 1.9.0rc1, 1.9.0-dev.2.
const VERSION_RE = /^v?\d+(\.\d+)*([.-]?(a|b|rc|dev|post)\.?\d*)?$/i

let pinned = ''
const passthrough = []
for (const arg of process.argv.slice(2)) {
  if (!pinned && VERSION_RE.test(arg)) {
    // The UI renders "v{{ version }}", so a leading v would double up.
    pinned = arg.replace(/^v/i, '')
    continue
  }
  passthrough.push(arg)
}

const run = (cmd, args, env) =>
  spawnSync(cmd, args, {
    stdio: 'inherit',
    shell: process.platform === 'win32',
    env,
  })

const env = { ...process.env }
if (pinned) {
  env.PIXLSTASH_VERSION_OVERRIDE = pinned
  console.log(`[screenshots] stamping captures with version ${pinned}`)
}

const result = run(
  'npx',
  ['playwright', 'test', '--config=playwright.screenshots.config.js', ...passthrough],
  env,
)

if (pinned) {
  // The harness builds the SPA into the real package dist (vite.config.js sets
  // outDir to ../pixlstash/frontend/dist with emptyOutDir), so a pinned run
  // leaves a bundle claiming a version this tree is not on — which then serves
  // from `python -m pixlstash.app` and misleads the next person to debug a
  // version check. Rebuild honestly before handing the tree back.
  console.log('[screenshots] rebuilding dist without the pinned version')
  const restore = run('npm', ['run', 'build'], process.env)
  if (restore.status !== 0) {
    console.error(
      `[screenshots] WARNING: restore build failed (exit ${restore.status}); ` +
        `pixlstash/frontend/dist still reports ${pinned}. Run \`npm run build\`.`,
    )
  }
}

if (result.error) {
  console.error(`[screenshots] could not start Playwright: ${result.error.message}`)
}
process.exit(result.status ?? 1)
