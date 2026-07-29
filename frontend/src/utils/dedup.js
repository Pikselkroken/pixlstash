// Pure helpers for the duplicate triage queue. No Vue, no Pinia, no network.
//
// The cover formula lives here rather than in a component because three
// surfaces need the same answer: the queue row's preselected cover, the compare
// view's "best value" highlight per column, and the auto-stack dialog's
// explanation of which copy it keeps. Three implementations would drift.
//
// The field names here are the backend's (`routes/dedup.py`): a candidate is
// keyed on `picture_id`, carries `is_raw`, `size_bytes`, `created_at`,
// `reference_folder_id` and `file_path`, and already ships its own
// `cover_score`. The local formula is kept as the fallback and as the thing
// the tests pin, so a server that stops sending `cover_score` degrades to a
// correct preselection rather than to none.

/**
 * Megapixels of a candidate, from whichever shape the record carries.
 * @param {Object} candidate
 * @returns {number} megapixels, or 0 when the dimensions are unknown.
 */
export function candidateMegapixels(candidate) {
  if (!candidate) return 0;
  const direct = Number(candidate.megapixels);
  if (Number.isFinite(direct) && direct > 0) return direct;
  const width = Number(candidate.width);
  const height = Number(candidate.height);
  if (!Number.isFinite(width) || !Number.isFinite(height)) return 0;
  if (width <= 0 || height <= 0) return 0;
  return (width * height) / 1e6;
}

/**
 * Whether a candidate is a RAW capture, which earns the cover-score bonus.
 *
 * RAW wins the tie against a same-resolution JPEG because it is the copy that
 * still holds the latitude to be re-edited; losing it to a re-export is the
 * expensive mistake this bonus exists to prevent.
 *
 * @param {Object} candidate
 * @returns {boolean}
 */
export function isRawCandidate(candidate) {
  // The server decides this: it knows the decoder that opened the file, where
  // the client only has an extension. The format list below is the fallback.
  if (typeof candidate?.is_raw === "boolean") return candidate.is_raw;
  const format = String(candidate?.format ?? "").toUpperCase();
  return format === "RAW" || RAW_FORMATS.has(format);
}

const RAW_FORMATS = new Set([
  "ARW",
  "CR2",
  "CR3",
  "DNG",
  "NEF",
  "ORF",
  "RAF",
  "RW2",
]);

/** The RAW bonus, in cover-score points. */
export const RAW_COVER_BONUS = 8;

/**
 * The design's cover score: `pixels x 4 + tags x 3 + userScore x 2 + RAW bonus`.
 *
 * Higher wins. The weights are the design's, not a heuristic to tune here.
 *
 * @param {Object} candidate
 * @returns {number}
 */
export function coverScore(candidate) {
  if (!candidate) return 0;
  const served = Number(candidate.cover_score);
  if (Number.isFinite(served)) return served;
  const megapixels = candidateMegapixels(candidate);
  const tags = Number(candidate.tag_count) || 0;
  const score = Number(candidate.score) || 0;
  return (
    megapixels * 4 +
    tags * 3 +
    score * 2 +
    (isRawCandidate(candidate) ? RAW_COVER_BONUS : 0)
  );
}

/**
 * Capture time as a sortable number, for the tie-break.
 * @param {Object} candidate
 * @returns {number} epoch ms, or `Infinity` when unknown so an undated
 *   candidate never wins a tie against a dated one.
 */
function captureTime(candidate) {
  const raw = candidate?.created_at ?? candidate?.captured_at;
  if (!raw) return Number.POSITIVE_INFINITY;
  const parsed = Date.parse(raw);
  return Number.isNaN(parsed) ? Number.POSITIVE_INFINITY : parsed;
}

/**
 * Index of the candidate the cover formula preselects.
 *
 * Ties break to the oldest capture time, so the original beats the copy that
 * was made from it.
 *
 * @param {Array<Object>} candidates
 * @returns {number} the winning index, or -1 for an empty list.
 */
export function pickCoverIndex(candidates) {
  if (!Array.isArray(candidates) || !candidates.length) return -1;
  let best = 0;
  let bestScore = coverScore(candidates[0]);
  let bestTime = captureTime(candidates[0]);
  for (let i = 1; i < candidates.length; i += 1) {
    const score = coverScore(candidates[i]);
    const time = captureTime(candidates[i]);
    if (score > bestScore || (score === bestScore && time < bestTime)) {
      best = i;
      bestScore = score;
      bestTime = time;
    }
  }
  return best;
}

/**
 * The picture id the cover formula preselects, honouring a server preselection.
 *
 * The backend runs the same formula, so its answer wins when present; the local
 * computation is the fallback that keeps the queue usable if the field is ever
 * absent, and is what the tests pin the formula against.
 *
 * @param {Object} group - a queue group.
 * @returns {number|string|null}
 */
export function suggestedCoverId(group) {
  if (
    group?.cover_picture_id !== undefined &&
    group.cover_picture_id !== null
  ) {
    return group.cover_picture_id;
  }
  const candidates = group?.candidates ?? [];
  const index = pickCoverIndex(candidates);
  return index < 0 ? null : (candidateId(candidates[index]) ?? null);
}

/**
 * A candidate's picture id.
 *
 * One accessor rather than `candidate.picture_id` scattered through the views,
 * so the day a candidate carries something else the change is here.
 *
 * @param {Object} candidate
 * @returns {number|null}
 */
export function candidateId(candidate) {
  const id = candidate?.picture_id ?? candidate?.id;
  return id === undefined ? null : id;
}

/**
 * The largest value of one numeric field across a group's candidates.
 *
 * Drives the compare view's per-column "best value" highlight.
 *
 * @param {Array<Object>} candidates
 * @param {function(Object): number} read - reads the field off a candidate.
 * @returns {number} the maximum, or 0 when nothing is comparable.
 */
export function bestOf(candidates, read) {
  if (!Array.isArray(candidates) || !candidates.length) return 0;
  let best = 0;
  for (const candidate of candidates) {
    const value = Number(read(candidate));
    if (Number.isFinite(value) && value > best) best = value;
  }
  return best;
}

/**
 * Order a group's evidence so the counter-evidence is read first.
 *
 * A group carrying red pills is exactly the one that needs Compare, so the
 * pills do the warning instead of a generic "review carefully" line. Putting
 * them first means the two pills a collapsed row has room for are the two that
 * matter.
 *
 * @param {Array<Object>} why - `[{ text, against }]` evidence entries, the
 *   backend's `WhyPillModel` shape.
 * @returns {Array<Object>} counter-evidence first, original order within each
 *   half.
 */
export function orderEvidence(why) {
  if (!Array.isArray(why)) return [];
  return [...why.filter((w) => w?.against), ...why.filter((w) => !w?.against)];
}

/**
 * A pill's rendered label.
 *
 * The backend calls it `text`; accepting `label` as well keeps a fixture or a
 * hand-built pill working rather than rendering an empty chip.
 *
 * @param {Object} pill
 * @returns {string}
 */
export function evidenceLabel(pill) {
  return String(pill?.text ?? pill?.label ?? "");
}

/**
 * Shorten a file path to its last two segments, head first.
 *
 * Truncation happens in JS rather than with `direction: rtl` because the RTL
 * trick reorders punctuation inside the filename on some paths, which is worse
 * than the overflow it fixes.
 *
 * @param {string} path
 * @returns {string}
 */
export function shortenPath(path) {
  if (!path) return "";
  const segments = String(path).split("/").filter(Boolean);
  if (segments.length <= 2) return path;
  return `…/${segments.slice(-2).join("/")}`;
}

/**
 * Whether a candidate's path should be shown.
 *
 * File location is shown only for pictures in reference folders, where the user
 * manages the files themselves and needs to know which copy is which. For a
 * managed library picture the path is an implementation detail.
 *
 * @param {Object} candidate
 * @returns {boolean}
 */
export function showsPath(candidate) {
  // The server already applies this rule: `file_path` is populated only for a
  // reference-folder picture and is null for a managed one. The id check is
  // belt and braces, so a future server that always sent the path would not
  // silently start leaking library layout into the UI.
  const path = candidatePath(candidate);
  return Boolean(path) && candidate?.reference_folder_id != null;
}

/**
 * A candidate's file path, or an empty string when it has none to show.
 * @param {Object} candidate
 * @returns {string}
 */
export function candidatePath(candidate) {
  return String(candidate?.file_path ?? candidate?.path ?? "");
}

/**
 * A candidate's file size in megabytes, from the stored byte count.
 * @param {Object} candidate
 * @returns {number} 0 when the size is unknown.
 */
export function candidateSizeMb(candidate) {
  const bytes = Number(candidate?.size_bytes);
  if (Number.isFinite(bytes) && bytes > 0) return bytes / 1e6;
  const mb = Number(candidate?.file_size_mb);
  return Number.isFinite(mb) && mb > 0 ? mb : 0;
}

/**
 * Human label for a group's confidence.
 *
 * The exact tier is not a percentage and must not be rendered as "100% similar":
 * "Exact" is a different kind of claim, and blurring the two is what makes a
 * near-duplicate suggestion look more certain than it is.
 *
 * @param {Object} group
 * @returns {{ exact: boolean, label: string }}
 */
export function confidenceLabel(group) {
  const tier = group?.tier ?? group?.kind;
  if (tier === "exact") return { exact: true, label: "Exact" };
  const confidence = Number(group?.confidence);
  if (!Number.isFinite(confidence)) return { exact: false, label: "Similar" };
  return { exact: false, label: `${Math.round(confidence * 100)}% similar` };
}
