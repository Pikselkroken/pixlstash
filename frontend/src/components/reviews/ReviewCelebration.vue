<template>
  <!-- Ordinary decisions: a small tick/glow pulse only (full fireworks are
       reserved for sticker moments). Purely visual - pointer-events: none. -->
  <div
    v-if="on && glowKey"
    :key="`glow-${glowKey}`"
    class="rs-glow"
    aria-hidden="true"
  >
    <v-icon size="22" class="rs-glow-icon">mdi-check-circle-outline</v-icon>
  </div>

  <!-- Sticker moment: fireworks + rising stars + a cheer line. Re-keyed per
       burst so the CSS animations restart. Driven by the EXPLICIT award event
       (never derived state), so undo can never trigger it. -->
  <div
    v-if="on && burst && !reducedMotion"
    :key="burst.key"
    class="rs-celebrate"
    aria-hidden="true"
  >
    <div class="rs-celebrate-origin">
      <span
        v-for="s in burst.sparks"
        :key="s.id"
        class="rs-spark"
        :style="{
          background: s.c,
          '--dx': `${s.dx}px`,
          '--dy': `${s.dy}px`,
        }"
      ></span>
    </div>
    <v-icon
      v-for="s in burst.stars"
      :key="`star-${s.id}`"
      class="rs-star"
      size="26"
      :style="{
        left: `${s.left}%`,
        color: s.c,
        animationDelay: `${s.delay}s`,
      }"
      >mdi-star</v-icon
    >
    <div class="rs-cheer">{{ burst.msg }}</div>
  </div>

  <!-- Sticker award: pops near the rail/header edge (never over the card
       centre), holds ~500ms, then flies down toward the shelf where the fresh
       copy lands. Reduced motion: a static fade-in/out instead. -->
  <div
    v-if="award"
    :key="award.id"
    class="rs-award"
    :class="{ 'rs-award--reduced': reducedMotion }"
    aria-hidden="true"
  >
    <div class="rs-award-fly">
      <div class="rs-award-pop">
        <ReviewSticker
          :icon="award.icon"
          :color="award.color"
          :label="award.label"
          :size="96"
          :tilt="-5"
        />
      </div>
      <span class="rs-award-text">{{ award.label }} sticker earned!</span>
    </div>
  </div>
</template>

<script setup>
// Over-the-top encouragement for a genuinely tedious chore ("Pretend this is
// fun"). Fireworks fire ONLY on sticker awards; ordinary decisions get a small
// tick/glow. Everything is gated behind prefers-reduced-motion.
import { onUnmounted, ref, watch } from "vue";
import ReviewSticker from "./ReviewSticker.vue";

const props = defineProps({
  on: { type: Boolean, default: false },
  // Bumps on every real decision (explicit event from the store).
  tick: { type: Number, default: 0 },
  // The sticker currently mid pop→fly animation (store.activeAward), or null.
  award: { type: Object, default: null },
});

const MSGS = [
  "You’re doing great! 🎉",
  "You can do it! ⭐",
  "Tag hero! 🚀",
  "Unstoppable! 🔥",
  "Look at you go! ✨",
  "Legendary! 🏆",
];
const COLORS = ["#ffd166", "#06d6a0", "#ef476f", "#4cc9f0", "#f78c6b"];

const reducedMotion = ref(false);
const mq = window.matchMedia?.("(prefers-reduced-motion: reduce)") ?? null;
function onMqChange() {
  reducedMotion.value = !!mq?.matches;
}
if (mq) {
  onMqChange();
  mq.addEventListener?.("change", onMqChange);
}
onUnmounted(() => mq?.removeEventListener?.("change", onMqChange));

const burst = ref(null);
const glowKey = ref(0);
let glowTimer = null;

// Ordinary decision → small glow pulse (skipped entirely under reduced motion).
watch(
  () => props.tick,
  (tick, prev) => {
    if (!props.on || tick == null || tick === prev) return;
    if (reducedMotion.value) return;
    if (props.award) return; // the sticker moment owns this decision's feedback
    glowKey.value = Date.now();
    if (glowTimer) clearTimeout(glowTimer);
    glowTimer = setTimeout(() => {
      glowKey.value = 0;
    }, 700);
  },
);

// Sticker moment → the full burst.
watch(
  () => props.award?.id,
  (id) => {
    if (!props.on || !id || reducedMotion.value) return;
    const sparks = Array.from({ length: 22 }, (_, i) => {
      const a = (Math.PI * 2 * i) / 22 + Math.random();
      const r = 90 + Math.random() * 130;
      return {
        id: i,
        dx: Math.cos(a) * r,
        dy: Math.sin(a) * r,
        c: COLORS[i % COLORS.length],
      };
    });
    const stars = Array.from({ length: 10 }, (_, i) => ({
      id: i,
      left: 6 + Math.random() * 88,
      delay: Math.random() * 0.5,
      c: COLORS[i % COLORS.length],
    }));
    burst.value = {
      msg: MSGS[Math.floor(Math.random() * MSGS.length)],
      sparks,
      stars,
      key: `burst-${id}`,
    };
  },
);

onUnmounted(() => {
  if (glowTimer) clearTimeout(glowTimer);
});
</script>

<style scoped>
.rs-glow {
  position: absolute;
  top: 14px;
  right: 20px;
  pointer-events: none;
  z-index: 5;
  color: rgb(var(--v-theme-dark-surface-success));
  animation: rs-glow 0.65s ease-out forwards;
}

.rs-celebrate {
  position: absolute;
  inset: 0;
  pointer-events: none;
  overflow: hidden;
  z-index: 5;
}
.rs-celebrate-origin {
  position: absolute;
  top: 30%;
  left: 24%;
}
.rs-spark {
  position: absolute;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  animation: rs-firework 0.9s ease-out forwards;
}
.rs-star {
  position: absolute;
  bottom: 60px;
  animation: rs-star-rise 1.5s ease-out forwards;
  opacity: 0;
}
.rs-cheer {
  position: absolute;
  top: 16%;
  left: 24%;
  transform: translateX(-50%);
  animation: rs-cheer 1.6s ease-out forwards;
  font-size: 26px;
  font-weight: 800;
  white-space: nowrap;
  color: rgb(var(--v-theme-on-dark-surface));
  text-shadow: 0 2px 18px rgba(0, 0, 0, 0.35);
}

/* Award plays near the rail/header edge - top-left of the session area, out of
   the card's way - then flies down-left toward the shelf. */
.rs-award {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 7;
  overflow: hidden;
}
.rs-award-fly {
  position: absolute;
  top: 12%;
  left: 60px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  /* Fly toward the rail's sticker shelf (down-left), after the ~500ms hold. */
  --fx: -110px;
  --fy: 58vh;
  animation: rs-sticker-fly 0.45s ease-in 0.9s forwards;
}
.rs-award-pop {
  animation: rs-sticker-pop 0.45s cubic-bezier(0.2, 1.6, 0.4, 1) both;
}
.rs-award-text {
  font-size: 14px;
  font-weight: 800;
  white-space: nowrap;
  color: rgb(var(--v-theme-on-dark-surface));
  text-shadow: 0 2px 14px rgba(0, 0, 0, 0.35);
}

/* Reduced motion: no pop, no flight - a gentle fade-in/out in place. */
.rs-award--reduced .rs-award-fly {
  animation: rs-award-fade 1.4s ease both;
}
.rs-award--reduced .rs-award-pop {
  animation: none;
}

@keyframes rs-glow {
  0% {
    transform: scale(0.6);
    opacity: 0;
  }
  30% {
    transform: scale(1.15);
    opacity: 1;
  }
  100% {
    transform: scale(1);
    opacity: 0;
  }
}
@keyframes rs-firework {
  0% {
    transform: translate(0, 0) scale(0.2);
    opacity: 1;
  }
  100% {
    transform: translate(var(--dx), var(--dy)) scale(1);
    opacity: 0;
  }
}
@keyframes rs-star-rise {
  0% {
    transform: translateY(0) scale(0) rotate(0deg);
    opacity: 0;
  }
  20% {
    opacity: 1;
  }
  100% {
    transform: translateY(-120px) scale(1) rotate(200deg);
    opacity: 0;
  }
}
@keyframes rs-cheer {
  0% {
    transform: translate(-50%, 14px) scale(0.85);
    opacity: 0;
  }
  15%,
  80% {
    transform: translate(-50%, 0) scale(1);
    opacity: 1;
  }
  100% {
    transform: translate(-50%, -10px) scale(1.04);
    opacity: 0;
  }
}
@keyframes rs-sticker-pop {
  0% {
    transform: scale(0) rotate(-30deg);
    opacity: 0;
  }
  55% {
    transform: scale(1.18) rotate(6deg);
    opacity: 1;
  }
  100% {
    transform: scale(1) rotate(-4deg);
    opacity: 1;
  }
}
@keyframes rs-sticker-fly {
  0% {
    transform: translate(0, 0) scale(1);
    opacity: 1;
  }
  100% {
    transform: translate(var(--fx), var(--fy)) scale(0.2);
    opacity: 0;
  }
}
@keyframes rs-award-fade {
  0% {
    opacity: 0;
  }
  15%,
  80% {
    opacity: 1;
  }
  100% {
    opacity: 0;
  }
}

/* Belt-and-braces: if reduced motion is on, no keyframe animation in this
   component runs at full amplitude. */
@media (prefers-reduced-motion: reduce) {
  .rs-glow,
  .rs-spark,
  .rs-star,
  .rs-cheer {
    animation: none;
    opacity: 0;
  }
}
</style>
