// The Move dialog (#2204): from *Move to…* the destination is any folder or
// subfolder, picked in the Folders tree; a drop or a paste that needs asking
// opens it with one already chosen, and it only says what goes where.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const listModelFolderDirs = vi.fn();
vi.mock("../../api/modelFolders", async (importOriginal) => ({
  ...(await importOriginal()),
  listModelFolderDirs: (...args) => listModelFolderDirs(...args),
}));
const startModelMove = vi.fn();
vi.mock("../../api/modelMoves", () => ({
  startModelMove: (...args) => startModelMove(...args),
  getModelMoveStatus: vi.fn(async () => ({ status: "running", results: [] })),
  cancelModelMove: vi.fn(),
  relocateModelFolder: vi.fn(),
}));

import ShelfMoveDialog from "./ShelfMoveDialog.vue";
import { useModelFoldersStore } from "../../stores/useModelFoldersStore";

const FOLDERS = [
  { id: 1, path: "/home/me/Models", kind: "user", movable: "per_item" },
  { id: 2, path: "/home/me/.pixlstash/models", kind: "managed", movable: "root_only" },
];
const ITEMS = [{ folder_id: 1, relpath: "alice.safetensors" }];

const AppDialogStub = {
  props: ["open"],
  emits: ["close", "accept"],
  template: '<div v-if="open"><slot /><slot name="footer" /></div>',
};

let wrapper;

function mountDialog(props = {}) {
  wrapper = mount(ShelfMoveDialog, {
    props: { open: true, items: ITEMS, totalBytes: 2048, ...props },
    attachTo: document.body,
    global: {
      stubs: {
        AppDialog: AppDialogStub,
        "v-icon": true,
        "v-menu": { props: ["modelValue"], template: '<div v-if="modelValue"><slot /></div>' },
        Tooltip: true,
      },
    },
  });
  return wrapper;
}

const row = (folderId, path = "") =>
  wrapper.find(`[role="treeitem"][data-folder-id="${folderId}"][data-path="${path}"]`);
const moveButton = () =>
  wrapper.findAll("button").find((b) => b.text().includes("Move"));

beforeEach(() => {
  setActivePinia(createPinia());
  const folders = useModelFoldersStore();
  folders.folders = FOLDERS;
  listModelFolderDirs.mockReset().mockImplementation(async (id, path) => ({
    folder_id: id,
    path,
    locked: id === 2,
    model_count: 1,
    bytes: 0,
    dirs:
      id === 1 && path === ""
        ? [{ name: "Lora", path: "Lora", model_count: 0, bytes: 0, has_dirs: false }]
        : [],
  }));
  startModelMove.mockReset().mockResolvedValue({ status: "running", results: [] });
});

afterEach(() => wrapper?.unmount());

describe("ShelfMoveDialog", () => {
  it("starts with nothing chosen from Move to…, and sends the picked subfolder", async () => {
    mountDialog();
    await flushPromises();
    expect(wrapper.find('[role="treeitem"][aria-selected="true"]').exists()).toBe(false);
    expect(moveButton().attributes("disabled")).toBeDefined();

    await row(1).find(".mft-twist").trigger("click");
    await flushPromises();
    await row(1, "Lora").trigger("click");
    expect(row(1, "Lora").attributes("aria-selected")).toBe("true");

    await moveButton().trigger("click");
    await flushPromises();
    expect(startModelMove).toHaveBeenCalledWith(1, ITEMS, "Lora");
    expect(wrapper.emitted("started")).toHaveLength(1);
  });

  it("refuses the folder every file is already in, and a locked one", async () => {
    mountDialog();
    await flushPromises();
    expect(row(1).attributes("aria-disabled")).toBe("true");
    expect(row(2).attributes("aria-disabled")).toBe("true");
    await row(2).trigger("click");
    expect(moveButton().attributes("disabled")).toBeDefined();
  });

  it("states a drop's move from and to, without the tree, Move focused", async () => {
    mountDialog({ initialDestination: { folderId: 1, path: "Lora" } });
    await flushPromises();
    expect(wrapper.find('[role="treeitem"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="shelf-move-route"]').text()).toBe(
      "Move alice.safetensors from Models to Models/Lora.",
    );
    expect(document.activeElement).toBe(moveButton().element);

    await moveButton().trigger("click");
    await flushPromises();
    expect(startModelMove).toHaveBeenCalledWith(1, ITEMS, "Lora");
  });

  it("shows the tree again when reopened from Move to…", async () => {
    mountDialog({ initialDestination: { folderId: 1, path: "Lora" } });
    await flushPromises();
    // The host clears the destination as it closes the dialog.
    await wrapper.setProps({ open: false, initialDestination: null });
    await wrapper.setProps({ open: true });
    await flushPromises();
    expect(wrapper.find('[data-testid="shelf-move-route"]').exists()).toBe(false);
    expect(wrapper.find('[role="treeitem"]').exists()).toBe(true);
  });

  it("names how many files and folders when a drop carries several", async () => {
    mountDialog({
      items: [...ITEMS, { folder_id: 1, relpath: "Lora/bob.safetensors" }],
      initialDestination: { folderId: 1, path: "VAE" },
    });
    await flushPromises();
    expect(wrapper.find('[data-testid="shelf-move-route"]').text()).toBe(
      "Move 2 files from 2 folders to Models/VAE.",
    );
  });
});
