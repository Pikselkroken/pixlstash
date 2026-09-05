// The host-path picker's FILE mode (the shelf's `Add file`, plan F6).
//
// The directory mode is exercised through the dialogs that use it; what needed
// its own suite is the mode that changes what a click MEANS. A click on a file
// selects it and never confirms - a single click that started a copy would be
// one slip of the pointer away from writing a file nobody chose - and the flag
// is opt-in on both sides, so every other picker keeps a directory-only list.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";

const browseFilesystem = vi.fn();
vi.mock("../../api/folders", () => ({
  browseFilesystem: (...args) => browseFilesystem(...args),
  createFilesystemFolder: vi.fn(),
}));

import FolderBrowser from "./FolderBrowser.vue";

const globalOpts = {
  global: {
    stubs: {
      "v-dialog": { template: "<div><slot /></div>" },
      "v-card": { template: "<div><slot /></div>" },
      "v-card-title": { template: "<div><slot /></div>" },
      "v-card-text": { template: "<div><slot /></div>" },
      "v-card-actions": { template: "<div><slot /></div>" },
      "v-spacer": true,
      "v-icon": true,
      "v-checkbox": true,
      "v-text-field": true,
      "v-progress-circular": true,
      "v-btn": {
        props: ["disabled"],
        template:
          "<button :disabled='disabled' @click=\"$emit('click')\"><slot /></button>",
      },
    },
  },
};

const LISTING = {
  path: "/home/u/Downloads",
  parent: "/home/u",
  entries: [
    { name: "sub", path: "/home/u/Downloads/sub", is_dir: true, is_file: false },
    {
      name: "loose.safetensors",
      path: "/home/u/Downloads/loose.safetensors",
      is_dir: false,
      is_file: true,
    },
  ],
};

/**
 * Mount closed, then open it - the dialog listens for the *transition*, which
 * is also how every caller uses it: the component lives in the parent's
 * template and `open` flips.
 */
async function open(props = {}) {
  const wrapper = mount(FolderBrowser, {
    ...globalOpts,
    props: { open: false, ...props },
  });
  await wrapper.setProps({ open: true });
  await new Promise((resolve) => setTimeout(resolve, 0));
  await wrapper.vm.$nextTick();
  return wrapper;
}

/** The footer's confirm button, which is the last one in the actions row. */
function selectButton(wrapper) {
  const buttons = wrapper.findAll("button");
  return buttons[buttons.length - 1];
}

beforeEach(() => {
  browseFilesystem.mockReset();
  browseFilesystem.mockResolvedValue(LISTING);
});

describe("FolderBrowser in file mode", () => {
  it("asks for files only when it is the one picking them", async () => {
    await open({ pickModelFile: true });
    expect(browseFilesystem).toHaveBeenCalledWith(null, {
      showHidden: false,
      includeModelFiles: true,
    });

    browseFilesystem.mockClear();
    await open();
    expect(browseFilesystem).toHaveBeenCalledWith(null, {
      showHidden: false,
      includeModelFiles: false,
    });
  });

  it("selects the file a click chose rather than confirming it", async () => {
    const wrapper = await open({ pickModelFile: true });
    expect(selectButton(wrapper).attributes("disabled")).toBeDefined();

    const file = wrapper.findAll(".browse-entry").at(-1);
    await file.trigger("click");

    // Chosen, not committed: nothing has been emitted yet.
    expect(wrapper.emitted("select")).toBeUndefined();
    expect(file.classes()).toContain("browse-entry--picked");
    expect(selectButton(wrapper).attributes("disabled")).toBeUndefined();
    expect(selectButton(wrapper).text()).toContain("loose.safetensors");

    await selectButton(wrapper).trigger("click");
    expect(wrapper.emitted("select")[0]).toEqual([
      "/home/u/Downloads/loose.safetensors",
    ]);
  });

  it("drops the choice when the listing moves on from it", async () => {
    // A choice belongs to the directory it was made in; keeping it would leave
    // the footer offering a file the list no longer shows.
    const wrapper = await open({ pickModelFile: true });
    await wrapper.findAll(".browse-entry").at(-1).trigger("click");

    await wrapper.findAll(".browse-entry").at(0).trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    await wrapper.vm.$nextTick();

    expect(selectButton(wrapper).attributes("disabled")).toBeDefined();
  });

  it("still selects the browsed directory when it is not picking a file", async () => {
    // Over-blocking is its own regression: the mode is additive, and every
    // folder picker in the app is this component.
    const wrapper = await open();
    await wrapper.findAll(".browse-entry").at(-1).trigger("click");
    expect(wrapper.emitted("select")).toBeUndefined();

    await selectButton(wrapper).trigger("click");
    expect(wrapper.emitted("select")[0]).toEqual(["/home/u/Downloads"]);
  });
});
