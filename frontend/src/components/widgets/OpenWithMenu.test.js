// Open with ▸ in the grid and lightbox context menus: where the row shows, what
// the submenu lists, and what a click emits. The server enforces loopback on
// both routes; these tests pin the client half, which keeps a dead row away
// from a LAN viewer and puts the apps that can save an edit back first.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { setActivePinia, createPinia } from "pinia";
import { flushPromises, mount } from "@vue/test-utils";

const { readOnly, listOpenWithApps } = vi.hoisted(() => ({
  readOnly: { value: false },
  listOpenWithApps: vi.fn(),
}));

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  const isReadOnly = ref(false);
  readOnly.ref = isReadOnly;
  return {
    API_BASE_URL: "/api/v1",
    apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
    isReadOnly,
    onSessionReset: () => () => {},
    sessionContext: ref(null),
  };
});

vi.mock("../../api/pictures", () => ({ listOpenWithApps }));

import ImageGridContextMenu from "./ImageGridContextMenu.vue";
import { clearOpenWithCache } from "./OpenWithMenu.vue";

const REQUIRED = { scrapheapPicturesId: "SCRAPHEAP", backendUrl: "http://x" };
const PNG = { id: 42, format: "png", faces: [], mediaKind: "picture" };
const stubs = {
  global: {
    stubs: {
      "v-icon": true,
      "v-progress-circular": true,
      Tooltip: true,
      teleport: true,
    },
  },
};

function mountMenu(props = {}) {
  return mount(ImageGridContextMenu, {
    props: {
      ...REQUIRED,
      visible: true,
      selectedImageIds: [42],
      selectedCharacter: "ALL",
      contextImage: PNG,
      ...props,
    },
    ...stubs,
  });
}

function openWithWrap(wrapper) {
  return wrapper.find('[data-group="open-with"]');
}

function rowText(el) {
  if (el.classes("ctx-sep")) return "---";
  if (el.classes("ctx-label")) return `# ${el.text().trim()}`;
  const name = el.find(".ctx-label-text");
  const pill = el.find(".ctx-default-pill").exists() ? " default" : "";
  return (name.exists() ? name.text() : el.text()).trim() + pill;
}

function rowTexts(wrapper) {
  return openWithWrap(wrapper).findAll(".ctx-submenu > *").map(rowText);
}

let originalLocation;

function setHostname(hostname) {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...originalLocation, hostname },
  });
}

beforeEach(() => {
  setActivePinia(createPinia());
  clearOpenWithCache();
  listOpenWithApps.mockReset();
  readOnly.ref.value = false;
  originalLocation = window.location;
  delete window.pixlstashDesktop;
});

afterEach(() => {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: originalLocation,
  });
  delete window.pixlstashDesktop;
});

describe("where Open with shows", () => {
  it("shows in the desktop shell and over loopback, never to a LAN viewer", () => {
    setHostname("192.168.0.1");
    expect(openWithWrap(mountMenu()).exists()).toBe(false);
    expect(openWithWrap(mountMenu({ lightboxMode: true })).exists()).toBe(false);

    window.pixlstashDesktop = {};
    expect(openWithWrap(mountMenu()).exists()).toBe(true);
    expect(openWithWrap(mountMenu({ lightboxMode: true })).exists()).toBe(true);

    delete window.pixlstashDesktop;
    setHostname("localhost");
    expect(openWithWrap(mountMenu()).exists()).toBe(true);
  });

  it("is absent for a video, a read-only session and a multi-selection", () => {
    setHostname("127.0.0.1");
    const video = { ...PNG, format: "mp4", mediaKind: "video" };
    expect(openWithWrap(mountMenu({ contextImage: video })).exists()).toBe(false);
    expect(
      openWithWrap(mountMenu({ contextImage: video, lightboxMode: true })).exists(),
    ).toBe(false);
    expect(openWithWrap(mountMenu({ selectedImageIds: [42, 43] })).exists()).toBe(
      false,
    );
    readOnly.ref.value = true;
    expect(openWithWrap(mountMenu()).exists()).toBe(false);
  });

  it("sits between Share image and Move to library in the grid", () => {
    setHostname("localhost");
    const wrapper = mountMenu();
    const order = wrapper
      .findAll(".image-ctx-menu > .ctx-item, .image-ctx-menu > .ctx-submenu-wrap")
      .map((el) => el.text().replace(/\s+/g, " ").trim());
    const share = order.findIndex((t) => t.startsWith("Share image"));
    expect(order[share + 1]).toMatch(/^Open with/);
  });
});

describe("the Open with submenu", () => {
  beforeEach(() => setHostname("localhost"));

  it("lists editors first, then the rest muted under their own label", async () => {
    let answer;
    listOpenWithApps.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    const listing = {
      apps: [
        { id: "gimp", name: "GNU Image Manipulation Program", default: false, editor: true },
        { id: "krita", name: "Krita", default: false, editor: true },
        { id: "viewer", name: "Image Viewer", default: true, editor: false },
        { id: "firefox", name: "Firefox", default: false, editor: false },
      ],
      other: true,
    };
    const wrapper = mountMenu();
    // Not fetched until the submenu is first hovered.
    expect(listOpenWithApps).not.toHaveBeenCalled();
    await openWithWrap(wrapper).trigger("mouseenter");
    expect(rowTexts(wrapper)).toEqual(["Looking for apps…"]);
    answer(listing);
    await flushPromises();

    expect(listOpenWithApps).toHaveBeenCalledWith(42);
    expect(rowTexts(wrapper)).toEqual([
      "# Editors",
      "GNU Image Manipulation Program",
      "Krita",
      "---",
      "# Other apps",
      "Image Viewer default",
      "Firefox",
      "---",
      "Other app…",
    ]);
    const muted = openWithWrap(wrapper).findAll(".ctx-item--muted").map(rowText);
    expect(muted).toEqual(["Image Viewer default", "Firefox"]);
  });

  it("drops the labels when every app is a viewer, and caches per format", async () => {
    listOpenWithApps.mockResolvedValue({
      apps: [
        { id: "viewer", name: "Image Viewer", default: true, editor: false },
        { id: "firefox", name: "Firefox", default: false, editor: false },
      ],
      other: false,
    });
    const first = mountMenu();
    await openWithWrap(first).trigger("mouseenter");
    await flushPromises();
    expect(rowTexts(first)).toEqual(["Image Viewer default", "Firefox"]);
    expect(openWithWrap(first).findAll(".ctx-item--muted")).toHaveLength(0);

    const second = mountMenu({ contextImage: { ...PNG, id: 43 } });
    await openWithWrap(second).trigger("mouseenter");
    await flushPromises();
    expect(rowTexts(second)).toEqual(["Image Viewer default", "Firefox"]);
    expect(listOpenWithApps).toHaveBeenCalledTimes(1);
  });

  it("says when there is nothing to open it with", async () => {
    listOpenWithApps.mockResolvedValue({ apps: [], other: false });
    const wrapper = mountMenu();
    await openWithWrap(wrapper).trigger("mouseenter");
    await flushPromises();
    expect(rowTexts(wrapper)).toEqual(["No apps found for this file type"]);
    expect(
      openWithWrap(wrapper).find(".ctx-submenu button").attributes("disabled"),
    ).toBeDefined();
  });

  it("emits open-with with the picture and the chosen app, or the chooser", async () => {
    listOpenWithApps.mockResolvedValue({
      apps: [{ id: "gimp", name: "GIMP", default: false, editor: true }],
      other: true,
    });
    const wrapper = mountMenu({ lightboxMode: true });
    await openWithWrap(wrapper).trigger("mouseenter");
    await flushPromises();
    const rows = openWithWrap(wrapper).findAll(".ctx-submenu button");
    await rows[0].trigger("click");
    expect(wrapper.emitted("open-with")[0]).toEqual([
      { pictureId: 42, appId: "gimp", appName: "GIMP" },
    ]);
    expect(wrapper.emitted("close")).toBeTruthy();

    await openWithWrap(wrapper).trigger("mouseenter");
    await openWithWrap(wrapper)
      .findAll(".ctx-submenu button")
      .at(-1)
      .trigger("click");
    expect(wrapper.emitted("open-with")[1]).toEqual([
      { pictureId: 42, other: true },
    ]);
  });
});
