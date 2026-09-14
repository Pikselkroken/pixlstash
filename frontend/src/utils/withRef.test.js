// withRef: a Vuetify activator's props and the app's own template ref on one
// element. Mounted, because the defect is Vue dropping one `ref` of two.

import { describe, it, expect } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { defineComponent, ref } from "vue";
import { createVuetify } from "vuetify";
import * as components from "vuetify/components";

import Tooltip from "../components/widgets/Tooltip.vue";
import { withRef } from "./withRef.js";

const vuetify = createVuetify({ components });

describe("withRef", () => {
  it("anchors the tooltip and still fills the app's ref", async () => {
    const Host = defineComponent({
      components: { Tooltip },
      setup() {
        const inputRef = ref(null);
        return { inputRef, withRef };
      },
      template: `
        <Tooltip text="Why">
          <template #activator="{ props }">
            <input v-bind="withRef(props, (el) => (inputRef = el))" />
          </template>
        </Tooltip>`,
    });
    const w = mount(Host, {
      global: { plugins: [vuetify] },
      attachTo: document.body,
    });
    await flushPromises();
    const input = w.find("input").element;
    expect(w.vm.inputRef).toBe(input);
    expect(w.findComponent(components.VTooltip).vm.activatorEl).toBe(input);
    w.unmount();
  });
});
