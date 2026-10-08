// Settings › ComfyUI: connecting, and the ComfyUI-PixlStash node pack line.
//
// No address is ever saved that the backend has not proven reachable. Pack
// status: only a definite answer is shown; no ComfyUI, or one that cannot be
// asked, shows nothing.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";

vi.mock("vuetify/components", () => ({
  VCard: { template: "<div><slot /></div>" },
  VDialog: {
    props: ["modelValue"],
    emits: ["update:modelValue"],
    template: '<div v-if="modelValue"><slot /></div>',
  },
  VIcon: { template: "<i />" },
  VProgressCircular: { template: "<i />" },
  VSwitch: {
    props: ["modelValue", "disabled"],
    emits: ["update:modelValue"],
    template:
      '<input type="checkbox" :checked="modelValue" :disabled="disabled" @change="$emit(\'update:modelValue\', $event.target.checked)" />',
  },
}));

const config = vi.hoisted(() => ({
  getUserConfig: vi.fn(),
  patchUserConfig: vi.fn(),
}));
vi.mock("../../api/config", () => config);
const comfyui = vi.hoisted(() => ({
  getPixlstashNode: vi.fn(),
  probeComfyui: vi.fn(),
  getComfyuiLink: vi.fn(),
  linkComfyui: vi.fn(),
  unlinkComfyui: vi.fn(),
}));
vi.mock("../../api/comfyui", () => comfyui);
vi.mock("../../stores/useFilterStore", () => ({
  useFilterStore: () => ({ comfyuiUrl: "" }),
}));
vi.mock("../../stores/useUserPrefsStore", () => ({
  useUserPrefsStore: () => ({ dateFormat: "iso" }),
}));

import ComfyuiHostSection from "./ComfyuiHostSection.vue";
import ComfyuiLinkDialog from "./ComfyuiLinkDialog.vue";

async function mountPane() {
  const wrapper = mount(ComfyuiHostSection, { props: { open: true } });
  await flushPromises();
  return wrapper;
}

const NOT_LINKED = {
  linked: false,
  comfyui_url: null,
  pixlstash_url: null,
  where: null,
  linked_at: null,
};
const LINKED = {
  linked: true,
  comfyui_url: "http://127.0.0.1:8188/",
  pixlstash_url: "http://127.0.0.1:9537/",
  where: "this_computer",
  linked_at: "2026-10-06T09:30:00Z",
  bound_address: "127.0.0.1",
  refused_from: null,
  exposure: null,
};

const status = (wrapper) => wrapper.find('[data-testid="comfyui-pack-status"]');

beforeEach(() => {
  config.getUserConfig.mockReset();
  config.patchUserConfig.mockReset();
  config.patchUserConfig.mockResolvedValue({});
  comfyui.getPixlstashNode.mockReset();
  comfyui.probeComfyui.mockReset();
  comfyui.getComfyuiLink.mockReset();
  comfyui.linkComfyui.mockReset();
  comfyui.unlinkComfyui.mockReset();
  comfyui.getComfyuiLink.mockResolvedValue(NOT_LINKED);
  comfyui.linkComfyui.mockReturnValue(new Promise(() => {}));
  comfyui.unlinkComfyui.mockResolvedValue({ linked: false });
  config.getUserConfig.mockResolvedValue({
    comfyui_url: "http://127.0.0.1:8188/",
  });
});

describe("ComfyUI-PixlStash status line", () => {
  it("says installed when ComfyUI has the pack", async () => {
    comfyui.getPixlstashNode.mockResolvedValue({ can_open_workflows: true });
    const line = status(await mountPane());
    expect(line.text()).toBe("ComfyUI-PixlStash node pack: installed.");
  });

  it("says not found, with the install link, when ComfyUI lacks it", async () => {
    comfyui.getPixlstashNode.mockResolvedValue({ can_open_workflows: false });
    const line = status(await mountPane());
    expect(line.text()).toContain("not found, or too old");
    expect(line.text()).toContain("then restart ComfyUI");
    const link = line.find("a");
    expect(link.attributes("href")).toBe(
      "https://github.com/Pikselkroken/ComfyUI-PixlStash",
    );
    expect(link.attributes("target")).toBe("_blank");
  });

  it("says nothing when ComfyUI cannot be asked", async () => {
    comfyui.getPixlstashNode.mockResolvedValue({ can_open_workflows: null });
    expect(status(await mountPane()).exists()).toBe(false);
  });

  it("says nothing when asking fails", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    comfyui.getPixlstashNode.mockRejectedValue(new Error("offline"));
    expect(status(await mountPane()).exists()).toBe(false);
  });

  it("keeps the newest answer when an older one lands late", async () => {
    let answerFirst;
    comfyui.getPixlstashNode
      .mockReturnValueOnce(new Promise((r) => (answerFirst = r)))
      .mockResolvedValueOnce({ can_open_workflows: true });
    const wrapper = await mountPane();
    await wrapper.setProps({ open: false });
    await wrapper.setProps({ open: true });
    await flushPromises();
    answerFirst({ can_open_workflows: false });
    await flushPromises();
    expect(status(wrapper).text()).toBe(
      "ComfyUI-PixlStash node pack: installed.",
    );
  });

  it("asks nothing while no ComfyUI is configured", async () => {
    config.getUserConfig.mockResolvedValue({ comfyui_url: null });
    comfyui.probeComfyui.mockResolvedValue({ reachable: false, url: "x" });
    const wrapper = await mountPane();
    expect(comfyui.getPixlstashNode).not.toHaveBeenCalled();
    expect(status(wrapper).exists()).toBe(false);
  });
});

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

describe("connecting ComfyUI", () => {
  beforeEach(() => {
    config.getUserConfig.mockResolvedValue({ comfyui_url: null });
    comfyui.getPixlstashNode.mockResolvedValue({ can_open_workflows: true });
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
    expect(byTestId(wrapper, "comfyui-connected-url").text()).toBe(
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
    expect(byTestId(wrapper, "comfyui-connected-url").text()).toBe(
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

  it("asks the saved address before saying Connected", async () => {
    config.getUserConfig.mockResolvedValue({
      comfyui_url: "https://comfy.example.com/",
    });
    let answer;
    comfyui.probeComfyui.mockReturnValue(new Promise((r) => (answer = r)));
    const wrapper = await mountPane();
    expect(comfyui.probeComfyui).toHaveBeenCalledWith(
      "https://comfy.example.com/",
    );
    expect(byTestId(wrapper, "comfyui-connected-url").text()).toBe(
      "https://comfy.example.com/",
    );
    expect(wrapper.text()).toContain("Checking");
    expect(wrapper.text()).not.toContain("Connected");
    answer({ reachable: true, url: "https://comfy.example.com/" });
    await flushPromises();
    expect(wrapper.text()).toContain("Connected");
    expect(wrapper.text()).not.toContain("Checking");
  });

  it("says Not answering when the saved address does not answer on load", async () => {
    config.getUserConfig.mockResolvedValue({
      comfyui_url: "http://127.0.0.1:8188/",
    });
    probeAnswers({});
    const wrapper = await mountPane();
    expect(wrapper.text()).toContain("Not answering");
    expect(wrapper.text()).toContain("No answer at http://127.0.0.1:8188/");
    expect(wrapper.text()).not.toContain("Connected");
  });

  it("drops a load-time answer that lands after a newer save", async () => {
    // The saved address's probe answers only after the person has
    // disconnected and connected the ComfyUI found on this computer.
    config.getUserConfig.mockResolvedValue({
      comfyui_url: "http://127.0.0.1:8188/",
    });
    let late;
    comfyui.probeComfyui
      .mockReturnValueOnce(new Promise((r) => (late = r)))
      .mockResolvedValue({
        reachable: true,
        url: "http://comfy-box.local:8189/",
      });
    const wrapper = await mountPane();
    await byTestId(wrapper, "comfyui-disconnect").trigger("click");
    await flushPromises();
    await byTestId(wrapper, "comfyui-connect-found").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Connected");
    late({ reachable: false, url: "http://127.0.0.1:8188/", detail: "down" });
    await flushPromises();
    expect(wrapper.text()).toContain("Connected");
    expect(wrapper.text()).not.toContain("Not answering");
  });

  it.each(["[::1]", "::1"])(
    "wraps an IPv6 host typed as %s in one pair of brackets",
    async (typed) => {
      probeAnswers({ "http://[::1]:8188/": "http://[::1]:8188/" });
      const wrapper = await mountPane();
      await fillAddress(wrapper, { host: typed, port: "8188" });
      expect(comfyui.probeComfyui).toHaveBeenLastCalledWith(
        "http://[::1]:8188/",
      );
      expect(config.patchUserConfig).toHaveBeenCalledWith({
        comfyui_url: "http://[::1]:8188/",
      });
    },
  );

  it("disconnect clears the saved URL", async () => {
    config.getUserConfig.mockResolvedValue({
      comfyui_url: "http://127.0.0.1:8188/",
    });
    probeAnswers({});
    const wrapper = await mountPane();
    await byTestId(wrapper, "comfyui-disconnect").trigger("click");
    await flushPromises();
    expect(config.patchUserConfig).toHaveBeenCalledWith({ comfyui_url: null });
    expect(wrapper.emitted("update:comfyui-configured")).toEqual([[false]]);
  });
});

it("says why when the saved address cannot be read, instead of hiding the card", async () => {
  vi.spyOn(console, "warn").mockImplementation(() => {});
  config.getUserConfig.mockRejectedValue(new Error("server down"));
  const wrapper = await mountPane();
  expect(wrapper.find(".cf-card").exists()).toBe(true);
  expect(wrapper.find('[role="alert"]').text()).toContain("server down");
});

describe("pulling workflows from ComfyUI", () => {
  const sw = (wrapper) =>
    wrapper.find('[data-testid="comfyui-pull-switch"]');

  it("is on when the config says nothing, and says what it does", async () => {
    const wrapper = await mountPane();
    expect(sw(wrapper).element.checked).toBe(true);
    expect(wrapper.text()).toContain(
      "Checks ComfyUI's saved workflows every minute and keeps each one's versions on its card.",
    );
  });

  it("reads an off setting, and PATCHes the new value when flipped", async () => {
    config.getUserConfig.mockResolvedValue({
      comfyui_url: "http://127.0.0.1:8188/",
      pull_comfyui_workflows: false,
    });
    const wrapper = await mountPane();
    expect(sw(wrapper).element.checked).toBe(false);

    await sw(wrapper).setValue(true);
    await flushPromises();
    expect(config.patchUserConfig).toHaveBeenCalledWith({
      pull_comfyui_workflows: true,
    });
    expect(sw(wrapper).element.checked).toBe(true);
  });

  it("puts the switch back and says so when the save fails", async () => {
    config.patchUserConfig.mockRejectedValue(new Error("boom"));
    const wrapper = await mountPane();
    await sw(wrapper).setValue(false);
    await flushPromises();
    expect(sw(wrapper).element.checked).toBe(true);
    expect(wrapper.find('[role="alert"]').text()).toContain(
      "Could not save that setting.",
    );
  });
});

describe("linking ComfyUI", () => {
  beforeEach(() => {
    comfyui.getPixlstashNode.mockResolvedValue({ can_open_workflows: true });
  });

  it("says Not linked and offers Link as the one primary action", async () => {
    const wrapper = await mountPane();
    expect(comfyui.getComfyuiLink).toHaveBeenCalled();
    expect(byTestId(wrapper, "comfyui-access-value").text()).toBe("Not linked");
    const linkBtn = byTestId(wrapper, "comfyui-link");
    expect(linkBtn.classes()).toContain("app-btn--primary");
    expect(wrapper.findAll(".app-btn--primary")).toHaveLength(1);
    expect(wrapper.text()).toContain(
      "Linking gives ComfyUI full access to this library",
    );
    expect(byTestId(wrapper, "comfyui-disconnect-note").exists()).toBe(false);
  });

  it("warns before linking a ComfyUI on the network, not one on this computer", async () => {
    config.getUserConfig.mockResolvedValue({
      comfyui_url: "http://comfy-box.local:8188/",
    });
    const onNetwork = await mountPane();
    expect(byTestId(onNetwork, "comfyui-exposure-notice").text()).toContain(
      "Link it only on a network you trust",
    );
    config.getUserConfig.mockResolvedValue({
      comfyui_url: "http://127.0.0.1:8188/",
    });
    const local = await mountPane();
    expect(byTestId(local, "comfyui-exposure-notice").exists()).toBe(false);
  });

  it("warns before linking a ComfyUI here that was started with --listen", async () => {
    comfyui.probeComfyui.mockResolvedValue({
      reachable: true,
      url: "http://127.0.0.1:8188/",
      listens_on_network: true,
    });
    const wrapper = await mountPane();
    expect(byTestId(wrapper, "comfyui-exposure-notice").text()).toContain(
      "restart it without --listen",
    );
  });

  it("keeps warning once linked when other computers can reach ComfyUI", async () => {
    comfyui.getComfyuiLink.mockResolvedValue({ ...LINKED, exposure: "listening" });
    const wrapper = await mountPane();
    expect(byTestId(wrapper, "comfyui-exposure-notice").exists()).toBe(true);
    expect(byTestId(wrapper, "comfyui-relink").exists()).toBe(false);
  });

  it("Check again clears the warning once ComfyUI stops listening on the network", async () => {
    comfyui.getComfyuiLink.mockResolvedValue({ ...LINKED, exposure: "listening" });
    comfyui.probeComfyui.mockResolvedValue({
      reachable: true,
      url: "http://127.0.0.1:8188/",
      listens_on_network: true,
    });
    const wrapper = await mountPane();
    expect(byTestId(wrapper, "comfyui-exposure-notice").exists()).toBe(true);
    comfyui.probeComfyui.mockResolvedValue({
      reachable: true,
      url: "http://127.0.0.1:8188/",
      listens_on_network: false,
    });
    await byTestId(wrapper, "comfyui-check-again").trigger("click");
    await flushPromises();
    expect(byTestId(wrapper, "comfyui-exposure-notice").exists()).toBe(false);
  });

  it("offers Link again when ComfyUI's key was refused from another address", async () => {
    comfyui.getComfyuiLink.mockResolvedValue({
      ...LINKED,
      where: "local_network",
      bound_address: "192.0.2.4",
      refused_from: "192.0.2.7",
    });
    const wrapper = await mountPane();
    const said = byTestId(wrapper, "comfyui-key-refused").text();
    expect(said).toContain("refused ComfyUI's key from 192.0.2.7");
    expect(said).toContain("only works from 192.0.2.4");
    const relink = byTestId(wrapper, "comfyui-relink");
    expect(relink.classes()).toContain("app-btn--primary");
    await relink.trigger("click");
    await flushPromises();
    expect(comfyui.linkComfyui).toHaveBeenCalledTimes(1);
  });

  it("offers Link again, quietly, for a link made before keys were bound", async () => {
    comfyui.getComfyuiLink.mockResolvedValue({ ...LINKED, bound_address: null });
    const wrapper = await mountPane();
    expect(byTestId(wrapper, "comfyui-key-unbound").exists()).toBe(true);
    expect(byTestId(wrapper, "comfyui-relink").classes()).not.toContain(
      "app-btn--primary",
    );
  });

  it("Check again reads the link again", async () => {
    comfyui.getComfyuiLink.mockResolvedValue(LINKED);
    const wrapper = await mountPane();
    expect(byTestId(wrapper, "comfyui-key-refused").exists()).toBe(false);
    comfyui.getComfyuiLink.mockResolvedValue({
      ...LINKED,
      refused_from: "192.0.2.7",
    });
    await byTestId(wrapper, "comfyui-check-again").trigger("click");
    await flushPromises();
    expect(byTestId(wrapper, "comfyui-key-refused").exists()).toBe(true);
  });

  it("says Full access with the date when linked, with no primary", async () => {
    comfyui.getComfyuiLink.mockResolvedValue(LINKED);
    const wrapper = await mountPane();
    expect(byTestId(wrapper, "comfyui-access-value").text()).toBe(
      "Full access · linked 2026-10-06",
    );
    expect(byTestId(wrapper, "comfyui-link").exists()).toBe(false);
    expect(wrapper.findAll(".app-btn--primary")).toHaveLength(0);
    expect(byTestId(wrapper, "comfyui-disconnect-note").text()).toContain(
      "removes PixlStash's key from ComfyUI",
    );
  });

  it("shows no Access row when the link cannot be read", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    comfyui.getComfyuiLink.mockRejectedValue(new Error("403"));
    const wrapper = await mountPane();
    expect(byTestId(wrapper, "comfyui-access").exists()).toBe(false);
  });

  it("Link opens the dialog, which posts at once", async () => {
    const wrapper = await mountPane();
    expect(comfyui.linkComfyui).not.toHaveBeenCalled();
    await byTestId(wrapper, "comfyui-link").trigger("click");
    await flushPromises();
    expect(comfyui.linkComfyui).toHaveBeenCalledTimes(1);
    expect(byTestId(wrapper, "comfyui-link-steps").exists()).toBe(true);
  });

  it("Disconnect revokes the link before clearing the URL", async () => {
    comfyui.getComfyuiLink.mockResolvedValue(LINKED);
    probeAnswers({});
    const order = [];
    comfyui.unlinkComfyui.mockImplementation(async () => {
      order.push("unlink");
      return { linked: false };
    });
    config.patchUserConfig.mockImplementation(async () => {
      order.push("clear");
      return {};
    });
    const wrapper = await mountPane();
    await byTestId(wrapper, "comfyui-disconnect").trigger("click");
    await flushPromises();
    expect(order).toEqual(["unlink", "clear"]);
    expect(config.patchUserConfig).toHaveBeenCalledWith({ comfyui_url: null });
  });

  it("Disconnect still clears the URL when revoking fails, and says the key is live", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    comfyui.getComfyuiLink.mockResolvedValue(LINKED);
    comfyui.unlinkComfyui.mockRejectedValue(new Error("revoke failed"));
    probeAnswers({});
    const wrapper = await mountPane();
    await byTestId(wrapper, "comfyui-disconnect").trigger("click");
    await flushPromises();
    expect(config.patchUserConfig).toHaveBeenCalledWith({ comfyui_url: null });
    expect(wrapper.emitted("update:comfyui-configured")).toEqual([[false]]);
    expect(wrapper.text()).toContain("revoke failed");
    expect(wrapper.text()).toContain("delete it under Account › API Tokens");
  });

  it("closing the link dialog returns focus to Link ComfyUI", async () => {
    const wrapper = mount(ComfyuiHostSection, {
      props: { open: true },
      attachTo: document.body,
    });
    await flushPromises();
    await byTestId(wrapper, "comfyui-link").trigger("click");
    await flushPromises();
    wrapper.findComponent(ComfyuiLinkDialog).vm.$emit("close");
    await flushPromises();
    expect(document.activeElement).toBe(byTestId(wrapper, "comfyui-link").element);
    wrapper.unmount();
  });

  it("Disconnect revokes when the link status could not be read", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    comfyui.getComfyuiLink.mockRejectedValue(new Error("server busy"));
    probeAnswers({});
    const wrapper = await mountPane();
    await byTestId(wrapper, "comfyui-disconnect").trigger("click");
    await flushPromises();
    expect(comfyui.unlinkComfyui).toHaveBeenCalled();
    expect(config.patchUserConfig).toHaveBeenCalledWith({ comfyui_url: null });
  });

  it("Disconnect does not call DELETE when not linked", async () => {
    probeAnswers({});
    const wrapper = await mountPane();
    await byTestId(wrapper, "comfyui-disconnect").trigger("click");
    await flushPromises();
    expect(comfyui.unlinkComfyui).not.toHaveBeenCalled();
    expect(config.patchUserConfig).toHaveBeenCalledWith({ comfyui_url: null });
  });
});
