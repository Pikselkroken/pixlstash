// The shelf's reading surface.
//
// The three assertions worth having are the ones that guard the measured data
// realities: a third of real adapters have no title, no base model and no
// trigger, so a row must never render a blank; a derived name must stay
// distinguishable from a chosen one; and `unknown` must never read as a
// checkpoint.

import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
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
// answers both requests. Same reason again - without it every
// `listAdapters.mockResolvedValue` here would answer them with adapter rows.
const listSupport = vi.fn();
const deleteModels = vi.fn();
const editModels = vi.fn();
const fetchModelCompanions = vi.fn();
const deleteWorkflowSet = vi.fn();
const fetchWorkflowSets = vi.fn();
const setWorkflowSetsHidden = vi.fn();

/** The set keys the active library hides, as the double's server holds them. */
let hiddenOnServer = new Set();

// The favourites are read on mount, and written by the heart and H. Doubled so
// each test says what the hub holds and reads back what was asked to change.
// The loss of a trained run, read by its id when a stack of its files opens.
const getTrainingJob = vi.fn();
vi.mock("../../api/training", () => ({
  getTrainingJob: (...args) => getTrainingJob(...args),
}));

const listFavourites = vi.fn();
const setFavourites = vi.fn();
vi.mock("../../api/favourites", () => ({
  listFavourites: (...args) => listFavourites(...args),
  setFavourites: (...args) => setFavourites(...args),
}));

vi.mock("../../api/modelShelf", () => ({
  BASE_MODEL_UNASSIGNED: "UNASSIGNED",
  listAdapters: (...args) => {
    if (args[0]?.fileKind === "engine") return listEngines(...args);
    if (args[0]?.fileKind === "unknown") return listUnclassified(...args);
    if (["vae", "text_encoder", "controlnet", "upscaler"].includes(args[0]?.fileKind)) {
      return listSupport(...args);
    }
    return listAdapters(...args);
  },
  listCheckpoints: (...args) => listCheckpoints(...args),
  // The shelf's destructive verb. Mocked for the same reason the folder reads
  // are - it is a network call on a user gesture - and because a suite that
  // really called it would be asserting the server's gate rather than the
  // view's.
  deleteModels: (...args) => deleteModels(...args),
  editModels: (...args) => editModels(...args),
  // Asked before the delete prompt opens; answered per test.
  fetchModelCompanions: (...args) => fetchModelCompanions(...args),
  deleteWorkflowSet: (...args) => deleteWorkflowSet(...args),
  fetchWorkflowSets: (...args) => fetchWorkflowSets(...args),
  setWorkflowSetsHidden: (...args) => setWorkflowSetsHidden(...args),
  // The base-model field asks for its completion list as it opens. Answered
  // with nothing here: the list is the widget's own suite's business, and left
  // unmocked this is a network call on a double-click.
  listBaseModelCompletions: vi.fn().mockResolvedValue([]),
}));

// The shelf reads the drives to band its folder groups. Left unmocked this
// reaches the network, and the failure that comes back is routed as a session
// reset - which empties the shelf store MID-TEST and made an unrelated sort
// assertion read the default view back.
const listModelFolderDevices = vi.fn();
const listModelFolders = vi.fn();
const listModelFolderDirs = vi.fn();
const getComfyuiReads = vi.fn();
// `onMounted` calls `moves.adopt()`, which reads the move job so a move
// started before a reload is picked up. Left unmocked it reaches the network,
// fails, and `console.warn`s from a promise nothing in the test awaits - which
// lands AFTER the file tears down and kills the whole vitest run with
// `EnvironmentTeardownError: Closing rpc while "onUserConsoleLog" was pending`.
// Every file passed and the runner still exited 1 (#880's first CI run). It is
// timing-dependent, so a local run can be green with the same bug present.
const getModelMoveStatus = vi.fn();
const startModelMove = vi.fn();
vi.mock("../../api/modelMoves", () => ({
  getModelMoveStatus: (...args) => getModelMoveStatus(...args),
  startModelMove: (...args) => startModelMove(...args),
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
const unstackStack = vi.fn();
// The two verbs that act inside a run: choosing its cover, and taking one file
// back out of it. Mocked for the same reason - the assertion worth having is
// which stack and which model reach the route.
const setStackCover = vi.fn();
const removeStackMember = vi.fn();
vi.mock("../../api/modelStacks", () => ({
  createStack: (...args) => createStack(...args),
  unstackStack: (...args) => unstackStack(...args),
  setStackCover: (...args) => setStackCover(...args),
  removeStackMember: (...args) => removeStackMember(...args),
}));

// The shelf reads `route.name` to decide which of its two views is showing, so
// the suite drives the tab by writing `nav.route.name`. REACTIVE, not a plain
// object with a getter: the tab is a `computed`, and a non-reactive source
// simply never re-evaluates - which reads in a test exactly like the panel
// failing to swap. Mocked rather than given a real router, because these tests
// mount the view alone and a router would have to carry every app route.
const nav = vi.hoisted(() => ({ route: null, push: null }));
vi.mock("vue-router", async () => {
  const { reactive } = await vi.importActual("vue");
  const { vi: vitest } = await import("vitest");
  nav.route = reactive({
    name: "models",
    query: {},
    params: {},
    path: "/models",
  });
  nav.push = vitest.fn(({ name }) => {
    nav.route.name = name;
  });
  return {
    useRoute: () => nav.route,
    useRouter: () => ({ push: nav.push, replace: vitest.fn() }),
  };
});

const createModelFolder = vi.fn();
vi.mock("../../api/modelFolders", async (importOriginal) => ({
  ...(await importOriginal()),
  createModelFolder: (...args) => createModelFolder(...args),
  getComfyuiReads: (...args) => getComfyuiReads(...args),
  listModelFolderDevices: (...args) => listModelFolderDevices(...args),
  listModelFolderDirs: (...args) => listModelFolderDirs(...args),
  listModelFolders: (...args) => listModelFolders(...args),
}));

// The names, colours and thumbnails behind the assignment rings (#892/#904).
// Mocked for the same reason the folder reads are: unmocked they reach the
// network from `onMounted`, and the rejection lands in a `console.warn` from a
// promise no test awaits - which is the teardown race noted above.
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

// The thumbnail verb's library route: the picker hands back a picture, the view
// fetches ITS BYTES and posts them to the icon store. Both halves are doubled
// here - the read because it is a network call on a user gesture, and the write
// because a suite that really called it would be asserting the server's gate
// rather than this view's wiring.
const getPictureThumbnailBlob = vi.fn();
// `importOriginal` spread, like its two neighbours: a bare replacement leaves
// every OTHER export of that module `undefined` across this view's whole module
// graph, so the next child to import `pictureThumbnailUrl` would die with a
// bare "is not a function" in a suite that has nothing to do with thumbnails.
vi.mock("../../api/pictures", async (importOriginal) => ({
  ...(await importOriginal()),
  getPictureThumbnailBlob: (...args) => getPictureThumbnailBlob(...args),
}));
// The folder-header menu's gates (#1270). They are Settings' gates and their
// reasons are pinned through `ModelFoldersSection.test.js`; here a stub keeps
// this suite about the menu.
const folderGates = vi.hoisted(() => ({
  moveReason: "",
  scan: () => {},
}));
vi.mock("../../composables/useModelFolderGates", () => ({
  useModelFolderGates: () => ({
    relocateReason: () => folderGates.moveReason,
    scan: (...args) => folderGates.scan(...args),
  }),
}));

const setModelIcon = vi.fn();
vi.mock("../../api/modelIcons", async (importOriginal) => ({
  ...(await importOriginal()),
  setModelIcon: (...args) => setModelIcon(...args),
}));

import ModelShelf from "./ModelShelf.vue";
import { characterThumbnailUrl } from "../../api/characters";
import { modelIconUrl } from "../../api/modelIcons";
import KindMenu from "../widgets/KindMenu.vue";
import { useModelMovesStore } from "../../stores/useModelMovesStore";
import { useFavouritesStore } from "../../stores/useFavouritesStore";
import { useModelFoldersStore } from "../../stores/useModelFoldersStore";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useReviewSessionsStore } from "../../stores/useReviewSessionsStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import { formatUserDate } from "../../utils/utils";

// The tooltip is stubbed to carry its text on the element it wraps or sits in:
// the real one renders nothing until it opens, and needs Vuetify besides.
const TooltipStub = {
  name: "Tooltip",
  props: ["text", "activator"],
  template:
    '<span class="tip" :data-text="text"><slot name="activator" :props="{}" /></span>',
};

/** The tooltip text a control carries, whether the tip sits inside or around it. */
function tip(wrapper) {
  const inner = wrapper.find(".tip");
  if (inner.exists()) return inner.attributes("data-text");
  return wrapper.element.parentElement?.dataset.text;
}

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      Tooltip: TooltipStub,
      "v-menu": {
        template: "<div><slot name='activator' :props='{}' /><slot /></div>",
      },
      ShelfShowPanel: true,
      ShelfSortPanel: true,
      // Their own suites mount them. Here they would only drag Vuetify's dialog
      // and tooltip providers into a suite that installs neither. The selection
      // BAR is deliberately left real: what a selection does to this view is
      // this suite's business.
      ShelfEditDialog: true,
      ShelfMoveDialog: true,
      FolderMoveDialog: true,
      ShelfRemoveFolderDialog: true,
      WorkflowSetRenameDialog: true,
      MergeCopiesDialog: true,
      ReplaceMissingDialog: true,

      // Same reason, for the same provider: it wraps `AppDialog`. Its own suite
      // mounts it against the companions payload.
      ModelWorksWithDialog: {
        name: "ModelWorksWithDialog",
        props: ["model"],
        template: "<div class='ww-stub'>{{ model?.filename }}</div>",
      },
      // Its own suite mounts it against a payload; here what matters is only
      // whether the shelf puts it on screen instead of the row list.
      ModelSetGrid: {
        name: "ModelSetGrid",
        emits: ["works-with", "menu"],
        // The real grid exposes the cards it draws selected; a test sets it.
        data: () => ({ selectedCardGroups: [] }),
        template: "<div class='set-grid-stub'></div>",
      },
      // The host-path picker `Add file` opens. Real, it would drag Vuetify's
      // dialog provider into a suite that installs none; stubbed, it still
      // emits `select`, which is the whole of what this view listens for.
      FolderBrowser: true,
      // The library picture picker the thumbnail verb opens. Its own suite
      // mounts it; real here it would drag Vuetify's dialog provider into a
      // suite that installs none, and read the shared entity lists on every
      // mount. The stub RENDERS ITS SLOTS rather than being `true`, because the
      // `Choose a file…` route this change deliberately keeps lives in one -
      // and a `true` stub would let that route be deleted with the suite green.
      PicturePicker: {
        name: "PicturePicker",
        props: ["open"],
        template: "<div class='picker-stub'><slot name='footer-start' /></div>",
      },
      ProgressOverlay: true,
      // The picker inside the selection bar, which the bar's own suite covers.
      // Left real it would read the shared entity lists on every mount here.
      AddToEntityControl: true,
    },
  },
};

/** The shape `/adapters` really returns, taken from a fixture probe. */
/**
 * A pointer click (`detail` 1) and a right-button press: `trigger` cannot set
 * either field, jsdom makes them getters. A keyboard click has `detail` 0.
 */
async function mouseClick(wrapper) {
  wrapper.element.dispatchEvent(
    new MouseEvent("click", { bubbles: true, detail: 1 }),
  );
}

async function rightPress(wrapper) {
  wrapper.element.dispatchEvent(
    new MouseEvent("pointerdown", { bubbles: true, button: 2 }),
  );
}

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
  // The ROW LIST is what this suite is about, and since #1438 the default axis
  // is the set grid, which replaces it. Chosen here rather than per test, so a
  // new test lands on the list without having to know that; the grid's own
  // screen is covered by `ModelSetGrid.test.js`.
  useModelShelfStore().setView({ groupBy: "none" });
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
  fetchWorkflowSets
    .mockReset()
    .mockResolvedValue({ combinations: [], no_set: [], hand_made: [] });
  hiddenOnServer = new Set();
  setWorkflowSetsHidden.mockReset().mockImplementation(async (keys, hidden) => {
    for (const key of keys) {
      if (hidden) hiddenOnServer.add(key);
      else hiddenOnServer.delete(key);
    }
    return [...hiddenOnServer];
  });
  listModelFolderDevices.mockReset();
  listModelFolderDevices.mockResolvedValue([]);
  // The thumbnail verb's two halves. Reset here rather than per-test: a
  // rejection armed for the unreachable-file case would otherwise still be
  // armed for whatever ran next.
  getPictureThumbnailBlob.mockReset();
  setModelIcon.mockReset();
  listModelFolders.mockReset();
  listModelFolders.mockResolvedValue([]);
  getComfyuiReads.mockReset();
  getComfyuiReads.mockResolvedValue({ known: false, reads: [] });
  listModelFolderDirs.mockReset();
  listModelFolderDirs.mockImplementation(async (id, path) => ({
    folder_id: id,
    path,
    locked: false,
    model_count: 0,
    bytes: 0,
    dirs: [],
  }));
  nav.route.name = "models";
  nav.push.mockClear();
  createModelFolder.mockReset();
  createModelFolder.mockResolvedValue({
    id: 7,
    path: "/home/g/ai-toolkit/output",
    kind: "source",
  });
  // `idle` is what a machine with no move in flight reports, and `adopt()`
  // adopts nothing from it - so the mount path stays silent instead of warning
  // from an unawaited promise.
  getModelMoveStatus.mockReset();
  getModelMoveStatus.mockResolvedValue({ status: "idle", results: [] });
  startModelMove.mockReset().mockResolvedValue({ status: "running", results: [] });
  addModelFile.mockReset();
  createStack.mockReset();
  unstackStack.mockReset();
  setStackCover.mockReset().mockResolvedValue({ stack_id: 7, model_ids: [] });
  removeStackMember
    .mockReset()
    .mockResolvedValue({ released: 1, dissolved: false });
  listCharacters.mockReset().mockResolvedValue([]);
  listPictureSets.mockReset().mockResolvedValue([]);
  listFavourites.mockReset().mockResolvedValue({ model: [], workflow: [], set: [] });
  setFavourites.mockReset().mockResolvedValue({ model: [], workflow: [], set: [] });
});

// A paste starts a move, and the move store polls it a second later. A fresh
// pinia does not stop that timer: it fires in whichever test is running by
// then, reads the idle status, and pushes "Nothing moved." into THAT test's
// notices - which a starved runner turned into a random failure.
afterEach(() => useModelMovesStore().resetForSession());

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
    // says so in words rather than leaving a cell that reads as a gap. The date
    // is the exception and deliberately so: it is a FIGURE, like the size
    // beside it, and a figure nobody recorded is left blank rather than
    // dashed - this fixture carries no `added_at`. Sets is blank too until the
    // sets are read, which this mount never asks for.
    const cells = row.findAll(".shelf-col").map((s) => textOf(s));
    expect(cells).toEqual(["LoRA", "not set", "342.1 MB", "", ""]);
  });

  it("takes the quant postfix out of the name and puts it on a chip", async () => {
    // The two halves of one change, asserted together on purpose: stripping
    // without badging makes two quant builds of one model read identically,
    // which is worse than leaving the postfix in the name.
    const wrapper = await mountShelf([
      adapter({
        display_name: null,
        filename: "z_image_turbo_bf16.safetensors",
        quant: "bf16",
      }),
    ]);
    const row = wrapper.find(".shelf-row");
    expect(row.find(".shelf-row-name").text()).toBe("z image turbo");
    expect(row.find(".shelf-chip--quant").text()).toBe("BF16");
    // And the file's own string is untouched on the line under it, which is
    // what a reader pastes into a ComfyUI node.
    expect(row.find(".shelf-row-file").text()).toContain(
      "z_image_turbo_bf16.safetensors",
    );
  });

  it("badges the precision even where the NAME never carried it", async () => {
    // The column is read from the safetensors header, so a file whose name
    // says nothing still has an answer - and a row the owner named by hand
    // keeps THEIR name and gets the chip all the same. `fp8_e4m3` is the id
    // the API serves, folded from the header's own `f8_e4m3`; the chip shows
    // the short label and the hover text carries the variant.
    const wrapper = await mountShelf([
      adapter({
        display_name: "Clementine",
        filename: "clem.safetensors",
        quant: "fp8_e4m3",
      }),
    ]);
    const row = wrapper.find(".shelf-row");
    expect(row.find(".shelf-row-name").text()).toBe("Clementine");
    expect(row.find(".shelf-chip--quant").text()).toBe("FP8");
  });

  it("puts the trigger word on the row, and marks a default as one", async () => {
    const wrapper = await mountShelf([
      adapter({
        id: 1,
        filename: "a.safetensors",
        trigger_words: ["ohwx", "woman"],
        trigger_words_source: "recorded",
      }),
      adapter({
        id: 2,
        sha256: "b".repeat(64),
        filename: "b.safetensors",
        trigger_words: ["Ada"],
        trigger_words_source: "character",
      }),
      adapter({ id: 3, sha256: "c".repeat(64), filename: "c.safetensors" }),
    ]);
    const chips = Object.fromEntries(
      wrapper.findAll(".shelf-row").map((row) => [
        row.find(".shelf-row-file").text(),
        row.find(".shelf-chip--trigger"),
      ]),
    );
    expect(chips["a.safetensors"].text()).toBe("Trigger word: ohwx +1");
    expect(chips["a.safetensors"].classes()).not.toContain("shelf-chip--none");
    // The person's name is not a word out of the file, so it takes the dashed
    // "nothing recorded" chip rather than the mono one.
    expect(chips["b.safetensors"].text()).toBe("Trigger word: Ada");
    expect(chips["b.safetensors"].classes()).toContain("shelf-chip--none");
    expect(chips["c.safetensors"].exists()).toBe(false);
  });

  it("draws no quant chip on a row that records no precision", async () => {
    const wrapper = await mountShelf([
      adapter({ display_name: null, filename: "Foxglove.safetensors" }),
    ]);
    expect(wrapper.find(".shelf-chip--quant").exists()).toBe(false);
  });

  it("marks the derived name by type, not by fading it", async () => {
    // Rank is size, weight and tracking, never opacity (§5.1) - and 37% of
    // rows faded would be a column of ghosts rather than a signal.
    const wrapper = await mountShelf([
      adapter({ id: 1, display_name: null }),
      adapter({ id: 2, display_name: "Clementine" }),
      // Nothing survives the strip, so this one IS the filename - the positive
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

    await field.setValue("Cyanwood");
    await field.trigger("keydown", { key: "Enter" });
    expect(edit).toHaveBeenCalledWith([7], { display_name: "Cyanwood" });
    expect(wrapper.find(".shelf-row-rename").exists()).toBe(false);
  });

  it("seeds the field with the shown name and writes nothing untouched", async () => {
    // Seeded with what the row shows, selected so typing replaces it. That
    // name is derived here - a guess - so Enter on it as-is must not adopt it.
    const wrapper = await mountShelf(
      [adapter({ id: 7, display_name: null })],
      [],
      [],
      { attachTo: document.body },
    );
    const store = useModelShelfStore();
    const edit = vi.spyOn(store, "editModelIds").mockResolvedValue(true);
    const shown = wrapper.find(".shelf-row-name").text();
    expect(shown).toBeTruthy();

    await wrapper.find(".shelf-row").trigger("keydown", { key: "F2" });
    await wrapper.vm.$nextTick();
    const field = wrapper.find(".shelf-row-rename");
    expect(field.element.value).toBe(shown);
    expect([
      field.element.selectionStart,
      field.element.selectionEnd,
    ]).toEqual([0, shown.length]);

    await field.trigger("keydown", { key: "Enter" });
    expect(edit).not.toHaveBeenCalled();
    wrapper.unmount();
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
    // Seeded from what the cell shows, unlike the name field: here that is
    // what the file said, so a correction is one word rather than a retype.
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

  it("starts a guess from the guess, Enter accepts it, and clearing it says 'none of these'", async () => {
    // A guessed row stores no base model of its own, so comparing against the
    // stored value made an emptied field look unchanged and wrote nothing.
    const wrapper = await mountShelf([
      adapter({
        id: 7,
        base_model: null,
        base_model_canonical: "Sana",
        base_model_source: "filename_fuzzy",
      }),
    ]);
    const store = useModelShelfStore();
    const edit = vi.spyOn(store, "editModelIds").mockResolvedValue(true);

    await wrapper.find(".shelf-base-text").trigger("dblclick");
    let field = wrapper.find(".shelf-row-base-edit");
    expect(field.element.value).toBe("Sana");
    // Clicking away from the untouched guess leaves it a guess...
    await field.trigger("blur");
    expect(edit).not.toHaveBeenCalled();

    // ...while Enter on it is a confirmation, the same write as the tag's.
    await wrapper.find(".shelf-base-text").trigger("dblclick");
    field = wrapper.find(".shelf-row-base-edit");
    await field.trigger("keydown", { key: "Enter" });
    await wrapper.vm.$nextTick();
    expect(edit).toHaveBeenCalledWith([7], { base_model: "Sana" });
    edit.mockClear();

    await wrapper.find(".shelf-base-text").trigger("dblclick");
    field = wrapper.find(".shelf-row-base-edit");
    await field.setValue("");
    await field.trigger("keydown", { key: "Enter" });
    expect(edit).toHaveBeenCalledWith([7], { base_model: null });
  });

  it("writes nothing on Enter when a stated base model is left as it is", async () => {
    // Only a guess has anything to accept; a stated value is already decided.
    const wrapper = await mountShelf([adapter({ id: 7 })]);
    const store = useModelShelfStore();
    const edit = vi.spyOn(store, "editModelIds").mockResolvedValue(true);

    await wrapper.find(".shelf-col--base span").trigger("dblclick");
    await wrapper.find(".shelf-row-base-edit").trigger("keydown", {
      key: "Enter",
    });
    await wrapper.vm.$nextTick();
    expect(edit).not.toHaveBeenCalled();
  });

  it("confirms a guess with one click, writing the label it shows", async () => {
    const wrapper = await mountShelf([
      adapter({
        id: 7,
        base_model: "sdxl",
        base_model_canonical: "SDXL 1.0",
        base_model_source: "declared_fuzzy",
      }),
      adapter({ id: 8 }),
    ]);
    const store = useModelShelfStore();
    const edit = vi.spyOn(store, "editModelIds").mockResolvedValue(true);

    const tags = wrapper.findAll(".shelf-base-guess");
    // Only the guessed row offers it; a stated base model is already decided.
    expect(tags).toHaveLength(1);
    await tags[0].trigger("click");
    expect(edit).toHaveBeenCalledWith([7], { base_model: "SDXL 1.0" });
    expect(store.isSelected(7)).toBe(false);
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
    // no prompt. It has to fire - and it must not fire a second time behind the
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
    // back drops a keyboard reader at the top of the document - the defect the
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

describe("the kind the workflows suggest (#1962)", () => {
  const encoder = (overrides = {}) =>
    adapter({
      id: 7,
      file_kind: "unknown",
      kind: null,
      display_name: "t5 xxl",
      suggested_kind: "text_encoder",
      suggested_by: 3,
      suggested_fields: ["clip_name"],
      kind_decided: false,
      ...overrides,
    });

  async function mountUnclassified(row) {
    useModelShelfStore().setFilters({
      adapters: false,
      checkpoints: false,
      unclassified: true,
    });
    return mountShelf([], [], [row]);
  }

  it("writes the suggestion under the Kind chip, and only while undecided", async () => {
    const offered = await mountUnclassified(encoder());
    const cell = offered.find(".shelf-row .shelf-col--kind");
    expect(textOf(cell.find(".kmenu-chip"))).toBe("Unclassified");
    expect(textOf(cell.find("[data-testid='kind-suggestion']"))).toBe(
      "Text encoder?",
    );

    const decided = await mountUnclassified(encoder({ kind_decided: true }));
    const plain = decided.find(".shelf-row .shelf-col--kind");
    expect(textOf(plain)).toBe("Unclassified");
    expect(plain.find("[data-testid='kind-suggestion']").exists()).toBe(false);
  });

  it("files the row from its menu, with a receipt that undoes it", async () => {
    editModels.mockReset().mockResolvedValue({ updated: [7], fields: ["file_kind"] });
    const wrapper = await mountUnclassified(encoder());
    wrapper.findComponent(KindMenu).vm.$emit("pick", "text_encoder");
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(editModels).toHaveBeenCalledWith([7], { file_kind: "text_encoder" });
    const notice = useNoticeStore().notices.at(-1);
    expect(notice.text).toBe("Filed t5 xxl as Text encoder.");
    expect(notice.action.label).toBe("Undo");

    await notice.action.handler();
    expect(editModels).toHaveBeenLastCalledWith([7], {
      file_kind: "unknown",
      suggest_kind: true,
    });
  });

  it("hands focus back to the Kind menu when the write fails too", async () => {
    editModels.mockReset().mockRejectedValue(new Error("offline"));
    const focus = vi.spyOn(HTMLElement.prototype, "focus");
    try {
      const wrapper = await mountUnclassified(encoder());
      wrapper.findComponent(KindMenu).vm.$emit("pick", "text_encoder");
      await new Promise((resolve) => setTimeout(resolve, 0));
      await wrapper.vm.$nextTick();
      expect(useNoticeStore().notices.at(-1).level).toBe("error");
      expect(
        focus.mock.contexts.some(
          (el) => el.dataset?.testid === "kind-menu",
        ),
      ).toBe(true);
    } finally {
      focus.mockRestore();
    }
  });

  it("dismisses the suggestion without touching the kind", async () => {
    editModels.mockReset().mockResolvedValue({ updated: [7], fields: [] });
    const wrapper = await mountUnclassified(encoder());
    wrapper.findComponent(KindMenu).vm.$emit("suggest", false);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(editModels).toHaveBeenCalledWith([7], { suggest_kind: false });
  });

  it("leaves an engine's kind a chip: PixlStash's own is not curated", async () => {
    // Served through the adapter double: which block brings it is not this
    // test's business, what its Kind cell draws is.
    const wrapper = await mountShelf([
      adapter({ id: 9, file_kind: "engine", capabilities: ["tagger"] }),
    ]);
    const row = wrapper.find(".shelf-row");
    expect(row.exists()).toBe(true);
    expect(row.findComponent(KindMenu).exists()).toBe(false);
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
    // kind is reachable over the raw API. Every adapter sits under the LoRA
    // Type row whatever its algorithm says, so the Type cell names it LoRA
    // rather than going blank: an empty Type column reads as a broken row.
    const wrapper = await mountShelf([adapter({ kind: "  " })]);
    expect(textOf(wrapper.find(".shelf-row .shelf-col"))).toContain("LoRA");
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
    // A file that is gone has nothing to picture: its glyph takes the mark's
    // place. One that is only out of reach keeps its mark.
    expect(rows[0].find(".shelf-row-mark .shelf-row-loc").exists()).toBe(true);
    expect(rows[0].find(".shelf-row-mark .mmark").exists()).toBe(false);
    expect(rows[1].find(".shelf-row-label .shelf-row-loc").exists()).toBe(true);
    expect(rows[1].find(".shelf-row-mark .mmark").exists()).toBe(true);
  });

  it("says where the file is on an axis that draws no folder header", async () => {
    // Only `groupBy: 'folder'` puts the folder on screen. Every other axis -
    // and `none`, the default this mounts with - left the reader with no way
    // to tell a copy on the spare disk from one in the ComfyUI tree.
    const wrapper = await mountShelf([
      adapter({
        locations: [
          { state: "present", folder_path: "/home/me/models", relpath: "a.st" },
          { state: "present", folder_path: "/media/me/spare", relpath: "a.st" },
        ],
      }),
    ]);
    expect(tip(wrapper.find(".shelf-row-file"))).toBe(
      `/home/me/models/a.st\n/media/me/spare/a.st\nSHA-256: ${"a".repeat(64)}`,
    );
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
    expect(tip(wrapper.find(".shelf-row--member .shelf-row-file"))).toBe(
      `/media/me/spare/b.st\nSHA-256: ${"b".repeat(64)}`,
    );
  });

  it("draws the loss of a trained run in its expansion", async () => {
    getTrainingJob.mockResolvedValue({
      id: 42,
      loss_curve: [
        { step: 1, loss: 0.5 },
        { step: 2, loss: 0.4 },
      ],
    });
    const wrapper = await mountShelf([
      adapter({ id: 1, stack_id: 7, stack_position: 0, training_run_id: 42 }),
      adapter({
        id: 2,
        stack_id: 7,
        stack_position: 1,
        sha256: "b".repeat(64),
        training_run_id: 42,
      }),
    ]);
    expect(wrapper.find("[data-testid='shelf-run-loss']").exists()).toBe(false);
    await wrapper.find(".shelf-row").trigger("keydown", { key: "ArrowRight" });
    await flushPromises();
    expect(getTrainingJob).toHaveBeenCalledWith(42);
    expect(
      wrapper.find("[data-testid='shelf-run-loss'] .lora-loss-figure").exists(),
    ).toBe(true);
  });

  it("draws no loss band for an expanded stack with no training run", async () => {
    getTrainingJob.mockClear();
    const wrapper = await mountShelf([
      adapter({ id: 1, stack_id: 7, stack_position: 0, training_run_id: null }),
      adapter({
        id: 2,
        stack_id: 7,
        stack_position: 1,
        sha256: "b".repeat(64),
        training_run_id: null,
      }),
    ]);
    await wrapper.find(".shelf-row").trigger("keydown", { key: "ArrowRight" });
    await flushPromises();
    expect(wrapper.find("[data-testid='shelf-run-loss']").exists()).toBe(false);
    expect(getTrainingJob).not.toHaveBeenCalled();
  });

  it("still names the hash when every copy has been forgotten", async () => {
    // No path left to give, but the hash is what the file is matched by.
    const wrapper = await mountShelf([adapter({ locations: [] })]);
    expect(tip(wrapper.find(".shelf-row-file"))).toBe(
      `SHA-256: ${"a".repeat(64)}`,
    );
  });

  it("says a checkpoint not hashed yet is waiting, rather than showing none", async () => {
    const wrapper = await mountShelf([
      adapter({ sha256: null, hash_pending: true }),
    ]);
    expect(tip(wrapper.find(".shelf-row-file"))).toBe(
      "/m/a.st\nSHA-256: not read yet",
    );
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

describe("refetching after an edit", () => {
  it("keeps the list, and so its scroll position, while the new rows load", async () => {
    // Assigning a stack to a character refetches the shelf. Swapping the list
    // for "Reading the shelf…" meanwhile rebuilt the scrollport at the top.
    const wrapper = await mountShelf([adapter()]);
    const scrollport = wrapper.find(".shelf-scroll").element;

    let land;
    listAdapters.mockReturnValue(new Promise((resolve) => (land = resolve)));
    const pending = useModelShelfStore().fetchRows();
    await wrapper.vm.$nextTick();

    expect(useModelShelfStore().loading).toBe(true);
    // The same element, not a rebuilt one: a rebuilt scrollport starts at 0.
    expect(wrapper.find(".shelf-scroll").element).toBe(scrollport);
    expect(textOf(wrapper.find(".shelf-body"))).not.toContain(
      "Reading the shelf",
    );

    land([adapter()]);
    await pending;
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".shelf-scroll").element).toBe(scrollport);
  });

  it("keeps the set grid mounted through a refetch too", async () => {
    const wrapper = await mountShelf([adapter()]);
    useModelShelfStore().setView({ groupBy: "workflow_set" });
    await wrapper.vm.$nextTick();
    const grid = wrapper.find(".set-grid-stub").element;
    expect(grid).toBeTruthy();

    listAdapters.mockReturnValue(new Promise(() => {}));
    useModelShelfStore().fetchRows();
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".set-grid-stub").element).toBe(grid);
  });

  it("says it is reading, not 'no models', while Reset refetches an empty view", async () => {
    const wrapper = await mountShelf([]);
    await useModelShelfStore().setFilters(
      { adapters: false, checkpoints: false, unclassified: false },
      { refetch: true },
    );
    listAdapters.mockReturnValue(new Promise(() => {}));
    useModelShelfStore().resetFilters();
    await wrapper.vm.$nextTick();
    expect(textOf(wrapper.find(".shelf-state"))).toContain("Reading the shelf");
  });

  it("still says it is reading before the first rows arrive", async () => {
    listAdapters.mockReturnValue(new Promise(() => {}));
    listCheckpoints.mockResolvedValue([]);
    useModelShelfStore().setView({ groupBy: "none" });
    const wrapper = mount(ModelShelf, globalOpts);
    await wrapper.vm.$nextTick();
    expect(textOf(wrapper.find(".shelf-state"))).toContain("Reading the shelf");
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
    fetchWorkflowSets.mockClear();
    useModelShelfStore().resetForSession();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await wrapper.vm.$nextTick();

    expect(listAdapters).toHaveBeenCalled();
    // The sets go with the rows; the grid and the Models rail asked for them
    // only as they mounted, and are still mounted.
    expect(fetchWorkflowSets).toHaveBeenCalledTimes(1);
    expect(useModelShelfStore().setsLoaded).toBe(true);
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
    // The rule that used to read "rows are not focus stops" - 1,800 empty stops
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
    // The description hangs on the shelf PANEL rather than the region: every
    // sentence in it is about the row list, and the region now also contains
    // the training-runs panel, which has none of those controls.
    const panel = wrapper.find("#shelf-panel-shelf");
    expect(panel.attributes("role")).toBe("region");
    expect(panel.attributes("aria-label")).toBe("Model list");
    expect(panel.attributes("aria-describedby")).toBe("shelf-help");
    expect(wrapper.find("#shelf-help").exists()).toBe(true);
  });

  it("describes the tail that is actually there", async () => {
    // The description is the only account of the bar a screen-reader user
    // gets, so it is the one place a removed control can go on existing for
    // them alone. It named Undo and Redo until the shelf stopped mounting
    // them.
    const wrapper = await mountShelf([adapter()]);
    const help = wrapper.find("#shelf-help").text();
    expect(help).toContain("Settings and the Models toggle");
    expect(help).toMatch(/nothing on this screen can be undone/i);
    expect(help).not.toMatch(/undo and redo/i);
  });
});

// ── F2: group headers and the Sort split-button ────────────────────────────

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

describe("the column headings", () => {
  /** The heading button for a column, by the width class on its wrapper. */
  const headOf = (wrapper, key) =>
    wrapper.find(`.shelf-head-col--${key} .shelf-head-cell`);
  const gripOf = (wrapper, key) =>
    wrapper.find(`.shelf-head-col--${key} .shelf-head-grip`);

  it("sorts on the column pressed, at that column's own end", async () => {
    // Not the direction the previous key was left at: arriving at Base from
    // "Date added, newest first" would otherwise hand back Z to A.
    const wrapper = await mountShelf([adapter()]);
    const store = useModelShelfStore();
    expect(store.view.sortKey).toBe("added_at");
    expect(store.view.sortDirection).toBe("desc");

    await headOf(wrapper, "base").trigger("click");
    expect(store.view.sortKey).toBe("base_model");
    expect(store.view.sortDirection).toBe("asc");

    // A size column starts at the big files, because that is what anyone
    // sorting a shelf by size is looking for.
    await headOf(wrapper, "size").trigger("click");
    expect(store.view.sortKey).toBe("size");
    expect(store.view.sortDirection).toBe("desc");

    // Name is drawn on its own rather than from the column list - it is the
    // flexible track, with no width and no grip - so it is asserted separately
    // or the one heading written by hand is the one nothing covers.
    await wrapper
      .find(".shelf-head-col--label .shelf-head-cell")
      .trigger("click");
    expect(store.view.sortKey).toBe("name");
    expect(store.view.sortDirection).toBe("asc");
  });

  it("flips the direction when the sorted column is pressed again", async () => {
    const wrapper = await mountShelf([adapter()]);
    const store = useModelShelfStore();
    await headOf(wrapper, "size").trigger("click");
    expect(store.view.sortDirection).toBe("desc");
    await headOf(wrapper, "size").trigger("click");
    expect(store.view.sortDirection).toBe("asc");
    expect(store.view.sortKey).toBe("size");
  });

  it("names its own state and what pressing it would do", async () => {
    // The heading is the sort control for a mouse user, so a screen-reader
    // user has to hear the same three things the arrow and the ink say.
    const wrapper = await mountShelf([adapter()]);
    expect(headOf(wrapper, "size").attributes("aria-label")).toBe(
      "Size, not sorted. Activate to sort Largest first.",
    );
    await headOf(wrapper, "size").trigger("click");
    expect(headOf(wrapper, "size").attributes("aria-label")).toBe(
      "Size, sorted Largest first. Activate to sort Smallest first.",
    );
  });

  it("is absent from every state that draws no rows", async () => {
    // A header for a list that is not there names nothing, and the three empty
    // states are the ones a reader is meant to act on rather than read past.
    // The positive control first, or every assertion below passes on a typo.
    const shown = await mountShelf([adapter()]);
    expect(shown.find(".shelf-head").exists()).toBe(true);

    setActivePinia(createPinia());
    const wrapper = await mountShelf([]);
    const store = useModelShelfStore();
    expect(wrapper.find(".shelf-head").exists()).toBe(false);

    store.setFilters({ adapters: false, checkpoints: false });
    store.setFilters({ engines: false, unclassified: false, support: false });
    await wrapper.vm.$nextTick();
    expect(store.nothingSelected).toBe(true);
    expect(wrapper.find(".shelf-head").exists()).toBe(false);

    store.error = "The shelf could not be read.";
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".shelf-head").exists()).toBe(false);
  });

  it("sorts on Type and Sets too, every column being a button", async () => {
    const wrapper = await mountShelf([adapter()]);
    const store = useModelShelfStore();
    await headOf(wrapper, "kind").trigger("click");
    expect(store.view.sortKey).toBe("type");
    expect(store.view.sortDirection).toBe("asc");
    await headOf(wrapper, "sets").trigger("click");
    expect(store.view.sortKey).toBe("sets");
    expect(store.view.sortDirection).toBe("desc");
    expect(wrapper.find(".shelf-head").findAll("button").length).toBe(6);
    expect(gripOf(wrapper, "kind").exists()).toBe(true);
  });

  it("carries the order into the grid's own hidden headings", async () => {
    // The visible strip is a control group outside the treegrid, so without
    // this a reader inside the grid hears the columns and not the order.
    const wrapper = await mountShelf([adapter()]);
    await headOf(wrapper, "base").trigger("click");
    const headers = wrapper.findAll('[role="columnheader"]');
    expect(headers.map((h) => h.attributes("aria-sort"))).toEqual([
      "none", // Name
      "none", // Type
      "ascending", // Base
      "none", // Size
      // Added names whichever date axis the shelf is on and is therefore
      // `none` here for `added_at` rather than absent.
      "none",
      "none", // Sets
    ]);
  });
});

describe("resizing the columns", () => {
  const gripOf = (wrapper, key) =>
    wrapper.find(`.shelf-head-col--${key} .shelf-head-grip`);

  // Dispatched rather than `trigger`ed: test-utils builds the event and then
  // assigns the options onto it, and `clientX` is a getter on MouseEvent, so
  // the coordinate the whole gesture is made of never arrives.
  //
  // `buttons` is part of the gesture and not decoration: a move that arrives
  // with nothing held down is how the code recognises a release it never heard
  // about, so a helper that left it at 0 would end every drag on its first
  // frame. `pointerup` is the one event that genuinely carries 0.
  const fire = async (wrapper, grip, type, clientX, buttons = 1) => {
    grip.element.dispatchEvent(
      new MouseEvent(type, { clientX, buttons, bubbles: true }),
    );
    await wrapper.vm.$nextTick();
  };

  const drag = async (wrapper, grip, from, to) => {
    await fire(wrapper, grip, "pointerdown", from);
    await fire(wrapper, grip, "pointermove", to);
    await fire(wrapper, grip, "pointerup", to, 0);
  };

  // Leftwards widens: the grip is the column's left edge, and the three fixed
  // columns are anchored to the strip's right edge, so that is the direction
  // that keeps the edge under the pointer.
  it("widens the column the grip belongs to, and remembers it", async () => {
    const wrapper = await mountShelf([adapter()]);
    const store = useModelShelfStore();
    expect(store.view.columnWidths.base).toBe(140);

    await drag(wrapper, gripOf(wrapper, "base"), 240, 200);
    expect(store.view.columnWidths.base).toBe(180);
    // The variable the rows read, not a per-cell style: one declaration, so a
    // heading and the cells under it cannot drift apart.
    expect(
      wrapper.find(".shelf").element.style.getPropertyValue("--shelf-col-base"),
    ).toBe("180px");
    expect(
      JSON.parse(window.localStorage.getItem("pixlstash:modelShelfView"))
        .columnWidths.base,
    ).toBe(180);
  });

  it("stops widening where the Name track would go under its floor", async () => {
    // The ceiling a drag actually meets is the panel, not the store's bound:
    // every pixel a fixed column takes comes out of Name, and past 200px of it
    // the shelf would scroll sideways with the rows sliding under a strip
    // whose background stops at the scrollport. jsdom lays nothing out, so the
    // measurement is the one thing that has to be stated here.
    const wrapper = await mountShelf([adapter()]);
    const store = useModelShelfStore();
    Object.defineProperty(
      wrapper.find(".shelf-head-col--label").element,
      "offsetWidth",
      { configurable: true, value: 260 },
    );

    await drag(wrapper, gripOf(wrapper, "base"), 400, 0);
    expect(store.view.columnWidths.base).toBe(200);

    // The separator ANNOUNCES that limit rather than the store's absolute
    // ceiling: a reader told the maximum is 400 and stopped at 200 has been
    // told the wrong thing about the control in their hand. 260 because the
    // stubbed track never shrinks - it is the live measurement that is being
    // asserted here, not the arithmetic.
    expect(gripOf(wrapper, "base").attributes("aria-valuemax")).toBe("260");

    // And a panel with nothing left to give still lets the column back down:
    // the measurement is a ceiling, so a request to narrow passes untouched.
    await drag(wrapper, gripOf(wrapper, "base"), 0, 40);
    expect(store.view.columnWidths.base).toBe(160);
  });

  it("holds a column at its floor rather than letting it vanish", async () => {
    // A column dragged to nothing takes its own grip off the screen with it,
    // and there is no way back.
    const wrapper = await mountShelf([adapter()]);
    await drag(wrapper, gripOf(wrapper, "size"), 0, 400);
    expect(useModelShelfStore().view.columnWidths.size).toBe(56);
  });

  it("stops tracking once the pointer is released", async () => {
    const wrapper = await mountShelf([adapter()]);
    const store = useModelShelfStore();
    const grip = gripOf(wrapper, "kind");
    await drag(wrapper, grip, 120, 100);
    expect(store.view.columnWidths.kind).toBe(84);
    await fire(wrapper, grip, "pointermove", 0, 0);
    expect(store.view.columnWidths.kind).toBe(84);
  });

  it("gives up a drag whose release it never heard, rather than sticking", async () => {
    // Pointer capture normally guarantees the pointerup, but a refused or lost
    // capture would otherwise leave the drag live for good: the next hover over
    // any grip would resume it from the original press and jump the column.
    const wrapper = await mountShelf([adapter()]);
    const store = useModelShelfStore();
    const grip = gripOf(wrapper, "base");

    await fire(wrapper, grip, "pointerdown", 220);
    await fire(wrapper, grip, "pointermove", 200);
    expect(store.view.columnWidths.base).toBe(160);

    // The release happened somewhere else. The next move arrives with nothing
    // held down, and that is the drag over.
    await fire(wrapper, grip, "pointermove", 160, 0);
    expect(store.view.columnWidths.base).toBe(160);
    await fire(wrapper, grip, "pointermove", 20, 0);
    expect(store.view.columnWidths.base).toBe(160);
  });

  it("refuses a secondary button on any device, a pen's barrel included", async () => {
    const wrapper = await mountShelf([adapter()]);
    const store = useModelShelfStore();
    const grip = gripOf(wrapper, "kind");
    grip.element.dispatchEvent(
      new MouseEvent("pointerdown", { clientX: 100, button: 2, bubbles: true }),
    );
    await wrapper.vm.$nextTick();
    await fire(wrapper, grip, "pointermove", 200);
    expect(store.view.columnWidths.kind).toBe(64);
  });

  it("moves the edge from the keyboard, which is why the grip is focusable", async () => {
    const wrapper = await mountShelf([adapter()]);
    const store = useModelShelfStore();
    const grip = gripOf(wrapper, "kind");
    expect(grip.attributes("role")).toBe("separator");
    expect(grip.attributes("tabindex")).toBe("0");
    expect(grip.attributes("aria-valuenow")).toBe("64");

    // The keys move the separator, so Left widens - the same mapping the
    // pointer has, because the grip is the column's left edge.
    await grip.trigger("keydown", { key: "ArrowLeft" });
    expect(store.view.columnWidths.kind).toBe(72);
    // 64 rather than 56: two steps down from 72 is 56, and `Kind`'s own floor
    // holds it at 64.
    await grip.trigger("keydown", { key: "ArrowRight" });
    await grip.trigger("keydown", { key: "ArrowRight" });
    expect(store.view.columnWidths.kind).toBe(64);
    expect(gripOf(wrapper, "kind").attributes("aria-valuenow")).toBe("64");
    expect(gripOf(wrapper, "kind").attributes("aria-valuetext")).toBe(
      "64 pixels",
    );
    expect(gripOf(wrapper, "kind").attributes("aria-valuemin")).toBe("64");
  });

  it("takes Home and End to the two ends", async () => {
    const wrapper = await mountShelf([adapter()]);
    const store = useModelShelfStore();
    const grip = gripOf(wrapper, "base");
    // Home is the separator as far left as it goes, which is the column at its
    // widest - the store's sanity ceiling here, because jsdom lays nothing out
    // and an unmeasured Name track means unlimited.
    await grip.trigger("keydown", { key: "Home" });
    expect(store.view.columnWidths.base).toBe(400);
    await grip.trigger("keydown", { key: "End" });
    expect(store.view.columnWidths.base).toBe(72);
  });

  it("puts a column back, which is the only way out of a mis-drag", async () => {
    // A width is remembered for good once it is dragged, so without a reset
    // the reader's only recovery is clearing the browser's storage.
    const wrapper = await mountShelf([adapter()]);
    const store = useModelShelfStore();
    const grip = gripOf(wrapper, "size");

    await drag(wrapper, grip, 500, 400);
    expect(store.view.columnWidths.size).toBe(174);
    await grip.trigger("keydown", { key: "Enter" });
    expect(store.view.columnWidths.size).toBe(74);

    await drag(wrapper, grip, 500, 400);
    await grip.trigger("dblclick");
    expect(store.view.columnWidths.size).toBe(74);
    expect(
      JSON.parse(window.localStorage.getItem("pixlstash:modelShelfView"))
        .columnWidths.size,
    ).toBe(74);
  });

  it("writes the blob once the drag ends, not on every frame of it", async () => {
    // `rememberView` rebuilds the whole blob and does a synchronous setItem;
    // at one per pointermove that is ~120 main-thread writes a second.
    const wrapper = await mountShelf([adapter()]);
    const grip = gripOf(wrapper, "base");
    const write = vi.spyOn(Storage.prototype, "setItem");

    await fire(wrapper, grip, "pointerdown", 230);
    await fire(wrapper, grip, "pointermove", 220);
    await fire(wrapper, grip, "pointermove", 210);
    await fire(wrapper, grip, "pointermove", 200);
    expect(useModelShelfStore().view.columnWidths.base).toBe(170);
    expect(write).not.toHaveBeenCalled();

    await fire(wrapper, grip, "pointerup", 200, 0);
    expect(write).toHaveBeenCalledTimes(1);
    expect(
      JSON.parse(window.localStorage.getItem("pixlstash:modelShelfView"))
        .columnWidths.base,
    ).toBe(170);
    write.mockRestore();
  });
});

describe("selecting rows", () => {
  const rowAt = (wrapper, i) => wrapper.findAll(".shelf-row")[i];

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

  it("takes every shown model on Ctrl+A, and the browser's select-all with it", async () => {
    // The bug was never that the chord did nothing: unclaimed it reached the
    // browser's own select-all, which - `.shelf` being `user-select: none` -
    // highlighted every part of the app EXCEPT the rows it was aimed at. Both
    // halves are asserted, because the selection can be right with that
    // highlight still there. The press starts with NOTHING selected, which is
    // the state `shelfOwnsTheKey` used to refuse outright.
    const wrapper = await mountShelf([
      adapter({ id: 1 }),
      adapter({ id: 2 }),
      adapter({ id: 3 }),
    ]);
    // Attached, because this is a window listener exactly as Escape's is.
    document.body.appendChild(wrapper.element);
    const store = useModelShelfStore();
    const press = new KeyboardEvent("keydown", {
      key: "a",
      ctrlKey: true,
      bubbles: true,
      cancelable: true,
    });
    rowAt(wrapper, 0).element.dispatchEvent(press);
    await wrapper.vm.$nextTick();

    expect([...store.selectedIds].sort()).toEqual([1, 2, 3]);
    expect(press.defaultPrevented).toBe(true);
    wrapper.unmount();
  });

  it("leaves Ctrl+A to a field being typed in", async () => {
    // Select-all inside a text field is the field's. Asserted from a field
    // OUTSIDE the shelf - the sidebar's search box, the app bar - because that
    // is the only place `isTypingTarget` is what declines: the shelf's own
    // rename field stops the event (`onRenameKeydown`) and the base-model field
    // carries `@keydown.stop`, so neither ever reaches this window listener,
    // and a press aimed at one of them would pass with the guard deleted.
    const wrapper = await mountShelf([adapter({ id: 1 }), adapter({ id: 2 })]);
    document.body.appendChild(wrapper.element);
    const field = document.createElement("input");
    document.body.appendChild(field);
    const store = useModelShelfStore();
    const press = new KeyboardEvent("keydown", {
      key: "a",
      ctrlKey: true,
      bubbles: true,
      cancelable: true,
    });
    field.dispatchEvent(press);
    await wrapper.vm.$nextTick();

    expect(store.selectedRows).toHaveLength(0);
    expect(press.defaultPrevented).toBe(false);
    field.remove();
    wrapper.unmount();
  });

  it("swallows Ctrl+A with nothing drawn, and keeps the selection", async () => {
    // `selectedIds` is pruned against a FETCH and never against the `Show`
    // narrowing, so a selection outlives a narrowing that empties the list.
    // `selectVisible()` there would replace it with an empty set - a silent
    // clear from a key that says "select", with the pill gone (it is gated on
    // `selectedRows`) so nothing on screen could have done it on purpose. The
    // press is still claimed, or it falls back to the native select-all.
    const wrapper = await mountShelf([adapter({ id: 1 }), adapter({ id: 2 })]);
    document.body.appendChild(wrapper.element);
    const store = useModelShelfStore();
    await rowAt(wrapper, 0).trigger("click");
    await rowAt(wrapper, 1).trigger("click", { ctrlKey: true });
    store.setFilters({ adapters: false });
    // Hand-made sets exist, but this is the ROW LIST: the set branch of Ctrl+A
    // belongs to the grid and must not take them (and clear the models) here.
    store.workflowSets = {
      ...store.workflowSets,
      handMade: [{ id: 10, name: "Kit", members: [] }],
    };
    await wrapper.vm.$nextTick();
    expect(store.visibleRows).toHaveLength(0);

    const press = new KeyboardEvent("keydown", {
      key: "a",
      ctrlKey: true,
      bubbles: true,
      cancelable: true,
    });
    window.dispatchEvent(press);
    await wrapper.vm.$nextTick();

    expect([...store.selectedIds].sort()).toEqual([1, 2]);
    expect(press.defaultPrevented).toBe(true);
    wrapper.unmount();
  });

  it("answers to Cmd+A, and to no other chord on the A key", async () => {
    // Meta is Ctrl on a Mac. Alt is what AltGr sends on a European layout, and
    // Ctrl+Shift+A is a chord this list does not define: both are the browser's.
    const wrapper = await mountShelf([adapter({ id: 1 }), adapter({ id: 2 })]);
    document.body.appendChild(wrapper.element);
    const store = useModelShelfStore();
    const press = (init) =>
      new KeyboardEvent("keydown", {
        key: "a",
        bubbles: true,
        cancelable: true,
        ...init,
      });

    for (const declined of [
      press({ ctrlKey: true, altKey: true }),
      press({ ctrlKey: true, shiftKey: true }),
      press({ ctrlKey: true, repeat: true }),
    ]) {
      window.dispatchEvent(declined);
      await wrapper.vm.$nextTick();
      expect(store.selectedRows).toHaveLength(0);
      expect(declined.defaultPrevented).toBe(false);
    }

    const cmd = press({ metaKey: true });
    window.dispatchEvent(cmd);
    await wrapper.vm.$nextTick();
    expect([...store.selectedIds].sort()).toEqual([1, 2]);
    expect(cmd.defaultPrevented).toBe(true);
    wrapper.unmount();
  });

  it("does not claim Ctrl+A from the runs tab", async () => {
    // The shelf stays mounted behind the runs panel, so its listener would
    // select rows nobody can see. This is about the SHELF's listener declining;
    // the runs grid owns its own keyboard model and its own selection.
    const wrapper = await mountShelf([adapter({ id: 1 }), adapter({ id: 2 })]);
    document.body.appendChild(wrapper.element);
    const store = useModelShelfStore();
    nav.route.name = "models-runs";
    await wrapper.vm.$nextTick();

    const press = new KeyboardEvent("keydown", {
      key: "a",
      ctrlKey: true,
      bubbles: true,
      cancelable: true,
    });
    window.dispatchEvent(press);
    await wrapper.vm.$nextTick();

    expect(store.selectedRows).toHaveLength(0);
    expect(press.defaultPrevented).toBe(false);
    wrapper.unmount();
  });

  it("shows no selection bar until something is selected", async () => {
    const wrapper = await mountShelf([adapter()]);
    expect(wrapper.find(".shelf-selbar").exists()).toBe(false);

    await rowAt(wrapper, 0).trigger("click");
    await wrapper.vm.$nextTick();
    expect(tip(wrapper.find(".selbar-count"))).toContain("1 model selected");
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

describe("a click on the list's background", () => {
  it("clears the selection, and a click on a row does not", async () => {
    const wrapper = await mountShelf([adapter({ id: 7 }), adapter({ id: 8 })]);
    const store = useModelShelfStore();
    await wrapper.findAll(".shelf-row")[0].trigger("click");
    expect(store.selectedRows).toHaveLength(1);

    // Pressed on a row and released on another (or a text drag out of the
    // rename field): the click lands on the list, and must not clear.
    await wrapper.findAll(".shelf-row")[0].trigger("pointerdown");
    await mouseClick(wrapper.find(".shelf-list"));
    expect(store.selectedRows).toHaveLength(1);

    // A right-press ends without a click; a keyboard click (detail 0) after a
    // left one does not count either.
    await rightPress(wrapper.find(".shelf-list"));
    await mouseClick(wrapper.find(".shelf-list"));
    expect(store.selectedRows).toHaveLength(1);
    await wrapper.find(".shelf-list").trigger("pointerdown");
    await wrapper.find(".shelf-list").trigger("click");
    expect(store.selectedRows).toHaveLength(1);

    await wrapper.find(".shelf-list").trigger("pointerdown");
    await mouseClick(wrapper.find(".shelf-list"));
    expect(store.selectedRows).toHaveLength(0);

    await wrapper.findAll(".shelf-row")[0].trigger("click");
    await wrapper.find(".shelf-group").trigger("pointerdown");
    await mouseClick(wrapper.find(".shelf-group"));
    expect(store.selectedRows).toHaveLength(0);

    await wrapper.findAll(".shelf-row")[0].trigger("click");
    await wrapper.find(".shelf-scroll").trigger("pointerdown");
    await mouseClick(wrapper.find(".shelf-scroll"));
    expect(store.selectedRows).toHaveLength(0);
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
    // control, unlike the listbox option this used to be - but the row is
    // still the disclosure, and a focusable second way to open the same run is
    // the dialect this list stopped speaking.
    const wrapper = await mountShelf(run());
    const row = wrapper.find(".shelf-row");
    expect(row.exists()).toBe(true);
    // Anything FOCUSABLE, not just the obvious tags: a `<span role="button"
    // tabindex="0">` is exactly as much of a second control, and a
    // tag-name-only assertion let that mutant through when this was checked.
    // `[tabindex]` is on the row itself, so the search is for descendants.
    // The count badge is the one disclosure control in a row, and it is a real
    // button on purpose: the grid role permits one where the old listbox did
    // not, and a pointer needs a target for the disclosure that Right/Left
    // gives the keyboard. The Kind menu (#1962) sets a value, not the
    // disclosure; everything else in the row is still inert.
    const controls = row
      .findAll(
        'button, a[href], input, select, textarea, [tabindex], [role="button"]',
      )
      .filter(
        (el) =>
          el.attributes("data-testid") !== "kind-menu" &&
          // The cover's heart (#1578) is a control of its own, on its own row.
          el.attributes("data-testid") !== "favourite-heart",
      );
    expect(controls).toHaveLength(1);
    expect(controls[0].classes()).toContain("shelf-stack-badge");
    // ...and it does not pick the row. The badge sits inside the one thing a
    // click on a row already does, so without `@click.stop` opening a run
    // would also select it - which is the gesture a reader uses to LOOK before
    // deciding whether to act.
    await controls[0].trigger("click");
    expect(useModelShelfStore().selectedRows).toHaveLength(0);
    expect(wrapper.findAll(".shelf-row--member")).toHaveLength(1);
  });

  it("labels a member by version when the stack spans versions", async () => {
    // A stack is a subject now, not one training run: `Foxglove` and
    // `Foxglove_v2` sit behind one row. Neither carries a step, so the old
    // label called both of them "Final" - which is the one place the reader
    // looks to tell a stack's members apart, saying nothing.
    const wrapper = await mountShelf([
      adapter({
        id: 1,
        stack_id: 8,
        stack_position: 0,
        filename: "Foxglove_v2.safetensors",
      }),
      adapter({
        id: 2,
        stack_id: 8,
        stack_position: 1,
        sha256: "c".repeat(64),
        filename: "Foxglove.safetensors",
      }),
    ]);
    await wrapper.find(".shelf-row").trigger("keydown", { key: "ArrowRight" });
    // The exact string, not `toContain("v1")`: the version LEADS the label, and
    // "Final · v1" would satisfy a containment check while reading backwards.
    expect(textOf(wrapper.find(".shelf-row--member .shelf-row-name"))).toBe(
      "v1 · Final",
    );
  });

  it("treats v2 and V2.0 as ONE version, as the server does", async () => {
    // The parity case. Comparing raw tokens here would count two versions and
    // prefix every member of what is really a single-version run - the noise
    // the label exists to avoid, in the case the guard was written for.
    const wrapper = await mountShelf([
      adapter({
        id: 1,
        stack_id: 11,
        stack_position: 0,
        filename: "Foxglove_v2.safetensors",
      }),
      adapter({
        id: 2,
        stack_id: 11,
        stack_position: 1,
        sha256: "e".repeat(64),
        filename: "Foxglove_V2.0_000000500.safetensors",
      }),
    ]);
    await wrapper.find(".shelf-row").trigger("keydown", { key: "ArrowRight" });
    expect(textOf(wrapper.find(".shelf-row--member .shelf-row-name"))).toBe(
      "Step 500",
    );
  });

  it("leaves a one-version run labelled by its step alone", async () => {
    // The positive control: repeating `v2` on every member of a run that is
    // entirely v2 is the "shares a name by construction" noise the label exists
    // to avoid, and the run's own row already says it.
    const wrapper = await mountShelf([
      adapter({
        id: 1,
        stack_id: 9,
        stack_position: 0,
        filename: "Foxglove_v2.safetensors",
      }),
      adapter({
        id: 2,
        stack_id: 9,
        stack_position: 1,
        sha256: "d".repeat(64),
        filename: "Foxglove_v2_000000500.safetensors",
      }),
    ]);
    await wrapper.find(".shelf-row").trigger("keydown", { key: "ArrowRight" });
    expect(textOf(wrapper.find(".shelf-row--member .shelf-row-name"))).toBe(
      "Step 500",
    );
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

});

describe("the date column", () => {
  // The naive-UTC stamp the backend serves, built from LOCAL noon: the shelf
  // renders a naive stamp in the reader's local time, so a hardcoded literal
  // would name a different day on a runner far enough east or west. Every date
  // in this block is built this way, and names the day it is written as on
  // every clock.
  const naive = (year, month, day) =>
    new Date(year, month - 1, day, 12, 0).toISOString().slice(0, 19);
  const ADDED = naive(2026, 7, 30);
  const headOf = (wrapper) =>
    wrapper.find(".shelf-head-col--date .shelf-head-cell");

  it("heads itself with the axis it is drawn in, and sorts on that", async () => {
    // The one heading in the strip that renames itself: there are two date
    // sort keys and one column, so the label and the key it presses move
    // together - otherwise the heading would name an axis the cells under it
    // are not showing.
    const wrapper = await mountShelf([adapter({ added_at: ADDED })]);
    const store = useModelShelfStore();
    expect(headOf(wrapper).text()).toBe("Date added");

    // Already the sorted key, so pressing it flips the direction rather than
    // re-picking it.
    expect(store.view.sortKey).toBe("added_at");
    await headOf(wrapper).trigger("click");
    expect(store.view.sortKey).toBe("added_at");
    expect(store.view.sortDirection).toBe("asc");

    // The Sort panel is the only way onto the other axis. Arriving there
    // renames this column rather than adding one, and the heading then sorts
    // on the key it has become.
    store.setView({ sortKey: "file_mtime" });
    await wrapper.vm.$nextTick();
    expect(headOf(wrapper).text()).toBe("File date");
    await headOf(wrapper).trigger("click");
    expect(store.view.sortKey).toBe("file_mtime");
    expect(store.view.sortDirection).toBe("asc");

    // And it is a resizable column like the other three.
    expect(
      wrapper.find(".shelf-head-col--date .shelf-head-grip").exists(),
    ).toBe(true);
    store.setColumnWidth("date", 120);
    await wrapper.vm.$nextTick();
    expect(
      wrapper.find(".shelf").element.style.getPropertyValue("--shelf-col-date"),
    ).toBe("120px");
  });

  it("stamps the day in the reader's own date format", async () => {
    // The DAY, not the stamp: a column is scanned, and the clock is a third of
    // the width of the default format. The full stamp stays reachable in the
    // title. Proven under two settings, because a format that is ignored looks
    // exactly like one that happens to match.
    const prefs = useUserPrefsStore();
    prefs.dateFormat = "eu";
    let wrapper = await mountShelf([adapter({ added_at: ADDED })]);
    let cell = wrapper.find(".shelf-col--date");
    expect(cell.text()).toBe("30/07/2026");
    expect(tip(cell)).toBe(`Date added: ${formatUserDate(ADDED, "eu")}`);

    prefs.dateFormat = "iso";
    wrapper = await mountShelf([adapter({ added_at: ADDED })]);
    expect(wrapper.find(".shelf-col--date").text()).toBe("2026-07-30");
  });

  it("follows the sort onto the file-date axis", async () => {
    // A column that always showed `added_at` would read as unordered the
    // moment the shelf was sorted on the other date. `newest_file_mtime` is
    // `st_mtime_ns`, so the ns→ms divide is what this proves.
    const prefs = useUserPrefsStore();
    prefs.dateFormat = "iso";
    const wrapper = await mountShelf([
      adapter({
        added_at: ADDED,
        newest_file_mtime: new Date(2024, 0, 9, 12, 0).getTime() * 1e6,
      }),
    ]);
    expect(wrapper.find(".shelf-col--date").text()).toBe("2026-07-30");

    useModelShelfStore().setView({ sortKey: "file_mtime" });
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".shelf-col--date").text()).toBe("2024-01-09");
    // And the header says which of the two dates a reader is hearing.
    const heads = wrapper.findAll('[role="row"].visually-hidden');
    // Sets is the last heading now, so the date heading is the one before it.
    expect(heads[0].findAll('[role="columnheader"]').at(-2).text()).toBe(
      "File date",
    );
  });

  it("gives a stack the run's date and each step its own", async () => {
    // A stack's date is its newest member's, never its cover's - the same rule
    // the sort orders on. The steps under it differ from each other, which is
    // the one field on a member row that is not simply the run's.
    const prefs = useUserPrefsStore();
    prefs.dateFormat = "iso";
    const wrapper = await mountShelf([
      adapter({
        id: 1,
        stack_id: 7,
        stack_position: 0,
        added_at: naive(2026, 1, 1),
        newest_member_at: naive(2026, 3, 4),
      }),
      // `newest_member_at` is a JOIN in the list query, so every member row
      // carries the stack's - which is exactly why a member has to be read for
      // its own date rather than for the first one it can answer with.
      adapter({
        id: 2,
        sha256: "b".repeat(64),
        stack_id: 7,
        stack_position: 1,
        added_at: naive(2026, 2, 2),
        newest_member_at: naive(2026, 3, 4),
      }),
    ]);
    expect(wrapper.find(".shelf-col--date").text()).toBe("2026-03-04");

    await wrapper.find(".shelf-stack-badge").trigger("click");
    const member = wrapper.find(".shelf-row--member");
    expect(member.find(".shelf-col--date").text()).toBe("2026-02-02");
  });

  it("gives cover and step their own file dates on the file-date axis", async () => {
    // `newest_file_mtime` is grouped per MODEL, not per stack, so on this axis
    // there is nothing for the cover to inherit and nothing for a member to be
    // shielded from: each answers for itself, which is what the sort orders
    // each of them on. Pinned because the two aggregates are shaped
    // differently - if `newest_file_mtime` ever became a stack aggregate like
    // its sibling, every step would start printing the run's date and only
    // this assertion would notice.
    const prefs = useUserPrefsStore();
    prefs.dateFormat = "iso";
    const wrapper = await mountShelf([
      adapter({
        id: 1,
        stack_id: 7,
        stack_position: 0,
        newest_file_mtime: new Date(2025, 4, 6, 12, 0).getTime() * 1e6,
      }),
      adapter({
        id: 2,
        sha256: "b".repeat(64),
        stack_id: 7,
        stack_position: 1,
        newest_file_mtime: new Date(2025, 6, 8, 12, 0).getTime() * 1e6,
      }),
    ]);
    useModelShelfStore().setView({ sortKey: "file_mtime" });
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".shelf-col--date").text()).toBe("2025-05-06");

    await wrapper.find(".shelf-stack-badge").trigger("click");
    expect(
      wrapper.find(".shelf-row--member").find(".shelf-col--date").text(),
    ).toBe("2025-07-08");
  });
});

describe("the thumbnail verb", () => {
  it("marks every selected model with the one picture", async () => {
    const bytes = new Blob(["webp"], { type: "image/webp" });
    getPictureThumbnailBlob.mockResolvedValue(bytes);
    setModelIcon.mockResolvedValue({
      model_id: 1,
      icon_sha256: "c".repeat(64),
    });
    const wrapper = await mountShelf([
      adapter({ id: 1 }),
      adapter({ id: 2, sha256: "b".repeat(64) }),
    ]);
    const store = useModelShelfStore();
    store.toggleSelected(1);
    store.toggleSelected(2);
    await wrapper.vm.$nextTick();

    await wrapper
      .findComponent({ name: "PicturePicker" })
      .vm.$emit("pick", { id: 55 });
    await new Promise((r) => setTimeout(r, 0));

    // One read of the picture, one write per model: the icon store is
    // content-addressed, so the repeated bytes collapse to one file.
    expect(getPictureThumbnailBlob).toHaveBeenCalledTimes(1);
    expect(setModelIcon).toHaveBeenCalledWith(1, bytes);
    expect(setModelIcon).toHaveBeenCalledWith(2, bytes);
  });

  it("asks before a bulk set overwrites marks, and opens nothing on no", async () => {
    // The images survive in the content-addressed store, but which model wore
    // which does not - the same test the bulk clear falls on.
    const wrapper = await mountShelf([
      adapter({ id: 1, icon_sha256: "a".repeat(64) }),
      adapter({ id: 2, sha256: "b".repeat(64) }),
    ]);
    const store = useModelShelfStore();
    store.toggleSelected(1);
    store.toggleSelected(2);
    await wrapper.vm.$nextTick();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

    await wrapper.find('[data-verb="set-icon"]').trigger("click");
    await new Promise((r) => setTimeout(r, 0));
    // `useConfirm` has no host mounted and falls back to `window.confirm`,
    // which shows the MESSAGE and nothing else - so the counts have to be
    // there, not in the title the fallback throws away.
    expect(confirmSpy.mock.calls[0][0]).toContain("All 2 selected models");
    expect(confirmSpy.mock.calls[0][0]).toContain("the 1 that already");
    expect(wrapper.findComponent({ name: "PicturePicker" }).props("open")).toBe(
      false,
    );

    // And the other direction: saying yes opens the picker. Over-blocking is
    // its own regression.
    confirmSpy.mockReturnValue(true);
    await wrapper.find('[data-verb="set-icon"]').trigger("click");
    await new Promise((r) => setTimeout(r, 0));
    expect(wrapper.findComponent({ name: "PicturePicker" }).props("open")).toBe(
      true,
    );
    confirmSpy.mockRestore();
  });

  it("does not ask when the selection has no marks to overwrite", async () => {
    const wrapper = await mountShelf([
      adapter({ id: 1 }),
      adapter({ id: 2, sha256: "b".repeat(64) }),
    ]);
    const store = useModelShelfStore();
    store.toggleSelected(1);
    store.toggleSelected(2);
    await wrapper.vm.$nextTick();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

    await wrapper.find('[data-verb="set-icon"]').trigger("click");
    await new Promise((r) => setTimeout(r, 0));
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(wrapper.findComponent({ name: "PicturePicker" }).props("open")).toBe(
      true,
    );
    confirmSpy.mockRestore();
  });

  it("marks every model in a ticked run, not just its cover", async () => {
    // A fully-selected stack is ONE row whose id is the cover's. Iterating
    // rows would silently skip the other two.
    const bytes = new Blob(["webp"], { type: "image/webp" });
    getPictureThumbnailBlob.mockResolvedValue(bytes);
    setModelIcon.mockResolvedValue({
      model_id: 1,
      icon_sha256: "e".repeat(64),
    });
    const wrapper = await mountShelf([
      adapter({ id: 1, stack_id: 9, stack_position: 0 }),
      adapter({
        id: 2,
        sha256: "b".repeat(64),
        stack_id: 9,
        stack_position: 1,
      }),
      adapter({
        id: 3,
        sha256: "c".repeat(64),
        stack_id: 9,
        stack_position: 2,
      }),
    ]);
    const store = useModelShelfStore();
    store.selectVisible();
    await wrapper.vm.$nextTick();
    expect(store.selectedRows).toHaveLength(1);

    await wrapper
      .findComponent({ name: "PicturePicker" })
      .vm.$emit("pick", { id: 55 });
    await new Promise((r) => setTimeout(r, 0));

    expect(setModelIcon.mock.calls.map((c) => c[0]).sort()).toEqual([1, 2, 3]);
  });

  it("offers Clear thumbnail only when something has one", async () => {
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    const store = useModelShelfStore();
    store.toggleSelected(1);
    await wrapper.vm.$nextTick();
    const clear = () =>
      wrapper
        .findAll("button")
        .find((b) => b.text().includes("Clear thumbnail"));
    expect(clear()).toBeUndefined();

    store.rows = [adapter({ id: 1, icon_sha256: "a".repeat(64) })];
    await wrapper.vm.$nextTick();
    expect(clear()).toBeDefined();
  });

  it("sends the chosen picture's BYTES, never a reference to it", async () => {
    // The icon store is content-addressed and lives beside the hub, while a
    // picture is a vault row - nothing keys across the two. So the library
    // route has to copy pixels, and the thumbnail is the copy it sends.
    const bytes = new Blob(["webp"], { type: "image/webp" });
    getPictureThumbnailBlob.mockResolvedValue(bytes);
    setModelIcon.mockResolvedValue({
      model_id: 1,
      icon_sha256: "c".repeat(64),
    });
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    const store = useModelShelfStore();
    store.toggleSelected(1);
    await wrapper.vm.$nextTick();

    await wrapper.findComponent({ name: "PicturePicker" }).vm.$emit("pick", {
      id: 55,
    });
    await new Promise((r) => setTimeout(r, 0));

    // Cache-busted: these bytes get STORED, so an hour-old thumbnail is the
    // wrong thing to keep.
    expect(getPictureThumbnailBlob).toHaveBeenCalledWith(55, {
      cacheBuster: expect.any(Number),
    });
    expect(setModelIcon).toHaveBeenCalledWith(1, bytes);
  });

  it("keeps Escape and Delete away from the rows while the picker is up", async () => {
    // The picker is one of the shelf's OWN dialogs, and the window-level guard
    // lists those by ref rather than by target: a press with nothing focused
    // inside a dialog targets `<body>`, which no ancestor test can see. Without
    // the ref, Escape over the picker would also wipe the selection underneath
    // it and Delete would open the delete confirmation for it.
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    const store = useModelShelfStore();
    store.toggleSelected(1);
    await wrapper.vm.$nextTick();

    wrapper.findComponent({ name: "PicturePicker" }).vm.$emit("close");
    await wrapper.vm.$nextTick();
    // Sanity: with the picker shut, Escape is the shelf's and clears.
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await wrapper.vm.$nextTick();
    expect(store.selectedRows).toHaveLength(0);

    store.toggleSelected(1);
    await wrapper.vm.$nextTick();
    await wrapper.find('[data-verb="set-icon"]').trigger("click");
    await wrapper.vm.$nextTick();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await wrapper.vm.$nextTick();
    expect(store.selectedRows).toHaveLength(1);
  });

  it("keeps the picker open behind the file chooser, and shuts it on a file", async () => {
    // `Choose a file…` is the shipped route this change deliberately does not
    // remove, and it lives in the picker's footer slot. Cancelling the OS
    // chooser must land the reader back in the picker rather than on a bare
    // shelf, so the picker is closed by a file ARRIVING, never by the chooser
    // opening.
    setModelIcon.mockResolvedValue({
      model_id: 1,
      icon_sha256: "d".repeat(64),
    });
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    const store = useModelShelfStore();
    store.toggleSelected(1);
    await wrapper.vm.$nextTick();
    await wrapper.find('[data-verb="set-icon"]').trigger("click");
    await wrapper.vm.$nextTick();

    const picker = () => wrapper.findComponent({ name: "PicturePicker" });
    expect(picker().props("open")).toBe(true);
    const chooseAFile = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Choose a file"));
    expect(chooseAFile).toBeDefined();

    const input = wrapper.find('input[type="file"]');
    const clicked = vi.spyOn(input.element, "click");
    await chooseAFile.trigger("click");
    expect(clicked).toHaveBeenCalled();
    // Still open: nothing has come back from the chooser yet.
    expect(picker().props("open")).toBe(true);

    const file = new File(["png"], "mark.png", { type: "image/png" });
    Object.defineProperty(input.element, "files", { value: [file] });
    await input.trigger("change");
    await new Promise((r) => setTimeout(r, 0));
    expect(picker().props("open")).toBe(false);
    expect(setModelIcon).toHaveBeenCalledWith(1, file);
  });

  it("keeps the picker open when the picture's bytes cannot be read", async () => {
    // A thumbnail is generated FROM the file, so an unplugged drive 404s. The
    // refusal must not land over a shelf the reader has to reopen the picker
    // from to try again.
    getPictureThumbnailBlob.mockRejectedValue(new Error("nope"));
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    const store = useModelShelfStore();
    store.toggleSelected(1);
    await wrapper.vm.$nextTick();
    await wrapper.find('[data-verb="set-icon"]').trigger("click");
    await wrapper.vm.$nextTick();

    const picker = () => wrapper.findComponent({ name: "PicturePicker" });
    picker().vm.$emit("pick", { id: 55 });
    await new Promise((r) => setTimeout(r, 0));

    expect(picker().props("open")).toBe(true);
    expect(setModelIcon).not.toHaveBeenCalled();
    expect(useNoticeStore().notices.at(-1).level).toBe("error");
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
    // nothing is written until the confirmation comes back.
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
    // `fuse: false` explicitly, because nothing selected is stacked. The flag
    // is not cosmetic - with it on, this call would absorb whole stacks - so
    // the default path has to be pinned rather than left to a truthiness test.
    expect(createStack).toHaveBeenCalledWith([1, 2], null, { fuse: false });
    confirmSpy.mockRestore();
  });

  it("fuses instead when something selected is already stacked", async () => {
    // Stacking two stacks. Every member of both is selected, because the shelf
    // selects a collapsed row atomically, and `fuse` is what lets the route
    // take rows out of the stacks they are in and absorb those stacks whole.
    const stacked = (id, sha, stackId, position) =>
      adapter({
        id,
        sha256: sha.repeat(64),
        stack_id: stackId,
        stack_position: position,
        locations: [
          {
            state: "present",
            folder_id: 1,
            folder_path: "/m",
            relpath: `${id}`,
          },
        ],
      });
    const wrapper = await mountShelf([
      stacked(1, "a", 7, 0),
      stacked(2, "b", 7, 1),
      stacked(3, "c", 8, 0),
      stacked(4, "d", 8, 1),
    ]);
    const store = useModelShelfStore();
    // Picked the way the list picks: a collapsed row takes its whole run, and
    // Ctrl keeps the first one while the second is added. Ticking the cover's
    // id alone is a different gesture now - it means that one file.
    store.selectFromClick(1, { ctrl: true });
    store.selectFromClick(3, { ctrl: true });
    await wrapper.vm.$nextTick();

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    createStack.mockResolvedValue({ stack_id: 9, member_count: 4 });
    await wrapper
      .findAll("button")
      .find((b) => b.text().includes("Stack these"))
      .trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(createStack).toHaveBeenCalledWith(
      expect.arrayContaining([1, 2, 3, 4]),
      null,
      { fuse: true },
    );
    confirmSpy.mockRestore();
  });

  it("ungroups the selected stacks, one call each", async () => {
    const stacked = (id, sha, stackId, position) =>
      adapter({
        id,
        sha256: sha.repeat(64),
        stack_id: stackId,
        stack_position: position,
        locations: [
          {
            state: "present",
            folder_id: 1,
            folder_path: "/m",
            relpath: `${id}`,
          },
        ],
      });
    const wrapper = await mountShelf([
      stacked(1, "a", 7, 0),
      stacked(2, "b", 7, 1),
      stacked(3, "c", 8, 0),
      stacked(4, "d", 8, 1),
    ]);
    const store = useModelShelfStore();
    // Picked the way the list picks: a collapsed row takes its whole run, and
    // Ctrl keeps the first one while the second is added. Ticking the cover's
    // id alone is a different gesture now - it means that one file.
    store.selectFromClick(1, { ctrl: true });
    store.selectFromClick(3, { ctrl: true });
    await wrapper.vm.$nextTick();

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const ungroup = () =>
      wrapper.findAll("button").find((b) => b.text().includes("Ungroup"));
    await ungroup().trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    // Confirmed first: it is a structural edit, even though no byte moves.
    expect(unstackStack).not.toHaveBeenCalled();

    confirmSpy.mockReturnValue(true);
    unstackStack.mockResolvedValue({ released: 2 });
    await ungroup().trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));

    // One call per STACK, not per selected row: the two selected rows cover
    // two stacks, and a stack is what the verb acts on.
    expect(unstackStack).toHaveBeenCalledTimes(2);
    expect(unstackStack.mock.calls.map((c) => c[0]).sort()).toEqual([7, 8]);
    confirmSpy.mockRestore();
  });
});


describe("Escape", () => {
  // The key is handled on the WINDOW, so every assertion here needs a real
  // event path out of the shelf - a detached wrapper's keydown bubbles into
  // nothing and would pass or fail for the wrong reason.
  async function mountAttachedShelf() {
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    document.body.appendChild(wrapper.element);
    return wrapper;
  }

  it("clears the selection from anywhere in the shelf, not only from a row", async () => {
    // It used to be handled on the row, so it only worked while a row held the
    // roving tab stop - not after a click moved focus, and not from the
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
    // app bar, or anything else after picking rows and Escape did nothing -
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
    wrapper.vm.addFileOpen = true;
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
    // is still mounted underneath it - and a selection nobody can see must not
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

describe("the trained label", () => {
  it("marks a row from an ai-toolkit run, and nothing found on disk", async () => {
    // The tag keys on `training_run_id`, the run the row came from.
    const wrapper = await mountShelf([
      adapter({ id: 611, display_name: "Ours", training_run_id: 4 }),
      adapter({ id: 612, display_name: "Theirs", training_run_id: null }),
    ]);
    const marked = wrapper
      .findAll(".shelf-chip--trained")
      .map((chip) => chip.text());
    expect(marked).toEqual(["Trained"]);
  });
});

describe("the training step", () => {
  it("shows the step on a row whose name had it stripped", async () => {
    // `deriveModelName` drops the trailing step on the stated grounds that it
    // "is parsed into its own field" - and nothing rendered that field outside
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
    // spaced - the point is that the step is no longer lost from it.
    expect(wrapper.get(".shelf-row-name").text()).toContain(
      "clementine zib 3b",
    );
    // `toLocaleString()` rather than a written-out "2,500": the separator
    // follows the runtime locale, so hard-coding it fails on a machine that
    // groups differently while the component is behaving correctly. Scoped to
    // the element for the same reason the negatives below are - "Step" also
    // appears in the shelf's own help paragraph.
    expect(wrapper.get(".shelf-chip--step").text()).toBe(
      `Step ${(2500).toLocaleString()}`,
    );
  });

  it("says nothing when the file records no step", async () => {
    // A hand-made adapter has no step, and "Step - " would invent one.
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
    expect(textOf(banner)).toContain("/mnt/7 is offline - 2 models");
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
    // what actually stops the rows - a veil that only looks disabled leaves
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
// app-wide tail - the stats toggle - rather than dropping it the moment the
// grid unmounts. Settings is the title bar's (#2158). Undo is the exception: nothing on this screen writes
// to the operation log, so there is nothing here for it to take back.
describe("the app-wide toolbar tail", () => {
  it("toggles the Models rail itself, and leaves Settings to the title bar", async () => {
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    const sidebar = useSidebarStore();
    expect(
      wrapper.find(".shelf-toolbar button[aria-label='Settings']").exists(),
    ).toBe(false);

    // Directional, not a flip: the rail opens on the first press and closes on
    // the second, from the shut state a fresh session starts in.
    // The rail on this screen is Models | Tasks, with its own flag: the
    // Library's stats flag is left alone.
    sidebar.modelsRailOpen = false;
    sidebar.statsOpen = false;
    const toggle = wrapper.find(".shelf-toolbar .tb-stats-btn");
    expect(toggle.attributes("aria-label")).toBe("Models");
    await toggle.trigger("click");
    expect(sidebar.modelsRailOpen).toBe(true);
    expect(toggle.attributes("aria-pressed")).toBe("true");
    await toggle.trigger("click");
    expect(sidebar.modelsRailOpen).toBe(false);
    expect(sidebar.statsOpen).toBe(false);
  });

  it("orders the tail separator → TbGlobalActions, last in the bar", async () => {
    // The documented tail minus undo
    // (docs/design/toolbar-responsive-decisions.md). Asserted as PLACEMENT and
    // not merely as presence: the whole point of the rule is that the app-wide
    // chrome sits after the view controls, ruled off from them, at the end.
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    // The Show control is the list's; the Sets place draws the grid without it.
    useModelShelfStore().setPlace("all");
    await wrapper.vm.$nextTick();
    const cluster = wrapper.find(".shelf-bar-cluster").element;
    // TbGlobalActions is multi-root; its rail toggle is a stable anchor.
    const toggle = wrapper.find(".shelf-bar-cluster .tb-stats-btn").element;
    const separator = wrapper.find(".shelf-bar-cluster .bar-separator").element;
    // The Show menu - the LAST of this view's own controls, and the one the
    // tail has to come after. (Every bar button is `--boxed` now, the tail's
    // own included, so the Show button is found by name.)
    const showBtn = wrapper.find(".shelf-show-btn").element;
    const follows = (a, b) =>
      Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);

    expect(follows(showBtn, separator)).toBe(true);
    // Adjacency, not merely order: the rule marks the boundary, so nothing may
    // slip between it and the chrome it rules off.
    expect(separator.nextElementSibling).toBe(toggle);
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

// The closed Models rail's handle is the Workflows one (visual-language
// "Closed inspector"): it names the selection, and a NEW selection bumps the
// counter the handle's bounce and the toolbar glyph's flash both play on.
describe("the closed Models rail's handle", () => {
  const handle = (wrapper) => wrapper.find('[data-testid="models-rail-handle"]');
  const rowAt = (wrapper, i) => wrapper.findAll(".shelf-row")[i];

  it("says All models, unstriped, until something is selected, then names it", async () => {
    const wrapper = await mountShelf([
      adapter({ id: 1 }),
      adapter({ id: 2, display_name: "Mossy Ink" }),
    ]);
    expect(handle(wrapper).classes()).toContain("inspector-edge-tab--labelled");
    expect(handle(wrapper).classes()).toContain("inspector-edge-tab--plain");
    expect(handle(wrapper).find(".inspector-edge-tab__label").text()).toBe(
      "All models",
    );
    expect(handle(wrapper).attributes("aria-label")).toBe(
      `Show all models (${useModelShelfStore().visibleRows.length})`,
    );

    await rowAt(wrapper, 1).trigger("click");
    expect(handle(wrapper).classes()).not.toContain("inspector-edge-tab--plain");
    expect(handle(wrapper).find(".inspector-edge-tab__label").text()).toBe(
      "Mossy Ink",
    );
    expect(handle(wrapper).attributes("aria-label")).toBe(
      "Show models: Mossy Ink",
    );

    await rowAt(wrapper, 0).trigger("click", { ctrlKey: true });
    expect(handle(wrapper).find(".inspector-edge-tab__label").text()).toBe(
      "2 models",
    );
  });

  it("nudges on a new selection, never on the same one or on clearing it", async () => {
    const wrapper = await mountShelf([adapter({ id: 1 }), adapter({ id: 2 })]);
    const sidebar = useSidebarStore();
    const store = useModelShelfStore();
    const start = sidebar.inspectorNudge;

    await rowAt(wrapper, 0).trigger("click");
    expect(sidebar.inspectorNudge).toBe(start + 1);
    expect(handle(wrapper).classes().join(" ")).toContain(
      "inspector-edge-tab--nudge-",
    );

    await rowAt(wrapper, 0).trigger("click");
    expect(sidebar.inspectorNudge).toBe(start + 1);

    await rowAt(wrapper, 1).trigger("click");
    expect(sidebar.inspectorNudge).toBe(start + 2);

    store.clearSelection();
    await wrapper.vm.$nextTick();
    expect(sidebar.inspectorNudge).toBe(start + 2);
  });

  it("nudges when a card's tray opens with nothing selected, not when it closes", async () => {
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    const sidebar = useSidebarStore();
    const store = useModelShelfStore();
    const start = sidebar.inspectorNudge;

    store.toggleSet("set:a");
    await wrapper.vm.$nextTick();
    expect(store.selectedRows).toEqual([]);
    expect(sidebar.inspectorNudge).toBe(start + 1);
    expect(handle(wrapper).find(".inspector-edge-tab__label").text()).toBe(
      "All models",
    );
    expect(handle(wrapper).classes().join(" ")).toContain(
      "inspector-edge-tab--nudge-",
    );

    store.toggleSet("set:a");
    await wrapper.vm.$nextTick();
    expect(sidebar.inspectorNudge).toBe(start + 1);
  });

  it("opens the rail on a click, and is gone while it is open", async () => {
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    await handle(wrapper).trigger("click");
    expect(useSidebarStore().modelsRailOpen).toBe(true);
    expect(handle(wrapper).exists()).toBe(false);
  });
});

describe("Delete", () => {
  // The file-manager gesture, spelled the way Explorer spells it: Del trashes,
  // Shift+Del deletes permanently. Handled on the WINDOW beside Escape, so
  // every assertion needs a real event path out of the shelf.
  // `deletable` as the server sends it: what the client may offer Delete on is
  // the server's answer, because `kind` alone cannot tell PixlStash's own
  // download folder from the HuggingFace cache.
  const FOLDER = {
    id: 1,
    path: "/m",
    kind: "user",
    movable: "per_item",
    deletable: true,
  };

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
    fetchModelCompanions.mockReset();
    fetchModelCompanions.mockResolvedValue({
      orphaned: [],
      shared: [],
      unknown: [],
      in_use: [],
      no_evidence: [],
    });
  });

  // The Models rail beside the shelf answers its own keys: a Delete pressed
  // there is the rail's to ask for, never the window handler's as well.
  it("is not the shelf's when pressed in the Models rail", async () => {
    const wrapper = await mountWithSelection();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const rail = document.createElement("div");
    rail.className = "mrail";
    document.body.appendChild(rail);
    rail.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Delete", bubbles: true }),
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(fetchModelCompanions).not.toHaveBeenCalled();
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(useModelShelfStore().selectedIds.size).toBe(1);
    rail.remove();
    wrapper.unmount();
  });

  // The Folders tree (#2206) is in the sidebar, outside `.shelf`, and its
  // Delete is Remove folder… (#2207). Here it must never open a file
  // deletion, selection or no selection.
  it("is not the shelf's when pressed in the Folders tree", async () => {
    const wrapper = await mountWithSelection();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const tree = document.createElement("div");
    tree.className = "mft";
    document.body.appendChild(tree);
    tree.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Delete", bubbles: true }),
    );
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(fetchModelCompanions).not.toHaveBeenCalled();
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(useModelShelfStore().selectedIds.size).toBe(1);
    tree.remove();
    wrapper.unmount();
  });

  it("says in the prompt what the delete leaves behind", async () => {
    // #1314: the question is asked for exactly the ids the call will send,
    // and its answer is in front of the reader before they agree.
    fetchModelCompanions.mockResolvedValue({
      orphaned: [{ id: 5, name: "ae.safetensors" }],
      shared: [{ id: 6, name: "clip_l.safetensors", used_with: [] }],
      unknown: [],
      in_use: [],
      no_evidence: [],
    });
    const wrapper = await mountWithSelection();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

    await pressDelete();

    expect(fetchModelCompanions).toHaveBeenCalledWith([1]);
    const said = confirmSpy.mock.calls[0][0];
    expect(said).toContain(
      "ae.safetensors ran only with models you are deleting",
    );
    expect(said).toContain("clip_l.safetensors is still used by other models");
    expect(deleteModels).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("opens one prompt however often Delete is pressed while it is being prepared", async () => {
    let answer;
    fetchModelCompanions.mockReturnValue(
      new Promise((resolve) => {
        answer = resolve;
      }),
    );
    const wrapper = await mountWithSelection();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

    await pressDelete();
    await pressDelete();
    answer({
      orphaned: [],
      shared: [],
      unknown: [],
      in_use: [],
      no_evidence: [],
    });
    await pressDelete();

    expect(fetchModelCompanions).toHaveBeenCalledTimes(1);
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });

  it("still asks, and says so, when what it leaves behind cannot be read", async () => {
    fetchModelCompanions.mockRejectedValue(new Error("offline"));
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const wrapper = await mountWithSelection();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    await pressDelete();

    expect(confirmSpy.mock.calls[0][0]).toContain("could not check");
    expect(deleteModels).toHaveBeenCalledWith([1], { permanent: false });
    wrapper.unmount();
  });

  it("opens the edit dialog for a verb the store asks for, and a listed row renames inline", async () => {
    const wrapper = await mountShelf([inFolder(1), inFolder(2)]);
    const store = useModelShelfStore();
    store.toggleSelected(2);

    store.requestVerb("set-kind");
    await wrapper.vm.$nextTick();
    expect(wrapper.vm.editVerb).toBe("kind");

    // A row drawn on the list takes its name in place, not in the dialog.
    wrapper.vm.editVerb = "";
    store.requestVerb("rename");
    await wrapper.vm.$nextTick();
    expect(wrapper.vm.editVerb).toBe("");
    expect(wrapper.find(".shelf-row-rename").exists()).toBe(true);
    wrapper.unmount();
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

  it("says a file in a hand-made set stays there, trashed or deleted for good (#1520)", async () => {
    const wrapper = await mountWithSelection();
    const store = useModelShelfStore();
    store.workflowSets = {
      ...store.workflowSets,
      handMade: [
        {
          id: 10,
          name: "Kit",
          members: [
            { sha256: "x".repeat(64), slot: "lora", on_shelf: true, id: 1 },
          ],
        },
      ],
    };
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

    await pressDelete();
    await pressDelete({ shiftKey: true });

    // Membership is kept by hash in a table neither delete touches, so the
    // sentence holds for both - and says HOW, beside "everything recorded".
    expect(confirmSpy).toHaveBeenCalledTimes(2);
    for (const [said] of confirmSpy.mock.calls) {
      expect(said).toContain('It stays in your set "Kit"');
      expect(said).toContain("kept by its file hash");
    }
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
    // for its whole run, and the call sends every member - so a prompt counting
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
    // The run, picked the way the list picks it: a collapsed row stands for
    // every file behind it. Ticking the cover's id alone now means that one
    // file, which is the gesture the expanded strip added.
    useModelShelfStore().selectFromClick(1, { ctrl: true });
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
    // And says WHICH folder. The sentence this replaced claimed PixlStash
    // "only removes files from your own model folders", which is untrue - the
    // managed store and PixlStash's own download folder are neither - and
    // named nothing the reader could go and act on.
    const said = useNoticeStore().notices.at(-1).text;
    expect(said).toContain("/hf");
    expect(said).toContain("Another tool owns that folder");
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

describe("the view switcher does not resize when you switch", () => {
  // Twice now the tabs have shoved the whole left group of the toolbar sideways
  // on every switch, because the selected state changed something that costs
  // layout - first `font-weight`, which makes the label wider. The selected
  // segment may change COLOUR all it likes; it may not change its box.
  // Read from the project root rather than via `import.meta.url`: this suite
  // runs in the jsdom environment, where `import.meta.url` is an http: URL and
  // `fileURLToPath` refuses it.
  const source = readFileSync(
    resolve(process.cwd(), "src/components/views/ModelShelf.vue"),
    "utf8",
  );
  const styles = source.slice(source.lastIndexOf("<style"));
  const bodyOf = (selector) =>
    styles
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .split("}")
      .filter((rule) => {
        const [sel] = rule.split("{");
        return sel
          .split(",")
          .some((s) => s.replace(/\s+/g, " ").trim() === selector);
      })
      .map((rule) => rule.split("{")[1] || "");

  const LAYOUT = [
    /(^|\s|;)font-weight\s*:/,
    /(^|\s|;)font-size\s*:/,
    /(^|\s|;)padding[^:]*:/,
    /(^|\s|;)margin[^:]*:/,
    /(^|\s|;)border-width\s*:/,
    /(^|\s|;)letter-spacing\s*:/,
  ];

  it("has a selected rule at all, so this is not asserting nothing", () => {
    expect(bodyOf(".shelf-viewseg--on").length).toBeGreaterThan(0);
  });

  it("changes no property that costs layout when a segment is selected", () => {
    for (const body of bodyOf(".shelf-viewseg--on")) {
      for (const banned of LAYOUT) {
        expect(body, `.shelf-viewseg--on must not set ${banned}`).not.toMatch(
          banned,
        );
      }
    }
  });

  it("keeps both segments on one type ramp step", () => {
    // A per-segment font-size would resize the pair for the same reason.
    for (const body of bodyOf(".shelf-viewseg")) {
      expect(body).not.toMatch(/(^|\s|;)font-weight\s*:/);
    }
  });
});

describe("acting inside a run", () => {
  // The strip stopped being a read-only listing (#1005). Its rows are pickable
  // like any other, which is what makes "this checkpoint is the good one" and
  // "this one is a different subject" gestures rather than shrugs.

  const member = (id, position, overrides = {}) =>
    adapter({
      id,
      sha256: String.fromCharCode(97 + id).repeat(64),
      stack_id: 7,
      stack_position: position,
      filename: `Foxglove_00000${position}500.safetensors`,
      locations: [
        { state: "present", folder_id: 1, folder_path: "/m", relpath: `${id}` },
      ],
      ...overrides,
    });

  /** Mount a two-file run and open it, which is where every gesture starts. */
  async function openRun(rows = [member(1, 0), member(2, 1)]) {
    listModelFolders.mockResolvedValue([
      { id: 1, path: "/m", kind: "user", movable: "per_item", deletable: true },
    ]);
    const wrapper = await mountShelf(rows);
    document.body.appendChild(wrapper.element);
    await wrapper.find(".shelf-row").trigger("keydown", { key: "ArrowRight" });
    return wrapper;
  }

  it("draws the deck behind the mark, not across the whole slot", async () => {
    // The ticks fill their nearest positioned box. Left in the slot they drew
    // a grey pill over the mark, which read as a broken thumbnail.
    const wrapper = await openRun();
    const ticks = wrapper.find(
      ".shelf-row:not(.shelf-row--member) [data-testid='stack-edge-ticks']",
    );
    expect(ticks.element.parentElement.classList).toContain("shelf-row-mark");
    expect(ticks.element.parentElement.querySelector(".mmark")).not.toBeNull();
    wrapper.unmount();
  });

  it("says which file the run is being drawn from, once it is open", async () => {
    const wrapper = await openRun();
    // On the cover's thumbnail, not among the name's chips (where it pushed
    // the line into the text around it).
    const flags = wrapper.findAll(
      ".shelf-row:not(.shelf-row--member) .shelf-row-ident .stack-cover-flag",
    );
    expect(flags.map((f) => f.text())).toEqual(["Cover"]);
    expect(
      wrapper.findAll(".shelf-row-label .shelf-chip").map((c) => c.text()),
    ).not.toContain("Cover");
    // Not on the member rows: exactly one file covers a run.
    expect(
      wrapper.findAll(".shelf-row--member .stack-cover-flag"),
    ).toHaveLength(0);
    wrapper.unmount();
  });

  it("picks ONE file when a member is clicked, not the whole run", async () => {
    // The distinction the strip exists for. Clicking the collapsed row still
    // takes the run whole - that is asserted by the delete suite - and this is
    // the second gesture, reachable only with the run open.
    const wrapper = await openRun();
    const store = useModelShelfStore();

    await wrapper.find(".shelf-row--member").trigger("click");

    expect(store.selectedModelIds).toEqual([2]);
    expect(store.selectedRows).toHaveLength(1);
    expect(wrapper.find(".shelf-row--member").classes()).toContain(
      "shelf-row--selected",
    );
    wrapper.unmount();
  });

  it("walks into the open strip with the arrow keys, and back out", async () => {
    const wrapper = await openRun();
    const cover = wrapper.find(".shelf-row:not(.shelf-row--member)");

    await cover.trigger("keydown", { key: "ArrowDown" });
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".shelf-row--member").attributes("tabindex")).toBe("0");

    // Left closes the run from inside it: the strip the cursor is standing in
    // is about to stop existing, so focus goes back to the row that owns it.
    await wrapper.find(".shelf-row--member").trigger("keydown", {
      key: "ArrowLeft",
    });
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".shelf-row--member").exists()).toBe(false);
    expect(
      wrapper.find(".shelf-row:not(.shelf-row--member)").attributes("tabindex"),
    ).toBe("0");
    wrapper.unmount();
  });

  it("keeps a tab stop when the strip the cursor was in closes", async () => {
    // A remembered row key that has stopped being drawn beats the fallback, so
    // nothing carries `tabindex="0"` and the whole list drops out of the tab
    // order until something is clicked.
    const wrapper = await openRun();
    const cover = wrapper.find(".shelf-row:not(.shelf-row--member)");
    await cover.trigger("keydown", { key: "ArrowDown" });
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".shelf-row--member").attributes("tabindex")).toBe("0");

    // Closed from the badge, which is not the path that hands focus back.
    await wrapper.find(".shelf-stack-badge").trigger("click");
    await wrapper.vm.$nextTick();
    expect(
      wrapper
        .findAll(".shelf-row")
        .filter((r) => r.attributes("tabindex") === "0"),
    ).toHaveLength(1);
    wrapper.unmount();
  });

  it("marks the cover only when the drawn top row really is it", async () => {
    // A filter can hide position 0, and `collapseStacks` then draws the lowest
    // surviving member at the top. That row stands in for the run; it is not
    // the cover, and labelling it would answer the question wrongly.
    const wrapper = await openRun([
      member(1, 0, { base_model: "sdxl" }),
      member(2, 1),
      member(3, 2),
    ]);
    useModelShelfStore().setFilters({ baseModels: ["flux.1-dev"] });
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".stack-cover-flag").exists()).toBe(false);
    wrapper.unmount();
  });

  it("reports both files when taking one out dissolved the run", async () => {
    // The half the reader did not ask for: a run of two loses one file and the
    // other comes loose with it. Counting the CALLS would say 1.
    const wrapper = await openRun();
    const notices = useNoticeStore();
    await wrapper.find(".shelf-row--member").trigger("click");
    removeStackMember.mockResolvedValue({ released: 2, dissolved: true });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    wrapper
      .findComponent({ name: "ShelfSelectionBar" })
      .vm.$emit("remove-from-stack");
    await new Promise((resolve) => setTimeout(resolve, 0));

    const text = notices.notices.at(-1).text;
    expect(text).toContain("2 files");
    expect(text).toContain("stopped being a run");
    confirmSpy.mockRestore();
    wrapper.unmount();
  });

  it("makes the picked member the cover, with no prompt in the way", async () => {
    const wrapper = await openRun();
    const confirmSpy = vi.spyOn(window, "confirm");
    await wrapper.find(".shelf-row--member").trigger("click");
    const before = listAdapters.mock.calls.length;

    wrapper.findComponent({ name: "ShelfSelectionBar" }).vm.$emit("make-cover");
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(setStackCover).toHaveBeenCalledWith(7, 2);
    // Refetched rather than reordered locally: the server owns the order, and
    // the row's name, kind and base all move with the cover.
    expect(listAdapters.mock.calls.length).toBeGreaterThan(before);
    expect(confirmSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
    wrapper.unmount();
  });

  it("takes the picked members out of their run, once confirmed", async () => {
    const wrapper = await openRun([member(1, 0), member(2, 1), member(3, 2)]);
    const store = useModelShelfStore();
    store.selectFromClick(2, {});
    store.selectFromClick(3, { ctrl: true });
    await wrapper.vm.$nextTick();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);

    wrapper
      .findComponent({ name: "ShelfSelectionBar" })
      .vm.$emit("remove-from-stack");
    await new Promise((resolve) => setTimeout(resolve, 0));

    // One call per file, so one refusal cannot discard the others.
    expect(removeStackMember).toHaveBeenCalledTimes(2);
    expect(removeStackMember).toHaveBeenCalledWith(7, 2);
    expect(removeStackMember).toHaveBeenCalledWith(7, 3);
    confirmSpy.mockRestore();
    wrapper.unmount();
  });

  it("does nothing at all when the prompt is declined", async () => {
    const wrapper = await openRun();
    await wrapper.find(".shelf-row--member").trigger("click");
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);

    wrapper
      .findComponent({ name: "ShelfSelectionBar" })
      .vm.$emit("remove-from-stack");
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(removeStackMember).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
    wrapper.unmount();
  });
});

// ===========================================================================
// The shipped DEFAULT (#1438)
//
// `mountShelf` chooses `groupBy: "none"` for every test above, because the row
// list is what they are about. Nothing then mounted this view in the state a
// reader actually opens it in, which is how a live-but-inert Sort control got
// as far as review. These mount it as shipped.
// ===========================================================================

async function mountDefaultShelf(rows = [adapter()]) {
  listAdapters.mockResolvedValue(rows);
  listCheckpoints.mockResolvedValue([]);
  listEngines.mockResolvedValue([]);
  listUnclassified.mockResolvedValue([]);
  listSupport.mockResolvedValue([]);
  const wrapper = mount(ModelShelf, globalOpts);
  await new Promise((resolve) => setTimeout(resolve, 0));
  await wrapper.vm.$nextTick();
  return wrapper;
}

describe("the set grid is what the shelf opens on", () => {
  const rowAt = (wrapper, i) => wrapper.findAll(".shelf-row")[i];

  it("draws the grid rather than the row list, with no axis chosen", async () => {
    const wrapper = await mountDefaultShelf();
    const store = useModelShelfStore();

    expect(store.view.groupBy).toBe("workflow_set");
    expect(wrapper.find(".set-grid-stub").exists()).toBe(true);
    expect(wrapper.find(".shelf-row").exists()).toBe(false);
  });

  it("hides Sort, because it steers a list that is not on screen", async () => {
    const wrapper = await mountDefaultShelf();
    const store = useModelShelfStore();

    // The shipped split-button, which is what `Sort` actually is.
    const hasSort = () => wrapper.find(".bar-split-button").exists();

    // A live control with no effect reads "Sort: Added" over a grid it cannot
    // reorder, so it is absent here and back the moment the row list is. The grid
    // orders itself by the weight of the evidence behind each set.
    expect(hasSort()).toBe(false);

    store.setView({ groupBy: "none" });
    await wrapper.vm.$nextTick();
    expect(hasSort()).toBe(true);
  });

  it("counts sets rather than models while the grid is on screen", async () => {
    const wrapper = await mountDefaultShelf();

    expect(textOf(wrapper.find(".shelf-sub"))).toContain("0 sets");

    useModelShelfStore().setView({ groupBy: "none" });
    await wrapper.vm.$nextTick();
    expect(textOf(wrapper.find(".shelf-sub"))).toContain("1 model");
  });

  /**
   * The shelf on the set grid, with a card actually drawn for model 1.
   *
   * Seeded straight onto the store rather than through the api double: this
   * suite stubs `ModelSetGrid`, so nothing here ever calls `loadWorkflowSets`
   * and an unseeded grid draws no cards - which is the state that made three of
   * these assertions pass for the wrong reason.
   */
  async function shelfOnTheGrid() {
    const wrapper = await mountDefaultShelf([
      adapter({ id: 1, sha256: "a".repeat(64) }),
      adapter({ id: 2, sha256: "b".repeat(64) }),
    ]);
    const store = useModelShelfStore();
    store.workflowSets = {
      combinations: [
        {
          key: "1",
          models: [{ id: 1, name: "a.st", kind: "checkpoint" }],
          recipes: 1,
          picture_count: 1,
          covers: [],
        },
      ],
    };
    store.setView({ groupBy: "workflow_set" });
    await wrapper.vm.$nextTick();
    return { wrapper, store };
  }

  it("the shelf's WINDOW listener deletes selected sets on Delete and no other key (#1520)", async () => {
    // Scope: the shelf-level listener only, with the grid stubbed out. A
    // review asked whether any key reaching the window with a set selected
    // would delete it; only Delete does. Backspace ON a focused hand-made card
    // is the grid's own gesture (the Mac keyboard's Delete key sends it) and
    // is covered in ModelSetGrid.test.js, so the two do not contradict.
    deleteWorkflowSet.mockReset().mockResolvedValue({
      deleted: { id: 10, name: "Kit", members: [] },
    });
    const { store } = await shelfOnTheGrid();
    store.workflowSets = {
      ...store.workflowSets,
      handMade: [{ id: 10, name: "Kit", members: [] }],
    };
    store.selectSet(10);
    for (const key of ["ArrowDown", "n", "F2", "Backspace"]) {
      window.dispatchEvent(new KeyboardEvent("keydown", { key }));
    }
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(deleteWorkflowSet).not.toHaveBeenCalled();

    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Delete" }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(deleteWorkflowSet).toHaveBeenCalledWith(10);
  });

  it("renames a selected hand-made set on F2, as its pill says (#1520)", async () => {
    const { wrapper, store } = await shelfOnTheGrid();
    store.workflowSets = {
      ...store.workflowSets,
      handMade: [{ id: 10, name: "Kit", members: [] }],
    };
    store.selectSet(10);
    await wrapper.vm.$nextTick();

    window.dispatchEvent(new KeyboardEvent("keydown", { key: "F2" }));
    await wrapper.vm.$nextTick();

    expect(
      wrapper.findComponent({ name: "WorkflowSetRenameDialog" }).props("set"),
    ).toMatchObject({ id: 10 });
  });

  it("floats the verb bar over the grid, as it does over the list", async () => {
    // It did not, while a card stood for a whole SET: a Delete aimed at one
    // could have taken a shared VAE with it. A card stands for its BASE MODEL
    // now and a tray row for one file, so everything the bar can be aimed at
    // there is one model - which is what its verbs write.
    // The PILL, not the `.selbar-float` strip it docks in: the strip is the
    // host's positioning wrapper and is always there, so asserting on it would
    // be asserting that `isShelfTab` is true.
    const { wrapper, store } = await shelfOnTheGrid();
    expect(wrapper.find(".selbar").exists()).toBe(false);

    store.toggleSelected(1);
    await wrapper.vm.$nextTick();
    expect(store.selectedRows.map((row) => row.id)).toEqual([1]);
    expect(wrapper.find(".selbar").exists()).toBe(true);

    // And model 2, which is on NO card, arms nothing while the grid is up.
    store.clearSelection();
    store.toggleSelected(2);
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".selbar").exists()).toBe(false);
    // It is still held, and the row list offers it again.
    store.setView({ groupBy: "none" });
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".selbar").exists()).toBe(true);
  });

  it("opens the verb menu where the grid says the pointer was", async () => {
    const { wrapper, store } = await shelfOnTheGrid();
    store.toggleSelected(1);
    await wrapper.vm.$nextTick();
    const bar = wrapper.findComponent({ name: "ShelfSelectionBar" });
    expect(bar.vm.contextOpen).toBe(false);

    await wrapper
      .findComponent({ name: "ModelSetGrid" })
      .vm.$emit("menu", { x: 210, y: 64 });
    await wrapper.vm.$nextTick();

    // One bar for both views, so there is one set of refusals rather than two
    // that can drift. The menu is its own, opened through its exposed method.
    expect(bar.vm.contextOpen).toBe(true);
    expect(bar.vm.contextAt).toEqual([210, 64]);

    // A card's menu says which card, and its Clone set to edit reaches the
    // store with the set the bar named.
    await wrapper
      .findComponent({ name: "ModelSetGrid" })
      .vm.$emit("menu", { x: 1, y: 2, setKey: "model:1" });
    expect(bar.vm.contextSetKey).toBe("model:1");
    store.cloneSetFromPictures = vi.fn();
    const group = { key: "model:1" };
    await bar.vm.$emit("clone-set", group);
    expect(store.cloneSetFromPictures).toHaveBeenCalledWith(group);
  });

  /** The grid with three cards: models 1 and 2, and a missing checkpoint. */
  async function gridWithThreeCards() {
    const { wrapper, store } = await shelfOnTheGrid();
    const card = (extra) => ({
      recipes: 1,
      picture_count: 1,
      covers: [],
      ...extra,
    });
    store.workflowSets = {
      ...store.workflowSets,
      combinations: [
        ...store.workflowSets.combinations,
        card({ key: "2", models: [{ id: 2, name: "b.st", kind: "checkpoint" }] }),
        card({
          key: "+gone.sft",
          models: [],
          missing: [{ name: "gone.sft", workflow_ids: ["auto:a"] }],
        }),
      ],
    };
    await wrapper.vm.$nextTick();
    expect(store.setGroups.map((g) => g.key).sort()).toEqual([
      "missing:gone.sft",
      "model:1",
      "model:2",
    ]);
    // The stubbed grid's answer to "which cards are drawn selected".
    const lightCards = async (...keys) => {
      wrapper.findComponent({ name: "ModelSetGrid" }).vm.selectedCardGroups =
        store.setGroups.filter((g) => keys.includes(g.key));
      await wrapper.vm.$nextTick();
    };
    return { wrapper, store, lightCards };
  }

  it("hides the cards the grid says are selected, and no other", async () => {
    const { wrapper, store, lightCards } = await gridWithThreeCards();
    const bar = wrapper.findComponent({ name: "ShelfSelectionBar" });
    const hide = () =>
      bar.findAll(".ctx-item").find((b) => b.text().includes("workflow set"));
    store.toggleSelected(1);
    store.toggleSelected(2);
    await wrapper.vm.$nextTick();
    // Two models are selected, but the grid names no card as lit (which is
    // what it answers for a pick made in a tray): no set verb.
    expect(hide()).toBeUndefined();

    await lightCards("model:1");
    expect(hide().text()).toBe("Hide workflow set");
    await hide().trigger("click");
    // The hide is a server call (#1869), so the grid redraws a turn later.
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(store.setGroups.map((g) => g.key).sort()).toEqual([
      "missing:gone.sft",
      "model:2",
    ]);
    // The row behind the card is untouched: hidden, not forgotten or deleted.
    expect(store.rows.map((r) => r.id)).toEqual([1, 2]);
  });

  it("hides every lit card from either pill's menu, missing ones included", async () => {
    const { wrapper, store, lightCards } = await gridWithThreeCards();
    store.toggleSelected(1);
    store.selectAllMissing();
    await lightCards("model:1");

    const fileRow = wrapper
      .findComponent({ name: "ShelfSelectionBar" })
      .findAll(".ctx-item")
      .find((b) => b.text().includes("workflow set"));
    const missingBar = wrapper.findComponent({ name: "MissingSetSelectionBar" });
    // One row, one count, on both: a card is a card whichever pill counts it.
    expect(fileRow.text()).toBe("Hide 2 workflow sets");
    expect(missingBar.find(".ctx-item[data-verb='hide-sets']").text()).toBe(
      "Hide 2 workflow sets",
    );

    await missingBar.find('.selbar [data-verb="hide-sets"]').trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));

    // Model 2's card was never selected.
    expect(store.setGroups.map((g) => g.key)).toEqual(["model:2"]);
    expect(store.hiddenSetCount).toBe(2);
    // Both left the screen, so neither is still held for a later Undo to arm.
    expect([...store.selectedIds]).toEqual([]);
    expect([...store.selectedMissingKeys]).toEqual([]);
  });

  it("hides a selected hand-made set from the set pill, by its store group", async () => {
    const { wrapper, store } = await gridWithThreeCards();
    store.workflowSets = {
      ...store.workflowSets,
      handMade: [{ id: 10, name: "Kit", members: [] }],
    };
    await wrapper.vm.$nextTick();
    const spy = vi.fn();
    store.toggleHiddenSets = spy;
    store.selectSet(10);
    await wrapper.vm.$nextTick();

    const setBar = wrapper.findComponent({ name: "WorkflowSetSelectionBar" });
    await setBar.find('.selbar [data-verb="hide-sets"]').trigger("click");

    // The store's group, by key: the set's card is what Hide reads.
    expect(spy.mock.calls.map(([groups]) => groups.map((g) => g.key))).toEqual([
      ["hand:10"],
    ]);
  });

  it("passes the lit model cards and the selected hand-made set together", async () => {
    const { wrapper, store, lightCards } = await gridWithThreeCards();
    store.workflowSets = {
      ...store.workflowSets,
      handMade: [{ id: 10, name: "Kit", members: [] }],
    };
    await wrapper.vm.$nextTick();
    const spy = vi.fn();
    store.toggleHiddenSets = spy;
    store.selectSet(10);
    store.selectFromClick(1, { ctrl: true });
    await lightCards("model:1");

    // Either pill's row acts on the whole mixed selection, one list.
    const setBar = wrapper.findComponent({ name: "WorkflowSetSelectionBar" });
    await setBar.find('.selbar [data-verb="hide-sets"]').trigger("click");
    expect(spy.mock.calls.map(([groups]) => groups.map((g) => g.key))).toEqual([
      ["model:1", "hand:10"],
    ]);
  });

  it("takes Ctrl+A to mean the models the GRID draws", async () => {
    // The key was taken away entirely while nothing on the grid was selectable.
    // Back on, it has to answer the screen it is on: the row list holds two
    // rows here and the grid draws one card, so a guard reading `visibleRows`
    // would pass and then select the wrong set - or, with no card at all,
    // silently clear what the reader was still holding.
    const { wrapper, store } = await shelfOnTheGrid();
    document.body.appendChild(wrapper.element);

    window.dispatchEvent(
      new KeyboardEvent("keydown", { key: "a", ctrlKey: true }),
    );
    await wrapper.vm.$nextTick();
    expect(store.selectedRows.map((row) => row.id)).toEqual([1]);
    expect(store.visibleRows).toHaveLength(2);
  });

  /** The grid with two of the owner's sets beside card 1. */
  async function gridWithHandMade() {
    const { wrapper, store } = await shelfOnTheGrid();
    document.body.appendChild(wrapper.element);
    store.workflowSets = {
      ...store.workflowSets,
      handMade: [
        { id: 10, name: "Kit", members: [] },
        { id: 11, name: "Other", members: [] },
      ],
    };
    await wrapper.vm.$nextTick();
    return { wrapper, store };
  }

  it("takes the models AND every hand-made set on Ctrl+A", async () => {
    const { wrapper, store } = await gridWithHandMade();

    window.dispatchEvent(
      new KeyboardEvent("keydown", { key: "a", ctrlKey: true }),
    );
    await wrapper.vm.$nextTick();
    expect(store.selectedRows.map((row) => row.id)).toEqual([1]);
    expect(store.selectedSets.map((set) => set.id)).toEqual([10, 11]);
    wrapper.unmount();
  });

  it("holds sets and models together on Ctrl+click, and a plain click replaces both", async () => {
    const { wrapper, store } = await gridWithHandMade();

    store.selectSet(10);
    store.selectFromClick(1, { ctrl: true });
    store.selectSet(11, { ctrl: true });
    await wrapper.vm.$nextTick();
    expect(store.selectedRows.map((row) => row.id)).toEqual([1]);
    expect(store.selectedSets.map((set) => set.id)).toEqual([10, 11]);

    store.selectFromClick(1);
    await wrapper.vm.$nextTick();
    expect(store.selectedSets).toHaveLength(0);
    expect(store.selectedRows.map((row) => row.id)).toEqual([1]);

    store.selectSet(10, { ctrl: true });
    store.selectSet(11);
    await wrapper.vm.$nextTick();
    expect(store.selectedRows).toHaveLength(0);
    expect(store.selectedSets.map((set) => set.id)).toEqual([11]);
    wrapper.unmount();
  });

  it("clears both kinds on Escape, and never deletes sets from a mixed Delete", async () => {
    // With models selected too, Delete is the FILE confirmation - a prompt -
    // and the sets are left for their own pill.
    deleteWorkflowSet.mockReset().mockResolvedValue({
      deleted: { id: 10, name: "Kit", members: [] },
    });
    const { wrapper, store } = await gridWithHandMade();
    const mixed = async () => {
      store.selectSet(10);
      store.selectFromClick(1, { ctrl: true });
      await wrapper.vm.$nextTick();
      expect(store.selectedSets).toHaveLength(1);
      expect(store.selectedRows).toHaveLength(1);
    };

    await mixed();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await wrapper.vm.$nextTick();
    expect(store.selectedSets).toHaveLength(0);
    expect(store.selectedRows).toHaveLength(0);

    await mixed();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Delete" }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(deleteWorkflowSet).not.toHaveBeenCalled();
    expect(deleteModels).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("takes the hand-made sets on Ctrl+A when the grid draws no model", async () => {
    const { wrapper, store } = await shelfOnTheGrid();
    document.body.appendChild(wrapper.element);
    store.workflowSets = {
      ...store.workflowSets,
      combinations: [],
      handMade: [{ id: 10, name: "Kit", members: [] }],
    };
    await wrapper.vm.$nextTick();
    expect(store.screenRows).toHaveLength(0);

    window.dispatchEvent(
      new KeyboardEvent("keydown", { key: "a", ctrlKey: true }),
    );
    await wrapper.vm.$nextTick();
    expect(store.selectedSets.map((set) => set.id)).toEqual([10]);
    wrapper.unmount();
  });

  it("clears on Escape from the grid, and refuses Delete once it has", async () => {
    const { wrapper, store } = await shelfOnTheGrid();
    document.body.appendChild(wrapper.element);
    store.toggleSelected(1);
    await wrapper.vm.$nextTick();
    // The pre-state, asserted: without it this test is green whenever the
    // selection happens to be empty for some other reason.
    expect(store.selectedRows).toHaveLength(1);

    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await wrapper.vm.$nextTick();
    expect(store.selectedRows).toHaveLength(0);

    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Delete" }));
    await wrapper.vm.$nextTick();
    expect(deleteModels).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("titles Works with from the row list with a name, not a serialised object", async () => {
    // A shelf row's `name` is `modelName`'s `{text, state}` pair; a set member's
    // is a plain string. Bound straight into the heading, the row list's own
    // entry point rendered the title as JSON - and every dialog test fabricated
    // a flat object, so the suite never saw the shape the row list sends
    // (#1479 review).
    const wrapper = await mountDefaultShelf([
      adapter({ id: 1, display_name: "Cyanwood Style" }),
    ]);
    const store = useModelShelfStore();
    store.setView({ groupBy: "none" });
    await wrapper.vm.$nextTick();
    await rowAt(wrapper, 0).trigger("click");

    wrapper.findComponent({ name: "ShelfSelectionBar" }).vm.$emit("works-with");
    await wrapper.vm.$nextTick();

    const handed = wrapper
      .findComponent({ name: "ModelWorksWithDialog" })
      .props().model;
    // The ROW is handed over whole - the dialog is what normalises the two
    // shapes - so this asserts the shape really does reach it.
    expect(handed.id).toBe(1);
    expect(handed.name).toMatchObject({ text: "Cyanwood Style" });
    wrapper.unmount();
  });

  it("hands the grid's file to the one Works with dialog", async () => {
    const wrapper = await mountDefaultShelf();

    await wrapper
      .findComponent({ name: "ModelSetGrid" })
      .vm.$emit("works-with", { id: 7, filename: "handed-up.st" });
    await wrapper.vm.$nextTick();

    // One dialog, owned here: two mounted copies with a ref each is two dialogs
    // that can disagree about which is open.
    expect(wrapper.findAll(".ww-stub")).toHaveLength(1);
    expect(wrapper.find(".ww-stub").text()).toBe("handed-up.st");
  });
});

describe("a row the owner has marked in a set", () => {
  const mark = (verdict, names) => ({ combo_key: "1,2", names, verdict });

  it("wears a cross that names the set, and the cross wins over a check", async () => {
    const wrapper = await mountShelf([
      adapter({
        set_verdicts: [
          mark("not_problem", ["Base", "VAE"]),
          mark("problem", ["Base", "LoRA"]),
        ],
      }),
    ]);
    const icon = wrapper.get(".shelf-row [data-testid='verdict-icon']");
    expect(icon.classes()).toContain("verdict-icon--negative");
    expect(icon.attributes("aria-label")).toBe(
      "Marked a problem in 1 set: Base + LoRA. Not a problem in 1 set: Base + VAE",
    );
  });

  it("wears a check when every answer was not-a-problem", async () => {
    const wrapper = await mountShelf([
      adapter({ set_verdicts: [mark("not_problem", ["Base", "VAE"])] }),
    ]);
    expect(
      wrapper.get(".shelf-row [data-testid='verdict-icon']").classes(),
    ).toContain("verdict-icon--positive");
  });

  it("wears nothing without verdicts", async () => {
    const wrapper = await mountShelf([adapter({ set_verdicts: [] })]);
    expect(
      wrapper.find(".shelf-row [data-testid='verdict-icon']").exists(),
    ).toBe(false);
  });
});


describe("the open folder (#2203)", () => {
  const FOLDER = {
    id: 1,
    path: "/home/me/StabilityMatrix/Models",
    kind: "user",
    movable: "per_item",
    deletable: true,
  };
  const at = (relpath) => [
    { state: "present", folder_id: 1, folder_path: FOLDER.path, relpath },
  ];

  async function openFolder(path = "") {
    listModelFolders.mockResolvedValue([FOLDER]);
    listModelFolderDevices.mockResolvedValue([
      {
        device_id: "d1",
        mount_point: "/",
        label: "System drive",
        total_bytes: 1000 * 1024 ** 3,
        free_bytes: 50 * 1024 ** 3,
        folder_ids: [1],
      },
    ]);
    listModelFolderDirs.mockImplementation(async (id, at) => ({
      folder_id: id,
      path: at,
      locked: false,
      model_count: 2,
      bytes: 0,
      dirs:
        at === ""
          ? [
              { name: "Lora", path: "Lora", model_count: 1, bytes: 2e8, has_dirs: false },
              { name: "VAE", path: "VAE", model_count: 0, bytes: 0, has_dirs: false },
            ]
          : [],
    }));
    const wrapper = await mountShelf([
      adapter({ id: 1, locations: at("loose.safetensors") }),
      adapter({ id: 2, sha256: "b".repeat(64), locations: at("Lora/x.safetensors") }),
    ]);
    const store = useModelShelfStore();
    await useModelFoldersStore().refreshDevices();
    store.setFolderScope({ folderId: 1, path });
    await flushPromises();
    return { wrapper, store };
  }

  describe("moving into a folder (#2204)", () => {
    const LOOSE = { folder_id: 1, relpath: "loose.safetensors" };
    const dialog = (wrapper) => wrapper.findComponent({ name: "ShelfMoveDialog" });
    const press = (key) => {
      document.body.dispatchEvent(
        new KeyboardEvent("keydown", { key, ctrlKey: key !== "Escape", bubbles: true }),
      );
      return flushPromises();
    };

    async function selectLoose() {
      const opened = await openFolder();
      document.body.appendChild(opened.wrapper.element);
      opened.store.toggleSelected(1);
      await flushPromises();
      return opened;
    }

    it("cuts with Ctrl X and pastes on the same drive without asking", async () => {
      const { wrapper, store } = await selectLoose();
      await press("x");
      expect(store.cutCopies.items).toEqual([LOOSE]);
      expect(wrapper.find(".shelf-row--cut").exists()).toBe(true);

      store.setFolderScope({ folderId: 1, path: "Lora" });
      await flushPromises();
      await press("v");
      // A rename on one drive: the move starts, as a file manager's does, and
      // no dialog asks first.
      expect(dialog(wrapper).props("open")).toBe(false);
      expect(startModelMove).toHaveBeenCalledWith(1, [LOOSE], "Lora");
      // The cut is spent once the move it pasted starts.
      expect(store.cutCopies).toBe(null);
      wrapper.unmount();
    });

    it("asks before a drop it cannot promise is a rename", async () => {
      const { wrapper, store } = await selectLoose();
      // The drive was not measured, so the move may copy: it is stated first.
      useModelFoldersStore().devices = [];
      store.requestMove(store.movableSelection(), { folderId: 1, path: "Lora" });
      await flushPromises();
      expect(startModelMove).not.toHaveBeenCalled();
      expect(dialog(wrapper).props("open")).toBe(true);
      expect(dialog(wrapper).props("items")).toEqual([LOOSE]);
      expect(dialog(wrapper).props("initialDestination")).toEqual({
        folderId: 1,
        path: "Lora",
      });
      wrapper.unmount();
    });

    it("puts a cut back with Escape, and pastes nothing outside a folder", async () => {
      const { wrapper, store } = await selectLoose();
      await press("x");
      await press("Escape");
      expect(store.cutCopies).toBe(null);
      expect(store.selectedRows.map((row) => row.id)).toEqual([1]);

      await press("x");
      store.setPlace("all");
      await flushPromises();
      await press("v");
      expect(dialog(wrapper).props("open")).toBe(false);
      wrapper.unmount();
    });

    /** A main-button press on `el`, and the pointer moved to `x`, `y`. */
    const pointer = (el, type, x = 0, y = 0) =>
      el.dispatchEvent(
        new MouseEvent(type, {
          button: 0,
          buttons: type === "pointerup" ? 0 : 1,
          clientX: x,
          clientY: y,
          bubbles: true,
        }),
      );

    it("drags an unselected row alone, selecting it first", async () => {
      const { wrapper, store } = await openFolder();
      expect(store.selectedRows).toEqual([]);
      const row = wrapper.find(".shelf-row");
      // Not the browser's drag: its image lifted late on the desktop shell.
      expect(row.attributes("draggable")).toBeUndefined();
      pointer(row.element, "pointerdown");
      // Under 4 px a press is still a click.
      pointer(window, "pointermove", 3, 0);
      expect(store.dragCopies).toBe(null);
      pointer(window, "pointermove", 10, 0);
      expect(store.selectedRows.map((one) => one.id)).toEqual([1]);
      expect(store.dragCopies.items).toEqual([LOOSE]);
      // Away from a folder the tip is the preview: what is carried.
      expect(store.dropTip).toEqual({ x: 10, y: 0, text: "loose.safetensors" });
      pointer(window, "pointerup", 10, 0);
      expect(store.dragCopies).toBe(null);
      expect(store.dropTip).toBe(null);
      wrapper.unmount();
    });

    it("hands the drop to the folder under the pointer, and eats the release's click", async () => {
      const { wrapper, store } = await openFolder();
      const folder = document.createElement("div");
      folder.dataset.modelCopiesDrop = "";
      const heard = [];
      folder.addEventListener("model-copies-over", (event) => {
        heard.push(["over", event.detail]);
        event.preventDefault();
      });
      folder.addEventListener("model-copies-drop", () =>
        heard.push(["drop", store.dragCopies?.items]),
      );
      document.body.appendChild(folder);
      const was = document.elementFromPoint;
      document.elementFromPoint = (x) => (x > 100 ? folder : document.body);
      const clicked = vi.fn();
      document.body.addEventListener("click", clicked);
      try {
        pointer(wrapper.find(".shelf-row").element, "pointerdown");
        pointer(window, "pointermove", 20, 0);
        expect(store.dropTip.text).toBe("loose.safetensors");
        pointer(window, "pointermove", 120, 5);
        pointer(window, "pointerup", 120, 5);
        expect(heard).toEqual([
          ["over", { x: 120, y: 5 }],
          ["drop", [LOOSE]],
        ]);
        document.body.click();
        expect(clicked).not.toHaveBeenCalled();
      } finally {
        document.elementFromPoint = was;
        document.body.removeEventListener("click", clicked);
        folder.remove();
      }
      wrapper.unmount();
    });

    it("drops on what a scroll put under the pointer, not what was there", async () => {
      const { wrapper, store } = await openFolder();
      const folder = document.createElement("div");
      folder.dataset.modelCopiesDrop = "";
      const drops = vi.fn();
      folder.addEventListener("model-copies-over", (event) => event.preventDefault());
      folder.addEventListener("model-copies-drop", drops);
      document.body.appendChild(folder);
      const was = document.elementFromPoint;
      let under = folder;
      document.elementFromPoint = () => under;
      try {
        pointer(wrapper.find(".shelf-row").element, "pointerdown");
        pointer(window, "pointermove", 20, 0);
        expect(store.dropTip).toBe(null);
        // The wheel scrolls the folder away from a pointer that stays put.
        under = document.body;
        document.dispatchEvent(new Event("scroll"));
        expect(store.dropTip.text).toBe("loose.safetensors");
        pointer(window, "pointerup", 20, 0);
        expect(drops).not.toHaveBeenCalled();
      } finally {
        document.elementFromPoint = was;
        folder.remove();
      }
      wrapper.unmount();
    });

    it("puts a drag back with Escape, keeping the selection", async () => {
      const { wrapper, store } = await selectLoose();
      pointer(wrapper.find(".shelf-row").element, "pointerdown");
      pointer(window, "pointermove", 10, 0);
      expect(store.dragCopies).not.toBe(null);
      await press("Escape");
      expect(store.dragCopies).toBe(null);
      expect(store.selectedRows.map((one) => one.id)).toEqual([1]);
      // Its release is still a click, which must not narrow the selection.
      const clicked = vi.fn();
      document.body.addEventListener("click", clicked);
      pointer(window, "pointerup", 10, 0);
      document.body.click();
      document.body.removeEventListener("click", clicked);
      expect(clicked).not.toHaveBeenCalled();
      wrapper.unmount();
    });

    it("drops nothing when the release was never heard", async () => {
      const { wrapper, store } = await openFolder();
      pointer(wrapper.find(".shelf-row").element, "pointerdown");
      pointer(window, "pointermove", 10, 0);
      expect(store.dragCopies).not.toBe(null);
      // A move with no button held: the release happened elsewhere.
      window.dispatchEvent(new MouseEvent("pointermove", { clientX: 30 }));
      expect(store.dragCopies).toBe(null);
      expect(store.dropTip).toBe(null);
      wrapper.unmount();
    });

    it("cuts and pastes from the row menu", async () => {
      const { wrapper, store } = await selectLoose();
      // Unmeasured, so the paste opens the dialog rather than starting.
      useModelFoldersStore().devices = [];
      const bar = wrapper.findComponent({ name: "ShelfSelectionBar" });
      const verb = (name) => bar.findAll(`.ctx-item[data-verb='${name}']`);
      // Paste is listed only while something is cut.
      expect(verb("paste").length).toBe(0);
      await verb("cut")[0].trigger("click");
      expect(store.cutCopies.items).toEqual([LOOSE]);

      store.setFolderScope({ folderId: 1, path: "Lora" });
      await flushPromises();
      expect(verb("paste")[0].attributes("disabled")).toBeUndefined();
      await verb("paste")[0].trigger("click");
      await flushPromises();
      expect(dialog(wrapper).props("open")).toBe(true);
      expect(dialog(wrapper).props("initialDestination")).toEqual({
        folderId: 1,
        path: "Lora",
      });
      dialog(wrapper).vm.$emit("started");
      expect(store.cutCopies).toBe(null);
      wrapper.unmount();
    });

    it("disables the menu's Paste outside a folder, and says why", async () => {
      const { wrapper, store } = await selectLoose();
      await press("x");
      store.setPlace("all");
      await flushPromises();
      const paste = wrapper
        .findComponent({ name: "ShelfSelectionBar" })
        .find(".ctx-item[data-verb='paste']");
      expect(paste.attributes("disabled")).toBeDefined();
      expect(tip(paste)).toContain("Open a folder to paste into it");
      wrapper.unmount();
    });

    it("refuses a paste into a locked folder, and a cut that has gone since", async () => {
      const { wrapper, store } = await selectLoose();
      await press("x");
      expect(store.pasteCut({ folderId: 1, path: "VAE", locked: true })).toBe(false);
      expect(useNoticeStore().notices.at(-1).text).toBe(
        "That folder is locked, so nothing can be moved into it.",
      );
      expect(store.cutCopies).not.toBe(null);

      store.cutCopies = { ...store.cutCopies, ids: new Set([999]) };
      expect(store.pasteCut({ folderId: 1, path: "VAE" })).toBe(false);
      expect(useNoticeStore().notices.at(-1).text).toBe(
        "What you cut has moved or gone since, so there is nothing to paste.",
      );
      expect(store.cutCopies).toBe(null);
      await flushPromises();
      expect(dialog(wrapper).props("open")).toBe(false);
      wrapper.unmount();
    });

    it("refuses to drag an unmovable row without touching the selection", async () => {
      listModelFolders.mockResolvedValue([FOLDER]);
      const wrapper = await mountShelf([
        adapter({ id: 1, locations: at("loose.safetensors") }),
        adapter({
          id: 2,
          sha256: "b".repeat(64),
          locations: [{ ...at("gone.safetensors")[0], state: "missing" }],
        }),
      ]);
      const store = useModelShelfStore();
      await flushPromises();
      store.toggleSelected(1);
      await flushPromises();
      const gone = wrapper
        .findAll(".shelf-row[data-row-key]")
        .find((one) => !one.classes("shelf-row--selected"));
      gone.element.dispatchEvent(
        new MouseEvent("pointerdown", { button: 0, bubbles: true }),
      );
      window.dispatchEvent(
        new MouseEvent("pointermove", { buttons: 1, clientX: 10 }),
      );
      expect(store.dragCopies).toBe(null);
      expect(store.selectedRows.map((one) => one.id)).toEqual([1]);
      // Says why, rather than the row simply refusing to lift.
      expect(useNoticeStore().notices.at(-1).text).toBe(
        "None of these files can be moved: they are missing, offline or in a locked folder.",
      );
      wrapper.unmount();
    });

    it("says on the row that its file is in a locked folder", async () => {
      // All models mixes rows that drag with rows that cannot; the file line
      // is where the reader can tell them apart before trying.
      listModelFolders.mockResolvedValue([
        FOLDER,
        { id: 2, path: "/hf", kind: "foreign", movable: "fixed" },
      ]);
      const wrapper = await mountShelf([
        adapter({ id: 1, locations: at("Loose_One.safetensors") }),
        adapter({
          id: 2,
          sha256: "b".repeat(64),
          locations: [
            { state: "present", folder_id: 2, folder_path: "/hf", relpath: "Held.safetensors" },
          ],
        }),
      ]);
      await flushPromises();
      const fileLine = (id) =>
        wrapper.find(`.shelf-row[data-row-key='${id}'] .shelf-row-file`).text();
      expect(fileLine(2)).toContain("in a locked folder");
      expect(fileLine(1)).not.toContain("locked");
      wrapper.unmount();
    });

    it("refuses a paste into the folder the cut files are already in", async () => {
      const { wrapper } = await selectLoose();
      await press("x");
      await press("v");
      expect(dialog(wrapper).props("open")).toBe(false);
      expect(useNoticeStore().notices.at(-1).text).toBe(
        "What you cut is already in that folder.",
      );
      wrapper.unmount();
    });
  });

  describe("where ComfyUI loads from (#2205)", () => {
    const READS = {
      known: true,
      reads: [{ folder_id: 1, subpath: "Lora", category: "loras", kind: "lora" }],
    };

    it("marks the folder ComfyUI loads from, and says a model outside it is not listed", async () => {
      getComfyuiReads.mockResolvedValue(READS);
      const { wrapper, store } = await openFolder();
      expect(getComfyuiReads).toHaveBeenCalledTimes(1);
      const row = wrapper.find(".shelf-row");
      expect(textOf(row.find("[data-testid='shelf-row-nolist']"))).toBe(
        "ComfyUI does not list it here",
      );
      const note = wrapper.find("[data-testid='shelf-misplaced-note']");
      expect(textOf(note)).toContain("1 model in this folder is outside the folders ComfyUI loads from");
      await note.find("[data-testid='shelf-select-misplaced']").trigger("click");
      expect(store.selectedRows.map((one) => one.id)).toEqual([1]);

      // Inside the folder ComfyUI loads LoRAs from, nothing to say.
      store.setFolderScope({ folderId: 1, path: "Lora" });
      await flushPromises();
      expect(wrapper.find("[data-testid='shelf-row-nolist']").exists()).toBe(false);
      expect(wrapper.find("[data-testid='shelf-misplaced-note']").exists()).toBe(false);
      wrapper.unmount();
    });

    it("draws no marks, lines or note while where ComfyUI loads from is unknown", async () => {
      getComfyuiReads.mockResolvedValue({ ...READS, known: false });
      const { wrapper } = await openFolder();
      expect(wrapper.find("[data-testid='comfyui-mark']").exists()).toBe(false);
      expect(wrapper.find("[data-testid='shelf-row-nolist']").exists()).toBe(false);
      expect(wrapper.find("[data-testid='shelf-misplaced-note']").exists()).toBe(false);
      wrapper.unmount();
    });

    it("says nothing about a locked folder, whose files cannot be dragged out", async () => {
      getComfyuiReads.mockResolvedValue(READS);
      const { wrapper, store } = await openFolder();
      expect(wrapper.find("[data-testid='shelf-row-nolist']").exists()).toBe(true);
      // Reopening the same folder is a no-op, so leave it first.
      store.setPlace("all");
      store.setFolderScope({ folderId: 1, path: "", locked: true });
      await flushPromises();
      expect(wrapper.find("[data-testid='shelf-row-nolist']").exists()).toBe(false);
      expect(wrapper.find("[data-testid='shelf-misplaced-note']").exists()).toBe(false);
      wrapper.unmount();
    });

    it("treats a refused read as unknown", async () => {
      const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
      getComfyuiReads.mockRejectedValue({ response: { status: 403 } });
      const { wrapper } = await openFolder();
      expect(wrapper.find("[data-testid='shelf-row-nolist']").exists()).toBe(false);
      expect(warn).toHaveBeenCalled();
      warn.mockRestore();
      wrapper.unmount();
    });
  });

  it("lists only the folder's own models: no subfolders, path or bands", async () => {
    // The Folders tree is where subfolders and the way back up live. The
    // root's listing names Lora and VAE, and no model is called VAE.
    const { wrapper, store } = await openFolder();
    expect(wrapper.findAll(".shelf-row").length).toBe(1);
    expect(textOf(wrapper)).not.toContain("VAE");
    expect(textOf(wrapper)).not.toContain("All folders");
    expect(textOf(wrapper.find(".shelf-sub"))).toBe("1 model");
    store.setView({ groupBy: "type", listGroupBy: "type" });
    await flushPromises();
    expect(wrapper.find(".shelf-group-heading").exists()).toBe(false);
    store.setPlace("all");
    await flushPromises();
    expect(wrapper.find(".shelf-group-heading").exists()).toBe(true);
    wrapper.unmount();
  });

  it("does not call a folder empty of subfolders while its listing is unread", async () => {
    const { wrapper } = await openFolder();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    listModelFolderDirs.mockRejectedValue({
      response: { status: 409, data: { detail: "Could not read the directory." } },
    });
    const store = useModelShelfStore();
    store.setFolderScope({ folderId: 1, path: "VAE" });
    await flushPromises();
    // An unread listing does not let the empty line claim there are none.
    expect(textOf(wrapper)).toContain("No models directly in this folder.");
    warn.mockRestore();
    wrapper.unmount();
  });

  it("states the drive's free space under the list", async () => {
    const { wrapper } = await openFolder();
    const status = textOf(wrapper.find("[data-testid='shelf-status']"));
    expect(status).toContain("System drive: 50.0 GB free of 1000.0 GB");
    expect(status).toContain("95% full");
    expect(
      wrapper.find(".shelf-status-bar--full").exists(),
    ).toBe(true);
    wrapper.unmount();
  });

});

describe("the sidebar's requests reach their dialog (#2168)", () => {
  it.each([
    ["add-file", "addFileOpen"],
    ["set-source", "addSourceOpen"],
  ])("opens its picker for %s", async (what, flag) => {
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    useModelShelfStore().requestShell(what, null);
    await wrapper.vm.$nextTick();
    expect(wrapper.vm[flag]).toBe(true);
    wrapper.unmount();
  });

  it.each(["add-folder", "manage-folders"])(
    "sends %s to Settings on Models",
    async (what) => {
      const wrapper = await mountShelf([adapter({ id: 1 })]);
      useModelShelfStore().requestShell(what, null);
      await wrapper.vm.$nextTick();
      expect(wrapper.emitted("open-settings")).toEqual([["models"]]);
      wrapper.unmount();
    },
  );
});

describe("going to a model by link (?model=)", () => {
  afterEach(() => {
    nav.route.query = {};
  });

  it("leaves Sets for All models and picks the row", async () => {
    // Sets draws the grid, which has no row to land on: Go to model from a
    // training run used to open there and do nothing.
    const wrapper = await mountShelf(
      [adapter({ id: 1 }), adapter({ id: 2 })],
      [],
      [],
      { attachTo: document.body },
    );
    const store = useModelShelfStore();
    store.setPlace("sets");
    await wrapper.vm.$nextTick();
    nav.route.query = { model: "2" };
    await flushPromises();
    expect(store.place).toBe("all");
    expect([...store.selectedIds]).toEqual([2]);
    const rows = wrapper.findAll(".shelf-row");
    expect(document.activeElement).toBe(rows[1].element);
    wrapper.unmount();
  });

  it("does not pin the reader to All models for a model it never finds", async () => {
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    const store = useModelShelfStore();
    nav.route.query = { model: "999" };
    await flushPromises();
    expect(store.place).toBe("all");
    store.setPlace("sets");
    // A refresh recomputes the drawn rows, which re-runs the link's watcher.
    store.rows = [...store.rows, adapter({ id: 3 })];
    await flushPromises();
    expect(store.place).toBe("sets");
    wrapper.unmount();
  });
});

describe("the list's Type, favourite and Sets columns (#2168)", () => {
  it("says an empty shelf is empty on In no set yet, not that all is in a set", async () => {
    const wrapper = await mountShelf([]);
    const store = useModelShelfStore();
    store.setsLoaded = true;
    store.setPlace("loose");
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("No models found.");
    expect(wrapper.text()).not.toContain("Every model is in a set.");
    wrapper.unmount();
  });

  it("says every model is in a set when the shelf has models and none is loose", async () => {
    const wrapper = await mountShelf([adapter({ id: 1 })]);
    const store = useModelShelfStore();
    store.workflowSets = {
      ...store.workflowSets,
      handMade: [{ id: 10, name: "Kit", members: [{ id: 1, on_shelf: true }] }],
    };
    store.setsLoaded = true;
    store.setPlace("loose");
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("Every model is in a set.");
    wrapper.unmount();
  });

  it("draws a row's picture: its own icon, else its person's face, else a mark", async () => {
    listCharacters.mockResolvedValue([{ id: 5, name: "Sarah" }]);
    const wrapper = await mountShelf([
      adapter({ id: 1, icon_sha256: "f".repeat(64) }),
      adapter({
        id: 2,
        sha256: "b".repeat(64),
        attachments: [{ entity_type: "character", entity_id: 5 }],
      }),
      adapter({ id: 3, sha256: "c".repeat(64) }),
    ]);
    await flushPromises();
    const marks = wrapper.findAll(".shelf-row .shelf-row-mark .mmark");
    expect(marks).toHaveLength(3);
    expect(marks[0].find("img").attributes("src")).toBe(
      modelIconUrl("f".repeat(64)),
    );
    expect(marks[1].find("img").attributes("src")).toBe(
      characterThumbnailUrl(5),
    );
    expect(marks[2].find(".mmark-initials").exists()).toBe(true);
    // #2168 took the assignment ring off the list; the face comes back alone.
    for (const mark of marks) {
      expect(mark.classes()).not.toContain("mmark--ring");
    }
    wrapper.unmount();
  });

  it("puts a set's icon on a row drawn before the sets arrived", async () => {
    // The rows are memoised, so a list landing after them must still repaint.
    let land;
    listPictureSets.mockReturnValue(new Promise((resolve) => (land = resolve)));
    const wrapper = await mountShelf([
      adapter({ attachments: [{ entity_type: "set", entity_id: 7 }] }),
    ]);
    land([{ id: 7, name: "Kit", set_icon: "mdi-star" }]);
    await flushPromises();
    const mark = wrapper.find(".shelf-row-mark .mmark");
    // The set's icon, not the thumbnail the icon replaced.
    expect(mark.find(".mmark-icon").exists()).toBe(true);
    expect(mark.find("img").exists()).toBe(false);
    wrapper.unmount();
  });

  it("draws a heart after the name of a favourite, and none on the rest", async () => {
    listFavourites.mockResolvedValue({
      model: ["a".repeat(64)],
      workflow: [],
      set: [],
    });
    const wrapper = await mountShelf([
      adapter({ id: 1 }),
      adapter({ id: 2, sha256: "c".repeat(64) }),
    ]);
    const rows = wrapper.findAll(".shelf-row");
    expect(rows[0].find(".shelf-row-heart").exists()).toBe(true);
    expect(rows[1].find(".shelf-row-heart").exists()).toBe(false);
    wrapper.unmount();
  });

  it("opens the Sets place, not just the grid, for a new set from a checkpoint", async () => {
    const wrapper = await mountShelf([adapter({ id: 1, file_kind: "checkpoint" })]);
    const store = useModelShelfStore();
    const create = vi.spyOn(store, "createHandMadeSet").mockResolvedValue(null);
    store.setPlace("all");
    store.toggleSelected(1);
    await wrapper.vm.$nextTick();
    wrapper.findComponent({ name: "ShelfSelectionBar" }).vm.$emit("new-set");
    await flushPromises();
    expect(create).toHaveBeenCalled();
    expect(store.place).toBe("sets");
    expect(store.view.groupBy).toBe("workflow_set");
    wrapper.unmount();
  });

  it("redraws only the row a selection touched, in a list banded by Type", async () => {
    // Two bands, because a memo nested in the groups' loop is what broke:
    // `v-memo` keeps one cache slot per list, so each band compared its rows
    // with the band before it and every row redrew on every click. A row
    // that was not redrawn keeps the very same rendered tree.
    const wrapper = await mountShelf([
      adapter({ id: 1 }),
      adapter({ id: 3, sha256: "c".repeat(64) }),
      adapter({ id: 2, sha256: "b".repeat(64), file_kind: "checkpoint" }),
    ]);
    const store = useModelShelfStore();
    store.setView({ groupBy: "type" });
    await flushPromises();
    expect(wrapper.findAll(".shelf-group").length).toBe(2);
    const trees = () =>
      new Map(
        wrapper
          .findAllComponents({ name: "MemoBlock" })
          .map((block) => [block.find(".shelf-row").attributes("data-row-key"), block.vm.$.subTree]),
      );
    const before = trees();
    expect([...before.keys()].sort()).toEqual(["1:lora", "2:checkpoint", "3:lora"]);

    store.toggleSelected(1);
    await flushPromises();
    const after = trees();
    // Booleans, because a failing vnode comparison cannot be printed.
    const kept = (key) => after.get(key) === before.get(key);
    expect(kept("1:lora")).toBe(false);
    expect(kept("2:checkpoint")).toBe(true);
    expect(kept("3:lora")).toBe(true);
    expect(wrapper.find(".shelf-row[data-row-key='1:lora']").classes()).toContain(
      "shelf-row--selected",
    );
    wrapper.unmount();
  });

  it("redraws a row when something outside it changes", async () => {
    // The list memoises each row (`rowMemo`), so a value read from outside
    // the row object only reaches the screen if the memo lists it. Each of
    // these changes after mount with the row object itself untouched.
    useUserPrefsStore().dateFormat = "eu";
    const wrapper = await mountShelf([
      adapter({
        id: 1,
        // Local noon, as the date column's own block builds its stamps.
        added_at: new Date(2026, 6, 30, 12, 0).toISOString().slice(0, 19),
        locations: [
          { state: "present", folder_id: 2, folder_path: "/hf", relpath: "Held.safetensors" },
        ],
      }),
    ]);
    // Favourites leading re-sorts, which rebuilds every row object and would
    // redraw the heart whether or not the memo lists it.
    useModelShelfStore().setView({ favouritesFirst: false });
    await flushPromises();
    const row = () => wrapper.find(".shelf-row");
    expect(row().find(".shelf-row-heart").exists()).toBe(false);
    expect(row().find(".shelf-row-file").text()).not.toContain("locked");

    // One at a time: changed together, any one listed value would redraw
    // the row for all of them.
    useFavouritesStore().keys = { model: ["a".repeat(64)], workflow: [], set: [] };
    await flushPromises();
    expect(row().find(".shelf-row-heart").exists()).toBe(true);
    useUserPrefsStore().dateFormat = "iso";
    await flushPromises();
    expect(row().find(".shelf-col--date").text()).toBe("2026-07-30");
    useModelFoldersStore().folders = [
      { id: 2, path: "/hf", kind: "foreign", movable: "fixed" },
    ];
    await flushPromises();
    expect(row().find(".shelf-row-file").text()).toContain("in a locked folder");
    wrapper.unmount();
  });

  it("counts the sets a row is in, and dashes a row that is in none", async () => {
    const wrapper = await mountShelf([
      adapter({ id: 1 }),
      adapter({ id: 2, sha256: "c".repeat(64) }),
    ]);
    const store = useModelShelfStore();
    store.workflowSets = {
      ...store.workflowSets,
      handMade: [{ id: 10, name: "Kit", members: [{ id: 1, on_shelf: true }] }],
    };
    await wrapper.vm.$nextTick();
    // Until the sets are read the column says nothing, not "in none".
    expect(wrapper.findAll(".shelf-row")[1].find(".shelf-col--sets").text()).toBe("");
    store.setsLoaded = true;
    await wrapper.vm.$nextTick();
    const rows = wrapper.findAll(".shelf-row");
    expect(rows[0].find(".shelf-col--sets").text()).toBe("1");
    expect(rows[1].find(".shelf-col--sets").text()).toBe("–");
    wrapper.unmount();
  });
});

describe("the folder removal dialog the tree asks for", () => {
  const SOURCE = { folderId: 1, path: "Lora", name: "Lora" };
  const PLAN = { file_count: 2, files: [], fingerprint: "fp", name: "Lora" };
  const dialog = (wrapper) =>
    wrapper.findComponent({ name: "ShelfRemoveFolderDialog" });

  it("answers the request on close: removed only after the dialog said so", async () => {
    const wrapper = await mountShelf([]);
    const store = useModelShelfStore();

    const cancelled = store.requestFolderRemove(SOURCE, PLAN, { remaining: 2 });
    await flushPromises();
    expect(dialog(wrapper).props("open")).toBe(true);
    expect(dialog(wrapper).props("remaining")).toBe(2);
    dialog(wrapper).vm.$emit("close");
    await expect(cancelled).resolves.toBe(false);
    expect(dialog(wrapper).props("open")).toBe(false);

    const removed = store.requestFolderRemove(SOURCE, PLAN);
    await flushPromises();
    dialog(wrapper).vm.$emit("removed");
    dialog(wrapper).vm.$emit("close");
    await expect(removed).resolves.toBe(true);
    wrapper.unmount();
  });

  it("leaves a request that arrives while the last dialog closes to its own dialog", async () => {
    const wrapper = await mountShelf([]);
    const store = useModelShelfStore();
    const first = store.requestFolderRemove(SOURCE, PLAN);
    await flushPromises();
    // The close waits a tick for the focus; the next request lands in it.
    dialog(wrapper).vm.$emit("close");
    const second = store.requestFolderRemove({ ...SOURCE, path: "VAE" }, PLAN);
    await expect(first).resolves.toBe(false);
    let answered = null;
    second.then((value) => (answered = value));
    await flushPromises();
    expect(answered).toBe(null);
    expect(dialog(wrapper).props("open")).toBe(true);
    expect(dialog(wrapper).props("source").path).toBe("VAE");
    dialog(wrapper).vm.$emit("removed");
    dialog(wrapper).vm.$emit("close");
    await expect(second).resolves.toBe(true);
    wrapper.unmount();
  });
});
