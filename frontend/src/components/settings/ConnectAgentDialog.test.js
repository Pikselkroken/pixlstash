// ConnectAgentDialog - the configuration it hands over is the whole product.
//
// The two blocks are pasted verbatim into a client, so a wrong server URL or a
// missing token is not a cosmetic bug: the agent simply never connects, and the
// owner has no way to tell which half was wrong.
//
// The URL is the trap, and the fix was to stop emitting one. The desktop shell
// serves its window from an ephemeral loopback port picked per launch, so a URL
// taken from `window.location` is written into the client's config file and is
// dead the next time PixlStash starts. `pixlstash-mcp` reads the configured
// port itself. These tests pin that no address is baked in.

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

// Deliberately an ephemeral desktop port, as `window.location` would give in
// the Electron shell: nothing the dialog emits may contain it. The literal is
// repeated inside the factory rather than referenced, because vi.mock is
// hoisted above every const and would otherwise throw on first use - which is
// exactly never, while the dialog imports nothing from this module.
const EPHEMERAL = "61675";
vi.mock("../../utils/apiClient", () => ({
  API_BASE_URL: "http://127.0.0.1:61675/api/v1",
}));

const createToken = vi.fn();
vi.mock("../../api/users", () => ({
  createToken: (...args) => createToken(...args),
}));

vi.mock("../../utils/clipboard", () => ({ copyText: vi.fn(async () => true) }));

import ConnectAgentDialog from "./ConnectAgentDialog.vue";

async function mintedDialog() {
  const wrapper = mount(ConnectAgentDialog, {
    props: { open: true },
    global: { stubs: { "v-icon": true } },
  });
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
  it("bakes in no address at all, least of all the window's own port", async () => {
    const wrapper = await mintedDialog();
    const text = wrapper.text();

    // The ephemeral port the window happens to be served from is the exact
    // value that used to end up in the client's config file.
    expect(text).not.toContain(EPHEMERAL);
    expect(text).not.toContain("--url");
    expect(text).not.toContain("/api/v1");
    // Positive control: the command is still there to be wrong.
    expect(text).toContain("claude mcp add");
  });

  it("says what to do with each block, not just what it is", async () => {
    const wrapper = await mintedDialog();
    const text = wrapper.text();

    // A copyable JSON object with no instruction is a puzzle: copying it is
    // the obvious part, knowing where it goes is not.
    expect(text).toMatch(/run this once in a terminal/i);
    expect(text).toMatch(/configuration file/i);
    // The one way this goes wrong destructively.
    expect(text).toMatch(/rather than replacing the file/i);
    expect(wrapper.find('a[href*="modelcontextprotocol.io"]').exists()).toBe(true);
  });

  it("names the address when PixlStash is being viewed over the network", async () => {
    // Reached over the LAN there is no local server-config.json for the agent
    // to read, so the default would resolve to the agent's own 127.0.0.1 and
    // find nothing. Here the origin is the only correct answer.
    vi.stubGlobal("location", {
      hostname: "192.0.2.10",
      origin: "https://192.0.2.10:9537",
    });
    try {
      const wrapper = await mintedDialog();
      const text = wrapper.text();

      expect(text).toContain("--url https://192.0.2.10:9537");
      expect(wrapper.find(".cad-warn").exists()).toBe(true);
      expect(text).toMatch(/reach that address/i);
      const json = JSON.parse(wrapper.find(".cad-code--json").text());
      expect(json.mcpServers.pixlstash.args).toEqual([
        "--url",
        "https://192.0.2.10:9537",
      ]);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("registers the server for every directory, not just the current one", async () => {
    const wrapper = await mintedDialog();

    // Without `-s user` the CLI defaults to `-s local` and the server exists
    // only in the directory the command was run from. The agent then has no
    // PixlStash tools anywhere else and falls back to reading files.
    expect(wrapper.text()).toContain("claude mcp add -s user pixlstash");
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

  it("mints a full-access token and passes --allow-write only when asked", async () => {
    const wrapper = mount(ConnectAgentDialog, {
      props: { open: true },
      global: { stubs: { "v-icon": true } },
    });
    await wrapper.findAll('[role="radio"]')[1].trigger("click");
    // The cost is stated before anything is minted.
    expect(wrapper.text()).toMatch(/full owner control/i);
    await wrapper.find(".app-dialog__footer button:last-child").trigger("click");
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();

    expect(createToken).toHaveBeenCalledWith({
      description: "AI agent (MCP, read/write)",
      scope: "ALL",
    });
    // Step 2 says which kind of token this is, beside the blocks copied.
    expect(wrapper.find(".cad-minted").text()).toMatch(/full-access/i);
    // Still two blocks, both carrying the flag.
    expect(wrapper.findAll(".cad-code")).toHaveLength(2);
    expect(wrapper.text()).toContain("pixlstash-mcp --allow-write");
    const json = JSON.parse(wrapper.find(".cad-code--json").text());
    expect(json.mcpServers.pixlstash.args).toEqual(["--allow-write"]);
  });

  it("never mints a full-access token from Enter alone", async () => {
    const wrapper = mount(ConnectAgentDialog, {
      props: { open: true },
      global: { stubs: { "v-icon": true } },
    });
    await wrapper.findAll('[role="radio"]')[1].trigger("click");
    wrapper.findComponent({ name: "AppDialog" }).vm.$emit("accept");
    await wrapper.vm.$nextTick();
    expect(createToken).not.toHaveBeenCalled();
  });

  it("leaves the read-only configuration without the flag", async () => {
    const wrapper = await mintedDialog();
    expect(wrapper.find(".cad-minted").text()).toMatch(/read-only/i);
    expect(wrapper.text()).not.toContain("--allow-write");
    const json = JSON.parse(wrapper.find(".cad-code--json").text());
    expect(json.mcpServers.pixlstash.args).toBeUndefined();
  });

  it("shows no token and an error when minting fails", async () => {
    createToken.mockRejectedValue(new Error("nope"));
    const wrapper = mount(ConnectAgentDialog, {
      props: { open: true },
      global: { stubs: { "v-icon": true } },
    });
    await wrapper.find(".app-dialog__footer button:last-child").trigger("click");
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".cad-error").exists()).toBe(true);
    expect(wrapper.findAll(".cad-code")).toHaveLength(0);
  });
});
