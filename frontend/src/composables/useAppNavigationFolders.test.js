// A folder click and what the URL says afterwards.
//
// The sidebar's folder tree emits `{referenceFolderId, pathPrefix, label}` for
// EVERY node - `FolderTreeNode.vue` requires the `rfId` - so a subfolder click
// and its folder's root click differ only in `pathPrefix`. The route builder
// keyed off the id alone, so both pushed `/ref-folder/5` and the subfolder
// vanished from the URL: a reload or a shared link landed one level up, on the
// whole folder, showing pictures the person had just navigated away from.
//
// The id still owns the payload (`useViewStore.applyView` refuses to overwrite
// a folder route's filter from `?path=`, or `reference_folder_id` would drop
// out of the grid query); `?path=` only says which folder INSIDE it is open.
// The sidebar half of the round trip is `SideBarFolderRouteRestore.test.js`.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { defineComponent, h } from "vue";
import { mount } from "@vue/test-utils";

const nav = vi.hoisted(() => ({ route: null, push: null, replace: null }));
vi.mock("vue-router", async () => {
  const { reactive } = await vi.importActual("vue");
  const { vi: vitest } = await import("vitest");
  nav.route = reactive({
    path: "/",
    name: "all-pictures",
    params: {},
    query: {},
  });
  nav.push = vitest.fn();
  nav.replace = vitest.fn();
  return {
    useRoute: () => nav.route,
    useRouter: () => ({
      push: nav.push,
      replace: nav.replace,
      currentRoute: { value: nav.route },
    }),
  };
});

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

const ROOT = "/home/me/library/refs";
const SUB = "/home/me/library/refs/2024/summer";

beforeEach(() => {
  setActivePinia(createPinia());
  nav.route.name = "all-pictures";
  nav.route.path = "/";
  nav.route.query = {};
  nav.push.mockReset().mockReturnValue(Promise.resolve());
  nav.replace.mockReset().mockReturnValue(Promise.resolve());
});

describe("a reference-folder click", () => {
  it("keeps the subfolder in the URL", () => {
    const { wrapper, api } = mountNav();

    api.handleSelectFolder({
      referenceFolderId: 5,
      pathPrefix: SUB,
      label: "summer",
    });

    expect(nav.push).toHaveBeenCalledWith({
      name: "ref-folder",
      params: { id: "5" },
      query: { path: SUB },
    });

    wrapper.unmount();
  });

  it("names the folder root when that is what was clicked", () => {
    const { wrapper, api } = mountNav();

    api.handleSelectFolder({
      referenceFolderId: 5,
      pathPrefix: ROOT,
      label: "refs",
    });

    expect(nav.push).toHaveBeenCalledWith({
      name: "ref-folder",
      params: { id: "5" },
      query: { path: ROOT },
    });

    wrapper.unmount();
  });

  it("pushes no query when the payload carries no path at all", () => {
    // The project menu's folder rows build the payload from the listing, and
    // an import folder's has no `pathPrefix`. An empty query, not `path=`.
    const { wrapper, api } = mountNav();

    api.handleSelectFolder({
      importSourceFolder: "/home/me/inbox",
      importFolderId: 7,
      label: "inbox",
    });

    expect(nav.push).toHaveBeenCalledWith({
      name: "import-folder",
      params: { id: "7" },
      query: {},
    });

    wrapper.unmount();
  });

  it("still sends an id-less folder payload to all-pictures with its path", () => {
    // "About your library" points at folders that are not registered as either
    // kind. Unchanged by the above.
    const { wrapper, api } = mountNav();

    api.handleSelectFolder({ pathPrefix: SUB, label: "summer" });

    expect(nav.push).toHaveBeenCalledWith({
      name: "all-pictures",
      query: { path: SUB },
    });

    wrapper.unmount();
  });
});
