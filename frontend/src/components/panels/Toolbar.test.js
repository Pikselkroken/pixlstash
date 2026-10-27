// The grid toolbar: browse only (#2160), its app-wide tail and its ⋯ overflow.
//
// jsdom does not evaluate container queries, so the width steps themselves are
// not simulated here - they are covered by the CSS being SHARED (the same
// scoped @container rules ship to both bars). What these tests pin is the part
// jsdom can see: the canonical tail order, the fold pairs existing with the
// same v-if on both sides, and read-only degrading the rows exactly as it
// degrades the buttons.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { h } from "vue";
import { setActivePinia, createPinia } from "pinia";
import { mount } from "@vue/test-utils";

vi.mock("../../utils/apiClient", async () => {
  const { ref } = await import("vue");
  return {
    onSessionReset: () => () => {},
    sessionContext: { value: null },
    apiClient: {
      get: vi.fn().mockResolvedValue({ data: {} }),
      post: vi.fn().mockResolvedValue({ data: {} }),
    },
    isReadOnly: ref(false),
    setRequestClientId: vi.fn(),
    API_BASE_URL: "http://backend.test/api/v1",
    newOperationBatchId: () => "cli-test",
  };
});

import Toolbar from "./Toolbar.vue";
import { isReadOnly as readOnlyRef } from "../../utils/apiClient";
import { useFilterStore } from "../../stores/useFilterStore";
import { useSortStore } from "../../stores/useSortStore";
import { useSidebarStore } from "../../stores/useSidebarStore";

// Vuetify is not installed in the test app; v-menu is stubbed with the two
// behaviours the toolbar relies on (activator slot props carry the toggle,
// default slot renders inline while open).
const VMenuStub = {
  name: "VMenu",
  props: {
    modelValue: { type: Boolean, default: false },
    location: { type: String, default: "" },
    origin: { type: String, default: "" },
  },
  emits: ["update:modelValue"],
  setup(props, { slots, emit }) {
    return () =>
      h(
        "div",
        {
          class: "v-menu-stub",
          "data-location": props.location,
          "data-origin": props.origin,
        },
        [
          slots.activator?.({
            props: {
              onClick: () => emit("update:modelValue", !props.modelValue),
            },
          }),
          props.modelValue ? slots.default?.() : null,
        ],
      );
  },
};

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      "v-menu": VMenuStub,
      "v-slider": true,
      "v-switch": true,
      VTooltip: true,
      FilterMenu: true,
      TbTagPanel: true,
      UndoControl: true,
      TbGlobalActions: true,
      // AppDialog's VDialog needs an installed Vuetify; these tests are about
      // the toolbar's menus.
      AppDialog: true,
    },
  },
};

function mountToolbar(props = {}, stubs = {}) {
  return mount(Toolbar, {
    global: {
      ...globalOpts.global,
      stubs: { ...globalOpts.global.stubs, ...stubs },
    },
    props: {
      allPicturesId: "ALL",
      unassignedPicturesId: "UNASSIGNED",
      ...props,
    },
  });
}

describe("Toolbar - the search field", () => {
  // #2160: a field-shaped trigger at the right of the bar, opening the same
  // popover the icon did. The popover is end-aligned under it, so its caret
  // takes the end inset rather than the 32px icon's centre.
  it("opens the search popover from the field, end-clamped", async () => {
    const wrapper = mountToolbar();
    const field = wrapper.find('button.tb-search-field[aria-label="Search"]');
    expect(field.exists()).toBe(true);
    expect(field.attributes("aria-keyshortcuts")).toBe("F");
    expect(field.text()).toContain("Search");
    expect(field.find("kbd").text()).toBe("F");

    const menu = wrapper
      .findAll(".v-menu-stub")
      .find((candidate) => candidate.find("button.tb-search-field").exists());
    expect(menu.attributes("data-location")).toBe("bottom end");
    expect(menu.attributes("data-origin")).toBe("top end");

    await field.trigger("click");
    expect(wrapper.find(".gb-search-panel .tbm-caret").classes()).toContain(
      "tbm-caret--end",
    );
  });

  it("shows a committed query in place of the placeholder", async () => {
    const { useSearchStore } = await import("../../stores/useSearchStore");
    const searchStore = useSearchStore();
    searchStore.searchQuery = "red hair";
    const wrapper = mountToolbar();
    expect(wrapper.find("button.tb-search-field").text()).toContain(
      "red hair",
    );
  });
});

/** Whether `a` precedes `b` in document order. */
function precedes(a, b) {
  return Boolean(
    a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  setActivePinia(createPinia());
  readOnlyRef.value = false;
  vi.spyOn(console, "warn").mockImplementation(() => {});
});

describe("Toolbar - narrow-screen composition", () => {
  it("offers a real navigation control when the sidebar is forced into a drawer", async () => {
    const sidebar = useSidebarStore();
    sidebar.sidebarForcedHidden = true;
    const wrapper = mountToolbar();

    const trigger = wrapper.get('[aria-label="Open library navigation"]');
    expect(trigger.attributes("aria-expanded")).toBe("false");

    await trigger.trigger("click");
    expect(sidebar.sidebarVisible).toBe(true);
    expect(trigger.attributes("aria-expanded")).toBe("true");
  });

  it("keeps toolbar height and grid offset on the same narrow-width ladder", async () => {
    const { readFileSync } = await import("node:fs");
    const toolbar = readFileSync(
      `${process.cwd()}/src/components/panels/Toolbar.vue`,
      "utf8",
    );
    const grid = readFileSync(
      `${process.cwd()}/src/components/views/ImageGrid.css`,
      "utf8",
    );

    for (const height of [96, 112]) {
      expect(toolbar).toContain(`height: ${height}px`);
      expect(grid).toContain(`--selbar-height: ${height}px`);
    }
  });
});

describe("Toolbar - the shell band's one box recipe", () => {
  // jsdom computes no layout, so rendered heights cannot be asserted; what
  // CAN be pinned is the coupling itself. `.selection-bar-overlay` is the
  // point of truth for the 36px band recipe and `.dq-toolbar` and
  // `.shelf-toolbar` copy it - the drift this guards against: the dq bar
  // shipped with `min-height` + vertical padding on a content-box, which
  // rendered 41px next to the grid's exact 36px once the 32px app-wide tail
  // buttons landed, and the shelf bar shipped at `--bar-height` (48px), which
  // stepped the whole content area 12px on every switch to /models.
  // (Tokenising the shipped 36px is a recorded lead-designer follow-up;
  // until then this test is the shared source of truth.)
  it("every view bar declares the identical height-determining recipe", async () => {
    const { readFileSync } = await import("node:fs");
    // Vitest serves modules from a virtual scheme, so the SFC sources are
    // read relative to the frontend/ working directory instead.
    const blockOf = (path, selector) => {
      // Comments stripped: the dq bar's comment NAMES the drifted recipe it
      // replaced, and the guard is about declarations, not prose.
      const source = readFileSync(`${process.cwd()}/${path}`, "utf8").replace(
        /\/\*[\s\S]*?\*\//g,
        "",
      );
      const start = source.indexOf(`${selector} {`);
      expect(start).toBeGreaterThan(-1);
      return source.slice(start, source.indexOf("}", start));
    };

    const grid = blockOf(
      "src/components/panels/Toolbar.vue",
      ".selection-bar-overlay",
    );
    const dq = blockOf(
      "src/components/views/DuplicateQueue.vue",
      ".dq-toolbar",
    );
    const shelf = blockOf(
      "src/components/views/ModelShelf.vue",
      ".shelf-toolbar",
    );
    const insights = blockOf(
      "src/components/views/LibraryInsights.vue",
      ".ins-toolbar",
    );
    // The three bars that gained the app-wide tail in #1415. The Workflows
    // one is `WorkflowsView` since F1b; it copied the shelf's box recipe so
    // the screen that replaced it sits at the same height.
    const workflows = blockOf(
      "src/components/views/WorkflowsView.vue",
      ".wfv-toolbar",
    );
    const moves = blockOf(
      "src/components/views/MovesReview.vue",
      ".mv-toolbar",
    );

    for (const block of [grid, dq, shelf, insights, workflows, moves]) {
      expect(block).toContain("height: 36px");
      expect(block).toContain("box-sizing: border-box");
      // min-height + vertical padding is exactly the recipe that drifted.
      expect(block).not.toContain("min-height");
      expect(block).toMatch(/padding: 0 var\(--space-\d\)/);
      // And every bar paints the chrome surface, not the page. The shelf bar
      // painted nothing, so `.shelf`'s `background` showed through and the
      // strip read as page rather than chrome. (The grid bar's `rgba(…, .95)`
      // is allowed: it is absolutely positioned over the scrolling grid.)
      //
      // EVERY background declaration in the block is checked, not merely that
      // one of them matches: a `toMatch` alone passes on a block that paints
      // the token and then overrides it with `transparent` on the next line,
      // which is the shipped defect itself.
      const paints = block.match(/background:[^;]*/g) ?? [];
      expect(paints).toHaveLength(1);
      expect(paints[0]).toMatch(/rgba?\(var\(--v-theme-toolbar\)/);
    }

    // The RIGHT insets must be the same token: the app-wide tail is a stable
    // anchor only if its icons land at the identical distance from the edge
    // in every view. (The dq and shelf bars' LEFT insets may differ - each
    // aligns with its own view's content gutter.) Shorthand forms accepted:
    // `0 X` (right = X) and `0 R 0 L` (right = first var).
    const rightInset = (block) => {
      const match = block.match(/padding:\s*0\s+var\((--space-\d)\)/);
      expect(match).toBeTruthy();
      return match[1];
    };
    expect(rightInset(dq)).toBe(rightInset(grid));
    expect(rightInset(shelf)).toBe(rightInset(grid));
    expect(rightInset(insights)).toBe(rightInset(grid));
    expect(rightInset(workflows)).toBe(rightInset(grid));
    expect(rightInset(moves)).toBe(rightInset(grid));

    // Two of the three bars LEAD with an identity (the queue's count, the
    // shelf's title) and each carries a quieter count beneath it. They read as
    // one bar in two contexts only if that pair is one size and one ink: the
    // shelf shipped its title at --text-xl, a 22px view heading that does not
    // sit in a 36px band, and its count at a third alpha.
    const decl = (block, property) => {
      const match = block.match(new RegExp(`${property}:\\s*([^;]+);`));
      expect(match, `${property} in ${block.slice(0, 24)}`).toBeTruthy();
      return match[1].trim();
    };
    const qtitle = blockOf(
      "src/components/views/DuplicateQueue.vue",
      ".qtitle",
    );
    const qsub = blockOf("src/components/views/DuplicateQueue.vue", ".qsub");
    const shelfTitle = blockOf(
      "src/components/views/ModelShelf.vue",
      ".shelf-title",
    );
    const shelfSub = blockOf(
      "src/components/views/ModelShelf.vue",
      ".shelf-sub",
    );

    const insTitle = blockOf(
      "src/components/views/LibraryInsights.vue",
      ".ins-title",
    );
    const insSub = blockOf(
      "src/components/views/LibraryInsights.vue",
      ".ins-sub",
    );

    expect(decl(shelfTitle, "font-size")).toBe(decl(qtitle, "font-size"));
    expect(decl(shelfTitle, "font-weight")).toBe(decl(qtitle, "font-weight"));
    expect(decl(shelfSub, "font-size")).toBe(decl(qsub, "font-size"));
    expect(decl(shelfSub, "color")).toBe(decl(qsub, "color"));
    expect(decl(insTitle, "font-size")).toBe(decl(qtitle, "font-size"));
    expect(decl(insTitle, "font-weight")).toBe(decl(qtitle, "font-weight"));
    expect(decl(insSub, "font-size")).toBe(decl(qsub, "font-size"));
    expect(decl(insSub, "color")).toBe(decl(qsub, "color"));

    // The two bars that joined the loop above lead with an identity too, and
    // were unguarded: the workflow library shipped its title at --text-lg and
    // its count at a fourth ink, the same drift the shelf's --text-xl was
    // (#1415).
    const wfTitle = blockOf(
      "src/components/views/WorkflowsView.vue",
      ".wfv-title",
    );
    const wfSub = blockOf(
      "src/components/views/WorkflowsView.vue",
      ".wfv-sub",
    );
    const mvTitle = blockOf(
      "src/components/views/MovesReview.vue",
      ".mv-title",
    );
    const mvSub = blockOf("src/components/views/MovesReview.vue", ".mv-sub");

    for (const [title, sub] of [
      [wfTitle, wfSub],
      [mvTitle, mvSub],
    ]) {
      expect(decl(title, "font-size")).toBe(decl(qtitle, "font-size"));
      expect(decl(title, "font-weight")).toBe(decl(qtitle, "font-weight"));
      expect(decl(sub, "font-size")).toBe(decl(qsub, "font-size"));
      expect(decl(sub, "color")).toBe(decl(qsub, "color"));
    }

    // And the identity is also where each bar GIVES. Every member of these
    // bars is `nowrap`, so a bar with no shrinking member pushes its surplus
    // through the right edge, where the grid area's `overflow: hidden` clips
    // it - and what sits at that edge is the app-wide tail, including the
    // toggle that had just narrowed the column by opening the rail (#1415).
    // `min-width: 0` is the half that actually lets a nowrap flex item shrink.
    const wfTail = blockOf(
      "src/components/views/WorkflowsView.vue",
      ".wfv-bar-tail",
    );
    for (const identity of [wfTitle, mvTitle]) {
      expect(identity).toContain("min-width: 0");
      expect(identity).toMatch(/flex-shrink: [1-9]/);
    }
    expect(wfTail).toContain("flex: 0 0 auto");
  });
});

describe("Toolbar - Recently changed stacks", () => {
  const options = [
    { label: "Date Created", value: "DATE" },
    { label: "Recently changed stacks", value: "STACK_UPDATED_AT" },
  ];

  async function openSortMenu(wrapper) {
    await wrapper.find(".bar-split-menu").trigger("click");
  }

  it("offers the decorated stack-time sort only in the stacked view", async () => {
    const sortStore = useSortStore();
    const filterStore = useFilterStore();
    sortStore.setSortOptions(options);

    const ordinary = mountToolbar();
    await openSortMenu(ordinary);
    expect(ordinary.text()).not.toContain("Recently changed stacks");
    ordinary.unmount();

    filterStore.stackStateFilter = "stacked";
    const stacked = mountToolbar();
    await openSortMenu(stacked);
    expect(stacked.text()).toContain("Recently changed stacks");
    const badge = stacked.find(".gb-sort-filter-badge");
    expect(badge.exists()).toBe(true);
    expect(badge.findComponent({ name: "Tooltip" }).props("text")).toBe(
      "Only available when viewing stacks",
    );
    expect(badge.attributes("aria-label")).toBe(
      "Only available when viewing stacks",
    );
  });

  it("falls back to Date when the stacked view is left", async () => {
    const sortStore = useSortStore();
    const filterStore = useFilterStore();
    sortStore.setSortOptions(options);
    filterStore.stackStateFilter = "stacked";
    sortStore.selectedSort = "STACK_UPDATED_AT";
    const wrapper = mountToolbar();

    filterStore.stackStateFilter = "all";
    await wrapper.vm.$nextTick();

    expect(sortStore.selectedSort).toBe("DATE");
  });
});

describe("Toolbar - browse only (#2160)", () => {
  // With nothing selected, the bar holds no verb that acts on pictures: Export
  // and Train LoRA live on the selection pill, Import and Review tags in the
  // sidebar, and undo on Ctrl+Z and the receipt.
  it("carries no selection verb, no import, no review and no undo", () => {
    const filterStore = useFilterStore();
    filterStore.comfyuiConfigured = true;
    const wrapper = mountToolbar();
    expect(wrapper.find(".tb-export-btn").exists()).toBe(false);
    expect(wrapper.find(".tb-train-btn").exists()).toBe(false);
    expect(wrapper.find('button[aria-label="Import photos"]').exists()).toBe(
      false,
    );
    expect(
      wrapper.find('button[aria-label="Review and fix tags"]').exists(),
    ).toBe(false);
    expect(wrapper.findComponent({ name: "UndoControl" }).exists()).toBe(
      false,
    );
  });

  // The tail is the rail toggle alone, after the search field.
  it("ends with the search field, then the rail toggle", () => {
    const wrapper = mountToolbar();
    const right = wrapper.find(".selection-bar-right").element;
    const globalActions = wrapper.findComponent({
      name: "TbGlobalActions",
    }).element;
    expect(right.lastElementChild).toBe(globalActions);
    expect(
      precedes(wrapper.find("button.tb-search-field").element, globalActions),
    ).toBe(true);
  });

  // Amendment #2: the burger lives at the END of the left group, where the
  // controls it collapses stood - never in the right tail.
  it("mounts the ⋯ as the left group's last child, not in the tail", () => {
    const wrapper = mountToolbar();
    expect(wrapper.find(".selection-bar-left .tb-overflow").exists()).toBe(
      true,
    );
    expect(wrapper.find(".selection-bar-right .tb-overflow").exists()).toBe(
      false,
    );
    const left = wrapper.find(".selection-bar-left").element;
    expect(left.lastElementChild.classList.contains("tb-overflow")).toBe(true);
  });

  // A rule marks a semantic boundary: the lens run | Generate. With nothing
  // after the lens run, the rule would stand boxed at its end, so it goes
  // with Generate.
  it("draws the one rule only when Generate follows it", () => {
    const filterStore = useFilterStore();
    filterStore.comfyuiConfigured = true;
    const withGenerate = mountToolbar();
    const separators = withGenerate.findAll(".bar-separator");
    expect(separators).toHaveLength(1);
    expect(separators[0].classes()).not.toContain("tb-fold-700");

    filterStore.comfyuiConfigured = false;
    expect(mountToolbar().findAll(".bar-separator")).toHaveLength(0);
  });

  it("keeps the tail and the ⋯ in a read-only session", () => {
    readOnlyRef.value = true;
    const wrapper = mountToolbar();
    expect(wrapper.findComponent({ name: "TbGlobalActions" }).exists()).toBe(
      true,
    );
    expect(wrapper.find(".tb-overflow").exists()).toBe(true);
  });
});

describe("Toolbar - the ⋯ overflow mirrors its controls", () => {
  async function openOverflow(wrapper) {
    await wrapper.find(".tbo-trigger").trigger("click");
    return wrapper.find(".tbo-panel");
  }

  // Amendment #2: the burger holds ONLY its own group's members. The search
  // field and the rail toggle never fold.
  it("carries rows for its own group's foldables and nothing else", async () => {
    const filterStore = useFilterStore();
    filterStore.comfyuiConfigured = true;
    const wrapper = mountToolbar();
    const panel = await openOverflow(wrapper);
    const labels = panel.findAll(".ctx-item").map((b) => b.text());
    expect(labels).toEqual(["Generate with ComfyUI…", "View options…"]);
  });

  // The rail panel this used to open is gone (v1.12 F5); the entry stays and
  // opens the Run popup instead. Without a door here, starting a run with
  // nothing selected meant navigating to Workflows first - every other entry
  // point is selection-scoped or needs a picture open.
  it("opens the Run popup with no selection, picker unset", async () => {
    const filterStore = useFilterStore();
    filterStore.comfyuiConfigured = true;
    const wrapper = mountToolbar();
    const { useRunDialogStore } = await import("../../stores/useRunDialogStore");
    const runDialog = useRunDialogStore();

    await wrapper
      .find('button[aria-label^="Generate new pictures"]')
      .trigger("click");

    expect(runDialog.source).toMatchObject({
      kind: "card",
      pickWorkflow: true,
    });
    // No pictures, so the card is the run's own source and the popup picks
    // where the output is filed.
    expect(runDialog.source.pictureIds).toEqual([]);
  });

  it("hides the entry entirely when ComfyUI is not configured", async () => {
    const wrapper = mountToolbar();
    const panel = await openOverflow(wrapper);
    expect(panel.findAll(".ctx-item").map((b) => b.text())).not.toContain(
      "Generate with ComfyUI…",
    );
    expect(
      wrapper.find('button[aria-label^="Generate new pictures"]').exists(),
    ).toBe(false);
  });

  it("keeps the rail toggle as a visible control", () => {
    const wrapper = mountToolbar();
    expect(wrapper.findComponent({ name: "TbGlobalActions" }).exists()).toBe(
      true,
    );
  });
});

describe("Toolbar - the funnel is lit by the filter strip's chips", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  function funnel(wrapper) {
    return wrapper
      .findAll(".v-menu-stub")
      .map((menu) => menu.find("button"))
      .find((b) => b.exists() && b.attributes("aria-label") === "Filters");
  }

  it("is lit while a filter is on, and off when none is", async () => {
    const wrapper = mountToolbar({ selectedCharacter: "ALL" });
    expect(funnel(wrapper).classes()).not.toContain("bar-btn--active");
    useFilterStore().tagFilter = ["hat"];
    await wrapper.vm.$nextTick();
    expect(funnel(wrapper).classes()).toContain("bar-btn--active");
  });

  // "No character" only applies in All Pictures, where it has a chip. In a set
  // view it has none, so the funnel must not claim a filter nobody can see.
  it("stays dark for a No character flag left over outside All Pictures", async () => {
    useFilterStore().noCharacterFilter = true;
    const wrapper = mountToolbar({ selectedCharacter: "null" });
    expect(funnel(wrapper).classes()).not.toContain("bar-btn--active");
  });
});
