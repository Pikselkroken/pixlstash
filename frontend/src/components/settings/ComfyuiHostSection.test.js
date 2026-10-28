// Settings › ComfyUI (#2194): with no connection, today's detect / found /
// address card; with connections, the table of them, each with its own key.
//
// No address is ever saved that the backend has not proven reachable.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";

vi.mock("vuetify/components", () => ({
  VDialog: {
    props: ["modelValue"],
    emits: ["update:modelValue"],
    template: '<div v-if="modelValue"><slot /></div>',
  },
  VIcon: { template: "<i />" },
  VProgressCircular: { template: "<i />" },
  VMenu: {
    props: ["modelValue"],
    template:
      '<div><slot name="activator" :props="{}" /><slot /></div>',
  },
  VSwitch: { template: "<i />" },
  VTooltip: {
    setup:
      (_p, { slots }) =>
      () =>
        slots.activator?.({ props: {} }),
  },
}));

const config = vi.hoisted(() => ({ patchUserConfig: vi.fn() }));
vi.mock("../../api/config", () => config);
const comfyui = vi.hoisted(() => ({
  listComfyuiConnections: vi.fn(),
  addComfyuiConnection: vi.fn(),
  probeComfyui: vi.fn(),
  linkComfyuiConnection: vi.fn(),
  installComfyuiPack: vi.fn(),
  updateComfyuiConnection: vi.fn(),
  removeComfyuiConnection: vi.fn(),
  unlinkComfyuiConnection: vi.fn(),
}));
vi.mock("../../api/comfyui", () => comfyui);
vi.mock("../../api/users", () => ({ setTokenLibraries: vi.fn() }));
vi.mock("../../stores/useLibrariesStore", () => ({
  useLibrariesStore: () => ({ libraries: [], canManage: true }),
}));
const filter = vi.hoisted(() => ({ comfyuiUrl: "" }));
vi.mock("../../stores/useFilterStore", () => ({ useFilterStore: () => filter }));
vi.mock("../../stores/useUserPrefsStore", () => ({
  useUserPrefsStore: () => ({ dateFormat: "iso" }),
}));

import ComfyuiHostSection from "./ComfyuiHostSection.vue";
import ComfyuiLinkDialog from "./ComfyuiLinkDialog.vue";

const UNLINKED = { linked: false };
const entry = (id, name, url, extra = {}) => ({
  id,
  name,
  url,
  status: null,
  gpu: null,
  vram_mb: null,
  nodes: null,
  link: UNLINKED,
  ...extra,
});
const list = (connections, current) => ({ connections, current });

async function mountPane() {
  const wrapper = mount(ComfyuiHostSection, { props: { open: true } });
  await flushPromises();
  return wrapper;
}

// The backend's probe: answers for exactly the URLs in `up`, normalised.
function probeAnswers(up = {}) {
  comfyui.probeComfyui.mockImplementation(async (url) =>
    up[url]
      ? { reachable: true, url: up[url], version: "0.3.62", detail: null }
      : { reachable: false, url, version: null, detail: `No answer at ${url}` },
  );
}

const byTestId = (wrapper, id) => wrapper.find(`[data-testid="${id}"]`);

async function fillAddress(wrapper, { scheme, host, port }) {
  if (scheme === "https") {
    const opts = wrapper.findAll('[role="radio"]');
    await opts.find((o) => o.text() === "https").trigger("click");
  }
  await byTestId(wrapper, "comfyui-host").find("input").setValue(host);
  await byTestId(wrapper, "comfyui-port").find("input").setValue(port);
  await byTestId(wrapper, "comfyui-connect-address").trigger("click");
  await flushPromises();
}

beforeEach(() => {
  vi.clearAllMocks();
  filter.comfyuiUrl = "";
  comfyui.listComfyuiConnections.mockResolvedValue(list([], null));
  comfyui.linkComfyuiConnection.mockReturnValue(new Promise(() => {}));
  config.patchUserConfig.mockResolvedValue({});
});

describe("connecting ComfyUI", () => {
  // Saving makes the address the one connection; the table lists it.
  beforeEach(() => {
    config.patchUserConfig.mockImplementation(async ({ comfyui_url }) => {
      comfyui.listComfyuiConnections.mockResolvedValue(
        list([entry("saved", "This PC", comfyui_url)], "saved"),
      );
      return { config: { comfyui_url } };
    });
  });

  it("finds ComfyUI on 8188 first and offers it", async () => {
    probeAnswers({
      "http://127.0.0.1:8188/": "http://127.0.0.1:8188/",
      "http://127.0.0.1:8000/": "http://127.0.0.1:8000/",
    });
    const wrapper = await mountPane();
    expect(comfyui.probeComfyui.mock.calls.map((c) => c[0])).toEqual([
      "http://127.0.0.1:8188/",
    ]);
    expect(byTestId(wrapper, "comfyui-found-url").text()).toBe(
      "http://127.0.0.1:8188/",
    );
    expect(config.patchUserConfig).not.toHaveBeenCalled();

    await byTestId(wrapper, "comfyui-connect-found").trigger("click");
    await flushPromises();
    expect(config.patchUserConfig).toHaveBeenCalledWith({
      comfyui_url: "http://127.0.0.1:8188/",
    });
    expect(wrapper.emitted("update:comfyui-configured")).toEqual([[true]]);
    expect(byTestId(wrapper, "comfyui-connection-saved").text()).toContain(
      "http://127.0.0.1:8188/",
    );
  });

  it("falls back to ComfyUI Desktop's 8000", async () => {
    probeAnswers({ "http://127.0.0.1:8000/": "http://127.0.0.1:8000/" });
    const wrapper = await mountPane();
    expect(comfyui.probeComfyui.mock.calls.map((c) => c[0])).toEqual([
      "http://127.0.0.1:8188/",
      "http://127.0.0.1:8000/",
    ]);
    expect(byTestId(wrapper, "comfyui-found-url").text()).toBe(
      "http://127.0.0.1:8000/",
    );
  });

  it("shows the address form when nothing answers locally", async () => {
    probeAnswers({});
    const wrapper = await mountPane();
    expect(byTestId(wrapper, "comfyui-found-url").exists()).toBe(false);
    expect(byTestId(wrapper, "comfyui-host").exists()).toBe(true);
    expect(wrapper.text()).toContain("Where is ComfyUI running?");
  });

  it("refuses an unreachable address, saying why, and saves nothing", async () => {
    probeAnswers({});
    const wrapper = await mountPane();
    await fillAddress(wrapper, { host: "comfy-box.local", port: "8189" });
    expect(comfyui.probeComfyui).toHaveBeenLastCalledWith(
      "http://comfy-box.local:8189/",
    );
    expect(byTestId(wrapper, "comfyui-address-error").text()).toContain(
      "No answer at http://comfy-box.local:8189/",
    );
    expect(
      byTestId(wrapper, "comfyui-host").find("input").attributes("aria-invalid"),
    ).toBe("true");
    expect(config.patchUserConfig).not.toHaveBeenCalled();
  });

  it("saves the probe's normalised url for a reachable address", async () => {
    // The backend lower-cases the host: its spelling is what gets saved.
    probeAnswers({
      "http://COMFY-BOX.local:8189/": "http://comfy-box.local:8189/",
    });
    const wrapper = await mountPane();
    await fillAddress(wrapper, { host: " COMFY-BOX.local ", port: "8189" });
    expect(config.patchUserConfig).toHaveBeenCalledWith({
      comfyui_url: "http://comfy-box.local:8189/",
    });
    expect(byTestId(wrapper, "comfyui-connection-saved").text()).toContain(
      "http://comfy-box.local:8189/",
    );
  });

  it("refuses a port out of range without probing it", async () => {
    probeAnswers({});
    const wrapper = await mountPane();
    const before = comfyui.probeComfyui.mock.calls.length;
    await fillAddress(wrapper, { host: "comfy-box.local", port: "70000" });
    expect(comfyui.probeComfyui.mock.calls.length).toBe(before);
    expect(byTestId(wrapper, "comfyui-address-error").text()).toContain(
      "1 and 65535",
    );
    expect(config.patchUserConfig).not.toHaveBeenCalled();
  });

  it("round-trips https: probed and saved as https", async () => {
    probeAnswers({ "https://comfy.example.com:443/": "https://comfy.example.com/" });
    const wrapper = await mountPane();
    await fillAddress(wrapper, {
      scheme: "https",
      host: "comfy.example.com",
      port: "443",
    });
    expect(config.patchUserConfig).toHaveBeenCalledWith({
      comfyui_url: "https://comfy.example.com/",
    });
  });
});

describe("the table of connections", () => {
  const PC = entry("pc", "This PC", "http://127.0.0.1:8188/", {
    status: "connected",
    nodes: true,
    link: { linked: true, linked_at: "2026-10-03T09:00:00Z", bound_address: "127.0.0.1" },
  });
  const BOX = entry("box", "Render box", "http://192.0.2.20:8188/");

  beforeEach(() => {
    comfyui.listComfyuiConnections.mockResolvedValue(list([PC, BOX], "pc"));
  });

  it("lists every connection, the current one checked, and probes nothing", async () => {
    const wrapper = await mountPane();
    expect(byTestId(wrapper, "comfyui-connection-pc").text()).toContain("This PC");
    expect(byTestId(wrapper, "comfyui-connection-box").text()).toContain("Render box");
    expect(byTestId(wrapper, "comfyui-run-on-pc").element.checked).toBe(true);
    expect(byTestId(wrapper, "comfyui-run-on-box").element.checked).toBe(false);
    // Wrong if the empty-state search ran behind a populated list.
    expect(comfyui.probeComfyui).not.toHaveBeenCalled();
    expect(byTestId(wrapper, "comfyui-probing").exists()).toBe(false);
  });

  it("shows Linked with its date, and Link on an unlinked row", async () => {
    const wrapper = await mountPane();
    expect(byTestId(wrapper, "comfyui-linked-pc").exists()).toBe(true);
    expect(byTestId(wrapper, "comfyui-connection-pc").text()).toContain("2026-10-03");
    expect(byTestId(wrapper, "comfyui-link-box").exists()).toBe(true);
    expect(byTestId(wrapper, "comfyui-link-pc").exists()).toBe(false);
  });

  it("Link opens the Link dialog on that row, and closing reads the list again", async () => {
    const wrapper = await mountPane();
    await byTestId(wrapper, "comfyui-link-box").trigger("click");
    await flushPromises();
    const dialog = wrapper.findComponent(ComfyuiLinkDialog);
    expect(dialog.props("connection")).toMatchObject({ id: "box", url: BOX.url });
    const reads = comfyui.listComfyuiConnections.mock.calls.length;
    dialog.vm.$emit("close");
    await flushPromises();
    expect(wrapper.findComponent(ComfyuiLinkDialog).exists()).toBe(false);
    expect(comfyui.listComfyuiConnections.mock.calls.length).toBe(reads + 1);
  });

  it("Add connection opens the new-connection dialog", async () => {
    const wrapper = await mountPane();
    await byTestId(wrapper, "comfyui-add-connection").trigger("click");
    expect(wrapper.text()).toContain("New ComfyUI connection");
  });

  it("the Link keys note opens Account › API Tokens", async () => {
    const wrapper = await mountPane();
    expect(byTestId(wrapper, "comfyui-link-keys-note").text()).toContain(
      "its own key",
    );
    await byTestId(wrapper, "comfyui-open-tokens").trigger("click");
    expect(wrapper.emitted("open-tab")).toEqual([["account"]]);
  });

  it("removing the last connection goes back to looking for ComfyUI", async () => {
    comfyui.listComfyuiConnections.mockResolvedValue(list([BOX], "box"));
    comfyui.removeComfyuiConnection.mockResolvedValue(list([], null));
    probeAnswers({});
    const wrapper = await mountPane();
    await byTestId(wrapper, "comfyui-menu-remove").trigger("click");
    await byTestId(wrapper, "comfyui-remove-confirm-button").trigger("click");
    await flushPromises();
    expect(comfyui.removeComfyuiConnection).toHaveBeenCalledWith("box");
    expect(wrapper.emitted("update:comfyui-configured")).toEqual([[false]]);
    expect(byTestId(wrapper, "comfyui-host").exists()).toBe(true);
  });
});
