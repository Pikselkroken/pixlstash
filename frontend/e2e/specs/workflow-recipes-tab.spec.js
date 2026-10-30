import { readFileSync } from "node:fs";

import { test, expect } from "../fixtures/test.js";

// The Workflows view's Recipes tab against the real `/recipes*` routes
// (#2316). Every other Workflows spec fakes `/recipes` as `[]`, so this is the
// first time the tab draws real rows and its writes reach a real database.
//
// Nothing is stubbed. Each test stores its own workflow through the import
// route and saves its recipes on it through `POST /recipes`, so the rows it
// asserts on are its own whatever else the shared backend has listed by then:
// other specs' workflows, and on a developer machine whatever the shared
// workflow inbox holds (#2313).
//
// Not here: the lightbox's "Matches your saved recipe X" banner (#1480). It
// needs a picture made in ComfyUI, the fixture library has none, and the e2e
// backend cannot import one (the import route needs the face worker, which is
// off). It waits on the seeded pictures of #2314.

const API = "/api/v1";

/**
 * A small SDXL text-to-image graph in ComfyUI's API format, on the stub
 * checkpoint the launcher's model folder holds. Enough for a run to be planned
 * (extract builds on the planner) and for the Run popup to find a prompt;
 * nothing here ever reaches a ComfyUI.
 *
 * No LoRA loader: without a ComfyUI to list its LoRAs the planner refuses to
 * fill one, and extract with it.
 */
const GRAPH = {
  1: {
    class_type: "CheckpointLoaderSimple",
    inputs: { ckpt_name: "RealVisXL_V4.safetensors" },
  },
  3: {
    class_type: "CLIPTextEncode",
    inputs: { text: "a lighthouse at dusk", clip: ["1", 1] },
  },
  4: {
    class_type: "CLIPTextEncode",
    inputs: { text: "blurry", clip: ["1", 1] },
  },
  5: {
    class_type: "EmptyLatentImage",
    inputs: { width: 832, height: 1216, batch_size: 1 },
  },
  6: {
    class_type: "KSampler",
    inputs: {
      seed: 1,
      steps: 28,
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
  8: {
    class_type: "SaveImage",
    inputs: { filename_prefix: "e2e", images: ["7", 0] },
  },
};

/** Distinct per test, so a name never matches a row another test left. */
let serial = 0;
function tag() {
  serial += 1;
  return `${Date.now().toString(36)}-${serial}`;
}

async function ok(response, what) {
  expect(response.ok(), `${what}: ${response.status()} ${await response.text()}`).toBe(true);
  return response.json();
}

/** Store a manual workflow through the import route; its id. */
async function storeWorkflow(api, name) {
  const body = await ok(
    await api.post(`${API}/comfyui/workflows/import`, {
      data: { name, workflow: GRAPH },
    }),
    "import the workflow",
  );
  return body.workflow_id;
}

/** Save a recipe on *workflowId* through the real route; the stored row. */
async function saveRecipe(api, workflowId, fields) {
  return ok(
    await api.post(`${API}/recipes`, { data: { workflow_id: workflowId, ...fields } }),
    `save recipe ${fields.name}`,
  );
}

/** The workflow's recipes as the server orders them. */
async function recipesOf(api, workflowId) {
  return ok(
    await api.get(`${API}/recipes`, { params: { workflow_id: workflowId } }),
    "list the recipes",
  );
}

/** The ids of the library's pictures, outside the Scrapheap. */
async function pictureIds(api) {
  const rows = await ok(
    await api.get(`${API}/pictures`, { params: { fields: "grid" } }),
    "list pictures",
  );
  expect(rows.length, "the fixture library has pictures").toBeGreaterThan(0);
  return rows.map((row) => row.id);
}

/**
 * A workflow of its own with three recipes on it, A, B and C in that order.
 * A carries a source picture, so delete has a picture to leave alone.
 */
async function seed(api) {
  const t = tag();
  const workflowName = `Recipes e2e ${t}`;
  const workflowId = await storeWorkflow(api, workflowName);
  const [pictureId] = await pictureIds(api);
  const names = ["A", "B", "C"].map((letter) => `Recipe ${letter} ${t}`);
  const recipes = [];
  for (const [i, name] of names.entries()) {
    recipes.push(
      await saveRecipe(api, workflowId, {
        name,
        prompt: `a lighthouse at dusk, take ${i + 1} ${t}`,
        ...(i === 0 ? { source_picture_id: pictureId } : {}),
      }),
    );
  }
  return { workflowId, workflowName, pictureId, names, recipes };
}

// ── What is on the page ─────────────────────────────────────────────────────

const rail = (page) => page.getByRole("complementary", { name: "Inspector" });
const recipesTab = (page) => rail(page).getByRole("button", { name: "Recipes", exact: true });
/** The Saved group's names, top to bottom. */
const savedNames = (page) => rail(page).locator(".wfrt-card .wfrt-name");
const card = (scope, name) => scope.locator(".wfrt-card").filter({ hasText: name });
const confirmDialog = (page) => page.locator(".app-dialog").filter({ hasText: "Delete this recipe?" });
const notices = (page) => page.locator(".notice-message");

/** The deep link a saved recipe's banner sends the owner down (#1480). */
async function openRecipes(page, workflowId, firstName) {
  await page.goto(`/workflows?workflow=${encodeURIComponent(workflowId)}&tab=recipes`);
  await expect(rail(page)).toBeVisible({ timeout: 15_000 });
  await expect(recipesTab(page)).toHaveAttribute("aria-pressed", "true");
  if (firstName) await expect(savedNames(page).first()).toBeVisible();
}

/** Open a row's ⋯ menu and press *item*. */
async function menu(page, scope, name, item) {
  await card(scope, name).getByRole("button", { name: `More for ${name}` }).click();
  await page.getByRole("menuitem", { name: item }).click();
}

// ── The tests ───────────────────────────────────────────────────────────────

test("the deep link opens the rail on the Recipes tab, the saved rows in the server's order", async ({
  page,
  apiContext,
}) => {
  const { workflowId, names } = await seed(apiContext);
  await openRecipes(page, workflowId, names[0]);

  await expect(page.locator(`.wfv-row[data-key="${workflowId}"]`)).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(rail(page).getByRole("button", { name: "Workflow", exact: true })).toHaveAttribute(
    "aria-pressed",
    "false",
  );
  await expect(savedNames(page)).toHaveText(names);
});

test("Delete removes the recipe for good and leaves the workflow and the picture", async ({
  page,
  apiContext,
}) => {
  const { workflowId, workflowName, pictureId, names } = await seed(apiContext);
  await openRecipes(page, workflowId, names[0]);

  await menu(page, rail(page), names[0], "Delete");
  await expect(confirmDialog(page)).toContainText(names[0]);
  await confirmDialog(page).getByRole("button", { name: "Delete", exact: true }).click();
  await expect(savedNames(page)).toHaveText(names.slice(1));

  await page.reload();
  await openRecipes(page, workflowId, names[1]);
  await expect(savedNames(page)).toHaveText(names.slice(1));

  const left = await recipesOf(apiContext, workflowId);
  expect(left.map((row) => row.name)).toEqual(names.slice(1));
  // The workflow is still there, under its name...
  const listed = await ok(await apiContext.get(`${API}/workflows`), "list workflows");
  expect(listed.cards.find((c) => c.id === workflowId)?.name).toBe(workflowName);
  // ...and so is the picture the deleted recipe was saved from.
  expect(await pictureIds(apiContext)).toContain(pictureId);
});

test("Rename writes the new name, and it survives a reload", async ({ page, apiContext }) => {
  const { workflowId, names, recipes } = await seed(apiContext);
  const renamed = `${names[1]} renamed`;
  await openRecipes(page, workflowId, names[0]);

  await menu(page, rail(page), names[1], "Rename");
  const field = rail(page).getByRole("textbox", { name: "Name for this recipe" });
  await expect(field).toBeFocused();
  await field.fill(renamed);
  await field.press("Enter");
  await expect(savedNames(page).nth(1)).toHaveText(renamed);

  await page.reload();
  await openRecipes(page, workflowId, names[0]);
  await expect(savedNames(page)).toHaveText([names[0], renamed, names[2]]);
  const row = (await recipesOf(apiContext, workflowId)).find((r) => r.id === recipes[1].id);
  expect(row.name).toBe(renamed);
});

test("dragging a recipe by its handle reorders it, through a real dragstart and drop", async ({
  page,
  apiContext,
}) => {
  const { workflowId, names } = await seed(apiContext);
  await openRecipes(page, workflowId, names[0]);

  // Native HTML5 drag is new to this suite, so the events themselves are
  // recorded: an order that changed some other way must not pass for a drag.
  await page.evaluate(() => {
    window.__recipeDrag = [];
    for (const type of ["dragstart", "drop"]) {
      document.addEventListener(type, () => window.__recipeDrag.push(type), true);
    }
  });
  // The drop handler is what writes the order: without it no PUT is sent.
  const order = page.waitForResponse(
    (r) => r.url().endsWith(`${API}/recipes/order`) && r.request().method() === "PUT",
    { timeout: 10_000 },
  );
  await card(rail(page), names[0])
    .getByRole("button", { name: `Reorder ${names[0]}`, exact: false })
    .dragTo(card(rail(page), names[2]));
  expect((await order).ok()).toBe(true);
  expect(await page.evaluate(() => window.__recipeDrag)).toEqual(["dragstart", "drop"]);

  const moved = [names[1], names[2], names[0]];
  await expect(savedNames(page)).toHaveText(moved);
  await page.reload();
  await openRecipes(page, workflowId, names[1]);
  await expect(savedNames(page)).toHaveText(moved);
  expect((await recipesOf(apiContext, workflowId)).map((r) => r.name)).toEqual(moved);
});

test("Extract workflow stores a new workflow and selects its card", async ({ page, apiContext }) => {
  const { workflowId, names } = await seed(apiContext);
  await openRecipes(page, workflowId, names[0]);

  const extracted = page.waitForResponse(
    (r) => /\/recipes\/\d+\/extract-workflow$/.test(r.url()) && r.request().method() === "POST",
  );
  await menu(page, rail(page), names[2], "Extract workflow");
  const body = await ok(await extracted, "extract the workflow");
  expect(body.name).toBe(names[2]);
  expect(body.workflow_id).not.toBe(workflowId);

  await expect(notices(page).filter({ hasText: `Extracted “${names[2]}”` })).toBeVisible();
  await expect
    .poll(() => new URL(page.url()).searchParams.get("workflow"))
    .toBe(body.workflow_id);
  const newCard = page.locator(`.wfv-row[data-key="${body.workflow_id}"]`);
  await expect(newCard).toHaveAttribute("aria-selected", "true");
  await expect(newCard).toContainText(names[2]);
  await expect(page.locator(`.wfv-row[data-key="${workflowId}"]`)).toHaveAttribute(
    "aria-selected",
    "false",
  );
});

test("Run… applies the recipe: the popup opens on it, with its prompt in the box", async ({
  page,
  apiContext,
}) => {
  const { workflowId, names, recipes } = await seed(apiContext);
  await openRecipes(page, workflowId, names[0]);

  await card(rail(page), names[1]).getByRole("button", { name: "Run…" }).click();
  const dialog = page.locator(".app-dialog").filter({ hasText: "Run workflow" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("Saved recipe");
  await expect(dialog.locator(".app-textarea textarea").first()).toHaveValue(recipes[1].prompt);
});

test("Export… downloads the recipe as a file", async ({ page, apiContext }) => {
  const { workflowId, names, recipes } = await seed(apiContext);
  await openRecipes(page, workflowId, names[0]);

  await menu(page, rail(page), names[0], "Export…");
  const dialog = page.locator(".app-dialog").filter({ hasText: "Export recipe" });
  // The consent list is the server's own `shares`: the prompt is one of them.
  await expect(dialog.locator(".exr-share").first()).toBeVisible();
  const download = page.waitForEvent("download");
  await dialog.getByRole("button", { name: "Export recipe" }).click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/\.json$/);
  const saved = JSON.parse(readFileSync(await file.path(), "utf8"));
  expect(saved.name).toBe(names[0]);
  expect(saved.prompt).toBe(recipes[0].prompt);
  expect(saved.workflow_id).toBe(workflowId);
  await expect(dialog).toBeHidden();
});

test("an unfiled recipe is listed once its workflow is deleted, and can still be renamed", async ({
  page,
  apiContext,
}) => {
  // Deleting a workflow writes its document into the SHARED workflow inbox and
  // sends it to the OS trash from there (#2313): on a developer machine that is
  // the owner's own inbox and trash. A CI runner is thrown away after the job.
  test.skip(!process.env.CI, "deletes a workflow through the shared inbox until #2313");

  const { workflowId, names, recipes } = await seed(apiContext);
  await ok(await apiContext.delete(`${API}/workflows/${encodeURIComponent(workflowId)}`), "delete the workflow");

  await page.goto("/workflows");
  const unfiled = page.locator(".wfv-unfiled");
  await expect(unfiled.locator(".wfrt-name").filter({ hasText: names[0] })).toBeVisible({
    timeout: 15_000,
  });
  for (const name of names) await expect(card(unfiled, name)).toBeVisible();

  // Still usable: a rename lands on the server.
  const renamed = `${names[0]} kept`;
  await menu(page, unfiled, names[0], "Rename");
  const field = unfiled.getByRole("textbox", { name: "Name for this recipe" });
  await field.fill(renamed);
  await field.press("Enter");
  await expect(card(unfiled, renamed)).toBeVisible();
  await page.reload();
  await expect(card(page.locator(".wfv-unfiled"), renamed)).toBeVisible({ timeout: 15_000 });

  const rows = await ok(
    await apiContext.get(`${API}/recipes`, { params: { unfiled: "true" } }),
    "list the unfiled recipes",
  );
  const row = rows.find((r) => r.id === recipes[0].id);
  expect(row?.name).toBe(renamed);
  // A deleted manual workflow takes the graph its recipes were saved from
  // with it (#1687), so the row says Extract is refused rather than failing.
  expect(row.extractable).toBe(false);
  await card(page.locator(".wfv-unfiled"), renamed)
    .getByRole("button", { name: `More for ${renamed}` })
    .click();
  await expect(page.getByTestId("wfrt-extract")).toHaveAttribute("aria-disabled", "true");
});
