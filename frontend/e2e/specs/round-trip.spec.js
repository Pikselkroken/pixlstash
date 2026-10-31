// The 2.0 round-trip in one spec (#2364): generate pictures of a new person,
// train a LoRA on them, run a workflow with that LoRA, and get the pictures
// back under the same person, with every hop on the record.
//
// Against the real backend, the fake ai-toolkit and the fake ComfyUI
// serve_e2e_backend.py lays out. Each screen has its own spec
// (training.spec.js, workflow-run-lora.spec.js); this one checks that the
// hops join: the pictures one hop makes are what the next one reads. Hops are
// driven through the API where a screen adds nothing to that, and through the
// Train LoRA dialog and the Run popup where the joint is the screen's own
// (the dialog training on the set the run filed into, the popup offering the
// person by the LoRA just trained).
//
// Everything it makes is deleted after, whatever failed: the runs and their
// pictures, the training run and its LoRA (permanently, after checking it
// sits in the work dir), the run folder, the set, the person, the workflow,
// and the checkpoint's base model put back.

import { realpathSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, sep } from 'node:path'
import { test, expect } from '../fixtures/test.js'
import { ok, tag } from '../fixtures/workflowsApi.js'
import { deleteModels, seededFolder } from '../pages/ModelShelfPage.js'
import { TrainingPage } from '../pages/TrainingPage.js'
import { WorkflowsPage } from '../pages/WorkflowsPage.js'

const API = '/api/v1'
const FAKE = process.env.PIXLSTASH_E2E_COMFYUI_URL
const WORK_DIR_PREFIX = `${realpathSync(tmpdir())}${sep}pixlstash-e2e-`
/** What a run of a set needs before Train will start (`MIN_PICTURES`). */
const DATASET_SIZE = 6
const CHECKPOINT = 'RealVisXL_V4.safetensors'
const PROMPT = 'a portrait by the window'
/** Text-to-image on the seeded checkpoint, saved by the ComfyUI-PixlStash Picture Saver. */
const GRAPH = {
  1: { class_type: 'CheckpointLoaderSimple', inputs: { ckpt_name: CHECKPOINT } },
  3: { class_type: 'CLIPTextEncode', inputs: { text: PROMPT, clip: ['1', 1] } },
  4: { class_type: 'CLIPTextEncode', inputs: { text: 'blurry', clip: ['1', 1] } },
  5: { class_type: 'EmptyLatentImage', inputs: { width: 512, height: 512, batch_size: 1 } },
  6: {
    class_type: 'KSampler',
    inputs: {
      seed: 1,
      steps: 20,
      cfg: 5,
      sampler_name: 'euler',
      scheduler: 'normal',
      denoise: 1,
      model: ['1', 0],
      positive: ['3', 0],
      negative: ['4', 0],
      latent_image: ['5', 0],
    },
  },
  7: { class_type: 'VAEDecode', inputs: { samples: ['6', 0], vae: ['1', 2] } },
  8: {
    class_type: 'PixlStashPictureSaver',
    inputs: { images: ['7', 0], filename_prefix: 'e2e-round-trip', save_workflow: true },
  },
}

/** Every file assertion: the path is inside this run's throwaway work dir. */
function expectUnder(workDir, path) {
  expect(workDir.startsWith(WORK_DIR_PREFIX), workDir).toBe(true)
  expect(path.startsWith(`${workDir}${sep}`), path).toBe(true)
}

const byId = (a, b) => a - b
const runDialog = (page) => page.locator('.app-dialog').filter({ hasText: 'Run workflow' })

test.describe('the round-trip', () => {
  let workDir
  let checkout
  let runsPath
  let checkpoint
  let workflowId
  let person
  let set
  let jobId
  const runs = []
  const pictures = []
  const modelIds = []

  test.beforeAll(async ({ apiContext }) => {
    ;({ workDir } = await seededFolder(apiContext))
    checkout = join(workDir, 'ai-toolkit')
    expectUnder(workDir, checkout)
    await ok(
      await apiContext.put(`${API}/training/ai-toolkit/config`, {
        data: { repo_path: checkout, python: join(checkout, '.venv', 'bin', 'python') },
      }),
      'connect the fake ai-toolkit',
    )
    const status = await ok(await apiContext.get(`${API}/training/ai-toolkit/status`), 'status')
    runsPath = status.runs.path
    expectUnder(workDir, runsPath)

    const { checkpoints } = await ok(await apiContext.get(`${API}/checkpoints`), 'list checkpoints')
    checkpoint = checkpoints.find((row) => row.filename.split('/').pop() === CHECKPOINT)
    expect(checkpoint, 'the seeded shelf has its checkpoint').toBeTruthy()

    const name = `Round trip ${tag()}`
    ;({ workflow_id: workflowId } = await ok(
      await apiContext.post(`${API}/comfyui/workflows/import`, { data: { name, workflow: GRAPH } }),
      'store the workflow',
    ))
    ;({ character: person } = await ok(
      await apiContext.post(`${API}/characters`, { data: { name: `Round trip person ${tag()}` } }),
      'make the person',
    ))
    // A child set under the person: what the run files into, and what Train
    // LoRA… is opened on.
    ;({ picture_set: set } = await ok(
      await apiContext.post(`${API}/picture_sets`, {
        data: { name: `Round trip likeness ${tag()}`, parent_character_id: person.id },
      }),
      'make the set',
    ))
  })

  test.afterAll(async ({ apiContext }) => {
    // Every step runs whatever the one before it did; the first failure is
    // rethrown at the end, so a stuck run never leaves the person behind.
    let failure = null
    const step = async (what, fn) => {
      try {
        await fn()
      } catch (error) {
        console.error(`clean-up: ${what} failed`, error)
        failure ??= error
      }
    }
    for (const runId of runs) {
      await step(`run ${runId}`, async () => {
        await expect
          .poll(
            async () => {
              const outcome = await ok(
                await apiContext.get(`${API}/workflows/runs/${runId}`),
                'run outcome',
              )
              pictures.push(...(outcome.picture_ids ?? []))
              return outcome.status
            },
            { timeout: 20_000 },
          )
          .toMatch(/^(completed|failed)$/)
        await ok(await apiContext.delete(`${API}/workflows/runs/${runId}`), `dismiss ${runId}`)
      })
    }
    const ids = [...new Set(pictures)]
    if (ids.length) {
      await step('the pictures', async () => {
        // Delete forever, and only these: to the Scrapheap, then purged by id.
        for (const id of ids) await ok(await apiContext.delete(`${API}/pictures/${id}`), `delete ${id}`)
        const { confirm_token: confirmToken } = await ok(
          await apiContext.post(`${API}/pictures/scrapheap/delete-preview`, {
            data: { picture_ids: ids },
          }),
          'preview the purge',
        )
        await ok(
          await apiContext.delete(`${API}/pictures/scrapheap`, {
            data: { picture_ids: ids, include_protected: false, confirm_token: confirmToken },
          }),
          'purge',
        )
      })
    }
    // The training run by its id, and any other run of the person's: a
    // POST whose answer was lost still made one.
    const runFolders = []
    await step('the training runs', async () => {
      const rows = (await ok(await apiContext.get(`${API}/training/jobs`), 'list runs')).filter(
        (j) => j.id === jobId || (person && j.character_id === person.id),
      )
      for (const row of rows) {
        if (row.run_name) runFolders.push(join(runsPath, row.run_name))
        modelIds.push(...(row.model_ids ?? []))
        if (['queued', 'running', 'stopping'].includes(row.status)) {
          await apiContext.post(`${API}/training/jobs/${row.id}/cancel`, { data: {} })
        }
        // 409 until the run has stopped, or finished importing.
        await expect
          .poll(async () => (await apiContext.delete(`${API}/training/jobs/${row.id}`)).status(), {
            timeout: 30_000,
          })
          .toBe(200)
      }
    })
    // The LoRA and anything else attached to the person: permanently, and
    // only from the work dir.
    await step('the LoRA', async () => {
      if (person) {
        const { adapters } = await ok(
          await apiContext.get(`${API}/adapters`, { params: { character_id: person.id } }),
          "the person's models",
        )
        modelIds.push(...adapters.map((a) => a.id))
      }
      const idsToDelete = [...new Set(modelIds)]
      if (!idsToDelete.length) return
      const { adapters } = await ok(await apiContext.get(`${API}/adapters`), 'list the shelf')
      for (const row of adapters.filter((r) => idsToDelete.includes(r.id))) {
        for (const loc of row.locations) expectUnder(workDir, join(loc.folder_path, loc.relpath))
      }
      await deleteModels(apiContext, idsToDelete)
    })
    for (const runFolder of runFolders) {
      await step('the run folder', async () => {
        expectUnder(workDir, runFolder)
        rmSync(runFolder, { recursive: true, force: true })
      })
    }
    if (checkpoint) {
      await step('the checkpoint', async () =>
        ok(
          await apiContext.patch(`${API}/models`, {
            data: { ids: [checkpoint.id], base_model: checkpoint.base_model ?? null },
          }),
          "put back the checkpoint's base model",
        ),
      )
    }
    if (set) {
      await step('the set', async () =>
        ok(await apiContext.delete(`${API}/picture_sets/${set.id}`), 'delete the set'),
      )
    }
    if (person) {
      await step('the person', async () =>
        ok(await apiContext.delete(`${API}/characters/${person.id}`), 'delete the person'),
      )
    }
    if (workflowId) {
      await step('the workflow', async () =>
        ok(
          await apiContext.delete(`${API}/workflows/${encodeURIComponent(workflowId)}`),
          'delete the workflow',
        ),
      )
    }
    await step('the ai-toolkit connection', async () =>
      ok(
        await apiContext.put(`${API}/training/ai-toolkit/config`, {
          data: { repo_path: null, python: null },
        }),
        'disconnect the fake ai-toolkit',
      ),
    )
    if (failure) throw failure
  })

  /** A run's outcome once it has ended, its pictures recorded for clean-up. */
  async function finished(api, runId) {
    let outcome
    await expect
      .poll(
        async () => {
          outcome = await ok(await api.get(`${API}/workflows/runs/${runId}`), 'run outcome')
          return outcome.status
        },
        { timeout: 30_000 },
      )
      .toBe('completed')
    pictures.push(...outcome.picture_ids)
    return outcome
  }

  /** Who a fresh picture is of: pending until face extraction, which the e2e backend never runs. */
  async function personOf(api, pictureId) {
    const body = await ok(
      await api.get(`${API}/pictures/${pictureId}/pending_character_id`),
      `read picture ${pictureId}'s person`,
    )
    return body.pending_character_id
  }

  test('generate pictures of a person, train a LoRA on them, run with it, and the pictures come back to them', async ({
    page,
    grid,
    sidebar,
    apiContext,
  }) => {
    test.setTimeout(180_000)

    // 1. Generate into the person: the workflow's Picture Saver files what
    //    the fake ComfyUI makes under them, in their set.
    const first = await ok(
      await apiContext.post(`${API}/workflows/run`, {
        data: {
          workflow_id: workflowId,
          count: DATASET_SIZE,
          character_ids: [person.id],
          destination: { set_id: set.id },
        },
      }),
      'run the workflow into the person',
    )
    runs.push(...first.prompts.map((p) => p.run_id))
    expect(first.status, JSON.stringify(first)).toBe('success')
    const generated = []
    for (const { run_id: runId } of first.prompts) {
      generated.push(...(await finished(apiContext, runId)).picture_ids)
    }
    expect(generated).toHaveLength(DATASET_SIZE)
    for (const id of generated) expect(await personOf(apiContext, id), `picture ${id}`).toBe(person.id)
    const { picture_ids: members } = await ok(
      await apiContext.get(`${API}/picture_sets/${set.id}/members`),
      'read the set',
    )
    expect([...members].sort(byId)).toEqual([...generated].sort(byId))

    // 2. Train: Train LoRA… on the person's set, from the sidebar, and wait
    //    for the fake trainer to finish.
    const training = new TrainingPage(page)
    await grid.goto()
    const group = page
      .locator('.sidebar-character-group')
      .filter({ has: page.getByText(person.name, { exact: true }) })
    await page.getByRole('button', { name: `Show the sets in ${person.name}`, exact: true }).click()
    await sidebar.openSetContextMenu(
      group.locator('.sidebar-set-item').filter({ has: page.getByText(set.name, { exact: true }) }),
    )
    await sidebar.ctxButton('Train LoRA…').click()
    await expect(training.dialog).toBeVisible()
    await expect(training.lead).toContainText(`Character LoRA of ${person.name}`)
    await training.advanced.click()
    await training.steps.fill('40')
    await training.steps.press('Tab')
    await expect(training.start).toBeEnabled()
    const posted = page.waitForResponse(
      (r) => r.request().method() === 'POST' && r.url().endsWith(`${API}/training/jobs`),
    )
    await training.start.click()
    const started = await ok(await posted, 'start training')
    jobId = started.id
    await expect(training.dialog).toBeHidden()
    let job
    await expect
      .poll(
        async () => {
          job = await ok(await apiContext.get(`${API}/training/jobs/${jobId}`), 'training run')
          return job.status
        },
        { timeout: 90_000 },
      )
      .toBe('completed')
    expect(job).toMatchObject({ character_id: person.id, set_id: set.id })

    // 3. The LoRA is on the shelf, attached to the person.
    expect(job.model_ids.length, JSON.stringify(job)).toBeGreaterThan(0)
    modelIds.push(...job.model_ids)
    const { adapters: theirs } = await ok(
      await apiContext.get(`${API}/adapters`, { params: { character_id: person.id } }),
      "the person's models",
    )
    expect(theirs, JSON.stringify(theirs)).toHaveLength(1)
    const [lora] = theirs
    expect(job.model_ids).toContain(lora.id)
    for (const loc of lora.locations) expectUnder(workDir, join(loc.folder_path, loc.relpath))

    // 6a. Lineage, the LoRA's side: the shelf row names the run that made it,
    //     and the run's dataset is exactly the pictures hop 1 made.
    expect(lora.provenance).toBe('trained')
    expect(lora.training_run_id).toBe(jobId)
    const snapshot = await ok(
      await apiContext.get(`${API}/training/jobs/${jobId}/snapshot`),
      "the run's dataset",
    )
    expect(snapshot).toMatchObject({ set_id: set.id, character_id: person.id })
    expect(snapshot.items.map((item) => item.picture_id).sort(byId)).toEqual(
      [...generated].sort(byId),
    )

    // 4. Run with it: the Run popup offers the person by the LoRA just
    //    trained, and the graph the fake ComfyUI receives loads that file.
    //    The popup offers a person's LoRA only for a workflow of its base
    //    model, so the checkpoint is given the LoRA's.
    expect(lora.base_model, 'the trained LoRA records its base model').toBeTruthy()
    await ok(
      await apiContext.patch(`${API}/models`, {
        data: { ids: [checkpoint.id], base_model: lora.base_model },
      }),
      "give the checkpoint the LoRA's base model",
    )
    const workflows = new WorkflowsPage(page)
    await workflows.goto()
    await workflows.card(workflowId).click()
    await workflows.rail.getByRole('button', { name: /^Run( with changes)?…$/ }).click()
    const dialog = runDialog(page)
    await expect(dialog).toBeVisible()
    const tile = dialog.getByRole('group', { name: 'Person' }).getByRole('checkbox', { name: person.name })
    await tile.click()
    await expect(tile).toBeChecked()
    const answer = page.waitForResponse(
      (r) => r.request().method() === 'POST' && new URL(r.url()).pathname === `${API}/workflows/run`,
    )
    await dialog.locator('footer').getByRole('button', { name: /^Run/ }).click()
    const second = await (await answer).json()
    runs.push(...(second.prompts ?? []).map((p) => p.run_id).filter(Boolean))
    expect(second.status, JSON.stringify(second)).toBe('success')
    await expect(dialog).toHaveCount(0)
    const [{ prompt_id: promptId, run_id: runId }] = second.prompts
    const sent = await ok(await apiContext.get(`${FAKE}/__fake/prompts`), 'read the fake')
    const graph = sent.find((entry) => entry.prompt_id === promptId)?.prompt
    expect(graph, 'the fake recorded the run').toBeTruthy()
    const loaders = Object.values(graph).filter((node) => /Lora|Adapter/.test(node.class_type))
    expect(loaders, JSON.stringify(graph)).toHaveLength(1)
    const loads = loaders[0].inputs.adapter_sha256 ?? loaders[0].inputs.lora_name
    expect([lora.sha256, lora.filename.split('/').pop()], JSON.stringify(loaders[0])).toContain(loads)
    // Spliced between the checkpoint and the sampler: it reads the
    // checkpoint's model, and the sampler reads the LoRA's.
    expect(loaders[0].inputs.model, 'the LoRA reads the checkpoint').toEqual(['1', 0])
    const sampler = Object.values(graph).find((node) => node.class_type === 'KSampler')
    expect(graph[sampler.inputs.model[0]], 'the sampler reads the model through the LoRA').toBe(
      loaders[0],
    )

    // 5. Back to the person: the new pictures are filed under them.
    const outcome = await finished(apiContext, runId)
    expect(outcome.picture_ids.length).toBeGreaterThan(0)
    for (const id of outcome.picture_ids) {
      expect(generated).not.toContain(id)
      expect(await personOf(apiContext, id), `picture ${id}`).toBe(person.id)
    }

    // 6b. Lineage, the pictures' side: each one's recipe names the LoRA by
    //     its digest, and that digest is the trained LoRA's shelf row.
    for (const id of outcome.picture_ids) {
      const recipe = await ok(
        await apiContext.get(`${API}/comfyui/pictures/${id}/recipe`, { params: { preflight: false } }),
        `picture ${id}'s recipe`,
      )
      expect(recipe.workflow_id, JSON.stringify(recipe)).toBe(workflowId)
      expect(
        recipe.lora_slots.map((slot) => [slot.by, slot.value]),
        JSON.stringify(recipe.lora_slots),
      ).toEqual([['digest', lora.sha256]])
      const rows = recipe.model_slots.filter((slot) => slot.model_id === lora.id)
      expect(rows, JSON.stringify(recipe.model_slots)).toHaveLength(1)
      expect(rows[0].verified).toBe(true)
    }
  })
})
