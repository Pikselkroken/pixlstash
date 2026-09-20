// The Workflows Filters panel (v1.12 F7).
//
// The thing that can silently invert here is WHICH SIDE applies a filter.
// *Hide one-offs* and *Show hidden workflows* are the server's, because
// widening the set re-runs the stacking; the other five are the client's,
// because they only ever remove a card. A panel that applied the first two in
// the browser would look identical on screen and be wrong about every stack,
// so each of the two is asserted as a REQUEST and the rest as a filtered list.
//
// The counts are the second: they label the checkboxes, and a count computed
// over the already-filtered cards goes to zero on the row you are reading.

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

/** The panel over a grid that already holds `cards` and the two counts. */
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
    global: { stubs: { "v-icon": true } },
  });
  await flushPromises();
  return { wrapper, store };
}

/** One checkbox row's `<input>`. */
const check = (wrapper, key) => wrapper.find(`[data-testid="wff-${key}"]`);

/** A checkbox row's whole line. */
const rowText = (wrapper, key) =>
  check(wrapper, key).element.closest("label").textContent.replace(/\s+/g, " ");

/** Just that row's count, exactly — `toContain("2")` also passes on 12. */
const rowCount = (wrapper, key) =>
  check(wrapper, key).element.closest("label").querySelector(".fm-n")
    .textContent;

/** The rows of one pick-one section, by its label. */
function section(wrapper, label) {
  const found = wrapper
    .findAll(".tbm-section")
    .find(
      (node) =>
        node.find(".tbm-label").exists() &&
        node.find(".tbm-label").text() === label,
    );
  if (!found) throw new Error(`no filter section called ${label}`);
  return found;
}

/** One pick-one section as `[label, count]` pairs, in the order drawn. */
const optionLabels = (wrapper, label) =>
  section(wrapper, label)
    .findAll(".optrow")
    .map((row) => [
      row.find(".optrow__label").text(),
      row.find(".fm-n").text(),
    ]);

beforeEach(() => {
  listWorkflowCards.mockReset();
});

describe("the Workflows filter panel", () => {
  it("labels the two server-side checkboxes with the counts the payload sent", async () => {
    const { wrapper } = await mountMenu();
    expect(rowText(wrapper, "hideOneOffs")).toContain("Hide one-offs");
    expect(rowCount(wrapper, "hideOneOffs")).toBe("7");
    expect(rowText(wrapper, "showHidden")).toContain("Show hidden workflows");
    expect(rowCount(wrapper, "showHidden")).toBe("4");
    expect(check(wrapper, "hideOneOffs").element.checked).toBe(true);
    expect(check(wrapper, "showHidden").element.checked).toBe(false);
  });

  it("states the one-off rule the backend actually applies", async () => {
    const { wrapper } = await mountMenu();
    expect(wrapper.text().replace(/\s+/g, " ")).toContain(
      "fewer than 3 pictures, no rating, no saved recipe, not imported",
    );
  });

  it("asks the server for the one-offs rather than filtering them here", async () => {
    const { wrapper } = await mountMenu();
    await check(wrapper, "hideOneOffs").setValue(false);
    await flushPromises();
    expect(listWorkflowCards).toHaveBeenCalledWith({
      includeHidden: false,
      includeOneOffs: true,
    });
  });

  it("asks the server for the hidden cards rather than filtering them here", async () => {
    const { wrapper } = await mountMenu();
    await check(wrapper, "showHidden").setValue(true);
    await flushPromises();
    expect(listWorkflowCards).toHaveBeenCalledWith({
      includeHidden: true,
      includeOneOffs: false,
    });
  });

  // That the SERVER keeps the counts steady across the flags is
  // `tests/test_workflows_api.py`'s to prove, and it does. What is left for
  // this end is narrower and was being claimed as the other: a ticked row
  // must go on drawing the payload's number rather than blanking, or
  // switching to counting what it can see.
  it("keeps drawing the payload's count on a row that is ticked", async () => {
    const { wrapper, store } = await mountMenu();
    await check(wrapper, "hideOneOffs").setValue(false);
    await flushPromises();
    expect(check(wrapper, "hideOneOffs").element.checked).toBe(false);
    expect(rowCount(wrapper, "hideOneOffs")).toBe(String(store.oneOffs));
    expect(rowCount(wrapper, "showHidden")).toBe(String(store.hidden));
    // And not the number of cards on screen, which is the wrong source it
    // would be natural to reach for. Asserted by making the two collide:
    // `not.toBe` between 7 and 3 is true of almost any implementation, so the
    // grid is given exactly `oneOffs` cards and the row must still read 7
    // because it read the payload rather than counted the screen.
    expect(store.filteredCards).toHaveLength(3);
    store.cards = Array.from({ length: store.oneOffs }, (_, i) => ({
      ...CARDS[0],
      key: `pad-${i}`,
    }));
    await flushPromises();
    expect(store.filteredCards).toHaveLength(store.oneOffs);
    expect(rowCount(wrapper, "hideOneOffs")).toBe("7");
  });

  it("keeps a workflow that holds a ghost of either kind", async () => {
    const { wrapper, store } = await mountMenu();
    expect(rowText(wrapper, "ghosts")).toContain("Keeps something deleted");
    // Two of the three: one picture ghost, one model ghost.
    expect(rowCount(wrapper, "ghosts")).toBe("2");

    await check(wrapper, "ghosts").setValue(true);
    expect(store.filteredCards.map((entry) => entry.name)).toEqual([
      "Upscale 2×",
      "Sketch to image",
    ]);
    // No request: this one only ever removes a card.
    expect(listWorkflowCards).not.toHaveBeenCalled();
  });

  it("counts each pick-one option over the whole grid, not the filtered one", async () => {
    const { wrapper, store } = await mountMenu();
    expect(optionLabels(wrapper, "Type")).toEqual([
      ["Text to Image", "1"],
      ["Upscale", "1"],
      ["Image to Image", "1"],
    ]);
    expect(optionLabels(wrapper, "Source")).toEqual([
      ["Imported file", "1"],
      ["Found in your pictures", "2"],
    ]);

    store.setFilters({ source: "imported" });
    await flushPromises();
    // Still every type, still counted over all three cards.
    expect(optionLabels(wrapper, "Type")).toHaveLength(3);
    expect(optionLabels(wrapper, "Source")).toEqual([
      ["Imported file", "1"],
      ["Found in your pictures", "2"],
    ]);
  });

  it("narrows the grid on a pick-one and puts one chip on the strip", async () => {
    const { wrapper, store } = await mountMenu();
    const upscale = section(wrapper, "Type")
      .findAll(".optrow")
      .find((row) => row.find(".optrow__label").text() === "Upscale");
    await upscale.trigger("click");

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
    const pick = (label) =>
      section(wrapper, "Source")
        .findAll(".optrow")
        .find((row) => row.find(".optrow__label").text() === label)
        .trigger("click");

    await pick("Imported file");
    expect(store.filteredCards.map((entry) => entry.name)).toEqual([
      "Upscale 2×",
    ]);

    await pick("Found in your pictures");
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
    expect(optionLabels(wrapper, "Checkpoint")).toEqual([
      ["realvisXL_v5.safetensors", "2"],
    ]);

    await section(wrapper, "Checkpoint")
      .findAll(".optrow")[0]
      .trigger("click");
    expect(store.filteredCards.map((entry) => entry.name)).toEqual([
      "Cinematic portrait",
      "Sketch to image",
    ]);
    expect(store.filterChips.map((chip) => [chip.kind, chip.value])).toEqual([
      ["Checkpoint", "realvisXL_v5.safetensors"],
    ]);
  });

  it("keeps a card at or above the minimum rating, unrated ones included out", async () => {
    const { wrapper, store } = await mountMenu();
    const four = section(wrapper, "Min rating")
      .findAll(".optrow")
      .find((row) => row.find(".optrow__label").text() === "4★ and up");
    await four.trigger("click");
    expect(store.filteredCards.map((entry) => entry.name)).toEqual([
      "Cinematic portrait",
    ]);
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
