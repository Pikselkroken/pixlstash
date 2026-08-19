<template>
  <div
    class="grow"
    :class="{
      'grow--focus': focused,
      'grow--selected': selected,
      'grow--revealed': revealed,
    }"
    role="group"
    :aria-label="accessibleName"
    :aria-current="focused ? 'true' : undefined"
    :aria-selected="selected ? 'true' : undefined"
    :data-testid="`mixed-stack-${stack.stack_id}`"
    @mousedown="onRowMouseDown"
    @click="emit('focus', $event)"
    @dblclick="onDblClick"
  >
    <div class="ginfo">
      <div class="gn">
        <v-icon v-if="focused" class="gcaret" size="18">mdi-menu-right</v-icon>
        <b>{{ title }}</b>
        <span class="gn-sep" aria-hidden="true">|</span>
        <span>{{ reason }}</span>
      </div>
      <!-- Evidence, in the `fact` tone: the stack already exists, so these
           describe what was measured about it rather than arguing for a verdict
           the user has not given. Same component and same `[{text, against}]`
           payload as the review queue. -->
      <DedupWhyPills :why="stack.why" variant="fact" :limit="whyLimit" />
      <!-- Not-yet-comparable is not a mistake, and must never be reported as
           one: a member the embedding worker has not reached yet can carry no
           edge, so it looks stranded without being unlike anything. It is
           therefore never pre-marked either. -->
      <span v-if="unhashedCount" class="mqnote">{{ unhashedSentence }}</span>
      <!-- The lock note. It is the `aria-describedby` target of the primary
           button, so it stays in the DOM and visible for as long as the button
           is blocked: a reason that lives only in a tooltip is a reason the
           keyboard never reaches. Named, because the set is what has to be
           unlocked and "locked" alone is a dead end. -->
      <span
        v-if="lockNote"
        :id="lockNoteId"
        class="mqlock"
        :class="{ 'mqlock--flash': lockFlash }"
        :title="lockTitle"
      >
        <v-icon size="13">mdi-lock-outline</v-icon>
        {{ lockNote }}
      </span>
    </div>

    <!-- One tile per MEMBER, never collapsed: the whole point of this page is
         to look inside an existing stack, so the deck the review queue draws
         would hide precisely what is being judged. The strip is the review
         queue's own, so a member here wears the same clothes a candidate wears
         there. -->
    <DedupPictureStrip
      :tiles="tiles"
      :thumb-height="thumbHeight"
      :focused="focused"
      :load-thumbnails="loadThumbnails"
      :cursor-index="focused ? cursorIndex : -1"
      @pick="onTile($event)"
      @toggle="onTile($event)"
    />

    <div class="gact">
      <!-- The primary NAMES ITS OUTCOME, and the name moves with the marks:
           `Split off 2` while a majority survives, `Unstack all 5` the moment
           the marks would leave fewer than two members behind. The icon changes
           at the same instant, so the two never disagree.

           It is a PREDICTION. What actually happened is read off the response's
           `stack_dissolved`, because the stack can have changed between the
           read and the press.

           A locked picture set freezes the whole stack, so both outcomes are
           refused: the button is marked `aria-disabled`, never `disabled`, so
           it keeps its place in the tab order and the reason it points at stays
           reachable. The page owns the guard, so the press is answered rather
           than dead. -->
      <button
        v-if="!readOnly"
        type="button"
        class="gbtn gbtn--stack"
        :tabindex="focused ? 0 : -1"
        :disabled="busy"
        aria-keyshortcuts="Enter"
        :title="lockNote ? lockTitle : primaryTitle"
        v-bind="primaryLockAttrs"
        @click.stop="emit('resolve')"
      >
        <v-icon size="16">mdi-{{ plan.icon }}</v-icon>
        <span>{{ plan.label }}</span>
        <kbd v-if="focused" aria-hidden="true">Enter</kbd>
      </button>
      <!-- Keep is what makes this list drainable. Without it the
           legitimate-but-odd stacks sit here forever and the page becomes
           ignorable. It changes no picture, so it is not undoable; the way back
           is to clear the Keep, which the page offers after the press.

           It stays live on a frozen row ON PURPOSE. Keep writes a dismissal,
           not a picture, so the backend's keep route carries no lock guard at
           all: disabling it here would strand the one row the user cannot
           otherwise clear from the list.

           **It is also the only verdict that acts in bulk.** The primary's
           outcome differs per row (one stack splits, the next dissolves), so a
           bulk primary could not name what it was about to do; a button that
           cannot name its outcome on this page is a button that must not act on
           twelve rows at once. -->
      <button
        v-if="!readOnly"
        type="button"
        class="gbtn"
        :tabindex="focused ? 0 : -1"
        :disabled="busy"
        aria-keyshortcuts="K"
        :title="
          bulk
            ? `Leave all ${selectionCount} selected stacks exactly as they are. They stop being listed until their pictures change.`
            : 'This stack is fine. It stops being listed until its pictures change.'
        "
        @click.stop="emit('keep')"
      >
        <v-icon size="16">mdi-check</v-icon>
        <span>{{ bulk ? `Keep ${selectionCount} stacks` : "Keep" }}</span>
        <kbd v-if="showsKeepKey" aria-hidden="true">K</kbd>
      </button>
      <button
        type="button"
        class="gcompare"
        :tabindex="focused ? 0 : -1"
        @click.stop="emit('compare')"
      >
        <v-icon size="15">mdi-compare-horizontal</v-icon>
        <span>Compare all {{ members.length }}</span>
        <kbd v-if="focused" aria-hidden="true">C</kbd>
      </button>
      <!-- Back to the review queue, but only when there is somewhere real to
           land: that queue is paged, and a shortcut that scrolled to a guessed
           row would be worse than one that is not offered. -->
      <button
        v-if="canShowQueue"
        type="button"
        class="gcompare"
        :tabindex="focused ? 0 : -1"
        title="Show the duplicate group this stack appears in, back in the review queue."
        @click.stop="emit('show-queue')"
      >
        <v-icon size="15">mdi-format-list-checks</v-icon>
        <span>In the queue</span>
      </button>
    </div>
  </div>
</template>

<script setup>
// One row of the Mixed stacks queue: a live stack whose members do not form a
// single connected cluster at the current similarity threshold (design D5).
//
// **It is a queue row, and it is a SIBLING of `DedupGroupRow`, not a mode of
// it.** The two share the strip, the focus treatment, the keyboard model and
// the action receipt, and they differ in everything else: this row's tiles are
// one stack's members rather than the units a verdict moves, its verdicts are
// split / unstack / keep rather than stack / keep separate, and its evidence is
// about an object that already exists rather than about one being proposed.
// `DedupGroupRow` is already 1,500 lines; a second variant axis inside it would
// be a file nobody can change safely.
//
// The model in one line: members start IN the stack, `X` marks a stranger, and
// the marked ones are what the primary button takes out. The server pre-marks
// the members it believes are strangers, so the row opens with some already
// marked, exactly as the review queue opens with the server's exclusions
// already applied. There is deliberately ONE stranger treatment: an engine mark
// and a user mark look the same and behave the same, because the button acts on
// one list and a user cannot act on a distinction the button does not make.
//
// The row owns no data of its own. Marks, the member cursor, the focus and the
// selection all belong to the page (`useMixedStackQueue`); this component
// reports what was pressed.

import { computed, useId } from "vue";

import DedupPictureStrip from "./DedupPictureStrip.vue";
import DedupWhyPills from "./DedupWhyPills.vue";
import { pictureThumbnailUrl } from "../../api/pictures";
import {
  isMixedStackStackable,
  mixedStackLockNote,
  mixedStackLockTitle,
  mixedStackMembers,
  mixedStackPrimary,
  mixedStackPrimaryTitle,
  mixedStackReason,
  mixedStackTitle,
  edgePercentText,
} from "../../utils/dedup";
import {
  DEFAULT_THUMBNAIL_SIZE_LEVEL,
  stripHeightForSizeLevel,
} from "../../utils/thumbnailSizes";

/**
 * Below this strip height the info column, not the strip, sets the row height,
 * and the second why-pill is what keeps it tall. The same rung `DedupGroupRow`
 * folds at, so the two rows change shape together.
 */
const ONE_PILL_BELOW_PX = 96;

const props = defineProps({
  /** One `MixedStackModel` row from `GET /dedup/mixed-stacks`. */
  stack: { type: Object, required: true },
  /** This row's position in the list, for the accessible name. */
  index: { type: Number, default: 0 },
  /** How many rows the list holds, for the same reason. */
  total: { type: Number, default: 0 },
  /** The keyboard cursor is on this row. */
  focused: { type: Boolean, default: false },
  /** Part of a Ctrl/Shift-click multi-selection. */
  selected: { type: Boolean, default: false },
  selectionCount: { type: Number, default: 0 },
  /** True when `K` would genuinely take the whole selection. */
  bulkKeys: { type: Boolean, default: false },
  /**
   * The picture ids marked as strangers: the engine's opening set as the user
   * has since adjusted it. This is the list the primary button acts on.
   */
  markedIds: { type: Array, default: () => [] },
  /** Which member `X` acts on, as an index into the strip. */
  cursorIndex: { type: Number, default: 0 },
  /** How tall the strip draws its pictures, from the queue's size control. */
  thumbHeight: {
    type: Number,
    default: stripHeightForSizeLevel(DEFAULT_THUMBNAIL_SIZE_LEVEL),
  },
  /** False outside the read-ahead window: placeholders instead of pictures. */
  loadThumbnails: { type: Boolean, default: true },
  /** An action on this row is in flight. */
  busy: { type: Boolean, default: false },
  /** A read-only session sees the evidence and none of the writes. */
  readOnly: { type: Boolean, default: false },
  /**
   * A loaded duplicate group holds this stack, so the return shortcut has
   * somewhere real to land. The page decides this, not the row: only the store
   * knows which groups are in the window.
   */
  canShowQueue: { type: Boolean, default: false },
  /** The two-way shortcut arrived here, so the row marks itself once. */
  revealed: { type: Boolean, default: false },
  /** A refusal named this row, so the lock note answers the press visibly. */
  lockFlash: { type: Boolean, default: false },
  /**
   * The pictures a 423 named, for the per-tile lock chip.
   *
   * Empty on a freshly-read row even when it is frozen: the payload rolls the
   * lock up over the whole stack and names no member, so the row's reason line
   * carries the freeze until a refusal says which pictures it is.
   */
  flashIds: { type: Array, default: () => [] },
});

const emit = defineEmits([
  "focus",
  "resolve",
  "keep",
  "compare",
  "show-queue",
  "toggle-mark",
  "set-cursor",
]);

const lockNoteId = useId();

const title = computed(() => mixedStackTitle(props.stack));
const reason = computed(() => mixedStackReason(props.stack));
const members = computed(() => mixedStackMembers(props.stack));

/** What the primary button is about to do, given the marks in force. */
const plan = computed(() => mixedStackPrimary(props.stack, props.markedIds));
const primaryTitle = computed(() => mixedStackPrimaryTitle(plan.value));

/** Whether a locked picture set freezes this stack, and so refuses both writes. */
const frozen = computed(() => !isMixedStackStackable(props.stack));
const lockNote = computed(() => mixedStackLockNote(props.stack));
const lockTitle = computed(() => mixedStackLockTitle(props.stack));

/**
 * `aria-disabled` plus the pointer at the note, never the `disabled` attribute:
 * a disabled button leaves the tab order, so a keyboard user could never reach
 * the control to discover why it does nothing.
 */
const primaryLockAttrs = computed(() =>
  lockNote.value
    ? { "aria-disabled": "true", "aria-describedby": lockNoteId }
    : {},
);

/** Whether a verdict from this row would act on the whole selection. */
const bulk = computed(() => props.selected && props.selectionCount > 1);

/**
 * The `K` chip: the focused row always, and every selected row while the bulk
 * gesture is live, because the key genuinely acts on all of them. The primary
 * never gets that treatment, because it never acts in bulk.
 */
const showsKeepKey = computed(
  () => props.focused || (props.selected && props.bulkKeys),
);

/** How many why-pills the info column has room for at this size. */
const whyLimit = computed(() =>
  props.thumbHeight < ONE_PILL_BELOW_PX ? 1 : 2,
);

const markedSet = computed(
  () => new Set(props.markedIds.map((id) => String(id))),
);

/** Members with no usable hash yet: not yet comparable, never a mistake. */
const unhashedCount = computed(
  () => props.stack?.unhashed_picture_ids?.length ?? 0,
);

const unhashedSentence = computed(() =>
  unhashedCount.value === 1
    ? "1 picture has not been analysed yet, so it cannot be compared."
    : `${unhashedCount.value} pictures have not been analysed yet, so they cannot be compared.`,
);

/**
 * The tiles the strip draws: one per member, marked or not.
 *
 * The mark is a BORDER and a glyph chip, never the excluded fade. A marked tile
 * is the row's evidence, and fading it would say "inert" about the only tiles
 * that are not.
 */
const tiles = computed(() =>
  members.value.map((member, i) => {
    const marked = markedSet.value.has(String(member.pictureId));
    const named = props.flashIds.some(
      (id) => String(id) === String(member.pictureId),
    );
    return {
      key: member.key,
      member,
      src: pictureThumbnailUrl(member.pictureId),
      ariaLabel: memberLabel(member, i),
      title: memberTitle(member),
      pressed: marked,
      marked,
      markIcon: marked ? "mdi-call-split" : "",
      // A locked set freezes the whole stack, so every tile is frozen; only the
      // pictures a refusal actually named wear the chip.
      locked: frozen.value,
      lockChip: named,
      lockFlash: named,
      chip: {
        icon: "mdi-approximately-equal",
        text: edgePercentText(member.nearestEdge),
        title: matchSentence(member),
      },
    };
  }),
);

/**
 * How close one member gets to its nearest sibling, in words.
 *
 * The absences are different facts and the row already distinguishes them, so
 * the sentence does too rather than reporting one dash several ways. A stranded
 * member gets its real number and the reason it is out: it did not match
 * *nothing*, it fell outside the threshold, and the user is the one holding
 * that slider.
 *
 * @param {Object} member
 * @returns {string}
 */
function matchSentence(member) {
  if (member.unhashed) {
    return "This picture has not been analysed yet, so it cannot be compared.";
  }
  if (member.nearestEdge === null) {
    return "Nothing else in this stack has been analysed yet, so there is nothing to compare this against.";
  }
  const closest = `Closest match ${edgePercentText(member.nearestEdge)}, to picture #${member.nearestPictureId}.`;
  return member.stranded
    ? `${closest} That is below your threshold, so it is out of the stack's cluster.`
    : closest;
}

/**
 * What a tile is called.
 *
 * The image is decorative here (the strip deliberately carries no metadata), so
 * without this every tile reaches a screen reader as the same unlabelled
 * control repeated N times. The mark leads, because it is the one fact the
 * primary button acts on.
 *
 * @param {Object} member
 * @param {number} i - the member's zero-based position, which is what `1`-`9`
 *   addresses.
 * @returns {string}
 */
function memberLabel(member, i) {
  const parts = [`Picture ${i + 1} of ${members.value.length}`];
  if (markedSet.value.has(String(member.pictureId))) {
    parts.push("marked as a stranger, it comes out of the stack");
  } else {
    parts.push("in the stack");
  }
  if (member.unhashed) parts.push("not analysed yet, so not comparable");
  else if (member.nearestEdge === null)
    parts.push("nothing to compare against");
  else {
    parts.push(`closest match ${edgePercentText(member.nearestEdge)}`);
    if (member.stranded) parts.push("below your threshold");
  }
  return parts.join(", ");
}

/**
 * The tooltip for a tile: the gesture, then the evidence.
 *
 * Only the focused row answers to `X`, so only the focused row claims it works.
 *
 * @param {Object} member
 * @returns {string}
 */
function memberTitle(member) {
  const match = matchSentence(member);
  if (frozen.value) return `${lockTitle.value} ${match}`;
  const marked = markedSet.value.has(String(member.pictureId));
  const gesture = marked
    ? props.focused
      ? "Click, or press X, to put this picture back in the stack."
      : "Click to put this picture back in the stack."
    : props.focused
      ? "Click, or press X, to mark this picture as a stranger, so it comes out of the stack."
      : "Click to mark this picture as a stranger, so it comes out of the stack.";
  return `${gesture} ${match}`;
}

/**
 * A tile press: focus the row, move the member cursor there, toggle the mark.
 *
 * Click and right-click do the SAME thing here, unlike the review queue where
 * click chooses a cover and right-click excludes. A mixed stack has no cover to
 * choose, so there is one gesture and both buttons perform it; that is also
 * what makes Compare's card click match this row exactly.
 *
 * @param {Object} tile
 */
function onTile(tile) {
  emit("focus");
  const index = members.value.findIndex(
    (member) => member.pictureId === tile.member.pictureId,
  );
  if (index >= 0) emit("set-cursor", index);
  emit("toggle-mark", tile.member.pictureId);
}

/**
 * The row's whole meaning in one sentence, for a screen reader.
 *
 * The visible row carries it across a title, a reason, a run of pills and a
 * strip of unlabelled thumbnails; none of that reaches assistive tech as a
 * unit. The lock note rides along, so a screen-reader user meeting the row from
 * the top hears why the action will not run before reaching the button.
 */
const accessibleName = computed(() =>
  [
    props.total
      ? `Stack ${props.index + 1} of ${props.total}`
      : `Stack ${props.index + 1}`,
    title.value,
    reason.value,
    plan.value.label,
    lockNote.value,
  ]
    .filter(Boolean)
    .map((part) => `${part}.`)
    .join(" "),
);

/**
 * A modified press means "select rows", so the browser's own gesture on the
 * same input, extending a text selection from wherever the caret last was, must
 * not also run. Selection starts on mousedown, before the click handler ever
 * sees the event, so this is the only place it can be refused.
 * @param {MouseEvent} event
 */
function onRowMouseDown(event) {
  if (event.shiftKey || event.ctrlKey || event.metaKey) event.preventDefault();
}

/**
 * Double-click means "open this": the same Compare the `C` key and the Compare
 * button reach. The action buttons keep their own double-click meaning, and a
 * modified double-click belongs to the selection gestures.
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
/* The review queue row's box, deliberately: this IS a queue now, and a page
   whose rows looked different would be read as a different kind of surface.
   `DedupGroupRow`'s `.grow` block is the point of truth for these declarations;
   they are repeated here because scoped styles do not cross components and the
   shared part of the two rows is the STRIP, which is a component. */
.grow {
  position: relative;
  display: grid;
  grid-template-columns: minmax(150px, 220px) minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--space-3) var(--space-5);
  padding: var(--space-3) var(--space-4);
  padding-left: var(--space-5);
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

.grow--focus {
  background: var(--active-wash);
  border-color: rgba(var(--v-theme-accent), 0.4);
}

.grow--focus::before {
  content: "";
  position: absolute;
  inset: 0 auto 0 0;
  width: var(--rail-w);
  border-radius: var(--radius-md) 0 0 var(--radius-md);
  background: rgb(var(--v-theme-accent));
}

.grow--selected {
  border-color: rgba(var(--v-theme-accent), 0.55);
  background: var(--hover-wash);
}

.grow--selected.grow--focus {
  background: var(--active-wash);
}

/* The row the two-way shortcut just landed on says so, or the jump silently
   does nothing visible on a list of near-identical rows. Transient, and it
   never competes with the focus treatment. */
.grow--revealed:not(.grow--focus) {
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

.mqnote {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.55);
}

/* The lock note. Its own padding is pulled back by exactly the step it adds, so
   the text keeps the title's left edge and the flash wash still has a box to
   fill. It has to stay in the DOM and visible for as long as the primary button
   points at it. */
.mqlock {
  display: inline-flex;
  align-items: center;
  align-self: flex-start;
  gap: var(--space-2);
  margin-left: calc(-1 * var(--space-2));
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-sm);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.mqlock .v-icon {
  color: inherit;
}

/* The visible answer to a blocked press. The WASH moves and the text colour
   does not: `warning` as body text is a 3:1 UI colour being asked to do a 4.5:1
   text job (visual-language.md section 4). */
.mqlock--flash {
  animation: mq-lock-flash var(--dur-2) var(--ease-standard);
}

@keyframes mq-lock-flash {
  50% {
    background: color-mix(
      in srgb,
      rgb(var(--v-theme-warning)) 26%,
      transparent
    );
  }
}

@media (prefers-reduced-motion: reduce) {
  .mqlock--flash {
    animation: none;
    background: color-mix(
      in srgb,
      rgb(var(--v-theme-warning)) 26%,
      transparent
    );
  }
}

/* The verdict column: one action per line, never wrapping under the strip.
   Same recipe as `DedupGroupRow`'s, for the same reason as `.grow` above. */
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
  color: rgb(var(--v-theme-on-surface));
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  transition:
    background var(--dur-1) var(--ease-standard),
    border-color var(--dur-1) var(--ease-standard);
}

.gbtn:hover:not(:disabled):not([aria-disabled="true"]),
.gcompare:hover {
  background: var(--hover-wash);
}

.gbtn:disabled {
  opacity: var(--opacity-disabled);
  cursor: default;
}

/* A refused primary looks disabled and is still a tab stop, which is the whole
   point of `aria-disabled`: the reason it points at has to be reachable. */
.gbtn[aria-disabled="true"] {
  opacity: var(--opacity-disabled);
  cursor: not-allowed;
}

/* The primary fills only on the focused row, so the eye lands on the one button
   `Enter` would press. ACCENT, not warning: warning marks evidence on this page
   (the strangers) and accent marks action, and the two never swap. */
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
