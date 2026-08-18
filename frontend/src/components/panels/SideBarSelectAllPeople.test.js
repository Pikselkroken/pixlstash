// Ctrl/Cmd+A over the sidebar selects every person the People list is showing.
//
// Select-all is implemented per surface — the grid takes every picture, the
// Duplicates queue every group — and the People list had no implementation, so
// the key there fell back to the page default: a text selection.
//
// What this file pins is the ownership, in both directions, against the REAL
// grid handler rather than a stand-in: over the sidebar the key selects the
// people and the grid's selection is left alone; away from the sidebar the grid
// still takes every image. Plus the payload shape the route depends on, and the
// surfaces that keep the key for themselves.
//
// The route half of the contract — that this payload actually reaches the URL
// as `?ids=…&mode=union` — is pinned in
// `composables/useAppNavigationMultiSelect.test.js`, because that is where the
// payload is consumed.

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

// The sidebar declines on the Duplicates view, so the route name has to be
// switchable per test.
let routeName = "all-pictures";

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: {}, params: {}, path: "/", name: routeName }),
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
import { useProjectStore } from "../../stores/useProjectStore";
import { useGridKeyboardNav } from "../../composables/useGridKeyboardNav";
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
    attachTo: document.body,
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
 * The REAL grid Ctrl+A owner, on `window` in the bubble phase exactly as
 * `ImageGrid` registers it. A stub would only prove the test's own wiring; this
 * proves the key never reaches the code that would select every image.
 */
function attachGridKeyboardNav() {
  const selectedImageIds = ref([]);
  const deps = {
    scrollWrapper: ref(null),
    allGridImages: ref([{ id: "p1" }, { id: "p2" }]),
    rowHeight: ref(128),
    visibleStart: ref(0),
    overlayOpen: ref(false),
    reviewOverlayOpen: ref(false),
    showSelectionBar: ref(false),
    searchResultsActive: ref(false),
    selectedImageIds,
    lastSelectedImageId: null,
    cursorIdx: ref(null),
    isMultiCharacterView: ref(false),
    isSetOverlapView: ref(false),
    hoveredImageIdx: ref(null),
    toolbarSelectionMenuOpen: ref(false),
    isJustifiedMode: ref(false),
    justifiedLayout: ref(null),
  };
  const { handleKeyDown } = useGridKeyboardNav(deps, {}, vi.fn(), {
    clearFaceSelection: vi.fn(),
    clearSearchQuery: vi.fn(),
    scrollCursorIntoView: vi.fn(),
    openOverlay: vi.fn(),
    deleteSelected: vi.fn(),
    selectionBarRef: ref({ openTagInput: vi.fn() }),
    applyScoresForSelection: vi.fn(),
    setScore: vi.fn(),
  });
  window.addEventListener("keydown", handleKeyDown);
  return {
    selectedImageIds,
    detach: () => window.removeEventListener("keydown", handleKeyDown),
  };
}

/** Dispatch Ctrl+A the way a real key press arrives, and report what happened. */
function pressSelectAll(target = document.body) {
  const event = new KeyboardEvent("keydown", {
    key: "a",
    ctrlKey: true,
    bubbles: true,
    cancelable: true,
  });
  target.dispatchEvent(event);
  return event;
}

/** The last `select-character` payload, or null. */
function lastSelection(wrapper) {
  const emitted = wrapper.emitted("select-character");
  if (!emitted || !emitted.length) return null;
  return emitted[emitted.length - 1][0] ?? null;
}

let grid;

beforeEach(() => {
  setActivePinia(createPinia());
  routeName = "all-pictures";
  isReadOnly.value = false;
  sessionContext.value = null;
  apiGet.mockReset().mockImplementation((url) => Promise.resolve(respond(url)));
  vi.spyOn(console, "warn").mockImplementation(() => {});
  vi.spyOn(console, "error").mockImplementation(() => {});
  grid = attachGridKeyboardNav();
});

afterEach(() => {
  grid.detach();
  vi.restoreAllMocks();
});

describe("Ctrl+A over the sidebar's People list", () => {
  it("selects every person it is showing, in a payload the route can carry", async () => {
    const wrapper = await mountSidebar();
    await wrapper.find(".sidebar").trigger("mouseenter");

    const event = pressSelectAll();
    const selection = lastSelection(wrapper);

    expect(selection?.ids).toEqual([ADA.id, GRACE.id]);
    // The primary id has to name a real person: `pushRouteForCurrentSelection`
    // drops `?ids=…` when it is ALL_PICTURES_ID, which loses the selection.
    expect(selection.ids).toContain(Number(selection.id));
    // And the mode has to be stated, or a remembered "intersection" turns
    // "select all people" into an empty grid.
    expect(selection.multiMode).toBe("union");
    // Emitting `select-set: null` would be the same bug by another route:
    // `handleSelectSet(null)` writes selectedCharacter = ALL synchronously.
    expect(wrapper.emitted("select-set")).toBeUndefined();
    // The browser's text select-all is what the bug looked like; it must not run.
    expect(event.defaultPrevented).toBe(true);
    // Neither must the grid's select-all-images.
    expect(grid.selectedImageIds.value).toEqual([]);

    wrapper.unmount();
  });

  it("leaves the key to the grid when the pointer is not over the sidebar", async () => {
    const wrapper = await mountSidebar();

    const event = pressSelectAll();

    expect(lastSelection(wrapper)).toBe(null);
    expect(event.defaultPrevented).toBe(true); // the grid claims it instead
    expect(grid.selectedImageIds.value).toEqual(["p1", "p2"]);

    wrapper.unmount();
  });

  it("leaves a focused text field its native select-all", async () => {
    const wrapper = await mountSidebar();
    await wrapper.find(".sidebar").trigger("mouseenter");
    // A sidebar field with focus — the state that produced the reported
    // "selects all text in the sidebar".
    const field = document.createElement("input");
    wrapper.find(".sidebar").element.appendChild(field);
    field.focus();

    const event = pressSelectAll(field);

    expect(lastSelection(wrapper)).toBe(null);
    expect(event.defaultPrevented).toBe(false);
    expect(grid.selectedImageIds.value).toEqual([]);

    wrapper.unmount();
  });

  it.each([
    [
      "a Vuetify dialog",
      "v-overlay--active",
      '<div class="v-overlay__scrim"></div>',
    ],
    ["the review overlay", "rs-overlay", ""],
  ])("declines while %s covers the app", async (_label, className, inner) => {
    const wrapper = await mountSidebar();
    await wrapper.find(".sidebar").trigger("mouseenter");
    // Those surfaces own the keyboard: acting behind a scrim is invisible, and
    // the review overlay deliberately leaves Ctrl+A free so its text copies.
    const cover = document.createElement("div");
    cover.className = className;
    cover.innerHTML = inner;
    document.body.appendChild(cover);

    pressSelectAll();

    // Declining means the key is left to whoever else owns it; what this pins
    // is that the sidebar took nothing.
    expect(lastSelection(wrapper)).toBe(null);

    cover.remove();
    wrapper.unmount();
  });

  it("declines on the Duplicates view, which owns Ctrl+A for its queue", async () => {
    routeName = "duplicates";
    const wrapper = await mountSidebar();
    await wrapper.find(".sidebar").trigger("mouseenter");

    pressSelectAll();

    // The queue's own Ctrl+A lives on a document BUBBLE listener, so the
    // sidebar declining is what keeps it reachable.
    expect(lastSelection(wrapper)).toBe(null);

    wrapper.unmount();
  });

  it("declines in project view mode, where the tree shows a different list", async () => {
    // The project tab renders per-project People groups from
    // `sortedCharacters.filter(…)`, several projects at once, so "all people"
    // has no single answer there.
    useProjectStore().projectViewMode = "project";
    const wrapper = await mountSidebar();
    await wrapper.find(".sidebar").trigger("mouseenter");

    pressSelectAll();

    expect(lastSelection(wrapper)).toBe(null);

    wrapper.unmount();
  });

  it("ignores a held chord, so it cannot push a route per repeat", async () => {
    const wrapper = await mountSidebar();
    await wrapper.find(".sidebar").trigger("mouseenter");

    const event = new KeyboardEvent("keydown", {
      key: "a",
      ctrlKey: true,
      repeat: true,
      bubbles: true,
      cancelable: true,
    });
    document.body.dispatchEvent(event);

    expect(lastSelection(wrapper)).toBe(null);

    wrapper.unmount();
  });
});
