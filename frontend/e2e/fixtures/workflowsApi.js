import { expect } from "./test.js";

// The real workflow API, for the Workflows specs that run without fakes
// (`workflows-card-lifecycle`, `workflows-change-bar`,
// `workflow-fakes-contract`). Each spec stores or duplicates the workflows it
// acts on and deletes them after: the backend is shared, and a card left
// behind is listed on every later Workflows screen.

export const API = "/api/v1";

/** The #2314 seed's workflow with a checkpoint and two LoRAs. */
export const SEEDED_LORA = "Seeded LoRA portrait";

/** The checkpoint `REPAIR_GRAPH` names, which the e2e shelf does not have. */
export const RETIRED_CHECKPOINT = "Retired_checkpoint_v1.safetensors";

/**
 * A text-to-image graph for the specs that edit and save: an upscale stage
 * after the sampler, so the Workflow tab offers a stage toggle, and a
 * checkpoint this machine does not have, so choosing the shelf's own is a
 * repair Save writes as a new version. Neither edit asks ComfyUI anything:
 * the e2e backend has none, and on a developer's machine the default address
 * is their own. No LoRA loader for the same reason: a save over a graph with
 * a filename LoRA slot asks ComfyUI which file to write there.
 */
export const REPAIR_GRAPH = {
  1: { class_type: "CheckpointLoaderSimple", inputs: { ckpt_name: RETIRED_CHECKPOINT } },
  2: { class_type: "CLIPTextEncode", inputs: { text: "a lighthouse at dusk", clip: ["1", 1] } },
  3: { class_type: "CLIPTextEncode", inputs: { text: "blurry", clip: ["1", 1] } },
  4: { class_type: "EmptyLatentImage", inputs: { width: 832, height: 1216, batch_size: 1 } },
  5: {
    class_type: "KSampler",
    inputs: {
      seed: 1,
      steps: 28,
      cfg: 5,
      sampler_name: "euler",
      scheduler: "normal",
      denoise: 1,
      model: ["1", 0],
      positive: ["2", 0],
      negative: ["3", 0],
      latent_image: ["4", 0],
    },
  },
  6: { class_type: "VAEDecode", inputs: { samples: ["5", 0], vae: ["1", 2] } },
  11: { class_type: "UpscaleModelLoader", inputs: { model_name: "4x-UltraSharp.pth" } },
  12: { class_type: "ImageUpscaleWithModel", inputs: { upscale_model: ["11", 0], image: ["6", 0] } },
  7: { class_type: "SaveImage", inputs: { filename_prefix: "e2e", images: ["12", 0] } },
};

/** The checkpoint file a stored workflow's graph loads now. */
export async function checkpointOf(api, id) {
  const { card } = await ok(await api.get(workflowPath(id)), `read ${id}`);
  return card.models.find((model) => model.kind === "checkpoint")?.filename;
}

/** The body of a response that must have succeeded. */
export async function ok(response, what) {
  expect(response.ok(), `${what}: ${response.status()} ${await response.text()}`).toBe(true);
  return response.json();
}

/** Distinct per call, so a name never matches a card another run left. */
let serial = 0;
export function tag() {
  serial += 1;
  return `${Date.now().toString(36)}-${serial}`;
}

export const workflowPath = (id) => `${API}/workflows/${encodeURIComponent(id)}`;

/** `GET /workflows`, with hidden cards when asked. */
export async function listWorkflows(api, query = {}) {
  return ok(await api.get(`${API}/workflows`, { params: query }), "list workflows");
}

/** The id of the listed card called *name*. */
export async function cardId(api, name) {
  const { cards } = await listWorkflows(api);
  const card = cards.find((c) => c.name === name);
  expect(card, `a card named ${name}`).toBeTruthy();
  return card.id;
}

/**
 * The workflows the running test made, for `removeAll` to delete. One list
 * per worker process, shared by every spec file it runs; the suite runs one
 * worker, one test at a time, and each spec drains it after every test.
 */
export const made = [];

/** Duplicate *id* through the real route; the copy's id, recorded. */
export async function duplicate(api, id) {
  const body = await ok(await api.post(`${workflowPath(id)}/duplicate`), `duplicate ${id}`);
  made.push(body.workflow_id);
  return body.workflow_id;
}

/** Store *graph* as a manual workflow through the import route; its id, recorded. */
export async function store(api, name, graph = REPAIR_GRAPH) {
  const body = await ok(
    await api.post(`${API}/comfyui/workflows/import`, { data: { name, workflow: graph } }),
    `import ${name}`,
  );
  made.push(body.workflow_id);
  return body.workflow_id;
}

/** Delete every workflow recorded; a 404 is one the test deleted itself. */
export async function removeAll(api) {
  for (const id of made.splice(0)) {
    const res = await api.delete(workflowPath(id));
    expect(res.ok() || res.status() === 404, `clean up ${id}: ${res.status()}`).toBe(true);
  }
}
