// The set grid (#1438): the card per combination, the panel under a stack, and
// the card that stands for the models no recipe names.
//
// The assertions worth having are the ones the honesty rule rests on - a set
// with no evidence is DRAWN rather than dropped, an unfoldable card says so, a
// filename-only member is flagged rather than hidden - and the line the
// selection rests on: a card selects the ONE model it is named after, never the
// set behind it, so no verb the bar carries can reach a file the reader did not
// aim at.

import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { enableAutoUnmount, mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  const stubs = vuetifyComponentStubs();
  const VMenu = {
    name: "VMenu",
    template: `<div><slot name="activator" :props="{}" /><slot /></div>`,
  };
  return new Proxy(stubs, {
    get: (target, prop) => (prop === "VMenu" ? VMenu : target[prop]),
  });
});

const fetchWorkflowSets = vi.fn();
const fetchWorkflowTemplates = vi.fn();
const createWorkflowSet = vi.fn();
const deleteWorkflowSet = vi.fn();
const addWorkflowSetMembers = vi.fn();
const removeWorkflowSetMembers = vi.fn();
const renameWorkflowSet = vi.fn();
const setWorkflowSetDeclines = vi.fn();
const setWorkflowSetsHidden = vi.fn();
// What the double's server holds as hidden in the active library (#1869): the
// hide is the server's answer now, not a localStorage flag, so a hide and the
// read that follows it must agree.
let hiddenOnServer = new Set();
const putSetVerdict = vi.fn();
// One double per result set, like the store's own suite: `/adapters` is ONE
// route serving five `file_kind`s, and a single mock would answer the engines
// and support requests with the same rows and make the shelf look duplicated.
const listAdapters = vi.fn();
const listSupport = vi.fn();

// The favourites: read on mount and written by a card's heart, doubled so the
// suite says what the hub holds and asserts the write.
const listFavourites = vi.fn();
const setFavourites = vi.fn();
vi.mock("../../api/favourites", () => ({
  listFavourites: (...args) => listFavourites(...args),
  setFavourites: (...args) => setFavourites(...args),
}));

vi.mock("../../api/modelShelf", () => ({
  BASE_MODEL_UNASSIGNED: "UNASSIGNED",
  listAdapters: (...args) => {
    const kind = args[0]?.fileKind;
    if (kind === "vae") return listSupport(...args);
    if (kind) return Promise.resolve([]);
    return listAdapters(...args);
  },
  listCheckpoints: vi.fn().mockResolvedValue([]),
  listBaseModelCompletions: vi.fn().mockResolvedValue([]),
  editModels: vi.fn(),
  forgetModels: vi.fn(),
  deleteModels: vi.fn(),
  setAdapterAttachments: vi.fn(),
  fetchWorkflowSets: (...args) => fetchWorkflowSets(...args),
  fetchWorkflowTemplates: (...args) => fetchWorkflowTemplates(...args),
  createWorkflowSet: (...args) => createWorkflowSet(...args),
  deleteWorkflowSet: (...args) => deleteWorkflowSet(...args),
  addWorkflowSetMembers: (...args) => addWorkflowSetMembers(...args),
  removeWorkflowSetMembers: (...args) => removeWorkflowSetMembers(...args),
  mergeWorkflowSets: vi.fn(),
  markWorkflowSetSeen: vi.fn().mockResolvedValue({ seen: [] }),
  renameWorkflowSet: (...args) => renameWorkflowSet(...args),
  setWorkflowSetDeclines: (...args) => setWorkflowSetDeclines(...args),
  setWorkflowSetsHidden: (...args) => setWorkflowSetsHidden(...args),
  putSetVerdict: (...args) => putSetVerdict(...args),
  putMemberVerdict: vi.fn(),
}));

const getWorkflowCard = vi.fn();
vi.mock("../../api/workflows", async (importOriginal) => ({
  ...(await importOriginal()),
  getWorkflowCard: (...args) => getWorkflowCard(...args),
}));

vi.mock("../../api/modelDownloads", () => ({
  startModelDownload: vi.fn(),
  cancelModelDownload: vi.fn(),
}));
vi.mock("../../api/modelIcons", () => ({
  setModelIcon: vi.fn(),
  clearModelIcons: vi.fn(),
  modelIconUrl: (sha) => `/api/v1/model-icons/${sha}`,
}));

import ModelSetGrid from "./ModelSetGrid.vue";
import { startModelDownload } from "../../api/modelDownloads";
import WorkflowSetChooser from "../panels/WorkflowSetChooser.vue";
import { pictureThumbnailUrl } from "../../api/pictures";
import { useModelShelfStore } from "../../stores/useModelShelfStore";
import { useOperationStore } from "../../stores/useOperationStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useTasksStore } from "../../stores/useTasksStore";

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      Tooltip: {
        props: ["text"],
        template:
          "<span :data-tip='text'><slot name='activator' :props='{}' /><slot /></span>",
      },
      RouterLink: {
        props: ["to"],
        template: "<a :data-workflow='to.query.workflow'><slot /></a>",
      },
    },
  },
};

/** A shelf row, as `/adapters` returns one. */
function row(id, filename, fileKind = "adapter") {
  return {
    id,
    sha256: String(id).repeat(64).slice(0, 64),
    file_kind: fileKind,
    kind: fileKind === "adapter" ? "lora" : null,
    display_name: null,
    filename,
    base_model: null,
    locations: [
      { state: "present", folder_id: 1, folder_path: "/m", relpath: filename },
    ],
    attachments: [],
  };
}

function member(id, name, kind, extra = {}) {
  return {
    id,
    name,
    kind,
    filename: name,
    file_size: 1000,
    ambiguous: false,
    ...extra,
  };
}

function combination(
  key,
  models,
  { recipes = 1, pictures = 1, covers = [7], runs = 0 } = {},
) {
  return {
    key,
    models,
    recipes,
    history_runs: runs,
    picture_count: pictures,
    // What the route serves: a picture and its cache key, never a path. An
    // `<img src>` bypasses the Axios interceptor, so the card is what turns
    // these into a URL through `pictureThumbnailUrl`.
    covers: covers.map((id) => ({ picture_id: id, version: `v${id}` })),
  };
}

const CKPT = member(1, "realvisXL_v5", "checkpoint");
const VAE = member(2, "sdxl_vae", "vae");
const LORA = member(3, "filmgrain_xl", "adapter");
const OTHER_CKPT = member(4, "juggernautXL_v9", "checkpoint");

async function mountGrid({
  combinations = [],
  noSet = [],
  rows = [],
  support = [],
  handMade = [],
  setChecks = [],
  attach = false,
  templates = [],
  newReady = 0,
} = {}) {
  listAdapters.mockResolvedValue(rows);
  listSupport.mockResolvedValue(support);
  fetchWorkflowSets.mockResolvedValue({
    combinations,
    no_set: noSet,
    hand_made: handMade,
    set_checks: setChecks,
    hidden_sets: [...hiddenOnServer],
    templates_new_ready: newReady,
  });
  fetchWorkflowTemplates.mockReset().mockResolvedValue({
    version: "0.1.117",
    comfyuiVersion: null,
    rows: templates,
  });
  const store = useModelShelfStore();
  await store.fetchRows();
  const wrapper = mount(ModelSetGrid, {
    ...globalOpts,
    ...(attach ? { attachTo: document.body } : {}),
  });
  await new Promise((resolve) => setTimeout(resolve, 0));
  await wrapper.vm.$nextTick();
  return { wrapper, store };
}

/** The tile opens the picker; its Make your own makes the empty set. */
async function makeYourOwn(wrapper) {
  await wrapper.find('[data-testid="new-workflow-set"]').trigger("click");
  await wrapper.find('[data-testid="make-your-own"]').trigger("click");
  await new Promise((resolve) => setTimeout(resolve, 0));
}

// Every grid unmounts after its test: a mounted template picker retries its
// focus for half a second, which otherwise outlives the file's environment.
enableAutoUnmount(afterEach);

beforeEach(() => {
  createWorkflowSet.mockReset();
  deleteWorkflowSet.mockReset();
  addWorkflowSetMembers.mockReset();
  removeWorkflowSetMembers.mockReset();
  renameWorkflowSet.mockReset();
  setWorkflowSetDeclines.mockReset();
  // A hide is a write to the library's own list, and the answer carries it: the
  // double keeps that list so a hide and the next read agree, as the server does.
  hiddenOnServer = new Set();
  setWorkflowSetsHidden.mockReset().mockImplementation(async (keys, hidden) => {
    for (const key of keys) {
      if (hidden) hiddenOnServer.add(key);
      else hiddenOnServer.delete(key);
    }
    return [...hiddenOnServer];
  });
  putSetVerdict.mockReset().mockResolvedValue({});
  listFavourites.mockReset().mockResolvedValue({ model: [], workflow: [], set: [] });
  setFavourites.mockReset().mockResolvedValue({ model: [], workflow: [], set: [] });
  setActivePinia(createPinia());
  // The grid is mounted without the shelf, which is what declares the host.
  useOperationStore().setLocalReceiptHost(true);
  window.localStorage.clear();
  getWorkflowCard.mockReset().mockResolvedValue({ card: { name: "" } });
  listAdapters.mockReset().mockResolvedValue([]);
  listSupport.mockReset().mockResolvedValue([]);
  fetchWorkflowSets
    .mockReset()
    .mockResolvedValue({ combinations: [], no_set: [] });
});

/**
 * A pointer click (`detail` 1) and a right-button press: `trigger` cannot set
 * either field, jsdom makes them getters. A keyboard click has `detail` 0.
 */
async function mouseClick(wrapper) {
  wrapper.element.dispatchEvent(
    new MouseEvent("click", { bubbles: true, detail: 1 }),
  );
}

async function rightPress(wrapper) {
  wrapper.element.dispatchEvent(
    new MouseEvent("pointerdown", { bubbles: true, button: 2 }),
  );
}

describe("the cards", () => {
  it("keeps a missing checkpoint's card when Show hides its support files", async () => {
    const { wrapper, store } = await mountGrid({
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [
        {
          ...combination('2+["gone.sft"]', [VAE]),
          missing: [{ name: "gone.sft", workflow_ids: [] }],
        },
      ],
    });
    // Show unticks the support files, the VAE with them.
    await store.setFilters({ support: false });
    await wrapper.vm.$nextTick();

    expect(wrapper.findAll('[data-testid="model-set-card"]')).toHaveLength(1);
  });

  it("draws a set with only a missing checkpoint, and opens its fix note", async () => {
    // An all-in-one checkpoint off the shelf: nothing the shelf holds, so no
    // member for `Show` to filter on and none for the tray to anchor on.
    const { wrapper, store } = await mountGrid({
      combinations: [
        {
          ...combination('+["aio.safetensors"]', []),
          missing: [{ name: "aio.safetensors", workflow_ids: ["auto:x"] }],
        },
      ],
    });

    expect(wrapper.find('[data-testid="model-set-card"]').text()).toContain(
      "aio",
    );
    store.toggleSet("missing:aio.safetensors");
    await wrapper.vm.$nextTick();
    const note = wrapper.find('[data-testid="model-set-missing"]');
    expect(note.exists()).toBe(true);
    expect(note.find("a").attributes("data-workflow")).toBe("auto:x");
  });

  it("names a set whose base model is gone after it, and links to its fix", async () => {
    const { wrapper, store } = await mountGrid({
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [
        {
          ...combination("2+flux1-dev.sft", [VAE]),
          missing: [{ name: "flux1-dev.sft", workflow_ids: ["auto:a"] }],
        },
      ],
    });

    const card = wrapper.find('[data-testid="model-set-card"]');
    expect(card.text()).toContain("flux1 dev");
    expect(card.text()).toContain("Missing");
    expect(card.text()).not.toContain("sdxl_vae");
    expect(card.attributes("aria-label")).toContain("base model not on shelf");

    store.toggleSet("missing:flux1-dev.sft");
    await wrapper.vm.$nextTick();
    const note = wrapper.find('[data-testid="model-set-missing"]');
    expect(note.text()).toContain("flux1-dev.sft");
    expect(note.find("a").attributes("data-workflow")).toBe("auto:a");
  });

  it("selects missing-base cards as themselves, with Ctrl and Space", async () => {
    const { wrapper, store } = await mountGrid({
      combinations: [
        {
          ...combination('+["a.sft"]', []),
          missing: [{ name: "a.sft", workflow_ids: ["auto:a"] }],
        },
        {
          ...combination('+["b.sft"]', []),
          missing: [{ name: "b.sft", workflow_ids: ["auto:b"] }],
        },
      ],
    });
    const rows = () => wrapper.findAll('.msg__row[data-key^="missing:"]');
    await rows()[0].trigger("click");
    await rows()[1].trigger("click", { ctrlKey: true });
    expect([...store.selectedMissingKeys]).toEqual(["missing:a.sft", "missing:b.sft"]);
    expect(rows().map((r) => r.attributes("aria-selected"))).toEqual(["true", "true"]);
    expect(store.selectedMissing.map((head) => head.name)).toEqual(["a.sft", "b.sft"]);
    // Space toggles the card under the cursor, as Ctrl+click does.
    await rows()[1].trigger("keydown", { key: " " });
    expect([...store.selectedMissingKeys]).toEqual(["missing:a.sft"]);
  });

  it("opens the Replace menu on a missing card, selecting it first", async () => {
    const { wrapper, store } = await mountGrid({
      combinations: [
        {
          ...combination('+["a.sft"]', []),
          missing: [{ name: "a.sft", workflow_ids: ["auto:a"] }],
        },
      ],
    });
    await wrapper.find('.msg__row[data-key="missing:a.sft"]').trigger("contextmenu");
    expect([...store.selectedMissingKeys]).toEqual(["missing:a.sft"]);
    expect(wrapper.emitted("missing-menu")).toHaveLength(1);
    expect(wrapper.emitted("menu")).toBeUndefined();
  });

  it("names the workflows in the note and offers to replace in all of them", async () => {
    getWorkflowCard.mockImplementation(async (id) => ({
      card: { id, name: id === "auto:n1" ? "Text to Image" : "Upscale" },
    }));
    const { wrapper, store } = await mountGrid({
      combinations: [
        {
          ...combination('+["gone.sft"]', []),
          missing: [{ name: "gone.sft", workflow_ids: ["auto:n1", "auto:n2"] }],
        },
      ],
    });
    store.toggleSet("missing:gone.sft");
    await wrapper.vm.$nextTick();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const note = wrapper.find('[data-testid="model-set-missing"]');
    expect(note.findAll("a").map((a) => a.text())).toEqual(["Text to Image", "Upscale"]);
    const replace = note.find('[data-testid="model-set-replace-missing"]');
    expect(replace.text()).toContain("Replace in all 2 workflows");
    await replace.trigger("click");
    const [[heads]] = wrapper.emitted("replace-missing");
    expect(heads[0].workflowsByName).toEqual({ "gone.sft": ["auto:n1", "auto:n2"] });
  });

  it("counts only the workflows Replace can write, never the manual ones", async () => {
    const { wrapper, store } = await mountGrid({
      combinations: [
        {
          ...combination('+["mix.sft"]', []),
          missing: [{ name: "mix.sft", workflow_ids: ["auto:m1", "manual:m2"] }],
        },
        {
          ...combination('+["solo.sft"]', []),
          missing: [{ name: "solo.sft", workflow_ids: ["manual:m3"] }],
        },
      ],
    });
    store.toggleSet("missing:mix.sft");
    await wrapper.vm.$nextTick();
    const replace = () => wrapper.find('[data-testid="model-set-replace-missing"]');
    expect(replace().text()).toBe("Replace…");
    // The manual one is named apart: its model is changed on its own
    // Workflow tab, not replaced from here.
    const note = wrapper.find('[data-testid="model-set-missing"]').text();
    expect(note).toMatch(/It is loaded by\s+\S/);
    expect(note).toContain("A manual workflow loads it too:");
    expect(note.replace(/\s+/g, " ")).toContain(
      "pick another model on its Workflow tab and save to change that.",
    );
    store.toggleSet("missing:solo.sft");
    await wrapper.vm.$nextTick();
    expect(replace().exists()).toBe(false);
  });

  it("says each one's Workflow tab when several manual workflows load a missing model", async () => {
    const { wrapper, store } = await mountGrid({
      combinations: [
        {
          ...combination('+["pair.sft"]', []),
          missing: [{ name: "pair.sft", workflow_ids: ["manual:p1", "manual:p2"] }],
        },
      ],
    });
    store.toggleSet("missing:pair.sft");
    await wrapper.vm.$nextTick();
    const note = wrapper.find('[data-testid="model-set-missing"]').text();
    expect(note.replace(/\s+/g, " ")).toContain("Manual workflows load it:");
    expect(note.replace(/\s+/g, " ")).toContain(
      "pick another model on each one's Workflow tab and save to change that.",
    );
  });

  it("ranges from the first missing card after select-all", async () => {
    const { store } = await mountGrid({
      combinations: ["a", "b", "c"].map((n) => ({
        ...combination(`+["${n}.sft"]`, []),
        missing: [{ name: `${n}.sft`, workflow_ids: ["auto:a"] }],
      })),
    });
    const order = store.missingSetKeys;
    store.selectVisible();
    store.selectMissing(order[1], { shift: true }, order);
    expect([...store.selectedMissingKeys]).toEqual(order.slice(0, 2));
  });

  it("draws one card per base model, named after it", async () => {
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [
        combination("1,2", [CKPT, VAE], { recipes: 4, pictures: 12 }),
        // A second recipe under the SAME checkpoint: one card, not two.
        combination("1,2,3", [CKPT, VAE, LORA], { recipes: 1, pictures: 3 }),
      ],
    });

    const cards = wrapper.findAll('[data-testid="model-set-card"]');
    expect(cards).toHaveLength(1);
    expect(cards[0].text()).toContain("realvisXL v5");
    // The member marks, the checkpoint first, each a Type letter and a name.
    const marks = cards[0].findAll(".msc__mark-chip");
    expect(marks.map((chip) => chip.find(".msc__k").text())).toEqual([
      "C",
      "V",
      "L",
    ]);
    expect(marks.map((chip) => chip.find(".msc__mark-name").text())).toEqual([
      "realvisXL v5",
      "sdxl vae",
      "filmgrain xl",
    ]);
    // The facts the card draws: size on disk (3 × 1000 bytes), never used.
    expect(cards[0].text()).toContain("2.9 KB");
    expect(cards[0].text()).toContain("never");
    // The count line is no longer drawn; it stays in the accessible name.
    expect(cards[0].attributes("aria-label")).toContain("3 models");
    expect(cards[0].attributes("aria-label")).toContain("5 recipes");
    expect(cards[0].attributes("aria-label")).toContain("15 pictures");
  });

  it("lays the cover out for the pictures it has, never leaving a hole", async () => {
    // `WorkflowCard` learned this once and the first copy of its geometry here
    // did not: a fixed 2fr/1fr mosaic drawn for three cells leaves an empty box
    // when a set has one or two pictures (#1479 review).
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2", [CKPT, VAE], { covers: [7] })],
    });

    const cover = wrapper.find(".msc__cover");
    expect(cover.classes()).toContain("msc__cover--1");
    expect(cover.findAll(".msc__pic")).toHaveLength(1);
    expect(cover.findAll("img")).toHaveLength(1);
  });

  it("draws three member marks, then a +N for the rest", async () => {
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [
        combination("1,2,3,4,5", [
          CKPT,
          VAE,
          LORA,
          member(4, "soft_light", "adapter"),
          member(5, "bad_hands", "adapter"),
        ]),
      ],
    });

    const card = wrapper.find('[data-testid="model-set-card"]');
    expect(card.findAll(".msc__mark-chip")).toHaveLength(3);
    expect(card.find(".msc__more").text()).toBe("+2");
    // The clipped two are still named, to a screen reader, on the card's label.
    expect(card.attributes("aria-label")).toContain("soft light");
    expect(card.attributes("aria-label")).toContain("bad hands");
  });

  it("flags a file the shelf lacks: a badge, and its name in warning ink", async () => {
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [
        {
          ...combination("1,2", [CKPT, VAE]),
          missing: [{ name: "qwen_2.5_vl_7b", workflow_ids: [] }],
        },
      ],
    });

    const card = wrapper.find('[data-testid="model-set-card"]');
    expect(card.find('[data-testid="set-badge-missing"]').text()).toBe(
      "Missing a file",
    );
    const missing = card.findAll(".msc__mark-chip--missing");
    expect(missing).toHaveLength(1);
    expect(missing[0].text()).toContain("qwen_2.5_vl_7b");
    expect(card.attributes("aria-label")).toContain("a file missing from the shelf");
  });

  it("draws the base model, the size on disk and when it last ran as chips", async () => {
    const { wrapper } = await mountGrid({
      rows: [{ ...row(1, "realvisXL_v5", "checkpoint"), base_model: "SDXL" }],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [
        {
          ...combination("1,2", [CKPT, VAE]),
          last_used_at: new Date(Date.now() - 3 * 3600 * 1000).toISOString(),
        },
      ],
    });

    // Two members of 1000 bytes each, and three hours since the last picture.
    const facts = wrapper.findAll(".msc__fact").map((fact) => fact.text());
    expect(facts).toEqual(["SDXL", "2.0 KB", "3h ago"]);
  });

  it("mirrors the cards drawn selected into the store, and clears it on unmount", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2", [CKPT, VAE])],
    });
    expect(store.pickedCardKeys).toEqual([]);

    store.toggleSelected(1);
    await wrapper.vm.$nextTick();
    expect(store.pickedCardKeys).toEqual(["model:1"]);

    store.clearSelection();
    await wrapper.vm.$nextTick();
    expect(store.pickedCardKeys).toEqual([]);

    store.toggleSelected(1);
    await wrapper.vm.$nextTick();
    wrapper.unmount();
    expect(store.pickedCardKeys).toEqual([]);
  });

  it("loads a cover through the api layer, base and cache key and all", async () => {
    // **An `<img src>` never reaches the Axios interceptor.** The payload sends
    // `{picture_id, version}`, and a card that put a bare path in `src` asked the
    // PAGE origin for a route it does not serve - every cover broken, and no
    // fixture noticed because each asserted the string it had just been handed.
    // So this asserts the URL the BROWSER is given, on the card and in the tray.
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2", [CKPT, VAE], { covers: [7] })],
    });

    const expected = pictureThumbnailUrl(7, { version: "v7" });
    expect(wrapper.find(".msc__cover img").attributes("src")).toBe(expected);
    expect(expected).toContain("/pictures/thumbnails/7.webp");
    expect(expected).toContain("v=v7");

    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-testid="model-set-panel"]').exists()).toBe(true);
  });

  it("says a card only ComfyUI ran came from ComfyUI (#1565)", async () => {
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [
        combination("1,2", [CKPT, VAE], {
          recipes: 0,
          pictures: 0,
          covers: [],
          runs: 3,
        }),
      ],
    });

    const card = wrapper.find('[data-testid="model-set-card"]');
    expect(card.text()).toContain("Ran in ComfyUI");
    expect(card.text()).toContain("3 runs");
    expect(card.text()).not.toContain("0 pictures");
    expect(card.findAll("img")).toHaveLength(0);
    expect(card.attributes("aria-label")).toContain("ran in ComfyUI");
  });

  it("says so on a card nothing else has ever run with", async () => {
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      combinations: [combination("1", [CKPT], { recipes: 3 })],
    });

    const card = wrapper.find('[data-testid="model-set-card"]');
    // Its one mark is the file itself, and the name says nothing else ran with it.
    expect(card.findAll(".msc__mark-chip")).toHaveLength(1);
    expect(card.attributes("aria-label")).toContain(
      "nothing else has run with it",
    );
    // No button opens the tray: a click selects, Enter and double-click open it.
    expect(wrapper.find(".msc__toggle").exists()).toBe(false);
  });

  it("opens a tray of the MODELS that have run with the base model", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5"), row(3, "filmgrain_xl")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [
        combination("1,2", [CKPT, VAE], { recipes: 5, pictures: 20 }),
        combination("1,2,3", [CKPT, VAE, LORA], { recipes: 2, pictures: 5 }),
      ],
    });
    // One card for the two combinations.
    expect(wrapper.findAll('[data-testid="model-set-card"]')).toHaveLength(1);

    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();

    // Three MEMBERS, one per model - not two members, one per combination.
    const members = wrapper.findAll('[data-testid="model-set-member"]');
    expect(members).toHaveLength(3);
    expect(members.map((m) => m.find(".msm__name").text())).toEqual([
      "realvisXL_v5",
      "sdxl_vae",
      "filmgrain_xl",
    ]);
    // The head is marked, and the LoRA carries its own evidence.
    expect(members[0].text()).toContain("Names this set");
    expect(members[2].text()).toContain("2 recipes");
    expect(members[2].text()).toContain("5 pictures");
  });

  it("says what a union cannot claim, before the list of it", async () => {
    // A group is every model its base model has co-occurred with, so two files in
    // one tray may never have run together. Without this the tray reads as a
    // reproducible set, which is the one claim this feature must not make.
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2", [CKPT, VAE])],
    });
    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();

    const note = wrapper.find(".msp__note");
    expect(note.exists()).toBe(true);
    expect(note.text()).toContain("not necessarily run with");
  });

  it("marks a member shared across sets, and one that is not", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5"), row(4, "juggernautXL_v9")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [
        combination("1,2", [CKPT, VAE], { pictures: 20 }),
        combination("2,4", [OTHER_CKPT, VAE], { pictures: 5 }),
      ],
    });
    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();

    const text = wrapper
      .findAll('[data-testid="model-set-member"]')
      .map((m) => m.text());
    // The VAE serves both checkpoints; the checkpoint serves only its own set.
    expect(text.find((t) => t.includes("sdxl_vae"))).toContain(
      "Also in 1 other set",
    );
    expect(text.find((t) => t.includes("realvisXL_v5"))).toContain(
      "Only in this set",
    );
  });

  it("flags a member the evidence could not pin down rather than hiding it", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2", [CKPT, { ...VAE, ambiguous: true }])],
    });
    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();

    const members = wrapper.findAll('[data-testid="model-set-member"]');
    const vae = members.find((m) => m.text().includes("sdxl_vae"));
    expect(vae.classes()).toContain("msm--unsure");
    expect(vae.text()).toContain("Which file ran is not recorded");
  });

  it("asks a model what else it has run with, naming that model", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2", [CKPT, VAE])],
    });
    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();

    await wrapper.findAll(".msm__name")[1].trigger("click");

    // The MODEL, by identity: the shelf owns the one dialog, so what this grid is
    // responsible for is handing up the right file.
    expect(wrapper.emitted("works-with")).toHaveLength(1);
    expect(wrapper.emitted("works-with")[0][0]).toMatchObject({
      id: 2,
      name: "sdxl_vae",
      kind: "vae",
    });
  });

  it("reaches every model in the tray from the keyboard", async () => {
    // The member cards' name buttons are at `tabindex="-1"` because the grid owns
    // Tab, so Enter on a tray row is the keyboard's route to a model's companions.
    // Now that a row IS one model, every one of them is reachable - the earlier
    // shape had a row per combination and could only offer its head (#1479).
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2", [CKPT, VAE])],
    });
    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();

    const grid = wrapper.find('[role="treegrid"]');
    // Down from the card lands on the first tray row, Down again on the second.
    await grid.trigger("keydown", { key: "ArrowDown" });
    await grid.trigger("keydown", { key: "ArrowDown" });
    await grid.trigger("keydown", { key: "Enter" });

    expect(wrapper.emitted("works-with")[0][0]).toMatchObject({
      name: "sdxl_vae",
    });
  });

  it("opens a shut Models rail on a card's double-click, tray and all", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2", [CKPT, VAE])],
    });
    const sidebar = useSidebarStore();
    const card = wrapper.find(".msg__row[data-key='model:1']");
    expect(sidebar.modelsRailOpen).toBe(false);

    await card.trigger("dblclick");
    expect(store.openSetKey).toBe("model:1");
    expect(sidebar.modelsRailOpen).toBe(true);
    expect(sidebar.cardTabRequest).toBe(1);

    // Open already: the tray still closes, the rail keeps its tab.
    await card.trigger("dblclick");
    expect(store.openSetKey).toBe("");
    expect(sidebar.cardTabRequest).toBe(1);
  });

  it("does the double-click's work from a card's Edit button", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2", [CKPT, VAE])],
    });
    const sidebar = useSidebarStore();
    const edit = () =>
      wrapper.find(".msg__row[data-key='model:1'] [data-testid='set-edit']");

    await edit().trigger("click");
    expect(store.openSetKey).toBe("model:1");
    expect(sidebar.modelsRailOpen).toBe(true);
    expect(sidebar.cardTabRequest).toBe(1);
    // The press opens the tray; it does not select the card.
    expect([...store.selectedIds]).toEqual([]);

    await edit().trigger("click");
    expect(store.openSetKey).toBe("");
    wrapper.unmount();
  });

  it("opens and closes a tray from the keyboard", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2", [CKPT, VAE])],
    });
    const grid = wrapper.find('[role="treegrid"]');

    await grid.trigger("keydown", { key: "Enter" });
    expect(store.openSetKey).toBe("model:1");

    await grid.trigger("keydown", { key: "Escape" });
    expect(store.openSetKey).toBe("");
  });

  it("steps off the Grid/List switch into the tray, not back to Grid", async () => {
    // Clicking List leaves focus on the switch. Down there used to be the
    // radiogroup's "next option" and flipped the tray straight back to Grid.
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2", [CKPT, VAE])],
    });
    store.toggleSet("model:1");
    store.setView({ trayView: "list" });
    await wrapper.vm.$nextTick();

    const list = wrapper.findAll('.msp__header [role="radio"]')[1];
    await list.trigger("keydown", { key: "ArrowDown" });
    expect(store.view.trayView).toBe("list");
    const grid = wrapper.find('[role="treegrid"]');
    await grid.trigger("keydown", { key: "Enter" });
    // The cursor is on a member, so Enter asks what it has run with.
    expect(wrapper.emitted("works-with")).toHaveLength(1);

    // Up from the switch returns to the card, whose Enter closes the tray.
    await list.trigger("keydown", { key: "ArrowUp" });
    expect(store.view.trayView).toBe("list");
    await grid.trigger("keydown", { key: "Enter" });
    expect(store.openSetKey).toBe("");
  });

  it("draws the tray as a list when the reader asks for one", async () => {
    // The Workflows stack panel's own switch, so a reader learns it once. The
    // columns differ because a set's members are models.
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2", [CKPT, VAE])],
    });
    store.toggleSet("model:1");
    store.setView({ trayView: "list" });
    await wrapper.vm.$nextTick();

    expect(wrapper.findAll('[data-testid="model-set-member"]')).toHaveLength(0);
    const heads = wrapper.findAll('[role="columnheader"]').map((h) => h.text());
    expect(heads).toEqual([
      "Model",
      "Kind",
      "Size",
      "Recipes",
      "Pictures",
      "In other sets",
    ]);
    // A row is still a level-2 treegrid row. A single click SELECTS it, as it
    // does on the row list; the question the card's name asks in Grid is this
    // row's default action, so it is a double click.
    await wrapper.findAll(".msp__row")[1].trigger("click");
    expect(wrapper.emitted("works-with")).toBeUndefined();
    await wrapper.findAll(".msp__row")[1].trigger("dblclick");
    expect(wrapper.emitted("works-with")[0][0]).toMatchObject({
      name: "sdxl_vae",
    });
  });

  /** A four-column card grid, which jsdom cannot give us by measuring. */
  function stubFourColumns() {
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor() {}
        observe(element) {
          Object.defineProperty(element, "clientWidth", {
            configurable: true,
            value: 1000,
          });
        }
        disconnect() {}
      },
    );
  }

  it("enters a list tray at its first row, whatever column the card was in", async () => {
    // **Two grids of different widths, so the crossing is not arithmetic.** A
    // List tray is one column whatever the card grid is doing, and stepping by
    // the outer column count carried the card's column offset into it: from the
    // THIRD card of a row, Down landed on the tray's third row. The within-tray
    // step was fixed without this half, so the skip survived at the boundary.
    stubFourColumns();
    try {
      const { wrapper, store } = await mountGrid({
        rows: [row(1, "realvisXL_v5"), row(3, "filmgrain_xl"), row(5, "third")],
        support: [row(2, "sdxl_vae", "vae"), row(6, "clipL", "text_encoder")],
        combinations: [
          combination(
            "5,2,6",
            [
              member(5, "third", "checkpoint"),
              VAE,
              member(6, "clipL", "text_encoder"),
            ],
            { pictures: 1 },
          ),
          combination("1,2", [CKPT, VAE], { pictures: 9 }),
          combination("3x", [member(3, "filmgrain_xl", "checkpoint")], {
            pictures: 5,
          }),
        ],
      });
      // The third card in the row is the one whose tray is open.
      store.toggleSet("model:5");
      store.setView({ trayView: "list" });
      await wrapper.vm.$nextTick();
      expect(
        wrapper.findAll(".msg__row").map((c) => c.attributes("data-key")),
      ).toEqual(["model:1", "model:3", "model:5"]);

      const grid = wrapper.find('[role="treegrid"]');
      await grid.trigger("keydown", { key: "ArrowRight" });
      await grid.trigger("keydown", { key: "ArrowRight" });
      await grid.trigger("keydown", { key: "ArrowDown" });
      await grid.trigger("keydown", { key: "Enter" });

      // The tray's FIRST row, not its third. Asserted on the model's name
      // rather than a position, so the fixture order cannot make it pass for
      // the wrong reason.
      expect(wrapper.emitted("works-with")[0][0]).toMatchObject({
        name: "third",
      });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("leaves a list tray at its last row when stepping up from below it", async () => {
    // The same crossing the other way. The tray sits between the first card row
    // and the cards after it, so Up from one of those has to arrive at the row
    // nearest it rather than at a column that tray does not have.
    stubFourColumns();
    try {
      const { wrapper, store } = await mountGrid({
        rows: Array.from({ length: 6 }, (_, i) => row(i + 10, `ckpt${i}`)),
        support: [row(2, "sdxl_vae", "vae")],
        combinations: [
          combination("10,2", [member(10, "ckpt0", "checkpoint"), VAE], {
            pictures: 9,
          }),
          ...[11, 12, 13, 14, 15].map((id, i) =>
            combination(`${id}`, [member(id, `ckpt${i + 1}`, "checkpoint")], {
              pictures: 8 - i,
            }),
          ),
        ],
      });
      store.toggleSet("model:10");
      store.setView({ trayView: "list" });
      await wrapper.vm.$nextTick();

      const grid = wrapper.find('[role="treegrid"]');
      // Down from the first card enters the tray, Down again leaves it for the
      // second row of cards; Up from there must come back to the tray's LAST
      // row, which is the VAE.
      await grid.trigger("keydown", { key: "ArrowDown" });
      await grid.trigger("keydown", { key: "ArrowDown" });
      await grid.trigger("keydown", { key: "ArrowDown" });
      await grid.trigger("keydown", { key: "ArrowUp" });
      await grid.trigger("keydown", { key: "Enter" });

      expect(wrapper.emitted("works-with")[0][0]).toMatchObject({
        name: "sdxl_vae",
      });
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("moves one list-tray row at a time even under a multi-column card grid", async () => {
    // Make the outer grid four columns wide. The list tray remains one column,
    // which is the mismatch that used to make Down skip its members.
    stubFourColumns();
    try {
      const { wrapper, store } = await mountGrid({
        rows: [row(1, "realvisXL_v5")],
        support: [row(2, "sdxl_vae", "vae"), row(3, "clip", "text_encoder")],
        combinations: [combination("1,2,3", [CKPT, VAE, LORA])],
      });
      store.toggleSet("model:1");
      store.setView({ trayView: "list" });
      await wrapper.vm.$nextTick();

      const grid = wrapper.find('[role="treegrid"]');
      await grid.trigger("keydown", { key: "ArrowDown" });
      await grid.trigger("keydown", { key: "ArrowDown" });
      await grid.trigger("keydown", { key: "Enter" });

      expect(wrapper.emitted("works-with")[0][0]).toMatchObject({
        name: "sdxl_vae",
      });
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe("the models no recipe names", () => {
  it("draws no card for them: the Models rail lists them", async () => {
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "never_run", "vae")],
      combinations: [combination("1", [CKPT])],
      noSet: [2],
    });

    expect(wrapper.text()).not.toContain("In no set");
    expect(wrapper.text()).not.toContain("never_run");
    // One card, plus the New workflow set tile (#1520).
    const grid = wrapper.find('[role="treegrid"]');
    expect(grid.findAll('[role="row"]')).toHaveLength(2);
    expect(grid.findAll(".msg__row")).toHaveLength(1);
  });

  it("says there is nothing to group when they are all the shelf holds, filtered or not", async () => {
    // A VAE and a LoRA that ran together beside no base model: a combination,
    // served for Works with, and no card.
    const { wrapper, store } = await mountGrid({
      rows: [row(3, "filmgrain_xl")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("2,3", [VAE, LORA])],
      noSet: [2, 3],
    });
    expect(wrapper.text()).toContain("No picture in this library records");
    expect(wrapper.findAll(".msg__row")).toHaveLength(0);

    // No set exists for Show to be hiding, so a filter must not turn this into
    // "1 set is being left out by Show".
    await store.setFilters({ support: false });
    await wrapper.vm.$nextTick();

    expect(store.activeCount).toBeGreaterThan(0);
    expect(wrapper.text()).toContain("No picture in this library records");
    expect(wrapper.text()).not.toContain("No workflow set matches");
    expect(wrapper.find('[data-testid="new-workflow-set"]').exists()).toBe(
      true,
    );
  });
});

describe("selection and the verbs", () => {
  /** The grid with one card, its tray open, and three selectable models. */
  async function openTray() {
    const state = await mountGrid({
      rows: [row(1, "realvisXL_v5"), row(3, "filmgrain_xl")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2,3", [CKPT, VAE, LORA])],
    });
    state.store.toggleSet("model:1");
    await state.wrapper.vm.$nextTick();
    return state;
  }

  it("selects the card's BASE MODEL, never the set behind it", async () => {
    // The whole reason this screen is safe to aim a Delete at. A card standing
    // for its tray would put a shared VAE behind a Delete meant for a
    // checkpoint; it stands for the one file it is named after instead.
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5"), row(3, "filmgrain_xl")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2,3", [CKPT, VAE, LORA])],
    });

    const grid = wrapper.find('[role="treegrid"]');
    expect(grid.attributes("aria-multiselectable")).toBe("true");
    await grid.find(".msg__row").trigger("click");

    expect([...store.selectedIds]).toEqual([1]);
    expect(grid.find(".msg__row").attributes("aria-selected")).toBe("true");
    expect(wrapper.find('[data-testid="model-set-card"]').classes()).toContain(
      "msc--on",
    );
  });

  it("clears the selection on a click on the background, not on a card", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2", [CKPT, VAE])],
    });
    await wrapper.find(".msg__row").trigger("click");
    expect([...store.selectedIds]).toEqual([1]);

    const grid = wrapper.find('[role="treegrid"]');
    // Pressed on a card, released elsewhere: the click lands on the grid but
    // is a fumbled pick, not a click on nothing.
    await wrapper.find(".msg__row").trigger("pointerdown");
    await mouseClick(grid);
    expect([...store.selectedIds]).toEqual([1]);

    // A right-press ends without a click; a keyboard click (detail 0) after a
    // left one does not count either.
    await rightPress(grid);
    await mouseClick(grid);
    expect([...store.selectedIds]).toEqual([1]);
    await grid.trigger("pointerdown");
    await grid.trigger("click");
    expect([...store.selectedIds]).toEqual([1]);

    await grid.trigger("pointerdown");
    await mouseClick(grid);
    expect([...store.selectedIds]).toEqual([]);

    await wrapper.find(".msg__row").trigger("click");
    await wrapper.find(".msg__scroll").trigger("pointerdown");
    await mouseClick(wrapper.find(".msg__scroll"));
    expect([...store.selectedIds]).toEqual([]);
  });

  it("selects one model in the tray, and marks it", async () => {
    const { wrapper, store } = await openTray();

    const members = wrapper.findAll(".msp__member");
    await members[1].trigger("click");

    expect([...store.selectedIds]).toEqual([2]);
    expect(members[1].attributes("aria-selected")).toBe("true");
    expect(
      wrapper.findAll('[data-testid="model-set-member"]')[1].classes(),
    ).toContain("msm--on");
  });

  it("does not light the card when its checkpoint is picked in the tray", async () => {
    // The head is drawn twice while its tray is open. Picking the tray row is a
    // pick of that one file, and a lit card reads as the whole set picked.
    const { wrapper, store } = await openTray();
    const card = () => wrapper.find('[data-testid="model-set-card"]');
    const row = () => wrapper.find(".msg__row");

    await wrapper.findAll(".msp__member")[0].trigger("click");

    expect([...store.selectedIds]).toEqual([1]);
    expect(wrapper.findAll(".msp__member")[0].attributes("aria-selected")).toBe(
      "true",
    );
    expect(card().classes()).not.toContain("msc--on");
    expect(row().attributes("aria-selected")).toBe("false");

    // The same model picked from its card does light it.
    await row().trigger("click");
    expect([...store.selectedIds]).toEqual([1]);
    expect(card().classes()).toContain("msc--on");

    // And a selection made anywhere else - Select all - follows the model again.
    await wrapper.findAll(".msp__member")[0].trigger("click");
    expect(card().classes()).not.toContain("msc--on");
    store.selectVisible();
    await wrapper.vm.$nextTick();
    expect(card().classes()).toContain("msc--on");
  });

  it("names the lit cards' sets for Hide, and no set for a tray pick", async () => {
    const { wrapper } = await mountGrid({
      rows: [
        row(1, "realvisXL_v5", "checkpoint"),
        row(3, "filmgrain_xl"),
        row(4, "Juggernaut_XL", "checkpoint"),
      ],
      combinations: [
        combination("1,3", [CKPT, LORA]),
        combination("4,3", [member(4, "Juggernaut_XL", "checkpoint"), LORA]),
      ],
    });
    const cards = () => wrapper.findAll(".msg__row[data-key^='model:']");
    // Off what the grid EXPOSES, which is all the shelf's template ref can
    // read: test-utils' `vm` would serve it from setup state either way.
    const keys = () =>
      wrapper.vm.$.exposed.selectedCardGroups.value.map((group) => group.key);
    expect(keys()).toEqual([]);

    await cards()[0].trigger("click");
    await cards()[1].trigger("click", { ctrlKey: true });
    expect(keys()).toEqual(cards().map((card) => card.attributes("data-key")));

    // The head picked in its own tray is a file: the card is unlit, and Hide
    // must not reach a set the reader never selected.
    await cards()[0].trigger("click");
    await cards()[0].trigger("dblclick");
    await wrapper.findAll(".msp__member")[0].trigger("click");
    expect(wrapper.findAll(".msp__member")[0].attributes("aria-selected")).toBe(
      "true",
    );
    expect(keys()).toEqual([]);
  });

  describe("a click with a tray open", () => {
    async function twoCards() {
      const state = await mountGrid({
        rows: [
          row(1, "realvisXL_v5", "checkpoint"),
          row(3, "filmgrain_xl"),
          row(4, "Juggernaut_XL", "checkpoint"),
        ],
        combinations: [
          combination("1,3", [CKPT, LORA]),
          combination("4,3", [member(4, "Juggernaut_XL", "checkpoint"), LORA]),
        ],
      });
      const cards = state.wrapper.findAll(".msg__row[data-key^='model:']");
      const key = (card) => card.attributes("data-key");
      const headOf = (card) => Number(key(card).split(":")[1]);
      return { ...state, cards, key, headOf };
    }

    it("moves the tray to the card clicked, and selects it", async () => {
      const { store, cards, key, headOf } = await twoCards();

      // Nothing open: a click selects and opens nothing.
      await cards[0].trigger("click");
      expect(store.openSetKey).toBe("");

      store.toggleSet(key(cards[0]));
      await cards[1].trigger("click");
      expect(store.openSetKey).toBe(key(cards[1]));
      expect([...store.selectedIds]).toEqual([headOf(cards[1])]);

      // The open card itself stays open under a click.
      await cards[1].trigger("click");
      expect(store.openSetKey).toBe(key(cards[1]));
    });

    it("leaves the tray where it is under Ctrl and Shift", async () => {
      const { store, cards, key } = await twoCards();
      store.toggleSet(key(cards[0]));

      await cards[1].trigger("click", { ctrlKey: true });
      await cards[1].trigger("click", { shiftKey: true });
      expect(store.openSetKey).toBe(key(cards[0]));
    });

    it("swallows the rest of a double click that moved the tray", async () => {
      const { wrapper, store, cards, key, headOf } = await twoCards();
      // `trigger` cannot set `detail`, which is what tells the presses apart.
      const press = async (card, type, detail) => {
        card.element.dispatchEvent(
          new MouseEvent(type, { bubbles: true, cancelable: true, detail }),
        );
        await wrapper.vm.$nextTick();
      };
      store.toggleSet(key(cards[0]));

      // The second press lands on whatever slid under the pointer.
      await press(cards[1], "click", 1);
      await press(cards[0], "click", 2);
      await press(cards[0], "dblclick", 2);
      expect(store.openSetKey).toBe(key(cards[1]));
      expect([...store.selectedIds]).toEqual([headOf(cards[1])]);

      // A move with no second press owes nothing: the next click is its own.
      await press(cards[0], "click", 1);
      await press(cards[1], "click", 1);
      expect(store.openSetKey).toBe(key(cards[1]));

      // And a double click on the open card still closes it.
      await press(cards[1], "click", 1);
      await press(cards[1], "click", 2);
      await press(cards[1], "dblclick", 2);
      expect(store.openSetKey).toBe("");
    });
  });

  it("adds an unlit card with Ctrl or Space rather than toggling its model out", async () => {
    const { wrapper, store } = await openTray();
    const card = () => wrapper.find('[data-testid="model-set-card"]');
    const members = () => wrapper.findAll(".msp__member");

    await members()[0].trigger("click");
    await wrapper.find(".msg__row").trigger("click", { ctrlKey: true });
    expect([...store.selectedIds]).toEqual([1]);
    expect(card().classes()).toContain("msc--on");

    // Space is the keyboard's Ctrl+click and must answer the same way.
    await members()[0].trigger("click");
    const grid = wrapper.find('[role="treegrid"]');
    await grid.trigger("keydown", { key: "ArrowUp" });
    await grid.trigger("keydown", { key: " " });
    expect([...store.selectedIds]).toEqual([1]);
    expect(card().classes()).toContain("msc--on");
  });

  it("right-clicking the checkpoint's tray row selects that row, not its card", async () => {
    // The head occurs twice; a lookup by model id would find the card first.
    const { wrapper, store } = await openTray();
    await wrapper
      .findAll(".msp__member")[0]
      .trigger("contextmenu", { clientX: 1, clientY: 2 });

    expect([...store.selectedIds]).toEqual([1]);
    expect(
      wrapper.find('[data-testid="model-set-card"]').classes(),
    ).not.toContain("msc--on");
  });

  it("lights the card again once the tray showing the pick closes", async () => {
    // Otherwise the bar is armed over a grid that marks nothing selected.
    const { wrapper, store } = await openTray();
    await wrapper.findAll(".msp__member")[0].trigger("click");
    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();

    expect([...store.selectedIds]).toEqual([1]);
    expect(wrapper.find('[data-testid="model-set-card"]').classes()).toContain(
      "msc--on",
    );
  });

  it("lights the card a Shift range passes over, and not one it does not", async () => {
    const { wrapper, store } = await openTray();
    const card = () => wrapper.find('[data-testid="model-set-card"]');
    const members = () => wrapper.findAll(".msp__member");

    // Within the tray: the range never touches the card.
    await members()[0].trigger("click");
    await members()[1].trigger("click", { shiftKey: true });
    expect([...store.selectedIds].sort()).toEqual([1, 2]);
    expect(card().classes()).not.toContain("msc--on");

    // From the card down into the tray: the card is in the range.
    await wrapper.find(".msg__row").trigger("click");
    await members()[1].trigger("click", { shiftKey: true });
    expect([...store.selectedIds].sort()).toEqual([1, 2]);
    expect(card().classes()).toContain("msc--on");
  });

  it("forgets a tray pick that Ctrl took back out", async () => {
    const { wrapper, store } = await openTray();
    const member = () => wrapper.findAll(".msp__member")[0];

    await member().trigger("click");
    await member().trigger("click", { ctrlKey: true });
    expect([...store.selectedIds]).toEqual([]);
    await wrapper.find(".msg__row").trigger("click", { ctrlKey: true });

    expect([...store.selectedIds]).toEqual([1]);
    expect(wrapper.find('[data-testid="model-set-card"]').classes()).toContain(
      "msc--on",
    );
  });

  it("does not light another set's card when its checkpoint is picked in this tray", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5"), row(4, "juggernautXL_v9")],
      combinations: [
        combination("1,4", [CKPT, OTHER_CKPT]),
        combination("4", [OTHER_CKPT]),
      ],
    });
    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();

    const members = wrapper.findAll(".msp__member");
    const other = members.find((m) => m.attributes("data-key") === "4");
    await members[0].trigger("click");
    await other.trigger("click", { ctrlKey: true });

    expect([...store.selectedIds].sort()).toEqual([1, 4]);
    const cards = wrapper.findAll('[data-testid="model-set-card"]');
    expect(cards).toHaveLength(2);
    for (const card of cards) expect(card.classes()).not.toContain("msc--on");
  });

  it("toggles with Ctrl and takes a range with Shift, in DRAWN order", async () => {
    const { wrapper, store } = await openTray();

    const card = wrapper.find(".msg__row");
    const members = wrapper.findAll(".msp__member");

    await members[1].trigger("click");
    await members[2].trigger("click", { ctrlKey: true });
    expect([...store.selectedIds].sort()).toEqual([2, 3]);

    // A range from the LoRA back up to the card spans the drawn order - the
    // card, then the tray rows under it - and replaces the selection.
    await card.trigger("click");
    await members[2].trigger("click", { shiftKey: true });
    expect([...store.selectedIds].sort()).toEqual([1, 2, 3]);
  });

  it("gives the verb menu the pointer, having first selected what is under it", async () => {
    // The file-manager rule the row list already follows: right-clicking
    // something not selected selects it and acts on it alone.
    const { wrapper, store } = await openTray();

    await wrapper
      .findAll(".msp__member")[2]
      .trigger("contextmenu", { clientX: 120, clientY: 340 });

    expect([...store.selectedIds]).toEqual([3]);
    // A tray row is a file in somebody's set, not a set: no card is named.
    expect(wrapper.emitted("menu")[0][0]).toEqual({ x: 120, y: 340, setKey: "" });

    // A card names its set, which is what Clone set to edit is about.
    await wrapper
      .find('[data-testid="model-set-card"]')
      .trigger("contextmenu", { clientX: 5, clientY: 6 });
    expect(wrapper.emitted("menu")[1][0]).toEqual({
      x: 5,
      y: 6,
      setKey: "model:1",
    });
  });

  it("leaves a selection of forty alone when one of them is right-clicked", async () => {
    const { wrapper, store } = await openTray();

    await wrapper.findAll(".msp__member")[1].trigger("click");
    await wrapper
      .findAll(".msp__member")[2]
      .trigger("click", { ctrlKey: true });
    await wrapper
      .findAll(".msp__member")[2]
      .trigger("contextmenu", { clientX: 1, clientY: 2 });

    expect([...store.selectedIds].sort()).toEqual([2, 3]);
    expect(wrapper.emitted("menu")).toHaveLength(1);
  });

  it("selects a model the Show narrowing hides from the row list", async () => {
    // A combination survives `Show` on any one visible member and is drawn
    // WHOLE, so a tray routinely lists files the row list is hiding. Untick
    // Adapters and the LoRA is still on screen - so it still has to be
    // selectable, and the verb bar still has to see it, or the click is a
    // silent no-op. `setGridModelIds` is what makes that true.
    const { wrapper, store } = await openTray();
    store.setFilters({ adapters: false });
    await wrapper.vm.$nextTick();

    expect(store.visibleRows.some((r) => r.id === 3)).toBe(false);
    await wrapper.findAll(".msp__member")[2].trigger("click");
    expect(store.selectedRows.map((r) => r.id)).toEqual([3]);
  });

  it("toggles with Space and extends with Shift+arrow, off the keyboard", async () => {
    const { wrapper, store } = await openTray();
    const grid = wrapper.find('[role="treegrid"]');

    // The cursor starts on the card. Space toggles it in, as it does on a row.
    await grid.trigger("keydown", { key: " " });
    expect([...store.selectedIds]).toEqual([1]);

    // The first Down lands on the head's OWN tray row - a set's head is drawn
    // twice while its tray is open, as the card and as the row marked *Names
    // this set* - so the range is still the one model. The second reaches the
    // VAE and extends it.
    await grid.trigger("keydown", { key: "ArrowDown", shiftKey: true });
    expect([...store.selectedIds]).toEqual([1]);
    await grid.trigger("keydown", { key: "ArrowDown", shiftKey: true });
    expect([...store.selectedIds].sort()).toEqual([1, 2]);
  });

  it("measures a Shift range from the clicked tray occurrence, not its card", async () => {
    // A occurs both as the first card and the first tray row. B and C are
    // visible cards between those occurrences, but were not in this range.
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "a"), row(2, "b"), row(3, "c")],
      support: [row(4, "d", "vae")],
      combinations: [
        combination("1,4", [
          member(1, "a", "checkpoint"),
          member(4, "d", "vae"),
        ]),
        combination("2", [member(2, "b", "checkpoint")]),
        combination("3", [member(3, "c", "checkpoint")]),
      ],
    });
    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();

    const members = wrapper.findAll(".msp__member");
    await members[0].trigger("click");
    await members[1].trigger("click", { shiftKey: true });

    expect([...store.selectedIds].sort()).toEqual([1, 4]);
  });

  it("renames what the cursor is on when F2 is pressed", async () => {
    // The verb menu advertises `F2` with a keycap, so it has to answer here.
    const { wrapper, store } = await openTray();
    const grid = wrapper.find('[role="treegrid"]');

    // Something else is selected: F2 still takes the model under the cursor.
    store.toggleSelected(3);
    await grid.trigger("keydown", { key: "ArrowDown" });
    await grid.trigger("keydown", { key: "ArrowDown" });
    await grid.trigger("keydown", { key: "F2" });

    expect([...store.selectedIds]).toEqual([2]);
    expect(wrapper.emitted("rename")).toHaveLength(1);
  });

  it("opens the verb menu from the Menu key and from Shift+F10", async () => {
    const { wrapper } = await openTray();
    const grid = wrapper.find('[role="treegrid"]');

    await grid.trigger("keydown", { key: "ContextMenu" });
    await grid.trigger("keydown", { key: "F10", shiftKey: true });
    expect(wrapper.emitted("menu")).toHaveLength(2);
  });

  it("lets Escape through to clear the selection once no tray is open", async () => {
    // One Escape, one thing undone: with a tray open the press closes it and is
    // STOPPED, so the shelf's window listener cannot also clear the selection
    // on the way out. With nothing open it is let through.
    const { wrapper, store } = await openTray();
    document.body.appendChild(wrapper.element);
    const grid = wrapper.find('[role="treegrid"]');
    const seen = [];
    const listen = (event) => seen.push(event.key);
    window.addEventListener("keydown", listen);
    try {
      await grid.trigger("keydown", { key: "Escape" });
      expect(store.openSetKey).toBe("");
      expect(seen).toEqual([]);

      await grid.trigger("keydown", { key: "Escape" });
      expect(seen).toEqual(["Escape"]);
    } finally {
      window.removeEventListener("keydown", listen);
    }
  });

  it("leaves the selection alone when the card's hide button is pressed", async () => {
    // The card's own buttons are not picks. Hide's click bubbles out of the
    // button and into the row, so unguarded it replaced the reader's selection
    // with that card - arming a model they never pointed at for a verb that has
    // no undo. (The ▸ that once sat here is gone; this is the button left.)
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5"), row(3, "filmgrain_xl")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [
        combination("1,2", [CKPT, VAE]),
        combination("4,2", [OTHER_CKPT, VAE]),
      ],
    });
    store.toggleSelected(2);
    store.toggleSelected(3);

    await wrapper.find('[data-testid="set-hide"]').trigger("click");

    expect(store.openSetKey).toBe("");
    expect([...store.selectedIds].sort()).toEqual([2, 3]);
  });

  it("selects a card on click and opens its tray only on double-click", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [combination("1,2", [CKPT, VAE])],
    });
    const card = wrapper.find('[data-testid="model-set-card"]');

    await card.trigger("click");
    expect(store.openSetKey).toBe("");
    expect([...store.selectedIds]).toEqual([1]);

    await card.trigger("dblclick");
    expect(store.openSetKey).toBe("model:1");
  });

  it("offers no selection for a model the shelf has no row for", async () => {
    // A combination can name a file from a block this session never fetched.
    // There is nothing to rename, move or delete for one of those, so the row
    // does not claim to be selectable and a click on it does nothing - better
    // than a tick the verb bar then cannot honour.
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5")],
      combinations: [
        combination("1,9", [CKPT, member(9, "never_fetched_vae", "vae")]),
      ],
    });
    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();

    const ghost = wrapper.findAll(".msp__member")[1];
    expect(ghost.attributes("data-key")).toBe("9");
    expect(ghost.attributes("aria-selected")).toBeUndefined();
    await ghost.trigger("click");
    await ghost.trigger("contextmenu", { clientX: 1, clientY: 1 });
    expect(store.selectedIds.size).toBe(0);
    expect(wrapper.emitted("menu")).toBeUndefined();
  });
});

describe("when there is nothing to group", () => {
  it("says no picture records its models, and does not call that a verdict", async () => {
    const { wrapper } = await mountGrid({
      rows: [],
      combinations: [],
      noSet: [],
    });

    expect(wrapper.text()).toContain("No picture in this library records");
    // The grid is still there, holding only the New workflow set tile: making
    // a set by hand is what an owner with no recorded pictures can do (#1520).
    expect(wrapper.findAll(".msg__row")).toHaveLength(0);
    expect(wrapper.find('[data-testid="new-workflow-set"]').exists()).toBe(
      true,
    );
  });

  it("reports a failed read rather than an empty grid", async () => {
    listAdapters.mockResolvedValue([]);
    fetchWorkflowSets.mockRejectedValue(new Error("hub is busy"));
    const store = useModelShelfStore();
    await store.fetchRows();
    const wrapper = mount(ModelSetGrid, globalOpts);
    await new Promise((resolve) => setTimeout(resolve, 0));
    await wrapper.vm.$nextTick();

    expect(wrapper.find('[role="alert"]').text()).toContain("hub is busy");
  });
});

describe("a tray shows who a model is assigned to", () => {
  it("draws a person-assigned LoRA with that person's face, in both trays and both views", async () => {
    const assigned = {
      ...row(3, "filmgrain_xl"),
      attachments: [{ entity_type: "character", entity_id: 7 }],
    };
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), assigned],
      combinations: [combination("1,3", [CKPT, LORA])],
    });
    const face = (el) =>
      el
        .findAll("img")
        .some((img) =>
          img.attributes("src")?.includes("/characters/7/thumbnail"),
        );

    // The evidence tray, Grid view: the member card.
    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();
    const card = wrapper
      .findAll('[data-testid="model-set-member"]')
      .find((el) => el.text().includes("filmgrain_xl"));
    expect(face(card)).toBe(true);

    // ...and List view.
    store.setView({ trayView: "list" });
    await wrapper.vm.$nextTick();
    const listRow = wrapper.find('.msp__row[data-key="3"]');
    expect(face(listRow)).toBe(true);
  });

  it("names a model's path and whole SHA-256 in the tray, as cards and as a list", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5"), row(3, "filmgrain_xl")],
      combinations: [combination("1,3", [CKPT, LORA])],
    });
    const title = `/m/filmgrain_xl\nSHA-256: ${"3".repeat(64)}`;
    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();
    const card = wrapper.findAll('[data-testid="model-set-member"]')[1];
    expect(card.find(".msm__file [data-tip]").attributes("data-tip")).toBe(
      title,
    );

    store.setView({ trayView: "list" });
    await wrapper.vm.$nextTick();
    expect(
      wrapper
        .find('.msp__row[data-key="3"] .msp__rowname [data-tip]')
        .attributes("data-tip"),
    ).toBe(title);
  });

  it("names the SHA-256 of a hand-made set's model that has left the shelf", async () => {
    const sha = "c".repeat(64);
    const { wrapper, store } = await mountGrid({
      rows: [],
      handMade: [
        {
          id: 10,
          name: null,
          members: [
            {
              sha256: sha,
              slot: "lora",
              label: "gone_xl",
              on_shelf: false,
              id: null,
              name: "gone_xl",
            },
          ],
          covers: [],
          picture_count: 0,
          incomplete: true,
        },
      ],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    expect(
      wrapper
        .find(`[data-key="m:${sha}"] .mss__tilename [data-tip]`)
        .attributes("data-tip"),
    ).toBe(`SHA-256: ${sha}`);
  });

  it("does the same on a hand-made set's slot tile", async () => {
    const assigned = {
      ...row(3, "filmgrain_xl"),
      attachments: [{ entity_type: "character", entity_id: 7 }],
    };
    const member = {
      sha256: assigned.sha256,
      slot: "lora",
      label: "filmgrain_xl",
      on_shelf: true,
      id: 3,
      name: "filmgrain_xl",
      filename: "filmgrain_xl",
      kind: "adapter",
      base_model: null,
    };
    const { wrapper, store } = await mountGrid({
      rows: [assigned],
      handMade: [
        {
          id: 10,
          name: null,
          members: [member],
          covers: [],
          picture_count: 0,
          incomplete: true,
        },
      ],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();

    const tile = wrapper.find(`[data-key="m:${assigned.sha256}"]`);
    // On the shelf, so the tile's tip has the row's path as well as the hash.
    expect(tile.find(".mss__tilename [data-tip]").attributes("data-tip")).toBe(
      `/m/filmgrain_xl\nSHA-256: ${assigned.sha256}`,
    );
    expect(
      tile
        .findAll("img")
        .some((img) =>
          img.attributes("src")?.includes("/characters/7/thumbnail"),
        ),
    ).toBe(true);
  });
});

describe("hand-made sets (#1520)", () => {
  /** A hand-made set as `GET /models/workflow-sets` serves it. */
  function handSet(id, members, extra = {}) {
    return {
      id,
      name: null,
      created_at: "2026-09-26T08:00:00Z",
      updated_at: "2026-09-26T08:00:00Z",
      incomplete: !members.some((m) => m.slot === "checkpoint"),
      checkpoint_ids: members
        .filter((m) => m.slot === "checkpoint" && m.on_shelf)
        .map((m) => m.id),
      picture_count: 0,
      recipes: 0,
      covers: [],
      members,
      ...extra,
    };
  }

  function slotMember(id, name, slot, extra = {}) {
    return {
      sha256: String(id).repeat(64).slice(0, 64),
      slot,
      label: name,
      on_shelf: true,
      id,
      name,
      filename: name,
      kind: slot === "lora" ? "adapter" : slot,
      base_model: "SDXL",
      file_size: 1000,
      ...extra,
    };
  }

  const SET_CKPT = slotMember(1, "realvisXL_v5", "checkpoint");
  const SET_VAE = slotMember(2, "sdxl_vae", "vae");

  it("badges every set Yours or nothing, and says when it has no checkpoint", async () => {
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), row(3, "filmgrain_xl")],
      handMade: [
        handSet(10, [SET_CKPT]),
        handSet(11, [slotMember(3, "filmgrain_xl", "lora")]),
      ],
    });

    const cards = wrapper.findAll('[data-testid="model-set-card"]');
    expect(cards).toHaveLength(2);
    // Neither served as automatic: one has no pictures, one no checkpoint.
    for (const card of cards) {
      expect(card.attributes("aria-label")).toContain("custom");
      expect(card.text()).toContain("Yours");
    }
    // Named after its checkpoint, in the shelf's own name for it, and
    // "Untitled set" without one.
    expect(cards[0].attributes("aria-label")).toMatch(/^realvisXL v5/);
    expect(cards[1].attributes("aria-label")).toMatch(/^Untitled set/);
    expect(cards[0].text()).not.toContain("Incomplete");
    expect(cards[1].text()).toContain("Incomplete: no checkpoint");
    // The name says what is drawn: the warning, not the facts it replaces.
    expect(cards[1].attributes("aria-label")).not.toContain("no pictures yet");
    expect(cards[0].attributes("aria-label")).toContain("no pictures yet");
    // No recipe on its checkpoint, so no recipe count.
    expect(cards[0].text()).not.toMatch(/recipe/);
  });

  it("announces a hidden hand-made card as hidden", async () => {
    // The visible "Hidden" tag is aria-hidden, so the name is the only place a
    // screen reader hears it - the derived branch says it, and so must this.
    hiddenOnServer.add("hand:10");
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [SET_CKPT])],
    });
    store.showHiddenSets = true;
    await wrapper.vm.$nextTick();
    const card = wrapper.find('[data-testid="model-set-card"]');
    expect(card.attributes("aria-label")).toContain("hidden");
  });

  it("hides a hand-made card from its own eye, the set and not a file", async () => {
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [SET_CKPT])],
    });
    await wrapper.find('[data-testid="set-hide"]').trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(setWorkflowSetsHidden).toHaveBeenCalledWith(["hand:10"], true);
    expect(wrapper.findAll('[data-testid="model-set-card"]')).toHaveLength(0);

    await wrapper.find('[data-testid="toggle-hidden-sets"]').trigger("click");
    const card = wrapper.find('[data-testid="model-set-card"]');
    expect(card.classes()).toContain("msc--hidden");
    await card.find('[data-testid="set-hide"]').trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(setWorkflowSetsHidden).toHaveBeenLastCalledWith(["hand:10"], false);
    wrapper.unmount();
  });

  it("offers no fill from a set hidden in this library, until Show them draws it", async () => {
    hiddenOnServer.add("hand:12");
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), row(3, "filmgrain_xl")],
      handMade: [
        handSet(11, [SET_CKPT]),
        handSet(12, [
          slotMember(4, "juggernautXL_v9", "checkpoint"),
          slotMember(3, "filmgrain_xl", "lora"),
        ]),
      ],
    });
    store.toggleSet("hand:11");
    await wrapper.vm.$nextTick();
    const fill = () =>
      wrapper.findAll("button").find((b) => b.text().includes("Fill from a set"));
    expect(fill().attributes("disabled")).toBeDefined();

    store.showHiddenSets = true;
    await wrapper.vm.$nextTick();
    expect(fill().attributes("disabled")).toBeUndefined();
    wrapper.unmount();
  });

  it("offers no fill of a model hidden in this library, until Show asks for those", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [
        row(1, "realvisXL_v5", "checkpoint"),
        { ...row(3, "filmgrain_xl"), hidden: true },
      ],
      handMade: [
        handSet(11, [SET_CKPT]),
        handSet(12, [
          slotMember(4, "juggernautXL_v9", "checkpoint"),
          slotMember(3, "filmgrain_xl", "lora"),
        ]),
      ],
    });
    store.toggleSet("hand:11");
    await wrapper.vm.$nextTick();
    const fill = () =>
      wrapper.findAll("button").find((b) => b.text().includes("Fill from a set"));
    // The other set's one LoRA is hidden here, so there is nothing to fill.
    expect(fill().attributes("disabled")).toBeDefined();

    store.setFilters({ showHidden: true });
    await wrapper.vm.$nextTick();
    expect(fill().attributes("disabled")).toBeUndefined();
    wrapper.unmount();
  });

  it("draws a combination the set covers on the set and not again as evidence", async () => {
    const { wrapper } = await mountGrid({
      rows: [
        row(1, "realvisXL_v5", "checkpoint"),
        row(4, "juggernautXL_v9", "checkpoint"),
      ],
      support: [row(2, "sdxl_vae", "vae")],
      combinations: [
        { ...combination("1,2", [CKPT, VAE]), covered_by: [10] },
        combination("4", [OTHER_CKPT]),
      ],
      handMade: [handSet(10, [SET_CKPT, SET_VAE], { picture_count: 1 })],
    });

    const names = wrapper
      .findAll('[data-testid="model-set-card"]')
      .map((card) => card.attributes("aria-label").split(",")[0]);
    expect(names).toEqual(["realvisXL v5", "juggernautXL v9"]);
  });

  it("selects the SET on a hand-made card, never a file, and deletes it with Delete and an Undo", async () => {
    deleteWorkflowSet.mockResolvedValue({
      deleted: handSet(10, [SET_CKPT], { name: "Portrait kit" }),
    });
    createWorkflowSet.mockResolvedValue(handSet(12, [SET_CKPT]));
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [SET_CKPT], { name: "Portrait kit" })],
    });
    const operations = useOperationStore();

    const card = wrapper.find(".msg__row");
    await card.trigger("click");
    expect([...store.selectedSetIds]).toEqual([10]);
    expect([...store.selectedIds]).toEqual([]);
    expect(card.attributes("aria-selected")).toBe("true");

    await wrapper
      .find('[role="treegrid"]')
      .trigger("keydown", { key: "Delete" });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(deleteWorkflowSet).toHaveBeenCalledWith(10);

    expect(operations.receipt.summary).toContain(
      'Deleted the set "Portrait kit"',
    );
    await operations.takeLocalReceiptAction();
    // The undo recreates the set from the snapshot, by hash and slot.
    expect(createWorkflowSet).toHaveBeenCalledWith({
      name: "Portrait kit",
      members: [
        { sha256: SET_CKPT.sha256, slot: "checkpoint", label: SET_CKPT.label },
      ],
    });
    // Redo deletes the set the undo made, which has a new id.
    expect(operations.receipt.mode).toBe("undone");
    await operations.takeLocalReceiptAction();
    expect(deleteWorkflowSet).toHaveBeenLastCalledWith(12);
  });

  it("duplicates a set with every member by hash and slot, and opens the copy", async () => {
    const gone = slotMember(5, "old_lora", "lora", {
      on_shelf: false,
      id: null,
    });
    const original = handSet(10, [SET_CKPT, SET_VAE, gone], {
      name: "Portrait kit",
    });
    createWorkflowSet.mockResolvedValue(
      handSet(12, [SET_CKPT, SET_VAE, gone], { name: "Portrait kit copy" }),
    );
    deleteWorkflowSet.mockResolvedValue({ deleted: handSet(12, []) });
    const { store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      support: [row(2, "sdxl_vae", "vae")],
      handMade: [original],
    });
    const operations = useOperationStore();

    await store.duplicateHandMadeSet(original);

    expect(createWorkflowSet).toHaveBeenCalledWith({
      name: "Portrait kit copy",
      members: [SET_CKPT, SET_VAE, gone].map((m) => ({
        sha256: m.sha256,
        slot: m.slot,
        label: m.label,
      })),
    });
    expect(store.openSetKey).toBe("hand:12");
    // Undo takes the copy away, never the original.
    await operations.takeLocalReceiptAction();
    expect(deleteWorkflowSet).toHaveBeenCalledWith(12);
    expect(deleteWorkflowSet).not.toHaveBeenCalledWith(10);
  });

  it("opens the template picker with N and from the tile, and Make your own makes the empty set", async () => {
    createWorkflowSet.mockResolvedValue(handSet(12, []));
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      combinations: [combination("1", [CKPT])],
    });

    await wrapper.find('[role="treegrid"]').trigger("keydown", { key: "n" });
    await new Promise((resolve) => setTimeout(resolve, 0));
    // N opens the picker; nothing is made until a press inside it.
    expect(fetchWorkflowTemplates).toHaveBeenCalledTimes(1);
    expect(createWorkflowSet).not.toHaveBeenCalled();

    await wrapper.find('[data-testid="make-your-own"]').trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(createWorkflowSet).toHaveBeenCalledWith({});
    expect(store.openSetKey).toBe("hand:12");

    await wrapper.find('[data-testid="new-workflow-set"]').trigger("click");
    expect(fetchWorkflowTemplates).toHaveBeenCalledTimes(2);
    expect(createWorkflowSet).toHaveBeenCalledTimes(1);
  });

  it("walks the tray's slots and removes a member with Backspace, keeping the file", async () => {
    removeWorkflowSetMembers.mockResolvedValue({
      set: handSet(10, [SET_CKPT]),
      removed: [{ sha256: SET_VAE.sha256, slot: "vae", label: "sdxl_vae" }],
    });
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      support: [row(2, "sdxl_vae", "vae")],
      handMade: [handSet(10, [SET_CKPT, SET_VAE])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();

    const panel = wrapper.find('[data-testid="model-set-slots-panel"]');
    // Every slot keeps its ＋, Checkpoint's too: a two-model workflow's set
    // holds two (#1690).
    const addTiles = panel.findAll(".mss__tile--add");
    expect(addTiles.map((tile) => tile.attributes("aria-label"))).toEqual([
      "Add checkpoint…",
      "Add text encoder…",
      "Add VAE…",
      "Add LoRA…",
      "Add…",
    ]);

    const grid = wrapper.find('[role="treegrid"]');
    // Card → checkpoint tile → the text-encoder slot → the VAE slot's member.
    await grid.trigger("keydown", { key: "ArrowDown" });
    await grid.trigger("keydown", { key: "ArrowDown" });
    await grid.trigger("keydown", { key: "ArrowDown" });
    await grid.trigger("keydown", { key: "Backspace" });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(removeWorkflowSetMembers).toHaveBeenCalledWith(10, [SET_VAE.sha256]);
  });

  it("enters the tray at its first tile on Down from the header bar", async () => {
    removeWorkflowSetMembers.mockResolvedValue({
      set: handSet(10, [SET_VAE]),
      removed: [{ sha256: SET_CKPT.sha256, slot: "checkpoint" }],
    });
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      support: [row(2, "sdxl_vae", "vae")],
      handMade: [handSet(10, [SET_CKPT, SET_VAE])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();

    // The header's rows are slot tiles, not members; Down must still land.
    await wrapper.find(".msp__header button:not([disabled])").trigger("keydown", {
      key: "ArrowDown",
    });
    await wrapper.find('[role="treegrid"]').trigger("keydown", {
      key: "Backspace",
    });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(removeWorkflowSetMembers).toHaveBeenCalledWith(10, [
      SET_CKPT.sha256,
    ]);
  });

  it("undoes a create through the delete verb, so that undo has an Undo too", async () => {
    createWorkflowSet.mockResolvedValue(handSet(12, []));
    deleteWorkflowSet.mockResolvedValue({
      deleted: handSet(12, [SET_CKPT]),
    });
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
    });
    const operations = useOperationStore();

    await makeYourOwn(wrapper);
    await operations.takeLocalReceiptAction();

    expect(deleteWorkflowSet).toHaveBeenCalledWith(12);
    // The delete's pill stays up, not the create's flipped to Redo.
    expect(operations.receipt.summary).toContain("Deleted the set");
    expect(operations.receipt.mode).toBe("did");
  });

  it("never says a create was undone when its delete failed", async () => {
    createWorkflowSet.mockResolvedValue(handSet(12, []));
    deleteWorkflowSet.mockRejectedValue(new Error("gone"));
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
    });
    const operations = useOperationStore();

    await makeYourOwn(wrapper);
    await operations.takeLocalReceiptAction();

    // No "Undone" and so no Redo, which would make the set a second time.
    expect(operations.receipt).toBe(null);
    expect(createWorkflowSet).toHaveBeenCalledTimes(1);
  });

  it("keeps the receipt and Undo for the sets it did delete when one fails", async () => {
    deleteWorkflowSet.mockImplementation((id) =>
      id === 11
        ? Promise.reject(new Error("gone"))
        : Promise.resolve({ deleted: handSet(id, [SET_CKPT]) }),
    );
    const { store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [SET_CKPT]), handSet(11, [SET_CKPT])],
    });
    await store.deleteHandMadeSets(store.handMadeSets);

    const receipt = useOperationStore().receipt;
    expect(receipt.summary).toContain("1 could not be deleted");
    expect(receipt.local.undo).toBeTypeOf("function");
  });

  it("does not make a set per repeat of a held N", async () => {
    createWorkflowSet.mockResolvedValue(handSet(12, []));
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      combinations: [combination("1", [CKPT])],
    });
    const grid = wrapper.find('[role="treegrid"]');
    await grid.trigger("keydown", { key: "n", repeat: true });
    expect(fetchWorkflowTemplates).not.toHaveBeenCalled();
    expect(createWorkflowSet).not.toHaveBeenCalled();
  });

  it("clears a mixed model and set selection on a click on the background", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [SET_CKPT])],
    });
    store.selectFromClick(1, { ctrl: false, shift: false }, [1]);
    store.selectSet(10, { ctrl: true });
    expect([...store.selectedSetIds]).toEqual([10]);
    expect([...store.selectedIds]).toEqual([1]);

    await wrapper.find(".msg__scroll").trigger("pointerdown");
    await mouseClick(wrapper.find(".msg__scroll"));
    expect([...store.selectedSetIds]).toEqual([]);
    expect([...store.selectedIds]).toEqual([]);
  });

  it("drops the set selection when the cursor enters a tray, and when the grid is left", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [SET_CKPT])],
    });
    await wrapper.find(".msg__row").trigger("click");
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    expect([...store.selectedSetIds]).toEqual([10]);

    await wrapper
      .find('[role="treegrid"]')
      .trigger("keydown", { key: "ArrowDown" });
    expect([...store.selectedSetIds]).toEqual([]);

    await wrapper.find(".msg__row").trigger("click");
    expect([...store.selectedSetIds]).toEqual([10]);
    store.setView({ groupBy: "none" });
    await wrapper.vm.$nextTick();
    expect([...store.selectedSetIds]).toEqual([]);
  });

  it("adds a model to a slot with ONE click, and stays open for the next", async () => {
    // `added` is the digests the server stored, as the route returns it, and
    // the set comes back holding them, as it does from the real route.
    const shaOf = (modelId) => String(modelId).repeat(64).slice(0, 64);
    const names = { 3: "filmgrain_xl", 5: "Soft_Light" };
    let held = [SET_CKPT];
    addWorkflowSetMembers.mockReset().mockImplementation((id, members) => {
      held = [
        ...held,
        ...members.map((m) => slotMember(m.model_id, names[m.model_id], "lora")),
      ];
      // The store re-reads the sets after a write: serve the grown one.
      fetchWorkflowSets.mockResolvedValue({
        combinations: [],
        no_set: [],
        hand_made: [handSet(10, held)],
      });
      return Promise.resolve({
        set: handSet(10, held),
        added: members.map((m) => shaOf(m.model_id)),
      });
    });
    const { wrapper, store } = await mountGrid({
      rows: [
        row(1, "realvisXL_v5", "checkpoint"),
        row(3, "filmgrain_xl"),
        row(5, "Soft_Light"),
      ],
      handMade: [handSet(10, [SET_CKPT])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();

    const loraTile = wrapper
      .findAll(".mss__tile--add")
      .find((tile) => tile.attributes("aria-label") === "Add LoRA…");
    await loraTile.trigger("click");
    const chooser = wrapper.find('[data-testid="workflow-set-chooser"]');
    // No tick box and no Add button: the click on the row IS the add.
    expect(chooser.text()).not.toMatch(/\bAdd \d/);
    expect(chooser.findAll("button").map((b) => b.text())).not.toContain("Add");

    const option = (name) =>
      chooser.findAll('[role="option"]').find((o) => o.text().includes(name));
    const order = () =>
      chooser.findAll('[role="option"]').map((o) => o.find(".wsc__fn").text());
    const before = order();
    expect(chooser.findAll(".wsc__tick")).toHaveLength(before.length);
    expect(chooser.findAll(".wsc__tick > *")).toHaveLength(0);
    await option("filmgrain_xl").trigger("click");
    await option("Soft_Light").trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));

    // Added rows stay where they were, ticked, rather than leaving the list
    // and moving the rest under the pointer; no visible "Added" line.
    expect(order()).toEqual(before);
    expect(option("filmgrain_xl").attributes("aria-selected")).toBe("true");
    expect(option("Soft_Light").attributes("aria-selected")).toBe("true");
    expect(chooser.findAll(".wsc__tick > *")).toHaveLength(2);
    expect(chooser.find(".tbm-footer").exists()).toBe(false);
    // Still open for the next pick.
    expect(wrapper.findComponent(WorkflowSetChooser).props("open")).toBe(true);
    expect(addWorkflowSetMembers.mock.calls).toEqual([
      [10, [{ model_id: 3, slot: "lora" }]],
      [10, [{ model_id: 5, slot: "lora" }]],
    ]);
    // Narrated to a screen reader in the shelf's names, and no pill yet: the
    // adds are one receipt once the popup closes (#1574).
    const operations = useOperationStore();
    expect(operations.receipt).toBe(null);
    expect(chooser.find('[role="status"]').text()).toContain(
      "Added filmgrain xl and Soft Light",
    );
    await chooser.trigger("keydown", { key: "Escape" });
    expect(operations.receipt.summary).toBe('Added 2 LoRAs to "realvisXL v5"');

    removeWorkflowSetMembers.mockReset().mockResolvedValue({ removed: [] });
    await operations.takeLocalReceiptAction();
    expect(removeWorkflowSetMembers).toHaveBeenCalledWith(10, [
      shaOf(3),
      shaOf(5),
    ]);
  });

  it("offers a LoRA still downloading, adds it by digest, and marks its tile", async () => {
    // A download carrying no digest has nothing to hold it by; one for
    // another slot is not this slot's. Mixed case: the file keeps its own.
    const coming = "d".repeat(64);
    useTasksStore().modelDownloads = [
      { id: "a", file: "Coming_LoRA.safetensors", status: "running", sha256: coming, slot: "lora", percent: 40 },
      { id: "b", file: "gated.safetensors", status: "running", sha256: null, slot: "lora" },
      { id: "c", file: "other_vae.safetensors", status: "running", sha256: "e".repeat(64), slot: "vae" },
    ];
    let held = [SET_CKPT];
    addWorkflowSetMembers.mockReset().mockImplementation((id, members) => {
      held = [
        ...held,
        ...members.map((m) => ({
          sha256: m.sha256,
          slot: m.slot,
          label: m.label,
          on_shelf: false,
          id: null,
          name: m.label,
        })),
      ];
      fetchWorkflowSets.mockResolvedValue({
        combinations: [],
        no_set: [],
        hand_made: [handSet(10, held)],
      });
      return Promise.resolve({
        set: handSet(10, held),
        added: members.map((m) => m.sha256),
      });
    });
    removeWorkflowSetMembers.mockReset().mockResolvedValue({ removed: [coming] });
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), row(3, "filmgrain_xl")],
      handMade: [handSet(10, [SET_CKPT])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    await wrapper
      .findAll(".mss__tile--add")
      .find((tile) => tile.attributes("aria-label") === "Add LoRA…")
      .trigger("click");
    const chooser = wrapper.find('[data-testid="workflow-set-chooser"]');
    expect(chooser.text()).toContain("Downloading");
    const options = chooser.findAll('[role="option"]');
    const option = options.find((o) => o.text().includes("Coming_LoRA"));
    expect(option.find('[data-testid="downloading-mark"]').exists()).toBe(true);
    expect(options.some((o) => o.text().includes("gated"))).toBe(false);
    expect(options.some((o) => o.text().includes("other_vae"))).toBe(false);

    await option.trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(addWorkflowSetMembers.mock.calls).toEqual([
      [10, [{ sha256: coming, slot: "lora", label: "Coming_LoRA.safetensors" }]],
    ]);
    expect(option.attributes("aria-selected")).toBe("true");
    await chooser.trigger("keydown", { key: "Escape" });
    await new Promise((resolve) => setTimeout(resolve, 0));

    // Held by its digest, the tile says it is on its way, not gone.
    const tile = wrapper
      .findAll(".mss__tile")
      .find((t) => t.text().includes("Coming LoRA"));
    expect(tile.find('[data-testid="downloading-mark"]').exists()).toBe(true);
    expect(tile.text()).not.toContain("Not on shelf");
    expect(tile.classes()).not.toContain("mss__tile--gone");

    // Undo takes it out by digest; Redo puts it back the same way, since it
    // has no shelf id to go back in by.
    const operations = useOperationStore();
    await operations.takeLocalReceiptAction();
    expect(removeWorkflowSetMembers).toHaveBeenCalledWith(10, [coming]);
    await operations.takeLocalReceiptAction();
    expect(addWorkflowSetMembers).toHaveBeenLastCalledWith(10, [
      { sha256: coming, slot: "lora", label: "Coming_LoRA.safetensors" },
    ]);
  });

  it("still adds a download by its digest when it lands between draw and click", async () => {
    const late = "9".repeat(64);
    const tasks = useTasksStore();
    tasks.modelDownloads = [
      { id: "z", file: "late.safetensors", status: "running", sha256: late, slot: "lora" },
    ];
    addWorkflowSetMembers.mockReset().mockResolvedValue({
      set: handSet(10, [SET_CKPT]),
      added: [late],
    });
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [SET_CKPT])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    await wrapper
      .findAll(".mss__tile--add")
      .find((tile) => tile.attributes("aria-label") === "Add LoRA…")
      .trigger("click");
    const option = wrapper
      .find('[data-testid="workflow-set-chooser"]')
      .findAll('[role="option"]')
      .find((o) => o.text().includes("late"));
    // The poll says it finished; the row on screen is the one clicked.
    tasks.modelDownloads = [{ ...tasks.modelDownloads[0], status: "done" }];
    await option.trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(addWorkflowSetMembers.mock.calls).toEqual([
      [10, [{ sha256: late, slot: "lora", label: "late.safetensors" }]],
    ]);
  });

  it("takes a pick back out with a click on its ticked row, without a pill", async () => {
    const shaOf = (modelId) => String(modelId).repeat(64).slice(0, 64);
    addWorkflowSetMembers.mockReset().mockImplementation((id, members) =>
      Promise.resolve({
        set: handSet(10, [SET_CKPT]),
        added: members.map((m) => shaOf(m.model_id)),
      }),
    );
    removeWorkflowSetMembers
      .mockReset()
      .mockImplementation((id, shas) => Promise.resolve({ removed: shas }));
    const { wrapper, store } = await mountGrid({
      rows: [
        row(1, "realvisXL_v5", "checkpoint"),
        row(3, "filmgrain_xl"),
        row(5, "Soft_Light"),
      ],
      handMade: [handSet(10, [SET_CKPT])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    await wrapper
      .findAll(".mss__tile--add")
      .find((tile) => tile.attributes("aria-label") === "Add LoRA…")
      .trigger("click");
    const chooser = wrapper.find('[data-testid="workflow-set-chooser"]');
    const field = chooser.find("input");
    // Enter with no arrow used adds the top match.
    await field.trigger("keydown", { key: "Enter" });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(addWorkflowSetMembers).toHaveBeenCalledWith(10, [
      { model_id: 3, slot: "lora" },
    ]);

    const filmgrain = () =>
      chooser
        .findAll('[role="option"]')
        .find((o) => o.text().includes("filmgrain_xl"));
    expect(filmgrain().attributes("aria-selected")).toBe("true");
    // The next Enter adds the next row down, not the ticked one back out.
    await field.trigger("keydown", { key: "Enter" });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(addWorkflowSetMembers).toHaveBeenLastCalledWith(10, [
      { model_id: 5, slot: "lora" },
    ]);
    expect(removeWorkflowSetMembers).not.toHaveBeenCalled();

    await filmgrain().trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(removeWorkflowSetMembers).toHaveBeenCalledWith(10, [shaOf(3)]);
    expect(filmgrain().attributes("aria-selected")).toBe("false");
    expect(chooser.find('[role="status"]').text()).toContain(
      "Took out filmgrain xl",
    );
    // Only what this popup added can be taken back: unticked, it adds again.
    await filmgrain().trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(addWorkflowSetMembers).toHaveBeenLastCalledWith(10, [
      { model_id: 3, slot: "lora" },
    ]);
    await filmgrain().trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    // One left to announce once it closes: Soft_Light.
    await chooser.trigger("keydown", { key: "Escape" });
    expect(useOperationStore().receipt.summary).toBe(
      'Added "Soft Light" to "realvisXL v5"',
    );
  });

  it("moves the arrow cursor on after an Enter add, so the next Enter adds the next row", async () => {
    const shaOf = (modelId) => String(modelId).repeat(64).slice(0, 64);
    addWorkflowSetMembers.mockReset().mockImplementation((id, members) =>
      Promise.resolve({
        set: handSet(10, [SET_CKPT]),
        added: members.map((m) => shaOf(m.model_id)),
      }),
    );
    removeWorkflowSetMembers.mockReset();
    const { wrapper, store } = await mountGrid({
      rows: [
        row(1, "realvisXL_v5", "checkpoint"),
        row(3, "filmgrain_xl"),
        row(5, "Soft_Light"),
      ],
      handMade: [handSet(10, [SET_CKPT])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    await wrapper
      .findAll(".mss__tile--add")
      .find((tile) => tile.attributes("aria-label") === "Add LoRA…")
      .trigger("click");
    const chooser = wrapper.find('[data-testid="workflow-set-chooser"]');
    const field = chooser.find("input");
    await field.trigger("keydown", { key: "ArrowDown" });
    expect(chooser.find(".wsc__r--cur").text()).toContain("filmgrain_xl");
    await field.trigger("keydown", { key: "Enter" });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(chooser.find(".wsc__r--cur").text()).toContain("Soft_Light");
    await field.trigger("keydown", { key: "Enter" });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(addWorkflowSetMembers.mock.calls).toEqual([
      [10, [{ model_id: 3, slot: "lora" }]],
      [10, [{ model_id: 5, slot: "lora" }]],
    ]);
    expect(removeWorkflowSetMembers).not.toHaveBeenCalled();
  });

  it("closes the Checkpoint popup on its one pick and lands on the new checkpoint", async () => {
    const CKPT_ROW = row(1, "realvisXL_v5", "checkpoint");
    addWorkflowSetMembers.mockReset().mockResolvedValue({
      set: handSet(10, [SET_CKPT]),
      added: [SET_CKPT.sha256],
    });
    const { wrapper, store } = await mountGrid({
      rows: [CKPT_ROW],
      handMade: [handSet(10, [])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    // The refetch after the add serves the set with its checkpoint.
    fetchWorkflowSets.mockResolvedValue({
      combinations: [],
      no_set: [],
      hand_made: [handSet(10, [SET_CKPT])],
    });

    await wrapper.find(".mss__tile--add").trigger("click");
    await wrapper
      .find('[data-testid="workflow-set-chooser"] [role="option"]')
      .trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    await wrapper.vm.$nextTick();

    expect(addWorkflowSetMembers).toHaveBeenCalledWith(10, [
      { model_id: 1, slot: "checkpoint" },
    ]);
    expect(wrapper.findComponent(WorkflowSetChooser).props("open")).toBe(false);
    const tile = wrapper.find(`[data-key="m:${SET_CKPT.sha256}"]`);
    expect(tile.attributes("tabindex")).toBe("0");
  });

  it("offers once to set the base model of a checkpoint that has none", async () => {
    const bare = slotMember(1, "realvisXL_v5", "checkpoint", {
      base_model: null,
    });
    const ckptRow = {
      ...row(1, "realvisXL_v5", "checkpoint"),
      base_model_canonical: "SDXL",
      base_model_source: "filename_fuzzy",
    };
    addWorkflowSetMembers.mockReset().mockResolvedValue({
      set: handSet(10, [bare]),
      added: [bare.sha256],
    });
    const { wrapper, store } = await mountGrid({
      rows: [ckptRow],
      handMade: [handSet(10, [])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    fetchWorkflowSets.mockResolvedValue({
      combinations: [],
      no_set: [],
      hand_made: [handSet(10, [bare])],
    });
    const { editModels } = await import("../../api/modelShelf");
    editModels.mockResolvedValue({ updated: [1] });

    await wrapper.find(".mss__tile--add").trigger("click");
    await wrapper
      .find('[data-testid="workflow-set-chooser"] [role="option"]')
      .trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    await wrapper.vm.$nextTick();

    const offer = wrapper.find('[data-testid="base-model-offer"]');
    expect(offer.text()).toContain("realvisXL v5 has no base model");
    // A stray `>` once rendered as text after the hint's closing tag.
    expect(offer.text()).not.toContain(">");
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    // Pre-filled from the shelf's own guess, never stored until confirmed.
    const set = offer.findAll("button").find((b) => b.text() === "Set SDXL");
    expect(set).toBeTruthy();
    await set.trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(editModels).toHaveBeenCalledWith([1], { base_model: "SDXL" });
    // `set-base` is a declared event, not one Vue warns about on every press.
    expect(
      warn.mock.calls.some((call) => String(call[0]).includes("set-base")),
    ).toBe(false);
    warn.mockRestore();
    expect(wrapper.find('[data-testid="base-model-offer"]').exists()).toBe(
      false,
    );
  });

  it("does not offer again once dismissed, nor for a checkpoint that has a base model", async () => {
    const bare = slotMember(1, "realvisXL_v5", "checkpoint", {
      base_model: null,
    });
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [bare])],
    });
    store.toggleSet("hand:10");
    store.checkpointAdded = { setId: 10, modelId: 1 };
    await wrapper.vm.$nextTick();
    const offer = wrapper.find('[data-testid="base-model-offer"]');
    await offer
      .findAll("button")
      .find((b) => b.text() === "Not now")
      .trigger("click");
    expect(wrapper.find('[data-testid="base-model-offer"]').exists()).toBe(
      false,
    );

    // An offer left unanswered goes when its tray closes, and does not come
    // back when the tray reopens.
    store.checkpointAdded = { setId: 10, modelId: 1 };
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-testid="base-model-offer"]').exists()).toBe(
      true,
    );
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-testid="base-model-offer"]').exists()).toBe(
      false,
    );

    // With no guess the field is empty and Set still answers: it says what
    // is missing instead of greying out with no reason.
    store.checkpointAdded = { setId: 10, modelId: 1 };
    await wrapper.vm.$nextTick();
    const empty = wrapper.find('[data-testid="base-model-offer"]');
    expect(empty.find("input").attributes("placeholder")).toContain(
      "Type a base model",
    );
    const setButton = empty.findAll("button").find((b) => b.text() === "Set");
    expect(setButton.attributes("disabled")).toBeUndefined();
    await setButton.trigger("click");
    expect(empty.text()).toContain("Type or pick a base model first.");
    const { editModels } = await import("../../api/modelShelf");
    expect(editModels).not.toHaveBeenCalled();
    store.checkpointAdded = null;
    await wrapper.vm.$nextTick();

    // A checkpoint that already has one is never asked about.
    store.checkpointAdded = { setId: 10, modelId: 1 };
    store.workflowSets = {
      ...store.workflowSets,
      handMade: [handSet(10, [SET_CKPT])],
    };
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-testid="base-model-offer"]').exists()).toBe(
      false,
    );
  });

  it("keeps the base-model offer up when the write fails, to try again", async () => {
    const bare = slotMember(1, "realvisXL_v5", "checkpoint", {
      base_model: null,
    });
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [bare])],
    });
    store.toggleSet("hand:10");
    store.checkpointAdded = { setId: 10, modelId: 1 };
    await wrapper.vm.$nextTick();
    const offer = () => wrapper.find('[data-testid="base-model-offer"]');
    const press = () =>
      offer()
        .findAll("button")
        .find((b) => b.text().startsWith("Set"))
        .trigger("click");
    const settle = async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
      await wrapper.vm.$nextTick();
    };
    await offer().find("input").setValue("Pony");
    const { editModels } = await import("../../api/modelShelf");
    editModels.mockReset().mockRejectedValueOnce(new Error("hub busy"));

    await press();
    await settle();
    expect(editModels).toHaveBeenCalledWith([1], { base_model: "Pony" });
    expect(offer().exists()).toBe(true);
    // The typed value survives the failure, ready to press again.
    expect(offer().find("input").element.value).toBe("Pony");

    // A second press while the retry is in flight sends nothing more.
    let finish;
    editModels.mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    await press();
    await press();
    expect(editModels).toHaveBeenCalledTimes(2);
    // An offer that replaced this one during the write is left alone.
    const next = { setId: 10, modelId: 2 };
    store.checkpointAdded = next;
    finish({ updated: [1] });
    await settle();
    expect(store.checkpointAdded).toEqual(next);

    // A stored answer ends its own offer.
    store.checkpointAdded = { setId: 10, modelId: 1 };
    await wrapper.vm.$nextTick();
    await offer().find("input").setValue("Pony");
    editModels.mockResolvedValueOnce({ updated: [1] });
    await press();
    await settle();
    expect(editModels).toHaveBeenCalledTimes(3);
    expect(store.checkpointAdded).toBe(null);
  });

  it("calls a set and its members what the shelf calls them, never the filename", async () => {
    const onShelf = slotMember(1, "RealVisXL_V5.safetensors", "checkpoint", {
      filename: "RealVisXL_V5.safetensors",
    });
    const gone = slotMember(5, "Film_Grain_XL.safetensors", "lora", {
      on_shelf: false,
      id: null,
    });
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "RealVisXL_V5.safetensors", "checkpoint")],
      handMade: [handSet(10, [onShelf, gone])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();

    const card = wrapper.find('[data-testid="model-set-card"]');
    expect(card.attributes("aria-label")).toMatch(/^RealVisXL V5,/);
    const panel = wrapper.find('[data-testid="model-set-slots-panel"]');
    expect(panel.text()).not.toContain(".safetensors");
    expect(panel.text()).toContain("Film Grain XL");
  });

  it("says a checkpoint left the shelf, not that there is none", async () => {
    const gone = slotMember(1, "realvisXL_v5", "checkpoint", {
      on_shelf: false,
      id: null,
    });
    const { wrapper, store } = await mountGrid({
      rows: [],
      handMade: [handSet(10, [gone], { incomplete: false })],
    });
    const card = wrapper.find('[data-testid="model-set-card"]');
    expect(card.text()).toContain("Checkpoint not on shelf");
    expect(card.text()).not.toContain("No checkpoint yet");
    expect(card.attributes("aria-label")).toContain("checkpoint not on shelf");

    // Not stuck: the off-shelf checkpoint's tile keeps its Remove, which is
    // what frees the slot for a replacement.
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    const tile = wrapper.find(`[data-key="m:${gone.sha256}"]`);
    expect(tile.find(".mss__remove").exists()).toBe(true);
  });

  it("hands the shelf the card to refocus when the set menu closes", async () => {
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [SET_CKPT])],
    });
    const card = wrapper.find(".msg__row");
    await card.trigger("contextmenu", { clientX: 5, clientY: 6 });
    const [{ x, y, el }] = wrapper.emitted("set-menu")[0];
    expect([x, y]).toEqual([5, 6]);
    expect(el).toBe(card.element);
  });

  it("counts a whole section in its heading, not the rows drawn before Show all", async () => {
    const loras = Array.from({ length: 10 }, (_, i) =>
      row(20 + i, `lora_${i}`),
    );
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), ...loras],
      handMade: [handSet(10, [SET_CKPT])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    await wrapper
      .findAll(".mss__tile--add")
      .find((tile) => tile.attributes("aria-label") === "Add LoRA…")
      .trigger("click");

    const chooser = wrapper.find('[data-testid="workflow-set-chooser"]');
    // Eight drawn, ten in the section. The one group is the other base
    // models, so it is drawn open rather than folded to a single line.
    expect(chooser.findAll('[role="option"]')).toHaveLength(8);
    const heading = chooser
      .findAll(".wsc__gh")
      .find((h) => h.text().includes("Other base models"));
    expect(heading.find(".fm-n").text()).toBe("10");
  });

  it("credits a pick only with what the server stored", async () => {
    // The set already held it: the route stores nothing and says so.
    addWorkflowSetMembers
      .mockReset()
      .mockResolvedValue({ set: handSet(10, [SET_CKPT]), added: [] });
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), row(3, "filmgrain_xl")],
      handMade: [handSet(10, [SET_CKPT])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    await wrapper
      .findAll(".mss__tile--add")
      .find((tile) => tile.attributes("aria-label") === "Add LoRA…")
      .trigger("click");
    const chooser = wrapper.find('[data-testid="workflow-set-chooser"]');
    await chooser.find('[role="option"]').trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));

    const status = chooser.find('[role="status"]');
    expect(status.text()).toContain("Already in this set, nothing added");
    // Not credited, so not ticked: a click on it would add, not take out.
    expect(
      chooser.find('[role="option"]').attributes("aria-selected"),
    ).toBe("false");
    await chooser.trigger("keydown", { key: "Escape" });
    expect(useOperationStore().receipt).toBe(null);
  });

  it("names a filter's only matches as already in the set, not as nothing", async () => {
    const held = slotMember(3, "filmgrain_xl", "lora");
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), row(3, "filmgrain_xl")],
      handMade: [handSet(10, [SET_CKPT, held])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    await wrapper
      .findAll(".mss__tile--add")
      .find((tile) => tile.attributes("aria-label") === "Add LoRA…")
      .trigger("click");
    const chooser = wrapper.find('[data-testid="workflow-set-chooser"]');
    await chooser.find("input").setValue("grain");

    expect(chooser.find(".wsc__empty").exists()).toBe(false);
    expect(chooser.find(".wsc__gone").text()).toBe(
      "filmgrain xl also matches, and is already in this set.",
    );
  });

  it("adds every ticked row of a checklist, not only the ones drawn before Show more", async () => {
    const loras = Array.from({ length: 10 }, (_, i) =>
      row(20 + i, `lora_${i}`),
    );
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), ...loras],
      handMade: [
        handSet(10, [SET_CKPT]),
        handSet(11, [
          slotMember(9, "juggernaut", "checkpoint"),
          ...loras.map((r) => slotMember(r.id, r.filename, "lora")),
        ]),
      ],
    });
    addWorkflowSetMembers
      .mockReset()
      .mockResolvedValue({ set: handSet(10, [SET_CKPT]), added: [] });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    await wrapper
      .findAll("button")
      .find((b) => b.text().includes("Fill from a set"))
      .trigger("click");
    const chooser = wrapper.find('[data-testid="workflow-set-chooser"]');
    // Eight drawn, ten ticked: Add counts and adds all ten.
    expect(chooser.findAll('[role="option"]')).toHaveLength(8);
    const add = chooser.findAll("button").find((b) => b.text() === "Add 10");
    expect(add).toBeTruthy();
    await add.trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(addWorkflowSetMembers.mock.calls[0][1]).toHaveLength(10);
  });

  it("adds nothing from a checklist on Enter once every row is unticked", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), row(3, "filmgrain_xl")],
      handMade: [
        handSet(10, [SET_CKPT]),
        handSet(11, [
          slotMember(9, "juggernaut", "checkpoint"),
          slotMember(3, "filmgrain_xl", "lora"),
        ]),
      ],
    });
    addWorkflowSetMembers.mockReset();
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    await wrapper
      .findAll("button")
      .find((b) => b.text().includes("Fill from a set"))
      .trigger("click");
    const chooser = wrapper.find('[data-testid="workflow-set-chooser"]');
    // A checklist marks no row with ⏎: Enter adds what is ticked.
    expect(chooser.find(".wsc__enter").exists()).toBe(false);
    const option = chooser.find('[role="option"]');
    expect(option.attributes("aria-selected")).toBe("true");
    await option.trigger("click");
    expect(option.attributes("aria-selected")).toBe("false");

    const field = chooser.find("input");
    await field.trigger("keydown", { key: "Enter" });
    // Nor with the cursor put down on that unticked row.
    await field.trigger("keydown", { key: "ArrowDown" });
    expect(chooser.find(".wsc__r--cur").exists()).toBe(true);
    await field.trigger("keydown", { key: "Enter" });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(addWorkflowSetMembers).not.toHaveBeenCalled();
  });

  it("folds the other base models to one line under the ranked groups", async () => {
    const sdxl = (id, name) => ({ ...row(id, name), base_model: "SDXL" });
    const { wrapper, store } = await mountGrid({
      rows: [
        row(1, "realvisXL_v5", "checkpoint"),
        sdxl(3, "filmgrain_xl"),
        row(20, "flux_lora_a"),
        row(21, "flux_lora_b"),
      ],
      handMade: [handSet(10, [SET_CKPT])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    await wrapper
      .findAll(".mss__tile--add")
      .find((tile) => tile.attributes("aria-label") === "Add LoRA…")
      .trigger("click");
    const chooser = wrapper.find('[data-testid="workflow-set-chooser"]');

    // Empty groups are not drawn at all.
    expect(chooser.findAll(".wsc__gh").map((h) => h.text())).toEqual([
      "SDXL LoRAs1",
    ]);
    expect(chooser.findAll('[role="option"]')).toHaveLength(1);
    const fold = chooser.find(".wsc__more");
    expect(fold.text()).toBe("2 LoRAs for other base models");

    // Down off the last row unfolds it, as the button would.
    const field = chooser.find("input");
    await field.trigger("keydown", { key: "ArrowDown" });
    await field.trigger("keydown", { key: "ArrowDown" });
    expect(chooser.findAll('[role="option"]')).toHaveLength(3);
    expect(chooser.find(".wsc__r--cur").text()).toContain("flux_lora_a");
  });

  it("keeps what is typed in the base-model field across a refetch", async () => {
    const bare = slotMember(1, "realvisXL_v5", "checkpoint", {
      base_model: null,
    });
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [bare])],
    });
    store.toggleSet("hand:10");
    store.checkpointAdded = { setId: 10, modelId: 1 };
    await wrapper.vm.$nextTick();
    const field = wrapper.find('[data-testid="base-model-offer"] input');
    await field.setValue("Pony");

    await store.loadWorkflowSets({ force: true });
    await wrapper.vm.$nextTick();

    expect(
      wrapper.find('[data-testid="base-model-offer"] input').element.value,
    ).toBe("Pony");
  });

  it("gives focus back to the Fill button when its checklist closes", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), row(3, "filmgrain_xl")],
      handMade: [
        handSet(10, [SET_CKPT]),
        handSet(11, [
          slotMember(9, "juggernaut", "checkpoint"),
          slotMember(3, "filmgrain_xl", "lora"),
        ]),
      ],
      attach: true,
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    const fill = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Fill from a set"));
    await fill.trigger("click");
    await wrapper
      .find('[data-testid="workflow-set-chooser"]')
      .trigger("keydown", { key: "Escape" });
    await wrapper.vm.$nextTick();

    expect(document.activeElement).toBe(fill.element);
    wrapper.unmount();
  });

  it("offers the base model for a checkpoint the SERVER stored, whatever the request said", async () => {
    const bare = slotMember(1, "realvisXL_v5", "checkpoint", {
      base_model: null,
    });
    const { store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [])],
    });
    // No slot in the request: the server placed it in Checkpoint.
    addWorkflowSetMembers.mockResolvedValue({
      set: handSet(10, [bare]),
      added: [bare.sha256],
    });
    await store.addToHandMadeSet(store.handMadeSets[0], [{ model_id: 1 }]);
    expect(store.checkpointAdded).toEqual({ setId: 10, modelId: 1 });

    // Asked for, but nothing was stored: no offer.
    store.checkpointAdded = null;
    addWorkflowSetMembers.mockResolvedValue({
      set: handSet(10, []),
      added: [],
    });
    await store.addToHandMadeSet(store.handMadeSets[0], [
      { model_id: 1, slot: "checkpoint" },
    ]);
    expect(store.checkpointAdded).toBe(null);
  });

  it("says nothing matched, not that a group is empty, while filtering", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), row(3, "filmgrain_xl")],
      handMade: [handSet(10, [SET_CKPT])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    await wrapper
      .findAll(".mss__tile--add")
      .find((tile) => tile.attributes("aria-label") === "Add LoRA…")
      .trigger("click");
    const chooser = wrapper.find('[data-testid="workflow-set-chooser"]');
    await chooser.find("input").setValue("zzz-nothing");

    // No stack of empty headings: one line saying the filter found nothing.
    expect(chooser.findAll(".wsc__gh")).toHaveLength(0);
    expect(chooser.find(".wsc__empty").text()).toBe(
      'Nothing matches "zzz-nothing".',
    );
  });

  it("reveals a cut section from the keyboard by arrowing past its last row", async () => {
    const loras = Array.from({ length: 10 }, (_, i) =>
      row(20 + i, `lora_${i}`),
    );
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), ...loras],
      handMade: [handSet(10, [SET_CKPT])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    await wrapper
      .findAll(".mss__tile--add")
      .find((tile) => tile.attributes("aria-label") === "Add LoRA…")
      .trigger("click");
    const chooser = wrapper.find('[data-testid="workflow-set-chooser"]');
    const field = chooser.find("input");
    expect(chooser.findAll('[role="option"]')).toHaveLength(8);

    // No cursor until an arrow puts one down: the first ↓ lands on lora_0.
    expect(chooser.find(".wsc__r--cur").exists()).toBe(false);
    for (let i = 0; i < 9; i += 1) {
      await field.trigger("keydown", { key: "ArrowDown" });
    }

    expect(chooser.findAll('[role="option"]')).toHaveLength(10);
    expect(chooser.find(".wsc__r--cur").text()).toContain("lora_8");
  });

  it("says how many sets an undo of a delete could not bring back", async () => {
    deleteWorkflowSet.mockImplementation((id) =>
      Promise.resolve({ deleted: handSet(id, [SET_CKPT], { name: `S${id}` }) }),
    );
    createWorkflowSet.mockImplementation(({ name }) =>
      name === "S11"
        ? Promise.reject(new Error("refused"))
        : Promise.resolve(handSet(12, [])),
    );
    const { store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [SET_CKPT]), handSet(11, [SET_CKPT])],
    });
    const { useNoticeStore } = await import("../../stores/useNoticeStore");
    const notices = useNoticeStore();

    await store.deleteHandMadeSets(store.handMadeSets);
    const operations = useOperationStore();
    await operations.takeLocalReceiptAction();

    expect(createWorkflowSet).toHaveBeenCalledTimes(2);
    // The one that came back can still be redone away.
    expect(operations.receipt.mode).toBe("undone");
    await operations.takeLocalReceiptAction();
    expect(deleteWorkflowSet).toHaveBeenLastCalledWith(12);
    expect(notices.notices.some((n) => n.text.includes("1 of 2 sets"))).toBe(
      true,
    );
  });

  it("narrates set edits on the grid's receipt pill: one at a time, Undo then Redo", async () => {
    const loras = [3, 4, 5, 6, 7].map((id) =>
      slotMember(id, `lora_${id}`, "lora"),
    );
    const { store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), row(3, "filmgrain_xl")],
      handMade: [handSet(10, [SET_CKPT])],
    });
    const { useNoticeStore } = await import("../../stores/useNoticeStore");
    const notices = useNoticeStore();
    const operations = useOperationStore();

    for (const lora of loras) {
      addWorkflowSetMembers.mockResolvedValueOnce({
        set: handSet(10, [SET_CKPT, lora]),
        added: [lora.sha256],
      });
      await store.addToHandMadeSet(store.handMadeSets[0], [{ model_id: 3 }]);
    }
    expect(notices.notices).toHaveLength(0);
    expect(operations.receipt.summary).toBe('Added 1 model to "realvisXL v5"');
    // Replaced, never counted: on the grid "+N" means Undo takes all N back.
    expect(operations.receipt.mergedCount).toBe(0);
    expect(operations.receipt.durationMs).toBe(5000);

    // A repeat that added nothing has no Undo: a plain notice, pill untouched.
    const pill = operations.receipt;
    addWorkflowSetMembers.mockResolvedValueOnce({
      set: handSet(10, [SET_CKPT]),
      added: [],
    });
    await store.addToHandMadeSet(store.handMadeSets[0], [{ model_id: 3 }]);
    expect(notices.notices).toHaveLength(1);
    expect(operations.receipt).toBe(pill);

    // Undo is the last add's, not the burst's, and flips the pill to Redo.
    removeWorkflowSetMembers.mockResolvedValue({ removed: [] });
    await operations.takeLocalReceiptAction();
    expect(removeWorkflowSetMembers).toHaveBeenCalledTimes(1);
    expect(removeWorkflowSetMembers).toHaveBeenCalledWith(10, [
      loras.at(-1).sha256,
    ]);
    expect(operations.receipt.mode).toBe("undone");
    addWorkflowSetMembers.mockResolvedValueOnce({
      set: handSet(10, [SET_CKPT, loras[0]]),
      added: [loras[0].sha256],
    });
    await operations.takeLocalReceiptAction();
    expect(addWorkflowSetMembers).toHaveBeenLastCalledWith(10, [
      { model_id: 3 },
    ]);
    expect(operations.receipt.mode).toBe("did");

    // A delete replaces the pill, with the longer window and its own Undo.
    deleteWorkflowSet.mockResolvedValue({ deleted: handSet(10, [SET_CKPT]) });
    await store.deleteHandMadeSets(store.handMadeSets);
    expect(operations.receipt.summary).toContain("Deleted the set");
    expect(operations.receipt.durationMs).toBe(8000);
    expect(operations.receipt.mergedCount).toBe(0);
  });

  it("deletes a focused set on Backspace too, the Mac keyboard's Delete key", async () => {
    deleteWorkflowSet.mockResolvedValue({ deleted: handSet(10, [SET_CKPT]) });
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [SET_CKPT])],
    });
    await wrapper.find(".msg__row").trigger("click");
    await wrapper
      .find('[role="treegrid"]')
      .trigger("keydown", { key: "Backspace" });
    await new Promise((resolve) => setTimeout(resolve, 0));
    // Deleting a set touches no file, and its receipt offers Undo.
    expect(deleteWorkflowSet).toHaveBeenCalledWith(10);
  });

  it("does not delete again on a held Delete", async () => {
    deleteWorkflowSet.mockResolvedValue({ deleted: handSet(10, [SET_CKPT]) });
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [SET_CKPT])],
    });
    await wrapper.find(".msg__row").trigger("click");
    await wrapper
      .find('[role="treegrid"]')
      .trigger("keydown", { key: "Delete", repeat: true });
    expect(deleteWorkflowSet).not.toHaveBeenCalled();
  });

  it("returns focus to a Fill button even when the cursor rested on a ＋ tile", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), row(3, "filmgrain_xl")],
      handMade: [
        handSet(10, [SET_CKPT]),
        handSet(11, [
          slotMember(9, "juggernaut", "checkpoint"),
          slotMember(3, "filmgrain_xl", "lora"),
        ]),
      ],
      attach: true,
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    const grid = wrapper.find('[role="treegrid"]');
    // Card, then the checkpoint tile, then the text-encoder slot's ＋ tile.
    await grid.trigger("keydown", { key: "ArrowDown" });
    await grid.trigger("keydown", { key: "ArrowDown" });
    expect(document.activeElement?.dataset.key).toBe("add:text_encoder");

    const fill = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Fill from a set"));
    await fill.trigger("click");
    await wrapper
      .find('[data-testid="workflow-set-chooser"]')
      .trigger("keydown", { key: "Escape" });
    await wrapper.vm.$nextTick();

    expect(document.activeElement).toBe(fill.element);
    wrapper.unmount();
  });

  it("keeps what is typed when the guess changes under an open offer", async () => {
    const bare = slotMember(1, "realvisXL_v5", "checkpoint", {
      base_model: null,
    });
    const { wrapper, store } = await mountGrid({
      rows: [
        {
          ...row(1, "realvisXL_v5", "checkpoint"),
          base_model_canonical: "SDXL",
          base_model_source: "filename_fuzzy",
        },
      ],
      handMade: [handSet(10, [bare])],
    });
    store.toggleSet("hand:10");
    store.checkpointAdded = { setId: 10, modelId: 1 };
    await wrapper.vm.$nextTick();
    await wrapper
      .find('[data-testid="base-model-offer"] input')
      .setValue("Pony");

    store.rows[0].base_model_canonical = "Illustrious";
    await wrapper.vm.$nextTick();

    expect(
      wrapper.find('[data-testid="base-model-offer"] input').element.value,
    ).toBe("Pony");
  });

  it("puts focus in the popup's filter once it has mounted", async () => {
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), row(3, "filmgrain_xl")],
      handMade: [handSet(10, [SET_CKPT])],
      attach: true,
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    await wrapper
      .findAll(".mss__tile--add")
      .find((tile) => tile.attributes("aria-label") === "Add LoRA…")
      .trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(document.activeElement).toBe(
      wrapper.find('[data-testid="workflow-set-chooser"] input').element,
    );
    wrapper.unmount();
  });

  it("enters a hand-made tray at its first tile from any column of a wide grid", async () => {
    // Four columns: the hand-made card sits in the second (the New tile is
    // first), so column arithmetic alone would land on the tray's SECOND tile.
    vi.stubGlobal(
      "ResizeObserver",
      class {
        constructor() {}
        observe(element) {
          Object.defineProperty(element, "clientWidth", {
            configurable: true,
            value: 1000,
          });
        }
        disconnect() {}
      },
    );
    try {
      const { wrapper, store } = await mountGrid({
        rows: [row(1, "realvisXL_v5", "checkpoint")],
        support: [row(2, "sdxl_vae", "vae")],
        handMade: [handSet(10, [SET_CKPT, SET_VAE])],
        attach: true,
      });
      store.toggleSet("hand:10");
      await wrapper.vm.$nextTick();
      await wrapper.find(".msg__row").trigger("click");

      await wrapper
        .find('[role="treegrid"]')
        .trigger("keydown", { key: "ArrowDown" });
      await wrapper.vm.$nextTick();

      expect(document.activeElement?.dataset.key).toBe(`m:${SET_CKPT.sha256}`);
      wrapper.unmount();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("does not light a checkpoint's evidence card for a pick in a hand-made tray", async () => {
    // #1589's rule, carried to the slots tray: the pick is drawn on the tile
    // the reader clicked, not on a card elsewhere that happens to share it.
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), row(3, "filmgrain_xl")],
      combinations: [combination("1,3", [CKPT, LORA])],
      handMade: [handSet(10, [SET_CKPT])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();

    await wrapper.find(`[data-key="m:${SET_CKPT.sha256}"]`).trigger("click");
    await wrapper.vm.$nextTick();

    expect([...store.selectedIds]).toEqual([1]);
    const evidence = wrapper.find('.msg__row[data-key="model:1"]');
    expect(evidence.attributes("aria-selected")).toBe("false");
  });

  it("marks a member whose file left the shelf, and does not let it be selected", async () => {
    const gone = slotMember(5, "old_lora", "lora", {
      on_shelf: false,
      id: null,
    });
    const { wrapper, store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [SET_CKPT, gone])],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();

    const tile = wrapper.find(".mss__tile--gone");
    expect(tile.text()).toContain("Not on shelf");
    expect(tile.attributes("aria-selected")).toBeUndefined();
    await tile.trigger("click");
    expect([...store.selectedIds]).toEqual([]);
  });

  describe("a template offer (#1999)", () => {
    const GHOST_LORA = {
      id: 3,
      sha256: "3".repeat(64),
      name: "filmgrain_xl",
      kind: "adapter",
      slot: "lora",
      picture_count: 0,
      recipes: 0,
    };
    const VAE = {
      name: "ae.safetensors",
      url: "https://example.com/ae.safetensors",
      directory: "vae",
      slot: "vae",
      sha256: "a".repeat(64),
    };
    const TEMPLATE_OFFER = {
      source: "template",
      template: { name: "image_realvis", title: "RealVis" },
      others: [{ name: "image_realvis_edit", title: "RealVis Edit" }],
      head_id: 1,
      head_name: "realvisXL_v5",
      picture_count: 0,
      recipes: 0,
      covers: [],
      models: [GHOST_LORA],
      downloads: [VAE, { ...VAE, name: "te.bin", url: "", sha256: "b".repeat(64) }],
    };

    async function mountOffer(extra = {}) {
      return mountGrid({
        rows: [row(1, "realvisXL_v5", "checkpoint"), row(3, "filmgrain_xl")],
        handMade: [
          handSet(10, [SET_CKPT], {
            name: "Portrait kit",
            offer: TEMPLATE_OFFER,
            ...extra,
          }),
        ],
      });
    }

    it("names the template, links what to download and lists its siblings", async () => {
      const { wrapper, store } = await mountOffer();
      const ask = wrapper.find('[data-testid="set-offer"]');
      expect(ask.text()).toContain(
        "The RealVis template loads 3 files this set does not have.",
      );
      expect(ask.find('[data-testid="set-offer-add"]').exists()).toBe(false);
      store.toggleSet("hand:10");
      await wrapper.vm.$nextTick();
      const strip = wrapper.find('[data-testid="merge-offer-strip"]');
      expect(strip.text()).toContain("The one on your shelf is dashed below.");
      expect(strip.text()).toContain("Other templates built around it: RealVis Edit.");
      const downloads = strip.find('[data-testid="offer-downloads"]');
      // A file the template list links is downloaded in the app, as a run's
      // missing model is; one with no link is only named.
      expect(downloads.find("a").exists()).toBe(false);
      const buttons = downloads.findAll('[data-testid="model-download"]');
      expect(buttons.map((b) => b.attributes("aria-label"))).toEqual([
        `Download ${VAE.name}`,
      ]);
      expect(downloads.text()).toContain("te.bin");
      expect(strip.text()).toContain("Add to set");
      const ghost = wrapper.find(".mss__tile--ghost");
      expect(ghost.text()).toContain("template");
      // An offered ghost is added, never "restored".
      expect(ghost.text()).toContain("Add");
      expect(ghost.text()).not.toContain("Restore");
    });

    it("downloads an offered file in the app, without moving the cursor", async () => {
      startModelDownload.mockResolvedValue({ id: "d1", file: VAE.name });
      const { wrapper, store } = await mountOffer();
      store.toggleSet("hand:10");
      await wrapper.vm.$nextTick();
      const strip = wrapper.find('[data-testid="merge-offer-strip"]');
      await strip.find('[data-testid="model-download"]').trigger("click");
      expect(startModelDownload).toHaveBeenCalledWith(VAE.name);
      expect(strip.classes()).not.toContain("mss__offer--cur");
    });

    it("keeps the downloads out with the models on Stop asking", async () => {
      setWorkflowSetDeclines.mockResolvedValue({
        set: handSet(10, [SET_CKPT]),
        previous: [],
      });
      const { wrapper } = await mountOffer();
      await wrapper.find('[data-testid="set-offer-no"]').trigger("click");
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(setWorkflowSetDeclines).toHaveBeenCalledWith(10, [
        GHOST_LORA.sha256,
        VAE.sha256,
        "b".repeat(64),
      ]);
    });

    it("adds one offered file from the card, and Enter on its ghost adds it", async () => {
      addWorkflowSetMembers.mockResolvedValue({
        set: handSet(10, [SET_CKPT]),
        added: [GHOST_LORA.sha256],
      });
      const one = { ...TEMPLATE_OFFER, downloads: [] };
      const { wrapper, store } = await mountOffer({ offer: one });
      await wrapper.find('[data-testid="set-offer-add"]').trigger("click");
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(addWorkflowSetMembers).toHaveBeenCalledWith(10, [
        { model_id: 3, slot: "lora" },
      ]);

      addWorkflowSetMembers.mockClear();
      store.toggleSet("hand:10");
      await wrapper.vm.$nextTick();
      await wrapper.find(".mss__tile--ghost").trigger("click");
      await wrapper.find('[role="treegrid"]').trigger("keydown", { key: "Enter" });
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(addWorkflowSetMembers).toHaveBeenCalledWith(10, [
        { model_id: 3, slot: "lora" },
      ]);
    });

    it("offers no Add when every file is a download", async () => {
      const { wrapper, store } = await mountOffer({
        offer: { ...TEMPLATE_OFFER, models: [], downloads: [VAE] },
      });
      const ask = wrapper.find('[data-testid="set-offer"]');
      expect(ask.text()).toContain(
        "The RealVis template loads a file this set does not have.",
      );
      expect(ask.text()).not.toContain("Add it?");
      store.toggleSet("hand:10");
      await wrapper.vm.$nextTick();
      const strip = wrapper.find('[data-testid="merge-offer-strip"]');
      // The file's own download button aside: it fetches, it does not add.
      const labels = strip
        .findAllComponents({ name: "AppButton" })
        .filter((button) => button.attributes("data-testid") !== "model-download")
        .map((button) => button.text());
      expect(labels).toEqual(["Stop asking"]);
      expect(strip.attributes("aria-label")).not.toContain("Enter adds");
    });
  });

  describe("a set living on its checkpoint (#1972)", () => {
    const FROM_PICTURES = slotMember(3, "filmgrain_xl", "lora", {
      origin: "pictures",
      stored: false,
    });
    const ADDED = slotMember(4, "soft_light", "lora", {
      origin: "added",
      stored: true,
    });
    const REMOVED = {
      id: 5,
      sha256: "5".repeat(64),
      name: "bad_hands",
      kind: "adapter",
      slot: "lora",
      picture_count: 0,
      recipes: 4,
    };

    async function mountLiving(extra = {}, { attach = false } = {}) {
      return mountGrid({
        attach,
        rows: [
          row(1, "realvisXL_v5", "checkpoint"),
          row(3, "filmgrain_xl"),
          row(4, "soft_light"),
          row(5, "bad_hands"),
        ],
        handMade: [
          handSet(
            10,
            [{ ...SET_CKPT, origin: "pictures", stored: true }, FROM_PICTURES, ADDED],
            {
              name: "Portrait kit",
              recipes: 14,
              picture_count: 212,
              declined: [REMOVED.sha256],
              removed: [REMOVED],
              ...extra,
            },
          ),
        ],
      });
    }

    it("says Yours, what it follows and what the owner changed on top", async () => {
      const { wrapper, store } = await mountLiving();
      const card = wrapper.find('[data-testid="model-set-card"]');
      expect(card.find('[data-testid="set-badge-yours"]').exists()).toBe(true);
      // Nothing new from the pictures: no dot, and no word of it.
      expect(card.find(".msc__new").exists()).toBe(false);
      expect(card.attributes("aria-label")).not.toContain("new from pictures");
      expect(card.attributes("aria-label")).toContain(
        "3 models, 14 recipes, 212 pictures",
      );
      expect(card.attributes("aria-label")).toContain("custom");

      store.toggleSet("hand:10");
      await wrapper.vm.$nextTick();
      const tray = wrapper.find('[data-testid="model-set-slots-panel"]');
      expect(tray.text()).toContain("2 changes from automatic");
      expect(tray.find('[data-testid="set-follows"]').text()).toBe(
        "Follows the pictures made with realvisXL v5: a model they start using joins this set. Your changes stay on top of it: 1 added, 1 removed.",
      );
      const tiles = tray.findAll(".msp__member");
      const added = tiles.find((t) => t.attributes("data-key") === `m:${ADDED.sha256}`);
      expect(added.text()).toContain("Added by you");
      expect(added.attributes("aria-label")).toContain("added by you");
      const picture = tiles.find(
        (t) => t.attributes("data-key") === `m:${FROM_PICTURES.sha256}`,
      );
      expect(picture.text()).not.toContain("Added by you");
      // The removed model stands where it was, with what it was used in.
      const ghost = tray.find(".mss__tile--ghost");
      expect(ghost.attributes("data-key")).toBe(`g:${REMOVED.sha256}`);
      expect(ghost.text()).toContain("Removed by you · 4 recipes");
    });

    it("reads Automatic with nothing changed, and offers no Reset", async () => {
      const { wrapper, store } = await mountLiving({
        automatic: true,
        declined: [],
        removed: [],
        members: [
          { ...SET_CKPT, origin: "pictures", stored: true },
          { ...FROM_PICTURES, new: true },
        ],
      });
      const card = wrapper.find('[data-testid="model-set-card"]');
      // Automatic draws no badge; the new-model dot stands alone instead.
      expect(card.find('[data-testid="set-badge-yours"]').exists()).toBe(false);
      expect(card.find('[data-testid="set-new-dot"]').exists()).toBe(true);
      expect(card.attributes("aria-label")).toContain("1 new from pictures");
      store.toggleSet("hand:10");
      await wrapper.vm.$nextTick();
      const tray = wrapper.find('[data-testid="model-set-slots-panel"]');
      expect(tray.find('[data-testid="set-reset"]').exists()).toBe(false);
      expect(tray.text()).not.toContain("changes from automatic");
      expect(tray.find(".mss__origin--new").text()).toBe("New");
    });

    it("restores a removed model from its ghost, by click or by Enter", async () => {
      const { wrapper, store } = await mountLiving();
      setWorkflowSetDeclines.mockResolvedValue({ set: { declined: [] }, previous: [] });
      store.toggleSet("hand:10");
      await wrapper.vm.$nextTick();
      const ghost = wrapper.find(".mss__tile--ghost");
      await ghost
        .findAll("button")
        .find((b) => b.text() === "Restore")
        .trigger("click");
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(setWorkflowSetDeclines).toHaveBeenCalledWith(10, []);

      setWorkflowSetDeclines.mockClear();
      const grid = wrapper.find('[role="treegrid"]');
      await ghost.trigger("click");
      await grid.trigger("keydown", { key: "Enter" });
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(setWorkflowSetDeclines).toHaveBeenCalledWith(10, []);
    });

    it("never reaches the file delete from a ghost", async () => {
      const { wrapper, store } = await mountLiving();
      store.toggleSet("hand:10");
      await wrapper.vm.$nextTick();
      await wrapper.find(".mss__tile--ghost").trigger("click");
      const press = new KeyboardEvent("keydown", {
        key: "Delete",
        bubbles: true,
        cancelable: true,
      });
      wrapper.find('[role="treegrid"]').element.dispatchEvent(press);
      expect(press.defaultPrevented).toBe(true);
      expect(setWorkflowSetDeclines).not.toHaveBeenCalled();
      expect(removeWorkflowSetMembers).not.toHaveBeenCalled();
    });

    it("keeps a model the pictures brought in out, and takes an added one out", async () => {
      const { wrapper, store } = await mountLiving();
      setWorkflowSetDeclines.mockResolvedValue({ set: { declined: [] }, previous: [] });
      removeWorkflowSetMembers.mockResolvedValue({ removed: [] });
      store.toggleSet("hand:10");
      await wrapper.vm.$nextTick();
      const remove = (sha) =>
        wrapper
          .find(`[data-key="m:${sha}"]`)
          .find(".mss__remove")
          .trigger("click");
      await remove(FROM_PICTURES.sha256);
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(setWorkflowSetDeclines).toHaveBeenCalledWith(10, [
        REMOVED.sha256,
        FROM_PICTURES.sha256,
      ]);
      expect(removeWorkflowSetMembers).not.toHaveBeenCalled();

      await remove(ADDED.sha256);
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(removeWorkflowSetMembers).toHaveBeenCalledWith(10, [ADDED.sha256]);
    });

    it("resets to automatic from the tray", async () => {
      const { wrapper, store } = await mountLiving();
      setWorkflowSetDeclines.mockResolvedValue({
        set: { declined: [] },
        previous: [REMOVED.sha256],
      });
      removeWorkflowSetMembers.mockResolvedValue({ removed: [] });
      store.toggleSet("hand:10");
      await wrapper.vm.$nextTick();
      await wrapper.find('[data-testid="set-reset"]').trigger("click");
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(setWorkflowSetDeclines).toHaveBeenCalledWith(10, []);
      // The checkpoint stays: it is what the set follows.
      expect(removeWorkflowSetMembers).toHaveBeenCalledWith(10, [ADDED.sha256]);
    });

    it("says the precision of a held model and of a removed one", async () => {
      // Two quants of one encoder: the shelf's name is the same for both.
      const held = slotMember(6, "Qwen3VL_4b_bf16.st", "text_encoder");
      const gone = { ...REMOVED, id: 7, sha256: "7".repeat(64), kind: "text_encoder", slot: "text_encoder" };
      const { wrapper, store } = await mountGrid({
        rows: [
          row(1, "realvisXL_v5", "checkpoint"),
          { ...row(6, "Qwen3VL_4b_bf16.st", "text_encoder"), quant: "bf16" },
          { ...row(7, "Qwen3VL_4b_fp8_scaled.st", "text_encoder"), quant: "fp8" },
        ],
        handMade: [handSet(10, [SET_CKPT, held], { removed: [gone] })],
      });
      store.toggleSet("hand:10");
      await wrapper.vm.$nextTick();
      const tray = wrapper.find('[data-testid="model-set-slots-panel"]');
      const quants = tray.findAll(".mss__quant").map((q) => q.text());
      expect(quants).toEqual(["BF16", "FP8"]);
    });
  });

  describe("New workflow set from a ComfyUI template (#2001)", () => {
    const file = (name, slot, onShelf, extra = {}) => ({
      name,
      url: `https://example.com/${name}`,
      directory: slot === "checkpoint" ? "diffusion_models" : "vae",
      slot,
      on_shelf: onShelf,
      model_id: null,
      other_quant: null,
      file_size: null,
      ...extra,
    });
    const variant = (name, files, extra = {}) => ({
      name,
      title: `${name} title`,
      kind: "image",
      tags: [],
      models: [`${name} model`],
      files,
      on_shelf: files.filter((f) => f.on_shelf).length,
      complete: files.every((f) => f.on_shelf),
      covered_by: null,
      head_arrived_at: null,
      usage: 0,
      new: false,
      ...extra,
    });
    const READY = variant("zit", [
      file("zit_bf16.safetensors", "checkpoint", true, { model_id: 1 }),
      file("ae.safetensors", "vae", true, { model_id: 2 }),
    ]);
    const COVERED = variant(
      "flux",
      [file("flux.safetensors", "checkpoint", true, { model_id: 3 })],
      { covered_by: { id: 10, name: "Portrait kit" } },
    );
    const PARTIAL = variant("wan", [
      file("wan_high.safetensors", "checkpoint", true, { model_id: 4 }),
      file("wan_low.safetensors", "checkpoint", false),
    ]);
    const TEMPLATES = [
      { key: "head:zit", variants: [READY] },
      { key: "head:flux", variants: [COVERED] },
      { key: "head:wan", variants: [PARTIAL] },
    ];

    async function openPicker(options) {
      const mounted = await mountGrid({
        rows: [row(1, "realvisXL_v5", "checkpoint")],
        templates: TEMPLATES,
        ...options,
      });
      await mounted.wrapper
        .find('[data-testid="new-workflow-set"]')
        .trigger("click");
      await new Promise((resolve) => setTimeout(resolve, 0));
      await mounted.wrapper.vm.$nextTick();
      return mounted;
    }

    function pressFor(wrapper, key) {
      return wrapper
        .find(`[data-testid="template-row"][data-key="${key}"]`)
        .find('[data-testid="template-press"]');
    }

    it("draws no ready line while nothing new is ready", async () => {
      const { wrapper } = await mountGrid({ rows: [row(1, "a", "checkpoint")] });
      expect(wrapper.find('[data-testid="new-ready-line"]').exists()).toBe(false);
      expect(wrapper.find(".msg__new-dot").exists()).toBe(false);
    });

    it("says how many new ones are ready on the tile", async () => {
      const { wrapper } = await mountGrid({
        rows: [row(1, "a", "checkpoint")],
        newReady: 2,
      });
      const line = wrapper.find('[data-testid="new-ready-line"]');
      expect(line.text()).toBe("2 new ready to make");
      // The dot is never the only signal: the line describes the tile.
      expect(wrapper.find(".msg__newrow").attributes("aria-describedby")).toBe(
        line.attributes("id"),
      );
    });

    it("leads with the ready template and makes its set from the shelf's files", async () => {
      createWorkflowSet.mockResolvedValue(handSet(12, []));
      const { wrapper } = await openPicker();

      const keys = wrapper
        .findAll('[data-testid="template-row"]')
        .map((r) => r.attributes("data-key"));
      // Ready first; the covered one is folded away; the partial one last.
      expect(keys).toEqual(["head:zit", "head:wan"]);
      expect(pressFor(wrapper, "head:zit").attributes("aria-label")).toBe(
        "Make set from zit model",
      );

      await pressFor(wrapper, "head:zit").trigger("click");
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(createWorkflowSet).toHaveBeenCalledWith({
        name: "zit model",
        template: "zit",
        members: [
          { model_id: 1, slot: "checkpoint" },
          { model_id: 2, slot: "vae" },
        ],
      });
    });

    it("makes a partial template's set from what is here and says what is missing", async () => {
      createWorkflowSet.mockResolvedValue(handSet(12, []));
      const { wrapper } = await openPicker();
      const press = pressFor(wrapper, "head:wan");
      expect(press.text()).toBe("Make set · 1 missing");

      await press.trigger("click");
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(createWorkflowSet).toHaveBeenCalledWith(
        expect.objectContaining({
          template: "wan",
          members: [{ model_id: 4, slot: "checkpoint" }],
        }),
      );
    });

    it("opens the set that already covers a template rather than make a second", async () => {
      const { wrapper, store } = await openPicker({
        handMade: [handSet(10, [SET_CKPT])],
      });
      const fold = wrapper.find('[data-testid="covered-fold"]');
      expect(fold.text()).toContain("1 already in a set: flux model, in Portrait kit");
      await fold.trigger("click");

      const press = pressFor(wrapper, "head:flux");
      expect(press.text()).toBe("In Portrait kit");
      await press.trigger("click");
      expect(createWorkflowSet).not.toHaveBeenCalled();
      expect(store.openSetKey).toBe("hand:10");
    });

    it("searches file names and filters by kind", async () => {
      const { wrapper } = await openPicker();
      const field = wrapper.find('[data-testid="workflow-template-picker"] input');
      await field.setValue("wan_low");
      expect(
        wrapper
          .findAll('[data-testid="template-row"]')
          .map((r) => r.attributes("data-key")),
      ).toEqual(["head:wan"]);
    });

    // A detached field never takes focus, so the tries run on; a test file
    // that ends meanwhile has its global `document` torn down under them.
    it("tries to focus the search field without the global document", async () => {
      const { wrapper } = await openPicker();
      const errors = vi.fn();
      wrapper.vm.$.appContext.config.errorHandler = errors;
      vi.stubGlobal("document", undefined);
      try {
        await new Promise((resolve) => setTimeout(resolve, 60));
      } finally {
        vi.unstubAllGlobals();
      }
      expect(errors).not.toHaveBeenCalled();
    });

    it("Enter on the cursor row makes that row's set, from the search field", async () => {
      createWorkflowSet.mockResolvedValue(handSet(12, []));
      const { wrapper } = await openPicker();
      const field = wrapper.find('[data-testid="workflow-template-picker"] input');
      await field.trigger("keydown", { key: "ArrowDown" });
      await field.trigger("keydown", { key: "ArrowDown" });
      await field.trigger("keydown", { key: "Enter" });
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(createWorkflowSet).toHaveBeenCalledWith(
        expect.objectContaining({ template: "wan" }),
      );
    });

    it("opens the set a later press finds already made, rather than make a second", async () => {
      const fresh = { ...READY, new: true };
      const { store } = await mountGrid({
        rows: [row(1, "a", "checkpoint")],
        handMade: [handSet(10, [SET_CKPT])],
        templates: [{ key: "head:zit", variants: [fresh] }],
      });
      const notices = useNoticeStore();
      store.onModelsArrived([2]);
      await new Promise((resolve) => setTimeout(resolve, 0));
      // Between the notice and the press, a set took the template.
      fetchWorkflowTemplates.mockResolvedValue({
        version: "0.1.117",
        comfyuiVersion: null,
        rows: [
          {
            key: "head:zit",
            variants: [{ ...fresh, new: false, covered_by: { id: 10, name: "Kit" } }],
          },
        ],
      });
      await notices.notices[0].action.handler();
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(createWorkflowSet).not.toHaveBeenCalled();
      expect(store.openSetKey).toBe("hand:10");
    });

    it("makes nothing from the notice when it cannot re-check the sets", async () => {
      const fresh = { ...READY, new: true };
      const { store } = await mountGrid({
        rows: [row(1, "a", "checkpoint")],
        templates: [{ key: "head:zit", variants: [fresh] }],
      });
      const notices = useNoticeStore();
      store.onModelsArrived([2]);
      await new Promise((resolve) => setTimeout(resolve, 0));
      fetchWorkflowTemplates.mockRejectedValue(new Error("offline"));

      await notices.notices[0].action.handler();
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(createWorkflowSet).not.toHaveBeenCalled();
      expect(notices.notices.at(-1).text).toContain(
        "Could not check whether zit model is already in a set",
      );
    });

    it("makes nothing from the notice when the template has left the list", async () => {
      const fresh = { ...READY, new: true };
      const { store } = await mountGrid({
        rows: [row(1, "a", "checkpoint")],
        templates: [{ key: "head:zit", variants: [fresh] }],
      });
      const notices = useNoticeStore();
      store.onModelsArrived([2]);
      await new Promise((resolve) => setTimeout(resolve, 0));
      fetchWorkflowTemplates.mockResolvedValue({
        version: "0.1.118",
        comfyuiVersion: null,
        rows: [],
      });

      await notices.notices[0].action.handler();
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(createWorkflowSet).not.toHaveBeenCalled();
      expect(notices.notices.at(-1).text).toContain("no longer in the template list");
    });

    it("makes nothing from the notice when a file has left the shelf", async () => {
      const fresh = { ...READY, new: true };
      const { store } = await mountGrid({
        rows: [row(1, "a", "checkpoint")],
        templates: [{ key: "head:zit", variants: [fresh] }],
      });
      const notices = useNoticeStore();
      store.onModelsArrived([2]);
      await new Promise((resolve) => setTimeout(resolve, 0));
      fetchWorkflowTemplates.mockResolvedValue({
        version: "0.1.117",
        comfyuiVersion: null,
        rows: [{ key: "head:zit", variants: [{ ...READY, complete: false }] }],
      });

      await notices.notices[0].action.handler();
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(createWorkflowSet).not.toHaveBeenCalled();
      expect(notices.notices.at(-1).text).toContain("Not every file for zit model");
    });

    it("a failed make shows why and puts focus back on the tile", async () => {
      createWorkflowSet.mockRejectedValue(new Error("is still being hashed"));
      const { wrapper } = await openPicker({ attach: true });

      await pressFor(wrapper, "head:zit").trigger("click");
      await new Promise((resolve) => setTimeout(resolve, 0));
      await wrapper.vm.$nextTick();
      expect(useNoticeStore().notices.at(-1)).toMatchObject({ level: "error" });
      expect(document.activeElement?.classList.contains("msg__newrow")).toBe(
        true,
      );
      wrapper.unmount();
    });

    it("puts a deleted set's template back with it on Undo", async () => {
      deleteWorkflowSet.mockResolvedValue({
        deleted: { ...handSet(10, [SET_CKPT]), template: "zit" },
      });
      createWorkflowSet.mockResolvedValue(handSet(11, [SET_CKPT]));
      const { store } = await mountGrid({
        rows: [row(1, "a", "checkpoint")],
        handMade: [handSet(10, [SET_CKPT])],
      });
      await store.deleteHandMadeSets(store.handMadeSets);
      await useOperationStore().takeLocalReceiptAction();
      expect(createWorkflowSet).toHaveBeenCalledWith(
        expect.objectContaining({ template: "zit" }),
      );
    });

    it("puts focus back on the tile when the name filter hides the new set", async () => {
      createWorkflowSet.mockResolvedValue({ ...handSet(12, []), name: "zit model" });
      const { wrapper, store } = await openPicker({ attach: true });
      store.filters.name = "nothing like it";
      await wrapper.vm.$nextTick();

      await pressFor(wrapper, "head:zit").trigger("click");
      await new Promise((resolve) => setTimeout(resolve, 0));
      await wrapper.vm.$nextTick();
      expect(document.activeElement?.classList.contains("msg__newrow")).toBe(
        true,
      );
      wrapper.unmount();
    });

    it("an arrival while the picker is reading never leaves it loading", async () => {
      const { store } = await mountGrid({ rows: [row(1, "a", "checkpoint")] });
      let answerPicker;
      fetchWorkflowTemplates
        .mockImplementationOnce(
          () => new Promise((resolve) => (answerPicker = resolve)),
        )
        .mockRejectedValueOnce(new Error("offline"));
      const warn = vi.spyOn(console, "warn").mockImplementation(() => {});

      const picker = store.loadWorkflowTemplates();
      store.onModelsArrived([2]);
      await new Promise((resolve) => setTimeout(resolve, 0));
      answerPicker({ version: "v", comfyuiVersion: null, rows: TEMPLATES });
      await picker;

      expect(store.templatesLoading).toBe(false);
      expect(store.workflowTemplates.rows).toHaveLength(TEMPLATES.length);
      warn.mockRestore();
    });

    it("announces an arrival that completed a template, and its button makes the set", async () => {
      createWorkflowSet.mockResolvedValue(handSet(12, []));
      const fresh = { ...READY, new: true };
      const { store } = await mountGrid({
        rows: [row(1, "a", "checkpoint")],
        templates: [
          { key: "head:zit", variants: [fresh] },
          // Shares the file that arrived, but its head did not just arrive
          // (or a set covers it): not new, so no notice either.
          {
            key: "head:old",
            variants: [
              variant("old", [
                file("old.safetensors", "checkpoint", true, { model_id: 8 }),
                file("ae.safetensors", "vae", true, { model_id: 2 }),
              ]),
            ],
          },
          // New too, but nothing of it arrived now: no notice for it.
          {
            key: "head:other",
            variants: [
              variant(
                "other",
                [file("o.safetensors", "checkpoint", true, { model_id: 9 })],
                { new: true },
              ),
            ],
          },
        ],
      });
      const notices = useNoticeStore();

      store.onModelsArrived([2]);
      await new Promise((resolve) => setTimeout(resolve, 0));

      expect(notices.notices.map((n) => n.text)).toEqual([
        "All 2 files for zit model are here.",
      ]);
      expect(createWorkflowSet).not.toHaveBeenCalled();
      await notices.notices[0].action.handler();
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(createWorkflowSet).toHaveBeenCalledWith(
        expect.objectContaining({ template: "zit" }),
      );
    });
  });

  it("leads a favourite hand-made set inside its group, and the groups keep their order", async () => {
    listFavourites.mockResolvedValue({ model: [], workflow: [], set: ["hand:11"] });
    const { store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [SET_CKPT]), handSet(11, [SET_CKPT])],
    });
    expect(store.handMadeGroups.map((g) => g.key)).toEqual(["hand:11", "hand:10"]);
    store.setView({ favouritesFirst: false });
    expect(store.handMadeGroups.map((g) => g.key)).toEqual(["hand:10", "hand:11"]);
  });

  it("draws a heart on a card and writes the set's own key when it is pressed", async () => {
    const { wrapper } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [SET_CKPT])],
    });
    const heart = wrapper.find('[data-testid="model-set-card"] [data-testid="favourite-heart"]');
    expect(heart.exists()).toBe(true);
    await heart.trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(setFavourites).toHaveBeenCalledWith("set", ["hand:10"], true);
  });

  it("Favourites only narrows the grid's cards to the favourite sets", async () => {
    listFavourites.mockResolvedValue({ model: [], workflow: [], set: ["hand:11"] });
    const { store } = await mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint")],
      handMade: [handSet(10, [SET_CKPT]), handSet(11, [SET_CKPT])],
    });
    store.setFilters({ favouritesOnly: true });
    expect(store.handMadeGroups.map((g) => g.key)).toEqual(["hand:11"]);
  });
});

describe("hiding a set from pictures", () => {
  async function mountOneSet() {
    return mountGrid({
      rows: [row(1, "realvisXL_v5", "checkpoint"), row(3, "filmgrain_xl")],
      combinations: [combination("1+3", [CKPT, LORA])],
      attach: true,
    });
  }

  it("hides on Delete, never reaching the file delete, and Undo shows it again", async () => {
    const { wrapper, store } = await mountOneSet();
    const grid = wrapper.find('[role="treegrid"]');
    // New workflow set → the card.
    await grid.trigger("keydown", { key: "ArrowRight" });
    const onWindowKey = vi.fn();
    window.addEventListener("keydown", onWindowKey);
    await grid.trigger("keydown", { key: "Delete" });
    window.removeEventListener("keydown", onWindowKey);
    // The hide is a server write; the grid redraws when its answer lands.
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(onWindowKey).not.toHaveBeenCalled();
    expect(wrapper.findAll('[data-testid="model-set-card"]')).toHaveLength(0);
    // Said, not counted.
    expect(wrapper.find(".msg__hidden").text()).toBe(
      "Some sets are hidden.Show them",
    );
    wrapper.unmount();
    const operations = useOperationStore();
    expect(operations.receipt.summary).toContain("Hid the set");
    await operations.takeLocalReceiptAction();
    expect(store.setGroups).toHaveLength(1);
  });

  it("makes the open set one of the owner's from its tray's Edit set", async () => {
    const { wrapper, store } = await mountOneSet();
    createWorkflowSet.mockResolvedValue({ id: 12, members: [] });
    // Offered in the tray, where the set's models are in front of the reader.
    expect(wrapper.find('[data-testid="set-clone"]').exists()).toBe(false);
    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();
    await wrapper.find('[data-testid="set-clone"]').trigger("click");
    // Its checkpoint: the pictures on it bring in the rest (#1972).
    expect(createWorkflowSet).toHaveBeenCalledWith({
      cloned: true,
      seen: true,
      members: [{ model_id: 1, slot: "checkpoint" }],
    });
    wrapper.unmount();
  });

  it("keeps the grid and its Show them line when every set is hidden", async () => {
    const { wrapper } = await mountOneSet();
    await wrapper.find('[data-testid="set-hide"]').trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(wrapper.text()).not.toContain("No picture in this library");
    expect(wrapper.find('[data-testid="toggle-hidden-sets"]').exists()).toBe(
      true,
    );
    wrapper.unmount();
  });

  it("hides from the card's button and draws hidden sets on request", async () => {
    const { wrapper } = await mountOneSet();
    await wrapper.find('[data-testid="set-hide"]').trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(wrapper.findAll('[data-testid="model-set-card"]')).toHaveLength(0);

    await wrapper.find('[data-testid="toggle-hidden-sets"]').trigger("click");
    const card = wrapper.find('[data-testid="model-set-card"]');
    expect(card.classes()).toContain("msc--hidden");
    expect(card.text()).toContain("Hidden");

    await wrapper.find('[data-testid="set-hide"]').trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(card.classes()).not.toContain("msc--hidden");
    wrapper.unmount();
  });
});

describe("ModelSetGrid owner verdicts", () => {
  const entry = (extra = {}) => ({
    combo_key: "1,2",
    member_ids: [1, 2],
    evidence: { together: 6, checked: 5, passing: 4 },
    check: "pass",
    verdict: null,
    suspects: [],
    member_verdicts: [],
    ...extra,
  });
  const base = {
    rows: [row(1, "realvisXL_v5")],
    support: [row(2, "sdxl_vae", "vae")],
    combinations: [combination("1,2", [CKPT, VAE])],
  };

  it("draws the owner's verdict on the card, joined by combo key", async () => {
    const { wrapper } = await mountGrid({
      ...base,
      setChecks: [entry({ verdict: "no" })],
    });
    const icon = wrapper.get(
      '[data-testid="model-set-card"] [data-testid="verdict-icon"]',
    );
    expect(icon.attributes("aria-label")).toBe(
      "Your verdict: this set does not produce sensible output",
    );
  });

  it("never draws PixlStash's check on the card, only an answer", async () => {
    const { wrapper } = await mountGrid({ ...base, setChecks: [entry()] });
    expect(wrapper.find('[data-testid="verdict-icon"]').exists()).toBe(false);
  });

  it("draws no check or verdict UI when no entry matches the union", async () => {
    const { wrapper, store } = await mountGrid({
      ...base,
      setChecks: [entry({ combo_key: "1,9", verdict: "yes" })],
    });
    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-testid="verdict-icon"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="verdict-ask"]').exists()).toBe(false);
    expect(wrapper.get(".msp__note").text()).toContain(
      "not necessarily run with",
    );
  });

  it("PUTs the answer under the set's combo key and then shows the icon", async () => {
    const { wrapper, store } = await mountGrid({
      ...base,
      setChecks: [entry()],
    });
    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();
    await wrapper.get('[data-testid="verdict-yes"]').trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    await wrapper.vm.$nextTick();
    expect(putSetVerdict).toHaveBeenCalledWith("1,2", "yes");
    const icon = wrapper.get('.msp [data-testid="verdict-icon"]');
    expect(icon.attributes("aria-label")).toContain(
      "Your verdict: this set produces sensible output",
    );
  });

  const handMember = (id, name, slot) => ({
    sha256: String(id).repeat(64).slice(0, 64),
    slot,
    label: name,
    on_shelf: true,
    id,
    name,
    filename: name,
    kind: slot,
    base_model: "SDXL",
    file_size: 1000,
  });
  const handBase = {
    rows: [row(1, "realvisXL_v5", "checkpoint")],
    support: [row(2, "sdxl_vae", "vae")],
    handMade: [
      {
        id: 10,
        name: null,
        created_at: "2026-09-26T08:00:00Z",
        updated_at: "2026-09-26T08:00:00Z",
        incomplete: false,
        checkpoint_ids: [1],
        picture_count: 0,
        recipes: 0,
        covers: [],
        members: [
          handMember(1, "realvisXL_v5", "checkpoint"),
          handMember(2, "sdxl_vae", "vae"),
        ],
      },
    ],
  };

  it("gives a hand-made set the same note, question and icons", async () => {
    const { wrapper, store } = await mountGrid({
      ...handBase,
      setChecks: [
        entry({
          member_verdicts: [{ model_id: 2, verdict: "problem" }],
        }),
      ],
    });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    const panel = wrapper.get('[data-testid="model-set-slots-panel"]');
    expect(panel.get('[data-testid="model-set-ask"]').text()).toContain(
      "Do you agree?",
    );
    expect(panel.find('[data-testid="model-set-note"]').text()).not.toContain(
      "Each of these has run with",
    );
    await panel.get('[data-testid="verdict-yes"]').trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(putSetVerdict).toHaveBeenCalledWith("1,2", "yes");
    // The VAE tile carries its own answer, and pressing it asks again.
    const icon = panel.get('.mss__tile [data-testid="verdict-icon"]');
    expect(icon.attributes("aria-label")).toBe(
      "Your verdict: sdxl vae is a problem in this set",
    );
  });

  it("says nothing about checks on a hand-made set with no entry", async () => {
    const { wrapper, store } = await mountGrid({ ...handBase, setChecks: [] });
    store.toggleSet("hand:10");
    await wrapper.vm.$nextTick();
    const panel = wrapper.get('[data-testid="model-set-slots-panel"]');
    expect(panel.find('[data-testid="model-set-note"]').exists()).toBe(false);
  });

  it("keeps the question open with an alert when the PUT is refused", async () => {
    putSetVerdict.mockRejectedValue(new Error("403"));
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    const { wrapper, store } = await mountGrid({
      ...base,
      setChecks: [entry()],
    });
    store.toggleSet("model:1");
    await wrapper.vm.$nextTick();
    await wrapper.get('[data-testid="verdict-yes"]').trigger("click");
    await new Promise((resolve) => setTimeout(resolve, 0));
    await wrapper.vm.$nextTick();
    expect(wrapper.get('.msp [role="alert"]').text()).toContain(
      "Could not save your answer. Try again.",
    );
    expect(wrapper.find('.msp [data-testid="verdict-icon"]').exists()).toBe(
      false,
    );
    expect(errorSpy).toHaveBeenCalledWith(
      "Could not record the set verdict",
      expect.objectContaining({ key: "1,2", verdict: "yes" }),
    );
    errorSpy.mockRestore();
  });
});

it("names a set's models for a model file dropped on its card", async () => {
  // The window's drop handler reads them to put the file beside the set's own
  // models (useModelDropTarget).
  const { wrapper } = await mountGrid({
    rows: [row(1, "realvisXL_v5")],
    support: [row(2, "sdxl_vae", "vae")],
    combinations: [combination("1,2", [CKPT, VAE])],
  });
  const cells = wrapper
    .findAll(".msg__cell")
    .map((cell) => cell.attributes("data-model-drop-models"))
    .filter(Boolean);
  expect(cells).toEqual(["1 2"]);
});
