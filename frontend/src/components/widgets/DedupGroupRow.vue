<template>
  <div
    class="grow"
    :class="{ 'grow--focus': focused, 'grow--selected': selected }"
    role="group"
    :aria-label="`Group ${index + 1}, ${group.candidates.length} pictures`"
    :aria-current="focused ? 'true' : undefined"
    :aria-selected="selected ? 'true' : undefined"
    :data-testid="`dedup-group-${group.signature}`"
    @click="emit('focus', $event)"
  >
    <div class="ginfo">
      <div class="gn">
        <v-icon v-if="focused" class="gcaret" size="18">mdi-menu-right</v-icon>
        <b>Group {{ index + 1 }}</b>
        <span>{{ group.candidates.length }} pictures</span>
      </div>
      <DedupConfidencePill :group="group" />
      <!-- The evidence stays on every row; the focused row is already marked
           four ways (bar, caret, wash, filled button) and the kbd chips on the
           verdict buttons say where the keyboard acts (owner call,
           2026-07-29: the explicit label was noise). -->
      <DedupWhyPills :why="group.why" :limit="2" />
    </div>

    <!-- Thumbnails at grid scale, edge to edge, carrying no metadata: only the
         cover label and the index, and the index only while the row is focused
         because that is the only row `1`-`9` can address. -->
    <div class="gstrip">
      <button
        v-for="(candidate, i) in group.candidates"
        :key="idOf(candidate)"
        type="button"
        class="gthumb"
        :class="{
          'gthumb--cover':
            idOf(candidate) === coverId && !isOut(idOf(candidate)),
          'gthumb--out': isOut(idOf(candidate)),
        }"
        :tabindex="focused ? 0 : -1"
        :aria-pressed="idOf(candidate) === coverId"
        :aria-label="thumbLabel(candidate, i)"
        :title="thumbTitle(candidate, i)"
        @click.stop="onPick(candidate)"
        @contextmenu.prevent.stop="onToggle(candidate)"
      >
        <!-- The IMG sizes its own box: the stored width/height are the raw
             file dimensions and ignore EXIF rotation, so a portrait phone
             shot reports landscape numbers. The browser decodes the rotated
             pixels, so height-fixed + width-auto is the only shape that is
             always true. The placeholder uses the metadata as an estimate;
             the image corrects it on load. -->
        <img
          v-if="loadThumbnails"
          class="gt"
          :src="thumbUrl(candidate)"
          alt=""
          loading="lazy"
          decoding="async"
        />
        <span
          v-else
          class="gt gt--placeholder"
          :style="thumbBoxStyle(candidate)"
          aria-hidden="true"
        ></span>
        <span v-if="focused" class="gnum">{{ i + 1 }}</span>
        <span
          v-if="idOf(candidate) === coverId && !isOut(idOf(candidate))"
          class="gcv"
          >Cover</span
        >
        <v-icon v-if="isOut(idOf(candidate))" class="gx" size="20"
          >mdi-minus-circle-outline</v-icon
        >
      </button>
    </div>

    <div v-if="verdict" class="gact">
      <!-- A decided row states its verdict and offers the one way back.
           Clearing never touches pictures: a reopened "stacked" group stays
           stacked until unstacked from the Stacks view. -->
      <span class="gverdict" :title="decidedTitle">
        <v-icon size="15">{{
          verdict === "stacked" ? "mdi-layers" : "mdi-call-split"
        }}</v-icon>
        {{ verdict === "stacked" ? "Stacked" : "Kept separate" }}
      </span>
      <button
        type="button"
        class="gbtn"
        :disabled="busy || readOnly"
        :title="
          bulk
            ? `Clear the decision on every one of the ${selectionCount} selected groups: they return to the review queue. Stacked pictures stay stacked until you unstack them.`
            : 'Clear this decision: the group returns to the review queue. Stacked pictures stay stacked until you unstack them.'
        "
        @click.stop="emit('clear-decision')"
      >
        <v-icon size="16">mdi-restore</v-icon>
        <span>{{ bulk ? `Clear ${selectionCount} decisions` : "Clear decision" }}</span>
      </button>
    </div>
    <div v-else class="gact">
      <!-- The two verdict buttons carry what the verdict COSTS, because neither
           one asks for a confirmation: stacking never deletes a file, and
           keeping separate is remembered for good. A user meeting the queue for
           the first time should not have to run one to find that out. -->
      <button
        type="button"
        class="gbtn gbtn--stack"
        :tabindex="focused ? 0 : -1"
        :disabled="busy || readOnly"
        :title="
          bulk
            ? `Stack every one of the ${selectionCount} selected groups behind its own cover. Every file stays on disk, and one Ctrl+Z reverses them all.`
            : 'Group these behind one cover. Every file stays on disk, and Ctrl+Z reverses it.'
        "
        @click.stop="emit('stack')"
      >
        <v-icon size="16">mdi-layers-plus</v-icon>
        <span>{{ bulk ? `Stack ${selectionCount} groups` : `Stack ${stackSize}` }}</span>
        <kbd v-if="showsVerdictKeys" aria-hidden="true">Enter</kbd>
      </button>
      <button
        type="button"
        class="gbtn"
        :tabindex="focused ? 0 : -1"
        :disabled="busy || readOnly"
        :title="
          bulk
            ? `Leave all ${selectionCount} selected groups as separate pictures. They stay in your library and stop being suggested.`
            : 'Leave these as separate pictures. They stay in your library and stop being suggested.'
        "
        @click.stop="emit('keep-separate')"
      >
        <v-icon size="16">mdi-call-split</v-icon>
        <span>{{ bulk ? `Keep ${selectionCount} separate` : "Keep separate" }}</span>
        <kbd v-if="showsVerdictKeys" aria-hidden="true">S</kbd>
      </button>
      <button
        type="button"
        class="gcompare"
        :tabindex="focused ? 0 : -1"
        @click.stop="emit('compare')"
      >
        <v-icon size="15">mdi-compare-horizontal</v-icon>
        <span>Compare all {{ group.candidates.length }}</span>
        <kbd v-if="focused" aria-hidden="true">C</kbd>
      </button>
    </div>
  </div>
</template>

<script setup>
// One group in the triage queue.
//
// Exactly one row in the queue is focused, and it says so four ways at once:
// an accent left bar, a caret, a tinted background and a filled Stack button.
// That redundancy is the point. `Up`/`Down` then `Enter`/`S` can never be
// ambiguous about which group they hit, and a user who looks away mid-run can
// find the cursor again without reading anything. (The former "Keyboard acts
// here" label was dropped as noise — owner call, 2026-07-29; the kbd chips on
// the verdict buttons carry that message.)
//
// The row owns no data of its own: covers, exclusions and verdicts all belong
// to the queue, which owns the auto-advance. This component only reports what
// was clicked.
//
// Every control here is a roving tab stop. A screenful of twenty groups holds
// well over a hundred buttons, and a Tab key that walks all of them is a Tab
// key nobody presses twice; only the focused row is reachable that way, which
// is also the only row the keyboard model acts on.
//
// `aria-current` on the row is the only part of the focused treatment that is
// not purely visual. Without it the five CSS signals say nothing at all to a
// screen reader, and "which group does Enter hit" becomes exactly the ambiguity
// the treatment exists to remove.

import { computed } from "vue";
import DedupConfidencePill from "./DedupConfidencePill.vue";
import DedupWhyPills from "./DedupWhyPills.vue";
import { pictureThumbnailUrl } from "../../api/pictures";
import { API_BASE_URL } from "../../utils/apiClient";
import { candidateId } from "../../utils/dedup";
import { MIN_STACK_MEMBERS } from "../../stores/useDedupStore";

/** A candidate's picture id. The server calls the field `picture_id`. */
const idOf = candidateId;

const props = defineProps({
  group: { type: Object, required: true },
  index: { type: Number, default: 0 },
  focused: { type: Boolean, default: false },
  // Part of a Ctrl/Shift-click multi-selection. While the selection holds two
  // or more groups, the verdict buttons rename themselves to say they act on
  // ALL of them — a bulk action must never look like a single one.
  selected: { type: Boolean, default: false },
  selectionCount: { type: Number, default: 0 },
  // True when Enter/S would genuinely take the whole selection (the focused
  // row is inside it). Every selected row then wears the Enter/S chips;
  // Compare's C stays on the focused row alone, since it opens one group.
  bulkKeys: { type: Boolean, default: false },
  // "stacked" | "keep_separate" on the decided page; empty on the open queue.
  // A decided row swaps its verdict buttons for the verdict and a Clear.
  verdict: { type: String, default: "" },
  decidedAt: { type: String, default: "" },
  coverId: { type: [Number, String], default: null },
  excludedIds: { type: Array, default: () => [] },
  // False for a row outside the read-ahead window: the thumbnails are the
  // expensive half of a row, so an off-screen group holds a placeholder box of
  // the same size rather than a decoded image.
  loadThumbnails: { type: Boolean, default: true },
  busy: { type: Boolean, default: false },
  readOnly: { type: Boolean, default: false },
});

const emit = defineEmits([
  "focus",
  "stack",
  "keep-separate",
  "compare",
  "set-cover",
  "toggle-excluded",
  "clear-decision",
]);

/** The verdict label's tooltip, carrying the timestamp when one is known. */
const decidedTitle = computed(() => {
  const what =
    props.verdict === "stacked"
      ? "This group was stacked."
      : "This group was kept separate.";
  return props.decidedAt ? `${what} Decided ${props.decidedAt}.` : what;
});

const stackSize = computed(
  () => props.group.candidates.length - props.excludedIds.length,
);

/** Whether a verdict from this row would act on the whole selection. */
const bulk = computed(() => props.selected && props.selectionCount > 1);

/** Enter/S chips: the focused row always; every selected row while the bulk
 * gesture is live, because the keys genuinely act on all of them. */
const showsVerdictKeys = computed(
  () => props.focused || (props.selected && props.bulkKeys),
);

/**
 * Whether one more exclusion would drop this group below the stack floor.
 *
 * The store refuses that exclusion, because the server refuses a one-member
 * stack. The row has to say so before the gesture rather than after it, or the
 * tooltip is promising an action that will not happen.
 */
const atStackFloor = computed(() => stackSize.value <= MIN_STACK_MEMBERS);

/**
 * Whether a candidate is currently left out of the stack.
 * @param {number|string} id
 * @returns {boolean}
 */
function isOut(id) {
  return props.excludedIds.includes(id);
}

/**
 * The thumbnail URL for one candidate.
 * @param {Object} candidate
 * @returns {string}
 */
function thumbUrl(candidate) {
  // baseUrl is load-bearing: the SPA and the backend are different origins in
  // the dev server, the demo and Electron, so a relative /pictures/... 404s.
  return pictureThumbnailUrl(idOf(candidate), {
    version: candidate.thumbnail_version,
    baseUrl: API_BASE_URL,
  });
}

/**
 * How tall every strip thumbnail is; widths follow each picture's shape.
 *
 * Must stay in step with `.gthumb { height }` below — the placeholder is sized
 * from here and the box from there, and a mismatch makes every row jump as the
 * images decode.
 */
const THUMB_HEIGHT_PX = 112;

/**
 * The PLACEHOLDER's estimated shape, from stored dimensions. Only an
 * estimate: stored width/height ignore EXIF rotation, so the real image may
 * arrive with the axes swapped — it then sizes the box itself.
 *
 * @param {Object} candidate
 * @returns {Object|null} an inline width, or null when the shape is unknown.
 */
function thumbBoxStyle(candidate) {
  const w = Number(candidate.width);
  const h = Number(candidate.height);
  if (!w || !h) return null;
  const ratio = Math.min(2.4, Math.max(0.45, w / h));
  return { width: `${Math.round(THUMB_HEIGHT_PX * ratio)}px` };
}

/**
 * What a thumbnail button is called.
 *
 * The image itself is decorative here (the row deliberately carries no
 * metadata), so without this every candidate reaches a screen reader as the
 * same unlabelled control repeated N times.
 *
 * @param {Object} candidate
 * @param {number} i - the candidate's zero-based position.
 * @returns {string}
 */
function thumbLabel(candidate, i) {
  const parts = [`Picture ${i + 1} of ${props.group.candidates.length}`];
  if (isOut(idOf(candidate))) parts.push("not in the stack");
  else if (idOf(candidate) === props.coverId) parts.push("cover");
  return parts.join(", ");
}

/**
 * The tooltip for a thumbnail, naming the key as well as the mouse gesture.
 *
 * Only the focused row answers to `1`-`9` and `X`, so only the focused row
 * claims they work.
 *
 * @param {Object} candidate
 * @param {number} i
 * @returns {string}
 */
function thumbTitle(candidate, i) {
  if (isOut(idOf(candidate))) {
    return props.focused
      ? "Right-click, or press X, to put this picture back in the stack"
      : "Right-click to put this picture back in the stack";
  }
  // At the floor the exclusion gesture is refused, so it must not be offered.
  if (atStackFloor.value) {
    return props.focused
      ? `Click, or press ${i + 1}, to make this the cover. A stack needs at least two pictures, so this one cannot be left out.`
      : "Click to make this the cover. A stack needs at least two pictures, so this one cannot be left out.";
  }
  return props.focused
    ? `Click, or press ${i + 1}, to make this the cover. Right-click, or press X, to leave it out of the stack.`
    : "Click to make this the cover, right-click to leave it out";
}

/**
 * Clicking a thumbnail focuses the row and makes that picture the cover.
 * @param {Object} candidate
 */
function onPick(candidate) {
  emit("focus");
  emit("set-cover", idOf(candidate));
}

/**
 * Right-clicking a thumbnail focuses the row and toggles the exclusion.
 * @param {Object} candidate
 */
function onToggle(candidate) {
  emit("focus");
  emit("toggle-excluded", idOf(candidate));
}
</script>

<style scoped>
.grow {
  position: relative;
  display: grid;
  /* Three columns — info | pictures | verdicts — per the owner's layout call
     (2026-07-29): the row reads left to right as "what this is, what's in it,
     what to do about it". minmax(0, 1fr) on the middle is what makes the
     picture strip scroll horizontally INSIDE its cell (one scrollbar per row)
     instead of blowing the row wide, and no column ever wraps under another. */
  grid-template-columns: minmax(150px, 190px) minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--space-3) var(--space-5);
  /* Tight vertically, comfortable horizontally. The row's height is spent on
     the pictures — the one thing in it the user actually has to look at — so
     the vertical padding is the smallest step that still reads as a card
     (owner call, 2026-07-29: the previous --space-4 was padding the strip out
     of the room it needed). */
  padding: var(--space-3) var(--space-4);
  padding-left: var(--space-5);
  border-radius: var(--radius-md);
  border: 1px solid rgb(var(--v-theme-divider));
  background: rgb(var(--v-theme-surface));
  cursor: pointer;
  transition:
    background var(--dur-1) var(--ease-standard),
    border-color var(--dur-1) var(--ease-standard);
}

.grow:hover {
  background: var(--hover-wash);
}

/* The focused row's five simultaneous signals. The left bar is a pseudo-element
   so it cannot shift the row's layout when the focus moves. */
.grow--focus {
  background: var(--active-wash);
  border-color: rgba(var(--v-theme-accent), 0.4);
}

.grow--focus::before {
  content: "";
  position: absolute;
  inset: 0 auto 0 0;
  width: 3px;
  border-radius: var(--radius-md) 0 0 var(--radius-md);
  background: rgb(var(--v-theme-accent));
}

/* Part of a multi-selection: the same accent family as the focus treatment,
   one step quieter — no left bar, that stays the keyboard cursor's. */
.grow--selected {
  border-color: rgba(var(--v-theme-accent), 0.55);
  background: var(--hover-wash);
}

.grow--selected.grow--focus {
  background: var(--active-wash);
}

/* The info column stacks its facts top-to-bottom and never wraps sideways. */
.ginfo {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-2);
  min-width: 0;
}

.gcaret {
  color: rgb(var(--v-theme-accent));
  flex-shrink: 0;
  align-self: center;
  margin-left: calc(-1 * var(--space-2));
}

.gn {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  min-width: 0;
}

.gn b {
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
  color: rgb(var(--v-theme-on-surface));
}

.gn span {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

/* The decided row's verdict statement — reads as state, not as a button. */
.gverdict {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: rgba(var(--v-theme-on-surface), 0.75);
}

.gstrip {
  display: flex;
  gap: var(--space-2);
  overflow-x: auto;
  scrollbar-width: thin;
  scrollbar-gutter: stable;
  padding-bottom: var(--space-1);
}

.gthumb {
  position: relative;
  flex: 0 0 auto;
  /* Width comes from the child: the decoded image (always the true, EXIF-
     corrected shape) or the placeholder's metadata estimate. */
  width: auto;
  min-width: 44px;
  /* Keep in step with THUMB_HEIGHT_PX in the script above. */
  height: 112px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-on-surface), 0.06);
  overflow: hidden;
  cursor: pointer;
}

.gthumb:focus-visible {
  box-shadow: var(--focus-ring);
  outline: none;
}

.gthumb--cover {
  border-color: rgb(var(--v-theme-accent));
}

/* An excluded candidate stays visible and stays clickable: it is a choice the
   user can walk back, not a deletion. */
.gthumb--out {
  opacity: 0.4;
}

.gt {
  display: block;
  width: auto;
  height: 100%;
  /* A ceiling, not a crop: only a beyond-panoramic shape gets clipped. Scaled
     with the height so the widest allowed shape stays the same 2.4:1. */
  max-width: 268px;
  object-fit: cover;
}

/* The unknown-shape fallback: a 4:3 box at the strip's height. */
.gt--placeholder {
  width: 150px;
  background: rgba(var(--v-theme-on-surface), 0.08);
}

.gnum,
.gcv {
  position: absolute;
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  border-radius: var(--radius-sm);
  padding: 0 var(--space-2);
  background: var(--scrim-photo);
  color: rgb(var(--v-theme-on-dark-surface));
}

.gnum {
  top: var(--space-2);
  left: var(--space-2);
  min-width: var(--badge-size);
  text-align: center;
}

.gcv {
  bottom: var(--space-2);
  left: var(--space-2);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
}

.gx {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  color: rgb(var(--v-theme-on-dark-surface));
}

/* The verdict column: one action per line, never wrapping under the strip. */
.gact {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: var(--space-2);
}

.gbtn,
.gcompare {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: 27px;
  padding: 0 var(--space-4);
  border-radius: var(--radius-md);
  border: 1px solid rgb(var(--v-theme-border));
  background: transparent;
  color: rgb(var(--v-theme-on-surface));
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  cursor: pointer;
  transition:
    background var(--dur-1) var(--ease-standard),
    border-color var(--dur-1) var(--ease-standard);
}

.gbtn:hover:not(:disabled),
.gcompare:hover {
  background: var(--hover-wash);
}

.gbtn:focus-visible,
.gcompare:focus-visible {
  box-shadow: var(--focus-ring);
  outline: none;
}

.gbtn:disabled {
  opacity: 0.38;
  cursor: default;
}

/* The primary verdict fills only on the focused row, so the eye lands on the
   one button `Enter` would press. */
.grow--focus .gbtn--stack {
  background: rgb(var(--v-theme-accent));
  border-color: rgb(var(--v-theme-accent));
  color: rgb(var(--v-theme-on-accent));
}

.gcompare {
  border-color: transparent;
  color: rgba(var(--v-theme-on-surface), 0.75);
}

kbd {
  font-family: var(--font-mono);
  font-size: var(--text-2xs);
  line-height: var(--leading-snug);
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
  border: 1px solid currentColor;
  opacity: 0.7;
}
</style>
