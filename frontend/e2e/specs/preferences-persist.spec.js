import { test, expect } from "@playwright/test";

// Preferences take a long road: a control writes a store, a watcher notices and
// PATCHes /users/me/config, and the next session reads it back. The Phase 3
// refactor moved every step of that - the guarded setters onto the stores, the
// fourteen persistence watchers into useAppConfig - and nothing exercised the
// round trip, so a preference that silently stopped persisting would have gone
// unnoticed until a user complained their settings kept resetting.
//
// Compact mode is the cheapest control to drive: a real button in the toolbar's
// Grid-view menu, writing straight to the grid store.

/** Open the toolbar's "Grid view" menu and return its Compact toggle. */
async function openViewMenu(page) {
  await page.locator(".bar-btn:has(.mdi-view-grid)").first().click();
  const compact = page.locator(".tbm-btn--compact");
  await expect(compact).toBeVisible();
  return compact;
}

test.describe("preferences round-trip", () => {
  test("a compact-mode change is PATCHed and survives a reload", async ({
    page,
  }) => {
    const patches = [];
    await page.route("**/users/me/config", async (route) => {
      if (route.request().method() === "PATCH") {
        patches.push(route.request().postDataJSON());
      }
      await route.continue();
    });

    await page.goto("/");
    await expect(page.locator(".thumbnail-card").first()).toBeVisible({
      timeout: 15_000,
    });

    const compact = await openViewMenu(page);
    const wasOn = await compact.evaluate((el) =>
      el.classList.contains("tbm-btn--on"),
    );
    await compact.click();

    // The change reached the server, not just the store.
    await expect
      .poll(() => patches.some((p) => p && "compact_mode" in p), {
        timeout: 10_000,
      })
      .toBe(true);
    const sent = patches.filter((p) => p && "compact_mode" in p).pop();
    expect(sent.compact_mode).toBe(!wasOn);

    // And it is what the next session gets.
    await page.reload();
    await expect(page.locator(".thumbnail-card").first()).toBeVisible({
      timeout: 15_000,
    });
    const afterReload = await openViewMenu(page);
    await expect
      .poll(
        () =>
          afterReload.evaluate((el) => el.classList.contains("tbm-btn--on")),
        { timeout: 10_000 },
      )
      .toBe(!wasOn);

    // Put it back so the shared fixture is left as it was found.
    await afterReload.click();
  });
});
