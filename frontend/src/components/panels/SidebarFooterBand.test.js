// The footer band folds its create verbs into a caret on the key when the
// key's whole label and the verbs cannot share the band. jsdom has no layout,
// so the widths the measurement reads are given per test.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { h } from "vue";
import SidebarFooterBand from "./SidebarFooterBand.vue";
import AppButton from "../widgets/AppButton.vue";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";

const widths = { band: 0, key: 0, labelScroll: 0, labelClient: 0 };
const saved = {};

function fake(prop, read) {
  saved[prop] = Object.getOwnPropertyDescriptor(HTMLElement.prototype, prop);
  Object.defineProperty(HTMLElement.prototype, prop, {
    configurable: true,
    get() {
      return read(this);
    },
  });
}

beforeEach(() => {
  setActivePinia(createPinia());
  fake("clientWidth", (el) =>
    el.classList.contains("sfb")
      ? widths.band
      : el.classList.contains("app-btn__label")
        ? widths.labelClient
        : 0,
  );
  fake("offsetWidth", (el) => (el.classList.contains("app-btn") ? widths.key : 0));
  fake("scrollWidth", (el) =>
    el.classList.contains("app-btn__label") ? widths.labelScroll : 0,
  );
});

afterEach(() => {
  for (const [prop, descriptor] of Object.entries(saved)) {
    if (descriptor) Object.defineProperty(HTMLElement.prototype, prop, descriptor);
    else delete HTMLElement.prototype[prop];
  }
});

const run = vi.fn();
const VERBS = ["a", "b", "c"].map((id) => ({
  id,
  label: `New ${id}…`,
  icon: "mdi-plus",
  mark: true,
  run,
}));

function mountBand() {
  return mount(SidebarFooterBand, {
    props: { verbs: VERBS },
    slots: {
      default: () => h(AppButton, { variant: "primary" }, () => "Import"),
    },
    global: {
      stubs: {
        "v-icon": true,
        Tooltip: true,
        "v-menu": {
          template: "<div><slot name='activator' :props='{}' /><slot /></div>",
        },
      },
    },
  });
}

describe("SidebarFooterBand", () => {
  it("sets the verbs beside the key when the key's whole label fits", async () => {
    Object.assign(widths, { band: 240, key: 80, labelScroll: 50, labelClient: 50 });
    const wrapper = mountBand();
    await flushPromises();
    expect(wrapper.findAll(".sfb-verbs [data-verb]")).toHaveLength(3);
    expect(wrapper.find(".sfb-caret").exists()).toBe(false);
  });

  it("folds them into the caret when the clipped label would not fit", async () => {
    // Drawn at 120 with 40 of its label clipped: 160 + 3 × 32 > 240.
    Object.assign(widths, { band: 240, key: 120, labelScroll: 90, labelClient: 50 });
    const wrapper = mountBand();
    await flushPromises();
    expect(wrapper.find(".sfb-verbs").exists()).toBe(false);
    expect(wrapper.find(".sfb-caret").exists()).toBe(true);
    run.mockClear();
    await wrapper.find('.ctx-item[data-verb="b"]').trigger("click");
    expect(run).toHaveBeenCalledTimes(1);
  });

  it("is the key alone on the narrow sidebar", async () => {
    useUserPrefsStore().sidebarWidth = 140;
    Object.assign(widths, { band: 140, key: 80, labelScroll: 50, labelClient: 50 });
    const wrapper = mountBand();
    await flushPromises();
    expect(wrapper.find(".sfb-verbs").exists()).toBe(false);
    expect(wrapper.find(".sfb-caret").exists()).toBe(false);
  });
});
