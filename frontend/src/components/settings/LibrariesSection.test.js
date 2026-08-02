// Settings › Libraries.
//
// The behaviours worth pinning are the ones a user would only discover by
// hitting them: that a remote session is told why it cannot switch rather than
// being left with a dead button, that a failed switch says the session stayed
// put, and that the pane teaches the CLI, since in this release the command
// line is the only way to add a library.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { nextTick } from "vue";

import LibrariesSection from "./LibrariesSection.vue";
import { listLibraries, setActiveLibrary } from "../../api/libraries";

vi.mock("../../api/libraries", () => ({
  listLibraries: vi.fn(),
  setActiveLibrary: vi.fn(),
}));

const confirmMock = vi.fn();
vi.mock("../../composables/useConfirm", () => ({
  useConfirm: () => ({ confirm: confirmMock }),
}));

vi.mock("../../utils/clipboard", () => ({ copyText: vi.fn() }));

const LOCAL_RESPONSE = {
  libraries: [
    {
      uuid: "uuid-a",
      name: "Family Photos",
      is_active: true,
      is_reachable: true,
      path: "/home/me/Pictures",
    },
    {
      uuid: "uuid-b",
      name: "Client work",
      is_active: false,
      is_reachable: true,
      path: "/mnt/work/client",
    },
  ],
  can_manage: true,
  in_docker: false,
  cli_hint: "pixlstash-cli libraries list",
};

function mountPane() {
  return mount(LibrariesSection, {
    props: { open: true },
    global: {
      stubs: {
        VIcon: true,
        VProgressCircular: true,
        AppButton: {
          props: ["disabled", "loading"],
          template:
            '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
        },
      },
    },
  });
}

async function settle(wrapper) {
  await nextTick();
  await nextTick();
  await nextTick();
  return wrapper;
}

beforeEach(() => {
  vi.clearAllMocks();
  listLibraries.mockResolvedValue(structuredClone(LOCAL_RESPONSE));
  confirmMock.mockResolvedValue(true);
  setActiveLibrary.mockResolvedValue({ status: "ok" });
});

describe("listing", () => {
  it("shows every library and marks the active one", async () => {
    const wrapper = await settle(mountPane());

    expect(wrapper.text()).toContain("Family Photos");
    expect(wrapper.text()).toContain("Client work");
    expect(wrapper.text()).toContain("Active");
  });

  it("offers no Switch on the active library", async () => {
    const wrapper = await settle(mountPane());

    const rows = wrapper.findAll(".library-row");
    expect(rows[0].find("button").exists()).toBe(false);
    expect(rows[1].find("button").exists()).toBe(true);
  });

  it("shows the folder when the server sent one", async () => {
    const wrapper = await settle(mountPane());
    expect(wrapper.text()).toContain("/home/me/Pictures");
  });

  it("renders without a path when the server omitted it", async () => {
    // A remote session: the server sends no paths, and the pane must not show
    // an empty line where one would be.
    listLibraries.mockResolvedValue({
      libraries: [
        {
          uuid: "uuid-a",
          name: "Family Photos",
          is_active: true,
          is_reachable: true,
        },
      ],
      can_manage: false,
      in_docker: false,
      cli_hint: null,
    });

    const wrapper = await settle(mountPane());

    expect(wrapper.text()).toContain("Family Photos");
    expect(wrapper.find(".library-row__path").exists()).toBe(false);
  });

  it("marks an unreachable library rather than hiding it", async () => {
    listLibraries.mockResolvedValue({
      libraries: [
        { uuid: "a", name: "Active one", is_active: true, is_reachable: true },
        {
          uuid: "b",
          name: "On a drive",
          is_active: false,
          is_reachable: false,
          path: "/mnt/external",
        },
      ],
      can_manage: true,
      in_docker: false,
      cli_hint: "pixlstash-cli libraries list",
    });

    const wrapper = await settle(mountPane());

    expect(wrapper.text()).toContain("Not found");
    expect(wrapper.text()).toContain("On a drive");
    const rows = wrapper.findAll(".library-row");
    expect(rows[1].find("button").attributes("disabled")).toBeDefined();
  });

  it("surfaces a listing failure instead of showing an empty pane", async () => {
    listLibraries.mockRejectedValue({
      response: { data: { detail: "nope" } },
    });

    const wrapper = await settle(mountPane());

    expect(wrapper.find('[role="alert"]').text()).toContain("nope");
  });
});

describe("the remote session", () => {
  it("explains in visible text why switching is unavailable", async () => {
    listLibraries.mockResolvedValue({
      ...structuredClone(LOCAL_RESPONSE),
      can_manage: false,
      cli_hint: null,
    });

    const wrapper = await settle(mountPane());

    // Visible text, not a tooltip: a disabled control has to explain itself
    // somewhere a keyboard or screen-reader user will reach.
    expect(wrapper.text()).toContain("allow_remote_host_ops");
    expect(
      wrapper.findAll(".library-row")[1].find("button").attributes("disabled"),
    ).toBeDefined();
  });
});

describe("switching", () => {
  it("asks before switching and says the app will reload", async () => {
    const wrapper = await settle(mountPane());

    await wrapper.findAll(".library-row")[1].find("button").trigger("click");
    await settle(wrapper);

    expect(confirmMock).toHaveBeenCalled();
    expect(confirmMock.mock.calls[0][0].message).toContain("reload");
  });

  it("does nothing when the confirm is declined", async () => {
    confirmMock.mockResolvedValue(false);
    const wrapper = await settle(mountPane());

    await wrapper.findAll(".library-row")[1].find("button").trigger("click");
    await settle(wrapper);

    expect(setActiveLibrary).not.toHaveBeenCalled();
  });

  it("sends the uuid, never a row id", async () => {
    const wrapper = await settle(mountPane());

    await wrapper.findAll(".library-row")[1].find("button").trigger("click");
    await settle(wrapper);

    expect(setActiveLibrary).toHaveBeenCalledWith("uuid-b");
  });

  it("says the session stayed put when a switch fails", async () => {
    setActiveLibrary.mockRejectedValue({
      response: { data: { detail: "Could not open it. Nothing has changed." } },
    });
    const wrapper = await settle(mountPane());

    await wrapper.findAll(".library-row")[1].find("button").trigger("click");
    await settle(wrapper);

    expect(wrapper.find('[role="alert"]').text()).toContain(
      "Nothing has changed",
    );
  });
});

describe("teaching the CLI", () => {
  it("shows the exact command for this deployment", async () => {
    const wrapper = await settle(mountPane());
    expect(wrapper.text()).toContain("pixlstash-cli libraries list");
  });

  it("lists the verbs and promises detach keeps files", async () => {
    const wrapper = await settle(mountPane());

    expect(wrapper.text()).toContain("attach");
    expect(wrapper.text()).toContain("detach");
    expect(wrapper.text()).toContain("No files are removed");
  });

  it("falls back to instructions when the server withheld the command", async () => {
    listLibraries.mockResolvedValue({
      ...structuredClone(LOCAL_RESPONSE),
      can_manage: false,
      cli_hint: null,
    });

    const wrapper = await settle(mountPane());

    expect(wrapper.text()).toContain("Run it on the machine hosting PixlStash");
  });

  it("mentions container paths only in Docker", async () => {
    const wrapper = await settle(mountPane());
    expect(wrapper.text()).not.toContain("inside the container");

    listLibraries.mockResolvedValue({
      ...structuredClone(LOCAL_RESPONSE),
      in_docker: true,
    });
    const docker = await settle(mountPane());
    expect(docker.text()).toContain("inside the container");
  });
});
