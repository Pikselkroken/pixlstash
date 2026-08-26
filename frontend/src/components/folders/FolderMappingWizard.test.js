// Wizard shell — step transitions and, crucially, what `mode` a scan/resume
// ends up with, since that is what the Preview step's commit sends over the
// wire (integration_architecture.md §22, FolderMappingPreviewStep.test.js).
//
// `resume` with an empty `taskId` and `mode: "local_import"` is the specific
// shape "Add a library"'s "pictures" verdict saves before switching the
// active library and reloading (AddLibraryDialog.vue): it means "skip
// choosing a folder, start scanning this known path fresh, and remember
// local_import" — not "reattach to an in-flight scan".

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { createPinia, setActivePinia } from "pinia";

vi.mock("vuetify/components", () => ({
  VDialog: { name: "v-dialog", template: "<div><slot /></div>" },
  VIcon: { name: "v-icon", template: "<i><slot /></i>" },
}));

import FolderMappingWizard from "./FolderMappingWizard.vue";
import { useFolderMappingStore } from "../../stores/useFolderMappingStore";

const ScanStub = {
  props: ["path", "resumeTaskId"],
  emits: ["task", "ready", "cancel"],
  template:
    '<div class="scan-stub" :data-path="path" :data-resume-task-id="resumeTaskId">' +
    "<button class=\"emit-task\" @click=\"$emit('task', 'started-1')\">task</button>" +
    "<button class=\"emit-ready\" @click=\"$emit('ready', { taskId: 'started-1', result: { picture_count: 5, levels: [] } })\">ready</button>" +
    "</div>",
};

const TreeStub = {
  props: ["result"],
  emits: ["back", "next"],
  template:
    '<div class="tree-stub"><button class="emit-next" @click="$emit(\'next\', [])">next</button></div>',
};

const PreviewStub = {
  props: [
    "path",
    "readTaskId",
    "assignments",
    "label",
    "pictureCount",
    "mode",
  ],
  template: '<div class="preview-stub" :data-mode="mode" />',
};

let pinia;

function mountWizard(props = {}) {
  return mount(FolderMappingWizard, {
    props: { open: true, ...props },
    global: {
      plugins: [pinia],
      stubs: {
        FolderMappingScanStep: ScanStub,
        FolderMappingTreeStep: TreeStub,
        FolderMappingPreviewStep: PreviewStub,
        FolderBrowser: true,
      },
    },
  });
}

async function settle(wrapper) {
  await nextTick();
  await nextTick();
  return wrapper;
}

beforeEach(() => {
  window.localStorage.clear();
  pinia = createPinia();
  setActivePinia(pinia);
});

describe("choosing a folder fresh", () => {
  it("defaults to reference mode and saves it once scanning starts", async () => {
    const wrapper = await settle(mountWizard());

    await wrapper
      .find(".mapping-wizard__choose-field .app-input__field")
      .setValue("/home/me/Pictures/Family");
    await wrapper
      .findAll(".mapping-wizard__choose-actions button")[0]
      .trigger("click");
    await settle(wrapper);

    const scan = wrapper.find(".scan-stub");
    expect(scan.exists()).toBe(true);
    expect(scan.attributes("data-resume-task-id")).toBe("");

    await scan.find(".emit-task").trigger("click");

    expect(useFolderMappingStore().pending).toEqual({
      taskId: "started-1",
      path: "/home/me/Pictures/Family",
      label: "Family",
      mode: "reference",
    });
  });
});

describe("resuming a local_import entry (empty taskId)", () => {
  const resume = {
    taskId: "",
    path: "/home/me/Pictures/Generations",
    label: "Generations",
    mode: "local_import",
  };

  it("skips the choose step and starts a fresh scan at that path", async () => {
    const wrapper = await settle(mountWizard({ resume }));

    // Never "choose" — the folder is already known.
    expect(wrapper.find(".mapping-wizard__choose").exists()).toBe(false);
    const scan = wrapper.find(".scan-stub");
    expect(scan.attributes("data-path")).toBe(resume.path);
    // Falsy resumeTaskId is what makes FolderMappingScanStep start a NEW
    // read instead of reattaching to one that does not exist yet.
    expect(scan.attributes("data-resume-task-id")).toBe("");
  });

  it("keeps local_import when the fresh scan's task id is saved", async () => {
    const wrapper = await settle(mountWizard({ resume }));

    await wrapper.find(".scan-stub .emit-task").trigger("click");

    expect(useFolderMappingStore().pending).toEqual({
      taskId: "started-1",
      path: resume.path,
      label: resume.label,
      mode: "local_import",
    });
  });

  it("threads local_import to the Preview step's commit", async () => {
    const wrapper = await settle(mountWizard({ resume }));

    await wrapper.find(".scan-stub .emit-ready").trigger("click");
    await settle(wrapper);
    await wrapper.find(".tree-stub .emit-next").trigger("click");
    await settle(wrapper);

    expect(wrapper.find(".preview-stub").attributes("data-mode")).toBe(
      "local_import",
    );
  });
});

describe("resuming a legacy entry with no mode field", () => {
  it("falls back to reference mode", async () => {
    const resume = {
      taskId: "task-99",
      path: "/home/me/Pictures/Old",
      label: "Old",
    };
    const wrapper = await settle(mountWizard({ resume }));

    await wrapper.find(".scan-stub .emit-task").trigger("click");

    expect(useFolderMappingStore().pending.mode).toBe("reference");
  });
});
