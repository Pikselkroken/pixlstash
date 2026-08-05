// The character row's active highlight, on the one path that reaches its
// single-selection fallback.
//
// The row's active class is
//
//   selectedCharacterIdSet.size > 0
//     ? selectedCharacterIdSet.has(char.id)
//     : selectionStore.selectedCharacter === char.id
//
// and the `else` half used to read a BARE `selectedCharacter`, an identifier
// that is not declared in the component's `<script setup>` at all. It resolved
// to `undefined`, so the comparison was always false and the branch could never
// light a row.
//
// That branch is not dead code. Every ordinary selection path writes the scalar
// and the id list together, so the set is non-empty and the ternary takes its
// first arm:
//
//   * `SideBar.selectCharacter` emits `ids: [numId]` for a real person, and
//     `useAppNavigation` writes both (`useAppNavigation.js:112-113`);
//   * the route parser derives `ids` from `params.id` when `?ids=` is absent
//     (`useViewStore.parseIds`), so `/character/7` yields `[7]` as well.
//
// The exception is `App.vue:391`: a share token scoped to a CHARACTER sets
// `selectionStore.selectedCharacter = ctx.resource_id` and never touches
// `selectedCharacterIds`, which stays at its `[]` default. That session renders
// the People section (it is gated `scopedResourceType !== 'picture_set'`), so
// the one person the token exists for was the one row that could never show as
// selected. That is the state this file pins.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount, flushPromises } from "@vue/test-utils";
import { ref } from "vue";

const apiGet = vi.fn();

vi.mock("../../utils/apiClient", async () => {
  const { ref: makeRef } = await import("vue");
  return {
    apiClient: {
      get: (...args) => apiGet(...args),
      post: vi.fn().mockResolvedValue({ data: {} }),
      patch: vi.fn().mockResolvedValue({ data: {} }),
      put: vi.fn().mockResolvedValue({ data: {} }),
      delete: vi.fn().mockResolvedValue({ data: {} }),
    },
    onSessionReset: () => () => {},
    activateShareToken: vi.fn(),
    appendShareToken: (url) => url,
    checkLoginStatus: vi.fn(),
    checkSession: vi.fn(),
    isAuthenticated: makeRef(true),
    isReadOnly: makeRef(false),
    sessionContext: makeRef(null),
    login: vi.fn(),
    logout: vi.fn(),
    newOperationBatchId: () => "cli-test",
    operationBatchHeaders: () => undefined,
    setRequestClientId: vi.fn(),
    notifySessionReset: vi.fn(),
    toBackendWebSocketUrl: () => "",
    API_BASE_URL: "/api/v1",
  };
});

vi.mock("vuetify/components", () => {
  const stubs = new Map();
  return new Proxy(
    {},
    {
      get(_t, prop) {
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
});

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: {}, params: {}, path: "/", name: "all-pictures" }),
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    currentRoute: ref({ query: {} }),
  }),
}));

globalThis.ResizeObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
globalThis.IntersectionObserver = class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

import { isReadOnly, sessionContext } from "../../utils/apiClient";
import { useSelectionStore } from "../../stores/useSelectionStore";
import SideBar from "./SideBar.vue";

const ADA = { id: 7, name: "Ada", image_count: 3, project_image_count: 3 };
const GRACE = { id: 9, name: "Grace", image_count: 5, project_image_count: 5 };
const CHARACTERS = [ADA, GRACE];

function respond(url) {
  const u = String(url ?? "");
  if (u.includes("/characters")) return { data: CHARACTERS };
  if (u.includes("/projects")) return { data: [] };
  if (u.includes("/picture_sets")) return { data: [] };
  if (u.includes("/summary")) return { data: { image_count: 3 } };
  return { data: [] };
}

async function mountSidebar() {
  const wrapper = mount(SideBar, {
    shallow: true,
    props: { backendUrl: "/api/v1" },
    global: {
      config: {
        compilerOptions: { isCustomElement: (tag) => tag.startsWith("v-") },
      },
    },
  });
  wrapper.vm.refreshSidebar();
  for (let i = 0; i < 5; i += 1) await flushPromises();
  return wrapper;
}

/**
 * Is the People-section row for `name` rendered with the active class?
 *
 * Located by the row's own `title`, which the template builds from the person's
 * name, so the assertion is tied to the row a user would actually click rather
 * than to a DOM position. Returns `null` when no such row was rendered, which
 * would otherwise read as a passing "not active".
 */
function characterRowIsActive(wrapper, name) {
  const row = wrapper
    .findAll(".sidebar-list-item")
    .find((el) => String(el.attributes("title") ?? "").startsWith(`${name} (`));
  if (!row) return null;
  return row.classes().includes("active");
}

let consoleWarn;

beforeEach(() => {
  setActivePinia(createPinia());
  isReadOnly.value = false;
  sessionContext.value = null;
  apiGet.mockReset().mockImplementation((url) => Promise.resolve(respond(url)));
  consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the character row's single-selection fallback", () => {
  it("lights the row when only the scalar selection is set", async () => {
    // Exactly the `App.vue:391` share-token state: the scalar names a real
    // person, the id list was never written. Before the fix the bare
    // `selectedCharacter` was `undefined` here and no row could go active.
    const selection = useSelectionStore();
    const wrapper = await mountSidebar();
    selection.selectedCharacter = ADA.id;
    selection.selectedCharacterIds = [];
    await wrapper.vm.$nextTick();

    expect(characterRowIsActive(wrapper, ADA.name)).toBe(true);
    // And only that row.
    expect(characterRowIsActive(wrapper, GRACE.name)).toBe(false);

    wrapper.unmount();
  });

  it("does not light a row when the scalar names nobody", async () => {
    // The other direction: "All Pictures" is not a person, so no person row may
    // claim the highlight. A fallback that matched loosely would light one.
    const selection = useSelectionStore();
    const wrapper = await mountSidebar();
    selection.selectedCharacter = "ALL";
    selection.selectedCharacterIds = [];
    await wrapper.vm.$nextTick();

    expect(characterRowIsActive(wrapper, ADA.name)).toBe(false);
    expect(characterRowIsActive(wrapper, GRACE.name)).toBe(false);

    wrapper.unmount();
  });

  it("still prefers the id set when the ordinary paths populate it", async () => {
    // The first arm of the ternary is what every normal selection uses; the fix
    // must not disturb it. Scalar and set deliberately disagree here so the
    // assertion can only pass if the SET won.
    const selection = useSelectionStore();
    const wrapper = await mountSidebar();
    selection.selectedCharacter = ADA.id;
    selection.selectedCharacterIds = [GRACE.id];
    await wrapper.vm.$nextTick();

    expect(characterRowIsActive(wrapper, GRACE.name)).toBe(true);
    expect(characterRowIsActive(wrapper, ADA.name)).toBe(false);

    wrapper.unmount();
  });

  it("lights every row of a genuine multi-selection", async () => {
    const selection = useSelectionStore();
    const wrapper = await mountSidebar();
    selection.selectedCharacter = ADA.id;
    selection.selectedCharacterIds = [ADA.id, GRACE.id];
    await wrapper.vm.$nextTick();

    expect(characterRowIsActive(wrapper, ADA.name)).toBe(true);
    expect(characterRowIsActive(wrapper, GRACE.name)).toBe(true);

    wrapper.unmount();
  });

  it("resolves the identifier, so no render warning is logged", async () => {
    // The cheap signal the bug produced: Vue warns once per render on a
    // template reference it cannot resolve.
    const selection = useSelectionStore();
    const wrapper = await mountSidebar();
    selection.selectedCharacter = ADA.id;
    selection.selectedCharacterIds = [];
    await wrapper.vm.$nextTick();

    const resolutionWarnings = consoleWarn.mock.calls.filter(([msg]) =>
      String(msg ?? "").includes("selectedCharacter"),
    );
    expect(resolutionWarnings).toEqual([]);

    wrapper.unmount();
  });
});
