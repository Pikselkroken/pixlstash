// The ai-toolkit training-runs view - the model shelf's second tab.
//
// The assertions worth having are the ones guarding promises this view is the
// only place to keep. Drawing the grid must import nothing. A run with no bare
// final file must SAY its cover is a guess (it is still training or was
// interrupted, and importing it silently is how the wrong step becomes the
// cover of a stack). A reload happens unprompted, so it must not move the
// ground under someone mid-decision. And a batch must go out SEQUENTIALLY -
// `POST /model-imports` holds a non-blocking lock, so a concurrent fan-out
// would 409 every request after the first.

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
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useNoticeStore } from "../../stores/useNoticeStore";

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      AiToolkitIcon: true,
      // Renders the activator AND the panel inline. The real one teleports, so
      // nothing inside a menu would be findable.
      "v-menu": {
        props: ["modelValue"],
        template: "<div><slot name='activator' :props='{}' /><slot /></div>",
      },
      AppButton: {
        template: "<button :disabled='disabled'><slot /></button>",
        props: ["disabled", "loading", "variant", "iconLeft", "size"],
      },
    },
  },
};

const FOLDERS = [
  { id: 1, path: "/runs", kind: "source", delete_after_import: false },
  { id: 2, path: "/models/store", kind: "managed", movable: "root_only" },
  { id: 3, path: "/hf-cache", kind: "foreign", movable: "external" },
];

function run(name = "Clementine", overrides = {}) {
  return {
    name,
    checkpoints: [
      { filename: `${name}_000000500.safetensors`, step: 500, size: 1000 },
      { filename: `${name}.safetensors`, step: null, size: 1000 },
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
// a later test fires - which is exactly how the teardown assertion below first
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

/** Tick the card whose name matches, the way a pointer would. */
async function tick(wrapper, name) {
  const card = wrapper
    .findAll(".tr-card")
    .find((c) => c.find(".tr-card-name").text() === name);
  await card.trigger("click");
  return card;
}

const importBtn = (wrapper) =>
  wrapper.findAll("button").find((b) => b.text().startsWith("Import"));

beforeEach(() => {
  setActivePinia(createPinia());
  listRuns.mockReset();
  importRun.mockReset();
  useModelShelfStore().fetchRows = vi.fn();
});

afterEach(() => {
  while (mounted.length) mounted.pop().unmount();
});

describe("drawing the grid", () => {
  it("describes every run without importing any of it", async () => {
    // The listing route's whole promise: it hashes, copies and writes nothing,
    // so the grid is drawn before the user has decided about anything. It is
    // also what makes reloading on every focus affordable.
    const wrapper = await openWith([run(), run("Foxglove")]);
    expect(wrapper.findAll(".tr-card")).toHaveLength(2);
    expect(importRun).not.toHaveBeenCalled();
  });

  it("covers with the first prompt at the highest step", async () => {
    // Highest step is what the run has learned so far. First PROMPT, not the
    // last rendered: `index` separates prompts within a step rather than time,
    // so the cover stays on one prompt and two cards stay comparable.
    const wrapper = await openWith([
      run("Clementine", {
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
    const unfinished = run("Clementine", {
      checkpoints: [
        { filename: "Clementine_000000500.safetensors", step: 500, size: 1 },
      ],
    });
    const wrapper = await openWith([unfinished]);
    expect(wrapper.find(".tr-card-note").text()).toContain("No final file yet");
  });

  it("keeps a run importable when its config could not be read", async () => {
    // Steps and samples come from filenames, so the config is decoration.
    const wrapper = await openWith([
      run("Clementine", { config_error: "bad" }),
    ]);
    expect(wrapper.text()).toContain("The steps still import");
    await tick(wrapper, "Clementine");
    expect(importBtn(wrapper).attributes("disabled")).toBeUndefined();
  });

  it("offers the way out when no output folder is set", async () => {
    const wrapper = await openWith([], [FOLDERS[1]]);
    expect(wrapper.text()).toContain("No ai-toolkit output folder is set");
    expect(listRuns).not.toHaveBeenCalled();
    await wrapper
      .findAll("button")
      .find((b) => b.text().includes("Set ai-toolkit folder"))
      .trigger("click");
    expect(wrapper.emitted("set-folder")).toBeTruthy();
  });

  it("reads the runs the moment the folder is set, without a remount", async () => {
    // The case this view is most likely to be in when the folder is set: its
    // OWN empty state is the control that sets it, so the shelf's "show the
    // runs" answer is a no-op - the runs tab is already showing. Depending on
    // `onMounted` alone left the panel blank beside a folder it now had.
    const wrapper = await openWith([], [FOLDERS[1]]);
    expect(wrapper.text()).toContain("No ai-toolkit output folder is set");
    expect(listRuns).not.toHaveBeenCalled();

    listRuns.mockResolvedValue([run(), run("Foxglove")]);
    useModelFoldersStore().folders = FOLDERS;
    await settle(wrapper);

    expect(listRuns).toHaveBeenCalledWith(1);
    expect(wrapper.findAll(".tr-card")).toHaveLength(2);
  });

  it("says it is working while the first read runs, rather than showing blank", async () => {
    // A directory walk plus a config parse per run is not instant, and an empty
    // panel reads as "there is nothing here" rather than as "working".
    let release;
    listRuns.mockReturnValue(new Promise((r) => (release = r)));
    const store = useModelFoldersStore();
    store.folders = FOLDERS;
    store.loaded = true;
    const wrapper = mount(TrainingRuns, globalOpts);
    mounted.push(wrapper);
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".tr-loading").exists()).toBe(true);
    expect(wrapper.text()).toContain("Reading runs…");

    release([run()]);
    await settle(wrapper);
    expect(wrapper.find(".tr-loading").exists()).toBe(false);
    expect(wrapper.findAll(".tr-card")).toHaveLength(1);
  });

  it("keeps the grid up while a RELOAD runs, instead of replacing it", async () => {
    // The reload button carries its own spinner. Swapping a list somebody is
    // reading for a loading state is worse than leaving it up a moment longer.
    const wrapper = await openWith([run()]);
    let release;
    listRuns.mockReturnValue(new Promise((r) => (release = r)));
    document.dispatchEvent(new Event("visibilitychange"));
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".tr-loading").exists()).toBe(false);
    expect(wrapper.findAll(".tr-card")).toHaveLength(1);
    release([run(), run("Foxglove")]);
    await settle(wrapper);
    expect(wrapper.findAll(".tr-card")).toHaveLength(2);
  });

  it("still finds a destination when the registry lands after mount", async () => {
    // The cold-start / direct-navigation case: `folders.refresh()` is not
    // awaited, so `destinations` is EMPTY at mount. Choosing the default there
    // left `destinationId` null with nothing to re-derive it, and `canSubmit`
    // requires one - so the reader could tick runs and find Import disabled
    // with nothing on screen saying why.
    listRuns.mockResolvedValue([run()]);
    const store = useModelFoldersStore();
    store.folders = [];
    store.loaded = true;
    const wrapper = mount(TrainingRuns, globalOpts);
    mounted.push(wrapper);
    await settle(wrapper);

    store.folders = FOLDERS;
    await settle(wrapper);

    await tick(wrapper, "Clementine");
    expect(importBtn(wrapper).attributes("disabled")).toBeUndefined();
    importRun.mockResolvedValue({ run_name: "Clementine", files: [] });
    await importBtn(wrapper).trigger("click");
    await settle(wrapper);
    expect(importRun.mock.calls[0][0].destinationFolderId).toBe(2);
  });

  it("re-chooses when the selected destination stops being one", async () => {
    // Forgotten, or relocated into `external`. Holding the id would send an
    // import at a folder the server refuses.
    const wrapper = await openWith([run()]);
    await tick(wrapper, "Clementine");
    const store = useModelFoldersStore();
    store.folders = [
      FOLDERS[0],
      { id: 9, path: "/models/other", kind: "user", movable: "per_item" },
    ];
    await settle(wrapper);

    importRun.mockResolvedValue({ run_name: "Clementine", files: [] });
    await importBtn(wrapper).trigger("click");
    await settle(wrapper);
    expect(importRun.mock.calls[0][0].destinationFolderId).toBe(9);
  });

  it("tells the shelf how many runs there are, for the count beside the tabs", async () => {
    const wrapper = await openWith([run(), run("Foxglove")]);
    expect(wrapper.emitted("count").at(-1)).toEqual([2]);
  });
});

describe("choosing runs", () => {
  it("takes more than one, because an import is a batch", async () => {
    const wrapper = await openWith([run(), run("Foxglove"), run("Hazel")]);
    await tick(wrapper, "Clementine");
    await tick(wrapper, "Hazel");
    expect(wrapper.findAll(".tr-card--checked")).toHaveLength(2);
    expect(importBtn(wrapper).text()).toContain("2 runs");
  });

  it("unticks what is already ticked", async () => {
    const wrapper = await openWith([run(), run("Foxglove")]);
    await tick(wrapper, "Clementine");
    await tick(wrapper, "Clementine");
    expect(wrapper.findAll(".tr-card--checked")).toHaveLength(0);
    // With nothing ticked the pill is gone, not merely disabled.
    expect(wrapper.find(".selbar").exists()).toBe(false);
  });

  it("selects all shown and clears again", async () => {
    const wrapper = await openWith([run(), run("Foxglove"), run("Hazel")]);
    await tick(wrapper, "Clementine");
    await wrapper
      .findAll(".shelf-mi")
      .find((b) => b.text().includes("Select all shown"))
      .trigger("click");
    expect(wrapper.findAll(".tr-card--checked")).toHaveLength(3);

    await wrapper
      .findAll(".shelf-mi")
      .find((b) => b.text().includes("Clear selection"))
      .trigger("click");
    expect(wrapper.findAll(".tr-card--checked")).toHaveLength(0);
  });

  it("clears on Escape", async () => {
    const wrapper = await openWith([run(), run("Foxglove")]);
    await tick(wrapper, "Clementine");
    await wrapper.find(".tr-grid").trigger("keydown", { key: "Escape" });
    expect(wrapper.findAll(".tr-card--checked")).toHaveLength(0);
  });

  it("offers the checkpoint picker only while exactly one run is chosen", async () => {
    // At two or more the answer is every checkpoint in each: a per-step list
    // across five runs is forty checkboxes for a decision nobody came here for.
    const wrapper = await openWith([run(), run("Foxglove")]);
    await tick(wrapper, "Clementine");
    expect(wrapper.findAll(".tr-step")).toHaveLength(2);

    await tick(wrapper, "Foxglove");
    expect(wrapper.findAll(".tr-step")).toHaveLength(0);
    expect(importBtn(wrapper).text()).toContain("4 files");
  });

  it("never offers a source or an external folder as a destination", async () => {
    // A source folder is taken from, never written into (the server refuses
    // it); an external one is shared with other software.
    const wrapper = await openWith([run()]);
    await tick(wrapper, "Clementine");
    const paths = wrapper
      .findAll('[role="menuitemradio"]')
      .map((b) => b.text());
    expect(paths).toEqual(["/models/store"]);
  });

  it("warns before importing when the folder deletes its runs", async () => {
    const wrapper = await openWith(
      [run()],
      [{ ...FOLDERS[0], delete_after_import: true }, FOLDERS[1]],
    );
    await tick(wrapper, "Clementine");
    expect(wrapper.find(".tr-warning").text()).toContain(
      "will be gone from disk",
    );
  });
});

describe("importing the batch", () => {
  it("sends one request per run, in order, never concurrently", async () => {
    // `POST /model-imports` takes SHELF_IO_LOCK with blocking=False, so a
    // fan-out would 409 everything after the first. Each call must therefore be
    // settled before the next is made.
    let inFlight = 0;
    let overlapped = false;
    importRun.mockImplementation(async () => {
      inFlight += 1;
      if (inFlight > 1) overlapped = true;
      await new Promise((r) => setTimeout(r, 0));
      inFlight -= 1;
      return { run_name: "x", files: [] };
    });

    const wrapper = await openWith([run(), run("Foxglove")]);
    await tick(wrapper, "Clementine");
    await tick(wrapper, "Foxglove");
    await importBtn(wrapper).trigger("click");
    await settle(wrapper);

    expect(overlapped).toBe(false);
    expect(importRun.mock.calls.map((c) => c[0].runName)).toEqual([
      "Clementine",
      "Foxglove",
    ]);
  });

  it("sends only the ticked checkpoints when one run is chosen", async () => {
    const wrapper = await openWith([run()]);
    await tick(wrapper, "Clementine");
    await wrapper.findAll(".tr-step input")[0].setValue(false);
    importRun.mockResolvedValue({ run_name: "Clementine", files: [] });

    await importBtn(wrapper).trigger("click");
    await settle(wrapper);

    expect(importRun).toHaveBeenCalledWith({
      sourceFolderId: 1,
      runName: "Clementine",
      destinationFolderId: 2,
      steps: [null],
    });
  });

  it("sends every checkpoint of every run when several are chosen", async () => {
    const wrapper = await openWith([run(), run("Foxglove")]);
    await tick(wrapper, "Clementine");
    await tick(wrapper, "Foxglove");
    importRun.mockResolvedValue({ run_name: "x", files: [] });

    await importBtn(wrapper).trigger("click");
    await settle(wrapper);

    expect(importRun.mock.calls.map((c) => c[0].steps)).toEqual([
      [500, null],
      [500, null],
    ]);
  });

  it("finishes the batch when one run fails, and names it", async () => {
    // Stopping at the first failure would leave the user unable to tell which
    // of the five are now on the shelf.
    importRun.mockImplementation(async ({ runName }) => {
      if (runName === "Foxglove") throw new Error("nope");
      return { run_name: runName, files: [] };
    });

    const wrapper = await openWith([run(), run("Foxglove"), run("Hazel")]);
    await tick(wrapper, "Clementine");
    await tick(wrapper, "Foxglove");
    await tick(wrapper, "Hazel");
    await importBtn(wrapper).trigger("click");
    await settle(wrapper);

    expect(importRun).toHaveBeenCalledTimes(3);
    const notice = useNoticeStore().notices.at(-1);
    expect(notice.level).toBe("warning");
    expect(notice.text).toContain("Imported 2 runs");
    expect(notice.text).toContain("Foxglove");
  });

  it("cannot be submitted with no checkpoints ticked", async () => {
    const wrapper = await openWith([run()]);
    await tick(wrapper, "Clementine");
    for (const box of wrapper.findAll(".tr-step input"))
      await box.setValue(false);
    expect(importBtn(wrapper).attributes("disabled")).toBeDefined();
  });

  it("cannot be submitted with no source root registered", async () => {
    // Both ends have to be named. Without the source clause, `submit` reaches
    // its loop with no `sourceFolderId` and spends a request per run to be
    // told so - and the receipt reports each refusal against the run rather
    // than against the missing folder.
    //
    // Asserted BEFORE the flush deliberately. The watcher empties the rows and
    // the tick when the root goes away, so a tick later `chosenRuns` is empty
    // and every other clause of `canSubmit` is false too - the assertion would
    // then pass with the source clause deleted. This instant, with the folder
    // already gone from the registry and the rows still up, is the only one
    // where the clause is what answers.
    const wrapper = await openWith([run()]);
    await tick(wrapper, "Clementine");
    expect(wrapper.vm.canSubmit).toBe(true);

    useModelFoldersStore().folders = [FOLDERS[1], FOLDERS[2]];
    expect(wrapper.vm.canSubmit).toBe(false);

    await settle(wrapper);
    expect(wrapper.vm.canSubmit).toBe(false);
    expect(importRun).not.toHaveBeenCalled();
  });
});

describe("staying current without moving the ground", () => {
  it("picks up runs that appeared since the list was read", async () => {
    // The whole reason this is a view and not a dialog. A dialog was read once
    // and dismissed, so a run that finished while it was open was invisible.
    const wrapper = await openWith([run()]);
    expect(wrapper.findAll(".tr-card")).toHaveLength(1);

    listRuns.mockResolvedValue([run(), run("Foxglove")]);
    document.dispatchEvent(new Event("visibilitychange"));
    await settle(wrapper);

    expect(wrapper.findAll(".tr-card")).toHaveLength(2);
  });

  it("keeps every ticked run, and the ticked checkpoints, across a reload", async () => {
    // A reload fires on its own, so it must not discard a decision in progress.
    const wrapper = await openWith([run(), run("Foxglove")]);
    await tick(wrapper, "Clementine");
    await wrapper.findAll(".tr-step input")[0].setValue(false);

    listRuns.mockResolvedValue([run(), run("Foxglove")]);
    window.dispatchEvent(new Event("focus"));
    await settle(wrapper);

    expect(wrapper.findAll(".tr-card--checked")).toHaveLength(1);
    const boxes = wrapper.findAll(".tr-step input");
    expect(boxes[0].element.checked).toBe(false);
    expect(boxes[1].element.checked).toBe(true);
  });

  it("drops only the runs that are gone, keeping the rest ticked", async () => {
    const wrapper = await openWith([run(), run("Foxglove"), run("Hazel")]);
    await tick(wrapper, "Clementine");
    await tick(wrapper, "Foxglove");

    listRuns.mockResolvedValue([run("Foxglove"), run("Hazel")]);
    document.dispatchEvent(new Event("visibilitychange"));
    await settle(wrapper);

    const checked = wrapper
      .findAll(".tr-card--checked")
      .map((c) => c.find(".tr-card-name").text());
    expect(checked).toEqual(["Foxglove"]);
  });

  // Mount, a folder change, a visibility change and a window focus each start a
  // read, and none of them cancels the last, so two are in flight whenever one
  // is slow. Ordering is not promised: the older read can answer last, and what
  // it carries is another folder's runs - or the same folder's, from before the
  // run that just finished existed.
  describe("with two reads in flight", () => {
    const SOURCE_B = {
      id: 4,
      path: "/other-runs",
      kind: "source",
      delete_after_import: false,
    };

    function deferred() {
      let resolve;
      let reject;
      const promise = new Promise((res, rej) => {
        resolve = res;
        reject = rej;
      });
      return { promise, resolve, reject };
    }

    /** Mount against folder 1 with its read held, then register folder 4. */
    async function switchSource() {
      const a = deferred();
      const b = deferred();
      listRuns.mockReturnValueOnce(a.promise).mockReturnValueOnce(b.promise);
      const store = useModelFoldersStore();
      store.folders = FOLDERS;
      store.loaded = true;
      const wrapper = mount(TrainingRuns, globalOpts);
      mounted.push(wrapper);
      await wrapper.vm.$nextTick();
      expect(listRuns).toHaveBeenNthCalledWith(1, 1);

      store.folders = [SOURCE_B, FOLDERS[1], FOLDERS[2]];
      await wrapper.vm.$nextTick();
      expect(listRuns).toHaveBeenNthCalledWith(2, 4);
      return { wrapper, a, b };
    }

    it("takes the old folder's rows down the moment the folder changes", async () => {
      // The window that makes #1019 reachable without any response ordering at
      // all: the new folder's walk takes as long as it takes, and until it
      // lands the OLD folder's cards are sitting under the NEW folder's path
      // with Import live. A tick that survives that sends the new folder's id
      // with a run name read from the old one.
      const wrapper = await openWith([run(), run("Foxglove")]);
      await tick(wrapper, "Clementine");

      let release;
      listRuns.mockReturnValue(new Promise((r) => (release = r)));
      useModelFoldersStore().folders = [SOURCE_B, FOLDERS[1], FOLDERS[2]];
      await wrapper.vm.$nextTick();

      expect(wrapper.findAll(".tr-card")).toHaveLength(0);
      expect(importBtn(wrapper)).toBeUndefined();
      expect(wrapper.emitted("count").at(-1)).toEqual([null]);
      expect(wrapper.find(".tr-loading").exists()).toBe(true);

      release([run("Hazel")]);
      await settle(wrapper);
      expect(wrapper.findAll(".tr-card-name").map((n) => n.text())).toEqual([
        "Hazel",
      ]);
    });

    it("keeps the new folder's runs when the old folder answers last", async () => {
      const { wrapper, a, b } = await switchSource();

      b.resolve([run("Foxglove")]);
      await settle(wrapper);
      a.resolve([run("Clementine")]);
      await settle(wrapper);

      expect(wrapper.findAll(".tr-card-name").map((n) => n.text())).toEqual([
        "Foxglove",
      ]);
      expect(wrapper.emitted("count").at(-1)).toEqual([1]);
    });

    it("does not report the current read finished when an older one ends", async () => {
      const { wrapper, a } = await switchSource();

      a.resolve([run("Clementine")]);
      await settle(wrapper);

      // Folder 4's read is still running, so the panel must still say so
      // rather than showing folder 1's grid or an empty one.
      expect(wrapper.findAll(".tr-card")).toHaveLength(0);
      expect(wrapper.find(".tr-loading").exists()).toBe(true);
    });

    it("does not show the old folder's failure against the new folder", async () => {
      const { wrapper, a, b } = await switchSource();

      b.resolve([run("Foxglove")]);
      await settle(wrapper);
      a.reject(new Error("gone"));
      await settle(wrapper);

      expect(wrapper.find('[role="alert"]').exists()).toBe(false);
      expect(wrapper.findAll(".tr-card")).toHaveLength(1);
    });

    it("imports from the folder the chosen runs were read under", async () => {
      // The batch is sequential and each request is awaited, so the registry
      // can change between two of them. Every run in the batch came out of ONE
      // listing, and both roots can hold a run of the same name - so naming
      // whichever folder is registered when its turn arrives is how run 2 gets
      // imported from a folder its row was never read from.
      const wrapper = await openWith([run(), run("Foxglove")]);
      await tick(wrapper, "Clementine");
      await tick(wrapper, "Foxglove");

      const store = useModelFoldersStore();
      importRun.mockImplementation(async () => {
        store.folders = [SOURCE_B, FOLDERS[1], FOLDERS[2]];
        return { run_name: "x", files: [] };
      });
      await importBtn(wrapper).trigger("click");
      await settle(wrapper);

      expect(importRun.mock.calls.map((c) => c[0].sourceFolderId)).toEqual([
        1, 1,
      ]);
    });

    it("drops the selection when the folder changes, rather than re-matching names", async () => {
      const wrapper = await openWith([run(), run("Foxglove")]);
      await tick(wrapper, "Clementine");
      expect(wrapper.findAll(".tr-card--checked")).toHaveLength(1);

      listRuns.mockResolvedValue([run(), run("Foxglove")]);
      useModelFoldersStore().folders = [SOURCE_B, FOLDERS[1], FOLDERS[2]];
      await settle(wrapper);

      expect(wrapper.findAll(".tr-card")).toHaveLength(2);
      expect(wrapper.findAll(".tr-card--checked")).toHaveLength(0);
    });

    it("keeps the newer listing when the same folder is read twice", async () => {
      // Two focus events, no folder change: the older read still must not win,
      // or a run that has just finished importing comes back.
      const wrapper = await openWith([run()]);
      const first = deferred();
      const second = deferred();
      listRuns
        .mockReturnValueOnce(first.promise)
        .mockReturnValueOnce(second.promise);
      window.dispatchEvent(new Event("focus"));
      document.dispatchEvent(new Event("visibilitychange"));
      await wrapper.vm.$nextTick();

      second.resolve([run("Foxglove")]);
      await settle(wrapper);
      first.resolve([run(), run("Foxglove"), run("Hazel")]);
      await settle(wrapper);

      expect(wrapper.findAll(".tr-card-name").map((n) => n.text())).toEqual([
        "Foxglove",
      ]);
    });
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
