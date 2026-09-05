// The grid toolbar's app-wide tail and its ⋯ overflow.
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
      GbFilterPanel: true,
      TbComfyPanel: true,
      TbExportPanel: true,
      TbImportPanel: true,
      TbTagPanel: true,
      UndoControl: true,
      TbGlobalActions: true,
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
      backendUrl: "http://backend.test",
      ...props,
    },
  });
}

describe("Toolbar - icon menu attachment", () => {
  it("keeps Search, Export, and Import end-clamped while pointing at each icon centre", async () => {
    const wrapper = mountToolbar(
      {},
      { TbExportPanel: false, TbImportPanel: false },
    );
    const iconMenus = [
      ["Search (F)", ".gb-search-panel"],
      ["Export current grid to zip", ".tb-export-panel"],
      ["Import photos", ".tb-import-panel"],
    ];

    for (const [title, panelSelector] of iconMenus) {
      const menu = wrapper
        .findAll(".v-menu-stub")
        .find((candidate) =>
          candidate.find(`button[title="${title}"]`).exists(),
        );
      expect(menu, `${title} menu`).toBeTruthy();
      expect(menu.attributes("data-location")).toBe("bottom end");
      expect(menu.attributes("data-origin")).toBe("top end");

      await menu.find(`button[title="${title}"]`).trigger("click");
      expect(wrapper.find(`${panelSelector} .tbm-caret`).classes()).toContain(
        "tbm-caret--icon-center-end",
      );
    }

    const { readFileSync } = await import("node:fs");
    const css = readFileSync(`${process.cwd()}/src/App.css`, "utf8");
    expect(css).toMatch(
      /\.tbm-caret--icon-center-end\s*{\s*right:\s*calc\(16px - 5\.5px\);\s*}/,
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

    for (const block of [grid, dq, shelf, insights]) {
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
    const badge = stacked.find(".tbm-toggle-filter-badge");
    expect(badge.exists()).toBe(true);
    expect(badge.attributes("title")).toBe(
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

describe("Toolbar - the canonical app-wide tail", () => {
  // The decision record: every toolbar that writes the operation log ends
  // [separator][UndoControl][TbGlobalActions] (the model shelf does not write
  // it and carries no undo, amendment #4); the ⋯ (amendment #2) is NOT part of
  // the tail - a burger stands at the end of the group it collapses, the left
  // action run.
  it("orders the tail separator → UndoControl → TbGlobalActions", () => {
    const wrapper = mountToolbar();
    const undo = wrapper.findComponent({ name: "UndoControl" }).element;
    const globalActions = wrapper.findComponent({
      name: "TbGlobalActions",
    }).element;

    expect(
      undo.previousElementSibling.classList.contains("bar-separator"),
    ).toBe(true);
    expect(precedes(undo, globalActions)).toBe(true);
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

  // The separator amendments: a rule marks a SEMANTIC boundary, not a group
  // edge - the elastic gap already draws left|right. Two rules remain, and
  // with the burger anchoring the action run (amendment #2) BOTH render at
  // all widths: G-S1's flanks stay populated down to the [Search][⋯] floor,
  // so it carries no fold class any more.
  it("renders exactly two separators, neither carrying a fold class", () => {
    const wrapper = mountToolbar();
    const separators = wrapper.findAll(".bar-separator");
    expect(separators).toHaveLength(2);
    for (const separator of separators) {
      expect(separator.classes()).not.toContain("tb-fold-700");
      expect(separator.classes()).not.toContain("tb-fold-600");
    }
    // The gap-guard variant is deleted outright.
    expect(wrapper.find(".bar-separator--gap-guard").exists()).toBe(false);
    // No rule leads the right group: its first element child is a control.
    const right = wrapper.find(".selection-bar-right").element;
    expect(right.firstElementChild.classList.contains("bar-separator")).toBe(
      false,
    );
  });

  it("mounts UndoControl exactly once - the left-group copy is gone", () => {
    const wrapper = mountToolbar();
    expect(wrapper.findAllComponents({ name: "UndoControl" })).toHaveLength(1);
  });

  // A read-only session KEEPS the control and inerts it (the demo has to show
  // that undo exists), so the tail is the same shape at every access level.
  // UndoControl owns the disabled state; this pins only that it is still here.
  it("keeps UndoControl in a read-only session, tail otherwise intact", () => {
    readOnlyRef.value = true;
    const wrapper = mountToolbar();
    expect(wrapper.findComponent({ name: "UndoControl" }).exists()).toBe(true);
    expect(wrapper.findComponent({ name: "TbGlobalActions" }).exists()).toBe(
      true,
    );
    expect(wrapper.find(".tb-overflow").exists()).toBe(true);
  });
});

// The zip holds the grid's selection when there is one (ImageGrid's
// exportCurrentViewToZip sends the selected ids and nothing else), so the
// control has to name that subset instead of promising the whole grid.
describe("Toolbar - the export control names what it will export", () => {
  const exportTitle = (wrapper) =>
    wrapper
      .findAll("button.tb-export-btn")
      .map((b) => b.attributes("title"))
      .at(0);

  it("offers the whole grid while nothing is selected", () => {
    expect(exportTitle(mountToolbar({ selectedCount: 0 }))).toBe(
      "Export current grid to zip",
    );
  });

  it("counts the selection when a subset of the grid is selected", () => {
    expect(exportTitle(mountToolbar({ selectedCount: 12 }))).toBe(
      "Export 12 pictures to zip",
    );
  });

  it("stays singular for one picture", () => {
    expect(exportTitle(mountToolbar({ selectedCount: 1 }))).toBe(
      "Export 1 picture to zip",
    );
  });

  it("carries the same count into the folded ⋯ row", async () => {
    const wrapper = mountToolbar({ selectedCount: 3 });
    await wrapper.find(".tbo-trigger").trigger("click");
    const labels = wrapper
      .find(".tbo-panel")
      .findAll(".tbm-action")
      .map((b) => b.text());
    expect(labels).toContain("Export 3 pictures to zip");
    expect(labels).not.toContain("Export grid to zip");
  });
});

describe("Toolbar - the ⋯ overflow mirrors its controls", () => {
  async function openOverflow(wrapper) {
    await wrapper.find(".tbo-trigger").trigger("click");
    return wrapper.find(".tbo-panel");
  }

  // Amendment #2: the burger holds ONLY its own group's members. Review,
  // Settings, Stats and History never appear here - Review stays a visible
  // button at all widths, Settings/Stats never fold, and below 480 the
  // History popover is simply unavailable (accepted, documented loss).
  it("carries rows for its own group's foldables and nothing else", async () => {
    const filterStore = useFilterStore();
    filterStore.comfyuiConfigured = true;
    const wrapper = mountToolbar();
    const panel = await openOverflow(wrapper);
    const labels = panel.findAll(".tbm-action").map((b) => b.text());
    expect(labels).toEqual([
      "Export grid to zip",
      "Import photos…",
      "Generate with ComfyUI…",
      "View options…",
    ]);
  });

  // The rows honour the SAME v-ifs as the buttons they mirror: no ComfyUI
  // configured, no row; read-only drops Import (owner-only dialog) and
  // History (there is no UndoControl to open).
  it("mirrors the v-ifs: ComfyUI row only when configured", async () => {
    const wrapper = mountToolbar();
    const panel = await openOverflow(wrapper);
    const labels = panel.findAll(".tbm-action").map((b) => b.text());
    expect(labels).not.toContain("Generate with ComfyUI…");
  });

  it("honours read-only: the Import row is gone", async () => {
    readOnlyRef.value = true;
    const wrapper = mountToolbar();
    const panel = await openOverflow(wrapper);
    const labels = panel.findAll(".tbm-action").map((b) => b.text());
    expect(labels).not.toContain("Import photos…");
    // Review never folds (amendment #2), so no row exists to gate.
    expect(labels).not.toContain("Review and fix tags…");
  });

  it("emits the same intents as the buttons it mirrors", async () => {
    const wrapper = mountToolbar();
    const panel = await openOverflow(wrapper);
    const row = (label) =>
      panel.findAll(".tbm-action").find((b) => b.text() === label);

    await row("Export grid to zip").trigger("click");
    expect(wrapper.emitted("confirm-export-zip")).toHaveLength(1);

    await openOverflow(wrapper);
    await row("Import photos…").trigger("click");
    expect(wrapper.emitted("open-import")).toHaveLength(1);
  });

  // The controls the amendment pinned OUT of the burger stay first-class.
  it("keeps Review and the global pair as visible controls", () => {
    const wrapper = mountToolbar();
    expect(wrapper.find('button[title="Review and fix tags"]').exists()).toBe(
      true,
    );
    expect(
      wrapper.find('button[title="Review and fix tags"]').classes(),
    ).not.toContain("tb-fold-700");
    expect(wrapper.findComponent({ name: "TbGlobalActions" }).exists()).toBe(
      true,
    );
  });
});
