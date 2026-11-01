// The Run anyway confirm: what the bar shows, the copy when a request is over
// the card, and that the dialog only asks — it never starts the run itself.

import { afterEach, describe, expect, it } from "vitest";
import { enableAutoUnmount, mount } from "@vue/test-utils";

import RunAnywayDialog from "./RunAnywayDialog.vue";

enableAutoUnmount(afterEach);

// The app's dialog frame, drawn inline so the body and footer are in the tree.
const AppDialogFrame = {
  props: ["open", "title", "size"],
  template: "<div><h2>{{ title }}</h2><slot /><slot name=\"footer\" /></div>",
};
const AppButtonStub = {
  template: "<button type=\"button\"><slot /></button>",
};

const DEVICE = {
  uuid: "GPU-1",
  name: "RTX 4090",
  total_mb: 24576,
  free_mb: 10240,
  budget_mb: 8192,
};
const HOLDERS = [
  { device: "GPU-1", tenant: "aitoolkit", used_mb: 12902, label: "Training" },
  { device: "GPU-1", tenant: "pixlstash", used_mb: 1229, label: "PixlStash" },
];

function mountDialog(props) {
  return mount(RunAnywayDialog, {
    props: { open: true, label: "ComfyUI 'Klein inpaint'", device: DEVICE, holders: HOLDERS, ...props },
    global: { stubs: { AppDialog: AppDialogFrame, AppButton: AppButtonStub } },
  });
}

describe("the Run anyway dialog", () => {
  it("names the run in its title and asks for what it needs", () => {
    const wrapper = mountDialog({ needMb: 14950 });
    expect(wrapper.find("h2").text()).toBe("Run ComfyUI 'Klein inpaint' anyway?");
    expect(wrapper.find('[data-testid="ra-request"]').text()).toBe("We're asking for 14.6 GB");
  });

  it("says the request is over the card when it is more than the free memory, and shows how much", () => {
    // 10 GB free; a 14.6 GB request is 4.6 GB over the free memory.
    const wrapper = mountDialog({ needMb: 14950 });
    expect(wrapper.find('[data-testid="ra-over"]').text()).toContain("4.6 GB over");
    expect(wrapper.find('[data-testid="ra-text"]').text()).toContain("more memory than this GPU has");
    // It names the spare margin PixlStash keeps, so the reader can check the sum.
    expect(wrapper.find('[data-testid="ra-text"]').text()).toContain("held back from new requests");
  });

  it("draws no red part when the request fits the free memory", () => {
    const wrapper = mountDialog({ needMb: 4096 });
    expect(wrapper.find('[data-testid="ra-over"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="ra-text"]').text()).not.toContain("more memory than");
  });

  it("puts Keep waiting first, so it is the default and what the keyboard reaches first", () => {
    const wrapper = mountDialog({ needMb: 14950 });
    const buttons = wrapper.findAll("button").map((b) => b.text());
    expect(buttons.indexOf("Keep waiting")).toBeLessThan(buttons.indexOf("Run anyway"));
  });

  it("asks through confirm, and never starts the run itself", async () => {
    const wrapper = mountDialog({ needMb: 14950 });
    await wrapper.find('[data-testid="ra-run-anyway"]').trigger("click");
    expect(wrapper.emitted("confirm")).toHaveLength(1);
    await wrapper.find('[data-testid="ra-keep-waiting"]').trigger("click");
    expect(wrapper.emitted("close")).toHaveLength(1);
    expect(wrapper.emitted("confirm")).toHaveLength(1);
  });
});
