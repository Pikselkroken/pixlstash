import { describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";

vi.mock("vuetify/components", () => ({
  VDialog: { name: "v-dialog", template: "<div><slot /></div>" },
  VIcon: { name: "v-icon", template: "<i><slot /></i>" },
}));

import OverlaySaveAsDialog from "./OverlaySaveAsDialog.vue";

const AppDialogStub = {
  props: ["open", "title", "subtitle", "width"],
  template:
    '<div v-if="open"><slot /><div><slot name="footer" /></div></div>',
};

const AppButtonStub = {
  emits: ["click"],
  template: '<button @click="$emit(\'click\')"><slot /></button>',
};

function mountDialog() {
  return mount(OverlaySaveAsDialog, {
    props: {
      open: true,
      suggestedName: "holiday.JPG",
      originalExtension: "jpg",
      mediaNoun: "picture",
    },
    global: {
      stubs: { AppDialog: AppDialogStub, AppButton: AppButtonStub },
    },
  });
}

describe("OverlaySaveAsDialog", () => {
  it("prefills the filename stem on its initial open render", () => {
    const wrapper = mountDialog();
    expect(wrapper.get("#overlay-save-as-name").element.value).toBe("holiday");
    expect(wrapper.get(".save-as-extension").text()).toBe(".jpg");
  });

  it("keeps the original extension when the name changes", async () => {
    const wrapper = mountDialog();
    await wrapper.get("#overlay-save-as-name").setValue("renamed.png");
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "Download")
      .trigger("click");
    expect(wrapper.emitted("save")).toEqual([["renamed.png.jpg"]]);
  });
});
