// Wizard step 3 ("Preview") — what's worth pinning here is the one thing this
// screen actually sends over the wire: `mode` has to reach the commit call
// unchanged, because that field is what tells the server whether to register
// an external reference folder or import into the active library in place
// (integration_architecture.md §22). The default has to keep matching every
// existing (reference-folder) caller that never passes `mode` at all.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";

vi.mock("vuetify/components", () => ({
  VIcon: { name: "v-icon", template: "<i><slot /></i>" },
  VProgressCircular: { name: "v-progress-circular", template: "<i />" },
}));

import FolderMappingPreviewStep from "./FolderMappingPreviewStep.vue";
import { startFolderStructureCommit } from "../../api/folderStructure";

vi.mock("../../api/folderStructure", () => ({
  startFolderStructureCommit: vi.fn(),
  getFolderStructureCommitStatus: vi.fn(),
}));

function mountStep(props = {}) {
  return mount(FolderMappingPreviewStep, {
    props: {
      path: "/home/me/Pictures/Generations",
      readTaskId: "task-1",
      assignments: [],
      label: "Generations",
      pictureCount: 12,
      ...props,
    },
    global: {
      stubs: {
        AppButton: {
          props: ["disabled", "loading"],
          template:
            '<button :disabled="disabled" @click="$emit(\'click\', $event)"><slot /></button>',
        },
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
  vi.clearAllMocks();
  // Never resolves in these tests: the assertion is on the request that was
  // sent, not on what happens once it settles.
  startFolderStructureCommit.mockReturnValue(new Promise(() => {}));
});

describe("committing", () => {
  it("defaults to reference mode, unchanged for every existing caller", async () => {
    const wrapper = await settle(mountStep());

    await wrapper.find(".preview-step__actions button").trigger("click");
    await settle(wrapper);

    expect(startFolderStructureCommit).toHaveBeenCalledWith(
      "task-1",
      [],
      "Generations",
      "reference",
    );
  });

  it("sends local_import through when the wizard is in that mode", async () => {
    const wrapper = await settle(mountStep({ mode: "local_import" }));

    await wrapper.find(".preview-step__actions button").trigger("click");
    await settle(wrapper);

    expect(startFolderStructureCommit).toHaveBeenCalledWith(
      "task-1",
      [],
      "Generations",
      "local_import",
    );
  });
});

describe("the 'no folder is created' fact", () => {
  it("keeps the reference-folder wording by default", async () => {
    const wrapper = await settle(mountStep());
    expect(wrapper.text()).toContain(
      "no folder is created inside your library",
    );
  });

  it("says these become ordinary library pictures for a local import", async () => {
    // The reference-folder line is false framing here: the scanned root
    // already IS the library's own root, and there is no external reference
    // folder to distinguish these pictures from.
    const wrapper = await settle(mountStep({ mode: "local_import" }));
    expect(wrapper.text()).toContain(
      "these pictures become ordinary pictures of this library",
    );
    expect(wrapper.text()).not.toContain(
      "no folder is created inside your library",
    );
  });
});
