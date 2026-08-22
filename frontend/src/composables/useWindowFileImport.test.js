// useWindowFileImport — what the window-wide catch-all accepts.
//
// The grid's own drop target filters by extension; this one did not, so a model
// file dropped on the shelf was handed to the picture importer, which uploaded
// the whole file before the backend skipped it as unsupported and the commit
// failed with "No staged files to import". Both directions are pinned here: an
// unsupported drop imports nothing and says so, and a supported one still
// imports — over-filtering would be its own regression.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { ref } from "vue";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import { useWindowFileImport } from "./useWindowFileImport";
import { useNoticeStore } from "../stores/useNoticeStore";

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
  const files = names.map((name) => new File(["x"], name));
  const event = new Event("drop", { bubbles: true, cancelable: true });
  event.dataTransfer = { files, items: [], types: ["Files"] };
  window.dispatchEvent(event);
  // The handler awaits the DataTransfer walk before importing.
  return new Promise((resolve) => setTimeout(resolve, 0));
}

beforeEach(() => {
  setActivePinia(createPinia());
  notices = useNoticeStore();
  startLocalImport.mockClear();
  wrapper = mount(Host);
});

afterEach(() => {
  wrapper.unmount();
});

describe("useWindowFileImport", () => {
  it("does not import a model file and warns instead", async () => {
    await drop("qwen_image_vae.safetensors");
    expect(startLocalImport).not.toHaveBeenCalled();
    expect(notices.notices.length).toBe(1);
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

  it("imports the pictures out of a mixed drop", async () => {
    await drop("lora.safetensors", "holiday.jpg");
    expect(startLocalImport.mock.calls[0][0].map((f) => f.name)).toEqual([
      "holiday.jpg",
    ]);
  });
});
