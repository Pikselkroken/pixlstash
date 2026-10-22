// Settings › Models: folder registration, and the two states it has to render
// rather than hit.
//
// `LOCAL_OWNER_ONLY` means a remote owner reads the list and 403s on every
// mutator, and the managed store answers 409 on DELETE because its state
// refuses rather than because the caller may not. Both are designed states
// here: a button that always fails on click is the failure this suite pins.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const listModelFolders = vi.fn();
const createModelFolder = vi.fn();
const forgetModelFolder = vi.fn();
const rescanModelFolder = vi.fn();
const setDefaultModelFolder = vi.fn();
const setDownloadModelFolder = vi.fn();

vi.mock("../../api/modelFolders", () => ({
  MANAGED_KIND: "managed",
  SOURCE_KIND: "source",
  DOWNLOADS_KIND: "downloads",
  CREATABLE_KINDS: ["user", "source", "downloads"],
  listModelFolders: (...a) => listModelFolders(...a),
  createModelFolder: (...a) => createModelFolder(...a),
  forgetModelFolder: (...a) => forgetModelFolder(...a),
  rescanModelFolder: (...a) => rescanModelFolder(...a),
  setDefaultModelFolder: (...a) => setDefaultModelFolder(...a),
  setDownloadModelFolder: (...a) => setDownloadModelFolder(...a),
}));

// The two pinned cards share their look, so each is found by its label.
const DEFAULT_CARD = "[aria-labelledby='mf-default-label']";
const DOWNLOAD_CARD = "[aria-labelledby='mf-download-label']";

vi.mock("../../stores/useModelShelfStore", () => ({
  useModelShelfStore: () => ({ fetchRows: vi.fn() }),
}));

// The move job is the store's, not the pane's - the pane only starts one.
const relocate = vi.fn();
let moveBusy = false;
vi.mock("../../stores/useModelMovesStore", () => ({
  useModelMovesStore: () => ({
    relocate: (...a) => relocate(...a),
    get busy() {
      return moveBusy;
    },
  }),
}));

import ModelFoldersSection from "./ModelFoldersSection.vue";
import { useLibrariesStore } from "../../stores/useLibrariesStore";

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      Tooltip: true,
      // Activator and content both rendered, so the menu items are reachable,
      // except when the menu is disabled, which is what a blocked menu is.
      "v-menu": {
        props: ["disabled"],
        template:
          "<div><slot name='activator' :props='{}' /><slot v-if='!disabled' /></div>",
      },
      FolderBrowser: true,
    },
  },
};

function folder(overrides = {}) {
  return {
    id: 1,
    path: "/home/me/loras",
    kind: "user",
    owner: null,
    movable: "per_item",
    relocatable: false,
    host_path: null,
    delete_after_import: false,
    last_checked: null,
    created_at: "2026-08-01T10:00:00",
    file_count: 91,
    present_bytes: 0,
    ...overrides,
  };
}

const MANAGED = folder({
  id: 2,
  path: "/var/pixlstash/models",
  kind: "managed",
  tool: "PixlStash",
  movable: "root_only",
  relocatable: true,
  file_count: 3,
});

// PixlStash's own download folder. `movable` is identical to the InsightFace
// packs' below, which is why the row reads the server's `relocatable` and never
// derives Move from `movable` itself.
const DOWNLOADED = folder({
  id: 4,
  path: "/home/me/.local/share/pixlstash/downloaded_models",
  kind: "foreign",
  owner: "pixlstash",
  tool: "PixlStash",
  movable: "root_only",
  relocatable: true,
  file_count: 4,
});

const INSIGHTFACE = folder({
  id: 5,
  path: "/home/me/.insightface/models",
  kind: "foreign",
  owner: "pixlstash",
  tool: "PixlStash",
  movable: "root_only",
  relocatable: true,
  file_count: 2,
});

// The HuggingFace cache: `fixed`, because its location is `HF_HOME` and another
// tool owns it. The row that must never carry Move, and the reason the verb is
// read from the server rather than derived from `kind`.
const HF_CACHE = folder({
  id: 6,
  path: "/home/me/.cache/huggingface/hub",
  kind: "foreign",
  owner: "pixlstash",
  tool: "HuggingFace",
  movable: "fixed",
  relocatable: false,
  file_count: 26,
});

const WATCHED = folder({
  id: 7,
  path: "/home/me/Downloads",
  kind: "downloads",
  movable: "external",
  file_count: 0,
});

async function open(rows, { canManage = true, inDocker = false } = {}) {
  listModelFolders.mockResolvedValue(rows);
  const wrapper = mount(ModelFoldersSection, {
    props: { open: true },
    ...globalOpts,
  });
  const libraries = useLibrariesStore();
  libraries.canManage = canManage;
  libraries.inDocker = inDocker;
  await new Promise((r) => setTimeout(r, 0));
  await wrapper.vm.$nextTick();
  return wrapper;
}

const byLabel = (wrapper, prefix) =>
  wrapper
    .findAll("button")
    .filter((b) => (b.attributes("aria-label") || "").startsWith(prefix));

const byText = (wrapper, text) =>
  wrapper.findAll("button").find((b) => b.text().includes(text));

const browser = (wrapper) => wrapper.findComponent({ name: "FolderBrowser" });

beforeEach(() => {
  setActivePinia(createPinia());
  listModelFolders.mockReset().mockResolvedValue([]);
  createModelFolder.mockReset().mockResolvedValue(folder({ id: 9 }));
  relocate.mockReset().mockResolvedValue(true);
  moveBusy = false;
  forgetModelFolder.mockReset().mockResolvedValue({ tombstoned_files: 0 });
  rescanModelFolder.mockReset().mockResolvedValue({ status: "started" });
  setDefaultModelFolder.mockReset().mockResolvedValue({});
  setDownloadModelFolder.mockReset().mockResolvedValue({});
});

describe("the list", () => {
  it("is one line per folder: path, chip, count and size, one action", async () => {
    const wrapper = await open([
      folder({ present_bytes: 116.3 * 1024 ** 3 }),
      HF_CACHE,
    ]);
    const rows = wrapper.findAll(".mf-list .mf-row");
    expect(rows).toHaveLength(2);
    expect(rows[0].text()).toContain("91 files · 116.3 GB");
    expect(rows[1].find(".mf-chip").text()).toBe("HuggingFace");
    // One action slot on every row, kept even where there is no verb.
    for (const row of rows) expect(row.findAll("button")).toHaveLength(1);
    expect(rows[1].find("button").classes()).toContain("mf-hidden");
  });

  it("omits the size at zero rather than claiming 0 B", async () => {
    const wrapper = await open([folder({ file_count: 0, present_bytes: 0 })]);
    expect(wrapper.text()).not.toContain("0 B");
  });

  it("has no per-row scan and no scan age", async () => {
    const wrapper = await open([
      folder({ last_checked: "2026-08-01T10:00:00" }),
    ]);
    expect(byLabel(wrapper, "Scan")).toHaveLength(0);
    expect(byLabel(wrapper, "Rescan")).toHaveLength(0);
    expect(wrapper.text()).not.toMatch(/Scanned|Never scanned/);
  });

  it("names the tool the server resolved, and nothing on the owner's own folder", async () => {
    const wrapper = await open([
      DOWNLOADED,
      HF_CACHE,
      folder({
        id: 8,
        path: "/home/me/drive/StabilityMatrix",
        tool: "StabilityMatrix",
      }),
      folder({ id: 9 }),
    ]);
    const chips = wrapper
      .findAll(".mf-list .mf-row")
      .map((row) => row.find(".mf-chip"))
      .map((chip) => (chip.exists() ? chip.text() : null));
    expect(chips).toEqual([
      "PixlStash",
      "HuggingFace",
      "StabilityMatrix",
      null,
    ]);
  });

  it("totals the folders under the list, leaving Downloads out", async () => {
    const wrapper = await open([
      folder({ present_bytes: 1024 ** 3 }),
      { ...MANAGED, is_default: true, present_bytes: 1024 ** 3 },
      WATCHED,
    ]);
    expect(wrapper.find(".mf-foot").text()).toContain("2 folders · 2.0 GB");
  });
});

describe("the managed store", () => {
  it("offers Move and never Forget", async () => {
    // DELETE on it is a 409, so a button there could only ever fail.
    const wrapper = await open([MANAGED]);
    expect(byLabel(wrapper, "Forget")).toHaveLength(0);
    expect(byLabel(wrapper, "Move")).toHaveLength(1);
  });
});

describe("relocation", () => {
  it("offers Move on the folders the server says relocate", async () => {
    const wrapper = await open([MANAGED, DOWNLOADED, INSIGHTFACE, HF_CACHE]);
    const labels = byLabel(wrapper, "Move").map((b) =>
      b.attributes("aria-label"),
    );
    expect(labels).toHaveLength(3);
    expect(labels.join(" ")).toContain("downloaded_models");
    expect(labels.join(" ")).toContain(".insightface");
    expect(labels.join(" ")).not.toContain("huggingface");
  });

  it("sends the picked path for the folder the owner clicked", async () => {
    const wrapper = await open([MANAGED, DOWNLOADED]);
    await byLabel(wrapper, "Move")[1].trigger("click");
    await browser(wrapper).vm.$emit("select", "/mnt/x");
    expect(relocate).toHaveBeenCalledWith(DOWNLOADED.id, "/mnt/x");
  });

  it("blocks the verb while a move is running rather than taking the 409", async () => {
    moveBusy = true;
    const wrapper = await open([MANAGED]);
    const [move] = byLabel(wrapper, "Move");
    expect(move.attributes("aria-disabled")).toBe("true");
    expect(move.attributes("disabled")).toBeUndefined();
    await move.trigger("click");
    expect(relocate).not.toHaveBeenCalled();
  });

  it("is unavailable to a remote owner, reachably", async () => {
    const wrapper = await open([MANAGED], { canManage: false });
    const [move] = byLabel(wrapper, "Move");
    expect(move.attributes("aria-disabled")).toBe("true");
    expect(move.attributes("aria-describedby")).toBe("mf-remote-note");
    expect(wrapper.get("#mf-remote-note").text()).toContain(
      "allow_remote_host_ops",
    );
    await move.trigger("click");
    expect(relocate).not.toHaveBeenCalled();
  });
});

describe("forgetting", () => {
  it("blocks the verb while a move is running rather than taking the 409", async () => {
    // Forgetting deletes the very location rows a move is repointing, so the
    // server takes the one machine-wide slot for it too (#1017).
    moveBusy = true;
    const wrapper = await open([folder()]);
    const [forget] = byLabel(wrapper, "Forget");
    expect(forget.attributes("aria-disabled")).toBe("true");
    await forget.trigger("click");
    expect(forgetModelFolder).not.toHaveBeenCalled();
  });

  it("still lets it through with no move running", async () => {
    const wrapper = await open([folder()]);
    const [forget] = byLabel(wrapper, "Forget");
    expect(forget.attributes("aria-disabled")).toBeUndefined();
    await forget.trigger("click");
    expect(forgetModelFolder).toHaveBeenCalledWith(1);
  });
});

describe("Rescan all", () => {
  it("scans every catalogued folder and skips the rest", async () => {
    const wrapper = await open([
      folder(),
      { ...MANAGED, is_default: true },
      HF_CACHE,
      folder({ id: 3, kind: "source", path: "/home/me/ai-toolkit/output" }),
      WATCHED,
    ]);
    await byText(wrapper, "Rescan all").trigger("click");
    const ids = rescanModelFolder.mock.calls.map((c) => c[0]).sort();
    expect(ids).toEqual([1, 2]);
  });

  it("counts the scans down and takes no second click while they start", async () => {
    let answer;
    rescanModelFolder.mockReturnValue(
      new Promise((r) => {
        answer = r;
      }),
    );
    const wrapper = await open([folder(), { ...MANAGED, is_default: true }]);
    await byText(wrapper, "Rescan all").trigger("click");
    expect(byText(wrapper, "Scanning").text()).toContain("Scanning 1 of 2…");
    await byText(wrapper, "Scanning").trigger("click");
    expect(rescanModelFolder).toHaveBeenCalledTimes(2);
    answer({ status: "started" });
    await new Promise((r) => setTimeout(r, 0));
    // Both still running on the server: the first of two.
    expect(byText(wrapper, "Scanning").text()).toContain("Scanning 1 of 2…");
  });

  it("is blocked, described and silent from a remote session", async () => {
    const wrapper = await open([folder()], { canManage: false });
    const rescan = byText(wrapper, "Rescan all");
    expect(rescan.attributes("aria-disabled")).toBe("true");
    expect(rescan.attributes("aria-describedby")).toBe("mf-remote-note");
    await rescan.trigger("click");
    expect(rescanModelFolder).not.toHaveBeenCalled();
  });
});

describe("Docker", () => {
  it("blocks adding rather than letting the 400 happen", async () => {
    const wrapper = await open([folder()], { inDocker: true });
    expect(byText(wrapper, "Add folder").attributes("aria-disabled")).toBe(
      "true",
    );
  });
});

describe("setting the ai-toolkit output folder", () => {
  const SOURCE = folder({
    id: 4,
    path: "/home/me/ai-toolkit/output",
    kind: "source",
    movable: "external",
  });

  it("offers to set it only while none is registered", async () => {
    expect((await open([folder()])).text()).toContain("Set ai-toolkit folder");
    expect((await open([folder(), SOURCE])).text()).not.toContain(
      "Set ai-toolkit folder",
    );
  });

  it("registers the pick as the output root, and a later Add as a plain folder", async () => {
    // The picker is shared, so the verb that opened it is forgotten when it
    // closes: cancelling a Set and then pressing Add must not register the
    // next folder as an output root.
    const wrapper = await open([folder()]);
    await byText(wrapper, "Set ai-toolkit folder").trigger("click");
    await browser(wrapper).vm.$emit("select", "/home/me/ai-toolkit/output");
    expect(createModelFolder).toHaveBeenCalledWith({
      path: "/home/me/ai-toolkit/output",
      kind: "source",
    });

    await browser(wrapper).vm.$emit("close");
    await byText(wrapper, "Add folder").trigger("click");
    await browser(wrapper).vm.$emit("select", "/home/me/loras2");
    expect(createModelFolder).toHaveBeenLastCalledWith({
      path: "/home/me/loras2",
      kind: "user",
    });
  });
});

describe("the Downloads folder", () => {
  it("offers to monitor one while none is set, and registers the pick", async () => {
    const wrapper = await open([folder()]);
    await byText(wrapper, "Monitor download folder").trigger("click");
    await browser(wrapper).vm.$emit("select", "/home/me/Downloads");
    expect(createModelFolder).toHaveBeenCalledWith({
      path: "/home/me/Downloads",
      kind: "downloads",
    });
  });

  it("sits in the header by name, out of the list", async () => {
    const wrapper = await open([folder(), WATCHED]);
    expect(wrapper.text()).not.toContain("Monitor download folder");
    expect(wrapper.find(".mf-dl").text()).toContain("Downloads");
    expect(wrapper.find(".mf-list").text()).not.toContain("Downloads");
    expect(wrapper.find(DEFAULT_CARD).text()).toContain(
      "New models from Downloads land here",
    );
  });

  it("stops watching through the pill's menu", async () => {
    const wrapper = await open([folder(), WATCHED]);
    await byText(wrapper, "Stop watching").trigger("click");
    expect(forgetModelFolder).toHaveBeenCalledWith(7);
  });

  it("changes by forgetting the old folder before registering the new one", async () => {
    // The server holds one Downloads folder at most, so registering first
    // would be refused.
    const wrapper = await open([folder(), WATCHED]);
    await byText(wrapper, "Change…").trigger("click");
    await browser(wrapper).vm.$emit("select", "/home/me/Desktop");
    await new Promise((r) => setTimeout(r, 0));
    expect(forgetModelFolder).toHaveBeenCalledWith(7);
    expect(createModelFolder).toHaveBeenCalledWith({
      path: "/home/me/Desktop",
      kind: "downloads",
    });
    expect(forgetModelFolder.mock.invocationCallOrder[0]).toBeLessThan(
      createModelFolder.mock.invocationCallOrder[0],
    );
  });

  it("puts the old folder back when the new one is refused", async () => {
    // Otherwise a refused pick leaves nothing watched at all.
    createModelFolder
      .mockRejectedValueOnce(new Error("refused"))
      .mockResolvedValueOnce(WATCHED);
    const wrapper = await open([folder(), WATCHED]);
    await byText(wrapper, "Change…").trigger("click");
    await browser(wrapper).vm.$emit("select", "/home/me/Desktop");
    await new Promise((r) => setTimeout(r, 0));
    expect(createModelFolder.mock.calls.map((c) => c[0])).toEqual([
      { path: "/home/me/Desktop", kind: "downloads" },
      { path: "/home/me/Downloads", kind: "downloads" },
    ]);
  });

  it("keeps a refused Change… reachable so its reason can be read", async () => {
    // Natively disabled, the item takes no hover, so its tooltip never opens.
    const wrapper = await open([folder(), WATCHED], { inDocker: true });
    const change = byText(wrapper, "Change…");
    expect(change.attributes("aria-disabled")).toBe("true");
    expect(change.attributes("disabled")).toBeUndefined();
    await change.trigger("click");
    expect(browser(wrapper).props("open")).toBe(false);
    expect(byText(wrapper, "Stop watching").attributes("aria-disabled")).toBe(
      undefined,
    );
  });

  it("refuses Change… while a move runs, since the swap forgets first", async () => {
    // The server answers that forget with a 409 (#1017).
    moveBusy = true;
    const wrapper = await open([folder(), WATCHED]);
    const change = byText(wrapper, "Change…");
    expect(change.attributes("aria-disabled")).toBe("true");
    await change.trigger("click");
    expect(browser(wrapper).props("open")).toBe(false);
    expect(forgetModelFolder).not.toHaveBeenCalled();
  });

  it("offers no menu to a remote session", async () => {
    const wrapper = await open([folder(), WATCHED], { canManage: false });
    expect(byText(wrapper, "Stop watching")).toBeUndefined();
    expect(wrapper.find(".mf-dl").attributes("aria-describedby")).toBe(
      "mf-remote-note",
    );
  });
});

describe("the default folder", () => {
  const CHOSEN = folder({ id: 1, is_default: true });
  const STORE = { ...MANAGED, is_default: false };

  it("is pinned above the list and appears nowhere else", async () => {
    const wrapper = await open([CHOSEN, STORE]);
    expect(wrapper.find(DEFAULT_CARD).text()).toContain("/home/me/loras");
    expect(wrapper.find(".mf-list").text()).not.toContain("/home/me/loras");
  });

  it("offers only the other folders that can be it", async () => {
    const wrapper = await open([CHOSEN, STORE, HF_CACHE]);
    const offered = wrapper
      .findAll(`${DEFAULT_CARD} .ctx-item`)
      .map((b) => b.text());
    expect(offered).toHaveLength(1);
    expect(offered[0]).toContain("/var/pixlstash/models");
  });

  it("asks the server to make the picked folder the default", async () => {
    const wrapper = await open([CHOSEN, STORE]);
    await wrapper.find(`${DEFAULT_CARD} .ctx-item`).trigger("click");
    expect(setDefaultModelFolder).toHaveBeenCalledWith(2, true);
  });

  it("offers no menu at all to a remote session", async () => {
    const wrapper = await open([CHOSEN, STORE], { canManage: false });
    expect(wrapper.find(`${DEFAULT_CARD} .ctx-item`).exists()).toBe(false);
    const change = byText(wrapper, "Change");
    expect(change.attributes("aria-disabled")).toBe("true");
    expect(change.attributes("aria-describedby")).toBe("mf-remote-note");
  });

  it("says why it cannot change while no other folder could be the default", async () => {
    const wrapper = await open([CHOSEN, HF_CACHE]);
    expect(wrapper.find(`${DEFAULT_CARD} .ctx-item`).exists()).toBe(false);
    expect(byText(wrapper, "Change").attributes("aria-disabled")).toBe("true");
  });
});

describe("the download folder", () => {
  // As the server reports it: with nothing marked, the default folder.
  const DEFAULTED = folder({ id: 1, is_default: true, is_download_target: true });
  const STORE = { ...MANAGED, is_default: false };

  it("is the default folder until another is picked, and says so", async () => {
    const wrapper = await open([DEFAULTED, STORE]);
    const card = wrapper.find(DOWNLOAD_CARD);
    expect(card.text()).toContain("/home/me/loras");
    expect(card.text()).toContain("Default folder");
  });

  it("asks the server to send downloads to the picked folder", async () => {
    const wrapper = await open([DEFAULTED, STORE]);
    const offered = wrapper.findAll(`${DOWNLOAD_CARD} .ctx-item`);
    expect(offered.map((b) => b.text())).toEqual([
      expect.stringContaining("/var/pixlstash/models"),
    ]);
    await offered[0].trigger("click");
    expect(setDownloadModelFolder).toHaveBeenCalledWith(2, true);
    expect(setDefaultModelFolder).not.toHaveBeenCalled();
  });

  it("pins a chosen download folder out of the list", async () => {
    const wrapper = await open([
      folder({ id: 1, is_default: true }),
      { ...STORE, is_download_target: true },
    ]);
    expect(wrapper.find(DOWNLOAD_CARD).text()).toContain("/var/pixlstash/models");
    expect(wrapper.find(DOWNLOAD_CARD).text()).not.toContain("Default folder");
    expect(wrapper.find(".mf-list").exists()).toBe(false);
  });
});
