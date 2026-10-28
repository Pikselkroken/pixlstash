// Removing a folder that holds files (#2207): every file on the left, what
// breaks on the right, a typed name when something breaks or on Shift, and
// the dry run's fingerprint sent back so nothing goes that was not shown.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const removeModelFolderDir = vi.fn();
vi.mock("../../api/modelFolders", async (importOriginal) => ({
  ...(await importOriginal()),
  removeModelFolderDir: (...args) => removeModelFolderDir(...args),
}));

import ShelfRemoveFolderDialog from "./ShelfRemoveFolderDialog.vue";
import { useModelFoldersStore } from "../../stores/useModelFoldersStore";
import { useModelShelfStore } from "../../stores/useModelShelfStore";

const SOURCE = { folderId: 1, path: "Lora/old", name: "old" };
const NO_BREAKS = { people: [], sets: [], workflows: [] };
const plan = (overrides = {}) => ({
  dry_run: true,
  folder_id: 1,
  path: "Lora/old",
  name: "old",
  file_count: 2,
  bytes: 320,
  files: [
    { relpath: "lost.safetensors", size: 300, model_id: 7, link: false },
    { relpath: "notes.txt", size: 20, model_id: null, link: false },
  ],
  models: [{ id: 7, name: "lost.safetensors", elsewhere: false }],
  breaks: NO_BREAKS,
  pictures: 0,
  copies: [
    { folder_id: 1, relpath: "Lora/old/lost.safetensors", model_id: 7, size: 300 },
  ],
  fingerprint: "fp-1",
  ...overrides,
});
const BREAKS = {
  people: [{ id: 3, name: "Ada", models: ["lost.safetensors"] }],
  sets: [],
  workflows: [{ id: "manual:w", name: "Portraits", models: ["lost.safetensors"] }],
};
const DONE = { files_removed: 2, removed_models: [7], permanent: false, trash_name: "Trash" };

const AppDialogStub = {
  props: ["open"],
  emits: ["close"],
  template: '<div v-if="open"><slot /><slot name="footer" /></div>',
};

let wrapper;

function mountDialog(planned) {
  wrapper = mount(ShelfRemoveFolderDialog, {
    props: { open: true, source: SOURCE, plan: planned },
    attachTo: document.body,
    global: {
      stubs: {
        AppDialog: AppDialogStub,
        // The click event goes through: its `shiftKey` is what decides.
        AppButton: {
          props: ["disabled", "loading", "variant", "keyHint"],
          emits: ["click"],
          template:
            '<button type="button" :disabled="disabled" @click="$emit(\'click\', $event)"><slot /></button>',
        },
      },
    },
  });
  return wrapper;
}

const confirmButton = () => wrapper.find('[data-testid="remove-folder-confirm"]');
const nameField = () => wrapper.find('[data-testid="remove-folder-name"]');
const removals = () => removeModelFolderDir.mock.calls.filter(([, body]) => !body.dryRun);

beforeEach(() => {
  setActivePinia(createPinia());
  removeModelFolderDir.mockReset();
  // The repaint after a removal is not under test; keep it off the wire.
  vi.spyOn(useModelShelfStore(), "fetchRows").mockResolvedValue();
  vi.spyOn(useModelFoldersStore(), "refresh").mockResolvedValue();
  vi.spyOn(useModelFoldersStore(), "reloadDirs").mockImplementation(() => {});
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
});

describe("ShelfRemoveFolderDialog", () => {
  it("lists every file, marks the ones the shelf does not list, and says nothing breaks", () => {
    mountDialog(plan());
    const files = wrapper.find('[data-testid="remove-folder-files"]').findAll("li");
    expect(files.map((li) => li.find(".srf-name").text())).toEqual([
      "lost.safetensors",
      "notes.txt",
    ]);
    expect(files[0].text()).not.toContain("not listed");
    expect(files[1].text()).toContain("not listed");
    expect(wrapper.text()).toContain("Nothing outside this folder uses these models.");
  });

  it("needs no typed name when nothing breaks, and sends the fingerprint", async () => {
    removeModelFolderDir.mockResolvedValue(DONE);
    mountDialog(plan());
    expect(nameField().exists()).toBe(false);
    expect(confirmButton().text()).toBe("Move folder to Trash");
    expect(confirmButton().attributes("disabled")).toBeUndefined();
    await confirmButton().trigger("click");
    await flushPromises();
    expect(removals()).toEqual([
      [1, { path: "Lora/old", permanent: false, expect: "fp-1" }],
    ]);
    expect(wrapper.emitted("close")).toBeTruthy();
  });

  it("needs the folder's name typed when something outside breaks", async () => {
    removeModelFolderDir.mockResolvedValue(DONE);
    mountDialog(plan({ breaks: BREAKS, pictures: 12 }));
    expect(wrapper.find('[data-testid="remove-folder-people"]').text()).toContain("Ada");
    expect(wrapper.find('[data-testid="remove-folder-workflows"]').text()).toContain(
      "Portraits",
    );
    expect(wrapper.find('[data-testid="remove-folder-pictures"]').text()).toContain(
      "12 pictures",
    );
    expect(nameField().exists()).toBe(true);
    expect(confirmButton().attributes("disabled")).toBeDefined();

    await nameField().setValue("ol");
    expect(confirmButton().attributes("disabled")).toBeDefined();
    await nameField().setValue("old");
    expect(confirmButton().attributes("disabled")).toBeUndefined();
    await confirmButton().trigger("click");
    await flushPromises();
    expect(removals()).toHaveLength(1);
  });

  it("turns into Permanently delete on Shift, and then asks for the name", async () => {
    removeModelFolderDir.mockResolvedValue({ ...DONE, permanent: true });
    mountDialog(plan());
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Shift", shiftKey: true }));
    await flushPromises();
    expect(confirmButton().text()).toBe("Permanently delete");
    expect(nameField().exists()).toBe(true);
    expect(confirmButton().attributes("disabled")).toBeDefined();

    await nameField().setValue("old");
    await confirmButton().trigger("click", { shiftKey: true });
    await flushPromises();
    expect(removals()).toEqual([
      [1, { path: "Lora/old", permanent: true, expect: "fp-1" }],
    ]);
  });

  it("never deletes permanently on a click without Shift, whatever the label said", async () => {
    removeModelFolderDir.mockResolvedValue(DONE);
    mountDialog(plan());
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Shift", shiftKey: true }));
    await flushPromises();
    await nameField().setValue("old");
    await confirmButton().trigger("click");
    await flushPromises();
    expect(removals()[0][1].permanent).toBe(false);
  });

  it("reads the folder again on a 409 for a changed folder, and sends the new fingerprint", async () => {
    const changed = plan({
      fingerprint: "fp-2",
      files: [...plan().files, { relpath: "late.txt", size: 5, model_id: null, link: false }],
    });
    let removes = 0;
    removeModelFolderDir.mockImplementation(async (id, body) => {
      if (body.dryRun) return changed;
      removes += 1;
      if (removes === 1) {
        throw { response: { status: 409, data: { detail: "'Lora/old' changed since it was read" } } };
      }
      return DONE;
    });
    mountDialog(plan());
    await confirmButton().trigger("click");
    await flushPromises();
    expect(wrapper.emitted("close")).toBeFalsy();
    expect(wrapper.text()).toContain("The folder changed since it was read.");
    expect(wrapper.text()).toContain("late.txt");

    await confirmButton().trigger("click");
    await flushPromises();
    expect(removals().map(([, body]) => body.expect)).toEqual(["fp-1", "fp-2"]);
    expect(wrapper.emitted("close")).toBeTruthy();
  });

  it("hands the folder's copies to the move dialog for Move the models out first", async () => {
    mountDialog(plan());
    await wrapper.find('[data-testid="remove-folder-move-out"]').trigger("click");
    expect(wrapper.emitted("move-models")[0][0]).toEqual({
      items: [{ folder_id: 1, relpath: "Lora/old/lost.safetensors" }],
      totalBytes: 300,
    });
    expect(removeModelFolderDir).not.toHaveBeenCalled();
  });
});
