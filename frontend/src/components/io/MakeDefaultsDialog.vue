<template>
  <AppDialog
    :open="open"
    title="Make these the defaults"
    size="sm"
    @close="emit('close')"
    @accept="submit"
  >
    <p class="mkd-lede">
      Runs from this workflow start from these. Your pictures and saved recipes
      are not changed.
    </p>
    <div ref="listEl" class="mkd-list">
      <div v-for="(row, index) in rows" :key="index" class="mkd-check">
        <input
          :id="`${uid}-${index}`"
          v-model="ticked[index]"
          class="mkd-box"
          type="checkbox"
          :disabled="busy"
        />
        <label :for="`${uid}-${index}`">
          {{ row.label }} {{ row.from }}<span class="mkd-quiet"> → </span>{{ row.to }}
        </label>
      </div>
    </div>
    <p v-if="!count" :id="`${uid}-none`" class="mkd-quiet mkd-note">
      Tick at least one
    </p>
    <p v-if="error" class="mkd-bad" role="alert">{{ error }}</p>

    <template #footer>
      <AppButton key-hint="esc" :disabled="busy" @click="emit('close')">
        Cancel
      </AppButton>
      <AppButton
        variant="primary"
        key-hint="enter"
        :loading="busy"
        :aria-disabled="count ? undefined : 'true'"
        :aria-describedby="count ? undefined : `${uid}-none`"
        @click="submit"
      >
        Make {{ count }} {{ count === 1 ? "default" : "defaults" }}
      </AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
/**
 * "Make these the defaults…" from a recipe card's ⋯ (#1653, design §1.2/§2.5).
 *
 * Lists a saved recipe's parameter differences, all ticked; confirming emits
 * the ticked rows and the caller writes them. Parameters only: the defaults
 * PUT refuses models and LoRAs, so those rows are absent, not disabled.
 */
import { computed, nextTick, ref, useId, watch } from "vue";

import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  /** `recipeDiff(...).params`: {slot_label, input_name, label, from, to}. */
  rows: { type: Array, default: () => [] },
  busy: { type: Boolean, default: false },
  error: { type: String, default: "" },
});

const emit = defineEmits(["close", "confirm"]);

const uid = useId();
const ticked = ref([]);
const listEl = ref(null);
const count = computed(() => ticked.value.filter(Boolean).length);

watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    ticked.value = props.rows.map(() => true);
    await nextTick();
    listEl.value?.querySelector("input")?.focus();
  },
  { immediate: true },
);

function submit() {
  if (!count.value || props.busy) return;
  emit(
    "confirm",
    props.rows.filter((_, index) => ticked.value[index]),
  );
}
</script>

<style scoped>
.mkd-lede {
  margin: 0;
  font-size: var(--text-base);
}

.mkd-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  font-variant-numeric: tabular-nums;
}

/* RunDialog's `.rund-check` / `.rund-box`, copied: a native checkbox with the
   olive tick, and the whole row a 24px target. */
.mkd-check {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-height: var(--control-h-sm);
  font-size: var(--text-sm);
}

.mkd-check label {
  cursor: pointer;
}

.mkd-box {
  width: 16px;
  height: 16px;
  accent-color: rgb(var(--v-theme-primary));
  cursor: pointer;
}

.mkd-note,
.mkd-bad {
  margin: 0;
  font-size: var(--text-xs);
}

.mkd-quiet {
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}

.mkd-bad {
  color: rgb(var(--v-theme-surface-error));
}
</style>
