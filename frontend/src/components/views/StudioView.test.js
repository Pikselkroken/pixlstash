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
  listSetInStudio: vi.fn(),
  stopUsingSetInStudio: vi.fn(),
}));
vi.mock("../../api/workflows", () => ({
  listWorkflowCards: api.listWorkflowCards,
  preflightWorkflowRun: api.preflightWorkflowRun,
  runWorkflowCard: api.runWorkflowCard,
}));
vi.mock("../../api/pictures", () => ({
  pictureThumbnailUrl: (id) => `/thumb/${id}.webp`,
  pictureMediaUrl: (id, format) => `/media/${id}.${format}`,
}));
vi.mock("../../api/pictureSets", () => ({ listPictureSets: vi.fn() }));

import { useProjectStore } from "../../stores/useProjectStore";
import { useStudioStore } from "../../stores/useStudioStore";
import StudioView from "./StudioView.vue";

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
    routes: [{ path: "/studio", name: "studio", component: { template: "<div />" } }],
  });
  await router.push(path);
  await router.isReady();
  const wrapper = mount(StudioView, { global: { plugins: [router] } });
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
  api.getStudioOverview.mockResolvedValue(OVERVIEW);
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

afterEach(() => {
  vi.useRealTimers();
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
    await wrapper.find('[data-testid="studio-card-location-30"]').trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.query.open).toBe("location:30");
    expect(wrapper.find('[data-testid="studio-item"]').exists()).toBe(true);
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await flushPromises();
    expect(router.currentRoute.value.query).toEqual({ section: "locations" });
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
