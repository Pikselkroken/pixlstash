<template>
  <span
    class="rs-sticker"
    :class="{ 'rs-sticker--fresh': fresh }"
    :style="stickerStyle"
    :title="label"
  >
    <v-icon :size="iconSize">{{ icon }}</v-icon>
  </span>
</template>

<script setup>
// One die-cut sticker: a Picture Set icon on a Picture Set colour (both from
// setAppearance.js via the store), restyled with a white edge, radial gloss,
// tilt and shadow. `fresh` plays the landing bounce when it arrives in the
// shelf.
import { computed } from "vue";

const props = defineProps({
  icon: { type: String, required: true },
  color: { type: String, required: true },
  label: { type: String, default: "" },
  size: { type: Number, default: 34 },
  tilt: { type: Number, default: -3 },
  fresh: { type: Boolean, default: false },
});

const iconSize = computed(() => Math.round(props.size * 0.56));

const stickerStyle = computed(() => ({
  width: `${props.size}px`,
  height: `${props.size}px`,
  // Radial gloss: a lighter mix of the sticker colour at the highlight point.
  background: `radial-gradient(circle at 32% 28%, color-mix(in srgb, ${props.color} 52%, white) 0%, ${props.color} 58%)`,
  transform: `rotate(${props.tilt}deg)`,
  "--rs-tilt": `${props.tilt}deg`,
}));
</script>

<style scoped>
.rs-sticker {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  border-radius: 50%;
  border: 2.5px solid #fff; /* the die-cut white edge */
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.35);
  color: #fff;
}
.rs-sticker :deep(.v-icon) {
  text-shadow: 0 1px 2px rgba(0, 0, 0, 0.3);
}
.rs-sticker--fresh {
  animation: rs-sticker-land 0.5s cubic-bezier(0.2, 1.4, 0.4, 1) both;
}
@media (prefers-reduced-motion: reduce) {
  .rs-sticker--fresh {
    animation: rs-sticker-fade 0.4s ease both;
  }
}
@keyframes rs-sticker-fade {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}
@keyframes rs-sticker-land {
  0% {
    transform: scale(2.2) rotate(22deg);
    opacity: 0;
  }
  60% {
    transform: scale(0.9) rotate(-7deg);
    opacity: 1;
  }
  100% {
    transform: scale(1) rotate(var(--rs-tilt, -3deg));
    opacity: 1;
  }
}
</style>
