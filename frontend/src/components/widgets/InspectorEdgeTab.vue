<template>
  <!-- The closed inspector's handle (visual-language "Closed inspector"): a
       vertical tab on the content's right edge that names what the inspector
       would describe. Click only, never hover - the grid's scrollbar is on
       this edge. The host mounts it only while its inspector is closed. -->
  <button
    class="inspector-edge-tab"
    :class="{
      'inspector-edge-tab--labelled': label,
      [`inspector-edge-tab--nudge-${parity}`]: parity,
    }"
    type="button"
    :aria-label="label ? `Show inspector: ${label}` : 'Show inspector'"
    data-testid="inspector-edge-tab"
    @click="emit('open')"
  >
    <v-icon size="16" aria-hidden="true">mdi-chevron-left</v-icon>
    <span v-if="label" class="inspector-edge-tab__label">{{ label }}</span>
  </button>
</template>

<script setup>
import { ref, watch } from "vue";
import { VIcon } from "vuetify/components";

const props = defineProps({
  /** What the closed inspector would describe: a name, `3 workflows`, or "". */
  label: { type: String, default: "" },
  /** A counter the host bumps on each NEW selection; each bump bounces once. */
  nudge: { type: Number, default: 0 },
});

const emit = defineEmits(["open"]);

// Two classes with identical keyframes, alternated, so a bump while the last
// bounce is still playing restarts it rather than being swallowed.
const parity = ref("");
watch(
  () => props.nudge,
  () => {
    parity.value = parity.value === "a" ? "b" : "a";
  },
);
</script>

<style scoped>
.inspector-edge-tab {
  position: absolute;
  right: 0;
  top: 50%;
  transform: translateY(-50%);
  z-index: var(--z-floating);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
  width: 20px;
  height: 52px;
  justify-content: center;
  padding: 0;
  border: 1px solid rgb(var(--v-theme-border));
  border-right: none;
  border-radius: var(--radius-md) 0 0 var(--radius-md);
  background: rgb(var(--v-theme-panel));
  color: rgb(var(--v-theme-on-surface));
  box-shadow: var(--elevation-2);
  cursor: pointer;
}

.inspector-edge-tab:hover {
  background-image: var(--hover-shade);
}

/* With a selection: 34 wide, as tall as the name needs up to 260, and the
   olive selection stripe down the left edge. */
.inspector-edge-tab--labelled {
  width: 34px;
  height: auto;
  max-height: 260px;
  padding: var(--space-3) 0;
  justify-content: flex-start;
  border-left: 3px solid var(--active-bar);
}

.inspector-edge-tab__label {
  writing-mode: vertical-rl;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  line-height: 1;
  min-height: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.inspector-edge-tab--nudge-a {
  animation: inspector-edge-tab-bounce-a var(--dur-attention) ease-out;
}

.inspector-edge-tab--nudge-b {
  animation: inspector-edge-tab-bounce-b var(--dur-attention) ease-out;
}

/* translateY(-50%) is the centring and has to ride along in every frame. */
@keyframes inspector-edge-tab-bounce-a {
  0%,
  30%,
  60%,
  85%,
  100% {
    transform: translate(0, -50%);
  }
  15% {
    transform: translate(-16px, -50%);
  }
  45% {
    transform: translate(-7px, -50%);
  }
  72% {
    transform: translate(-2px, -50%);
  }
}

@keyframes inspector-edge-tab-bounce-b {
  0%,
  30%,
  60%,
  85%,
  100% {
    transform: translate(0, -50%);
  }
  15% {
    transform: translate(-16px, -50%);
  }
  45% {
    transform: translate(-7px, -50%);
  }
  72% {
    transform: translate(-2px, -50%);
  }
}

@media (prefers-reduced-motion: reduce) {
  .inspector-edge-tab--nudge-a,
  .inspector-edge-tab--nudge-b {
    animation: none;
  }
}
</style>
