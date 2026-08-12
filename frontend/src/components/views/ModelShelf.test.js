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
// The engines block is the same route with `file_kind=engine`, and a different
// result set. Its own double, or every `listAdapters.mockResolvedValue` here
// would answer it with adapter rows and the shelf would render each one twice.
const listEngines = vi.fn();

vi.mock("../../api/modelShelf", () => ({
  BASE_MODEL_UNASSIGNED: "UNASSIGNED",
  listAdapters: (...args) =>
    args[0]?.fileKind === "engine"
      ? listEngines(...args)
      : listAdapters(...args),
  listCheckpoints: (...args) => listCheckpoints(...args),
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

vi.mock("../../api/modelFolders", async (importOriginal) => ({
  ...(await importOriginal()),
  listModelFolderDevices: (...args) => listModelFolderDevices(...args),
  listModelFolders: (...args) => listModelFolders(...args),
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
      ShelfSortPanel: true,
      // Their own suites mount them. Here they would only drag Vuetify's dialog
      // and tooltip providers into a suite that installs neither. The selection
      // BAR is deliberately left real: what a selection does to this view is
      // this suite's business.
      ModelFoldersDialog: true,
      ShelfEditDialog: true,
      ShelfMoveDialog: true,
      ModelImportDialog: true,
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

async function mountShelf(rows, checkpoints = []) {
  listAdapters.mockResolvedValue(rows);
  listCheckpoints.mockResolvedValue(checkpoints);
  listEngines.mockResolvedValue([]);
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
  listEngines.mockReset().mockResolvedValue([]);
  listModelFolderDevices.mockReset();
  listModelFolderDevices.mockResolvedValue([]);
  listModelFolders.mockReset();
  listModelFolders.mockResolvedValue([]);
  // `idle` is what a machine with no move in flight reports, and `adopt()`
  // adopts nothing from it — so the mount path stays silent instead of warning
  // from an unawaited promise.
  getModelMoveStatus.mockReset();
  getModelMoveStatus.mockResolvedValue({ status: "idle", results: [] });
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

  it("is a multi-select listbox, and says which rows are selected", async () => {
    // The role is what tells a screen reader this list is selectable at all;
    // it replaced the per-row checkbox that used to carry that meaning.
    const wrapper = await mountShelf([adapter({ id: 1 }), adapter({ id: 2 })]);
    const list = wrapper.find("ul.shelf-list");
    expect(list.attributes("role")).toBe("listbox");
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
    const store = useModelShelfStore();
    await rowAt(wrapper, 0).trigger("keydown", { key: " " });
    await rowAt(wrapper, 0).trigger("keydown", {
      key: "ArrowDown",
      shiftKey: true,
    });
    expect([...store.selectedIds].sort()).toEqual([1, 2]);

    await rowAt(wrapper, 0).trigger("keydown", { key: "Escape" });
    expect(store.selectedRows).toHaveLength(0);
  });

  it("shows no selection bar until something is selected", async () => {
    const wrapper = await mountShelf([adapter()]);
    expect(wrapper.find(".shelf-selbar").exists()).toBe(false);

    await rowAt(wrapper, 0).trigger("click");
    await wrapper.vm.$nextTick();
    expect(textOf(wrapper.find(".shelf-selbar"))).toContain("1 model selected");
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

    const bands = wrapper.findAll(".shelf-band-heading");
    expect(bands).toHaveLength(1);
    // The volume's name, not its mount point: a Linux mount point runs to
    // `/media/glindkvist/102AB4B6757AF9A3` and crowds the header out.
    expect(textOf(bands[0])).toContain("FastModels");
    expect(textOf(bands[0])).not.toContain("/mnt/fast");
    expect(bands[0].find(".shelf-band-label").attributes("title")).toBe(
      "/mnt/fast",
    );
    // Free leads: it is the number that decides whether the next checkpoint
    // fits, and the meter is read at a glance rather than computed from.
    expect(textOf(bands[0])).toContain("512.0 GB free of 1.0 TB");
    expect(wrapper.findAll(".shelf-band-fill")).toHaveLength(2);
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

    expect(textOf(wrapper.find(".shelf-band-heading"))).toContain(
      "Capacity unknown",
    );
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

    expect(wrapper.find(".shelf-band-heading").exists()).toBe(false);
    expect(wrapper.findAll(".shelf-group-btn").length).toBeGreaterThan(0);
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
    expect(mark.find("v-icon-stub").exists()).toBe(true);
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

    expect(wrapper.find(".shelf-band-heading").exists()).toBe(true);
    const rows = wrapper.findAll(".shelf-row");
    expect(rows).toHaveLength(1);

    await rows[0].trigger("click");
    await wrapper.vm.$nextTick();
    expect(textOf(wrapper.find(".shelf-selbar"))).toContain("1 model selected");
    // The band is a header, not a row: it must not have become selectable.
    expect(wrapper.find(".shelf-band-heading").attributes("role")).not.toBe(
      "option",
    );
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

describe("a click that ends a text drag", () => {
  it("is ignored only when the text was dragged inside that row", async () => {
    // Asking whether ANY text is selected anywhere made the whole list
    // unclickable for as long as the reader had a selection elsewhere.
    const wrapper = await mountShelf([adapter({ id: 1 }), adapter({ id: 2 })]);
    const store = useModelShelfStore();
    const rows = wrapper.findAll(".shelf-row");

    // A live selection anchored OUTSIDE the row must not block the click.
    const outside = document.createElement("p");
    outside.textContent = "selected somewhere else";
    document.body.appendChild(outside);
    vi.spyOn(window, "getSelection").mockReturnValue({
      isCollapsed: false,
      anchorNode: outside,
      toString: () => "selected somewhere else",
    });

    await rows[0].trigger("click");
    expect([...store.selectedIds]).toEqual([1]);

    // Anchored INSIDE the clicked row, it is a drag and must be ignored.
    vi.spyOn(window, "getSelection").mockReturnValue({
      isCollapsed: false,
      anchorNode: rows[1].element.firstChild,
      toString: () => "a name",
    });
    await rows[1].trigger("click");
    expect([...store.selectedIds]).toEqual([1]);

    window.getSelection.mockRestore();
    outside.remove();
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

    const note = wrapper.find(".shelf-empty-folder");
    expect(note.attributes("role")).toBeUndefined();
    expect(note.attributes("tabindex")).toBeUndefined();
  });
});

describe("dragging models onto a folder", () => {
  const FOLDERS = [
    { id: 1, path: "/models/loras", kind: "user", movable: "per_item" },
    { id: 2, path: "/models/store", kind: "managed", movable: "root_only" },
    { id: 3, path: "/runs", kind: "source", movable: "per_item" },
  ];

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

  it("puts no focusable control inside the option", async () => {
    // The defect the review of #881 found, and the one this list's own comment
    // already warned about: a <button> inside `role="option"` is unreachable to
    // a listbox's keyboard model. The count is drawn, but it is not a control.
    const wrapper = await mountShelf(run());
    const option = wrapper.find('[role="option"]');
    expect(option.exists()).toBe(true);
    // Anything FOCUSABLE, not just the obvious tags: a `<span role="button"
    // tabindex="0">` is exactly as unreachable inside an option, and a
    // tag-name-only assertion let that mutant through when this was checked.
    expect(
      option.findAll(
        'button, a[href], input, select, textarea, [tabindex], [role="button"]',
      ),
    ).toHaveLength(0);
    expect(option.find(".shelf-row-steps").exists()).toBe(true);
  });

  it("opens and closes from the row with Right and Left", async () => {
    const wrapper = await mountShelf(run());
    const option = wrapper.find('[role="option"]');
    expect(wrapper.findAll(".shelf-row--member")).toHaveLength(0);

    await option.trigger("keydown", { key: "ArrowRight" });
    expect(wrapper.findAll(".shelf-row--member")).toHaveLength(1);

    await option.trigger("keydown", { key: "ArrowLeft" });
    expect(wrapper.findAll(".shelf-row--member")).toHaveLength(0);
  });

  it("speaks the count and the state, since the span is aria-hidden", async () => {
    const wrapper = await mountShelf(run());
    const option = wrapper.find('[role="option"]');
    expect(option.attributes("aria-label")).toContain("2 files");
    expect(option.attributes("aria-label")).toContain("collapsed");

    await option.trigger("keydown", { key: "ArrowRight" });
    expect(option.attributes("aria-label")).toContain("expanded");
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
    const setIcon = () =>
      wrapper.findAll("button").find((b) => b.text().includes("Set icon"));
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

describe("Escape", () => {
  it("clears the selection from anywhere in the shelf, not only from a row", async () => {
    // It used to be handled on the row, so it only worked while a row held the
    // roving tab stop — not after a click moved focus, and not from the
    // toolbar. "Escape clears the selection" has to mean everywhere.
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    const store = useModelShelfStore();
    store.toggleSelected(1);
    await wrapper.vm.$nextTick();
    expect(store.selectedRows).toHaveLength(1);

    await wrapper.find(".shelf-toolbar").trigger("keydown", { key: "Escape" });
    expect(store.selectedRows).toHaveLength(0);
  });

  it("leaves the selection alone when a dialog owns the key", async () => {
    // Escape inside a dialog means "close me". Clearing the selection
    // underneath at the same time is a second, unasked-for effect.
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    const store = useModelShelfStore();
    store.toggleSelected(1);
    await wrapper.find(".shelf-toolbar").trigger("click");
    wrapper.vm.editVerb = "rename";
    await wrapper.vm.$nextTick();

    await wrapper.find(".shelf-toolbar").trigger("keydown", { key: "Escape" });
    expect(store.selectedRows).toHaveLength(1);
  });
});
