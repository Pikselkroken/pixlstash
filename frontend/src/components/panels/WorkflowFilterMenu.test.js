// The Workflows Filters menu: the picture grid's cascade with this screen's
// rows. Every filter, the three flags included, is a pick-one submenu.
//
// The thing that can silently invert here is WHICH SIDE applies a filter.
// *One-offs* and *Hidden* are the server's, because widening the set re-runs
// the stacking; the other five are the client's, because they only ever
// remove a card. A menu that applied the first two in the browser would look
// identical on screen and be wrong about every stack, so each of the two is
// asserted as a REQUEST and the rest as a filtered list.
//
// The counts are the second: they label the rows, and a count computed over
// the already-filtered cards goes to zero on the row you are reading.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const listWorkflowCards = vi.fn();
vi.mock("../../api/workflows", () => ({
  listWorkflowCards: (...args) => listWorkflowCards(...args),
  getWorkflowCard: vi.fn(),
  workflowCoverUrl: (cover) => cover?.url ?? "",
}));

import WorkflowFilterMenu from "./WorkflowFilterMenu.vue";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";

function card(overrides = {}) {
  return {
    key: "a".repeat(64),
    name: "Cinematic portrait",
    type: "txt2img",
    type_label: "Text to Image",
    imported: false,
    models: [{ name: "realvisXL_v5.safetensors", kind: "checkpoint" }],
    loras: [],
    differs_by: [],
    picture_count: 184,
    rating: 4.8,
    covers: [],
    stack_size: 1,
    saved_recipe_count: 0,
    defaults: [],
    topology_hash: "d".repeat(64),
    variant_count: 1,
    member_keys: [],
    last_used: null,
    rank: 4.5,
    ghosts: 0,
    model_ghosts: 0,
    ...overrides,
  };
}

const CARDS = [
  card(),
  card({
    key: "b".repeat(64),
    name: "Upscale 2×",
    type: "upscale",
    type_label: "Upscale",
    imported: true,
    models: [{ name: "4x-UltraSharp.safetensors", kind: "upscale" }],
    rating: 2.5,
    ghosts: 3,
  }),
  card({
    key: "c".repeat(64),
    name: "Sketch to image",
    type: "img2img",
    type_label: "Image to Image",
    rating: null,
    model_ghosts: 1,
  }),
];

/** The menu over a grid that already holds `cards` and the two counts. */
async function mountMenu({ cards = CARDS, oneOffs = 7, hidden = 4 } = {}) {
  setActivePinia(createPinia());
  const store = useWorkflowsStore();
  store.cards = cards;
  store.oneOffs = oneOffs;
  store.hidden = hidden;
  listWorkflowCards.mockResolvedValue({
    cards: [...cards],
    one_offs: oneOffs,
    hidden,
  });
  const wrapper = mount(WorkflowFilterMenu, {
    props: { open: true },
    global: { stubs: { "v-icon": true } },
    attachTo: document.body,
  });
  await flushPromises();
  return { wrapper, store };
}

/** A root row, by its filter key. */
const rootRow = (wrapper, kind) => wrapper.find(`[data-kind="${kind}"]`);

/** What a root row says about its filter: "", "1" or "4★+". */
const rowValue = (wrapper, kind) => rootRow(wrapper, kind).find(".fm-n").text();

/** Open a root row's submenu and return it. */
async function openSub(wrapper, kind) {
  if (rootRow(wrapper, kind).attributes("aria-expanded") !== "true") {
    await rootRow(wrapper, kind).trigger("click");
    await flushPromises();
  }
  const sub = wrapper.find(`[data-testid="wff-sub-${kind}"]`);
  if (!sub.exists()) throw new Error(`no submenu for ${kind}`);
  return sub;
}

/** One submenu as `[label, count]` pairs, in the order drawn. */
async function optionLabels(wrapper, kind) {
  const sub = await openSub(wrapper, kind);
  return sub
    .findAll(".optrow")
    .map((row) => [
      row.attributes("aria-label") ?? row.find(".optrow__label").text(),
      row.find(".fm-n").text(),
    ]);
}

/** Click the radio labelled `label` in a submenu. */
async function pick(wrapper, kind, label) {
  const sub = await openSub(wrapper, kind);
  const row = sub
    .findAll(".optrow")
    .find(
      (r) =>
        (r.attributes("aria-label") ?? r.find(".optrow__label").text()) ===
        label,
    );
  if (!row) throw new Error(`no ${label} in ${kind}`);
  await row.trigger("click");
  await flushPromises();
}

/** The label of the checked radio in a submenu. */
async function chosen(wrapper, kind) {
  const sub = await openSub(wrapper, kind);
  return sub.find('[aria-checked="true"] .optrow__label').text();
}

beforeEach(() => {
  listWorkflowCards.mockReset();
});

describe("the Workflows filter menu", () => {
  it("draws the grid's root: three sections, a row per filter, no checkbox", async () => {
    const { wrapper } = await mountMenu();
    expect(wrapper.findAll(".tbm-label").map((l) => l.text())).toEqual([
      "Workflow",
      "Quality",
      "Show",
    ]);
    expect(
      wrapper.findAll(".fm-row .fm-row-label").map((l) => l.text()),
    ).toEqual([
      "Type",
      "Checkpoint",
      "Source",
      "Rating",
      "One-offs",
      "Hidden",
      "Ghosts",
    ]);
    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(false);
    // Nothing open until a row is.
    expect(wrapper.find(".fm-sub").exists()).toBe(false);
  });

  it("says on the root row only whether its filter is on", async () => {
    const { wrapper, store } = await mountMenu();
    for (const kind of ["type", "hideOneOffs", "showHidden", "minRating"]) {
      expect(rowValue(wrapper, kind)).toBe("");
    }
    store.setFilters({ type: "upscale", hideOneOffs: false, minRating: 4 });
    await flushPromises();
    expect(rowValue(wrapper, "type")).toBe("1");
    // Hide is the default, so it is Show that turns the row on.
    expect(rowValue(wrapper, "hideOneOffs")).toBe("1");
    expect(rowValue(wrapper, "showHidden")).toBe("");
    expect(rowValue(wrapper, "minRating")).toBe("4★+");
  });

  it("opens one submenu at a time, and closes it on a second click", async () => {
    const { wrapper } = await mountMenu();
    await openSub(wrapper, "type");
    await openSub(wrapper, "source");
    expect(wrapper.findAll(".fm-sub")).toHaveLength(1);
    await rootRow(wrapper, "source").trigger("click");
    expect(wrapper.find(".fm-sub").exists()).toBe(false);
  });

  it("drops the open submenu when the menu closes", async () => {
    const { wrapper } = await mountMenu();
    await openSub(wrapper, "type");
    await wrapper.setProps({ open: false });
    expect(wrapper.find(".fm-sub").exists()).toBe(false);
  });

  it("labels the two server-side flags with the counts the payload sent", async () => {
    const { wrapper } = await mountMenu();
    expect(await optionLabels(wrapper, "hideOneOffs")).toEqual([
      ["Hide", "7"],
      ["Show", "7"],
    ]);
    expect(await chosen(wrapper, "hideOneOffs")).toBe("Hide");
    expect(await optionLabels(wrapper, "showHidden")).toEqual([
      ["Hide", "4"],
      ["Show", "4"],
    ]);
    expect(await chosen(wrapper, "showHidden")).toBe("Hide");
  });

  it("states the one-off rule the backend actually applies", async () => {
    const { wrapper } = await mountMenu();
    const sub = await openSub(wrapper, "hideOneOffs");
    expect(sub.find(".tbm-footer").text().replace(/\s+/g, " ")).toContain(
      "fewer than 3 pictures, no rating, no saved recipe, and was not imported",
    );
  });

  it("asks the server for the one-offs rather than filtering them here", async () => {
    const { wrapper } = await mountMenu();
    await pick(wrapper, "hideOneOffs", "Show");
    expect(listWorkflowCards).toHaveBeenCalledWith({
      includeHidden: false,
      includeOneOffs: true,
    });
  });

  it("asks the server for the hidden cards rather than filtering them here", async () => {
    const { wrapper } = await mountMenu();
    await pick(wrapper, "showHidden", "Show");
    expect(listWorkflowCards).toHaveBeenCalledWith({
      includeHidden: true,
      includeOneOffs: false,
    });
  });

  // That the SERVER keeps the counts steady across the flags is
  // `tests/test_workflows_api.py`'s to prove. What is left for this end is
  // that a flag that is on goes on drawing the payload's number rather than
  // counting what it can see.
  it("keeps drawing the payload's count on a flag that is on", async () => {
    const { wrapper, store } = await mountMenu();
    await pick(wrapper, "hideOneOffs", "Show");
    expect(await chosen(wrapper, "hideOneOffs")).toBe("Show");
    expect((await optionLabels(wrapper, "hideOneOffs"))[1][1]).toBe(
      String(store.oneOffs),
    );
    // Make the grid hold exactly `oneOffs` cards: the row must still read 7
    // because it read the payload, not because 7 happened to differ from 3.
    store.cards = Array.from({ length: store.oneOffs }, (_, i) => ({
      ...CARDS[0],
      key: `pad-${i}`,
    }));
    store.oneOffs = 5;
    await flushPromises();
    expect(await optionLabels(wrapper, "hideOneOffs")).toEqual([
      ["Hide", "5"],
      ["Show", "5"],
    ]);
  });

  it("keeps a workflow that holds a ghost of either kind", async () => {
    const { wrapper, store } = await mountMenu();
    // Two of the three: one picture ghost, one model ghost.
    expect(await optionLabels(wrapper, "ghosts")).toEqual([
      ["Any", "3"],
      ["Keeps something deleted", "2"],
    ]);

    await pick(wrapper, "ghosts", "Keeps something deleted");
    expect(store.filteredCards.map((entry) => entry.name)).toEqual([
      "Upscale 2×",
      "Sketch to image",
    ]);
    // No request: this one only ever removes a card.
    expect(listWorkflowCards).not.toHaveBeenCalled();
  });

  it("counts each pick-one option over the whole grid, not the filtered one", async () => {
    const { wrapper, store } = await mountMenu();
    expect(await optionLabels(wrapper, "type")).toEqual([
      ["All", "3"],
      ["Text to Image", "1"],
      ["Upscale", "1"],
      ["Image to Image", "1"],
    ]);
    const sources = [
      ["Any", "3"],
      ["Imported file", "1"],
      ["Found in your pictures", "2"],
    ];
    expect(await optionLabels(wrapper, "source")).toEqual(sources);

    store.setFilters({ source: "imported" });
    await flushPromises();
    // Still every type, still counted over all three cards.
    expect(await optionLabels(wrapper, "type")).toHaveLength(4);
    expect(await optionLabels(wrapper, "source")).toEqual(sources);
  });

  it("narrows the grid on a pick-one and puts one chip on the strip", async () => {
    const { wrapper, store } = await mountMenu();
    await pick(wrapper, "type", "Upscale");

    expect(store.filters.type).toBe("upscale");
    expect(store.filteredCards.map((entry) => entry.name)).toEqual([
      "Upscale 2×",
    ]);
    expect(store.filterChips.map((chip) => [chip.kind, chip.value])).toEqual([
      ["Type", "Upscale"],
    ]);

    store.filterChips[0].remove();
    expect(store.filters.type).toBeNull();
    expect(store.filteredCards).toHaveLength(3);
  });

  // `Boolean(card.imported) !== imported` is one character from being its own
  // opposite, and nothing asserted which side of it a card landed on.
  it("keeps the imported cards on Source, and the found ones on the other", async () => {
    const { wrapper, store } = await mountMenu();
    await pick(wrapper, "source", "Imported file");
    expect(store.filteredCards.map((entry) => entry.name)).toEqual([
      "Upscale 2×",
    ]);

    await pick(wrapper, "source", "Found in your pictures");
    expect(store.filteredCards.map((entry) => entry.name)).toEqual([
      "Cinematic portrait",
      "Sketch to image",
    ]);
  });

  // The checkpoint is picked by `checkpointModel`, which prefers the BASE
  // model over document order — the exact preference that drifted in #1416.
  // "Upscale 2×" carries an `upscale` slot and no base model, so an
  // implementation reading `models[0]` would offer it here as a checkpoint.
  it("offers the base models as checkpoints, and narrows to the one picked", async () => {
    const { wrapper, store } = await mountMenu();
    expect(await optionLabels(wrapper, "checkpoint")).toEqual([
      ["Any", "3"],
      ["realvisXL_v5.safetensors", "2"],
    ]);

    await pick(wrapper, "checkpoint", "realvisXL_v5.safetensors");
    expect(store.filteredCards.map((entry) => entry.name)).toEqual([
      "Cinematic portrait",
      "Sketch to image",
    ]);
    expect(store.filterChips.map((chip) => [chip.kind, chip.value])).toEqual([
      ["Checkpoint", "realvisXL_v5.safetensors"],
    ]);
  });

  it("narrows the checkpoint list as the field is typed in", async () => {
    const cards = [
      ...CARDS,
      card({
        key: "e".repeat(64),
        models: [{ name: "juggernaut.safetensors", kind: "checkpoint" }],
      }),
    ];
    const { wrapper } = await mountMenu({ cards });
    const sub = await openSub(wrapper, "checkpoint");
    expect(sub.find(".tbm-footer").text()).toBe("2 checkpoints in this grid.");
    // The field has focus on open, as the design's keyboard table says.
    expect(document.activeElement).toBe(sub.find("input").element);

    await sub.find("input").setValue("JUGG");
    expect(await optionLabels(wrapper, "checkpoint")).toEqual([
      ["Any", "4"],
      ["juggernaut.safetensors", "1"],
    ]);

    await sub.find("input").setValue("nothing-like-it");
    expect(await optionLabels(wrapper, "checkpoint")).toEqual([["Any", "4"]]);
    expect(sub.text()).toContain("No checkpoint matches.");
  });

  it("keeps a card at or above the minimum rating, unrated ones included out", async () => {
    const { wrapper, store } = await mountMenu();
    await pick(wrapper, "minRating", "At least 4 stars");
    expect(store.filters.minRating).toBe(4);
    expect(store.filteredCards.map((entry) => entry.name)).toEqual([
      "Cinematic portrait",
    ]);
  });

  it("moves through the root rows with the arrows and in and out with → and ←", async () => {
    const { wrapper } = await mountMenu();
    rootRow(wrapper, "source").element.focus();
    await rootRow(wrapper, "source").trigger("keydown", { key: "ArrowDown" });
    expect(document.activeElement).toBe(rootRow(wrapper, "minRating").element);
    await rootRow(wrapper, "type").trigger("keydown", { key: "ArrowUp" });
    expect(document.activeElement).toBe(rootRow(wrapper, "ghosts").element);

    await rootRow(wrapper, "showHidden").trigger("keydown", {
      key: "ArrowRight",
    });
    await flushPromises();
    const radio = wrapper.find(
      '[data-testid="wff-sub-showHidden"] [aria-checked="true"]',
    );
    expect(document.activeElement).toBe(radio.element);

    await radio.trigger("keydown", { key: "ArrowLeft" });
    expect(wrapper.find(".fm-sub").exists()).toBe(false);
    expect(document.activeElement).toBe(rootRow(wrapper, "showHidden").element);
  });

  it("clears every filter at once, including the server's two", async () => {
    const { wrapper, store } = await mountMenu();
    store.setFilters({ showHidden: true, type: "upscale", ghosts: true });
    await flushPromises();
    expect(store.filterChips).toHaveLength(3);

    listWorkflowCards.mockClear();
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "Clear all")
      .trigger("click");
    await flushPromises();

    expect(store.filterChips).toHaveLength(0);
    expect(listWorkflowCards).toHaveBeenCalledWith({
      includeHidden: false,
      includeOneOffs: false,
    });
  });
});

// A filter that removes the open stack's cover. The panel is drawn inside the
// cover's row, so it leaves the screen either way — what must not survive is
// the store going on saying a stack is open, with members nothing filtered.
describe("the open stack and the filters", () => {
  const STACKED = [
    { ...CARDS[0], stack_size: 2, member_keys: ["b".repeat(64)] },
    ...CARDS.slice(1),
  ];

  it("closes a stack whose cover the filter takes away", async () => {
    const { store } = await mountMenu({ cards: STACKED });
    store.openStackKey = STACKED[0].key;
    store.members = { [STACKED[0].key]: [STACKED[0], CARDS[1]] };

    // "Cinematic portrait" is the only Text to Image card, so Upscale drops it.
    store.setFilters({ type: "upscale" });

    expect(store.openStackKey).toBeNull();
    expect(store.openMembers).toEqual([]);
  });

  it("leaves a stack open when the filter keeps its cover", async () => {
    const { store } = await mountMenu({ cards: STACKED });
    store.openStackKey = STACKED[0].key;
    store.members = { [STACKED[0].key]: [STACKED[0], CARDS[1]] };

    store.setFilters({ type: "txt2img" });

    expect(store.openStackKey).toBe(STACKED[0].key);
    expect(store.openMembers).toHaveLength(2);
  });
});

// The selection survives a filter only for the cards still on screen. The
// selection bar and the rail's bulk verbs read `selectedKeys`, so a stale one
// offers Hide and Stack together on cards nobody can see — which #1460's
// selection bar has no way to notice on its own.
describe("the selection and the filters", () => {
  it("drops selected cards the filter took off the screen", async () => {
    const { store } = await mountMenu();
    store.selectedKeys = [CARDS[0].key, CARDS[1].key, CARDS[2].key];

    store.setFilters({ type: "upscale" });

    expect(store.selectedKeys).toEqual([CARDS[1].key]);
  });

  it("keeps a selected member of a stack that is still drawn", async () => {
    const cover = { ...CARDS[0], stack_size: 2, member_keys: [CARDS[1].key] };
    const { store } = await mountMenu({ cards: [cover, ...CARDS.slice(1)] });
    store.members = { [cover.key]: [cover, CARDS[1]] };
    store.selectedKeys = [CARDS[1].key, CARDS[2].key];

    // Keeps the cover, drops "Sketch to image".
    store.setFilters({ type: "txt2img" });

    expect(store.selectedKeys).toEqual([CARDS[1].key]);
  });
});
