import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";

const getFolderStructureCommitStatus = vi.fn();
const startFolderStructureCommit = vi.fn();
const stopFolderStructureCommit = vi.fn();
vi.mock("../../api/folderStructure", () => ({
  getFolderStructureCommitStatus: (...a) =>
    getFolderStructureCommitStatus(...a),
  startFolderStructureCommit: (...a) => startFolderStructureCommit(...a),
  stopFolderStructureCommit: (...a) => stopFolderStructureCommit(...a),
}));

import FolderMappingPreviewStep from "./FolderMappingPreviewStep.vue";

function mountStep(props = {}) {
  return mount(FolderMappingPreviewStep, {
    props: {
      path: "/home/me/pictures",
      readTaskId: "read-1",
      assignments: [{ kind: "project", relative_path: "2024 Shoots" }],
      commitOnMount: true,
      ...props,
    },
    global: {
      stubs: {
        "v-icon": { template: "<i><slot /></i>" },
        "v-progress-circular": { template: "<span />" },
        AppButton: {
          props: ["disabled", "loading", "variant", "size"],
          template: '<button :disabled="disabled || loading"><slot /></button>',
        },
      },
    },
  });
}

function buttonWith(wrapper, text) {
  return wrapper.findAll("button").find((b) => b.text().includes(text));
}

describe("FolderMappingPreviewStep", () => {
  beforeEach(() => {
    for (const fn of [
      getFolderStructureCommitStatus,
      startFolderStructureCommit,
      stopFolderStructureCommit,
    ]) {
      fn.mockReset();
    }
    getFolderStructureCommitStatus.mockResolvedValue({
      status: "running",
      stage: "indexing",
      processed: 3,
      total: 10,
    });
  });

  it("cannot be stopped in the seconds before the commit has a task id", async () => {
    // Between `committing` going true and the server answering, both stops used
    // to send stop("") - which fails with "Could not stop the import." while the
    // mapping commits anyway.
    let started;
    startFolderStructureCommit.mockImplementation(
      () => new Promise((resolve) => (started = resolve)),
    );

    const wrapper = mountStep();
    await flushPromises();

    const later = buttonWith(wrapper, "Organise later");
    const abort = buttonWith(wrapper, "Abort");
    expect(later.attributes("disabled")).toBeDefined();
    expect(abort.attributes("disabled")).toBeDefined();

    await later.trigger("click");
    await abort.trigger("click");
    await flushPromises();
    expect(stopFolderStructureCommit).not.toHaveBeenCalled();

    // Positive control: the moment the id lands, both stops work and address it.
    started({ task_id: "commit-7" });
    await flushPromises();

    const liveLater = buttonWith(wrapper, "Organise later");
    expect(liveLater.attributes("disabled")).toBeUndefined();
    await liveLater.trigger("click");
    await flushPromises();
    expect(stopFolderStructureCommit).toHaveBeenCalledWith("commit-7", "defer");
  });

  it("still commits with no assignments when nothing is running", async () => {
    // "Organise later" before the import means something else entirely, and the
    // guard above must not disable it.
    startFolderStructureCommit.mockResolvedValue({ task_id: "commit-9" });

    const wrapper = mountStep({ commitOnMount: false });
    await flushPromises();

    const later = buttonWith(wrapper, "Organise later");
    expect(later.attributes("disabled")).toBeUndefined();
    await later.trigger("click");
    await flushPromises();

    expect(startFolderStructureCommit).toHaveBeenCalledWith(
      "read-1",
      [],
      "",
      "reference",
      null,
    );
  });
});
