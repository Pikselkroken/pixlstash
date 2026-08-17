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
import { createPinia, setActivePinia } from "pinia";

// The CLI commands live in a dialog, so the real VDialog would need Vuetify's
// defaults instance. Render the slot inline instead, matching
// LibrarySwitchOverlay.test.js.
vi.mock("vuetify/components", () => ({
  VCard: { name: "v-card", template: "<div><slot /></div>" },
  VDialog: { name: "v-dialog", template: "<div><slot /></div>" },
  VIcon: { name: "v-icon", template: "<i><slot /></i>" },
  VProgressCircular: { name: "v-progress-circular", template: "<i />" },
}));

import LibrariesSection from "./LibrariesSection.vue";
import { listLibraries, setActiveLibrary } from "../../api/libraries";
import { useLibrarySwitchStore } from "../../stores/useLibrariesStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { copyText } from "../../utils/clipboard";

vi.mock("../../api/libraries", () => ({
  listLibraries: vi.fn(),
  setActiveLibrary: vi.fn(),
  LIBRARIES_DOCUMENTATION_URL:
    "https://github.com/Pikselkroken/pixlstash#multiple-libraries",
}));

const confirmMock = vi.fn();
vi.mock("../../composables/useConfirm", () => ({
  useConfirm: () => ({ confirm: confirmMock }),
}));

vi.mock("../../utils/clipboard", () => ({ copyText: vi.fn() }));
vi.mock("../../utils/reloadPage", () => ({ reloadPage: vi.fn() }));

const LOCAL_RESPONSE = {
  libraries: [
    {
      uuid: "uuid-a",
      name: "Family Photos",
      is_active: true,
      is_reachable: true,
      path: "/home/me/Pictures",
      active_share_links: 2,
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

let pinia;

function mountPane() {
  return mount(LibrariesSection, {
    props: { open: true },
    global: {
      plugins: [pinia],
      stubs: {
        VIcon: true,
        VProgressCircular: true,
        AppButton: {
          props: ["disabled", "loading"],
          template:
            '<button :disabled="disabled" @click="$emit(\'click\', $event)"><slot /></button>',
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
  pinia = createPinia();
  setActivePinia(pinia);
  vi.clearAllMocks();
  listLibraries.mockResolvedValue(structuredClone(LOCAL_RESPONSE));
  confirmMock.mockResolvedValue(true);
  setActiveLibrary.mockResolvedValue({ status: "ok" });
  copyText.mockResolvedValue(true);
});

describe("listing", () => {
  it("shows every library and marks the active one", async () => {
    const wrapper = await settle(mountPane());

    expect(wrapper.text()).toContain("Family Photos");
    expect(wrapper.text()).toContain("Client work");
    expect(wrapper.text()).toContain("Active");
    expect(
      wrapper.findAll(".library-row")[0].find(".library-row__label").attributes(
        "title",
      ),
    ).toBe("Family Photos");
    expect(wrapper.find(".library-chip").classes()).not.toContain(
      "library-chip--warn",
    );
  });

  it("offers no Switch on the active library", async () => {
    const wrapper = await settle(mountPane());

    const rows = wrapper.findAll(".library-row");
    expect(rows[0].find(".library-row__action button").exists()).toBe(false);
    expect(rows[1].find(".library-row__action button").exists()).toBe(true);
  });

  it("shows the folder when the server sent one", async () => {
    const wrapper = await settle(mountPane());
    expect(wrapper.text()).toContain("/home/me/Pictures");
    const path = wrapper.find(".library-row__path");
    expect(path.attributes("aria-expanded")).toBe("false");
    await path.trigger("click");
    expect(path.attributes("aria-expanded")).toBe("true");
    expect(path.classes()).toContain("library-row__path--expanded");
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
    expect(
      rows[1].find(".library-row__action button").attributes("disabled"),
    ).toBeDefined();
    expect(rows[1].find(".library-chip--warn").exists()).toBe(true);
  });

  it("surfaces a listing failure instead of showing an empty pane", async () => {
    listLibraries.mockRejectedValue({
      response: { data: { detail: "nope" } },
    });

    const wrapper = await settle(mountPane());

    expect(wrapper.find('[role="alert"]').text()).toContain("nope");
    expect(wrapper.find('[role="alert"] button').text()).toBe("Retry");

    listLibraries.mockResolvedValue(structuredClone(LOCAL_RESPONSE));
    await wrapper.find('[role="alert"] button').trigger("click");
    await settle(wrapper);
    expect(wrapper.text()).toContain("Family Photos");
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
      wrapper
        .findAll(".library-row")[1]
        .find(".library-row__action button")
        .attributes("disabled"),
    ).toBeDefined();
  });
});

describe("switching", () => {
  it("asks before switching and says the app will reload", async () => {
    const wrapper = await settle(mountPane());

    await wrapper
      .findAll(".library-row")[1]
      .find(".library-row__action button")
      .trigger("click");
    await settle(wrapper);

    expect(confirmMock).toHaveBeenCalled();
    expect(confirmMock.mock.calls[0][0].message).toContain("reload");
    expect(confirmMock.mock.calls[0][0].warning).toContain(
      "2 share links point at Family Photos",
    );
  });

  it("does nothing when the confirm is declined", async () => {
    confirmMock.mockResolvedValue(false);
    const wrapper = await settle(mountPane());

    await wrapper
      .findAll(".library-row")[1]
      .find(".library-row__action button")
      .trigger("click");
    await settle(wrapper);

    expect(setActiveLibrary).not.toHaveBeenCalled();
  });

  it("sends the uuid, never a row id", async () => {
    const wrapper = await settle(mountPane());

    await wrapper
      .findAll(".library-row")[1]
      .find(".library-row__action button")
      .trigger("click");
    await settle(wrapper);

    expect(setActiveLibrary).toHaveBeenCalledWith("uuid-b");
  });

  it("says the session stayed put when a switch fails", async () => {
    setActiveLibrary.mockRejectedValue({
      response: { data: { detail: "Could not open it. Nothing has changed." } },
    });
    const wrapper = await settle(mountPane());

    await wrapper
      .findAll(".library-row")[1]
      .find(".library-row__action button")
      .trigger("click");
    await settle(wrapper);

    const switchStore = useLibrarySwitchStore();
    expect(switchStore.phase).toBe("failed");
    expect(switchStore.error).toContain("Nothing has changed");
    expect(switchStore.currentLibrary.name).toBe("Family Photos");
  });

  it("restores focus to the real Switch button after a failed switch", async () => {
    setActiveLibrary.mockRejectedValue({
      response: { data: { detail: "Could not open it." } },
    });
    const wrapper = mount(LibrariesSection, {
      props: { open: true },
      attachTo: document.body,
      global: {
        plugins: [pinia],
        stubs: {
          VIcon: true,
          VProgressCircular: true,
          AppButton: {
            props: ["disabled", "loading"],
            template:
              '<button :disabled="disabled" @click="$emit(\'click\', $event)"><slot /></button>',
          },
        },
      },
    });
    await settle(wrapper);
    const trigger = wrapper
      .findAll(".library-row")[1]
      .find(".library-row__action button");

    await trigger.trigger("click");
    await settle(wrapper);
    const switchStore = useLibrarySwitchStore();
    expect(switchStore.phase).toBe("failed");
    await switchStore.stayOnCurrent();
    expect(document.activeElement).toBe(trigger.element);
    wrapper.unmount();
  });

  it("does not POST until the share-link warning has been accepted", async () => {
    let resolveConfirm;
    confirmMock.mockReturnValue(
      new Promise((resolve) => {
        resolveConfirm = resolve;
      }),
    );
    const wrapper = await settle(mountPane());

    await wrapper
      .findAll(".library-row")[1]
      .find(".library-row__action button")
      .trigger("click");
    expect(confirmMock.mock.calls[0][0].warning).toContain("2 share links");
    expect(setActiveLibrary).not.toHaveBeenCalled();

    resolveConfirm(true);
    await settle(wrapper);
    expect(setActiveLibrary).toHaveBeenCalledWith("uuid-b");
  });
});

describe("teaching the CLI", () => {
  it("shows the exact command for this deployment", async () => {
    const wrapper = await settle(mountPane());
    expect(wrapper.text()).toContain("pixlstash-cli libraries list");
  });

  it("lists the verbs and promises detach keeps files", async () => {
    const wrapper = await settle(mountPane());

    expect(wrapper.text()).toContain("create <folder>");
    expect(wrapper.text()).toContain("attach <folder>");
    expect(wrapper.text()).toContain("detach <name>");
    expect(wrapper.text()).toContain("No files are removed");
  });

  it("keeps the commands behind a button so the pane stays short", async () => {
    // Four commands, each carrying an absolute interpreter path, buried the
    // list-and-switch flow this pane exists for. They are reference material,
    // so the pane offers a way in rather than spending its height on them.
    const wrapper = await settle(mountPane());

    const trigger = wrapper
      .findAll("button")
      .find((button) => button.text().includes("Show the commands"));
    expect(trigger, "the pane must offer a way into the commands").toBeTruthy();
    await trigger.trigger("click");
    await nextTick();

    expect(wrapper.text()).toContain("create <folder>");
  });

  it("falls back to instructions when the server withheld the command", async () => {
    listLibraries.mockResolvedValue({
      ...structuredClone(LOCAL_RESPONSE),
      can_manage: false,
      cli_hint: null,
    });

    const wrapper = await settle(mountPane());

    expect(wrapper.text()).toContain("Run it on the machine hosting PixlStash");
    expect(wrapper.find('a[href*="github.com/Pikselkroken/pixlstash"]').exists()).toBe(
      true,
    );
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

  it("copies each complete deployment-correct command", async () => {
    const wrapper = await settle(mountPane());
    const rows = wrapper.findAll(".libraries-cli");

    await rows[2].find("button").trigger("click");
    expect(copyText).toHaveBeenCalledWith(
      "pixlstash-cli libraries attach <folder>",
    );
    expect(rows[0].find(".libraries-cli__copy").exists()).toBe(true);
    expect(rows[2].find(".libraries-cli__copy").exists()).toBe(true);
  });

  it("confirms the copy only once the clipboard actually took it", async () => {
    const wrapper = await settle(mountPane());

    await wrapper.findAll(".libraries-cli")[0].find("button").trigger("click");
    await nextTick();

    expect(wrapper.findAll(".libraries-cli")[0].text()).toContain("Copied");
    expect(wrapper.find('[role="status"]').text()).toContain(
      "Copied pixlstash-cli libraries list",
    );
    expect(useNoticeStore().notices).toHaveLength(0);
  });

  // The clipboard refuses on an insecure context, and "Copied" then leaves the
  // user pasting whatever was there before.
  it("says how to copy by hand when the clipboard refuses", async () => {
    copyText.mockResolvedValue(false);
    const wrapper = await settle(mountPane());

    await wrapper.findAll(".libraries-cli")[0].find("button").trigger("click");
    await nextTick();

    expect(wrapper.findAll(".libraries-cli")[0].text()).not.toContain("Copied");
    expect(wrapper.find('[role="status"]').text()).toBe("");
    const notice = useNoticeStore().notices.at(-1);
    expect(notice.level).toBe("error");
    // Actionable: it says what to do instead, on both platforms.
    expect(notice.text).toContain("Ctrl+C");
    expect(notice.text).toContain("Command-C");
  });

  // A failure landing late must not wipe a copy that has since succeeded — the
  // same lie as the reported bug, pointing the other way.
  it("leaves a later successful copy alone when an earlier one fails", async () => {
    let failSlowCopy;
    copyText.mockImplementationOnce(
      () => new Promise((resolve) => (failSlowCopy = () => resolve(false))),
    );
    const wrapper = await settle(mountPane());
    const rows = wrapper.findAll(".libraries-cli");

    await rows[0].find("button").trigger("click");
    await rows[1].find("button").trigger("click");
    await nextTick();
    expect(rows[1].text()).toContain("Copied");

    failSlowCopy();
    await nextTick();
    await nextTick();

    expect(rows[1].text()).toContain("Copied");
    expect(wrapper.find('[role="status"]').text()).toContain(
      "pixlstash-cli libraries create <folder>",
    );
  });

  it("forgets the confirmation when the dialog closes", async () => {
    const wrapper = await settle(mountPane());
    // The stubbed dialog always renders its slot, so open it explicitly:
    // otherwise closing is not a change and the watcher never runs.
    await wrapper
      .findAll("button")
      .find((b) => b.text() === "Show the commands")
      .trigger("click");

    await wrapper.findAll(".libraries-cli")[0].find("button").trigger("click");
    await nextTick();
    await wrapper.findAll(".libraries-cli-dialog__actions button")[0].trigger(
      "click",
    );
    await nextTick();

    expect(wrapper.find('[role="status"]').text()).toBe("");
    expect(wrapper.findAll(".libraries-cli")[0].text()).not.toContain("Copied");
  });

  it("drops its feedback timer when the pane goes away", async () => {
    const setTimeoutSpy = vi.spyOn(window, "setTimeout");
    const clearTimeoutSpy = vi.spyOn(window, "clearTimeout");
    const wrapper = await settle(mountPane());

    await wrapper.findAll(".libraries-cli")[0].find("button").trigger("click");
    await nextTick();
    const timer = setTimeoutSpy.mock.results.at(-1).value;
    wrapper.unmount();

    expect(clearTimeoutSpy).toHaveBeenCalledWith(timer);
    setTimeoutSpy.mockRestore();
    clearTimeoutSpy.mockRestore();
  });

  it("only shows the one-library primer after a successful one-row response", async () => {
    listLibraries.mockRejectedValueOnce(new Error("offline"));
    const failed = await settle(mountPane());
    expect(failed.text()).not.toContain("You have one library");

    listLibraries.mockResolvedValue({
      ...structuredClone(LOCAL_RESPONSE),
      libraries: [structuredClone(LOCAL_RESPONSE.libraries[0])],
    });
    const one = await settle(mountPane());
    expect(one.text()).toContain("You have one library");
    expect(one.text()).toContain("attach <folder>");
  });
});
