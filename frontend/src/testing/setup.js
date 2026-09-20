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
// ImageOverlay encodes a PNG - so a full run reported it ~67 times, every one
// of them noise about a capability this environment is never going to have.
//
// Returning null is exactly what jsdom returns after reporting it, so every
// caller's `if (!ctx)` branch behaves as before and only the report is gone. A
// suite that needs a drawable context replaces this for its own lifetime, the
// way `ImageOverlayMediaActions.test.js` already does.
//
// WHAT THIS IS NOT. It is not a fix for the teardown flake, and must not be
// read as one (#1446). Vitest forwards every worker console write to the main
// process over rpc, and one still in flight when a fast worker's environment
// tears down is raised as `EnvironmentTeardownError: Closing rpc while
// "onUserConsoleLog" was pending` - an unhandled error that exits the run 1
// with every test passing. These 67 were ~6% of the ~1079 writes a run
// forwards; 51 files still forward the other ~1012, one of them 69 on its own.
// Measured on the fixed tree: the two-file repro still fails 2 runs in 30 when
// squeezed onto one core (`taskset -c 0 npx vitest run <two files>`).
//
// The channel itself is what has to close, and neither `test.silent` nor
// `onConsoleLog` does it: both are read in the main process, while the worker
// installs its forwarding console unconditionally
// (`vitest/dist/chunks/base.*.js` → `createCustomConsole()`, which never
// consults `silent`). Measured: `--silent=true` still produced 3 teardown
// errors across 30 squeezed runs. Closing it needs a vitest upgrade past the
// teardown-rpc bug, which is a dependency decision and not this file's.
if (typeof HTMLCanvasElement !== "undefined") {
  HTMLCanvasElement.prototype.getContext = function getContext() {
    return null;
  };
}
