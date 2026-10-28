// The tag field and chips of the grid's rail and of a review card: T's
// field, Enter on all, coverage tinted on several and confidence on one,
// anomalies first, the long tail folded, and the verbs the tag dialog had.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { enableAutoUnmount, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const bulkFetchTags = vi.fn();
const listTagPredictions = vi.fn();
const addPictureTag = vi.fn(async () => ({}));
const removePictureTag = vi.fn(async () => ({}));
const confirmTagPrediction = vi.fn(async () => ({}));
const rejectTagPrediction = vi.fn(async () => ({}));
vi.mock("../../api/tags", () => ({
  bulkFetchTags: (...a) => bulkFetchTags(...a),
  listTagPredictions: (...a) => listTagPredictions(...a),
  addPictureTag: (...a) => addPictureTag(...a),
  removePictureTag: (...a) => removePictureTag(...a),
  confirmTagPrediction: (...a) => confirmTagPrediction(...a),
  rejectTagPrediction: (...a) => rejectTagPrediction(...a),
  listTags: vi.fn(async () => [
    { tag: "filing cabinet", count: 3 },
    { tag: "film grain", count: 412 },
    { tag: "rain", count: 9 },
  ]),
}));
vi.mock("../../api/config", () => ({
  getUserConfig: vi.fn(async () => ({
    smart_score_penalised_tags: ["watermark"],
  })),
}));
vi.mock("../../api/users", () => ({ getPenalisedTags: vi.fn() }));
vi.mock("../../api/taggers", () => ({ listTaggers: vi.fn(async () => ({})) }));
vi.mock("../../api/pictures", () => ({ resetPicturesTags: vi.fn() }));

import RailTags from "./RailTags.vue";
import { useTagClipboardStore } from "../../stores/useTagClipboardStore";
import { useSidebarStore } from "../../stores/useSidebarStore";

enableAutoUnmount(afterEach);

const STUBS = {
  "v-icon": true,
  "v-menu": true,
  Tooltip: { props: ["text"], template: '<span class="tip" :data-text="text" />' },
};

async function flush() {
  for (let i = 0; i < 4; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}

async function mountTags(ids, props = {}) {
  const wrapper = mount(RailTags, {
    props: { ids, ...props },
    global: { stubs: STUBS },
    attachTo: document.body,
  });
  // Several wait out the settle; one reads at once.
  if (ids.length > 1) await new Promise((r) => setTimeout(r, 260));
  await flush();
  return wrapper;
}

const chip = (wrapper, name) => wrapper.find(`.rail-chip[data-tag="${name}"]`);
const tint = (wrapper, name) =>
  Number(chip(wrapper, name).attributes("style").match(/--tint:\s*([\d.]+)/)[1]);

beforeEach(() => {
  setActivePinia(createPinia());
  for (const fn of [
    addPictureTag,
    removePictureTag,
    confirmTagPrediction,
    rejectTagPrediction,
  ]) {
    // Reset, not cleared: a test that swaps in its own implementation
    // cannot leave it to the next one even when it fails half-way.
    fn.mockReset().mockResolvedValue({});
  }
  listTagPredictions.mockReset().mockResolvedValue({
    tag_predictions: [],
    meta: {},
  });
});

describe("several pictures' tags", () => {
  beforeEach(() => {
    bulkFetchTags.mockReset().mockResolvedValue([
      { id: 1, tags: [{ id: 11, tag: "man" }, { id: 12, tag: "photo" }, { id: 13, tag: "desk" }] },
      { id: 2, tags: [{ id: 21, tag: "man" }, { id: 22, tag: "photo" }] },
      { id: 3, tags: [{ id: 31, tag: "man" }, { id: 32, tag: "watermark" }] },
    ]);
  });

  it("tint by coverage, anomalies first, and fold the one-picture tail", async () => {
    const wrapper = await mountTags([1, 2, 3]);
    const names = () =>
      wrapper.findAll(".rail-tags__chips .rail-chip").map((c) => c.attributes("data-tag"));
    expect(names()).toEqual(["watermark", "man", "photo"]);
    expect(chip(wrapper, "watermark").classes()).toContain("rail-chip--anomaly");
    expect(tint(wrapper, "man")).toBe(1);
    expect(tint(wrapper, "photo")).toBeCloseTo(2 / 3);
    expect(chip(wrapper, "man").find(".tip").attributes("data-text")).toBe("On all 3");

    const more = wrapper.find(".rail-tags__more");
    expect(more.text()).toBe("+ 1 more on one picture");
    await more.trigger("click");
    expect(names()).toEqual(["watermark", "man", "photo", "desk"]);
  });

  it("add a part-tinted tag to the rest on a click; × removes and rejects it on all", async () => {
    const wrapper = await mountTags([1, 2, 3]);
    await chip(wrapper, "photo").find(".rail-chip__label").trigger("click");
    await flush();
    expect(addPictureTag.mock.calls.map(([id, tag]) => [id, tag])).toEqual([
      [3, "photo"],
    ]);
    // A tag on all of them has nothing to add: it is no button.
    expect(chip(wrapper, "man").find(".rail-chip__label").exists()).toBe(false);

    await chip(wrapper, "man").find(".rail-chip__x").trigger("click");
    await flush();
    expect(removePictureTag.mock.calls.map(([id, tagId]) => [id, tagId])).toEqual([
      [1, 11],
      [2, 21],
      [3, 31],
    ]);
    // As the lightbox's ×: the tagger is told, in the same undo step.
    expect(rejectTagPrediction.mock.calls.map(([id, tag]) => [id, tag])).toEqual([
      [1, "man"],
      [2, "man"],
      [3, "man"],
    ]);
    const batches = new Set(
      [...removePictureTag.mock.calls, ...rejectTagPrediction.mock.calls].map(
        ([, , opts]) => opts?.batchId,
      ),
    );
    expect([...batches]).toEqual([expect.any(String)]);
  });

  it("take the next tag while the last is still being written", async () => {
    let finish;
    addPictureTag.mockImplementation(() => new Promise((r) => (finish = r)));
    const wrapper = await mountTags([1, 2, 3]);
    const input = wrapper.find(".rail-tags__input");
    await input.setValue("rain");
    await input.trigger("keydown", { key: "Enter" });
    expect(input.element.value).toBe("");
    await input.setValue("se");
    finish({});
    await flush();
    expect(input.element.value).toBe("se");
  });

  it("say who gets a typed tag, and complete it from the library", async () => {
    const wrapper = await mountTags([1, 2, 3]);
    const input = wrapper.find(".rail-tags__input");
    input.element.focus();
    await input.setValue("fil");
    await flush();
    expect(wrapper.find(".rail-tags__to").text()).toBe("to all 3");
    const options = wrapper.findAll('[role="option"]');
    expect(options.map((o) => o.text())).toEqual([
      "film grain412Tab",
      "filing cabinet3",
    ]);
    // The Tab keycap follows the arrowed completion, and back to the first.
    const tabbed = () =>
      wrapper.findAll('[role="option"]').map((o) => o.find("kbd").exists());
    await input.trigger("keydown", { key: "ArrowDown" });
    await input.trigger("keydown", { key: "ArrowDown" });
    expect(tabbed()).toEqual([false, true]);
    await input.trigger("keydown", { key: "Tab" });
    expect(input.element.value).toBe("filing cabinet");
    await input.setValue("fil");
    await flush();
    await input.trigger("keydown", { key: "ArrowDown" });
    await input.trigger("keydown", { key: "ArrowUp" });
    await input.trigger("keydown", { key: "ArrowUp" });
    expect(tabbed()).toEqual([true, false]);
    // Tab completes and writes nothing; Enter then adds it to all.
    await input.trigger("keydown", { key: "Tab" });
    expect(input.element.value).toBe("film grain");
    expect(addPictureTag).not.toHaveBeenCalled();
    await input.trigger("keydown", { key: "Enter" });
    await flush();
    expect(addPictureTag.mock.calls.map(([id, tag]) => [id, tag])).toEqual([
      [1, "film grain"],
      [2, "film grain"],
      [3, "film grain"],
    ]);
    expect(document.activeElement).toBe(input.element);
  });

  it("take Esc a layer at a time: the completions, then the field", async () => {
    const wrapper = await mountTags([1, 2, 3]);
    const input = wrapper.find(".rail-tags__input");
    input.element.focus();
    await input.setValue("fil");
    await flush();
    const grid = vi.fn();
    window.addEventListener("keydown", grid);
    try {
      await input.trigger("keydown", { key: "Escape" });
      expect(wrapper.find('[role="listbox"]').exists()).toBe(false);
      expect(wrapper.emitted("leave")).toBeUndefined();
      await input.trigger("keydown", { key: "Escape" });
      expect(wrapper.emitted("leave")).toHaveLength(1);
      expect(document.activeElement).not.toBe(input.element);
      // Neither press reached the grid, whose Esc clears the selection.
      expect(grid).not.toHaveBeenCalled();
    } finally {
      window.removeEventListener("keydown", grid);
    }
  });

  it("paste the copied tags on all of them", async () => {
    const clipboard = useTagClipboardStore();
    clipboard.clipboardTags = ["rain", "sea"];
    const paste = vi
      .spyOn(clipboard, "pasteClipboard")
      .mockResolvedValue({ pictureIds: [1, 2, 3] });
    const wrapper = await mountTags([1, 2, 3]);
    await wrapper.find('[aria-label="Paste 2 tags on all 3"]').trigger("click");
    await flush();
    expect(paste).toHaveBeenCalledWith([1, 2, 3]);
    expect(wrapper.emitted("tags-applied")[0][0]).toMatchObject({
      pictureIds: [1, 2, 3],
    });
  });
});

describe("one picture's tags", () => {
  beforeEach(() => {
    bulkFetchTags.mockReset().mockResolvedValue([
      { id: 5, tags: [{ id: 51, tag: "desk" }, { id: 52, tag: "photo" }, { id: 53, tag: "mine" }] },
    ]);
    listTagPredictions.mockResolvedValue({
      tag_predictions: [
        { tag: "photo", confidence: 0.9, status: "ACCEPTED", model_version: "t" },
        { tag: "desk", confidence: 0.4, status: "ACCEPTED", model_version: "t" },
        { tag: "mine", confidence: 1, status: "ACCEPTED", model_version: "manual" },
        { tag: "woman", confidence: 0.22, status: "REJECTED", model_version: "t" },
      ],
      meta: {},
    });
  });

  it("tint by the tagger's confidence, surest first, a hand-added tag at full", async () => {
    const wrapper = await mountTags([5]);
    expect(listTagPredictions).toHaveBeenCalledWith(5, {});
    const names = wrapper
      .findAll(".rail-tags__chips .rail-chip--conf")
      .map((c) => c.attributes("data-tag"));
    expect(names).toEqual(["mine", "photo", "desk"]);
    expect(tint(wrapper, "photo")).toBe(0.9);
    expect(tint(wrapper, "mine")).toBe(1);
    expect(chip(wrapper, "mine").find(".tip").attributes("data-text")).toBe(
      "Added by hand",
    );
    // Only the rejected are offered for accepting, with their confidence.
    const rejected = wrapper.findAll(".rail-chip--quiet");
    expect(rejected.map((c) => c.attributes("data-tag"))).toEqual(["woman"]);
    expect(rejected[0].find(".rail-chip__n").text()).toBe("22%");
    await rejected[0].find(".rail-chip__x").trigger("click");
    await flush();
    expect(confirmTagPrediction.mock.calls[0].slice(0, 2)).toEqual([5, "woman"]);
  });

  it("drop the last picture's confidences when this one's read fails", async () => {
    const wrapper = await mountTags([5]);
    expect(tint(wrapper, "photo")).toBe(0.9);
    listTagPredictions.mockRejectedValue(new Error("offline"));
    await wrapper.setProps({ ids: [6] });
    await flush();
    expect(tint(wrapper, "photo")).toBe(1);
    expect(chip(wrapper, "photo").find(".tip").attributes("data-text")).toBe(
      "Added by hand",
    );
  });

  it("take T on a locked picture, so the next T still works", async () => {
    const sidebar = useSidebarStore();
    sidebar.tagFieldPending = true;
    await mountTags([5], { locked: true, takesTagKey: true });
    expect(sidebar.tagFieldPending).toBe(false);
  });

  it("are read-only on a locked picture", async () => {
    const wrapper = await mountTags([5], { locked: true, lockNote: "Locked" });
    expect(wrapper.find(".rail-tags__input").exists()).toBe(false);
    expect(wrapper.find(".rail-chip__x").exists()).toBe(false);
    expect(wrapper.findAll(".rail-chip--conf")).toHaveLength(3);
  });
});
