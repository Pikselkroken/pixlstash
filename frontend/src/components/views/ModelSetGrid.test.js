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
    await grid.find('[role="row"]').trigger("click");

    expect([...store.selectedIds]).toEqual([1]);
    expect(grid.find('[role="row"]').attributes("aria-selected")).toBe("true");
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
    await wrapper.findAll(".msp__member")[2].trigger("click", { ctrlKey: true });
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
