<template>
  <div class="tb-export-panel popup-panel">
    <div class="tb-export-title">
      Export {{ exportStore.exportCount ?? 0 }} picture{{
        (exportStore.exportCount ?? 0) === 1 ? "" : "s"
      }}
    </div>
    <v-select
      v-model="tbExportTypeModel"
      :items="exportStore.exportTypeOptions ?? []"
      item-title="title"
      item-value="value"
      label="Export type"
      density="comfortable"
    />
    <v-select
      v-model="tbExportCaptionModeModel"
      :items="exportStore.exportCaptionOptions ?? []"
      item-title="title"
      item-value="value"
      label="Captions"
      density="comfortable"
      :disabled="exportStore.exportTypeLocksCaptions"
    />
    <v-select
      v-model="tbExportResolutionModel"
      :items="exportStore.exportResolutionOptions ?? []"
      item-title="title"
      item-value="value"
      label="Resolution"
      density="comfortable"
    />
    <v-select
      v-if="tbExportCaptionModeModel === 'tags'"
      v-model="tbExportTagFormatModel"
      :items="exportStore.exportTagFormatOptions ?? []"
      item-title="title"
      item-value="value"
      label="Tag format"
      density="comfortable"
    />
    <v-switch
      v-model="tbExportIncludeCharacterNameModel"
      label="Include character name"
      color="primary"
      density="comfortable"
      :disabled="
        tbExportCaptionModeModel === 'none' ||
        exportStore.exportTypeLocksCaptions
      "
    />
    <v-switch
      v-model="tbExportUseOriginalFileNamesModel"
      label="Use original file names"
      color="primary"
      density="comfortable"
    />
    <v-btn color="primary" @click="onExport"> Export </v-btn>
  </div>
</template>

<script setup>
import { computed } from "vue";
import { useExportStore } from "../../stores/useExportStore";

const emit = defineEmits(["confirm-export"]);

const exportStore = useExportStore();

const tbExportTypeModel = computed({
  get: () => exportStore.exportType,
  set: (v) => {
    exportStore.exportType = v;
  },
});
const tbExportCaptionModeModel = computed({
  get: () => exportStore.exportCaptionMode,
  set: (v) => {
    exportStore.exportCaptionMode = v;
  },
});
const tbExportTagFormatModel = computed({
  get: () => exportStore.exportTagFormat,
  set: (v) => {
    exportStore.exportTagFormat = v;
  },
});
const tbExportResolutionModel = computed({
  get: () => exportStore.exportResolution,
  set: (v) => {
    exportStore.exportResolution = v;
  },
});
const tbExportIncludeCharacterNameModel = computed({
  get: () => exportStore.exportIncludeCharacterName,
  set: (v) => {
    exportStore.exportIncludeCharacterName = v;
  },
});
const tbExportUseOriginalFileNamesModel = computed({
  get: () => exportStore.exportUseOriginalFileNames,
  set: (v) => {
    exportStore.exportUseOriginalFileNames = v;
  },
});

function onExport() {
  exportStore.exportMenuOpen = false;
  emit("confirm-export");
}
</script>

<style scoped>
.tb-export-panel {
  padding: var(--space-4) var(--space-4);
  min-width: 260px;
  gap: var(--space-3);
}

.tb-export-title {
  font-size: var(--text-md);
  font-weight: 500;
  padding-bottom: var(--space-2);
}
</style>
