// The shelf's reading surface.
//
// The three assertions worth having are the ones that guard the measured data
// realities: a third of real adapters have no title, no base model and no
// trigger, so a row must never render a blank; a derived name must stay
// distinguishable from a chosen one; and `unknown` must never read as a
// checkpoint.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const listAdapters = vi.fn();
const listCheckpoints = vi.fn();

vi.mock("../../api/modelShelf", () => ({
  BASE_MODEL_UNASSIGNED: "UNASSIGNED",
  listAdapters: (...args) => listAdapters(...args),
  listCheckpoints: (...args) => listCheckpoints(...args),
}));

import ModelShelf from "./ModelShelf.vue";
import { useModelShelfStore } from "../../stores/useModelShelfStore";

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      "v-menu": {
        template: "<div><slot name='activator' :props='{}' /><slot /></div>",
      },
      ShelfShowPanel: true,
    },
  },
};

/** The shape `/adapters` really returns, taken from a fixture probe. */
function adapter(overrides = {}) {
  return {
    id: 1,
    sha256: "a".repeat(64),
    file_kind: "adapter",
    kind: "lora",
    display_name: "Cyanwood Style",
    filename: "Cyanwood_Style_000000250.safetensors",
    base_model: "flux.1-dev",
    file_size: 358733183,
    locations: [{ state: "present", folder_path: "/m", relpath: "a.st" }],
    attachments: [],
    ...overrides,
  };
}

async function mountShelf(rows, checkpoints = []) {
  listAdapters.mockResolvedValue(rows);
  listCheckpoints.mockResolvedValue(checkpoints);
  const wrapper = mount(ModelShelf, globalOpts);
  await new Promise((resolve) => setTimeout(resolve, 0));
  await wrapper.vm.$nextTick();
  return wrapper;
}

/** Text with runs of whitespace collapsed, so wrapping is not asserted. */
function textOf(el) {
  return el.text().replace(/\s+/g, " ");
}

beforeEach(() => {
  setActivePinia(createPinia());
  window.localStorage.clear();
  listAdapters.mockReset();
  listCheckpoints.mockReset();
});

describe("a row with nothing in its header", () => {
  it("shows a name derived from the filename, never a blank", async () => {
    const wrapper = await mountShelf([
      adapter({
        display_name: null,
        base_model: null,
        filename: "Foxglove_Char_000000250.safetensors",
      }),
    ]);
    const row = wrapper.find(".shelf-row");
    expect(row.find(".shelf-row-name").text()).toContain("Foxglove Char");
    // Every slot on the metadata line renders something: the kind is derived
    // from `file_kind` and is the guaranteed anchor when all else is null.
    const slots = row.findAll(".shelf-row-meta > span").map((s) => s.text());
    expect(slots).toEqual(["LoRA", "Base model not set", "342.1 MB"]);
  });

  it("marks the derived name by type, not by fading it", async () => {
    // Rank is size, weight and tracking, never opacity (§5.1) — and 37% of
    // rows faded would be a column of ghosts rather than a signal.
    const wrapper = await mountShelf([
      adapter({ id: 1, display_name: null }),
      adapter({ id: 2, display_name: "Clementine" }),
    ]);
    const names = wrapper.findAll(".shelf-row-name");
    expect(names[0].classes()).toContain("shelf-row-name--derived");
    expect(names[1].classes()).not.toContain("shelf-row-name--derived");
    // ...and the mark is announced, because a font swap is silent.
    expect(names[0].text()).toContain("name taken from the filename");
  });
});

describe("file kinds", () => {
  it("never renders an unclassified file as a checkpoint", async () => {
    // `unknown` is in neither default list (see api/modelShelf.js), so the row
    // only reaches the shelf when the Unclassified box asks for its block.
    useModelShelfStore().setFilters({
      adapters: false,
      checkpoints: false,
      unclassified: true,
    });
    const wrapper = await mountShelf([
      adapter({ file_kind: "unknown", kind: null, display_name: "aurora" }),
    ]);
    const meta = textOf(wrapper.find(".shelf-row-meta"));
    expect(meta).toContain("Unclassified");
    expect(meta).not.toContain("Checkpoint");
  });

  it("names a checkpoint as one, having no algorithm to name", async () => {
    const wrapper = await mountShelf(
      [],
      [adapter({ file_kind: "checkpoint", kind: null })],
    );
    expect(textOf(wrapper.find(".shelf-row-meta"))).toContain("Checkpoint");
  });
});

describe("location state", () => {
  it("keeps 'we could not look' visually apart from 'it is not there'", async () => {
    const wrapper = await mountShelf([
      adapter({ id: 1, locations: [{ state: "missing" }] }),
      adapter({ id: 2, locations: [{ state: "unreachable" }] }),
      adapter({ id: 3 }),
    ]);
    const slots = wrapper.findAll(".shelf-row-loc");
    expect(slots[0].classes()).toContain("shelf-row-loc--missing");
    expect(slots[1].classes()).toContain("shelf-row-loc--unreachable");
    // Present reserves its slot and shows nothing, so no row ever shifts.
    expect(slots[2].classes()).toContain("shelf-row-loc--present");
  });
});

describe("empty states", () => {
  it("says where PixlStash looks when the shelf is empty", async () => {
    // No reset offered here: resetting the filters would fix nothing, and
    // offering it would blame the user for an empty disk.
    const wrapper = await mountShelf([]);
    const state = textOf(wrapper.find(".shelf-state"));
    expect(state).toContain("No models found");
    expect(state).not.toContain("Reset filters");
  });

  it("offers a way out of a filter that matched nothing", async () => {
    const wrapper = await mountShelf([adapter({ base_model: "sdxl" })]);
    useModelShelfStore().setFilters({ baseModels: ["UNASSIGNED"] });
    await wrapper.vm.$nextTick();
    const state = textOf(wrapper.find(".shelf-state"));
    expect(state).toContain("No models match these filters");
    expect(state).toContain("Reset filters");
  });

  it("says so when Show asks for nothing at all", async () => {
    const wrapper = await mountShelf([adapter()]);
    useModelShelfStore().setFilters({
      adapters: false,
      checkpoints: false,
      unclassified: false,
    });
    await wrapper.vm.$nextTick();
    expect(textOf(wrapper.find(".shelf-state"))).toContain(
      "Nothing is selected in Show",
    );
  });
});

describe("keyboard", () => {
  it("leaves rows out of the tab order while they have no verb", async () => {
    // 1,800 empty tab stops is a trap. Roving focus arrives with the first
    // thing a focused row can do (F2/F4).
    const wrapper = await mountShelf([adapter()]);
    expect(wrapper.find(".shelf-row").attributes("tabindex")).toBeUndefined();
    expect(wrapper.find(".shelf").attributes("tabindex")).toBe("-1");
  });
});
