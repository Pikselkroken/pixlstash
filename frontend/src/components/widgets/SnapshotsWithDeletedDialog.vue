<script setup>
import { kindChipColor, relativeDate } from "../../utils/snapshots";
import { formatUserDate } from "../../utils/utils";

defineProps({
  modelValue: { type: Boolean, default: false },
  snapshots: { type: Array, default: () => [] },
});

const emit = defineEmits(["update:modelValue"]);
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

        <v-list class="snap-del-list" density="compact">
          <v-list-item
            v-for="snap in snapshots"
            :key="snap.id"
            class="snap-del-item"
          >
            <template #prepend>
              <v-chip
                size="x-small"
                label
                :color="kindChipColor(snap.kind)"
                class="snap-del-kind"
              >
                {{ snap.kind }}
              </v-chip>
            </template>
            <v-list-item-title class="snap-del-item-title">
              {{ snap.label || `${snap.kind} snapshot` }}
            </v-list-item-title>
            <v-list-item-subtitle
              :title="formatUserDate(snap.created_at, 'iso')"
            >
              {{ relativeDate(snap.created_at) }} ·
              {{ snap.matched_count }}
              {{ snap.matched_count === 1 ? "picture" : "pictures" }}
            </v-list-item-subtitle>
          </v-list-item>
        </v-list>
      </v-card-text>

      <v-card-actions class="snap-del-actions">
        <v-spacer />
        <v-btn variant="text" @click="emit('update:modelValue', false)">
          Close
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<style scoped>
.snap-del-title {
  display: flex;
  align-items: center;
  font-size: 1rem;
  font-weight: 600;
}
.snap-del-hint {
  font-size: 0.85rem;
  line-height: 1.4;
  opacity: 0.85;
  margin-bottom: 10px;
}
.snap-del-list {
  background: transparent;
  max-height: 260px;
  overflow-y: auto;
}
.snap-del-item {
  border-radius: 6px;
}
.snap-del-kind {
  margin-right: 8px;
  min-width: 64px;
  justify-content: center;
}
.snap-del-item-title {
  font-size: 0.9rem;
}
</style>
