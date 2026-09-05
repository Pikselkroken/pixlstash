// "About your library": the navigation half.
//
// The screen's whole value is that a finding's button lands on the pictures
// the finding counted. `LibraryInsights` re-emits the server's action object
// and stops there - every decision about WHERE that goes is here, and none of
// it was covered until this file existed.
//
// Two things are pinned:
//
//   * each `kind` in the backend's closed vocabulary
//     (`routes/insights.py::InsightActionModel`) reaches the destination the
//     contract names, carrying the folder path or the face facet that makes
//     the destination the finding's own set rather than a superset;
//   * `/insights` is owner-only, so a READ session neither mounts it nor sits
//     on it - the same #1014 treatment as the model shelf, and the sibling of
//     `useAppNavigationModels.test.js`.
//
// `sessionContext` is the REAL one from `apiClient`, not a hand-set boolean, so
// the scoped and unscoped cases genuinely differ in what feeds the predicate.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { defineComponent, h } from "vue";
import { mount } from "@vue/test-utils";

const nav = vi.hoisted(() => ({ route: null, replace: null, push: null }));
vi.mock("vue-router", async () => {
  const { reactive } = await vi.importActual("vue");
  const { vi: vitest } = await import("vitest");
  nav.route = reactive({
    path: "/",
    name: "all-pictures",
    params: {},
    query: {},
  });
  nav.replace = vitest.fn();
  nav.push = vitest.fn();
  return {
    useRoute: () => nav.route,
    useRouter: () => ({
      push: nav.push,
      replace: nav.replace,
      currentRoute: { value: nav.route },
    }),
  };
});

import { sessionContext } from "../utils/apiClient";
import { useAppNavigation } from "./useAppNavigation";

function mountNav() {
  let api = null;
  const wrapper = mount(
    defineComponent({
      setup() {
        api = useAppNavigation();
        return () => h("div");
      },
    }),
  );
  return { wrapper, api };
}

const SHARE_TOKEN = "example-share-token";
const UNSCOPED_READ = { scope: "READ" };
const SCOPED_READ = {
  scope: "READ",
  resource_type: "picture_set",
  resource_id: 12,
};

const FOLDER = "/home/me/library/_unsorted";

beforeEach(() => {
  setActivePinia(createPinia());
  sessionContext.value = null;
  nav.route.name = "all-pictures";
  nav.route.path = "/";
  nav.route.query = {};
  nav.replace.mockReset().mockReturnValue(Promise.resolve());
  nav.push.mockReset().mockReturnValue(Promise.resolve());
});

describe("a finding's action reaches its destination", () => {
  it("opens the unassigned view narrowed to the pile's folder", () => {
    const { wrapper, api } = mountNav();

    api.handleInsightAction({
      kind: "unassigned_in_folder",
      path: FOLDER,
      folder_label: "_unsorted",
    });

    // `?path=` and not a bare `/character/UNASSIGNED`: the finding counted one
    // folder's unassigned pictures, and the whole library's is a different,
    // much larger number under a header naming that folder.
    expect(nav.push).toHaveBeenCalledWith({
      name: "character",
      params: { id: "UNASSIGNED" },
      query: { path: FOLDER },
    });

    wrapper.unmount();
  });

  it("opens the unassigned view with the face facet for unnamed faces", () => {
    const { wrapper, api } = mountNav();

    api.handleInsightAction({ kind: "unassigned_with_face" });

    // Unassigned means no face here is named; `with_face` means there is one.
    // The pair is exactly the set the finding counted - without the facet the
    // destination is every unassigned picture, most with no face at all.
    expect(nav.push).toHaveBeenCalledWith({
      name: "character",
      params: { id: "UNASSIGNED" },
      query: { face: "with_face" },
    });

    wrapper.unmount();
  });

  it("scopes the duplicate queue to the folder the finding names", () => {
    const { wrapper, api } = mountNav();

    api.handleInsightAction({
      kind: "duplicates_in_folder",
      path: "/home/me/library",
      folder_label: "library",
    });

    expect(nav.push).toHaveBeenCalledWith({
      name: "duplicates",
      query: {
        scope: "folder",
        scope_id: "/home/me/library",
        scope_label: "library",
        scope_icon: "mdi-folder-outline",
      },
    });

    wrapper.unmount();
  });

  it("opens the whole queue when there is no folder to scope to", () => {
    // Two trees on different roots: their common ancestor is `/`, which would
    // be a whole-vault scan wearing a folder's name. The backend sends the
    // unscoped kind instead, and it must still open something.
    const { wrapper, api } = mountNav();

    api.handleInsightAction({ kind: "duplicates" });

    expect(nav.push).toHaveBeenCalledWith({ name: "duplicates", query: {} });

    wrapper.unmount();
  });

  it("navigates nowhere for a kind it does not own", () => {
    // `settings` is a dialog, not a route; App.vue takes that one straight to
    // `openSettingsDialog`. A silent fall-through to some default destination
    // would be worse than doing nothing.
    const { wrapper, api } = mountNav();

    api.handleInsightAction({ kind: "settings", tab: "behaviour" });
    api.handleInsightAction(null);

    expect(nav.push).not.toHaveBeenCalled();

    wrapper.unmount();
  });
});

describe("About your library and a READ session", () => {
  for (const [label, ctx] of [
    ["an unscoped READ credential", UNSCOPED_READ],
    ["a scoped READ credential", SCOPED_READ],
  ]) {
    it(`does not show the screen to ${label} that lands on /insights`, () => {
      sessionContext.value = ctx;
      nav.route.name = "insights";
      nav.route.query = { token: SHARE_TOKEN };
      const { wrapper, api } = mountNav();

      // False, so App.vue never mounts it and the owner-only GET is never sent.
      expect(api.isInsightsView.value).toBe(false);
      // …and the visitor is moved off it, credential intact: the bounce is the
      // only navigation that fires exclusively for a share session, so a
      // dropped `?token=` here breaks the link on the next reload.
      expect(nav.replace).toHaveBeenCalledWith({
        name: "all-pictures",
        query: { token: SHARE_TOKEN },
      });

      wrapper.unmount();
    });
  }

  it("shows it to the owner", () => {
    sessionContext.value = null;
    nav.route.name = "insights";
    const { wrapper, api } = mountNav();

    // Over-blocking the owner would be its own regression.
    expect(api.isInsightsView.value).toBe(true);
    expect(nav.replace).not.toHaveBeenCalled();

    wrapper.unmount();
  });

  it("bounces a READ session that navigates to /insights after mount", async () => {
    sessionContext.value = UNSCOPED_READ;
    nav.route.query = { token: SHARE_TOKEN };
    const { wrapper, api } = mountNav();
    expect(nav.replace).not.toHaveBeenCalled();

    nav.route.name = "insights";
    await wrapper.vm.$nextTick();

    expect(api.isInsightsView.value).toBe(false);
    expect(nav.replace).toHaveBeenCalled();

    wrapper.unmount();
  });
});
