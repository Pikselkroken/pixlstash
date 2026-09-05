// Wizard step 2 ("MapTree", DECISIONS.md pass 6). What is pinned here is the
// house selection rule - acting on a selected row acts on the whole selection
// in that level - the band's three labels and what each one applies to, the
// Mixed read, and the scrollbar offset being measured rather than typed.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mount } from "@vue/test-utils";
import { h, nextTick } from "vue";

// The menu is always open: every row's and band's items are in the DOM, so a
// test picks the item it wants by `data-kind` inside the row it wants.
vi.mock("vuetify/components", () => ({
  VMenu: {
    name: "VMenu",
    setup(_, { slots }) {
      return () => h("div", { class: "v-menu-stub" }, [slots.activator?.({ props: {} }), slots.default?.()]);
    },
  },
}));

import FolderMappingTreeStep from "./FolderMappingTreeStep.vue";

function folder(id, depth, name, kind = null, candidates = []) {
  return {
    id,
    depth,
    name,
    relative_path: depth === 1 ? "" : name,
    picture_count: 10,
    proposal: { kind, candidates, match: null, evidence: [] },
  };
}

function level(depth, folders, kind = null) {
  return { depth, folder_count: folders.length, folders, proposal: { kind, candidates: [], evidence: [] } };
}

function makeResult() {
  return {
    levels: [
      level(1, [folder("1/0", 1, "Library")]),
      level(
        2,
        ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta"].map((n, i) => folder(`2/${i}`, 2, n)),
        "project",
      ),
      level(3, [folder("3/0", 3, "Mira", "person"), folder("3/1", 3, "Tests", "person")]),
    ],
  };
}

function mountStep() {
  return mount(FolderMappingTreeStep, {
    props: { result: makeResult() },
    attachTo: document.body,
    global: { stubs: { "v-icon": true, AppButton: { template: "<button><slot /></button>" } } },
  });
}

const levelEl = (w, depth) => w.find(`[data-depth="${depth}"]`);
const rows = (w, depth) => levelEl(w, depth).findAll(".map-tree__row");
const rowKind = (row) => row.find(".map-tree__kdd-label").text();
const bandLabel = (w, depth) => levelEl(w, depth).find(".map-tree__band-label").text();
const bandKdd = (w, depth) => levelEl(w, depth).find(".map-tree__kdd--band");

let wrapper;
afterEach(() => wrapper?.unmount());

describe("the band label", () => {
  it("has three states: all, these N (filtered), N selected", async () => {
    wrapper = mountStep();
    expect(bandLabel(wrapper, 2)).toBe("Set them all to");

    await levelEl(wrapper, 2).find(".map-tree__filter").setValue("eta");
    expect(bandLabel(wrapper, 2)).toBe("Set these 3 to");

    const r = rows(wrapper, 2);
    await r[0].trigger("click");
    await r[1].trigger("click", { ctrlKey: true });
    expect(bandLabel(wrapper, 2)).toBe("Set 2 selected to");
  });

  it("offers no filter box on a level of six rows or fewer", () => {
    wrapper = mountStep();
    expect(levelEl(wrapper, 3).find(".map-tree__filter").exists()).toBe(false);
    expect(levelEl(wrapper, 2).find(".map-tree__filter").exists()).toBe(true);
  });
});

describe("the Mixed read", () => {
  it("reads the one kind when every row agrees, Mixed with a stripe per kind otherwise", async () => {
    wrapper = mountStep();
    const kdd = () => bandKdd(wrapper, 2);
    expect(kdd().text()).toContain("Project");
    expect(kdd().classes()).not.toContain("map-tree__kdd--mixed");

    await rows(wrapper, 2)[2].find('[data-kind="set"]').trigger("click");
    expect(kdd().text()).toContain("Mixed");
    expect(kdd().classes()).toContain("map-tree__kdd--mixed");
    const stripes = kdd().attributes("style");
    expect(stripes).toContain("--v-theme-tertiary");
    expect(stripes).toContain("--v-theme-accent");
    expect(stripes).not.toContain("--v-theme-secondary");
  });
});

describe("the selection rule", () => {
  it("changing one selected row's dropdown changes every selected row in that level only", async () => {
    wrapper = mountStep();
    const r = rows(wrapper, 2);
    await r[0].trigger("click");
    await r[2].trigger("click", { shiftKey: true });
    expect(r.filter((row) => row.classes("map-tree__row--sel"))).toHaveLength(3);

    await r[1].find('[data-kind="tag"]').trigger("click");
    expect(r.slice(0, 3).map(rowKind)).toEqual(["Tag", "Tag", "Tag"]);
    expect(rowKind(r[3])).toBe("Project");
    expect(rows(wrapper, 3).map(rowKind)).toEqual(["Person", "Person"]);
  });

  it("an unselected row acts on itself only", async () => {
    wrapper = mountStep();
    const r = rows(wrapper, 2);
    await r[0].trigger("click");
    await r[4].find('[data-kind="set"]').trigger("click");
    expect(rowKind(r[0])).toBe("Project");
    expect(rowKind(r[4])).toBe("Set");
  });

  it("a digit on a focused selected row sets the selection", async () => {
    wrapper = mountStep();
    const r = rows(wrapper, 2);
    await r[0].trigger("click");
    await r[1].trigger("click", { ctrlKey: true });
    await r[1].trigger("keydown", { key: "3" });
    expect(r.slice(0, 3).map(rowKind)).toEqual(["Person", "Person", "Project"]);
  });

  it("Esc clears the selection", async () => {
    wrapper = mountStep();
    const r = rows(wrapper, 2);
    await r[0].trigger("click");
    expect(bandLabel(wrapper, 2)).toBe("Set 1 selected to");
    await r[0].trigger("keydown", { key: "Escape" });
    expect(bandLabel(wrapper, 2)).toBe("Set them all to");
    expect(r[0].classes()).not.toContain("map-tree__row--sel");
  });

  it("the band's dropdown with a filter active changes only the visible rows", async () => {
    wrapper = mountStep();
    await levelEl(wrapper, 2).find(".map-tree__filter").setValue("eta");
    await levelEl(wrapper, 2).find('.map-tree__band [data-kind="set"]').trigger("click");
    await levelEl(wrapper, 2).find(".map-tree__filter").setValue("");
    // Beta, Zeta, Eta match "eta"; the rest keep the level default.
    expect(rows(wrapper, 2).map(rowKind)).toEqual(["Project", "Set", "Project", "Project", "Project", "Set", "Set"]);
  });

  it("the band's dropdown with nothing filtered or selected sets the whole level", async () => {
    wrapper = mountStep();
    await rows(wrapper, 2)[0].find('[data-kind="set"]').trigger("click");
    await levelEl(wrapper, 2).find('.map-tree__band [data-kind="tag"]').trigger("click");
    expect(new Set(rows(wrapper, 2).map(rowKind))).toEqual(new Set(["Tag"]));
  });
});

describe("what leaves the step", () => {
  it("emits the root and every non-folder row, and nothing for 'later'", async () => {
    wrapper = mountStep();
    await rows(wrapper, 1)[0].find('[data-kind="project"]').trigger("click");
    await rows(wrapper, 2)[0].find('[data-kind="folder"]').trigger("click");
    const buttons = wrapper.findAll(".map-tree__footer button");
    await buttons[1].trigger("click");
    const built = wrapper.emitted("next")[0][0];
    expect(built).toContainEqual({ relative_path: "", kind: "project" });
    expect(built.find((a) => a.relative_path === "Alpha")).toBeUndefined();
    expect(built.filter((a) => a.kind === "project")).toHaveLength(7);
    await buttons[0].trigger("click");
    expect(wrapper.emitted("later")).toHaveLength(1);
  });

  it("renders no 'Show all'", () => {
    wrapper = mountStep();
    expect(wrapper.text()).not.toContain("Show all");
  });
});

describe("the scrollbar offset", () => {
  const proto = HTMLElement.prototype;
  beforeEach(() => {
    Object.defineProperty(proto, "offsetWidth", { configurable: true, get: () => 300 });
    Object.defineProperty(proto, "clientWidth", { configurable: true, get: () => 290 });
  });
  afterEach(() => {
    delete proto.offsetWidth;
    delete proto.clientWidth;
  });

  it("writes --sb from the measured offsetWidth minus clientWidth", async () => {
    wrapper = mountStep();
    await nextTick();
    expect(wrapper.find(".map-tree").attributes("style")).toContain("--sb: 10px");
  });
});
