// #2138: in the grid a click selects and a double-click opens, unless the
// user picked "Single click" in Settings > Appearance > Mouse. The first plain
// click in double-click mode explains the change once. Mounts the real grid the
// way ImageGridOverlayCloseReturn.test.js does and drives its handlers.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { ref } from "vue";
import { useNoticeStore } from "../../stores/useNoticeStore.js";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore.js";
import { useSelectionStore } from "../../stores/useSelectionStore.js";
import { useProjectStore } from "../../stores/useProjectStore.js";
import { useSortStore } from "../../stores/useSortStore.js";

vi.mock("../../utils/apiClient", async () => {
  const { ref: makeRef, computed: makeComputed } = await import("vue");
  return {
    onSessionReset: () => () => {},
    apiClient: {
      get: vi.fn().mockResolvedValue({ data: [] }),
      post: vi.fn().mockResolvedValue({ data: {} }),
      patch: vi.fn(),
      put: vi.fn(),
      delete: vi.fn(),
    },
    activateShareToken: vi.fn(),
    appendShareToken: (url) => url,
    checkLoginStatus: vi.fn(),
    checkSession: vi.fn(),
    isAuthenticated: makeRef(true),
    isReadOnly: makeComputed(() => false),
    login: vi.fn(),
    logout: vi.fn(),
    sessionContext: makeRef({ scope: "ALL" }),
    setRequestClientId: vi.fn(),
    API_BASE_URL: "/api/v1",
  };
});

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

// The query the grid reads on close. Written per test, which is the whole point:
// the lightbox was opened from somewhere, and that somewhere is in the URL.
const routeQuery = {};
const replace = vi.fn().mockResolvedValue(undefined);

// The real router's shape for the two answers that matter: a named record for a
// destination it has, an unnamed catch-all match for anything else (verified
// against `createRouter` with this app's table - `matched.length` is 1 either
// way, which is why the name is what decides).
const resolve = (path) =>
  ["/", "/workflows", "/workflows-next", "/models"].includes(path)
    ? { name: path === "/" ? "all-pictures" : path.slice(1), matched: [{}] }
    : { name: undefined, matched: [{}] };

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: routeQuery, params: {}, path: "/", name: "grid" }),
  useRouter: () => ({
    push: vi.fn(),
    replace: (...args) => replace(...args),
    resolve,
    currentRoute: ref({ query: {} }),
  }),
}));

import ImageGrid from "./ImageGrid.vue";

function mountGrid() {
  const selectionStore = useSelectionStore();
  const projectStore = useProjectStore();
  const sortStore = useSortStore();
  selectionStore.selectedCharacter = "ALL";
  selectionStore.selectedSet = null;
  selectionStore.selectedSetIds = [];
  projectStore.projectViewMode = "global";
  projectStore.selectedProjectId = null;
  sortStore.selectedSort = "DATE";
  sortStore.selectedDescending = true;

  return mount(ImageGrid, {
    shallow: true,
    global: {
      config: {
        compilerOptions: { isCustomElement: (tag) => tag.startsWith("v-") },
        // `isCustomElement` above is a COMPILER option, and this component's
        // template was compiled by Vite long before the test ran, so it cannot
        // take effect: every Vuetify tag in the tree warns "Failed to resolve
        // component" instead, once per mount. That is a statement about the
        // mount being deliberately shallow, not about the code, so it is
        // dropped here - and only it. Every other Vue warning still prints,
        // because those are ones this file would want to see.
        //
        // Dropping it is also what keeps this file off the rpc that carries
        // console output to the main process: a write still in flight when a
        // fast file's worker tears down reds the whole run (see
        // `testing/setup.js`), and this file is fast.
        warnHandler(message, _instance, trace) {
          if (message.startsWith("Failed to resolve component")) return;
          console.warn(`[Vue warn]: ${message}${trace}`);
        },
      },
    },
    props: { backendUrl: "/api/v1" },
  });
}

beforeEach(() => {
  setActivePinia(createPinia());
  window.localStorage.clear();
  vi.spyOn(console, "debug").mockImplementation(() => {});
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

const IMG = { id: 41 };
const click = (init = {}) => ({
  ctrlKey: false,
  metaKey: false,
  shiftKey: false,
  stopPropagation: vi.fn(),
  ...init,
});
const hints = () =>
  useNoticeStore().notices.filter((n) => n.text.includes("Double-click"));

describe("grid open gesture", () => {
  it("double-click mode: a click selects without opening, a double-click opens", () => {
    const wrapper = mountGrid();
    wrapper.vm.handleThumbnailClick(IMG, 0, click());
    expect(wrapper.vm.overlayOpen).toBe(false);
    expect(wrapper.vm.selectedImageIds).toEqual([41]);
    wrapper.vm.handleThumbnailDblClick(IMG, click());
    expect(wrapper.vm.overlayOpen).toBe(true);
  });

  it("single-click mode: a click opens, and the double-click stays inert", () => {
    useUserPrefsStore().gridOpenGesture = "single";
    const wrapper = mountGrid();
    wrapper.vm.handleThumbnailClick(IMG, 0, click());
    expect(wrapper.vm.overlayOpen).toBe(true);
    wrapper.vm.closeOverlay();
    wrapper.vm.handleThumbnailDblClick(IMG, click());
    expect(wrapper.vm.overlayOpen).toBe(false);
  });

  it("a modified or touch-select double-click does not open", () => {
    const wrapper = mountGrid();
    wrapper.vm.handleThumbnailDblClick(IMG, click({ ctrlKey: true }));
    wrapper.vm.handleThumbnailDblClick(IMG, click({ shiftKey: true }));
    wrapper.vm.touchSelectMode = true;
    wrapper.vm.handleThumbnailDblClick(IMG, click());
    expect(wrapper.vm.overlayOpen).toBe(false);
  });

  it("the first plain click explains the change once, with a Settings action", () => {
    const wrapper = mountGrid();
    wrapper.vm.handleThumbnailClick(IMG, 0, click());
    vi.advanceTimersByTime(600);
    expect(hints()).toHaveLength(1);
    hints()[0].action.handler();
    expect(wrapper.emitted("open-settings")).toEqual([["appearance"]]);
    // Gone from the stack; it must not come back.
    useNoticeStore().notices = [];
    wrapper.vm.handleThumbnailClick(IMG, 0, click());
    vi.advanceTimersByTime(600);
    expect(hints()).toHaveLength(0);
    // Remembered across a reload.
    const again = mountGrid();
    again.vm.handleThumbnailClick(IMG, 0, click());
    vi.advanceTimersByTime(600);
    expect(hints()).toHaveLength(0);
  });

  it("a double-click while the lightbox is already up neither reopens it nor hints", () => {
    const wrapper = mountGrid();
    wrapper.vm.handleThumbnailClick(IMG, 0, click());
    wrapper.vm.openOverlay(IMG);
    wrapper.vm.overlayInitialExpandedStackIds = ["sentinel"];
    wrapper.vm.handleThumbnailDblClick(IMG, click());
    vi.advanceTimersByTime(600);
    // openOverlay re-captures this; untouched means it did not run again.
    expect(wrapper.vm.overlayInitialExpandedStackIds).toEqual(["sentinel"]);
    expect(hints()).toHaveLength(0);
  });

  it("a modified double-click still cancels the pending hint", () => {
    const wrapper = mountGrid();
    wrapper.vm.handleThumbnailClick(IMG, 0, click());
    wrapper.vm.handleThumbnailDblClick(IMG, click({ ctrlKey: true }));
    vi.advanceTimersByTime(600);
    expect(hints()).toHaveLength(0);
  });

  it("a double-click never raises the hint", () => {
    const wrapper = mountGrid();
    wrapper.vm.handleThumbnailClick(IMG, 0, click());
    wrapper.vm.handleThumbnailDblClick(IMG, click());
    vi.advanceTimersByTime(600);
    expect(hints()).toHaveLength(0);
  });
});
