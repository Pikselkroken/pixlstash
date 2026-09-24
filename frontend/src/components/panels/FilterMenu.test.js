// The filter menu's own rules, which the strip then shows: a tag is required or
// excluded but never both, raising the minimum score drags the maximum up, a
// tag-confidence threshold is one chip per tag that another pick moves, every
// pick-one list follows the radiogroup contract (arrows select, one tab stop),
// its leading All/Any row puts each kind back to its own "off" value, and every
// count is the view plus exactly one filter.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

import FilterMenu from "./FilterMenu.vue";
import { useFilterStore } from "../../stores/useFilterStore.js";
import { getPictureCount } from "../../api/pictures";
import { resetFilterCounts } from "../../composables/useFilterCounts";

vi.mock("../../api/tags", () => ({
  listTags: vi.fn().mockResolvedValue([
    { tag: "hat", count: 12 },
    { tag: "outdoors", count: 7 },
  ]),
}));
vi.mock("../../api/pictures", () => ({
  listComfyuiModels: vi
    .fn()
    .mockResolvedValue([{ value: "flux1-dev.safetensors", name: null }]),
  listComfyuiLoras: vi.fn().mockResolvedValue([]),
  getPictureCount: vi.fn().mockResolvedValue({ count: 3 }),
}));

async function mountMenu() {
  const wrapper = mount(FilterMenu, {
    props: { countBaseQuery: "set_id=4", open: true },
    global: { stubs: { "v-icon": true, Tooltip: true } },
  });
  await flushPromises();
  return wrapper;
}

async function openKind(wrapper, label) {
  const row = wrapper
    .findAll("button.fm-row")
    .find((b) => b.find(".fm-row-label").text() === label);
  expect(row, `no row "${label}"`).toBeTruthy();
  await row.trigger("click");
  await flushPromises();
}

function checkbox(wrapper, label) {
  const row = wrapper
    .findAll("label.fm-check")
    .find((l) => l.find(".fm-check-label").text() === label);
  expect(row, `no checkbox "${label}"`).toBeTruthy();
  return row.find("input");
}

beforeEach(() => {
  setActivePinia(createPinia());
  getPictureCount.mockClear();
  resetFilterCounts();
});

describe("FilterMenu", () => {
  it("shows no counts during a search, where the view cannot be counted", async () => {
    const wrapper = mount(FilterMenu, {
      props: { countBaseQuery: null, open: true },
      global: { stubs: { "v-icon": true, Tooltip: true } },
    });
    await flushPromises();
    await openKind(wrapper, "Faces");
    expect(getPictureCount).not.toHaveBeenCalled();
    expect(wrapper.find(".fm-sub .fm-n").text()).toBe("");
  });

  it("moves a tag between Has and Lacks instead of holding it in both", async () => {
    const store = useFilterStore();
    const wrapper = await mountMenu();
    await openKind(wrapper, "Tags");

    await checkbox(wrapper, "hat").setValue(true);
    expect(store.tagFilter).toEqual(["hat"]);

    const lacks = wrapper
      .findAll('[role="radio"]')
      .find((b) => b.text() === "Lacks tag");
    await lacks.trigger("click");
    await checkbox(wrapper, "hat").setValue(true);
    expect(store.tagRejectedFilter).toEqual(["hat"]);
    expect(store.tagFilter).toEqual([]);
  });

  it("raises At most to a new At least above it", async () => {
    const store = useFilterStore();
    store.maxScoreFilter = 2;
    const wrapper = await mountMenu();
    await openKind(wrapper, "Score");

    await wrapper.find('[aria-label="At least 4 stars"]').trigger("click");
    expect(store.minScoreFilter).toBe(4);
    expect(store.maxScoreFilter).toBe(4);
    expect(
      wrapper.find('[aria-label="At most 3 stars"]').attributes("disabled"),
    ).toBeDefined();
  });

  it("keeps one confidence chip per tag: another threshold moves it", async () => {
    const store = useFilterStore();
    const wrapper = await mountMenu();
    await openKind(wrapper, "Tag confidence");

    const pick = async (label) => {
      const row = wrapper
        .findAll('[role="radio"]')
        .find((b) => b.text().startsWith(label));
      await row.trigger("click");
      await flushPromises();
    };
    await pick("hat");
    await pick("80%");
    expect(store.tagConfidenceAboveFilter).toEqual(["hat:0.80"]);
    await pick("90%");
    expect(store.tagConfidenceAboveFilter).toEqual(["hat:0.90"]);
    await pick("90%");
    expect(store.tagConfidenceAboveFilter).toEqual(["hat:0.90"]);
  });

  it("counts each choice as the view plus that one filter", async () => {
    const store = useFilterStore();
    store.tagFilter = ["outdoors"];
    const wrapper = await mountMenu();
    await openKind(wrapper, "Faces");

    const queries = getPictureCount.mock.calls.map(([q]) => q);
    expect(queries).toContain("set_id=4");
    expect(queries).toContain("set_id=4&face_filter=with_face");
    // The other active filters stay out of a row's count.
    expect(queries.some((q) => q.includes("tag="))).toBe(false);
    expect(wrapper.find(".fm-sub .fm-n").text()).toBe("3");
  });

  // One case per "off" sentinel: "all", null and false. A coerced sentinel is
  // how a pick-one filter ends up stuck on or silently off.
  it.each([
    ["Media", "mediaTypeFilter", "Images", "images", "all"],
    ["Faces", "faceBboxFilter", "Has face", "with_face", null],
    ["Stacks", "stackStateFilter", "Stacked", "stacked", "all"],
    ["Sharing", "sharedOnlyFilter", "Shared", true, false],
  ])(
    "%s: arrow selects, Enter keeps it, the off row restores the off value",
    async (kind, field, label, on, off) => {
      const store = useFilterStore();
      const wrapper = await mountMenu();
      await openKind(wrapper, kind);
      const group = wrapper.find('.fm-sub [role="radiogroup"]');
      const row = () =>
        wrapper
          .findAll('[role="radio"]')
          .find((b) => b.text().startsWith(label));

      await group.trigger("keydown", { key: "ArrowDown" });
      expect(store[field]).toBe(on);
      // Enter on the chosen row is a click on it: it confirms, never undoes.
      await row().trigger("click");
      expect(store[field]).toBe(on);

      const radios = group.findAll('[role="radio"]');
      expect(radios[0].attributes("aria-checked")).toBe("false");
      await radios[0].trigger("click");
      expect(store[field]).toBe(off);
      expect(radios[0].attributes("aria-checked")).toBe("true");
    },
  );

  it("leads each pick-one list with its off row, All or Any", async () => {
    const wrapper = await mountMenu();
    for (const [kind, any] of [
      ["Media", "All"],
      ["Faces", "Any"],
      ["Stacks", "All"],
      ["Sharing", "All"],
    ]) {
      await openKind(wrapper, kind);
      const first = wrapper.find('.fm-sub [role="radio"]');
      expect(first.find(".optrow__label").text()).toBe(any);
      expect(first.attributes("aria-checked")).toBe("true");
      expect(wrapper.find(".fm-sub .tbm-footer").exists()).toBe(false);
    }
  });

  it("marks each Score row's radio from its own group's value", async () => {
    const store = useFilterStore();
    store.minScoreFilter = 3;
    store.maxScoreFilter = 4;
    const wrapper = mount(FilterMenu, {
      props: { countBaseQuery: "set_id=4", open: true },
      // Renders the icon name, which the `true` stub drops.
      global: { stubs: { "v-icon": { template: "<i><slot /></i>" } } },
    });
    await flushPromises();
    await openKind(wrapper, "Score");
    const marked = (group) =>
      group
        .findAll('[role="radio"]')
        .filter((b) => b.text().includes("mdi-radiobox-marked"))
        .map((b) => b.attributes("aria-label"));

    const [atLeast, atMost] = wrapper.findAll('.fm-sub [role="radiogroup"]');
    expect(marked(atLeast)).toEqual(["At least 3 stars"]);
    expect(marked(atMost)).toEqual(["At most 4 stars"]);
  });

  // 0 stars is unrated: a range from 0 takes the unrated with it, At most 0 is
  // them alone, and 0 to 5 is no filter at all.
  it("reads 0 stars as unrated, with no separate unscored switch", async () => {
    const store = useFilterStore();
    const wrapper = await mountMenu();
    await openKind(wrapper, "Score");
    const checked = () =>
      wrapper
        .findAll('.fm-sub [role="radio"][aria-checked="true"]')
        .map((b) => b.attributes("aria-label"));
    const click = (label) =>
      wrapper.find(`[aria-label="${label}"]`).trigger("click");
    const state = () => [
      store.minScoreFilter,
      store.maxScoreFilter,
      store.unscoredOnlyFilter,
    ];

    expect(checked()).toEqual(["At least 0 stars", "At most 5 stars"]);
    expect(wrapper.text()).not.toContain("Include unscored");

    await click("At most 0 stars");
    expect(state()).toEqual([null, 0, true]);
    await click("At most 3 stars");
    expect(state()).toEqual([null, 3, true]);
    await click("At least 2 stars");
    expect(state()).toEqual([2, 3, false]);
    await click("At least 0 stars");
    await click("At most 5 stars");
    expect(state()).toEqual([null, null, false]);
    expect(checked()).toEqual(["At least 0 stars", "At most 5 stars"]);
  });

  it("shows the stats sidebar's unrated-only filter as At most 0", async () => {
    const store = useFilterStore();
    store.unscoredOnlyFilter = true;
    const wrapper = await mountMenu();
    await openKind(wrapper, "Score");
    expect(
      wrapper
        .find('[aria-label="At most 0 stars"]')
        .attributes("aria-checked"),
    ).toBe("true");

    // Clicking the marked At least 0 confirms it, never drops the filter.
    await wrapper.find('[aria-label="At least 0 stars"]').trigger("click");
    expect(store.unscoredOnlyFilter).toBe(true);
    expect(store.minScoreFilter).toBeNull();
    expect(
      wrapper
        .find('[aria-label="At most 0 stars"]')
        .attributes("aria-checked"),
    ).toBe("true");

    // A minimum above the shown At most 0 drags it up, as it would anywhere.
    await wrapper.find('[aria-label="At least 2 stars"]').trigger("click");
    expect([
      store.minScoreFilter,
      store.maxScoreFilter,
      store.unscoredOnlyFilter,
    ]).toEqual([2, 2, false]);
  });

  it("gives each Score radiogroup one tab stop, moved and selected by arrows", async () => {
    const store = useFilterStore();
    const wrapper = await mountMenu();
    await openKind(wrapper, "Score");
    const [atLeast] = wrapper.findAll('.fm-sub [role="radiogroup"]');
    const stops = () =>
      atLeast
        .findAll('[role="radio"]')
        .filter((b) => b.attributes("tabindex") === "0");

    expect(stops()).toHaveLength(1);
    await atLeast.trigger("keydown", { key: "ArrowDown" });
    expect(store.minScoreFilter).toBe(1);
    await atLeast.trigger("keydown", { key: "ArrowDown" });
    expect(store.minScoreFilter).toBe(2);
    expect(stops()).toHaveLength(1);
    expect(stops()[0].attributes("aria-label")).toBe("At least 2 stars");
  });

  it("opens a submenu with focus in its body, not on Clear", async () => {
    const store = useFilterStore();
    store.stackStateFilter = "stacked";
    const wrapper = mount(FilterMenu, {
      props: { countBaseQuery: "set_id=4", open: true },
      global: { stubs: { "v-icon": true, Tooltip: true } },
      attachTo: document.body,
    });
    await flushPromises();
    await openKind(wrapper, "Stacks");
    expect(document.activeElement?.getAttribute("role")).toBe("radio");
    wrapper.unmount();
  });
});
