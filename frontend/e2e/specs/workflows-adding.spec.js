import { readFileSync } from "node:fs";

import { test, expect } from "../fixtures/test.js";

// The two ways a workflow enters the library, against the real backend (#2317):
// a file (the empty state's button, the sidebar's Add workflow, a drop) and a
// shipped template. Neither needs ComfyUI: the import only validates and
// stores the document, and with no ComfyUI answering `GET /comfyui/templates`
// lists the templates PixlStash ships.
//
// Order matters, and Playwright keeps it (one worker, file order): the empty
// state is only empty before the first import, and Connect ComfyUI is only
// offered before the Templates block saves an address. So the empty-state test
// stores nothing, and every later test works whatever the library holds, which
// keeps a retry of any one of them honest.
//
// Nothing here deletes a workflow, because a delete trashes through the shared
// inbox (#2313). The same issue is why the empty state can see a machine's own
// inbox: until it is fixed, the e2e backend imports what that folder holds at
// start-up, and a file there fails the empty-state test.

const WORKFLOWS = "/api/v1/workflows";
const IMPORT = "/api/v1/comfyui/workflows/import";
const TEMPLATES = "/api/v1/comfyui/templates";

/** A minimal API-format graph: one node with a class and inputs. */
const graph = (seed) => ({ 3: { class_type: "KSampler", inputs: { seed } } });

/**
 * A seed no earlier run against this backend used: a copy of a stored graph
 * is refused (#2347), so a retry must not meet its own first attempt.
 */
const fresh = () => Date.now() * 1000 + Math.floor(Math.random() * 1000);

/** A minimal editor-format document, laid out at *x*. */
const editorDoc = (seed, x = 0) => ({
  last_node_id: 3,
  last_link_id: 0,
  nodes: [
    {
      id: 3,
      type: "KSampler",
      pos: [x, 40],
      size: [315, 262],
      mode: 0,
      inputs: [],
      outputs: [],
      properties: { "Node name for S&R": "KSampler" },
      widgets_values: [seed, "fixed", 20, 8, "euler", "normal", 1],
    },
  ],
  links: [],
  groups: x ? [{ title: "Moved", bounding: [x, 0, 400, 400] }] : [],
  extra: { ds: { scale: 1, offset: [x, 0] } },
  version: 0.4,
});

/** Store *workflow* as *name* through the import route; its id. */
async function stored(apiContext, name, workflow) {
  const res = await apiContext.post(IMPORT, { data: { name, workflow } });
  expect(res.ok(), await res.text()).toBe(true);
  return (await res.json()).workflow_id;
}

const alreadyHere = (page) => page.getByTestId("wfv-already-here");

/** The ids `GET /workflows` lists, straight from the server. */
async function workflowIds(apiContext) {
  const res = await apiContext.get(WORKFLOWS);
  expect(res.ok()).toBe(true);
  return (await res.json()).cards.map((card) => card.id);
}

const row = (page, id) => page.locator(`.wfv-row[data-key="${id}"]`);
const announcement = (page) => page.locator('.wfv > p[role="status"]');
const importPosts = (page) => {
  const seen = [];
  page.on("request", (req) => {
    if (req.method() === "POST" && new URL(req.url()).pathname === IMPORT) {
      seen.push(req);
    }
  });
  return seen;
};

/**
 * Drop *files* on the grid as the OS would: `dragenter`, `dragover` and
 * `drop` on the scroller, carrying a real `DataTransfer` of `File`s, bubbling
 * to the view's own listeners. A file given a `size` is a JSON object padded
 * to that many bytes, so a size guard that let it through would be seen
 * sending it rather than failing to parse it.
 */
async function dropFiles(page, files) {
  const transfer = await page.evaluateHandle((list) => {
    const dt = new DataTransfer();
    for (const f of list) {
      const body = f.size ? ['{"a":"', "a".repeat(f.size - 8), '"}'] : [f.text];
      dt.items.add(new File(body, f.name, { type: "application/json" }));
    }
    return dt;
  }, files);
  const target = page.locator(".wfv-scroll, .wfv-empty").first();
  await target.dispatchEvent("dragenter", { dataTransfer: transfer });
  // The wash is the view's dragenter handler answering a drag of files.
  await expect(page.locator(".wfv")).toHaveClass(/wfv--drop/);
  await target.dispatchEvent("dragover", { dataTransfer: transfer });
  await target.dispatchEvent("drop", { dataTransfer: transfer });
  await expect(page.locator(".wfv")).not.toHaveClass(/wfv--drop/);
  await transfer.dispose();
}

test.describe("empty state", () => {
  test("an unseeded library offers a file and ComfyUI, and Connect opens its pane", async ({
    page,
    apiContext,
  }) => {
    // The state this test is about, checked rather than assumed: if anything
    // earlier in the run stored a workflow, this says so instead of failing
    // on a missing button.
    expect(await workflowIds(apiContext)).toEqual([]);
    await page.goto("/workflows");
    const empty = page.locator(".wfv-empty");
    await expect(empty).toBeVisible({ timeout: 15_000 });
    // Its button is the file picker, one file or several.
    const chooser = page.waitForEvent("filechooser");
    await empty.getByRole("button", { name: "Drop a workflow file" }).click();
    expect((await chooser).isMultiple()).toBe(true);

    await empty.getByRole("button", { name: "Connect ComfyUI" }).click();
    await expect(page.locator("#settings-nav-comfyui")).toHaveAttribute(
      "aria-current",
      "page",
    );
    await expect(page.locator("#settings-pane-comfyui")).toBeVisible();
  });
});

test.describe("import", () => {
  test("Add workflow takes a file; its card lands selected and survives a reload", async ({
    page,
    apiContext,
  }) => {
    await page.goto("/workflows");
    const button = page.getByRole("button", { name: "Add workflow" });
    await expect(button).toBeVisible({ timeout: 15_000 });
    const chooser = page.waitForEvent("filechooser");
    await button.click();
    const imported = page.waitForResponse(
      (res) => new URL(res.url()).pathname === IMPORT,
    );
    await (await chooser).setFiles({
      name: "First import.json",
      mimeType: "application/json",
      buffer: Buffer.from(JSON.stringify(graph(fresh()))),
    });
    const body = await (await imported).json();
    expect(body.name).toBe("First import");

    const card = row(page, body.workflow_id);
    await expect(card).toHaveAttribute("aria-selected", "true");
    await expect(card.locator(".wf-card__name")).toHaveText("First import");
    await expect(announcement(page)).toHaveText("Added First import");
    expect(await workflowIds(apiContext)).toContain(body.workflow_id);

    await page.reload();
    await expect(row(page, body.workflow_id)).toBeVisible({ timeout: 15_000 });
  });

  test("a drop of two files imports both and announces the count", async ({
    page,
  }) => {
    await page.goto("/workflows");
    await expect(page.locator(".wfv-scroll")).toBeVisible({ timeout: 15_000 });
    const posts = importPosts(page);
    // Neither file is the window-wide importer's: a drop of JSON alone is
    // this view's, and nothing goes to the picture import.
    const otherImports = [];
    page.on("request", (req) => {
      const path = new URL(req.url()).pathname;
      if (req.method() === "POST" && /import/.test(path) && path !== IMPORT) {
        otherImports.push(path);
      }
    });
    const responses = [];
    page.on("response", async (res) => {
      if (new URL(res.url()).pathname === IMPORT) responses.push(res);
    });

    await dropFiles(page, [
      { name: "Dropped one.json", text: JSON.stringify(graph(fresh())) },
      { name: "Dropped two.json", text: JSON.stringify(graph(fresh())) },
    ]);
    await expect(announcement(page)).toHaveText("Added 2 workflows");
    expect(posts).toHaveLength(2);
    expect(otherImports).toEqual([]);

    const ids = await Promise.all(
      responses.map(async (res) => (await res.json()).workflow_id),
    );
    for (const id of ids) await expect(row(page, id)).toBeVisible();
    // The last to land is the one selected.
    await expect(row(page, ids[1])).toHaveAttribute("aria-selected", "true");
  });

  test("an oversized file, invalid JSON and JSON that is not a workflow are each refused, and none makes a card", async ({
    page,
    apiContext,
  }) => {
    await page.goto("/workflows");
    await expect(page.locator(".wfv-scroll")).toBeVisible({ timeout: 15_000 });
    const before = await workflowIds(apiContext);
    const posts = importPosts(page);
    const alert = page.locator(".wfv-error");

    // MAX_WORKFLOW_BYTES (50 MiB) and one byte: refused before it is read.
    await dropFiles(page, [{ name: "huge.json", size: 50 * 1024 * 1024 + 1 }]);
    await expect(alert).toHaveText("huge.json is too large to be a workflow.");
    expect(posts).toHaveLength(0);

    // Not JSON: the parse fails in the browser, so nothing is sent either.
    await dropFiles(page, [{ name: "broken.json", text: "{ not json" }]);
    await expect(alert).toContainText("JSON");
    expect(posts).toHaveLength(0);

    // JSON, but no ComfyUI graph: the server refuses it with its reason.
    const refused = page.waitForResponse(
      (res) => new URL(res.url()).pathname === IMPORT,
    );
    await dropFiles(page, [
      { name: "notes.json", text: JSON.stringify({ hello: "world" }) },
    ]);
    expect((await refused).status()).toBe(400);
    await expect(alert).toContainText("not a ComfyUI workflow");

    // Only the last of the three was ever sent.
    expect(posts.map((req) => req.postDataJSON().name)).toEqual(["notes"]);
    expect(await workflowIds(apiContext)).toEqual(before);
  });

  test("a file already here is refused: the view lands on its card and the note names it", async ({
    page,
    apiContext,
  }) => {
    const seed = fresh();
    const id = await stored(apiContext, `Here already ${seed}`, graph(seed));
    await page.goto("/workflows");
    await expect(row(page, id)).toBeVisible({ timeout: 15_000 });
    const before = await workflowIds(apiContext);

    const refused = page.waitForResponse(
      (res) => new URL(res.url()).pathname === IMPORT,
    );
    await dropFiles(page, [
      { name: "Twice.json", text: JSON.stringify(graph(seed)) },
    ]);
    const res = await refused;
    expect(res.status()).toBe(409);
    expect((await res.json()).detail).toMatchObject({
      code: "workflow_exists",
      workflow_id: id,
      name: `Here already ${seed}`,
    });

    await expect(row(page, id)).toHaveAttribute("aria-selected", "true");
    await expect(row(page, id)).toBeFocused();
    await expect(alreadyHere(page)).toContainText(
      `Twice.json is already here as Here already ${seed}, so it was not added again.`,
    );
    await expect(announcement(page)).toHaveText(
      `Twice.json is already here as Here already ${seed}`,
    );
    // Not an error: nothing failed, the file is simply here already.
    await expect(page.locator(".wfv-error")).toHaveCount(0);
    expect(await workflowIds(apiContext)).toEqual(before);
  });

  test("a layout-only difference is still the same workflow", async ({
    page,
    apiContext,
  }) => {
    const seed = fresh();
    const id = await stored(apiContext, `Laid out ${seed}`, editorDoc(seed));
    await page.goto("/workflows");
    await expect(row(page, id)).toBeVisible({ timeout: 15_000 });
    const refused = page.waitForResponse(
      (res) => new URL(res.url()).pathname === IMPORT,
    );
    // The node moved, a group drawn round it, the canvas panned.
    await dropFiles(page, [
      { name: "Moved.json", text: JSON.stringify(editorDoc(seed, 640)) },
    ]);
    expect((await refused).status()).toBe(409);
    await expect(row(page, id)).toHaveAttribute("aria-selected", "true");
    await expect(alreadyHere(page)).toContainText(`as Laid out ${seed}`);
  });

  test("once the workflow is saved over, its original file imports as a new workflow", async ({
    page,
    apiContext,
  }) => {
    const seed = fresh();
    const original = graph(seed);
    const id = await stored(apiContext, `Edited ${seed}`, original);
    const saved = await apiContext.put(
      `${WORKFLOWS}/${encodeURIComponent(id)}/graph`,
      {
        data: {
          workflow: { 3: { class_type: "KSampler", inputs: { seed, steps: 41 } } },
          read_version: 1,
        },
      },
    );
    expect(saved.ok(), await saved.text()).toBe(true);

    await page.goto("/workflows");
    await expect(row(page, id)).toBeVisible({ timeout: 15_000 });
    const imported = page.waitForResponse(
      (res) => new URL(res.url()).pathname === IMPORT,
    );
    await dropFiles(page, [
      { name: "Original.json", text: JSON.stringify(original) },
    ]);
    const res = await imported;
    expect(res.status()).toBe(200);
    const added = (await res.json()).workflow_id;
    expect(added).not.toBe(id);
    await expect(row(page, added)).toHaveAttribute("aria-selected", "true");
    await expect(announcement(page)).toHaveText("Added Original");
    await expect(alreadyHere(page)).toHaveCount(0);
  });

  test("a drop with one file already here adds the others and names the one", async ({
    page,
    apiContext,
  }) => {
    const seed = fresh();
    const id = await stored(apiContext, `In the drop ${seed}`, graph(seed));
    await page.goto("/workflows");
    await expect(row(page, id)).toBeVisible({ timeout: 15_000 });
    const responses = [];
    page.on("response", (res) => {
      if (new URL(res.url()).pathname === IMPORT) responses.push(res);
    });

    await dropFiles(page, [
      { name: "New one.json", text: JSON.stringify(graph(fresh())) },
      { name: "Dup.json", text: JSON.stringify(graph(seed)) },
      { name: "New two.json", text: JSON.stringify(graph(fresh())) },
    ]);
    await expect(announcement(page)).toHaveText(
      `Added 2 workflows; 1 is already here as In the drop ${seed}`,
    );
    expect(responses.map((res) => res.status())).toEqual([200, 409, 200]);
    const last = (await responses[2].json()).workflow_id;
    // On the last new card; the note points at the one already here. (Which
    // card pulses is the unit suite's: the pulse is gone within a second.)
    await expect(row(page, last)).toHaveAttribute("aria-selected", "true");
    await expect(alreadyHere(page)).toContainText(
      `Dup.json is already here as In the drop ${seed}`,
    );
  });

  test("a file that is a hidden workflow turns Show hidden on to reach it", async ({
    page,
    apiContext,
  }) => {
    const seed = fresh();
    const id = await stored(apiContext, `Hidden ${seed}`, graph(seed));
    const hidden = await apiContext.patch(
      `${WORKFLOWS}/${encodeURIComponent(id)}`,
      { data: { hidden: true } },
    );
    expect(hidden.ok(), await hidden.text()).toBe(true);
    await page.goto("/workflows");
    await expect(page.locator(".wfv-scroll")).toBeVisible({ timeout: 15_000 });
    await expect(row(page, id)).toHaveCount(0);

    await dropFiles(page, [
      { name: "Hidden.json", text: JSON.stringify(graph(seed)) },
    ]);
    await expect(row(page, id)).toHaveAttribute("aria-selected", "true", {
      timeout: 15_000,
    });
    await expect(alreadyHere(page)).toContainText(
      "It is hidden, so hidden workflows are shown now.",
    );
  });
});

test.describe("templates tab", () => {
  // An address nothing answers on (the discard port), so the list is the
  // shipped one on every machine. With no address saved the tab would show
  // its set-up state, and the server would read the default address, which
  // on a developer's machine is their own running ComfyUI.
  const NOWHERE = "http://127.0.0.1:9";
  let shipped = [];

  /** The owner's API, outside a test: hooks that run once get no fixture. */
  async function ownerApi(playwright, baseURL) {
    const { token } = JSON.parse(
      readFileSync(new URL("../.auth/token.json", import.meta.url), "utf8"),
    );
    return playwright.request.newContext({
      baseURL,
      extraHTTPHeaders: { Authorization: `Bearer ${token}` },
    });
  }

  test.beforeAll(async ({ playwright, baseURL }) => {
    const api = await ownerApi(playwright, baseURL);
    const saved = await api.patch("/api/v1/users/me/config", {
      data: { comfyui_url: NOWHERE },
    });
    expect(saved.ok()).toBe(true);
    const listed = await (await api.get(TEMPLATES)).json();
    expect(listed.reachable).toBe(false);
    // Partner templates are hidden until asked for.
    shipped = listed.templates.filter((t) => t.source !== "partner");
    expect(shipped.length).toBeGreaterThan(2);
    await api.dispose();
  });

  test.afterAll(async ({ playwright, baseURL }) => {
    const api = await ownerApi(playwright, baseURL);
    const cleared = await api.patch("/api/v1/users/me/config", {
      data: { comfyui_url: null },
    });
    expect(cleared.ok()).toBe(true);
    await api.dispose();
  });

  test.beforeEach(async ({ page }) => {
    // The shipped previews come from GitHub through the server; none is
    // under test, and the cards draw their no-preview state without one.
    await page.route(
      (url) => url.pathname === `${TEMPLATES}/thumbnail`,
      (route) => route.fulfill({ status: 404, body: "" }),
    );
    await page.goto("/workflows");
    await page
      .locator(".sidebar-list-item", { hasText: "Templates" })
      .click();
    await expect(page.locator(".wtp-row").first()).toBeVisible({
      timeout: 15_000,
    });
  });

  const rows = (page) => page.locator(".wtp-row");
  const keys = (page) =>
    rows(page).evaluateAll((els) => els.map((el) => el.dataset.key));

  test("lists the shipped templates; a model filter narrows them and each sort reorders them", async ({
    page,
  }) => {
    await expect(rows(page)).toHaveCount(shipped.length);
    await expect(page.getByTestId("wtp-source")).toHaveAttribute(
      "aria-label",
      /the templates PixlStash ships/,
    );

    // A model several templates share, but not all of them.
    const counts = new Map();
    for (const t of shipped) {
      for (const m of t.model_names ?? []) counts.set(m, (counts.get(m) ?? 0) + 1);
    }
    const shared = [...counts].find(([, n]) => n >= 3 && n < shipped.length);
    expect(shared, "no model is shared by 3+ shipped templates").toBeTruthy();
    const [model] = shared;
    const narrowed = shipped.filter((t) => t.model_names?.includes(model));

    await page.getByRole("button", { name: "Filter", exact: true }).click();
    await page.locator('.fm-row[data-kind="model"]').click();
    const sub = page.getByTestId("wtf-sub-model");
    await sub.getByLabel("Find a model").fill(model);
    await sub
      .getByTestId("wtf-option")
      .filter({ has: page.getByText(model, { exact: true }) })
      .locator("input")
      .check();
    await expect(rows(page)).toHaveCount(narrowed.length);
    await page.keyboard.press("Escape");

    const byTitle = (a, b) => String(a.title).localeCompare(String(b.title));
    const sortBy = async (label) => {
      await page.getByRole("button", { name: /^Sort:/ }).click();
      await page
        .getByRole("radiogroup", { name: "Sort by" })
        .getByRole("radio", { name: label, exact: true })
        .click();
      await page.keyboard.press("Escape");
    };
    const byName = [...narrowed].sort(byTitle).map((t) => t.key);
    const byUse = [...narrowed]
      .sort((a, b) => (b.usage ?? 0) - (a.usage ?? 0) || byTitle(a, b))
      .map((t) => t.key);
    // Two different orders, or the second sort would prove nothing.
    expect(byUse).not.toEqual(byName);
    await sortBy("Name");
    await expect.poll(() => keys(page)).toEqual(byName);
    await sortBy("Most used");
    await expect.poll(() => keys(page)).toEqual(byUse);
  });

  test("a click and the keyboard select, and the inspector shows the selected template", async ({
    page,
  }) => {
    const title = page.getByTestId("tpli-title");
    const first = rows(page).nth(0);
    const second = rows(page).nth(1);
    const titleOf = async (el) => {
      const key = await el.getAttribute("data-key");
      return shipped.find((t) => t.key === key).title;
    };

    await first.click();
    await expect(first).toHaveAttribute("aria-selected", "true");
    await expect(title).toHaveText(await titleOf(first));

    // Escape clears; the arrow moves the cursor and Space picks it.
    await page.keyboard.press("Escape");
    await expect(page.locator('.wtp-row[aria-selected="true"]')).toHaveCount(0);
    await page.keyboard.press("ArrowRight");
    await expect(second).toBeFocused();
    await expect(second).toHaveAttribute("aria-selected", "false");
    await page.keyboard.press(" ");
    await expect(second).toHaveAttribute("aria-selected", "true");
    await expect(title).toHaveText(await titleOf(second));
    await expect(page.getByTestId("tpli-shipped")).toBeVisible();
  });

  test("adding two selected templates lands on Mine with both new cards selected", async ({
    page,
    apiContext,
  }) => {
    const picked = [rows(page).nth(0), rows(page).nth(1)];
    await picked[0].click();
    await picked[1].click({ modifiers: ["ControlOrMeta"] });
    await expect(page.getByTestId("wtp-count")).toHaveText("2 templates");

    const added = page.waitForResponse(
      (res) => new URL(res.url()).pathname === `${TEMPLATES}/add`,
    );
    await page.getByTestId("wtp-add").click();
    const body = await (await added).json();
    expect(body.failed).toEqual([]);
    expect(body.added).toHaveLength(2);

    await expect(page.locator(".wtp")).toHaveCount(0);
    for (const entry of body.added) {
      await expect(row(page, entry.workflow_id)).toHaveAttribute(
        "aria-selected",
        "true",
      );
    }
    await expect(page.locator('.wfv-row[aria-selected="true"]')).toHaveCount(2);
    expect(await workflowIds(apiContext)).toEqual(
      expect.arrayContaining(body.added.map((entry) => entry.workflow_id)),
    );
  });

  test("Download missing models asks for each missing file; only the download is faked", async ({
    page,
  }) => {
    const template = shipped.find(
      (t) => t.files.filter((f) => !f.present && f.downloadable).length >= 2,
    );
    expect(template, "no shipped template lacks 2+ downloadable files").toBeTruthy();
    const wanted = [
      ...new Set(
        template.files
          .filter((f) => !f.present && f.downloadable)
          .map((f) => f.name),
      ),
    ];
    const asked = [];
    await page.route(
      (url) => url.pathname === "/api/v1/model-downloads",
      async (route) => {
        if (route.request().method() !== "POST") return route.fallback();
        const { file } = route.request().postDataJSON();
        asked.push(file);
        return route.fulfill({
          json: { id: `fake-${asked.length}`, file, status: "running" },
        });
      },
    );

    await page.getByRole("button", { name: /^Sort:/ }).click();
    await page
      .getByRole("radiogroup", { name: "Sort by" })
      .getByRole("radio", { name: "Name", exact: true })
      .click();
    await page.keyboard.press("Escape");
    const target = page.locator(`.wtp-row[data-key="${template.key}"]`);
    await target.scrollIntoViewIfNeeded();
    await target.click({ button: "right" });
    const item = page
      .getByTestId("wtp-menu")
      .locator('[data-verb="download-missing"]');
    await expect(item).toHaveText(`Download ${wanted.length} missing models`);
    await item.click();

    await expect.poll(() => [...asked].sort()).toEqual([...wanted].sort());
    await expect(
      page.locator(".notice-message", {
        hasText: `Downloading ${wanted.length} models.`,
      }),
    ).toBeVisible();
  });
});
