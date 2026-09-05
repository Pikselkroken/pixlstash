// The library picture picker.
//
// What is worth guarding is what the plan's §3 fixtures name plus what a later
// caller would silently break: the facets are the vault's own groupings and
// really scope the read, the selection is single and a refused tile can never
// stand in for it, search is the escape hatch (a different endpoint, same
// scope) and is capped where the route imposes no ceiling of its own, and a
// picture imported while the picker is open becomes selectable without the
// reader's facet, search or choice being thrown away.
//
// The picker deliberately says NOTHING about a paste - `ImageImporter`
// announces the import from inside it, which is the only place that knows
// whether it happened. See the Paste note in the component.

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
const listCharacters = vi.fn();
const listPictureSets = vi.fn();
const listProjects = vi.fn();
vi.mock("../../api/characters", () => ({
  listCharacters: (...a) => listCharacters(...a),
}));
vi.mock("../../api/pictureSets", () => ({
  listPictureSets: (...a) => listPictureSets(...a),
}));
vi.mock("../../api/projects", () => ({
  listProjects: (...a) => listProjects(...a),
}));

import PicturePicker from "./PicturePicker.vue";
import { useTasksStore } from "../../stores/useTasksStore";

function batch(ids, { done = true } = {}) {
  return {
    pictures: ids.map((id) => ({ id, file_path: `/pics/p${id}.png` })),
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
  searchPictures.mockReset().mockResolvedValue([{ id: 9, file_path: "/p/p9.png" }]);
  getPictureCount.mockReset().mockResolvedValue({ count: 28172 });
  listCharacters.mockReset().mockResolvedValue([
    { id: 7, name: "Clementine", image_count: 1029 },
    { id: 8, name: "Sarah", image_count: 160 },
  ]);
  listPictureSets.mockReset().mockResolvedValue([
    { id: 3, name: "Good detail", picture_count: 97 },
    // A reference set is machinery, not a grouping anyone chose: the sidebar
    // hides it and so does this.
    { id: 4, name: "ref", picture_count: 5, reference_character: 7 },
  ]);
  listProjects
    .mockReset()
    .mockResolvedValue([{ id: 2, name: "Personal", image_count: 9118 }]);
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
    // field is one - so Enter in the search box both searches and reaches the
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

describe("an import finishing while the picker is open", () => {
  it("re-reads the list, so what was just pasted is selectable", async () => {
    // The picker does not handle the paste and says nothing about it - the
    // window importer and `ImageImporter` own that, and announce it from
    // inside the import where the truth is. What IS this component's business
    // is that the result becomes selectable without reopening the dialog.
    const w = await mountPicker();
    const tasks = useTasksStore();
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

  it("does not throw away the facet, the search or the choice", async () => {
    // An import finishing is not a reason to undo what the reader has been
    // doing while they waited - and any import may be one they never started.
    const w = await mountPicker();
    const tasks = useTasksStore();
    const clementine = w
      .findAll(".pp-facet")
      .find((b) => b.text().includes("Clementine"));
    await clementine.trigger("click");
    await flush(w);
    await w.findAll(".pp-cell")[0].trigger("click");

    tasks.setImportRun("run-1", { status: "running" });
    await flush(w);
    tasks.clearImportRun("run-1");
    await flush(w);

    expect(lastStreamQuery().get("character_id")).toBe("7");
    expect(w.findAll(".pp-cell--on")).toHaveLength(1);
    w.unmount();
  });
});

describe("the ceilings, and saying so", () => {
  it("cuts a runaway search and says it cut it", async () => {
    // `/pictures/search` ignores `top_n` and defaults its limit to
    // `sys.maxsize`, and this grid is not virtualised - so the cap is applied
    // here, and a silent one would read as "that is all there is".
    searchPictures.mockResolvedValue(
      Array.from({ length: 500 }, (_, i) => ({ id: 1000 + i })),
    );
    const w = await mountPicker();
    await w.find(".pp-search input").setValue("a");
    await w.find(".pp-search input").trigger("keydown", { key: "Enter" });
    await flush(w);

    expect(w.findAll(".pp-cell")).toHaveLength(120);
    expect(w.text()).toContain("Showing the first 120 matches");
  });

  it("appends the next batch rather than replacing the list", async () => {
    streamPictures.mockResolvedValue(batch([1, 2, 3], { done: false }));
    const w = await mountPicker();
    expect(w.findAll(".pp-cell")).toHaveLength(3);

    streamPictures.mockResolvedValue(batch([4, 5], { done: true }));
    const more = w.findAll("button").find((b) => b.text().includes("Show more"));
    await more.trigger("click");
    await flush(w);

    expect(w.findAll(".pp-cell")).toHaveLength(5);
    expect(streamPictures.mock.calls.at(-1)[1].offset).toBe(3);
    // Gone once the stream says there is nothing left.
    expect(w.findAll("button").find((b) => b.text().includes("Show more"))).toBe(
      undefined,
    );
  });

  it("does not let a stale read overwrite the list the reader is on", async () => {
    // The facet changed while the first read was still in flight. Its answer
    // belongs to a scope nobody is looking at any more.
    let releaseFirst;
    streamPictures.mockImplementationOnce(
      () => new Promise((r) => (releaseFirst = () => r(batch([1, 2, 3])))),
    );
    const w = mount(PicturePicker, { props: { open: true, subtitle: "x" } });
    await flush(w);

    streamPictures.mockResolvedValue(batch([9]));
    const clementine = w
      .findAll(".pp-facet")
      .find((b) => b.text().includes("Clementine"));
    await clementine.trigger("click");
    await flush(w);
    expect(w.findAll(".pp-cell")).toHaveLength(1);

    releaseFirst();
    await flush(w);
    expect(w.findAll(".pp-cell")).toHaveLength(1);
  });
});

describe("the facet rail's truncation", () => {
  it("shows the top few and opens the rest on All N", async () => {
    listCharacters.mockResolvedValue(
      Array.from({ length: 17 }, (_, i) => ({
        id: i + 1,
        name: `Someone ${i + 1}`,
        image_count: 100 - i,
      })),
    );
    const w = await mountPicker();
    const people = () =>
      w.findAll(".pp-facet__label").filter((n) => n.text().startsWith("Someone"));
    expect(people()).toHaveLength(3);

    const all = w.findAll(".pp-more").find((b) => b.text().includes("All 17"));
    expect(all).toBeDefined();
    await all.trigger("click");
    expect(people()).toHaveLength(17);
  });
});

describe("a picture whose file cannot be reached", () => {
  it("never stands in for the choice that was already made", async () => {
    // The refusal has to reach the gestures that mean "choose this one AND
    // take it". If it only reaches the choosing half, a double-click or Enter
    // on a refused tile falls through and accepts whatever was chosen BEFORE
    // it - silent, and a picture the reader did not point at.
    const w = await mountPicker();
    await w.findAll(".pp-cell")[0].trigger("click");
    expect(w.findAll(".pp-cell--on")).toHaveLength(1);

    await w.findAll(".pp-cell img")[1].trigger("error");
    await flush(w);
    const gone = w.findAll(".pp-cell")[1];

    await gone.trigger("dblclick");
    expect(w.emitted("pick")).toBeFalsy();
    await gone.trigger("keydown", { key: "Enter" });
    expect(w.emitted("pick")).toBeFalsy();
  });

  it("says so and refuses to be chosen", async () => {
    // A thumbnail is generated FROM the file, so an unplugged drive 404s. An
    // empty box that can still be clicked reads as one that has not loaded yet.
    const w = await mountPicker();
    await w.findAll(".pp-cell img")[0].trigger("error");
    await flush(w);

    const cell = w.findAll(".pp-cell")[0];
    expect(cell.classes()).toContain("pp-cell--gone");
    expect(cell.attributes("title")).toContain("not available");
    await cell.trigger("click");
    expect(w.findAll(".pp-cell--on")).toHaveLength(0);
  });
});

describe("the grid's keyboard", () => {
  it("is one tab stop, and Enter on a tile uses it", async () => {
    // AppDialog exempts buttons from its Enter contract so native activation
    // wins, so without the tile handling Enter itself the badge on
    // `Use this picture` promises something the keyboard cannot do.
    const w = await mountPicker();
    const cells = () => w.findAll(".pp-cell");
    expect(cells().map((c) => c.attributes("tabindex"))).toEqual([
      "0",
      "-1",
      "-1",
    ]);

    await cells()[1].trigger("keydown", { key: "ArrowRight" });
    await flush(w);
    expect(cells()[2].classes()).toContain("pp-cell--on");
    // The tab stop follows the choice, so Tab returns to where the reader was.
    expect(cells().map((c) => c.attributes("tabindex"))).toEqual([
      "-1",
      "-1",
      "0",
    ]);

    await cells()[2].trigger("keydown", { key: "Enter" });
    expect(w.emitted("pick")).toHaveLength(1);
    expect(w.emitted("pick")[0][0].id).toBe(3);
  });
});
