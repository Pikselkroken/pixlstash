// The grid context menu's side of #759. The three Add-to controls own their own
// keyboard once open (AddToEntityControl.test.js covers that); the menu only has
// to let them be reached and then get out of the way:
//
// * its roving focus must include the `.ate-btn` triggers, which are not
//   `.ctx-item` - while it walked `.ctx-item` alone, arrow keys skipped Project,
//   Person and Set entirely and assignment was pointer-only;
// * ArrowRight must open the focused trigger's flyout and land in its search box;
// * its capture-phase Escape handler must exempt events from inside `.ate-menu`,
//   or the first Escape in that search box tore down the whole menu.
//
// The flyouts are REAL here, not stubbed: the point of these cases is the seam
// between the two components.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { nextTick } from "vue";
import { setActivePinia, createPinia } from "pinia";
import { mount, flushPromises } from "@vue/test-utils";

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return {
    API_BASE_URL: "/api/v1",
    apiClient: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
    isReadOnly: ref(false),
    onSessionReset: () => () => {},
    // An owner session, so the Project row renders.
    sessionContext: ref(null),
  };
});
vi.mock("../../api/snapshots", () => ({ hashCompareSnapshot: vi.fn() }));
vi.mock("../../api/characters", () => ({
  getCharacterName: vi.fn(),
  listCharacters: vi.fn().mockResolvedValue([{ id: 1, name: "Alice" }]),
  getCharacterMembership: vi.fn().mockResolvedValue({
    character_assignments: {},
    pictures_with_faces: [],
  }),
  addCharacterFaces: vi.fn(),
  removeCharacterFaces: vi.fn(),
}));
vi.mock("../../api/pictureSets", () => ({
  listPictureSets: vi
    .fn()
    .mockResolvedValue([{ id: 7, name: "Portraits", picture_count: 1 }]),
  getPictureSetMembership: vi.fn().mockResolvedValue({}),
  addPictureToSet: vi.fn(),
  removePictureFromSet: vi.fn(),
}));
vi.mock("../../api/projects", () => ({
  listProjects: vi.fn().mockResolvedValue([{ id: 4, name: "Shoot" }]),
  getProjectMembership: vi.fn().mockResolvedValue({
    project_assignments: {},
    unassigned_picture_ids: [],
  }),
}));

import ImageGridContextMenu from "./ImageGridContextMenu.vue";

const REQUIRED = {
  allPicturesId: "ALL",
  unassignedPicturesId: "UNASSIGNED",
  scrapheapPicturesId: "SCRAPHEAP",
  backendUrl: "http://x",
};

beforeEach(() => {
  setActivePinia(createPinia());
});

async function mountMenu() {
  const wrapper = mount(ImageGridContextMenu, {
    props: {
      ...REQUIRED,
      visible: true,
      selectedImageIds: ["10", "11"],
      selectedCharacter: "ALL",
    },
    attachTo: document.body,
    // Teleport stays real so the menu lands in <body> like it does in the app;
    // the wrapper still queries it, and focus assertions need it in the document.
    global: { stubs: { "v-icon": true } },
  });
  await flushPromises();
  return wrapper;
}

const menuEl = () => document.querySelector(".image-ctx-menu");
const press = async (el, key) => {
  el.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
  await nextTick();
};

describe("grid context menu keyboard reach into the Add-to controls", () => {
  it("includes the Add-to triggers in its roving focus order", async () => {
    const wrapper = await mountMenu();
    const triggers = Array.from(menuEl().querySelectorAll(".ate-btn"));
    expect(triggers).toHaveLength(3); // Project, Person, Set

    // Walking down from the top of the menu reaches all three. Before the fix
    // the roving query was `.ctx-item` only and none of them were reachable.
    const seen = new Set();
    let cursor = menuEl();
    for (let i = 0; i < 60; i += 1) {
      await press(cursor, "ArrowDown");
      cursor = document.activeElement;
      if (triggers.includes(cursor)) seen.add(cursor);
      if (seen.size === triggers.length) break;
    }
    expect(seen.size).toBe(3);
    wrapper.unmount();
  });

  it("opens the focused trigger's flyout on ArrowRight, with the caret in its search box", async () => {
    const wrapper = await mountMenu();
    const trigger = menuEl().querySelectorAll(".ate-btn")[0];
    trigger.focus();

    await press(trigger, "ArrowRight");
    await flushPromises();
    await nextTick();

    const flyout = trigger.closest(".ate").querySelector(".ate-menu");
    expect(flyout.classList.contains("open")).toBe(true);
    expect(document.activeElement).toBe(flyout.querySelector("input"));
    wrapper.unmount();
  });

  it("leaves Escape inside a flyout to the flyout, and takes the second press", async () => {
    const wrapper = await mountMenu();
    const trigger = menuEl().querySelectorAll(".ate-btn")[0];
    trigger.focus();
    await press(trigger, "ArrowRight");
    await flushPromises();
    await nextTick();

    const flyout = trigger.closest(".ate").querySelector(".ate-menu");
    const search = flyout.querySelector("input");
    await press(search, "Escape");

    // The flyout is gone, the menu behind it is not, and focus is back on the
    // trigger so the next Escape has somewhere to land.
    expect(flyout.classList.contains("open")).toBe(false);
    expect(wrapper.emitted("close")).toBeUndefined();
    expect(document.activeElement).toBe(trigger);

    await press(trigger, "Escape");
    expect(wrapper.emitted("close")).toBeTruthy();
    wrapper.unmount();
  });

  it("lets Enter through to the flyout row's native activation", async () => {
    // The menu's own key handling must not preventDefault on its way past: the
    // rows are real buttons and Enter/Space activation is the browser's job.
    const wrapper = await mountMenu();
    const trigger = menuEl().querySelectorAll(".ate-btn")[0];
    trigger.focus();
    await press(trigger, "ArrowRight");
    await flushPromises();
    await nextTick();

    const row = trigger.closest(".ate").querySelector(".ate-item");
    const event = new KeyboardEvent("keydown", {
      key: "Enter",
      bubbles: true,
      cancelable: true,
    });
    row.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
    wrapper.unmount();
  });

  it("does not steal arrow keys while a flyout is driving them", async () => {
    const wrapper = await mountMenu();
    const trigger = menuEl().querySelectorAll(".ate-btn")[0];
    trigger.focus();
    await press(trigger, "ArrowRight");
    await flushPromises();
    await nextTick();

    const flyout = trigger.closest(".ate").querySelector(".ate-menu");
    await press(flyout.querySelector("input"), "ArrowDown");

    // Focus moved to the flyout's first row, not to the next menu item.
    expect(document.activeElement).toBe(flyout.querySelector(".ate-item"));
    expect(document.activeElement.closest(".ate-menu")).toBe(flyout);
    wrapper.unmount();
  });
});
