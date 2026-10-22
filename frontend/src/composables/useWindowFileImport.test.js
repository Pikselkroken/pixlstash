// useWindowFileImport - what the window-wide catch-all does with a drop.
//
// Two things, and they used to be one. A model file went to the PICTURE
// importer, which uploaded it in full before the backend skipped it as
// unsupported and the commit failed with "No staged files to import"; now it
// goes to the shelf, by path, through the same POST /model-files the Add file…
// menu calls. Everything else is filtered against what the staging route
// actually accepts instead of against the (much wider) list of what the app can
// display. Both directions are pinned: an unsupported drop imports nothing and
// says so, a supported one still imports, and a model file lands on the shelf -
// over-filtering would be its own regression.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { ref } from "vue";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import { useWindowFileImport } from "./useWindowFileImport";
import { useNoticeStore } from "../stores/useNoticeStore";
import { addModelFile } from "../api/modelFiles";
import { useModelShelfStore } from "../stores/useModelShelfStore";
import { useModelFoldersStore } from "../stores/useModelFoldersStore";
import { listAdapters, listCheckpoints } from "../api/modelShelf";
import {
  modelDropChoice,
  settleModelDropChoice,
} from "./useModelDropTarget";

vi.mock("../api/modelFiles", () => ({ addModelFile: vi.fn() }));
vi.mock("../api/modelShelf", () => ({
  listAdapters: vi.fn(),
  listCheckpoints: vi.fn(),
}));

const startLocalImport = vi.fn();

const Host = {
  setup() {
    useWindowFileImport({
      sidebarRef: ref({ startLocalImport, currentProjectId: null }),
    });
    return () => null;
  },
};

let wrapper;
let notices;

function paste(...mimes) {
  const event = new Event("paste", { bubbles: true, cancelable: true });
  event.clipboardData = {
    items: mimes.map((type, i) => ({
      kind: "file",
      type,
      getAsFile: () => new File(["x"], `pasted-${i}.${type.split("/").pop()}`),
    })),
  };
  window.dispatchEvent(event);
}

function drop(...names) {
  return dropOn(document.body, ...names);
}

/** The same drop, aimed at an element - the grid claims its own. */
function dropOn(target, ...names) {
  const files = names.map((name) => new File(["x"], name));
  const event = new Event("drop", { bubbles: true, cancelable: true });
  event.dataTransfer = { files, items: [], types: ["Files"] };
  target.dispatchEvent(event);
  // The handler awaits the DataTransfer walk before importing.
  return new Promise((resolve) => setTimeout(resolve, 0));
}

/** A drop of a FOLDER: only the recursive walk can see what is inside. */
function dropFolder(folderName, ...names) {
  const files = names.map((name) => new File(["x"], name));
  const entry = {
    isFile: false,
    isDirectory: true,
    name: folderName,
    createReader: () => {
      let done = false;
      return {
        readEntries(cb) {
          if (done) return cb([]);
          done = true;
          cb(
            files.map((file) => ({
              isFile: true,
              isDirectory: false,
              name: file.name,
              file: (ok) => ok(file),
            })),
          );
        },
      };
    },
  };
  const event = new Event("drop", { bubbles: true, cancelable: true });
  event.dataTransfer = {
    // What a folder drop really looks like: one opaque entry in `files`.
    files: [new File([], folderName)],
    items: [{ kind: "file", webkitGetAsEntry: () => entry }],
    types: ["Files"],
  };
  document.body.dispatchEvent(event);
  return new Promise((resolve) => setTimeout(resolve, 0));
}

/** The desktop shell's answer to "where did this file come from?". */
function withDesktopShell(pathByName) {
  window.pixlstashDesktop = {
    getDroppedFilePath: (file) => pathByName[file.name] || "",
  };
}

beforeEach(() => {
  setActivePinia(createPinia());
  notices = useNoticeStore();
  startLocalImport.mockClear();
  addModelFile.mockReset();
  // The route answers with the name it registered; echo it so a test can tell
  // one file's receipt from another's.
  addModelFile.mockImplementation(async (path) => ({
    filename: String(path).split("/").pop(),
  }));
  // The shelf refresh is a fetch either way; this test is about the routing.
  vi.spyOn(useModelShelfStore(), "fetchRows").mockResolvedValue(undefined);
  vi.spyOn(useModelFoldersStore(), "refresh").mockResolvedValue(undefined);
  wrapper = mount(Host);
});

afterEach(() => {
  wrapper.unmount();
  delete window.pixlstashDesktop;
  vi.restoreAllMocks();
});

describe("useWindowFileImport", () => {
  it("adds a dropped model file to the shelf, by path, on the desktop", async () => {
    withDesktopShell({ "lora.safetensors": "/models/lora.safetensors" });
    await drop("lora.safetensors");
    expect(addModelFile).toHaveBeenCalledWith("/models/lora.safetensors", null);
    // Never the picture importer: that is the 242 MB upload this replaces.
    expect(startLocalImport).not.toHaveBeenCalled();
    expect(notices.notices.at(-1).level).toBe("success");
  });

  it("points a browser tab at the menu instead, and uploads nothing", async () => {
    // No shell, so no path - and without a path there is nothing to send.
    await drop("lora.safetensors");
    expect(addModelFile).not.toHaveBeenCalled();
    expect(startLocalImport).not.toHaveBeenCalled();
    expect(notices.notices.at(-1).level).toBe("warning");
    expect(notices.notices.at(-1).text).toContain("Add file");
  });

  it("reports a refused model file rather than a bare count", async () => {
    withDesktopShell({ "lora.safetensors": "/models/lora.safetensors" });
    addModelFile.mockRejectedValue({
      response: { data: { detail: "That file is already inside a folder." } },
    });
    await drop("lora.safetensors");
    const last = notices.notices.at(-1);
    expect(last.level).toBe("error");
    expect(last.text).toContain("already inside");
  });

  it("still imports a supported picture", async () => {
    await drop("holiday.jpg");
    expect(startLocalImport).toHaveBeenCalledTimes(1);
    expect(startLocalImport.mock.calls[0][0].map((f) => f.name)).toEqual([
      "holiday.jpg",
    ]);
  });

  it("does not import a file the staging route would refuse", async () => {
    // `.psd` is a format the app can display, so the display-side extension
    // lists say yes and the import route says no. Before the split it uploaded
    // in full and died on the commit, exactly like the model file did.
    await drop("layers.psd");
    expect(startLocalImport).not.toHaveBeenCalled();
    expect(notices.notices.length).toBe(1);
  });

  it("does not paste a file the staging route would refuse", () => {
    // The clipboard calls a Photoshop file an image; the importer does not.
    paste("image/vnd.adobe.photoshop");
    expect(startLocalImport).not.toHaveBeenCalled();
  });

  it("still pastes a screenshot", () => {
    paste("image/png");
    expect(startLocalImport).toHaveBeenCalledTimes(1);
  });

  // F1b (#1404) retired the shelf, and the grid that replaced it has no drop
  // handler — a workflow file is added through *Add…*. The stand-aside that
  // let the shelf claim a JSON-only drop went with it, so there is no longer
  // any element this listener treats specially: JSON is reported as
  // unsupported wherever it lands.
  //
  // Deliberately NOT written as "dropped on `.wfv`". Nothing reads a class
  // here any more, so a test that built one would pass whatever it was named
  // — an assertion about generic handling wearing a view-specific title.
  it("reports workflow JSON as unsupported wherever it is dropped", async () => {
    await drop("flow.json");
    expect(notices.notices.length).toBe(1);
    expect(startLocalImport).not.toHaveBeenCalled();
  });

  it("still takes a picture dropped alongside a workflow file", async () => {
    await drop("flow.json", "holiday.jpg");
    expect(startLocalImport).toHaveBeenCalledTimes(1);
  });

  it("takes a model dropped on the image grid, which has no use for it", async () => {
    const grid = document.createElement("div");
    grid.className = "image-grid";
    document.body.appendChild(grid);
    try {
      withDesktopShell({ "lora.safetensors": "/models/lora.safetensors" });
      await dropOn(grid, "lora.safetensors");
      expect(addModelFile).toHaveBeenCalledWith("/models/lora.safetensors", null);
    } finally {
      grid.remove();
    }
  });

  it("leaves the pictures of a mixed grid drop to the grid", async () => {
    const grid = document.createElement("div");
    grid.className = "image-grid";
    document.body.appendChild(grid);
    try {
      withDesktopShell({ "lora.safetensors": "/models/lora.safetensors" });
      await dropOn(grid, "lora.safetensors", "holiday.jpg");
      expect(addModelFile).toHaveBeenCalledWith("/models/lora.safetensors", null);
      // The grid's own handler imports them, with the selected character
      // threaded in - something this handler cannot do.
      expect(startLocalImport).not.toHaveBeenCalled();
    } finally {
      grid.remove();
    }
  });

  it("finds a model inside a dropped folder, beside its samples", async () => {
    // A trainer's output directory. The flat file list shows one opaque entry,
    // so this used to import the samples and lose the adapter in silence.
    withDesktopShell({ "lora.safetensors": "/runs/out/lora.safetensors" });
    await dropFolder("out", "lora.safetensors", "sample-01.jpg");
    expect(addModelFile).toHaveBeenCalledWith("/runs/out/lora.safetensors", null);
    expect(startLocalImport.mock.calls[0][0].map((f) => f.name)).toEqual([
      "sample-01.jpg",
    ]);
  });

  it("does not hold the pictures of a mixed drop behind the model copy", async () => {
    // A multi-gigabyte copy runs for minutes; the photos must not queue.
    let finishCopy;
    addModelFile.mockImplementation(
      () =>
        new Promise((resolve) => {
          finishCopy = () => resolve({ filename: "lora.safetensors" });
        }),
    );
    withDesktopShell({ "lora.safetensors": "/models/lora.safetensors" });
    await drop("lora.safetensors", "holiday.jpg");
    expect(startLocalImport).toHaveBeenCalledTimes(1);
    finishCopy();
  });

  it("gives each drop its own card, so one copy cannot erase another", async () => {
    let finishFirst;
    addModelFile.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishFirst = () => resolve({ filename: "big.safetensors" });
        }),
    );
    withDesktopShell({
      "big.safetensors": "/models/big.safetensors",
      "small.safetensors": "/models/small.safetensors",
    });
    await drop("big.safetensors");
    await drop("small.safetensors");
    // The big copy is still running, so its card must still be up next to the
    // small one's receipt - a shared key dismissed it and said "Added".
    const texts = notices.notices.map((n) => n.text);
    expect(texts.some((t) => t.includes("big.safetensors"))).toBe(true);
    expect(texts.some((t) => t.includes("Added small.safetensors"))).toBe(true);
    finishFirst();
  });

  it("splits a mixed drop between the shelf and the importer", async () => {
    withDesktopShell({ "lora.safetensors": "/models/lora.safetensors" });
    await drop("lora.safetensors", "holiday.jpg");
    expect(addModelFile).toHaveBeenCalledWith("/models/lora.safetensors", null);
    expect(startLocalImport.mock.calls[0][0].map((f) => f.name)).toEqual([
      "holiday.jpg",
    ]);
  });

  describe("where a dropped model lands", () => {
    // Folder 1 is the default; 3 is a ComfyUI folder the owner registered; 9
    // is an ai-toolkit output root, which a file is never put into.
    const FOLDERS = [
      { id: 1, kind: "managed", path: "/home/me/models", is_default: true },
      { id: 3, kind: "user", path: "/home/me/ComfyUI/models", is_default: false },
      { id: 4, kind: "user", path: "/home/me/more-models", is_default: false },
      { id: 9, kind: "source", path: "/home/me/ai-toolkit/output", is_default: false },
    ];
    const at = (folder_id, state = "present") => ({ folder_id, state });

    let target;
    beforeEach(() => {
      const folders = useModelFoldersStore();
      folders.folders = FOLDERS;
      folders.loaded = true;
      listCheckpoints.mockResolvedValue([
        { id: 5, sha256: null, locations: [at(3)] },
      ]);
      listAdapters.mockResolvedValue([
        { id: 6, sha256: "a".repeat(64), locations: [at(3), at(9)] },
        { id: 7, sha256: "b".repeat(64), locations: [at(1)] },
        { id: 8, sha256: "c".repeat(64), locations: [at(3, "missing")] },
        { id: 10, sha256: "d".repeat(64), locations: [at(4)] },
      ]);
      withDesktopShell({ "lora.safetensors": "/home/me/Downloads/lora.safetensors" });
      target = document.createElement("div");
      target.appendChild(document.createElement("span"));
      document.body.appendChild(target);
    });
    afterEach(() => {
      settleModelDropChoice(undefined);
      target.remove();
    });

    /** Let the folder lookup's awaits run out. */
    const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

    it("copies into the shelf folder it was dropped on", async () => {
      target.dataset.modelDropFolder = "3";
      await dropOn(target.firstChild, "lora.safetensors");
      expect(addModelFile).toHaveBeenCalledWith(
        "/home/me/Downloads/lora.safetensors",
        3,
      );
      expect(modelDropChoice.value).toBeNull();
    });

    it("asks where to put it when a card's models are elsewhere", async () => {
      // A checkpoint by id and a LoRA by hash, both in folder 3, and one more
      // LoRA in folder 4.
      target.dataset.modelDropModels = `5 ${"a".repeat(64)} 10`;
      await dropOn(target.firstChild, "lora.safetensors");
      await settle();
      expect(addModelFile).not.toHaveBeenCalled();
      // The folder holding most of them first, the default last, and never
      // the source root.
      expect(modelDropChoice.value.options.map((o) => o.id)).toEqual([3, 4, 1]);
      settleModelDropChoice(3);
      await settle();
      expect(addModelFile).toHaveBeenCalledWith(
        "/home/me/Downloads/lora.safetensors",
        3,
      );
    });

    it("adds nothing when the question is cancelled", async () => {
      target.dataset.modelDropModels = "5";
      await dropOn(target.firstChild, "lora.safetensors");
      await settle();
      settleModelDropChoice(undefined);
      await settle();
      expect(addModelFile).not.toHaveBeenCalled();
    });

    it("does not ask when the default folder already holds the card's models", async () => {
      target.dataset.modelDropModels = `5 ${"b".repeat(64)}`;
      await dropOn(target.firstChild, "lora.safetensors");
      await settle();
      expect(modelDropChoice.value).toBeNull();
      expect(addModelFile).toHaveBeenCalledWith(
        "/home/me/Downloads/lora.safetensors",
        null,
      );
    });

    it("does not ask when none of the card's models has a copy", async () => {
      // Model 8's only copy is missing, and a missing copy is no place.
      target.dataset.modelDropModels = "c".repeat(64);
      await dropOn(target.firstChild, "lora.safetensors");
      await settle();
      expect(modelDropChoice.value).toBeNull();
      expect(addModelFile).toHaveBeenCalledWith(
        "/home/me/Downloads/lora.safetensors",
        null,
      );
    });
  });
});
