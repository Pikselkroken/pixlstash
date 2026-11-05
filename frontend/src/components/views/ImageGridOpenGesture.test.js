// #2138: in the grid a click selects and a double-click opens, unless the
// user picked "Single click" in Settings > Appearance > Mouse. The first plain
// click in double-click mode explains the change once. Mounts the real grid the
// way ImageGridLightboxCloseReturn.test.js does and drives its handlers.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { ref } from "vue";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore.js";
import { useWhatsChangedStore } from "../../stores/useWhatsChangedStore";
import { useSelectionStore } from "../../stores/useSelectionStore.js";
import { useProjectStore } from "../../stores/useProjectStore.js";
import { useSortStore } from "../../stores/useSortStore.js";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore.js";

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

describe("grid open gesture", () => {
  it("double-click mode: a click selects without opening, a double-click opens", () => {
    const wrapper = mountGrid();
    wrapper.vm.handleThumbnailClick(IMG, 0, click());
    expect(wrapper.vm.lightboxOpen).toBe(false);
    expect(wrapper.vm.selectedImageIds).toEqual([41]);
    wrapper.vm.handleThumbnailDblClick(IMG, click());
    expect(wrapper.vm.lightboxOpen).toBe(true);
  });

  it("single-click mode: a click opens, and the double-click stays inert", () => {
    useUserPrefsStore().gridOpenGesture = "single";
    const wrapper = mountGrid();
    wrapper.vm.handleThumbnailClick(IMG, 0, click());
    expect(wrapper.vm.lightboxOpen).toBe(true);
    wrapper.vm.closeLightbox();
    wrapper.vm.handleThumbnailDblClick(IMG, click());
    expect(wrapper.vm.lightboxOpen).toBe(false);
  });

  it("a modified or touch-select double-click does not open", () => {
    const wrapper = mountGrid();
    wrapper.vm.handleThumbnailDblClick(IMG, click({ ctrlKey: true }));
    wrapper.vm.handleThumbnailDblClick(IMG, click({ shiftKey: true }));
    wrapper.vm.touchSelectMode = true;
    wrapper.vm.handleThumbnailDblClick(IMG, click());
    expect(wrapper.vm.lightboxOpen).toBe(false);
  });

  // The 2.0 What's changed checklist (#2145) replaced the one-time hint: the
  // grid reports the gesture, the store decides whether a list is up.
  it("a plain click ticks the checklist's click step, in either mode", () => {
    const tick = vi.spyOn(useWhatsChangedStore(), "tick");
    const wrapper = mountGrid();
    wrapper.vm.handleThumbnailClick(IMG, 0, click());
    expect(tick).toHaveBeenCalledWith("click");
    tick.mockClear();
    useUserPrefsStore().gridOpenGesture = "single";
    wrapper.vm.handleThumbnailClick(IMG, 0, click());
    expect(tick).toHaveBeenCalledWith("click");
  });

  it("a modified click is not the gesture and ticks nothing", () => {
    const tick = vi.spyOn(useWhatsChangedStore(), "tick");
    const wrapper = mountGrid();
    wrapper.vm.handleThumbnailClick(IMG, 0, click({ ctrlKey: true }));
    expect(tick).not.toHaveBeenCalled();
  });

  it("right-clicking a picture ticks the menu step", () => {
    const tick = vi.spyOn(useWhatsChangedStore(), "tick");
    const wrapper = mountGrid();
    wrapper.vm.handleImageContextMenu(IMG, { clientX: 5, clientY: 5 });
    expect(tick).toHaveBeenCalledWith("menu");
  });

  it("right-clicking a face box on a picture ticks it too", () => {
    const tick = vi.spyOn(useWhatsChangedStore(), "tick");
    const wrapper = mountGrid();
    wrapper.vm.handleFaceBboxContextMenu(IMG, { face: { id: 3 } }, { clientX: 5, clientY: 5 });
    expect(tick).toHaveBeenCalledWith("menu");
  });

  it("a double-click while the lightbox is already up does not reopen it", () => {
    const wrapper = mountGrid();
    wrapper.vm.handleThumbnailClick(IMG, 0, click());
    wrapper.vm.openLightbox(IMG);
    wrapper.vm.lightboxInitialExpandedStackIds = ["sentinel"];
    wrapper.vm.handleThumbnailDblClick(IMG, click());
    vi.advanceTimersByTime(600);
    // openLightbox re-captures this; untouched means it did not run again.
    expect(wrapper.vm.lightboxInitialExpandedStackIds).toEqual(["sentinel"]);
  });
});

// The review board is a separate overlay from the lightbox, and the grid's keys
// and drag go inert under it through the REVIEW store's own flag. The keyboard
// and drag-drop tests inject that flag as a mock ref, so only a real store can
// show the grid still reads it.
describe("grid under the review board", () => {
  it("follows the review store's overlayOpen", () => {
    const wrapper = mountGrid();
    const reviews = useReviewSessionsStore();
    expect(wrapper.vm.reviewOverlayOpen).toBe(false);
    reviews.overlayOpen = true;
    expect(wrapper.vm.reviewOverlayOpen).toBe(true);
  });
});
