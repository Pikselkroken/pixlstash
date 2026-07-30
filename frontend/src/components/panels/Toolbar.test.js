// The grid toolbar's app-wide tail and its ⋯ overflow.
//
// jsdom does not evaluate container queries, so the width steps themselves are
// not simulated here — they are covered by the CSS being SHARED (the same
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
import { useSidebarStore } from "../../stores/useSidebarStore";

// Vuetify is not installed in the test app; v-menu is stubbed with the two
// behaviours the toolbar relies on (activator slot props carry the toggle,
// default slot renders inline while open).
const VMenuStub = {
  name: "VMenu",
  props: { modelValue: { type: Boolean, default: false } },
  emits: ["update:modelValue"],
  setup(props, { slots, emit }) {
    return () =>
      h("div", { class: "v-menu-stub" }, [
        slots.activator?.({
          props: {
            onClick: () => emit("update:modelValue", !props.modelValue),
          },
        }),
        props.modelValue ? slots.default?.() : null,
      ]);
  },
};

const globalOpts = {
  global: {
    stubs: {
      "v-icon": true,
      "v-menu": VMenuStub,
      "v-slider": true,
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

function mountToolbar(props = {}) {
  return mount(Toolbar, {
    ...globalOpts,
    props: {
      allPicturesId: "ALL",
      unassignedPicturesId: "UNASSIGNED",
      backendUrl: "http://backend.test",
      ...props,
    },
  });
}

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

describe("Toolbar — the shell band's one box recipe", () => {
  // jsdom computes no layout, so rendered heights cannot be asserted; what
  // CAN be pinned is the coupling itself. `.selection-bar-overlay` is the
  // point of truth for the 36px band recipe and `.dq-toolbar` copies it —
  // the drift this guards against: the dq bar shipped with `min-height` +
  // vertical padding on a content-box, which rendered 41px next to the
  // grid's exact 36px once the 32px app-wide tail buttons landed.
  // (Tokenising the shipped 36px is a recorded lead-designer follow-up;
  // until then this test is the shared source of truth.)
  it("both bars declare the identical height-determining recipe", async () => {
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

    for (const block of [grid, dq]) {
      expect(block).toContain("height: 36px");
      expect(block).toContain("box-sizing: border-box");
      // min-height + vertical padding is exactly the recipe that drifted.
      expect(block).not.toContain("min-height");
      expect(block).toMatch(/padding: 0 var\(--space-\d\)/);
    }

    // The RIGHT insets must be the same token: the app-wide tail is a stable
    // anchor only if its icons land at the identical distance from the edge
    // in every view. (The dq bar's LEFT inset may differ — it aligns with the
    // queue's own content gutter.) Shorthand forms accepted: `0 X` (right =
    // X) and `0 R 0 L` (right = first var).
    const rightInset = (block) => {
      const match = block.match(/padding:\s*0\s+var\((--space-\d)\)/);
      expect(match).toBeTruthy();
      return match[1];
    };
    expect(rightInset(dq)).toBe(rightInset(grid));
  });
});

describe("Toolbar — the canonical app-wide tail", () => {
  // The decision record: EVERY toolbar ends [separator][UndoControl]
  // [TbGlobalActions], with the ⋯ overflow ahead of Undo once folding starts.
  it("orders the tail separator → ⋯ → UndoControl → TbGlobalActions", () => {
    const wrapper = mountToolbar();
    const overflow = wrapper.find(".tb-overflow").element;
    const undo = wrapper.findComponent({ name: "UndoControl" }).element;
    const globalActions = wrapper.findComponent({
      name: "TbGlobalActions",
    }).element;

    expect(
      overflow.previousElementSibling.classList.contains("bar-separator"),
    ).toBe(true);
    expect(precedes(overflow, undo)).toBe(true);
    expect(precedes(undo, globalActions)).toBe(true);
  });

  // The separator amendment: a rule marks a SEMANTIC boundary, not a group
  // edge — the elastic gap already draws left|right. Two rules remain: G-S1
  // (lens run | action run, folding with the actions at ≤700) and G-S4 (the
  // tail boundary, every width). G-S3 (the right group's leading rule) and
  // the G-S2 gap-guard are gone. Width steps live in shared CSS; the class
  // contract is the testable surface under jsdom.
  it("renders exactly two separators: G-S1 (folds at 700) and G-S4 (never)", () => {
    const wrapper = mountToolbar();
    const separators = wrapper.findAll(".bar-separator");
    expect(separators).toHaveLength(2);
    const [gS1, gS4] = separators;
    expect(gS1.classes()).toContain("tb-fold-700");
    expect(gS4.classes()).not.toContain("tb-fold-700");
    // The gap-guard variant is deleted outright.
    expect(wrapper.find(".bar-separator--gap-guard").exists()).toBe(false);
    // No rule leads the right group: its first element child is a control.
    const right = wrapper.find(".selection-bar-right").element;
    expect(
      right.firstElementChild.classList.contains("bar-separator"),
    ).toBe(false);
  });

  it("mounts UndoControl exactly once — the left-group copy is gone", () => {
    const wrapper = mountToolbar();
    expect(wrapper.findAllComponents({ name: "UndoControl" })).toHaveLength(1);
  });

  it("drops UndoControl in a read-only session, tail otherwise intact", () => {
    readOnlyRef.value = true;
    const wrapper = mountToolbar();
    expect(wrapper.findComponent({ name: "UndoControl" }).exists()).toBe(
      false,
    );
    expect(wrapper.findComponent({ name: "TbGlobalActions" }).exists()).toBe(
      true,
    );
    expect(wrapper.find(".tb-overflow").exists()).toBe(true);
  });
});

describe("Toolbar — the ⋯ overflow mirrors its controls", () => {
  async function openOverflow(wrapper) {
    await wrapper.find(".tbo-trigger").trigger("click");
    return wrapper.find(".tbo-panel");
  }

  it("carries a row for every foldable control, same conditions", async () => {
    const filterStore = useFilterStore();
    filterStore.comfyuiConfigured = true;
    const wrapper = mountToolbar();
    const panel = await openOverflow(wrapper);
    const labels = panel.findAll(".tbm-action").map((b) => b.text());
    expect(labels).toEqual([
      "Export grid to zip",
      "Import photos…",
      "Generate with ComfyUI…",
      "Review and fix tags…",
      "View options…",
      "Settings…",
      "Stats sidebar",
      "History…",
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

  it("honours read-only: Import and History rows are gone", async () => {
    readOnlyRef.value = true;
    const wrapper = mountToolbar();
    const panel = await openOverflow(wrapper);
    const labels = panel.findAll(".tbm-action").map((b) => b.text());
    expect(labels).not.toContain("Import photos…");
    expect(labels).not.toContain("History…");
    // Review folds but stays visible-and-disabled, like its button.
    const review = panel
      .findAll(".tbm-action")
      .find((b) => b.text() === "Review and fix tags…");
    expect(review.attributes("disabled")).toBeDefined();
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

    await openOverflow(wrapper);
    await row("Settings…").trigger("click");
    expect(wrapper.emitted("open-settings")).toHaveLength(1);
  });

  it("the Stats row toggles the rail and wears the pressed state", async () => {
    const sidebarStore = useSidebarStore();
    const before = sidebarStore.statsOpen;
    const wrapper = mountToolbar();
    let panel = await openOverflow(wrapper);
    const stats = panel
      .findAll(".tbm-action")
      .find((b) => b.text() === "Stats sidebar");
    expect(stats.attributes("aria-pressed")).toBe(String(before));
    await stats.trigger("click");
    expect(sidebarStore.statsOpen).toBe(!before);
    panel = await openOverflow(wrapper);
    expect(
      panel
        .findAll(".tbm-action")
        .find((b) => b.text() === "Stats sidebar")
        .attributes("aria-pressed"),
    ).toBe(String(!before));
  });
});
