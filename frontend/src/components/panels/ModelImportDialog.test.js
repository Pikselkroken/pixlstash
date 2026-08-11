// The ai-toolkit import card grid.
//
// The assertions worth having are the two that guard promises the backend makes
// and this dialog is the only place to keep: drawing the grid must not import
// anything, and a run with no bare final file must SAY its cover is a guess —
// that run is either still training or was interrupted, and importing it
// silently is how the wrong step becomes the cover of a stack.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const listRuns = vi.fn();
const importRun = vi.fn();
vi.mock("../../api/modelImports", () => ({
  listRuns: (...args) => listRuns(...args),
  importRun: (...args) => importRun(...args),
  runSampleUrl: (folderId, runName, filename) =>
    `/api/v1/model-folders/${folderId}/runs/${runName}/samples/${filename}`,
}));

import ModelImportDialog from "./ModelImportDialog.vue";
import { useModelFoldersStore } from "../../stores/useModelFoldersStore";

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      // Renders its slots without dragging Vuetify's dialog provider in.
      AppDialog: {
        props: ["open"],
        template: "<div v-if='open'><slot /><slot name='footer' /></div>",
      },
      AppButton: {
        template: "<button :disabled='disabled'><slot /></button>",
        props: ["disabled", "loading", "variant", "keyHint"],
      },
    },
  },
};

const FOLDERS = [
  { id: 1, path: "/runs", kind: "source", delete_after_import: false },
  { id: 2, path: "/models/store", kind: "managed", movable: "root_only" },
  { id: 3, path: "/hf-cache", kind: "foreign", movable: "external" },
];

function run(overrides = {}) {
  return {
    name: "Clementine",
    checkpoints: [
      { filename: "Clementine_000000500.safetensors", step: 500, size: 1000 },
      { filename: "Clementine.safetensors", step: null, size: 1000 },
    ],
    samples: [
      { filename: "s_500_0.jpg", step: 500, index: 0 },
      { filename: "s_250_0.jpg", step: 250, index: 0 },
    ],
    base_model: "flux.1-dev",
    trigger_words: [],
    rank: 32,
    config_error: null,
    ...overrides,
  };
}

async function openWith(runs, folders = FOLDERS) {
  listRuns.mockResolvedValue(runs);
  useModelFoldersStore().folders = folders;
  const wrapper = mount(ModelImportDialog, {
    ...globalOpts,
    props: { open: true },
  });
  await new Promise((resolve) => setTimeout(resolve, 0));
  await wrapper.vm.$nextTick();
  return wrapper;
}

beforeEach(() => {
  setActivePinia(createPinia());
  listRuns.mockReset();
  importRun.mockReset();
});

describe("drawing the grid", () => {
  it("describes every run without importing any of it", async () => {
    // The listing route's whole promise: it hashes, copies and writes nothing,
    // so the grid is drawn before the user has decided about anything.
    const wrapper = await openWith([run(), run({ name: "Foxglove" })]);
    expect(wrapper.findAll(".mid-card")).toHaveLength(2);
    expect(importRun).not.toHaveBeenCalled();
  });

  it("covers with the first prompt at the highest step", async () => {
    // Highest step is what the run has learned so far. First PROMPT, not the
    // last rendered: `index` separates prompts within a step rather than time,
    // so the cover stays on one prompt and two cards stay comparable.
    const wrapper = await openWith([
      run({
        samples: [
          { filename: "s_250_0.jpg", step: 250, index: 0 },
          { filename: "s_500_1.jpg", step: 500, index: 1 },
          { filename: "s_500_0.jpg", step: 500, index: 0 },
        ],
      }),
    ]);
    expect(wrapper.find(".mid-card-preview").attributes("src")).toContain(
      "s_500_0.jpg",
    );
  });

  it("says so when a run has no final file, rather than picking silently", async () => {
    // ai-toolkit writes the bare final at the end, so a run without one is
    // still training or was interrupted. The highest step is then the best
    // available answer, not a certain one.
    const unfinished = run({
      checkpoints: [
        { filename: "Clementine_000000500.safetensors", step: 500, size: 1 },
      ],
    });
    const wrapper = await openWith([unfinished]);
    expect(wrapper.find(".mid-card-note").text()).toContain(
      "No final file yet",
    );
  });

  it("keeps a run importable when its config could not be read", async () => {
    // Steps and samples come from filenames, so the config is decoration.
    const wrapper = await openWith([run({ config_error: "bad yaml" })]);
    await wrapper.find(".mid-card").trigger("click");
    expect(wrapper.text()).toContain("The steps still import");
    expect(wrapper.find(".mid-steps").exists()).toBe(true);
  });

  it("has nothing to show when no output root is registered", async () => {
    const wrapper = await openWith([], [FOLDERS[1]]);
    expect(wrapper.text()).toContain(
      "No ai-toolkit output folder is registered",
    );
    expect(listRuns).not.toHaveBeenCalled();
  });
});

describe("choosing what to take", () => {
  it("ticks the whole run on pick, because a run is one stack", async () => {
    const wrapper = await openWith([run()]);
    await wrapper.find(".mid-card").trigger("click");
    const boxes = wrapper.findAll(".mid-step input");
    expect(boxes).toHaveLength(2);
    expect(boxes.every((b) => b.element.checked)).toBe(true);
  });

  it("never offers a source or an external folder as a destination", async () => {
    // A source folder is taken from, never written into (the server refuses
    // it); an external one is shared with other software.
    const wrapper = await openWith([run()]);
    await wrapper.find(".mid-card").trigger("click");
    const paths = wrapper
      .findAll(".mid-field select")
      .at(-1)
      .findAll("option")
      .map((o) => o.text());
    expect(paths).toEqual(["/models/store"]);
  });

  it("warns before importing when the folder deletes its runs", async () => {
    // The one part of an import that cannot be undone, said before it starts.
    const wrapper = await openWith(
      [run()],
      [{ ...FOLDERS[0], delete_after_import: true }, FOLDERS[1]],
    );
    await wrapper.find(".mid-card").trigger("click");
    expect(wrapper.find(".mid-warning").text()).toContain(
      "will be gone from disk",
    );
  });

  it("sends the picked steps and nothing else", async () => {
    const wrapper = await openWith([run()]);
    await wrapper.find(".mid-card").trigger("click");
    await wrapper.findAll(".mid-step input")[0].setValue(false);
    importRun.mockResolvedValue({ run_name: "Clementine", files: [] });

    await wrapper.findAll("button").at(-1).trigger("click");
    expect(importRun).toHaveBeenCalledWith({
      sourceFolderId: 1,
      runName: "Clementine",
      destinationFolderId: 2,
      steps: [null],
    });
  });

  it("cannot be submitted with nothing ticked", async () => {
    const wrapper = await openWith([run()]);
    await wrapper.find(".mid-card").trigger("click");
    for (const box of wrapper.findAll(".mid-step input")) {
      await box.setValue(false);
    }
    expect(
      wrapper.findAll("button").at(-1).attributes("disabled"),
    ).toBeDefined();
  });
});
