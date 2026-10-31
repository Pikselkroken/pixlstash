import { mkdirSync } from "node:fs";
import { join } from "node:path";

import { test, expect } from "../fixtures/test.js";
import {
  CKPT,
  NOW,
  OPEN,
  SHA,
  VAE,
  WF,
  gate,
  stubWorkflows,
} from "../fixtures/workflowTabStubs.js";

// The Workflow tab as the editor, and the version menu in its head (#1850,
// and the submenu of what can be done with an earlier version, #1792).
//
// The unit tests hold the logic. What they cannot see is here, because jsdom
// lays nothing out, opens no real `v-menu` and focuses nothing it is not told
// to: that the rows fit the 288px rail with a long model name in them, that a
// menu opened from the rail lands inside the window, which button is amber,
// and where the keyboard is after a menu, a dialog or the bar goes away.
//
// Every route the screen reads is answered from one small fake of the server
// (`world`, in `fixtures/workflowTabStubs.js`), which keeps what a write
// stored: a PUT of the changes answers the next GET and moves the card's
// `unsaved_changes`, a go-back adds a version. A workflow route nobody stubbed is recorded and fails the test,
// so nothing here can pass on the real backend's 404.

/** Set to a directory to keep the screenshots; unset, none is written. */
const SHOTS = process.env.WORKFLOW_TAB_SHOTS || "";

test.use({ locale: "en-GB", timezoneId: "UTC" });

let net;
test.afterEach(() => {
  // Loud, and last: a request that fell through would have been answered 599.
  expect(net?.unstubbed ?? [], "workflow routes this spec did not stub").toEqual([]);
});

// ── What is on the page ─────────────────────────────────────────────────────

const rail = (page) => page.getByRole("complementary", { name: "Inspector" });
const cardRow = (page, index = 0) => page.locator(".wfv-row").nth(index);
const chip = (page) => rail(page).getByRole("button", { name: /^Version \d+/ });
const versionMenu = (page) => page.getByRole("menu", { name: "Versions" });
const versionRow = (page, number) =>
  versionMenu(page).locator(`[data-version="${number}"]`);
/** The submenu an earlier version's row opens (#1792). */
const versionActions = (page, number) =>
  page.getByRole("menu", { name: `Version ${number}` });
const bar = (page) => rail(page).getByRole("group", { name: "Unsaved changes" });
const runButton = (page) => rail(page).getByRole("button", { name: /^Run( with changes)?…$/ });
const checkpointRow = (page) => rail(page).getByTestId("wftab-row-checkpoint");
const stage = (page, name) => rail(page).getByRole("button", { name });
const strength = (page, name) =>
  lorasDialog(page).getByRole("spinbutton", { name: `Strength of ${name}` });
const editLoras = (page) => rail(page).getByRole("button", { name: "Edit LoRAs" });
/** The rail's Edit LoRAs dialog (#2165): live edits, Done closes. */
const lorasDialog = (page) => page.locator(".app-dialog").filter({ hasText: "Edit LoRAs" });
/** The chain editor, which Add a LoRA or reorder… opens in its place. */
const chainDialog = (page) => page.locator(".app-dialog").filter({ has: page.getByTestId("eld-primary") });
const notices = (page) => page.locator(".notice-message");

/** Open the Workflows screen on the stubbed library and select a card. */
async function selectWorkflow(page, index = 0) {
  await page.clock.setFixedTime(new Date(NOW));
  await page.goto("/workflows");
  await expect(cardRow(page, 1)).toBeVisible({ timeout: 15_000 });
  await cardRow(page, index).click();
  await expect(rail(page)).toBeVisible();
  // Drawn: the detail read has landed and the rows are in.
  await expect(checkpointRow(page)).toBeVisible();
}

/** Edit LoRAs open on the rail, with its rows read. */
async function openLoras(page) {
  await editLoras(page).click();
  await expect(lorasDialog(page)).toBeVisible();
  await settled(lorasDialog(page));
}

/** Done closes Edit LoRAs; every change in it was already unsaved. */
async function doneLoras(page) {
  await lorasDialog(page).getByTestId("wftab-loras-done").click();
  await expect(lorasDialog(page)).toBeHidden();
}

/** A stage switched, a loader removed and a strength typed: three changes. */
async function changeThree(page) {
  await stage(page, "Face detailer").click();
  await expect(bar(page)).toContainText("1 unsaved change");
  await openLoras(page);
  await lorasDialog(page).getByRole("button", { name: "Remove film-grain-35mm" }).click();
  await expect(lorasDialog(page).getByRole("button", { name: "Put film-grain-35mm back" })).toBeFocused();
  await doneLoras(page);
  await expect(bar(page)).toContainText("2 unsaved changes");
  await openLoras(page);
  await strength(page, "detail_slider_v2").fill("0.45");
  await strength(page, "detail_slider_v2").press("Enter");
  await doneLoras(page);
  await expect(bar(page)).toContainText("3 unsaved changes");
}

/**
 * Wait until *locator* has stopped moving and fading: a menu or a dialog
 * scales and fades in, and a box measured (or a picture taken) on the way is
 * of something smaller and fainter than what the owner sees.
 *
 * Still is not the same as not begun. An overlay sits at opacity 0, in its
 * place, until its enter transition starts, and that transition's first
 * frames still read as its start values: four identical frames there are of
 * a dialog nobody can see yet, and whatever is probed next hits the scrim. So
 * a frame counts only once the thing is showing and nothing is running on it.
 */
async function settled(locator) {
  await locator.evaluate(
    (el) =>
      new Promise((resolve) => {
        const moving = el.closest(".v-overlay__content") ?? el;
        let last = "";
        let same = 0;
        const tick = () => {
          const box = moving.getBoundingClientRect();
          const opacity = getComputedStyle(moving).opacity;
          const now = [box.x, box.y, box.width, box.height, opacity].join();
          const begun = Number(opacity) > 0 && !moving.getAnimations().length;
          same = now === last && begun ? same + 1 : 0;
          last = now;
          if (same >= 4) resolve();
          else requestAnimationFrame(tick);
        };
        tick();
      }),
  );
}

/** Whether *locator*'s content is wider than its box. */
const overflows = (locator) =>
  locator.evaluate((el) => el.scrollWidth > el.clientWidth);

/** Whether *locator*'s box lies wholly inside the window. */
async function insideViewport(page, locator) {
  const box = await locator.boundingBox();
  const view = page.viewportSize();
  return (
    Boolean(box) &&
    box.x >= 0 &&
    box.y >= 0 &&
    box.x + box.width <= view.width &&
    box.y + box.height <= view.height
  );
}

/**
 * Whether *locator* is the thing drawn at its own far corners: a surface an
 * ancestor clipped, or one lying under the rail's edge, is not. The probe sits
 * 12px in from each corner so a rounded corner (surfaces are 8px here) still
 * answers for itself; a real clip hides a whole corner region, far more than
 * that.
 */
const drawnAtItsCorners = (locator) =>
  locator.evaluate((el) => {
    const box = el.getBoundingClientRect();
    const inset = 12;
    const at = (x, y) => el.contains(document.elementFromPoint(x, y));
    return (
      at(box.left + inset, box.top + inset) &&
      at(box.right - inset, box.top + inset) &&
      at(box.left + inset, box.bottom - inset) &&
      at(box.right - inset, box.bottom - inset)
    );
  });

const backgroundOf = (locator) =>
  locator.evaluate((el) => getComputedStyle(el).backgroundColor);

/** One computed style of *locator*, or of its `pseudo` element. */
const styleOf = (locator, property, pseudo) =>
  locator.evaluate(
    (el, [name, of]) => getComputedStyle(el, of)[name],
    [property, pseudo],
  );

async function shot(locator, name) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  await locator.screenshot({ path: join(SHOTS, `w-${name}.png`) });
}

/** The rail and whatever floats beside it, as one tight crop. */
async function shotAround(page, locators, name) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  const boxes = (await Promise.all(locators.map((entry) => entry.boundingBox()))).filter(Boolean);
  const left = Math.max(0, Math.min(...boxes.map((box) => box.x)) - 8);
  const top = Math.max(0, Math.min(...boxes.map((box) => box.y)) - 8);
  const right = Math.max(...boxes.map((box) => box.x + box.width)) + 8;
  const bottom = Math.max(...boxes.map((box) => box.y + box.height)) + 8;
  const view = page.viewportSize();
  await page.screenshot({
    path: join(SHOTS, `w-${name}.png`),
    clip: {
      x: left,
      y: top,
      width: Math.min(right, view.width) - left,
      height: Math.min(bottom, view.height) - top,
    },
  });
}

// ── 1. The head and the version menu ────────────────────────────────────────

test.describe("the head and the version menu", () => {
  test("the chip sits under the picture count and opens a menu inside the window", async ({ page }) => {
    net = await stubWorkflows(page);
    await selectWorkflow(page);

    await expect(chip(page)).toHaveText("Version 3 · 2 Oct");
    const chipBox = await chip(page).boundingBox();
    // A line of its own in the head, above the Models panel (the star chart
    // sits beside the tabs, not in the head).
    const models = await rail(page).getByTestId("wftab-default-recipe").boundingBox();
    expect(chipBox.y + chipBox.height).toBeLessThanOrEqual(models.y + 1);
    expect(await overflows(rail(page).locator(".inspector-body"))).toBe(false);

    await chip(page).click();
    await expect(versionMenu(page)).toBeVisible();
    await expect(chip(page)).toHaveAttribute("aria-expanded", "true");
    await expect(versionMenu(page).locator("[data-version]")).toHaveCount(3);
    await settled(versionMenu(page));
    expect(await insideViewport(page, versionMenu(page))).toBe(true);
    // Not cut off where the rail ends: it is the surface drawn at its corners.
    expect(await drawnAtItsCorners(versionMenu(page))).toBe(true);

    // The current version is said, and carries the check: the others do not,
    // and open what can be done with them instead.
    await expect(versionRow(page, 3)).toHaveAttribute("aria-checked", "true");
    await expect(versionRow(page, 2)).toHaveAttribute("aria-haspopup", "menu");
    await expect(versionRow(page, 3).locator(".ctx-icon.v-icon")).toHaveCount(1);
    await expect(versionRow(page, 2).locator(".ctx-icon.v-icon")).toHaveCount(0);
    // The reason is the server's own words, the day beside it.
    await expect(versionRow(page, 2)).toContainText("28 Aug · LoRAs edited");
    await expect(versionRow(page, 1)).toContainText("as PixlStash first read it");
    await shotAround(page, [rail(page), versionMenu(page)], "version-menu");
  });

  test("the arrows move between rows, and Escape puts the keyboard back on the chip", async ({ page }) => {
    net = await stubWorkflows(page);
    await selectWorkflow(page);

    await chip(page).focus();
    // From the chip, the arrow opens the menu on its first row.
    await page.keyboard.press("ArrowDown");
    await expect(versionMenu(page)).toBeVisible();
    await expect(versionRow(page, 3)).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(versionRow(page, 2)).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(versionRow(page, 1)).toBeFocused();
    await page.keyboard.press("ArrowUp");
    await expect(versionRow(page, 2)).toBeFocused();

    await page.keyboard.press("Escape");
    await expect(versionMenu(page)).toBeHidden();
    await expect(chip(page)).toBeFocused();
    expect(net.restores).toEqual([]);
  });

  test("an older row goes back: the head moves, and a notice offers Undo", async ({ page }) => {
    net = await stubWorkflows(page);
    await selectWorkflow(page);

    await chip(page).click();
    await versionRow(page, 2).click();
    // The row opens what can be done with it, and the menu stays: a click in
    // a real `v-menu`'s content closes it unless the row stops its click.
    const actions = versionActions(page, 2);
    await expect(actions).toBeVisible();
    await expect(versionMenu(page)).toBeVisible();
    expect(net.restores).toEqual([]);
    await settled(actions);
    // Beside the menu, inside the window, and not clipped by the menu.
    expect(await insideViewport(page, actions)).toBe(true);
    expect(await drawnAtItsCorners(actions)).toBe(true);
    await shotAround(page, [rail(page), versionMenu(page), actions], "version-actions");
    await actions.getByRole("menuitem", { name: "Go back to this version" }).click();

    await expect(chip(page)).toHaveText("Version 4 · today");
    expect(net.restores).toEqual([2]);
    await expect(versionMenu(page)).toBeHidden();
    await expect(chip(page)).toBeFocused();
    await expect(notices(page).last()).toHaveText(
      "Back on version 2, kept as version 4. Nothing was removed.",
    );
    const undo = page.locator(".notice-card").last().getByRole("button", { name: "Undo" });
    await expect(undo).toBeVisible();

    // The menu now lists the go-back itself, in the server's words.
    await chip(page).click();
    await expect(versionMenu(page).locator("[data-version]")).toHaveCount(4);
    await expect(versionRow(page, 4)).toHaveAttribute("aria-checked", "true");
    await expect(versionRow(page, 4)).toContainText("today · went back to version 2");
    await page.keyboard.press("Escape");

    // Undo is going back to the version left.
    await undo.click();
    await expect(chip(page)).toHaveText("Version 5 · today");
    expect(net.restores).toEqual([2, 3]);
  });

  test("an older row duplicates from the keyboard, and the workflow stays where it is", async ({ page }) => {
    net = await stubWorkflows(page);
    await selectWorkflow(page);

    await chip(page).focus();
    await page.keyboard.press("ArrowDown");
    await expect(versionRow(page, 3)).toBeFocused();
    await page.keyboard.press("ArrowDown");
    await expect(versionRow(page, 2)).toBeFocused();
    // → opens it on its first row, ← goes back to the version's row.
    await page.keyboard.press("ArrowRight");
    const actions = versionActions(page, 2);
    await expect(
      actions.getByRole("menuitem", { name: "Go back to this version" }),
    ).toBeFocused();
    await page.keyboard.press("ArrowLeft");
    await expect(actions).toBeHidden();
    await expect(versionRow(page, 2)).toBeFocused();
    await page.keyboard.press("Enter");
    await page.keyboard.press("ArrowDown");
    await expect(actions.getByRole("menuitem", { name: "Duplicate" })).toBeFocused();
    // No ComfyUI in this world: nothing offered to open.
    await expect(actions.getByRole("menuitem", { name: "Open in ComfyUI" })).toHaveCount(0);
    await page.keyboard.press("Enter");

    await expect(notices(page).last()).toHaveText(
      "Duplicated version 2 as Portrait, soft window light (version 2).",
    );
    expect(net.duplicates).toEqual([2]);
    expect(net.restores).toEqual([]);
    await expect(chip(page)).toHaveText("Version 3 · 2 Oct");
    await expect(versionMenu(page)).toBeHidden();
  });

  test("a refused go-back is said under the head, in the server's words", async ({ page }) => {
    const sentence = "That version cannot be read, so it cannot be gone back to.";
    net = await stubWorkflows(page, { restoreRefusal: sentence });
    await selectWorkflow(page);

    await chip(page).click();
    await versionRow(page, 1).click();
    await versionActions(page, 1)
      .getByRole("menuitem", { name: "Go back to this version" })
      .click();

    const refusal = rail(page).getByRole("alert").filter({ hasText: "Could not go back" });
    await expect(refusal).toContainText(sentence);
    expect(net.restores).toEqual([1]);
    // Still on the version it had, and the sentence is under the head, above
    // the Models panel.
    await expect(chip(page)).toHaveText("Version 3 · 2 Oct");
    const below = await rail(page).getByTestId("wftab-default-recipe").boundingBox();
    const box = await refusal.boundingBox();
    expect(box.y + box.height).toBeLessThanOrEqual(below.y + 1);
    expect(await overflows(refusal)).toBe(false);

    await refusal.getByRole("button", { name: "Dismiss" }).click();
    await expect(refusal).toBeHidden();
    await expect(chip(page)).toBeFocused();
  });
});

// ── 2. Rows as editors, at the real rail width ──────────────────────────────

test.describe("rows as editors", () => {
  test("nothing in the rail runs past its edge, a long model name included", async ({ page }) => {
    net = await stubWorkflows(page);
    await selectWorkflow(page);
    // Three loaders, each a row with a name and its strength.
    await expect(rail(page).getByTestId("wftab-default-lora")).toHaveCount(3);
    await expect(stage(page, "Upscale")).toHaveAttribute("aria-pressed", "true");
    await expect(stage(page, "Face detailer")).toHaveAttribute("aria-pressed", "false");

    const railBox = await rail(page).boundingBox();
    expect(Math.round(railBox.width)).toBe(288);
    expect(await overflows(rail(page).locator(".inspector-body"))).toBe(false);
    const rows = rail(page).locator(".wftab-row");
    const count = await rows.count();
    expect(count).toBeGreaterThanOrEqual(6);
    for (let index = 0; index < count; index += 1) {
      expect(await overflows(rows.nth(index)), `row ${index}`).toBe(false);
      const box = await rows.nth(index).boundingBox();
      expect(box.x + box.width, `row ${index} ends inside the rail`).toBeLessThanOrEqual(
        railBox.x + railBox.width,
      );
    }
    // The strength field and the × are in the Edit LoRAs dialog: its rows fit too.
    await openLoras(page);
    const dialogRows = lorasDialog(page).getByTestId("wfl-row");
    await expect(dialogRows).toHaveCount(3);
    for (let index = 0; index < 3; index += 1) {
      expect(await overflows(dialogRows.nth(index)), `dialog row ${index}`).toBe(false);
    }
    await doneLoras(page);
  });

  test("a stage toggles with Space, and a strength takes a typed value", async ({ page }) => {
    net = await stubWorkflows(page);
    await selectWorkflow(page);

    await stage(page, "Face detailer").focus();
    await page.keyboard.press("Space");
    await expect(stage(page, "Face detailer")).toHaveAttribute("aria-pressed", "true");
    await expect(bar(page)).toContainText("1 unsaved change");
    expect(net.changePuts.at(-1).stages).toEqual({ face_detailer: true });

    await openLoras(page);
    const field = strength(page, "detail_slider_v2");
    await expect(field).toHaveValue("0.60");
    await field.fill("0.45");
    await field.press("Enter");
    await expect(bar(page)).toContainText("2 unsaved changes");
    // Polled: the strength's write follows the stage's answer (one write on
    // the wire), and the bar says two before it has left.
    await expect
      .poll(() => net.changePuts.at(-1).loras)
      .toEqual([
        {
          node_id: "7",
          field: "lora_name",
          sha256: SHA.detail,
          strength_model: 0.45,
          strength_clip: 0.45,
        },
      ]);
    await expect(
      lorasDialog(page).getByTestId("wfl-row").first().getByTestId("wftab-lora-saved"),
    ).toContainText("Saved: 0.60");
    // The field is wide enough for what was typed.
    expect(await overflows(field)).toBe(false);
    await doneLoras(page);
  });

  test("× moves the keyboard to Put back, and Put back moves it to ×", async ({ page }) => {
    net = await stubWorkflows(page);
    await selectWorkflow(page);

    await openLoras(page);
    const remove = lorasDialog(page).getByRole("button", { name: "Remove film-grain-35mm" });
    const putBack = lorasDialog(page).getByRole("button", { name: "Put film-grain-35mm back" });
    await remove.click();
    await expect(putBack).toBeFocused();
    expect(net.changePuts.at(-1).skip_loras).toEqual([{ node_id: "8", field: "lora_name" }]);
    // The row is still there, struck through and marked, in the dialog and
    // in the rail's list.
    const row = lorasDialog(page).getByTestId("wfl-row").nth(1);
    await expect(row.getByText("Removed", { exact: true })).toBeVisible();
    await expect(row.locator("s")).toHaveText("film-grain-35mm");
    await expect(lorasDialog(page).getByTestId("wfl-row")).toHaveCount(3);
    expect(await overflows(row)).toBe(false);
    await expect(rail(page).getByTestId("wftab-lora-removed")).toBeVisible();

    await page.keyboard.press("Enter");
    await expect(remove).toBeFocused();
    await expect(bar(page)).toBeHidden();
    await expect.poll(() => net.changePuts.at(-1).skip_loras).toEqual([]);
    await doneLoras(page);
  });
});

// ── 3. The bar ──────────────────────────────────────────────────────────────

test.describe("the bar", () => {
  test("is absent with nothing waiting, and with three changes sits above Run with changes…", async ({ page }) => {
    net = await stubWorkflows(page);
    await selectWorkflow(page);
    await expect(bar(page)).toBeHidden();
    await expect(runButton(page)).toContainText("Run…");

    await changeThree(page);
    await expect(bar(page).getByTestId("wftab-draft-count")).toHaveText("3 unsaved changes");
    await expect(runButton(page)).toContainText("Run with changes…");

    const barBox = await bar(page).boundingBox();
    const runBox = await runButton(page).boundingBox();
    expect(barBox.y + barBox.height).toBeLessThanOrEqual(runBox.y);
    expect(await overflows(bar(page))).toBe(false);

    // Run… is the one amber button: neither verb wears its fill.
    const save = bar(page).getByRole("button", { name: "Save", exact: true });
    const saveAsNew = bar(page).getByRole("button", { name: "Save as new…" });
    const amber = await backgroundOf(runButton(page));
    expect(await backgroundOf(save)).not.toBe(amber);
    expect(await backgroundOf(saveAsNew)).not.toBe(amber);
    // Save is the filled verb and Save as new… the outline beside it, at one
    // height: which of the two is the ordinary one is not a guess.
    const clear = "rgba(0, 0, 0, 0)";
    const teal = await backgroundOf(save);
    expect(teal).not.toBe(clear);
    expect(await backgroundOf(saveAsNew)).toBe(clear);
    expect((await save.boundingBox()).height).toBe(
      (await saveAsNew.boundingBox()).height,
    );
    // The shelf is a band in Save's teal, not the rail's own ground.
    expect(await styleOf(bar(page), "borderTopColor")).toBe(teal);
    expect(await backgroundOf(bar(page))).not.toBe(clear);
    // One teal ink for everything that says "not saved yet": the count's
    // glyph, the outline, and the dot before the row's own Edited.
    const ink = await styleOf(bar(page).locator(".wftab-draft__count .v-icon"), "color");
    expect(ink).not.toBe(await styleOf(bar(page), "color"));
    expect(await styleOf(saveAsNew, "borderTopColor")).toBe(ink);
    const edited = rail(page).getByTestId("wftab-stages-edited");
    expect(await styleOf(edited, "backgroundColor", "::before")).toBe(ink);
    expect(await styleOf(edited, "width", "::before")).toBe("8px");
    // It arrived with the first change: the pulse is the bar's own.
    expect(await styleOf(bar(page), "animationName")).toContain("wftab-draft-arrive");
    await shotAround(page, [rail(page)], "rail-three-changes");
  });

  test("arrives without the pulse under reduced motion, and says its first count", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    net = await stubWorkflows(page);
    await selectWorkflow(page);
    const said = rail(page).getByTestId("wftab-draft-arrival");
    await expect(said).toHaveText("");

    await stage(page, "Face detailer").click();
    await expect(said).toHaveText("1 unsaved change");
    await expect(bar(page)).toHaveClass(/wftab-draft--arrived/);
    expect(await styleOf(bar(page), "animationName")).toBe("none");
  });

  test("stays in view above Run… when the rows are taller than the window", async ({ page }) => {
    net = await stubWorkflows(page);
    await page.setViewportSize({ width: 1280, height: 480 });
    await selectWorkflow(page);
    await stage(page, "Face detailer").click();
    await expect(bar(page)).toContainText("1 unsaved change");

    // The body scrolls: there is more of it than the window shows.
    const body = rail(page).locator(".inspector-body");
    expect(await body.evaluate((el) => el.scrollHeight > el.clientHeight)).toBe(true);
    // The bar is not part of what scrolls: whole, in the window, above Run….
    expect(await insideViewport(page, bar(page))).toBe(true);
    expect(await insideViewport(page, runButton(page))).toBe(true);
    expect(await drawnAtItsCorners(bar(page))).toBe(true);
    // Scrolled to the foot of the rows, it has not moved.
    const before = await bar(page).boundingBox();
    await body.evaluate((el) => el.scrollTo(0, el.scrollHeight));
    expect(await bar(page).boundingBox()).toEqual(before);
    await expect(bar(page).getByRole("button", { name: "Save", exact: true })).toBeVisible();
  });

  test("draws no Save where the server closed it, and prints why", async ({ page }) => {
    const reason =
      "A change of checkpoint is saved as a new workflow, so this one keeps its pictures.";
    net = await stubWorkflows(page, {
      verbs: { save: { open: false, reason }, save_as_new: OPEN },
    });
    await selectWorkflow(page);
    await stage(page, "Face detailer").click();

    await expect(bar(page)).toContainText("1 unsaved change");
    await expect(bar(page).getByRole("button", { name: "Save", exact: true })).toHaveCount(0);
    const saveAsNew = bar(page).getByRole("button", { name: "Save as new…" });
    await expect(saveAsNew).toBeVisible();
    // The one verb that keeps, so the filled one: in the band's own teal.
    expect(await backgroundOf(saveAsNew)).toBe(
      await styleOf(bar(page), "borderTopColor"),
    );
    await expect(bar(page).getByText(reason, { exact: true })).toBeVisible();
    expect(await overflows(bar(page))).toBe(false);
    await shot(bar(page), "bar-save-closed");
  });

  test("Save as new… asks for the name with the keyboard in it, and Escape goes back", async ({ page }) => {
    net = await stubWorkflows(page);
    await selectWorkflow(page);
    await stage(page, "Face detailer").click();

    const saveAsNew = bar(page).getByRole("button", { name: "Save as new…" });
    await saveAsNew.click();
    const name = bar(page).getByRole("textbox", { name: "Name of the new workflow" });
    await expect(name).toBeFocused();
    await expect(bar(page).getByRole("button", { name: "Save", exact: true })).toHaveCount(0);
    await shot(bar(page), "bar-name-question");

    await page.keyboard.press("Escape");
    await expect(name).toBeHidden();
    await expect(saveAsNew).toBeFocused();
    // Escape left the rail, the selection and the changes as they were.
    await expect(bar(page)).toContainText("1 unsaved change");
    expect(net.savesAsNew).toEqual([]);

    // And with a name, Enter saves it as a new workflow, which is selected.
    await saveAsNew.click();
    await name.fill("Portrait, softer");
    await page.keyboard.press("Enter");
    await expect(rail(page).getByTestId("wftab-title")).toHaveText("Portrait, softer");
    expect(net.savesAsNew).toEqual([{ name: "Portrait, softer" }]);
    expect(net.saves).toEqual([]);
    await expect(bar(page)).toBeHidden();
  });

  test("Save sends the version, the bar goes, and the keyboard is on Run…", async ({ page }) => {
    net = await stubWorkflows(page);
    await selectWorkflow(page);
    await changeThree(page);

    await bar(page).getByRole("button", { name: "Save", exact: true }).click();
    await expect(bar(page)).toBeHidden();
    expect(net.saves).toEqual([{ version: 3 }]);
    await expect(runButton(page)).toBeFocused();
    await expect(runButton(page)).toContainText("Run…");
    await expect(chip(page)).toHaveText("Version 4 · today");
    await expect(notices(page).last()).toHaveText(
      "Saved as version 4. The version menu takes it back.",
    );
  });

  test("Discard sends the DELETE and asks nothing", async ({ page }) => {
    net = await stubWorkflows(page);
    await selectWorkflow(page);
    await changeThree(page);

    await bar(page).getByRole("button", { name: "Discard" }).click();
    await expect(bar(page)).toBeHidden();
    expect(net.discards).toEqual([WF]);
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(runButton(page)).toBeFocused();
    // The rows read as saved again.
    await expect(rail(page).getByTestId("wftab-stages-edited")).toBeHidden();
    await expect(stage(page, "Face detailer")).toHaveAttribute("aria-pressed", "false");
    await expect(rail(page).getByTestId("wftab-lora-removed")).toBeHidden();
  });
});

// ── 4. The card ─────────────────────────────────────────────────────────────

test.describe("the card", () => {
  const unsaved = (page) => cardRow(page).getByTestId("wf-card-unsaved");

  test("wears Unsaved after the first edit and loses it after Save, with no reload", async ({ page }) => {
    net = await stubWorkflows(page);
    await selectWorkflow(page);
    await expect(unsaved(page)).toHaveCount(0);

    await stage(page, "Face detailer").click();
    await expect(unsaved(page)).toHaveText("Unsaved");
    // With the tab's teal dot before the word.
    expect(await styleOf(unsaved(page), "width", "::before")).toBe("8px");
    expect(await styleOf(unsaved(page), "backgroundColor", "::before")).not.toBe(
      "rgba(0, 0, 0, 0)",
    );
    await expect(cardRow(page, 1).getByTestId("wf-card-unsaved")).toHaveCount(0);
    await shot(cardRow(page), "card-unsaved");

    await bar(page).getByRole("button", { name: "Save", exact: true }).click();
    await expect(unsaved(page)).toHaveCount(0);
  });

  test("keeps Unsaved clear of the picture count at the card's narrowest", async ({ page }) => {
    net = await stubWorkflows(page);
    await selectWorkflow(page);
    await stage(page, "Face detailer").click();
    await expect(unsaved(page)).toBeVisible();

    const count = cardRow(page).locator(".wf-card__badge--end").first();
    let narrowest = Infinity;
    for (const width of [1280, 1120, 1000, 900]) {
      await page.setViewportSize({ width, height: 720 });
      await expect(unsaved(page)).toBeVisible();
      // The grid re-flows over a transition; measure once it has stopped.
      await settled(cardRow(page));
      const card = await cardRow(page).boundingBox();
      const left = await unsaved(page).boundingBox();
      const right = await count.boundingBox();
      narrowest = Math.min(narrowest, card.width);
      expect(left.x + left.width, `at ${width}px the two badges do not meet`).toBeLessThan(right.x);
      expect(left.x).toBeGreaterThanOrEqual(card.x);
      expect(right.x + right.width).toBeLessThanOrEqual(card.x + card.width);
    }
    // The window does not get the grid down to its 240px column floor with
    // two cards in it, so the card is held to that width itself.
    expect(narrowest).toBeGreaterThan(240);
    await cardRow(page).evaluate((el) => {
      el.style.width = "240px";
      el.style.justifySelf = "start";
    });
    await settled(cardRow(page));
    const card = await cardRow(page).boundingBox();
    expect(Math.round(card.width)).toBe(240);
    const left = await unsaved(page).boundingBox();
    const right = await count.boundingBox();
    expect(left.x + left.width).toBeLessThan(right.x);
    expect(right.x + right.width).toBeLessThanOrEqual(card.x + card.width);
  });
});

// ── 5. Away and back ────────────────────────────────────────────────────────

test("changes stay with the workflow when another card is selected, and nothing asks", async ({ page }) => {
  net = await stubWorkflows(page);
  await selectWorkflow(page);
  await changeThree(page);
  const asked = [];
  page.on("dialog", (dialog) => {
    asked.push(dialog.message());
    void dialog.dismiss();
  });

  await cardRow(page, 1).click();
  await expect(rail(page).getByTestId("wftab-title")).toHaveText("Landscape, golden hour");
  await expect(bar(page)).toBeHidden();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(rail(page).getByTestId("wftab-stages-edited")).toBeHidden();

  // While the other card is shown, one of the three is taken back somewhere
  // else: what the rail shows on the way back is then the server's set, not
  // anything it kept of its own.
  const { stages: _gone, ...rest } = net.world.waiting[WF];
  net.world.waiting[WF] = { ...rest, stages: {} };
  const puts = net.changePuts.length;
  await cardRow(page, 0).click();
  await expect(rail(page).getByTestId("wftab-title")).toHaveText("Portrait, soft window light");
  await expect(bar(page).getByTestId("wftab-draft-count")).toHaveText("2 unsaved changes");
  await expect(stage(page, "Face detailer")).toHaveAttribute("aria-pressed", "false");
  await expect(rail(page).getByTestId("wftab-lora-removed")).toBeVisible();
  // Read, not written: coming back stored nothing and discarded nothing.
  expect(net.changePuts).toHaveLength(puts);
  expect(net.discards).toEqual([]);
  expect(asked).toEqual([]);
});

// ── 6. Change model set… and Edit LoRAs… ───────────────────────────────────────────

test.describe("Change model set…", () => {
  test("opens from the Models head inside the window and fills rows as unsaved changes", async ({ page }) => {
    net = await stubWorkflows(page);
    await selectWorkflow(page);

    const button = rail(page).getByRole("button", { name: "Change model set…" });
    const models = rail(page).getByTestId("wftab-default-recipe");
    const head = await models.locator(".section-head").boundingBox();
    const box = await button.boundingBox();
    expect(box.y).toBeGreaterThanOrEqual(head.y - 1);
    expect(box.y + box.height).toBeLessThanOrEqual(head.y + head.height + 1);

    await button.click();
    const menu = page.getByTestId("wftab-use-set-menu");
    await expect(menu).toBeVisible();
    const rows = menu.locator("[data-set]");
    await expect(rows).toHaveCount(2);
    await settled(menu);
    expect(await insideViewport(page, menu)).toBe(true);
    expect(await drawnAtItsCorners(menu)).toBe(true);
    // One request for every set, and each row says what it would do.
    expect(net.planAsks).toHaveLength(1);
    expect(net.planAsks[0].sets).toHaveLength(2);
    await expect(rows.nth(0)).toContainText("Portraits");
    await expect(rows.nth(0)).toContainText("fills 2 rows");
    await expect(rows.nth(1)).toContainText("needs a UNETLoader");
    await shotAround(page, [rail(page), menu], "use-a-set-menu");

    await rows.nth(0).click();
    await expect(menu).toBeHidden();
    await expect(button).toBeFocused();
    await expect(bar(page).getByTestId("wftab-draft-count")).toHaveText("2 unsaved changes");
    expect(net.changePuts).toHaveLength(1);
    expect(net.changePuts[0].models).toEqual([
      { address: CKPT, filename: "SDXL/juggernautXL_v9.safetensors" },
      { address: VAE, filename: "vae/sdxl-vae-fp16.safetensors" },
    ]);
    // Nothing was saved and no workflow was made.
    expect(net.saves).toEqual([]);
    expect(net.savesAsNew).toEqual([]);
    await expect(checkpointRow(page).getByText("Edited", { exact: true })).toBeVisible();
    await expect(rail(page).getByTestId("wftab-row-vae").getByText("Edited", { exact: true })).toBeVisible();
    expect(await overflows(rail(page).locator(".inspector-body"))).toBe(false);
  });
});

test.describe("Edit LoRAs…", () => {
  const done = (page) => lorasDialog(page).getByTestId("wftab-loras-done");

  test("a removal is an unsaved change at once, and Done only closes", async ({ page }) => {
    net = await stubWorkflows(page);
    await selectWorkflow(page);

    await openLoras(page);
    await expect(done(page)).toHaveText("Done");
    await expect(lorasDialog(page).getByTestId("wfl-row")).toHaveCount(3);

    await lorasDialog(page).getByRole("button", { name: "Remove film-grain-35mm" }).click();
    await expect(lorasDialog(page).getByRole("button", { name: "Put film-grain-35mm back" })).toBeFocused();
    // Written at once: the bar has it while the dialog is still open.
    await expect(bar(page).getByTestId("wftab-draft-count")).toHaveText("1 unsaved change");
    expect(net.changePuts).toHaveLength(1);
    expect(net.changePuts[0].skip_loras).toEqual([{ node_id: "8", field: "lora_name" }]);
    expect(net.changePuts[0].loras).toEqual([]);
    await expect(lorasDialog(page).getByTestId("wftab-lora-removed")).toHaveText("Removed");
    await shot(lorasDialog(page), "edit-loras-removed");

    await doneLoras(page);
    // Only the set of changes: the chain itself was never written.
    expect(net.chainWrites).toEqual([]);
    expect(net.saves).toEqual([]);
    expect(net.changePuts).toHaveLength(1);
    await expect(rail(page).getByTestId("wftab-lora-removed")).toBeVisible();
    await expect(bar(page).getByTestId("wftab-draft-count")).toHaveText("1 unsaved change");
  });

  test("a move in the chain editor turns its button into Save with its note, and Cancel returns the keyboard", async ({ page }) => {
    net = await stubWorkflows(page);
    await selectWorkflow(page);

    await openLoras(page);
    await lorasDialog(page).getByTestId("wftab-lora-chain").click();
    const primary = (page) => chainDialog(page).getByTestId("eld-primary");
    const note = (page) => chainDialog(page).getByTestId("eld-note");
    await expect(chainDialog(page)).toBeVisible();
    await settled(chainDialog(page));
    const handle = chainDialog(page).getByRole("button", { name: /^Reorder detail_slider_v2/ });
    await handle.focus();
    await page.keyboard.press("Alt+ArrowDown");

    await expect(primary(page)).toHaveText("Save");
    await expect(primary(page)).toBeEnabled();
    await expect(note(page)).toContainText("Moving or adding a loader cannot wait as an unsaved change.");
    await expect(note(page)).toContainText("The version menu in the Workflow tab takes it back.");
    // Said as it happens, to a reader who is not looking at the button.
    await expect(chainDialog(page).getByTestId("eld-button-said")).toContainText(
      "The button now reads Save.",
    );
    await expect(chainDialog(page).getByTestId("eld-count")).toHaveText("1 loader moved");
    // The note is the button's description, and neither runs out of the dialog.
    const describedBy = await primary(page).getAttribute("aria-describedby");
    expect(describedBy).toBe(await note(page).getAttribute("id"));
    expect(await overflows(note(page))).toBe(false);
    expect(await insideViewport(page, note(page))).toBe(true);
    expect(await drawnAtItsCorners(note(page))).toBe(true);
    await shot(chainDialog(page), "edit-loras-save");

    await chainDialog(page).getByRole("button", { name: "Cancel" }).click();
    await expect(chainDialog(page)).toBeHidden();
    await expect(editLoras(page)).toBeFocused();
    // Cancelled: nothing was written anywhere.
    expect(net.chainWrites).toEqual([]);
    expect(net.changePuts).toEqual([]);
    await expect(bar(page)).toBeHidden();
  });
});

// ── 7. What only a browser shows: locks, order, and the stale sentence ──────

test.describe("a save that is slow", () => {
  test("locks every row control until it answers, and an edit made meanwhile goes nowhere", async ({ page }) => {
    net = await stubWorkflows(page);
    await selectWorkflow(page);
    await stage(page, "Face detailer").click();
    const save = bar(page).getByRole("button", { name: "Save", exact: true });
    await expect(save).toBeVisible();
    await expect(save).toBeEnabled();
    await expect.poll(() => net.changePuts.length).toBe(1);

    net.hold.save = gate();
    await save.click();
    await expect.poll(() => net.saves.length).toBe(1);
    // Out: nothing that edits the set can be reached, and the verbs wait too.
    await expect(rail(page).getByRole("button", { name: "Change model set…" })).toBeDisabled();
    await expect(stage(page, "Upscale")).toBeDisabled();
    await expect(stage(page, "Face detailer")).toBeDisabled();
    await expect(bar(page).getByRole("button", { name: "Discard" })).toBeDisabled();
    await expect(bar(page).getByRole("button", { name: "Save as new…" })).toBeDisabled();
    // The LoRA rows' controls are in Edit LoRAs, which opens from the rail
    // while the save is out.
    await openLoras(page);
    await expect(strength(page, "detail_slider_v2")).toBeDisabled();
    await expect(lorasDialog(page).getByRole("button", { name: "Remove film-grain-35mm" })).toBeDisabled();
    // Pressed anyway, by a pointer: nothing was edited, and nothing was sent.
    await lorasDialog(page).getByRole("button", { name: "Remove film-grain-35mm" }).click({ force: true });
    await expect(lorasDialog(page).getByRole("button", { name: "Put film-grain-35mm back" })).toHaveCount(0);
    await doneLoras(page);
    await stage(page, "Upscale").click({ force: true });
    await expect(stage(page, "Upscale")).toHaveAttribute("aria-pressed", "true");
    expect(net.changePuts).toHaveLength(1);

    net.hold.save.release();
    await expect(bar(page)).toBeHidden();
    await expect(stage(page, "Upscale")).toBeEnabled();
    await openLoras(page);
    await expect(strength(page, "detail_slider_v2")).toBeEnabled();
    await doneLoras(page);
    expect(net.saves).toEqual([{ version: 3 }]);
    // And an edit can be made again, which is one write more.
    await stage(page, "Upscale").click();
    await expect(bar(page)).toContainText("1 unsaved change");
    expect(net.changePuts).toHaveLength(2);
    expect(net.changePuts[1].stages).toEqual({ upscale: false });
  });

  test("a strength typed and Save pressed in one gesture send the edit first, then the save", async ({ page }) => {
    net = await stubWorkflows(page);
    await selectWorkflow(page);
    // The bar is up with its verbs before the strength is typed.
    await stage(page, "Face detailer").click();
    const save = bar(page).getByRole("button", { name: "Save", exact: true });
    await expect(save).toBeVisible();
    await expect.poll(() => net.changePuts.length).toBe(1);

    // The strength's write is held on the wire. Done blurs the field (the
    // edit), and Save is pressed while that write is out.
    net.hold.put = gate();
    await openLoras(page);
    await strength(page, "detail_slider_v2").fill("0.45");
    await doneLoras(page);
    await save.click();
    await expect.poll(() => net.order).toEqual(["put", "put"]);
    expect(net.saves).toEqual([]);
    net.hold.put.release();

    await expect(bar(page)).toBeHidden();
    expect(net.order).toEqual(["put", "put", "save"]);
    expect(net.saves).toEqual([{ version: 3 }]);
    expect(net.changePuts.at(-1).loras).toEqual([
      {
        node_id: "7",
        field: "lora_name",
        sha256: SHA.detail,
        strength_model: 0.45,
        strength_clip: 0.45,
      },
    ]);
    expect(net.changePuts.at(-1).stages).toEqual({ face_detailer: true });
  });
});

test.describe("going back a version with changes waiting", () => {
  test("the note under Open in ComfyUI says the changes are not in what opens", async ({ page }) => {
    net = await stubWorkflows(page, { comfyui: true });
    await selectWorkflow(page);
    await expect(rail(page).getByTestId("wftab-open-comfyui")).toBeVisible();
    await stage(page, "Face detailer").click();
    await expect(bar(page)).toContainText("1 unsaved change");
    const note = rail(page).getByTestId("wftab-open-unsaved");
    await expect(note).toHaveText(
      "Open in ComfyUI opens the workflow with the unsaved changes in it. Saving there saves them too.",
    );

    await chip(page).click();
    await versionRow(page, 2).click();
    const actions = versionActions(page, 2);
    // With ComfyUI set, the version can be opened there without going back.
    await expect(actions.getByRole("menuitem", { name: "Open in ComfyUI" })).toBeVisible();
    await actions.getByRole("menuitem", { name: "Go back to this version" }).click();
    await expect(chip(page)).toHaveText("Version 4 · today");
    expect(net.restores).toEqual([2]);
    // The changes are still there, and now say where they were made.
    await expect(bar(page)).toContainText("1 unsaved change");
    await expect(note).toHaveText(
      "Open in ComfyUI opens the workflow as it is now. The unsaved changes were made on an earlier version and are not in it.",
    );
  });
});

test.describe("Open in ComfyUI on the card menu", () => {
  const cardMenu = (page) => page.getByTestId("wf-verbs").filter({ visible: true });
  const opener = (page) => cardMenu(page).getByRole("menuitem", { name: "Open in ComfyUI" });
  const sub = (page) => page.getByRole("menu", { name: "Open in ComfyUI" });

  test("opens a submenu that keeps the menu open, by pointer and by keyboard", async ({ page }) => {
    net = await stubWorkflows(page, { comfyui: true });
    await selectWorkflow(page);
    // What would open, without opening a tab on a ComfyUI that is not there.
    await page.evaluate(() => {
      window.__opened = [];
      window.open = (url) => {
        window.__opened.push(url);
        return null;
      };
    });
    const opened = () => page.evaluate(() => window.__opened);

    await cardRow(page).click({ button: "right" });
    await expect(opener(page)).toBeVisible();
    // A click on the row opens the submenu; a v-menu closes on any other click.
    await opener(page).click();
    await expect(sub(page)).toBeVisible();
    await expect(opener(page)).toHaveAttribute("aria-expanded", "true");
    await sub(page).getByRole("menuitem", { name: "Original" }).click();
    await expect(cardMenu(page)).toHaveCount(0);
    let [url] = await opened();
    expect(new URL(url).searchParams.get("pixlstash_workflow")).toBe(WF);
    expect(new URL(url).searchParams.get("pixlstash_original")).toBe("1");

    // Hover opens it too; and it sits inside the window.
    await cardRow(page).click({ button: "right" });
    await opener(page).hover();
    await expect(sub(page)).toBeVisible();
    const box = await sub(page).boundingBox();
    expect(box.x + box.width).toBeLessThanOrEqual(page.viewportSize().width);
    await shotAround(page, [cardMenu(page), sub(page)], "card-menu-comfyui");
    await page.keyboard.press("Escape");
    await expect(cardMenu(page)).toHaveCount(0);

    await cardRow(page).click({ button: "right" });
    await opener(page).focus();
    await page.keyboard.press("ArrowRight");
    const adapted = sub(page).getByRole("menuitem", { name: "Adapted for PixlStash" });
    await expect(adapted).toBeFocused();
    await page.keyboard.press("ArrowLeft");
    await expect(opener(page)).toBeFocused();
    await expect(sub(page)).toHaveCount(0);
    // Space opens it on its first row and opens nothing in ComfyUI yet.
    await page.keyboard.press(" ");
    await expect(adapted).toBeFocused();
    expect(await opened()).toHaveLength(1);
    await page.keyboard.press("ArrowLeft");
    await page.keyboard.press("Enter");
    await expect(adapted).toBeFocused();
    expect(await opened()).toHaveLength(1);
    await page.keyboard.press("Enter");
    await expect(cardMenu(page)).toHaveCount(0);
    url = (await opened())[1];
    expect(new URL(url).searchParams.get("pixlstash_workflow")).toBe(WF);
    expect(new URL(url).searchParams.has("pixlstash_original")).toBe(false);
  });
});
