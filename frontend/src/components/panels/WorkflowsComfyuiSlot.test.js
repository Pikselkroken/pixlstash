// The Workflows sidebar's header slot (#2166): the one ComfyUI connection, or
// an amber Set up ComfyUI with none saved. What can silently go wrong is the
// status: an instance that does not answer must read Offline in the slot and
// in the menu, never Connected.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const probeComfyui = vi.fn();
const listComfyuiConnections = vi.fn();
const addComfyuiConnection = vi.fn();
vi.mock("../../api/comfyui", () => ({
  probeComfyui: (...args) => probeComfyui(...args),
  listComfyuiConnections: (...args) => listComfyuiConnections(...args),
  addComfyuiConnection: (...args) => addComfyuiConnection(...args),
}));
const patchUserConfig = vi.fn();
vi.mock("../../api/config", () => ({
  patchUserConfig: (...args) => patchUserConfig(...args),
}));

import WorkflowsComfyuiSlot from "./WorkflowsComfyuiSlot.vue";
import { useFilterStore } from "../../stores/useFilterStore";

const LOCAL = "http://127.0.0.1:8188/";

async function mountSlot({ url = LOCAL, configRead = true } = {}) {
  const pinia = createPinia();
  setActivePinia(pinia);
  const filterStore = useFilterStore();
  filterStore.comfyuiConfigRead = configRead;
  filterStore.comfyuiConfigured = Boolean(url);
  filterStore.comfyuiUrl = url;
  const wrapper = mount(WorkflowsComfyuiSlot, {
    global: {
      plugins: [pinia],
      stubs: {
        "v-icon": true,
        "v-menu": {
          props: ["modelValue"],
          emits: ["update:modelValue"],
          template:
            "<div><slot name='activator' :props='{ onClick: () => $emit(\"update:modelValue\", true) }' /><slot /></div>",
        },
        AppDialog: {
          props: ["open", "title"],
          template: "<div v-if='open' data-testid='dialog'><slot /><slot name='footer' /></div>",
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
  listComfyuiConnections.mockReset();
  listComfyuiConnections.mockResolvedValue({ connections: [], current: null });
  addComfyuiConnection.mockReset();
  patchUserConfig.mockReset();
  probeComfyui.mockReset();
  probeComfyui.mockImplementation(async (url) => ({
    reachable: true,
    url,
    gpu: "NVIDIA GeForce RTX 4090",
    vram_mb: 24_564,
  }));
});

describe("before the config is read", () => {
  it("offers neither Set up nor a connection", async () => {
    const { wrapper } = await mountSlot({ url: "", configRead: false });
    expect(wrapper.find('[data-testid="workflows-comfyui-setup"]').exists()).toBe(false);
    expect(slot(wrapper).exists()).toBe(false);
    expect(wrapper.text()).toBe("");
  });
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

  it("checks again when the saved address changes, and drops the old answer at once", async () => {
    const { wrapper, filterStore } = await mountSlot();
    expect(slot(wrapper).text()).toContain("RTX 4090");
    let answer;
    probeComfyui.mockImplementation(() => new Promise((resolve) => (answer = resolve)));
    filterStore.comfyuiUrl = "http://192.0.2.20:8188/";
    await flushPromises();
    // The new address is asked about; the old one's card and status are gone
    // while the answer is out.
    expect(probeComfyui).toHaveBeenLastCalledWith("http://192.0.2.20:8188/");
    expect(slot(wrapper).text()).toContain("Checking…");
    expect(slot(wrapper).text()).not.toContain("RTX 4090");
    answer({ reachable: false, url: "http://192.0.2.20:8188/" });
    await flushPromises();
    expect(slot(wrapper).text()).toContain("Offline");
  });

  it("opens the ComfyUI settings from Settings", async () => {
    const { wrapper } = await mountSlot();
    await item(wrapper, "ComfyUI settings…").trigger("click");
    expect(wrapper.emitted("open-settings")).toEqual([["comfyui"]]);
  });
});

describe("with several ComfyUI saved (#2167)", () => {
  const BOX = "http://192.0.2.20:8188/";
  const listed = {
    connections: [
      { id: "saved", name: "This PC", url: LOCAL, status: "connected", gpu: null },
      { id: "b1", name: "Render box", url: BOX, status: "offline", gpu: null },
    ],
    current: "saved",
  };
  const rows = (wrapper) => wrapper.findAll('[role="menuitemradio"]');
  const menuOpen = async (wrapper) => {
    await slot(wrapper).trigger("click");
    await flushPromises();
  };

  beforeEach(() => {
    listComfyuiConnections.mockResolvedValue(listed);
  });

  it("lists every connection with its status, the current one checked", async () => {
    probeComfyui.mockImplementation(async (url) => ({ reachable: url === LOCAL, url }));
    const { wrapper } = await mountSlot();
    await menuOpen(wrapper);
    expect(probeComfyui).toHaveBeenCalledWith(BOX);
    expect(rows(wrapper).map((r) => r.attributes("aria-checked"))).toEqual([
      "true",
      "false",
    ]);
    expect(rows(wrapper)[1].text()).toContain("Render box");
    expect(rows(wrapper)[1].text()).toContain("192.0.2.20:8188");
    expect(rows(wrapper)[1].find(".wcs-row-status").text()).toBe("Offline");
  });

  it("shows the last status while a connection is being asked again", async () => {
    const { wrapper } = await mountSlot();
    probeComfyui.mockImplementation(() => new Promise(() => {}));
    await menuOpen(wrapper);
    expect(rows(wrapper)[1].find(".wcs-row-status").text()).toBe("Offline");
  });

  it("switches by saving the picked address, and the slot follows", async () => {
    patchUserConfig.mockResolvedValue({ config: { comfyui_url: BOX } });
    const { wrapper, filterStore } = await mountSlot();
    await rows(wrapper)[1].trigger("click");
    await flushPromises();
    expect(patchUserConfig).toHaveBeenCalledWith({ comfyui_url: BOX });
    expect(filterStore.comfyuiUrl).toBe(BOX);
    expect(slot(wrapper).text()).toContain("Render box");
  });

  it("picking the current one saves nothing", async () => {
    const { wrapper } = await mountSlot();
    await rows(wrapper)[0].trigger("click");
    expect(patchUserConfig).not.toHaveBeenCalled();
  });

  it("New ComfyUI connection adds one without switching", async () => {
    const { wrapper, filterStore } = await mountSlot();
    await item(wrapper, "New ComfyUI connection…").trigger("click");
    const dialog = wrapper.find('[data-testid="dialog"]');
    expect(dialog.exists()).toBe(true);
    const [address, name] = dialog.findAll("input");
    await address.setValue("192.0.2.30:8188");
    await name.setValue("Laptop");
    const after = {
      connections: [
        ...listed.connections,
        { id: "c3", name: "Laptop", url: "http://192.0.2.30:8188/", status: null },
      ],
      current: "saved",
    };
    addComfyuiConnection.mockResolvedValue(after);
    listComfyuiConnections.mockResolvedValue(after);
    await dialog.findAll("button").find((b) => b.text().startsWith("Add")).trigger("click");
    await flushPromises();
    expect(addComfyuiConnection).toHaveBeenCalledWith({
      url: "http://192.0.2.30:8188",
      name: "Laptop",
    });
    expect(wrapper.find('[data-testid="dialog"]').exists()).toBe(false);
    expect(rows(wrapper).map((r) => r.text())).toEqual(
      expect.arrayContaining([expect.stringContaining("Laptop")]),
    );
    expect(patchUserConfig).not.toHaveBeenCalled();
    expect(filterStore.comfyuiUrl).toBe(LOCAL);
  });
});
