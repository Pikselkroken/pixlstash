<script setup>
import { kindChipColor, relativeDate } from "../../utils/snapshots";
import { formatUserDate } from "../../utils/utils";
import AppButton from "./AppButton.vue";
import Tooltip from "./Tooltip.vue";
import AppDialog from "./AppDialog.vue";

defineProps({
  modelValue: { type: Boolean, default: false },
  snapshots: { type: Array, default: () => [] },
  dontShowAgain: { type: Boolean, default: false },
});

const emit = defineEmits(["update:modelValue", "update:dontShowAgain"]);
</script>

<template>
  <AppDialog
    :open="modelValue"
    title="Deleted pictures still in snapshots"
    @close="emit('update:modelValue', false)"
    @accept="emit('update:modelValue', false)"
  >
    <div class="snap-del-hints">
      <p class="snap-del-hint">
        The pictures you just permanently deleted still have their metadata
        (tags, descriptions, and other details) stored inside the snapshots
        below. The image files are gone, but that metadata remains until the
        snapshot itself is deleted.
      </p>
      <p class="snap-del-hint">
        The snapshots can be deleted from from
        <strong>Settings → Snapshots</strong>.
      </p>
    </div>

    <ul class="snap-del-list">
      <li v-for="snap in snapshots" :key="snap.id" class="snap-del-item">
        <v-chip
          size="x-small"
          label
          :color="kindChipColor(snap.kind)"
          class="snap-del-kind"
        >
          {{ snap.kind }}
        </v-chip>
        <div class="snap-del-item-text">
          <div class="snap-del-item-title">
            {{ snap.label || `${snap.kind} snapshot` }}
          </div>
          <div class="snap-del-item-subtitle">
            <Tooltip
              :text="formatUserDate(snap.created_at, 'iso')"
              activator="parent"
            />
            {{ relativeDate(snap.created_at) }} ·
            {{ snap.matched_count }}
            {{ snap.matched_count === 1 ? "picture" : "pictures" }}
          </div>
        </div>
      </li>
    </ul>

    <template #footer>
      <v-checkbox
        :model-value="dontShowAgain"
        label="Don't show this again"
        density="compact"
        hide-details
        class="snap-del-dont-show"
        @update:model-value="emit('update:dontShowAgain', $event)"
      />
      <AppButton variant="secondary" @click="emit('update:modelValue', false)">
        Close
      </AppButton>
    </template>
  </AppDialog>
</template>

<style scoped>
.snap-del-hints {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.snap-del-hint {
  font-size: var(--text-sm);
  line-height: 1.4;
  opacity: 0.85;
  margin: 0;
}
.snap-del-list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 260px;
  overflow-y: auto;
}
.snap-del-item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) 0;
}
.snap-del-kind {
  flex-shrink: 0;
  min-width: 64px;
  justify-content: center;
}
.snap-del-item-text {
  min-width: 0;
}
.snap-del-item-title {
  font-size: var(--text-base);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.snap-del-item-subtitle {
  font-size: var(--text-xs);
  opacity: var(--opacity-text-secondary);
}
/* The checkbox takes the footer's left edge; Close stays right. */
.snap-del-dont-show {
  margin-right: auto;
}
.snap-del-dont-show :deep(.v-label) {
  font-size: var(--text-sm);
  opacity: 0.85;
}
</style>
