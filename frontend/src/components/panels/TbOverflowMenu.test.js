// The toolbar ⋯ overflow's focus contract: focus goes into the panel on open,
// onto a row that is actually drawn, and comes back to the trigger however
// the panel closes.

import { afterEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  return vuetifyComponentStubs();
});

import TbOverflowMenu from "./TbOverflowMenu.vue";

let wrapper;
afterEach(() => wrapper?.unmount());

/**
 * Mounted with three rows. `hiddenChecked` hides the checked row the way a
 * host's container query hides a row whose bar button is still on the bar.
 */
function mountMenu({ hiddenChecked = false } = {}) {
  wrapper = mount(TbOverflowMenu, {
    attachTo: document.body,
    slots: {
      default: `
        <button type="button" class="ctx-item" data-row="a">A</button>
        <button type="button" class="ctx-item" data-row="b" aria-checked="true"
          ${hiddenChecked ? 'style="display: none"' : ""}>B</button>
        <button type="button" class="ctx-item" data-row="c">C</button>`,
    },
  });
  return wrapper;
}

const trigger = () => wrapper.find(".tbo-trigger");
async function toggle() {
  await trigger().trigger("click");
  await nextTick();
  await nextTick();
}

describe("TbOverflowMenu focus", () => {
  it("opens onto the checked row", async () => {
    mountMenu();
    await toggle();
    expect(document.activeElement.dataset.row).toBe("b");
  });

  it("skips a checked row that is not drawn, for the first drawn row", async () => {
    mountMenu({ hiddenChecked: true });
    await toggle();
    expect(document.activeElement.dataset.row).toBe("a");
  });

  it("closing from the trigger hands focus back to the trigger", async () => {
    mountMenu();
    await toggle();
    expect(document.activeElement.dataset.row).toBe("b");
    await toggle();
    expect(wrapper.find(".tbo-panel").exists()).toBe(false);
    expect(document.activeElement).toBe(trigger().element);
  });
});
