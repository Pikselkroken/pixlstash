// The ai-toolkit training-runs view.
//
// The assertions worth having are the ones guarding promises the backend makes
// and this view is the only place to keep. Drawing the grid must not import
// anything; a run with no bare final file must SAY its cover is a guess (that
// run is either still training or was interrupted, and importing it silently is
// how the wrong step becomes the cover of a stack); and — new to the view — a
// reload must not move the ground under someone mid-decision, because unlike
// the dialog this replaced, reloads happen on their own.

import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
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

import TrainingRuns from "./TrainingRuns.vue";
import { useModelFoldersStore } from "../../stores/useModelFoldersStore";

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      AiToolkitIcon: true,
      AppButton: {
        template: "<button :disabled='disabled'><slot /></button>",
        props: ["disabled", "loading", "variant", "iconLeft"],
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

async function settle(wrapper) {
  await new Promise((resolve) => setTimeout(resolve, 0));
  await wrapper.vm.$nextTick();
  await wrapper.vm.$nextTick();
}

// Every mount is tracked and torn down. This view registers listeners on
// `document` and `window`, so a wrapper left mounted keeps answering the events
// a later test fires — which is exactly how the teardown assertion below first
// counted twenty-five reloads instead of one.
const mounted = [];

async function openWith(runs, folders = FOLDERS) {
  listRuns.mockResolvedValue(runs);
  const store = useModelFoldersStore();
  store.folders = folders;
  // Already read, so mounting does not go to the network for the registry.
  store.loaded = true;
  const wrapper = mount(TrainingRuns, globalOpts);
  mounted.push(wrapper);
  await settle(wrapper);
  return wrapper;
}

beforeEach(() => {
  setActivePinia(createPinia());
  listRuns.mockReset();
  importRun.mockReset();
});

afterEach(() => {
  while (mounted.length) mounted.pop().unmount();
});

describe("drawing the grid", () => {
  it("describes every run without importing any of it", async () => {
    // The listing route's whole promise: it hashes, copies and writes nothing,
    // so the grid is drawn before the user has decided about anything. It is
    // also what makes reloading on every focus affordable.
    const wrapper = await openWith([run(), run({ name: "Foxglove" })]);
    expect(wrapper.findAll(".tr-card")).toHaveLength(2);
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
    expect(wrapper.find(".tr-card-preview").attributes("src")).toContain(
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
    expect(wrapper.find(".tr-card-note").text()).toContain("No final file yet");
  });

  it("keeps a run importable when its config could not be read", async () => {
    // Steps and samples come from filenames, so the config is decoration.
    const wrapper = await openWith([run({ config_error: "bad yaml" })]);
    await wrapper.find(".tr-card").trigger("click");
    expect(wrapper.text()).toContain("The steps still import");
    expect(wrapper.find(".tr-steps").exists()).toBe(true);
  });

  it("points at the control that sets the folder when none is set", async () => {
    // The view is reachable with no output root only by URL, so its empty state
    // has to name the way out rather than describe the state it is in.
    const wrapper = await openWith([], [FOLDERS[1]]);
    expect(wrapper.text()).toContain("No ai-toolkit output folder is set");
    expect(wrapper.text()).toContain("Set ai-toolkit folder");
    expect(listRuns).not.toHaveBeenCalled();
  });
});

describe("choosing what to take", () => {
  it("ticks the whole run on pick, because a run is one stack", async () => {
    const wrapper = await openWith([run()]);
    await wrapper.find(".tr-card").trigger("click");
    const boxes = wrapper.findAll(".tr-step input");
    expect(boxes).toHaveLength(2);
    expect(boxes.every((b) => b.element.checked)).toBe(true);
  });

  it("never offers a source or an external folder as a destination", async () => {
    // A source folder is taken from, never written into (the server refuses
    // it); an external one is shared with other software.
    const wrapper = await openWith([run()]);
    await wrapper.find(".tr-card").trigger("click");
    const paths = wrapper
      .find(".tr-field select")
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
    await wrapper.find(".tr-card").trigger("click");
    expect(wrapper.find(".tr-warning").text()).toContain(
      "will be gone from disk",
    );
  });

  it("sends the picked steps and nothing else", async () => {
    const wrapper = await openWith([run()]);
    await wrapper.find(".tr-card").trigger("click");
    await wrapper.findAll(".tr-step input")[0].setValue(false);
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
    await wrapper.find(".tr-card").trigger("click");
    for (const box of wrapper.findAll(".tr-step input")) {
      await box.setValue(false);
    }
    expect(
      wrapper.findAll("button").at(-1).attributes("disabled"),
    ).toBeDefined();
  });
});

describe("staying current without moving the ground", () => {
  it("picks up runs that appeared since the list was read", async () => {
    // The whole reason this is a view and not a dialog. A dialog was read once
    // and dismissed, so a run that finished while it was open was invisible.
    const wrapper = await openWith([run()]);
    expect(wrapper.findAll(".tr-card")).toHaveLength(1);

    listRuns.mockResolvedValue([run(), run({ name: "Foxglove" })]);
    document.dispatchEvent(new Event("visibilitychange"));
    await settle(wrapper);

    expect(wrapper.findAll(".tr-card")).toHaveLength(2);
  });

  it("keeps the picked run and its ticked checkpoints across a reload", async () => {
    // A reload fires on its own, so it must not discard a decision in progress.
    // Untick one box, reload, and the choice has to survive: re-ticking every
    // checkpoint would silently import a step the user had just excluded.
    const wrapper = await openWith([run(), run({ name: "Foxglove" })]);
    await wrapper.find(".tr-card").trigger("click");
    await wrapper.findAll(".tr-step input")[0].setValue(false);

    listRuns.mockResolvedValue([run(), run({ name: "Foxglove" })]);
    window.dispatchEvent(new Event("focus"));
    await settle(wrapper);

    expect(wrapper.find(".tr-card--picked").text()).toContain("Clementine");
    const boxes = wrapper.findAll(".tr-step input");
    expect(boxes[0].element.checked).toBe(false);
    expect(boxes[1].element.checked).toBe(true);
  });

  it("drops the selection when the picked run is gone", async () => {
    // Imported from another window, or deleted on disk. Keeping the name would
    // leave the import bar pointing at a run that is no longer there.
    const wrapper = await openWith([run(), run({ name: "Foxglove" })]);
    await wrapper.find(".tr-card").trigger("click");
    expect(wrapper.find(".tr-bar").exists()).toBe(true);

    listRuns.mockResolvedValue([run({ name: "Foxglove" })]);
    document.dispatchEvent(new Event("visibilitychange"));
    await settle(wrapper);

    expect(wrapper.find(".tr-bar").exists()).toBe(false);
    expect(wrapper.find(".tr-card--picked").exists()).toBe(false);
  });

  it("stops listening once it is left", async () => {
    // The listeners are on `document` and `window`, so an unmounted view that
    // kept them would keep fetching runs for a screen nobody is looking at.
    const wrapper = await openWith([run()]);
    wrapper.unmount();
    mounted.pop();
    listRuns.mockClear();

    document.dispatchEvent(new Event("visibilitychange"));
    window.dispatchEvent(new Event("focus"));
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(listRuns).not.toHaveBeenCalled();
  });
});
