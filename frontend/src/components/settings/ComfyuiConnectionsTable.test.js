// ComfyuiConnectionsTable (#2194): the Run on radio, the row menu per row
// state, Remove confirmed only when it costs something, and rename and edit
// address in place.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";

vi.mock("vuetify/components", () => ({
  VIcon: { template: "<i />" },
  VProgressCircular: { template: "<i />" },
  // Rendered open, so a row's menu items can be pressed directly.
  VMenu: {
    template: '<div><slot name="activator" :props="{}" /><slot /></div>',
  },
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
  probeComfyui: vi.fn(),
  removeComfyuiConnection: vi.fn(),
  unlinkComfyuiConnection: vi.fn(),
  updateComfyuiConnection: vi.fn(),
}));
vi.mock("../../api/comfyui", () => comfyui);
const filter = vi.hoisted(() => ({ comfyuiUrl: "" }));
vi.mock("../../stores/useFilterStore", () => ({ useFilterStore: () => filter }));
vi.mock("../../stores/useUserPrefsStore", () => ({
  useUserPrefsStore: () => ({ dateFormat: "iso" }),
}));

import ComfyuiConnectionsTable from "./ComfyuiConnectionsTable.vue";

const LINKED = {
  linked: true,
  linked_at: "2026-10-03T09:00:00Z",
  bound_address: "127.0.0.1",
  refused_from: null,
};
const entry = (id, name, url, extra = {}) => ({
  id,
  name,
  url,
  status: "connected",
  gpu: null,
  vram_mb: null,
  nodes: true,
  link: { linked: false },
  ...extra,
});
const PC = entry("pc", "This PC", "http://127.0.0.1:8188/", { link: LINKED });
const BOX = entry("box", "Render box", "http://192.0.2.20:8188/");
const LAPTOP = entry("lap", "Laptop", "http://192.0.2.30:8188/");

function mountTable(connections = [PC, BOX, LAPTOP], current = "pc") {
  return mount(ComfyuiConnectionsTable, {
    props: { connections, current },
    attachTo: document.body,
  });
}

const byTestId = (wrapper, id) => wrapper.find(`[data-testid="${id}"]`);
const menuItem = (wrapper, id, item) =>
  byTestId(wrapper, `comfyui-menu-${id}`).find(`[data-testid="comfyui-menu-${item}"]`);

beforeEach(() => {
  vi.clearAllMocks();
  filter.comfyuiUrl = PC.url;
  config.patchUserConfig.mockImplementation(async ({ comfyui_url }) => ({
    config: { comfyui_url },
  }));
});

describe("Run on", () => {
  it("is a radio per row, the current one checked", () => {
    const wrapper = mountTable();
    const radios = wrapper.findAll('input[type="radio"]');
    expect(radios).toHaveLength(3);
    // One group, so the arrow keys move between rows as on any radio group.
    expect(new Set(radios.map((r) => r.attributes("name"))).size).toBe(1);
    expect(radios.map((r) => r.element.checked)).toEqual([true, false, false]);
    expect(radios[1].attributes("aria-label")).toBe("Run on Render box");
    wrapper.unmount();
  });

  it("picking a row switches as the sidebar does, and touches no key", async () => {
    const wrapper = mountTable();
    const radio = byTestId(wrapper, "comfyui-run-on-box");
    radio.element.checked = true;
    await radio.trigger("change");
    await flushPromises();
    expect(config.patchUserConfig).toHaveBeenCalledWith({ comfyui_url: BOX.url });
    expect(filter.comfyuiUrl).toBe(BOX.url);
    expect(wrapper.emitted("changed").at(-1)[0].current).toBe("box");
    expect(comfyui.unlinkComfyuiConnection).not.toHaveBeenCalled();
    expect(byTestId(wrapper, "comfyui-table-status").text()).toBe("Run on: Render box");
    wrapper.unmount();
  });

  it("two quick picks end on the second, whichever answers last", async () => {
    let answerFirst;
    config.patchUserConfig
      .mockImplementationOnce(
        () => new Promise((resolve) => (answerFirst = resolve)),
      )
      .mockImplementationOnce(async ({ comfyui_url }) => ({
        config: { comfyui_url },
      }));
    const wrapper = mountTable();
    for (const id of ["box", "lap"]) {
      const radio = byTestId(wrapper, `comfyui-run-on-${id}`);
      radio.element.checked = true;
      await radio.trigger("change");
    }
    await flushPromises();
    answerFirst({ config: { comfyui_url: BOX.url } });
    await flushPromises();
    expect(filter.comfyuiUrl).toBe(LAPTOP.url);
    expect(wrapper.emitted("changed").at(-1)[0].current).toBe("lap");
    wrapper.unmount();
  });

  it("a switch that did not save puts the check back and says so", async () => {
    config.patchUserConfig.mockRejectedValue(new Error("server busy"));
    const wrapper = mountTable();
    const radio = byTestId(wrapper, "comfyui-run-on-box");
    radio.element.focus();
    radio.element.checked = true;
    await radio.trigger("change");
    await flushPromises();
    expect(byTestId(wrapper, "comfyui-run-on-pc").element.checked).toBe(true);
    // The remounted radios keep focus: on the one still current.
    expect(document.activeElement).toBe(byTestId(wrapper, "comfyui-run-on-pc").element);
    expect(byTestId(wrapper, "comfyui-run-on-box").element.checked).toBe(false);
    expect(byTestId(wrapper, "comfyui-table-error").text()).toContain(
      "This PC is still current",
    );
    expect(wrapper.emitted("changed")).toBeUndefined();
    wrapper.unmount();
  });
});

describe("the row menu", () => {
  it("offers Link again and Unlink on a linked row only", () => {
    const wrapper = mountTable();
    expect(menuItem(wrapper, "pc", "relink").exists()).toBe(true);
    expect(menuItem(wrapper, "pc", "unlink").exists()).toBe(true);
    expect(menuItem(wrapper, "box", "relink").exists()).toBe(false);
    expect(menuItem(wrapper, "box", "unlink").exists()).toBe(false);
    for (const id of ["pc", "box"]) {
      for (const item of ["rename", "edit-address", "remove"]) {
        expect(menuItem(wrapper, id, item).exists()).toBe(true);
      }
    }
    wrapper.unmount();
  });

  it("Link again asks the section to open the Link dialog on that row", async () => {
    const wrapper = mountTable();
    await menuItem(wrapper, "pc", "relink").trigger("click");
    expect(wrapper.emitted("link")).toEqual([[expect.objectContaining({ id: "pc" })]]);
    wrapper.unmount();
  });

  it("Unlink revokes that row's key only, and reads the list again", async () => {
    comfyui.unlinkComfyuiConnection.mockResolvedValue({ linked: false });
    const wrapper = mountTable();
    await menuItem(wrapper, "pc", "unlink").trigger("click");
    await flushPromises();
    expect(comfyui.unlinkComfyuiConnection).toHaveBeenCalledTimes(1);
    expect(comfyui.unlinkComfyuiConnection).toHaveBeenCalledWith("pc");
    expect(wrapper.emitted("changed")).toEqual([[null]]);
    wrapper.unmount();
  });
});

describe("Remove", () => {
  it("removes an unlinked row that runs nothing at once", async () => {
    comfyui.removeComfyuiConnection.mockResolvedValue({
      connections: [PC, LAPTOP],
      current: "pc",
    });
    const wrapper = mountTable();
    await menuItem(wrapper, "box", "remove").trigger("click");
    await flushPromises();
    expect(byTestId(wrapper, "comfyui-remove-confirm").exists()).toBe(false);
    expect(comfyui.removeComfyuiConnection).toHaveBeenCalledWith("box");
    expect(wrapper.emitted("changed")[0][0].connections).toEqual([PC, LAPTOP]);
    wrapper.unmount();
  });

  it("asks first for the current, linked row, naming who takes over", async () => {
    const wrapper = mountTable();
    await menuItem(wrapper, "pc", "remove").trigger("click");
    const confirm = byTestId(wrapper, "comfyui-remove-confirm");
    expect(confirm.text()).toContain("Remove This PC?");
    expect(confirm.text()).toContain("Its key is revoked first");
    expect(confirm.text()).toContain("Render box takes over Run on");
    expect(comfyui.removeComfyuiConnection).not.toHaveBeenCalled();

    await byTestId(wrapper, "comfyui-remove-cancel").trigger("click");
    expect(byTestId(wrapper, "comfyui-remove-confirm").exists()).toBe(false);
    expect(comfyui.removeComfyuiConnection).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("asks first for a linked row that is not current", async () => {
    const wrapper = mountTable([PC, BOX], "box");
    await menuItem(wrapper, "pc", "remove").trigger("click");
    expect(byTestId(wrapper, "comfyui-remove-confirm").text()).toContain(
      "It is linked",
    );
    wrapper.unmount();
  });

  it("a remove already sent cannot be cancelled", async () => {
    comfyui.removeComfyuiConnection.mockReturnValue(new Promise(() => {}));
    const wrapper = mountTable();
    await menuItem(wrapper, "pc", "remove").trigger("click");
    await byTestId(wrapper, "comfyui-remove-confirm-button").trigger("click");
    await byTestId(wrapper, "comfyui-remove-cancel").trigger("click");
    await byTestId(wrapper, "comfyui-remove-cancel").trigger("keydown", { key: "Escape" });
    expect(byTestId(wrapper, "comfyui-remove-confirm").exists()).toBe(true);
    wrapper.unmount();
  });

  it("keeps the row, and says why, when its key cannot be revoked", async () => {
    comfyui.removeComfyuiConnection.mockRejectedValue({
      response: { status: 500, data: { detail: "The connection's key could not be revoked" } },
    });
    const wrapper = mountTable();
    await menuItem(wrapper, "pc", "remove").trigger("click");
    await byTestId(wrapper, "comfyui-remove-confirm-button").trigger("click");
    await flushPromises();
    expect(byTestId(wrapper, "comfyui-remove-confirm").text()).toContain(
      "could not be revoked",
    );
    expect(wrapper.emitted("changed")).toBeUndefined();
    wrapper.unmount();
  });
});

describe("rename and edit address, in place", () => {
  it("F2 renames in place; Enter saves the trimmed name", async () => {
    comfyui.updateComfyuiConnection.mockResolvedValue({
      connections: [{ ...BOX, name: "Studio" }],
      current: "pc",
    });
    const wrapper = mountTable();
    await byTestId(wrapper, "comfyui-connection-box").trigger("keydown", { key: "F2" });
    const field = byTestId(wrapper, "comfyui-rename-field").find("input");
    await field.setValue("  Studio ");
    await field.trigger("keydown", { key: "Enter" });
    await flushPromises();
    expect(comfyui.updateComfyuiConnection).toHaveBeenCalledWith("box", {
      name: "Studio",
    });
    expect(byTestId(wrapper, "comfyui-rename-field").exists()).toBe(false);
    wrapper.unmount();
  });

  it("Enter saves once, though it also submits the field's form", async () => {
    comfyui.updateComfyuiConnection.mockReturnValue(new Promise(() => {}));
    const wrapper = mountTable();
    await menuItem(wrapper, "box", "rename").trigger("click");
    const field = byTestId(wrapper, "comfyui-rename-field").find("input");
    await field.setValue("Studio");
    // What a browser does with Enter in a form's only text field.
    await field.trigger("keydown", { key: "Enter" });
    await field.element.form.dispatchEvent(new Event("submit", { cancelable: true }));
    await flushPromises();
    expect(comfyui.updateComfyuiConnection).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });

  it("a refused name is said once, in the row under it", async () => {
    const wrapper = mountTable();
    await menuItem(wrapper, "box", "rename").trigger("click");
    const field = byTestId(wrapper, "comfyui-rename-field").find("input");
    await field.setValue("  ");
    await field.trigger("keydown", { key: "Enter" });
    await flushPromises();
    expect(wrapper.text().split("A name cannot be empty.").length - 1).toBe(1);
    expect(byTestId(wrapper, "comfyui-edit-error").text()).toBe("A name cannot be empty.");
    wrapper.unmount();
  });

  it("Escape leaves the name as it was and asks nothing", async () => {
    const wrapper = mountTable();
    await menuItem(wrapper, "box", "rename").trigger("click");
    const field = byTestId(wrapper, "comfyui-rename-field").find("input");
    await field.setValue("Studio");
    await field.trigger("keydown", { key: "Escape" });
    expect(byTestId(wrapper, "comfyui-rename-field").exists()).toBe(false);
    expect(comfyui.updateComfyuiConnection).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("leaving the rename field cancels it without taking focus", async () => {
    const wrapper = mountTable();
    await menuItem(wrapper, "box", "rename").trigger("click");
    const field = byTestId(wrapper, "comfyui-rename-field").find("input");
    const elsewhere = byTestId(wrapper, "comfyui-test-pc").element;
    elsewhere.focus();
    await field.trigger("blur");
    await flushPromises();
    expect(byTestId(wrapper, "comfyui-rename-field").exists()).toBe(false);
    expect(document.activeElement).toBe(elsewhere);
    wrapper.unmount();
  });

  it("the same address in another case is no move, and asks nothing", async () => {
    comfyui.probeComfyui.mockResolvedValue({ reachable: true, url: PC.url });
    const wrapper = mountTable([{ ...PC, url: "HTTP://127.0.0.1:8188" }, BOX]);
    await menuItem(wrapper, "pc", "edit-address").trigger("click");
    await byTestId(wrapper, "comfyui-edit-save").trigger("click");
    await flushPromises();
    expect(comfyui.updateComfyuiConnection).not.toHaveBeenCalled();
    expect(wrapper.emitted("link")).toBeUndefined();
    wrapper.unmount();
  });

  it("saves an address only once ComfyUI answers there", async () => {
    comfyui.probeComfyui.mockResolvedValue({
      reachable: false,
      url: "http://192.0.2.21:8188/",
      detail: "Nothing answered at 192.0.2.21:8188.",
    });
    const wrapper = mountTable();
    await menuItem(wrapper, "box", "edit-address").trigger("click");
    await byTestId(wrapper, "comfyui-edit-host").find("input").setValue("192.0.2.21");
    await byTestId(wrapper, "comfyui-edit-save").trigger("click");
    await flushPromises();
    expect(byTestId(wrapper, "comfyui-edit-error").text()).toContain("Nothing answered");
    expect(comfyui.updateComfyuiConnection).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("an unlinked row moves with no Link dialog", async () => {
    const moved = "http://192.0.2.21:8188/";
    comfyui.probeComfyui.mockResolvedValue({ reachable: true, url: moved });
    comfyui.updateComfyuiConnection.mockResolvedValue({
      connections: [PC, { ...BOX, url: moved }],
      current: "pc",
    });
    const wrapper = mountTable();
    await menuItem(wrapper, "box", "edit-address").trigger("click");
    expect(byTestId(wrapper, "comfyui-edit-relink-note").exists()).toBe(false);
    await byTestId(wrapper, "comfyui-edit-host").find("input").setValue("192.0.2.21");
    await byTestId(wrapper, "comfyui-edit-save").trigger("click");
    await flushPromises();
    expect(comfyui.updateComfyuiConnection).toHaveBeenCalledWith("box", { url: moved });
    expect(wrapper.emitted("link")).toBeUndefined();
    wrapper.unmount();
  });

  it("a linked row says its key goes, then opens the Link dialog at the new address", async () => {
    const moved = "http://127.0.0.1:8189/";
    comfyui.probeComfyui.mockResolvedValue({ reachable: true, url: moved });
    comfyui.updateComfyuiConnection.mockResolvedValue({
      connections: [
        { ...PC, url: moved, name: "127.0.0.1:8189", link: { linked: false } },
        BOX,
      ],
      current: "pc",
    });
    const wrapper = mountTable();
    await menuItem(wrapper, "pc", "edit-address").trigger("click");
    expect(byTestId(wrapper, "comfyui-edit-relink-note").text()).toContain(
      "revokes it",
    );
    await byTestId(wrapper, "comfyui-edit-port").find("input").setValue("8189");
    await byTestId(wrapper, "comfyui-edit-save").trigger("click");
    await flushPromises();
    expect(comfyui.updateComfyuiConnection).toHaveBeenCalledWith("pc", { url: moved });
    expect(filter.comfyuiUrl).toBe(moved);
    expect(wrapper.emitted("link")).toEqual([
      [expect.objectContaining({ id: "pc", url: moved, name: "127.0.0.1:8189" })],
    ]);
    wrapper.unmount();
  });
});

describe("notes under a row", () => {
  it("says a row is not answering, and how to fix it", () => {
    const wrapper = mountTable([{ ...PC, status: "offline" }, BOX], "box");
    expect(byTestId(wrapper, "comfyui-note-offline-pc").text()).toContain(
      "start it with --listen, or edit the address.",
    );
    expect(byTestId(wrapper, "comfyui-note-offline-box").exists()).toBe(false);
    wrapper.unmount();
  });

  it("says where a key was refused from, and flags a key never bound", () => {
    const wrapper = mountTable([
      { ...PC, link: { ...LINKED, refused_from: "192.0.2.99" } },
      { ...BOX, link: { ...LINKED, bound_address: null } },
    ]);
    expect(byTestId(wrapper, "comfyui-note-refused-pc").text()).toContain(
      "192.0.2.99",
    );
    expect(byTestId(wrapper, "comfyui-note-unbound-box").exists()).toBe(true);
    wrapper.unmount();
  });

  it("Test probes that row's address and reads the list again", async () => {
    comfyui.probeComfyui.mockResolvedValue({
      reachable: true,
      url: BOX.url,
      gpu: "NVIDIA GeForce RTX 3090",
      vram_mb: 24576,
    });
    const wrapper = mountTable();
    await byTestId(wrapper, "comfyui-test-box").trigger("click");
    await flushPromises();
    expect(comfyui.probeComfyui).toHaveBeenCalledWith(BOX.url);
    expect(wrapper.emitted("changed")).toEqual([[null]]);
    expect(byTestId(wrapper, "comfyui-table-status").text()).toBe(
      "Render box connected, RTX 3090 · 24 GB",
    );
    wrapper.unmount();
  });
});
