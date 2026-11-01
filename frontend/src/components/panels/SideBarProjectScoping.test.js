// The sidebar's project surfaces under a session that was granted no project
// scope, and the round-trip it no longer makes for anybody.
//
// Two independent defects are pinned here, both of the same kind: the sidebar
// reaching for project information it either cannot have or never shows.
//
//   1. The DOCKED (narrow) sidebar rendered the Global / Projects / Folders
//      switcher for every session, while the expanded sidebar already omitted
//      it for a share token (`v-else-if="!scopedResourceType"`). A token scoped
//      to a character, a picture or a set is 403'd by `GET /projects`, so its
//      Projects flyout was an empty box and its Folders flyout owner-only. The
//      two widths must agree.
//
//   2. `fetchSidebarData` issued `GET /projects/UNASSIGNED/summary` on every
//      refresh and wrote the answer to `projectCounts["UNASSIGNED"]`, which no
//      template has ever read. It cost the owner a round-trip per refresh for
//      nothing, and for a scoped token it is a project route that 403s: a
//      warning logged on every refresh for a number nobody could see.
//
// Both directions are asserted throughout: the owner's sidebar must keep every
// project affordance and every real count, including a legitimate zero, because
// over-blocking is its own regression.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { mount, flushPromises } from "@vue/test-utils";
import { ref } from "vue";

// One seam for every network call, as in ImageGridProjectAssignRefresh.test.js:
// all `src/api/*` modules go through this axios instance, so the recorded GETs
// are the requests the sidebar actually decided to issue.
const apiGet = vi.fn();
const apiPost = vi.fn();
const apiPatch = vi.fn();
const apiPut = vi.fn();
const apiDelete = vi.fn();

// `isReadOnly` / `sessionContext` are created INSIDE the factory and imported
// back below. `vi.mock` is hoisted above every top-level declaration in this
// file, and unlike the `apiGet` spies (which the factory only *calls* later)
// these two are read while the factory runs, so a top-level `const` would be in
// its temporal dead zone at that moment.
vi.mock("../../utils/apiClient", async () => {
  const { ref: makeRef } = await import("vue");
  return {
    apiClient: {
      get: (...args) => apiGet(...args),
      post: (...args) => apiPost(...args),
      patch: (...args) => apiPatch(...args),
      put: (...args) => apiPut(...args),
      delete: (...args) => apiDelete(...args),
    },
    onSessionReset: () => () => {},
    activateShareToken: vi.fn(),
    appendShareToken: (url) => url,
    checkLoginStatus: vi.fn(),
    checkSession: vi.fn(),
    isAuthenticated: makeRef(true),
    isReadOnly: makeRef(false),
    sessionContext: makeRef(null),
    login: vi.fn(),
    logout: vi.fn(),
    newOperationBatchId: () => "cli-test",
    operationBatchHeaders: () => undefined,
    setRequestClientId: vi.fn(),
    notifySessionReset: vi.fn(),
    toBackendWebSocketUrl: () => "",
    API_BASE_URL: "/api/v1",
  };
});

// The `vuetify/components` barrel pulls component CSS Vitest cannot load; the
// sidebar only needs these names to exist as components.
vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: {}, params: {}, path: "/", name: "all-pictures" }),
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    currentRoute: ref({ query: {} }),
  }),
}));



import { isReadOnly, sessionContext } from "../../utils/apiClient";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useSelectionStore } from "../../stores/useSelectionStore";
import { useFilterStore } from "../../stores/useFilterStore";
import SideBar from "./SideBar.vue";

const PROJECTS = [
  { id: 3, name: "Book", image_count: 118 },
  // A real zero. It must survive every null-guard on the path to the badge:
  // swallowing it would report "unknown" for a project that is genuinely empty.
  { id: 4, name: "Cards", image_count: 0 },
];
const CHARACTERS = [
  { id: 1, name: "Ada", image_count: 40, project_image_count: 7 },
];
// GET /library/folders: Book holding Ada's folder, which holds the owner's
// own "2024"; Global; Unassigned. Cards has nothing filed, so no folder.
const FOLDERS = [
  { path: "Book", name: "Book", kind: "project", entity_id: 3, project_id: 3, count: 6 },
  { path: "Book/Ada", name: "Ada", kind: "character", entity_id: 1, project_id: 3, count: 5 },
  { path: "Book/Ada/2024", name: "2024", kind: "folder", entity_id: null, project_id: 3, count: 2 },
  { path: "Global", name: "Global", kind: "global", entity_id: null, project_id: null, count: 1 },
  { path: "Unassigned", name: "Unassigned", kind: "unassigned", entity_id: null, project_id: null, count: 4 },
];

/** Route each GET to a plausible, well-shaped body for its endpoint. */
function respond(url) {
  const u = String(url ?? "");
  if (u.includes("/library/folders")) return { data: { folders: FOLDERS } };
  if (u.includes("/projects")) return { data: PROJECTS };
  if (u.includes("/characters")) return { data: CHARACTERS };
  if (u.includes("/picture_sets")) return { data: [] };
  if (u.includes("/summary")) return { data: { image_count: 5 } };
  return { data: [] };
}

/** Every GET the sidebar issued whose path names the projects resource. */
function projectRequests() {
  return apiGet.mock.calls
    .map(([url]) => String(url ?? ""))
    .filter((u) => u.includes("/projects"));
}

function mountSidebar({ docked = false } = {}) {
  const sidebarStore = useSidebarStore();
  sidebarStore.sidebarDocked = docked;
  sidebarStore.sidebarPinned = true;
  sidebarStore.sidebarForcedHidden = false;
  return mount(SideBar, {
    shallow: true,
    props: { backendUrl: "/api/v1" },
    global: {
      config: {
        compilerOptions: { isCustomElement: (tag) => tag.startsWith("v-") },
      },
    },
  });
}

/** Put the session into a share token scoped to one non-project resource. */
function scopeSessionTo(resourceType) {
  isReadOnly.value = true;
  sessionContext.value = { scope: "READ", resource_type: resourceType };
}

/**
 * Drive one full sidebar refresh and wait for its reads to settle.
 *
 * `refreshSidebar` is the Tier-3 entry point App.vue calls (frontend
 * architecture §4 Tier 3) and the only caller of `fetchSidebarData`, so this is
 * the real code path the removed round-trip lived on, not a synthetic one.
 */
async function refreshAndSettle(wrapper) {
  wrapper.vm.refreshSidebar();
  for (let i = 0; i < 5; i += 1) await flushPromises();
}

beforeEach(() => {
  setActivePinia(createPinia());
  // The tree's open branches and its mode are persisted; start each test
  // from a closed Library tree.
  window.localStorage.clear();
  isReadOnly.value = false;
  sessionContext.value = null;
  for (const fn of [apiGet, apiPost, apiPatch, apiPut, apiDelete]) fn.mockReset();
  apiGet.mockImplementation((url) => Promise.resolve(respond(url)));
  apiPost.mockResolvedValue({ data: {} });
  apiPatch.mockResolvedValue({ data: {} });
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the docked rail (#2448)", () => {
  /** The rail's buttons, by their accessible names. */
  function railNames(wrapper) {
    return wrapper
      .findAll(".sidebar-collapsed-item")
      .map((b) => b.attributes("aria-label"));
  }

  it("offers the owner one Library button holding the dropdown, People and Sets, a folder each in Folders", async () => {
    const wrapper = mountSidebar({ docked: true });
    await refreshAndSettle(wrapper);
    expect(railNames(wrapper)).toEqual(
      expect.arrayContaining(["Showing Library. Switch to Folders", "Any project"]),
    );
    expect(railNames(wrapper)).not.toContain("People");
    expect(railNames(wrapper)).not.toContain("Sets");
    const library = wrapper.vm.railNodes.find((n) => n.kind === "library");
    const items = library.items();
    expect(items.map((i) => i.section ?? i.label)).toEqual(
      expect.arrayContaining(["Limit view to", "Any project", "Book", "People", "Ada"]),
    );
    wrapper.vm.setTreeMode("folders");
    await flushPromises();
    // One button per top-level folder: Cards has nothing filed, so none.
    expect(railNames(wrapper)).toEqual(
      expect.arrayContaining(["Book", "Global", "Unassigned"]),
    );
    expect(railNames(wrapper)).not.toContain("Cards");
    const book = wrapper.vm.railNodes.find((n) => n.label === "Book");
    expect(book.items().map((i) => i.label ?? (i.sep && "—"))).toEqual([
      "All of Book",
      "—",
      "Ada",
    ]);
    expect(railNames(wrapper)).not.toContain("Any project");
    wrapper.vm.setTreeMode("library");
    wrapper.unmount();
  });

  it("is omitted for a token scoped to another resource", async () => {
    for (const resourceType of ["character", "picture_set", "picture"]) {
      setActivePinia(createPinia());
      scopeSessionTo(resourceType);
      const wrapper = mountSidebar({ docked: true });
      await flushPromises();
      const names = railNames(wrapper);
      expect(names, `a ${resourceType}-scoped token was offered the dropdown`).not.toContain(
        "Any project",
      );
      expect(names, `a ${resourceType}-scoped token was offered the switch`).not.toContain(
        "Showing Library. Switch to Folders",
      );
      wrapper.unmount();
    }
  });

  it("stays for an unscoped read-only token, which can read every project", async () => {
    isReadOnly.value = true;
    sessionContext.value = { scope: "READ", resource_type: null };
    const wrapper = mountSidebar({ docked: true });
    await refreshAndSettle(wrapper);
    expect(railNames(wrapper)).toContain("Any project");
    wrapper.unmount();
  });

  it("lights Library for a listed person, never for a place or no selection", async () => {
    const wrapper = mountSidebar({ docked: true });
    await refreshAndSettle(wrapper);
    const selection = useSelectionStore();
    const people = wrapper.vm.railNodes.find((n) => n.kind === "library");

    for (const id of [null, "", "ALL", "UNASSIGNED", "SCRAPHEAP"]) {
      selection.selectedCharacter = id;
      expect(wrapper.vm.railNodeActive(people), `lit for ${id}`).toBe(false);
    }
    selection.selectedCharacter = 1;
    expect(wrapper.vm.railNodeActive(people)).toBe(true);
    wrapper.unmount();
  });

  it("chooses a project from the dropdown's flyout", async () => {
    const wrapper = mountSidebar({ docked: true });
    await refreshAndSettle(wrapper);
    const scope = wrapper
      .findAll(".sidebar-collapsed-item")
      .find((b) => b.attributes("aria-label") === "Any project");
    await scope.trigger("click");
    const book = wrapper.vm.railFlyout.items.find((i) => i.label === "Book");
    wrapper.vm.pickRailEntry(book.run);
    const [payload] = wrapper.emitted("select-character").at(-1);
    expect(payload.id).toBe("ALL");
    expect(payload.projectContext).toEqual({ mode: "project", projectId: 3 });
    expect(wrapper.vm.railFlyout).toBeNull();
    // With a project in view, its flyout leads with all of it.
    wrapper.vm.chooseScope(3);
    await flushPromises();
    const library = wrapper.vm.railNodes.find((n) => n.kind === "library");
    expect(library.items()[0].label).toBe("All of Book");
    wrapper.unmount();
  });
});

describe("the sidebar's project reads", () => {
  it("still reads the project list, which is where the counts come from", async () => {
    // The load-bearing half of the pair below: this asserts the refresh really
    // does reach the projects resource, so "no UNASSIGNED summary" cannot pass
    // by the sidebar simply never having fetched anything.
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);

    expect(
      projectRequests().some((u) => !u.includes("/summary")),
      `no plain project-list read among: ${JSON.stringify(projectRequests())}`,
    ).toBe(true);

    wrapper.unmount();
  });

  it("asks for the not-in-a-project count once, for the tree", async () => {
    // Not in a project (Library) and Global (Folders) both show it; it lands
    // in its own ref, not in the per-project map.
    apiGet.mockImplementation((url) =>
      Promise.resolve(
        String(url).includes("/projects/UNASSIGNED/summary")
          ? { data: { image_count: 9 } }
          : respond(url),
      ),
    );
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);

    const unassignedSummaries = projectRequests().filter((u) =>
      u.includes("UNASSIGNED"),
    );
    expect(unassignedSummaries).toHaveLength(1);
    expect(wrapper.vm.notInProjectCount).toBe(9);

    wrapper.unmount();
  });

  it("renders each project's own count, including a real zero", async () => {
    // The owner's numbers must be untouched by any of this. A `0` is an answer
    // ("this project is empty"), not a missing one, and a guard that swallowed
    // it would silently blank the badge on an empty project.
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);

    expect(wrapper.vm.projectCounts).toMatchObject({ 3: 118, 4: 0 });
    // And nothing writes the bucket the removed request used to fill.
    expect(Object.keys(wrapper.vm.projectCounts)).not.toContain("UNASSIGNED");

    wrapper.unmount();
  });

  it("makes no project request at all for a character-scoped token", async () => {
    scopeSessionTo("character");
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);

    expect(
      projectRequests(),
      "a character-scoped token would be 403'd by every one of these",
    ).toEqual([]);

    wrapper.unmount();
  });
});

describe("the project dropdown under the switch (#2159, #2448)", () => {
  it("shows in Library mode only, with each project's count", async () => {
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);
    expect(wrapper.findComponent({ name: "SidebarScopeSlot" }).exists()).toBe(true);
    expect(wrapper.vm.scopeCounts.byId).toMatchObject({ 3: 118, 4: 0 });

    wrapper.vm.setTreeMode("folders");
    await flushPromises();
    expect(wrapper.findComponent({ name: "SidebarScopeSlot" }).exists()).toBe(false);
    wrapper.vm.setTreeMode("library");
    wrapper.unmount();
  });

  it("navigates to All media in the project chosen", async () => {
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);

    wrapper.vm.chooseScope(3);
    const [payload] = wrapper.emitted("select-character").at(-1);
    expect(payload.id).toBe("ALL");
    expect(payload.projectContext).toEqual({ mode: "project", projectId: 3 });

    wrapper.unmount();
  });

  it("scopes to the pictures in no project, and All media back to any", async () => {
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);

    wrapper.vm.chooseScope("none");
    expect(wrapper.emitted("select-character").at(-1)[0].projectContext).toEqual(
      { mode: "project", projectId: null },
    );

    wrapper.vm.selectAllMedia(false);
    expect(wrapper.emitted("select-character").at(-1)[0].projectContext).toEqual(
      { mode: "global", projectId: null },
    );

    wrapper.unmount();
  });

  it("narrows People to the scope and opens a person inside it", async () => {
    // Ada is in no project: listed under Any project and Not in a project,
    // not in project 4, and seen from a scope she opens inside it.
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);
    expect(wrapper.vm.libraryBranches[0].characters.map((c) => c.id)).toEqual([1]);

    wrapper.vm.chooseScope(4);
    await flushPromises();
    expect(wrapper.vm.libraryBranches[0].characters).toEqual([]);

    wrapper.vm.selectCharacter(1, "Ada");
    expect(wrapper.emitted("select-character").at(-1)[0].projectContext).toEqual(
      { mode: "project", projectId: 4 },
    );

    wrapper.unmount();
  });

  it("reads the favourites count library-wide, whatever the scope", async () => {
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);
    wrapper.vm.chooseScope(3);
    await refreshAndSettle(wrapper);

    const counts = apiGet.mock.calls
      .map(([url]) => String(url))
      .filter((u) => u.startsWith("/pictures/count"));
    expect(counts.at(-1)).toBe("/pictures/count?favourite=1");

    wrapper.unmount();
  });
});

describe("the Folders tree (#2448, #2458)", () => {
  it("draws the folders pictures are filed in, and remembers the mode", async () => {
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);
    wrapper.vm.setTreeMode("folders");
    await flushPromises();
    wrapper.vm.expandTreeKeys(["f:Book", "f:Book/Ada"]);
    await flushPromises();

    const rows = wrapper.findAll(".sidebar-folder-row");
    expect(rows.map((row) => row.attributes("data-testid"))).toEqual([
      "sidebar-folder-project",
      "sidebar-folder-character",
      "sidebar-folder-folder",
      "sidebar-folder-global",
      "sidebar-folder-unassigned",
    ]);
    // The count is what is filed there, not the person's total (7 in Book).
    expect(rows[1].find(".sidebar-list-count").text()).toBe("5");
    expect(
      Object.entries(window.localStorage).some(
        ([key, value]) =>
          key.startsWith("pixlstash:sidebar:treeMode") && value === "folders",
      ),
    ).toBe(true);
    wrapper.vm.setTreeMode("library");
    wrapper.unmount();
  });

  it("opens a person inside the project it is filed under, and Unassigned in none", async () => {
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);
    wrapper.vm.setTreeMode("folders");
    wrapper.vm.expandTreeKeys(["f:Book"]);
    await flushPromises();

    await wrapper.find('[data-testid="sidebar-folder-character"]').trigger("click");
    let [payload] = wrapper.emitted("select-character").at(-1);
    expect(payload.id).toBe(1);
    expect(payload.projectContext).toEqual({ mode: "project", projectId: 3 });

    await wrapper.find('[data-testid="sidebar-folder-unassigned"]').trigger("click");
    [payload] = wrapper.emitted("select-character").at(-1);
    expect(payload.id).toBe("UNASSIGNED");
    expect(payload.projectContext).toEqual({ mode: "project", projectId: null });
    wrapper.vm.setTreeMode("library");
    wrapper.unmount();
  });

  it("says so when nothing is filed in a folder", async () => {
    apiGet.mockImplementation((url) =>
      Promise.resolve(
        String(url).includes("/library/folders")
          ? { data: { folders: [] } }
          : respond(url),
      ),
    );
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);
    wrapper.vm.setTreeMode("folders");
    await flushPromises();
    expect(wrapper.find('[data-testid="sidebar-folders-empty"]').exists()).toBe(true);
    wrapper.vm.setTreeMode("library");
    wrapper.unmount();
  });

  it("says so when the folders cannot be read, rather than drawing nothing", async () => {
    apiGet.mockImplementation((url) =>
      String(url).includes("/library/folders")
        ? Promise.reject(Object.assign(new Error("boom"), { response: { status: 500 } }))
        : Promise.resolve(respond(url)),
    );
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);
    wrapper.vm.setTreeMode("folders");
    await flushPromises();
    expect(wrapper.find('[data-testid="sidebar-folders-failed"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="sidebar-folders-empty"]').exists()).toBe(false);

    // The next read that works takes the line away.
    apiGet.mockImplementation((url) => Promise.resolve(respond(url)));
    await refreshAndSettle(wrapper);
    expect(wrapper.find('[data-testid="sidebar-folders-failed"]').exists()).toBe(false);
    expect(wrapper.findAll(".sidebar-folder-row").length).toBeGreaterThan(0);
    wrapper.vm.setTreeMode("library");
    wrapper.unmount();
  });

  it("is not offered to a read-only session, which cannot read it", async () => {
    // Unscoped READ: it can read every project, but not the owner's disk.
    isReadOnly.value = true;
    sessionContext.value = { scope: "READ", resource_type: null };
    window.localStorage.setItem("pixlstash:sidebar:treeMode:", "folders");
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);
    expect(wrapper.find(".sidebar-tree-switch").exists()).toBe(false);
    expect(wrapper.find(".sidebar-folder-row").exists()).toBe(false);
    expect(
      apiGet.mock.calls.some(([url]) => String(url).includes("/library/folders")),
    ).toBe(false);
    wrapper.unmount();
  });
});

describe("Open in file manager on a Folders row (#2448, #2458)", () => {
  it("names the folder by the path the tree was given", async () => {
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);
    wrapper.vm.setTreeMode("folders");
    wrapper.vm.expandTreeKeys(["f:Book", "f:Book/Ada"]);
    await flushPromises();

    const own = wrapper.vm.folderRows.find((r) => r.key === "f:Book/Ada/2024");
    wrapper.vm.openFolderRowMenu(own, { clientX: 1, clientY: 1 });
    expect(wrapper.vm.sidebarCtxFolderRow.key).toBe("f:Book/Ada/2024");
    await wrapper.vm.openFolderInFileManager(own);
    expect(apiPost).toHaveBeenLastCalledWith("/library/folders/open", {
      path: "Book/Ada/2024",
    });
    wrapper.vm.setTreeMode("library");
    wrapper.unmount();
  });
});

describe("Find in library (#2448)", () => {
  it("keeps only the names that match, and Enter opens the first", async () => {
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);
    await wrapper.find('[data-testid="sidebar-find"]').setValue("ad");

    expect(wrapper.vm.libraryBranches[0].characters.map((c) => c.name)).toEqual([
      "Ada",
    ]);
    expect(wrapper.find(".sidebar-find-line").text()).toContain("1 match");

    await wrapper
      .find('[data-testid="sidebar-find"]')
      .trigger("keydown", { key: "Enter" });
    const [payload] = wrapper.emitted("select-character").at(-1);
    expect(payload.id).toBe(1);
    expect(payload.projectContext).toEqual({ mode: "global", projectId: null });

    await wrapper
      .find('[data-testid="sidebar-find"]')
      .trigger("keydown", { key: "Escape" });
    expect(wrapper.vm.findQuery).toBe("");
    wrapper.unmount();
  });

  it("underlines a Folders project that matches by name", async () => {
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);
    wrapper.vm.setTreeMode("folders");
    await flushPromises();
    await wrapper.find('[data-testid="sidebar-find"]').setValue("boo");

    const rows = wrapper.findAll('[data-testid="sidebar-folder-project"]');
    expect(rows).toHaveLength(1);
    expect(rows[0].find(".sidebar-find-mark").text()).toBe("Boo");
    expect(rows[0].classes()).not.toContain("sidebar-row--dim");
    wrapper.vm.setTreeMode("library");
    wrapper.unmount();
  });

  it("hands words that match no name to the picture search", async () => {
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);
    await wrapper.find('[data-testid="sidebar-find"]').setValue("harbour");
    expect(wrapper.find(".sidebar-find-none").text()).toContain("harbour");

    wrapper.vm.searchPicturesForFind();
    expect(wrapper.vm.searchStore.searchQuery).toBe("harbour");
    expect(wrapper.vm.findQuery).toBe("");
    wrapper.unmount();
  });
});

describe("a project's move menus", () => {
  it("close on Escape and hand the keyboard back to their +", () => {
    const wrapper = mountSidebar();
    const trigger = document.createElement("button");
    document.body.appendChild(trigger);

    wrapper.vm.openSetMoveMenu({ currentTarget: trigger });
    expect(wrapper.vm.setMoveMenuOpen).toBe(true);
    const event = new KeyboardEvent("keydown", { key: "Escape" });
    wrapper.vm.onMoveMenuKeydown(event);

    expect(wrapper.vm.setMoveMenuOpen).toBe(false);
    expect(document.activeElement).toBe(trigger);

    trigger.remove();
    wrapper.unmount();
  });

  it("open one at a time, so focus goes to the one just opened", () => {
    // Both are `.sidebar-move-menu`; left open together, `_focusMoveMenu`
    // focuses whichever teleported first rather than the one just opened.
    const wrapper = mountSidebar();

    wrapper.vm.openCharacterMoveMenu({});
    wrapper.vm.openSetMoveMenu({});
    expect(wrapper.vm.characterMoveMenuOpen).toBe(false);
    expect(wrapper.vm.setMoveMenuOpen).toBe(true);

    wrapper.vm.openCharacterMoveMenu({});
    expect(wrapper.vm.setMoveMenuOpen).toBe(false);
    expect(wrapper.vm.characterMoveMenuOpen).toBe(true);

    wrapper.unmount();
  });
});

describe("the All of <project> row under the dropdown", () => {
  const allOfRow = (wrapper) => wrapper.find('[data-testid="sidebar-all-of-scope"]');

  /** A picture drag from the grid, as the drop sees it. */
  function pictureDrop(imageIds) {
    const payload = JSON.stringify({ type: "image-ids", imageIds });
    return {
      preventDefault: () => {},
      dataTransfer: {
        types: ["application/json", "application/x-pixlstash-pictures"],
        files: [],
        getData: (type) => (type === "application/json" ? payload : ""),
      },
    };
  }

  it("names the project with its count, lit until a person is chosen", async () => {
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);
    expect(allOfRow(wrapper).exists(), "Any project has no row").toBe(false);

    wrapper.vm.chooseScope(3);
    await flushPromises();
    const row = allOfRow(wrapper);
    expect(row.text()).toContain("All of Book");
    expect(row.find(".sidebar-list-count").text()).toBe("118");
    expect(row.classes()).toContain("active");
    expect(row.attributes("aria-current")).toBe("page");

    // The route, not the emit, moves the selection: App writes the store.
    useSelectionStore().selectedCharacter = 1;
    await flushPromises();
    expect(allOfRow(wrapper).classes()).not.toContain("active");
    expect(allOfRow(wrapper).attributes("aria-current")).toBeUndefined();
    wrapper.unmount();
  });

  it("goes back to all of the project on a click", async () => {
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);
    wrapper.vm.chooseScope(3);
    useSelectionStore().selectedCharacter = 1;
    await flushPromises();

    const before = wrapper.emitted("select-character").length;
    await allOfRow(wrapper).trigger("click");
    expect(wrapper.emitted("select-character")).toHaveLength(before + 1);
    const [payload] = wrapper.emitted("select-character").at(-1);
    expect(payload.id).toBe("ALL");
    expect(payload.projectContext).toEqual({ mode: "project", projectId: 3 });
    wrapper.unmount();
  });

  it("stays unlit over the project's favourites, and a click shows all of it", async () => {
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);
    const filters = useFilterStore();
    filters.favouriteOnlyFilter = true;
    wrapper.vm.chooseScope(3);
    await flushPromises();
    expect(allOfRow(wrapper).classes()).not.toContain("active");

    await allOfRow(wrapper).trigger("click");
    expect(filters.favouriteOnlyFilter).toBe(false);
    expect(allOfRow(wrapper).classes()).toContain("active");
    wrapper.unmount();
  });

  it("steps aside while Find is on", async () => {
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);
    wrapper.vm.chooseScope(3);
    await flushPromises();
    expect(allOfRow(wrapper).exists()).toBe(true);
    wrapper.vm.findQuery = "Ada";
    await flushPromises();
    expect(allOfRow(wrapper).exists()).toBe(false);
    wrapper.unmount();
  });

  it("reads All not in a project for the pictures in none", async () => {
    apiGet.mockImplementation((url) =>
      Promise.resolve(
        String(url).includes("/projects/UNASSIGNED/summary")
          ? { data: { image_count: 9 } }
          : respond(url),
      ),
    );
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);
    wrapper.vm.chooseScope("none");
    await flushPromises();
    const row = allOfRow(wrapper);
    expect(row.text()).toContain("All not in a project");
    expect(row.find(".sidebar-list-count").text()).toBe("9");
    expect(row.find(".sidebar-project-tile").exists()).toBe(false);
    wrapper.unmount();
  });

  it("adds pictures dropped on it to the project, and none to Not in a project", async () => {
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);

    wrapper.vm.chooseScope("none");
    await wrapper.vm.handleDropOnScope(pictureDrop([7, 8]));
    expect(apiPatch).not.toHaveBeenCalled();

    wrapper.vm.chooseScope(3);
    await wrapper.vm.handleDropOnScope(pictureDrop([7, 8]));
    expect(apiPatch).toHaveBeenCalledWith("/pictures/project", {
      picture_ids: [7, 8],
      project_id: 3,
      mode: "add",
    });
    expect(wrapper.emitted("images-moved").at(-1)[0]).toEqual({
      imageIds: [7, 8],
      refresh: true,
    });
    wrapper.unmount();
  });

  it("leaves dropped files to the window's importer", async () => {
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);
    wrapper.vm.chooseScope(3);
    const startImport = vi.fn();
    wrapper.vm.imageImporterRef = { startImport };
    await wrapper.vm.handleDropOnScope({
      preventDefault: () => {},
      dataTransfer: {
        types: ["Files"],
        files: [new File(["x"], "a.png", { type: "image/png" })],
        items: [],
        getData: () => "",
      },
    });
    await flushPromises();
    expect(startImport).not.toHaveBeenCalled();
    expect(apiPatch).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("takes no drop in a read-only session", async () => {
    isReadOnly.value = true;
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);
    wrapper.vm.chooseScope(3);
    const drag = pictureDrop([7]);
    let accepted = false;
    drag.preventDefault = () => {
      accepted = true;
    };
    wrapper.vm.dragOverScopeItem(drag);
    expect(accepted, "the row accepted the drag").toBe(false);
    await wrapper.vm.handleDropOnScope(pictureDrop([7]));
    expect(apiPatch).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("is a project token's first row, which has no dropdown", async () => {
    isReadOnly.value = true;
    sessionContext.value = { scope: "READ", resource_type: "project", resource_id: 3 };
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);
    expect(wrapper.findComponent({ name: "SidebarScopeSlot" }).exists()).toBe(false);
    expect(allOfRow(wrapper).text()).toContain("All of Book");
    wrapper.unmount();
  });

  it("is not drawn for a person or set token, which has no project", async () => {
    for (const resourceType of ["character", "picture_set"]) {
      setActivePinia(createPinia());
      scopeSessionTo(resourceType);
      const wrapper = mountSidebar();
      await refreshAndSettle(wrapper);
      expect(allOfRow(wrapper).exists(), resourceType).toBe(false);
      wrapper.unmount();
    }
  });
});
