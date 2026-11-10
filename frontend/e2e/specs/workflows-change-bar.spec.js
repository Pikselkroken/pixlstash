import { test, expect } from "../fixtures/test.js";
import {
  RETIRED_CHECKPOINT,
  checkpointOf,
  listWorkflows,
  made,
  ok,
  removeAll,
  store,
  tag,
  workflowPath,
} from "../fixtures/workflowsApi.js";

// The Workflow tab's change bar against the real `/changes`, `/save`,
// `/save-as-new` and `/versions/{v}/restore` (#2315). The bar's fake-server
// spec (`workflow-tab-editor.spec.js`) holds the layout and the timing; this
// one holds that what the bar says happened, happened on the server.
//
// Each test stores its own workflow (`REPAIR_GRAPH`): an upscale stage, and a
// checkpoint the e2e shelf does not have. Two edits, because they save
// differently: switching the stage off is stored as a default and makes no
// version, and choosing the shelf's checkpoint through Change model set… is a
// repair written into the graph, which does. Where a version must move, the
// graph's checkpoint is the content checked, never the version number alone:
// a restore that bumped the number and kept the graph would pass a number
// check.

const SHELF_CHECKPOINT = "RealVisXL_V4.safetensors";

let id;

test.beforeEach(async ({ apiContext, workflows }) => {
  id = await store(apiContext, `e2e change bar ${tag()}`);
  await workflows.goto();
  await workflows.openInspector(id);
});

test.afterEach(async ({ apiContext }) => {
  await removeAll(apiContext);
});

const bar = (workflows) => workflows.rail.getByRole("group", { name: "Unsaved changes" });
const notice = (page, text) => page.locator(".notice-card").filter({ hasText: text });

async function changes(api, key = id) {
  return ok(await api.get(`${workflowPath(key)}/changes`), "read the changes");
}

async function versions(api) {
  return (await ok(await api.get(`${workflowPath(id)}/versions`), "read the versions")).versions;
}


/** Switch the upscale stage off: one unsaved change. */
async function toggleStage(workflows) {
  await workflows.rail.getByTestId("wftab-stage-upscale").click();
  await expect(workflows.rail.getByTestId("wftab-draft-count")).toHaveText(/1 unsaved change/);
}

/** Fill the checkpoint from the shelf's one set, then Save: a new version. */
async function repairAndSave(page, workflows) {
  await workflows.rail.getByRole("button", { name: "Change model set…" }).click();
  const menu = page.getByTestId("wftab-use-set-menu");
  await menu.locator("[data-set]").filter({ hasText: "RealVisXL V4" }).click();
  await expect(menu).toBeHidden();
  await expect(workflows.rail.getByTestId("wftab-draft-count")).toHaveText(/1 unsaved change/);
  await workflows.rail.getByTestId("wftab-save").click();
  await expect(bar(workflows)).toBeHidden();
}

test("an edit shows Unsaved on the card, and Discard clears it on the server", async ({
  page,
  apiContext,
  workflows,
}) => {
  await toggleStage(workflows);
  await expect(workflows.card(id).getByTestId("wf-card-unsaved")).toHaveText("Unsaved");
  expect((await changes(apiContext)).count).toBe(1);

  await workflows.rail.getByTestId("wftab-discard").click();
  await expect(bar(workflows)).toBeHidden();
  const after = await changes(apiContext);
  expect(after.count).toBe(0);
  expect(after.waiting).toBeNull();
  await expect(workflows.card(id).getByTestId("wf-card-unsaved")).toHaveCount(0);
  await page.reload();
  await expect(workflows.card(id)).toBeVisible({ timeout: 15_000 });
  await expect(workflows.card(id).getByTestId("wf-card-unsaved")).toHaveCount(0);
});

test("Save writes a new version, and the version menu lists it", async ({
  page,
  apiContext,
  workflows,
}) => {
  const before = await versions(apiContext);
  expect(await checkpointOf(apiContext, id)).toBe(RETIRED_CHECKPOINT);

  await repairAndSave(page, workflows);
  const next = before[0].version + 1;
  await expect(notice(page, `Saved as version ${next}.`)).toBeVisible();

  const after = await versions(apiContext);
  expect(after).toHaveLength(before.length + 1);
  expect(after[0]).toMatchObject({ version: next, current: true });
  expect(await checkpointOf(apiContext, id)).toBe(SHELF_CHECKPOINT);
  expect((await changes(apiContext)).count).toBe(0);

  await workflows.rail.getByTestId("wftab-version").click();
  const menu = page.getByTestId("wftab-version-menu");
  await expect(menu.locator(`[data-version="${next}"]`)).toContainText(`Version ${next}`);
  await expect(menu.locator(`[data-version="${next}"]`)).toHaveAttribute("aria-checked", "true");
});

test("Save as new… makes a card with the given name and leaves this one as it was", async ({
  page,
  apiContext,
  workflows,
}) => {
  const name = `e2e-new-${tag()}`;
  await toggleStage(workflows);
  await workflows.rail.getByTestId("wftab-save-as-new").click();
  await workflows.rail.getByTestId("wftab-new-name").fill(name);
  await workflows.rail.getByTestId("wftab-save-as-new-confirm").click();
  await expect(notice(page, `Saved as a new workflow, ${name}.`)).toBeVisible();

  const created = (await listWorkflows(apiContext)).cards.find((c) => c.name === name);
  // Recorded before anything can fail: a card left behind sits on every later
  // Workflows screen.
  if (created) made.push(created.id);
  expect(created, `a card named ${name}`).toBeTruthy();
  await expect(workflows.row(created.id)).toHaveCount(1);
  // The new one has the edit; this one has neither the edit nor what waited.
  const read = async (key) => (await ok(await apiContext.get(workflowPath(key)), key)).card;
  expect((await read(created.id)).default_recipe.stages.upscale).toBe(false);
  expect((await read(id)).default_recipe.stages.upscale).toBe(true);
  expect((await changes(apiContext)).count).toBe(0);
});

test("Go back to an older version moves the head, and its Undo moves it back", async ({
  page,
  apiContext,
  workflows,
}) => {
  const first = (await versions(apiContext))[0].version;
  await repairAndSave(page, workflows);
  await expect(notice(page, `Saved as version ${first + 1}.`)).toBeVisible();
  expect(await checkpointOf(apiContext, id)).toBe(SHELF_CHECKPOINT);

  await workflows.rail.getByTestId("wftab-version").click();
  const menu = page.getByTestId("wftab-version-menu");
  await menu.locator(`[data-version="${first}"]`).click();
  await page
    .getByRole("menu", { name: `Version ${first}` })
    .getByRole("menuitem", { name: "Go back to this version" })
    .click();
  const back = notice(page, `Back on version ${first}, kept as version ${first + 2}.`);
  await expect(back).toBeVisible();
  // The head is the old graph again, kept as a new version.
  expect(await checkpointOf(apiContext, id)).toBe(RETIRED_CHECKPOINT);
  expect((await versions(apiContext))[0]).toMatchObject({ version: first + 2, current: true });

  await back.locator(".notice-action").filter({ hasText: "Undo" }).click();
  await expect(notice(page, `Back on version ${first + 1}, kept as version ${first + 3}.`)).toBeVisible();
  expect(await checkpointOf(apiContext, id)).toBe(SHELF_CHECKPOINT);
});
