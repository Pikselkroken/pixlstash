// Assigning pictures to a project must only refetch the grid where project
// membership can actually change what the grid shows.
//
// Reported bug: assigning pictures to a project from **All Pictures** reloaded
// the whole grid. useGridFetch only appends `project_id` to the grid query when
// `projectViewMode === "project"`, so in the global view an assignment changes
// neither which pictures match nor their order, so the refetch was pure churn
// (flicker plus lost scroll position and selection). Reproduced end to end:
// the assignment issued GET /pictures/count + GET /pictures/stream?...&offset=0.
//
// This mounts the REAL ImageGrid.vue and counts the grid queries that reach the
// (mocked) apiClient, which is the same measurement the e2e reproduction makes
// on the wire. Both directions are asserted: no refetch in the global view, and
// a refetch still happens in the project-scoped view, because over-blocking
// would be its own regression (a picture leaving the project view has to go).

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { ref, computed } from "vue";

// One seam for every network call: all `src/api/*` modules go through this
// axios instance, so counting its GETs counts the grid's actual queries.
const apiGet = vi.fn();
const apiPost = vi.fn();
const apiPatch = vi.fn();
const apiPut = vi.fn();
const apiDelete = vi.fn();

vi.mock("../../utils/apiClient", () => {
  const isAuthenticated = ref(true);
  const sessionContext = ref({ scope: "ALL" });
  return {
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

// The `vuetify/components` barrel pulls component CSS that Vitest cannot load
// from node_modules. ImageGrid only needs these to exist as components; a proxy
// hands back a trivial stub for whatever name is imported anywhere in its tree.
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

const ALL_PICTURES_ID = "ALL";

/** Count the grid's own list queries (the signature of a full grid reload). */
function gridQueryCount() {
  return apiGet.mock.calls.filter(([url]) => {
    const u = String(url ?? "");
    return u.includes("/pictures/stream") || u.includes("/pictures/count");
  }).length;
}

function mountGrid(props = {}) {
  return mount(ImageGrid, {
    shallow: true,
    global: {
      // Vuetify's components are registered globally by main.js, which the test
      // does not run; treat the `v-*` tags as custom elements so the template
      // compiles without a wall of resolve warnings.
      config: {
        compilerOptions: { isCustomElement: (tag) => tag.startsWith("v-") },
      },
    },
    props: {
      backendUrl: "/api/v1",
      allPicturesId: ALL_PICTURES_ID,
      unassignedPicturesId: "UNASSIGNED",
      scrapheapPicturesId: "SCRAPHEAP",
      selectedCharacter: ALL_PICTURES_ID,
      selectedSet: null,
      selectedSetIds: [],
      projectViewMode: "global",
      selectedProjectId: null,
      selectedSort: "DATE",
      selectedDescending: true,
      ...props,
    },
  });
}

beforeEach(() => {
  setActivePinia(createPinia());
  apiGet.mockReset();
  apiPost.mockReset();
  apiPatch.mockReset();
  apiPut.mockReset();
  apiDelete.mockReset();
  // Every read returns an empty, well-shaped payload; the grid never needs real
  // data here, only the record of which queries it decided to issue.
  apiGet.mockResolvedValue({ data: { pictures: [], count: 0, total: 0 } });
  apiPatch.mockResolvedValue({ data: { updated: 3 } });
  apiPost.mockResolvedValue({ data: {} });
});

describe("project assignment: refetch only where the view is project-scoped", () => {
  it("does not refetch the grid in the global view (All Pictures)", async () => {
    const wrapper = mountGrid();
    await wrapper.vm.$nextTick();
    apiGet.mockClear();

    await wrapper.vm.handleSetProjectForSelected({
      projectId: 7,
      action: "added",
      pictureIds: [101, 102, 103],
      expandStacks: false,
    });

    // The assignment itself must still be sent...
    expect(apiPatch).toHaveBeenCalledTimes(1);
    expect(String(apiPatch.mock.calls[0][0])).toContain("/pictures/project");
    // ...but nothing about the All Pictures query depends on project
    // membership, so the grid must not re-query itself.
    expect(
      gridQueryCount(),
      `grid re-queried in the global view: ${JSON.stringify(
        apiGet.mock.calls.map(([u]) => u),
      )}`,
    ).toBe(0);

    wrapper.unmount();
  });

  it("still refetches the grid in the project-scoped view", async () => {
    const wrapper = mountGrid({
      projectViewMode: "project",
      selectedProjectId: 7,
    });
    await wrapper.vm.$nextTick();
    apiGet.mockClear();

    await wrapper.vm.handleSetProjectForSelected({
      projectId: 7,
      action: "added",
      pictureIds: [101, 102, 103],
      expandStacks: false,
    });

    expect(apiPatch).toHaveBeenCalledTimes(1);
    // Membership genuinely decides what this view shows, so the pictures that
    // just entered (or left) it have to be reconciled. Over-blocking here would
    // strand a picture in a project view it no longer belongs to.
    expect(gridQueryCount()).toBeGreaterThan(0);

    wrapper.unmount();
  });

  it("refetches in the unassigned-project view, where an add removes pictures", async () => {
    // projectViewMode "project" with no selected project is the
    // `project_id=UNASSIGNED` pseudo-view: assigning a project to a picture
    // takes it out of that view.
    const wrapper = mountGrid({
      projectViewMode: "project",
      selectedProjectId: null,
    });
    await wrapper.vm.$nextTick();
    apiGet.mockClear();

    await wrapper.vm.handleSetProjectForSelected({
      projectId: 7,
      action: "added",
      pictureIds: [101],
      expandStacks: false,
    });

    expect(gridQueryCount()).toBeGreaterThan(0);

    wrapper.unmount();
  });
});
