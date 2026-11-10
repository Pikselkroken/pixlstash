// Moving a folder (#2206): the tree picks the place, the server's dry run says
// whether it is a rename or a copy, and only a copy states its cost first.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const moveModelFolderDir = vi.fn();
vi.mock("../../api/modelFolders", async (importOriginal) => ({
  ...(await importOriginal()),
  listModelFolderDirs: vi.fn(async (id, path) => ({
    folder_id: id,
    path,
    locked: false,
    model_count: 0,
    bytes: 0,
    dirs:
      id === 1 && path === ""
        ? [
            { name: "Lora", path: "Lora", model_count: 1, bytes: 1, has_dirs: false },
            { name: "VAE", path: "VAE", model_count: 0, bytes: 0, has_dirs: false },
          ]
        : [],
  })),
  moveModelFolderDir: (...args) => moveModelFolderDir(...args),
}));
vi.mock("../../api/modelMoves", () => ({
  startModelMove: vi.fn(),
  getModelMoveStatus: vi.fn(async () => ({ status: "idle", results: [] })),
  cancelModelMove: vi.fn(),
  relocateModelFolder: vi.fn(),
}));

import FolderMoveDialog from "./FolderMoveDialog.vue";
import { useModelFoldersStore } from "../../stores/useModelFoldersStore";
import { useModelMovesStore } from "../../stores/useModelMovesStore";

const FOLDERS = [
  { id: 1, path: "/home/me/Models", kind: "user", movable: "per_item" },
  { id: 2, path: "/mnt/example/models", kind: "user", movable: "per_item" },
];
const SOURCE = { folderId: 1, path: "Lora", name: "Lora" };
const STARTED = {
  status: "running",
  kind: "folder",
  total: 1,
  done: 0,
  results: [],
  name: "Lora",
};
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

const AppDialogStub = {
  props: ["open"],
  emits: ["close", "accept"],
  template: '<div v-if="open"><slot /><slot name="footer" /></div>',
};

let wrapper;

function mountDialog(props = {}) {
  wrapper = mount(FolderMoveDialog, {
    props: { open: true, source: SOURCE, ...props },
    attachTo: document.body,
    global: {
      stubs: {
        AppDialog: AppDialogStub,
        AppButton: {
          props: ["disabled", "loading", "variant", "keyHint"],
          emits: ["click"],
          template:
            '<button type="button" :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
        },
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
  wrapper.findAll("button").find((b) => b.text() === "Move");

/** Folder rows start closed; open the registered folder so its subfolders show. */
async function expandFolderOne() {
  await row(1).find(".mft-twist").trigger("click");
  await flushPromises();
}

beforeEach(() => {
  setActivePinia(createPinia());
  moveModelFolderDir.mockReset();
  const folders = useModelFoldersStore();
  folders.folders = FOLDERS;
  folders.devices = [];
});

describe("FolderMoveDialog", () => {
  it("refuses the folder itself and the folder it is already in", async () => {
    mountDialog();
    await flushPromises();
    await expandFolderOne();
    expect(row(1, "Lora").attributes("aria-disabled")).toBe("true");
    expect(row(1, "").attributes("aria-disabled")).toBe("true");
    expect(row(1, "VAE").attributes("aria-disabled")).toBeUndefined();
    wrapper.unmount();
  });

  it("starts a same-drive move at once: the dry run, then the move, no second step", async () => {
    moveModelFolderDir.mockImplementation(async (id, body) =>
      body.dryRun ? plan(true) : STARTED,
    );
    mountDialog();
    await flushPromises();
    await expandFolderOne();
    await row(1, "VAE").trigger("click");
    await flushPromises();
    await moveButton().trigger("click");
    await flushPromises();
    expect(moveModelFolderDir.mock.calls.map((call) => Boolean(call[1].dryRun))).toEqual([
      true,
      false,
    ]);
    expect(moveModelFolderDir.mock.calls[1][1]).toMatchObject({
      path: "Lora",
      destinationPath: "VAE",
    });
    expect(wrapper.emitted("started")).toHaveLength(1);
    expect(wrapper.emitted("close")).toHaveLength(1);
    wrapper.unmount();
  });

  it("across drives it states the facts first, and only the next press moves", async () => {
    moveModelFolderDir.mockImplementation(async (id, body) =>
      body.dryRun ? plan(false) : STARTED,
    );
    mountDialog();
    await flushPromises();
    await expandFolderOne();
    await row(1, "VAE").trigger("click");
    await flushPromises();
    await moveButton().trigger("click");
    await flushPromises();

    const facts = wrapper.find('[data-testid="folder-move-facts"]');
    expect(facts.exists()).toBe(true);
    expect(facts.text()).toContain("Move Lora into Models › VAE");
    expect(facts.text()).toContain("21 models and 4 other files");
    expect(facts.text()).toContain("Another drive, so copied, checked, then removed.");
    expect(facts.text()).toContain("GB on");
    // The dry run alone so far: nothing has moved.
    expect(moveModelFolderDir).toHaveBeenCalledTimes(1);
    expect(wrapper.emitted("started")).toBe(undefined);

    await moveButton().trigger("click");
    await flushPromises();
    expect(moveModelFolderDir).toHaveBeenCalledTimes(2);
    expect(moveModelFolderDir.mock.calls[1][1].dryRun).toBe(undefined);
    expect(wrapper.emitted("started")).toHaveLength(1);
    wrapper.unmount();
  });

  it("opens on the facts when a drop already read them, and starts on Move", async () => {
    moveModelFolderDir.mockResolvedValue(STARTED);
    mountDialog({
      source: SOURCE,
      initialDestination: { folderId: 2, path: "" },
      plan: plan(false),
    });
    await flushPromises();
    expect(wrapper.find('[data-testid="folder-move-facts"]').exists()).toBe(true);
    expect(moveModelFolderDir).not.toHaveBeenCalled();
    await moveButton().trigger("click");
    await flushPromises();
    expect(moveModelFolderDir).toHaveBeenCalledTimes(1);
    expect(moveModelFolderDir.mock.calls[0][1]).toMatchObject({
      destinationFolderId: 2,
      destinationPath: "",
    });
    wrapper.unmount();
  });

  it("says the server's refusal and stays open", async () => {
    moveModelFolderDir.mockRejectedValue({
      response: { data: { detail: "Not enough space on the target drive." } },
    });
    mountDialog();
    await flushPromises();
    await expandFolderOne();
    await row(1, "VAE").trigger("click");
    await flushPromises();
    await moveButton().trigger("click");
    await flushPromises();
    expect(wrapper.find('[role="alert"]').text()).toBe(
      "Not enough space on the target drive.",
    );
    expect(wrapper.emitted("close")).toBe(undefined);
    wrapper.unmount();
  });

  afterEach(() => {
    useModelMovesStore().resetForSession();
    useModelFoldersStore().resetForSession();
  });
});
