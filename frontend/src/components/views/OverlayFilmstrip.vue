<template>
  <div
    ref="railRef"
    class="overlay-rail"
    :class="{ hidden: hidden }"
  >
    <div
      class="filmstrip-viewport"
      @wheel.prevent.stop="onFilmstripWheel"
    >
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
            },
          ]"
          @click.stop="emit('select', item.index)"
          :title="item.description || 'Image'"
        >
          <div
            class="filmstrip-thumb-tile"
            :style="item.stackTileStyle"
          >
            <img
              v-if="item.thumbSrc"
              :class="[
                'filmstrip-thumb-image',
                { 'filmstrip-thumb-image-active': item.isActive },
              ]"
              :src="item.thumbSrc"
              :alt="item.description || 'Thumbnail'"
              loading="lazy"
              draggable="false"
            />
            <div
              v-else
              :class="[
                'filmstrip-thumb-placeholder',
                { 'filmstrip-thumb-image-active': item.isActive },
              ]"
            >
              <v-icon size="22">
                {{ item.isVideo ? "mdi-video" : "mdi-image" }}
              </v-icon>
            </div>
          </div>
          <div
            v-if="
              item.stackBadgeVisible &&
              item.thumbSrc &&
              item.isStackLead
            "
            class="filmstrip-badge filmstrip-badge--top-left"
            :title="item.stackBadgeTitle"
            @click.stop="emit('toggle-expand', item)"
            @mouseenter.stop="emit('prefetch', item)"
          >
            <v-icon size="14" :style="item.stackIconStyle"
              >mdi-layers</v-icon
            >
          </div>
          <div
            v-if="item.problemBadgeVisible"
            :class="[
              'filmstrip-badge',
              item.stackBadgeVisible
                ? 'filmstrip-badge--top-left-stack'
                : 'filmstrip-badge--top-left',
            ]"
            :title="item.problemTitle"
          >
            <v-icon size="14" color="error"
              >mdi-emoticon-sad-outline</v-icon
            >
          </div>
        </button>
      </transition-group>
    </div>
  </div>
</template>

<script setup>
import { ref } from "vue";

const props = defineProps({
  items: { type: Array, default: () => [] },
  canvasStyle: { type: Object, default: () => ({}) },
  hidden: { type: Boolean, default: false },
});

const emit = defineEmits(["select", "toggle-expand", "prefetch", "navigate"]);

const railRef = ref(null);
defineExpose({ railEl: railRef });

const FILMSTRIP_WHEEL_THRESHOLD = 60;
const WHEEL_LINE_HEIGHT_PX = 16;
const FILMSTRIP_WHEEL_SENSITIVITY = 0.2;
const FILMSTRIP_WHEEL_STEP_COOLDOWN_MS = 30;

let filmstripWheelAccumulator = 0;
let filmstripWheelLastStepTs = 0;

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
  padding: var(--filmstrip-padding, 8px) 6px;
  transition: opacity 0.2s ease;
  overflow: hidden;
  height: calc(100% - var(--topbar-height));
  z-index: 3;
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
  gap: var(--filmstrip-gap, 8px);
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
  border: none;
  padding: 0;
  background: transparent;
  cursor: pointer;
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
  margin-top: calc(-1 * var(--filmstrip-gap, 8px));
}

.filmstrip-thumb-image {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
  border-radius: 8px;
}

.filmstrip-thumb-image-active {
  box-shadow: 0 0 0 4px rgba(var(--v-theme-accent), 0.9);
  z-index: 2;
}

.filmstrip-thumb-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(var(--v-theme-on-dark-surface), 0.08);
  color: rgba(var(--v-theme-on-dark-surface), 0.85);
  border-radius: 8px;
}

.filmstrip-badge {
  position: absolute;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: rgba(var(--v-theme-dark-surface), 0.7);
  border: 1px solid rgba(var(--v-theme-on-dark-surface), 0.35);
  border-radius: 6px;
  padding: 2px 4px;
  color: rgb(var(--v-theme-on-dark-surface));
  box-shadow: 0 2px 6px rgba(var(--v-theme-shadow), 0.3);
  z-index: 2;
}

.filmstrip-badge--top-left {
  top: 8px;
  left: 8px;
}

.filmstrip-badge--top-left-stack {
  top: 28px;
  left: 8px;
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
