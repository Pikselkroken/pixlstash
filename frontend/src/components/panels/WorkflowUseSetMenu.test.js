// *Change model set…* in the Workflow tab's Models head (#1850, part 3).
//
// What can silently go wrong here: the whole shelf read for every card that is
// merely selected, a row filled with the file it already loads (an unsaved
// change that changes nothing), a set that cannot be used going grey with no
// reason, another workflow's plans drawn on this one, and focus left on the
// page body when the menu closes.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { h, ref } from "vue";
import { flushPromises, mount } from "@vue/test-utils";

const fetchWorkflowSets = vi.fn();
const planSetClones = vi.fn();

vi.mock("../../api/modelShelf", () => ({
  fetchWorkflowSets: (...args) => fetchWorkflowSets(...args),
}));
vi.mock("../../api/workflows", () => ({
  planSetClones: (...args) => planSetClones(...args),
}));

import WorkflowUseSetMenu from "./WorkflowUseSetMenu.vue";

// Vuetify is not installed in the test app, so `v-menu` is stubbed with the
// two behaviours the component relies on: the activator's click toggles it,
// and the rows are in the page only while it is open. `afterEnter` is the
// overlay's own event, emitted by hand where a test needs the menu "drawn".
const VMenuStub = {
  name: "VMenu",
  props: { modelValue: { type: Boolean, default: false } },
  emits: ["update:modelValue", "afterEnter"],
  setup(props, { slots, emit }) {
    // As `v-menu` does, the activator's props carry a `ref` of the menu's
    // own. A component that also puts a `ref` on its button loses one of the
    // two, and in a browser it was its own: the button it then could not find
    // was the one focus had to go back to.
    const activator = ref(null);
    return () =>
      h("div", { class: "v-menu-stub" }, [
        slots.activator?.({
          props: {
            ref: activator,
            onClick: () => emit("update:modelValue", !props.modelValue),
          },
        }),
        props.modelValue ? slots.default?.() : null,
      ]);
  },
};

// The tab's model rows: a checkpoint in a folder, a VAE, a text encoder.
const MODELS = [
  { address: "4.ckpt_name", filename: "sdxl/realvisxl_v50.safetensors" },
  { address: "9.vae_name", filename: "sdxl_vae.safetensors" },
  { address: "11.clip_name", filename: "clip_l.safetensors" },
];

const SHELF = {
  combinations: [
    {
      key: "c1",
      picture_count: 4,
      models: [
        {
          id: 31,
          kind: "checkpoint",
          // Nobody named it: the shelf reads its file, never `.safetensors`.
          name: "juggernautXL_v9.safetensors",
          filename: "juggernautXL_v9.safetensors",
        },
        { id: 32, kind: "vae", name: "sdxl vae", filename: "sdxl_vae.safetensors" },
        { id: 33, kind: "adapter", name: "mara", filename: "mara_v3.safetensors" },
      ],
    },
  ],
  no_set: [],
  hand_made: [
    {
      id: 7,
      name: "Portraits",
      members: [
        // Off the shelf: never sent, so the plan is not refused for it.
        { id: 10, slot: "checkpoint", on_shelf: false, name: "gone" },
        { id: 11, slot: "checkpoint", on_shelf: true, name: "Dreamshaper" },
        { id: 12, slot: "vae", on_shelf: true, name: "fp16 vae" },
        { id: 13, slot: "text_encoder", on_shelf: true, name: "clip l" },
      ],
    },
    {
      id: 8,
      name: "Flux dev",
      members: [{ id: 21, slot: "checkpoint", on_shelf: true, name: "flux dev" }],
    },
    {
      id: 9,
      name: "What it has",
      members: [
        { id: 22, slot: "checkpoint", on_shelf: true, name: "realvis" },
        // A hand-made set's LoRA is never asked about as a model.
        { id: 23, slot: "lora", on_shelf: true, name: "mara" },
      ],
    },
  ],
};

const plan = (key, extra = {}) => ({
  key,
  fit: "same_base_model",
  reason: null,
  swaps: {},
  ...extra,
});

const PLANS = {
  base_filename: "sdxl/realvisxl_v50.safetensors",
  base_model: "SDXL",
  plans: [
    plan("hand:7", {
      swaps: {
        "sdxl/realvisxl_v50.safetensors": "sdxl/dreamshaperXL.safetensors",
        // Spelled unlike the row's file: still the same file.
        "vae/SDXL_VAE.safetensors": "vae/sdxl-vae-fp16.safetensors",
        // The file the row already loads, under another folder and case.
        "clip_l.safetensors": "text/CLIP_L.safetensors",
      },
    }),
    plan("hand:8", { fit: "wont_load", reason: "Needs a UNETLoader" }),
    plan("hand:9"),
    plan("model:31", {
      swaps: { "sdxl/realvisxl_v50.safetensors": "juggernautXL_v9.safetensors" },
    }),
  ],
};

const mounted = [];
afterEach(() => {
  while (mounted.length) mounted.pop().unmount();
  vi.restoreAllMocks();
});

beforeEach(() => {
  fetchWorkflowSets.mockReset().mockResolvedValue(SHELF);
  planSetClones.mockReset().mockResolvedValue(PLANS);
});

function mountMenu(props = {}) {
  const wrapper = mount(WorkflowUseSetMenu, {
    props: { workflowId: "a", models: MODELS, ...props },
    attachTo: document.body,
    global: { stubs: { VMenu: VMenuStub, VIcon: true, Tooltip: true } },
  });
  mounted.push(wrapper);
  return wrapper;
}

const button = (wrapper) => wrapper.find('[data-testid="wftab-use-set"]');
const menu = (wrapper) => wrapper.find('[data-testid="wftab-use-set-menu"]');
const row = (wrapper, key) => wrapper.find(`[data-set="${key}"]`);
const meta = (wrapper, key) => row(wrapper, key).find(".ctx-meta").text();
const unread = (wrapper) =>
  wrapper.find('[data-testid="wftab-use-set-unread"]');

async function openMenu(wrapper) {
  await button(wrapper).trigger("click");
  await flushPromises();
}

/** A promise and the function that settles it, for an answer held back. */
function held() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("the button", () => {
  it("says what it is and that it opens a menu, and reads nothing yet", () => {
    const wrapper = mountMenu();
    expect(button(wrapper).text()).toBe("Change model set…");
    expect(button(wrapper).attributes("aria-haspopup")).toBe("menu");
    expect(button(wrapper).attributes("aria-expanded")).toBe("false");
    // A row acts at once: the button says what choosing one does.
    expect(button(wrapper).find("tooltip-stub").attributes("text")).toBe(
      "Fill the model rows from one of your sets, as unsaved changes",
    );
    // The shelf is read when somebody asks, not for every selected card.
    expect(fetchWorkflowSets).not.toHaveBeenCalled();
    expect(planSetClones).not.toHaveBeenCalled();
  });

  it("is disabled with its reason named, and then opens from nowhere", async () => {
    const wrapper = mountMenu({ disabled: true, disabledReason: "why-not" });
    expect(button(wrapper).attributes("disabled")).toBeDefined();
    expect(button(wrapper).attributes("aria-describedby")).toBe("why-not");
    wrapper.vm.open();
    await flushPromises();
    expect(menu(wrapper).exists()).toBe(false);
    expect(fetchWorkflowSets).not.toHaveBeenCalled();
  });

  it("carries no reason while it is enabled", () => {
    const wrapper = mountMenu({ disabledReason: "why-not" });
    expect(button(wrapper).attributes("aria-describedby")).toBeUndefined();
  });
});

describe("opening", () => {
  it("reads the shelf once and asks for every set's plan in one request", async () => {
    const wrapper = mountMenu();
    await openMenu(wrapper);
    expect(button(wrapper).attributes("aria-expanded")).toBe("true");
    expect(fetchWorkflowSets).toHaveBeenCalledTimes(1);
    expect(planSetClones).toHaveBeenCalledTimes(1);
    // Hand-made sets first; a set's LoRAs and off-shelf files are not sent.
    expect(planSetClones).toHaveBeenCalledWith("a", [
      { key: "hand:7", checkpoint_ids: [11], model_ids: [12, 13] },
      { key: "hand:8", checkpoint_ids: [21], model_ids: [] },
      { key: "hand:9", checkpoint_ids: [22], model_ids: [] },
      { key: "model:31", checkpoint_ids: [31], model_ids: [32] },
    ]);
  });

  it("offers no set hidden in this library, of either kind (#1869)", async () => {
    fetchWorkflowSets.mockResolvedValue({
      ...SHELF,
      hidden_sets: ["hand:8", "model:31"],
    });
    const wrapper = mountMenu();
    await openMenu(wrapper);
    // The hide took exactly its own two: no plan is asked for either, and
    // neither has a row.
    expect(planSetClones.mock.calls[0][1].map((set) => set.key)).toEqual([
      "hand:7",
      "hand:9",
    ]);
    expect(row(wrapper, "hand:8").exists()).toBe(false);
    expect(row(wrapper, "model:31").exists()).toBe(false);
    expect(row(wrapper, "hand:7").exists()).toBe(true);
  });

  it("offers no set named after a hidden model, and fills in no hidden member", async () => {
    // 21 heads nothing from pictures; 31 does, so its set goes with it.
    fetchWorkflowSets.mockResolvedValue({ ...SHELF, hidden_models: [31] });
    let wrapper = mountMenu();
    await openMenu(wrapper);
    expect(planSetClones.mock.calls[0][1].map((set) => set.key)).toEqual([
      "hand:7",
      "hand:8",
      "hand:9",
    ]);

    // A hidden support file leaves the set offered, without that file.
    planSetClones.mockClear();
    fetchWorkflowSets.mockResolvedValue({ ...SHELF, hidden_models: [32] });
    wrapper = mountMenu();
    await openMenu(wrapper);
    expect(planSetClones.mock.calls[0][1].at(-1)).toEqual({
      key: "model:31",
      checkpoint_ids: [31],
      model_ids: [],
    });
  });

  it("says the sets are being read until they are", async () => {
    const answer = held();
    planSetClones.mockReturnValue(answer.promise);
    const wrapper = mountMenu();
    await openMenu(wrapper);
    expect(unread(wrapper).text()).toBe("Reading your sets…");
    expect(unread(wrapper).attributes("aria-disabled")).toBe("true");
    answer.resolve(PLANS);
    await flushPromises();
    expect(unread(wrapper).exists()).toBe(false);
    expect(row(wrapper, "hand:7").exists()).toBe(true);
  });

  it("says so when the owner has no sets, without asking for plans", async () => {
    fetchWorkflowSets.mockResolvedValue({ combinations: [], hand_made: [] });
    const wrapper = mountMenu();
    await openMenu(wrapper);
    expect(unread(wrapper).text()).toBe("You have no sets yet.");
    expect(planSetClones).not.toHaveBeenCalled();
  });
});

describe("the rows", () => {
  it("name each set as the shelf does and count the rows it fills", async () => {
    const wrapper = mountMenu();
    await openMenu(wrapper);
    expect(
      wrapper.findAll("[data-set]").map((entry) => entry.attributes("data-set")),
    ).toEqual(["hand:7", "hand:8", "hand:9", "model:31"]);
    expect(row(wrapper, "hand:7").find(".ctx-label-text").text()).toBe(
      "Portraits",
    );
    // The text encoder is left out: its swap is the file it already loads.
    expect(meta(wrapper, "hand:7")).toBe("fills 2 rows");
    // Said whole: the two spans alone would be read run together.
    expect(row(wrapper, "hand:7").attributes("aria-label")).toBe(
      "Portraits, fills 2 rows",
    );
    expect(row(wrapper, "hand:7").attributes("aria-disabled")).toBeUndefined();
    // A set read from pictures is named after its checkpoint, never its file.
    const evidence = row(wrapper, "model:31").find(".ctx-label-text").text();
    expect(evidence).toMatch(/juggernaut/i);
    expect(evidence).not.toMatch(/safetensors/);
  });

  it("say that a set's LoRAs are not added", async () => {
    const wrapper = mountMenu();
    await openMenu(wrapper);
    expect(meta(wrapper, "model:31")).toBe(
      "fills 1 row · its LoRAs are not added",
    );
    // Only a set that carries any.
    expect(meta(wrapper, "hand:7")).not.toContain("LoRA");
  });

  it("keep a set that will not load, disabled, with the server's reason", async () => {
    const wrapper = mountMenu();
    await openMenu(wrapper);
    expect(meta(wrapper, "hand:8")).toBe("Needs a UNETLoader");
    expect(row(wrapper, "hand:8").attributes("aria-disabled")).toBe("true");
    expect(row(wrapper, "hand:8").classes()).toContain("ctx-item--disabled");
    expect(row(wrapper, "hand:8").attributes("aria-label")).toBe(
      "Flux dev, Needs a UNETLoader",
    );
    await row(wrapper, "hand:8").trigger("click");
    expect(wrapper.emitted("use")).toBeUndefined();
    expect(menu(wrapper).exists()).toBe(true);
  });

  it("disable a set that changes none of the rows", async () => {
    const wrapper = mountMenu();
    await openMenu(wrapper);
    expect(meta(wrapper, "hand:9")).toBe("changes nothing here");
    expect(row(wrapper, "hand:9").attributes("aria-disabled")).toBe("true");
    await row(wrapper, "hand:9").trigger("click");
    expect(wrapper.emitted("use")).toBeUndefined();
  });

  it("disable a set whose files the tab has no row for", async () => {
    const wrapper = mountMenu({
      models: [{ address: "9.vae_name", filename: "other_vae.safetensors" }],
    });
    await openMenu(wrapper);
    expect(meta(wrapper, "model:31")).toBe("changes nothing here");
  });
});

describe("choosing a set", () => {
  it("hands the tab the rows it fills, each by its address, and closes", async () => {
    const wrapper = mountMenu();
    await openMenu(wrapper);
    await row(wrapper, "hand:7").trigger("click");
    expect(wrapper.emitted("use")).toEqual([
      [
        {
          name: "Portraits",
          models: [
            {
              address: "4.ckpt_name",
              filename: "sdxl/dreamshaperXL.safetensors",
            },
            { address: "9.vae_name", filename: "vae/sdxl-vae-fp16.safetensors" },
          ],
        },
      ],
    ]);
    expect(menu(wrapper).exists()).toBe(false);
  });

  describe("over a row that already has an unsaved pick", () => {
    const PICKED = [
      {
        address: "4.ckpt_name",
        filename: "sdxl/realvisxl_v50.safetensors",
        kind: "checkpoint",
        pick: "sdxl/dreamshaperXL.safetensors",
      },
      {
        address: "9.vae_name",
        filename: "sdxl_vae.safetensors",
        kind: "vae",
        pick: "vae/other-vae.safetensors",
      },
      { address: "11.clip_name", filename: "clip_l.safetensors", kind: "text_encoder" },
    ];

    it("takes the pick back off where the set holds the saved file, and says so in its count", async () => {
      // `swaps` lists only what changes: a set that agrees with the saved
      // graph has none, and says which files it holds in the rest of its plan.
      planSetClones.mockResolvedValue({
        plans: [
          plan("hand:7", {
            unpaired_bases: [],
            takes: { "vae/SDXL_VAE.safetensors": 21, "clip_l.safetensors": 22 },
          }),
          plan("hand:8", { fit: "wont_load", reason: "Needs a UNETLoader" }),
          plan("hand:9"),
          plan("model:31"),
        ],
      });
      const wrapper = mountMenu({ models: PICKED });
      await openMenu(wrapper);
      expect(meta(wrapper, "hand:7")).toContain("fills 2 rows");
      await row(wrapper, "hand:7").trigger("click");
      // Each under its saved file: the tab reads that as the pick gone. The
      // text encoder, untouched and unchanged, is not named.
      expect(wrapper.emitted("use")[0][0].models).toEqual([
        { address: "4.ckpt_name", filename: "sdxl/realvisxl_v50.safetensors" },
        { address: "9.vae_name", filename: "sdxl_vae.safetensors" },
      ]);
    });

    it("leaves a pick alone on a row the set has nothing for", async () => {
      planSetClones.mockResolvedValue({
        plans: [
          plan("hand:7", {
            // The base model was not paired, and the set holds no VAE.
            unpaired_bases: ["sdxl/realvisxl_v50.safetensors"],
            takes: {},
          }),
          plan("hand:8"),
          plan("hand:9"),
          plan("model:31"),
        ],
      });
      const wrapper = mountMenu({ models: PICKED });
      await openMenu(wrapper);
      expect(meta(wrapper, "hand:7")).toBe("changes nothing here");
      await row(wrapper, "hand:7").trigger("click");
      expect(wrapper.emitted("use")).toBeUndefined();
    });

    it("does not count a row that already shows the file the set gives it", async () => {
      planSetClones.mockResolvedValue({
        plans: [
          plan("hand:7", {
            swaps: {
              "sdxl/realvisxl_v50.safetensors": "sdxl/dreamshaperXL.safetensors",
            },
            unpaired_bases: [],
            takes: {},
          }),
          plan("hand:8"),
          plan("hand:9"),
          plan("model:31"),
        ],
      });
      const wrapper = mountMenu({ models: PICKED });
      await openMenu(wrapper);
      expect(meta(wrapper, "hand:7")).toBe("changes nothing here");
    });
  });

  // The plans are read off the graph with the owner's replacements applied
  // ("Replace with…", `model_fixes`), so `swaps`, `takes` and `unpaired_bases`
  // name each loader by the file it loads NOW. A row is found by that file,
  // which the tab hands over as `filename`: a row keyed by anything else is a
  // row no set can fill.
  describe("the file a row is found by", () => {
    const ROW = "4.ckpt_name";
    const DREAMSHAPER = "sdxl/dreamshaperXL.safetensors";
    const only = (extra) => ({
      plans: [plan("hand:7", { unpaired_bases: [], takes: {}, ...extra })],
    });

    it.each([
      [
        "a plain row: the file the saved graph loads",
        { filename: "sdxl/realvisxl_v50.safetensors", pick: null },
        { swaps: { "sdxl/realvisxl_v50.safetensors": DREAMSHAPER } },
        [{ address: ROW, filename: DREAMSHAPER }],
      ],
      [
        "a missing row with no replacement: the missing file, as the graph spells it",
        { filename: "Old/Gone_Model.safetensors", pick: null },
        { swaps: { "Old/Gone_Model.safetensors": DREAMSHAPER } },
        [{ address: ROW, filename: DREAMSHAPER }],
      ],
      [
        "a row holding an earlier replacement: the replacement, not the file it replaced",
        { filename: "SDXL/realvis_bf16.safetensors", pick: null },
        { swaps: { "SDXL/realvis_bf16.safetensors": DREAMSHAPER } },
        [{ address: ROW, filename: DREAMSHAPER }],
      ],
      [
        "a row with an unsaved pick: still its saved file, and the set's goes over the pick",
        { filename: "sdxl/realvisxl_v50.safetensors", pick: "pony/ponyV6.safetensors" },
        { swaps: { "sdxl/realvisxl_v50.safetensors": DREAMSHAPER } },
        [{ address: ROW, filename: DREAMSHAPER }],
      ],
      [
        "a replaced row with a pick, and a set holding the replacement: put back under the replacement",
        { filename: "SDXL/realvis_bf16.safetensors", pick: "pony/ponyV6.safetensors" },
        { swaps: {} },
        [{ address: ROW, filename: "SDXL/realvis_bf16.safetensors" }],
      ],
      // A set holding the ORIGINAL the replacement stood in for is one that
      // leaves the loader as saved: the pick comes off, and what is left is
      // the replacement, never the missing original.
      [
        "a replaced VAE row with a pick, and a set holding the original: put back under the replacement",
        { kind: "vae", filename: "vae/sdxl_fixed.safetensors", pick: "vae/other.safetensors" },
        { swaps: {}, takes: { "vae/sdxl_fixed.safetensors": 12 } },
        [{ address: ROW, filename: "vae/sdxl_fixed.safetensors" }],
      ],
    ])("%s", async (_case, model, answer, fill) => {
      planSetClones.mockResolvedValue(only(answer));
      const wrapper = mountMenu({
        models: [{ address: ROW, kind: "checkpoint", ...model }],
      });
      await openMenu(wrapper);
      expect(meta(wrapper, "hand:7")).toBe("fills 1 row");
      await row(wrapper, "hand:7").trigger("click");
      expect(wrapper.emitted("use")[0][0].models).toEqual(fill);
    });

    it.each([
      [
        "a plan keyed by the file a replacement took the place of",
        { filename: "SDXL/realvis_bf16.safetensors", pick: null },
        { swaps: { "realvisXL_v5.safetensors": DREAMSHAPER } },
      ],
      [
        "a replaced row whose loader the set left unpaired, pick or not",
        { filename: "SDXL/realvis_bf16.safetensors", pick: "pony/ponyV6.safetensors" },
        { unpaired_bases: ["SDXL/realvis_bf16.safetensors"] },
      ],
      // The server reads the original as what that loader already loads, so
      // it sends no swap: the row is not filled with a file that is missing.
      [
        "a replaced checkpoint row, from a set holding the original it replaced",
        { filename: "SDXL/realvis_bf16.safetensors", pick: null },
        { swaps: {}, unpaired_bases: [] },
      ],
      [
        "a replaced VAE row, from a set holding the original it replaced",
        { kind: "vae", filename: "vae/sdxl_fixed.safetensors", pick: null },
        { swaps: {}, takes: { "vae/sdxl_fixed.safetensors": 12 } },
      ],
      [
        "a row that already shows the file the set gives it",
        { filename: "sdxl/realvisxl_v50.safetensors", pick: DREAMSHAPER },
        { swaps: { "sdxl/realvisxl_v50.safetensors": DREAMSHAPER } },
      ],
    ])("fills nothing for %s", async (_case, model, answer) => {
      planSetClones.mockResolvedValue(only(answer));
      const wrapper = mountMenu({
        models: [{ address: ROW, kind: "checkpoint", ...model }],
      });
      await openMenu(wrapper);
      expect(meta(wrapper, "hand:7")).toBe("changes nothing here");
      expect(row(wrapper, "hand:7").attributes("aria-disabled")).toBe("true");
    });
  });

  it("puts focus back on the button", async () => {
    const wrapper = mountMenu();
    await openMenu(wrapper);
    row(wrapper, "hand:7").element.focus();
    expect(document.activeElement).toBe(row(wrapper, "hand:7").element);
    await row(wrapper, "hand:7").trigger("click");
    await flushPromises();
    expect(document.activeElement).toBe(button(wrapper).element);
  });

  it("leaves focus on another control the closing click landed on", async () => {
    const elsewhere = document.createElement("button");
    document.body.appendChild(elsewhere);
    try {
      const wrapper = mountMenu();
      await openMenu(wrapper);
      elsewhere.focus();
      // The click outside, as `v-menu` reports it.
      wrapper.findComponent(VMenuStub).vm.$emit("update:modelValue", false);
      await flushPromises();
      expect(document.activeElement).toBe(elsewhere);
    } finally {
      elsewhere.remove();
    }
  });
});

describe("another workflow", () => {
  it("closes the menu, and an answer for the one before draws nothing", async () => {
    const first = held();
    const second = held();
    planSetClones
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const wrapper = mountMenu();
    await openMenu(wrapper);
    await wrapper.setProps({ workflowId: "b" });
    await flushPromises();
    expect(menu(wrapper).exists()).toBe(false);

    await openMenu(wrapper);
    expect(planSetClones).toHaveBeenLastCalledWith("b", expect.any(Array));
    // The first workflow's plans land on the second's open menu.
    first.resolve(PLANS);
    await flushPromises();
    expect(wrapper.findAll("[data-set]")).toHaveLength(0);
    expect(unread(wrapper).text()).toBe("Reading your sets…");

    second.resolve({ plans: [plan("hand:9")] });
    await flushPromises();
    expect(
      wrapper.findAll("[data-set]").map((entry) => entry.attributes("data-set")),
    ).toEqual(["hand:9"]);
  });
});

describe("an earlier open", () => {
  it("that fails after the menu was opened again says nothing on the new one", async () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    let fail;
    planSetClones
      .mockReturnValueOnce(
        new Promise((_, reject) => {
          fail = reject;
        }),
      )
      .mockReturnValueOnce(held().promise);
    const wrapper = mountMenu();
    await openMenu(wrapper);
    await button(wrapper).trigger("click");
    await openMenu(wrapper);
    fail(new Error("offline"));
    await flushPromises();
    expect(unread(wrapper).text()).toBe("Reading your sets…");
  });
});

describe("a read that fails", () => {
  it("says so in the menu, logs which workflow, and asks again next time", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const offline = new Error("offline");
    planSetClones.mockRejectedValueOnce(offline);
    const wrapper = mountMenu();
    await openMenu(wrapper);
    expect(unread(wrapper).text()).toBe("Could not read your sets just now.");
    expect(wrapper.findAll("[data-set]")).toHaveLength(0);
    expect(warn).toHaveBeenCalledWith(expect.stringContaining(" a"), offline);

    await button(wrapper).trigger("click");
    await openMenu(wrapper);
    expect(fetchWorkflowSets).toHaveBeenCalledTimes(2);
    expect(unread(wrapper).exists()).toBe(false);
    expect(row(wrapper, "hand:7").exists()).toBe(true);
  });
});

describe("from outside the tab", () => {
  it("opens and reads, and takes the keyboard once the menu is drawn", async () => {
    const wrapper = mountMenu();
    wrapper.vm.open();
    await flushPromises();
    expect(menu(wrapper).exists()).toBe(true);
    expect(planSetClones).toHaveBeenCalledTimes(1);
    wrapper.findComponent(VMenuStub).vm.$emit("afterEnter");
    expect(document.activeElement).toBe(menu(wrapper).element);
  });

  it("leaves focus on a row that already has it", async () => {
    const wrapper = mountMenu();
    wrapper.vm.open();
    await flushPromises();
    row(wrapper, "hand:7").element.focus();
    wrapper.findComponent(VMenuStub).vm.$emit("afterEnter");
    expect(document.activeElement).toBe(row(wrapper, "hand:7").element);
  });

  it("does not take the keyboard when the button opened it", async () => {
    const wrapper = mountMenu();
    button(wrapper).element.focus();
    await openMenu(wrapper);
    wrapper.findComponent(VMenuStub).vm.$emit("afterEnter");
    expect(document.activeElement).toBe(button(wrapper).element);
  });

  it("can be handed the keyboard", () => {
    const wrapper = mountMenu();
    wrapper.vm.focus();
    expect(document.activeElement).toBe(button(wrapper).element);
  });
});
