// The verb layer's control surface.
//
// The assertion worth having is the Forget gate. It is enabled by the rows'
// STATE and not by the size of the selection, and `unreachable` is the one that
// matters: it means "we could not look" (an unplugged drive), so treating it as
// a deletion would wipe the curation for a whole disk on one press.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

import ShelfSelectionBar from "./ShelfSelectionBar.vue";
import { useModelShelfStore } from "../../stores/useModelShelfStore";

// `AddToEntityControl` is stubbed rather than mounted: it reads the shared
// entity lists on mount, and what this file has to assert about it is the props
// the bar hands it, which a stub records exactly.
const globalOpts = {
  global: { stubs: { "v-icon": true, AddToEntityControl: true } },
};

/** The Assign picker for one entity type, as the bar configured it. */
function picker(wrapper, type) {
  return wrapper
    .findAllComponents({ name: "AddToEntityControl" })
    .find((c) => c.props("type") === type);
}

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

  it("hands Assign the rows it can address, not the whole selection", async () => {
    // A checkpoint 400s on the attachment route and an unhashed row has no hash
    // to be addressed by. Handing them in anyway would compute the tri-state
    // across rows that can never be attached, so a person every adapter is
    // already assigned to would still read as partial.
    selectRows([
      row(1, "present"),
      row(2, "present", { file_kind: "checkpoint" }),
      row(3, "present", { sha256: null }),
    ]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    expect(picker(wrapper, "character").props("subjectIds")).toEqual([1]);
    expect(picker(wrapper, "character").attributes("title")).toContain(
      "the 1 of 3",
    );
  });

  it("refuses Assign when nothing in the selection can take one", async () => {
    selectRows([row(1, "present", { file_kind: "checkpoint" })]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    expect(picker(wrapper, "set").props("disabled")).toBe(true);
  });

  it("builds the membership map off the rows, so nothing is fetched", async () => {
    // `attachments` come back on the list. Supplying the map is also what
    // switches the picker out of its picture readers, which cannot answer
    // "which of these ADAPTERS is in this set" at all.
    selectRows([
      row(1, "present", {
        attachments: [
          { entity_type: "character", entity_id: 4 },
          { entity_type: "set", entity_id: 9 },
        ],
      }),
      row(2, "present", {
        attachments: [{ entity_type: "character", entity_id: 4 }],
      }),
    ]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    const byCharacter = picker(wrapper, "character").props("membership");
    expect(byCharacter["4"]).toEqual(new Set(["1", "2"]));
    expect(picker(wrapper, "set").props("membership")["9"]).toEqual(
      new Set(["1"]),
    );
    // Empty and not null: an object with no keys still means "the host owns
    // this", and null would send the picker back to reading picture membership.
    expect(picker(wrapper, "set").props("membership")["4"]).toBeUndefined();
  });

  it("passes an empty map rather than null when nothing is attached", async () => {
    selectRows([row(1, "present")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    expect(picker(wrapper, "character").props("membership")).toEqual({});
  });

  it("turns the picker's attach and detach into one store call each", async () => {
    const store = selectRows([row(1, "present")]);
    const setAttachment = vi.fn();
    store.setAttachment = setAttachment;
    const wrapper = mount(ShelfSelectionBar, globalOpts);

    picker(wrapper, "character").vm.$emit("attach", {
      entityType: "character",
      entityId: 4,
      entityName: "Alice",
      subjectIds: ["1"],
    });
    picker(wrapper, "set").vm.$emit("detach", {
      entityType: "set",
      entityId: 9,
      subjectIds: ["1"],
    });
    await wrapper.vm.$nextTick();

    expect(setAttachment.mock.calls[0][0].attach).toBe(true);
    expect(setAttachment.mock.calls[1][0]).toMatchObject({
      entityType: "set",
      entityId: 9,
      attach: false,
    });
  });

  it("says what the selection weighs, stack members included", async () => {
    // The figure is what makes a bulk verb reviewable: "Forget these 2" says
    // nothing about what is reclaimed. A stack counts every member, because the
    // verbs act on the whole run and one row stands for all of it.
    selectRows([
      row(1, "present", { file_size: 1024 * 1024 * 200 }),
      row(2, "present", {
        file_size: 1024 * 1024 * 100,
        stack_id: 7,
        stack_position: 0,
      }),
      row(3, "present", {
        file_size: 1024 * 1024 * 100,
        stack_id: 7,
        stack_position: 1,
      }),
    ]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    // Two rows drawn (the stack folded into one), three files counted.
    expect(wrapper.find(".shelf-selbar-count").text()).toBe(
      "2 models selected · 400.0 MB",
    );
  });

  it("states the count alone when no size is recorded", async () => {
    // A file the hash worker has not reached has no size. `0 B` would claim the
    // selection is empty, which is a different and wrong statement.
    selectRows([row(1, "present")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    expect(wrapper.find(".shelf-selbar-count").text()).toBe("1 model selected");
  });

  it("offers Stack for two present adapters in one folder", async () => {
    selectRows([row(1, "present"), row(2, "present")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    await verb(wrapper, "Stack these").trigger("click");
    expect(wrapper.emitted("stack")).toHaveLength(1);
  });

  it("refuses Stack across folders, which would invent a run", async () => {
    // The gate the route enforces in `apply_stack`: a run is files that sit
    // together, so stacking across two drives would create one that never was.
    selectRows([
      row(1, "present"),
      row(2, "present", {
        locations: [{ state: "present", folder_id: 2, relpath: "y" }],
      }),
    ]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    const stack = verb(wrapper, "Stack these");
    expect(stack.attributes("disabled")).toBeDefined();
    expect(stack.attributes("title")).toContain("one folder");
  });

  it("names the missing file, not the folders, when a copy is not there", async () => {
    // The two refusals are different repairs. An unplugged drive is fixed by
    // plugging it in; being told the files are in different folders would send
    // the reader to move something instead, and they are in one folder.
    selectRows([row(1, "present"), row(2, "unreachable")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    const stack = verb(wrapper, "Stack these");
    expect(stack.attributes("disabled")).toBeDefined();
    expect(stack.attributes("title")).toContain("on this machine");
    expect(stack.attributes("title")).not.toContain("folder");
  });

  it("refuses Stack on one model, a checkpoint, or a row already in a run", async () => {
    const store = selectRows([row(1, "present")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    expect(verb(wrapper, "Stack these").attributes("disabled")).toBeDefined();

    store.rows = [
      ...store.rows,
      row(2, "present", { file_kind: "checkpoint" }),
    ];
    store.toggleSelected(2);
    await wrapper.vm.$nextTick();
    expect(verb(wrapper, "Stack these").attributes("title")).toContain(
      "Only adapters",
    );

    store.rows = [row(1, "present"), row(3, "present", { stack_id: 7 })];
    store.clearSelection();
    store.toggleSelected(1);
    store.toggleSelected(3);
    await wrapper.vm.$nextTick();
    expect(verb(wrapper, "Stack these").attributes("title")).toContain(
      "already part of a run",
    );
  });

  it("clears the selection without touching the rows", async () => {
    const store = selectRows([row(1, "present")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    await verb(wrapper, "Clear").trigger("click");
    expect(store.selectedRows).toHaveLength(0);
    expect(store.rows).toHaveLength(1);
  });
});
