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
 * Whether a candidate may legally join its group's stack.
 *
 * False when a locked picture set freezes it: such a picture can be in neither
 * the stack (a locked set's membership cannot change) nor the metadata union
 * (its labels cannot change), so the server refuses it. The queue marks it and
 * leaves it out of the request rather than letting the user press Stack into a
 * guaranteed refusal.
 *
 * **Defaults to true.** A backend that predates the field serves no `stackable`,
 * and treating "absent" as "blocked" would empty every group on the queue.
 *
 * @param {Object} candidate
 * @returns {boolean}
 */
export function candidateStackable(candidate) {
  return candidate?.stackable !== false;
}

/**
 * The locked sets keeping a candidate out of the stack, as `[{id, name}]`.
 *
 * Empty for a stackable candidate, and for a backend that serves no
 * `blocked_by_sets`.
 *
 * @param {Object} candidate
 * @returns {Array<Object>}
 */
export function candidateBlockedBySets(candidate) {
  const sets = candidate?.blocked_by_sets;
  return Array.isArray(sets) ? sets : [];
}

/**
 * The ids of a group's candidates that a locked set keeps out of the stack.
 *
 * @param {Object} group
 * @returns {Array<number>}
 */
export function lockedCandidateIds(group) {
  return (group?.candidates ?? [])
    .filter((candidate) => !candidateStackable(candidate))
    .map((candidate) => candidateId(candidate))
    .filter((id) => id !== null);
}

// --- Units: what a stack verdict can actually move ---------------------------
//
// The queue used to render one tile per picture, but the backend folds whole
// STACKS (`_stack_members` in `dedup_verdict_service.py`), so a row offering to
// exclude one member of an existing stack, or to make one member the cover, was
// offering a gesture the server cannot honour.
//
// A **unit** is the smallest thing a verdict moves independently:
//
//   * a **loose picture**, `stack_id IS NULL`, its own unit;
//   * a **deck**, every candidate sharing one non-null `stack_id`, collapsed.
//
// A deck stands for the ENTIRE existing stack, not the members that happen to
// be in the group: `stacks[id].member_count` is the stack's live depth and is
// routinely larger than the number of matched candidates, so a group's true
// picture total can exceed `candidates.length`. Its face is the stack's leader,
// which is frequently NOT one of the matched members; that is the common case,
// and it is deliberately the picture shown, because a cover choice on a deck
// resolves to the leader.

/**
 * A candidate's stack id, normalised.
 *
 * @param {Object} candidate
 * @returns {number|string|null} null when the picture is not stacked.
 */
export function candidateStackId(candidate) {
  const raw = candidate?.stack_id ?? candidate?.stackId;
  if (raw === null || raw === undefined || raw === "") return null;
  return raw;
}

/**
 * @typedef {Object} DedupUnit
 * @property {"picture"|"deck"} kind
 * @property {string} key - stable `v-for` key.
 * @property {number|string|null} stackId
 * @property {number|string|null} coverPictureId - the picture a cover choice on
 *   this unit resolves to: a deck's stack leader, or the loose picture itself.
 * @property {number} depth - how many pictures the unit stands for.
 * @property {number} matchedCount - how many of them are in this group.
 * @property {Array<Object>} candidates - the group candidates it collapses.
 * @property {Array<number|string>} pictureIds - their ids.
 * @property {boolean} stackable - false when a locked set freezes ANY member.
 * @property {Array<Object>} blockedBySets
 * @property {string} thumbnailVersion - the face's thumbnail cache-buster.
 * @property {Object|null} face - the candidate the face is drawn from, when the
 *   group carries it. Null for a deck whose leader is not a group member, which
 *   is why the per-picture overlays are conditional on it.
 */

/**
 * Build a loose picture's unit.
 * @param {Object} candidate
 * @returns {DedupUnit}
 */
function looseUnit(candidate) {
  const id = candidateId(candidate);
  return {
    kind: "picture",
    key: `p:${id}`,
    stackId: null,
    coverPictureId: id,
    depth: 1,
    matchedCount: 1,
    candidates: [candidate],
    pictureIds: [id],
    stackable: candidateStackable(candidate),
    blockedBySets: candidateBlockedBySets(candidate),
    thumbnailVersion: String(candidate?.thumbnail_version ?? ""),
    face: candidate,
  };
}

/**
 * Partition a group's candidates into units, in candidate order.
 *
 * A stack's first candidate holds the deck's place in the strip; the rest of
 * that stack's candidates fold into it rather than taking a slot of their own.
 *
 * Degrades on a backend that serves no `stacks` block: candidates are still
 * collapsed by `stack_id`, and a stack the payload cannot size falls back to
 * the number of its members that are in the group. A unit that ends up standing
 * for one picture is a `picture`, not a one-deep deck.
 *
 * @param {Object} group - a queue group.
 * @returns {Array<DedupUnit>}
 */
export function groupUnits(group) {
  const candidates = group?.candidates ?? [];
  const stacks = group?.stacks ?? {};
  const units = [];
  const byStack = new Map();
  for (const candidate of candidates) {
    const stackId = candidateStackId(candidate);
    if (stackId === null) {
      units.push(looseUnit(candidate));
      continue;
    }
    const key = String(stackId);
    const existing = byStack.get(key);
    if (existing) {
      existing.candidates.push(candidate);
      existing.pictureIds.push(candidateId(candidate));
      continue;
    }
    const unit = {
      kind: "deck",
      key: `s:${key}`,
      stackId,
      coverPictureId: null,
      depth: 0,
      matchedCount: 0,
      candidates: [candidate],
      pictureIds: [candidateId(candidate)],
      stackable: true,
      blockedBySets: [],
      thumbnailVersion: "",
      face: null,
    };
    byStack.set(key, unit);
    units.push(unit);
  }
  for (const [key, unit] of byStack)
    finaliseDeck(unit, stacks[key], candidates);
  return units;
}

/**
 * Fill in a deck's depth, face and lock rollup from the group's `stacks` block.
 *
 * @param {DedupUnit} unit
 * @param {Object} [entry] - the group's `stacks[stack_id]` entry, when served.
 * @param {Array<Object>} candidates - the whole group, because a deck's leader
 *   may sit anywhere in it (or nowhere at all).
 */
function finaliseDeck(unit, entry, candidates) {
  unit.matchedCount = unit.pictureIds.length;
  const served = Number(entry?.member_count);
  unit.depth = Math.max(
    Number.isFinite(served) ? served : 0,
    unit.matchedCount,
  );
  const leaderId = entry?.leader_picture_id ?? unit.pictureIds[0] ?? null;
  unit.coverPictureId = leaderId;
  unit.face =
    candidates.find((candidate) => candidateId(candidate) === leaderId) ?? null;
  unit.thumbnailVersion = String(
    entry?.leader_thumbnail_version ?? unit.face?.thumbnail_version ?? "",
  );
  // The entry IS the server's unit-level rollup (it already accounts for a
  // locked sibling OUTSIDE the group). The per-candidate check is the belt:
  // a payload that predates the rollup still blocks a deck whose visible
  // member is frozen, rather than sending it into a guaranteed refusal.
  unit.stackable =
    entry?.stackable !== false && unit.candidates.every(candidateStackable);
  const sets = Array.isArray(entry?.blocked_by_sets)
    ? entry.blocked_by_sets
    : [];
  unit.blockedBySets = sets.length
    ? sets
    : unit.candidates.flatMap(candidateBlockedBySets);
  // A stack has two or more members by definition; anything that sizes to one
  // is a payload that could not describe a stack, and it renders as a picture.
  if (unit.depth < 2) {
    unit.kind = "picture";
    unit.depth = 1;
  }
}

/**
 * The unit one picture id belongs to.
 *
 * A deck answers to any of its matched members AND to its leader, because the
 * leader is what a cover choice on the deck resolves to and it is frequently
 * not a group member at all.
 *
 * @param {Array<DedupUnit>} units
 * @param {number|string} pictureId
 * @returns {DedupUnit|null}
 */
export function unitForPictureId(units, pictureId) {
  if (pictureId === null || pictureId === undefined) return null;
  for (const unit of units ?? []) {
    if (unit.pictureIds.includes(pictureId)) return unit;
    if (unit.coverPictureId === pictureId) return unit;
  }
  return null;
}

/**
 * Whether every picture a unit stands for is currently excluded.
 *
 * Exclusion is a whole-unit gesture, so a partially-excluded deck is a state
 * the row never produces; reading it as "still in" is the safe direction.
 *
 * @param {DedupUnit} unit
 * @param {Array<number|string>} excludedIds
 * @returns {boolean}
 */
export function isUnitExcluded(unit, excludedIds) {
  const ids = unit?.pictureIds ?? [];
  if (!ids.length) return false;
  return ids.every((id) => (excludedIds ?? []).includes(id));
}

/**
 * The units a Stack verdict would actually collect.
 * @param {Array<DedupUnit>} units
 * @param {Array<number|string>} excludedIds
 * @returns {Array<DedupUnit>}
 */
export function includedUnits(units, excludedIds) {
  return (units ?? []).filter(
    (unit) => unit.stackable && !isUnitExcluded(unit, excludedIds),
  );
}

/**
 * How a group is composed, for the row header: `Stack of 5 + 1 picture`.
 *
 * Decks lead, in strip order, then the loose pictures as one count, the shape
 * the spec's examples take, and the one that stays readable when a group holds
 * two stacks and three strays. A group with no deck keeps the plain `N
 * pictures` the header has always shown.
 *
 * @param {Array<DedupUnit>} units
 * @returns {string}
 */
export function unitCompositionLabel(units) {
  const list = units ?? [];
  const decks = list.filter((unit) => unit.kind === "deck");
  const loose = list.length - decks.length;
  if (!decks.length) return `${loose} ${loose === 1 ? "picture" : "pictures"}`;
  const parts = decks.map((deck) => `stack of ${deck.depth}`);
  if (loose) parts.push(`${loose} ${loose === 1 ? "picture" : "pictures"}`);
  const sentence = parts.join(" + ");
  return sentence.charAt(0).toUpperCase() + sentence.slice(1);
}

/**
 * What the Stack button is about to do, at three widths.
 *
 * The outcome, not the gesture: the three shapes match the backend's own
 * `SweepOutcome`, and the button is the last text a user working at speed reads
 * before committing, because expansion is opt-in and they may never open one.
 *
 *   | all loose   | `Stack 3`             |
 *   | deck + loose| `Add 1 to stack of 4` |
 *   | deck + deck | `Merge 2 stacks`      |
 *
 * `degrades` says whether the three differ at all, so a label that cannot
 * shorten is never given the classes that would hide it under width pressure.
 *
 * @param {Array<DedupUnit>} units - the INCLUDED units, exclusions applied.
 * @returns {{full: string, mid: string, short: string, degrades: boolean}}
 */
export function stackVerdictLabel(units) {
  const list = units ?? [];
  const decks = list.filter((unit) => unit.kind === "deck");
  const loose = list.length - decks.length;
  const plain = `Stack ${list.length}`;
  if (decks.length >= 2) {
    const merge = `Merge ${decks.length} stacks`;
    // Loose pictures fold in alongside the merge, and `Merge 2 stacks` would
    // move three things while naming two. Rare (11 of 1,726 unresolved groups
    // on a real library) but it is exactly the lie this labelling exists to
    // stop, so it is named in full and sheds only under width pressure, where
    // the header's composition still carries it.
    if (loose > 0) {
      return {
        full: `${merge} + ${loose} ${loose === 1 ? "picture" : "pictures"}`,
        mid: merge,
        short: "Merge",
        degrades: true,
      };
    }
    return same(merge);
  }
  if (decks.length === 1 && loose > 0) {
    return {
      full: `Add ${loose} to stack of ${decks[0].depth}`,
      mid: `Add ${loose} to stack`,
      short: `Add ${loose}`,
      degrades: true,
    };
  }
  return same(plain);
}

/**
 * A label with nothing to shed.
 * @param {string} text
 * @returns {{full: string, mid: string, short: string, degrades: boolean}}
 */
function same(text) {
  return { full: text, mid: text, short: text, degrades: false };
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
/**
 * A candidate's displayable smart score, or null.
 *
 * The backend serves `smart_score` as NULL while the score is not yet
 * computed and `-1.0` when computation failed; neither is a number a person
 * should read, so both come back as null and every display simply omits the
 * cell. A genuine 0 is displayable.
 *
 * @param {Object} candidate
 * @returns {number|null}
 */
export function candidateSmartScore(candidate) {
  const raw = candidate?.smart_score;
  // Checked BEFORE coercion: Number(null) is 0, which would turn
  // "not yet computed" into a confident 0.00 on screen.
  if (raw === null || raw === undefined) return null;
  const value = Number(raw);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

/**
 * A candidate's displayable sharpness, or null.
 *
 * The server serves `sharpness` (the cover ranking's third tier) already
 * nulled for missing/failed, so null simply means "nothing to show"; the
 * guard mirrors {@link candidateSmartScore}'s as a belt against older
 * payloads. A genuine 0 is displayable.
 *
 * @param {Object} candidate
 * @returns {number|null}
 */
export function candidateSharpness(candidate) {
  const raw = candidate?.sharpness;
  if (raw === null || raw === undefined) return null;
  const value = Number(raw);
  return Number.isFinite(value) && value >= 0 ? value : null;
}

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

// --- Verdict-refusal copy ---------------------------------------------------
//
// Pure string builders over a verdict response or an axios rejection. They live
// here rather than in DuplicateQueue.vue because they are the wire contract
// rendered as English, with no component state in them, and because a sentence
// the user reads on a refusal deserves a test that does not need a mounted view.

/**
 * The server's own explanation for a refusal, when it gave one.
 *
 * A dedup verdict is refused for reasons the user can act on ("a stack needs at
 * least two pictures", a locked set), and a generic "could not stack that
 * group" hides every one of them behind the same sentence. FastAPI puts the
 * reason in `detail`; anything else is not a message worth quoting.
 *
 * @param {*} err - the rejection the store recorded.
 * @returns {string} the server's sentence, or the empty string.
 */
export function serverDetail(err) {
  const detail = err?.response?.data?.detail;
  // A locked-set refusal is a structured detail, not a sentence: the backend
  // serves `{code, action, sets, picture_ids}` precisely so the client can build
  // its own copy without string-parsing. Reading only strings here is what made
  // the one refusal the user can actually act on arrive as the generic line.
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    if (detail.code !== "set_locked" && detail.code !== "pictures_locked") {
      return "";
    }
    const names = (detail.sets ?? [])
      .map((entry) => entry?.name)
      .filter(Boolean);
    if (!names.length) return "A locked set is freezing these pictures.";
    const joined = names.join(", ");
    return names.length === 1
      ? `They are in the locked set '${joined}', which cannot gain or change members.`
      : `They are in the locked sets '${joined}', which cannot gain or change members.`;
  }
  if (typeof detail !== "string") return "";
  const text = detail.trim();
  if (!text) return "";
  return /[.!?]$/.test(text) ? text : `${text}.`;
}

/**
 * The picture ids a refusal named, for the lock-chip flash.
 *
 * @param {*} err
 * @returns {Array<number>}
 */
export function lockedPictureIds(err) {
  const ids = err?.response?.data?.detail?.picture_ids;
  return Array.isArray(ids) ? ids : [];
}

/**
 * One sentence for a partial stack: what landed, and what a locked set held back.
 *
 * @param {Array<Object>} skipped - the response's `skipped` entries.
 * @param {number} stacked - how many pictures actually went into the stack.
 * @returns {string} empty when nothing was skipped.
 */
export function partialStackSentence(skipped, stacked) {
  if (!skipped?.length) return "";
  const names = [
    ...new Set(
      skipped.flatMap((entry) =>
        (entry?.sets ?? []).map((s) => s?.name).filter(Boolean),
      ),
    ),
  ];
  const held =
    skipped.length === 1
      ? "1 picture stayed out"
      : `${skipped.length} pictures stayed out`;
  const where = names.length
    ? ` (locked set${names.length > 1 ? "s" : ""} '${names.join(", ")}')`
    : "";
  return `Stacked ${stacked}; ${held}${where}.`;
}

// --- Mixed stacks (design D5) -----------------------------------------------
//
// A **mixed stack** is a live stack whose members do not form one connected
// cluster at the queue's similarity threshold. The helpers below turn one
// server row into the three things the list draws: its title, the sentence
// that says what is wrong with it, and the outcome its primary button names.
//
// They are pure and live here for the same reason the verdict copy does: the
// wire contract rendered as English deserves a test that does not mount a view.

/**
 * The strip height below which the deck badge runs its DENSE rule.
 *
 * 168px is the `small` rung of the shared thumbnail ladder, and the rule
 * inverts there rather than fading: an unflagged deck keeps its numeral and
 * drops the icon, a flagged one keeps the icon and drops the numeral. Below
 * that width a badge carrying both is wider than the tile it labels.
 */
export const DENSE_STACK_BADGE_BELOW_PX = 168;

/**
 * Whether one mixed-stack row is the STRONG case: a member joined to nothing
 * else in the stack at this threshold.
 *
 * Only the strong case is ever marked on a tile. At the measured 12% a mark is
 * one tile in eight and becomes a warning field, and the soft cases are often
 * legitimate (a burst where one frame panned off), so marking them trains the
 * user to dismiss the colour before the real one appears. The soft cases
 * surface in words instead, on this list and in the expansion.
 *
 * @param {Object} stack - one `MixedStackModel` row.
 * @returns {boolean}
 */
export function hasStrandedMember(stack) {
  return (stack?.stranded_picture_ids?.length ?? 0) > 0;
}

/**
 * The stack ids the queue's deck badges flag, from a page of mixed stacks.
 *
 * The list is ranked stranded-members-descending, so every strong case sits at
 * the head of it: one page is enough to answer this honestly, and a stack that
 * never appears is simply not flagged.
 *
 * @param {Array<Object>} stacks
 * @returns {Set<string>} stack ids as strings, because the row's `stackId`
 *   arrives from a different payload and the two must compare.
 */
export function flaggedStackIdSet(stacks) {
  const flagged = new Set();
  for (const stack of stacks ?? []) {
    if (hasStrandedMember(stack)) flagged.add(String(stack.stack_id));
  }
  return flagged;
}

/**
 * What one mixed-stack row is called: the stack, at its live size.
 *
 * The same noun phrase the queue's deck already uses, so a user meeting the
 * same stack on both surfaces meets the same words.
 *
 * @param {Object} stack
 * @returns {string}
 */
export function mixedStackTitle(stack) {
  const count = Number(stack?.member_count) || 0;
  return `Stack of ${count}`;
}

/**
 * Why this stack is listed, in one line.
 *
 * The strong case names the strangers, because that is what the primary button
 * is about to remove. The soft case says only that the members do not all
 * match, because there is no single member to blame and naming one would be a
 * claim the data does not support.
 *
 * @param {Object} stack
 * @returns {string}
 */
export function mixedStackReason(stack) {
  const stranded = stack?.stranded_picture_ids?.length ?? 0;
  if (stranded === 1) return "1 picture doesn't match the rest";
  if (stranded > 1) return `${stranded} pictures don't match the rest`;
  const components = Number(stack?.component_count) || 0;
  if (components > 2) {
    return `These don't all match: ${components} groups that don't overlap`;
  }
  return "These don't all match";
}

/**
 * The outcome the row's primary button names, and the ids it will send.
 *
 * `split` when a strict majority cluster survives removing the strangers,
 * `unstack` when there is no majority worth keeping. The server decides which
 * (`suggested_action`); this only renders it, and falls back to `unstack` when
 * a row carries no stranded member to split off, because "Split off 0" is a
 * button that cannot do anything.
 *
 * @param {Object} stack
 * @returns {{action: string, label: string, pictureIds: Array<number>}}
 */
export function mixedStackAction(stack) {
  const stranded = stack?.stranded_picture_ids ?? [];
  const suggested = String(stack?.suggested_action ?? "");
  if (suggested === "split" && stranded.length) {
    return {
      action: "split",
      label: `Split off ${stranded.length}`,
      pictureIds: [...stranded],
    };
  }
  return { action: "unstack", label: "Unstack", pictureIds: [] };
}

/**
 * The members the row shows as suspects, worst first.
 *
 * The stranded members lead: they are what the primary button acts on, and
 * they are the row's reason to exist. Below the strong case there is no single
 * stranger, so the row shows every member that is NOT in the majority cluster
 * instead: the pictures the stack would shed if it were split. The largest
 * component is deliberately excluded: it is the stack that survives, and
 * showing four of its members beside a warning border would accuse the
 * majority of being the problem.
 *
 * Capped, because the row is a list entry and not a second queue.
 *
 * @param {Object} stack
 * @param {number} [limit=6]
 * @returns {Array<number>} picture ids.
 */
export function mixedStackSuspects(stack, limit = 6) {
  const seen = new Set();
  const suspects = [];
  const push = (id) => {
    if (id === undefined || id === null) return;
    const key = String(id);
    if (seen.has(key)) return;
    seen.add(key);
    suspects.push(id);
  };
  for (const id of stack?.stranded_picture_ids ?? []) push(id);
  // Smallest first, largest dropped: the survivors are not suspects.
  const components = [...(stack?.components ?? [])]
    .sort((a, b) => (a?.length ?? 0) - (b?.length ?? 0))
    .slice(0, -1);
  for (const component of components) {
    for (const id of component ?? []) push(id);
  }
  return suspects.slice(0, Math.max(0, limit));
}
