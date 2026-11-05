// The What's changed rail (#2145): the band's × and Get outta here both
// leave, a done step explains itself and then offers the choice, and the
// choice is the same preference Settings › Appearance › Mouse holds.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { ref } from "vue";

vi.mock("vuetify/components", () => ({
  VIcon: { name: "v-icon", template: "<i><slot /></i>" },
  VTooltip: { name: "v-tooltip", template: "<span><slot /></span>" },
}));
vi.mock("../../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  onSessionReset: () => () => {},
  isReadOnly: ref(false),
}));
vi.mock("../../api/config", () => ({ patchUserConfig: vi.fn(async () => ({})) }));

import WhatsChangedRail from "./WhatsChangedRail.vue";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import { useWhatsChangedStore } from "../../stores/useWhatsChangedStore";

let store;

beforeEach(() => {
  setActivePinia(createPinia());
  useSidebarStore().statsOpen = true;
  store = useWhatsChangedStore();
  store.checklist = true;
  store.card = true;
});

const mountRail = () => mount(WhatsChangedRail, { attachTo: document.body });

describe("WhatsChangedRail", () => {
  it("lists the five gestures, none done", () => {
    const w = mountRail();
    expect(w.findAll(".wc-step")).toHaveLength(5);
    expect(w.text()).toContain("0 of 5");
    expect(w.find(".wc-step--done").exists()).toBe(false);
    w.unmount();
  });

  it("the step just done explains itself and offers the old way", async () => {
    store.tick("click");
    const w = mountRail();
    await flushPromises();
    const step = w.find('[data-step="click"]');
    expect(step.classes()).toContain("wc-step--open");
    expect(step.text()).toContain("That click selected it");
    expect(w.text()).toContain("1 of 5");
    await step.findAll('[role="radio"]').find((b) => b.text() === "Single click").trigger("click");
    expect(useUserPrefsStore().gridOpenGesture).toBe("single");
    w.unmount();
  });

  it("the band's × and Get outta here both leave and keep the card", async () => {
    for (const leave of [
      (w) => w.find('[aria-label="Close What\'s changed"]').trigger("click"),
      (w) =>
        w.findAll("button").find((b) => b.text().includes("Get outta here")).trigger("click"),
    ]) {
      store.checklist = true;
      store.railTab = "changes";
      const w = mountRail();
      await leave(w);
      expect(store.checklistShown).toBe(false);
      expect(store.railTab).toBe("details");
      expect(store.card).toBe(true);
      w.unmount();
    }
  });

  it("Details in the band switches the rail back", async () => {
    const w = mountRail();
    await w.find('[data-band="details"]').trigger("click");
    expect(store.railTab).toBe("details");
    expect(store.checklistShown).toBe(true);
    w.unmount();
  });
});
