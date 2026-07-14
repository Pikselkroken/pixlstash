<template>
  <div class="s-chip">
    <span class="s-chip__label">{{ label }}</span>
    <span
      v-if="meta"
      class="s-chip__meta"
      :style="metaColor ? { color: metaColor } : null"
    >
      {{ meta }}
    </span>
    <slot />
    <button
      type="button"
      class="s-chip__remove"
      title="Remove"
      @click="emit('remove')"
    >
      <v-icon size="15">mdi-close</v-icon>
    </button>
  </div>
</template>

<script setup>
import { VIcon } from "vuetify/components";

defineProps({
  label: { type: String, default: "" },
  meta: { type: String, default: "" },
  metaColor: { type: String, default: "" },
});

const emit = defineEmits(["remove"]);
</script>

<style scoped>
.s-chip {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) var(--space-1) var(--space-1) var(--space-3);
  border-radius: var(--radius-sm);
  background: rgb(var(--v-theme-input-background));
  border: 1px solid rgb(var(--v-theme-border));
}

.s-chip__label {
  flex: 1;
  min-width: 0;
  font-size: var(--text-sm);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.s-chip__meta {
  font-size: var(--text-xs);
  color: rgba(var(--v-theme-on-surface), 0.6);
  white-space: nowrap;
}

.s-chip__remove {
  width: 20px;
  height: 20px;
  flex-shrink: 0;
  border: none;
  background: transparent;
  color: rgba(var(--v-theme-on-surface), 0.6);
  cursor: pointer;
  border-radius: var(--radius-sm);
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.s-chip__remove:hover {
  color: rgb(var(--v-theme-error));
  background: var(--hover-wash);
}
</style>
