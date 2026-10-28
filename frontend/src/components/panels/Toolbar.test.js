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
import { flushPromises, mount } from "@vue/test-utils";

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

// Vuetify is not installed in the test app; v-menu is stubbed with the
// behaviours the toolbar relies on (activator slot props carry the toggle and,
// as Vuetify 3's VMenu does, the popup ARIA merged with `activatorProps`;
// default slot renders inline while open).
const VMenuStub = {
  name: "VMenu",
  props: {
    modelValue: { type: Boolean, default: false },
    location: { type: String, default: "" },
    origin: { type: String, default: "" },
    activatorProps: { type: Object, default: () => ({}) },
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
              "aria-haspopup": "menu",
              "aria-expanded": String(props.modelValue),
              ...props.activatorProps,
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
  // The field itself, not a trigger for a popover: at rest the 260 px field
  // with F on it; focused it widens in place and Sort's label and View step
  // aside.
  it("rests as a field named Search, with F on it", () => {
    const wrapper = mountToolbar();
    const field = wrapper.find('.stf button.stf-rest[aria-label="Search"]');
    expect(field.exists()).toBe(true);
    expect(field.attributes("aria-keyshortcuts")).toBe("F");
    expect(field.text()).toContain("Search");
    expect(field.find("kbd").text()).toBe("F");
    expect(wrapper.find(".gb-search-panel").exists()).toBe(false);
  });

  it("opens in place on F, and the bar steps aside while it is open", async () => {
    const { useSearchStore } = await import("../../stores/useSearchStore");
    const wrapper = mountToolbar();
    const bar = wrapper.find(".selection-bar-overlay");
    expect(bar.classes()).not.toContain("selection-bar-overlay--searching");

    useSearchStore().requestSearchFocus();
    await flushPromises();
    expect(wrapper.find(".stf").classes()).toContain("stf--open");
    expect(wrapper.find("input.stf-input").exists()).toBe(true);
    expect(bar.classes()).toContain("selection-bar-overlay--searching");
  });

  // jsdom applies no scoped CSS, so the rules themselves are what can be
  // pinned: the open field hides View, and touches Sort's value only inside
  // the 700px container step, where the field would otherwise be squeezed.
  it("keeps Sort's value visible while the search is open, wide bars only", async () => {
    const { readFileSync } = await import("node:fs");
    const source = readFileSync(
      `${process.cwd()}/src/components/panels/Toolbar.vue`,
      "utf8",
    ).replace(/\/\*[\s\S]*?\*\//g, "");
    // Selectors of the --searching rules, each with the at-rule it sits in.
    const rules = [];
    const stack = [];
    let text = "";
    for (const ch of source) {
      if (ch === "{") {
        if (text.includes("--searching")) rules.push([text, stack.at(-1)]);
        stack.push(text.trim());
        text = "";
      } else if (ch === "}") {
        stack.pop();
        text = "";
      } else if (ch === ";") {
        text = "";
      } else {
        text += ch;
      }
    }
    const sortRule = /bar-split|bar-btn-sort/;
    const unconditional = rules.filter(([, parent]) => !parent?.startsWith("@"));
    expect(unconditional.map(([sel]) => sel).join()).toContain(".tb-view");
    for (const [selectors] of unconditional) {
      expect(selectors).not.toMatch(sortRule);
    }
    const sortRules = rules.filter(([sel]) => sortRule.test(sel));
    expect(sortRules.map(([, parent]) => parent)).toEqual([
      "@container selbar (max-width: 700px)",
    ]);
  });

  it("shows a committed query and the filters as its summary", async () => {
    const { useSearchStore } = await import("../../stores/useSearchStore");
    useSearchStore().searchQuery = "red hair";
    useFilterStore().tagFilter = ["rain"];
    const wrapper = mountToolbar();
    const field = wrapper.find("button.stf-rest");
    expect(field.text()).toContain("red hair · rain");
    expect(field.classes()).toContain("stf-rest--active");
  });

  // One state, several views: the funnel counts what the strip shows.
  it("counts the active filters on the Filter button", () => {
    useFilterStore().tagFilter = ["rain", "umbrella"];
    const wrapper = mountToolbar();
    expect(
      wrapper.find('[aria-label="Filters"] .bar-filter-badge').text(),
    ).toBe("2");
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

    // The queue LEADS with an identity and a quieter count beside it; the
    // shelf dropped its title (the sidebar names the view) but keeps the count.
    // They read as one bar in two contexts only if that count is one size and
    // one ink: the shelf once shipped its count at a third alpha.
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

    expect(decl(shelfSub, "font-size")).toBe(decl(qsub, "font-size"));
    expect(decl(shelfSub, "color")).toBe(decl(qsub, "color"));
    expect(decl(insTitle, "font-size")).toBe(decl(qtitle, "font-size"));
    expect(decl(insTitle, "font-weight")).toBe(decl(qtitle, "font-weight"));
    expect(decl(insSub, "font-size")).toBe(decl(qsub, "font-size"));
    expect(decl(insSub, "color")).toBe(decl(qsub, "color"));

    // Moves leads with an identity too, and was unguarded: the workflow
    // library shipped its title at --text-lg and its count at a fourth ink,
    // the same drift the shelf's --text-xl was (#1415). The Workflows bar is
    // browse-only since #2165: its view is named by the rail head and the
    // sidebar counts it, so it carries no identity pair to drift.
    expect(readFileSync(
      `${process.cwd()}/src/components/views/WorkflowsView.vue`,
      "utf8",
    )).not.toMatch(/class="wfv-(title|sub)"/);
    const mvTitle = blockOf(
      "src/components/views/MovesReview.vue",
      ".mv-title",
    );
    const mvSub = blockOf("src/components/views/MovesReview.vue", ".mv-sub");

    for (const [title, sub] of [[mvTitle, mvSub]]) {
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
    expect(mvTitle).toContain("min-width: 0");
    expect(mvTitle).toMatch(/flex-shrink: [1-9]/);
    // The Workflows bar has no identity since #2165; its Search field gives.
    const wfSearch = blockOf(
      "src/components/views/WorkflowsView.vue",
      ".wfv-search",
    );
    expect(wfSearch).toMatch(/flex: 0 [1-9] 260px/);
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
      precedes(wrapper.find(".stf").element, globalActions),
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

  // Generate left the bar, and with it the rule that marked the lens run |
  // Generate boundary: nothing follows the lens run for it to separate.
  it("offers no Generate and draws no rule, even with ComfyUI set up", () => {
    const filterStore = useFilterStore();
    filterStore.comfyuiConfigured = true;
    const wrapper = mountToolbar();
    expect(wrapper.findAll(".selection-bar-left .bar-separator")).toHaveLength(
      0,
    );
    expect(
      wrapper.find('button[aria-label^="Generate new pictures"]').exists(),
    ).toBe(false);
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
    expect(labels).toEqual(["View options…"]);
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
