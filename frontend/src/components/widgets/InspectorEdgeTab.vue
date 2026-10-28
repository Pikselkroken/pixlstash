<template>
  <!-- The closed inspector's handle (visual-language "Closed inspector"): a
       vertical tab on the content's right edge that names what the inspector
       would describe. Click only, never hover - the grid's scrollbar is on
       this edge. The host mounts it only while its inspector is closed; it
       shows on a nudge and goes again `HIDE_AFTER_MS` later, unless the
       pointer or focus is on it, and at once if the label empties (the
       selection it named was cleared). -->
  <button
    v-show="shown && label"
    ref="el"
    class="inspector-edge-tab"
    :class="{
      'inspector-edge-tab--labelled': label,
      'inspector-edge-tab--plain': plain,
      [`inspector-edge-tab--nudge-${parity}`]: parity,
    }"
    type="button"
    :aria-label="
      name || (label ? `Show inspector: ${label}` : 'Show inspector')
    "
    data-testid="inspector-edge-tab"
    @click="emit('open')"
    @mouseleave="arm"
    @blur="arm"
  >
    <v-icon size="16" aria-hidden="true">mdi-chevron-left</v-icon>
    <span v-if="label" class="inspector-edge-tab__label">{{ label }}</span>
  </button>
</template>

<script setup>
import { onBeforeUnmount, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

const props = defineProps({
  /** What the closed inspector would describe: a name, `3 workflows`, or "". */
  label: { type: String, default: "" },
  /**
   * A counter the host bumps on each NEW selection; each bump shows the tab
   * and bounces it once.
   */
  nudge: { type: Number, default: 0 },
  /**
   * The label names no selection (the Models rail's "All models"): the tab is
   * drawn without the selection stripe.
   */
  plain: { type: Boolean, default: false },
  /** The accessible name, when "Show inspector" is not what opens. */
  name: { type: String, default: "" },
});

const emit = defineEmits(["open"]);

// A cue, not a fixture: the tab says a selection is waiting behind the closed
// rail and then gets out of the way. Hovered or focused when the time is up,
// it stays until the pointer or focus leaves, so the timer never takes it
// from under a click or from under focus.
const HIDE_AFTER_MS = 5000;
const shown = ref(false);
const el = ref(null);
let hideTimer = 0;

function arm() {
  if (!shown.value) return;
  clearTimeout(hideTimer);
  hideTimer = setTimeout(() => {
    if (!el.value?.matches(":hover, :focus")) shown.value = false;
  }, HIDE_AFTER_MS);
}

// Two classes with identical keyframes, alternated, so a bump while the last
// bounce is still playing restarts it rather than being swallowed.
const parity = ref("");
watch(
  () => props.nudge,
  () => {
    parity.value = parity.value === "a" ? "b" : "a";
    shown.value = true;
    arm();
  },
);
// An emptied label ends the cue: it comes back only on the next nudge, not
// when the host re-derives a label for the same selection.
watch(
  () => props.label,
  (label) => {
    if (label) return;
    clearTimeout(hideTimer);
    shown.value = false;
  },
);

onBeforeUnmount(() => clearTimeout(hideTimer));
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

/* A label that names no selection keeps the tab's own border on that edge. */
.inspector-edge-tab--plain {
  border-left: 1px solid rgb(var(--v-theme-border));
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
  animation: inspector-edge-tab-bounce-a var(--dur-attention) var(--ease-decelerate);
}

.inspector-edge-tab--nudge-b {
  animation: inspector-edge-tab-bounce-b var(--dur-attention) var(--ease-decelerate);
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
