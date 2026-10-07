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

import { beforeEach, describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

import WorkflowSelectionBar from "./WorkflowSelectionBar.vue";
import { useWorkflowsStore } from "../../stores/useWorkflowsStore";

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

/** One pill button, by the verb it carries. */
const verb = (wrapper, name) => wrapper.find(`[data-verb="${name}"]`);

const enabled = (wrapper, name) =>
  verb(wrapper, name).attributes("data-disabled") === "false";

const tooltip = (wrapper, name) =>
  verb(wrapper, name).attributes("data-tooltip");

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
    const { wrapper } = bar(["a"]);
    expect(enabled(wrapper, "run")).toBe(true);
    expect(enabled(wrapper, "rename")).toBe(true);

    const many = bar(["a", "b"]).wrapper;
    expect(enabled(many, "run")).toBe(false);
    expect(enabled(many, "rename")).toBe(false);
    // The refusal is in the tooltip and the verb stays in the aria-label: the
    // button keeps its place so the row never reflows, which is the whole
    // argument for disabling rather than hiding.
    expect(verb(many, "rename").attributes("aria-label")).toBe("Rename");
    expect(tooltip(many, "rename")).toContain("Select one workflow");
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
    expect(
      bar(["d"]).wrapper.find('[data-verb="hide"]').attributes("aria-label"),
    ).toBe("Unhide these workflows");
    // Mixed is Hide: the gesture on "these, and that one already hidden" is to
    // get them all out of the grid.
    expect(
      bar(["a", "d"])
        .wrapper.find('[data-verb="hide"]')
        .attributes("aria-label"),
    ).toBe("Hide these workflows");
  });

  it("does not read as Unhide when the store cannot resolve the selection", () => {
    // A key the grid no longer holds resolves to no card, and `every` over an
    // empty list is vacuously true — which would turn the button into Unhide
    // over a selection nobody has hidden.
    const { wrapper } = bar(["gone"]);
    expect(wrapper.find('[data-verb="hide"]').attributes("aria-label")).toBe(
      "Hide these workflows",
    );
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
    expect(rowVerbs(menu).slice(0, 3)).toEqual([
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
