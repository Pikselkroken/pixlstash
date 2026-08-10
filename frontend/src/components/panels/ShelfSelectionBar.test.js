// The verb layer's control surface.
//
// The assertion worth having is the Forget gate. It is enabled by the rows'
// STATE and not by the size of the selection, and `unreachable` is the one that
// matters: it means "we could not look" (an unplugged drive), so treating it as
// a deletion would wipe the curation for a whole disk on one press.

import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

import ShelfSelectionBar from "./ShelfSelectionBar.vue";
import { useModelShelfStore } from "../../stores/useModelShelfStore";

const globalOpts = { global: { stubs: { "v-icon": true } } };

function row(id, state, overrides = {}) {
  return {
    id,
    sha256: String(id).repeat(64).slice(0, 64),
    file_kind: "adapter",
    kind: "lora",
    display_name: `Model ${id}`,
    filename: `m${id}.safetensors`,
    base_model: "SDXL 1.0",
    locations: [{ state, folder_id: 1, folder_path: "/m", relpath: "x" }],
    attachments: [],
    ...overrides,
  };
}

/** Put rows on the shelf and select them, without going near the network. */
function selectRows(rows) {
  const store = useModelShelfStore();
  store.rows = rows;
  for (const r of rows) store.toggleSelected(r.id);
  return store;
}

function verb(wrapper, label) {
  return wrapper.findAll("button").find((b) => b.text().includes(label));
}

beforeEach(() => {
  setActivePinia(createPinia());
  window.localStorage.clear();
});

describe("the selection bar", () => {
  it("is absent with nothing selected", () => {
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    expect(wrapper.find(".shelf-selbar").exists()).toBe(false);
  });

  it("offers Rename for one model and refuses it for two", async () => {
    // A name is a fact about one file; in bulk it would give every selected row
    // the same one, and the server refuses it too. Disabled rather than hidden,
    // so the row of verbs does not reflow under the pointer.
    const store = selectRows([row(1, "present")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    expect(verb(wrapper, "Rename").attributes("disabled")).toBeUndefined();

    store.rows = [...store.rows, row(2, "present")];
    store.toggleSelected(2);
    await wrapper.vm.$nextTick();
    expect(verb(wrapper, "Rename").attributes("disabled")).toBeDefined();
  });

  it("refuses Forget while a copy is still on disk", async () => {
    selectRows([row(1, "present")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    const forget = verb(wrapper, "Forget");
    expect(forget.attributes("disabled")).toBeDefined();
    expect(forget.attributes("title")).toContain("files are gone");
  });

  it("refuses Forget on a drive it could not read", async () => {
    // The one that matters. `unreachable` is not `missing`: an unplugged NAS
    // must never be one press away from losing its curation.
    selectRows([row(1, "unreachable")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    expect(verb(wrapper, "Forget").attributes("disabled")).toBeDefined();
  });

  it("offers Forget once every copy is missing", async () => {
    selectRows([row(1, "missing")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    expect(verb(wrapper, "Forget").attributes("disabled")).toBeUndefined();
  });

  it("offers Forget on a mixed selection and says how many it will take", async () => {
    // The server forgets what it can and reports the rest, so the bar must not
    // refuse the whole gesture because one file came back.
    selectRows([row(1, "missing"), row(2, "present")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    const forget = verb(wrapper, "Forget");
    expect(forget.attributes("disabled")).toBeUndefined();
    expect(forget.attributes("title")).toContain("the 1 whose files are gone");
  });

  it("emits rather than acting, so the confirmations live in one place", async () => {
    selectRows([row(1, "missing")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    await verb(wrapper, "Set base model").trigger("click");
    await verb(wrapper, "Forget").trigger("click");
    expect(wrapper.emitted("set-base-model")).toHaveLength(1);
    expect(wrapper.emitted("forget")).toHaveLength(1);
  });

  it("clears the selection without touching the rows", async () => {
    const store = selectRows([row(1, "present")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    await verb(wrapper, "Clear").trigger("click");
    expect(store.selectedRows).toHaveLength(0);
    expect(store.rows).toHaveLength(1);
  });
});
