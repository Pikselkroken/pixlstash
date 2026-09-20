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

function combination(key, models, { recipes = 1, pictures = 1 } = {}) {
  return {
    key,
    models,
    recipes,
    picture_count: pictures,
    covers: [{ picture_id: 7, url: "/pictures/thumbnails/7.webp?v=1" }],
  };
}

const CKPT = member(1, "realvisXL_v5", "checkpoint");
const VAE = member(2, "sdxl_vae", "vae");
const LORA = member(3, "filmgrain_xl", "adapter");

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
  it("draws one card per stack, named by the files that identify it", async () => {
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [
        combination("1,2", [CKPT, VAE], { recipes: 4, pictures: 12 }),
      ],
    });

    const cards = wrapper.findAll('[data-testid="model-set-card"]');
    expect(cards).toHaveLength(1);
    expect(cards[0].text()).toContain("realvisXL_v5 · sdxl_vae");
  });

  it("says nothing to fold on a card with one combination under it", async () => {
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      combinations: [combination("1", [CKPT], { recipes: 3 })],
    });

    expect(wrapper.text()).toContain("nothing to fold");
    // And no ▸ at all, because there is nothing behind it. The row's existence
    // is asserted first, so this cannot pass on an absent element, and the two
    // stack marks are asserted absent rather than only the ARIA attribute.
    // Not `row`, which is this suite's row-fixture helper.
    const cardRow = wrapper.find('[role="row"]');
    expect(cardRow.exists()).toBe(true);
    expect(cardRow.attributes("aria-expanded")).toBeUndefined();
    expect(wrapper.find(".msc__toggle").exists()).toBe(false);
    expect(wrapper.find(".msc__badge--start").exists()).toBe(false);
  });

  it("opens the folded combinations under the card, listing every file", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5"), row(3, "filmgrain_xl")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [
        combination("1,2", [CKPT, VAE], { recipes: 5, pictures: 20 }),
        combination("1,2,3", [CKPT, VAE, LORA], { recipes: 2, pictures: 5 }),
      ],
    });
    // One card: the two combinations are one file apart, which is the default.
    expect(wrapper.findAll('[data-testid="model-set-card"]')).toHaveLength(1);

    store.toggleSet("1,2");
    await wrapper.vm.$nextTick();

    const combos = wrapper.findAll('[data-testid="model-combo-card"]');
    expect(combos).toHaveLength(2);
    // Every file, not a summary: the second combination lists all three.
    const files = combos[1].findAll(".combo__filename").map((el) => el.text());
    expect(files).toEqual(["realvisXL_v5", "sdxl_vae", "filmgrain_xl"]);
    expect(combos[1].text()).toContain("+ filmgrain_xl");
  });

  it("flags a member the evidence could not pin down rather than hiding it", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [
        combination("1,2", [
          CKPT,
          member(2, "sdxl_vae", "vae", { ambiguous: true }),
        ]),
      ],
    });
    store.toggleSet("1,2");
    await wrapper.vm.$nextTick();

    const combo = wrapper.find('[data-testid="model-combo-card"]');
    expect(combo.findAll(".combo__filename").map((el) => el.text())).toContain(
      "sdxl_vae",
    );
    expect(combo.find(".combo__warn").exists()).toBe(true);
  });

  it("asks a file what else it has run with, naming that file", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2", [CKPT, VAE])],
    });
    store.toggleSet("1,2");
    await wrapper.vm.$nextTick();

    await wrapper.findAll(".combo__filename")[1].trigger("click");

    // The MODEL, by identity, not a rendered stub: the shelf owns the one
    // dialog, so what this grid is responsible for is handing up the right file.
    expect(wrapper.emitted("works-with")).toHaveLength(1);
    expect(wrapper.emitted("works-with")[0][0]).toMatchObject({
      id: 2,
      name: "sdxl_vae",
      kind: "vae",
    });
  });

  it("reaches the same answer from the keyboard, which has no button to press", async () => {
    // Every file button inside a combination is at `tabindex="-1"` because the
    // grid owns Tab, so Enter on a row is the ONLY keyboard route to the
    // companions of a file. Without it the dialog is pointer-only.
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2", [CKPT, VAE])],
    });

    await wrapper
      .find('[role="treegrid"]')
      .trigger("keydown", { key: "Enter" });

    // A card with nothing to fold answers for the file it is NAMED after.
    expect(wrapper.emitted("works-with")[0][0]).toMatchObject({
      id: 1,
      name: "realvisXL_v5",
    });
  });

  it("opens and closes a stack from the keyboard", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5"), row(3, "filmgrain_xl")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [
        combination("1,2", [CKPT, VAE], { pictures: 20 }),
        combination("1,2,3", [CKPT, VAE, LORA], { pictures: 5 }),
      ],
    });
    const grid = wrapper.find('[role="treegrid"]');

    await grid.trigger("keydown", { key: "Enter" });
    expect(store.openSetKey).toBe("1,2");

    await grid.trigger("keydown", { key: "Escape" });
    expect(store.openSetKey).toBe("");
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
    expect(ghost.text()).toContain("In no set — 1 model");
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
