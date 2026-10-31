// The Folders tree (#2203): drives, registered folders and their subfolders,
// read one level per expand, walked with the tree pattern's keys.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const listModelFolderDirs = vi.fn();
const createModelFolderDir = vi.fn();
const moveModelFolderDir = vi.fn();
const removeModelFolderDir = vi.fn();
vi.mock("../../api/modelFolders", async (importOriginal) => ({
  ...(await importOriginal()),
  listModelFolderDirs: (...args) => listModelFolderDirs(...args),
  createModelFolderDir: (...args) => createModelFolderDir(...args),
  moveModelFolderDir: (...args) => moveModelFolderDir(...args),
  removeModelFolderDir: (...args) => removeModelFolderDir(...args),
}));
// The move job's watch is not under test here; its reads stay off the wire.
vi.mock("../../api/modelMoves", () => ({
  startModelMove: vi.fn(),
  getModelMoveStatus: vi.fn(async () => ({ status: "idle", results: [] })),
  cancelModelMove: vi.fn(),
  relocateModelFolder: vi.fn(),
}));

import ModelFolderTree from "./ModelFolderTree.vue";
import { comfyuiNameWords } from "../../composables/useModelCopyDrop";
import { FOLDER_TYPE } from "../../composables/useModelFolderMove";
import { useLibrariesStore } from "../../stores/useLibrariesStore";
import { useModelFoldersStore } from "../../stores/useModelFoldersStore";
import { useModelMovesStore } from "../../stores/useModelMovesStore";
import { useModelShelfStore } from "../../stores/useModelShelfStore";

const FOLDERS = [
  { id: 1, path: "/home/me/StabilityMatrix/Models", kind: "user", movable: "per_item" },
  { id: 2, path: "/home/me/.pixlstash/models", kind: "managed", movable: "root_only", tool: "PixlStash" },
  { id: 3, path: "/home/me/ai-toolkit/output", kind: "source", movable: "external" },
];
const DEVICES = [
  {
    device_id: "d1",
    mount_point: "/",
    label: "System drive",
    total_bytes: 1000,
    free_bytes: 60,
    folder_ids: [1, 2],
  },
];
const LISTINGS = {
  "1:": [
    { name: "Lora", path: "Lora", model_count: 46, direct_model_count: 42, bytes: 1, has_dirs: true },
    { name: "VAE", path: "VAE", model_count: 3, direct_model_count: 3, bytes: 1, has_dirs: false },
  ],
  "1:Lora": [
    { name: "flux", path: "Lora/flux", model_count: 4, direct_model_count: 4, bytes: 1, has_dirs: false },
  ],
};

// The menu is a Vuetify overlay, which needs an app instance this suite does
// not mount. The stub draws its content while open, so the rows are testable.
const VMenuStub = {
  props: ["modelValue", "target"],
  template: '<div v-if="modelValue"><slot /></div>',
};

function mountTree(props = {}) {
  return mount(ModelFolderTree, {
    props,
    attachTo: document.body,
    global: {
      stubs: { "v-icon": true, "v-menu": VMenuStub, Tooltip: true },
    },
  });
}

const item = (wrapper, folderId, path = "") =>
  wrapper.find(
    `[role="treeitem"][data-folder-id="${folderId}"][data-path="${path}"]`,
  );
const keys = (wrapper) =>
  wrapper.findAll('[role="treeitem"]').map((one) => one.attributes("data-key"));

beforeEach(() => {
  setActivePinia(createPinia());
  const folders = useModelFoldersStore();
  folders.folders = FOLDERS;
  folders.devices = DEVICES;
  listModelFolderDirs.mockReset().mockImplementation(async (id, path) => ({
    folder_id: id,
    path,
    locked: id === 2,
    model_count: id === 2 ? 5 : 52,
    direct_model_count: id === 2 ? 0 : 3,
    bytes: 0,
    dirs: id === 2 ? [] : (LISTINGS[`${id}:${path}`] ?? []),
  }));
});

describe("ModelFolderTree", () => {
  it("draws the drive, then the folders the shelf catalogues, with counts", async () => {
    const wrapper = mountTree();
    await flushPromises();
    expect(wrapper.find('[role="tree"]').exists()).toBe(true);
    // ai-toolkit's output root is taken from, never catalogued.
    expect(keys(wrapper)).toEqual(["drive:d1", "f:1:", "f:2:"]);
    const drive = wrapper.find('[data-key="drive:d1"]');
    expect(drive.attributes("aria-level")).toBe("1");
    expect(drive.text()).toContain("System drive");
    // 94% full: the free space and the meter take the warning colour.
    expect(drive.find(".mft-free--full").exists()).toBe(true);
    expect(drive.find(".mft-bar--full").exists()).toBe(true);
    expect(item(wrapper, 1).attributes("aria-level")).toBe("2");
    wrapper.unmount();
  });

  it("counts a folder's own models, and everything under a locked one", async () => {
    // What opening the row on the shelf lists: one level, or a locked folder
    // whole, since it cannot be browsed into.
    const wrapper = mountTree();
    await flushPromises();
    expect(item(wrapper, 1).find(".mft-count").text()).toBe("3");
    expect(item(wrapper, 2).find(".mft-count").text()).toBe("5");
    await item(wrapper, 1).find(".mft-twist").trigger("click");
    await flushPromises();
    expect(item(wrapper, 1, "Lora").find(".mft-count").text()).toBe("42");
    wrapper.unmount();
  });

  it("tells namesakes apart by where they are, never by renaming them", async () => {
    const folders = useModelFoldersStore();
    folders.folders = [
      ...FOLDERS,
      { id: 4, path: "/home/me/.insightface/models", kind: "foreign", movable: "root_only", tool: "PixlStash" },
      { id: 5, path: "/mnt/scratch/Forge-old/models", kind: "user", movable: "per_item" },
    ];
    folders.devices = [
      { ...DEVICES[0], folder_ids: [1, 2, 4] },
      { device_id: "d2", mount_point: "/mnt/scratch", label: "Scratch", total_bytes: 10, free_bytes: 5, folder_ids: [5] },
    ];
    const wrapper = mountTree();
    await flushPromises();
    const origin = (id) => item(wrapper, id).find('[data-testid="model-folder-origin"]').text();
    // A tool's folder says its whole path; a folder named for itself says its
    // parent, so the name is not said twice.
    expect(item(wrapper, 1).find(".mft-label").text()).toBe("Models");
    expect(origin(1)).toBe("~/StabilityMatrix");
    expect(item(wrapper, 2).find(".mft-label").text()).toBe("PixlStash");
    expect(origin(2)).toBe("~/.pixlstash/models");
    expect(item(wrapper, 4).find(".mft-label").text()).toBe("PixlStash");
    expect(origin(4)).toBe("~/.insightface/models");
    // From the drive row above it, which already names the drive.
    expect(origin(5)).toBe("Forge-old");
    expect(item(wrapper, 2).attributes("aria-label")).toContain("~/.pixlstash/models");
    // A subfolder is one line: the root above says where it is.
    await item(wrapper, 1).find(".mft-twist").trigger("click");
    await flushPromises();
    expect(item(wrapper, 1, "Lora").find('[data-testid="model-folder-origin"]').exists()).toBe(false);
    expect(item(wrapper, 1, "Lora").classes()).not.toContain("mft-row--two");
    expect(item(wrapper, 1).classes()).toContain("mft-row--two");
    wrapper.unmount();
  });

  it("draws the lock as the folder glyph, so nothing trails the name", async () => {
    const wrapper = mountTree();
    await flushPromises();
    const tail = (id) => item(wrapper, id).element.lastElementChild.className;
    // No lock trails the count, and the ⋯ is laid over it rather than after.
    expect(tail(1)).toBe("mft-more");
    expect(item(wrapper, 1).classes()).toContain("mft-row--verbs");
    expect(tail(2)).toBe("mft-count");
    expect(item(wrapper, 2).classes()).not.toContain("mft-row--verbs");
    expect(item(wrapper, 2).find("[data-testid='model-folder-lock']").classes()).toContain("mft-glyph");
    expect(item(wrapper, 1).find("[data-testid='model-folder-lock']").exists()).toBe(false);
    wrapper.unmount();
  });

  it("reads each folder's own level once, and a subfolder only on expand", async () => {
    const wrapper = mountTree();
    await flushPromises();
    expect(listModelFolderDirs.mock.calls).toEqual([
      [1, ""],
      [2, ""],
    ]);
    expect(item(wrapper, 1).attributes("aria-expanded")).toBe("false");
    await item(wrapper, 1).find(".mft-twist").trigger("click");
    await flushPromises();
    // The root was already read: expanding it asks for nothing new.
    expect(listModelFolderDirs).toHaveBeenCalledTimes(2);
    expect(item(wrapper, 1).attributes("aria-expanded")).toBe("true");
    expect(item(wrapper, 1, "Lora").attributes("aria-level")).toBe("3");
    // VAE has no subfolders, so it offers no expand.
    expect(item(wrapper, 1, "VAE").attributes("aria-expanded")).toBe(undefined);
    await item(wrapper, 1, "Lora").find(".mft-twist").trigger("click");
    await flushPromises();
    expect(listModelFolderDirs).toHaveBeenLastCalledWith(1, "Lora");
    expect(item(wrapper, 1, "Lora/flux").attributes("aria-level")).toBe("4");
    wrapper.unmount();
  });

  it("draws a locked folder with its lock, never expandable, and opens it whole", async () => {
    const wrapper = mountTree();
    // Not even before its listing answers, when an unread folder is offered.
    expect(item(wrapper, 1).attributes("aria-expanded")).toBe("false");
    expect(item(wrapper, 2).attributes("aria-expanded")).toBe(undefined);
    await flushPromises();
    const locked = item(wrapper, 2);
    expect(locked.find("[data-testid='model-folder-lock']").exists()).toBe(true);
    expect(locked.attributes("aria-expanded")).toBe(undefined);
    expect(locked.attributes("aria-label")).toContain("locked");
    await locked.find(".mft-twist").trigger("click");
    expect(keys(wrapper)).toEqual(["drive:d1", "f:1:", "f:2:"]);
    await locked.trigger("click");
    expect(wrapper.emitted("select")).toEqual([
      [{ folderId: 2, path: "", locked: true }],
    ]);
    wrapper.unmount();
  });

  it("refuses a locked folder in pick mode", async () => {
    const wrapper = mountTree({ mode: "pick" });
    await flushPromises();
    expect(item(wrapper, 2).attributes("aria-disabled")).toBe("true");
    await item(wrapper, 2).trigger("click");
    expect(wrapper.emitted("select")).toBe(undefined);
    await item(wrapper, 1).trigger("click");
    expect(wrapper.emitted("select")).toEqual([
      [{ folderId: 1, path: "", locked: false }],
    ]);
    wrapper.unmount();
  });

  it("walks with the arrows: down, right to expand and enter, left to the parent", async () => {
    const wrapper = mountTree();
    await flushPromises();
    // One tab stop, the first row while nothing is open.
    const stops = wrapper
      .findAll('[role="treeitem"]')
      .filter((one) => one.attributes("tabindex") === "0");
    expect(stops.map((one) => one.attributes("data-key"))).toEqual(["drive:d1"]);
    const tree = wrapper.find('[role="tree"]');
    await tree.trigger("keydown", { key: "ArrowDown" });
    expect(document.activeElement).toBe(item(wrapper, 1).element);
    await tree.trigger("keydown", { key: "ArrowRight" });
    await flushPromises();
    expect(item(wrapper, 1).attributes("aria-expanded")).toBe("true");
    await tree.trigger("keydown", { key: "ArrowRight" });
    expect(document.activeElement).toBe(item(wrapper, 1, "Lora").element);
    // From VAE, Left goes to its parent, not to Lora above it.
    await tree.trigger("keydown", { key: "ArrowDown" });
    expect(document.activeElement).toBe(item(wrapper, 1, "VAE").element);
    await tree.trigger("keydown", { key: "ArrowLeft" });
    expect(document.activeElement).toBe(item(wrapper, 1).element);
    await tree.trigger("keydown", { key: "ArrowLeft" });
    expect(item(wrapper, 1).attributes("aria-expanded")).toBe("false");
    await tree.trigger("keydown", { key: "End" });
    expect(document.activeElement).toBe(item(wrapper, 2).element);
    await tree.trigger("keydown", { key: "Enter" });
    expect(wrapper.emitted("select")).toEqual([
      [{ folderId: 2, path: "", locked: true }],
    ]);
    await tree.trigger("keydown", { key: "Home" });
    expect(document.activeElement).toBe(
      wrapper.find('[data-key="drive:d1"]').element,
    );
    // Left on an open drive closes it, and its folders go with it.
    await tree.trigger("keydown", { key: "ArrowLeft" });
    expect(keys(wrapper)).toEqual(["drive:d1"]);
    wrapper.unmount();
  });

  it("opens the way down to the selected subfolder and lights it", async () => {
    const wrapper = mountTree({ selected: { folderId: 1, path: "Lora/flux" } });
    await flushPromises();
    const lit = item(wrapper, 1, "Lora/flux");
    expect(lit.attributes("aria-selected")).toBe("true");
    expect(lit.attributes("tabindex")).toBe("0");
    expect(item(wrapper, 1).attributes("aria-selected")).toBe("false");
    wrapper.unmount();
  });

  it("offers no subfolders when the listing is refused", async () => {
    listModelFolderDirs.mockRejectedValue(new Error("403"));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const wrapper = mountTree();
    await flushPromises();
    expect(item(wrapper, 1).attributes("aria-expanded")).toBe(undefined);
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
    wrapper.unmount();
  });
});

describe("ModelFolderTree as a drop target (#2204)", () => {
  // The shelf drags with the pointer and sends the row under it these events.
  function drag(el, type) {
    const event = new CustomEvent(`model-copies-${type}`, {
      cancelable: true,
      detail: { x: 5, y: 6 },
    });
    el.element.dispatchEvent(event);
    return event;
  }

  it("takes dragged model rows on a folder and refuses a drive, a locked folder and their own folder", async () => {
    const shelf = useModelShelfStore();
    const copies = {
      items: [{ folder_id: 1, relpath: "alice.safetensors" }],
      totalBytes: 10,
      bytesByFolderId: new Map([[1, 10]]),
    };
    shelf.dragCopies = copies;
    const wrapper = mountTree();
    await flushPromises();
    await item(wrapper, 1).find(".mft-twist").trigger("click");
    await flushPromises();

    const lora = item(wrapper, 1, "Lora");
    expect(lora.attributes("data-model-copies-drop")).toBe("");
    const over = drag(lora, "over");
    await flushPromises();
    expect(over.defaultPrevented).toBe(true);
    expect(lora.classes()).toContain("drop-ring");
    // Same drive: the tip says rename, and names where they go.
    expect(shelf.dropTip.text).toContain("Move to Models/Lora");
    expect(shelf.dropTip.text).toContain("Renamed");
    expect(shelf.dropTip).toMatchObject({ x: 5, y: 6 });
    drag(lora, "leave");
    await flushPromises();
    expect(lora.classes()).not.toContain("drop-ring");

    for (const refused of [
      wrapper.find('[data-key="drive:d1"]'),
      item(wrapper, 2),
      item(wrapper, 1),
    ]) {
      // Each refused drop ends that drag; the next is a new one.
      shelf.dragCopies = copies;
      const event = drag(refused, "over");
      await flushPromises();
      expect(event.defaultPrevented).toBe(false);
      expect(refused.classes()).toContain("not-droppable");
      drag(refused, "drop");
      expect(shelf.moveRequest).toBe(null);
    }

    shelf.dragCopies = copies;
    drag(lora, "over");
    drag(lora, "drop");
    expect(shelf.moveRequest.destination).toEqual({ folderId: 1, path: "Lora" });
    expect(shelf.moveRequest.copies).toBe(copies);
    expect(shelf.dragCopies).toBe(null);
    wrapper.unmount();
  });

  it("takes no drop in pick mode", async () => {
    useModelShelfStore().dragCopies = {
      items: [{ folder_id: 1, relpath: "Lora/alice.safetensors" }],
      bytesByFolderId: new Map(),
    };
    const wrapper = mountTree({ mode: "pick" });
    await flushPromises();
    // Unmarked, so the shelf never finds it under the pointer.
    expect(item(wrapper, 1).attributes("data-model-copies-drop")).toBeUndefined();
    wrapper.unmount();
  });
});


describe("ModelFolderTree and where ComfyUI loads from (#2205)", () => {
  const READS = {
    known: true,
    reads: [
      { folder_id: 1, subpath: "Lora", category: "loras", kind: "lora" },
      { folder_id: 1, subpath: "VAE", category: "vae", kind: "vae" },
    ],
  };
  function dragOver(el) {
    el.element.dispatchEvent(
      new CustomEvent("model-copies-over", {
        cancelable: true,
        detail: { x: 0, y: 0 },
      }),
    );
  }

  async function expanded() {
    const wrapper = mountTree();
    await flushPromises();
    await item(wrapper, 1).find(".mft-twist").trigger("click");
    await flushPromises();
    return wrapper;
  }

  const marks = (row) =>
    row.findAll('[data-testid="comfyui-mark"]').map((one) => one.text());

  it("marks each folder with what ComfyUI loads from it, and says so", async () => {
    useModelFoldersStore().comfyuiReads = READS;
    const wrapper = await expanded();
    expect(marks(item(wrapper, 1, "Lora"))).toEqual(["L"]);
    expect(marks(item(wrapper, 1, "VAE"))).toEqual(["V"]);
    expect(marks(item(wrapper, 1))).toEqual([]);
    expect(item(wrapper, 1, "Lora").attributes("aria-label")).toContain(
      "ComfyUI loads LoRAs from here",
    );
    wrapper.unmount();
  });

  it("draws no marks while where ComfyUI loads from is not known", async () => {
    useModelFoldersStore().comfyuiReads = { ...READS, known: false };
    const wrapper = await expanded();
    expect(wrapper.findAll('[data-testid="comfyui-mark"]')).toHaveLength(0);
    wrapper.unmount();
  });

  it("names the new ComfyUI name in the drop tip when a move changes it", async () => {
    useModelFoldersStore().comfyuiReads = READS;
    const shelf = useModelShelfStore();
    const wrapper = await expanded();
    await item(wrapper, 1, "Lora").find(".mft-twist").trigger("click");
    await flushPromises();
    const copy = { folder_id: 1, relpath: "Lora/Neon.safetensors" };
    const loose = { folder_id: 1, relpath: "Ink.safetensors" };
    const kinds = new Map([
      ["1\u0000Lora/Neon.safetensors", "lora"],
      ["1\u0000Ink.safetensors", "lora"],
    ]);
    const drag = (items) => ({
      items,
      totalBytes: 1,
      bytesByFolderId: new Map([[1, 1]]),
      kinds,
    });

    shelf.dragCopies = drag([copy]);
    dragOver(item(wrapper, 1, "Lora/flux"));
    expect(shelf.dropTip.text).toContain(
      "ComfyUI: Neon.safetensors → flux/Neon.safetensors",
    );
    // Out of every LoRA folder: ComfyUI stops listing it.
    shelf.dragCopies = drag([copy]);
    dragOver(item(wrapper, 1, "VAE"));
    expect(shelf.dropTip.text).toContain(
      "ComfyUI will no longer list Neon.safetensors",
    );
    // From the root into Lora, the fix the design is for.
    shelf.dragCopies = drag([loose]);
    dragOver(item(wrapper, 1, "Lora"));
    expect(shelf.dropTip.text).toContain("ComfyUI will list Ink.safetensors");

    // Unknown: the tip says nothing about ComfyUI.
    useModelFoldersStore().comfyuiReads = { ...READS, known: false };
    shelf.dragCopies = drag([copy]);
    dragOver(item(wrapper, 1, "Lora/flux"));
    expect(shelf.dropTip.text).not.toContain("ComfyUI");
    wrapper.unmount();
  });
});

describe("comfyuiNameWords (#2205)", () => {
  const reads = {
    known: true,
    reads: [{ folder_id: 1, subpath: "Lora", category: "loras", kind: "lora" }],
  };
  const copies = (...relpaths) => ({
    items: relpaths.map((relpath) => ({ folder_id: 1, relpath })),
    kinds: new Map(relpaths.map((relpath) => [`1\u0000${relpath}`, "lora"])),
  });

  it("counts the other copies whose name changes", () => {
    expect(
      comfyuiNameWords(
        copies("Lora/a.st", "Lora/b.st", "Lora/c.st"),
        { folderId: 1, path: "Lora/flux" },
        reads,
      ),
    ).toBe("ComfyUI: a.st → flux/a.st (and 2 more)");
  });

  it("says when ComfyUI stops listing them, and nothing when no name changes", () => {
    expect(
      comfyuiNameWords(copies("Lora/a.st", "Lora/b.st"), { folderId: 1, path: "" }, reads),
    ).toBe("ComfyUI will no longer list a.st (and 1 more)");
    expect(
      comfyuiNameWords(copies("x/a.st"), { folderId: 1, path: "y" }, reads),
    ).toBe("");
    expect(
      comfyuiNameWords(copies("Lora/a.st"), { folderId: 1, path: "" }, {
        ...reads,
        known: false,
      }),
    ).toBe("");
  });
});

describe("the folder verbs (#2206)", () => {
  const dragEvent = (type, types, extra = {}) => {
    const event = new Event(type, { bubbles: true, cancelable: true });
    event.dataTransfer = {
      types,
      dropEffect: "",
      effectAllowed: "",
      setData: vi.fn(),
      ...extra,
    };
    return event;
  };
  const rightClick = (el) =>
    el.element.dispatchEvent(
      new MouseEvent("contextmenu", {
        bubbles: true,
        cancelable: true,
        clientX: 10,
        clientY: 20,
      }),
    );
  const menu = (wrapper) => wrapper.find('[data-testid="model-folder-menu"]');
  const verbs = (wrapper) =>
    menu(wrapper)
      .findAll('[role="menuitem"]')
      .map((one) => one.attributes("data-verb"));
  const field = (wrapper) => wrapper.find('[data-testid="model-folder-name"]');
  const LORA_LISTING = LISTINGS["1:Lora"];
  // A plan the dry run answers, with its facts as the server states them.
  const plan = (sameDrive) => ({
    dry_run: true,
    same_drive: sameDrive,
    models: 21,
    files: 25,
    other_files: 4,
    bytes: 4_600_000_000,
    folder_id: 1,
    path: "Lora",
    destination_folder_id: 1,
    destination_path: "VAE",
    name: "Lora",
  });
  const started = { status: "running", kind: "folder", total: 1, done: 0, results: [], name: "Lora" };

  beforeEach(() => {
    useLibrariesStore().canManage = true;
    createModelFolderDir.mockReset();
    moveModelFolderDir.mockReset();
    removeModelFolderDir.mockReset();
  });

  afterEach(() => {
    LISTINGS["1:Lora"] = LORA_LISTING;
    useModelMovesStore().resetForSession();
    useModelFoldersStore().resetForSession();
    useModelShelfStore().folderMoveRequest = null;
    useModelShelfStore().folderRemoveRequest = null;
  });

  async function openLora() {
    const wrapper = mountTree();
    await flushPromises();
    await item(wrapper, 1).find(".mft-twist").trigger("click");
    await flushPromises();
    return wrapper;
  }

  it("gives a subfolder New folder, Move, Rename, a rule, and Remove", async () => {
    const wrapper = await openLora();
    expect(item(wrapper, 1, "Lora").find('[data-testid="model-folder-more"]').exists()).toBe(true);
    rightClick(item(wrapper, 1, "Lora"));
    await flushPromises();
    expect(verbs(wrapper)).toEqual(["new", "move", "rename", "remove"]);
    expect(menu(wrapper).find('[role="separator"]').exists()).toBe(true);
    expect(menu(wrapper).find('[data-verb="rename"]').text()).toContain("F2");
    expect(menu(wrapper).find('[data-verb="new"]').text()).toContain("Ctrl Shift N");
    const remove = menu(wrapper).find('[data-verb="remove"]');
    expect(remove.attributes("aria-disabled")).not.toBe("true");
    expect(remove.text()).toContain("Del");
    wrapper.unmount();
  });

  it("gives a registered root New folder and Stop watching, and no Move, Rename or Remove", async () => {
    const wrapper = mountTree();
    await flushPromises();
    rightClick(item(wrapper, 1));
    await flushPromises();
    expect(verbs(wrapper)).toEqual(["new", "stop"]);
    await menu(wrapper).find('[data-verb="stop"]').trigger("click");
    expect(useModelShelfStore().shellRequest.what).toBe("manage-folders");
    wrapper.unmount();
  });

  it("gives a locked folder its lock and no menu at all", async () => {
    const wrapper = mountTree();
    await flushPromises();
    const locked = item(wrapper, 2);
    expect(locked.find('[data-testid="model-folder-lock"]').exists()).toBe(true);
    expect(locked.find('[data-testid="model-folder-more"]').exists()).toBe(false);
    const event = new MouseEvent("contextmenu", { bubbles: true, cancelable: true });
    locked.element.dispatchEvent(event);
    await flushPromises();
    expect(event.defaultPrevented).toBe(false);
    expect(menu(wrapper).exists()).toBe(false);
    wrapper.unmount();
  });

  it("leads a folder's menu with Paste here while models are cut, and pastes there", async () => {
    const shelf = useModelShelfStore();
    const paste = vi.spyOn(shelf, "pasteCut").mockReturnValue(true);
    shelf.cutCopies = {
      items: [{ folder_id: 1, relpath: "Lora/alice.safetensors" }],
      ids: new Set([1]),
    };
    const wrapper = await openLora();
    rightClick(item(wrapper, 1, "VAE"));
    await flushPromises();
    expect(verbs(wrapper)).toEqual(["paste", "new", "move", "rename", "remove"]);
    expect(menu(wrapper).find('[data-verb="paste"]').text()).toContain("Ctrl V");
    await menu(wrapper).find('[data-verb="paste"]').trigger("click");
    expect(paste).toHaveBeenCalledWith({ folderId: 1, path: "VAE", locked: false });

    // The folder the cut files are already in says so, and pastes nothing.
    paste.mockClear();
    rightClick(item(wrapper, 1, "Lora"));
    await flushPromises();
    const refused = menu(wrapper).find('[data-verb="paste"]');
    expect(refused.attributes("aria-disabled")).toBe("true");
    await refused.trigger("click");
    expect(paste).not.toHaveBeenCalled();
    wrapper.unmount();
    shelf.cutCopies = null;
  });

  it("pastes with Ctrl V on a folder row, a locked one included, and only while models are cut", async () => {
    const shelf = useModelShelfStore();
    const paste = vi.spyOn(shelf, "pasteCut").mockReturnValue(true);
    const wrapper = await openLora();
    const ctrlV = async (el) => {
      el.element.focus();
      await el.trigger("keydown", { key: "v", ctrlKey: true });
    };
    await ctrlV(item(wrapper, 1, "VAE"));
    expect(paste).not.toHaveBeenCalled();

    shelf.cutCopies = {
      items: [{ folder_id: 1, relpath: "Lora/alice.safetensors" }],
      ids: new Set([1]),
    };
    await ctrlV(item(wrapper, 1, "VAE"));
    expect(paste).toHaveBeenLastCalledWith({ folderId: 1, path: "VAE", locked: false });
    // A locked folder has no menu; the store refuses it with its reason.
    await ctrlV(item(wrapper, 2));
    expect(paste).toHaveBeenLastCalledWith({ folderId: 2, path: "", locked: true });
    wrapper.unmount();
    shelf.cutCopies = null;
  });

  it("offers no menu in pick mode, where the rows only choose", async () => {
    const wrapper = mountTree({ mode: "pick" });
    await flushPromises();
    rightClick(item(wrapper, 1));
    await flushPromises();
    expect(menu(wrapper).exists()).toBe(false);
    wrapper.unmount();
  });

  it("refuses each verb with its reason: the remote tier, a move running, a scan", async () => {
    const wrapper = await openLora();
    const lora = () => item(wrapper, 1, "Lora");

    useLibrariesStore().canManage = false;
    rightClick(lora());
    await flushPromises();
    expect(menu(wrapper).find('[data-verb="new"]').attributes("aria-disabled")).toBe("true");
    expect(menu(wrapper).find('[data-verb="rename"]').attributes("aria-disabled")).toBe("true");
    expect(wrapper.html()).toContain("Only available on the machine running PixlStash");
    useLibrariesStore().canManage = true;

    useModelMovesStore().job = { ...started, status: "running" };
    rightClick(lora());
    await flushPromises();
    expect(menu(wrapper).find('[data-verb="move"]').attributes("aria-disabled")).toBe("true");
    expect(wrapper.html()).toContain("A move is already running.");
    useModelMovesStore().job = null;

    const folders = useModelFoldersStore();
    folders.folders = folders.folders.map((one) =>
      one.id === 1 ? { ...one, scan_status: "running" } : one,
    );
    rightClick(lora());
    await flushPromises();
    expect(menu(wrapper).find('[data-verb="rename"]').attributes("aria-disabled")).toBe("true");
    expect(wrapper.html()).toContain("This folder is being scanned right now.");
    wrapper.unmount();
  });

  it("closes a name field whose row is collapsed away, so folders drag again", async () => {
    const wrapper = await openLora();
    rightClick(item(wrapper, 1, "Lora"));
    await flushPromises();
    await menu(wrapper).find('[data-verb="rename"]').trigger("click");
    await flushPromises();
    expect(field(wrapper).exists()).toBe(true);
    expect(item(wrapper, 1, "Lora").attributes("draggable")).toBeUndefined();

    await item(wrapper, 1).find(".mft-twist").trigger("click");
    await flushPromises();
    await item(wrapper, 1).find(".mft-twist").trigger("click");
    await flushPromises();
    expect(field(wrapper).exists()).toBe(false);
    expect(item(wrapper, 1, "Lora").attributes("draggable")).toBe("true");
    wrapper.unmount();
  });

  it("makes a folder from its name field, and lists and focuses it where it was made", async () => {
    const wrapper = await openLora();
    createModelFolderDir.mockImplementation(async (id, body) => {
      LISTINGS["1:Lora"] = [
        ...LORA_LISTING,
        { name: body.name, path: `Lora/${body.name}`, model_count: 0, bytes: 0, has_dirs: false },
      ];
      return { folder_id: id, path: `Lora/${body.name}` };
    });
    rightClick(item(wrapper, 1, "Lora"));
    await flushPromises();
    await menu(wrapper).find('[data-verb="new"]').trigger("click");
    await flushPromises();
    expect(field(wrapper).exists()).toBe(true);
    expect(field(wrapper).attributes("aria-label")).toBe("New folder in Lora");
    expect(document.activeElement).toBe(field(wrapper).element);

    await field(wrapper).setValue("sdxl");
    await field(wrapper).trigger("keydown", { key: "Enter" });
    await flushPromises();
    expect(createModelFolderDir).toHaveBeenCalledWith(1, { path: "Lora", name: "sdxl" });
    expect(field(wrapper).exists()).toBe(false);
    expect(item(wrapper, 1, "Lora/sdxl").exists()).toBe(true);
    expect(document.activeElement).toBe(item(wrapper, 1, "Lora/sdxl").element);
    wrapper.unmount();
  });

  it("keeps the field open under the server's refusal, and Escape closes it without asking", async () => {
    const wrapper = await openLora();
    rightClick(item(wrapper, 1, "Lora"));
    await flushPromises();
    await menu(wrapper).find('[data-verb="new"]').trigger("click");
    await flushPromises();
    createModelFolderDir.mockRejectedValueOnce({
      response: { data: { detail: "'flux' already exists there." } },
    });
    await field(wrapper).setValue("flux");
    await field(wrapper).trigger("keydown", { key: "Enter" });
    await flushPromises();
    expect(field(wrapper).exists()).toBe(true);
    expect(field(wrapper).element.value).toBe("flux");
    expect(wrapper.find('[role="alert"]').text()).toContain("already exists there");

    await field(wrapper).trigger("keydown", { key: "Escape" });
    await flushPromises();
    expect(field(wrapper).exists()).toBe(false);
    expect(createModelFolderDir).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });

  it("renames from F2 with the old name selected, and a refusal stays under the field", async () => {
    const wrapper = await openLora();
    item(wrapper, 1, "Lora").element.focus();
    await item(wrapper, 1, "Lora").trigger("keydown", { key: "F2" });
    await flushPromises();
    expect(field(wrapper).element.value).toBe("Lora");
    expect(field(wrapper).attributes("aria-label")).toBe("Rename Lora");

    moveModelFolderDir.mockRejectedValueOnce({
      response: { data: { detail: "'Lora2' already exists there." } },
    });
    await field(wrapper).setValue("Lora2");
    await field(wrapper).trigger("keydown", { key: "Enter" });
    await flushPromises();
    expect(moveModelFolderDir).toHaveBeenCalledWith(1, {
      path: "Lora",
      destinationFolderId: 1,
      destinationPath: "",
      newName: "Lora2",
    });
    expect(field(wrapper).exists()).toBe(true);
    expect(wrapper.find('[role="alert"]').text()).toContain("already exists there");
    wrapper.unmount();
  });

  it("leaves the reader's next field alone when an earlier create answers late", async () => {
    const wrapper = await openLora();
    let answer;
    createModelFolderDir.mockReturnValueOnce(
      new Promise((resolve) => {
        answer = resolve;
      }),
    );
    item(wrapper, 1, "Lora").element.focus();
    await item(wrapper, 1, "Lora").trigger("keydown", { key: "N", ctrlKey: true, shiftKey: true });
    await flushPromises();
    await field(wrapper).setValue("flux2");
    await field(wrapper).trigger("keydown", { key: "Enter" });
    await field(wrapper).trigger("keydown", { key: "Escape" });
    await flushPromises();
    await item(wrapper, 1, "Lora").trigger("keydown", { key: "F2" });
    await flushPromises();
    expect(field(wrapper).attributes("aria-label")).toBe("Rename Lora");

    answer({ folder_id: 1, path: "Lora/flux2" });
    await flushPromises();
    expect(field(wrapper).exists()).toBe(true);
    expect(field(wrapper).attributes("aria-label")).toBe("Rename Lora");
    wrapper.unmount();
  });

  it("focuses a revealed folder once the listings above it have landed", async () => {
    const wrapper = mountTree();
    await flushPromises();
    // Nothing under folder 1 is loaded yet; the listings answer late.
    const late = [];
    listModelFolderDirs.mockImplementation(
      (id, path) =>
        new Promise((resolve) =>
          late.push(() =>
            resolve({
              folder_id: id,
              path,
              locked: false,
              model_count: 1,
              bytes: 0,
              dirs: LISTINGS[`${id}:${path}`] ?? [],
            }),
          ),
        ),
    );
    wrapper.vm.reveal(1, "Lora", "Lora/flux");
    await flushPromises();
    late.forEach((answer) => answer());
    await flushPromises();
    expect(document.activeElement).toBe(item(wrapper, 1, "Lora/flux").element);
    wrapper.unmount();
  });

  it("keeps a renamed folder open and focused once its job lands", async () => {
    const root = LISTINGS["1:"];
    try {
      const wrapper = await openLora();
      await item(wrapper, 1, "Lora").find(".mft-twist").trigger("click");
      await flushPromises();
      expect(keys(wrapper)).toContain("f:1:Lora/flux");

      item(wrapper, 1, "Lora").element.focus();
      await item(wrapper, 1, "Lora").trigger("keydown", { key: "F2" });
      await flushPromises();
      moveModelFolderDir.mockResolvedValueOnce(started);
      await field(wrapper).setValue("Lora2");
      await field(wrapper).trigger("keydown", { key: "Enter" });
      await flushPromises();

      // The rename lands: the server's listings now name it, the job finishes
      // and the store re-reads what is loaded, as the poll does.
      LISTINGS["1:"] = [{ ...root[0], name: "Lora2", path: "Lora2" }, root[1]];
      LISTINGS["1:Lora2"] = [{ ...LORA_LISTING[0], path: "Lora2/flux" }];
      useModelMovesStore().job = {
        ...started,
        status: "finished",
        results: [{ folder_id: 1, relpath: "Lora", status: "moved", detail: null }],
        name: "Lora2",
      };
      useModelFoldersStore().reloadDirs();
      await flushPromises();

      expect(keys(wrapper)).toContain("f:1:Lora2/flux");
      expect(keys(wrapper)).not.toContain("f:1:Lora/flux");
      expect(document.activeElement).toBe(item(wrapper, 1, "Lora2").element);
      wrapper.unmount();
    } finally {
      LISTINGS["1:"] = root;
      delete LISTINGS["1:Lora2"];
    }
  });

  it("takes F2 and Ctrl+Shift+N on a row, so nothing reaches the shelf's window keys", async () => {
    const wrapper = await openLora();
    const seen = vi.fn();
    window.addEventListener("keydown", seen);
    try {
      item(wrapper, 1, "Lora").element.focus();
      await item(wrapper, 1, "Lora").trigger("keydown", { key: "F2" });
      await flushPromises();
      expect(field(wrapper).attributes("aria-label")).toBe("Rename Lora");
      await field(wrapper).trigger("keydown", { key: "Escape" });
      await flushPromises();

      await item(wrapper, 1, "Lora").trigger("keydown", {
        key: "N",
        ctrlKey: true,
        shiftKey: true,
      });
      await flushPromises();
      expect(field(wrapper).attributes("aria-label")).toBe("New folder in Lora");
      expect(seen).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener("keydown", seen);
    }
    wrapper.unmount();
  });

  // #2207. An empty folder has nothing to show, so it goes at once.
  it("removes an empty folder at once, with the dry run's fingerprint and no dialog", async () => {
    const wrapper = await openLora();
    removeModelFolderDir.mockImplementation(async (id, body) =>
      body.dryRun
        ? { file_count: 0, files: [], fingerprint: "fp-empty", name: "VAE" }
        : { files_removed: 0, removed_models: [], permanent: false, trash_name: "Trash" },
    );
    rightClick(item(wrapper, 1, "VAE"));
    await flushPromises();
    await menu(wrapper).find('[data-verb="remove"]').trigger("click");
    await flushPromises();
    expect(removeModelFolderDir.mock.calls).toEqual([
      [1, { path: "VAE", dryRun: true }],
      [1, { path: "VAE", permanent: false, expect: "fp-empty" }],
    ]);
    expect(useModelShelfStore().folderRemoveRequest).toBe(null);
    wrapper.unmount();
  });

  it("deletes an empty folder permanently on Shift+Del, and trashes it on Del", async () => {
    const wrapper = await openLora();
    removeModelFolderDir.mockImplementation(async (id, body) =>
      body.dryRun
        ? { file_count: 0, files: [], fingerprint: "fp-empty", name: "VAE" }
        : { files_removed: 0, removed_models: [], permanent: body.permanent, trash_name: "Trash" },
    );
    item(wrapper, 1, "VAE").element.focus();
    await item(wrapper, 1, "VAE").trigger("keydown", { key: "Delete", shiftKey: true });
    await flushPromises();
    await item(wrapper, 1, "VAE").trigger("keydown", { key: "Delete" });
    await flushPromises();
    const removals = removeModelFolderDir.mock.calls.filter(([, body]) => !body.dryRun);
    expect(removals.map(([, body]) => body.permanent)).toEqual([true, false]);
    wrapper.unmount();
  });

  it("asks the shelf for the dialog when the folder holds files", async () => {
    const wrapper = await openLora();
    const read = { file_count: 2, files: [], fingerprint: "fp", name: "VAE" };
    removeModelFolderDir.mockResolvedValue(read);
    rightClick(item(wrapper, 1, "VAE"));
    await flushPromises();
    await menu(wrapper).find('[data-verb="remove"]').trigger("click");
    await flushPromises();
    expect(removeModelFolderDir).toHaveBeenCalledTimes(1);
    const request = useModelShelfStore().folderRemoveRequest;
    expect(request.source).toEqual({ folderId: 1, path: "VAE", name: "VAE" });
    expect(request.plan).toBe(read);
    wrapper.unmount();
  });

  // Del on a row is the folder's, even with models selected on the shelf: it
  // never reaches the window, where the shelf's file delete listens.
  it("takes Del on a focused row as Remove folder, with models selected", async () => {
    const wrapper = await openLora();
    useModelShelfStore().selectedIds = new Set([1, 2]);
    removeModelFolderDir.mockResolvedValue({ file_count: 3, files: [], fingerprint: "fp", name: "Lora" });
    const seen = vi.fn();
    window.addEventListener("keydown", seen);
    try {
      item(wrapper, 1, "Lora").element.focus();
      await item(wrapper, 1, "Lora").trigger("keydown", { key: "Delete" });
      await item(wrapper, 1, "Lora").trigger("keydown", { key: "Delete", shiftKey: true });
      await flushPromises();
      expect(seen).not.toHaveBeenCalled();
      expect(removeModelFolderDir).toHaveBeenCalledWith(1, { path: "Lora", dryRun: true });
      expect(useModelShelfStore().folderRemoveRequest.source.path).toBe("Lora");
    } finally {
      window.removeEventListener("keydown", seen);
    }
    wrapper.unmount();
  });

  it("lets Move folder to… ask the shelf for the dialog on the pick step", async () => {
    const wrapper = await openLora();
    rightClick(item(wrapper, 1, "Lora"));
    await flushPromises();
    await menu(wrapper).find('[data-verb="move"]').trigger("click");
    const request = useModelShelfStore().folderMoveRequest;
    expect(request.source).toEqual({ folderId: 1, path: "Lora", name: "Lora" });
    expect(request.destination).toBe(null);
    expect(request.plan).toBe(null);
    wrapper.unmount();
  });

  it("starts a same-drive folder drop with no dialog: the dry run, then the move", async () => {
    const wrapper = await openLora();
    moveModelFolderDir.mockImplementation(async (id, body) =>
      body.dryRun ? plan(true) : started,
    );
    item(wrapper, 1, "Lora").element.dispatchEvent(dragEvent("dragstart", [FOLDER_TYPE]));
    item(wrapper, 1, "VAE").element.dispatchEvent(dragEvent("drop", [FOLDER_TYPE]));
    await flushPromises();
    expect(moveModelFolderDir.mock.calls.map((call) => Boolean(call[1].dryRun))).toEqual([
      true,
      false,
    ]);
    expect(useModelShelfStore().folderMoveRequest).toBe(null);
    wrapper.unmount();
  });

  it("asks first with the facts when a folder drop crosses drives", async () => {
    const wrapper = await openLora();
    moveModelFolderDir.mockImplementation(async (id, body) =>
      body.dryRun ? plan(false) : started,
    );
    item(wrapper, 1, "Lora").element.dispatchEvent(dragEvent("dragstart", [FOLDER_TYPE]));
    item(wrapper, 1, "VAE").element.dispatchEvent(dragEvent("drop", [FOLDER_TYPE]));
    await flushPromises();
    // Only the dry run: nothing moves until the dialog's Move is pressed.
    expect(moveModelFolderDir).toHaveBeenCalledTimes(1);
    expect(moveModelFolderDir.mock.calls[0][1].dryRun).toBe(true);
    const request = useModelShelfStore().folderMoveRequest;
    expect(request.source).toMatchObject({ folderId: 1, path: "Lora" });
    expect(request.destination).toEqual({ folderId: 1, path: "VAE", locked: false });
    expect(request.plan.same_drive).toBe(false);
    wrapper.unmount();
  });

  it("refuses a folder onto itself and onto its own descendant, with no ring", async () => {
    const wrapper = await openLora();
    await item(wrapper, 1, "Lora").find(".mft-twist").trigger("click");
    await flushPromises();
    item(wrapper, 1, "Lora").element.dispatchEvent(dragEvent("dragstart", [FOLDER_TYPE]));
    for (const path of ["Lora", "Lora/flux"]) {
      const over = dragEvent("dragover", [FOLDER_TYPE]);
      item(wrapper, 1, path).element.dispatchEvent(over);
      await flushPromises();
      expect(over.defaultPrevented).toBe(false);
      expect(item(wrapper, 1, path).classes()).toContain("not-droppable");
      expect(item(wrapper, 1, path).classes()).not.toContain("drop-ring");
    }
    // A sibling takes it, with the ring.
    const over = dragEvent("dragover", [FOLDER_TYPE]);
    item(wrapper, 1, "VAE").element.dispatchEvent(over);
    await flushPromises();
    expect(over.defaultPrevented).toBe(true);
    expect(item(wrapper, 1, "VAE").classes()).toContain("drop-ring");
    wrapper.unmount();
  });
});
