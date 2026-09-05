// Choose a layout - the dialog off the active library's overflow menu.
//
// The behaviours pinned here are the ones that cost data or trust when they
// regress: the layout being frozen for the duration of a move, the Undo
// surviving a re-count, and the Move button being inert while the count on the
// bar does not describe the layout on screen. The rest pins the shape the
// owner asked for - four coloured levels on one line, and a tree that is the
// argument for the layout rather than a count.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import { useLibrariesStore } from "../../stores/useLibrariesStore";

vi.mock("vuetify/components", () => ({
  VIcon: { template: "<i><slot /></i>" },
  VCheckbox: {
    props: { modelValue: Boolean, disabled: Boolean, label: String },
    emits: ["update:modelValue"],
    template:
      '<label><input type="checkbox" :checked="modelValue" :disabled="disabled" @change="$emit(\'update:modelValue\', $event.target.checked)" />{{ label }}</label>',
  },
  VTextField: {
    props: { modelValue: String, disabled: Boolean },
    emits: ["update:modelValue"],
    template:
      '<input :value="modelValue" :disabled="disabled" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
  VSelect: {
    name: "v-select",
    // Typed rather than a name list: Vuetify declares these Boolean, so a bare
    // `persistent-placeholder` attribute coerces to true. An untyped stub keeps
    // it as "" and the assertion would be testing the stub, not the component.
    props: {
      modelValue: { type: Array, default: () => [] },
      items: { type: Array, default: () => [] },
      label: { type: String, default: "" },
      placeholder: { type: String, default: "" },
      disabled: { type: Boolean, default: false },
      persistentPlaceholder: { type: Boolean, default: false },
    },
    emits: ["update:modelValue"],
    template:
      '<select :data-level="label" :disabled="disabled"><slot name="selection" :index="0" /></select>',
  },
}));

const getLayoutSettings = vi.fn();
const setLayoutSettings = vi.fn();
const getLayoutMigrationPreview = vi.fn();
const runLayoutMigrationPass = vi.fn();
vi.mock("../../api/serverConfig", () => ({
  getLayoutSettings: (...a) => getLayoutSettings(...a),
  setLayoutSettings: (...a) => setLayoutSettings(...a),
  getLayoutMigrationPreview: (...a) => getLayoutMigrationPreview(...a),
  runLayoutMigrationPass: (...a) => runLayoutMigrationPass(...a),
}));

const undoBatchById = vi.fn();
vi.mock("../../stores/useOperationStore", () => ({
  useOperationStore: () => ({ undoBatchById }),
}));

import LibraryLayoutDialog from "./LibraryLayoutDialog.vue";

const WITH_LAYOUT = {
  layout: "project/person,set",
  layout_unfiled: "Unassigned",
  default_layout: "project/person,set",
};
const FOUR_LEVELS = { ...WITH_LAYOUT, layout: "project/person/set/tag" };

const NOTHING_TO_MOVE = {
  picture_count: 0,
  folder_count: 0,
  samples: [],
  collision_count: 0,
  collisions: [],
  cross_volume_count: 0,
  skipped_counts: {},
  tree: [],
};
const WOULD_MOVE = {
  ...NOTHING_TO_MOVE,
  picture_count: 4109,
  folder_count: 312,
  tree: [
    {
      path: "Harbour Nights",
      name: "Harbour Nights",
      depth: 0,
      have: 1204,
      arriving: 312,
      leaving: 0,
      is_new: false,
    },
    {
      path: "Harbour Nights/Nova",
      name: "Nova",
      depth: 1,
      have: 0,
      arriving: 488,
      leaving: 0,
      is_new: true,
    },
    {
      path: "Studies",
      name: "Studies",
      depth: 0,
      have: 2410,
      arriving: 0,
      leaving: 0,
      is_new: false,
    },
    {
      path: "Unassigned",
      name: "Unassigned",
      depth: 0,
      have: 903,
      arriving: 0,
      leaving: 480,
      is_new: false,
    },
  ],
};

const PASS_DONE = {
  batch_id: "srv-layout-migration-0123456789abcdef",
  moved_count: 12,
  next_after_id: 12,
  done: true,
};

const AppDialogStub = {
  props: ["open", "title", "width", "persistent"],
  template:
    "<div v-if='open' class='dlg' :data-width='width'><h2>{{ title }}</h2>" +
    "<div class='dlg-body'><slot /></div>" +
    "<footer class='dlg-foot'><slot name='footer' /></footer></div>",
};

const AppButtonStub = {
  props: ["disabled", "loading", "variant", "size", "iconLeft"],
  emits: ["click"],
  template:
    '<button :disabled="disabled || loading" :data-variant="variant" @click="$emit(\'click\')"><slot /></button>',
};

function mountDialog() {
  const store = useLibrariesStore();
  store.canManage = true;
  store.hasLoadedSuccessfully = true;
  return mount(LibraryLayoutDialog, {
    props: { open: true },
    global: {
      stubs: { AppDialog: AppDialogStub, AppButton: AppButtonStub },
    },
  });
}

/** Drive one level's dropdown the way `v-select multiple` does. */
function editLevel(wrapper, index, facets) {
  const selects = wrapper.findAllComponents({ name: "v-select" });
  selects[index].vm.$emit("update:modelValue", facets);
}

function buttonWith(wrapper, text) {
  return wrapper.findAll("button").find((b) => b.text().includes(text));
}

describe("LibraryLayoutDialog", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    for (const fn of [
      getLayoutSettings,
      setLayoutSettings,
      getLayoutMigrationPreview,
      runLayoutMigrationPass,
      undoBatchById,
    ]) {
      fn.mockReset();
    }
    getLayoutSettings.mockResolvedValue(WITH_LAYOUT);
    getLayoutMigrationPreview.mockResolvedValue(WOULD_MOVE);
    setLayoutSettings.mockResolvedValue(WITH_LAYOUT);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  // ---- the builder ---------------------------------------------------------

  it("keeps the label in the outline and shows None until a level is picked", async () => {
    // The empty slot used to sit its label inline as placeholder text while a
    // filled one floated it into the outline notch, so the two read as
    // different kinds of control rather than the same one at two states.
    const wrapper = mountDialog();
    await flushPromises();
    const selects = wrapper.findAllComponents({ name: "v-select" });
    for (const select of selects) {
      expect(select.props("persistentPlaceholder")).toBe(true);
      expect(select.props("placeholder")).toBe("None");
      expect(select.props("label")).toMatch(/^Level \d$/);
    }
  });

  it("clearing the last level turns the layout off, sending layout: null", async () => {
    // Turning a layout OFF has to be reachable. `LibrarySettings.layout` being
    // non-NULL is the gate on the whole layout tracker
    // (`database.py::_library_has_layout`), so a library that can never send
    // `layout: null` can never stop LayoutMoveTask renaming its files. This
    // used to be refused outright.
    vi.useFakeTimers();
    // The real PATCH answers with the settings as stored, so the builder is
    // re-applied from the response. A fixed mock would snap the cleared level
    // back and the second clear would never reach an empty layout.
    setLayoutSettings.mockImplementation((patch) =>
      Promise.resolve({
        layout: patch.layout ?? null,
        layout_unfiled: "Unassigned",
      }),
    );
    const wrapper = mountDialog();
    await flushPromises();
    setLayoutSettings.mockClear();

    // Two levels: clearing one is fine, the other becomes level 1.
    editLevel(wrapper, 1, []);
    await vi.advanceTimersByTimeAsync(600);
    await flushPromises();
    expect(
      wrapper.findAllComponents({ name: "v-select" })[0].props("modelValue"),
    ).toEqual(["project"]);

    // Now clear the only one left: saved as `null`, not refused.
    setLayoutSettings.mockClear();
    editLevel(wrapper, 0, []);
    await vi.advanceTimersByTimeAsync(600);
    await flushPromises();
    expect(setLayoutSettings).toHaveBeenCalledWith(
      expect.objectContaining({ layout: null }),
    );
  });

  it("opening a library with no layout writes NOTHING", async () => {
    // The regression this file exists to pin. Opening the dialog used to seed
    // `[["project"]]` through the normal save path, PATCHing a layout onto the
    // library as a side effect of looking at the screen. Because
    // `LibrarySettings.layout` being non-NULL arms the layout tracker, that
    // turned a browse into a standing decision to move the owner's files.
    getLayoutSettings.mockResolvedValue({
      layout: null,
      layout_unfiled: "Unassigned",
      default_layout: "project/person,set",
    });
    vi.useFakeTimers();
    const wrapper = mountDialog();
    await flushPromises();
    // Well past SAVE_DEBOUNCE_MS: a debounced write would have fired by now.
    await vi.advanceTimersByTimeAsync(5000);
    await flushPromises();

    expect(setLayoutSettings).not.toHaveBeenCalled();
    // One empty slot to grow into, nothing chosen.
    const selects = wrapper.findAllComponents({ name: "v-select" });
    expect(selects).toHaveLength(1);
    expect(selects[0].props("modelValue")).toEqual([]);
  });

  it("a library with no layout only writes once the owner picks a level", async () => {
    getLayoutSettings.mockResolvedValue({
      layout: null,
      layout_unfiled: "Unassigned",
      default_layout: "project/person,set",
    });
    setLayoutSettings.mockResolvedValue({ ...WITH_LAYOUT, layout: "project" });
    vi.useFakeTimers();
    const wrapper = mountDialog();
    await flushPromises();
    expect(setLayoutSettings).not.toHaveBeenCalled();

    editLevel(wrapper, 0, ["project"]);
    await vi.advanceTimersByTimeAsync(600);
    await flushPromises();
    expect(setLayoutSettings).toHaveBeenCalledWith(
      expect.objectContaining({ layout: "project" }),
    );
  });

  it("leaves a library that already has a layout alone", async () => {
    // The seed must never overwrite a choice the owner already made.
    vi.useFakeTimers();
    mountDialog();
    await flushPromises();
    await vi.advanceTimersByTimeAsync(600);
    await flushPromises();
    expect(setLayoutSettings).not.toHaveBeenCalled();
  });

  it("draws four levels on one line and nothing else in the row", async () => {
    // The width budget the row is drawn to (see MAX_LEVELS in the component):
    // 620px dialog - 48px padding = 572px, minus three separators and six gaps
    // = ~527px over four 120px-basis columns. It only holds while the row
    // carries selects and separators and NOTHING else, so a per-level remove
    // button or an add control coming back is what this fails on.
    getLayoutSettings.mockResolvedValue(FOUR_LEVELS);

    const wrapper = mountDialog();
    await flushPromises();

    const row = wrapper.find(".layout-levels");
    expect(row.findAll("select")).toHaveLength(4);
    expect(row.findAll(".layout-levels__sep")).toHaveLength(3);
    expect(row.findAll("button")).toHaveLength(0);
    // Each level is its own flex column, and each is numbered - the colour is
    // never the only thing telling one level from another.
    expect(row.findAll(".layout-level")).toHaveLength(4);
    expect(
      row.findAll("select").map((s) => s.attributes("data-level")),
    ).toEqual(["Level 1", "Level 2", "Level 3", "Level 4"]);
  });

  it("offers one empty level to grow into, and none once all four are used", async () => {
    const wrapper = mountDialog();
    await flushPromises();
    // project / person,set, so two levels plus the empty one.
    expect(wrapper.findAll(".layout-levels select")).toHaveLength(3);

    wrapper.unmount();
    getLayoutSettings.mockResolvedValue(FOUR_LEVELS);
    const full = mountDialog();
    await flushPromises();
    expect(full.findAll(".layout-levels select")).toHaveLength(4);
  });

  // ---- the tree ------------------------------------------------------------

  it("draws the owner's own folders with have, delta and a new badge", async () => {
    const wrapper = mountDialog();
    await flushPromises();

    const rows = wrapper.findAll(".layout-tree__row");
    expect(rows).toHaveLength(4);

    // Depth is an indent, not a prefix in the name.
    expect(rows[0].find(".layout-tree__name").attributes("style")).toContain(
      "0 * var(--indent-step)",
    );

    expect(rows[0].find(".layout-tree__have").text()).toBe("1,204");
    expect(rows[0].find(".layout-tree__delta").text()).toBe("+312");
    // A folder that does not exist yet has no count to show, and says so.
    expect(rows[1].find(".layout-tree__badge").text()).toBe("new");
    expect(rows[1].find(".layout-tree__have").text()).toBe("—");
    // Arriving reads primary, leaving reads accent, neither reads as the other.
    expect(rows[1].find(".layout-tree__delta").classes()).toContain(
      "layout-tree__delta--in",
    );
    expect(rows[2].find(".layout-tree__delta").text()).toBe("unchanged");
    expect(rows[3].find(".layout-tree__delta").text()).toBe("−480");
    expect(rows[3].find(".layout-tree__delta").classes()).toContain(
      "layout-tree__delta--out",
    );
  });

  it("names the absent ancestors, because under a deep layout almost every row is an orphan", async () => {
    // A folder is a row only when one of have/arriving/leaving is non-zero, so
    // a project that holds no pictures of its own is not in the response and
    // its people are. Indenting on depth alone then says nothing - there is no
    // parent row to be indented relative to - and nine rows read "arm hair",
    // "beard", "arm hair" with no idea whose they are.
    getLayoutMigrationPreview.mockResolvedValue({
      ...NOTHING_TO_MOVE,
      picture_count: 60,
      folder_count: 2,
      tree: [
        {
          path: "Harbour Nights/Nova/arm hair",
          name: "arm hair",
          depth: 2,
          have: 0,
          arriving: 40,
          leaving: 0,
          is_new: true,
        },
        {
          path: "Rooftop Test/Wren/arm hair",
          name: "arm hair",
          depth: 2,
          have: 0,
          arriving: 20,
          leaving: 0,
          is_new: true,
        },
      ],
    });

    const wrapper = mountDialog();
    await flushPromises();

    const rows = wrapper.findAll(".layout-tree__row");
    // Two rows with the same leaf name are told apart by their parentage, not
    // by an indent that has nothing to indent under.
    expect(rows[0].find(".layout-tree__crumbs").text()).toBe(
      "Harbour Nights / Nova /",
    );
    expect(rows[1].find(".layout-tree__crumbs").text()).toBe(
      "Rooftop Test / Wren /",
    );
    expect(rows[0].find(".layout-tree__label").text()).toBe("arm hair");
    // Nothing is invented: the withheld ancestors get no rows of their own.
    expect(rows).toHaveLength(2);
    // No ancestor is present, so there is nothing to indent under.
    expect(rows[0].find(".layout-tree__name").attributes("style")).toContain(
      "0 * var(--indent-step)",
    );
    // The full stored path is on the row whatever it managed to draw.
    expect(rows[0].find(".layout-tree__name").attributes("title")).toBe(
      "Harbour Nights/Nova/arm hair",
    );
  });

  it("indents instead of repeating an ancestor that does have a row", async () => {
    const wrapper = mountDialog();
    await flushPromises();

    const rows = wrapper.findAll(".layout-tree__row");
    // `Harbour Nights` is a row, so `Harbour Nights/Nova` says only "Nova" and
    // steps in under it.
    expect(rows[0].find(".layout-tree__crumbs").exists()).toBe(false);
    expect(rows[1].find(".layout-tree__crumbs").exists()).toBe(false);
    expect(rows[1].find(".layout-tree__label").text()).toBe("Nova");
    expect(rows[1].find(".layout-tree__name").attributes("style")).toContain(
      "1 * var(--indent-step)",
    );
  });

  it("lists every folder rather than a count of the ones it left out", async () => {
    const wrapper = mountDialog();
    await flushPromises();

    expect(wrapper.findAll(".layout-tree__row")).toHaveLength(
      WOULD_MOVE.tree.length,
    );
    expect(wrapper.find(".layout-tree__more").exists()).toBe(false);
  });

  it("previews and runs the sweep with the same flag, off by default", async () => {
    runLayoutMigrationPass.mockResolvedValue(PASS_DONE);
    const wrapper = mountDialog();
    await flushPromises();
    expect(getLayoutMigrationPreview).toHaveBeenLastCalledWith({
      sweepUnfiled: false,
    });

    await wrapper.find(".layout-unfiled input[type=checkbox]").setValue(true);
    await flushPromises();
    expect(getLayoutMigrationPreview).toHaveBeenLastCalledWith({
      sweepUnfiled: true,
    });

    await buttonWith(wrapper, "Move them now").trigger("click");
    await flushPromises();
    expect(runLayoutMigrationPass.mock.calls[0][0]).toEqual({
      afterId: 0,
      batchId: null,
      sweepUnfiled: true,
    });
  });

  it("saves the unfiled folder name like any other layout edit", async () => {
    vi.useFakeTimers();
    const wrapper = mountDialog();
    await flushPromises();
    setLayoutSettings.mockClear();

    await wrapper.find(".layout-unfiled__name").setValue("Loose");
    await vi.advanceTimersByTimeAsync(600);
    await flushPromises();
    expect(setLayoutSettings).toHaveBeenCalledWith({ layoutUnfiled: "Loose" });
  });

  // ---- the three carried-forward guards -------------------------------------

  it("freezes the layout for the duration of a move", async () => {
    // Editing mid-run makes later passes re-plan against a new layout, so half
    // the library lands on one layout and half on another, under one undo
    // batch that describes neither.
    vi.useFakeTimers();
    let finishPass;
    runLayoutMigrationPass.mockReturnValue(
      new Promise((resolve) => (finishPass = resolve)),
    );

    const wrapper = mountDialog();
    await flushPromises();
    await buttonWith(wrapper, "Move them now").trigger("click");
    await flushPromises();

    // The controls are out of reach...
    expect(
      wrapper
        .findAll(".layout-levels select")
        .every((s) => s.attributes("disabled") !== undefined),
    ).toBe(true);
    // ...and an edit that reaches the component anyway writes nothing.
    editLevel(wrapper, 0, ["tag"]);
    vi.advanceTimersByTime(2000);
    await flushPromises();
    expect(setLayoutSettings).not.toHaveBeenCalled();

    finishPass(PASS_DONE);
    await flushPromises();
  });

  it("keeps the Undo across the re-count an edit triggers", async () => {
    // The batch id is the only route back to a move that already happened, and
    // an edit is not a reason to throw one away.
    vi.useFakeTimers();
    runLayoutMigrationPass.mockResolvedValue(PASS_DONE);

    const wrapper = mountDialog();
    await flushPromises();
    await buttonWith(wrapper, "Move them now").trigger("click");
    await flushPromises();
    expect(buttonWith(wrapper, "Undo")).toBeTruthy();

    // An edit, which re-counts: debounced save, then a fresh preview.
    editLevel(wrapper, 0, ["tag"]);
    vi.advanceTimersByTime(1000);
    await flushPromises();

    expect(setLayoutSettings).toHaveBeenCalled();
    expect(getLayoutMigrationPreview.mock.calls.length).toBeGreaterThan(1);
    expect(buttonWith(wrapper, "Undo")).toBeTruthy();

    await buttonWith(wrapper, "Undo").trigger("click");
    await flushPromises();
    expect(undoBatchById).toHaveBeenCalledWith(
      "srv-layout-migration-0123456789abcdef",
    );
  });

  it("says undoing, with an indeterminate bar, while the undo runs", async () => {
    // Undo is one request. Rendering it through the per-pass "Moving X of Y"
    // template read the finished run's count against a post-run preview of 0.
    runLayoutMigrationPass.mockResolvedValue(PASS_DONE);
    let finishUndo;
    undoBatchById.mockReturnValue(
      new Promise((resolve) => {
        finishUndo = resolve;
      }),
    );

    const wrapper = mountDialog();
    await flushPromises();
    await buttonWith(wrapper, "Move them now").trigger("click");
    await flushPromises();
    await buttonWith(wrapper, "Undo").trigger("click");
    await flushPromises();

    const text = wrapper.find(".layout-consequence__num").text();
    expect(text).toContain("Undoing…");
    expect(text).not.toContain("of 0");
    expect(wrapper.find("progress").attributes("value")).toBeUndefined();
    expect(buttonWith(wrapper, "Stop after this pass")).toBeFalsy();

    finishUndo({ operations: [] });
    await flushPromises();
    expect(wrapper.find(".layout-consequence__num").text()).not.toContain(
      "Undoing",
    );
  });

  it("will not move on a stale count, and says counting instead of a number", async () => {
    // The bar carries the number beside the verb, so a modal confirm would be a
    // second yes for one gesture. The button being inert while the number does
    // not describe the layout on screen is what stands in for it.
    vi.useFakeTimers();

    const wrapper = mountDialog();
    await flushPromises();
    expect(wrapper.find(".layout-consequence__num").text()).toContain("4,109");
    expect(
      buttonWith(wrapper, "Move them now").attributes("disabled"),
    ).toBeUndefined();

    // An edit. Nothing is in flight yet - the save is still inside its
    // debounce - so `previewing` is false and staleness is the only thing
    // holding the button.
    editLevel(wrapper, 0, ["tag"]);
    await flushPromises();

    expect(wrapper.find(".layout-consequence__num").text()).toBe("counting…");
    expect(wrapper.text()).not.toContain("4,109");
    expect(buttonWith(wrapper, "Move them now").attributes("disabled")).toBe(
      "",
    );

    vi.advanceTimersByTime(1000);
    await flushPromises();

    expect(wrapper.find(".layout-consequence__num").text()).toContain("4,109");
    expect(
      buttonWith(wrapper, "Move them now").attributes("disabled"),
    ).toBeUndefined();
    expect(runLayoutMigrationPass).not.toHaveBeenCalled();
  });

  // ---- the consequence bar --------------------------------------------------

  it("puts both exits side by side, and only the move changes colour", async () => {
    const wrapper = mountDialog();
    await flushPromises();

    const buttons = wrapper.findAll(".layout-consequence__buttons button");
    expect(buttons.map((b) => b.text())).toEqual([
      "Keep layout, move nothing",
      "Move them now",
    ]);
    // Declining the move is the common case, so it is a filled button rather
    // than a transparent Cancel. `secondary` is the neutral fill; `ghost` is
    // the transparent one.
    expect(buttons[0].attributes("data-variant")).toBe("secondary");
    expect(buttons[1].attributes("data-variant")).toBe("primary_green");
  });

  it("swaps the bar for a progress region and a stop while a move runs", async () => {
    let finishPass;
    runLayoutMigrationPass.mockReturnValue(
      new Promise((resolve) => (finishPass = resolve)),
    );

    const wrapper = mountDialog();
    await flushPromises();
    await buttonWith(wrapper, "Move them now").trigger("click");
    await flushPromises();

    expect(
      wrapper.find(".layout-consequence__count").attributes("aria-live"),
    ).toBe("polite");
    expect(wrapper.find("progress").exists()).toBe(true);
    expect(buttonWith(wrapper, "Stop after this pass")).toBeTruthy();
    expect(buttonWith(wrapper, "Move them now")).toBeFalsy();

    finishPass(PASS_DONE);
    await flushPromises();
  });

  it("runs every pass under one batch id, so the whole move is one undo", async () => {
    runLayoutMigrationPass
      .mockResolvedValueOnce({
        batch_id: "srv-layout-migration-0123456789abcdef",
        moved_count: 200,
        next_after_id: 200,
        done: false,
      })
      .mockResolvedValueOnce(PASS_DONE);

    const wrapper = mountDialog();
    await flushPromises();
    await buttonWith(wrapper, "Move them now").trigger("click");
    await flushPromises();

    expect(runLayoutMigrationPass.mock.calls[0][0]).toEqual({
      afterId: 0,
      batchId: null,
      sweepUnfiled: false,
    });
    expect(runLayoutMigrationPass.mock.calls[1][0]).toEqual({
      afterId: 200,
      batchId: "srv-layout-migration-0123456789abcdef",
      sweepUnfiled: false,
    });
  });

  it("names what a run refused instead of claiming a clean finish", async () => {
    runLayoutMigrationPass.mockResolvedValue({
      ...PASS_DONE,
      skipped: [
        { picture_id: 1, reason: "move_failed" },
        { picture_id: 2, reason: "move_failed" },
      ],
    });

    const wrapper = mountDialog();
    await flushPromises();
    await buttonWith(wrapper, "Move them now").trigger("click");
    await flushPromises();

    expect(wrapper.find(".layout-flags").text()).toContain(
      "2 could not be moved just now",
    );
  });

  it("shows the locality sentence rather than controls a remote owner cannot use", async () => {
    setActivePinia(createPinia());
    const store = useLibrariesStore();
    store.canManage = false;
    store.hasLoadedSuccessfully = true;

    const wrapper = mount(LibraryLayoutDialog, {
      props: { open: true },
      global: { stubs: { AppDialog: AppDialogStub, AppButton: AppButtonStub } },
    });
    await flushPromises();

    expect(wrapper.text()).toContain(
      "only available on the machine running PixlStash",
    );
    expect(getLayoutSettings).not.toHaveBeenCalled();
    expect(wrapper.find(".layout-levels").exists()).toBe(false);
  });

  it("offers a retry, not an empty builder, when the layout cannot be read", async () => {
    // An empty layout after a failed GET looks exactly like "no layout", and
    // the next edit would PATCH one over a layout nobody has read.
    getLayoutSettings.mockRejectedValue(new Error("backend asleep"));

    const wrapper = mountDialog();
    await flushPromises();

    expect(wrapper.text()).toContain("backend asleep");
    expect(buttonWith(wrapper, "Try again")).toBeTruthy();
    expect(wrapper.find(".layout-levels").exists()).toBe(false);
  });
});
