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

/** Route each GET to a plausible, well-shaped body for its endpoint. */
function respond(url) {
  const u = String(url ?? "");
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

describe("the docked sidebar's project switcher", () => {
  it("is offered to the owner", async () => {
    const wrapper = mountSidebar({ docked: true });
    await flushPromises();
    expect(wrapper.find(".sidebar-collapsed-project-wrap").exists()).toBe(true);
    wrapper.unmount();
  });

  it("is omitted for a token scoped to another resource", async () => {
    // The same rule the expanded sidebar already applied to its tab strip: with
    // no project list to show and no Folders tab to reach, the switcher leads
    // nowhere.
    for (const resourceType of ["character", "picture_set", "picture"]) {
      setActivePinia(createPinia());
      scopeSessionTo(resourceType);
      const wrapper = mountSidebar({ docked: true });
      await flushPromises();
      expect(
        wrapper.find(".sidebar-collapsed-project-wrap").exists(),
        `a ${resourceType}-scoped token was still offered the switcher`,
      ).toBe(false);
      wrapper.unmount();
    }
  });

  it("stays for an unscoped read-only token, which can read every project", () => {
    isReadOnly.value = true;
    sessionContext.value = { scope: "READ", resource_type: null };
    const wrapper = mountSidebar({ docked: true });
    expect(wrapper.find(".sidebar-collapsed-project-wrap").exists()).toBe(true);
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

  it("asks for the not-in-a-project count once, for the scope menu", async () => {
    // The header slot's "Not in a project" row (#2159) is the number's one
    // consumer; it lands in the scope counts, not in the per-project map.
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
    expect(wrapper.vm.scopeCounts.none).toBe(9);

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

describe("the project scope in the header slot (#2159)", () => {
  it("navigates to All media in the project chosen", async () => {
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);

    wrapper.vm.chooseScope(3);
    const [payload] = wrapper.emitted("select-character").at(-1);
    expect(payload.id).toBe("ALL");
    expect(payload.projectContext).toEqual({ mode: "project", projectId: 3 });
    expect(wrapper.vm.scopeCounts.current).toBe(118);

    wrapper.unmount();
  });

  it("scopes to the pictures in no project, and back to any", async () => {
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);

    wrapper.vm.chooseScope("none");
    expect(wrapper.emitted("select-character").at(-1)[0].projectContext).toEqual(
      { mode: "project", projectId: null },
    );

    wrapper.vm.chooseScope("any");
    expect(wrapper.emitted("select-character").at(-1)[0].projectContext).toEqual(
      { mode: "global", projectId: null },
    );

    wrapper.unmount();
  });

  it("opens a person inside the scope, not the person's own project", async () => {
    // Ada's own project_id is unset; seen from project 4 she must stay in 4.
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);
    wrapper.vm.chooseScope(4);

    wrapper.vm.selectCharacter(1, "Ada");
    expect(wrapper.emitted("select-character").at(-1)[0].projectContext).toEqual(
      { mode: "project", projectId: 4 },
    );

    wrapper.unmount();
  });

  it("blanks the favourites count until the new scope's arrives", async () => {
    apiGet.mockImplementation((url) =>
      Promise.resolve(
        String(url).startsWith("/pictures/count")
          ? { data: { count: 6 } }
          : respond(url),
      ),
    );
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);
    expect(wrapper.vm.favouriteCount).toBe(6);

    wrapper.vm.chooseScope(3);
    await wrapper.vm.$nextTick();
    expect(wrapper.vm.favouriteCount).toBeNull();

    wrapper.unmount();
  });

  it("asks for the favourites count in the current scope", async () => {
    const wrapper = mountSidebar();
    await refreshAndSettle(wrapper);
    wrapper.vm.chooseScope(3);
    await refreshAndSettle(wrapper);

    const counts = apiGet.mock.calls
      .map(([url]) => String(url))
      .filter((u) => u.startsWith("/pictures/count"));
    expect(counts.at(-1)).toBe("/pictures/count?favourite=1&project_id=3");

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
