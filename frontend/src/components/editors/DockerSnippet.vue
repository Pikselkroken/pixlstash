<template>
  <div class="docker-snippet-wrap">
    <code class="docker-snippet" :class="{ 'docker-snippet--full': full }">{{
      text
    }}</code>
    <AppButton
      size="sm"
      icon-only
      icon-left="content-copy"
      class="docker-snippet-copy"
      :tooltip="tooltip"
      @click="emit('copy')"
    />
  </div>
</template>

<script setup>
// One copyable Docker command in FolderEditor; the editor owns the copy and
// its status line, so this only says the button was pressed.
import AppButton from "../widgets/AppButton.vue";

defineProps({
  text: { type: String, default: "" },
  tooltip: { type: String, required: true },
  // A multi-line command: wraps instead of scrolling sideways.
  full: { type: Boolean, default: false },
});
const emit = defineEmits(["copy"]);
</script>

<style scoped>
.docker-snippet-wrap {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
}

.docker-snippet {
  flex: 1;
  display: block;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  background: rgba(var(--v-theme-dark-surface), 0.55);
  color: rgb(var(--v-theme-on-dark-surface));
  font-size: var(--text-xs);
  white-space: nowrap;
  overflow: auto hidden;
}

.docker-snippet--full {
  white-space: pre-wrap;
  overflow: auto;
  line-height: 1.35;
}

.docker-snippet-copy {
  flex-shrink: 0;
  margin-top: var(--space-1);
}
</style>
