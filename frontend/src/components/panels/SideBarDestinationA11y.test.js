// The sidebar's top-level destinations, as keyboard-operable controls.
//
// All Pictures, Duplicates and Scrapheap were clickable `<div>`s: no tabindex,
// no role, no `aria-current`. A keyboard-only user could not reach or activate
// any of them, and a screen reader was never told which destination the session
// was in. Both layouts rendered them the same way, so the docked rail was
// unreachable too.
//
// They are `<button>`s now, which is what buys Tab reach and Enter/Space
// activation - neither of which jsdom simulates, so what is asserted here is
// the element type that provides them, plus the `aria-current` that no element
// type provides for free. Both directions: the active destination announces as
// current, the inactive ones say nothing, and clicking still does what it did.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount, flushPromises } from "@vue/test-utils";
import { reactive, ref } from "vue";

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

// Reactive, because `isDuplicatesView` is a computed over `route.name`: a plain
// object would let the test set the name and see nothing re-render.
const route = reactive({
  query: {},
  params: {},
  path: "/",
  name: "all-pictures",
});

vi.mock("vue-router", () => ({
  useRoute: () => route,
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
import { useSidebarStore } from "../../stores/useSidebarStore";
import SideBar from "./SideBar.vue";

const ALL_ID = "ALL";
const SCRAPHEAP_ID = "SCRAPHEAP";
const DESTINATIONS = ["All Pictures", "Duplicates", "Scrapheap"];

function respond(url) {
  const u = String(url ?? "");
  if (u.includes("/characters")) return { data: [] };
  if (u.includes("/projects")) return { data: [] };
  if (u.includes("/picture_sets")) return { data: [] };
  if (u.includes("/summary")) return { data: { image_count: 0 } };
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
  await flushPromises();
  return wrapper;
}

/** The expanded sidebar's destination row, located by its visible label. */
function destination(wrapper, label) {
  return wrapper
    .findAll(".sidebar-list-item")
    .find(
      (el) =>
        el.find(".sidebar-list-label").exists() &&
        el.find(".sidebar-list-label").text() === label,
    );
}

/** The docked rail's destination, located by its title (the rail has no labels). */
function dockedDestination(wrapper, label) {
  return wrapper
    .findAll(".sidebar-collapsed-item")
    .find((el) => el.attributes("title") === label);
}

beforeEach(() => {
  setActivePinia(createPinia());
  // The docked/expanded choice is persisted, so without this the rail test
  // leaks into every test after it and the expanded rows stop rendering.
  window.localStorage.clear();
  isReadOnly.value = false;
  sessionContext.value = null;
  route.name = "all-pictures";
  apiGet.mockReset().mockImplementation((url) => Promise.resolve(respond(url)));
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the sidebar's destinations are keyboard-operable", () => {
  it("renders every destination as a button", async () => {
    // The whole defect in one assertion: a <div> here is unreachable by Tab and
    // deaf to Enter and Space, however it is styled.
    const wrapper = await mountSidebar();

    for (const label of DESTINATIONS) {
      const row = destination(wrapper, label);
      expect(row, `${label} row is missing`).toBeTruthy();
      expect(row.element.tagName, label).toBe("BUTTON");
      // A button taken out of the tab order would be the same bug wearing the
      // right tag.
      expect(row.attributes("tabindex")).toBeUndefined();
    }

    wrapper.unmount();
  });

  it("renders the docked rail's destinations as buttons too", async () => {
    // The rail is the same three destinations in the narrow layout. Fixing only
    // the wide one leaves a keyboard user stranded whenever the sidebar docks.
    const wrapper = await mountSidebar();
    useSidebarStore().setSidebarDocked(true);
    await wrapper.vm.$nextTick();

    for (const label of DESTINATIONS) {
      const item = dockedDestination(wrapper, label);
      expect(item, `${label} rail item is missing`).toBeTruthy();
      expect(item.element.tagName, label).toBe("BUTTON");
      // The rail shows an icon and no text, so the name has to be spelled out.
      expect(item.attributes("aria-label")).toBe(label);
    }

    wrapper.unmount();
  });
});

describe("aria-current names the destination you are in", () => {
  it("marks All Pictures and nothing else", async () => {
    const selection = useSelectionStore();
    const wrapper = await mountSidebar();
    selection.selectedCharacter = ALL_ID;
    selection.selectedCharacterIds = [];
    await wrapper.vm.$nextTick();

    expect(
      destination(wrapper, "All Pictures").attributes("aria-current"),
    ).toBe("page");
    expect(
      destination(wrapper, "Scrapheap").attributes("aria-current"),
    ).toBeUndefined();
    expect(
      destination(wrapper, "Duplicates").attributes("aria-current"),
    ).toBeUndefined();

    wrapper.unmount();
  });

  it("moves to Scrapheap when the selection does", async () => {
    const selection = useSelectionStore();
    const wrapper = await mountSidebar();
    selection.selectedCharacter = SCRAPHEAP_ID;
    selection.selectedCharacterIds = [];
    await wrapper.vm.$nextTick();

    expect(destination(wrapper, "Scrapheap").attributes("aria-current")).toBe(
      "page",
    );
    expect(
      destination(wrapper, "All Pictures").attributes("aria-current"),
    ).toBeUndefined();

    wrapper.unmount();
  });

  it("marks Duplicates on the duplicates route", async () => {
    // Duplicates is addressed by route name, not by a selection sentinel.
    const wrapper = await mountSidebar();
    route.name = "duplicates";
    await wrapper.vm.$nextTick();

    expect(destination(wrapper, "Duplicates").attributes("aria-current")).toBe(
      "page",
    );
    expect(
      destination(wrapper, "All Pictures").attributes("aria-current"),
    ).toBeUndefined();

    wrapper.unmount();
  });
});

describe("what the conversion must not change", () => {
  it("still activates on click", async () => {
    const wrapper = await mountSidebar();

    await destination(wrapper, "Duplicates").trigger("click");

    expect(wrapper.emitted("select-duplicates")).toHaveLength(1);

    wrapper.unmount();
  });

  it("keeps a read-only Duplicates focusable and explained", async () => {
    // `disabled` would take the row out of the tab order AND kill the title
    // that is the only statement of why it is inert. `aria-disabled` says the
    // same thing to assistive tech without either loss.
    isReadOnly.value = true;
    const wrapper = await mountSidebar();

    const row = destination(wrapper, "Duplicates");
    expect(row.attributes("disabled")).toBeUndefined();
    expect(row.attributes("aria-disabled")).toBe("true");
    expect(row.attributes("title")).toBeTruthy();

    await row.trigger("click");
    expect(wrapper.emitted("select-duplicates")).toBeUndefined();

    wrapper.unmount();
  });
});
