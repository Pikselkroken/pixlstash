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
  workflowCoverUrl: (cover) => cover,
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

/** A checkbox row's whole line, count included. */
const rowText = (wrapper, key) =>
  check(wrapper, key).element.closest("label").textContent.replace(/\s+/g, " ");

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
    expect(rowText(wrapper, "hideOneOffs")).toContain("7");
    expect(rowText(wrapper, "showHidden")).toContain("Show hidden workflows");
    expect(rowText(wrapper, "showHidden")).toContain("4");
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

  // The count keeps meaning "how many one-offs this library has", so the
  // checkbox goes on being labelled while it is the one letting them in.
  it("keeps the counts steady when a checkbox is ticked", async () => {
    const { wrapper } = await mountMenu();
    await check(wrapper, "hideOneOffs").setValue(false);
    await flushPromises();
    expect(rowText(wrapper, "hideOneOffs")).toContain("7");
    expect(rowText(wrapper, "showHidden")).toContain("4");
  });

  it("keeps a workflow that holds a ghost of either kind", async () => {
    const { wrapper, store } = await mountMenu();
    expect(rowText(wrapper, "ghosts")).toContain("Keeps something deleted");
    // Two of the three: one picture ghost, one model ghost.
    expect(rowText(wrapper, "ghosts")).toContain("2");

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
