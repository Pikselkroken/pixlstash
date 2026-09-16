// The filter menu's own rules, which the strip then shows: a tag is required or
// excluded but never both, raising the minimum score drags the maximum up, a
// tag-confidence threshold is one chip per tag that a second pick moves or
// removes, and every count is the view plus exactly one filter.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

import FilterMenu from "./FilterMenu.vue";
import { useFilterStore } from "../../stores/useFilterStore.js";
import { getPictureCount } from "../../api/pictures";

vi.mock("../../api/tags", () => ({
  listTags: vi.fn().mockResolvedValue([
    { tag: "hat", count: 12 },
    { tag: "outdoors", count: 7 },
  ]),
}));
vi.mock("../../api/pictures", () => ({
  listComfyuiModels: vi.fn().mockResolvedValue(["flux1-dev.safetensors"]),
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

  it("keeps one confidence chip per tag: a new threshold moves it, the same one removes it", async () => {
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
    expect(store.tagConfidenceAboveFilter).toEqual([]);
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

  it("toggles a picked pick-one choice back off", async () => {
    const store = useFilterStore();
    const wrapper = await mountMenu();
    await openKind(wrapper, "Stacks");
    const stacked = () =>
      wrapper
        .findAll('[role="radio"]')
        .find((b) => b.text().startsWith("Stacked"));
    await stacked().trigger("click");
    expect(store.stackStateFilter).toBe("stacked");
    await stacked().trigger("click");
    expect(store.stackStateFilter).toBe("all");
  });
});
