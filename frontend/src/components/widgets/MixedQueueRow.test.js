// One row of the Mixed stacks queue.
//
// The model in one line: members start IN the stack, the marked ones are what
// the primary button takes out, and the button's name follows the marks. What
// is pinned here is the part of that a screenshot cannot show: the label and
// the icon crossing the dissolve boundary in both directions, the one stranger
// treatment, and a frozen row that offers no markable tile while keeping its
// primary reachable.

import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";

import MixedQueueRow from "./MixedQueueRow.vue";

// The icon stub renders its slot, because the icon NAME is load-bearing here:
// the primary's glyph has to change at the same instant its label does, and the
// default auto-stub drops the slot that carries it.
const globalOpts = {
  global: { stubs: { "v-icon": { template: "<i><slot /></i>" } } },
};

/** One `MixedStackModel` row, in the backend's shape. */
function stack(over = {}) {
  return {
    stack_id: 42,
    member_count: 5,
    member_ids: [7, 8, 9, 10, 11],
    membership_fingerprint: "fp-1",
    component_count: 2,
    component_sizes: [4, 1],
    components: [[7, 8, 9, 10], [11]],
    largest_component_size: 4,
    stranded_picture_ids: [11],
    weakest_edge: 0.91,
    unhashed_picture_ids: [],
    // Two numbers per member, deliberately. `strongest_edge` is thresholded and
    // is null for the stranger by construction; `nearest_edge` is what that
    // member really measures against its closest sibling, and 11 measures 0.89:
    // just outside the cut, which is exactly the member the page used to write
    // off as matching nothing.
    member_edges: [
      {
        picture_id: 7,
        strongest_edge: 0.97,
        closest_picture_id: 8,
        nearest_edge: 0.97,
        nearest_picture_id: 8,
      },
      {
        picture_id: 8,
        strongest_edge: 0.97,
        closest_picture_id: 7,
        nearest_edge: 0.97,
        nearest_picture_id: 7,
      },
      {
        picture_id: 9,
        strongest_edge: 0.93,
        closest_picture_id: 7,
        nearest_edge: 0.93,
        nearest_picture_id: 7,
      },
      {
        picture_id: 10,
        strongest_edge: 0.91,
        closest_picture_id: 9,
        nearest_edge: 0.91,
        nearest_picture_id: 9,
      },
      {
        picture_id: 11,
        strongest_edge: null,
        closest_picture_id: null,
        nearest_edge: 0.89,
        nearest_picture_id: 10,
      },
    ],
    why: [
      { text: "1 picture is only 89% like the rest", against: true },
      { text: "Weakest match 91%", against: false },
    ],
    suggested_action: "split",
    leader_picture_id: 7,
    ...over,
  };
}

function mountRow(props = {}) {
  return mount(MixedQueueRow, {
    ...globalOpts,
    props: { stack: stack(), markedIds: [11], total: 3, ...props },
  });
}

/** The action column's buttons, in order: primary, Keep, Compare, [queue]. */
const actions = (wrapper) => wrapper.findAll(".gact button");

describe("MixedQueueRow: one tile per member, never collapsed", () => {
  // The whole point of this page is to look INSIDE an existing stack, so the
  // deck the review queue draws would hide precisely what is being judged.
  it("draws every member of the stack", () => {
    const wrapper = mountRow();
    expect(wrapper.findAll(".gthumb")).toHaveLength(5);
    expect(wrapper.find(".gn").text()).toContain("Stack of 5");
  });

  it("states the evidence as facts, not as an argument", () => {
    const wrapper = mountRow();
    const pills = wrapper.findAll(".why-pill");
    expect(pills.length).toBeGreaterThan(0);
    for (const pill of pills) {
      expect(pill.classes()).toContain("why-pill--fact");
      expect(pill.classes()).not.toContain("why-pill--neg");
    }
  });

  // Not-yet-comparable is not a mistake and must never be reported as one.
  it("says the not-yet-analysed members apart from the strangers", () => {
    const wrapper = mountRow({
      stack: stack({ unhashed_picture_ids: [10] }),
    });
    expect(wrapper.find(".mqnote").text()).toContain(
      "has not been analysed yet",
    );
  });
});

describe("MixedQueueRow: one stranger treatment", () => {
  // The engine's mark and the user's are drawn identically, because they
  // behave identically and compose into the one list the button acts on.
  it("borders every marked tile and gives it the split glyph", () => {
    const wrapper = mountRow({ markedIds: [8, 11] });
    const units = wrapper.findAll(".gunit");
    for (const [i, marked] of [false, true, false, false, true].entries()) {
      expect(
        units[i].find(".gthumb").classes().includes("gthumb--marked"),
      ).toBe(marked);
      expect(units[i].find(".gmark").exists()).toBe(marked);
      // Never the excluded fade: a marked tile is the evidence.
      expect(units[i].find(".gthumb").classes()).not.toContain("gthumb--out");
    }
  });

  it("reports a tile press as a cursor move and a mark toggle", async () => {
    const wrapper = mountRow();
    await wrapper.findAll(".gthumb")[2].trigger("click");
    expect(wrapper.emitted("set-cursor")[0]).toEqual([2]);
    expect(wrapper.emitted("toggle-mark")[0]).toEqual([9]);
    expect(wrapper.emitted("focus")).toHaveLength(1);
  });

  // A mixed stack has no cover to choose, so there is ONE gesture and both
  // mouse buttons perform it. That is also what makes Compare's card click
  // match the row exactly.
  it("treats a right-click as the same gesture", async () => {
    const wrapper = mountRow();
    await wrapper.findAll(".gthumb")[4].trigger("contextmenu");
    expect(wrapper.emitted("toggle-mark")[0]).toEqual([11]);
  });

  // The reported bug, on the tile: the stranger measures 89% against its
  // closest sibling and the chip must say so. It was showing a dash, because
  // the only number it had was the thresholded one, which is null for a
  // stranger by definition. "Matches nothing" was never true of this picture.
  it("shows each member's closest match, the stranger's included", () => {
    const chips = mountRow()
      .findAll(".gsmart")
      .map((c) => c.text().replace(/^\S+\s*/, ""));
    expect(chips).toEqual(["97%", "97%", "93%", "91%", "89%"]);
  });

  // The dash survives for the one case that has no measurement at all.
  it("keeps the dash for a member with nothing to compare against", () => {
    const chips = mountRow({
      stack: stack({
        unhashed_picture_ids: [11],
        member_edges: [
          {
            picture_id: 11,
            strongest_edge: null,
            closest_picture_id: null,
            nearest_edge: null,
            nearest_picture_id: null,
          },
        ],
      }),
    })
      .findAll(".gsmart")
      .map((c) => c.text().replace(/^\S+\s*/, ""));
    expect(chips[4]).toBe("–");
  });
});

describe("MixedQueueRow: the primary names its outcome", () => {
  it("splits the marked members while a majority survives", () => {
    const primary = actions(mountRow({ markedIds: [11] }))[0];
    expect(primary.text()).toContain("Split off 1");
    expect(primary.html()).toContain("mdi-call-split");
  });

  // The dissolve boundary, in both directions, label and icon together.
  it("flips to Unstack all N when fewer than two would be left, and back", async () => {
    const wrapper = mountRow({ markedIds: [9, 10, 11] });
    expect(actions(wrapper)[0].text()).toContain("Split off 3");
    expect(actions(wrapper)[0].html()).toContain("mdi-call-split");

    await wrapper.setProps({ markedIds: [8, 9, 10, 11] });
    expect(actions(wrapper)[0].text()).toContain("Unstack all 5");
    expect(actions(wrapper)[0].html()).toContain("mdi-layers-off");
    expect(actions(wrapper)[0].html()).not.toContain("mdi-call-split");

    await wrapper.setProps({ markedIds: [9, 10, 11] });
    expect(actions(wrapper)[0].text()).toContain("Split off 3");
    expect(actions(wrapper)[0].html()).toContain("mdi-call-split");
  });

  it("names the unstack when nothing is marked at all", () => {
    expect(actions(mountRow({ markedIds: [] }))[0].text()).toContain(
      "Unstack all 5",
    );
  });

  it("offers Compare over every member", () => {
    expect(mountRow().find(".gcompare").text()).toContain("Compare all 5");
  });
});

describe("MixedQueueRow: only Keep acts in bulk", () => {
  // The primary's outcome differs per row (one stack splits, the next
  // dissolves), so a bulk primary could not name what it was about to do.
  it("renames Keep for a selection and leaves the primary alone", () => {
    const buttons = actions(
      mountRow({ selected: true, selectionCount: 12, markedIds: [11] }),
    );
    expect(buttons[0].text()).toContain("Split off 1");
    expect(buttons[0].text()).not.toContain("12");
    expect(buttons[1].text()).toContain("Keep 12 stacks");
  });

  it("wears the K chip on a selected row while the bulk gesture is live", () => {
    const buttons = actions(
      mountRow({ selected: true, selectionCount: 12, bulkKeys: true }),
    );
    expect(buttons[1].find("kbd").exists()).toBe(true);
    // Enter never acts on the selection, so its chip stays on the cursor's row.
    expect(buttons[0].find("kbd").exists()).toBe(false);
  });
});

describe("MixedQueueRow: a frozen row", () => {
  const frozen = () =>
    stack({ stackable: false, blocked_by_sets: [{ id: 3, name: "Frozen" }] });

  // A locked set refuses split and unstack alike, and refuses the whole stack.
  it("offers no markable tile at all", () => {
    const wrapper = mountRow({ stack: frozen() });
    const units = wrapper.findAll(".gunit");
    expect(units).toHaveLength(5);
    for (const unit of units) {
      expect(unit.find(".gthumb").classes()).toContain("gthumb--locked");
    }
  });

  // `aria-disabled`, never the `disabled` attribute: a disabled button leaves
  // the tab order, so a keyboard user could never reach the control to discover
  // why it does nothing.
  it("keeps the primary reachable and points it at the reason", () => {
    const wrapper = mountRow({ stack: frozen(), focused: true });
    const primary = actions(wrapper)[0];
    expect(primary.attributes("aria-disabled")).toBe("true");
    expect(primary.attributes("disabled")).toBeUndefined();
    expect(primary.attributes("tabindex")).toBe("0");
    const note = wrapper.find(".mqlock");
    expect(note.text()).toContain("Frozen by locked set 'Frozen'");
    expect(primary.attributes("aria-describedby")).toBe(note.attributes("id"));
  });

  // Keep writes a dismissal, not a picture, so the backend's keep route carries
  // no lock guard: disabling it would strand the one row the user cannot
  // otherwise clear from the list.
  it("leaves Keep live", () => {
    const keep = actions(mountRow({ stack: frozen() }))[1];
    expect(keep.attributes("aria-disabled")).toBeUndefined();
    expect(keep.attributes("disabled")).toBeUndefined();
  });

  // The payload rolls the lock up over the whole stack and names no member, so
  // the chip waits for a refusal that does.
  it("puts the lock chip only on the pictures a refusal named", () => {
    const wrapper = mountRow({ stack: frozen(), flashIds: [11] });
    const chips = wrapper.findAll(".glock");
    expect(chips).toHaveLength(1);
    expect(wrapper.findAll(".gunit")[4].find(".glock").exists()).toBe(true);
    expect(wrapper.find(".glock").classes()).toContain("glock--flash");
  });

  it("does not offer the action column at all in a read-only session", () => {
    const wrapper = mountRow({ readOnly: true });
    expect(wrapper.find(".gbtn").exists()).toBe(false);
    // Reading is not a verdict, so Compare stays.
    expect(wrapper.find(".gcompare").exists()).toBe(true);
  });
});

describe("MixedQueueRow: the focus treatment", () => {
  it("keeps an unfocused row's controls out of the tab order", () => {
    const wrapper = mountRow({ focused: false });
    for (const button of wrapper.findAll("button")) {
      expect(button.attributes("tabindex")).toBe("-1");
    }
  });

  it("draws the cursor rail only on the focused row", () => {
    expect(
      mountRow({ focused: false, cursorIndex: 2 })
        .find(".gunit--cursor")
        .exists(),
    ).toBe(false);
    expect(
      mountRow({ focused: true, cursorIndex: 2 })
        .findAll(".gunit")[2]
        .classes(),
    ).toContain("gunit--cursor");
  });

  it("names the whole row for a screen reader, lock and outcome included", () => {
    const wrapper = mountRow({
      stack: stack({
        stackable: false,
        blocked_by_sets: [{ id: 3, name: "Frozen" }],
      }),
      index: 1,
      total: 3,
    });
    const name = wrapper.attributes("aria-label");
    expect(name).toContain("Stack 2 of 3");
    expect(name).toContain("Stack of 5");
    expect(name).toContain("Split off 1");
    expect(name).toContain("Frozen by locked set 'Frozen'");
  });

  it("refuses the browser's text selection on a modified press", async () => {
    const wrapper = mountRow();
    for (const modifier of ["shiftKey", "ctrlKey", "metaKey"]) {
      const event = new MouseEvent("mousedown", {
        [modifier]: true,
        cancelable: true,
      });
      wrapper.element.dispatchEvent(event);
      expect(event.defaultPrevented).toBe(true);
    }
  });

  it("opens Compare on a double-click on the row surface", async () => {
    const wrapper = mountRow();
    await wrapper.trigger("dblclick");
    expect(wrapper.emitted("compare")).toHaveLength(1);
  });
});
