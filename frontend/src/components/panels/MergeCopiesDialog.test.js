// The keep-one-copy dialog (#1439).
//
// Three rules from the issue live here rather than on the server, so they are
// asserted here: nothing is pre-selected (the wrong default is a 20 GB
// redownload), the ComfyUI warning arrives BEFORE the press rather than in the
// receipt after it, and what the reader chose is what is sent.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const mergeModelCopies = vi.fn();
vi.mock("../../api/modelFiles", () => ({
  mergeModelCopies: (...args) => mergeModelCopies(...args),
}));

import MergeCopiesDialog from "./MergeCopiesDialog.vue";
import { useModelShelfStore } from "../../stores/useModelShelfStore";

// `AppDialog` is Vuetify's overlay, which these mounts do not install. The stub
// renders the body and the footer, which is all this file asserts on.
const AppDialogStub = {
  name: "AppDialog",
  props: ["open", "title", "subtitle"],
  template: '<div class="dialog"><slot /><slot name="footer" /></div>',
};

const AppButtonStub = {
  name: "AppButton",
  props: ["variant", "disabled", "loading", "keyHint"],
  emits: ["click"],
  template:
    '<button :disabled="disabled" :data-variant="variant" ' +
    "@click=\"$emit('click')\"><slot /></button>",
};

const globalOpts = {
  global: { stubs: { AppDialog: AppDialogStub, AppButton: AppButtonStub } },
};

const ROW = {
  id: 7,
  name: "Alice",
  locations: [
    {
      state: "present",
      folder_id: 1,
      folder_path: "/models/loras",
      relpath: "alice.safetensors",
    },
    {
      state: "present",
      folder_id: 2,
      folder_path: "/other",
      relpath: "alice-copy.safetensors",
    },
    // A registration rather than bytes: there is nothing here to choose.
    {
      state: "missing",
      folder_id: 3,
      folder_path: "/gone",
      relpath: "alice.safetensors",
    },
  ],
};

const CLEAN_PLAN = {
  merged: [],
  files_removed: 0,
  permanent: false,
  dry_run: true,
  trash_name: "Trash",
  comfyui_reads: [],
  refused: [],
};

function open(row = ROW) {
  return mount(MergeCopiesDialog, {
    props: { open: true, row },
    ...globalOpts,
  });
}

/** The confirm button - the second one, the first being Cancel. */
function confirm(wrapper) {
  return wrapper.findAll("button").at(-1);
}

beforeEach(() => {
  setActivePinia(createPinia());
  mergeModelCopies.mockReset();
  mergeModelCopies.mockResolvedValue(CLEAN_PLAN);
});

describe("the keep-one-copy dialog", () => {
  it("offers the copies that are on the disk and pre-selects none", () => {
    const wrapper = open();
    const radios = wrapper.findAll('input[type="radio"]');
    expect(radios).toHaveLength(2);
    expect(radios.some((r) => r.element.checked)).toBe(false);
    // The `missing` row is a registration, not a copy anyone can keep.
    expect(wrapper.text()).not.toContain("/gone");
    expect(confirm(wrapper).attributes("disabled")).toBeDefined();
  });

  it("asks the server what it would do before the press, not after", async () => {
    // A dry run, so the warning below is in front of the reader while they
    // decide rather than in the receipt once the bytes have gone.
    const wrapper = open();
    await wrapper.findAll('input[type="radio"]')[0].setValue();
    await new Promise((r) => setTimeout(r, 0));

    expect(mergeModelCopies).toHaveBeenCalledWith(
      [{ model_id: 7, folder_id: 1, relpath: "alice.safetensors" }],
      { dryRun: true },
    );
    expect(confirm(wrapper).attributes("disabled")).toBeUndefined();
  });

  it("says that ComfyUI reads the copy being removed", async () => {
    mergeModelCopies.mockResolvedValue({
      ...CLEAN_PLAN,
      comfyui_reads: [
        {
          model_id: 7,
          folder_id: 2,
          relpath: "alice-copy.safetensors",
          keeper_relpath: "alice.safetensors",
          keeper_advertised: true,
        },
      ],
    });
    const wrapper = open();
    await wrapper.findAll('input[type="radio"]')[0].setValue();
    await new Promise((r) => setTimeout(r, 0));
    expect(wrapper.text()).toContain("still names the file that went");
  });

  it("says the harder thing when the keeper is not one ComfyUI reads", async () => {
    // Two different situations and two different sentences: with the keeper
    // advertised PixlStash can put the run on it, and without it nothing can be
    // substituted at all.
    mergeModelCopies.mockResolvedValue({
      ...CLEAN_PLAN,
      comfyui_reads: [
        {
          model_id: 7,
          folder_id: 2,
          relpath: "alice-copy.safetensors",
          keeper_relpath: "alice.safetensors",
          keeper_advertised: false,
        },
      ],
    });
    const wrapper = open();
    await wrapper.findAll('input[type="radio"]')[0].setValue();
    await new Promise((r) => setTimeout(r, 0));
    expect(wrapper.text()).toContain("cannot put a run on it either");
  });

  it("shows the refusal the real call would make, and refuses the press", async () => {
    mergeModelCopies.mockResolvedValue({
      ...CLEAN_PLAN,
      refused: [{ id: 7, reason: "keeper_not_present" }],
    });
    const wrapper = open();
    await wrapper.findAll('input[type="radio"]')[0].setValue();
    await new Promise((r) => setTimeout(r, 0));
    expect(wrapper.text()).toContain("not on the disk any more");
  });

  it("sends the copy the reader chose and closes", async () => {
    const store = useModelShelfStore();
    const merge = vi.spyOn(store, "mergeCopies").mockResolvedValue(true);
    const wrapper = open();
    await wrapper.findAll('input[type="radio"]')[1].setValue();
    await new Promise((r) => setTimeout(r, 0));

    await confirm(wrapper).trigger("click");
    await new Promise((r) => setTimeout(r, 0));

    expect(merge).toHaveBeenCalledWith([
      { model_id: 7, folder_id: 2, relpath: "alice-copy.safetensors" },
    ]);
    expect(wrapper.emitted("close")).toBeTruthy();
  });
});
