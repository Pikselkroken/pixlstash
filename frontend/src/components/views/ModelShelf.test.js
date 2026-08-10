// The shelf's reading surface.
//
// The three assertions worth having are the ones that guard the measured data
// realities: a third of real adapters have no title, no base model and no
// trigger, so a row must never render a blank; a derived name must stay
// distinguishable from a chosen one; and `unknown` must never read as a
// checkpoint.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const listAdapters = vi.fn();
const listCheckpoints = vi.fn();

vi.mock("../../api/modelShelf", () => ({
  BASE_MODEL_UNASSIGNED: "UNASSIGNED",
  listAdapters: (...args) => listAdapters(...args),
  listCheckpoints: (...args) => listCheckpoints(...args),
}));

// The shelf reads the drives to band its folder groups. Left unmocked this
// reaches the network, and the failure that comes back is routed as a session
// reset — which empties the shelf store MID-TEST and made an unrelated sort
// assertion read the default view back.
const listModelFolderDevices = vi.fn();
vi.mock("../../api/modelFolders", async (importOriginal) => ({
  ...(await importOriginal()),
  listModelFolderDevices: (...args) => listModelFolderDevices(...args),
}));

import ModelShelf from "./ModelShelf.vue";
import { useModelShelfStore } from "../../stores/useModelShelfStore";

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      "v-menu": {
        template: "<div><slot name='activator' :props='{}' /><slot /></div>",
      },
      ShelfShowPanel: true,
      ShelfSortPanel: true,
      // Its own suite mounts it. Here it would only drag Vuetify's dialog and
      // tooltip providers into a suite that installs neither.
      ModelFoldersDialog: true,
    },
  },
};

/** The shape `/adapters` really returns, taken from a fixture probe. */
function adapter(overrides = {}) {
  return {
    id: 1,
    sha256: "a".repeat(64),
    file_kind: "adapter",
    kind: "lora",
    display_name: "Cyanwood Style",
    filename: "Cyanwood_Style_000000250.safetensors",
    base_model: "flux.1-dev",
    file_size: 358733183,
    locations: [{ state: "present", folder_path: "/m", relpath: "a.st" }],
    attachments: [],
    ...overrides,
  };
}

async function mountShelf(rows, checkpoints = []) {
  listAdapters.mockResolvedValue(rows);
  listCheckpoints.mockResolvedValue(checkpoints);
  const wrapper = mount(ModelShelf, globalOpts);
  await new Promise((resolve) => setTimeout(resolve, 0));
  await wrapper.vm.$nextTick();
  return wrapper;
}

/** Text with runs of whitespace collapsed, so wrapping is not asserted. */
function textOf(el) {
  return el.text().replace(/\s+/g, " ");
}

beforeEach(() => {
  setActivePinia(createPinia());
  window.localStorage.clear();
  listAdapters.mockReset();
  listCheckpoints.mockReset();
  listModelFolderDevices.mockReset();
  listModelFolderDevices.mockResolvedValue([]);
});

describe("a row with nothing in its header", () => {
  it("shows a name derived from the filename, never a blank", async () => {
    const wrapper = await mountShelf([
      adapter({
        display_name: null,
        base_model: null,
        filename: "Foxglove_Char_000000250.safetensors",
      }),
    ]);
    const row = wrapper.find(".shelf-row");
    expect(row.find(".shelf-row-name").text()).toContain("Foxglove Char");
    // Every slot on the metadata line renders something: the kind is derived
    // from `file_kind` and is the guaranteed anchor when all else is null.
    const slots = row.findAll(".shelf-row-meta > span").map((s) => s.text());
    expect(slots).toEqual(["LoRA", "Base model not set", "342.1 MB"]);
  });

  it("marks the derived name by type, not by fading it", async () => {
    // Rank is size, weight and tracking, never opacity (§5.1) — and 37% of
    // rows faded would be a column of ghosts rather than a signal.
    const wrapper = await mountShelf([
      adapter({ id: 1, display_name: null }),
      adapter({ id: 2, display_name: "Clementine" }),
    ]);
    const names = wrapper.findAll(".shelf-row-name");
    expect(names[0].classes()).toContain("shelf-row-name--derived");
    expect(names[1].classes()).not.toContain("shelf-row-name--derived");
    // ...and the mark is announced, because a font swap is silent.
    expect(names[0].text()).toContain("name taken from the filename");
  });
});

describe("file kinds", () => {
  it("never renders an unclassified file as a checkpoint", async () => {
    // `unknown` is in neither default list (see api/modelShelf.js), so the row
    // only reaches the shelf when the Unclassified box asks for its block.
    useModelShelfStore().setFilters({
      adapters: false,
      checkpoints: false,
      unclassified: true,
    });
    const wrapper = await mountShelf([
      adapter({ file_kind: "unknown", kind: null, display_name: "aurora" }),
    ]);
    const meta = textOf(wrapper.find(".shelf-row-meta"));
    expect(meta).toContain("Unclassified");
    expect(meta).not.toContain("Checkpoint");
  });

  it("names a checkpoint as one, having no algorithm to name", async () => {
    const wrapper = await mountShelf(
      [],
      [adapter({ file_kind: "checkpoint", kind: null })],
    );
    expect(textOf(wrapper.find(".shelf-row-meta"))).toContain("Checkpoint");
  });
});

describe("location state", () => {
  it("keeps 'we could not look' visually apart from 'it is not there'", async () => {
    const wrapper = await mountShelf([
      adapter({ id: 1, locations: [{ state: "missing" }] }),
      adapter({ id: 2, locations: [{ state: "unreachable" }] }),
      adapter({ id: 3 }),
    ]);
    const slots = wrapper.findAll(".shelf-row-loc");
    expect(slots[0].classes()).toContain("shelf-row-loc--missing");
    expect(slots[1].classes()).toContain("shelf-row-loc--unreachable");
    // Present reserves its slot and shows nothing, so no row ever shifts.
    expect(slots[2].classes()).toContain("shelf-row-loc--present");
  });
});

describe("empty states", () => {
  it("says where PixlStash looks when the shelf is empty", async () => {
    // No reset offered here: resetting the filters would fix nothing, and
    // offering it would blame the user for an empty disk.
    const wrapper = await mountShelf([]);
    const state = textOf(wrapper.find(".shelf-state"));
    expect(state).toContain("No models found");
    expect(state).not.toContain("Reset filters");
  });

  it("offers a way out of a filter that matched nothing", async () => {
    const wrapper = await mountShelf([adapter({ base_model: "sdxl" })]);
    useModelShelfStore().setFilters({ baseModels: ["UNASSIGNED"] });
    await wrapper.vm.$nextTick();
    const state = textOf(wrapper.find(".shelf-state"));
    expect(state).toContain("No models match these filters");
    expect(state).toContain("Reset filters");
  });

  it("says so when Show asks for nothing at all", async () => {
    const wrapper = await mountShelf([adapter()]);
    useModelShelfStore().setFilters({
      adapters: false,
      checkpoints: false,
      unclassified: false,
    });
    await wrapper.vm.$nextTick();
    expect(textOf(wrapper.find(".shelf-state"))).toContain(
      "Nothing is selected in Show",
    );
  });
});

describe("after a session reset", () => {
  it("refetches rather than claiming the machine has no models", async () => {
    // Logout, login, a share token or a restore empties the store. Nothing
    // refetched, so the shelf showed its terminal "there is nothing here"
    // state for a library that had simply not been asked.
    const wrapper = await mountShelf([adapter()]);
    expect(wrapper.find(".shelf-row").exists()).toBe(true);

    listAdapters.mockClear();
    useModelShelfStore().resetForSession();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await wrapper.vm.$nextTick();

    expect(listAdapters).toHaveBeenCalled();
    expect(textOf(wrapper.find(".shelf-body"))).not.toContain(
      "No models found",
    );
    expect(wrapper.find(".shelf-row").exists()).toBe(true);
  });
});

describe("keyboard", () => {
  it("takes focus on mount, not one round trip later", async () => {
    // Focusing after the fetch discarded wherever the user had moved in the
    // meantime. DuplicateQueue, the contract this view mirrors, is synchronous.
    listAdapters.mockReturnValue(new Promise(() => {}));
    listCheckpoints.mockReturnValue(new Promise(() => {}));
    const wrapper = mount(ModelShelf, {
      ...globalOpts,
      attachTo: document.body,
    });
    await wrapper.vm.$nextTick();

    expect(document.activeElement).toBe(wrapper.find(".shelf").element);

    wrapper.unmount();
  });

  it("leaves rows out of the tab order while they have no verb", async () => {
    // 1,800 empty tab stops is a trap. Roving focus arrives with the first
    // thing a focused row can do (F2/F4).
    const wrapper = await mountShelf([adapter()]);
    expect(wrapper.find(".shelf-row").attributes("tabindex")).toBeUndefined();
    expect(wrapper.find(".shelf").attributes("tabindex")).toBe("-1");
  });
});

describe("the shelf's own accessible name", () => {
  it("carries a role that is allowed to have one", async () => {
    // A bare div is role `generic`, which prohibits an accessible name, so
    // both aria-label and aria-describedby are dropped and the whole
    // #shelf-help paragraph is never announced.
    const wrapper = await mountShelf([adapter()]);
    const root = wrapper.find(".shelf");
    expect(root.attributes("role")).toBe("region");
    expect(root.attributes("aria-label")).toBe("Model shelf");
    expect(root.attributes("aria-describedby")).toBe("shelf-help");
    expect(wrapper.find("#shelf-help").exists()).toBe(true);
  });
});

// ── F2: group headers and the Sort split-button ────────────────────────────

describe("group headers", () => {
  it("draws no header at all while the list is ungrouped", async () => {
    // The flat F1 list and the grouped list are ONE piece of markup with the
    // header switched off, not two copies of the row template.
    const wrapper = await mountShelf([adapter()]);
    expect(wrapper.find(".shelf-group-btn").exists()).toBe(false);
    expect(wrapper.findAll(".shelf-row").length).toBe(1);
  });

  it("states the group and its size, and collapses to the header alone", async () => {
    const wrapper = await mountShelf([
      adapter({ id: 1, base_model: "sdxl" }),
      adapter({ id: 2, base_model: "sdxl" }),
    ]);
    useModelShelfStore().setView({ groupBy: "base_model" });
    await wrapper.vm.$nextTick();

    const header = wrapper.find(".shelf-group-btn");
    expect(textOf(header)).toContain("sdxl");
    expect(textOf(header)).toContain("2 models");
    expect(header.attributes("aria-expanded")).toBe("true");
    // The accessible name carries the count, so the reader hears how big the
    // group is BEFORE deciding to open it.
    expect(header.attributes("aria-label")).toBe("sdxl, 2 models");

    await header.trigger("click");
    expect(wrapper.find(".shelf-group-btn").attributes("aria-expanded")).toBe(
      "false",
    );
    expect(wrapper.findAll(".shelf-row").length).toBe(0);
    // The header survives with its count: a group that vanished would be
    // indistinguishable from one the filters removed.
    expect(textOf(wrapper.find(".shelf-group-btn"))).toContain("2 models");
  });

  it("is a real button and leaves the rows out of the tab order", async () => {
    // Rows carry no verb, so the headers are the only stops in the list, which
    // is what makes Tab a group-to-group move without inventing a shortcut.
    const wrapper = await mountShelf([adapter({ base_model: "sdxl" })]);
    useModelShelfStore().setView({ groupBy: "base_model" });
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".shelf-group-btn").element.tagName).toBe("BUTTON");
    expect(wrapper.find(".shelf-group-heading").element.tagName).toBe("H3");
    expect(wrapper.find(".shelf-row").attributes("tabindex")).toBeUndefined();
  });

  it("sets a folder path in the path variant, never uppercased", async () => {
    // §3 gives the mono face to file paths, and uppercasing one misstates the
    // string. A base-model name is a label and takes the other treatment.
    const wrapper = await mountShelf([
      adapter({
        locations: [{ state: "present", folder_path: "/mnt/Models" }],
      }),
    ]);
    const store = useModelShelfStore();
    store.setView({ groupBy: "folder" });
    await wrapper.vm.$nextTick();
    const label = wrapper.find(".shelf-group-label");
    expect(label.classes()).toContain("shelf-group-label--path");
    expect(label.text()).toBe("/mnt/Models");

    store.setView({ groupBy: "base_model" });
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".shelf-group-label").classes()).toContain(
      "shelf-group-label--name",
    );
  });

  it("counts models and copies apart when a model sits in two folders", async () => {
    const wrapper = await mountShelf([
      adapter({
        locations: [
          { state: "present", folder_path: "/a" },
          { state: "present", folder_path: "/b" },
        ],
      }),
    ]);
    useModelShelfStore().setView({ groupBy: "folder" });
    await wrapper.vm.$nextTick();
    expect(textOf(wrapper.find(".shelf-sub"))).toBe("1 model · 2 copies");
  });
});

describe("the Sort split-button", () => {
  it("names its direction so a keyboard user hears it on focus return", async () => {
    // The accessible name IS the state and flips on press. "Ascending" would be
    // useless on a date column and backwards on a size one, so each key words
    // its own two ends.
    const wrapper = await mountShelf([adapter()]);
    const store = useModelShelfStore();
    store.setView({ sortKey: "size", sortDirection: "desc" });
    await wrapper.vm.$nextTick();

    const toggle = wrapper.find(".bar-split-toggle");
    expect(toggle.attributes("aria-label")).toBe("Largest first");
    await toggle.trigger("click");
    expect(store.view.sortDirection).toBe("asc");
    expect(wrapper.find(".bar-split-toggle").attributes("aria-label")).toBe(
      "Smallest first",
    );
  });

  it("claims a dialog rather than a menu, because the panel is not one", async () => {
    // `aria-haspopup="menu"` promises a role="menu" popup with roving arrow
    // keys. The .tbm panel is a div of grouped toggles and implements none of
    // that; the same reasoning rejected role="listbox"/option here before.
    const wrapper = await mountShelf([adapter()]);
    const trigger = wrapper.find(".bar-split-menu");
    expect(trigger.attributes("aria-haspopup")).toBe("dialog");
    expect(trigger.attributes("aria-expanded")).toBe("false");
    expect(wrapper.find(".bar-split-button").attributes("role")).toBe("group");
  });

  it("announces a resort, because the rows reorder silently", async () => {
    const wrapper = await mountShelf([adapter()]);
    useModelShelfStore().setView({ sortKey: "name", sortDirection: "asc" });
    await wrapper.vm.$nextTick();
    const status = wrapper.find('[role="status"]');
    expect(status.text()).toBe("Sorted by name: A to Z");
  });
});

describe("drive bands", () => {
  const inFolder = (id, folderId, path) =>
    adapter({
      id,
      sha256: String(id).repeat(64).slice(0, 64),
      locations: [
        {
          state: "present",
          folder_id: folderId,
          folder_path: path,
          relpath: "a.st",
        },
      ],
    });

  it("draws one band per drive, with a meter that reads free space first", async () => {
    listModelFolderDevices.mockResolvedValue([
      {
        device_id: "9",
        mount_point: "/mnt/fast",
        label: "FastModels",
        total_bytes: 1024 ** 4,
        free_bytes: 512 * 1024 ** 3,
        shelf_bytes: 256 * 1024 ** 3,
        folder_ids: [1, 2],
      },
    ]);
    const wrapper = await mountShelf([
      inFolder(1, 1, "/mnt/fast/loras"),
      inFolder(2, 2, "/mnt/fast/checkpoints"),
    ]);
    useModelShelfStore().setView({ groupBy: "folder", folderLayout: "drive" });
    await wrapper.vm.$nextTick();

    const bands = wrapper.findAll(".shelf-band-heading");
    expect(bands).toHaveLength(1);
    // The volume's name, not its mount point: a Linux mount point runs to
    // `/media/glindkvist/102AB4B6757AF9A3` and crowds the header out.
    expect(textOf(bands[0])).toContain("FastModels");
    expect(textOf(bands[0])).not.toContain("/mnt/fast");
    expect(bands[0].find(".shelf-band-label").attributes("title")).toBe(
      "/mnt/fast",
    );
    // Free leads: it is the number that decides whether the next checkpoint
    // fits, and the meter is read at a glance rather than computed from.
    expect(textOf(bands[0])).toContain("512.0 GB free of 1.0 TB");
    expect(wrapper.findAll(".shelf-band-fill")).toHaveLength(2);
  });

  it("says a drive is unknown rather than drawing it empty", async () => {
    // An empty meter reads as a drive with nothing on it, which is the one
    // thing "we could not measure it" does not mean.
    listModelFolderDevices.mockResolvedValue([
      {
        device_id: null,
        mount_point: "/net/models",
        total_bytes: null,
        free_bytes: null,
        shelf_bytes: 0,
        folder_ids: [7],
      },
    ]);
    const wrapper = await mountShelf([inFolder(1, 7, "/net/models/loras")]);
    useModelShelfStore().setView({ groupBy: "folder", folderLayout: "drive" });
    await wrapper.vm.$nextTick();

    expect(textOf(wrapper.find(".shelf-band-heading"))).toContain(
      "Capacity unknown",
    );
    expect(wrapper.find(".shelf-band-meter").exists()).toBe(false);
  });

  it("draws no band at all under the A to Z layout", async () => {
    // The band is the drive layout's own tier. Folder-alphabetical is one flat
    // run of folder headers, which is what it was before F2.
    listModelFolderDevices.mockResolvedValue([
      {
        device_id: "9",
        mount_point: "/mnt/fast",
        total_bytes: 1000,
        free_bytes: 400,
        shelf_bytes: 100,
        folder_ids: [1],
      },
    ]);
    const wrapper = await mountShelf([inFolder(1, 1, "/mnt/fast/loras")]);
    useModelShelfStore().setView({ groupBy: "folder", folderLayout: "alpha" });
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".shelf-band-heading").exists()).toBe(false);
    expect(wrapper.findAll(".shelf-group-btn").length).toBeGreaterThan(0);
  });
});
