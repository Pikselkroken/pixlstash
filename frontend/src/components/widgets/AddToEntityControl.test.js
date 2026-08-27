// AddToEntityControl.vue - two suites, kept together deliberately.
//
// * **#646, the shared entity-list cache.** The menu is `v-if`-mounted, so every
//   open destroys and recreates these controls. These cases pin render-from-cache
//   on reopen, revalidate-anyway, and membership hydrating without gating rows.
// * **#645, the create affordance, `face` mode and `floatMenu`.** Opt-in
//   `allowCreate`, the pinned "New person…" row, the no-match Create "query"… row,
//   the single-select face mode that performs no writes, and the menu escaping a
//   clipping / scrolling host.
//
// The mocks below are bare `vi.fn()`s and each suite states its own fixtures, so
// neither can silently inherit the other's people or sets.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { nextTick, ref } from "vue";

vi.mock("../../api/characters", () => ({
  listCharacters: vi.fn(),
  getCharacterMembership: vi.fn(),
  addCharacterFaces: vi.fn(),
}));
vi.mock("../../api/pictureSets", () => ({
  listPictureSets: vi.fn(),
  getPictureSetMembership: vi.fn(),
  addPictureToSet: vi.fn(),
  removePictureFromSet: vi.fn(),
}));
vi.mock("../../api/projects", () => ({
  listProjects: vi.fn(),
  getProjectMembership: vi.fn(),
}));
vi.mock("../../utils/apiClient", () => ({
  API_BASE_URL: "/api/v1",
  isReadOnly: ref(false),
  sessionContext: ref(null),
  onSessionReset: () => () => {},
}));

import {
  listPictureSets,
  getPictureSetMembership,
  addPictureToSet,
} from "../../api/pictureSets";
import { listProjects, getProjectMembership } from "../../api/projects";
import { listCharacters, getCharacterMembership } from "../../api/characters";
import AddToEntityControl from "./AddToEntityControl.vue";

/** The #645 suite's people. Distinct from #646's CHARACTERS on purpose. */
const PEOPLE = [
  { id: 1, name: "Alice" },
  { id: 2, name: "Bob" },
];

vi.mock("../../api/pictureSets", () => ({
  listPictureSets: vi.fn(),
  getPictureSetMembership: vi.fn(),
  addPictureToSet: vi.fn(),
  removePictureFromSet: vi.fn(),
}));
vi.mock("../../api/projects", () => ({
  listProjects: vi.fn(),
  getProjectMembership: vi.fn(),
}));
vi.mock("../../utils/apiClient", () => ({
  isReadOnly: ref(false),
  sessionContext: ref(null),
  onSessionReset: () => () => {},
}));

const SETS = [
  { id: 7, name: "Portraits", picture_count: 12 },
  { id: 8, name: "Landscapes", picture_count: 3 },
];

const CHARACTERS = [
  { id: 11, name: "Ada" },
  { id: 12, name: "Grace" },
];

let pinia;

function mountControl(props = {}) {
  return mount(AddToEntityControl, {
    props: {
      type: "set",
      backendUrl: "http://backend.test",
      subjectIds: ["101"],
      ...props,
    },
    global: {
      plugins: [pinia],
      stubs: { "v-icon": true, Teleport: true },
    },
  });
}

const rowNames = (wrapper) =>
  wrapper.findAll(".ate-item .ate-item-name").map((n) => n.text());

function deferred() {
  let resolve;
  const promise = new Promise((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

describe("AddToEntityControl", () => {
  beforeEach(() => {
    pinia = createPinia();
    setActivePinia(pinia);
    listPictureSets.mockReset().mockResolvedValue(SETS);
    getPictureSetMembership.mockReset().mockResolvedValue({ 7: ["101"] });
    addPictureToSet.mockReset().mockResolvedValue({});
    listCharacters.mockReset().mockResolvedValue(CHARACTERS);
    getCharacterMembership.mockReset().mockResolvedValue({
      character_assignments: { 11: ["101"] },
      pictures_with_faces: ["101"],
    });
    vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the list from cache on a reopen, and revalidates anyway", async () => {
    const first = mountControl();
    await first.find("button.ate-btn").trigger("click");
    await flushPromises();
    expect(rowNames(first)).toEqual(["Portraits", "Landscapes"]);
    expect(listPictureSets).toHaveBeenCalledTimes(1);
    // The context menu's `v-if` tears the control down on close.
    first.unmount();

    // The next open would previously have shown "Loading sets..." until a full
    // list read came back. Now the cache is already on screen, before anything
    // resolves.
    const reopened = mountControl();
    const listRead = deferred();
    listPictureSets.mockReturnValueOnce(listRead.promise);
    await reopened.find("button.ate-btn").trigger("click");

    expect(rowNames(reopened)).toEqual(["Portraits", "Landscapes"]);
    expect(reopened.find(".ate-empty").exists()).toBe(false);
    // C3: revalidate-on-open is mandatory - a scoped session gets no ws events,
    // so this is its only invalidation path.
    expect(listPictureSets).toHaveBeenCalledTimes(2);

    listRead.resolve([{ id: 9, name: "Renamed", picture_count: 1 }]);
    await flushPromises();
    expect(rowNames(reopened)).toEqual(["Renamed"]);
    reopened.unmount();
  });

  it("renders the rows before the membership lands, then ticks them", async () => {
    const membership = deferred();
    getPictureSetMembership.mockReturnValueOnce(membership.promise);

    const wrapper = mountControl();
    await wrapper.find("button.ate-btn").trigger("click");
    await flushPromises();

    // The list is up while membership is still in flight - it must not gate it.
    expect(rowNames(wrapper)).toEqual(["Portraits", "Landscapes"]);
    expect(wrapper.findAll(".ate-item--checked")).toHaveLength(0);

    membership.resolve({ 7: ["101"] });
    await flushPromises();
    expect(
      wrapper.findAll(".ate-item--checked .ate-item-name").map((n) => n.text()),
    ).toEqual(["Portraits"]);
    wrapper.unmount();
  });

  it("does not carry one selection's membership over to the next", async () => {
    const wrapper = mountControl();
    await wrapper.find("button.ate-btn").trigger("click");
    await flushPromises();
    expect(wrapper.findAll(".ate-item--checked")).toHaveLength(1);

    const slowMembership = deferred();
    getPictureSetMembership.mockReturnValueOnce(slowMembership.promise);
    await wrapper.setProps({ subjectIds: ["202"] });
    await flushPromises();

    // The previous selection's ticks are gone the moment the selection changes.
    expect(wrapper.findAll(".ate-item--checked")).toHaveLength(0);
    slowMembership.resolve({ 8: ["202"] });
    await flushPromises();
    expect(
      wrapper.findAll(".ate-item--checked .ate-item-name").map((n) => n.text()),
    ).toEqual(["Landscapes"]);
    wrapper.unmount();
  });

  // The character reader also produces `picturesWithFaces`, which gates whether
  // a character row can read as checked at all. It used to be assigned inside
  // the reader - i.e. BEFORE fetchMembers' selection guard - so a superseded
  // response discarded its membership but still wrote its face ids, silently
  // un-ticking the current selection's rows.
  it("discards both halves of a superseded character membership response", async () => {
    const slowFirst = deferred();
    const fastSecond = deferred();
    getCharacterMembership
      .mockReturnValueOnce(slowFirst.promise)
      .mockReturnValueOnce(fastSecond.promise);

    const wrapper = mountControl({ type: "character", subjectIds: ["101"] });
    await wrapper.find("button.ate-btn").trigger("click");
    await flushPromises();

    // The selection moves on while the first membership read is still open.
    await wrapper.setProps({ subjectIds: ["202"] });
    fastSecond.resolve({
      character_assignments: { 12: ["202"] },
      pictures_with_faces: ["202"],
    });
    await flushPromises();
    expect(
      wrapper.findAll(".ate-item--checked .ate-item-name").map((n) => n.text()),
    ).toEqual(["Grace"]);

    // The superseded response lands last and must change nothing at all.
    slowFirst.resolve({
      character_assignments: { 11: ["101"] },
      pictures_with_faces: ["101"],
    });
    await flushPromises();
    expect(
      wrapper.findAll(".ate-item--checked .ate-item-name").map((n) => n.text()),
    ).toEqual(["Grace"]);
    wrapper.unmount();
  });

  it("refetches the list when an assignment 404s on a stale entity", async () => {
    const wrapper = mountControl();
    await wrapper.find("button.ate-btn").trigger("click");
    await flushPromises();
    listPictureSets.mockClear();

    addPictureToSet.mockRejectedValueOnce({
      response: { status: 404, data: { detail: "Picture set not found" } },
    });
    // "Landscapes" - the row this selection is not yet in.
    await wrapper.findAll("button.ate-item")[1].trigger("click");
    await flushPromises();

    expect(wrapper.find(".ate-status").text()).toContain("no longer exists");
    expect(listPictureSets).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });
});

// ── #645: create affordance, face mode, floatMenu ───────────────────────────

// The control reads `useEntityListsStore`, so these mounts need pinia too.
// Teleport is deliberately NOT stubbed here: the floatMenu cases below
// assert the menu really leaves its scrolling host.
const globalStubs = () => ({
  global: { plugins: [pinia], stubs: { "v-icon": true } },
});

async function mountOpen(props = {}) {
  const wrapper = mount(AddToEntityControl, {
    props: {
      type: "character",
      backendUrl: "http://x",
      subjectIds: ["10", "11"],
      allowCreate: true,
      ...props,
    },
    ...globalStubs(),
  });
  await wrapper.find(".ate-btn").trigger("click");
  await flushPromises();
  return wrapper;
}

function pinnedCreateButton(wrapper) {
  return wrapper.find(".ate-create-pinned .ate-item--create");
}

beforeEach(() => {
  pinia = createPinia();
  setActivePinia(pinia);
  vi.clearAllMocks();
  listCharacters.mockResolvedValue(PEOPLE);
  getCharacterMembership.mockResolvedValue({
    character_assignments: {},
    pictures_with_faces: [],
  });
  listPictureSets.mockResolvedValue([]);
  getPictureSetMembership.mockResolvedValue({});
  listProjects.mockResolvedValue([]);
  getProjectMembership.mockResolvedValue({
    project_assignments: {},
    unassigned_picture_ids: [],
  });
});

describe("pinned New person row", () => {
  it("is visible for the character type, with the item list", async () => {
    const wrapper = await mountOpen();
    const pinned = pinnedCreateButton(wrapper);
    expect(pinned.exists()).toBe(true);
    expect(pinned.text()).toContain("New person…");
    // An action, not one of the listbox's options - so it carries no role and
    // sits outside the listbox element.
    expect(pinned.attributes("role")).toBeUndefined();
    expect(pinned.element.closest("[role=listbox]")).toBe(null);
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
    const wrapper = await mountOpen({ subjectIds: [] });
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
    expect(row.attributes("role")).toBeUndefined();
    expect(row.element.closest("[role=listbox]")).toBe(null);
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
    const wrapper = await mountOpen({ subjectIds: [] });
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
    ...globalStubs(),
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
    expect(rows.map((r) => r.attributes("aria-selected"))).toEqual([
      "false",
      "true",
      "false",
    ]);
    expect(rows.map((r) => r.attributes("role"))).toEqual([
      "option",
      "option",
      "option",
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
    ...globalStubs(),
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

  it("refuses to float a flyout, whatever the host asks for", async () => {
    // The model shelf's row context menu passed `floatMenu` AND
    // `placement="right"`, which the prop doc has always called incompatible.
    // Both symptoms follow from honouring it: `sizeMenu` parks the panel BELOW
    // the row instead of beside it, and teleporting takes it out of the `.ate`
    // root the flyout hovers off - so moving the pointer towards it fires
    // `mouseleave` and shuts it before it arrives. Refused here, where every
    // caller routes through, rather than at each call site.
    const { wrapper, host } = await mountInScroller({
      floatMenu: true,
      placement: "right",
    });
    const menu = host.querySelector(".ate-menu");
    expect(menu).toBeTruthy();
    expect(menu.classList.contains("ate-menu--floating")).toBe(false);
    // No viewport coordinates were written: it is placed by the flyout CSS.
    expect(menu.style.left).toBe("");
    // The one that keeps it reachable: still inside the hover root.
    expect(wrapper.find(".ate").element.contains(menu)).toBe(true);
    wrapper.unmount();
    host.remove();
  });

  // jsdom lays nothing out, so the trigger has to be told where it is. `right`
  // is the only figure the flip reads: 185px of flyout either fits beside it or
  // does not.
  function putTriggerAt(wrapper, right) {
    wrapper.find(".ate").element.getBoundingClientRect = () => ({
      left: right - 200,
      right,
      top: 0,
      bottom: 24,
      width: 200,
      height: 24,
    });
  }

  /** A flyout mounted but NOT opened, with its trigger placed. */
  function mountFlyout(right) {
    const host = document.createElement("div");
    document.body.appendChild(host);
    const wrapper = mount(AddToEntityControl, {
      props: {
        type: "face",
        backendUrl: "http://x",
        faceId: 4,
        subjectIds: [],
        placement: "right",
      },
      attachTo: host,
      ...globalStubs(),
    });
    putTriggerAt(wrapper, right);
    return { wrapper, host };
  }

  it("flips a flyout at the right edge when it is opened without a hover", async () => {
    // The flip used to be measured in `onFlyoutMouseenter` alone, so a flyout
    // opened by click or by Enter - the whole keyboard path - kept whatever the
    // last hover left behind, which on a first open is "not flipped", and the
    // panel painted off the right of the screen with nothing to clamp it.
    const { wrapper, host } = mountFlyout(window.innerWidth);
    await wrapper.find(".ate-btn").trigger("click");
    await flushPromises();
    expect(wrapper.find(".ate").classes()).toContain("ate--flip");
    wrapper.unmount();
    host.remove();
  });

  it("re-measures the side on a resize, without waiting for a hover", async () => {
    // The other half of the same defect, and the one a keyboard user cannot
    // work around: a flyout opened where it fitted, then a window resize (or an
    // orientation change) that leaves it hard against the edge. Nothing else
    // recomputes the side - `sizeMenu` is what the resize listener calls, so it
    // has to.
    const { wrapper, host } = mountFlyout(400);
    await wrapper.find(".ate-btn").trigger("click");
    await flushPromises();
    expect(wrapper.find(".ate").classes()).not.toContain("ate--flip");

    putTriggerAt(wrapper, window.innerWidth);
    window.dispatchEvent(new Event("resize"));
    await flushPromises();
    expect(wrapper.find(".ate").classes()).toContain("ate--flip");
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

  it("keeps its keyboard once teleported, where no host can help it", async () => {
    // The floatMenu case is why navigation lives in the control: the panel is in
    // <body>, so a host keydown listener never sees these keys. Escape must also
    // stop here rather than bubbling on to the lightbox's window handler, which
    // would close the whole overlay behind the menu.
    const { wrapper, host } = await mountInScroller({ floatMenu: true });
    const menu = document.querySelector(".ate-menu");
    const onWindow = vi.fn();
    window.addEventListener("keydown", onWindow);

    const search = menu.querySelector("input");
    search.focus();
    search.dispatchEvent(
      new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }),
    );
    await nextTick();
    expect(document.activeElement).toBe(menu.querySelector(".ate-item"));

    document.activeElement.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );
    await nextTick();
    expect(menu.classList.contains("open")).toBe(false);
    expect(document.activeElement).toBe(wrapper.find(".ate-btn").element);
    expect(onWindow).not.toHaveBeenCalled();

    window.removeEventListener("keydown", onWindow);
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

// ── #759: keyboard operation and ARIA structure ─────────────────────────────
//
// The control was pointer-only in practice: `role="menu"` wrapped a text input,
// bulk membership had no ARIA state outside face mode, closing dropped focus on
// <body>, and the grid host's roving focus never reached the trigger. These
// cases pin the contract the control now owns itself - hosts only have to let
// it through, which ImageGridContextMenu.test.js covers on its side.

describe("keyboard operation and ARIA structure", () => {
  let mounted = null;

  beforeEach(() => {
    listPictureSets.mockResolvedValue(SETS);
    // "10" is in set 7 and "11" is not, so set 7 is the partial (mixed) case.
    getPictureSetMembership.mockResolvedValue({ 7: ["10"] });
  });

  afterEach(() => {
    mounted?.unmount();
    mounted = null;
  });

  /** Mounted into the document, because every case here asserts on focus. */
  async function openSet(props = {}) {
    mounted = mount(AddToEntityControl, {
      props: {
        type: "set",
        backendUrl: "http://x",
        subjectIds: ["10", "11"],
        ...props,
      },
      attachTo: document.body,
      global: { plugins: [pinia], stubs: { "v-icon": true } },
    });
    await mounted.find(".ate-btn").trigger("click");
    await flushPromises();
    await nextTick();
    return mounted;
  }

  const rows = (wrapper) => wrapper.findAll(".ate-item");
  const searchBox = (wrapper) => wrapper.find(".ate-search input");
  const press = (wrapper, target, key) =>
    wrapper.find(target).trigger("keydown", { key });

  it("exposes a labelled search box and a listbox instead of a menu", async () => {
    const wrapper = await openSet();
    expect(wrapper.find('[role="menu"]').exists()).toBe(false);
    expect(searchBox(wrapper).attributes("aria-label")).toBe("Search sets");

    const listbox = wrapper.find('[role="listbox"]');
    expect(listbox.attributes("aria-label")).toBe("Sets");
    expect(listbox.attributes("aria-multiselectable")).toBe("true");
    // Status text is announced rather than silently repainted.
    expect(wrapper.find(".ate-status").exists()).toBe(false);
    await wrapper.findAll(".ate-item")[1].trigger("click");
    await flushPromises();
    const status = wrapper.find(".ate-status");
    expect(status.attributes("role")).toBe("status");
    expect(status.attributes("aria-live")).toBe("polite");
    // The trigger points at that listbox, and offers a listbox rather than the
    // menu it used to claim.
    const trigger = wrapper.find(".ate-btn");
    expect(trigger.attributes("aria-haspopup")).toBe("listbox");
    expect(trigger.attributes("aria-controls")).toBe(listbox.attributes("id"));
    // Every row is an option of that listbox - nothing is left loose.
    expect(listbox.findAll(".ate-item")).toHaveLength(rows(wrapper).length);
  });

  it("reports partial bulk membership in the option's name, not aria-checked", async () => {
    const wrapper = await openSet();
    // `aria-checked` on an option is not reliably announced; the state a
    // listbox exposes is `aria-selected`, and partial rides in the name.
    expect(rows(wrapper).map((r) => r.attributes("aria-checked"))).toEqual([
      undefined,
      undefined,
    ]);
    expect(rows(wrapper).map((r) => r.attributes("aria-selected"))).toEqual([
      "false",
      "false",
    ]);
    expect(rows(wrapper)[0].text()).toContain(", partially applied");
    expect(rows(wrapper)[1].text()).not.toContain("partially applied");
    expect(
      rows(wrapper)[0].find(".visually-hidden").exists(),
      "the supplementary text must not be visible",
    ).toBe(true);

    // A full membership is selected, and says nothing extra.
    getPictureSetMembership.mockResolvedValue({ 7: ["10", "11"] });
    mounted.unmount();
    mounted = null;
    const full = await openSet();
    expect(rows(full)[0].attributes("aria-selected")).toBe("true");
    expect(rows(full)[0].text()).not.toContain("partially applied");
  });

  it("walks the search box and rows with the arrow keys, without wrapping", async () => {
    const wrapper = await openSet();
    const [portraits, landscapes] = rows(wrapper).map((r) => r.element);
    expect(document.activeElement).toBe(searchBox(wrapper).element);

    await press(wrapper, ".ate-search input", "ArrowDown");
    expect(document.activeElement).toBe(portraits);
    await press(wrapper, ".ate-item", "ArrowDown");
    expect(document.activeElement).toBe(landscapes);

    // No wrap at the end...
    await wrapper
      .findAll(".ate-item")[1]
      .trigger("keydown", { key: "ArrowDown" });
    expect(document.activeElement).toBe(landscapes);
    // ...and ArrowUp off the first row lands back in the search box, which is
    // how a keyboard user gets back to filtering.
    await wrapper
      .findAll(".ate-item")[1]
      .trigger("keydown", { key: "ArrowUp" });
    await wrapper.find(".ate-item").trigger("keydown", { key: "ArrowUp" });
    expect(document.activeElement).toBe(searchBox(wrapper).element);
  });

  it("jumps to the first and last row with Home and End, but not while typing", async () => {
    const wrapper = await openSet();
    const [portraits, landscapes] = rows(wrapper).map((r) => r.element);

    // In the search box these stay text-editing keys.
    await press(wrapper, ".ate-search input", "End");
    expect(document.activeElement).toBe(searchBox(wrapper).element);

    await press(wrapper, ".ate-search input", "ArrowDown");
    await press(wrapper, ".ate-item", "End");
    expect(document.activeElement).toBe(landscapes);
    await wrapper.findAll(".ate-item")[1].trigger("keydown", { key: "Home" });
    expect(document.activeElement).toBe(portraits);
  });

  it("keeps roving focus on the options, never on a create row (#782)", async () => {
    // The create rows share `.ate-item` but sit outside the listbox, so the old
    // class-based query let End land on an action button instead of an option.
    mounted = mount(AddToEntityControl, {
      props: {
        type: "character",
        backendUrl: "http://x",
        subjectIds: ["10", "11"],
        allowCreate: true,
      },
      attachTo: document.body,
      global: { plugins: [pinia], stubs: { "v-icon": true } },
    });
    await mounted.find(".ate-btn").trigger("click");
    await flushPromises();
    await nextTick();

    const pinned = mounted.find(".ate-create-pinned .ate-item--create");
    expect(pinned.exists()).toBe(true);
    const options = mounted.findAll('[role="option"]').map((o) => o.element);
    expect(options.length).toBeGreaterThan(1);

    await press(mounted, ".ate-search input", "ArrowDown");
    await press(mounted, '[role="option"]', "End");
    expect(document.activeElement).toBe(options[options.length - 1]);

    // ArrowDown off the last option stops there too.
    await press(mounted, '[role="option"]', "ArrowDown");
    expect(document.activeElement).toBe(options[options.length - 1]);

    await press(mounted, '[role="option"]', "Home");
    expect(document.activeElement).toBe(options[0]);
  });

  it("closes on Escape and hands focus back to the trigger", async () => {
    const wrapper = await openSet();
    await press(wrapper, ".ate-search input", "Escape");
    expect(wrapper.find(".ate-menu").classes()).not.toContain("open");
    expect(document.activeElement).toBe(wrapper.find(".ate-btn").element);
  });

  it("closes a flyout on ArrowLeft and hands focus back to the trigger", async () => {
    const wrapper = await openSet({ placement: "right" });
    // Deliberate activation of a flyout puts the caret in its search box.
    expect(document.activeElement).toBe(searchBox(wrapper).element);

    await press(wrapper, ".ate-search input", "ArrowDown");
    await press(wrapper, ".ate-item", "ArrowLeft");
    expect(wrapper.find(".ate-menu").classes()).not.toContain("open");
    expect(document.activeElement).toBe(wrapper.find(".ate-btn").element);
  });

  it("returns focus to the trigger after a row assignment closes the menu", async () => {
    listCharacters.mockResolvedValue(PEOPLE);
    mounted = mount(AddToEntityControl, {
      props: {
        type: "face",
        backendUrl: "http://x",
        faceId: 3,
        assignedCharacterId: null,
      },
      attachTo: document.body,
      global: { plugins: [pinia], stubs: { "v-icon": true } },
    });
    await mounted.find(".ate-btn").trigger("click");
    await flushPromises();
    // Rows are [Unassigned, Alice, Bob]; picking Alice assigns and closes.
    await mounted.findAll(".ate-item")[1].trigger("click");
    await nextTick();
    expect(mounted.emitted("assign")).toBeTruthy();
    expect(document.activeElement).toBe(mounted.find(".ate-btn").element);
  });

  it("does not claim multi-select for the single-valued modes", async () => {
    // A picture has exactly one project, so that listbox is not multiselectable.
    const wrapper = await openSet({ type: "project" });
    expect(
      wrapper.find('[role="listbox"]').attributes("aria-multiselectable"),
    ).toBeUndefined();
  });

  it("leaves Enter to the row's native activation", async () => {
    // Rows are real <button>s: Enter and Space activate them for free, and the
    // keyboard handler must not swallow the keystroke on its way there.
    const wrapper = await openSet();
    const row = wrapper.findAll(".ate-item")[1];
    const event = new KeyboardEvent("keydown", {
      key: "Enter",
      bubbles: true,
      cancelable: true,
    });
    row.element.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
  });

  it("leaves focus alone when the menu closes without holding it", async () => {
    const wrapper = await openSet();
    const outside = document.createElement("button");
    document.body.appendChild(outside);
    outside.focus();

    document.body.dispatchEvent(
      new MouseEvent("pointerdown", { bubbles: true }),
    );
    await nextTick();
    expect(wrapper.find(".ate-menu").classes()).not.toContain("open");
    // The click-outside target keeps the focus it just took.
    expect(document.activeElement).toBe(outside);
    outside.remove();
  });
});

// ---------------------------------------------------------------------------
// Host-driven mode (model shelf F3).
//
// The shelf attaches ADAPTERS through this same picker, so the control has to
// work for subjects it cannot read membership for and must not write. These
// cases pin the two regressions the generalisation could cause: a picture-only
// rule leaking into a non-picture host, and the writing paths still firing.
// ---------------------------------------------------------------------------

describe("host-driven mode", () => {
  const SETS = [
    { id: 1, name: "Alpha" },
    { id: 2, name: "Beta" },
  ];

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.clearAllMocks();
    listPictureSets.mockResolvedValue(SETS);
    listCharacters.mockResolvedValue(PEOPLE);
    getPictureSetMembership.mockResolvedValue({});
    getCharacterMembership.mockResolvedValue({});
  });

  async function mountHosted(props = {}) {
    const wrapper = mount(AddToEntityControl, {
      props: {
        type: "set",
        backendUrl: "http://backend.test",
        subjectIds: ["a", "b", "c"],
        membership: { 1: new Set(["a", "b"]) },
        ...props,
      },
      global: { plugins: [pinia], stubs: { "v-icon": true, Teleport: true } },
    });
    await wrapper.find(".ate-btn").trigger("click");
    await flushPromises();
    return wrapper;
  }

  const rowFor = (wrapper, name) =>
    wrapper.findAll(".ate-item").find((r) => r.text().includes(name));

  it("reads its tri-state from the host's map, without fetching", async () => {
    const wrapper = await mountHosted();
    // Two of the three subjects are in Alpha, so it is partial and says so.
    expect(rowFor(wrapper, "Alpha").text()).toContain("partially applied");
    expect(rowFor(wrapper, "Beta").text()).not.toContain("partially applied");
    // The readers would not understand these ids, so they must not be called.
    expect(getPictureSetMembership).not.toHaveBeenCalled();
  });

  it("resolves a partial entity UP, writing only the missing subjects", async () => {
    // The same rule the writing paths apply. Detaching from a mixed state would
    // drop an attachment the user never named, and the shelf has no undo.
    const wrapper = await mountHosted();
    await rowFor(wrapper, "Alpha").trigger("click");

    expect(wrapper.emitted("detach")).toBeUndefined();
    expect(wrapper.emitted("attach")[0][0]).toEqual({
      entityType: "set",
      entityId: 1,
      entityName: "Alpha",
      subjectIds: ["c"],
    });
  });

  it("detaches only from a fully applied entity", async () => {
    const wrapper = await mountHosted({
      membership: { 1: new Set(["a", "b", "c"]) },
    });
    await rowFor(wrapper, "Alpha").trigger("click");

    expect(wrapper.emitted("attach")).toBeUndefined();
    expect(wrapper.emitted("detach")[0][0]).toEqual({
      entityType: "set",
      entityId: 1,
      subjectIds: ["a", "b", "c"],
    });
  });

  it("writes nothing itself", async () => {
    const wrapper = await mountHosted();
    await rowFor(wrapper, "Beta").trigger("click");
    expect(addPictureToSet).not.toHaveBeenCalled();
    expect(wrapper.emitted("attach")).toHaveLength(1);
  });

  it("does not narrow character membership by which subjects have faces", async () => {
    // The regression this generalisation could cause. "A picture with no face
    // cannot be a character member" is a picture rule; applied to adapters it
    // filters every id away and the whole list reads unchecked however many are
    // attached.
    const wrapper = await mountHosted({
      type: "character",
      membership: { 1: new Set(["a", "b", "c"]) },
    });
    const alice = rowFor(wrapper, "Alice");
    expect(alice.classes()).toContain("ate-item--checked");
    expect(alice.attributes("aria-selected")).toBe("true");
  });
});
