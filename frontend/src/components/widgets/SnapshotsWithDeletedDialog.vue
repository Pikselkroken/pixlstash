<script setup>
import { kindChipColor, relativeDate } from "../../utils/snapshots";
import { formatUserDate } from "../../utils/utils";
import AppButton from "./AppButton.vue";
import Tooltip from "./Tooltip.vue";

defineProps({
  modelValue: { type: Boolean, default: false },
  snapshots: { type: Array, default: () => [] },
  dontShowAgain: { type: Boolean, default: false },
});

const emit = defineEmits(["update:modelValue", "update:dontShowAgain"]);
</script>

<template>
  <v-dialog
    :model-value="modelValue"
    max-width="520"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <v-card class="snap-del-card">
      <v-card-title class="snap-del-title">
        <v-icon size="18" class="mr-2">mdi-shield-alert-outline</v-icon>
        Deleted pictures still in snapshots
      </v-card-title>

      <v-card-text class="snap-del-body">
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
      </v-card-text>

      <v-card-actions class="snap-del-actions">
        <v-checkbox
          :model-value="dontShowAgain"
          label="Don't show this again"
          density="compact"
          hide-details
          class="snap-del-dont-show"
          @update:model-value="emit('update:dontShowAgain', $event)"
        />
        <v-spacer />
        <AppButton variant="secondary" @click="emit('update:modelValue', false)">
          Close
        </AppButton>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.snap-del-title {
  display: flex;
  align-items: center;
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
}
.snap-del-hint {
  font-size: var(--text-sm);
  line-height: 1.4;
  opacity: 0.85;
  margin-bottom: var(--space-3);
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
.snap-del-dont-show :deep(.v-label) {
  font-size: var(--text-sm);
  opacity: 0.85;
}
</style>
