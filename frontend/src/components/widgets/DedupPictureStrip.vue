<template>
  <div
    ref="stripEl"
    class="gstrip"
    :class="{ 'gstrip--cursor': cursorIndex >= 0 }"
    :style="stripStyle"
  >
    <div
      v-for="(tile, i) in tiles"
      :key="tile.key"
      :ref="(el) => registerTile(el, i)"
      class="gunit"
      :class="{ 'gunit--cursor': i === cursorIndex }"
    >
      <!-- Behind the tile and OUTSIDE it: `.gthumb` clips its overflow, and a
           deck's peeking edges are drawn past the tile's own box. -->
      <slot name="behind" :tile="tile" :index="i"></slot>
      <button
        type="button"
        class="gthumb"
        :class="{
          'gthumb--cover': tile.cover,
          'gthumb--marked': tile.marked,
          'gthumb--out': tile.out,
          'gthumb--locked': tile.locked,
        }"
        :tabindex="focused ? 0 : -1"
        :aria-pressed="tile.pressed"
        :aria-label="tile.ariaLabel"
        :title="tile.title"
        @click.stop="emit('pick', tile, i)"
        @contextmenu.prevent.stop="emit('toggle', tile, i)"
      >
        <!-- The IMG sizes its own box: the stored width/height are the raw file
             dimensions and ignore EXIF rotation, so a portrait phone shot
             reports landscape numbers. The browser decodes the rotated pixels,
             so height-fixed + width-auto is the only shape that is always true.
             The placeholder uses the metadata as an estimate; the image
             corrects it on load. -->
        <img
          v-if="loadThumbnails"
          class="gt"
          :src="tile.src"
          alt=""
          loading="lazy"
          decoding="async"
        />
        <span
          v-else
          class="gt gt--placeholder"
          :style="thumbBoxStyle(tile)"
          aria-hidden="true"
        ></span>
        <!-- The top-left corner is a COLUMN, not a slot: the index and the lock
             chip can both be present (a locked tile in a focused row) and used
             to be stacked on the same pixels. Same construction as ImageGrid's
             .thumbnail-top-left-badges. The index leads because `focused` is a
             strip-level fact (every tile shows its index, or none does), so
             leading with it keeps the whole strip's indices on one line, which
             is the only reason the index exists. -->
        <div v-if="focused || lockChipOn(tile)" class="gtl">
          <span v-if="focused" class="gnum">{{ i + 1 }}</span>
          <!-- The server's exclusion, not the user's, so it gets its own
               marker. Same chip as ReviewBinaryCard's .rs-thumb-lock, so the
               app has one lock chip. -->
          <span
            v-if="lockChipOn(tile)"
            class="glock"
            :class="{ 'glock--flash': tile.lockFlash }"
          >
            <v-icon size="12">mdi-lock-outline</v-icon>
          </span>
        </div>
        <!-- Bottom left, one slot with two tenants that never coexist: the
             queue row's `Cover` word, and the Mixed row's stranger glyph. A
             mixed stack has no cover to name, and a duplicate group has no
             stranger to mark. -->
        <span v-if="tile.cornerLabel" class="gcv">{{ tile.cornerLabel }}</span>
        <span v-else-if="tile.markIcon" class="gmark">
          <v-icon size="12">{{ tile.markIcon }}</v-icon>
        </span>
        <!-- Bottom-right, hover-only: the smart score in the queue, the
             strongest match in the Mixed row. Decorative, because the same
             facts are readable in Compare. -->
        <span
          v-if="loadThumbnails && tile.chip"
          class="gsmart"
          aria-hidden="true"
          :title="tile.chip.title"
        >
          <v-icon size="12">{{ tile.chip.icon }}</v-icon>
          {{ tile.chip.text }}
        </span>
        <v-icon v-if="tile.centreIcon" class="gx" size="20">{{
          tile.centreIcon
        }}</v-icon>
      </button>
      <!-- The top-right badge column is an absolutely-positioned SIBLING of the
           tile, not a child: `StackBadge` is a <button> and `.gthumb` is a
           <button>, and a button inside a button is invalid markup. Same
           construction as `.dc-zoom` in DedupCompareDialog.

           Grid rule: a PERMANENT badge leads, a hover-only one follows. A
           leading member that is only `opacity: 0` is still in flow, so the
           column itself never moves; only what sits beneath it does. -->
      <div v-if="hasTopRight" class="gtr">
        <slot name="top-right" :tile="tile" :index="i"></slot>
      </div>
    </div>
  </div>
</template>

<script setup>
// The picture strip both duplicate rows are built on.
//
// It exists because there are now TWO rows drawing the same strip: the review
// queue's `DedupGroupRow`, whose tiles are the units a stack verdict moves, and
// `MixedQueueRow`, whose tiles are one existing stack's members. They ask
// different questions and carry different chips, but the strip itself is one
// thing: the same height-driven sizing math, the same panorama ceiling, the
// same placeholder estimate, the same roving tab stop, the same corner columns
// and the same chip recipes. A second copy of that would drift within a
// release, and `DedupGroupRow` is already 1,500 lines, so the strip comes OUT
// rather than growing a second variant axis inside it.
//
// The class names are deliberately the shipped ones (`.gstrip`, `.gunit`,
// `.gthumb`, `.gt`, `.gtl`, `.gtr`, `.gnum`, `.gcv`, `.glock`, `.gx`,
// `.gsmart`). They are the queue's vocabulary in tests, in the e2e page object
// and in three design documents; renaming them would be a rename, not an
// extraction, and it would hide any real regression inside the noise.
//
// Every tile is a plain data object built by the row (see the `tiles` prop).
// The strip owns no state of its own except the scroll it performs to keep the
// cursor visible: it reports what was pressed and lets the row decide.

import { computed, ref, useSlots, watch } from "vue";

import {
  DEFAULT_THUMBNAIL_SIZE_LEVEL,
  stripHeightForSizeLevel,
} from "../../utils/thumbnailSizes";

/**
 * The widest shape a thumbnail may keep before it is cropped, as a ratio. A
 * ceiling, not a crop: only a beyond-panoramic picture reaches it.
 */
const MAX_THUMB_RATIO = 2.4;

/**
 * @typedef {Object} StripTile
 * @property {string} key - stable `v-for` key.
 * @property {string} src - the thumbnail URL.
 * @property {string} ariaLabel - the tile's accessible name. The image is
 *   decorative (the strip deliberately carries no metadata), so without this
 *   every tile reaches a screen reader as the same unlabelled control.
 * @property {string} [title] - the tooltip: the gesture, and the key when the
 *   row is focused.
 * @property {boolean} [pressed] - `aria-pressed`, when the tile is a toggle.
 * @property {boolean} [cover] - draws the accent border.
 * @property {boolean} [marked] - draws the warning border: this tile is
 *   evidence the row's primary button is about to act on.
 * @property {boolean} [out] - left out of the stack; the IMAGE fades.
 * @property {boolean} [locked] - a locked set froze it; the image fades and the
 *   tile refuses the pointer.
 * @property {boolean} [lockChip] - whether this tile NAMES the freeze. Defaults
 *   to `locked`, which is the queue row's case: there the lock is a per-unit
 *   fact. On the Mixed stacks row the lock is a whole-STACK fact and the
 *   payload names no member, so every tile is `locked` and only the pictures a
 *   refusal actually named wear the chip; the row's reason line carries the
 *   rest. A chip on every tile of a frozen row would be a lock field, and the
 *   colour would stop meaning anything.
 * @property {boolean} [lockFlash] - one-shot amber on the lock chip.
 * @property {string} [cornerLabel] - bottom-left word chip (`Cover`).
 * @property {string} [markIcon] - bottom-left glyph chip, the alternative
 *   tenant of the same corner.
 * @property {string} [centreIcon] - the centred tick over an excluded tile.
 * @property {{icon: string, text: string, title: string}} [chip] - the
 *   bottom-right hover chip.
 * @property {{width: number, height: number}} [box] - stored dimensions, for
 *   the placeholder's shape estimate only.
 */

const props = defineProps({
  /** One {@link StripTile} per picture the strip draws, in reading order. */
  tiles: { type: Array, default: () => [] },
  /**
   * How tall the strip draws its pictures, from the queue's size control. The
   * strip is laid out from this one number: the box, the placeholder estimate
   * and the panorama ceiling all follow it. `defineProps` is hoisted, so the
   * default is computed from the imported ladder rather than a local constant.
   */
  thumbHeight: {
    type: Number,
    default: stripHeightForSizeLevel(DEFAULT_THUMBNAIL_SIZE_LEVEL),
  },
  /**
   * Whether this strip's row is the one the keyboard acts on.
   *
   * It drives both halves of the roving tab stop: the tiles are reachable by
   * Tab only here, and the index chips (which is what `1`-`9` addresses) are
   * drawn only here. A screenful of twenty rows holds well over a hundred
   * buttons, and a Tab key that walks all of them is a Tab key nobody presses
   * twice.
   */
  focused: { type: Boolean, default: false },
  /**
   * False for a row outside the read-ahead window: the thumbnails are the
   * expensive half of a row, so an off-screen row holds a placeholder box of
   * the same size rather than a decoded image.
   */
  loadThumbnails: { type: Boolean, default: true },
  /**
   * The tile the member cursor is on, or -1 for none.
   *
   * A RAIL under the tile rather than a ring around it, because the border slot
   * is already spent on cover and on the stranger mark. The strip scrolls it
   * into view, since a cursor moved off-screen by a digit is a cursor the user
   * cannot find.
   */
  cursorIndex: { type: Number, default: -1 },
});

const emit = defineEmits(["pick", "toggle"]);

const slots = useSlots();
const stripEl = ref(null);
const tileEls = ref([]);

/** Whether any row filled the top-right column, so an empty one is not drawn. */
const hasTopRight = computed(() => Boolean(slots["top-right"]));

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

/**
 * Whether a tile names the freeze with the lock chip.
 * @param {StripTile} tile
 * @returns {boolean}
 */
function lockChipOn(tile) {
  return tile?.lockChip === undefined ? Boolean(tile?.locked) : tile.lockChip;
}

/**
 * Keep a handle on each tile's element, so the cursor can be scrolled to it.
 * @param {Element|null} el
 * @param {number} index
 */
function registerTile(el, index) {
  tileEls.value[index] = el ?? null;
}

/**
 * The PLACEHOLDER's estimated shape, from stored dimensions. Only an estimate:
 * stored width/height ignore EXIF rotation, so the real image may arrive with
 * the axes swapped; it then sizes the box itself.
 *
 * @param {StripTile} tile
 * @returns {Object|null} an inline width, or null when the shape is unknown.
 */
function thumbBoxStyle(tile) {
  const w = Number(tile?.box?.width);
  const h = Number(tile?.box?.height);
  if (!w || !h) return null;
  const ratio = Math.min(MAX_THUMB_RATIO, Math.max(0.45, w / h));
  return { width: `${Math.round(props.thumbHeight * ratio)}px` };
}

/**
 * Bring the cursor tile into view.
 *
 * The strip is a horizontal scroller, so a digit that moved the cursor past the
 * right edge would otherwise read as a dead key. `nearest` on both axes: the
 * strip must never scroll the page vertically to satisfy a sideways move.
 */
watch(
  () => [props.cursorIndex, props.tiles.length],
  () => {
    if (props.cursorIndex < 0) return;
    const el = tileEls.value[props.cursorIndex];
    el?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  },
);
</script>

<style scoped>
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

/* The cursor rail is drawn one `--space-2` step BELOW the tile, so the strip
   has to carry the room for it. Only a strip that has a cursor pays the height:
   the queue row's pitch is sampled from its own geometry, and a row that grew
   by four pixels for a rail it never draws would move that measurement. */
.gstrip--cursor {
  padding-bottom: var(--space-3);
}

/* One tile's box. It exists so the count badge can be an absolutely-positioned
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

/* The member cursor: a RAIL, not a ring.
   The tile's border already carries two meanings (accent = cover, warning =
   marked as a stranger) and a third would be a third colour on one edge nobody
   could read. The rail sits outside the tile's box entirely, so it composes
   with either border instead of competing with it. */
.gunit--cursor::after {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  bottom: calc(-1 * var(--space-2));
  height: var(--rail-w);
  border-radius: var(--radius-pill);
  background: var(--active-bar);
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

/* Marked as a stranger: the tile the primary button is about to move out.
   Deliberately NOT the excluded fade. A marked tile is the row's EVIDENCE, and
   fading it would say "inert" about the only tiles that are not; the border is
   the 3:1 UI job the warning token is authored for. One treatment for both the
   engine's marks and the user's, because they behave identically and compose
   into the one list the button acts on. */
.gthumb--marked {
  border-color: rgb(var(--v-theme-warning));
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

/* The lock chip and the stranger chip are ONE construction with two glyphs:
   an 18px square on the photo scrim, in the app's single chip recipe (same as
   ReviewBinaryCard's .rs-thumb-lock). Neutral at rest in both cases. The amber
   is spent on the tile's BORDER for a stranger and on a refused press for a
   lock; a chip that was itself amber would turn a page of frozen or marked rows
   into a warning field and the colour would stop meaning anything. */
.glock,
.gmark {
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

/* The stranger chip takes the bottom-left corner, the one `.gcv` uses on the
   queue row; the two never appear together. */
.gmark {
  position: absolute;
  bottom: var(--space-2);
  left: var(--space-2);
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
   here the thumbnail owns its whole gesture vocabulary and an interactive star
   would swallow the click on the very pixels a hover invites. These keep full
   strength on an excluded thumb: the fade is the picture's, not the box's.

   The opacity lives on the OVERLAY, not on the .gtr column, so that one column
   can hold both a hover-only member and a permanent one. The slotted rule
   reaches the row's own star wrapper, which is rendered in the ROW's scope. */
.gsmart,
:slotted(.gstars) {
  opacity: 0;
  transition: opacity var(--dur-1) var(--ease-standard);
  pointer-events: none;
}

/* Hovered on the UNIT, not the button: the stars live in a sibling column (see
   `.gunit`), so a `.gthumb:hover` descendant selector cannot reach them.
   `.gunit` is exactly the tile's box, so the trigger area is unchanged. */
.gunit:hover .gsmart,
.gunit:hover :slotted(.gstars) {
  opacity: 1;
}

/* The bottom-right chip. The grid has NO thumbnail smart-score overlay to reuse
   (it shows the number in the info row under the card), so this mirrors the
   closest shipped recipes instead: this strip's own photo-chip treatment
   (.gnum and .gcv: --scrim-photo fill, --radius-sm, --text-2xs on
   on-dark-surface) and the metadata panel's two-decimal precision. */
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
</style>
