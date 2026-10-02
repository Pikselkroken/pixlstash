import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";
import { createVuetify } from "vuetify";
import * as components from "vuetify/components";

import StackLayer from "./StackLayer.vue";

const vuetify = createVuetify({ components });

const mounted = [];
afterEach(() => {
  while (mounted.length) mounted.pop().unmount();
  document.body.innerHTML = "";
});

/** A dialog already open, with a StackLayer opened after it. */
async function dialogThenLayer() {
  const wrapper = mount(
    {
      components: { StackLayer },
      data: () => ({ layer: false }),
      template: `
        <v-dialog :model-value="true" content-class="the-dialog">
          <div>dialog body</div>
        </v-dialog>
        <StackLayer :open="layer">
          <div v-if="layer" class="the-layer">layer</div>
        </StackLayer>`,
    },
    {
      attachTo: document.body,
      // The suite-wide stub (testing/setup.js) is what this file tests against.
      global: { plugins: [vuetify], stubs: { StackLayer: false } },
    },
  );
  mounted.push(wrapper);
  await nextTick();
  wrapper.vm.layer = true;
  await nextTick();
  await nextTick();
  return wrapper;
}

const zOf = (el) => Number(el.closest(".v-overlay").style.zIndex);

describe("StackLayer", () => {
  it("lands on the overlay stack above a dialog that is already open", async () => {
    await dialogThenLayer();
    const dialog = document.querySelector(".the-dialog");
    const layer = document.querySelector(".the-layer");

    expect(layer.closest(".v-overlay-container")).not.toBeNull();
    expect(zOf(layer)).toBeGreaterThan(zOf(dialog));
  });

  it("lifts Vuetify's layout containment, so a fixed child uses the viewport", async () => {
    await dialogThenLayer();
    const content = document.querySelector(".the-layer").parentElement;

    expect(content.classList.contains("v-overlay__content")).toBe(true);
    expect(content.classList.contains("stack-layer")).toBe(true);
    // jsdom applies no SFC styles, so the declaration is read from the source.
    const source = readFileSync(
      `${process.cwd()}/src/components/widgets/StackLayer.vue`,
      "utf8",
    );
    const start = source.indexOf(".v-overlay__content.stack-layer {");
    expect(start).toBeGreaterThan(-1);
    expect(source.slice(start, source.indexOf("}", start))).toMatch(
      /\bcontain:\s*none\s*;/,
    );
  });

  // Its owner handles its own keys (a completion list's first Escape closes
  // the list); the layer itself must not close, or take the list away from
  // under the owner's handler.
  it("is not closed by Escape", async () => {
    const wrapper = await dialogThenLayer();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await new Promise((resolve) => setTimeout(resolve));
    await nextTick();

    expect(wrapper.vm.layer).toBe(true);
    expect(document.querySelector(".the-layer")).not.toBeNull();
    expect(
      document.querySelector(".the-layer").closest(".v-overlay--active"),
    ).not.toBeNull();
  });

  it("puts nothing on the stack while closed", async () => {
    const wrapper = mount(StackLayer, {
      props: { open: false },
      slots: { default: '<div class="the-layer">layer</div>' },
      attachTo: document.body,
      global: { plugins: [vuetify] },
    });
    mounted.push(wrapper);
    await nextTick();

    expect(document.querySelector(".v-overlay--active")).toBeNull();
  });
});
