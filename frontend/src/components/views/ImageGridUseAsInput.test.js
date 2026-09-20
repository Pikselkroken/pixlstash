// "Use as input for…" from the lightbox (#1406), now the Run popup with its
// workflow picker unset (#1407). The Recipe tab's second action and the
// lightbox right-click menu both land here.
//
// The part that is easy to ship wrong is WHICH pictures the popup opens on,
// because the obvious rule is the wrong one here. `handleImageContextMenu`
// keeps a selection the right-clicked picture is already part of, which is
// right in the GRID where the selection is on screen. It is wrong in the
// lightbox: `openOverlay` never touches `selectedImageIds`, so the selection
// is invisible, and both ways in name exactly one picture. Handing the popup
// an unseen 50-picture selection from a control whose tooltip says "this
// picture" is the regression this file exists to catch.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { ref } from "vue";
import { useSelectionStore } from "../../stores/useSelectionStore.js";
import { useProjectStore } from "../../stores/useProjectStore.js";
import { useSortStore } from "../../stores/useSortStore.js";
import { useRunDialogStore } from "../../stores/useRunDialogStore.js";

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
  it("opens the popup on that picture alone, with the workflow picker unset", () => {
    const wrapper = mountGrid();
    const runDialog = useRunDialogStore();

    wrapper.vm.runWorkflowOnPicture(42);

    expect(runDialog.source).toMatchObject({
      pictureIds: [42],
      pickWorkflow: true,
    });
  });

  it("opens on that picture even when others are selected", () => {
    // The case the grid's own rule would preserve, and the reason it must not
    // here: the lightbox does not show the selection, so those other two
    // pictures are ones the reader cannot see and did not ask to run.
    const wrapper = mountGrid();
    const runDialog = useRunDialogStore();
    wrapper.vm.selectedImageIds = [11, 42, 13];

    wrapper.vm.runWorkflowOnPicture(42);

    expect(runDialog.source.pictureIds).toEqual([42]);
  });

  it("closes the lightbox, which the popup would otherwise open behind", () => {
    const wrapper = mountGrid();
    wrapper.vm.overlayOpen = true;
    wrapper.vm.overlayImageId = 42;

    wrapper.vm.runWorkflowOnPicture(42);

    expect(wrapper.vm.overlayOpen).toBe(false);
  });

  it("falls back to NOTHING with no picture, never to the hidden selection", () => {
    const wrapper = mountGrid();
    const runDialog = useRunDialogStore();
    wrapper.vm.overlayImageId = 7;
    wrapper.vm.selectedImageIds = [11, 12];

    // Seeded with something recognisable first: `source` is null on a fresh
    // store, so asserting null against an untouched default would pass just as
    // well with the whole function body deleted.
    runDialog.openRun({ kind: "picture", pictureIds: [99] });
    wrapper.vm.runWorkflowOnPicture(null);

    expect(runDialog.source.pictureIds).toEqual([99]);
  });

  it("does take the whole selection from the grid's own menu entry", () => {
    // The sibling path, asserted here so the narrowing above cannot be
    // "fixed" into narrowing both: "Run a workflow on these…" means these.
    const wrapper = mountGrid();
    const runDialog = useRunDialogStore();
    wrapper.vm.selectedImageIds = [11, 12, 13];

    wrapper.vm.runWorkflowOnSelection();

    expect(runDialog.source.pictureIds).toEqual([11, 12, 13]);
  });
});
