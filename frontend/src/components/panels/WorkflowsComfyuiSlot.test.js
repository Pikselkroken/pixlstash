// The Workflows sidebar's header slot (#2166): the one ComfyUI connection, or
// an amber Set up ComfyUI with none saved. What can silently go wrong is the
// status: an instance that does not answer must read Offline in the slot and
// in the menu, never Connected.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const probeComfyui = vi.fn();
vi.mock("../../api/comfyui", () => ({
  probeComfyui: (...args) => probeComfyui(...args),
}));

import WorkflowsComfyuiSlot from "./WorkflowsComfyuiSlot.vue";
import { useFilterStore } from "../../stores/useFilterStore";

const LOCAL = "http://127.0.0.1:8188/";

async function mountSlot({ url = LOCAL } = {}) {
  const pinia = createPinia();
  setActivePinia(pinia);
  const filterStore = useFilterStore();
  filterStore.comfyuiConfigured = Boolean(url);
  filterStore.comfyuiUrl = url;
  const wrapper = mount(WorkflowsComfyuiSlot, {
    global: {
      plugins: [pinia],
      stubs: {
        "v-icon": true,
        "v-menu": {
          template: "<div><slot name='activator' :props='{}' /><slot /></div>",
        },
      },
    },
  });
  await flushPromises();
  return { wrapper, filterStore };
}

const slot = (wrapper) => wrapper.find('[data-testid="workflows-comfyui-slot"]');
const menu = (wrapper) => wrapper.find('[data-testid="workflows-comfyui-menu"]');
const item = (wrapper, label) =>
  wrapper.findAll('[role="menuitem"]').find((b) => b.text() === label);

enableAutoUnmount(afterEach);

beforeEach(() => {
  probeComfyui.mockReset();
  probeComfyui.mockImplementation(async (url) => ({
    reachable: true,
    url,
    gpu: "NVIDIA GeForce RTX 4090",
    vram_mb: 24_564,
  }));
});

describe("with no ComfyUI saved", () => {
  it("is an amber Set up ComfyUI that opens the ComfyUI settings", async () => {
    const { wrapper } = await mountSlot({ url: "" });
    const setup = wrapper.find('[data-testid="workflows-comfyui-setup"]');
    expect(setup.text()).toContain("Set up ComfyUI");
    expect(slot(wrapper).exists()).toBe(false);
    await setup.trigger("click");
    expect(wrapper.emitted("open-settings")).toEqual([["comfyui"]]);
    expect(probeComfyui).not.toHaveBeenCalled();
  });
});

describe("with one ComfyUI saved", () => {
  it("names this PC, connected, with its card", async () => {
    const { wrapper } = await mountSlot();
    expect(probeComfyui).toHaveBeenCalledWith(LOCAL);
    expect(slot(wrapper).text()).toContain("This PC");
    expect(slot(wrapper).text()).toContain("Connected · RTX 4090 · 24 GB");
    expect(slot(wrapper).attributes("aria-label")).toBe("ComfyUI: This PC, Connected");
    expect(menu(wrapper).text()).toContain("Run on");
    expect(menu(wrapper).text()).toContain("127.0.0.1:8188 · RTX 4090 · 24 GB");
  });

  it("reads Offline in the slot and the menu when it does not answer", async () => {
    probeComfyui.mockImplementation(async (url) => ({ reachable: false, url, detail: "down" }));
    const { wrapper } = await mountSlot({ url: "http://192.0.2.20:8188/" });
    expect(slot(wrapper).text()).toContain("192.0.2.20");
    expect(slot(wrapper).text()).toContain("Offline");
    expect(slot(wrapper).text()).not.toContain("Connected");
    expect(slot(wrapper).find(".wcs-dot").attributes("data-state")).toBe("offline");
    expect(menu(wrapper).find(".wcs-row-status").text()).toBe("Offline");
  });

  it("says it could not check rather than guess when the probe fails", async () => {
    probeComfyui.mockRejectedValue(new Error("network"));
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const { wrapper } = await mountSlot();
    expect(slot(wrapper).text()).toContain("Not checked");
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it("checks again when the saved address changes, and drops the old answer", async () => {
    const { wrapper, filterStore } = await mountSlot();
    probeComfyui.mockImplementation(async (url) => ({ reachable: false, url }));
    filterStore.comfyuiUrl = "http://192.0.2.20:8188/";
    await flushPromises();
    expect(probeComfyui).toHaveBeenLastCalledWith("http://192.0.2.20:8188/");
    expect(slot(wrapper).text()).toContain("Offline");
    expect(slot(wrapper).text()).not.toContain("RTX 4090");
  });

  it("opens the ComfyUI settings from both New connection and Settings", async () => {
    const { wrapper } = await mountSlot();
    await item(wrapper, "New ComfyUI connection…").trigger("click");
    await item(wrapper, "ComfyUI settings…").trigger("click");
    expect(wrapper.emitted("open-settings")).toEqual([["comfyui"], ["comfyui"]]);
  });
});
