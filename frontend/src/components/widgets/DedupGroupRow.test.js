// One group in the triage queue.
//
// The tests pin the two things the row owes the keyboard and the screen reader,
// neither of which is visible in a screenshot: only the focused row is a tab
// stop, and the focused row says so in something other than CSS.

import { readFileSync } from "node:fs";

import { describe, it, expect, beforeEach } from "vitest";
import { mount } from "@vue/test-utils";
import { setActivePinia, createPinia } from "pinia";

import DedupGroupRow from "./DedupGroupRow.vue";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";
import { formatUserDate } from "../../utils/utils";

const globalOpts = { global: { stubs: { "v-icon": true } } };

// The row reads the user's date format from the prefs store.
beforeEach(() => {
  setActivePinia(createPinia());
});

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

describe("DedupGroupRow — the verdict key scheme (amendment #3)", () => {
  // One chip per button — the PRIMARY key shown (S, Stack's synonym, is
  // taught in copy, never as a second chip) — while aria-keyshortcuts
  // carries the full machine-readable set: the chips are aria-hidden, and
  // before this nothing announced the keys at all.
  it("chips show Enter and K; aria-keyshortcuts carries the full set", () => {
    const wrapper = mountRow({ focused: true });
    const stack = wrapper.find(".gbtn--stack");
    expect(stack.find("kbd").text()).toBe("Enter");
    expect(stack.attributes("aria-keyshortcuts")).toBe("Enter S");

    const keep = wrapper.findAll(".gbtn")[1];
    expect(keep.find("kbd").text()).toBe("K");
    expect(keep.attributes("aria-keyshortcuts")).toBe("K");
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

describe("DedupGroupRow — the decided row's timestamp and alignment", () => {
  const ISO = "2026-07-30T14:05:00"; // naive UTC, the house convention

  // The stamp follows the USER'S date-format setting, through the same
  // formatUserDate(iso, dateFormat) pattern every other timestamp uses —
  // proven by rendering differently under two settings, each matching the
  // shared util's output for that setting.
  it("stamps the decision time in the user's own date format", () => {
    const prefs = useUserPrefsStore();
    prefs.dateFormat = "eu";
    const eu = mountRow({ verdict: "stacked", decidedAt: ISO })
      .find(".gdecided-at")
      .text();
    expect(eu).toBe(formatUserDate(ISO, "eu"));

    prefs.dateFormat = "us";
    const us = mountRow({ verdict: "stacked", decidedAt: ISO })
      .find(".gdecided-at")
      .text();
    expect(us).toBe(formatUserDate(ISO, "us"));
    expect(us).not.toBe(eu);
  });

  it("carries the formatted stamp in the verdict tooltip too", () => {
    const prefs = useUserPrefsStore();
    prefs.dateFormat = "eu";
    const title = mountRow({ verdict: "keep_separate", decidedAt: ISO })
      .find(".gverdict")
      .attributes("title");
    expect(title).toContain(formatUserDate(ISO, "eu"));
  });

  // An older backend (or older rows) serve no decided_at: no cell, no dash.
  it("renders no timestamp cell when decided_at is absent", () => {
    const wrapper = mountRow({ verdict: "stacked" });
    expect(wrapper.find(".gdecided-at").exists()).toBe(false);
    expect(wrapper.find(".gverdict").attributes("title")).toBe(
      "This group was stacked.",
    );
  });

  // The alignment mechanism (owner report: label text must align with the
  // button's text, not outer borders): the label is a non-interactive span
  // wearing the button's box with an invisible border. jsdom computes no
  // layout, so the declarations are pinned at the source like the toolbar
  // band guardrail.
  it("the verdict label wears the Clear button's box with a transparent border", async () => {
    const wrapper = mountRow({ verdict: "stacked", decidedAt: ISO });
    expect(wrapper.find(".gverdict").element.tagName).toBe("SPAN");
    expect(wrapper.find(".gbtn").exists()).toBe(true);

    const { readFileSync } = await import("node:fs");
    const source = readFileSync(
      `${process.cwd()}/src/components/widgets/DedupGroupRow.vue`,
      "utf8",
    ).replace(/\/\*[\s\S]*?\*\//g, "");
    const blockOf = (selector) => {
      const start = source.indexOf(`${selector} {`);
      expect(start).toBeGreaterThan(-1);
      return source.slice(start, source.indexOf("}", start));
    };
    const label = blockOf(".gverdict");
    expect(label).toContain("border: 1px solid transparent");
    expect(label).toContain("padding: 0 var(--space-4)");
    expect(label).toContain("height: 27px");
    // The same inset the button's block declares (a `.gbtn, .gcompare`
    // selector list, so it is located by its first selector).
    const buttonStart = source.indexOf(".gbtn,");
    expect(buttonStart).toBeGreaterThan(-1);
    const button = source.slice(buttonStart, source.indexOf("}", buttonStart));
    expect(button).toContain("padding: 0 var(--space-4)");
    expect(button).toContain("height: 27px");
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

describe("DedupGroupRow — hover score overlays", () => {
  /** A two-copy group carrying stars and smart scores. */
  const scored = {
    ...group(2),
    candidates: [
      { picture_id: 1, score: 3, smart_score: 3.7156 },
      { picture_id: 2, score: 0, smart_score: null },
    ],
  };

  // Both overlays render inside the thumb (hover reveal is the grid's CSS
  // recipe, which jsdom does not compute — the structure and null handling
  // are the testable surface).
  it("renders the grid's star overlay and a smart score chip per thumbnail", () => {
    const wrapper = mountRow({ group: scored });
    const thumbs = wrapper.findAll(".gthumb");

    const stars = thumbs[0].findComponent({ name: "StarRatingOverlay" });
    expect(stars.exists()).toBe(true);
    expect(stars.props("score")).toBe(3);
    expect(stars.props("compact")).toBe(true);

    const chip = thumbs[0].find(".gsmart");
    expect(chip.text()).toContain("3.72");
    expect(chip.attributes("title")).toBe("Smart score 3.72");
    expect(chip.attributes("aria-hidden")).toBe("true");
  });

  // NULL means not-yet-computed and -1.0 means failed: no chip either way.
  it("renders no smart chip for a pending or failed score", () => {
    const failed = {
      ...scored,
      candidates: [
        { picture_id: 1, score: 0, smart_score: null },
        { picture_id: 2, score: 0, smart_score: -1.0 },
      ],
    };
    const wrapper = mountRow({ group: failed });
    expect(wrapper.findAll(".gsmart")).toHaveLength(0);
    // The star overlay still shows (score 0 renders its dim invitations,
    // exactly as the grid's does).
    expect(
      wrapper.findAllComponents({ name: "StarRatingOverlay" }),
    ).toHaveLength(2);
  });

  it("skips both overlays on an unloaded placeholder row", () => {
    const wrapper = mountRow({ group: scored, loadThumbnails: false });
    expect(wrapper.findAll(".gsmart")).toHaveLength(0);
    expect(
      wrapper.findAllComponents({ name: "StarRatingOverlay" }),
    ).toHaveLength(0);
  });

  // The overlays are display-only: the thumbnail keeps its whole gesture
  // vocabulary with them mounted.
  it("leaves click, right-click and double-click to the thumbnail", async () => {
    const wrapper = mountRow({ group: scored });
    const thumb = wrapper.findAll(".gthumb")[0];
    await thumb.trigger("click");
    expect(wrapper.emitted("set-cover")).toEqual([[1]]);
    await thumb.trigger("contextmenu");
    expect(wrapper.emitted("toggle-excluded")).toEqual([[1]]);
    await thumb.trigger("dblclick");
    expect(wrapper.emitted("compare")).toHaveLength(1);
  });

  it("keeps the excluded treatment with the overlays mounted", () => {
    const wrapper = mountRow({ group: scored, excludedIds: [2] });
    const thumbs = wrapper.findAll(".gthumb");
    expect(thumbs[1].classes()).toContain("gthumb--out");
    expect(
      thumbs[1].findComponent({ name: "StarRatingOverlay" }).exists(),
    ).toBe(true);
  });
});

describe("DedupGroupRow — the thumbnail's badge corners and its fade", () => {
  /** A pair where #1 is the user's exclusion and #2 is the server's lock. */
  const mixed = {
    ...group(2),
    candidates: [
      { picture_id: 1, score: 3, smart_score: 2.5 },
      {
        picture_id: 2,
        score: 0,
        stackable: false,
        blocked_by_sets: [{ id: 7, name: "Portfolio" }],
      },
    ],
  };

  /** Focused (so the index renders) with #1 excluded and #2 locked. */
  function mountMixed() {
    return mountRow({ group: mixed, focused: true, excludedIds: [1] });
  }

  // jsdom computes no layout, so the corner geometry is pinned at the source
  // like the verdict-label alignment above.
  const styleSource = () => {
    const source = readFileSync(
      `${process.cwd()}/src/components/widgets/DedupGroupRow.vue`,
      "utf8",
    ).replace(/\/\*[\s\S]*?\*\//g, "");
    return source.slice(source.indexOf("<style"));
  };
  const blockOf = (marker) => {
    const source = styleSource();
    const start = source.indexOf(marker);
    expect(start).toBeGreaterThan(-1);
    return source.slice(start, source.indexOf("}", start));
  };

  // The bug: both chips were absolutely positioned at the same top-left inset,
  // so a locked candidate in a focused row drew its index underneath the lock.
  it("stacks the index and the lock in one top-left column instead of one slot", () => {
    const locked = mountMixed().findAll(".gthumb")[1];
    const column = locked.find(".gtl");
    expect(column.exists()).toBe(true);

    // Both chips live in that column, in reading order, and neither positions
    // itself any more: the column owns the inset, so they cannot collide.
    expect(column.find(".gnum").exists()).toBe(true);
    expect(column.find(".glock").exists()).toBe(true);
    expect(column.element.children).toHaveLength(2);
    expect(column.element.children[0]).toBe(locked.find(".gnum").element);

    const layout = blockOf(".gtl,");
    expect(layout).toContain("flex-direction: column");
    expect(layout).toContain("gap: var(--space-1)");
    expect(blockOf(".gnum {")).not.toContain("top:");
    expect(blockOf(".glock {")).not.toContain("top:");
  });

  // The top-right corner is a column for the same reason before it needs to be:
  // the next badge added there must not restart the collision.
  it("gives the top-right corner the same column", () => {
    const column = mountMixed().find(".gtr");
    expect(column.exists()).toBe(true);
    expect(column.find(".gstars").exists()).toBe(true);
    // The reveal opacity stays on the member, not the column, so one column can
    // hold a hover-only badge and a permanent one at once.
    expect(blockOf(".gstars,")).toContain("opacity: 0");
    expect(blockOf(".gtl,")).not.toContain("opacity");
  });

  // The real fix: the fade used to sit on the BUTTON, so it dimmed the very
  // chips that explain why the picture is dimmed. Structurally, the chips are
  // siblings of the image and not its descendants, so an opacity on the image
  // can never reach them.
  it("fades only the picture, leaving the chips that explain it at full strength", () => {
    const thumbs = mountMixed().findAll(".gthumb");
    const excluded = thumbs[0];
    const locked = thumbs[1];

    expect(excluded.classes()).toContain("gthumb--out");
    expect(locked.classes()).toContain("gthumb--locked");

    // The explanatory marks are present and none of them is inside .gt.
    for (const [thumb, selectors] of [
      [excluded, [".gx", ".gnum", ".gstars", ".gsmart"]],
      [locked, [".glock", ".gnum", ".gstars"]],
    ]) {
      const image = thumb.find(".gt").element;
      for (const selector of selectors) {
        const chip = thumb.find(selector);
        expect(chip.exists()).toBe(true);
        expect(image.contains(chip.element)).toBe(false);
      }
    }

    // And the fade targets the image alone, at the disabled token.
    const source = styleSource();
    expect(source).not.toContain(".gthumb--out {");
    expect(blockOf(".gthumb--out .gt,")).toContain(
      "opacity: var(--opacity-disabled)",
    );
    expect(blockOf(".gthumb--locked {")).not.toContain("opacity");
    // Toggling the exclusion in place has to read as a change. Newline-anchored:
    // `.gthumb--locked .gt {` above ends in the same three characters.
    expect(blockOf("\n.gt {")).toContain(
      "transition: opacity var(--dur-1) var(--ease-standard)",
    );
  });

  // Design-token drift: raw opacities and a raw 0.15s ease in a file that has
  // tokens for both.
  it("carries no raw opacity or duration in the strip", () => {
    const source = styleSource();
    expect(source).not.toContain("opacity: 0.4");
    expect(source).not.toContain("opacity: 0.38");
    expect(source).not.toContain("0.15s ease");
    expect(blockOf(".gbtn:disabled")).toContain(
      "opacity: var(--opacity-disabled)",
    );
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
