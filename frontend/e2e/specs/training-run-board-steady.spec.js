// A training run's board sizes itself to the window (fit() in
// TrainingRunLive.vue). It once sized the chart for the rows at the old pitch,
// so a wider pitch overflowed the page; the page's scrollbar narrowed the
// board, the pitch flipped back, and the board flickered between two layouts
// every frame. Headless Chromium hides scrollbars, which hides the loop, so
// this spec shows them, as the desktop app does.

import { test, expect } from '../fixtures/test.js'

test.use({ launchOptions: { ignoreDefaultArgs: ['--hide-scrollbars'] } })

const columns = [0, 250, 500, 750, 1000, 1250, 1500]
const run = {
  id: 7, uuid: 'abcd', name: 'example-run', status: 'running', phase: 'training',
  previews: null, previews_deferred: false, step: 750, total_steps: 1500,
  set_id: null, character_id: null, trigger_word: 'ohwx',
  phases: [{ phase: 'waiting_gpu', seconds: 30 }, { phase: 'loading', seconds: 125 }, { phase: 'training', seconds: 600 }],
  columns,
  rows: [0, 1, 2, 3].map((index) => ({
    index, prompt: `photo ${index} of ohwx woman`, with_trigger: index < 2,
    cells: columns.map((c, i) => (c <= 750 ? { state: 'sample', filename: `s_${i}.jpg` } : { state: 'future', filename: null })),
  })),
  run_folder: { folder_id: 3, run_name: 'example-run' }, imported: false, coverable: [], cover_step: null,
  loss_curve: Array.from({ length: 50 }, (_, i) => ({ step: i * 10, loss: 0.5 - i * 0.002 })),
  dataset_picture_ids: [1, 2, 3], streamed: null,
  speed: { seconds_per_step: 1.9, remaining_low_s: 960, remaining_high_s: 1260 },
  telemetry: { gpu: { total_mb: 24576, used_mb: 21811, ours_mb: null }, ram: { total_mb: null, used_mb: null, ours_mb: null }, need: { vram_mb: 16000, ram_mb: 16000 }, paging: false },
  waiting: null, finished_while_closed: null, error: null,
  created_at: '2026-10-26T01:00:00+00:00', started_at: '2026-10-26T01:00:30+00:00', completed_at: null,
}

test('the run board settles on one layout', async ({ page }) => {
  // A size where the pitch is bound by height and the old fit() looped.
  await page.setViewportSize({ width: 1780, height: 900 })
  await page.route('**/api/v1/training/jobs', (r) => r.fulfill({ json: [run] }))
  await page.route('**/api/v1/training/jobs/7', (r) => r.fulfill({ json: run }))
  await page.route('**/api/v1/training/jobs/7/live', (r) => r.fulfill({ json: run }))
  await page.goto(`/models/runs?run=${run.uuid}`)
  await page.locator('.trl-board').waitFor()

  const layouts = await page.evaluate(async () => {
    const seen = new Set()
    for (let i = 0; i < 60; i++) {
      await new Promise((r) => requestAnimationFrame(r))
      const board = document.querySelector('.trl-board')
      const body = document.querySelector('.trl-body')
      seen.add(`${board.style.getPropertyValue('--trl-pitch')} ${board.style.getPropertyValue('--trl-chart-h')} ${body.scrollHeight > body.clientHeight}`)
    }
    return [...seen]
  })
  expect(layouts).toHaveLength(1)
  // And the page does not scroll down: the board fits the window.
  expect(layouts[0].endsWith('false')).toBe(true)
})
