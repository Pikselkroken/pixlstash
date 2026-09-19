// Tooltip - the one tooltip surface (docs/design/buttons.md, "Tooltips").
//
// Mounted on the real Vuetify tooltip, because the defects this guards live in
// Vuetify's behaviour: it binds `aria-describedby` onto the activator over the
// control's own, it listens for `focus` (which does not bubble), and it knows
// nothing of a tip nested inside another.

import { describe, it, expect } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createVuetify } from "vuetify";
import * as components from "vuetify/components";

import Tooltip from "./Tooltip.vue";
import { describedAs } from "../../testing/describedAs.js";

const vuetify = createVuetify({ components });

function mountHost(template, data = {}) {
  return mount(
    { components: { Tooltip }, data: () => data, template },
    { global: { plugins: [vuetify] }, attachTo: document.body },
  );
}

const ids = (el) =>
  (el.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean);

async function settle() {
  await flushPromises();
  await new Promise((r) => setTimeout(r, 0));
  await flushPromises();
}

describe("Tooltip", () => {
  it("keeps a parent control's own describedby and adds its description", async () => {
    const w = mountHost(
      `<div><div id="reason">Blocked because</div>
       <button aria-describedby="reason">Scan<Tooltip text="Scan now" activator="parent" /></button></div>`,
    );
    await settle();
    const btn = w.find("button").element;
    // Before and after the hover that builds the Vuetify tooltip.
    expect(ids(btn)[0]).toBe("reason");
    expect(describedAs(btn)).toBe("Blocked because Scan now");
    await w.find("button").trigger("mouseenter");
    await settle();
    expect(ids(btn)[0]).toBe("reason");
    expect(describedAs(btn)).toBe("Blocked because Scan now");
    // Not the ARIA 1.3 attribute, which only Chromium exposes.
    expect(btn.hasAttribute("aria-description")).toBe(false);
    w.unmount();
  });

  it("adds no description when its text is the control's name", async () => {
    const w = mountHost(
      `<button aria-label="Close"><Tooltip text="Close" activator="parent" :describe="false" /></button>`,
    );
    await flushPromises();
    const btn = w.find("button");
    await btn.trigger("mouseenter");
    await settle();
    expect(btn.attributes("aria-describedby")).toBeUndefined();
    w.unmount();
  });

  it("describes a slot activator without dropping its own describedby", async () => {
    const w = mountHost(`
      <div><span id="hint">Hint</span>
      <Tooltip text="Why">
        <template #activator="{ props }">
          <input aria-describedby="hint" v-bind="props" />
        </template>
      </Tooltip></div>`);
    await settle();
    const input = w.find("input").element;
    expect(ids(input)[0]).toBe("hint");
    expect(describedAs(input)).toBe("Hint Why");
    w.unmount();
  });

  it("puts the description back when the control's own value re-renders", async () => {
    const w = mountHost(
      `<div><span id="a">A</span><span id="b">B</span>
       <button :aria-describedby="own">Go<Tooltip text="Tip" activator="parent" /></button></div>`,
      { own: "a" },
    );
    await settle();
    const btn = w.find("button").element;
    expect(describedAs(btn)).toBe("A Tip");
    w.vm.own = "b";
    await settle();
    expect(describedAs(btn)).toBe("B Tip");
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
    await settle();
    expect(w.findComponent(components.VTooltip).exists()).toBe(true);
    expect(document.body.textContent).toContain("Scan now");
    w.unmount();
  });

  // The parent is often a wrapper round the control that takes focus, and
  // `focus` does not bubble: tabbing in has to reach the tip anyway.
  it("arms and opens when focus lands inside a wrapper parent", async () => {
    const w = mountHost(
      `<div class="row"><Tooltip text="Row tip" activator="parent" /><button>Inside</button></div>`,
    );
    await flushPromises();
    const btn = w.find("button").element;
    // jsdom has no focus-visible heuristics; a keyboard focus is what is meant.
    btn.matches = (sel) =>
      sel === ":focus-visible" || Element.prototype.matches.call(btn, sel);
    btn.focus();
    await settle();
    const tip = w.findComponent(components.VTooltip);
    expect(tip.exists()).toBe(true);
    expect(tip.props("modelValue")).toBe(true);
    btn.blur();
    await flushPromises();
    expect(tip.props("modelValue")).toBe(false);
    w.unmount();
  });

  // A native title shows only the innermost element's; a row and the button
  // inside it must not both hold a tip open.
  it("keeps only the innermost of two nested tips open", async () => {
    const w = mountHost(
      `<div class="card"><Tooltip text="Card" activator="parent" /><button class="del">x<Tooltip text="Remove" activator="parent" /></button></div>`,
    );
    await flushPromises();
    const tips = () => w.findAllComponents(components.VTooltip);
    await w.find(".card").trigger("mouseenter");
    await settle();
    expect(tips()).toHaveLength(1);
    expect(tips()[0].props("modelValue")).toBe(true);
    await w.find(".del").trigger("mouseenter");
    await settle();
    const [card, del] = tips();
    expect(del.props("modelValue")).toBe(true);
    expect(card.props("modelValue")).toBe(false);
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
    await settle();
    w.vm.tip = false;
    await settle();
    expect(btn.attributes("aria-describedby")).toBe("reason");
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
      await settle();
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

  // A right-click on a row opens a menu under a pointer that is still on the
  // row; the hover delay already running must not put the tip back over the
  // menu, where it would hide the row the pointer wants.
  it("stays shut after a press until the pointer leaves the control", async () => {
    const w = mountHost(
      `<button>Row<Tooltip text="Row tip" activator="parent" /></button>`,
    );
    await flushPromises();
    const btn = w.find("button");
    const tip = () => w.findComponent(components.VTooltip);
    await btn.trigger("mouseenter");
    await settle();
    expect(tip().props("modelValue")).toBe(true);

    await btn.trigger("mousedown", { button: 2 });
    await flushPromises();
    expect(tip().props("modelValue")).toBe(false);
    // Vuetify's hover timer firing again with the pointer still on the row.
    await btn.trigger("mouseenter");
    await settle();
    expect(tip().props("modelValue")).toBe(false);

    await btn.trigger("mouseleave");
    await btn.trigger("mouseenter");
    await settle();
    expect(tip().props("modelValue")).toBe(true);
    w.unmount();
  });
});
