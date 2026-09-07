// The shared fixture (not raw @playwright/test) so this context gets the
// production-network block registered on the browser fixture (issue #1213),
// even though the test itself uses no page objects from it.
import { test, expect } from "../fixtures/test.js";

// The dialog is a static table of key hints, so it has no unit coverage and
// nothing else exercises it. This pins the two things that can actually
// break: F1 reaching the handler, and the table rendering rows.

test("shortcuts dialog opens on F1 and lists the grid shortcuts", async ({
  page,
}) => {
  await page.goto("/");
  await expect(page.locator(".thumbnail-card").first()).toBeVisible({
    timeout: 15_000,
  });
  await page.keyboard.press("F1");
  const dialog = page.locator(".shortcuts-dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.locator(".shortcuts-table tr")).not.toHaveCount(0);
  await expect(dialog.getByText("Open search")).toBeVisible();
  await page.keyboard.press("F1");
  await expect(dialog).toBeHidden();
});
