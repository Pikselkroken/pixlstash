<template>
  <AppDialog
    :open="open"
    title="Import tag groups"
    size="sm"
    @close="emit('close')"
  >
    <p class="tid-message" data-testid="tid-message">
      {{ count }} {{ count === 1 ? "tag" : "tags" }} you filed yourself
      {{ count === 1 ? "is" : "are" }} in other groups in this file.
    </p>
    <p v-if="sample.length" class="tid-sample">
      For example: {{ sample.join(", ") }}
    </p>
    <p class="tid-note">
      Keep my filing leaves those tags where you put them. Move them too files
      them under the group the file gives them. Either can be undone.
    </p>

    <template #footer>
      <AppButton variant="secondary" key-hint="esc" @click="emit('close')">
        Cancel
      </AppButton>
      <AppButton
        ref="keepRef"
        variant="secondary"
        data-testid="tid-keep"
        @click="emit('keep')"
      >
        Keep my filing
      </AppButton>
      <AppButton variant="primary" data-testid="tid-move" @click="emit('move')">
        Move them too
      </AppButton>
    </template>
  </AppDialog>
</template>

<script setup>
// The question an import asks before it moves a tag the user filed by hand.
// Keep my filing is the default focus: the safe answer is the one Enter or a
// quick Tab-and-Space gives, and Esc (or Cancel) commits nothing.
import { nextTick, ref, watch } from "vue";
import AppButton from "../widgets/AppButton.vue";
import AppDialog from "../widgets/AppDialog.vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  // Tags the user filed in a group the file puts them out of.
  count: { type: Number, default: 0 },
  // Up to ten of their names, for the reader to recognise.
  sample: { type: Array, default: () => [] },
});

const emit = defineEmits(["keep", "move", "close"]);

const keepRef = ref(null);

watch(
  () => props.open,
  async (open) => {
    if (!open) return;
    await nextTick();
    keepRef.value?.$el?.focus?.();
  },
);
</script>

<style scoped>
.tid-message {
  margin: 0;
  font-size: var(--text-base);
  line-height: var(--leading-body);
}
.tid-sample,
.tid-note {
  margin: 0;
  font-size: var(--text-sm);
  line-height: var(--leading-body);
  color: rgba(var(--v-theme-on-surface), var(--opacity-text-secondary));
}
</style>
