// The Studio sidebar's row menus: every row offers what its grid's toolbar or
// its lightbox can do, from a right-click or the Menu key.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter } from "vue-router";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

const api = vi.hoisted(() => ({
  getStudioOverview: vi.fn(),
  setStudioFavourite: vi.fn(),
  deleteScene: vi.fn(),
  stopUsingSetInStudio: vi.fn(),
  confirm: vi.fn(),
}));
vi.mock("../../composables/useConfirm", () => ({
  useConfirm: () => ({ confirm: api.confirm }),
}));
vi.mock("../../api/studio", () => ({
  getStudioOverview: api.getStudioOverview,
  setStudioFavourite: api.setStudioFavourite,
  deleteScene: api.deleteScene,
  stopUsingSetInStudio: api.stopUsingSetInStudio,
}));
vi.mock("../../api/projects", () => ({ listProjects: vi.fn(async () => []) }));
vi.mock("../../api/pictures", () => ({
  pictureThumbnailUrl: (id) => `/thumb/${id}.webp`,
}));

import { useProjectStore } from "../../stores/useProjectStore";
import { useStudioStore } from "../../stores/useStudioStore";
import StudioSidebar from "./StudioSidebar.vue";

const OVERVIEW = {
  scenes: [
    { id: 1, name: "Rain at the taxi rank", location_set_id: 30, cast_ids: [5], outfit_ids: [], prop_ids: [], favourite: false },
  ],
  cast: [{ id: 5, name: "Mira", outfit_ids: [20], favourite: true, thumbnail_picture_id: null }],
  outfits: [{ id: 20, name: "Mustard raincoat", character_id: 5, picture_ids: [21], favourite: false }],
  locations: [{ id: 30, name: "Neon rain street", picture_ids: [], favourite: false }],
  props: [{ id: 40, name: "Red umbrella", picture_ids: [41], favourite: false }],
};

let router;
let mounted = null;

async function mountAt(path, { inProject = true } = {}) {
  router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: "/studio", component: { template: "<div />" } }],
  });
  await router.push(path);
  await router.isReady();
  if (inProject) {
    const projects = useProjectStore();
    projects.projectViewMode = "project";
    projects.selectedProjectId = 7;
  }
  const store = useStudioStore();
  store.overview = structuredClone(OVERVIEW);
  store.loaded = true;
  const wrapper = mount(StudioSidebar, {
    global: {
      plugins: [router],
      stubs: {
        teleport: true,
        SidebarScopeSlot: true,
        LocationAnglesDialog: true,
        Tooltip: true,
      },
    },
  });
  await flushPromises();
  mounted = wrapper;
  return wrapper;
}

async function rightClick(wrapper, testid) {
  await wrapper.find(`[data-testid="${testid}"]`).trigger("contextmenu", {
    clientX: 20,
    clientY: 40,
  });
  await flushPromises();
}

const verbs = (wrapper) =>
  wrapper.findAll("[data-testid=studio-menu] .ctx-item").map((b) => b.attributes("data-verb"));

const verb = (wrapper, key) => wrapper.find(`[data-testid=studio-menu] [data-verb="${key}"]`);

beforeEach(() => {
  setActivePinia(createPinia());
  for (const fn of Object.values(api)) fn.mockReset();
  api.getStudioOverview.mockResolvedValue(OVERVIEW);
});

// Each sidebar listens on the document for Escape; a leftover one would take it.
afterEach(() => {
  mounted?.unmount();
  mounted = null;
});

describe("the Studio sidebar's row menus", () => {
  it("offers each kind's verbs on its item rows", async () => {
    const wrapper = await mountAt("/studio?project=7");
    await rightClick(wrapper, "studio-item-scene-1");
    expect(verbs(wrapper)).toEqual(["open", "favourite", "delete"]);
    await rightClick(wrapper, "studio-item-cast-5");
    expect(verbs(wrapper)).toEqual(["open", "favourite", "new-outfit"]);
    await rightClick(wrapper, "studio-item-location-30");
    expect(verbs(wrapper)).toEqual([
      "open",
      "favourite",
      "new-scene-here",
      "more-angles",
      "new-sheets",
      "stop-using",
    ]);
    // A location with no pictures has nothing to draw more angles from.
    expect(verb(wrapper, "more-angles").attributes("disabled")).toBeDefined();
    await rightClick(wrapper, "studio-item-prop-40");
    expect(verbs(wrapper)).toEqual(["open", "favourite", "new-sheets", "stop-using"]);
  });

  it("offers each section's toolbar on its row, with Any project too", async () => {
    const wrapper = await mountAt("/studio", { inProject: false });
    await rightClick(wrapper, "studio-row-scenes");
    expect(verbs(wrapper)).toEqual(["show", "new-scene"]);
    await rightClick(wrapper, "studio-row-favourites");
    expect(verbs(wrapper)).toEqual(["show"]);
    await rightClick(wrapper, "studio-row-cast");
    expect(verbs(wrapper)).toEqual(["show"]);
    await rightClick(wrapper, "studio-row-props");
    expect(verbs(wrapper)).toEqual(["show", "add-set", "draw-sheets"]);

    await verb(wrapper, "draw-sheets").trigger("click");
    expect(wrapper.emitted("item-sheets")).toEqual([[{ kind: "prop" }]]);
    expect(wrapper.find("[data-testid=studio-menu]").exists()).toBe(false);

    await rightClick(wrapper, "studio-row-locations");
    await verb(wrapper, "add-set").trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.query).toMatchObject({ section: "locations", add: "1" });
  });

  it("asks for a new scene at the location it was opened on", async () => {
    const wrapper = await mountAt("/studio?project=7");
    await rightClick(wrapper, "studio-item-location-30");
    await verb(wrapper, "new-scene-here").trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.query).toMatchObject({
      section: "scenes",
      new: "location:30",
    });
  });

  it("stars and unstars the item it was opened on", async () => {
    const wrapper = await mountAt("/studio?project=7");
    await rightClick(wrapper, "studio-item-cast-5");
    expect(verb(wrapper, "favourite").text()).toContain("Remove from favourites");
    await verb(wrapper, "favourite").trigger("click");
    await flushPromises();
    expect(api.setStudioFavourite).toHaveBeenCalledWith("cast", 5, false);
    expect(api.getStudioOverview).toHaveBeenCalled();
  });

  it("deletes a scene only once confirmed, and closes it if it was open", async () => {
    const wrapper = await mountAt("/studio?project=7&section=scenes&open=scene:1");
    api.confirm.mockResolvedValueOnce(false);
    await rightClick(wrapper, "studio-item-scene-1");
    await verb(wrapper, "delete").trigger("click");
    await flushPromises();
    expect(api.deleteScene).not.toHaveBeenCalled();

    api.confirm.mockResolvedValueOnce(true);
    await rightClick(wrapper, "studio-item-scene-1");
    await verb(wrapper, "delete").trigger("click");
    await flushPromises();
    expect(api.deleteScene).toHaveBeenCalledWith(1);
    expect(router.currentRoute.value.query.open).toBeUndefined();
  });

  it("stops using a set in the Studio", async () => {
    const wrapper = await mountAt("/studio?project=7");
    await rightClick(wrapper, "studio-item-prop-40");
    await verb(wrapper, "stop-using").trigger("click");
    await flushPromises();
    expect(api.stopUsingSetInStudio).toHaveBeenCalledWith(40);
  });

  it("opens from the Menu key on a focused row, and Escape closes it", async () => {
    const wrapper = await mountAt("/studio?project=7");
    await wrapper.find('[data-testid="studio-item-prop-40"]').trigger("keydown", {
      key: "F10",
      shiftKey: true,
    });
    await flushPromises();
    expect(verbs(wrapper)).toEqual(["open", "favourite", "new-sheets", "stop-using"]);
    // A lightbox listens for Esc on the window; the menu's Esc must not reach it.
    const lightboxEsc = vi.fn();
    window.addEventListener("keydown", lightboxEsc);
    try {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
      await flushPromises();
      expect(wrapper.find("[data-testid=studio-menu]").exists()).toBe(false);
      expect(lightboxEsc).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener("keydown", lightboxEsc);
    }
  });

  it("closes when the Studio moves somewhere else", async () => {
    const wrapper = await mountAt("/studio?project=7");
    await rightClick(wrapper, "studio-item-prop-40");
    expect(verbs(wrapper)).not.toEqual([]);
    await router.push("/studio?project=7&section=props");
    await flushPromises();
    expect(wrapper.find("[data-testid=studio-menu]").exists()).toBe(false);
  });
});
