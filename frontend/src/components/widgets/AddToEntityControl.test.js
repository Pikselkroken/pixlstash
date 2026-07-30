// The Person/Set/Project flyouts of the image context menu (issue #646).
//
// The menu is `v-if`-mounted, so every open destroys and recreates these
// controls. Before the shared list store that wiped their only cache and cost a
// full list read plus a membership read per hover. These tests pin the three
// behaviours that replaced it: render from cache on reopen, revalidate anyway,
// and hydrate the checkmarks without holding up the rows.

import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import { mount, flushPromises } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { ref } from "vue";

vi.mock("../../api/characters", () => ({
  listCharacters: vi.fn(),
  getCharacterMembership: vi.fn(),
  addCharacterFaces: vi.fn(),
  removeCharacterFaces: vi.fn(),
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
  isReadOnly: ref(false),
  sessionContext: ref(null),
  onSessionReset: () => () => {},
}));

import {
  listPictureSets,
  getPictureSetMembership,
  addPictureToSet,
} from "../../api/pictureSets";
import { listCharacters, getCharacterMembership } from "../../api/characters";
import AddToEntityControl from "./AddToEntityControl.vue";

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
      pictureIds: ["101"],
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
    // C3: revalidate-on-open is mandatory — a scoped session gets no ws events,
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

    // The list is up while membership is still in flight — it must not gate it.
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
    await wrapper.setProps({ pictureIds: ["202"] });
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
  // the reader — i.e. BEFORE fetchMembers' selection guard — so a superseded
  // response discarded its membership but still wrote its face ids, silently
  // un-ticking the current selection's rows.
  it("discards both halves of a superseded character membership response", async () => {
    const slowFirst = deferred();
    const fastSecond = deferred();
    getCharacterMembership
      .mockReturnValueOnce(slowFirst.promise)
      .mockReturnValueOnce(fastSecond.promise);

    const wrapper = mountControl({ type: "character", pictureIds: ["101"] });
    await wrapper.find("button.ate-btn").trigger("click");
    await flushPromises();

    // The selection moves on while the first membership read is still open.
    await wrapper.setProps({ pictureIds: ["202"] });
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
    // "Landscapes" — the row this selection is not yet in.
    await wrapper.findAll("button.ate-item")[1].trigger("click");
    await flushPromises();

    expect(wrapper.find(".ate-status").text()).toContain("no longer exists");
    expect(listPictureSets).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });
});
