import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";

vi.mock("vuetify/components", () => ({
  VIcon: { name: "v-icon", template: "<i><slot /></i>" },
  VCheckbox: {
    name: "v-checkbox",
    props: ["modelValue", "label"],
    emits: ["update:modelValue"],
    template:
      '<label><input type="checkbox" :checked="modelValue" @change="$emit(\'update:modelValue\', $event.target.checked)" />{{ label }}</label>',
  },
  VDialog: { name: "v-dialog", template: "<div><slot /></div>" },
  VTooltip: {
    template: '<slot name="activator" :props="{}" :is-active="false" />',
  },
}));

import ConfirmDialog from "./ConfirmDialog.vue";
import { unregisterConfirmHost, useConfirm } from "../../composables/useConfirm";

async function settle() {
  await nextTick();
  await nextTick();
}

beforeEach(() => unregisterConfirmHost());
afterEach(() => unregisterConfirmHost());

describe("ConfirmDialog", () => {
  it("renders the real warning and initially focuses the primary action", async () => {
    const wrapper = mount(ConfirmDialog, { attachTo: document.body });
    const pending = useConfirm().confirm({
      title: "Switch to Client work?",
      message: "PixlStash will reload.",
      warning:
        "2 share links point at Family Photos. They stop working until you switch back.",
      confirmLabel: "Switch and reload",
    });
    await settle();

    expect(wrapper.text()).toContain("2 share links point at Family Photos");
    const buttons = wrapper.findAll("button");
    expect(buttons.at(-1).text()).toContain("Switch and reload");
    expect(document.activeElement).toBe(buttons.at(-1).element);

    await wrapper.find(".confirm-dialog__message").trigger("keydown", {
      key: "Enter",
    });
    await expect(pending).resolves.toBe(true);
    wrapper.unmount();
  });

  it("Escape cancels and returns focus to the invoking control", async () => {
    const trigger = document.createElement("button");
    document.body.append(trigger);
    trigger.focus();
    const wrapper = mount(ConfirmDialog, { attachTo: document.body });
    const pending = useConfirm().confirm("Continue?");
    await settle();

    await wrapper.find(".confirm-dialog__message").trigger("keydown", {
      key: "Escape",
    });
    await expect(pending).resolves.toBe(false);
    await settle();
    expect(document.activeElement).toBe(trigger);

    wrapper.unmount();
    trigger.remove();
  });

  it("reports a ticked don't-show-again only when the user confirms", async () => {
    const wrapper = mount(ConfirmDialog, { attachTo: document.body });
    const onDontShowAgain = vi.fn();

    const cancelled = useConfirm().confirm({ message: "Retag?", onDontShowAgain });
    await settle();
    await wrapper.find('input[type="checkbox"]').setValue(true);
    await wrapper.findAll("button").at(-2).trigger("click");
    await expect(cancelled).resolves.toBe(false);
    expect(onDontShowAgain).not.toHaveBeenCalled();

    const confirmed = useConfirm().confirm({ message: "Retag?", onDontShowAgain });
    await settle();
    // A new request starts unticked.
    expect(wrapper.find('input[type="checkbox"]').element.checked).toBe(false);
    await wrapper.find('input[type="checkbox"]').setValue(true);
    await wrapper.findAll("button").at(-1).trigger("click");
    await expect(confirmed).resolves.toBe(true);
    expect(onDontShowAgain).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });

  it("shows no checkbox unless the caller asks for one", async () => {
    const wrapper = mount(ConfirmDialog, { attachTo: document.body });
    const pending = useConfirm().confirm("Continue?");
    await settle();
    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(false);
    await wrapper.findAll("button").at(-1).trigger("click");
    await pending;
    wrapper.unmount();
  });
});
