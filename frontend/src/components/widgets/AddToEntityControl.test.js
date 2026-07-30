// The Person flyout's create affordance (#645):
//   - opt-in via the `allowCreate` prop (default false): hosts that do not
//     handle the "create" event (SelectionMenu, ImageOverlay) must never show
//     a row that does nothing;
//   - a pinned "New person…" row, character type only, always visible;
//   - a no-match empty state that becomes an actionable Create "query"… row;
//   - Enter in the search box activating the no-match create;
//   - both disabled when readonly or when there is no picture selection;
//   - the "create" event carrying the typed query, with NO creation logic in
//     this component.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { nextTick } from "vue";

vi.mock("../../api/pictureSets", () => ({
  listPictureSets: vi.fn().mockResolvedValue([]),
  getPictureSetMembership: vi.fn().mockResolvedValue({}),
  addPictureToSet: vi.fn(),
  removePictureFromSet: vi.fn(),
}));

vi.mock("../../api/projects", () => ({
  listProjects: vi.fn().mockResolvedValue([]),
  getProjectMembership: vi
    .fn()
    .mockResolvedValue({ project_assignments: {}, unassigned_picture_ids: [] }),
}));

vi.mock("../../api/characters", () => ({
  listCharacters: vi.fn().mockResolvedValue([
    { id: 1, name: "Alice" },
    { id: 2, name: "Bob" },
  ]),
  getCharacterMembership: vi
    .fn()
    .mockResolvedValue({ character_assignments: {}, pictures_with_faces: [] }),
  addCharacterFaces: vi.fn(),
  removeCharacterFaces: vi.fn(),
}));

import AddToEntityControl from "./AddToEntityControl.vue";

const globalStubs = { global: { stubs: { "v-icon": true } } };

async function mountOpen(props = {}) {
  const wrapper = mount(AddToEntityControl, {
    props: {
      type: "character",
      backendUrl: "http://x",
      pictureIds: ["10", "11"],
      allowCreate: true,
      ...props,
    },
    ...globalStubs,
  });
  await wrapper.find(".ate-btn").trigger("click");
  await flushPromises();
  return wrapper;
}

function pinnedCreateButton(wrapper) {
  return wrapper.find(".ate-create-pinned .ate-item--create");
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("pinned New person row", () => {
  it("is visible for the character type, with the item list", async () => {
    const wrapper = await mountOpen();
    const pinned = pinnedCreateButton(wrapper);
    expect(pinned.exists()).toBe(true);
    expect(pinned.text()).toContain("New person…");
    expect(pinned.attributes("role")).toBe("menuitem");
    expect(pinned.attributes("disabled")).toBeUndefined();
    // The regular character rows are still there.
    const names = wrapper
      .findAll(".ate-list .ate-item-name")
      .map((n) => n.text());
    expect(names).toContain("Alice");
    expect(names).toContain("Bob");
  });

  it("does not render for the set or project types", async () => {
    for (const type of ["set", "project"]) {
      const wrapper = await mountOpen({ type });
      expect(pinnedCreateButton(wrapper).exists()).toBe(false);
    }
  });

  it("does not render without allowCreate (the default), even for characters", async () => {
    const wrapper = await mountOpen({ allowCreate: undefined });
    expect(pinnedCreateButton(wrapper).exists()).toBe(false);
    // The regular character rows are unaffected.
    const names = wrapper
      .findAll(".ate-list .ate-item-name")
      .map((n) => n.text());
    expect(names).toContain("Alice");
  });

  it("emits create with the current query when clicked", async () => {
    const wrapper = await mountOpen();
    await wrapper.find(".ate-search input").setValue("Al");
    await pinnedCreateButton(wrapper).trigger("click");
    expect(wrapper.emitted("create")).toEqual([["Al"]]);
  });

  it("is disabled when readonly", async () => {
    const wrapper = await mountOpen({ readonly: true });
    expect(pinnedCreateButton(wrapper).attributes("disabled")).toBeDefined();
  });

  it("is disabled when there is no picture selection", async () => {
    const wrapper = await mountOpen({ pictureIds: [] });
    expect(pinnedCreateButton(wrapper).attributes("disabled")).toBeDefined();
  });
});

describe("no-match empty state", () => {
  it("becomes an actionable Create row quoting the query", async () => {
    const wrapper = await mountOpen();
    await wrapper.find(".ate-search input").setValue("Zed");
    const row = wrapper.find(".ate-list .ate-item--create");
    expect(row.exists()).toBe(true);
    expect(row.text()).toContain('Create "Zed"…');
    expect(row.attributes("role")).toBe("menuitem");
    await row.trigger("click");
    expect(wrapper.emitted("create")).toEqual([["Zed"]]);
  });

  it("stays a plain empty state when the query is empty", async () => {
    const { listCharacters } = await import("../../api/characters");
    listCharacters.mockResolvedValueOnce([]);
    const wrapper = await mountOpen();
    expect(wrapper.find(".ate-list .ate-item--create").exists()).toBe(false);
    expect(wrapper.find(".ate-empty").text()).toBe("No characters found");
  });

  it("Enter in the search box activates the no-match create", async () => {
    const wrapper = await mountOpen();
    const input = wrapper.find(".ate-search input");
    await input.setValue("Zed");
    await input.trigger("keydown.enter");
    expect(wrapper.emitted("create")).toEqual([["Zed"]]);
  });

  it("Enter does nothing while the query still matches people", async () => {
    const wrapper = await mountOpen();
    const input = wrapper.find(".ate-search input");
    await input.setValue("Ali");
    await input.trigger("keydown.enter");
    expect(wrapper.emitted("create")).toBeUndefined();
  });

  it("Enter does nothing when disabled by an empty selection", async () => {
    const wrapper = await mountOpen({ pictureIds: [] });
    const input = wrapper.find(".ate-search input");
    await input.setValue("Zed");
    await input.trigger("keydown.enter");
    expect(wrapper.emitted("create")).toBeUndefined();
  });

  it("stays a plain empty state and Enter is inert without allowCreate", async () => {
    const wrapper = await mountOpen({ allowCreate: undefined });
    const input = wrapper.find(".ate-search input");
    await input.setValue("Zed");
    expect(wrapper.find(".ate-list .ate-item--create").exists()).toBe(false);
    expect(wrapper.find(".ate-empty").text()).toBe("No characters found");
    await input.trigger("keydown.enter");
    expect(wrapper.emitted("create")).toBeUndefined();
  });
});

// ── The single-select `face` mode (#645) ─────────────────────────────────────
// A face has exactly one person or none, so this mode uses radio glyphs, adds
// an Unassigned row, and performs NO writes: it emits and the host calls the
// face-level API.

async function mountFace(props = {}) {
  const wrapper = mount(AddToEntityControl, {
    props: {
      type: "face",
      backendUrl: "http://x",
      faceId: 4,
      assignedCharacterId: 1,
      assignedCharacterName: "Alice",
      allowCreate: true,
      forceDark: true,
      ...props,
    },
    ...globalStubs,
  });
  await wrapper.find(".ate-btn").trigger("click");
  await flushPromises();
  return wrapper;
}

function rowByName(wrapper, name) {
  return wrapper
    .findAll(".ate-list .ate-item")
    .find((b) => b.text().includes(name));
}

describe("face mode", () => {
  it("shows the current assignment on the trigger", async () => {
    const wrapper = await mountFace();
    expect(wrapper.find(".ate-label").text()).toBe("Alice");
    const unassigned = await mountFace({
      assignedCharacterId: null,
      assignedCharacterName: "",
    });
    expect(unassigned.find(".ate-label").text()).toBe("Unassigned");
  });

  it("offers Unassigned first, then the people", async () => {
    const wrapper = await mountFace();
    const names = wrapper
      .findAll(".ate-list .ate-item-name")
      .map((n) => n.text());
    expect(names).toEqual(["Unassigned", "Alice", "Bob"]);
  });

  it("marks exactly the assigned person with a radio glyph", async () => {
    const wrapper = await mountFace();
    const rows = wrapper.findAll(".ate-list .ate-item");
    expect(rows.map((r) => r.attributes("aria-checked"))).toEqual([
      "false",
      "true",
      "false",
    ]);
    expect(rows.map((r) => r.attributes("role"))).toEqual([
      "menuitemradio",
      "menuitemradio",
      "menuitemradio",
    ]);
    // The checked-olive class belongs to the multi-picture mode, not here: the
    // radio shape carries the state so the highlight colour stays unique to
    // the create row.
    expect(wrapper.find(".ate-item--checked").exists()).toBe(false);
  });

  it("emits assign for another person, and does not write anything itself", async () => {
    const { addCharacterFaces } = await import("../../api/characters");
    const wrapper = await mountFace();
    await rowByName(wrapper, "Bob").trigger("click");
    expect(wrapper.emitted("assign")).toEqual([
      [{ faceId: 4, characterId: 2, characterName: "Bob" }],
    ]);
    expect(addCharacterFaces).not.toHaveBeenCalled();
  });

  it("emits unassign for the Unassigned row", async () => {
    const wrapper = await mountFace();
    await rowByName(wrapper, "Unassigned").trigger("click");
    expect(wrapper.emitted("unassign")).toEqual([[{ faceId: 4 }]]);
  });

  it("re-picking the current person is a no-op", async () => {
    const wrapper = await mountFace();
    await rowByName(wrapper, "Alice").trigger("click");
    expect(wrapper.emitted("assign")).toBeUndefined();
    expect(wrapper.emitted("unassign")).toBeUndefined();
  });

  it("offers the create row, and disables it without a face id", async () => {
    const wrapper = await mountFace();
    expect(pinnedCreateButton(wrapper).exists()).toBe(true);
    await pinnedCreateButton(wrapper).trigger("click");
    expect(wrapper.emitted("create")).toEqual([[""]]);

    const noFace = await mountFace({ faceId: null });
    expect(pinnedCreateButton(noFace).attributes("disabled")).toBeDefined();
    const readonly = await mountFace({ readonly: true });
    expect(pinnedCreateButton(readonly).attributes("disabled")).toBeDefined();
  });

  it("searching filters the people and can reach the create row", async () => {
    const wrapper = await mountFace();
    await wrapper.find(".ate-search input").setValue("Zed");
    expect(wrapper.find(".ate-list .ate-item--create").text()).toContain(
      'Create "Zed"…',
    );
    await wrapper.find(".ate-list .ate-item--create").trigger("click");
    expect(wrapper.emitted("create")).toEqual([["Zed"]]);
  });

  it("disables every row in a read-only session", async () => {
    const wrapper = await mountFace({ readonly: true });
    const rows = wrapper.findAll(".ate-list .ate-item");
    expect(rows.every((r) => r.attributes("disabled") !== undefined)).toBe(
      true,
    );
  });
});

// ── floatMenu: the menu must escape a clipping / scrolling host ───────────────
// The defect (#645 follow-up): in the overlay's Faces panel the in-place,
// absolutely positioned menu was clipped by `.overlay-sidebar`
// (overflow: hidden) AND inflated the scroll extent of `.face-assign-grid`
// (overflow-y: auto), which is the spurious scrollbar the user reported. These
// pin the escape itself, not just that a menu renders.

async function mountInScroller(props = {}) {
  const host = document.createElement("div");
  host.className = "scrolling-host";
  document.body.appendChild(host);
  const wrapper = mount(AddToEntityControl, {
    props: {
      type: "face",
      backendUrl: "http://x",
      faceId: 4,
      assignedCharacterId: null,
      allowCreate: true,
      forceDark: true,
      ...props,
    },
    attachTo: host,
    ...globalStubs,
  });
  await wrapper.find(".ate-btn").trigger("click");
  await flushPromises();
  await nextTick();
  return { wrapper, host };
}

describe("floatMenu", () => {
  it("takes the menu out of the scrolling host, so it cannot add scroll extent", async () => {
    const { wrapper, host } = await mountInScroller({ floatMenu: true });
    const menu = document.querySelector(".ate-menu");
    expect(menu).toBeTruthy();
    // The actual defect: the menu is no longer inside the host that scrolls.
    expect(host.contains(menu)).toBe(false);
    expect(document.body.contains(menu)).toBe(true);
    // The trigger stays where it was.
    expect(host.contains(wrapper.find(".ate-btn").element)).toBe(true);
    wrapper.unmount();
    host.remove();
  });

  it("keeps the menu in place without the prop, so other call sites are untouched", async () => {
    const { wrapper, host } = await mountInScroller();
    const menu = host.querySelector(".ate-menu");
    expect(menu).toBeTruthy();
    expect(host.contains(menu)).toBe(true);
    expect(menu.classList.contains("ate-menu--floating")).toBe(false);
    // And it keeps the in-place sizing contract: height only, no position.
    expect(menu.style.left).toBe("");
    expect(menu.style.top).toBe("");
    wrapper.unmount();
    host.remove();
  });

  it("positions against the viewport and stacks above the lightbox", async () => {
    const { wrapper, host } = await mountInScroller({ floatMenu: true });
    const menu = document.querySelector(".ate-menu.ate-menu--floating");
    expect(menu).toBeTruthy();
    // sizeMenu wrote viewport coordinates, not just a max-height.
    expect(menu.style.left).toMatch(/^-?\d+px$/);
    expect(menu.style.maxHeight).toMatch(/^\d+px$/);
    // top/bottom are a pair: exactly one is a length, the other is auto.
    const anchored = [menu.style.top, menu.style.bottom];
    expect(anchored.filter((v) => /^-?\d+px$/.test(v))).toHaveLength(1);
    expect(anchored.filter((v) => v === "auto")).toHaveLength(1);
    wrapper.unmount();
    host.remove();
  });

  it("closes on an outside click with the node teleported", async () => {
    // The containment checks in handleOutsideClick must still hold once the
    // menu lives in <body>: menuRef.contains() is what keeps a click INSIDE
    // the teleported menu from closing it.
    const { wrapper, host } = await mountInScroller({ floatMenu: true });
    const menu = document.querySelector(".ate-menu.ate-menu--floating");
    expect(menu.classList.contains("open")).toBe(true);

    // A click inside the teleported menu does not close it.
    menu.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true }));
    await nextTick();
    expect(menu.classList.contains("open")).toBe(true);

    // A click elsewhere does.
    document.body.dispatchEvent(
      new MouseEvent("pointerdown", { bubbles: true }),
    );
    await nextTick();
    expect(menu.classList.contains("open")).toBe(false);
    wrapper.unmount();
    host.remove();
  });
});
