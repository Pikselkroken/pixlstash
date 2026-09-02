// "Add a library", start to finish, in ONE dialog. What is worth pinning:
// "Bring them in" swaps the verdict card for the scan card without the
// dialog closing or the choose pane remounting; Cancel before the library is
// built leaves nothing behind (read cancelled, no library, no saved entry);
// building is addLibrary -> save an autoCommit entry -> switch; and the
// resumed autoCommit entry commits on mount with the saved assignments and is
// immediately downgraded to a plain resume entry.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

vi.mock("vuetify/components", () => ({
  VDialog: { name: "v-dialog", template: "<div><slot /></div>" },
  VIcon: { name: "v-icon", template: "<i><slot /></i>" },
  VProgressCircular: { name: "v-progress-circular", template: "<i />" },
}));

import FolderMappingWizard from "./FolderMappingWizard.vue";
import FolderMappingChooseStep from "./FolderMappingChooseStep.vue";
import FolderMappingPreviewStep from "./FolderMappingPreviewStep.vue";
import AppDialog from "../widgets/AppDialog.vue";
import {
  addLibrary,
  inspectLibraryPath,
  listLibraries,
  setActiveLibrary,
} from "../../api/libraries";
import {
  cancelFolderStructureRead,
  getFolderStructureCommitStatus,
  getFolderStructureReadStatus,
  startFolderStructureCommit,
  startFolderStructureRead,
} from "../../api/folderStructure";
import { useFolderMappingStore } from "../../stores/useFolderMappingStore";
import { useLibrariesStore } from "../../stores/useLibrariesStore";
import { reloadPage } from "../../utils/reloadPage";

vi.mock("../../api/libraries", () => ({
  inspectLibraryPath: vi.fn(),
  addLibrary: vi.fn(),
  setActiveLibrary: vi.fn(),
  listLibraries: vi.fn(),
}));

vi.mock("../../api/folderStructure", () => ({
  startFolderStructureRead: vi.fn(),
  getFolderStructureReadStatus: vi.fn(),
  cancelFolderStructureRead: vi.fn(),
  startFolderStructureCommit: vi.fn(),
  getFolderStructureCommitStatus: vi.fn(),
  stopFolderStructureCommit: vi.fn(),
}));

vi.mock("../../api/folders", () => ({
  browseFilesystem: vi.fn().mockResolvedValue({ path: "/", entries: [] }),
  createFilesystemFolder: vi.fn(),
}));

vi.mock("../../utils/reloadPage", () => ({ reloadPage: vi.fn() }));

const PATH = "/home/me/Pictures/Generations";

const PICTURES = {
  verdict: "pictures",
  path: PATH,
  can_add: true,
  headline: "28,412 pictures, no library here yet",
  detail: "Bring them in and name what your folders mean. Nothing is moved.",
  suggested_name: "Generations",
};

const READ_RESULT = { picture_count: 5, folder_count: 2, levels: [] };
const ASSIGNMENTS = [{ relative_path: "Alice", kind: "person" }];

const TreeStub = {
  props: ["result"],
  emits: ["next", "later"],
  template:
    '<div class="tree-stub">' +
    "<button class=\"emit-next\" @click=\"$emit('next', [{ relative_path: 'Alice', kind: 'person' }])\">next</button>" +
    '<button class="emit-later" @click="$emit(\'later\')">later</button>' +
    "</div>",
};

let pinia;

function mountWizard(props = {}) {
  return mount(FolderMappingWizard, {
    props: { open: true, ...props },
    global: {
      plugins: [pinia],
      stubs: {
        FolderMappingTreeStep: TreeStub,
        FolderBrowser: true,
        AppButton: {
          props: ["disabled", "loading"],
          template:
            '<button :disabled="disabled" @click="$emit(\'click\', $event)"><slot /></button>',
        },
      },
    },
  });
}

async function settle() {
  for (let i = 0; i < 4; i += 1) await flushPromises();
}

async function typePath(wrapper, path) {
  await wrapper.find(".choose-step__field .app-input__field").setValue(path);
  await wrapper.find(".choose-step__field .app-input__field").trigger("blur");
  await settle();
}

function button(wrapper, text) {
  return wrapper
    .findAll("button")
    .find((candidate) => candidate.text().trim() === text);
}

async function bringThemIn(wrapper) {
  await typePath(wrapper, PATH);
  await button(wrapper, "Bring them in").trigger("click");
  await settle();
}

beforeEach(() => {
  window.localStorage.clear();
  pinia = createPinia();
  setActivePinia(pinia);
  vi.clearAllMocks();
  useLibrariesStore().libraries = [
    {
      uuid: "uuid-current",
      name: "Family Photos",
      is_active: true,
      path: "/home/me/Pictures/Family",
    },
  ];
  listLibraries.mockResolvedValue({ libraries: [], can_manage: true });
  inspectLibraryPath.mockResolvedValue(structuredClone(PICTURES));
  addLibrary.mockResolvedValue({ uuid: "uuid-new", name: "Generations" });
  setActiveLibrary.mockResolvedValue({ status: "ok" });
  startFolderStructureRead.mockResolvedValue({ task_id: "read-1" });
  getFolderStructureReadStatus.mockResolvedValue({
    status: "running",
    stage: "walking",
    processed: 0,
    total: 0,
    result: null,
  });
  cancelFolderStructureRead.mockResolvedValue({ status: "cancelled" });
  startFolderStructureCommit.mockResolvedValue({ task_id: "commit-1" });
  getFolderStructureCommitStatus.mockResolvedValue({
    status: "running",
    stage: "indexing",
    processed: 0,
    total: 5,
  });
});

describe("a 'pictures' verdict", () => {
  it("swaps the verdict card for the scan card without the dialog changing", async () => {
    const wrapper = mountWizard();
    await settle();
    const chooseUid = wrapper.findComponent(FolderMappingChooseStep).vm.$.uid;

    await bringThemIn(wrapper);

    // The same component instance: the pane was not remounted.
    expect(wrapper.findComponent(FolderMappingChooseStep).vm.$.uid).toBe(
      chooseUid,
    );
    expect(wrapper.findComponent(AppDialog).props("open")).toBe(true);
    expect(wrapper.findComponent(AppDialog).props("title")).toBe(
      "Add a library",
    );
    expect(wrapper.emitted("close")).toBeFalsy();
    expect(wrapper.find(".choose-step__verdict").exists()).toBe(false);
    expect(wrapper.find(".scan-step .mapping-card").text()).toContain(
      "Working out what your folders mean",
    );
    expect(startFolderStructureRead).toHaveBeenCalledWith(PATH, {
      matchExisting: false,
    });
    expect(addLibrary).not.toHaveBeenCalled();
    expect(useFolderMappingStore().pending).toBeNull();
  });

  it("cancels the read and leaves nothing behind on Cancel", async () => {
    const wrapper = mountWizard();
    await settle();
    await bringThemIn(wrapper);

    await button(wrapper, "Cancel").trigger("click");
    await settle();

    expect(cancelFolderStructureRead).toHaveBeenCalledWith("read-1");
    expect(addLibrary).not.toHaveBeenCalled();
    expect(setActiveLibrary).not.toHaveBeenCalled();
    expect(useFolderMappingStore().pending).toBeNull();
    expect(wrapper.emitted("close")).toBeTruthy();
  });

  it("does the same for the header close", async () => {
    const wrapper = mountWizard();
    await settle();
    await bringThemIn(wrapper);

    wrapper.findComponent(AppDialog).vm.$emit("close");
    await settle();

    expect(cancelFolderStructureRead).toHaveBeenCalledWith("read-1");
    expect(addLibrary).not.toHaveBeenCalled();
    expect(wrapper.emitted("close")).toBeTruthy();
  });
});

describe("a read someone else already finished", () => {
  // The desktop startup screen reads the folder while the GPU runtime
  // downloads. Its RESULT is what travels: the task lives in the server's
  // memory and the backend restarts before the app loads, so a resumed task id
  // answered "Task not found" while the wizard spun on work that was done.
  it("opens on the mapping questions, and asks the server nothing", async () => {
    const wrapper = mountWizard({
      resume: {
        path: "/home/me/Pictures",
        result: READ_RESULT,
        mode: "local_import",
      },
    });
    await settle();

    expect(wrapper.find(".tree-stub").exists()).toBe(true);
    expect(getFolderStructureReadStatus).not.toHaveBeenCalled();
    expect(startFolderStructureRead).not.toHaveBeenCalled();

    wrapper.unmount();
  });

  it("still starts at the folder question when nothing was parked", async () => {
    const wrapper = mountWizard();
    await settle();

    expect(wrapper.find(".tree-stub").exists()).toBe(false);

    wrapper.unmount();
  });
});

describe("building the library", () => {
  async function reachTheMapping(wrapper) {
    getFolderStructureReadStatus.mockResolvedValue({
      status: "completed",
      stage: "done",
      processed: 2,
      total: 2,
      result: READ_RESULT,
    });
    await bringThemIn(wrapper);
    await button(wrapper, "Set up my library").trigger("click");
    await settle();
    expect(wrapper.find(".tree-stub").exists()).toBe(true);
  }

  it("adds the library, saves the commit for after the switch, then switches", async () => {
    const wrapper = mountWizard();
    await settle();
    await reachTheMapping(wrapper);

    await wrapper.find(".tree-stub .emit-next").trigger("click");
    await settle();
    expect(wrapper.findComponent(AppDialog).props("title")).toBe(
      "Before anything is written",
    );

    await button(wrapper, "Yes, build this library").trigger("click");
    await settle();

    expect(addLibrary).toHaveBeenCalledWith(PATH, "Generations");
    expect(useFolderMappingStore().pending).toEqual({
      taskId: "read-1",
      path: PATH,
      label: "Generations",
      mode: "local_import",
      assignments: ASSIGNMENTS,
      pictureCount: 5,
      autoCommit: true,
    });
    expect(setActiveLibrary).toHaveBeenCalledWith("uuid-new");
    expect(reloadPage).toHaveBeenCalled();
    expect(startFolderStructureCommit).not.toHaveBeenCalled();
    expect(wrapper.emitted("close")).toBeTruthy();
  });

  it("'Drop this, organise later' builds it with no assignments", async () => {
    const wrapper = mountWizard();
    await settle();
    await reachTheMapping(wrapper);

    await wrapper.find(".tree-stub .emit-later").trigger("click");
    await settle();

    expect(addLibrary).toHaveBeenCalledWith(PATH, "Generations");
    expect(useFolderMappingStore().pending).toMatchObject({
      assignments: [],
      autoCommit: true,
    });
    expect(setActiveLibrary).toHaveBeenCalledWith("uuid-new");
  });

  it("stays open with the server's refusal when the create fails", async () => {
    addLibrary.mockRejectedValue({
      response: { data: { detail: '"Generations" covers this folder.' } },
    });
    const wrapper = mountWizard();
    await settle();
    await reachTheMapping(wrapper);

    await wrapper.find(".tree-stub .emit-later").trigger("click");
    await settle();

    expect(wrapper.find(".mapping-wizard__error").text()).toContain(
      "covers this folder",
    );
    expect(setActiveLibrary).not.toHaveBeenCalled();
    expect(useFolderMappingStore().pending).toBeNull();
    expect(wrapper.emitted("close")).toBeFalsy();
  });
});

describe("resuming after the switch", () => {
  const entry = {
    taskId: "read-1",
    path: PATH,
    label: "Generations",
    mode: "local_import",
    assignments: ASSIGNMENTS,
    pictureCount: 5,
    autoCommit: true,
  };

  it("commits the saved assignments on mount and downgrades the entry", async () => {
    useFolderMappingStore().save(entry);
    const wrapper = mountWizard({ resume: entry });
    await settle();

    expect(wrapper.findComponent(AppDialog).props("title")).toBe(
      "Before anything is written",
    );
    expect(startFolderStructureCommit).toHaveBeenCalledWith(
      "read-1",
      ASSIGNMENTS,
      "Generations",
      "local_import",
      null,
    );
    expect(addLibrary).not.toHaveBeenCalled();
    expect(startFolderStructureRead).not.toHaveBeenCalled();
    // From here a reopen reattaches to the read; it must never commit twice.
    expect(useFolderMappingStore().pending).toEqual({
      taskId: "read-1",
      path: PATH,
      label: "Generations",
      mode: "local_import",
    });
  });

  it("reattaches a plain entry at the scan card and keeps it on close", async () => {
    const plain = {
      taskId: "read-1",
      path: PATH,
      label: "Generations",
      mode: "local_import",
    };
    useFolderMappingStore().save(plain);
    const wrapper = mountWizard({ resume: plain });
    await settle();

    expect(wrapper.find(".scan-step").exists()).toBe(true);
    expect(startFolderStructureRead).not.toHaveBeenCalled();
    expect(startFolderStructureCommit).not.toHaveBeenCalled();

    wrapper.findComponent(AppDialog).vm.$emit("close");
    await settle();

    expect(cancelFolderStructureRead).not.toHaveBeenCalled();
    expect(useFolderMappingStore().pending).toEqual(plain);
  });
});

describe("the empty library's own folder", () => {
  // No read yet and no entry: the sidebar opens this when the library is
  // empty but its folder is not (a desktop first run over loose pictures).
  const own = { path: "/home/me/Pictures/Family", mode: "local_import" };

  it("reads the folder, saves the read, and organising later commits", async () => {
    getFolderStructureReadStatus.mockResolvedValue({
      status: "completed",
      stage: "done",
      processed: 2,
      total: 2,
      result: READ_RESULT,
    });
    const wrapper = mountWizard({ resume: own });
    await settle();

    expect(startFolderStructureRead).toHaveBeenCalledWith(own.path, {
      matchExisting: true,
    });
    expect(useFolderMappingStore().pending).toEqual({
      taskId: "read-1",
      path: own.path,
      label: "",
      mode: "local_import",
    });

    await button(wrapper, "Set up my library").trigger("click");
    await settle();
    await wrapper.find(".tree-stub .emit-later").trigger("click");
    await settle();

    expect(addLibrary).not.toHaveBeenCalled();
    expect(startFolderStructureCommit).toHaveBeenCalledWith(
      "read-1",
      [],
      "",
      "local_import",
      READ_RESULT,
    );
  });
});

describe("the other verdicts", () => {
  it.each([
    ["vault", "A library you already made", "Add it"],
    ["empty", "Empty", "Start here"],
  ])("%s adds and switches, with no read", async (verdict, headline, label) => {
    inspectLibraryPath.mockResolvedValue({
      ...structuredClone(PICTURES),
      verdict,
      headline,
    });
    const wrapper = mountWizard();
    await settle();
    await typePath(wrapper, PATH);

    await button(wrapper, label).trigger("click");
    await settle();

    expect(addLibrary).toHaveBeenCalledWith(PATH, "Generations");
    expect(setActiveLibrary).toHaveBeenCalledWith("uuid-new");
    expect(startFolderStructureRead).not.toHaveBeenCalled();
    expect(useFolderMappingStore().pending).toBeNull();
    expect(wrapper.emitted("close")).toBeTruthy();
  });
});

describe("the branches nobody walks on purpose", () => {
  // The wizard's own decisions, one test each, because every one of them is
  // reachable from a real first run and none of them is reachable by clicking
  // through the happy path.

  async function reachTheMapping(wrapper) {
    getFolderStructureReadStatus.mockResolvedValue({
      status: "completed",
      stage: "done",
      processed: 2,
      total: 2,
      result: READ_RESULT,
    });
    await bringThemIn(wrapper);
    await button(wrapper, "Set up my library").trigger("click");
    await settle();
  }

  const resumed = {
    taskId: "read-1",
    path: PATH,
    label: "Generations",
    mode: "local_import",
    assignments: ASSIGNMENTS,
    pictureCount: 5,
    autoCommit: true,
  };

  it("brings the read's result back for a resumed commit, so Back still works", async () => {
    // The result did not survive the reload. Without this one poll, "Back to
    // the mapping" after a failed commit lands on a step with nothing to show.
    getFolderStructureReadStatus.mockResolvedValue({
      status: "completed",
      result: READ_RESULT,
    });
    useFolderMappingStore().save(resumed);
    const wrapper = mountWizard({ resume: resumed });
    await settle();

    expect(getFolderStructureReadStatus).toHaveBeenCalledWith("read-1");
    wrapper.findComponent(FolderMappingPreviewStep).vm.$emit("back");
    await settle();

    expect(wrapper.find(".tree-stub").exists()).toBe(true);

    wrapper.unmount();
  });

  it("carries on when that poll fails, rather than failing the commit", async () => {
    getFolderStructureReadStatus.mockRejectedValue(new Error("gone"));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    useFolderMappingStore().save(resumed);
    const wrapper = mountWizard({ resume: resumed });
    await settle();

    expect(startFolderStructureCommit).toHaveBeenCalled();
    expect(warn).toHaveBeenCalled();

    wrapper.unmount();
  });

  it("refuses to close once a commit has started", async () => {
    // A commit runs to completion server-side and cannot be un-started, so a
    // dialog that closed over it would leave the work invisible.
    useFolderMappingStore().save(resumed);
    const wrapper = mountWizard({ resume: resumed });
    await settle();
    wrapper
      .findComponent(FolderMappingPreviewStep)
      .vm.$emit("update:committing", true);
    await settle();

    wrapper.findComponent(AppDialog).vm.$emit("close");
    await settle();

    expect(wrapper.emitted("close")).toBeFalsy();

    wrapper.unmount();
  });

  it("builds the library once, however many times the button is pressed", async () => {
    let release;
    addLibrary.mockImplementation(
      () =>
        new Promise((resolve) => {
          release = () => resolve({ uuid: "uuid-new", name: "Generations" });
        }),
    );
    const wrapper = mountWizard();
    await reachTheMapping(wrapper);
    await wrapper.find(".tree-stub .emit-next").trigger("click");
    await settle();

    wrapper
      .findComponent(FolderMappingPreviewStep)
      .vm.$emit("build", ASSIGNMENTS);
    wrapper
      .findComponent(FolderMappingPreviewStep)
      .vm.$emit("build", ASSIGNMENTS);
    await settle();
    release();
    await settle();

    expect(addLibrary).toHaveBeenCalledTimes(1);

    wrapper.unmount();
  });

  it("does not cancel a read that has already settled", async () => {
    // A settled read is kept server-side and is the thing a reopen reattaches
    // to; cancelling it on the way out would throw the answer away.
    const wrapper = mountWizard();
    await reachTheMapping(wrapper);

    wrapper.findComponent(AppDialog).vm.$emit("close");
    await settle();

    expect(cancelFolderStructureRead).not.toHaveBeenCalled();

    wrapper.unmount();
  });

  it("survives a commit starting with no pending entry at all", async () => {
    // The parked-read path opens the wizard from an entry the store never saw
    // (the desktop startup screen read the folder), so `pending` is null when
    // the commit starts. The downgrade has to notice, rather than reading
    // `taskId` off nothing.
    const wrapper = mountWizard({
      resume: { path: PATH, result: READ_RESULT, mode: "local_import" },
    });
    await settle();
    expect(useFolderMappingStore().pending).toBe(null);

    await wrapper.find(".tree-stub .emit-next").trigger("click");
    await settle();
    wrapper.findComponent(FolderMappingPreviewStep).vm.$emit("commit-started");
    await settle();

    expect(useFolderMappingStore().pending).toBe(null);

    wrapper.unmount();
  });

  it("commits a parked read by its result, because its task no longer exists", async () => {
    // The failure this replaces: the desktop's first run read the folder on
    // one server process and restarted onto the GPU runtime before the owner
    // answered, so "Yes, build this library" met "Task not found" with the
    // answer sitting in the dialog.
    const wrapper = mountWizard({
      resume: { path: PATH, result: READ_RESULT, mode: "local_import" },
    });
    await settle();
    await wrapper.find(".tree-stub .emit-next").trigger("click");
    await settle();

    await button(wrapper, "Yes, build this library").trigger("click");
    await settle();

    expect(startFolderStructureCommit).toHaveBeenCalledWith(
      "",
      ASSIGNMENTS,
      "",
      "local_import",
      READ_RESULT,
    );
    expect(addLibrary).not.toHaveBeenCalled();

    wrapper.unmount();
  });

  it("names each step, because the title is the only thing that says where you are", async () => {
    const wrapper = mountWizard();
    expect(wrapper.findComponent(AppDialog).props("title")).toBe(
      "Add a library",
    );

    await reachTheMapping(wrapper);
    expect(wrapper.findComponent(AppDialog).props("title")).toBe(
      "Create the PixlStash database",
    );

    await wrapper.find(".tree-stub .emit-next").trigger("click");
    await settle();
    expect(wrapper.findComponent(AppDialog).props("title")).toBe(
      "Before anything is written",
    );

    wrapper.unmount();
  });

  it("counts no pictures when the read reports none, rather than undefined", async () => {
    const wrapper = mountWizard();
    await bringThemIn(wrapper);
    wrapper.findComponent(FolderMappingChooseStep).vm.$emit("ready", {
      taskId: "read-1",
      result: { levels: [] },
    });
    await settle();
    await wrapper.find(".tree-stub .emit-next").trigger("click");
    await settle();

    expect(
      wrapper.findComponent(FolderMappingPreviewStep).props("pictureCount"),
    ).toBe(0);

    wrapper.unmount();
  });
});

describe("reopening", () => {
  it("starts over rather than showing the previous answer", async () => {
    const wrapper = mountWizard();
    await settle();
    await typePath(wrapper, PATH);
    expect(wrapper.find(".choose-step__verdict").exists()).toBe(true);

    await wrapper.setProps({ open: false });
    await wrapper.setProps({ open: true });
    await settle();

    expect(wrapper.find(".choose-step__verdict").exists()).toBe(false);
    expect(
      wrapper.find(".choose-step__field .app-input__field").element.value,
    ).toBe("");
  });
});
