import { test, expect } from '../fixtures/test.js'

// The fake ComfyUI (e2e/fake_comfyui.py, #2360) against the real run routes:
// nothing here is a `page.route`. A run goes from `POST /workflows/run` to the
// fake, which records the graph and saves a picture; the backend fetches it
// from `/view` and imports it for the person the run names, as it would from
// a real ComfyUI.
//
// Each test makes its own workflow and person and deletes them, with the
// pictures its runs made, afterwards: the backend and the fake are shared by
// every spec in the run.

const API = '/api/v1'
const FAKE = process.env.PIXLSTASH_E2E_COMFYUI_URL

/** Text-to-image on the stub checkpoint the launcher's model folder holds. */
const GRAPH = {
  1: {
    class_type: 'CheckpointLoaderSimple',
    inputs: { ckpt_name: 'RealVisXL_V4.safetensors' },
  },
  3: {
    class_type: 'CLIPTextEncode',
    inputs: { text: 'a lighthouse at dusk', clip: ['1', 1] },
  },
  4: { class_type: 'CLIPTextEncode', inputs: { text: 'blurry', clip: ['1', 1] } },
  5: {
    class_type: 'EmptyLatentImage',
    inputs: { width: 512, height: 512, batch_size: 1 },
  },
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
    class_type: 'SaveImage',
    inputs: { filename_prefix: 'e2e', images: ['7', 0] },
  },
}

const made = { workflows: [], people: [], pictures: [] }

async function ok(response, what) {
  expect(response.ok(), `${what}: ${response.status()} ${await response.text()}`).toBe(true)
  return response.json()
}

async function seed(api) {
  const t = `${Date.now().toString(36)}-${made.workflows.length}`
  const { workflow_id: workflowId } = await ok(
    await api.post(`${API}/comfyui/workflows/import`, {
      data: { name: `Fake ComfyUI e2e ${t}`, workflow: GRAPH },
    }),
    'import the workflow',
  )
  made.workflows.push(workflowId)
  const { character } = await ok(
    await api.post(`${API}/characters`, { data: { name: `Fake ComfyUI person ${t}` } }),
    'make a person',
  )
  made.people.push(character.id)
  return { workflowId, personId: character.id }
}

/** Tell the fake how to fail; `null` puts it right. */
async function failWith(api, mode) {
  await ok(await api.put(`${FAKE}/__fake/fail`, { data: { mode } }), `fail ${mode}`)
}

test.afterEach(async ({ apiContext }) => {
  await failWith(apiContext, null)
  const ids = made.pictures.splice(0)
  if (ids.length) {
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
  }
  for (const id of made.people.splice(0)) {
    await ok(await apiContext.delete(`${API}/characters/${id}`), `delete person ${id}`)
  }
  for (const id of made.workflows.splice(0)) {
    await ok(await apiContext.delete(`${API}/workflows/${id}`), `delete workflow ${id}`)
  }
})

test('a run reaches the fake ComfyUI and its picture is filed for the person it names', async ({
  apiContext,
}) => {
  const { workflowId, personId } = await seed(apiContext)

  const run = await ok(
    await apiContext.post(`${API}/workflows/run`, {
      data: { workflow_id: workflowId, character_id: personId },
    }),
    'run the workflow',
  )
  expect(run.status, JSON.stringify(run)).toBe('success')
  const [{ prompt_id: promptId, run_id: runId }] = run.prompts
  expect(promptId).toBeTruthy()

  // The fake was sent the graph, and it is this workflow's.
  const sent = await ok(await apiContext.get(`${FAKE}/__fake/prompts`), 'read the fake')
  const graph = sent.find((p) => p.prompt_id === promptId)?.prompt
  expect(graph, 'the fake recorded this run').toBeTruthy()
  const byClass = (cls) => Object.values(graph).filter((node) => node.class_type === cls)
  expect(byClass('CheckpointLoaderSimple')[0].inputs.ckpt_name).toBe('RealVisXL_V4.safetensors')
  expect(byClass('CLIPTextEncode').map((n) => n.inputs.text)).toContain('a lighthouse at dusk')
  expect(byClass('SaveImage')).toHaveLength(1)

  // The run imported what the fake saved...
  let outcome
  await expect
    .poll(
      async () => {
        outcome = await ok(await apiContext.get(`${API}/workflows/runs/${runId}`), 'run outcome')
        return outcome.status
      },
      { timeout: 20_000 },
    )
    .toBe('completed')
  made.pictures.push(...outcome.picture_ids)
  expect(outcome.picture_ids, JSON.stringify(outcome)).toHaveLength(1)

  // ...and filed it under the person the run named. A fresh picture has no
  // faces yet, so the person waits on it until face extraction names its
  // largest face; the e2e backend loads no face model, so it waits here.
  const [pictureId] = outcome.picture_ids
  const pending = await ok(
    await apiContext.get(`${API}/pictures/${pictureId}/pending_character_id`),
    'read the picture',
  )
  expect(pending.pending_character_id).toBe(personId)
})

test('the fake fails on demand, through the real run path', async ({ apiContext }) => {
  const { workflowId } = await seed(apiContext)
  const body = { data: { workflow_id: workflowId } }

  // No model files: the pre-flight refuses, and nothing is sent.
  await failWith(apiContext, 'missing_models')
  const preflight = await ok(
    await apiContext.post(`${API}/workflows/run/preflight`, body),
    'preflight',
  )
  expect(preflight.ok).toBe(false)
  const reasons = preflight.groups.flatMap((g) => g.reasons.map((r) => r.code))
  expect(reasons).toContain('missing_models')

  // A node error: ComfyUI's own refusal of the graph reaches the caller.
  await failWith(apiContext, 'node_error')
  const refused = await apiContext.post(`${API}/workflows/run`, body)
  expect(refused.status()).toBe(502)
  expect((await refused.json()).detail).toContain('The fake ComfyUI was told to fail')
})
