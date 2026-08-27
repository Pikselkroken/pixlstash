/**
 * Stand-ins for the `vuetify/components` barrel.
 *
 * The barrel pulls in each component's sibling CSS. Suites that only need the
 * Vuetify tags to *resolve* (rather than to render Vuetify's real behaviour)
 * stub the whole barrel; a Proxy does it without naming every component, so a
 * template that reaches for one more `v-*` tag does not break the suite.
 *
 * NOT applied globally from `testing/setup.js` on purpose: some suites mount
 * AppDialog / AppButton and depend on the REAL components, which is what the
 * `server.deps.inline: ['vuetify']` entry in vite.config.js exists to enable.
 * Stubbing the barrel for everyone would silently gut those.
 *
 * Usage - the dynamic import is required because `vi.mock` is hoisted above
 * every top-level binding in the calling file:
 *
 *   vi.mock("vuetify/components", async () => {
 *     const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
 *     return vuetifyComponentStubs();
 *   });
 *
 * @returns {Proxy} a module namespace yielding a stub per requested name.
 */
export function vuetifyComponentStubs() {
  const stubs = new Map();
  return new Proxy(
    {},
    {
      get(_target, prop) {
        if (prop === "__esModule") return true;
        if (typeof prop !== "string") return undefined;
        if (!stubs.has(prop)) {
          stubs.set(prop, { name: prop, template: "<div><slot /></div>" });
        }
        return stubs.get(prop);
      },
      has: () => true,
    },
  );
}
