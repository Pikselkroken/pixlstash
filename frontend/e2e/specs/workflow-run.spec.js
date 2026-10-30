import { readFileSync } from "node:fs";

import { test, expect } from "../fixtures/test.js";
import {
  WF,
  PEOPLE,
  stubWorkflow,
  dialog,
  tile,
  runButton,
  openPopup,
  selectWorkflow,
} from "../fixtures/workflowStubs.js";

// Running a workflow, after Run is pressed (#2318): a refused pre-flight, the
// run followed in the Tasks tab to its pictures, Run anyway, cancelling,
// dismissing and clearing, and Open in ComfyUI.
//
// **ComfyUI's stand-in is route fakes, not a fake ComfyUI server** (option (b)
// of #2318). The e2e library has no workflow a fake ComfyUI could run (#2314),
// so the routes a run talks to are answered here: the pre-flight, the run,
// the opening, `/workflows/runs*`, the lease actions under `/resources/leases`,
// ComfyUI's pack check, and the `runs`, `finished` and `resources` of
// `/workers/progress` (the rest of the poll is the real backend's). Every
// answer comes from `workflowRunFakes.json`, which
// `tests/test_e2e_workflow_run_fakes.py` holds against the server's response
// models and the code that builds the poll, so a fake that drifts from the
// server reds the backend gate even while this spec stays green. Answers here
// change a fake's values, never its keys: a key added here escapes that guard.
//
// The popup and the inspector behind it are `workflowStubs.js`'s, as in
// `run-popup-save.spec.js`.

const FAKES = JSON.parse(
  readFileSync(new URL("../fixtures/workflowRunFakes.json", import.meta.url), "utf8"),
);
/** Never a real ComfyUI: `.test` does not resolve, and the tab is answered here. */
const COMFYUI = "http://comfyui.test:8188";
const LABEL = FAKES.liveRun.label;
const LEASE_ID = FAKES.resources.leases[0].id;

/** A finished run, as the poll lists it, ended just now. */
const finishedRun = (overrides = {}) => ({
  ...FAKES.finished,
  ended_at: new Date().toISOString(),
  ...overrides,
});

/**
 * Answer the run's routes from *world*, which a test changes as the run goes
 * on, and record what was sent. Registered after `stubWorkflow`, so it is
 * asked first; anything it does not answer falls back to that.
 */
async function stubRun(page, world = {}) {
  const sent = {
    world: {
      /** The pre-flight's answer, or "real" to ask the e2e backend. */
      preflight: FAKES.preflight,
      live: [],
      finished: [],
      resources: null,
      comfyui: false,
      ...world,
    },
    runs: [],
    openings: [],
    anyway: [],
    cancelled: [],
    dismissed: [],
    clears: 0,
  };
  await page.route(
    (url) => url.pathname.startsWith("/api/v1/"),
    async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const path = decodeURIComponent(url.pathname).slice("/api/v1".length);
      const method = request.method();
      const { world: now } = sent;
      const json = (body) => route.fulfill({ json: body });

      if (method === "POST" && path === "/workflows/run/preflight") {
        if (now.preflight === "real") return route.fulfill({ response: await route.fetch() });
        return json(now.preflight);
      }
      if (method === "POST" && path === "/workflows/run") {
        sent.runs.push(request.postDataJSON());
        // ComfyUI has it now: the poll lists it as running.
        now.live = [FAKES.liveRun];
        return json(FAKES.run);
      }
      if (method === "POST" && path === "/workflows/run/opening") {
        sent.openings.push(request.postDataJSON());
        return json(FAKES.opening);
      }
      if (method === "DELETE" && path === "/workflows/runs") {
        sent.clears += 1;
        const kept = now.finished.filter((run) => run.offer);
        const cleared = now.finished.length - kept.length;
        now.finished = kept;
        return json({ ...FAKES.cleared, cleared });
      }
      const dismissed = /^\/workflows\/runs\/([^/]+)$/.exec(path);
      if (method === "DELETE" && dismissed) {
        sent.dismissed.push(dismissed[1]);
        now.finished = now.finished.filter((run) => run.id !== dismissed[1]);
        return json(FAKES.cleared);
      }
      const lease = /^\/resources\/leases\/([^/]+)\/(run-anyway|cancel)$/.exec(path);
      if (method === "POST" && lease) {
        (lease[2] === "cancel" ? sent.cancelled : sent.anyway).push(lease[1]);
        // Started or withdrawn, it is not waiting any more.
        now.resources = null;
        return json(FAKES.leaseAnswer);
      }
      if (method === "GET" && path === "/workers/progress") {
        const real = await route.fetch();
        return route.fulfill({
          response: real,
          json: {
            ...(await real.json()),
            runs: now.live,
            finished: now.finished,
            resources: now.resources,
          },
        });
      }
      if (now.comfyui && method === "GET" && path === "/users/me/config") {
        const real = await route.fetch();
        return route.fulfill({
          response: real,
          json: { ...(await real.json()), comfyui_url: COMFYUI },
        });
      }
      if (now.comfyui && method === "GET" && path === "/comfyui/pixlstash-node") {
        return json(FAKES.pixlstashNode);
      }
      return route.fallback();
    },
  );
  return sent;
}

const inspector = (page) => page.getByRole("complementary", { name: "Inspector" });
const tasksPanel = (page) => page.getByTestId("titlebar-tasks-panel");

/** The Workflows screen with the title bar's Tasks pill open. */
async function openTasks(page) {
  await selectWorkflow(page);
  await page.getByTestId("titlebar-tasks").click();
  await expect(tasksPanel(page)).toBeVisible();
}

/**
 * Press a Run that refuses, and wait out the run it must not send. The click
 * is dispatched on the button: Playwright will not press an `aria-disabled`
 * one itself, and a forced click goes to coordinates, which land on the scrim
 * while the dialog is still scaling in.
 */
async function pressRefusedRun(page) {
  const posted = page
    .waitForRequest(
      (request) => request.method() === "POST" && request.url().endsWith("/api/v1/workflows/run"),
      { timeout: 1000 },
    )
    .then(
      () => true,
      () => false,
    );
  await runButton(page).dispatchEvent("click");
  expect(await posted, "Run sent a run it was refusing").toBe(false);
}

let net;
test.afterEach(() => {
  expect(net?.unstubbed ?? [], "workflow routes nobody stubbed").toEqual([]);
});

test.describe("a refused pre-flight", () => {
  test("says why, and Run cannot be pressed", async ({ page }) => {
    net = await stubWorkflow(page);
    const sent = await stubRun(page, { preflight: FAKES.refused });
    await openPopup(page);
    await expect(dialog(page)).toContainText("PixlStash has no ComfyUI address to run this on.");
    await expect(dialog(page).locator("footer")).toContainText("This run cannot start; see below.");
    await expect(runButton(page)).toHaveAttribute("aria-disabled", "true");

    await pressRefusedRun(page);
    await expect(dialog(page)).toBeVisible();
    expect(sent.runs).toEqual([]);
  });

  test("prints the server's own words for a body it refuses outright", async ({ page }) => {
    net = await stubWorkflow(page);
    // The e2e backend has no such workflow, and answers so itself.
    const sent = await stubRun(page, { preflight: "real" });
    const refusal = page.waitForResponse(
      (response) => response.url().endsWith("/workflows/run/preflight"),
    );
    await selectWorkflow(page);
    await inspector(page).getByRole("button", { name: /^Run( with changes)?…$/ }).click();
    const answer = await refusal;
    expect(answer.status()).toBeGreaterThanOrEqual(400);
    expect(answer.status()).toBeLessThan(500);
    const { detail } = await answer.json();
    expect(typeof detail).toBe("string");

    await expect(dialog(page).locator("footer")).toContainText(detail);
    await expect(runButton(page)).toHaveAttribute("aria-disabled", "true");
    await pressRefusedRun(page);
    await expect(dialog(page)).toBeVisible();
    expect(sent.runs).toEqual([]);
  });
});

test.describe("Run", () => {
  test("closes the popup, and the Tasks tab follows the run to its pictures", async ({ page }) => {
    net = await stubWorkflow(page);
    const sent = await stubRun(page);
    await openPopup(page);
    await runButton(page).click();
    await expect(dialog(page)).toHaveCount(0);
    expect(sent.runs).toHaveLength(1);
    expect(sent.runs[0].workflow_id).toBe(WF);

    // The notice's Show is the way to the run: the title bar's Tasks pill.
    await page
      .locator(".notice-card")
      .filter({ hasText: "Started 1 run in ComfyUI." })
      .getByRole("button", { name: "Show" })
      .click();
    const row = tasksPanel(page).getByTestId("tm-comfy-row");
    await expect(row).toContainText(LABEL);
    await expect(row.locator(".tm-mark")).toHaveClass(/tm-mark--running/);
    await row.hover();
    await expect(page.getByText("ComfyUI running…, 40%")).toBeVisible();
    sent.world.live = [{ ...FAKES.liveRun, percent: 75 }];
    await expect(page.getByText("ComfyUI running…, 75%")).toBeVisible();

    // It ends with two pictures: the row moves to Finished and shows them.
    sent.world.live = [];
    sent.world.finished = [finishedRun()];
    await expect(row).toHaveCount(0);
    const done = tasksPanel(page).getByTestId("tm-finished-run");
    await expect(done).toContainText(LABEL);
    await tasksPanel(page).getByRole("button", { name: `Show the run of ${LABEL}` }).click();
    await expect(done).toContainText("Finished just now.");
    const tiles = done.getByTestId("tm-finished-tile");
    await expect(tiles).toHaveCount(2);
    await expect(tiles.first()).toHaveAccessibleName("Open picture 1 of 2");
    await expect(tiles.first().locator("img")).toHaveAttribute("src", /\/1\.\w+(\?|$)/);
  });

  test("sends every person picked, in the order picked", async ({ page }) => {
    net = await stubWorkflow(page);
    const sent = await stubRun(page);
    await openPopup(page);
    await tile(page, "Bea").click();
    await tile(page, "Ada").click();
    await runButton(page).click();
    await expect(dialog(page)).toHaveCount(0);

    const [bea, ada] = [PEOPLE[1], PEOPLE[0]];
    expect(sent.runs).toHaveLength(1);
    expect(sent.runs[0].add_loras.map((lora) => lora.sha256)).toEqual([
      bea.lora.sha256,
      ada.lora.sha256,
    ]);
  });
});

test.describe("Run anyway", () => {
  test("Keep waiting sends nothing, and Run anyway starts the waiting run", async ({ page }) => {
    net = await stubWorkflow(page);
    const sent = await stubRun(page, { resources: FAKES.resources });
    await openTasks(page);
    const row = tasksPanel(page).getByTestId("tm-waiting-row");
    await expect(row).toContainText(LABEL);
    const ask = tasksPanel(page).getByRole("button", { name: `Run ${LABEL} anyway` });

    await row.hover();
    await ask.click();
    const confirm = page.getByTestId("ra-run-anyway");
    await expect(confirm).toBeVisible();
    await page.getByTestId("ra-keep-waiting").click();
    await expect(confirm).toHaveCount(0);
    await expect(row).toBeVisible();
    expect(sent.anyway).toEqual([]);

    await row.hover();
    await ask.click();
    await confirm.click();
    await expect(confirm).toHaveCount(0);
    await expect(row).toHaveCount(0);
    expect(sent.anyway).toEqual([LEASE_ID]);
    expect(sent.cancelled).toEqual([]);
  });
});

test.describe("the Tasks tab", () => {
  test("cancels a run waiting for the GPU", async ({ page }) => {
    net = await stubWorkflow(page);
    const sent = await stubRun(page, { resources: FAKES.resources });
    await openTasks(page);
    const row = tasksPanel(page).getByTestId("tm-waiting-row");
    await row.hover();
    await tasksPanel(page).getByRole("button", { name: `Cancel ${LABEL}` }).click();
    await expect(row).toHaveCount(0);
    expect(sent.cancelled).toEqual([LEASE_ID]);
    expect(sent.anyway).toEqual([]);
  });

  test("dismisses one finished run and keeps the other", async ({ page }) => {
    net = await stubWorkflow(page);
    const other = finishedRun({ id: "run-e2e-other", label: "Night street, rain" });
    const sent = await stubRun(page, { finished: [finishedRun(), other] });
    await openTasks(page);
    const rows = tasksPanel(page).getByTestId("tm-finished-run");
    await expect(rows).toHaveCount(2);

    const first = rows.filter({ hasText: LABEL });
    await first.hover();
    await first.getByRole("button", { name: `Clear the run of ${LABEL}` }).click();
    await expect(rows).toHaveCount(1);
    await expect(rows).toContainText(other.label);
    expect(sent.dismissed).toEqual([FAKES.finished.id]);
    expect(sent.clears).toBe(0);
  });

  test("Clear takes every finished run away", async ({ page }) => {
    net = await stubWorkflow(page);
    const sent = await stubRun(page, {
      finished: [finishedRun(), finishedRun({ id: "run-e2e-other", label: "Night street, rain" })],
    });
    await openTasks(page);
    const rows = tasksPanel(page).getByTestId("tm-finished-run");
    await expect(rows).toHaveCount(2);
    await tasksPanel(page).getByTestId("tm-finished-clear").click();
    await expect(rows).toHaveCount(0);
    await expect(tasksPanel(page)).toContainText("No active tasks");
    expect(sent.clears).toBe(1);
    expect(sent.dismissed).toEqual([]);
  });
});

test("Open in ComfyUI opens the run there, and queues nothing", async ({ page, context }) => {
  const opened = [];
  await context.route(`${COMFYUI}/**`, (route) => {
    opened.push(route.request().url());
    return route.fulfill({ contentType: "text/html", body: "<title>ComfyUI</title>" });
  });
  net = await stubWorkflow(page);
  const sent = await stubRun(page, { comfyui: true });
  await openPopup(page);

  const tab = page.waitForEvent("popup");
  await dialog(page).getByTestId("rund-open-comfyui").click();
  const comfyui = await tab;
  const want = new URL(`${COMFYUI}/`);
  want.searchParams.set("pixlstash_run", FAKES.opening.opening);
  want.searchParams.set("pixlstash_of", WF);
  await comfyui.waitForURL(want.toString());
  expect(opened).toEqual([want.toString()]);

  expect(sent.openings).toHaveLength(1);
  expect(sent.openings[0]).toMatchObject({ workflow_id: WF, count: 1 });
  // The popup stays: the run is still to make.
  await expect(dialog(page)).toBeVisible();
  expect(sent.runs).toEqual([]);
});
