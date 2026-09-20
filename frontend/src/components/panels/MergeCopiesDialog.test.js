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
    expect(wrapper.text()).toContain("one you open in ComfyUI itself will not");
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
    expect(wrapper.text()).toContain("cannot see the one you are keeping");
  });

  it("shows the refusal the real call would make, and refuses the press", async () => {
    // Both halves, because the sentence on its own is not the gate: a live
    // button under "that copy is not on the disk any more" invites the reader to
    // press it and read the same words back as a failure.
    const store = useModelShelfStore();
    const merge = vi.spyOn(store, "mergeCopies").mockResolvedValue(true);
    mergeModelCopies.mockResolvedValue({
      ...CLEAN_PLAN,
      refused: [{ id: 7, reason: "keeper_not_present" }],
    });
    const wrapper = open();
    await wrapper.findAll('input[type="radio"]')[0].setValue();
    await new Promise((r) => setTimeout(r, 0));

    expect(wrapper.text()).toContain("not on your disk any more");
    expect(confirm(wrapper).attributes("disabled")).toBeDefined();
    await confirm(wrapper).trigger("click");
    await new Promise((r) => setTimeout(r, 0));
    expect(merge).not.toHaveBeenCalled();
  });

  it("refuses the press until the dry run has answered", async () => {
    // The warning has to be in front of the reader, so the button cannot be live
    // while the question is still in the air.
    let release;
    mergeModelCopies.mockReturnValue(
      new Promise((resolve) => {
        release = () => resolve(CLEAN_PLAN);
      }),
    );
    const wrapper = open();
    await wrapper.findAll('input[type="radio"]')[0].setValue();
    await new Promise((r) => setTimeout(r, 0));
    expect(confirm(wrapper).attributes("disabled")).toBeDefined();

    release();
    await new Promise((r) => setTimeout(r, 0));
    expect(confirm(wrapper).attributes("disabled")).toBeUndefined();
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

describe("the keep-one-copy dialog's checks", () => {
  it("closes the confirm when the dry run fails", async () => {
    // Failing open was the defect: with no plan there is no refusal to read, so
    // the button stayed live with the ComfyUI warning never asked - and the real
    // call reports that one only in the receipt, after the files have gone.
    mergeModelCopies.mockRejectedValue(new Error("network"));
    const wrapper = open();
    await wrapper.findAll('input[type="radio"]')[0].setValue();
    await new Promise((r) => setTimeout(r, 0));

    expect(confirm(wrapper).attributes("disabled")).toBeDefined();
    expect(wrapper.text()).toContain("could not check this");
  });

  it("matches a dry run's answer to the keeper that asked for it", async () => {
    // A radio group is arrow-keyed, so walking the copies dispatches a request
    // per keeper and overlap is the ordinary keyboard path. Whichever lands last
    // used to win, which could leave the first keeper's clean plan enabling a
    // confirm for a second keeper the server would refuse.
    const answers = [];
    mergeModelCopies.mockImplementation(
      () => new Promise((resolve) => answers.push(resolve)),
    );
    const wrapper = open();
    const radios = wrapper.findAll('input[type="radio"]');

    await radios[0].setValue();
    await new Promise((r) => setTimeout(r, 0));
    await radios[1].setValue();
    await new Promise((r) => setTimeout(r, 0));
    expect(answers).toHaveLength(2);

    // The FIRST keeper's answer lands last, and says everything is fine.
    answers[1]({
      ...CLEAN_PLAN,
      refused: [{ id: 7, reason: "not_a_duplicate" }],
    });
    answers[0](CLEAN_PLAN);
    await new Promise((r) => setTimeout(r, 0));

    // The selected keeper's own answer is the one on screen, and the stale clean
    // plan did not enable the press.
    expect(wrapper.text()).toContain("only one copy left");
    expect(confirm(wrapper).attributes("disabled")).toBeDefined();
  });

  it("forgets the previous model when it is reopened", async () => {
    // Untested, the whole reset block could be deleted and the suite stayed
    // green - leaving the last model's ComfyUI warning on screen against the
    // next one, beside a keeper the reader did not choose.
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
    const wrapper = mount(MergeCopiesDialog, {
      props: { open: true, row: ROW },
      ...globalOpts,
    });
    await wrapper.findAll('input[type="radio"]')[0].setValue();
    await new Promise((r) => setTimeout(r, 0));
    expect(wrapper.text()).toContain("cannot see the one you are keeping");

    await wrapper.setProps({ open: false });
    await wrapper.setProps({ open: true });
    await new Promise((r) => setTimeout(r, 0));

    const radios = wrapper.findAll('input[type="radio"]');
    expect(radios.some((r) => r.element.checked)).toBe(false);
    expect(wrapper.text()).not.toContain("cannot see the one you are keeping");
    expect(confirm(wrapper).attributes("disabled")).toBeDefined();
  });

  it("says on each row what will happen to it", async () => {
    // The consequence used to live only in the intro paragraph, which is what a
    // reader skips on a dialog they opened deliberately - and here the skipped
    // part is which file gets deleted.
    const wrapper = open();
    expect(wrapper.text()).not.toContain("goes to");

    await wrapper.findAll('input[type="radio"]')[0].setValue();
    await new Promise((r) => setTimeout(r, 0));
    const rows = wrapper.findAll(".mcd-copy");
    expect(rows[0].text()).toContain("kept");
    expect(rows[1].text()).toContain("goes to Trash");
  });

  it("puts focus on the first copy and gives it back on close", async () => {
    const opener = document.createElement("button");
    document.body.appendChild(opener);
    opener.focus();
    const wrapper = mount(MergeCopiesDialog, {
      props: { open: true, row: ROW },
      attachTo: document.body,
      ...globalOpts,
    });
    await new Promise((r) => setTimeout(r, 0));
    expect(document.activeElement).toBe(
      wrapper.findAll('input[type="radio"]')[0].element,
    );

    await wrapper.findAll("button")[0].trigger("click");
    await new Promise((r) => setTimeout(r, 0));
    // The context menu this was opened from is gone, so without the hand-back
    // focus lands on <body>.
    expect(document.activeElement).toBe(opener);
    wrapper.unmount();
    opener.remove();
  });
});
