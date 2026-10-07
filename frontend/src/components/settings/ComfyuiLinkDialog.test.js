// ComfyuiLinkDialog: posts on open, renders the four steps from the reply, and
// puts each fix next to the step that needs it.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";

vi.mock("vuetify/components", () => ({
  VDialog: {
    props: ["modelValue"],
    template: '<div v-if="modelValue"><slot /></div>',
  },
  VIcon: { template: "<i><slot /></i>" },
  VProgressCircular: { template: '<i class="spinner" />' },
  VTooltip: {
    setup:
      (_p, { slots }) =>
      () =>
        slots.activator?.({ props: {} }),
  },
}));

const comfyui = vi.hoisted(() => ({
  linkComfyui: vi.fn(),
  installComfyuiPack: vi.fn(),
  getPixlstashNode: vi.fn(),
}));
vi.mock("../../api/comfyui", () => comfyui);

import ComfyuiLinkDialog from "./ComfyuiLinkDialog.vue";

const step = (id, state, extra = {}) => ({
  id,
  state,
  detail: null,
  reason: null,
  ...extra,
});

function replyWith(linkStep, { nodes = step("nodes", "done") } = {}) {
  const failedAt = [nodes, linkStep].some((s) => s.state !== "done");
  return {
    linked: !failedAt,
    link: {},
    steps: [
      step("reach", "done", { detail: "127.0.0.1:8188 · on this computer" }),
      nodes,
      linkStep,
      step("check", failedAt ? "not_run" : "done"),
    ],
  };
}

async function openDialog() {
  const wrapper = mount(ComfyuiLinkDialog, { props: { open: true } });
  await flushPromises();
  return wrapper;
}

const byTestId = (wrapper, id) => wrapper.find(`[data-testid="${id}"]`);
const stateOf = (wrapper, id) =>
  byTestId(wrapper, `comfyui-link-step-${id}`).attributes("data-state");

beforeEach(() => {
  comfyui.linkComfyui.mockReset();
  comfyui.installComfyuiPack.mockReset();
  comfyui.getPixlstashNode.mockReset();
});

afterEach(() => {
  delete window.pixlstashDesktop;
  vi.useRealTimers();
});

describe("ComfyuiLinkDialog", () => {
  it("posts on open and shows row 1 working, the rest waiting", async () => {
    comfyui.linkComfyui.mockReturnValue(new Promise(() => {}));
    const wrapper = mount(ComfyuiLinkDialog, { props: { open: true } });
    await wrapper.vm.$nextTick();
    expect(comfyui.linkComfyui).toHaveBeenCalledTimes(1);
    expect(stateOf(wrapper, "reach")).toBe("working");
    expect(wrapper.find(".spinner").exists()).toBe(true);
    for (const id of ["nodes", "link", "check"]) {
      expect(stateOf(wrapper, id)).toBe("not_run");
    }
    const list = byTestId(wrapper, "comfyui-link-steps");
    expect(list.attributes("role")).toBe("status");
    expect(byTestId(wrapper, "comfyui-link-close").text()).toBe("Cancel");
  });

  it("renders every step state from the reply and Done when linked", async () => {
    comfyui.linkComfyui.mockResolvedValue(replyWith(step("link", "done")));
    const wrapper = await openDialog();
    for (const id of ["reach", "nodes", "link", "check"]) {
      expect(stateOf(wrapper, id)).toBe("done");
    }
    expect(wrapper.text()).toContain("127.0.0.1:8188 · on this computer");
    expect(byTestId(wrapper, "comfyui-link-done").exists()).toBe(true);
    await byTestId(wrapper, "comfyui-link-done").trigger("click");
    expect(wrapper.emitted("close")).toHaveLength(1);
  });

  it("renders failed, needs_you and not_run", async () => {
    comfyui.linkComfyui.mockResolvedValue(
      replyWith(
        step("link", "failed", {
          reason: "write_failed",
          detail: "ComfyUI refused the settings write.",
        }),
      ),
    );
    const wrapper = await openDialog();
    expect(stateOf(wrapper, "link")).toBe("failed");
    expect(stateOf(wrapper, "check")).toBe("not_run");
    expect(wrapper.text()).toContain("ComfyUI refused the settings write.");
    expect(wrapper.text()).toContain("Waiting");
    expect(byTestId(wrapper, "comfyui-link-done").exists()).toBe(false);
    expect(byTestId(wrapper, "comfyui-link-close").text()).toBe("Close");
  });

  it("pack_missing shows the install hint, and Try again re-posts", async () => {
    comfyui.linkComfyui.mockResolvedValue(
      replyWith(step("link", "not_run"), {
        nodes: step("nodes", "needs_you", {
          reason: "pack_missing",
          detail: "ComfyUI-PixlStash is not installed in this ComfyUI.",
        }),
      }),
    );
    const wrapper = await openDialog();
    expect(stateOf(wrapper, "nodes")).toBe("needs_you");
    const fix = byTestId(wrapper, "comfyui-link-fix-pack_missing");
    expect(fix.text()).toContain("then restart ComfyUI");
    expect(fix.find("a").attributes("href")).toBe(
      "https://github.com/Pikselkroken/ComfyUI-PixlStash",
    );
    await byTestId(wrapper, "comfyui-link-retry").trigger("click");
    await flushPromises();
    expect(comfyui.linkComfyui).toHaveBeenCalledTimes(2);
  });

  const remoteOff = () =>
    replyWith(
      step("link", "needs_you", {
        reason: "remote_access_off",
        detail: "ComfyUI is on another computer. Remote access is off.",
      }),
    );

  it("remote_access_off on the desktop turns on remote access with HTTPS", async () => {
    const desktop = {
      getServerSettings: vi.fn(async () => ({
        enabled: false,
        port: 9600,
        ssl: false,
      })),
      setServerSettings: vi.fn(async () => {}),
    };
    window.pixlstashDesktop = desktop;
    comfyui.linkComfyui.mockResolvedValue(remoteOff());
    const wrapper = await openDialog();
    const fix = byTestId(wrapper, "comfyui-link-fix-remote_access_off");
    expect(fix.text()).toContain(
      "ComfyUI's own page stays plain HTTP, so anyone on your network who opens it can see the key.",
    );
    expect(wrapper.text()).toContain("Remote access is off.");
    expect(fix.text()).not.toContain("in PixlStash's server settings");
    const btn = byTestId(wrapper, "comfyui-link-remote-https");
    expect(btn.classes()).toContain("app-btn--primary");
    await btn.trigger("click");
    await flushPromises();
    expect(desktop.setServerSettings).toHaveBeenCalledWith({
      enabled: true,
      port: 9600,
      ssl: true,
    });
  });

  it("remote_access_off outside the desktop says where to turn it on", async () => {
    comfyui.linkComfyui.mockResolvedValue(remoteOff());
    const wrapper = await openDialog();
    expect(byTestId(wrapper, "comfyui-link-remote-https").exists()).toBe(false);
    const fix = byTestId(wrapper, "comfyui-link-fix-remote_access_off");
    expect(fix.text()).toContain(
      "Turn on remote access with HTTPS in PixlStash's server settings, then try again.",
    );
    expect(byTestId(wrapper, "comfyui-link-retry").exists()).toBe(true);
  });

  it("password_missing points at Account", async () => {
    comfyui.linkComfyui.mockResolvedValue(
      replyWith(
        step("link", "needs_you", {
          reason: "password_missing",
          detail: "Remote access needs an owner password.",
        }),
      ),
    );
    const wrapper = await openDialog();
    expect(
      byTestId(wrapper, "comfyui-link-fix-password_missing").text(),
    ).toContain("Set an owner password under Account first.");
  });

  it("a failed request says why and offers Try again", async () => {
    comfyui.linkComfyui.mockRejectedValue(new Error("Network Error"));
    const wrapper = await openDialog();
    expect(byTestId(wrapper, "comfyui-link-error").text()).toBe(
      "Network Error",
    );
    expect(byTestId(wrapper, "comfyui-link-retry").exists()).toBe(true);
  });
});

describe("ComfyuiLinkDialog: installing the nodes", () => {
  const packMissing = (canInstall) => ({
    ...replyWith(step("link", "not_run"), {
      nodes: step("nodes", "needs_you", {
        reason: "pack_missing",
        detail: "Not installed in this ComfyUI.",
      }),
    }),
    can_install_pack: canInstall,
  });

  const installed = (extra = {}) => ({
    installed_to: "/home/me/ComfyUI/custom_nodes/ComfyUI-PixlStash",
    version: "1.2.0",
    replaced: [],
    restart: "requested",
    detail: null,
    ...extra,
  });

  async function openAndInstall(installReply) {
    vi.useFakeTimers();
    comfyui.linkComfyui.mockResolvedValueOnce(packMissing(true));
    comfyui.installComfyuiPack.mockResolvedValue(installReply);
    const wrapper = await openDialog();
    await byTestId(wrapper, "comfyui-link-install").trigger("click");
    await flushPromises();
    return wrapper;
  }

  it("offers Install only when the reply says it can install", async () => {
    comfyui.linkComfyui.mockResolvedValue(packMissing(false));
    const off = await openDialog();
    expect(byTestId(off, "comfyui-link-install").exists()).toBe(false);
    expect(byTestId(off, "comfyui-link-manual-steps").exists()).toBe(true);
    expect(byTestId(off, "comfyui-link-retry").exists()).toBe(true);

    comfyui.linkComfyui.mockResolvedValue(packMissing(true));
    const on = await openDialog();
    const btn = byTestId(on, "comfyui-link-install");
    expect(btn.text()).toBe("Install and restart ComfyUI");
    expect(btn.classes()).toContain("app-btn--primary");
    expect(on.text()).toContain("Not installed in this ComfyUI.");
    expect(on.text()).toContain("An older copy goes to the trash.");
    // Manual steps stay behind the quiet toggle, and Try again with them.
    expect(byTestId(on, "comfyui-link-manual-steps").exists()).toBe(false);
    expect(byTestId(on, "comfyui-link-retry").exists()).toBe(false);
    await byTestId(on, "comfyui-link-show-manual").trigger("click");
    expect(byTestId(on, "comfyui-link-manual-steps").text()).toContain(
      "then restart ComfyUI",
    );
    expect(byTestId(on, "comfyui-link-retry").exists()).toBe(true);
  });

  it("Install posts and the nodes row works while it runs", async () => {
    comfyui.linkComfyui.mockResolvedValue(packMissing(true));
    comfyui.installComfyuiPack.mockReturnValue(new Promise(() => {}));
    const wrapper = await openDialog();
    await byTestId(wrapper, "comfyui-link-install").trigger("click");
    expect(comfyui.installComfyuiPack).toHaveBeenCalledTimes(1);
    expect(stateOf(wrapper, "nodes")).toBe("working");
    expect(wrapper.text()).toContain("Installing the PixlStash nodes…");
  });

  it("lists the replaced copies, then re-links once ComfyUI has the nodes", async () => {
    const wrapper = await openAndInstall(
      installed({ replaced: ["/home/me/old/ComfyUI-PixlStash"] }),
    );
    expect(byTestId(wrapper, "comfyui-link-replaced").text()).toContain(
      "/home/me/old/ComfyUI-PixlStash",
    );
    expect(stateOf(wrapper, "nodes")).toBe("working");
    expect(wrapper.text()).toContain("Restarting ComfyUI…");

    comfyui.getPixlstashNode.mockRejectedValueOnce(new Error("down"));
    comfyui.getPixlstashNode.mockResolvedValueOnce({
      can_open_workflows: null,
    });
    comfyui.getPixlstashNode.mockResolvedValue({ can_open_workflows: true });
    comfyui.linkComfyui.mockResolvedValue(replyWith(step("link", "done")));
    await vi.advanceTimersByTimeAsync(2000);
    await vi.advanceTimersByTimeAsync(2000);
    expect(comfyui.linkComfyui).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();
    expect(comfyui.getPixlstashNode).toHaveBeenCalledTimes(3);
    expect(comfyui.linkComfyui).toHaveBeenCalledTimes(2);
    expect(stateOf(wrapper, "nodes")).toBe("done");
    expect(byTestId(wrapper, "comfyui-link-done").exists()).toBe(true);
    // Linked: the polling is over.
    await vi.advanceTimersByTimeAsync(10000);
    expect(comfyui.getPixlstashNode).toHaveBeenCalledTimes(3);
  });

  it("a manual restart asks for one and says why", async () => {
    const wrapper = await openAndInstall(
      installed({
        restart: "manual",
        detail: "ComfyUI-Manager is not installed.",
      }),
    );
    expect(stateOf(wrapper, "nodes")).toBe("needs_you");
    expect(wrapper.text()).toContain("Restart ComfyUI to load the nodes.");
    expect(wrapper.text()).toContain("ComfyUI-Manager is not installed.");
    comfyui.getPixlstashNode.mockResolvedValue({ can_open_workflows: false });
    await vi.advanceTimersByTimeAsync(2000);
    expect(comfyui.getPixlstashNode).toHaveBeenCalledTimes(1);
  });

  it("gives up after two minutes and offers Try again", async () => {
    comfyui.getPixlstashNode.mockResolvedValue({ can_open_workflows: false });
    const wrapper = await openAndInstall(installed());
    await vi.advanceTimersByTimeAsync(118000);
    expect(stateOf(wrapper, "nodes")).toBe("working");
    await vi.advanceTimersByTimeAsync(2000);
    expect(stateOf(wrapper, "nodes")).toBe("needs_you");
    expect(wrapper.text()).toContain(
      "ComfyUI did not come back with the nodes. Restart it, then Try again.",
    );
    const calls = comfyui.getPixlstashNode.mock.calls.length;
    await vi.advanceTimersByTimeAsync(10000);
    expect(comfyui.getPixlstashNode).toHaveBeenCalledTimes(calls);
    comfyui.linkComfyui.mockResolvedValue(packMissing(true));
    await byTestId(wrapper, "comfyui-link-retry").trigger("click");
    await flushPromises();
    expect(comfyui.linkComfyui).toHaveBeenCalledTimes(2);
  });

  it("a refused install shows why, the manual steps and Try again", async () => {
    vi.useFakeTimers();
    comfyui.linkComfyui.mockResolvedValue(packMissing(true));
    comfyui.installComfyuiPack.mockRejectedValue({
      response: {
        status: 409,
        data: { detail: "No custom_nodes folder found." },
      },
    });
    const wrapper = await openDialog();
    await byTestId(wrapper, "comfyui-link-install").trigger("click");
    await flushPromises();
    expect(byTestId(wrapper, "comfyui-link-install-error").text()).toBe(
      "No custom_nodes folder found.",
    );
    expect(byTestId(wrapper, "comfyui-link-retry").exists()).toBe(true);
    await byTestId(wrapper, "comfyui-link-show-manual").trigger("click");
    expect(byTestId(wrapper, "comfyui-link-manual-steps").exists()).toBe(true);
    await vi.advanceTimersByTimeAsync(10000);
    expect(comfyui.getPixlstashNode).not.toHaveBeenCalled();
  });

  it("stops polling when the dialog closes, and on unmount", async () => {
    comfyui.getPixlstashNode.mockResolvedValue({ can_open_workflows: false });
    const wrapper = await openAndInstall(installed());
    await vi.advanceTimersByTimeAsync(2000);
    expect(comfyui.getPixlstashNode).toHaveBeenCalledTimes(1);
    await wrapper.setProps({ open: false });
    await vi.advanceTimersByTimeAsync(10000);
    expect(comfyui.getPixlstashNode).toHaveBeenCalledTimes(1);

    const second = await openAndInstall(installed());
    second.unmount();
    await vi.advanceTimersByTimeAsync(10000);
    expect(comfyui.getPixlstashNode).toHaveBeenCalledTimes(1);
  });
});
