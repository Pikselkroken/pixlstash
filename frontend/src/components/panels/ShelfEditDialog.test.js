// Three verbs, one dialog.
//
// The assertions worth having are the two the route's shape depends on: the
// dialog sends ONLY the field its verb owns (so Set base model cannot blank the
// names in the selection), and the bulk overwrite warns by counting the values
// it is about to destroy rather than the rows the reader can already see.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

// The base-model field asks the server for its completion list the moment
// somebody types in it. Left real that is a live request from a unit test, and
// the failure lands as a console line after teardown - which kills the whole
// run, not just this file (see the note in ModelShelf.test.js).
vi.mock("../../api/modelShelf", () => ({
  BASE_MODEL_UNASSIGNED: "UNASSIGNED",
  listAdapters: vi.fn().mockResolvedValue([]),
  listCheckpoints: vi.fn().mockResolvedValue([]),
  listBaseModelCompletions: vi.fn().mockResolvedValue([]),
  editModels: vi.fn().mockResolvedValue({ updated: [], fields: [] }),
  forgetModels: vi.fn().mockResolvedValue({ forgotten: [], refused: [] }),
  setAdapterAttachments: vi.fn().mockResolvedValue({}),
}));

import ShelfEditDialog from "./ShelfEditDialog.vue";
import { useModelShelfStore } from "../../stores/useModelShelfStore";

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      // The shell teleports and is AppDialog's own contract; the footer slot
      // has to render or the submit button is unreachable here.
      AppDialog: {
        props: ["open", "title", "subtitle"],
        template:
          "<div v-if='open'><h2>{{ title }}</h2><p>{{ subtitle }}</p><slot /><slot name='footer' /></div>",
      },
    },
  },
};

function row(id, overrides = {}) {
  return {
    id,
    sha256: String(id).repeat(64).slice(0, 64),
    file_kind: "adapter",
    kind: "lora",
    display_name: `Model ${id}`,
    filename: `m${id}.safetensors`,
    base_model: "SDXL 1.0",
    locations: [
      { state: "present", folder_id: 1, folder_path: "/m", relpath: "x" },
    ],
    attachments: [],
    ...overrides,
  };
}

/**
 * Put rows on the shelf and select them, without going near the network.
 *
 * The `Show` selection is widened to include unclassified files, because that
 * is the only way a row with `file_kind: "unknown"` is on screen to be selected
 * at all: the shelf does not list them by default, and the selection is drawn
 * from the VISIBLE rows. Correcting an unknown is exactly the flow that has to
 * tick that box first.
 */
function select(rows) {
  const store = useModelShelfStore();
  store.filters.unclassified = true;
  store.rows = rows;
  for (const r of rows) store.toggleSelected(r.id);
  store.editSelected = vi.fn().mockResolvedValue(true);
  return store;
}

function submitButton(wrapper) {
  return wrapper
    .findAll("button")
    .find((b) => /Rename|Apply to/.test(b.text()));
}

beforeEach(() => {
  setActivePinia(createPinia());
  window.localStorage.clear();
});

describe("what the dialog sends", () => {
  it("sends the base model alone, never the name beside it", async () => {
    const store = select([row(1), row(2)]);
    const wrapper = mount(ShelfEditDialog, {
      ...globalOpts,
      props: { verb: "base-model" },
    });
    await wrapper.vm.$nextTick();

    await wrapper.find("input[type=text]").setValue("FLUX.2");
    await submitButton(wrapper).trigger("click");

    expect(store.editSelected).toHaveBeenCalledWith({ base_model: "FLUX.2" });
  });

  it("clears a column with an explicit null rather than an empty string", async () => {
    // "" would be stored and would then be neither a value nor unset, which is
    // the state the `Needs a name` queue exists to exclude.
    const store = select([row(1)]);
    const wrapper = mount(ShelfEditDialog, {
      ...globalOpts,
      props: { verb: "rename" },
    });
    await wrapper.vm.$nextTick();

    await wrapper.find("input[type=text]").setValue("   ");
    await submitButton(wrapper).trigger("click");

    expect(store.editSelected).toHaveBeenCalledWith({ display_name: null });
  });

  it("seeds the algorithm from two rows the shelf calls one algorithm", async () => {
    // `model.kind` is free text, and the shelf now folds it: `lora` and `LoRA`
    // draw one group header, one Show checkbox, and two Kind cells both
    // reading `LoRA`. Comparing raw here opened this field BLANK - which means
    // "they disagree" - on a selection the rest of the screen presents as one
    // thing. Seeded with the first row's own spelling, so saving converges it.
    select([row(1, { kind: "lora" }), row(2, { kind: "LoRA" })]);
    const wrapper = mount(ShelfEditDialog, {
      ...globalOpts,
      props: { verb: "kind" },
    });
    await wrapper.vm.$nextTick();

    expect(wrapper.find("input[type=text]").element.value).toBe("lora");
  });

  it("still blanks the algorithm when the rows really do disagree", async () => {
    // The other direction: over-folding would silently offer one algorithm as
    // the shared value of a mixed selection and overwrite the other on save.
    select([row(1, { kind: "lora" }), row(2, { kind: "lokr" })]);
    const wrapper = mount(ShelfEditDialog, {
      ...globalOpts,
      props: { verb: "kind" },
    });
    await wrapper.vm.$nextTick();

    expect(wrapper.find("input[type=text]").element.value).toBe("");
  });

  it("asks for an algorithm before it will call a file an adapter", async () => {
    // The hub carries `CHECK (file_kind <> 'adapter' OR kind IS NOT NULL)`, so
    // without one the request is refused. The button is the honest place to say
    // so rather than the error that follows.
    select([row(1, { file_kind: "unknown", kind: null })]);
    const wrapper = mount(ShelfEditDialog, {
      ...globalOpts,
      props: { verb: "kind" },
    });
    await wrapper.vm.$nextTick();

    await wrapper.findAll("input[type=radio]")[0].setValue();
    await wrapper.vm.$nextTick();
    expect(submitButton(wrapper).attributes("disabled")).toBeDefined();

    await wrapper.find("input[type=text]").setValue("lokr");
    await wrapper.vm.$nextTick();
    expect(submitButton(wrapper).attributes("disabled")).toBeUndefined();
  });

  it("will not call a MIXED selection already-kinded off the one row that is", async () => {
    // `.every`, never `.some`. At one row the two are the same, which is what
    // every other case here selects; the guard only does work at two. With a
    // blank field, `.some` would enable Save and send `file_kind` alone -
    // leaving the second row with the non-algorithm it started with, under a
    // receipt reading "Set the algorithm on 2 models".
    select([
      row(1, { file_kind: "adapter", kind: "lora" }),
      row(2, { file_kind: "adapter", kind: "  " }),
    ]);
    const wrapper = mount(ShelfEditDialog, {
      ...globalOpts,
      props: { verb: "kind" },
    });
    await wrapper.vm.$nextTick();

    expect(wrapper.find("input[type=text]").element.value).toBe("");
    expect(submitButton(wrapper).attributes("disabled")).toBeDefined();
  });

  it("does not count a whitespace-only kind as an algorithm already on file", async () => {
    // The CHECK is `kind IS NOT NULL`, not `kind <> ''`, so `"  "` satisfies
    // the database and satisfies nobody else. Read raw it is truthy - Save
    // goes live on a verb that would write only `file_kind` and leave the row
    // with the same non-algorithm it had.
    select([row(1, { file_kind: "adapter", kind: "  " })]);
    const wrapper = mount(ShelfEditDialog, {
      ...globalOpts,
      props: { verb: "kind" },
    });
    await wrapper.vm.$nextTick();

    expect(wrapper.find("input[type=text]").element.value).toBe("  ");
    expect(submitButton(wrapper).attributes("disabled")).toBeDefined();
  });

  it("sets the features a model serves, which the Kind column already showed", async () => {
    // The reported gap: `Captioning`, `Faces` and `Detection` are what the Kind
    // column reads on a classified row, and the editor offered only file kinds
    // - a value on screen the owner could not correct.
    const store = select([row(1, { file_kind: "unknown", kind: null })]);
    const wrapper = mount(ShelfEditDialog, {
      ...globalOpts,
      props: { verb: "kind" },
    });
    await wrapper.vm.$nextTick();

    const boxes = wrapper.findAll("input[type=checkbox]");
    expect(boxes).toHaveLength(6);
    await boxes[0].setValue(true); // Captioning
    await boxes[2].setValue(true); // Detection
    await submitButton(wrapper).trigger("click");

    expect(store.editSelected).toHaveBeenCalledWith({
      file_kind: "unknown",
      // Ordered as ticked, so the first box is the word the Kind column shows.
      capabilities: ["captioner", "detector"],
    });
  });

  it("leaves the features alone when the reader did not touch them", async () => {
    // Set kind is opened to change the file kind far more often than the
    // features. A dialog that always posted the set would flatten a mixed
    // selection onto whatever the first row happened to say.
    const store = select([
      row(1, { capabilities: ["captioner"] }),
      row(2, { capabilities: ["face"] }),
    ]);
    const wrapper = mount(ShelfEditDialog, {
      ...globalOpts,
      props: { verb: "kind" },
    });
    await wrapper.vm.$nextTick();

    await submitButton(wrapper).trigger("click");

    expect(store.editSelected).toHaveBeenCalledWith({
      file_kind: "adapter",
      kind: "lora",
    });
  });

  it("seeds the boxes from the selection and says when it cannot", async () => {
    select([
      row(1, { capabilities: ["face"] }),
      row(2, { capabilities: ["face"] }),
    ]);
    const agreed = mount(ShelfEditDialog, {
      ...globalOpts,
      props: { verb: "kind" },
    });
    await agreed.vm.$nextTick();
    expect(
      agreed.findAll("input[type=checkbox]").filter((b) => b.element.checked),
    ).toHaveLength(1);
    expect(agreed.text()).not.toContain("not filed the same way");

    // An empty box that means "they differ" and an empty box that means "none"
    // are the same box, so the mixed case says which it is. A fresh pinia,
    // because `select` TOGGLES: re-selecting the same ids on the same store
    // would deselect them and mount the dialog over nothing.
    setActivePinia(createPinia());
    select([row(1, { capabilities: ["face"] }), row(2, { capabilities: [] })]);
    const mixed = mount(ShelfEditDialog, {
      ...globalOpts,
      props: { verb: "kind" },
    });
    await mixed.vm.$nextTick();
    expect(
      mixed.findAll("input[type=checkbox]").filter((b) => b.element.checked),
    ).toHaveLength(0);
    expect(mixed.text()).toContain("not filed the same way");
  });
});

describe("the bulk overwrite warning", () => {
  it("counts the values it will destroy, not the rows on screen", async () => {
    // "12 selected" is something the reader can already see. The number that
    // decides whether this was a mistake is how many recorded base models are
    // about to be gone, and there is no undo behind it.
    select([row(1), row(2), row(3, { base_model: null })]);
    const wrapper = mount(ShelfEditDialog, {
      ...globalOpts,
      props: { verb: "base-model" },
    });
    await wrapper.find("input[type=text]").setValue("FLUX.2");
    await wrapper.vm.$nextTick();

    const warning = wrapper.find(".sed-warning");
    expect(warning.text()).toContain("2 of them");
    expect(warning.text()).toContain("no undo");
  });

  it("stays quiet for a single model", async () => {
    // One row is not a bulk overwrite, and a prompt on every edit is how people
    // learn to click through prompts.
    select([row(1)]);
    const wrapper = mount(ShelfEditDialog, {
      ...globalOpts,
      props: { verb: "base-model" },
    });
    await wrapper.find("input[type=text]").setValue("FLUX.2");
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".sed-warning").exists()).toBe(false);
  });
});
