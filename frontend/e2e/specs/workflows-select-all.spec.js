import { test, expect } from "../fixtures/test.js";

// Ctrl/Cmd+A after a click on the Workflows view's empty background. The bug
// was focus: nothing behind the cards was focusable, so the click left focus
// on <body> and the grid's key handler never heard the chord. That is a
// browser behaviour (a click focuses the nearest focusable ancestor), which
// jsdom does not model, hence a real browser here.
//
// The fixture library carries no ComfyUI workflows, so the card list is
// stubbed; the view only needs the list to draw the grid.

const card = (id) => ({
  id,
  name: `Workflow ${id}`,
  base_topology: `topology-${id}`,
  topologies: [`topology-${id}`],
  models: [],
  loras: [],
  picture_count: 1,
  rating: 3,
  rank: 1,
  covers: [],
});
const CARDS = ["a", "b", "c"].map(card);

test.beforeEach(async ({ page }) => {
  await page.route(
    (url) => url.pathname === "/api/v1/workflows",
    (route) =>
      route.fulfill({ json: { cards: CARDS, one_offs: 0, hidden: 0 } }),
  );
  await page.goto("/workflows");
  await expect(page.locator(".wfv-row")).toHaveCount(CARDS.length, {
    timeout: 15_000,
  });
});

const selected = (page) => page.locator('.wfv-row[aria-selected="true"]');

test("Ctrl+A selects every card after a click on the background below them", async ({
  page,
}) => {
  const scroller = page.locator(".wfv-scroll");
  const box = await scroller.boundingBox();
  // Bottom of the pane: below three cards on one row there is only background.
  await page.mouse.click(box.x + box.width / 2, box.y + box.height - 8);
  await expect(scroller).toBeFocused();
  await expect(selected(page)).toHaveCount(0);

  await page.keyboard.press("ControlOrMeta+a");
  await expect(selected(page)).toHaveCount(CARDS.length);

  await page.keyboard.press("Escape");
  await expect(selected(page)).toHaveCount(0);
});

test("Ctrl+A selects every card after a click in the gap between two cards", async ({
  page,
}) => {
  const first = await page.locator(".wfv-row").nth(0).boundingBox();
  const second = await page.locator(".wfv-row").nth(1).boundingBox();
  await page.mouse.click(
    (first.x + first.width + second.x) / 2,
    first.y + first.height / 2,
  );
  await expect(page.locator(".wfv-scroll")).toBeFocused();

  await page.keyboard.press("ControlOrMeta+a");
  await expect(selected(page)).toHaveCount(CARDS.length);
});
