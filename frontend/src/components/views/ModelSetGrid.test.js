// The set grid (#1438): the card per combination, the panel under a stack, and
// the card that stands for the models no recipe names.
//
// The assertions worth having are the ones the honesty rule rests on - a set
// with no evidence is DRAWN rather than dropped, an unfoldable card says so, a
// filename-only member is flagged rather than hidden - and the line the
// selection rests on: a card selects the ONE model it is named after, never the
// set behind it, so no verb the bar carries can reach a file the reader did not
// aim at.

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
const createWorkflowSet = vi.fn();
const deleteWorkflowSet = vi.fn();
const addWorkflowSetMembers = vi.fn();
const removeWorkflowSetMembers = vi.fn();
const renameWorkflowSet = vi.fn();
const setWorkflowSetDeclines = vi.fn();
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
  createWorkflowSet: (...args) => createWorkflowSet(...args),
  deleteWorkflowSet: (...args) => deleteWorkflowSet(...args),
  addWorkflowSetMembers: (...args) => addWorkflowSetMembers(...args),
  removeWorkflowSetMembers: (...args) => removeWorkflowSetMembers(...args),
  renameWorkflowSet: (...args) => renameWorkflowSet(...args),
  setWorkflowSetDeclines: (...args) => setWorkflowSetDeclines(...args),
}));

vi.mock("../../api/modelIcons", () => ({
  setModelIcon: vi.fn(),
  clearModelIcons: vi.fn(),
  modelIconUrl: (sha) => `/api/v1/model-icons/${sha}`,
}));

import ModelSetGrid from "./ModelSetGrid.vue";
import WorkflowSetChooser from "../panels/WorkflowSetChooser.vue";
import { pictureThumbnailUrl } from "../../api/pictures";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useOperationStore } from "../../stores/useOperationStore";

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
  handMade = [],
  attach = false,
} = {}) {
  listAdapters.mockResolvedValue(rows);
  listSupport.mockResolvedValue(support);
  fetchWorkflowSets.mockResolvedValue({
    combinations,
    no_set: noSet,
    hand_made: handMade,
  });
  const store = useModelShelfStore();
  await store.fetchRows();
  const wrapper = mount(ModelSetGrid, {
    ...globalOpts,
    ...(attach ? { attachTo: document.body } : {}),
  });
  await new Promise((resolve) => setTimeout(resolve, 0));
  await wrapper.vm.$nextTick();
  return { wrapper, store };
}

beforeEach(() => {
  createWorkflowSet.mockReset();
  deleteWorkflowSet.mockReset();
  addWorkflowSetMembers.mockReset();
  removeWorkflowSetMembers.mockReset();
  renameWorkflowSet.mockReset();
  setWorkflowSetDeclines.mockReset();
  setActivePinia(createPinia());
  // The grid is mounted without the shelf, which is what declares the host.
  useOperationStore().setLocalReceiptHost(true);
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

  it("steps off the Grid/List switch into the tray, not back to Grid", async () => {
    // Clicking List leaves focus on the switch. Down there used to be the
    // radiogroup's "next option" and flipped the tray straight back to Grid.
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2", [CKPT, VAE])],
    });
    store.toggleSet("model:1");
    store.setView({ trayView: "list" });
    await wrapper.vm.$nextTick();

    const list = wrapper.findAll('.msp__header [role="radio"]')[1];
    await list.trigger("keydown", { key: "ArrowDown" });
    expect(store.view.trayView).toBe("list");
    const grid = wrapper.find('[role="treegrid"]');
    await grid.trigger("keydown", { key: "Enter" });
    // The cursor is on a member, so Enter asks what it has run with.
    expect(wrapper.emitted("works-with")).toHaveLength(1);

    // Up from the switch returns to the card, whose Enter closes the tray.
    await list.trigger("keydown", { key: "ArrowUp" });
    expect(store.view.trayView).toBe("list");
    await grid.trigger("keydown", { key: "Enter" });
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
    // A row is still a level-2 treegrid row. A single click SELECTS it, as it
    // does on the row list; the question the card's name asks in Grid is this
    // row's default action, so it is a double click.
    await wrapper.findAll(".msp__row")[1].trigger("click");
    expect(wrapper.emitted("works-with")).toBeUndefined();
    await wrapper.findAll(".msp__row")[1].trigger("dblclick");
    expect(wrapper.emitted("works-with")[0][0]).toMatchObject({
      name: "sdxl_vae",
    });
  });

  /** A four-column card grid, which jsdom cannot give us by measuring. */
  function stubFourColumns() {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor() {}
        observe(element) {
          Object.defineProperty(element, "clientWidth", {
            configurable: true,
            value: 1000,
          });
        }
        disconnect() {}
      },
    );
  }

  it("enters a list tray at its first row, whatever column the card was in", async () => {
    // **Two grids of different widths, so the crossing is not arithmetic.** A
    // List tray is one column whatever the card grid is doing, and stepping by
    // the outer column count carried the card's column offset into it: from the
    // THIRD card of a row, Down landed on the tray's third row. The within-tray
    // step was fixed without this half, so the skip survived at the boundary.
    stubFourColumns();
    try {
      const { wrapper, store } = await mountGrid({
        rows: [row(1, "realvisXL_v5"), row(3, "filmgrain_xl"), row(5, "third")],
        support: [row(2, "sdxl_vae", "vae"), row(6, "clipL", "text_encoder")],
        combinations: [
          combination(
            "5,2,6",
            [
              member(5, "third", "checkpoint"),
              VAE,
              member(6, "clipL", "text_encoder"),
            ],
            { pictures: 1 },
          ),
          combination("1,2", [CKPT, VAE], { pictures: 9 }),
          combination("3x", [LORA], { pictures: 5 }),
        ],
      });
      // The third card in the row is the one whose tray is open.
      store.toggleSet("model:5");
      store.setView({ trayView: "list" });
      await wrapper.vm.$nextTick();
      expect(
        wrapper.findAll(".msg__row").map((c) => c.attributes("data-key")),
      ).toEqual(["model:1", "model:3", "model:5"]);

      const grid = wrapper.find('[role="treegrid"]');
      await grid.trigger("keydown", { key: "ArrowRight" });
      await grid.trigger("keydown", { key: "ArrowRight" });
      await grid.trigger("keydown", { key: "ArrowDown" });
      await grid.trigger("keydown", { key: "Enter" });

      // The tray's FIRST row, not its third. Asserted on the model's name
      // rather than a position, so the fixture order cannot make it pass for
      // the wrong reason.
      expect(wrapper.emitted("works-with")[0][0]).toMatchObject({
        name: "third",
      });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("leaves a list tray at its last row when stepping up from below it", async () => {
    // The same crossing the other way. The tray sits between the first card row
    // and the cards after it, so Up from one of those has to arrive at the row
    // nearest it rather than at a column that tray does not have.
    stubFourColumns();
    try {
      const { wrapper, store } = await mountGrid({
        rows: Array.from({ length: 6 }, (_, i) => row(i + 10, `ckpt${i}`)),
        support: [row(2, "sdxl_vae", "vae")],
        combinations: [
          combination("10,2", [member(10, "ckpt0", "checkpoint"), VAE], {
            pictures: 9,
          }),
          ...[11, 12, 13, 14, 15].map((id, i) =>
            combination(`${id}`, [member(id, `ckpt${i + 1}`, "checkpoint")], {
              pictures: 8 - i,
            }),
          ),
        ],
      });
      store.toggleSet("model:10");
      store.setView({ trayView: "list" });
      await wrapper.vm.$nextTick();

      const grid = wrapper.find('[role="treegrid"]');
      // Down from the first card enters the tray, Down again leaves it for the
      // second row of cards; Up from there must come back to the tray's LAST
      // row, which is the VAE.
      await grid.trigger("keydown", { key: "ArrowDown" });
      await grid.trigger("keydown", { key: "ArrowDown" });
      await grid.trigger("keydown", { key: "ArrowDown" });
      await grid.trigger("keydown", { key: "ArrowUp" });
      await grid.trigger("keydown", { key: "Enter" });

      expect(wrapper.emitted("works-with")[0][0]).toMatchObject({
        name: "sdxl_vae",
      });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("moves one list-tray row at a time even under a multi-column card grid", async () => {
    // Make the outer grid four columns wide. The list tray remains one column,
    // which is the mismatch that used to make Down skip its members.
    stubFourColumns();
    try {
      const { wrapper, store } = await mountGrid({
        rows: [row(1, "realvisXL_v5")],
        support: [row(2, "sdxl_vae", "vae"), row(3, "clip", "text_encoder")],
        combinations: [combination("1,2,3", [CKPT, VAE, LORA])],
      });
      store.toggleSet("model:1");
      store.setView({ trayView: "list" });
      await wrapper.vm.$nextTick();

      const grid = wrapper.find('[role="treegrid"]');
      await grid.trigger("keydown", { key: "ArrowDown" });
      await grid.trigger("keydown", { key: "ArrowDown" });
      await grid.trigger("keydown", { key: "Enter" });

      expect(wrapper.emitted("works-with")[0][0]).toMatchObject({
        name: "sdxl_vae",
      });
    } finally {
      vi.unstubAllGlobals();
    }
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
    // One card, plus the New workflow set tile (#1520) - which is a row, and
    // is the only other thing the arrows may walk to.
    const grid = wrapper.find('[role="treegrid"]');
    expect(grid.findAll('[role="row"]')).toHaveLength(2);
    expect(grid.findAll(".msg__row")).toHaveLength(1);
    expect(grid.find(".msg__ghost").exists()).toBe(false);
  });
});

describe("selection and the verbs", () => {
  /** The grid with one card, its tray open, and three selectable models. */
  async function openTray() {
    const state = await mountGrid({
      rows: [row(1, "realvisXL_v5"), row(3, "filmgrain_xl")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2,3", [CKPT, VAE, LORA])],
    });
    state.store.toggleSet("model:1");
    await state.wrapper.vm.$nextTick();
    return state;
  }

  it("selects the card's BASE MODEL, never the set behind it", async () => {
    // The whole reason this screen is safe to aim a Delete at. A card standing
    // for its tray would put a shared VAE behind a Delete meant for a
    // checkpoint; it stands for the one file it is named after instead.
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5"), row(3, "filmgrain_xl")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2,3", [CKPT, VAE, LORA])],
    });

    const grid = wrapper.find('[role="treegrid"]');
    expect(grid.attributes("aria-multiselectable")).toBe("true");
    await grid.find(".msg__row").trigger("click");

    expect([...store.selectedIds]).toEqual([1]);
    expect(grid.find(".msg__row").attributes("aria-selected")).toBe("true");
    expect(wrapper.find('[data-testid="model-set-card"]').classes()).toContain(
      "msc--on",
    );
  });

  it("selects one model in the tray, and marks it", async () => {
    const { wrapper, store } = await openTray();

    const members = wrapper.findAll(".msp__member");
    await members[1].trigger("click");

    expect([...store.selectedIds]).toEqual([2]);
    expect(members[1].attributes("aria-selected")).toBe("true");
    expect(
      wrapper.findAll('[data-testid="model-set-member"]')[1].classes(),
    ).toContain("msm--on");
  });

  it("does not light the card when its checkpoint is picked in the tray", async () => {
    // The head is drawn twice while its tray is open. Picking the tray row is a
    // pick of that one file, and a lit card reads as the whole set picked.
    const { wrapper, store } = await openTray();
    const card = () => wrapper.find('[data-testid="model-set-card"]');
    const row = () => wrapper.find(".msg__row");

    await wrapper.findAll(".msp__member")[0].trigger("click");

    expect([...store.selectedIds]).toEqual([1]);
    expect(wrapper.findAll(".msp__member")[0].attributes("aria-selected")).toBe(
      "true",
    );
    expect(card().classes()).not.toContain("msc--on");
    expect(row().attributes("aria-selected")).toBe("false");

    // The same model picked from its card does light it.
    await row().trigger("click");
    expect([...store.selectedIds]).toEqual([1]);
    expect(card().classes()).toContain("msc--on");

    // And a selection made anywhere else - Select all - follows the model again.
    await wrapper.findAll(".msp__member")[0].trigger("click");
    expect(card().classes()).not.toContain("msc--on");
    store.selectVisible();
    await wrapper.vm.$nextTick();
    expect(card().classes()).toContain("msc--on");
  });

  it("adds an unlit card with Ctrl or Space rather than toggling its model out", async () => {
    const { wrapper, store } = await openTray();
    const card = () => wrapper.find('[data-testid="model-set-card"]');
    const members = () => wrapper.findAll(".msp__member");

    await members()[0].trigger("click");
    await wrapper.find(".msg__row").trigger("click", { ctrlKey: true });
    expect([...store.selectedIds]).toEqual([1]);
    expect(card().classes()).toContain("msc--on");

    // Space is the keyboard's Ctrl+click and must answer the same way.
    await members()[0].trigger("click");
    const grid = wrapper.find('[role="treegrid"]');
    await grid.trigger("keydown", { key: "ArrowUp" });
    await grid.trigger("keydown", { key: " " });
    expect([...store.selectedIds]).toEqual([1]);
    expect(card().classes()).toContain("msc--on");
  });

  it("right-clicking the checkpoint's tray row selects that row, not its card", async () => {
    // The head occurs twice; a lookup by model id would find the card first.
    const { wrapper, store } = await openTray();
    await wrapper
      .findAll(".msp__member")[0]
      .trigger("contextmenu", { clientX: 1, clientY: 2 });

    expect([...store.selectedIds]).toEqual([1]);
    expect(
      wrapper.find('[data-testid="model-set-card"]').classes(),
    ).not.toContain("msc--on");
  });

  it("lights the card again once the tray showing the pick closes", async () => {
    // Otherwise the bar is armed over a grid that marks nothing selected.
    const { wrapper, store } = await openTray();
    await wrapper.findAll(".msp__member")[0].trigger("click");
    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();

    expect([...store.selectedIds]).toEqual([1]);
    expect(wrapper.find('[data-testid="model-set-card"]').classes()).toContain(
      "msc--on",
    );
  });

  it("lights the card a Shift range passes over, and not one it does not", async () => {
    const { wrapper, store } = await openTray();
    const card = () => wrapper.find('[data-testid="model-set-card"]');
    const members = () => wrapper.findAll(".msp__member");

    // Within the tray: the range never touches the card.
    await members()[0].trigger("click");
    await members()[1].trigger("click", { shiftKey: true });
    expect([...store.selectedIds].sort()).toEqual([1, 2]);
    expect(card().classes()).not.toContain("msc--on");

    // From the card down into the tray: the card is in the range.
    await wrapper.find(".msg__row").trigger("click");
    await members()[1].trigger("click", { shiftKey: true });
    expect([...store.selectedIds].sort()).toEqual([1, 2]);
    expect(card().classes()).toContain("msc--on");
  });

  it("forgets a tray pick that Ctrl took back out", async () => {
    const { wrapper, store } = await openTray();
    const member = () => wrapper.findAll(".msp__member")[0];

    await member().trigger("click");
    await member().trigger("click", { ctrlKey: true });
    expect([...store.selectedIds]).toEqual([]);
    await wrapper.find(".msg__row").trigger("click", { ctrlKey: true });

    expect([...store.selectedIds]).toEqual([1]);
    expect(wrapper.find('[data-testid="model-set-card"]').classes()).toContain(
      "msc--on",
    );
  });

  it("does not light another set's card when its checkpoint is picked in this tray", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5"), row(4, "juggernautXL_v9")],
      combinations: [
        combination("1,4", [CKPT, OTHER_CKPT]),
        combination("4", [OTHER_CKPT]),
      ],
    });
    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();

    const members = wrapper.findAll(".msp__member");
    const other = members.find((m) => m.attributes("data-key") === "4");
    await members[0].trigger("click");
    await other.trigger("click", { ctrlKey: true });

    expect([...store.selectedIds].sort()).toEqual([1, 4]);
    const cards = wrapper.findAll('[data-testid="model-set-card"]');
    expect(cards).toHaveLength(2);
    for (const card of cards) expect(card.classes()).not.toContain("msc--on");
  });

  it("toggles with Ctrl and takes a range with Shift, in DRAWN order", async () => {
    const { wrapper, store } = await openTray();

    const card = wrapper.find(".msg__row");
    const members = wrapper.findAll(".msp__member");

    await members[1].trigger("click");
    await members[2].trigger("click", { ctrlKey: true });
    expect([...store.selectedIds].sort()).toEqual([2, 3]);

    // A range from the LoRA back up to the card spans the drawn order - the
    // card, then the tray rows under it - and replaces the selection.
    await card.trigger("click");
    await members[2].trigger("click", { shiftKey: true });
    expect([...store.selectedIds].sort()).toEqual([1, 2, 3]);
  });

  it("gives the verb menu the pointer, having first selected what is under it", async () => {
    // The file-manager rule the row list already follows: right-clicking
    // something not selected selects it and acts on it alone.
    const { wrapper, store } = await openTray();

    await wrapper
      .findAll(".msp__member")[2]
      .trigger("contextmenu", { clientX: 120, clientY: 340 });

    expect([...store.selectedIds]).toEqual([3]);
    expect(wrapper.emitted("menu")[0][0]).toEqual({ x: 120, y: 340 });
  });

  it("leaves a selection of forty alone when one of them is right-clicked", async () => {
    const { wrapper, store } = await openTray();

    await wrapper.findAll(".msp__member")[1].trigger("click");
    await wrapper
      .findAll(".msp__member")[2]
      .trigger("click", { ctrlKey: true });
    await wrapper
      .findAll(".msp__member")[2]
      .trigger("contextmenu", { clientX: 1, clientY: 2 });

    expect([...store.selectedIds].sort()).toEqual([2, 3]);
    expect(wrapper.emitted("menu")).toHaveLength(1);
  });

  it("selects a model the Show narrowing hides from the row list", async () => {
    // A combination survives `Show` on any one visible member and is drawn
    // WHOLE, so a tray routinely lists files the row list is hiding. Untick
    // Adapters and the LoRA is still on screen - so it still has to be
    // selectable, and the verb bar still has to see it, or the click is a
    // silent no-op. `setGridModelIds` is what makes that true.
    const { wrapper, store } = await openTray();
    store.setFilters({ adapters: false });
    await wrapper.vm.$nextTick();

    expect(store.visibleRows.some((r) => r.id === 3)).toBe(false);
    await wrapper.findAll(".msp__member")[2].trigger("click");
    expect(store.selectedRows.map((r) => r.id)).toEqual([3]);
  });

  it("toggles with Space and extends with Shift+arrow, off the keyboard", async () => {
    const { wrapper, store } = await openTray();
    const grid = wrapper.find('[role="treegrid"]');

    // The cursor starts on the card. Space toggles it in, as it does on a row.
    await grid.trigger("keydown", { key: " " });
    expect([...store.selectedIds]).toEqual([1]);

    // The first Down lands on the head's OWN tray row - a set's head is drawn
    // twice while its tray is open, as the card and as the row marked *Names
    // this set* - so the range is still the one model. The second reaches the
    // VAE and extends it.
    await grid.trigger("keydown", { key: "ArrowDown", shiftKey: true });
    expect([...store.selectedIds]).toEqual([1]);
    await grid.trigger("keydown", { key: "ArrowDown", shiftKey: true });
    expect([...store.selectedIds].sort()).toEqual([1, 2]);
  });

  it("measures a Shift range from the clicked tray occurrence, not its card", async () => {
    // A occurs both as the first card and the first tray row. B and C are
    // visible cards between those occurrences, but were not in this range.
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "a"), row(2, "b"), row(3, "c")],
      support: [row(4, "d", "vae")],
      combinations: [
        combination("1,4", [
          member(1, "a", "checkpoint"),
          member(4, "d", "vae"),
        ]),
        combination("2", [member(2, "b", "checkpoint")]),
        combination("3", [member(3, "c", "checkpoint")]),
      ],
    });
    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();

    const members = wrapper.findAll(".msp__member");
    await members[0].trigger("click");
    await members[1].trigger("click", { shiftKey: true });

    expect([...store.selectedIds].sort()).toEqual([1, 4]);
  });

  it("renames what the cursor is on when F2 is pressed", async () => {
    // The verb menu advertises `F2` with a keycap, so it has to answer here.
    const { wrapper, store } = await openTray();
    const grid = wrapper.find('[role="treegrid"]');

    // Something else is selected: F2 still takes the model under the cursor.
    store.toggleSelected(3);
    await grid.trigger("keydown", { key: "ArrowDown" });
    await grid.trigger("keydown", { key: "ArrowDown" });
    await grid.trigger("keydown", { key: "F2" });

    expect([...store.selectedIds]).toEqual([2]);
    expect(wrapper.emitted("rename")).toHaveLength(1);
  });

  it("opens the verb menu from the Menu key and from Shift+F10", async () => {
    const { wrapper } = await openTray();
    const grid = wrapper.find('[role="treegrid"]');

    await grid.trigger("keydown", { key: "ContextMenu" });
    await grid.trigger("keydown", { key: "F10", shiftKey: true });
    expect(wrapper.emitted("menu")).toHaveLength(2);
  });

  it("lets Escape through to clear the selection once no tray is open", async () => {
    // One Escape, one thing undone: with a tray open the press closes it and is
    // STOPPED, so the shelf's window listener cannot also clear the selection
    // on the way out. With nothing open it is let through.
    const { wrapper, store } = await openTray();
    document.body.appendChild(wrapper.element);
    const grid = wrapper.find('[role="treegrid"]');
    const seen = [];
    const listen = (event) => seen.push(event.key);
    window.addEventListener("keydown", listen);
    try {
      await grid.trigger("keydown", { key: "Escape" });
      expect(store.openSetKey).toBe("");
      expect(seen).toEqual([]);

      await grid.trigger("keydown", { key: "Escape" });
      expect(seen).toEqual(["Escape"]);
    } finally {
      window.removeEventListener("keydown", listen);
    }
  });

  it("leaves the selection alone when the card's chevron is pressed", async () => {
    // ▸ is navigation: "show me what is in this set". Its click bubbles out of
    // the button and into the row, so unguarded it replaced the reader's
    // selection with that card - arming a model they never pointed at for a
    // verb that has no undo.
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5"), row(3, "filmgrain_xl")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [
        combination("1,2", [CKPT, VAE]),
        combination("4,2", [OTHER_CKPT, VAE]),
      ],
    });
    store.toggleSelected(2);
    store.toggleSelected(3);

    await wrapper.findAll(".msc__toggle")[0].trigger("click");

    expect(store.openSetKey).toBe("model:1");
    expect([...store.selectedIds].sort()).toEqual([2, 3]);
  });

  it("offers no selection for a model the shelf has no row for", async () => {
    // A combination can name a file from a block this session never fetched.
    // There is nothing to rename, move or delete for one of those, so the row
    // does not claim to be selectable and a click on it does nothing - better
    // than a tick the verb bar then cannot honour.
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      combinations: [
        combination("1,9", [CKPT, member(9, "never_fetched_vae", "vae")]),
      ],
    });
    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();

    const ghost = wrapper.findAll(".msp__member")[1];
    expect(ghost.attributes("data-key")).toBe("9");
    expect(ghost.attributes("aria-selected")).toBeUndefined();
    await ghost.trigger("click");
    await ghost.trigger("contextmenu", { clientX: 1, clientY: 1 });
    expect(store.selectedIds.size).toBe(0);
    expect(wrapper.emitted("menu")).toBeUndefined();
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
    // The grid is still there, holding only the New workflow set tile: making
    // a set by hand is what an owner with no recorded pictures can do (#1520).
    expect(wrapper.findAll(".msg__row")).toHaveLength(0);
    expect(wrapper.find('[data-testid="new-workflow-set"]').exists()).toBe(
      true,
    );
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

describe("a tray shows who a model is assigned to", () => {
  it("draws a person-assigned LoRA with that person's face, in both trays and both views", async () => {
    const assigned = {
      ...row(3, "filmgrain_xl"),
      attachments: [{ entity_type: "character", entity_id: 7 }],
    };
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), assigned],
      combinations: [combination("1,3", [CKPT, LORA])],
    });
    const face = (el) =>
      el
        .findAll("img")
        .some((img) =>
          img.attributes("src")?.includes("/characters/7/thumbnail"),
        );

    // The evidence tray, Grid view: the member card.
    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();
    const card = wrapper
      .findAll('[data-testid="model-set-member"]')
      .find((el) => el.text().includes("filmgrain_xl"));
    expect(face(card)).toBe(true);

    // ...and List view.
    store.setView({ trayView: "list" });
    await wrapper.vm.$nextTick();
    const listRow = wrapper.find('.msp__row[data-key="3"]');
    expect(face(listRow)).toBe(true);
  });

  it("does the same on a hand-made set's slot tile", async () => {
    const assigned = {
      ...row(3, "filmgrain_xl"),
      attachments: [{ entity_type: "character", entity_id: 7 }],
    };
    const member = {
      sha256: assigned.sha256,
      slot: "lora",
      label: "filmgrain_xl",
      on_shelf: true,
      id: 3,
      name: "filmgrain_xl",
      filename: "filmgrain_xl",
      kind: "adapter",
      base_model: null,
    };
    const { wrapper, store } = await mountGrid({
      rows: [assigned],
      handMade: [
        {
          id: 10,
          name: null,
          members: [member],
          covers: [],
          picture_count: 0,
          incomplete: true,
        },
      ],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();

    const tile = wrapper.find(`[data-key="m:${assigned.sha256}"]`);
    expect(
      tile
        .findAll("img")
        .some((img) =>
          img.attributes("src")?.includes("/characters/7/thumbnail"),
        ),
    ).toBe(true);
  });
});

describe("hand-made sets (#1520)", () => {
  /** A hand-made set as `GET /models/workflow-sets` serves it. */
  function handSet(id, members, extra = {}) {
    return {
      id,
      name: null,
      created_at: "2026-09-26T08:00:00Z",
      updated_at: "2026-09-26T08:00:00Z",
      incomplete: !members.some((m) => m.slot === "checkpoint"),
      checkpoint_id: members.find((m) => m.slot === "checkpoint")?.id ?? null,
      picture_count: 0,
      recipes: 0,
      covers: [],
      members,
      ...extra,
    };
  }

  function slotMember(id, name, slot, extra = {}) {
    return {
      sha256: String(id).repeat(64).slice(0, 64),
      slot,
      label: name,
      on_shelf: true,
      id,
      name,
      filename: name,
      kind: slot === "lora" ? "adapter" : slot,
      base_model: "SDXL",
      file_size: 1000,
      ...extra,
    };
  }

  const SET_CKPT = slotMember(1, "realvisXL_v5", "checkpoint");
  const SET_VAE = slotMember(2, "sdxl_vae", "vae");

  it("draws every hand-made card as grouped by you, and says when it has no checkpoint", async () => {
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), row(3, "filmgrain_xl")],
      handMade: [
        handSet(10, [SET_CKPT]),
        handSet(11, [slotMember(3, "filmgrain_xl", "lora")]),
      ],
    });

    const cards = wrapper.findAll('[data-testid="model-set-card"]');
    expect(cards).toHaveLength(2);
    for (const card of cards) {
      expect(card.attributes("aria-label")).toContain("grouped by you");
      expect(card.text()).toContain("Grouped by you");
    }
    // Named after its checkpoint, in the shelf's own name for it, and
    // "Untitled set" without one.
    expect(cards[0].attributes("aria-label")).toMatch(/^realvisXL v5/);
    expect(cards[1].attributes("aria-label")).toMatch(/^Untitled set/);
    expect(cards[0].text()).not.toContain("Incomplete");
    expect(cards[1].text()).toContain("Incomplete: no checkpoint");
    // The name says what is drawn: the warning, not the facts it replaces.
    expect(cards[1].attributes("aria-label")).not.toContain("no picture yet");
    // Honesty: no recipe count on a hand-made card.
    expect(cards[0].text()).not.toMatch(/recipe/);
  });

  it("draws a combination the set covers on the set and not again as evidence", async () => {
    const { wrapper } = await mountGrid({
      rows: [
        row(1, "realvisXL_v5", "checkpoint"),
        row(4, "juggernautXL_v9", "checkpoint"),
      ],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [
        { ...combination("1,2", [CKPT, VAE]), covered_by: [10] },
        combination("4", [OTHER_CKPT]),
      ],
      handMade: [handSet(10, [SET_CKPT, SET_VAE], { picture_count: 1 })],
    });

    const names = wrapper
      .findAll('[data-testid="model-set-card"]')
      .map((card) => card.attributes("aria-label").split(",")[0]);
    expect(names).toEqual(["realvisXL v5", "juggernautXL_v9"]);
  });

  it("selects the SET on a hand-made card, never a file, and deletes it with Delete and an Undo", async () => {
    deleteWorkflowSet.mockResolvedValue({
      deleted: handSet(10, [SET_CKPT], { name: "Portrait kit" }),
    });
    createWorkflowSet.mockResolvedValue(handSet(12, [SET_CKPT]));
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [SET_CKPT], { name: "Portrait kit" })],
    });
    const operations = useOperationStore();

    const card = wrapper.find(".msg__row");
    await card.trigger("click");
    expect([...store.selectedSetIds]).toEqual([10]);
    expect([...store.selectedIds]).toEqual([]);
    expect(card.attributes("aria-selected")).toBe("true");

    await wrapper
      .find('[role="treegrid"]')
      .trigger("keydown", { key: "Delete" });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(deleteWorkflowSet).toHaveBeenCalledWith(10);

    expect(operations.receipt.summary).toContain(
      'Deleted the set "Portrait kit"',
    );
    await operations.takeLocalReceiptAction();
    // The undo recreates the set from the snapshot, by hash and slot.
    expect(createWorkflowSet).toHaveBeenCalledWith({
      name: "Portrait kit",
      members: [
        { sha256: SET_CKPT.sha256, slot: "checkpoint", label: SET_CKPT.label },
      ],
    });
    // Redo deletes the set the undo made, which has a new id.
    expect(operations.receipt.mode).toBe("undone");
    await operations.takeLocalReceiptAction();
    expect(deleteWorkflowSet).toHaveBeenLastCalledWith(12);
  });

  it("makes a set from the keyboard with N and from the tile", async () => {
    createWorkflowSet.mockResolvedValue(handSet(12, []));
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      combinations: [combination("1", [CKPT])],
    });

    await wrapper.find('[role="treegrid"]').trigger("keydown", { key: "n" });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(createWorkflowSet).toHaveBeenCalledTimes(1);
    expect(store.openSetKey).toBe("hand:12");

    await wrapper.find('[data-testid="new-workflow-set"]').trigger("click");
    expect(createWorkflowSet).toHaveBeenCalledTimes(2);
  });

  it("walks the tray's slots and removes a member with Backspace, keeping the file", async () => {
    removeWorkflowSetMembers.mockResolvedValue({
      set: handSet(10, [SET_CKPT]),
      removed: [{ sha256: SET_VAE.sha256, slot: "vae", label: "sdxl_vae" }],
    });
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      support: [row(2, "sdxl_vae", "vae")],
      handMade: [handSet(10, [SET_CKPT, SET_VAE])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();

    const panel = wrapper.find('[data-testid="model-set-slots-panel"]');
    // Checkpoint holds its one and offers no ＋; the other four slots do.
    const addTiles = panel.findAll(".mss__tile--add");
    expect(addTiles.map((tile) => tile.attributes("aria-label"))).toEqual([
      "Add text encoder…",
      "Add VAE…",
      "Add LoRA…",
      "Add…",
    ]);

    const grid = wrapper.find('[role="treegrid"]');
    // Card → checkpoint tile → the text-encoder slot → the VAE slot's member.
    await grid.trigger("keydown", { key: "ArrowDown" });
    await grid.trigger("keydown", { key: "ArrowDown" });
    await grid.trigger("keydown", { key: "ArrowDown" });
    await grid.trigger("keydown", { key: "Backspace" });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(removeWorkflowSetMembers).toHaveBeenCalledWith(10, [SET_VAE.sha256]);
  });

  it("enters the tray at its first tile on Down from the header bar", async () => {
    removeWorkflowSetMembers.mockResolvedValue({
      set: handSet(10, [SET_VAE]),
      removed: [{ sha256: SET_CKPT.sha256, slot: "checkpoint" }],
    });
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      support: [row(2, "sdxl_vae", "vae")],
      handMade: [handSet(10, [SET_CKPT, SET_VAE])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();

    // The header's rows are slot tiles, not members; Down must still land.
    await wrapper.find(".msp__header button:not([disabled])").trigger("keydown", {
      key: "ArrowDown",
    });
    await wrapper.find('[role="treegrid"]').trigger("keydown", {
      key: "Backspace",
    });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(removeWorkflowSetMembers).toHaveBeenCalledWith(10, [
      SET_CKPT.sha256,
    ]);
  });

  it("undoes a create through the delete verb, so that undo has an Undo too", async () => {
    createWorkflowSet.mockResolvedValue(handSet(12, []));
    deleteWorkflowSet.mockResolvedValue({
      deleted: handSet(12, [SET_CKPT]),
    });
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
    });
    const operations = useOperationStore();

    await wrapper.find('[data-testid="new-workflow-set"]').trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    await operations.takeLocalReceiptAction();

    expect(deleteWorkflowSet).toHaveBeenCalledWith(12);
    // The delete's pill stays up, not the create's flipped to Redo.
    expect(operations.receipt.summary).toContain("Deleted the set");
    expect(operations.receipt.mode).toBe("did");
  });

  it("never says a create was undone when its delete failed", async () => {
    createWorkflowSet.mockResolvedValue(handSet(12, []));
    deleteWorkflowSet.mockRejectedValue(new Error("gone"));
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
    });
    const operations = useOperationStore();

    await wrapper.find('[data-testid="new-workflow-set"]').trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    await operations.takeLocalReceiptAction();

    // No "Undone" and so no Redo, which would make the set a second time.
    expect(operations.receipt).toBe(null);
    expect(createWorkflowSet).toHaveBeenCalledTimes(1);
  });

  it("keeps the receipt and Undo for the sets it did delete when one fails", async () => {
    deleteWorkflowSet.mockImplementation((id) =>
      id === 11
        ? Promise.reject(new Error("gone"))
        : Promise.resolve({ deleted: handSet(id, [SET_CKPT]) }),
    );
    const { store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [SET_CKPT]), handSet(11, [SET_CKPT])],
    });
    await store.deleteHandMadeSets(store.handMadeSets);

    const receipt = useOperationStore().receipt;
    expect(receipt.summary).toContain("1 could not be deleted");
    expect(receipt.local.undo).toBeTypeOf("function");
  });

  it("does not make a set per repeat of a held N", async () => {
    createWorkflowSet.mockResolvedValue(handSet(12, []));
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      combinations: [combination("1", [CKPT])],
    });
    const grid = wrapper.find('[role="treegrid"]');
    await grid.trigger("keydown", { key: "n", repeat: true });
    expect(createWorkflowSet).not.toHaveBeenCalled();
  });

  it("drops the set selection when the cursor enters a tray, and when the grid is left", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [SET_CKPT])],
    });
    await wrapper.find(".msg__row").trigger("click");
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    expect([...store.selectedSetIds]).toEqual([10]);

    await wrapper
      .find('[role="treegrid"]')
      .trigger("keydown", { key: "ArrowDown" });
    expect([...store.selectedSetIds]).toEqual([]);

    await wrapper.find(".msg__row").trigger("click");
    expect([...store.selectedSetIds]).toEqual([10]);
    store.setView({ groupBy: "none" });
    await wrapper.vm.$nextTick();
    expect([...store.selectedSetIds]).toEqual([]);
  });

  it("adds a model to a slot with ONE click, and stays open for the next", async () => {
    // `added` is the digests the server stored, as the route returns it.
    const shaOf = (modelId) => String(modelId).repeat(64).slice(0, 64);
    addWorkflowSetMembers.mockReset().mockImplementation((id, members) =>
      Promise.resolve({
        set: handSet(10, [SET_CKPT]),
        added: members.map((m) => shaOf(m.model_id)),
      }),
    );
    const { wrapper, store } = await mountGrid({
      rows: [
        row(1, "realvisXL_v5", "checkpoint"),
        row(3, "filmgrain_xl"),
        row(5, "Soft_Light"),
      ],
      handMade: [handSet(10, [SET_CKPT])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();

    const loraTile = wrapper
      .findAll(".mss__tile--add")
      .find((tile) => tile.attributes("aria-label") === "Add LoRA…");
    await loraTile.trigger("click");
    const chooser = wrapper.find('[data-testid="workflow-set-chooser"]');
    // No tick box and no Add button: the click on the row IS the add.
    expect(chooser.text()).not.toMatch(/\bAdd \d/);
    expect(chooser.findAll("button").map((b) => b.text())).not.toContain("Add");

    const option = (name) =>
      chooser.findAll('[role="option"]').find((o) => o.text().includes(name));
    await option("filmgrain_xl").trigger("click");
    await option("Soft_Light").trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));

    // Still open for the next pick.
    expect(wrapper.findComponent(WorkflowSetChooser).props("open")).toBe(true);
    expect(addWorkflowSetMembers.mock.calls).toEqual([
      [10, [{ model_id: 3, slot: "lora" }]],
      [10, [{ model_id: 5, slot: "lora" }]],
    ]);
    // Narrated in the popup's own status line, in the shelf's names, and no
    // pill yet: the adds are one receipt once the popup closes (#1574).
    const operations = useOperationStore();
    expect(operations.receipt).toBe(null);
    expect(chooser.find('[role="status"]').text()).toContain(
      "Added filmgrain xl and Soft Light",
    );
    await chooser.trigger("keydown", { key: "Escape" });
    expect(operations.receipt.summary).toBe('Added 2 LoRAs to "realvisXL v5"');

    removeWorkflowSetMembers.mockReset().mockResolvedValue({ removed: [] });
    await operations.takeLocalReceiptAction();
    expect(removeWorkflowSetMembers).toHaveBeenCalledWith(10, [
      shaOf(3),
      shaOf(5),
    ]);
  });

  it("takes back the last pick from the popup's own Undo, without a pill", async () => {
    const shaOf = (modelId) => String(modelId).repeat(64).slice(0, 64);
    addWorkflowSetMembers.mockReset().mockImplementation((id, members) =>
      Promise.resolve({
        set: handSet(10, [SET_CKPT]),
        added: members.map((m) => shaOf(m.model_id)),
      }),
    );
    removeWorkflowSetMembers
      .mockReset()
      .mockImplementation((id, shas) => Promise.resolve({ removed: shas }));
    const { wrapper, store } = await mountGrid({
      rows: [
        row(1, "realvisXL_v5", "checkpoint"),
        row(3, "filmgrain_xl"),
        row(5, "Soft_Light"),
      ],
      handMade: [handSet(10, [SET_CKPT])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    await wrapper
      .findAll(".mss__tile--add")
      .find((tile) => tile.attributes("aria-label") === "Add LoRA…")
      .trigger("click");
    const chooser = wrapper.find('[data-testid="workflow-set-chooser"]');
    const field = chooser.find("input");
    // Enter with no arrow used adds the top match.
    await field.trigger("keydown", { key: "Enter" });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(addWorkflowSetMembers).toHaveBeenCalledWith(10, [
      { model_id: 3, slot: "lora" },
    ]);

    const undo = chooser
      .find('[role="status"]')
      .findAll("button")
      .find((b) => b.text() === "Undo");
    await undo.trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(removeWorkflowSetMembers).toHaveBeenCalledWith(10, [shaOf(3)]);
    expect(chooser.find('[role="status"]').text()).toContain(
      "Took out filmgrain xl",
    );
    // Nothing left to announce once it closes.
    await chooser.trigger("keydown", { key: "Escape" });
    expect(useOperationStore().receipt).toBe(null);
  });

  it("closes the Checkpoint popup on its one pick and lands on the new checkpoint", async () => {
    const CKPT_ROW = row(1, "realvisXL_v5", "checkpoint");
    addWorkflowSetMembers.mockReset().mockResolvedValue({
      set: handSet(10, [SET_CKPT]),
      added: [SET_CKPT.sha256],
    });
    const { wrapper, store } = await mountGrid({
      rows: [CKPT_ROW],
      handMade: [handSet(10, [])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    // The refetch after the add serves the set with its checkpoint.
    fetchWorkflowSets.mockResolvedValue({
      combinations: [],
      no_set: [],
      hand_made: [handSet(10, [SET_CKPT])],
    });

    await wrapper.find(".mss__tile--add").trigger("click");
    await wrapper
      .find('[data-testid="workflow-set-chooser"] [role="option"]')
      .trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    await wrapper.vm.$nextTick();

    expect(addWorkflowSetMembers).toHaveBeenCalledWith(10, [
      { model_id: 1, slot: "checkpoint" },
    ]);
    expect(wrapper.findComponent(WorkflowSetChooser).props("open")).toBe(false);
    const tile = wrapper.find(`[data-key="m:${SET_CKPT.sha256}"]`);
    expect(tile.attributes("tabindex")).toBe("0");
  });

  it("offers once to set the base model of a checkpoint that has none", async () => {
    const bare = slotMember(1, "realvisXL_v5", "checkpoint", {
      base_model: null,
    });
    const ckptRow = {
      ...row(1, "realvisXL_v5", "checkpoint"),
      base_model_canonical: "SDXL",
      base_model_source: "filename_fuzzy",
    };
    addWorkflowSetMembers.mockReset().mockResolvedValue({
      set: handSet(10, [bare]),
      added: [bare.sha256],
    });
    const { wrapper, store } = await mountGrid({
      rows: [ckptRow],
      handMade: [handSet(10, [])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    fetchWorkflowSets.mockResolvedValue({
      combinations: [],
      no_set: [],
      hand_made: [handSet(10, [bare])],
    });
    const { editModels } = await import("../../api/modelShelf");
    editModels.mockResolvedValue({ updated: [1] });

    await wrapper.find(".mss__tile--add").trigger("click");
    await wrapper
      .find('[data-testid="workflow-set-chooser"] [role="option"]')
      .trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    await wrapper.vm.$nextTick();

    const offer = wrapper.find('[data-testid="base-model-offer"]');
    expect(offer.text()).toContain("realvisXL v5 has no base model");
    // A stray `>` once rendered as text after the hint's closing tag.
    expect(offer.text()).not.toContain(">");
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    // Pre-filled from the shelf's own guess, never stored until confirmed.
    const set = offer.findAll("button").find((b) => b.text() === "Set SDXL");
    expect(set).toBeTruthy();
    await set.trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(editModels).toHaveBeenCalledWith([1], { base_model: "SDXL" });
    // `set-base` is a declared event, not one Vue warns about on every press.
    expect(
      warn.mock.calls.some((call) => String(call[0]).includes("set-base")),
    ).toBe(false);
    warn.mockRestore();
    expect(wrapper.find('[data-testid="base-model-offer"]').exists()).toBe(
      false,
    );
  });

  it("does not offer again once dismissed, nor for a checkpoint that has a base model", async () => {
    const bare = slotMember(1, "realvisXL_v5", "checkpoint", {
      base_model: null,
    });
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [bare])],
    });
    store.toggleSet("hand:10");
    store.checkpointAdded = { setId: 10, modelId: 1 };
    await wrapper.vm.$nextTick();
    const offer = wrapper.find('[data-testid="base-model-offer"]');
    await offer
      .findAll("button")
      .find((b) => b.text() === "Not now")
      .trigger("click");
    expect(wrapper.find('[data-testid="base-model-offer"]').exists()).toBe(
      false,
    );

    // An offer left unanswered goes when its tray closes, and does not come
    // back when the tray reopens.
    store.checkpointAdded = { setId: 10, modelId: 1 };
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-testid="base-model-offer"]').exists()).toBe(
      true,
    );
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-testid="base-model-offer"]').exists()).toBe(
      false,
    );

    // With no guess the field is empty and Set still answers: it says what
    // is missing instead of greying out with no reason.
    store.checkpointAdded = { setId: 10, modelId: 1 };
    await wrapper.vm.$nextTick();
    const empty = wrapper.find('[data-testid="base-model-offer"]');
    expect(empty.find("input").attributes("placeholder")).toContain(
      "Type a base model",
    );
    const setButton = empty.findAll("button").find((b) => b.text() === "Set");
    expect(setButton.attributes("disabled")).toBeUndefined();
    await setButton.trigger("click");
    expect(empty.text()).toContain("Type or pick a base model first.");
    const { editModels } = await import("../../api/modelShelf");
    expect(editModels).not.toHaveBeenCalled();
    store.checkpointAdded = null;
    await wrapper.vm.$nextTick();

    // A checkpoint that already has one is never asked about.
    store.checkpointAdded = { setId: 10, modelId: 1 };
    store.workflowSets = {
      ...store.workflowSets,
      handMade: [handSet(10, [SET_CKPT])],
    };
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-testid="base-model-offer"]').exists()).toBe(
      false,
    );
  });

  it("calls a set and its members what the shelf calls them, never the filename", async () => {
    const onShelf = slotMember(1, "RealVisXL_V5.safetensors", "checkpoint", {
      filename: "RealVisXL_V5.safetensors",
    });
    const gone = slotMember(5, "Film_Grain_XL.safetensors", "lora", {
      on_shelf: false,
      id: null,
    });
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "RealVisXL_V5.safetensors", "checkpoint")],
      handMade: [handSet(10, [onShelf, gone])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();

    const card = wrapper.find('[data-testid="model-set-card"]');
    expect(card.attributes("aria-label")).toMatch(/^RealVisXL V5,/);
    const panel = wrapper.find('[data-testid="model-set-slots-panel"]');
    expect(panel.text()).not.toContain(".safetensors");
    expect(panel.text()).toContain("Film Grain XL");
  });

  it("says a checkpoint left the shelf, not that there is none", async () => {
    const gone = slotMember(1, "realvisXL_v5", "checkpoint", {
      on_shelf: false,
      id: null,
    });
    const { wrapper, store } = await mountGrid({
      rows: [],
      handMade: [handSet(10, [gone], { incomplete: false })],
    });
    const card = wrapper.find('[data-testid="model-set-card"]');
    expect(card.text()).toContain("Checkpoint not on shelf");
    expect(card.text()).not.toContain("No checkpoint yet");
    expect(card.attributes("aria-label")).toContain("checkpoint not on shelf");

    // Not stuck: the off-shelf checkpoint's tile keeps its Remove, which is
    // what frees the slot for a replacement.
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    const tile = wrapper.find(`[data-key="m:${gone.sha256}"]`);
    expect(tile.find(".mss__remove").exists()).toBe(true);
  });

  it("hands the shelf the card to refocus when the set menu closes", async () => {
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [SET_CKPT])],
    });
    const card = wrapper.find(".msg__row");
    await card.trigger("contextmenu", { clientX: 5, clientY: 6 });
    const [{ x, y, el }] = wrapper.emitted("set-menu")[0];
    expect([x, y]).toEqual([5, 6]);
    expect(el).toBe(card.element);
  });

  it("counts a whole section in its heading, not the rows drawn before Show all", async () => {
    const loras = Array.from({ length: 10 }, (_, i) =>
      row(20 + i, `lora_${i}`),
    );
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), ...loras],
      handMade: [handSet(10, [SET_CKPT])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    await wrapper
      .findAll(".mss__tile--add")
      .find((tile) => tile.attributes("aria-label") === "Add LoRA…")
      .trigger("click");

    const chooser = wrapper.find('[data-testid="workflow-set-chooser"]');
    // Eight drawn, ten in the section. The one group is the other base
    // models, so it is drawn open rather than folded to a single line.
    expect(chooser.findAll('[role="option"]')).toHaveLength(8);
    const heading = chooser
      .findAll(".wsc__gh")
      .find((h) => h.text().includes("Other base models"));
    expect(heading.find(".fm-n").text()).toBe("10");
  });

  it("credits a pick only with what the server stored", async () => {
    // The set already held it: the route stores nothing and says so.
    addWorkflowSetMembers
      .mockReset()
      .mockResolvedValue({ set: handSet(10, [SET_CKPT]), added: [] });
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), row(3, "filmgrain_xl")],
      handMade: [handSet(10, [SET_CKPT])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    await wrapper
      .findAll(".mss__tile--add")
      .find((tile) => tile.attributes("aria-label") === "Add LoRA…")
      .trigger("click");
    const chooser = wrapper.find('[data-testid="workflow-set-chooser"]');
    await chooser.find('[role="option"]').trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));

    const status = chooser.find('[role="status"]');
    expect(status.text()).toContain("Already in this set, nothing added");
    expect(status.findAll("button")).toHaveLength(0);
    await chooser.trigger("keydown", { key: "Escape" });
    expect(useOperationStore().receipt).toBe(null);
  });

  it("names a filter's only matches as already in the set, not as nothing", async () => {
    const held = slotMember(3, "filmgrain_xl", "lora");
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), row(3, "filmgrain_xl")],
      handMade: [handSet(10, [SET_CKPT, held])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    await wrapper
      .findAll(".mss__tile--add")
      .find((tile) => tile.attributes("aria-label") === "Add LoRA…")
      .trigger("click");
    const chooser = wrapper.find('[data-testid="workflow-set-chooser"]');
    await chooser.find("input").setValue("grain");

    expect(chooser.find(".wsc__empty").exists()).toBe(false);
    expect(chooser.find(".wsc__gone").text()).toBe(
      "filmgrain xl also matches, and is already in this set.",
    );
  });

  it("adds every ticked row of a checklist, not only the ones drawn before Show more", async () => {
    const loras = Array.from({ length: 10 }, (_, i) =>
      row(20 + i, `lora_${i}`),
    );
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), ...loras],
      combinations: [
        combination("1,20", [
          CKPT,
          ...loras.map((r) => member(r.id, r.filename, "adapter")),
        ]),
      ],
      handMade: [handSet(10, [SET_CKPT])],
    });
    addWorkflowSetMembers
      .mockReset()
      .mockResolvedValue({ set: handSet(10, [SET_CKPT]), added: [] });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    await wrapper
      .findAll("button")
      .find((b) => b.text().includes("Fill from pictures"))
      .trigger("click");
    const chooser = wrapper.find('[data-testid="workflow-set-chooser"]');
    // Eight drawn, ten ticked: Add counts and adds all ten.
    expect(chooser.findAll('[role="option"]')).toHaveLength(8);
    const add = chooser.findAll("button").find((b) => b.text() === "Add 10");
    expect(add).toBeTruthy();
    await add.trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(addWorkflowSetMembers.mock.calls[0][1]).toHaveLength(10);
  });

  it("adds nothing from a checklist on Enter once every row is unticked", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), row(3, "filmgrain_xl")],
      combinations: [combination("1,3", [CKPT, LORA])],
      handMade: [handSet(10, [SET_CKPT])],
    });
    addWorkflowSetMembers.mockReset();
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    await wrapper
      .findAll("button")
      .find((b) => b.text().includes("Fill from pictures"))
      .trigger("click");
    const chooser = wrapper.find('[data-testid="workflow-set-chooser"]');
    // A checklist marks no row with ⏎: Enter adds what is ticked.
    expect(chooser.find(".wsc__enter").exists()).toBe(false);
    const option = chooser.find('[role="option"]');
    expect(option.attributes("aria-selected")).toBe("true");
    await option.trigger("click");
    expect(option.attributes("aria-selected")).toBe("false");

    const field = chooser.find("input");
    await field.trigger("keydown", { key: "Enter" });
    // Nor with the cursor put down on that unticked row.
    await field.trigger("keydown", { key: "ArrowDown" });
    expect(chooser.find(".wsc__r--cur").exists()).toBe(true);
    await field.trigger("keydown", { key: "Enter" });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(addWorkflowSetMembers).not.toHaveBeenCalled();
  });

  it("folds the other base models to one line under the ranked groups", async () => {
    const sdxl = (id, name) => ({ ...row(id, name), base_model: "SDXL" });
    const { wrapper, store } = await mountGrid({
      rows: [
        row(1, "realvisXL_v5", "checkpoint"),
        sdxl(3, "filmgrain_xl"),
        row(20, "flux_lora_a"),
        row(21, "flux_lora_b"),
      ],
      handMade: [handSet(10, [SET_CKPT])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    await wrapper
      .findAll(".mss__tile--add")
      .find((tile) => tile.attributes("aria-label") === "Add LoRA…")
      .trigger("click");
    const chooser = wrapper.find('[data-testid="workflow-set-chooser"]');

    // Empty groups are not drawn at all.
    expect(chooser.findAll(".wsc__gh").map((h) => h.text())).toEqual([
      "SDXL LoRAs1",
    ]);
    expect(chooser.findAll('[role="option"]')).toHaveLength(1);
    const fold = chooser.find(".wsc__more");
    expect(fold.text()).toBe("2 LoRAs for other base models");

    // Down off the last row unfolds it, as the button would.
    const field = chooser.find("input");
    await field.trigger("keydown", { key: "ArrowDown" });
    await field.trigger("keydown", { key: "ArrowDown" });
    expect(chooser.findAll('[role="option"]')).toHaveLength(3);
    expect(chooser.find(".wsc__r--cur").text()).toContain("flux_lora_a");
  });

  it("keeps what is typed in the base-model field across a refetch", async () => {
    const bare = slotMember(1, "realvisXL_v5", "checkpoint", {
      base_model: null,
    });
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [bare])],
    });
    store.toggleSet("hand:10");
    store.checkpointAdded = { setId: 10, modelId: 1 };
    await wrapper.vm.$nextTick();
    const field = wrapper.find('[data-testid="base-model-offer"] input');
    await field.setValue("Pony");

    await store.loadWorkflowSets({ force: true });
    await wrapper.vm.$nextTick();

    expect(
      wrapper.find('[data-testid="base-model-offer"] input').element.value,
    ).toBe("Pony");
  });

  it("gives focus back to the Fill button when its checklist closes", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), row(3, "filmgrain_xl")],
      combinations: [combination("1,3", [CKPT, LORA])],
      handMade: [handSet(10, [SET_CKPT])],
      attach: true,
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    const fill = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Fill from pictures"));
    await fill.trigger("click");
    await wrapper
      .find('[data-testid="workflow-set-chooser"]')
      .trigger("keydown", { key: "Escape" });
    await wrapper.vm.$nextTick();

    expect(document.activeElement).toBe(fill.element);
    wrapper.unmount();
  });

  it("offers the base model for a checkpoint the SERVER stored, whatever the request said", async () => {
    const bare = slotMember(1, "realvisXL_v5", "checkpoint", {
      base_model: null,
    });
    const { store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [])],
    });
    // No slot in the request: the server placed it in Checkpoint.
    addWorkflowSetMembers.mockResolvedValue({
      set: handSet(10, [bare]),
      added: [bare.sha256],
    });
    await store.addToHandMadeSet(store.handMadeSets[0], [{ model_id: 1 }]);
    expect(store.checkpointAdded).toEqual({ setId: 10, modelId: 1 });

    // Asked for, but nothing was stored: no offer.
    store.checkpointAdded = null;
    addWorkflowSetMembers.mockResolvedValue({
      set: handSet(10, []),
      added: [],
    });
    await store.addToHandMadeSet(store.handMadeSets[0], [
      { model_id: 1, slot: "checkpoint" },
    ]);
    expect(store.checkpointAdded).toBe(null);
  });

  it("says nothing matched, not that a group is empty, while filtering", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), row(3, "filmgrain_xl")],
      handMade: [handSet(10, [SET_CKPT])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    await wrapper
      .findAll(".mss__tile--add")
      .find((tile) => tile.attributes("aria-label") === "Add LoRA…")
      .trigger("click");
    const chooser = wrapper.find('[data-testid="workflow-set-chooser"]');
    await chooser.find("input").setValue("zzz-nothing");

    // No stack of empty headings: one line saying the filter found nothing.
    expect(chooser.findAll(".wsc__gh")).toHaveLength(0);
    expect(chooser.find(".wsc__empty").text()).toBe(
      'Nothing matches "zzz-nothing".',
    );
  });

  it("reveals a cut section from the keyboard by arrowing past its last row", async () => {
    const loras = Array.from({ length: 10 }, (_, i) =>
      row(20 + i, `lora_${i}`),
    );
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), ...loras],
      handMade: [handSet(10, [SET_CKPT])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    await wrapper
      .findAll(".mss__tile--add")
      .find((tile) => tile.attributes("aria-label") === "Add LoRA…")
      .trigger("click");
    const chooser = wrapper.find('[data-testid="workflow-set-chooser"]');
    const field = chooser.find("input");
    expect(chooser.findAll('[role="option"]')).toHaveLength(8);

    // No cursor until an arrow puts one down: the first ↓ lands on lora_0.
    expect(chooser.find(".wsc__r--cur").exists()).toBe(false);
    for (let i = 0; i < 9; i += 1) {
      await field.trigger("keydown", { key: "ArrowDown" });
    }

    expect(chooser.findAll('[role="option"]')).toHaveLength(10);
    expect(chooser.find(".wsc__r--cur").text()).toContain("lora_8");
  });

  it("lands on the open set's card when the tile a chooser came from is gone", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [])],
      attach: true,
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    await wrapper.find(".mss__tile--add").trigger("click");
    // The Checkpoint ＋ goes away underneath the open chooser.
    store.workflowSets = {
      ...store.workflowSets,
      handMade: [handSet(10, [SET_CKPT])],
    };
    await wrapper.vm.$nextTick();
    await wrapper
      .find('[data-testid="workflow-set-chooser"]')
      .trigger("keydown", { key: "Escape" });
    await wrapper.vm.$nextTick();

    expect(document.activeElement).toBe(wrapper.find(".msg__row").element);
    wrapper.unmount();
  });

  it("says how many sets an undo of a delete could not bring back", async () => {
    deleteWorkflowSet.mockImplementation((id) =>
      Promise.resolve({ deleted: handSet(id, [SET_CKPT], { name: `S${id}` }) }),
    );
    createWorkflowSet.mockImplementation(({ name }) =>
      name === "S11"
        ? Promise.reject(new Error("refused"))
        : Promise.resolve(handSet(12, [])),
    );
    const { store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [SET_CKPT]), handSet(11, [SET_CKPT])],
    });
    const { useNoticeStore } = await import("../../stores/useNoticeStore");
    const notices = useNoticeStore();

    await store.deleteHandMadeSets(store.handMadeSets);
    const operations = useOperationStore();
    await operations.takeLocalReceiptAction();

    expect(createWorkflowSet).toHaveBeenCalledTimes(2);
    // The one that came back can still be redone away.
    expect(operations.receipt.mode).toBe("undone");
    await operations.takeLocalReceiptAction();
    expect(deleteWorkflowSet).toHaveBeenLastCalledWith(12);
    expect(notices.notices.some((n) => n.text.includes("1 of 2 sets"))).toBe(
      true,
    );
  });

  it("narrates set edits on the grid's receipt pill: one at a time, Undo then Redo", async () => {
    const loras = [3, 4, 5, 6, 7].map((id) =>
      slotMember(id, `lora_${id}`, "lora"),
    );
    const { store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), row(3, "filmgrain_xl")],
      handMade: [handSet(10, [SET_CKPT])],
    });
    const { useNoticeStore } = await import("../../stores/useNoticeStore");
    const notices = useNoticeStore();
    const operations = useOperationStore();

    for (const lora of loras) {
      addWorkflowSetMembers.mockResolvedValueOnce({
        set: handSet(10, [SET_CKPT, lora]),
        added: [lora.sha256],
      });
      await store.addToHandMadeSet(store.handMadeSets[0], [{ model_id: 3 }]);
    }
    expect(notices.notices).toHaveLength(0);
    expect(operations.receipt.summary).toBe('Added 1 model to "realvisXL v5"');
    // Replaced, never counted: on the grid "+N" means Undo takes all N back.
    expect(operations.receipt.mergedCount).toBe(0);
    expect(operations.receipt.durationMs).toBe(5000);

    // A repeat that added nothing has no Undo: a plain notice, pill untouched.
    const pill = operations.receipt;
    addWorkflowSetMembers.mockResolvedValueOnce({
      set: handSet(10, [SET_CKPT]),
      added: [],
    });
    await store.addToHandMadeSet(store.handMadeSets[0], [{ model_id: 3 }]);
    expect(notices.notices).toHaveLength(1);
    expect(operations.receipt).toBe(pill);

    // Undo is the last add's, not the burst's, and flips the pill to Redo.
    removeWorkflowSetMembers.mockResolvedValue({ removed: [] });
    await operations.takeLocalReceiptAction();
    expect(removeWorkflowSetMembers).toHaveBeenCalledTimes(1);
    expect(removeWorkflowSetMembers).toHaveBeenCalledWith(10, [
      loras.at(-1).sha256,
    ]);
    expect(operations.receipt.mode).toBe("undone");
    addWorkflowSetMembers.mockResolvedValueOnce({
      set: handSet(10, [SET_CKPT, loras[0]]),
      added: [loras[0].sha256],
    });
    await operations.takeLocalReceiptAction();
    expect(addWorkflowSetMembers).toHaveBeenLastCalledWith(10, [
      { model_id: 3 },
    ]);
    expect(operations.receipt.mode).toBe("did");

    // A delete replaces the pill, with the longer window and its own Undo.
    deleteWorkflowSet.mockResolvedValue({ deleted: handSet(10, [SET_CKPT]) });
    await store.deleteHandMadeSets(store.handMadeSets);
    expect(operations.receipt.summary).toContain("Deleted the set");
    expect(operations.receipt.durationMs).toBe(8000);
    expect(operations.receipt.mergedCount).toBe(0);
  });

  it("deletes a focused set on Backspace too, the Mac keyboard's Delete key", async () => {
    deleteWorkflowSet.mockResolvedValue({ deleted: handSet(10, [SET_CKPT]) });
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [SET_CKPT])],
    });
    await wrapper.find(".msg__row").trigger("click");
    await wrapper
      .find('[role="treegrid"]')
      .trigger("keydown", { key: "Backspace" });
    await new Promise((resolve) => setTimeout(resolve, 0));
    // Deleting a set touches no file, and its receipt offers Undo.
    expect(deleteWorkflowSet).toHaveBeenCalledWith(10);
  });

  it("does not delete again on a held Delete", async () => {
    deleteWorkflowSet.mockResolvedValue({ deleted: handSet(10, [SET_CKPT]) });
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [SET_CKPT])],
    });
    await wrapper.find(".msg__row").trigger("click");
    await wrapper
      .find('[role="treegrid"]')
      .trigger("keydown", { key: "Delete", repeat: true });
    expect(deleteWorkflowSet).not.toHaveBeenCalled();
  });

  it("returns focus to a Fill button even when the cursor rested on a ＋ tile", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), row(3, "filmgrain_xl")],
      combinations: [combination("1,3", [CKPT, LORA])],
      handMade: [handSet(10, [SET_CKPT])],
      attach: true,
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    const grid = wrapper.find('[role="treegrid"]');
    // Card, then the checkpoint tile, then the text-encoder slot's ＋ tile.
    await grid.trigger("keydown", { key: "ArrowDown" });
    await grid.trigger("keydown", { key: "ArrowDown" });
    expect(document.activeElement?.dataset.key).toBe("add:text_encoder");

    const fill = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Fill from pictures"));
    await fill.trigger("click");
    await wrapper
      .find('[data-testid="workflow-set-chooser"]')
      .trigger("keydown", { key: "Escape" });
    await wrapper.vm.$nextTick();

    expect(document.activeElement).toBe(fill.element);
    wrapper.unmount();
  });

  it("keeps what is typed when the guess changes under an open offer", async () => {
    const bare = slotMember(1, "realvisXL_v5", "checkpoint", {
      base_model: null,
    });
    const { wrapper, store } = await mountGrid({
      rows: [
        {
          ...row(1, "realvisXL_v5", "checkpoint"),
          base_model_canonical: "SDXL",
          base_model_source: "filename_fuzzy",
        },
      ],
      handMade: [handSet(10, [bare])],
    });
    store.toggleSet("hand:10");
    store.checkpointAdded = { setId: 10, modelId: 1 };
    await wrapper.vm.$nextTick();
    await wrapper
      .find('[data-testid="base-model-offer"] input')
      .setValue("Pony");

    store.rows[0].base_model_canonical = "Illustrious";
    await wrapper.vm.$nextTick();

    expect(
      wrapper.find('[data-testid="base-model-offer"] input').element.value,
    ).toBe("Pony");
  });

  it("puts focus in the popup's filter once it has mounted", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), row(3, "filmgrain_xl")],
      handMade: [handSet(10, [SET_CKPT])],
      attach: true,
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    await wrapper
      .findAll(".mss__tile--add")
      .find((tile) => tile.attributes("aria-label") === "Add LoRA…")
      .trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(document.activeElement).toBe(
      wrapper.find('[data-testid="workflow-set-chooser"] input').element,
    );
    wrapper.unmount();
  });

  it("enters a hand-made tray at its first tile from any column of a wide grid", async () => {
    // Four columns: the hand-made card sits in the second (the New tile is
    // first), so column arithmetic alone would land on the tray's SECOND tile.
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor() {}
        observe(element) {
          Object.defineProperty(element, "clientWidth", {
            configurable: true,
            value: 1000,
          });
        }
        disconnect() {}
      },
    );
    try {
      const { wrapper, store } = await mountGrid({
        rows: [row(1, "realvisXL_v5", "checkpoint")],
        support: [row(2, "sdxl_vae", "vae")],
        handMade: [handSet(10, [SET_CKPT, SET_VAE])],
        attach: true,
      });
      store.toggleSet("hand:10");
      await wrapper.vm.$nextTick();
      await wrapper.find(".msg__row").trigger("click");

      await wrapper
        .find('[role="treegrid"]')
        .trigger("keydown", { key: "ArrowDown" });
      await wrapper.vm.$nextTick();

      expect(document.activeElement?.dataset.key).toBe(`m:${SET_CKPT.sha256}`);
      wrapper.unmount();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("does not light a checkpoint's evidence card for a pick in a hand-made tray", async () => {
    // #1589's rule, carried to the slots tray: the pick is drawn on the tile
    // the reader clicked, not on a card elsewhere that happens to share it.
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), row(3, "filmgrain_xl")],
      combinations: [combination("1,3", [CKPT, LORA])],
      handMade: [handSet(10, [SET_CKPT])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();

    await wrapper.find(`[data-key="m:${SET_CKPT.sha256}"]`).trigger("click");
    await wrapper.vm.$nextTick();

    expect([...store.selectedIds]).toEqual([1]);
    const evidence = wrapper.find('.msg__row[data-key="model:1"]');
    expect(evidence.attributes("aria-selected")).toBe("false");
  });

  it("marks a member whose file left the shelf, and does not let it be selected", async () => {
    const gone = slotMember(5, "old_lora", "lora", {
      on_shelf: false,
      id: null,
    });
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [SET_CKPT, gone])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();

    const tile = wrapper.find(".mss__tile--gone");
    expect(tile.text()).toContain("Not on shelf");
    expect(tile.attributes("aria-selected")).toBeUndefined();
    await tile.trigger("click");
    expect([...store.selectedIds]).toEqual([]);
  });

  describe("the merge offer (#1523)", () => {
    const GHOST_LORA = {
      id: 3,
      sha256: "3".repeat(64),
      name: "filmgrain_xl",
      kind: "adapter",
      slot: "lora",
      picture_count: 812,
      recipes: 4,
    };
    const GHOST_OTHER = {
      id: 5,
      sha256: "5".repeat(64),
      name: "ultrasharp",
      kind: "unknown",
      slot: "other",
      picture_count: 1204,
      recipes: 6,
    };
    const OFFER = {
      head_id: 1,
      head_name: "realvisXL_v5",
      picture_count: 1204,
      recipes: 6,
      covers: [{ picture_id: 7, version: "v7" }],
      models: [GHOST_LORA, GHOST_OTHER],
    };

    async function mountOffer(extra = {}, { attach = false } = {}) {
      return mountGrid({
        attach,
        rows: [
          row(1, "realvisXL_v5", "checkpoint"),
          row(3, "filmgrain_xl"),
          row(5, "ultrasharp", "unknown"),
        ],
        handMade: [
          handSet(10, [SET_CKPT], { name: "Portrait kit", offer: OFFER, ...extra }),
        ],
      });
    }

    it("puts a lasting offer on the card that opens the tray on its strip", async () => {
      const { wrapper, store } = await mountOffer();
      const card = wrapper.find('[data-testid="model-set-card"]');
      expect(card.find('[data-testid="merge-offer"]').text()).toContain(
        "1204 pictures need 2 more",
      );
      expect(card.text()).toContain("+2");
      expect(card.attributes("aria-label")).toContain("merge offered");

      await card.find('[data-testid="merge-offer"]').trigger("click");
      await new Promise((resolve) => setTimeout(resolve, 0));
      await wrapper.vm.$nextTick();
      expect(store.openSetKey).toBe("hand:10");
      const strip = wrapper.find('[data-testid="merge-offer-strip"]');
      expect(strip.attributes("tabindex")).toBe("0");
      expect(strip.text()).toContain("Merge, add 2");
      // The ghosts stand in the slots they would land in, with their counts.
      const ghosts = wrapper.findAll(".mss__tile--ghost");
      expect(ghosts.map((g) => g.attributes("data-key"))).toEqual([
        `g:${GHOST_LORA.sha256}`,
        `g:${GHOST_OTHER.sha256}`,
      ]);
      expect(ghosts[0].text()).toContain("812 pictures");
    });

    it("merges on Enter at the strip, with a receipt that counts who joined and an Undo", async () => {
      addWorkflowSetMembers.mockResolvedValue({
        set: handSet(10, [SET_CKPT], { picture_count: 1204 }),
        added: [GHOST_LORA.sha256, GHOST_OTHER.sha256],
      });
      const { wrapper, store } = await mountOffer();
      const operations = useOperationStore();
      store.toggleSet("hand:10");
      await wrapper.vm.$nextTick();

      const grid = wrapper.find('[role="treegrid"]');
      await grid.trigger("keydown", { key: "ArrowDown" });
      await grid.trigger("keydown", { key: "Enter" });
      await new Promise((resolve) => setTimeout(resolve, 0));

      expect(addWorkflowSetMembers).toHaveBeenCalledWith(10, [
        { model_id: 3, slot: "lora" },
        { model_id: 5, slot: "other" },
      ]);
      expect(operations.receipt.summary).toContain(
        'Added 2 models to "Portrait kit"',
      );
      expect(operations.receipt.summary).toContain("1204 pictures joined it");
      await operations.takeLocalReceiptAction();
      expect(removeWorkflowSetMembers).toHaveBeenCalledWith(10, [
        GHOST_LORA.sha256,
        GHOST_OTHER.sha256,
      ]);
    });

    it("keeps one ghost out with Delete, never reaching the file delete", async () => {
      setWorkflowSetDeclines.mockResolvedValue({
        set: handSet(10, [SET_CKPT]),
        previous: ["9".repeat(64)],
      });
      // Attached, so a press the grid let through would reach the window.
      const { wrapper, store } = await mountOffer(
        { declined: ["9".repeat(64)] },
        { attach: true },
      );
      store.toggleSet("hand:10");
      await wrapper.vm.$nextTick();

      const grid = wrapper.find('[role="treegrid"]');
      // Card → strip → checkpoint → text encoders' ＋ → VAE's ＋ → LoRA ghost.
      for (let i = 0; i < 5; i += 1) {
        await grid.trigger("keydown", { key: "ArrowDown" });
      }
      const onWindowKey = vi.fn();
      window.addEventListener("keydown", onWindowKey);
      await grid.trigger("keydown", { key: "Delete" });
      await new Promise((resolve) => setTimeout(resolve, 0));
      window.removeEventListener("keydown", onWindowKey);

      // Added to what the set already keeps out, never replacing it.
      expect(setWorkflowSetDeclines).toHaveBeenCalledWith(10, [
        "9".repeat(64),
        GHOST_LORA.sha256,
      ]);
      expect(onWindowKey).not.toHaveBeenCalled();
      wrapper.unmount();
      const operations = useOperationStore();
      // In the shelf's own name for it.
      expect(operations.receipt.summary).toContain(
        'Kept filmgrain xl out of "Portrait kit"',
      );
      await operations.takeLocalReceiptAction();
      expect(setWorkflowSetDeclines).toHaveBeenLastCalledWith(10, [
        "9".repeat(64),
      ]);
    });

    it("keeps the whole offer separate with Delete on the strip, never reaching the file delete", async () => {
      setWorkflowSetDeclines.mockResolvedValue({
        set: handSet(10, [SET_CKPT]),
        previous: [],
      });
      const { wrapper, store } = await mountOffer({}, { attach: true });
      store.toggleSet("hand:10");
      await wrapper.vm.$nextTick();
      const grid = wrapper.find('[role="treegrid"]');
      await grid.trigger("keydown", { key: "ArrowDown" });
      const onWindowKey = vi.fn();
      window.addEventListener("keydown", onWindowKey);
      await grid.trigger("keydown", { key: "Delete" });
      await new Promise((resolve) => setTimeout(resolve, 0));
      window.removeEventListener("keydown", onWindowKey);

      expect(setWorkflowSetDeclines).toHaveBeenCalledWith(10, [
        GHOST_LORA.sha256,
        GHOST_OTHER.sha256,
      ]);
      expect(onWindowKey).not.toHaveBeenCalled();
      wrapper.unmount();
    });

    it("undoes the newer Keep separate without dropping an older one", async () => {
      const { store } = await mountOffer();
      // A server that remembers the list, so each refetch reads it back.
      let stored = [];
      setWorkflowSetDeclines.mockImplementation(async (id, sha256) => {
        stored = sha256;
        return { set: handSet(id, [SET_CKPT], { declined: sha256 }) };
      });
      fetchWorkflowSets.mockImplementation(async () => ({
        combinations: [],
        no_set: [],
        hand_made: [
          handSet(10, [SET_CKPT], { offer: OFFER, declined: stored }),
        ],
      }));
      const operations = useOperationStore();
      const set = () => store.handMadeSets[0];

      // Both fired before either answers, as two quick Deletes would be.
      await Promise.all([
        store.keepOutOfHandMadeSet(set(), [GHOST_LORA]),
        store.keepOutOfHandMadeSet(set(), [GHOST_OTHER]),
      ]);
      expect(setWorkflowSetDeclines).toHaveBeenLastCalledWith(10, [
        GHOST_LORA.sha256,
        GHOST_OTHER.sha256,
      ]);

      // One pill: the latest write's, which takes out only its own.
      await operations.takeLocalReceiptAction();
      expect(setWorkflowSetDeclines).toHaveBeenLastCalledWith(10, [
        GHOST_LORA.sha256,
      ]);
    });

    it("never builds the next Keep separate from a refetch that is out of date", async () => {
      const { store } = await mountOffer();
      setWorkflowSetDeclines.mockImplementation(async (id, sha256) => ({
        set: handSet(id, [SET_CKPT], { declined: sha256 }),
      }));
      // Every refetch answers from before the writes, as one that left
      // before they landed would.
      const set = () => store.handMadeSets[0];
      await store.keepOutOfHandMadeSet(set(), [GHOST_LORA]);
      await store.keepOutOfHandMadeSet(set(), [GHOST_OTHER]);
      expect(setWorkflowSetDeclines).toHaveBeenLastCalledWith(10, [
        GHOST_LORA.sha256,
        GHOST_OTHER.sha256,
      ]);
    });

    it("says what was kept separate and offers the merge again", async () => {
      setWorkflowSetDeclines.mockResolvedValue({
        set: handSet(10, [SET_CKPT]),
        previous: [GHOST_LORA.sha256],
      });
      const { wrapper, store } = await mountOffer({
        offer: null,
        declined: [GHOST_LORA.sha256],
        kept_separate: 812,
      });
      expect(wrapper.find('[data-testid="merge-offer"]').exists()).toBe(false);
      store.toggleSet("hand:10");
      await wrapper.vm.$nextTick();

      const kept = wrapper.find(".mss__kept");
      expect(kept.text()).toContain("Kept separate from 812 pictures.");
      await kept.find("button").trigger("click");
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(setWorkflowSetDeclines).toHaveBeenCalledWith(10, []);
    });
  });
});
