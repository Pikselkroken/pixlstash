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
  VSwitch: {
    props: ["modelValue", "disabled"],
    emits: ["update:modelValue"],
    template:
      '<button role="switch" :aria-checked="String(!!modelValue)" :disabled="disabled" @click="$emit(\'update:modelValue\', !modelValue)" />',
  },
  VTooltip: {
    setup:
      (_p, { slots }) =>
      () =>
        slots.activator?.({ props: {} }),
  },
}));

const comfyui = vi.hoisted(() => ({
  linkComfyuiConnection: vi.fn(),
  installComfyuiPack: vi.fn(),
  probeComfyui: vi.fn(),
}));
vi.mock("../../api/comfyui", () => comfyui);
const users = vi.hoisted(() => ({ setTokenLibraries: vi.fn() }));
vi.mock("../../api/users", () => users);
const libraries = vi.hoisted(() => ({ libraries: [], canManage: true }));
vi.mock("../../stores/useLibrariesStore", () => ({
  useLibrariesStore: () => libraries,
}));

import ComfyuiLinkDialog from "./ComfyuiLinkDialog.vue";

const THIS_PC = { id: "pc", name: "This PC", url: "http://127.0.0.1:8188/" };
const STUDIO = { id: "st", name: "Studio", url: "http://192.0.2.20:8188/" };

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
  const wrapper = mount(ComfyuiLinkDialog, { props: { open: true, connection: THIS_PC } });
  await flushPromises();
  return wrapper;
}

const byTestId = (wrapper, id) => wrapper.find(`[data-testid="${id}"]`);
const stateOf = (wrapper, id) =>
  byTestId(wrapper, `comfyui-link-step-${id}`).attributes("data-state");

beforeEach(() => {
  comfyui.linkComfyuiConnection.mockReset();
  comfyui.installComfyuiPack.mockReset();
  comfyui.probeComfyui.mockReset();
  users.setTokenLibraries.mockReset();
  libraries.libraries = [{ uuid: "a" }];
});

afterEach(() => {
  delete window.pixlstashDesktop;
  vi.useRealTimers();
});

describe("ComfyuiLinkDialog", () => {
  it("offers Use in every library once linked, with more than one library", async () => {
    const linkedReply = {
      ...replyWith(step("link", "done")),
      link: { linked: true, token_id: 7, all_libraries: false },
    };
    comfyui.linkComfyuiConnection.mockResolvedValue(linkedReply);
    expect(byTestId(await openDialog(), "comfyui-link-every-library").exists()).toBe(
      false,
    );

    libraries.libraries = [{ uuid: "a" }, { uuid: "b" }];
    users.setTokenLibraries.mockResolvedValue({ all_libraries: true });
    const wrapper = await openDialog();
    await byTestId(wrapper, "comfyui-link-every-library").trigger("click");
    await flushPromises();
    expect(users.setTokenLibraries).toHaveBeenCalledWith(7, true);

    // Not while the link is not made: there is no key to widen.
    comfyui.linkComfyuiConnection.mockResolvedValue(
      replyWith(step("link", "needs_you", { reason: "https_off" })),
    );
    expect(byTestId(await openDialog(), "comfyui-link-every-library").exists()).toBe(
      false,
    );
  });

  it("posts on open and shows row 1 working, the rest waiting", async () => {
    comfyui.linkComfyuiConnection.mockReturnValue(new Promise(() => {}));
    const wrapper = mount(ComfyuiLinkDialog, { props: { open: true, connection: THIS_PC } });
    await wrapper.vm.$nextTick();
    expect(comfyui.linkComfyuiConnection).toHaveBeenCalledTimes(1);
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
    comfyui.linkComfyuiConnection.mockResolvedValue(replyWith(step("link", "done")));
    const wrapper = await openDialog();
    for (const id of ["reach", "nodes", "link", "check"]) {
      expect(stateOf(wrapper, id)).toBe("done");
    }
    expect(wrapper.text()).toContain("127.0.0.1:8188 · on this computer");
    expect(byTestId(wrapper, "comfyui-link-done").exists()).toBe(true);
    await byTestId(wrapper, "comfyui-link-done").trigger("click");
    expect(wrapper.emitted("close")).toHaveLength(1);
  });

  it("warns, with what to do, when other computers can reach ComfyUI", async () => {
    comfyui.linkComfyuiConnection.mockResolvedValue({
      ...replyWith(step("link", "done")),
      exposure: "listening",
    });
    const listening = await openDialog();
    const notice = byTestId(listening, "comfyui-exposure-notice");
    expect(notice.text()).toContain(
      "Other computers can use PixlStash through ComfyUI",
    );
    expect(notice.text()).toContain("restart it without --listen");

    comfyui.linkComfyuiConnection.mockResolvedValue({
      ...replyWith(step("link", "done")),
      exposure: "other_computer",
    });
    const elsewhere = await openDialog();
    expect(byTestId(elsewhere, "comfyui-exposure-notice").text()).toContain(
      "Link it only on a network you trust",
    );

    comfyui.linkComfyuiConnection.mockResolvedValue({
      ...replyWith(step("link", "done")),
      exposure: null,
    });
    const local = await openDialog();
    expect(byTestId(local, "comfyui-exposure-notice").exists()).toBe(false);
  });

  it("renders failed, needs_you and not_run", async () => {
    comfyui.linkComfyuiConnection.mockResolvedValue(
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
    comfyui.linkComfyuiConnection.mockResolvedValue(
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
    expect(comfyui.linkComfyuiConnection).toHaveBeenCalledTimes(2);
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
    comfyui.linkComfyuiConnection.mockResolvedValue(remoteOff());
    const wrapper = await openDialog();
    const fix = byTestId(wrapper, "comfyui-link-fix-remote_access_off");
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
    comfyui.linkComfyuiConnection.mockResolvedValue(remoteOff());
    const wrapper = await openDialog();
    expect(byTestId(wrapper, "comfyui-link-remote-https").exists()).toBe(false);
    const fix = byTestId(wrapper, "comfyui-link-fix-remote_access_off");
    expect(fix.text()).toContain(
      "Turn on remote access with HTTPS in PixlStash's server settings, then try again.",
    );
    expect(byTestId(wrapper, "comfyui-link-retry").exists()).toBe(true);
  });

  it("password_missing points at Account", async () => {
    comfyui.linkComfyuiConnection.mockResolvedValue(
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
    comfyui.linkComfyuiConnection.mockRejectedValue(new Error("Network Error"));
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
    comfyui.linkComfyuiConnection.mockResolvedValueOnce(packMissing(true));
    comfyui.installComfyuiPack.mockResolvedValue(installReply);
    const wrapper = await openDialog();
    await byTestId(wrapper, "comfyui-link-install").trigger("click");
    await flushPromises();
    return wrapper;
  }

  it("offers Install only when the reply says it can install", async () => {
    comfyui.linkComfyuiConnection.mockResolvedValue(packMissing(false));
    const off = await openDialog();
    expect(byTestId(off, "comfyui-link-install").exists()).toBe(false);
    expect(byTestId(off, "comfyui-link-manual-steps").exists()).toBe(true);
    expect(byTestId(off, "comfyui-link-retry").exists()).toBe(true);

    comfyui.linkComfyuiConnection.mockResolvedValue(packMissing(true));
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
    comfyui.linkComfyuiConnection.mockResolvedValue(packMissing(true));
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

    comfyui.probeComfyui.mockRejectedValueOnce(new Error("down"));
    comfyui.probeComfyui.mockResolvedValueOnce({
      can_open_workflows: null,
    });
    comfyui.probeComfyui.mockResolvedValue({ can_open_workflows: true });
    comfyui.linkComfyuiConnection.mockResolvedValue(replyWith(step("link", "done")));
    await vi.advanceTimersByTimeAsync(2000);
    await vi.advanceTimersByTimeAsync(2000);
    expect(comfyui.linkComfyuiConnection).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();
    expect(comfyui.probeComfyui).toHaveBeenCalledTimes(3);
    expect(comfyui.linkComfyuiConnection).toHaveBeenCalledTimes(2);
    expect(stateOf(wrapper, "nodes")).toBe("done");
    expect(byTestId(wrapper, "comfyui-link-done").exists()).toBe(true);
    // The re-link does not take away what the install moved to the trash.
    expect(byTestId(wrapper, "comfyui-link-replaced").text()).toContain(
      "/home/me/old/ComfyUI-PixlStash",
    );
    // Linked: the polling is over.
    await vi.advanceTimersByTimeAsync(10000);
    expect(comfyui.probeComfyui).toHaveBeenCalledTimes(3);
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
    comfyui.probeComfyui.mockResolvedValue({ can_open_workflows: false });
    await vi.advanceTimersByTimeAsync(2000);
    expect(comfyui.probeComfyui).toHaveBeenCalledTimes(1);
  });

  it("gives up after two minutes and offers Try again", async () => {
    comfyui.probeComfyui.mockResolvedValue({ can_open_workflows: false });
    const wrapper = await openAndInstall(installed());
    await vi.advanceTimersByTimeAsync(118000);
    expect(stateOf(wrapper, "nodes")).toBe("working");
    await vi.advanceTimersByTimeAsync(2000);
    expect(stateOf(wrapper, "nodes")).toBe("needs_you");
    expect(wrapper.text()).toContain(
      "ComfyUI did not come back with the nodes. Restart it, then Try again.",
    );
    const calls = comfyui.probeComfyui.mock.calls.length;
    await vi.advanceTimersByTimeAsync(10000);
    expect(comfyui.probeComfyui).toHaveBeenCalledTimes(calls);
    comfyui.linkComfyuiConnection.mockResolvedValue(packMissing(true));
    await byTestId(wrapper, "comfyui-link-retry").trigger("click");
    await flushPromises();
    expect(comfyui.linkComfyuiConnection).toHaveBeenCalledTimes(2);
  });

  it("a refused install shows why, the manual steps and Try again", async () => {
    vi.useFakeTimers();
    comfyui.linkComfyuiConnection.mockResolvedValue(packMissing(true));
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
    expect(comfyui.probeComfyui).not.toHaveBeenCalled();
  });

  it("stops polling when the dialog closes, and on unmount", async () => {
    comfyui.probeComfyui.mockResolvedValue({ can_open_workflows: false });
    const wrapper = await openAndInstall(installed());
    await vi.advanceTimersByTimeAsync(2000);
    expect(comfyui.probeComfyui).toHaveBeenCalledTimes(1);
    await wrapper.setProps({ open: false });
    await vi.advanceTimersByTimeAsync(10000);
    expect(comfyui.probeComfyui).toHaveBeenCalledTimes(1);

    const second = await openAndInstall(installed());
    second.unmount();
    await vi.advanceTimersByTimeAsync(10000);
    expect(comfyui.probeComfyui).toHaveBeenCalledTimes(1);
  });
});

describe("ComfyuiLinkDialog: one connection (#2194)", () => {
  it("links the connection it was opened on, by its id", async () => {
    comfyui.linkComfyuiConnection.mockResolvedValue(replyWith(step("link", "done")));
    const wrapper = await openDialog();
    expect(comfyui.linkComfyuiConnection).toHaveBeenCalledWith("pc");
    expect(byTestId(wrapper, "comfyui-link-confirm").exists()).toBe(false);
  });

  it("asks first for a ComfyUI on another computer, with the exposure notice", async () => {
    comfyui.linkComfyuiConnection.mockResolvedValue(replyWith(step("link", "done")));
    const wrapper = mount(ComfyuiLinkDialog, {
      props: { open: true, connection: STUDIO },
    });
    await flushPromises();
    expect(byTestId(wrapper, "comfyui-link-confirm").text()).toContain(
      "Studio gets its own key",
    );
    expect(byTestId(wrapper, "comfyui-exposure-notice").exists()).toBe(true);
    expect(comfyui.linkComfyuiConnection).not.toHaveBeenCalled();

    await byTestId(wrapper, "comfyui-link-confirm-link").trigger("click");
    await flushPromises();
    expect(comfyui.linkComfyuiConnection).toHaveBeenCalledWith("st");
    expect(byTestId(wrapper, "comfyui-link-steps").exists()).toBe(true);
  });

  it("Cancel on the question links nothing", async () => {
    const wrapper = mount(ComfyuiLinkDialog, {
      props: { open: true, connection: STUDIO },
    });
    await flushPromises();
    await byTestId(wrapper, "comfyui-link-close").trigger("click");
    expect(wrapper.emitted("close")).toHaveLength(1);
    expect(comfyui.linkComfyuiConnection).not.toHaveBeenCalled();
  });

  it("installs into that connection and probes its own address for the nodes", async () => {
    vi.useFakeTimers();
    comfyui.linkComfyuiConnection.mockResolvedValue({
      ...replyWith(step("link", "not_run"), {
        nodes: step("nodes", "needs_you", { reason: "pack_missing" }),
      }),
      can_install_pack: true,
    });
    comfyui.installComfyuiPack.mockResolvedValue({ restart: "requested", replaced: [] });
    comfyui.probeComfyui.mockResolvedValue({ can_open_workflows: false });
    const wrapper = await openDialog();
    await byTestId(wrapper, "comfyui-link-install").trigger("click");
    await flushPromises();
    expect(comfyui.installComfyuiPack).toHaveBeenCalledWith({ connectionId: "pc" });
    await vi.advanceTimersByTimeAsync(2000);
    expect(comfyui.probeComfyui).toHaveBeenCalledWith("http://127.0.0.1:8188/");
    wrapper.unmount();
  });
});

