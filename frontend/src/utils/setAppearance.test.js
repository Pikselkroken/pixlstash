// Default appearance for a new picture set (#457): the sidebar used to offer
// the first *unused* palette entry, which meant mdi-camera + red every time a
// set was created in a fresh project. It now rotates on from the newest set.

import { describe, it, expect } from "vitest";
import { SET_ICONS, SET_COLORS, nextSetAppearance } from "./setAppearance";

const ICONS = SET_ICONS.map((ic) => ic.value);
const COLORS = SET_COLORS.map((c) => c.value);

describe("nextSetAppearance", () => {
  it("starts at the head of the palette for the first set", () => {
    expect(nextSetAppearance([])).toEqual({
      set_icon: ICONS[0],
      set_color: COLORS[0],
    });
  });

  it("continues after the newest set even in an empty project", () => {
    const sets = [{ id: 4, set_icon: ICONS[2], set_color: COLORS[2] }];
    // Sibling scope is empty (a new project), so the old "first unused" rule
    // handed back ICONS[0]/COLORS[0] here.
    expect(nextSetAppearance(sets, [])).toEqual({
      set_icon: ICONS[3],
      set_color: COLORS[3],
    });
  });

  it("reads the newest set by id, not list order", () => {
    const sets = [
      { id: 9, set_icon: ICONS[5], set_color: COLORS[5] },
      { id: 2, set_icon: ICONS[1], set_color: COLORS[1] },
    ];
    expect(nextSetAppearance(sets.slice().reverse())).toEqual({
      set_icon: ICONS[6],
      set_color: COLORS[6],
    });
  });

  it("skips what a sibling already uses", () => {
    const newest = [{ id: 1, set_icon: ICONS[0], set_color: COLORS[0] }];
    const siblings = [
      ...newest,
      { id: 2, set_icon: ICONS[1], set_color: COLORS[1] },
    ];
    expect(nextSetAppearance(newest, siblings)).toEqual({
      set_icon: ICONS[2],
      set_color: COLORS[2],
    });
  });

  it("ignores sets with no palette value (card stack, reference sets)", () => {
    const sets = [
      { id: 7, set_icon: "cards", set_color: null },
      { id: 3, set_icon: ICONS[4], set_color: COLORS[4] },
    ];
    expect(nextSetAppearance(sets)).toEqual({
      set_icon: ICONS[5],
      set_color: COLORS[5],
    });
  });

  it("wraps rather than running out when every entry is taken", () => {
    const all = ICONS.map((icon, i) => ({
      id: i + 1,
      set_icon: icon,
      set_color: COLORS[i % COLORS.length],
    }));
    const next = nextSetAppearance(all);
    expect(ICONS).toContain(next.set_icon);
    expect(COLORS).toContain(next.set_color);
  });
});
