// The Workflows Filters panel (v1.12 F7).
//
// The thing that can silently invert here is WHICH SIDE applies a filter.
// *Hide one-offs* and *Hidden workflows* are the server's, because
// widening the set re-runs the stacking; the other five are the client's,
// because they only ever remove a card. A panel that applied the first two in
// the browser would look identical on screen and be wrong about every stack,
// so each of the two is asserted as a REQUEST and the rest as a filtered list.
//
// The counts are the second: they label the checkboxes, and a count computed
// over the already-filtered cards goes to zero on the row you are reading.

import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const listWorkflowCards = vi.fn();
vi.mock("../../api/workflows", () => ({
  listWorkflowCards: (...args) => listWorkflowCards(...args),
  getWorkflowCard: vi.fn(),
  workflowCoverUrl: (cover) => cover?.url ?? "",
}));

import FilterChecklistMenu from "./FilterChecklistMenu.vue";
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
async function mountMenu({
  cards = CARDS,
  oneOffs = 7,
  hidden = 4,
  attachTo,
} = {}) {
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
    attachTo,
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

/** A root row of the Workflow section, by its filter key. */
const kindRow = (wrapper, key) =>
  wrapper.find(`[data-testid="wff-row-${key}"]`);

/** What a root row says is picked; empty means "any". */
const rowValue = (wrapper, key) => kindRow(wrapper, key).find(".fm-n").text();

/** Opens a kind's flyout (if another is open, it replaces it) and returns it. */
async function section(wrapper, key) {
  if (kindRow(wrapper, key).attributes("aria-expanded") !== "true") {
    await kindRow(wrapper, key).trigger("click");
    await flushPromises();
  }
  const found = wrapper.find(".fm-sub-slot");
  if (!found.exists()) throw new Error(`no flyout opened for ${key}`);
  return found;
}

/** One Type or Source flyout as `[label, count]` pairs, in the order drawn. */
const optionLabels = async (wrapper, key) =>
  (await section(wrapper, key))
    .findAll(".optrow")
    .map((row) => [
      row.find(".optrow__label").text(),
      row.find(".fm-n").text(),
    ]);

/** The Checkpoint flyout's rows as `[label, count]` pairs. */
const checkpointRows = async (wrapper) =>
  (await section(wrapper, "checkpoint"))
    .findAll('[role="radio"]')
    .map((row) => [row.find(".fm-row-label").text(), row.find(".fm-n").text()]);

beforeEach(() => {
  listWorkflowCards.mockReset();
});

describe("the Workflows filter panel", () => {
  it("labels the two server-side checkboxes with the counts the payload sent", async () => {
    const { wrapper } = await mountMenu();
    expect(rowText(wrapper, "hideOneOffs")).toContain("Hide one-offs");
    expect(rowCount(wrapper, "hideOneOffs")).toBe("7");
    expect(rowText(wrapper, "showHidden")).toContain("Hidden workflows");
    expect(rowCount(wrapper, "showHidden")).toBe("4");
    expect(check(wrapper, "hideOneOffs").element.checked).toBe(true);
    expect(check(wrapper, "showHidden").element.checked).toBe(false);
  });

  it("states the one-off rule the backend actually applies", async () => {
    const { wrapper } = await mountMenu();
    expect(wrapper.text().replace(/\s+/g, " ")).toContain(
      "A one-off: under 3 pictures, unrated, no recipe, not imported.",
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
    expect(await optionLabels(wrapper, "type")).toEqual([
      ["Text to Image", "1"],
      ["Upscale", "1"],
      ["Image to Image", "1"],
    ]);
    expect(await optionLabels(wrapper, "source")).toEqual([
      ["Imported file", "1"],
      ["Found in your pictures", "2"],
    ]);

    store.setFilters({ source: "imported" });
    await flushPromises();
    // Still every type, still counted over all three cards.
    expect(await optionLabels(wrapper, "type")).toHaveLength(3);
    expect(await optionLabels(wrapper, "source")).toEqual([
      ["Imported file", "1"],
      ["Found in your pictures", "2"],
    ]);
  });

  it("narrows the grid on a pick-one and puts one chip on the strip", async () => {
    const { wrapper, store } = await mountMenu();
    const upscale = (await section(wrapper, "type"))
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
    const pick = async (label) =>
      (await section(wrapper, "source"))
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
    expect(await checkpointRows(wrapper)).toEqual([
      ["realvisXL_v5.safetensors", "2"],
    ]);

    await (
      await section(wrapper, "checkpoint")
    )
      .find('[role="radio"]')
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
    await (
      await section(wrapper, "minRating")
    )
      .find('[aria-label="At least 4 stars"]')
      .trigger("click");
    expect(store.filteredCards.map((entry) => entry.name)).toEqual([
      "Cinematic portrait",
    ]);
  });

  // The row's value is the chip's, so the root and the strip cannot say one
  // pick two ways. Empty means "any", not a stale label.
  it("says on each root row what is picked, in the chip's words", async () => {
    const { wrapper, store } = await mountMenu();
    for (const key of ["type", "checkpoint", "source", "minRating"]) {
      expect(rowValue(wrapper, key)).toBe("");
    }
    store.setFilters({
      type: "upscale",
      checkpoint: "realvisXL_v5.safetensors",
      source: "found",
      minRating: 3,
    });
    await flushPromises();
    expect(rowValue(wrapper, "type")).toBe("Upscale");
    expect(rowValue(wrapper, "checkpoint")).toBe("realvisXL_v5.safetensors");
    expect(rowValue(wrapper, "source")).toBe("Found in your pictures");
    expect(rowValue(wrapper, "minRating")).toBe("3★ and up");
  });

  it("opens one flyout at a time, and drops it when the menu closes", async () => {
    const { wrapper } = await mountMenu();
    expect(wrapper.find(".fm-sub-slot").exists()).toBe(false);
    await section(wrapper, "type");
    await section(wrapper, "source");
    expect(wrapper.findAll(".fm-sub-slot")).toHaveLength(1);
    expect(kindRow(wrapper, "type").attributes("aria-expanded")).toBe("false");
    expect(kindRow(wrapper, "source").attributes("aria-expanded")).toBe("true");

    await wrapper.setProps({ open: true });
    await wrapper.setProps({ open: false });
    expect(wrapper.find(".fm-sub-slot").exists()).toBe(false);
  });

  // The store holds ONE checkpoint: picking a second replaces the first
  // rather than adding to it. A radio is never unticked: picking the chosen
  // row again keeps it, and the flyout's Clear is the way back to any.
  it("picks one checkpoint, from the keyboard as well as the pointer", async () => {
    const two = [
      ...CARDS,
      card({
        key: "e".repeat(64),
        name: "Flux portrait",
        models: [{ name: "flux1-dev.safetensors", kind: "checkpoint" }],
      }),
    ];
    const { wrapper, store } = await mountMenu({ cards: two });
    const flyout = await section(wrapper, "checkpoint");
    expect(flyout.findAll('input[type="checkbox"]')).toHaveLength(0);

    const field = flyout.find("input");
    await field.trigger("keydown", { key: "Enter" });
    expect(store.filters.checkpoint).toBe("realvisXL_v5.safetensors");

    await field.trigger("keydown", { key: "ArrowDown" });
    await field.trigger("keydown", { key: "Enter" });
    expect(store.filters.checkpoint).toBe("flux1-dev.safetensors");
    expect(store.filterChips.map((chip) => chip.value)).toEqual([
      "flux1-dev.safetensors",
    ]);
    const checked = flyout.findAll('[role="radio"][aria-checked="true"]');
    expect(checked.map((row) => row.find(".fm-row-label").text())).toEqual([
      "flux1-dev.safetensors",
    ]);

    await checked[0].trigger("click");
    await field.trigger("keydown", { key: "Enter" });
    expect(store.filters.checkpoint).toBe("flux1-dev.safetensors");

    await flyout
      .findAll("button")
      .find((button) => button.text() === "Clear")
      .trigger("click");
    expect(store.filters.checkpoint).toBeNull();
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
// The cascade's keyboard contract, the grid's: → opens a row's flyout and
// puts focus in its body, ← comes back to the row. Attached to the document,
// because focus is what is being asserted.
describe("the filter cascade from the keyboard", () => {
  let mounted;
  afterEach(() => mounted?.unmount());

  async function keyboardMenu(options) {
    const { wrapper, store } = await mountMenu({
      ...options,
      attachTo: document.body,
    });
    mounted = wrapper;
    return { wrapper, store };
  }

  const active = () => document.activeElement;

  it("opens a row's flyout on → and focuses its chosen option", async () => {
    const { wrapper, store } = await keyboardMenu();
    store.setFilters({ minRating: 3 });
    await flushPromises();
    await kindRow(wrapper, "minRating").trigger("keydown", {
      key: "ArrowRight",
    });
    await flushPromises();
    expect(kindRow(wrapper, "minRating").attributes("aria-expanded")).toBe(
      "true",
    );
    expect(active().getAttribute("aria-label")).toBe("At least 3 stars");
  });

  it("comes back to the row on ←, without changing the pick", async () => {
    const { wrapper, store } = await keyboardMenu();
    store.setFilters({ minRating: 3, type: "upscale" });
    await flushPromises();
    for (const key of ["minRating", "type"]) {
      await kindRow(wrapper, key).trigger("keydown", { key: "ArrowRight" });
      await flushPromises();
      expect(wrapper.find(".fm-sub-slot").element.contains(active())).toBe(
        true,
      );
      active().dispatchEvent(
        new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }),
      );
      await flushPromises();
      expect(active()).toBe(kindRow(wrapper, key).element);
      expect(wrapper.find(".fm-sub-slot").exists()).toBe(false);
    }
    expect(store.filters.minRating).toBe(3);
    expect(store.filters.type).toBe("upscale");
  });

  it("leaves ← to the Checkpoint search while its caret can still move", async () => {
    const { wrapper } = await keyboardMenu();
    await kindRow(wrapper, "checkpoint").trigger("keydown", {
      key: "ArrowRight",
    });
    await flushPromises();
    const field = wrapper.find(".fm-sub-slot input");
    expect(active()).toBe(field.element);

    await field.setValue("real");
    field.element.setSelectionRange(4, 4);
    await field.trigger("keydown", { key: "ArrowLeft" });
    expect(wrapper.find(".fm-sub-slot").exists()).toBe(true);

    field.element.setSelectionRange(0, 0);
    await field.trigger("keydown", { key: "ArrowLeft" });
    await flushPromises();
    expect(wrapper.find(".fm-sub-slot").exists()).toBe(false);
    expect(active()).toBe(kindRow(wrapper, "checkpoint").element);
  });

  // The field is the pick-one list's one tab stop: a click on a row must not
  // take focus from it, or ↑/↓/Enter stop working until it is clicked again.
  it("keeps focus in the Checkpoint search when a row is clicked", async () => {
    const { wrapper } = await keyboardMenu();
    const flyout = await section(wrapper, "checkpoint");
    expect(flyout.find('[role="radiogroup"]').exists()).toBe(true);
    const row = flyout.find('[role="radio"]').element;
    const down = new MouseEvent("mousedown", {
      bubbles: true,
      cancelable: true,
    });
    row.dispatchEvent(down);
    expect(down.defaultPrevented).toBe(true);
  });

  // A radio is only ever asked ON: Enter on the chosen row must not emit a
  // request to untick it, whatever the parent then does with it.
  it("asks for the highlighted checkpoint on, even when it is chosen", async () => {
    const { wrapper, store } = await keyboardMenu();
    store.setFilters({ checkpoint: "realvisXL_v5.safetensors" });
    const flyout = await section(wrapper, "checkpoint");
    await flyout.find("input").trigger("keydown", { key: "Enter" });
    expect(
      wrapper.findComponent(FilterChecklistMenu).emitted("toggle"),
    ).toEqual([["realvisXL_v5.safetensors", true]]);
  });
});

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
