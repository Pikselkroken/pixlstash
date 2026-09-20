// The shared environment stubs, pinned where their absence is expensive.
//
// The canvas one removes ~67 reports a run about a capability jsdom does not
// have. Nothing asserts on them, so without a test here the stub reads as dead
// tidying and gets dropped; the assertions below are what say it is deliberate.
//
// It is NOT the fix for the teardown flake (#1446) - see `setup.js` for what
// that would take. It removes ~6% of the console traffic that flake rides on.

import { describe, it, expect } from "vitest";

describe("the jsdom canvas stub", () => {
  it("returns null, as jsdom itself does", () => {
    // Every caller branches on a missing context, so the stub must not quietly
    // hand them a truthy one.
    expect(document.createElement("canvas").getContext("2d")).toBeNull();
  });

  it("reports nothing to the virtual console", () => {
    // jsdom's private channel, and the only place the report is observable: it
    // does not go through `console.error`, which is why patching that was no
    // fix at all.
    const virtualConsole = window._virtualConsole;
    expect(virtualConsole, "jsdom's virtual console").toBeTruthy();

    const reported = [];
    const listen = (err) => reported.push(err?.message ?? String(err));
    virtualConsole.on("jsdomError", listen);
    try {
      document.createElement("canvas").getContext("2d");
      document.createElement("canvas").getContext("webgl");
    } finally {
      virtualConsole.removeListener("jsdomError", listen);
    }

    expect(reported).toEqual([]);
  });
});
