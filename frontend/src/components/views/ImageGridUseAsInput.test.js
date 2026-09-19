// "Use as input for…" from the lightbox (#1406): the Recipe tab's second
// action and the lightbox right-click menu both land here.
//
// The act is three things at once - close the lightbox, narrow the selection,
// open the run panel - and the middle one is the part that is easy to ship
// wrong. A selection is shared state with no undo: `handleImageContextMenu`
// and `handleFaceBboxContextMenu` both narrow to one picture ONLY when it is
// not already selected, precisely so that right-clicking inside a selection
// you built on purpose does not throw it away. A new path that replaces
// unconditionally looks identical in every screenshot and loses the selection.

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
      },
    },
    props: { backendUrl: "/api/v1" },
  });
}

beforeEach(() => {
  setActivePinia(createPinia());
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

  it("keeps a selection the picture is already part of", () => {
    // The convention `handleImageContextMenu` follows. Acting on one picture
    // of a selection the user built is not a reason to discard the rest, and
    // the run panel reads the live selection, so this decides what runs.
    const wrapper = mountGrid();
    wrapper.vm.selectedImageIds = [11, 42, 13];
    wrapper.vm.lastSelectedImageId = 11;

    wrapper.vm.useOverlayPictureAsInput(42);

    expect(wrapper.vm.selectedImageIds).toEqual([11, 42, 13]);
    expect(wrapper.vm.lastSelectedImageId).toBe(11);
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
