// The title bar (#2158): one strip in every workspace. What is pinned here is
// what the issue makes a must - only the lit segment changes on a switch, the
// switch is a nav with `aria-current`, the old routes still answer - plus the
// library picker's menu and the read-only rule the sidebar rows used to carry.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { reactive } from "vue";
import { createMemoryHistory, createRouter } from "vue-router";

vi.mock("vuetify/components", () => ({
  VIcon: { name: "v-icon", template: "<i><slot /></i>" },
  // Both slots inline: what a menu offers matters here, not that Vuetify can
  // position it.
  VMenu: {
    name: "v-menu",
    template: '<div><slot name="activator" :props="{}" /><slot /></div>',
  },
  VTooltip: {
    name: "VTooltip",
    props: ["text"],
    setup:
      (_p, { slots }) =>
      () =>
        slots.activator?.({ props: {} }),
  },
}));

vi.mock("../composables/useVersionCheck", () => ({
  useVersionCheck: () => ({
    latestVersion: { value: "" },
    latestVersionUrl: { value: "" },
    latestSecurityLevel: { value: "" },
    updateAvailable: { value: false },
    updateDismissed: { value: false },
    isHighSecurity: { value: false },
    securityUpdateTitle: { value: "" },
    dismissUpdateAlert: vi.fn(),
  }),
}));

vi.mock("../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return {
    isReadOnly: ref(false),
    onSessionReset: () => () => {},
    apiClient: { get: vi.fn(), post: vi.fn() },
  };
});

vi.mock("../api/libraries", () => ({
  listLibraries: vi.fn(),
  setActiveLibrary: vi.fn(),
}));

const confirmMock = vi.fn();
vi.mock("../composables/useConfirm", () => ({
  useConfirm: () => ({ confirm: confirmMock }),
}));

vi.mock("../utils/reloadPage", () => ({ reloadPage: vi.fn() }));

const tasks = reactive({ activeCount: 0, hasActiveTasks: false });
vi.mock("../stores/useTasksStore", () => ({ useTasksStore: () => tasks }));

vi.mock("./panels/TasksPanel.vue", () => ({
  default: { template: '<div class="tasks-panel-stub"></div>' },
}));

import { listLibraries, setActiveLibrary } from "../api/libraries";
import { useLibrariesStore } from "../stores/useLibrariesStore";
import { useModelShelfStore } from "../stores/useModelShelfStore";
import { useFolderMappingStore } from "../stores/useFolderMappingStore";
import { useSidebarStore } from "../stores/useSidebarStore";
import { isReadOnly } from "../utils/apiClient";
import TitleBar from "./TitleBar.vue";
import Tooltip from "./widgets/Tooltip.vue";

const GB = 1024 ** 3;

const LIBRARIES = [
  {
    uuid: "a",
    name: "Family Photos",
    is_active: true,
    is_reachable: true,
    total_bytes: 1000 * GB,
    free_bytes: 210 * GB,
  },
  {
    uuid: "b",
    name: "Archive",
    is_active: false,
    is_reachable: true,
    total_bytes: 1000 * GB,
    free_bytes: 650 * GB,
  },
  {
    uuid: "c",
    name: "Client shoots",
    is_active: false,
    is_reachable: false,
    total_bytes: null,
    free_bytes: null,
  },
];

function makeRouter() {
  const page = { template: "<div />" };
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", name: "all-pictures", component: page },
      { path: "/set/:id", name: "set", component: page },
      { path: "/scrapheap", name: "scrapheap", component: page },
      { path: "/workflows", name: "workflows", component: page },
      { path: "/models", name: "models", component: page },
      { path: "/models/runs", name: "models-runs", component: page },
    ],
  });
}

let router;
let originalDesktop;

async function mountBar(path = "/", props = {}, options = {}) {
  await router.push(path);
  await router.isReady();
  const wrapper = mount(TitleBar, {
    ...options,
    props: { activeLibraryName: "Family Photos", ...props },
    global: {
      plugins: [router],
      stubs: { WordmarkLogo: { template: "<span>PixlStash</span>" } },
    },
  });
  await flushPromises();
  return wrapper;
}

function segments(wrapper) {
  return wrapper.find('nav[aria-label="Workspace"]').findAll("a, button");
}

function segment(wrapper, label) {
  return segments(wrapper).find((el) => el.text().includes(label));
}

/** Markup with the per-mount tooltip ids numbered out. */
function markup(wrapper) {
  return wrapper.html().replace(/app-tooltip-desc-\d+/g, "app-tooltip-desc");
}

/** The bar's markup with the switch's lit state taken out. */
function chromeWithoutLitSegment(wrapper) {
  return markup(wrapper)
    .replace(/ aria-current="page"/g, "")
    .replace(/ titlebar-ws--current/g, "");
}

beforeEach(() => {
  setActivePinia(createPinia());
  router = makeRouter();
  isReadOnly.value = false;
  tasks.activeCount = 0;
  tasks.hasActiveTasks = false;
  confirmMock.mockReset().mockResolvedValue(true);
  setActiveLibrary.mockReset().mockResolvedValue({ status: "ok" });
  listLibraries.mockReset().mockResolvedValue({
    libraries: LIBRARIES,
    can_manage: true,
  });
  originalDesktop = window.pixlstashDesktop;
  window.pixlstashDesktop = undefined;
});

afterEach(() => {
  window.pixlstashDesktop = originalDesktop;
  vi.restoreAllMocks();
});

async function loadLibraries(canManage = true) {
  listLibraries.mockResolvedValue({
    libraries: LIBRARIES,
    can_manage: canManage,
  });
  await useLibrariesStore().refresh();
}

describe("the workspace switch", () => {
  it("is a nav whose current workspace carries aria-current", async () => {
    const wrapper = await mountBar("/");
    const lit = segments(wrapper).filter(
      (el) => el.attributes("aria-current") === "page",
    );
    expect(lit.map((el) => el.text())).toEqual(["Media"]);
    // Real links, so Tab reaches them and they can open in a new tab.
    expect(segments(wrapper).map((el) => el.element.tagName)).toEqual([
      "A",
      "A",
      "A",
    ]);
  });

  it("routes to today's paths, so bookmarks still land", async () => {
    const wrapper = await mountBar("/");
    expect(segments(wrapper).map((el) => el.attributes("href"))).toEqual([
      "/",
      "/workflows",
      "/models",
    ]);
  });

  it.each([
    ["/set/3", "Media"],
    ["/scrapheap", "Media"],
    ["/workflows", "Workflows"],
    ["/models", "Models"],
    // The shelf's second route is still the Models workspace.
    ["/models/runs", "Models"],
  ])("lights %s as %s", async (path, label) => {
    const wrapper = await mountBar(path);
    expect(segment(wrapper, label).attributes("aria-current")).toBe("page");
    expect(
      segments(wrapper).filter((el) => el.attributes("aria-current")),
    ).toHaveLength(1);
  });

  it("navigates from one workspace to another", async () => {
    const wrapper = await mountBar("/set/3");
    await segment(wrapper, "Models").trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.path).toBe("/models");
  });

  it("leaves the current workspace where it is when its segment is pressed", async () => {
    // Media names where you are; it is not a reset to All Pictures.
    const wrapper = await mountBar("/set/3");
    await segment(wrapper, "Media").trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.path).toBe("/set/3");
  });

  it.each([
    ["/set/3", "Workflows", "/set/3"],
    ["/scrapheap", "Models", "/scrapheap"],
    // A lightbox open over the grid is not where you were.
    ["/set/3?overlay=12&from=/workflows&review=board", "Workflows", "/set/3"],
  ])("returns Media to %s after a visit to %s", async (start, away, back) => {
    const wrapper = await mountBar(start);
    await segment(wrapper, away).trigger("click");
    await flushPromises();
    expect(segment(wrapper, "Media").attributes("href")).toBe(back);
    await segment(wrapper, "Media").trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.fullPath).toBe(back);
  });

  it.each([
    ["/models/runs", "/models/runs"],
    // A training run's own page is where you were (#2118).
    ["/models/runs?run=7", "/models/runs?run=7"],
    ["/models/runs?folder-run=lora-a", "/models/runs?folder-run=lora-a"],
    // Only boot reads a share session's `?token=`, so the link keeps it.
    [
      "/models/runs?run=7&token=example-share",
      "/models/runs?run=7&token=example-share",
    ],
    // A Recipe chip's one-shot jump to a model is not.
    ["/models?model=4", "/models"],
  ])("returns Models from %s to %s", async (start, back) => {
    const wrapper = await mountBar(start);
    await segment(wrapper, "Workflows").trigger("click");
    await flushPromises();
    expect(segment(wrapper, "Models").attributes("href")).toBe(back);
    await segment(wrapper, "Models").trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.fullPath).toBe(back);
  });

  it("changes nothing in the bar but the lit segment", async () => {
    // jsdom has no layout, so "moves no control by a pixel" is pinned as its
    // cause: no element, class or text in the strip depends on the workspace.
    await loadLibraries();
    tasks.activeCount = 2;
    tasks.hasActiveTasks = true;
    const shots = [];
    for (const path of ["/", "/workflows", "/models"]) {
      const wrapper = await mountBar(path);
      shots.push(chromeWithoutLitSegment(wrapper));
      wrapper.unmount();
    }
    expect(shots[1]).toBe(shots[0]);
    expect(shots[2]).toBe(shots[0]);
  });

  it("marks new models on the Models segment", async () => {
    const wrapper = await mountBar("/");
    expect(segment(wrapper, "Models").find(".titlebar-ws-news").exists()).toBe(
      false,
    );
    useModelShelfStore().unseenArrivals = true;
    await flushPromises();
    const models = segment(wrapper, "Models");
    expect(models.find(".titlebar-ws-news").exists()).toBe(true);
    expect(models.text()).toContain("new models arrived");
  });
});

describe("a read-only session", () => {
  it("sees Workflows and Models, inert and explained", async () => {
    isReadOnly.value = true;
    const wrapper = await mountBar("/", { activeLibraryName: "" });

    for (const [label, hint] of [
      [
        "Workflows",
        "The workflow library is only available in your own library",
      ],
      ["Models", "The model shelf is only available in your own library"],
    ]) {
      const locked = segment(wrapper, label);
      expect(locked.element.tagName).toBe("BUTTON");
      expect(locked.attributes("aria-disabled")).toBe("true");
      // `aria-disabled`, not the native attribute: still tabbable, so a
      // keyboard user reaches the explanation.
      expect(locked.attributes("disabled")).toBeUndefined();
      expect(locked.findComponent(Tooltip).props("text")).toBe(hint);
      await locked.trigger("click");
      await flushPromises();
      expect(router.currentRoute.value.path).toBe("/");
    }
    expect(segment(wrapper, "Media").attributes("aria-current")).toBe("page");
  });

  it("gets neither the library picker nor the tasks pill", async () => {
    isReadOnly.value = true;
    const wrapper = await mountBar("/", { activeLibraryName: "" });
    expect(wrapper.find('[data-testid="titlebar-library"]').exists()).toBe(
      false,
    );
    expect(wrapper.find('[data-testid="titlebar-tasks"]').exists()).toBe(false);
    // Settings stays: a share visitor still has preferences.
    expect(wrapper.find('button[aria-label="Settings"]').exists()).toBe(true);
  });

  it("keeps its share token on every segment's link", async () => {
    // Only boot reads `?token=`, so a link without it opens a session that
    // 401s on reload or in a new tab.
    isReadOnly.value = true;
    const wrapper = await mountBar("/set/3?token=example-share", {
      activeLibraryName: "",
    });
    expect(segment(wrapper, "Media").attributes("href")).toBe(
      "/set/3?token=example-share",
    );
    isReadOnly.value = false;
    const owner = await mountBar("/set/3?token=example-share");
    expect(segments(owner).map((el) => el.attributes("href"))).toEqual([
      "/set/3?token=example-share",
      "/workflows?token=example-share",
      "/models?token=example-share",
    ]);
  });

  it("is the only session the switch refuses", async () => {
    // The positive control: an owner's Workflows and Models are links.
    const wrapper = await mountBar("/");
    expect(segment(wrapper, "Workflows").element.tagName).toBe("A");
    expect(segment(wrapper, "Models").element.tagName).toBe("A");
  });
});

describe("the library picker", () => {
  it("names the open library over its drive's fullness", async () => {
    await loadLibraries();
    const wrapper = await mountBar("/");
    const trigger = wrapper.find('[data-testid="titlebar-library"]');
    expect(trigger.text()).toContain("Family Photos");
    // 790 of 1000 GB used: amber from 75 %.
    const bar = trigger.find(".tlp-bar");
    expect(bar.attributes("data-state")).toBe("warning");
    expect(bar.find("span").attributes("style")).toContain("width: 79%");
    expect(trigger.attributes("aria-label")).toContain(
      "79% full, getting full",
    );
  });

  it("lists every library with its free space, then the shared note and the two verbs", async () => {
    await loadLibraries();
    const wrapper = await mountBar("/workflows");
    const menu = wrapper.find('[data-testid="titlebar-library-menu"]');
    const rows = menu.findAll('[role="menuitemradio"]');
    expect(rows.map((row) => row.attributes("data-uuid"))).toEqual([
      "a",
      "b",
      "c",
    ]);
    expect(rows[0].attributes("aria-checked")).toBe("true");
    expect(rows[1].text()).toContain("650.0 GB free");
    expect(rows[2].text()).toContain("Not found");
    expect(rows[2].attributes("aria-disabled")).toBe("true");
    expect(menu.text()).toContain(
      "Models and workflows are shared by all libraries.",
    );
    expect(
      menu.findAll('[role="menuitem"]').map((item) => item.text()),
    ).toEqual(["Add a library…", "Manage libraries…"]);
  });

  it("opens the same menu from every workspace", async () => {
    await loadLibraries();
    const menus = [];
    for (const path of ["/", "/workflows", "/models"]) {
      const wrapper = await mountBar(path);
      menus.push(markup(wrapper.find('[data-testid="titlebar-library-menu"]')));
      wrapper.unmount();
    }
    expect(menus[1]).toBe(menus[0]);
    expect(menus[2]).toBe(menus[0]);
  });

  it("switches through the same confirmation Settings uses", async () => {
    await loadLibraries();
    const wrapper = await mountBar("/");
    await wrapper.find('[data-uuid="b"]').trigger("click");
    await flushPromises();
    expect(confirmMock).toHaveBeenCalledTimes(1);
    expect(confirmMock.mock.calls[0][0].title).toBe("Switch to Archive?");
    expect(setActiveLibrary).toHaveBeenCalledWith("b");
  });

  it("switches nothing when the confirmation is declined, or for an unplugged drive", async () => {
    await loadLibraries();
    confirmMock.mockResolvedValue(false);
    const wrapper = await mountBar("/");
    await wrapper.find('[data-uuid="b"]').trigger("click");
    await wrapper.find('[data-uuid="c"]').trigger("click");
    await flushPromises();
    expect(confirmMock).toHaveBeenCalledTimes(1);
    expect(setActiveLibrary).not.toHaveBeenCalled();
  });

  it("gives the keyboard back to the picker when the switch is declined", async () => {
    await loadLibraries();
    confirmMock.mockResolvedValue(false);
    const wrapper = await mountBar("/", {}, { attachTo: document.body });
    const row = wrapper.find('[data-uuid="b"]');
    row.element.focus();
    await row.trigger("click");
    await flushPromises();
    expect(document.activeElement).toBe(
      wrapper.find('[data-testid="titlebar-library"]').element,
    );
    wrapper.unmount();
  });

  it("says why a row cannot be chosen", async () => {
    await loadLibraries(false);
    const wrapper = await mountBar("/");
    const tip = (uuid) =>
      wrapper.find(`[data-uuid="${uuid}"]`).findComponent(Tooltip);
    expect(tip("c").props("text")).toBe("Its drive is not connected");
    expect(tip("b").props("text")).toContain("only available on the machine");
    // The open library is not refused, so it explains nothing.
    expect(tip("a").exists()).toBe(false);
  });

  it("offers a remote owner no switch and no Add, but still Manage", async () => {
    await loadLibraries(false);
    const wrapper = await mountBar("/");
    const menu = wrapper.find('[data-testid="titlebar-library-menu"]');
    expect(menu.find('[data-uuid="b"]').attributes("aria-disabled")).toBe(
      "true",
    );
    await menu.find('[data-uuid="b"]').trigger("click");
    await flushPromises();
    expect(confirmMock).not.toHaveBeenCalled();
    expect(
      menu.findAll('[role="menuitem"]').map((item) => item.text()),
    ).toEqual(["Manage libraries…"]);
  });

  it("opens Add a library and Manage libraries", async () => {
    await loadLibraries();
    const wrapper = await mountBar("/");
    const [add, manage] = wrapper
      .find('[data-testid="titlebar-library-menu"]')
      .findAll('[role="menuitem"]');
    await add.trigger("click");
    expect(useFolderMappingStore().wizardOpen).toBe(true);
    await manage.trigger("click");
    expect(wrapper.emitted("open-settings")).toEqual([["libraries"]]);
  });
});

describe("the tasks pill", () => {
  it("counts what is running, with the amber dot only while something is", async () => {
    const wrapper = await mountBar("/");
    const pill = wrapper.find('[data-testid="titlebar-tasks"]');
    expect(pill.text()).toContain("Tasks");
    expect(pill.find(".ttp-dot").exists()).toBe(false);

    tasks.activeCount = 3;
    tasks.hasActiveTasks = true;
    await flushPromises();
    expect(pill.text()).toContain("3 tasks");
    expect(pill.find(".ttp-dot").exists()).toBe(true);
    expect(pill.attributes("aria-label")).toBe(
      "3 active tasks. Open the task list",
    );
  });

  it("holds today's task list", async () => {
    const wrapper = await mountBar("/");
    const panel = wrapper.find('[data-testid="titlebar-tasks-panel"]');
    expect(panel.attributes("role")).toBe("dialog");
    expect(panel.find(".tasks-panel-stub").exists()).toBe(true);
  });

  it("opens when the lightbox hands it a deep link to the task list", async () => {
    // The lightbox's rail has no Tasks tab (#2163), so a notice's *Show* goes
    // here while a picture is open.
    const wrapper = await mountBar("/");
    const pill = () => wrapper.find('[data-testid="titlebar-tasks"]');
    expect(pill().classes()).not.toContain("bar-btn--open");
    useSidebarStore().tasksPillRequest += 1;
    await flushPromises();
    expect(pill().classes()).toContain("bar-btn--open");
  });
});

describe("the rest of the strip", () => {
  it("opens Settings", async () => {
    const wrapper = await mountBar("/");
    await wrapper.find('button[aria-label="Settings"]').trigger("click");
    expect(wrapper.emitted("open-settings")).toEqual([[]]);
  });

  it("renders in a plain browser, without window controls", async () => {
    // It used to render nothing here. The workspace switch lives in it now,
    // and a browser has no other way to Workflows and Models.
    const wrapper = await mountBar("/");
    expect(wrapper.find(".titlebar").exists()).toBe(true);
    expect(wrapper.find('button[aria-label="Close"]').exists()).toBe(false);
  });

  // The platform is pinned rather than read off the host, so each case runs
  // the same on every machine instead of skipping on the one it describes.
  it("draws the window controls in the desktop shell", async () => {
    vi.spyOn(navigator, "platform", "get").mockReturnValue("Linux x86_64");
    window.pixlstashDesktop = { windowClose: vi.fn() };
    const wrapper = await mountBar("/");
    await wrapper.find('button[aria-label="Close"]').trigger("click");
    expect(window.pixlstashDesktop.windowClose).toHaveBeenCalled();
    wrapper.unmount();
  });

  it("leaves the window controls to macOS's own traffic lights", async () => {
    vi.spyOn(navigator, "platform", "get").mockReturnValue("MacIntel");
    window.pixlstashDesktop = { windowClose: vi.fn() };
    const wrapper = await mountBar("/");
    expect(wrapper.find('button[aria-label="Close"]').exists()).toBe(false);
    expect(wrapper.find(".titlebar--mac").exists()).toBe(true);
    wrapper.unmount();
  });
});
