// "Works with" for one model (#1438).
//
// Two assertions carry this screen. The ranking has to be by RECIPES, because
// that is the only thing co-occurrence measures - a list ordered by pictures
// would put a prolific one-off above the pairing the library actually leans on.
// And the closing notice has to be there whenever a companion list is: without
// it a short list reads as a compatibility verdict, which is the one claim this
// feature must not make.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

const fetchWorkflowSets = vi.fn();

vi.mock("../../api/modelShelf", () => ({
  BASE_MODEL_UNASSIGNED: "UNASSIGNED",
  listAdapters: vi.fn().mockResolvedValue([]),
  listCheckpoints: vi.fn().mockResolvedValue([]),
  listBaseModelCompletions: vi.fn().mockResolvedValue([]),
  editModels: vi.fn(),
  forgetModels: vi.fn(),
  deleteModels: vi.fn(),
  setAdapterAttachments: vi.fn(),
  fetchWorkflowSets: (...args) => fetchWorkflowSets(...args),
}));

vi.mock("../../api/modelIcons", () => ({
  setModelIcon: vi.fn(),
  clearModelIcons: vi.fn(),
  modelIconUrl: (sha) => `/api/v1/model-icons/${sha}`,
}));

import ModelWorksWithDialog from "./ModelWorksWithDialog.vue";
import { useModelShelfStore } from "../../stores/useModelShelfStore";

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      Tooltip: true,
      ChipRow: {
        props: ["items"],
        template:
          "<div class='chips'><span v-for='i in items' :key='i.key'>{{ i.label }}</span></div>",
      },
      // The shell teleports and is AppDialog's own contract; the body slot has
      // to render or there is nothing here to assert against.
      AppDialog: {
        props: ["open", "title", "subtitle"],
        template:
          "<div v-if='open'><h2>{{ title }}</h2><p class='sub'>{{ subtitle }}</p><slot /></div>",
      },
    },
  },
};

function member(id, name, kind) {
  return { id, name, kind, filename: name, file_size: 1000, ambiguous: false };
}

const CKPT = member(1, "realvisXL_v5", "checkpoint");
const OTHER = member(9, "juggernautXL_v9", "checkpoint");
const VAE = member(2, "sdxl_vae", "vae");
const CLIP = member(4, "clip_l", "text_encoder");

function combination(key, models, { recipes = 1, pictures = 1 } = {}) {
  return { key, models, recipes, picture_count: pictures, covers: [] };
}

async function mountDialog(model, combinations) {
  fetchWorkflowSets.mockResolvedValue({ combinations, no_set: [] });
  const wrapper = mount(ModelWorksWithDialog, {
    ...globalOpts,
    props: { model },
  });
  await new Promise((resolve) => setTimeout(resolve, 0));
  await wrapper.vm.$nextTick();
  return wrapper;
}

beforeEach(() => {
  setActivePinia(createPinia());
  window.localStorage.clear();
  fetchWorkflowSets
    .mockReset()
    .mockResolvedValue({ combinations: [], no_set: [] });
});

describe("the companion list", () => {
  const SETS = [
    // `clip_l` shares 6 recipes with the VAE and `realvisXL_v5` only 2, but the
    // checkpoint's pictures dwarf the encoder's: ordered by pictures the two
    // would swap, which is what makes this fixture worth having.
    combination("1,2,4", [CKPT, VAE, CLIP], { recipes: 2, pictures: 500 }),
    combination("2,4,9", [OTHER, VAE, CLIP], { recipes: 4, pictures: 10 }),
  ];

  it("ranks by recipes, which is the only thing co-occurrence measures", async () => {
    const wrapper = await mountDialog({ ...VAE }, SETS);

    const names = wrapper.findAll(".ww__name").map((el) => el.text());
    expect(names).toEqual(["clip_l", "juggernautXL_v9", "realvisXL_v5"]);
    expect(wrapper.findAll(".ww__figures")[0].text()).toContain("6 recipes");
  });

  it("states the sets it is in above the companions", async () => {
    const wrapper = await mountDialog({ ...VAE }, SETS);

    expect(wrapper.text()).toContain("In 2 sets");
    expect(wrapper.find(".chips").text()).toContain("realvisXL_v5 · sdxl_vae");
  });

  it("never lists the model itself", async () => {
    const wrapper = await mountDialog({ ...VAE }, SETS);

    expect(wrapper.findAll(".ww__name").map((el) => el.text())).not.toContain(
      "sdxl_vae",
    );
  });

  it("closes with the notice that a missing companion is untested", async () => {
    const wrapper = await mountDialog({ ...VAE }, SETS);

    expect(wrapper.find(".ww__notice").text()).toContain(
      "Nothing is ruled out",
    );
  });

  it("offers the rest behind one press rather than drawing twenty", async () => {
    const many = [
      combination(
        "1,2,4,9,10,11",
        [
          CKPT,
          VAE,
          CLIP,
          OTHER,
          member(10, "extra_a", "adapter"),
          member(11, "extra_b", "adapter"),
        ],
        { recipes: 3 },
      ),
    ];
    const wrapper = await mountDialog({ ...VAE }, many);

    // Identity, not a count: which four are drawn is the ranking's promise.
    // Every companion here shares the one recipe, so the tie-break is the name
    // - which is the branch a count-only assertion would never reach.
    expect(wrapper.findAll(".ww__name").map((el) => el.text())).toEqual([
      "clip_l",
      "extra_a",
      "extra_b",
      "juggernautXL_v9",
    ]);
    // The named control, not whichever button the dialog renders first.
    const more = wrapper.find(".ww__more");
    expect(more.text()).toContain("Show all 5 companions");
    await more.trigger("click");
    expect(wrapper.findAll(".ww__name")).toHaveLength(5);
  });
});

describe("a model no recipe names", () => {
  it("says so without implying anything about what it works with", async () => {
    const wrapper = await mountDialog({ ...VAE }, [
      combination("1,4", [CKPT, CLIP]),
    ]);

    expect(wrapper.text()).toContain(
      "No recipe in this library names this file",
    );
    expect(wrapper.text()).toContain("only a record of what has been tried");
    // And no notice: there is no list for it to qualify, and the sentence above
    // already carries the caveat.
    expect(wrapper.find(".ww__notice").exists()).toBe(false);
  });

  it("reports a failed read rather than an empty answer", async () => {
    fetchWorkflowSets.mockRejectedValue(new Error("hub is busy"));
    const wrapper = mount(ModelWorksWithDialog, {
      ...globalOpts,
      props: { model: { ...VAE } },
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    await wrapper.vm.$nextTick();

    expect(wrapper.find('[role="alert"]').text()).toContain("hub is busy");
  });
});

describe("the two shapes it is handed", () => {
  // A SET MEMBER carries `name` as a string and `kind` as the file kind. A SHELF
  // ROW carries `name` as `modelName`'s `{text, state}` pair and `kind` as the
  // adapter's algorithm, with the file kind under `file_kind`. Every test above
  // fabricates the first, which is why binding `model.name` straight into the
  // heading shipped a title rendered as JSON from the row list - one of the two
  // entry points the changelog advertises (#1479 review).

  /** A row as `visibleRows` really serves one. */
  function shelfRow(overrides = {}) {
    return {
      id: 2,
      sha256: "b".repeat(64),
      file_kind: "vae",
      kind: null,
      display_name: "SDXL VAE",
      filename: "sdxl_vae.safetensors",
      name: { text: "SDXL VAE", state: "named" },
      file_size: 334_000_000,
      ...overrides,
    };
  }

  it("titles itself from a shelf row's name pair, never the object", async () => {
    const wrapper = await mountDialog(shelfRow(), [
      combination("1,2", [CKPT, VAE], { recipes: 2 }),
    ]);

    expect(wrapper.find("h2").text()).toBe("SDXL VAE");
    expect(wrapper.find("h2").text()).not.toContain("state");
    // And the kind survives: a row has no `kindLabel`, so it comes off
    // `file_kind` rather than being dropped.
    expect(wrapper.find(".sub").text()).toContain("VAE");
    expect(wrapper.find(".sub").text()).toContain("318.5 MB");
  });

  it("names an adapter row by its ALGORITHM, as its own Kind column does", async () => {
    // `LoKr`, not the generic "LoRA" every adapter would otherwise get: the
    // dialog is opened from a row whose Kind cell says `LoKr`, and the two must
    // not disagree about the same file. A fixture using `lora` here would leave
    // the branch unreachable, because the generic word is also "LoRA".
    const wrapper = await mountDialog(
      shelfRow({
        file_kind: "adapter",
        kind: "lokr",
        display_name: null,
        name: { text: "Cyanwood Style", state: "derived" },
      }),
      [combination("1,2", [CKPT, VAE], { recipes: 1 })],
    );

    expect(wrapper.find("h2").text()).toBe("Cyanwood Style");
    expect(wrapper.find(".sub").text()).toContain("LoKr");
  });

  it("falls back to the filename when a row has no name at all", async () => {
    const wrapper = await mountDialog(
      shelfRow({
        display_name: null,
        name: { text: "", state: "needs-a-name" },
      }),
      [],
    );

    expect(wrapper.find("h2").text()).toBe("sdxl_vae.safetensors");
  });

  it("still reads a set member, whose name is a plain string", async () => {
    const wrapper = await mountDialog({ ...VAE }, [
      combination("1,2", [CKPT, VAE], { recipes: 2 }),
    ]);

    expect(wrapper.find("h2").text()).toBe("sdxl_vae");
    expect(wrapper.find(".sub").text()).toContain("VAE");
  });
});

describe("the dialog itself", () => {
  it("is closed with no model, and asks for the read when one arrives", async () => {
    fetchWorkflowSets.mockResolvedValue({ combinations: [], no_set: [] });
    const wrapper = mount(ModelWorksWithDialog, {
      ...globalOpts,
      props: { model: null },
    });
    expect(wrapper.text()).toBe("");
    expect(fetchWorkflowSets).not.toHaveBeenCalled();

    await wrapper.setProps({ model: { ...VAE } });
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(fetchWorkflowSets).toHaveBeenCalledTimes(1);
    expect(wrapper.find("h2").text()).toBe("sdxl_vae");
  });

  it("collapses the list again when it is pointed at another model", async () => {
    const store = useModelShelfStore();
    // Six files, so the anchor has five companions and *Show all* is offered.
    const many = [
      combination(
        "1,2,4,9,10,11",
        [
          CKPT,
          VAE,
          CLIP,
          OTHER,
          member(10, "extra_a", "adapter"),
          member(11, "extra_b", "adapter"),
        ],
        { recipes: 3 },
      ),
    ];
    const wrapper = await mountDialog({ ...VAE }, many);
    await wrapper.find(".ww__more").trigger("click");
    expect(wrapper.findAll(".ww__name")).toHaveLength(5);

    await wrapper.setProps({ model: { ...CLIP } });
    await wrapper.vm.$nextTick();

    expect(wrapper.findAll(".ww__name")).toHaveLength(4);
    // Read once: the store's guard drops the repeat, so switching model is free.
    expect(fetchWorkflowSets).toHaveBeenCalledTimes(1);
    expect(store.setsLoaded).toBe(true);
  });
});
