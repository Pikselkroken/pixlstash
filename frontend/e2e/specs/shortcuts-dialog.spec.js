// The shared fixture (not raw @playwright/test) so this context gets the
// production-network block registered on the browser fixture (issue #1213),
// even though the test itself uses no page objects from it.
import { test, expect } from "../fixtures/test.js";

// The dialog is a static table of key hints, so it has no unit coverage and
// nothing else exercises it. This pins the two things that can actually
// break: ? reaching the handler, and the table rendering rows. (F1 is the
// Guide's since #2825.)

test("shortcuts dialog opens on ? and lists the grid shortcuts", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator(".thumbnail-card").first()).toBeVisible({
    timeout: 15_000,
  });
  await page.keyboard.press("?");
  const dialog = page.locator(".app-dialog:has(.shortcuts-table)");
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".shortcuts-table tr")).not.toHaveCount(0);
  await expect(dialog.getByText("Open search")).toBeVisible();
  // Every AppDialog centres on the window, not on the overlay's left edge.
  const box = await dialog.boundingBox();
  const { width } = page.viewportSize();
  expect(Math.abs(box.x + box.width / 2 - width / 2)).toBeLessThan(2);
  await page.keyboard.press("?");
  await expect(dialog).toBeHidden();
});
