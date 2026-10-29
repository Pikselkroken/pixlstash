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
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";

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
    // A tag on all of them has nothing to add: its click selects it.
    const man = chip(wrapper, "man").find(".rail-chip__label");
    expect(man.attributes("aria-label")).toBeUndefined();
    expect(man.attributes("aria-pressed")).toBe("false");

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
    // Only the rejected are offered for accepting, with their confidence;
    // one under 25% waits behind a link.
    expect(wrapper.findAll(".rail-chip--quiet")).toHaveLength(0);
    const faint = wrapper.find(".rail-tags__rejected .rail-tags__more");
    expect(faint.text()).toBe("+ 1 under 25%");
    await faint.trigger("click");
    const rejected = wrapper.findAll(".rail-chip--quiet");
    expect(rejected.map((c) => c.attributes("data-tag"))).toEqual(["woman"]);
    expect(rejected[0].find(".rail-chip__n").text()).toBe("22%");
    expect(faint.text()).toBe("Hide those under 25%");
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

describe("selecting tags", () => {
  beforeEach(() => {
    bulkFetchTags.mockReset().mockResolvedValue([
      { id: 5, tags: [{ id: 51, tag: "rain" }, { id: 52, tag: "coat" }, { id: 53, tag: "desk" }] },
    ]);
    listTagPredictions.mockResolvedValue({
      tag_predictions: [
        { tag: "umbrella", confidence: 0.6, status: "REJECTED", model_version: "t" },
      ],
      meta: {},
    });
  });

  const label = (wrapper, name) => chip(wrapper, name).find(".rail-chip__label");
  const selected = (wrapper) =>
    wrapper.findAll(".rail-chip--selected").map((c) => c.attributes("data-tag"));
  const bar = (wrapper) => wrapper.find(".rail-tags__selbar");
  const press = (key, init = {}, target = window) => {
    const event = new KeyboardEvent("keydown", {
      key,
      bubbles: true,
      cancelable: true,
      ...init,
    });
    target.dispatchEvent(event);
    return event;
  };

  it("a click selects a chip and a second deselects it; nothing is copied", async () => {
    const wrapper = await mountTags([5]);
    const store = useTagClipboardStore();
    await label(wrapper, "rain").trigger("click");
    expect(selected(wrapper)).toEqual(["rain"]);
    expect(label(wrapper, "rain").attributes("aria-pressed")).toBe("true");
    expect(bar(wrapper).text()).toContain("1 selected");
    await label(wrapper, "rain").trigger("click");
    expect(selected(wrapper)).toEqual([]);
    expect(bar(wrapper).exists()).toBe(false);
    expect(store.clipboardTags).toEqual([]);
  });

  it("Copy copies the selection, rejected chips too, in the order picked", async () => {
    const wrapper = await mountTags([5]);
    const store = useTagClipboardStore();
    await label(wrapper, "desk").trigger("click");
    await label(wrapper, "umbrella").trigger("click");
    await chip(wrapper, "rain").trigger("contextmenu");
    await bar(wrapper).find('[aria-label="Copy selected tags"]').trigger("click");
    expect(store.clipboardTags).toEqual(["desk", "umbrella", "rain"]);
    await bar(wrapper).find('[aria-label="Clear selection"]').trigger("click");
    expect(selected(wrapper)).toEqual([]);
  });

  it("Copy all rejected tags copies every rejected chip", async () => {
    const wrapper = await mountTags([5]);
    const store = useTagClipboardStore();
    await wrapper.find('[aria-label="Copy all rejected tags"]').trigger("click");
    expect(store.clipboardTags).toEqual(["umbrella"]);
  });

  it("the × of a selected chip removes the selection in one step, never a rejected chip", async () => {
    const wrapper = await mountTags([5]);
    for (const name of ["rain", "coat", "umbrella"]) {
      await label(wrapper, name).trigger("click");
    }
    await chip(wrapper, "rain").find(".rail-chip__x").trigger("mouseenter");
    expect(
      wrapper.findAll(".rail-chip--remove-hot").map((c) => c.attributes("data-tag")),
    ).toEqual(["coat", "rain"]);
    expect(chip(wrapper, "rain").find(".rail-chip__x").attributes("aria-label")).toBe(
      "Remove 2 selected tags (Del)",
    );
    await chip(wrapper, "rain").find(".rail-chip__x").trigger("click");
    await flush();
    expect(removePictureTag.mock.calls.map(([id, tagId]) => [id, tagId])).toEqual([
      [5, 52],
      [5, 51],
    ]);
    expect(rejectTagPrediction.mock.calls.map(([, tag]) => tag)).toEqual(["coat", "rain"]);
    const batches = new Set(
      [...removePictureTag.mock.calls, ...rejectTagPrediction.mock.calls].map(
        ([, , opts]) => opts?.batchId,
      ),
    );
    expect(batches.size).toBe(1);
  });

  it("the × of a chip outside the selection removes that chip alone", async () => {
    const wrapper = await mountTags([5]);
    await label(wrapper, "rain").trigger("click");
    await chip(wrapper, "desk").find(".rail-chip__x").trigger("mouseenter");
    expect(wrapper.findAll(".rail-chip--remove-hot")).toHaveLength(0);
    await chip(wrapper, "desk").find(".rail-chip__x").trigger("click");
    await flush();
    expect(removePictureTag.mock.calls.map(([, tagId]) => tagId)).toEqual([53]);
  });

  it("drops the selection when the picture changes", async () => {
    const wrapper = await mountTags([5]);
    await label(wrapper, "rain").trigger("click");
    await wrapper.setProps({ ids: [6] });
    await flush();
    expect(bar(wrapper).exists()).toBe(false);
  });

  it("takes Escape, Delete and Ctrl+C before the page's own keys", async () => {
    const wrapper = await mountTags([5]);
    const store = useTagClipboardStore();
    const page = vi.fn();
    window.addEventListener("keydown", page);
    try {
      await label(wrapper, "rain").trigger("click");
      await label(wrapper, "umbrella").trigger("click");
      expect(press("c", { ctrlKey: true }).defaultPrevented).toBe(true);
      expect(store.clipboardTags).toEqual(["rain", "umbrella"]);
      // Delete removes the tags, and the grid never sees it (its Delete
      // sends pictures to the Scrapheap).
      expect(press("Delete").defaultPrevented).toBe(true);
      await flush();
      expect(removePictureTag.mock.calls.map(([, tagId]) => tagId)).toEqual([51]);
      await label(wrapper, "coat").trigger("click");
      expect(press("Escape").defaultPrevented).toBe(true);
      await flush();
      expect(selected(wrapper)).toEqual([]);
      expect(page).not.toHaveBeenCalled();
      // With nothing selected the keys are the page's again.
      press("Escape");
      expect(page).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener("keydown", page);
    }
  });

  it("leaves Escape and Delete to the tag field", async () => {
    const wrapper = await mountTags([5]);
    await label(wrapper, "rain").trigger("click");
    const input = wrapper.find(".rail-tags__input").element;
    input.focus();
    press("Delete", {}, input);
    press("Escape", {}, input);
    await flush();
    expect(selected(wrapper)).toEqual(["rain"]);
    expect(removePictureTag).not.toHaveBeenCalled();
  });

  it("with only rejected chips selected, Delete removes nothing and keeps them", async () => {
    const wrapper = await mountTags([5]);
    await label(wrapper, "umbrella").trigger("click");
    press("Delete");
    await flush();
    expect(removePictureTag).not.toHaveBeenCalled();
    expect(selected(wrapper)).toEqual(["umbrella"]);
  });

  it("dragging a tag onto Rejected rejects it; a rejected one onto Tags accepts it", async () => {
    const wrapper = await mountTags([5]);
    const dataTransfer = { setData: vi.fn(), effectAllowed: "" };
    await chip(wrapper, "rain").trigger("dragstart", { dataTransfer });
    // Its own list is no target.
    const own = new Event("dragover", { cancelable: true });
    wrapper.find(".rail-tags__applied").element.dispatchEvent(own);
    expect(own.defaultPrevented).toBe(false);
    await wrapper.find(".rail-tags__rejected").trigger("dragover");
    expect(wrapper.find(".rail-tags__rejected").classes()).toContain(
      "rail-tags__body--drop",
    );
    await wrapper.find(".rail-tags__rejected").trigger("drop");
    await flush();
    expect(removePictureTag.mock.calls.map(([, tagId]) => tagId)).toEqual([51]);
    expect(rejectTagPrediction.mock.calls.map(([, tag]) => tag)).toEqual(["rain"]);

    await chip(wrapper, "umbrella").trigger("dragstart", { dataTransfer });
    await wrapper.find(".rail-tags__applied").trigger("dragover");
    await wrapper.find(".rail-tags__applied").trigger("drop");
    await flush();
    expect(confirmTagPrediction.mock.calls.map(([id, tag]) => [id, tag])).toEqual([
      [5, "umbrella"],
    ]);
  });

  it("never drags on a locked picture", async () => {
    const wrapper = await mountTags([5], { locked: true });
    expect(chip(wrapper, "rain").attributes("draggable")).toBeUndefined();
  });

  it("marks a detected tag and says when it is pointed at", async () => {
    const wrapper = await mountTags([5], { boxedTags: new Set(["coat"]) });
    expect(chip(wrapper, "coat").find(".rail-chip__box").exists()).toBe(true);
    expect(chip(wrapper, "rain").find(".rail-chip__box").exists()).toBe(false);
    await chip(wrapper, "rain").trigger("mouseenter");
    expect(wrapper.emitted("hover-tag")).toBeUndefined();
    await chip(wrapper, "coat").trigger("mouseenter");
    await chip(wrapper, "coat").trigger("mouseleave");
    expect(wrapper.emitted("hover-tag")).toEqual([["coat"], [null]]);
  });

  it("hides the reader's hidden tags while their filter is on, but not one just added", async () => {
    const prefs = useUserPrefsStore();
    prefs.hiddenTags = ["Desk", "lamp"];
    prefs.applyTagFilter = true;
    const wrapper = await mountTags([5]);
    expect(chip(wrapper, "desk").exists()).toBe(false);
    expect(chip(wrapper, "rain").exists()).toBe(true);

    bulkFetchTags.mockResolvedValue([
      { id: 5, tags: [{ id: 51, tag: "rain" }, { id: 54, tag: "lamp" }] },
    ]);
    await wrapper.find(".rail-tags__input").setValue("lamp");
    await wrapper.find(".rail-tags__input").trigger("keydown", { key: "Enter" });
    await flush();
    expect(chip(wrapper, "lamp").exists()).toBe(true);

    prefs.applyTagFilter = false;
    await flush();
    expect(chip(wrapper, "desk").exists()).toBe(false);
    expect(chip(wrapper, "lamp").exists()).toBe(true);
  });
});

describe("selecting several pictures' tags", () => {
  beforeEach(() => {
    bulkFetchTags.mockReset().mockResolvedValue([
      { id: 1, tags: [{ id: 11, tag: "man" }, { id: 12, tag: "photo" }] },
      { id: 2, tags: [{ id: 21, tag: "man" }] },
    ]);
  });

  it("a modifier selects a part-tinted chip instead of adding it to the rest", async () => {
    const wrapper = await mountTags([1, 2]);
    const photo = chip(wrapper, "photo").find(".rail-chip__label");
    await photo.trigger("click", { ctrlKey: true });
    await photo.trigger("click", { shiftKey: true });
    await photo.trigger("click", { metaKey: true });
    expect(addPictureTag).not.toHaveBeenCalled();
    expect(chip(wrapper, "photo").classes()).toContain("rail-chip--selected");
    await chip(wrapper, "man").find(".rail-chip__label").trigger("click");
    expect(wrapper.find(".rail-tags__selbar").text()).toContain("2 selected");
  });
});
