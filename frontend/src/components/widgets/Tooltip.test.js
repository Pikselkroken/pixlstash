// Tooltip - the one tooltip surface (docs/design/buttons.md, "Tooltips").
//
// Mounted on the real Vuetify tooltip, because the defect this guards lives in
// Vuetify: it binds `aria-describedby` onto the activator, over the control's
// own. A blocked button that points at its visible reason lost that pointer
// the moment it gained a tip.

import { describe, it, expect } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createVuetify } from "vuetify";
import * as components from "vuetify/components";

import Tooltip from "./Tooltip.vue";

const vuetify = createVuetify({ components });

function mountHost(template, data = {}) {
  return mount(
    { components: { Tooltip }, data: () => data, template },
    { global: { plugins: [vuetify] }, attachTo: document.body },
  );
}

describe("Tooltip", () => {
  it("keeps a parent control's own aria-describedby", async () => {
    const w = mountHost(
      `<button aria-describedby="reason">Scan<Tooltip text="Scan now" activator="parent" /></button>`,
    );
    await flushPromises();
    const btn = w.find("button");
    // Before and after the hover that builds the Vuetify tooltip.
    expect(btn.attributes("aria-describedby")).toBe("reason");
    await btn.trigger("mouseenter");
    await flushPromises();
    expect(btn.attributes("aria-describedby")).toBe("reason");
    expect(btn.attributes("aria-description")).toBe("Scan now");
    w.unmount();
  });

  it("adds no describedby of its own, and no description when it names", async () => {
    const w = mountHost(
      `<button aria-label="Close"><Tooltip text="Close" activator="parent" :describe="false" /></button>`,
    );
    await flushPromises();
    const btn = w.find("button");
    await btn.trigger("mouseenter");
    await flushPromises();
    expect(btn.attributes("aria-describedby")).toBeUndefined();
    expect(btn.attributes("aria-description")).toBeUndefined();
    w.unmount();
  });

  it("leaves a slot activator's own aria-describedby alone", async () => {
    const w = mountHost(`
      <Tooltip text="Why">
        <template #activator="{ props }">
          <input aria-describedby="hint" v-bind="props" />
        </template>
      </Tooltip>`);
    await flushPromises();
    const input = w.find("input");
    expect(input.attributes("aria-describedby")).toBe("hint");
    expect(input.attributes("aria-description")).toBe("Why");
    w.unmount();
  });

  // A grid tile carries several tips; building a v-tooltip for each up front
  // made every tile pay for tips nobody opened.
  it("builds nothing until the control is hovered, then opens on that hover", async () => {
    const w = mountHost(
      `<button>Scan<Tooltip text="Scan now" activator="parent" /></button>`,
    );
    await flushPromises();
    expect(w.findComponent(components.VTooltip).exists()).toBe(false);
    await w.find("button").trigger("mouseenter");
    await flushPromises();
    await new Promise((r) => setTimeout(r, 0));
    await flushPromises();
    expect(w.findComponent(components.VTooltip).exists()).toBe(true);
    expect(document.body.textContent).toContain("Scan now");
    w.unmount();
  });

  it("gives the control back its own describedby when the tip goes", async () => {
    const w = mountHost(
      `<button aria-describedby="reason">Delete<Tooltip v-if="tip" text="Deletes the source" activator="parent" /></button>`,
      { tip: true },
    );
    await flushPromises();
    const btn = w.find("button");
    await btn.trigger("mouseenter");
    await flushPromises();
    w.vm.tip = false;
    await flushPromises();
    expect(btn.attributes("aria-describedby")).toBe("reason");
    expect(btn.attributes("aria-description")).toBeUndefined();
    w.unmount();
  });

  it("closes on Escape wherever focus is, and when its control is pressed", async () => {
    const w = mountHost(
      `<button>Scan<Tooltip text="Scan now" activator="parent" /></button>`,
    );
    await flushPromises();
    const btn = w.find("button");
    const tip = () => w.findComponent(components.VTooltip);
    const opened = async () => {
      await btn.trigger("mouseenter");
      await new Promise((r) => setTimeout(r, 0));
      await flushPromises();
    };

    await opened();
    expect(tip().props("modelValue")).toBe(true);
    document.body.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );
    await flushPromises();
    expect(tip().props("modelValue")).toBe(false);

    await btn.trigger("mouseleave");
    await opened();
    expect(tip().props("modelValue")).toBe(true);
    await btn.trigger("mousedown");
    await flushPromises();
    expect(tip().props("modelValue")).toBe(false);
    w.unmount();
  });
});
