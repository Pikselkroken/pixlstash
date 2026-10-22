// The Workflow tab (v1.12 F3): the three things about it that can silently
// invert.
//
// * A default's provenance and its reset. Reset is a WHOLE-SET write, so the
//   request has to carry every other edited value; a reset that sends only
//   the row it was fired on resets all of them and reads as working.
// * Models and Defaults read the detail's `default_recipe` (#1623), never the
//   base card's own slots once the recipe has been read.
// * Run… with several workflows selected. It must stay on screen and refuse,
//   which is `aria-disabled` plus the reason; a button that disappears
//   teaches nobody why.

import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const getWorkflowCard = vi.fn();
const editModels = vi.fn();
vi.mock("../../api/modelShelf", async (importOriginal) => ({
  ...(await importOriginal()),
  editModels: (...args) => editModels(...args),
}));
const listWorkflowCards = vi.fn();
const patchWorkflowCard = vi.fn();
const preflightWorkflowRun = vi.fn();
const setWorkflowDefaults = vi.fn();
const setWorkflowPins = vi.fn();
const getLoraSummary = vi.fn();
const getLoraChain = vi.fn();
const getWorkflowFormInputs = vi.fn();
const readModelSwap = vi.fn();
const setWorkflowDefaultLora = vi.fn();
const getWorkflowChanges = vi.fn();
const setWorkflowChanges = vi.fn();
const discardWorkflowChanges = vi.fn();
const saveWorkflow = vi.fn();
const saveWorkflowAsNew = vi.fn();
const getWorkflowVersions = vi.fn();
const restoreWorkflowVersion = vi.fn();

vi.mock("../../api/workflows", () => ({
  getWorkflowVersions: (...args) => getWorkflowVersions(...args),
  restoreWorkflowVersion: (...args) => restoreWorkflowVersion(...args),
  getWorkflowCard: (...args) => getWorkflowCard(...args),
  listWorkflowCards: (...args) => listWorkflowCards(...args),
  patchWorkflowCard: (...args) => patchWorkflowCard(...args),
  preflightWorkflowRun: (...args) => preflightWorkflowRun(...args),
  setWorkflowDefaults: (...args) => setWorkflowDefaults(...args),
  setWorkflowPins: (...args) => setWorkflowPins(...args),
  getLoraSummary: (...args) => getLoraSummary(...args),
  // `WorkflowCard` renders its covers through this, so a mock without it
  // throws in the render and every assertion in the file goes with it.
  workflowCoverUrl: (cover) => cover?.url ?? "",
  getLoraChain: (...args) => getLoraChain(...args),
  getWorkflowFormInputs: (...args) => getWorkflowFormInputs(...args),
  readModelSwap: (...args) => readModelSwap(...args),
  setWorkflowDefaultLora: (...args) => setWorkflowDefaultLora(...args),
  getWorkflowChanges: (...args) => getWorkflowChanges(...args),
  setWorkflowChanges: (...args) => setWorkflowChanges(...args),
  discardWorkflowChanges: (...args) => discardWorkflowChanges(...args),
  saveWorkflow: (...args) => saveWorkflow(...args),
  saveWorkflowAsNew: (...args) => saveWorkflowAsNew(...args),
}));

const getPixlstashNode = vi.fn();
vi.mock("../../api/comfyui", () => ({
  getPixlstashNode: (...args) => getPixlstashNode(...args),
}));

// *Show all N pictures* (F7) leaves this screen for the library, and
// `?tab=recipes` (#1480) arrives on it from the lightbox's match banner.
const push = vi.fn();
const replace = vi.fn();
const route = vi.hoisted(() => ({ name: "workflows", query: {} }));
vi.mock("vue-router", () => ({
  useRouter: () => ({ push, replace }),
  useRoute: () => route,
}));

import WorkflowTab from "./WorkflowTab.vue";
import KindMenu from "../widgets/KindMenu.vue";
import { useFilterStore } from "../../stores/useFilterStore";
import { useNoticeStore } from "../../stores/useNoticeStore";
import { useRunDialogStore } from "../../stores/useRunDialogStore";
import { useSearchStore } from "../../stores/useSearchStore";
import { useSelectionStore } from "../../stores/useSelectionStore";
import { useSidebarStore } from "../../stores/useSidebarStore";
import { useTasksStore } from "../../stores/useTasksStore";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";

/** Called with the workflow id when *Use a set…* is opened from outside. */
const useSetOpened = vi.fn();
/** What waits on the server: the changes routes below are one small fake. */
let waitingOnServer = null;

const KEY = "a".repeat(64);
const OTHER = "b".repeat(64);

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      "v-menu": {
        template: "<div><slot name='activator' :props='{}'/><slot/></div>",
      },
      Tooltip: true,
      // The set picker is a component of its own with tests of its own: here
      // it is the button, what it is given, and what it hands back.
      WorkflowUseSetMenu: {
        name: "WorkflowUseSetMenu",
        props: ["workflowId", "models", "disabled", "disabledReason"],
        emits: ["use"],
        methods: {
          open() {
            useSetOpened(this.workflowId);
          },
        },
        template:
          '<button data-testid="wftab-use-set" :disabled="disabled">Use a set…</button>',
      },
    },
  },
};

function card(overrides = {}) {
  const shown = {
    id: KEY,
    name: "Cinematic portrait",
    type: "txt2img",
    imported: false,
    models: [
      {
        name: "realvisXL_v5.safetensors",
        kind: "checkpoint",
        slot_label: "m1",
      },
    ],
    loras: [
      {
        name: "lightning-8step",
        kind: "lora",
        slot_label: "l1",
      },
    ],
    picture_count: 184,
    rating: 4.8,
    covers: [],
    saved_recipe_count: 0,
    defaults: [],
    base_topology: "d".repeat(64),
    topologies: ["d".repeat(64)],
    variant_count: 1,
    last_used: null,
    rank: 4.5,
    ...overrides,
  };
  // Each named slot records the file its recipe model names, as the server
  // serves it: that is what a recipe model is matched to its slot by.
  shown.models = (shown.models ?? []).map((model) =>
    model.name && model.filename === undefined
      ? { ...model, filename: `${model.name}.safetensors` }
      : model,
  );
  return shown;
}

const ADA = `asset:${"1".repeat(64)}`;
const BO = `asset:${"2".repeat(64)}`;

/** `GET /workflows/{id}/lora-summary`: two LoRAs that change, Ada on the cover. */
function loraSummary(overrides = {}) {
  return {
    pictures: 5,
    shared: [],
    varying: [
      {
        asset: BO,
        filename: "bo.safetensors",
        name: "Bo",
        on_shelf: true,
        // The shelf file's CONTENT digest: not the hex of the asset (a name hash).
        sha256: "b".repeat(64),
        pictures: 3,
        picture_ids: [31, 32, 33],
      },
      {
        asset: ADA,
        filename: "ada.safetensors",
        name: "Ada",
        on_shelf: true,
        sha256: "a".repeat(64),
        pictures: 2,
        picture_ids: [21, 22],
      },
    ],
    without: null,
    cover_asset: ADA,
    ...overrides,
  };
}

/** `GET /workflows/{id}/lora-chain`, as the route serves it (#1478). */
function loraChain(overrides = {}) {
  return {
    workflow_id: KEY,
    editable: true,
    refusal: null,
    source: { node_id: "4", class_type: "CheckpointLoaderSimple", outputs: ["MODEL", "CLIP"] },
    sink: { summary: "KSampler #7 reads model", consumers: [] },
    loaders: [
      { node_id: "14", name: "lightning-8step", filename: "lightning-8step.safetensors", strength: 1, on_shelf: true, sha256: "s1" },
      { node_id: "22", name: "neon-rain-v2", filename: "neon-rain-v2.safetensors", strength: 0.85, on_shelf: true, sha256: "s2" },
      { node_id: "31", name: "film-grain-35mm", filename: "film-grain-35mm.safetensors", strength: 0.4, on_shelf: true, sha256: "s3" },
      { node_id: "33", name: "hairstyle-v3", filename: "hairstyle-v3.safetensors", strength: 0.6, on_shelf: false, sha256: null },
    ],
    added_loader_class: "LoraLoader",
    ...overrides,
  };
}

/** A card slot word -> the `default_recipe.models[].kind` the server sends. */
const RECIPE_KIND = { unet: "checkpoint", clip: "text_encoder" };

/**
 * `GET /workflows/{id}`. The card's `default_recipe` is filled here, as the
 * detail read fills it: its `values` are the `defaults` a test names.
 */
function detail({ card: cardOverrides = {}, ...overrides } = {}) {
  const shown = card(cardOverrides);
  return {
    card: {
      default_recipe: {
        sampled: 3,
        // The base card's named slots at their addresses, as the server
        // samples them for a card with one variant.
        models: (shown.models ?? [])
          .filter((model) => model.name)
          .map((model) => ({
            address: `core:${model.slot_label}/ckpt_name`,
            // The model-fix kind the server sends, not the slot word.
            kind: RECIPE_KIND[model.kind] ?? model.kind,
            filename: `${model.name}.safetensors`,
            provenance: "best",
          })),
        loras: [],
        values: shown.defaults,
        stages: { upscale: false, face_detailer: false },
      },
      ...shown,
    },
    notes: null,
    hidden: false,
    variants: [],
    pins: null,
    ...overrides,
  };
}

const STEPS = {
  label: "steps",
  slot_label: "slot-a",
  input_name: "steps",
  value: "8",
  provenance: "best",
};
const CFG = {
  label: "cfg",
  slot_label: "slot-a",
  input_name: "cfg",
  value: "7.5",
  provenance: "edited",
};
const WIDTH = {
  label: "width",
  slot_label: "slot-a",
  input_name: "width",
  value: "832",
  provenance: "edited",
};

const SAMPLER = {
  label: "sampler_name",
  slot_label: "slot-a",
  input_name: "sampler_name",
  value: "euler",
  provenance: "best",
};

/** The default row whose label is `name`, wherever the fold has put it. */
function rowNamed(wrapper, name) {
  const row = wrapper
    .findAll(".wfdef")
    .find((entry) => entry.find(".wfdef-name").text().startsWith(name));
  if (!row) throw new Error(`no defaults row called ${name}`);
  return row;
}

/** A default row's lock: pressed when the parameter is fixed. */
function lockOf(wrapper, name) {
  return rowNamed(wrapper, name).find("[data-testid='wfdef-lock']");
}

/** Whether the row called `name` is drawn inside the Fixed group. */
function inFixedGroup(wrapper, name) {
  const group = wrapper.find("[data-testid='wftab-fixed-group']");
  if (!group.exists()) return false;
  return group.element.contains(rowNamed(wrapper, name).element);
}

async function flush(wrapper) {
  await new Promise((resolve) => setTimeout(resolve, 0));
  await wrapper.vm.$nextTick();
}

// Unmounted after each test: a pre-flight still settling would otherwise
// land in the next test's call count.
const mounted = [];
afterEach(() => {
  while (mounted.length) mounted.pop().unmount();
});

/** Mount the rail with `keys` selected and `cards` in the grid. */
async function mountWith(keys, cards = [card()]) {
  const store = useWorkflowsStore();
  store.cards = cards;
  store.selectedKeys = keys;
  const wrapper = mount(WorkflowTab, globalOpts);
  mounted.push(wrapper);
  await flush(wrapper);
  return { wrapper, store };
}

/** Past the pre-flight and the replacement reads it sends, both rendered. */
async function settle(wrapper) {
  await flush(wrapper);
  await flush(wrapper);
}

/**
 * `readModelSwap` answering `?replacing=` per slot kind, as the server does:
 * `replacements` already filtered to what goes with the checkpoint and what
 * the loader lists (#1596).
 */
function replacementsByKind(byKind, reasons = {}, narrowed = true) {
  return (_key, { slotKind } = {}) =>
    Promise.resolve({
      replacements: byKind[slotKind] ?? [],
      replacements_reason: reasons[slotKind] ?? null,
      replacements_narrowed: slotKind === "checkpoint" ? narrowed : null,
    });
}

function textOf(wrapper) {
  return wrapper.text().replace(/\s+/g, " ");
}

beforeEach(() => {
  setActivePinia(createPinia());
  window.localStorage.clear();
  route.query = {};
  useSidebarStore().workflowInspectorOpen = true;
  getWorkflowCard.mockReset().mockResolvedValue(detail());
  listWorkflowCards.mockReset().mockResolvedValue({
    cards: [card()],
    one_offs: 0,
    hidden: 0,
  });
  patchWorkflowCard.mockReset().mockResolvedValue(detail());
  setWorkflowDefaults.mockReset().mockResolvedValue(detail());
  setWorkflowPins.mockReset().mockResolvedValue({ pins: [] });
  setWorkflowDefaultLora.mockReset().mockResolvedValue(detail());
  getLoraSummary.mockReset().mockResolvedValue(loraSummary());
  getLoraChain.mockReset().mockResolvedValue(loraChain());
  getWorkflowFormInputs.mockReset().mockResolvedValue({ nodes: [] });
  getWorkflowVersions.mockReset().mockResolvedValue({ versions: [] });
  restoreWorkflowVersion.mockReset().mockResolvedValue({ versions: [] });
  replace.mockReset();
  preflightWorkflowRun.mockReset().mockResolvedValue({ groups: [] });
  // One small fake of the changes routes: a write is kept, a read answers
  // it, a discard clears it. Every write answers the state it now holds.
  waitingOnServer = null;
  useSetOpened.mockReset();
  getWorkflowChanges
    .mockReset()
    .mockImplementation(() => Promise.resolve(changesState(waitingOnServer)));
  setWorkflowChanges.mockReset().mockImplementation((_key, set) => {
    waitingOnServer = set;
    return Promise.resolve(changesState(set));
  });
  discardWorkflowChanges.mockReset().mockImplementation(() => {
    waitingOnServer = null;
    return Promise.resolve(changesState());
  });
  saveWorkflow.mockReset().mockResolvedValue({
    workflow_id: KEY,
    version: 2,
    new_workflow: false,
    left_out: [],
  });
  saveWorkflowAsNew.mockReset().mockResolvedValue({
    workflow_id: OTHER,
    name: "Portrait, softer",
    new_workflow: true,
    left_out: [],
  });
  getWorkflowVersions.mockReset().mockResolvedValue(versionsAt(3));
  restoreWorkflowVersion.mockReset().mockResolvedValue(versionsAt(4));
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

/**
 * `GET /workflows/{id}/changes`, and what every write of the set answers:
 * `waiting` null when nothing waits, `count` as the server counts it.
 */
function changesState(waiting = null, overrides = {}) {
  const set = waiting ?? {};
  const count =
    (set.models?.length ?? 0) +
    (set.loras?.length ?? 0) +
    (set.skip_loras?.length ?? 0) +
    Object.keys(set.stages ?? {}).length +
    (set.values?.length ?? 0);
  return {
    workflow_id: KEY,
    version: null,
    waiting: count ? set : null,
    count,
    ran: null,
    save: { open: true, reason: null },
    save_as_new: { open: true, reason: null },
    ran_save: null,
    ...overrides,
  };
}

/** A card with a history: `number` is its current version. */
function versioned(number = 3, overrides = {}) {
  return card({
    versions: Math.min(number, 50),
    version: number,
    version_at: "2026-10-02T12:00:00+00:00",
    origin_category: "own",
    ...overrides,
  });
}

/**
 * `GET /workflows/{id}/versions` with `current` the newest, newest first, in
 * the server's own words. Version 4 is what going back to 2 makes.
 */
function versionsAt(current, workflowId = KEY) {
  const all = [
    { version: 4, created_at: "2026-10-05T12:00:00+00:00", reason: "went back to version 2" },
    { version: 3, created_at: "2026-10-02T12:00:00+00:00", reason: "models changed" },
    { version: 2, created_at: "2026-09-28T12:00:00+00:00", reason: "LoRAs edited" },
    { version: 1, created_at: "2026-09-20T12:00:00+00:00", reason: null },
  ];
  return {
    workflow_id: workflowId,
    version: current,
    versions: all
      .filter((row) => row.version <= current)
      .map((row) => ({ ...row, current: row.version === current })),
  };
}

/** A promise and the function that settles it, for an answer held back. */
function held() {
  let release;
  const promise = new Promise((resolve) => {
    release = resolve;
  });
  return { promise, release };
}

describe("the version menu in the head", () => {
  const chip = (wrapper) => wrapper.find('[data-testid="wftab-version"]');
  const rows = (wrapper) =>
    wrapper.findAll('[data-testid="wftab-version-menu"] [data-version]');
  const row = (wrapper, number) =>
    wrapper.find(
      `[data-testid="wftab-version-menu"] [data-version="${number}"]`,
    );
  const refusal = (wrapper) =>
    wrapper.find('[data-testid="wftab-version-error"]');

  /** Mount on a card at version 3 whose detail and grid reads agree. */
  async function mountVersioned(number = 3) {
    const shown = versioned(number);
    getWorkflowCard.mockResolvedValue(detail({ card: shown }));
    listWorkflowCards.mockResolvedValue({ cards: [shown], one_offs: 0, hidden: 0 });
    return mountWith([KEY], [shown]);
  }

  /** Two rails: KEY on version 3, OTHER on version 2, each with its own list. */
  async function mountTwoRails() {
    const a = versioned(3);
    const b = versioned(2, { id: OTHER, name: "Other" });
    getWorkflowCard.mockImplementation((key) =>
      Promise.resolve(detail({ card: key === OTHER ? b : a })),
    );
    getWorkflowVersions.mockImplementation((key) =>
      Promise.resolve(key === OTHER ? versionsAt(2, OTHER) : versionsAt(3)),
    );
    return mountWith([KEY], [a, b]);
  }

  it("is its own button beside the pictures link, saying the version and its day", async () => {
    const { wrapper } = await mountVersioned();
    expect(chip(wrapper).element.tagName).toBe("BUTTON");
    expect(chip(wrapper).attributes("aria-haspopup")).toBe("menu");
    expect(chip(wrapper).text()).toMatch(/^Version 3 · (2 Oct|Oct 2)$/);
    // The count is still the link it was.
    expect(wrapper.find('[data-testid="wftab-show-pictures"]').text()).toBe(
      "184 pictures",
    );
  });

  it("reads the head off the card while the versions read is still out", async () => {
    getWorkflowVersions.mockReturnValue(new Promise(() => {}));
    const { wrapper } = await mountVersioned();
    expect(chip(wrapper).text()).toMatch(/^Version 3 · /);
    expect(
      wrapper.find('[data-testid="wftab-version-unread"]').text(),
    ).toBe("Reading the versions…");
  });

  it("names the current version's number, not how many are kept", async () => {
    // Past the 50 kept, version 61 is the 50th row: the head says 61.
    const pruned = versioned(61);
    getWorkflowVersions.mockReturnValue(new Promise(() => {}));
    getWorkflowCard.mockResolvedValue(detail({ card: pruned }));
    const { wrapper } = await mountWith([KEY], [pruned]);
    expect(chip(wrapper).text()).toMatch(/^Version 61 · /);
  });

  it.each(["pictures", "own", "comfyui"])(
    "is absent, and reads nothing, for a workflow with nothing to go back to (%s)",
    async (origin) => {
      const plain = card({
        versions: 1,
        version: 1,
        version_at: null,
        origin_category: origin,
      });
      getWorkflowCard.mockResolvedValue(detail({ card: plain }));
      const { wrapper } = await mountWith([KEY], [plain]);
      expect(chip(wrapper).exists()).toBe(false);
      expect(getWorkflowVersions).not.toHaveBeenCalled();
      // The count stands as it did.
      expect(wrapper.find('[data-testid="wftab-show-pictures"]').exists()).toBe(
        true,
      );
    },
  );

  it("invents no version for an automatic workflow the read says has none", async () => {
    // The card is a read behind: the versions route is the one that knows.
    getWorkflowVersions.mockResolvedValue({
      workflow_id: KEY,
      version: null,
      versions: [],
    });
    const { wrapper } = await mountVersioned();
    expect(chip(wrapper).exists()).toBe(false);
  });

  it("is absent when the read says there is one version only", async () => {
    // Nothing to go back to, whatever the card (a read behind) counted.
    getWorkflowVersions.mockResolvedValue(versionsAt(1));
    const { wrapper } = await mountVersioned();
    expect(chip(wrapper).exists()).toBe(false);
  });

  it("lists the versions newest first, each with the server's own reason", async () => {
    const { wrapper } = await mountVersioned();
    expect(getWorkflowVersions).toHaveBeenCalledTimes(1);
    expect(getWorkflowVersions).toHaveBeenCalledWith(KEY);
    expect(rows(wrapper).map((entry) => entry.attributes("data-version"))).toEqual(
      ["3", "2", "1"],
    );
    expect(row(wrapper, 3).text()).toContain("models changed");
    expect(row(wrapper, 2).text()).toContain("LoRAs edited");
    expect(row(wrapper, 3).attributes("aria-checked")).toBe("true");
    expect(row(wrapper, 2).attributes("aria-checked")).toBe("false");
  });

  describe("a version saved from ComfyUI (#1852)", () => {
    const line = (wrapper) =>
      wrapper.find('[data-testid="wftab-from-comfyui"]');
    /** Version 3 as the newest, arrived as `source` says. */
    const kept = (source, reason = "from ComfyUI") => {
      const at = versionsAt(3);
      at.versions[0] = {
        version: 3,
        created_at: new Date().toISOString(),
        source,
        reason,
        current: true,
      };
      return at;
    };

    it("is named in its row of the menu", async () => {
      getWorkflowVersions.mockResolvedValue(kept("comfyui"));
      const { wrapper } = await mountVersioned();
      expect(row(wrapper, 3).text()).toContain("from ComfyUI");
    });

    it("is said in one line under the chip, with the way back beside it", async () => {
      getWorkflowVersions.mockResolvedValue(kept("comfyui"));
      const { wrapper } = await mountVersioned();
      expect(line(wrapper).text()).toMatch(
        /^Saved from ComfyUI just now ·\s+Go back$/,
      );

      // Go back makes the version BEFORE it current, by the menu's own write.
      restoreWorkflowVersion.mockResolvedValue(versionsAt(4));
      const back = wrapper.find('[data-testid="wftab-go-back"]');
      expect(back.attributes("aria-label")).toBe("Go back to version 2");
      await back.trigger("click");
      await flush(wrapper);
      expect(restoreWorkflowVersion).toHaveBeenCalledWith(KEY, 2);
      expect(line(wrapper).exists()).toBe(false);
    });

    it("is not said of a version saved in PixlStash", async () => {
      getWorkflowVersions.mockResolvedValue(kept("loras", "LoRAs edited"));
      const { wrapper } = await mountVersioned();
      expect(line(wrapper).exists()).toBe(false);
    });

    it("is not said of a file the old pull read, worded the same", async () => {
      getWorkflowVersions.mockResolvedValue(kept("pull", "from ComfyUI"));
      const { wrapper } = await mountVersioned();
      expect(line(wrapper).exists()).toBe(false);
    });

    it("keeps the line when going back is refused, and says why", async () => {
      getWorkflowVersions.mockResolvedValue(kept("comfyui"));
      restoreWorkflowVersion.mockRejectedValue(new Error("offline"));
      const { wrapper } = await mountVersioned();
      await wrapper.find('[data-testid="wftab-go-back"]').trigger("click");
      await flush(wrapper);
      expect(refusal(wrapper).exists()).toBe(true);
      expect(line(wrapper).exists()).toBe(true);
    });
  });

  it("goes back: the earlier graph becomes the newest version and the rail re-reads", async () => {
    const { wrapper } = await mountVersioned();
    const after = versioned(4, { version_at: "2026-10-05T12:00:00+00:00" });
    getWorkflowCard.mockClear().mockResolvedValue(detail({ card: after }));
    getLoraChain.mockClear();
    getWorkflowFormInputs.mockClear();
    listWorkflowCards.mockClear().mockResolvedValue({
      cards: [after],
      one_offs: 0,
      hidden: 0,
    });

    await row(wrapper, 2).trigger("click");
    await flush(wrapper);
    await flush(wrapper);

    expect(restoreWorkflowVersion).toHaveBeenCalledTimes(1);
    expect(restoreWorkflowVersion).toHaveBeenCalledWith(KEY, 2);
    // The head and the menu, off the answer.
    expect(chip(wrapper).text()).toMatch(/^Version 4 · /);
    expect(rows(wrapper).map((entry) => entry.attributes("data-version"))).toEqual(
      ["4", "3", "2", "1"],
    );
    expect(row(wrapper, 4).attributes("aria-checked")).toBe("true");
    expect(row(wrapper, 4).text()).toContain("went back to version 2");
    // Everything read off the graph, and the grid's card.
    expect(getWorkflowCard).toHaveBeenCalledWith(KEY);
    expect(getLoraChain).toHaveBeenCalledWith(KEY);
    expect(getWorkflowFormInputs).toHaveBeenCalledWith(KEY);
    expect(listWorkflowCards).toHaveBeenCalled();
    // The answer WAS the list at version 4: the card catching up asks nothing.
    expect(getWorkflowVersions).toHaveBeenCalledTimes(1);
    expect(refusal(wrapper).exists()).toBe(false);
  });

  it("says what it did, with an Undo that goes back to the version left", async () => {
    const { wrapper } = await mountVersioned();
    await row(wrapper, 2).trigger("click");
    await flush(wrapper);

    const notice = useNoticeStore().notices.at(-1);
    expect(notice.level).toBe("success");
    expect(notice.text).toBe(
      "Back on version 2, kept as version 4. Nothing was removed.",
    );
    expect(notice.action.label).toBe("Undo");

    restoreWorkflowVersion.mockClear();
    await notice.action.handler();
    expect(restoreWorkflowVersion).toHaveBeenCalledWith(KEY, 3);
  });

  it("remembers the version an Undo left for the workflow it undid, whatever the rail shows by then", async () => {
    const { wrapper, store } = await mountTwoRails();
    await row(wrapper, 2).trigger("click");
    await flush(wrapper);
    const first = useNoticeStore().notices.at(-1);

    // The rail moves on while the notice is still up.
    store.selectedKeys = [OTHER];
    await flush(wrapper);
    // Undo goes back to 3, which the server keeps as version 5.
    restoreWorkflowVersion.mockReset().mockResolvedValue({
      workflow_id: KEY,
      version: 5,
      versions: [
        {
          version: 5,
          created_at: "2026-10-06T12:00:00+00:00",
          reason: "went back to version 3",
          current: true,
        },
        ...versionsAt(4).versions.map((entry) => ({ ...entry, current: false })),
      ],
    });
    await first.action.handler();
    expect(restoreWorkflowVersion).toHaveBeenCalledWith(KEY, 3);

    // The workflow it undid was on 4. The 2 the rail shows is another's.
    const second = useNoticeStore().notices.at(-1);
    restoreWorkflowVersion.mockClear();
    await second.action.handler();
    expect(restoreWorkflowVersion).toHaveBeenCalledWith(KEY, 4);
  });

  it.each([
    ["lists no versions", []],
    // Rows, but none marked as the one it is on: the top one is not "left".
    ["marks no row as current", versionsAt(4).versions.map((entry) => ({ ...entry, current: false }))],
  ])("offers no Undo when the answer %s, rather than a wrong one", async (_how, versions) => {
    restoreWorkflowVersion.mockResolvedValue({
      workflow_id: KEY,
      version: 4,
      versions,
    });
    const { wrapper } = await mountVersioned();
    await row(wrapper, 2).trigger("click");
    await flush(wrapper);
    const notice = useNoticeStore().notices.at(-1);
    expect(notice.text).toContain("Back on version 2");
    expect(notice.action).toBeNull();
  });

  it("leaves a refusal on screen alone when an Undo goes back on a workflow the rail has left", async () => {
    const { wrapper, store } = await mountTwoRails();
    restoreWorkflowVersion.mockImplementation((key) =>
      key === OTHER
        ? Promise.reject({
            response: {
              status: 409,
              data: { detail: "That version cannot be read." },
            },
          })
        : Promise.resolve(versionsAt(4)),
    );
    await row(wrapper, 2).trigger("click");
    await flush(wrapper);
    const undo = useNoticeStore().notices.at(-1).action;

    store.selectedKeys = [OTHER];
    await flush(wrapper);
    await row(wrapper, 1).trigger("click");
    await flush(wrapper);
    expect(refusal(wrapper).text()).toContain("That version cannot be read.");

    await undo.handler();
    await flush(wrapper);
    expect(refusal(wrapper).text()).toContain("That version cannot be read.");
  });

  it("asks nothing first: going back is undone by going back", async () => {
    const { wrapper } = await mountVersioned();
    await row(wrapper, 1).trigger("click");
    expect(restoreWorkflowVersion).toHaveBeenCalledWith(KEY, 1);
  });

  it("does nothing on the row of the version the workflow is on", async () => {
    const { wrapper } = await mountVersioned();
    await row(wrapper, 3).trigger("click");
    await flush(wrapper);
    expect(restoreWorkflowVersion).not.toHaveBeenCalled();
  });

  it("says a refusal under the head in the server's words, and reads the list again", async () => {
    restoreWorkflowVersion.mockRejectedValue({
      response: {
        status: 409,
        data: { detail: "That is the version this workflow has." },
      },
    });
    const { wrapper } = await mountVersioned();
    getWorkflowVersions.mockClear();
    const noticesBefore = useNoticeStore().notices.length;

    await row(wrapper, 2).trigger("click");
    await flush(wrapper);

    expect(refusal(wrapper).attributes("role")).toBe("alert");
    expect(refusal(wrapper).text()).toContain(
      "That is the version this workflow has.",
    );
    // A refusal means the list on screen was stale.
    expect(getWorkflowVersions).toHaveBeenCalledWith(KEY);
    // Said once, where the owner is looking: not as a toast as well.
    expect(useNoticeStore().notices.length).toBe(noticesBefore);
    // Still on the version it had.
    expect(chip(wrapper).text()).toMatch(/^Version 3 · /);

    await wrapper
      .find('[data-testid="wftab-version-error-dismiss"]')
      .trigger("click");
    expect(refusal(wrapper).exists()).toBe(false);
  });

  it("drops a refusal when another workflow is selected", async () => {
    restoreWorkflowVersion.mockRejectedValue({
      response: { status: 404, data: { detail: "This workflow keeps no such version." } },
    });
    const other = versioned(2, { id: OTHER, name: "Other" });
    const shown = versioned(3);
    getWorkflowCard.mockImplementation((key) =>
      Promise.resolve(detail({ card: key === OTHER ? other : shown })),
    );
    getWorkflowVersions.mockImplementation((key) =>
      Promise.resolve(versionsAt(key === OTHER ? 2 : 3, key)),
    );
    const { wrapper, store } = await mountWith([KEY], [shown, other]);
    await row(wrapper, 2).trigger("click");
    await flush(wrapper);
    expect(refusal(wrapper).exists()).toBe(true);

    store.selectedKeys = [OTHER];
    await flush(wrapper);
    expect(refusal(wrapper).exists()).toBe(false);
  });

  it("never draws one workflow's versions on another", async () => {
    // A's read is still out when B is selected, and lands after B's.
    const slow = held();
    const other = versioned(2, { id: OTHER, name: "Other" });
    const shown = versioned(3);
    getWorkflowCard.mockImplementation((key) =>
      Promise.resolve(detail({ card: key === OTHER ? other : shown })),
    );
    getWorkflowVersions.mockImplementation((key) =>
      key === KEY ? slow.promise : Promise.resolve(versionsAt(2, OTHER)),
    );
    const { wrapper, store } = await mountWith([KEY], [shown, other]);

    store.selectedKeys = [OTHER];
    await flush(wrapper);
    expect(chip(wrapper).text()).toMatch(/^Version 2 · /);

    slow.release(versionsAt(3, KEY));
    await flush(wrapper);
    expect(chip(wrapper).text()).toMatch(/^Version 2 · /);
    expect(rows(wrapper).map((entry) => entry.attributes("data-version"))).toEqual(
      ["2", "1"],
    );
  });

  it("shows a newly selected workflow's own number while its versions are still out", async () => {
    // A's list has landed and is still held when B is selected.
    const other = versioned(7, { id: OTHER, name: "Other" });
    const shown = versioned(3);
    getWorkflowCard.mockImplementation((key) =>
      Promise.resolve(detail({ card: key === OTHER ? other : shown })),
    );
    getWorkflowVersions.mockImplementation((key) =>
      key === OTHER ? new Promise(() => {}) : Promise.resolve(versionsAt(3)),
    );
    const { wrapper, store } = await mountWith([KEY], [shown, other]);
    expect(rows(wrapper)).toHaveLength(3);

    store.selectedKeys = [OTHER];
    await flush(wrapper);
    expect(chip(wrapper).text()).toMatch(/^Version 7 · /);
    expect(rows(wrapper)).toHaveLength(0);
  });

  it("follows a save made elsewhere: the card's version moves, the list is read again", async () => {
    const { wrapper, store } = await mountVersioned();
    expect(getWorkflowVersions).toHaveBeenCalledTimes(1);
    getWorkflowVersions.mockResolvedValue(versionsAt(4));

    // The grid re-read that `workflows_changed` brings.
    store.cards = [versioned(4, { version_at: "2026-10-05T12:00:00+00:00" })];
    await flush(wrapper);

    expect(getWorkflowVersions).toHaveBeenCalledTimes(2);
    expect(chip(wrapper).text()).toMatch(/^Version 4 · /);
    expect(row(wrapper, 4).attributes("aria-checked")).toBe("true");
  });

  it("falls back to the card's number when the versions read fails, and asks again on open", async () => {
    getWorkflowVersions.mockRejectedValue(new Error("offline"));
    const { wrapper } = await mountVersioned();
    expect(chip(wrapper).text()).toMatch(/^Version 3 · /);
    expect(
      wrapper.find('[data-testid="wftab-version-unread"]').text(),
    ).toBe("Could not read the versions just now.");

    getWorkflowVersions.mockReset().mockResolvedValue(versionsAt(3));
    wrapper.findComponent({ name: "WorkflowVersionMenu" }).vm.$emit("open");
    await flush(wrapper);
    expect(getWorkflowVersions).toHaveBeenCalledTimes(1);
    expect(rows(wrapper)).toHaveLength(3);
  });
});

describe("the models the panel names", () => {
  it("calls the checkpoint what the SHELF calls it, not what the file is", async () => {
    // The same preference the card's generated name row was built from
    // (#1454). This panel opens from that card, so a Checkpoint line reading
    // `realvisXL_v5.safetensors` under a title reading `Krea 2` is one model
    // named twice.
    const named = card({
      models: [
        {
          name: "realvisxl_v5.safetensors",
          title: "Krea 2",
          kind: "checkpoint",
          slot_label: "m1",
        },
        // The VAE line reads the shelf too: it is the same sentence one row
        // down, and half a panel agreeing is the drift, not the fix.
        {
          name: "ae.safetensors",
          title: "Flux Autoencoder",
          kind: "vae",
          slot_label: "m2",
        },
      ],
    });
    getWorkflowCard.mockResolvedValue(detail({ card: named }));
    const { wrapper } = await mountWith([KEY], [named]);
    const text = textOf(wrapper);
    expect(text).toContain("Krea 2");
    expect(text).not.toContain("realvisxl_v5.safetensors");
    expect(text).toContain("Flux Autoencoder");
    expect(text).not.toContain("ae.safetensors");
  });

  it("names a shelf loader's checkpoint by the file its id names", async () => {
    // A `checkpoint_id` recipe model's `filename` is a shelf row id (#1721),
    // which the slot recorded too; neither reads as the number.
    const named = card({
      models: [
        {
          name: "flux 2 klein 9b",
          title: "FLUX.2 Klein 9B",
          kind: "checkpoint",
          slot_label: "m1",
          filename: "75",
        },
      ],
    });
    const shown = detail({ card: named });
    shown.card.default_recipe.models = [
      {
        address: "core:m1/checkpoint_id",
        kind: "checkpoint",
        filename: "75",
        shelf_filename: "flux-2-klein-9b-fp8.safetensors",
        provenance: "best",
      },
    ];
    getWorkflowCard.mockResolvedValue(shown);
    const { wrapper } = await mountWith([KEY], [named]);
    const text = textOf(wrapper);
    expect(text).toContain("FLUX.2 Klein 9B");
    expect(text).not.toMatch(/\b75\b/);
  });

  it("never falls back to the id when the shelf row names no file", async () => {
    // `shelf_filename: null` on a shelf loader: the row is on the shelf but
    // names no single file. The slot's title still names it; without one the
    // row says so in words.
    for (const title of ["FLUX.2 Klein 9B", null]) {
      const named = card({
        models: [
          {
            name: null,
            title,
            kind: "checkpoint",
            slot_label: "m1",
            filename: "75",
          },
        ],
      });
      const shown = detail({ card: named });
      shown.card.default_recipe.models = [
        {
          address: "core:m1/checkpoint_id",
          kind: "checkpoint",
          filename: "75",
          shelf_filename: null,
          provenance: "best",
        },
      ];
      getWorkflowCard.mockResolvedValue(shown);
      const { wrapper } = await mountWith([KEY], [named]);
      const text = textOf(wrapper);
      expect(text).not.toMatch(/\b75\b/);
      expect(text).toContain(title ?? "Unnamed shelf model");
      wrapper.unmount();
    }
  });

  /** The panel for one card and a default recipe of one checkpoint *file*. */
  async function checkpointRowFor(models, file) {
    const named = card({ models });
    const shown = detail({ card: named });
    shown.card.default_recipe.models = [
      {
        address: "core:m1/ckpt_name",
        kind: "checkpoint",
        filename: file,
        provenance: "best",
      },
    ];
    getWorkflowCard.mockResolvedValue(shown);
    const { wrapper } = await mountWith([KEY], [named]);
    return textOf(wrapper);
  }

  it("does not lend one quant build's slot to another", async () => {
    // Both derive to `flux 2 klein 9b`; only the recorded file tells them apart.
    const text = await checkpointRowFor(
      [
        {
          name: "flux 2 klein 9b",
          title: "FLUX.2 Klein 9B",
          kind: "checkpoint",
          quant: "fp8_e4m3",
          slot_label: "m1",
          filename: "flux-2-klein-9b-fp8.safetensors",
        },
      ],
      "flux-2-klein-9b-bf16.safetensors",
    );
    expect(text).toContain("flux-2-klein-9b-bf16");
    expect(text).not.toContain(".safetensors");
    expect(text).not.toContain("FLUX.2 Klein 9B");
  });

  it("does not match a slot whose name only prefixes the file", async () => {
    const text = await checkpointRowFor(
      [
        {
          name: "flux",
          title: "Flux Prefix",
          kind: "checkpoint",
          slot_label: "m0",
          filename: "flux.safetensors",
        },
        {
          name: "flux 2 klein 9b",
          title: "FLUX.2 Klein 9B",
          kind: "checkpoint",
          slot_label: "m1",
          filename: "flux-2-klein-9b.safetensors",
        },
      ],
      "flux-2-klein-9b.safetensors",
    );
    expect(text).toContain("FLUX.2 Klein 9B");
    expect(text).not.toContain("Flux Prefix");
  });

  it("says the precision the server took out of the name", async () => {
    // The panel shows `name`, which no longer carries the postfix - so without
    // the badge two quant builds of one model read identically here, which is
    // the exact failure stripping the name is allowed only because the badge
    // prevents. Asserted per line, because a badge on the wrong row would
    // satisfy a search of the whole panel.
    const quantised = card({
      models: [
        {
          name: "t5xxl",
          title: null,
          kind: "checkpoint",
          quant: "fp8_e4m3",
          slot_label: "m1",
        },
        // Nothing recorded: no badge, and NOT an empty separator.
        { name: "ae", title: null, kind: "vae", quant: null, slot_label: "m2" },
      ],
      loras: [
        {
          name: "detail",
          quant: "q4_k_m",
          slot_label: "l1",
        },
      ],
    });
    getWorkflowCard.mockResolvedValue(detail({ card: quantised }));
    const { wrapper } = await mountWith([KEY], [quantised]);
    const values = wrapper.findAll(".wftab-value").map((el) => el.text());
    expect(values.slice(0, 2)).toEqual(["t5xxl · FP8 E4M3", "ae"]);
  });

  it("falls back to the filename where the shelf has no name", async () => {
    // The ordinary case - the line must say the file, never go blank.
    const { wrapper } = await mountWith([KEY]);
    expect(textOf(wrapper)).toContain("realvisXL_v5.safetensors");
  });
});

describe("a checkpoint that will not load", () => {
  // Never recorded, forgotten with the shelf's copy, or recorded and simply
  // not installed in ComfyUI. Whatever the case, the row says WHICH file: a
  // warning that cannot say what is missing gives the reader nothing to do.
  const unnamed = card({
    models: [{ name: null, kind: "checkpoint", slot_label: "n1/ckpt_name" }],
  });
  const named = card({
    models: [
      { name: "realvisXL_v5", kind: "checkpoint", slot_label: "n1/ckpt_name" },
    ],
  });

  function missingFile(file) {
    return {
      groups: [
        {
          reasons: [
            {
              code: "missing_models",
              models: [{ file, folder: "checkpoints" }],
            },
          ],
        },
      ],
    };
  }

  /** The row's file name and the tooltip carrying the whole value. */
  function missingLine(wrapper) {
    const line = wrapper.find('[data-testid="wftab-missing-file"]');
    return {
      text: line.text().replace(/\s+/g, " "),
      tooltip: line.find("tooltip-stub").attributes("text"),
    };
  }

  it("names the file ComfyUI does not have, without its folders", async () => {
    preflightWorkflowRun.mockResolvedValue(
      missingFile("SDXL/realvisXL_v5.safetensors"),
    );
    getWorkflowCard.mockResolvedValue(detail({ card: named }));
    const { wrapper } = await mountWith([KEY], [named]);
    await settle(wrapper);
    expect(textOf(wrapper)).toContain("Checkpoint missing");
    const line = missingLine(wrapper);
    // The name as a person looks for it; the whole recorded value is the tip.
    expect(line.text).toBe("realvisXL_v5 is not installed in ComfyUI.");
    expect(line.tooltip).toBe("SDXL/realvisXL_v5.safetensors");
  });

  it("says restart ComfyUI for a shelf checkpoint its old pack cannot load", async () => {
    // #1923: the running ComfyUI-PixlStash predates the loader's shelf widget.
    preflightWorkflowRun.mockResolvedValue({
      groups: [
        {
          reasons: [
            {
              code: "pack_outdated",
              models: [
                { file: "SDXL/realvisXL_v5.safetensors", folder: "checkpoints" },
              ],
            },
          ],
        },
      ],
    });
    getWorkflowCard.mockResolvedValue(detail({ card: named }));
    const { wrapper } = await mountWith([KEY], [named]);
    await settle(wrapper);
    expect(textOf(wrapper)).toContain("Restart ComfyUI");
    expect(textOf(wrapper)).not.toContain("Checkpoint missing");
    expect(missingLine(wrapper).text).toBe(
      "realvisXL_v5 is on your model shelf, but the ComfyUI-PixlStash " +
        "running in ComfyUI is out of date and cannot load it. " +
        "Restart ComfyUI, or update the pack.",
    );
  });

  it("makes a missing checkpoint a row that needs a pick, asked for by the graph's spelling", async () => {
    // The recipe says `realvisXL_v5`, the graph loads the fp8 build of it:
    // the missing file is still the Checkpoint row's, and it is the graph's
    // spelling the server is asked about.
    preflightWorkflowRun.mockResolvedValue(
      missingFile("SDXL/realvisXL_v5_fp8.safetensors"),
    );
    getWorkflowCard.mockResolvedValue(detail({ card: named }));
    readModelSwap.mockReset().mockImplementation(
      replacementsByKind({
        checkpoint: [
          { id: 7, filename: "realvisXL_v5_bf16.safetensors", display_name: "RealVis 5" },
        ],
      }),
    );
    const { wrapper } = await mountWith([KEY], [named]);
    await settle(wrapper);

    expect(readModelSwap).toHaveBeenCalledWith(KEY, {
      replacing: "SDXL/realvisXL_v5_fp8.safetensors",
      slotKind: "checkpoint",
      cached: true,
    });
    expect(textOf(wrapper)).toContain("Checkpoint missing");
    const picker = wrapper.find('[data-testid="wftab-pick-checkpoint"] select');
    expect(picker.text()).toContain("RealVis 5");
    // Held to the missing one's base model: nothing to warn of.
    expect(wrapper.find('[data-testid="wftab-replace-unmatched"]').exists()).toBe(
      false,
    );

    await picker.setValue("realvisXL_v5_bf16.safetensors");
    await flush(wrapper);
    // A pick waits with the other changes: it is not written by itself.
    expect(setWorkflowChanges).toHaveBeenCalledTimes(1);
    expect(setWorkflowChanges.mock.calls[0][1].models).toEqual([
      {
        address: "core:n1/ckpt_name/ckpt_name",
        filename: "realvisXL_v5_bf16.safetensors",
      },
    ]);
    // Picked: the warning gives way to the saved value under the field.
    expect(textOf(wrapper)).not.toContain("Checkpoint missing");
    expect(wrapper.find('[data-testid="wftab-saved"]').text()).toContain(
      "Saved: realvisXL_v5_fp8",
    );
  });

  it("says so when the picker lists every checkpoint, none known to match", async () => {
    preflightWorkflowRun.mockResolvedValue(
      missingFile("SDXL/realvisXL_v5_fp8.safetensors"),
    );
    getWorkflowCard.mockResolvedValue(detail({ card: named }));
    readModelSwap
      .mockReset()
      .mockImplementation(
        replacementsByKind({ checkpoint: [{ id: 7, filename: "sd15.safetensors" }] }, {}, false),
      );
    const { wrapper } = await mountWith([KEY], [named]);
    await settle(wrapper);
    expect(wrapper.find('[data-testid="wftab-pick-checkpoint"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="wftab-replace-unmatched"]').text()).toContain(
      "is known to match it",
    );
  });

  it.each([
    ["none_go_with_it", "is known to work with this checkpoint"],
    ["none_loadable", "is one this loader can load"],
    ["unread", "Could not read what could replace it"],
    [null, "Nothing on your shelf can replace it."],
  ])("says why a missing checkpoint has nothing to pick (%s)", async (reason, text) => {
    preflightWorkflowRun.mockResolvedValue(
      missingFile("SDXL/realvisXL_v5_fp8.safetensors"),
    );
    getWorkflowCard.mockResolvedValue(detail({ card: named }));
    readModelSwap
      .mockReset()
      .mockImplementation(replacementsByKind({}, { checkpoint: reason }));
    const { wrapper } = await mountWith([KEY], [named]);
    await settle(wrapper);
    expect(wrapper.find('[data-testid="wftab-pick-checkpoint"]').exists()).toBe(
      false,
    );
    expect(
      wrapper.find('[data-testid="wftab-no-replacement-checkpoint"]').text(),
    ).toContain(text);
  });

  it("shows the file an earlier replacement loads, with no mark and no way back to write", async () => {
    // A replacement made before #1850 is still applied by the server. The
    // row reads as the file the workflow now loads; "Replaced" is gone.
    getWorkflowCard.mockResolvedValue(
      detail({
        card: named,
        model_fixes: [
          {
            slot_label: "n1/ckpt_name",
            was: "realvisXL_v5.safetensors",
            now: "SDXL/realvisXL_v5_bf16.safetensors",
            slot_kind: "checkpoint",
          },
        ],
      }),
    );
    const { wrapper } = await mountWith([KEY], [named]);
    await settle(wrapper);
    const row = wrapper.find('[data-testid="wftab-row-checkpoint"]');
    expect(row.find(".wftab-value").text()).toBe("realvisXL_v5_bf16");
    expect(textOf(wrapper)).not.toMatch(/Replaced|originally used/);
    expect(readModelSwap).toHaveBeenCalledWith(KEY, {
      replacing: "SDXL/realvisXL_v5_bf16.safetensors",
      slotKind: "checkpoint",
      cached: true,
    });
  });

  it("offers no replacement for a file nobody can name", async () => {
    preflightWorkflowRun.mockResolvedValue(missingFile("(forgotten model)"));
    getWorkflowCard.mockResolvedValue(
      detail({ card: unnamed, graph_base_models: [] }),
    );
    readModelSwap.mockReset().mockResolvedValue({
      checkpoints: [{ id: 7, filename: "x.safetensors" }],
    });
    const { wrapper } = await mountWith([KEY], [unnamed]);
    await settle(wrapper);
    expect(readModelSwap).not.toHaveBeenCalled();
    expect(wrapper.find('[data-testid="wftab-pick-checkpoint"]').exists()).toBe(
      false,
    );
  });

  it("names an unnamed checkpoint from the graph a run would submit", async () => {
    // The hub forgot the name; the pre-flight says "(forgotten model)", which
    // names nothing. The graph still says which file it loads.
    preflightWorkflowRun.mockResolvedValue(missingFile("(forgotten model)"));
    getWorkflowCard.mockResolvedValue(
      detail({
        card: unnamed,
        graph_base_models: ["Flux/klein-9b-fp8.safetensors"],
      }),
    );
    const { wrapper } = await mountWith([KEY], [unnamed]);
    await settle(wrapper);
    const line = missingLine(wrapper);
    expect(line.text).toBe("klein-9b-fp8 is not installed in ComfyUI.");
    expect(line.tooltip).toBe("Flux/klein-9b-fp8.safetensors");
    expect(textOf(wrapper)).not.toContain("Not recorded");
  });

  it("says so plainly when no file name was kept anywhere", async () => {
    preflightWorkflowRun.mockResolvedValue(missingFile("(forgotten model)"));
    getWorkflowCard.mockResolvedValue(
      detail({ card: unnamed, graph_base_models: [] }),
    );
    const { wrapper } = await mountWith([KEY], [unnamed]);
    await settle(wrapper);
    expect(textOf(wrapper)).toContain("Checkpoint missing");
    expect(textOf(wrapper)).toContain("No file name was kept for it anywhere.");
    expect(textOf(wrapper)).not.toContain("(forgotten model)");
  });

  it("does not call a checkpoint missing that ComfyUI has", async () => {
    // Unnamed on the card, but the pre-flight found nothing missing: it is
    // installed, and the row shows the file the graph loads.
    preflightWorkflowRun.mockResolvedValue({ groups: [] });
    getWorkflowCard.mockResolvedValue(
      detail({ card: unnamed, graph_base_models: ["SDXL/pony.safetensors"] }),
    );
    const { wrapper } = await mountWith([KEY], [unnamed]);
    await settle(wrapper);
    expect(textOf(wrapper)).not.toContain("Checkpoint missing");
    expect(textOf(wrapper)).toContain("pony");
    expect(textOf(wrapper)).not.toContain(".safetensors");
    expect(textOf(wrapper)).not.toContain("SDXL/");
  });

  it("says Checkpoints for two files named only by the graph", async () => {
    preflightWorkflowRun.mockResolvedValue({ groups: [] });
    getWorkflowCard.mockResolvedValue(
      detail({
        card: unnamed,
        graph_base_models: ["wan2.2_high.safetensors", "wan2.2_low.safetensors"],
      }),
    );
    const { wrapper } = await mountWith([KEY], [unnamed]);
    await settle(wrapper);
    const row = wrapper.find("[data-testid='wftab-row-checkpoint']");
    expect(row.find(".wftab-label").text()).toBe("Checkpoints");
    expect(textOf(wrapper)).toContain("wan2.2_high + wan2.2_low");
  });

  it("counts two gone shelf loaders as two, though they read alike", async () => {
    preflightWorkflowRun.mockResolvedValue({ groups: [] });
    const gone = "(model no longer on the shelf)";
    getWorkflowCard.mockResolvedValue(
      detail({ card: unnamed, graph_base_models: [gone, gone] }),
    );
    const { wrapper } = await mountWith([KEY], [unnamed]);
    await settle(wrapper);
    const row = wrapper.find("[data-testid='wftab-row-checkpoint']");
    expect(row.find(".wftab-label").text()).toBe("Checkpoints");
    expect(textOf(wrapper)).toContain(`${gone} + ${gone}`);
  });

  it("warns from the card alone while ComfyUI cannot be asked", async () => {
    preflightWorkflowRun.mockRejectedValue(new Error("ComfyUI is down"));
    getWorkflowCard.mockResolvedValue(
      detail({ card: unnamed, graph_base_models: ["SDXL/pony.safetensors"] }),
    );
    const { wrapper } = await mountWith([KEY], [unnamed]);
    await settle(wrapper);
    expect(textOf(wrapper)).toContain("Checkpoint missing");
    // Named, but not claimed uninstalled: nobody could ask.
    expect(missingLine(wrapper).text).toBe("pony");
  });

  it("draws no warning before the card's detail has arrived", async () => {
    getWorkflowCard.mockReturnValue(new Promise(() => {}));
    const { wrapper } = await mountWith([KEY], [unnamed]);
    expect(wrapper.find(".wftab-warn").exists()).toBe(false);
  });

  it("says None in this workflow for a graph that loads no base model", async () => {
    // An upscaler: "Not recorded" read as a gap in the records, when the
    // graph was read and simply loads none.
    const upscale = card({
      models: [
        {
          name: "4x-ultrasharp",
          kind: "upscale_model",
          slot_label: "u/model_name",
        },
      ],
    });
    getWorkflowCard.mockResolvedValue(
      detail({ card: upscale, graph_base_models: [] }),
    );
    const { wrapper } = await mountWith([KEY], [upscale]);
    await settle(wrapper);
    expect(textOf(wrapper)).toContain("None in this workflow");
    expect(textOf(wrapper)).not.toContain("Not recorded");
    expect(textOf(wrapper)).not.toContain("Checkpoint missing");
  });

  it("does not blame the checkpoint for another missing model", async () => {
    preflightWorkflowRun.mockResolvedValue({
      groups: [
        {
          reasons: [
            {
              code: "missing_models",
              models: [{ file: "other.vae.safetensors", folder: "vae" }],
            },
          ],
        },
      ],
    });
    getWorkflowCard.mockResolvedValue(detail());
    const { wrapper } = await mountWith([KEY]);
    await settle(wrapper);
    expect(preflightWorkflowRun).toHaveBeenCalledTimes(1);
    expect(textOf(wrapper)).toContain("realvisXL_v5.safetensors");
    expect(textOf(wrapper)).not.toContain("Checkpoint missing");
  });

  it("asks the moment the detail is in, for the answer the server holds", async () => {
    // No wait before the ask, and `cached`: the server answers from the
    // object_info it last read, so what the rows say settles with the rows.
    getWorkflowCard.mockResolvedValue(detail());
    await mountWith([KEY]);
    expect(preflightWorkflowRun).toHaveBeenCalledTimes(1);
    expect(preflightWorkflowRun).toHaveBeenCalledWith(
      { workflow_id: KEY, values: [] },
      { cached: true },
    );
  });

  it("asks nothing more once the rail has closed", async () => {
    let answer;
    preflightWorkflowRun.mockReturnValue(
      new Promise((resolve) => {
        answer = () => resolve({ groups: [] });
      }),
    );
    getWorkflowCard.mockResolvedValue(detail());
    const { wrapper } = await mountWith([KEY]);
    wrapper.unmount();
    mounted.length = 0;
    answer();
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(readModelSwap).not.toHaveBeenCalled();
  });

  it("does not put card A's pre-flight answer on card B", async () => {
    let answerA;
    preflightWorkflowRun.mockImplementation((body) =>
      body.workflow_id === KEY
        ? new Promise((resolve) => {
            answerA = () =>
              resolve({
                groups: [
                  {
                    reasons: [
                      {
                        code: "missing_models",
                        models: [
                          { file: "a.safetensors", folder: "checkpoints" },
                        ],
                      },
                    ],
                  },
                ],
              });
          })
        : Promise.resolve({ groups: [] }),
    );
    getWorkflowCard.mockImplementation(async (key) =>
      detail({ card: { id: key, name: key === KEY ? "Card A" : "Card B" } }),
    );
    const { wrapper, store } = await mountWith(
      [KEY],
      [card({ name: "Card A" }), card({ id: OTHER, name: "Card B" })],
    );
    await settle(wrapper);
    store.selectedKeys = [OTHER];
    await flush(wrapper);
    // A answers while B's own ask is still settling: nothing else would
    // cover the wrong answer up.
    answerA();
    await flush(wrapper);
    expect(textOf(wrapper)).toContain("Card B");
    expect(textOf(wrapper)).not.toContain("Checkpoint missing");
  });

  it("names the checkpoint plainly when the pre-flight cannot be asked", async () => {
    preflightWorkflowRun.mockRejectedValue(new Error("ComfyUI is down"));
    getWorkflowCard.mockResolvedValue(detail());
    const { wrapper } = await mountWith([KEY]);
    await settle(wrapper);
    expect(preflightWorkflowRun).toHaveBeenCalledTimes(1);
    expect(textOf(wrapper)).toContain("realvisXL_v5.safetensors");
    expect(textOf(wrapper)).not.toContain("Checkpoint missing");
  });
  it("does not call a Flux graph's unet a missing checkpoint", async () => {
    const flux = card({
      models: [{ name: "flux1-dev", kind: "unet", slot_label: "n1/unet_name" }],
    });
    getWorkflowCard.mockResolvedValue(detail({ card: flux }));
    const { wrapper } = await mountWith([KEY], [flux]);
    expect(textOf(wrapper)).toContain("flux1-dev");
    expect(textOf(wrapper)).not.toContain("Checkpoint missing");
  });
});

describe("a VAE or text encoder row (#1596, #1850)", () => {
  const withSupport = card({
    models: [
      { name: "realvisXL_v5", kind: "checkpoint", slot_label: "n1/ckpt_name" },
      { name: "sdxl_vae", kind: "vae", slot_label: "n2/vae_name" },
      { name: "t5xxl", kind: "clip", slot_label: "n3/clip_name" },
    ],
  });

  function missing(...models) {
    return {
      groups: [{ reasons: [{ code: "missing_models", models }] }],
    };
  }

  function row(wrapper, kind) {
    return wrapper.find(`[data-testid="wftab-row-${kind}"]`);
  }

  it("names the default recipe's text encoder on the Text encoder row", async () => {
    // The recipe says `text_encoder` where the card's slot says `clip`: the
    // row and its shelf name have to join the two.
    preflightWorkflowRun.mockResolvedValue({ groups: [{ reasons: [] }] });
    getWorkflowCard.mockResolvedValue(detail({ card: withSupport }));
    const { wrapper } = await mountWith([KEY], [withSupport]);
    expect(row(wrapper, "text_encoder").text()).toContain("t5xxl");
  });

  it("is a picker whether or not its file is missing, each asked about once", async () => {
    getWorkflowCard.mockResolvedValue(detail({ card: withSupport }));
    readModelSwap.mockReset().mockImplementation(
      replacementsByKind({
        vae: [{ id: 3, filename: "sdxl_vae_bf16.safetensors", via: "declared" }],
        text_encoder: [{ id: 4, filename: "t5xxl_fp16.safetensors" }],
      }),
    );
    const { wrapper } = await mountWith([KEY], [withSupport]);
    await settle(wrapper);

    // One read per row, by the file the row loads and its slot kind.
    expect(readModelSwap).toHaveBeenCalledTimes(3);
    expect(readModelSwap).toHaveBeenCalledWith(KEY, {
      replacing: "sdxl_vae.safetensors",
      slotKind: "vae",
      cached: true,
    });
    expect(textOf(wrapper)).not.toContain("VAE missing");
    const vae = row(wrapper, "vae").find('[data-testid="wftab-pick-vae"] select');
    // Never a bare filename, and untested says so.
    expect(vae.text()).toContain("sdxl_vae_bf16 (untested)");
    expect(vae.text()).not.toContain(".safetensors");
    expect(
      row(wrapper, "text_encoder")
        .find('[data-testid="wftab-pick-text_encoder"] select')
        .text(),
    ).toContain("t5xxl_fp16");
    // The checkpoint has nothing to offer here: its row stays text, unblamed.
    expect(row(wrapper, "checkpoint").find("select").exists()).toBe(false);
    expect(
      wrapper.find('[data-testid="wftab-no-replacement-checkpoint"]').exists(),
    ).toBe(false);
  });

  it("picks the shelf's likely match for a missing file, to save or take back", async () => {
    // The shelf holds the missing T5 under another name: it is picked as an
    // unsaved change, so Save uses it without the owner choosing it again.
    preflightWorkflowRun.mockResolvedValue(
      missing({ file: "t5/t5xxl_fp8.safetensors", folder: "text_encoders" }),
    );
    getWorkflowCard.mockResolvedValue(detail({ card: withSupport }));
    readModelSwap.mockReset().mockImplementation(
      replacementsByKind({
        text_encoder: [
          { id: 4, filename: "t5xxl_fp16.safetensors" },
          { id: 5, filename: "t5-xxl_fp8.safetensors", likely: true },
        ],
      }),
    );
    const { wrapper } = await mountWith([KEY], [withSupport]);
    await settle(wrapper);

    expect(setWorkflowChanges).toHaveBeenCalledTimes(1);
    const [picked] = setWorkflowChanges.mock.calls[0][1].models;
    expect(picked.filename).toBe("t5-xxl_fp8.safetensors");
    expect(picked.address).toMatch(/clip_name/);
    expect(row(wrapper, "text_encoder").text()).not.toContain(
      "Text encoder missing",
    );
    // Said as the tab's own fix, with what it did one focus away.
    const encoder = row(wrapper, "text_encoder");
    expect(encoder.find('[data-testid="wftab-edited"]').text()).toBe("Fixed");
    expect(encoder.find('[data-testid="wftab-saved"]').text()).toContain(
      "Corrected: t5xxl_fp8",
    );
    const note = encoder
      .find('[data-testid="wftab-fix-mark"]')
      .attributes("data-note");
    expect(note).toContain("t5xxl_fp8.safetensors is not installed in ComfyUI");
    expect(note).toContain("t5-xxl_fp8.safetensors");
    const count = wrapper.find('[data-testid="wftab-draft-count"]');
    expect(count.text()).toBe("1 automatic fix");

    // Taken back, it stays taken back.
    await row(wrapper, "text_encoder")
      .find('[data-testid="wftab-reset-model"]')
      .trigger("click");
    await settle(wrapper);
    expect(setWorkflowChanges).toHaveBeenCalledTimes(2);
    expect(setWorkflowChanges.mock.calls[1][1].models ?? []).toEqual([]);
    expect(row(wrapper, "text_encoder").text()).toContain("Text encoder missing");
    // Its own name is no pick: the option says the file is missing.
    expect(
      row(wrapper, "text_encoder")
        .find('[data-testid="wftab-pick-text_encoder"] select option')
        .text(),
    ).toBe("t5xxl_fp8 (missing)");
  });

  it("files the missing file's misfiled shelf copy and picks it, in one choice", async () => {
    // #1962 after #1966: the copy is on the shelf as Unclassified, so nothing
    // offers it until it is filed as a text encoder.
    preflightWorkflowRun.mockResolvedValue(
      missing({ file: "t5/t5xxl_fp8.safetensors", folder: "text_encoders" }),
    );
    getWorkflowCard.mockResolvedValue(detail({ card: withSupport }));
    const copy = {
      id: 5,
      filename: "t5-xxl_fp8.gguf",
      display_name: null,
      file_kind: "unknown",
    };
    let filed = false;
    readModelSwap.mockReset().mockImplementation((_key, { slotKind } = {}) =>
      Promise.resolve(
        slotKind !== "text_encoder"
          ? { replacements: [], replacements_reason: null }
          : filed
            ? {
                replacements: [{ id: 5, filename: copy.filename, likely: true }],
                replacements_reason: null,
                refile: null,
              }
            : {
                replacements: [],
                replacements_reason: "none_go_with_it",
                refile: copy,
              },
      ),
    );
    editModels.mockReset().mockImplementation(async () => {
      filed = true;
      return { updated: [5], fields: ["file_kind"] };
    });
    const { wrapper } = await mountWith([KEY], [withSupport]);
    await settle(wrapper);

    const encoder = row(wrapper, "text_encoder");
    // The offer, never a pick: nothing waits until the owner chooses.
    expect(setWorkflowChanges).not.toHaveBeenCalled();
    expect(textOf(encoder)).toContain("t5-xxl_fp8: Unclassified on shelf");
    expect(textOf(encoder)).toContain("Text encoder?");
    // The one note under the picker: the offer, not "Nothing to offer".
    expect(
      wrapper.find('[data-testid="wftab-no-replacement-text_encoder"]').exists(),
    ).toBe(false);

    encoder.findComponent(KindMenu).vm.$emit("pick", "text_encoder");
    await settle(wrapper);
    expect(editModels).toHaveBeenCalledWith([5], { file_kind: "text_encoder" });
    expect(setWorkflowChanges).toHaveBeenCalledTimes(1);
    const [picked] = setWorkflowChanges.mock.calls[0][1].models;
    expect(picked.filename).toBe("t5-xxl_fp8.gguf");
    expect(
      row(wrapper, "text_encoder").find('[data-testid="wftab-edited"]').text(),
    ).toBe("Fixed");
  });

  it("says which way a suggestion answer went", async () => {
    const offer = {
      address: "core:n3/clip_name/ckpt_name",
      model_id: 4,
      file_kind: "unknown",
      suggested_kind: "text_encoder",
      suggested_by: 2,
      suggested_fields: ["clip_name"],
    };
    getWorkflowCard.mockResolvedValue(
      detail({ card: withSupport, kind_offers: [offer] }),
    );
    editModels.mockReset().mockResolvedValue({ updated: [4], fields: [] });
    const { wrapper } = await mountWith([KEY], [withSupport]);
    await settle(wrapper);
    const menu = () => row(wrapper, "text_encoder").findComponent(KindMenu);

    menu().vm.$emit("suggest", false);
    await settle(wrapper);
    expect(useNoticeStore().notices.at(-1).text).toBe(
      "The shelf will not suggest a kind for it.",
    );
    menu().vm.$emit("suggest", true);
    await settle(wrapper);
    expect(useNoticeStore().notices.at(-1).text).toBe(
      "The shelf will suggest a kind for it again.",
    );
  });

  it("files the copy as another kind without picking it or saying it was", async () => {
    preflightWorkflowRun.mockResolvedValue(
      missing({ file: "t5/t5xxl_fp8.safetensors", folder: "text_encoders" }),
    );
    getWorkflowCard.mockResolvedValue(detail({ card: withSupport }));
    readModelSwap.mockReset().mockResolvedValue({
      replacements: [],
      replacements_reason: "none_go_with_it",
      refile: {
        id: 5,
        filename: "t5-xxl_fp8.gguf",
        display_name: null,
        file_kind: "unknown",
      },
    });
    editModels.mockReset().mockResolvedValue({ updated: [5], fields: ["file_kind"] });
    const { wrapper } = await mountWith([KEY], [withSupport]);
    await settle(wrapper);
    const asked = readModelSwap.mock.calls.length;

    row(wrapper, "text_encoder")
      .findComponent(KindMenu)
      .vm.$emit("pick", "checkpoint");
    await settle(wrapper);
    expect(editModels).toHaveBeenCalledWith([5], { file_kind: "checkpoint" });
    expect(readModelSwap.mock.calls.length).toBe(asked + 1);
    expect(setWorkflowChanges).not.toHaveBeenCalled();
    expect(useNoticeStore().notices.at(-1).text).toBe(
      "Filed t5-xxl_fp8 as Checkpoint on your shelf.",
    );
  });

  it("offers no renamed copy for a file ComfyUI has", async () => {
    preflightWorkflowRun.mockResolvedValue({ groups: [{ reasons: [] }] });
    getWorkflowCard.mockResolvedValue(detail({ card: withSupport }));
    readModelSwap.mockReset().mockResolvedValue({
      replacements: [],
      replacements_reason: "none_go_with_it",
      refile: {
        id: 5,
        filename: "t5-xxl_fp8.gguf",
        display_name: null,
        file_kind: "unknown",
      },
    });
    const { wrapper } = await mountWith([KEY], [withSupport]);
    await settle(wrapper);
    expect(row(wrapper, "text_encoder").findComponent(KindMenu).exists()).toBe(
      false,
    );
  });

  it("warns on the missing one's own row, and never on a vision encoder's account", async () => {
    preflightWorkflowRun.mockResolvedValue(
      missing(
        { file: "t5/t5xxl_fp8.safetensors", folder: "text_encoders" },
        { file: "clip_vision_h.safetensors", folder: "clip_vision" },
      ),
    );
    getWorkflowCard.mockResolvedValue(detail({ card: withSupport }));
    readModelSwap
      .mockReset()
      .mockImplementation(
        replacementsByKind({}, { text_encoder: "none_go_with_it" }),
      );
    const { wrapper } = await mountWith([KEY], [withSupport]);
    await settle(wrapper);
    expect(row(wrapper, "text_encoder").text()).toContain("Text encoder missing");
    expect(row(wrapper, "text_encoder").text()).toContain(
      "t5xxl_fp8 is not installed in ComfyUI.",
    );
    expect(
      wrapper.find('[data-testid="wftab-no-replacement-text_encoder"]').text(),
    ).toContain("is known to work with this checkpoint");
    expect(row(wrapper, "vae").text()).not.toContain("missing");
    // Nothing reads as the missing one renamed: nothing is picked for it.
    expect(setWorkflowChanges).not.toHaveBeenCalled();
    expect(textOf(wrapper)).not.toContain("clip_vision_h");
    expect(readModelSwap).toHaveBeenCalledWith(KEY, {
      replacing: "t5/t5xxl_fp8.safetensors",
      slotKind: "text_encoder",
      cached: true,
    });
  });

  it("offers the shelf's kind under a text encoder the shelf files as Unclassified", async () => {
    // #1962: every workflow loads it as a text encoder, the shelf does not.
    const offer = {
      address: "core:n3/clip_name/ckpt_name",
      model_id: 4,
      file_kind: "unknown",
      suggested_kind: "text_encoder",
      suggested_by: 2,
      suggested_fields: ["clip_name"],
    };
    getWorkflowCard.mockResolvedValue(
      detail({ card: withSupport, kind_offers: [offer] }),
    );
    editModels.mockReset().mockResolvedValue({ updated: [4], fields: ["file_kind"] });
    const { wrapper } = await mountWith([KEY], [withSupport]);
    await settle(wrapper);

    const encoder = row(wrapper, "text_encoder");
    // Named for what it is, as the shelf and the warning name it.
    expect(encoder.find(".wftab-label").text()).toBe("Text encoder");
    expect(textOf(encoder)).toContain("Unclassified on shelf");
    expect(textOf(encoder)).toContain("Text encoder?");
    // Only on the loader the offer names.
    expect(row(wrapper, "vae").findComponent(KindMenu).exists()).toBe(false);

    getWorkflowCard.mockClear();
    encoder.findComponent(KindMenu).vm.$emit("pick", "text_encoder");
    await settle(wrapper);
    expect(editModels).toHaveBeenCalledWith([4], { file_kind: "text_encoder" });
    // Read again, so the answered offer goes.
    expect(getWorkflowCard).toHaveBeenCalledWith(KEY);
  });

  it("keeps the offer and its focus when filing it fails", async () => {
    const offer = {
      address: "core:n3/clip_name/ckpt_name",
      model_id: 4,
      file_kind: "unknown",
      suggested_kind: "text_encoder",
      suggested_by: 2,
      suggested_fields: ["clip_name"],
    };
    getWorkflowCard.mockResolvedValue(
      detail({ card: withSupport, kind_offers: [offer] }),
    );
    editModels.mockReset().mockRejectedValue(new Error("offline"));
    const { wrapper } = await mountWith([KEY], [withSupport]);
    await settle(wrapper);
    const focus = vi.spyOn(HTMLElement.prototype, "focus");
    try {
      row(wrapper, "text_encoder")
        .findComponent(KindMenu)
        .vm.$emit("pick", "text_encoder");
      await settle(wrapper);
      expect(
        focus.mock.contexts.some((el) => el.dataset?.testid === "kind-menu"),
      ).toBe(true);
      expect(row(wrapper, "text_encoder").findComponent(KindMenu).exists()).toBe(
        true,
      );
    } finally {
      focus.mockRestore();
    }
  });

  it("says a failed read on a missing row rather than showing nothing", async () => {
    preflightWorkflowRun.mockResolvedValue(
      missing({ file: "SDXL/sdxl_vae_fp8.safetensors", folder: "vae" }),
    );
    getWorkflowCard.mockResolvedValue(detail({ card: withSupport }));
    readModelSwap.mockReset().mockRejectedValue(new Error("offline"));
    const { wrapper } = await mountWith([KEY], [withSupport]);
    await settle(wrapper);
    expect(
      wrapper.find('[data-testid="wftab-no-replacement-vae"]').text(),
    ).toContain("Could not read what could replace it");
  });

  it("still reads the pickers when ComfyUI cannot be asked what is missing", async () => {
    preflightWorkflowRun.mockRejectedValue(new Error("ComfyUI is down"));
    getWorkflowCard.mockResolvedValue(detail({ card: withSupport }));
    readModelSwap.mockReset().mockImplementation(
      replacementsByKind({
        vae: [{ id: 3, filename: "sdxl_vae_bf16.safetensors" }],
      }),
    );
    const { wrapper } = await mountWith([KEY], [withSupport]);
    await settle(wrapper);
    expect(wrapper.find('[data-testid="wftab-pick-vae"]').exists()).toBe(true);
  });
});

describe("the default recipe (#1623)", () => {
  it("names the default recipe's checkpoint, not the base card's", async () => {
    const shown = detail();
    shown.card.default_recipe.models = [
      {
        address: "core:ckpt/ckpt_name",
        kind: "checkpoint",
        filename: "SDXL/juggernautXL_v9.safetensors",
        provenance: "best",
      },
    ];
    getWorkflowCard.mockResolvedValue(shown);
    const { wrapper } = await mountWith([KEY]);
    const text = textOf(wrapper);
    expect(text).toContain("juggernautXL_v9");
    expect(text).not.toContain(".safetensors");
    expect(text).not.toContain("realvisXL_v5");
  });

  it("names both checkpoints of a two-loader workflow, once each", async () => {
    // A Wan 2.2 high + low pair, the low file on two loaders: never one of
    // them, and never which loader is high.
    const shown = detail();
    shown.card.default_recipe.models = [
      ["core:a/unet_name", "wan2.2_high.safetensors"],
      ["core:b/unet_name", "wan2.2_low.safetensors"],
      ["core:c/unet_name", "wan2.2_low.safetensors"],
    ].map(([address, filename]) => ({ address, kind: "checkpoint", filename }));
    getWorkflowCard.mockResolvedValue(shown);
    const { wrapper } = await mountWith([KEY]);
    // A row per loader, since a pick is one loader's (#1850): one label
    // over them, each named by its file.
    const rows = wrapper.findAll("[data-testid='wftab-row-checkpoint']");
    expect(rows.map((row) => row.find(".wftab-value").text())).toEqual([
      "wan2.2_high",
      "wan2.2_low",
      "wan2.2_low",
    ]);
    expect(wrapper.findAll(".wftab-label").map((label) => label.text())).toContain(
      "Checkpoints",
    );
    expect(rows[1].find(".wftab-label").exists()).toBe(false);
  });

  it("lists the default recipe's values as the defaults, not the card's", async () => {
    const shown = detail({ card: { defaults: [SAMPLER] } });
    shown.card.default_recipe.values = [STEPS];
    getWorkflowCard.mockResolvedValue(shown);
    const { wrapper } = await mountWith([KEY]);
    expect(rowNamed(wrapper, "steps").exists()).toBe(true);
    expect(wrapper.findAll(".wfdef").length).toBe(1);
  });
});

describe("the DEFAULT RECIPE section (#1653)", () => {
  // Shelf CONTENT digests: deliberately not the hex of the `asset:` refs
  // (BO / ADA hash the NAME), which is the join a real library needs.
  const BO_SHA = "b".repeat(64);
  const ADA_SHA = "a".repeat(64);

  /** The detail with `loras` in its default recipe and `recipe_values` on the card. */
  function withRecipe({ loras = [], stages, recipeValues, provenance } = {}) {
    const shown = detail({
      card: recipeValues ? { recipe_values: recipeValues } : {},
    });
    shown.card.default_recipe.loras = loras;
    if (stages) shown.card.default_recipe.stages = stages;
    if (provenance) {
      shown.card.default_recipe.models.forEach((model) => {
        model.provenance = provenance;
      });
    }
    return shown;
  }

  function loraRow(wrapper, name) {
    const row = wrapper
      .findAll("[data-testid='wftab-default-lora']")
      .find((entry) => entry.find(".wftab-chain-name").text() === name);
    if (!row) throw new Error(`no default LoRA row called ${name}`);
    return row;
  }

  it("draws Models, LoRAs and Parameters as panels, each verb in its own head", async () => {
    const { wrapper } = await mountWith([KEY]);
    const heads = wrapper.findAll(".wftab-panel > .wftab-sec-head");
    expect(heads.map((head) => head.find("[role='heading']").text())).toEqual([
      "Models",
      "LoRAs",
      "Parameters",
    ]);
    expect(heads[0].find("[data-testid='wftab-use-set']").text()).toBe(
      "Use a set…",
    );
    expect(heads[1].find("[data-testid='wftab-edit-loras']").exists()).toBe(true);
    expect(textOf(wrapper)).not.toContain("In every picture");
  });

  it("draws its star ratings in the head, the 4★+ bars the defaults come from at full ink", async () => {
    const counts = [0, 2, 5, 8, 4];
    const { wrapper } = await mountWith([KEY], [card({ rating_counts: counts })]);
    const chart = wrapper.find(".wftab-head [data-testid='wftab-stars']");
    expect(chart.attributes("aria-label")).toBe(
      "Ratings: 1 star 0, 2 stars 2, 3 stars 5, 4 stars 8, 5 stars 4. " +
        "The defaults come from its pictures rated 4 stars or more.",
    );
    const bars = chart.findAll("rect");
    expect(bars.map((bar) => bar.classes()[0])).toEqual([
      "wfstars-lo",
      "wfstars-lo",
      "wfstars-lo",
      "wfstars-hi",
      "wfstars-hi",
    ]);
    // Scaled to the tallest; an empty rating keeps a 1px stub.
    expect(bars.map((bar) => bar.attributes("height"))).toEqual([
      "1",
      "4",
      "10",
      "16",
      "8",
    ]);
  });

  it("claims no source for a default recipe nobody sampled", async () => {
    const own = detail();
    own.card.default_recipe.sampled = 0;
    getWorkflowCard.mockResolvedValue(own);
    const { wrapper } = await mountWith([KEY], [card({ rating_counts: [0, 0, 1, 2, 0] })]);
    const chart = wrapper.find("[data-testid='wftab-stars']");
    expect(chart.attributes("aria-label")).toBe(
      "Ratings: 1 star 0, 2 stars 0, 3 stars 1, 4 stars 2, 5 stars 0.",
    );
    expect(chart.findAll("rect.wfstars-lo")).toHaveLength(0);
  });

  it("claims no source for a manual workflow, whose one sample is its own document", async () => {
    const own = detail({ card: { manual: true } });
    own.card.default_recipe.sampled = 1;
    getWorkflowCard.mockResolvedValue(own);
    const { wrapper } = await mountWith(
      [KEY],
      [card({ manual: true, rating_counts: [0, 0, 0, 3, 1] })],
    );
    const chart = wrapper.find("[data-testid='wftab-stars']");
    expect(chart.attributes("aria-label")).not.toContain("defaults");
    expect(chart.findAll("rect.wfstars-lo")).toHaveLength(0);
  });

  it("inks every bar when none is rated 4★ yet, and draws no chart when nothing is rated", async () => {
    const { wrapper } = await mountWith([KEY], [card({ rating_counts: [1, 3, 0, 0, 0] })]);
    const chart = wrapper.find("[data-testid='wftab-stars']");
    expect(chart.attributes("aria-label")).toContain(
      "None is rated 4 stars yet, so the defaults come from all its pictures.",
    );
    expect(chart.findAll("rect.wfstars-lo")).toHaveLength(0);

    const unrated = await mountWith([KEY], [card({ rating_counts: [0, 0, 0, 0, 0] })]);
    expect(unrated.wrapper.find("[data-testid='wftab-stars']").exists()).toBe(false);
    expect(textOf(unrated.wrapper)).not.toContain("Star ratings");
  });

  it("reads its default recipe before naming any model, never the card's", async () => {
    getWorkflowCard.mockReturnValue(new Promise(() => {}));
    const { wrapper } = await mountWith([KEY]);
    expect(textOf(wrapper)).toContain("Reading its default recipe…");
    expect(wrapper.find("[data-testid='wftab-row-checkpoint']").exists()).toBe(false);
  });

  it("offers Retry when the default recipe could not be read", async () => {
    getWorkflowCard.mockRejectedValueOnce(new Error("down"));
    const { wrapper } = await mountWith([KEY]);
    expect(textOf(wrapper)).toContain("Could not read its default recipe just now.");
    await wrapper.find("[data-testid='wftab-recipe-retry']").trigger("click");
    await flush(wrapper);
    expect(getWorkflowCard).toHaveBeenCalledTimes(2);
    expect(wrapper.find("[data-testid='wftab-row-checkpoint']").exists()).toBe(true);
  });

  it("counts a default LoRA not in every picture, and takes it out of the pile", async () => {
    getWorkflowCard.mockResolvedValue(
      withRecipe({
        // Named differently from the summary's file: the join is the digest.
        loras: [{ asset: BO.toUpperCase(), filename: "loras/bo_v2.safetensors", sha256: BO_SHA.toUpperCase(), strength: 0.8, provenance: "best" }],
      }),
    );
    const { wrapper } = await mountWith([KEY]);
    const row = loraRow(wrapper, "Bo");
    expect(row.find(".wftab-coverage").text()).toBe("in 3 of 5");
    expect(row.find(".wftab-chain-strength").text()).toBe("0.80");
    // Once: a default row or the pile, never both.
    expect(wrapper.find(`[data-testid='wftab-fan-row-${BO}']`).exists()).toBe(false);
    const pile = wrapper.find("[data-testid='wftab-pile']");
    expect(pile.text()).toContain("Ada");
    expect(pile.text()).not.toContain("+1");
    // The pile sits in the LoRAs panel, under the default rows.
    const pileRow = wrapper.find("[data-testid='wftab-loras'] [data-testid='wftab-changes']");
    expect(pileRow.find(".wftab-label").text()).toBe("Also used");
  });

  it("piles a LoRA in every picture that the default leaves out (a person's)", async () => {
    // In every picture, yet not a default: the workflow runs without a
    // person's LoRA. It must still be listed, or it could not be added back.
    const cy = `asset:${"3".repeat(64)}`;
    // Bo is a default, so the pile is as long as `varying` and only its
    // members say the "No LoRA" count no longer answers for it.
    getWorkflowCard.mockResolvedValue(
      withRecipe({
        loras: [{ asset: BO, filename: "bo.safetensors", sha256: BO_SHA, strength: 0.8, provenance: "best" }],
      }),
    );
    getLoraSummary.mockResolvedValue(
      loraSummary({
        without: 4,
        shared: [
          { asset: cy, filename: "cy.safetensors", name: "Cy", on_shelf: true, sha256: "c".repeat(64), pictures: 5, picture_ids: [31] },
        ],
      }),
    );
    const { wrapper } = await mountWith([KEY]);
    expect(wrapper.vm.alsoUsed.map((use) => use.name)).toEqual(["Cy", "Ada"]);
    expect(wrapper.find("[data-testid='wftab-pile']").text()).toContain("+1");
    // "No LoRA" was counted against the varying ones alone.
    expect(wrapper.vm.pileSummary.without).toBe(null);
  });

  it("never joins by filename: a same-named LoRA with another digest stays in the pile", async () => {
    getWorkflowCard.mockResolvedValue(
      withRecipe({
        loras: [{ asset: `asset:${"f".repeat(64)}`, filename: "bo.safetensors", sha256: "f".repeat(64), strength: 1, provenance: "best" }],
      }),
    );
    const { wrapper } = await mountWith([KEY]);
    expect(wrapper.find(`[data-testid='wftab-fan-row-${BO}']`).exists()).toBe(true);
    expect(wrapper.find(".wftab-coverage").exists()).toBe(false);
  });

  it("drops the pile's No LoRA row once a default LoRA leaves the pile", async () => {
    const none = { asset: "", filename: null, name: null, on_shelf: true, pictures: 1, picture_ids: [9] };
    getLoraSummary.mockResolvedValue(loraSummary({ without: none }));
    getWorkflowCard.mockResolvedValue(
      withRecipe({ loras: [{ asset: BO, filename: "bo.safetensors", sha256: BO_SHA, strength: 1, provenance: "best" }] }),
    );
    const { wrapper } = await mountWith([KEY]);
    expect(wrapper.findComponent({ name: "WorkflowLoraPile" }).props("summary").without).toBe(null);
  });

  it("keeps the No LoRA row while the pile is the server's whole varying", async () => {
    const none = { asset: "", filename: null, name: null, on_shelf: true, pictures: 1, picture_ids: [9] };
    getLoraSummary.mockResolvedValue(loraSummary({ without: none }));
    getWorkflowCard.mockResolvedValue(withRecipe({ loras: [] }));
    const { wrapper } = await mountWith([KEY]);
    expect(wrapper.findComponent({ name: "WorkflowLoraPile" }).props("summary").without).toEqual(none);
  });

  it("matches a default LoRA with no digest to the pile by its file", async () => {
    getWorkflowCard.mockResolvedValue(
      withRecipe({
        loras: [{ filename: "loras/BO.safetensors", sha256: null, strength: 1, provenance: "best" }],
      }),
    );
    const { wrapper } = await mountWith([KEY]);
    expect(wrapper.find(`[data-testid='wftab-fan-row-${BO}']`).exists()).toBe(false);
    expect(wrapper.find("[data-testid='wftab-pile']").text()).toContain("Ada");
  });

  describe("Add to default on a pile row (#1653)", () => {
    it("takes a default LoRA out of the pile by shelf file when its asset differs", async () => {
      // One file loaded under another name: the default names it by one
      // asset, the pile by another, and they are still the one LoRA.
      getWorkflowCard.mockResolvedValue(
        withRecipe({
          loras: [{ asset: `asset:${"9".repeat(64)}`, filename: "bo_old.safetensors", sha256: BO_SHA, strength: 1, provenance: "best" }],
        }),
      );
      const { wrapper } = await mountWith([KEY]);
      expect(wrapper.find(`[data-testid='wftab-fan-row-${BO}']`).exists()).toBe(false);
      expect(loraRow(wrapper, "Bo").find(".wftab-coverage").text()).toBe("in 3 of 5");
    });


    const boAdded = (strength = 0.8) =>
      withRecipe({
        loras: [{ asset: BO, filename: "bo.safetensors", sha256: BO_SHA, strength, provenance: "edited" }],
      });
    const addButton = (wrapper, asset) =>
      wrapper.find(`[data-testid='wftab-fan-row-${asset}'] [data-testid='wftab-add-default']`);

    it("moves the LoRA from the pile into the default recipe, and Undo takes it out", async () => {
      getWorkflowCard.mockResolvedValue(withRecipe({ loras: [] }));
      setWorkflowDefaultLora.mockResolvedValueOnce(boAdded());
      const { wrapper } = await mountWith([KEY]);
      await addButton(wrapper, BO).trigger("click");
      await flush(wrapper);
      expect(setWorkflowDefaultLora).toHaveBeenCalledWith(KEY, { asset: BO, include: true });
      expect(wrapper.find(`[data-testid='wftab-fan-row-${BO}']`).exists()).toBe(false);
      expect(loraRow(wrapper, "Bo").text()).toContain("Yours");

      const notice = useNoticeStore().notices.at(-1);
      expect(notice.level).toBe("success");
      expect(notice.text).toContain("Your pictures stay where they are");
      getWorkflowCard.mockResolvedValue(boAdded());
      setWorkflowDefaultLora.mockResolvedValueOnce(withRecipe({ loras: [] }));
      await notice.action.handler();
      await flush(wrapper);
      expect(setWorkflowDefaultLora).toHaveBeenLastCalledWith(KEY, { sha256: BO_SHA, include: null });
      expect(wrapper.find(`[data-testid='wftab-fan-row-${BO}']`).exists()).toBe(true);
    });

    it("still confirms, with Undo, when the selection moved while it was out", async () => {
      getWorkflowCard.mockResolvedValue(withRecipe({ loras: [] }));
      let answer;
      setWorkflowDefaultLora.mockImplementationOnce(
        () => new Promise((resolve) => (answer = resolve)),
      );
      const { wrapper, store } = await mountWith([KEY]);
      await addButton(wrapper, BO).trigger("click");
      await flush(wrapper);
      store.selectedKeys = [];
      answer(boAdded());
      await flush(wrapper);
      const notice = useNoticeStore().notices.at(-1);
      expect(notice.level).toBe("success");
      expect(notice.action.label).toBe("Undo");
    });

    it("does not undo a LoRA whose strength was changed since", async () => {
      getWorkflowCard.mockResolvedValue(withRecipe({ loras: [] }));
      setWorkflowDefaultLora.mockResolvedValueOnce(boAdded());
      const { wrapper } = await mountWith([KEY]);
      await addButton(wrapper, BO).trigger("click");
      await flush(wrapper);
      getWorkflowCard.mockResolvedValue(boAdded(0.5));
      await useNoticeStore().notices.at(-1).action.handler();
      expect(setWorkflowDefaultLora).toHaveBeenCalledTimes(1);
      expect(useNoticeStore().notices.at(-1).text).toContain("Nothing to undo");
    });

    it("offers it only on a LoRA the shelf names as one file", async () => {
      getWorkflowCard.mockResolvedValue(withRecipe({ loras: [] }));
      const summary = loraSummary();
      // On the shelf, but under two files: the route could not choose.
      summary.varying[1].sha256 = null;
      getLoraSummary.mockResolvedValue(summary);
      const { wrapper } = await mountWith([KEY]);
      expect(addButton(wrapper, summary.varying[0].asset).exists()).toBe(true);
      expect(addButton(wrapper, summary.varying[1].asset).exists()).toBe(false);
    });
  });

  it("has no ALSO USED section when every LoRA that changes is a default", async () => {
    getWorkflowCard.mockResolvedValue(
      withRecipe({
        loras: [
          { asset: BO, filename: "bo.safetensors", sha256: BO_SHA, strength: 1, provenance: "best" },
          { asset: ADA, filename: "ada.safetensors", sha256: ADA_SHA, strength: 1, provenance: "best" },
        ],
      }),
    );
    const { wrapper } = await mountWith([KEY]);
    expect(wrapper.find("[data-testid='wftab-changes']").exists()).toBe(false);
  });

  it("marks an edited LoRA Yours, with no reset and no Make default", async () => {
    getWorkflowCard.mockResolvedValue(
      withRecipe({
        loras: [
          { asset: BO, filename: "bo.safetensors", sha256: BO_SHA, strength: 1, provenance: "edited" },
          { asset: ADA, filename: "ada.safetensors", sha256: ADA_SHA, strength: 1, provenance: "best" },
        ],
      }),
    );
    const { wrapper } = await mountWith([KEY]);
    expect(loraRow(wrapper, "Bo").find(".wftab-yours").text()).toBe("Yours");
    // Saved, so without the teal dot of what is not.
    expect(loraRow(wrapper, "Bo").find(".wftab-yours").classes()).toEqual([
      "wftab-yours",
    ]);
    expect(loraRow(wrapper, "Ada").find(".wftab-yours").exists()).toBe(false);
    // LoRAs have no write yet (F-1): nothing on screen may pretend otherwise.
    expect(loraRow(wrapper, "Bo").find(".wfdef-reset").exists()).toBe(false);
    expect(textOf(wrapper)).not.toContain("Make default");
    expect(textOf(wrapper)).not.toContain("Take out of default");
  });

  it("says a stage is off, and why, and draws no Stages row without stages", async () => {
    getWorkflowCard.mockResolvedValue(
      withRecipe({ stages: { upscale: true, face_detailer: false } }),
    );
    const { wrapper } = await mountWith([KEY]);
    const stages = wrapper.find("[data-testid='wftab-stages']");
    expect(stages.text().replace(/\s+/g, " ")).toBe(
      "Stages Upscale Face detailer",
    );
    expect(
      stages.findAll("input[type='checkbox']").map((box) => box.element.checked),
    ).toEqual([true, false]);
    expect(textOf(wrapper)).toContain("Off: most of its pictures ran without it.");

    getWorkflowCard.mockResolvedValue(withRecipe({ stages: {} }));
    const { wrapper: none } = await mountWith([OTHER], [card({ id: OTHER })]);
    expect(none.find("[data-testid='wftab-stages']").exists()).toBe(false);
    expect(textOf(none)).not.toContain("Off: most of its pictures");
  });

  it("says which kind of upscale a stage is, in a tip on its name", async () => {
    const shown = withRecipe({ stages: { upscale: true, intermediate_save: true } });
    shown.card.default_recipe.stage_details = { upscale: "Upscale model (4x-UltraSharp)" };
    getWorkflowCard.mockResolvedValue(shown);
    const { wrapper } = await mountWith([KEY]);
    const stage = wrapper
      .find("[data-testid='wftab-stage-upscale']")
      .element.closest("label");
    expect(stage.textContent.trim()).toBe("Upscale");
    expect(
      stage.querySelector("tooltip-stub").getAttribute("text"),
    ).toBe("Upscale model (4x-UltraSharp)");
    // A stage that is only ever one thing carries no tip, and is named as
    // the Run popup names it.
    const plain = wrapper
      .find("[data-testid='wftab-stage-intermediate_save']")
      .element.closest("label");
    expect(plain.querySelector("tooltip-stub")).toBe(null);
    expect(
      wrapper.find("[data-testid='wftab-stages']").text().replace(/\s+/g, " "),
    ).toBe(`Stages Upscale Intermediate save`);
  });

  it("offers Show N on the checkpoint only below the workflow's count", async () => {
    const values = (pictures) => ({
      checkpoints: [
        { name: "SDXL/realvisXL_v5.safetensors.safetensors", pictures },
        { name: "SDXL/juggernautXL_v9.safetensors", pictures: 6 },
      ],
      loras: [],
    });
    getWorkflowCard.mockResolvedValue(withRecipe());
    const { wrapper } = await mountWith([KEY], [card({ recipe_values: values(178) })]);
    const show = wrapper.find("[data-testid='wftab-show-checkpoint']");
    expect(show.text()).toBe("Show 178");
    expect(show.attributes("aria-label")).toBe(
      "Show the 178 pictures in Cinematic portrait made with realvisXL_v5.safetensors",
    );
    await show.trigger("click");
    const filters = useFilterStore();
    expect(filters.workflowFilter).toEqual([
      {
        id: KEY,
        name: "Cinematic portrait",
        opened: {
          kind: "model",
          value: "SDXL/realvisXL_v5.safetensors.safetensors",
          label: "realvisXL_v5.safetensors",
        },
      },
    ]);
    expect(filters.comfyuiModelFilter).toEqual(["SDXL/realvisXL_v5.safetensors.safetensors"]);
    expect(push).toHaveBeenCalledWith("/");

    // In every picture: it would only repeat the head's link.
    const { wrapper: every } = await mountWith([KEY], [card({ recipe_values: values(184) })]);
    expect(every.find("[data-testid='wftab-show-checkpoint']").exists()).toBe(false);
  });

  it("lists no other checkpoint that is in every picture", async () => {
    getWorkflowCard.mockResolvedValue(withRecipe());
    const { wrapper } = await mountWith(
      [KEY],
      [
        card({
          recipe_values: {
            // The default's file spelled so it matches no entry here.
            checkpoints: [{ name: "SDXL/juggernautXL_v9.safetensors", pictures: 184 }],
            loras: [],
          },
        }),
      ],
    );
    expect(wrapper.find("[data-testid='wftab-other-checkpoints']").exists()).toBe(false);
  });

  it("discloses the other checkpoints, each with its own Show N", async () => {
    getWorkflowCard.mockResolvedValue(withRecipe());
    const { wrapper } = await mountWith(
      [KEY],
      [
        card({
          recipe_values: {
            checkpoints: [
              { name: "realvisXL_v5.safetensors.safetensors", pictures: 178 },
              { name: "SDXL/juggernautXL_v9.safetensors", pictures: 6 },
            ],
            loras: [],
          },
        }),
      ],
    );
    const toggle = wrapper.find("[data-testid='wftab-other-checkpoints']");
    expect(toggle.text()).toContain("+1 other");
    expect(toggle.attributes("aria-expanded")).toBe("false");
    expect(wrapper.find("#wftab-other-checkpoints").exists()).toBe(false);
    await toggle.trigger("click");
    expect(toggle.attributes("aria-expanded")).toBe("true");
    const item = wrapper.find("#wftab-other-checkpoints li");
    expect(item.text().replace(/\s+/g, " ")).toBe("juggernautXL_v9 Show 6");
    await item.find("button").trigger("click");
    expect(useFilterStore().comfyuiModelFilter).toEqual(["SDXL/juggernautXL_v9.safetensors"]);
  });

  it("marks the Checkpoints row Yours when only the second file was set", async () => {
    const shown = withRecipe();
    shown.card.default_recipe.models = [
      ["core:a/unet_name", "wan2.2_high.safetensors", "best"],
      ["core:b/unet_name", "wan2.2_low.safetensors", "edited"],
    ].map(([address, filename, provenance]) => ({
      address,
      kind: "checkpoint",
      filename,
      provenance,
    }));
    getWorkflowCard.mockResolvedValue(shown);
    const { wrapper } = await mountWith([KEY]);
    // Per loader now: the mark is on the row of the file that was set.
    const rows = wrapper.findAll("[data-testid='wftab-row-checkpoint']");
    expect(rows.map((row) => row.find(".wftab-yours").exists())).toEqual([
      false,
      true,
    ]);
  });

  it("lists neither of a two-loader pair as another checkpoint, and shows each", async () => {
    // A Wan 2.2 high + low pair: the low file is the workflow's own, not an
    // alternative its pictures used.
    const shown = withRecipe();
    shown.card.default_recipe.models = [
      ["core:a/unet_name", "wan2.2_high.safetensors"],
      ["core:b/unet_name", "wan2.2_low.safetensors"],
    ].map(([address, filename]) => ({ address, kind: "checkpoint", filename }));
    getWorkflowCard.mockResolvedValue(shown);
    const { wrapper } = await mountWith(
      [KEY],
      [
        card({
          recipe_values: {
            checkpoints: [
              { name: "wan/wan2.2_high.safetensors", pictures: 170 },
              { name: "wan/wan2.2_low.safetensors", pictures: 160 },
              { name: "wan/wan2.1.safetensors", pictures: 6 },
            ],
            loras: [],
          },
        }),
      ],
    );
    const toggle = wrapper.find("[data-testid='wftab-other-checkpoints']");
    expect(toggle.text()).toContain("+1 other");
    await toggle.trigger("click");
    expect(
      wrapper.findAll("#wftab-other-checkpoints li").map((li) => li.text().replace(/\s+/g, " ")),
    ).toEqual(["wan2.1 Show 6"]);
    const shows = wrapper.findAll("[data-testid='wftab-show-checkpoint']");
    expect(shows.map((button) => button.text())).toEqual(["Show 170", "Show 160"]);
    await shows[1].trigger("click");
    expect(useFilterStore().comfyuiModelFilter).toEqual(["wan/wan2.2_low.safetensors"]);
  });

  it("offers Show N on a default LoRA from recipe_values, matched by file", async () => {
    getWorkflowCard.mockResolvedValue(
      withRecipe({
        loras: [{ asset: BO, filename: "loras/bo.safetensors", sha256: BO_SHA, strength: 1, provenance: "best" }],
      }),
    );
    const { wrapper } = await mountWith(
      [KEY],
      [card({ recipe_values: { checkpoints: [], loras: [{ name: "Loras\\BO.safetensors", pictures: 3 }] } })],
    );
    const show = loraRow(wrapper, "Bo").findAll("button").find((b) => b.text() === "Show 3");
    await show.trigger("click");
    const filters = useFilterStore();
    expect(filters.comfyuiLoraFilter).toEqual(["Loras\\BO.safetensors"]);
    expect(filters.workflowFilter[0].opened).toEqual({
      kind: "lora",
      value: "Loras\\BO.safetensors",
      label: "Bo",
    });
  });
});

describe("a default's provenance and reset", () => {
  it("marks only the edited value, as Yours, and no computed one (#1653)", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, CFG] } }),
    );
    const { wrapper } = await mountWith([KEY]);
    // Paired to the row, not counted across the panel: one "Yours"
    // SOMEWHERE is also true when the condition is inverted.
    expect(rowNamed(wrapper, "cfg").find(".wfdef-prov").text()).toBe("Yours");
    expect(rowNamed(wrapper, "steps").find(".wfdef-prov").exists()).toBe(false);
    expect(textOf(wrapper)).not.toContain("from your best pictures");
  });

  it("takes the defaults the Recipes tab wrote, so its own next PUT keeps them", async () => {
    getWorkflowCard.mockResolvedValue(detail({ card: { defaults: [STEPS, CFG] } }));
    const { wrapper } = await mountWith([KEY]);
    const tabButton = (name) =>
      wrapper.findAll("button").find((b) => b.text().trim() === name);
    await tabButton("Recipes").trigger("click");
    await flush(wrapper);
    // "Make these the defaults" made steps an edit.
    const written = detail({
      card: { defaults: [{ ...STEPS, value: "30", provenance: "edited" }, CFG] },
    });
    wrapper.findComponent({ name: "WorkflowRecipesTab" }).vm.$emit("defaults-changed", KEY, written);
    await tabButton("Workflow").trigger("click");
    await flush(wrapper);
    await rowNamed(wrapper, "cfg").find(".wfdef-reset").trigger("click");
    await flush(wrapper);
    expect(setWorkflowDefaults).toHaveBeenLastCalledWith(KEY, [
      { slot_label: "slot-a", input_name: "steps", value: "30" },
    ]);
  });

  it("keeps the Recipes tab's answer over a detail read that lands after it", async () => {
    const { wrapper } = await mountWith([KEY]);
    // A re-read (Retry, a reselect) goes out and is slow ...
    let late;
    getWorkflowCard.mockImplementationOnce(() => new Promise((resolve) => (late = resolve)));
    const tabButton = (name) =>
      wrapper.findAll("button").find((b) => b.text().trim() === name);
    useWorkflowsStore().selectedKeys = [];
    await flush(wrapper);
    useWorkflowsStore().selectedKeys = [KEY];
    await flush(wrapper);
    await tabButton("Recipes").trigger("click");
    await flush(wrapper);
    // ... the Recipes tab writes, and its answer arrives first ...
    const written = detail({
      card: { defaults: [{ ...STEPS, value: "30", provenance: "edited" }, CFG] },
    });
    wrapper.findComponent({ name: "WorkflowRecipesTab" }).vm.$emit("defaults-changed", KEY, written);
    // ... then the stale read lands.
    late(detail({ card: { defaults: [STEPS, CFG] } }));
    await flush(wrapper);
    await tabButton("Workflow").trigger("click");
    await flush(wrapper);
    await rowNamed(wrapper, "cfg").find(".wfdef-reset").trigger("click");
    await flush(wrapper);
    expect(setWorkflowDefaults).toHaveBeenLastCalledWith(KEY, [
      { slot_label: "slot-a", input_name: "steps", value: "30" },
    ]);
  });

  it("offers the reset only on a value the owner edited", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, CFG] } }),
    );
    const { wrapper } = await mountWith([KEY]);
    // WHICH row, not how many: one reset among two rows is equally true of
    // the inverted condition.
    expect(rowNamed(wrapper, "cfg").find(".wfdef-reset").exists()).toBe(true);
    expect(rowNamed(wrapper, "steps").find(".wfdef-reset").exists()).toBe(
      false,
    );
  });

  it("resets one value by writing back every other edited one", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, CFG, WIDTH] } }),
    );
    const { wrapper } = await mountWith([KEY]);
    // `cfg` is the first edited row; resetting it must leave `width` set.
    await wrapper.findAll(".wfdef-reset")[0].trigger("click");
    await flush(wrapper);
    expect(setWorkflowDefaults).toHaveBeenCalledWith(KEY, [
      { slot_label: "slot-a", input_name: "width", value: "832" },
    ]);
  });

  it("edits a value set each run, keeping the other edits, and never a fixed one", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [{ ...STEPS, value: 8 }, CFG, SAMPLER] } }),
    );
    const { wrapper } = await mountWith([KEY]);
    expect(
      rowNamed(wrapper, "sampler_name").find("[data-testid='wfdef-input']").exists(),
    ).toBe(false);
    const input = rowNamed(wrapper, "steps").find("[data-testid='wfdef-input']");
    // Not a number: put back, nothing written.
    await input.setValue("twelve");
    await flush(wrapper);
    expect(setWorkflowDefaults).not.toHaveBeenCalled();
    expect(input.element.value).toBe("8");
    await input.setValue("12");
    await flush(wrapper);
    expect(setWorkflowDefaults).toHaveBeenLastCalledWith(KEY, [
      { slot_label: "slot-a", input_name: "cfg", value: "7.5" },
      { slot_label: "slot-a", input_name: "steps", value: 12 },
    ]);
  });

  it("puts the stored value back in the box when the edit is not saved", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [{ ...STEPS, value: 8 }] } }),
    );
    setWorkflowDefaults.mockRejectedValue(new Error("refused"));
    const { wrapper } = await mountWith([KEY]);
    const input = rowNamed(wrapper, "steps").find("[data-testid='wfdef-input']");
    await input.setValue("12");
    await flush(wrapper);
    await flush(wrapper);
    expect(setWorkflowDefaults).toHaveBeenCalled();
    expect(input.element.value).toBe("8");
  });

  it("sets the design's numbers each run and fixes the rest when the card has no choice", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, SAMPLER] } }),
    );
    const { wrapper } = await mountWith([KEY]);
    // WHICH group each row is in. Counting pressed locks is equally true
    // when the two groups are swapped, and that swap is the whole of what
    // the lock does.
    expect(inFixedGroup(wrapper, "steps")).toBe(false);
    expect(inFixedGroup(wrapper, "sampler_name")).toBe(true);
    expect(lockOf(wrapper, "sampler_name").attributes("aria-pressed")).toBe("true");
    expect(lockOf(wrapper, "steps").attributes("aria-pressed")).toBe("false");
    expect(textOf(wrapper)).toContain("Set each run");
    expect(textOf(wrapper)).toContain("Pinned 1");
    expect(textOf(wrapper)).toContain("Pinned values are used by every run.");
    expect(textOf(wrapper)).not.toContain("parameters");
  });

  it("keeps an empty pin list apart from no choice at all", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS] }, pins: [] }),
    );
    const { wrapper } = await mountWith([KEY]);
    expect(wrapper.findAll('.wfdef [aria-pressed="false"]')).toHaveLength(0);
    // Nothing set each run means every row is fixed, and the panel must not
    // simply be empty.
    expect(inFixedGroup(wrapper, "steps")).toBe(true);
    expect(textOf(wrapper)).not.toContain("Set each run");
  });

  it("says nothing about fixed values when nothing is fixed", async () => {
    getWorkflowCard.mockResolvedValue(detail({ card: { defaults: [STEPS] } }));
    const { wrapper } = await mountWith([KEY]);
    expect(wrapper.find("[data-testid='wftab-fixed-group']").exists()).toBe(false);
    expect(textOf(wrapper)).not.toContain("Pinned values are used by every run.");
  });

  it("folds more than three fixed rows behind their count", async () => {
    const fixed = ["sampler_name", "scheduler", "denoise", "seed"].map((name) => ({
      ...SAMPLER,
      label: name,
      input_name: name,
    }));
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, ...fixed] } }),
    );
    const { wrapper } = await mountWith([KEY]);
    const group = wrapper.find("[data-testid='wftab-fixed-group'] details");
    expect(group.find("summary").text().replace(/\s+/g, " ")).toBe("Pinned 4");
    expect(rowNamed(wrapper, "seed").element.closest("details")).toBe(group.element);
    expect(rowNamed(wrapper, "steps").element.closest("details")).toBeNull();
  });

  it("keeps a stored pin whose parameter is not among today's defaults", async () => {
    const gone = { slot_label: "slot-b", input_name: "denoise" };
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, SAMPLER] }, pins: [gone] }),
    );
    const { wrapper } = await mountWith([KEY]);
    await lockOf(wrapper, "sampler_name").trigger("click");
    await flush(wrapper);
    expect(setWorkflowPins).toHaveBeenCalledWith(KEY, [
      gone,
      { slot_label: "slot-a", input_name: "sampler_name" },
    ]);
  });

  it("reads the workflow again when the Run popup writes while its read is out", async () => {
    // The read in flight may have been answered before the write landed, so
    // its pins are the old ones and must not win.
    let answerOld;
    getWorkflowCard.mockReturnValueOnce(
      new Promise((resolve) => (answerOld = resolve)),
    );
    const freed = [
      { slot_label: "slot-a", input_name: "steps" },
      { slot_label: "slot-a", input_name: "sampler_name" },
    ];
    getWorkflowCard.mockResolvedValueOnce(
      detail({ card: { defaults: [STEPS, SAMPLER] }, pins: freed }),
    );
    const { wrapper } = await mountWith([KEY]);
    useRunDialogStore().pinsWritten = { workflowId: KEY, pins: freed };
    await flush(wrapper);
    answerOld(detail({ card: { defaults: [STEPS, SAMPLER] }, pins: null }));
    await flush(wrapper);
    expect(lockOf(wrapper, "sampler_name").attributes("aria-pressed")).toBe("false");
  });

  it("keeps a toggled row where it is until the workflow is opened again", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, SAMPLER] } }),
    );
    setWorkflowPins.mockImplementation(async (_key, pins) => ({ pins }));
    const { wrapper } = await mountWith([KEY]);
    await lockOf(wrapper, "sampler_name").trigger("click");
    await flush(wrapper);
    // The lock says it is set each run now, but the row did not jump out
    // from under the pointer.
    expect(lockOf(wrapper, "sampler_name").attributes("aria-pressed")).toBe("false");
    expect(inFixedGroup(wrapper, "sampler_name")).toBe(true);
  });

  it("takes the pins the Run popup wrote, so the next lock does not undo them", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, SAMPLER] } }),
    );
    const { wrapper } = await mountWith([KEY]);
    const freed = [
      { slot_label: "slot-a", input_name: "steps" },
      { slot_label: "slot-a", input_name: "sampler_name" },
    ];
    useRunDialogStore().pinsWritten = { workflowId: KEY, pins: freed };
    await flush(wrapper);
    expect(lockOf(wrapper, "sampler_name").attributes("aria-pressed")).toBe("false");
    await lockOf(wrapper, "steps").trigger("click");
    await flush(wrapper);
    expect(setWorkflowPins).toHaveBeenCalledWith(KEY, [freed[1]]);
  });

  it("pins one parameter without unpinning the rest", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, SAMPLER] } }),
    );
    const { wrapper } = await mountWith([KEY]);
    // The pin is a WHOLE-SET write, exactly like the reset above: pinning
    // `sampler_name` has to carry `steps` too, and the inverted predicate
    // ("pin everything except this one") is invisible to a test that only
    // counts pressed pins.
    await rowNamed(wrapper, "sampler_name").find("button").trigger("click");
    await flush(wrapper);
    expect(setWorkflowPins).toHaveBeenCalledWith(KEY, [
      { slot_label: "slot-a", input_name: "steps" },
      { slot_label: "slot-a", input_name: "sampler_name" },
    ]);
  });

  it("unpins one parameter without pinning the rest", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, SAMPLER] } }),
    );
    const { wrapper } = await mountWith([KEY]);
    await rowNamed(wrapper, "steps").find("button").trigger("click");
    await flush(wrapper);
    expect(setWorkflowPins).toHaveBeenCalledWith(KEY, []);
  });
});

describe("an editor file ComfyUI has not converted (#1530)", () => {
  const editorFile = { imported: true, variant_count: 0 };

  it("says its parameters are not available yet, and that Open converts it", async () => {
    getWorkflowCard.mockResolvedValue(detail({ card: editorFile }));
    const { wrapper } = await mountWith([KEY], [card(editorFile)]);
    expect(wrapper.find('[data-testid="wftab-editor-only"]').exists()).toBe(true);
    expect(textOf(wrapper)).toContain("PixlStash could not convert this ComfyUI workflow");
    expect(textOf(wrapper)).toContain("Open it in ComfyUI from here");
    // There is no manual step to send anybody to any more.
    expect(textOf(wrapper)).not.toContain("Convert for PixlStash");
  });

  it("says nothing of converting a card its pictures made", async () => {
    const pictureOnly = { imported: false, variant_count: 0 };
    getWorkflowCard.mockResolvedValue(detail({ card: pictureOnly }));
    const { wrapper } = await mountWith([KEY], [card(pictureOnly)]);
    expect(wrapper.find('[data-testid="wftab-editor-only"]').exists()).toBe(false);
  });

  it("says nothing of converting once it has a recipe", async () => {
    const converted = { imported: true, variant_count: 1 };
    getWorkflowCard.mockResolvedValue(detail({ card: converted }));
    const { wrapper } = await mountWith([KEY], [card(converted)]);
    expect(wrapper.find('[data-testid="wftab-editor-only"]').exists()).toBe(false);
  });
});

describe("the LoRA pile", () => {
  it("piles what changes, with the cover's LoRA on top", async () => {
    const { wrapper } = await mountWith([KEY]);
    // The cover's LoRA is on top, though Bo has more pictures.
    const pile = wrapper.find("[data-testid='wftab-pile']");
    expect(pile.text()).toContain("Ada");
    expect(pile.text()).toContain("+1");
    expect(textOf(wrapper)).toContain("On top: the cover picture's LoRA.");
    expect(getLoraSummary).toHaveBeenCalledWith(KEY, { cover: undefined });
  });

  it("shows no pile when nothing changes between pictures", async () => {
    getLoraSummary.mockResolvedValue(loraSummary({ varying: [] }));
    const { wrapper } = await mountWith([KEY]);
    expect(wrapper.find("[data-testid='wftab-changes']").exists()).toBe(false);
  });

  it("offers no Promote: the pile only shows (#1623)", async () => {
    getLoraSummary.mockResolvedValue(
      loraSummary({
        without: { asset: "", pictures: 2, picture_ids: [41] },
      }),
    );
    const { wrapper } = await mountWith([KEY]);
    const row = wrapper.find("[data-testid='wftab-fan-row-none']");
    expect(row.text()).toContain("No LoRA");
    const fan = wrapper.find("[data-testid='wftab-fan']");
    expect(fan.text()).not.toContain("Promote");
    expect(fan.text()).toContain("Show 3");
  });

  it("shows the workflow's pictures of one LoRA, as a removable chip", async () => {
    const { wrapper } = await mountWith([KEY]);
    await wrapper
      .find(`[data-testid='wftab-fan-row-${BO}']`)
      .findAll("button")
      .find((button) => button.text() === "Show 3")
      .trigger("click");
    // Two chips' worth (F-4): the workflow, and the LoRA narrowing it.
    expect(useFilterStore().workflowFilter).toEqual([
      {
        id: KEY,
        lora: BO,
        name: "Cinematic portrait",
        loraName: "Bo",
      },
    ]);
    expect(push).toHaveBeenCalledWith("/");
  });
});

describe("the Recipes tab (v1.12 F6)", () => {
  // The other half of the lightbox banner's link (#1480): `?workflow=`
  // selects the card, and `?tab=recipes` opens the rail on its recipes. The
  // `?workflow=` watcher runs after the `?tab=` one and used to put the tab
  // back on Workflow.
  it("lands on the recipes, with the rail open, from ?tab=recipes", async () => {
    route.query = { workflow: KEY, tab: "recipes" };
    const sidebar = useSidebarStore();
    // Shut, which is the state that made the link a dead end of its own: the
    // card would be selected behind a rail nobody opened.
    sidebar.workflowInspectorOpen = false;
    const { wrapper } = await mountWith([KEY]);

    expect(sidebar.workflowInspectorOpen).toBe(true);
    expect(wrapper.findComponent({ name: "WorkflowRecipesTab" }).exists()).toBe(
      true,
    );
  });

  it("leaves the rail alone without that query", async () => {
    // The control for the check above: `tab` defaults to Workflow, so a
    // watcher that fired unconditionally would only show up in the RAIL being
    // forced open on a screen the reader had shut it on.
    const sidebar = useSidebarStore();
    sidebar.workflowInspectorOpen = false;
    const { wrapper } = await mountWith([KEY]);
    expect(sidebar.workflowInspectorOpen).toBe(false);
    expect(wrapper.findComponent({ name: "WorkflowRecipesTab" }).exists()).toBe(
      false,
    );
  });

  it("shows the workflow's recipes, and the footer belongs to Workflow", async () => {
    const { wrapper } = await mountWith([KEY]);
    const tab = wrapper
      .findAll("button")
      .find((b) => b.text().trim() === "Recipes");
    expect(tab).toBeTruthy();
    await tab.trigger("click");
    await flush(wrapper);

    expect(wrapper.findComponent({ name: "WorkflowRecipesTab" }).exists()).toBe(
      true,
    );
    // Run… is the Workflow tab's; the Recipes tab runs a recipe from its row.
    expect(
      wrapper.findAll("button").some((b) => b.text().includes("Run…")),
    ).toBe(false);
  });

  it("answers for a selection of several, as the union of their recipes", async () => {
    const { wrapper, store } = await mountWith([KEY]);
    await wrapper
      .findAll("button")
      .find((b) => b.text().trim() === "Recipes")
      .trigger("click");
    await flush(wrapper);

    store.selectedKeys = [KEY, OTHER];
    await flush(wrapper);

    // Still the Recipes body, now asking about both cards: selecting several
    // is the owner asking what they have between them.
    const tab = wrapper.findComponent({ name: "WorkflowRecipesTab" });
    expect(tab.exists()).toBe(true);
    expect(tab.props("workflowIds")).toEqual([KEY, OTHER]);
  });

  it("puts the refused Run… back when the Workflow tab is chosen", async () => {
    // The footer belongs to the Workflow body, and Run… has to stay on screen
    // and refuse rather than vanish. Leaving the Recipes tab is what brings
    // that screen — and so that control — back.
    const { wrapper, store } = await mountWith([KEY]);
    await wrapper
      .findAll("button")
      .find((b) => b.text().trim() === "Recipes")
      .trigger("click");
    await flush(wrapper);
    store.selectedKeys = [KEY, OTHER];
    await flush(wrapper);
    expect(
      wrapper.findAll("button").some((b) => b.text().includes("Run…")),
    ).toBe(false);

    await wrapper
      .findAll("button")
      .find((b) => b.text().trim() === "Workflow")
      .trigger("click");
    await flush(wrapper);

    const run = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Run…"));
    expect(run).toBeTruthy();
    expect(run.attributes("aria-disabled")).toBe("true");
  });
});

describe("with several workflows selected", () => {
  it("keeps Run… on screen, refused, with the reason attached", async () => {
    const { wrapper } = await mountWith(
      [KEY, OTHER],
      [card(), card({ id: OTHER })],
    );
    const run = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Run…"));
    expect(run).toBeTruthy();
    expect(run.attributes("aria-disabled")).toBe("true");
    const described = run.attributes("aria-describedby");
    expect(wrapper.find(`#${described}`).text()).toBe(
      "Run one workflow at a time",
    );
  });

  it("opens the Run popup on THIS card when Run… is pressed", async () => {
    // The only entry point to a card-sourced run now that the toolbar's
    // Generate button is gone, and it was unguarded: `function run() { return; }`
    // kept the whole suite green.
    const { wrapper } = await mountWith([KEY], [card()]);
    const run = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Run…"));

    await run.trigger("click");
    await flush(wrapper);

    expect(useRunDialogStore().source).toMatchObject({
      kind: "card",
      workflowId: KEY,
      // No picture behind it, so the popup shows the card's cover and an empty
      // prompt rather than prefilling from a recipe it does not have.
      emptyPrompt: true,
    });
  });

  it("does not start a run when Run… is pressed", async () => {
    const { wrapper } = await mountWith(
      [KEY, OTHER],
      [card(), card({ id: OTHER })],
    );
    const run = wrapper
      .findAll("button")
      .find((b) => b.text().includes("Run…"));
    await run.trigger("click");
    await flush(wrapper);
    // Seeded first: `source` is null on a fresh store, so asserting null
    // against an untouched default would pass with `run()` deleted entirely.
    const runDialog = useRunDialogStore();
    expect(runDialog.source).toBe(null);
    runDialog.openRun({ kind: "picture", pictureIds: [99] });
    await run.trigger("click");
    await flush(wrapper);
    expect(runDialog.source.pictureIds).toEqual([99]);
  });

  it("says how many are selected and offers no verbs of its own", async () => {
    // **The rail counts; the grid's selection pill acts** (#1455). These two
    // buttons were here first, and once the pill offered the same two they
    // disagreed within a week: this Hide was hardcoded where the pill's is a
    // toggle that reads Unhide on an all-hidden selection. Two live controls
    // on one screen, one of them wrong about what it was about to do.
    const { wrapper } = await mountWith(
      [KEY, OTHER],
      [card(), card({ id: OTHER })],
    );
    const text = textOf(wrapper);
    expect(text).toContain("2 workflows selected");
    // The reader is told where the verbs went rather than left to find them.
    expect(text).toContain("bar at the bottom of the grid");
    const labels = wrapper.findAll("button").map((b) => b.text());
    expect(labels).not.toContain("Merge");
    expect(labels).not.toContain("Hide");
  });
});

describe("Open in ComfyUI", () => {
  // A real workflow id, `auto:` + core hash, which the link percent-encodes:
  // the desktop shell's guard refuses the bare 64-hex key the ids replaced.
  const ID = `auto:${"a".repeat(64)}`;
  const ID_PARAM = `auto%3A${"a".repeat(64)}`;

  const openButton = (wrapper) => {
    const found = wrapper.find('[data-testid="wftab-open-comfyui"]');
    return found.exists() ? found : undefined;
  };

  let open;
  beforeEach(() => {
    open = vi.spyOn(window, "open").mockImplementation(() => null);
    getPixlstashNode.mockReset();
    getPixlstashNode.mockResolvedValue({ can_open_workflows: true });
  });
  afterEach(() => {
    vi.restoreAllMocks();
    delete window.pixlstashDesktop;
  });

  function configure(url = "http://127.0.0.1:8188/") {
    const filterStore = useFilterStore();
    filterStore.comfyuiConfigured = true;
    filterStore.comfyuiUrl = url;
  }

  it("opens the configured ComfyUI on THIS card, in a new tab", async () => {
    configure();
    const { wrapper } = await mountWith([ID], [card({ id: ID })]);

    await openButton(wrapper).trigger("click");

    expect(open).toHaveBeenCalledWith(
      `http://127.0.0.1:8188/?pixlstash_workflow=${ID_PARAM}`,
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("opens the workflow with its own nodes from the More menu", async () => {
    configure();
    const { wrapper } = await mountWith([ID], [card({ id: ID })]);
    const original = wrapper.find('[data-testid="wftab-open-original"]');
    expect(original.text()).toBe("Open original in ComfyUI");

    await original.trigger("click");

    // The flag the ComfyUI-PixlStash node reads; the button's link has none.
    expect(open).toHaveBeenCalledWith(
      `http://127.0.0.1:8188/?pixlstash_workflow=${ID_PARAM}&pixlstash_original=1`,
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("offers the original only where Open in ComfyUI would open", async () => {
    const original = (wrapper) =>
      wrapper.find('[data-testid="wftab-open-original"]').exists();
    const { wrapper } = await mountWith([ID], [card({ id: ID })]);
    expect(original(wrapper)).toBe(false);

    getPixlstashNode.mockResolvedValue({ can_open_workflows: false });
    configure();
    await flush(wrapper);
    expect(original(wrapper)).toBe(false);
  });

  it("sends a listen-everywhere address to the machine the page came from", async () => {
    configure("http://0.0.0.0:8188/");
    const { wrapper } = await mountWith([ID], [card({ id: ID })]);

    await openButton(wrapper).trigger("click");

    expect(open.mock.calls[0][0]).toBe(
      `http://${window.location.hostname}:8188/?pixlstash_workflow=${ID_PARAM}`,
    );
  });

  it("is not offered without a ComfyUI address", async () => {
    const { wrapper } = await mountWith([ID], [card({ id: ID })]);
    expect(openButton(wrapper)).toBeUndefined();

    // The flag alone is not enough: the button is gated on the address it
    // opens, so a flag set without one never draws a button that does nothing.
    useFilterStore().comfyuiConfigured = true;
    await flush(wrapper);
    expect(openButton(wrapper)).toBeUndefined();

    useFilterStore().comfyuiUrl = "http://127.0.0.1:8188/";
    await flush(wrapper);
    expect(openButton(wrapper)).toBeTruthy();
  });

  it("is named for a screen reader, since it is only the ComfyUI mark", async () => {
    configure();
    const { wrapper } = await mountWith([ID], [card({ id: ID })]);
    expect(openButton(wrapper).attributes("aria-label")).toBe(
      "Open in ComfyUI",
    );
  });

  it("says it opens a copy when ComfyUI's nodes are from before saving back", async () => {
    configure();
    getPixlstashNode.mockResolvedValue({
      can_open_workflows: true,
      saves_back: false,
    });
    const { wrapper } = await mountWith([ID], [card({ id: ID })]);
    await flush(wrapper);
    expect(openButton(wrapper).attributes("aria-label")).toBe(
      "Open a copy in ComfyUI",
    );
  });

  it("goes through the desktop shell's bridge, which window.open cannot", async () => {
    configure();
    const openComfyui = vi.fn().mockResolvedValue(true);
    window.pixlstashDesktop = { openComfyui };
    const { wrapper } = await mountWith([ID], [card({ id: ID })]);

    await openButton(wrapper).trigger("click");

    expect(openComfyui).toHaveBeenCalledWith(
      `http://127.0.0.1:8188/?pixlstash_workflow=${ID_PARAM}`,
    );
    expect(open).not.toHaveBeenCalled();
  });

  it("says so when the desktop shell refuses the link", async () => {
    configure();
    window.pixlstashDesktop = { openComfyui: vi.fn().mockResolvedValue(false) };
    const { wrapper } = await mountWith([ID], [card({ id: ID })]);

    await openButton(wrapper).trigger("click");
    await flush(wrapper);

    expect(JSON.stringify(useNoticeStore().$state)).toContain(
      "Could not open ComfyUI",
    );
  });

  it("is not offered on an older desktop shell with no bridge", async () => {
    configure();
    window.pixlstashDesktop = {};
    const { wrapper } = await mountWith([ID], [card({ id: ID })]);
    expect(openButton(wrapper)).toBeUndefined();
  });

  it("stays on screen and refuses, with the reason, when several are selected", async () => {
    configure();
    const { wrapper } = await mountWith(
      [KEY, OTHER],
      [card(), card({ id: OTHER })],
    );
    const button = openButton(wrapper);
    expect(button.attributes("aria-disabled")).toBe("true");
    const described = button.attributes("aria-describedby");
    expect(wrapper.find(`#${described}`).text()).toBe(
      "Open one workflow at a time",
    );
    await button.trigger("click");
    expect(open).not.toHaveBeenCalled();
  });

  it("refuses, with the reason, when ComfyUI lacks the PixlStash node", async () => {
    configure();
    getPixlstashNode.mockResolvedValue({ can_open_workflows: false });
    const { wrapper } = await mountWith([ID], [card({ id: ID })]);
    await flush(wrapper);

    const button = openButton(wrapper);
    expect(button.attributes("aria-disabled")).toBe("true");
    const described = button.attributes("aria-describedby");
    const reason = wrapper.find(`#${described}`);
    expect(reason.text()).toContain("needs the ComfyUI-PixlStash node");
    expect(reason.text()).toContain("leave Workflows and come back");
    const link = reason.find("a");
    expect(link.attributes("href")).toBe(
      "https://github.com/Pikselkroken/ComfyUI-PixlStash",
    );
    expect(link.attributes("target")).toBe("_blank");
    await button.trigger("click");
    expect(open).not.toHaveBeenCalled();
  });

  it("still opens when ComfyUI cannot be asked about the node", async () => {
    configure();
    getPixlstashNode.mockResolvedValue({ can_open_workflows: null });
    const { wrapper } = await mountWith([ID], [card({ id: ID })]);
    await flush(wrapper);

    await openButton(wrapper).trigger("click");

    expect(open).toHaveBeenCalled();
  });

  it("refuses an address that is not a web address", async () => {
    configure("javascript:alert(1)");
    const { wrapper } = await mountWith([ID], [card({ id: ID })]);

    await openButton(wrapper).trigger("click");

    expect(open).not.toHaveBeenCalled();
    expect(JSON.stringify(useNoticeStore().$state)).toContain(
      "not a web address",
    );
  });
});

describe("a write that comes back after the selection moved", () => {
  it("does not write card A's answer onto card B", async () => {
    // A's reset must not land in a rail showing B.
    getWorkflowCard.mockImplementation(async (key) =>
      detail({ card: { key, defaults: [key === KEY ? CFG : SAMPLER] } }),
    );
    let settle;
    setWorkflowDefaults.mockReturnValue(
      new Promise((resolve) => {
        settle = () => resolve(detail({ card: { id: KEY, defaults: [CFG] } }));
      }),
    );
    const { wrapper, store } = await mountWith(
      [KEY],
      [card({ name: "Card A" }), card({ id: OTHER, name: "Card B" })],
    );
    await rowNamed(wrapper, "cfg").find(".wfdef-reset").trigger("click");
    store.selectedKeys = [OTHER];
    await flush(wrapper);
    settle();
    await flush(wrapper);
    expect(textOf(wrapper)).toContain("Card B");
    expect(textOf(wrapper)).not.toContain("Card A");
    // The detail carries the DEFAULTS, so A's answer landing here puts A's
    // parameters under B's name. The header comes from the grid and would
    // keep saying B either way.
    expect(() => rowNamed(wrapper, "cfg")).toThrow();
    expect(rowNamed(wrapper, "sampler_name").exists()).toBe(true);
  });

  it("does not let a hide's answer put the pre-toggle pins back", async () => {
    // The hide PATCH answers with the WHOLE detail, pins included, so landing
    // after a pin write it would put the pin back while the server holds the
    // new value.
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, SAMPLER] } }),
    );
    // A hub that answers with the pins it holds AT THAT MOMENT. The PATCH is
    // the slow one: queued first, run beside the pin write it would answer
    // last, carrying `pins: null`.
    let pinned = null;
    let settleHide;
    setWorkflowPins.mockImplementation(async (_key, pins) => {
      pinned = pins;
      return { pins };
    });
    patchWorkflowCard.mockImplementation(
      () =>
        new Promise((resolve) => {
          const answer = { pins: pinned };
          settleHide = () =>
            resolve(
              detail({
                card: { defaults: [STEPS, SAMPLER] },
                hidden: true,
                ...answer,
              }),
            );
        }),
    );
    const { wrapper } = await mountWith([KEY]);
    await wrapper.findAll("button").find((b) => b.text() === "Hide").trigger("click");
    await rowNamed(wrapper, "sampler_name").find("button").trigger("click");
    await flush(wrapper);

    // The hide is in flight and the pin write waits behind it rather than
    // racing it.
    expect(patchWorkflowCard).toHaveBeenCalledWith(KEY, { hidden: true });
    expect(setWorkflowPins).not.toHaveBeenCalled();
    settleHide();
    await flush(wrapper);

    // Both ran, and the pin stuck.
    expect(setWorkflowPins).toHaveBeenCalledTimes(1);
    expect(lockOf(wrapper, "sampler_name").attributes("aria-pressed")).toBe("false");
  });

  it("drops a whole-set write queued behind another when the selection moves", async () => {
    // The pins PUT is whole-set, built from `defaults` when it RUNS, which is
    // after the hide it waited behind. By then those are card B's, and
    // sending them to A would replace A's own choice with B's.
    getWorkflowCard.mockImplementation(async (key) =>
      detail({ card: { id: key, defaults: key === KEY ? [CFG] : [WIDTH] } }),
    );
    let settle;
    patchWorkflowCard.mockReturnValue(
      new Promise((resolve) => {
        settle = () => resolve(detail({ card: { defaults: [CFG] } }));
      }),
    );
    const { wrapper, store } = await mountWith(
      [KEY],
      [card({ name: "Card A" }), card({ id: OTHER, name: "Card B" })],
    );
    await wrapper.findAll("button").find((b) => b.text() === "Hide").trigger("click");
    await rowNamed(wrapper, "cfg").find("button").trigger("click");
    store.selectedKeys = [OTHER];
    await flush(wrapper);
    settle();
    await flush(wrapper);
    expect(patchWorkflowCard).toHaveBeenCalledWith(KEY, { hidden: true });
    expect(setWorkflowPins).not.toHaveBeenCalled();
  });

  it("runs one write at a time, so two cannot discard each other", async () => {
    getWorkflowCard.mockResolvedValue(
      detail({ card: { defaults: [STEPS, CFG] } }),
    );
    setWorkflowPins.mockReturnValue(new Promise(() => {}));
    const { wrapper } = await mountWith([KEY]);
    await rowNamed(wrapper, "steps").find("button").trigger("click");
    await rowNamed(wrapper, "cfg").find(".wfdef-reset").trigger("click");
    await flush(wrapper);
    // `detail` carries the defaults, the pins and the card together, so a
    // second write in flight means the slower answer overwrites the faster
    // one's change.
    expect(setWorkflowDefaults).not.toHaveBeenCalled();
  });
});

describe("the more menu and hiding", () => {
  it("hides the card and can still unhide it once it has left the grid", async () => {
    getWorkflowCard.mockResolvedValue(detail());
    patchWorkflowCard.mockResolvedValue(detail({ hidden: true }));
    // A hidden card is not in `GET /workflows`, which is exactly the
    // case that used to take the footer and its menu off screen.
    listWorkflowCards.mockResolvedValue({ cards: [], one_offs: 0, hidden: 1 });
    const { wrapper } = await mountWith([KEY]);
    const hide = wrapper.findAll("button").find((b) => b.text() === "Hide");
    await hide.trigger("click");
    await flush(wrapper);
    expect(patchWorkflowCard).toHaveBeenCalledWith(KEY, { hidden: true });
    expect(
      wrapper.findAll("button").find((b) => b.text() === "Unhide"),
    ).toBeTruthy();
  });
});

describe("which card the rail shows", () => {
  // Half of the chip's round trip: this screen is the only one that knows a
  // card's key, and `useGridFetch.test.js` holds the half that spends it.
  it("opens the library on the card's own pictures, as a removable chip", async () => {
    const { wrapper } = await mountWith([KEY]);
    const link = wrapper.find('[data-testid="wftab-show-pictures"]');
    expect(link.text()).toBe("184 pictures");
    expect(link.attributes("aria-label")).toBe("Show all 184 pictures");

    await link.trigger("click");

    expect(useFilterStore().workflowFilter).toEqual([
      { id: KEY, name: "Cinematic portrait" },
    ]);
    expect(push).toHaveBeenCalledWith("/");
  });

  // The link promises a number it was handed by the card. A reader with a tag
  // filter on, or a character selected, would be shown fewer than that with
  // nothing on screen saying why — so the view it lands in gives way.
  it("clears the filters and the sidebar scope, because it says 'all'", async () => {
    const { wrapper } = await mountWith([KEY]);
    const filterStore = useFilterStore();
    const selectionStore = useSelectionStore();
    filterStore.tagFilter = ["hat"];
    filterStore.minScoreFilter = 4;
    selectionStore.selectedCharacter = 9;
    selectionStore.selectedSet = 3;
    useSearchStore().searchQuery = "cats";

    await wrapper.find('[data-testid="wftab-show-pictures"]').trigger("click");

    expect(filterStore.tagFilter).toEqual([]);
    expect(filterStore.minScoreFilter).toBeNull();
    expect(selectionStore.selectedCharacter).toBe("ALL");
    expect(selectionStore.selectedSet).toBeNull();
    expect(useSearchStore().searchQuery).toBe("");
    // And the one filter it is FOR survives `resetFilters`, which clears it
    // too — the order of those two lines is the whole behaviour.
    expect(filterStore.workflowFilter).toEqual([
      { id: KEY, name: "Cinematic portrait" },
    ]);
  });

  // Nothing to show, so nothing to press: a link that filtered the library
  // down to nothing would read as a broken grid rather than as an empty card.
  it("leaves the figure as plain text on a card with no pictures", async () => {
    const { wrapper } = await mountWith([KEY], [card({ picture_count: 0 })]);
    expect(wrapper.find('[data-testid="wftab-show-pictures"]').exists()).toBe(
      false,
    );
    expect(textOf(wrapper)).toContain("0 pictures");
  });

});

describe("the Tasks tab", () => {
  // The rail on /workflows is the only thing on that screen, so without this
  // tab a run started here cannot be watched from here.
  it("offers Tasks last, and shows the task manager instead of the workflow", async () => {
    const { wrapper } = await mountWith([KEY]);
    const band = wrapper.findAll("button.inspector-tab");
    // Recipes joined the band in F6 (#1408); Tasks stays last, which is the
    // property this test is about.
    expect(band.map((t) => t.text())).toEqual(["Recipes", "Workflow", "Tasks"]);

    await band[2].trigger("click");
    await flush(wrapper);
    const text = textOf(wrapper);
    // The task manager's own empty state, and none of the workflow.
    expect(text).toContain("No active tasks");
    expect(text).not.toContain("Cinematic portrait");
    // Run… acts on the workflow, so it goes with the workflow: a footer left
    // behind would run a card the reader can no longer see.
    expect(wrapper.find(".wftab-foot").exists()).toBe(false);

    await wrapper.findAll("button.inspector-tab")[1].trigger("click");
    await flush(wrapper);
    expect(textOf(wrapper)).toContain("Cinematic portrait");
    expect(wrapper.find(".wftab-foot").exists()).toBe(true);
  });

  it("follows the store's deep link onto the Tasks tab", async () => {
    // A run started from THIS rail pushes a toast whose *Show* action asks for
    // the Tasks tab. It used to call a method exposed by `StatsSidebar`, which
    // is not mounted on /workflows, so the one screen that starts runs was the
    // one screen where *Show* did nothing at all.
    const { wrapper } = await mountWith([KEY]);
    expect(textOf(wrapper)).not.toContain("No active tasks");

    useSidebarStore().showTasksTab();
    await flush(wrapper);
    expect(textOf(wrapper)).toContain("No active tasks");
  });

  it("pulses the tab while work is running, and only then", async () => {
    const { wrapper } = await mountWith([KEY]);
    const tasksStore = useTasksStore();
    expect(
      wrapper
        .findAll("button.inspector-tab")
        .at(-1)
        .find(".inspector-tab-pulse")
        .exists(),
    ).toBe(false);

    tasksStore.comfyuiRuns = [
      {
        prompt_id: "run-1",
        status: "running",
        percent: 10,
        label: "Cinematic portrait",
      },
    ];
    await flush(wrapper);
    expect(
      wrapper
        .findAll("button.inspector-tab")
        .at(-1)
        .find(".inspector-tab-pulse")
        .exists(),
    ).toBe(true);
  });
});

describe("the LoRA chain (#1478)", () => {
  const chainOpts = {
    global: {
      stubs: {
        ...globalOpts.global.stubs,
        EditLorasDialog: {
          name: "EditLorasDialog",
          props: ["open", "workflowId", "cardName", "pictureCount", "dropLora"],
          emits: ["close", "overwritten"],
          template: "<div class='eld-stub' />",
        },
      },
    },
  };

  // No picture unless a test says so: nothing sampled, so the chain a run
  // loads is the whole list.
  beforeEach(() => {
    getLoraSummary.mockResolvedValue(loraSummary({ pictures: 0, varying: [] }));
    const unsampled = detail();
    unsampled.card.default_recipe.sampled = 0;
    getWorkflowCard.mockResolvedValue(unsampled);
  });

  async function mountChain(keys = [KEY]) {
    const store = useWorkflowsStore();
    store.cards = [card()];
    store.selectedKeys = keys;
    const wrapper = mount(WorkflowTab, chainOpts);
    // Unmounted with the rest. Left mounted, a rail with Edit LoRAs… open
    // re-renders when its pre-flight lands, by then under a later test's
    // stubs, which mounts the real dialog with no Vuetify to mount it in.
    mounted.push(wrapper);
    await flush(wrapper);
    return { wrapper, store };
  }

  function editButton(wrapper) {
    return wrapper.find("[data-testid='wftab-edit-loras']");
  }

  /** A row's strength: the field where it can be set, else the text. */
  function strengthOf(row) {
    const field = row.find("[data-testid='wftab-lora-strength']");
    return field.exists()
      ? field.element.value
      : row.find(".wftab-chain-strength").text();
  }

  it("lists a picture-less workflow's loaders in apply order, with the shelf count", async () => {
    // Nothing to count, so nothing is shared and nothing changes: the rows
    // are the workflow's own chain.
    const { wrapper } = await mountChain();
    expect(getLoraChain).toHaveBeenCalledWith(KEY);
    const rows = wrapper.findAll("[data-testid='wftab-default-lora']");
    expect(
      rows.map((row) => [
        row.find(".wftab-chain-name").text(),
        strengthOf(row),
      ]),
    ).toEqual([
      ["lightning-8step", "1.00"],
      ["neon-rain-v2", "0.85"],
      ["film-grain-35mm", "0.40"],
      // Not on the shelf, so shown as the file it is.
      ["hairstyle-v3.safetensors", "0.60"],
    ]);
    expect(
      wrapper.find("[data-testid='wftab-shared-note']").text().replace(/\s+/g, " "),
    ).toBe("In the order the chain applies them. 3 of 4 are on your model shelf.");
    // The Workflow | Recipe switches are gone.
    expect(wrapper.findComponent({ name: "Segmented" }).exists()).toBe(false);
  });

  it("puts Edit LoRAs… in the head of the panel holding the LoRAs, apart from the clone", async () => {
    // Both verbs in one head overflowed a narrow inspector sideways.
    const { wrapper } = await mountChain();
    const models = wrapper.find("[data-testid='wftab-default-recipe'] .wftab-sec-head");
    expect(models.find("[data-testid='wftab-use-set']").exists()).toBe(true);
    expect(models.find("[data-testid='wftab-edit-loras']").exists()).toBe(false);
    const loras = wrapper.find("[data-testid='wftab-loras']");
    expect(loras.find(".wftab-sec-head [data-testid='wftab-edit-loras']").exists()).toBe(true);
    const rows = wrapper.findAll("[data-testid='wftab-default-lora']");
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.every((row) => loras.element.contains(row.element))).toBe(true);
  });

  it("gives a chain row the file its name's tooltip carries", async () => {
    const { wrapper } = await mountChain();
    const names = wrapper.findAll(".wftab-chain-name");
    expect(names.length).toBeGreaterThan(0);
    expect(names[0].find("tooltip-stub").attributes("text")).toBe(
      "lightning-8step.safetensors",
    );
  });

  it("lists the default recipe's LoRAs in the chain's order, joined by digest", async () => {
    // Given out of order. The chain says the order, by shelf DIGEST; the
    // summary, joined by ASSET reference (another hash), says what to call each and whether it is on the shelf; the strength is
    // the default recipe's own.
    const sampled = detail();
    sampled.card.default_recipe.loras = [
      { asset: "asset:n9", filename: "hairstyle-v3.safetensors", sha256: "S9", strength: 0.6, provenance: "best" },
      { asset: "asset:n3", filename: "film-grain-35mm.safetensors", sha256: "s3", strength: 0.5, provenance: "best" },
      { asset: "asset:n1", filename: "renamed.safetensors", sha256: "s1", strength: 1, provenance: "best" },
    ];
    getWorkflowCard.mockResolvedValue(sampled);
    const use = (sha, name, onShelf = true) => ({
      asset: `asset:${sha}`,
      filename: `${name}.safetensors`,
      name,
      on_shelf: onShelf,
      pictures: 5,
    });
    getLoraSummary.mockResolvedValue(
      loraSummary({
        shared: [use("N9", "Hairstyle v3", false), use("n1", "Lightning"), use("n3", "Film grain")],
        varying: [],
      }),
    );
    const { wrapper } = await mountChain();
    const rows = wrapper.findAll("[data-testid='wftab-default-lora']");
    expect(
      rows.map((row) => [
        row.find(".wftab-chain-name").text(),
        strengthOf(row),
      ]),
    ).toEqual([
      ["Lightning", "1.00"],
      ["Film grain", "0.50"],
      ["Hairstyle v3", "0.60"],
    ]);
    // In every picture, so no count on any of them.
    expect(wrapper.find(".wftab-coverage").exists()).toBe(false);
    expect(
      wrapper.find("[data-testid='wftab-shared-note']").text().replace(/\s+/g, " "),
    ).toBe("Hairstyle v3 is not on your shelf.");
  });

  it("lists a forked chain's lane loaders after its trunk", async () => {
    const [first, second] = loraChain().loaders;
    getLoraChain.mockResolvedValue(
      loraChain({
        source: null,
        loaders: [],
        lanes: [
          { sampler: { node_id: "3" }, loaders: [first] },
          { sampler: { node_id: "15" }, loaders: [second] },
        ],
      }),
    );
    const { wrapper } = await mountChain();
    expect(
      wrapper.findAll(".wftab-chain-name").map((name) => name.text()),
    ).toEqual(["lightning-8step", "neon-rain-v2"]);
    expect(textOf(wrapper)).not.toContain("No LoRA loader");
  });

  it("offers Edit LoRAs… on a workflow with no loader at all", async () => {
    getLoraChain.mockResolvedValue(loraChain({ loaders: [] }));
    const { wrapper } = await mountChain();
    expect(textOf(wrapper)).toContain("No LoRA loader. Editing adds the first one.");
    const edit = editButton(wrapper);
    expect(edit.exists()).toBe(true);
    expect(edit.attributes("disabled")).toBeUndefined();
    expect(wrapper.find("[data-testid='wftab-shelf-line']").exists()).toBe(false);

    await edit.trigger("click");
    await flush(wrapper);
    const dialog = wrapper.findComponent({ name: "EditLorasDialog" });
    expect(dialog.exists()).toBe(true);
    expect(dialog.props("workflowId")).toBe(KEY);
    expect(dialog.props("cardName")).toBe("Cinematic portrait");
    expect(dialog.props("pictureCount")).toBe(184);
    expect(dialog.props("dropLora")).toBe("");
  });

  it("reads the chain again when Edit LoRAs… saved over the card itself", async () => {
    const { wrapper } = await mountChain();
    await editButton(wrapper).trigger("click");
    await flush(wrapper);
    const reads = getLoraChain.mock.calls.length;
    const details = getWorkflowCard.mock.calls.length;
    getLoraChain.mockResolvedValue(loraChain({ loaders: [] }));

    // Another card's overwrite is not this rail's to re-read.
    const dialog = wrapper.findComponent({ name: "EditLorasDialog" });
    dialog.vm.$emit("overwritten", OTHER);
    await flush(wrapper);
    expect(getLoraChain.mock.calls.length).toBe(reads);

    dialog.vm.$emit("overwritten", KEY);
    await flush(wrapper);
    expect(getLoraChain.mock.calls.length).toBe(reads + 1);
    // The detail too: a manual workflow's version line comes with it.
    expect(getWorkflowCard.mock.calls.length).toBe(details + 1);
    expect(textOf(wrapper)).toContain("No LoRA loader. Editing adds the first one.");
  });

  it("says so, and refuses Edit, for a card with no graph", async () => {
    getLoraChain.mockRejectedValue({ response: { status: 409 } });
    const { wrapper } = await mountChain();
    expect(textOf(wrapper)).toContain("PixlStash has no graph for this workflow");
    expect(editButton(wrapper).attributes("disabled")).toBeDefined();
  });

  it("opens Edit LoRAs… from Save-as-recipe's link, with the entry deleted", async () => {
    route.query = { workflow: OTHER, edit: "loras", drop_lora: "hairstyle-v3.safetensors" };
    const sidebar = useSidebarStore();
    sidebar.workflowInspectorOpen = false;
    const { wrapper, store } = await mountChain([]);

    expect(store.selectedKeys).toEqual([OTHER]);
    expect(sidebar.workflowInspectorOpen).toBe(true);
    const dialog = wrapper.findComponent({ name: "EditLorasDialog" });
    expect(dialog.exists()).toBe(true);
    expect(dialog.props("workflowId")).toBe(OTHER);
    expect(dialog.props("dropLora")).toBe("hairstyle-v3.safetensors");
    // One-shot: taken off the URL so a reload does not reopen it.
    expect(replace).toHaveBeenCalledWith({ query: { workflow: OTHER } });
  });
});

describe("expose a parameter", () => {
  const SCALE = { slot_label: "core:up", input_name: "scale_by" };
  const NODES = [
    {
      node_id: "3",
      title: "KSampler",
      class_type: "KSampler",
      inputs: [
        // A row already, and a choice: its options feed the row's list.
        {
          ...SAMPLER,
          kind: "choice",
          options: ["euler", "dpmpp_2m"],
          exposed: true,
        },
      ],
    },
    {
      node_id: "8",
      title: "Final upscale",
      class_type: "ImageScaleBy",
      inputs: [{ ...SCALE, value: 1.5, kind: "number", options: null, exposed: false }],
    },
  ];
  const dialog = (wrapper) =>
    wrapper.findComponent({ name: "ExposeParameterDialog" });
  const stubbed = {
    global: {
      stubs: { ...globalOpts.global.stubs, ExposeParameterDialog: true },
    },
  };

  async function mountExposing(shown, refused = null) {
    getWorkflowCard.mockResolvedValue(shown);
    if (refused) getWorkflowFormInputs.mockRejectedValue(refused);
    else getWorkflowFormInputs.mockResolvedValue({ nodes: NODES });
    const store = useWorkflowsStore();
    store.cards = [card()];
    store.selectedKeys = [KEY];
    const wrapper = mount(WorkflowTab, stubbed);
    mounted.push(wrapper);
    await flush(wrapper);
    return wrapper;
  }

  it("offers the nodes' settings that are not parameters yet, and only those", async () => {
    const wrapper = await mountExposing(
      detail({ card: { defaults: [STEPS, SAMPLER] } }),
    );
    expect(getWorkflowFormInputs).toHaveBeenCalledWith(KEY);
    expect(dialog(wrapper).exists()).toBe(false);
    await wrapper.find("[data-testid='wftab-expose']").trigger("click");
    // The sampler node holds nothing but a row, so it is not offered at all.
    expect(dialog(wrapper).props("nodes")).toEqual([NODES[1]]);
  });

  it("writes the pick as the workflow's own value and sets it each run", async () => {
    const exposed = {
      ...SCALE,
      label: "scale_by",
      value: 1.5,
      provenance: "edited",
      exposed: true,
    };
    const wrapper = await mountExposing(
      detail({ card: { defaults: [STEPS, CFG, SAMPLER] } }),
    );
    setWorkflowDefaults.mockResolvedValue(
      detail({ card: { defaults: [STEPS, CFG, SAMPLER, exposed] } }),
    );
    setWorkflowPins.mockImplementation(async (_key, pins) => ({ pins }));
    await wrapper.find("[data-testid='wftab-expose']").trigger("click");
    dialog(wrapper).vm.$emit("expose", { ...SCALE, value: 1.5, name: "Scale" });
    await flush(wrapper);
    // Whole-set: the edit that was there goes back with it, without a name,
    // so the name it may have stands.
    expect(setWorkflowDefaults).toHaveBeenCalledWith(KEY, [
      { slot_label: "slot-a", input_name: "cfg", value: "7.5" },
      { ...SCALE, value: 1.5, name: "Scale" },
    ]);
    expect(setWorkflowDefaults.mock.calls[0][1][0]).not.toHaveProperty("name");
    // And the pins nobody had chosen are written out, with it among them.
    expect(setWorkflowPins).toHaveBeenCalledWith(KEY, [
      { slot_label: "slot-a", input_name: "steps" },
      { slot_label: "slot-a", input_name: "cfg" },
      SCALE,
    ]);
    expect(dialog(wrapper).exists()).toBe(false);
    expect(lockOf(wrapper, "scale_by").attributes("aria-pressed")).toBe("false");
    expect(inFixedGroup(wrapper, "scale_by")).toBe(false);
    // Named after its setting alone, so its tip says which node's it is; a
    // parameter the pictures vote on needs no telling, though its node is
    // in the same read.
    const tip = (name) =>
      rowNamed(wrapper, name).find(".wfdef-name").findComponent({ name: "Tooltip" });
    expect(tip("scale_by").props("text")).toBe("Final upscale · scale_by");
    expect(tip("sampler_name").exists()).toBe(false);
    // Its ↺ would have nothing to go back to: the button removes the row.
    expect(
      rowNamed(wrapper, "scale_by").find("[data-testid='wfdef-reset']").attributes("aria-label"),
    ).toBe("Remove scale_by from the parameters");
    expect(
      rowNamed(wrapper, "cfg").find("[data-testid='wfdef-reset']").attributes("aria-label"),
    ).toBe("Put cfg back to what your pictures say");
    // It is a row now, so there is nothing left to offer.
    await wrapper.find("[data-testid='wftab-expose']").trigger("click");
    expect(dialog(wrapper).props("nodes")).toEqual([]);
  });

  it("keeps a choice's list on screen while the graph is read again, and when that read fails", async () => {
    const wrapper = await mountExposing(
      detail({
        card: { defaults: [STEPS, SAMPLER] },
        pins: [{ slot_label: SAMPLER.slot_label, input_name: SAMPLER.input_name }],
      }),
    );
    const list = () =>
      rowNamed(wrapper, "sampler_name").find(
        "[data-testid='wfdef-select'] select",
      );
    const drawn = list().element;
    let refuse;
    getWorkflowFormInputs.mockReturnValue(
      new Promise((_resolve, reject) => {
        refuse = reject;
      }),
    );
    // A save in the Run popup: everything off the graph is read again.
    useRunDialogStore().saved = { workflowId: KEY, savedAs: null, at: 1 };
    await flush(wrapper);
    // The very element, not one swapped for a plain box and drawn again:
    // focus on it would have gone with it.
    expect(list().exists()).toBe(true);
    expect(list().element).toBe(drawn);

    refuse(new Error("offline"));
    await flush(wrapper);
    expect(list().exists()).toBe(true);
    expect(list().element).toBe(drawn);
  });

  it("draws no plain box where a list is still on its way", async () => {
    // The choices come from their own read. A row drawn before it lands is a
    // box that turns into a list under the pointer; it waits instead.
    let answer;
    getWorkflowFormInputs.mockReturnValue(
      new Promise((resolve) => {
        answer = () => resolve({ nodes: NODES });
      }),
    );
    getWorkflowCard.mockResolvedValue(
      detail({
        card: { defaults: [STEPS, SAMPLER] },
        pins: [{ slot_label: SAMPLER.slot_label, input_name: SAMPLER.input_name }],
      }),
    );
    const store = useWorkflowsStore();
    store.cards = [card()];
    store.selectedKeys = [KEY];
    const wrapper = mount(WorkflowTab, stubbed);
    mounted.push(wrapper);
    await flush(wrapper);
    const panel = () => wrapper.find("[data-testid='wftab-parameters']");
    expect(panel().text()).toContain("Reading its parameters");
    expect(panel().find("[data-testid='wfdef-input']").exists()).toBe(false);

    answer();
    await flush(wrapper);
    expect(panel().text()).not.toContain("Reading its parameters");
    expect(
      rowNamed(wrapper, "sampler_name")
        .find("[data-testid='wfdef-select']")
        .exists(),
    ).toBe(true);
  });

  it("waits for no lists where there is no row to draw", async () => {
    getWorkflowFormInputs.mockReturnValue(new Promise(() => {}));
    getWorkflowCard.mockResolvedValue(detail({ card: { defaults: [] } }));
    const store = useWorkflowsStore();
    store.cards = [card()];
    store.selectedKeys = [KEY];
    const wrapper = mount(WorkflowTab, stubbed);
    mounted.push(wrapper);
    await flush(wrapper);
    const panel = wrapper.find("[data-testid='wftab-parameters']");
    expect(panel.text()).not.toContain("Reading its parameters");
    expect(panel.text()).toContain("Nothing this workflow made records a setting");
  });

  it("stops waiting for the lists of a ComfyUI that is slow to answer", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    try {
      getWorkflowFormInputs.mockReturnValue(new Promise(() => {}));
      getWorkflowCard.mockResolvedValue(
        detail({
          card: { defaults: [STEPS, SAMPLER] },
          pins: [
            { slot_label: SAMPLER.slot_label, input_name: SAMPLER.input_name },
          ],
        }),
      );
      const store = useWorkflowsStore();
      store.cards = [card()];
      store.selectedKeys = [KEY];
      const wrapper = mount(WorkflowTab, stubbed);
      mounted.push(wrapper);
      await vi.advanceTimersByTimeAsync(999);
      const panel = wrapper.find("[data-testid='wftab-parameters']");
      expect(panel.find("[data-testid='wfdef-input']").exists()).toBe(false);
      // Plain boxes still set the value, so they are what is drawn.
      await vi.advanceTimersByTimeAsync(1);
      expect(panel.find("[data-testid='wfdef-input']").exists()).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it("draws a choice set each run as a list of ComfyUI's options", async () => {
    const wrapper = await mountExposing(
      detail({
        card: { defaults: [STEPS, { ...SAMPLER, value: "res_2s" }] },
        pins: [{ slot_label: SAMPLER.slot_label, input_name: SAMPLER.input_name }],
      }),
    );
    const list = rowNamed(wrapper, "sampler_name").find(
      "[data-testid='wfdef-select'] select",
    );
    // Its own value stays listed though this ComfyUI does not offer it.
    expect(list.findAll("option").map((o) => o.text())).toEqual([
      "res_2s",
      "euler",
      "dpmpp_2m",
    ]);
    // A number is still typed.
    expect(rowNamed(wrapper, "steps").find("[data-testid='wfdef-select']").exists()).toBe(false);
    await list.setValue("euler");
    await flush(wrapper);
    expect(setWorkflowDefaults).toHaveBeenCalledWith(KEY, [
      { slot_label: SAMPLER.slot_label, input_name: "sampler_name", value: "euler" },
    ]);
  });

  it("offers an on-or-off value as the two words, and writes the value they mean", async () => {
    const tiled = {
      label: "tiled",
      slot_label: "core:up",
      input_name: "tiled",
      value: true,
      provenance: "edited",
      exposed: true,
    };
    const wrapper = await mountExposing(
      detail({
        card: { defaults: [STEPS, tiled] },
        pins: [{ slot_label: "core:up", input_name: "tiled" }],
      }),
    );
    const list = rowNamed(wrapper, "tiled").find("[data-testid='wfdef-select'] select");
    expect(list.findAll("option").map((o) => o.text())).toEqual(["true", "false"]);
    await list.setValue("false");
    await flush(wrapper);
    // The boolean, never the word: ComfyUI reads the text "false" as on.
    expect(setWorkflowDefaults).toHaveBeenCalledWith(KEY, [
      { slot_label: "core:up", input_name: "tiled", value: false },
    ]);
  });

  it("keeps the newest read of a workflow when an older one of it lands last", async () => {
    getWorkflowCard.mockResolvedValue(detail({ card: { defaults: [STEPS] } }));
    // Left and come back to before the first answers: two reads of one key.
    const late = {};
    getWorkflowFormInputs
      .mockImplementationOnce(() => new Promise((_, reject) => (late.form = reject)))
      .mockResolvedValue({ nodes: NODES });
    getLoraChain
      .mockImplementationOnce(() => new Promise((_, reject) => (late.chain = reject)))
      .mockResolvedValue(loraChain());
    const store = useWorkflowsStore();
    store.cards = [card()];
    store.selectedKeys = [KEY];
    const wrapper = mount(WorkflowTab, stubbed);
    mounted.push(wrapper);
    await flush(wrapper);
    store.selectedKeys = [];
    await flush(wrapper);
    store.selectedKeys = [KEY];
    await flush(wrapper);
    // The first reads fail now, after the second ones were answered.
    late.form(new Error("gone"));
    late.chain(new Error("gone"));
    await flush(wrapper);
    await wrapper.find("[data-testid='wftab-expose']").trigger("click");
    expect(dialog(wrapper).props("failed")).toBe("");
    expect(dialog(wrapper).props("nodes")).toHaveLength(2);
    expect(wrapper.vm.chainFailed).toBe(false);
    expect(wrapper.vm.chain).not.toBeNull();
  });

  it("says why when the workflow has no graph to pick from", async () => {
    const wrapper = await mountExposing(
      detail({ card: { defaults: [STEPS] } }),
      { response: { status: 409 } },
    );
    // The rows are untouched by the failed read: still plain boxes.
    expect(rowNamed(wrapper, "steps").find("[data-testid='wfdef-input']").exists()).toBe(true);
    await wrapper.find("[data-testid='wftab-expose']").trigger("click");
    expect(dialog(wrapper).props("failed")).toContain("has no graph");
    expect(dialog(wrapper).props("nodes")).toEqual([]);
  });
});

// #1839: where a run that ended without a picture is said to have, with the
// sentence that says why, until it is dismissed or the workflow runs again.
describe("the selected workflow's failed run", () => {
  const block = (w) => w.find('[data-testid="wftab-run-failure"]');
  const message =
    "Load Diffusion Model (UNETLoader) failed: KeyError 'asym_w4a8_int8'";

  it("says the run failed and why, and Dismiss takes it down", async () => {
    const { wrapper } = await mountWith([KEY]);
    expect(block(wrapper).exists()).toBe(false);

    const runDialog = useRunDialogStore();
    runDialog.runFailed(KEY, { promptId: "p-1", message, stopped: false });
    await flush(wrapper);
    // Not an alert: the panel mounts again on every selection.
    expect(block(wrapper).attributes("role")).toBe("status");
    expect(block(wrapper).text()).toContain("Last run failed");
    expect(block(wrapper).text()).toContain(message);
    expect(block(wrapper).classes()).not.toContain("wftab-run-failure--stopped");

    await wrapper.find('[data-testid="wftab-run-failure-dismiss"]').trigger("click");
    await flush(wrapper);
    expect(block(wrapper).exists()).toBe(false);
    expect(runDialog.failures[KEY]).toBeUndefined();
  });

  // Dismiss removes its own button; focus must not fall to the page body.
  it("hands focus to Run… when it is dismissed", async () => {
    useWorkflowsStore().cards = [card()];
    useWorkflowsStore().selectedKeys = [KEY];
    useRunDialogStore().runFailed(KEY, { promptId: "p-1", message, stopped: false });
    const wrapper = mount(WorkflowTab, { ...globalOpts, attachTo: document.body });
    mounted.push(wrapper);
    await flush(wrapper);
    const dismiss = wrapper.find('[data-testid="wftab-run-failure-dismiss"]');
    dismiss.element.focus();
    await dismiss.trigger("click");
    await flush(wrapper);
    // The button itself: the body's text holds "Run…" too.
    expect(document.activeElement?.tagName).toBe("BUTTON");
    expect(document.activeElement.textContent.trim()).toBe("Run…");
  });

  it("says stopped for a run somebody interrupted", async () => {
    const { wrapper } = await mountWith([KEY]);
    useRunDialogStore().runFailed(KEY, {
      promptId: "p-1",
      message: "Interrupted at KSampler",
      stopped: true,
    });
    await flush(wrapper);
    expect(block(wrapper).text()).toContain("Last run stopped");
    expect(block(wrapper).classes()).toContain("wftab-run-failure--stopped");
  });

  it("is another workflow's business when another is selected", async () => {
    const { wrapper } = await mountWith([KEY]);
    useRunDialogStore().runFailed(OTHER, { promptId: "p-1", message, stopped: false });
    await flush(wrapper);
    expect(block(wrapper).exists()).toBe(false);
  });
});

// ── The tab as the editor (#1850 part 3) ────────────────────────────────────
//
// What can silently invert here: an edit written straight to the workflow
// instead of joining the one set of unsaved changes; a row put back to its
// saved value kept in the set as a change; the first of two quick edits
// drawing over the second; and a rail that goes blank when the workflow is
// saved from somewhere else.

describe("the tab as the editor (#1850)", () => {
  const ADDRESS = "core:m1/ckpt_name";
  const JUGGERNAUT = {
    id: 7,
    filename: "SDXL/juggernautXL_v9.safetensors",
    display_name: "Juggernaut XL 9",
  };

  const editorOpts = {
    attachTo: document.body,
    global: {
      stubs: {
        ...globalOpts.global.stubs,
        EditLorasDialog: {
          name: "EditLorasDialog",
          props: ["open", "workflowId", "cardName", "pictureCount", "dropLora"],
          emits: ["close", "apply", "overwritten"],
          template: "<div class='eld-stub' />",
        },
      },
    },
  };

  const bar = (wrapper) => wrapper.find('[data-testid="wftab-draft"]');
  const count = (wrapper) =>
    wrapper.find('[data-testid="wftab-draft-count"]').text().replace(/\s+/g, " ");
  const lastSet = () => setWorkflowChanges.mock.calls.at(-1)[1];
  const runLabel = (wrapper) =>
    wrapper.find(".wftab-foot .app-btn--primary").text();
  const picker = (wrapper) =>
    wrapper.find('[data-testid="wftab-pick-checkpoint"] select');
  const loraRowsOf = (wrapper) =>
    wrapper.findAll("[data-testid='wftab-default-lora']");
  const WAITING = {
    models: [{ address: ADDRESS, filename: JUGGERNAUT.filename }],
  };
  /** The one lookup for the bar's verbs and the rows' controls. */
  const verb = (wrapper, name) => wrapper.find(`[data-testid="wftab-${name}"]`);
  const save = (wrapper) => verb(wrapper, "save");
  const discard = (wrapper) => verb(wrapper, "discard");
  const stage = (wrapper, name) => verb(wrapper, `stage-${name}`);

  /** A card whose checkpoint is named as the server names one: no extension. */
  const plain = (overrides = {}) =>
    card({
      models: [{ name: "realvisXL_v5", kind: "checkpoint", slot_label: "m1" }],
      ...overrides,
    });

  /** Mounted in the page, so focus can be followed. */
  async function mountEditor(cards = [plain()], keys = [KEY]) {
    const store = useWorkflowsStore();
    store.cards = cards;
    store.selectedKeys = keys;
    listWorkflowCards.mockResolvedValue({ cards, one_offs: 0, hidden: 0 });
    const wrapper = mount(WorkflowTab, editorOpts);
    mounted.push(wrapper);
    await flush(wrapper);
    return { wrapper, store };
  }

  /** Two workflows, each answering for its own detail. */
  function twoWorkflows() {
    const other = plain({ id: OTHER, name: "Other" });
    getWorkflowCard.mockImplementation((key) =>
      Promise.resolve(detail({ card: key === OTHER ? other : plain() })),
    );
    return [plain(), other];
  }

  /** A picture-less workflow: its LoRA rows are the graph's own loaders. */
  function chainDetail(overrides = {}) {
    const shown = detail({ card: plain(), ...overrides });
    shown.card.default_recipe.sampled = 0;
    return shown;
  }

  beforeEach(() => {
    readModelSwap
      .mockReset()
      .mockImplementation(replacementsByKind({ checkpoint: [JUGGERNAUT] }));
    getWorkflowCard.mockResolvedValue(detail({ card: plain() }));
    getLoraSummary.mockResolvedValue(loraSummary({ pictures: 0, varying: [] }));
  });

  describe("a model row", () => {
    it("joins the unsaved changes with a pick, and writes nothing to the workflow", async () => {
      const { wrapper } = await mountEditor();
      await settle(wrapper);
      expect(bar(wrapper).exists()).toBe(false);
      expect(runLabel(wrapper)).toBe("Run…");
      // Not missing, and still a picker: the saved file is its first option.
      expect(textOf(wrapper)).not.toContain("Checkpoint missing");
      expect(picker(wrapper).findAll("option").map((o) => o.text())).toEqual([
        "realvisXL_v5",
        "Juggernaut XL 9",
      ]);

      listWorkflowCards.mockClear();
      await picker(wrapper).setValue(JUGGERNAUT.filename);
      await flush(wrapper);

      expect(setWorkflowChanges).toHaveBeenCalledTimes(1);
      expect(setWorkflowChanges.mock.calls[0][0]).toBe(KEY);
      // This tab's own write is not echoed back over the socket, so the grid
      // is read again here: that is what puts Unsaved on the card.
      expect(listWorkflowCards).toHaveBeenCalled();
      expect(lastSet()).toEqual({
        models: [{ address: ADDRESS, filename: JUGGERNAUT.filename }],
        loras: [],
        skip_loras: [],
        stages: {},
        values: [],
      });
      // Nothing saved, nothing made: only the set.
      expect(saveWorkflow).not.toHaveBeenCalled();
      expect(patchWorkflowCard).not.toHaveBeenCalled();

      const row = wrapper.find('[data-testid="wftab-row-checkpoint"]');
      expect(row.find('[data-testid="wftab-edited"]').text()).toBe("Edited");
      expect(
        row.find('[data-testid="wftab-saved"]').text().replace(/\s+/g, " "),
      ).toBe("Saved: realvisXL_v5");
      expect(picker(wrapper).element.value).toBe(JUGGERNAUT.filename);
      expect(count(wrapper)).toBe("1 unsaved change");
      expect(runLabel(wrapper)).toBe("Run with changes…");
    });

    it("leaves the set when it is put back, by the reset or by picking the saved file", async () => {
      const { wrapper } = await mountEditor();
      await settle(wrapper);
      await picker(wrapper).setValue(JUGGERNAUT.filename);
      await flush(wrapper);

      const reset = wrapper.find('[data-testid="wftab-reset-model"]');
      expect(reset.attributes("aria-label")).toBe(
        "Put Checkpoint back to realvisXL_v5",
      );
      await reset.trigger("click");
      await flush(wrapper);
      // Removed, not kept as a change that changes nothing.
      expect(lastSet().models).toEqual([]);
      expect(bar(wrapper).exists()).toBe(false);
      expect(wrapper.find('[data-testid="wftab-edited"]').exists()).toBe(false);

      await picker(wrapper).setValue(JUGGERNAUT.filename);
      await flush(wrapper);
      await picker(wrapper).setValue("");
      await flush(wrapper);
      expect(lastSet().models).toEqual([]);
      expect(bar(wrapper).exists()).toBe(false);
    });

    it("draws what already waits on the server when the workflow is opened", async () => {
      waitingOnServer = {
        models: [{ address: ADDRESS, filename: JUGGERNAUT.filename }],
        stages: { upscale: true },
      };
      const { wrapper } = await mountEditor();
      await settle(wrapper);
      expect(getWorkflowChanges).toHaveBeenCalledWith(KEY);
      expect(count(wrapper)).toBe("2 unsaved changes");
      expect(picker(wrapper).element.value).toBe(JUGGERNAUT.filename);
      expect(wrapper.find('[data-testid="wftab-edited"]').exists()).toBe(true);
      expect(setWorkflowChanges).not.toHaveBeenCalled();
    });

    it("is text on a shelf loader, whose value is a shelf row and not a file", async () => {
      const shown = detail();
      shown.card.default_recipe.models = [
        {
          address: "core:m1/checkpoint_id",
          kind: "checkpoint",
          filename: "41",
          shelf_filename: "SDXL/realvisXL_v5.safetensors",
          provenance: "best",
        },
      ];
      getWorkflowCard.mockResolvedValue(shown);
      const { wrapper } = await mountEditor();
      await settle(wrapper);
      expect(readModelSwap).not.toHaveBeenCalled();
      expect(picker(wrapper).exists()).toBe(false);
      expect(
        wrapper.find('[data-testid="wftab-row-checkpoint"] .wftab-value').text(),
      ).toBe("realvisXL_v5");
      // And a set is not handed a row it cannot fill.
      expect(
        wrapper.findComponent({ name: "WorkflowUseSetMenu" }).props("models"),
      ).toEqual([]);
    });

    it("locks the rows while what waits could not be read, and says so with the way to ask again", async () => {
      getWorkflowChanges.mockReset().mockRejectedValue(new Error("offline"));
      const { wrapper } = await mountEditor();
      await settle(wrapper);
      const note = () => wrapper.find('[data-testid="wftab-changes-failed"]');
      const box = () => stage(wrapper, "upscale");
      const useSet = () => wrapper.find('[data-testid="wftab-use-set"]');
      expect(note().text()).toContain(
        "Could not read this workflow's unsaved changes just now",
      );
      // Not editable: a control that took the pick would show a value
      // nothing holds, with no way to make it again.
      expect(picker(wrapper).element.disabled).toBe(true);
      expect(box().element.disabled).toBe(true);
      expect(useSet().element.disabled).toBe(true);
      expect(
        wrapper.findComponent({ name: "WorkflowUseSetMenu" }).props("disabledReason"),
      ).toBe("wftab-changes-failed");
      await box().trigger("change");
      await flush(wrapper);
      expect(setWorkflowChanges).not.toHaveBeenCalled();
      expect(bar(wrapper).exists()).toBe(false);

      getWorkflowChanges
        .mockReset()
        .mockImplementation(() => Promise.resolve(changesState(null)));
      const retry = wrapper.find('[data-testid="wftab-changes-retry"]');
      retry.element.focus();
      await retry.trigger("click");
      await flush(wrapper);
      expect(getWorkflowChanges).toHaveBeenCalledWith(KEY);
      expect(note().exists()).toBe(false);
      expect(picker(wrapper).element.disabled).toBe(false);
      expect(box().element.disabled).toBe(false);
      // Retry left with its note: focus is on the heading of what it unlocked.
      expect(document.activeElement.textContent).toBe("Models");
    });

    it("keeps the rows locked until what waits has been read", async () => {
      const read = held();
      getWorkflowChanges.mockReset().mockReturnValue(read.promise);
      const { wrapper } = await mountEditor();
      await settle(wrapper);
      const box = () => stage(wrapper, "upscale");
      expect(picker(wrapper).element.disabled).toBe(true);
      expect(box().element.disabled).toBe(true);
      // Still being read is not a failure: nothing to retry yet.
      expect(wrapper.find('[data-testid="wftab-changes-failed"]').exists()).toBe(
        false,
      );
      read.release(changesState({ stages: { upscale: true } }));
      await flush(wrapper);
      expect(box().element.disabled).toBe(false);
      expect(box().element.checked).toBe(true);
      expect(picker(wrapper).element.disabled).toBe(false);
    });

    it("refuses, out loud, an edit that comes past the locked rows, and asks for the changes again", async () => {
      getWorkflowChanges.mockReset().mockRejectedValue(new Error("offline"));
      const { wrapper } = await mountEditor();
      await settle(wrapper);
      getWorkflowChanges.mockClear();
      // A menu left open hands its answer over whatever the rows say.
      wrapper.findComponent({ name: "WorkflowUseSetMenu" }).vm.$emit("use", {
        name: "Pony stack",
        models: [{ address: ADDRESS, filename: "pony/ponyV6.safetensors" }],
      });
      await flush(wrapper);
      expect(setWorkflowChanges).not.toHaveBeenCalled();
      expect(useNoticeStore().notices.at(-1)).toMatchObject({
        level: "error",
        text: "That change was not kept: this workflow's unsaved changes could not be read. Try again.",
      });
      // Asked again, so the next try can work.
      expect(getWorkflowChanges).toHaveBeenCalledWith(KEY);
    });
  });

  describe("Use a set…", () => {
    it("is handed the rows a set may fill, and fills them as unsaved changes", async () => {
      const { wrapper } = await mountEditor();
      await settle(wrapper);
      const menu = wrapper.findComponent({ name: "WorkflowUseSetMenu" });
      expect(menu.props("workflowId")).toBe(KEY);
      expect(menu.props("models")).toEqual([
        {
          address: ADDRESS,
          filename: "realvisXL_v5.safetensors",
          kind: "checkpoint",
          pick: null,
        },
      ]);

      menu.vm.$emit("use", {
        name: "Pony stack",
        models: [
          { address: ADDRESS, filename: "pony/ponyV6.safetensors" },
          // Not a row of this workflow: never written.
          { address: "core:zz/vae_name", filename: "x.safetensors" },
        ],
      });
      await flush(wrapper);

      expect(lastSet().models).toEqual([
        { address: ADDRESS, filename: "pony/ponyV6.safetensors" },
      ]);
      expect(saveWorkflow).not.toHaveBeenCalled();
      // A pick the shelf read did not list is still what the field shows.
      expect(picker(wrapper).element.value).toBe("pony/ponyV6.safetensors");
      expect(
        picker(wrapper).findAll("option").map((o) => o.text()),
      ).toContain("ponyV6");
      expect(count(wrapper)).toBe("1 unsaved change");
    });

    it("takes an earlier pick back off a row the set puts on its saved file", async () => {
      const { wrapper } = await mountEditor();
      await settle(wrapper);
      await picker(wrapper).setValue(JUGGERNAUT.filename);
      await flush(wrapper);
      const menu = wrapper.findComponent({ name: "WorkflowUseSetMenu" });
      // The menu is told what the row shows now, so it can tell.
      expect(menu.props("models")[0].pick).toBe(JUGGERNAUT.filename);

      menu.vm.$emit("use", {
        name: "The set it was made with",
        models: [{ address: ADDRESS, filename: "SDXL/realvisXL_v5.safetensors" }],
      });
      await flush(wrapper);
      // Gone from the set, not kept as a pick of the saved file.
      expect(lastSet().models).toEqual([]);
      expect(bar(wrapper).exists()).toBe(false);
      expect(wrapper.find('[data-testid="wftab-edited"]').exists()).toBe(false);
    });

    it("hands a set the file a row loads now: the owner's earlier replacement, not the file it replaced", async () => {
      // The plans are keyed by what each loader loads once the replacement
      // is applied, so that is the file the row has to be found by.
      getWorkflowCard.mockResolvedValue(
        detail({
          card: plain(),
          model_fixes: [
            {
              slot_label: "m1/ckpt_name",
              was: "realvisXL_v5.safetensors",
              now: "SDXL/realvisXL_v5_bf16.safetensors",
              slot_kind: "checkpoint",
            },
          ],
        }),
      );
      const { wrapper } = await mountEditor();
      await settle(wrapper);
      const menu = wrapper.findComponent({ name: "WorkflowUseSetMenu" });
      expect(menu.props("models")).toEqual([
        {
          address: ADDRESS,
          filename: "SDXL/realvisXL_v5_bf16.safetensors",
          kind: "checkpoint",
          pick: null,
        },
      ]);
      // A set that holds that very file leaves the row as saved.
      menu.vm.$emit("use", {
        name: "The set it runs on",
        models: [{ address: ADDRESS, filename: "realvisXL_v5_bf16.safetensors" }],
      });
      await flush(wrapper);
      expect(lastSet().models).toEqual([]);
    });

    describe("asked for from the grid", () => {
      const other = () => plain({ id: OTHER, name: "Other" });

      it("waits for the rows of the workflow asked for, never opening on the last one's", async () => {
        const slow = held();
        getWorkflowCard.mockImplementation((key) =>
          key === OTHER ? slow.promise : Promise.resolve(detail({ card: plain() })),
        );
        const { wrapper, store } = await mountEditor([plain(), other()]);
        await settle(wrapper);
        store.requestUseSet(OTHER);
        await flush(wrapper);
        expect(store.selectedKeys).toEqual([OTHER]);
        expect(useSetOpened).not.toHaveBeenCalled();

        slow.release(detail({ card: other() }));
        await flush(wrapper);
        await flush(wrapper);
        expect(useSetOpened).toHaveBeenCalledTimes(1);
        expect(useSetOpened).toHaveBeenCalledWith(OTHER);
      });

      it("waits for the unsaved changes to be read: the menu fills rows nothing can edit until then", async () => {
        const read = held();
        getWorkflowChanges.mockReset().mockReturnValue(read.promise);
        const { wrapper, store } = await mountEditor();
        await settle(wrapper);
        store.requestUseSet(KEY);
        await flush(wrapper);
        expect(useSetOpened).not.toHaveBeenCalled();

        read.release(changesState(null));
        await flush(wrapper);
        await flush(wrapper);
        expect(useSetOpened).toHaveBeenCalledTimes(1);
      });

      it("is taken back when the selection moves on before its rows are in", async () => {
        const slow = held();
        getWorkflowCard.mockImplementation((key) =>
          key === OTHER ? slow.promise : Promise.resolve(detail({ card: plain() })),
        );
        const { wrapper, store } = await mountEditor([plain(), other()]);
        await settle(wrapper);
        store.requestUseSet(OTHER);
        await flush(wrapper);
        store.selectedKeys = [KEY];
        await flush(wrapper);

        // Selected again later, by hand: nobody asked for the menu now.
        getWorkflowCard.mockImplementation((key) =>
          Promise.resolve(detail({ card: key === OTHER ? other() : plain() })),
        );
        store.selectedKeys = [OTHER];
        await flush(wrapper);
        await flush(wrapper);
        expect(useSetOpened).not.toHaveBeenCalled();
      });

      it("is taken back where no row could be filled, and says why", async () => {
        // A shelf loader: its value is a shelf row, which no set fills.
        const shelf = detail();
        shelf.card.default_recipe.models = [
          {
            address: "core:m1/checkpoint_id",
            kind: "checkpoint",
            filename: "41",
            shelf_filename: "SDXL/realvisXL_v5.safetensors",
            provenance: "best",
          },
        ];
        getWorkflowCard.mockImplementation((key) =>
          Promise.resolve(key === OTHER ? detail({ card: other() }) : shelf),
        );
        const { wrapper, store } = await mountEditor([plain(), other()]);
        await settle(wrapper);
        store.requestUseSet(KEY);
        await flush(wrapper);
        expect(useSetOpened).not.toHaveBeenCalled();
        expect(useNoticeStore().notices.at(-1)).toMatchObject({
          level: "info",
          text: "This workflow has no model a set could fill.",
        });

        // The workflow gains a row a set can fill and is selected again:
        // the menu stays shut, the wish was for then.
        getWorkflowCard.mockImplementation((key) =>
          Promise.resolve(detail({ card: key === OTHER ? other() : plain() })),
        );
        store.selectedKeys = [OTHER];
        await flush(wrapper);
        store.selectedKeys = [KEY];
        await flush(wrapper);
        await flush(wrapper);
        expect(useSetOpened).not.toHaveBeenCalled();
      });
    });

    it("opens on the workflow the selection bar asked for", async () => {
      const { wrapper, store } = await mountEditor();
      await settle(wrapper);
      useSidebarStore().workflowInspectorOpen = false;
      store.requestUseSet(KEY);
      await flush(wrapper);
      expect(useSetOpened).toHaveBeenCalledWith(KEY);
      expect(useSidebarStore().workflowInspectorOpen).toBe(true);
      // One-shot: taken, so it does not open again by itself.
      expect(store.useSetKey).toBe("");
    });
  });

  describe("the stages", () => {
    const mark = (wrapper) =>
      wrapper.find('[data-testid="wftab-stages-edited"]');

    it("are checkboxes: a toggle joins the changes, and back again leaves them", async () => {
      const { wrapper } = await mountEditor();
      expect(stage(wrapper, "upscale").element.checked).toBe(false);
      expect(mark(wrapper).exists()).toBe(false);

      await stage(wrapper, "upscale").setValue(true);
      await flush(wrapper);
      expect(lastSet().stages).toEqual({ upscale: true });
      expect(mark(wrapper).text()).toBe("Edited");
      expect(count(wrapper)).toBe("1 unsaved change");

      await stage(wrapper, "upscale").setValue(false);
      await flush(wrapper);
      expect(lastSet().stages).toEqual({});
      expect(mark(wrapper).exists()).toBe(false);
      expect(bar(wrapper).exists()).toBe(false);
    });
  });

  describe("a LoRA row", () => {
    const strength = (row) => row.find("[data-testid='wftab-lora-strength']");

    beforeEach(() => {
      getWorkflowCard.mockResolvedValue(chainDetail());
    });

    it("sets a loader's strength where it is read, and the saved one puts it back", async () => {
      const { wrapper } = await mountEditor();
      const neon = () => loraRowsOf(wrapper)[1];
      expect(strength(neon()).element.value).toBe("0.85");
      expect(strength(neon()).attributes("aria-label")).toBe(
        "Strength of neon-rain-v2",
      );

      await strength(neon()).setValue("0.5");
      await strength(neon()).trigger("change");
      await flush(wrapper);
      expect(lastSet().loras).toEqual([
        {
          node_id: "22",
          field: "lora_name",
          sha256: "s2",
          strength_model: 0.5,
          strength_clip: 0.5,
        },
      ]);
      expect(neon().find('[data-testid="wftab-lora-edited"]').text()).toBe(
        "Edited",
      );
      expect(
        neon().find('[data-testid="wftab-lora-saved"]').text().replace(/\s+/g, " "),
      ).toBe("Saved: 0.85");

      await neon().find('[data-testid="wftab-reset-strength"]').trigger("click");
      await flush(wrapper);
      expect(lastSet().loras).toEqual([]);
      expect(bar(wrapper).exists()).toBe(false);
    });

    it("keeps a strength half typed when an answer lands under it", async () => {
      // Typed, not committed: Enter or blur is what writes it. The tab is
      // drawn again meanwhile by whatever lands (here the list, renamed),
      // which used to put the row's number back so that Enter changed nothing.
      const { wrapper } = await mountEditor();
      const field = strength(loraRowsOf(wrapper)[1]);
      field.element.value = "0.5";
      const store = useWorkflowsStore();
      store.cards = store.cards.map((entry) => ({ ...entry, name: "Renamed" }));
      await flush(wrapper);
      expect(wrapper.find(".wftab-title").text()).toContain("Renamed");
      expect(field.element.value).toBe("0.5");

      await field.trigger("change");
      await flush(wrapper);
      expect(lastSet().loras.map((lora) => lora.strength_model)).toEqual([0.5]);
    });

    it("puts the field back, and writes nothing, for a strength that is not one", async () => {
      const { wrapper } = await mountEditor();
      const field = strength(loraRowsOf(wrapper)[1]);
      for (const typed of ["", "11", "abc"]) {
        field.element.value = typed;
        await field.trigger("change");
        await flush(wrapper);
        expect(field.element.value).toBe("0.85");
      }
      expect(setWorkflowChanges).not.toHaveBeenCalled();
    });

    it("removes a loader with ×, keeps its row until the save, and moves focus with the control", async () => {
      const { wrapper } = await mountEditor();
      const film = () => loraRowsOf(wrapper)[2];
      const remove = film().find('[data-testid="wftab-lora-remove"]');
      expect(remove.attributes("aria-label")).toBe("Remove film-grain-35mm");

      await remove.trigger("click");
      await flush(wrapper);
      expect(lastSet().skip_loras).toEqual([
        { node_id: "31", field: "lora_name" },
      ]);
      expect(loraRowsOf(wrapper)).toHaveLength(4);
      expect(film().find('[data-testid="wftab-lora-removed"]').text()).toBe(
        "Removed",
      );
      expect(film().find("s").text()).toBe("film-grain-35mm");
      expect(strength(film()).exists()).toBe(false);
      const putBack = film().find('[data-testid="wftab-lora-put-back"]');
      expect(putBack.attributes("aria-label")).toBe("Put film-grain-35mm back");
      // × left with the row's state: focus is on what took its place.
      expect(document.activeElement).toBe(putBack.element);

      await putBack.trigger("click");
      await flush(wrapper);
      expect(lastSet().skip_loras).toEqual([]);
      expect(film().find("s").exists()).toBe(false);
      expect(document.activeElement).toBe(
        film().find('[data-testid="wftab-lora-remove"]').element,
      );
    });

    it("drops a waiting strength when its loader is removed: a slot is set or removed, never both", async () => {
      const { wrapper } = await mountEditor();
      const neon = () => loraRowsOf(wrapper)[1];
      await strength(neon()).setValue("0.5");
      await strength(neon()).trigger("change");
      await flush(wrapper);
      await neon().find('[data-testid="wftab-lora-remove"]').trigger("click");
      await flush(wrapper);
      expect(lastSet().loras).toEqual([]);
      expect(lastSet().skip_loras).toEqual([
        { node_id: "22", field: "lora_name" },
      ]);
      expect(count(wrapper)).toBe("1 unsaved change");
    });

    it("can remove a loader the shelf does not know, but not set its strength", async () => {
      // A strength change names the shelf LoRA the loader holds; this one
      // has none to name.
      const { wrapper } = await mountEditor();
      const hair = loraRowsOf(wrapper)[3];
      expect(strength(hair).exists()).toBe(false);
      expect(hair.find(".wftab-chain-strength").text()).toBe("0.60");
      expect(hair.find('[data-testid="wftab-lora-remove"]').exists()).toBe(true);
    });
  });

  describe("the bar", () => {
    const saveAsNew = (wrapper) =>
      wrapper.find('[data-testid="wftab-save-as-new"]');
    const runButton = (wrapper) =>
      wrapper.find(".wftab-foot .app-btn--primary").element;
    /** A real click: focus goes to the button, then it is pressed. */
    const press = (button) => {
      button.element.focus();
      return button.trigger("click");
    };

    // Teal is "not saved yet": the filled verb is the ordinary one, the
    // outline the other, and neither is a second amber button beside Run….
    it("draws Save as the teal default and Save as new… as the outline beside it", async () => {
      waitingOnServer = { ...WAITING };
      const { wrapper } = await mountEditor();
      await settle(wrapper);
      expect(save(wrapper).classes()).toEqual(
        expect.arrayContaining(["app-btn--save", "app-btn--md"]),
      );
      expect(saveAsNew(wrapper).classes()).toEqual(
        expect.arrayContaining(["app-btn--outline", "app-btn--md"]),
      );
      expect(bar(wrapper).findAll(".app-btn--primary")).toHaveLength(0);
      // The same mark on the row that differs, and on nothing that is saved.
      expect(
        wrapper.find('[data-testid="wftab-edited"]').classes(),
      ).toContain("wftab-yours--unsaved");

      // Asking for the name, the verb that keeps is still the teal one.
      await saveAsNew(wrapper).trigger("click");
      await flush(wrapper);
      expect(
        wrapper.find('[data-testid="wftab-save-as-new-confirm"]').classes(),
      ).toContain("app-btn--save");
    });

    describe("arriving", () => {
      const arrival = (wrapper) =>
        wrapper.find('[data-testid="wftab-draft-arrival"]');
      const pulsing = (wrapper) =>
        bar(wrapper).classes().includes("wftab-draft--arrived");

      it("pulses and is announced once, when the first change is made", async () => {
        const { wrapper } = await mountEditor();
        await settle(wrapper);
        // There before anything waits: a live region that arrives with its
        // text already in it is not read out.
        expect(arrival(wrapper).attributes("role")).toBe("status");
        expect(arrival(wrapper).text()).toBe("");

        await stage(wrapper, "upscale").setValue(true);
        await flush(wrapper);
        expect(pulsing(wrapper)).toBe(true);
        expect(arrival(wrapper).text()).toBe("1 unsaved change");

        // A second change moves the bar's count and says nothing: the region
        // empties rather than keep a number that is no longer true.
        await picker(wrapper).setValue(JUGGERNAUT.filename);
        await flush(wrapper);
        expect(count(wrapper)).toBe("2 unsaved changes");
        expect(arrival(wrapper).text()).toBe("");
        expect(pulsing(wrapper)).toBe(true);

        // Back to one: the same number as the arrival, and still not said
        // again. The bar never left, so nothing has arrived.
        await picker(wrapper).setValue("");
        await flush(wrapper);
        expect(count(wrapper)).toBe("1 unsaved change");
        expect(arrival(wrapper).text()).toBe("");
        expect(pulsing(wrapper)).toBe(true);
      });

      it("is not played for changes that were already waiting", async () => {
        waitingOnServer = { ...WAITING };
        const { wrapper } = await mountEditor();
        await settle(wrapper);
        expect(count(wrapper)).toBe("1 unsaved change");
        expect(pulsing(wrapper)).toBe(false);
        expect(arrival(wrapper).text()).toBe("");
      });

      it("is not played again when the workflow is left and come back to", async () => {
        const cards = twoWorkflows();
        getWorkflowChanges.mockImplementation((key) =>
          Promise.resolve(
            key === OTHER
              ? changesState(null, { workflow_id: OTHER })
              : changesState(waitingOnServer),
          ),
        );
        const { wrapper, store } = await mountEditor(cards);
        await settle(wrapper);
        await stage(wrapper, "upscale").setValue(true);
        await flush(wrapper);
        expect(pulsing(wrapper)).toBe(true);

        store.selectedKeys = [OTHER];
        await settle(wrapper);
        expect(bar(wrapper).exists()).toBe(false);
        store.selectedKeys = [KEY];
        await settle(wrapper);
        // The change still waits, and finding it there is not it arriving.
        expect(count(wrapper)).toBe("1 unsaved change");
        expect(pulsing(wrapper)).toBe(false);
        expect(arrival(wrapper).text()).toBe("");
      });

      it("is over when the bar leaves, and comes back with the next first change", async () => {
        const { wrapper } = await mountEditor();
        await settle(wrapper);
        await stage(wrapper, "upscale").setValue(true);
        await flush(wrapper);
        await stage(wrapper, "upscale").setValue(false);
        await flush(wrapper);
        expect(bar(wrapper).exists()).toBe(false);
        expect(arrival(wrapper).text()).toBe("");

        await stage(wrapper, "upscale").setValue(true);
        await flush(wrapper);
        expect(pulsing(wrapper)).toBe(true);
        expect(arrival(wrapper).text()).toBe("1 unsaved change");
      });
    });

    it("says how many changes wait, never which, with the two verbs", async () => {
      waitingOnServer = { ...WAITING, stages: { upscale: true, face_detailer: true } };
      const { wrapper } = await mountEditor();
      expect(count(wrapper)).toBe("3 unsaved changes");
      expect(bar(wrapper).text()).not.toContain("juggernaut");
      expect(save(wrapper).text()).toBe("Save");
      expect(saveAsNew(wrapper).text()).toBe("Save as new…");
      // Teal, not amber: Run… stays the one amber button on the surface.
      expect(save(wrapper).classes()).toContain("app-btn--save");
      expect(wrapper.findAll(".app-btn--primary")).toHaveLength(1);
    });

    it("saves what waits over the workflow, then reads the rail again without blanking it", async () => {
      waitingOnServer = { ...WAITING };
      const { wrapper } = await mountEditor();
      await settle(wrapper);
      getWorkflowCard.mockClear();
      saveWorkflow.mockImplementation(() => {
        waitingOnServer = null;
        return Promise.resolve({ workflow_id: KEY, version: 2, left_out: [] });
      });
      const slow = held();
      getWorkflowCard.mockReturnValue(slow.promise);

      await press(save(wrapper));
      await flush(wrapper);

      expect(saveWorkflow).toHaveBeenCalledTimes(1);
      // The version the changes were read against goes with it (null here: an
      // automatic workflow never saved over), so a workflow changed since is
      // refused by the server.
      expect(saveWorkflow).toHaveBeenCalledWith(KEY, { version: null });
      expect(bar(wrapper).exists()).toBe(false);
      expect(document.activeElement).toBe(runButton(wrapper));
      expect(useNoticeStore().notices.at(-1).text).toBe(
        "Saved as version 2. The version menu takes it back.",
      );
      // The rows are read again in place: the panel never says "Reading…".
      expect(getWorkflowCard).toHaveBeenCalledWith(KEY);
      expect(textOf(wrapper)).not.toContain("Reading its default recipe");
      expect(wrapper.find('[data-testid="wftab-row-checkpoint"]').exists()).toBe(
        true,
      );
      slow.release(detail());
      await flush(wrapper);
    });

    // The grid is read again with the save's answer, and its card saying
    // nothing waits starts a read of the changes newer than the save's own.
    // The save's read is then dropped, and the newer one takes the bar away.
    it("holds the lock until the read that overtook the save's is drawn, and focuses Run… once the bar has left", async () => {
      waitingOnServer = { ...WAITING };
      const { wrapper } = await mountEditor([plain({ unsaved_changes: 1 })]);
      await settle(wrapper);
      saveWorkflow.mockImplementation(() => {
        waitingOnServer = null;
        return Promise.resolve({ workflow_id: KEY, version: 2, left_out: [] });
      });
      listWorkflowCards.mockResolvedValue({
        cards: [plain({ unsaved_changes: 0 })],
        one_offs: 0,
        hidden: 0,
      });
      const reads = [];
      getWorkflowChanges.mockImplementation(() => {
        reads.push(held());
        return reads.at(-1).promise;
      });
      // Every focus() from here on: on what, and whether the bar was drawn.
      const focused = [];
      const focus = HTMLElement.prototype.focus;
      const spy = vi
        .spyOn(HTMLElement.prototype, "focus")
        .mockImplementation(function record(...args) {
          focused.push({ on: this, barUp: bar(wrapper).exists() });
          return focus.apply(this, args);
        });
      try {
        await press(save(wrapper));
        focused.length = 0;
        await flush(wrapper);
        // The save's read, then the card's.
        expect(reads).toHaveLength(2);

        reads[0].release(changesState());
        await flush(wrapper);
        // Overtaken: nothing is drawn of it, and the save is not over. A
        // lock lifted here hands focus back to Save, which is about to go.
        expect(bar(wrapper).exists()).toBe(true);
        expect(save(wrapper).attributes("disabled")).toBeDefined();
        expect(picker(wrapper).element.disabled).toBe(true);
        expect(focused).toEqual([]);

        reads[1].release(changesState());
        await flush(wrapper);
        expect(bar(wrapper).exists()).toBe(false);
        expect(picker(wrapper).element.disabled).toBe(false);
        expect(focused).toEqual([{ on: runButton(wrapper), barUp: false }]);
        expect(document.activeElement).toBe(runButton(wrapper));
      } finally {
        spy.mockRestore();
      }
    });

    it("says a refused save in the bar, in the server's words, and reads the verbs again", async () => {
      waitingOnServer = { ...WAITING };
      const { wrapper } = await mountEditor();
      getWorkflowChanges.mockClear();
      saveWorkflow.mockRejectedValue({
        response: {
          status: 409,
          data: { detail: "This workflow has changed since these changes were made." },
        },
      });
      await save(wrapper).trigger("click");
      await flush(wrapper);
      const error = wrapper.find('[data-testid="wftab-draft-error"]');
      expect(error.attributes("role")).toBe("alert");
      expect(error.text()).toBe(
        "This workflow has changed since these changes were made.",
      );
      // Still waiting: a refusal discards nothing.
      expect(count(wrapper)).toBe("1 unsaved change");
      expect(getWorkflowChanges).toHaveBeenCalledWith(KEY);
    });

    it("draws no dead button for a verb the server closed: its reason is said instead", async () => {
      waitingOnServer = { ...WAITING };
      const reason =
        "A change of checkpoint is saved as a new workflow, so this one keeps its pictures.";
      getWorkflowChanges.mockImplementation(() =>
        Promise.resolve(
          changesState(waitingOnServer, { save: { open: false, reason } }),
        ),
      );
      const { wrapper } = await mountEditor();
      expect(save(wrapper).exists()).toBe(false);
      // The one verb left that keeps, so it is drawn as the filled one.
      expect(saveAsNew(wrapper).classes()).toContain("app-btn--save");
      expect(
        wrapper.findAll('[data-testid="wftab-draft-reason"]').map((p) => p.text()),
      ).toEqual([reason]);
    });

    it("says one sentence once when both verbs are closed for the same reason", async () => {
      waitingOnServer = { ...WAITING };
      const reason = "ComfyUI is not answering.";
      getWorkflowChanges.mockImplementation(() =>
        Promise.resolve(
          changesState(waitingOnServer, {
            save: { open: false, reason },
            save_as_new: { open: false, reason },
          }),
        ),
      );
      const { wrapper } = await mountEditor();
      expect(save(wrapper).exists()).toBe(false);
      expect(saveAsNew(wrapper).exists()).toBe(false);
      expect(wrapper.findAll('[data-testid="wftab-draft-reason"]')).toHaveLength(1);
      // Discard is always there.
      expect(wrapper.find('[data-testid="wftab-discard"]').exists()).toBe(true);
    });

    it("asks Save as new… its one question, the name, and selects the workflow it makes", async () => {
      waitingOnServer = { ...WAITING };
      const made = card({ id: OTHER, name: "Portrait, softer" });
      const { wrapper, store } = await mountEditor();
      await saveAsNew(wrapper).trigger("click");
      await flush(wrapper);

      const name = wrapper.find('[data-testid="wftab-new-name"]');
      expect(document.activeElement).toBe(name.element);
      expect(name.element.value).toBe("");
      const confirm = () =>
        wrapper.find('[data-testid="wftab-save-as-new-confirm"]');
      // No name, nothing to press.
      expect(confirm().attributes("disabled")).toBeDefined();

      // Back: the two verbs again, nothing written.
      await wrapper.find('[data-testid="wftab-new-name-back"]').trigger("click");
      await flush(wrapper);
      expect(name.exists() && wrapper.find('[data-testid="wftab-new-name"]').exists()).toBe(false);
      expect(document.activeElement).toBe(saveAsNew(wrapper).element);
      expect(saveWorkflowAsNew).not.toHaveBeenCalled();

      await saveAsNew(wrapper).trigger("click");
      await wrapper.find('[data-testid="wftab-new-name"]').setValue("  Portrait, softer ");
      listWorkflowCards.mockResolvedValue({
        cards: [card(), made],
        one_offs: 0,
        hidden: 0,
      });
      saveWorkflowAsNew.mockImplementation(() => {
        waitingOnServer = null;
        return Promise.resolve({
          workflow_id: OTHER,
          name: "Portrait, softer",
          new_workflow: true,
          left_out: [],
        });
      });
      await press(confirm());
      await flush(wrapper);
      await flush(wrapper);

      expect(saveWorkflowAsNew).toHaveBeenCalledWith(KEY, {
        name: "Portrait, softer",
      });
      expect(saveWorkflow).not.toHaveBeenCalled();
      expect(store.selectedKeys).toEqual([OTHER]);
      // The bar went with the selection: focus is on the new workflow's Run….
      expect(bar(wrapper).exists()).toBe(false);
      expect(document.activeElement).toBe(runButton(wrapper));
      expect(useNoticeStore().notices.at(-1).text).toBe(
        "Saved as a new workflow, Portrait, softer.",
      );
    });

    it("takes Enter in the name field as Save as new, and Escape as Back", async () => {
      waitingOnServer = { ...WAITING };
      const { wrapper } = await mountEditor();
      await saveAsNew(wrapper).trigger("click");
      await flush(wrapper);
      const name = () => wrapper.find('[data-testid="wftab-new-name"]');

      await name().trigger("keydown", { key: "Escape" });
      await flush(wrapper);
      expect(name().exists()).toBe(false);
      expect(saveWorkflowAsNew).not.toHaveBeenCalled();

      await saveAsNew(wrapper).trigger("click");
      await name().setValue("Softer");
      // Held, so the second Enter lands while the save is still out.
      const answer = held();
      saveWorkflowAsNew.mockImplementation(() => answer.promise);
      listWorkflowCards.mockResolvedValue({
        cards: [card(), card({ id: OTHER, name: "Softer" })],
        one_offs: 0,
        hidden: 0,
      });
      await name().trigger("keydown", { key: "Enter" });
      await name().trigger("keydown", { key: "Enter" });
      waitingOnServer = null;
      answer.release({ workflow_id: OTHER, name: "Softer", new_workflow: true, left_out: [] });
      await flush(wrapper);
      await flush(wrapper);
      expect(saveWorkflowAsNew).toHaveBeenCalledTimes(1);
      expect(saveWorkflowAsNew).toHaveBeenCalledWith(KEY, { name: "Softer" });
      // The field that held focus went with the question, and then the bar.
      expect(bar(wrapper).exists()).toBe(false);
      expect(document.activeElement).toBe(runButton(wrapper));
    });

    it("says which loaders a save left out because they hold a person's LoRA", async () => {
      waitingOnServer = { ...WAITING };
      const { wrapper } = await mountEditor();
      saveWorkflow.mockImplementation(() => {
        waitingOnServer = null;
        return Promise.resolve({
          workflow_id: KEY,
          version: 2,
          left_out: [{ node_id: "9", file: "ada.safetensors" }],
        });
      });
      await save(wrapper).trigger("click");
      await flush(wrapper);
      expect(useNoticeStore().notices.at(-1).text).toContain(
        "1 loader holding a person's LoRA was left out",
      );
    });

    it("discards without asking, and puts focus on Run…", async () => {
      waitingOnServer = { ...WAITING };
      const { wrapper } = await mountEditor();
      await press(wrapper.find('[data-testid="wftab-discard"]'));
      await flush(wrapper);
      expect(discardWorkflowChanges).toHaveBeenCalledTimes(1);
      expect(discardWorkflowChanges).toHaveBeenCalledWith(KEY);
      expect(wrapper.findComponent({ name: "ConfirmDialog" }).exists()).toBe(false);
      expect(bar(wrapper).exists()).toBe(false);
      expect(runLabel(wrapper)).toBe("Run…");
      expect(document.activeElement).toBe(runButton(wrapper));
    });

    // Nothing here pressed anything: the set was saved somewhere else, and
    // the grid's card says so.
    it.each([
      ["moves focus that was in the bar to Run…", save, runButton],
      ["leaves focus that was on a row where it is", picker, (wrapper) => picker(wrapper).element],
    ])(
      "%s when a save made elsewhere takes the bar away",
      async (_what, holder, after) => {
        waitingOnServer = { ...WAITING };
        const { wrapper, store } = await mountEditor([
          plain({ unsaved_changes: 1 }),
        ]);
        await settle(wrapper);
        holder(wrapper).element.focus();

        waitingOnServer = null;
        store.cards = [plain({ unsaved_changes: 0 })];
        await flush(wrapper);
        expect(bar(wrapper).exists()).toBe(false);
        expect(document.activeElement).toBe(after(wrapper));
      },
    );

    it.each([
      // The node asks for the unsaved graph and saves back (#1852).
      [
        { can_open_workflows: true, saves_back: true },
        "Open in ComfyUI opens the workflow with the unsaved changes in it. Saving there saves them too.",
      ],
      // Nodes from before that open a copy of the saved one.
      [
        { can_open_workflows: true, saves_back: false },
        "Open in ComfyUI opens the saved workflow. The unsaved changes are not in it until they are saved.",
      ],
    ])(
      "says what Open in ComfyUI opens while changes wait, and nothing otherwise (%o)",
      async (node, sentence) => {
        useFilterStore().comfyuiUrl = "http://127.0.0.1:8188";
        getPixlstashNode.mockResolvedValue(node);
        const { wrapper } = await mountEditor();
        const line = () => wrapper.find('[data-testid="wftab-open-unsaved"]');
        expect(line().exists()).toBe(false);
        await wrapper.find('[data-testid="wftab-stage-upscale"]').setValue(true);
        await flush(wrapper);
        expect(line().text().replace(/\s+/g, " ")).toBe(sentence);
        expect(
          wrapper.find('[data-testid="wftab-open-comfyui"]').attributes("aria-describedby"),
        ).toBe("wftab-open-unsaved");
      },
    );
  });

  describe("changes made on a version the workflow has left", () => {
    const STALE =
      "Open in ComfyUI opens the workflow as it is now. The unsaved changes were made on an earlier version and are not in it.";

    it.each([
      [{ can_open_workflows: true, saves_back: true }],
      // A copy of the saved graph has none of them either, and "until they
      // are saved" would promise a Save that is closed.
      [{ can_open_workflows: true, saves_back: false }],
    ])(
      "are not promised to be in what Open in ComfyUI opens (%o)",
      async (node) => {
        useFilterStore().comfyuiUrl = "http://127.0.0.1:8188";
        getPixlstashNode.mockResolvedValue(node);
        const shown = versioned(3, { models: plain().models });
        getWorkflowCard.mockResolvedValue(detail({ card: shown }));
        const { wrapper } = await mountEditor([shown]);
        await settle(wrapper);
        const line = () => wrapper.find('[data-testid="wftab-open-unsaved"]');
        await picker(wrapper).setValue(JUGGERNAUT.filename);
        await flush(wrapper);
        expect(line().text()).not.toContain("earlier version");

        // Going back a version: what waits was made on the one left, and the
        // graph ComfyUI is handed leaves it out.
        getWorkflowChanges.mockImplementation(() =>
          Promise.resolve(
            changesState(waitingOnServer, {
              version: 4,
              stale: true,
              save: {
                open: false,
                reason: "This workflow has changed since these changes were made.",
              },
            }),
          ),
        );
        await wrapper
          .find('[data-testid="wftab-version-menu"] [data-version="2"]')
          .trigger("click");
        await flush(wrapper);
        await flush(wrapper);

        expect(count(wrapper)).toBe("1 unsaved change");
        expect(line().text().replace(/\s+/g, " ")).toBe(STALE);
        // Still what the button is described by.
        expect(
          wrapper
            .find('[data-testid="wftab-open-comfyui"]')
            .attributes("aria-describedby"),
        ).toBe("wftab-open-unsaved");
      },
    );
  });

  describe("the bar while an edit's write is out", () => {
    const NOTHING = "There is nothing to save.";
    const CHECKPOINT =
      "A change of checkpoint is saved as a new workflow, so this one keeps its pictures.";
    const reasons = (wrapper) =>
      wrapper.findAll('[data-testid="wftab-draft-reason"]').map((p) => p.text());
    /** The changes routes as the server answers them: an empty set closes Save. */
    const answer = (set, extra = {}) =>
      changesState(set, {
        save: changesState(set).count
          ? { open: true, reason: null }
          : { open: false, reason: NOTHING },
        ...extra,
      });

    it("says the count and offers Discard on a first edit, and holds the verbs and their reasons for the answer", async () => {
      getWorkflowChanges.mockImplementation(() =>
        Promise.resolve(answer(waitingOnServer)),
      );
      const put = held();
      setWorkflowChanges.mockImplementation((_key, set) =>
        put.promise.then(() => {
          waitingOnServer = set;
          return answer(set);
        }),
      );
      const { wrapper } = await mountEditor();
      await wrapper.find('[data-testid="wftab-stage-upscale"]').setValue(true);
      await flush(wrapper);

      // What was read is about the set before the edit, an empty one: its
      // reason and its verbs say nothing of the one change being counted.
      expect(count(wrapper)).toBe("1 unsaved change");
      expect(verb(wrapper, "discard").exists()).toBe(true);
      expect(reasons(wrapper)).toEqual([]);
      expect(verb(wrapper, "save").exists()).toBe(false);
      expect(verb(wrapper, "save-as-new").exists()).toBe(false);

      put.release();
      await flush(wrapper);
      expect(verb(wrapper, "save").exists()).toBe(true);
      expect(verb(wrapper, "save-as-new").exists()).toBe(true);
      expect(reasons(wrapper)).toEqual([]);
    });

    it("prints no reason read for the set before the edit, and the answer's own once it is in", async () => {
      waitingOnServer = {
        models: [{ address: ADDRESS, filename: JUGGERNAUT.filename }],
      };
      const closed = { save: { open: false, reason: CHECKPOINT } };
      getWorkflowChanges.mockImplementation(() =>
        Promise.resolve(answer(waitingOnServer, closed)),
      );
      const put = held();
      setWorkflowChanges.mockImplementation((_key, set) =>
        put.promise.then(() => answer(set, closed)),
      );
      const { wrapper } = await mountEditor();
      expect(reasons(wrapper)).toEqual([CHECKPOINT]);

      await wrapper.find('[data-testid="wftab-stage-upscale"]').setValue(true);
      await flush(wrapper);
      expect(count(wrapper)).toBe("2 unsaved changes");
      expect(reasons(wrapper)).toEqual([]);
      // The verb that was drawn stays, so a click on it is not swallowed.
      expect(verb(wrapper, "save-as-new").exists()).toBe(true);
      expect(verb(wrapper, "save-as-new").element.disabled).toBe(false);

      put.release();
      await flush(wrapper);
      expect(reasons(wrapper)).toEqual([CHECKPOINT]);
    });

    it("keeps the reason of a stale set through an edit: editing it again does not end that", async () => {
      const SINCE = "This workflow has changed since these changes were made.";
      waitingOnServer = { stages: { upscale: true } };
      const stale = { stale: true, save: { open: false, reason: SINCE } };
      getWorkflowChanges.mockImplementation(() =>
        Promise.resolve(answer(waitingOnServer, stale)),
      );
      const put = held();
      setWorkflowChanges.mockImplementation((_key, set) =>
        put.promise.then(() => answer(set, stale)),
      );
      const { wrapper } = await mountEditor();
      await wrapper.find('[data-testid="wftab-stage-face_detailer"]').setValue(true);
      await flush(wrapper);
      expect(count(wrapper)).toBe("2 unsaved changes");
      expect(reasons(wrapper)).toEqual([SINCE]);
      put.release();
      await flush(wrapper);
      expect(reasons(wrapper)).toEqual([SINCE]);
    });
  });

  describe("changes Save is closed for, for another reason than the version", () => {
    it("are still said to be in what Open in ComfyUI opens: only a stale set is left out", async () => {
      useFilterStore().comfyuiUrl = "http://127.0.0.1:8188";
      getPixlstashNode.mockResolvedValue({ can_open_workflows: true, saves_back: true });
      waitingOnServer = {
        models: [{ address: ADDRESS, filename: JUGGERNAUT.filename }],
      };
      getWorkflowChanges.mockImplementation(() =>
        Promise.resolve(
          changesState(waitingOnServer, {
            stale: false,
            save: {
              open: false,
              reason:
                "A change of checkpoint is saved as a new workflow, so this one keeps its pictures.",
            },
          }),
        ),
      );
      const { wrapper } = await mountEditor();
      expect(
        wrapper
          .find('[data-testid="wftab-open-unsaved"]')
          .text()
          .replace(/\s+/g, " "),
      ).toBe(
        "Open in ComfyUI opens the workflow with the unsaved changes in it. Saving there saves them too.",
      );
    });
  });

  describe("one set, one writer at a time", () => {
    const useSetMenu = (wrapper) =>
      wrapper.findComponent({ name: "WorkflowUseSetMenu" });
    const SAVED = { workflow_id: KEY, version: 2, left_out: [] };

    it.each([
      ["Save", save, () => saveWorkflow, SAVED],
      ["Discard", discard, () => discardWorkflowChanges, changesState()],
    ])(
      "locks every control that edits the set while %s is out, so no edit can ride beside it",
      async (_verb, button, route, answer) => {
        // One of each kind of row: a pick, a strength, a removed loader.
        waitingOnServer = {
          ...WAITING,
          loras: [
            {
              node_id: "22",
              field: "lora_name",
              sha256: "s2",
              strength_model: 0.5,
              strength_clip: 0.5,
            },
          ],
          skip_loras: [{ node_id: "31", field: "lora_name" }],
        };
        getWorkflowCard.mockResolvedValue(chainDetail());
        const out = held();
        route().mockImplementation(() => out.promise);
        const { wrapper } = await mountEditor();
        await settle(wrapper);
        const editors = () =>
          [
            '[data-testid="wftab-pick-checkpoint"] select',
            '[data-testid="wftab-reset-model"]',
            '[data-testid="wftab-stage-upscale"]',
            '[data-testid="wftab-lora-strength"]',
            '[data-testid="wftab-reset-strength"]',
            '[data-testid="wftab-lora-remove"]',
            '[data-testid="wftab-lora-put-back"]',
            '[data-testid="wftab-use-set"]',
          ].map((selector) => wrapper.find(selector));
        expect(editors().map((control) => control.element.disabled)).toEqual(
          Array(8).fill(false),
        );

        await button(wrapper).trigger("click");
        await flush(wrapper);
        expect(route()).toHaveBeenCalledTimes(1);
        // `disabled` itself: out of the tab order, and said as unavailable.
        expect(editors().map((control) => control.element.disabled)).toEqual(
          Array(8).fill(true),
        );
        await stage(wrapper, "upscale").trigger("change");
        await flush(wrapper);
        expect(setWorkflowChanges).not.toHaveBeenCalled();

        waitingOnServer = null;
        out.release(answer);
        await flush(wrapper);
        await flush(wrapper);
        expect(bar(wrapper).exists()).toBe(false);
        expect(stage(wrapper, "upscale").element.disabled).toBe(false);
        expect(picker(wrapper).element.disabled).toBe(false);
      },
    );

    it("locks the rows while a go-back is out and its graph is read: they are about to say another version's", async () => {
      const shown = versioned(3, { models: plain().models });
      getWorkflowCard.mockResolvedValue(detail({ card: shown }));
      const { wrapper } = await mountEditor([shown]);
      await settle(wrapper);
      const back = held();
      restoreWorkflowVersion.mockImplementation(() => back.promise);
      await wrapper
        .find('[data-testid="wftab-version-menu"] [data-version="2"]')
        .trigger("click");
      await flush(wrapper);
      expect(stage(wrapper, "upscale").element.disabled).toBe(true);
      expect(picker(wrapper).element.disabled).toBe(true);

      // Answered, and the rows of the version it made still being read.
      const reread = held();
      getWorkflowCard.mockReturnValue(reread.promise);
      back.release(versionsAt(4));
      await flush(wrapper);
      expect(stage(wrapper, "upscale").element.disabled).toBe(true);

      reread.release(detail({ card: versioned(4, { models: plain().models }) }));
      await flush(wrapper);
      await flush(wrapper);
      expect(stage(wrapper, "upscale").element.disabled).toBe(false);
      expect(picker(wrapper).element.disabled).toBe(false);
    });

    it("refuses, out loud, an edit that comes past the locked rows while a save is out", async () => {
      waitingOnServer = { ...WAITING };
      const out = held();
      saveWorkflow.mockImplementation(() => out.promise);
      const { wrapper } = await mountEditor();
      await settle(wrapper);
      await save(wrapper).trigger("click");
      await flush(wrapper);
      // A menu opened before the press hands its answer over all the same.
      useSetMenu(wrapper).vm.$emit("use", {
        name: "Pony stack",
        models: [{ address: ADDRESS, filename: "pony/ponyV6.safetensors" }],
      });
      await flush(wrapper);
      expect(setWorkflowChanges).not.toHaveBeenCalled();
      expect(useNoticeStore().notices.at(-1)).toMatchObject({
        level: "error",
        text: "That change was not kept: this workflow's changes were being written. Make it again.",
      });
      waitingOnServer = null;
      out.release(SAVED);
      await flush(wrapper);
    });

    it("leaves the verbs open while an edit's write is out, and saves behind it", async () => {
      // A strength typed and Save clicked is one gesture: the blur starts the
      // edit's write, and a Save it disabled would swallow the click.
      waitingOnServer = { ...WAITING };
      const put = held();
      setWorkflowChanges.mockImplementation((_key, set) =>
        put.promise.then(() => {
          waitingOnServer = set;
          return changesState(set);
        }),
      );
      const { wrapper } = await mountEditor();
      await stage(wrapper, "upscale").setValue(true);
      expect(setWorkflowChanges).toHaveBeenCalledTimes(1);
      expect(save(wrapper).element.disabled).toBe(false);
      expect(discard(wrapper).element.disabled).toBe(false);
      expect(
        wrapper.find('[data-testid="wftab-save-as-new"]').element.disabled,
      ).toBe(false);

      await save(wrapper).trigger("click");
      await flush(wrapper);
      // Behind the edit, never beside it.
      expect(saveWorkflow).not.toHaveBeenCalled();
      put.release();
      await flush(wrapper);
      expect(saveWorkflow).toHaveBeenCalledTimes(1);
    });

    it("saves with the version read when Save was pressed, for the workflow it was pressed on", async () => {
      waitingOnServer = { ...WAITING };
      const cards = twoWorkflows();
      getWorkflowChanges.mockImplementation((key) =>
        Promise.resolve(
          key === OTHER
            ? changesState(null, { workflow_id: OTHER, version: null })
            : changesState(waitingOnServer, { version: 3 }),
        ),
      );
      const { wrapper, store } = await mountEditor(cards);
      await settle(wrapper);
      // A write the save has to wait behind: the hide, still on the wire.
      const hiding = held();
      patchWorkflowCard.mockImplementation(() => hiding.promise);
      await wrapper.findAll("button").find((b) => b.text() === "Hide").trigger("click");
      await save(wrapper).trigger("click");

      // The rail moves on, and the next workflow's changes are read.
      store.selectedKeys = [OTHER];
      await flush(wrapper);
      expect(saveWorkflow).not.toHaveBeenCalled();
      hiding.release(detail({ card: plain(), hidden: true }));
      await flush(wrapper);
      // Version 3, as read at the press: not the null of the workflow shown.
      expect(saveWorkflow).toHaveBeenCalledWith(KEY, { version: 3 });
    });

    it("does not let a save's re-read of a workflow the rail has left retire the reads of the one it shows", async () => {
      waitingOnServer = { ...WAITING };
      const cards = twoWorkflows();
      const { wrapper, store } = await mountEditor(cards);
      await settle(wrapper);
      // The save answers, and its read of what now waits is still out when
      // B is selected, B's own detail out too.
      const after = held();
      const slow = held();
      getWorkflowChanges.mockImplementation((key) =>
        key === OTHER
          ? Promise.resolve(changesState(null, { workflow_id: OTHER }))
          : after.promise,
      );
      await save(wrapper).trigger("click");
      await flush(wrapper);
      expect(saveWorkflow).toHaveBeenCalledTimes(1);
      getWorkflowCard.mockImplementation((key) =>
        key === OTHER ? slow.promise : Promise.resolve(detail({ card: plain() })),
      );
      store.selectedKeys = [OTHER];
      await flush(wrapper);

      after.release(changesState(null));
      await flush(wrapper);
      await flush(wrapper);
      slow.release(detail({ card: cards[1] }));
      await flush(wrapper);
      // B's own rows, which a read for A must not have thrown away.
      expect(wrapper.find('[data-testid="wftab-row-checkpoint"]').exists()).toBe(
        true,
      );
    });
  });

  describe("two edits in a hurry", () => {
    it("never lets the first write's answer draw over the second edit", async () => {
      const first = held();
      const second = held();
      const { wrapper } = await mountEditor();
      await settle(wrapper);
      setWorkflowChanges
        .mockImplementationOnce(() => first.promise)
        .mockImplementationOnce(() => second.promise);

      await wrapper.find('[data-testid="wftab-stage-upscale"]').setValue(true);
      await wrapper.find('[data-testid="wftab-stage-face_detailer"]').setValue(true);
      await flush(wrapper);
      // One write on the wire: the second edit waits behind it.
      expect(setWorkflowChanges).toHaveBeenCalledTimes(1);
      expect(count(wrapper)).toBe("2 unsaved changes");

      // The first answer, which knows of one change only, lands while the
      // second write is still out: the rail goes on showing both.
      first.release(changesState({ stages: { upscale: true } }));
      await flush(wrapper);
      expect(setWorkflowChanges).toHaveBeenCalledTimes(2);
      expect(lastSet().stages).toEqual({ upscale: true, face_detailer: true });
      expect(count(wrapper)).toBe("2 unsaved changes");
      expect(
        wrapper.find('[data-testid="wftab-stage-face_detailer"]').element.checked,
      ).toBe(true);

      second.release(
        changesState({ stages: { upscale: true, face_detailer: true } }),
      );
      await flush(wrapper);
      expect(count(wrapper)).toBe("2 unsaved changes");
    });

    it("does not let a late read of one workflow wipe what waits on the next", async () => {
      // A's read is still out when B is selected, and lands after B's.
      const slow = held();
      const cards = twoWorkflows();
      getWorkflowChanges.mockImplementation((key) =>
        key === KEY
          ? slow.promise
          : Promise.resolve(changesState({ stages: { face_detailer: true } })),
      );
      const { wrapper, store } = await mountEditor(cards);
      store.selectedKeys = [OTHER];
      await flush(wrapper);
      expect(count(wrapper)).toBe("1 unsaved change");

      slow.release(changesState({ stages: { upscale: true } }));
      await flush(wrapper);
      // B's own change, and none of A's.
      expect(count(wrapper)).toBe("1 unsaved change");
      expect(
        wrapper.find('[data-testid="wftab-stage-face_detailer"]').element.checked,
      ).toBe(true);
      expect(
        wrapper.find('[data-testid="wftab-stage-upscale"]').element.checked,
      ).toBe(false);
    });

    it("does not let an edit's answer for the workflow just left retire the read of the one arrived at", async () => {
      // A strength typed, then another card clicked: the blur sends A's
      // write and the click selects B, and A's answer comes back first.
      const cards = twoWorkflows();
      const put = held();
      const readB = held();
      getWorkflowChanges.mockImplementation((key) =>
        key === OTHER ? readB.promise : Promise.resolve(changesState(null)),
      );
      setWorkflowChanges.mockImplementation(() => put.promise);
      const { wrapper, store } = await mountEditor(cards);
      await wrapper.find('[data-testid="wftab-stage-upscale"]').setValue(true);
      store.selectedKeys = [OTHER];
      await flush(wrapper);

      put.release(changesState({ stages: { upscale: true } }));
      await flush(wrapper);
      readB.release(
        changesState({ stages: { face_detailer: true } }, { workflow_id: OTHER }),
      );
      await flush(wrapper);

      // B's own change is drawn, with its bar, and its rows can be edited.
      expect(count(wrapper)).toBe("1 unsaved change");
      const box = wrapper.find('[data-testid="wftab-stage-face_detailer"]');
      expect(box.element.checked).toBe(true);
      expect(box.element.disabled).toBe(false);
      expect(getWorkflowChanges.mock.calls.filter(([key]) => key === OTHER)).toHaveLength(1);
    });

    it("does not draw a workflow's old read when the rail comes straight back to it", async () => {
      // A's write answers while B is shown, so nothing here took it; back on
      // A, the read from before that write is not what waits any more.
      const cards = twoWorkflows();
      const put = held();
      setWorkflowChanges.mockImplementationOnce((_key, set) =>
        put.promise.then(() => {
          waitingOnServer = set;
          return changesState(set);
        }),
      );
      const { wrapper, store } = await mountEditor(cards);
      const box = (name) => stage(wrapper, name);
      await box("upscale").setValue(true);

      const again = held();
      getWorkflowChanges
        .mockReset()
        .mockImplementation((key) =>
          key === OTHER ? new Promise(() => {}) : again.promise,
        );
      store.selectedKeys = [OTHER];
      await flush(wrapper);
      put.release();
      await flush(wrapper);
      store.selectedKeys = [KEY];
      await flush(wrapper);

      // Not editable on the read from before the write: an edit built on
      // it would store a set without the change that write made.
      expect(box("face_detailer").element.disabled).toBe(true);
      await box("face_detailer").trigger("change");
      await flush(wrapper);
      expect(setWorkflowChanges).toHaveBeenCalledTimes(1);

      again.release(changesState(waitingOnServer));
      await flush(wrapper);
      expect(box("upscale").element.checked).toBe(true);
      await box("face_detailer").setValue(true);
      await flush(wrapper);
      expect(lastSet().stages).toEqual({ upscale: true, face_detailer: true });
    });

    it("does not draw the last workflow's changes while the next one's are being read", async () => {
      const cards = twoWorkflows();
      getWorkflowChanges.mockImplementation((key) =>
        key === OTHER
          ? new Promise(() => {})
          : Promise.resolve(changesState({ stages: { upscale: true } })),
      );
      const { wrapper, store } = await mountEditor(cards);
      expect(count(wrapper)).toBe("1 unsaved change");

      store.selectedKeys = [OTHER];
      await flush(wrapper);
      expect(bar(wrapper).exists()).toBe(false);
      expect(
        wrapper.find('[data-testid="wftab-stage-upscale"]').element.checked,
      ).toBe(false);
    });
  });

  describe("a workflow changed from somewhere else", () => {
    it("reads its rows again in place when the card's version moves", async () => {
      const before = versioned(3);
      getWorkflowCard.mockResolvedValue(detail({ card: before }));
      const { wrapper, store } = await mountEditor([before]);
      await settle(wrapper);

      // A save from the Run popup, the Tasks tab or ComfyUI: announced, the
      // grid re-read, and the card now says version 4.
      const after = versioned(4);
      const moved = detail({ card: after });
      moved.card.default_recipe.models[0].filename = "SDXL/juggernautXL_v9.safetensors";
      const slow = held();
      getWorkflowCard.mockClear().mockReturnValue(slow.promise);
      getLoraChain.mockClear();
      getWorkflowChanges.mockClear();
      store.cards = [after];
      await flush(wrapper);

      expect(getWorkflowCard).toHaveBeenCalledTimes(1);
      expect(getLoraChain).toHaveBeenCalledWith(KEY);
      expect(getWorkflowChanges).toHaveBeenCalledWith(KEY);
      // Not blanked while the read is out.
      expect(textOf(wrapper)).not.toContain("Reading its default recipe");
      expect(
        wrapper.find('[data-testid="wftab-row-checkpoint"]').text(),
      ).toContain("realvisXL_v5");

      slow.release(moved);
      await flush(wrapper);
      expect(
        wrapper.find('[data-testid="wftab-row-checkpoint"]').text(),
      ).toContain("juggernautXL_v9");
    });

    it("keeps the LoRA rows on screen while their chain is read again", async () => {
      const before = versioned(3, { models: plain().models });
      getWorkflowCard.mockResolvedValue(chainDetail({ card: before }));
      const { wrapper, store } = await mountEditor([before]);
      expect(loraRowsOf(wrapper)).toHaveLength(4);

      getLoraChain.mockReturnValue(new Promise(() => {}));
      store.cards = [versioned(4, { models: plain().models })];
      await flush(wrapper);
      expect(loraRowsOf(wrapper)).toHaveLength(4);
      expect(textOf(wrapper)).not.toContain("Reading its LoRAs");
    });

    it("keeps the LoRA rows on screen when the re-read of their chain fails", async () => {
      const before = versioned(3, { models: plain().models });
      getWorkflowCard.mockResolvedValue(chainDetail({ card: before }));
      const { wrapper, store } = await mountEditor([before]);
      expect(loraRowsOf(wrapper)).toHaveLength(4);

      getLoraChain.mockRejectedValue(new Error("offline"));
      store.cards = [versioned(4, { models: plain().models })];
      await flush(wrapper);
      await flush(wrapper);
      // What was read is still the best there is: not blanked for a failure.
      expect(loraRowsOf(wrapper)).toHaveLength(4);
      expect(textOf(wrapper)).not.toContain("Could not read its LoRAs just now");
    });

    it("locks the rows from the card's version moving until its graph has been read again", async () => {
      const before = versioned(3, { models: plain().models });
      getWorkflowCard.mockResolvedValue(detail({ card: before }));
      const { wrapper, store } = await mountEditor([before]);
      await settle(wrapper);
      const box = () => stage(wrapper, "upscale");
      expect(box().element.disabled).toBe(false);

      const after = versioned(4, { models: plain().models });
      const reread = held();
      getWorkflowCard.mockReturnValue(reread.promise);
      store.cards = [after];
      await flush(wrapper);
      // The rows still say version 3's saved values, and an edit is measured
      // against them: none is taken until version 4's are in.
      expect(box().element.disabled).toBe(true);
      expect(picker(wrapper).element.disabled).toBe(true);
      await box().trigger("change");
      await flush(wrapper);
      expect(setWorkflowChanges).not.toHaveBeenCalled();

      reread.release(detail({ card: after }));
      await flush(wrapper);
      await flush(wrapper);
      expect(box().element.disabled).toBe(false);
      expect(picker(wrapper).element.disabled).toBe(false);
    });

    it("puts a strength being typed back, commits nothing, and gives the field its focus back", async () => {
      const before = versioned(3, { models: plain().models });
      getWorkflowCard.mockResolvedValue(chainDetail({ card: before }));
      const { wrapper, store } = await mountEditor([before]);
      const field = () =>
        loraRowsOf(wrapper)[1].find("[data-testid='wftab-lora-strength']");
      // Half a number typed, not yet committed (no blur, no Enter).
      field().element.focus();
      field().element.value = "0.5";

      const after = versioned(4, { models: plain().models });
      const reread = held();
      getWorkflowCard.mockReturnValue(reread.promise);
      store.cards = [after];
      await flush(wrapper);
      // It was typed for a row that is being read again: put back, not kept
      // to be committed against a graph it was not typed for.
      expect(field().element.disabled).toBe(true);
      expect(field().element.value).toBe("0.85");
      // A browser drops the focus of a control as it is disabled, which this
      // DOM does not: so that it is handed back is read off the call.
      const focused = vi.spyOn(field().element, "focus");

      reread.release(chainDetail({ card: after }));
      await flush(wrapper);
      await flush(wrapper);
      expect(field().element.disabled).toBe(false);
      expect(field().element.value).toBe("0.85");
      expect(focused).toHaveBeenCalledTimes(1);
      expect(document.activeElement).toBe(field().element);
      expect(setWorkflowChanges).not.toHaveBeenCalled();
    });

    it("drops its bar when the changes were saved or discarded elsewhere", async () => {
      waitingOnServer = { stages: { upscale: true } };
      const waitingCard = card({ unsaved_changes: 1 });
      getWorkflowCard.mockResolvedValue(detail({ card: waitingCard }));
      const { wrapper, store } = await mountEditor([waitingCard]);
      expect(count(wrapper)).toBe("1 unsaved change");

      waitingOnServer = null;
      getWorkflowChanges.mockClear();
      store.cards = [card({ unsaved_changes: 0 })];
      await flush(wrapper);
      expect(getWorkflowChanges).toHaveBeenCalledWith(KEY);
      expect(bar(wrapper).exists()).toBe(false);
      expect(
        wrapper.find('[data-testid="wftab-stage-upscale"]').element.checked,
      ).toBe(false);
    });

    it("follows a save made from the Run popup or the Tasks tab in this window, which nothing announces", async () => {
      // The server skips the window that wrote, and a save of stages alone
      // moves neither the card's version nor, here, its count: the popup's
      // own signal is the only word of it.
      waitingOnServer = { stages: { upscale: true } };
      const { wrapper } = await mountEditor();
      await settle(wrapper);
      expect(count(wrapper)).toBe("1 unsaved change");

      waitingOnServer = null;
      const after = detail({ card: plain() });
      after.card.default_recipe.stages = { upscale: true, face_detailer: false };
      const slow = held();
      getWorkflowCard.mockClear().mockReturnValue(slow.promise);
      getWorkflowChanges.mockClear();
      getLoraChain.mockClear();
      useRunDialogStore().saved = { workflowId: KEY, savedAs: null, at: 1 };
      await flush(wrapper);

      expect(getWorkflowChanges).toHaveBeenCalledWith(KEY);
      expect(getWorkflowCard).toHaveBeenCalledWith(KEY);
      expect(getLoraChain).toHaveBeenCalledWith(KEY);
      expect(bar(wrapper).exists()).toBe(false);
      // In place: nothing blanked while the rows are read again.
      expect(textOf(wrapper)).not.toContain("Reading its default recipe");

      slow.release(after);
      await flush(wrapper);
      // Saved, so the stage is on as saved and no longer a change.
      expect(
        wrapper.find('[data-testid="wftab-stage-upscale"]').element.checked,
      ).toBe(true);
      expect(wrapper.find('[data-testid="wftab-stages-edited"]').exists()).toBe(
        false,
      );
    });

    it("reads the graph once when the popup's save moves the card's version too", async () => {
      const before = versioned(3, { models: plain().models });
      getWorkflowCard.mockResolvedValue(detail({ card: before }));
      const { wrapper, store } = await mountEditor([before]);
      await settle(wrapper);
      const after = versioned(4, { models: plain().models });
      getWorkflowCard.mockClear().mockResolvedValue(detail({ card: after }));
      getLoraChain.mockClear();
      getWorkflowFormInputs.mockClear();

      // The popup says so first; the grid's card catches up a moment later.
      useRunDialogStore().saved = { workflowId: KEY, savedAs: null, at: 1 };
      await flush(wrapper);
      store.cards = [after];
      await flush(wrapper);
      await flush(wrapper);
      expect(getWorkflowCard).toHaveBeenCalledTimes(1);
      expect(getLoraChain).toHaveBeenCalledTimes(1);
      expect(getWorkflowFormInputs).toHaveBeenCalledTimes(1);
    });

    it("leaves the rail alone when the popup saved another workflow", async () => {
      const { wrapper } = await mountEditor();
      await settle(wrapper);
      getWorkflowCard.mockClear();
      getWorkflowChanges.mockClear();
      useRunDialogStore().saved = { workflowId: OTHER, savedAs: null, at: 1 };
      await flush(wrapper);
      expect(getWorkflowCard).not.toHaveBeenCalled();
      expect(getWorkflowChanges).not.toHaveBeenCalled();
    });

    it("asks nothing again when the card only catches up with what the rail already holds", async () => {
      const { wrapper, store } = await mountEditor();
      await wrapper.find('[data-testid="wftab-stage-upscale"]').setValue(true);
      await flush(wrapper);
      getWorkflowChanges.mockClear();
      store.cards = [card({ unsaved_changes: 1 })];
      await flush(wrapper);
      expect(getWorkflowChanges).not.toHaveBeenCalled();
    });
  });

  describe("Edit LoRAs…", () => {
    beforeEach(() => {
      getWorkflowCard.mockResolvedValue(chainDetail());
    });

    async function openDialog(wrapper) {
      await wrapper.find("[data-testid='wftab-edit-loras']").trigger("click");
      return wrapper.findComponent({ name: "EditLorasDialog" });
    }

    it.each([
      ["Cancel", (dialog) => dialog.vm.$emit("close")],
      [
        "Apply",
        (dialog) => {
          dialog.vm.$emit("apply", {
            skip_loras: [{ node_id: "31", field: "lora_name" }],
            loras: [],
          });
          dialog.vm.$emit("close");
        },
      ],
      [
        "Save",
        (dialog) => {
          dialog.vm.$emit("overwritten", KEY);
          dialog.vm.$emit("close");
        },
      ],
    ])("puts focus back on Edit LoRAs… when the dialog ends in %s", async (_how, end) => {
      const { wrapper } = await mountEditor();
      const opener = wrapper.find("[data-testid='wftab-edit-loras']");
      const dialog = await openDialog(wrapper);
      document.activeElement?.blur?.();
      expect(document.activeElement).toBe(document.body);
      end(dialog);
      await flush(wrapper);
      expect(wrapper.findComponent({ name: "EditLorasDialog" }).exists()).toBe(
        false,
      );
      expect(document.activeElement).toBe(opener.element);
    });

    it("takes Apply's removals and strengths into the unsaved changes, over what waits", async () => {
      waitingOnServer = {
        stages: { upscale: true },
        // Waiting on the loader the dialog then removes.
        loras: [
          {
            node_id: "31",
            field: "lora_name",
            sha256: "s3",
            strength_model: 0.2,
            strength_clip: 0.2,
          },
        ],
      };
      const { wrapper } = await mountEditor();
      const dialog = await openDialog(wrapper);
      dialog.vm.$emit("apply", {
        skip_loras: [{ node_id: "31", field: "lora_name" }],
        loras: [
          {
            node_id: "22",
            field: "lora_name",
            sha256: "s2",
            strength_model: 0.5,
            strength_clip: 0.5,
          },
        ],
      });
      await flush(wrapper);

      expect(setWorkflowChanges).toHaveBeenCalledTimes(1);
      expect(lastSet()).toEqual({
        models: [],
        loras: [
          {
            node_id: "22",
            field: "lora_name",
            sha256: "s2",
            strength_model: 0.5,
            strength_clip: 0.5,
          },
        ],
        skip_loras: [{ node_id: "31", field: "lora_name" }],
        stages: { upscale: true },
        values: [],
      });
      expect(count(wrapper)).toBe("3 unsaved changes");
      expect(
        loraRowsOf(wrapper)[2].find('[data-testid="wftab-lora-removed"]').exists(),
      ).toBe(true);
    });

    it("stores what was waiting again after a structural Save made a version, less the loaders it took out", async () => {
      waitingOnServer = {
        stages: { upscale: true },
        skip_loras: [
          { node_id: "31", field: "lora_name" },
          { node_id: "14", field: "lora_name" },
        ],
      };
      const { wrapper } = await mountEditor();
      const dialog = await openDialog(wrapper);
      // The chain as the dialog's Save left it: loader 31 is gone.
      const [first, second] = loraChain().loaders;
      getLoraChain.mockResolvedValue(loraChain({ loaders: [first, second] }));
      const order = [];
      discardWorkflowChanges.mockImplementation(() => {
        order.push("discard");
        waitingOnServer = null;
        return Promise.resolve(changesState());
      });
      setWorkflowChanges.mockImplementation((_key, set) => {
        order.push("put");
        waitingOnServer = set;
        return Promise.resolve(changesState(set, { version: 2 }));
      });

      dialog.vm.$emit("overwritten", KEY);
      await flush(wrapper);
      await flush(wrapper);

      // Discarded, then stored against the new version: the same rows.
      expect(order).toEqual(["discard", "put"]);
      expect(lastSet().stages).toEqual({ upscale: true });
      expect(lastSet().skip_loras).toEqual([
        { node_id: "14", field: "lora_name" },
      ]);
      expect(count(wrapper)).toBe("2 unsaved changes");
      expect(wrapper.find('[data-testid="wftab-save"]').exists()).toBe(true);
    });

    it("stores again an edit whose write was still out when the dialog saved", async () => {
      waitingOnServer = { stages: { upscale: true } };
      const { wrapper } = await mountEditor();
      const dialog = await openDialog(wrapper);
      const put = held();
      setWorkflowChanges.mockImplementationOnce((_key, set) =>
        put.promise.then(() => {
          waitingOnServer = set;
          return changesState(set);
        }),
      );
      await wrapper.find('[data-testid="wftab-stage-face_detailer"]').setValue(true);
      expect(setWorkflowChanges).toHaveBeenCalledTimes(1);

      dialog.vm.$emit("overwritten", KEY);
      await flush(wrapper);
      // Behind the edit's write: what is stored again is read after it.
      expect(discardWorkflowChanges).not.toHaveBeenCalled();
      put.release();
      await flush(wrapper);
      await flush(wrapper);

      expect(discardWorkflowChanges).toHaveBeenCalledTimes(1);
      expect(setWorkflowChanges).toHaveBeenCalledTimes(2);
      expect(lastSet().stages).toEqual({ upscale: true, face_detailer: true });
      expect(count(wrapper)).toBe("2 unsaved changes");
    });

    it("locks the rows and the verbs while what waited is stored again", async () => {
      waitingOnServer = { stages: { upscale: true } };
      const { wrapper } = await mountEditor();
      const dialog = await openDialog(wrapper);
      const cleared = held();
      discardWorkflowChanges.mockImplementation(() =>
        cleared.promise.then(() => changesState()),
      );
      const box = () => stage(wrapper, "face_detailer");

      dialog.vm.$emit("overwritten", KEY);
      await flush(wrapper);
      await flush(wrapper);
      expect(discardWorkflowChanges).toHaveBeenCalledTimes(1);
      // Between the discard and the store there is nothing on the server:
      // an edit or a save made now would be of a set that is not there.
      expect(box().element.disabled).toBe(true);
      expect(save(wrapper).element.disabled).toBe(true);
      await box().trigger("change");
      await flush(wrapper);
      expect(setWorkflowChanges).not.toHaveBeenCalled();

      cleared.release();
      await flush(wrapper);
      await flush(wrapper);
      expect(lastSet().stages).toEqual({ upscale: true });
      expect(box().element.disabled).toBe(false);
      expect(save(wrapper).element.disabled).toBe(false);
    });

    it("keeps the rows locked through a second Save of the dialog that waits behind the first", async () => {
      waitingOnServer = { stages: { upscale: true } };
      const { wrapper } = await mountEditor();
      const first = held();
      const second = held();
      discardWorkflowChanges
        .mockImplementationOnce(() => first.promise.then(() => changesState()))
        .mockImplementationOnce(() => second.promise.then(() => changesState()));
      const box = () => stage(wrapper, "face_detailer");
      const dialog = await openDialog(wrapper);
      dialog.vm.$emit("overwritten", KEY);
      dialog.vm.$emit("overwritten", KEY);
      await flush(wrapper);
      await flush(wrapper);

      // The first is done; the second, queued behind it, has the set now.
      first.release();
      await flush(wrapper);
      await flush(wrapper);
      expect(discardWorkflowChanges).toHaveBeenCalledTimes(2);
      expect(box().element.disabled).toBe(true);

      second.release();
      await flush(wrapper);
      await flush(wrapper);
      expect(box().element.disabled).toBe(false);
      expect(count(wrapper)).toBe("1 unsaved change");
    });

    it("takes Apply behind a save that is out, over what then waits", async () => {
      waitingOnServer = { stages: { upscale: true } };
      const { wrapper } = await mountEditor();
      const dialog = await openDialog(wrapper);
      const out = held();
      saveWorkflow.mockImplementation(() => out.promise);
      await wrapper.find('[data-testid="wftab-save"]').trigger("click");
      await flush(wrapper);

      dialog.vm.$emit("apply", {
        skip_loras: [{ node_id: "31", field: "lora_name" }],
        loras: [],
      });
      await flush(wrapper);
      // Not beside the save, which would store the saved set again with it.
      expect(setWorkflowChanges).not.toHaveBeenCalled();

      waitingOnServer = null;
      out.release({ workflow_id: KEY, version: 2, left_out: [] });
      await flush(wrapper);
      await flush(wrapper);
      expect(setWorkflowChanges).toHaveBeenCalledTimes(1);
      expect(lastSet()).toEqual({
        models: [],
        loras: [],
        skip_loras: [{ node_id: "31", field: "lora_name" }],
        stages: {},
        values: [],
      });
      expect(count(wrapper)).toBe("1 unsaved change");
    });

    it("does not let the re-read of a workflow the rail has left retire the reads of the one it shows", async () => {
      const other = plain({ id: OTHER, name: "Other" });
      const { wrapper, store } = await mountEditor([plain(), other]);
      const dialog = await openDialog(wrapper);
      // An edit's write still out, so the dialog's Save waits its turn.
      const put = held();
      setWorkflowChanges.mockImplementationOnce(() => put.promise);
      await wrapper.find('[data-testid="wftab-stage-upscale"]').setValue(true);
      dialog.vm.$emit("overwritten", KEY);
      await flush(wrapper);

      const slow = held();
      getWorkflowCard.mockImplementation((key) =>
        key === OTHER ? slow.promise : Promise.resolve(chainDetail()),
      );
      store.selectedKeys = [OTHER];
      await flush(wrapper);
      put.release(changesState({ stages: { upscale: true } }));
      await flush(wrapper);
      await flush(wrapper);
      slow.release(chainDetail({ card: other }));
      await flush(wrapper);
      // B's own rows, which a read for A must not have thrown away.
      expect(wrapper.find('[data-testid="wftab-row-checkpoint"]').exists()).toBe(
        true,
      );
    });

    it("reads the graph once when its Save moves the card's version", async () => {
      const before = versioned(3, { models: plain().models });
      getWorkflowCard.mockResolvedValue(chainDetail({ card: before }));
      const { wrapper, store } = await mountEditor([before]);
      const dialog = await openDialog(wrapper);
      const after = versioned(4, { models: plain().models });
      getWorkflowCard.mockClear().mockResolvedValue(chainDetail({ card: after }));
      listWorkflowCards.mockResolvedValue({ cards: [after], one_offs: 0, hidden: 0 });
      getLoraChain.mockClear();

      dialog.vm.$emit("overwritten", KEY);
      await flush(wrapper);
      await flush(wrapper);
      // The grid's card has caught up, and asked for nothing again.
      expect(store.cards[0].version).toBe(4);
      expect(getWorkflowCard).toHaveBeenCalledTimes(1);
      expect(getLoraChain).toHaveBeenCalledTimes(1);
    });

    it("leaves what waits as it is, and says so, when the new version's LoRAs cannot be read", async () => {
      waitingOnServer = {
        stages: { upscale: true },
        skip_loras: [{ node_id: "31", field: "lora_name" }],
      };
      const { wrapper } = await mountEditor();
      const dialog = await openDialog(wrapper);
      getLoraChain.mockRejectedValue(new Error("offline"));

      dialog.vm.$emit("overwritten", KEY);
      await flush(wrapper);
      await flush(wrapper);
      // Neither guess is taken: keeping every LoRA change may name a loader
      // the edit took out, and dropping them loses ones that are still there.
      expect(discardWorkflowChanges).not.toHaveBeenCalled();
      expect(setWorkflowChanges).not.toHaveBeenCalled();
      expect(count(wrapper)).toBe("2 unsaved changes");
      expect(useNoticeStore().notices.at(-1)).toMatchObject({
        level: "error",
        text: "The LoRA edit was saved. The changes that were waiting are left as they were: its LoRAs could not be read to store them against the new version.",
      });
      // The rows are let go again.
      expect(
        wrapper.find('[data-testid="wftab-stage-upscale"]').element.disabled,
      ).toBe(false);
    });

    it("stores what waited again for the workflow the dialog saved, whatever the rail shows by then", async () => {
      waitingOnServer = {
        stages: { upscale: true },
        skip_loras: [
          { node_id: "31", field: "lora_name" },
          { node_id: "14", field: "lora_name" },
        ],
      };
      const other = plain({ id: OTHER, name: "Other" });
      const { wrapper, store } = await mountEditor([plain(), other]);
      const dialog = await openDialog(wrapper);
      // The rail moves on with the dialog still open on the first workflow.
      getWorkflowChanges.mockImplementation((key) =>
        Promise.resolve(
          key === OTHER
            ? changesState(null, { workflow_id: OTHER })
            : changesState(waitingOnServer),
        ),
      );
      store.selectedKeys = [OTHER];
      await flush(wrapper);
      expect(loraRowsOf(wrapper)).toHaveLength(4);

      // The chain as the dialog's Save left it, asked for by id: loader 31 is
      // gone from THAT workflow. The one on screen keeps its four.
      const [first, second] = loraChain().loaders;
      getLoraChain.mockClear().mockImplementation((key) =>
        Promise.resolve(
          key === KEY ? loraChain({ loaders: [first, second] }) : loraChain(),
        ),
      );
      const cleared = held();
      discardWorkflowChanges.mockImplementation(() =>
        cleared.promise.then(() => {
          waitingOnServer = null;
          return changesState();
        }),
      );
      dialog.vm.$emit("overwritten", KEY);
      await flush(wrapper);
      await flush(wrapper);
      expect(getLoraChain.mock.calls).toEqual([[KEY]]);
      expect(discardWorkflowChanges).toHaveBeenCalledWith(KEY);
      expect(loraRowsOf(wrapper)).toHaveLength(4);

      // Back on it mid-turn: the rows are locked by the same mark.
      store.selectedKeys = [KEY];
      await flush(wrapper);
      expect(
        wrapper.find('[data-testid="wftab-stage-face_detailer"]').element.disabled,
      ).toBe(true);

      cleared.release();
      await flush(wrapper);
      await flush(wrapper);
      expect(setWorkflowChanges).toHaveBeenCalledTimes(1);
      expect(setWorkflowChanges.mock.calls[0][0]).toBe(KEY);
      expect(lastSet().stages).toEqual({ upscale: true });
      expect(lastSet().skip_loras).toEqual([
        { node_id: "14", field: "lora_name" },
      ]);
      expect(count(wrapper)).toBe("2 unsaved changes");
      expect(
        wrapper.find('[data-testid="wftab-stage-face_detailer"]').element.disabled,
      ).toBe(false);
    });

    it("says so when the changes carried over can no longer be saved", async () => {
      waitingOnServer = { stages: { upscale: true } };
      const { wrapper } = await mountEditor();
      const dialog = await openDialog(wrapper);
      const reason = "These changes cannot be saved over this version.";
      setWorkflowChanges.mockImplementation((_key, set) => {
        waitingOnServer = set;
        return Promise.resolve(
          changesState(set, { save: { open: false, reason } }),
        );
      });
      dialog.vm.$emit("overwritten", KEY);
      await flush(wrapper);
      await flush(wrapper);
      expect(useNoticeStore().notices.at(-1)).toMatchObject({
        level: "error",
        text: reason,
      });
    });
  });
});
