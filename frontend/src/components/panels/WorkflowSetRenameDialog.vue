<template>
  <AppDialog
    :open="Boolean(set)"
    title="Rename workflow set"
    size="sm"
    @close="emit('close')"
  >
    <AppInput
      v-model="name"
      label="Name"
      :placeholder="fallback"
      autofocus
      @enter="save"
    />
    <p class="wsr__note">
      Leave it empty and the set is called after its checkpoint ({{
        fallback
      }}).
    </p>
    <template #footer>
      <AppButton variant="ghost" @click="emit('close')">Cancel</AppButton>
      <AppButton variant="primary" @click="save">Rename</AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
/**
 * Rename one hand-made workflow set (#1520). A name is optional: empty goes back
 * to the checkpoint's name, or "Untitled set" without one.
 */
import { ref, watch } from "vue";

import { setCheckpoint } from "../../utils/workflowSets";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";
import AppInput from "../widgets/AppInput.vue";

const props = defineProps({
  /** The set being renamed, or null while closed. */
  set: { type: Object, default: null },
});

const emit = defineEmits(["close", "save"]);

const name = ref("");
const fallback = ref("");

watch(
  () => props.set,
  (set) => {
    name.value = set?.name ?? "";
    fallback.value = setCheckpoint(set)?.name || "Untitled set";
  },
  { immediate: true },
);

function save() {
  emit("save", name.value);
}
</script>

<style scoped>
.wsr__note {
  margin: var(--space-3) 0 0;
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
