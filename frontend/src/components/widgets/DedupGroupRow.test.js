// One group in the triage queue.
//
// The tests pin the two things the row owes the keyboard and the screen reader,
// neither of which is visible in a screenshot: only the focused row is a tab
// stop, and the focused row says so in something other than CSS.

import { describe, it, expect } from "vitest";
import { mount } from "@vue/test-utils";

import DedupGroupRow from "./DedupGroupRow.vue";

const globalOpts = { global: { stubs: { "v-icon": true } } };

/** A group of `n` candidates, in the backend's shape. */
function group(n = 3) {
  return {
    signature: "g1",
    tier: "near",
    confidence: 0.94,
    member_count: n,
    cover_picture_id: 1,
    why: [{ text: "same dimensions", against: false }],
    candidates: Array.from({ length: n }, (_, i) => ({ picture_id: i + 1 })),
  };
}

function mountRow(props = {}) {
  return mount(DedupGroupRow, {
    ...globalOpts,
    props: { group: group(), index: 0, coverId: 1, ...props },
  });
}

describe("DedupGroupRow — the tab order", () => {
  // Twenty groups on screen is well over a hundred buttons. A Tab key that
  // walks all of them is a Tab key nobody presses twice.
  it("keeps every control out of the tab order on an unfocused row", () => {
    const wrapper = mountRow({ focused: false });
    const tabbable = wrapper
      .findAll("button")
      .filter((b) => b.attributes("tabindex") !== "-1");
    expect(tabbable).toHaveLength(0);
  });

  // The focused row is the only row the keyboard model acts on, so it is the
  // only row Tab should reach.
  it("puts the focused row's controls in the tab order", () => {
    const wrapper = mountRow({ focused: true });
    const buttons = wrapper.findAll("button");
    expect(buttons.length).toBeGreaterThan(0);
    for (const button of buttons) {
      expect(button.attributes("tabindex")).toBe("0");
    }
  });
});

describe("DedupGroupRow — modified clicks select rows, not text", () => {
  // Shift-click means "extend the row selection". The browser reads the same
  // gesture as "extend the text selection", and it acts on mousedown, before
  // the click handler runs — so the row must refuse the default there.
  it("prevents the browser default on a shift or ctrl/cmd press", () => {
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

  // A plain press keeps its default: text in the row stays selectable the
  // ordinary way.
  it("leaves an unmodified press alone", () => {
    const wrapper = mountRow();
    const event = new MouseEvent("mousedown", { cancelable: true });
    wrapper.element.dispatchEvent(event);
    expect(event.defaultPrevented).toBe(false);
  });
});

describe("DedupGroupRow — what assistive tech is told", () => {
  // The focused-row treatment is five CSS signals and nothing else, which says
  // nothing at all to a screen reader.
  it("marks the focused row as current", () => {
    expect(mountRow({ focused: true }).attributes("aria-current")).toBe("true");
    expect(
      mountRow({ focused: false }).attributes("aria-current"),
    ).toBeUndefined();
  });

  it("names the row and its size", () => {
    const wrapper = mountRow({ index: 4 });
    expect(wrapper.attributes("aria-label")).toBe("Group 5, 3 pictures");
  });

  // Without a label every candidate reaches a screen reader as the same
  // unlabelled control repeated N times: the image is deliberately decorative.
  it("names each thumbnail by its position and its state", () => {
    const wrapper = mountRow({ coverId: 2, excludedIds: [3] });
    const labels = wrapper
      .findAll(".gthumb")
      .map((b) => b.attributes("aria-label"));
    expect(labels).toEqual([
      "Picture 1 of 3",
      "Picture 2 of 3, cover",
      "Picture 3 of 3, not in the stack",
    ]);
  });

  // Only the focused row answers to 1-9 and X, so only the focused row may
  // claim the keys work.
  it("names the keys in the tooltip only where they work", () => {
    expect(
      mountRow({ focused: true }).find(".gthumb").attributes("title"),
    ).toContain("press 1");
    expect(
      mountRow({ focused: false }).find(".gthumb").attributes("title"),
    ).not.toContain("press 1");
  });
});

describe("DedupGroupRow — what the verdicts cost", () => {
  // Neither verdict asks for a confirmation, so each has to say what it does
  // before it is pressed rather than after.
  it("says that stacking deletes nothing and can be undone", () => {
    const title = mountRow().find(".gbtn--stack").attributes("title");
    expect(title).toContain("stays on disk");
    expect(title).toContain("Ctrl+Z");
  });

  // The verdict is remembered, and the pictures survive it. The copy must not
  // promise a "reopen" affordance that does not exist yet.
  it("says that keeping separate is remembered and loses nothing", () => {
    const title = mountRow().findAll(".gbtn")[1].attributes("title");
    expect(title).toContain("stay in your library");
    expect(title).toContain("stop being suggested");
    expect(title).not.toContain("reopen");
  });

  it("locks both verdicts in a read-only session", () => {
    const wrapper = mountRow({ readOnly: true });
    for (const button of wrapper.findAll(".gbtn")) {
      expect(button.attributes("disabled")).toBeDefined();
    }
    // Comparing is reading, so it stays live.
    expect(wrapper.find(".gcompare").attributes("disabled")).toBeUndefined();
  });
});

describe("DedupGroupRow — double-click opens Compare", () => {
  // Double-click means "open this" everywhere files are listed, so the row
  // answers it with the same Compare the C key and the button reach.
  it("emits compare on a double-click on the row surface", async () => {
    const wrapper = mountRow();
    await wrapper.trigger("dblclick");
    expect(wrapper.emitted("compare")).toHaveLength(1);
  });

  // The two single clicks a dblclick delivers pick the same cover twice,
  // which is idempotent; Compare then opens over exactly that state.
  it("opens compare from a thumbnail without losing the cover pick", async () => {
    const wrapper = mountRow();
    const thumb = wrapper.findAll(".gthumb")[1];
    await thumb.trigger("click");
    await thumb.trigger("click");
    await thumb.trigger("dblclick");
    expect(wrapper.emitted("set-cover")).toEqual([[2], [2]]);
    expect(wrapper.emitted("compare")).toHaveLength(1);
  });

  // A fast double press on Stack is two Stack clicks (guarded by busy); it
  // must not ALSO raise a dialog over whatever group slid into the row.
  it("leaves the action buttons their own double-click meaning", async () => {
    const wrapper = mountRow();
    await wrapper.find(".gbtn--stack").trigger("dblclick");
    await wrapper.find(".gcompare").trigger("dblclick");
    expect(wrapper.emitted("compare")).toBeUndefined();
  });

  // Ctrl/Shift clicks are the selection gestures, and they double-fire
  // harmlessly; a modified double-click must not open anything.
  it("ignores a modified double-click", async () => {
    const wrapper = mountRow();
    await wrapper.trigger("dblclick", { ctrlKey: true });
    await wrapper.trigger("dblclick", { shiftKey: true });
    expect(wrapper.emitted("compare")).toBeUndefined();
  });
});

describe("DedupGroupRow — the size control", () => {
  // One number drives the row. Sizing the box in CSS and the placeholder in JS
  // from two copies of the height is how a row starts jumping as it decodes.
  it("lays the strip out from the height it is given", () => {
    const style = mountRow({ thumbHeight: 184 }).find(".gstrip").attributes("style");
    expect(style).toContain("--gthumb-h: 184px");
    // The panorama ceiling and the unknown-shape fallback scale with it.
    expect(style).toContain("--gthumb-max-w: 442px");
    expect(style).toContain("--gthumb-fallback-w: 245px");
  });

  it("sizes an unloaded placeholder from the same height", () => {
    const withShape = {
      ...group(2),
      candidates: [
        { picture_id: 1, width: 4000, height: 3000 },
        { picture_id: 2, width: 4000, height: 3000 },
      ],
    };
    const wrapper = mountRow({
      group: withShape,
      loadThumbnails: false,
      thumbHeight: 64,
    });
    // 4:3 at 64px tall.
    expect(wrapper.find(".gt--placeholder").attributes("style")).toContain(
      "width: 85px",
    );
  });

  // At the small end the info column, not the strip, sets the row height. One
  // pill is safe BECAUSE the evidence is ordered counter-first: the pill that
  // survives the limit is always the one arguing against stacking.
  it("keeps the counter-evidence pill when it drops to one", () => {
    const contested = {
      ...group(2),
      why: [
        { text: "same dimensions", against: false },
        { text: "different crop", against: true },
      ],
    };
    const small = mountRow({ group: contested, thumbHeight: 64 });
    const pills = small.findAll(".why-pill");
    expect(pills).toHaveLength(1);
    expect(pills[0].text()).toContain("different crop");

    const normal = mountRow({ group: contested, thumbHeight: 112 });
    expect(normal.findAll(".why-pill")).toHaveLength(2);
  });
});
