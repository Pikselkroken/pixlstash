// Where closing the lightbox goes.
//
// The workflow library replaces the grid the lightbox is mounted in, so its
// tiles open a picture by leaving for the picture-grid route and asking, through
// `?from=`, to be sent back on close. Before that the close simply dropped
// `?overlay=` and the reader was left on All Pictures.
//
// This is the decision alone. That `ImageGrid` actually asks it, and asks it
// with the right answer for each kind of close, is pinned by
// `components/views/ImageGridOverlayCloseReturn.test.js`, which mounts the real
// component.

import { describe, it, expect } from "vitest";
import { overlayCloseTarget } from "./overlayRoute";

describe("overlayCloseTarget", () => {
  it("returns to the view the picture was opened from", () => {
    expect(overlayCloseTarget({ overlay: "812", from: "/workflows" })).toEqual({
      path: "/workflows",
      query: {},
    });
  });

  it("carries a share session's token back with it", () => {
    // The rest of the query belonged to the route being left; `?token=` belongs
    // to the session, and a navigation that drops it leaves the visitor on a URL
    // that 401s on the next reload (useAppNavigation.withShareToken).
    expect(
      overlayCloseTarget({
        overlay: "812",
        from: "/workflows",
        token: "example-share-token",
        path: "/home/me/shots",
      }),
    ).toEqual({
      path: "/workflows",
      query: { token: "example-share-token" },
    });
  });

  it("only drops ?overlay= when there is nowhere to go back to", () => {
    expect(
      overlayCloseTarget({ overlay: "812", path: "/home/me/shots" }),
    ).toEqual({ query: { path: "/home/me/shots" } });
  });

  it("keeps the rest of the query on the way back out", () => {
    // `?from=` is consumed, not carried: it is an instruction for this close.
    expect(
      overlayCloseTarget({ overlay: "812", from: "", review: "board" }),
    ).toEqual({ query: { review: "board" } });
  });

  it("refuses anything that is not a same-document absolute path", () => {
    // It arrives from the URL bar. vue-router would resolve a foreign one
    // against the route table and land on the catch-all, so this is a second
    // lock rather than the only one — but a navigation target taken from the
    // query should not need the catch-all to be safe.
    for (const from of [
      "https://example.com/workflows",
      "//example.com/workflows",
      "workflows",
      ["/workflows", "/models"],
      null,
    ]) {
      expect(overlayCloseTarget({ overlay: "812", from })).toEqual({
        query: {},
      });
    }
  });

  it("stays put, and still drops ?from=, for a close the grid makes itself", () => {
    // A reverse-image search, a delete or "use as input" closes the lightbox to
    // show its own result in the grid behind it. Leaving for the shelf would put
    // that result on a screen with no grid — and a `?from=` left in the query
    // would make the NEXT close jump instead.
    expect(
      overlayCloseTarget({ overlay: "812", from: "/workflows" }, false),
    ).toEqual({ query: {} });
  });

  it("survives a route with no query at all", () => {
    expect(overlayCloseTarget(undefined)).toEqual({ query: {} });
  });
});
