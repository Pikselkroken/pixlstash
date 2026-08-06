import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

// Imported statically, not lazily inside the tests. `vi.mock` is hoisted
// above every import, so a lazy import buys no mock ordering. It only moves
// the cost of compiling this 5.7k-line SFC (~7s on a loaded machine) inside
// the first test's 5s timeout, which is what made this file flake in the full
// suite while passing on its own.
import ImageOverlay from "./ImageOverlay.vue";

// A test that fails mid-way must not leave a mounted overlay behind: its
// window-level keydown listener would answer every later test in this file.
enableAutoUnmount(afterEach);

const apiGet = vi.fn(async (url) => {
  if (typeof url === "string" && url.includes("/workflow")) {
    const error = new Error("no workflow");
    error.response = { status: 404 };
    throw error;
  }
  if (typeof url === "string" && /\/pictures\/7\.jpg/.test(url)) {
    return { data: new Blob(["original-jpeg"], { type: "image/jpeg" }) };
  }
  return { data: [] };
});

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return {
    onSessionReset: () => () => {},
    sessionContext: { value: null },
    apiClient: { get: (...args) => apiGet(...args), post: vi.fn(), delete: vi.fn() },
    appendShareToken: (url) => url,
    isReadOnly: ref(false),
    setRequestClientId: vi.fn(),
  };
});


const STUBS = {
  OverlayTagsPanel: true,
  OverlayFilmstrip: true,
  OverlayDescriptionPanel: true,
  OverlayMetadataPanel: true,
  AddToEntityControl: true,
  CharacterEditor: true,
  StarRatingOverlay: true,
  PluginParametersUI: true,
  OverlayActionReceipt: true,
  OverlaySaveAsDialog: {
    props: ["open", "suggestedName", "originalExtension", "mediaNoun"],
    emits: ["close", "save"],
    data: () => ({ name: "" }),
    watch: {
      open: {
        immediate: true,
        handler(value) {
          if (!value) return;
          const suffix = `.${this.originalExtension}`;
          this.name = this.suggestedName.toLowerCase().endsWith(suffix)
            ? this.suggestedName.slice(0, -suffix.length)
            : this.suggestedName;
        },
      },
    },
    template: `
      <div v-if="open" class="save-as-test-dialog">
        <input id="overlay-save-as-name" :value="name" @input="name = $event.target.value" />
        <button @click="$emit('close')">Cancel</button>
        <button @click="$emit('save', name + '.' + originalExtension)">Download</button>
      </div>
    `,
  },
  "v-icon": true,
  "v-menu": true,
  "v-tooltip": true,
};

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const originalClipboard = navigator.clipboard;
const originalClipboardItem = window.ClipboardItem;
const originalPicker = window.showSaveFilePicker;
const originalDesktop = window.pixlstashDesktop;
const originalGetSelection = window.getSelection;
const originalCanvasContext = HTMLCanvasElement.prototype.getContext;
const originalCanvasToBlob = HTMLCanvasElement.prototype.toBlob;
const originalCreateObjectUrl = URL.createObjectURL;
const originalRevokeObjectUrl = URL.revokeObjectURL;

let clipboardWrite;
let canvasDraw;
let anchorClick;

async function openOverlay(media = {}) {
  const wrapper = mount(ImageOverlay, {
    props: {
      open: false,
      initialImageId: 7,
      allImages: [
        {
          id: 7,
          format: "jpg",
          original_file_name: "holiday.jpg",
          tags: [],
          ...media,
        },
      ],
      backendUrl: "http://test",
      tagUpdate: { key: 0, pictureIds: [] },
      descriptionUpdate: { key: 0, pictureIds: [] },
      smartScoreUpdate: { key: 0, pictureIds: [] },
    },
    global: { stubs: STUBS },
    attachTo: document.body,
  });
  await wrapper.setProps({ open: true });
  await flush();
  await flush();
  return wrapper;
}

async function readyStill(wrapper) {
  const image = wrapper.find(".overlay-img").element;
  for (const [property, value] of [
    ["complete", true],
    ["naturalWidth", 80],
    ["naturalHeight", 60],
  ]) {
    Object.defineProperty(image, property, { configurable: true, value });
  }
  await wrapper.find(".overlay-img").trigger("load");
  return image;
}

function press(key, init = {}) {
  const event = new KeyboardEvent("keydown", {
    key,
    bubbles: true,
    cancelable: true,
    ...init,
  });
  (init.target || window).dispatchEvent(event);
  return event;
}

beforeEach(async () => {
  setActivePinia(createPinia());
  apiGet.mockClear();
  clipboardWrite = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { write: clipboardWrite },
  });
  window.ClipboardItem = class ClipboardItem {
    constructor(data) {
      this.data = data;
    }
  };
  canvasDraw = vi.fn();
  HTMLCanvasElement.prototype.getContext = vi.fn(() => ({ drawImage: canvasDraw }));
  HTMLCanvasElement.prototype.toBlob = vi.fn((callback) =>
    callback(new Blob(["png pixels"], { type: "image/png" })),
  );
  anchorClick = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  URL.createObjectURL = vi.fn(() => "blob:media");
  URL.revokeObjectURL = vi.fn();
  delete window.showSaveFilePicker;
  delete window.pixlstashDesktop;
  const { isReadOnly } = await import("../../utils/apiClient");
  isReadOnly.value = false;
});

afterEach(() => {
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: originalClipboard,
  });
  window.ClipboardItem = originalClipboardItem;
  window.showSaveFilePicker = originalPicker;
  window.pixlstashDesktop = originalDesktop;
  window.getSelection = originalGetSelection;
  HTMLCanvasElement.prototype.getContext = originalCanvasContext;
  HTMLCanvasElement.prototype.toBlob = originalCanvasToBlob;
  URL.createObjectURL = originalCreateObjectUrl;
  URL.revokeObjectURL = originalRevokeObjectUrl;
  anchorClick.mockRestore();
  document.body.innerHTML = "";
});

describe("ImageOverlay local media commands", () => {
  it("Ctrl+S invokes regular Save with original bytes and filename, including read-only", async () => {
    const { isReadOnly } = await import("../../utils/apiClient");
    isReadOnly.value = true;
    await openOverlay();
    const event = press("s", { ctrlKey: true });
    await flush();

    expect(event.defaultPrevented).toBe(true);
    expect(apiGet).toHaveBeenCalledWith("/pictures/7.jpg", {
      responseType: "blob",
    });
    expect(anchorClick).toHaveBeenCalledTimes(1);
    expect(document.querySelector("a")?.download).toBe("holiday.jpg");
  });

  it("reports regular Save download failures without claiming success", async () => {
    const wrapper = await openOverlay();
    apiGet.mockRejectedValueOnce(new Error("network unavailable"));
    const result = await wrapper.vm.saveMedia({ id: 7, format: "jpg" });
    expect(result).toBe(false);
    expect(anchorClick).not.toHaveBeenCalled();
  });

  it("Ctrl+C copies still pixels as PNG and never writes URL or text", async () => {
    const wrapper = await openOverlay();
    const image = await readyStill(wrapper);
    const event = press("c", { ctrlKey: true });
    await flush();

    expect(event.defaultPrevented).toBe(true);
    expect(canvasDraw).toHaveBeenCalledWith(image, 0, 0, 80, 60);
    expect(clipboardWrite).toHaveBeenCalledTimes(1);
    const item = clipboardWrite.mock.calls[0][0][0];
    expect(Object.keys(item.data)).toEqual(["image/png"]);
    expect(await item.data["image/png"]).toBeInstanceOf(Blob);
  });

  it("Copy current frame draws the displayed video frame into PNG", async () => {
    const wrapper = await openOverlay({ format: "mp4", original_file_name: "clip.mp4" });
    const video = wrapper.find(".overlay-video").element;
    for (const [property, value] of [
      ["readyState", 2],
      ["videoWidth", 1920],
      ["videoHeight", 1080],
    ]) {
      Object.defineProperty(video, property, { configurable: true, value });
    }
    await wrapper.vm.copyMedia({ id: 7, format: "mp4" });

    expect(canvasDraw).toHaveBeenCalledWith(video, 0, 0, 1920, 1080);
    expect(clipboardWrite).toHaveBeenCalledTimes(1);
  });

  it("reports clipboard failure without substituting URL text", async () => {
    clipboardWrite.mockRejectedValue(new DOMException("denied", "NotAllowedError"));
    const wrapper = await openOverlay();
    await readyStill(wrapper);
    const result = await wrapper.vm.copyMedia({ id: 7, format: "jpg" });

    expect(result).toBe(false);
    expect(clipboardWrite).toHaveBeenCalledTimes(1);
    expect(apiGet.mock.calls.some(([url]) => /\/pictures\/7\.jpg/.test(url))).toBe(false);
  });

  it("does not own Save/Copy in text-entry or selected-text contexts", async () => {
    const wrapper = await openOverlay();
    await readyStill(wrapper);
    const input = document.createElement("input");
    document.body.appendChild(input);
    input.focus();
    const save = press("s", { ctrlKey: true, target: input });
    const copy = press("c", { ctrlKey: true, target: input });
    expect(save.defaultPrevented).toBe(false);
    expect(copy.defaultPrevented).toBe(false);

    input.blur();
    window.getSelection = () => ({ isCollapsed: false, toString: () => "caption" });
    const selectedCopy = press("c", { ctrlKey: true });
    expect(selectedCopy.defaultPrevented).toBe(false);
    expect(clipboardWrite).not.toHaveBeenCalled();
    expect(anchorClick).not.toHaveBeenCalled();
  });

  it("Save As uses the native writable picker when available and writes original bytes", async () => {
    const { useNoticeStore } = await import("../../stores/useNoticeStore");
    const noticeStore = useNoticeStore();
    const calls = [];
    const writable = {
      write: vi.fn(async (blob) => calls.push(["write", await blob.text()])),
      close: vi.fn(async () => calls.push(["close"])),
    };
    window.showSaveFilePicker = vi.fn(async () => {
      calls.push(["picker"]);
      return {
        name: "renamed-in-chrome.jpg",
        createWritable: vi.fn(async () => writable),
      };
    });
    apiGet.mockImplementationOnce(async () => ({ data: [] }));
    const wrapper = await openOverlay();
    apiGet.mockImplementation(async (url) => {
      if (/\/pictures\/7\.jpg/.test(url)) {
        calls.push(["fetch"]);
        return { data: new Blob(["original-jpeg"], { type: "image/jpeg" }) };
      }
      return { data: [] };
    });
    await wrapper.vm.saveMediaAs({
      id: 7,
      format: "jpg",
      original_file_name: "holiday.jpg",
    });

    expect(calls).toEqual([
      ["picker"],
      ["fetch"],
      ["write", "original-jpeg"],
      ["close"],
    ]);
    expect(noticeStore.notices.at(-1)?.text).toBe(
      "Saved renamed-in-chrome.jpg.",
    );
  });

  it("falls back honestly to a named download when no save picker exists", async () => {
    const wrapper = await openOverlay();
    const resultPromise = wrapper.vm.saveMediaAs({
      id: 7,
      format: "jpg",
      original_file_name: "holiday.jpeg",
    });
    await flush();
    const input = document.querySelector("#overlay-save-as-name");
    expect(input).toBeTruthy();
    expect(input.value).toBe("holiday");
    input.value = "renamed-holiday";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    const download = [...document.querySelectorAll("button")].find(
      (button) => button.textContent.trim() === "Download",
    );
    download.click();
    const result = await resultPromise;
    expect(result).toBe(true);
    expect(apiGet).toHaveBeenCalledWith("/pictures/7.jpg", {
      responseType: "blob",
    });
    expect(anchorClick).toHaveBeenCalledTimes(1);
    expect(document.querySelector("a")?.href).toBe("blob:media");
    expect(document.querySelector("a")?.download).toBe(
      "renamed-holiday.jpeg",
    );
  });

  it("silently cancels the no-picker filename dialog", async () => {
    const wrapper = await openOverlay();
    const resultPromise = wrapper.vm.saveMediaAs({ id: 7, format: "jpg" });
    await flush();
    const cancel = [...document.querySelectorAll("button")].find(
      (button) => button.textContent.trim() === "Cancel",
    );
    cancel.click();
    expect(await resultPromise).toBe(false);
    expect(anchorClick).not.toHaveBeenCalled();
    expect(apiGet.mock.calls.some(([url]) => /\/pictures\/7\.jpg/.test(url))).toBe(false);
  });

  it("treats Save As cancellation silently", async () => {
    window.showSaveFilePicker = vi.fn(async () => {
      throw new DOMException("cancelled", "AbortError");
    });
    const wrapper = await openOverlay();
    const result = await wrapper.vm.saveMediaAs({ id: 7, format: "jpg" });
    expect(result).toBe(false);
    expect(apiGet.mock.calls.some(([url]) => /\/pictures\/7\.jpg/.test(url))).toBe(false);
  });

  it("reports a Save As write failure after a file was chosen", async () => {
    window.showSaveFilePicker = vi.fn(async () => ({
      createWritable: vi.fn(async () => ({
        write: vi.fn(async () => {
          throw new Error("disk full");
        }),
        close: vi.fn(),
      })),
    }));
    const wrapper = await openOverlay();
    const result = await wrapper.vm.saveMediaAs({ id: 7, format: "jpg" });
    expect(result).toBe(false);
    expect(anchorClick).not.toHaveBeenCalled();
  });

  it("uses authenticated renderer bytes for Electron Save As and native PNG copy", async () => {
    const beginMediaSaveAs = vi.fn(async () => ({
      canceled: false,
      saveId: "save-1",
    }));
    const completeMediaSaveAs = vi.fn(async () => ({ saved: true }));
    const copyPngToClipboard = vi.fn(async () => ({ copied: true }));
    window.pixlstashDesktop = {
      beginMediaSaveAs,
      completeMediaSaveAs,
      cancelMediaSaveAs: vi.fn(),
      copyPngToClipboard,
    };
    const wrapper = await openOverlay();
    await readyStill(wrapper);
    await wrapper.vm.saveMediaAs({ id: 7, format: "jpg", original_file_name: "holiday.jpg" });
    await wrapper.vm.copyMedia({ id: 7, format: "jpg" });

    expect(apiGet).toHaveBeenCalledWith("/pictures/7.jpg", {
      responseType: "blob",
    });
    expect(beginMediaSaveAs).toHaveBeenCalledWith("holiday.jpg");
    expect(completeMediaSaveAs).toHaveBeenCalledWith(
      "save-1",
      expect.any(ArrayBuffer),
    );
    expect(copyPngToClipboard).toHaveBeenCalledWith(expect.any(ArrayBuffer));
    expect(clipboardWrite).not.toHaveBeenCalled();
  });
});
