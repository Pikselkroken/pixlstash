// ConnectAgentDialog - the configuration it hands over is the whole product.
//
// The two blocks are pasted verbatim into a client, so a wrong server URL or a
// missing token is not a cosmetic bug: the agent simply never connects, and the
// owner has no way to tell which half was wrong. The `/api/v1` strip is the
// specific trap - API_BASE_URL carries the prefix and pixlstash-mcp appends its
// own, so passing it through unchanged produces /api/v1/api/v1/... .

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";

vi.mock("vuetify/components", () => ({
  VIcon: { name: "v-icon", template: "<i><slot /></i>" },
  VDialog: { name: "v-dialog", template: "<div><slot /></div>" },
  VTooltip: {
    name: "VTooltip",
    setup:
      (_p, { slots }) =>
      () =>
        slots.activator?.({ props: {} }),
  },
}));

vi.mock("../../utils/apiClient", () => ({
  API_BASE_URL: "http://127.0.0.1:9537/api/v1",
}));

const createToken = vi.fn();
vi.mock("../../api/users", () => ({
  createToken: (...args) => createToken(...args),
}));

vi.mock("../../utils/clipboard", () => ({ copyText: vi.fn(async () => true) }));

import ConnectAgentDialog from "./ConnectAgentDialog.vue";

async function mintedDialog() {
  const wrapper = mount(ConnectAgentDialog, { props: { open: true } });
  await wrapper.vm.$nextTick();
  // The dialog mints on an explicit press, never on open.
  expect(createToken).not.toHaveBeenCalled();
  await wrapper.find(".app-dialog__footer button:last-child").trigger("click");
  await wrapper.vm.$nextTick();
  await wrapper.vm.$nextTick();
  return wrapper;
}

beforeEach(() => {
  createToken.mockReset();
  createToken.mockResolvedValue({ token: "example-minted-token" });
});

describe("the configuration handed to the agent", () => {
  it("strips the API prefix, because pixlstash-mcp appends its own", async () => {
    const wrapper = await mintedDialog();
    const text = wrapper.text();

    expect(text).toContain("--url http://127.0.0.1:9537");
    expect(text).not.toContain("9537/api/v1");
  });

  it("carries the minted token in both blocks", async () => {
    const wrapper = await mintedDialog();
    const blocks = wrapper.findAll(".cad-code");

    expect(blocks).toHaveLength(2);
    for (const block of blocks) {
      expect(block.text()).toContain("example-minted-token");
    }
  });

  it("asks for a read-only token, never a full-access one", async () => {
    await mintedDialog();

    expect(createToken).toHaveBeenCalledWith(
      expect.objectContaining({ scope: "READ" }),
    );
  });

  it("shows no token and an error when minting fails", async () => {
    createToken.mockRejectedValue(new Error("nope"));
    const wrapper = mount(ConnectAgentDialog, { props: { open: true } });
    await wrapper.find(".app-dialog__footer button:last-child").trigger("click");
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".cad-error").exists()).toBe(true);
    expect(wrapper.findAll(".cad-code")).toHaveLength(0);
  });
});
