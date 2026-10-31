import { test, expect } from "../fixtures/test.js";
import { ok, tag } from "../fixtures/workflowsApi.js";
import { WorkflowsPage } from "../pages/WorkflowsPage.js";

// A run with people's LoRAs, from the Run popup, against the real backend and
// the fake ComfyUI (#2362). `workflow-run.spec.js` (#2339) drives the same
// popup over route fakes and checks only what the browser sends; here nothing
// on the run routes is faked, so what is checked is the graph the server
// builds and hands to ComfyUI (`GET /__fake/prompts`) and where the server
// files what comes back.
//
// The LoRAs are the two the launcher's model folder seeds. Each test makes the
// workflow and people it needs (and a set, where it names one), gives the
// seeded models a base model, attaches the LoRAs to its people, and puts all of
// it back afterwards with the pictures its runs made and their Tasks rows: the
// backend and the fake are shared by every spec in the run.

const API = "/api/v1";
const FAKE = process.env.PIXLSTASH_E2E_COMFYUI_URL;

const PROMPT = "a lighthouse at dusk";
/** The seeded shelf's LoRAs, by file name, and the trigger word each is given. */
const LORAS = [
  { filename: "Clementine_Portrait.safetensors", word: "clmtn woman" },
  { filename: "DetailTweaker_v2.safetensors", word: "dtwk man" },
];
/** Text-to-image with no LoRA of its own, on the seeded checkpoint. */
const PLAIN_GRAPH = {
  1: { class_type: "CheckpointLoaderSimple", inputs: { ckpt_name: "RealVisXL_V4.safetensors" } },
  3: { class_type: "CLIPTextEncode", inputs: { text: PROMPT, clip: ["1", 1] } },
  4: { class_type: "CLIPTextEncode", inputs: { text: "blurry", clip: ["1", 1] } },
  5: { class_type: "EmptyLatentImage", inputs: { width: 512, height: 512, batch_size: 1 } },
  6: {
    class_type: "KSampler",
    inputs: {
      seed: 1,
      steps: 20,
      cfg: 5,
      sampler_name: "euler",
      scheduler: "normal",
      denoise: 1,
      model: ["1", 0],
      positive: ["3", 0],
      negative: ["4", 0],
      latent_image: ["5", 0],
    },
  },
  7: { class_type: "VAEDecode", inputs: { samples: ["6", 0], vae: ["1", 2] } },
  8: { class_type: "SaveImage", inputs: { filename_prefix: "e2e-lora", images: ["7", 0] } },
};
/**
 * The same, with the negative prompt encoded by a second copy of the
 * checkpoint: two text encoders for one model, so a LoRA added for a person has
 * no one CLIP to sit in front of, and the server will not guess.
 */
const TWO_CLIPS_GRAPH = {
  ...PLAIN_GRAPH,
  9: { class_type: "CheckpointLoaderSimple", inputs: { ckpt_name: "RealVisXL_V4.safetensors" } },
  4: { class_type: "CLIPTextEncode", inputs: { text: "blurry", clip: ["9", 1] } },
};
/** The base model the shelf rows are given, so the popup can match people to the workflow. */
const BASE_MODEL = "SDXL";

const made = { workflows: [], people: [], sets: [], runs: [], pictures: [] };
/** The shelf rows of `LORAS`, as the shelf had them before the test: put back after. */
let shelf = [];
/** The seeded checkpoint's shelf row, likewise. */
let checkpoint = null;

const dialog = (page) => page.locator(".app-dialog").filter({ hasText: "Run workflow" });
const footer = (page) => dialog(page).locator("footer");
const runButton = (page) => footer(page).getByRole("button", { name: /^Run/ });
const promptBox = (page) => dialog(page).locator(".app-textarea textarea").first();
const tile = (page, name) =>
  dialog(page).getByRole("group", { name: "Person" }).getByRole("checkbox", { name });
const tasksPanel = (page) => page.getByTestId("titlebar-tasks-panel");

test.beforeEach(async ({ apiContext }) => {
  const { adapters } = await ok(
    await apiContext.get(`${API}/adapters`, { params: { file_kind: "adapter" } }),
    "list the shelf's LoRAs",
  );
  shelf = LORAS.map(({ filename }) => {
    const row = adapters.find((adapter) => adapter.filename.split("/").pop() === filename);
    expect(row, `the seeded shelf has ${filename}`).toBeTruthy();
    return row;
  });
  const { checkpoints } = await ok(await apiContext.get(`${API}/checkpoints`), "list checkpoints");
  checkpoint = checkpoints.find(
    (row) => row.filename.split("/").pop() === PLAIN_GRAPH[1].inputs.ckpt_name,
  );
  expect(checkpoint, "the seeded shelf has its checkpoint").toBeTruthy();
  // A person's LoRA is offered only for a workflow of its own base model.
  await ok(
    await apiContext.patch(`${API}/models`, {
      data: { ids: [checkpoint.id, ...shelf.map((row) => row.id)], base_model: BASE_MODEL },
    }),
    "give the seeded models a base model",
  );
});

test.afterEach(async ({ apiContext }) => {
  // Every step is tried whatever failed before it, so one failure does not
  // leave the rest behind for the specs after this one; the first is thrown.
  const failures = [];
  const attempt = async (what, step) => {
    try {
      await step();
    } catch (error) {
      console.error(`clean-up: ${what} failed`, error);
      failures.push(error);
    }
  };
  // The shelf rows first: an attachment names a person deleted below.
  for (const row of [checkpoint, ...shelf.splice(0)].filter(Boolean)) {
    await attempt(`base model of ${row.filename}`, async () =>
      ok(
        await apiContext.patch(`${API}/models`, {
          data: { ids: [row.id], base_model: row.base_model ?? null },
        }),
        `put back ${row.filename}'s base model`,
      ),
    );
    if (row === checkpoint) continue;
    await attempt(`attachments of ${row.filename}`, async () =>
      ok(
        await apiContext.put(`${API}/adapters/${row.sha256}/attachments`, {
          data: row.attachments.map(({ entity_type, entity_id }) => ({ entity_type, entity_id })),
        }),
        `put back ${row.filename}'s attachments`,
      ),
    );
    await attempt(`trigger words of ${row.filename}`, async () =>
      ok(
        await apiContext.patch(`${API}/models`, {
          data: { ids: [row.id], trigger_words: row.trigger_words ?? [] },
        }),
        `put back ${row.filename}'s trigger words`,
      ),
    );
  }
  checkpoint = null;
  // A run's pictures, whether or not the test got as far as reading them, and
  // its row in the Tasks list.
  for (const runId of made.runs.splice(0)) {
    await attempt(`run ${runId}`, async () => {
      await expect
        .poll(
          async () => {
            const outcome = await ok(
              await apiContext.get(`${API}/workflows/runs/${runId}`),
              "run outcome",
            );
            made.pictures.push(...(outcome.picture_ids ?? []));
            return outcome.status;
          },
          { timeout: 20_000 },
        )
        .toMatch(/^(completed|failed)$/);
      await ok(await apiContext.delete(`${API}/workflows/runs/${runId}`), `dismiss ${runId}`);
    });
  }
  const ids = [...new Set(made.pictures.splice(0))];
  if (ids.length) {
    await attempt("the pictures", async () => {
      // Delete forever, and only these: to the Scrapheap, then purged by id.
      for (const id of ids)
        await ok(await apiContext.delete(`${API}/pictures/${id}`), `delete ${id}`);
      const { confirm_token: confirmToken } = await ok(
        await apiContext.post(`${API}/pictures/scrapheap/delete-preview`, {
          data: { picture_ids: ids },
        }),
        "preview the purge",
      );
      await ok(
        await apiContext.delete(`${API}/pictures/scrapheap`, {
          data: { picture_ids: ids, include_protected: false, confirm_token: confirmToken },
        }),
        "purge",
      );
    });
  }
  for (const id of made.sets.splice(0)) {
    await attempt(`set ${id}`, async () =>
      ok(await apiContext.delete(`${API}/picture_sets/${id}`), `delete set ${id}`),
    );
  }
  for (const id of made.people.splice(0)) {
    await attempt(`person ${id}`, async () =>
      ok(await apiContext.delete(`${API}/characters/${id}`), `delete person ${id}`),
    );
  }
  for (const id of made.workflows.splice(0)) {
    await attempt(`workflow ${id}`, async () =>
      ok(
        await apiContext.delete(`${API}/workflows/${encodeURIComponent(id)}`),
        `delete workflow ${id}`,
      ),
    );
  }
  if (failures.length) throw failures[0];
});

/** Store *graph* as a workflow of its own: `{id, name}`. */
async function storeWorkflow(api, graph) {
  const name = `LoRA run e2e ${tag()}`;
  const { workflow_id: id } = await ok(
    await api.post(`${API}/comfyui/workflows/import`, { data: { name, workflow: graph } }),
    "import the workflow",
  );
  made.workflows.push(id);
  return { id, name };
}

/**
 * A person per seeded LoRA, the LoRA attached to them and given its trigger
 * word, through the API: `[{id, name, lora, word}]`.
 */
async function seedPeople(api, count) {
  const people = [];
  for (const [index, row] of shelf.slice(0, count).entries()) {
    const name = `LoRA person ${tag()}`;
    const { character } = await ok(
      await api.post(`${API}/characters`, { data: { name } }),
      "make a person",
    );
    made.people.push(character.id);
    await ok(
      await api.put(`${API}/adapters/${row.sha256}/attachments`, {
        data: [{ entity_type: "character", entity_id: character.id }],
      }),
      `attach ${row.filename}`,
    );
    await ok(
      await api.patch(`${API}/models`, {
        data: { ids: [row.id], trigger_words: [LORAS[index].word] },
      }),
      `give ${row.filename} a trigger word`,
    );
    people.push({ id: character.id, name, lora: row, word: LORAS[index].word });
  }
  return people;
}

/** Select the workflow's card and press the inspector's Run…. */
async function openRun(page, workflowId) {
  const workflows = new WorkflowsPage(page);
  await workflows.goto();
  // Not `openInspector`: a graph with two checkpoint loaders draws two rows.
  await workflows.card(workflowId).click();
  await expect(workflows.rail.getByTestId("wftab-row-checkpoint").first()).toBeVisible();
  await workflows.rail.getByRole("button", { name: /^Run( with changes)?…$/ }).click();
  await expect(dialog(page)).toBeVisible();
  await expect(page.locator(".v-overlay__content").filter({ has: dialog(page) })).not.toHaveClass(
    /dialog-transition/,
  );
  await expect(promptBox(page)).toHaveValue(new RegExp(PROMPT));
}

/**
 * Pick each person's tile, set their LoRA's strength to *strengths*, and add
 * the trigger words the popup says are missing. Returns the strengths and the
 * prompt as the popup shows them once it is done.
 */
async function pickPeople(page, people, strengths) {
  const shown = [];
  for (const [index, person] of people.entries()) {
    await tile(page, person.name).click();
    await expect(tile(page, person.name)).toBeChecked();
    const strength = dialog(page)
      .getByRole("spinbutton", { name: /^Strength of / })
      .nth(index);
    await strength.fill(String(strengths[index]));
    await strength.blur();
    shown.push(Number(await strength.inputValue()));
  }
  const line = dialog(page).getByTestId("rund-trigger");
  await expect(line).toBeVisible();
  // A button per missing word: each press puts one in and takes its button away.
  for (let left = people.length; left > 0; left -= 1) {
    await expect(line.locator(".rund-trigger-add")).toHaveCount(left);
    await line.locator(".rund-trigger-add").first().click();
  }
  await expect(line).toHaveCount(0);
  const prompt = await promptBox(page).inputValue();
  for (const person of people) expect(prompt, "the popup put the word in").toContain(person.word);
  return { strengths: shown, prompt };
}

/** Press Run, and read the run's answer off the wire: `{prompt_id, run_id}` per prompt. */
async function run(page) {
  const answer = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === `${API}/workflows/run`,
  );
  await runButton(page).click();
  const response = await answer;
  const body = await response.json();
  expect(response.ok(), JSON.stringify(body)).toBe(true);
  made.runs.push(...(body.prompts ?? []).map((entry) => entry.run_id).filter(Boolean));
  expect(body.status, JSON.stringify(body)).toBe("success");
  await expect(dialog(page)).toHaveCount(0);
  return body.prompts;
}

/** The graph the fake ComfyUI was sent for *promptId*. */
async function sentGraph(api, promptId) {
  const sent = await ok(await api.get(`${FAKE}/__fake/prompts`), "read the fake");
  const graph = sent.find((entry) => entry.prompt_id === promptId)?.prompt;
  expect(graph, "the fake recorded this run").toBeTruthy();
  return graph;
}

/** The run's outcome once it has ended; its pictures recorded for clean-up. */
async function finished(api, runId) {
  let outcome;
  await expect
    .poll(
      async () => {
        outcome = await ok(await api.get(`${API}/workflows/runs/${runId}`), "run outcome");
        return outcome.status;
      },
      { timeout: 20_000 },
    )
    .toBe("completed");
  made.pictures.push(...outcome.picture_ids);
  return outcome;
}

/** The loaders in *graph* that load *filename*, by any of the loader classes. */
function loadersOf(graph, filename) {
  return Object.values(graph).filter(
    (node) => /Lora/i.test(node.class_type) && node.inputs?.lora_name === filename,
  );
}

/** The text the sampler's positive input reads, followed back through the graph. */
function positiveText(graph) {
  const sampler = Object.values(graph).find((node) => node.class_type === "KSampler");
  return graph[sampler.inputs.positive[0]].inputs.text;
}

test("a person's LoRA reaches the graph at the strength the popup showed, and the picture is filed under them", async ({
  page,
  apiContext,
}) => {
  const { id: workflowId, name: workflowName } = await storeWorkflow(apiContext, PLAIN_GRAPH);
  const [person] = await seedPeople(apiContext, 1);
  const { picture_set: set } = await ok(
    await apiContext.post(`${API}/picture_sets`, { data: { name: `LoRA run set ${tag()}` } }),
    "make a set",
  );
  made.sets.push(set.id);

  await openRun(page, workflowId);
  const { strengths, prompt } = await pickPeople(page, [person], [0.7]);
  expect(strengths).toEqual([0.7]);
  await dialog(page)
    .getByRole("combobox", { name: "Results go to" })
    .selectOption({ label: set.name });
  const [{ prompt_id: promptId, run_id: runId }] = await run(page);

  // 1. The graph ComfyUI was sent loads the person's LoRA, at that strength,
  // and its prompt is the popup's, trigger word and all.
  const graph = await sentGraph(apiContext, promptId);
  const loaders = loadersOf(graph, person.lora.filename);
  expect(loaders, JSON.stringify(graph)).toHaveLength(1);
  expect(loaders[0].inputs.strength_model).toBe(0.7);
  expect(positiveText(graph)).toBe(prompt);
  // And the sampler reads the model through that loader, not around it.
  const sampler = Object.values(graph).find((node) => node.class_type === "KSampler");
  expect(graph[sampler.inputs.model[0]]).toBe(loaders[0]);

  // 3. The picture is filed under the person, in the set, with the run on it.
  const outcome = await finished(apiContext, runId);
  expect(outcome.picture_ids, JSON.stringify(outcome)).toHaveLength(1);
  const [pictureId] = outcome.picture_ids;
  // A fresh picture has no faces, so the person waits on it until face
  // extraction names its largest one; the e2e backend loads no face model.
  const pending = await ok(
    await apiContext.get(`${API}/pictures/${pictureId}/pending_character_id`),
    "read the picture's person",
  );
  expect(pending.pending_character_id).toBe(person.id);
  const members = await ok(
    await apiContext.get(`${API}/picture_sets/${set.id}/members`),
    "read the set",
  );
  expect(members.picture_ids).toContain(pictureId);
  const recipe = await ok(
    await apiContext.get(`${API}/comfyui/pictures/${pictureId}/recipe`, {
      params: { preflight: false },
    }),
    "read the recipe",
  );
  expect(recipe.workflow_id, JSON.stringify(recipe)).toBe(workflowId);
  expect(recipe.loras).toEqual([person.lora.filename]);
  expect(recipe.lora_slots.map((slot) => slot.strengths?.model)).toEqual([0.7]);
  expect(recipe.positive_prompt).toBe(prompt);

  // 4. The Tasks tab follows the run to the end, and its tile opens the picture.
  await page.getByTestId("titlebar-tasks").click();
  await expect(tasksPanel(page)).toBeVisible();
  // Other specs' runs are listed too: this one is under its workflow's name.
  const done = tasksPanel(page).getByTestId("tm-finished-run").filter({ hasText: workflowName });
  await expect(done).toHaveCount(1);
  await tasksPanel(page)
    .getByRole("button", { name: `Show the run of ${workflowName}` })
    .click();
  const tiles = done.getByTestId("tm-finished-tile");
  await expect(tiles).toHaveCount(1);
  await expect(tiles.first().locator("img")).toHaveAttribute(
    "src",
    new RegExp(`/${pictureId}\\.\\w+(\\?|$)`),
  );
  await tiles.first().click();

  // The lightbox's Recipe tab says what the picture was made with.
  const lightbox = page.locator(".lightbox");
  await expect(lightbox).toBeVisible();
  await expect(lightbox.locator(".lightbox-media-inner img").first()).toHaveAttribute(
    "src",
    new RegExp(`/pictures/${pictureId}(\\.|/|\\?)`),
  );
  // The Tasks list stays open over the lightbox: put it away by its own pill.
  await page.getByTestId("titlebar-tasks").click();
  await expect(tasksPanel(page)).toBeHidden();
  await expect(lightbox).toBeVisible();
  const details = lightbox.getByRole("complementary", { name: "Picture details" });
  await details.getByRole("button", { name: "Recipe" }).click();
  const pane = details.locator(".recipe-pane");
  await expect(pane.getByRole("button", { name: "Open" })).toBeVisible();
  await expect(pane.locator(".recipe-prompt")).toHaveText(prompt);
  const chip = pane.locator(".recipe-chip").filter({ hasText: /Clementine/i });
  await expect(chip).toBeVisible();
  await expect(chip.locator(".recipe-chip-strength")).toContainText("0.7");
});

test("a run with two people loads both LoRAs, each with its trigger word in the prompt", async ({
  page,
  apiContext,
}) => {
  const { id: workflowId } = await storeWorkflow(apiContext, PLAIN_GRAPH);
  const people = await seedPeople(apiContext, 2);

  await openRun(page, workflowId);
  const { strengths, prompt } = await pickPeople(page, people, [0.8, 0.45]);
  expect(strengths).toEqual([0.8, 0.45]);
  const [{ prompt_id: promptId, run_id: runId }] = await run(page);

  // 2. Both LoRAs, each in a loader of its own at its own strength, stacked
  // on the model the sampler reads; both words in the prompt ComfyUI got.
  const graph = await sentGraph(apiContext, promptId);
  for (const [index, person] of people.entries()) {
    const loaders = loadersOf(graph, person.lora.filename);
    expect(loaders, `${person.lora.filename} in ${JSON.stringify(graph)}`).toHaveLength(1);
    expect(loaders[0].inputs.strength_model).toBe(strengths[index]);
  }
  const text = positiveText(graph);
  expect(text).toBe(prompt);
  for (const person of people) expect(text).toContain(person.word);
  const chain = [];
  let node = Object.values(graph).find((n) => n.class_type === "KSampler").inputs.model;
  while (node && /Lora/i.test(graph[node[0]].class_type)) {
    chain.push(graph[node[0]].inputs.lora_name);
    node = graph[node[0]].inputs.model;
  }
  expect(chain.sort()).toEqual(people.map((p) => p.lora.filename).sort());

  // The run ends with its picture. Who it is of is not checked here: with
  // several people it waits on face extraction in a table no route reads
  // (`PicturePendingPerson`), and the e2e backend loads no face model.
  const outcome = await finished(apiContext, runId);
  expect(outcome.picture_ids).toHaveLength(1);
});

test("a person whose LoRA the workflow has no place for is refused by the server's pre-flight, in its words", async ({
  page,
  apiContext,
}) => {
  const { id: workflowId } = await storeWorkflow(apiContext, TWO_CLIPS_GRAPH);
  const [person] = await seedPeople(apiContext, 1);
  // Without the person the same workflow would run: the refusal is the LoRA's.
  const alone = await ok(
    await apiContext.post(`${API}/workflows/run/preflight`, { data: { workflow_id: workflowId } }),
    "pre-flight the workflow alone",
  );
  expect(alone.ok, JSON.stringify(alone)).toBe(true);
  const before = await ok(await apiContext.get(`${FAKE}/__fake/prompts`), "read the fake");

  await openRun(page, workflowId);
  await tile(page, person.name).click();
  await expect(tile(page, person.name)).toBeChecked();
  // Run checks the plan again on the server before anything is queued, and
  // that check is what refuses: the popup has no way to know.
  const answer = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === `${API}/workflows/run`,
  );
  await runButton(page).click();
  const refused = await (await answer).json();
  // Recorded for clean-up in case it ran after all.
  made.runs.push(...(refused.prompts ?? []).map((entry) => entry.run_id).filter(Boolean));
  expect(refused.status, JSON.stringify(refused)).toBe("refused");
  expect(refused.prompts).toEqual([]);
  const reasons = refused.groups.flatMap((group) => group.reasons);
  expect(
    reasons.map((reason) => reason.code),
    JSON.stringify(refused),
  ).toEqual(["lora_not_insertable"]);
  const [{ detail }] = reasons;
  expect(detail).toContain("2 text encoders");

  // The popup stays, says so, and prints the server's sentence whole.
  await expect(footer(page)).toContainText("This run cannot start; see below.");
  await expect(dialog(page)).toContainText(detail);
  await expect(runButton(page)).toHaveAttribute("aria-disabled", "true");
  // And ComfyUI was sent nothing.
  const after = await ok(await apiContext.get(`${FAKE}/__fake/prompts`), "read the fake");
  expect(after.length, "a refused run reached ComfyUI").toBe(before.length);
});
