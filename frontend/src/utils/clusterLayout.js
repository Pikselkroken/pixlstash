/**
 * Where each face of a face cluster sits: centre-out, most typical in the
 * middle, sized by how typical it is.
 *
 * Faces are banded by rank, not in equal counts: with equal bands a large
 * cluster has a dozen faces at the biggest size and "the most typical face
 * sits in the middle" stops reading.
 */

/** The share of a cluster, by rank, each size step takes: top 10%, the next
 * 30%, the next 35%, the rest. */
export const BAND_SHARES = [0.1, 0.3, 0.35, 0.25];

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

/**
 * Lay out *members* (most typical first) as squares around a centre.
 *
 * Greedy: each face walks an outward spiral from the centre and takes the
 * first spot clear of every face placed before it, so the most typical faces
 * sit nearest the middle.
 *
 * @param {Array<{picture_id: number}>} members - most typical first.
 * @param {number[]} steps - the size of each band in px, biggest first.
 * @param {number} [gap=4] - the space kept between two faces.
 * @returns {{ items: Array<{picture_id: number, x: number, y: number,
 *   size: number, band: number}>, width: number, height: number }}
 *   positions from the top left of a box `width` by `height`.
 */
export function layoutCluster(members, steps, gap = 4) {
  // ponytail: greedy spiral, O(n²) per cluster; fine at the 60 faces shown.
  const placed = [];
  const step = Math.max(2, Math.min(...steps) / 4);
  members.forEach((member, rank) => {
    const band = Math.min(bandOf(rank, members.length), steps.length - 1);
    const size = steps[band];
    for (let t = 0; ; t += 0.2) {
      // Archimedean spiral: the radius grows `step` px a radian.
      const r = step * t;
      const cx = r * Math.cos(t);
      const cy = r * Math.sin(t);
      const spot = { x: cx - size / 2, y: cy - size / 2, size };
      if (!placed.some((p) => overlaps(spot, p, gap))) {
        placed.push({ picture_id: member.picture_id, band, ...spot });
        break;
      }
    }
  });
  if (!placed.length) return { items: [], width: 0, height: 0 };
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
  };
}
