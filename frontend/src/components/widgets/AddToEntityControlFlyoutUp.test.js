// The `placement="right"` flyout (Add to project/person/set ▸ in the grid
// context menu and the selection menu) opens upwards when its row is too near
// the bottom of the window, as every `.ctx-submenu` does (`v-submenu-fit`).

import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return {
    API_BASE_URL: "/api/v1",
    apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
    isReadOnly: ref(false),
    onSessionReset: () => () => {},
    sessionContext: ref(null),
  };
});
vi.mock("../../api/projects", () => ({
  listProjects: vi.fn(async () => []),
  getProjectMembership: vi.fn(async () => ({})),
}));

import AddToEntityControl from "./AddToEntityControl.vue";

const WINDOW_HEIGHT = 800;

/** Opens the flyout on a 32px row whose top is `rowTop`. */
async function openAt(rowTop) {
  const wrapper = mount(AddToEntityControl, {
    props: { type: "project", subjectIds: ["10"], placement: "right" },
    global: { stubs: { "v-icon": true, Tooltip: true, teleport: true } },
    attachTo: document.body,
  });
  wrapper.element.getBoundingClientRect = () => ({
    top: rowTop,
    bottom: rowTop + 32,
    left: 0,
    right: 200,
    width: 200,
    height: 32,
  });
  await wrapper.find(".ate-btn").trigger("click");
  await flushPromises();
  return wrapper.find(".ate-menu").element.style;
}

describe("the flyout near the bottom of the window", () => {
  const original = window.innerHeight;
  beforeAll(() => {
    window.innerHeight = WINDOW_HEIGHT;
  });
  afterAll(() => {
    window.innerHeight = original;
  });
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("hangs down from its row when there is room below", async () => {
    const style = await openAt(100);
    expect(style.bottom).toBe("");
    expect(style.maxHeight).toBe("688px");
  });

  it("opens upwards, its bottom level with the row's, when there is not", async () => {
    const style = await openAt(720);
    expect(style.top).toBe("auto");
    expect(style.bottom).toBe("0px");
    expect(style.maxHeight).toBe("740px");
  });
});
