// The Studio (#2819): its grids and their filters, Favourites mixing kinds,
// the project carried by the route, and a scene's Shoot running the chosen
// workflow with the shot the scene writes and handing the runs to the scene.

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
  getScene: vi.fn(),
  patchScene: vi.fn(),
  writeShot: vi.fn(),
  followSceneRuns: vi.fn(),
  createScene: vi.fn(),
  setStudioFavourite: vi.fn(),
  listWorkflowCards: vi.fn(),
  preflightWorkflowRun: vi.fn(),
  runWorkflowCard: vi.fn(),
  deleteScene: vi.fn(),
  confirm: vi.fn(),
  listSetInStudio: vi.fn(),
  createPictureSet: vi.fn(),
  deletePictureSet: vi.fn(),
  patchPictureSet: vi.fn(),
  bulkAddPicturesToSet: vi.fn(),
  deleteCharacter: vi.fn(),
  patchCharacter: vi.fn(),
  removePictureFromSet: vi.fn(),
  deletePictures: vi.fn(),
}));
vi.mock("../../composables/useConfirm", () => ({
  useConfirm: () => ({ confirm: api.confirm }),
}));
vi.mock("../../api/studio", () => ({
  getStudioOverview: api.getStudioOverview,
  getScene: api.getScene,
  patchScene: api.patchScene,
  writeShot: api.writeShot,
  followSceneRuns: api.followSceneRuns,
  createScene: api.createScene,
  setStudioFavourite: api.setStudioFavourite,
  deleteScene: api.deleteScene,
  removeTake: vi.fn(),
  listSetInStudio: api.listSetInStudio,
  stopUsingSetInStudio: vi.fn(),
}));
vi.mock("../../api/workflows", () => ({
  listWorkflowCards: api.listWorkflowCards,
  preflightWorkflowRun: api.preflightWorkflowRun,
  runWorkflowCard: api.runWorkflowCard,
}));
vi.mock("../../api/pictures", () => ({
  deletePictures: api.deletePictures,
  pictureThumbnailUrl: (id) => `/thumb/${id}.webp`,
  pictureMediaUrl: (id, format) => `/media/${id}.${format}`,
}));
vi.mock("../../api/pictureSets", () => ({
  listPictureSets: vi.fn(),
  createPictureSet: api.createPictureSet,
  deletePictureSet: api.deletePictureSet,
  patchPictureSet: api.patchPictureSet,
  bulkAddPicturesToSet: api.bulkAddPicturesToSet,
  removePictureFromSet: api.removePictureFromSet,
}));
vi.mock("../../api/characters", () => ({
  characterThumbnailUrl: (id) => `/character/${id}.webp`,
  deleteCharacter: api.deleteCharacter,
  patchCharacter: api.patchCharacter,
}));

import { useProjectStore } from "../../stores/useProjectStore";
import { useStudioStore } from "../../stores/useStudioStore";
import { useLocationDrawStore } from "../../stores/useLocationDrawStore";
import { lightboxCloseTarget } from "../../utils/lightboxRoute";
import StudioView from "./StudioView.vue";
import AddToEntityControl from "../widgets/AddToEntityControl.vue";

const OVERVIEW = {
  scenes: [
    {
      id: 1, kind: "scene", name: "Rain at the taxi rank", location_set_id: 30,
      cast_ids: [5, 6], outfit_ids: [20], prop_ids: [40], take_count: 2,
      running: 0, cover_picture_id: 900, favourite: true,
    },
    {
      id: 2, kind: "scene", name: "Shoreline walk", location_set_id: null,
      cast_ids: [6], outfit_ids: [], prop_ids: [], take_count: 0,
      running: 0, cover_picture_id: null, favourite: false,
    },
  ],
  cast: [
    { id: 5, name: "Mira", outfit_ids: [20], scene_count: 1, favourite: true, thumbnail_picture_id: 50 },
    { id: 6, name: "Juno", outfit_ids: [], scene_count: 2, favourite: false, thumbnail_picture_id: 60 },
  ],
  outfits: [
    { id: 20, name: "Mustard raincoat", character_id: 5, picture_ids: [21, 22, 23, 24], favourite: false, scene_count: 1 },
  ],
  locations: [
    { id: 30, name: "Neon rain street", picture_ids: [31, 32, 33, 34], favourite: true, scene_count: 1 },
  ],
  props: [
    { id: 40, name: "Red umbrella", picture_ids: [41, 42, 43, 44], favourite: false, scene_count: 1 },
  ],
};

const SCENE = {
  id: 1,
  name: "Rain at the taxi rank",
  project_id: 7,
  location_set_id: 30,
  camera: { shot: "medium", view: 0 },
  setting: { weather: "heavy rain", time: "night", happening: "", light: "" },
  cast: [
    { character_id: 5, outfit_set_id: 20, doing: "hailing the taxi", pose: "", brings: "refs", box: [0.1, 0.2, 0.2, 0.7] },
    { character_id: 6, outfit_set_id: null, doing: "", pose: "", brings: "text", box: [0.5, 0.2, 0.2, 0.7] },
  ],
  props: [{ set_id: 40, state: "open", held_by: 6, box: null }],
  runs: [],
  takes: [{ picture_id: 900, kind: "still", from_picture_id: null, format: "png" }],
  ended: [],
  favourite: true,
};

let router;

async function mountAt(path) {
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/studio", name: "studio", component: { template: "<div />" } },
      { path: "/", name: "all-pictures", component: { template: "<div />" } },
    ],
  });
  await router.push(path);
  await router.isReady();
  const wrapper = mount(StudioView, { global: { plugins: [router] }, attachTo: document.body });
  mounted = wrapper;
  await flushPromises();
  return wrapper;
}

const cardKeys = (wrapper) =>
  wrapper
    .findAll("[data-testid^=studio-card-]")
    .map((el) => el.attributes("data-testid").replace("studio-card-", ""));

beforeEach(() => {
  setActivePinia(createPinia());
  for (const fn of Object.values(api)) fn.mockReset();
  // A copy: a test that unstars an item must not unstar it for the next.
  api.getStudioOverview.mockResolvedValue(structuredClone(OVERVIEW));
  api.getScene.mockResolvedValue(SCENE);
  api.writeShot.mockResolvedValue({
    prompt: "A medium shot in a neon street (picture 1).",
    negative: "blurry",
    references: [31, 77, 21],
    loras: [],
  });
  api.listWorkflowCards.mockResolvedValue({
    cards: [
      { id: "manual:edit", name: "Image Edit", type: "img2img", reference_inputs: 2 },
      { id: "manual:i2v", name: "MiniMax H3: Image to Video", type: "video", reference_inputs: 0 },
    ],
  });
  api.preflightWorkflowRun.mockResolvedValue({
    groups: [
      {
        picture_inputs: [
          { slot_label: "Picture 1", input_name: "image", mode: "fill" },
          { slot_label: "Picture 2", input_name: "image", mode: "fill" },
          { slot_label: "Mask", input_name: "image", mode: "fixed" },
        ],
      },
    ],
  });
  api.runWorkflowCard.mockResolvedValue({ prompts: [{ run_id: "run-a" }, { run_id: "run-b" }] });
  api.followSceneRuns.mockResolvedValue({ ...SCENE, runs: [{ key: "run-a" }, { key: "run-b" }] });
  try {
    localStorage.clear();
  } catch {
    // jsdom always has it; nothing to clear otherwise.
  }
});

let mounted = null;

afterEach(() => {
  vi.useRealTimers();
  mounted?.unmount();
  mounted = null;
});

describe("the Studio's grids", () => {
  it("lists the scenes and filters them by who is in them", async () => {
    const wrapper = await mountAt("/studio");
    expect(api.getStudioOverview).toHaveBeenCalledWith("any");
    expect(cardKeys(wrapper)).toEqual(["scene-1", "scene-2"]);
    const filter = wrapper.findComponent('[data-testid="studio-filter-person"]');
    // A native select hands back a string.
    await filter.vm.$emit("update:modelValue", "5");
    await flushPromises();
    expect(cardKeys(wrapper)).toEqual(["scene-1"]);
  });

  it("mixes kinds in Favourites, each card named by its kind, and filters by kind", async () => {
    const wrapper = await mountAt("/studio?section=favourites");
    expect(cardKeys(wrapper)).toEqual(["scene-1", "cast-5", "location-30"]);
    // Each badge keeps its icon and its word, so the kind reads without hue.
    const badges = wrapper.findAll(".studio-badge");
    expect(badges.map((b) => b.text().replace(/^mdi-[a-z-]+/, ""))).toEqual([
      "Scene",
      "Cast",
      "Location",
    ]);
    expect(badges.every((b) => b.find(".studio-tile").exists())).toBe(true);
    const seg = wrapper.findComponent({ name: "Segmented" });
    await seg.vm.$emit("update:modelValue", "location");
    await flushPromises();
    expect(cardKeys(wrapper)).toEqual(["location-30"]);
  });

  it("falls back to All when the last favourite of the chosen kind is unstarred", async () => {
    const wrapper = await mountAt("/studio?section=favourites");
    await wrapper.findComponent({ name: "Segmented" }).vm.$emit("update:modelValue", "location");
    await flushPromises();
    useStudioStore().overview.locations[0].favourite = false;
    await flushPromises();
    expect(cardKeys(wrapper)).toEqual(["scene-1", "cast-5"]);
  });

  it("reads the project from the route into the shared project store", async () => {
    await mountAt("/studio?project=7");
    const projectStore = useProjectStore();
    expect(projectStore.projectViewMode).toBe("project");
    expect(projectStore.selectedProjectId).toBe(7);
    expect(api.getStudioOverview).toHaveBeenLastCalledWith(7);
  });

  it("opens an item's lightbox from its card and closes back to the grid", async () => {
    const wrapper = await mountAt("/studio?section=locations");
    // One click selects; it takes two to open.
    await wrapper.find('[data-testid="studio-card-location-30"]').trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.query.open).toBeUndefined();
    await wrapper.find('[data-testid="studio-card-location-30"]').trigger("dblclick");
    await flushPromises();
    expect(router.currentRoute.value.query.open).toBe("location:30");
    expect(wrapper.find('[data-testid="studio-item"]').exists()).toBe(true);
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await flushPromises();
    expect(router.currentRoute.value.query).toEqual({ section: "locations" });
  });

  it("opens New scene at the location a sidebar row menu asked for", async () => {
    const wrapper = await mountAt("/studio?section=scenes&new=location:30");
    expect(router.currentRoute.value.query.new).toBeUndefined();
    const dialog = wrapper.findAllComponents({ name: "AppDialog" }).find((d) => d.props("title") === "New scene");
    expect(dialog.props("open")).toBe(true);
    const location = wrapper.findComponent('[data-testid="studio-new-location"]');
    expect(location.props("modelValue")).toBe("30");
  });

  it("lists every outfit in the Wardrobe, filtered by who wears it, and adds a set as an outfit", async () => {
    const overview = structuredClone(OVERVIEW);
    overview.outfits.push({ id: 25, name: "Yellow wellies", character_id: null, picture_ids: [26], favourite: false, scene_count: 0 });
    api.getStudioOverview.mockResolvedValue(overview);
    const wrapper = await mountAt("/studio?section=wardrobe");
    expect(wrapper.find(".studio-title").text()).toBe("Wardrobe");
    expect(cardKeys(wrapper)).toEqual(["outfit-20", "outfit-25"]);
    expect(wrapper.text()).toContain("Mira's outfit");
    expect(wrapper.text()).toContain("Not anyone's");

    const filter = wrapper.findComponent('[data-testid="studio-filter-wearer"]');
    await filter.vm.$emit("update:modelValue", "nobody");
    expect(cardKeys(wrapper)).toEqual(["outfit-25"]);
    await filter.vm.$emit("update:modelValue", "5");
    expect(cardKeys(wrapper)).toEqual(["outfit-20"]);

    await wrapper.find('[data-testid="studio-draw-sheets"]').trigger("click");
    expect(wrapper.emitted("item-sheets")).toEqual([[{ kind: "outfit" }]]);
    await wrapper.find('[data-testid="studio-add-set"]').trigger("click");
    await flushPromises();
    const dialog = wrapper.findAllComponents({ name: "AppDialog" }).find((d) => d.props("title") === "Add an outfit");
    expect(dialog.props("open")).toBe(true);
    await wrapper.findComponent('[data-testid="studio-add-set-pick"]').vm.$emit("update:modelValue", "88");
    await dialog.findAllComponents({ name: "AppButton" }).find((b) => b.text() === "Add").trigger("click");
    await flushPromises();
    expect(api.listSetInStudio).toHaveBeenCalledWith(88, "outfit");
  });

  it("opens Add a set when a sidebar row menu asks for it", async () => {
    const wrapper = await mountAt("/studio?section=props&add=1");
    expect(router.currentRoute.value.query).toEqual({ section: "props" });
    const dialog = wrapper.findAllComponents({ name: "AppDialog" }).find((d) => d.props("title") === "Add a prop");
    expect(dialog.props("open")).toBe(true);
  });
});

const selected = (wrapper) =>
  wrapper
    .findAll(".studio-card[aria-pressed=true]")
    .map((el) => el.attributes("data-key"));
const menu = () => document.body.querySelector('[data-testid="studio-card-menu"]');
const verb = (name) => menu()?.querySelector(`[data-verb="${name}"]`);

describe("selecting Studio cards", () => {
  it("selects on click, adds with Ctrl, ranges with Shift, and clears on Esc or the empty grid", async () => {
    const wrapper = await mountAt("/studio?section=favourites");
    const card = (key) => wrapper.find(`[data-key="${key}"]`);
    expect(selected(wrapper)).toEqual([]);

    await card("scene:1").trigger("click");
    expect(selected(wrapper)).toEqual(["scene:1"]);
    await card("location:30").trigger("click", { ctrlKey: true });
    expect(selected(wrapper)).toEqual(["scene:1", "location:30"]);
    await card("scene:1").trigger("click", { ctrlKey: true });
    expect(selected(wrapper)).toEqual(["location:30"]);

    const keys = wrapper.findAll(".studio-card").map((el) => el.attributes("data-key"));
    await card(keys[0]).trigger("click");
    await card(keys[2]).trigger("click", { shiftKey: true });
    expect(selected(wrapper)).toEqual(keys.slice(0, 3));
    expect(router.currentRoute.value.query.open).toBeUndefined();

    await card(keys[0]).trigger("keydown", { key: "Escape" });
    expect(selected(wrapper)).toEqual([]);

    await card("scene:1").trigger("click");
    const scroller = wrapper.find(".studio-scroll--tiles");
    await scroller.trigger("pointerdown");
    // A real click carries its count; trigger() cannot set it.
    scroller.element.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 }));
    await flushPromises();
    expect(selected(wrapper)).toEqual([]);
  });

  it("opens the menu on a right-click, on that card alone when it was not selected", async () => {
    const wrapper = await mountAt("/studio?section=scenes");
    await wrapper.find('[data-key="scene:2"]').trigger("click");
    await wrapper.find('[data-key="scene:1"]').trigger("contextmenu");
    await flushPromises();
    expect(selected(wrapper)).toEqual(["scene:1"]);
    expect(verb("open")).toBeTruthy();
    expect(verb("duplicate").disabled).toBe(false);

    verb("open").click();
    await flushPromises();
    expect(router.currentRoute.value.query.open).toBe("scene:1");
    expect(menu()).toBeNull();
  });

  it("duplicates a scene with its cast, props, setting and camera under a free name", async () => {
    api.createScene.mockResolvedValue({ id: 9 });
    const wrapper = await mountAt("/studio?section=scenes");
    await wrapper.find('[data-key="scene:1"]').trigger("contextmenu");
    await flushPromises();
    verb("duplicate").click();
    await flushPromises();
    expect(api.createScene).toHaveBeenCalledWith({
      name: "Rain at the taxi rank copy",
      project_id: 7,
      location_set_id: 30,
    });
    expect(api.patchScene).toHaveBeenCalledWith(9, {
      camera: SCENE.camera,
      setting: SCENE.setting,
      cast: SCENE.cast,
      props: SCENE.props,
    });
  });

  it("takes a half-made scene copy away again when its cast cannot be written", async () => {
    api.createScene.mockResolvedValue({ id: 9 });
    api.patchScene.mockRejectedValueOnce(new Error("offline"));
    api.deleteScene.mockResolvedValue({});
    const wrapper = await mountAt("/studio?section=scenes");
    await wrapper.find('[data-key="scene:1"]').trigger("contextmenu");
    await flushPromises();
    verb("duplicate").click();
    await flushPromises();
    expect(api.deleteScene).toHaveBeenCalledWith(9);
  });

  it("takes a half-made set copy away again when its pictures cannot be added", async () => {
    api.createPictureSet.mockResolvedValue({ picture_set: { id: 32 } });
    api.bulkAddPicturesToSet.mockRejectedValueOnce(new Error("offline"));
    api.deletePictureSet.mockResolvedValue({});
    const wrapper = await mountAt("/studio?section=props");
    await wrapper.find('[data-key="prop:40"]').trigger("contextmenu");
    await flushPromises();
    verb("duplicate").click();
    await flushPromises();
    expect(api.deletePictureSet).toHaveBeenCalledWith(32);
    expect(api.listSetInStudio).not.toHaveBeenCalled();
  });

  it("duplicates a set as a set of the same pictures, listed as the same kind; never a person", async () => {
    api.createPictureSet.mockResolvedValue({ picture_set: { id: 31 } });
    const wrapper = await mountAt("/studio?section=favourites");
    await wrapper.find('[data-key="location:30"]').trigger("contextmenu");
    await flushPromises();
    verb("duplicate").click();
    await flushPromises();
    expect(api.createPictureSet).toHaveBeenCalledWith(
      expect.objectContaining({ name: "Neon rain street copy" }),
    );
    expect(api.bulkAddPicturesToSet).toHaveBeenCalledWith(31, [31, 32, 33, 34]);
    expect(api.listSetInStudio).toHaveBeenCalledWith(31, "location");

    await wrapper.find('[data-key="cast:5"]').trigger("contextmenu");
    await flushPromises();
    expect(verb("duplicate").disabled).toBe(true);
  });

  it("deletes the whole selection, each by its kind, after asking once", async () => {
    api.confirm.mockResolvedValue(true);
    const wrapper = await mountAt("/studio?section=favourites");
    await wrapper.find('[data-key="scene:1"]').trigger("click");
    await wrapper.find('[data-key="cast:5"]').trigger("click", { ctrlKey: true });
    await wrapper.find('[data-key="location:30"]').trigger("click", { ctrlKey: true });
    await wrapper.find('[data-key="location:30"]').trigger("contextmenu");
    await flushPromises();
    expect(verb("delete").textContent).toContain("Delete 3");
    verb("delete").click();
    await flushPromises();
    expect(api.confirm).toHaveBeenCalledTimes(1);
    expect(api.deleteScene).toHaveBeenCalledWith(1);
    expect(api.deleteCharacter).toHaveBeenCalledWith(5);
    expect(api.deletePictureSet).toHaveBeenCalledWith(30);
  });

  it("deletes nothing when the ask is declined", async () => {
    api.confirm.mockResolvedValue(false);
    const wrapper = await mountAt("/studio?section=scenes");
    await wrapper.find('[data-key="scene:1"]').trigger("click");
    await wrapper.find('[data-key="scene:1"]').trigger("keydown", { key: "Delete" });
    await flushPromises();
    expect(api.confirm).toHaveBeenCalledTimes(1);
    expect(api.deleteScene).not.toHaveBeenCalled();
  });

  it("puts the selection in a project: a scene moves, a person or a set gains it", async () => {
    api.getStudioOverview.mockResolvedValue({
      ...OVERVIEW,
      scenes: OVERVIEW.scenes.map((sc) => ({ ...sc, project_ids: sc.id === 1 ? [3] : [] })),
      cast: OVERVIEW.cast.map((c) => ({ ...c, project_ids: [3] })),
      locations: OVERVIEW.locations.map((l) => ({ ...l, project_ids: [] })),
    });
    const wrapper = await mountAt("/studio?section=favourites");
    await wrapper.find('[data-key="scene:1"]').trigger("click");
    await wrapper.find('[data-key="cast:5"]').trigger("click", { ctrlKey: true });
    await wrapper.find('[data-key="location:30"]').trigger("click", { ctrlKey: true });
    await wrapper.find('[data-key="location:30"]').trigger("contextmenu");
    await flushPromises();
    const picker = wrapper.findComponent(AddToEntityControl);
    // Project 3 holds two of the three: the flyout shows it partly ticked.
    expect([...picker.props("membership")["project-3"]].sort()).toEqual(["cast:5", "scene:1"]);

    picker.vm.$emit("selected", { projectId: 9, action: "added" });
    await flushPromises();
    expect(api.patchScene).toHaveBeenCalledWith(1, { project_id: 9 });
    expect(api.patchCharacter).toHaveBeenCalledWith(5, { project_ids: [3, 9] });
    expect(api.patchPictureSet).toHaveBeenCalledWith(30, { project_ids: [9] });

    picker.vm.$emit("selected", { projectId: 3, action: "removed" });
    await flushPromises();
    expect(api.patchScene).toHaveBeenLastCalledWith(1, { project_id: null });
    expect(api.patchCharacter).toHaveBeenLastCalledWith(5, { project_ids: [] });
  });
});

describe("adding existing outfits to a wardrobe", () => {
  async function pickFor(personKey, ids) {
    const overview = structuredClone(OVERVIEW);
    overview.outfits.push(
      { id: 25, name: "Yellow wellies", character_id: null, picture_ids: [26], favourite: false, scene_count: 0 },
    );
    api.getStudioOverview.mockResolvedValue(overview);
    const wrapper = await mountAt(`/studio?section=cast&open=${personKey}`);
    await wrapper.find('[data-testid="studio-add-outfit"]').trigger("click");
    await flushPromises();
    for (const id of ids) await wrapper.find(`[data-testid="outfit-pick-${id}"]`).trigger("click");
    await wrapper.find('[data-testid="outfit-add"]').trigger("click");
    await flushPromises();
    return wrapper;
  }

  it("makes an outfit that is no one's the person's, and copies another person's", async () => {
    api.patchPictureSet.mockResolvedValue({});
    api.createPictureSet.mockResolvedValue({ picture_set: { id: 90 } });
    await pickFor("cast:6", [25, 20]);
    expect(api.patchPictureSet).toHaveBeenCalledWith(25, { parent_character_id: 6 });
    // Mira's raincoat is taken from nobody: Juno gets a set of the same pictures.
    expect(api.patchPictureSet).not.toHaveBeenCalledWith(20, expect.anything());
    expect(api.createPictureSet).toHaveBeenCalledWith(
      expect.objectContaining({ name: "Mustard raincoat", parent_character_id: 6 }),
    );
    expect(api.bulkAddPicturesToSet).toHaveBeenCalledWith(90, [21, 22, 23, 24]);
    expect(api.listSetInStudio).toHaveBeenCalledWith(90, "outfit");
  });

  it("names the copy for the person when its name is taken", async () => {
    api.createPictureSet
      .mockRejectedValueOnce(Object.assign(new Error("taken"), { response: { status: 409 } }))
      .mockResolvedValueOnce({ picture_set: { id: 91 } });
    await pickFor("cast:6", [20]);
    expect(api.createPictureSet).toHaveBeenLastCalledWith(
      expect.objectContaining({ name: "Mustard raincoat (Juno)" }),
    );
    expect(api.listSetInStudio).toHaveBeenCalledWith(91, "outfit");
  });

  it("offers nothing the person already wears", async () => {
    const overview = structuredClone(OVERVIEW);
    api.getStudioOverview.mockResolvedValue(overview);
    const wrapper = await mountAt("/studio?section=cast&open=cast:5");
    await wrapper.find('[data-testid="studio-add-outfit"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="outfit-pick-20"]').exists()).toBe(false);
  });
});

describe("a picture from the Studio in the Lightbox", () => {
  // Opened over All Pictures, and closed back onto the Studio as it was.
  async function opensAndComesBack(open) {
    const here = router.currentRoute.value.fullPath;
    await open();
    await flushPromises();
    const at = router.currentRoute.value;
    expect(at.name).toBe("all-pictures");
    await router.replace(lightboxCloseTarget(at.query));
    expect(router.currentRoute.value.fullPath).toBe(here);
    return at.query.overlay;
  }

  it("opens a location's picture on a double-click, and Esc comes back to the location", async () => {
    const wrapper = await mountAt("/studio?section=locations&open=location:30");
    const overlay = await opensAndComesBack(() =>
      wrapper.find('[data-testid="studio-pic-32"]').trigger("dblclick"),
    );
    expect(overlay).toBe("32");
  });

  it("opens a person's picture and a scene's take the same way", async () => {
    const person = await mountAt("/studio?section=cast&open=cast:5");
    expect(
      await opensAndComesBack(() => person.find('[data-testid="studio-person-face"]').trigger("dblclick")),
    ).toBe("50");
    person.unmount();
    const scene = await mountAt("/studio?section=scenes&open=scene:1");
    expect(
      await opensAndComesBack(() => scene.find('[data-testid="scene-take-900"]').trigger("dblclick")),
    ).toBe("900");
  });
});

describe("a location draw in the background", () => {
  it("shows its progress under the location's card, and opens it from there", async () => {
    const draws = useLocationDrawStore();
    draws.report({ locationId: 30, name: "Neon rain street", status: "Filming the place…", fraction: 0.4, ready: false });
    const wrapper = await mountAt("/studio?section=locations");
    const bar = wrapper.find('[data-testid="studio-draw-progress-30"]');
    expect(bar.text()).toContain("Filming the place…");
    expect(bar.find(".draw-progress__track span").attributes("style")).toContain("width: 40%");
    await bar.trigger("click");
    expect(draws.dialogOpen).toBe(true);
    // The card itself still selects: the bar is its own control.
    expect(wrapper.find('[data-key="location:30"]').attributes("aria-pressed")).toBe("false");
  });

  it("shows a new location being drawn as a card of its own until its set exists", async () => {
    const draws = useLocationDrawStore();
    draws.report({ locationId: null, name: "Hotel room", status: "Wide pictures to choose from", fraction: 1, ready: true });
    const wrapper = await mountAt("/studio?section=locations");
    const card = wrapper.find('[data-testid="studio-new-location-draw"]');
    expect(card.text()).toContain("Hotel room");
    expect(card.element.parentElement.textContent).toContain("Wide pictures to choose from");
    await card.trigger("click");
    expect(draws.dialogOpen).toBe(true);
    draws.report(null);
    await flushPromises();
    expect(wrapper.find('[data-testid="studio-new-location-draw"]').exists()).toBe(false);
  });
});

describe("a location's pictures", () => {
  const pic = (wrapper, id) => wrapper.find(`[data-testid="studio-pic-${id}"]`);
  const chosen = (wrapper) =>
    wrapper.findAll(".studio-angle__pick[aria-pressed=true]").map((b) => b.attributes("data-testid"));

  it("shows every picture, the first four named, and takes the selected out of the location", async () => {
    const overview = structuredClone(OVERVIEW);
    overview.locations[0].picture_ids = [31, 32, 33, 34, 35];
    api.getStudioOverview.mockResolvedValue(overview);
    api.removePictureFromSet.mockResolvedValue({});
    const wrapper = await mountAt("/studio?section=locations&open=location:30");
    expect(wrapper.findAll("figcaption").map((f) => f.text())).toEqual([
      "Wide", "Reverse", "Left", "Right", "Picture 5",
    ]);
    await pic(wrapper, 31).trigger("click");
    await pic(wrapper, 33).trigger("click", { ctrlKey: true });
    expect(chosen(wrapper)).toEqual(["studio-pic-31", "studio-pic-33"]);
    await wrapper.find('[data-testid="studio-pics-remove"]').trigger("click");
    await flushPromises();
    expect(api.removePictureFromSet.mock.calls).toEqual([[30, 31], [30, 33]]);
    // The picture is only taken out of the location, never deleted.
    expect(api.deletePictures).not.toHaveBeenCalled();
  });

  it("deletes a Shift range with the Delete key, and Esc clears before it closes", async () => {
    api.deletePictures.mockResolvedValue({});
    const wrapper = await mountAt("/studio?section=locations&open=location:30");
    await pic(wrapper, 31).trigger("click");
    await pic(wrapper, 33).trigger("click", { shiftKey: true });
    expect(chosen(wrapper)).toEqual(["studio-pic-31", "studio-pic-32", "studio-pic-33"]);
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Delete" }));
    await flushPromises();
    expect(api.deletePictures).toHaveBeenCalledWith([31, 32, 33]);

    await pic(wrapper, 34).trigger("click");
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await flushPromises();
    expect(chosen(wrapper)).toEqual([]);
    expect(router.currentRoute.value.query.open).toBe("location:30");
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await flushPromises();
    expect(router.currentRoute.value.query.open).toBeUndefined();
  });

  it("deletes from the right-click menu, the picture under it alone when it was not selected", async () => {
    api.deletePictures.mockResolvedValue({});
    const wrapper = await mountAt("/studio?section=locations&open=location:30");
    await pic(wrapper, 31).trigger("click");
    await pic(wrapper, 34).trigger("contextmenu");
    await flushPromises();
    expect(chosen(wrapper)).toEqual(["studio-pic-34"]);
    document.body.querySelector('[data-testid="studio-pics-menu"] [data-verb="delete"]').click();
    await flushPromises();
    expect(api.deletePictures).toHaveBeenCalledWith([34]);
    expect(document.body.querySelector('[data-testid="studio-pics-menu"]')).toBeNull();
  });

  it("keeps the lightbox's keys off while the picture menu is open", async () => {
    const wrapper = await mountAt("/studio?section=locations&open=location:30");
    await pic(wrapper, 31).trigger("contextmenu");
    await flushPromises();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Delete" }));
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight" }));
    await flushPromises();
    expect(api.deletePictures).not.toHaveBeenCalled();
    expect(router.currentRoute.value.query.open).toBe("location:30");
  });
});

describe("a scene", () => {
  it("asks before it deletes a scene", async () => {
    const wrapper = await mountAt("/studio?open=scene:1");
    const remove = () =>
      wrapper.findAll("button").find((b) => b.text().includes("Delete the scene"));
    api.confirm.mockResolvedValueOnce(false);
    await remove().trigger("click");
    await flushPromises();
    expect(api.confirm).toHaveBeenCalledWith(expect.objectContaining({ danger: true }));
    expect(api.deleteScene).not.toHaveBeenCalled();
    api.confirm.mockResolvedValueOnce(true);
    api.deleteScene.mockResolvedValue({});
    await remove().trigger("click");
    await flushPromises();
    expect(api.deleteScene).toHaveBeenCalledWith(1);
  });

  it("keeps following its shoots after one failed read", async () => {
    vi.useFakeTimers();
    api.getScene
      .mockResolvedValueOnce({ ...SCENE, runs: [{ key: "run-a" }] })
      .mockRejectedValueOnce(new Error("network"))
      .mockResolvedValue({ ...SCENE, runs: [] });
    await mountAt("/studio?open=scene:1");
    await vi.advanceTimersByTimeAsync(3100);
    await vi.advanceTimersByTimeAsync(3100);
    expect(api.getScene).toHaveBeenCalledTimes(3);
  });

  it("tries a failed first read of the scene again, but not a refused one", async () => {
    vi.useFakeTimers();
    api.getScene.mockRejectedValueOnce(new Error("network"));
    const wrapper = await mountAt("/studio?open=scene:1");
    expect(wrapper.text()).toContain("Couldn't read the scene");
    await vi.advanceTimersByTimeAsync(3100);
    expect(wrapper.find('[data-testid="scene-mark-person-0"]').exists()).toBe(true);

    api.getScene.mockReset();
    api.getScene.mockRejectedValue(
      Object.assign(new Error("gone"), { response: { status: 404, data: { detail: "No scene 2." } } }),
    );
    await router.push("/studio?open=scene:2");
    await vi.advanceTimersByTimeAsync(10000);
    expect(api.getScene).toHaveBeenCalledTimes(1);
  });

  it("puts a box on the stage for each person and prop", async () => {
    const wrapper = await mountAt("/studio?open=scene:1");
    expect(wrapper.find('[data-testid="studio-scene"]').exists()).toBe(true);
    const marks = wrapper.findAll("[data-testid^=scene-mark-]").map((m) => m.text());
    expect(marks[0]).toContain("Mira");
    expect(marks[1]).toContain("Juno");
    expect(marks[2]).toContain("Red umbrella · held by Juno");
  });

  it("places and sizes a box from the keyboard", async () => {
    vi.useFakeTimers();
    api.patchScene.mockImplementation(async (_id, body) => ({ ...SCENE, ...body }));
    const wrapper = await mountAt("/studio?open=scene:1");
    const mira = wrapper.find('[data-testid="scene-mark-person-0"]');
    await mira.trigger("keydown", { key: "ArrowRight" });
    await mira.trigger("keydown", { key: "ArrowDown", altKey: true });
    await vi.advanceTimersByTimeAsync(700);
    const cast = api.patchScene.mock.calls.at(-1)[1].cast;
    expect(cast[0].box.map((v) => Math.round(v * 100))).toEqual([12, 20, 20, 72]);
  });

  it("never sizes a box past the stage's edge into a negative size", async () => {
    vi.useFakeTimers();
    api.getScene.mockResolvedValue({
      ...SCENE,
      cast: [{ ...SCENE.cast[0], box: [1.05, 1.05, 0.2, 0.2] }],
      props: [],
    });
    api.patchScene.mockImplementation(async (_id, body) => ({ ...SCENE, ...body }));
    const wrapper = await mountAt("/studio?open=scene:1");
    await wrapper
      .find('[data-testid="scene-mark-person-0"]')
      .trigger("keydown", { key: "ArrowLeft", altKey: true });
    await vi.advanceTimersByTimeAsync(700);
    const [, , w, h] = api.patchScene.mock.calls.at(-1)[1].cast[0].box;
    expect(w).toBeGreaterThan(0);
    expect(h).toBeGreaterThan(0);
  });

  it("puts down what a person held when they are taken out of the scene", async () => {
    api.patchScene.mockImplementation(async (_id, body) => ({ ...SCENE, ...body }));
    const wrapper = await mountAt("/studio?open=scene:1");
    await wrapper.find('[data-testid="scene-mark-person-1"]').trigger("pointerdown");
    await wrapper.findAll("button").find((b) => b.text().includes("Take out of the scene")).trigger("click");
    await flushPromises();
    const body = api.patchScene.mock.calls.at(-1)[1];
    expect(body.cast.map((m) => m.character_id)).toEqual([5]);
    expect(body.props[0].held_by).toBeNull();
  });

  it("never moves a box wider than the stage off its left edge", async () => {
    vi.useFakeTimers();
    api.getScene.mockResolvedValue({
      ...SCENE,
      cast: [{ ...SCENE.cast[0], box: [0, 0, 1.2, 1.2] }],
      props: [],
    });
    api.patchScene.mockImplementation(async (_id, body) => ({ ...SCENE, ...body }));
    const wrapper = await mountAt("/studio?open=scene:1");
    await wrapper.find('[data-testid="scene-mark-person-0"]').trigger("keydown", { key: "ArrowRight" });
    await vi.advanceTimersByTimeAsync(700);
    const [x, y] = api.patchScene.mock.calls.at(-1)[1].cast[0].box;
    expect(x).toBeGreaterThanOrEqual(0);
    expect(y).toBeGreaterThanOrEqual(0);
  });

  it("shoots with the chosen workflow, filling its picture inputs from the shot", async () => {
    const wrapper = await mountAt("/studio?open=scene:1");
    await wrapper.find('[data-testid="scene-shoot"]').trigger("click");
    await flushPromises();
    // The fixed input is not counted, so two references are asked for.
    expect(api.writeShot).toHaveBeenLastCalledWith(1, { kind: "still", max_references: 2 });
    const body = api.runWorkflowCard.mock.calls[0][0];
    expect(body.workflow_id).toBe("manual:edit");
    expect(body.prompt).toBe("A medium shot in a neon street (picture 1).");
    expect(body.inputs).toEqual([
      { slot_label: "Picture 1", input_name: "image", picture_id: 31 },
      { slot_label: "Picture 2", input_name: "image", picture_id: 77 },
    ]);
    expect(body.count).toBe(2);
    expect(api.followSceneRuns).toHaveBeenCalledWith(1, [
      { key: "run-a", kind: "still", from_picture_id: null },
      { key: "run-b", kind: "still", from_picture_id: null },
    ]);
    expect(wrapper.findAll(".scene-take--drawing")).toHaveLength(2);
  });

  it("keeps handing a shoot's runs to the scene when the first hand-over fails", async () => {
    vi.useFakeTimers();
    api.followSceneRuns.mockRejectedValueOnce(new Error("network"));
    const wrapper = await mountAt("/studio?open=scene:1");
    await wrapper.find('[data-testid="scene-shoot"]').trigger("click");
    await vi.advanceTimersByTimeAsync(3100);
    expect(api.followSceneRuns).toHaveBeenCalledTimes(2);
    expect(api.followSceneRuns.mock.calls[1]).toEqual(api.followSceneRuns.mock.calls[0]);
    expect(wrapper.findAll(".scene-take--drawing")).toHaveLength(2);
  });

  it("stops handing a shoot's runs over when the scene refuses them", async () => {
    vi.useFakeTimers();
    api.followSceneRuns.mockRejectedValue(
      Object.assign(new Error("gone"), { response: { status: 404, data: { detail: "No scene 1." } } }),
    );
    const wrapper = await mountAt("/studio?open=scene:1");
    await wrapper.find('[data-testid="scene-shoot"]').trigger("click");
    await vi.advanceTimersByTimeAsync(10000);
    expect(api.followSceneRuns).toHaveBeenCalledTimes(1);
  });

  it("makes a clip from a still take with the video workflow", async () => {
    const wrapper = await mountAt("/studio?open=scene:1");
    await wrapper.find('[data-testid="scene-take-900"]').trigger("click");
    await wrapper.find('[data-testid="scene-clip-from-take"]').trigger("click");
    await flushPromises();
    expect(api.writeShot).toHaveBeenLastCalledWith(1, {
      kind: "clip",
      from_picture_id: 900,
      max_references: 2,
    });
    const body = api.runWorkflowCard.mock.calls[0][0];
    expect(body.workflow_id).toBe("manual:i2v");
    expect(body.count).toBe(1);
    expect(api.followSceneRuns.mock.calls[0][1][0]).toEqual({
      key: "run-a",
      kind: "clip",
      from_picture_id: 900,
    });
  });

  it("says why a clip cannot start from a still when the workflow takes no picture", async () => {
    api.preflightWorkflowRun.mockResolvedValue({ groups: [{ picture_inputs: [] }] });
    const wrapper = await mountAt("/studio?open=scene:1");
    await wrapper.find('[data-testid="scene-take-900"]').trigger("click");
    await wrapper.find('[data-testid="scene-clip-from-take"]').trigger("click");
    await flushPromises();
    expect(api.runWorkflowCard).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("takes no picture");
  });
});

describe("the sheet tiles", () => {
  const panels = (wrapper, testid) =>
    wrapper
      .find(`[data-testid="${testid}"]`)
      .findAll(".studio-tile-panel")
      .map((p) => {
        const img = p.find("img");
        return [p.attributes("style"), img.exists() ? img.attributes("src") : null];
      });

  const withCast = (cast) =>
    api.getStudioOverview.mockResolvedValue({ ...OVERVIEW, cast });

  it("draws a person as their sheet: four headshots two by two, two body shots", async () => {
    withCast([
      { ...OVERVIEW.cast[0], headshot_ids: [50, 51, 52, 53], body_ids: [54, 55], picture_count: 9 },
    ]);
    const wrapper = await mountAt("/studio?section=cast");
    expect(panels(wrapper, "studio-card-cast-5")).toEqual([
      ["grid-area: 1 / 1;", "/thumb/50.webp"],
      ["grid-area: 1 / 2;", "/thumb/51.webp"],
      ["grid-area: 2 / 1;", "/thumb/52.webp"],
      ["grid-area: 2 / 2;", "/thumb/53.webp"],
      ["grid-area: 1 / 3 / 3 / 4;", "/thumb/54.webp"],
      ["grid-area: 1 / 4 / 3 / 5;", "/thumb/55.webp"],
    ]);
    expect(wrapper.find('[data-testid="studio-draw-sheet-5"]').exists()).toBe(false);
    // A single-kind grid drops the kind badge: the toolbar names the kind.
    expect(wrapper.find(".studio-badge").exists()).toBe(false);
  });

  it("shows a person short of pictures without holes, and offers a sheet", async () => {
    withCast([
      { ...OVERVIEW.cast[0], headshot_ids: [50, 51], body_ids: [], picture_count: 2 },
      { ...OVERVIEW.cast[1], headshot_ids: [60], body_ids: [], picture_count: 1 },
    ]);
    const wrapper = await mountAt("/studio?section=cast");
    // Two pictures: the first fills the headshot half, the other the body half.
    expect(panels(wrapper, "studio-card-cast-5")).toEqual([
      ["grid-area: 1 / 1 / 3 / 3;", "/thumb/50.webp"],
      ["grid-area: 1 / 3 / 3 / 5;", "/thumb/51.webp"],
    ]);
    // One picture: it fills the headshot half and the rest offers a sheet.
    expect(panels(wrapper, "studio-card-cast-6")).toEqual([
      ["grid-area: 1 / 1 / 3 / 3;", "/thumb/60.webp"],
      ["grid-area: 1 / 3 / 3 / 5;", null],
    ]);
    expect(wrapper.text()).toContain("One picture. A sheet fills the other five.");
    await wrapper.find('[data-testid="studio-draw-sheet-6"]').trigger("click");
    const [[payload]] = wrapper.emitted("item-sheets");
    expect(payload.kind).toBe("character");
    expect(payload.character).toMatchObject({ id: 6, name: "Juno", image_count: 1 });
    expect(router.currentRoute.value.query.open).toBeUndefined();
  });

  it("says a person with no pictures has none", async () => {
    withCast([{ ...OVERVIEW.cast[1], thumbnail_picture_id: null, headshot_ids: [], body_ids: [], picture_count: 0 }]);
    const wrapper = await mountAt("/studio?section=cast");
    expect(wrapper.text()).toContain("No pictures yet");
    expect(wrapper.find('[data-testid="studio-draw-sheet-6"]').exists()).toBe(true);
  });

  it("labels a location's four views and keeps a scene's take with its cast", async () => {
    const wrapper = await mountAt("/studio?section=locations");
    const labels = wrapper
      .find('[data-testid="studio-card-location-30"]')
      .findAll(".studio-tile-label")
      .map((l) => l.text());
    expect(labels).toEqual(["Wide", "Reverse", "Left", "Right"]);

    const scenes = await mountAt("/studio");
    const take = scenes.find('[data-testid="studio-card-scene-1"]');
    expect(take.find(".studio-tile-media--scene").exists()).toBe(true);
    expect(take.findAll(".studio-tile-chips img")).toHaveLength(2);
  });

  it("shapes an item's and a scene's card as their pictures, never cropping them", async () => {
    const overview = structuredClone(OVERVIEW);
    overview.locations[0].aspect = 1.7778;
    overview.outfits[0].aspect = 0.8;
    overview.scenes[0].cover_aspect = 1.5;
    api.getStudioOverview.mockResolvedValue(overview);
    const shape = (w, key) =>
      w.find(`[data-testid="studio-card-${key}"] .studio-tile-media`).attributes("style") ?? "";
    const places = await mountAt("/studio?section=locations");
    expect(shape(places, "location-30")).toContain("--tile-aspect: 1.7778");
    places.unmount();
    const clothes = await mountAt("/studio?section=wardrobe");
    expect(shape(clothes, "outfit-20")).toContain("--tile-aspect: 0.8");
    clothes.unmount();
    const scenes = await mountAt("/studio?section=scenes");
    expect(shape(scenes, "scene-1")).toContain("--tile-aspect: 1.5");
    // Not measured: the card keeps the shape its kind's sheet has.
    expect(shape(scenes, "scene-2")).not.toContain("--tile-aspect");
  });

  it("shows an item short of its four angles as its first picture, whole", async () => {
    api.getStudioOverview.mockResolvedValue({
      ...OVERVIEW,
      props: [{ ...OVERVIEW.props[0], picture_ids: [41, 42] }],
    });
    const wrapper = await mountAt("/studio?section=props");
    expect(panels(wrapper, "studio-card-prop-40")).toEqual([
      ["grid-area: 1 / 1;", "/thumb/41.webp"],
    ]);
  });
});
