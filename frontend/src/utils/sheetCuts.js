/**
 * The photo boxes a character sheet is cut into, as the step edits them on the
 * zoomed sheet. A box is `[left, top, right, bottom]`, fractions of the sheet
 * (0..1), in panel order: passport photos 1-4, then body photos 5 and 6.
 *
 * The server refuses a box under 32 px a side; a 5 % box is over that on every
 * size the step draws, so the edit never reaches it.
 */

/** The least a box's side may be, as a share of the sheet. */
export const MIN_SHARE = 0.05;
/** One arrow-key step, as a share of the sheet. */
export const NUDGE = 0.005;

/** The edges a handle moves: its own letters, `l` `t` `r` `b`. */
export const HANDLES = {
  nw: ["l", "t"],
  n: ["t"],
  ne: ["r", "t"],
  e: ["r"],
  se: ["r", "b"],
  s: ["b"],
  sw: ["l", "b"],
  w: ["l"],
};

const ARROWS = {
  ArrowLeft: { x: -1, y: 0, edge: "l" },
  ArrowRight: { x: 1, y: 0, edge: "r" },
  ArrowUp: { x: 0, y: -1, edge: "t" },
  ArrowDown: { x: 0, y: 1, edge: "b" },
};

function clamp(value, low, high) {
  return Math.min(Math.max(value, low), high);
}

/** The box moved by (dx, dy) shares, held on the sheet. */
export function moveBox([left, top, right, bottom], dx, dy) {
  const x = clamp(dx, -left, 1 - right);
  const y = clamp(dy, -top, 1 - bottom);
  return [left + x, top + y, right + x, bottom + y];
}

/** The box with each of *edges* moved by (dx, dy) shares, none past the sheet
 * or within MIN_SHARE of its opposite edge. */
export function resizeBox(box, edges, dx, dy) {
  const [left, top, right, bottom] = box;
  return [
    edges.includes("l") ? clamp(left + dx, 0, right - MIN_SHARE) : left,
    edges.includes("t") ? clamp(top + dy, 0, bottom - MIN_SHARE) : top,
    edges.includes("r") ? clamp(right + dx, left + MIN_SHARE, 1) : right,
    edges.includes("b") ? clamp(bottom + dy, top + MIN_SHARE, 1) : bottom,
  ];
}

/**
 * The box after an arrow key, or null for any other key. Plain arrows move
 * the box. Shift with an arrow pushes the edge on that side outward; Alt pulls
 * it inward, so Shift and Alt together are Shift.
 */
export function nudgeBox(box, key, { shift = false, alt = false } = {}) {
  const arrow = ARROWS[key];
  if (!arrow) return null;
  if (!shift && !alt) return moveBox(box, arrow.x * NUDGE, arrow.y * NUDGE);
  const sign = shift ? 1 : -1;
  return resizeBox(box, [arrow.edge], sign * arrow.x * NUDGE, sign * arrow.y * NUDGE);
}
