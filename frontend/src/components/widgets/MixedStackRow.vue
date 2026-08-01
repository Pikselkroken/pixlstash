<template>
  <div
    class="mrow"
    :class="{ 'mrow--revealed': revealed }"
    role="group"
    :aria-label="accessibleName"
    :data-testid="`mixed-stack-${stack.stack_id}`"
  >
    <!-- The stack's cover, at ONE fixed size whatever the queue's slider says.
         This is a list, not the queue: a row here is scanned, not judged
         picture by picture, and a size control over it would be a control with
         nothing to buy. The ticks and the badge come with it so the same stack
         wears the same clothes here as it does in the queue and the grid. -->
    <div class="mcover">
      <StackEdgeTicks :count="stack.member_count" />
      <img
        class="mcover-img"
        :src="coverUrl"
        alt=""
        loading="lazy"
        decoding="async"
      />
      <div class="mcover-badge">
        <StackBadge
          :count="stack.member_count"
          :flagged="flagged"
          :action-title="badgeTitle"
          @activate="emit('show-queue')"
        />
      </div>
    </div>

    <!-- Title over reason. The title is the same noun phrase the queue's deck
         uses, so the same stack reads the same way on both surfaces; the reason
         is the row's actual content and is what ranks it. No rank numeral: the
         order IS the ranking, and a printed "1" would invite the user to think
         of it as a queue with an end. -->
    <div class="mtitle">
      <b>{{ title }}</b>
      <span class="mreason">{{ reason }}</span>
      <!-- Not-yet-comparable is not a mistake, and must never be reported as
           one: a member the embedding worker has not reached yet can carry no
           edge, so it looks stranded without being unlike anything. -->
      <span v-if="unhashedCount" class="mreason mreason--soft">{{
        unhashedSentence
      }}</span>
    </div>

    <!-- The suspects: the row's reason to exist. Small, so the run of them
         reads as one piece of evidence rather than as five decisions; warning
         bordered, because the border is the only thing on this page that says
         "these are the ones". -->
    <ul class="msuspects" :aria-label="suspectsLabel">
      <li v-for="id in suspects" :key="id" class="msuspect">
        <img
          class="msuspect-img"
          :src="thumbUrl(id)"
          alt=""
          loading="lazy"
          decoding="async"
        />
      </li>
      <li v-if="moreSuspects" class="msuspect-more">
        +{{ moreSuspects.toLocaleString() }}
      </li>
    </ul>

    <div class="mactions">
      <!-- The primary action NAMES ITS OUTCOME, because it is the last text
           before a change to the library: `Split off 1` when there is a clear
           stranger, `Unstack` when there is no majority left to keep. Both are
           one operation, so one Ctrl+Z reverses either. -->
      <AppButton
        v-if="!readOnly"
        variant="ghost"
        size="sm"
        :icon-left="plan.action === 'split' ? 'call-split' : 'layers-off'"
        :loading="busy"
        :title="primaryTitle"
        @click="emit('resolve')"
        >{{ plan.label }}</AppButton
      >
      <!-- Keep is what makes this list drainable. Without it the
           legitimate-but-odd stacks sit here forever and the page becomes
           ignorable. It changes no picture, so it is not undoable; the way
           back is to clear the Keep, which the page offers after the press. -->
      <AppButton
        v-if="!readOnly"
        variant="ghost"
        size="sm"
        icon-left="check"
        :loading="busy"
        title="This stack is fine. It stops being listed until its members change."
        @click="emit('keep')"
        >Keep</AppButton
      >
      <!-- Back to the queue, but only when there is somewhere real to land:
           the queue is paged, and a shortcut that scrolled to a guessed row
           would be worse than one that is not offered. -->
      <AppButton
        v-if="canShowQueue"
        variant="ghost"
        size="sm"
        icon-left="format-list-checks"
        title="Show the duplicate group this stack appears in, back in the review queue."
        @click="emit('show-queue')"
        >In the queue</AppButton
      >
    </div>
  </div>
</template>

<script setup>
// One mixed stack: a live stack whose members do not form a single connected
// cluster at the queue's current similarity threshold (design D5).
//
// **It is deliberately not a `DedupGroupRow`.** That row's shape, a card with
// a border, a background, a radius and a focus bar, says "decide this now,
// with the keyboard, and I will advance to the next one". This is a list of
// stacks to look into when there is time, most users will never have one, and
// a second thing that looked like the queue would be read as a second queue
// with a second to-do count. So: divider between rows, no border, no
// background, no radius, no focus bar.
//
// Ranked worst first by the server (stranded members descending, then
// component count, then weakest edge) and printed with no rank numerals: the
// order is the ranking, and a numeral would promise a position that changes
// the moment the threshold slider moves.

import { computed } from "vue";
import AppButton from "./AppButton.vue";
import StackBadge from "./StackBadge.vue";
import StackEdgeTicks from "./StackEdgeTicks.vue";
import { pictureThumbnailUrl } from "../../api/pictures";
import { API_BASE_URL } from "../../utils/apiClient";
import {
  hasStrandedMember,
  mixedStackAction,
  mixedStackReason,
  mixedStackSuspects,
  mixedStackTitle,
} from "../../utils/dedup";

/** How many suspect thumbnails one row shows before it counts the rest. */
const SUSPECT_LIMIT = 6;

const props = defineProps({
  /** One `MixedStackModel` row from `GET /dedup/mixed-stacks`. */
  stack: { type: Object, required: true },
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
});

const emit = defineEmits(["resolve", "keep", "show-queue"]);

const title = computed(() => mixedStackTitle(props.stack));
const reason = computed(() => mixedStackReason(props.stack));
const plan = computed(() => mixedStackAction(props.stack));
const flagged = computed(() => hasStrandedMember(props.stack));

const suspects = computed(() => mixedStackSuspects(props.stack, SUSPECT_LIMIT));

/** How many suspects did not fit, so the run never implies it is the whole set. */
const moreSuspects = computed(() => {
  const shown = suspects.value.length;
  const strangers =
    props.stack?.stranded_picture_ids?.length ||
    Math.max(
      0,
      (Number(props.stack?.member_count) || 0) -
        (Number(props.stack?.largest_component_size) || 0),
    );
  return Math.max(0, strangers - shown);
});

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
 * The row's whole meaning in one sentence, for a screen reader.
 *
 * The visible row carries it across a title, a reason line and a run of
 * unlabelled thumbnails; none of that reaches assistive tech as a unit.
 */
const accessibleName = computed(
  () => `${title.value}. ${reason.value}. ${plan.value.label}.`,
);

const suspectsLabel = computed(() =>
  suspects.value.length === 1
    ? "The picture that does not match the rest"
    : `The ${suspects.value.length} pictures that do not match the rest`,
);

const badgeTitle = computed(
  () =>
    `A stack of ${props.stack?.member_count ?? 0} pictures. ${reason.value}.`,
);

const primaryTitle = computed(() =>
  plan.value.action === "split"
    ? `Take ${plan.value.pictureIds.length === 1 ? "that picture" : "those pictures"} out of the stack and leave the rest together. Nothing is deleted, and Ctrl+Z puts it back.`
    : "Free every picture in this stack. Nothing is deleted, and Ctrl+Z restores the stack exactly as it was.",
);

/** The stack's leader, versioned so the row renders from this payload alone. */
const coverUrl = computed(() =>
  pictureThumbnailUrl(props.stack?.leader_picture_id, {
    version: props.stack?.leader_thumbnail_version,
    baseUrl: API_BASE_URL,
  }),
);

/**
 * One suspect's thumbnail.
 * @param {number} id
 * @returns {string}
 */
function thumbUrl(id) {
  return pictureThumbnailUrl(id, { baseUrl: API_BASE_URL });
}
</script>

<style scoped>
/* A LIST row. No border, no background, no radius, no focus bar: everything
   that would make it read as a card, and therefore as a second queue, is
   deliberately absent. The divider is the whole separation, and the last row
   drops it so the list does not end on a rule. */
.mrow {
  display: grid;
  grid-template-columns: auto minmax(140px, 1fr) auto auto;
  align-items: center;
  gap: var(--space-5);
  padding: var(--space-4) var(--space-2);
  border-bottom: 1px solid rgb(var(--v-theme-divider));
}

.mrow:last-child {
  border-bottom: none;
}

/* The one exception to "no per-row treatment", and it is transient: the row the
   two-way shortcut just landed on says so, or the jump silently does nothing
   visible on a list of near-identical rows. A wash, not a border, so the row's
   box is unchanged and the list's rhythm survives. */
.mrow--revealed {
  background: var(--active-wash);
}

/* 64px, fixed. The cover is an identifier here, not evidence: the evidence is
   the run of suspects to its right. */
.mcover {
  position: relative;
  width: 64px;
  height: 64px;
  flex-shrink: 0;
}

.mcover-img {
  position: relative;
  z-index: var(--z-raised);
  width: 100%;
  height: 100%;
  object-fit: cover;
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-on-surface), 0.06);
}

/* The badge column, an absolutely-positioned SIBLING of the image: StackBadge
   is a <button>, and the queue row's `.gtr` construction is the precedent. */
.mcover-badge {
  position: absolute;
  top: var(--space-1);
  right: var(--space-1);
  z-index: var(--z-raised);
  display: flex;
}

.mtitle {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}

.mtitle b {
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
}

.mreason {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.7);
}

.mreason--soft {
  color: rgba(var(--v-theme-on-surface), 0.55);
}

/* The suspects. A plain run, wrapping rather than scrolling: a horizontal
   scroller inside a list row is a second gesture on the axis the page already
   scrolls on. */
.msuspects {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.msuspect {
  display: flex;
}

/* The warning border IS the marking on this page: these are the pictures the
   primary button is about to move. `warning` as a border is the 3:1 UI job the
   token is authored for. */
.msuspect-img {
  width: 40px;
  height: 40px;
  object-fit: cover;
  border-radius: var(--radius-sm);
  border: 1px solid rgb(var(--v-theme-warning));
  background: rgba(var(--v-theme-on-surface), 0.06);
}

.msuspect-more {
  display: inline-flex;
  align-items: center;
  min-height: var(--badge-size);
  padding: 0 var(--space-2);
  border-radius: var(--radius-pill);
  background: rgba(var(--v-theme-on-surface), 0.08);
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
  color: rgba(var(--v-theme-on-surface), 0.75);
}

/* Quiet by construction: every action here is a ghost. Nothing on this page is
   urgent, and a filled button would argue otherwise on a list most users will
   never see. */
.mactions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  justify-content: flex-end;
}

@container mixedlist (max-width: 720px) {
  /* The suspects and the actions stack under the title rather than compete
     for a width neither can have. The cover keeps its column. */
  .mrow {
    grid-template-columns: auto minmax(0, 1fr);
  }
  .msuspects,
  .mactions {
    grid-column: 2;
    justify-content: flex-start;
  }
}
</style>
