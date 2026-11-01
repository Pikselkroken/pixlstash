// The Tags field's BY GROUP list (#2469): with the field empty and groups in
// the library, each group is a row that adds its term, a chevron opens its top
// tags, and typing replaces the list with suggestions in which a matching group
// name is one row. Tab never takes the list, so the field is not a trap.

import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

import FilterTagField from "./FilterTagField.vue";
import { useTagGroupsStore } from "../../stores/useTagGroupsStore";

// Most used first, as /tags answers it.
const TAGS = [
  { tag: "hat", count: 12 },
  { tag: "outdoors", count: 7 },
  { tag: "pine", count: 5 },
  { tag: "wolf", count: 3 },
  { tag: "sun", count: 2 },
  { tag: "moss", count: 1 },
  { tag: "zebra", count: 1 },
];

const ROWS = [
  { id: "has", label: "Has tag", short: "has", tone: "has", chips: [] },
  { id: "lacks", label: "Lacks tag", short: "lacks", tone: "lacks", chips: [] },
];

function filedGroups() {
  const store = useTagGroupsStore();
  store.groups = [
    { id: 1, name: "Character", position: 0 },
    { id: 2, name: "Copyright", position: 1 },
  ];
  // hat, wolf, sun, moss are Character (four); outdoors is Copyright; the
  // rest are Ungrouped.
  store.groupOf = new Map([
    ["hat", 1],
    ["wolf", 1],
    ["sun", 1],
    ["moss", 1],
    ["outdoors", 2],
  ]);
  return store;
}

function mountField(props = {}) {
  return mount(FilterTagField, {
    props: { title: "Tags", tags: TAGS, rows: ROWS, grouped: true, ...props },
    global: { stubs: { "v-icon": true } },
  });
}

const options = (wrapper) => wrapper.findAll('[role="option"]');
const labelOf = (option) => option.find(".fm-check-label").text();
const countOf = (option) => option.find(".fm-n").text();
const rowNamed = (wrapper, name) =>
  options(wrapper).find((o) => labelOf(o) === name);

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("FilterTagField: BY GROUP", () => {
  it("lists the groups in order with Ungrouped last, each with its tag count", () => {
    filedGroups();
    const wrapper = mountField();

    expect(wrapper.text()).toContain("By group");
    expect(options(wrapper).map(labelOf)).toEqual([
      "Character",
      "Copyright",
      "Ungrouped",
    ]);
    expect(options(wrapper).map(countOf)).toEqual(["4 tags", "1 tag", "2 tags"]);
  });

  it("adds a group's term to Has on click, and to Lacks on Shift-click", async () => {
    filedGroups();
    const wrapper = mountField();

    await rowNamed(wrapper, "Character").trigger("click");
    await rowNamed(wrapper, "Ungrouped").trigger("click", { shiftKey: true });
    expect(wrapper.emitted("add")).toEqual([
      ["__group:1", "has"],
      ["__group:none", "lacks"],
    ]);
  });

  it("opens a group's top tags with their picture counts, then says how many more", async () => {
    filedGroups();
    const wrapper = mountField();

    // The chevron opens the group and is not the row: no group is added.
    await rowNamed(wrapper, "Character").find(".ftf-twist").trigger("click");
    expect(wrapper.emitted("add")).toBeUndefined();
    expect(options(wrapper).map(labelOf)).toEqual([
      "Character",
      "hat",
      "wolf",
      "sun",
      "Copyright",
      "Ungrouped",
    ]);
    expect(options(wrapper)[1].find(".fm-n").text()).toBe("12");
    expect(wrapper.find(".ftf-more").text()).toBe("1 more · type to narrow");

    // A tag there is picked exactly as a typed suggestion is.
    await rowNamed(wrapper, "wolf").trigger("click");
    await rowNamed(wrapper, "wolf").trigger("click", { shiftKey: true });
    expect(wrapper.emitted("add")).toEqual([
      ["wolf", "has"],
      ["wolf", "lacks"],
    ]);
  });

  it("gives way to suggestions as soon as something is typed, and a group name matches too", async () => {
    filedGroups();
    const wrapper = mountField();
    const field = wrapper.find("input");

    await field.setValue("cop");
    expect(wrapper.text()).not.toContain("By group");
    expect(options(wrapper).map(labelOf)).toEqual(["Copyright"]);
    expect(countOf(options(wrapper)[0])).toBe("1 tag");

    // Enter takes the group row as it takes a tag: Has, and the field clears.
    await field.trigger("keydown", { key: "Enter" });
    expect(wrapper.emitted("add")).toEqual([["__group:2", "has"]]);
    expect(field.element.value).toBe("");

    // A tag match lists beside a group match: "ha" is in Character too.
    await field.setValue("ha");
    expect(options(wrapper).map(labelOf)).toEqual(["Character", "hat"]);
    await field.setValue("ch");
    expect(options(wrapper).map(labelOf)).toEqual(["Character"]);
  });

  it("is operable from the keyboard: arrows move, → opens, Enter picks, Tab is left alone", async () => {
    filedGroups();
    const wrapper = mountField();
    const field = wrapper.find("input");

    await field.trigger("keydown", { key: "ArrowDown" });
    await field.trigger("keydown", { key: "Enter" });
    expect(wrapper.emitted("add")).toEqual([["__group:2", "has"]]);

    // → opens the highlighted group, a second → steps into its first tag.
    await field.trigger("keydown", { key: "ArrowUp" });
    await field.trigger("keydown", { key: "ArrowRight" });
    expect(options(wrapper).map(labelOf)).toContain("hat");
    await field.trigger("keydown", { key: "ArrowRight" });
    await field.trigger("keydown", { key: "Enter", shiftKey: true });
    expect(wrapper.emitted("add")[1]).toEqual(["hat", "lacks"]);

    // Tab with nothing typed moves focus on as usual: not prevented, no pick.
    const tab = new KeyboardEvent("keydown", { key: "Tab", cancelable: true });
    field.element.dispatchEvent(tab);
    expect(tab.defaultPrevented).toBe(false);
    expect(wrapper.emitted("add")).toHaveLength(2);
  });

  it("shows no BY GROUP list when the library has no groups", () => {
    const wrapper = mountField();
    expect(wrapper.text()).not.toContain("By group");
    expect(options(wrapper)).toHaveLength(0);
  });

  it("offers no groups to the Tag confidence field, which is not grouped", () => {
    filedGroups();
    const wrapper = mountField({ grouped: false });
    expect(wrapper.text()).not.toContain("By group");
    expect(options(wrapper)).toHaveLength(0);
  });

  it("shows a group chip's own words, not its raw term", () => {
    filedGroups();
    const wrapper = mountField({
      rows: [
        {
          ...ROWS[0],
          chips: [{ tag: "__group:1", label: "any Character tag" }],
        },
        ROWS[1],
      ],
    });
    expect(wrapper.find(".ftf-chip-name").text()).toBe("any Character tag");
    expect(wrapper.find(".ftf-chip-body").attributes("title")).toBe(
      "any Character tag",
    );
  });
});
