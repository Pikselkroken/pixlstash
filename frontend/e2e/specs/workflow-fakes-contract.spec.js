import { test, expect } from "../fixtures/test.js";
import { CKPT, WF as TAB_WF, stubWorkflows } from "../fixtures/workflowTabStubs.js";
import { WF as POPUP_WF, stubWorkflow } from "../fixtures/workflowStubs.js";
import {
  API,
  SEEDED_LORA,
  cardId,
  listWorkflows,
  ok,
  removeAll,
  store,
  tag,
  workflowPath,
} from "../fixtures/workflowsApi.js";

// The contract guard for the Workflows fakes (#2315).
//
// `workflow-tab-editor.spec.js` and `run-popup-save.spec.js` (and
// `workflow-run.spec.js`, which shares the popup's fake) answer every
// `/workflows*` call from a fake, so they stay green whatever the real server
// sends. This spec installs each fake on a page, asks it what those specs ask,
// asks the real backend the same, and fails when the fake serves a key the
// real answer does not have, or a value of another JSON type. A real field
// renamed, removed or retyped turns it red.
//
// A key the real answer has and the fake lacks is not drift: the fakes carry
// only what their specs read. `null` on either side matches anything, since a
// nullable field is null in one world and filled in another. An array is held
// against every element the real one has (their shapes united); a real array
// that is empty cannot be checked, and says so in the test's annotations,
// except the few the fakes lean on, which must not be empty (`MUST_VERIFY`).
//
// Not compared: `POST /workflows/run` and the poll's `finished[]` need a run
// (`workflow-run.spec.js` holds those against the server's models, through
// `tests/test_e2e_workflow_run_fakes.py`); `GET /comfyui/pixlstash-node`
// needs a ComfyUI, and so does `PUT /workflows/{id}/lora-chain`, even as a
// dry run (it asks ComfyUI what each node hands on); the `/users/me/config`
// fakes are the real body with one key added; `/characters/{id}/thumbnail` is
// an SVG.

/** Objects whose keys are data (a stage's name, a file's), not field names. */
const MAPS = new Set(["stages", "stage_details", "swaps", "takes", "choices", "picks"]);

/** The answers the fakes lean on: an empty real one would check nothing. */
const MUST_VERIFY = [
  "GET /workflows .cards",
  "GET /workflows/{id}/lora-chain .loaders",
  "GET /workflows/{id}/versions, after a save .versions",
  "GET /adapters .adapters",
];

const kind = (value) =>
  value === null ? "null" : Array.isArray(value) ? "array" : typeof value;

/** One shape for many values: objects' keys and arrays' elements united. */
function unite(values) {
  const present = values.filter((value) => value !== null && value !== undefined);
  if (!present.length) return null;
  if (present.every(Array.isArray)) {
    const items = present.flat();
    return items.length ? [unite(items)] : [];
  }
  const objects = present.filter((value) => kind(value) === "object");
  if (objects.length !== present.length) return present[0];
  const keys = new Set(objects.flatMap(Object.keys));
  return Object.fromEntries([...keys].map((key) => [key, unite(objects.map((o) => o[key]))]));
}

/**
 * Every path where *fake* serves what *real* does not. *real* is already
 * `unite`d, so each real array holds one element: the shape of all of them.
 */
function drift(fake, real, path, found, unverified) {
  if (fake === null || real === null) return;
  if (kind(fake) !== kind(real)) {
    found.push(`${path}: fake ${kind(fake)}, real ${kind(real)}`);
    return;
  }
  if (Array.isArray(fake)) {
    if (!fake.length) return;
    if (!real.length) {
      unverified.push(path);
      return;
    }
    fake.forEach((item, index) => drift(item, real[0], `${path}[${index}]`, found, unverified));
    return;
  }
  if (kind(fake) !== "object") return;
  const map = MAPS.has(path.split(".").pop());
  for (const [key, value] of Object.entries(fake)) {
    const at = `${path}.${key}`;
    if (map) {
      drift(value, unite(Object.values(real)), at, found, unverified);
    } else if (!(key in real)) {
      found.push(`${at}: fake key not in real`);
    } else {
      drift(value, real[key], at, found, unverified);
    }
  }
}

/**
 * The calls the two faked specs make, in an order that leaves each answer
 * worth comparing: changes wait before they are read and saved, and a save
 * comes before the versions are read again. `on` is the real workflow the
 * call is made on: `seed` (the seeded LoRA workflow), `drawn` (the seeded
 * workflow that has pictures) or `own` (one this spec stored, with no LoRA
 * loader, for writes).
 * `fakes` names the fakes that answer it; `before` readies a fake's world.
 */
const CALLS = [
  { label: "GET /workflows", method: "GET", path: () => "/workflows", fakes: ["tab", "popup"] },
  { label: "GET /workflows/{id}", method: "GET", path: (id) => wf(id), fakes: ["tab", "popup"] },
  { label: "GET /workflows/{id}/lora-chain", path: (id) => `${wf(id)}/lora-chain`, fakes: ["tab", "popup"] },
  {
    label: "GET /workflows/{id}/lora-summary",
    path: (id) => `${wf(id)}/lora-summary`,
    on: "drawn",
    fakes: ["tab", "popup"],
  },
  { label: "GET /workflows/{id}/form-inputs", path: (id) => `${wf(id)}/form-inputs`, fakes: ["tab", "popup"] },
  {
    label: "GET /workflows/{id}/model-swap",
    path: (id) => `${wf(id)}/model-swap?slot_kind=checkpoint`,
    fakes: ["tab", "popup"],
  },
  { label: "GET /workflows/{id}/changes", path: (id) => `${wf(id)}/changes`, fakes: ["tab", "popup"] },
  { label: "GET /workflows/{id}/versions", path: (id) => `${wf(id)}/versions`, fakes: ["tab", "popup"] },
  {
    // On a graph with no LoRA loader: a filename LoRA slot is resolved
    // against ComfyUI's list, and there is no ComfyUI here.
    label: "POST /workflows/run/preflight",
    method: "POST",
    path: () => "/workflows/run/preflight",
    body: (id) => ({ workflow_id: id, values: [] }),
    on: "own",
    fakes: ["tab", "popup"],
  },
  {
    label: "POST /workflows/{id}/set-clone-plans",
    method: "POST",
    path: (id) => `${wf(id)}/set-clone-plans`,
    body: (id, ctx) => ({ sets: [{ key: "a", checkpoint_ids: [ctx.checkpoint], model_ids: [] }] }),
    fakes: ["tab"],
  },
  { label: "GET /models/workflow-sets", path: () => "/models/workflow-sets", fakes: ["tab"] },
  { label: "GET /adapters", path: () => "/adapters", fakes: ["tab", "popup"] },
  { label: "GET /recipes", path: () => "/recipes", fakes: ["tab", "popup"] },
  { label: "GET /characters", path: () => "/characters", fakes: ["popup"] },
  // Writes, on a workflow of this spec's own.
  {
    // The checkpoint repaired from the shelf: a stage alone is stored as a
    // default and makes no version, and the save below must make one.
    label: "PUT /workflows/{id}/changes",
    method: "PUT",
    path: (id) => `${wf(id)}/changes`,
    body: (id, ctx) => ({
      models: [{ address: ctx.address[id], filename: "RealVisXL_V4.safetensors" }],
    }),
    on: "own",
    fakes: ["tab"],
  },
  {
    label: "GET /workflows/{id}/changes, waiting",
    path: (id) => `${wf(id)}/changes`,
    on: "own",
    fakes: ["tab", "popup"],
  },
  {
    // The tab's fake adds `stale` and closes Save for a set made on a
    // version since left; the real answer always carries `stale`.
    label: "GET /workflows/{id}/changes, stale",
    path: (id) => `${wf(id)}/changes`,
    on: "own",
    fakes: ["tab"],
    before: { tab: (net) => (net.world.stale[TAB_WF] = true) },
  },
  {
    label: "POST /workflows/{id}/save, dry run",
    method: "POST",
    path: (id) => `${wf(id)}/save`,
    body: () => ({ dry_run: true }),
    on: "own",
    fakes: ["popup"],
  },
  {
    label: "POST /workflows/{id}/save",
    method: "POST",
    path: (id) => `${wf(id)}/save`,
    body: () => ({}),
    on: "own",
    fakes: ["tab", "popup"],
  },
  {
    label: "GET /workflows/{id}/versions, after a save",
    path: (id) => `${wf(id)}/versions`,
    on: "own",
    fakes: ["tab", "popup"],
  },
  {
    label: "POST /workflows/{id}/versions/1/restore",
    method: "POST",
    path: (id) => `${wf(id)}/versions/1/restore`,
    on: "own",
    fakes: ["tab"],
  },
  {
    label: "POST /workflows/{id}/versions/1/opening",
    method: "POST",
    path: (id) => `${wf(id)}/versions/1/opening`,
    on: "own",
    fakes: ["tab"],
  },
  {
    label: "POST /workflows/{id}/duplicate",
    method: "POST",
    path: (id) => `${wf(id)}/duplicate`,
    on: "own",
    fakes: ["tab"],
    made: true,
  },
  {
    label: "PUT /workflows/{id}/changes, again",
    method: "PUT",
    path: (id) => `${wf(id)}/changes`,
    body: () => ({ stages: { upscale: false } }),
    on: "own",
    fakes: ["tab"],
  },
  {
    label: "POST /workflows/{id}/save-as-new",
    method: "POST",
    path: (id) => `${wf(id)}/save-as-new`,
    body: (id, ctx) => ({ name: `Contract guard new ${ctx.tag}` }),
    on: "own",
    fakes: ["tab", "popup"],
    made: true,
  },
  {
    label: "DELETE /workflows/{id}/changes",
    method: "DELETE",
    path: (id) => `${wf(id)}/changes`,
    on: "own",
    fakes: ["tab"],
  },
];

function wf(id) {
  return `/workflows/${encodeURIComponent(id)}`;
}

/** Ask the fake on *page*, through the page's own fetch, which its routes answer. */
function askFake(page, method, path, body) {
  return page.evaluate(
    async ([m, p, b]) => {
      const res = await fetch(`/api/v1${p}`, {
        method: m,
        headers: b === undefined ? {} : { "content-type": "application/json" },
        body: b === undefined ? undefined : JSON.stringify(b),
      });
      return { status: res.status, body: await res.json() };
    },
    [method, path, body],
  );
}

test.afterEach(async ({ apiContext }) => {
  await removeAll(apiContext);
});

test("the Workflows fakes serve nothing the real server does not", async ({
  page,
  context,
  apiContext,
}) => {
  // The real answers first: the same calls, on real workflows.
  const { cards } = await listWorkflows(apiContext);
  const drawn = cards.find((c) => !c.manual && c.picture_count > 0);
  expect(drawn, "a seeded workflow with pictures").toBeTruthy();
  const sets = await ok(await apiContext.get(`${API}/models/workflow-sets`), "list the sets");
  const checkpoint = sets.combinations
    .flatMap((combination) => combination.models)
    .find((model) => model.kind === "checkpoint")?.id;
  expect(checkpoint, "a checkpoint on the e2e shelf").toBeTruthy();
  const ctx = { checkpoint, tag: tag(), address: {} };
  const on = {
    seed: await cardId(apiContext, SEEDED_LORA),
    drawn: drawn.id,
    own: await store(apiContext, `Contract guard ${ctx.tag}`),
  };
  // The checkpoint loader each side repairs: the fake's own, and the stored
  // graph's, as its card addresses it.
  const own = await ok(await apiContext.get(workflowPath(on.own)), "read its own workflow");
  ctx.address[on.own] = own.card.default_recipe.models.find((m) => m.kind === "checkpoint").address;
  ctx.address[TAB_WF] = CKPT;
  const real = {};
  for (const call of CALLS) {
    const id = on[call.on ?? "seed"];
    const res = await apiContext.fetch(`${API}${call.path(id)}`, {
      method: call.method ?? "GET",
      data: call.body?.(id, ctx),
    });
    real[call.label] = await ok(res, `real ${call.label}`);
    if (call.made) {
      const body = real[call.label];
      await apiContext.delete(workflowPath(body.workflow_id));
    }
  }

  // Then each fake, on a page of its own: both route every `/api/v1/` call.
  const fakes = {
    tab: { page, install: (p) => stubWorkflows(p), id: TAB_WF },
    popup: { page: await context.newPage(), install: (p) => stubWorkflow(p), id: POPUP_WF },
  };
  const found = [];
  const unverified = [];
  for (const [name, fake] of Object.entries(fakes)) {
    const net = await fake.install(fake.page);
    // Any page of the app's origin, for the fetches to be same-origin.
    await fake.page.goto("/version");
    for (const call of CALLS.filter((c) => c.fakes.includes(name))) {
      call.before?.[name]?.(net);
      const answer = await askFake(
        fake.page,
        call.method ?? "GET",
        call.path(fake.id),
        call.body?.(fake.id, ctx),
      );
      expect(answer.status, `${name} fake, ${call.label}: ${JSON.stringify(answer.body)}`).toBeLessThan(400);
      const before = unverified.length;
      drift(answer.body, unite([real[call.label]]), `${name} ${call.label} `, found, unverified);
      for (const path of unverified.slice(before)) {
        test.info().annotations.push({ type: "unverified", description: path });
      }
    }
    expect(net.unstubbed, `${name} fake: calls it did not answer`).toEqual([]);
  }

  const vacuous = MUST_VERIFY.filter((path) =>
    unverified.some((at) => at.replace(/^\w+ /, "") === path),
  );
  expect(vacuous, "real answers that were empty where the fakes need them checked").toEqual([]);
  expect(found, "fake keys the real server does not serve").toEqual([]);
});
