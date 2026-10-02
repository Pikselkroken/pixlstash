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
    { attachTo: document.body, global: { plugins: [vuetify] } },
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
