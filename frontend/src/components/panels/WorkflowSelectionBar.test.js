// The Workflows grid's verb layer (#1455).
//
// Two things are worth asserting here and a reading of the component cannot
// confirm either.
//
// **The gates**, because each one is a refusal the SERVER also enforces: Run
// takes one workflow, Rename one, and Delete a manual one — so a gate that disagrees with the route
// is a button that can only come back refused.
//
// **The parity**, because `menu-parity.spec.js` exists: the picture grid's
// dropdown and context menu are two components held equal by convention, and
// #403 is what that costs. Here they are two mounts of one render function, so
// the check is that this stays true — a future hand that writes a second list
// for the `⋯` menu turns this red, which is the whole point of asserting
// something that is currently true by construction.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

const getPixlstashNode = vi.fn();
vi.mock("../../api/comfyui", () => ({
  getPixlstashNode: (...args) => getPixlstashNode(...args),
}));

const preflightWorkflowRun = vi.fn();
vi.mock("../../api/workflows", async (importOriginal) => ({
  ...(await importOriginal()),
  preflightWorkflowRun: (...args) => preflightWorkflowRun(...args),
}));

import WorkflowSelectionBar from "./WorkflowSelectionBar.vue";
import { useFilterStore } from "../../stores/useFilterStore";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";
import { useFavouritesStore } from "../../stores/useFavouritesStore";
import { useTasksStore } from "../../stores/useTasksStore";

// `v-menu` is stubbed because Vuetify is not installed in these mounts, and an
// UNRESOLVED component renders its default slot while silently dropping its
// named ones — so the count button, which is `#activator` content, would not
// exist and every assertion about it would pass vacuously. This stub renders
// both, which is what the real menu does once it is open.
const VMenuStub = {
  name: "VMenu",
  props: ["modelValue"],
  template:
    '<div class="v-menu-stub">' +
    '<slot name="activator" :props="{}" /><slot /></div>',
};

const TooltipStub = {
  name: "Tooltip",
  props: ["text", "activator"],
  template:
    '<span class="tip" :data-text="text"><slot name="activator" :props="{}" /></span>',
};

// The pill's buttons are stubbed so the gate is readable off the DOM: the real
// `AppBarButton` consumes `tooltip` as a prop, and `disabled` as a native
// attribute is the empty string, which is falsy — a helper reading `!disabled`
// would then call every disabled button enabled and the whole file would pass
// against the opposite of what it asserts.
const AppBarButtonStub = {
  name: "AppBarButton",
  props: ["icon", "shape", "danger", "disabled", "tooltip", "loading"],
  template:
    '<button :data-disabled="String(Boolean(disabled))" :data-tooltip="tooltip"><slot /></button>',
};

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      "v-menu": VMenuStub,
      Tooltip: TooltipStub,
      AppBarButton: AppBarButtonStub,
    },
  },
};

const card = (id, extra = {}) => ({
  id,
  name: id,
  models: [],
  loras: [],
  picture_count: 1,
  covers: [],
  topologies: ["t-" + id],
  imported: false,
  hidden: false,
  ...extra,
});

const CARDS = [
  card("a"),
  card("b", { imported: true, manual: true }),
  // A workflow holding three topologies, which Split can hand out.
  card("c", {
    imported: true,
    manual: true,
    topologies: ["a".repeat(64), "b".repeat(64), "c".repeat(64)],
  }),
  card("d", { hidden: true }),
  card("p", {
    picture_count: 3,
    covers: [
      { url: "/1", picture_id: 11 },
      { url: "/2", picture_id: 22 },
      { url: "/3", picture_id: 33 },
    ],
  }),
];

/** Mounted, with `keys` selected. */
function bar(keys = []) {
  const store = useWorkflowsStore();
  store.cards = CARDS;
  store.selectRange(keys);
  const wrapper = mount(WorkflowSelectionBar, globalOpts);
  return { wrapper, store };
}

/**
 * One pill button, by the verb it carries. The pill's face only: the `⋯` menu
 * and the card menu repeat the same `data-verb`s, and they are not the pill.
 */
const verb = (wrapper, name) =>
  wrapper
    .findAll(`.selbar [data-verb="${name}"]`)
    .find((el) => !el.element.closest(".ctx-menu"));

/** The face's verbs, in the order they are drawn. */
const faceVerbs = (wrapper) =>
  wrapper
    .findAll(".selbar [data-verb]")
    .filter((el) => !el.element.closest(".ctx-menu"))
    .map((el) => el.attributes("data-verb"));

const enabled = (wrapper, name) =>
  verb(wrapper, name).attributes("data-disabled") === "false";

const tooltip = (wrapper, name) =>
  verb(wrapper, name).attributes("data-tooltip");

/** A row of the `⋯` menu: the verb's row, the one a refused verb keeps. */
const menuRow = (wrapper, name) =>
  verbMenus(wrapper)[0].find(`[data-verb="${name}"]`);

const rowEnabled = (row) => row.attributes("aria-disabled") !== "true";

/** The refusal a verb row carries, read from its tooltip. */
const rowTooltip = (row) => row.find(".tip").attributes("data-text");

/** A verb menu's rows, as a reader would read them aloud. */
const rowLabels = (root) =>
  root.findAll(".ctx-item").map((el) => el.find(".ctx-label-text").text());

/**
 * A verb menu's rows by VERB, which is what parity is about.
 *
 * Not the labels: the open row names the picture the pointer was over, so the
 * context menu can read "Open picture 2 of 3" where `⋯` reads "Open cover
 * picture" — the same verb, correctly saying which picture it is about to
 * open. #403 was a verb present in one menu and absent from the other, and
 * comparing rendered strings would call this legitimate difference that bug.
 */
const rowVerbs = (root) =>
  root.findAll(".ctx-item").map((el) => el.attributes("data-verb"));

/** The verb menus on screen. NOT `.wf-menu`: the count menu wears that too. */
const verbMenus = (wrapper) => wrapper.findAll('[data-testid="wf-verbs"]');

describe("the pill's gates", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("is not drawn at all with nothing selected", () => {
    const { wrapper } = bar([]);
    expect(wrapper.find(".selbar").exists()).toBe(false);
  });

  it("Run and Rename are single-selection, and say why when they are not", () => {
    // Off the face since #2165: they are `⋯` rows, which keep their refusal
    // as a tooltip, one row each, so the reason stays one hover away.
    const { wrapper } = bar(["a"]);
    expect(rowEnabled(menuRow(wrapper, "run"))).toBe(true);
    expect(rowEnabled(menuRow(wrapper, "rename"))).toBe(true);

    const many = bar(["a", "b"]).wrapper;
    expect(rowEnabled(menuRow(many, "run"))).toBe(false);
    expect(rowEnabled(menuRow(many, "rename"))).toBe(false);
    expect(rowTooltip(menuRow(many, "rename"))).toContain("Select one workflow");
    expect(verb(many, "rename"), "Rename is not on the face").toBeUndefined();
    expect(verb(many, "run"), "Run is not on the face").toBeUndefined();
  });

  it("Delete needs EVERY selected workflow to be manual, not merely one", () => {
    const manual = bar(["b", "c"]).wrapper;
    expect(enabled(manual, "delete")).toBe(true);
    expect(tooltip(manual, "delete")).toContain("pictures stay");
    // The verb's name is stable; the refusal is the tooltip's.
    expect(verb(manual, "delete").attributes("aria-label")).toBe(
      "Delete workflow",
    );

    // `a` is automatic: the library groups it from its pictures and the route
    // 409s. A press that deleted the one it could and left the rest would be
    // a partial destruction nobody asked for, so the whole selection is
    // refused, and the reason names the way out.
    const mixed = bar(["a", "b"]).wrapper;
    expect(enabled(mixed, "delete")).toBe(false);
    expect(tooltip(mixed, "delete")).toContain("hidden instead");
  });

  it("Delete is refused on an imported workflow that is not manual", () => {
    // `imported` was the old gate; `manual` is what the route tests now.
    const store = useWorkflowsStore();
    store.cards = [card("x", { imported: true, manual: false })];
    store.selectRange(["x"]);
    const wrapper = mount(WorkflowSelectionBar, globalOpts);
    expect(enabled(wrapper, "delete")).toBe(false);
    const row = verbMenus(wrapper)[0]
      .findAll(".ctx-item")
      .find((el) => el.attributes("data-verb") === "delete");
    expect(row.attributes("aria-disabled")).toBe("true");
  });

  it("Hide reads as Unhide only when there is nothing left to hide", () => {
    const label = (wrapper) =>
      menuRow(wrapper, "hide").find(".ctx-label-text").text();
    expect(label(bar(["d"]).wrapper)).toBe("Unhide");
    // Mixed is Hide: the gesture on "these, and that one already hidden" is to
    // get them all out of the grid.
    expect(label(bar(["a", "d"]).wrapper)).toBe("Hide");
  });

  it("does not read as Unhide when the store cannot resolve the selection", () => {
    // A key the grid no longer holds resolves to no card, and `every` over an
    // empty list is vacuously true — which would turn the button into Unhide
    // over a selection nobody has hidden.
    const { wrapper } = bar(["gone"]);
    expect(menuRow(wrapper, "hide").find(".ctx-label-text").text()).toBe("Hide");
  });

  it("names every verb on its face with an aria-label of its own, never its tooltip", () => {
    // The file's rule: the label is the stable verb, the tooltip the refusal.
    const { wrapper } = bar(["a", "b"]);
    const unnamed = faceVerbs(wrapper).filter(
      (name) => !verb(wrapper, name).attributes("aria-label"),
    );
    expect(unnamed).toEqual([]);
    expect(verb(wrapper, "clear").attributes("aria-label")).toBe("Clear selection");
  });

  it("Clear is the pill's last verb, and clears the selection", async () => {
    const { wrapper, store } = bar(["a", "b"]);
    expect(faceVerbs(wrapper).at(-1)).toBe("clear");
    expect(verb(wrapper, "clear").attributes("data-tooltip")).toBe(
      "Clear selection (Esc)",
    );
    await verb(wrapper, "clear").trigger("click");
    expect(store.selectedKeys).toEqual([]);
  });
});

describe("the two menus cannot diverge", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("the ⋯ menu and the context menu list exactly the same verbs", () => {
    // **The stub renders both menus whether or not they are open**, which is
    // what makes this comparison possible at all — and is why there is no
    // `openContextMenu` call here: it would read as "the menu opens" and
    // assert nothing of the sort. That path is covered where it can be:
    // `WorkflowsView.test.js`'s ContextMenu-key test mounts a stub gated on
    // `modelValue`, so the menu is genuinely absent until the key is pressed.
    //
    // What IS under test here is that the two mounts cannot list different
    // things — the failure `menu-parity.spec.js` exists for (#403).
    const { wrapper } = bar(["a", "b"]);
    const menus = verbMenus(wrapper);
    // Two surfaces, and the comparison below is vacuous with fewer.
    expect(menus.length, "both menus should be rendered by the stub").toBe(2);
    const [more, context] = menus.map(rowVerbs);
    expect(more.length, "the verb menu rendered no rows").toBeGreaterThan(0);
    // Every row carries one, or the comparison is a list of undefineds that
    // would match any other list of undefineds.
    expect(more.every(Boolean), "every row needs a data-verb").toBe(true);
    expect(context).toEqual(more);
  });

  it("every pill verb is in the menu too, so the icons teach the words", () => {
    const { wrapper } = bar(["a"]);
    const labels = rowLabels(verbMenus(wrapper)[0]);
    // The pill is a SUBSET of the menu by design — Export and Duplicate live
    // only behind `⋯` — but nothing may be on the pill alone, or an icon would
    // be a chord with no word anywhere to learn it by.
    expect(labels).toEqual(
      expect.arrayContaining([
        "Run…",
        "Show all pictures using this workflow",
        "Rename",
        "Hide",
        "Delete…",
      ]),
    );
  });

  it("shows the pictures of every selected workflow, after Run (#1797)", async () => {
    const { wrapper } = bar(["a", "b", "p"]);
    expect(enabled(wrapper, "show-pictures")).toBe(true);
    expect(tooltip(wrapper, "show-pictures")).toBe(
      "Show the pictures of these 3 workflows",
    );
    const menu = verbMenus(wrapper)[1];
    // Favourite leads the menu (#1578), then the picture rows.
    expect(rowVerbs(menu).slice(0, 4)).toEqual([
      "favourite",
      "open-cover",
      "run",
      "show-pictures",
    ]);
    const row = menu
      .findAll(".ctx-item")
      .find((el) => el.attributes("data-verb") === "show-pictures");
    expect(row.find(".ctx-label-text").text()).toBe(
      "Show all pictures using these 3 workflows",
    );
    await row.trigger("click");
    expect(wrapper.emitted("show-pictures")).toHaveLength(1);
    await verb(wrapper, "show-pictures").trigger("click");
    expect(wrapper.emitted("show-pictures")).toHaveLength(2);
  });

  it("refuses Show all pictures on a workflow with none to show", () => {
    const store = useWorkflowsStore();
    store.cards = [...CARDS, card("z", { picture_count: 0 })];
    store.selectRange(["z"]);
    const wrapper = mount(WorkflowSelectionBar, globalOpts);
    expect(enabled(wrapper, "show-pictures")).toBe(false);
    expect(tooltip(wrapper, "show-pictures")).toBe(
      "This workflow has no pictures to show",
    );
  });

  it("opens the picture the pointer was on, not always the cover", async () => {
    const { wrapper } = bar(["p"]);
    const label = () =>
      verbMenus(wrapper)[0]
        .findAll(".ctx-item")
        .find((el) => el.attributes("data-verb") === "open-cover")
        .find(".ctx-label-text")
        .text();

    // `⋯` has no pointer and no target, so it means the card's cover.
    expect(label()).toBe("Open cover picture");
    expect(wrapper.vm.openTarget.id).toBe(11);

    // A right-click that landed on the SECOND tile means that one — being
    // offered the first is the menu answering a different question from the
    // one the gesture asked.
    // Awaited: the opener closes and reopens across a tick, so the target is
    // not set on the line that asks for it.
    await wrapper.vm.openContextMenu(1, 2, { id: 22, index: 2, total: 3 });
    await wrapper.vm.$nextTick();
    expect(label()).toBe("Open picture 2 of 3");
    expect(wrapper.vm.openTarget.id).toBe(22);
  });

  it("forgets the pointed-at picture when the menu closes", async () => {
    const { wrapper } = bar(["p"]);
    await wrapper.vm.openContextMenu(1, 2, { id: 22, index: 2, total: 3 });
    await wrapper.vm.$nextTick();
    expect(wrapper.vm.openTarget.id).toBe(22);

    // Otherwise the next `⋯` inherits whichever tile was last right-clicked
    // and opens a picture off a card the reader may have moved on from.
    wrapper.vm.closeContextMenu();
    await wrapper.vm.$nextTick();
    expect(wrapper.vm.openTarget.id).toBe(11);
  });

  it("says video, not picture, for a workflow that saves video", async () => {
    const store = useWorkflowsStore();
    const covers = [
      { url: "/1", picture_id: 11 },
      { url: "/2", picture_id: 22 },
    ];
    store.cards = [
      ...CARDS,
      card("v", { type: "video", manual: true, covers }),
      card("w", { type: "video", picture_count: 0 }),
    ];
    store.selectRange(["v"]);
    const wrapper = mount(WorkflowSelectionBar, globalOpts);
    const labels = () => rowLabels(verbMenus(wrapper)[0]);

    expect(labels()).toEqual(
      expect.arrayContaining([
        "Open cover video",
        "Show all videos using this workflow",
      ]),
    );
    expect(tooltip(wrapper, "show-pictures")).toBe(
      "Show the videos of this workflow",
    );
    expect(tooltip(wrapper, "delete")).toContain("Its videos stay");
    await wrapper.vm.openContextMenu(1, 2, { id: 22, index: 2, total: 2 });
    await wrapper.vm.$nextTick();
    expect(labels()).toContain("Open video 2 of 2");

    store.selectRange(["w"]);
    await wrapper.vm.$nextTick();
    expect(tooltip(wrapper, "show-pictures")).toBe(
      "This workflow has no videos to show",
    );

    // Every selected workflow saves video, so the plural rows say so too.
    store.selectRange(["v", "w"]);
    await wrapper.vm.$nextTick();
    expect(labels()).toContain("Show all videos using these 2 workflows");

    // A selection that is not all video keeps the library's own word.
    store.selectRange(["v", "p"]);
    await wrapper.vm.$nextTick();
    expect(labels()).toContain("Show all pictures using these 2 workflows");
  });

  it("refuses a verb with aria-disabled, so its reason stays hoverable", () => {
    // Rename with two workflows selected is listed anyway and refused with
    // its reason.
    //
    // **Never the NATIVE `disabled`.** That fires no pointer events, so the
    // tooltip carrying the reason never opens — and "the verb stays on screen
    // because the refusal is one hover away" is the entire argument for not
    // hiding it. A row that is grey with no way to learn why is worse than a
    // row that is not there.
    const { wrapper } = bar(["a", "b"]);
    const row = verbMenus(wrapper)[0]
      .findAll(".ctx-item")
      .find((el) => el.find(".ctx-label-text").text() === "Rename");
    expect(row.attributes("aria-disabled")).toBe("true");
    expect(row.attributes("disabled")).toBeUndefined();
    expect(row.classes()).toContain("ctx-item--disabled");
  });

  it("drops a refused row's click rather than firing the verb", async () => {
    const { wrapper } = bar(["a", "b"]);
    const row = verbMenus(wrapper)[0]
      .findAll(".ctx-item")
      .find((el) => el.find(".ctx-label-text").text() === "Rename");
    await row.trigger("click");
    // Without the native attribute the browser no longer swallows the press,
    // so the handler has to. A refused row that still fired would be strictly
    // worse than the disabled one it replaced.
    expect(wrapper.emitted("rename")).toBeUndefined();
  });

  it("re-gates its rows when the selection changes under it", async () => {
    // The menu reads the bar's state itself rather than through props, so
    // this is what proves it still re-renders when that state moves.
    const { wrapper, store } = bar(["a", "b"]);
    const rename = () =>
      verbMenus(wrapper)[0]
        .findAll(".ctx-item")
        .find((el) => el.attributes("data-verb") === "rename");
    expect(rename().attributes("aria-disabled")).toBe("true");
    store.selectRange(["a"]);
    await wrapper.vm.$nextTick();
    expect(rename().attributes("aria-disabled")).toBeUndefined();
    await rename().trigger("click");
    expect(wrapper.emitted("rename")).toHaveLength(1);
  });
});

// #1850, part 3: the clone dialogs went, and what they did is the Workflow
// tab's *Change model set…*, which this row asks for.
describe("the pill's face", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("is Show pictures, Open in ComfyUI, Export, the menu, Delete, Clear, in that order", () => {
    // The ComfyUI verb is drawn only with a ComfyUI to open (see its describe);
    // without one the face is the rest, in the same order.
    const { wrapper } = bar(["a"]);
    expect(faceVerbs(wrapper)).toEqual([
      "show-pictures",
      "export",
      "more",
      "delete",
      "clear",
    ]);
  });

  it("Export is single-selection, and says why when it is not", () => {
    expect(enabled(bar(["a"]).wrapper, "export")).toBe(true);
    const many = bar(["a", "b"]).wrapper;
    expect(enabled(many, "export")).toBe(false);
    expect(tooltip(many, "export")).toContain("Select one workflow to export it");
  });

  it("Export on the face emits export, as its row does", async () => {
    const { wrapper } = bar(["a"]);
    await verb(wrapper, "export").trigger("click");
    expect(wrapper.emitted("export")).toHaveLength(1);
  });

  it("Open in ComfyUI on the face takes the menu row's gate, and opens adapted", async () => {
    useFilterStore().comfyuiUrl = "http://127.0.0.1:8188/";
    getPixlstashNode.mockResolvedValue({ can_open_workflows: true });
    const store = useWorkflowsStore();
    store.cards = CARDS;
    store.selectRange(["a"]);
    const wrapper = mount(WorkflowSelectionBar, globalOpts);
    await flushPromises();
    expect(enabled(wrapper, "open-comfyui")).toBe(true);
    await verb(wrapper, "open-comfyui").trigger("click");
    expect(wrapper.emitted("open-comfyui")).toEqual([[false]]);

    store.selectRange(["a", "b"]);
    await flushPromises();
    expect(enabled(wrapper, "open-comfyui")).toBe(false);
    expect(tooltip(wrapper, "open-comfyui")).toContain("Select one workflow");
  });
});

describe("Convert to Inpainting workflow", () => {
  const rowOf = (wrapper) =>
    verbMenus(wrapper)[0]
      .findAll(".ctx-item")
      .find((el) => el.attributes("data-verb") === "inpaint-copy");

  const one = (extra) => {
    const store = useWorkflowsStore();
    store.cards = [...CARDS, card("x", extra)];
    store.selectRange(["x"]);
    return mount(WorkflowSelectionBar, globalOpts);
  };

  it("is offered for an edit workflow typed text to image, and asks for it", async () => {
    // An edit graph starting from an empty latent is typed txt2img: the server
    // decides whether it converts, not the badge.
    const wrapper = one({ type: "txt2img" });
    const row = rowOf(wrapper);
    expect(row.find(".ctx-label-text").text()).toBe("Convert to Inpainting workflow");
    expect(row.attributes("aria-disabled")).toBeUndefined();
    await row.trigger("click");
    expect(wrapper.emitted("inpaint-copy")).toHaveLength(1);
    wrapper.unmount();
  });

  it.each(["inpaint", "outpaint", "video", "upscale"])(
    "is refused for the %s type, with why",
    async (type) => {
      const wrapper = one({ type });
      const row = rowOf(wrapper);
      expect(row.attributes("aria-disabled")).toBe("true");
      expect(row.find(".tip").attributes("data-text")).not.toContain("Make a new");
      await row.trigger("click");
      expect(wrapper.emitted("inpaint-copy")).toBeUndefined();
      wrapper.unmount();
    },
  );

  it("is refused for several", async () => {
    const { wrapper } = bar(["a", "b"]);
    const row = rowOf(wrapper);
    expect(row.attributes("aria-disabled")).toBe("true");
    expect(row.find(".tip").attributes("data-text")).toBe(
      "Select one workflow to convert it to inpainting",
    );
    wrapper.unmount();
  });
});

describe("Change model set…", () => {
  const rowOf = (wrapper, name) =>
    verbMenus(wrapper)[0]
      .findAll(".ctx-item")
      .find((el) => el.attributes("data-verb") === name);

  it("is offered for one workflow, and asks for it", async () => {
    const { wrapper } = bar(["a"]);
    const row = rowOf(wrapper, "use-set");
    expect(row.find(".ctx-label-text").text()).toBe("Change model set…");
    expect(row.attributes("aria-disabled")).toBeUndefined();
    expect(row.find(".tip").attributes("data-text")).toBe(
      "Fill its model rows from one of your sets, as unsaved changes",
    );
    await row.trigger("click");
    expect(wrapper.emitted("use-set")).toHaveLength(1);
    wrapper.unmount();
  });

  it("is refused for several, with why", async () => {
    const { wrapper } = bar(["a", "b"]);
    const row = rowOf(wrapper, "use-set");
    expect(row.attributes("aria-disabled")).toBe("true");
    expect(row.find(".tip").attributes("data-text")).toBe(
      "Select one workflow to change its model set",
    );
    await row.trigger("click");
    expect(wrapper.emitted("use-set")).toBeUndefined();
    wrapper.unmount();
  });

  it("sits with the rows that change the card, before the ones that make one", () => {
    const { wrapper } = bar(["a"]);
    const verbs = rowVerbs(verbMenus(wrapper)[0]);
    expect(verbs.slice(verbs.indexOf("rename"))).toEqual([
      "rename",
      "set-thumbnail",
      "pin-thumbnail",
      "hide",
      "use-set",
      "export",
      "duplicate",
      "inpaint-copy",
      "delete",
    ]);
    wrapper.unmount();
  });

  it("no row, tooltip or name says copy or clone (rule 9 of #1846)", () => {
    // One selected and several: the refusals are sentences of their own.
    for (const keys of [["a"], ["a", "b"]]) {
      const { wrapper } = bar(keys);
      const said = [
        wrapper.text(),
        ...wrapper.findAll(".tip").map((el) => el.attributes("data-text")),
        ...wrapper.findAll("[data-tooltip]").map((el) => el.attributes("data-tooltip")),
        ...wrapper.findAll("[aria-label]").map((el) => el.attributes("aria-label")),
      ].join("\n");
      expect(said).toContain("Duplicate");
      expect(said).not.toMatch(/\b(cop(y|ies|ied)|clon(e|ed|ing)|fixed|edited|original)\b/i);
      wrapper.unmount();
    }
  });
});

// The custom thumbnail's two rows (#2000's template pictures, and the owner's).
describe("the thumbnail rows", () => {
  const rowOf = (wrapper, name) =>
    verbMenus(wrapper)[0]
      .findAll(".ctx-item")
      .find((el) => el.attributes("data-verb") === name);

  beforeEach(() => setActivePinia(createPinia()));

  it("refuses to pin a workflow with no thumbnail, saying why", async () => {
    const { wrapper } = bar(["a"]);
    const pin = rowOf(wrapper, "pin-thumbnail");
    expect(pin.attributes("aria-disabled")).toBe("true");
    expect(pin.find(".tip").attributes("data-text")).toBe(
      "This workflow has no custom thumbnail. Choose one first",
    );
    await pin.trigger("click");
    expect(wrapper.emitted("pin-thumbnail")).toBeUndefined();
    // Choosing one is still offered.
    const choose = rowOf(wrapper, "set-thumbnail");
    expect(choose.attributes("aria-disabled")).toBeUndefined();
    await choose.trigger("click");
    expect(wrapper.emitted("set-thumbnail")).toHaveLength(1);
    wrapper.unmount();
  });

  it("pins an unpinned thumbnail and unpins a pinned one", async () => {
    for (const [pinned, label, sent] of [
      [false, "Always show custom thumbnail", true],
      [true, "Show generated pictures", false],
    ]) {
      const { wrapper, store } = bar([]);
      store.cards = [card("t", { thumbnail: { url: "/model-icons/x", pinned } })];
      store.selectRange(["t"]);
      await flushPromises();
      const pin = rowOf(wrapper, "pin-thumbnail");
      expect(pin.find(".ctx-label-text").text()).toBe(label);
      await pin.trigger("click");
      expect(wrapper.emitted("pin-thumbnail")).toEqual([[sent]]);
      wrapper.unmount();
    }
  });

  it("is refused for several workflows", () => {
    const { wrapper } = bar(["a", "b"]);
    for (const name of ["set-thumbnail", "pin-thumbnail"]) {
      expect(rowOf(wrapper, name).attributes("aria-disabled")).toBe("true");
    }
    wrapper.unmount();
  });
});

// The Workflow tab's two open verbs, under one row with a submenu.
describe("Open in ComfyUI", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    getPixlstashNode.mockReset();
    getPixlstashNode.mockResolvedValue({ can_open_workflows: true });
  });

  /** Mounted with a ComfyUI configured, after the node check answered. */
  async function withComfyui(keys, opts = {}) {
    useFilterStore().comfyuiUrl = "http://127.0.0.1:8188/";
    const store = useWorkflowsStore();
    store.cards = CARDS;
    store.selectRange(keys);
    const wrapper = mount(WorkflowSelectionBar, { ...globalOpts, ...opts });
    await flushPromises();
    return wrapper;
  }

  const trigger = (wrapper) =>
    verbMenus(wrapper)[0].find('[data-verb="open-comfyui"][aria-haspopup]');

  it("is absent with no ComfyUI to open", () => {
    const { wrapper } = bar(["a"]);
    expect(wrapper.find('[data-verb="open-comfyui"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("opens a submenu of Adapted and Original, each firing its verb", async () => {
    const wrapper = await withComfyui(["a"]);
    // In both menus, so their parity still holds.
    expect(rowVerbs(verbMenus(wrapper)[1])).toEqual(rowVerbs(verbMenus(wrapper)[0]));
    expect(trigger(wrapper).attributes("aria-disabled")).toBeUndefined();
    expect(wrapper.find(".ctx-submenu").exists()).toBe(false);

    await trigger(wrapper).trigger("click");
    const sub = verbMenus(wrapper)[0].find(".ctx-submenu");
    expect(rowLabels(sub)).toEqual(["Adapted for PixlStash", "Original"]);
    // Each row wears the mark of whose nodes it opens with.
    const [adaptedRow, originalRow] = sub.findAll(".ctx-item");
    expect(adaptedRow.find("img.ctx-icon").attributes("src")).toBe("/Logo.png");
    expect(adaptedRow.find("svg").exists()).toBe(false);
    expect(originalRow.find("svg.ctx-icon").exists()).toBe(true);
    expect(originalRow.find("img").exists()).toBe(false);

    await sub.findAll(".ctx-item")[0].trigger("click");
    expect(wrapper.emitted("open-comfyui")).toEqual([[false]]);
    // The verb shut the submenu; open it again for the other row.
    expect(wrapper.find(".ctx-submenu").exists()).toBe(false);
    await trigger(wrapper).trigger("click");
    await verbMenus(wrapper)[0].findAll(".ctx-submenu .ctx-item")[1].trigger("click");
    expect(wrapper.emitted("open-comfyui")).toEqual([[false], [true]]);
    wrapper.unmount();
  });

  it("is refused for several workflows, with why", async () => {
    const wrapper = await withComfyui(["a", "b"]);
    const row = trigger(wrapper);
    expect(row.attributes("aria-disabled")).toBe("true");
    expect(row.find(".tip").attributes("data-text")).toBe(
      "Select one workflow to open it in ComfyUI",
    );
    await row.trigger("click");
    expect(wrapper.find(".ctx-submenu").exists()).toBe(false);
    expect(wrapper.emitted("open-comfyui")).toBeUndefined();
    wrapper.unmount();
  });

  it("is refused when ComfyUI lacks the node, naming it", async () => {
    getPixlstashNode.mockResolvedValue({ can_open_workflows: false });
    const wrapper = await withComfyui(["a"]);
    const row = trigger(wrapper);
    expect(row.attributes("aria-disabled")).toBe("true");
    expect(row.find(".tip").attributes("data-text")).toContain(
      "ComfyUI-PixlStash node",
    );
    expect(row.find(".tip").attributes("data-text")).toContain(
      "leave Workflows and come back",
    );
    await row.trigger("click");
    expect(wrapper.find(".ctx-submenu").exists()).toBe(false);
    wrapper.unmount();
  });

  it("is refused while ComfyUI is not running, saying so", async () => {
    getPixlstashNode.mockResolvedValue({
      can_open_workflows: null,
      saves_back: null,
      opens_runs: null,
    });
    const wrapper = await withComfyui(["a"]);
    const row = trigger(wrapper);
    expect(row.attributes("aria-disabled")).toBe("true");
    expect(row.find(".tip").attributes("data-text")).toBe(
      "ComfyUI is not running at http://127.0.0.1:8188/. " +
        "Start ComfyUI to open it there",
    );
    expect(enabled(wrapper, "open-comfyui")).toBe(false);
    expect(tooltip(wrapper, "open-comfyui")).toContain("not running");
    wrapper.unmount();
  });

  it("Right arrow opens it on its first row, Left goes back", async () => {
    const wrapper = await withComfyui(["a"], { attachTo: document.body });
    const row = trigger(wrapper);
    row.element.focus();
    await row.trigger("keydown", { key: "ArrowRight" });
    await flushPromises();
    const rows = verbMenus(wrapper)[0].findAll(".ctx-submenu .ctx-item");
    expect(document.activeElement).toBe(rows[0].element);

    // Down moves inside the submenu, not along the menu behind it.
    await rows[0].trigger("keydown", { key: "ArrowDown" });
    expect(document.activeElement).toBe(rows[1].element);

    // A pointer passing over and off the row leaves a focused submenu open.
    await row.element.parentElement.dispatchEvent(new MouseEvent("mouseleave"));
    await flushPromises();
    expect(document.activeElement).toBe(rows[1].element);
    expect(wrapper.find(".ctx-submenu").exists()).toBe(true);

    await rows[1].trigger("keydown", { key: "ArrowLeft" });
    expect(document.activeElement).toBe(row.element);
    expect(wrapper.find(".ctx-submenu").exists()).toBe(false);
    wrapper.unmount();
  });
});

// #2000: a draft has no graph to run, and Open in ComfyUI is how it is
// finished, so only Run is refused, with the way out in its tooltip.
describe("a draft's gates", () => {
  beforeEach(() => setActivePinia(createPinia()));

  it("refuses Run and says what finishes it", async () => {
    const store = useWorkflowsStore();
    store.cards = [...CARDS, card("x", { manual: true, draft: true })];
    store.selectRange(["x"]);
    const wrapper = mount(WorkflowSelectionBar, globalOpts);
    expect(rowEnabled(menuRow(wrapper, "run"))).toBe(false);
    expect(rowTooltip(menuRow(wrapper, "run"))).toBe(
      "Finish it in ComfyUI first: open it there and save it",
    );
    // A workflow that is not a draft still runs.
    store.selectRange(["a"]);
    await flushPromises();
    expect(rowEnabled(menuRow(wrapper, "run"))).toBe(true);
  });
});

// #1578: Favourite leads the pill and the menu; the verb follows the selection.
describe("Favourite (#1578)", () => {
  beforeEach(() => setActivePinia(createPinia()));

  const favouriteRow = (wrapper) =>
    verbMenus(wrapper)[1]
      .findAll(".ctx-item")
      .find((el) => el.attributes("data-verb") === "favourite");

  it("reads Favourite over a mixed selection, and Unfavourite over one that is all favourites", () => {
    useFavouritesStore().keys = { model: [], workflow: ["a"], set: [] };
    const mixed = bar(["a", "b"]).wrapper;
    expect(favouriteRow(mixed).find(".ctx-label-text").text()).toBe("Favourite");
    expect(favouriteRow(mixed).find(".ctx-shortcut").text()).toBe("H");

    useFavouritesStore().keys = { model: [], workflow: ["a", "b"], set: [] };
    const all = bar(["a", "b"]).wrapper;
    expect(favouriteRow(all).find(".ctx-label-text").text()).toBe("Unfavourite");
  });

  it("is a `⋯` row, not a face verb, and emits favourite from it", async () => {
    // Off the face since #2165: the pill's face is Show pictures, Open in
    // ComfyUI, Export, Delete and Clear. Favourite keeps its row and its H.
    const { wrapper } = bar(["a"]);
    expect(verb(wrapper, "favourite")).toBeUndefined();
    await favouriteRow(wrapper).trigger("click");
    expect(wrapper.emitted("favourite")).toHaveLength(1);
  });

  it("the ⋯ menu holds rename, thumbnail, duplicate, use a set, hide and favourite", () => {
    const { wrapper } = bar(["a"]);
    expect(rowVerbs(verbMenus(wrapper)[0])).toEqual(
      expect.arrayContaining([
        "favourite",
        "rename",
        "set-thumbnail",
        "duplicate",
        "use-set",
        "hide",
        "run",
      ]),
    );
  });
});

// Download missing models: the view asks the pre-flight; the row only says
// whether there is a graph to ask about.
describe("Download missing models", () => {
  beforeEach(() => setActivePinia(createPinia()));

  const rowOf = (wrapper) =>
    verbMenus(wrapper)[0]
      .findAll(".ctx-item")
      .find((el) => el.attributes("data-verb") === "download-missing");

  it("is offered over several workflows, drafts among them, and asks", async () => {
    const store = useWorkflowsStore();
    store.cards = [...CARDS, card("x", { manual: true, draft: true })];
    store.selectRange(["a", "x"]);
    const wrapper = mount(WorkflowSelectionBar, globalOpts);
    const row = rowOf(wrapper);
    expect(row.attributes("aria-disabled")).toBeUndefined();
    await row.trigger("click");
    expect(wrapper.emitted("download-missing")).toHaveLength(1);
    wrapper.unmount();
  });

  it("is refused over drafts alone, saying what finishes one", async () => {
    const store = useWorkflowsStore();
    store.cards = [...CARDS, card("x", { manual: true, draft: true })];
    store.selectRange(["x"]);
    const wrapper = mount(WorkflowSelectionBar, globalOpts);
    const row = rowOf(wrapper);
    expect(row.attributes("aria-disabled")).toBe("true");
    expect(row.find(".tip").attributes("data-text")).toBe(
      "A draft has no graph to check yet: open it in ComfyUI and save it",
    );
    await row.trigger("click");
    expect(wrapper.emitted("download-missing")).toBeUndefined();
    wrapper.unmount();
  });

  // The menu opening asks each selected workflow's pre-flight what it lacks.
  const answer = (...files) => ({
    groups: [
      {
        reasons: [
          {
            code: "missing_models",
            models: files.map((file) => ({ file, downloadable: true })),
          },
        ],
      },
    ],
  });
  async function opened(keys) {
    const store = useWorkflowsStore();
    store.cards = CARDS;
    store.selectRange(keys);
    const wrapper = mount(WorkflowSelectionBar, globalOpts);
    await wrapper.vm.openContextMenu(0, 0);
    await flushPromises();
    return wrapper;
  }

  it("is refused once the workflows lack nothing it can fetch", async () => {
    preflightWorkflowRun.mockReset().mockResolvedValue(answer());
    const wrapper = await opened(["a", "b"]);
    expect(preflightWorkflowRun.mock.calls.map(([body]) => body.workflow_id)).toEqual([
      "a",
      "b",
    ]);
    const row = rowOf(wrapper);
    expect(row.attributes("aria-disabled")).toBe("true");
    expect(row.find(".tip").attributes("data-text")).toBe(
      "Nothing these workflows are missing can be downloaded",
    );
    await row.trigger("click");
    expect(wrapper.emitted("download-missing")).toBeUndefined();
    wrapper.unmount();
  });

  it("is offered when one of them lacks a file it can fetch", async () => {
    preflightWorkflowRun
      .mockReset()
      .mockResolvedValueOnce(answer())
      .mockResolvedValueOnce(answer("vae.safetensors"));
    const wrapper = await opened(["a", "b"]);
    expect(rowOf(wrapper).attributes("aria-disabled")).toBeUndefined();
    wrapper.unmount();
  });

  it("stays offered when a workflow could not be asked", async () => {
    preflightWorkflowRun.mockReset().mockRejectedValue(new Error("gone"));
    const wrapper = await opened(["a"]);
    expect(rowOf(wrapper).attributes("aria-disabled")).toBeUndefined();
    wrapper.unmount();
  });

  it("stays offered when ComfyUI did not answer the check", async () => {
    preflightWorkflowRun.mockReset().mockResolvedValue({
      groups: [{ reasons: [{ code: "comfyui_unreachable" }] }],
    });
    const wrapper = await opened(["a"]);
    expect(rowOf(wrapper).attributes("aria-disabled")).toBeUndefined();
    wrapper.unmount();
  });

  it("stays offered when there was no graph to judge", async () => {
    preflightWorkflowRun.mockReset().mockResolvedValue({
      groups: [{ source: null, reasons: [{ code: "ui_format" }] }],
    });
    const wrapper = await opened(["a"]);
    expect(rowOf(wrapper).attributes("aria-disabled")).toBeUndefined();
    wrapper.unmount();
  });

  it("says a file the pre-flight reports on its way is downloading, and rechecks when it stops", async () => {
    const downloading = {
      groups: [
        {
          reasons: [
            {
              code: "models_downloading",
              models: [{ file: "vae.safetensors", percent: 40 }],
            },
          ],
        },
      ],
    };
    preflightWorkflowRun
      .mockReset()
      .mockResolvedValueOnce(downloading)
      .mockResolvedValueOnce(answer("vae.safetensors"));
    const tasks = useTasksStore();
    tasks.modelDownloads = [
      { id: "d1", file: "vae.safetensors", status: "running" },
    ];
    const wrapper = await opened(["a"]);
    let row = rowOf(wrapper);
    expect(row.attributes("aria-disabled")).toBe("true");
    expect(row.find(".tip").attributes("data-text")).toBe(
      "Already downloading: the Tasks list in the title bar shows how far",
    );
    // It failed: off the running list, and the pre-flight offers it again.
    tasks.modelDownloads = [
      { id: "d1", file: "vae.safetensors", status: "failed" },
    ];
    await flushPromises();
    expect(preflightWorkflowRun).toHaveBeenCalledTimes(2);
    row = rowOf(wrapper);
    expect(row.attributes("aria-disabled")).toBeUndefined();
    wrapper.unmount();
  });

  it("is refused while everything it lacks is already downloading", async () => {
    preflightWorkflowRun
      .mockReset()
      .mockResolvedValue(answer("vae/vae.safetensors"));
    useTasksStore().modelDownloads = [
      { id: "d1", file: "vae.safetensors", status: "running" },
    ];
    const wrapper = await opened(["a"]);
    const row = rowOf(wrapper);
    expect(row.attributes("aria-disabled")).toBe("true");
    expect(row.find(".tip").attributes("data-text")).toBe(
      "Already downloading: the Tasks list in the title bar shows how far",
    );
    wrapper.unmount();
  });
});
