// The open stack's panel (v1.12 F2) — the List view and the member menu.
//
// The parity test is the point of the file. "The same menu in ⋯ and in
// right-click and in both views" is four combinations, and a component that
// builds them from one list passes it by construction — which is exactly why
// it is asserted: the next person to add an item can make them differ in one
// edit, and nothing else in the suite would notice.

import { beforeEach, describe, expect, it, vi } from "vitest";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";

vi.mock("vuetify/components", async () => {
  const { vuetifyComponentStubs } = await import("../../testing/vuetifyStubs");
  const stubs = vuetifyComponentStubs();
  // The menu's content is rendered inline rather than teleported, so the
  // items are findable; `modelValue` still gates it, so "the menu is open"
  // stays a real assertion.
  const VMenu = {
    name: "VMenu",
    props: ["modelValue"],
    template: `<div><slot name="activator" :props="{}" /><slot v-if="modelValue" /></div>`,
  };
  return new Proxy(stubs, {
    get: (target, prop) => (prop === "VMenu" ? VMenu : target[prop]),
  });
});

import StackPanel from "./StackPanel.vue";
import { workflowCoverUrl } from "../../api/workflows";
import { useWorkflowPrefsStore } from "../../stores/useWorkflowPrefsStore";

const member = (key, extra = {}) => ({
  key,
  name: key,
  models: [{ name: "realvisXL_v5", kind: "checkpoint" }],
  loras: [],
  differs_by: [],
  picture_count: 4,
  rating: 4.25,
  covers: [`/thumb/${key}/1`, `/thumb/${key}/2`],
  stack_size: 3,
  member_keys: [],
  ...extra,
});

const MEMBERS = [
  // The cover carries the UNION of what its members differ by — that is what
  // the grid draws on the one tile — so the panel has to blank it rather than
  // list a row's siblings' differences as its own.
  member("cover", { differs_by: ["+ face detailer", "other checkpoint"] }),
  member("second", { differs_by: ["+ face detailer"] }),
  member("third", {
    models: [{ name: "juggernautXL_v9", kind: "checkpoint" }],
    differs_by: ["other checkpoint"],
  }),
];

function makePanel(props = {}) {
  return mount(StackPanel, {
    props: {
      panelId: "panel",
      name: "Cinematic portrait",
      members: MEMBERS,
      size: 3,
      columns: 3,
      canReorder: true,
      ...props,
    },
  });
}

/** The menu's items, in order, as a reader would read them. */
function menuLabels(wrapper) {
  const menu = wrapper.find('[data-testid="member-menu"]');
  if (!menu.exists()) return null;
  // The label, not the whole item: the stubbed `v-icon` renders its name as
  // text and would be compared as part of the item.
  return menu
    .findAll(".ctx-item")
    .map((item) => item.find(".ctx-label-text").text());
}

/** Right-click one member row. */
async function rightClick(wrapper, key) {
  await wrapper.find(`[data-key="${key}"]`).trigger("contextmenu", {
    clientX: 10,
    clientY: 20,
  });
}

/** Press the ⋯ button on one member row. */
async function pressMore(wrapper, key) {
  const row = wrapper.find(`[data-key="${key}"]`);
  await row.find('button[aria-label*="What you can do"]').trigger("click");
}

describe("StackPanel", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.spyOn(console, "warn").mockImplementation(() => {});
    // jsdom's own `localStorage`, emptied: stubbing the whole `window` takes
    // its event constructors with it and `trigger("contextmenu")` then throws.
    window.localStorage.clear();
  });

  it("opens on Grid and draws the members as cards", () => {
    const wrapper = makePanel();
    expect(wrapper.find(".stack-panel__grid").exists()).toBe(true);
    expect(wrapper.find(".stack-panel__list").exists()).toBe(false);
    expect(wrapper.findAll('[data-testid="workflow-card"]')).toHaveLength(3);
  });

  it("draws List as treegrid rows with column gridcells and no table", async () => {
    useWorkflowPrefsStore().setStackView("list");
    const wrapper = makePanel();
    await wrapper.vm.$nextTick();

    expect(wrapper.find("table").exists()).toBe(false);
    const rows = wrapper.findAll(".stack-panel__row");
    expect(rows).toHaveLength(3);
    for (const row of rows) {
      expect(row.attributes("role")).toBe("row");
      expect(row.attributes("aria-level")).toBe("2");
      // Six columns: workflow, checkpoint, differs by, pictures, rating, ⋯.
      expect(row.findAll('[role="gridcell"]')).toHaveLength(6);
    }
    expect(
      wrapper.findAll('[role="columnheader"]').map((cell) => cell.text()),
    ).toEqual([
      "Workflow",
      "Checkpoint",
      "Differs by",
      "Pictures",
      "Rating",
      "",
    ]);
  });

  it("names the cover and, in Checkpoint, shows only what differs", async () => {
    useWorkflowPrefsStore().setStackView("list");
    const wrapper = makePanel();
    await wrapper.vm.$nextTick();

    const rows = wrapper.findAll(".stack-panel__row");
    expect(rows[0].find(".stack-panel__pill").text()).toBe("Cover");
    expect(rows[1].find(".stack-panel__pill").exists()).toBe(false);

    // The cover is what the column is measured against, and `second` shares
    // its checkpoint — so only `third` names one.
    // The first visible chip's label: `ChipRow` keeps a hidden copy of every
    // chip to measure against, so the cell's own `text()` reads each one twice.
    const checkpoints = rows.map((row) => {
      const chip = row.find(".stack-panel__ckpt .chip-row__label");
      return chip.exists() ? chip.text() : "";
    });
    expect(checkpoints).toEqual(["", "", "juggernautXL_v9"]);
  });

  it("draws a thumbnail per cover the member has, and no empty images", async () => {
    useWorkflowPrefsStore().setStackView("list");
    const wrapper = makePanel({
      members: [MEMBERS[0], { ...MEMBERS[1], covers: [] }],
    });
    await wrapper.vm.$nextTick();

    const rows = wrapper.findAll(".stack-panel__row");
    // Three cells always, so one picture does not stretch across the box —
    // but an `<img>` only where there is something to put in it. A `v-show`
    // here made this assertion vacuous: three empty tags whatever `covers`
    // held, in a browser that draws a broken-image glyph for each.
    expect(rows[0].findAll(".stack-panel__thumb")).toHaveLength(3);
    const images = rows[0].findAll("img");
    expect(images).toHaveLength(2);
    for (const image of images) expect(image.attributes("alt")).toBe("");
    expect(rows[1].findAll("img")).toHaveLength(0);

    // **Joined, not the payload path.** `covers` arrives API-relative and an
    // `<img src>` bypasses Axios, so the raw value asks the PAGE origin for a
    // path no route serves — every thumbnail a broken image. `toBeTruthy()`
    // was here and passed on exactly that. Asserted against the api layer's
    // own helper so this does not become a second spelling of the prefix, and
    // against the raw value so the helper cannot be a no-op.
    const raw = MEMBERS[0].covers[0];
    expect(images[0].attributes("src")).toBe(workflowCoverUrl(raw));
    expect(images[0].attributes("src")).not.toBe(raw);
  });

  it("keeps the cover's union out of the row's name as well as its cells", async () => {
    useWorkflowPrefsStore().setStackView("list");
    const wrapper = makePanel();
    await wrapper.vm.$nextTick();

    // Every cell but ⋯ is `aria-hidden`, so the label is the whole of what a
    // screen reader hears. Blanking only the cells would leave it saying the
    // cover differs from itself by what its siblings differ from IT by.
    const label = wrapper.find(".stack-panel__row").attributes("aria-label");
    expect(label).toContain("the stack's cover");
    expect(label).not.toContain("face detailer");
    expect(label).not.toContain("other checkpoint");
    // The rest of the name is untouched, so this is a narrowing and not a
    // silencing: the row still identifies its workflow.
    expect(label).toContain("cover");
    expect(label).toContain("realvisXL_v5");

    // …and a member row still says what it differs by, which is its point.
    const second = wrapper
      .findAll(".stack-panel__row")[1]
      .attributes("aria-label");
    expect(second).toContain("face detailer");
  });

  it("blanks the cover row's Differs by, which is the others' union", async () => {
    useWorkflowPrefsStore().setStackView("list");
    const wrapper = makePanel();
    await wrapper.vm.$nextTick();

    const chips = wrapper
      .findAll(".stack-panel__row")
      .map((row) =>
        row
          .findAll(".stack-panel__facts .chip-row__label")
          .map((chip) => chip.text()),
      );
    // Row 1 is the cover: the column says what is NOT shared, and the cover's
    // own chips are every OTHER row's differences. Listing them there beside
    // a Checkpoint cell blanked for that same reason is the column arguing
    // with itself.
    expect(chips[0]).toEqual([]);
    expect(chips[1]).toContain("+ face detailer");
    expect(chips[2]).toContain("other checkpoint");
  });

  it("draws no checkpoint chip for a model whose name was forgotten", async () => {
    useWorkflowPrefsStore().setStackView("list");
    const wrapper = makePanel({
      members: [
        MEMBERS[0],
        { ...MEMBERS[1], models: [{ name: null, kind: "checkpoint" }] },
        { ...MEMBERS[2], models: [{ name: null, kind: "checkpoint" }] },
      ],
    });
    await wrapper.vm.$nextTick();

    // A chip with no label is a bordered glyph reading "differs, by nothing
    // in particular", and two forgotten names are not evidence of a shared
    // model either.
    expect(wrapper.findAll(".stack-panel__ckpt .chip-row__chip")).toHaveLength(
      0,
    );
  });

  it("offers the same menu from ⋯ and right-click, in Grid and in List", async () => {
    const seen = [];
    for (const view of ["grid", "list"]) {
      for (const open of [pressMore, rightClick]) {
        setActivePinia(createPinia());
        useWorkflowPrefsStore().setStackView(view);
        const wrapper = makePanel();
        await wrapper.vm.$nextTick();
        expect(menuLabels(wrapper)).toBeNull();
        await open(wrapper, "second");
        seen.push(menuLabels(wrapper));
      }
    }
    expect(seen[0]).toEqual([
      "Make it the cover",
      "Move earlier",
      "Move later",
      "Unstack",
      "Hide",
    ]);
    // All four the same list, compared against each other rather than only
    // against the literal: a change to the items keeps this honest.
    for (const labels of seen) expect(labels).toEqual(seen[0]);
  });

  it("emits the menu's verbs for the member under the pointer", async () => {
    const wrapper = makePanel();
    await rightClick(wrapper, "third");
    const items = wrapper.findAll(".ctx-item");

    await items[0].trigger("click");
    expect(wrapper.emitted("make-cover")).toEqual([["third"]]);
    await rightClick(wrapper, "third");
    await wrapper.findAll(".ctx-item")[1].trigger("click");
    expect(wrapper.emitted("move")).toEqual([["third", -1]]);
    await rightClick(wrapper, "third");
    await wrapper.findAll(".ctx-item")[3].trigger("click");
    expect(wrapper.emitted("unstack")).toEqual([["third"]]);
  });

  it("refuses the moves that have nowhere to go, and all of them with no stack id", async () => {
    const wrapper = makePanel();
    await rightClick(wrapper, "cover");
    let disabled = wrapper
      .findAll(".ctx-item")
      .map((item) => item.attributes("disabled") !== undefined);
    // The cover cannot become the cover, nor move earlier; later it can.
    expect(disabled).toEqual([true, true, false, false, false]);

    // …and the last member is the mirror of it: nowhere later to go.
    await rightClick(wrapper, "third");
    disabled = wrapper
      .findAll(".ctx-item")
      .map((item) => item.attributes("disabled") !== undefined);
    expect(disabled).toEqual([false, false, true, false, false]);

    await wrapper.setProps({ canReorder: false });
    await rightClick(wrapper, "second");
    disabled = wrapper
      .findAll(".ctx-item")
      .map((item) => item.attributes("disabled") !== undefined);
    // Nothing to address a reorder by: the three ordering verbs go, the two
    // that name a card alone stay.
    expect(disabled).toEqual([true, true, true, false, false]);
  });

  it("tears the menu down and back up when another row is right-clicked", async () => {
    const wrapper = makePanel();
    await rightClick(wrapper, "second");
    expect(wrapper.find('[data-testid="member-menu"]').exists()).toBe(true);

    // A right button press fires `mousedown` and `contextmenu` but no
    // `click`, so Vuetify's click-outside does not dismiss the open menu —
    // and its location strategy reads the anchor on OPEN, never on change.
    // Left in place it would stay at the first row's coordinates while its
    // verbs acted on the second. Position is not observable in jsdom; that
    // the menu closes and reopens, which is what re-anchors it, is.
    const reopening = rightClick(wrapper, "third");
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-testid="member-menu"]').exists()).toBe(false);
    await reopening;
    expect(wrapper.find('[data-testid="member-menu"]').exists()).toBe(true);

    await wrapper.findAll(".ctx-item")[3].trigger("click");
    expect(wrapper.emitted("unstack")).toEqual([["third"]]);
  });

  it("opens on the row named, when the keyboard asks rather than a pointer", async () => {
    const wrapper = makePanel();
    // The grid's roving cursor owns Tab and both ⋯ buttons sit at
    // `tabindex="-1"`, so this method is the only route Unstack and Hide have
    // for somebody not using a pointer.
    wrapper.vm.openMenuAt("third");
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-testid="member-menu"]').exists()).toBe(true);
    await wrapper.findAll(".ctx-item")[4].trigger("click");
    expect(wrapper.emitted("hide")).toEqual([["third"]]);
  });

  it("switches view without touching the selection", async () => {
    const prefs = useWorkflowPrefsStore();
    const wrapper = makePanel({ selectedKeys: ["second"] });
    expect(
      wrapper.find('[data-key="second"]').attributes("aria-selected"),
    ).toBe("true");

    prefs.setStackView("list");
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".stack-panel__list").exists()).toBe(true);
    expect(
      wrapper.find('[data-key="second"]').attributes("aria-selected"),
    ).toBe("true");
  });
});
