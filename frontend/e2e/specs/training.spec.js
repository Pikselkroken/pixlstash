// Training a LoRA of a person, end to end (#2361).
//
// Against the real backend and the fake ai-toolkit serve_e2e_backend.py lays
// out (training-fixture.spec.js is the fixture's own check). The spec makes
// its own person, a set under them holding three of the fixture's pictures,
// and trains on that set from the sidebar's Train LoRA… entry. It covers the
// three ways a run ends: finished (the LoRA is on the shelf, attached to the
// person), stopped from the UI (no LoRA), and failed (the trainer's reason is
// on the run's page).
//
// The failing run uses a second interpreter inside the same checkout's .venv:
// a wrapper that sets FAKE_RUN_FAIL for the fake trainer and execs the real
// one, which the config PUT accepts as the checkout's own.
//
// Everything it makes is deleted after: the runs, the LoRA (permanently, after
// checking it sits in the work dir), the run folders, the person and the set.

import { chmodSync, existsSync, realpathSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, sep } from 'node:path'
import { test, expect } from '../fixtures/test.js'
import { ModelShelfPage, deleteModels, seededFolder } from '../pages/ModelShelfPage.js'
import { TrainingPage } from '../pages/TrainingPage.js'

const API = '/api/v1'
const WORK_DIR_PREFIX = `${realpathSync(tmpdir())}${sep}pixlstash-e2e-`
// One line, no quote marks: it goes into the wrapper's shell line verbatim.
const TRAINER_REASON = 'e2e fake loader error: no weights in the base model folder'
const LIVE = ['queued', 'running', 'stopping']

async function body(res) {
  expect(res.ok(), `${res.url()} answered ${res.status()}: ${await res.text()}`).toBe(true)
  return res.json()
}

/** Every file assertion: the path is inside this run's throwaway work dir. */
function expectUnder(workDir, path) {
  expect(workDir.startsWith(WORK_DIR_PREFIX), workDir).toBe(true)
  expect(path.startsWith(`${workDir}${sep}`), path).toBe(true)
}

const job = async (ctx, id) => body(await ctx.get(`${API}/training/jobs/${id}`))
/** The list's row for a job: the one that names its run folder. */
const listed = async (ctx, id) =>
  (await body(await ctx.get(`${API}/training/jobs`))).find((j) => j.id === id)
const adapterIds = async (ctx, params = {}) =>
  (await body(await ctx.get(`${API}/adapters`, { params }))).adapters.map((a) => a.id)

/** A run's parsed "Training n / total" headline, or null. */
async function liveStep(training) {
  const m = (await training.liveStatus.innerText()).match(/Training ([\d,]+) \/ ([\d,]+)/)
  return m ? Number(m[1].replace(/,/g, '')) : null
}

test.describe('training a LoRA of a person', () => {
  const uniq = Date.now().toString(36)
  let workDir
  let checkout
  let status
  let person
  let set
  const jobIds = []
  const modelIds = []

  const connect = (ctx, python) =>
    ctx.put(`${API}/training/ai-toolkit/config`, {
      data: { repo_path: checkout, python: python ?? join(checkout, '.venv', 'bin', 'python') },
    })

  /** The dialog's POST, for the runs started without the dialog. */
  async function startRun(ctx, label, steps) {
    const created = await body(
      await ctx.post(`${API}/training/jobs`, {
        data: {
          set_id: set.id,
          character_id: person.id,
          name: `${person.name} (${label})`,
          trigger_word: `e2e${label}`,
          effort: 'standard',
          fit: 'auto',
          subject: 'person',
          pins: { 'train.steps': steps },
        },
      }),
    )
    jobIds.push(created.id)
    return created
  }

  test.beforeAll(async ({ apiContext }) => {
    ;({ workDir } = await seededFolder(apiContext))
    checkout = join(workDir, 'ai-toolkit')
    expectUnder(workDir, checkout)
    const connected = await connect(apiContext)
    expect(connected.ok(), await connected.text()).toBe(true)
    status = await body(await apiContext.get(`${API}/training/ai-toolkit/status`))
    expect(status.configured).toBe(true)
    expectUnder(workDir, status.runs.path)

    person = (
      await body(await apiContext.post(`${API}/characters`, { data: { name: `E2E Trainee ${uniq}` } }))
    ).character
    set = (
      await body(
        await apiContext.post(`${API}/picture_sets`, {
          data: { name: `E2E likeness ${uniq}`, parent_character_id: person.id },
        }),
      )
    ).picture_set
    // Three fixture pictures, read only: membership is the set's own.
    const pictures = await body(await apiContext.get(`${API}/pictures`, { params: { limit: 3 } }))
    expect(pictures.length).toBe(3)
    await body(
      await apiContext.post(`${API}/picture_sets/${set.id}/members`, {
        data: { picture_ids: pictures.map((p) => p.id) },
      }),
    )
  })

  test.afterAll(async ({ apiContext }) => {
    try {
      const runFolders = []
      for (const id of jobIds) {
        const row = await listed(apiContext, id)
        if (!row) continue
        if (row.run_name) runFolders.push(join(status.runs.path, row.run_name))
        modelIds.push(...(row.model_ids ?? []))
        if (LIVE.includes(row.status)) {
          await apiContext.post(`${API}/training/jobs/${id}/cancel`, { data: {} })
        }
        // 409 until the run has stopped, or finished importing.
        await expect
          .poll(async () => (await apiContext.delete(`${API}/training/jobs/${id}`)).status(), {
            timeout: 30_000,
          })
          .toBe(200)
      }
      // The LoRA and its checkpoints: permanently, and only from the work dir.
      const ids = [...new Set(modelIds)]
      if (ids.length) {
        const rows = (await body(await apiContext.get(`${API}/adapters`))).adapters
        for (const row of rows.filter((r) => ids.includes(r.id))) {
          for (const loc of row.locations) expectUnder(workDir, join(loc.folder_path, loc.relpath))
        }
        await deleteModels(apiContext, ids)
      }
      for (const folder of runFolders) {
        expectUnder(workDir, folder)
        rmSync(folder, { recursive: true, force: true })
      }
      if (set) await body(await apiContext.delete(`${API}/picture_sets/${set.id}`))
      if (person) await body(await apiContext.delete(`${API}/characters/${person.id}`))
    } finally {
      const disconnected = await apiContext.put(`${API}/training/ai-toolkit/config`, {
        data: { repo_path: null, python: null },
      })
      expect(disconnected.ok(), await disconnected.text()).toBe(true)
    }
  })

  test('a run started from a set under a person trains, and its LoRA lands on the shelf as theirs', async ({
    page,
    grid,
    sidebar,
    apiContext,
  }) => {
    test.setTimeout(150_000)
    const training = new TrainingPage(page)
    const shelfBefore = await adapterIds(apiContext)

    // 1. Train LoRA… on the set under the person, from the sidebar.
    await grid.goto()
    const group = page
      .locator('.sidebar-character-group')
      .filter({ has: page.getByText(person.name, { exact: true }) })
    await page.getByRole('button', { name: `Show the sets in ${person.name}`, exact: true }).click()
    const setRow = group.locator('.sidebar-set-item').filter({ has: page.getByText(set.name, { exact: true }) })
    await sidebar.openSetContextMenu(setRow)
    await sidebar.ctxButton('Train LoRA…').click()
    await expect(training.dialog).toBeVisible()

    // A character LoRA of the person, trained on the set it was opened on.
    await expect(training.lead).toContainText(`Character LoRA of ${person.name}`)
    await expect(
      training.lead.getByRole('radio', { name: new RegExp(`^${set.name} ·`) }),
    ).toHaveAttribute('aria-checked', 'true')

    // The recipes on offer are the ones the checkout's base models allow.
    const presets = await body(await apiContext.get(`${API}/training/presets`))
    const offered = presets.filter((p) => p.offered)
    expect(offered.length, 'the fake checkout offers at least one recipe').toBeGreaterThan(0)
    const options = await training.baseModelOptions()
    for (const preset of presets) {
      const option = options.find((text) => text.includes(preset.label))
      expect(option, `${preset.label} is listed`).toBeTruthy()
      expect(option.includes('Needs a newer ai-toolkit'), option).toBe(!preset.offered)
    }

    // Read the defaults the dialog's own rules filled in; the run must use them.
    await expect(training.trigger).not.toHaveValue('')
    const trigger = await training.trigger.inputValue()
    const baseText = await training.baseModel.innerText()
    const recipe = offered.find((p) => baseText.includes(p.label))
    expect(recipe, `the default recipe (${baseText}) is one on offer`).toBeTruthy()

    // Short enough to finish in the test, long enough to be seen training.
    await training.advanced.click()
    await training.steps.fill('200')
    await training.steps.press('Tab')
    await expect(training.start).toBeEnabled()
    const posted = page.waitForResponse(
      (r) => r.request().method() === 'POST' && r.url().endsWith(`${API}/training/jobs`),
    )
    await training.start.click()
    const res = await posted
    expect(res.ok(), await res.text()).toBe(true)
    const { id, uuid } = await res.json()
    jobIds.push(id)
    await expect(training.dialog).toBeHidden()

    const started = await job(apiContext, id)
    expect(started).toMatchObject({
      character_id: person.id,
      set_id: set.id,
      trigger_word: trigger,
    })
    expect(started.advanced_pins).toEqual({ 'train.steps': 200 })
    expect(started.preset.split('@')[0]).toBe(recipe.id)

    // 2. It shows on Training runs as training, and its page shows steps
    //    advancing and a loss chart.
    await page.goto('/models/runs')
    const card = training.card(started.name)
    await expect(card.locator('.tr-card-chip')).toHaveText('Training', { timeout: 30_000 })

    await training.openRun(uuid)
    let first = null
    await expect
      .poll(async () => (first = await liveStep(training)) ?? -1, { timeout: 30_000 })
      .toBeGreaterThan(0)
    await expect
      .poll(async () => (await liveStep(training)) ?? 201, { timeout: 15_000 })
      .toBeGreaterThan(first)
    await expect(training.lossChart).toHaveAttribute(
      'aria-label',
      /^Training loss from [\d.e+-]+ at step \d+ to [\d.e+-]+ at step \d+ over \d+ recorded points\.$/,
    )

    // 3. It finishes, and the LoRA is on the shelf, attached to the person.
    await expect(training.liveStatus).toContainText('Finished', { timeout: 60_000 })
    const done = await job(apiContext, id)
    expect(done.status).toBe('completed')
    expect(done.completed_at).toBeTruthy()
    expect(done.model_ids.length).toBeGreaterThan(0)
    modelIds.push(...done.model_ids)
    for (const mid of done.model_ids) expect(shelfBefore).not.toContain(mid)

    const { run_name: runName } = await listed(apiContext, id)
    const runFolder = join(status.runs.path, runName)
    expectUnder(workDir, runFolder)
    expect(existsSync(join(runFolder, `${runName}.safetensors`))).toBe(true)

    const rows = (await body(await apiContext.get(`${API}/adapters`))).adapters.filter((r) =>
      done.model_ids.includes(r.id),
    )
    expect(rows.length).toBe(done.model_ids.length)
    for (const row of rows) {
      for (const loc of row.locations) expectUnder(workDir, join(loc.folder_path, loc.relpath))
    }
    // The shelf's side: the trained file carries the person.
    const lora = rows.find((r) =>
      r.attachments.some((a) => a.entity_type === 'character' && a.entity_id === person.id),
    )
    expect(lora, 'a model from the run is attached to the person').toBeTruthy()
    expect(lora.filename).toBe(`${runName}.safetensors`)
    const detail = await body(await apiContext.get(`${API}/adapters/${lora.sha256}`))
    expect(detail.attachments).toContainEqual({ entity_type: 'character', entity_id: person.id })
    // The person's side: their models are this run's LoRA.
    expect(await adapterIds(apiContext, { character_id: person.id })).toEqual([lora.id])

    // On screen: the file on the Models shelf, and in the person's Adapters.
    const shelf = new ModelShelfPage(page)
    await shelf.goto()
    await expect(shelf.row(lora.filename)).toBeVisible()

    await grid.goto()
    const personRow = group.locator('.sidebar-list-item:not(.sidebar-set-item)')
    await personRow.click({ button: 'right' })
    await sidebar.ctxButton('Edit').click()
    const tray = page.locator('.app-dialog .adapter-tray')
    await expect(tray).toContainText('1 attached')
    await expect(tray.locator('.adapter-card')).toHaveCount(1)
  })

  test('a run stopped from its page stops, writes no LoRA and adds nothing to the shelf', async ({
    page,
    apiContext,
  }) => {
    test.setTimeout(90_000)
    const training = new TrainingPage(page)
    const shelfBefore = await adapterIds(apiContext)
    // Long enough that it is still training when Stop is pressed.
    const { id, uuid } = await startRun(apiContext, 'stopped', 5000)

    await training.openRun(uuid)
    await expect
      .poll(async () => (await liveStep(training)) ?? -1, { timeout: 30_000 })
      .toBeGreaterThan(0)
    page.once('dialog', (d) => d.accept())
    await training.liveStop.click()
    await expect(training.liveStatus).toContainText(/Stopped at step [\d,]+/, { timeout: 20_000 })

    const stopped = await job(apiContext, id)
    expect(stopped.status).toBe('stopped')
    expect(stopped.step).toBeLessThan(5000)
    expect(stopped.model_ids).toEqual([])
    const { run_name: runName } = await listed(apiContext, id)
    expect(runName).toBeTruthy()
    const runFolder = join(status.runs.path, runName)
    expectUnder(workDir, runFolder)
    expect(existsSync(runFolder)).toBe(true)
    expect(existsSync(join(runFolder, `${runName}.safetensors`))).toBe(false)
    expect(await adapterIds(apiContext)).toEqual(shelfBefore)
  })

  test("a failed run shows the trainer's reason, not a generic error", async ({
    page,
    apiContext,
  }) => {
    test.setTimeout(60_000)
    const training = new TrainingPage(page)
    // A second interpreter in the checkout's own .venv, which the config PUT
    // accepts, that makes the fake trainer fail with TRAINER_REASON.
    const wrapper = join(checkout, '.venv', 'bin', 'python-e2e-fails')
    expectUnder(workDir, wrapper)
    writeFileSync(
      wrapper,
      `#!/bin/sh\nFAKE_RUN_FAIL='${TRAINER_REASON}' exec '${join(checkout, '.venv', 'bin', 'python')}' "$@"\n`,
    )
    chmodSync(wrapper, 0o755)
    try {
      const switched = await connect(apiContext, wrapper)
      expect(switched.ok(), await switched.text()).toBe(true)
      const { id, uuid } = await startRun(apiContext, 'failing', 20)
      await expect
        .poll(async () => (await job(apiContext, id)).status, { timeout: 30_000 })
        .toBe('error')
      const failed = await job(apiContext, id)
      expect(failed.error).toContain(TRAINER_REASON)
      expect(failed.model_ids).toEqual([])

      // The run's page on Training runs. Not its card in the grid: a run that
      // fails before its first checkpoint has a folder the listing skips, so
      // it has no card there.
      await training.openRun(uuid)
      await expect(training.liveStatus).toContainText('Failed')
      await expect(training.liveNotice).toContainText('The run failed.')
      await expect(training.liveNotice).toContainText(TRAINER_REASON)
    } finally {
      const restored = await connect(apiContext)
      expect(restored.ok(), await restored.text()).toBe(true)
      rmSync(wrapper, { force: true })
    }
  })
})
