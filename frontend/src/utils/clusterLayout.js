/**
 * Where each face of a face cluster sits: packed tight around the most typical
 * face, sized by how typical each is, inside the box it is shown in. Each tile
 * keeps its picture's aspect ratio, its long side the band's size.
 *
 * Faces are banded by rank, not in equal counts: with equal bands a large
 * cluster has a dozen faces at the biggest size and "the most typical face
 * sits in the middle" stops reading.
 */

/** The share of a cluster, by rank, each size step takes: top 10%, the next
 * 30%, the next 35%, the rest. */
export const BAND_SHARES = [0.1, 0.3, 0.35, 0.25];

/** The face sizes to try, biggest first, each a set of four bands: a cluster
 * that does not fit its box at one set takes the next. --face-thumb-xl,
 * --space-9, -8, -7, -6, -5. */
export const STEP_LADDER = [
  [96, 64, 48, 32],
  [64, 48, 32, 24],
  [48, 32, 24, 16],
];

/** The space between two faces to try at each set of sizes, widest first:
 * the spacing goes before the faces get smaller. --space-1, then none. */
export const GAPS = [2, 0];

/** The widest and tallest a tile gets, so a panorama is not a sliver; past
 * them the picture is cropped. */
export const ASPECT_LIMITS = [0.5, 2];

/** A tile's width and height: *aspect* (width / height) inside a *size*
 * square, its long side *size*. */
export function tileOf(size, aspect = 1) {
  const a = Math.min(ASPECT_LIMITS[1], Math.max(ASPECT_LIMITS[0], aspect || 1));
  return a >= 1 ? { w: size, h: size / a } : { w: size * a, h: size };
}

/** The band (0 = biggest) of the face at *rank* (0-based) of *count*. */
export function bandOf(rank, count) {
  if (rank === 0) return 0;
  let edge = 0;
  for (let band = 0; band < BAND_SHARES.length; band++) {
    edge += BAND_SHARES[band];
    if (rank / count < edge) return band;
  }
  return BAND_SHARES.length - 1;
}

function overlaps(a, b, gap) {
  return (
    a.x < b.x + b.w + gap &&
    b.x < a.x + a.w + gap &&
    a.y < b.y + b.h + gap &&
    b.y < a.y + a.h + gap
  );
}

/** The spots beside *p* a *w* by *h* tile could take: each side, at its
 * start, middle and end. */
function besides(p, w, h, gap) {
  const along = [p.x, p.x + (p.w - w) / 2, p.x + p.w - w];
  const down = [p.y, p.y + (p.h - h) / 2, p.y + p.h - h];
  const out = [];
  for (const x of along) {
    out.push({ x, y: p.y - h - gap }, { x, y: p.y + p.h + gap });
  }
  for (const y of down) {
    out.push({ x: p.x - w - gap, y }, { x: p.x + p.w + gap, y });
  }
  return out;
}

/** Pack at one set of steps; null when a face finds no room in the box. */
function pack(members, steps, gap, width, height) {
  // ponytail: every placed face offers 12 spots and each is checked against
  // every placed face, O(n³); a few ms at the 60 faces shown.
  const placed = [];
  const halfW = width / 2;
  const halfH = height / 2;
  for (let rank = 0; rank < members.length; rank++) {
    const band = Math.min(bandOf(rank, members.length), steps.length - 1);
    const size = steps[band];
    const { w, h } = tileOf(size, members[rank].aspect);
    const spots = placed.length
      ? placed.flatMap((p) => besides(p, w, h, gap))
      : [{ x: -w / 2, y: -h / 2 }];
    let best = null;
    let bestDist = Infinity;
    for (const spot of spots) {
      const s = { ...spot, size, w, h };
      if (s.x < -halfW || s.x + w > halfW || s.y < -halfH || s.y + h > halfH) {
        continue;
      }
      // The centre the cluster grows round is the top face's, at the origin.
      const cx = s.x + w / 2;
      const cy = s.y + h / 2;
      const dist = cx * cx + cy * cy;
      if (dist >= bestDist || placed.some((p) => overlaps(s, p, gap))) continue;
      best = s;
      bestDist = dist;
    }
    if (!best) return null;
    placed.push({ picture_id: members[rank].picture_id, band, ...best });
  }
  return placed;
}

/**
 * Lay out *members* (most typical first) as tiles packed round the first.
 *
 * Greedy: each face, in rank order, takes the spot beside a placed face that
 * is nearest the centre and clear of every other face, so the most typical
 * faces sit in the middle. The first set of steps in *ladder* that fits the
 * box wins, each tried at every gap in *gaps* before the next: the spacing is
 * cut before the faces shrink. When none fits, the smallest set packs with no
 * gap unbounded and `fits` is false; the caller scales it down.
 *
 * @param {Array<{picture_id: number, aspect?: number}>} members - most
 *   typical first; `aspect` is width / height, square when missing.
 * @param {number[][]} [ladder=STEP_LADDER] - sets of band sizes in px,
 *   biggest first.
 * @param {{width?: number, height?: number, gaps?: number[]}} [box] - the
 *   room available (unbounded when not given) and the gaps to try.
 * @returns {{ items: Array<{picture_id: number, x: number, y: number,
 *   w: number, h: number, size: number, band: number}>, width: number,
 *   height: number,
 *   fits: boolean }} positions from the top left of a box `width` by `height`.
 */
export function layoutCluster(
  members,
  ladder = STEP_LADDER,
  { width = Infinity, height = Infinity, gaps = GAPS } = {},
) {
  if (!members.length) return { items: [], width: 0, height: 0, fits: true };
  const w = width || Infinity;
  const h = height || Infinity;
  let placed = null;
  for (const steps of ladder) {
    for (const gap of gaps) {
      placed = pack(members, steps, gap, w, h);
      if (placed) break;
    }
    if (placed) break;
  }
  const fits = !!placed;
  if (!placed) placed = pack(members, ladder.at(-1), gaps.at(-1), Infinity, Infinity);
  const left = Math.min(...placed.map((p) => p.x));
  const top = Math.min(...placed.map((p) => p.y));
  // Edges rounded, not sizes, so two touching tiles still touch.
  const items = placed.map((p) => {
    const x = Math.round(p.x - left);
    const y = Math.round(p.y - top);
    return {
      ...p,
      x,
      y,
      w: Math.round(p.x - left + p.w) - x,
      h: Math.round(p.y - top + p.h) - y,
    };
  });
  return {
    items,
    width: Math.max(...items.map((p) => p.x + p.w)),
    height: Math.max(...items.map((p) => p.y + p.h)),
    fits,
  };
}
