<script setup>
/**
 * Field-by-field comparison of one duplicate group.
 *
 * Usage:
 *   <DedupCompareDialog
 *     ref="compareRef"
 *     :open="compareOpen"
 *     :group="group"
 *     :cover-id="coverId"
 *     :excluded-ids="excludedIds"
 *     :busy="verdictInFlight"
 *     @close="compareOpen = false"
 *     @set-cover="onSetCover"
 *     @toggle-excluded="onToggleExcluded"
 *     @stack="onStack"
 *     @keep-separate="onKeepSeparate"
 *   />
 *
 * The whole decision is made here: the verdict buttons live in this dialog's
 * footer, so opening Compare is not a detour the user has to back out of before
 * they can act. The component is presentational for the queue state - it reads
 * props and emits intent - but it OWNS the zoom (the design system's
 * full-screen blink compare): which candidate is up, fit vs actual pixels, the
 * pan. The queue's keyboard model drives that state through the exposed API so
 * there is exactly one key owner.
 *
 * Layout, per the owner's calls (2026-07-29): the dialog takes the full grid
 * space (AppDialog `fullscreen`), the images dominate - down-scaled ORIGINALS,
 * not thumbnails, because the whole point of Compare is fine detail - and the
 * metadata sits under each picture as the design system's compact two-column
 * label-over-value grid instead of a tall row list.
 *
 * ONE CARD PER UNIT, not per candidate (`docs/design/mixed-stacks-and-stack-units.md`
 * D2/D4). The queue row already renders units (a whole existing stack is one
 * deck) and a verdict moves units, so a strip drawn per candidate compares
 * things no verdict can move independently. A deck's card shows its LEADER's
 * numbers, labelled as such and never an aggregate: the metric columns answer
 * "which file is better", a mean megapixel count answers nothing, and an
 * aggregate would silently break the per-column best-value highlight, which is
 * a comparison between individual FILES. What the deck stands for is stated
 * once, in words, by the group-level `Contains` row.
 *
 * TWO MODES, ONE COMPONENT (`mode`). Compare serves the Mixed stacks page as
 * well, and the two surfaces differ in what a card MEANS, not in how the
 * comparison is drawn: one strip, one zoom, one footer, one set of styles.
 *
 *   * `group` (the default) is the review queue's: a card is a unit, its
 *     primary click chooses the cover, right-click leaves it out, and the
 *     footer gives the stack / keep-separate verdict.
 *   * `mixed` is one live stack: a card is one MEMBER, so the deck logic, the
 *     lazy member fetch, the `Contains` row and the expansion band all fall
 *     away on their own, because a mixed card's unit is a single picture. Its
 *     primary click MARKS that member as a stranger (click and right-click are
 *     one gesture, exactly as the row's are, because a mixed stack has no cover
 *     to choose), and the footer's primary splits the marked members off or
 *     unstacks the lot. The per-column best-value chip is suppressed: it
 *     answers "which is the better file" and this page asks "which of these
 *     does not belong".
 *
 * A second dialog was deliberately NOT built. The zoom alone is ~200 lines of
 * cursor-anchored scale arithmetic, and a copy of it would drift the moment
 * either surface was touched.
 */
import { computed, nextTick, ref, watch } from "vue";
import {
  anchorZoomScroll,
  atFitFloor,
  formatZoomPercent,
  normalizeWheelDelta,
  zoomStepScale,
  ZOOM_EXIT_GESTURE_GAP_MS,
  ZOOM_EXIT_RESISTANCE,
  ZOOM_MAX_SCALE,
} from "../../utils/zoomMath";
import AppDialog from "./AppDialog.vue";
import AppButton from "./AppButton.vue";
import DedupConfidencePill from "./DedupConfidencePill.vue";
import DedupWhyPills from "./DedupWhyPills.vue";
import StackExpansionStrip from "./StackExpansionStrip.vue";
import { pictureThumbnailUrl } from "../../api/pictures";
import { listStackMembers, MAX_STACK_MEMBER_PAGE } from "../../api/dedup";
import { API_BASE_URL } from "../../utils/apiClient";
import {
  bestOf,
  candidateId,
  candidatePath,
  candidateSizeMb,
  candidateMegapixels,
  candidateSharpness,
  candidateSmartScore,
  groupUnits,
  includedUnits,
  isUnitExcluded,
  mixedStackMembers,
  mixedStackPrimaryTitle,
  mixedStackReason,
  shortenPath,
  showsPath,
  stackVerdictLabel,
  edgePercentText,
  unitForPictureId,
} from "../../utils/dedup";
import { formatUserDate } from "../../utils/utils";

const props = defineProps({
  open: { type: Boolean, default: false },
  /**
   * Which surface opened this: `group` (the duplicate review queue) or `mixed`
   * (the Mixed stacks page). It decides what a card MEANS and what its primary
   * click does; everything else about the dialog is shared.
   */
  mode: {
    type: String,
    default: "group",
    validator: (value) => ["group", "mixed"].includes(value),
  },
  /**
   * The group under comparison:
   * `{ id, kind, confidence, why: [{label, against}], candidates: [...] }`.
   */
  group: { type: Object, default: null },
  /** False on Decided: compare the original candidates, not their current deck. */
  collapseStacks: { type: Boolean, default: true },
  /**
   * `mode: "mixed"` only: the one `MixedStackModel` row under comparison. Its
   * `member_ids` are the cards, in canonical stack order.
   */
  mixedStack: { type: Object, default: null },
  /**
   * `mode: "mixed"` only: the picture ids marked as strangers, the server's
   * opening marks as the user has since adjusted them. The page owns this list
   * and the footer's primary acts on it.
   */
  markedIds: { type: Array, default: () => [] },
  /**
   * `mode: "mixed"` only: the footer primary's label, already predicted by the
   * page (`Split off 2` / `Unstack all 5`). It is the page's prediction rather
   * than a second computation here, because the row and the dialog naming the
   * same press two different things is the one bug this cannot afford.
   */
  primaryLabel: { type: String, default: "" },
  /** `mode: "mixed"` only: that button's icon, without the `mdi-` prefix. */
  primaryIcon: { type: String, default: "" },
  /** The cover currently in force, as chosen by the parent. */
  coverId: { type: [Number, String], default: null },
  /** Candidates the user has left out of the stack. */
  excludedIds: { type: Array, default: () => [] },
  /** True while a verdict is in flight, which locks the verdict buttons. */
  busy: { type: Boolean, default: false },
  /**
   * True in a share or read-only session. Reading the comparison is still
   * useful there, so the dialog stays open to it; the verdict footer goes,
   * because a control that can never work is worse than an absent one.
   */
  readOnly: { type: Boolean, default: false },
});

const emit = defineEmits([
  "close",
  // `mode: "group"`.
  "set-cover",
  "toggle-excluded",
  "stack",
  "keep-separate",
  // `mode: "mixed"`. `toggle-mark` carries the member's picture id; the other
  // two are the footer's two verdicts and carry nothing, because the page owns
  // the marks the request is built from.
  "toggle-mark",
  "resolve",
  "keep",
]);

// The en dash placeholder for a value the picture does not carry. A blank cell
// reads as "still loading"; the dash says "there is nothing here".
const EMPTY = "–";

/** Formats the browser can decode in an <img>; anything else (RAW, video)
 * falls back to the thumbnail, which the server already rendered. */
const BROWSER_IMAGE_FORMATS = new Set([
  "jpg",
  "jpeg",
  "png",
  "webp",
  "gif",
  "bmp",
  "avif",
]);

/** Whether a card is one member of a live stack rather than a group unit. */
const isMixed = computed(() => props.mode === "mixed");

/** The dialog names what it is comparing, because the two are not the same
 * object: a proposal in one mode, an existing stack in the other. */
const dialogTitle = computed(() =>
  isMixed.value ? "Compare stack" : "Compare group",
);

/**
 * The claim in the header, beside the close button: the group's confidence, or
 * the mixed row's own reason for being listed. Both are the one-line answer to
 * "why am I looking at this", so they share the slot.
 */
const headerNote = computed(() => mixedStackReason(props.mixedStack));

/**
 * One mixed stack's members, in canonical stack order. Empty in `group` mode,
 * so nothing on that path ever reads `mixedStack`.
 */
const mixedMembers = computed(() =>
  isMixed.value ? mixedStackMembers(props.mixedStack) : [],
);

/** The marks in force, keyed as strings because the ids arrive from a payload
 * the page has already round-tripped. */
const markedSet = computed(
  () => new Set((props.markedIds ?? []).map((id) => String(id))),
);

/**
 * One member as a single-picture UNIT.
 *
 * The strip, the meta grid and the zoom all speak units, and a mixed card
 * genuinely IS one: a stack member is the smallest thing the split acts on.
 * Modelling it as a unit rather than adding a second card shape is what makes
 * every deck-shaped branch below (the `Contains` row, the expansion band, the
 * lazy leader fetch) fall away on its own instead of being switched off by
 * hand.
 *
 * @param {Object} member - one `mixedStackMembers` entry.
 * @returns {Object} a `DedupUnit`, plus the member model the Match column and
 *   the stranger treatment read.
 */
function memberUnit(member) {
  return {
    kind: "picture",
    key: member.key,
    stackId: props.mixedStack?.stack_id ?? null,
    coverPictureId: member.pictureId,
    depth: 1,
    matchedCount: 1,
    candidates: [],
    pictureIds: [member.pictureId],
    stackable: true,
    blockedBySets: [],
    thumbnailVersion: "",
    face: null,
    member,
  };
}

/**
 * The units under comparison: a loose picture is its own, and every candidate
 * sharing one `stack_id` collapses into a single deck standing for the ENTIRE
 * existing stack. The partition is `utils/dedup.js`'s, the same one the queue
 * row, the store and the keyboard model read, so the strip, the digits, the
 * floor and the request can never disagree about what a card is.
 *
 * In `mixed` mode there is nothing to collapse: every card is one member. The
 * Decided caller also opts out of collapsing because it is reviewing the
 * original candidates, not preparing another unit-level verdict.
 */
const units = computed(() =>
  isMixed.value
    ? mixedMembers.value.map(memberUnit)
    : groupUnits(props.group, { collapseStacks: props.collapseStacks }),
);

/**
 * Whether this card's member is marked as a stranger.
 *
 * @param {Object} unit
 * @returns {boolean} always false outside `mixed` mode.
 */
function isMarkedUnit(unit) {
  if (!isMixed.value) return false;
  return markedSet.value.has(String(unit?.coverPictureId));
}

/**
 * Whether this card's picture will NOT be in the resulting stack, whichever
 * gesture put it there: the user's exclusion in `group` mode, their stranger
 * mark in `mixed`. The In-stack checkbox reads this; the card's border does
 * not, because the two states look deliberately different (a dashed excluded
 * card, a warning-bordered stranger).
 *
 * @param {Object} unit
 * @returns {boolean}
 */
function leavesStack(unit) {
  return isMixed.value ? isMarkedUnit(unit) : isUnitOut(unit);
}

/** Whether any card on screen stands for more than one picture. */
const anyDeck = computed(() => units.value.some((u) => u.kind === "deck"));

/**
 * What each card renders: its unit, and the picture its numbers are read from:
 * the loose picture itself, or a deck's stack LEADER, which is the picture the
 * card shows and the one a cover choice on that deck resolves to.
 */
const cards = computed(() =>
  units.value.map((unit) => ({ unit, face: faceOf(unit) })),
);

/**
 * The compared FILES, for the per-column maxima.
 *
 * Nulls are dropped: a deck whose leader is neither a group candidate nor
 * fetched yet has no numbers to compare, and contributing a zero would make
 * every other card look like the winner of every column.
 */
const faces = computed(() => cards.value.map((c) => c.face).filter(Boolean));

/** The per-column maxima that drive the best-value highlight. */
const bestMegapixels = computed(() => bestOf(faces.value, candidateMegapixels));
const bestFileSize = computed(() => bestOf(faces.value, candidateSizeMb));
const bestScore = computed(() => bestOf(faces.value, (c) => c.score));
const bestTagCount = computed(() => bestOf(faces.value, (c) => c.tag_count));
const bestSmartScore = computed(() => bestOf(faces.value, candidateSmartScore));

/**
 * Whether ANY copy carries a displayable smart score - the same group-level
 * decision the Location row makes, and for the same reason: the meta grid's
 * height comes off the image, so the row appears on every card or on none,
 * keeping the pictures registered against each other. Against a backend that
 * does not serve `smart_score` yet, no card shows the row.
 */
const anySmartScore = computed(() =>
  faces.value.some((c) => candidateSmartScore(c) !== null),
);

/**
 * A candidate's smart score for display: the metadata panel's own precision
 * (`toFixed(2)`), the dash for a copy that has none while its siblings do.
 * @param {Object} candidate
 * @returns {string}
 */
function smartScoreText(candidate) {
  const value = candidateSmartScore(candidate);
  return value === null ? EMPTY : value.toFixed(2);
}

const bestSharpness = computed(() => bestOf(faces.value, candidateSharpness));

/** Group-level, exactly like the Smart score column above. */
const anySharpness = computed(() =>
  faces.value.some((c) => candidateSharpness(c) !== null),
);

/**
 * A candidate's sharpness for display. Three decimals, not two: the server
 * serialises the metric at 3dp on a typical 0–0.5 range, where two decimals
 * would flatten genuinely different copies into the same number.
 * @param {Object} candidate
 * @returns {string}
 */
function sharpnessText(candidate) {
  const value = candidateSharpness(candidate);
  return value === null ? EMPTY : value.toFixed(3);
}

/**
 * Whether ANY copy in this group carries a path worth showing.
 *
 * The Location row is a whole extra line in the meta grid, and the meta grid is
 * what the image area gives its leftover height to. With the row rendered per
 * candidate, one reference-folder copy next to a library-managed one produced
 * two cards whose pictures started and ended at different heights - the
 * comparison's whole premise is that the images are registered against each
 * other, so the row is a group-level decision: every card gets it, or none do.
 */
const anyPathShown = computed(() => faces.value.some(showsPath));

/** The units a Stack verdict would actually collect, exclusions applied. */
const includedUnitList = computed(() =>
  includedUnits(units.value, props.excludedIds),
);

/**
 * What the Stack button is about to do, in the queue row's own words
 * (`Stack 3` / `Add 1 to stack of 4` / `Merge 2 stacks`). A picture count here
 * would contradict the row the user pressed C on, and would under-state a
 * verdict that folds a whole existing stack in.
 */
const verdictLabel = computed(() => stackVerdictLabel(includedUnitList.value));

/**
 * The evidence under the strip, from whichever payload owns it.
 *
 * Same component, same `[{text, against}]` shape, different TONE: a duplicate
 * group is a proposal and its pills argue for and against it, while a mixed
 * stack already exists and its pills describe what was measured about it. A row
 * of red chips over a stack the user made would read as an accusation.
 */
const whyPills = computed(() =>
  isMixed.value ? (props.mixedStack?.why ?? []) : (props.group?.why ?? []),
);

/**
 * Which outcome the footer's primary names, read off the icon the page handed
 * us rather than recomputed.
 *
 * The label and the icon move together (`mixedStackPrimary` decides both at
 * once), so the icon IS the outcome; recomputing it here would give the tooltip
 * its own opinion, and a tooltip that can contradict the label beside it is
 * worse than no tooltip.
 */
const primaryAction = computed(() =>
  props.primaryIcon === "call-split" ? "split" : "unstack",
);

/** The primary's tooltip, in the same words the queue row's carries. */
const primaryTitle = computed(() =>
  mixedStackPrimaryTitle({
    action: primaryAction.value,
    pictureIds: props.markedIds ?? [],
  }),
);

/**
 * Whether a unit is the group's cover.
 *
 * A deck answers to its leader as well as to its matched members, because the
 * leader is what a cover choice on it resolves to and it is frequently not a
 * group candidate at all.
 *
 * @param {Object} unit
 * @returns {boolean}
 */
function isUnitCover(unit) {
  // A mixed stack already exists and already has a leader, so there is no cover
  // to choose here: the card's press marks a stranger instead.
  if (isMixed.value) return false;
  if (props.coverId == null) return false;
  if (unitForPictureId([unit], props.coverId) === unit) return true;
  // A member promoted from the expansion is a picture of this stack that the
  // group never named, so the id-based match above cannot see it. Without this
  // the card that IS the cover would stop saying so the moment the user
  // promoted one of its own members.
  if (unit.kind !== "deck") return false;
  const members = membersEntry(unit.stackId)?.members ?? [];
  return members.some((member) => candidateId(member) === props.coverId);
}

/**
 * Whether every picture this unit stands for is out of the stack. Exclusion is
 * a whole-unit gesture, so a deck is in or out, never half in.
 *
 * @param {Object} unit
 * @returns {boolean}
 */
function isUnitOut(unit) {
  // Mixed mode has no exclusions. A member is in the stack or marked as a
  // stranger, and a stranger is NOT drawn as an excluded card: it is the row's
  // evidence, and the dashed, quieted treatment would say "inert" about the one
  // card that is not.
  if (isMixed.value) return false;
  return isUnitExcluded(unit, props.excludedIds);
}

/**
 * The picture id an exclusion gesture on this unit travels as.
 *
 * A matched member rather than the leader: the store resolves whichever id it
 * is handed back to the whole unit, but only a matched member is guaranteed to
 * be in the group the request names.
 *
 * @param {Object} unit
 * @returns {number|string|null}
 */
function unitToggleId(unit) {
  return unit.pictureIds[0] ?? unit.coverPictureId;
}

/**
 * The preview source: the down-scaled ORIGINAL for browser-decodable formats
 * (Compare exists to judge fine detail, which a grid-scale thumbnail cannot
 * carry), the server-rendered thumbnail for RAW and video, and for a picture
 * whose record the payload does not carry at all (a deck's leader before its
 * fetch lands), the thumbnail the group already versions for us.
 *
 * @param {number|string} pictureId
 * @param {Object|null} record - the candidate/member row, when we hold it.
 * @param {string} [version] - the thumbnail cache-buster to fall back on.
 * @returns {string}
 */
function previewUrl(pictureId, record, version = "") {
  const format = String(record?.format || "").toLowerCase();
  if (BROWSER_IMAGE_FORMATS.has(format)) {
    return `${API_BASE_URL}/pictures/${pictureId}.${format}`;
  }
  return pictureThumbnailUrl(pictureId, {
    version: record?.thumbnail_version ?? version,
  });
}

/**
 * One unit's picture, as the card draws it.
 * @param {Object} unit
 * @returns {string}
 */
function unitPreviewUrl(unit) {
  return previewUrl(unit.coverPictureId, faceOf(unit), unit.thumbnailVersion);
}

// ── An existing stack's members ────────────────────────────────────────────
// Neither payload carries the picture rows this dialog compares, so both modes
// read them, and they read them differently because they need different amounts.
//
//   * `group`: the queue payload sizes a deck and names its leader, but carries
//     the leader's ROW only when the leader happens to be one of the group's
//     candidates, which it frequently is not (`stacks[id].leader_picture_id` is
//     routinely outside `matched_picture_ids`). So the leader's row is fetched
//     on open when it is missing: ONE member, per deck. The rest of the stack
//     stays lazy behind the expansion, because inlining a 40-member stack is
//     what the queue's never-render-whole rule forbids.
//   * `mixed`: every member is a card, so the whole stack is read in ONE
//     request on open. That rule is about the queue's ROWS, not about a dialog
//     the user opened deliberately, and the row already told us how many
//     members there are. Without it every metric cell is an en dash and, worse,
//     `previewUrl` has no `format` and serves a THUMBNAIL into a zoom whose
//     whole job is to show actual pixels.
//
// Neither read is load-bearing. A failure leaves the ids, the evidence, the
// gestures and both verdicts working, because all of those come off the row.

/** `stackId -> { members, loading, failed, complete }`. */
const stackMembers = ref({});

/**
 * What the dialog is showing right now, as one comparable token.
 *
 * The dialog flips its subject IN PLACE, in both modes: a verdict auto-advances
 * the queue to the next group, and the mixed cursor moves to the next row. So a
 * response that outlived its subject must be dropped rather than written over
 * the rows of whatever is on screen when it lands.
 *
 * The mixed half is keyed on the MEMBERSHIP as well as the stack, and the
 * fingerprint is the load-bearing half of that pair even though the id is the
 * obvious choice. A stack re-served at a different threshold has the same
 * membership, so the fingerprint is unchanged and nothing is re-read: the same
 * cost the id alone would have had. The two differ only when membership
 * actually changes, which is exactly when a re-read is wanted, and the case
 * that forces it is a member being ADDED: that card has no cached row, so
 * without the fingerprint it would show the en dash for the rest of the
 * dialog's life with nothing left to trigger a second read. `member_ids` is
 * what `useMixedStackQueue` already compares for its own held-row check.
 *
 * The stack id stays in the key so a server that publishes no fingerprint
 * degrades to id-only behaviour rather than to one undefined key for every row.
 */
const subjectKey = computed(() =>
  isMixed.value
    ? `stack:${props.mixedStack?.stack_id ?? ""}:${props.mixedStack?.membership_fingerprint ?? ""}`
    : `group:${props.group?.signature ?? ""}`,
);

/**
 * One stack's fetch state.
 * @param {number|string} stackId
 * @returns {Object|null}
 */
function membersEntry(stackId) {
  if (stackId === null || stackId === undefined) return null;
  return stackMembers.value[String(stackId)] ?? null;
}

/**
 * Fetch a stack's members, once.
 *
 * @param {number|string} stackId
 * @param {Object} [options]
 * @param {boolean} [options.full=false] - the whole stack (an expansion) rather
 *   than just the leader (a card's numbers).
 * @param {boolean} [options.force=false] - retry after a failure.
 */
async function fetchStackMembers(
  stackId,
  { full = false, force = false } = {},
) {
  const key = String(stackId);
  const held = stackMembers.value[key];
  if (held?.loading) return;
  if (!force) {
    if (held?.complete) return;
    if (!full && held?.members?.length) return;
  }
  const subject = subjectKey.value;
  stackMembers.value = {
    ...stackMembers.value,
    [key]: {
      members: held?.members ?? [],
      loading: true,
      failed: false,
      complete: false,
    },
  };
  try {
    const data = await listStackMembers(stackId, {
      limit: full ? MAX_STACK_MEMBER_PAGE : 1,
    });
    // The dialog flips its subject in place (a verdict auto-advances the group,
    // the mixed cursor moves to the next stack), so a response that outlived
    // that subject must not be written over the new one's rows.
    if (subjectKey.value !== subject) return;
    const members = Array.isArray(data?.members) ? data.members : [];
    stackMembers.value = {
      ...stackMembers.value,
      [key]: {
        members,
        loading: false,
        failed: false,
        // Only a page that reached the end may be summed into a footprint.
        complete: full && (data?.next_offset ?? null) === null,
      },
    };
  } catch (err) {
    console.warn(
      `[dedup] failed to read the members of stack ${stackId} for the compare dialog (subject ${subject}, full=${full})`,
      err,
    );
    if (subjectKey.value !== subject) return;
    stackMembers.value = {
      ...stackMembers.value,
      [key]: {
        members: held?.members ?? [],
        loading: false,
        failed: true,
        complete: false,
      },
    };
  }
}

/** The mixed stack's one member read, once it has been attempted. */
const mixedFacesEntry = computed(() =>
  isMixed.value ? membersEntry(props.mixedStack?.stack_id) : null,
);

/**
 * Whether that read failed.
 *
 * Nothing is blocked behind it. The ids, the Match column, the evidence pills,
 * the marking gestures and both verdicts are all built from the row payload the
 * page already holds, so what is missing is the per-picture DETAIL: the metric
 * cells and the full-size previews. The line says that and offers the retry;
 * it does not disable anything, because a surface that still works must not be
 * presented as broken.
 */
const mixedFacesFailed = computed(() => Boolean(mixedFacesEntry.value?.failed));

/** Read the mixed stack's members again after a failure. */
function retryMixedFaces() {
  const stackId = props.mixedStack?.stack_id;
  if (stackId === null || stackId === undefined) return;
  fetchStackMembers(stackId, { full: true, force: true });
}

/**
 * The record a unit's numbers are read from.
 *
 * A loose picture is its own; a deck's is its stack LEADER, taken from the
 * group's candidates when the leader is one of them and from the fetched member
 * page otherwise; a mixed card's is that member's own row out of the one page
 * this stack was read as. Null while none is available, which every metric cell
 * renders as the en dash rather than as a confident zero, and which
 * `previewUrl` renders as the thumbnail until the real format is known.
 *
 * @param {Object} unit
 * @returns {Object|null}
 */
function faceOf(unit) {
  if (!unit) return null;
  if (isMixed.value) {
    const members = membersEntry(unit.stackId)?.members ?? [];
    // No `?? members[0]` fallback, unlike the deck below: there the first
    // member IS a usable stand-in for the leader, here every card is a
    // different picture and the wrong row would put one member's numbers under
    // another member's photo.
    return members.find((m) => candidateId(m) === unit.coverPictureId) ?? null;
  }
  if (unit.kind !== "deck") return unit.candidates[0] ?? null;
  if (unit.face) return unit.face;
  const members = membersEntry(unit.stackId)?.members ?? [];
  return (
    members.find((m) => candidateId(m) === unit.coverPictureId) ??
    members[0] ??
    null
  );
}

/**
 * A deck's footprint on disk, or null while it is not knowable.
 *
 * Summed from the members, so it appears only once the whole member list has
 * been read (the expansion). The payload carries no total, and summing the one
 * page we hold would state a stack's size from a fraction of it.
 *
 * @param {Object} unit
 * @returns {number|null} megabytes.
 */
function deckSizeMb(unit) {
  const entry = membersEntry(unit?.stackId);
  if (!entry?.complete || !entry.members.length) return null;
  const total = entry.members.reduce(
    (sum, member) => sum + candidateSizeMb(member),
    0,
  );
  return total > 0 ? total : null;
}

/**
 * What a card actually stands for: `5 pictures · 42 MB`, or `1 picture`.
 *
 * The row exists because the File column shows the LEADER's size, which on a
 * deck would otherwise be read as the whole stack's footprint.
 *
 * @param {Object} unit
 * @returns {string}
 */
function containsText(unit) {
  if (unit.kind !== "deck") return "1 picture";
  const base = `${unit.depth} pictures`;
  const size = deckSizeMb(unit);
  return size === null ? base : `${base} · ${size.toFixed(1)} MB`;
}

/**
 * What the In-stack row is called.
 *
 * `In the stack` in mixed mode, because the stack is a thing that already
 * exists and the question is whether this picture belongs to it; `In stack` in
 * group mode, where the stack is the one about to be made.
 */
const inStackLabel = computed(() =>
  isMixed.value ? "In the stack" : "In stack",
);

/**
 * The In-stack readout, per unit: a deck goes in whole or not at all.
 * @param {Object} unit
 * @returns {string}
 */
function inStackText(unit) {
  // `Stranger`, not `No`: the mark says what this picture IS relative to its
  // siblings, which is the fact the primary button acts on.
  if (isMixed.value) return isMarkedUnit(unit) ? "Stranger" : "Yes";
  if (isUnitOut(unit)) return unit.kind === "deck" ? "None" : "No";
  return unit.kind === "deck" ? `All ${unit.depth}` : "Yes";
}

/**
 * What the In-stack checkbox is about to do, said in full.
 * @param {Object} unit
 * @returns {string}
 */
function inStackToggleTitle(unit) {
  if (isMixed.value) {
    return isMarkedUnit(unit)
      ? "Put this picture back in the stack"
      : "Mark this picture as a stranger, so it comes out of the stack";
  }
  if (isUnitOut(unit)) {
    return unit.kind === "deck"
      ? "Put this whole stack back in"
      : "Put this copy back in the stack";
  }
  return unit.kind === "deck"
    ? "Leave this whole stack out"
    : "Leave this copy out of the stack";
}

/**
 * One member's closest match to any sibling, as a percentage.
 *
 * Bound to `nearestEdge`, which is measured whatever the threshold says, so a
 * stranded member shows the 89% it really is instead of a dash that read as
 * "matches nothing". The en dash survives for the one case where there is
 * genuinely no measurement: nothing comparable to measure against.
 *
 * @param {Object} unit
 * @returns {string}
 */
function matchText(unit) {
  return edgePercentText(unit?.member?.nearestEdge);
}

/**
 * The Match cell's tooltip: what the number is, then what a dash stands for.
 *
 * The absences are NOT one fact reported several ways. A picture nothing has
 * analysed yet is not a mistake and must never be reported as one; a picture
 * with nothing to compare against has no measurement rather than a poor one;
 * and a measured value names the sibling it was measured against, plus whether
 * the threshold put it outside the cluster. The same sentences the queue row
 * says, so a user meeting the member on both surfaces meets the same words.
 *
 * @param {Object} unit
 * @returns {string}
 */
function matchTitle(unit) {
  const member = unit?.member;
  if (!member) return "";
  const lead = "How close this picture gets to its nearest sibling.";
  if (member.unhashed) {
    return `${lead} This picture has not been analysed yet, so it cannot be compared.`;
  }
  if (member.nearestEdge === null) {
    return `${lead} Nothing else in this stack has been analysed yet, so there is nothing to compare this against.`;
  }
  const closest = `${lead} Closest match ${edgePercentText(member.nearestEdge)}, to picture #${member.nearestPictureId}.`;
  return member.stranded
    ? `${closest} That is below your threshold, so it is out of the stack's cluster.`
    : closest;
}

/**
 * A unit's name, for the accessible label and the image's alt text.
 *
 * The deck's whole disclosure lives here: how deep the stack really is and how
 * much of it this group named. There is no corner budget for a second numeral.
 *
 * @param {Object} unit
 * @param {number} index
 * @returns {string}
 */
function unitName(unit, index) {
  // The queue row's own vocabulary (`thumbLabel` in DedupGroupRow), so the
  // thing a screen reader heard in the row is named the same way here.
  const position = `${index + 1} of ${units.value.length}`;
  if (isMixed.value) {
    // The strip is deliberately unlabelled imagery, so without the mark in the
    // name every card reaches assistive tech as the same anonymous control
    // repeated N times. It leads, because it is the one fact the primary button
    // acts on. The same sentence the queue row's tiles carry.
    return isMarkedUnit(unit)
      ? `Picture ${position}, marked as a stranger, it comes out of the stack`
      : `Picture ${position}, in the stack`;
  }
  if (unit.kind !== "deck") return `Picture ${position}`;
  const depth =
    unit.matchedCount < unit.depth
      ? `a stack of ${unit.depth} pictures, ${unit.matchedCount} of them matched`
      : `a stack of ${unit.depth} pictures`;
  return `Item ${position}, ${depth}`;
}

/**
 * What clicking a card does, said in full.
 * @param {Object} unit
 * @returns {string}
 */
function pickTitle(unit) {
  if (isMixed.value) {
    const marked = isMarkedUnit(unit);
    if (props.readOnly) {
      return marked
        ? "Marked as a stranger; it would come out of the stack."
        : "In the stack.";
    }
    return marked
      ? "Click, or press X, to put this picture back in the stack."
      : "Click, or press X, to mark this picture as a stranger, so it comes out of the stack.";
  }
  return unit.kind === "deck"
    ? "Make this stack the cover. Its leader, the picture shown here, leads the result."
    : "Make this the cover";
}

/**
 * The card's PRIMARY gesture, which is the whole difference between the two
 * modes: choose a cover, or mark a stranger.
 *
 * @param {Object} unit
 */
function onCardPick(unit) {
  if (!isMixed.value) {
    emit("set-cover", unit.coverPictureId);
    return;
  }
  markMember(unit);
}

/**
 * The card's SECONDARY gesture.
 *
 * In group mode right-click leaves a unit out, which is a different verdict
 * from choosing a cover. In mixed mode it is the SAME gesture as the click,
 * because the queue row it mirrors makes it so: a mixed stack has no cover, so
 * there is one thing a tile can mean and both buttons perform it.
 *
 * @param {Object} unit
 */
function onCardContext(unit) {
  if (!isMixed.value) {
    emit("toggle-excluded", unitToggleId(unit));
    return;
  }
  markMember(unit);
}

/**
 * The In-stack checkbox, which travels with the mode: the unit's exclusion, or
 * the member's stranger mark.
 *
 * @param {Object} unit
 */
function onInStackToggle(unit) {
  if (!isMixed.value) {
    emit("toggle-excluded", unitToggleId(unit));
    return;
  }
  markMember(unit);
}

/**
 * Mark or unmark one member as a stranger. Refused in a read-only session, the
 * same way the verdict footer is: a gesture the server would refuse is worse
 * offered than absent.
 *
 * @param {Object} unit
 */
function markMember(unit) {
  if (props.readOnly) return;
  emit("toggle-mark", unit.coverPictureId);
}

// ── The expansion: what is inside a deck ───────────────────────────────────
// A full-width band BELOW the strip, never inside a card: a card that grew by a
// band's height would break the registration between the pictures, which is the
// one thing this whole surface is for. At most one open at a time, for the same
// reason: a second band pushes the cards off the screen.

/** How tall the expanded members are drawn. Height only; width follows the
 * decoded image, because stored dimensions ignore EXIF rotation. */
const EXPANSION_THUMB_HEIGHT = 96;

const expandedStackId = ref(null);

/** The deck the band is open on, or null. */
const expandedUnit = computed(() => {
  if (expandedStackId.value === null) return null;
  return (
    units.value.find(
      (unit) =>
        unit.kind === "deck" &&
        String(unit.stackId) === String(expandedStackId.value),
    ) ?? null
  );
});

const expansionEntry = computed(() =>
  membersEntry(expandedUnit.value?.stackId),
);

const expansionLoading = computed(
  () => Boolean(expandedUnit.value) && Boolean(expansionEntry.value?.loading),
);

const expansionFailed = computed(
  () =>
    Boolean(expandedUnit.value) &&
    !expansionEntry.value?.loading &&
    (Boolean(expansionEntry.value?.failed) ||
      !expansionEntry.value?.members?.length),
);

/** The members in the shape `StackExpansionStrip` reads, in stack order. */
const expansionMembers = computed(() =>
  (expansionEntry.value?.members ?? []).map((member) => ({
    id: candidateId(member),
    thumbnail_version: member?.thumbnail_version,
  })),
);

/**
 * Which member the strip flags as the cover: the group's own cover when it is
 * one of these pictures, the stack's leader otherwise.
 */
const expansionCoverId = computed(() => {
  const unit = expandedUnit.value;
  if (!unit) return null;
  const ids = expansionMembers.value.map((member) => member.id);
  if (props.coverId != null && ids.includes(props.coverId))
    return props.coverId;
  return unit.coverPictureId;
});

/**
 * Whether this unit's members are on screen.
 * @param {Object} unit
 * @returns {boolean}
 */
function isExpanded(unit) {
  return (
    unit.kind === "deck" &&
    expandedStackId.value !== null &&
    String(unit.stackId) === String(expandedStackId.value)
  );
}

/**
 * Open one deck's members, closing whichever was open.
 * @param {Object} unit
 */
function toggleExpansion(unit) {
  if (unit.kind !== "deck") return;
  promoteId.value = null;
  if (isExpanded(unit)) {
    expandedStackId.value = null;
    return;
  }
  expandedStackId.value = unit.stackId;
  fetchStackMembers(unit.stackId, { full: true });
}

/** Retry the member read after a failure. */
function retryExpansion() {
  const unit = expandedUnit.value;
  if (unit) fetchStackMembers(unit.stackId, { full: true, force: true });
}

// ── Promoting a member to cover ────────────────────────────────────────────
// Allowed here and nowhere else (D2): from the queue row it was withdrawn,
// because the gesture also re-covers that stack in the library and a tile has
// no room to say so. This band does, so the promotion is a two-step whose
// second step carries the consequence in its own text rather than a bare click.

const promoteId = ref(null);

/**
 * A member the user asked to promote, once confirmed.
 * @param {number|string} pictureId
 */
function askPromote(pictureId) {
  if (props.readOnly) return;
  promoteId.value = pictureId;
}

/** The sentence the confirmation is made against. */
const promoteConsequence = computed(() => {
  const unit = expandedUnit.value;
  if (promoteId.value === null || !unit) return "";
  return `Make #${promoteId.value} the cover. It also becomes the picture this stack of ${unit.depth} shows everywhere in your library, not only here.`;
});

function confirmPromote() {
  if (promoteId.value === null) return;
  emit("set-cover", promoteId.value);
  promoteId.value = null;
}

/**
 * Whether a value ties the column maximum, so it renders as the best one.
 *
 * A column whose maximum is 0 has nothing to win, so nothing is highlighted.
 *
 * NEVER in mixed mode. The chip answers "which of these is the better file",
 * and that page is asking "which of these does not belong": a winner's chip
 * next to a stranger's warning border is a confident answer to the wrong
 * question, and the reader has to work out which of the two marks to believe.
 * The Match column is that page's comparison, and it has its own chip.
 */
function isBest(value, best) {
  if (isMixed.value) return false;
  const numeric = Number(value);
  return best > 0 && Number.isFinite(numeric) && numeric === best;
}

function resolutionText(candidate) {
  const width = Number(candidate?.width);
  const height = Number(candidate?.height);
  if (!Number.isFinite(width) || !Number.isFinite(height)) return EMPTY;
  return `${width} x ${height}`;
}

function fileText(candidate) {
  if (!candidate) return EMPTY;
  const size = candidateSizeMb(candidate);
  const format = candidate.format || "";
  if (!size) return format || EMPTY;
  return `${size.toFixed(1)} MB, ${format || EMPTY}`;
}

function capturedText(candidate) {
  return candidate?.created_at
    ? formatUserDate(candidate.created_at, "iso")
    : EMPTY;
}

function starCount(candidate) {
  const score = Math.round(Number(candidate?.score));
  return Number.isFinite(score) && score > 0 ? score : 0;
}

function tagText(candidate) {
  // No record is not the same claim as no tags: a deck whose leader has not
  // arrived yet must not report an authoritative "none".
  if (!candidate) return EMPTY;
  const count = Number(candidate.tag_count) || 0;
  return count > 0 ? `${count} tags` : "none";
}

// ── Zoom: the design system's full-screen blink compare ────────────────────
// One candidate fills the screen; flipping in place (arrows, 1-9) makes the
// differences jump out as motion. The magnification is a CONTINUOUS scale
// (owner requirement): the wheel means zoom for the whole gesture - wheel up
// over a candidate opens the zoom and continued wheeling keeps magnifying,
// anchored at the cursor (binding: the image point under the pointer stays
// stationary through every scale change) - and wheeling out three full
// notches of deliberate resistance past the fit floor leaves the zoom back
// to Compare. Fit and 100% (actual
// pixels) are SNAP STOPS on the continuum (the header buttons and the P key),
// drag pans at every overflowing level, and a flip keeps the scale and pan so
// the blink stays registered.

// The zoom flips PICTURES, not units: the flattened sequence is unit 1's
// leader, then the rest of unit 1's known members in stack order, then unit 2's
// leader, and so on. Being able to eyeball a sibling at 100% is the strongest
// disclosure this surface has for a group that named only one member of a
// stack, and a zoom that could only reach the leader would be offering the
// deck's face as if it were the deck. The digits keep their layering: inside
// the zoom 1-9 address these pictures, outside it they address the cards.

/** The picture the zoom is showing, by ID rather than by index: the sequence
 * GROWS when an expansion's members land, and an index would silently slide
 * onto a different picture underneath the user. */
const zoomId = ref(null);
const zoomImgEl = ref(null);
const zoomScrollEl = ref(null);
let panState = null;

/** The current scale, 1 = actual pixels; null until the image has measured
 * (the un-measured state renders the classic fit look via CSS). */
// One key press moves the zoom about 27%, which is `exp(120 * ZOOM_INTENSITY)`.
// Expressed as a wheel delta rather than a scale factor so the keyboard and the
// wheel share one curve instead of two that drift.
const ZOOM_KEY_STEP_DELTA = 120;
// A pan step is a fifth of the viewport: far enough to make progress across a
// 1024px crop, small enough to keep the eye's place.
const ZOOM_PAN_FRACTION = 0.2;
const ZOOM_PAN_MIN_STEP = 48;

const zoomScale = ref(null);
/** The floor of the continuum: the scale at which this image exactly fits. */
const zoomFitScale = ref(1);
/** The displayed image's natural pixel size, measured on load. */
const zoomNatural = ref(null);
/** Outward wheel delta accumulated while AT the fit floor; three full
 * notches of deliberate resistance close the zoom (the hysteresis - see
 * ZOOM_EXIT_RESISTANCE in utils/zoomMath.js). A pause longer than the
 * gesture gap starts the count over. */
let zoomCloseAccumulator = 0;
let zoomCloseLastOutTs = 0;

/**
 * Every picture the zoom can reach, in reading order.
 *
 * A deck contributes its leader first and then the members we hold: the whole
 * stack once an expansion has fetched it, and until then only the members this
 * group named, because those are the only rows the payload carries.
 */
const zoomPictures = computed(() => {
  const list = [];
  // Mixed mode has nothing folded: one entry per member, in stack order, which
  // is exactly the strip's own order. The record is that member's fetched row,
  // so the zoom serves the down-scaled ORIGINAL rather than a thumbnail; until
  // it lands it is null, and the thumbnail stands in.
  if (isMixed.value) {
    return units.value.map((unit) => ({
      id: unit.coverPictureId,
      record: faceOf(unit),
      version: unit.thumbnailVersion,
      unit,
    }));
  }
  for (const unit of units.value) {
    if (unit.kind !== "deck") {
      const candidate = unit.candidates[0];
      list.push({
        id: candidateId(candidate),
        record: candidate,
        version: unit.thumbnailVersion,
        unit,
      });
      continue;
    }
    const fetched = membersEntry(unit.stackId)?.members ?? [];
    if (fetched.length > 1) {
      for (const member of fetched) {
        list.push({
          id: candidateId(member),
          record: member,
          version: String(member?.thumbnail_version ?? ""),
          unit,
        });
      }
      continue;
    }
    list.push({
      id: unit.coverPictureId,
      record: faceOf(unit),
      version: unit.thumbnailVersion,
      unit,
    });
    for (const candidate of unit.candidates) {
      const id = candidateId(candidate);
      if (id === unit.coverPictureId) continue;
      list.push({
        id,
        record: candidate,
        version: String(candidate?.thumbnail_version ?? ""),
        unit,
      });
    }
  }
  return list;
});

const zoomIndex = computed(() => {
  if (zoomId.value === null) return null;
  const index = zoomPictures.value.findIndex(
    (entry) => entry.id === zoomId.value,
  );
  return index < 0 ? null : index;
});

const zoomOpen = computed(() => props.open && zoomIndex.value !== null);

/** The entry on screen: its id, the record behind it (null while a leader's
 * row has not landed), and the unit it belongs to. */
const zoomEntry = computed(() =>
  zoomOpen.value ? zoomPictures.value[zoomIndex.value] : null,
);

const zoomMetaText = computed(() => {
  const entry = zoomEntry.value;
  if (!entry) return "";
  const c = entry.record;
  const parts = [`#${entry.id}`, resolutionText(c), fileText(c)];
  if (entry.unit.kind === "deck") {
    parts.push(
      entry.id === entry.unit.coverPictureId
        ? `Leads a stack of ${entry.unit.depth}`
        : `In a stack of ${entry.unit.depth}`,
    );
  }
  const stars = starCount(c);
  if (stars) parts.push("★".repeat(stars));
  const smart = candidateSmartScore(c);
  if (smart !== null) parts.push(`Smart score ${smart.toFixed(2)}`);
  return parts.filter((p) => p !== EMPTY).join(" · ");
});

/** Where the picture at `index` sits, clamped into the sequence. */
function zoomIdAt(index) {
  const list = zoomPictures.value;
  if (!list.length) return null;
  const clamped = Math.max(0, Math.min(list.length - 1, index));
  return list[clamped].id;
}

/** The cover's index, where Z lands when no picture was named. */
function coverIndex() {
  const index = zoomPictures.value.findIndex((entry) =>
    isUnitCover(entry.unit),
  );
  return index >= 0 ? index : 0;
}

function openZoom(index = null) {
  if (!zoomPictures.value.length) return;
  zoomId.value = zoomIdAt(index == null ? coverIndex() : index);
}

function closeZoom() {
  zoomId.value = null;
  zoomScale.value = null;
  zoomNatural.value = null;
  zoomCloseAccumulator = 0;
  zoomCloseLastOutTs = 0;
  panState = null;
}

/** Flip forward/back, wrapping - a blink loop, not a bounded carousel. The
 * scale and pan are deliberately NOT reset: flipping at identical
 * magnification is what makes differences read as motion (the new image's
 * own fit floor re-clamps on load). */
function flipZoom(delta) {
  if (!zoomOpen.value) return;
  const n = zoomPictures.value.length;
  zoomId.value = zoomPictures.value[(zoomIndex.value + delta + n) % n].id;
}

function zoomTo(index) {
  if (!zoomOpen.value) return;
  if (index >= 0 && index < zoomPictures.value.length) {
    zoomId.value = zoomPictures.value[index].id;
  }
}

/** Where a card's own zoom button lands: that unit's leader. */
function unitZoomStart(unitIndex) {
  const unit = units.value[unitIndex];
  if (!unit) return 0;
  const index = zoomPictures.value.findIndex((entry) => entry.unit === unit);
  return index < 0 ? 0 : index;
}

/** Whether the current scale sits on a snap stop (1% slack). */
function nearScale(target) {
  const scale = zoomScale.value;
  return scale !== null && Math.abs(scale - target) <= target * 0.01;
}

const zoomAtFit = computed(() => nearScale(zoomFitScale.value));
const zoomAtActual = computed(() => nearScale(1));

/** The readout: percentage of ACTUAL pixels, the photo-tool convention.
 * Visibility of status - it is also what makes the blink guarantee (same
 * magnification across flips) verifiable by eye. */
const zoomPercent = computed(() =>
  zoomScale.value === null ? null : formatZoomPercent(zoomScale.value),
);

/**
 * Snap to a stop on the continuum, anchored at the viewport centre (a
 * keypress or button has no cursor to anchor on; centre is the convention).
 * @param {number} target
 */
function snapZoomTo(target) {
  if (!zoomOpen.value || zoomScale.value === null) return;
  const el = zoomScrollEl.value;
  applyZoomScale(
    Math.max(zoomFitScale.value, Math.min(ZOOM_MAX_SCALE, target)),
    el ? { x: el.clientWidth / 2, y: el.clientHeight / 2 } : { x: 0, y: 0 },
  );
}

/** P: flip between the two snap stops - fit and 100% - exactly the two
 * states the old toggle had, now as points on the continuum. */
function toggleZoomPixels() {
  if (!zoomOpen.value) return;
  snapZoomTo(zoomAtActual.value ? zoomFitScale.value : 1);
}

/** Measure the displayed image once it loads: its natural size fixes the fit
 * floor, and the first measurement lands the scale ON that floor (the zoom
 * opens at fit; a flip instead re-clamps the kept scale to the new floor). */
function onZoomImgLoad() {
  const img = zoomImgEl.value;
  const el = zoomScrollEl.value;
  const c = zoomEntry.value?.record;
  const naturalW = img?.naturalWidth || Number(c?.width) || 0;
  const naturalH = img?.naturalHeight || Number(c?.height) || 0;
  if (!naturalW || !naturalH) return;
  zoomNatural.value = { w: naturalW, h: naturalH };
  const cw = el?.clientWidth || 0;
  const ch = el?.clientHeight || 0;
  // No measurable viewport (or a zero-layout test environment): the floor
  // falls back to actual pixels so the continuum still behaves.
  zoomFitScale.value = cw && ch ? Math.min(cw / naturalW, ch / naturalH) : 1;
  if (zoomScale.value === null) {
    zoomScale.value = zoomFitScale.value;
  } else {
    zoomScale.value = Math.max(
      zoomFitScale.value,
      Math.min(ZOOM_MAX_SCALE, zoomScale.value),
    );
  }
}

/** The explicit pixel size the scale implies; empty until measured, which
 * leaves the classic CSS fit rendering in charge. */
const zoomImgStyle = computed(() => {
  if (zoomScale.value === null || !zoomNatural.value) return undefined;
  return {
    width: `${zoomNatural.value.w * zoomScale.value}px`,
    height: `${zoomNatural.value.h * zoomScale.value}px`,
    maxWidth: "none",
    maxHeight: "none",
  };
});

/** Whether the scaled image overflows the viewport (pan has meaning). */
const zoomOverflowing = computed(() => {
  const el = zoomScrollEl.value;
  if (!el || zoomScale.value === null || !zoomNatural.value) return false;
  return (
    zoomNatural.value.w * zoomScale.value > el.clientWidth ||
    zoomNatural.value.h * zoomScale.value > el.clientHeight
  );
});

/**
 * Apply a new scale with the CURSOR ANCHOR (binding): the image point under
 * the pointer is computed before the change, the scale applied, and the
 * scroll re-solved so that point is back under the pointer - clamped at the
 * edges. The scroll is written after the DOM has adopted the new image size.
 *
 * @param {number} next
 * @param {{x: number, y: number}} cursor - relative to the scroll container.
 */
function applyZoomScale(next, cursor) {
  const el = zoomScrollEl.value;
  const natural = zoomNatural.value;
  const oldScale = zoomScale.value;
  zoomScale.value = next;
  if (!el || !natural || oldScale === null) return;
  const target = anchorZoomScroll({
    cursorX: cursor.x,
    cursorY: cursor.y,
    scrollLeft: el.scrollLeft,
    scrollTop: el.scrollTop,
    containerWidth: el.clientWidth,
    containerHeight: el.clientHeight,
    imageWidth: natural.w,
    imageHeight: natural.h,
    oldScale,
    newScale: next,
  });
  nextTick(() => {
    el.scrollLeft = target.left;
    el.scrollTop = target.top;
  });
}

/**
 * Step the zoom from the keyboard, anchored on the VIEWPORT CENTRE.
 *
 * Routed through `zoomStepScale`, the same function the wheel uses, so the two
 * inputs cannot drift into different zoom curves. The wheel anchors on the
 * cursor because there is one; a key press has no pointer, so the centre of
 * what the user is looking at is the honest anchor.
 *
 * @param {number} direction - +1 to zoom in, -1 to zoom out.
 */
function stepZoomByKey(direction) {
  if (!zoomOpen.value || zoomScale.value === null) return;
  const el = zoomScrollEl.value;
  // Negative deltaY is "zoom in" in the wheel's own convention.
  const next = zoomStepScale(
    zoomScale.value,
    -direction * ZOOM_KEY_STEP_DELTA,
    zoomFitScale.value,
  );
  if (next === zoomScale.value) return;
  applyZoomScale(
    next,
    el ? { x: el.clientWidth / 2, y: el.clientHeight / 2 } : { x: 0, y: 0 },
  );
}

/**
 * Pan the zoomed image from the keyboard.
 *
 * Only meaningful while the image overflows its viewport, which is exactly when
 * a mouse user would drag. At Fit there is nothing to pan and the key should
 * fall through rather than silently doing nothing.
 *
 * @param {number} dx - columns to move, in step units.
 * @param {number} dy - rows to move, in step units.
 * @returns {boolean} whether the pan was applied.
 */
function panZoomByKey(dx, dy) {
  const el = zoomScrollEl.value;
  if (!zoomOpen.value || !el || !zoomOverflowing.value) return false;
  const stepX = Math.max(ZOOM_PAN_MIN_STEP, el.clientWidth * ZOOM_PAN_FRACTION);
  const stepY = Math.max(
    ZOOM_PAN_MIN_STEP,
    el.clientHeight * ZOOM_PAN_FRACTION,
  );
  el.scrollLeft += dx * stepX;
  el.scrollTop += dy * stepY;
  return true;
}

/**
 * Escape peels ONE layer: with the zoom up, a close request (AppDialog's own
 * Escape handling on its subtree, Vuetify's ESC/scrim, the header X) closes
 * the ZOOM and keeps the dialog; the next one closes the dialog. The queue's
 * keyboard model already layers Escape this way when the event reaches it,
 * but a keydown with DOM focus inside the dialog subtree is claimed by
 * AppDialog first (it stopPropagation-s), so the dialog's close intent has to
 * respect the layer here too or that path closes both at once.
 */
function requestClose() {
  if (zoomOpen.value) {
    closeZoom();
    return;
  }
  emit("close");
}

// ── Wheel: the mouse's way into (and through) the zoom ─────────────────────
// Scrolling over a candidate's picture opens the blink compare on it, like
// the zoom button. Inside the zoom, the wheel flips candidates in Fit - the
// same in-place flip the arrow keys do, so differences read as motion. In
// Actual pixels the surface is a scroll-to-pan container, so the wheel stays
// native there and the flip gesture deliberately does not exist.

/**
 * Wheel over a candidate's picture: wheel UP (the zoom-in direction) opens
 * the zoom, and the SAME gesture's next ticks keep zooming in over the zoom
 * surface - one continuous motion, no meaning-switch. The opening event only
 * opens (at fit), so the first tick after it steps once and cannot
 * overshoot; a wheel DOWN over a thumbnail is already fully zoomed out and
 * deliberately does nothing (and is not hijacked).
 *
 * First-tick anchoring: the jump from a strip thumbnail to the full-screen
 * surface has no meaningful cursor geometry to preserve, so the zoom opens
 * at fit and anchors perfectly from the first in-tick on the zoom surface.
 *
 * @param {number} unitIndex - the CARD wheeled over; the zoom opens on that
 *   unit's leader, which is the picture the card is showing.
 * @param {WheelEvent} event
 */
function onThumbWheel(unitIndex, event) {
  if (event.deltaY >= 0) return;
  event.preventDefault();
  event.stopPropagation();
  openZoom(unitZoomStart(unitIndex));
}

/**
 * The wheel inside the zoom means ZOOM, continuously: up magnifies, down
 * shrinks toward the fit floor, and three further full notches AT the floor
 * (deliberate resistance) close back to Compare (the accumulator is the
 * hysteresis - see ZOOM_EXIT_RESISTANCE in utils/zoomMath.js). Always
 * preventDefault: the wheel must never scroll the page or the dialog behind
 * the zoom.
 *
 * @param {WheelEvent} event
 */
function onZoomWheel(event) {
  event.preventDefault();
  if (zoomScale.value === null) return;
  // Normalize line/page-mode wheels to pixels (shared zoom-family rule):
  // a raw line-mode deltaY of ±3 fed into pixel-tuned arithmetic zoomed
  // ~50× weaker per notch, and took ~40 notches at the floor to leave.
  const deltaY = normalizeWheelDelta(event, {
    pageHeightPx: zoomScrollEl.value?.clientHeight || undefined,
  });
  if (!deltaY) return;
  if (deltaY > 0 && atFitFloor(zoomScale.value, zoomFitScale.value)) {
    // The exit accumulates only AT the floor; a pause longer than the gesture
    // gap starts the count over, so a later gesture meets the full
    // resistance itself instead of inheriting stale part-way accumulation.
    const now = Date.now();
    if (now - zoomCloseLastOutTs > ZOOM_EXIT_GESTURE_GAP_MS) {
      zoomCloseAccumulator = 0;
    }
    zoomCloseLastOutTs = now;
    zoomCloseAccumulator += deltaY;
    if (zoomCloseAccumulator >= ZOOM_EXIT_RESISTANCE) closeZoom();
    return;
  }
  // Any zoom-in movement resets the exit accumulation.
  zoomCloseAccumulator = 0;
  const next = zoomStepScale(zoomScale.value, deltaY, zoomFitScale.value);
  if (next === zoomScale.value) return;
  const rect = zoomScrollEl.value?.getBoundingClientRect?.();
  applyZoomScale(next, {
    x: rect ? event.clientX - rect.left : 0,
    y: rect ? event.clientY - rect.top : 0,
  });
}

// The queue's keyboard model is the single key owner; it drives the zoom
// through this surface instead of the dialog competing for the keydown.
// `zoomLevel` is the imperative read the tests (and any future readout)
// use; the visible percentage renders from the same scale.
defineExpose({
  isZoomOpen: () => zoomOpen.value,
  openZoom,
  closeZoom,
  flipZoom,
  zoomTo,
  toggleZoomPixels,
  stepZoom: stepZoomByKey,
  panZoom: panZoomByKey,
  canPanZoom: () => zoomOpen.value && zoomOverflowing.value,
  zoomLevel: () => zoomScale.value,
});

// A new group, or a closed dialog, always starts un-zoomed at Fit: a zoom held
// over from another group would open on the wrong picture. The stack reads go
// with it: the ids are another group's, and a member page left in the cache
// would draw the wrong stack under the next group's deck.
watch(
  () => [props.open, props.group?.signature],
  () => resetSubjectState(),
);

/**
 * The mixed half of the same reset, as a SEPARATE watcher on a string key.
 *
 * It cannot be a fourth element of the array above, and that is not a style
 * choice. That getter returns a fresh array on every evaluation, so `Object.is`
 * always reports a change and the watcher fires whenever the caller hands over
 * a new `mixedStack` object at all, identical contents included. Keying the
 * reset on membership would then be decorative: a threshold reload re-serves
 * the same stack as a new object and would throw away the picture rows and
 * re-read them, which is exactly the cost the fingerprint exists to avoid.
 * `subjectKey` is a string, so this fires when the membership genuinely
 * changes and not otherwise.
 *
 * Guarded on the mode so the group path keeps precisely the watcher it always
 * had, and gains no second, differently-timed reset.
 */
watch(
  () => subjectKey.value,
  () => {
    if (!isMixed.value) return;
    resetSubjectState();
  },
);

/** Everything held about the subject that just went off screen. */
function resetSubjectState() {
  closeZoom();
  stackMembers.value = {};
  expandedStackId.value = null;
  promoteId.value = null;
}

// A deck whose leader is not one of the group's candidates has no numbers to
// compare until its row is read, and that is the common case rather than an
// edge one. One member per such deck, on a surface the user opened on purpose.
watch(
  () => (props.open ? units.value : null),
  (list) => {
    if (isMixed.value) {
      // The whole stack, in one request: every member is a card here, and
      // without their rows the metric cells are dashes and the zoom is looking
      // at a thumbnail. The reset watch above has already cleared the cache for
      // a new stack, so this refetches when the cursor moves to the next row.
      const stackId = props.mixedStack?.stack_id;
      if (list?.length && stackId !== null && stackId !== undefined) {
        fetchStackMembers(stackId, { full: true });
      }
      return;
    }
    for (const unit of list ?? []) {
      if (unit.kind === "deck" && !unit.face) fetchStackMembers(unit.stackId);
    }
  },
  { immediate: true },
);

// ── Drag-to-pan, at every overflowing zoom level ───────────────────────────
// The wheel never pans (it zooms); the drag is the pan gesture wherever the
// scaled image overflows. At fit there is nothing to pan and the drag no-ops.

function onZoomPointerDown(event) {
  if (!zoomOpen.value || event.button !== 0) return;
  const el = zoomScrollEl.value;
  if (!el) return;
  panState = {
    x: event.clientX,
    y: event.clientY,
    left: el.scrollLeft,
    top: el.scrollTop,
    moved: false,
  };
}

function onZoomPointerMove(event) {
  if (!panState) return;
  const el = zoomScrollEl.value;
  if (!el) return;
  const dx = event.clientX - panState.x;
  const dy = event.clientY - panState.y;
  if (Math.abs(dx) + Math.abs(dy) > 3) panState.moved = true;
  el.scrollLeft = panState.left - dx;
  el.scrollTop = panState.top - dy;
}

function onZoomPointerUp() {
  // Cleared on the next tick so the click handler can still tell a drag from
  // a pick.
  setTimeout(() => {
    panState = null;
  }, 0);
}

/**
 * Click picks the cover: unless the click was the tail end of a pan.
 *
 * The gesture stays UNIT-level even though the zoom flips pictures: on a deck
 * member it names the deck (its leader), because promoting one member of a
 * stack also re-covers that stack in the library, and that consequence gets
 * said in words in the expansion band or not offered at all.
 */
function onZoomClick() {
  if (panState?.moved) return;
  const entry = zoomEntry.value;
  if (!entry) return;
  // The zoom's gesture is the card's gesture at full screen, so it follows the
  // mode with it: choose the cover, or mark the stranger. In mixed mode the
  // entry IS the member, so there is no unit-level indirection to make.
  if (isMixed.value) {
    markMember(entry.unit);
    return;
  }
  emit("set-cover", entry.unit.coverPictureId);
}

function onZoomContextMenu() {
  const entry = zoomEntry.value;
  if (!entry) return;
  if (isMixed.value) {
    markMember(entry.unit);
    return;
  }
  emit("toggle-excluded", unitToggleId(entry.unit));
}
</script>

<template>
  <AppDialog :open="open" :title="dialogTitle" fullscreen @close="requestClose">
    <!-- The claim rides in the header, next to the close button, so it stays
         visible while the strip below scrolls: the group's confidence, or the
         mixed row's own reason for being listed. -->
    <template #header-right>
      <span v-if="isMixed" class="dc-confidence">{{ headerNote }}</span>
      <DedupConfidencePill v-else :group="group" />
    </template>

    <!-- ── The unit strip ──────────────────────────────────────────────────
         One card per UNIT, not per candidate: a whole existing stack is one
         deck, because that is the smallest thing a verdict can move. In mixed
         mode a unit is one member of one live stack, which is the smallest
         thing a split can move: same strip, same cards, different meaning.
         The cards GROW into the freed width, the images are the point of this
         surface, and scroll sideways only once there are too many to fit; the
         comparison only works when the fields line up across cards. -->
    <div class="dc-strip">
      <div
        v-for="({ unit, face }, index) in cards"
        :key="unit.key"
        class="dc-card"
        :class="{
          'dc-card--out': isUnitOut(unit),
          'dc-card--split': isMarkedUnit(unit),
        }"
        @contextmenu.prevent="onCardContext(unit)"
      >
        <button
          type="button"
          class="dc-pick"
          :aria-pressed="isMixed ? isMarkedUnit(unit) : isUnitCover(unit)"
          :aria-label="unitName(unit, index)"
          :title="pickTitle(unit)"
          @click="onCardPick(unit)"
        >
          <!-- Wheel over the picture starts the zoom on it: the mouse's
               equivalent of the corner button, without the pixel hunt. -->
          <span class="dc-thumb" @wheel="onThumbWheel(index, $event)">
            <img
              class="dc-thumb-img"
              :src="unitPreviewUrl(unit)"
              :alt="unitName(unit, index)"
              loading="lazy"
              decoding="async"
              draggable="false"
              @dragstart.prevent
            />
            <span class="dc-index">{{ index + 1 }}</span>
            <span v-if="isUnitCover(unit)" class="dc-flag dc-flag--cover">
              Cover
            </span>
            <!-- A deck is showing ONE picture and standing for several, so it
                 says which one this is: the numbers below are its, not the
                 stack's. -->
            <span
              v-if="unit.kind === 'deck'"
              class="dc-flag dc-flag--leader"
              title="This stack's leader. The numbers below are this picture's, and a cover choice on this card resolves to it."
            >
              Leader
            </span>
            <span v-if="isUnitOut(unit)" class="dc-flag dc-flag--out">
              Not in stack
            </span>
            <!-- The stranger says what is about to happen to it, in the same
                 corner the excluded flag uses, because it is the same kind of
                 statement: this picture is not staying with the others. -->
            <span v-if="isMarkedUnit(unit)" class="dc-flag dc-flag--split">
              Split off
            </span>
          </span>
        </button>
        <!-- The zoom trigger is a sibling of the pick button (a button inside
             a button is invalid markup), absolutely placed over the corner. -->
        <button
          type="button"
          class="dc-zoom"
          title="Zoom - full-screen blink compare (Z)"
          :aria-label="`Zoom ${unitName(unit, index)}`"
          @click.stop="openZoom(unitZoomStart(index))"
        >
          <v-icon size="16">mdi-magnify-plus-outline</v-icon>
        </button>

        <!-- The design system's compact meta: two columns, label over value,
             so the numbers read first and the metadata never squeezes the
             image. -->
        <span class="dc-meta">
          <!-- A deck's numbers are its LEADER's, and the label says so: an
               aggregate would answer no question this column exists to answer,
               and would break the per-column best mark, which compares FILES. -->
          <span class="dc-cell">
            <span class="dc-label">{{
              unit.kind === "deck" ? "Leader" : "ID"
            }}</span>
            <span class="dc-val">#{{ unit.coverPictureId }}</span>
          </span>
          <!-- The Match column is the mixed page's whole comparison: how close
               this member gets to its nearest sibling, measured whatever the
               threshold says, so a stranger reads 89% rather than a dash that
               looked like "matches nothing". The en dash is left for the one
               case with no measurement at all: nothing comparable to measure
               against. It sits beside the ID because those two are
               what a reader scans first here. Group-level like Location and
               Smart score (every card or none), because the meta grid is what
               the picture above it gives its leftover height to, and a row on
               some cards only would leave the pictures out of register. -->
          <span v-if="isMixed" class="dc-cell">
            <span class="dc-label">Match</span>
            <span
              class="dc-val"
              :class="{ 'dc-val--gap': isMarkedUnit(unit) }"
              :title="matchTitle(unit)"
              >{{ matchText(unit) }}</span
            >
          </span>
          <span class="dc-cell">
            <span class="dc-label">Resolution</span>
            <span
              class="dc-val"
              :class="{
                'dc-val--best': isBest(
                  candidateMegapixels(face),
                  bestMegapixels,
                ),
              }"
              >{{ resolutionText(face) }}</span
            >
          </span>
          <span class="dc-cell">
            <span class="dc-label">File</span>
            <span
              class="dc-val"
              :class="{
                'dc-val--best': isBest(candidateSizeMb(face), bestFileSize),
              }"
              >{{ fileText(face) }}</span
            >
          </span>
          <!-- What the card actually stands for. Group-level like Location and
               Smart score: it renders on every card or on none, because the
               meta grid is what the picture above it gives its leftover height
               to, and a row on some cards would leave the pictures at
               different heights. It sits against File on purpose: File is the
               LEADER's size, and on a deck that is the number a reader would
               otherwise take for the whole stack's footprint. -->
          <span v-if="anyDeck" class="dc-cell">
            <span class="dc-label">Contains</span>
            <span v-if="unit.kind === 'deck'" class="dc-val">
              <button
                type="button"
                class="dc-expand"
                :aria-expanded="isExpanded(unit)"
                :title="
                  isExpanded(unit)
                    ? 'Hide the pictures in this stack'
                    : 'Show the pictures in this stack, below the cards'
                "
                @click.stop="toggleExpansion(unit)"
              >
                <v-icon size="14">{{
                  isExpanded(unit) ? "mdi-chevron-down" : "mdi-chevron-right"
                }}</v-icon>
                {{ containsText(unit) }}
              </button>
            </span>
            <span v-else class="dc-val">{{ containsText(unit) }}</span>
          </span>
          <span class="dc-cell">
            <span class="dc-label">Captured</span>
            <span class="dc-val">{{ capturedText(face) }}</span>
          </span>
          <span class="dc-cell">
            <span class="dc-label">Score</span>
            <span
              class="dc-val"
              :class="{ 'dc-val--best': isBest(face?.score, bestScore) }"
            >
              <template v-if="starCount(face)">
                <v-icon
                  v-for="star in starCount(face)"
                  :key="star"
                  size="13"
                  class="dc-star"
                  >mdi-star</v-icon
                >
              </template>
              <template v-else>{{ EMPTY }}</template>
            </span>
          </span>
          <!-- Group-level like the Location row: rendered on every card as
               soon as ONE copy has a smart score, so the cards keep the same
               shape and the pictures stay aligned. -->
          <span v-if="anySmartScore" class="dc-cell">
            <span class="dc-label">Smart score</span>
            <span
              class="dc-val"
              :class="{
                'dc-val--best': isBest(
                  candidateSmartScore(face),
                  bestSmartScore,
                ),
              }"
              >{{ smartScoreText(face) }}</span
            >
          </span>
          <span v-if="anySharpness" class="dc-cell">
            <span class="dc-label">Sharpness</span>
            <span
              class="dc-val"
              :class="{
                'dc-val--best': isBest(candidateSharpness(face), bestSharpness),
              }"
              >{{ sharpnessText(face) }}</span
            >
          </span>
          <span class="dc-cell">
            <span class="dc-label">Metadata</span>
            <span
              class="dc-val"
              :class="{
                'dc-val--best': isBest(face?.tag_count, bestTagCount),
              }"
              >{{ tagText(face) }}</span
            >
          </span>
          <!-- The checkbox toggles the whole unit: a stack goes into the
               verdict entire or not at all, so `All 5` and `None` are the only
               states a deck has. In mixed mode the unit is one member and the
               checkbox is the stranger mark, so the same control keeps meaning
               "will this picture be in the stack when I press the button". -->
          <span class="dc-cell">
            <span class="dc-label">{{ inStackLabel }}</span>
            <span class="dc-val dc-instack">
              <button
                type="button"
                class="dc-toggle"
                :aria-pressed="!leavesStack(unit)"
                :title="inStackToggleTitle(unit)"
                @click.stop="onInStackToggle(unit)"
              >
                <v-icon size="14">{{
                  leavesStack(unit)
                    ? "mdi-checkbox-blank-outline"
                    : "mdi-checkbox-marked-outline"
                }}</v-icon>
              </button>
              <span>{{ inStackText(unit) }}</span>
            </span>
          </span>
          <!-- The path is shown only for a reference-folder picture, where the
               user manages the files and needs to know which copy is which.
               Full width: paths do not fit half a column. The row appears on
               every card as soon as ONE copy has a path, so the cards stay the
               same shape and the pictures stay aligned. -->
          <span v-if="anyPathShown" class="dc-cell dc-cell--wide">
            <span class="dc-label">Location</span>
            <span
              v-if="showsPath(face)"
              class="dc-val dc-path"
              :title="candidatePath(face)"
            >
              <v-icon
                size="13"
                class="dc-path-icon"
                title="Reference folder, you manage these files yourself"
                >mdi-folder-eye-outline</v-icon
              >
              {{ shortenPath(candidatePath(face)) }}
            </span>
            <span
              v-else
              class="dc-val dc-path"
              title="Stored in your PixlStash library, not in a reference folder"
            >
              <v-icon size="13" class="dc-path-icon"
                >mdi-image-multiple-outline</v-icon
              >
              In your library
            </span>
          </span>
        </span>
      </div>
    </div>

    <!-- ── The mixed member read's one failure state ───────────────────────
         The comparison is NOT blocked behind this read: the ids, the Match
         column, the evidence, the marking gestures and both verdicts all come
         off the row payload, and the thumbnails still draw. What is missing is
         the per-picture detail, so the line says exactly that and disables
         nothing. Same construction as the expansion band's error, in the same
         place below the strip, because it is the same kind of statement. -->
    <div
      v-if="mixedFacesFailed"
      class="dc-expansion-state dc-expansion-state--error dc-mixed-error"
      role="status"
      data-testid="dedup-mixed-error"
    >
      <v-icon size="16">mdi-alert-outline</v-icon>
      <!-- The sentence names all three things that are actually missing and
           claims only what is tested: the marks and both verdicts are built
           from the row payload, so they still round-trip. The zoom is named
           because it degrades quietly: it still opens, on thumbnails, and a
           1:1 readout over a thumbnail is not the comparison it promises. -->
      <span
        >Could not read these pictures' details, so the sizes, dates and
        full-size previews are missing. Marking and splitting still work.</span
      >
      <AppButton variant="ghost" size="sm" @click="retryMixedFaces"
        >Try again</AppButton
      >
    </div>

    <!-- ── The expansion: one deck's members ───────────────────────────────
         FULL WIDTH, BELOW the strip, never inside a card. A band inside a card
         would grow that card and take the pictures out of register with each
         other, which is the one thing this surface exists to hold. At most one
         is open at a time, for the same reason: two bands push the cards off
         the screen. -->
    <div v-if="expandedUnit" class="dc-expansion" data-testid="dedup-expansion">
      <div v-if="expansionLoading" class="dc-expansion-state" role="status">
        <v-icon size="16" class="mdi-spin">mdi-loading</v-icon>
        Reading the pictures in this stack
      </div>
      <div
        v-else-if="expansionFailed"
        class="dc-expansion-state dc-expansion-state--error"
        role="alert"
      >
        <v-icon size="16">mdi-alert-outline</v-icon>
        <span
          >Could not read the pictures in this stack. The verdict buttons still
          work.</span
        >
        <AppButton variant="ghost" size="sm" @click="retryExpansion"
          >Try again</AppButton
        >
      </div>
      <template v-else>
        <StackExpansionStrip
          :count="expandedUnit.depth"
          :members="expansionMembers"
          :cover-id="expansionCoverId"
          :reason="`${expandedUnit.matchedCount} of them are in this group`"
          :thumb-height="EXPANSION_THUMB_HEIGHT"
          :read-only="readOnly"
          :show-unstack="false"
          @set-cover="askPromote"
        />
        <!-- Promotion is a two-step here BECAUSE it reaches past this group:
             the second step names what it does to the library, so the
             consequence is read rather than discovered. -->
        <div v-if="promoteId !== null" class="dc-promote" role="group">
          <v-icon size="16" class="dc-promote-icon">mdi-image-move</v-icon>
          <span class="dc-promote-text">{{ promoteConsequence }}</span>
          <AppButton variant="ghost" size="sm" @click="promoteId = null"
            >Cancel</AppButton
          >
          <AppButton variant="secondary" size="sm" @click="confirmPromote"
            >Make it the cover</AppButton
          >
        </div>
      </template>
    </div>

    <!-- ── Why these were grouped ──────────────────────────────────────────
         The same pill component the queue row uses, so the evidence a user
         glanced at in the row is literally the same treatment they study here.
         Compare shows all of it, where the row shows only the first two. The
         `fact` tone on the mixed page: the stack already exists, so its pills
         report what was measured rather than argue for a verdict. -->
    <DedupWhyPills
      class="dc-why"
      :why="whyPills"
      :variant="isMixed ? 'fact' : 'argument'"
    />

    <!-- ── The verdict footer ──────────────────────────────────────────────
         The whole decision is made here, in both modes, so opening Compare is
         never a detour the user has to back out of before they can act. -->
    <template #footer>
      <span v-if="!readOnly" class="dc-hint">
        <template v-if="isMixed">
          Click a picture, or press X, to mark it as a stranger. Z zooms. No
          file is ever deleted.
        </template>
        <template v-else>
          Click a picture, or press its number, to make it the cover.
          Right-click, or press X, to leave it out. Z zooms. No file is ever
          deleted.
        </template>
      </span>
      <AppButton variant="ghost" key-hint="esc" @click="requestClose"
        >Close</AppButton
      >
      <template v-if="isMixed">
        <!-- Keep is what makes the mixed list drainable: it changes no picture,
             so it is not undoable, and the way back is to clear the Keep. -->
        <AppButton
          v-if="!readOnly"
          variant="secondary"
          icon-left="check"
          key-hint="k"
          :disabled="busy"
          title="This stack is fine. It stops being listed until its pictures change."
          @click="emit('keep')"
        >
          Keep
        </AppButton>
        <!-- The primary NAMES ITS OUTCOME, and the page owns that prediction:
             the row and this footer must say the same thing about the same
             press, so the label and the icon arrive as props rather than being
             computed a second time here. -->
        <AppButton
          v-if="!readOnly"
          variant="primary"
          :icon-left="primaryIcon"
          key-hint="enter"
          :disabled="busy"
          :title="primaryTitle"
          @click="emit('resolve')"
        >
          {{ primaryLabel }}
        </AppButton>
      </template>
      <template v-else>
        <AppButton
          v-if="!readOnly"
          variant="secondary"
          icon-left="call-split"
          key-hint="k"
          :disabled="busy"
          title="Leave these as separate pictures. They stay in your library and stop being suggested."
          @click="emit('keep-separate')"
        >
          Keep separate
        </AppButton>
        <AppButton
          v-if="!readOnly"
          variant="primary"
          icon-left="layers-plus"
          key-hint="enter"
          :disabled="busy"
          title="Group these behind one cover. Every file stays on disk, and Ctrl+Z reverses it."
          @click="emit('stack')"
        >
          {{ verdictLabel.full }}
        </AppButton>
      </template>
    </template>
  </AppDialog>

  <!-- ── The blink compare ──────────────────────────────────────────────────
       Full screen, above the dialog: one PICTURE at a time, flipped in place
       so differences show as motion. It flips pictures rather than units
       because looking at a stack sibling at 100% is the strongest disclosure
       this surface has when a group named only one member of a stack.
       Deliberately near-black chrome at fixed colors: this is a
       photo-judgement surface, same rationale as the lightbox. -->
  <Teleport to="body">
    <div v-if="zoomOpen" class="dc-zv" data-testid="dedup-zoom">
      <div class="dc-zv-top">
        <div class="dc-zv-flip" role="tablist" aria-label="Picture">
          <button
            v-for="(entry, index) in zoomPictures"
            :key="entry.id"
            type="button"
            :class="{ 'dc-zv-on': index === zoomIndex }"
            @click="zoomTo(index)"
          >
            {{ index + 1 }}
          </button>
        </div>
        <span
          v-if="zoomEntry && isUnitCover(zoomEntry.unit)"
          class="dc-flag dc-flag--zv"
          >Cover</span
        >
        <span
          v-if="zoomEntry && isUnitOut(zoomEntry.unit)"
          class="dc-flag dc-flag--zv"
          >Not in stack</span
        >
        <!-- One flag replaces both on the mixed page, because there is one
             state a member can be in that the card does not already show. -->
        <span
          v-if="zoomEntry && isMarkedUnit(zoomEntry.unit)"
          class="dc-flag dc-flag--zv"
          >Split off</span
        >
        <span class="dc-zv-meta">{{ zoomMetaText }}</span>
        <!-- Fit and 100% are SNAP STOPS on the wheel's continuum; the
             percentage readout is what makes "same magnification across the
             blink" verifiable by eye. -->
        <div class="dc-zv-mode">
          <button
            type="button"
            :class="{ 'dc-zv-on': zoomAtFit }"
            title="Snap to fit - every candidate in the same box, the blink registered"
            @click="snapZoomTo(zoomFitScale)"
          >
            <v-icon size="15">mdi-fit-to-screen-outline</v-icon>
            Fit
          </button>
          <button
            type="button"
            :class="{ 'dc-zv-on': zoomAtActual }"
            title="Snap to 1:1 - resolution differences show as size jumps (P)"
            @click="snapZoomTo(1)"
          >
            <v-icon size="15">mdi-magnify-scan</v-icon>
            Actual pixels
          </button>
          <span v-if="zoomPercent !== null" class="dc-zv-pct">{{
            zoomPercent
          }}</span>
        </div>
        <button
          type="button"
          class="dc-zv-close"
          title="Back to compare (Esc)"
          @click="closeZoom()"
        >
          <v-icon size="18">mdi-close</v-icon>
        </button>
      </div>
      <div
        ref="zoomScrollEl"
        class="dc-zv-img"
        :class="{ 'dc-zv-img--pannable': zoomOverflowing }"
        @wheel="onZoomWheel"
        @mousedown="onZoomPointerDown"
        @mousemove="onZoomPointerMove"
        @mouseup="onZoomPointerUp"
        @mouseleave="onZoomPointerUp"
        @click="onZoomClick"
        @contextmenu.prevent="onZoomContextMenu"
      >
        <!-- draggable=false is load-bearing: the browser's native image drag
             starts on the same gesture as the pan and wins the race, leaving
             the pan dead and a ghost image under the cursor. -->
        <img
          v-if="zoomEntry"
          ref="zoomImgEl"
          :src="previewUrl(zoomEntry.id, zoomEntry.record, zoomEntry.version)"
          :style="zoomImgStyle"
          alt=""
          draggable="false"
          @load="onZoomImgLoad"
          @dragstart.prevent
        />
      </div>
      <div class="dc-zv-foot" aria-hidden="true">
        <span
          ><kbd>←</kbd><kbd>→</kbd> or <kbd>1</kbd>–<kbd>9</kbd> flip in place,
          differences jump out as motion</span
        >
        <span>Scroll zooms, drag pans - zoom out past Fit to leave</span>
        <span><kbd>P</kbd> Fit ↔ 100%</span>
        <!-- The legend names the verdicts the footer is actually offering; the
             keys are the same two in both modes, the outcomes are not. -->
        <span><kbd>Enter</kbd> {{ isMixed ? primaryLabel : "stack" }}</span>
        <span><kbd>K</kbd> {{ isMixed ? "keep" : "keep separate" }}</span>
        <span><kbd>Esc</kbd> back</span>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.dc-confidence {
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: rgba(var(--v-theme-on-surface), 0.7);
}

/* ── The strip ──────────────────────────────────────────────────────────────
   The cards grow to spend the fullscreen dialog's width on the images; a
   sideways scroll appears only when even the minimum card width overflows. */
.dc-strip {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: stretch;
  gap: var(--space-4);
  overflow-x: auto;
  scrollbar-gutter: stable;
  padding-bottom: var(--space-3);
  scrollbar-width: thin;
  scrollbar-color: rgba(var(--v-theme-on-surface), 0.4) transparent;
}

.dc-card {
  position: relative;
  flex: 1 0 300px;
  max-width: 640px;
  display: flex;
  flex-direction: column;
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
  box-shadow: var(--elevation-1);
  overflow: hidden;
}

/* An excluded copy stays fully readable, because it is still part of the
   comparison; it is just not going into the stack. */
.dc-card--out {
  border-style: dashed;
  border-color: rgba(var(--v-theme-on-surface), 0.35);
}

/* A stranger. Deliberately NOT the excluded treatment: the border carries the
   hue at full strength, because this card is the evidence the page exists for
   and quieting it would say "inert" about the one card that is not. The flag
   chip is the second, non-colour channel (WCAG 1.4.1). */
.dc-card--split {
  border-color: rgb(var(--v-theme-warning));
}

.dc-pick {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  width: 100%;
  text-align: left;
  padding: 0;
  color: inherit;
  font: inherit;
  transition: background var(--dur-1) var(--ease-standard);
}

.dc-pick:hover {
  background: var(--hover-wash);
}

.dc-pick[aria-pressed="true"] {
  background: var(--active-wash);
}

/* The image takes every pixel the metadata does not strictly need. */
.dc-thumb {
  position: relative;
  display: block;
  flex: 1;
  min-height: 220px;
  background: rgba(var(--v-theme-on-surface), 0.06);
}

.dc-thumb-img {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: contain;
  display: block;
}

/* Count pill, per the badge rules: primary fill, never the amber accent. */
.dc-index {
  position: absolute;
  top: var(--space-2);
  left: var(--space-2);
  min-width: var(--badge-size);
  height: var(--badge-size);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 var(--space-2);
  border-radius: var(--radius-pill);
  background: rgb(var(--v-theme-primary));
  color: rgb(var(--v-theme-on-primary));
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
}

/* The flags sit on top of an arbitrary photo, so they take the photo scrim. */
.dc-flag {
  position: absolute;
  top: var(--space-2);
  right: var(--space-2);
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-pill);
  background: var(--scrim-photo);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
}

.dc-flag--out,
.dc-flag--split {
  top: auto;
  bottom: var(--space-2);
}

/* Bottom LEFT, so it never collides with the cover flag above it or the
   excluded flag beside it. */
.dc-flag--leader {
  top: auto;
  right: auto;
  bottom: var(--space-2);
  left: var(--space-2);
}

/* The zoom trigger sits under the index pill, over the photo. */
.dc-zoom {
  position: absolute;
  top: calc(var(--space-2) + var(--badge-size) + var(--space-2));
  left: var(--space-2);
  z-index: var(--z-raised);
  width: 26px;
  height: 26px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  background: var(--scrim-photo);
  color: rgb(var(--v-theme-on-dark-surface));
}

.dc-zoom:hover {
  color: rgb(var(--v-theme-accent));
}

/* ── The compact meta grid: two columns, label over value ─────────────────── */
.dc-meta {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-2) var(--space-3);
  padding: var(--space-3) var(--space-4);
  align-content: start;
}

.dc-cell {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.dc-cell--wide {
  grid-column: 1 / -1;
}

/* The label is deliberately quiet: the values are what the user compares. */
.dc-label {
  font-size: var(--text-2xs);
  letter-spacing: var(--tracking-label);
  text-transform: uppercase;
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.dc-val {
  font-size: var(--text-sm);
  color: rgb(var(--v-theme-on-surface));
  font-variant-numeric: tabular-nums;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  /* The best-value chip below draws a border. Reserving its vertical space on
     every value keeps the meta rows the same height from card to card, and the
     meta block is what the picture above it gives its leftover height to: a
     card that wins three columns must not end up with a shorter picture. */
  border-block: 1px solid transparent;
}

/* The winner of a column reads first: a quiet primary-tinted chip. */
.dc-val--best {
  font-weight: var(--weight-semibold);
  background: rgba(var(--v-theme-primary), 0.18);
  border: 1px solid rgba(var(--v-theme-primary), 0.5);
  border-radius: var(--radius-sm);
  padding: 0 var(--space-2);
  align-self: flex-start;
  max-width: 100%;
}

/* The stranger's Match value: the same chip construction re-tinted to warning,
   so the two read as one component with one differing signal rather than as two
   inventions. It marks the OPPOSITE claim, that this is the value which does
   not belong, which is why the two never appear on the same card: `isBest` is
   suppressed in mixed mode entirely. The text stays `on-surface`: `warning` is
   a 3:1 UI colour and the tint is not a solid fill, so an `on-warning`
   foreground here would be the "on-<x> on a tint" trap (visual-language.md §4). */
.dc-val--gap {
  font-weight: var(--weight-semibold);
  background: rgba(var(--v-theme-warning), 0.12);
  border: 1px solid rgba(var(--v-theme-warning), 0.35);
  border-radius: var(--radius-sm);
  padding: 0 var(--space-2);
  align-self: flex-start;
  max-width: 100%;
}

.dc-star {
  color: rgb(var(--v-theme-accent));
}

.dc-path {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
}

.dc-path-icon {
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.dc-instack {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.dc-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: var(--space-1);
  border-radius: var(--radius-sm);
  color: rgba(var(--v-theme-on-surface), 0.7);
  transition:
    background var(--dur-1) var(--ease-standard),
    color var(--dur-1) var(--ease-standard);
}

.dc-toggle:hover {
  background: var(--hover-wash);
  color: rgb(var(--v-theme-on-surface));
}

/* The expansion trigger reads as the value it replaces, not as a button in a
   column of values: the chevron carries the affordance and the state. */
.dc-expand {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  max-width: 100%;
  padding: 0;
  color: inherit;
  font: inherit;
  font-variant-numeric: tabular-nums;
  text-align: left;
}

.dc-expand:hover {
  color: rgb(var(--v-theme-accent));
}

.dc-expand:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
  border-radius: var(--radius-sm);
}

/* ── The expansion band ────────────────────────────────────────────────────
   Full width under the strip. `flex-shrink: 0` keeps it whole: the strip above
   it is the flexible child and gives up the height, so opening a band never
   squeezes the band itself into a scrolling sliver. */
.dc-expansion {
  margin-top: var(--space-4);
  flex-shrink: 0;
}

.dc-expansion-state {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-4);
  border: 1px solid rgb(var(--v-theme-divider));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
  font-size: var(--text-sm);
  color: rgba(var(--v-theme-on-surface), 0.7);
}

/* The mixed read's failure line borrows the band's construction but not its
   parent, because mixed mode has no expansion band. So it carries `.dc-expansion`'s
   own two rules itself: the strip above is the flexible child and gives up the
   height, which is what stops this line being squeezed into a scrolling sliver. */
.dc-mixed-error {
  margin-top: var(--space-4);
  flex-shrink: 0;
}

/* The hue is on the glyph and the border; the text stays `on-surface`, because
   `on-<x>` is only ever correct on a solid `<x>` fill. */
.dc-expansion-state--error {
  border-color: rgb(var(--v-theme-warning));
  color: rgb(var(--v-theme-on-surface));
}

.dc-expansion-state--error .v-icon {
  color: rgb(var(--v-theme-warning));
}

/* The consequence sentence sits with the two buttons that answer it, so the
   cost and the commitment are read in one line. */
.dc-promote {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-top: var(--space-3);
  padding: var(--space-3);
  border: 1px solid rgb(var(--v-theme-border));
  border-radius: var(--radius-md);
  background: rgb(var(--v-theme-surface));
}

.dc-promote-icon {
  color: rgba(var(--v-theme-on-surface), 0.6);
  flex-shrink: 0;
}

.dc-promote-text {
  flex: 1;
  min-width: 0;
  font-size: var(--text-sm);
  line-height: var(--leading-snug);
  color: rgb(var(--v-theme-on-surface));
}

/* ── Why pills ───────────────────────────────────────────────────────────── */
.dc-why {
  margin-top: var(--space-4);
  flex-shrink: 0;
}

.dc-hint {
  margin-right: auto;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

/* ── The blink compare ─────────────────────────────────────────────────────
   Fixed colors on purpose: like the lightbox, a photo-judgement surface gets
   near-black chrome in both themes. Sits above the modal dialog. */
.dc-zv {
  position: fixed;
  inset: 0;
  z-index: calc(var(--z-modal) + 100);
  display: flex;
  flex-direction: column;
  background: #0a0a0a;
}

.dc-zv-top {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  flex-shrink: 0;
}

.dc-zv-flip {
  display: flex;
  gap: var(--space-1);
}

.dc-zv-flip button {
  min-width: 30px;
  height: 30px;
  border: 1px solid rgba(255, 255, 255, 0.24);
  border-radius: var(--radius-sm);
  background: transparent;
  color: #fff;
  font-family: var(--font-ui);
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  cursor: pointer;
}

.dc-zv-flip button:hover {
  background: rgba(255, 255, 255, 0.12);
}

.dc-zv .dc-zv-on {
  background: rgb(var(--v-theme-accent));
  border-color: rgb(var(--v-theme-accent));
  color: rgb(var(--v-theme-on-accent));
  font-weight: var(--weight-semibold);
}

.dc-flag--zv {
  position: static;
}

.dc-zv-meta {
  flex: 1;
  font-size: var(--text-xs);
  color: rgba(255, 255, 255, 0.72);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.dc-zv-mode {
  display: flex;
  gap: var(--space-1);
  flex-shrink: 0;
}

.dc-zv-mode button {
  height: 30px;
  padding: 0 var(--space-3);
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  border: 1px solid rgba(255, 255, 255, 0.24);
  border-radius: var(--radius-sm);
  background: transparent;
  color: #fff;
  font-family: var(--font-ui);
  font-size: var(--text-xs);
  cursor: pointer;
}

.dc-zv-mode button:hover {
  background: rgba(255, 255, 255, 0.12);
}

/* The live magnification, in the photo-tool convention (100% = 1:1). Same
   fixed chrome values as the meta line: this is the near-black judgement
   surface, deliberately un-themed. */
.dc-zv-pct {
  align-self: center;
  min-width: 5ch;
  text-align: right;
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
  color: rgba(255, 255, 255, 0.72);
}

.dc-zv-close {
  width: 30px;
  height: 30px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  color: rgba(255, 255, 255, 0.72);
}

.dc-zv-close:hover {
  background: rgba(255, 255, 255, 0.12);
  color: #fff;
}

/* One continuous zoom surface. `overflow: hidden` is deliberate: the wheel
   ZOOMS (never scrolls) and the drag is the only pan, so no scrollbar may
   appear and no native wheel-scroll may compete; the scroll offsets are
   driven programmatically by the cursor-anchor math. Flex + auto margins on
   the image: centred while it fits, full programmatic scroll range once it
   overflows (auto margins collapse to zero under negative free space). */
.dc-zv-img {
  flex: 1;
  min-height: 0;
  display: flex;
  overflow: hidden;
}

.dc-zv-img--pannable {
  cursor: grab;
}

/* Until the image has measured (its `load` fixes the fit floor), the classic
   fit rendering holds; the measured inline width/height then take over. */
.dc-zv-img img {
  display: block;
  margin: auto;
  flex-shrink: 0;
  width: auto;
  height: 100%;
  max-width: 100%;
  object-fit: contain;
}

.dc-zv-foot {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-5);
  padding: var(--space-3);
  flex-shrink: 0;
  font-size: var(--text-xs);
  color: rgba(255, 255, 255, 0.6);
}

.dc-zv-foot kbd {
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
  background: rgba(255, 255, 255, 0.14);
  color: #fff;
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
}
</style>
