// Capture the first-run setup wizard (electron/src/renderer/setup.html) showing
// the CPU-vs-GPU compute choice on an NVIDIA (CUDA) and an AMD (ROCm) machine.
//
// The wizard is a standalone Electron renderer page, NOT the Vue SPA, so there's
// no dev server to drive: we load its HTML over file:// and inject a stub preload
// bridge (window.pixlstashDesktop) mirroring the setup surface of
// electron/src/preload.ts that setup.js calls. probeSetup() returns a detected
// GPU so the compute panel un-hides; the rest are inert handlers. addInitScript
// runs before page scripts and is not subject to the page CSP, so the stub lands
// even though setup.html sets `script-src 'self'`. Writes JPEGs to the same
// website/screenshots/output/ dir as the SPA captures.
import { mkdirSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { test, expect } from '@playwright/test'

const here = dirname(fileURLToPath(import.meta.url))
const OUT_DIR = join(here, '..', '..', '..', 'website', 'screenshots', 'output')
const SETUP_HTML = join(
  here,
  '..',
  '..',
  '..',
  'electron',
  'src',
  'renderer',
  'setup.html',
)

mkdirSync(OUT_DIR, { recursive: true })

// Stub the bridge setup.js reads. probeSetup() drives the compute panel; the
// rest only need to exist so the page wires up without throwing. `gpu` is
// injected per-scene. commitSetup is a no-op (we never click "Get started").
function installSetupBridge(gpu) {
  const val = (v) => () => Promise.resolve(v)
  window.pixlstashDesktop = {
    probeSetup: val({
      defaults: {
        imageRoot: 'C:\\Users\\You\\Pictures\\PixlStash',
        useGpu: true,
      },
      importedFrom: null,
      gpu,
    }),
    pickLibraryFolder: val(null),
    commitSetup: val(undefined),
    onProgress: () => () => {},
    windowMinimize: val(undefined),
    windowToggleMaximize: val(undefined),
    windowClose: val(undefined),
  }
}

// Realistic detected-GPU payloads (probeSetup().gpu in main.ts): label mirrors
// ACCEL_LABELS, name mirrors HardwareDetector's gpuName.
const wizardScenes = [
  {
    asset: 'ScreenshotWizardCuda.jpg',
    gpu: {
      available: true,
      accel: 'cu128',
      label: 'NVIDIA GPU (CUDA 12.8)',
      name: 'NVIDIA GeForce RTX 4090',
    },
  },
  {
    asset: 'ScreenshotWizardRocm.jpg',
    gpu: {
      available: true,
      accel: 'rocm',
      label: 'AMD GPU (ROCm, experimental)',
      name: 'AMD Radeon RX 7900 XTX',
    },
  },
]

test.describe('first-run setup wizard (compute choice)', () => {
  for (const scene of wizardScenes) {
    test(`wizard → ${scene.asset}`, async ({ page }) => {
      // Snug window framing the title bar + 560px panel. The wizard content is
      // top-aligned and ~624px tall, so 680 leaves a small bottom margin without
      // acres of empty space (the real window is taller, 1280×860).
      await page.setViewportSize({ width: 820, height: 680 })
      await page.addInitScript(installSetupBridge, scene.gpu)
      await page.goto(pathToFileURL(SETUP_HTML).href)
      // The compute panel only un-hides once probeSetup resolves a detected GPU.
      await expect(page.locator('#computePanel')).toBeVisible()
      await expect(page.getByText(scene.gpu.label)).toBeVisible()
      await page.waitForTimeout(300)
      await page.screenshot({
        path: join(OUT_DIR, scene.asset),
        type: 'jpeg',
        quality: 90,
      })
    })
  }
})
