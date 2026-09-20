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
  replace.mockClear();
  for (const key of Object.keys(routeQuery)) delete routeQuery[key];
  // `markEnd` logs every timed interaction in a dev build, and mounting with
  // `?overlay=` opens the lightbox - the reload path - so each mount here emits
  // one. Real behaviour, nothing asserts it, and the same rpc argument applies.
  vi.spyOn(console, "debug").mockImplementation(() => {});
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

  it("ignores whatever the close emit carries", async () => {
    // `@close` is bound as a CALL. Bound bare, the emit's payload would land in
    // `returnToOrigin`: the day someone emits `close(reason)`, a falsy one
    // silently stops the return and a truthy one silently forces it, with
    // nothing red anywhere. Today's three emit sites are argument-free, so this
    // fires one that is not.
    Object.assign(routeQuery, { overlay: "812", from: "/workflows" });
    const wrapper = mountGrid();

    const overlay = wrapper.findComponent({ name: "ImageOverlay" });
    expect(overlay.exists(), "the lightbox is mounted by this grid").toBe(true);
    overlay.vm.$emit("close", false);
    await wrapper.vm.$nextTick();

    expect(replace).toHaveBeenCalledWith({ path: "/workflows", query: {} });
  });

  it("stays on the grid when the grid closes it to show its own result", () => {
    // The Run popup closes the lightbox so the popup is not opened behind it.
    // (This was "use as input" and the rail run panel until #1407.) Leaving for
    // the shelf would be worse here than on the other such paths: the popup is
    // App.vue's, but the progress runner and the view context are this grid's
    // and `onUnmounted` closes the popup with them - so the jump would shut the
    // popup the reader had just opened.
    Object.assign(routeQuery, { overlay: "812", from: "/workflows" });
    const wrapper = mountGrid();

    wrapper.vm.runWorkflowOnPicture(812);

    // Still here - and `?from=` is spent, so the reader's NEXT close does not
    // jump either.
    expect(replace).toHaveBeenCalledWith({ query: {} });
  });

  it("stays put when ?from= names nothing the router has", () => {
    // Hand-edited or stale. The catch-all would redirect it to the home view,
    // so honouring it would close the lightbox onto All Pictures - the landing
    // this whole change exists to prevent.
    Object.assign(routeQuery, { overlay: "812", from: "/nonsense" });
    const wrapper = mountGrid();

    wrapper.vm.closeOverlay();

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
