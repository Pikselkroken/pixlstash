import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { test, expect } from "../fixtures/test.js";
import {
  API,
  SEEDED_LORA,
  cardId,
  duplicate,
  listWorkflows,
  made,
  ok,
  removeAll,
  tag,
  workflowPath,
} from "../fixtures/workflowsApi.js";

// A card's verbs against the real backend (#2315): delete, rename, hide,
// duplicate, export and the custom thumbnail. Nothing is faked.
//
// Each test acts on a duplicate of the seeded LoRA workflow (#2314), made
// through the real route, and deletes what it made afterwards: a duplicate is
// a manual workflow, so it can be deleted, and the seed is left as it was for
// the specs after this one.

const __dirname = dirname(fileURLToPath(import.meta.url));
const COVER_IMAGE = resolve(
  __dirname,
  "../../../test-data/images/041514da-7779-4544-8e9b-e1dbe19540a4.jpg",
);

const deleteDialog = (page, title) => page.getByRole("dialog", { name: title });
const selectedKeys = (page) =>
  page
    .locator('.wfv-row[aria-selected="true"]')
    .evaluateAll((rows) => rows.map((row) => row.dataset.key));

/** Pictures the e2e hook filed, for afterEach to unfile. */
let filed = [];
/** Recipes a test saved, for afterEach to delete. */
let recipes = [];

/**
 * Two library pictures no workflow holds yet: the fixture's own, never the
 * seed's, whose filing the specs after this one read.
 */
async function freePictures(api) {
  const { cards } = await listWorkflows(api, { include_hidden: true, include_one_offs: true });
  const held = new Set();
  for (const card of cards) {
    for (const id of await ok(await api.get(`${workflowPath(card.id)}/pictures`), "its pictures")) {
      held.add(id);
    }
  }
  const rows = await ok(await api.get(`${API}/pictures`, { params: { fields: "grid" } }), "pictures");
  const free = rows.map((row) => row.id).filter((id) => !held.has(id));
  expect(free.length, "two fixture pictures no workflow holds").toBeGreaterThanOrEqual(2);
  return free.slice(0, 2);
}

/** File *pictureIds* on *workflowId* (null unfiles), as a run's import would. */
async function file(api, pictureIds, workflowId) {
  await ok(
    await api.post(`${API}/test-hooks/file-pictures`, {
      data: { picture_ids: pictureIds, workflow_id: workflowId },
    }),
    "file the pictures",
  );
}

async function workflow(api, id) {
  return ok(await api.get(workflowPath(id)), `read ${id}`);
}

test.afterEach(async ({ apiContext }) => {
  if (filed.length) await file(apiContext, filed.splice(0), null);
  for (const id of recipes.splice(0)) await apiContext.delete(`${API}/recipes/${id}`);
  await removeAll(apiContext);
});

test.describe("delete", () => {
  test("asks first, then the card is gone, its pictures stay and its recipe is unfiled", async ({
    page,
    apiContext,
    workflows,
  }) => {
    const id = await duplicate(apiContext, await cardId(apiContext, SEEDED_LORA));
    const { card } = await workflow(apiContext, id);
    const pictures = await freePictures(apiContext);
    await file(apiContext, pictures, id);
    filed = pictures;
    // Positive control: without it the survival check below proves nothing.
    const listed = await ok(await apiContext.get(`${workflowPath(id)}/pictures`), "its pictures");
    expect([...listed].sort()).toEqual([...pictures].sort());
    const recipeName = `e2e-unfiled-${tag()}`;
    const recipe = await ok(
      await apiContext.post(`${API}/recipes`, {
        data: { workflow_id: id, name: recipeName, prompt: "a lighthouse at dusk" },
      }),
      "save a recipe",
    );
    recipes.push(recipe.id);

    await workflows.goto();
    await workflows.card(id).click();
    await page.keyboard.press("Delete");
    const ask = deleteDialog(page, "Delete this workflow?");
    await expect(ask).toBeVisible();
    await expect(ask).toContainText(card.name);
    await expect(ask).toContainText("Unfiled recipes");
    await ask.getByRole("button", { name: /^Delete/ }).click();
    await expect(ask).toBeHidden();
    await expect(workflows.row(id)).toHaveCount(0);
    // Focus is back in the grid, not on <body>.
    await expect
      .poll(() => page.evaluate(() => Boolean(document.activeElement?.closest('[role="treegrid"]'))))
      .toBe(true);

    await page.reload();
    await expect(page.locator(".wfv-row").first()).toBeVisible({ timeout: 15_000 });
    await expect(workflows.row(id)).toHaveCount(0);
    expect((await apiContext.get(workflowPath(id))).status()).toBe(404);
    for (const pictureId of pictures) {
      expect((await apiContext.get(`${API}/pictures/${pictureId}`)).status()).toBe(200);
    }
    await expect(
      page.locator(".wfv-unfiled .wfrt-card").filter({ hasText: recipeName }),
    ).toBeVisible();
    const unfiled = await ok(
      await apiContext.get(`${API}/recipes`, { params: { unfiled: "true" } }),
      "list the unfiled recipes",
    );
    expect(unfiled.map((row) => row.id)).toContain(recipe.id);
  });

  test("two at once, one gone already: says how many were deleted", async ({
    page,
    apiContext,
    workflows,
  }) => {
    const seed = await cardId(apiContext, SEEDED_LORA);
    const first = await duplicate(apiContext, seed);
    const second = await duplicate(apiContext, seed);

    await workflows.goto();
    await workflows.card(first).click();
    await workflows.card(second).click({ modifiers: ["Control"] });
    await expect.poll(() => selectedKeys(page)).toEqual(expect.arrayContaining([first, second]));
    await page.keyboard.press("Delete");
    const ask = deleteDialog(page, "Delete these 2 workflows?");
    await expect(ask).toBeVisible();

    // Gone on the server while the question is open: the store's delete of it
    // gets a real 404.
    await ok(await apiContext.delete(workflowPath(second)), "delete one behind the dialog");
    await ask.getByRole("button", { name: /^Delete/ }).click();
    await expect(workflows.notices.filter({ hasText: "1 of 2 workflows were deleted; the rest could not be." })).toBeVisible();

    await page.reload();
    await expect(page.locator(".wfv-row").first()).toBeVisible({ timeout: 15_000 });
    await expect(workflows.row(first)).toHaveCount(0);
    await expect(workflows.row(second)).toHaveCount(0);
  });
});

test("Rename, by F2 and from the menu, survives a reload", async ({ page, apiContext, workflows }) => {
  const id = await duplicate(apiContext, await cardId(apiContext, SEEDED_LORA));
  const rename = page.getByRole("dialog", { name: "Rename workflow" });
  const byKey = `e2e-renamed-${tag()}`;
  const byMenu = `e2e-renamed-${tag()}`;

  await workflows.goto();
  await workflows.card(id).click();
  await page.keyboard.press("F2");
  await expect(rename).toBeVisible();
  await rename.getByRole("textbox", { name: "Name" }).fill(byKey);
  await rename.getByRole("textbox", { name: "Name" }).press("Enter");
  await expect(rename).toBeHidden();
  await page.reload();
  await expect(workflows.card(id)).toHaveAccessibleName(new RegExp(byKey), { timeout: 15_000 });
  expect((await workflow(apiContext, id)).card.name).toBe(byKey);

  await workflows.menu(id);
  await workflows.verb("rename").click();
  await expect(rename).toBeVisible();
  await rename.getByRole("textbox", { name: "Name" }).fill(byMenu);
  await rename.getByRole("button", { name: "Rename" }).click();
  await expect(rename).toBeHidden();
  await page.reload();
  await expect(workflows.card(id)).toHaveAccessibleName(new RegExp(byMenu), { timeout: 15_000 });
  expect((await workflow(apiContext, id)).card.name).toBe(byMenu);
});

test("Hide takes the card off the grid, and Show hidden brings it back to unhide", async ({
  page,
  apiContext,
  workflows,
}) => {
  const id = await duplicate(apiContext, await cardId(apiContext, SEEDED_LORA));
  const { hidden } = await listWorkflows(apiContext);

  await workflows.goto();
  await workflows.menu(id);
  await expect(workflows.verb("hide")).toHaveText(/Hide/);
  await workflows.verb("hide").click();
  await expect(workflows.row(id)).toHaveCount(0);
  expect((await listWorkflows(apiContext)).hidden).toBe(hidden + 1);

  await page.getByRole("button", { name: /^(\d+ )?Filter$/ }).click();
  await page.locator('.fm-row[data-kind="showHidden"]').click();
  await page.getByTestId("wff-sub-showHidden").getByRole("radio", { name: "Show" }).click();
  await page.keyboard.press("Escape");
  await expect(workflows.row(id)).toHaveCount(1);

  await workflows.menu(id);
  await expect(workflows.verb("hide")).toHaveText(/Unhide/);
  await workflows.verb("hide").click();
  await expect.poll(async () => (await listWorkflows(apiContext)).hidden).toBe(hidden);
  expect((await workflow(apiContext, id)).hidden).toBe(false);
});

test("Duplicate makes a card of its own, renamed without touching the original", async ({
  page,
  apiContext,
  workflows,
}) => {
  const id = await duplicate(apiContext, await cardId(apiContext, SEEDED_LORA));
  const { card } = await workflow(apiContext, id);

  await workflows.goto();
  await workflows.menu(id);
  await workflows.verb("duplicate").click();
  await expect(workflows.notices.filter({ hasText: `Duplicated as ${card.name} (copy).` })).toBeVisible();
  // The copy lands selected.
  await expect.poll(async () => (await selectedKeys(page)).filter((key) => key !== id)).toHaveLength(1);
  const [copy] = (await selectedKeys(page)).filter((key) => key !== id);
  made.push(copy);

  const name = `e2e-copy-${tag()}`;
  await page.keyboard.press("F2");
  const rename = page.getByRole("dialog", { name: "Rename workflow" });
  await rename.getByRole("textbox", { name: "Name" }).fill(name);
  await rename.getByRole("textbox", { name: "Name" }).press("Enter");
  await expect(rename).toBeHidden();
  await page.reload();
  await expect(workflows.card(copy)).toHaveAccessibleName(new RegExp(name), { timeout: 15_000 });
  expect((await workflow(apiContext, copy)).card.name).toBe(name);
  expect((await workflow(apiContext, id)).card.name).toBe(card.name);
});

test("Export downloads a file that imports back to the same workflow", async ({
  page,
  apiContext,
  workflows,
}) => {
  const id = await duplicate(apiContext, await cardId(apiContext, SEEDED_LORA));

  await workflows.goto();
  await workflows.card(id).click();
  const download = page.waitForEvent("download");
  await page.locator('.wfv-pill-host [data-verb="export"]').click();
  const file = await download;
  expect(file.suggestedFilename()).toMatch(/\.json$/);
  const saved = test.info().outputPath(file.suggestedFilename());
  await file.saveAs(saved);

  const before = new Set(await selectedKeys(page));
  await page.locator('input[type="file"][accept=".json,application/json"]').setInputFiles(saved);
  // The import selects the card it made.
  await expect
    .poll(async () => (await selectedKeys(page)).filter((key) => !before.has(key)))
    .toHaveLength(1);
  const [imported] = (await selectedKeys(page)).filter((key) => !before.has(key));
  made.push(imported);

  const exported = async (key) =>
    (await ok(await apiContext.get(`${workflowPath(key)}/export`), `export ${key}`)).workflow;
  const original = await exported(id);
  expect(JSON.parse(readFileSync(saved, "utf8"))).toEqual(original);
  expect(await exported(imported)).toEqual(original);
});

test("a chosen thumbnail is pinned over the pictures, and unpinned shows them again", async ({
  page,
  apiContext,
  workflows,
}) => {
  // With pictures, so a thumbnail shows only while pinned: without any it
  // shows either way, and the pin would prove nothing.
  const id = await duplicate(apiContext, await cardId(apiContext, SEEDED_LORA));
  const pictures = await freePictures(apiContext);
  await file(apiContext, pictures, id);
  filed = pictures;
  const cover = workflows.card(id).locator("img").first();

  await workflows.goto();
  await expect(cover).toHaveAttribute("src", /\/pictures\/thumbnails\//);
  await workflows.menu(id);
  const chooser = page.waitForEvent("filechooser");
  await workflows.verb("set-thumbnail").click();
  await (await chooser).setFiles(COVER_IMAGE);
  await expect.poll(async () => (await workflow(apiContext, id)).card.thumbnail?.pinned).toBe(true);
  const { card } = await workflow(apiContext, id);

  await page.reload();
  await expect(cover).toHaveAttribute("src", new RegExp(card.thumbnail.url.split("?")[0]), {
    timeout: 15_000,
  });

  await workflows.menu(id);
  await expect(workflows.verb("pin-thumbnail")).toHaveText(/Show generated pictures/);
  await workflows.verb("pin-thumbnail").click();
  await expect.poll(async () => (await workflow(apiContext, id)).card.thumbnail?.pinned).toBe(false);
  await page.reload();
  await expect(cover).toHaveAttribute("src", /\/pictures\/thumbnails\//, { timeout: 15_000 });
});
