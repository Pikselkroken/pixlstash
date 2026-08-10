// Three verbs, one dialog.
//
// The assertions worth having are the two the route's shape depends on: the
// dialog sends ONLY the field its verb owns (so Set base model cannot blank the
// names in the selection), and the bulk overwrite warns by counting the values
// it is about to destroy rather than the rows the reader can already see.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

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
