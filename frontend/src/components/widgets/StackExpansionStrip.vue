<template>
  <div class="sxstrip" :style="stripStyle" data-testid="stack-expansion-strip">
    <div class="sxhead">
      <v-icon class="sxico" size="18">mdi-image-multiple</v-icon>
      <span class="sxtitle">Stack of {{ count }}</span>
      <span v-if="reason" class="sxmeta">{{ reason }}</span>
      <span v-if="capturedLabel" class="sxmeta">{{ capturedLabel }}</span>
    </div>

    <!-- The members at a readable size, in stack order, cover first. Scrolls
         sideways rather than wrapping: a stack is a sequence, and a wrapped
         second line breaks the reading order the position numbers rely on. -->
    <div class="sxrow">
      <button
        v-for="member in members"
        :key="member.id"
        type="button"
        class="sxthumb"
        :class="{ 'sxthumb--cover': isCover(member.id) }"
        :aria-pressed="isCover(member.id)"
        :disabled="readOnly"
        :title="memberTitle(member.id)"
        data-testid="stack-member"
        @click.stop="onPick(member)"
      >
        <img
          class="sxt"
          :src="thumbUrl(member)"
          alt=""
          loading="lazy"
          decoding="async"
        />
        <span v-if="isCover(member.id)" class="sxcv">Cover</span>
      </button>

      <button
        v-if="!readOnly && showUnstack"
        type="button"
        class="sxbtn"
        data-testid="stack-unstack"
        @click.stop="emit('unstack')"
      >
        <v-icon size="16">mdi-call-split</v-icon>
        <span>Unstack</span>
      </button>
    </div>
  </div>
</template>

<script setup>
// The header strip above an expanded stack's members in the grid.
//
// It answers the two questions an expanded stack raises: what made these one
// stack, and which frame is the one the grid shows. The reason and the capture
// date sit next to the count so the grouping is inspectable rather than
// something the app just did; picking a different cover and taking the stack
// apart are the two actions that follow from disagreeing with it.
//
// `capturedLabel` arrives preformatted. Date formatting is a locale decision
// that belongs where the data is read, and a second formatter here would drift
// from the rest of the grid within a release.
//
// Presentational: it owns no stack state and reports both actions upward.
//
// MOUNTED BY the two surfaces that show a duplicate group (design decision D4
// in `docs/design/mixed-stacks-and-stack-units.md`), both full width and BELOW
// their own strip of pictures:
//
//   * `DedupCompareDialog`, below the candidate strip and never inside a card,
//     where a variable-height band would destroy the height registration the
//     whole comparison depends on. That one allows promotion, behind a two-step
//     confirmation that says the cover changes across the library.
//   * `DedupGroupRow`, below the row's three columns and never inline in its
//     `overflow-x` picture strip. That one passes `readOnly` and
//     `showUnstack: false`: the queue row is a place to LOOK, and neither of
//     this component's two actions can be honoured there without rewriting the
//     library from inside a panel opened to inspect it.
//
// STILL NOT MOUNTED IN `ImageGrid`, deliberately. The strip has to span the full
// grid width above the stack's members, and `ImageGrid` is virtualised on a
// uniform tile: its spacer arithmetic and its `img.idx` keys both assume every
// child is one tile tall. Wedging a spanning row in without teaching
// `useVirtualScroll` about variable row heights desynchronises the scroll
// window, which is the failure this grid's "never splice allGridImages" rule
// exists to prevent. The cover marker, the half of this that a user actually
// loses when a stack expands, ships separately as the per-tile flag in
// `ImageGrid`. Mounting the strip there is a change to the grid's layout
// arithmetic and belongs in its own change.

import { computed } from "vue";

import { pictureThumbnailUrl } from "../../api/pictures";
import { API_BASE_URL } from "../../utils/apiClient";

const props = defineProps({
  /** Members in the stack, including any not rendered in this strip. */
  count: { type: Number, required: true },
  /** `[{ id, thumbnail_version }]` in stack order; the first is the cover. */
  members: { type: Array, default: () => [] },
  /** The explicit cover, when the caller tracks one apart from stack order. */
  coverId: { type: [Number, String], default: null },
  /** Why these are one stack, e.g. "Burst, 81% similar". */
  reason: { type: String, default: "" },
  /** Preformatted capture date. Never formatted in this component. */
  capturedLabel: { type: String, default: "" },
  /** A shared or guest view: the strip informs, it does not act. */
  readOnly: { type: Boolean, default: false },
  /**
   * The member thumbnails' HEIGHT in px. The caller owns it because the two
   * surfaces that show a stack size their pictures differently (the queue runs
   * a 112-406px size slider), and a strip hardcoded to one of them contradicts
   * the other. Only the height: the width follows the decoded image, because
   * stored dimensions ignore EXIF rotation and a fixed box would letterbox or
   * crop every rotated portrait shot.
   */
  thumbHeight: { type: Number, default: 96 },
  /**
   * Whether the trailing Unstack action is offered. False for a caller that
   * has no unstack pathway to honour: the Compare dialog's band, where the
   * expansion is disclosure inside an undecided verdict, not a place to take a
   * stack apart. Distinct from `readOnly`, which also freezes the cover.
   */
  showUnstack: { type: Boolean, default: true },
});

const emit = defineEmits(["unstack", "set-cover"]);

/**
 * How wide a member may get before it is cropped: 2.4:1, the same
 * beyond-panoramic ceiling the queue's strip uses, scaled with the height so
 * the widest allowed shape is the same at every size.
 */
const MAX_THUMB_RATIO = 2.4;

/** The height-driven recipe, handed to the CSS as custom properties. */
const stripStyle = computed(() => ({
  "--sx-thumb-h": `${props.thumbHeight}px`,
  "--sx-thumb-max-w": `${Math.round(props.thumbHeight * MAX_THUMB_RATIO)}px`,
}));

// Falls back to stack order, so a caller that has not lifted the cover into its
// own state still gets exactly one flagged member rather than none.
const effectiveCoverId = computed(
  () => props.coverId ?? props.members[0]?.id ?? null,
);

/**
 * Whether a member is the stack's cover.
 * @param {number|string} id
 * @returns {boolean}
 */
function isCover(id) {
  if (effectiveCoverId.value == null || id == null) return false;
  return String(id) === String(effectiveCoverId.value);
}

/**
 * The tooltip for one member button.
 * @param {number|string} id
 * @returns {string}
 */
function memberTitle(id) {
  if (isCover(id)) return "This is the cover";
  return props.readOnly ? "Stack member" : "Make this the cover";
}

/**
 * The thumbnail URL for one member.
 * @param {Object} member
 * @returns {string}
 */
function thumbUrl(member) {
  return pictureThumbnailUrl(member.id, {
    version: member.thumbnail_version,
    baseUrl: API_BASE_URL,
  });
}

/**
 * Clicking a member promotes it to cover. Clicking the cover is a no-op: it is
 * the commonest misclick in the strip, and emitting there would hand the caller
 * a redundant write to undo.
 * @param {Object} member
 */
function onPick(member) {
  if (isCover(member.id)) return;
  emit("set-cover", member.id);
}
</script>

<style scoped>
.sxstrip {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-3);
  border-radius: var(--radius-md);
  border: 1px solid rgb(var(--v-theme-divider));
  background: rgb(var(--v-theme-surface));
}

.sxhead {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.sxico {
  color: rgb(var(--v-theme-on-surface));
  flex-shrink: 0;
}

.sxtitle {
  font-size: var(--text-base);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-snug);
  color: rgb(var(--v-theme-on-surface));
}

/* The evidence, deliberately de-emphasised. It is there to be checked, not to
   compete with the count for the first glance. */
.sxmeta {
  font-size: var(--text-xs);
  line-height: var(--leading-snug);
  color: rgba(var(--v-theme-on-surface), 0.6);
}

.sxrow {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  overflow-x: auto;
  scrollbar-width: thin;
  scrollbar-gutter: stable;
  padding-bottom: var(--space-1);
}

/* The queue strip's recipe (DedupGroupRow's `.gthumb`), so a picture is the
   same shape whether the user is deciding on the stack or living with it: the
   HEIGHT is set by the caller and the WIDTH comes from the decoded image. Not a
   preference: stored width/height ignore EXIF rotation, so a fixed box is
   wrong for every rotated portrait shot. */
.sxthumb {
  position: relative;
  flex: 0 0 auto;
  width: auto;
  min-width: 44px;
  height: var(--sx-thumb-h, 96px);
  padding: 0;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  background: rgba(var(--v-theme-on-surface), 0.06);
  overflow: hidden;
  cursor: pointer;
}

.sxthumb:hover:not(:disabled) {
  background: var(--hover-wash);
}

.sxthumb:focus-visible {
  box-shadow: var(--focus-ring);
  outline: none;
}

/* The cover is flagged twice, by edge and by label, so it survives both a
   colour-blind read and a glance at a strip of near-identical frames. */
.sxthumb--cover {
  border-color: rgb(var(--v-theme-accent));
}

/* Read-only keeps the pictures at full strength: the strip is still worth
   looking at when there is nothing to press. Only the cursor changes. */
.sxthumb:disabled {
  cursor: default;
}

.sxt {
  display: block;
  width: auto;
  height: 100%;
  /* A ceiling, not a crop: only a beyond-panoramic shape gets clipped. */
  max-width: var(--sx-thumb-max-w, 230px);
  object-fit: cover;
}

/* Sits directly on the photo, so the photo scrim and `on-dark-surface`. */
.sxcv {
  position: absolute;
  bottom: var(--space-2);
  left: var(--space-2);
  padding: 0 var(--space-2);
  border-radius: var(--radius-sm);
  background: var(--scrim-photo);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: var(--text-2xs);
  font-weight: var(--weight-semibold);
  line-height: var(--leading-snug);
  text-transform: uppercase;
  letter-spacing: var(--tracking-label);
}

/* Ghost: taking a stack apart is a reversal, not the strip's headline action,
   so it reads as available rather than inviting. */
.sxbtn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  flex: 0 0 auto;
  padding: var(--space-2) var(--space-3);
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.75);
  font-family: var(--font-ui);
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  line-height: var(--leading-snug);
  cursor: pointer;
  transition: background var(--dur-1) var(--ease-standard);
}

.sxbtn:hover {
  background: var(--hover-wash);
}

.sxbtn:focus-visible {
  box-shadow: var(--focus-ring);
  outline: none;
}
</style>
