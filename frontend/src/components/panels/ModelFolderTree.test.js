// The Folders tree (#2203): drives, registered folders and their subfolders,
// read one level per expand, walked with the tree pattern's keys.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const listModelFolderDirs = vi.fn();
vi.mock("../../api/modelFolders", async (importOriginal) => ({
  ...(await importOriginal()),
  listModelFolderDirs: (...args) => listModelFolderDirs(...args),
}));

import ModelFolderTree from "./ModelFolderTree.vue";
import { useModelFoldersStore } from "../../stores/useModelFoldersStore";
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
    { name: "Lora", path: "Lora", model_count: 46, bytes: 1, has_dirs: true },
    { name: "VAE", path: "VAE", model_count: 3, bytes: 1, has_dirs: false },
  ],
  "1:Lora": [
    { name: "flux", path: "Lora/flux", model_count: 4, bytes: 1, has_dirs: false },
  ],
};

function mountTree(props = {}) {
  return mount(ModelFolderTree, {
    props,
    attachTo: document.body,
    global: { stubs: { "v-icon": true } },
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
    expect(item(wrapper, 1).find(".mft-count").text()).toBe("52");
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
  const TYPE = "application/x-pixlstash-model-copies";

  function drag(el, type) {
    const event = new Event(type, { bubbles: true, cancelable: true });
    event.dataTransfer = { types: [TYPE], dropEffect: "" };
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
    const over = drag(lora, "dragover");
    await flushPromises();
    expect(over.defaultPrevented).toBe(true);
    expect(lora.classes()).toContain("drop-ring");
    // Same drive: the tip says rename, and names where they go.
    expect(shelf.dropTip.text).toContain("Move to Models/Lora");
    expect(shelf.dropTip.text).toContain("Renamed");

    for (const refused of [
      wrapper.find('[data-key="drive:d1"]'),
      item(wrapper, 2),
      item(wrapper, 1),
    ]) {
      // Each refused drop ends that drag; the next is a new one.
      shelf.dragCopies = copies;
      const event = drag(refused, "dragover");
      await flushPromises();
      expect(event.defaultPrevented).toBe(false);
      expect(refused.classes()).toContain("not-droppable");
      drag(refused, "drop");
      expect(shelf.moveRequest).toBe(null);
    }

    shelf.dragCopies = copies;
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
    expect(drag(item(wrapper, 1), "dragover").defaultPrevented).toBe(false);
    wrapper.unmount();
  });
});

