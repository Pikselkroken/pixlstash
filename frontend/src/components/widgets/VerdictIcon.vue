<template>
  <AppButton
    v-if="interactive"
    ref="buttonRef"
    class="verdict-icon"
    :class="`verdict-icon--${positive ? 'positive' : 'negative'}`"
    variant="ghost"
    size="sm"
    :icon-left="positive ? 'check-circle' : 'close-circle'"
    icon-only
    :tooltip="tip"
    :aria-label="label"
    data-testid="verdict-icon"
    @click.stop="emit('click', $event)"
    @dblclick.stop
  />
  <span
    v-else
    class="verdict-icon verdict-icon--static"
    :class="`verdict-icon--${positive ? 'positive' : 'negative'}`"
    role="img"
    :aria-label="label"
    data-testid="verdict-icon"
    ><Tooltip :text="tip" activator="parent" /><v-icon size="16">{{
      positive ? "mdi-check-circle" : "mdi-close-circle"
    }}</v-icon></span
  >
</template>

<script setup>
// The owner's verdict, as one small icon (display only unless `interactive`:
// a status image, so the tip reaches a pointer and the label a screen reader
// without a control that does nothing): a check for approval, a cross for the
// reverse. The shape carries the meaning and the status colour backs it, so
// greyscale loses nothing. Used wherever a verdict is drawn: the set panel, a
// member row or card, a set card and a model's own shelf row.
import { computed, ref } from "vue";
import { VIcon } from "vuetify/components";

import { isPositive } from "../../utils/setVerdicts";
import AppButton from "./AppButton.vue";
import Tooltip from "./Tooltip.vue";

const props = defineProps({
  /** "yes" | "no" (a set) or "not_problem" | "problem" (a member). */
  verdict: { type: String, required: true },
  /** The accessible name; also the tip unless `tooltip` says otherwise. */
  label: { type: String, required: true },
  tooltip: { type: String, default: "" },
  /** A button that reopens the question, rather than a status image. */
  interactive: { type: Boolean, default: false },
});

const emit = defineEmits(["click"]);

const positive = computed(() => isPositive(props.verdict));
const tip = computed(() => props.tooltip || props.label);
const buttonRef = ref(null);

defineExpose({ focus: () => buttonRef.value?.focus() });
</script>

<style scoped>
.verdict-icon--positive {
  color: rgb(var(--v-theme-surface-success));
}

.verdict-icon--negative {
  color: rgb(var(--v-theme-surface-error));
}

.verdict-icon--static {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
}
</style>
