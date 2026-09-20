// The set grid (#1438): the card per combination, the panel under a stack, and
// the card that stands for the models no recipe names.
//
// The assertions worth having are the ones the honesty rule rests on - a set
// with no evidence is DRAWN rather than dropped, an unfoldable card says so, a
// filename-only member is flagged rather than hidden - and the one this screen
// deliberately does not offer: there is nothing here to select, so no verb bar
// can be aimed at a card that stands for several files.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  const stubs = vuetifyComponentStubs();
  const VMenu = {
    name: "VMenu",
    template: `<div><slot name="activator" :props="{}" /><slot /></div>`,
  };
  return new Proxy(stubs, {
    get: (target, prop) => (prop === "VMenu" ? VMenu : target[prop]),
  });
});

const fetchWorkflowSets = vi.fn();
// One double per result set, like the store's own suite: `/adapters` is ONE
// route serving five `file_kind`s, and a single mock would answer the engines
// and support requests with the same rows and make the shelf look duplicated.
const listAdapters = vi.fn();
const listSupport = vi.fn();

vi.mock("../../api/modelShelf", () => ({
  BASE_MODEL_UNASSIGNED: "UNASSIGNED",
  listAdapters: (...args) => {
    const kind = args[0]?.fileKind;
    if (kind === "vae") return listSupport(...args);
    if (kind) return Promise.resolve([]);
    return listAdapters(...args);
  },
  listCheckpoints: vi.fn().mockResolvedValue([]),
  listBaseModelCompletions: vi.fn().mockResolvedValue([]),
  editModels: vi.fn(),
  forgetModels: vi.fn(),
  deleteModels: vi.fn(),
  setAdapterAttachments: vi.fn(),
  fetchWorkflowSets: (...args) => fetchWorkflowSets(...args),
}));

vi.mock("../../api/modelIcons", () => ({
  setModelIcon: vi.fn(),
  clearModelIcons: vi.fn(),
  modelIconUrl: (sha) => `/api/v1/model-icons/${sha}`,
}));

import ModelSetGrid from "./ModelSetGrid.vue";
import { pictureThumbnailUrl } from "../../api/pictures";
import { useModelShelfStore } from "../../stores/useModelShelfStore";

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      Tooltip: {
        template: "<span><slot name='activator' :props='{}' /><slot /></span>",
      },
    },
  },
};

/** A shelf row, as `/adapters` returns one. */
function row(id, filename, fileKind = "adapter") {
  return {
    id,
    sha256: String(id).repeat(64).slice(0, 64),
    file_kind: fileKind,
    kind: fileKind === "adapter" ? "lora" : null,
    display_name: null,
    filename,
    base_model: null,
    locations: [
      { state: "present", folder_id: 1, folder_path: "/m", relpath: filename },
    ],
    attachments: [],
  };
}

function member(id, name, kind, extra = {}) {
  return {
    id,
    name,
    kind,
    filename: name,
    file_size: 1000,
    ambiguous: false,
    ...extra,
  };
}

function combination(
  key,
  models,
  { recipes = 1, pictures = 1, covers = [7] } = {},
) {
  return {
    key,
    models,
    recipes,
    picture_count: pictures,
    // What the route serves: a picture and its cache key, never a path. An
    // `<img src>` bypasses the Axios interceptor, so the card is what turns
    // these into a URL through `pictureThumbnailUrl`.
    covers: covers.map((id) => ({ picture_id: id, version: `v${id}` })),
  };
}

const CKPT = member(1, "realvisXL_v5", "checkpoint");
const VAE = member(2, "sdxl_vae", "vae");
const LORA = member(3, "filmgrain_xl", "adapter");
const OTHER_CKPT = member(4, "juggernautXL_v9", "checkpoint");

async function mountGrid({
  combinations = [],
  noSet = [],
  rows = [],
  support = [],
} = {}) {
  listAdapters.mockResolvedValue(rows);
  listSupport.mockResolvedValue(support);
  fetchWorkflowSets.mockResolvedValue({ combinations, no_set: noSet });
  const store = useModelShelfStore();
  await store.fetchRows();
  const wrapper = mount(ModelSetGrid, globalOpts);
  await new Promise((resolve) => setTimeout(resolve, 0));
  await wrapper.vm.$nextTick();
  return { wrapper, store };
}

beforeEach(() => {
  setActivePinia(createPinia());
  window.localStorage.clear();
  listAdapters.mockReset().mockResolvedValue([]);
  listSupport.mockReset().mockResolvedValue([]);
  fetchWorkflowSets
    .mockReset()
    .mockResolvedValue({ combinations: [], no_set: [] });
});

describe("the cards", () => {
  it("draws one card per base model, named after it", async () => {
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [
        combination("1,2", [CKPT, VAE], { recipes: 4, pictures: 12 }),
        // A second recipe under the SAME checkpoint: one card, not two.
        combination("1,2,3", [CKPT, VAE, LORA], { recipes: 1, pictures: 3 }),
      ],
    });

    const cards = wrapper.findAll('[data-testid="model-set-card"]');
    expect(cards).toHaveLength(1);
    expect(cards[0].text()).toContain("realvisXL_v5");
    // The per-kind tally of everything else, and the facts summed over both.
    expect(cards[0].text()).toContain("VAE 1");
    expect(cards[0].text()).toContain("LoRA 1");
    expect(cards[0].text()).toContain("3 models");
    expect(cards[0].text()).toContain("5 recipes");
    expect(cards[0].text()).toContain("15 pictures");
  });

  it("lays the cover out for the pictures it has, never leaving a hole", async () => {
    // `WorkflowCard` learned this once and the first copy of its geometry here
    // did not: a fixed 2fr/1fr mosaic drawn for three cells leaves an empty box
    // when a set has one or two pictures (#1479 review).
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2", [CKPT, VAE], { covers: [7] })],
    });

    const cover = wrapper.find(".msc__cover");
    expect(cover.classes()).toContain("msc__cover--1");
    expect(cover.findAll(".msc__pic")).toHaveLength(1);
    expect(cover.findAll("img")).toHaveLength(1);
  });

  it("loads a cover through the api layer, base and cache key and all", async () => {
    // **An `<img src>` never reaches the Axios interceptor.** The payload sends
    // `{picture_id, version}`, and a card that put a bare path in `src` asked the
    // PAGE origin for a route it does not serve - every cover broken, and no
    // fixture noticed because each asserted the string it had just been handed.
    // So this asserts the URL the BROWSER is given, on the card and in the tray.
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2", [CKPT, VAE], { covers: [7] })],
    });

    const expected = pictureThumbnailUrl(7, { version: "v7" });
    expect(wrapper.find(".msc__cover img").attributes("src")).toBe(expected);
    expect(expected).toContain("/pictures/thumbnails/7.webp");
    expect(expected).toContain("v=v7");

    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-testid="model-set-panel"]').exists()).toBe(true);
  });

  it("says so on a card nothing else has ever run with", async () => {
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      combinations: [combination("1", [CKPT], { recipes: 3 })],
    });

    expect(wrapper.text()).toContain("Nothing else has run with it");
    // The ▸ is still there: a tray of one model is still a tray, and the card
    // has no other way to show which file it is.
    expect(wrapper.find(".msc__toggle").exists()).toBe(true);
  });

  it("opens a tray of the MODELS that have run with the base model", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5"), row(3, "filmgrain_xl")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [
        combination("1,2", [CKPT, VAE], { recipes: 5, pictures: 20 }),
        combination("1,2,3", [CKPT, VAE, LORA], { recipes: 2, pictures: 5 }),
      ],
    });
    // One card for the two combinations.
    expect(wrapper.findAll('[data-testid="model-set-card"]')).toHaveLength(1);

    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();

    // Three MEMBERS, one per model - not two members, one per combination.
    const members = wrapper.findAll('[data-testid="model-set-member"]');
    expect(members).toHaveLength(3);
    expect(members.map((m) => m.find(".msm__name").text())).toEqual([
      "realvisXL_v5",
      "sdxl_vae",
      "filmgrain_xl",
    ]);
    // The head is marked, and the LoRA carries its own evidence.
    expect(members[0].text()).toContain("Names this set");
    expect(members[2].text()).toContain("2 recipes");
    expect(members[2].text()).toContain("5 pictures");
  });

  it("says what a union cannot claim, before the list of it", async () => {
    // A group is every model its base model has co-occurred with, so two files in
    // one tray may never have run together. Without this the tray reads as a
    // reproducible set, which is the one claim this feature must not make.
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2", [CKPT, VAE])],
    });
    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();

    const note = wrapper.find(".msp__note");
    expect(note.exists()).toBe(true);
    expect(note.text()).toContain("not necessarily run with");
  });

  it("marks a member shared across sets, and one that is not", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5"), row(4, "juggernautXL_v9")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [
        combination("1,2", [CKPT, VAE], { pictures: 20 }),
        combination("2,4", [OTHER_CKPT, VAE], { pictures: 5 }),
      ],
    });
    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();

    const text = wrapper
      .findAll('[data-testid="model-set-member"]')
      .map((m) => m.text());
    // The VAE serves both checkpoints; the checkpoint serves only its own set.
    expect(text.find((t) => t.includes("sdxl_vae"))).toContain(
      "Also in 1 other set",
    );
    expect(text.find((t) => t.includes("realvisXL_v5"))).toContain(
      "Only in this set",
    );
  });

  it("flags a member the evidence could not pin down rather than hiding it", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2", [CKPT, { ...VAE, ambiguous: true }])],
    });
    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();

    const members = wrapper.findAll('[data-testid="model-set-member"]');
    const vae = members.find((m) => m.text().includes("sdxl_vae"));
    expect(vae.classes()).toContain("msm--unsure");
    expect(vae.text()).toContain("Which file ran is not recorded");
  });

  it("asks a model what else it has run with, naming that model", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2", [CKPT, VAE])],
    });
    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();

    await wrapper.findAll(".msm__name")[1].trigger("click");

    // The MODEL, by identity: the shelf owns the one dialog, so what this grid is
    // responsible for is handing up the right file.
    expect(wrapper.emitted("works-with")).toHaveLength(1);
    expect(wrapper.emitted("works-with")[0][0]).toMatchObject({
      id: 2,
      name: "sdxl_vae",
      kind: "vae",
    });
  });

  it("reaches every model in the tray from the keyboard", async () => {
    // The member cards' name buttons are at `tabindex="-1"` because the grid owns
    // Tab, so Enter on a tray row is the keyboard's route to a model's companions.
    // Now that a row IS one model, every one of them is reachable - the earlier
    // shape had a row per combination and could only offer its head (#1479).
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2", [CKPT, VAE])],
    });
    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();

    const grid = wrapper.find('[role="treegrid"]');
    // Down from the card lands on the first tray row, Down again on the second.
    await grid.trigger("keydown", { key: "ArrowDown" });
    await grid.trigger("keydown", { key: "ArrowDown" });
    await grid.trigger("keydown", { key: "Enter" });

    expect(wrapper.emitted("works-with")[0][0]).toMatchObject({
      name: "sdxl_vae",
    });
  });

  it("opens and closes a tray from the keyboard", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2", [CKPT, VAE])],
    });
    const grid = wrapper.find('[role="treegrid"]');

    await grid.trigger("keydown", { key: "Enter" });
    expect(store.openSetKey).toBe("model:1");

    await grid.trigger("keydown", { key: "Escape" });
    expect(store.openSetKey).toBe("");
  });

  it("draws the tray as a list when the reader asks for one", async () => {
    // The Workflows stack panel's own switch, so a reader learns it once. The
    // columns differ because a set's members are models.
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2", [CKPT, VAE])],
    });
    store.toggleSet("model:1");
    store.setView({ trayView: "list" });
    await wrapper.vm.$nextTick();

    expect(wrapper.findAll('[data-testid="model-set-member"]')).toHaveLength(0);
    const heads = wrapper.findAll('[role="columnheader"]').map((h) => h.text());
    expect(heads).toEqual([
      "Model",
      "Kind",
      "Size",
      "Recipes",
      "Pictures",
      "In other sets",
    ]);
    // A row is still a level-2 treegrid row, and clicking it asks the same
    // question the card's name does.
    await wrapper.findAll(".msp__row")[1].trigger("click");
    expect(wrapper.emitted("works-with")[0][0]).toMatchObject({
      name: "sdxl_vae",
    });
  });
});

describe("the models no recipe names", () => {
  it("draws them as a card of their own, worded so it is not a verdict", async () => {
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "never_run", "vae")],
      combinations: [combination("1", [CKPT])],
      noSet: [2],
    });

    const ghost = wrapper.find(".msg__ghost");
    expect(ghost.exists()).toBe(true);
    expect(ghost.text()).toContain("In no set");
    expect(ghost.text()).toContain("1 model");
    // The NARROW claim, and no more: `no_set` means no kept picture here was
    // made with them, which is not "no recipe names them" and is not a verdict.
    expect(ghost.text()).toContain("No kept picture in this library was made");
    expect(ghost.text()).toContain(
      "nothing here rules out what they work with",
    );
    expect(ghost.text()).not.toContain("No recipe in this library binds");
  });

  it("is narrowed by Show, like every other card", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "never_run", "vae")],
      combinations: [],
      noSet: [1, 2],
    });
    expect(wrapper.find(".msg__ghost").text()).toContain("2 models");

    await store.setFilters({ support: false });
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".msg__ghost").text()).toContain("1 model");
  });

  it("is not a row in the grid, so the arrows never walk into it", async () => {
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "never_run", "vae")],
      combinations: [combination("1", [CKPT])],
      noSet: [2],
    });

    // The treegrid holds the cards and nothing else: a ghost inside it would be
    // the last thing Down reaches, and it is not a set.
    const grid = wrapper.find('[role="treegrid"]');
    expect(grid.findAll('[role="row"]')).toHaveLength(1);
    expect(grid.find(".msg__ghost").exists()).toBe(false);
  });
});

describe("what this screen does not offer", () => {
  it("makes no card selectable, so no verb can be aimed at a set", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2", [CKPT, VAE])],
    });

    const grid = wrapper.find('[role="treegrid"]');
    expect(grid.attributes("aria-multiselectable")).toBeUndefined();
    await grid.find('[role="row"]').trigger("click");
    expect(store.selectedIds.size).toBe(0);
  });
});

describe("when there is nothing to group", () => {
  it("says no picture records its models, and does not call that a verdict", async () => {
    const { wrapper } = await mountGrid({
      rows: [],
      combinations: [],
      noSet: [],
    });

    expect(wrapper.text()).toContain("No picture in this library records");
    expect(wrapper.find('[role="treegrid"]').exists()).toBe(false);
  });

  it("reports a failed read rather than an empty grid", async () => {
    listAdapters.mockResolvedValue([]);
    fetchWorkflowSets.mockRejectedValue(new Error("hub is busy"));
    const store = useModelShelfStore();
    await store.fetchRows();
    const wrapper = mount(ModelSetGrid, globalOpts);
    await new Promise((resolve) => setTimeout(resolve, 0));
    await wrapper.vm.$nextTick();

    expect(wrapper.find('[role="alert"]').text()).toContain("hub is busy");
  });
});
