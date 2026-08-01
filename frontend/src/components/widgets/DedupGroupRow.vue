<template>
  <div
    class="grow"
    :class="{ 'grow--focus': focused, 'grow--selected': selected }"
    role="group"
    :aria-label="`Group ${index + 1}, ${composition}`"
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
        <!-- The COMPOSITION, not a picture count: `Stack of 5 + 1 picture`.
             The size never leaves the header, because the verdict button is
             allowed to shed it under width pressure and this is then the only
             place the group's true depth is stated. -->
        <span>{{ composition }}</span>
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
      <!-- One tile per UNIT, not per picture. A deck occupies one slot however
           many of its members the group named, because a stack verdict moves
           the whole stack: a strip that drew them apart was offering per-picture
           gestures the backend folds back together. -->
      <div v-for="(unit, i) in units" :key="unit.key" class="gunit">
        <!-- Behind the tile and OUTSIDE it: `.gthumb` clips its overflow, and
             the deck's peeking edges are drawn past the tile's own box. -->
        <StackEdgeTicks v-if="unit.kind === 'deck'" :count="unit.depth" />
        <button
          type="button"
          class="gthumb"
          :class="{
            'gthumb--cover':
              isCover(unit) && !isOut(unit) && !isLockedOut(unit),
            'gthumb--out': isOut(unit) && !isLockedOut(unit),
            'gthumb--locked': isLockedOut(unit),
          }"
          :tabindex="focused ? 0 : -1"
          :aria-pressed="isCover(unit)"
          :aria-label="thumbLabel(unit, i)"
          :title="thumbTitle(unit, i)"
          @click.stop="onPick(unit)"
          @contextmenu.prevent.stop="onToggle(unit)"
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
            :src="thumbUrl(unit)"
            alt=""
            loading="lazy"
            decoding="async"
          />
          <span
            v-else
            class="gt gt--placeholder"
            :style="thumbBoxStyle(unit)"
            aria-hidden="true"
          ></span>
          <!-- The top-left corner is a COLUMN, not a slot: the index and the
               lock chip can both be present (a locked unit in a focused row)
               and used to be stacked on the same pixels. Same construction as
               ImageGrid's .thumbnail-top-left-badges. The index leads because
               `focused` is a row-level fact (every tile in the strip shows its
               index, or none does), so leading with it keeps the whole strip's
               indices on one line, which is the only reason the index
               exists. -->
          <div v-if="focused || isLockedOut(unit)" class="gtl">
            <span v-if="focused" class="gnum">{{ i + 1 }}</span>
            <!-- The server's exclusion, not the user's, so it gets its own
                 marker: the two can appear in the same strip and must never be
                 read as the same walk-back-able state. Same chip as
                 ReviewBinaryCard's .rs-thumb-lock, so the app has one lock
                 chip. -->
            <span
              v-if="isLockedOut(unit)"
              class="glock"
              :class="{ 'glock--flash': isFlashing(unit) }"
            >
              <v-icon size="12">mdi-lock-outline</v-icon>
            </span>
          </div>
          <span
            v-if="isCover(unit) && !isOut(unit) && !isLockedOut(unit)"
            class="gcv"
            >Cover</span
          >
          <!-- The smart score, bottom-right and hover-only. Drawn from the
               unit's FACE, so a deck shows it only when its leader is one of
               the group's candidates: the alternative is labelling the leader's
               picture with a matched member's number, which is exactly the
               mismatch the deck exists to remove. -->
          <span
            v-if="loadThumbnails && smartTextOf(unit)"
            class="gsmart"
            aria-hidden="true"
            :title="`Smart score ${smartTextOf(unit)}`"
          >
            <v-icon size="12">mdi-brain</v-icon>
            {{ smartTextOf(unit) }}
          </span>
          <v-icon v-if="isOut(unit) && !isLockedOut(unit)" class="gx" size="20"
            >mdi-minus-circle-outline</v-icon
          >
        </button>
        <!-- The top-right badge column is an absolutely-positioned SIBLING of
             the tile, not a child: `StackBadge` is a <button> and `.gthumb` is
             a <button>, and a button inside a button is invalid markup. Same
             construction as `.dc-zoom` in DedupCompareDialog.

             Grid rule: a PERMANENT badge leads, a hover-only one follows. A
             leading member that is only `opacity: 0` is still in flow, so the
             column itself never moves; only what sits beneath it does. -->
        <div v-if="unit.kind === 'deck' || loadThumbnails" class="gtr">
          <!-- The badge is the EXPANSION trigger (D4). It used to repeat the
               tile's own set-cover gesture, which made it a second control
               doing the first one's job; the count is the natural handle for
               "show me what is in there". -->
          <StackBadge
            v-if="unit.kind === 'deck'"
            :count="unit.depth"
            :tabindex="focused ? 0 : -1"
            :expanded="isExpanded(unit)"
            :action-title="expandTitle(unit)"
            @activate="onExpand(unit)"
          />
          <!-- Hover-only, DISPLAY-ONLY (pointer-events stays off, see the CSS):
               the tile keeps owning click=cover, right-click=exclude,
               double-click=compare. aria-hidden, because the same facts live in
               Compare's meta grid, the queue's readable surface for them. -->
          <span
            v-if="loadThumbnails && unit.face"
            class="gstars"
            aria-hidden="true"
          >
            <StarRatingOverlay
              :score="Number(unit.face.score) || 0"
              :icon-size="14"
              :compact="true"
            />
          </span>
        </div>
      </div>
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
        <!-- The button NAMES ITS OUTCOME (`Stack 3` / `Add 1 to stack of 4` /
             `Merge 2 stacks`), because expansion is opt-in: a user working at
             speed with Enter never opens one, so this is the last text before
             committing. Under width pressure it sheds the size and then the
             destination, in CSS both ways: the fold is a container query, not
             a measurement, exactly as the toolbar's overflow works. The
             degrading classes are applied ONLY when there is something to shed,
             so a label with one form is never hidden with nothing to replace
             it. -->
        <span v-if="bulk">Stack {{ selectionCount }} groups</span>
        <template v-else>
          <span :class="verdictLabel.degrades ? 'gsl gsl--full' : null">{{
            verdictLabel.full
          }}</span>
          <template v-if="verdictLabel.degrades">
            <span class="gsl gsl--mid">{{ verdictLabel.mid }}</span>
            <span class="gsl gsl--short">{{ verdictLabel.short }}</span>
          </template>
        </template>
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
        <span>Compare all {{ units.length }}</span>
        <kbd v-if="focused" aria-hidden="true">C</kbd>
      </button>
    </div>

    <!-- ── The expansion: what is inside a deck (D4) ──────────────────────
         A full-width band BELOW the row's three columns, never inline in
         `.gstrip`: that strip is already an `overflow-x` scroller, and
         nesting a second horizontal scroller on the same axis is ambiguous
         on a trackpad and on touch. Exploding a deck into the strip would
         also destroy the unit reading the row exists to create.

         The queue owns the "at most one open, on the focused row" rule,
         because its scroll spacers are sized from a single uniform row
         pitch and a variable-height row breaks that arithmetic.

         READ-ONLY here, deliberately: `StackExpansionStrip` can emit
         `unstack` and `set-cover`, and both would rewrite the user's
         library from inside a panel they opened in order to LOOK.
         Promotion lives in Compare, where the consequence sentence has
         room to be read. -->
    <div
      v-if="expandedUnit"
      class="gexp"
      data-testid="dedup-row-expansion"
      @click.stop
    >
      <div v-if="expansionLoading" class="gexp-state" role="status">
        <v-icon size="16" class="mdi-spin">mdi-loading</v-icon>
        Reading the pictures in this stack
      </div>
      <!-- The verdict is still live while the read fails: the band is
           disclosure, and a failure to disclose must not read as a failure
           to decide. -->
      <div
        v-else-if="expansionFailed"
        class="gexp-state gexp-state--error"
        role="alert"
      >
        <v-icon size="16">mdi-alert-outline</v-icon>
        <span
          >Could not read the pictures in this stack. The verdict buttons still
          work.</span
        >
        <AppButton
          variant="ghost"
          size="sm"
          :tabindex="focused ? 0 : -1"
          @click.stop="emit('retry-expansion')"
          >Try again</AppButton
        >
      </div>
      <!-- The row's own height-driven recipe, not the strip's 128x96
           default: the queue runs a 112-406px size slider, and a band that
           ignored it would contradict the tiles directly above it. Height
           only: the width follows the decoded image, because stored
           dimensions ignore EXIF rotation. -->
      <StackExpansionStrip
        v-else
        :count="expandedUnit.depth"
        :members="expansionMembers"
        :cover-id="expansionCoverId"
        :reason="expansionReason"
        :thumb-height="thumbHeight"
        read-only
        :show-unstack="false"
      />
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
import AppButton from "./AppButton.vue";
import DedupConfidencePill from "./DedupConfidencePill.vue";
import DedupWhyPills from "./DedupWhyPills.vue";
import { pictureThumbnailUrl } from "../../api/pictures";
import { API_BASE_URL } from "../../utils/apiClient";
import {
  candidateSmartScore,
  groupUnits,
  isUnitExcluded,
  includedUnits,
  unitForPictureId,
  unitCompositionLabel,
  stackVerdictLabel,
} from "../../utils/dedup";
import { buildLockReason } from "../../stores/useLockedSetsStore";
import { formatUserDate } from "../../utils/utils";
import StarRatingOverlay from "./StarRatingOverlay.vue";
import StackBadge from "./StackBadge.vue";
import StackEdgeTicks from "./StackEdgeTicks.vue";
import StackExpansionStrip from "./StackExpansionStrip.vue";
import {
  DEFAULT_THUMBNAIL_SIZE_LEVEL,
  stripHeightForSizeLevel,
} from "../../utils/thumbnailSizes";
import { MIN_STACK_MEMBERS } from "../../stores/useDedupStore";
import { useUserPrefsStore } from "../../stores/useUserPrefsStore";

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
  // ── The expansion band (D4) ────────────────────────────────────────────
  // The stack whose members are showing under this row, or null. The QUEUE
  // owns this state, not the row: at most one expansion exists in the whole
  // queue and it lives on the focused row, because `DuplicateQueue` sizes
  // both scroll spacers from a single uniform row pitch and a second
  // variable-height row breaks that arithmetic.
  expandedStackId: { type: [Number, String], default: null },
  // `[{ id, thumbnail_version }]` in stack order, fetched lazily by the queue.
  expansionMembers: { type: Array, default: () => [] },
  expansionLoading: { type: Boolean, default: false },
  expansionFailed: { type: Boolean, default: false },
});

const emit = defineEmits([
  "focus",
  "stack",
  "keep-separate",
  "compare",
  "set-cover",
  "toggle-excluded",
  "clear-decision",
  "toggle-expansion",
  "retry-expansion",
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
 * The row's UNITS: the things a stack verdict can move independently.
 *
 * Every loop, count, index and label below is over these rather than over
 * `group.candidates`, which is what makes the thing on screen the thing the
 * backend moves.
 */
const units = computed(() => groupUnits(props.group));

/** The group's composition, for the header and the row's accessible name. */
const composition = computed(() => unitCompositionLabel(units.value));

/** The units a locked picture set keeps out of the stack. */
const lockedUnits = computed(() =>
  units.value.filter((unit) => !unit.stackable),
);

/** The units the Stack button would collect. */
const includedUnitList = computed(() =>
  includedUnits(units.value, props.excludedIds),
);

/** What the Stack button is about to do, at three widths. */
const verdictLabel = computed(() => stackVerdictLabel(includedUnitList.value));

/**
 * Whether a locked set keeps this unit out.
 *
 * Unit-level, because a locked set freezes a whole stack: one frozen member
 * blocks its entire deck, including siblings the group never named.
 *
 * @param {Object} unit
 * @returns {boolean}
 */
function isLockedOut(unit) {
  return !unit.stackable;
}

/**
 * True when no legal stack exists at all: a locked set leaves fewer than two
 * units that may be stacked together. The row still offers Keep separate,
 * which is a real decision about a real duplicate pair and the only one left.
 */
const noLegalStack = computed(
  () =>
    lockedUnits.value.length > 0 &&
    includedUnitList.value.length < MIN_STACK_MEMBERS,
);

/** Why Stack is unavailable, naming the sets so the fix is discoverable. */
const lockedStackReason = computed(() => {
  const names = [
    ...new Set(
      lockedUnits.value
        .flatMap((unit) => unit.blockedBySets.map((entry) => entry.name))
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
const atStackFloor = computed(
  () => includedUnitList.value.length <= MIN_STACK_MEMBERS,
);

/**
 * Whether a unit is currently left out of the stack.
 * @param {Object} unit
 * @returns {boolean}
 */
function isOut(unit) {
  return isUnitExcluded(unit, props.excludedIds);
}

/**
 * Whether a unit is the group's cover.
 *
 * A deck answers to its leader as well as to its matched members, because the
 * cover choice resolves to the leader and the leader is frequently not in the
 * group at all.
 *
 * @param {Object} unit
 * @returns {boolean}
 */
function isCover(unit) {
  return unitForPictureId([unit], props.coverId) === unit;
}

/**
 * Whether a refused Stack named a picture of this unit, for the lock flash.
 * @param {Object} unit
 * @returns {boolean}
 */
function isFlashing(unit) {
  return unit.pictureIds.some((id) => props.flashIds.includes(id));
}

/**
 * The thumbnail URL for one unit's face.
 *
 * A deck's face is its stack's LEADER, which the payload names and versions
 * separately precisely because it is usually not one of the group's candidates.
 *
 * @param {Object} unit
 * @returns {string}
 */
function thumbUrl(unit) {
  // baseUrl is load-bearing: the SPA and the backend are different origins in
  // the dev server, the demo and Electron, so a relative /pictures/... 404s.
  return pictureThumbnailUrl(unit.coverPictureId, {
    version: unit.thumbnailVersion,
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
 * A deck whose leader is not a group candidate has no dimensions to estimate
 * from and falls through to the strip's 4:3 box, exactly as an unknown shape
 * does.
 *
 * @param {Object} unit
 * @returns {Object|null} an inline width, or null when the shape is unknown.
 */
function thumbBoxStyle(unit) {
  const w = Number(unit.face?.width);
  const h = Number(unit.face?.height);
  if (!w || !h) return null;
  const ratio = Math.min(MAX_THUMB_RATIO, Math.max(0.45, w / h));
  return { width: `${Math.round(props.thumbHeight * ratio)}px` };
}

/**
 * The locked sets freezing a unit, by name.
 * @param {Object} unit
 * @returns {Array<string>}
 */
function lockNamesOf(unit) {
  return [
    ...new Set(unit.blockedBySets.map((entry) => entry.name).filter(Boolean)),
  ];
}

/**
 * What a tile is called.
 *
 * The image itself is decorative here (the row deliberately carries no
 * metadata), so without this every tile reaches a screen reader as the same
 * unlabelled control repeated N times.
 *
 * **A deck's name states the stack's true size**, and how many of it the group
 * actually matched when those differ. On a real library one stack-touching
 * group in three names only ONE member of a stack, so the tile shows a picture
 * that stands for four; that sentence is the whole disclosure until the count
 * badge is pressed, and there is no visual substitute for it; the corner has
 * no budget for a second numeral (the spec's dropped "1 of 4 matched" marker).
 *
 * @param {Object} unit
 * @param {number} i - the unit's zero-based position, which is what `1`-`9`
 *   addresses.
 * @returns {string}
 */
function thumbLabel(unit, i) {
  const position = `${i + 1} of ${units.value.length}`;
  const parts =
    unit.kind === "deck"
      ? [
          `Item ${position}`,
          unit.matchedCount < unit.depth
            ? `a stack of ${unit.depth} pictures, ${unit.matchedCount} of them matched`
            : `a stack of ${unit.depth} pictures`,
        ]
      : [`Picture ${position}`];
  if (isLockedOut(unit)) {
    // Named, not just "locked": the set is the thing the user has to unlock,
    // and a screen reader gets no tooltip to fall back on.
    const names = lockNamesOf(unit);
    parts.push(
      names.length
        ? `frozen by the locked set ${names.join(", ")}, cannot be stacked`
        : "frozen by a locked set, cannot be stacked",
    );
  } else if (isOut(unit)) parts.push("not in the stack");
  else if (isCover(unit)) parts.push("cover");
  return parts.join(", ");
}

/**
 * The tooltip for a tile, naming the key as well as the mouse gesture.
 *
 * Only the focused row answers to `1`-`9` and `X`, so only the focused row
 * claims they work.
 *
 * @param {Object} unit
 * @param {number} i
 * @returns {string}
 */
function thumbTitle(unit, i) {
  const noun = unit.kind === "deck" ? "stack" : "picture";
  if (isLockedOut(unit)) {
    // The single-sourced "why is this read-only / how do I unlock" sentence, so
    // the queue never re-words what the grid and the overlay already say.
    const reason = buildLockReason(lockNamesOf(unit));
    return reason
      ? `${reason} It stays out of the stack.`
      : `This ${noun} is in a locked set, so it stays out of the stack.`;
  }
  if (isOut(unit)) {
    return props.focused
      ? `Right-click, or press X, to put this ${noun} back in the stack`
      : `Right-click to put this ${noun} back in the stack`;
  }
  const cover =
    unit.kind === "deck"
      ? "to keep this stack's cover"
      : "to make this the cover";
  // At the floor the exclusion gesture is refused, so it must not be offered.
  if (atStackFloor.value) {
    const floor =
      "A stack needs at least two items in this row, so this one cannot be left out.";
    return props.focused
      ? `Click, or press ${i + 1}, ${cover}. ${floor}`
      : `Click ${cover}. ${floor}`;
  }
  return props.focused
    ? `Click, or press ${i + 1}, ${cover}. Right-click, or press X, to leave this ${noun} out.`
    : `Click ${cover}, right-click to leave it out`;
}

// ── The expansion band ─────────────────────────────────────────────────────
// Disclosure, not a mode: opening one changes nothing else about the row. The
// verdicts stay live, the other units keep their numbers, their cover and their
// exclusion state, and an `Enter` pressed straight after opening does exactly
// what it would have done anyway.

/** The deck whose members are on screen, or null. */
const expandedUnit = computed(() => {
  if (props.expandedStackId === null || props.expandedStackId === undefined) {
    return null;
  }
  return (
    units.value.find(
      (unit) =>
        unit.kind === "deck" &&
        String(unit.stackId) === String(props.expandedStackId),
    ) ?? null
  );
});

/** What the band's header says about the group's reach into this stack. */
const expansionReason = computed(() => {
  const unit = expandedUnit.value;
  if (!unit) return "";
  return unit.matchedCount === 1
    ? "1 of them is in this group"
    : `${unit.matchedCount} of them are in this group`;
});

/**
 * Which member the strip flags as the cover: the group's own cover when it is
 * one of these pictures, the stack's leader otherwise. A promotion made in
 * Compare therefore still reads as the cover here.
 */
const expansionCoverId = computed(() => {
  const unit = expandedUnit.value;
  if (!unit) return null;
  if (props.coverId != null) {
    const ids = props.expansionMembers.map((member) => member.id);
    if (ids.includes(props.coverId)) return props.coverId;
  }
  return unit.coverPictureId;
});

/**
 * Whether this unit's members are the ones on screen.
 * @param {Object} unit
 * @returns {boolean}
 */
function isExpanded(unit) {
  return expandedUnit.value === unit;
}

/**
 * What the count badge promises, and where it puts it.
 *
 * Only the focused row answers to `E`, so only the focused row claims it works.
 *
 * @param {Object} unit
 * @returns {string}
 */
function expandTitle(unit) {
  if (isExpanded(unit)) {
    return props.focused
      ? "Hide the pictures in this stack, or press E"
      : "Hide the pictures in this stack";
  }
  const what = `Show the ${unit.depth} pictures in this stack, below the row`;
  return props.focused ? `${what}, or press E` : what;
}

/**
 * The badge opens the deck in place.
 *
 * It focuses the row first, because the expansion may only live on the focused
 * row: pressing a badge on another row moves the cursor there rather than
 * leaving two rows disagreeing about which one the keyboard acts on.
 *
 * @param {Object} unit
 */
function onExpand(unit) {
  emit("focus");
  emit("toggle-expansion", unit.stackId);
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
 * A unit's smart score for the hover chip: the metadata panel's own two-decimal
 * precision, empty when the backend served none (NULL), when the computation
 * failed (-1.0), or when the unit is a deck whose leader is not in the group,
 * no chip in any of those cases.
 * @param {Object} unit
 * @returns {string}
 */
function smartTextOf(unit) {
  const value = unit.face ? candidateSmartScore(unit.face) : null;
  return value === null ? "" : value.toFixed(2);
}

/**
 * Clicking a tile focuses the row and makes that unit the cover.
 *
 * On a deck the cover resolves to the stack's LEADER, the picture the tile is
 * already showing: because that is the only picture the server can lead the
 * resulting stack with, and picking a matched member instead would re-curate a
 * stack the user already made.
 *
 * The tile alone: the deck's count badge is the expansion trigger (D4), not a
 * second way to press the tile it sits on.
 *
 * @param {Object} unit
 */
function onPick(unit) {
  emit("focus");
  // A unit that is not in the stack cannot lead it. Focusing the row still
  // happens, so the click is not a dead press.
  if (isLockedOut(unit)) return;
  emit("set-cover", unit.coverPictureId);
}

/**
 * Right-clicking a tile focuses the row and toggles the WHOLE unit's exclusion.
 *
 * The store resolves the picture to its unit and takes the deck out entire:
 * per-picture exclusion was a silent no-op, because the backend folds every
 * member of any stack the group touches back in.
 *
 * @param {Object} unit
 */
function onToggle(unit) {
  emit("focus");
  emit("toggle-excluded", unit.coverPictureId);
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
  /* The row is the query container for its own verdict label, so that label
     degrades on the width it actually has rather than on the viewport's. Same
     mechanism as the toolbar's overflow fold: CSS both ways, no measurement.
     Inline-size only: the row's HEIGHT must stay content-driven, because
     DuplicateQueue samples its scroll pitch from it. */
  container: grow / inline-size;
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
  /* Headroom for a deck's edge ticks, which are drawn up and to the right of
     the tile by exactly two `--space-1` steps. `overflow-x: auto` computes
     `overflow-y: auto` as well, so without this the peek is clipped away and a
     deck reads as a flat picture with a badge on it. */
  padding-top: var(--space-2);
  padding-bottom: var(--space-1);
}

/* One unit's box. It exists so the count badge can be an absolutely-positioned
   SIBLING of the tile rather than a child: `StackBadge` is a <button> and
   `.gthumb` is a <button>, and nesting them is invalid markup that no browser
   resolves the way the markup reads (`.dc-zoom` in DedupCompareDialog is the
   shipped precedent). It wraps the tile exactly, so the corner insets below are
   the tile's corners. */
.gunit {
  position: relative;
  flex: 0 0 auto;
  display: flex;
  height: var(--gthumb-h);
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
   is how the index came to be drawn underneath the lock chip. Pointer-inert as a
   container: the tile owns the whole gesture vocabulary and only a member that
   is itself a control (the deck badge) opts back in. */
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

/* The deck badge's corner, with the hover-only stars beneath it: a PERMANENT
   badge leads the column, a hover-only one follows, so the badge's rest
   position is never set by the height of an invisible star strip. This column
   is a sibling of `.gthumb`, not a child (see `.gunit`). */
.gtr {
  right: var(--space-2);
  align-items: flex-end;
  /* Stated rather than left to DOM order: this column is a SIBLING of the tile
     and has to paint over it, which is exactly what `--z-raised` names
     ("lifted over an immediate sibling: tile badge"). `.gtl` needs none; it is
     inside the tile. */
  z-index: var(--z-raised);
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
   hover; the grid has no focus-triggered display and neither does this,
   matched deliberately, hover means hover). Display-only: pointer-events stays
   OFF at all times, unlike the grid where hover arms the stars for clicking,
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

/* Hovered on the UNIT, not the button: the stars now live in a sibling column
   (see `.gunit`), so a `.gthumb:hover` descendant selector can no longer reach
   them. `.gunit` is exactly the tile's box, so the trigger area is unchanged. */
.gunit:hover .gstars,
.gunit:hover .gsmart {
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

/* ── The expansion band ────────────────────────────────────────────────────
   Its own grid row, spanning all three columns: the band belongs to the row,
   not to any one of its columns, and `1 / -1` is what keeps it out of the
   picture strip's `overflow-x` scroller. A reading surface, so it does not
   take the row's pointer affordance. */
.gexp {
  grid-column: 1 / -1;
  min-width: 0;
  cursor: default;
}

/* The Compare band's own state recipe (`.dc-expansion-state`), so the two
   surfaces that can show a stack's members report a slow or failed read the
   same way. */
.gexp-state {
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

/* The hue is on the glyph and the border; the text stays `on-surface`, because
   `on-<x>` is only ever correct on a solid `<x>` fill. */
.gexp-state--error {
  border-color: rgb(var(--v-theme-warning));
  color: rgb(var(--v-theme-on-surface));
}

.gexp-state--error .v-icon {
  color: rgb(var(--v-theme-warning));
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

/* ── The Stack label's degrade ladder ──────────────────────────────────────
   `Add 1 to stack of 4` → `Add 1 to stack` → `Add 1`. All three forms are in
   the DOM and a container query picks one, which is the shipped toolbar fold
   pattern (no ResizeObserver, no JS measurement). The classes are applied only
   when there IS something to shed, so a one-form label (`Stack 3`,
   `Merge 2 stacks`) is never hidden with no replacement in flow.

   The widths are the point at which the three-column row starts squeezing the
   picture strip rather than the verdict column, measured against the row's own
   inline size: the strip is the column that must keep its room. */
.gsl--mid,
.gsl--short {
  display: none;
}

@container grow (max-width: 880px) {
  .gsl--full {
    display: none;
  }

  .gsl--mid {
    display: inline;
  }
}

@container grow (max-width: 720px) {
  .gsl--mid {
    display: none;
  }

  .gsl--short {
    display: inline;
  }
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
