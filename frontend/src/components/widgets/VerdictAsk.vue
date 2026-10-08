<template>
  <div class="vask" data-testid="verdict-ask">
    <VerdictIcon
      v-if="verdict && !reopened"
      ref="iconRef"
      interactive
      :verdict="verdict"
      :label="`Your verdict: ${answeredText}. Click to change.`"
      @click="reopen"
    />
    <div v-else class="vask__open">
      <p v-if="$slots.default" class="vask__q"><slot /></p>
      <div class="vask__acts" role="group" :aria-label="groupLabel">
        <AppButton
          v-for="option in options"
          :key="option.value"
          ref="optionRefs"
          size="sm"
          variant="secondary"
          :aria-label="option.name"
          :aria-pressed="
            reopened ? String(verdict === option.value) : undefined
          "
          :data-testid="`verdict-${option.value}`"
          @click.stop="choose(option.value)"
          >{{ option.label }}</AppButton
        >
        <AppButton
          v-if="reopened"
          size="sm"
          variant="ghost"
          data-testid="verdict-cancel"
          @click.stop="cancel"
          >Cancel</AppButton
        >
      </div>
      <p v-if="error" class="vask__error" role="alert">
        <v-icon size="14">mdi-alert-circle-outline</v-icon>{{ error }}
      </p>
      <p v-if="reopened" class="vask__help">
        This is a note for you; it changes nothing else.
      </p>
    </div>
  </div>
</template>

<script setup>
// One question the owner answers with a mark, and the icon it collapses to.
//
// Unanswered: the question (default slot) and two equal buttons. Answered: ONE
// icon button whose name states the verdict. Clicking it reopens the question
// with the current answer pressed and a Cancel. Focus follows the spec: after an
// answer the icon, after Change the pressed answer, after Cancel the icon.
import { nextTick, ref, watch } from "vue";
import { VIcon } from "vuetify/components";

import AppButton from "./AppButton.vue";
import VerdictIcon from "./VerdictIcon.vue";

const props = defineProps({
  /** The current answer, or null while unanswered. */
  verdict: { type: String, default: null },
  /** `[{value, label, name}]`: the visible word and the full accessible name. */
  options: { type: Array, required: true },
  /** "this set produces sensible output": the verdict in words. */
  answeredText: { type: String, required: true },
  groupLabel: { type: String, default: "Your answer" },
  /** A refused save: shown beside the question, which stays open. */
  error: { type: String, default: "" },
});

const emit = defineEmits(["answer", "cancel"]);

const reopened = ref(false);
const iconRef = ref(null);
const optionRefs = ref([]);
let focusIconWhenDrawn = false;

async function focusIcon() {
  await nextTick();
  iconRef.value?.focus();
}

async function reopen() {
  reopened.value = true;
  await nextTick();
  const at = props.options.findIndex((o) => o.value === props.verdict);
  optionRefs.value[Math.max(at, 0)]?.focus();
}

function cancel() {
  reopened.value = false;
  emit("cancel");
  focusIcon();
}

function choose(value) {
  reopened.value = false;
  // The icon only exists once the new answer has come back through the prop.
  if (props.verdict === value) focusIcon();
  else focusIconWhenDrawn = true;
  emit("answer", value);
}

// A save that failed leaves the old answer standing: ask again, not the icon,
// with focus on the standing answer, since the button pressed is gone.
watch(
  () => props.error,
  (error) => {
    if (!error) return;
    focusIconWhenDrawn = false;
    if (props.verdict) reopen();
  },
);

defineExpose({ reopen });

watch(
  () => props.verdict,
  () => {
    if (focusIconWhenDrawn) {
      focusIconWhenDrawn = false;
      focusIcon();
    }
  },
);
</script>

<style scoped>
.vask__open {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2) var(--space-3);
}

.vask__q {
  margin: 0;
}

.vask__acts {
  display: inline-flex;
  gap: var(--space-2);
}

.vask__error {
  display: flex;
  flex-basis: 100%;
  align-items: center;
  gap: var(--space-2);
  margin: 0;
  color: rgb(var(--v-theme-surface-error));
}

.vask__help {
  flex-basis: 100%;
  margin: 0;
  color: rgba(var(--v-theme-on-panel), var(--opacity-text-secondary));
}
</style>
