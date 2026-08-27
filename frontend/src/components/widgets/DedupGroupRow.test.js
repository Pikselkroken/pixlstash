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

describe("DedupGroupRow - the tab order", () => {
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

describe("DedupGroupRow - modified clicks select rows, not text", () => {
  // Shift-click means "extend the row selection". The browser reads the same
  // gesture as "extend the text selection", and it acts on mousedown, before
  // the click handler runs - so the row must refuse the default there.
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

describe("DedupGroupRow - what assistive tech is told", () => {
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

describe("DedupGroupRow - the verdict key scheme (amendment #3)", () => {
  // One chip per button - the PRIMARY key shown (S, Stack's synonym, is
  // taught in copy, never as a second chip) - while aria-keyshortcuts
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

describe("DedupGroupRow - what the verdicts cost", () => {
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

describe("DedupGroupRow - the decided row's timestamp and alignment", () => {
  const ISO = "2026-07-30T14:05:00"; // naive UTC, the house convention

  // The stamp follows the USER'S date-format setting, through the same
  // formatUserDate(iso, dateFormat) pattern every other timestamp uses -
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

describe("DedupGroupRow - double-click opens Compare", () => {
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

describe("DedupGroupRow - hover score overlays", () => {
  /** A two-copy group carrying stars and smart scores. */
  const scored = {
    ...group(2),
    candidates: [
      { picture_id: 1, score: 3, smart_score: 3.7156 },
      { picture_id: 2, score: 0, smart_score: null },
    ],
  };

  // Both overlays render inside the unit's box (hover reveal is the grid's CSS
  // recipe, which jsdom does not compute - the structure and null handling
  // are the testable surface). The stars sit in the top-right column, which is
  // a SIBLING of the tile button so it can also host the deck badge.
  it("renders the grid's star overlay and a smart score chip per thumbnail", () => {
    const wrapper = mountRow({ group: scored });
    const units = wrapper.findAll(".gunit");

    const stars = units[0].findComponent({ name: "StarRatingOverlay" });
    expect(stars.exists()).toBe(true);
    expect(stars.props("score")).toBe(3);
    expect(stars.props("compact")).toBe(true);

    const chip = units[0].find(".gsmart");
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
    const units = wrapper.findAll(".gunit");
    expect(units[1].find(".gthumb").classes()).toContain("gthumb--out");
    expect(units[1].findComponent({ name: "StarRatingOverlay" }).exists()).toBe(
      true,
    );
  });
});

describe("DedupGroupRow: the thumbnail's badge corners and its fade", () => {
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
  // like the verdict-label alignment above. It now lives in the SHARED strip:
  // the queue row and the Mixed stacks row draw the same tile, so the recipe
  // is one implementation and this guardrail follows it there. The DOM
  // assertions below are unchanged and still run against the mounted row,
  // which is what proves the extraction did not regress it.
  const styleSource = () => {
    const source = readFileSync(
      `${process.cwd()}/src/components/widgets/DedupPictureStrip.vue`,
      "utf8",
    ).replace(/\/\*[\s\S]*?\*\//g, "");
    return source.slice(source.indexOf("<style"));
  };
  const rowStyleSource = () => {
    const source = readFileSync(
      `${process.cwd()}/src/components/widgets/DedupGroupRow.vue`,
      "utf8",
    ).replace(/\/\*[\s\S]*?\*\//g, "");
    return source.slice(source.indexOf("<style"));
  };
  const blockIn = (source, marker) => {
    const start = source.indexOf(marker);
    expect(start).toBeGreaterThan(-1);
    return source.slice(start, source.indexOf("}", start));
  };
  const blockOf = (marker) => blockIn(styleSource(), marker);

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
    // The lock chip shares its construction with the Mixed row's stranger
    // chip, so the block is a selector list located by its first selector.
    expect(blockOf(".glock,")).not.toContain("top:");
  });

  // The top-right corner is a column for the same reason before it needs to be:
  // the next badge added there must not restart the collision.
  it("gives the top-right corner the same column", () => {
    const column = mountMixed().find(".gtr");
    expect(column.exists()).toBe(true);
    expect(column.find(".gstars").exists()).toBe(true);
    // The reveal opacity stays on the member, not the column, so one column can
    // hold a hover-only badge and a permanent one at once. The stars are
    // slotted content, rendered in the row's scope, so the strip reaches them
    // through `:slotted()` rather than by owning their markup.
    expect(blockOf(".gsmart,")).toContain("opacity: 0");
    expect(blockOf(".gsmart,")).toContain(":slotted(.gstars)");
    expect(blockOf(".gtl,")).not.toContain("opacity");
  });

  // The real fix: the fade used to sit on the BUTTON, so it dimmed the very
  // chips that explain why the picture is dimmed. Structurally, the chips are
  // siblings of the image and not its descendants, so an opacity on the image
  // can never reach them.
  it("fades only the picture, leaving the chips that explain it at full strength", () => {
    const units = mountMixed().findAll(".gunit");
    const excluded = units[0];
    const locked = units[1];

    expect(excluded.find(".gthumb").classes()).toContain("gthumb--out");
    expect(locked.find(".gthumb").classes()).toContain("gthumb--locked");

    // The explanatory marks are present and none of them is inside .gt.
    for (const [unit, selectors] of [
      [excluded, [".gx", ".gnum", ".gstars", ".gsmart"]],
      [locked, [".glock", ".gnum", ".gstars"]],
    ]) {
      const image = unit.find(".gt").element;
      for (const selector of selectors) {
        const chip = unit.find(selector);
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
    for (const source of [styleSource(), rowStyleSource()]) {
      expect(source).not.toContain("opacity: 0.4");
      expect(source).not.toContain("opacity: 0.38");
      expect(source).not.toContain("0.15s ease");
    }
    expect(blockIn(rowStyleSource(), ".gbtn:disabled")).toContain(
      "opacity: var(--opacity-disabled)",
    );
  });
});

describe("DedupGroupRow - the size control", () => {
  // One number drives the row. Sizing the box in CSS and the placeholder in JS
  // from two copies of the height is how a row starts jumping as it decodes.
  it("lays the strip out from the height it is given", () => {
    const style = mountRow({ thumbHeight: 184 })
      .find(".gstrip")
      .attributes("style");
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

// --- The deck: one tile per unit, not per picture ----------------------------
//
// A stack verdict moves whole stacks, so the strip draws a stack as ONE tile
// whose depth is the stack's real member count. The case that makes this
// load-bearing is the common one: a group naming a single member of a
// four-deep stack, where a tile sized from `candidates` shows one picture and
// then silently moves four.

/** A group naming one member of a 4-stack, plus two loose pictures. */
function stackedGroup(over = {}) {
  return {
    signature: "sg",
    tier: "near",
    confidence: 0.94,
    member_count: 3,
    cover_picture_id: 700,
    why: [],
    candidates: [
      { picture_id: 503, stack_id: 12, thumbnail_version: "x" },
      { picture_id: 700, thumbnail_version: "y" },
      { picture_id: 701, thumbnail_version: "z" },
    ],
    stacks: {
      12: {
        stack_id: 12,
        member_count: 4,
        leader_picture_id: 501,
        leader_thumbnail_version: "1024x768",
        matched_picture_ids: [503],
        stackable: true,
        blocked_by_sets: [],
      },
    },
    ...over,
  };
}

describe("DedupGroupRow: the deck", () => {
  it("draws one tile per unit, not one per candidate", () => {
    const wrapper = mountRow({ group: stackedGroup(), coverId: 501 });
    expect(wrapper.findAll(".gunit")).toHaveLength(3);
    expect(wrapper.findAll(".gthumb")).toHaveLength(3);
  });

  it("shows every original candidate individually on a decided row", async () => {
    const wrapper = mountRow({
      group: stackedGroup(),
      verdict: "stacked",
      collapseStacks: false,
      coverId: 503,
    });
    expect(wrapper.findAll(".gthumb")).toHaveLength(3);
    expect(wrapper.findComponent({ name: "StackBadge" }).exists()).toBe(false);
    expect(wrapper.attributes("aria-label")).toBe("Group 1, 3 pictures");
    await wrapper.find(".gthumb").trigger("click");
    await wrapper.find(".gthumb").trigger("contextmenu");
    expect(wrapper.emitted("focus")).toHaveLength(2);
    expect(wrapper.emitted("set-cover")).toBeUndefined();
    expect(wrapper.emitted("toggle-excluded")).toBeUndefined();
    expect(wrapper.find(".gthumb").attributes("title")).toContain(
      "compare this decided group",
    );
  });

  // The deck's face is the stack's LEADER, which this group never names as a
  // candidate: a tile showing one picture while meaning another is exactly the
  // mismatch the deck exists to remove.
  it("faces a deck with its stack's leader, at the leader's own version", () => {
    const wrapper = mountRow({ group: stackedGroup(), coverId: 501 });
    const src = wrapper.findAll(".gt")[0].attributes("src");
    expect(src).toContain("/thumbnails/501.");
    expect(src).toContain("v=1024x768");
    // Not the matched member's picture, and not its cache-buster either.
    expect(src).not.toContain("503");
  });

  // Reused from the grid, not reinvented: the ticks say "deck" before anything
  // is read, and the badge carries the count.
  it("wears the grid's edge ticks and count badge, sized from the live depth", () => {
    const units = mountRow({ group: stackedGroup(), coverId: 501 }).findAll(
      ".gunit",
    );
    const ticks = units[0].findComponent({ name: "StackEdgeTicks" });
    expect(ticks.exists()).toBe(true);
    expect(ticks.props("count")).toBe(4);

    const badge = units[0].findComponent({ name: "StackBadge" });
    expect(badge.exists()).toBe(true);
    expect(badge.props("count")).toBe(4);
    // A real stack, not a queue suggestion: the "?" treatment would say the
    // opposite of what this tile means.
    expect(badge.props("unresolved")).toBe(false);

    // A loose picture gets neither.
    expect(units[1].findComponent({ name: "StackEdgeTicks" }).exists()).toBe(
      false,
    );
    expect(units[1].findComponent({ name: "StackBadge" }).exists()).toBe(false);
  });

  // StackBadge is a <button> and .gthumb is a <button>: nesting them is invalid
  // markup that no browser resolves the way the markup reads. Same construction
  // as .dc-zoom in the compare dialog.
  it("hangs the badge column beside the tile, never inside it", () => {
    const unit = mountRow({ group: stackedGroup(), coverId: 501 }).findAll(
      ".gunit",
    )[0];
    const tile = unit.find(".gthumb").element;
    const badge = unit.findComponent({ name: "StackBadge" }).element;
    expect(tile.contains(badge)).toBe(false);
    expect(unit.find(".gtr").element.contains(badge)).toBe(true);
    // The permanent badge LEADS the column; a hover-only member follows.
    expect(unit.find(".gtr").element.children[0]).toBe(badge);
  });

  // The badge is a control, so it must obey the row's roving tab stop like
  // every other one: a screenful of decks must not add a screenful of tab stops.
  it("keeps the badge out of the tab order on an unfocused row", () => {
    const off = mountRow({ group: stackedGroup(), coverId: 501 });
    expect(
      off.findComponent({ name: "StackBadge" }).attributes("tabindex"),
    ).toBe("-1");
    const on = mountRow({
      group: stackedGroup(),
      coverId: 501,
      focused: true,
    });
    expect(
      on.findComponent({ name: "StackBadge" }).attributes("tabindex"),
    ).toBe("0");
  });

  // A deck whose leader is not in the group has no per-picture metadata to
  // show; labelling the leader's picture with a matched member's score would be
  // the same mismatch in a corner chip.
  it("shows no per-picture score on a deck it cannot source one for", () => {
    const unit = mountRow({ group: stackedGroup(), coverId: 501 }).findAll(
      ".gunit",
    )[0];
    expect(unit.find(".gsmart").exists()).toBe(false);
    expect(unit.findComponent({ name: "StarRatingOverlay" }).exists()).toBe(
      false,
    );
  });
});

describe("DedupGroupRow: cover and exclusion over units", () => {
  // A cover choice on a deck resolves to the stack's leader, because that is
  // the only picture the server can lead the resulting stack with.
  it("emits the leader when a deck is picked as cover", async () => {
    const wrapper = mountRow({ group: stackedGroup(), coverId: 501 });
    await wrapper.findAll(".gthumb")[0].trigger("click");
    expect(wrapper.emitted("set-cover")).toEqual([[501]]);
  });

  // The badge is the EXPANSION trigger (D4), not a second way to press the tile
  // it sits on: two controls doing one job means one of them is spare, and the
  // count is the natural handle for "show me what is in there".
  it("expands the deck from its badge instead of re-picking the cover", async () => {
    const wrapper = mountRow({ group: stackedGroup(), coverId: 501 });
    await wrapper.findComponent({ name: "StackBadge" }).vm.$emit("activate");
    expect(wrapper.emitted("set-cover")).toBeUndefined();
    expect(wrapper.emitted("toggle-expansion")).toEqual([[12]]);
    // It focuses the row first: the band may only live on the focused row.
    expect(wrapper.emitted("focus")).toHaveLength(1);
  });

  // Right-click addresses the UNIT; the store takes the whole deck out.
  it("emits the leader when a deck is right-clicked", async () => {
    const wrapper = mountRow({ group: stackedGroup(), coverId: 501 });
    await wrapper.findAll(".gthumb")[0].trigger("contextmenu");
    expect(wrapper.emitted("toggle-excluded")).toEqual([[501]]);
  });

  // The deck reads as the cover when the leader is, even though the leader is
  // not one of the group's own candidates.
  it("marks the deck as cover from its leader", () => {
    const wrapper = mountRow({ group: stackedGroup(), coverId: 501 });
    const tiles = wrapper.findAll(".gthumb");
    expect(tiles[0].classes()).toContain("gthumb--cover");
    expect(tiles[0].attributes("aria-pressed")).toBe("true");
    expect(tiles[1].classes()).not.toContain("gthumb--cover");
  });

  // Exclusion is whole-unit, so the deck's tile shows the excluded treatment
  // once its member is out: a half-dimmed deck would be a state the gesture
  // never produces.
  it("reads a deck as out once every picture it stands for is out", () => {
    const out = mountRow({
      group: stackedGroup(),
      coverId: 700,
      excludedIds: [503],
    });
    expect(out.findAll(".gthumb")[0].classes()).toContain("gthumb--out");
  });

  // Compare counts units: the dialog opens over the things a verdict moves.
  it("counts units in Compare all N", () => {
    expect(
      mountRow({ group: stackedGroup(), coverId: 501 })
        .find(".gcompare")
        .text(),
    ).toContain("Compare all 3");
  });

  // A locked set freezes a whole stack, including members outside the group,
  // so the deck's own rollup is what marks the tile.
  it("locks a whole deck from the served rollup", () => {
    const frozen = stackedGroup();
    frozen.stacks[12].stackable = false;
    frozen.stacks[12].blocked_by_sets = [{ id: 7, name: "Portfolio" }];
    const wrapper = mountRow({ group: frozen, coverId: 700, focused: true });
    const tile = wrapper.findAll(".gthumb")[0];
    expect(tile.classes()).toContain("gthumb--locked");
    expect(tile.find(".glock").exists()).toBe(true);
    expect(tile.attributes("aria-label")).toContain("Portfolio");
  });
});

describe("DedupGroupRow: the verdict button names its outcome", () => {
  const labelOf = (wrapper) =>
    wrapper
      .find(".gbtn--stack")
      .findAll("span")
      .map((s) => s.text());

  it("says Stack N when every unit is a loose picture", () => {
    const wrapper = mountRow();
    expect(labelOf(wrapper)).toEqual(["Stack 3"]);
    // One form, so it must never wear the classes that hide it under width
    // pressure: there would be nothing left in flow to replace it.
    expect(wrapper.find(".gsl").exists()).toBe(false);
  });

  it("says Add N to stack of M for a deck beside loose pictures", () => {
    const wrapper = mountRow({ group: stackedGroup(), coverId: 501 });
    expect(labelOf(wrapper)).toEqual([
      "Add 2 to stack of 4",
      "Add 2 to stack",
      "Add 2",
    ]);
    // The degrade ladder: all three forms in the DOM, one picked by a container
    // query, exactly as the toolbar's overflow folds.
    expect(wrapper.find(".gsl--full").exists()).toBe(true);
    expect(wrapper.find(".gsl--mid").exists()).toBe(true);
    expect(wrapper.find(".gsl--short").exists()).toBe(true);
  });

  it("says Merge N stacks for two decks", () => {
    const twoDecks = stackedGroup({
      candidates: [
        { picture_id: 503, stack_id: 12 },
        { picture_id: 601, stack_id: 13 },
      ],
      stacks: {
        12: { stack_id: 12, member_count: 5, leader_picture_id: 501 },
        13: { stack_id: 13, member_count: 3, leader_picture_id: 600 },
      },
    });
    expect(labelOf(mountRow({ group: twoDecks, coverId: 501 }))).toEqual([
      "Merge 2 stacks",
    ]);
  });

  // The label names what the button will actually do, so an exclusion changes
  // it: two loose pictures and a deck become "Add 1 to stack of 4".
  it("follows the exclusions", () => {
    const wrapper = mountRow({
      group: stackedGroup(),
      coverId: 501,
      excludedIds: [701],
    });
    expect(labelOf(wrapper)[0]).toBe("Add 1 to stack of 4");
  });

  // A bulk selection renames both verdicts, and that must outrank the outcome
  // label: a bulk action must never look like a single one.
  it("keeps the bulk rename ahead of the outcome label", () => {
    const wrapper = mountRow({
      group: stackedGroup(),
      coverId: 501,
      selected: true,
      selectionCount: 4,
    });
    expect(labelOf(wrapper)).toEqual(["Stack 4 groups"]);
  });
});

describe("DedupGroupRow: the composition and the accessible names", () => {
  it("states the composition in the header and the row's name", () => {
    const wrapper = mountRow({ group: stackedGroup(), coverId: 501, index: 2 });
    expect(wrapper.find(".gn").text()).toContain("Stack of 4 + 2 pictures");
    expect(wrapper.attributes("aria-label")).toBe(
      "Group 3, Stack of 4 + 2 pictures",
    );
  });

  // THE disclosure. The tile shows one picture standing for four, and the row
  // has no corner budget for a second numeral; this sentence is the only place
  // a screen-reader user learns the depth and the overlap, and in this pass
  // there is no visual substitute for it at all.
  it("names a deck by its true size and how much of it matched", () => {
    const labels = mountRow({ group: stackedGroup(), coverId: 501 })
      .findAll(".gthumb")
      .map((b) => b.attributes("aria-label"));
    expect(labels).toEqual([
      "Item 1 of 3, a stack of 4 pictures, 1 of them matched, cover",
      "Picture 2 of 3",
      "Picture 3 of 3",
    ]);
  });

  // When the group names the whole stack there is no overlap to disclose, and
  // a "4 of them matched" clause would be noise on every tile.
  it("drops the matched clause when the group names the whole stack", () => {
    const whole = stackedGroup({
      candidates: [
        { picture_id: 501, stack_id: 12 },
        { picture_id: 502, stack_id: 12 },
        { picture_id: 700 },
      ],
      stacks: {
        12: { stack_id: 12, member_count: 2, leader_picture_id: 501 },
      },
    });
    const label = mountRow({ group: whole, coverId: 501 })
      .find(".gthumb")
      .attributes("aria-label");
    expect(label).toBe("Item 1 of 2, a stack of 2 pictures, cover");
  });

  // The tooltip speaks about the unit the pointer is on, so a deck is never
  // described as "this picture".
  it("calls a deck a stack in its tooltip", () => {
    const wrapper = mountRow({
      group: stackedGroup(),
      coverId: 501,
      focused: true,
    });
    const tiles = wrapper.findAll(".gthumb");
    expect(tiles[0].attributes("title")).toContain("this stack's cover");
    expect(tiles[0].attributes("title")).toContain("leave this stack out");
    expect(tiles[1].attributes("title")).toContain("leave this picture out");
  });
});

// --- The expansion band (D4) -------------------------------------------------
//
// The row shows one picture of a stack and moves the whole thing. The band is
// where the rest of it can be looked at, and the two rules that make it safe
// are structural: it is a full-width row BELOW the columns (never inside the
// strip, which is already a horizontal scroller), and it is read-only (the
// strip can emit `unstack` and `set-cover`, and both would rewrite the library
// from inside a panel opened in order to look).

/** The two members `stacks[12]` names, in the shape the strip reads. */
const MEMBERS = [
  { id: 501, thumbnail_version: "a" },
  { id: 502, thumbnail_version: "b" },
  { id: 503, thumbnail_version: "c" },
  { id: 504, thumbnail_version: "d" },
];

/** A row with the band open on stack 12. */
function mountExpanded(props = {}) {
  return mountRow({
    group: stackedGroup(),
    coverId: 501,
    focused: true,
    expandedStackId: 12,
    expansionMembers: MEMBERS,
    ...props,
  });
}

describe("DedupGroupRow: the expansion band", () => {
  it("renders nothing until a stack is named", () => {
    const wrapper = mountRow({ group: stackedGroup(), coverId: 501 });
    expect(wrapper.find('[data-testid="dedup-row-expansion"]').exists()).toBe(
      false,
    );
  });

  // Inline in `.gstrip` would nest a second horizontal scroller on the same
  // axis, ambiguous on a trackpad and on touch, and would explode the deck
  // the row exists to present as one unit.
  it("sits below the row's columns, never inside the picture strip", () => {
    const wrapper = mountExpanded();
    const band = wrapper.find('[data-testid="dedup-row-expansion"]');
    expect(band.exists()).toBe(true);

    const strip = wrapper.find(".gstrip").element;
    expect(strip.contains(band.element)).toBe(false);
    expect(
      strip.compareDocumentPosition(band.element) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    // The whole stack, not only the member this group named.
    expect(band.findAll('[data-testid="stack-member"]')).toHaveLength(4);
  });

  // The verdict is the row's job and the band is a disclosure: opening one must
  // not take a control away or change what Enter would do.
  it("leaves every verdict control exactly as it was", () => {
    const collapsed = mountRow({
      group: stackedGroup(),
      coverId: 501,
      focused: true,
    });
    const labels = (wrapper) =>
      wrapper
        .findAll(".gact button")
        .map((b) => [b.text(), b.attributes("disabled")]);
    expect(labels(mountExpanded())).toEqual(labels(collapsed));
  });

  // Promotion re-covers that stack across the whole library. The band is a
  // place to LOOK, and it has no room for that sentence; Compare does.
  it("offers no control that could rewrite the library", () => {
    const band = mountExpanded().find('[data-testid="dedup-row-expansion"]');
    expect(band.find('[data-testid="stack-unstack"]').exists()).toBe(false);
    for (const member of band.findAll('[data-testid="stack-member"]')) {
      expect(member.attributes("disabled")).toBeDefined();
    }
    expect(
      band.findComponent({ name: "StackExpansionStrip" }).props("readOnly"),
    ).toBe(true);
  });

  // The queue runs a 112-406px size slider; a band at the strip's own 96px
  // default would contradict the tiles directly above it.
  it("draws its members at the row's own picture height", () => {
    const strip = mountExpanded({ thumbHeight: 240 }).findComponent({
      name: "StackExpansionStrip",
    });
    expect(strip.props("thumbHeight")).toBe(240);
  });

  // The cover the row is showing, not the strip's fallback to stack order: a
  // member promoted in Compare must still read as the cover here.
  it("flags the group's cover when it is one of the members", () => {
    expect(
      mountExpanded({ coverId: 503 })
        .findComponent({ name: "StackExpansionStrip" })
        .props("coverId"),
    ).toBe(503);
    // A cover outside the stack falls back to the stack's leader, so exactly
    // one member is flagged rather than none.
    expect(
      mountExpanded({ coverId: 700 })
        .findComponent({ name: "StackExpansionStrip" })
        .props("coverId"),
    ).toBe(501);
  });

  it("says it is reading while the members are in flight", () => {
    const band = mountExpanded({
      expansionMembers: [],
      expansionLoading: true,
    }).find('[data-testid="dedup-row-expansion"]');
    expect(band.attributes("role")).toBeUndefined();
    expect(band.find('[role="status"]').text()).toContain(
      "Reading the pictures in this stack",
    );
    expect(band.findComponent({ name: "StackExpansionStrip" }).exists()).toBe(
      false,
    );
  });

  // A failure to DISCLOSE must not read as a failure to decide, so the message
  // says the verdict buttons still work and the retry sits next to it.
  it("reports a failed read and offers the retry", async () => {
    const wrapper = mountExpanded({
      expansionMembers: [],
      expansionFailed: true,
    });
    const band = wrapper.find('[data-testid="dedup-row-expansion"]');
    expect(band.find('[role="alert"]').text()).toContain(
      "The verdict buttons still work",
    );
    await band.find(".gexp-state--error button").trigger("click");
    expect(wrapper.emitted("retry-expansion")).toHaveLength(1);
  });

  // The badge is the disclosure's trigger, so it has to say so: the CSS
  // rotation of a chevron is nothing at all to a screen reader.
  it("publishes the badge's disclosure state and names what it opens", () => {
    const closed = mountRow({
      group: stackedGroup(),
      coverId: 501,
      focused: true,
    }).findComponent({ name: "StackBadge" });
    expect(closed.attributes("aria-expanded")).toBe("false");
    expect(closed.attributes("title")).toBe(
      "Show the 4 pictures in this stack, below the row, or press E",
    );

    const open = mountExpanded().findComponent({ name: "StackBadge" });
    expect(open.attributes("aria-expanded")).toBe("true");
    expect(open.attributes("title")).toBe(
      "Hide the pictures in this stack, or press E",
    );
  });

  // Only the focused row answers to E, so only the focused row claims it.
  it("drops the key from the badge's name on an unfocused row", () => {
    const badge = mountRow({
      group: stackedGroup(),
      coverId: 501,
    }).findComponent({ name: "StackBadge" });
    expect(badge.attributes("title")).toBe(
      "Show the 4 pictures in this stack, below the row",
    );
  });

  // A click inside the band is a read, and the row's own click clears a
  // multi-selection: pressing Try again must not cost the user their gesture.
  it("keeps a click inside the band off the row", () => {
    const wrapper = mountExpanded();
    wrapper.find('[data-testid="dedup-row-expansion"]').trigger("click");
    expect(wrapper.emitted("focus")).toBeUndefined();
  });
});

// --- The mixed-stack warning chip (design D5) --------------------------------
//
// A deck whose stack does not hang together at the current threshold wears the
// mark in the badge's icon slot. Three properties are load-bearing and are all
// easy to break by accident: only the STRONG case is marked, the mark never
// gates a verdict, and below the ladder's `small` rung the dense rule inverts
// rather than dropping the mark.

describe("DedupGroupRow: the mixed-stack chip", () => {
  it("marks a deck whose stack the queue flagged", () => {
    const wrapper = mountRow({
      group: stackedGroup(),
      coverId: 501,
      flaggedStackIds: new Set(["12"]),
    });
    const badge = wrapper.find('[data-testid="stack-badge"]');
    expect(badge.attributes("data-flagged")).toBe("true");
  });

  // The soft cases are often legitimate (a burst where one frame panned off),
  // and they never reach this set: the store puts only stranded-member stacks
  // in it. A row given an empty set must therefore mark nothing.
  it("marks nothing when the stack is not in the flagged set", () => {
    const wrapper = mountRow({
      group: stackedGroup(),
      coverId: 501,
      flaggedStackIds: new Set(),
    });
    expect(
      wrapper.find('[data-testid="stack-badge"]').attributes("data-flagged"),
    ).toBeUndefined();
  });

  // Ids arrive as numbers on one payload and strings on the other, and a set
  // keyed the wrong way silently flags nothing at all.
  it("compares the stack id as a string", () => {
    const wrapper = mountRow({
      group: stackedGroup(),
      coverId: 501,
      flaggedStackIds: new Set([12]),
    });
    expect(
      wrapper.find('[data-testid="stack-badge"]').attributes("data-flagged"),
    ).toBeUndefined();
  });

  // THE rule the design is most explicit about: a mixed stack is one a user may
  // legitimately want to add to, and a warning that blocked would be the third
  // control this feature offered that it could not honour.
  it("never blocks or disables a verdict", () => {
    const wrapper = mountRow({
      group: stackedGroup(),
      coverId: 501,
      focused: true,
      flaggedStackIds: new Set(["12"]),
    });
    const stack = wrapper.find(".gbtn--stack");
    expect(stack.attributes("disabled")).toBeUndefined();
    const keep = wrapper.findAll(".gbtn")[1];
    expect(keep.attributes("disabled")).toBeUndefined();
    expect(wrapper.find(".gcompare").attributes("disabled")).toBeUndefined();
    // And the tile still answers: the cover gesture is untouched.
    expect(
      wrapper.findAll(".gthumb")[0].attributes("disabled"),
    ).toBeUndefined();
  });

  it("still emits a verdict from a row holding a flagged deck", async () => {
    const wrapper = mountRow({
      group: stackedGroup(),
      coverId: 501,
      focused: true,
      flaggedStackIds: new Set(["12"]),
    });
    await wrapper.find(".gbtn--stack").trigger("click");
    expect(wrapper.emitted("stack")).toHaveLength(1);
  });

  // 168px is the ladder's `small` rung. Below it the tile has room for one of
  // the two, and which one survives depends on which fact the badge is for.
  it("inverts the badge below the dense rung", () => {
    const dense = mountRow({
      group: stackedGroup(),
      coverId: 501,
      thumbHeight: 140,
      flaggedStackIds: new Set(["12"]),
    });
    expect(dense.find(".sbcount").exists()).toBe(false);
    expect(dense.find(".sbico").exists()).toBe(true);

    const denseUnflagged = mountRow({
      group: stackedGroup(),
      coverId: 501,
      thumbHeight: 140,
      flaggedStackIds: new Set(),
    });
    expect(denseUnflagged.find(".sbcount").text()).toBe("4");
    expect(denseUnflagged.find(".sbico").exists()).toBe(false);
  });

  it("draws both at and above the dense rung", () => {
    const wrapper = mountRow({
      group: stackedGroup(),
      coverId: 501,
      thumbHeight: 168,
      flaggedStackIds: new Set(["12"]),
    });
    expect(wrapper.find(".sbcount").text()).toBe("4");
    expect(wrapper.find(".sbico").exists()).toBe(true);
  });

  // The shortcut to the Mixed stacks page lives in the band the badge opens,
  // not in a second corner control: the corner is full, and a line in the
  // collapsed row would put a per-row variable into the queue's uniform scroll
  // pitch.
  it("offers the way to the Mixed stacks page from the open band", async () => {
    const wrapper = mountRow({
      group: stackedGroup(),
      coverId: 501,
      focused: true,
      flaggedStackIds: new Set(["12"]),
      expandedStackId: 12,
      expansionMembers: [{ id: 501 }, { id: 503 }],
    });
    const link = wrapper.find(".gexp-flag button");
    expect(link.exists()).toBe(true);
    await link.trigger("click");
    expect(wrapper.emitted("show-mixed")).toEqual([[12]]);
  });

  it("says nothing about a mixed stack when the open deck is not flagged", () => {
    const wrapper = mountRow({
      group: stackedGroup(),
      coverId: 501,
      focused: true,
      flaggedStackIds: new Set(),
      expandedStackId: 12,
      expansionMembers: [{ id: 501 }],
    });
    expect(wrapper.find(".gexp-flag").exists()).toBe(false);
  });
});
