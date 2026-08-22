// The library picture picker.
//
// The four things worth guarding are the ones the plan's §3 fixtures name and
// the ones a later caller would silently break: the facets are the vault's own
// groupings and really scope the read, the selection is single, search is the
// escape hatch (a different endpoint, same scope), and a paste says so and is
// selectable once the import lands.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

vi.mock("vuetify/components", () => ({
  VIcon: { name: "v-icon", template: "<i><slot /></i>" },
  VDialog: { name: "v-dialog", template: "<div><slot /></div>" },
}));

const streamPictures = vi.fn();
const searchPictures = vi.fn();
const getPictureCount = vi.fn();

vi.mock("../../api/pictures", () => ({
  streamPictures: (...a) => streamPictures(...a),
  searchPictures: (...a) => searchPictures(...a),
  getPictureCount: (...a) => getPictureCount(...a),
  pictureThumbnailUrl: (id) => `/api/v1/pictures/thumbnails/${id}.webp`,
}));

// The picker reads the shared entity lists rather than inventing its own
// grouping, so the doubles go in at the api layer the store fetches from.
vi.mock("../../api/characters", () => ({
  listCharacters: vi.fn().mockResolvedValue([
    { id: 7, name: "Clementine", image_count: 1029 },
    { id: 8, name: "Sarah", image_count: 160 },
  ]),
}));
vi.mock("../../api/pictureSets", () => ({
  listPictureSets: vi.fn().mockResolvedValue([
    { id: 3, name: "Good detail", picture_count: 97 },
    // A reference set is machinery, not a grouping anyone chose: the sidebar
    // hides it and so does this.
    { id: 4, name: "ref", picture_count: 5, reference_character: 7 },
  ]),
}));
vi.mock("../../api/projects", () => ({
  listProjects: vi.fn().mockResolvedValue([
    { id: 2, name: "Personal", image_count: 9118 },
  ]),
}));

import PicturePicker from "./PicturePicker.vue";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useTasksStore } from "../../stores/useTasksStore";

function batch(ids, { done = true } = {}) {
  return {
    pictures: ids.map((id) => ({ id, file_name: `p${id}.png` })),
    done,
    next_offset: ids.length,
  };
}

/** The query string the last stream call was made with. */
function lastStreamQuery() {
  return new URLSearchParams(streamPictures.mock.calls.at(-1)[0]);
}

async function mountPicker() {
  const w = mount(PicturePicker, { props: { open: true, subtitle: "for Cyanwood" } });
  // The open watcher fires the list read, the count and the three entity
  // refreshes; let all of them settle before asserting on what is drawn.
  await flush(w);
  return w;
}

async function flush(w) {
  for (let i = 0; i < 6; i += 1) {
    await Promise.resolve();
    await w.vm.$nextTick();
  }
}

beforeEach(() => {
  setActivePinia(createPinia());
  streamPictures.mockReset().mockResolvedValue(batch([1, 2, 3]));
  searchPictures.mockReset().mockResolvedValue([{ id: 9, file_name: "p9.png" }]);
  getPictureCount.mockReset().mockResolvedValue({ count: 28172 });
});

describe("the facets", () => {
  it("are the vault's own groupings, and the reference set is not one", async () => {
    const w = await mountPicker();
    const labels = w.findAll(".pp-facet__label").map((n) => n.text());
    expect(labels).toContain("Everything");
    expect(labels).toContain("Personal");
    expect(labels).toContain("Clementine");
    expect(labels).toContain("Good detail");
    expect(labels).not.toContain("ref");
  });

  it("scopes the read to the one that was picked", async () => {
    const w = await mountPicker();
    expect(lastStreamQuery().get("character_id")).toBe(null);

    const clementine = w
      .findAll(".pp-facet")
      .find((b) => b.text().includes("Clementine"));
    await clementine.trigger("click");
    await flush(w);

    expect(lastStreamQuery().get("character_id")).toBe("7");
    // One facet at a time: the previous scope is replaced, never added to.
    expect(lastStreamQuery().get("project_id")).toBe(null);
  });
});

describe("choosing", () => {
  it("keeps exactly one picture chosen", async () => {
    const w = await mountPicker();
    const cells = () => w.findAll(".pp-cell");
    await cells()[0].trigger("click");
    await cells()[2].trigger("click");
    expect(w.findAll(".pp-cell--on")).toHaveLength(1);
    expect(cells()[2].classes()).toContain("pp-cell--on");
  });

  it("emits the picture itself, once, and only when one is chosen", async () => {
    const w = await mountPicker();
    const use = () =>
      w.findAll("button").find((b) => b.text().includes("Use this picture"));
    expect(use().attributes("disabled")).toBeDefined();

    await w.findAll(".pp-cell")[1].trigger("click");
    await use().trigger("click");
    expect(w.emitted("pick")).toHaveLength(1);
    expect(w.emitted("pick")[0][0].id).toBe(2);
  });
});

describe("search", () => {
  it("is the escape hatch: a different endpoint, the same scope", async () => {
    const w = await mountPicker();
    const clementine = w
      .findAll(".pp-facet")
      .find((b) => b.text().includes("Clementine"));
    await clementine.trigger("click");
    await flush(w);

    await w.find(".pp-search input").setValue("red dress");
    await w.find(".pp-search input").trigger("keydown", { key: "Enter" });
    await flush(w);

    expect(searchPictures).toHaveBeenCalledWith("red dress", {
      query: "character_id=7",
    });
    expect(w.findAll(".pp-cell")).toHaveLength(1);
  });
});

describe("the keyboard", () => {
  it("drops the previous choice when the list changes under it", async () => {
    // AppDialog accepts on plain Enter from a single-line input, and a search
    // field is one — so Enter in the search box both searches and reaches the
    // dialog's accept. What stops that confirming a tile the reader has stopped
    // looking at is the reload dropping the choice, not the key.
    const w = await mountPicker();
    await w.findAll(".pp-cell")[0].trigger("click");
    expect(w.findAll(".pp-cell--on")).toHaveLength(1);

    await w.find(".pp-search input").setValue("red dress");
    await w.find(".pp-search input").trigger("keydown", { key: "Enter" });
    await flush(w);

    expect(searchPictures).toHaveBeenCalled();
    expect(w.findAll(".pp-cell--on")).toHaveLength(0);
    expect(w.emitted("pick")).toBeFalsy();
  });
});

describe("paste", () => {
  function pasteEvent(file) {
    const event = new Event("paste");
    event.clipboardData = {
      items: [{ kind: "file", type: "image/png", getAsFile: () => file }],
    };
    return event;
  }

  it("says the picture is being filed, rather than filing it in silence", async () => {
    const w = await mountPicker();
    window.dispatchEvent(
      pasteEvent(new File(["x"], "shot.png", { type: "image/png" })),
    );
    await flush(w);
    expect(useNoticeStore().notices.at(-1).text).toMatch(/Importing the pasted/);
    w.unmount();
  });

  it("reloads once the import finishes, so what was pasted is selectable", async () => {
    const w = await mountPicker();
    const tasks = useTasksStore();
    window.dispatchEvent(
      pasteEvent(new File(["x"], "shot.png", { type: "image/png" })),
    );
    await flush(w);

    const before = streamPictures.mock.calls.length;
    tasks.setImportRun("run-1", { status: "running" });
    await flush(w);
    expect(streamPictures.mock.calls.length).toBe(before);

    streamPictures.mockResolvedValue(batch([42, 1, 2, 3]));
    tasks.clearImportRun("run-1");
    await flush(w);
    expect(streamPictures.mock.calls.length).toBeGreaterThan(before);
    expect(w.findAll(".pp-cell")).toHaveLength(4);
    w.unmount();
  });

  it("leaves a paste into the search field alone — that is a search", async () => {
    const w = await mountPicker();
    const event = pasteEvent(new File(["x"], "shot.png", { type: "image/png" }));
    Object.defineProperty(event, "target", {
      value: w.find(".pp-search input").element,
    });
    window.dispatchEvent(event);
    await flush(w);
    expect(useNoticeStore().notices).toHaveLength(0);
    w.unmount();
  });
});
