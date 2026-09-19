// The shared environment stubs, pinned where their absence is expensive.
//
// The canvas one is not a convenience: jsdom reporting a missing canvas is what
// reds the gate, because vitest forwards that report over rpc and a write still
// in flight at worker teardown becomes an unhandled `EnvironmentTeardownError`
// and exit 1 with every test passing (#1446). Nothing else notices it, so
// without this the stub could be dropped as dead tidying and the flake would
// come back with the next fast test file.

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
