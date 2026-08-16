// The shelf's reading surface.
//
// The three assertions worth having are the ones that guard the measured data
// realities: a third of real adapters have no title, no base model and no
// trigger, so a row must never render a blank; a derived name must stay
// distinguishable from a chosen one; and `unknown` must never read as a
// checkpoint.

import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const listAdapters = vi.fn();
const listCheckpoints = vi.fn();
// The engines block is the same route with `file_kind=engine`, and a different
// result set. Its own double, or every `listAdapters.mockResolvedValue` here
// would answer it with adapter rows and the shelf would render each one twice.
const listEngines = vi.fn();
// And the unclassified block, for the same reason, now that it is on by
// default (#927): one route, one more result set, its own double.
const listUnclassified = vi.fn();
// And the support block: two `file_kind`s behind one checkbox, so one double
// answers both requests. Same reason again — without it every
// `listAdapters.mockResolvedValue` here would answer them with adapter rows.
const listSupport = vi.fn();
const deleteModels = vi.fn();

vi.mock("../../api/modelShelf", () => ({
  BASE_MODEL_UNASSIGNED: "UNASSIGNED",
  listAdapters: (...args) => {
    if (args[0]?.fileKind === "engine") return listEngines(...args);
    if (args[0]?.fileKind === "unknown") return listUnclassified(...args);
    if (args[0]?.fileKind === "vae" || args[0]?.fileKind === "text_encoder") {
      return listSupport(...args);
    }
    return listAdapters(...args);
  },
  listCheckpoints: (...args) => listCheckpoints(...args),
  // The shelf's destructive verb. Mocked for the same reason the folder reads
  // are — it is a network call on a user gesture — and because a suite that
  // really called it would be asserting the server's gate rather than the
  // view's.
  deleteModels: (...args) => deleteModels(...args),
  // The base-model field asks for its completion list as it opens. Answered
  // with nothing here: the list is the widget's own suite's business, and left
  // unmocked this is a network call on a double-click.
  listBaseModelCompletions: vi.fn().mockResolvedValue([]),
}));

// The shelf reads the drives to band its folder groups. Left unmocked this
// reaches the network, and the failure that comes back is routed as a session
// reset — which empties the shelf store MID-TEST and made an unrelated sort
// assertion read the default view back.
const listModelFolderDevices = vi.fn();
const listModelFolders = vi.fn();
// `onMounted` calls `moves.adopt()`, which reads the move job so a move
// started before a reload is picked up. Left unmocked it reaches the network,
// fails, and `console.warn`s from a promise nothing in the test awaits — which
// lands AFTER the file tears down and kills the whole vitest run with
// `EnvironmentTeardownError: Closing rpc while "onUserConsoleLog" was pending`.
// Every file passed and the runner still exited 1 (#880's first CI run). It is
// timing-dependent, so a local run can be green with the same bug present.
const getModelMoveStatus = vi.fn();
vi.mock("../../api/modelMoves", () => ({
  getModelMoveStatus: (...args) => getModelMoveStatus(...args),
  startModelMove: vi.fn(),
  cancelModelMove: vi.fn(),
}));

// `Add file` (F6). Mocked rather than left real for the same reason the folder
// reads are: it is a network call on a user gesture, and this suite is about
// what the shelf does with the answer.
const addModelFile = vi.fn();
vi.mock("../../api/modelFiles", () => ({
  addModelFile: (...args) => addModelFile(...args),
}));

// The manual stack verb's only write. Mocked here rather than left real
// because the bar's button is what this suite drives, and the assertion worth
// having is which ids reach the route.
const createStack = vi.fn();
vi.mock("../../api/modelStacks", () => ({
  createStack: (...args) => createStack(...args),
  listStackProposals: vi.fn(),
}));

vi.mock("../../api/modelFolders", async (importOriginal) => ({
  ...(await importOriginal()),
  listModelFolderDevices: (...args) => listModelFolderDevices(...args),
  listModelFolders: (...args) => listModelFolders(...args),
}));

// The names, colours and thumbnails behind the assignment rings (#892/#904).
// Mocked for the same reason the folder reads are: unmocked they reach the
// network from `onMounted`, and the rejection lands in a `console.warn` from a
// promise no test awaits — which is the teardown race noted above.
const listCharacters = vi.fn();
const listPictureSets = vi.fn();
vi.mock("../../api/characters", async (importOriginal) => ({
  ...(await importOriginal()),
  listCharacters: (...args) => listCharacters(...args),
}));
vi.mock("../../api/pictureSets", async (importOriginal) => ({
  ...(await importOriginal()),
  listPictureSets: (...args) => listPictureSets(...args),
}));

import ModelShelf from "./ModelShelf.vue";
import { useModelMovesStore } from "../../stores/useModelMovesStore";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";
import { useSidebarStore } from "../../stores/useSidebarStore";

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      "v-menu": {
        template: "<div><slot name='activator' :props='{}' /><slot /></div>",
      },
      ShelfShowPanel: true,
      ShelfSortPanel: true,
      // Their own suites mount them. Here they would only drag Vuetify's dialog
      // and tooltip providers into a suite that installs neither. The selection
      // BAR is deliberately left real: what a selection does to this view is
      // this suite's business.
      ModelFoldersDialog: true,
      ShelfEditDialog: true,
      ShelfMoveDialog: true,
      ModelImportDialog: true,
      // The host-path picker `Add file` opens. Real, it would drag Vuetify's
      // dialog provider into a suite that installs none; stubbed, it still
      // emits `select`, which is the whole of what this view listens for.
      FolderBrowser: true,
      ShelfStackProposalsDialog: true,
      ProgressOverlay: true,
      // The picker inside the selection bar, which the bar's own suite covers.
      // Left real it would read the shared entity lists on every mount here.
      AddToEntityControl: true,
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

async function mountShelf(
  rows,
  checkpoints = [],
  unclassified = [],
  extra = {},
) {
  listAdapters.mockResolvedValue(rows);
  listCheckpoints.mockResolvedValue(checkpoints);
  listEngines.mockResolvedValue([]);
  listUnclassified.mockResolvedValue(unclassified);
  listSupport.mockResolvedValue([]);
  const wrapper = mount(ModelShelf, { ...globalOpts, ...extra });
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
  listEngines.mockReset().mockResolvedValue([]);
  listUnclassified.mockReset().mockResolvedValue([]);
  listSupport.mockReset().mockResolvedValue([]);
  listModelFolderDevices.mockReset();
  listModelFolderDevices.mockResolvedValue([]);
  listModelFolders.mockReset();
  listModelFolders.mockResolvedValue([]);
  // `idle` is what a machine with no move in flight reports, and `adopt()`
  // adopts nothing from it — so the mount path stays silent instead of warning
  // from an unawaited promise.
  getModelMoveStatus.mockReset();
  getModelMoveStatus.mockResolvedValue({ status: "idle", results: [] });
  addModelFile.mockReset();
  listCharacters.mockReset().mockResolvedValue([]);
  listPictureSets.mockReset().mockResolvedValue([]);
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
    // Every column renders something: the kind is derived from `file_kind` and
    // is the guaranteed anchor when all else is null, and an absent base model
    // says so in words rather than leaving a cell that reads as a gap.
    const cells = row.findAll(".shelf-col").map((s) => textOf(s));
    expect(cells).toEqual(["LoRA", "not set", "342.1 MB"]);
  });

  it("marks the derived name by type, not by fading it", async () => {
    // Rank is size, weight and tracking, never opacity (§5.1) — and 37% of
    // rows faded would be a column of ghosts rather than a signal.
    const wrapper = await mountShelf([
      adapter({ id: 1, display_name: null }),
      adapter({ id: 2, display_name: "Clementine" }),
      // Nothing survives the strip, so this one IS the filename — the positive
      // control, or the two negatives below would also pass with the tag ripped
      // out of the component altogether.
      adapter({ id: 3, display_name: null, filename: "000002750.st" }),
    ]);
    const names = wrapper.findAll(".shelf-row-name");
    expect(names[0].classes()).toContain("shelf-row-name--derived");
    expect(names[1].classes()).toContain("shelf-row-name--named");
    // ...and it carries no tag: "DERIVED" on most of the column said nothing a
    // reader acts on.
    const rows = wrapper.findAll(".shelf-row");
    expect(rows[2].find(".shelf-name-tag").text()).toBe("from filename");
    expect(rows[0].find(".shelf-name-tag").exists()).toBe(false);
    expect(rows[1].find(".shelf-name-tag").exists()).toBe(false);
  });
});

describe("the four naming states", () => {
  /** One row per state, in the order the design lists them. */
  async function mountStates() {
    return mountShelf([
      adapter({ id: 1, display_name: "Clementine" }),
      adapter({ id: 2, display_name: null, filename: null }),
      adapter({ id: 3, display_name: null, filename: "Foxglove_Char.st" }),
      // Nothing survives the strip, so the shown string IS the filename.
      adapter({ id: 4, display_name: null, filename: "000002750.st" }),
    ]);
  }

  it("draws each of them differently", async () => {
    const rows = (await mountStates()).findAll(".shelf-row");
    const stateClass = (row) =>
      row
        .find(".shelf-row-name")
        .classes()
        .find((c) => c.startsWith("shelf-row-name--"));
    expect(rows.map(stateClass)).toEqual([
      "shelf-row-name--named",
      "shelf-row-name--needs-a-name",
      "shelf-row-name--derived",
      "shelf-row-name--from-file",
    ]);
  });

  it("tells derived from the file's own name without opening anything", async () => {
    const rows = (await mountStates()).findAll(".shelf-row");
    // Only the file's own string gets a word: a derived name already reads as
    // ours from its type and rule, so tagging it was a chip on most of the
    // column that a reader could not act on.
    expect(rows[2].find(".shelf-name-tag").exists()).toBe(false);
    expect(rows[3].find(".shelf-name-tag").text()).toBe("from filename");
  });

  it("shows an unnamed model an empty field, not inert text", async () => {
    const rows = (await mountStates()).findAll(".shelf-row");
    // The row that most needs naming used to read `no name in file`, which
    // looks like a name and invites nothing. It is a prompt with a persistent
    // pencil now (#897).
    expect(rows[1].find(".shelf-row-name").text()).toBe("Name this model");
    // The invitation is the NAME's own treatment now, not a pencil beside it:
    // the resolved design puts the affordance on the field, so the accent rule
    // and the italic are always there on this row and on no other.
    expect(rows[1].find(".shelf-row-name").classes()).toContain(
      "shelf-row-name--needs-a-name",
    );
    expect(rows[0].find(".shelf-row-name").classes()).not.toContain(
      "shelf-row-name--needs-a-name",
    );
  });
});

describe("renaming a row in place", () => {
  it("opens the field on F2 and commits it on Enter", async () => {
    // F2 and not a tab stop per row: the affordance has to be reachable by
    // keyboard, and the shelf's dialect is that the ROW is the control.
    const wrapper = await mountShelf([adapter({ id: 7, display_name: null })]);
    const store = useModelShelfStore();
    const edit = vi.spyOn(store, "editModelIds").mockResolvedValue(true);

    await wrapper.find(".shelf-row").trigger("keydown", { key: "F2" });
    const field = wrapper.find(".shelf-row-rename");
    expect(field.exists()).toBe(true);
    // Seeded EMPTY on a derived row: the derived string is a guess, and
    // pre-filling it would turn one Enter into somebody having chosen it.
    expect(field.element.value).toBe("");

    await field.setValue("Cyanwood");
    await field.trigger("keydown", { key: "Enter" });
    expect(edit).toHaveBeenCalledWith([7], { display_name: "Cyanwood" });
    expect(wrapper.find(".shelf-row-rename").exists()).toBe(false);
  });

  it("writes nothing on Escape", async () => {
    const wrapper = await mountShelf([adapter({ id: 7 })]);
    const store = useModelShelfStore();
    const edit = vi.spyOn(store, "editModelIds").mockResolvedValue(true);

    await wrapper.find(".shelf-row").trigger("keydown", { key: "F2" });
    await wrapper.find(".shelf-row-rename").setValue("Nope");
    await wrapper
      .find(".shelf-row-rename")
      .trigger("keydown", { key: "Escape" });
    expect(edit).not.toHaveBeenCalled();
    expect(wrapper.find(".shelf-row-rename").exists()).toBe(false);
  });

  it("keeps the field's keys off the list underneath", async () => {
    // Arrow walks the rows, Space picks and Escape clears the selection, so a
    // name could not be typed with any of them still live under the field.
    const wrapper = await mountShelf([adapter({ id: 7 }), adapter({ id: 8 })]);
    const store = useModelShelfStore();
    await wrapper.find(".shelf-row").trigger("keydown", { key: "F2" });
    await wrapper.find(".shelf-row-rename").trigger("keydown", { key: " " });
    expect(store.selectedRows).toHaveLength(0);
  });

  it("renames every member of a run, because they share one name", async () => {
    const wrapper = await mountShelf([
      adapter({ id: 1, stack_id: 5, display_name: null, training_step: 250 }),
      adapter({ id: 2, stack_id: 5, display_name: null, training_step: 500 }),
    ]);
    const store = useModelShelfStore();
    const edit = vi.spyOn(store, "editModelIds").mockResolvedValue(true);

    await wrapper.find(".shelf-row").trigger("keydown", { key: "F2" });
    await wrapper.find(".shelf-row-rename").setValue("Cyanwood");
    await wrapper
      .find(".shelf-row-rename")
      .trigger("keydown", { key: "Enter" });
    expect(edit).toHaveBeenCalledWith([1, 2], { display_name: "Cyanwood" });
  });
});

describe("editing a base model in place", () => {
  it("opens the field on a double-click and commits it on Enter", async () => {
    const wrapper = await mountShelf([adapter({ id: 7 })]);
    const store = useModelShelfStore();
    const edit = vi.spyOn(store, "editModelIds").mockResolvedValue(true);

    await wrapper.find(".shelf-col--base span").trigger("dblclick");
    const field = wrapper.find(".shelf-row-base-edit");
    expect(field.exists()).toBe(true);
    // Seeded from the stored value, unlike the name field: nothing infers a
    // base model, so what the row shows is what the file said and a correction
    // is one word rather than a retype.
    expect(field.element.value).toBe("flux.1-dev");

    await field.setValue("FLUX.2");
    await field.trigger("keydown", { key: "Enter" });
    expect(edit).toHaveBeenCalledWith([7], { base_model: "FLUX.2" });
    expect(wrapper.find(".shelf-row-base-edit").exists()).toBe(false);
  });

  it("opens on the `not set` chip too, which is a value like any other", async () => {
    // The row that most needs this gesture is the one with nothing recorded,
    // and a chip that cannot be double-clicked is exactly the row you have to
    // go to a dialog for.
    const wrapper = await mountShelf([adapter({ id: 7, base_model: null })]);
    const store = useModelShelfStore();
    const edit = vi.spyOn(store, "editModelIds").mockResolvedValue(true);

    await wrapper.find(".shelf-chip--none").trigger("dblclick");
    const field = wrapper.find(".shelf-row-base-edit");
    expect(field.element.value).toBe("");

    await field.setValue("SDXL 1.0");
    await field.trigger("keydown", { key: "Enter" });
    expect(edit).toHaveBeenCalledWith([7], { base_model: "SDXL 1.0" });
  });

  it("clears the column with an explicit null rather than an empty string", async () => {
    const wrapper = await mountShelf([adapter({ id: 7 })]);
    const store = useModelShelfStore();
    const edit = vi.spyOn(store, "editModelIds").mockResolvedValue(true);

    await wrapper.find(".shelf-col--base span").trigger("dblclick");
    const field = wrapper.find(".shelf-row-base-edit");
    await field.setValue("   ");
    await field.trigger("keydown", { key: "Enter" });
    expect(edit).toHaveBeenCalledWith([7], { base_model: null });
  });

  it("gives the first Escape to the menu and the second to the edit", async () => {
    // With a menu open the first Escape must only take the menu back, or one
    // press aimed at a dropdown throws away what was typed. The completion list
    // is seeded here for that reason: mocked empty, this test would pass while
    // the two-stage Escape was broken.
    const wrapper = await mountShelf([adapter({ id: 7 })]);
    const store = useModelShelfStore();
    const edit = vi.spyOn(store, "editModelIds").mockResolvedValue(true);
    store.rows = [
      ...store.rows,
      { ...adapter({ id: 9 }), base_model: "Nope 1" },
    ];

    await wrapper.find(".shelf-col--base span").trigger("dblclick");
    const field = wrapper.find(".shelf-row-base-edit");
    await field.setValue("Nope");
    await field.trigger("keydown", { key: "Escape" });
    expect(wrapper.find(".shelf-row-base-edit").exists()).toBe(true);

    await field.trigger("keydown", { key: "Escape" });
    expect(edit).not.toHaveBeenCalled();
    expect(wrapper.find(".shelf-row-base-edit").exists()).toBe(false);
  });

  it("commits when the field loses focus, and only once", async () => {
    // The riskiest line in the gesture: clicking away writes, with no undo and
    // no prompt. It has to fire — and it must not fire a second time behind the
    // Enter that already wrote, which is why the field closes before it writes.
    const wrapper = await mountShelf([adapter({ id: 7 })]);
    const store = useModelShelfStore();
    const edit = vi.spyOn(store, "editModelIds").mockResolvedValue(true);

    await wrapper.find(".shelf-col--base span").trigger("dblclick");
    const field = wrapper.find(".shelf-row-base-edit");
    await field.setValue("FLUX.2");
    await field.trigger("blur");

    expect(edit).toHaveBeenCalledWith([7], { base_model: "FLUX.2" });
    expect(edit).toHaveBeenCalledTimes(1);
  });

  it("does not write twice when Enter is followed by the unmount's blur", async () => {
    const wrapper = await mountShelf([adapter({ id: 7 })]);
    const store = useModelShelfStore();
    const edit = vi.spyOn(store, "editModelIds").mockResolvedValue(true);

    await wrapper.find(".shelf-col--base span").trigger("dblclick");
    const field = wrapper.find(".shelf-row-base-edit");
    await field.setValue("FLUX.2");
    await field.trigger("keydown", { key: "Enter" });
    await field.trigger("blur");

    expect(edit).toHaveBeenCalledTimes(1);
  });

  it("hands focus back to the row on a key, and leaves it alone on a click", async () => {
    // The grid's tab stop roves, so a field that closes without giving focus
    // back drops a keyboard reader at the top of the document — the defect the
    // rename path fixed and this one had to inherit. A blur is the opposite
    // case: the reader has already chosen where to go.
    const wrapper = await mountShelf([adapter({ id: 7 })], [], [], {
      attachTo: document.body,
    });
    const store = useModelShelfStore();
    vi.spyOn(store, "editModelIds").mockResolvedValue(true);

    const rowEl = wrapper.find(".shelf-row");
    await rowEl.find(".shelf-col--base span").trigger("dblclick");
    await wrapper.find(".shelf-row-base-edit").trigger("keydown", {
      key: "Enter",
    });
    await wrapper.vm.$nextTick();
    expect(document.activeElement).toBe(rowEl.element);

    await rowEl.find(".shelf-col--base span").trigger("dblclick");
    document.body.focus();
    await wrapper.find(".shelf-row-base-edit").trigger("blur");
    await wrapper.vm.$nextTick();
    expect(document.activeElement).not.toBe(rowEl.element);
    wrapper.unmount();
  });

  it("is reachable from the keyboard, like the name beside it", async () => {
    // A double click is not a keyboard gesture, and the row advertises the key
    // it answers to. Without this the field is pointer-only.
    const wrapper = await mountShelf([adapter({ id: 7 })]);
    const row = wrapper.find(".shelf-row");
    expect(row.attributes("aria-keyshortcuts")).toContain("Shift+F2");

    await row.trigger("keydown", { key: "F2", shiftKey: true });
    expect(wrapper.find(".shelf-row-base-edit").exists()).toBe(true);
    // And plain F2 still opens the OTHER field, not this one.
    expect(wrapper.find(".shelf-row-rename").exists()).toBe(false);
  });

  it("keeps the field's keys off the list underneath", async () => {
    const wrapper = await mountShelf([adapter({ id: 7 }), adapter({ id: 8 })]);
    const store = useModelShelfStore();

    await wrapper.find(".shelf-col--base span").trigger("dblclick");
    await wrapper.find(".shelf-row-base-edit").trigger("keydown", { key: " " });
    expect(store.selectedRows).toHaveLength(0);
  });

  it("writes every member of a run, which was trained against one base", async () => {
    const wrapper = await mountShelf([
      adapter({ id: 1, stack_id: 5, training_step: 250 }),
      adapter({ id: 2, stack_id: 5, training_step: 500 }),
    ]);
    const store = useModelShelfStore();
    const edit = vi.spyOn(store, "editModelIds").mockResolvedValue(true);

    await wrapper.find(".shelf-col--base span").trigger("dblclick");
    const field = wrapper.find(".shelf-row-base-edit");
    await field.setValue("FLUX.2");
    await field.trigger("keydown", { key: "Enter" });
    expect(edit).toHaveBeenCalledWith([1, 2], { base_model: "FLUX.2" });
  });
});

describe("file kinds", () => {
  it("never renders an unclassified file as a checkpoint", async () => {
    // `unknown` is in neither of the other two lists (see api/modelShelf.js),
    // so the row reaches the shelf only through the Unclassified block.
    useModelShelfStore().setFilters({
      adapters: false,
      checkpoints: false,
      unclassified: true,
    });
    const wrapper = await mountShelf(
      [],
      [],
      [adapter({ file_kind: "unknown", kind: null, display_name: "aurora" })],
    );
    const kind = textOf(wrapper.find(".shelf-row .shelf-col"));
    expect(kind).toContain("Unclassified");
    expect(kind).not.toContain("Checkpoint");
  });

  it("names a checkpoint as one, having no algorithm to name", async () => {
    const wrapper = await mountShelf(
      [],
      [adapter({ file_kind: "checkpoint", kind: null })],
    );
    expect(textOf(wrapper.find(".shelf-row .shelf-col"))).toContain(
      "Checkpoint",
    );
  });

  it("still names an adapter whose algorithm folds to nothing", async () => {
    // The hub CHECK is `kind IS NOT NULL`, not non-empty, so a whitespace-only
    // kind is reachable over the raw API. The cell falls back to `Adapter`
    // rather than going blank: an empty Kind column reads as a broken row, and
    // "it is an adapter, we cannot name the algorithm" is the actual fact.
    const wrapper = await mountShelf([adapter({ kind: "  " })]);
    expect(textOf(wrapper.find(".shelf-row .shelf-col"))).toContain("Adapter");
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
    // Two glyphs, not three: the mark leads the NAME line now, and a present
    // row has no absence to announce. The two that do are told apart by shape
    // and not only by hue, which is the rule that matters here.
    expect(slots).toHaveLength(2);
    expect(slots[0].classes()).toContain("shelf-row-loc--missing");
    expect(slots[1].classes()).toContain("shelf-row-loc--unreachable");
    const rows = wrapper.findAll(".shelf-row");
    expect(rows[2].find(".shelf-row-loc").exists()).toBe(false);
  });

  it("says where the file is on an axis that draws no folder header", async () => {
    // Only `groupBy: 'folder'` puts the folder on screen. Every other axis —
    // and `none`, the default this mounts with — left the reader with no way
    // to tell a copy on the spare disk from one in the ComfyUI tree.
    const wrapper = await mountShelf([
      adapter({
        locations: [
          { state: "present", folder_path: "/home/me/models", relpath: "a.st" },
          { state: "present", folder_path: "/media/me/spare", relpath: "a.st" },
        ],
      }),
    ]);
    expect(wrapper.find(".shelf-row-file").attributes("title")).toBe(
      "/home/me/models/a.st\n/media/me/spare/a.st",
    );
  });

  it("names THIS copy on a folder-grouped row, not the other folder's", async () => {
    // The draw stands for one copy — that is why it already reports that
    // copy's state rather than the merged one. A tooltip reading the merged
    // array put a path where the file IS on the row that says it is gone.
    const wrapper = await mountShelf([
      adapter({
        locations: [
          { state: "present", folder_path: "/home/me/models", relpath: "a.st" },
          { state: "missing", folder_path: "/media/me/spare", relpath: "a.st" },
        ],
      }),
    ]);
    useModelShelfStore().setView({ groupBy: "folder" });
    await wrapper.vm.$nextTick();
    const titles = wrapper
      .findAll(".shelf-row-file")
      .map((el) => el.attributes("title"));
    expect(titles).toEqual([
      "/home/me/models/a.st",
      "/media/me/spare/a.st · not where it was",
    ]);
  });

  it("says where each step of an expanded run is", async () => {
    // A member is a shelf row drawn by a second binding, and a run's steps can
    // sit in different folders from each other.
    const wrapper = await mountShelf([
      adapter({ id: 1, stack_id: 7, stack_position: 0 }),
      adapter({
        id: 2,
        stack_id: 7,
        stack_position: 1,
        sha256: "b".repeat(64),
        locations: [
          { state: "present", folder_path: "/media/me/spare", relpath: "b.st" },
        ],
      }),
    ]);
    await wrapper.find(".shelf-row").trigger("keydown", { key: "ArrowRight" });
    expect(
      wrapper.find(".shelf-row--member .shelf-row-file").attributes("title"),
    ).toBe("/media/me/spare/b.st");
  });

  it("offers no tooltip at all when every copy has been forgotten", async () => {
    // An empty `title` is a tooltip that flashes and says nothing. The file
    // line already carries the words for this state.
    const wrapper = await mountShelf([adapter({ locations: [] })]);
    expect(wrapper.find(".shelf-row-file").attributes("title")).toBeUndefined();
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
      engines: false,
      support: false,
    });
    await wrapper.vm.$nextTick();
    expect(textOf(wrapper.find(".shelf-state"))).toContain(
      "Nothing is selected in Show",
    );
  });

  it("draws the engines when Engines is the only block ticked", async () => {
    // The reported bug, at the layer it was seen: the toolbar counted the
    // engine rows and the body drew "Nothing is selected in Show" over the
    // top of them, because the check knew about three blocks out of four.
    const wrapper = await mountShelf([adapter()]);
    listEngines.mockResolvedValue([
      adapter({
        id: 900,
        file_kind: "engine",
        kind: "captioner",
        display_name: "JoyCaption",
      }),
    ]);
    await useModelShelfStore().setFilters(
      { adapters: false, checkpoints: false, unclassified: false },
      { refetch: true },
    );
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".shelf-state").exists()).toBe(false);
    expect(textOf(wrapper.find(".shelf-row"))).toContain("JoyCaption");
  });

  it("offers Reset rather than 'add a folder' when a narrowed shelf is empty", async () => {
    // A narrowed selection only FETCHES the blocks it asks for, so a shelf
    // reopened with one empty block ticked arrives with no rows at all on a
    // machine full of models. Reading that as the terminal "there is nothing
    // here" leaves the reader no way back.
    const wrapper = await mountShelf([]);
    await useModelShelfStore().setFilters(
      { adapters: false, checkpoints: false, unclassified: false },
      { refetch: true },
    );
    await wrapper.vm.$nextTick();
    const state = textOf(wrapper.find(".shelf-state"));
    expect(state).toContain("No models match these filters");
    expect(state).toContain("Reset filters");
  });
});

describe("after a session reset", () => {
  it("refetches rather than claiming the machine has no models", async () => {
    // Logout, login, a share token or a restore empties the store. Nothing
    // refetched, so the shelf showed its terminal "there is nothing here"
    // state for a library that had simply not been asked.
    const wrapper = await mountShelf([adapter()]);
    expect(wrapper.find(".shelf-row").exists()).toBe(true);

    listAdapters.mockClear();
    useModelShelfStore().resetForSession();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await wrapper.vm.$nextTick();

    expect(listAdapters).toHaveBeenCalled();
    expect(textOf(wrapper.find(".shelf-body"))).not.toContain(
      "No models found",
    );
    expect(wrapper.find(".shelf-row").exists()).toBe(true);
  });
});

describe("keyboard", () => {
  it("takes focus on mount, not one round trip later", async () => {
    // Focusing after the fetch discarded wherever the user had moved in the
    // meantime. DuplicateQueue, the contract this view mirrors, is synchronous.
    listAdapters.mockReturnValue(new Promise(() => {}));
    listCheckpoints.mockReturnValue(new Promise(() => {}));
    const wrapper = mount(ModelShelf, {
      ...globalOpts,
      attachTo: document.body,
    });
    await wrapper.vm.$nextTick();

    expect(document.activeElement).toBe(wrapper.find(".shelf").element);

    wrapper.unmount();
  });

  it("gives the rows ONE tab stop between them, not 1,800", async () => {
    // The rule that used to read "rows are not focus stops" — 1,800 empty stops
    // is a trap. F3 gave a row something to do, so it is now a roving tabindex
    // rather than no tabindex: exactly one row is reachable by Tab and the
    // arrows move which one.
    const wrapper = await mountShelf([
      adapter({ id: 1 }),
      adapter({ id: 2 }),
      adapter({ id: 3 }),
    ]);
    const stops = wrapper
      .findAll(".shelf-row")
      .map((r) => r.attributes("tabindex"));
    expect(stops.filter((t) => t === "0")).toHaveLength(1);
    expect(stops.filter((t) => t === "-1")).toHaveLength(2);
    expect(wrapper.find(".shelf").attributes("tabindex")).toBe("-1");
  });
});

describe("the shelf's own accessible name", () => {
  it("carries a role that is allowed to have one", async () => {
    // A bare div is role `generic`, which prohibits an accessible name, so
    // both aria-label and aria-describedby are dropped and the whole
    // #shelf-help paragraph is never announced.
    const wrapper = await mountShelf([adapter()]);
    const root = wrapper.find(".shelf");
    expect(root.attributes("role")).toBe("region");
    expect(root.attributes("aria-label")).toBe("Model shelf");
    expect(root.attributes("aria-describedby")).toBe("shelf-help");
    expect(wrapper.find("#shelf-help").exists()).toBe(true);
  });

  it("describes the tail that is actually there", async () => {
    // The description is the only account of the bar a screen-reader user
    // gets, so it is the one place a removed control can go on existing for
    // them alone. It named Undo and Redo until the shelf stopped mounting
    // them.
    const wrapper = await mountShelf([adapter()]);
    const help = wrapper.find("#shelf-help").text();
    expect(help).toContain("Settings and the stats sidebar toggle");
    expect(help).toMatch(/nothing on this screen can be undone/i);
    expect(help).not.toMatch(/undo and redo/i);
  });
});

// ── F2: group headers and the Sort split-button ────────────────────────────

describe("group headers", () => {
  it("draws no header at all while the list is ungrouped", async () => {
    // The flat F1 list and the grouped list are ONE piece of markup with the
    // header switched off, not two copies of the row template.
    const wrapper = await mountShelf([adapter()]);
    expect(wrapper.find(".shelf-group-btn").exists()).toBe(false);
    expect(wrapper.findAll(".shelf-row").length).toBe(1);
  });

  it("states the group and its size, and collapses to the header alone", async () => {
    const wrapper = await mountShelf([
      adapter({ id: 1, base_model: "sdxl" }),
      adapter({ id: 2, base_model: "sdxl" }),
    ]);
    useModelShelfStore().setView({ groupBy: "base_model" });
    await wrapper.vm.$nextTick();

    const header = wrapper.find(".shelf-group-btn");
    expect(textOf(header)).toContain("sdxl");
    expect(textOf(header)).toContain("2 models");
    expect(header.attributes("aria-expanded")).toBe("true");
    // The accessible name carries the count, so the reader hears how big the
    // group is BEFORE deciding to open it.
    expect(header.attributes("aria-label")).toBe("sdxl, 2 models");

    await header.trigger("click");
    expect(wrapper.find(".shelf-group-btn").attributes("aria-expanded")).toBe(
      "false",
    );
    expect(wrapper.findAll(".shelf-row").length).toBe(0);
    // The header survives with its count: a group that vanished would be
    // indistinguishable from one the filters removed.
    expect(textOf(wrapper.find(".shelf-group-btn"))).toContain("2 models");
  });

  it("is a real button, so Tab still moves group to group", async () => {
    // The header stays a stop of its own. It shared that job with nothing while
    // rows carried no verb; now it shares it with the list's single roving
    // stop, which is one Tab press away rather than 1,800.
    const wrapper = await mountShelf([adapter({ base_model: "sdxl" })]);
    useModelShelfStore().setView({ groupBy: "base_model" });
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".shelf-group-btn").element.tagName).toBe("BUTTON");
    expect(wrapper.find(".shelf-group-heading").element.tagName).toBe("H3");
  });

  it("sets a folder path in the path variant, never uppercased", async () => {
    // §3 gives the mono face to file paths, and uppercasing one misstates the
    // string. A base-model name is a label and takes the other treatment.
    const wrapper = await mountShelf([
      adapter({
        locations: [{ state: "present", folder_path: "/mnt/Models" }],
      }),
    ]);
    const store = useModelShelfStore();
    store.setView({ groupBy: "folder" });
    await wrapper.vm.$nextTick();
    const label = wrapper.find(".shelf-group-label");
    expect(label.classes()).toContain("shelf-group-label--path");
    expect(label.text()).toBe("/mnt/Models");

    store.setView({ groupBy: "base_model" });
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".shelf-group-label").classes()).toContain(
      "shelf-group-label--name",
    );
  });

  it("counts models and copies apart when a model sits in two folders", async () => {
    const wrapper = await mountShelf([
      adapter({
        locations: [
          { state: "present", folder_path: "/a" },
          { state: "present", folder_path: "/b" },
        ],
      }),
    ]);
    useModelShelfStore().setView({ groupBy: "folder" });
    await wrapper.vm.$nextTick();
    expect(textOf(wrapper.find(".shelf-sub"))).toBe("1 model · 2 copies");
  });
});

describe("the Sort split-button", () => {
  it("names its direction so a keyboard user hears it on focus return", async () => {
    // The accessible name IS the state and flips on press. "Ascending" would be
    // useless on a date column and backwards on a size one, so each key words
    // its own two ends.
    const wrapper = await mountShelf([adapter()]);
    const store = useModelShelfStore();
    store.setView({ sortKey: "size", sortDirection: "desc" });
    await wrapper.vm.$nextTick();

    const toggle = wrapper.find(".bar-split-toggle");
    expect(toggle.attributes("aria-label")).toBe("Largest first");
    await toggle.trigger("click");
    expect(store.view.sortDirection).toBe("asc");
    expect(wrapper.find(".bar-split-toggle").attributes("aria-label")).toBe(
      "Smallest first",
    );
  });

  it("claims a dialog rather than a menu, because the panel is not one", async () => {
    // `aria-haspopup="menu"` promises a role="menu" popup with roving arrow
    // keys. The .tbm panel is a div of grouped toggles and implements none of
    // that; the same reasoning rejected role="listbox"/option here before.
    const wrapper = await mountShelf([adapter()]);
    const trigger = wrapper.find(".bar-split-menu");
    expect(trigger.attributes("aria-haspopup")).toBe("dialog");
    expect(trigger.attributes("aria-expanded")).toBe("false");
    expect(wrapper.find(".bar-split-button").attributes("role")).toBe("group");
  });

  it("announces a resort, because the rows reorder silently", async () => {
    const wrapper = await mountShelf([adapter()]);
    useModelShelfStore().setView({ sortKey: "name", sortDirection: "asc" });
    await wrapper.vm.$nextTick();
    const status = wrapper.find('[role="status"]');
    expect(status.text()).toBe("Sorted by name: A to Z");
  });
});

describe("selecting rows", () => {
  const rowAt = (wrapper, i) => wrapper.findAll(".shelf-row")[i];

  it("selects by model, so one file drawn in two folders is one selection", async () => {
    // Under folder grouping a model with copies in two folders is DRAWN twice.
    // The verbs write the model, so a per-row selection would let the same file
    // be half selected and ask the reader to hold a distinction the data has
    // not got.
    const wrapper = await mountShelf([
      adapter({
        locations: [
          { state: "present", folder_id: 1, folder_path: "/a", relpath: "x" },
          { state: "present", folder_id: 2, folder_path: "/b", relpath: "x" },
        ],
      }),
    ]);
    useModelShelfStore().setView({ groupBy: "folder" });
    await wrapper.vm.$nextTick();

    expect(wrapper.findAll(".shelf-row")).toHaveLength(2);
    await rowAt(wrapper, 0).trigger("click");
    await wrapper.vm.$nextTick();

    expect(useModelShelfStore().selectedRows).toHaveLength(1);
    expect(wrapper.findAll(".shelf-row--selected")).toHaveLength(2);
  });

  it("replaces the selection on a plain click", async () => {
    // The file-manager rule, and the one a checkbox got wrong: a bare click is
    // not a toggle, it starts again.
    const wrapper = await mountShelf([adapter({ id: 1 }), adapter({ id: 2 })]);
    const store = useModelShelfStore();
    await rowAt(wrapper, 0).trigger("click");
    await rowAt(wrapper, 1).trigger("click");
    expect([...store.selectedIds]).toEqual([2]);
  });

  it("toggles one row on Ctrl+click and leaves the rest alone", async () => {
    const wrapper = await mountShelf([
      adapter({ id: 1 }),
      adapter({ id: 2 }),
      adapter({ id: 3 }),
    ]);
    const store = useModelShelfStore();
    await rowAt(wrapper, 0).trigger("click");
    await rowAt(wrapper, 2).trigger("click", { ctrlKey: true });
    expect([...store.selectedIds].sort()).toEqual([1, 3]);

    await rowAt(wrapper, 2).trigger("click", { ctrlKey: true });
    expect([...store.selectedIds]).toEqual([1]);
  });

  it("takes the range from the anchor on Shift+click, and replaces", async () => {
    // Replace rather than merge, exactly as the grid does: it is what makes a
    // mis-aimed range one click to correct instead of two.
    const wrapper = await mountShelf([
      adapter({ id: 1 }),
      adapter({ id: 2 }),
      adapter({ id: 3 }),
      adapter({ id: 4 }),
    ]);
    const store = useModelShelfStore();
    await rowAt(wrapper, 1).trigger("click");
    await rowAt(wrapper, 3).trigger("click", { shiftKey: true });
    expect([...store.selectedIds].sort()).toEqual([2, 3, 4]);

    // The anchor stays put, so shrinking the range back measures from the same
    // end rather than from wherever the last click landed.
    await rowAt(wrapper, 2).trigger("click", { shiftKey: true });
    expect([...store.selectedIds].sort()).toEqual([2, 3]);
  });

  it("ranges upward as readily as downward", async () => {
    const wrapper = await mountShelf([
      adapter({ id: 1 }),
      adapter({ id: 2 }),
      adapter({ id: 3 }),
    ]);
    const store = useModelShelfStore();
    await rowAt(wrapper, 2).trigger("click");
    await rowAt(wrapper, 0).trigger("click", { shiftKey: true });
    expect([...store.selectedIds].sort()).toEqual([1, 2, 3]);
  });

  it("is a multi-select treegrid, and says which rows are selected", async () => {
    // The role is what tells a screen reader this list is selectable at all;
    // it replaced the per-row checkbox that used to carry that meaning. A
    // treegrid rather than a listbox since the rows became columns (#891):
    // only a grid can carry a `columnheader`, and only a TREEgrid has the
    // Right/Left disclosure this list already implemented.
    const wrapper = await mountShelf([adapter({ id: 1 }), adapter({ id: 2 })]);
    const list = wrapper.find("ul.shelf-list");
    expect(list.attributes("role")).toBe("treegrid");
    expect(list.attributes("aria-multiselectable")).toBe("true");

    await rowAt(wrapper, 0).trigger("click");
    await wrapper.vm.$nextTick();
    expect(rowAt(wrapper, 0).attributes("aria-selected")).toBe("true");
    expect(rowAt(wrapper, 1).attributes("aria-selected")).toBe("false");
  });

  it("keeps exactly one tab stop, seeded so the list is reachable", async () => {
    // A roving tabindex with nothing at 0 makes the whole list unreachable by
    // Tab, which is the failure this seeding exists to prevent.
    const wrapper = await mountShelf([adapter({ id: 1 }), adapter({ id: 2 })]);
    const stops = wrapper
      .findAll(".shelf-row")
      .map((r) => r.attributes("tabindex"));
    expect(stops).toEqual(["0", "-1"]);
  });

  it("moves the tab stop on an arrow without selecting anything", async () => {
    // Walking 1,800 rows must not arm a verb against every one passed.
    const wrapper = await mountShelf([adapter({ id: 1 }), adapter({ id: 2 })]);
    const store = useModelShelfStore();
    await rowAt(wrapper, 0).trigger("keydown", { key: "ArrowDown" });
    await wrapper.vm.$nextTick();

    expect(store.selectedRows).toHaveLength(0);
    expect(
      wrapper.findAll(".shelf-row").map((r) => r.attributes("tabindex")),
    ).toEqual(["-1", "0"]);
  });

  it("extends with Shift+arrow and clears with Escape", async () => {
    const wrapper = await mountShelf([
      adapter({ id: 1 }),
      adapter({ id: 2 }),
      adapter({ id: 3 }),
    ]);
    // Attached, because Escape is handled on the window: a keydown on a
    // detached row bubbles into nothing and would pass for the wrong reason.
    document.body.appendChild(wrapper.element);
    const store = useModelShelfStore();
    await rowAt(wrapper, 0).trigger("keydown", { key: " " });
    await rowAt(wrapper, 0).trigger("keydown", {
      key: "ArrowDown",
      shiftKey: true,
    });
    expect([...store.selectedIds].sort()).toEqual([1, 2]);

    await rowAt(wrapper, 0).trigger("keydown", { key: "Escape" });
    expect(store.selectedRows).toHaveLength(0);
    wrapper.unmount();
  });

  it("shows no selection bar until something is selected", async () => {
    const wrapper = await mountShelf([adapter()]);
    expect(wrapper.find(".shelf-selbar").exists()).toBe(false);

    await rowAt(wrapper, 0).trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".selbar-count").attributes("title")).toContain(
      "1 model selected",
    );
  });

  it("drops a selected model that the shelf no longer holds", async () => {
    // Without the prune the bar counts rows that are not on screen and the next
    // verb posts an id the server has to refuse.
    const wrapper = await mountShelf([adapter({ id: 1 }), adapter({ id: 2 })]);
    const store = useModelShelfStore();
    await rowAt(wrapper, 0).trigger("click");
    await rowAt(wrapper, 1).trigger("click", { ctrlKey: true });
    expect(store.selectedRows).toHaveLength(2);

    listAdapters.mockResolvedValue([adapter({ id: 1 })]);
    await store.fetchRows();
    await wrapper.vm.$nextTick();

    expect([...store.selectedIds]).toEqual([1]);
  });
});

describe("drive bands", () => {
  const inFolder = (id, folderId, path) =>
    adapter({
      id,
      sha256: String(id).repeat(64).slice(0, 64),
      locations: [
        {
          state: "present",
          folder_id: folderId,
          folder_path: path,
          relpath: "a.st",
        },
      ],
    });

  it("draws one band per drive, with a meter that reads free space first", async () => {
    listModelFolderDevices.mockResolvedValue([
      {
        device_id: "9",
        mount_point: "/mnt/fast",
        label: "FastModels",
        total_bytes: 1024 ** 4,
        free_bytes: 512 * 1024 ** 3,
        shelf_bytes: 256 * 1024 ** 3,
        folder_ids: [1, 2],
      },
    ]);
    const wrapper = await mountShelf([
      inFolder(1, 1, "/mnt/fast/loras"),
      inFolder(2, 2, "/mnt/fast/checkpoints"),
    ]);
    useModelShelfStore().setView({ groupBy: "folder", folderLayout: "drive" });
    await wrapper.vm.$nextTick();

    const bands = wrapper.findAll(".shelf-band");
    expect(bands).toHaveLength(1);
    // The volume's name, not its mount point: a Linux mount point runs to
    // `/media/<user>/A1B2C3D4E5F60789` and crowds the header out.
    expect(textOf(bands[0])).toContain("FastModels");
    // And the mount point beside it, in the mono face: the volume label answers
    // "which disk" and the path answers "which one is that", which on a machine
    // with two identically-named drives is the only half that does.
    expect(textOf(bands[0].find(".shelf-band-path"))).toBe("/mnt/fast");
    // Free leads: it is the number that decides whether the next checkpoint
    // fits, and the meter is read at a glance rather than computed from.
    expect(textOf(bands[0])).toContain("512.0 GB free of 1.0 TB");

    // Three segments carving up the track, in the legend's order, and their
    // widths add to the whole drive. Two overlaid fills could not say which of
    // "how full is the disk" and "how much is ours" a boundary marked (#893).
    const segs = wrapper.findAll(".shelf-band-seg");
    expect(segs).toHaveLength(3);
    expect(segs.map((s) => s.attributes("style"))).toEqual([
      "width: 25%;", // 256 GB on the shelf
      "width: 25%;", // 256 GB belonging to anything else
      "width: 50%;", // 512 GB free
    ]);
    // The meter is decorative: the same figures are already visible text in
    // this heading, so labelling it made every band announce them twice.
    expect(wrapper.find(".shelf-band-meter").attributes("aria-hidden")).toBe(
      "true",
    );
    expect(
      wrapper.find(".shelf-band-meter").attributes("role"),
    ).toBeUndefined();
  });

  it("keys the meter once for the view, not once per band", async () => {
    listModelFolderDevices.mockResolvedValue([
      {
        device_id: "9",
        mount_point: "/mnt/fast",
        label: "FastModels",
        total_bytes: 1024 ** 4,
        free_bytes: 512 * 1024 ** 3,
        shelf_bytes: 256 * 1024 ** 3,
        folder_ids: [1],
      },
      {
        device_id: "10",
        mount_point: "/mnt/slow",
        label: "SlowModels",
        total_bytes: 1024 ** 4,
        free_bytes: 512 * 1024 ** 3,
        shelf_bytes: 128 * 1024 ** 3,
        folder_ids: [2],
      },
    ]);
    const wrapper = await mountShelf([
      inFolder(1, 1, "/mnt/fast/loras"),
      inFolder(2, 2, "/mnt/slow/checkpoints"),
    ]);
    useModelShelfStore().setView({ groupBy: "folder", folderLayout: "drive" });
    await wrapper.vm.$nextTick();

    expect(wrapper.findAll(".shelf-band")).toHaveLength(2);
    expect(wrapper.findAll(".shelf-keys")).toHaveLength(1);
    expect(textOf(wrapper.find(".shelf-keys"))).toContain("On the shelf");
    expect(textOf(wrapper.find(".shelf-keys"))).toContain("Other files");
    expect(textOf(wrapper.find(".shelf-keys"))).toContain("Free");
  });

  it("draws no key when no meter is on screen to key", async () => {
    // An unmeasured drive renders no meter, so a shelf of only offline drives
    // would otherwise print a key to a picture nobody can see.
    listModelFolderDevices.mockResolvedValue([
      {
        device_id: null,
        mount_point: "/net/models",
        total_bytes: null,
        free_bytes: null,
        shelf_bytes: 0,
        folder_ids: [7],
      },
    ]);
    const wrapper = await mountShelf([inFolder(1, 7, "/net/models/loras")]);
    useModelShelfStore().setView({ groupBy: "folder", folderLayout: "drive" });
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".shelf-band").exists()).toBe(true);
    expect(wrapper.find(".shelf-keys").exists()).toBe(false);
  });

  it("warns when a drive has no room for the next checkpoint", async () => {
    listModelFolderDevices.mockResolvedValue([
      {
        device_id: "9",
        mount_point: "/mnt/fast",
        label: "FastModels",
        total_bytes: 1024 ** 4,
        free_bytes: 8 * 1024 ** 3,
        shelf_bytes: 512 * 1024 ** 3,
        folder_ids: [1],
      },
    ]);
    const wrapper = await mountShelf([inFolder(1, 1, "/mnt/fast/loras")]);
    useModelShelfStore().setView({ groupBy: "folder", folderLayout: "drive" });
    await wrapper.vm.$nextTick();

    // The word carries the state in greyscale, and in a screen reader, without
    // a live region: this is a standing fact about a disk, not an event.
    expect(textOf(wrapper.find(".shelf-band"))).toContain(
      "Only 8.0 GB free of 1.0 TB",
    );
    expect(wrapper.find(".shelf-band-meter--low").exists()).toBe(true);
    expect(wrapper.find(".shelf-band-figures--low").exists()).toBe(true);
    expect(wrapper.find(".shelf-band").attributes("role")).not.toBe("alert");
  });

  it("leaves a roomy drive unwarned however full it looks", async () => {
    // 400 GB left is a tenth of this drive. A percentage rule would cry wolf
    // here, on exactly the hardware people keep models on.
    listModelFolderDevices.mockResolvedValue([
      {
        device_id: "9",
        mount_point: "/mnt/fast",
        label: "FastModels",
        total_bytes: 4000 * 1024 ** 3,
        free_bytes: 400 * 1024 ** 3,
        shelf_bytes: 1024 ** 3,
        folder_ids: [1],
      },
    ]);
    const wrapper = await mountShelf([inFolder(1, 1, "/mnt/fast/loras")]);
    useModelShelfStore().setView({ groupBy: "folder", folderLayout: "drive" });
    await wrapper.vm.$nextTick();

    expect(textOf(wrapper.find(".shelf-band"))).not.toContain("Only");
    expect(wrapper.find(".shelf-band-meter--low").exists()).toBe(false);
  });

  it("says a drive is unknown rather than drawing it empty", async () => {
    // An empty meter reads as a drive with nothing on it, which is the one
    // thing "we could not measure it" does not mean.
    listModelFolderDevices.mockResolvedValue([
      {
        device_id: null,
        mount_point: "/net/models",
        total_bytes: null,
        free_bytes: null,
        shelf_bytes: 0,
        folder_ids: [7],
      },
    ]);
    const wrapper = await mountShelf([inFolder(1, 7, "/net/models/loras")]);
    useModelShelfStore().setView({ groupBy: "folder", folderLayout: "drive" });
    await wrapper.vm.$nextTick();

    expect(textOf(wrapper.find(".shelf-band"))).toContain("Capacity unknown");
    expect(wrapper.find(".shelf-band-meter").exists()).toBe(false);
  });

  it("draws no band at all under the A to Z layout", async () => {
    // The band is the drive layout's own tier. Folder-alphabetical is one flat
    // run of folder headers, which is what it was before F2.
    listModelFolderDevices.mockResolvedValue([
      {
        device_id: "9",
        mount_point: "/mnt/fast",
        total_bytes: 1000,
        free_bytes: 400,
        shelf_bytes: 100,
        folder_ids: [1],
      },
    ]);
    const wrapper = await mountShelf([inFolder(1, 1, "/mnt/fast/loras")]);
    useModelShelfStore().setView({ groupBy: "folder", folderLayout: "alpha" });
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".shelf-band").exists()).toBe(false);
    expect(wrapper.findAll(".shelf-group-btn").length).toBeGreaterThan(0);
  });
});

describe("what a folder header says about its folder (#899)", () => {
  const inFolder = (id, folderId, path, state = "present") =>
    adapter({
      id,
      sha256: String(id).repeat(64).slice(0, 64),
      locations: [
        { state, folder_id: folderId, folder_path: path, relpath: "a.st" },
      ],
    });

  async function mountFolders(rows, { folders = [], devices = [] } = {}) {
    listModelFolders.mockResolvedValue(folders);
    listModelFolderDevices.mockResolvedValue(devices);
    const wrapper = await mountShelf(rows);
    useModelShelfStore().setView({ groupBy: "folder", folderLayout: "alpha" });
    await wrapper.vm.$nextTick();
    return wrapper;
  }

  it("rails the folders on one drive in one colour, and names the volume", async () => {
    const wrapper = await mountFolders(
      [
        inFolder(1, 1, "/mnt/fast/loras"),
        inFolder(2, 2, "/mnt/fast/ckpt"),
        inFolder(3, 3, "/ext/loras"),
      ],
      {
        folders: [
          { id: 1, path: "/mnt/fast/loras", kind: "user", file_count: 1 },
          { id: 2, path: "/mnt/fast/ckpt", kind: "user", file_count: 1 },
          { id: 3, path: "/ext/loras", kind: "user", file_count: 1 },
        ],
        devices: [
          { device_id: "9", label: "FastModels", folder_ids: [1, 2] },
          { device_id: "12", label: "Archive", folder_ids: [3] },
        ],
      },
    );
    // Alphabetical by path under this layout: /ext/loras, then the two on
    // /mnt/fast.
    const headers = wrapper.findAll(".shelf-group-btn");
    const rails = headers.map((btn) => btn.attributes("style") || "");
    expect(rails[1]).toContain("border-left-color");
    // Same disk, same rail; a different disk, a different one.
    expect(rails[2]).toBe(rails[1]);
    expect(rails[0]).not.toBe(rails[1]);
    // ...and the identity is a WORD, because the hue is only a grouping hint.
    expect(textOf(headers[1])).toContain("FastModels");
    expect(textOf(headers[0])).toContain("Archive");
  });

  it("leaves the drive chip to the band when there is a band", async () => {
    // Under `Drive, then folder` the band above IS the chip. Repeating it on
    // every folder under it would be noise rather than a second signal.
    listModelFolders.mockResolvedValue([
      { id: 1, path: "/mnt/fast/loras", kind: "user", file_count: 1 },
    ]);
    listModelFolderDevices.mockResolvedValue([
      { device_id: "9", label: "FastModels", folder_ids: [1] },
    ]);
    const wrapper = await mountShelf([inFolder(1, 1, "/mnt/fast/loras")]);
    useModelShelfStore().setView({ groupBy: "folder", folderLayout: "drive" });
    await wrapper.vm.$nextTick();

    expect(textOf(wrapper.find(".shelf-band"))).toContain("FastModels");
    expect(textOf(wrapper.find(".shelf-group-btn"))).not.toContain(
      "FastModels",
    );
    // The rail still runs down the header: it is what says the two folders
    // under one band belong together once the band has scrolled away.
    expect(wrapper.find(".shelf-group-btn").attributes("style")).toContain(
      "border-left-color",
    );
  });

  it("states the tier in a word and a glyph, on the header itself", async () => {
    const wrapper = await mountFolders(
      [
        inFolder(1, 1, "/store"),
        inFolder(2, 2, "/hf"),
        inFolder(3, 3, "/mine"),
      ],
      {
        folders: [
          { id: 1, path: "/store", kind: "managed", file_count: 1 },
          { id: 2, path: "/hf", kind: "foreign", file_count: 1 },
          { id: 3, path: "/mine", kind: "user", file_count: 1 },
        ],
      },
    );
    const headers = wrapper.findAll(".shelf-group-btn");
    const chips = headers.map((h) =>
      h.findAll(".shelf-chip").map((c) => textOf(c)),
    );
    // Alphabetical by path: /hf, /mine, /store.
    expect(chips[0]).toEqual(["Locked"]);
    expect(chips[1]).toEqual([]);
    expect(chips[2]).toEqual(["Managed"]);
    // The reader who never opens the header still hears all three.
    expect(headers[2].attributes("aria-label")).toBe(
      "/store, Managed, 1 model",
    );
  });

  it("marks an offline folder without ever using the error treatment", async () => {
    const wrapper = await mountFolders(
      [inFolder(1, 1, "/ext/loras", "unreachable")],
      {
        folders: [{ id: 1, path: "/ext/loras", kind: "user", file_count: 1 }],
      },
    );
    const header = wrapper.find(".shelf-group-btn");
    expect(header.classes()).toContain("shelf-group-btn--offline");
    // A word as well as the dashed rail, so nothing here is carried by colour.
    expect(textOf(header)).toContain("Offline");
    expect(header.attributes("aria-label")).toContain("offline");
    // The dashed rail replaces the drive hue rather than joining it: we cannot
    // see the disk, so nothing on this header may claim which one it is.
    expect(header.attributes("style") || "").not.toContain("border-left-color");
  });

  it("indents a folder registered inside another registered folder", async () => {
    const wrapper = await mountFolders(
      [inFolder(1, 1, "/models"), inFolder(2, 2, "/models/loras")],
      {
        folders: [
          { id: 1, path: "/models", kind: "user", file_count: 1 },
          { id: 2, path: "/models/loras", kind: "user", file_count: 1 },
        ],
      },
    );
    const [parent, child] = wrapper.findAll(".shelf-group-btn");
    // `--depth` is the shared row system's own indent, not a private padding.
    expect(parent.attributes("style") || "").not.toContain("--depth");
    expect(child.attributes("style")).toContain("--depth: 1");
  });
});

describe("the group header's reserved column", () => {
  it("carries the axis glyph rather than an empty gap", async () => {
    // The width is reserved either way so the header's label lines up with the
    // row names. Left empty it reads as a thumbnail that failed to load.
    const wrapper = await mountShelf([adapter()]);
    useModelShelfStore().setView({ groupBy: "folder", folderLayout: "alpha" });
    await wrapper.vm.$nextTick();

    const mark = wrapper.find(".shelf-group-mark");
    expect(mark.exists()).toBe(true);
    expect(mark.element.tagName.toLowerCase()).toBe("v-icon-stub");
  });

  it("draws each FEATURE's own glyph there, not one axis glyph on every header", async () => {
    // The bug: the header fell back to the axis's glyph for want of its own, so
    // every feature wore one star. Asserted on the rendered mark rather than on
    // the store's field, because the fallback lives in the template and the
    // store test passes either way.
    listAdapters.mockResolvedValue([
      adapter({ id: 1, capabilities: ["tagger"] }),
      adapter({ id: 2, capabilities: ["face"] }),
    ]);
    listCheckpoints.mockResolvedValue([]);
    // The auto-stub swallows its slot, which is why the test above can only
    // reach the tag name. This one has to read the glyph, so `v-icon` renders.
    const wrapper = mount(ModelShelf, {
      global: {
        ...globalOpts.global,
        stubs: {
          ...globalOpts.global.stubs,
          "v-icon": { template: "<i><slot /></i>" },
        },
      },
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    await wrapper.vm.$nextTick();
    useModelShelfStore().setView({ groupBy: "feature" });
    await wrapper.vm.$nextTick();

    const marks = wrapper
      .findAll(".shelf-group-mark")
      .map((mark) => mark.text());
    expect(marks.sort()).toEqual(["mdi-face-recognition", "mdi-tag-outline"]);
  });
});

describe("selection and drive bands together", () => {
  it("selects a row that sits under a band, and bands do not become rows", async () => {
    // The seam this merge created: F2 renders `shownGroups` (banded) where F1
    // rendered `store.groups`, and F3 puts a checkbox inside the row loop. If
    // `bandGroups` ever dropped `rows` on its way through, the list would draw
    // headers over nothing and the bar would never open.
    listModelFolderDevices.mockResolvedValue([
      {
        device_id: "9",
        mount_point: "/mnt/fast",
        label: "FastModels",
        total_bytes: 1000,
        free_bytes: 400,
        shelf_bytes: 100,
        folder_ids: [1],
      },
    ]);
    const wrapper = await mountShelf([
      adapter({
        locations: [
          {
            state: "present",
            folder_id: 1,
            folder_path: "/mnt/fast/loras",
            relpath: "a",
          },
        ],
      }),
    ]);
    useModelShelfStore().setView({ groupBy: "folder", folderLayout: "drive" });
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".shelf-band").exists()).toBe(true);
    const rows = wrapper.findAll(".shelf-row");
    expect(rows).toHaveLength(1);

    await rows[0].trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".selbar-count").attributes("title")).toContain(
      "1 model selected",
    );
    // The band is a header, not a row: it must not have become selectable.
    expect(wrapper.find(".shelf-band").attributes("role")).not.toBe("option");
  });
});

describe("focus under folder grouping, where a model is drawn twice", () => {
  const twiceDrawn = () =>
    adapter({
      id: 1,
      locations: [
        { state: "present", folder_id: 1, folder_path: "/a", relpath: "x" },
        { state: "present", folder_id: 2, folder_path: "/b", relpath: "x" },
      ],
    });

  async function mountGrouped(rows) {
    const wrapper = await mountShelf(rows);
    useModelShelfStore().setView({ groupBy: "folder", folderLayout: "alpha" });
    await wrapper.vm.$nextTick();
    return wrapper;
  }

  it("still keeps exactly one tab stop across both draws", async () => {
    // Keyed by model id, BOTH draws satisfied `row.id === rovingRowId` and both
    // carried tabindex="0" — two focusable options for one listbox position.
    const wrapper = await mountGrouped([twiceDrawn()]);
    const stops = wrapper
      .findAll(".shelf-row")
      .map((r) => r.attributes("tabindex"));
    expect(stops).toHaveLength(2);
    expect(stops.filter((t) => t === "0")).toHaveLength(1);
  });

  it("moves the stop to the second draw, not back to the first", async () => {
    // The bug the id-keying hid: `indexOf(row.id)` returned the FIRST draw's
    // index whichever draw the cursor was on, so ArrowDown from the second draw
    // moved to the second draw again.
    const wrapper = await mountGrouped([twiceDrawn()]);
    await wrapper.findAll(".shelf-row")[0].trigger("keydown", {
      key: "ArrowDown",
    });
    await wrapper.vm.$nextTick();

    const stops = wrapper
      .findAll(".shelf-row")
      .map((r) => r.attributes("tabindex"));
    expect(stops).toEqual(["-1", "0"]);
  });

  it("gives every drawn row a key, including in the ungrouped default", async () => {
    // `rowKey` was set only where a model can be drawn twice, so in the default
    // view the list's v-for key — and everything else keyed per drawn row — was
    // undefined for every row.
    const flat = await mountShelf([adapter({ id: 1 }), adapter({ id: 2 })]);
    const keys = flat
      .findAll(".shelf-row")
      .map((r) => r.attributes("data-row-key"));
    expect(keys).toEqual(["1", "2"]);

    const grouped = await mountGrouped([twiceDrawn()]);
    const groupedKeys = grouped
      .findAll(".shelf-row")
      .map((r) => r.attributes("data-row-key"));
    expect(new Set(groupedKeys).size).toBe(2);
  });

  it("selects the model, so both of its draws light up", async () => {
    // Focus is per drawn row; selection stays per model. Clicking one draw
    // selects the model, and the other draw of it must show as selected too.
    const wrapper = await mountGrouped([twiceDrawn()]);
    await wrapper.findAll(".shelf-row")[0].trigger("click");
    await wrapper.vm.$nextTick();

    expect(useModelShelfStore().selectedRows).toHaveLength(1);
    expect(wrapper.findAll(".shelf-row--selected")).toHaveLength(2);
  });
});

describe("a click on the row being renamed", () => {
  it("does not pick it, and does not stop the OTHER rows being picked", async () => {
    // The panel is `user-select: none` (#932) and the rename field is the one
    // thing on it that opts back in, so it is the only place a text drag can
    // still start. Released on the row underneath, that drag is a mouseup the
    // field's own `@click.stop` never sees, and the click picked the row.
    //
    // Both directions, because the scope is the whole guard: blocking every
    // click while a field is open would kill "click the next row to move on",
    // and that is not a text drag.
    const wrapper = await mountShelf([adapter({ id: 7 }), adapter({ id: 8 })]);
    const store = useModelShelfStore();
    const rows = wrapper.findAll(".shelf-row");

    await rows[0].trigger("keydown", { key: "F2" });
    await wrapper.find(".shelf-row-rename").setValue("Cyanw");

    await rows[0].trigger("click");
    expect(store.selectedRows).toHaveLength(0);

    await rows[1].trigger("click");
    expect([...store.selectedIds]).toEqual([8]);
  });
});

describe("a registered folder holding no models", () => {
  it("gets a group of its own, so the managed store is visible", async () => {
    // It is ruled to always exist and to be the default destination for a drop
    // or an import. A destination you cannot see is not a destination.
    listModelFolders.mockResolvedValue([
      {
        id: 1,
        path: "/models/loras",
        last_checked: "2026-08-10T00:00:00Z",
        file_count: 1,
      },
      { id: 2, path: "/models/store", last_checked: "2026-08-10T00:00:00Z" },
    ]);
    const wrapper = await mountShelf([
      adapter({
        locations: [
          {
            state: "present",
            folder_id: 1,
            folder_path: "/models/loras",
            relpath: "a",
          },
        ],
      }),
    ]);
    useModelShelfStore().setView({ groupBy: "folder", folderLayout: "alpha" });
    await wrapper.vm.$nextTick();

    const labels = wrapper.findAll(".shelf-group-label").map((l) => l.text());
    expect(labels).toContain("/models/store");
    expect(textOf(wrapper.find(".shelf-empty-folder"))).toBe(
      "No models in this folder.",
    );
  });

  it("says it has not been looked in yet when it has not", async () => {
    // "We have not looked" is the owner's to act on; "we looked and it is
    // empty" is not, and a bare "0 models" says neither.
    //
    // A shelf with NO models at all renders its own empty state instead of the
    // group list, which is the better answer there ("add a folder"), so the
    // case asserted is the one the owner actually hits: models elsewhere, and
    // one folder nothing has looked in.
    listModelFolders.mockResolvedValue([
      {
        id: 1,
        path: "/models/loras",
        last_checked: "2026-08-10T00:00:00Z",
        file_count: 1,
      },
      { id: 2, path: "/models/store", last_checked: null },
    ]);
    const wrapper = await mountShelf([
      adapter({
        locations: [
          {
            state: "present",
            folder_id: 1,
            folder_path: "/models/loras",
            relpath: "a",
          },
        ],
      }),
    ]);
    useModelShelfStore().setView({ groupBy: "folder", folderLayout: "alpha" });
    await wrapper.vm.$nextTick();

    expect(textOf(wrapper.find(".shelf-empty-folder"))).toBe(
      "Not scanned yet.",
    );
  });

  it("stays out of the way on every other axis", async () => {
    // The folder registry is a fact about folders. Grouping by base model and
    // seeing a folder appear would be a category error.
    listModelFolders.mockResolvedValue([
      { id: 2, path: "/models/store", last_checked: null },
    ]);
    const wrapper = await mountShelf([adapter({ base_model: "sdxl" })]);
    useModelShelfStore().setView({ groupBy: "base_model" });
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".shelf-empty-folder").exists()).toBe(false);
  });

  it("is not selectable, having no model to act on", async () => {
    // A verb armed against a folder would be a verb with nothing to write.
    listModelFolders.mockResolvedValue([
      {
        id: 1,
        path: "/models/loras",
        last_checked: "2026-08-10T00:00:00Z",
        file_count: 1,
      },
      { id: 2, path: "/models/store", last_checked: null },
    ]);
    const wrapper = await mountShelf([
      adapter({
        locations: [
          {
            state: "present",
            folder_id: 1,
            folder_path: "/models/loras",
            relpath: "a",
          },
        ],
      }),
    ]);
    useModelShelfStore().setView({ groupBy: "folder", folderLayout: "alpha" });
    await wrapper.vm.$nextTick();

    // A row, because a grid takes nothing else — but never a SELECTABLE one:
    // no `aria-selected` and no tab stop, since there is no model here to act
    // on. Its one cell spans the width rather than pretending to have columns.
    const note = wrapper.find(".shelf-empty-folder");
    expect(note.attributes("role")).toBe("row");
    expect(note.attributes("aria-selected")).toBeUndefined();
    expect(note.attributes("tabindex")).toBeUndefined();
  });
});

/** A DataTransfer stand-in: jsdom's drag events carry none. */
function transfer(types = []) {
  return {
    types,
    setData: vi.fn(function (type) {
      this.types = [...this.types, type];
    }),
    effectAllowed: "",
    dropEffect: "",
  };
}

describe("dragging models onto a folder", () => {
  const FOLDERS = [
    { id: 1, path: "/models/loras", kind: "user", movable: "per_item" },
    { id: 2, path: "/models/store", kind: "managed", movable: "root_only" },
    { id: 3, path: "/runs", kind: "source", movable: "per_item" },
  ];

  async function shelfWithFolders(rows) {
    listModelFolders.mockResolvedValue(FOLDERS);
    const wrapper = await mountShelf(rows);
    useModelShelfStore().setView({ groupBy: "folder", folderLayout: "alpha" });
    await wrapper.vm.$nextTick();
    return wrapper;
  }

  function present(folderId, path) {
    return [
      {
        state: "present",
        folder_id: folderId,
        folder_path: path,
        relpath: "a",
      },
    ];
  }

  function headerFor(wrapper, label) {
    return wrapper
      .findAll(".shelf-group-btn")
      .find((b) => b.text().includes(label));
  }

  it("marks its payload so no other drop target can claim it", async () => {
    // A model dropped on a set or character row has no meaning at all, and
    // `types` is the only thing readable during dragover — so the marker key is
    // what refuses it, before the pointer suggests the drop would work (#757).
    const wrapper = await shelfWithFolders([
      adapter({ locations: present(1, "/models/loras") }),
    ]);
    const dt = transfer();
    await wrapper.find(".shelf-row").trigger("dragstart", { dataTransfer: dt });
    expect(dt.types).toContain("application/x-pixlstash-model-files");
    expect(dt.types).not.toContain("application/x-pixlstash-pictures");
  });

  it("refuses to drag a row whose file is not on this machine", async () => {
    // The gesture could only ever end in a refusal, and the pointer would say
    // it works the whole way there.
    const wrapper = await shelfWithFolders([
      adapter({
        locations: [
          {
            state: "missing",
            folder_id: 1,
            folder_path: "/models/loras",
            relpath: "a",
          },
        ],
      }),
    ]);
    expect(wrapper.find(".shelf-row").attributes("draggable")).toBe("false");
  });

  it("never offers the drag on an engine, whatever its location says", async () => {
    // The HuggingFace cache is `blobs/` under content hashes with `snapshots/`
    // symlinking names onto them, shared with every other HF tool, and an
    // engine row's relpath there is a whole repo directory. A drag that looks
    // like it works on 116 GB of someone else's bookkeeping must not be
    // offered; the server refuses it too, and this is the half that stops the
    // gesture starting.
    const wrapper = await mountShelf([
      adapter({
        id: 700,
        file_kind: "engine",
        kind: "captioner",
        display_name: "fancyfeast/llama-joycaption",
        locations: [
          {
            state: "present",
            folder_id: 9,
            folder_path: "/home/g/.cache/huggingface/hub",
            relpath: "models--fancyfeast--llama-joycaption",
          },
        ],
      }),
    ]);
    expect(wrapper.find(".shelf-row").attributes("draggable")).toBe("false");
  });

  it("accepts the drag on a folder it may write to, and only there", async () => {
    // preventDefault() is what ACCEPTS a drop, so it is called inside the
    // handler for the payloads this target takes — never as a `.prevent`
    // modifier, which would accept a picture drag from the grid too.
    const wrapper = await shelfWithFolders([
      adapter({ locations: present(1, "/models/loras") }),
    ]);
    const dt = transfer(["application/x-pixlstash-model-files"]);

    const accepted = { preventDefault: vi.fn(), dataTransfer: dt };
    headerFor(wrapper, "/models/store").element.dispatchEvent(
      Object.assign(new Event("dragover", { cancelable: true }), accepted),
    );
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".shelf-group-btn--drop").exists()).toBe(true);
  });

  it("refuses a source folder, which is taken from and never written into", async () => {
    // `ModelMover.plan` refuses it too. Checking here as well is what stops the
    // pointer promising a drop the dialog would then have to take back.
    const wrapper = await shelfWithFolders([
      adapter({ locations: present(1, "/models/loras") }),
    ]);
    const event = Object.assign(new Event("dragover", { cancelable: true }), {
      dataTransfer: transfer(["application/x-pixlstash-model-files"]),
    });
    headerFor(wrapper, "/runs").element.dispatchEvent(event);
    await wrapper.vm.$nextTick();
    expect(event.defaultPrevented).toBe(false);
    expect(wrapper.find(".shelf-group-btn--drop").exists()).toBe(false);
  });

  it("refuses a payload it does not recognise", async () => {
    const wrapper = await shelfWithFolders([
      adapter({ locations: present(1, "/models/loras") }),
    ]);
    const event = Object.assign(new Event("dragover", { cancelable: true }), {
      dataTransfer: transfer(["application/x-pixlstash-pictures"]),
    });
    headerFor(wrapper, "/models/store").element.dispatchEvent(event);
    await wrapper.vm.$nextTick();
    // Not prevented, so the browser's own "no drop here" cursor stands.
    expect(event.defaultPrevented).toBe(false);
    expect(wrapper.find(".shelf-group-btn--drop").exists()).toBe(false);
  });

  it("opens the dialog on drop rather than moving on release", async () => {
    // There is no undo behind a move, so a 438 GB copy across a USB drive must
    // never be one slip of the pointer away from starting.
    const wrapper = await shelfWithFolders([
      adapter({ locations: present(1, "/models/loras") }),
    ]);
    const store = useModelShelfStore();
    store.toggleSelected(1);
    await wrapper.vm.$nextTick();

    const dt = transfer(["application/x-pixlstash-model-files"]);
    await headerFor(wrapper, "/models/store").trigger("drop", {
      dataTransfer: dt,
    });

    const dialog = wrapper.findComponent({ name: "ShelfMoveDialog" });
    expect(dialog.props("open")).toBe(true);
    expect(dialog.props("destinationFolderId")).toBe(2);
    expect(dialog.props("items")).toEqual([{ folder_id: 1, relpath: "a" }]);
  });
});

describe("dropping onto the capacity meter", () => {
  const GB = 1024 ** 3;
  const FOLDERS = [
    { id: 1, path: "/mnt/fast/loras", kind: "user", movable: "per_item" },
    { id: 2, path: "/mnt/slow/store", kind: "managed", movable: "root_only" },
  ];

  /**
   * One 100 GB adapter on the Fast drive, and an empty folder on Slow.
   *
   * `slowFree` is the whole point of the fixture: it is what decides whether
   * the drop fits, and every test here differs only in that number.
   */
  async function twoDrives({ slowFree, devices } = {}) {
    listModelFolders.mockResolvedValue(FOLDERS);
    listModelFolderDevices.mockResolvedValue(
      devices ?? [
        {
          device_id: "fast",
          mount_point: "/mnt/fast",
          label: "Fast",
          total_bytes: 4000 * GB,
          free_bytes: 2000 * GB,
          shelf_bytes: 500 * GB,
          folder_ids: [1],
        },
        {
          device_id: "slow",
          mount_point: "/mnt/slow",
          label: "Slow",
          total_bytes: 1000 * GB,
          free_bytes: slowFree,
          shelf_bytes: 100 * GB,
          folder_ids: [2],
        },
      ],
    );
    const wrapper = await mountShelf([
      adapter({
        id: 1,
        file_size: 100 * GB,
        locations: [
          {
            state: "present",
            folder_id: 1,
            folder_path: "/mnt/fast/loras",
            relpath: "a.st",
          },
        ],
      }),
    ]);
    const store = useModelShelfStore();
    store.setView({ groupBy: "folder", folderLayout: "drive" });
    store.toggleSelected(1);
    await wrapper.vm.$nextTick();
    return wrapper;
  }

  function bandFor(wrapper, label) {
    return wrapper
      .findAll(".shelf-band")
      .find((band) => band.text().includes(label));
  }

  /** Pick the row up, then hold the pointer over a band. */
  async function dragOnto(wrapper, label, type = "dragover") {
    const dt = transfer();
    await wrapper.find(".shelf-row").trigger("dragstart", { dataTransfer: dt });
    const event = Object.assign(new Event(type, { cancelable: true }), {
      dataTransfer: dt,
    });
    bandFor(wrapper, label).element.dispatchEvent(event);
    await wrapper.vm.$nextTick();
    return event;
  }

  it("projects the drop as a hatched segment cut out of the free space", async () => {
    // The consequence, drawn before the drop commits. Four segments now, still
    // summing to the drive: 100 GB on the shelf, 500 GB of other files, the
    // 100 GB ghost, and the 300 GB that would be left.
    const wrapper = await twoDrives({ slowFree: 400 * GB });
    const event = await dragOnto(wrapper, "Slow");
    expect(event.defaultPrevented).toBe(true);

    const band = bandFor(wrapper, "Slow");
    expect(band.classes()).toContain("shelf-band--drop");
    expect(
      band.findAll(".shelf-band-seg").map((s) => s.attributes("style")),
    ).toEqual(["width: 10%;", "width: 50%;", "width: 10%;", "width: 30%;"]);
    // Hatched rather than a fourth flat colour: a projection is provisional
    // and a measurement is not, and the two must not be one reading apart.
    expect(band.find(".shelf-band-seg--ghost").exists()).toBe(true);
    expect(band.find(".shelf-band-seg--ghost-reject").exists()).toBe(false);
    // The half that survives greyscale and is readable out loud.
    expect(textOf(band)).toContain("100.0 GB fits · 300.0 GB free after");

    // And only the band under the pointer: the other drive still reads as
    // measured, because nothing is being dropped on it.
    expect(bandFor(wrapper, "Fast").findAll(".shelf-band-seg")).toHaveLength(3);
  });

  it("refuses the drop while the pointer is still down, not after it", async () => {
    // The honest place to refuse a move. `preventDefault()` is what ACCEPTS a
    // drop, so not calling it leaves the browser's own "no drop here" cursor
    // over a band already drawn in the error treatment.
    const wrapper = await twoDrives({ slowFree: 40 * GB });
    const event = await dragOnto(wrapper, "Slow");
    expect(event.defaultPrevented).toBe(false);

    const band = bandFor(wrapper, "Slow");
    expect(band.classes()).toContain("shelf-band--reject");
    expect(band.classes()).not.toContain("shelf-band--drop");
    expect(band.find(".shelf-band-seg--ghost-reject").exists()).toBe(true);
    expect(textOf(band)).toContain("100.0 GB will not fit · 60.0 GB short");
  });

  it("does not open the dialog for a drop the band refused", async () => {
    const wrapper = await twoDrives({ slowFree: 40 * GB });
    await dragOnto(wrapper, "Slow", "drop");
    expect(
      wrapper.findComponent({ name: "ShelfMoveDialog" }).props("open"),
    ).toBe(false);
  });

  it("resolves a band drop to the first folder on that drive a move may go to", async () => {
    // A band is a disk and a move needs a folder, so one is chosen — safely,
    // because the drop does not move on release: the dialog states the
    // destination and its select corrects it.
    const wrapper = await twoDrives({ slowFree: 400 * GB });
    await dragOnto(wrapper, "Slow", "drop");

    const dialog = wrapper.findComponent({ name: "ShelfMoveDialog" });
    expect(dialog.props("open")).toBe(true);
    expect(dialog.props("destinationFolderId")).toBe(2);
    expect(dialog.props("items")).toEqual([{ folder_id: 1, relpath: "a.st" }]);
  });

  it("counts a move within one drive as nothing to copy", async () => {
    // Those are renames — the server reports `bytes_to_copy` of zero for them
    // — so projecting 100 GB onto the disk the bytes are already on would
    // refuse a move that costs nothing.
    const wrapper = await twoDrives({ slowFree: 400 * GB });
    await dragOnto(wrapper, "Fast");

    const band = bandFor(wrapper, "Fast");
    expect(band.classes()).toContain("shelf-band--drop");
    expect(textOf(band)).toContain("Already on this drive · nothing to copy");
  });

  it("refuses a folder header on a full drive too, not only its band", async () => {
    // The refusal belongs to the DISK. A folder whose drive has no room cannot
    // take the files either, so the header stops highlighting and the band
    // above it is where the reader is told why.
    const wrapper = await twoDrives({ slowFree: 40 * GB });
    const dt = transfer();
    await wrapper.find(".shelf-row").trigger("dragstart", { dataTransfer: dt });
    const event = Object.assign(new Event("dragover", { cancelable: true }), {
      dataTransfer: dt,
    });
    wrapper
      .findAll(".shelf-group-btn")
      .find((b) => b.text().includes("/mnt/slow/store"))
      .element.dispatchEvent(event);
    await wrapper.vm.$nextTick();

    expect(event.defaultPrevented).toBe(false);
    expect(wrapper.find(".shelf-group-btn--drop").exists()).toBe(false);
    expect(bandFor(wrapper, "Slow").classes()).toContain("shelf-band--reject");
  });

  it("does not refuse a drive it could not measure", async () => {
    // "We cannot say" must not be drawn as "does not fit". The band takes the
    // drop and highlights for it; it simply has no ghost and no outcome to
    // state, and the server still checks before it copies.
    const wrapper = await twoDrives({ devices: [] });
    const event = await dragOnto(wrapper, "/mnt/slow/store");
    expect(event.defaultPrevented).toBe(true);

    const band = bandFor(wrapper, "/mnt/slow/store");
    expect(band.classes()).toContain("shelf-band--drop");
    expect(band.find(".shelf-band-seg--ghost").exists()).toBe(false);
    expect(textOf(band)).toContain("Capacity unknown");
  });
});

describe("a run's disclosure", () => {
  function run() {
    return [
      adapter({ id: 1, stack_id: 7, stack_position: 0 }),
      adapter({
        id: 2,
        stack_id: 7,
        stack_position: 1,
        sha256: "b".repeat(64),
      }),
    ];
  }

  it("keeps the count a drawn figure and not a second control", async () => {
    // The defect the review of #881 found. A grid row would now TOLERATE a
    // control, unlike the listbox option this used to be — but the row is
    // still the disclosure, and a focusable second way to open the same run is
    // the dialect this list stopped speaking.
    const wrapper = await mountShelf(run());
    const row = wrapper.find(".shelf-row");
    expect(row.exists()).toBe(true);
    // Anything FOCUSABLE, not just the obvious tags: a `<span role="button"
    // tabindex="0">` is exactly as much of a second control, and a
    // tag-name-only assertion let that mutant through when this was checked.
    // `[tabindex]` is on the row itself, so the search is for descendants.
    // The count badge is the one control in a row, and it is a real button on
    // purpose: the grid role permits one where the old listbox did not, and a
    // pointer needs a target for the disclosure that Right/Left gives the
    // keyboard. Everything else in the row is still inert.
    const controls = row.findAll(
      'button, a[href], input, select, textarea, [tabindex], [role="button"]',
    );
    expect(controls).toHaveLength(1);
    expect(controls[0].classes()).toContain("shelf-stack-badge");
    // ...and it does not pick the row. The badge sits inside the one thing a
    // click on a row already does, so without `@click.stop` opening a run
    // would also select it — which is the gesture a reader uses to LOOK before
    // deciding whether to act.
    await controls[0].trigger("click");
    expect(useModelShelfStore().selectedRows).toHaveLength(0);
    expect(wrapper.findAll(".shelf-row--member")).toHaveLength(1);
  });

  it("opens and closes from the row with Right and Left", async () => {
    const wrapper = await mountShelf(run());
    const row = wrapper.find(".shelf-row");
    expect(wrapper.findAll(".shelf-row--member")).toHaveLength(0);

    await row.trigger("keydown", { key: "ArrowRight" });
    expect(wrapper.findAll(".shelf-row--member")).toHaveLength(1);

    await row.trigger("keydown", { key: "ArrowLeft" });
    expect(wrapper.findAll(".shelf-row--member")).toHaveLength(0);
  });

  it("says the count in a cell and the state in aria-expanded", async () => {
    // What the row's hand-built `aria-label` used to do. A grid cell can
    // simply hold the figure, so the count is drawn text a reader reaches
    // rather than a string assembled for it, and the open/closed half is the
    // attribute the treegrid role already defines for exactly this.
    const wrapper = await mountShelf(run());
    const row = wrapper.find(".shelf-row");
    expect(textOf(row.find(".shelf-stack-badge"))).toContain("2");
    expect(row.attributes("aria-expanded")).toBe("false");
    expect(row.attributes("aria-level")).toBe("1");

    await row.trigger("keydown", { key: "ArrowRight" });
    expect(row.attributes("aria-expanded")).toBe("true");
    // The run's other steps are child rows, and say so: the DOM draws them as
    // siblings, so the level is the only thing carrying the nesting.
    expect(wrapper.find(".shelf-row--member").attributes("aria-level")).toBe(
      "2",
    );
  });

  it("heads the columns once, and on every grid a reader may land in", async () => {
    // The point of #891: a `columnheader` heads the grid it is in and nothing
    // else, and grouping makes one grid per group — so every group carries the
    // row, and all but the first carry it visually-hidden so the eye sees one
    // header line rather than one per folder.
    const wrapper = await mountShelf([
      adapter({
        id: 1,
        locations: [
          { state: "present", folder_id: 1, folder_path: "/a", relpath: "x" },
        ],
      }),
      adapter({
        id: 2,
        sha256: "c".repeat(64),
        locations: [
          { state: "present", folder_id: 2, folder_path: "/b", relpath: "y" },
        ],
      }),
    ]);
    useModelShelfStore().setView({ groupBy: "folder", folderLayout: "alpha" });
    await wrapper.vm.$nextTick();

    // One strip per grid, and NONE of them drawn: a `columnheader` heads the
    // grid it is in and nothing else, so grouping needs one per group — and
    // the resolved design has no visible header strip, because the kind is a
    // chip, the base is a word and the size is right-aligned, which is what
    // makes the columns readable without being named. The names stay for the
    // reader who cannot see that.
    const heads = wrapper.findAll('[role="row"].visually-hidden');
    expect(heads).toHaveLength(2);
    expect(
      heads[0].findAll('[role="columnheader"]').map((cell) => cell.text()),
    ).toEqual(["Model", "Name", "Kind", "Base", "Size"]);
  });
});

describe("the assignment ring (#892, redrawn for #904)", () => {
  it("names every attached entity, and never by colour alone", async () => {
    listCharacters.mockResolvedValue([
      { id: 7, name: "Ada", character_color: "#e91e63" },
    ]);
    listPictureSets.mockResolvedValue([
      { id: 3, name: "Beach", set_color: "#3f51b5" },
    ]);
    const wrapper = await mountShelf([
      adapter({
        attachments: [
          { entity_type: "character", entity_id: 7 },
          { entity_type: "set", entity_id: 3 },
        ],
      }),
    ]);
    const mark = wrapper.find(".mmark");
    // The greyscale test, as arithmetic: strip the hue and the ring still
    // carries a STYLE, and the mark still says who out loud. Both attachments
    // are named even though one ring is drawn — the mark has one edge.
    expect(mark.attributes("title")).toBe("Ada (person), Beach (set)");
    expect(textOf(mark)).toContain("Ada (person), Beach (set)");
    expect(mark.classes()).toContain("mmark--ring");
    expect(
      mark
        .classes()
        .some((c) => /^mmark--(solid|dashed|thick|double)$/.test(c)),
    ).toBe(true);
    // The hue is the entity's own, so a character wears one colour app-wide.
    expect(mark.attributes("style")).toContain("#e91e63");
  });

  it("holds one treatment per entity, across every row that wears it", async () => {
    // The style is the identity carrier, so it is a fact about the character
    // rather than about the row: two adapters assigned to one person must draw
    // the same ring or the treatment says nothing at all.
    listCharacters.mockResolvedValue([{ id: 7, name: "Ada" }]);
    const attachments = [{ entity_type: "character", entity_id: 7 }];
    const wrapper = await mountShelf([
      adapter({ id: 1, attachments }),
      adapter({ id: 2, sha256: "b".repeat(64), attachments }),
    ]);
    const styles = wrapper
      .findAll(".mmark")
      .map((m) =>
        m.classes().find((c) => c.startsWith("mmark--") && c !== "mmark--ring"),
      );
    expect(styles[0]).toBe(styles[1]);
  });

  it("sets no hue property at all on an unassigned mark", async () => {
    // Not an EMPTY one. `var(--mmark-ring, transparent)` takes its fallback
    // only when the property is unset; set-but-empty resolves to nothing, which
    // is invalid at computed-value time and drops the whole `border` shorthand
    // — the 2px width with it, leaving the dashed ring a different size from
    // every other ring on the shelf.
    const wrapper = await mountShelf([adapter({ attachments: [] })]);
    expect(wrapper.find(".mmark").attributes("style")).toBeUndefined();
  });

  it("draws the dashed grey ring for a model assigned to nothing", async () => {
    const wrapper = await mountShelf([adapter({ attachments: [] })]);
    const mark = wrapper.find(".mmark");
    // A state, not a gap: an unringed mark under a design where every other
    // mark has one reads as a rendering fault rather than as "assigned to
    // nothing", and the word is what a reader hears.
    expect(mark.classes()).toContain("mmark--none");
    expect(mark.attributes("title")).toBe("Unassigned");
  });

  it("still rings an attachment whose entity the lists do not answer", async () => {
    // The vault is the authority on what is attached. Dropping the ring would
    // say "not assigned", which is a different and wrong fact.
    const wrapper = await mountShelf([
      adapter({ attachments: [{ entity_type: "character", entity_id: 42 }] }),
    ]);
    const mark = wrapper.find(".mmark");
    expect(mark.classes()).not.toContain("mmark--none");
    expect(mark.attributes("title")).toBe("#42 (person)");
  });

  it("borrows the assigned face when the model has no picture of its own", async () => {
    // A LoRA of Sarah with no icon is far better identified by Sarah's
    // reference face than by the letters the generator would draw, and the ring
    // around it is already her colour — so the two halves say one thing.
    listCharacters.mockResolvedValue([{ id: 7, name: "Ada" }]);
    const wrapper = await mountShelf([
      adapter({ attachments: [{ entity_type: "character", entity_id: 7 }] }),
    ]);
    const img = wrapper.find(".mmark-img");
    expect(img.exists()).toBe(true);
    expect(img.attributes("src")).toContain("/characters/7");
  });

  it("keeps the model's own icon ahead of the face it is assigned to", async () => {
    // Somebody chose that picture for this file. The assignment is still drawn
    // — it is the ring — so nothing is lost by the icon winning the middle.
    listCharacters.mockResolvedValue([{ id: 7, name: "Ada" }]);
    const wrapper = await mountShelf([
      adapter({
        icon_sha256: "f".repeat(64),
        attachments: [{ entity_type: "character", entity_id: 7 }],
      }),
    ]);
    expect(wrapper.find(".mmark-img").attributes("src")).not.toContain(
      "/characters/7",
    );
  });
});

describe("the icon verb", () => {
  it("offers Set icon for one model and refuses it for two", async () => {
    const wrapper = await mountShelf([
      adapter({ id: 1 }),
      adapter({ id: 2, sha256: "b".repeat(64) }),
    ]);
    const store = useModelShelfStore();
    store.toggleSelected(1);
    await wrapper.vm.$nextTick();
    const setIcon = () => wrapper.find('[data-verb="set-icon"]');
    expect(setIcon().attributes("disabled")).toBeUndefined();

    store.toggleSelected(2);
    await wrapper.vm.$nextTick();
    expect(setIcon().attributes("disabled")).toBeDefined();
  });

  it("offers Clear icon only when something has one", async () => {
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    const store = useModelShelfStore();
    store.toggleSelected(1);
    await wrapper.vm.$nextTick();
    const clear = () =>
      wrapper.findAll("button").find((b) => b.text().includes("Clear icon"));
    expect(clear()).toBeUndefined();

    store.rows = [adapter({ id: 1, icon_sha256: "a".repeat(64) })];
    await wrapper.vm.$nextTick();
    expect(clear()).toBeDefined();
  });

  it("accepts only the image types the store will take", async () => {
    // The server checks magic bytes, but the picker should not offer a file it
    // is going to refuse.
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    const input = wrapper.find('input[type="file"]');
    expect(input.attributes("accept")).toBe("image/png,image/jpeg,image/webp");
  });
});

describe("the manual stack verb", () => {
  it("sends the selected models, and only after the prompt is answered", async () => {
    // Two present adapters in one folder are the case the route accepts; the
    // gate itself is asserted in the bar's own suite. What matters here is that
    // nothing is written until the confirmation comes back, because there is no
    // way to unstack a run afterwards.
    const inFolder = (id, sha) =>
      adapter({
        id,
        sha256: sha.repeat(64),
        locations: [
          {
            state: "present",
            folder_id: 1,
            folder_path: "/m",
            relpath: `${id}`,
          },
        ],
      });
    const wrapper = await mountShelf([inFolder(1, "a"), inFolder(2, "b")]);
    const store = useModelShelfStore();
    store.toggleSelected(1);
    store.toggleSelected(2);
    await wrapper.vm.$nextTick();
    const stack = () =>
      wrapper.findAll("button").find((b) => b.text().includes("Stack these"));

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    await stack().trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(createStack).not.toHaveBeenCalled();

    confirmSpy.mockReturnValue(true);
    createStack.mockResolvedValue({ stack_id: 3, member_count: 2 });
    await stack().trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(createStack).toHaveBeenCalledWith([1, 2]);
    confirmSpy.mockRestore();
  });
});

describe("the model-folders door", () => {
  // Deleted once already, in the toolbar consolidation for #904, and nothing in
  // this suite noticed: the shelf kept rendering the dialog with no control left
  // to open it. Asserted on a POPULATED shelf, because the empty state's own
  // button unmounts exactly when the registry starts needing edits.
  it("opens the folders dialog from the toolbar", async () => {
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    const folders = wrapper.find(
      '.shelf-toolbar button[aria-label="Model folders"]',
    );
    expect(folders.exists()).toBe(true);
    expect(
      wrapper.findComponent({ name: "ModelFoldersDialog" }).props("open"),
    ).toBe(false);

    await folders.trigger("click");
    expect(
      wrapper.findComponent({ name: "ModelFoldersDialog" }).props("open"),
    ).toBe(true);
  });

  // Focus return needs a real document, because `activeElement` is the whole
  // assertion and a detached wrapper has none. Both doors are asserted: the
  // toolbar button rides the fallback, and the Add item names the Add button
  // because the item itself unmounts with the menu. Deleting either half of
  // the plumbing — the `folderInvoker` write or the `.focus()` — fails this.
  async function closeFoldersFrom(wrapper, opener) {
    await opener.trigger("click");
    wrapper.findComponent({ name: "ModelFoldersDialog" }).vm.$emit("close");
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();
  }

  it("hands focus back to whichever door opened it", async () => {
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    document.body.appendChild(wrapper.element);

    const folders = wrapper.find(
      '.shelf-toolbar button[aria-label="Model folders"]',
    );
    await closeFoldersFrom(wrapper, folders);
    expect(document.activeElement).toBe(folders.element);

    const add = wrapper.find(
      '.shelf-toolbar button[title="Add models to the shelf"]',
    );
    const addItem = wrapper
      .findAll(".shelf-mi")
      .find((b) => b.text().includes("Add folder"));
    await closeFoldersFrom(wrapper, addItem);
    expect(document.activeElement).toBe(add.element);

    wrapper.unmount();
  });

  it("returns to the empty-state button, until the first scan unmounts it", async () => {
    // The empty state is the one door that can disappear underneath its own
    // dialog. While it is still there it gets focus back like any other — a
    // reader who opened it to look and closed without adding must not be
    // thrown to a toolbar icon they never pressed. Once a scan has found
    // something the button is gone, and THAT is what the `isConnected`
    // fallback is for.
    const wrapper = await mountShelf([]);
    document.body.appendChild(wrapper.element);
    const store = useModelShelfStore();

    const emptyBtn = wrapper
      .findAll(".shelf-state button")
      .find((b) => b.text().includes("Add a model folder"));
    await closeFoldersFrom(wrapper, emptyBtn);
    expect(document.activeElement).toBe(emptyBtn.element);

    // Now the scan lands while the dialog is open: the empty state unmounts.
    await emptyBtn.trigger("click");
    store.rows = [adapter({ id: 1 })];
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".shelf-state").exists()).toBe(false);
    wrapper.findComponent({ name: "ModelFoldersDialog" }).vm.$emit("close");
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();
    expect(document.activeElement).toBe(
      wrapper.find('.shelf-toolbar button[aria-label="Model folders"]').element,
    );

    wrapper.unmount();
  });
});

describe("Escape", () => {
  // The key is handled on the WINDOW, so every assertion here needs a real
  // event path out of the shelf — a detached wrapper's keydown bubbles into
  // nothing and would pass or fail for the wrong reason.
  async function mountAttachedShelf() {
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    document.body.appendChild(wrapper.element);
    return wrapper;
  }

  it("clears the selection from anywhere in the shelf, not only from a row", async () => {
    // It used to be handled on the row, so it only worked while a row held the
    // roving tab stop — not after a click moved focus, and not from the
    // toolbar. "Escape clears the selection" has to mean everywhere.
    const wrapper = await mountAttachedShelf();
    const store = useModelShelfStore();
    store.toggleSelected(1);
    await wrapper.vm.$nextTick();
    expect(store.selectedRows).toHaveLength(1);

    await wrapper.find(".shelf-toolbar").trigger("keydown", { key: "Escape" });
    expect(store.selectedRows).toHaveLength(0);
    wrapper.unmount();
  });

  it("clears the selection when focus has left the shelf entirely", async () => {
    // The listener used to sit on the shelf's own root, so the key only
    // reached it while focus was inside that subtree. Click the sidebar, the
    // app bar, or anything else after picking rows and Escape did nothing —
    // which is how "Escape clears the selection" reads as broken.
    const wrapper = await mountAttachedShelf();
    const store = useModelShelfStore();
    store.toggleSelected(1);
    await wrapper.vm.$nextTick();

    document.body.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );
    expect(store.selectedRows).toHaveLength(0);
    wrapper.unmount();
  });

  it("stops listening once the shelf is gone", async () => {
    // The view is v-else-if'd away when another one opens, and a window
    // listener outlives its element unless it is taken down.
    const wrapper = await mountAttachedShelf();
    const store = useModelShelfStore();
    store.toggleSelected(1);
    await wrapper.vm.$nextTick();
    wrapper.unmount();

    document.body.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );
    expect(store.selectedRows).toHaveLength(1);
  });

  it("leaves the key to whoever is being typed in", async () => {
    // The bare input stands in for the app's search field, whose own Escape
    // clears the search. Clearing the shelf's selection underneath at the same
    // time is the same unasked-for second effect a dialog gets protected from.
    const wrapper = await mountAttachedShelf();
    const store = useModelShelfStore();
    store.toggleSelected(1);
    await wrapper.vm.$nextTick();

    const input = document.createElement("input");
    document.body.appendChild(input);
    input.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );
    expect(store.selectedRows).toHaveLength(1);
    input.remove();
    wrapper.unmount();
  });

  it("leaves the selection alone when a dialog owns the key", async () => {
    // Escape inside a dialog means "close me". Clearing the selection
    // underneath at the same time is a second, unasked-for effect.
    const wrapper = await mountAttachedShelf();
    const store = useModelShelfStore();
    store.toggleSelected(1);
    await wrapper.find(".shelf-toolbar").trigger("click");
    wrapper.vm.editVerb = "rename";
    await wrapper.vm.$nextTick();

    await wrapper.find(".shelf-toolbar").trigger("keydown", { key: "Escape" });
    expect(store.selectedRows).toHaveLength(1);

    // Every dialog the shelf owns, not only the edit one: a press with nothing
    // focused targets `<body>`, so the ref is the only thing that can see them.
    wrapper.vm.editVerb = "";
    wrapper.vm.foldersOpen = true;
    await wrapper.vm.$nextTick();
    document.body.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );
    expect(store.selectedRows).toHaveLength(1);
    wrapper.unmount();
  });

  it("leaves the key to an open menu, whose activator still holds the focus", async () => {
    // Vuetify only pulls focus into a menu's content on a later `focusin`, so a
    // menu opened with the MOUSE leaves focus on its activator inside the
    // shelf. Read off the target alone, the shelf's own Sort, Show and verb
    // menus would close and drop the selection in one press.
    const wrapper = await mountAttachedShelf();
    const store = useModelShelfStore();
    store.toggleSelected(1);
    await wrapper.vm.$nextTick();

    const overlay = document.createElement("div");
    overlay.className = "v-overlay v-overlay--active";
    document.body.appendChild(overlay);
    await wrapper.find(".shelf-toolbar").trigger("keydown", { key: "Escape" });
    expect(store.selectedRows).toHaveLength(1);

    // A tooltip is not an owner: hovering one elsewhere must not swallow the key.
    overlay.className = "v-overlay v-overlay--active v-tooltip";
    await wrapper.find(".shelf-toolbar").trigger("keydown", { key: "Escape" });
    expect(store.selectedRows).toHaveLength(0);
    overlay.remove();
    wrapper.unmount();
  });

  it("leaves the key to a full-screen surface over the shelf", async () => {
    // The review overlay renders outside `App.vue`'s view switch, so the shelf
    // is still mounted underneath it — and a selection nobody can see must not
    // be cleared by the press that dismisses what is covering it.
    const wrapper = await mountAttachedShelf();
    const store = useModelShelfStore();
    const reviews = useReviewSessionsStore();
    store.toggleSelected(1);
    reviews.overlayOpen = true;
    await wrapper.vm.$nextTick();

    document.body.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );
    expect(store.selectedRows).toHaveLength(1);
    reviews.overlayOpen = false;
    wrapper.unmount();
  });

  it("leaves the key to the auto-hide sidebar it is dismissing", async () => {
    // `useGlobalKeydown` hides the revealed sidebar on Escape and deliberately
    // does not stop the event, so one press would otherwise hide the sidebar
    // and wipe the selection behind it.
    const wrapper = await mountAttachedShelf();
    const store = useModelShelfStore();
    const sidebar = useSidebarStore();
    store.toggleSelected(1);
    // Both flags are computed: unpinning makes it an overlay and reveals it.
    sidebar.setSidebarPinned(false);
    sidebar.revealSidebar();
    await wrapper.vm.$nextTick();

    document.body.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );
    expect(store.selectedRows).toHaveLength(1);
    wrapper.unmount();
  });
});

describe("the training step", () => {
  it("shows the step on a row whose name had it stripped", async () => {
    // `deriveModelName` drops the trailing step on the stated grounds that it
    // "is parsed into its own field" — and nothing rendered that field outside
    // an expanded stack, so two checkpoints of one run read identically.
    const wrapper = await mountShelf([
      adapter({
        id: 601,
        display_name: null,
        filename: "clementine-zib-3b_000002500.safetensors",
        training_step: 2500,
      }),
    ]);
    // `cleanAssetName` turns separators into spaces, so the derived name is
    // spaced — the point is that the step is no longer lost from it.
    expect(wrapper.get(".shelf-row-name").text()).toContain(
      "clementine zib 3b",
    );
    // `toLocaleString()` rather than a written-out "2,500": the separator
    // follows the runtime locale, so hard-coding it fails on a machine that
    // groups differently while the component is behaving correctly. Scoped to
    // the element for the same reason the negatives below are — "Step" also
    // appears in the shelf's own help paragraph.
    expect(wrapper.get(".shelf-chip--step").text()).toBe(
      `Step ${(2500).toLocaleString()}`,
    );
  });

  it("says nothing when the file records no step", async () => {
    // A hand-made adapter has no step, and "Step —" would invent one.
    const wrapper = await mountShelf([
      adapter({ id: 602, display_name: "Portrait mix", training_step: null }),
    ]);
    // The element, not the page text: "Step" appears in the shelf's own help
    // paragraph, so a substring check over everything would pass or fail for
    // reasons that have nothing to do with this row.
    expect(wrapper.find(".shelf-chip--step").exists()).toBe(false);
  });

  it("never puts a single step on a stack cover", async () => {
    // A cover stands for every step in the run, so naming one would be false.
    // The member count is what belongs there.
    // Two rows sharing a stack fold into one cover; a lone row with a
    // `stack_id` is not a cover and correctly keeps its own step.
    const wrapper = await mountShelf([
      adapter({
        id: 603,
        display_name: null,
        filename: "clementine-zib-3b_000002500.safetensors",
        training_step: 2500,
        stack_id: 7,
      }),
      adapter({
        id: 604,
        display_name: null,
        filename: "clementine-zib-3b_000005000.safetensors",
        training_step: 5000,
        stack_id: 7,
      }),
    ]);
    expect(wrapper.find(".shelf-chip--step").exists()).toBe(false);
  });
});

describe("Add file", () => {
  // F6's remainder: the way one adapter that belongs to no training run gets
  // onto the shelf without a folder being registered for it.

  async function pick(wrapper, path) {
    wrapper.findComponent({ name: "FolderBrowser" }).vm.$emit("select", path);
    await new Promise((resolve) => setTimeout(resolve, 0));
    await wrapper.vm.$nextTick();
  }

  it("offers the verb under Add, with a name a reader can hear", async () => {
    // Not a button of its own in the bar any more: adding a folder, adding a
    // loose file and importing a run are three ways into one thing, so they
    // share the one accented control (#904).
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    const item = wrapper
      .findAll(".shelf-mi")
      .find((entry) => entry.text().includes("Add file"));
    expect(item).toBeDefined();
  });

  it("sends the picked path and refreshes the shelf, so no rescan is needed", async () => {
    // The done-when of #901: the row is there when the call returns. The shelf
    // is refetched rather than the response being spliced in, because the
    // server decides what the row says (name, kind, base model, size).
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    addModelFile.mockResolvedValue({
      model_id: 7,
      filename: "loose.safetensors",
      folder_id: 2,
      folder_path: "/store",
    });
    const before = listAdapters.mock.calls.length;

    await pick(wrapper, "/home/u/Downloads/loose.safetensors");

    expect(addModelFile).toHaveBeenCalledWith(
      "/home/u/Downloads/loose.safetensors",
    );
    expect(listAdapters.mock.calls.length).toBeGreaterThan(before);
    const notices = useNoticeStore();
    expect(notices.notices[0].level).toBe("success");
    // The one thing a reader might fear about a verb that copies: it says the
    // original is untouched, because nothing else in the UI would say so.
    expect(notices.notices[0].text).toContain("still where it was");
  });

  it("reports a refusal rather than leaving it looking like it landed", async () => {
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    addModelFile.mockRejectedValue({
      response: { data: { detail: "That file is already inside /m." } },
    });

    await pick(wrapper, "/m/known.safetensors");

    const notices = useNoticeStore();
    expect(notices.notices[0].level).toBe("error");
    expect(notices.notices[0].text).toContain("already inside");
  });
});

describe("the two kinds of absence", () => {
  // The whole of #898: a registered file that is GONE and a drive that is
  // simply not plugged in are different facts, and rendering them the same way
  // trains the reader to ignore both. The offline case is the common one for
  // anyone keeping adapters on an external disk.

  const at = (folderId, state) => ({
    folder_id: folderId,
    folder_path: `/mnt/${folderId}`,
    relpath: "a.safetensors",
    state,
  });

  it("gives broken and offline rows different classes, not one 'absent' one", async () => {
    const wrapper = await mountShelf([
      adapter({ id: 1, locations: [at(7, "missing")] }),
      adapter({ id: 2, locations: [at(8, "unreachable")] }),
      adapter({ id: 3, locations: [at(9, "present")] }),
    ]);
    const rows = wrapper.findAll(".shelf-row");
    expect(rows[0].classes()).toContain("shelf-row--broken");
    expect(rows[0].classes()).not.toContain("shelf-row--offline");
    expect(rows[1].classes()).toContain("shelf-row--offline");
    expect(rows[1].classes()).not.toContain("shelf-row--broken");
    expect(rows[2].classes()).not.toContain("shelf-row--broken");
    expect(rows[2].classes()).not.toContain("shelf-row--offline");
  });

  it("keeps the error colour off the offline row", async () => {
    // The status glyph is the one place a hue is spent on this column, and
    // `unreachable` must not take it: nothing is wrong and nothing is lost.
    const wrapper = await mountShelf([
      adapter({ id: 1, locations: [at(7, "missing")] }),
      adapter({ id: 2, locations: [at(8, "unreachable")] }),
    ]);
    const marks = wrapper.findAll(".shelf-row-loc");
    expect(marks[0].classes()).toContain("shelf-row-loc--missing");
    expect(marks[1].classes()).toContain("shelf-row-loc--unreachable");
  });

  it("states an offline mount's scope once, with its row count", async () => {
    const wrapper = await mountShelf([
      adapter({ id: 1, locations: [at(7, "unreachable")] }),
      adapter({ id: 2, locations: [at(7, "unreachable")] }),
    ]);
    const banner = wrapper.find(".shelf-banner");
    expect(banner.exists()).toBe(true);
    expect(textOf(banner)).toContain("/mnt/7 is offline — 2 models");
    // Once, not once per row.
    expect(wrapper.findAll(".shelf-banner")).toHaveLength(1);
  });

  it("says nothing about a folder that was readable", async () => {
    // `missing` means the folder WAS read. Calling that mount offline would be
    // the same conflation the row treatment exists to undo.
    const wrapper = await mountShelf([
      adapter({ id: 1, locations: [at(7, "missing")] }),
    ]);
    expect(wrapper.find(".shelf-banner").exists()).toBe(false);
  });
});

describe("what a scan just added", () => {
  it("badges only the rows the fetch brought in, and clears them next time", async () => {
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    const store = useModelShelfStore();
    expect(wrapper.find(".shelf-row-new").exists()).toBe(false);

    listAdapters.mockResolvedValue([adapter({ id: 1 }), adapter({ id: 2 })]);
    await store.fetchRows({ markNew: true });
    await wrapper.vm.$nextTick();
    const badged = wrapper
      .findAll(".shelf-row")
      .filter((row) => row.find(".shelf-row-new").exists());
    expect(badged).toHaveLength(1);
    expect(textOf(badged[0])).toContain("New");

    // An ordinary refresh is not an arrival, so the mark does not survive one.
    await store.fetchRows();
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".shelf-row-new").exists()).toBe(false);
  });
});

describe("a long move, in the panel that is running it", () => {
  it("dims and inerts the list without touching the toolbar", async () => {
    // #900: the panel dims, not the app. The veil is INSIDE `.shelf-body`, so
    // Show and Sort still answer while files are in flight, and `inert` is
    // what actually stops the rows — a veil that only looks disabled leaves
    // every one of them clickable and in the tab order.
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    const moves = useModelMovesStore();
    expect(wrapper.find(".shelf-dim").exists()).toBe(false);

    moves.job = { status: "running", total: 4, done: 1, results: [] };
    await wrapper.vm.$nextTick();
    const body = wrapper.find(".shelf-body");
    expect(body.find(".shelf-dim").exists()).toBe(true);
    expect(body.attributes("inert")).toBeDefined();
    expect(wrapper.find(".shelf-toolbar").attributes("inert")).toBeUndefined();
  });

  it("anchors the bar in the shelf and leaves the failure in its place", async () => {
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    const moves = useModelMovesStore();
    const card = () => wrapper.find(".shelf-progress progress-overlay-stub");
    expect(card().attributes("visible")).toBe("false");

    moves.job = { status: "running", total: 4, done: 1, results: [] };
    await wrapper.vm.$nextTick();
    expect(card().attributes("visible")).toBe("true");
    expect(card().attributes("status")).toBe("running");
    expect(card().attributes("abortlabel")).toBe("Stop");

    // The run ends badly: the card stays where the progress was rather than
    // handing the news to a notice that clears itself, and the bar fills.
    moves.job = { status: "finished", total: 4, done: 4, results: [] };
    moves.failure = "Moved 3 files. 1 file could not be moved and stayed put.";
    await wrapper.vm.$nextTick();
    expect(card().attributes("visible")).toBe("true");
    expect(card().attributes("status")).toBe("failed");
    expect(card().attributes("percent")).toBe("100");
    expect(card().attributes("message")).toContain("could not be moved");
    expect(card().attributes("abortlabel")).toBe("Dismiss");
  });
});

// #938-follow-up: the shelf is a destination like Duplicates, so it carries the
// app-wide tail — Settings and the stats toggle — rather than dropping both the
// moment the grid unmounts. Undo is the exception: nothing on this screen writes
// to the operation log, so there is nothing here for it to take back.
describe("the app-wide toolbar tail", () => {
  it("asks App.vue for Settings and toggles the stats sidebar itself", async () => {
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    const sidebar = useSidebarStore();
    const settings = wrapper.find(".shelf-toolbar button[title='Settings']");

    await settings.trigger("click");
    expect(wrapper.emitted("open-settings")).toHaveLength(1);

    // Directional, not a flip: the rail opens on the first press and closes on
    // the second, from the shut state a fresh session starts in.
    sidebar.statsOpen = false;
    await wrapper.find(".shelf-toolbar .tb-stats-btn").trigger("click");
    expect(sidebar.statsOpen).toBe(true);
    await wrapper.find(".shelf-toolbar .tb-stats-btn").trigger("click");
    expect(sidebar.statsOpen).toBe(false);
  });

  it("orders the tail separator → TbGlobalActions, last in the bar", async () => {
    // The documented tail minus undo
    // (docs/design/toolbar-responsive-decisions.md). Asserted as PLACEMENT and
    // not merely as presence: the whole point of the rule is that the app-wide
    // chrome sits after the view controls, ruled off from them, at the end.
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    const cluster = wrapper.find(".shelf-bar-cluster").element;
    // TbGlobalActions is multi-root; its Settings button is a stable anchor.
    const settings = wrapper.find("button[title='Settings']").element;
    const separator = wrapper.find(".shelf-bar-cluster .bar-separator").element;
    // The Show menu — the LAST of this view's own controls, and the one the
    // tail has to come after. (`.bar-btn--boxed` alone would find the stack
    // sweep, which sits outside the cluster and proves nothing.)
    const showBtn = wrapper
      .findAll(".shelf-bar-cluster .bar-btn--boxed")
      .at(-1).element;
    const follows = (a, b) =>
      Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

    expect(follows(showBtn, separator)).toBe(true);
    // Adjacency, not merely order: the rule marks the boundary, so nothing may
    // slip between it and the chrome it rules off.
    expect(separator.nextElementSibling).toBe(settings);
    // Nothing of this view's own follows the app-wide chrome. TbGlobalActions
    // is multi-root, so its stats button IS the bar's last element.
    expect(cluster.lastElementChild.classList.contains("tb-stats-btn")).toBe(
      true,
    );
  });

  // The shelf writes nothing to the operation log, so undo/redo and the
  // History popover are not offered here at all.
  it("mounts no undo control", async () => {
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    expect(wrapper.findComponent({ name: "UndoControl" }).exists()).toBe(false);
  });
});

describe("Delete", () => {
  // The file-manager gesture, spelled the way Explorer spells it: Del trashes,
  // Shift+Del deletes permanently. Handled on the WINDOW beside Escape, so
  // every assertion needs a real event path out of the shelf.
  const FOLDER = { id: 1, path: "/m", kind: "user", movable: "per_item" };

  const inFolder = (id) =>
    adapter({
      id,
      locations: [
        { state: "present", folder_id: 1, folder_path: "/m", relpath: `${id}` },
      ],
    });

  async function mountWithSelection() {
    listModelFolders.mockResolvedValue([FOLDER]);
    const wrapper = await mountShelf([inFolder(1)]);
    document.body.appendChild(wrapper.element);
    const store = useModelShelfStore();
    store.toggleSelected(1);
    await wrapper.vm.$nextTick();
    return wrapper;
  }

  function pressDelete(extra = {}) {
    document.body.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Delete", bubbles: true, ...extra }),
    );
    return new Promise((resolve) => setTimeout(resolve, 0));
  }

  beforeEach(() => {
    deleteModels.mockReset();
    deleteModels.mockResolvedValue({
      deleted: [1],
      files_removed: 1,
      permanent: false,
      refused: [],
    });
  });

  // The `window.confirm` spies below are restored here rather than at the end
  // of each test: a failed assertion never reaches its own `mockRestore`, and a
  // leaked spy turns one red test into four.
  afterEach(() => vi.restoreAllMocks());

  it("asks before it deletes, and deletes nothing when the answer is no", async () => {
    // The key opens a prompt, never a deletion: a stray Del with forty rows
    // selected has to cost one Escape and nothing else.
    const wrapper = await mountWithSelection();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

    await pressDelete();
    expect(confirmSpy).toHaveBeenCalled();
    expect(confirmSpy.mock.calls[0][0]).toContain("Trash");
    expect(deleteModels).not.toHaveBeenCalled();

    confirmSpy.mockReturnValue(true);
    await pressDelete();
    expect(deleteModels).toHaveBeenCalledWith([1], { permanent: false });
    confirmSpy.mockRestore();
    wrapper.unmount();
  });

  it("makes Shift+Delete a permanent one, and says so in the prompt", async () => {
    const wrapper = await mountWithSelection();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    await pressDelete({ shiftKey: true });
    expect(confirmSpy.mock.calls[0][0]).toContain("permanently");
    expect(deleteModels).toHaveBeenCalledWith([1], { permanent: true });
    confirmSpy.mockRestore();
    wrapper.unmount();
  });

  it("leaves the key to whoever is being typed in", async () => {
    // A Del in a search field is a character, not a file deletion. This is the
    // guard Escape already had, and it matters far more here.
    const wrapper = await mountWithSelection();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const field = document.createElement("input");
    document.body.appendChild(field);

    field.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Delete", bubbles: true }),
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(deleteModels).not.toHaveBeenCalled();

    field.remove();
    confirmSpy.mockRestore();
    wrapper.unmount();
  });

  it("does nothing for a selection it could only get refused for", async () => {
    // The gate is the server's, and this is the same one drawn client-side so
    // the prompt is never shown for files PixlStash will not unlink.
    listModelFolders.mockResolvedValue([
      { id: 1, path: "/hf", kind: "foreign", movable: "fixed" },
    ]);
    const wrapper = await mountShelf([inFolder(1)]);
    document.body.appendChild(wrapper.element);
    useModelShelfStore().toggleSelected(1);
    await wrapper.vm.$nextTick();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    await pressDelete();
    expect(confirmSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
    wrapper.unmount();
  });

  it("counts a run's members, and deletes exactly what it counted", async () => {
    // The wrong-count-in-a-destructive-prompt case. A stack is ONE row standing
    // for its whole run, and the call sends every member — so a prompt counting
    // rows would offer "Move this model to the Trash?" over six checkpoints and
    // tens of gigabytes.
    listModelFolders.mockResolvedValue([FOLDER]);
    const wrapper = await mountShelf([
      adapter({
        id: 1,
        stack_id: 7,
        stack_position: 0,
        locations: [
          { state: "present", folder_id: 1, folder_path: "/m", relpath: "1" },
        ],
      }),
      adapter({
        id: 2,
        stack_id: 7,
        stack_position: 1,
        sha256: "b".repeat(64),
        locations: [
          { state: "present", folder_id: 1, folder_path: "/m", relpath: "2" },
        ],
      }),
    ]);
    document.body.appendChild(wrapper.element);
    useModelShelfStore().toggleSelected(1);
    await wrapper.vm.$nextTick();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    deleteModels.mockResolvedValue({
      deleted: [1, 2],
      files_removed: 2,
      permanent: false,
      refused: [],
    });

    await pressDelete();
    expect(confirmSpy.mock.calls[0][0]).toContain("2 models");
    expect(deleteModels).toHaveBeenCalledWith([1, 2], { permanent: false });
    wrapper.unmount();
  });

  it("says why rather than doing nothing when the key finds nothing", async () => {
    // The pill's button is disabled there; `Delete` has no disabled state, so
    // without this a valid-looking selection answers a keypress with silence.
    listModelFolders.mockResolvedValue([
      { id: 1, path: "/hf", kind: "foreign", movable: "fixed" },
    ]);
    const wrapper = await mountShelf([inFolder(1)]);
    document.body.appendChild(wrapper.element);
    useModelShelfStore().toggleSelected(1);
    await wrapper.vm.$nextTick();

    await pressDelete();
    expect(useNoticeStore().notices.at(-1).text).toContain("own model folders");
    expect(deleteModels).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("stops listening once the shelf is gone", async () => {
    // The view is v-else-if'd away when another one opens, and a window
    // listener that outlived it would delete files from another screen.
    const wrapper = await mountWithSelection();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    wrapper.unmount();

    await pressDelete();
    expect(confirmSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });
});
