// Issue #717 - a share link minted while the owner browsed a project.
//
// `AccountSection.shareUrl` builds the link from `window.location.pathname`
// (Settings is a dialog, not a route), so a token minted from `/project/5`
// hands the recipient `/project/5?token=…`. The recipient's route then puts the
// app in project mode, `useGridFetch` appends `project_id=5`, and the AuthzGate
// refuses it: a character / picture-set / picture token has an empty visible
// project set.
//
// These tests exercise the real code on both halves of the chain:
//
//   * the ORDERING App.vue depends on - `useViewStore.startRouteSync` is
//     installed at setup with `immediate: true` (App.vue:322), so the URL's
//     project scope is already written by the time the scoped-session block in
//     `onMounted` (App.vue:384) runs. Proven below with the real store and the
//     real Vue lifecycle, not asserted from reading the file;
//   * the CONSEQUENCE - the real `useGridFetch` builds the real query string,
//     so `project_id` is asserted on the wire rather than inferred from
//     `projectViewMode`.
//
// What these tests do NOT prove: they do not mount `App.vue` (≈2 580 lines of
// shell, WebSocket and Vuetify), so the scoped-session block's own selection
// writes are represented by their VALUES (`selectedCharacter = 9`), not by
// running that block. The ordering test above is what makes that substitution
// safe: whatever that block writes, it writes it after the route has landed.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { defineComponent, h, onMounted, reactive, ref, watch } from "vue";
import { mount } from "@vue/test-utils";

import { useViewStore } from "./useViewStore";
import { useProjectStore } from "./useProjectStore";
import { useSelectionStore } from "./useSelectionStore";
import { sessionContext } from "../utils/apiClient";
import { useGridFetch } from "../composables/useGridFetch";
import { getPictureCount, streamPictures } from "../api/pictures";

vi.mock("../api/pictures", () => ({
  getPictureCount: vi.fn(),
  streamPictures: vi.fn(),
  getLikenessGroups: vi.fn(),
  faceSearch: vi.fn(),
  likenessSearch: vi.fn(),
  searchPictures: vi.fn(),
  listPicturesByIds: vi.fn(),
}));

/** A route location of the shape `useViewStore` parses. */
function routeOf(name, params = {}, query = {}) {
  return reactive({ name, params, query });
}

/** Put the transport in the state `GET /session/context` would have left it. */
function scopedSession(resourceType, resourceId) {
  sessionContext.value = {
    is_owner: false,
    scope: "READ",
    resource_type: resourceType,
    resource_id: resourceId,
  };
}

/**
 * Install the app's one route watcher exactly as App.vue does: at setup, with
 * `immediate: true`, inside a mounted component's effect scope.
 *
 * @param {object} route reactive route location
 * @param {Function} [atMounted] runs in `onMounted`, i.e. where App.vue's
 *   scoped-session normalisation runs
 */
function mountWithRouteSync(route, atMounted) {
  const Probe = defineComponent({
    setup() {
      useViewStore().startRouteSync(route, { watch });
      onMounted(() => atMounted?.());
      return () => h("div");
    },
  });
  return mount(Probe);
}

/**
 * Minimal `useGridFetch` harness - same shape as the one in
 * `composables/useGridFetch.test.js`. Selection, filter and project facets come
 * from the live Pinia stores, which is the point: this builds the REAL query.
 */
function makeGrid({ primarySelectedSetId = null } = {}) {
  const refs = {
    allGridImages: ref([]),
    lastFetchedGridImages: ref([]),
    scrollWrapper: ref(null),
    preserveScrollOnNextFetch: ref(false),
    pendingScrollTop: ref(null),
    overlayOpen: ref(false),
    pendingGridImages: ref(null),
    pendingOverlayGridRefresh: ref(false),
    visibleStart: ref(0),
    visibleEnd: ref(0),
    divisibleViewWindow: ref(40),
    initialRender: ref(false),
    rowHeight: ref(128),
    sharedPictureIds: ref(new Set()),
    guestConsentState: ref(null),
    guestSessionId: ref(null),
    highlightNextFetch: ref(false),
    hasLoadedOnce: ref(false),
    previousImageIds: new Set(),
    normalizedSelectedCharacterIds: ref([]),
    normalizedSelectedSetIds: ref([]),
    hasSetSelection: ref(primarySelectedSetId != null),
    isSetOverlapView: ref(false),
    isMultiCharacterView: ref(false),
    primarySelectedSetId: ref(primarySelectedSetId),
    smartScoreProgress: reactive({ visible: false, percent: 0, message: "" }),
    exportProgress: reactive({ visible: false, percent: 0, message: "" }),
    reverseImageSearchPictureIds: ref([]),
    faceLikenessSearchFaceId: ref(null),
  };

  const callbacks = {
    collapseStackImages: (x) => x,
    mapGridImages: (x) => x,
    syncExpandAllStacksFromFetchedImages: vi.fn(),
    refreshExpandedStacksAfterFetch: vi.fn(),
    resetThumbnailState: vi.fn(),
    triggerNewImageHighlight: vi.fn(),
    updateVisibleThumbnails: vi.fn(),
    fetchThumbnailsBatch: vi.fn(),
    maybeRefreshOverlayForComfyui: vi.fn(),
    startSmartScoreProgress: vi.fn(),
    completeSmartScoreProgress: vi.fn(),
    onGridFetchStart: vi.fn(),
    onGridVisibleMetadataReady: vi.fn(),
    onGridFetchDone: vi.fn(),
  };

  return useGridFetch(refs, reactive({ backendUrl: "http://test" }), callbacks);
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.clearAllMocks();
  sessionContext.value = null;
});

afterEach(() => {
  sessionContext.value = null;
});

/**
 * Drive one fetch and return the query string it built.
 * @param {object} [options] forwarded to {@link makeGrid}
 */
async function queryForCurrentState(options) {
  getPictureCount.mockResolvedValue({ count: 0 });
  streamPictures.mockResolvedValue({ pictures: [], done: true });
  const grid = makeGrid(options);
  await grid.fetchAllGridImages({ force: true });
  return getPictureCount.mock.calls[0][0];
}

describe("the ordering issue #717 depends on", () => {
  it("writes the URL's project scope before onMounted runs", () => {
    // App.vue installs the route watcher at setup with `immediate: true`
    // (App.vue:322) and normalises the scoped session in `onMounted`
    // (App.vue:384). If that order were reversed there would be no bug: the
    // mount-time block would have the last word and the URL could not impose a
    // project the token cannot see.
    //
    // Measured on an owner session on purpose. The ordering is a property of
    // the lifecycle, not of the credential, and pinning it against a scoped
    // session would make this test re-state the fix instead of the premise.
    let modeAtMounted = null;
    let projectIdAtMounted = null;

    const wrapper = mountWithRouteSync(routeOf("project", { id: "5" }), () => {
      const projectStore = useProjectStore();
      modeAtMounted = projectStore.projectViewMode;
      projectIdAtMounted = projectStore.selectedProjectId;
    });

    expect(modeAtMounted).toBe("project");
    expect(projectIdAtMounted).toBe(5);
    wrapper.unmount();
  });
});

describe("a scoped share session landing on a project URL", () => {
  it("does not ask the server for a project a character token cannot see", async () => {
    scopedSession("character", 9);
    const wrapper = mountWithRouteSync(routeOf("project", { id: "5" }), () => {
      // App.vue:390 - the scoped-session block selects the shared character.
      useSelectionStore().selectedCharacter = 9;
    });

    const query = await queryForCurrentState();

    expect(query).toContain("character_id=9");
    expect(query).not.toContain("project_id");
    wrapper.unmount();
  });

  it("does not ask the server for a project a picture-set token cannot see", async () => {
    scopedSession("picture_set", 3);
    const wrapper = mountWithRouteSync(routeOf("project", { id: "5" }), () => {
      // App.vue:387 - the scoped-session block selects the shared set.
      useSelectionStore().selectedSet = 3;
    });

    const query = await queryForCurrentState({ primarySelectedSetId: 3 });

    expect(query).toContain("set_id=3");
    expect(query).not.toContain("project_id");
    wrapper.unmount();
  });

  it("drops the URL's project scope for a nested project-character link too", async () => {
    // `/project/5/character/9` is the other pathname the share builder can
    // inherit, and it reaches project mode by a different route branch.
    scopedSession("character", 9);
    const wrapper = mountWithRouteSync(
      routeOf("project-character", { projectId: "5", id: "9" }),
    );

    const query = await queryForCurrentState();

    expect(query).toContain("character_id=9");
    expect(query).not.toContain("project_id");
    wrapper.unmount();
  });
});

describe("the sessions that must keep their project scope", () => {
  it("still applies its own project for a project-scoped token", async () => {
    scopedSession("project", 5);
    const wrapper = mountWithRouteSync(routeOf("project", { id: "5" }));

    const projectStore = useProjectStore();
    expect(projectStore.projectViewMode).toBe("project");
    expect(projectStore.selectedProjectId).toBe(5);
    expect(await queryForCurrentState()).toContain("project_id=5");
    wrapper.unmount();
  });

  it("clamps a project-scoped token to its own project, not the URL's", async () => {
    // The owner can mint a token for project 5 while standing on project 99;
    // the link then carries `/project/99`.
    scopedSession("project", 5);
    const wrapper = mountWithRouteSync(routeOf("project", { id: "99" }));

    expect(await queryForCurrentState()).toContain("project_id=5");
    wrapper.unmount();
  });

  it("leaves an owner session's project route completely alone", async () => {
    sessionContext.value = { is_owner: true, scope: "ALL" };
    const wrapper = mountWithRouteSync(routeOf("project", { id: "5" }));

    const projectStore = useProjectStore();
    expect(projectStore.projectViewMode).toBe("project");
    expect(projectStore.selectedProjectId).toBe(5);
    expect(await queryForCurrentState()).toContain("project_id=5");
    wrapper.unmount();
  });

  it("leaves a whole-library READ share alone", async () => {
    // A READ token with no resource scope sees every project
    // (`visible_project_ids` returns None for it), so forcing global mode on
    // `scope !== "ALL"` would be over-blocking.
    sessionContext.value = {
      is_owner: false,
      scope: "READ",
      resource_type: null,
      resource_id: null,
    };
    const wrapper = mountWithRouteSync(routeOf("project", { id: "5" }));

    expect(await queryForCurrentState()).toContain("project_id=5");
    wrapper.unmount();
  });

  it("leaves a global route alone for every session", async () => {
    scopedSession("character", 9);
    const wrapper = mountWithRouteSync(routeOf("all-pictures"));

    const projectStore = useProjectStore();
    expect(projectStore.projectViewMode).toBe("global");
    expect(projectStore.selectedProjectId).toBe(null);
    wrapper.unmount();
  });
});
