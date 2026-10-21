// The set's rename field opens on the name the card shows, selected, and
// saving that name untouched must not turn a checkpoint-derived name into a
// typed one.

import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";

import WorkflowSetRenameDialog from "./WorkflowSetRenameDialog.vue";

const globalOpts = {
  global: {
    stubs: {
      AppDialog: {
        props: ["open", "title"],
        template: "<div v-if='open'><slot /><slot name='footer' /></div>",
      },
    },
  },
};

const unnamed = {
  id: 10,
  name: null,
  members: [{ slot: "checkpoint", name: "RealVis XL" }],
};

async function open(set) {
  const wrapper = mount(WorkflowSetRenameDialog, {
    ...globalOpts,
    attachTo: document.body,
    props: { set: null },
  });
  await wrapper.setProps({ set });
  return wrapper;
}

const renameButton = (wrapper) =>
  wrapper.findAll("button").find((b) => b.text() === "Rename");

describe("WorkflowSetRenameDialog", () => {
  it("opens on the shown name, selected, so typing replaces it", async () => {
    const wrapper = await open(unnamed);
    const field = wrapper.find("input").element;
    expect(field.value).toBe("RealVis XL");
    expect([field.selectionStart, field.selectionEnd]).toEqual([0, 10]);
    wrapper.unmount();
  });

  it("keeps an unnamed set unnamed when the derived name is saved untouched", async () => {
    const wrapper = await open(unnamed);
    await renameButton(wrapper).trigger("click");
    expect(wrapper.emitted("save")).toBeUndefined();
    expect(wrapper.emitted("close")).toHaveLength(1);
    wrapper.unmount();
  });

  it("sends a typed name, and writes nothing for a named set left as it is", async () => {
    const wrapper = await open(unnamed);
    await wrapper.find("input").setValue("Portraits");
    await renameButton(wrapper).trigger("click");
    expect(wrapper.emitted("save").at(-1)).toEqual(["Portraits"]);

    await wrapper.setProps({ set: null });
    await wrapper.setProps({ set: { ...unnamed, name: "Kit" } });
    expect(wrapper.find("input").element.value).toBe("Kit");
    await renameButton(wrapper).trigger("click");
    expect(wrapper.emitted("save")).toHaveLength(1);
    wrapper.unmount();
  });
});
