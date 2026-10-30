import { test, expect } from "../fixtures/test.js";

// How a reader gets around the Workflows screen (#2319): the deep links the
// router documents (`?workflow=`, `?topology=`, `&edit=loras&drop_lora=`), a
// cover tile out to the lightbox and back, *Show all N pictures* out to the
// grid, the toolbar's sort, filters and search, and the keyboard.
//
// The fixture library has no workflows yet (#2314 seeds them), so the
// `/workflows*` routes are answered here from three cards, as the other
// Workflows specs do. Everything else is the real backend: the cover tiles
// name real fixture pictures, the lightbox opens them, and favourites are
// written to the server. A workflow route nobody answered is recorded and
// fails the test, so nothing here can pass on the real backend's 404.

const PORTRAIT = `manual:${"a".repeat(32)}`;
const LANDSCAPE = `auto:${"b".repeat(64)}`;
const ANIME = `auto:${"c".repeat(64)}`;
const OLD = `auto:${"d".repeat(64)}`;
// A one-off: the server holds it, and the grid leaves it out.
const ONE_OFF = `auto:${"e".repeat(64)}`;

const NAMES = {
  [PORTRAIT]: "Portrait, soft window light",
  [LANDSCAPE]: "Landscape, golden hour",
  [ANIME]: "Anime upscale",
  [OLD]: "Old experiment",
  [ONE_OFF]: "One-off sketch",
};

const loader = (node, filename, strength) => ({
  node_id: node,
  class_type: "LoraLoader",
  field: "lora_name",
  filename,
  name: filename.replace(/\.safetensors$/, ""),
  strength,
  strength_clip: strength,
  sha256: String(node).repeat(64).slice(0, 64),
  on_shelf: true,
});
const CHAIN = [
  loader("7", "detail_slider_v2.safetensors", 0.6),
  loader("8", "soft_light.safetensors", 0.8),
];

/**
 * A card as `GET /workflows` serves it. Three orders tell the sorts apart:
 * by rating Portrait, Landscape, Anime; by name Anime, Landscape, Portrait;
 * by picture count Landscape, Anime, Portrait.
 */
function cardOf(id, covers) {
  const base = {
    id,
    name: NAMES[id],
    type: "txt2img",
    type_label: "Text to Image",
    base_topology: `topology-${id.slice(-4)}`,
    topologies: [`topology-${id.slice(-4)}`],
    models: [],
    loras: [],
    covers: [],
    manual: false,
    hidden: false,
  };
  return {
    [PORTRAIT]: {
      ...base,
      manual: true,
      picture_count: 2,
      rating: 4.5,
      rank: 4.5,
      covers: covers.map((pictureId) => ({
        picture_id: pictureId,
        url: `/pictures/thumbnails/${pictureId}.webp`,
      })),
    },
    [LANDSCAPE]: {
      ...base,
      type: "img2img",
      type_label: "Image to Image",
      picture_count: 40,
      rating: 3,
      rank: 3,
    },
    [ANIME]: {
      ...base,
      type: "upscale",
      type_label: "Upscale",
      picture_count: 12,
      rating: 2,
      rank: 2,
    },
    [OLD]: { ...base, hidden: true, picture_count: 5, rating: 1, rank: 1 },
    [ONE_OFF]: { ...base, picture_count: 1, rating: 0, rank: 0 },
  }[id];
}

/**
 * Answer the workflow routes from the cards. Returns what was written (every
 * DELETE and every saved LoRA chain that reached the server) and every
 * workflow route nobody answered.
 */
async function fakeWorkflows(page, covers) {
  const net = { deletes: [], chainWrites: [], pictureFilters: [], unstubbed: [] };
  const json = (route, body, status = 200) => route.fulfill({ status, json: body });
  await page.route(
    (url) => url.pathname.startsWith("/api/v1/"),
    async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const path = decodeURIComponent(url.pathname).slice("/api/v1".length);
      const method = request.method();

      // *Show all N pictures* filters the library on the workflow, which the
      // fixture's pictures do not belong to. The filter is taken off the read
      // so the grid has pictures to open; the chip and the URL are untouched.
      // What was asked for is kept, so a test can see the filter was sent.
      if (!path.startsWith("/workflows") && url.searchParams.has("workflow")) {
        net.pictureFilters.push(url.searchParams.getAll("workflow"));
        url.searchParams.delete("workflow");
        return route.continue({ url: url.toString() });
      }
      if (path === "/recipes" && method === "GET") return json(route, []);
      if (path === "/adapters" && method === "GET") return json(route, { adapters: [] });
      if (path === "/models/workflow-sets" && method === "GET") {
        return json(route, { combinations: [], no_set: [], hand_made: [], set_checks: [] });
      }
      if (!path.startsWith("/workflows")) return route.fallback();

      if (path === "/workflows" && method === "GET") {
        const ids = [PORTRAIT, LANDSCAPE, ANIME];
        if (url.searchParams.get("include_hidden") === "true") ids.push(OLD);
        return json(route, {
          cards: ids.map((id) => cardOf(id, covers)),
          one_offs: 1,
          hidden: 1,
        });
      }
      if (path === "/workflows/run/preflight" && method === "POST") {
        const id = request.postDataJSON().workflow_id;
        return json(route, {
          ok: true,
          runs: 1,
          groups: [{ workflow_id: id, reasons: [], picture_inputs: [] }],
        });
      }
      const own = Object.keys(NAMES).find(
        (id) => path === `/workflows/${id}` || path.startsWith(`/workflows/${id}/`),
      );
      const rest = own ? path.slice(`/workflows/${own}`.length) : null;
      if (rest === "" && method === "DELETE") {
        net.deletes.push(own);
        return json(route, { detail: "this spec never deletes" }, 409);
      }
      if (rest === "" && method === "GET") {
        return json(route, {
          card: cardOf(own, covers),
          notes: null,
          hidden: false,
          variants: [],
          pins: null,
          graph_base_models: [],
          model_fixes: [],
        });
      }
      if (rest === "/changes" && method === "GET") {
        return json(route, {
          workflow_id: own,
          version: 1,
          waiting: null,
          count: 0,
          ran: null,
          save: { open: true, reason: null },
          save_as_new: { open: true, reason: null },
          ran_save: null,
        });
      }
      if (rest === "/versions" && method === "GET") {
        return json(route, { workflow_id: own, version: 1, versions: [] });
      }
      if (rest === "/lora-summary" && method === "GET") {
        return json(route, {
          workflow_id: own,
          pictures: 0,
          shared: [],
          varying: [],
          without: null,
          cover_asset: null,
        });
      }
      if (rest === "/form-inputs" && method === "GET") return json(route, { nodes: [] });
      if (rest === "/lora-chain" && method === "GET") {
        return json(route, {
          workflow_id: own,
          editable: true,
          refusal: null,
          source: { node_id: "4", class_type: "CheckpointLoaderSimple", outputs: ["MODEL", "CLIP"] },
          sink: { summary: "KSampler #12 reads model", consumers: [] },
          loaders: CHAIN,
          added_loader_class: "LoraLoader",
          lanes: [],
          branch_note: null,
        });
      }
      if (rest === "/lora-chain" && method === "PUT") {
        // Edit LoRAs previews every edit as a dry run; only a save writes.
        const body = request.postDataJSON();
        if (!body.dry_run) net.chainWrites.push(body);
        return json(route, {
          dry_run: Boolean(body.dry_run),
          name: null,
          workflow_id: body.dry_run ? null : own,
          overwritten: !body.dry_run,
          changes: [],
        });
      }

      net.unstubbed.push(`${method} ${path}${url.search}`);
      return json(route, { detail: "not stubbed by workflows-navigation.spec.js" }, 599);
    },
  );
  return net;
}

// ── What is on the page ─────────────────────────────────────────────────────

const rows = (page) => page.locator(".wfv-row");
const row = (page, id) => page.locator(`.wfv-row[data-key="${id}"]`);
const selected = (page) => page.locator('.wfv-row[aria-selected="true"]');
const rail = (page) => page.getByRole("complementary", { name: "Inspector" });
const strip = (page) => page.getByRole("region", { name: "Active filters" });
const notices = (page) => page.locator(".wfv-note");
const verbs = (page) => page.getByTestId("wf-verbs");

/** The grid's card order, by workflow id. */
const order = (page) =>
  rows(page).evaluateAll((elements) => elements.map((element) => element.dataset.key));

/**
 * Navigate inside the running app, as one of its own links does (the Recipe
 * section's Open, the Run popup's Open in Workflows): through the router, with
 * no document load, so the stores keep what the reader had done.
 */
const inApp = (page, to) =>
  page.evaluate(
    (target) =>
      document.querySelector("#app").__vue_app__.config.globalProperties.$router.push(target),
    to,
  );

/** The query of the address bar, as a plain object. */
const query = (page) => Object.fromEntries(new URL(page.url()).searchParams);

/**
 * Whether the focused element draws a focus ring: it matches `:focus-visible`
 * and paints an outline or a box shadow while it does.
 */
const ringed = (locator) =>
  locator.evaluate((element) => {
    if (!element.matches(":focus-visible")) return false;
    const style = getComputedStyle(element);
    const outline = style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0;
    return outline || style.boxShadow !== "none";
  });

/** Open the screen and wait for the three cards. */
async function openGrid(page, path = "/workflows") {
  await page.goto(path);
  await expect(rows(page)).toHaveCount(3, { timeout: 15_000 });
}

let net;
let covers;

test.beforeAll(async ({ apiContext }) => {
  // Two real pictures for the Portrait card's covers, so the lightbox opens
  // something the library has.
  const listed = await apiContext.get("/api/v1/pictures", { params: { fields: "grid" } });
  expect(listed.ok(), await listed.text()).toBe(true);
  covers = (await listed.json()).slice(0, 2).map((picture) => picture.id);
  expect(covers).toHaveLength(2);
});

test.beforeEach(async ({ page }) => {
  net = await fakeWorkflows(page, covers);
});

test.afterEach(async ({ apiContext }) => {
  // Favourites are the real server's: take back any this spec made.
  const cleared = await apiContext.put("/api/v1/favourites", {
    data: { kind: "workflow", keys: Object.keys(NAMES), favourite: false },
  });
  expect(cleared.ok(), await cleared.text()).toBe(true);
  // Last, and loud: a request that fell through was answered 599.
  expect(net?.unstubbed ?? [], "workflow routes this spec did not stub").toEqual([]);
});

// ── Deep links ──────────────────────────────────────────────────────────────

test.describe("deep links", () => {
  test("?workflow= selects the card, focuses it and opens the rail on it", async ({ page }) => {
    await openGrid(page, `/workflows?workflow=${encodeURIComponent(LANDSCAPE)}`);

    await expect(selected(page)).toHaveCount(1);
    await expect(row(page, LANDSCAPE)).toHaveAttribute("aria-selected", "true");
    await expect(row(page, LANDSCAPE)).toBeFocused();
    await expect(rail(page)).toContainText(NAMES[LANDSCAPE]);
    await expect(notices(page)).toHaveCount(0);
  });

  test("?workflow= for a card the grid does not list says so", async ({ page }) => {
    await openGrid(page, `/workflows?workflow=${encodeURIComponent(ONE_OFF)}`);

    // What the grid leaves out is named: hidden ones uncounted, one-offs counted.
    await expect(notices(page)).toHaveText(
      "That workflow is not in this grid. Left out: hidden workflows and 1 one-off.",
    );
    await expect(page.getByTestId("wfv-link-filtered")).toHaveCount(0);
    await expect(rows(page)).toHaveCount(3);
  });

  test("?workflow= for a card a filter hides says so, and Clear all brings it", async ({ page }) => {
    await openGrid(page);
    await page.getByRole("textbox", { name: "Search workflows by name" }).fill("golden");
    await expect(rows(page)).toHaveCount(1);

    await inApp(page, `/workflows?workflow=${encodeURIComponent(PORTRAIT)}`);
    const note = page.getByTestId("wfv-link-filtered");
    await expect(note).toContainText("That workflow is in this grid, but a filter is hiding it.");
    // Not the other note: this one is about a filter, not the server.
    await expect(notices(page).filter({ hasText: "not in this grid" })).toHaveCount(0);

    await note.getByRole("button", { name: "Clear all" }).click();
    await expect(rows(page)).toHaveCount(3);
    await expect(row(page, PORTRAIT)).toHaveAttribute("aria-selected", "true");
    await expect(row(page, PORTRAIT)).toBeFocused();
    await expect(note).toHaveCount(0);
  });

  test("?topology= opens the first workflow holding that topology", async ({ page }) => {
    await openGrid(page, `/workflows?topology=topology-${ANIME.slice(-4)}`);

    await expect(row(page, ANIME)).toHaveAttribute("aria-selected", "true");
    await expect(row(page, ANIME)).toBeFocused();
    await expect(selected(page)).toHaveCount(1);
    await expect(rail(page)).toContainText(NAMES[ANIME]);
  });

  test("&edit=loras&drop_lora= arrives with that loader deleted, once", async ({ page }) => {
    await openGrid(page);
    await inApp(
      page,
      `/workflows?workflow=${encodeURIComponent(PORTRAIT)}&edit=loras&drop_lora=soft_light.safetensors`,
    );

    const dialog = page.getByRole("dialog", { name: "Edit LoRAs" });
    await expect(dialog).toBeVisible();
    // Struck through and offered back, the other loader untouched.
    await expect(dialog.getByRole("button", { name: "Put soft_light back" })).toBeVisible();
    await expect(dialog.getByRole("button", { name: "Put detail_slider_v2 back" })).toHaveCount(0);
    // Both are one-shot: off the URL once honoured, `workflow` kept.
    await expect.poll(() => query(page)).toEqual({ workflow: PORTRAIT });

    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expect(dialog).toBeHidden();
    expect(net.chainWrites).toEqual([]);

    // The rail took `edit` and `drop_lora` off with a `replace`, so the entry
    // the link pushed now holds the workflow alone: Back goes to the plain
    // grid and Forward to that, and neither reopens what was cancelled.
    await page.goBack();
    await expect.poll(() => query(page)).toEqual({});
    await page.goForward();
    await expect.poll(() => query(page)).toEqual({ workflow: PORTRAIT });
    await expect(row(page, PORTRAIT)).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("dialog", { name: "Edit LoRAs" })).toHaveCount(0);
  });
});

// ── Out to a picture and back ───────────────────────────────────────────────

test.describe("cover tile and lightbox", () => {
  test("a cover tile opens its picture and the close comes back to the card", async ({
    page,
    lightbox,
  }) => {
    await openGrid(page);
    await row(page, LANDSCAPE).click();

    // Right-click the SECOND tile of the Portrait card: the menu offers that
    // picture, not the cover.
    const tile = row(page, PORTRAIT).locator('.wf-card__pic[data-picture-index="2"]');
    await tile.click({ button: "right" });
    await verbs(page).getByRole("menuitem", { name: "Open picture 2 of 2" }).click();

    await expect(lightbox.root).toBeVisible();
    await expect.poll(() => new URL(page.url()).pathname).toBe("/");
    expect(query(page)).toEqual({ overlay: String(covers[1]), from: "/workflows" });

    await lightbox.closeButton.click();
    await expect.poll(() => new URL(page.url()).pathname).toBe("/workflows");
    expect(query(page)).toEqual({});
    await expect(rows(page)).toHaveCount(3);
    // The right-click made Portrait the selection, and the return puts the
    // cursor back on it.
    await expect(selected(page)).toHaveCount(1);
    await expect(row(page, PORTRAIT)).toHaveAttribute("aria-selected", "true");
    await expect(row(page, PORTRAIT)).toBeFocused();
  });

  test("Show all N pictures goes to the grid without `from`, so a close stays there", async ({
    page,
    grid,
    lightbox,
  }) => {
    await openGrid(page);
    await row(page, LANDSCAPE).locator(".wf-card__info").click();
    await page
      .getByTestId("workflow-info-popover")
      .getByRole("button", { name: "Show all 40 pictures" })
      .click();

    await expect.poll(() => new URL(page.url()).pathname).toBe("/");
    expect(query(page)).not.toHaveProperty("from");
    // The workflow is a removable chip on the picture grid's strip.
    await expect(
      page.getByRole("button", { name: `Remove filter: Workflow ${NAMES[LANDSCAPE]}` }),
    ).toBeVisible();

    // …and the library was asked for that workflow's pictures alone.
    await expect.poll(() => net.pictureFilters).toContainEqual([LANDSCAPE]);
    expect(net.pictureFilters.every((ids) => ids.length === 1 && ids[0] === LANDSCAPE)).toBe(true);

    await grid.waitForThumbnailLoaded();
    await lightbox.openFromGrid();
    await expect(lightbox.root).toBeVisible();
    expect(query(page)).not.toHaveProperty("from");

    await lightbox.closeButton.click();
    await expect(lightbox.root).toBeHidden();
    await expect.poll(() => new URL(page.url()).pathname).toBe("/");
    // The lightbox hides before the close's `replace` lands, and the path was
    // "/" all along, so the poll above does not wait for it: poll the query.
    await expect.poll(() => query(page)).not.toHaveProperty("overlay");
    await expect(rows(page)).toHaveCount(0);
  });
});

// ── The toolbar ─────────────────────────────────────────────────────────────

test.describe("sort, filters and search", () => {
  test("the sort menu reorders the cards", async ({ page }) => {
    await openGrid(page);
    expect(await order(page)).toEqual([PORTRAIT, LANDSCAPE, ANIME]);

    const sort = page.getByRole("button", { name: /^Sort:/ });
    await sort.click();
    await page.getByRole("radiogroup", { name: "Sort by" }).getByRole("radio", { name: "Name" }).click();
    await expect.poll(() => order(page)).toEqual([ANIME, LANDSCAPE, PORTRAIT]);
    await page.getByRole("radio", { name: "Picture count" }).click();
    await expect.poll(() => order(page)).toEqual([LANDSCAPE, ANIME, PORTRAIT]);
    await page.keyboard.press("Escape");
    await expect(sort).toContainText("Picture count");
  });

  test("a Filters menu row narrows the cards to a chip, and Clear all restores them", async ({ page }) => {
    await openGrid(page);
    // Named "Filter", and "1 Filter" with its badge.
    const filter = page.getByRole("button", { name: /^(\d+ )?Filter$/ });

    await filter.click();
    await page.locator('.fm-row[data-kind="type"]').click();
    await page.getByTestId("wff-sub-type").getByRole("radio", { name: /^Image to Image/ }).click();
    await page.keyboard.press("Escape");
    await expect.poll(() => order(page)).toEqual([LANDSCAPE]);
    await expect(
      strip(page).getByRole("button", { name: "Remove filter: Type Image to Image" }),
    ).toBeVisible();
    await expect(filter).toHaveAccessibleName("1 Filter");

    // The server's flag: Show hidden reads the grid again with the hidden card.
    await filter.click();
    await page.locator('.fm-row[data-kind="showHidden"]').click();
    await page.getByTestId("wff-sub-showHidden").getByRole("radio", { name: "Show" }).click();
    await page.keyboard.press("Escape");
    await strip(page).getByRole("button", { name: "Remove filter: Type Image to Image" }).click();
    await expect.poll(() => order(page)).toEqual([PORTRAIT, LANDSCAPE, ANIME, OLD]);
    await expect(strip(page).getByRole("button", { name: /^Remove filter:/ })).toHaveCount(1);

    await strip(page).getByRole("button", { name: "Clear all" }).click();
    await expect.poll(() => order(page)).toEqual([PORTRAIT, LANDSCAPE, ANIME]);
    await expect(strip(page).getByRole("button", { name: /^Remove filter:/ })).toHaveCount(0);
    await expect(filter).toHaveAccessibleName("Filter");
  });

  test("the name search narrows to a chip, and an empty result offers Clear all", async ({ page }) => {
    await openGrid(page);
    const search = page.getByRole("textbox", { name: "Search workflows by name" });

    await search.fill("golden");
    await expect.poll(() => order(page)).toEqual([LANDSCAPE]);
    await expect(strip(page).getByRole("button", { name: /^Remove filter:/ })).toHaveCount(1);

    await search.fill("nothing is called this");
    await expect(rows(page)).toHaveCount(0);
    const empty = notices(page).filter({ hasText: "No workflow matches these filters." });
    await empty.getByRole("button", { name: "Clear all" }).click();
    await expect.poll(() => order(page)).toEqual([PORTRAIT, LANDSCAPE, ANIME]);
    await expect(search).toHaveValue("");
  });

  test("favourites lead, the Favourites place narrows to them, and a reload keeps them", async ({
    page,
  }) => {
    await openGrid(page);
    await row(page, ANIME).hover();
    await row(page, ANIME).getByRole("button", { name: `Favourite ${NAMES[ANIME]}` }).click();
    // Favourites first is on by default: the favourite leads the rating sort.
    await expect.poll(() => order(page)).toEqual([ANIME, PORTRAIT, LANDSCAPE]);

    const places = page.getByRole("navigation", { name: "Workflow navigation" });
    await places.getByRole("button", { name: /^Favourites/ }).click();
    await expect.poll(() => order(page)).toEqual([ANIME]);
    await places.getByRole("button", { name: /^All workflows/ }).click();
    await expect.poll(() => order(page)).toEqual([ANIME, PORTRAIT, LANDSCAPE]);

    // What persists across a reload is the favourite, which is the server's.
    // The sort and the filters are the session's: they come back at their
    // defaults.
    await page.getByRole("button", { name: /^Sort:/ }).click();
    await page.getByRole("radio", { name: "Name" }).click();
    await page.keyboard.press("Escape");
    await page.getByRole("textbox", { name: "Search workflows by name" }).fill("a");
    await expect.poll(() => order(page)).toEqual([ANIME, LANDSCAPE, PORTRAIT]);

    await page.reload();
    await expect(rows(page)).toHaveCount(3, { timeout: 15_000 });
    await expect.poll(() => order(page)).toEqual([ANIME, PORTRAIT, LANDSCAPE]);
    await expect(
      row(page, ANIME).getByRole("button", { name: `Favourite ${NAMES[ANIME]}` }),
    ).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("button", { name: /^Sort:/ })).toContainText("Your ratings");
    await expect(page.getByRole("textbox", { name: "Search workflows by name" })).toHaveValue("");
    await expect(strip(page).getByRole("button", { name: /^Remove filter:/ })).toHaveCount(0);
  });
});

// ── The keyboard ────────────────────────────────────────────────────────────

test.describe("keyboard", () => {
  test("arrows move a visible cursor, Space adds, Escape clears", async ({ page }) => {
    await openGrid(page);
    await row(page, PORTRAIT).click();
    await expect(row(page, PORTRAIT)).toBeFocused();

    await page.keyboard.press("ArrowRight");
    await expect(row(page, LANDSCAPE)).toBeFocused();
    expect(await ringed(row(page, LANDSCAPE))).toBe(true);
    // The cursor moves; the selection does not follow it.
    await expect(selected(page)).toHaveCount(1);
    await expect(row(page, PORTRAIT)).toHaveAttribute("aria-selected", "true");

    await page.keyboard.press("ArrowLeft");
    await expect(row(page, PORTRAIT)).toBeFocused();
    expect(await ringed(row(page, PORTRAIT))).toBe(true);

    // Down keeps the column, and past the last row settles on the last card.
    const columns = Number(
      await page.locator(".wfv-grid").evaluate((grid) => grid.style.getPropertyValue("--wf-columns")),
    );
    await page.keyboard.press("ArrowDown");
    const below = Math.min(columns, 2);
    await expect(rows(page).nth(below)).toBeFocused();
    await page.keyboard.press("ArrowUp");
    await expect(rows(page).nth(Math.max(below - columns, 0))).toBeFocused();

    await page.keyboard.press("ArrowRight");
    await expect(row(page, LANDSCAPE)).toBeFocused();
    await page.keyboard.press(" ");
    await expect(selected(page)).toHaveCount(2);
    expect(await ringed(row(page, LANDSCAPE))).toBe(true);

    await page.keyboard.press("Escape");
    await expect(selected(page)).toHaveCount(0);
    await expect(row(page, LANDSCAPE)).toBeFocused();
  });

  test("Enter opens the card's info and F2 opens Rename, each handing focus back", async ({ page }) => {
    await openGrid(page);
    await row(page, LANDSCAPE).click();

    await page.keyboard.press("Enter");
    const info = page.getByTestId("workflow-info-popover");
    await expect(info).toBeVisible();
    await expect(info).toContainText(NAMES[LANDSCAPE]);
    await page.keyboard.press("Escape");
    await expect(info).toBeHidden();

    await row(page, LANDSCAPE).focus();
    await page.keyboard.press("F2");
    const rename = page.getByRole("dialog", { name: "Rename workflow" });
    await expect(rename).toBeVisible();
    await expect(rename.getByRole("textbox", { name: "Name" })).toHaveValue(NAMES[LANDSCAPE]);
    await page.keyboard.press("Escape");
    await expect(rename).toBeHidden();
    await expect(row(page, LANDSCAPE)).toBeFocused();
    expect(await ringed(row(page, LANDSCAPE))).toBe(true);
  });

  test("Delete asks first and never deletes on its own", async ({ page }) => {
    await openGrid(page);

    // An automatic workflow cannot be deleted: the key does nothing at all.
    await row(page, LANDSCAPE).click();
    await page.keyboard.press("Delete");
    await expect(page.getByRole("dialog", { name: "Delete this workflow?" })).toHaveCount(0);

    // A manual one asks, and Cancel sends nothing. Had the Delete above opened
    // a confirm late, there would now be two dialogs of this name, and the
    // strict locator below fails on that.
    await row(page, PORTRAIT).click();
    await page.keyboard.press("Delete");
    const ask = page.getByRole("dialog", { name: "Delete this workflow?" });
    await expect(ask).toBeVisible();
    await expect(ask).toContainText(NAMES[PORTRAIT]);
    await ask.getByRole("button", { name: "Cancel" }).click();
    await expect(ask).toBeHidden();

    await page.keyboard.press("Delete");
    await expect(ask).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(ask).toBeHidden();

    expect(net.deletes).toEqual([]);
    await expect(rows(page)).toHaveCount(3);
    await expect(row(page, PORTRAIT)).toHaveAttribute("aria-selected", "true");
  });

  test("Shift+F10 and the ContextMenu key open the card menu, Escape gives focus back", async ({
    page,
  }) => {
    await openGrid(page);
    await row(page, PORTRAIT).click();
    await page.keyboard.press("ArrowRight");
    await expect(row(page, LANDSCAPE)).toBeFocused();

    for (const key of ["Shift+F10", "ContextMenu"]) {
      await page.keyboard.press(key);
      await expect(verbs(page)).toBeVisible();
      // The menu acts on the cursor's card, which it made the selection.
      await expect(selected(page)).toHaveCount(1);
      await expect(row(page, LANDSCAPE)).toHaveAttribute("aria-selected", "true");
      await expect(verbs(page).getByRole("menuitem", { name: "Rename" })).toBeVisible();

      await page.keyboard.press("Escape");
      await expect(verbs(page)).toBeHidden();
      await expect(row(page, LANDSCAPE)).toBeFocused();
      expect(await ringed(row(page, LANDSCAPE))).toBe(true);
    }
  });
});
