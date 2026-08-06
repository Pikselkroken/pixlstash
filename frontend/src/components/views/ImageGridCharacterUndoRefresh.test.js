// Successful character-assignment undo/redo must re-read a character grid.
// The backend emits pictures_changed for the restored face metadata, but the
// realtime layer intentionally suppresses this tab's own echo. Without the
// explicit post-history reconciliation in ImageGrid, the changed assignment
// stays rendered until the user manually refreshes.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { computed, ref } from "vue";
import { createPinia, setActivePinia } from "pinia";
import { useOperationStore } from "../../stores/useOperationStore.js";
import { useSelectionStore } from "../../stores/useSelectionStore.js";

const apiGet = vi.fn();
const apiPost = vi.fn();
const apiPatch = vi.fn();
const apiPut = vi.fn();
const apiDelete = vi.fn();

vi.mock("../../utils/apiClient", () => {
  const isAuthenticated = ref(true);
  const sessionContext = ref({ scope: "ALL" });
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
    isReadOnly: computed(() => false),
    login: vi.fn(),
    logout: vi.fn(),
    sessionContext,
    setRequestClientId: vi.fn(),
    API_BASE_URL: "/api/v1",
  };
});

vi.mock("vuetify/components", () => {
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
});

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

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: {}, params: {}, path: "/", name: "grid" }),
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    currentRoute: ref({ query: {} }),
  }),
}));

import ImageGrid from "./ImageGrid.vue";

const ASSIGNMENT = {
  id: 17,
  batch_id: null,
  op_type: "characters.assign",
  target_ids: [101],
  target_count: 1,
  undoable: true,
  status: "applied",
  summary: "Assigned pictures to a character",
};

let characterHasPicture;

function isGridQuery(url) {
  const value = String(url ?? "");
  return value.includes("/pictures/count") || value.includes("/pictures/stream");
}

function gridQueryCount() {
  return apiGet.mock.calls.filter(([url]) => isGridQuery(url)).length;
}

function mountGrid(selectedCharacter) {
  const selectionStore = useSelectionStore();
  selectionStore.selectedCharacter = selectedCharacter;
  selectionStore.selectedCharacterIds =
    Number.isFinite(Number(selectedCharacter)) && Number(selectedCharacter) > 0
      ? [Number(selectedCharacter)]
      : [];
  selectionStore.selectedSet = null;
  selectionStore.selectedSetIds = [];

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

function primeUndo(op = ASSIGNMENT) {
  const store = useOperationStore();
  store.operations = [op];
  store.canUndo = true;
  store.nextUndo = op;
  return store;
}

function primeRedo(op = ASSIGNMENT) {
  const undone = { ...op, status: "undone" };
  const store = useOperationStore();
  store.operations = [undone];
  store.canRedo = true;
  store.nextRedo = undone;
  return store;
}

beforeEach(() => {
  setActivePinia(createPinia());
  characterHasPicture = true;
  apiGet.mockReset();
  apiPost.mockReset();
  apiPatch.mockReset();
  apiPut.mockReset();
  apiDelete.mockReset();

  apiGet.mockImplementation(async (url) => {
    const value = String(url ?? "");
    if (value.includes("/operations/undo-state")) {
      return {
        data: {
          can_undo: false,
          can_redo: true,
          next_undo: null,
          next_redo: { ...ASSIGNMENT, status: "undone" },
        },
      };
    }
    if (value.includes("/operations")) {
      return { data: [{ ...ASSIGNMENT, status: "undone" }] };
    }
    if (value.includes("/pictures/count")) {
      return { data: { count: characterHasPicture ? 1 : 0 } };
    }
    if (value.includes("/pictures/stream")) {
      return {
        data: {
          pictures: characterHasPicture
            ? [
                {
                  id: 101,
                  name: "assigned.jpg",
                  width: 100,
                  height: 100,
                },
              ]
            : [],
        },
      };
    }
    if (value.includes("/characters/42")) {
      return { data: { id: 42, name: "Ada", image_count: 1 } };
    }
    return { data: {} };
  });
  apiPost.mockImplementation(async (url) => {
    if (String(url ?? "").includes("/operations/undo")) {
      characterHasPicture = false;
      return {
        data: {
          operations: [{ ...ASSIGNMENT, status: "undone" }],
          picture_ids: [101],
          picture_count: 1,
        },
      };
    }
    if (String(url ?? "").includes("/operations/redo")) {
      characterHasPicture = true;
      return {
        data: {
          operations: [{ ...ASSIGNMENT, status: "applied" }],
          picture_ids: [101],
          picture_count: 1,
        },
      };
    }
    return { data: {} };
  });
});

describe("character assignment undo grid reconciliation", () => {
  it("refetches the active character after the undo succeeds", async () => {
    const store = primeUndo();
    const wrapper = mountGrid(42);
    await flushPromises();
    apiGet.mockClear();

    await store.undo();
    await flushPromises();

    expect(gridQueryCount()).toBeGreaterThan(0);
    expect(wrapper.vm.allGridImages).toEqual([]);
    // The shared store still owns and exposes the normal undone receipt.
    expect(store.receipt).toMatchObject({ mode: "undone", operationId: 17 });

    wrapper.unmount();
  });

  it("does not refetch All Pictures for the same undo", async () => {
    const store = primeUndo();
    const wrapper = mountGrid("ALL");
    await flushPromises();
    apiGet.mockClear();

    await store.undo();
    await flushPromises();

    expect(gridQueryCount()).toBe(0);
    wrapper.unmount();
  });

  it("does not refetch a character view for an unrelated undo", async () => {
    const unrelated = { ...ASSIGNMENT, id: 18, op_type: "pictures.tags.add" };
    const store = primeUndo(unrelated);
    const wrapper = mountGrid(42);
    await flushPromises();
    apiGet.mockClear();

    await store.undo();
    await flushPromises();

    expect(gridQueryCount()).toBe(0);
    wrapper.unmount();
  });
});

describe("character assignment redo grid reconciliation", () => {
  it("refetches the active character after redoing an assignment", async () => {
    characterHasPicture = false;
    const store = primeRedo();
    const wrapper = mountGrid(42);
    await flushPromises();
    expect(wrapper.vm.allGridImages).toEqual([]);
    apiGet.mockClear();

    await store.redo();
    await flushPromises();

    expect(gridQueryCount()).toBeGreaterThan(0);
    expect(wrapper.vm.allGridImages.map((picture) => picture.id)).toEqual([101]);
    // Redo keeps the shared store's normal replay receipt intact.
    expect(store.receipt).toMatchObject({ mode: "did", operationId: 17 });

    wrapper.unmount();
  });

  it("refetches the active character after redoing an unassignment", async () => {
    const unassignment = {
      ...ASSIGNMENT,
      id: 18,
      op_type: "characters.unassign",
      summary: "Unassigned pictures from a character",
    };
    const store = primeRedo(unassignment);
    apiPost.mockImplementation(async (url) => {
      if (String(url ?? "").includes("/operations/redo")) {
        characterHasPicture = false;
        return {
          data: {
            operations: [{ ...unassignment, status: "applied" }],
            picture_ids: [101],
            picture_count: 1,
          },
        };
      }
      return { data: {} };
    });
    const wrapper = mountGrid(42);
    await flushPromises();
    apiGet.mockClear();

    await store.redo();
    await flushPromises();

    expect(gridQueryCount()).toBeGreaterThan(0);
    expect(wrapper.vm.allGridImages).toEqual([]);

    wrapper.unmount();
  });

  it("does not refetch All Pictures for the same redo", async () => {
    characterHasPicture = false;
    const store = primeRedo();
    const wrapper = mountGrid("ALL");
    await flushPromises();
    apiGet.mockClear();

    await store.redo();
    await flushPromises();

    expect(gridQueryCount()).toBe(0);
    wrapper.unmount();
  });

  it("does not refetch a character view for an unrelated redo", async () => {
    const unrelated = { ...ASSIGNMENT, id: 19, op_type: "pictures.tags.add" };
    const store = primeRedo(unrelated);
    const wrapper = mountGrid(42);
    await flushPromises();
    apiGet.mockClear();

    await store.redo();
    await flushPromises();

    expect(gridQueryCount()).toBe(0);
    wrapper.unmount();
  });

  it("does not refetch when redo fails", async () => {
    characterHasPicture = false;
    const store = primeRedo();
    apiPost.mockRejectedValueOnce(new Error("redo failed"));
    const wrapper = mountGrid(42);
    await flushPromises();
    apiGet.mockClear();

    await store.redo();
    await flushPromises();

    expect(gridQueryCount()).toBe(0);
    expect(wrapper.vm.allGridImages).toEqual([]);
    wrapper.unmount();
  });

  it("defers the redo refetch while the overlay is open", async () => {
    characterHasPicture = false;
    const store = primeRedo();
    const wrapper = mountGrid(42);
    await flushPromises();
    wrapper.vm.$.setupState.overlayOpen = true;
    apiGet.mockClear();

    await store.redo();
    await flushPromises();

    expect(gridQueryCount()).toBe(0);
    expect(wrapper.vm.$.setupState.pendingOverlayGridRefresh).toBe(true);
    expect(wrapper.vm.allGridImages).toEqual([]);
    wrapper.unmount();
  });
});
