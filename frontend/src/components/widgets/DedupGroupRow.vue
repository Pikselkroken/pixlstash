<template>
  <div
    class="grow"
    :class="{ 'grow--focus': focused, 'grow--selected': selected }"
    role="group"
    :aria-label="`Group ${index + 1}, ${group.candidates.length} pictures`"
    :aria-current="focused ? 'true' : undefined"
    :aria-selected="selected ? 'true' : undefined"
    :data-testid="`dedup-group-${group.signature}`"
    @mousedown="onRowMouseDown"
    @click="emit('focus', $event)"
    @dblclick="onDblClick"
  >
    <div class="ginfo">
      <div class="gn">
        <v-icon v-if="focused" class="gcaret" size="18">mdi-menu-right</v-icon>
        <b>Group {{ index + 1 }}</b>
        <span class="gn-sep" aria-hidden="true">|</span>
        <span>{{ group.candidates.length }} pictures</span>
      </div>
      <DedupConfidencePill :group="group" />
      <!-- The evidence stays on every row; the focused row is already marked
           four ways (bar, caret, wash, filled button) and the kbd chips on the
           verdict buttons say where the keyboard acts (owner call,
           2026-07-29: the explicit label was noise). -->
      <DedupWhyPills :why="group.why" :limit="whyLimit" />
    </div>

    <!-- Thumbnails at grid scale, edge to edge, carrying no metadata: only the
         cover label and the index, and the index only while the row is focused
         because that is the only row `1`-`9` can address. -->
    <div class="gstrip" :style="stripStyle">
      <button
        v-for="(candidate, i) in group.candidates"
        :key="idOf(candidate)"
        type="button"
        class="gthumb"
        :class="{
          'gthumb--cover':
            idOf(candidate) === coverId &&
            !isOut(idOf(candidate)) &&
            !isLockedOut(candidate),
          'gthumb--out': isOut(idOf(candidate)) && !isLockedOut(candidate),
          'gthumb--locked': isLockedOut(candidate),
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
        <!-- The top-left corner is a COLUMN, not a slot: the index and the lock
             chip can both be present (a locked candidate in a focused row) and
             used to be stacked on the same pixels. Same construction as
             ImageGrid's .thumbnail-top-left-badges. The index leads because
             `focused` is a row-level fact (every thumb in the strip shows its
             index, or none does), so leading with it keeps the whole strip's
             indices on one line, which is the only reason the index exists. -->
        <div v-if="focused || isLockedOut(candidate)" class="gtl">
          <span v-if="focused" class="gnum">{{ i + 1 }}</span>
          <!-- The server's exclusion, not the user's, so it gets its own marker:
               the two can appear in the same strip and must never be read as the
               same walk-back-able state. Same chip as ReviewBinaryCard's
               .rs-thumb-lock, so the app has one lock chip. -->
          <span
            v-if="isLockedOut(candidate)"
            class="glock"
            :class="{ 'glock--flash': flashIds.includes(idOf(candidate)) }"
          >
            <v-icon size="12">mdi-lock-outline</v-icon>
          </span>
        </div>
        <span
          v-if="
            idOf(candidate) === coverId &&
            !isOut(idOf(candidate)) &&
            !isLockedOut(candidate)
          "
          class="gcv"
          >Cover</span
        >
        <!-- Hover-only score overlays (owner request): the grid's own
             StarRatingOverlay top-right, the smart score bottom-right, both
             revealed by the grid's exact hover recipe and DISPLAY-ONLY here
             (pointer-events stays off, see the CSS) — the thumbnail keeps
             owning click=cover, right-click=exclude, double-click=compare.
             aria-hidden: the same facts live in Compare's meta grid, which
             is the queue's readable surface for them.

             The top-right corner is a column for the same reason as the
             top-left, and stays one even though the stars are its only member
             today. Grid rule for whoever adds the next one: a PERMANENT badge
             leads, a hover-only one follows. A leading member that is only
             `opacity: 0` is still in flow, so the column itself never moves;
             only what sits beneath it does. -->
        <div v-if="loadThumbnails" class="gtr">
          <span class="gstars" aria-hidden="true">
            <StarRatingOverlay
              :score="Number(candidate.score) || 0"
              :icon-size="14"
              :compact="true"
            />
          </span>
        </div>
        <span
          v-if="loadThumbnails && smartTextOf(candidate)"
          class="gsmart"
          aria-hidden="true"
          :title="`Smart score ${smartTextOf(candidate)}`"
        >
          <v-icon size="12">mdi-brain</v-icon>
          {{ smartTextOf(candidate) }}
        </span>
        <v-icon
          v-if="isOut(idOf(candidate)) && !isLockedOut(candidate)"
          class="gx"
          size="20"
          >mdi-minus-circle-outline</v-icon
        >
      </button>
    </div>

    <div v-if="verdict" class="gact">
      <!-- A decided row states its verdict and offers the one way back.
           Clearing never touches pictures: a reopened "stacked" group stays
           stacked until unstacked from the Stacks view. -->
      <span class="gverdict" :title="decidedTitle">
        <v-icon size="16">{{
          verdict === "stacked" ? "mdi-layers" : "mdi-call-split"
        }}</v-icon>
        {{ verdict === "stacked" ? "Stacked" : "Kept separate" }}
      </span>
      <!-- When the decision was made, in the user's own date format. Older
           rows (or an older backend) serve no decided_at: no cell, no dash. -->
      <span v-if="decidedStamp" class="gdecided-at">{{ decidedStamp }}</span>
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
        <span>{{
          bulk ? `Clear ${selectionCount} decisions` : "Clear decision"
        }}</span>
      </button>
    </div>
    <div v-else class="gact">
      <!-- The two verdict buttons carry what the verdict COSTS, because neither
           one asks for a confirmation: stacking never deletes a file, and
           keeping separate is remembered for good. A user meeting the queue for
           the first time should not have to run one to find that out. -->
      <!-- Amendment #3: S became a SYNONYM of Enter for Stack (the owner's
           S-for-Stack slip is now self-healing), and K took Keep separate.
           The chips stay one key per button — the primary key shown, the
           synonym taught in copy — while aria-keyshortcuts carries the full
           machine-readable set (the chips are aria-hidden, so this is the
           only channel that announces the keys at all). -->
      <button
        type="button"
        class="gbtn gbtn--stack"
        :tabindex="focused ? 0 : -1"
        :disabled="busy || readOnly || noLegalStack"
        aria-keyshortcuts="Enter S"
        :title="
          noLegalStack
            ? lockedStackReason
            : bulk
              ? `Stack every one of the ${selectionCount} selected groups behind its own cover. Every file stays on disk, and one Ctrl+Z reverses them all.`
              : 'Group these behind one cover. Every file stays on disk, and Ctrl+Z reverses it.'
        "
        @click.stop="emit('stack')"
      >
        <v-icon size="16">mdi-layers-plus</v-icon>
        <span>{{
          bulk ? `Stack ${selectionCount} groups` : `Stack ${stackSize}`
        }}</span>
        <kbd v-if="showsVerdictKeys" aria-hidden="true">Enter</kbd>
      </button>
      <button
        type="button"
        class="gbtn"
        :tabindex="focused ? 0 : -1"
        :disabled="busy || readOnly"
        aria-keyshortcuts="K"
        :title="
          bulk
            ? `Leave all ${selectionCount} selected groups as separate pictures. They stay in your library and stop being suggested.`
            : 'Leave these as separate pictures. They stay in your library and stop being suggested.'
        "
        @click.stop="emit('keep-separate')"
      >
        <v-icon size="16">mdi-call-split</v-icon>
        <span>{{
          bulk ? `Keep ${selectionCount} separate` : "Keep separate"
        }}</span>
        <kbd v-if="showsVerdictKeys" aria-hidden="true">K</kbd>
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
import {
  candidateId,
  candidateSmartScore,
  candidateStackable,
  candidateBlockedBySets,
  lockedCandidateIds,
} from "../../utils/dedup";
import { buildLockReason } from "../../stores/useLockedSetsStore";
import { formatUserDate } from "../../utils/utils";
import StarRatingOverlay from "./StarRatingOverlay.vue";
import {
  DEFAULT_THUMBNAIL_SIZE_LEVEL,
  stripHeightForSizeLevel,
} from "../../utils/thumbnailSizes";
import { MIN_STACK_MEMBERS } from "../../stores/useDedupStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";

/** A candidate's picture id. The server calls the field `picture_id`. */
const idOf = candidateId;

/**
 * The widest shape a thumbnail may keep before it is cropped, as a ratio. A
 * ceiling, not a crop: only a beyond-panoramic picture reaches it.
 */
const MAX_THUMB_RATIO = 2.4;

/**
 * Below this height the info column, not the strip, sets the row height, and
 * the second why-pill is what keeps it tall. Dropping to one pill is safe
 * BECAUSE `orderEvidence` puts counter-evidence first: the pill that survives
 * the limit is always the one that argues against stacking.
 */
const ONE_PILL_BELOW_PX = 96;

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
  // How tall the candidate strip draws its pictures, from the queue's size
  // control. The row is laid out from this one number: the box, the placeholder
  // estimate and the panorama ceiling all follow it.
  // `defineProps` is hoisted, so the default is computed from the imported
  // ladder rather than from a local constant.
  thumbHeight: {
    type: Number,
    default: stripHeightForSizeLevel(DEFAULT_THUMBNAIL_SIZE_LEVEL),
  },
  busy: { type: Boolean, default: false },
  readOnly: { type: Boolean, default: false },
  // Picture ids to flash the lock chip on: the sighted counterpart to the
  // announcement when a Stack was refused. The queue sets it and clears it.
  flashIds: { type: Array, default: () => [] },
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

const userPrefsStore = useUserPrefsStore();

/**
 * When the decision was made, in the user's own date format — the same
 * `formatUserDate(iso, dateFormat)` pattern every other timestamp in the app
 * renders through (scrapheap deadlines, picture metadata). `decided_at`
 * arrives as a naive-UTC ISO string per house convention; the util
 * normalises it. Empty when the backend served none.
 */
const decidedStamp = computed(() =>
  props.decidedAt
    ? formatUserDate(props.decidedAt, userPrefsStore.dateFormat)
    : "",
);

/** The verdict label's tooltip, carrying the timestamp when one is known. */
const decidedTitle = computed(() => {
  const what =
    props.verdict === "stacked"
      ? "This group was stacked."
      : "This group was kept separate.";
  return decidedStamp.value ? `${what} Decided ${decidedStamp.value}.` : what;
});

/**
 * The candidates a locked picture set keeps out of the stack.
 *
 * The server's decision, served per candidate on the queue page. It is not a
 * user exclusion and `X` cannot walk it back, which is why it is tracked
 * separately from `excludedIds` all the way down to the marker.
 */
const lockedIds = computed(() => lockedCandidateIds(props.group));

/**
 * Whether a locked set keeps this candidate out.
 * @param {Object} candidate
 * @returns {boolean}
 */
function isLockedOut(candidate) {
  return !candidateStackable(candidate);
}

/** Every id the stack would leave out, the user's and the server's together. */
const outIds = computed(() => {
  const merged = new Set(props.excludedIds);
  for (const id of lockedIds.value) merged.add(id);
  return merged;
});

const stackSize = computed(
  () => props.group.candidates.length - outIds.value.size,
);

/**
 * True when no legal stack exists at all: a locked set leaves fewer than two
 * members that may be stacked together. The row still offers Keep separate,
 * which is a real decision about a real duplicate pair and the only one left.
 */
const noLegalStack = computed(
  () => lockedIds.value.length > 0 && stackSize.value < MIN_STACK_MEMBERS,
);

/** Why Stack is unavailable, naming the sets so the fix is discoverable. */
const lockedStackReason = computed(() => {
  const names = [
    ...new Set(
      (props.group.candidates ?? [])
        .filter((candidate) => !candidateStackable(candidate))
        .flatMap((candidate) =>
          candidateBlockedBySets(candidate).map((entry) => entry.name),
        )
        .filter(Boolean),
    ),
  ];
  const reason = buildLockReason(names);
  return reason
    ? `A stack needs at least two pictures that are not frozen. ${reason} Keep separate still works.`
    : "A stack needs at least two pictures that are not frozen by a locked set. Keep separate still works.";
});

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
 * The strip's measurements as CSS variables, so ONE number drives the box, the
 * unknown-shape fallback and the panorama ceiling together. Sizing the box in
 * CSS and the placeholder in JS from two copies of the height is how a row
 * starts jumping as its images decode.
 */
const stripStyle = computed(() => ({
  "--gthumb-h": `${props.thumbHeight}px`,
  "--gthumb-max-w": `${Math.round(props.thumbHeight * MAX_THUMB_RATIO)}px`,
  // The unknown-shape fallback is a 4:3 box at the strip's height.
  "--gthumb-fallback-w": `${Math.round((props.thumbHeight * 4) / 3)}px`,
}));

/** How many why-pills the info column has room for at this size. */
const whyLimit = computed(() =>
  props.thumbHeight < ONE_PILL_BELOW_PX ? 1 : 2,
);

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
  const ratio = Math.min(MAX_THUMB_RATIO, Math.max(0.45, w / h));
  return { width: `${Math.round(props.thumbHeight * ratio)}px` };
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
  if (isLockedOut(candidate)) {
    // Named, not just "locked": the set is the thing the user has to unlock,
    // and a screen reader gets no tooltip to fall back on.
    const names = candidateBlockedBySets(candidate)
      .map((entry) => entry.name)
      .filter(Boolean);
    parts.push(
      names.length
        ? `frozen by the locked set ${names.join(", ")}, cannot be stacked`
        : "frozen by a locked set, cannot be stacked",
    );
  } else if (isOut(idOf(candidate))) parts.push("not in the stack");
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
  if (isLockedOut(candidate)) {
    // The single-sourced "why is this read-only / how do I unlock" sentence, so
    // the queue never re-words what the grid and the overlay already say.
    const names = candidateBlockedBySets(candidate)
      .map((entry) => entry.name)
      .filter(Boolean);
    const reason = buildLockReason(names);
    return reason
      ? `${reason} It stays out of the stack.`
      : "This picture is in a locked set, so it stays out of the stack.";
  }
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
 * A modified press means "select rows", so the browser's own gesture on the
 * same input — extending a text selection from wherever the caret last was —
 * must not also run. Selection starts on mousedown, before the click handler
 * ever sees the event, so this is the only place it can be refused.
 * @param {MouseEvent} event
 */
function onRowMouseDown(event) {
  if (event.shiftKey || event.ctrlKey || event.metaKey) {
    event.preventDefault();
  }
}

/**
 * A candidate's smart score for the hover chip: the metadata panel's own
 * two-decimal precision, empty when the backend served none (NULL) or the
 * computation failed (-1.0) — no chip either way.
 * @param {Object} candidate
 * @returns {string}
 */
function smartTextOf(candidate) {
  const value = candidateSmartScore(candidate);
  return value === null ? "" : value.toFixed(2);
}

/**
 * Clicking a thumbnail focuses the row and makes that picture the cover.
 * @param {Object} candidate
 */
function onPick(candidate) {
  emit("focus");
  // A picture that is not in the stack cannot lead it. Focusing the row still
  // happens, so the click is not a dead press.
  if (isLockedOut(candidate)) return;
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

/**
 * Double-click means "open this": the same Compare the `C` key and the
 * Compare button reach, from the row surface or a thumbnail.
 *
 * A double-click also delivers its two single clicks first, and that is fine
 * by construction: on the row surface they focus (idempotent), on a thumbnail
 * they pick the same cover twice, and Compare then opens over that state.
 * Two carve-outs keep the gesture from surprising anyone:
 *
 *   * the action buttons (`.gbtn`, `.gcompare`, the Clear on a decided row)
 *     keep their own double-click meaning — a fast double press on Stack is
 *     two Stack clicks, already guarded by `busy`, and must not ALSO open a
 *     dialog over the next group;
 *   * a modified double-click belongs to the selection gestures (Ctrl/Shift
 *     click), which double-fire harmlessly and must not open anything.
 *
 * @param {MouseEvent} event
 */
function onDblClick(event) {
  if (event.ctrlKey || event.metaKey || event.shiftKey) return;
  const el = event.target instanceof Element ? event.target : null;
  if (el && el.closest("button") && !el.closest(".gthumb")) return;
  emit("compare");
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
  gap: var(--space-3);
  min-width: 0;
}

/* Decorative divider between the group number and its member count; the row's
   aria-label already phrases the pair, so this is aria-hidden. */
.gn-sep {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.35);
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

/* The decided row's verdict statement — reads as state, not as a button.
   TEXT-edge aligned with the Clear button below it (owner report: the outer
   borders lined up, the text did not): the label wears the button's exact
   box — a 1px border made transparent, the same horizontal padding, the same
   height and icon gap — so its icon and text columns start precisely where
   the button's do, in both themes. It stays a <span> with no hover, focus or
   cursor treatment, so the invisible border can never read as an affordance. */
.gverdict {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: 27px;
  border: 1px solid transparent;
  padding: 0 var(--space-4);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: rgba(var(--v-theme-on-surface), 0.75);
}

/* The decision's timestamp: the row's own muted-metadata treatment (the
   `.gn span` recipe) with tabular numerals like every timestamp, sharing the
   verdict label's transparent-border inset so all three text edges in the
   column align. */
.gdecided-at {
  border-inline: 1px solid transparent;
  padding: 0 var(--space-4);
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
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
  /* Set by the strip from the queue's size control. */
  height: var(--gthumb-h);
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
   user can walk back, not a deletion.

   The fade lands on the IMAGE, never on the button. Everything else in the box
   (the exclusion tick, the lock chip, the index, the rating) is what EXPLAINS
   the state, and fading the explanation along with the photo is what made the
   lock chip need a flash animation to be noticed at all. Same rule for the
   server's lock below: only the picture dims. */
.gthumb--out .gt,
.gthumb--locked .gt {
  opacity: var(--opacity-disabled);
}

.gt {
  display: block;
  width: auto;
  height: 100%;
  /* A ceiling, not a crop: only a beyond-panoramic shape gets clipped. Scaled
     with the height so the widest allowed shape stays the same 2.4:1. */
  max-width: var(--gthumb-max-w);
  object-fit: cover;
  /* Right-click toggles the exclusion in place, so the dim has to read as a
     change rather than as a different picture appearing. */
  transition: opacity var(--dur-1) var(--ease-standard);
}

/* The unknown-shape fallback: a 4:3 box at the strip's height. */
.gt--placeholder {
  width: var(--gthumb-fallback-w);
  background: rgba(var(--v-theme-on-surface), 0.08);
}

/* The two top corners are badge COLUMNS, mirroring ImageGrid's
   .thumbnail-top-left-badges / .thumbnail-top-right-badges. A corner that is a
   single absolutely-positioned slot only works until it holds two things, which
   is how the index came to be drawn underneath the lock chip. Display-only, like
   every other overlay in this strip: the thumbnail owns the whole gesture
   vocabulary and nothing here may take a press off it. */
.gtl,
.gtr {
  position: absolute;
  top: var(--space-2);
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  pointer-events: none;
}

.gtl {
  left: var(--space-2);
  align-items: flex-start;
}

/* The stars' corner, so the column owns the inset they used to declare. */
.gtr {
  right: var(--space-2);
  align-items: flex-end;
}

.gnum,
.gcv {
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  border-radius: var(--radius-sm);
  padding: 0 var(--space-2);
  background: var(--scrim-photo);
  color: rgb(var(--v-theme-on-dark-surface));
}

/* In the top-left column; the column owns the inset. */
.gnum {
  min-width: var(--badge-size);
  text-align: center;
}

.gcv {
  position: absolute;
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

/* A locked-out candidate is dimmed like a user exclusion (on the image, see
   .gthumb--out above), but it is NOT a choice the user can walk back, so it does
   not take the pointer affordance. Double-click still opens Compare: looking at
   it is always allowed. */
.gthumb--locked {
  cursor: not-allowed;
}

/* The server's exclusion marker. Same chip as ReviewBinaryCard's
   .rs-thumb-lock (one lock chip in the app), on the spacing scale rather than
   that card's 3px one-off, and in the top-left column because .gx owns the
   middle and the two can appear in the same strip. Neutral at rest: the amber
   is spent only on a refused press, or a page of frozen rows becomes a
   warning field and the colour stops meaning anything. */
.glock {
  width: 18px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  background: var(--scrim-photo);
  color: rgb(var(--v-theme-on-dark-surface));
  pointer-events: none;
}

/* The sighted counterpart to the announcement, on the exact thumbnail that
   blocked the press. Same recipe as ReviewDecisionBar's rs-lock-flash. */
.glock--flash {
  animation: g-lock-flash var(--dur-2) var(--ease-standard);
}

@keyframes g-lock-flash {
  50% {
    background: color-mix(
      in srgb,
      rgb(var(--v-theme-warning)) 26%,
      transparent
    );
    color: rgb(var(--v-theme-warning));
  }
}

/* Not a no-op: the refusal still has to be visible, so the animation collapses
   to its own end state rather than disappearing. */
@media (prefers-reduced-motion: reduce) {
  .glock--flash {
    animation: none;
    background: color-mix(
      in srgb,
      rgb(var(--v-theme-warning)) 26%,
      transparent
    );
    color: rgb(var(--v-theme-warning));
  }
}

/* ── Hover-only score overlays ─────────────────────────────────────────────
   The grid's exact reveal recipe (opacity on the overlay, shown on the thumb's
   hover; the grid has no focus-triggered display and neither does this —
   matched deliberately, hover means hover). Display-only: pointer-events stays
   OFF at all times, unlike the grid where hover arms the stars for clicking —
   here the thumbnail owns click=cover, right-click=exclude and
   double-click=compare, and an interactive star would swallow the cover click
   on the very pixels a hover invites. These keep full strength on an excluded
   thumb: the fade is the picture's, not the box's.

   The opacity lives on the OVERLAY, not on the .gtr column, so that one column
   can hold both a hover-only member and a permanent one. */
.gstars,
.gsmart {
  opacity: 0;
  transition: opacity var(--dur-1) var(--ease-standard);
  pointer-events: none;
}

.gthumb:hover .gstars,
.gthumb:hover .gsmart {
  opacity: 1;
}

/* The smart score chip, bottom-right. The grid has NO thumbnail smart-score
   overlay to reuse (it shows the number in the info row under the card), so
   this mirrors the closest shipped recipes instead: this strip's own
   photo-chip treatment (.gnum/.gcv — --scrim-photo fill, --radius-sm,
   --text-2xs on on-dark-surface), the sort menu's mdi-brain iconography and
   the metadata panel's two-decimal precision. */
.gsmart {
  position: absolute;
  bottom: var(--space-2);
  right: var(--space-2);
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
  background: var(--scrim-photo);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
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
  opacity: var(--opacity-disabled);
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
