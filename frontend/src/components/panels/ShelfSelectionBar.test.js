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
import { useModelFoldersStore } from "../../stores/useModelFoldersStore";
import { useModelShelfStore } from "../../stores/useModelShelfStore";

// `AddToEntityControl` is stubbed rather than mounted: it reads the shared
// entity lists on mount, and what this file has to assert about it is the props
// the bar hands it, which a stub records exactly.
//
// `v-menu` is stubbed because Vuetify is not installed in these mounts, and an
// UNRESOLVED component renders its default slot and silently drops its named
// ones - so the count button and the Assign button, which are `#activator`
// content, would not exist at all and every assertion about them would pass
// vacuously. The stub renders both slots, which is what the real menu does once
// it is open.
const VMenuStub = {
  name: "VMenu",
  props: ["modelValue"],
  template:
    '<div class="v-menu-stub">' +
    '<slot name="activator" :props="{}" /><slot /></div>',
};

const globalOpts = {
  global: {
    stubs: { "v-icon": true, AddToEntityControl: true, "v-menu": VMenuStub },
  },
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

/**
 * One verb on the pill, by name rather than by its label.
 *
 * The pill's verbs are ICONS with their words in the tooltip and in the context
 * menu (#904), so there is no text to match on - `data-verb` is the hook, and
 * it is also what stops a test passing because two buttons happened to share a
 * word.
 */
function verb(wrapper, name) {
  return wrapper.find(`[data-verb="${name}"]`);
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
    expect(verb(wrapper, "rename").attributes("disabled")).toBeUndefined();

    store.rows = [...store.rows, row(2, "present")];
    store.toggleSelected(2);
    await wrapper.vm.$nextTick();
    expect(verb(wrapper, "rename").attributes("disabled")).toBeDefined();
  });

  it("refuses Forget while a copy is still on disk", async () => {
    selectRows([row(1, "present")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    const forget = verb(wrapper, "forget");
    expect(forget.attributes("disabled")).toBeDefined();
    expect(forget.attributes("title")).toContain("files are gone");
  });

  it("refuses Forget on a drive it could not read", async () => {
    // The one that matters. `unreachable` is not `missing`: an unplugged NAS
    // must never be one press away from losing its curation.
    selectRows([row(1, "unreachable")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    expect(verb(wrapper, "forget").attributes("disabled")).toBeDefined();
  });

  it("offers Forget once every copy is missing", async () => {
    selectRows([row(1, "missing")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    expect(verb(wrapper, "forget").attributes("disabled")).toBeUndefined();
  });

  it("offers Forget on a mixed selection and says how many it will take", async () => {
    // The server forgets what it can and reports the rest, so the bar must not
    // refuse the whole gesture because one file came back.
    selectRows([row(1, "missing"), row(2, "present")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    const forget = verb(wrapper, "forget");
    expect(forget.attributes("disabled")).toBeUndefined();
    expect(forget.attributes("title")).toContain("the 1 whose files are gone");
  });

  it("names every icon verb, and does not lean on the tooltip to do it", async () => {
    // The pill's verbs are icons, so `aria-label` is the only thing naming
    // them - and it has to be the VERB, stable across selections, because the
    // `title` beside it is the refusal and changes with what is selected.
    // `title` is not a reliable accessible name and does not exist on touch.
    selectRows([row(1, "present")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    const verbs = wrapper.findAll("[data-verb]");
    expect(verbs.length).toBeGreaterThan(0);
    for (const button of verbs) {
      expect(button.attributes("aria-label")).toBeTruthy();
    }
    // Stable: the tooltip moves with the selection, the name does not.
    const before = wrapper.find('[data-verb="move"]').attributes("aria-label");
    selectRows([row(2, "missing")]);
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-verb="move"]').attributes("aria-label")).toBe(
      before,
    );
  });

  it("emits rather than acting, so the confirmations live in one place", async () => {
    // The two confirmations (Forget, Stack) are the view's, and this is what
    // keeps them there: the pill never calls a store verb of its own.
    selectRows([row(1, "missing"), row(2, "missing")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    await verb(wrapper, "forget").trigger("click");
    await verb(wrapper, "rename").trigger("click");
    expect(wrapper.emitted("forget")).toHaveLength(1);
    // Rename is disabled at two rows, so nothing is emitted - which is the
    // other half of the same contract.
    expect(wrapper.emitted("rename")).toBeUndefined();
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
    // The pill shows the figures and the sentence is in the tooltip: it floats
    // over the list and every character of it costs a row underneath.
    expect(wrapper.find(".selbar-count").text()).toBe("2· 400.0 MB");
    expect(wrapper.find(".selbar-count").attributes("title")).toBe(
      "2 models selected · 400.0 MB",
    );
  });

  it("states the count alone when no size is recorded", async () => {
    // A file the hash worker has not reached has no size. `0 B` would claim the
    // selection is empty, which is a different and wrong statement.
    selectRows([row(1, "present")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    expect(wrapper.find(".selbar-count").text()).toBe("1");
    expect(wrapper.find(".selbar-count").attributes("title")).toBe(
      "1 model selected",
    );
  });

  it("offers Stack for two present adapters in one folder", async () => {
    selectRows([row(1, "present"), row(2, "present")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    await verb(wrapper, "stack").trigger("click");
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
    const stack = verb(wrapper, "stack");
    expect(stack.attributes("disabled")).toBeDefined();
    expect(stack.attributes("title")).toContain("one folder");
  });

  it("names the missing file, not the folders, when a copy is not there", async () => {
    // The two refusals are different repairs. An unplugged drive is fixed by
    // plugging it in; being told the files are in different folders would send
    // the reader to move something instead, and they are in one folder.
    selectRows([row(1, "present"), row(2, "unreachable")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    const stack = verb(wrapper, "stack");
    expect(stack.attributes("disabled")).toBeDefined();
    expect(stack.attributes("title")).toContain("on this machine");
    expect(stack.attributes("title")).not.toContain("folder");
  });

  it("refuses Stack on one model or a checkpoint", async () => {
    const store = selectRows([row(1, "present")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    expect(verb(wrapper, "stack").attributes("disabled")).toBeDefined();

    store.rows = [
      ...store.rows,
      row(2, "present", { file_kind: "checkpoint" }),
    ];
    store.toggleSelected(2);
    await wrapper.vm.$nextTick();
    expect(verb(wrapper, "stack").attributes("title")).toContain(
      "Only adapters",
    );
  });

  it("OFFERS Stack on a row that is already stacked, as a fuse", async () => {
    // This used to be a refusal - "something here is already part of a run" -
    // and it was the gate that made stacking two stacks impossible. Fusing is
    // the operation the bar now exists to offer, so the button has to be live
    // and has to say which of the two things it will do.
    selectRows([row(1, "present"), row(3, "present", { stack_id: 7 })]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    await wrapper.vm.$nextTick();

    expect(verb(wrapper, "stack").attributes("disabled")).toBeUndefined();
    expect(verb(wrapper, "stack").attributes("title")).toContain("Fuse");
  });

  it("refuses Ungroup unless everything selected is in a stack", async () => {
    const store = selectRows([row(1, "present")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    expect(verb(wrapper, "unstack").attributes("disabled")).toBeDefined();
    expect(verb(wrapper, "unstack").attributes("title")).toContain(
      "not part of a stack",
    );

    store.rows = [row(3, "present", { stack_id: 7 })];
    store.clearSelection();
    store.toggleSelected(3);
    await wrapper.vm.$nextTick();
    expect(verb(wrapper, "unstack").attributes("disabled")).toBeUndefined();
  });

  it("clears the selection without touching the rows", async () => {
    const store = selectRows([row(1, "present")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    // Clear lives under the count, which is the pill's one dropdown: "I meant
    // all of them" and "never mind" are the two things you say about a
    // selection, so they sit on the selection itself.
    const clear = wrapper
      .findAll(".shelf-mi")
      .find((item) => item.text().includes("Clear selection"));
    await clear.trigger("click");
    expect(store.selectedRows).toHaveLength(0);
    expect(store.rows).toHaveLength(1);
  });
});

describe("the delete verb", () => {
  /**
   * Register folder 1, which every `row()` above puts its copy in.
   *
   * `deletable` is the server's, and not derivable from `kind`: PixlStash's own
   * download folder is `foreign` and deletable, the HuggingFace cache is
   * `foreign` and not.
   */
  function registerFolder(kind = "user", deletable = kind === "user") {
    const folders = useModelFoldersStore();
    folders.folders = [
      { id: 1, path: "/m", kind, movable: "per_item", owner: null, deletable },
    ];
    return folders;
  }

  it("offers Delete for a file in the owner's own folder", () => {
    registerFolder("user");
    selectRows([row(1, "present")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    const del = verb(wrapper, "delete");
    expect(del.attributes("disabled")).toBeUndefined();
    expect(del.attributes("aria-label")).toBe("Move to Trash");
  });

  it("refuses a file in a folder PixlStash keeps for itself", () => {
    // The HuggingFace cache is a symlink store shared with every other tool on
    // the machine. The shelf lists it so the owner can see what it costs, not
    // so the shelf can unlink from it.
    registerFolder("foreign");
    selectRows([row(1, "present")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    const del = verb(wrapper, "delete");
    expect(del.attributes("disabled")).toBeDefined();
    expect(del.attributes("title")).toContain("your own folders");
  });

  it("refuses a copy on a drive it could not read", () => {
    // `unreachable` is "we could not look", and the bytes are still out there.
    registerFolder("user");
    selectRows([row(1, "unreachable")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    expect(verb(wrapper, "delete").attributes("disabled")).toBeDefined();
  });

  it("refuses the whole model when one of its copies is untouchable", () => {
    // Deleted whole or not at all: unlinking the reachable half would leave the
    // row the owner wanted gone still on the shelf.
    const folders = registerFolder("user");
    folders.folders = [
      ...folders.folders,
      { id: 2, path: "/hf", kind: "foreign", movable: "fixed", owner: "hf" },
    ];
    selectRows([
      row(1, "present", {
        locations: [
          { state: "present", folder_id: 1, relpath: "a" },
          { state: "present", folder_id: 2, relpath: "b" },
        ],
      }),
    ]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    expect(verb(wrapper, "delete").attributes("disabled")).toBeDefined();
  });

  it("says Permanently delete while Shift is held", async () => {
    registerFolder("user");
    selectRows([row(1, "present")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);

    window.dispatchEvent(new KeyboardEvent("keydown", { shiftKey: true }));
    await wrapper.vm.$nextTick();
    const del = verb(wrapper, "delete");
    expect(del.attributes("aria-label")).toBe("Permanently delete");
    expect(del.attributes("title")).toContain("no undo");

    window.dispatchEvent(new KeyboardEvent("keyup", { shiftKey: false }));
    await wrapper.vm.$nextTick();
    expect(verb(wrapper, "delete").attributes("aria-label")).toBe(
      "Move to Trash",
    );
  });

  it("reads permanence off the press, not off the tracked key state", async () => {
    // The label may be a moment stale - a blur with Shift down never fires its
    // keyup - and a stale label must never turn a trash into an unlink.
    registerFolder("user");
    selectRows([row(1, "present")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);

    await verb(wrapper, "delete").trigger("click");
    expect(wrapper.emitted("delete")).toEqual([[false]]);

    await verb(wrapper, "delete").trigger("click", { shiftKey: true });
    expect(wrapper.emitted("delete")[1]).toEqual([true]);
  });

  it("puts the same verb in the context menu, in the danger treatment", async () => {
    registerFolder("user");
    selectRows([row(1, "present")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    const item = wrapper
      .findAll(".shelf-mi")
      .find((mi) => mi.text().includes("Move to Trash"));
    expect(item.classes()).toContain("shelf-mi--danger");

    await item.trigger("click", { shiftKey: true });
    expect(wrapper.emitted("delete")).toEqual([[true]]);
  });
});

describe("Open in file manager", () => {
  // The verb acts on the machine PixlStash runs on rather than on the library,
  // so what this file can assert is the gate and the emit: whether the item is
  // offered, and that pressing it hands the decision to the view.
  const item = (wrapper) =>
    wrapper
      .findAll(".shelf-mi")
      .find((mi) => mi.text().includes("Open in file manager"));

  it("is offered for one model whose file is actually there", () => {
    selectRows([row(1, "present")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    expect(item(wrapper).attributes("disabled")).toBeUndefined();
  });

  it("is offered when ONE of several copies is there", () => {
    // A model catalogued in two folders, one of them on a drive that is not
    // plugged in: the present copy is what opens, so the verb is live. `every`
    // rather than `some` would refuse it and there would be no way to reach a
    // file that is sitting right there.
    selectRows([
      row(1, "present", {
        locations: [
          {
            state: "unreachable",
            folder_id: 2,
            folder_path: "/nas",
            relpath: "x",
          },
          { state: "present", folder_id: 1, folder_path: "/m", relpath: "x" },
        ],
      }),
    ]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    expect(item(wrapper).attributes("disabled")).toBeUndefined();
  });

  it("is refused when the copy is missing or its drive is unplugged", async () => {
    for (const state of ["missing", "unreachable"]) {
      setActivePinia(createPinia());
      selectRows([row(1, state)]);
      const wrapper = mount(ShelfSelectionBar, globalOpts);
      expect(item(wrapper).attributes("disabled")).toBeDefined();
      expect(item(wrapper).attributes("title")).toContain("no copy of this");
    }
  });

  it("is a single-selection verb: forty rows would be forty windows", () => {
    selectRows([row(1, "present"), row(2, "present")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    expect(item(wrapper)).toBeUndefined();
  });

  it("emits rather than calling, like every other verb here", async () => {
    selectRows([row(1, "present")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    await item(wrapper).trigger("click");
    expect(wrapper.emitted("open-location")).toHaveLength(1);
  });
});

describe("the two verbs that act inside a run", () => {
  // Cover and Take-out are the only verbs whose subject is ONE file of a
  // stack, so the gate they share is the one worth asserting: a whole run
  // selected is somebody else's business (Ungroup's), and a loose row is in no
  // run at all.

  /** A two-member run on the shelf, with only the ids given selected. */
  function selectInRun(ids) {
    const store = useModelShelfStore();
    store.rows = [
      row(1, "present", { stack_id: 7, stack_position: 0 }),
      row(2, "present", { stack_id: 7, stack_position: 1 }),
    ];
    store.clearSelection();
    for (const id of ids) store.toggleSelected(id);
    return store;
  }

  function menuItem(wrapper, text) {
    return wrapper.findAll(".shelf-mi").find((b) => b.text().includes(text));
  }

  it("offers both verbs on one member of a run", () => {
    selectInRun([2]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    expect(wrapper.vm.coverable).toBe(true);
    expect(wrapper.vm.releasable).toBe(true);
    expect(
      menuItem(wrapper, "Make this the cover").attributes("disabled"),
    ).toBeUndefined();
    expect(
      menuItem(wrapper, "Take out of this run").attributes("disabled"),
    ).toBeUndefined();
  });

  it("refuses to make the cover the cover, and says so rather than hiding", () => {
    selectInRun([1]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    expect(wrapper.vm.coverable).toBe(false);
    const entry = menuItem(wrapper, "Make this the cover");
    expect(entry.attributes("disabled")).toBeDefined();
    expect(entry.attributes("title")).toContain("already stands for its run");
    // Still releasable: the cover is a file of the run like any other, and
    // taking it out promotes the one behind it.
    expect(wrapper.vm.releasable).toBe(true);
  });

  it("refuses both on a whole run, and points at Ungroup", () => {
    // Every member selected IS the run - that is what selecting a collapsed
    // row does - and "take a run out of itself" is not a gesture.
    selectInRun([1, 2]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    expect(wrapper.vm.selectedMembers).toHaveLength(0);
    expect(wrapper.vm.coverable).toBe(false);
    expect(wrapper.vm.releasable).toBe(false);
    expect(
      menuItem(wrapper, "Take out of this run").attributes("title"),
    ).toContain("Ungroup");
  });

  it("refuses a cover on two files at once: a run has one", () => {
    const store = useModelShelfStore();
    store.rows = [
      row(1, "present", { stack_id: 7, stack_position: 0 }),
      row(2, "present", { stack_id: 7, stack_position: 1 }),
      row(3, "present", { stack_id: 7, stack_position: 2 }),
    ];
    store.clearSelection();
    store.toggleSelected(2);
    store.toggleSelected(3);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    expect(wrapper.vm.coverable).toBe(false);
    // Taking two files out at once is fine, though: they are two files.
    expect(wrapper.vm.releasable).toBe(true);
    expect(menuItem(wrapper, "Take out of their runs")).toBeDefined();
  });

  it("refuses to Ungroup a run the reader only picked one file of", () => {
    // The gate `stack_id != null` is true of a member too, so on its own it
    // would let picking one checkpoint break up the whole run of six. Take-out
    // is the verb for one file; this one is for the run.
    selectInRun([2]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    expect(wrapper.vm.unstackable).toBe(false);
    expect(verb(wrapper, "unstack").attributes("title")).toContain(
      "select the run itself",
    );

    // ...and the run itself still ungroups, which is the positive control.
    selectInRun([1, 2]);
    const whole = mount(ShelfSelectionBar, globalOpts);
    expect(whole.vm.unstackable).toBe(true);
  });

  it("refuses on a loose row, because there is no run to act inside", () => {
    selectRows([row(1, "present")]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    expect(wrapper.vm.coverable).toBe(false);
    expect(wrapper.vm.releasable).toBe(false);
    expect(
      menuItem(wrapper, "Take out of this run").attributes("title"),
    ).toContain("not part of a run");
  });

  it("emits rather than calling, like every other verb here", async () => {
    selectInRun([2]);
    const wrapper = mount(ShelfSelectionBar, globalOpts);
    await menuItem(wrapper, "Make this the cover").trigger("click");
    await menuItem(wrapper, "Take out of this run").trigger("click");
    expect(wrapper.emitted("make-cover")).toHaveLength(1);
    expect(wrapper.emitted("remove-from-stack")).toHaveLength(1);
  });
});
