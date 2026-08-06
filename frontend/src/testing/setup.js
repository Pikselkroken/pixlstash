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
