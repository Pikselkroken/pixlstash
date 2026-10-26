<template>
  <div ref="railRef" class="overlay-rail" :class="{ hidden: hidden }">
    <div
      ref="viewportRef"
      class="filmstrip-viewport"
      @scroll.passive="onViewportScroll"
      @keydown="onViewportKeydown"
    >
      <div class="filmstrip-sizer" :style="sizerStyle">
        <transition-group
          name="filmstrip-slide"
          tag="div"
          class="filmstrip-list"
          :style="listStyle"
        >
          <button
            v-for="item in items"
            :key="item.id ? `filmstrip-${item.id}` : `filmstrip-${item.index}`"
            :class="[
              'filmstrip-thumb',
              {
                'filmstrip-thumb-stack-joined': item.isStackJoined,
                'filmstrip-thumb--picked': isPicked(item),
              },
            ]"
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
                :class="[
                  'filmstrip-thumb-image',
                  { 'filmstrip-thumb-image-active': item.isActive },
                ]"
                :src="item.thumbSrc"
                :alt="item.description || 'Thumbnail'"
                decoding="async"
                draggable="false"
                @error="onThumbError(item)"
              />
              <div
                v-else
                :class="[
                  'filmstrip-thumb-placeholder',
                  { 'filmstrip-thumb-image-active': item.isActive },
                ]"
              >
                <v-icon :size="isThumbBroken(item) ? 28 : 22">
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
              <v-icon size="14" :style="item.stackIconStyle">mdi-layers</v-icon>
            </div>
            <div
              v-if="item.problemBadgeVisible"
              :class="[
                'filmstrip-badge',
                item.stackBadgeVisible
                  ? 'filmstrip-badge--top-left-stack'
                  : 'filmstrip-badge--top-left',
              ]"
            >
              <Tooltip :text="item.problemTitle" activator="parent" />
              <v-icon size="14" color="dark-surface-error"
                >mdi-emoticon-sad-outline</v-icon
              >
            </div>
            <!-- Move earlier / Make cover / Move later on a picture of a stack
                 opened here, stacked like the rail. Spans, not buttons: the
                 thumb is itself a button. -->
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
import { computed, reactive, ref } from "vue";
import Tooltip from "../widgets/Tooltip.vue";

const props = defineProps({
  // The thumbs to draw: a window of the strip, each carrying its `index`.
  items: { type: Array, default: () => [] },
  // Native scrolling over a spacer as tall as all `total` thumbs, the window
  // placed at `start`; off on the narrow layout, which draws `items` as is.
  virtual: { type: Boolean, default: false },
  total: { type: Number, default: 0 },
  start: { type: Number, default: 0 },
  slotSize: { type: Number, default: 0 },
  hidden: { type: Boolean, default: false },
  readOnly: { type: Boolean, default: false },
  // Ids Ctrl/Cmd-clicked for Compare, in the order picked.
  pickedIds: { type: Array, default: () => [] },
});

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
      icon: "mdi-arrow-up",
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
      icon: "mdi-arrow-down",
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

const emit = defineEmits([
  "select",
  "toggle-expand",
  "prefetch",
  "first-visible",
  "stack-move",
  "pick",
  "context-menu",
]);

const railRef = ref(null);
const viewportRef = ref(null);
defineExpose({ railEl: railRef, reveal });

// Scrolled natively, as the grid is: the wheel and a trackpad move it by the
// pixel, and the parent renders the thumbs around `first-visible`.
const sizerStyle = computed(() =>
  props.virtual ? { height: `${props.total * props.slotSize}px` } : {},
);
const listStyle = computed(() =>
  props.virtual
    ? { transform: `translateY(${props.start * props.slotSize}px)` }
    : {},
);

function onViewportScroll() {
  const el = viewportRef.value;
  if (!el || !props.slotSize) return;
  emit("first-visible", Math.floor(el.scrollTop / props.slotSize));
}

// The arrow keys step the lightbox's picture (its window listener still gets
// them); with a thumb focused they must not also scroll the strip natively,
// or it drifts away from the picture they step to.
const STEP_KEYS = ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"];

function onViewportKeydown(event) {
  if (STEP_KEYS.includes(event.key)) event.preventDefault();
}

function scrollViewportTo(top, smooth) {
  const el = viewportRef.value;
  const max = Math.max(0, props.total * props.slotSize - el.clientHeight);
  const target = Math.min(Math.max(0, top), max);
  if (smooth && typeof el.scrollTo === "function") {
    el.scrollTo({ top: target, behavior: "smooth" });
  } else {
    el.scrollTop = target;
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
  const slot = props.slotSize;
  if (!props.virtual || !el || !slot || !(index >= 0)) return;
  const top = index * slot;
  const bottom = top + slot;
  const viewTop = el.scrollTop;
  const viewBottom = viewTop + el.clientHeight;
  if (!centre && top >= viewTop && bottom <= viewBottom) return;
  if (!centre && top >= viewTop - slot && bottom <= viewBottom + slot) {
    scrollViewportTo(top < viewTop ? top : bottom - el.clientHeight, true);
  } else {
    scrollViewportTo(top - (el.clientHeight - slot) / 2, false);
  }
}
</script>

<style scoped>
.overlay-rail {
  position: absolute;
  top: var(--topbar-height);
  left: 0;
  bottom: 0;
  width: var(--filmstrip-rail-width, var(--rail-open-width));
  background: rgba(var(--v-theme-dark-surface), 0.9);
  border-left: 1px solid rgba(var(--v-theme-on-dark-surface), 0.08);
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: var(--filmstrip-padding, var(--space-3)) 6px;
  transition: opacity 0.2s ease;
  overflow: hidden;
  height: calc(100% - var(--topbar-height));
  z-index: var(--z-raised);
}

.overlay-rail.hidden {
  opacity: 0;
  pointer-events: none;
}

.filmstrip-viewport {
  width: var(--filmstrip-thumb-size, 100%);
  height: 100%;
  overflow-x: hidden;
  overflow-y: auto;
  overscroll-behavior: contain;
  scrollbar-width: none;
  align-self: center;
}

.filmstrip-viewport::-webkit-scrollbar {
  display: none;
}

.filmstrip-sizer {
  position: relative;
  width: 100%;
}

.filmstrip-list {
  display: flex;
  flex-direction: column;
  gap: var(--filmstrip-gap, var(--space-3));
  overflow-y: visible;
  width: var(--filmstrip-thumb-size, 100%);
  align-items: center;
  overflow-x: visible;
  align-self: center;
  padding-right: 0;
  box-sizing: border-box;
}

.filmstrip-thumb {
  padding: 0;
  border-radius: 0;
  overflow: visible;
  width: var(--filmstrip-thumb-size, 100%);
  height: var(--filmstrip-thumb-size, auto);
  max-width: 100%;
  aspect-ratio: 1 / 1;
  position: relative;
}

.filmstrip-slide-move {
  transition: transform 0.34s cubic-bezier(0.22, 1, 0.36, 1);
}

.filmstrip-thumb-tile {
  width: 100%;
  height: 100%;
  background: var(--filmstrip-stack-bg, transparent);
  padding: 6px;
  box-sizing: border-box;
  border-radius: 0;
  overflow: visible;
}

.filmstrip-thumb-stack-joined {
  margin-top: calc(-1 * var(--filmstrip-gap, var(--space-3)));
}

.filmstrip-thumb-image {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  border-radius: var(--radius-md);
}

.filmstrip-thumb-image-active {
  box-shadow: 0 0 0 4px rgb(var(--v-theme-dark-surface-primary));
  z-index: var(--z-raised);
}

.filmstrip-thumb-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgba(var(--v-theme-on-dark-surface), 0.85);
  border-radius: var(--radius-md);
}

.filmstrip-badge {
  position: absolute;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: rgba(var(--v-theme-dark-surface), 0.7);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.35);
  border-radius: 6px;
  padding: var(--space-1) var(--space-2);
  color: rgb(var(--v-theme-on-dark-surface));
  box-shadow: 0 2px 6px rgba(var(--v-theme-shadow), 0.3);
  z-index: var(--z-raised);
}

.filmstrip-badge--top-left {
  top: 8px;
  left: 8px;
}

/* Picked for Compare: the comparison's teal, so it never reads as the olive
   of the picture on screen. */
.filmstrip-thumb--picked .filmstrip-thumb-image,
.filmstrip-thumb--picked .filmstrip-thumb-placeholder {
  box-shadow: 0 0 0 3px rgb(var(--v-theme-dark-surface-tertiary));
}

.filmstrip-thumb--picked .filmstrip-thumb-image-active {
  box-shadow:
    0 0 0 4px rgb(var(--v-theme-dark-surface-primary)),
    0 0 0 7px rgb(var(--v-theme-dark-surface-tertiary));
}

.filmstrip-badge--pick {
  bottom: var(--space-3);
  right: var(--space-3);
  min-width: 20px;
  padding: 0 var(--space-2);
  border: none;
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-tertiary));
  color: rgb(var(--v-theme-on-tertiary));
  font-size: var(--text-2xs);
  font-weight: var(--weight-medium);
}

.filmstrip-badge--top-left-stack {
  top: 28px;
  left: 8px;
}

.filmstrip-stack-bar {
  position: absolute;
  right: var(--space-2);
  top: 50%;
  transform: translateY(-50%);
  display: flex;
  flex-direction: column;
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

@media (max-width: 720px) {
  .overlay-rail {
    position: absolute;
    top: auto;
    bottom: 0;
    left: 0;
    right: 0;
    width: 100%;
    height: 100px;
    flex-direction: row;
    justify-content: flex-start;
    padding: 6px 10px;
  }

  .overlay-rail.open {
    width: 100%;
  }

  .filmstrip-sizer {
    height: 100%;
  }

  .filmstrip-list {
    min-height: 0;
    flex-direction: row;
    overflow: visible;
    width: 100%;
    transform: none !important;
    transition: none;
  }

  .filmstrip-viewport {
    width: 100%;
    height: 100%;
    overflow-x: auto;
    overflow-y: hidden;
  }

  .filmstrip-thumb {
    flex: 1 1 0;
    min-width: 0;
    width: auto;
    height: 100%;
    aspect-ratio: unset;
  }

  .filmstrip-thumb img {
    height: 100%;
    width: 100%;
  }
}
</style>
