// Where the keyboard and a screen reader land when ⓘ is pressed (review of
// PR #1414).
//
// This file mounts the REAL `VMenu`, because that is the whole question: the
// stubbed menu in `WorkflowCard.test.js` is always open and never focuses
// anything, so `role="dialog"` could be deleted with every other test still
// green. Vuetify's own focus handling moves focus to the first FOCUSABLE child
// of the content — which, since F7 put *Show all N pictures* in the panel, is
// the picture count. So the component's own focus call now has a rival rather
// than an empty field: without it, pressing ⓘ lands the reader on a link in
// the middle of the panel, and before F7 it landed them nowhere at all. Both
// are the same bug, and this test is what stops either.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createVuetify } from "vuetify";
import * as vuetifyComponents from "vuetify/components";
import * as vuetifyDirectives from "vuetify/directives";
import { nextTick } from "vue";

import InfoPopover from "./InfoPopover.vue";

const push = vi.fn();
vi.mock("vue-router", () => ({
  useRouter: () => ({ push }),
  useRoute: () => ({ name: "workflows", query: {} }),
}));

beforeEach(() => {
  setActivePinia(createPinia());
  push.mockClear();
});

const vuetify = createVuetify({
  components: vuetifyComponents,
  directives: vuetifyDirectives,
});

const CARD = {
  key: "w1",
  name: "Cinematic portrait",
  models: [{ name: "realvisXL_v5", kind: "checkpoint" }],
  loras: [{ name: "lightning-8step", mark: "structural" }],
  differs_by: ["+ face detailer"],
  picture_count: 184,
  stack_size: 6,
  saved_recipe_count: 3,
};

/** Mount the popover with a plain button as its activator, attached to body. */
function mountPopover() {
  return mount(InfoPopover, {
    props: { card: CARD },
    attachTo: document.body,
    global: { plugins: [vuetify] },
    slots: {
      activator: `<template #activator="{ props }">
        <button v-bind="props" type="button">info</button>
      </template>`,
    },
  });
}

const panel = () =>
  document.querySelector('[data-testid="workflow-info-popover"]');

describe("InfoPopover accessibility", () => {
  it("focuses the named panel when ⓘ opens it", async () => {
    const wrapper = mountPopover();
    const activator = wrapper.find("button");
    expect(activator.attributes("aria-haspopup")).toBe("dialog");
    expect(panel()).toBeNull();

    await activator.trigger("click");
    await nextTick();
    await nextTick();

    const opened = panel();
    expect(opened).toBeTruthy();
    expect(opened.getAttribute("role")).toBe("dialog");
    expect(opened.getAttribute("aria-label")).toBe("About Cinematic portrait");
    // The panel itself takes focus, so the announcement and the virtual cursor
    // arrive at the content rather than being stranded on the button.
    expect(document.activeElement).toBe(opened);

    wrapper.unmount();
  });

  it("lets Vuetify say whether it is expanded, rather than saying it twice", async () => {
    const wrapper = mountPopover();
    const activator = wrapper.find("button");
    expect(activator.attributes("aria-expanded")).toBe("false");

    await activator.trigger("click");
    await nextTick();
    expect(activator.attributes("aria-expanded")).toBe("true");

    wrapper.unmount();
  });
});
