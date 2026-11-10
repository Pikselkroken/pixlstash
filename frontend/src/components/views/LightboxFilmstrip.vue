<template>
  <!-- A band under the stage (#2163): the neighbouring pictures at their own
       aspect ratios, the one on screen ringed in olive and the rest dimmed. -->
  <div class="lightbox-rail" :class="{ hidden: hidden }">
    <div
      ref="viewportRef"
      class="filmstrip-viewport"
      role="group"
      aria-label="Filmstrip"
      @scroll.passive="onViewportScroll"
      @wheel="onViewportWheel"
      @keydown="onViewportKeydown"
    >
      <div class="filmstrip-sizer" :style="{ width: `${totalWidth}px` }">
        <!-- No enter/leave transitions: Vue keeps a leaving thumb in the DOM
             for two frames even with no CSS. Moves (stack reorder) still
             animate. -->
        <transition-group
          name="filmstrip-slide"
          tag="div"
          :css="false"
          class="filmstrip-list"
        >
          <button
            v-for="item in items"
            :key="item.id ? `filmstrip-${item.id}` : `filmstrip-${item.index}`"
            :class="[
              'filmstrip-thumb',
              {
                'filmstrip-thumb--active': item.isActive,
                'filmstrip-thumb--picked': isPicked(item),
              },
            ]"
            :style="slotStyle(item.index)"
            :aria-current="item.isActive ? 'true' : undefined"
            :aria-pressed="
              pickedIds.length ? String(isPicked(item)) : undefined
            "
            @click.stop="onThumbClick(item, $event)"
            @contextmenu.prevent.stop="emit('context-menu', item, $event)"
          >
            <Tooltip
              :text="item.description || 'Image'"
              activator="parent"
              :describe="false"
            />
            <div class="filmstrip-thumb-tile" :style="item.stackTileStyle">
              <img
                v-if="item.thumbSrc && !isThumbBroken(item)"
                class="filmstrip-thumb-image"
                :src="item.thumbSrc"
                :alt="item.description || 'Thumbnail'"
                decoding="async"
                draggable="false"
                @error="onThumbError(item)"
              />
              <div v-else class="filmstrip-thumb-placeholder">
                <v-icon :size="isThumbBroken(item) ? 22 : 18">
                  {{
                    isThumbBroken(item)
                      ? "mdi-image-broken-variant"
                      : item.isVideo
                        ? "mdi-video"
                        : "mdi-image"
                  }}
                </v-icon>
              </div>
            </div>
            <div
              v-if="isPicked(item)"
              class="filmstrip-badge filmstrip-badge--pick"
              aria-hidden="true"
            >
              {{ pickLetter(item) }}
            </div>
            <div
              v-if="item.stackBadgeVisible && item.thumbSrc && item.isStackLead"
              class="filmstrip-badge filmstrip-badge--top-left"
              @click.stop="emit('toggle-expand', item)"
              @mouseenter.stop="emit('prefetch', item)"
            >
              <Tooltip :text="item.stackBadgeTitle" activator="parent" />
              <v-icon size="12" :style="item.stackIconStyle">mdi-layers</v-icon>
            </div>
            <div
              v-if="item.problemBadgeVisible"
              class="filmstrip-badge filmstrip-badge--top-right"
            >
              <Tooltip :text="item.problemTitle" activator="parent" />
              <v-icon size="12" color="dark-surface-error"
                >mdi-emoticon-sad-outline</v-icon
              >
            </div>
            <!-- Move earlier / Make cover / Move later on a picture of a stack
                 opened here. Spans, not buttons: the thumb is itself a
                 button. -->
            <div
              v-if="item.stackPlace && !readOnly"
              class="filmstrip-stack-bar"
              role="group"
              aria-label="Stack order"
            >
              <span
                v-for="handle in stackHandles(item.stackPlace)"
                :key="handle.where"
                class="filmstrip-stack-btn"
                :class="{
                  'filmstrip-stack-btn--cover': handle.where === 'cover',
                  'filmstrip-stack-btn--disabled': handle.disabled,
                }"
                role="button"
                :aria-label="handle.label"
                :aria-disabled="handle.disabled ? 'true' : undefined"
                @click.stop="
                  !handle.disabled && emit('stack-move', item, handle.where)
                "
              >
                <Tooltip :text="handle.label" activator="parent" />
                <v-icon size="14">{{ handle.icon }}</v-icon>
              </span>
            </div>
          </button>
        </transition-group>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from "vue";
import Tooltip from "../widgets/Tooltip.vue";

const props = defineProps({
  // The thumbs to draw: a window of the strip, each carrying its `index`.
  items: { type: Array, default: () => [] },
  // Every thumb's left edge and width in px, for the whole strip and not just
  // the window, so the strip scrolls natively over its full length.
  offsets: { type: Array, default: () => [] },
  widths: { type: Array, default: () => [] },
  hidden: { type: Boolean, default: false },
  readOnly: { type: Boolean, default: false },
  // Ids Ctrl/Cmd-clicked for Compare, in the order picked.
  pickedIds: { type: Array, default: () => [] },
});

const emit = defineEmits([
  "select",
  "toggle-expand",
  "prefetch",
  "visible-range",
  "stack-move",
  "pick",
  "context-menu",
]);

function isPicked(item) {
  return item.id != null && props.pickedIds.includes(String(item.id));
}

// A and B: which side of the comparison each pick lands on.
function pickLetter(item) {
  const at = props.pickedIds.indexOf(String(item.id));
  return at === -1 ? "" : String.fromCharCode(65 + at);
}

// Ctrl or Cmd picks a thumb for Compare; a plain click goes to it.
function onThumbClick(item, event) {
  if (event.ctrlKey || event.metaKey) emit("pick", item);
  else emit("select", item.index);
}

function stackHandles(place) {
  const last = place.position >= place.count - 1;
  return [
    {
      where: "earlier",
      label: "Move earlier",
      icon: "mdi-arrow-left",
      disabled: place.position === 0,
    },
    {
      where: "cover",
      label: "Make this the cover",
      icon: "mdi-star-outline",
      disabled: place.position === 0,
    },
    {
      where: "later",
      label: "Move later",
      icon: "mdi-arrow-right",
      disabled: last,
    },
  ];
}

// Thumbs whose <img> fired @error (e.g. an undecodable source, #585): show a
// centered broken-image icon instead of the browser's alt-text rendering.
const failedThumbKeys = reactive(new Set());

function thumbKey(item) {
  return item.id ?? `idx-${item.index}`;
}

function onThumbError(item) {
  failedThumbKeys.add(thumbKey(item));
}

function isThumbBroken(item) {
  return failedThumbKeys.has(thumbKey(item));
}

const viewportRef = ref(null);
defineExpose({ reveal });

const totalWidth = computed(() => {
  const n = props.offsets.length;
  return n ? props.offsets[n - 1] + (props.widths[n - 1] || 0) : 0;
});

function slotStyle(index) {
  return {
    left: `${props.offsets[index] ?? 0}px`,
    width: `${props.widths[index] ?? 0}px`,
  };
}

/** The last thumb whose left edge is at or before `x`. */
function indexAt(x) {
  const offsets = props.offsets;
  let lo = 0;
  let hi = offsets.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (offsets[mid] <= x) lo = mid;
    else hi = mid - 1;
  }
  return Math.max(0, lo);
}

// Scrolled natively, as the grid is: the parent draws the thumbs around the
// range reported here.
function onViewportScroll() {
  const el = viewportRef.value;
  if (!el || !props.offsets.length) return;
  emit("visible-range", {
    first: indexAt(el.scrollLeft),
    last: indexAt(el.scrollLeft + el.clientWidth),
  });
}

// The strip widens when the rail shuts or the window grows, with no scroll to
// say so: report the range again, or the far end is drawn blank. Observing
// also reports once on mount.
let viewportObserver = null;
onMounted(() => {
  if (typeof ResizeObserver === "undefined" || !viewportRef.value) return;
  viewportObserver = new ResizeObserver(() => onViewportScroll());
  viewportObserver.observe(viewportRef.value);
});
onUnmounted(() => viewportObserver?.disconnect());

// A mouse wheel turns vertically; across a horizontal strip it should move
// the strip. A trackpad's own sideways swipe is left alone.
const WHEEL_LINE_PX = 40;
function onViewportWheel(event) {
  const el = viewportRef.value;
  if (!el || Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
  event.preventDefault();
  // Lines (Firefox's wheel) and pages are not pixels.
  const unit =
    event.deltaMode === 1 ? WHEEL_LINE_PX : event.deltaMode === 2 ? el.clientWidth : 1;
  el.scrollLeft += event.deltaY * unit;
}

// The arrow keys step the lightbox's picture (its window listener still gets
// them); with a thumb focused they must not also scroll the strip natively,
// or it drifts away from the picture they step to.
const STEP_KEYS = ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"];

function onViewportKeydown(event) {
  if (STEP_KEYS.includes(event.key)) event.preventDefault();
}

function scrollViewportTo(left, smooth) {
  const el = viewportRef.value;
  const max = Math.max(0, totalWidth.value - el.clientWidth);
  const target = Math.min(Math.max(0, left), max);
  if (smooth && typeof el.scrollTo === "function") {
    el.scrollTo({ left: target, behavior: "smooth" });
  } else {
    el.scrollLeft = target;
    // Report at once, so the window is drawn in the same frame as the jump.
    onViewportScroll();
  }
}

/**
 * Bring thumb `index` into view. In view, nothing moves; a thumb at or just
 * past an edge scrolls smoothly by the least that shows it whole; anything
 * further, or `centre`, jumps to centre it.
 */
function reveal(index, { centre = false } = {}) {
  const el = viewportRef.value;
  if (!el || !(index >= 0) || index >= props.offsets.length) return;
  const left = props.offsets[index];
  const width = props.widths[index] || 0;
  const right = left + width;
  const viewLeft = el.scrollLeft;
  const viewRight = viewLeft + el.clientWidth;
  if (!centre && left >= viewLeft && right <= viewRight) return;
  if (!centre && left >= viewLeft - width && right <= viewRight + width) {
    scrollViewportTo(left < viewLeft ? left : right - el.clientWidth, true);
  } else {
    scrollViewportTo(left - (el.clientWidth - width) / 2, false);
  }
}
</script>

<style scoped>
/* The band's height and its thumbs' are the design's layout fixtures
   (#2163): 56px pictures with 8px of band either side. */
.lightbox-rail {
  --filmstrip-thumb-h: 56px;

  flex: 0 0 72px;
  display: flex;
  min-width: 0;
  background: rgb(var(--v-theme-dark-surface));
  border-top: 1px solid rgba(var(--v-theme-on-dark-surface), 0.12);
  transition: opacity var(--dur-2) var(--ease-standard);
}

.lightbox-rail.hidden {
  opacity: 0;
  pointer-events: none;
}

.filmstrip-viewport {
  flex: 1;
  min-width: 0;
  overflow-x: auto;
  overflow-y: hidden;
  overscroll-behavior: contain;
  scrollbar-width: none;
}

.filmstrip-viewport::-webkit-scrollbar {
  display: none;
}

/* Short strips sit in the middle of the band; a long one starts at its
   left edge and scrolls. No padding on the viewport: `reveal` measures the
   thumbs from the scroll origin. */
.filmstrip-sizer {
  position: relative;
  height: 100%;
  margin: 0 auto;
}

.filmstrip-list {
  position: relative;
  height: 100%;
}

.filmstrip-thumb {
  position: absolute;
  top: 0;
  bottom: 0;
  padding: 0;
  border-radius: 0;
}

.filmstrip-slide-move {
  transition: transform 0.34s cubic-bezier(0.22, 1, 0.36, 1);
}

/* A stack opened here wears its colour behind its members, which sit with no
   gap between them, so the run reads as one object. */
.filmstrip-thumb-tile {
  height: 100%;
  display: flex;
  align-items: center;
  background: var(--filmstrip-stack-bg, transparent);
}

.filmstrip-thumb-image,
.filmstrip-thumb-placeholder {
  width: 100%;
  height: var(--filmstrip-thumb-h);
  display: block;
  opacity: var(--opacity-text-secondary);
  transition: opacity var(--dur-1) var(--ease-standard);
}

.filmstrip-thumb-image {
  object-fit: cover;
}

.filmstrip-thumb-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgba(var(--v-theme-on-dark-surface), 0.85);
}

.filmstrip-thumb:hover .filmstrip-thumb-image,
.filmstrip-thumb:hover .filmstrip-thumb-placeholder,
.filmstrip-thumb--active .filmstrip-thumb-image,
.filmstrip-thumb--active .filmstrip-thumb-placeholder {
  opacity: 1;
}

/* The picture on screen: the selection's olive, as a ring. */
.filmstrip-thumb--active .filmstrip-thumb-image,
.filmstrip-thumb--active .filmstrip-thumb-placeholder {
  outline: var(--focus-width) solid rgb(var(--v-theme-dark-surface-primary));
  outline-offset: calc(-1 * var(--focus-width));
}

/* Picked for Compare: the comparison's teal, so it never reads as the olive
   of the picture on screen. */
.filmstrip-thumb--picked .filmstrip-thumb-image,
.filmstrip-thumb--picked .filmstrip-thumb-placeholder {
  opacity: 1;
  box-shadow: inset 0 0 0 var(--focus-width)
    rgb(var(--v-theme-dark-surface-tertiary));
}

.filmstrip-badge {
  position: absolute;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  padding: var(--space-1);
  background: var(--scrim-photo-strong);
  color: rgb(var(--v-theme-on-dark-surface));
  z-index: var(--z-raised);
}

.filmstrip-badge--top-left {
  top: calc(var(--space-3) + var(--space-1));
  left: var(--space-1);
}

.filmstrip-badge--top-right {
  top: calc(var(--space-3) + var(--space-1));
  right: var(--space-1);
}

.filmstrip-badge--pick {
  bottom: calc(var(--space-3) + var(--space-1));
  right: var(--space-1);
  min-width: 16px;
  padding: 0 var(--space-2);
  background: rgb(var(--v-theme-tertiary));
  color: rgb(var(--v-theme-on-tertiary));
  font-size: var(--text-2xs);
  font-weight: var(--weight-medium);
}

/* Wider than a narrow thumb, so it lies over its neighbours while shown. */
.filmstrip-stack-bar {
  position: absolute;
  bottom: var(--space-1);
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  gap: var(--space-1);
  padding: var(--space-1);
  border-radius: var(--radius-pill);
  background: var(--scrim-photo-strong);
  z-index: var(--z-raised);
  opacity: 0;
  pointer-events: none;
  transition: opacity var(--dur-1) var(--ease-standard);
}

.filmstrip-thumb:hover .filmstrip-stack-bar,
.filmstrip-thumb:focus-visible .filmstrip-stack-bar {
  opacity: 1;
  pointer-events: auto;
}

.filmstrip-stack-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--control-h-sm);
  height: var(--control-h-sm);
  border-radius: var(--radius-pill);
  color: rgb(var(--v-theme-on-dark-surface));
  cursor: pointer;
}

.filmstrip-stack-btn:hover:not(.filmstrip-stack-btn--disabled) {
  background: rgba(var(--v-theme-on-dark-surface), 0.16);
}

.filmstrip-stack-btn--cover:not(.filmstrip-stack-btn--disabled) {
  background: rgba(var(--v-theme-dark-surface-accent), 0.28);
}

.filmstrip-stack-btn--disabled {
  opacity: var(--opacity-disabled);
  cursor: default;
}
</style>
