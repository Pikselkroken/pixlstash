/**
 * Where each face of a face cluster sits: packed tight around the most typical
 * face, sized by how typical each is, inside the box it is shown in.
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
    a.x < b.x + b.size + gap &&
    b.x < a.x + a.size + gap &&
    a.y < b.y + b.size + gap &&
    b.y < a.y + a.size + gap
  );
}

/** The spots beside *p* a face of *size* could take: each side, at its start,
 * middle and end. */
function besides(p, size, gap) {
  const along = [p.x, p.x + (p.size - size) / 2, p.x + p.size - size];
  const down = [p.y, p.y + (p.size - size) / 2, p.y + p.size - size];
  const out = [];
  for (const x of along) {
    out.push({ x, y: p.y - size - gap }, { x, y: p.y + p.size + gap });
  }
  for (const y of down) {
    out.push({ x: p.x - size - gap, y }, { x: p.x + p.size + gap, y });
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
    const spots = placed.length
      ? placed.flatMap((p) => besides(p, size, gap))
      : [{ x: -size / 2, y: -size / 2 }];
    let best = null;
    let bestDist = Infinity;
    for (const spot of spots) {
      const s = { ...spot, size };
      if (s.x < -halfW || s.x + size > halfW || s.y < -halfH || s.y + size > halfH) {
        continue;
      }
      // The centre the cluster grows round is the top face's, at the origin.
      const cx = s.x + size / 2;
      const cy = s.y + size / 2;
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
 * Lay out *members* (most typical first) as squares packed round the first.
 *
 * Greedy: each face, in rank order, takes the spot beside a placed face that
 * is nearest the centre and clear of every other face, so the most typical
 * faces sit in the middle. The first set of steps in *ladder* that fits the
 * box wins, each tried at every gap in *gaps* before the next: the spacing is
 * cut before the faces shrink. When none fits, the smallest set packs with no
 * gap unbounded and `fits` is false; the caller scales it down.
 *
 * @param {Array<{picture_id: number}>} members - most typical first.
 * @param {number[][]} [ladder=STEP_LADDER] - sets of band sizes in px,
 *   biggest first.
 * @param {{width?: number, height?: number, gaps?: number[]}} [box] - the
 *   room available (unbounded when not given) and the gaps to try.
 * @returns {{ items: Array<{picture_id: number, x: number, y: number,
 *   size: number, band: number}>, width: number, height: number,
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
  const items = placed.map((p) => ({
    ...p,
    x: Math.round(p.x - left),
    y: Math.round(p.y - top),
  }));
  return {
    items,
    width: Math.max(...items.map((p) => p.x + p.size)),
    height: Math.max(...items.map((p) => p.y + p.size)),
    fits,
  };
}
