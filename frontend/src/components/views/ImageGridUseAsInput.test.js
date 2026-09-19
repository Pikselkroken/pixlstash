// "Use as input for…" from the lightbox (#1406): the Recipe tab's second
// action and the lightbox right-click menu both land here.
//
// The act is three things at once - close the lightbox, narrow the selection,
// open the run panel - and the middle one is the part that is easy to ship
// wrong, because the obvious rule is the wrong one here.
//
// `handleImageContextMenu` keeps a selection the right-clicked picture is
// already part of. That is right in the GRID, where the selection is on screen
// and the user can see what the menu is about to act on. It is wrong in the
// lightbox: `openOverlay` never touches `selectedImageIds`, so the selection is
// invisible, and both ways into this function name exactly one picture. Keeping
// an unseen 50-picture selection would hand the run panel all 50 from a control
// whose tooltip says "this picture".

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { ref } from "vue";
import { useSelectionStore } from "../../stores/useSelectionStore.js";
import { useProjectStore } from "../../stores/useProjectStore.js";
import { useSortStore } from "../../stores/useSortStore.js";
import {
  FROM_SELECTION,
  useWorkflowRunStore,
} from "../../stores/useWorkflowRunStore.js";

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

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: {}, params: {}, path: "/", name: "grid" }),
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn().mockResolvedValue(undefined),
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
        // The line above is a COMPILER option and this template was compiled by
        // Vite long before the test ran, so it cannot take effect: every
        // Vuetify tag warns "Failed to resolve component" instead, once per
        // mount. That says the mount is deliberately shallow, not that anything
        // is wrong, so it is dropped - and only it.
        //
        // Worth dropping because this file is FAST: vitest forwards console
        // output to the main process over rpc, and a write still in flight when
        // a quick worker tears down reds the whole run with every test passing
        // (`testing/setup.js`). This file was named in three of the five such
        // errors that failed #1446's gate.
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
  // `markEnd` logs every timed interaction in a dev build. Real behaviour,
  // nothing here asserts it, and the rpc argument above applies.
  vi.spyOn(console, "debug").mockImplementation(() => {});
});

describe("using the lightbox picture as a workflow's input", () => {
  it("narrows to the picture, and moves the shift-click anchor with it", () => {
    const wrapper = mountGrid();
    wrapper.vm.selectedImageIds = [11, 12, 13];
    wrapper.vm.lastSelectedImageId = 13;

    wrapper.vm.useOverlayPictureAsInput(42);

    expect(wrapper.vm.selectedImageIds).toEqual([42]);
    // Left on 13, this is the anchor the next shift-click ranges from - a
    // picture that is no longer in the selection at all.
    expect(wrapper.vm.lastSelectedImageId).toBe(42);
  });

  it("narrows even when the picture is already in the selection", () => {
    // The case the grid's own rule would preserve, and the reason it must not
    // here: the lightbox does not show the selection, so those other 12
    // pictures are ones the reader cannot see and did not ask to run.
    // `workflowRunStore.selectionIds` is the live grid selection and
    // `WorkflowRunPanel` reads it at run time, so leaving them in decides what
    // actually runs.
    const wrapper = mountGrid();
    wrapper.vm.selectedImageIds = [11, 42, 13];
    wrapper.vm.lastSelectedImageId = 11;

    wrapper.vm.useOverlayPictureAsInput(42);

    expect(wrapper.vm.selectedImageIds).toEqual([42]);
    expect(wrapper.vm.lastSelectedImageId).toBe(42);
  });

  it("opens the run panel on the selection", () => {
    const wrapper = mountGrid();
    const runStore = useWorkflowRunStore();
    runStore.open = false;

    wrapper.vm.useOverlayPictureAsInput(42);

    expect(runStore.open).toBe(true);
    expect(runStore.origin).toBe(FROM_SELECTION);
  });

  it("closes the lightbox, because the run panel is the rail it covers", () => {
    const wrapper = mountGrid();
    wrapper.vm.overlayOpen = true;
    wrapper.vm.overlayImageId = 42;

    wrapper.vm.useOverlayPictureAsInput(42);

    expect(wrapper.vm.overlayOpen).toBe(false);
  });

  it("falls back to the open picture when no id is passed", () => {
    const wrapper = mountGrid();
    wrapper.vm.overlayOpen = true;
    wrapper.vm.overlayImageId = 7;
    wrapper.vm.selectedImageIds = [];

    wrapper.vm.useOverlayPictureAsInput();

    expect(wrapper.vm.selectedImageIds).toEqual([7]);
  });

  it("does nothing at all with no picture to act on", () => {
    const wrapper = mountGrid();
    const runStore = useWorkflowRunStore();
    runStore.open = false;
    wrapper.vm.overlayImageId = null;
    wrapper.vm.selectedImageIds = [11];

    wrapper.vm.useOverlayPictureAsInput(null);

    expect(wrapper.vm.selectedImageIds).toEqual([11]);
    expect(runStore.open).toBe(false);
  });
});
