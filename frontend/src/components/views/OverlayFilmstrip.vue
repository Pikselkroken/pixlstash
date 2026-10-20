<template>
  <div ref="railRef" class="overlay-rail" :class="{ hidden: hidden }">
    <div class="filmstrip-viewport" @wheel.prevent.stop="onFilmstripWheel">
      <transition-group
        name="filmstrip-slide"
        tag="div"
        class="filmstrip-list"
        :style="canvasStyle"
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
          :aria-pressed="pickedIds.length ? String(isPicked(item)) : undefined"
          @click.stop="onThumbClick(item, $event)"
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
              loading="lazy"
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
</template>

<script setup>
import { reactive, ref } from "vue";
import Tooltip from "../widgets/Tooltip.vue";

const props = defineProps({
  items: { type: Array, default: () => [] },
  canvasStyle: { type: Object, default: () => ({}) },
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
    { where: "later", label: "Move later", icon: "mdi-arrow-down", disabled: last },
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
  "navigate",
  "stack-move",
  "pick",
]);

const railRef = ref(null);
defineExpose({ railEl: railRef, resetWheel });

const FILMSTRIP_WHEEL_THRESHOLD = 60;
const WHEEL_LINE_HEIGHT_PX = 16;
const FILMSTRIP_WHEEL_SENSITIVITY = 0.2;
const FILMSTRIP_WHEEL_STEP_COOLDOWN_MS = 30;

let filmstripWheelAccumulator = 0;
let filmstripWheelLastStepTs = 0;

function resetWheel() {
  filmstripWheelAccumulator = 0;
  filmstripWheelLastStepTs = 0;
}

function normalizeWheelDelta(event) {
  if (!event) return 0;
  const raw = Number(event.deltaY ?? 0);
  if (!Number.isFinite(raw) || raw === 0) return 0;
  const scaled = raw * FILMSTRIP_WHEEL_SENSITIVITY;
  if (event.deltaMode === 1) {
    return scaled * WHEEL_LINE_HEIGHT_PX;
  }
  if (event.deltaMode === 2) {
    return scaled * (window.innerHeight || 800);
  }
  return scaled;
}

function onFilmstripWheel(event) {
  const now = Date.now();
  if (now - filmstripWheelLastStepTs < FILMSTRIP_WHEEL_STEP_COOLDOWN_MS) {
    return;
  }
  const deltaY = normalizeWheelDelta(event);
  if (!Number.isFinite(deltaY) || deltaY === 0) return;
  filmstripWheelAccumulator += deltaY;
  if (Math.abs(filmstripWheelAccumulator) < FILMSTRIP_WHEEL_THRESHOLD) {
    return;
  }
  const direction = Math.sign(filmstripWheelAccumulator);
  filmstripWheelAccumulator -= direction * FILMSTRIP_WHEEL_THRESHOLD;
  filmstripWheelLastStepTs = now;
  emit("navigate", direction);
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
  overflow: hidden;
  align-self: center;
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
  min-height: 100%;
  transition: transform 0.34s cubic-bezier(0.22, 1, 0.36, 1);
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

.filmstrip-slide-enter-active,
.filmstrip-slide-leave-active {
  transition: opacity 0.2s ease-out;
}

.filmstrip-slide-enter-from,
.filmstrip-slide-leave-to {
  opacity: 0;
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
.filmstrip-thumb--picked .filmstrip-thumb-tile {
  outline: 3px solid rgb(var(--v-theme-dark-surface-tertiary));
  outline-offset: 2px;
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
