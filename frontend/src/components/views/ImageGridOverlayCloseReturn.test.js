// Closing the lightbox goes back where the picture was opened from.
//
// The workflow library replaces this grid, so its "Made with it" tiles open a
// picture by leaving for the picture-grid route with `?overlay=<id>` — and the
// close used to drop the reader on All Pictures, a view they never asked for.
// The tile now sends `?from=<path>` too and the close honours it.
//
// `utils/overlayRoute.test.js` pins the decision itself. This pins that the
// grid ASKS it, with the right answer for each kind of close: invert either and
// the feature is dead while a helper-only suite stays green. So it mounts the
// real ImageGrid.vue, as ten of its siblings here already do.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";
import { ref } from "vue";
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

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: routeQuery, params: {}, path: "/", name: "grid" }),
  useRouter: () => ({
    push: vi.fn(),
    replace: (...args) => replace(...args),
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
  replace.mockClear();
  for (const key of Object.keys(routeQuery)) delete routeQuery[key];
});

describe("closing the lightbox opened from the workflow library", () => {
  it("returns to the shelf when the reader closes it", () => {
    // `close` is emitted by the X, by Escape and by the backdrop, and carries no
    // payload - so this is the default arm of `closeOverlay`.
    Object.assign(routeQuery, { overlay: "812", from: "/workflows" });
    const wrapper = mountGrid();

    wrapper.vm.closeOverlay();

    expect(replace).toHaveBeenCalledWith({ path: "/workflows", query: {} });
  });

  it("stays on the grid when the grid closes it to show its own result", () => {
    // "Use as input" closes the lightbox because the run panel it opens is the
    // rail the lightbox covers. Leaving for the shelf would open that panel on a
    // screen with no grid and no selection to run against.
    Object.assign(routeQuery, { overlay: "812", from: "/workflows" });
    const wrapper = mountGrid();

    wrapper.vm.useOverlayPictureAsInput(812);

    // Still here - and `?from=` is spent, so the reader's NEXT close does not
    // jump either.
    expect(replace).toHaveBeenCalledWith({ query: {} });
  });

  it("keeps the rest of the query when there is nowhere to go back to", () => {
    // The shipped close, unchanged: no `?from=`, so only `?overlay=` goes.
    Object.assign(routeQuery, { overlay: "812", review: "board" });
    const wrapper = mountGrid();

    wrapper.vm.closeOverlay();

    expect(replace).toHaveBeenCalledWith({ query: { review: "board" } });
  });
});
