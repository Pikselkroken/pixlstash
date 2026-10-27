// The Set picker's pinned person block (#2124): when the view is a person, or a
// selection shows exactly one person, that person's child sets and "New set in
// <Person>…" sit above "Other sets", and search filters only the rest.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const membership = vi.hoisted(() => ({ character_assignments: {} }));

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
vi.mock("../../api/pictureSets", () => ({
  listPictureSets: vi.fn(async () => SETS),
  getPictureSetMembership: vi.fn(async () => ({})),
  addPictureToSet: vi.fn(),
  removePictureFromSet: vi.fn(),
}));
vi.mock("../../api/characters", () => ({
  listCharacters: vi.fn(async () => PEOPLE),
  getCharacterMembership: vi.fn(async () => membership),
  addCharacterFaces: vi.fn(),
  removeCharacterFaces: vi.fn(),
  characterThumbnailUrl: (id) => `/thumb/${id}`,
}));
vi.mock("../../api/projects", () => ({
  listProjects: vi.fn(async () => []),
  getProjectMembership: vi.fn(async () => ({})),
}));

import AddToEntityControl from "./AddToEntityControl.vue";
import { useSelectionStore } from "../../stores/useSelectionStore";

const PEOPLE = [
  { id: 7, name: "Wren" },
  { id: 8, name: "Juno" },
];
const SETS = [
  { id: 1, name: "LoRA v3", parent_character_id: 7 },
  { id: 2, name: "Rainy neon streets" },
  { id: 3, name: "Juno LoRA", parent_character_id: 8 },
];

beforeEach(() => {
  setActivePinia(createPinia());
  membership.character_assignments = {};
});

async function openPicker() {
  const wrapper = mount(AddToEntityControl, {
    props: {
      type: "set",
      subjectIds: ["10", "11"],
      backendUrl: "http://x",
      allowCreate: true,
    },
    global: {
      stubs: { "v-icon": true, Tooltip: true, teleport: true },
    },
    attachTo: document.body,
  });
  await wrapper.find(".ate-btn").trigger("click");
  await flushPromises();
  return wrapper;
}

const names = (el) => el.findAll(".ate-item-name").map((n) => n.text());

describe("the pinned person block", () => {
  it("leads with the viewed person's sets and keeps them through search", async () => {
    useSelectionStore().selectedCharacter = 7;
    const wrapper = await openPicker();

    const pin = wrapper.find(".ate-pin");
    expect(pin.find(".ate-pin-name").text()).toBe("Wren");
    expect(names(pin)).toEqual(["LoRA v3", "New set in Wren…2 pictures"]);
    expect(wrapper.find(".ate-section-label").text()).toBe("Other sets");

    await wrapper.find(".ate-search input").setValue("zzz");
    expect(names(wrapper.find(".ate-pin"))).toContain("LoRA v3");
    const others = wrapper
      .findAll('[role="listbox"]')
      .filter((lb) => !lb.element.closest(".ate-pin"));
    expect(others.flatMap(names)).toEqual([]);
    wrapper.unmount();
  });

  it("creates under the pinned person", async () => {
    useSelectionStore().selectedCharacter = 7;
    const wrapper = await openPicker();
    const create = wrapper
      .findAll(".ate-item--create")
      .find((b) => b.text().includes("New set in Wren"));
    expect(create.text()).toContain("2 pictures");
    await create.trigger("click");
    expect(wrapper.emitted("create")).toEqual([[{ characterId: 7, name: "Wren" }]]);
    wrapper.unmount();
  });

  it("takes the one person every selected picture shows", async () => {
    membership.character_assignments = { 8: ["10", "11"] };
    const wrapper = await openPicker();
    expect(wrapper.find(".ate-pin-name").text()).toBe("Juno");
    wrapper.unmount();
  });

  it("pins nobody when someone else is in the pictures too", async () => {
    membership.character_assignments = { 7: ["10", "11"], 8: ["11"] };
    const wrapper = await openPicker();
    expect(wrapper.find(".ate-pin").exists()).toBe(false);
    wrapper.unmount();
  });

  it("pins nobody for a mixed selection and offers a plain New set…", async () => {
    membership.character_assignments = { 7: ["10"], 8: ["11"] };
    const wrapper = await openPicker();
    expect(wrapper.find(".ate-pin").exists()).toBe(false);
    const create = wrapper
      .findAll(".ate-item--create")
      .find((b) => b.text() === "New set…");
    await create.trigger("click");
    expect(wrapper.emitted("create")).toEqual([[{ characterId: null, name: "" }]]);
    wrapper.unmount();
  });
});
