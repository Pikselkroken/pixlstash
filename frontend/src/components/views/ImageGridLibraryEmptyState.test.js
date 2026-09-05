// Does the empty-library state actually render inside the grid, and does its
// folder button reach anything?
//
// The component's own suite mounts it in isolation, so every one of its
// assertions passed with the state disconnected from the app entirely - proved
// by disabling `showLibraryEmptyState`, the `@choose-folder` binding and the
// `defineExpose` entry all at once and watching 606 tests stay green. These are
// the two things that were not covered, plus the three conditions under which
// the state must NOT appear.
//
// Mounts the real ImageGrid, following ImageGridEmptyScrapheapFromSidebar.js.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { useSelectionStore } from "../../stores/useSelectionStore.js";
import { useProjectStore } from "../../stores/useProjectStore.js";
import { useSortStore } from "../../stores/useSortStore.js";
import { ref, nextTick } from "vue";

const apiGet = vi.fn();
const apiPost = vi.fn();
const apiPatch = vi.fn();
const apiPut = vi.fn();
const apiDelete = vi.fn();

// Async factory with a local `await import("vue")`: the store imports above
// pull in apiClient, so this factory runs BEFORE the file's own top-level `vue`
// import has initialised. Closing over that binding throws "Cannot access
// __vi_import__ before initialization".
vi.mock("../../utils/apiClient", async () => {
  const { ref: makeRef, computed: makeComputed } = await import("vue");
  const isAuthenticated = makeRef(true);
  const readOnly = makeRef(false);
  const sessionContext = makeRef({ scope: "ALL" });
  return {
    onSessionReset: () => () => {},
    apiClient: {
      get: (...args) => apiGet(...args),
      post: (...args) => apiPost(...args),
      patch: (...args) => apiPatch(...args),
      put: (...args) => apiPut(...args),
      delete: (...args) => apiDelete(...args),
    },
    activateShareToken: vi.fn(),
    appendShareToken: (url) => url,
    checkLoginStatus: vi.fn(),
    checkSession: vi.fn(),
    isAuthenticated,
    isReadOnly: makeComputed(() => readOnly.value),
    login: vi.fn(),
    logout: vi.fn(),
    sessionContext,
    setRequestClientId: vi.fn(),
    API_BASE_URL: "/api/v1",
    // Reached back through the module so a test can flip the session scope.
    __readOnly: readOnly,
  };
});

// The `vuetify/components` barrel pulls component CSS that Vitest cannot load
// from node_modules; hand back a trivial stub for whatever name is imported.
vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});



vi.mock("vue-router", () => ({
  useRoute: () => ({ query: {}, params: {}, path: "/", name: "grid" }),
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    currentRoute: ref({ query: {} }),
  }),
}));

import ImageGrid from "./ImageGrid.vue";
import * as apiClient from "../../utils/apiClient";
import LibraryEmptyState from "./LibraryEmptyState.vue";

const ALL_PICTURES_ID = "ALL";

/** The count request the empty-library claim rests on. */
const SUMMARY_URL = `/characters/${ALL_PICTURES_ID}/summary`;

function mountGrid() {
  const selectionStore = useSelectionStore();
  const projectStore = useProjectStore();
  const sortStore = useSortStore();
  selectionStore.selectedCharacter = ALL_PICTURES_ID;
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

/** Let the grid finish its first load with nothing in it. */
async function settleEmpty(wrapper) {
  for (let i = 0; i < 8; i += 1) await nextTick();
  wrapper.vm.gridReady = true;
  wrapper.vm.emptyStateDelayPassed = true;
  await nextTick();
  return wrapper;
}

const emptyState = (wrapper) => wrapper.findComponent(LibraryEmptyState);

beforeEach(() => {
  setActivePinia(createPinia());
  apiGet.mockReset();
  apiPost.mockReset();
  apiPatch.mockReset();
  apiPut.mockReset();
  apiDelete.mockReset();
  apiClient.__readOnly.value = false;
  // Every read answers empty; the count answers zero.
  apiGet.mockImplementation((url) => {
    if (String(url).includes("/summary")) {
      return Promise.resolve({ data: { image_count: 0 } });
    }
    return Promise.resolve({ data: { images: [], total: 0 } });
  });
  apiPost.mockResolvedValue({ data: {} });
});

describe("an install with nothing in it", () => {
  it("renders the three routes out, inside the real grid", async () => {
    const wrapper = await settleEmpty(mountGrid());

    expect(emptyState(wrapper).exists()).toBe(true);
  });

  it("reaches the app with the folder route", async () => {
    const wrapper = await settleEmpty(mountGrid());

    await emptyState(wrapper).vm.$emit("choose-folder");

    expect(wrapper.emitted("choose-folder")).toHaveLength(1);
  });

  it("asks for the workflows pane by name for ComfyUI", async () => {
    // Not just "open settings": the ComfyUI URL lives on that pane, and an
    // unnamed tab lands on Appearance.
    const wrapper = await settleEmpty(mountGrid());

    await emptyState(wrapper).vm.$emit("connect-comfyui");

    expect(wrapper.emitted("open-settings")[0]).toEqual(["workflows"]);
  });

  it("passes chosen files up as a local import", async () => {
    const wrapper = await settleEmpty(mountGrid());
    const files = [new File(["x"], "one.jpg")];

    await emptyState(wrapper).vm.$emit("add-files", files);

    expect(wrapper.emitted("local-import")[0][0]).toEqual({ files });
  });

  it("drops what PixlStash cannot read, and says so", async () => {
    // An OS picker's `accept` is advisory - "All Files" is always on offer - so
    // this is the same filter and the same notice key the two drop paths use.
    // Without it this was the one import route that took anything silently.
    const wrapper = await settleEmpty(mountGrid());
    const keep = new File(["x"], "one.jpg", { type: "image/jpeg" });
    const drop = new File(["y"], "notes.docx", { type: "application/msword" });

    await emptyState(wrapper).vm.$emit("add-files", [keep, drop]);

    expect(wrapper.emitted("local-import")[0][0]).toEqual({ files: [keep] });
  });

  it("takes a FileList as readily as an array", async () => {
    // The card emits a real Array, but `importChosenFiles` is the obvious place
    // to hand the `FileList` off an <input>, which has no `.filter`. Taking
    // both means a future caller gets an import rather than a TypeError thrown
    // a long way from the mistake.
    const wrapper = await settleEmpty(mountGrid());
    const keep = new File(["x"], "one.jpg", { type: "image/jpeg" });
    // jsdom will not construct a FileList, so this is its array-like shape:
    // indexed keys, a length and `item()`, but no Array methods.
    const fileList = {
      0: keep,
      length: 1,
      item: (i) => (i === 0 ? keep : null),
    };

    await emptyState(wrapper).vm.$emit("add-files", fileList);

    expect(wrapper.emitted("local-import")[0][0]).toEqual({ files: [keep] });
  });

  it("takes nothing at all without reading a length off it", async () => {
    const wrapper = await settleEmpty(mountGrid());

    await emptyState(wrapper).vm.$emit("add-files", undefined);

    expect(wrapper.emitted("local-import")).toBeFalsy();
  });

  it("starts no import at all when nothing chosen is readable", async () => {
    const wrapper = await settleEmpty(mountGrid());
    const drop = new File(["y"], "notes.docx", { type: "application/msword" });

    await emptyState(wrapper).vm.$emit("add-files", [drop]);

    expect(wrapper.emitted("local-import")).toBeFalsy();
  });
});

describe("when the library is not known to be empty", () => {
  it("says nothing at all when the count never answered", async () => {
    // The count starts at 0 and its fetch swallows failures, so an unanswered
    // request is indistinguishable from an empty library - except by this flag.
    // Claiming "This library is empty" over a backend that did not reply, with
    // three buttons under it, is the worst version of being wrong.
    apiGet.mockImplementation((url) => {
      if (String(url).includes(SUMMARY_URL)) return Promise.reject(new Error("down"));
      if (String(url).includes("/summary")) {
        return Promise.resolve({ data: { image_count: 0 } });
      }
      return Promise.resolve({ data: { images: [], total: 0 } });
    });

    const wrapper = await settleEmpty(mountGrid());

    expect(emptyState(wrapper).exists()).toBe(false);
    expect(wrapper.vm.showEmptyState).toBe(true);
  });

  it("says nothing to a share recipient", async () => {
    // Every route out leads somewhere a read-only token cannot go, and the zero
    // it was given is the count the summary route refused it - not a fact about
    // anybody's library.
    apiClient.__readOnly.value = true;

    const wrapper = await settleEmpty(mountGrid());

    expect(emptyState(wrapper).exists()).toBe(false);
  });

  it("leaves the scrap heap its own question", async () => {
    const wrapper = mountGrid();
    useSelectionStore().selectedCharacter = "SCRAPHEAP";
    await settleEmpty(wrapper);

    expect(emptyState(wrapper).exists()).toBe(false);
  });
});
