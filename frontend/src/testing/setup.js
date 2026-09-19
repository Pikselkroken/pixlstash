// Global test environment, loaded by vitest via `setupFiles` in vite.config.js.
//
// jsdom implements neither observer API, and any component that measures itself
// throws on construction without them. Every suite that mounts such a component
// used to declare its own identical no-op class; there is nothing suite-specific
// about "this environment has no layout", so it belongs here once.
//
// Assigned unconditionally: jsdom never provides these, so there is no real
// implementation to clobber, and a conditional would silently skip the stub if
// a future jsdom shipped a partial one.

class NoopObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() {
    return [];
  }
}

globalThis.ResizeObserver = NoopObserver;
globalThis.IntersectionObserver = NoopObserver;

// Scrolling is layout, which jsdom also does not do. Components call this for
// keyboard navigation and focus management; unstubbed it is simply absent.
if (typeof Element !== "undefined" && !Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {};
}

// jsdom has no canvas: `getContext()` reports "Not implemented:
// HTMLCanvasElement's getContext() method: without installing the canvas npm
// package" through the virtual console and then returns null. Three components
// call it - ImageGrid measures label widths, StatsSidebar draws its sparklines,
// ImageOverlay encodes a PNG - so a full run reported it ~67 times.
//
// That report is not cosmetic. Vitest forwards worker console output to the
// main process over rpc, and a write still in flight when a fast worker's
// environment tears down is raised as `EnvironmentTeardownError: Closing rpc
// while "onUserConsoleLog" was pending` - an unhandled error that exits the run
// 1 with every test passing. It lands on whichever files finish quickest, so
// the gate goes red or green on the file list rather than on the code: #1446
// failed with 264 files and 4529 tests all green, while the same tree passed
// locally.
//
// Returning null is exactly what jsdom returns after reporting it, so every
// caller's `if (!ctx)` branch behaves as before and only the report is gone. A
// suite that needs a drawable context replaces this for its own lifetime, the
// way `ImageOverlayMediaActions.test.js` already does.
if (typeof HTMLCanvasElement !== "undefined") {
  HTMLCanvasElement.prototype.getContext = function getContext() {
    return null;
  };
}
