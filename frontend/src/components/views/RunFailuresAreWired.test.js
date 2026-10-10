// A failed run is shown on its workflow through three lines of wiring that no
// component test can see missing: the socket's event reaching the store, the
// store's entry reaching the card, and a part-queued batch's reason reaching a
// notice (#1839). Asserted against the SOURCE, as OverlaySignalsAreWired does:
// each is a line of template or a call in `App.vue`, and mounting the app to
// check one binding would cost more than the bug does.

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const read = (relative) =>
  readFileSync(fileURLToPath(new URL(relative, import.meta.url)), "utf8");

const app = read("../../App.vue");
const view = read("./WorkflowsView.vue");

describe("a failed run reaches its workflow", () => {
  it("is read off every plugin_progress event, on every view", () => {
    expect(app).toMatch(
      /watch\(\s*\(\) => wsStore\.wsPluginProgress,[\s\S]{0,400}runDialogStore\.runEvent\(wrapped\?\.payload\)/,
    );
    // Before the runner check, which only decides whether a notice is raised.
    const watcher = app.slice(app.indexOf("runDialogStore.runEvent("));
    expect(watcher.indexOf("hasRunner")).toBeGreaterThan(0);
  });

  it("is handed to the workflow's card", () => {
    expect(view).toMatch(
      /<WorkflowCard[^>]*:run-failure="runDialog\.failures\[entry\.card\.id\] \?\? null"/,
    );
  });

  it("raises a part-queued batch's reason as a notice", () => {
    expect(app).toMatch(/function onRunStarted\(\{[^}]*error = ""[^}]*\}/);
    expect(app).toMatch(/if \(error\) \{\s*noticeStore\.error\(/);
  });
});
